import asyncio
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.api.v1 import media
from app.api.v1.media import (
    MAX_BYTES,
    _asset_out,
    create_media_review_decision,
    ensure_media_review_allows_use,
)
from app.schemas.phase3 import ManualAssetProvenance, MediaReviewDecisionIn
from fastapi import HTTPException
from pydantic import ValidationError


def test_media_response_hides_filesystem_path():
    asset = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        path="/srv/uploads/tenant/media/secret-file.webp",
        content_type="image/webp",
        source="licensed registry",
        license="own",
        author="Operator",
        phash="a" * 16,
        normalized=False,
        tags=[],
    )

    result = _asset_out(asset)

    assert result["path"] == f"/api/v1/media/{asset.id}/file"
    assert "secret-file" not in result["path"]
    assert result["provenance"] == {}
    assert result["hashes"] == {}


def test_media_response_projects_only_controlled_provenance_and_hashes():
    asset = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        path="/srv/uploads/tenant/media/secret-file.webp",
        content_type="image/webp",
        source="https://source.example.test/asset",
        license="CC BY 4.0",
        author="Author",
        phash="a" * 16,
        normalized=False,
        tags=[],
        meta={
            "provenance": {"kind": "manual_upload", "rights_confirmed": True},
            "hashes": {"original_sha256": "a" * 64, "stored_sha256": "b" * 64},
            "private": "not-projected",
        },
    )

    result = _asset_out(asset)

    assert result["provenance"] == {"kind": "manual_upload", "rights_confirmed": True}
    assert result["hashes"]["stored_sha256"] == "b" * 64
    assert "private" not in result
    assert "secret-file" not in str(result)


def test_manual_media_provenance_requires_confirmed_rights_and_valid_license():
    own = ManualAssetProvenance(
        rights_basis="own",
        rights_confirmed=True,
        source_reference="Оператор подтвердил оригинал",
    )
    assert own.kind == "manual_upload"

    with pytest.raises(ValidationError, match="source URL or an internal rights reference"):
        ManualAssetProvenance(rights_basis="own", rights_confirmed=True)
    with pytest.raises(ValidationError, match="license name"):
        ManualAssetProvenance(
            rights_basis="licensed",
            rights_confirmed=True,
            source_reference="Договор 17",
        )
    with pytest.raises(ValidationError, match="expired"):
        ManualAssetProvenance(
            rights_basis="cc",
            rights_confirmed=True,
            source_reference="Карточка лицензии",
            license_name="CC BY",
            license_expires_at=date.today() - timedelta(days=1),
        )


def test_media_upload_limit_is_eight_megabytes():
    assert MAX_BYTES == 8 * 1024 * 1024


def test_media_availability_projects_expired_and_malformed_provenance():
    expired = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        path="/safe/file.webp",
        content_type="image/webp",
        source=None,
        license=None,
        author=None,
        phash=None,
        normalized=False,
        tags=[],
        meta={
            "provenance": {
                "kind": "manual_upload",
                "rights_confirmed": True,
                "license_expires_at": "2000-01-01",
            },
            "hashes": {"stored_sha256": "a" * 64},
        },
    )
    malformed = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        path="/safe/file.webp",
        content_type="image/webp",
        source=None,
        license=None,
        author=None,
        phash=None,
        normalized=False,
        tags=[],
        meta={"provenance": {"kind": "manual_upload", "rights_confirmed": True}, "hashes": {}},
    )

    assert _asset_out(expired)["availability"] == "expired"
    assert _asset_out(malformed)["availability"] == "rights_missing"


def test_rejected_media_review_requires_reason_and_manual_replacement_guidance():
    with pytest.raises(ValidationError, match="reason"):
        MediaReviewDecisionIn(
            decision="rejected", manual_replacement_guidance="Upload a new original"
        )
    with pytest.raises(ValidationError, match="manual replacement guidance"):
        MediaReviewDecisionIn(decision="rejected", reason="Rights evidence is insufficient")


def test_media_review_decision_is_audited_against_current_stored_hash(monkeypatch):
    tenant_id, asset_id, actor_id = uuid4(), uuid4(), uuid4()
    asset = SimpleNamespace(
        id=asset_id,
        tenant_id=tenant_id,
        meta={"hashes": {"stored_sha256": "a" * 64}},
    )
    entry = SimpleNamespace(
        id=19,
        payload={},
        actor_id=actor_id,
        created_at=None,
        record_hash="b" * 64,
    )

    class Session:
        committed = False

        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: asset)

        async def commit(self):
            self.committed = True

    db = Session()
    append = AsyncMock(return_value=entry)
    monkeypatch.setattr(media, "append_audit", append)
    auth = SimpleNamespace(role="manager", tenant_id=tenant_id, user=SimpleNamespace(id=actor_id))

    result = asyncio.run(
        create_media_review_decision(
            asset_id,
            MediaReviewDecisionIn(
                decision="rejected",
                reason="License evidence is incomplete",
                manual_replacement_guidance="Upload a licensed replacement manually",
                evidence="Review ticket MR-1",
            ),
            auth,
            db,
        )
    )

    assert db.committed is True
    assert append.await_args.kwargs["payload"] == {
        "asset_id": str(asset_id),
        "stored_sha256": "a" * 64,
        "decision": "rejected",
        "reason": "License evidence is incomplete",
        "manual_replacement_guidance": "Upload a licensed replacement manually",
        "evidence": "Review ticket MR-1",
    }
    assert result["record_hash"] == "b" * 64
    assert "path" not in result


def test_latest_rejected_review_blocks_only_matching_asset_hash(monkeypatch):
    tenant_id, asset_id = uuid4(), uuid4()
    rejected = SimpleNamespace(
        payload={
            "decision": "rejected",
            "manual_replacement_guidance": "Upload a new source file manually",
        }
    )
    monkeypatch.setattr(media, "_media_review_history", AsyncMock(return_value=[rejected]))

    with pytest.raises(ValueError, match="Upload a new source file manually"):
        asyncio.run(
            ensure_media_review_allows_use(
                SimpleNamespace(),
                tenant_id=tenant_id,
                asset_id=asset_id,
                stored_sha256="a" * 64,
            )
        )

    monkeypatch.setattr(
        media,
        "_media_review_history",
        AsyncMock(return_value=[SimpleNamespace(payload={"decision": "approved"})]),
    )
    asyncio.run(
        ensure_media_review_allows_use(
            SimpleNamespace(),
            tenant_id=tenant_id,
            asset_id=asset_id,
            stored_sha256="b" * 64,
        )
    )


def test_media_review_routes_are_registered():
    from app.main import app

    paths = app.openapi()["paths"]
    assert "post" in paths["/api/v1/media/{asset_id}/review-decisions"]
    assert "get" in paths["/api/v1/media/{asset_id}/review-decisions"]


def test_media_review_write_preserves_tenant_isolation():
    asset = SimpleNamespace(
        id=uuid4(), tenant_id=uuid4(), meta={"hashes": {"stored_sha256": "a" * 64}}
    )

    class Session:
        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: asset)

    auth = SimpleNamespace(role="manager", tenant_id=uuid4(), user=SimpleNamespace(id=uuid4()))
    with pytest.raises(HTTPException, match="Forbidden") as exc_info:
        asyncio.run(
            create_media_review_decision(
                asset.id,
                MediaReviewDecisionIn(decision="approved"),
                auth,
                Session(),
            )
        )
    assert exc_info.value.status_code == 403
