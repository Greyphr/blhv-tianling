"""Typed application settings loaded from environment variables and ``.env``."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for Tianling."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    discord_token: str
    guild_id: int
    database_url: str = "sqlite+aiosqlite:///data/tianling.db"
    log_level: str = "INFO"
    profile_stale_days: int = 30


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
