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


class CityProjectReadinessOut(BaseModel):
    project_family_member_id: UUID
    child_project_id: UUID
    child_project_name: str
    child_project_slug: str
    hostname: str
    geo_id: UUID
    source_structure_revision_id: UUID | None
    facts_state: str | None
    facts_version: int | None
    public_fact_diff: dict[str, list[str]]
    private_recipient_configured: bool
    keyword_count: int
    primary_geo_ready: bool
    page_plans_by_state: dict[str, int]
    site_exists: bool
    next_action: str

    model_config = ConfigDict(extra="forbid")


class ProjectFamilyMemberOut(BaseModel):
    id: UUID
    master_project_id: UUID
    child_project_id: UUID
    geo_id: UUID
    hostname: str
    source_structure_revision_id: UUID | None
    child_project: dict
    draft_fact_revision_id: UUID | None

    model_config = ConfigDict(from_attributes=True)
