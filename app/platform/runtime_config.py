from __future__ import annotations

import secrets
from collections.abc import Iterable

from app.control.repository import AppRepository
from app.platform.config import Settings


PERSISTED_CONFIG_KEYS = {
    "admin_token",
    "proxy_api_keys",
    "upstream_base_url",
    "request_timeout_seconds",
    "max_upstream_attempts",
    "request_log_retention",
    "allow_unknown_model_passthrough",
    "affinity_hash_secret",
    "cooldown_rate_limit_seconds",
    "cooldown_5xx_seconds",
    "cooldown_network_seconds",
    "cooldown_long_seconds",
    "transform_debug_enabled",
    "transform_debug_retention",
    "transform_debug_level",
}


def apply_persisted_config_overrides(settings: Settings, repository: AppRepository) -> None:
    for item in repository.list_settings():
        key = item["key"]
        if key in PERSISTED_CONFIG_KEYS:
            setattr(settings, key, item["value"])


def ensure_affinity_hash_secret(settings: Settings, repository: AppRepository) -> None:
    existing = repository.get_setting("affinity_hash_secret")
    if existing and existing.get("value"):
        settings.affinity_hash_secret = existing["value"]
        return

    value = getattr(settings, "affinity_hash_secret", None) or secrets.token_urlsafe(32)
    settings.affinity_hash_secret = value
    repository.upsert_setting("affinity_hash_secret", value)


def normalize_string_list(value: object) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        parts = value.replace("\n", ",").split(",")
        return [part.strip() for part in parts if part.strip()]
    if isinstance(value, Iterable):
        return [str(item).strip() for item in value if str(item).strip()]
    raise TypeError("expected list or string")
