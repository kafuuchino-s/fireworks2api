from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json


@dataclass(frozen=True)
class FireworksErrorDecision:
    error_type: str
    should_failover: bool
    should_cooldown: bool
    should_disable_key: bool
    client_status: int


def classify_fireworks_error(
    *,
    status_code: int | None = None,
    body: Any = None,
    exc: BaseException | None = None,
) -> FireworksErrorDecision:
    error_type, normalized_status = _classify_error_type(status_code=status_code, body=body, exc=exc)

    if error_type == "validation_error":
        return FireworksErrorDecision(error_type, False, False, False, 400)
    if error_type == "auth_error":
        return FireworksErrorDecision(error_type, True, False, True, 401 if normalized_status in {401, 403} else 401)
    if error_type == "model_not_found":
        return FireworksErrorDecision(error_type, False, False, False, 404)
    if error_type in {"rate_limit", "quota_exhausted"}:
        return FireworksErrorDecision(error_type, True, True, False, 429)
    if error_type in {"capacity_error", "server_error"}:
        client_status = normalized_status if normalized_status and normalized_status >= 500 else 503
        return FireworksErrorDecision(error_type, True, True, False, client_status)
    if error_type == "timeout_error":
        return FireworksErrorDecision(error_type, True, True, False, 504)
    if error_type == "network_error":
        return FireworksErrorDecision(error_type, True, True, False, 502)
    if error_type == "upstream_error":
        if normalized_status is not None and 400 <= normalized_status < 500:
            return FireworksErrorDecision(error_type, False, False, False, normalized_status)
        client_status = normalized_status if normalized_status is not None else 502
        return FireworksErrorDecision(error_type, True, True, False, client_status)
    if normalized_status == 404:
        return FireworksErrorDecision("model_not_found", False, False, False, 404)
    if normalized_status in {401, 403}:
        return FireworksErrorDecision("auth_error", True, False, True, normalized_status)
    if normalized_status == 429:
        return FireworksErrorDecision("rate_limit", True, True, False, 429)
    if normalized_status == 408:
        return FireworksErrorDecision("timeout_error", True, True, False, 504)
    if normalized_status and normalized_status >= 500:
        return FireworksErrorDecision("server_error", True, True, False, normalized_status)
    if normalized_status and 400 <= normalized_status < 500:
        return FireworksErrorDecision("upstream_error", False, False, False, normalized_status)
    return FireworksErrorDecision("upstream_error", True, True, False, 502)


def _classify_error_type(*, status_code: int | None, body: Any, exc: BaseException | None) -> tuple[str, int | None]:
    parsed_body = _parse_body(body)
    normalized_status = status_code

    if exc is not None:
        name = type(exc).__name__.lower()
        if "timeout" in name:
            return "timeout_error", normalized_status
        if any(token in name for token in ("connect", "network", "transport", "protocol", "remoteprotocol", "readerror")):
            return "network_error", normalized_status

    code_text = _body_text(parsed_body, "code") or _body_text(parsed_body, "error_code")
    message_text = _body_text(parsed_body, "message") or _body_text(parsed_body, "error_message")
    if message_text is None and isinstance(parsed_body, str):
        message_text = parsed_body
    error_text = " ".join(filter(None, [str(code_text or ""), str(message_text or "")])).lower()

    if normalized_status is None and isinstance(parsed_body, dict):
        inner = parsed_body.get("error")
        if isinstance(inner, dict):
            normalized_status = inner.get("status") if isinstance(inner.get("status"), int) else normalized_status

    if normalized_status == 400 or _contains_any(error_text, ["validation", "invalid request", "bad request", "unsupported parameter", "missing required parameter"]):
        return "validation_error", normalized_status
    if normalized_status in {401, 403} or _contains_any(error_text, ["unauthorized", "forbidden", "authentication", "api key", "invalid key"]):
        return "auth_error", normalized_status
    if normalized_status == 404 or _contains_any(error_text, ["model not found", "not found", "no such model"]):
        return "model_not_found", normalized_status
    if normalized_status == 402 or _contains_any(error_text, ["monthly spending limit", "failure to pay", "past invoices", "account suspended", "is suspended", "paid plan", "payment required", "payment method", "usage limits", "usage limit"]):
        return "quota_exhausted", normalized_status
    if normalized_status == 408:
        return "timeout_error", normalized_status
    if normalized_status == 429 or _contains_any(error_text, ["rate limit", "quota exhausted", "quota exceeded", "too many requests"]):
        if _contains_any(error_text, ["quota"]) or _contains_any(error_text, ["billing", "exhausted", "exceeded", "spending limit"]):
            return "quota_exhausted", normalized_status
        return "rate_limit", normalized_status
    if normalized_status and normalized_status >= 500:
        if _contains_any(error_text, ["capacity", "overloaded", "unavailable", "server error", "internal"]):
            return "capacity_error", normalized_status
        return "server_error", normalized_status
    if exc is not None:
        return "network_error" if "timeout" not in type(exc).__name__.lower() else "timeout_error", normalized_status
    if normalized_status is None:
        return "upstream_error", normalized_status
    return "upstream_error", normalized_status


def _parse_body(body: Any) -> Any:
    if isinstance(body, str):
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body
    return body


def _body_text(body: Any, key: str) -> str | None:
    if isinstance(body, dict):
        value = body.get(key)
        if isinstance(value, str):
            return value
        error = body.get("error")
        if isinstance(error, dict):
            value = error.get(key)
            if isinstance(value, str):
                return value
    return None


def _contains_any(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)
