from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from app.main import app
from app.schemas.research import SemanticSourceRunCreate
from pydantic import ValidationError


def source_payload(**overrides) -> dict:
    return {
        "provider": "bukvarix",
        "acquisition": "manual_export",
        "mode": "domain",
        "source_label": "Ручной экспорт за октябрь",
        "observed_at": datetime.now(UTC).isoformat(),
        "project_keyword_ids": [str(uuid4())],
        "confirm_record_manual_export": True,
        **overrides,
    }


def test_manual_semantic_source_schema_is_explicit_and_credential_free():
    body = SemanticSourceRunCreate.model_validate(source_payload())

    assert body.provider == "bukvarix"
    assert body.acquisition == "manual_export"
    assert body.confirm_record_manual_export is True
    for field, value in (
        ("confirm_record_manual_export", False),
        ("provider", "other"),
        ("acquisition", "api"),
        ("source_label", "https://unsafe.example/export"),
        ("notes", "api_key=unsafe"),
    ):
        with pytest.raises(ValidationError):
            SemanticSourceRunCreate.model_validate(source_payload(**{field: value}))
    with pytest.raises(ValidationError):
        SemanticSourceRunCreate.model_validate(source_payload(api_key="secret"))


def test_manual_semantic_source_schema_rejects_duplicate_project_keywords():
    keyword_id = uuid4()
    with pytest.raises(ValidationError, match="duplicates"):
        SemanticSourceRunCreate.model_validate(
            source_payload(project_keyword_ids=[str(keyword_id), str(keyword_id)])
        )


def test_bukvarix_routes_expose_only_fixed_preview_and_explicit_commit_boundaries():
    paths = app.openapi()["paths"]
    run_path = "/api/v1/projects/{project_id}/bukvarix-keyword-runs"

    assert "get" in paths["/api/v1/projects/{project_id}/semantic-sources/bukvarix/status"]
    assert {"get", "post"}.issubset(paths[run_path])
    assert "post" in paths[f"{run_path}/{{run_id}}/commit"]
    assert {"get", "post"}.issubset(paths["/api/v1/projects/{project_id}/semantic-source-runs"])
    assert all("endpoint" not in path and "credential" not in path for path in paths)


def test_bukvarix_https_migration_has_bounded_rls_scoped_preview_tables():
    migration = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0048_bukvarix_https_public_runs.py"
    ).read_text(encoding="utf-8")

    assert "0047_durable_site_build_queue" in migration
    assert "project_bukvarix_keyword_runs" in migration
    assert "project_bukvarix_keyword_results" in migration
    assert "https_public_free" in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "api_key" not in migration
    assert "endpoint" not in migration
    assert "httpx" not in migration
    assert "result_count <= 1000" in migration


def test_automated_provenance_is_allowed_in_source_run_output_only():
    from app.schemas.research import SemanticSourceRunOut

    output = SemanticSourceRunOut.model_validate(
        {
            "id": str(uuid4()),
            "project_id": str(uuid4()),
            "provider": "bukvarix",
            "acquisition": "https_public_free",
            "mode": "domain",
            "source_label": "Bukvarix HTTPS public free",
            "observed_at": datetime.now(UTC),
            "notes": None,
            "selected_keyword_count": 1,
            "created_at": datetime.now(UTC),
        }
    )
    assert output.acquisition == "https_public_free"
    with pytest.raises(ValidationError):
        SemanticSourceRunCreate.model_validate(source_payload(acquisition="https_public_free"))


def test_semantic_source_migration_has_project_scope_rls_and_no_secret_columns():
    migration = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0046_project_semantic_source_runs.py"
    ).read_text(encoding="utf-8")

    assert "0045_site_structure_ai_run_imports" in migration
    assert "project_semantic_source_runs" in migration
    assert "project_semantic_source_run_keywords" in migration
    assert "uq_semantic_source_run_project_keyword" in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "tenant_isolation_{table}" in migration
    assert migration.count("_tenant_policy(") >= 3
    assert "api_key" not in migration
    assert "endpoint" not in migration
    assert "httpx" not in migration
