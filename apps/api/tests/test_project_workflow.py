from __future__ import annotations

import asyncio
import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.api.v1 import projects
from app.api.v1.projects import (
    _asset_usage_status,
    _build_assets_for_manifest,
    _candidate_index_states,
    _current_qa_run,
    _draft_manifest_hash,
    _lead_routing_publish_blockers,
    _manifest_asset_usage,
    _project_site_or_409,
    _public_fact_values,
    _reconcile_site_page_projection,
    _selected_build_projection,
    _selection_snapshots,
    _serialize_fact,
    _verified_media_hash,
    attach_draft_block_media,
    attach_draft_media,
    create_index_promotion,
    materialize_project_build,
    publish_project_build,
    rollback_project_build,
)
from app.main import app
from app.schemas.workflow import (
    BuildPublishRequest,
    BuildRollbackRequest,
    ClaimSlotBinding,
    FactRevisionCreate,
    LeadOutcomeIn,
    PageDraftBlockMediaAttachIn,
    PageDraftMediaAttachIn,
    PageIndexPromotionIn,
    PagePlanCreate,
    ProjectGeoUpdate,
    ProjectKeywordsUpdate,
)
from app.services.claim_slots import resolve_claim_slot_bindings
from app.services.generation import create_page_draft
from app.services.qa import run_page_qa
from fastapi import HTTPException
from pydantic import ValidationError
from site_panel_shared.manifests import PageManifest, SiteManifest


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


class BuildWorkflowDatabase:
    def __init__(self, results: list[object]):
        self.results = results
        self.added: list[object] = []
        self.committed = False

    async def execute(self, _: object) -> object:
        value = self.results.pop(0)
        if isinstance(value, list):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: value))
        return SimpleNamespace(scalar_one_or_none=lambda: value)

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        for value in self.added:
            if getattr(value, "id", "set") is None:
                value.id = uuid4()

    async def get(self, _model, _key):
        return None

    async def commit(self) -> None:
        self.committed = True


def _build_manifest_snapshot(site_id, tenant_id, pages: list[dict]) -> dict:
    return SiteManifest.model_validate(
        {
            "site_id": site_id,
            "tenant_id": tenant_id,
            "domain": "example.test",
            "pages": pages,
        }
    ).model_dump(mode="json")


def _page_manifest(slug: str, title: str) -> dict:
    return PageManifest(
        slug=slug,
        title_template=title,
        h1_template=title,
        service="Ремонт",
    ).model_dump(mode="json")


def _page_metadata(slug: str, *, index_state: str = "indexed", thin: bool = False) -> dict:
    path = "/" if slug == "/" else f"/{slug.strip('/')}/"
    return {
        "slug": slug,
        "path": path,
        "index_state": index_state,
        "thin": thin,
        "content_chars": 240,
        "hash": "a" * 64,
    }


def test_asset_usage_projection_is_snapshot_derived_and_checks_current_hash():
    tenant_id, site_id, asset_id = uuid4(), uuid4(), uuid4()
    manifest = SiteManifest.model_validate(
        {
            "site_id": site_id,
            "tenant_id": tenant_id,
            "domain": "example.test",
            "pages": [
                {
                    **_page_manifest("/", "Страница с media"),
                    "media": [
                        {
                            "asset_id": asset_id,
                            "stored_sha256": "a" * 64,
                            "alt": "Изображение услуги",
                        }
                    ],
                }
            ],
        }
    )

    usage = _manifest_asset_usage(
        manifest=manifest,
        scope="candidate",
        source={"build_id": "candidate-id", "build_hash": "b" * 64},
    )

    assert usage == [
        {
            "scope": "candidate",
            "source": {"build_id": "candidate-id", "build_hash": "b" * 64},
            "slug": "/",
            "placement": "gallery",
            "asset_id": str(asset_id),
            "expected_sha256": "a" * 64,
            "alt": "Изображение услуги",
        }
    ]
    assert _asset_usage_status(None, "a" * 64) == "missing_asset"
    asset = SimpleNamespace(
        meta={
            "provenance": {"kind": "manual_upload", "rights_confirmed": True},
            "hashes": {"stored_sha256": "b" * 64},
        }
    )
    assert _asset_usage_status(asset, "a" * 64) == "hash_mismatch"


def test_project_asset_usage_includes_historical_build_snapshots(monkeypatch):
    tenant_id, project_id, site_id, asset_id = (uuid4() for _ in range(4))
    project = SimpleNamespace(
        id=project_id, tenant_id=tenant_id, site_id=site_id, domain="example.test"
    )
    site = SimpleNamespace(id=site_id, build_hash="active-hash")
    manifest = SiteManifest.model_validate(
        {
            "site_id": site_id,
            "tenant_id": tenant_id,
            "domain": "example.test",
            "pages": [
                {
                    **_page_manifest("/media", "Историческое медиа"),
                    "media": [{"asset_id": asset_id, "stored_sha256": "a" * 64, "alt": "Фото"}],
                }
            ],
        }
    ).model_dump(mode="json")
    builds = [
        SimpleNamespace(
            id=uuid4(),
            status="ready",
            build_hash="candidate-hash",
            manifest_snapshot=manifest,
        ),
        SimpleNamespace(
            id=uuid4(), status="published", build_hash="active-hash", manifest_snapshot=manifest
        ),
        SimpleNamespace(
            id=uuid4(), status="published", build_hash="older-hash", manifest_snapshot=manifest
        ),
        SimpleNamespace(
            id=uuid4(), status="rolled_back", build_hash="rollback-hash", manifest_snapshot=manifest
        ),
    ]

    class Session:
        def __init__(self):
            self.results = [[], builds, builds[1], []]

        async def execute(self, _statement):
            result = self.results.pop(0)
            if isinstance(result, list):
                return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: result))
            return SimpleNamespace(scalar_one_or_none=lambda: result)

    monkeypatch.setattr(projects, "_project_or_404", AsyncMock(return_value=project))
    monkeypatch.setattr(projects, "_project_site_or_409", AsyncMock(return_value=site))
    auth = SimpleNamespace(tenant_id=tenant_id, role="tenant_admin")

    usages = asyncio.run(projects.list_project_asset_usage(project_id, auth, Session()))

    by_build = {row["source"]["build_id"]: row for row in usages}
    assert len(usages) == 4
    assert by_build[str(builds[0].id)]["scope"] == "candidate"
    assert by_build[str(builds[1].id)]["scope"] == "published"
    assert by_build[str(builds[2].id)]["scope"] == "historical"
    assert by_build[str(builds[3].id)]["scope"] == "historical"
    assert by_build[str(builds[2].id)]["source"]["build_hash"] == "older-hash"
    assert all(row["current_status"] == "missing_asset" for row in usages)


