from __future__ import annotations

from typing import Any

REASONING_TOP_K_DEFAULT = 40
REASONING_TOP_K_DEFAULT_WARNING = "top_k injected default 40 for Fireworks reasoning stability"
KIMI_THINKING_TEMPERATURE = 1.0
KIMI_INSTANT_TEMPERATURE = 0.6
KIMI_TOP_P = 0.95


def needs_reasoning_top_k_default(upstream_model: str | None) -> bool:
    base = (upstream_model or "").strip().lower().rsplit("/", 1)[-1]
    normalized = base.replace("_", "-")
    # kimi-k2p6 family removed: smoke test confirmed kimi works stably on
    # Fireworks /v1/responses without top_k, and the chat_completions fallback
    # path loses reasoning content entirely.  Only deepseek-v4 still needs it.
    return normalized.startswith("deepseek-v4")


def needs_kimi_k2p6_sampling_defaults(upstream_model: str | None) -> bool:
    base = (upstream_model or "").strip().lower().rsplit("/", 1)[-1]
    normalized = base.replace("_", "-")
    return normalized in {"kimi-k2p6", "kimi-k2p6-turbo", "kimi-k2.6", "kimi-k2.6-turbo"}


def _thinking_disabled(payload: dict[str, Any]) -> bool:
    thinking = payload.get("thinking")
    if not isinstance(thinking, dict):
        return False
    return str(thinking.get("type") or "").strip().lower() == "disabled"


def _has_reasoning_effort(payload: dict[str, Any]) -> bool:
    effort = payload.get("reasoning_effort")
    return isinstance(effort, str) and bool(effort.strip())


def _set_sampling_value(
    payload: dict[str, Any],
    field: str,
    value: float | int,
    *,
    reason: str,
    warning: str,
) -> tuple[dict[str, Any] | None, str | None]:
    current = payload.get(field)
    if current == value:
        return None, None
    payload[field] = value
    if current is None:
        return {"field": field, "action": "default", "to": value, "reason": reason}, warning
    return {"field": field, "action": "normalized", "from": current, "to": value, "reason": reason}, warning


def apply_reasoning_top_k_default(payload: dict[str, Any], upstream_model: str | None) -> tuple[dict[str, Any] | None, str | None]:
    needs_default = needs_reasoning_top_k_default(upstream_model)
    if "top_k" in payload and payload.get("top_k") is not None:
        return None, None

    if not needs_default:
        return None, None

    payload["top_k"] = REASONING_TOP_K_DEFAULT
    return (
        {
            "field": "top_k",
            "action": "default",
            "reason": "fireworks_reasoning_sampling_stability",
        },
        REASONING_TOP_K_DEFAULT_WARNING,
    )


def apply_model_sampling_defaults(payload: dict[str, Any], upstream_model: str | None) -> tuple[list[dict[str, Any]], list[str]]:
    field_changes: list[dict[str, Any]] = []
    warnings: list[str] = []

    top_k_change, top_k_warning = apply_reasoning_top_k_default(payload, upstream_model)
    if top_k_change is not None:
        field_changes.append(top_k_change)
    if top_k_warning is not None:
        warnings.append(top_k_warning)

    if not needs_kimi_k2p6_sampling_defaults(upstream_model):
        return field_changes, warnings

    # Kimi-specific chat defaults: top_k for sampling stability on
    # /v1/chat/completions (separate from the Responses fallback decision
    # controlled by needs_reasoning_top_k_default which only covers deepseek-v4).
    # Only inject if the caller has not set an explicit top_k already.
    if "top_k" not in payload or payload.get("top_k") is None:
        top_k_change, top_k_warning = _set_sampling_value(
            payload,
            "top_k",
            REASONING_TOP_K_DEFAULT,
            reason="kimi_k2p6_fireworks_chat_stability",
            warning="top_k injected default 40 for Kimi K2.6 Fireworks chat stability",
        )
        if top_k_change is not None:
            field_changes.append(top_k_change)
        if top_k_warning is not None:
            warnings.append(top_k_warning)

    if payload.get("thinking") is None and not _has_reasoning_effort(payload):
        payload.pop("thinking", None)
        payload["thinking"] = {"type": "disabled"}
        field_changes.append(
            {
                "field": "thinking",
                "action": "default",
                "to": "disabled",
                "reason": "kimi_k2p6_fireworks_chat_stability",
            }
        )
        warnings.append("thinking disabled by default for Kimi K2.6 Fireworks chat stability")

    temperature = KIMI_INSTANT_TEMPERATURE if _thinking_disabled(payload) else KIMI_THINKING_TEMPERATURE
    for field, value in (("temperature", temperature), ("top_p", KIMI_TOP_P)):
        change, warning = _set_sampling_value(
            payload,
            field,
            value,
            reason="kimi_k2p6_fixed_sampling",
            warning=f"{field} normalized for Kimi K2.6 sampling stability",
        )
        if change is not None:
            field_changes.append(change)
        if warning is not None:
            warnings.append(warning)

    return field_changes, warnings


__all__ = [
    "KIMI_INSTANT_TEMPERATURE",
    "KIMI_THINKING_TEMPERATURE",
    "KIMI_TOP_P",
    "REASONING_TOP_K_DEFAULT",
    "REASONING_TOP_K_DEFAULT_WARNING",
    "apply_model_sampling_defaults",
    "apply_reasoning_top_k_default",
    "needs_kimi_k2p6_sampling_defaults",
    "needs_reasoning_top_k_default",
]
