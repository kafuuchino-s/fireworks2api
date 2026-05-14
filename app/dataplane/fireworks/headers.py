from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.dataplane.routing.affinity import derived_cache_key


_PASS_THROUGH_HEADER_NAMES = (
    "x-session-affinity",
    "x-multi-turn-session-id",
    "x-prompt-cache-isolation-key",
)


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_upstream_headers(
    headers: Mapping[str, Any],
    *,
    stable_key: str,
    affinity_hash_secret: str,
) -> dict[str, str]:
    upstream: dict[str, str] = {}
    for name in _PASS_THROUGH_HEADER_NAMES:
        value = _coerce_text(headers.get(name))
        if value:
            upstream[name] = value

    if "x-session-affinity" not in upstream:
        upstream["x-session-affinity"] = derived_cache_key(stable_key, affinity_hash_secret)

    return upstream


__all__ = ["build_upstream_headers"]
