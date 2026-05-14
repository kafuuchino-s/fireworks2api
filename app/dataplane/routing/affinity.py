from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.platform.redaction import hmac_prefix


_AFFINITY_HEADER_NAMES = (
    "x-session-affinity",
    "x-multi-turn-session-id",
    "session_id",
    "session-id",
    "conversation_id",
    "conversation-id",
)


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_header(headers: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = _coerce_text(headers.get(name))
        if value:
            return value
    return None


def client_identity_from_request(
    headers: Mapping[str, Any],
    client_host: str | None,
    client_port: int | None,
) -> str:
    forwarded_for = _first_header(headers, "x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    real_ip = _first_header(headers, "x-real-ip")
    if real_ip:
        return real_ip

    if client_host:
        return client_host

    return "unknown"


def extract_stable_key(
    body: Mapping[str, Any],
    headers: Mapping[str, Any],
    model_alias: str,
    client_identity: str,
) -> tuple[str, str]:
    for body_field in ("prompt_cache_key", "user"):
        value = _coerce_text(body.get(body_field))
        if value:
            return value, f"body.{body_field}"

    value = _first_header(headers, *_AFFINITY_HEADER_NAMES)
    if value:
        return value, "header.affinity"

    value = _coerce_text(body.get("previous_response_id"))
    if value:
        return value, "body.previous_response_id"

    fallback = f"{model_alias}:{client_identity or 'anonymous'}"
    return fallback, "fallback"


def build_route_key(
    model_alias: str,
    stable_key: str,
    client_identity: str | None = None,
) -> str:
    route_key = f"{model_alias}:{stable_key}"
    if client_identity:
        return f"{client_identity}:{route_key}"
    return route_key


def stable_key_hash(stable_key: str, secret: str, length: int = 12) -> str:
    return hmac_prefix(secret, stable_key, length=length)


def derived_cache_key(stable_key: str, secret: str, length: int = 16) -> str:
    return hmac_prefix(secret, stable_key, length=length)


__all__ = [
    "build_route_key",
    "client_identity_from_request",
    "derived_cache_key",
    "extract_stable_key",
    "stable_key_hash",
]
