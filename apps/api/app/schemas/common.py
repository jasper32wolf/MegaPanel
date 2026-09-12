from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class HealthResponse(BaseModel):
    status: str
    version: str
    env: str


class TenantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    slug: str = Field(min_length=2, max_length=100, pattern=r"^[a-z0-9-]+$")


class TenantOut(BaseModel):
    id: UUID
    name: str
    slug: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    role: str = "tenant_admin"
    tenant_id: UUID | None = None


class UserOut(BaseModel):
    id: UUID
    email: EmailStr
    role: str
    tenant_id: UUID | None
    is_active: bool

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    totp_code: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class SiteCreate(BaseModel):
    domain: str = Field(min_length=3, max_length=255)
    locale: str = "ru"
    contacts: dict = Field(default_factory=dict)


class SiteOut(BaseModel):
    id: UUID
    tenant_id: UUID
    domain: str
    locale: str
    publish_state: str
    build_hash: str | None
    version: int
    created_at: datetime

    model_config = {"from_attributes": True}


class KeywordImportItem(BaseModel):
    phrase: str
    category: str | None = None


class KeywordImportRequest(BaseModel):
    items: list[KeywordImportItem]
