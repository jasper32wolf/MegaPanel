from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.api.v1 import projects
from app.api.v1.projects import (
    _draft_manifest_hash,
    _project_site_or_409,
    _public_fact_values,
    _selection_snapshots,
    _serialize_fact,
    _verified_media_hash,
    attach_draft_media,
)
from app.main import app
from app.schemas.workflow import (
    FactRevisionCreate,
    LeadOutcomeIn,
    PageDraftMediaAttachIn,
    PagePlanCreate,
    ProjectGeoUpdate,
    ProjectKeywordsUpdate,
)
from app.services.generation import create_page_draft
from app.services.qa import run_page_qa
from fastapi import HTTPException
from pydantic import ValidationError


def _private_lead_email_migration():
    migration_path = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0022_encrypt_private_lead_email.py"
    )
    spec = importlib.util.spec_from_file_location("private_lead_email_migration", migration_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_fact_revision_normalizes_the_public_business_profile():
    revision = FactRevisionCreate(
        facts={
            "organization": "Тестовая организация",
            "service": "Ремонт",
            "contacts": {"phone": "+79990000000", "address": "Казань"},
            "legal": {"operator": "ООО Тест"},
            "company_history": "Проверенная история",
        },
        private_lead_email="leads@example.com",
    )

    assert revision.facts["legal"] == {"org": "ООО Тест"}
    assert revision.facts["contacts"] == {"phone": "+79990000000", "address": "Казань"}
    assert str(revision.private_lead_email) == "leads@example.com"


@pytest.mark.parametrize("private_field", ["email", "private_lead_email"])
def test_fact_revision_rejects_private_email_in_public_contacts(private_field: str):
    with pytest.raises(ValidationError, match="private lead email"):
        FactRevisionCreate(
            facts={"service": "Ремонт", "contacts": {private_field: "leads@example.com"}}
        )


def test_fact_serialization_only_exposes_private_email_presence():
    revision = SimpleNamespace(
        id=uuid4(),
        version=1,
        state="draft",
        facts={"service": "Ремонт", "contacts": {"phone": "+79990000000"}},
        private_lead_email_enc="ciphertext",
        source_notes=None,
        facts_hash="a" * 64,
        supersedes_id=None,
        confirmed_at=None,
        created_at=None,
    )

    payload = _serialize_fact(revision)

    assert payload["has_private_lead_email"] is True
    assert "private_lead_email" not in payload["facts"]
    assert "ciphertext" not in str(payload)


def test_private_lead_email_migration_adds_only_the_encrypted_column(
    monkeypatch: pytest.MonkeyPatch,
):
    migration = _private_lead_email_migration()
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def add_column(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(migration.op, "add_column", add_column)

    migration.upgrade()

    assert migration.down_revision == "0021_encrypt_legacy_webhook_secrets"
    assert len(calls) == 1
    args, _ = calls[0]
    assert args[0] == "project_fact_revisions"
    assert args[1].name == "private_lead_email_enc"
    assert args[1].nullable is True


def test_project_fact_revisions_route_is_registered():
    paths = app.openapi()["paths"]

    assert "get" in paths["/api/v1/projects/{project_id}/facts"]
    assert "post" in paths["/api/v1/projects/{project_id}/commercial-page-plans"]
    assert "post" in paths["/api/v1/projects/{project_id}/page-drafts/{draft_id}/media"]


def test_media_attachment_hash_requires_confirmed_rights_and_matching_sha():
    asset = SimpleNamespace(
        meta={
            "provenance": {"kind": "manual_upload", "rights_confirmed": True},
            "hashes": {"stored_sha256": "a" * 64},
        }
    )

    assert _verified_media_hash(asset) == "a" * 64
    with pytest.raises(ValueError, match="rights"):
        _verified_media_hash(SimpleNamespace(meta={"provenance": {}, "hashes": {}}))


def test_media_attachment_changes_canonical_draft_hash():
    base = {
        "slug": "/",
        "title_template": "Ремонт",
        "h1_template": "Ремонт",
        "service": "Ремонт",
    }
    attached = {
        **base,
        "media": [{"asset_id": str(uuid4()), "stored_sha256": "a" * 64, "alt": "Фото"}],
    }

    assert _draft_manifest_hash(base) != _draft_manifest_hash(attached)


def test_draft_media_attachment_snapshots_hash_and_resets_qa(monkeypatch):
    tenant_id, project_id, draft_id, asset_id = (uuid4() for _ in range(4))
    project = SimpleNamespace(id=project_id, tenant_id=tenant_id)
    draft = SimpleNamespace(
        id=draft_id,
        project_id=project_id,
        state="draft",
        page_manifest={
            "slug": "/",
            "title_template": "Ремонт",
            "h1_template": "Ремонт",
            "service": "Ремонт",
        },
        qa_runs=[{"source_hash": "old"}],
        last_qa_verdict="pass",
        qa_override={"reason": "old"},
        content_hash="old",
        page_plan_id=uuid4(),
        revision=1,
        generator_meta={},
        failure_message=None,
        created_at=None,
        updated_at=None,
    )
    asset = SimpleNamespace(
        id=asset_id,
        tenant_id=tenant_id,
        meta={
            "provenance": {"kind": "manual_upload", "rights_confirmed": True},
            "hashes": {"stored_sha256": "a" * 64},
        },
    )

    class Session:
        def __init__(self):
            self.calls = 0
            self.committed = False

        async def execute(self, _statement):
            self.calls += 1
            value = draft if self.calls == 1 else asset
            return SimpleNamespace(scalar_one_or_none=lambda: value)

        async def commit(self):
            self.committed = True

    db = Session()
    monkeypatch.setattr(projects, "_project_or_404", AsyncMock(return_value=project))
    monkeypatch.setattr(projects, "_asset_path", lambda _: Path("asset.webp"))
    monkeypatch.setattr(projects, "append_audit", AsyncMock())
    auth = SimpleNamespace(tenant_id=tenant_id, user=SimpleNamespace(id=uuid4()))

    result = asyncio.run(
        attach_draft_media(
            project_id,
            draft_id,
            PageDraftMediaAttachIn(asset_id=asset_id, alt="Проверенное фото"),
            auth,
            db,
        )
    )

    assert result["page_manifest"]["media"] == [
        {"asset_id": str(asset_id), "stored_sha256": "a" * 64, "alt": "Проверенное фото"}
    ]
    assert draft.qa_runs == []
    assert draft.last_qa_verdict is None
    assert draft.qa_override == {}
    assert draft.content_hash != "old"
    assert db.committed


def test_draft_media_attachment_rejects_non_draft(monkeypatch):
    project_id, tenant_id = uuid4(), uuid4()
    project = SimpleNamespace(id=project_id, tenant_id=tenant_id)
    draft = SimpleNamespace(state="review")

    class Session:
        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: draft)

    monkeypatch.setattr(projects, "_project_or_404", AsyncMock(return_value=project))
    auth = SimpleNamespace(tenant_id=tenant_id, user=SimpleNamespace(id=uuid4()))
    with pytest.raises(HTTPException, match="before submitting") as exc_info:
        asyncio.run(
            attach_draft_media(
                project_id,
                uuid4(),
                PageDraftMediaAttachIn(asset_id=uuid4(), alt="Фото"),
                auth,
                Session(),
            )
        )

    assert exc_info.value.status_code == 409


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


def test_commercial_generation_uses_only_the_bound_confirmed_fact() -> None:
    project = SimpleNamespace(id=uuid4(), domain="example.test")
    plan = SimpleNamespace(
        slug="/mission",
        kit_key="service-local-v1",
        version=1,
        source_refs={"commercial_fact_key": "mission"},
        block_selection={"blocks": ["hero"]},
        keyword_snapshot={"items": []},
        geo_snapshot={"items": [{"geo_id": str(uuid4()), "name": "Казань", "role": "primary"}]},
    )
    facts = SimpleNamespace(
        id=uuid4(),
        facts_hash="d" * 64,
        facts={
            "service": "Ремонт техники",
            "mission": "Помогать клиентам с подтверждённой услугой ремонта.",
            "contacts": {"phone": "+79990000000"},
        },
    )

    page, snapshot, content = create_page_draft(project=project, plan=plan, facts=facts)

    assert page["h1_template"] == "Миссия компании"
    assert page["unique_core"] == facts.facts["mission"]
    assert page["meta_description_template"] == facts.facts["mission"]
    assert snapshot["commercial_fact_key"] == "mission"
    assert "Миссия компании" in content


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


def test_fact_revision_rejects_top_level_private_lead_email():
    with pytest.raises(ValidationError, match="dedicated field"):
        FactRevisionCreate(facts={"service": "Ремонт", "private_lead_email": "leads@example.test"})