def test_materializing_candidate_does_not_mutate_active_site_page_projection(monkeypatch):
    tenant_id, project_id, site_id = uuid4(), uuid4(), uuid4()
    active_page = SimpleNamespace(
        slug="/",
        manifest=_page_manifest("/", "Активная страница"),
        project_id=uuid4(),
        page_plan_id=uuid4(),
        page_draft_id=uuid4(),
        content_chars=999,
        thin=False,
        index_state="indexed",
        publish_state="published",
    )
    snapshot = _build_manifest_snapshot(
        site_id,
        tenant_id,
        [_page_manifest("/", "Candidate"), _page_manifest("/new", "Новая страница")],
    )
    project = SimpleNamespace(
        id=project_id, tenant_id=tenant_id, site_id=site_id, domain="example.test"
    )
    site = SimpleNamespace(
        id=site_id,
        tenant_id=tenant_id,
        manifest=snapshot,
        lead_token="lead-token",
        build_hash="b" * 64,
    )
    result = {
        "build_hash": "c" * 64,
        "pages": [
            _page_metadata("/", index_state="noindex"),
            _page_metadata("/new", index_state="noindex"),
        ],
        "indexed_count": 2,
    }

    class Builder:
        calls: list[dict] = []

        def __init__(self, _root):
            pass

        def build(self, *args, **kwargs):
            self.calls.append(kwargs)
            return result

    db = BuildWorkflowDatabase([[active_page], [], []])
    monkeypatch.setattr(projects, "_project_or_404", AsyncMock(return_value=project))
    monkeypatch.setattr(projects, "_project_site_or_409", AsyncMock(return_value=site))
    monkeypatch.setattr(projects, "SiteBuilder", Builder)
    monkeypatch.setattr(projects, "append_site_build_event", AsyncMock())
    monkeypatch.setattr(projects, "enqueue_site_build", AsyncMock())
    monkeypatch.setattr(projects, "append_audit", AsyncMock())
    auth = SimpleNamespace(user=SimpleNamespace(id=uuid4()))
    before = {
        name: getattr(active_page, name)
        for name in (
            "manifest",
            "project_id",
            "page_plan_id",
            "page_draft_id",
            "content_chars",
            "thin",
            "index_state",
            "publish_state",
        )
    }

    response = asyncio.run(materialize_project_build(project_id, auth, db))

    assert response["activated"] is False
    assert response["published"] is False
    assert response["status"] == "queued"
    assert Builder.calls == []
    assert {type(item).__name__ for item in db.added} == {"SiteBuild"}
    build = db.added[0]
    assert build.manifest_snapshot == snapshot
    assert build.input_snapshot["manifest"] == snapshot
    assert build.page_metadata_snapshot is None
    assert build.build_hash is None
    assert {name: getattr(active_page, name) for name in before} == before
    assert db.committed is True


def test_explicit_index_promotion_requires_passing_current_qa(monkeypatch):
    tenant_id, project_id, site_id, draft_id = (uuid4() for _ in range(4))
    page = _page_manifest("/", "Страница для индексации")
    source_hash = _draft_manifest_hash(page)
    project = SimpleNamespace(id=project_id, tenant_id=tenant_id, site_id=site_id)
    site = SimpleNamespace(
        id=site_id,
        tenant_id=tenant_id,
        manifest=_build_manifest_snapshot(site_id, tenant_id, [page]),
    )
    draft = SimpleNamespace(
        id=draft_id,
        state="applied",
        content_hash=source_hash,
        page_manifest=page,
        qa_runs=[{"source_hash": source_hash, "verdict": "pass"}],
        last_qa_verdict="pass",
    )
    db = BuildWorkflowDatabase([[draft]])
    monkeypatch.setattr(projects, "_project_or_404", AsyncMock(return_value=project))
    monkeypatch.setattr(projects, "_project_site_or_409", AsyncMock(return_value=site))
    monkeypatch.setattr(projects, "append_audit", AsyncMock())
    auth = SimpleNamespace(user=SimpleNamespace(id=uuid4()))

    response = asyncio.run(
        create_index_promotion(
            project_id,
            PageIndexPromotionIn(
                slug="/", reason="Проверена готовность страницы к поисковой выдаче", confirmed=True
            ),
            auth,
            db,
        )
    )

    promotion = db.added[0]
    assert promotion.slug == "/"
    assert promotion.source_hash == source_hash
    assert promotion.qa_source_hash == source_hash
    assert response["source_hash"] == source_hash
    assert db.committed is True


def test_index_promotion_is_bound_to_the_selected_page_source_hash():
    tenant_id, project_id, site_id = (uuid4() for _ in range(3))
    page = PageManifest.model_validate(_page_manifest("/", "Текущая версия"))
    source_hash = _draft_manifest_hash(page.model_dump(mode="json"))
    promoted_at = datetime.now(UTC)
    promotion = SimpleNamespace(slug="/", source_hash=source_hash, decided_at=promoted_at)
    project = SimpleNamespace(id=project_id)
    site = SimpleNamespace(id=site_id)
    db = BuildWorkflowDatabase([[promotion]])

    index_states, source_hashes, promoted, matched_promotions = asyncio.run(
        _candidate_index_states(
            db,
            project=project,
            site=site,
            manifest=SimpleNamespace(pages=[page]),
            rows=[],
        )
    )

    assert index_states == {"/": "indexed"}
    assert source_hashes == {"/": source_hash}
    assert promoted == {"/": promoted_at}
    assert matched_promotions == {"/": promotion}


