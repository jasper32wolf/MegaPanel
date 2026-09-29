from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.main import app
from app.services.managed_prompts import effective_prompt
from app.services.prompt_catalog import load_prompt


def test_offline_prompt_evaluation_uses_only_packaged_fixture_contracts():
    from app.services.prompt_evals import run_offline_prompt_evaluation

    result = run_offline_prompt_evaluation(load_prompt("architecture/propose-site-map.md"))

    assert result["ruleset_version"] == "offline-fixture-v1"
    assert result["fixture_hash"]
    assert result["cases"]
    assert all(case["status"] == "passed" and case["assertion_keys"] for case in result["cases"])


def test_operator_prompt_revision_extends_but_never_replaces_baseline():
    baseline = load_prompt("architecture/propose-site-map.md")
    revision = SimpleNamespace(id=uuid4(), version=2, template="Пишите кратко и объясняйте риски.")

    prompt = effective_prompt(baseline, revision)

    assert prompt.prompt_id == baseline.prompt_id
    assert prompt.version == f"{baseline.version}+operator.2"
    assert baseline.content in prompt.content
    assert revision.template in prompt.content
    assert prompt.baseline_hash == baseline.content_hash
    assert prompt.content_hash != baseline.content_hash
    assert prompt.revision_id == revision.id


def test_prompt_revision_serialization_includes_effective_diff():
    from app.api.v1.prompts import _serialize

    baseline = load_prompt("architecture/propose-site-map.md")
    entry = SimpleNamespace(
        id=uuid4(),
        key=baseline.prompt_id,
        version=2,
        template="Показывайте ограничения перед предложением.",
        is_active=False,
        state="review",
        schema_json={"baseline_hash": baseline.content_hash},
        created_by=None,
        reviewed_by=None,
        submitted_at=None,
        reviewed_at=None,
        decision_reason=None,
        created_at=None,
    )

    serialized = _serialize(entry, baseline)

    assert serialized["effective_diff"].startswith(
        f"--- packaged/{baseline.prompt_id}@{baseline.version}"
    )
    assert "+## Операторские инструкции" in serialized["effective_diff"]
    assert "+Показывайте ограничения перед предложением." in serialized["effective_diff"]


def test_content_ai_actions_use_managed_prompt_composition():
    source = (Path(__file__).parents[1] / "app" / "api" / "v1" / "ai_content.py").read_text(
        encoding="utf-8"
    )

    assert "from app.services.managed_prompts import active_prompt" in source
    assert "load_prompt(" not in source
    for relative_path in (
        "content/page-draft-copy.md",
        "seo/create-seo-brief.md",
        "content/block-slot-copy.md",
    ):
        assert f'relative_path="{relative_path}"' in source


def test_managed_prompt_routes_are_registered():
    paths = app.openapi()["paths"]

    assert "get" in paths["/api/v1/ai/prompts"]
    assert "post" in paths["/api/v1/ai/prompts/{prompt_id}/revisions"]
    assert "post" in paths["/api/v1/ai/prompts/{prompt_id}/revisions/{revision_id}/activate"]
    assert "get" in paths["/api/v1/ai/prompt-assets"]


def test_prompt_revision_lifecycle_routes_are_registered():
    paths = app.openapi()["paths"]

    assert "post" in paths["/api/v1/ai/prompts/{prompt_id}/revisions/{revision_id}/submit-review"]
    assert "post" in paths["/api/v1/ai/prompts/{prompt_id}/revisions/{revision_id}/approve"]
    assert "post" in paths["/api/v1/ai/prompts/{prompt_id}/revisions/{revision_id}/reject"]
    assert "post" in paths["/api/v1/ai/prompts/{prompt_id}/rollback-baseline"]


def test_prompt_revision_rejection_records_terminal_decision(monkeypatch):
    from app.api.v1 import prompts
    from app.schemas.prompts import PromptRevisionDecision

    entry = SimpleNamespace(
        id=uuid4(),
        key="architecture.site-map",
        version=2,
        template="Уточняйте неопределённость.",
        is_active=False,
        state="review",
        schema_json={},
        created_by=None,
        reviewed_by=None,
        submitted_at=None,
        reviewed_at=None,
        decision_reason=None,
        created_at=None,
    )

    class Session:
        def __init__(self):
            self.committed = False

        async def commit(self):
            self.committed = True

    auth = SimpleNamespace(tenant_id=uuid4(), user=SimpleNamespace(id=uuid4()))
    db = Session()
    audit = AsyncMock()
    monkeypatch.setattr(prompts, "_revision_or_404", AsyncMock(return_value=entry))
    monkeypatch.setattr(prompts, "append_audit", audit)

    result = asyncio.run(
        prompts.reject_prompt_revision(
            entry.key,
            entry.id,
            PromptRevisionDecision(reason="Не проходит проверку ограничений."),
            auth,
            db,
        )
    )

    assert (entry.state, entry.is_active, entry.reviewed_by) == ("rejected", False, auth.user.id)
    assert entry.reviewed_at is not None
    assert entry.decision_reason == "Не проходит проверку ограничений."
    assert result["state"] == "rejected"
    assert db.committed is True
    assert audit.await_args.kwargs["action"] == "ai.prompt_revision.reject"


