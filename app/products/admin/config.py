from __future__ import annotations

import os
import sys
from typing import Any

import secrets

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.platform.runtime_config import normalize_string_list

from .deps import _repository, _settings

router = APIRouter()


class AdminConfigPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    admin_token: str | None = None
    proxy_api_keys: list[str] | str | None = None
    upstream_base_url: str | None = Field(default=None, min_length=1)
    request_timeout_seconds: float | None = Field(default=None, gt=0)
    max_upstream_attempts: int | None = Field(default=None, ge=1)
    request_log_retention: int | None = Field(default=None, ge=1)
    allow_unknown_model_passthrough: bool | None = None
    cooldown_rate_limit_seconds: int | None = Field(default=None, ge=0)
    cooldown_5xx_seconds: int | None = Field(default=None, ge=0)
    cooldown_network_seconds: int | None = Field(default=None, ge=0)
    cooldown_long_seconds: int | None = Field(default=None, ge=0)
    fireworks_quota_ttl_seconds: int | None = Field(default=None, ge=1)
    fireworks_quota_refresh_concurrency: int | None = Field(default=None, ge=1)
    fireworks_auto_disable_exhausted_accounts: bool | None = None
    fireworks_quota_background_refresh_enabled: bool | None = None
    fireworks_quota_refresh_interval_seconds: int | None = Field(default=None, ge=1)
    fireworks_quota_refresh_jitter_seconds: int | None = Field(default=None, ge=0)
    fireworks_quota_refresh_on_startup: bool | None = None
    transform_debug_enabled: bool | None = None
    transform_debug_retention: int | None = Field(default=None, ge=1)
    transform_debug_level: str | None = Field(default=None, min_length=1)

    @field_validator("proxy_api_keys", mode="before")
    @classmethod
    def _parse_lists(cls, value: Any):
        return normalize_string_list(value) if value is not None else None


def _config_payload(settings) -> dict[str, Any]:
    proxy_api_keys = list(getattr(settings, "proxy_api_keys", []) or [])
    return {
        "admin_token_configured": bool(getattr(settings, "admin_token", None)),
        "admin_token_masked": "***" if getattr(settings, "admin_token", None) else "",
        "proxy_api_keys": proxy_api_keys,
        "proxy_api_keys_count": len(proxy_api_keys),
        "upstream_base_url": getattr(settings, "upstream_base_url", None),
        "request_timeout_seconds": getattr(settings, "request_timeout_seconds", None),
        "max_upstream_attempts": getattr(settings, "max_upstream_attempts", None),
        "request_log_retention": getattr(settings, "request_log_retention", None),
        "allow_unknown_model_passthrough": getattr(settings, "allow_unknown_model_passthrough", None),
        "cooldown_rate_limit_seconds": getattr(settings, "cooldown_rate_limit_seconds", None),
        "cooldown_5xx_seconds": getattr(settings, "cooldown_5xx_seconds", None),
        "cooldown_network_seconds": getattr(settings, "cooldown_network_seconds", None),
        "cooldown_long_seconds": getattr(settings, "cooldown_long_seconds", None),
        "fireworks_quota_ttl_seconds": getattr(settings, "fireworks_quota_ttl_seconds", None),
        "fireworks_quota_refresh_concurrency": getattr(settings, "fireworks_quota_refresh_concurrency", None),
        "fireworks_auto_disable_exhausted_accounts": getattr(settings, "fireworks_auto_disable_exhausted_accounts", None),
        "fireworks_quota_background_refresh_enabled": getattr(settings, "fireworks_quota_background_refresh_enabled", None),
        "fireworks_quota_refresh_interval_seconds": getattr(settings, "fireworks_quota_refresh_interval_seconds", None),
        "fireworks_quota_refresh_jitter_seconds": getattr(settings, "fireworks_quota_refresh_jitter_seconds", None),
        "fireworks_quota_refresh_on_startup": getattr(settings, "fireworks_quota_refresh_on_startup", None),
        "transform_debug_enabled": getattr(settings, "transform_debug_enabled", None),
        "transform_debug_retention": getattr(settings, "transform_debug_retention", None),
        "transform_debug_level": getattr(settings, "transform_debug_level", None),
    }


def _runtime_diagnostics(settings, repository) -> dict[str, Any]:
    keys = repository.list_keys(include_disabled=True)
    malformed_key_count = sum(1 for key in keys if repository.is_locally_malformed_fireworks_key(key.api_key))
    db_path = getattr(repository, "db_path", getattr(settings, "db_path", None))
    data_dir = getattr(settings, "data_dir", None)
    return {
        "pid": os.getpid(),
        "ppid": os.getppid() if hasattr(os, "getppid") else None,
        "cwd": os.getcwd(),
        "python_executable": sys.executable,
        "database_path": str(db_path.resolve()) if db_path is not None else None,
        "data_dir": str(data_dir.resolve()) if data_dir is not None else None,
        "sync_env_keys_on_startup": bool(getattr(settings, "sync_env_keys_on_startup", False)),
        "env_fireworks_api_keys_count": len(getattr(settings, "fireworks_api_keys", []) or []),
        "env_fireworks_api_keys_json_count": len(getattr(settings, "fireworks_api_keys_json", []) or []),
        "db_key_count": len(keys),
        "malformed_key_count": malformed_key_count,
    }


@router.get("/config/runtime")
async def get_config(request: Request):
    settings = _settings(request)
    repository = _repository(request)
    return {
        "config": _config_payload(settings),
        "persisted_keys": sorted(item["key"] for item in repository.list_settings()),
        "runtime_diagnostics": _runtime_diagnostics(settings, repository),
    }


@router.patch("/config/runtime")
async def patch_config(request: Request, payload: AdminConfigPatch):
    settings = _settings(request)
    repository = _repository(request)
    updates = payload.model_dump(exclude_unset=True)

    if "proxy_api_keys" in updates and updates["proxy_api_keys"] is not None:
        updates["proxy_api_keys"] = normalize_string_list(updates["proxy_api_keys"])
    if "admin_token" in updates and updates["admin_token"] == "":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="admin_token cannot be blank")

    for key, value in updates.items():
        setattr(settings, key, value)
        repository.upsert_setting(key, value)

    return {"config": _config_payload(settings), "updated_keys": sorted(updates), "runtime_diagnostics": _runtime_diagnostics(settings, repository)}


@router.post("/config/proxy-keys/generate", status_code=status.HTTP_201_CREATED)
async def generate_proxy_key(request: Request):
    settings = _settings(request)
    repository = _repository(request)
    new_key = f"sk-fw2api-{secrets.token_urlsafe(24)}"
    proxy_keys = list(getattr(settings, "proxy_api_keys", []) or [])
    proxy_keys.append(new_key)
    settings.proxy_api_keys = proxy_keys
    repository.upsert_setting("proxy_api_keys", proxy_keys)
    return {"generated_key": new_key, "config": _config_payload(settings)}


@router.delete("/config/proxy-keys/{key}")
async def delete_proxy_key(request: Request, key: str):
    settings = _settings(request)
    repository = _repository(request)
    proxy_keys = [item for item in list(getattr(settings, "proxy_api_keys", []) or []) if item != key]
    if len(proxy_keys) == len(getattr(settings, "proxy_api_keys", []) or []):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="proxy key not found")
    settings.proxy_api_keys = proxy_keys
    repository.upsert_setting("proxy_api_keys", proxy_keys)
    return {"deleted": True, "config": _config_payload(settings)}
