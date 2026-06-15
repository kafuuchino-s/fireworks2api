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
    estimated = False
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
        estimated = estimated or bool(part.get("estimated"))
        raw_usage = dict(part) if raw_usage is None else raw_usage

    return UsageStats(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        estimated=estimated,
        raw_usage=raw_usage,
    )


@dataclass
class UsageStats:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    raw_usage: dict[str, Any] | None = None
    estimated: bool = False

    def merge(self, other: UsageStats) -> UsageStats:
        return UsageStats(
            input_tokens=max(self.input_tokens, other.input_tokens),
            output_tokens=max(self.output_tokens, other.output_tokens),
            cached_tokens=max(self.cached_tokens, other.cached_tokens),
            raw_usage=other.raw_usage or self.raw_usage,
            estimated=self.estimated or other.estimated,
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
            estimated=merged.estimated or usage.estimated,
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


# ---------------------------------------------------------------------------
# Fallback estimation helpers
# ---------------------------------------------------------------------------

# Conservative approximation: one token covers roughly 4 ASCII characters on
# average for the multilingual models served by Fireworks.  This is intentionally
# coarse because we do not have the model's tokenizer available.
_APPROX_CHARS_PER_TOKEN = 4

# Fixed overhead charged per image in most vision pipelines.  Fireworks pricing
# does not expose per-image token counts in the response, so we use a small
# placeholder that keeps the usage non-zero without inflating billing metrics.
_APPROX_IMAGE_INPUT_TOKENS = 256


def _text_length(text: Any) -> int:
    if isinstance(text, str):
        return len(text)
    if isinstance(text, (bytes, bytearray)):
        return len(text)
    return 0


def _estimate_tokens_from_text(text: Any) -> int:
    length = _text_length(text)
    if length <= 0:
        return 0
    # Round up so short prompts are not reported as zero tokens.
    return max(1, (length + _APPROX_CHARS_PER_TOKEN - 1) // _APPROX_CHARS_PER_TOKEN)


def _collect_text_parts_from_content(content: Any) -> list[str]:
    texts: list[str] = []
    if isinstance(content, str):
        if content:
            texts.append(content)
        return texts
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type in {"text", "input_text", "output_text"}:
                text = part.get("text")
                if isinstance(text, str) and text:
                    texts.append(text)
            elif part_type in {"image_url", "input_image", "image"}:
                # Image URLs / data URLs contribute token overhead; the actual
                # payload is counted separately as image overhead.
                pass
    return texts


def _estimate_input_tokens_from_messages(messages: Any) -> int:
    """Estimate input tokens from a chat-style messages list."""
    if not isinstance(messages, list):
        return 0
    total = 0
    image_count = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        for text in _collect_text_parts_from_content(content):
            total += _estimate_tokens_from_text(text)
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in {"image_url", "input_image", "image"}:
                    image_count += 1
        if message.get("role") == "tool" or message.get("role") == "function":
            # Tool/function messages carry structured output as content.
            tool_content = message.get("content")
            if isinstance(tool_content, str):
                total += _estimate_tokens_from_text(tool_content)
    total += image_count * _APPROX_IMAGE_INPUT_TOKENS
    return max(0, total)


def _estimate_input_tokens_from_responses_input(input_items: Any) -> int:
    """Estimate input tokens from a Responses-style input list."""
    if not isinstance(input_items, list):
        if isinstance(input_items, str):
            return _estimate_tokens_from_text(input_items)
        return 0
    total = 0
    image_count = 0
    for item in input_items:
        if not isinstance(item, dict):
            if isinstance(item, str):
                total += _estimate_tokens_from_text(item)
            continue
        item_type = item.get("type")
        if item_type in {"message", None}:
            content = item.get("content")
            for text in _collect_text_parts_from_content(content):
                total += _estimate_tokens_from_text(text)
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") in {"input_image", "image", "image_url"}:
                        image_count += 1
        elif item_type in {"text", "input_text", "output_text"}:
            text = item.get("text")
            if isinstance(text, str):
                total += _estimate_tokens_from_text(text)
        elif item_type == "function_call_output":
            output = item.get("output")
            if isinstance(output, str):
                total += _estimate_tokens_from_text(output)
        elif item_type == "function_call":
            # Approximate the tool-call JSON representation.
            total += _estimate_tokens_from_text(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
        elif item_type == "tool_output":
            output = item.get("output")
            if isinstance(output, str):
                total += _estimate_tokens_from_text(output)
    total += image_count * _APPROX_IMAGE_INPUT_TOKENS
    return max(0, total)


def _estimate_input_tokens_from_payload(payload: Mapping[str, Any]) -> int:
    """Estimate input tokens from the forwarded upstream payload."""
    if not isinstance(payload, Mapping):
        return 0
    if "messages" in payload:
        return _estimate_input_tokens_from_messages(payload.get("messages"))
    if "input" in payload:
        return _estimate_input_tokens_from_responses_input(payload.get("input"))
    if "prompt" in payload:
        prompt = payload["prompt"]
        if isinstance(prompt, str):
            return _estimate_tokens_from_text(prompt)
        if isinstance(prompt, list) and prompt and isinstance(prompt[0], str):
            return sum(_estimate_tokens_from_text(p) for p in prompt)
    return 0


def _estimate_output_tokens_from_text(text: str) -> int:
    return _estimate_tokens_from_text(text)


def _estimate_output_tokens_from_payload(payload: Mapping[str, Any]) -> int:
    """Estimate output tokens from a non-streaming response payload.

    Only used when the upstream response contains no usage at all.
    """
    if not isinstance(payload, Mapping):
        return 0

    # Chat completions response
    choices = payload.get("choices")
    if isinstance(choices, list):
        total = 0
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    total += _estimate_output_tokens_from_text(content)
                reasoning = message.get("reasoning_content")
                if isinstance(reasoning, str):
                    total += _estimate_output_tokens_from_text(reasoning)
                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list):
                    for tool_call in tool_calls:
                        if not isinstance(tool_call, dict):
                            continue
                        function = tool_call.get("function")
                        if isinstance(function, dict):
                            arguments = function.get("arguments")
                            if isinstance(arguments, str):
                                total += _estimate_output_tokens_from_text(arguments)
        return total

    # Responses API response
    output = payload.get("output")
    if isinstance(output, list):
        total = 0
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "message":
                content = item.get("content")
                for text in _collect_text_parts_from_content(content):
                    total += _estimate_output_tokens_from_text(text)
            elif item_type == "reasoning":
                summary = item.get("summary")
                if isinstance(summary, list):
                    for part in summary:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            total += _estimate_output_tokens_from_text(part["text"])
            elif item_type == "function_call":
                arguments = item.get("arguments")
                if isinstance(arguments, str):
                    total += _estimate_output_tokens_from_text(arguments)
        return total

    # Completions response
    if isinstance(payload.get("text"), str):
        return _estimate_output_tokens_from_text(payload["text"])

    return 0


def estimate_usage_from_payloads(
    request_payload: Mapping[str, Any],
    response_payload: Mapping[str, Any] | None,
    *,
    estimated: bool = True,
) -> UsageStats:
    """Return a usage estimate based on request/response text length.

    This is a coarse fallback for upstream models that omit the ``usage`` field
    or return zero values.  It should never override non-zero upstream usage.
    """
    input_tokens = _estimate_input_tokens_from_payload(request_payload)
    output_tokens = _estimate_output_tokens_from_payload(response_payload) if response_payload else 0
    return UsageStats(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=0,
        estimated=estimated,
    )


def maybe_estimate_usage(
    usage: UsageStats,
    request_payload: Mapping[str, Any] | None,
    response_payload: Mapping[str, Any] | None,
    upstream_model: str | None = None,
) -> UsageStats:
    """Return usage with fallback estimates if the upstream value is zero.

    Preserves any non-zero upstream usage; only fills in missing fields.
    If a model is known to omit usage in streaming responses, try a tokenizer
    based estimate first; otherwise fall back to the simple text-length estimate.
    """
    if usage.input_tokens and usage.output_tokens:
        return usage

    # Try tokenizer-aware estimation for known models.
    try:
        from app.dataplane.usage_estimator import (
            estimate_input_tokens,
            estimate_output_tokens_from_payload,
        )

        tokenizer_input = estimate_input_tokens(request_payload or {}, upstream_model) if request_payload else 0
        tokenizer_output = estimate_output_tokens_from_payload(response_payload, upstream_model) if response_payload else 0
        if tokenizer_input or tokenizer_output:
            usage = UsageStats(
                input_tokens=max(usage.input_tokens, tokenizer_input),
                output_tokens=max(usage.output_tokens, tokenizer_output),
                cached_tokens=usage.cached_tokens,
                raw_usage=usage.raw_usage,
                estimated=True,
            )
            if usage.input_tokens and usage.output_tokens:
                return usage
    except Exception:
        # If tokenizer estimation fails, fall back to simple text-length estimate.
        pass

    if not usage.input_tokens and not usage.output_tokens:
        estimated = estimate_usage_from_payloads(
            request_payload or {},
            response_payload,
            estimated=True,
        )
        return UsageStats(
            input_tokens=max(usage.input_tokens, estimated.input_tokens),
            output_tokens=max(usage.output_tokens, estimated.output_tokens),
            cached_tokens=max(usage.cached_tokens, estimated.cached_tokens),
            raw_usage=usage.raw_usage,
            estimated=True,
        )

    if not usage.input_tokens and request_payload:
        estimated_input = _estimate_input_tokens_from_payload(request_payload)
        return UsageStats(
            input_tokens=estimated_input,
            output_tokens=usage.output_tokens,
            cached_tokens=usage.cached_tokens,
            raw_usage=usage.raw_usage,
            estimated=True,
        )

    if not usage.output_tokens:
        # If a response payload is available, prefer it; otherwise fall back to a
        # coarse text-length estimate from the request text so the admin log never
        # reports zero output for a successful streaming response.
        estimated_output = 0
        if response_payload:
            estimated_output = _estimate_output_tokens_from_payload(response_payload)
        if not estimated_output and isinstance(request_payload, dict):
            if "messages" in request_payload:
                text_parts: list[str] = []
                for message in request_payload.get("messages", []):
                    if isinstance(message, dict):
                        text_parts.extend(_collect_text_parts_from_content(message.get("content")))
                estimated_output = _estimate_output_tokens_from_text("".join(text_parts))
            elif "input" in request_payload:
                input_value = request_payload.get("input")
                if isinstance(input_value, str):
                    estimated_output = _estimate_output_tokens_from_text(input_value)
                elif isinstance(input_value, list):
                    text_parts = []
                    for item in input_value:
                        if isinstance(item, dict):
                            text_parts.extend(_collect_text_parts_from_content(item.get("content")))
                        elif isinstance(item, str):
                            text_parts.append(item)
                    estimated_output = _estimate_output_tokens_from_text("".join(text_parts))
        if estimated_output:
            return UsageStats(
                input_tokens=usage.input_tokens,
                output_tokens=estimated_output,
                cached_tokens=usage.cached_tokens,
                raw_usage=usage.raw_usage,
                estimated=True,
            )

    return usage


__all__ = [
    "UsageStats",
    "extract_usage",
    "extract_usage_from_headers",
    "merge_usage",
    "usage_cache_hit_ratio",
    "usage_from_mapping",
    "estimate_usage_from_payloads",
    "maybe_estimate_usage",
]