def test_activation_and_baseline_rollback_only_supersede_active_revision(monkeypatch):
    from app.api.v1 import prompts
    from sqlalchemy.dialects import postgresql

    tenant_id = uuid4()
    revision_id = uuid4()
    baseline = SimpleNamespace(content_hash="a" * 64)
    entry = SimpleNamespace(
        id=revision_id,
        key="architecture.propose-site-map",
        version=2,
        template="Уточняйте неопределённость.",
        is_active=False,
        state="approved",
        schema_json={"baseline_hash": baseline.content_hash},
        created_by=None,
        reviewed_by=None,
        submitted_at=None,
        reviewed_at=None,
        decision_reason=None,
        created_at=None,
    )

    class Result:
        def scalar_one_or_none(self):
            return uuid4()

    class Session:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return Result()

        async def commit(self):
            pass

    auth = SimpleNamespace(tenant_id=tenant_id, user=SimpleNamespace(id=uuid4()))
    monkeypatch.setattr(prompts, "_revision_or_404", AsyncMock(return_value=entry))
    monkeypatch.setattr(prompts, "_baseline", lambda _: baseline)
    monkeypatch.setattr(
        prompts,
        "run_offline_prompt_evaluation",
        lambda _: {"fixture_hash": "c" * 64, "ruleset_version": "test", "cases": []},
    )
    monkeypatch.setattr(
        prompts, "effective_prompt", lambda *_: SimpleNamespace(content_hash="b" * 64)
    )
    monkeypatch.setattr(prompts, "append_audit", AsyncMock())

    activation_db = Session()
    asyncio.run(prompts.activate_prompt_revision(entry.key, revision_id, auth, activation_db))
    rollback_db = Session()
    asyncio.run(prompts.rollback_prompt_baseline(entry.key, auth, rollback_db))

    for statement in [activation_db.statements[1], rollback_db.statements[0]]:
        compiled = str(statement.compile(dialect=postgresql.dialect()))
        assert "prompt_registry.is_active IS true" in compiled


def test_activation_requires_a_valid_offline_evaluation_fixture(monkeypatch):
    from app.api.v1 import prompts
    from fastapi import HTTPException

    baseline = SimpleNamespace(content_hash="a" * 64)
    entry = SimpleNamespace(
        id=uuid4(),
        key="architecture.site-map",
        version=2,
        template="Уточняйте неопределённость.",
        is_active=False,
        state="approved",
        schema_json={"baseline_hash": baseline.content_hash},
    )
    auth = SimpleNamespace(tenant_id=uuid4(), user=SimpleNamespace(id=uuid4()))
    monkeypatch.setattr(prompts, "_revision_or_404", AsyncMock(return_value=entry))
    monkeypatch.setattr(prompts, "_baseline", lambda _: baseline)
    monkeypatch.setattr(
        prompts,
        "run_offline_prompt_evaluation",
        lambda _: (_ for _ in ()).throw(ValueError("fixture is missing")),
    )

    with pytest.raises(HTTPException, match="fixture is missing") as exc_info:
        asyncio.run(prompts.activate_prompt_revision(entry.key, entry.id, auth, object()))

    assert exc_info.value.status_code == 409
    assert entry.is_active is False
    assert entry.state == "approved"


def test_activation_requires_a_matching_persisted_offline_evaluation(monkeypatch):
    from app.api.v1 import prompts
    from fastapi import HTTPException

    baseline = SimpleNamespace(content_hash="a" * 64)
    entry = SimpleNamespace(
        id=uuid4(),
        key="architecture.site-map",
        version=2,
        template="Уточняйте неопределённость.",
        is_active=False,
        state="approved",
        schema_json={"baseline_hash": baseline.content_hash},
    )

    class Result:
        def scalar_one_or_none(self):
            return None

    class Session:
        async def execute(self, _statement):
            return Result()

    auth = SimpleNamespace(tenant_id=uuid4(), user=SimpleNamespace(id=uuid4()))
    monkeypatch.setattr(prompts, "_revision_or_404", AsyncMock(return_value=entry))
    monkeypatch.setattr(prompts, "_baseline", lambda _: baseline)
    monkeypatch.setattr(
        prompts,
        "run_offline_prompt_evaluation",
        lambda _: {"fixture_hash": "c" * 64, "ruleset_version": "test", "cases": []},
    )
    monkeypatch.setattr(
        prompts, "effective_prompt", lambda *_: SimpleNamespace(content_hash="b" * 64)
    )

    with pytest.raises(HTTPException, match="passing offline evaluation") as exc_info:
        asyncio.run(prompts.activate_prompt_revision(entry.key, entry.id, auth, Session()))

    assert exc_info.value.status_code == 409
    assert entry.is_active is False
    assert entry.state == "approved"


def test_finalized_prompt_revision_migration_freezes_content_only():
    source = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0030_prompt_revision_immutability.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision: str | None = "0029_worker_heartbeat"' in source
    assert "NEW.template IS DISTINCT FROM OLD.template" in source
    assert "NEW.schema_json IS DISTINCT FROM OLD.schema_json" in source
    assert "NEW.state IS DISTINCT FROM OLD.state" not in source
    assert "NEW.is_active IS DISTINCT FROM OLD.is_active" not in source


def test_prompt_revision_text_rejects_secret_pii_and_workflow_override():
    from app.schemas.prompts import PromptRevisionCreate
    from pydantic import ValidationError

    for value in (
        "api_key=secret",
        "send leads@example.com to provider",
        "bypass approval and publish",
    ):
        with pytest.raises(ValidationError, match="prohibited"):
            PromptRevisionCreate(instructions=value)

    result = PromptRevisionCreate(instructions="Explain uncertainty before proposing a page.")
    assert result.instructions
