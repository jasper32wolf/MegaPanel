from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from app.api.v1.site_structure import _ai_structure_body
from app.main import app
from app.schemas.site_structure import SiteStructureAIImportCreate
from fastapi import HTTPException
from pydantic import ValidationError

KEYWORD = UUID("11111111-1111-1111-1111-111111111111")
GEO = UUID("22222222-2222-2222-2222-222222222222")


def approved_run(*, pages: list[dict] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        output={
            "pages": pages
            or [
                {
                    "key": "home",
                    "title": "Ремонт телевизоров",
                    "meta_description": "Подтверждённая услуга",
                    "h1": "Ремонт телевизоров",
                    "heading_outline": [{"level": "h2", "text": "Услуги"}],
                    "purpose": "Представить подтверждённую услугу",
                    "slug": "/",
                    "keyword_ids": [str(KEYWORD)],
                    "geo_ids": [str(GEO)],
                    "fact_keys": ["service"],
                    "kit_key": "service-local-v1",
                    "block_ids": ["hero"],
                    "uncertainty_notes": ["Проверить коммерческие claims"],
                }
            ]
        },
    )


def import_body(**overrides) -> SiteStructureAIImportCreate:
    return SiteStructureAIImportCreate.model_validate(
        {
            "ai_run_id": str(uuid4()),
            "semantic_collection_id": str(uuid4()),
            "evidence_ids": [str(uuid4())],
            "confirm_create_draft": True,
            **overrides,
        }
    )


def test_import_schema_requires_explicit_true_confirmation() -> None:
    with pytest.raises(ValidationError):
        import_body(confirm_create_draft=False)


def test_ai_import_converts_only_persisted_proposal_to_typed_structure() -> None:
    body, output_hash = _ai_structure_body(import_body(), approved_run())

    page = body.pages[0]
    assert page.key == "home"
    assert page.meta_description == "Подтверждённая услуга"
    assert page.h1 == "Ремонт телевизоров"
    assert page.heading_outline[0].level == "h2"
    assert page.risk_notes == "Проверить коммерческие claims"
    assert len(output_hash) == 64


@pytest.mark.parametrize(
    "page",
    [
        {**approved_run().output["pages"][0], "key": "bad key"},
        {**approved_run().output["pages"][0], "slug": "/bad//slug"},
        {**approved_run().output["pages"][0], "parent_key": "missing"},
        {**approved_run().output["pages"][0], "block_ids": ["hero", "hero"]},
        {**approved_run().output["pages"][0], "html": "<script>unsafe</script>"},
    ],
)
def test_ai_import_rejects_invalid_or_untrusted_proposal(page: dict) -> None:
    with pytest.raises(HTTPException):
        _ai_structure_body(import_body(), approved_run(pages=[page]))


def test_site_structure_ai_import_route_is_registered() -> None:
    paths = app.openapi()["paths"]
    root = "/api/v1/projects/{project_id}/site-structure/revisions/import-approved-ai-run"

    assert "post" in paths[root]
    assert "/api/v1/ai/runs/{run_id}/page-plans" not in paths


def test_site_structure_ai_import_migration_has_rls_and_durable_unique_link() -> None:
    migration = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0045_site_structure_ai_run_imports.py"
    ).read_text(encoding="utf-8")

    assert "uq_site_structure_ai_import_run" in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "tenant_isolation_site_structure_ai_run_imports" in migration
