from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from app.api.v1.project_families import _city_next_action, _public_fact_diff
from app.main import app
from app.schemas.project_family import (
    CityProjectReadinessOut,
    ProjectCityCloneCreate,
    ProjectFamilyMemberOut,
)
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


def test_city_readiness_keeps_public_fact_diff_and_private_boolean_only():
    diff = _public_fact_diff(
        {"service": "repair", "contacts": {"phone": "1"}, "address": "master"},
        {"service": "repair", "contacts": {"phone": "2"}, "custom": "child"},
    )
    body = CityProjectReadinessOut.model_validate(
        {
            "project_family_member_id": str(uuid4()),
            "child_project_id": str(uuid4()),
            "child_project_name": "Уфа",
            "child_project_slug": "ufa",
            "hostname": "ufa.example.test",
            "geo_id": str(uuid4()),
            "source_structure_revision_id": None,
            "facts_state": "draft",
            "facts_version": 1,
            "public_fact_diff": diff,
            "private_recipient_configured": False,
            "keyword_count": 0,
            "primary_geo_ready": True,
            "page_plans_by_state": {},
            "site_exists": False,
            "next_action": "review_city_facts",
        }
    )

    assert body.public_fact_diff == {
        "changed": ["contacts"],
        "missing": ["address"],
        "additional": ["custom"],
    }
    assert body.private_recipient_configured is False
    assert "private_lead_email_enc" not in body.model_dump()


@pytest.mark.parametrize(
    ("facts_state", "keyword_count", "plans", "site_exists", "expected"),
    [
        ("draft", 1, {}, False, "review_city_facts"),
        ("confirmed", 0, {}, False, "select_keywords"),
        ("confirmed", 1, {"draft": 1}, False, "submit_plan_for_review"),
        ("confirmed", 1, {"review": 1}, False, "approve_plan"),
        ("confirmed", 1, {"approved": 1}, False, "generate_draft"),
        ("confirmed", 1, {}, False, "prepare_page_plan"),
        ("confirmed", 1, {}, True, "create_candidate"),
    ],
)
def test_city_readiness_next_action_is_read_only_and_deterministic(
    facts_state, keyword_count, plans, site_exists, expected
):
    assert (
        _city_next_action(
            facts_state=facts_state,
            keyword_count=keyword_count,
            plan_counts=plans,
            site_exists=site_exists,
        )
        == expected
    )


def test_city_project_routes_are_registered():
    routes = app.openapi()["paths"]
    path = "/api/v1/projects/{project_id}/city-projects"

    assert {"get", "post"}.issubset(routes[path])
    assert "get" in routes[f"{path}/readiness"]


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