def test_materialized_build_snapshots_only_matched_index_promotion_evidence(monkeypatch):
    tenant_id, project_id, site_id = (uuid4() for _ in range(3))
    page = _page_manifest("/", "Страница для immutable evidence")
    source_hash = _draft_manifest_hash(page)
    decided_at = datetime.now(UTC)
    promotion = SimpleNamespace(
        id=uuid4(),
        slug="/",
        source_hash=source_hash,
        qa_source_hash=source_hash,
        reason="Оператор подтвердил готовность после passing QA",
        decided_at=decided_at,
    )
    project = SimpleNamespace(
        id=project_id, tenant_id=tenant_id, site_id=site_id, domain="example.test"
    )
    site = SimpleNamespace(
        id=site_id,
        tenant_id=tenant_id,
        manifest=_build_manifest_snapshot(site_id, tenant_id, [page]),
        lead_token="lead-token",
        build_hash=None,
    )

    class Builder:
        def __init__(self, _root):
            pass

        def build(self, *args, **kwargs):
            return {
                "build_hash": "b" * 64,
                "pages": [_page_metadata("/")],
                "indexed_count": 1,
            }

    db = BuildWorkflowDatabase([[], [promotion], []])
    monkeypatch.setattr(projects, "_project_or_404", AsyncMock(return_value=project))
    monkeypatch.setattr(projects, "_project_site_or_409", AsyncMock(return_value=site))
    monkeypatch.setattr(projects, "SiteBuilder", Builder)
    monkeypatch.setattr(projects, "append_site_build_event", AsyncMock())
    monkeypatch.setattr(projects, "enqueue_site_build", AsyncMock())
    monkeypatch.setattr(projects, "append_audit", AsyncMock())

    asyncio.run(
        materialize_project_build(project_id, SimpleNamespace(user=SimpleNamespace(id=uuid4())), db)
    )

    build = next(item for item in db.added if type(item).__name__ == "SiteBuild")
    assert build.input_snapshot["index_promotions"]["/"] == {
        "id": str(promotion.id),
        "slug": "/",
        "source_hash": source_hash,
        "qa_source_hash": source_hash,
        "reason": promotion.reason,
        "decided_at": decided_at.isoformat(),
    }
    assert build.input_snapshot["promoted_at"]["/"] == decided_at.isoformat()


def test_list_builds_projects_only_immutable_index_promotion_snapshot(monkeypatch):
    tenant_id, project_id, site_id, build_id = (uuid4() for _ in range(4))
    decided_at = datetime.now(UTC).isoformat()
    source_hash = "a" * 64
    metadata = [
        {
            **_page_metadata("/"),
            "source_hash": source_hash,
            "promoted_at": decided_at,
            "index_promotion": {
                "id": str(uuid4()),
                "slug": "/",
                "source_hash": source_hash,
                "qa_source_hash": source_hash,
                "reason": "Подтверждено для выдачи после проверки",
                "decided_at": decided_at,
            },
        }
    ]
    build = SimpleNamespace(
        id=build_id,
        status="ready",
        build_hash="b" * 64,
        previous_build_hash=None,
        pages_built=1,
        created_at=datetime.fromisoformat(decided_at),
        activated_at=None,
        manifest_snapshot={},
        legal_review={},
        page_metadata_snapshot=metadata,
    )
    project = SimpleNamespace(id=project_id, tenant_id=tenant_id, site_id=site_id)
    db = BuildWorkflowDatabase([[build], [], [], []])
    monkeypatch.setattr(projects, "_project_or_404", AsyncMock(return_value=project))
    monkeypatch.setattr(
        projects, "_project_site_or_409", AsyncMock(return_value=SimpleNamespace(build_hash=None))
    )

    response = asyncio.run(projects.list_project_builds(project_id, SimpleNamespace(), db))

    assert response[0]["index_promotion_provenance"] == [
        {
            "slug": "/",
            "reason": "Подтверждено для выдачи после проверки",
            "decided_at": decided_at,
        }
    ]
    assert build.page_metadata_snapshot is metadata
    assert db.committed is False
    assert len(db.results) == 0


def test_page_metadata_snapshot_accepts_legacy_records_and_validates_promotion_evidence():
    from app.services.site_build_metadata import validate_page_metadata_snapshot

    legacy = _page_metadata("/")
    assert validate_page_metadata_snapshot([legacy]) == {"/": legacy}

    source_hash = "a" * 64
    decided_at = datetime.now(UTC).isoformat()
    promoted = {
        **legacy,
        "source_hash": source_hash,
        "promoted_at": decided_at,
        "index_promotion": {
            "id": str(uuid4()),
            "slug": "/",
            "source_hash": source_hash,
            "qa_source_hash": source_hash,
            "reason": "Проверено оператором после passing QA",
            "decided_at": decided_at,
        },
    }
    assert validate_page_metadata_snapshot([promoted])["/"] == promoted

    promoted["index_promotion"]["source_hash"] = "b" * 64
    with pytest.raises(ValueError, match="snapshot is invalid"):
        validate_page_metadata_snapshot([promoted])


def test_selected_build_projection_requires_exact_immutable_metadata_snapshot():
    site_id, tenant_id = uuid4(), uuid4()
    site = SimpleNamespace(id=site_id, tenant_id=tenant_id)
    build = SimpleNamespace(
        manifest_snapshot=_build_manifest_snapshot(
            site_id, tenant_id, [_page_manifest("/", "Страница")]
        ),
        page_metadata_snapshot=[_page_metadata("/other")],
    )

    with pytest.raises(ValueError, match="do not match"):
        _selected_build_projection(build, site)


