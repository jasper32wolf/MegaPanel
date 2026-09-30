from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

MAX_OUTPUT_SCHEMA_BYTES = 64 * 1024
MAX_OUTPUT_SCHEMA_DEPTH = 12


class StructuredOutputMode(StrEnum):
    """The provider-native structured-output contract available for a model."""

    JSON_OBJECT = "json_object"
    JSON_SCHEMA = "json_schema"
    NATIVE = "native"
    UNSUPPORTED = "unsupported"


def normalize_output_schema(schema: object) -> dict[str, Any]:
    """Copy and bound a user schema before putting it on an upstream request."""
    if not isinstance(schema, dict):
        raise ValueError("Output schema must be a JSON object")

    def depth(value: object, level: int = 1) -> int:
        if isinstance(value, dict):
            if not value:
                return level
            return max(depth(item, level + 1) for item in value.values())
        if isinstance(value, list):
            if not value:
                return level
            return max(depth(item, level + 1) for item in value)
        return level

    try:
        encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        normalized = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Output schema must contain only JSON values") from exc
    if len(encoded.encode("utf-8")) > MAX_OUTPUT_SCHEMA_BYTES:
        raise ValueError("Output schema exceeds the size limit")
    if depth(normalized) > MAX_OUTPUT_SCHEMA_DEPTH:
        raise ValueError("Output schema exceeds the depth limit")
    if not isinstance(normalized, dict):
        raise ValueError("Output schema must be a JSON object")
    return normalized


class ProviderKind(StrEnum):
    NATIVE = "native"
    OPENAI_COMPATIBLE = "openai_compatible"


@dataclass(frozen=True)
class ProviderCapabilities:
    structured_output: bool = False
    structured_output_mode: StructuredOutputMode = StructuredOutputMode.JSON_OBJECT
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
