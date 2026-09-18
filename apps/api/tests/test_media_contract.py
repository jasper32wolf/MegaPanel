from types import SimpleNamespace
from uuid import uuid4

from app.api.v1.media import MAX_BYTES, _asset_out


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


def test_media_upload_limit_is_eight_megabytes():
    assert MAX_BYTES == 8 * 1024 * 1024
