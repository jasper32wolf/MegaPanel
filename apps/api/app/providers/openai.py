from __future__ import annotations

from app.providers.base import ProviderKind
from app.providers.openai_compatible import OpenAICompatibleAdapter


class OpenAIAdapter(OpenAICompatibleAdapter):
    kind = ProviderKind.NATIVE

    def __init__(
        self, *, api_key: str, model: str = "gpt-4o-mini", timeout: float = 60.0, client=None
    ) -> None:
        super().__init__(
            provider_id="openai",
            base_url="https://api.openai.com/v1",
            api_key=api_key,
            default_models=(),
            timeout=timeout,
            client=client,
        )
        self.default_model = model
