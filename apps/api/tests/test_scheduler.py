from __future__ import annotations

import importlib.util
from pathlib import Path


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

    assert 'enqueue_job("scheduler_execute_task", str(wakeup.scheduler_job_id))' in scheduler
    assert "FOR UPDATE" not in scheduler  # SQLAlchemy lock semantics stay typed.
    assert "with_for_update(skip_locked=True)" in scheduler
    assert "run_queued_candidate_build(session, job.source_id)" in worker
    assert "publish_project_build" not in worker
    assert "activate=False" in (api_dir / "app" / "api" / "v1" / "projects.py").read_text(
        encoding="utf-8"
    )
    for action in ("/pause", "/resume", "/cancel"):
        assert action in routes
    assert "input_snapshot" not in routes
    assert "lead_token" not in routes
    assert "create_site_build_job(db, build=build)" in index_schedule
    assert "IndexNow" not in index_schedule
    assert "create_bukvarix_keyword_job(db, run=run)" in semantic_sources
    assert "enqueue_bukvarix_keyword_run" not in semantic_sources
    assert "never imports or publishes" in bukvarix
    assert "commit_bukvarix" not in scheduler
    assert "run_bukvarix_keyword_run(session, job.source_id)" in worker
