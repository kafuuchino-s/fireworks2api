from __future__ import annotations

from typing import Any

from app.dataplane.usage import UsageStats, usage_cache_hit_ratio

from .context import ProxyRequestContext


def compute_cache_hit_ratio(usage: UsageStats) -> float:
    return usage_cache_hit_ratio(usage.input_tokens, usage.cached_tokens)


def prepare_log_payload(*, endpoint: str, context: ProxyRequestContext, selected_key, stream: bool, service_tier: str | None, usage: UsageStats, latency_ms: int | None, status_code: int, error_type: str | None, upstream_request_id: str | None) -> dict[str, Any]:
    return {
        "endpoint": endpoint,
        "model_alias": context.model_name,
        "upstream_model": context.resolved_model.upstream_model,
        "key_fingerprint": selected_key.fingerprint if selected_key else None,
        "stable_key_hash": context.stable_key_hash_value,
        "stream": stream,
        "service_tier": service_tier,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cached_tokens": usage.cached_tokens,
        "cache_hit_ratio": compute_cache_hit_ratio(usage),
        "latency_ms": latency_ms,
        "status_code": status_code,
        "error_type": error_type,
        "upstream_request_id": upstream_request_id,
    }
