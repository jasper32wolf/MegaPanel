from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DesignProfileScope = Literal["family", "project"]
DesignProfileState = Literal["draft", "review", "approved", "rejected"]

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_SAFE_TOKEN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class DesignTokensIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: str = "#0f6e5c"
    secondary: str = "#1a3d34"
    background: str = "#f7f9f8"
    surface: str = "#ffffff"
    text: str = "#14201c"
    muted: str = "#5f7269"
    radius: int = Field(default=12, ge=0, le=24)
    density: float = Field(default=1.0, ge=0.9, le=1.1)
    font_pair: Literal["sans", "serif_mix", "display"] = "sans"
    gradient: Literal["none", "soft", "bold"] = "soft"
    motion: Literal["none", "reduced"] = "reduced"

    @field_validator("primary", "secondary", "background", "surface", "text", "muted")
    @classmethod
    def require_hex_color(cls, value: str) -> str:
        if not _HEX_COLOR.fullmatch(value):
            raise ValueError("Design colors must use #rrggbb notation")
        return value.lower()


class DesignLayoutPolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_kits: list[str] = Field(min_length=1, max_length=8)
    allowed_variants: list[str] = Field(default_factory=list, max_length=24)
    required_blocks: list[str] = Field(default_factory=list, max_length=15)
    allowed_media_roles: list[Literal["hero", "process", "team", "portfolio", "proof"]] = Field(
        default_factory=list,
        max_length=5,
    )

    @field_validator("allowed_kits", "allowed_variants", "required_blocks")
    @classmethod
    def require_unique_safe_tokens(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().lower() for item in value]
        if any(not _SAFE_TOKEN.fullmatch(item) for item in normalized):
            raise ValueError("Design policy contains an invalid identifier")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Design policy contains duplicate identifiers")
        return normalized


class DesignAccessibilityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_contrast_ratio: float = Field(default=4.5, ge=4.5, le=7.0)
    require_descriptive_alt: bool = True
    require_visible_cta: bool = True
    require_reduced_motion: bool = True


class DesignProfileIn(BaseModel):
    """Typed design policy without raw CSS, HTML, JavaScript, URLs, or generated claims."""

    model_config = ConfigDict(extra="forbid")

    site_family: str = Field(min_length=2, max_length=64)
    niche_fit: list[str] = Field(default_factory=list, max_length=20)
    tokens: DesignTokensIn = Field(default_factory=DesignTokensIn)
    layout: DesignLayoutPolicyIn
    imagery_guidance: list[str] = Field(default_factory=list, max_length=12)
    voice_traits: list[str] = Field(default_factory=list, max_length=8)
    differentiation_rationale: str = Field(min_length=20, max_length=1000)
    accessibility: DesignAccessibilityIn = Field(default_factory=DesignAccessibilityIn)
    prohibited_patterns: list[str] = Field(default_factory=list, max_length=20)

    @field_validator(
        "site_family",
        "niche_fit",
        "imagery_guidance",
        "voice_traits",
        "differentiation_rationale",
        "prohibited_patterns",
    )
    @classmethod
    def reject_executable_or_deceptive_design_content(
        cls, value: str | list[str]
    ) -> str | list[str]:
        values = [value] if isinstance(value, str) else value
        for item in values:
            normalized = item.strip()
            lowered = normalized.lower()
            if not normalized:
                raise ValueError("Design guidance cannot be empty")
            prohibited_text = ("<", ">", "{", "}", "javascript:", "http://", "https://")
            if any(token in lowered for token in prohibited_text):
                raise ValueError("Design guidance cannot contain code, markup, or URLs")
            crawler_terms = ("cloaking", "скрыт", "user-agent", "ip-адрес", "поисков")
            contains_bot_word = bool(re.search(r"\bбот\w*\b", lowered))
            if contains_bot_word or any(term in lowered for term in crawler_terms):
                raise ValueError(
                    "Design policy cannot contain crawler-specific or hidden-content behavior"
                )
        return value.strip() if isinstance(value, str) else [item.strip() for item in value]

    @model_validator(mode="after")
    def require_visible_quality_constraints(self) -> DesignProfileIn:
        if not self.prohibited_patterns:
            self.prohibited_patterns = [
                "hidden-content",
                "crawler-specific-output",
                "unsupported-claims",
            ]
        return self


class DesignProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: DesignProfileScope
    name: str = Field(min_length=2, max_length=255)
    profile: DesignProfileIn
    supersedes_id: UUID | None = None


class DesignProfileDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=4000)


class DesignProfileOut(BaseModel):
    id: UUID
    project_id: UUID
    scope: DesignProfileScope
    name: str
    version: int
    state: DesignProfileState
    profile: DesignProfileIn
    profile_hash: str
    supersedes_id: UUID | None
    submitted_at: datetime | None
    reviewed_at: datetime | None
    decision_reason: str | None
    created_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class EffectiveDesignProfileOut(BaseModel):
    project_id: UUID
    inherited_from_project_id: UUID | None
    family_profile: DesignProfileOut | None
    project_profile: DesignProfileOut | None
    effective_profile: DesignProfileIn | None
    effective_profile_hash: str | None
