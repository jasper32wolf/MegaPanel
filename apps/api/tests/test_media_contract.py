from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1.media import MAX_BYTES, _asset_out
from app.schemas.phase3 import ManualAssetProvenance
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
