from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

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
    schema_org: dict[str, Any] = Field(default_factory=dict)
    seed: int = 0


class SiteManifest(BaseModel):
    """Site-as-Code root document stored in PostgreSQL."""

    site_id: UUID
    tenant_id: UUID
    domain: str
    locale: str = "ru"
    css_vars: dict[str, str] = Field(default_factory=dict)
    pages: list[PageManifest] = Field(default_factory=list)
    legal: dict[str, Any] = Field(default_factory=dict)
    contacts: dict[str, Any] = Field(default_factory=dict)
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
