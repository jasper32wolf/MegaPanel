from __future__ import annotations

from typing import Literal
from uuid import UUID

from app.schemas.workflow import normalize_page_plan_slug
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SiteStructureHeadingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["h2", "h3", "h4", "h5", "h6"]
    text: str = Field(min_length=1, max_length=255)


class SiteStructurePageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=160, pattern=r"^[a-z0-9-]+$")
    parent_key: str | None = Field(default=None, min_length=1, max_length=160)
    slug: str = Field(min_length=1, max_length=512)
    title: str = Field(min_length=1, max_length=255)
    meta_description: str = Field(default="", max_length=170)
    h1: str = Field(default="", max_length=255)
    heading_outline: list[SiteStructureHeadingIn] = Field(default_factory=list, max_length=40)
    objective: str = Field(min_length=3, max_length=512)
    intent: str | None = Field(default=None, max_length=128)
    kit_key: str = Field(min_length=2, max_length=128)
    block_ids: list[str] = Field(default_factory=list, max_length=100)
    risk_notes: str | None = Field(default=None, max_length=4000)

    @field_validator("slug")
    @classmethod
    def normalize_slug(cls, value: str) -> str:
        return normalize_page_plan_slug(value)

    @model_validator(mode="after")
    def require_unique_blocks(self) -> SiteStructurePageIn:
        if len(self.block_ids) != len(set(self.block_ids)):
            raise ValueError("Page block selection contains duplicates")
        if self.parent_key == self.key:
            raise ValueError("Page cannot be its own parent")
        return self


def validate_site_structure_tree(
    pages: list[SiteStructurePageIn], evidence_ids: list[UUID]
) -> None:
    keys = [page.key for page in pages]
    slugs = [page.slug for page in pages]
    if len(keys) != len(set(keys)):
        raise ValueError("Site structure contains duplicate page keys")
    if len(slugs) != len(set(slugs)):
        raise ValueError("Site structure contains duplicate page slugs")
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("Site structure contains duplicate evidence")
    parents = {page.key: page.parent_key for page in pages}
    if any(parent_key and parent_key not in parents for parent_key in parents.values()):
        raise ValueError("Site structure references an unknown parent page")
    for key in parents:
        visited: set[str] = set()
        current: str | None = key
        while current:
            if current in visited:
                raise ValueError("Site structure contains a parent cycle")
            visited.add(current)
            current = parents[current]


class SiteStructureRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_collection_id: UUID
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=50)
    pages: list[SiteStructurePageIn] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_unique_tree(self) -> SiteStructureRevisionCreate:
        validate_site_structure_tree(self.pages, self.evidence_ids)
        return self


class SiteStructureRevisionUpdate(SiteStructureRevisionCreate):
    expected_structure_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_unique_updated_tree(self) -> SiteStructureRevisionUpdate:
        validate_site_structure_tree(self.pages, self.evidence_ids)
        return self


class SiteStructureAIImportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai_run_id: UUID
    semantic_collection_id: UUID
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=50)
    confirm_create_draft: Literal[True]

    @model_validator(mode="after")
    def require_unique_evidence(self) -> SiteStructureAIImportCreate:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("Site structure contains duplicate evidence")
        return self


class SiteStructureCityChildrenMaterializeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    child_project_ids: list[UUID] = Field(min_length=1, max_length=50)
    confirm_create_drafts: Literal[True]

    @model_validator(mode="after")
    def require_unique_children(self) -> SiteStructureCityChildrenMaterializeCreate:
        if len(self.child_project_ids) != len(set(self.child_project_ids)):
            raise ValueError("City child selection contains duplicates")
        return self


class SiteStructureRevisionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=4000)


class SiteStructureRevisionOut(BaseModel):
    id: UUID
    project_id: UUID
    version: int
    state: Literal["draft", "review", "approved", "rejected"]
    semantic_collection_id: UUID
    evidence_ids: list
    structure: dict
    structure_hash: str
    source_snapshot: dict
    source_snapshot_hash: str | None
    submitted_at: str | None
    reviewed_at: str | None
    decision_reason: str | None
    materialized_at: str | None
    materialized_page_plan_ids: list[UUID]
    created_at: str | None

    model_config = ConfigDict(from_attributes=True)


class SiteStructureAIImportOut(BaseModel):
    revision: SiteStructureRevisionOut
    imported: bool
    ai_run_id: UUID
    source_output_hash: str

    model_config = ConfigDict(extra="forbid")


class SiteStructureCityChildMaterializationOut(BaseModel):
    child_project_id: UUID
    project_family_member_id: UUID
    page_plan_ids: list[UUID]

    model_config = ConfigDict(extra="forbid")


class SiteStructureCityChildrenMaterializationOut(BaseModel):
    revision_id: UUID
    master_project_id: UUID
    structure_hash: str
    children: list[SiteStructureCityChildMaterializationOut]

    model_config = ConfigDict(extra="forbid")
