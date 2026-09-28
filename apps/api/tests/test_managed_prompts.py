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
    assert "post" in paths["/api/v1/ai/prompts/{prompt_id}/rollback-baseline"]


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

    class Session:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)

        async def commit(self):
            pass

    auth = SimpleNamespace(tenant_id=tenant_id, user=SimpleNamespace(id=uuid4()))
    monkeypatch.setattr(prompts, "_revision_or_404", AsyncMock(return_value=entry))
    monkeypatch.setattr(prompts, "_baseline", lambda _: baseline)
    monkeypatch.setattr(prompts, "append_audit", AsyncMock())

    activation_db = Session()
    asyncio.run(prompts.activate_prompt_revision(entry.key, revision_id, auth, activation_db))
    rollback_db = Session()
    asyncio.run(prompts.rollback_prompt_baseline(entry.key, auth, rollback_db))

    for statement in [activation_db.statements[0], rollback_db.statements[0]]:
        compiled = str(statement.compile(dialect=postgresql.dialect()))
        assert "prompt_registry.is_active IS true" in compiled


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
