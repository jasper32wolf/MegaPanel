from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.services.release_gate import (
    RELEASE_GATE_RULESET_VERSION,
    build_snapshot_hash,
    evaluate_build_release_gate,
    legal_review_status,
    legal_snapshot_hash,
)
from site_panel_shared.manifests import PageManifest, SiteManifest


def _site_and_build(*, legal: dict | None = None, metadata: list[dict] | None = None):
    site_id, tenant_id = uuid4(), uuid4()
    manifest = SiteManifest(
        site_id=site_id,
        tenant_id=tenant_id,
        domain="example.test",
        legal=legal or {},
        pages=[
            PageManifest(
                slug="/",
                title_template="Ремонт",
                h1_template="Ремонт",
                service="Ремонт",
            )
        ],
    ).model_dump(mode="json")
    build = SimpleNamespace(
        manifest_snapshot=manifest,
        page_metadata_snapshot=metadata
        or [
            {
                "slug": "/",
                "path": "/",
                "index_state": "noindex",
                "thin": False,
                "content_chars": 100,
                "hash": "a" * 64,
            }
        ],
    )
    return SimpleNamespace(id=site_id, tenant_id=tenant_id), build


def test_release_gate_passes_for_a_complete_immutable_noindex_candidate():
    site, build = _site_and_build(
        legal={
            "org": "ООО Тест",
            "address": "Казань",
            "jurisdiction": "Российская Федерация",
            "privacy_email": "privacy@example.com",
        }
    )

    result = evaluate_build_release_gate(build, site)

    assert result["status"] == "pass"
    assert result["blockers"] == []
    assert result["warnings"] == []
    assert result["ruleset_version"] == RELEASE_GATE_RULESET_VERSION
    assert result["snapshot_hash"] == build_snapshot_hash(build)


def test_release_gate_blocks_missing_legal_facts_and_tampered_metadata():
    site, build = _site_and_build(metadata=[{"slug": "/"}])

    result = evaluate_build_release_gate(build, site)

    assert result["status"] == "block"
    assert "Set the legal organization before publish" in result["blockers"]
    assert "Selected build page metadata snapshot is invalid" in result["blockers"]


def test_release_gate_warns_for_indexed_pages_without_changing_snapshot():
    site, build = _site_and_build(
        legal={
            "org": "ООО Тест",
            "address": "Казань",
            "jurisdiction": "Российская Федерация",
            "privacy_email": "privacy@example.com",
        },
        metadata=[
            {
                "slug": "/",
                "path": "/",
                "index_state": "indexed",
                "thin": False,
                "content_chars": 100,
                "hash": "a" * 64,
            }
        ],
    )

    result = evaluate_build_release_gate(build, site)

    assert result["status"] == "pass"
    assert result["warnings"] == [
        "Indexed pages require an explicit operator promotion workflow: /"
    ]
    assert build.page_metadata_snapshot[0]["index_state"] == "indexed"


def test_legal_review_is_bound_to_its_candidate_legal_snapshot():
    _site, build = _site_and_build(
        legal={
            "org": "ООО Тест",
            "address": "Казань",
            "jurisdiction": "Российская Федерация",
            "privacy_email": "privacy@example.com",
        }
    )
    build.build_hash = "b" * 64
    build.legal_review = {
        "state": "approved",
        "legal_snapshot_hash": legal_snapshot_hash(build),
        "evidence_ref": "LEGAL-123",
        "reviewed_at": "2026-09-29T12:00:00+00:00",
    }

    assert legal_review_status(build)["status"] == "pass"
    build.manifest_snapshot["legal"]["address"] = "Москва"
    assert legal_review_status(build)["status"] == "block"


def test_rejected_legal_review_keeps_reason_and_guidance_without_passing_gate():
    _site, build = _site_and_build()
    build.build_hash = "b" * 64
    build.legal_review = {
        "state": "rejected",
        "legal_snapshot_hash": legal_snapshot_hash(build),
        "evidence_ref": "LEGAL-124",
        "reason": "Юридический адрес требует подтверждения.",
        "replacement_guidance": "Обновите подтверждённые legal facts и создайте новый candidate.",
        "reviewed_at": "2026-10-01T12:00:00+00:00",
    }

    result = legal_review_status(build)

    assert result["status"] == "block"
    assert result["review"] == {
        "state": "rejected",
        "evidence_ref": "LEGAL-124",
        "reason": "Юридический адрес требует подтверждения.",
        "replacement_guidance": "Обновите подтверждённые legal facts и создайте новый candidate.",
        "reviewed_at": "2026-10-01T12:00:00+00:00",
    }


def test_rejected_legal_review_requires_reason():
    from app.schemas.workflow import BuildLegalReviewIn
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="requires a reason"):
        BuildLegalReviewIn(decision="rejected", evidence_ref="LEGAL-125")
    with pytest.raises(ValidationError, match="requires remediation guidance"):
        BuildLegalReviewIn(
            decision="rejected",
            evidence_ref="LEGAL-125",
            reason="Нужна дополнительная проверка.",
        )


def test_release_gate_migration_follows_prompt_evaluation_head():
    source = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0034_build_release_gates.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision: str | None = "0033_prompt_evaluation_runs"' in source
    assert "build_release_gates" in source
    assert "tenant_isolation_build_release_gates" in source
