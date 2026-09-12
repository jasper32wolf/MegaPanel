from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

LIBRARY_VERSION = "1.0.0"

GradientMode = Literal["none", "soft", "bold"]
FontPair = Literal["sans", "serif_mix", "display"]


class ThemeProfile(BaseModel):
    radius: int = 12
    primary: str = "#0f6e5c"
    secondary: str = "#1a3d34"
    bg: str = "#f7f9f8"
    surface: str = "#ffffff"
    text: str = "#14201c"
    muted: str = "#5f7269"
    gradient: GradientMode = "soft"
    density: float = 1.0
    shadow: str = "0 8px 28px rgba(20,32,28,0.08)"
    font_pair: FontPair = "sans"


class BlockSpec(BaseModel):
    type: str
    name: str
    html: str
    css: str = ""
    props: dict[str, Any] = Field(default_factory=dict)
    cro: list[str] = Field(default_factory=list)


class KitSpec(BaseModel):
    key: str
    name: str
    version: str = LIBRARY_VERSION
    description: str = ""
    niches: list[str] = Field(default_factory=list)
    blocks: list[BlockSpec] = Field(default_factory=list)
    theme: ThemeProfile = Field(default_factory=ThemeProfile)
