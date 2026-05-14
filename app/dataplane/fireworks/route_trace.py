from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_REDACTED = "[REDACTED]"

_SAFE_HEADER_NAMES = {
    "x-request-id",
    "request-id",
    "fireworks-request-id",
    "x-fireworks-request-id",
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-reset-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-tokens",
    "x-cache",
    "x-cache-status",
    "cache-control",
    "age",
}

_CAPABILITY_TAG_FIELDS = {
    "tools": ("tools", "function"),
    "tool_choice": ("function",),
    "mcp": ("mcp",),
    "stream": ("sse",),
    "image": ("image",),
    "images": ("image",),
    "reasoning": ("reasoning",),
    "reasoning_effort": ("reasoning",),
    "thinking": ("thinking",),
    "prompt_cache_key": ("prompt_cache",),
    "prompt_cache_isolation_key": ("prompt_cache",),
    "service_tier": ("priority",),
    "parallel_tool_calls": ("tools",),
    "response_format": ("function",),
    "output_config": ("function",),
}

_SAFE_ACTION_FIELDS = {"field", "action", "to", "reason"}
_SAFE_ACTION_VALUES = {
    "field",
    "action",
    "to",
    "reason",
    "keep",
    "drop",
    "rename",
    "map",
    "preserve",
    "remove",
    "allow",
    "reject",
    "stream",
    "tools",
    "function",
    "mcp",
    "image",
    "reasoning",
    "thinking",
    "prompt_cache",
    "alias",
    "upstream",
    "[REDACTED]",
}

_REDACTED = "[REDACTED]"
_SECRET_HEADER_NAMES = {"authorization", "proxy-authorization", "x-api-key", "api-key", "x-mcp-authorization"}


def _redact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, val in value.items():
            key_text = str(key).strip().lower()
            if key_text in _SECRET_HEADER_NAMES:
                redacted[str(key)] = _REDACTED
            elif key_text == "headers" and isinstance(val, Mapping):
                redacted[str(key)] = {str(h_key): _REDACTED for h_key in val.keys()}
            else:
                redacted[str(key)] = _redact_value(val)
        return redacted
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def _safe_headers(headers: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not headers:
        return ()
    names = []
    for name in headers:
        lowered = str(name).strip().lower()
        if lowered in _SAFE_HEADER_NAMES:
            names.append(lowered)
    return tuple(sorted(dict.fromkeys(names)))


def derive_capability_tags(context: Any, *, endpoint: str | None = None) -> tuple[str, ...]:
    tags: set[str] = set()
    endpoint_text = endpoint or ""
    endpoint_key = endpoint_text.split(":", 1)[0].strip()
    if endpoint_key:
        if endpoint_key == "cross_endpoint_fallback/priority":
            tags.update({"cross_endpoint_fallback", "priority"})
            target_endpoint = endpoint_text.split(":", 1)[1].strip() if ":" in endpoint_text else ""
            if target_endpoint:
                tags.add({"chat_completions": "chat_completions", "chat/completions": "chat_completions"}.get(target_endpoint, target_endpoint))
            public_route_body = getattr(context, "body", {}) or {}
            if "input" in public_route_body:
                tags.add("responses")
        else:
            base_tag = {
                "chat/completions": "chat",
                "chat_completions": "chat",
                "messages": "anthropic_messages",
                "anthropic_messages": "anthropic_messages",
                "responses_lifecycle": "responses:lifecycle",
            }.get(endpoint_key, endpoint_key)
            tags.add(base_tag)
    body = getattr(context, "body", {}) or {}
    for field, tags_for_field in _CAPABILITY_TAG_FIELDS.items():
        value = body.get(field)
        if field in body and value is not None and value is not False:
            if field == "service_tier" and (not isinstance(value, str) or value.strip().lower() != "priority"):
                continue
            tags.update(tags_for_field)
    tools = body.get("tools")
    if isinstance(tools, list) and any(isinstance(tool, Mapping) and tool.get("type") == "mcp" for tool in tools):
        tags.add("mcp")
    return tuple(sorted(tags))


def sanitize_field_actions(actions: Any) -> tuple[dict[str, str], ...]:
    if not isinstance(actions, list):
        return ()
    sanitized: list[dict[str, str]] = []
    for item in actions:
        if not isinstance(item, Mapping):
            continue
        cleaned: dict[str, str] = {}
        for key in _SAFE_ACTION_FIELDS:
            value = _text(item.get(key))
            if key == "field":
                if value is not None:
                    cleaned[key] = value
                continue
            if key == "reason":
                if value is not None:
                    cleaned[key] = value
                continue
            if value is None:
                continue
            if value not in _SAFE_ACTION_VALUES and value.lower() not in _SAFE_ACTION_VALUES:
                continue
            cleaned[key] = value
        if cleaned:
            sanitized.append(cleaned)
    return tuple(sanitized)


def build_route_transform_trace(
    context: Any,
    *,
    public_route: str,
    product: str = "openai",
    operation: str | None = None,
    adapter: str,
    fireworks_endpoint: str,
    request_shape: Mapping[str, Any] | None = None,
    field_actions: Any = None,
    warnings: Any = None,
    payload: Mapping[str, Any] | None = None,
    headers: Mapping[str, Any] | None = None,
    routing_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    body = getattr(context, "body", {}) or {}
    shape = request_shape or {}
    trace = {
        "public_route": public_route,
        "product": product,
        "operation": operation,
        "adapter": adapter,
        "fireworks_endpoint": fireworks_endpoint,
        "model_alias": getattr(context, "model_name", None),
        "upstream_model": getattr(getattr(context, "resolved_model", None), "upstream_model", None),
        "capability_tags": derive_capability_tags(context, endpoint=fireworks_endpoint),
        "request_shape": {
            "payload_field_names": tuple(sorted(str(name) for name in shape.get("payload_field_names", ()))),
            "forwarded_header_names": _safe_headers(shape.get("forwarded_headers")),
        },
        "field_actions": sanitize_field_actions(field_actions),
        "warnings": tuple(warnings or ()),
    }
    if payload is not None:
        trace["payload"] = _redact_value(payload)
    if headers is not None:
        trace["headers"] = _redact_value(headers)
    if routing_metadata:
        safe_metadata: dict[str, Any] = {}
        for key in ("routing_mode", "selected_account_count", "primary_account_bucket", "skipped_account_count", "degraded_account_count", "stable_key_source", "stable_key_hash_value", "affinity_header", "selected_key_count"):
            value = routing_metadata.get(key)
            if value is not None:
                safe_metadata[key] = value
        trace["routing"] = safe_metadata
    return trace


def complete_route_transform_trace(trace: Mapping[str, Any], *, result: Any) -> dict[str, Any]:
    completed = dict(trace)
    completed["result"] = result
    return completed


__all__ = [
    "build_route_transform_trace",
    "complete_route_transform_trace",
    "derive_capability_tags",
    "sanitize_field_actions",
]
