"""Atomic UTC rollups, bounded raw retention and session-level consent withdrawal."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from app.core.config import get_settings
from app.models import Site
from app.models.leads import AnalyticsDailyAggregate, AnalyticsEvent, AnalyticsRevokedSession
from app.services.telemetry import _ALLOWED_EVENTS
from sqlalchemy import delete, func, select, text, union_all
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

_DAILY_LOCK = 734589213
_SESSION_LOCK_NAMESPACE = 734589214
_LOW_SAMPLE_THRESHOLD = 5


def _utc_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _event_day():
    return func.date(func.timezone("UTC", AnalyticsEvent.created_at))


def _valid_first_party():
    return (
        AnalyticsEvent.source == "first_party",
        AnalyticsEvent.site_id.is_not(None),
        select(Site.id)
        .where(
            Site.id == AnalyticsEvent.site_id,
            Site.tenant_id == AnalyticsEvent.tenant_id,
        )
        .exists(),
        AnalyticsEvent.event.in_(sorted(_ALLOWED_EVENTS)),
        AnalyticsEvent.path.op("~")(r"^/[^?#]{0,511}$"),
    )


def _rollup_statement(
    *,
    before: datetime,
    tenant_id: UUID | None = None,
    site_id: UUID | None = None,
    days: list[date] | None = None,
):
    day = _event_day()
    source = (
        select(
            AnalyticsEvent.tenant_id,
            AnalyticsEvent.site_id,
            day.label("event_date"),
            AnalyticsEvent.event,
            AnalyticsEvent.path,
            func.count(AnalyticsEvent.id).label("event_count"),
            func.count(func.distinct(AnalyticsEvent.payload["session"].astext)).label(
                "session_days"
            ),
        )
        .where(*_valid_first_party(), AnalyticsEvent.created_at < before)
        .group_by(
            AnalyticsEvent.tenant_id,
            AnalyticsEvent.site_id,
            day,
            AnalyticsEvent.event,
            AnalyticsEvent.path,
        )
    )
    if tenant_id is not None:
        source = source.where(AnalyticsEvent.tenant_id == tenant_id)
    if site_id is not None:
        source = source.where(AnalyticsEvent.site_id == site_id)
    if days is not None:
        source = source.where(day.in_(days))
    statement = insert(AnalyticsDailyAggregate).from_select(
        ["tenant_id", "site_id", "event_date", "event", "path", "event_count", "session_days"],
        source,
    )
    return statement.on_conflict_do_update(
        index_elements=[
            AnalyticsDailyAggregate.tenant_id,
            AnalyticsDailyAggregate.site_id,
            AnalyticsDailyAggregate.event_date,
            AnalyticsDailyAggregate.event,
            AnalyticsDailyAggregate.path,
        ],
        set_={
            "event_count": statement.excluded.event_count,
            "session_days": statement.excluded.session_days,
            "updated_at": func.now(),
        },
    )


async def rollup_and_purge(db: AsyncSession, *, now: datetime | None = None) -> dict:
    """Rebuild recent complete days, preserve older totals, then purge in one transaction."""
    now = now or datetime.now(UTC)
    settings = get_settings()
    today = now.date()
    today_start = _utc_midnight(today)
    raw_cutoff = _utc_midnight(today - timedelta(days=settings.telemetry_raw_retention_days))
    aggregate_cutoff = today - timedelta(days=settings.telemetry_aggregate_retention_days)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _DAILY_LOCK})
    # Older days already purged are final; only days still backed by raw data
    # can be rebuilt (including groups that disappeared following withdrawal).
    await db.execute(
        delete(AnalyticsDailyAggregate).where(
            AnalyticsDailyAggregate.event_date >= raw_cutoff.date(),
            AnalyticsDailyAggregate.event_date < today,
        )
    )
    await db.execute(_rollup_statement(before=today_start))
    removed = await db.scalar(
        select(func.count(AnalyticsEvent.id)).where(AnalyticsEvent.created_at < raw_cutoff)
    )
    await db.execute(delete(AnalyticsEvent).where(AnalyticsEvent.created_at < raw_cutoff))
    await db.execute(
        delete(AnalyticsDailyAggregate).where(AnalyticsDailyAggregate.event_date < aggregate_cutoff)
    )
    await db.execute(
        delete(AnalyticsRevokedSession).where(AnalyticsRevokedSession.expires_at <= now)
    )
    await db.commit()
    return {
        "raw_deleted": int(removed or 0),
        "raw_retention_days": settings.telemetry_raw_retention_days,
    }


async def lock_session(db: AsyncSession, digest: str) -> None:
    """Serialize collection and withdrawal of the same pseudonymous browser session."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:digest))"),
        {"namespace": _SESSION_LOCK_NAMESPACE, "digest": digest},
    )


