from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter


def average_hash(img: Image.Image, size: int = 8) -> str:
    gray = img.convert("L").resize((size, size))
    pixels = list(gray.get_flattened_data()) if hasattr(gray, "get_flattened_data") else list(gray.getdata())
    avg = sum(pixels) / len(pixels)
    bits = "".join("1" if p >= avg else "0" for p in pixels)
    return f"{int(bits, 2):016x}"


def normalize_image(raw: bytes, site_salt: str = "") -> tuple[bytes, str]:
    """Micro crop/rotate/noise/gamma for licensed assets uniqueness (TZ §6)."""
    img = Image.open(BytesIO(raw))
    w, h = img.size
    # 1% crop
    dx, dy = max(1, w // 100), max(1, h // 100)
    img = img.crop((dx, dy, w - dx, h - dy))
    img = img.rotate(0.5, expand=True, fillcolor=(255, 255, 255))
    img = ImageEnhance.Brightness(img).enhance(1.02)
    img = ImageEnhance.Contrast(img).enhance(1.01)
    img = img.filter(ImageFilter.GaussianBlur(radius=0.3))
    if img.mode not in {"RGB", "RGBA"}:
        img = img.convert("RGB")
    ph = average_hash(img)
    out = BytesIO()
    img.save(out, format="WEBP", quality=85)
    data = out.getvalue()
    # Cache key hint
    _ = hashlib.sha256(raw + site_salt.encode()).hexdigest()
    return data, ph


def save_normalized(raw: bytes, out_path: Path, site_salt: str = "") -> str:
    data, phash = normalize_image(raw, site_salt)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    return phash
