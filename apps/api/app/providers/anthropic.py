from __future__ import annotations

import json
from typing import Any

import httpx

from app.providers.base import (
    ProviderCapabilities,
    ProviderError,
    ProviderKind,
    ProviderModel,
    StructuredRequest,
    StructuredResponse,
    Usage,
)
from app.services.hardening import egress

MAX_RESPONSE_BYTES = 1_048_576


def _usage(data: dict[str, Any]) -> Usage:
    usage_data = data.get("usage")
    if not isinstance(usage_data, dict):
        return Usage()
    try:
        return Usage(
            input_tokens=int(usage_data.get("input_tokens") or 0),
            output_tokens=int(usage_data.get("output_tokens") or 0),
            reported=True,
        )
    except (TypeError, ValueError):
        return Usage()


class AnthropicAdapter:
    kind = ProviderKind.NATIVE
    provider_id = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-3-5-haiku-latest",
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self.default_model = model
        self._timeout = timeout
        self._client = client
        self._models = (
            ProviderModel(
                provider_id=self.provider_id,
                model_id=model,
                display_name=model,
                capabilities=ProviderCapabilities(structured_output=True, streaming=True),
            ),
        )

    def model_capabilities(self, model: str) -> ProviderCapabilities:
        return ProviderCapabilities(structured_output=True, streaming=True)

    async def generate_structured(self, request: StructuredRequest) -> StructuredResponse:
        try:
            egress.assert_allowed("https://api.anthropic.com/v1/messages")
            await egress.assert_public_dns("https://api.anthropic.com/v1/messages")
        except PermissionError as exc:
            raise ProviderError(
                "egress_blocked", "Provider endpoint failed the egress policy"
            ) from exc
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout, follow_redirects=False)
        try:
            async with client.stream(
                "POST",
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": request.model,
                    "max_tokens": request.max_tokens,
                    "system": request.system_prompt,
                    "messages": [{"role": "user", "content": request.user_prompt}],
                },
            ) as response:
                if response.status_code in {408, 409, 425, 429} or response.status_code >= 500:
                    raise ProviderError(
                        "upstream_retryable", "Anthropic returned a retryable error", retryable=True
                    )
                if response.status_code in {401, 403}:
                    raise ProviderError("unauthorized", "Provider credentials were rejected")
                if response.status_code >= 400:
                    raise ProviderError(
                        "upstream_error", f"Anthropic returned HTTP {response.status_code}"
                    )
                try:
                    content_length = int(response.headers.get("content-length", "0"))
                except ValueError as exc:
                    raise ProviderError(
                        "invalid_response", "Anthropic returned invalid response headers"
                    ) from exc
                if content_length > MAX_RESPONSE_BYTES:
                    raise ProviderError(
                        "response_too_large", "Provider response exceeded the size limit"
                    )
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise ProviderError(
                            "response_too_large", "Provider response exceeded the size limit"
                        )
                try:
                    data: dict[str, Any] = json.loads(raw)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise ProviderError(
                        "invalid_response", "Anthropic returned invalid JSON"
                    ) from exc
                request_id = response.headers.get("request-id")
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", "Provider request timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("network_error", "Provider request failed", retryable=True) from exc
        finally:
            if own_client:
                await client.aclose()
        usage = _usage(data)
        try:
            text = "".join(
                item.get("text", "")
                for item in data.get("content", [])
                if item.get("type") == "text"
            )
            result = json.loads(text)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ProviderError(
                "invalid_structured_output",
                "Provider returned invalid structured output",
                usage=usage,
                request_id=request_id,
            ) from exc
        if not isinstance(result, dict):
            raise ProviderError(
                "invalid_structured_output",
                "Provider output must be a JSON object",
                usage=usage,
                request_id=request_id,
            )
        return StructuredResponse(
            provider_id=self.provider_id,
            model=request.model,
            data=result,
            usage=usage,
            request_id=request_id,
        )

    async def list_models(self) -> list[ProviderModel]:
        return list(self._models)


__all__ = ["AnthropicAdapter"]
