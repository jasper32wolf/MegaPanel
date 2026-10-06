"""PostgreSQL-owned fair scheduler for allowlisted candidate work."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.config import get_settings
from app.models import (
    SchedulerAttempt,
    SchedulerJob,
    SchedulerProjectTurn,
    SchedulerWakeup,
    SiteBuild,
)
from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

_WORK_TYPE_SITE_BUILD = "site_build"
_EVENTS = frozenset(
    {
        "queued",
        "claimed",
        "wake_published",
        "started",
        "lease_expired",
        "paused",
        "resumed",
        "cancel_requested",
        "cancelled",
        "succeeded",
        "failed",
    }
)
_SAFE_CODE = re.compile(r"^[a-z0-9_]{1,64}$")
_LEASE_SECONDS = 10 * 60
_MAX_CONCURRENCY = 1


def _now() -> datetime:
    return datetime.now(UTC)


async def append_scheduler_attempt(
    db: AsyncSession,
    *,
    job: SchedulerJob,
    event_type: str,
    safe_code: str | None = None,
    details: dict | None = None,
) -> SchedulerAttempt:
    if event_type not in _EVENTS:
        raise ValueError("Unsupported scheduler event type")
    if safe_code is not None and not _SAFE_CODE.fullmatch(safe_code):
        raise ValueError("Invalid scheduler safe code")
    payload = details or {}
    if not isinstance(payload, dict) or len(str(payload).encode("utf-8")) > 4096:
        raise ValueError("Scheduler event details exceed the safe limit")
    sequence = (
        await db.scalar(
            select(func.coalesce(func.max(SchedulerAttempt.sequence), 0)).where(
                SchedulerAttempt.scheduler_job_id == job.id
            )
        )
        or 0
    ) + 1
    attempt = SchedulerAttempt(
        scheduler_job_id=job.id,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        site_id=job.site_id,
        sequence=sequence,
        attempt=job.attempt_count,
        event_type=event_type,
        safe_code=safe_code,
        details=payload,
    )
    db.add(attempt)
    return attempt


async def create_site_build_job(db: AsyncSession, *, build: SiteBuild) -> SchedulerJob:
    """Idempotently bind a frozen candidate build to one scheduler record."""
    if not build.project_id or build.snapshot_version != 1 or not build.input_snapshot_hash:
        raise ValueError("Only frozen project candidate builds can be scheduled")
    existing = await db.scalar(
        select(SchedulerJob).where(
            SchedulerJob.work_type == _WORK_TYPE_SITE_BUILD,
            SchedulerJob.source_id == build.id,
        )
    )
    if existing:
        if (
            existing.tenant_id != build.tenant_id
            or existing.project_id != build.project_id
            or existing.site_id != build.site_id
            or existing.source_hash != build.input_snapshot_hash
        ):
            raise ValueError("Scheduler job source identity mismatch")
        return existing
    job = SchedulerJob(
        tenant_id=build.tenant_id,
        project_id=build.project_id,
        site_id=build.site_id,
        work_type=_WORK_TYPE_SITE_BUILD,
        source_id=build.id,
        source_hash=build.input_snapshot_hash,
        source_version=build.snapshot_version,
        priority=build.queue_priority,
        not_before=build.not_before,
        requested_by=build.requested_by,
    )
    db.add(job)
    await db.flush()
    await append_scheduler_attempt(db, job=job, event_type="queued")
    return job


async def requeue_site_build_job(db: AsyncSession, *, build: SiteBuild) -> SchedulerJob:
    job = await create_site_build_job(db, build=build)
    if job.state == "queued":
        return job
    if job.state not in {"failed", "cancelled", "paused"}:
        raise ValueError("Scheduler job is not retryable")
    now = _now()
    job.state = "queued"
    job.priority = build.queue_priority
    job.not_before = build.not_before
    job.eligible_at = now
    job.queued_at = now
    job.finished_at = None
    job.failure_code = None
    job.lease_id = None
    job.leased_at = None
    job.lease_expires_at = None
    await append_scheduler_attempt(db, job=job, event_type="queued", safe_code="manual_retry")
    return job


async def recover_expired_scheduler_leases(db: AsyncSession) -> int:
    now = _now()
    jobs = list(
        (
            await db.execute(
                select(SchedulerJob)
                .where(
                    SchedulerJob.state.in_(("leased", "running")),
                    SchedulerJob.lease_expires_at.is_not(None),
                    SchedulerJob.lease_expires_at < now,
                )
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for job in jobs:
        job.state = "failed"
        job.failure_code = "worker_timeout"
        job.finished_at = now
        job.lease_id = None
        job.lease_expires_at = None
        await append_scheduler_attempt(
            db, job=job, event_type="lease_expired", safe_code="worker_timeout"
        )
    if jobs:
        await db.commit()
    return len(jobs)


async def _active_slots(db: AsyncSession) -> int:
    return int(
        await db.scalar(
            select(func.count(SchedulerJob.id)).where(
                SchedulerJob.state.in_(("leased", "running", "cancel_requested"))
            )
        )
        or 0
    )


async def dispatch_due_jobs(db: AsyncSession, *, limit: int = _MAX_CONCURRENCY) -> list[UUID]:
    """Lease due jobs fairly by project and make transactional UUID wakeups."""
    slots = max(0, min(limit, _MAX_CONCURRENCY - await _active_slots(db)))
    if not slots:
        return []
    now = _now()
    candidates = list(
        (
            await db.execute(
                select(SchedulerJob)
                .where(
                    SchedulerJob.state == "queued",
                    SchedulerJob.eligible_at <= now,
                    or_(SchedulerJob.not_before.is_(None), SchedulerJob.not_before <= now),
                )
                .order_by(
                    SchedulerJob.priority.desc(), SchedulerJob.first_eligible_at, SchedulerJob.id
                )
                .with_for_update(skip_locked=True)
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    heads: dict[UUID, SchedulerJob] = {}
    for job in candidates:
        if job.project_id and job.project_id not in heads:
            heads[job.project_id] = job
    if not heads:
        return []
    turns = {
        turn.project_id: turn
        for turn in (
            await db.execute(
                select(SchedulerProjectTurn).where(
                    SchedulerProjectTurn.project_id.in_(tuple(heads)),
                    SchedulerProjectTurn.tenant_id == next(iter(heads.values())).tenant_id,
                )
            )
        )
        .scalars()
        .all()
    }
    ordered = sorted(
        heads.values(),
        key=lambda job: (
            turns.get(job.project_id).dispatch_turn if job.project_id in turns else -1,
            -job.priority,
            job.first_eligible_at,
            str(job.id),
        ),
    )
    selected = ordered[:slots]
    for job in selected:
        lease_id = uuid.uuid4()
        job.state = "leased"
        job.attempt_count += 1
        job.lease_id = lease_id
        job.leased_at = now
        job.lease_expires_at = now + timedelta(seconds=_LEASE_SECONDS)
        job.failure_code = None
        await append_scheduler_attempt(db, job=job, event_type="claimed")
        db.add(SchedulerWakeup(scheduler_job_id=job.id, tenant_id=job.tenant_id, lease_id=lease_id))
        turn = turns.get(job.project_id)
        if turn is None:
            turn = SchedulerProjectTurn(tenant_id=job.tenant_id, project_id=job.project_id)
            db.add(turn)
        turn.dispatch_turn = max((item.dispatch_turn for item in turns.values()), default=0) + 1
        turn.last_dispatched_at = now
    await db.commit()
    return [job.id for job in selected]


async def publish_pending_wakeups(db: AsyncSession, *, limit: int = 20) -> int:
    wakeups = list(
        (
            await db.execute(
                select(SchedulerWakeup)
                .where(SchedulerWakeup.state == "pending", SchedulerWakeup.available_at <= _now())
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    published = 0
    for wakeup in wakeups:
        try:
            pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
            try:
                await pool.enqueue_job("scheduler_execute_task", str(wakeup.scheduler_job_id))
            finally:
                await pool.aclose()
        except Exception:  # noqa: BLE001
            wakeup.attempt_count += 1
            continue
        wakeup.state = "published"
        wakeup.published_at = _now()
        job = await db.get(SchedulerJob, wakeup.scheduler_job_id)
        if job:
            await append_scheduler_attempt(db, job=job, event_type="wake_published")
        published += 1
    await db.commit()
    return published


async def claim_execution(db: AsyncSession, job_id: UUID) -> SchedulerJob | None:
    job = await db.scalar(select(SchedulerJob).where(SchedulerJob.id == job_id).with_for_update())
    if not job or job.state != "leased" or not job.lease_id:
        return None
    if job.lease_expires_at is None or job.lease_expires_at < _now():
        return None
    job.state = "running"
    job.started_at = _now()
    await append_scheduler_attempt(db, job=job, event_type="started")
    await db.commit()
    return job


async def complete_site_build_execution(db: AsyncSession, *, job_id: UUID, result: dict) -> dict:
    job = await db.scalar(select(SchedulerJob).where(SchedulerJob.id == job_id).with_for_update())
    if not job:
        return result
    if result.get("status") == "ready":
        job.state = "succeeded"
        job.failure_code = None
        event_type = "succeeded"
        code = None
    else:
        job.state = "failed"
        job.failure_code = str(result.get("error_code") or "source_execution_failed")[:64]
        event_type = "failed"
        code = job.failure_code
    job.finished_at = _now()
    job.lease_id = None
    job.lease_expires_at = None
    await append_scheduler_attempt(db, job=job, event_type=event_type, safe_code=code)
    await db.commit()
    return result


async def pause_job(db: AsyncSession, *, job: SchedulerJob) -> None:
    if job.state not in {"queued", "failed"}:
        raise ValueError("Only queued or failed scheduled work can be paused")
    job.state = "paused"
    job.paused_at = _now()
    await append_scheduler_attempt(db, job=job, event_type="paused")


async def resume_job(db: AsyncSession, *, job: SchedulerJob) -> None:
    if job.state != "paused":
        raise ValueError("Only paused scheduled work can be resumed")
    now = _now()
    job.state = "queued"
    job.eligible_at = now
    job.queued_at = now
    job.paused_at = None
    await append_scheduler_attempt(db, job=job, event_type="resumed")


async def cancel_job(db: AsyncSession, *, job: SchedulerJob) -> None:
    now = _now()
    if job.state in {"queued", "paused", "failed"}:
        job.state = "cancelled"
        job.cancelled_at = now
        await append_scheduler_attempt(db, job=job, event_type="cancelled")
        return
    if job.state in {"leased", "running"}:
        job.state = "cancel_requested"
        job.cancel_requested_at = now
        await append_scheduler_attempt(db, job=job, event_type="cancel_requested")
        return
    raise ValueError("Scheduled work is already terminal")


__all__ = [
    "append_scheduler_attempt",
    "cancel_job",
    "claim_execution",
    "complete_site_build_execution",
    "create_site_build_job",
    "dispatch_due_jobs",
    "pause_job",
    "publish_pending_wakeups",
    "recover_expired_scheduler_leases",
    "requeue_site_build_job",
    "resume_job",
]
