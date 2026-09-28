from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from app.main import app
from app.schemas.workflow import (
    SemanticCollectionCreate,
    SemanticCollectionKeywordIn,
    SemanticGeoBindingIn,
    SemanticPlanTargetIn,
)
from pydantic import ValidationError


def _migration():
    path = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0026_project_semantic_collections.py"
    )
    spec = importlib.util.spec_from_file_location("semantic_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_semantic_collection_rejects_duplicate_members_and_geo_bindings():
    keyword_id = uuid4()
    geo_id = uuid4()
    member = SemanticCollectionKeywordIn(
        project_keyword_id=keyword_id,
        geo_bindings=[SemanticGeoBindingIn(project_geo_place_id=geo_id)],
    )
    with pytest.raises(ValidationError, match="duplicate project keywords"):
        SemanticCollectionCreate(name="Основная", members=[member, member])
    with pytest.raises(ValidationError, match="geography contains duplicates"):
        SemanticCollectionKeywordIn(
            project_keyword_id=keyword_id,
            geo_bindings=[
                SemanticGeoBindingIn(project_geo_place_id=geo_id),
                SemanticGeoBindingIn(project_geo_place_id=geo_id),
            ],
        )


def test_semantic_target_rejects_duplicate_collection_members():
    collection_id = uuid4()
    member_id = uuid4()
    with pytest.raises(ValidationError, match="duplicate collection keywords"):
        SemanticPlanTargetIn(
            collection_id=collection_id,
            targets=[
                {"collection_keyword_id": member_id, "geo_binding_ids": [uuid4()]},
                {"collection_keyword_id": member_id, "geo_binding_ids": [uuid4()]},
            ],
        )


def test_semantic_routes_are_registered_and_page_plan_exposes_target_snapshot():
    paths = app.openapi()["paths"]
    assert "post" in paths["/api/v1/projects/{project_id}/semantic-collections"]
    assert "patch" in paths["/api/v1/projects/{project_id}/semantic-collections/{collection_id}"]
    assert (
        "post"
        in paths["/api/v1/projects/{project_id}/semantic-collections/{collection_id}/approve"]
    )
    assert "get" in paths["/api/v1/projects/{project_id}/semantic-signals"]
    assert "SemanticPlanTargetIn" in str(app.openapi()["components"]["schemas"])


def test_semantic_migration_has_current_parent_and_rls_tables(monkeypatch: pytest.MonkeyPatch):
    migration = _migration()
    calls: list[str] = []
    monkeypatch.setattr(
        migration.op, "create_table", lambda name, *args, **kwargs: calls.append(name)
    )
    monkeypatch.setattr(migration.op, "create_index", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration.op, "add_column", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration, "_enable_rls", lambda table: calls.append(f"rls:{table}"))
    migration.upgrade()
    assert migration.down_revision == "0025_project_competitor_evidence"
    assert calls[:3] == [
        "project_semantic_collections",
        "project_semantic_collection_keywords",
        "project_semantic_keyword_geo_bindings",
    ]
    assert calls[-3:] == [
        "rls:project_semantic_collections",
        "rls:project_semantic_collection_keywords",
        "rls:project_semantic_keyword_geo_bindings",
    ]
