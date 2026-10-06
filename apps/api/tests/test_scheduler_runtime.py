from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app import worker
from app.api.v1 import competitors
from app.api.v1.projects import _current_candidate_lease
from app.models import SchedulerJob, SchedulerWakeup
from app.schemas.research import CompetitorCrawlCreate
from app.services import competitor_crawl, scheduler


def _job(*, work_type: str = "site_build", state: str = "leased") -> SchedulerJob:
    return SchedulerJob(
        id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        source_id=uuid4(),
        work_type=work_type,
        source_hash="a" * 64,
        source_version=1,
        state=state,
        priority=50,
        attempt_count=1,
        lease_id=uuid4(),
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


@pytest.mark.asyncio
async def test_dispatch_rotates_projects_before_within_project_priority(monkeypatch):
    tenant_id, project_a, project_b = uuid4(), uuid4(), uuid4()
    later_project_job = _job(state="queued")
    later_project_job.tenant_id = tenant_id
    later_project_job.project_id = project_b
    later_project_job.priority = 20
    later_project_job.attempt_count = 0
    turns = [
        SimpleNamespace(tenant_id=tenant_id, project_id=project_a, dispatch_turn=5),
        SimpleNamespace(tenant_id=tenant_id, project_id=project_b, dispatch_turn=2),
    ]

    class Database:
        calls = 0
        added: list[object]

        def __init__(self):
            self.added = []
            self.statements: list[str] = []
            self.commit = AsyncMock()

        async def execute(self, _statement):
            self.statements.append(str(_statement))
            self.calls += 1
            if self.calls == 1:
                return None  # PostgreSQL transaction-scoped dispatch mutex.
            if self.calls == 2:
                return SimpleNamespace(all=lambda: [(tenant_id, project_a), (tenant_id, project_b)])
            if self.calls == 3:
                return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: turns))
            return SimpleNamespace(all=lambda: [])

        async def scalar(self, statement):
            if str(statement).lstrip().lower().startswith("select count("):
                return 0
            return later_project_job

        def add(self, item):
            self.added.append(item)

    db = Database()
    monkeypatch.setattr(scheduler, "append_scheduler_attempt", AsyncMock())

    assert await scheduler.dispatch_due_jobs(db) == [later_project_job.id]
    assert "pg_advisory_xact_lock" in db.statements[0]
    assert later_project_job.state == "leased"
    assert turns[1].dispatch_turn == 6
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_outbox_sends_both_job_and_current_lease_uuids(monkeypatch):
    job = _job()
    wakeup = SchedulerWakeup(
        id=uuid4(),
        scheduler_job_id=job.id,
        tenant_id=job.tenant_id,
        lease_id=job.lease_id,
        state="pending",
        attempt_count=0,
        available_at=datetime.now(UTC),
    )
    calls: list[tuple[str, ...]] = []

    class Pool:
        async def enqueue_job(self, *args):
            calls.append(args)

        async def aclose(self):
            pass

    async def create_pool(_settings):
        return Pool()

    async def scalar(statement):
        return job if "scheduler_jobs" in str(statement) else wakeup

    db = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [wakeup.id])),
        scalar=scalar,
        get=AsyncMock(return_value=wakeup),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(scheduler, "create_pool", create_pool)
    monkeypatch.setattr(scheduler, "append_scheduler_attempt", AsyncMock())

    assert await scheduler.publish_pending_wakeups(db) == 1
    assert calls == [("scheduler_execute_task", str(job.id), str(job.lease_id))]
    assert wakeup.state == "published"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_outbox_discards_expired_lease_without_sending(monkeypatch):
    job = _job()
    wakeup = SchedulerWakeup(
        id=uuid4(),
        scheduler_job_id=job.id,
        tenant_id=job.tenant_id,
        lease_id=uuid4(),
        state="pending",
        attempt_count=0,
        available_at=datetime.now(UTC),
    )

    async def scalar(statement):
        return job if "scheduler_jobs" in str(statement) else wakeup

    db = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [wakeup.id])),
        scalar=scalar,
        get=AsyncMock(return_value=wakeup),
        commit=AsyncMock(),
    )
    pool = AsyncMock()
    monkeypatch.setattr(scheduler, "create_pool", pool)

    assert await scheduler.publish_pending_wakeups(db) == 0
    assert wakeup.state == "expired"
    pool.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_wakeup_cannot_claim_a_new_lease(monkeypatch):
    job = _job()
    db = SimpleNamespace(scalar=AsyncMock(return_value=job), commit=AsyncMock())
    source_status = AsyncMock(return_value=("queued", None))
    monkeypatch.setattr(scheduler, "_source_status", source_status)

    assert await scheduler.claim_execution(db, job.id, uuid4()) is None
    source_status.assert_not_awaited()
    assert job.state == "leased"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_source_is_blocked_before_provider_work(monkeypatch):
    job = _job(work_type="bukvarix_keyword")
    db = SimpleNamespace(scalar=AsyncMock(return_value=job), commit=AsyncMock())
    monkeypatch.setattr(
        scheduler, "_source_status", AsyncMock(return_value=(None, "source_mismatch"))
    )
    append = AsyncMock()
    monkeypatch.setattr(scheduler, "append_scheduler_attempt", append)

    assert await scheduler.claim_execution(db, job.id, job.lease_id) is None
    assert job.state == "failed"
    assert job.failure_code == "source_mismatch"
    append.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_old_executor_cannot_finalize_a_released_attempt(monkeypatch):
    job = _job(state="running")
    append = AsyncMock()
    monkeypatch.setattr(scheduler, "append_scheduler_attempt", append)
    db = SimpleNamespace(scalar=AsyncMock(return_value=job), commit=AsyncMock())

    result = await scheduler.complete_execution(
        db, job_id=job.id, lease_id=uuid4(), result={"status": "completed"}
    )

    assert result["status"] == "stale_lease"
    assert job.state == "running"
    assert job.lease_id is not None
    append.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_candidate_cannot_write_frozen_build_result():
    job = _job(work_type="site_build", state="running")
    build = SimpleNamespace(id=job.source_id, status="running")
    db = SimpleNamespace(scalar=AsyncMock(return_value=job), refresh=AsyncMock())

    assert await _current_candidate_lease(db, build=build, lease_id=uuid4()) is False
    db.refresh.assert_not_awaited()
    assert await _current_candidate_lease(db, build=build, lease_id=job.lease_id) is True
    db.refresh.assert_awaited_once_with(build)


