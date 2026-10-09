from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from app.services.scheduler import competitor_crawl_source_hash


def _migration():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0055_generic_durable_scheduler.py"
    spec = importlib.util.spec_from_file_location("generic_scheduler_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scheduler_migration_is_tenant_scoped_and_follows_author_head():
    migration = _migration()
    source = Path(migration.__file__).read_text(encoding="utf-8")

    assert migration.down_revision == "0054_page_draft_author_binding"
    for table in (
        "scheduler_jobs",
        "scheduler_attempts",
        "scheduler_wakeups",
        "scheduler_project_turns",
    ):
        assert table in source
        assert f'_tenant_policy("{table}")' in source
    assert "uq_scheduler_job_source" in source
    assert "uq_scheduler_active_project" in source
    assert "uq_scheduler_active_site" in source
    assert "trg_forbid_scheduler_attempt_mutation" in source


def test_bukvarix_scheduler_allowlist_follows_generic_scheduler():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0056_schedule_bukvarix_previews.py"
    source = path.read_text(encoding="utf-8")

    assert 'down_revision: str | Sequence[str] | None = "0055_generic_durable_scheduler"' in source
    assert "bukvarix_keyword" in source
    assert "scheduler_jobs" in source


def test_competitor_crawl_source_hash_binds_https_scope_and_bounds():
    crawl = SimpleNamespace(
        root_url="https://example.test/",
        origin="https://example.test",
        configuration={"max_pages": 10, "max_depth": 2},
    )
    original = competitor_crawl_source_hash(crawl)
    assert len(original) == 64
    crawl.configuration = {"max_depth": 2, "max_pages": 10}
    assert competitor_crawl_source_hash(crawl) == original
    crawl.configuration = {"max_pages": 11, "max_depth": 2}
    assert competitor_crawl_source_hash(crawl) != original


def test_competitor_crawls_are_scheduler_owned_without_auto_approving_evidence():
    api_dir = Path(__file__).parents[1]
    migration = (api_dir / "alembic" / "versions" / "0057_schedule_competitor_crawls.py").read_text(
        encoding="utf-8"
    )
    api_source = (api_dir / "app" / "api" / "v1" / "competitors.py").read_text(encoding="utf-8")
    worker_source = (api_dir / "app" / "worker.py").read_text(encoding="utf-8")

    assert 'down_revision: str | None = "0056_schedule_bukvarix_previews"' in migration
    assert "'competitor_crawl'" in migration
    assert "create_competitor_crawl_job(db, crawl=crawl" in api_source
    assert "enqueue_competitor_crawl(crawl.id)" not in api_source
    assert "run_competitor_crawl(session, job.source_id)" in worker_source
    assert "approve_domain_crawl_evidence" not in worker_source


def test_scheduler_fencing_migration_preserves_legacy_queued_work():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0058_scheduler_lease_fencing.py"
    source = path.read_text(encoding="utf-8")

    assert 'down_revision: str | None = "0057_schedule_competitor_crawls"' in source
    assert "forbid_scheduler_source_mutation" in source
    assert "'expired'" in source
    assert "ON CONFLICT (work_type, source_id) DO NOTHING" in source
    assert "migration_backfill" in source
    assert "set_config('app.bypass_rls', 'on', true)" in source
    assert "set_config('app.bypass_rls', 'off', true)" in source


def test_scheduler_is_uuid_outbox_based_and_cannot_publish():
    api_dir = Path(__file__).parents[1]
    scheduler = (api_dir / "app" / "services" / "scheduler.py").read_text(encoding="utf-8")
    worker = (api_dir / "app" / "worker.py").read_text(encoding="utf-8")
    routes = (api_dir / "app" / "api" / "v1" / "scheduled_work.py").read_text(encoding="utf-8")
    index_schedule = (api_dir / "app" / "services" / "index_schedule.py").read_text(
        encoding="utf-8"
    )
    semantic_sources = (api_dir / "app" / "api" / "v1" / "semantic_sources.py").read_text(
        encoding="utf-8"
    )
    bukvarix = (api_dir / "app" / "services" / "bukvarix_queue.py").read_text(encoding="utf-8")
    build_queue = (api_dir / "app" / "services" / "site_build_queue.py").read_text(encoding="utf-8")
    crawl = (api_dir / "app" / "services" / "competitor_crawl.py").read_text(encoding="utf-8")

    assert '"scheduler_execute_task", str(job.id), str(wakeup.lease_id)' in scheduler
    assert "job.lease_id != lease_id" in scheduler
    assert "pg_advisory_xact_lock" in scheduler
    assert "FOR UPDATE" not in scheduler  # SQLAlchemy lock semantics stay typed.
    assert "with_for_update(skip_locked=True)" in scheduler
    assert "run_queued_candidate_build(" in worker
    assert "scheduler_lease_id=lease" in worker
    assert "publish_project_build" not in worker
    assert "activate=False" in (api_dir / "app" / "api" / "v1" / "projects.py").read_text(
        encoding="utf-8"
    )
    for action in ("/pause", "/resume", "/cancel", "/retry"):
        assert action in routes
    assert "input_snapshot" not in routes
    assert "lead_token" not in routes
    assert "create_site_build_job(db, build=build)" in index_schedule
    assert "IndexNow" not in index_schedule
    assert "create_bukvarix_keyword_job(db, run=run)" in semantic_sources
    assert "enqueue_bukvarix_keyword_run" not in semantic_sources
    assert "never imports or publishes" in bukvarix
    assert bukvarix.count("~_scheduler_owns_run()") == 2
    assert "~managed.exists()" in build_queue
    assert "~managed.exists()" in crawl
    assert "recover_legacy_competitor_crawls(session)" in worker
    assert "commit_bukvarix" not in scheduler
    assert "run_bukvarix_keyword_run(session, job.source_id)" in worker
