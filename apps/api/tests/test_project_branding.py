from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1 import project_families, projects
from app.api.v1.projects import _serialize_project, update_project_branding
from app.schemas.project_family import ProjectCityCloneCreate
from app.schemas.workflow import ProjectBrandingUpdate
from site_panel_shared.manifests import SiteManifest


class BrandingDatabase:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _project: object) -> None:
        return None


def test_site_manifest_branding_is_optional_and_strict():
    payload = {
        "site_id": uuid4(),
        "tenant_id": uuid4(),
        "domain": "example.test",
        "branding": {
            "logo": {
                "asset_id": uuid4(),
                "stored_sha256": "a" * 64,
                "alt": "Логотип проекта",
            },
            "favicon": {"asset_id": uuid4(), "stored_sha256": "b" * 64},
        },
    }

    manifest = SiteManifest.model_validate(payload)
    assert manifest.branding and manifest.branding.logo
    assert manifest.branding.logo.alt == "Логотип проекта"
    with pytest.raises(ValueError):
        SiteManifest.model_validate(
            {
                **payload,
                "branding": {
                    "favicon": {
                        "asset_id": uuid4(),
                        "stored_sha256": "c" * 64,
                        "url": "/favicon.ico",
                    }
                },
            }
        )


def test_project_serialization_returns_branding_without_storage_paths():
    project = SimpleNamespace(
        id=uuid4(),
        name="Проект",
        slug="project",
        domain="example.test",
        locale="ru",
        niche=None,
        status="draft",
        version=1,
        site_id=None,
        current_fact_revision_id=None,
        domain_check_meta={},
        branding={"favicon": {"asset_id": str(uuid4()), "stored_sha256": "a" * 64}},
        created_at=None,
        updated_at=None,
    )

    payload = _serialize_project(project)

    assert payload["branding"] == project.branding
    assert "path" not in str(payload["branding"])


def test_project_branding_migration_follows_retention_head():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0067_project_branding_assets.py"
    spec = importlib.util.spec_from_file_location("project_branding_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.down_revision == "0066_storage_retention_runs"
    assert module.revision == "0067_project_branding_assets"


def test_branding_patch_preserves_unchanged_role(monkeypatch):
    tenant_id = uuid4()
    logo_id, favicon_id = uuid4(), uuid4()
    project = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        version=7,
        site_id=None,
        branding={
            "favicon": {"asset_id": str(favicon_id), "stored_sha256": "b" * 64},
        },
    )
    auth = SimpleNamespace(user=SimpleNamespace(id=uuid4()))
    db = BrandingDatabase()
    audited = []

    async def project_or_404(_db, _project_id, _auth):
        return project

    async def verified_reference(_db, **kwargs):
        assert kwargs["project"] is project
        assert kwargs["asset_id"] == logo_id
        assert kwargs["expected_sha256"] == "a" * 64

    async def append(_db, **kwargs):
        audited.append(kwargs)

    monkeypatch.setattr(projects, "_project_or_404", project_or_404)
    monkeypatch.setattr(projects, "_verified_branding_reference", verified_reference)
    monkeypatch.setattr(projects, "append_audit", append)

    result = asyncio.run(
        update_project_branding(
            project.id,
            ProjectBrandingUpdate(
                version=7,
                logo={"asset_id": logo_id, "stored_sha256": "a" * 64, "alt": "Логотип"},
            ),
            auth,
            db,
        )
    )

    assert db.commits == 1
    assert project.version == 8
    assert result["logo"]["asset_id"] == str(logo_id)
    assert result["favicon"]["asset_id"] == str(favicon_id)
    assert audited[0]["action"] == "project.branding.update"
    assert audited[0]["payload"]["fields"] == ["logo"]


def test_city_project_copies_branding_by_value(monkeypatch):
    master = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        locale="ru",
        niche="repair",
        branding={"favicon": {"asset_id": str(uuid4()), "stored_sha256": "a" * 64}},
    )
    facts = SimpleNamespace(facts={})
    city = SimpleNamespace(id=uuid4(), kind="city", is_validated=True)
    added = []

    class CityCloneDatabase:
        def __init__(self) -> None:
            self.results = [city, None]

        async def execute(self, _statement):
            value = self.results.pop(0)
            return SimpleNamespace(scalar_one_or_none=lambda: value)

        def add(self, value) -> None:
            added.append(value)

        async def flush(self) -> None:
            for value in added:
                if getattr(value, "id", None) is None:
                    value.id = uuid4()

        async def commit(self) -> None:
            return None

    async def project_or_404(_db, _project_id, _auth):
        return master

    async def confirmed_facts(_db, _master):
        return facts

    async def append(_db, **_kwargs):
        return None

    monkeypatch.setattr(project_families, "_project_or_404", project_or_404)
    monkeypatch.setattr(project_families, "_confirmed_facts", confirmed_facts)
    monkeypatch.setattr(project_families, "append_audit", append)
    monkeypatch.setattr(project_families, "_member_out", lambda *_args: {})

    asyncio.run(
        project_families.create_city_project(
            master.id,
            ProjectCityCloneCreate(
                geo_id=city.id,
                name="Городской проект",
                slug="city-project",
                hostname="city.example.test",
            ),
            SimpleNamespace(user=SimpleNamespace(id=uuid4())),
            CityCloneDatabase(),
        )
    )

    child = next(value for value in added if type(value).__name__ == "Project")
    assert child.branding == master.branding
    assert child.branding is not master.branding
    master.branding["favicon"]["stored_sha256"] = "b" * 64
    assert child.branding["favicon"]["stored_sha256"] == "a" * 64
