from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from site_panel_blocks.schema import LIBRARY_VERSION, BlockSpec, KitSpec, ThemeProfile

KITS_DIR = Path(__file__).resolve().parent / "kits"
_SLOT = re.compile(r"\{([a-z][a-z0-9_]*)\}", re.IGNORECASE)
_AI_TEXT_SLOTS = {"unique_core"}


def library_version() -> str:
    return LIBRARY_VERSION


def _read_block(kit_dir: Path, block_type: str, meta: dict) -> BlockSpec:
    html_path = kit_dir / "blocks" / f"{block_type}.html"
    css_path = kit_dir / "blocks" / f"{block_type}.css"
    html = html_path.read_text(encoding="utf-8") if html_path.exists() else ""
    css = css_path.read_text(encoding="utf-8") if css_path.exists() else ""
    return BlockSpec(
        type=block_type,
        name=meta.get("name") or block_type.replace("_", " ").title(),
        html=html.strip(),
        css=css.strip(),
        props=meta.get("props") or {},
        cro=meta.get("cro") or [],
    )


@lru_cache(maxsize=16)
def load_kit(key: str) -> KitSpec:
    kit_dir = KITS_DIR / key
    if not kit_dir.is_dir():
        raise FileNotFoundError(f"Unknown kit: {key}")
    meta = json.loads((kit_dir / "kit.json").read_text(encoding="utf-8"))
    theme_raw = {}
    theme_path = kit_dir / "theme.json"
    if theme_path.exists():
        theme_raw = json.loads(theme_path.read_text(encoding="utf-8"))
    block_metas = {b["type"]: b for b in meta.get("block_meta", [])}
    blocks: list[BlockSpec] = []
    for block_type in meta.get("blocks", []):
        blocks.append(_read_block(kit_dir, block_type, block_metas.get(block_type, {})))
    return KitSpec(
        key=meta["key"],
        name=meta.get("name") or key,
        version=meta.get("version") or LIBRARY_VERSION,
        description=meta.get("description") or "",
        niches=meta.get("niches") or [],
        blocks=blocks,
        theme=ThemeProfile(**theme_raw) if theme_raw else ThemeProfile(),
    )


def block_slot_schema(key: str, block_type: str) -> dict[str, dict[str, Any]]:
    """Return the server-owned plain-text slot contract for one curated block."""
    block = next((item for item in load_kit(key).blocks if item.type == block_type), None)
    if block is None:
        raise FileNotFoundError(f"Unknown block {block_type!r} in kit {key!r}")
    return {
        name: {"type": "string", "max_length": 8000}
        for name in _SLOT.findall(block.html)
        if name in _AI_TEXT_SLOTS
    }


def list_kits() -> list[dict]:
    out = []
    if not KITS_DIR.exists():
        return out
    for path in sorted(KITS_DIR.iterdir()):
        if not path.is_dir() or not (path / "kit.json").exists():
            continue
        kit = load_kit(path.name)
        out.append(
            {
                "key": kit.key,
                "name": kit.name,
                "version": kit.version,
                "description": kit.description,
                "niches": kit.niches,
                "blocks": [b.type for b in kit.blocks],
            }
        )
    return out


__all__ = ["block_slot_schema", "library_version", "list_kits", "load_kit"]
