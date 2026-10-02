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


def test_semantic_source_routes_are_generic_and_bukvarix_stays_status_only():
    paths = app.openapi()["paths"]

    assert "get" in paths["/api/v1/projects/{project_id}/semantic-sources/bukvarix/status"]
    assert {"get", "post"}.issubset(paths["/api/v1/projects/{project_id}/semantic-source-runs"])
    assert all("bukvarix" not in path or path.endswith("/status") for path in paths)


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
