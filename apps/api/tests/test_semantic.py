from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.api.v1 import semantic
from app.main import app
from app.schemas.workflow import (
    SemanticCollectionCreate,
    SemanticCollectionKeywordIn,
    SemanticCollectionUpdate,
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


def test_semantic_collection_accepts_manual_source_runs_and_rejects_duplicates():
    source_run_id = uuid4()
    body = SemanticCollectionCreate(
        name="Основная",
        manual_source_run_ids=[source_run_id],
    )
    update = SemanticCollectionUpdate(
        name="Основная",
        manual_source_run_ids=[source_run_id],
        version=1,
    )

    assert body.manual_source_run_ids == [source_run_id]
    assert update.manual_source_run_ids == [source_run_id]
    with pytest.raises(ValidationError, match="duplicate manual source runs"):
        SemanticCollectionCreate(
            name="Основная",
            manual_source_run_ids=[source_run_id, source_run_id],
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


@pytest.mark.asyncio
async def test_semantic_impact_is_read_only_and_never_schedules_mutation(
    monkeypatch: pytest.MonkeyPatch,
):
    collection_id = uuid4()
    project_id = uuid4()
    signals = {
        "collections": [{"id": str(collection_id), "name": "Новые запросы", "version": 1}],
        "totals": {"covered": 0, "planned": 0, "uncovered": 1, "unbound": 0},
        "coverage": [
            {
                "collection_keyword_id": str(uuid4()),
                "geo_binding_id": str(uuid4()),
                "status": "uncovered",
                "plans": [],
            }
        ],
        "collisions": [],
        "unmapped_plans": [],
    }
    monkeypatch.setattr(semantic, "semantic_signals", AsyncMock(return_value=signals))

    result = await semantic.semantic_impact(
        project_id=project_id,
        collection_id=collection_id,
        auth=SimpleNamespace(),
        db=SimpleNamespace(),
    )

    assert result["recommendations"][0]["action"] == "new_page"
    assert result["policy"] == {
        "read_only": True,
        "automatic_draft": False,
        "automatic_build": False,
        "automatic_publish": False,
    }


class SemanticSignalsDatabase:
    def __init__(self, collections: list[object], plans: list[object]):
        self.rows = [collections, plans]

    async def execute(self, _: object) -> object:
        rows = self.rows.pop(0)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))


@pytest.mark.asyncio
async def test_semantic_signals_distinguishes_approved_planned_uncovered_unbound_and_collisions(
    monkeypatch: pytest.MonkeyPatch,
):
    project_id = uuid4()
    collection_id = uuid4()
    covered_member_id, planned_member_id, uncovered_member_id, unbound_member_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    covered_binding_id, planned_binding_id, uncovered_binding_id = uuid4(), uuid4(), uuid4()
    project = SimpleNamespace(id=project_id, tenant_id=uuid4())
    collection = SimpleNamespace(id=collection_id, name="Approved", version=3)
    approved_plan = SimpleNamespace(
        id=uuid4(),
        slug="/approved",
        state="approved",
        semantic_target_snapshot={
            "targets": [
                {
                    "collection_keyword_id": str(covered_member_id),
                    "geo_binding_ids": [str(covered_binding_id)],
                }
            ]
        },
    )
    draft_plan = SimpleNamespace(
        id=uuid4(),
        slug="/draft",
        state="draft",
        semantic_target_snapshot={
            "targets": [
                {
                    "collection_keyword_id": str(planned_member_id),
                    "geo_binding_ids": [str(planned_binding_id)],
                }
            ]
        },
    )
    review_collision = SimpleNamespace(
        id=uuid4(),
        slug="/review",
        state="review",
        semantic_target_snapshot={
            "targets": [
                {
                    "collection_keyword_id": str(planned_member_id),
                    "geo_binding_ids": [str(planned_binding_id)],
                }
            ]
        },
    )
    unmapped = SimpleNamespace(
        id=uuid4(), slug="/legacy", state="draft", semantic_target_snapshot={}
    )
    members = [
        {
            "id": str(member_id),
            "project_keyword_id": str(uuid4()),
            "keyword_id": str(uuid4()),
            "geo_bindings": ([{"id": str(binding_id)}] if binding_id else []),
        }
        for member_id, binding_id in [
            (covered_member_id, covered_binding_id),
            (planned_member_id, planned_binding_id),
            (uncovered_member_id, uncovered_binding_id),
            (unbound_member_id, None),
        ]
    ]
    monkeypatch.setattr(semantic, "_project_or_404", AsyncMock(return_value=project))
    monkeypatch.setattr(semantic, "_member_rows", AsyncMock(return_value=members))
    result = await semantic.semantic_signals(
        project_id,
        auth=SimpleNamespace(),
        db=SemanticSignalsDatabase(
            [collection], [approved_plan, draft_plan, review_collision, unmapped]
        ),
    )

    assert result["totals"] == {
        "members": 4,
        "bindings": 3,
        "covered": 1,
        "planned": 1,
        "uncovered": 1,
        "unbound": 1,
    }
    assert [item["status"] for item in result["coverage"]] == [
        "covered",
        "planned",
        "uncovered",
        "unbound",
    ]
    assert result["collisions"] == result["cannibalization"]
    assert result["cannibalization"][0]["plans"] == [
        {"plan_id": str(draft_plan.id), "slug": "/draft", "state": "draft"},
        {"plan_id": str(review_collision.id), "slug": "/review", "state": "review"},
    ]
    assert result["unmapped_plans"] == [
        {"plan_id": str(unmapped.id), "slug": "/legacy", "state": "draft"}
    ]
    assert result["policy"] == {
        "mode": "advisory",
        "read_only": True,
        "blocks_candidate": False,
        "basis": "approved collection and persisted PagePlan semantic target snapshots",
    }


def test_collection_source_runs_are_local_provenance_only():
    source = (Path(__file__).parents[1] / "app" / "api" / "v1" / "semantic.py").read_text(
        encoding="utf-8"
    )

    assert "ProjectSemanticSourceRun" in source
    assert '"manual_source_run_ids": source_run_refs' in source
    assert "httpx" not in source
    assert "enqueue_" not in source


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
