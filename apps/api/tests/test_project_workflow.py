from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1 import projects
from app.api.v1.projects import _project_site_or_409, _public_fact_values, _selection_snapshots
from app.main import app
from app.schemas.workflow import (
    FactRevisionCreate,
    LeadOutcomeIn,
    PagePlanCreate,
    ProjectGeoUpdate,
    ProjectKeywordsUpdate,
)
from app.services.generation import create_page_draft
from app.services.qa import run_page_qa
from fastapi import HTTPException
from pydantic import ValidationError


class SelectionDatabase:
    def __init__(self, keyword_rows: list[object], geo_rows: list[object]):
        self.rows = [keyword_rows, geo_rows]

    async def execute(self, _: object) -> object:
        rows = self.rows.pop(0)
        return SimpleNamespace(all=lambda: rows)


class ProjectSiteDatabase:
    def __init__(self, site: object | None):
        self.site = site
        self.statement: object | None = None

    async def execute(self, statement: object) -> object:
        self.statement = statement
        return SimpleNamespace(scalar_one_or_none=lambda: self.site)


class PreviewDatabase:
    def __init__(self, rows: list[object]):
        self.rows = rows

    async def execute(self, _: object) -> object:
        row = self.rows.pop(0)
        return SimpleNamespace(scalar_one_or_none=lambda: row)


def test_page_plan_normalizes_root_slug():
    assert (
        PagePlanCreate(slug=" / ", objective="Главная страница", kit_key="service-local-v1").slug
        == "/"
    )


def test_page_plan_normalizes_valid_path_slug():
    assert (
        PagePlanCreate(
            slug=" Repair-Washing-Machines/urgent-service ",
            objective="Ремонт техники",
            kit_key="service-local-v1",
        ).slug
        == "/repair-washing-machines/urgent-service"
    )


@pytest.mark.parametrize("slug", ["/../admin", "/repair//urgent", "/repair?x=1", "/ремонт"])
def test_page_plan_rejects_invalid_path_slug(slug: str):
    with pytest.raises(ValidationError, match="Page path"):
        PagePlanCreate(slug=slug, objective="Ремонт техники", kit_key="service-local-v1")


@pytest.mark.parametrize("field", ["webhook_url", "webhook_secret", "webhook_secret_enc"])
def test_fact_revision_rejects_protected_webhook_fields(field: str):
    with pytest.raises(ValidationError, match="webhook credentials"):
        FactRevisionCreate(facts={"service": "Ремонт", "contacts": {field: "secret"}})


def test_legacy_fact_serialization_removes_protected_webhook_fields():
    facts = _public_fact_values(
        {
            "service": "Ремонт",
            "contacts": {
                "phone": "+79990000000",
                "webhook_url": "https://hooks.example.test/lead",
                "webhook_secret": "plaintext",
                "webhook_secret_enc": "encrypted",
            },
        }
    )

    assert facts["contacts"] == {"phone": "+79990000000"}


def test_project_fact_revisions_route_is_registered():
    assert "get" in app.openapi()["paths"]["/api/v1/projects/{project_id}/facts"]


def test_project_site_link_requires_matching_tenant_and_reciprocal_project():
    tenant_id = uuid4()
    project = SimpleNamespace(id=uuid4(), site_id=uuid4(), tenant_id=tenant_id)
    legacy_site = SimpleNamespace(id=project.site_id, tenant_id=tenant_id, project_id=None)
    db = ProjectSiteDatabase(legacy_site)

    assert asyncio.run(_project_site_or_409(db, project)) is legacy_site
    assert "sites.tenant_id" in str(db.statement)

    invalid_sites = (
        None,
        SimpleNamespace(id=project.site_id, tenant_id=uuid4(), project_id=None),
        SimpleNamespace(id=project.site_id, tenant_id=tenant_id, project_id=uuid4()),
    )
    for site in invalid_sites:
        with pytest.raises(HTTPException, match="Project site link is invalid") as exc_info:
            asyncio.run(_project_site_or_409(ProjectSiteDatabase(site), project))
        assert exc_info.value.status_code == 409


def test_authenticated_preview_serves_release_assets_without_path_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    tenant_id = uuid4()
    project_id = uuid4()
    site_id = uuid4()
    build_id = uuid4()
    build_hash = "a" * 64
    project = SimpleNamespace(id=project_id, tenant_id=tenant_id, site_id=site_id)
    site = SimpleNamespace(id=site_id, tenant_id=tenant_id, project_id=project_id)
    build = SimpleNamespace(id=build_id, status="ready", build_hash=build_hash)
    auth = SimpleNamespace(role="superadmin", tenant_id=tenant_id, user=SimpleNamespace(id=uuid4()))
    release = tmp_path / str(site_id) / "releases" / build_hash
    release.mkdir(parents=True)
    asset = release / "site-panel-leads.js"
    asset.write_text("window.leadFormReady = true;", encoding="utf-8")
    monkeypatch.setattr(projects.settings, "sites_root", str(tmp_path))

    response = asyncio.run(
        projects.preview_project_build(
            project_id,
            build_id,
            "site-panel-leads.js",
            auth,
            PreviewDatabase([project, site, build]),
        )
    )

    assert Path(response.path) == asset
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"

    with pytest.raises(HTTPException, match="Preview page not found") as exc_info:
        asyncio.run(
            projects.preview_project_build(
                project_id,
                build_id,
                "../../outside",
                auth,
                PreviewDatabase([project, site, build]),
            )
        )
    assert exc_info.value.status_code == 404


