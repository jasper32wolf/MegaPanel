from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FORBIDDEN = ("<", ">", "{", "}", "javascript:", "http://", "https://")


class AuthorProfileIn(BaseModel):
    """Public facts about a real reviewed author, not AI-created expertise claims."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=2, max_length=160)
    name: str = Field(min_length=2, max_length=255)
    role: str = Field(min_length=2, max_length=255)
    biography: str = Field(min_length=40, max_length=4_000)
    expertise: list[str] = Field(min_length=1, max_length=12)
    evidence: list[str] = Field(min_length=1, max_length=12)
    portrait_asset_id: UUID

    @field_validator("slug")
    @classmethod
    def normalize_slug(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SLUG.fullmatch(normalized):
            raise ValueError("Author slug must use lowercase Latin letters, digits and hyphens")
        return normalized

    @field_validator("name", "role", "biography", "expertise", "evidence")
    @classmethod
    def require_safe_public_text(cls, value: str | list[str]) -> str | list[str]:
        values = [value] if isinstance(value, str) else value
        result = []
        for item in values:
            normalized = item.strip()
            if not normalized:
                raise ValueError("Author profile text cannot be empty")
            lowered = normalized.lower()
            if any(token in lowered for token in _FORBIDDEN):
                raise ValueError("Author profile cannot contain markup, code or URLs")
            result.append(normalized)
        return result[0] if isinstance(value, str) else result

    @model_validator(mode="after")
    def require_unique_expertise_and_evidence(self) -> AuthorProfileIn:
        if len(self.expertise) != len(set(self.expertise)):
            raise ValueError("Author expertise contains duplicates")
        if len(self.evidence) != len(set(self.evidence)):
            raise ValueError("Author evidence contains duplicates")
        return self


class AuthorProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: AuthorProfileIn
    supersedes_id: UUID | None = None


class AuthorProfileDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=4_000)


class AuthorProfileOut(BaseModel):
    id: UUID
    project_id: UUID
    supersedes_id: UUID | None
    profile: AuthorProfileIn
    profile_hash: str
    version: int
    state: str
    submitted_at: datetime | None
    reviewed_at: datetime | None
    decision_reason: str | None
    created_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


def author_profile_hash(profile: AuthorProfileIn | dict) -> str:
    payload = profile.model_dump(mode="json") if isinstance(profile, AuthorProfileIn) else profile
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
