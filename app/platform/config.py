from __future__ import annotations

import json
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        enable_decoding=False,
    )

    app_name: str = "fireworks2api"
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = Path("data")
    database_path: Path | None = None

    upstream_base_url: str = "https://api.fireworks.ai/inference/v1"
    request_timeout_seconds: float = 120.0
    max_upstream_attempts: int = 3

    fireworks_api_keys: list[str] = Field(default_factory=list)
    fireworks_api_keys_json: list[dict[str, str]] = Field(default_factory=list)
    sync_env_keys_on_startup: bool = False

    admin_token: str | None = None
    proxy_api_keys: list[str] = Field(default_factory=list)
    cors_allow_origins: list[str] = Field(default_factory=list)
    enable_admin_static: bool = False

    request_log_retention: int = 1000
    allow_unknown_model_passthrough: bool = False
    responses_cache_fields_enabled: bool = False
    log_hash_secret: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    affinity_hash_secret: str | None = None

    transform_debug_enabled: bool = False
    transform_debug_retention: int = 50
    transform_debug_level: str = "summary"
    anthropic_messages_mode: str = "native"

    cooldown_rate_limit_seconds: int = 60
    cooldown_5xx_seconds: int = 20
    cooldown_network_seconds: int = 30
    cooldown_long_seconds: int = 3600

    debug_log_bodies: bool = False

    @field_validator("fireworks_api_keys", "proxy_api_keys", "cors_allow_origins", mode="before")
    @classmethod
    def parse_csv(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("fireworks_api_keys_json", mode="before")
    @classmethod
    def parse_keys_json(cls, value: Any) -> list[dict[str, str]]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            parsed = json.loads(value)
            if not isinstance(parsed, list):
                raise ValueError("FIREWORKS_API_KEYS_JSON must be a JSON array")
            return parsed
        return value

    @property
    def db_path(self) -> Path:
        return self.database_path or self.data_dir / "fireworks2api.sqlite3"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    return settings


__all__ = ["Settings", "get_settings"]