@pytest.mark.asyncio
async def test_current_lease_finalizes_after_cancellation(monkeypatch):
    job = _job(work_type="competitor_crawl", state="cancel_requested")
    append = AsyncMock()
    monkeypatch.setattr(scheduler, "append_scheduler_attempt", append)
    db = SimpleNamespace(scalar=AsyncMock(return_value=job), commit=AsyncMock())

    await scheduler.complete_execution(
        db, job_id=job.id, lease_id=job.lease_id, result={"status": "done"}
    )

    assert job.state == "cancelled"
    assert job.lease_id is None
    append.assert_awaited_once()
    assert append.call_args.kwargs["event_type"] == "cancelled"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_expired_job_releases_global_capacity(monkeypatch):
    job = _job(work_type="competitor_crawl", state="cancel_requested")
    job.lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    source = SimpleNamespace(status="cancelled", cancelled_at=datetime.now(UTC))
    rows = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [job]))
    db = SimpleNamespace(
        execute=AsyncMock(return_value=rows),
        get=AsyncMock(return_value=source),
        commit=AsyncMock(),
    )
    expire = AsyncMock()
    append = AsyncMock()
    monkeypatch.setattr(scheduler, "_expire_pending_wakeups", expire)
    monkeypatch.setattr(scheduler, "append_scheduler_attempt", append)

    assert await scheduler.recover_expired_scheduler_leases(db) == 1
    assert job.state == "cancelled"
    assert job.lease_id is None
    assert job.lease_expires_at is None
    expire.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_timed_out_started_bukvarix_run_fails_instead_of_replaying(monkeypatch):
    job = _job(work_type="bukvarix_keyword", state="running")
    job.lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    source = SimpleNamespace(status="running", failure_code=None, completed_at=None)
    rows = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [job]))
    db = SimpleNamespace(
        execute=AsyncMock(return_value=rows),
        get=AsyncMock(return_value=source),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(scheduler, "_expire_pending_wakeups", AsyncMock())
    monkeypatch.setattr(scheduler, "append_scheduler_attempt", AsyncMock())

    assert await scheduler.recover_expired_scheduler_leases(db) == 1
    assert job.state == "failed"
    assert source.status == "failed"
    assert source.failure_code == "worker_timeout"
    assert source.completed_at is not None


@pytest.mark.asyncio
async def test_expired_unstarted_candidate_is_requeued_after_redis_outage(monkeypatch):
    job = _job(work_type="site_build", state="leased")
    job.lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    source = SimpleNamespace(status="queued")
    rows = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [job]))
    db = SimpleNamespace(
        execute=AsyncMock(return_value=rows),
        get=AsyncMock(return_value=source),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(scheduler, "_expire_pending_wakeups", AsyncMock())
    monkeypatch.setattr(scheduler, "append_scheduler_attempt", AsyncMock())

    assert await scheduler.recover_expired_scheduler_leases(db) == 1
    assert job.state == "queued"
    assert job.lease_id is None
    assert source.status == "queued"


@pytest.mark.asyncio
async def test_orphaned_legacy_crawl_becomes_visible_failure(monkeypatch):
    crawl = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        status="running",
        error_code=None,
        error_message=None,
        finished_at=None,
    )
    rows = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [crawl]))
    db = SimpleNamespace(execute=AsyncMock(return_value=rows), commit=AsyncMock())
    audit = AsyncMock()
    monkeypatch.setattr(competitor_crawl, "append_audit", audit)

    assert await competitor_crawl.recover_legacy_competitor_crawls(db) == 1
    assert crawl.status == "failed"
    assert crawl.error_code == "worker_timeout"
    assert crawl.finished_at is not None
    audit.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_generic_worker_passes_only_fenced_uuid_to_crawl_runner(monkeypatch):
    job = _job(work_type="competitor_crawl", state="running")
    session = object()

    @asynccontextmanager
    async def db_session():
        yield session

    claim = AsyncMock(return_value=job)
    crawl = AsyncMock(return_value={"status": "done", "crawl_id": str(job.source_id)})
    finish = AsyncMock(return_value={"status": "done"})
    monkeypatch.setattr(worker, "open_db_session", db_session)
    monkeypatch.setattr(worker, "claim_execution", claim)
    monkeypatch.setattr(worker, "run_competitor_crawl", crawl)
    monkeypatch.setattr(worker, "complete_execution", finish)

    assert await worker.scheduler_execute_task({}, str(job.id), str(job.lease_id)) == {
        "status": "done"
    }
    claim.assert_awaited_once_with(session, job.id, job.lease_id)
    crawl.assert_awaited_once_with(session, job.source_id)
    assert finish.call_args.kwargs["lease_id"] == job.lease_id
    assert (await worker.scheduler_execute_task({}, str(job.id)))["status"] == "stale_lease"


