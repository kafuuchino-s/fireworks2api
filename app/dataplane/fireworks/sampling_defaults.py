from __future__ import annotations

from typing import Any

REASONING_TOP_K_DEFAULT = 40
REASONING_TOP_K_DEFAULT_WARNING = "top_k injected default 40 for Fireworks reasoning stability"


def needs_reasoning_top_k_default(upstream_model: str | None) -> bool:
    base = (upstream_model or "").strip().lower().rsplit("/", 1)[-1]
    normalized = base.replace("_", "-")
    # kimi-k2p6 family removed: smoke test confirmed kimi works stably on
    # Fireworks /v1/responses without top_k, and the chat_completions fallback
    # path loses reasoning content entirely.  Only deepseek-v4 still needs it.
    return normalized.startswith("deepseek-v4")


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
    """Apply Fireworks-specific sampling defaults.

    Most models rely on Fireworks' own backend defaults; we only inject
    top_k for known reasoning-stability issues (e.g. deepseek-v4 via the
    Responses -> Chat Completions fallback path).
    """
    field_changes: list[dict[str, Any]] = []
    warnings: list[str] = []

    top_k_change, top_k_warning = apply_reasoning_top_k_default(payload, upstream_model)
    if top_k_change is not None:
        field_changes.append(top_k_change)
    if top_k_warning is not None:
        warnings.append(top_k_warning)

    return field_changes, warnings


__all__ = [
    "REASONING_TOP_K_DEFAULT",
    "REASONING_TOP_K_DEFAULT_WARNING",
    "apply_model_sampling_defaults",
    "apply_reasoning_top_k_default",
    "needs_reasoning_top_k_default",
]
