from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from app.api.v1.projects import _serialize_draft, _serialize_plan
from app.core.config import get_settings
from app.db.rls import set_tenant_rls
from app.db.session import engine as app_engine
from app.db.session import open_db_session
from app.models import PageDraft, PagePlan, Project, Site, Tenant
from sqlalchemy import delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

RUN_POSTGRES_RLS_INTEGRATION = os.getenv("POSTGRES_RLS_INTEGRATION") == "1"
RLS_ROLE = "site_panel_rls_test"
RLS_ROLE_PASSWORD = "site-panel-rls-test-password"


async def drop_rls_role(db) -> None:
    role_exists = await db.scalar(
        text("SELECT 1 FROM pg_roles WHERE rolname = :role_name"),
        {"role_name": RLS_ROLE},
    )
    if role_exists:
        await db.execute(text(f"DROP OWNED BY {RLS_ROLE}"))
        await db.execute(text(f"DROP ROLE {RLS_ROLE}"))


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
        await app_engine.dispose()
        first_tenant_id = uuid4()
        second_tenant_id = uuid4()
        first_site_id = uuid4()
        second_site_id = uuid4()

        rls_engine = None
        try:
            async with open_db_session() as db:
                await drop_rls_role(db)
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
            try:
                async with open_db_session() as db:
                    await db.execute(
                        delete(Tenant).where(Tenant.id.in_((first_tenant_id, second_tenant_id)))
                    )
                    await drop_rls_role(db)
                    await db.commit()
            finally:
                await app_engine.dispose()

    asyncio.run(run())


@pytest.mark.skipif(
    not RUN_POSTGRES_RLS_INTEGRATION,
    reason="requires a PostgreSQL service container with migrations applied",
)
def test_workflow_update_timestamps_can_be_serialized_after_commit() -> None:
    async def run() -> None:
        tenant_id = uuid4()
        project_id = uuid4()
        plan_id = uuid4()
        draft_id = uuid4()
        try:
            async with open_db_session() as db:
                db.add(
                    Tenant(
                        id=tenant_id,
                        name="Workflow timestamps",
                        slug=f"workflow-{tenant_id.hex}",
                    )
                )
                await db.flush()
                db.add(
                    Project(id=project_id, tenant_id=tenant_id, name="Workflow", slug="workflow")
                )
                await db.flush()
                db.add(
                    PagePlan(
                        id=plan_id,
                        project_id=project_id,
                        tenant_id=tenant_id,
                        slug="/",
                        objective="Workflow timestamps",
                        kit_key="service-local-v1",
                    )
                )
                await db.flush()
                db.add(
                    PageDraft(
                        id=draft_id,
                        page_plan_id=plan_id,
                        project_id=project_id,
                        tenant_id=tenant_id,
                        revision=1,
                        state="draft",
                    )
                )
                await db.commit()
                plan = await db.get(PagePlan, plan_id)
                draft = await db.get(PageDraft, draft_id)
                assert plan is not None and draft is not None
                plan.state = "review"
                draft.state = "qa_done"
                await db.commit()
                assert _serialize_plan(plan)["updated_at"]
                assert _serialize_draft(draft)["updated_at"]
        finally:
            async with open_db_session() as db:
                await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
                await db.commit()
            await app_engine.dispose()

    asyncio.run(run())
