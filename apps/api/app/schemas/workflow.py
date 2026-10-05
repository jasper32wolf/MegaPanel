from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from site_panel_shared.phone import normalize_phone_e164

ProjectStatus = Literal["draft", "active", "archived"]
FactState = Literal["draft", "confirmed"]
PagePlanState = Literal["draft", "review", "approved", "rejected"]
PageDraftState = Literal["queued", "generating", "draft", "review", "applied", "rejected", "failed"]
QaVerdict = Literal["pass", "warn", "block"]
_PAGE_PATH = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*")
PROTECTED_CONTACT_FIELDS = frozenset({"webhook_url", "webhook_secret", "webhook_secret_enc"})


def normalize_page_plan_slug(value: str) -> str:
    path = value.strip().strip("/").lower()
    if not path:
        return "/"
    if not _PAGE_PATH.fullmatch(path):
        raise ValueError("Page path must use lowercase Latin letters, digits, hyphens and slashes")
    return f"/{path}"


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


class PublicContacts(BaseModel):
    phone: str | None = Field(default=None, min_length=5, max_length=64)
    address: str | None = Field(default=None, max_length=500)
    work_hours: str | None = Field(default=None, max_length=500)

    @field_validator("phone")
    @classmethod
    def normalize_unambiguous_russian_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_phone_e164(value) or value.strip()


class LegalProfile(BaseModel):
    org: str | None = Field(default=None, max_length=255)
    privacy_email: EmailStr | None = None
    inn: str | None = Field(default=None, max_length=32)
    ogrn: str | None = Field(default=None, max_length=32)
    address: str | None = Field(default=None, max_length=500)
    jurisdiction: str | None = Field(default=None, max_length=128)


