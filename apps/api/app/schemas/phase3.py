from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


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
    urls: list[HttpUrl] = Field(min_length=1, max_length=10)
    terms_acknowledged: bool

    @field_validator("urls")
    @classmethod
    def require_unique_urls(cls, value: list[HttpUrl]) -> list[HttpUrl]:
        normalized = [str(url) for url in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Competitor URLs must be unique")
        return value

    @field_validator("terms_acknowledged")
    @classmethod
    def require_terms_acknowledgement(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Confirm responsibility for the supplied public URLs")
        return value


class ScanOut(BaseModel):
    id: UUID
    tenant_id: UUID
    project_id: UUID | None
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
    project_id: UUID | None
    title: str
    kind: str
    content: dict
    source_scan_id: UUID | None
    state: str
    approved_by: UUID | None
    approved_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ManualAssetProvenance(BaseModel):
    model_config = {"extra": "forbid"}

    kind: Literal["manual_upload"] = "manual_upload"
    rights_basis: Literal["own", "licensed", "cc"]
    rights_confirmed: Literal[True]
    source_url: HttpUrl | None = None
    source_reference: str | None = Field(default=None, min_length=3, max_length=500)
    license_name: str | None = Field(default=None, max_length=128)
    license_url: HttpUrl | None = None
    license_expires_at: date | None = None
    author: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_manual_rights(self) -> ManualAssetProvenance:
        if not self.source_url and not self.source_reference:
            raise ValueError("Provide a source URL or an internal rights reference")
        if self.rights_basis != "own" and not self.license_name:
            raise ValueError("Licensed and CC assets require a license name")
        if self.license_expires_at and self.license_expires_at < date.today():
            raise ValueError("License has expired")
        return self


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
    provenance: dict
    hashes: dict
    availability: Literal["eligible", "expired", "rights_missing"]

    model_config = {"from_attributes": True}
