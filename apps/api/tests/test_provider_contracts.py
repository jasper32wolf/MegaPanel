from __future__ import annotations

import asyncio

import httpx
import pytest
from app.providers import (
    OpenAICompatibleAdapter,
    ProviderCapabilities,
    ProviderError,
    ProviderModel,
    StructuredRequest,
    ZhipuGLMAdapter,
)
from app.services.hardening import egress


def request() -> StructuredRequest:
    return StructuredRequest(
        model="test-model",
        system_prompt="Return JSON",
        user_prompt="{}",
        output_schema={"type": "object"},
    )


def test_openai_compatible_parses_structured_output_and_usage(monkeypatch) -> None:
    monkeypatch.setattr(egress, "allowlist", egress.allowlist | {"provider.test"})

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-request-id": "req-1"},
            json={
                "id": "chat-1",
                "choices": [{"message": {"content": '{"pages": []}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            },
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = OpenAICompatibleAdapter(
                provider_id="gateway",
                base_url="https://provider.test/v1",
                api_key="secret-value",
                default_models=(
                    ProviderModel(
                        provider_id="gateway",
                        model_id="test-model",
                        display_name="Test",
                        capabilities=ProviderCapabilities(structured_output=True),
                    ),
                ),
                client=client,
            )
            result = await adapter.generate_structured(request())
            assert result.data == {"pages": []}
            assert result.usage.input_tokens == 12
            assert result.usage.output_tokens == 7
            assert result.request_id == "req-1"

    asyncio.run(run())


def test_provider_rejects_non_https_endpoint() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        OpenAICompatibleAdapter(provider_id="bad", base_url="http://provider.test/v1", api_key="x")


def test_provider_rejects_private_and_credentialed_endpoints() -> None:
    for endpoint in (
        "https://127.0.0.1/v1",
        "https://user:pass@provider.test/v1",
        "https://provider.test/v1?api_key=secret",
    ):
        with pytest.raises((PermissionError, ValueError)):
            OpenAICompatibleAdapter(provider_id="bad", base_url=endpoint, api_key="x")


def test_provider_normalizes_unauthorized_without_leaking_key(monkeypatch) -> None:
    monkeypatch.setattr(egress, "allowlist", egress.allowlist | {"provider.test"})

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "secret-value"})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = OpenAICompatibleAdapter(
                provider_id="gateway",
                base_url="https://provider.test/v1",
                api_key="secret-value",
                client=client,
            )
            with pytest.raises(ProviderError) as caught:
                await adapter.generate_structured(request())
            assert caught.value.code == "unauthorized"
            assert "secret-value" not in str(caught.value)

    asyncio.run(run())


def test_provider_rejects_oversized_response(monkeypatch) -> None:
    monkeypatch.setattr(egress, "allowlist", egress.allowlist | {"provider.test"})

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (1024 * 1024 + 1))

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = OpenAICompatibleAdapter(
                provider_id="gateway",
                base_url="https://provider.test/v1",
                api_key="test-key",
                client=client,
            )
            with pytest.raises(ProviderError) as caught:
                await adapter.generate_structured(request())
            assert caught.value.code == "response_too_large"

    asyncio.run(run())


def test_glm_declares_native_provider_and_model() -> None:
    adapter = ZhipuGLMAdapter(api_key="key")
    assert adapter.provider_id == "zhipu_glm"
    assert adapter.kind.value == "native"
    assert adapter.model_capabilities("glm-4-flash").structured_output
