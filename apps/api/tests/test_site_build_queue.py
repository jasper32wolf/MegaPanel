from __future__ import annotations

import asyncio
import importlib.util
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.api.v1 import projects
from app.api.v1.projects import retry_project_build
from app.schemas.workflow import BuildRollbackRequest, CandidateBuildRequest
from app.services import site_build_queue
from app.worker import WorkerSettings, candidate_build_task
from pydantic import ValidationError


def _migration():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0047_durable_site_build_queue.py"
    spec = importlib.util.spec_from_file_location("site_build_queue_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_candidate_build_schedule_requires_a_timezone_and_bounded_priority():
    future = datetime.now(UTC) + timedelta(minutes=15)
    request = CandidateBuildRequest(queue_priority=80, not_before=future)

    assert request.queue_priority == 80
    assert request.not_before == future
    with pytest.raises(ValidationError):
        CandidateBuildRequest(not_before=future.replace(tzinfo=None))
    with pytest.raises(ValidationError):
        CandidateBuildRequest(queue_priority=101)


def test_candidate_queue_uses_due_priority_and_a_single_vps_slot():
    source = Path(site_build_queue.__file__).read_text(encoding="utf-8")

    assert "SiteBuild.queue_priority.desc(), SiteBuild.created_at.asc()" in source
    assert "SiteBuild.not_before.is_(None)" in source
    assert "max_concurrency: int = 1" in source


class EventDatabase:
    def __init__(self):
        self.added: list[object] = []
        self.values = [0, 4]

    async def execute(self, _statement: object) -> object:
        return SimpleNamespace(scalar_one=lambda: self.values.pop(0))

    def add(self, value: object) -> None:
        self.added.append(value)


def test_candidate_queue_enqueues_only_durable_id_and_closes_pool(monkeypatch):
    build_id = uuid4()
    calls: list[tuple[str, str]] = []

    class Pool:
        closed = False

        async def enqueue_job(self, name: str, value: str):
            calls.append((name, value))

        async def aclose(self):
            self.closed = True

    pool = Pool()

    async def create_pool_stub(_settings):
        return pool

    monkeypatch.setattr(site_build_queue, "create_pool", create_pool_stub)

    asyncio.run(site_build_queue.enqueue_site_build(build_id))

    assert calls == [("candidate_build_task", str(build_id))]
    assert pool.closed is True


def test_candidate_queue_closes_pool_when_enqueue_fails(monkeypatch):
    class Pool:
        closed = False

        async def enqueue_job(self, _name: str, _value: str):
            raise RuntimeError("redis unavailable")

        async def aclose(self):
            self.closed = True

    pool = Pool()

    async def create_pool_stub(_settings):
        return pool

    monkeypatch.setattr(site_build_queue, "create_pool", create_pool_stub)

    with pytest.raises(RuntimeError, match="redis unavailable"):
        asyncio.run(site_build_queue.enqueue_site_build(uuid4()))
    assert pool.closed is True


def test_safe_build_event_is_bounded_and_append_only_metadata():
    build = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        site_id=uuid4(),
        attempt_count=2,
    )
    db = EventDatabase()

    event = asyncio.run(
        site_build_queue.append_site_build_event(
            db,
            build=build,
            event_type="failed",
            safe_code="build_execution_failed",
            details={"stage": "render"},
        )
    )

    assert event.sequence == 5
    assert event.attempt == 2
    assert event.details == {"stage": "render"}
    assert db.added == [event]
    with pytest.raises(ValueError, match="Unsupported"):
        asyncio.run(
            site_build_queue.append_site_build_event(
                EventDatabase(), build=build, event_type="raw_trace"
            )
        )
    with pytest.raises(ValueError, match="safe code"):
        asyncio.run(
            site_build_queue.append_site_build_event(
                EventDatabase(), build=build, event_type="failed", safe_code="Bad code"
            )
        )


def test_candidate_worker_is_registered_and_dispatches_only_build_id(monkeypatch):
    build_id = uuid4()
    received: list[object] = []

    @asynccontextmanager
    async def session_stub():
        yield object()

    async def runner_stub(session, received_build_id):
        received.extend([session, received_build_id])
        return {"status": "ready", "build_id": str(received_build_id)}

    monkeypatch.setattr("app.worker.open_db_session", session_stub)
    monkeypatch.setattr("app.worker.run_queued_candidate_build", runner_stub)

    result = asyncio.run(candidate_build_task({}, str(build_id)))

    assert candidate_build_task in WorkerSettings.functions
    assert result == {"status": "ready", "build_id": str(build_id)}
    assert received[1] == build_id


