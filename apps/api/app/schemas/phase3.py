from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


class BlockCreate(BaseModel):
    type: str = Field(
        pattern=r"^(hero|services_grid|pricing_table|calculator|faq|lead_form|contacts|custom)$"
    )
    name: str = Field(min_length=2, max_length=160)
    html: str = Field(min_length=1)
    css: str = ""
    props: dict = Field(default_factory=dict)


class BlockOut(BaseModel):
    id: UUID
    tenant_id: UUID
    type: str
    name: str
    hash_class: str
    html: str
    css: str
    props: dict
    is_active: bool

    model_config = {"from_attributes": True}


class ScanCreate(BaseModel):
    seed_url: HttpUrl
    max_pages: int = Field(default=5, ge=1, le=20)


class ScanOut(BaseModel):
    id: UUID
    tenant_id: UUID
    seed_url: str
    status: str
    urls: list
    skeleton: dict
    error: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeOut(BaseModel):
    id: UUID
    tenant_id: UUID
    title: str
    kind: str
    content: dict

    model_config = {"from_attributes": True}


class MediaOut(BaseModel):
    id: UUID
    tenant_id: UUID
    path: str
    content_type: str
    source: str | None
    license: str | None
    author: str | None
    phash: str | None
    normalized: bool
    tags: list

    model_config = {"from_attributes": True}
