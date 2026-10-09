from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from app.schemas.author import AuthorProfileIn, author_profile_hash
from pydantic import ValidationError


def _migration():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0053_author_profile_revisions.py"
    spec = importlib.util.spec_from_file_location("author_profile_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _profile(**overrides: object) -> AuthorProfileIn:
    values: dict[str, object] = {
        "slug": "ivan-petrov",
        "name": "Иван Петров",
        "role": "Руководитель сервисной службы",
        "biography": (
            "Иван отвечает за организацию сервисных работ и проверку качества "
            "выполненных заявок компании."
        ),
        "expertise": ["Диагностика оборудования", "Контроль качества работ"],
        "evidence": ["Внутренний приказ о назначении", "Проверяемый опыт работы"],
        "portrait_asset_id": uuid4(),
    }
    values.update(overrides)
    return AuthorProfileIn(**values)


def test_author_profile_is_typed_hashed_and_rejects_generated_markup():
    profile = _profile()

    assert author_profile_hash(profile) == author_profile_hash(profile.model_dump(mode="json"))
    with pytest.raises(ValidationError, match="markup"):
        _profile(biography="<script>invented expertise</script>" + "x" * 40)
    with pytest.raises(ValidationError, match="duplicates"):
        _profile(expertise=["Диагностика", "Диагностика"])
    with pytest.raises(ValidationError, match="slug"):
        _profile(slug="Иван Петров")


def test_author_profile_migration_has_rls_and_immutable_lifecycle_contract():
    migration = _migration()
    source = Path(migration.__file__).read_text(encoding="utf-8")

    assert migration.down_revision == "0052_secure_first_party_telemetry"
    assert '"author_profile_revisions"' in source
    assert "portrait_asset_id" in source
    assert "profile_hash" in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "draft', 'review', 'approved', 'rejected" in source


def test_page_draft_author_binding_is_explicit_and_migration_ordered():
    api_dir = Path(__file__).parents[1]
    migration_path = api_dir / "alembic" / "versions" / "0054_page_draft_author_binding.py"
    source = migration_path.read_text(encoding="utf-8")
    projects_source = (api_dir / "app" / "api" / "v1" / "projects.py").read_text(encoding="utf-8")
    manifests_source = (
        Path(__file__).parents[3]
        / "packages"
        / "shared"
        / "src"
        / "site_panel_shared"
        / "manifests.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision: str | None = "0053_author_profile_revisions"' in source
    assert "author_profile_revision_id" in source
    assert "author_profile_revisions.id" in source
    assert "class AuthorProfileSnapshot" in manifests_source
    assert "author: AuthorProfileSnapshot | None = None" in manifests_source
    assert '"/{project_id}/page-drafts/{draft_id}/author"' in projects_source
    assert "_approved_author_snapshot_or_409" in projects_source
    assert "draft.qa_runs = []" in projects_source
    assert "_manifest_author_compliance_blockers" in projects_source


def test_author_routes_keep_review_and_media_provenance_boundaries():
    from app.main import app

    paths = app.openapi()["paths"]
    base = "/api/v1/projects/{project_id}/authors"
    assert "get" in paths[base]
    assert "post" in paths[base]
    assert "post" in paths[f"{base}/{{revision_id}}/submit-review"]
    assert "post" in paths[f"{base}/{{revision_id}}/approve"]
    assert "post" in paths[f"{base}/{{revision_id}}/reject"]

    source = (Path(__file__).parents[1] / "app" / "api" / "v1" / "authors.py").read_text(
        encoding="utf-8"
    )
    assert "_approved_portrait_or_409" in source
    assert "ensure_media_review_allows_use" in source
    assert 'state != "review"' in source
    assert "SiteBuild" not in source
    assert "publish" not in source.lower()
