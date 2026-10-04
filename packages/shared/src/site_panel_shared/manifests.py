from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from site_panel_shared.enums import IndexState, PublishState


class BlockDef(BaseModel):
    """Safe HTML/CSS block from Block Factory."""

    type: str
    hash_class: str
    html: str
    css: str = ""
    props: dict[str, Any] = Field(default_factory=dict)
    order: int = 0


class GeoEntity(BaseModel):
    id: UUID | None = None
    kind: str  # country|region|city|district|street|metro|landmark
    name: str
    name_forms: dict[str, str] = Field(default_factory=dict)  # nom/gen/prep/...
    parent_id: UUID | None = None
    lat: float | None = None
    lon: float | None = None
    timezone: str | None = None
    population: int | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)


class PageMedia(BaseModel):
    model_config = {"extra": "forbid"}

    asset_id: UUID
    stored_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    alt: str = Field(min_length=1, max_length=255)


class DesignSnapshot(BaseModel):
    """Resolved reviewed visual policy frozen with a page or site artifact."""

    profile_revision_id: UUID | None = None
    profile_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    profile_scope: str | None = Field(default=None, pattern=r"^(family|project)$")
    layout_variant: str | None = Field(default=None, max_length=64)
    tokens: dict[str, str] = Field(default_factory=dict, max_length=32)
    art_direction: dict[str, Any] = Field(default_factory=dict, max_length=16)


class PageManifest(BaseModel):
    slug: str
    title_template: str
    h1_template: str
    meta_description_template: str = ""
    service: str
    modifier: str | None = None
    method: str | None = None
    geo_id: UUID | None = None
    blocks: list[BlockDef] = Field(default_factory=list)
    publish_state: PublishState = PublishState.DRAFT
    index_state: IndexState = IndexState.NOINDEX
    unique_core: str | None = None
    block_slot_values: dict[str, dict[str, str | None]] = Field(default_factory=dict)
    media: list[PageMedia] = Field(default_factory=list, max_length=12)
    block_media: dict[str, PageMedia] = Field(default_factory=dict, max_length=12)
    schema_org: dict[str, Any] = Field(default_factory=dict)
    design: DesignSnapshot | None = None
    seed: int = 0

    @model_validator(mode="after")
    def require_valid_media_placements(self) -> PageManifest:
        block_ids = [block.type for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("Page blocks must have unique types for media placement")
        unknown_block_ids = set(self.block_media).difference(block_ids)
        if unknown_block_ids:
            raise ValueError("Block media must target a page block")
        asset_ids = [item.asset_id for item in [*self.media, *self.block_media.values()]]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("Page media assets must be unique")
        if len(asset_ids) > 12:
            raise ValueError("A page can contain at most 12 media assets")
        return self


class SiteManifest(BaseModel):
    """Site-as-Code root document stored in PostgreSQL."""

    site_id: UUID
    tenant_id: UUID
    domain: str
    locale: str = "ru"
    css_vars: dict[str, str] = Field(default_factory=dict)
    design: DesignSnapshot | None = None
    pages: list[PageManifest] = Field(default_factory=list)
    legal: dict[str, Any] = Field(default_factory=dict)
    contacts: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    version: int = 1
    updated_at: datetime | None = None


class GenerationJob(BaseModel):
    job_id: UUID
    tenant_id: UUID
    site_id: UUID | None = None
    category_ids: list[UUID] = Field(default_factory=list)
    geo_ids: list[UUID] = Field(default_factory=list)
    model_tier: str = "micro"
    priority: int = 100
