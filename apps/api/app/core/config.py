from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_secret_key: str = "dev-secret"
    app_pepper: str = "dev-pepper"
    blind_index_pepper: str = "dev-blind"
    field_encryption_key: str = "dev-field-encryption-key-change-me"
    cors_origins: str = "http://localhost:5173"
    panel_public_url: str = "http://localhost:5173"
    api_public_url: str = "http://localhost:8000"

    database_url: str = "postgresql+asyncpg://site_panel:site_panel_dev@localhost:5432/site_panel"
    redis_url: str = "redis://localhost:6379/0"
    caddy_admin_url: str = "http://localhost:2019"
    sites_root: str = "./data/sites"

    sentry_dsn: str = ""
    log_level: str = "INFO"

    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 14

    # LLM
    llm_default_provider: str = "deepseek"
    deepseek_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    llm_micro_model_deepseek: str = "deepseek-chat"
    llm_micro_model_anthropic: str = "claude-3-5-haiku-latest"
    llm_micro_model_openai: str = "gpt-4o-mini"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