def test_project_selection_requires_keywords_and_primary_geo():
    project = SimpleNamespace(id=uuid4())
    _, _, blockers = asyncio.run(_selection_snapshots(SelectionDatabase([], []), project))

    assert blockers == [
        "Select at least one project keyword",
        "Select one primary geographic place",
    ]


def test_project_selection_snapshots_operator_choices():
    keyword_id = uuid4()
    geo_id = uuid4()
    keyword = SimpleNamespace(id=keyword_id, phrase="ремонт машин", meta={"intent": "order"})
    place = SimpleNamespace(id=geo_id, name="Казань", kind="city", name_forms={"prep": "Казани"})
    keyword_binding = SimpleNamespace(cluster="repair", intent=None, priority=10)
    geo_binding = SimpleNamespace(role="primary", position=0, morph_overrides={})
    project = SimpleNamespace(id=uuid4())

    keywords, geo, blockers = asyncio.run(
        _selection_snapshots(
            SelectionDatabase([(keyword_binding, keyword)], [(geo_binding, place)]), project
        )
    )

    assert blockers == []
    assert keywords["items"][0]["keyword_id"] == str(keyword_id)
    assert geo["items"][0]["forms"]["prep"] == "Казани"


def test_project_selection_models_reject_duplicates():
    identifier = uuid4()

    with pytest.raises(ValidationError, match="duplicates"):
        ProjectKeywordsUpdate(items=[{"keyword_id": identifier}, {"keyword_id": identifier}])
    with pytest.raises(ValidationError, match="only one primary"):
        ProjectGeoUpdate(
            items=[{"geo_id": uuid4(), "role": "primary"}, {"geo_id": uuid4(), "role": "primary"}]
        )


def test_terminal_lead_outcomes_require_a_reason():
    with pytest.raises(ValidationError, match="Provide a reason"):
        LeadOutcomeIn(outcome="lost")

    assert LeadOutcomeIn(outcome="lost", reason="price").reason == "price"


def test_generation_uses_confirmed_snapshot_only():
    project = SimpleNamespace(id=uuid4(), domain="example.test")
    plan = SimpleNamespace(
        slug="/repair",
        kit_key="service-local-v1",
        version=3,
        keyword_snapshot={"items": []},
        geo_snapshot={
            "items": [
                {
                    "geo_id": str(uuid4()),
                    "name": "Казань",
                    "forms": {"prep": "Казани"},
                    "role": "primary",
                }
            ]
        },
    )
    facts = SimpleNamespace(
        id=uuid4(),
        facts_hash="a" * 64,
        facts={"service": "Ремонт техники", "contacts": {"phone": "+79990000000"}},
    )

    page, snapshot, _ = create_page_draft(project=project, plan=plan, facts=facts)

    assert snapshot["facts"] == facts.facts
    assert page["slug"] == "/repair"
    assert snapshot["generator_meta"]["tokens"] == 0


def test_generation_applies_approved_curated_block_selection() -> None:
    project = SimpleNamespace(id=uuid4(), domain="example.test")
    plan = SimpleNamespace(
        slug="/repair",
        kit_key="service-local-v1",
        version=3,
        block_selection={"blocks": ["hero", "faq"]},
        keyword_snapshot={"items": []},
        geo_snapshot={
            "items": [
                {
                    "geo_id": str(uuid4()),
                    "name": "Казань",
                    "forms": {"prep": "Казани"},
                    "role": "primary",
                }
            ]
        },
    )
    facts = SimpleNamespace(
        id=uuid4(),
        facts_hash="b" * 64,
        facts={"service": "Ремонт техники", "contacts": {"phone": "+79990000000"}},
    )

    page, _, _ = create_page_draft(project=project, plan=plan, facts=facts)

    assert [block["type"] for block in page["blocks"]] == ["hero", "faq"]
    assert [block["order"] for block in page["blocks"]] == [0, 1]


def test_generation_rejects_unknown_curated_block_selection() -> None:
    project = SimpleNamespace(id=uuid4(), domain="example.test")
    plan = SimpleNamespace(
        slug="/repair",
        kit_key="service-local-v1",
        version=1,
        block_selection={"blocks": ["not-a-curated-block"]},
        keyword_snapshot={"items": []},
        geo_snapshot={"items": []},
    )
    facts = SimpleNamespace(id=uuid4(), facts_hash="c" * 64, facts={"service": "Ремонт"})

    with pytest.raises(ValueError, match="invalid curated block selection"):
        create_page_draft(project=project, plan=plan, facts=facts)
    page = {
        "slug": "/repair",
        "title_template": "Ремонт",
        "h1_template": "Ремонт",
        "meta_description_template": "Ремонт",
        "service": "Ремонт",
    }

    blocked = run_page_qa(
        page_manifest=page,
        input_snapshot={
            "facts": {},
            "project_domain": "example.test",
            "geo_snapshot": {"items": [{}]},
        },
        existing_texts=[],
    )
    warned = run_page_qa(
        page_manifest=page,
        input_snapshot={
            "facts": {"service": "Ремонт", "contacts": {"phone": "+79990000000"}},
            "project_domain": "example.test",
            "geo_snapshot": {"items": [{}]},
            "keyword_snapshot": {"items": []},
        },
        existing_texts=[],
    )

    assert blocked["verdict"] == "block"
    assert warned["verdict"] == "warn"
