from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

RecoveryAction = Literal["status", "restart", "rollback", "recover", "restore"]
OperationStatus = Literal[
    "requested",
    "queued",
    "in_progress",
    "success",
    "failure",
    "cancelled",
    "unknown",
]


class UpdateRequest(BaseModel):
    release_sha: str = Field(pattern=r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
    confirmation: Literal["DEPLOY"]


class RecoveryRequest(BaseModel):
    action: RecoveryAction
    snapshot_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{8,64}$")
    confirmation: str

    @model_validator(mode="after")
    def validate_confirmation(self) -> RecoveryRequest:
        expected = "RESTORE" if self.action == "restore" else self.action.upper()
        if self.confirmation != expected:
            raise ValueError(f"Confirmation must equal {expected}")
        if self.action == "restore" and not self.snapshot_id:
            raise ValueError("A concrete snapshot_id is required for restore")
        if self.action != "restore" and self.snapshot_id is not None:
            raise ValueError("snapshot_id is allowed only for restore")
        return self


class SystemOperationOut(BaseModel):
    id: UUID
    kind: Literal["update", "recovery"]
    action: str
    release_sha: str | None
    snapshot_id: str | None
    request_id: str
    workflow: str
    workflow_run_id: int | None
    workflow_url: str | None
    status: OperationStatus
    error_code: str | None
    created_at: str | None
    updated_at: str | None
    completed_at: str | None


class GitHubControlOut(BaseModel):
    configured: bool
    repository: str | None = None
    message: str


class VerifiedReleaseOut(BaseModel):
    sha: str
    updated_at: str | None
    workflow_url: str | None


class VerifiedReleasesOut(BaseModel):
    configured: bool
    releases: list[VerifiedReleaseOut] = Field(default_factory=list)
