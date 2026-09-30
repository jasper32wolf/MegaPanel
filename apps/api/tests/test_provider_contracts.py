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
            assert result.usage.known is True
            assert result.usage.input_tokens == 12
            assert result.usage.output_tokens == 7
            assert result.request_id == "req-1"

    asyncio.run(run())


def test_provider_marks_missing_usage_as_unknown(monkeypatch) -> None:
    monkeypatch.setattr(egress, "allowlist", egress.allowlist | {"provider.test"})

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"pages": []}'}}]},
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = OpenAICompatibleAdapter(
                provider_id="gateway",
                base_url="https://provider.test/v1",
                api_key="secret-value",
                client=client,
            )
            result = await adapter.generate_structured(request())
            assert result.usage.known is False
            assert result.usage.input_tokens == 0
            assert result.usage.output_tokens == 0

    asyncio.run(run())


def test_provider_keeps_explicit_zero_usage_as_known(monkeypatch) -> None:
    monkeypatch.setattr(egress, "allowlist", egress.allowlist | {"provider.test"})

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"pages": []}'}}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            },
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = OpenAICompatibleAdapter(
                provider_id="gateway",
                base_url="https://provider.test/v1",
                api_key="secret-value",
                client=client,
            )
            result = await adapter.generate_structured(request())
            assert result.usage.known is True
            assert result.usage.input_tokens == 0
            assert result.usage.output_tokens == 0

    asyncio.run(run())


def test_provider_preserves_usage_when_structured_output_is_invalid(monkeypatch) -> None:
    monkeypatch.setattr(egress, "allowlist", egress.allowlist | {"provider.test"})

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-request-id": "req-malformed"},
            json={
                "id": "chat-malformed",
                "choices": [{"message": {"content": "not-json"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            },
        )

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

            assert caught.value.code == "invalid_structured_output"
            assert caught.value.request_id == "req-malformed"
            assert caught.value.usage is not None
            assert caught.value.usage.known is True
            assert caught.value.usage.input_tokens == 12
            assert caught.value.usage.output_tokens == 7

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


def test_openai_compatible_discovers_bounded_models_with_get_headers_and_path(monkeypatch) -> None:
    monkeypatch.setattr(egress, "allowlist", egress.allowlist | {"provider.test"})
    observed: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(
            200,
            json={"data": [{"id": " z-model "}, {"id": "z-model"}, {"id": "bad\\nmodel"}, {"id": 4}]},
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = OpenAICompatibleAdapter(
                provider_id="gateway",
                base_url="https://provider.test/v1",
                api_key="secret-value",
                client=client,
            )
            result = await adapter.list_models()
            assert [model.model_id for model in result] == ["z-model"]
            assert result[0].metadata_source == "provider_discovery"

    asyncio.run(run())
    assert observed[0].method == "GET"
    assert observed[0].url.path == "/v1/models"
    assert observed[0].headers["authorization"] == "Bearer secret-value"
    assert observed[0].headers["accept"] == "application/json"
    assert "content-type" not in observed[0].headers


def test_openai_compatible_discovery_caps_models_and_rejects_invalid_shape(monkeypatch) -> None:
    monkeypatch.setattr(egress, "allowlist", egress.allowlist | {"provider.test"})

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": f"model-{index}"} for index in range(150)]})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = OpenAICompatibleAdapter(
                provider_id="gateway", base_url="https://provider.test/v1", api_key="key", client=client
            )
            assert len(await adapter.list_models()) == 100

    asyncio.run(run())

    async def malformed(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"id": "model-a"}})

    async def check_malformed() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(malformed)) as client:
            adapter = OpenAICompatibleAdapter(
                provider_id="gateway", base_url="https://provider.test/v1", api_key="key", client=client
            )
            with pytest.raises(ProviderError, match="invalid model list"):
                await adapter.list_models()

    asyncio.run(check_malformed())


def test_openai_compatible_discovery_normalizes_errors_without_secrets(monkeypatch) -> None:
    monkeypatch.setattr(egress, "allowlist", egress.allowlist | {"provider.test"})

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "secret-value"})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = OpenAICompatibleAdapter(
                provider_id="gateway", base_url="https://provider.test/v1", api_key="secret-value", client=client
            )
            with pytest.raises(ProviderError) as caught:
                await adapter.list_models()
            assert caught.value.code == "unauthorized"
            assert "secret-value" not in str(caught.value)

    asyncio.run(run())


def test_native_openai_compatible_adapter_keeps_static_models(monkeypatch) -> None:
    adapter = ZhipuGLMAdapter(api_key="key")
    original = adapter._request

    async def fail(*_args, **_kwargs):
        raise AssertionError("native static listing must not call /models")

    adapter._request = fail
    try:
        result = asyncio.run(adapter.list_models())
    finally:
        adapter._request = original
    assert [model.model_id for model in result] == ["glm-4-flash"]


def test_glm_declares_native_provider_and_model() -> None:
    adapter = ZhipuGLMAdapter(api_key="key")
    assert adapter.provider_id == "zhipu_glm"
    assert adapter.kind.value == "native"
    assert adapter.model_capabilities("glm-4-flash").structured_output
