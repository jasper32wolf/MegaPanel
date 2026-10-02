from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from app.main import app
from app.schemas.project_family import ProjectCityCloneCreate, ProjectFamilyMemberOut
from pydantic import ValidationError


def city_clone_payload(**overrides) -> dict:
    return {
        "geo_id": str(uuid4()),
        "name": "Ремонт телевизоров в Уфе",
        "slug": "repair-ufa",
        "hostname": "ufa.example.test",
        **overrides,
    }


def test_city_project_schema_requires_safe_operator_slug():
    body = ProjectCityCloneCreate.model_validate(city_clone_payload())

    assert body.slug == "repair-ufa"
    with pytest.raises(ValidationError, match="pattern"):
        ProjectCityCloneCreate.model_validate(city_clone_payload(slug="Ремонт Уфа"))


def test_city_member_out_keeps_confirmed_child_without_draft_facts():
    body = ProjectFamilyMemberOut.model_validate(
        {
            "id": str(uuid4()),
            "master_project_id": str(uuid4()),
            "child_project_id": str(uuid4()),
            "geo_id": str(uuid4()),
            "hostname": "ufa.example.test",
            "source_structure_revision_id": None,
            "child_project": {},
            "draft_fact_revision_id": None,
        }
    )

    assert body.draft_fact_revision_id is None


def test_city_project_routes_are_registered():
    routes = app.openapi()["paths"]
    path = "/api/v1/projects/{project_id}/city-projects"

    assert {"get", "post"}.issubset(routes[path])


def test_city_project_migration_keeps_tenant_scope_and_one_city_one_hostname():
    migration = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0044_project_city_family.py"
    ).read_text(encoding="utf-8")

    assert "uq_project_family_master_geo" in migration
    assert "uq_project_family_child" in migration
    assert "uq_project_family_hostname" in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "tenant_isolation_project_family_members" in migration