def test_reconcile_site_page_projection_uses_selected_snapshot_and_archives_omitted_pages():
    site_id, tenant_id, project_id = uuid4(), uuid4(), uuid4()
    selected = SimpleNamespace(
        slug="/",
        manifest=_page_manifest("/", "Старое содержимое"),
        project_id=project_id,
        page_plan_id=uuid4(),
        page_draft_id=uuid4(),
        content_chars=111,
        thin=False,
        index_state="indexed",
        publish_state="published",
    )
    omitted = SimpleNamespace(
        slug="/removed",
        manifest=_page_manifest("/removed", "История"),
        project_id=project_id,
        page_plan_id=uuid4(),
        page_draft_id=uuid4(),
        content_chars=222,
        thin=False,
        index_state="indexed",
        publish_state="published",
    )
    site = SimpleNamespace(id=site_id, tenant_id=tenant_id)
    project = SimpleNamespace(id=project_id)
    manifest, metadata = _selected_build_projection(
        SimpleNamespace(
            manifest_snapshot=_build_manifest_snapshot(
                site_id,
                tenant_id,
                [_page_manifest("/", "Выбранная сборка"), _page_manifest("/new", "Новая")],
            ),
            page_metadata_snapshot=[
                _page_metadata("/"),
                _page_metadata("/new", thin=True, index_state="noindex"),
            ],
        ),
        site,
    )
    db = BuildWorkflowDatabase([[selected, omitted]])

    asyncio.run(
        _reconcile_site_page_projection(
            db,
            site=site,
            project=project,
            manifest=manifest,
            metadata_by_slug=metadata,
        )
    )

    assert selected.manifest == _page_manifest("/", "Выбранная сборка")
    assert selected.publish_state == "published"
    assert selected.index_state == "indexed"
    assert selected.content_chars == 240
    assert selected.page_plan_id is None
    assert selected.page_draft_id is None
    assert omitted.manifest == _page_manifest("/removed", "История")
    assert omitted.page_plan_id is not None
    assert omitted.page_draft_id is not None
    assert (omitted.publish_state, omitted.index_state) == ("archived", "noindex")
    new_page = db.added[0]
    assert (new_page.slug, new_page.publish_state, new_page.index_state, new_page.thin) == (
        "/new",
        "published",
        "noindex",
        True,
    )


def test_publish_projects_selected_snapshot_and_archives_omitted_pages(monkeypatch):
    tenant_id, project_id, site_id, build_id = (uuid4() for _ in range(4))
    selected_snapshot = _build_manifest_snapshot(
        site_id,
        tenant_id,
        [_page_manifest("/", "Снимок candidate"), _page_manifest("/new", "Новая")],
    )
    selected_snapshot["legal"] = {
        "org": "ООО Тест",
        "address": "Казань",
        "jurisdiction": "Российская Федерация",
        "privacy_email": "privacy@example.com",
    }
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        site_id=site_id,
        domain="example.test",
        domain_check_meta={"dns_status": "ok"},
    )
    site = SimpleNamespace(
        id=site_id,
        tenant_id=tenant_id,
        domain="example.test",
        build_hash="a" * 64,
        previous_build_hash=None,
        publish_state="published",
        manifest=_build_manifest_snapshot(
            site_id, tenant_id, [_page_manifest("/", "Позднее изменение")]
        ),
    )
    build = SimpleNamespace(
        id=build_id,
        project_id=project_id,
        tenant_id=tenant_id,
        site_id=site_id,
        status="ready",
        build_hash="b" * 64,
        manifest_snapshot=selected_snapshot,
        page_metadata_snapshot=[
            _page_metadata("/"),
            _page_metadata("/new", thin=True, index_state="noindex"),
        ],
        activated_at=None,
    )
    selected = SimpleNamespace(
        slug="/",
        manifest=_page_manifest("/", "Активное содержимое"),
        project_id=project_id,
        page_plan_id=uuid4(),
        page_draft_id=uuid4(),
        content_chars=99,
        thin=False,
        index_state="queued",
        publish_state="published",
    )
    omitted = SimpleNamespace(
        slug="/removed",
        manifest=_page_manifest("/removed", "Историческая страница"),
        project_id=project_id,
        page_plan_id=uuid4(),
        page_draft_id=uuid4(),
        content_chars=120,
        thin=False,
        index_state="indexed",
        publish_state="published",
    )

    class Builder:
        activations: list[tuple[str, str]] = []

        def __init__(self, _root):
            pass

        def activate(self, site_key: str, build_hash: str) -> bool:
            self.activations.append((site_key, build_hash))
            return True

    class Caddy:
        async def upsert_site_vhost(self, *_args):
            return {"ok": True}

    db = BuildWorkflowDatabase([build, [selected, omitted]])
    monkeypatch.setattr(projects, "_project_or_404", AsyncMock(return_value=project))
    monkeypatch.setattr(projects, "_project_site_or_409", AsyncMock(return_value=site))
    monkeypatch.setattr(projects, "SiteBuilder", Builder)
    monkeypatch.setattr(projects, "CaddyClient", Caddy)
    monkeypatch.setattr(projects, "append_audit", AsyncMock())
    auth = SimpleNamespace(user=SimpleNamespace(id=uuid4()))

    response = asyncio.run(
        publish_project_build(project_id, build_id, BuildPublishRequest(confirmed=True), auth, db)
    )

    assert response["published"] is True
    assert Builder.activations == [(str(site_id), "b" * 64)]
    assert (site.previous_build_hash, site.build_hash, site.publish_state) == (
        "a" * 64,
        "b" * 64,
        "published",
    )
    assert site.manifest["pages"] == [_page_manifest("/", "Позднее изменение")]
    assert selected.manifest == _page_manifest("/", "Снимок candidate")
    assert (selected.index_state, selected.content_chars, selected.publish_state) == (
        "indexed",
        240,
        "published",
    )
    assert (omitted.publish_state, omitted.index_state) == ("archived", "noindex")
    new_page = db.added[0]
    assert (new_page.slug, new_page.manifest, new_page.thin) == (
        "/new",
        _page_manifest("/new", "Новая"),
        True,
    )
    assert db.committed is True


