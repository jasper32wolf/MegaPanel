from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql+asyncpg://site_panel:site_panel_dev@localhost:5432/site_panel"
    sites_root: str = "./data/sites"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
