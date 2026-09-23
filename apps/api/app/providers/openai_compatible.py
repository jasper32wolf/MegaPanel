from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

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

Transport = Callable[[httpx.Request], Awaitable[httpx.Response]]
MAX_RESPONSE_BYTES = 1_048_576


class OpenAICompatibleAdapter:
    kind = ProviderKind.OPENAI_COMPATIBLE

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        api_key: str,
        default_models: tuple[ProviderModel, ...] = (),
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Provider endpoint must be an HTTPS URL")
        if parsed.username or parsed.password:
            raise ValueError("Provider endpoint must not contain credentials")
        egress.assert_safe_endpoint(base_url)
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_models = default_models
        self._timeout = timeout
        self._client = client

    def model_capabilities(self, model: str) -> ProviderCapabilities:
        for item in self._default_models:
            if item.model_id == model:
                return item.capabilities
        return ProviderCapabilities(structured_output=True)

    async def _request(self, payload: dict[str, Any]) -> tuple[dict[str, Any], httpx.Response]:
        try:
            egress.assert_allowed(self.base_url)
            if self._client is None:
                await egress.assert_public_dns(self.base_url)
        except PermissionError as exc:
            raise ProviderError(
                "egress_blocked", "Provider endpoint failed the egress policy"
            ) from exc
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout, follow_redirects=False)
        try:
            async with client.stream(
                "POST", f"{self.base_url}/chat/completions", headers=headers, json=payload
            ) as response:
                try:
                    content_length = int(response.headers.get("content-length", "0"))
                except ValueError as exc:
                    raise ProviderError(
                        "invalid_response", "Provider returned invalid response headers"
                    ) from exc
                if content_length > MAX_RESPONSE_BYTES:
                    raise ProviderError(
                        "response_too_large", "Provider response exceeded the size limit"
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise ProviderError(
                            "response_too_large", "Provider response exceeded the size limit"
                        )
                if response.status_code in {408, 409, 425, 429} or response.status_code >= 500:
                    raise ProviderError(
                        "upstream_retryable",
                        f"Provider returned HTTP {response.status_code}",
                        retryable=True,
                    )
                if response.status_code in {401, 403}:
                    raise ProviderError("unauthorized", "Provider credentials were rejected")
                if response.status_code >= 400:
                    raise ProviderError(
                        "upstream_error", f"Provider returned HTTP {response.status_code}"
                    )
                try:
                    data = json.loads(body)
                except (ValueError, json.JSONDecodeError) as exc:
                    raise ProviderError(
                        "invalid_response", "Provider returned invalid JSON"
                    ) from exc
                return data, response
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", "Provider request timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("network_error", "Provider request failed", retryable=True) from exc
        finally:
            if own_client:
                await client.aclose()

    async def generate_structured(self, request: StructuredRequest) -> StructuredResponse:
        capabilities = self.model_capabilities(request.model)
        if not capabilities.structured_output:
            raise ProviderError(
                "unsupported_capability", "Selected model does not support structured output"
            )
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "response_format": {"type": "json_object"},
        }
        data, response = await self._request(payload)
        try:
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("content is not text")
            result = json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderError(
                "invalid_structured_output", "Provider returned invalid structured output"
            ) from exc
        if not isinstance(result, dict):
            raise ProviderError(
                "invalid_structured_output", "Provider output must be a JSON object"
            )
        usage_data = data.get("usage") or {}
        return StructuredResponse(
            provider_id=self.provider_id,
            model=request.model,
            data=result,
            usage=Usage(
                input_tokens=int(
                    usage_data.get("prompt_tokens") or usage_data.get("input_tokens") or 0
                ),
                output_tokens=int(
                    usage_data.get("completion_tokens") or usage_data.get("output_tokens") or 0
                ),
            ),
            request_id=response.headers.get("x-request-id") or data.get("id"),
        )

    async def list_models(self) -> list[ProviderModel]:
        return list(self._default_models)
