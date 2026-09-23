from __future__ import annotations

from app.providers.base import ProviderKind
from app.providers.openai_compatible import OpenAICompatibleAdapter


class DeepSeekAdapter(OpenAICompatibleAdapter):
    kind = ProviderKind.NATIVE

    def __init__(
        self, *, api_key: str, model: str = "deepseek-chat", timeout: float = 60.0, client=None
    ) -> None:
        super().__init__(
            provider_id="deepseek",
            base_url="https://api.deepseek.com",
            api_key=api_key,
            default_models=(),
            timeout=timeout,
            client=client,
        )
        self._default_models = ()
        self.default_model = model
