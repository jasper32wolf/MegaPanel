from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from app.db.rls import set_tenant_rls
from app.models import (
    Project,
    SchedulerAttempt,
    SchedulerJob,
    SchedulerProjectTurn,
    SchedulerWakeup,
    Tenant,
)
from postgres_test_session import isolated_db_session
from sqlalchemy import text

_ROLE = "site_panel_scheduler_rls_test"


@pytest.mark.skipif(
    os.getenv("POSTGRES_RLS_INTEGRATION") != "1",
    reason="requires a migrated PostgreSQL service and role-management privileges",
)
def test_scheduler_tables_enforce_tenant_rls_with_non_owner_role():
    async def run() -> None:
        created_role = False
        try:
            async with isolated_db_session() as db:
                existing = await db.scalar(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": _ROLE}
                )
                assert not existing, "Dedicated scheduler test role already exists"
                await db.execute(text(f"CREATE ROLE {_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS"))
                await db.execute(text(f"GRANT USAGE ON SCHEMA public TO {_ROLE}"))
                for table in (
                    "scheduler_jobs",
                    "scheduler_attempts",
                    "scheduler_wakeups",
                    "scheduler_project_turns",
                ):
                    await db.execute(text(f"GRANT SELECT ON {table} TO {_ROLE}"))
                await db.execute(text(f"GRANT INSERT ON scheduler_jobs TO {_ROLE}"))
                await db.commit()
                created_role = True

            tenant_ids = (uuid4(), uuid4())
            project_ids = (uuid4(), uuid4())
            job_ids = (uuid4(), uuid4())
            attempt_ids = (uuid4(), uuid4())
            wakeup_ids = (uuid4(), uuid4())
            turn_ids = (uuid4(), uuid4())
            async with isolated_db_session() as db:
                for index in range(2):
                    db.add(
                        Tenant(
                            id=tenant_ids[index],
                            name="Scheduler RLS proof",
                            slug=f"scheduler-rls-{tenant_ids[index].hex}",
                        )
                    )
                await db.flush()
                for index in range(2):
                    db.add(
                        Project(
                            id=project_ids[index],
                            tenant_id=tenant_ids[index],
                            name="Scheduler RLS project",
                            slug="scheduler-rls-project",
                        )
                    )
                await db.flush()
                for index in range(2):
                    db.add(
                        SchedulerJob(
                            id=job_ids[index],
                            tenant_id=tenant_ids[index],
                            project_id=project_ids[index],
                            work_type="site_build",
                            source_id=uuid4(),
                            source_hash="a" * 64,
                            source_version=1,
                            state="queued",
                            priority=50,
                        )
                    )
                await db.flush()  # Persist parents before inserting attempts and wakeups.
                for index in range(2):
                    db.add(
                        SchedulerAttempt(
                            id=attempt_ids[index],
                            scheduler_job_id=job_ids[index],
                            tenant_id=tenant_ids[index],
                            project_id=project_ids[index],
                            sequence=1,
                            attempt=0,
                            event_type="queued",
                            details={},
                        )
                    )
                    db.add(
                        SchedulerWakeup(
                            id=wakeup_ids[index],
                            scheduler_job_id=job_ids[index],
                            tenant_id=tenant_ids[index],
                            lease_id=uuid4(),
                            state="pending",
                            attempt_count=0,
                        )
                    )
                    db.add(
                        SchedulerProjectTurn(
                            id=turn_ids[index],
                            tenant_id=tenant_ids[index],
                            project_id=project_ids[index],
                            dispatch_turn=0,
                        )
                    )
                await db.flush()
                await db.execute(text(f"SET LOCAL ROLE {_ROLE}"))
                await set_tenant_rls(db, str(tenant_ids[0]))
                for table, ids in (
                    ("scheduler_jobs", job_ids),
                    ("scheduler_attempts", attempt_ids),
                    ("scheduler_wakeups", wakeup_ids),
                    ("scheduler_project_turns", turn_ids),
                ):
                    visible = (
                        (
                            await db.execute(
                                text(f"SELECT id FROM {table} WHERE id IN (:first, :second)"),
                                {"first": ids[0], "second": ids[1]},
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert visible == [ids[0]], table
                with pytest.raises(Exception, match="row-level security"):
                    async with db.begin_nested():
                        await db.execute(
                            text(
                                "INSERT INTO scheduler_jobs "
                                "(id, tenant_id, project_id, work_type, source_id, source_hash) "
                                "VALUES (:id, :tenant, :project, 'site_build', :source, :hash)"
                            ),
                            {
                                "id": uuid4(),
                                "tenant": tenant_ids[1],
                                "project": project_ids[1],
                                "source": uuid4(),
                                "hash": "b" * 64,
                            },
                        )
                await db.rollback()  # Discard proof records without deleting append-only events.
        finally:
            if created_role:
                async with isolated_db_session() as db:
                    await db.execute(text(f"DROP OWNED BY {_ROLE}"))
                    await db.execute(text(f"DROP ROLE {_ROLE}"))
                    await db.commit()

    asyncio.run(run())
