from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import Request

from app.control.repository import KeyRecord, ModelMapping
from app.control.fireworks_model_registry import get_official_model, lookup_official_pricing
from app.platform.redaction import redact_secret
from app.platform.config import Settings


def _repository(request: Request):
    return request.app.state.repository


def _settings(request: Request):
    return request.app.state.settings


def _security_posture(request):
    settings: Settings = _settings(request)
    repository = _repository(request)
    keys = repository.list_keys()
    return {
        "admin_token_configured": bool(settings.admin_token),
        "proxy_keys_configured": bool(settings.proxy_api_keys),
        "admin_static_enabled": bool(settings.enable_admin_static),
        "key_count": len(keys),
        "full_prompt_logging_disabled": True,
        "keys_masked": True,
    }


def _is_future(iso_value: str | None) -> bool:
    if not iso_value:
        return False
    try:
        return datetime.fromisoformat(iso_value) > datetime.now(UTC)
    except ValueError:
        return False


def _service_status(summary: dict[str, Any]) -> str:
    if summary["key_total"] == 0:
        return "no_keys"
    if summary["healthy_key_count"] > 0:
        return "healthy"
    if summary["cooldown_key_count"] > 0:
        return "cooldown_only"
    if summary["disabled_key_count"] == summary["key_total"]:
        return "disabled"
    return "degraded"


def _key_payload(repository, record: KeyRecord) -> dict[str, Any]:
    usage = repository.key_usage_summary(record.fingerprint)
    return {
        "name": record.name,
        "fingerprint": record.fingerprint,
        "masked_key": redact_secret(record.api_key, visible=6),
        "enabled": record.enabled,
        "cooldown_until": record.cooldown_until,
        "cooldown_active": record.enabled and _is_future(record.cooldown_until),
        "disabled_reason": record.disabled_reason,
        "last_error_type": record.last_error_type,
        "last_error_at": record.last_error_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "recent_request_count": usage["request_count"],
        "recent_success_count": usage["success_count"],
        "recent_failure_count": usage["failure_count"],
        "recent_input_tokens": usage["input_tokens"],
        "recent_output_tokens": usage["output_tokens"],
        "recent_cached_tokens": usage["cached_tokens"],
        "cache_hit_ratio": usage["cache_hit_ratio"],
        "avg_latency_ms": usage["avg_latency_ms"],
        "usage": usage,
    }


def _model_payload(model: ModelMapping) -> dict[str, Any]:
    return {
        "alias": model.alias,
        "upstream_model": model.upstream_model,
        "enabled": model.enabled,
    }


def _catalog_entry_for_upstream(catalog: Any, upstream_model: str) -> dict[str, Any] | None:
    if not catalog:
        return None
    if isinstance(catalog, dict):
        entry = catalog.get(upstream_model) if isinstance(catalog.get(upstream_model), dict) else None
        if entry is not None:
            return entry
        items = catalog.get("items") or catalog.get("data") or catalog.get("models") or []
        for candidate in items if isinstance(items, list) else []:
            if isinstance(candidate, dict) and str(candidate.get("upstream_model") or candidate.get("id") or candidate.get("name") or "").strip() == upstream_model:
                return candidate
        return None
    if isinstance(catalog, list):
        for candidate in catalog:
            if isinstance(candidate, dict) and str(candidate.get("upstream_model") or candidate.get("id") or candidate.get("name") or "").strip() == upstream_model:
                return candidate
    return None


def _optional_model_metadata(request, upstream_model: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    official = get_official_model(upstream_model)
    if official is not None:
        payload["kind"] = official.kind
        payload["supported_functionality"] = dict(official.supported_functionality)
        payload["pricing"] = {tier: dict(price) for tier, price in official.pricing.items()}
    official_pricing = lookup_official_pricing(upstream_model)
    if official_pricing is not None and "pricing" not in payload:
        payload["pricing"] = official_pricing
    state = getattr(getattr(request, "app", None), "state", None)
    catalog = None if state is None else next((getattr(state, attr, None) for attr in ("fireworks_model_catalog", "fireworks_models_catalog", "fireworks_catalog") if getattr(state, attr, None)), None)
    entry = _catalog_entry_for_upstream(catalog, upstream_model)
    if entry:
        for field in ("kind", "supported_functionality", "pricing", "price"):
            value = entry.get(field)
            if value is not None and field not in payload:
                payload[field] = value
    if upstream_model.endswith("-fast") or upstream_model.endswith("-turbo"):
        base_upstream = upstream_model.removeprefix("accounts/fireworks/routers/")
        base_upstream = upstream_model.removeprefix("accounts/fireworks/models/")
        base_upstream = base_upstream.removesuffix("-fast").removesuffix("-turbo")
        base_model = f"accounts/fireworks/models/{base_upstream}"
        if not payload.get("kind") or not payload.get("supported_functionality"):
            base_entry = _catalog_entry_for_upstream(catalog, base_model)
            if base_entry:
                if payload.get("kind") is None and base_entry.get("kind") is not None:
                    payload["kind"] = base_entry["kind"]
                if payload.get("supported_functionality") is None and base_entry.get("supported_functionality") is not None:
                    payload["supported_functionality"] = base_entry["supported_functionality"]
    return payload


def _merge_model_metadata(existing: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    if not metadata:
        return existing
    merged = dict(existing)
    merged.update(metadata)
    return merged


def _request_payload(item: dict[str, Any]) -> dict[str, Any]:
    key_name = item.get("key_name")
    masked_key = item.get("masked_key")
    return {
        "id": item.get("id"),
        "timestamp": item.get("timestamp"),
        "endpoint": item.get("endpoint"),
        "model_alias": item.get("model_alias"),
        "upstream_model": item.get("upstream_model"),
        "key_fingerprint": item.get("key_fingerprint"),
        "key_name": key_name,
        "masked_key": masked_key,
        "key_label": item.get("key_label") or key_name or masked_key or "unknown",
        "stable_key_hash": item.get("stable_key_hash"),
        "stream": bool(item.get("stream")),
        "service_tier": item.get("service_tier"),
        "input_tokens": int(item.get("input_tokens") or 0),
        "output_tokens": int(item.get("output_tokens") or 0),
        "cached_tokens": int(item.get("cached_tokens") or 0),
        "cache_hit_ratio": float(item.get("cache_hit_ratio") or 0),
        "estimated": bool(item.get("estimated")),
        "latency_ms": item.get("latency_ms"),
        "status_code": item.get("status_code"),
        "error_type": item.get("error_type"),
        "upstream_request_id": item.get("upstream_request_id"),
    }
