from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlparse

from pydantic import model_validator
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
    caddy_sites_root: str = "./data/sites"
    uploads_root: str = "./data/uploads"
    dsar_exports_root: str = "./data/dsar"
    dsar_export_ttl_hours: int = 24

    sentry_dsn: str = ""
    log_level: str = "INFO"

    # Optional GitHub Actions control plane for the authenticated operator UI.
    # Values remain VPS-local environment secrets and are never returned by API.
    github_repository: str = ""
    github_control_token: str = ""
    github_api_url: str = "https://api.github.com"

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
    ai_endpoint_allowlist: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def validate_production_settings(self) -> Settings:
        if self.app_env.lower() != "production":
            return self

        secrets = {
            "APP_SECRET_KEY": self.app_secret_key,
            "APP_PEPPER": self.app_pepper,
            "BLIND_INDEX_PEPPER": self.blind_index_pepper,
            "FIELD_ENCRYPTION_KEY": self.field_encryption_key,
        }
        invalid = [
            name
            for name, value in secrets.items()
            if len(value) < 32
            or any(marker in value.lower() for marker in ("change-me", "dev-secret", "dev-pepper"))
        ]
        if invalid:
            raise ValueError(f"Production secrets are missing or unsafe: {', '.join(invalid)}")

        public_urls = {
            "PANEL_PUBLIC_URL": self.panel_public_url,
            "API_PUBLIC_URL": self.api_public_url,
        }
        invalid_urls = [
            name
            for name, value in public_urls.items()
            if urlparse(value).scheme != "https" or not urlparse(value).netloc
        ]
        if invalid_urls:
            raise ValueError(f"Production URLs must use HTTPS: {', '.join(invalid_urls)}")

        if self.panel_public_url not in self.cors_origin_list:
            raise ValueError("CORS_ORIGINS must include PANEL_PUBLIC_URL in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