def test_rollback_restores_target_snapshot_and_archives_newer_only_pages(monkeypatch):
    tenant_id, project_id, site_id = (uuid4() for _ in range(3))
    target_snapshot = _build_manifest_snapshot(
        site_id,
        tenant_id,
        [_page_manifest("/", "Старая страница"), _page_manifest("/old", "Старый путь")],
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        site_id=site_id,
        domain="example.test",
        domain_check_meta={"dns_status": "ok"},
    )
    site = SimpleNamespace(
        id=site_id,
        tenant_id=tenant_id,
        domain="example.test",
        build_hash="b" * 64,
        previous_build_hash="a" * 64,
        publish_state="published",
    )
    target = SimpleNamespace(
        project_id=project_id,
        tenant_id=tenant_id,
        site_id=site_id,
        status="ready",
        build_hash="a" * 64,
        manifest_snapshot=target_snapshot,
        page_metadata_snapshot=[_page_metadata("/"), _page_metadata("/old")],
        first_published_at=datetime.now(UTC),
        activated_at=None,
    )
    current = SimpleNamespace(
        slug="/",
        manifest=_page_manifest("/", "Новая страница"),
        project_id=project_id,
        page_plan_id=uuid4(),
        page_draft_id=uuid4(),
        content_chars=500,
        thin=False,
        index_state="indexed",
        publish_state="published",
    )
    newer_only = SimpleNamespace(
        slug="/new",
        manifest=_page_manifest("/new", "Новый путь"),
        project_id=project_id,
        page_plan_id=uuid4(),
        page_draft_id=uuid4(),
        content_chars=500,
        thin=False,
        index_state="indexed",
        publish_state="published",
    )

    class Builder:
        activations: list[tuple[str, str]] = []

        def __init__(self, _root):
            pass

        def activate(self, site_key: str, build_hash: str) -> bool:
            self.activations.append((site_key, build_hash))
            return True

    class Caddy:
        async def upsert_site_vhost(self, *_args):
            return {"ok": True}

    db = BuildWorkflowDatabase([target, [current, newer_only]])
    monkeypatch.setattr(projects, "_project_or_404", AsyncMock(return_value=project))
    monkeypatch.setattr(projects, "_project_site_or_409", AsyncMock(return_value=site))
    monkeypatch.setattr(projects, "SiteBuilder", Builder)
    monkeypatch.setattr(projects, "CaddyClient", Caddy)
    monkeypatch.setattr(projects, "evaluate_build_release_gate", lambda *_args: {"blockers": []})
    monkeypatch.setattr(projects, "legal_review_status", lambda _build: {"blockers": []})
    monkeypatch.setattr(projects, "_lead_routing_publish_blockers", AsyncMock(return_value=[]))
    monkeypatch.setattr(projects, "append_audit", AsyncMock())
    auth = SimpleNamespace(user=SimpleNamespace(id=uuid4()))

    response = asyncio.run(
        rollback_project_build(
            project_id,
            BuildRollbackRequest(
                build_hash="a" * 64, confirmation_text=f"ROLLBACK {'a' * 64}"
            ),
            auth,
            db,
        )
    )

    assert response["build_hash"] == "a" * 64
    assert Builder.activations == [(str(site_id), "a" * 64)]
    assert (site.previous_build_hash, site.build_hash, site.publish_state) == (
        "b" * 64,
        "a" * 64,
        "published",
    )
    assert current.manifest == _page_manifest("/", "Старая страница")
    assert (current.publish_state, current.index_state, current.content_chars) == (
        "published",
        "indexed",
        240,
    )
    assert (newer_only.publish_state, newer_only.index_state) == ("archived", "noindex")
    old_page = db.added[0]
    assert (old_page.slug, old_page.publish_state, old_page.index_state) == (
        "/old",
        "published",
        "indexed",
    )
    assert target.status == "ready"
    assert target.activated_at is not None
    assert db.committed is True


def test_publish_blocks_build_without_page_metadata_before_activation(monkeypatch):
    tenant_id, project_id, site_id, build_id = (uuid4() for _ in range(4))
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        site_id=site_id,
        domain="example.test",
        domain_check_meta={"dns_status": "ok"},
    )
    site = SimpleNamespace(id=site_id, tenant_id=tenant_id, domain="example.test")
    build = SimpleNamespace(
        id=build_id,
        project_id=project_id,
        tenant_id=tenant_id,
        site_id=site_id,
        status="ready",
        build_hash="a" * 64,
        manifest_snapshot=_build_manifest_snapshot(
            site_id, tenant_id, [_page_manifest("/", "Страница")]
        ),
        page_metadata_snapshot=None,
    )
    db = BuildWorkflowDatabase([build])
    monkeypatch.setattr(projects, "_project_or_404", AsyncMock(return_value=project))
    monkeypatch.setattr(projects, "_project_site_or_409", AsyncMock(return_value=site))
    monkeypatch.setattr(
        projects,
        "SiteBuilder",
        lambda *_args: pytest.fail("builder must not activate"),
    )

    with pytest.raises(HTTPException, match="immutable page metadata") as exc_info:
        asyncio.run(
            publish_project_build(
                project_id,
                build_id,
                BuildPublishRequest(confirmed=True),
                object(),
                db,
            )
        )

    assert exc_info.value.status_code == 409
    assert db.committed is False


