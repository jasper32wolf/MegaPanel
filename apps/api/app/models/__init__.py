from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    branding: Mapped[dict] = mapped_column(JSONB, default=dict)
    quotas: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(default=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    users: Mapped[list[User]] = relationship(back_populates="tenant")
    sites: Mapped[list[Site]] = relationship(back_populates="tenant")
    categories: Mapped[list[TaxonomyCategory]] = relationship(back_populates="tenant")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="viewer")
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_pending: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tenant: Mapped[Tenant | None] = relationship(back_populates="users")


class FinopsEntry(Base):
    __tablename__ = "finops_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_usd: Mapped[float] = mapped_column(Float, default=0.0)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DsarJob(Base):
    __tablename__ = "dsar_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    subject_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    locale: Mapped[str] = mapped_column(String(10), default="ru")
    manifest: Mapped[dict] = mapped_column(JSONB, default=dict)
    publish_state: Mapped[str] = mapped_column(String(32), default="draft")
    build_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous_build_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    niche: Mapped[str | None] = mapped_column(String(128), nullable=True)
    indexnow_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    caddy_configured: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant: Mapped[Tenant] = relationship(back_populates="sites")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    record_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="0" * 64)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Keyword(Base):
    __tablename__ = "keywords"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    phrase: Mapped[str] = mapped_column(Text, nullable=False)
    normalized: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("taxonomy_categories.id", ondelete="SET NULL"), nullable=True
    )
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaxonomyCategory(Base):
    """Услуга × модификатор × метод — JSONB attrs for any niche (TZ §2)."""

    __tablename__ = "taxonomy_categories"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_taxonomy_tenant_slug"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    niche: Mapped[str] = mapped_column(String(128), nullable=False)  # ремонт / клининг / ...
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    service: Mapped[str] = mapped_column(String(255), nullable=False)  # базовая услуга
    modifier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    method: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attrs: Mapped[dict] = mapped_column(JSONB, default=dict)
    templates: Mapped[dict] = mapped_column(JSONB, default=dict)  # title/h1/faq seeds
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tenant: Mapped[Tenant] = relationship(back_populates="categories")


class GeoPlace(Base):
    """Unified geo hierarchy: country→region→city→district→street→metro→landmark."""

    __tablename__ = "geo_places"
    __table_args__ = (UniqueConstraint("kind", "external_id", name="uq_geo_kind_ext"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name_forms: Mapped[dict] = mapped_column(JSONB, default=dict)  # nom/gen/prep/dat/acc/ins
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("geo_places.id", ondelete="SET NULL"), nullable=True, index=True
    )
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)  # FIAS/GAR/OSM
    source: Mapped[str] = mapped_column(String(32), default="manual")  # fias|osm|geonames|manual
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    population: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attrs: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_validated: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CoverageGap(Base):
    __tablename__ = "coverage_gaps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    geo_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MorphCache(Base):
    __tablename__ = "morph_cache"
    __table_args__ = (UniqueConstraint("word_hash", name="uq_morph_word_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    word_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    lemma: Mapped[str] = mapped_column(String(255), nullable=False)
    forms: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OnboardingSession(Base):
    __tablename__ = "onboarding_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    step: Mapped[str] = mapped_column(String(64), default="niche")  # niche|geo|template|domain|build
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# Re-export phase-3 models
from app.models.blocks import (  # noqa: E402
    BlockKit,
    BlockKitItem,
    CompetitorScan,
    ContentBlock,
    KnowledgeDoc,
    MediaAsset,
    make_hash_class,
)
from app.models.ai import (  # noqa: E402
    ContentHash,
    DeadLetterJob,
    GenerationJob,
    LlmCache,
    PromptEntry,
)
from app.models.publish import (  # noqa: E402
    BulkOperation,
    Domain,
    Redirect,
    SiteBuild,
    SitePage,
)
from app.models.leads import AnalyticsEvent, Consent, Lead  # noqa: E402
from app.models.ops import ContentDecayEvent, FootprintAudit, SerpCheck, StagingApproval  # noqa: E402
from app.models.panel import ApiKey, Notification, Plugin, SavedView, WebhookSubscription  # noqa: E402

__all__ = [
    "AnalyticsEvent",
    "ApiKey",
    "AuditLog",
    "BlockKit",
    "BlockKitItem",
    "BulkOperation",
    "CompetitorScan",
    "Consent",
    "ContentBlock",
    "ContentDecayEvent",
    "ContentHash",
    "CoverageGap",
    "DeadLetterJob",
    "Domain",
    "DsarJob",
    "FinopsEntry",
    "FootprintAudit",
    "GenerationJob",
    "GeoPlace",
    "Keyword",
    "KnowledgeDoc",
    "Lead",
    "LlmCache",
    "MediaAsset",
    "MorphCache",
    "Notification",
    "OnboardingSession",
    "Plugin",
    "PromptEntry",
    "Redirect",
    "SavedView",
    "SerpCheck",
    "Site",
    "SiteBuild",
    "SitePage",
    "StagingApproval",
    "TaxonomyCategory",
    "Tenant",
    "User",
    "WebhookSubscription",
    "make_hash_class",
]
