from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

ProjectStatus = Literal["draft", "active", "archived"]
FactState = Literal["draft", "confirmed"]
PagePlanState = Literal["draft", "review", "approved", "rejected"]
PageDraftState = Literal["queued", "generating", "draft", "review", "applied", "rejected", "failed"]
QaVerdict = Literal["pass", "warn", "block"]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    slug: str = Field(min_length=2, max_length=160, pattern=r"^[a-z0-9-]+$")
    domain: str | None = Field(default=None, min_length=3, max_length=255)
    locale: str = Field(default="ru", min_length=2, max_length=10)
    niche: str | None = Field(default=None, max_length=128)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    domain: str | None = Field(default=None, min_length=3, max_length=255)
    niche: str | None = Field(default=None, max_length=128)
    status: ProjectStatus | None = None
    version: int = Field(ge=1)


class FactRevisionCreate(BaseModel):
    facts: dict = Field(default_factory=dict)
    source_notes: str | None = Field(default=None, max_length=4000)

    @field_validator("facts")
    @classmethod
    def require_business_facts(cls, value: dict) -> dict:
        if not value:
            raise ValueError("Provide confirmed business facts")
        return value


class ProjectKeywordIn(BaseModel):
    keyword_id: UUID
    cluster: str | None = Field(default=None, max_length=255)
    intent: str | None = Field(default=None, max_length=128)
    priority: int | None = Field(default=None, ge=0, le=100)
    notes: str | None = Field(default=None, max_length=2000)


class ProjectKeywordsUpdate(BaseModel):
    items: list[ProjectKeywordIn] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def require_unique_keywords(self) -> ProjectKeywordsUpdate:
        ids = [item.keyword_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Project keyword selection contains duplicates")
        return self


class ProjectGeoIn(BaseModel):
    geo_id: UUID
    role: Literal["primary", "service_area", "reference"] = "service_area"
    position: int = Field(default=0, ge=0, le=10000)
    morph_overrides: dict[str, str] = Field(default_factory=dict)


class ProjectGeoUpdate(BaseModel):
    items: list[ProjectGeoIn] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def require_unique_places(self) -> ProjectGeoUpdate:
        ids = [item.geo_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Project geography selection contains duplicates")
        if sum(item.role == "primary" for item in self.items) > 1:
            raise ValueError("A project can have only one primary place")
        return self


class PagePlanCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=512)
    objective: str = Field(min_length=3, max_length=512)
    intent: str | None = Field(default=None, max_length=128)
    risk_notes: str | None = Field(default=None, max_length=4000)
    kit_key: str = Field(min_length=2, max_length=128)
    block_selection: dict = Field(default_factory=dict)
    source_refs: dict = Field(default_factory=dict)

    @field_validator("slug")
    @classmethod
    def normalize_slug(cls, value: str) -> str:
        path = value.strip().strip("/").lower()
        if not path:
            return "/"
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*", path):
            raise ValueError(
                "Page path must use lowercase Latin letters, digits, hyphens and slashes"
            )
        return f"/{path}"


class PagePlanUpdate(BaseModel):
    objective: str | None = Field(default=None, min_length=3, max_length=512)
    intent: str | None = Field(default=None, max_length=128)
    risk_notes: str | None = Field(default=None, max_length=4000)
    kit_key: str | None = Field(default=None, min_length=2, max_length=128)
    block_selection: dict | None = None
    source_refs: dict | None = None
    version: int = Field(ge=1)


class PagePlanDecision(BaseModel):
    reason: str | None = Field(default=None, max_length=4000)


class PageDraftRequest(BaseModel):
    pass


class QaOverrideIn(BaseModel):
    reason: Literal["operator_review", "known_exception", "approved_legal_copy"]
    justification: str = Field(min_length=10, max_length=2000)


class PageDraftDecision(BaseModel):
    reason: str | None = Field(default=None, max_length=4000)


class BuildPublishRequest(BaseModel):
    confirmed: bool


class BuildRollbackRequest(BaseModel):
    build_hash: str = Field(min_length=16, max_length=64)
    confirmed: bool


class LeadOutcomeIn(BaseModel):
    outcome: Literal["unknown", "contacted", "won", "lost", "unqualified"]
    reason: (
        Literal["no_answer", "price", "geography", "timing", "duplicate", "no_capacity", "other"]
        | None
    ) = None
    note: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def require_reason_for_terminal_outcome(self) -> LeadOutcomeIn:
        if self.outcome in {"lost", "unqualified"} and not self.reason:
            raise ValueError("Provide a reason for this outcome")
        return self
