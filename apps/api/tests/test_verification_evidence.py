from __future__ import annotations

import asyncio
import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.api.v1.panel import report_verification
from app.services.operations import (
    VERIFICATION_CONTROLS,
    append_operational_verification,
    list_verification_projection,
)


class Result:
    def __init__(self, rows: list[object]):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class VerificationDatabase:
    def __init__(self, rows: list[object]):
        self.rows = rows
        self.added: list[object] = []

    async def execute(self, _: object) -> Result:
        return Result(self.rows)

    def add(self, value: object) -> None:
        self.added.append(value)


def test_empty_registry_is_explicitly_not_observed_and_redacted():
    projection = asyncio.run(list_verification_projection(VerificationDatabase([])))

    assert len(projection) == len(VERIFICATION_CONTROLS)
    assert {item["state"] for item in projection} == {"not_observed"}
    assert all(item["observed_at"] is None for item in projection)
    assert all("source_ref" not in item and "code_sha" not in item for item in projection)
    assert all(item["limitation"] for item in projection)


def test_writer_accepts_only_registry_bound_evidence():
    db = VerificationDatabase([])
    observed_at = datetime(2026, 10, 1, 12, tzinfo=UTC)
    row = append_operational_verification(
        db,
        check_key="controlled_fixture_contract",
        mode="fixture",
        outcome="passed",
        source_kind="controlled_runner",
        source_ref="run-1",
        code_sha="a" * 40,
        observed_at=observed_at,
    )

    assert db.added == [row]
    assert row.observed_at == observed_at
    assert row.source_ref == "run-1"
    with pytest.raises(ValueError, match="Unsupported verification control"):
        append_operational_verification(
            db,
            check_key="vps_external_origin",
            mode="fixture",
            outcome="passed",
            source_kind="controlled_runner",
            source_ref="run-2",
        )
    with pytest.raises(ValueError, match="verification source"):
        append_operational_verification(
            db,
            check_key="controlled_fixture_contract",
            mode="fixture",
            outcome="passed",
            source_kind="free_form",
            source_ref="run-3",
        )


def test_verification_report_has_fixed_scope_and_only_get_route():
    report = asyncio.run(
        report_verification(
            auth=SimpleNamespace(),
            db=VerificationDatabase([]),
        )
    )

    assert report["scope"].startswith("bounded recorded")
    assert len(report["checks"]) == len(VERIFICATION_CONTROLS)
    path = "/api/v1/panel/reports/verification"
    from app.main import app

    methods = app.openapi()["paths"][path]
    assert set(methods) == {"get"}


def test_verification_migration_is_append_only_and_follows_current_head():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0039_operational_verifications.py"
    spec = importlib.util.spec_from_file_location("verification_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    source = path.read_text(encoding="utf-8")
    assert migration.down_revision == "0038_async_ai_execution"
    assert "BEFORE UPDATE OR DELETE" in source
    assert "INSERT" not in source
    assert "details" not in source
    assert "operational_verifications" in source


def test_model_has_no_tenant_or_free_form_payload_fields():
    from app.models import OperationalVerification

    columns = set(OperationalVerification.__table__.columns.keys())
    assert "tenant_id" not in columns
    assert "details" not in columns
    assert "payload" not in columns
    assert columns == {
        "id",
        "check_key",
        "mode",
        "outcome",
        "source_kind",
        "source_ref",
        "code_sha",
        "observed_at",
        "recorded_at",
    }
