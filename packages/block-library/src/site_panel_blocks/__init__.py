"""Versioned Block Factory library for service-site kits (TZ §6 / §9)."""

from __future__ import annotations

from site_panel_blocks.catalog import list_kits, load_kit, library_version
from site_panel_blocks.morph import apply_theme, hash_class_for, instantiate_blocks
from site_panel_blocks.schema import BlockSpec, KitSpec, LIBRARY_VERSION, ThemeProfile

__all__ = [
    "LIBRARY_VERSION",
    "BlockSpec",
    "KitSpec",
    "ThemeProfile",
    "apply_theme",
    "hash_class_for",
    "instantiate_blocks",
    "library_version",
    "list_kits",
    "load_kit",
]
