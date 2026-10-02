from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from app.main import app
from app.schemas.site_structure import SiteStructureRevisionCreate
from pydantic import ValidationError

SEMANTIC_COLLECTION = uuid4()
EVIDENCE = uuid4()


def structure_page(**overrides) -> dict:
    return {
        "key": "home",
        "slug": "/",
        "title": "Ремонт телевизоров",
        "meta_description": "Подтверждённая услуга",
        "h1": "Ремонт телевизоров",
        "heading_outline": [{"level": "h2", "text": "Услуги"}],
        "objective": "Представить подтверждённую услугу",
        "kit_key": "service-local-v1",
        "block_ids": ["hero"],
        **overrides,
    }


def revision_payload(**overrides) -> dict:
    return {
        "semantic_collection_id": str(SEMANTIC_COLLECTION),
        "evidence_ids": [str(EVIDENCE)],
        "pages": [structure_page()],
        **overrides,
    }


def test_site_structure_schema_normalizes_page_paths_and_preserves_blueprint():
    body = SiteStructureRevisionCreate.model_validate(
        revision_payload(pages=[structure_page(slug="/repair/")])
    )

    assert body.pages[0].slug == "/repair"
    assert body.pages[0].heading_outline[0].level == "h2"


@pytest.mark.parametrize(
    "payload, expected",
    [
        (revision_payload(pages=[structure_page(), structure_page()]), "duplicate page keys"),
        (
            revision_payload(pages=[structure_page(parent_key="missing")]),
            "unknown parent",
        ),
        (revision_payload(evidence_ids=[str(EVIDENCE), str(EVIDENCE)]), "duplicate evidence"),
        (revision_payload(pages=[structure_page(block_ids=["hero", "hero"])]), "duplicates"),
    ],
)
def test_site_structure_schema_rejects_invalid_tree_and_duplicate_inputs(payload, expected):
    with pytest.raises(ValidationError, match=expected):
        SiteStructureRevisionCreate.model_validate(payload)


def test_site_structure_routes_are_registered():
    paths = app.openapi()["paths"]
    root = "/api/v1/projects/{project_id}/site-structure/revisions"

    assert {"get", "post"}.issubset(paths[root])
    for action in ("submit-review", "approve", "reject", "materialize"):
        assert "post" in paths[f"{root}/{{revision_id}}/{action}"]


def test_site_structure_migration_enforces_rls_and_approved_immutability():
    migration = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0043_site_structure_revisions.py"
    ).read_text(encoding="utf-8")

    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "tenant_isolation_site_structure_revisions" in migration
    assert "site_structure_revisions_approved_immutable" in migration
    assert "approved site structure revision is immutable" in migration
