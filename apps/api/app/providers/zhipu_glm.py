from __future__ import annotations

from app.providers.base import ProviderCapabilities, ProviderKind, ProviderModel
from app.providers.openai_compatible import OpenAICompatibleAdapter


class ZhipuGLMAdapter(OpenAICompatibleAdapter):
    """Direct adapter for Zhipu's GLM OpenAI-compatible chat API."""

    kind = ProviderKind.NATIVE

    def __init__(self, *, api_key: str, timeout: float = 60.0, client=None) -> None:
        super().__init__(
            provider_id="zhipu_glm",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            api_key=api_key,
            default_models=(
                ProviderModel(
                    provider_id="zhipu_glm",
                    model_id="glm-4-flash",
                    display_name="GLM-4-Flash",
                    capabilities=ProviderCapabilities(structured_output=True, streaming=True),
                    metadata_source="zhipu",
                ),
            ),
            timeout=timeout,
            client=client,
        )