def test_publish_caddy_failure_with_unverified_recovery_has_no_projection_mutation(monkeypatch):
    tenant_id, project_id, site_id, build_id = (uuid4() for _ in range(4))
    snapshot = _build_manifest_snapshot(site_id, tenant_id, [_page_manifest("/", "Candidate")])
    snapshot["legal"] = {
        "org": "ООО Тест",
        "address": "Казань",
        "jurisdiction": "Российская Федерация",
        "privacy_email": "privacy@example.com",
    }
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        site_id=site_id,
        domain="example.test",
        domain_check_meta={"dns_status": "ok"},
    )
    site = SimpleNamespace(
        id=site_id,
        tenant_id=tenant_id,
        domain="example.test",
        build_hash="a" * 64,
        previous_build_hash=None,
        publish_state="published",
    )
    build = SimpleNamespace(
        id=build_id,
        project_id=project_id,
        tenant_id=tenant_id,
        site_id=site_id,
        status="ready",
        build_hash="b" * 64,
        manifest_snapshot=snapshot,
        page_metadata_snapshot=[_page_metadata("/")],
        activated_at=None,
    )

    class Builder:
        activations: list[tuple[str, str]] = []
        restorations: list[tuple[str, str, str | None]] = []

        def __init__(self, _root):
            pass

        def activate(self, site_key: str, build_hash: str) -> bool:
            self.activations.append((site_key, build_hash))
            return True

        def restore_activation(
            self, site_key: str, candidate_hash: str, old_hash: str | None
        ) -> bool:
            self.restorations.append((site_key, candidate_hash, old_hash))
            return False

    class Caddy:
        async def upsert_site_vhost(self, *_args):
            return {"ok": False}

    db = BuildWorkflowDatabase([build])
    audit = AsyncMock()
    monkeypatch.setattr(projects, "_project_or_404", AsyncMock(return_value=project))
    monkeypatch.setattr(projects, "_project_site_or_409", AsyncMock(return_value=site))
    monkeypatch.setattr(projects, "SiteBuilder", Builder)
    monkeypatch.setattr(projects, "CaddyClient", Caddy)
    monkeypatch.setattr(projects, "append_audit", audit)

    with pytest.raises(HTTPException, match="recovery is unverified") as exc_info:
        asyncio.run(
            publish_project_build(
                project_id,
                build_id,
                BuildPublishRequest(confirmed=True),
                object(),
                db,
            )
        )

    assert exc_info.value.status_code == 503
    assert Builder.activations == [(str(site_id), "b" * 64)]
    assert Builder.restorations == [(str(site_id), "b" * 64, "a" * 64)]
    assert (site.build_hash, site.publish_state, build.status, build.activated_at) == (
        "a" * 64,
        "published",
        "ready",
        None,
    )
    assert db.added == []
    assert db.committed is False
    audit.assert_not_awaited()


def test_first_publish_caddy_failure_compensates_without_persistence(monkeypatch):
    tenant_id, project_id, site_id, build_id = (uuid4() for _ in range(4))
    snapshot = _build_manifest_snapshot(site_id, tenant_id, [_page_manifest("/", "Candidate")])
    snapshot["legal"] = {
        "org": "ООО Тест",
        "address": "Казань",
        "jurisdiction": "Российская Федерация",
        "privacy_email": "privacy@example.com",
    }
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        site_id=site_id,
        domain="example.test",
        domain_check_meta={"dns_status": "ok"},
    )
    site = SimpleNamespace(
        id=site_id,
        tenant_id=tenant_id,
        domain="example.test",
        build_hash=None,
        previous_build_hash=None,
        publish_state="draft",
    )
    build = SimpleNamespace(
        id=build_id,
        project_id=project_id,
        tenant_id=tenant_id,
        site_id=site_id,
        status="ready",
        build_hash="b" * 64,
        manifest_snapshot=snapshot,
        page_metadata_snapshot=[_page_metadata("/")],
        activated_at=None,
    )

    class Builder:
        activations: list[tuple[str, str]] = []
        restorations: list[tuple[str, str, str | None]] = []

        def __init__(self, _root):
            pass

        def activate(self, site_key: str, build_hash: str) -> bool:
            self.activations.append((site_key, build_hash))
            return True

        def restore_activation(
            self, site_key: str, candidate_hash: str, old_hash: str | None
        ) -> bool:
            self.restorations.append((site_key, candidate_hash, old_hash))
            return True

    class Caddy:
        async def upsert_site_vhost(self, *_args):
            return {"ok": False}

    db = BuildWorkflowDatabase([build])
    audit = AsyncMock()
    monkeypatch.setattr(projects, "_project_or_404", AsyncMock(return_value=project))
    monkeypatch.setattr(projects, "_project_site_or_409", AsyncMock(return_value=site))
    monkeypatch.setattr(projects, "SiteBuilder", Builder)
    monkeypatch.setattr(projects, "CaddyClient", Caddy)
    monkeypatch.setattr(projects, "append_audit", audit)

    with pytest.raises(HTTPException, match="candidate activation reverted") as exc_info:
        asyncio.run(
            publish_project_build(
                project_id,
                build_id,
                BuildPublishRequest(confirmed=True),
                object(),
                db,
            )
        )

    assert exc_info.value.status_code == 503
    assert Builder.activations == [(str(site_id), "b" * 64)]
    assert Builder.restorations == [(str(site_id), "b" * 64, None)]
    assert (site.build_hash, site.publish_state, build.status, build.activated_at) == (
        None,
        "draft",
        "ready",
        None,
    )
    assert db.added == []
    assert db.committed is False
    audit.assert_not_awaited()


