from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from .deps import _repository, _settings, _service_status

router = APIRouter()


@router.get("/overview")
async def overview(request: Request) -> dict[str, Any]:
    repository = _repository(request)
    settings = _settings(request)
    summary = repository.overview()
    proxy_api_keys = getattr(settings, "proxy_api_keys", [])
    return {
        **summary,
        "recent_request_count": summary["request_count"],
        "recent_error_count": summary["error_count"],
        "service_status": _service_status(summary),
        "upstream_base_url": getattr(settings, "upstream_base_url", None),
        "request_timeout_seconds": getattr(settings, "request_timeout_seconds", None),
        "max_upstream_attempts": getattr(settings, "max_upstream_attempts", None),
        "allow_unknown_model_passthrough": getattr(settings, "allow_unknown_model_passthrough", None),
        "request_log_retention": getattr(settings, "request_log_retention", None),
        "transform_debug_enabled": getattr(settings, "transform_debug_enabled", None),
        "transform_debug_retention": getattr(settings, "transform_debug_retention", None),
        "transform_debug_level": getattr(settings, "transform_debug_level", None),
        "sync_env_keys_on_startup": getattr(settings, "sync_env_keys_on_startup", None),
        "enable_admin_static": getattr(settings, "enable_admin_static", None),
        "cors_origins": getattr(settings, "cors_allow_origins", []),
        "proxy_key_count": len(proxy_api_keys),
        "proxy_keys_configured": bool(proxy_api_keys),
        "affinity_hash_secret_configured": bool(getattr(settings, "affinity_hash_secret", None)),
        "log_hash_secret_configured": bool(getattr(settings, "log_hash_secret", None)),
        "cooldown_rate_limit_seconds": getattr(settings, "cooldown_rate_limit_seconds", None),
        "cooldown_5xx_seconds": getattr(settings, "cooldown_5xx_seconds", None),
        "cooldown_network_seconds": getattr(settings, "cooldown_network_seconds", None),
        "cooldown_long_seconds": getattr(settings, "cooldown_long_seconds", None),
        "admin_token_configured": bool(getattr(settings, "admin_token", None)),
        "write_enabled": bool(getattr(settings, "admin_token", None)),
    }
