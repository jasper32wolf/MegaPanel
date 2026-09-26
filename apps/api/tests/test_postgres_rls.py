from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from app.core.config import get_settings
from app.db.rls import set_tenant_rls
from app.db.session import open_db_session
from app.models import Site, Tenant
from sqlalchemy import delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

RUN_POSTGRES_RLS_INTEGRATION = os.getenv("POSTGRES_RLS_INTEGRATION") == "1"
RLS_ROLE = "site_panel_rls_test"
RLS_ROLE_PASSWORD = "site-panel-rls-test-password"


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    async def execute(self, statement, parameters=None) -> None:
        self.calls.append((str(statement), parameters))


def test_tenant_rls_explicitly_disables_a_prior_bypass() -> None:
    async def run() -> None:
        session = FakeSession()

        await set_tenant_rls(session, "tenant-id", bypass=False)

        assert session.calls == [
            ("SELECT set_config('app.bypass_rls', :value, false)", {"value": "off"}),
            ("SELECT set_config('app.tenant_id', :tid, true)", {"tid": "tenant-id"}),
        ]

    asyncio.run(run())


@pytest.mark.skipif(
    not RUN_POSTGRES_RLS_INTEGRATION,
    reason="requires a PostgreSQL service container with migrations applied",
)
def test_rls_scopes_sites_after_maintenance_session() -> None:
    async def run() -> None:
        first_tenant_id = uuid4()
        second_tenant_id = uuid4()
        first_site_id = uuid4()
        second_site_id = uuid4()

        rls_engine = None
        try:
            async with open_db_session() as db:
                await db.execute(text(f"DROP ROLE IF EXISTS {RLS_ROLE}"))
                await db.execute(
                    text(
                        f"CREATE ROLE {RLS_ROLE} LOGIN PASSWORD "
                        f"'{RLS_ROLE_PASSWORD}' NOSUPERUSER NOBYPASSRLS"
                    )
                )
                await db.execute(text(f"GRANT USAGE ON SCHEMA public TO {RLS_ROLE}"))
                await db.execute(text(f"GRANT SELECT ON sites TO {RLS_ROLE}"))
                db.add_all(
                    [
                        Tenant(
                            id=first_tenant_id,
                            name="RLS first tenant",
                            slug=f"rls-first-{first_tenant_id.hex}",
                            branding={},
                            quotas={},
                        ),
                        Tenant(
                            id=second_tenant_id,
                            name="RLS second tenant",
                            slug=f"rls-second-{second_tenant_id.hex}",
                            branding={},
                            quotas={},
                        ),
                        Site(
                            id=first_site_id,
                            tenant_id=first_tenant_id,
                            domain=f"rls-first-{first_site_id.hex}.example.test",
                            lead_token=f"lead-token-{first_site_id.hex}",
                            manifest={},
                        ),
                        Site(
                            id=second_site_id,
                            tenant_id=second_tenant_id,
                            domain=f"rls-second-{second_site_id.hex}.example.test",
                            lead_token=f"lead-token-{second_site_id.hex}",
                            manifest={},
                        ),
                    ]
                )
                await db.commit()

            rls_url = make_url(get_settings().database_url).set(
                username=RLS_ROLE,
                password=RLS_ROLE_PASSWORD,
            )
            rls_engine = create_async_engine(rls_url)
            rls_sessions = async_sessionmaker(rls_engine, expire_on_commit=False)
            async with rls_sessions() as db:
                site_ids = (first_site_id, second_site_id)
                await set_tenant_rls(db, None, bypass=True)
                assert set(
                    (await db.scalars(select(Site.id).where(Site.id.in_(site_ids)))).all()
                ) == set(site_ids)

                await set_tenant_rls(db, str(first_tenant_id))
                visible_site_ids = set(
                    (await db.scalars(select(Site.id).where(Site.id.in_(site_ids)))).all()
                )

            assert visible_site_ids == {first_site_id}
        finally:
            if rls_engine is not None:
                await rls_engine.dispose()
            async with open_db_session() as db:
                await db.execute(
                    delete(Tenant).where(Tenant.id.in_((first_tenant_id, second_tenant_id)))
                )
                await db.execute(text(f"DROP ROLE IF EXISTS {RLS_ROLE}"))
                await db.commit()

    asyncio.run(run())
