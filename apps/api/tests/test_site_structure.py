from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1.site_structure import _page_plan_from_structure
from app.main import app
from app.schemas.site_structure import (
    SiteStructureCityChildrenMaterializeCreate,
    SiteStructureRevisionCreate,
    SiteStructureRevisionUpdate,
)
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


def test_site_structure_schema_rejects_parent_cycle_and_validates_update_hash():
    pages = [
        structure_page(key="services", slug="/services", parent_key="repair"),
        structure_page(key="repair", slug="/repair", parent_key="services"),
    ]
    with pytest.raises(ValidationError, match="parent cycle"):
        SiteStructureRevisionCreate.model_validate(revision_payload(pages=pages))

    update = SiteStructureRevisionUpdate.model_validate(
        {**revision_payload(), "expected_structure_hash": "a" * 64}
    )
    assert update.expected_structure_hash == "a" * 64


def test_city_materialization_plan_keeps_only_structure_provenance():
    revision = SimpleNamespace(id=uuid4(), version=3)
    project = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    member_id = uuid4()
    plan = _page_plan_from_structure(
        revision=revision,
        page=structure_page(),
        project=project,
        existing=None,
        family_materialization={
            "site_structure_revision_id": str(revision.id),
            "master_project_id": str(uuid4()),
            "child_project_id": str(project.id),
            "project_family_member_id": str(member_id),
            "source_structure_hash": "a" * 64,
        },
    )

    assert plan.project_id == project.id
    assert plan.state == "draft"
    assert plan.fact_revision_id is None
    assert plan.keyword_snapshot == {}
    assert plan.geo_snapshot == {}
    assert plan.semantic_target_snapshot == {}
    assert plan.source_refs["family_materialization"]["project_family_member_id"] == str(member_id)


def test_city_children_materialization_schema_requires_explicit_unique_selection():
    child_id = uuid4()
    body = SiteStructureCityChildrenMaterializeCreate.model_validate(
        {"child_project_ids": [str(child_id)], "confirm_create_drafts": True}
    )

    assert body.child_project_ids == [child_id]
    with pytest.raises(ValidationError, match="duplicates"):
        SiteStructureCityChildrenMaterializeCreate.model_validate(
            {
                "child_project_ids": [str(child_id), str(child_id)],
                "confirm_create_drafts": True,
            }
        )
    with pytest.raises(ValidationError):
        SiteStructureCityChildrenMaterializeCreate.model_validate(
            {"child_project_ids": [str(child_id)], "confirm_create_drafts": False}
        )


def test_site_structure_routes_are_registered():
    paths = app.openapi()["paths"]
    root = "/api/v1/projects/{project_id}/site-structure/revisions"

    assert {"get", "post"}.issubset(paths[root])
    assert "patch" in paths[f"{root}/{{revision_id}}"]
    for action in (
        "submit-review",
        "approve",
        "reject",
        "materialize",
        "materialize-city-children",
    ):
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
