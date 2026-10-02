from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProjectCityCloneCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    geo_id: UUID
    name: str = Field(min_length=2, max_length=255)
    slug: str = Field(min_length=2, max_length=160, pattern=r"^[a-z0-9-]+$")
    hostname: str = Field(min_length=3, max_length=255)
    source_structure_revision_id: UUID | None = None


class ProjectFamilyMemberOut(BaseModel):
    id: UUID
    master_project_id: UUID
    child_project_id: UUID
    geo_id: UUID
    hostname: str
    source_structure_revision_id: UUID | None
    child_project: dict
    draft_fact_revision_id: UUID

    model_config = ConfigDict(from_attributes=True)