def test_rollback_caddy_failure_restores_release_without_projection_mutation(monkeypatch):
    tenant_id, project_id, site_id = (uuid4() for _ in range(3))
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        site_id=site_id,
        domain="example.test",
        domain_check_meta={"dns_status": "ok"},
    )
    site = SimpleNamespace(
        id=site_id,
        tenant_id=tenant_id,
        domain="example.test",
        build_hash="b" * 64,
        previous_build_hash="a" * 64,
        publish_state="published",
    )
    target = SimpleNamespace(
        project_id=project_id,
        tenant_id=tenant_id,
        site_id=site_id,
        status="ready",
        build_hash="a" * 64,
        manifest_snapshot=_build_manifest_snapshot(
            site_id, tenant_id, [_page_manifest("/", "Старая")]
        ),
        page_metadata_snapshot=[_page_metadata("/")],
        first_published_at=datetime.now(UTC),
        activated_at=None,
    )

    class Builder:
        activations: list[tuple[str, str]] = []
        restorations: list[tuple[str, str, str | None]] = []

        def __init__(self, _root):
            pass

        def activate(self, site_key: str, build_hash: str) -> bool:
            self.activations.append((site_key, build_hash))
            return True

        def restore_activation(
            self, site_key: str, candidate_hash: str, old_hash: str | None
        ) -> bool:
            self.restorations.append((site_key, candidate_hash, old_hash))
            return True

    class Caddy:
        async def upsert_site_vhost(self, *_args):
            return {"ok": False}

    db = BuildWorkflowDatabase([target])
    audit = AsyncMock()
    monkeypatch.setattr(projects, "_project_or_404", AsyncMock(return_value=project))
    monkeypatch.setattr(projects, "_project_site_or_409", AsyncMock(return_value=site))
    monkeypatch.setattr(projects, "SiteBuilder", Builder)
    monkeypatch.setattr(projects, "CaddyClient", Caddy)
    monkeypatch.setattr(projects, "evaluate_build_release_gate", lambda *_args: {"blockers": []})
    monkeypatch.setattr(projects, "legal_review_status", lambda _build: {"blockers": []})
    monkeypatch.setattr(projects, "_lead_routing_publish_blockers", AsyncMock(return_value=[]))
    monkeypatch.setattr(projects, "append_audit", audit)

    with pytest.raises(HTTPException, match="previous release restored") as exc_info:
        asyncio.run(
            rollback_project_build(
                project_id,
                BuildRollbackRequest(
                build_hash="a" * 64, confirmation_text=f"ROLLBACK {'a' * 64}"
            ),
                object(),
                db,
            )
        )

    assert exc_info.value.status_code == 503
    assert Builder.activations == [(str(site_id), "a" * 64)]
    assert Builder.restorations == [(str(site_id), "a" * 64, "b" * 64)]
    assert (site.build_hash, site.publish_state, target.status, target.activated_at) == (
        "b" * 64,
        "published",
        "ready",
        None,
    )
    assert db.added == []
    assert db.committed is False
    audit.assert_not_awaited()


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        ([], []),
        (["review"], ["Activate the reviewed lead routing policy before publish"]),
        (["active"], []),
    ],
)
def test_publish_requires_activation_after_a_routing_policy_exists(states, expected):
    site = SimpleNamespace(id=uuid4(), tenant_id=uuid4(), project_id=uuid4())

    class Database:
        async def execute(self, _statement):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: states))

    assert asyncio.run(_lead_routing_publish_blockers(Database(), site)) == expected


def test_current_qa_run_requires_the_current_content_hash():
    draft = SimpleNamespace(
        content_hash="a" * 64,
        last_qa_verdict="pass",
        qa_runs=[{"source_hash": "a" * 64, "verdict": "pass"}],
    )

    assert _current_qa_run(draft) == {"source_hash": "a" * 64, "verdict": "pass"}
    draft.content_hash = "b" * 64
    assert _current_qa_run(draft) is None


def test_page_metadata_snapshot_migration_follows_current_head():
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0031_site_build_page_metadata_snapshot.py"
    )
    source = migration_path.read_text(encoding="utf-8")

    assert 'down_revision: str | None = "0030_prompt_revision_immutability"' in source
    assert '"page_metadata_snapshot"' in source


def test_project_status_document_names_the_actual_alembic_head():
    api_dir = Path(__file__).parents[1]
    head = ScriptDirectory.from_config(Config(str(api_dir / "alembic.ini"))).get_current_head()
    status_document = (Path(__file__).parents[3] / "docs" / "ХОД-РАБОТ.md").read_text(
        encoding="utf-8"
    )

    assert head is not None
    assert f"`{head}" in status_document


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
    assert "post" in paths["/api/v1/projects/{project_id}/page-drafts/{draft_id}/block-media"]


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
    review_gate = AsyncMock()
    monkeypatch.setattr(projects, "_project_or_404", AsyncMock(return_value=project))
    monkeypatch.setattr(projects, "_asset_path", lambda _: Path("asset.webp"))
    monkeypatch.setattr(projects, "ensure_media_review_allows_use", review_gate)
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
    review_gate.assert_awaited_once_with(
        db,
        tenant_id=tenant_id,
        asset_id=asset_id,
        stored_sha256="a" * 64,
    )
    assert db.committed


def test_draft_block_media_attachment_snapshots_hash_and_resets_qa(monkeypatch):
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
            "blocks": [{"type": "hero", "hash_class": "hero", "html": "<p>Hero</p>"}],
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
    monkeypatch.setattr(projects, "ensure_media_review_allows_use", AsyncMock())
    monkeypatch.setattr(projects, "append_audit", AsyncMock())
    auth = SimpleNamespace(tenant_id=tenant_id, user=SimpleNamespace(id=uuid4()))

    result = asyncio.run(
        attach_draft_block_media(
            project_id,
            draft_id,
            PageDraftBlockMediaAttachIn(
                block_id="hero", asset_id=asset_id, alt="Проверенное фото hero"
            ),
            auth,
            db,
        )
    )

    assert result["page_manifest"]["block_media"] == {
        "hero": {
            "asset_id": str(asset_id),
            "stored_sha256": "a" * 64,
            "alt": "Проверенное фото hero",
        }
    }
    assert draft.qa_runs == []
    assert draft.last_qa_verdict is None
    assert draft.qa_override == {}
    assert draft.content_hash != "old"
    assert db.committed


def test_rejected_media_blocks_new_candidate_build_only(monkeypatch):
    tenant_id, site_id, asset_id = uuid4(), uuid4(), uuid4()
    manifest = SiteManifest.model_validate(
        {
            "site_id": site_id,
            "tenant_id": tenant_id,
            "domain": "example.test",
            "pages": [
                {
                    **_page_manifest("/", "Страница с проверяемым медиа"),
                    "media": [{"asset_id": asset_id, "stored_sha256": "a" * 64, "alt": "Фото"}],
                }
            ],
        }
    )
    asset = SimpleNamespace(
        id=asset_id,
        tenant_id=tenant_id,
        content_type="image/webp",
        meta={
            "provenance": {"kind": "manual_upload", "rights_confirmed": True},
            "hashes": {"stored_sha256": "a" * 64},
        },
    )

    class Session:
        async def execute(self, _statement):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [asset]))

    monkeypatch.setattr(projects, "_asset_path", lambda _: Path("asset.webp"))
    monkeypatch.setattr(
        projects,
        "ensure_media_review_allows_use",
        AsyncMock(side_effect=ValueError("Media asset was rejected for this stored file")),
    )

    with pytest.raises(HTTPException, match="Media asset blocker") as exc_info:
        asyncio.run(_build_assets_for_manifest(Session(), manifest=manifest, tenant_id=tenant_id))
    assert exc_info.value.status_code == 422


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


