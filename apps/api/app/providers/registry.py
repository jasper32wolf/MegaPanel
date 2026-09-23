from __future__ import annotations

from collections.abc import Callable

from app.providers.base import ProviderAdapter


class ProviderRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., ProviderAdapter]] = {}

    def register(self, provider_id: str, factory: Callable[..., ProviderAdapter]) -> None:
        if not provider_id or provider_id in self._factories:
            raise ValueError("Provider ID must be unique and non-empty")
        self._factories[provider_id] = factory

    def create(self, provider_id: str, **config: object) -> ProviderAdapter:
        try:
            factory = self._factories[provider_id]
        except KeyError as exc:
            raise KeyError(f"Unknown provider: {provider_id}") from exc
        return factory(**config)

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))


registry = ProviderRegistry()


def register_builtin_providers(target: ProviderRegistry = registry) -> None:
    from app.providers.anthropic import AnthropicAdapter
    from app.providers.deepseek import DeepSeekAdapter
    from app.providers.openai import OpenAIAdapter
    from app.providers.zhipu_glm import ZhipuGLMAdapter

    for provider_id, factory in (
        ("anthropic", AnthropicAdapter),
        ("deepseek", DeepSeekAdapter),
        ("openai", OpenAIAdapter),
        ("zhipu_glm", ZhipuGLMAdapter),
    ):
        if provider_id not in target.ids():
            target.register(provider_id, factory)
