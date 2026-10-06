"""Optional migrated-PostgreSQL proof for rollups, withdrawal and tenant isolation."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.db.rls import set_tenant_rls
from app.db.session import open_db_session
from app.models import Site, Tenant
from app.models.leads import AnalyticsDailyAggregate, AnalyticsEvent, AnalyticsRevokedSession
from app.services.telemetry import session_digest
from app.services.telemetry_retention import (
    page_view_summary,
    revoke_session,
    rollup_and_purge,
    session_was_revoked,
)
from sqlalchemy import delete, func, select, text

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_RLS_INTEGRATION") != "1",
    reason="requires migrated PostgreSQL and role-management privileges",
)

_NOW = datetime(2026, 10, 6, 0, 15, tzinfo=UTC)
_ROLE = "site_panel_telemetry_rls_test"


def _event(tenant_id: UUID, site_id: UUID, *, day: datetime, session: str, path: str):
    return AnalyticsEvent(
        tenant_id=tenant_id,
        site_id=site_id,
        event="page_view",
        path=path,
        payload={"session": session_digest(session)},
        source="first_party",
        created_at=day,
    )


def test_rollup_retention_withdrawal_and_utc_boundaries():
    async def run() -> None:
        tenant_id, site_id = uuid4(), uuid4()
        yesterday = _NOW - timedelta(minutes=16)  # 23:59 UTC on the previous day.
        old = _NOW - timedelta(days=31)
        sessions = [f"browser-session-{index:04d}" for index in range(6)]
        try:
            async with open_db_session() as db:
                db.add(Tenant(id=tenant_id, name="Telemetry retention", slug=tenant_id.hex))
                db.add(
                    Site(
                        id=site_id,
                        tenant_id=tenant_id,
                        domain=f"{site_id.hex}.test",
                        lead_token=uuid4().hex,
                    )
                )
                await db.flush()
                for index, session in enumerate(sessions):
                    db.add(
                        _event(tenant_id, site_id, day=yesterday, session=session, path="/recent/")
                    )
                    if index < 5:
                        db.add(_event(tenant_id, site_id, day=old, session=session, path="/old/"))
                        db.add(
                            _event(tenant_id, site_id, day=_NOW, session=session, path="/today/")
                        )
                        db.add(
                            _event(
                                tenant_id, site_id, day=yesterday,
                                session=session, path="/repeat/",
                            )
                        )
                        db.add(
                            _event(tenant_id, site_id, day=_NOW, session=session, path="/repeat/")
                        )
                db.add(
                    _event(tenant_id, site_id, day=yesterday, session=sessions[0], path="/small/")
                )
                db.add(
                    AnalyticsEvent(
                        tenant_id=tenant_id,
                        site_id=site_id,
                        event="page_view",
                        path="/legacy/",
                        payload={"email": "private@example.test"},
                        source="legacy",
                        created_at=old,
                    )
                )
                db.add(
                    _event(tenant_id, uuid4(), day=old, session=sessions[0], path="/orphan/")
                )
                await db.commit()

            async with open_db_session() as db:
                result = await rollup_and_purge(db, now=_NOW)
                assert result["raw_deleted"] == 7
                rows = await page_view_summary(
                    db, tenant_id=tenant_id, site_id=site_id, days=90, now=_NOW
                )
                by_path = {row["path"]: row for row in rows}
                assert by_path["/old/"]["page_views"] == 5
                assert by_path["/recent/"]["page_views"] == 6
                assert by_path["/today/"]["page_views"] == 5
                assert by_path["/repeat/"]["consented_session_days"] == 10
                assert by_path["/small/"]["page_views"] is None
                assert by_path["/small/"]["consented_session_days"] is None
                assert "/legacy/" not in by_path
                assert await rollup_and_purge(db, now=_NOW) == {
                    "raw_deleted": 0,
                    "raw_retention_days": 30,
                }
                assert (
                    await page_view_summary(
                        db, tenant_id=tenant_id, site_id=site_id, days=90, now=_NOW
                    )
                    == rows
                )
                aggregate = await db.scalar(
                    select(AnalyticsDailyAggregate).where(
                        AnalyticsDailyAggregate.site_id == site_id,
                        AnalyticsDailyAggregate.path == "/old/",
                    )
                )
                assert aggregate.event_count == 5
                assert aggregate.session_days == 5
                assert "session" not in aggregate.__table__.columns
                assert "payload" not in aggregate.__table__.columns

            async with open_db_session() as db:
                digest = session_digest(sessions[0])
                assert (
                    await revoke_session(
                        db, tenant_id=tenant_id, site_id=site_id, digest=digest, now=_NOW
                    )
                    == 5
                )
                assert await session_was_revoked(
                    db, tenant_id=tenant_id, site_id=site_id, digest=digest, now=_NOW
                )
                rows = await page_view_summary(
                    db, tenant_id=tenant_id, site_id=site_id, days=90, now=_NOW
                )
                by_path = {row["path"]: row for row in rows}
                assert by_path["/recent/"]["page_views"] == 5
                assert by_path["/repeat/"]["consented_session_days"] == 8
                assert by_path["/old/"]["page_views"] == 5  # Already non-linkable.
                assert by_path["/today/"]["page_views"] is None
                assert "/small/" not in by_path
                assert (
                    await db.scalar(
                        select(func.count(AnalyticsEvent.id)).where(
                            AnalyticsEvent.site_id == site_id,
                            AnalyticsEvent.payload["session"].astext == digest,
                        )
                    )
                    == 0
                )
                await rollup_and_purge(db, now=_NOW + timedelta(days=2))
                assert not await session_was_revoked(
                    db,
                    tenant_id=tenant_id,
                    site_id=site_id,
                    digest=digest,
                    now=_NOW + timedelta(days=2),
                )
        finally:
            async with open_db_session() as db:
                await db.execute(
                    delete(AnalyticsEvent).where(AnalyticsEvent.tenant_id == tenant_id)
                )
                await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
                await db.commit()

    asyncio.run(run())


def test_raw_aggregate_and_revocation_marker_enforce_tenant_rls():
    async def run() -> None:
        created_role = False
        tenant_ids = (uuid4(), uuid4())
        site_ids = (uuid4(), uuid4())
        try:
            async with open_db_session() as db:
                assert not await db.scalar(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": _ROLE}
                ), "Dedicated telemetry test role already exists"
                await db.execute(text(f"CREATE ROLE {_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS"))
                await db.execute(text(f"GRANT USAGE ON SCHEMA public TO {_ROLE}"))
                for table in (
                    "analytics_events",
                    "analytics_daily_aggregates",
                    "analytics_revoked_sessions",
                ):
                    await db.execute(text(f"GRANT SELECT, INSERT ON {table} TO {_ROLE}"))
                await db.commit()
                created_role = True
            async with open_db_session() as db:
                for index in range(2):
                    db.add(
                        Tenant(
                            id=tenant_ids[index], name="Telemetry RLS", slug=tenant_ids[index].hex
                        )
                    )
                    db.add(
                        Site(
                            id=site_ids[index],
                            tenant_id=tenant_ids[index],
                            domain=f"{site_ids[index].hex}.test",
                            lead_token=uuid4().hex,
                        )
                    )
                await db.flush()
                for index in range(2):
                    db.add(
                        _event(
                            tenant_ids[index],
                            site_ids[index],
                            day=_NOW,
                            session=f"rls-browser-session-{index}",
                            path="/",
                        )
                    )
                    db.add(
                        AnalyticsDailyAggregate(
                            tenant_id=tenant_ids[index],
                            site_id=site_ids[index],
                            event_date=_NOW.date(),
                            event="page_view",
                            path="/",
                            event_count=5,
                            session_days=5,
                        )
                    )
                    db.add(
                        AnalyticsRevokedSession(
                            tenant_id=tenant_ids[index],
                            site_id=site_ids[index],
                            session_digest="a" * 64,
                            expires_at=_NOW + timedelta(days=1),
                        )
                    )
                await db.flush()
                await db.execute(text(f"SET LOCAL ROLE {_ROLE}"))
                await set_tenant_rls(db, str(tenant_ids[0]))
                for table in (
                    "analytics_events",
                    "analytics_daily_aggregates",
                    "analytics_revoked_sessions",
                ):
                    visible = (
                        (
                            await db.execute(
                                text(f"SELECT tenant_id FROM {table} ORDER BY tenant_id")
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert visible == [tenant_ids[0]], table
                foreign = {"tenant": tenant_ids[1], "site": site_ids[1]}
                forbidden_inserts = (
                    (
                        "INSERT INTO analytics_events "
                        "(id, tenant_id, site_id, event, path) "
                        "VALUES (:id, :tenant, :site, 'page_view', '/')",
                        {**foreign, "id": -int(uuid4().int % 1_000_000_000) - 1},
                    ),
                    (
                        "INSERT INTO analytics_daily_aggregates "
                        "(tenant_id, site_id, event_date, event, path, event_count, session_days) "
                        "VALUES (:tenant, :site, :day, 'page_view', '/foreign/', 1, 1)",
                        {**foreign, "day": _NOW.date()},
                    ),
                    (
                        "INSERT INTO analytics_revoked_sessions "
                        "(tenant_id, site_id, session_digest, expires_at) "
                        "VALUES (:tenant, :site, :digest, :expiry)",
                        {**foreign, "digest": "b" * 64, "expiry": _NOW},
                    ),
                )
                for statement, parameters in forbidden_inserts:
                    with pytest.raises(Exception, match="row-level security"):
                        async with db.begin_nested():
                            await db.execute(text(statement), parameters)
                await db.rollback()
        finally:
            if created_role:
                async with open_db_session() as db:
                    await db.execute(text(f"DROP OWNED BY {_ROLE}"))
                    await db.execute(text(f"DROP ROLE {_ROLE}"))
                    await db.commit()

    asyncio.run(run())
