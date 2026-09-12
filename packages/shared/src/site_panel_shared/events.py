from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from site_panel_shared.enums import BuildStatus, LeadStatus


class LeadCreated(BaseModel):
    event: str = "lead.created"
    lead_id: UUID
    tenant_id: UUID
    site_id: UUID
    page_slug: str | None = None
    status: LeadStatus = LeadStatus.NEW
    created_at: datetime = Field(default_factory=datetime.utcnow)


class BuildFinished(BaseModel):
    event: str = "build.finished"
    build_id: UUID
    site_id: UUID
    tenant_id: UUID
    status: BuildStatus
    build_hash: str
    previous_build_hash: str | None = None
    duration_ms: int = 0


class IndexPromoted(BaseModel):
    event: str = "index.promoted"
    site_id: UUID
    tenant_id: UUID
    urls: list[str]
    count: int


class AuditAppended(BaseModel):
    event: str = "audit.appended"
    tenant_id: UUID | None
    actor_id: UUID | None
    action: str
    record_hash: str
    prev_hash: str
