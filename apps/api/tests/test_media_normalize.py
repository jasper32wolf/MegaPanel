from io import BytesIO

from PIL import Image

from app.services.media_normalize import average_hash, normalize_image


def test_normalize_changes_bytes_and_hash():
    img = Image.new("RGB", (120, 80), color=(40, 120, 80))
    buf = BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()
    out, ph = normalize_image(raw, site_salt="tenant-1")
    assert out != raw
    assert len(ph) == 16
    assert average_hash(Image.open(BytesIO(out)))
