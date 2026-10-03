"""Durable transport and safe event helpers for candidate builds."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID

from app.core.config import get_settings
from app.models import SiteBuild, SiteBuildEvent
from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

_EVENT_TYPES = {
    "queued",
    "enqueue_deferred",
    "started",
    "ready",
    "failed",
    "retry_requested",
    "worker_lease_expired",
}
_SAFE_CODE = re.compile(r"^[a-z0-9_]{1,64}$")
_MAX_EVENTS_PER_ATTEMPT = 20


async def enqueue_site_build(build_id: UUID) -> None:
    """Enqueue only a durable build identifier; no mutable build input leaves PostgreSQL."""
    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        await pool.enqueue_job("candidate_build_task", str(build_id))
    finally:
        await pool.aclose()


async def append_site_build_event(
    db: AsyncSession,
    *,
    build: SiteBuild,
    event_type: str,
    safe_code: str | None = None,
    details: dict | None = None,
) -> SiteBuildEvent:
    """Append bounded, allowlisted operator metadata without storing raw execution output."""
    if event_type not in _EVENT_TYPES:
        raise ValueError("Unsupported site build event type")
    if safe_code is not None and not _SAFE_CODE.fullmatch(safe_code):
        raise ValueError("Invalid site build safe code")
    payload = details or {}
    if not isinstance(payload, dict) or len(str(payload).encode("utf-8")) > 4096:
        raise ValueError("Site build event details exceed the safe limit")
    if build.project_id is None:
        raise ValueError("Site build event requires a project")

    current_count = (
        await db.execute(
            select(func.count(SiteBuildEvent.id)).where(
                SiteBuildEvent.site_build_id == build.id,
                SiteBuildEvent.attempt == build.attempt_count,
            )
        )
    ).scalar_one()
    if current_count >= _MAX_EVENTS_PER_ATTEMPT:
        raise ValueError("Site build event limit reached")
    sequence = (
        await db.execute(
            select(func.coalesce(func.max(SiteBuildEvent.sequence), 0)).where(
                SiteBuildEvent.site_build_id == build.id
            )
        )
    ).scalar_one() + 1
    event = SiteBuildEvent(
        site_build_id=build.id,
        tenant_id=build.tenant_id,
        project_id=build.project_id,
        site_id=build.site_id,
        sequence=sequence,
        attempt=build.attempt_count,
        event_type=event_type,
        safe_code=safe_code,
        details=payload,
    )
    db.add(event)
    return event


async def due_site_build_ids(db: AsyncSession, *, limit: int = 20) -> list[UUID]:
    """Return durable queued work for transport recovery; terminal failures stay manual."""
    return list(
        (
            await db.execute(
                select(SiteBuild.id)
                .where(SiteBuild.status == "queued")
                .order_by(SiteBuild.created_at.asc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def expire_stale_site_builds(db: AsyncSession) -> int:
    """Make an abandoned worker lease visible and retryable; never rebuild automatically."""
    stale = list(
        (
            await db.execute(
                select(SiteBuild)
                .where(
                    SiteBuild.status == "running",
                    SiteBuild.lease_expires_at.is_not(None),
                    SiteBuild.lease_expires_at < datetime.now(UTC),
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    for build in stale:
        build.status = "failed"
        build.failure_code = "worker_timeout"
        build.completed_at = datetime.now(UTC)
        build.lease_expires_at = None
        await append_site_build_event(
            db, build=build, event_type="worker_lease_expired", safe_code="worker_timeout"
        )
    if stale:
        await db.commit()
    return len(stale)


__all__ = [
    "append_site_build_event",
    "due_site_build_ids",
    "enqueue_site_build",
    "expire_stale_site_builds",
]
