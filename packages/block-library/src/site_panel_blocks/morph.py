from __future__ import annotations

import colorsys
import hashlib
import re
from typing import Any
from uuid import UUID

from site_panel_blocks.schema import BlockSpec, ThemeProfile


def _seed_int(seed: str | UUID | int) -> int:
    if isinstance(seed, int):
        return seed & 0xFFFFFFFF
    raw = str(seed).encode()
    return int(hashlib.sha256(raw).hexdigest()[:8], 16)


def _hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _rgb_to_hex(r: float, g: float, b: float) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, int(r * 255))),
        max(0, min(255, int(g * 255))),
        max(0, min(255, int(b * 255))),
    )


def _shift_hue(hex_color: str, degrees: float) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    h = (h + degrees / 360.0) % 1.0
    nr, ng, nb = colorsys.hsv_to_rgb(h, s, v)
    return _rgb_to_hex(nr, ng, nb)


def apply_theme(seed: str | UUID | int, base: ThemeProfile | None = None) -> dict[str, str]:
    """Deterministic theme morph → CSS custom properties (without -- prefix)."""
    profile = base or ThemeProfile()
    n = _seed_int(seed)
    hue_shift = ((n % 17) - 8)  # -8..+8
    radius_step = (n // 17) % 5  # 0..4
    radius = max(0, min(24, profile.radius + (radius_step - 2) * 2))
    gradients = ("none", "soft", "bold")
    gradient = gradients[n % 3] if profile.gradient != "none" else "none"
    fonts = ("sans", "serif_mix", "display")
    font_pair = fonts[(n // 3) % 3]

    primary = _shift_hue(profile.primary, hue_shift)
    secondary = _shift_hue(profile.secondary, hue_shift * 0.6)

    gradient_css = "none"
    if gradient == "soft":
        gradient_css = f"linear-gradient(145deg, {primary}22, transparent 60%)"
    elif gradient == "bold":
        gradient_css = f"linear-gradient(120deg, {primary}, {secondary})"

    density = 0.9 + ((n % 5) * 0.05)
    shadow = profile.shadow if (n % 2) else "0 2px 10px rgba(20,32,28,0.06)"

    return {
        "sp-radius": f"{radius}px",
        "sp-radius-sm": f"{max(0, radius - 4)}px",
        "sp-primary": primary,
        "sp-secondary": secondary,
        "sp-bg": profile.bg,
        "sp-surface": profile.surface,
        "sp-text": profile.text,
        "sp-muted": profile.muted,
        "sp-gradient": gradient_css,
        "sp-shadow": shadow,
        "sp-density": str(round(density, 2)),
        "sp-font": (
            "'Source Sans 3', system-ui, sans-serif"
            if font_pair == "sans"
            else "'Fraunces', Georgia, serif"
            if font_pair == "display"
            else "Georgia, 'Source Sans 3', serif"
        ),
        "sp-gap": f"{round(1.0 * density, 2)}rem",
    }


def hash_class_for(block_type: str, html: str, site_seed: str | UUID | int) -> str:
    digest = hashlib.sha256(f"{block_type}:{html}:{site_seed}".encode()).hexdigest()[:6]
    safe = re.sub(r"[^a-z0-9]+", "", block_type.lower())[:12] or "blk"
    return f"blk-{safe}-{digest}"


def rewrite_css_for_hash(css: str, hash_class: str, block_type: str) -> str:
    """Prefix bare selectors with hashed class; keep :root vars global."""
    if not css.strip():
        return ""
    # Replace placeholder .blk-TYPE with actual hash
    css = css.replace(f".blk-{block_type}", f".{hash_class}")
    css = css.replace("__HASH__", f".{hash_class}")
    return css


def instantiate_blocks(
    blocks: list[BlockSpec],
    site_seed: str | UUID | int,
    theme: ThemeProfile | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    css_vars = apply_theme(site_seed, theme)
    out: list[dict[str, Any]] = []
    for i, block in enumerate(blocks):
        hc = hash_class_for(block.type, block.html, site_seed)
        out.append(
            {
                "type": block.type,
                "hash_class": hc,
                "html": block.html,
                "css": rewrite_css_for_hash(block.css, hc, block.type),
                "props": {**block.props, "cro": block.cro},
                "order": i,
                "name": block.name,
            }
        )
    return out, css_vars
