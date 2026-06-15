from app.dataplane.fireworks.proxy import close_quietly, failover_on_error, proxy_fireworks_request, read_response_text, safe_upstream_request_id
from app.dataplane.usage import UsageStats, extract_usage_from_headers, merge_usage

from .context import (
    ProxyRequestContext,
    build_proxy_context,
    build_proxy_context_from_body,
    build_proxy_context_optional_model,
    copy_body,
    ensure_proxy_auth,
    get_model_name,
    get_settings_and_repository,
    load_json_body,
)
from .logging import compute_cache_hit_ratio, prepare_log_payload
from .payloads import (
    build_chat_upstream_headers,
    build_chat_upstream_payload,
    build_responses_upstream_headers,
    build_responses_upstream_payload,
)
from .transform_debug import build_transform_debug_summary, record_transform_debug


def drop_reasoning_effort_if_thinking(payload):
    if payload.get("thinking") is not None and payload.get("reasoning_effort") is not None:
        payload.pop("reasoning_effort", None)


def parse_usage_from_response_data(data):
    from app.dataplane.usage import extract_usage

    return extract_usage(data)


def maybe_derive_cache_key(route_key: str, secret: str) -> str:
    return route_key


def record_request_log(context: ProxyRequestContext, *, endpoint: str, selected_key, stream: bool, service_tier: str | None, usage: UsageStats, latency_ms: int | None, status_code: int, error_type: str | None, upstream_request_id: str | None) -> str:
    return context.repository.insert_request_log(prepare_log_payload(endpoint=endpoint, context=context, selected_key=selected_key, stream=stream, service_tier=service_tier, usage=usage, latency_ms=latency_ms, status_code=status_code, error_type=error_type, upstream_request_id=upstream_request_id), context.settings.request_log_retention)


def record_proxy_transform_debug(context: ProxyRequestContext, *, endpoint: str, upstream_endpoint: str, payload: dict, headers: dict[str, str], stream: bool | None = None, service_tier: str | None = None, field_changes: list[dict] | None = None, warnings: list[str] | None = None, response_status_code: int | None = None, error_type: str | None = None, latency_ms: int | None = None) -> None:
    repository = getattr(context, "repository", None)
    settings = getattr(context, "settings", None)
    if repository is None or settings is None:
        return
    upstream_model = getattr(getattr(context, "resolved_model", None), "upstream_model", None)
    model_alias = getattr(context, "model_name", None) or (getattr(context, "body", {}) or {}).get("model")
    if field_changes is None and model_alias and upstream_model and model_alias != upstream_model:
        field_changes = [{"field": "model", "from": model_alias, "to": upstream_model}]
    summary = build_transform_debug_summary(
        endpoint=endpoint,
        upstream_endpoint=upstream_endpoint,
        model_alias=model_alias,
        upstream_model=upstream_model,
        stream=stream,
        service_tier=service_tier,
        stable_key_source=getattr(context, "stable_key_source", None),
        payload=payload,
        forwarded_headers=headers,
        field_changes=field_changes,
        warnings=warnings,
        response_status_code=response_status_code,
        error_type=error_type,
        latency_ms=latency_ms,
    )
    record_transform_debug(repository, settings, summary)


def _merge_request_usage(response, body_usage: UsageStats) -> UsageStats:
    return merge_usage(body_usage, extract_usage_from_headers(response.headers))


__all__ = [
    "ProxyRequestContext",
    "build_chat_upstream_headers",
    "build_chat_upstream_payload",
    "build_proxy_context",
    "build_proxy_context_from_body",
    "build_proxy_context_optional_model",
    "build_responses_upstream_headers",
    "build_responses_upstream_payload",
    "close_quietly",
    "compute_cache_hit_ratio",
    "copy_body",
    "drop_reasoning_effort_if_thinking",
    "ensure_proxy_auth",
    "failover_on_error",
    "get_model_name",
    "get_settings_and_repository",
    "load_json_body",
    "maybe_derive_cache_key",
    "parse_usage_from_response_data",
    "proxy_fireworks_request",
    "read_response_text",
    "record_request_log",
    "record_proxy_transform_debug",
    "safe_upstream_request_id",
]
