"""Safe project-scoped controls for database-scheduled work."""

from __future__ import annotations

from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.api.v1.projects import _project_or_404, retry_project_build
from app.db.session import get_db
from app.models import SchedulerAttempt, SchedulerJob
from app.services.audit import append_audit
from app.services.scheduler import cancel_job, pause_job, resume_job
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
_READ = require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
_WRITE = require_roles("superadmin", "tenant_admin", "manager", "editor")


def _serialize(job: SchedulerJob, attempts: list[SchedulerAttempt] | None = None) -> dict:
    return {
        "id": str(job.id),
        "work_type": job.work_type,
        "source_id": str(job.source_id),
        "state": job.state,
        "priority": job.priority,
        "not_before": job.not_before.isoformat() if job.not_before else None,
        "eligible_at": job.eligible_at.isoformat() if job.eligible_at else None,
        "attempt_count": job.attempt_count,
        "lease_expires_at": job.lease_expires_at.isoformat() if job.lease_expires_at else None,
        "failure_code": job.failure_code,
        "cancel_requested_at": (
            job.cancel_requested_at.isoformat() if job.cancel_requested_at else None
        ),
        "attempts": [
            {
                "event_type": item.event_type,
                "safe_code": item.safe_code,
                "attempt": item.attempt,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in (attempts or [])[-20:]
        ],
    }


async def _job_or_404(
    db: AsyncSession, *, project_id: UUID, job_id: UUID, lock: bool = False
) -> SchedulerJob:
    statement = select(SchedulerJob).where(
        SchedulerJob.id == job_id, SchedulerJob.project_id == project_id
    )
    job = await db.scalar(statement.with_for_update() if lock else statement)
    if not job:
        raise HTTPException(status_code=404, detail="Scheduled work was not found")
    return job


@router.get("/{project_id}/scheduled-work")
async def list_scheduled_work(
    project_id: UUID,
    auth: AuthContext = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    project = await _project_or_404(db, project_id, auth)
    jobs = list(
        (
            await db.execute(
                select(SchedulerJob)
                .where(
                    SchedulerJob.project_id == project.id,
                    SchedulerJob.tenant_id == project.tenant_id,
                )
                .order_by(SchedulerJob.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_serialize(job) for job in jobs]


@router.get("/{project_id}/scheduled-work/{job_id}")
async def get_scheduled_work(
    project_id: UUID,
    job_id: UUID,
    auth: AuthContext = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    job = await _job_or_404(db, project_id=project.id, job_id=job_id)
    if job.tenant_id != project.tenant_id:
        raise HTTPException(status_code=404, detail="Scheduled work was not found")
    attempts = list(
        (
            await db.execute(
                select(SchedulerAttempt)
                .where(SchedulerAttempt.scheduler_job_id == job.id)
                .order_by(SchedulerAttempt.sequence)
            )
        )
        .scalars()
        .all()
    )
    return _serialize(job, attempts)


async def _mutate(
    *,
    project_id: UUID,
    job_id: UUID,
    action: str,
    auth: AuthContext,
    db: AsyncSession,
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    job = await _job_or_404(db, project_id=project.id, job_id=job_id, lock=True)
    if job.tenant_id != project.tenant_id:
        raise HTTPException(status_code=404, detail="Scheduled work was not found")
    try:
        if action == "pause":
            await pause_job(db, job=job)
        elif action == "resume":
            await resume_job(db, job=job)
        else:
            await cancel_job(db, job=job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await append_audit(
        db,
        action=f"scheduler.{action}",
        payload={"project_id": str(project.id), "scheduler_job_id": str(job.id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize(job)


@router.post("/{project_id}/scheduled-work/{job_id}/retry")
async def retry_scheduled_work(
    project_id: UUID,
    job_id: UUID,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    job = await _job_or_404(db, project_id=project.id, job_id=job_id)
    if job.tenant_id != project.tenant_id:
        raise HTTPException(status_code=404, detail="Scheduled work was not found")
    if job.work_type != "site_build" or job.state != "failed":
        raise HTTPException(
            status_code=409,
            detail="Only failed candidate builds can be retried from the frozen snapshot",
        )
    await retry_project_build(project_id, job.source_id, auth, db)
    return _serialize(job)


@router.post("/{project_id}/scheduled-work/{job_id}/pause")
async def pause_scheduled_work(
    project_id: UUID,
    job_id: UUID,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await _mutate(project_id=project_id, job_id=job_id, action="pause", auth=auth, db=db)


@router.post("/{project_id}/scheduled-work/{job_id}/resume")
async def resume_scheduled_work(
    project_id: UUID,
    job_id: UUID,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await _mutate(project_id=project_id, job_id=job_id, action="resume", auth=auth, db=db)


@router.post("/{project_id}/scheduled-work/{job_id}/cancel")
async def cancel_scheduled_work(
    project_id: UUID,
    job_id: UUID,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await _mutate(project_id=project_id, job_id=job_id, action="cancel", auth=auth, db=db)