def test_claim_slot_bindings_are_verbatim_and_target_server_owned_slots():
    facts = {"allowed_claims": ["Письменная гарантия", "Согласуем удобное время"]}
    bindings = resolve_claim_slot_bindings(
        kit_key="service-local-v1",
        block_ids=["hero"],
        block_selection={
            "claim_slot_bindings": [
                {"block_id": "hero", "slot": "unique_core", "claim_index": 0},
                {"block_id": "hero", "slot": "hero_supporting_text", "claim_index": 1},
            ]
        },
        facts=facts,
    )

    assert [binding["claim"] for binding in bindings] == facts["allowed_claims"]
    with pytest.raises(ValueError, match="editable curated text slot"):
        resolve_claim_slot_bindings(
            kit_key="service-local-v1",
            block_ids=["hero"],
            block_selection={
                "claim_slot_bindings": [{"block_id": "hero", "slot": "service", "claim_index": 0}]
            },
            facts=facts,
        )
    with pytest.raises(ValueError, match="unavailable confirmed claim"):
        resolve_claim_slot_bindings(
            kit_key="service-local-v1",
            block_ids=["hero"],
            block_selection={
                "claim_slot_bindings": [
                    {"block_id": "hero", "slot": "unique_core", "claim_index": 2}
                ]
            },
            facts=facts,
        )


def test_claim_binding_request_rejects_duplicate_targets_and_claims():
    with pytest.raises(ValidationError, match="duplicate targets"):
        PagePlanCreate(
            slug="/repair",
            objective="Ремонт",
            kit_key="service-local-v1",
            claim_slot_bindings=[
                ClaimSlotBinding(block_id="hero", slot="unique_core", claim_index=0),
                ClaimSlotBinding(block_id="hero", slot="unique_core", claim_index=1),
            ],
        )
    with pytest.raises(ValidationError, match="duplicate claims"):
        PagePlanCreate(
            slug="/repair",
            objective="Ремонт",
            kit_key="service-local-v1",
            claim_slot_bindings=[
                ClaimSlotBinding(block_id="hero", slot="unique_core", claim_index=0),
                ClaimSlotBinding(block_id="hero", slot="hero_supporting_text", claim_index=0),
            ],
        )


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


def test_generation_copies_bound_claims_exactly_into_curated_slots():
    project = SimpleNamespace(id=uuid4(), domain="example.test")
    plan = SimpleNamespace(
        slug="/repair",
        kit_key="service-local-v1",
        version=1,
        block_selection={
            "blocks": ["hero"],
            "claim_slot_bindings": [
                {"block_id": "hero", "slot": "unique_core", "claim_index": 0},
                {"block_id": "hero", "slot": "hero_supporting_text", "claim_index": 1},
            ],
        },
        keyword_snapshot={"items": []},
        geo_snapshot={"items": [{"geo_id": str(uuid4()), "name": "Казань", "role": "primary"}]},
    )
    facts = SimpleNamespace(
        id=uuid4(),
        facts_hash="c" * 64,
        facts={
            "service": "Ремонт техники",
            "contacts": {"phone": "+79990000000"},
            "allowed_claims": ["Письменная гарантия", "Согласуем удобное время"],
        },
    )

    page, snapshot, content = create_page_draft(project=project, plan=plan, facts=facts)

    assert page["unique_core"] == "Письменная гарантия"
    assert page["block_slot_values"] == {
        "hero": {"hero_supporting_text": "Согласуем удобное время"}
    }
    assert snapshot["claim_slot_bindings"] == [
        {"block_id": "hero", "slot": "unique_core", "claim_index": 0},
        {"block_id": "hero", "slot": "hero_supporting_text", "claim_index": 1},
    ]
    assert "Согласуем удобное время" in content


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
        FactRevisionCreate(facts={"service": "Ремонт", "private_lead_email": "leads@example.com"})


def test_fact_revision_keeps_public_privacy_contact_separate_from_private_lead_email():
    revision = FactRevisionCreate(
        facts={
            "service": "Ремонт",
            "legal": {"org": "ООО Тест", "privacy_email": "privacy@example.com"},
        },
        private_lead_email="leads@example.com",
    )

    assert revision.facts["legal"]["privacy_email"] == "privacy@example.com"
    assert str(revision.private_lead_email) == "leads@example.com"


def test_legal_publish_gate_requires_explicit_public_legal_fields():
    from app.api.v1.projects import _legal_publish_blockers
    from site_panel_shared.manifests import SiteManifest

    manifest = SiteManifest(site_id=uuid4(), tenant_id=uuid4(), domain="example.test")
    assert _legal_publish_blockers(manifest) == [
        "Set the legal organization before publish",
        "Set the legal address before publish",
        "Set the legal jurisdiction before publish",
        "Set a public privacy/DSAR email before publish",
    ]

    manifest.legal = {
        "org": "ООО Тест",
        "address": "Казань, улица Тестовая, 1",
        "jurisdiction": "Российская Федерация",
        "privacy_email": "privacy@example.com",
    }
    assert _legal_publish_blockers(manifest) == []


def test_fact_revision_normalizes_confirmed_allowed_claims():
    revision = FactRevisionCreate(
        facts={
            "service": "Ремонт",
            "allowed_claims": ["  Письменная гарантия на работы  ", "", "Выезд по записи"],
        }
    )

    assert revision.facts["allowed_claims"] == ["Письменная гарантия на работы", "Выезд по записи"]
