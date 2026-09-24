from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ProviderCapabilitiesOut(BaseModel):
    structured_output: bool
    streaming: bool
    model_listing: bool
    max_context_tokens: int | None = None


class ProviderPricingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_price_usd_per_million: float = Field(ge=0, allow_inf_nan=False)
    output_price_usd_per_million: float = Field(ge=0, allow_inf_nan=False)
    is_free: bool | None = None
    source: str = Field(min_length=1, max_length=256)
    observed_at: datetime


class ProviderModelOut(BaseModel):
    provider_id: str
    model_id: str
    display_name: str
    capabilities: ProviderCapabilitiesOut
    input_price_usd_per_million: float | None = None
    output_price_usd_per_million: float | None = None
    is_free: bool | None = None
    metadata_source: str | None = None
    metadata_observed_at: str | None = None


class ProviderConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    kind: Literal["native", "openai_compatible"]
    base_url: HttpUrl | None = None
    api_key: str = Field(min_length=1, max_length=4096)
    models: list[str] = Field(default_factory=list, max_length=100)
    model_pricing: dict[str, ProviderPricingIn] = Field(default_factory=dict)


class ProviderConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=128)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    models: list[str] | None = Field(default=None, max_length=100)
    model_pricing: dict[str, ProviderPricingIn] | None = None


class ProviderConnectionOut(BaseModel):
    id: UUID
    provider_id: str
    label: str
    kind: Literal["native", "openai_compatible"]
    base_url: str | None
    credential_last4: str | None
    enabled: bool
    created_at: str | None
    updated_at: str | None


class ProviderTestOut(BaseModel):
    ok: bool
    provider_id: str
    code: str | None = None
    message: str | None = None


class ArchitectureProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_connection_id: UUID
    model: str = Field(min_length=1, max_length=256)
    confirm_external_processing: bool = False
    confirm_provider_budget: bool = False
    max_cost_usd: float = Field(gt=0, le=100, allow_inf_nan=False)
    max_output_tokens: int = Field(default=2048, ge=128, le=4096)
    operator_constraints: list[str] = Field(default_factory=list, max_length=50)
    regenerate_page_ids: list[UUID] = Field(default_factory=list, max_length=100)
    confirmed_estimated_cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    quote_snapshot_hash: str | None = Field(default=None, min_length=64, max_length=64)


class ArchitectureQuoteOut(BaseModel):
    provider_id: str
    model_id: str
    estimated_cost_usd: float
    max_cost_usd: float
    input_snapshot_hash: str
    pricing_source: str
    pricing_observed_at: str


class AIDraftGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_connection_id: UUID
    model: str = Field(min_length=1, max_length=256)
    max_cost_usd: float = Field(gt=0, le=100, allow_inf_nan=False)
    max_output_tokens: int = Field(default=2048, ge=128, le=4096)
    operator_confirmed_external_processing: bool = False
    operator_confirmed_provider_budget: bool = False
    confirmed_estimated_cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    quote_snapshot_hash: str | None = Field(default=None, min_length=64, max_length=64)


class AIDraftTextOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=70)
    h1: str = Field(min_length=1, max_length=255)
    meta_description: str = Field(min_length=1, max_length=170)
    unique_core: str = Field(min_length=1, max_length=8000)
    fact_keys: list[str] = Field(default_factory=list, max_length=100)


class AIRunOut(BaseModel):
    id: UUID
    action: str
    status: str
    provider_id: str | None
    model_id: str | None
    prompt_id: str
    prompt_version: str
    prompt_hash: str
    input_snapshot_hash: str
    output: dict
    usage: dict
    cost_usd: float | None
    error_code: str | None
    created_at: str | None

    model_config = ConfigDict(extra="forbid")


class AIRunSummary(BaseModel):
    id: UUID
    project_id: UUID | None
    action: str
    status: str
    provider_id: str | None
    model_id: str | None
    prompt_id: str
    prompt_version: str
    prompt_hash: str
    input_snapshot_hash: str
    usage: dict
    cost_usd: float | None
    error_code: str | None
    created_at: str | None


class SEOBriefOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=60)
    description: str = Field(min_length=1, max_length=160)
    h1: str = Field(min_length=1, max_length=255)
    canonical_path: str = Field(min_length=1, max_length=512)
    robots: Literal["index,follow", "noindex,follow"]
    keyword_ids: list[UUID] = Field(default_factory=list, max_length=100)
    fact_keys: list[str] = Field(default_factory=list, max_length=100)
    structured_data_types: list[str] = Field(default_factory=list, max_length=20)
    uncertainty_notes: list[str] = Field(default_factory=list, max_length=20)


class PageProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=255)
    purpose: str = Field(min_length=1, max_length=1000)
    slug: str = Field(min_length=1, max_length=160)
    keyword_ids: list[UUID] = Field(default_factory=list, max_length=100)
    geo_ids: list[UUID] = Field(default_factory=list, max_length=100)
    fact_keys: list[str] = Field(default_factory=list, max_length=100)
    kit_key: str = Field(min_length=1, max_length=160)
    block_ids: list[str] = Field(default_factory=list, max_length=100)
    uncertainty_notes: list[str] = Field(default_factory=list, max_length=20)


class ArchitectureProposalOut(BaseModel):
    run_id: UUID
    status: Literal["pending_approval", "approved", "rejected", "failed"]
    pages: list[PageProposal]
    prompt_id: str
    prompt_version: str
    prompt_hash: str
    input_snapshot_hash: str
    requires_operator_approval: Literal[True] = True
    estimated_cost_usd: float | None = None
    max_cost_usd: float | None = None
    page_plan_ids: list[UUID] = Field(default_factory=list)
    page_plans_imported: bool = False
    error_code: str | None = None
