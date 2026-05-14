from __future__ import annotations

from typing import Any, Mapping


_SAFE_FORWARD_HEADERS = (
    "x-session-affinity",
    "x-multi-turn-session-id",
    "x-prompt-cache-isolation-key",
)


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_transform_debug_summary(*, endpoint: str, upstream_endpoint: str, model_alias: str | None, upstream_model: str | None, stream: bool | None, service_tier: str | None, stable_key_source: str | None, payload: Mapping[str, Any] | None, forwarded_headers: Mapping[str, Any] | None, field_changes: list[dict[str, Any]] | None = None, warnings: list[str] | None = None, response_status_code: int | None = None, error_type: str | None = None, latency_ms: int | None = None) -> dict[str, Any]:
    return {
        "endpoint": endpoint,
        "upstream_endpoint": upstream_endpoint,
        "model_alias": model_alias,
        "upstream_model": upstream_model,
        "stream": stream,
        "service_tier": service_tier,
        "stable_key_source": stable_key_source,
        "payload_fields": sorted(str(name) for name in (payload or {}).keys()),
        "forwarded_headers": [name for name in _SAFE_FORWARD_HEADERS if _coerce_text((forwarded_headers or {}).get(name))],
        "field_changes": field_changes or [],
        "warnings": warnings or [],
        "response_status_code": response_status_code,
        "error_type": error_type,
        "latency_ms": latency_ms,
    }


def record_transform_debug(repository, settings, summary: dict[str, Any]) -> None:
    if not getattr(settings, "transform_debug_enabled", False):
        return
    retention = getattr(settings, "transform_debug_retention", 0) or 0
    repository.record_transform_debug(summary, retention)


def build_transform_debug_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    return {name: _coerce_text(headers.get(name)) for name in _SAFE_FORWARD_HEADERS if _coerce_text(headers.get(name))}