@pytest.mark.asyncio
async def test_crawl_creation_commits_scheduler_job_before_return(monkeypatch):
    project = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    auth = SimpleNamespace(user=SimpleNamespace(id=uuid4()), tenant_id=project.tenant_id)
    create_job = AsyncMock()
    monkeypatch.setattr(competitors, "_project_or_404", AsyncMock(return_value=project))
    monkeypatch.setattr(competitors, "create_competitor_crawl_job", create_job)
    monkeypatch.setattr(competitors, "append_audit", AsyncMock())

    class Database:
        def __init__(self):
            self.crawl = None
            self.committed = False

        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: None)

        def add(self, crawl):
            self.crawl = crawl

        async def flush(self):
            self.crawl.id = uuid4()

        async def commit(self):
            create_job.assert_awaited_once()
            self.committed = True

        async def refresh(self, _crawl):
            pass

    db = Database()
    result = await competitors.create_domain_crawl(
        project.id,
        CompetitorCrawlCreate(
            root_url="https://example.test",
            terms_acknowledged=True,
            max_pages=3,
            max_depth=1,
        ),
        auth,
        db,
    )

    assert db.committed
    assert result is db.crawl
    assert result.root_url == "https://example.test/"
    assert result.project_id == project.id
    assert create_job.call_args.kwargs["crawl"] is result
    assert create_job.call_args.kwargs["requested_by"] == auth.user.id


@pytest.mark.asyncio
async def test_queued_bukvarix_cancellation_updates_source(monkeypatch):
    job = _job(work_type="bukvarix_keyword", state="queued")
    source = SimpleNamespace(status="queued", completed_at=None)
    db = SimpleNamespace(get=AsyncMock(return_value=source))
    monkeypatch.setattr(scheduler, "_expire_pending_wakeups", AsyncMock())
    monkeypatch.setattr(scheduler, "append_scheduler_attempt", AsyncMock())

    await scheduler.cancel_job(db, job=job)

    assert source.status == "cancelled"
    assert source.completed_at is not None
    assert job.state == "cancelled"
    assert job.lease_id is None
