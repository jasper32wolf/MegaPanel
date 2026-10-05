"""Prepare due index batches without publishing or bypassing current-content checks."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.api.v1.projects import (
    _current_qa_run,
    _draft_manifest_hash,
    _freeze_candidate_build_input,
)
from app.models import (
    IndexPromotionSchedule,
    IndexPromotionScheduleItem,
    PageDraft,
    PageIndexPromotion,
    Project,
    Site,
    SiteBuild,
)
from app.services.audit import append_audit
from app.services.site_build_queue import append_site_build_event
from site_panel_shared.manifests import PageManifest, SiteManifest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def _prior_batches_are_published(
    db: AsyncSession,
    *,
    items: list[IndexPromotionScheduleItem],
    next_batch: int,
) -> bool:
    build_ids = [
        item.candidate_build_id
        for item in items
        if item.batch_number < next_batch and item.candidate_build_id is not None
    ]
    if not build_ids:
        return True
    builds = list(
        (await db.execute(select(SiteBuild).where(SiteBuild.id.in_(build_ids)))).scalars().all()
    )
    return len(builds) == len(build_ids) and all(build.first_published_at for build in builds)


async def _site_has_pending_candidate(db: AsyncSession, site_id: UUID) -> bool:
    return bool(
        (
            await db.execute(
                select(SiteBuild.id)
                .where(SiteBuild.site_id == site_id, SiteBuild.status.in_(("queued", "running")))
                .limit(1)
            )
        ).scalar_one_or_none()
    )


async def _pause_stale_schedule(
    db: AsyncSession,
    *,
    schedule: IndexPromotionSchedule,
    items: list[IndexPromotionScheduleItem],
    reason: str,
) -> None:
    schedule.state = "paused"
    schedule.paused_at = datetime.now(UTC)
    for item in items:
        item.state = "stale"
        item.stale_reason = reason
    await append_audit(
        db,
        action="page.index_schedule.paused_stale",
        payload={"schedule_id": str(schedule.id), "reason": reason},
        tenant_id=schedule.tenant_id,
        actor_id=None,
    )


async def _validate_batch(
    db: AsyncSession,
    *,
    schedule: IndexPromotionSchedule,
    items: list[IndexPromotionScheduleItem],
) -> str | None:
    project = await db.get(Project, schedule.project_id)
    site = await db.get(Site, schedule.site_id)
    if (
        project is None
        or site is None
        or project.tenant_id != schedule.tenant_id
        or site.tenant_id != schedule.tenant_id
        or site.project_id not in {None, project.id}
    ):
        return "scope_changed"
    manifest = SiteManifest.model_validate(site.manifest)
    pages = {page.slug: page for page in manifest.pages}
    for item in items:
        page = pages.get(item.slug)
        draft = await db.get(PageDraft, item.page_draft_id)
        if page is None or _draft_manifest_hash(page.model_dump(mode="json")) != item.source_hash:
            return "content_changed"
        if (
            draft is None
            or draft.tenant_id != schedule.tenant_id
            or draft.project_id != schedule.project_id
            or draft.state != "applied"
            or draft.content_hash != item.qa_source_hash
            or not draft.page_manifest
            or _draft_manifest_hash(draft.page_manifest) != item.qa_source_hash
            or not _current_qa_run(draft)
            or draft.last_qa_verdict != "pass"
            or PageManifest.model_validate(draft.page_manifest).slug != item.slug
        ):
            return "qa_or_draft_changed"
    return None


async def prepare_due_index_schedule_batches(
    db: AsyncSession, *, limit: int = 1
) -> list[dict]:
    """Create at most ``limit`` frozen candidates; never activate or publish them."""
    now = datetime.now(UTC)
    schedules = list(
        (
            await db.execute(
                select(IndexPromotionSchedule)
                .where(IndexPromotionSchedule.state == "active")
                .order_by(IndexPromotionSchedule.starts_at, IndexPromotionSchedule.created_at)
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    prepared: list[dict] = []
    for schedule in schedules:
        items = list(
            (
                await db.execute(
                    select(IndexPromotionScheduleItem)
                    .where(IndexPromotionScheduleItem.schedule_id == schedule.id)
                    .order_by(
                        IndexPromotionScheduleItem.batch_number,
                        IndexPromotionScheduleItem.slug,
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        planned = [item for item in items if item.state == "planned" and item.planned_at <= now]
        if not planned:
            if items and all(item.state == "prepared" for item in items):
                if await _prior_batches_are_published(
                    db, items=items, next_batch=max(item.batch_number for item in items) + 1
                ):
                    schedule.state = "completed"
                    schedule.completed_at = now
            continue
        batch_number = min(item.batch_number for item in planned)
        batch = [item for item in planned if item.batch_number == batch_number]
        if not await _prior_batches_are_published(db, items=items, next_batch=batch_number):
            continue
        if await _site_has_pending_candidate(db, schedule.site_id):
            continue
        stale_reason = await _validate_batch(db, schedule=schedule, items=batch)
        if stale_reason:
            await _pause_stale_schedule(
                db, schedule=schedule, items=batch, reason=stale_reason
            )
            continue

        project = await db.get(Project, schedule.project_id)
        site = await db.get(Site, schedule.site_id)
        assert project is not None and site is not None
        for item in batch:
            promotion = PageIndexPromotion(
                tenant_id=schedule.tenant_id,
                site_id=schedule.site_id,
                project_id=schedule.project_id,
                page_draft_id=item.page_draft_id,
                slug=item.slug,
                source_hash=item.source_hash,
                qa_source_hash=item.qa_source_hash,
                reason=f"План постепенной индексации: {schedule.reason}",
                decided_by=schedule.approved_by,
                decided_at=now,
            )
            db.add(promotion)
            await db.flush()
            item.promotion_id = promotion.id
        snapshot, snapshot_hash = await _freeze_candidate_build_input(
            db, project=project, site=site
        )
        build = SiteBuild(
            site_id=site.id,
            tenant_id=site.tenant_id,
            project_id=project.id,
            status="queued",
            previous_build_hash=site.build_hash,
            input_snapshot=snapshot,
            input_snapshot_hash=snapshot_hash,
            snapshot_version=1,
            manifest_snapshot=snapshot["manifest"],
            page_plan_ids=snapshot["page_plan_ids"],
            requested_by=schedule.created_by,
            queue_priority=25,
            last_enqueued_at=now,
        )
        db.add(build)
        await db.flush()
        await append_site_build_event(
            db,
            build=build,
            event_type="queued",
            details={"index_schedule_id": str(schedule.id), "batch_number": batch_number},
        )
        for item in batch:
            item.state = "prepared"
            item.candidate_build_id = build.id
            item.prepared_at = now
        await append_audit(
            db,
            action="page.index_schedule.batch_prepared",
            payload={
                "schedule_id": str(schedule.id),
                "batch_number": batch_number,
                "build_id": str(build.id),
                "item_count": len(batch),
            },
            tenant_id=schedule.tenant_id,
            actor_id=None,
        )
        prepared.append(
            {
                "schedule_id": str(schedule.id),
                "batch_number": batch_number,
                "build_id": str(build.id),
            }
        )
    if prepared or any(schedule.state in {"paused", "completed"} for schedule in schedules):
        await db.commit()
    return prepared
