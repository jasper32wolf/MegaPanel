"""PostgreSQL-owned fair scheduler for allowlisted candidate work."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.config import get_settings
from app.core.security import sha256_hex
from app.models import (
    CompetitorCrawlRun,
    ProjectBukvarixKeywordRun,
    SchedulerAttempt,
    SchedulerJob,
    SchedulerProjectTurn,
    SchedulerWakeup,
    SiteBuild,
)
from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

_WORK_TYPE_BUKVARIX_KEYWORD = "bukvarix_keyword"
_WORK_TYPE_COMPETITOR_CRAWL = "competitor_crawl"
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
    await db.flush()
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


async def create_bukvarix_keyword_job(
    db: AsyncSession, *, run: ProjectBukvarixKeywordRun
) -> SchedulerJob:
    """Bind a frozen, preview-only Bukvarix run to an idempotent scheduler job."""
    if run.status != "queued" or not run.seed_snapshot_hash:
        raise ValueError("Only queued Bukvarix runs with a frozen seed snapshot can be scheduled")
    existing = await db.scalar(
        select(SchedulerJob).where(
            SchedulerJob.work_type == _WORK_TYPE_BUKVARIX_KEYWORD,
            SchedulerJob.source_id == run.id,
        )
    )
    if existing:
        if (
            existing.tenant_id != run.tenant_id
            or existing.project_id != run.project_id
            or existing.source_hash != run.seed_snapshot_hash
        ):
            raise ValueError("Scheduler job source identity mismatch")
        return existing
    job = SchedulerJob(
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        work_type=_WORK_TYPE_BUKVARIX_KEYWORD,
        source_id=run.id,
        source_hash=run.seed_snapshot_hash,
        source_version=1,
        priority=50,
        requested_by=run.requested_by,
    )
    db.add(job)
    await db.flush()
    await append_scheduler_attempt(db, job=job, event_type="queued")
    return job


def competitor_crawl_source_hash(crawl: CompetitorCrawlRun) -> str:
    return sha256_hex(
        json.dumps(
            {
                "root_url": crawl.root_url,
                "origin": crawl.origin,
                "configuration": crawl.configuration or {},
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


async def create_competitor_crawl_job(
    db: AsyncSession, *, crawl: CompetitorCrawlRun, requested_by: UUID
) -> SchedulerJob:
    """Queue only a bounded, persisted HTTPS research run; evidence stays unapproved."""
    if crawl.status != "queued" or not crawl.project_id or not crawl.tenant_id:
        raise ValueError("Only queued project crawls can be scheduled")
    source_hash = competitor_crawl_source_hash(crawl)
    existing = await db.scalar(
        select(SchedulerJob).where(
            SchedulerJob.work_type == _WORK_TYPE_COMPETITOR_CRAWL,
            SchedulerJob.source_id == crawl.id,
        )
    )
    if existing:
        if (
            existing.tenant_id != crawl.tenant_id
            or existing.project_id != crawl.project_id
            or existing.source_hash != source_hash
        ):
            raise ValueError("Scheduler job source identity mismatch")
        return existing
    job = SchedulerJob(
        tenant_id=crawl.tenant_id,
        project_id=crawl.project_id,
        work_type=_WORK_TYPE_COMPETITOR_CRAWL,
        source_id=crawl.id,
        source_hash=source_hash,
        source_version=1,
        priority=50,
        requested_by=requested_by,
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


async def _expire_pending_wakeups(db: AsyncSession, job: SchedulerJob) -> None:
    if job.lease_id is not None:
        await db.execute(
            update(SchedulerWakeup)
            .where(
                SchedulerWakeup.scheduler_job_id == job.id,
                SchedulerWakeup.lease_id == job.lease_id,
                SchedulerWakeup.state == "pending",
            )
            .values(state="expired")
        )


async def recover_expired_scheduler_leases(db: AsyncSession) -> int:
    """Fence timed-out workers; requeue only work that never started."""
    now = _now()
    jobs = list(
        (
            await db.execute(
                select(SchedulerJob)
                .where(
                    SchedulerJob.state.in_(("leased", "running", "cancel_requested")),
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
        await _expire_pending_wakeups(db, job)
        if job.work_type == _WORK_TYPE_BUKVARIX_KEYWORD:
            source = await db.get(ProjectBukvarixKeywordRun, job.source_id, with_for_update=True)
        elif job.work_type == _WORK_TYPE_COMPETITOR_CRAWL:
            source = await db.get(CompetitorCrawlRun, job.source_id, with_for_update=True)
        else:
            source = await db.get(SiteBuild, job.source_id, with_for_update=True)
        if job.state == "cancel_requested" or (source and source.status == "cancelled"):
            job.state = "cancelled"
            job.cancelled_at = now
            if (
                source
                and source.status in {"queued", "running"}
                and job.work_type != _WORK_TYPE_SITE_BUILD
            ):
                source.status = "cancelled"
            job.failure_code = None
        elif source and source.status in {"ready", "completed", "done", "partial"}:
            job.state = "succeeded"
            job.failure_code = None
        elif job.state == "leased" and source is not None and source.status == "queued":
            job.state = "queued"
            job.eligible_at = now
            job.queued_at = now
            job.failure_code = None
        else:
            job.state = "failed"
            job.failure_code = "worker_timeout"
            if source and source.status in {"queued", "running"}:
                source.status = "failed"
                if job.work_type == _WORK_TYPE_SITE_BUILD:
                    source.failure_code = "worker_timeout"
                    source.completed_at = now
                    source.lease_expires_at = None
                elif job.work_type == _WORK_TYPE_BUKVARIX_KEYWORD:
                    source.failure_code = "worker_timeout"
                    source.completed_at = now
                else:
                    source.error_code = "worker_timeout"
                    source.finished_at = now
        if job.state != "queued":
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
    active = ("leased", "running", "cancel_requested")
    count = int(
        await db.scalar(select(func.count(SchedulerJob.id)).where(SchedulerJob.state.in_(active)))
        or 0
    )
    # Existing direct ARQ jobs still consume the same VPS resources during migration.
    for source, work_type in (
        (SiteBuild, _WORK_TYPE_SITE_BUILD),
        (ProjectBukvarixKeywordRun, _WORK_TYPE_BUKVARIX_KEYWORD),
        (CompetitorCrawlRun, _WORK_TYPE_COMPETITOR_CRAWL),
    ):
        managed = select(SchedulerJob.id).where(
            SchedulerJob.source_id == source.id,
            SchedulerJob.work_type == work_type,
            SchedulerJob.state.in_(active),
        )
        count += int(
            await db.scalar(
                select(func.count(source.id)).where(source.status == "running", ~managed.exists())
            )
            or 0
        )
    return count


async def dispatch_due_jobs(db: AsyncSession, *, limit: int = _MAX_CONCURRENCY) -> list[UUID]:
    """Serialize capacity reservations and rotate between eligible projects."""
    await db.execute(text("SELECT pg_advisory_xact_lock(1852659297)"))
    slots = max(0, min(limit, _MAX_CONCURRENCY - await _active_slots(db)))
    if not slots:
        await db.commit()
        return []
    now = _now()
    due = (
        SchedulerJob.state == "queued",
        SchedulerJob.eligible_at <= now,
        or_(SchedulerJob.not_before.is_(None), SchedulerJob.not_before <= now),
    )
    project_keys = list(
        (
            await db.execute(
                select(SchedulerJob.tenant_id, SchedulerJob.project_id)
                .where(*due, SchedulerJob.project_id.is_not(None))
                .distinct()
            )
        ).all()
    )
    if not project_keys:
        await db.commit()
        return []
    turns = {
        (turn.tenant_id, turn.project_id): turn
        for turn in (
            await db.execute(
                select(SchedulerProjectTurn).where(
                    SchedulerProjectTurn.project_id.in_(
                        tuple(project_id for _, project_id in project_keys)
                    )
                )
            )
        )
        .scalars()
        .all()
    }
    active_keys = set(
        (
            await db.execute(
                select(SchedulerJob.tenant_id, SchedulerJob.project_id).where(
                    SchedulerJob.state.in_(("leased", "running", "cancel_requested"))
                )
            )
        ).all()
    )
    ordered_keys = sorted(
        project_keys,
        key=lambda key: (turns[key].dispatch_turn if key in turns else -1, str(key[1])),
    )
    effective_priority = func.least(
        100,
        SchedulerJob.priority
        + func.floor(func.extract("epoch", func.now() - SchedulerJob.first_eligible_at) / 3600),
    )
    selected: list[SchedulerJob] = []
    next_turn = max((turn.dispatch_turn for turn in turns.values()), default=0)
    for tenant_id, project_id in ordered_keys:
        if len(selected) >= slots:
            break
        key = (tenant_id, project_id)
        if key in active_keys:
            continue
        job = await db.scalar(
            select(SchedulerJob)
            .where(*due, SchedulerJob.tenant_id == tenant_id, SchedulerJob.project_id == project_id)
            .order_by(effective_priority.desc(), SchedulerJob.first_eligible_at, SchedulerJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            continue
        lease_id = uuid.uuid4()
        job.state = "leased"
        job.attempt_count += 1
        job.lease_id = lease_id
        job.leased_at = now
        job.lease_expires_at = now + timedelta(seconds=_LEASE_SECONDS)
        job.failure_code = None
        await append_scheduler_attempt(db, job=job, event_type="claimed")
        db.add(SchedulerWakeup(scheduler_job_id=job.id, tenant_id=job.tenant_id, lease_id=lease_id))
        turn = turns.get(key)
        if turn is None:
            turn = SchedulerProjectTurn(tenant_id=tenant_id, project_id=project_id)
            db.add(turn)
            turns[key] = turn
        next_turn += 1
        turn.dispatch_turn = next_turn
        turn.last_dispatched_at = now
        active_keys.add(key)
        selected.append(job)
    await db.commit()
    return [job.id for job in selected]


async def publish_pending_wakeups(db: AsyncSession, *, limit: int = 20) -> int:
    """Publish only the current lease; hold the job lock until its event is committed."""
    wakeup_ids = list(
        (
            await db.scalars(
                select(SchedulerWakeup.id)
                .where(SchedulerWakeup.state == "pending", SchedulerWakeup.available_at <= _now())
                .order_by(SchedulerWakeup.created_at, SchedulerWakeup.id)
                .limit(limit)
            )
        ).all()
    )
    published = 0
    for wakeup_id in wakeup_ids:
        source = await db.get(SchedulerWakeup, wakeup_id)
        if source is None:
            continue
        job = await db.scalar(
            select(SchedulerJob)
            .where(SchedulerJob.id == source.scheduler_job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        wakeup = await db.scalar(
            select(SchedulerWakeup)
            .where(SchedulerWakeup.id == wakeup_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if wakeup is None or wakeup.state != "pending":
            await db.commit()
            continue
        if (
            job is None
            or job.tenant_id != wakeup.tenant_id
            or job.state != "leased"
            or job.lease_id != wakeup.lease_id
            or job.lease_expires_at is None
            or job.lease_expires_at <= _now()
        ):
            wakeup.state = "expired"
            await db.commit()
            continue
        try:
            pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
            try:
                await pool.enqueue_job("scheduler_execute_task", str(job.id), str(wakeup.lease_id))
            finally:
                await pool.aclose()
        except Exception:  # noqa: BLE001
            wakeup.attempt_count += 1
            wakeup.available_at = _now() + timedelta(seconds=30)
            await db.commit()
            continue
        wakeup.state = "published"
        wakeup.published_at = _now()
        await append_scheduler_attempt(db, job=job, event_type="wake_published")
        await db.commit()
        published += 1
    return published


async def _source_status(db: AsyncSession, job: SchedulerJob) -> tuple[str | None, str | None]:
    """Validate the frozen identity before calling an external or filesystem worker."""
    if job.work_type == _WORK_TYPE_SITE_BUILD:
        source = await db.get(SiteBuild, job.source_id)
        if not source or not source.input_snapshot:
            return None, "source_missing"
        if (
            source.tenant_id != job.tenant_id
            or source.project_id != job.project_id
            or source.site_id != job.site_id
            or source.snapshot_version != job.source_version
            or source.input_snapshot_hash != job.source_hash
            or sha256_hex(
                json.dumps(
                    source.input_snapshot,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            != job.source_hash
        ):
            return None, "source_mismatch"
    elif job.work_type == _WORK_TYPE_BUKVARIX_KEYWORD:
        source = await db.get(ProjectBukvarixKeywordRun, job.source_id)
        if not source:
            return None, "source_missing"
        if (
            source.tenant_id != job.tenant_id
            or source.project_id != job.project_id
            or source.seed_snapshot_hash != job.source_hash
            or sha256_hex(
                json.dumps(
                    source.seed_snapshot,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            != job.source_hash
        ):
            return None, "source_mismatch"
    elif job.work_type == _WORK_TYPE_COMPETITOR_CRAWL:
        source = await db.get(CompetitorCrawlRun, job.source_id)
        if not source:
            return None, "source_missing"
        if (
            source.tenant_id != job.tenant_id
            or source.project_id != job.project_id
            or competitor_crawl_source_hash(source) != job.source_hash
        ):
            return None, "source_mismatch"
    else:
        return None, "unsupported_work_type"
    return source.status, None


async def claim_execution(db: AsyncSession, job_id: UUID, lease_id: UUID) -> SchedulerJob | None:
    job = await db.scalar(select(SchedulerJob).where(SchedulerJob.id == job_id).with_for_update())
    if not job or job.state != "leased" or job.lease_id != lease_id:
        return None
    if job.lease_expires_at is None or job.lease_expires_at <= _now():
        return None
    source_status, error = await _source_status(db, job)
    if error or source_status != "queued":
        await complete_execution(
            db,
            job_id=job.id,
            lease_id=lease_id,
            result={
                "status": source_status or "failed",
                "error_code": error or "source_not_queued",
            },
        )
        return None
    job.state = "running"
    job.started_at = _now()
    job.lease_expires_at = job.started_at + timedelta(seconds=_LEASE_SECONDS)
    await append_scheduler_attempt(db, job=job, event_type="started")
    await db.commit()
    return job


async def complete_execution(
    db: AsyncSession, *, job_id: UUID, lease_id: UUID, result: dict
) -> dict:
    """Fence completion by the exact lease that started the source work."""
    job = await db.scalar(select(SchedulerJob).where(SchedulerJob.id == job_id).with_for_update())
    if (
        not job
        or job.lease_id != lease_id
        or job.state not in {"leased", "running", "cancel_requested"}
    ):
        return {"status": "stale_lease", "scheduler_job_id": str(job_id)}
    if job.state == "cancel_requested" or result.get("status") == "cancelled":
        job.state = "cancelled"
        job.cancelled_at = _now()
        event_type = "cancelled"
        code = None
    elif result.get("status") in {"ready", "completed", "done", "partial"}:
        job.state = "succeeded"
        job.failure_code = None
        event_type = "succeeded"
        code = None
    else:
        job.state = "failed"
        proposed_code = str(result.get("error_code") or "source_execution_failed")
        job.failure_code = (
            proposed_code if _SAFE_CODE.fullmatch(proposed_code) else "source_execution_failed"
        )
        event_type = "failed"
        code = job.failure_code
    job.finished_at = _now()
    job.lease_id = None
    job.lease_expires_at = None
    await append_scheduler_attempt(db, job=job, event_type=event_type, safe_code=code)
    await db.commit()
    return result


async def pause_job(db: AsyncSession, *, job: SchedulerJob) -> None:
    if job.state != "queued":
        raise ValueError("Only queued scheduled work can be paused")
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
    """Cancel pending work or request cooperative cancellation of an active crawl."""
    if job.state not in {"queued", "paused", "leased", "running"}:
        raise ValueError("Scheduled work cannot be cancelled in its current state")
    if job.state == "running" and job.work_type != _WORK_TYPE_COMPETITOR_CRAWL:
        raise ValueError("An active build or Bukvarix request cannot be interrupted")
    now = _now()
    if job.work_type == _WORK_TYPE_BUKVARIX_KEYWORD:
        source = await db.get(ProjectBukvarixKeywordRun, job.source_id, with_for_update=True)
    elif job.work_type == _WORK_TYPE_COMPETITOR_CRAWL:
        source = await db.get(CompetitorCrawlRun, job.source_id, with_for_update=True)
    else:
        source = await db.get(SiteBuild, job.source_id, with_for_update=True)
    if source is None or source.status not in {"queued", "running"}:
        raise ValueError("Source work is no longer cancellable")
    if job.state == "running":
        source.status = "cancelled"
        source.cancelled_at = now
        job.state = "cancel_requested"
        job.cancel_requested_at = now
        await append_scheduler_attempt(db, job=job, event_type="cancel_requested")
        return
    await _expire_pending_wakeups(db, job)
    if job.work_type == _WORK_TYPE_SITE_BUILD:
        source.status = "failed"
        source.failure_code = "cancelled_by_operator"
        source.completed_at = now
        source.lease_expires_at = None
    elif job.work_type == _WORK_TYPE_BUKVARIX_KEYWORD:
        source.status = "cancelled"
        source.completed_at = now
    else:
        source.status = "cancelled"
        source.cancelled_at = now
    job.state = "cancelled"
    job.cancelled_at = now
    job.finished_at = now
    job.lease_id = None
    job.lease_expires_at = None
    await append_scheduler_attempt(db, job=job, event_type="cancelled")


__all__ = [
    "append_scheduler_attempt",
    "cancel_job",
    "claim_execution",
    "complete_execution",
    "create_bukvarix_keyword_job",
    "create_site_build_job",
    "dispatch_due_jobs",
    "pause_job",
    "publish_pending_wakeups",
    "recover_expired_scheduler_leases",
    "requeue_site_build_job",
    "resume_job",
]
