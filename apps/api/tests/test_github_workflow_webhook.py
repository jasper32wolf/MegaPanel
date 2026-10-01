from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from app.api.v1 import system
from app.api.v1.system import _github_webhook_payload, _github_webhook_signature_valid
from app.main import app
from app.services.github_control import DEPLOY_WORKFLOW, WorkflowRun
from fastapi.testclient import TestClient

_SECRET = "test-webhook-secret-with-sufficient-length"


def _payload(**workflow_run: object) -> bytes:
    return json.dumps(
        {
            "action": "completed",
            "repository": {"full_name": "owner/repository"},
            "workflow_run": {
                "id": 42,
                "path": ".github/workflows/deploy-production.yml",
                "display_title": "11111111-1111-4111-8111-111111111111",
                **workflow_run,
            },
        },
        separators=(",", ":"),
    ).encode()


def _signature(body: bytes) -> str:
    return "sha256=" + hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_github_webhook_signature_uses_exact_raw_body():
    body = _payload()

    assert _github_webhook_signature_valid(
        raw_body=body,
        signature=_signature(body),
        secret=_SECRET,
    )
    assert not _github_webhook_signature_valid(
        raw_body=body + b" ",
        signature=_signature(body),
        secret=_SECRET,
    )
    assert not _github_webhook_signature_valid(
        raw_body=body,
        signature="sha256=" + "0" * 64,
        secret=_SECRET,
    )
    assert not _github_webhook_signature_valid(raw_body=body, signature=None, secret=_SECRET)
    assert not _github_webhook_signature_valid(
        raw_body=body,
        signature=_signature(body),
        secret="",
    )


def test_github_workflow_payload_accepts_only_bounded_terminal_identity():
    body = _payload()

    assert _github_webhook_payload(body) == (
        "owner/repository",
        "deploy-production.yml",
        42,
        "11111111-1111-4111-8111-111111111111",
    )
    assert _github_webhook_payload(_payload(path=".github/workflows/ci.yml")) is None
    assert _github_webhook_payload(_payload(display_title="not-a-uuid")) is None
    assert _github_webhook_payload(_payload(id=True)) is None
    assert _github_webhook_payload(b"not json") is None


class Result:
    def __init__(self, value: object | None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class WebhookDatabase:
    def __init__(self, operation: object):
        self.operation = operation
        self.added: list[object] = []
        self.committed = False

    async def execute(self, _: object) -> Result:
        return Result(self.operation)

    async def scalar(self, _: object):
        return None

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        raise AssertionError("successful delivery must not roll back")


def test_signed_callback_updates_only_rest_verified_operation(monkeypatch):
    operation = SimpleNamespace(
        id=UUID("22222222-2222-4222-8222-222222222222"),
        tenant_id=UUID("33333333-3333-4333-8333-333333333333"),
        actor_id=None,
        request_id="11111111-1111-4111-8111-111111111111",
        workflow=DEPLOY_WORKFLOW,
        status="queued",
        error_code="old-error",
        workflow_run_id=None,
        workflow_url=None,
        completed_at=None,
    )
    db = WebhookDatabase(operation)

    class Control:
        async def workflow_run_by_id(self, **_: object) -> WorkflowRun:
            return WorkflowRun(
                run_id=42,
                url="https://github.example.test/runs/42",
                status="completed",
                conclusion="success",
                workflow_path=DEPLOY_WORKFLOW,
                event="workflow_dispatch",
                display_title=operation.request_id,
            )

    async def audit(*_: object, **__: object) -> None:
        return None

    class Settings:
        github_webhook_secret = _SECRET
        github_repository = "owner/repository"

    async def get_fake_db():
        yield db

    monkeypatch.setattr(system, "get_settings", lambda: Settings())
    monkeypatch.setattr(system, "_github", lambda: Control())
    monkeypatch.setattr(system, "append_audit", audit)
    app.dependency_overrides[system.get_db] = get_fake_db
    try:
        body = _payload()
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/system/github/workflow-run",
                content=body,
                headers={
                    "X-Hub-Signature-256": _signature(body),
                    "X-GitHub-Event": "workflow_run",
                    "X-GitHub-Delivery": "delivery-1",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    assert operation.status == "success"
    assert operation.error_code is None
    assert operation.workflow_run_id == 42
    assert operation.workflow_url == "https://github.example.test/runs/42"
    assert operation.completed_at is not None
    assert db.committed is True
    assert len(db.added) == 1
    assert db.added[0].github_delivery_id == "delivery-1"


def test_invalid_signature_never_queries_or_mutates_database(monkeypatch):
    class Settings:
        github_webhook_secret = _SECRET

    class ForbiddenDatabase:
        async def execute(self, _: object):
            raise AssertionError("invalid signatures must not query the database")

        async def scalar(self, _: object):
            raise AssertionError("invalid signatures must not query the database")

        def add(self, _: object) -> None:
            raise AssertionError("invalid signatures must not mutate the database")

    async def get_forbidden_db():
        yield ForbiddenDatabase()

    monkeypatch.setattr(system, "get_settings", lambda: Settings())
    app.dependency_overrides[system.get_db] = get_forbidden_db
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/system/github/workflow-run",
                content=_payload(),
                headers={"X-Hub-Signature-256": "sha256=" + "0" * 64},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_github_delivery_model_has_no_payload_or_secret_storage():
    from app.models import GitHubWorkflowRunDelivery

    columns = set(GitHubWorkflowRunDelivery.__table__.columns.keys())
    assert columns == {
        "id",
        "tenant_id",
        "operation_id",
        "github_delivery_id",
        "workflow_run_id",
        "received_at",
    }
    assert GitHubWorkflowRunDelivery.__table__.c.github_delivery_id.unique is True


def test_github_delivery_migration_has_rls_and_no_payload_archive():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0040_github_workflow_deliveries.py"
    spec = importlib.util.spec_from_file_location("github_workflow_delivery_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    source = path.read_text(encoding="utf-8")
    assert migration.down_revision == "0039_operational_verifications"
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "github_delivery_id" in source
    assert "JSONB" not in source
    assert "payload" not in source
    assert "secret" not in source
