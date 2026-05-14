from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


def _to_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _extract_cached_tokens(usage: Mapping[str, Any]) -> int:
    candidates: list[Any] = [
        usage.get("cached_tokens"),
        usage.get("cachedTokens"),
        usage.get("cached-prompt-tokens"),
        usage.get("cachedPromptTokens"),
    ]
    candidates.append(_to_int(usage.get("cache_creation_input_tokens")) + _to_int(usage.get("cache_read_input_tokens")))
    for key in ("prompt_tokens_details", "input_tokens_details", "promptTokensDetails", "inputTokensDetails"):
        nested = usage.get(key)
        if isinstance(nested, Mapping):
            candidates.extend(
                [
                    nested.get("cached_tokens"),
                    nested.get("cachedTokens"),
                    nested.get("cached-prompt-tokens"),
                    nested.get("cachedPromptTokens"),
                ]
            )
            cache_creation = _to_int(nested.get("cache_creation_input_tokens"))
            cache_read = _to_int(nested.get("cache_read_input_tokens"))
            if cache_creation or cache_read:
                candidates.append(cache_creation + cache_read)
    return max((_to_int(candidate) for candidate in candidates), default=0)


def _extract_usage_value(usage: Mapping[str, Any], *keys: str) -> int:
    candidates: list[Any] = []
    for key in keys:
        candidates.append(usage.get(key))
    return max((_to_int(candidate) for candidate in candidates), default=0)


def _extract_key_variants(usage: Mapping[str, Any], base_key: str) -> int:
    camel_key = "".join(part.capitalize() if i else part for i, part in enumerate(base_key.split("_")))
    kebab_key = base_key.replace("_", "-")
    compact_key = base_key.replace("_", "")
    return _extract_usage_value(usage, base_key, camel_key, kebab_key, compact_key)


def _extract_perf_metrics(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    perf_metrics = payload.get("perf_metrics")
    if isinstance(perf_metrics, Mapping):
        return perf_metrics
    return None


def _usage_from_parts(*parts: Mapping[str, Any] | None) -> UsageStats:
    input_tokens = 0
    output_tokens = 0
    cached_tokens = 0
    raw_usage: dict[str, Any] | None = None

    for part in parts:
        if not isinstance(part, Mapping):
            continue
        current_input = max(
            _extract_key_variants(part, "input_tokens"),
            _extract_key_variants(part, "prompt_tokens"),
        )
        current_output = max(
            _extract_key_variants(part, "output_tokens"),
            _extract_key_variants(part, "completion_tokens"),
        )
        current_cached = _extract_cached_tokens(part)
        input_tokens = max(input_tokens, current_input)
        output_tokens = max(output_tokens, current_output)
        cached_tokens = max(cached_tokens, current_cached)
        raw_usage = dict(part) if raw_usage is None else raw_usage

    return UsageStats(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        raw_usage=raw_usage,
    )


@dataclass
class UsageStats:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    raw_usage: dict[str, Any] | None = None

    def merge(self, other: UsageStats) -> UsageStats:
        return UsageStats(
            input_tokens=max(self.input_tokens, other.input_tokens),
            output_tokens=max(self.output_tokens, other.output_tokens),
            cached_tokens=max(self.cached_tokens, other.cached_tokens),
            raw_usage=other.raw_usage or self.raw_usage,
        )


def usage_cache_hit_ratio(input_tokens: int, cached_tokens: int) -> float:
    if input_tokens <= 0:
        return 0.0
    return cached_tokens / input_tokens


def usage_from_mapping(usage: Mapping[str, Any]) -> UsageStats:
    return _usage_from_parts(usage)


def merge_usage(*usages: UsageStats) -> UsageStats:
    merged = UsageStats()
    for usage in usages:
        merged = UsageStats(
            input_tokens=max(merged.input_tokens, usage.input_tokens),
            output_tokens=max(merged.output_tokens, usage.output_tokens),
            cached_tokens=max(merged.cached_tokens, usage.cached_tokens),
            raw_usage=usage.raw_usage or merged.raw_usage,
        )
    return merged


def extract_usage_from_headers(headers: Mapping[str, Any]) -> UsageStats:
    return UsageStats(
        input_tokens=_extract_usage_value(headers, "fireworks-prompt-tokens"),
        cached_tokens=_extract_usage_value(headers, "fireworks-cached-prompt-tokens"),
        raw_usage=dict(headers),
    )


def extract_usage(payload: Any) -> UsageStats:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return UsageStats()

    if not isinstance(payload, Mapping):
        return UsageStats()

    usage = payload.get("usage")
    perf_metrics = _extract_perf_metrics(payload)
    if isinstance(usage, Mapping):
        return merge_usage(usage_from_mapping(usage), _usage_from_parts(perf_metrics))

    response = payload.get("response")
    if isinstance(response, Mapping):
        nested_usage = response.get("usage")
        nested_perf_metrics = _extract_perf_metrics(response)
        if isinstance(nested_usage, Mapping):
            return merge_usage(usage_from_mapping(nested_usage), _usage_from_parts(nested_perf_metrics))

    if perf_metrics is not None:
        return _usage_from_parts(perf_metrics)

    return UsageStats()


__all__ = [
    "UsageStats",
    "extract_usage",
    "extract_usage_from_headers",
    "merge_usage",
    "usage_cache_hit_ratio",
    "usage_from_mapping",
]
