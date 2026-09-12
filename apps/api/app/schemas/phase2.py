from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TaxonomyCreate(BaseModel):
    niche: str = Field(min_length=2, max_length=128)
    slug: str = Field(min_length=2, max_length=160, pattern=r"^[a-z0-9-]+$")
    service: str = Field(min_length=2, max_length=255)
    modifier: str | None = None
    method: str | None = None
    attrs: dict = Field(default_factory=dict)
    templates: dict = Field(default_factory=dict)


class TaxonomyOut(BaseModel):
    id: UUID
    tenant_id: UUID
    niche: str
    slug: str
    service: str
    modifier: str | None
    method: str | None
    attrs: dict
    templates: dict
    is_active: bool

    model_config = {"from_attributes": True}


class GeoCreate(BaseModel):
    kind: str = Field(pattern=r"^(country|region|city|district|street|metro|landmark)$")
    name: str = Field(min_length=1, max_length=255)
    parent_id: UUID | None = None
    external_id: str | None = None
    lat: float | None = None
    lon: float | None = None
    timezone: str | None = None
    population: int | None = None
    attrs: dict = Field(default_factory=dict)


class GeoOut(BaseModel):
    id: UUID
    kind: str
    name: str
    name_forms: dict
    parent_id: UUID | None
    source: str
    lat: float | None
    lon: float | None
    timezone: str | None
    population: int | None
    is_validated: bool

    model_config = {"from_attributes": True}


class MorphRequest(BaseModel):
    word: str = Field(min_length=1, max_length=255)


class MorphOut(BaseModel):
    word: str
    forms: dict[str, str]
    placeholders: dict[str, str]


class ToponymValidateRequest(BaseModel):
    name: str
    kind: str | None = "city"


class OnboardingStart(BaseModel):
    niche: str


class OnboardingStep(BaseModel):
    step: str = Field(pattern=r"^(niche|geo|template|domain|build)$")
    payload: dict = Field(default_factory=dict)


class OnboardingOut(BaseModel):
    id: UUID
    tenant_id: UUID
    step: str
    payload: dict
    completed: bool
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class HealthCheckOut(BaseModel):
    dns_ok: bool = False
    ssl_ok: bool = False
    llm_ok: bool = False
    redis_ok: bool = False
    db_ok: bool = True
    details: dict = Field(default_factory=dict)
