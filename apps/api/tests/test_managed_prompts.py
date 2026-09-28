from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
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