async def session_was_revoked(
    db: AsyncSession, *, tenant_id: UUID, site_id: UUID, digest: str, now: datetime
) -> bool:
    return bool(
        await db.scalar(
            select(AnalyticsRevokedSession.session_digest).where(
                AnalyticsRevokedSession.tenant_id == tenant_id,
                AnalyticsRevokedSession.site_id == site_id,
                AnalyticsRevokedSession.session_digest == digest,
                AnalyticsRevokedSession.expires_at > now,
            )
        )
    )


async def revoke_session(
    db: AsyncSession, *, tenant_id: UUID, site_id: UUID, digest: str, now: datetime | None = None
) -> int:
    """Delete matching raw rows and rebuild impacted complete days, atomically."""
    now = now or datetime.now(UTC)
    await lock_session(db, digest)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _DAILY_LOCK})
    selector = (
        AnalyticsEvent.tenant_id == tenant_id,
        AnalyticsEvent.site_id == site_id,
        AnalyticsEvent.source == "first_party",
        AnalyticsEvent.payload["session"].astext == digest,
    )
    affected = (
        (
            await db.execute(
                select(func.distinct(_event_day())).where(
                    *selector, AnalyticsEvent.created_at < _utc_midnight(now.date())
                )
            )
        )
        .scalars()
        .all()
    )
    removed = await db.scalar(select(func.count(AnalyticsEvent.id)).where(*selector))
    await db.execute(delete(AnalyticsEvent).where(*selector))
    if affected:
        await db.execute(
            delete(AnalyticsDailyAggregate).where(
                AnalyticsDailyAggregate.tenant_id == tenant_id,
                AnalyticsDailyAggregate.site_id == site_id,
                AnalyticsDailyAggregate.event_date.in_(affected),
            )
        )
        await db.execute(
            _rollup_statement(
                before=_utc_midnight(now.date()),
                tenant_id=tenant_id,
                site_id=site_id,
                days=affected,
            )
        )
    marker = insert(AnalyticsRevokedSession).values(
        tenant_id=tenant_id,
        site_id=site_id,
        session_digest=digest,
        expires_at=now + timedelta(hours=24),
    )
    await db.execute(
        marker.on_conflict_do_update(
            index_elements=[
                AnalyticsRevokedSession.tenant_id,
                AnalyticsRevokedSession.site_id,
                AnalyticsRevokedSession.session_digest,
            ],
            set_={"expires_at": marker.excluded.expires_at},
        )
    )
    await db.commit()
    return int(removed or 0)


async def page_view_summary(
    db: AsyncSession, *, tenant_id: UUID, site_id: UUID, days: int, now: datetime | None = None
) -> list[dict]:
    now = now or datetime.now(UTC)
    start_date = now.date() - timedelta(days=days - 1)
    raw_cutoff = _utc_midnight(
        now.date() - timedelta(days=get_settings().telemetry_raw_retention_days)
    )
    recent_start = max(_utc_midnight(start_date), raw_cutoff)
    historical = select(
        AnalyticsDailyAggregate.path.label("path"),
        AnalyticsDailyAggregate.event_count.label("views"),
        AnalyticsDailyAggregate.session_days.label("sessions"),
    ).where(
        AnalyticsDailyAggregate.tenant_id == tenant_id,
        AnalyticsDailyAggregate.site_id == site_id,
        AnalyticsDailyAggregate.event == "page_view",
        AnalyticsDailyAggregate.event_date >= start_date,
        AnalyticsDailyAggregate.event_date < raw_cutoff.date(),
    )
    recent = (
        select(
            AnalyticsEvent.path.label("path"),
            func.count(AnalyticsEvent.id).label("views"),
            func.count(func.distinct(AnalyticsEvent.payload["session"].astext)).label("sessions"),
        )
        .where(
            *_valid_first_party(),
            AnalyticsEvent.tenant_id == tenant_id,
            AnalyticsEvent.site_id == site_id,
            AnalyticsEvent.event == "page_view",
            AnalyticsEvent.created_at >= recent_start,
            AnalyticsEvent.created_at <= now,
        )
        .group_by(AnalyticsEvent.path, _event_day())
    )
    combined = union_all(historical, recent).subquery()
    views = func.sum(combined.c.views)
    sessions = func.sum(combined.c.sessions)
    rows = (
        await db.execute(
            select(combined.c.path, views, sessions)
            .group_by(combined.c.path)
            .order_by(views.desc(), combined.c.path)
            .limit(500)
        )
    ).all()
    return [
        {
            "path": path,
            "page_views": int(view_count) if session_count >= _LOW_SAMPLE_THRESHOLD else None,
            "consented_session_days": (
                int(session_count) if session_count >= _LOW_SAMPLE_THRESHOLD else None
            ),
            "low_sample": session_count < _LOW_SAMPLE_THRESHOLD,
            "traffic_state": (
                "not_enough_data"
                if session_count < _LOW_SAMPLE_THRESHOLD
                else "low_traffic"
                if view_count < 10
                else "observed"
            ),
        }
        for path, view_count, session_count in rows
    ]