class BusinessProfile(BaseModel):
    organization: str | None = Field(default=None, max_length=255)
    brand: str | None = Field(default=None, max_length=255)
    service: str | None = Field(default=None, max_length=255)
    services: list[str] = Field(default_factory=list, max_length=30)
    contacts: PublicContacts = Field(default_factory=PublicContacts)
    legal: LegalProfile = Field(default_factory=LegalProfile)
    company_history: str | None = Field(default=None, max_length=4000)
    mission: str | None = Field(default=None, max_length=2000)
    legal_entities: str | None = Field(default=None, max_length=2000)
    payment_terms: str | None = Field(default=None, max_length=2000)
    allowed_claims: list[str] = Field(default_factory=list, max_length=100)
    unique_core: str | None = Field(default=None, max_length=4000)

    @field_validator("services", "allowed_claims")
    @classmethod
    def normalize_text_list(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


def normalize_business_profile(value: dict) -> dict:
    facts = dict(value or {})
    if "private_lead_email" in facts:
        raise ValueError("Configure the private lead email through the dedicated field")
    contacts = dict(facts.get("contacts") or {})
    if PROTECTED_CONTACT_FIELDS.intersection(contacts):
        raise ValueError("Configure webhook credentials through site webhook settings")
    if {"email", "private_lead_email"}.intersection(contacts):
        raise ValueError("Configure the private lead email separately from public contacts")
    legal = dict(facts.get("legal") or {})
    if legal.get("operator") and not legal.get("org"):
        legal["org"] = legal["operator"]
    facts["contacts"] = contacts
    facts["legal"] = legal
    profile = BusinessProfile.model_validate(facts)
    normalized = profile.model_dump(mode="json", exclude_none=True)
    return {
        **facts,
        **normalized,
        "contacts": normalized.get("contacts", {}),
        "legal": normalized.get("legal", {}),
    }


class FactRevisionCreate(BaseModel):
    facts: dict = Field(default_factory=dict)
    private_lead_email: EmailStr | None = None
    source_notes: str | None = Field(default=None, max_length=4000)

    @field_validator("facts")
    @classmethod
    def require_business_facts(cls, value: dict) -> dict:
        if not value:
            raise ValueError("Provide confirmed business facts")
        return normalize_business_profile(value)


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


class SemanticGeoBindingIn(BaseModel):
    model_config = {"extra": "forbid"}

    project_geo_place_id: UUID
    scope: Literal["primary", "service_area"] = "service_area"
    notes: str | None = Field(default=None, max_length=2000)


class SemanticCollectionKeywordIn(BaseModel):
    model_config = {"extra": "forbid"}

    project_keyword_id: UUID
    cluster: str | None = Field(default=None, max_length=255)
    intent: str | None = Field(default=None, max_length=128)
    priority: int | None = Field(default=None, ge=0, le=100)
    notes: str | None = Field(default=None, max_length=2000)
    geo_bindings: list[SemanticGeoBindingIn] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def require_unique_geo_bindings(self) -> SemanticCollectionKeywordIn:
        ids = [item.project_geo_place_id for item in self.geo_bindings]
        if len(ids) != len(set(ids)):
            raise ValueError("Semantic keyword geography contains duplicates")
        return self


class SemanticCollectionCreate(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=50)
    manual_source_run_ids: list[UUID] = Field(default_factory=list, max_length=20)
    members: list[SemanticCollectionKeywordIn] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def require_unique_members_and_evidence(self) -> SemanticCollectionCreate:
        member_ids = [item.project_keyword_id for item in self.members]
        if len(member_ids) != len(set(member_ids)):
            raise ValueError("Semantic collection contains duplicate project keywords")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("Semantic collection contains duplicate evidence")
        if len(self.manual_source_run_ids) != len(set(self.manual_source_run_ids)):
            raise ValueError("Semantic collection contains duplicate manual source runs")
        return self


class SemanticCollectionUpdate(SemanticCollectionCreate):
    version: int = Field(ge=1)


class SemanticCollectionDecision(BaseModel):
    model_config = {"extra": "forbid"}

    reason: str | None = Field(default=None, max_length=4000)


class SemanticPlanTargetItem(BaseModel):
    model_config = {"extra": "forbid"}

    collection_keyword_id: UUID
    geo_binding_ids: list[UUID] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_unique_geo_bindings(self) -> SemanticPlanTargetItem:
        if len(self.geo_binding_ids) != len(set(self.geo_binding_ids)):
            raise ValueError("Semantic plan target contains duplicate geo bindings")
        return self


class SemanticPlanTargetIn(BaseModel):
    model_config = {"extra": "forbid"}

    collection_id: UUID
    targets: list[SemanticPlanTargetItem] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_unique_members(self) -> SemanticPlanTargetIn:
        ids = [item.collection_keyword_id for item in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("Semantic plan target contains duplicate collection keywords")
        return self


class ClaimSlotBinding(BaseModel):
    model_config = {"extra": "forbid"}

    block_id: str = Field(min_length=1, max_length=128)
    slot: str = Field(min_length=1, max_length=128)
    claim_index: int = Field(ge=0, le=99)


class PagePlanCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=512)
    objective: str = Field(min_length=3, max_length=512)
    intent: str | None = Field(default=None, max_length=128)
    risk_notes: str | None = Field(default=None, max_length=4000)
    kit_key: str = Field(min_length=2, max_length=128)
    block_selection: dict = Field(default_factory=dict)
    claim_slot_bindings: list[ClaimSlotBinding] = Field(default_factory=list, max_length=20)
    source_refs: dict = Field(default_factory=dict)
    semantic_target: SemanticPlanTargetIn | None = None

    @model_validator(mode="after")
    def require_unique_claim_slot_targets(self) -> PagePlanCreate:
        targets = [(item.block_id, item.slot) for item in self.claim_slot_bindings]
        if len(targets) != len(set(targets)):
            raise ValueError("Claim slot bindings contain duplicate targets")
        claim_indices = [item.claim_index for item in self.claim_slot_bindings]
        if len(claim_indices) != len(set(claim_indices)):
            raise ValueError("Claim slot bindings contain duplicate claims")
        return self

    @field_validator("slug")
    @classmethod
    def normalize_slug(cls, value: str) -> str:
        return normalize_page_plan_slug(value)


class PagePlanUpdate(BaseModel):
    objective: str | None = Field(default=None, min_length=3, max_length=512)
    intent: str | None = Field(default=None, max_length=128)
    risk_notes: str | None = Field(default=None, max_length=4000)
    kit_key: str | None = Field(default=None, min_length=2, max_length=128)
    block_selection: dict | None = None
    claim_slot_bindings: list[ClaimSlotBinding] | None = Field(default=None, max_length=20)
    source_refs: dict | None = None
    semantic_target: SemanticPlanTargetIn | None = None
    version: int = Field(ge=1)

    @model_validator(mode="after")
    def require_unique_claim_slot_targets(self) -> PagePlanUpdate:
        targets = [(item.block_id, item.slot) for item in self.claim_slot_bindings or []]
        if len(targets) != len(set(targets)):
            raise ValueError("Claim slot bindings contain duplicate targets")
        claim_indices = [item.claim_index for item in self.claim_slot_bindings or []]
        if len(claim_indices) != len(set(claim_indices)):
            raise ValueError("Claim slot bindings contain duplicate claims")
        return self


class PagePlanDecision(BaseModel):
    reason: str | None = Field(default=None, max_length=4000)


class PageDraftRequest(BaseModel):
    pass


class PageDraftMediaAttachIn(BaseModel):
    asset_id: UUID
    alt: str = Field(min_length=1, max_length=255)


class PageDraftBlockMediaAttachIn(PageDraftMediaAttachIn):
    model_config = {"extra": "forbid"}

    block_id: str = Field(min_length=1, max_length=128)


class QaOverrideIn(BaseModel):
    reason: Literal["operator_review", "known_exception", "approved_legal_copy"]
    justification: str = Field(min_length=10, max_length=2000)


class PageDraftDecision(BaseModel):
    reason: str | None = Field(default=None, max_length=4000)


class BuildLegalReviewIn(BaseModel):
    decision: Literal["approved", "rejected"]
    evidence_ref: str = Field(min_length=3, max_length=255)
    reason: str | None = Field(default=None, min_length=3, max_length=2000)
    replacement_guidance: str | None = Field(default=None, min_length=3, max_length=2000)

    @model_validator(mode="after")
    def require_rejection_context(self):
        if self.decision != "rejected":
            return self
        if not self.reason or not self.reason.strip():
            raise ValueError("Rejected legal review requires a reason")
        if not self.replacement_guidance or not self.replacement_guidance.strip():
            raise ValueError("Rejected legal review requires remediation guidance")
        return self


class PageIndexPromotionIn(BaseModel):
    slug: str = Field(min_length=1, max_length=512)
    reason: str = Field(min_length=10, max_length=2000)
    confirmed: bool

    @field_validator("slug")
    @classmethod
    def normalize_slug(cls, value: str) -> str:
        return normalize_page_plan_slug(value)


class CandidateBuildRequest(BaseModel):
    """Scheduling metadata only; a queued build is never a publication request."""

    queue_priority: int = Field(default=50, ge=0, le=100)
    not_before: datetime | None = None

    @field_validator("not_before")
    @classmethod
    def require_bounded_aware_schedule(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Build schedule must include a timezone")
        value = value.astimezone(UTC)
        if value > datetime.now(UTC) + timedelta(days=90):
            raise ValueError("Build schedule cannot be more than 90 days ahead")
        return value


class IndexPromotionScheduleCreate(BaseModel):
    model_config = {"extra": "forbid"}

    slugs: list[str] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=10, max_length=2000)
    starts_at: datetime
    interval_hours: int = Field(default=24, ge=1, le=720)
    batch_size: int = Field(default=1, ge=1, le=20)

    @field_validator("slugs")
    @classmethod
    def normalize_unique_slugs(cls, values: list[str]) -> list[str]:
        normalized = [normalize_page_plan_slug(value) for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Index schedule contains duplicate pages")
        return normalized

    @field_validator("starts_at")
    @classmethod
    def require_schedule_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Index schedule must include a timezone")
        value = value.astimezone(UTC)
        if value > datetime.now(UTC) + timedelta(days=365):
            raise ValueError("Index schedule cannot be more than one year ahead")
        return value


class IndexPromotionScheduleDecision(BaseModel):
    model_config = {"extra": "forbid"}

    confirmed: bool
    reason: str | None = Field(default=None, max_length=2000)


class BuildPublishRequest(BaseModel):
    confirmed: bool


class BuildRollbackRequest(BaseModel):
    build_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    confirmation_text: str = Field(min_length=73, max_length=73)


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
