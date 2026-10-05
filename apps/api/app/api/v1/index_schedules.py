"""Explicit, hash-bound plans for gradual index eligibility."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.api.v1.projects import (
    _current_qa_run,
    _draft_manifest_hash,
    _project_or_404,
    _project_site_or_409,
)
from app.db.session import get_db
from app.models import (
    IndexPromotionSchedule,
    IndexPromotionScheduleItem,
    PageDraft,
    Project,
    Site,
)
from app.schemas.workflow import IndexPromotionScheduleCreate, IndexPromotionScheduleDecision
from app.services.audit import append_audit
from fastapi import APIRouter, Depends, HTTPException, status
from site_panel_shared.manifests import PageManifest, SiteManifest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
_READ = require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
_WRITE = require_roles("superadmin", "tenant_admin", "manager", "editor")
_REVIEW = require_roles("superadmin", "tenant_admin", "manager")


def _serialize_item(item: IndexPromotionScheduleItem) -> dict:
    return {
        "id": str(item.id),
        "slug": item.slug,
        "source_hash": item.source_hash,
        "batch_number": item.batch_number,
        "planned_at": item.planned_at.isoformat(),
        "state": item.state,
        "candidate_build_id": str(item.candidate_build_id) if item.candidate_build_id else None,
        "stale_reason": item.stale_reason,
    }


async def _serialize_schedule(db: AsyncSession, schedule: IndexPromotionSchedule) -> dict:
    items = list(
        (
            await db.execute(
                select(IndexPromotionScheduleItem)
                .where(IndexPromotionScheduleItem.schedule_id == schedule.id)
                .order_by(IndexPromotionScheduleItem.batch_number, IndexPromotionScheduleItem.slug)
            )
        )
        .scalars()
        .all()
    )
    return {
        "id": str(schedule.id),
        "state": schedule.state,
        "reason": schedule.reason,
        "starts_at": schedule.starts_at.isoformat(),
        "interval_hours": schedule.interval_hours,
        "batch_size": schedule.batch_size,
        "approved_at": schedule.approved_at.isoformat() if schedule.approved_at else None,
        "paused_at": schedule.paused_at.isoformat() if schedule.paused_at else None,
        "completed_at": schedule.completed_at.isoformat() if schedule.completed_at else None,
        "items": [_serialize_item(item) for item in items],
    }


async def _eligible_drafts(
    db: AsyncSession, *, project: Project, site: Site
) -> dict[str, tuple[PageDraft, str]]:
    manifest = SiteManifest.model_validate(site.manifest)
    drafts = list(
        (
            await db.execute(
                select(PageDraft).where(
                    PageDraft.project_id == project.id,
                    PageDraft.state == "applied",
                    PageDraft.content_hash.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    by_hash = {
        draft.content_hash: draft
        for draft in drafts
        if draft.content_hash
        and draft.page_manifest
        and _current_qa_run(draft)
        and draft.last_qa_verdict == "pass"
        and _draft_manifest_hash(draft.page_manifest) == draft.content_hash
    }
    eligible: dict[str, tuple[PageDraft, str]] = {}
    for page in manifest.pages:
        source_hash = _draft_manifest_hash(page.model_dump(mode="json"))
        draft = by_hash.get(source_hash)
        if draft and PageManifest.model_validate(draft.page_manifest).slug == page.slug:
            eligible[page.slug] = (draft, source_hash)
    return eligible


@router.get("/{project_id}/index-schedules")
async def list_index_schedules(
    project_id: UUID,
    auth: AuthContext = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    project = await _project_or_404(db, project_id, auth)
    schedules = list(
        (
            await db.execute(
                select(IndexPromotionSchedule)
                .where(
                    IndexPromotionSchedule.project_id == project.id,
                    IndexPromotionSchedule.tenant_id == project.tenant_id,
                )
                .order_by(IndexPromotionSchedule.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [await _serialize_schedule(db, schedule) for schedule in schedules]


@router.post("/{project_id}/index-schedules", status_code=status.HTTP_201_CREATED)
async def create_index_schedule(
    project_id: UUID,
    body: IndexPromotionScheduleCreate,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    site = await _project_site_or_409(db, project)
    eligible = await _eligible_drafts(db, project=project, site=site)
    unavailable = [slug for slug in body.slugs if slug not in eligible]
    if unavailable:
        raise HTTPException(
            status_code=409,
            detail={
                "blockers": [
                    "Run passing QA and explicitly apply the current page "
                    "before scheduling indexing"
                ],
                "slugs": unavailable,
            },
        )

    schedule = IndexPromotionSchedule(
        tenant_id=project.tenant_id,
        project_id=project.id,
        site_id=site.id,
        reason=body.reason.strip(),
        starts_at=body.starts_at,
        interval_hours=body.interval_hours,
        batch_size=body.batch_size,
        created_by=auth.user.id,
    )
    db.add(schedule)
    await db.flush()
    for index, slug in enumerate(body.slugs):
        draft, source_hash = eligible[slug]
        batch_number = index // body.batch_size + 1
        db.add(
            IndexPromotionScheduleItem(
                schedule_id=schedule.id,
                tenant_id=project.tenant_id,
                project_id=project.id,
                site_id=site.id,
                page_draft_id=draft.id,
                slug=slug,
                source_hash=source_hash,
                qa_source_hash=draft.content_hash,
                batch_number=batch_number,
                planned_at=body.starts_at
                + timedelta(hours=body.interval_hours * (batch_number - 1)),
            )
        )
    await append_audit(
        db,
        action="page.index_schedule.create",
        payload={
            "project_id": str(project.id),
            "schedule_id": str(schedule.id),
            "item_count": len(body.slugs),
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return await _serialize_schedule(db, schedule)


@router.post("/{project_id}/index-schedules/{schedule_id}/submit-review")
async def submit_index_schedule(
    project_id: UUID,
    schedule_id: UUID,
    body: IndexPromotionScheduleDecision,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="Explicit schedule confirmation is required")
    project = await _project_or_404(db, project_id, auth)
    schedule = (
        await db.execute(
            select(IndexPromotionSchedule)
            .where(
                IndexPromotionSchedule.id == schedule_id,
                IndexPromotionSchedule.project_id == project.id,
                IndexPromotionSchedule.tenant_id == project.tenant_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not schedule or schedule.state != "draft":
        raise HTTPException(status_code=409, detail="Only a draft index schedule can be submitted")
    schedule.state = "review"
    await append_audit(
        db,
        action="page.index_schedule.submit_review",
        payload={"project_id": str(project.id), "schedule_id": str(schedule.id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return await _serialize_schedule(db, schedule)


@router.post("/{project_id}/index-schedules/{schedule_id}/approve")
async def approve_index_schedule(
    project_id: UUID,
    schedule_id: UUID,
    body: IndexPromotionScheduleDecision,
    auth: AuthContext = Depends(_REVIEW),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="Explicit schedule approval is required")
    project = await _project_or_404(db, project_id, auth)
    schedule = (
        await db.execute(
            select(IndexPromotionSchedule)
            .where(
                IndexPromotionSchedule.id == schedule_id,
                IndexPromotionSchedule.project_id == project.id,
                IndexPromotionSchedule.tenant_id == project.tenant_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not schedule or schedule.state != "review":
        raise HTTPException(status_code=409, detail="Only a schedule under review can be approved")
    schedule.state = "active"
    schedule.approved_by = auth.user.id
    schedule.approved_at = datetime.now(UTC)
    await append_audit(
        db,
        action="page.index_schedule.approve",
        payload={"project_id": str(project.id), "schedule_id": str(schedule.id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return await _serialize_schedule(db, schedule)


@router.post("/{project_id}/index-schedules/{schedule_id}/{action}")
async def control_index_schedule(
    project_id: UUID,
    schedule_id: UUID,
    action: str,
    body: IndexPromotionScheduleDecision,
    auth: AuthContext = Depends(_REVIEW),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if action not in {"pause", "cancel"} or not body.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Explicit supported schedule action is required",
        )
    project = await _project_or_404(db, project_id, auth)
    schedule = (
        await db.execute(
            select(IndexPromotionSchedule)
            .where(
                IndexPromotionSchedule.id == schedule_id,
                IndexPromotionSchedule.project_id == project.id,
                IndexPromotionSchedule.tenant_id == project.tenant_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not schedule or schedule.state not in {"active", "paused"}:
        raise HTTPException(
            status_code=409,
            detail="Only an active or paused schedule can be changed",
        )
    schedule.state = "paused" if action == "pause" else "cancelled"
    schedule.paused_at = datetime.now(UTC) if action == "pause" else schedule.paused_at
    await append_audit(
        db,
        action=f"page.index_schedule.{action}",
        payload={"project_id": str(project.id), "schedule_id": str(schedule.id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return await _serialize_schedule(db, schedule)
