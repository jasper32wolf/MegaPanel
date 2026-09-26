from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class ProviderKind(StrEnum):
    NATIVE = "native"
    OPENAI_COMPATIBLE = "openai_compatible"


@dataclass(frozen=True)
class ProviderCapabilities:
    structured_output: bool = False
    streaming: bool = False
    model_listing: bool = False
    max_context_tokens: int | None = None


@dataclass(frozen=True)
class ProviderModel:
    provider_id: str
    model_id: str
    display_name: str
    capabilities: ProviderCapabilities
    input_price_usd_per_million: float | None = None
    output_price_usd_per_million: float | None = None
    is_free: bool | None = None
    metadata_source: str | None = None
    metadata_observed_at: str | None = None


@dataclass(frozen=True)
class StructuredRequest:
    model: str
    system_prompt: str
    user_prompt: str
    output_schema: dict[str, Any]
    temperature: float = 0.2
    max_tokens: int = 2048


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    reported: bool = False

    @property
    def known(self) -> bool:
        return self.reported


@dataclass(frozen=True)
class StructuredResponse:
    provider_id: str
    model: str
    data: dict[str, Any]
    usage: Usage = field(default_factory=Usage)
    request_id: str | None = None


class ProviderError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        usage: Usage | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.usage = usage
        self.request_id = request_id


class ProviderAdapter(Protocol):
    provider_id: str
    kind: ProviderKind

    async def generate_structured(self, request: StructuredRequest) -> StructuredResponse: ...

    async def list_models(self) -> list[ProviderModel]: ...

    def model_capabilities(self, model: str) -> ProviderCapabilities: ...
