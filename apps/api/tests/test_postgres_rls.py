from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.api.v1.projects import _serialize_draft, _serialize_plan
from app.core.config import get_settings
from app.db.rls import set_tenant_rls
from app.db.session import engine as app_engine
from app.db.session import open_db_session
from app.models import PageDraft, PagePlan, Project, Site, Tenant
from site_panel_security import FieldEncryptor
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


@pytest.mark.skipif(
    not RUN_POSTGRES_RLS_INTEGRATION,
    reason="requires a local PostgreSQL service with database creation privileges",
)
def test_legacy_data_upgrade_from_0019_to_current_head() -> None:
    api_dir = Path(__file__).resolve().parents[1]
    head_revision = ScriptDirectory.from_config(
        Config(str(api_dir / "alembic.ini"))
    ).get_current_head()
    assert head_revision is not None

    async def run() -> None:
        source_url = make_url(get_settings().database_url)
        assert source_url.host in {"127.0.0.1", "localhost"} and source_url.database == "site_panel"
        database_name = f"site_panel_upgrade_{uuid4().hex}"
        upgrade_url = source_url.set(database=database_name)
        admin_engine = create_async_engine(source_url, isolation_level="AUTOCOMMIT")
        created = False

        def upgrade(revision: str) -> None:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "alembic",
                    "-c",
                    str(api_dir / "alembic.ini"),
                    "upgrade",
                    revision,
                ],
                cwd=api_dir,
                env={
                    **os.environ,
                    "DATABASE_URL": upgrade_url.render_as_string(hide_password=False),
                },
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            assert result.returncode == 0, result.stderr

        try:
            async with admin_engine.connect() as connection:
                await connection.execute(text(f"CREATE DATABASE {database_name}"))
            created = True
            await asyncio.to_thread(upgrade, "0019_lead_idempotency")

            isolated_engine = create_async_engine(upgrade_url)
            tenant_id, safe_id, unsafe_id, lead_id = uuid4(), uuid4(), uuid4(), uuid4()
            safe_secret = "migration-safe-legacy-secret"
            unsafe_secret = "migration-unsafe-legacy-secret"
            legacy_message = "Migration fixture message"
            try:
                async with isolated_engine.begin() as connection:
                    revision = await connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                    assert revision == "0019_lead_idempotency"
                    version_column_length = await connection.scalar(
                        text(
                            "SELECT character_maximum_length FROM information_schema.columns "
                            "WHERE table_name = 'alembic_version' AND column_name = 'version_num'"
                        )
                    )
                    assert version_column_length == 32
                    assert await connection.scalar(
                        text(
                            "SELECT EXISTS ("
                            "SELECT FROM information_schema.columns "
                            "WHERE table_schema = 'public' AND table_name = 'leads' "
                            "AND column_name = 'message'"
                            ")"
                        )
                    )
                    assert not await connection.scalar(
                        text(
                            "SELECT EXISTS ("
                            "SELECT FROM information_schema.columns "
                            "WHERE table_schema = 'public' AND table_name = 'leads' "
                            "AND column_name = 'message_enc'"
                            ")"
                        )
                    )
                    await connection.execute(
                        text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
                        {"id": tenant_id, "name": "Upgrade fixture", "slug": database_name},
                    )
                    for site_id, domain, manifest in (
                        (
                            safe_id,
                            "safe.example.test",
                            {
                                "contacts": {
                                    "webhook_url": "https://hooks.example.test/lead",
                                    "webhook_secret": safe_secret,
                                }
                            },
                        ),
                        (
                            unsafe_id,
                            "unsafe.example.test",
                            {
                                "contacts": {
                                    "webhook_url": "http://127.0.0.1/lead",
                                    "webhook_secret": unsafe_secret,
                                }
                            },
                        ),
                    ):
                        await connection.execute(
                            text(
                                "INSERT INTO sites (id, tenant_id, domain, lead_token, manifest) "
                                "VALUES (:id, :tenant_id, :domain, :lead_token, "
                                "CAST(:manifest AS jsonb))"
                            ),
                            {
                                "id": site_id,
                                "tenant_id": tenant_id,
                                "domain": domain,
                                "lead_token": uuid4().hex,
                                "manifest": json.dumps(manifest),
                            },
                        )
                    await connection.execute(
                        text(
                            "INSERT INTO leads ("
                            "id, tenant_id, site_id, message, idempotency_key"
                            ") VALUES ("
                            ":id, :tenant_id, :site_id, :message, :idempotency_key"
                            ")"
                        ),
                        {
                            "id": lead_id,
                            "tenant_id": tenant_id,
                            "site_id": safe_id,
                            "message": legacy_message,
                            "idempotency_key": "legacy-migration-lead",
                        },
                    )
                    assert (
                        await connection.scalar(
                            text("SELECT message FROM leads WHERE id = :id"), {"id": lead_id}
                        )
                        == legacy_message
                    )
            finally:
                await isolated_engine.dispose()

            await asyncio.to_thread(upgrade, "head")
            isolated_engine = create_async_engine(upgrade_url)
            try:
                async with isolated_engine.connect() as connection:
                    revision = await connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                    assert revision == head_revision
                    version_column_length = await connection.scalar(
                        text(
                            "SELECT character_maximum_length FROM information_schema.columns "
                            "WHERE table_name = 'alembic_version' AND column_name = 'version_num'"
                        )
                    )
                    assert version_column_length >= len(head_revision)
                    rows = await connection.execute(text("SELECT id, manifest FROM sites"))
                    manifests = dict(rows.all())
                    assert set(manifests) == {safe_id, unsafe_id}
                    safe_contacts = manifests[safe_id]["contacts"]
                    assert "webhook_secret" not in safe_contacts
                    encryptor = FieldEncryptor.from_base64(get_settings().field_encryption_key)
                    assert encryptor.decrypt(safe_contacts["webhook_secret_enc"]) == safe_secret
                    assert manifests[unsafe_id]["contacts"] == {
                        "webhook_url": "http://127.0.0.1/lead",
                        "webhook_secret": unsafe_secret,
                    }
                    assert not await connection.scalar(
                        text(
                            "SELECT EXISTS ("
                            "SELECT FROM information_schema.columns "
                            "WHERE table_schema = 'public' AND table_name = 'leads' "
                            "AND column_name = 'message'"
                            ")"
                        )
                    )
                    message_enc = await connection.scalar(
                        text("SELECT message_enc FROM leads WHERE id = :id"), {"id": lead_id}
                    )
                    assert message_enc is not None and message_enc != legacy_message
                    assert encryptor.decrypt(message_enc) == legacy_message
                    assert await connection.scalar(
                        text(
                            "SELECT EXISTS ("
                            "SELECT FROM information_schema.tables "
                            "WHERE table_schema = 'public' "
                            "AND table_name = 'github_workflow_run_deliveries'"
                            ")"
                        )
                    )
                    rls_flags = await connection.execute(
                        text(
                            "SELECT relrowsecurity, relforcerowsecurity "
                            "FROM pg_class WHERE oid = "
                            "'github_workflow_run_deliveries'::regclass"
                        )
                    )
                    assert rls_flags.one() == (True, True)
                    policy = await connection.scalar(
                        text(
                            "SELECT qual FROM pg_policies "
                            "WHERE schemaname = 'public' "
                            "AND tablename = 'github_workflow_run_deliveries' "
                            "AND policyname = 'tenant_isolation_github_workflow_run_deliveries'"
                        )
                    )
                    assert policy is not None and "app.tenant_id" in policy
            finally:
                await isolated_engine.dispose()
        finally:
            try:
                if created:
                    async with admin_engine.connect() as connection:
                        await connection.execute(text(f"DROP DATABASE {database_name}"))
            finally:
                await admin_engine.dispose()

    asyncio.run(run())
