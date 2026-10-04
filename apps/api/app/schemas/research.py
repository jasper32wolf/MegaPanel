from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class CompetitorCrawlCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_url: HttpUrl
    terms_acknowledged: bool
    max_pages: int = Field(default=100, ge=1, le=500)
    max_depth: int = Field(default=8, ge=0, le=12)

    @field_validator("root_url")
    @classmethod
    def require_https_root(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("Competitor research requires an HTTPS root URL")
        if value.username or value.password or value.query or value.fragment:
            raise ValueError("Root URL cannot contain credentials, query, or fragment")
        if value.port not in {None, 443}:
            raise ValueError("Competitor research only allows HTTPS port 443")
        return value

    @field_validator("terms_acknowledged")
    @classmethod
    def require_terms_acknowledgement(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Confirm responsibility for the supplied public domain")
        return value


class CompetitorCrawlOut(BaseModel):
    id: UUID
    project_id: UUID
    root_url: str
    origin: str
    status: Literal["queued", "running", "partial", "done", "blocked", "failed", "cancelled"]
    configuration: dict
    progress: dict
    coverage: dict
    error_code: str | None
    error_message: str | None
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class CompetitorCrawlPageOut(BaseModel):
    id: UUID
    url: str
    canonical_url: str | None
    parent_url: str | None
    discovery_source: str
    depth: int
    status: Literal["queued", "fetched", "skipped", "failed"]
    http_status: int | None
    signals: dict
    warnings: list
    error_code: str | None
    fetched_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class CompetitorCrawlCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


class BukvarixImportPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domains: list[str] = Field(min_length=1, max_length=10)

    @field_validator("domains")
    @classmethod
    def validate_domains(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().lower().rstrip(".") for item in value]
        if any(not item or "://" in item or "/" in item for item in normalized):
            raise ValueError("Provide hostnames only, without protocol or path")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Domains must be unique")
        return normalized


class BukvarixProviderStatus(BaseModel):
    enabled: Literal[True] = True
    status: Literal["https_public_free"] = "https_public_free"
    message: str
    personal_credentials_supported: Literal[False] = False
    max_seed_keywords: int
    max_results_per_run: int
    non_publish_policy: Literal[True] = True


class BukvarixKeywordRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed_project_keyword_ids: list[UUID] = Field(min_length=1, max_length=10)
    confirm_https_public_free: Literal[True]

    @model_validator(mode="after")
    def require_unique_seeds(self) -> BukvarixKeywordRunCreate:
        if len(self.seed_project_keyword_ids) != len(set(self.seed_project_keyword_ids)):
            raise ValueError("Bukvarix seed keyword selection contains duplicates")
        return self


class BukvarixKeywordResultOut(BaseModel):
    id: UUID
    source_project_keyword_id: UUID
    phrase: str
    metrics: list[int | float]

    model_config = ConfigDict(from_attributes=True)


class BukvarixKeywordRunOut(BaseModel):
    id: UUID
    project_id: UUID
    status: Literal["queued", "running", "completed", "failed"]
    provider_mode: Literal["https_public_free"]
    query_count: int
    result_count: int
    failure_code: str | None
    committed_source_run_id: UUID | None
    queued_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime | None
    results: list[BukvarixKeywordResultOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class BukvarixKeywordRunCommit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_result_ids: list[UUID] = Field(min_length=1, max_length=500)
    confirm_import_https_public_free: Literal[True]

    @model_validator(mode="after")
    def require_unique_results(self) -> BukvarixKeywordRunCommit:
        if len(self.selected_result_ids) != len(set(self.selected_result_ids)):
            raise ValueError("Bukvarix selected results contain duplicates")
        return self


class BukvarixKeywordRunCommitOut(BaseModel):
    source_run_id: UUID
    created_keywords: int
    existing_keywords: int
    linked_project_keywords: int


class SemanticSourceRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["bukvarix"]
    acquisition: Literal["manual_export"]
    mode: Literal["domain", "compare", "multi_domain"]
    source_label: str = Field(min_length=2, max_length=255)
    observed_at: datetime
    notes: str | None = Field(default=None, max_length=4000)
    project_keyword_ids: list[UUID] = Field(min_length=1, max_length=1000)
    confirm_record_manual_export: Literal[True]

    @field_validator("source_label", "notes")
    @classmethod
    def reject_urls_and_secrets(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if "://" in normalized:
            raise ValueError("Manual source metadata cannot contain a URL")
        credential_pattern = r"(?i)api[_ -]?key|authorization|bearer\s+|password\s*=|token\s*="
        if re.search(credential_pattern, normalized):
            raise ValueError("Manual source metadata cannot contain credentials")
        return normalized or None

    @model_validator(mode="after")
    def require_unique_project_keywords(self) -> SemanticSourceRunCreate:
        if len(self.project_keyword_ids) != len(set(self.project_keyword_ids)):
            raise ValueError("Manual source keyword selection contains duplicates")
        return self


class SemanticSourceRunOut(BaseModel):
    id: UUID
    project_id: UUID
    provider: Literal["bukvarix"]
    acquisition: Literal["manual_export", "https_public_free"]
    mode: Literal["domain", "compare", "multi_domain"]
    source_label: str
    observed_at: datetime
    notes: str | None
    selected_keyword_count: int
    created_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class StructureSourceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_collection_id: UUID
    competitor_crawl_ids: list[UUID] = Field(default_factory=list, max_length=20)
    keyword_source_run_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_research_source(self) -> StructureSourceSelection:
        if not self.competitor_crawl_ids and not self.keyword_source_run_ids:
            raise ValueError("Select at least one prepared research source")
        return self
