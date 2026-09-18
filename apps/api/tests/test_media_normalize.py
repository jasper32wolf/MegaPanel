from io import BytesIO

import pytest
from PIL import Image

from app.services.media_normalize import average_hash, decode_image, normalize_image


def test_decode_image_loads_valid_image():
    buf = BytesIO()
    Image.new("RGB", (120, 80), color=(40, 120, 80)).save(buf, format="PNG")

    image = decode_image(buf.getvalue())

    assert image.size == (120, 80)


def test_decode_image_rejects_invalid_bytes():
    with pytest.raises(ValueError, match="Invalid or oversized image"):
        decode_image(b"not an image")


def test_normalize_changes_bytes_and_hash():
    img = Image.new("RGB", (120, 80), color=(40, 120, 80))
    buf = BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()
    out, ph = normalize_image(raw, site_salt="tenant-1")
    assert out != raw
    assert len(ph) == 16
    assert average_hash(Image.open(BytesIO(out)))
