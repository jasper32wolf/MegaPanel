from __future__ import annotations

import asyncio
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import yaml
from app.api.v1 import system
from app.models import AuthSession, Notification, OperationalEvent, User, WebhookDeliveryAttempt
from app.services import dsar, storage_retention
from sqlalchemy.dialects import postgresql

ROOT = Path(__file__).resolve().parents[3]
SUPERVISOR_PATH = ROOT / "scripts" / "log_supervisor.py"


def test_effective_policy_hash_changes_only_with_reviewed_values(monkeypatch):
    settings = SimpleNamespace(
        auth_session_retention_days=180,
        notification_retention_days=90,
        operational_event_retention_days=90,
        delivery_attempt_retention_days=90,
        dsar_export_ttl_hours=24,
    )
    monkeypatch.setattr(storage_retention, "get_settings", lambda: settings)

    assert storage_retention.effective_policy() == {
        "auth_session_retention_days": 180,
        "notification_retention_days": 90,
        "operational_event_retention_days": 90,
        "delivery_attempt_retention_days": 90,
        "dsar_export_ttl_hours": 24,
    }
    original = storage_retention.policy_hash()
    settings.notification_retention_days = 91
    assert storage_retention.policy_hash() != original


def test_retention_selectors_remain_tenant_scoped_and_preserve_dead_letters():
    tenant_id = uuid4()
    cutoff = datetime(2026, 10, 1, tzinfo=UTC)

    sessions = storage_retention._session_selector(tenant_id=tenant_id, cutoff=cutoff)
    session_statement = storage_retention.select(AuthSession.id).select_from(AuthSession, User)
    session_sql = str(session_statement.where(*sessions).compile(dialect=postgresql.dialect()))
    assert "users.tenant_id" in session_sql
    assert "auth_sessions.user_id = users.id" in session_sql

    notifications = storage_retention._notification_selector(cutoff=cutoff)
    notification_sql = str(
        storage_retention.select(Notification.id).where(*notifications).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "alert_deliveries.status NOT IN" in notification_sql
    assert storage_retention._TERMINAL_DELIVERY_STATUSES == ("delivered",)


def test_retention_deletion_queries_select_only_ids():
    tenant_id = uuid4()
    run = SimpleNamespace(tenant_id=tenant_id)
    now = datetime(2026, 10, 11, tzinfo=UTC)
    policy = {
        "auth_session_retention_days": 180,
        "notification_retention_days": 90,
        "operational_event_retention_days": 90,
        "delivery_attempt_retention_days": 90,
        "dsar_export_ttl_hours": 24,
    }

    original = storage_retention.effective_policy
    storage_retention.effective_policy = lambda: policy
    statements = []

    async def delete_ids(_db, model, statement):
        statements.append((model, statement))
        return 0

    original_delete = storage_retention._delete_ids
    storage_retention._delete_ids = delete_ids
    try:
        asyncio.run(storage_retention._execute_run(SimpleNamespace(), run, now=now))
    finally:
        storage_retention.effective_policy = original
        storage_retention._delete_ids = original_delete

    assert [model for model, _ in statements] == [
        AuthSession,
        Notification,
        OperationalEvent,
        WebhookDeliveryAttempt,
    ]
    assert all(".id" in str(statement.selected_columns) for _, statement in statements)


def test_expired_dsar_cleanup_does_not_follow_symlinks_outside_root(tmp_path, monkeypatch):
    root = tmp_path / "exports"
    tenant_dir = root / "tenant"
    tenant_dir.mkdir(parents=True)
    expired = tenant_dir / "expired.json"
    fresh = tenant_dir / "fresh.json"
    outside = tmp_path / "outside.json"
    expired.write_text("[]", encoding="utf-8")
    fresh.write_text("[]", encoding="utf-8")
    outside.write_text("protected", encoding="utf-8")
    old = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    expired.touch()
    fresh.touch()
    import os

    os.utime(expired, (old, old))
    escaped = tenant_dir / "escaped.json"
    try:
        escaped.symlink_to(outside)
    except OSError:
        return

    monkeypatch.setattr(
        dsar,
        "get_settings",
        lambda: SimpleNamespace(dsar_exports_root=str(root), dsar_export_ttl_hours=1),
    )

    assert dsar.purge_expired_exports() == 1
    assert not expired.exists()
    assert fresh.exists()
    assert outside.read_text(encoding="utf-8") == "protected"


def test_storage_routes_are_mfa_protected_and_not_generic_deletes():
    paths = system.router.routes
    declared = {route.path: route.methods for route in paths if hasattr(route, "methods")}

    assert "/storage" in declared
    assert declared["/storage"] == {"GET"}
    assert declared["/storage/previews"] == {"POST"}
    assert declared["/storage/previews/{run_id}/run"] == {"POST"}
    assert not any("delete" in path.lower() for path in declared)
    source = (ROOT / "apps" / "api" / "app" / "api" / "v1" / "system.py").read_text(
        encoding="utf-8"
    )
    storage_start = source.index('@router.get("/storage")')
    storage_end = source.index('@router.post("/github/workflow-run",')
    storage_source = source[storage_start:storage_end]
    assert storage_source.count("Depends(require_system_operator)") == 3
    retention_path = ROOT / "apps" / "api" / "app" / "services" / "storage_retention.py"
    retention_source = retention_path.read_text(encoding="utf-8")
    assert 'confirmation != "PURGE EXPIRED DATA"' in retention_source


def test_worker_registers_scheduled_and_manual_retention_tasks():
    from app.worker import (
        WorkerSettings,
        dsar_export_retention_task,
        schedule_storage_retention_task,
        site_release_retention_task,
        storage_retention_sweep_task,
    )

    tasks = (
        dsar_export_retention_task,
        storage_retention_sweep_task,
        schedule_storage_retention_task,
        site_release_retention_task,
    )
    for task in tasks:
        assert task in WorkerSettings.functions
        assert any(job.coroutine is task for job in WorkerSettings.cron_jobs)


def test_log_supervisor_rotates_without_exceeding_the_active_file_limit(tmp_path):
    spec = importlib.util.spec_from_file_location("log_supervisor", SUPERVISOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.MAX_BYTES = 10
    module.GENERATIONS = 2
    target = tmp_path / "api.out.log"

    log = module.RotatingLog(target)
    log.write(b"x" * 25)

    assert target.read_bytes() == b"xxxxx"
    assert target.stat().st_size <= 10
    assert target.with_name("api.out.log.1").read_bytes() == b"x" * 10
    assert target.with_name("api.out.log.2").read_bytes() == b"x" * 10


def test_compose_and_host_logging_have_fixed_bounds():
    for relative_path in (
        "infra/docker/docker-compose.production.yml",
        "infra/docker/docker-compose.yml",
        "infra/docker/docker-compose.deps.yml",
    ):
        compose = yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))
        assert compose["x-bounded-logging"] == {
            "driver": "local",
            "options": {"max-size": "10m", "max-file": "3"},
        }
        services = compose["services"].values()
        assert all(service.get("logging") == compose["x-bounded-logging"] for service in services)
        redis = compose["services"]["redis"]
        assert "--maxmemory-policy" in redis["command"]
        assert redis["command"][redis["command"].index("--maxmemory-policy") + 1] == "noeviction"
        assert "--auto-aof-rewrite-percentage" in redis["command"]

    bootstrap = (ROOT / "scripts" / "bootstrap-github-deploy.sh").read_text(encoding="utf-8")
    assert "SystemMaxUse=256M" in bootstrap
    assert "SystemMaxFileSec=14day" in bootstrap
    release_manager = (ROOT / "scripts" / "release-manager.sh").read_text(encoding="utf-8")
    assert "validate_audit_rotation_settings" in release_manager
    assert 'AUDIT_LOG_GENERATIONS="${AUDIT_LOG_GENERATIONS:-52}"' in release_manager
    assert "gzip -n --" in release_manager
    assert "rm -f --" in release_manager