def test_retry_requeues_only_failed_build_with_supported_frozen_snapshot(monkeypatch):
    tenant_id, project_id, site_id, build_id = (uuid4() for _ in range(4))
    project = SimpleNamespace(id=project_id, tenant_id=tenant_id, site_id=site_id)
    site = SimpleNamespace(id=site_id)
    build = SimpleNamespace(
        id=build_id,
        tenant_id=tenant_id,
        project_id=project_id,
        site_id=site_id,
        status="failed",
        input_snapshot={"version": 1},
        snapshot_version=1,
        failure_code="build_execution_failed",
        started_at=None,
        completed_at=None,
        lease_expires_at=None,
        last_enqueued_at=None,
    )

    class Database:
        committed = False

        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: build)

        async def commit(self):
            self.committed = True

    db = Database()
    monkeypatch.setattr(projects, "_project_or_404", AsyncMock(return_value=project))
    monkeypatch.setattr(projects, "_project_site_or_409", AsyncMock(return_value=site))
    monkeypatch.setattr(projects, "append_site_build_event", AsyncMock())
    monkeypatch.setattr(projects, "append_audit", AsyncMock())

    response = asyncio.run(
        retry_project_build(
            project_id,
            build_id,
            SimpleNamespace(user=SimpleNamespace(id=uuid4())),
            db,
        )
    )

    assert response == {"id": str(build_id), "status": "queued", "retryable": False}
    assert build.status == "queued"
    assert build.input_snapshot == {"version": 1}
    assert build.not_before is not None
    assert db.committed is True


def test_worker_builds_frozen_candidate_without_activation_or_publication(monkeypatch):
    tenant_id, project_id, site_id, build_id = (uuid4() for _ in range(4))
    manifest = {
        "site_id": str(site_id),
        "tenant_id": str(tenant_id),
        "domain": "example.test",
        "pages": [
            {
                "slug": "/",
                "title_template": "Главная",
                "h1_template": "Главная",
                "service": "Услуга",
            }
        ],
    }
    build = SimpleNamespace(
        id=build_id,
        tenant_id=tenant_id,
        project_id=project_id,
        site_id=site_id,
        status="queued",
        input_snapshot={
            "manifest": manifest,
            "context": {"manifest_context": {}, "phone": ""},
            "index_states": {"/": "noindex"},
            "source_hashes": {"/": "a" * 64},
            "promoted_at": {"/": None},
            "index_promotions": {},
            "page_plan_ids": [],
        },
        snapshot_version=1,
        attempt_count=0,
        requested_by=uuid4(),
        build_hash=None,
        pages_built=0,
        duration_ms=0,
        log="",
        manifest_snapshot=None,
        page_metadata_snapshot=None,
        page_plan_ids=None,
        lease_expires_at=None,
        completed_at=None,
        failure_code=None,
    )
    project = SimpleNamespace(id=project_id, tenant_id=tenant_id)
    site = SimpleNamespace(
        id=site_id,
        tenant_id=tenant_id,
        project_id=project_id,
        lead_token="token",
    )

    class Database:
        commits = 0

        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: build)

        async def get(self, model, _key):
            return project if model.__name__ == "Project" else site

        async def commit(self):
            self.commits += 1

    class Builder:
        calls: list[dict] = []

        def __init__(self, _root):
            pass

        def build(self, _manifest, _context, **kwargs):
            self.calls.append(kwargs)
            return {
                "build_hash": "b" * 64,
                "pages": [
                    {
                        "slug": "/",
                        "path": "/",
                        "index_state": "noindex",
                        "thin": False,
                        "content_chars": 100,
                        "hash": "c" * 64,
                    }
                ],
                "indexed_count": 0,
            }

    db = Database()
    monkeypatch.setattr(projects, "SiteBuilder", Builder)
    monkeypatch.setattr(projects, "_build_assets_for_manifest", AsyncMock(return_value=[]))
    monkeypatch.setattr(projects, "append_site_build_event", AsyncMock())
    monkeypatch.setattr(projects, "store_release_gate", AsyncMock())
    monkeypatch.setattr(projects, "append_audit", AsyncMock())
    monkeypatch.setattr(projects, "_record_release_event", lambda *_args, **_kwargs: None)

    result = asyncio.run(projects.run_queued_candidate_build(db, build_id))

    assert result == {"status": "ready", "build_id": str(build_id), "build_hash": "b" * 64}
    assert build.status == "ready"
    assert build.build_hash == "b" * 64
    assert Builder.calls == [{"index_states": {"/": "noindex"}, "assets": [], "activate": False}]
    assert not hasattr(site, "build_hash")
    assert db.commits == 2


def test_rollback_requires_exact_full_hash_phrase():
    build_hash = "a" * 64
    request = BuildRollbackRequest(
        build_hash=build_hash,
        confirmation_text=f"ROLLBACK {build_hash}",
    )
    assert request.confirmation_text == f"ROLLBACK {build_hash}"
    with pytest.raises(ValidationError):
        BuildRollbackRequest(build_hash=build_hash, confirmation_text="ROLLBACK a")


def test_build_queue_migration_has_rls_append_only_events_and_legacy_backfill(monkeypatch):
    migration = _migration()
    calls: list[str] = []
    monkeypatch.setattr(
        migration.op, "create_table", lambda name, *args, **kwargs: calls.append(f"table:{name}")
    )
    monkeypatch.setattr(migration.op, "add_column", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration.op, "alter_column", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration.op, "create_check_constraint", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration.op, "create_index", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration.op, "execute", lambda sql: calls.append(str(sql)))

    migration.upgrade()

    assert migration.down_revision == "0046_project_semantic_source_runs"
    assert calls.count("table:site_build_events") == 1
    sql = "\n".join(calls)
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "append-only" in sql
    assert "legacy_incomplete" in sql
    function_sql = next(item for item in calls if "CREATE FUNCTION" in item)
    trigger_sql = next(item for item in calls if "CREATE TRIGGER" in item)
    assert "CREATE TRIGGER" not in function_sql
    assert "CREATE FUNCTION" not in trigger_sql
