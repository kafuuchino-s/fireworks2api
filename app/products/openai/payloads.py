from __future__ import annotations

from typing import Any

from app.dataplane.fireworks.headers import build_upstream_headers

from .adapters import build_chat_adapter, build_responses_adapter
from .context import ProxyRequestContext


def build_chat_upstream_payload(context: ProxyRequestContext) -> dict[str, Any]:
    payload, _, _ = build_chat_adapter(context)
    return payload


def build_responses_upstream_payload(context: ProxyRequestContext) -> dict[str, Any]:
    payload, _, _ = build_responses_adapter(context)
    return payload


def build_chat_upstream_headers(context: ProxyRequestContext) -> dict[str, str]:
    return build_upstream_headers(context.request_headers, stable_key=context.stable_key, affinity_hash_secret=context.settings.affinity_hash_secret or context.settings.log_hash_secret)


def build_responses_upstream_headers(context: ProxyRequestContext) -> dict[str, str]:
    return build_upstream_headers(context.request_headers, stable_key=context.stable_key, affinity_hash_secret=context.settings.affinity_hash_secret or context.settings.log_hash_secret)
