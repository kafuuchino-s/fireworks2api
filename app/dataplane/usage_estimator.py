from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from typing import Any


# Mapping from Fireworks upstream model id to a HuggingFace tokenizer repo.
# These are chosen to match the tokenizer of the underlying open-weights model.
# Token counts from AutoTokenizer are multiplied by a per-family calibration
# factor that accounts for the extra message-formatting tokens that Fireworks
# adds to the raw input.
_KNOWN_TOKENIZER_MAPPINGS: dict[str, tuple[str, float]] = {
    "accounts/fireworks/models/kimi-k2p7-code": ("MoonshotAI/Kimi-K2.7-Code", 1.50),
    "accounts/fireworks/routers/kimi-k2p7-code-fast": ("MoonshotAI/Kimi-K2.7-Code", 1.50),
    "accounts/fireworks/models/kimi-k2p6": ("MoonshotAI/Kimi-K2.6", 1.50),
    "accounts/fireworks/routers/kimi-k2p6-turbo": ("MoonshotAI/Kimi-K2.6", 1.50),
    "accounts/fireworks/models/kimi-k2p5": ("MoonshotAI/Kimi-K2.5", 1.50),
    "accounts/fireworks/models/deepseek-v4-pro": ("deepseek-ai/DeepSeek-V3", 1.25),
    "accounts/fireworks/models/deepseek-v4-flash": ("deepseek-ai/DeepSeek-V3", 1.25),
    "accounts/fireworks/models/glm-5p2": ("THUDM/glm-4-9b-chat", 1.50),
    "accounts/fireworks/models/glm-5p1": ("THUDM/glm-4-9b-chat", 1.50),
    "accounts/fireworks/routers/glm-5p1-fast": ("THUDM/glm-4-9b-chat", 1.50),
}

# Fallback for models that are not in the mapping above.
_APPROX_CHARS_PER_TOKEN = 4

# Vision input overhead placeholder.
_APPROX_IMAGE_INPUT_TOKENS = 256


def _text_length(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    return 0


def _approximate_tokens_from_text(text: str) -> int:
    length = _text_length(text)
    if length <= 0:
        return 0
    return max(1, (length + _APPROX_CHARS_PER_TOKEN - 1) // _APPROX_CHARS_PER_TOKEN)


def _collect_texts_from_content(content: Any) -> list[str]:
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
    return texts


def _count_images_in_content(content: Any) -> int:
    if not isinstance(content, list):
        return 0
    count = 0
    for part in content:
        if isinstance(part, dict) and part.get("type") in {"image_url", "input_image", "image"}:
            count += 1
    return count


class _LazyTokenizer:
    """Lazy-loading wrapper around a HuggingFace AutoTokenizer."""

    def __init__(self, hf_model_name: str, calibration_factor: float) -> None:
        self.hf_model_name = hf_model_name
        self.calibration_factor = calibration_factor
        self._tokenizer: Any | None = None

    def _load(self) -> Any:
        if self._tokenizer is None:
            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(
                self.hf_model_name,
                trust_remote_code=True,
            )
        return self._tokenizer

    def count(self, text: str) -> int:
        tokenizer = self._load()
        raw = len(tokenizer.encode(text, add_special_tokens=False))
        return max(1, int(raw * self.calibration_factor))


@lru_cache(maxsize=16)
def _get_tokenizer(hf_model_name: str, calibration_factor: float) -> _LazyTokenizer:
    return _LazyTokenizer(hf_model_name, calibration_factor)


def _tokenizer_for_model(upstream_model: str | None) -> _LazyTokenizer | None:
    if not upstream_model:
        return None
    mapping = _KNOWN_TOKENIZER_MAPPINGS.get(upstream_model)
    if mapping is None:
        return None
    hf_name, calibration = mapping
    return _get_tokenizer(hf_name, calibration)


def _estimate_text_tokens(text: str, upstream_model: str | None) -> int:
    tokenizer = _tokenizer_for_model(upstream_model)
    if tokenizer is not None:
        return tokenizer.count(text)
    return _approximate_tokens_from_text(text)


def estimate_input_tokens(payload: Mapping[str, Any], upstream_model: str | None) -> int:
    """Estimate input tokens from the upstream payload.

    Supports chat-style ``messages`` and Responses-style ``input``.
    Unknown models fall back to a character-based approximation.
    """
    if not isinstance(payload, Mapping):
        return 0

    total = 0
    image_count = 0

    if "messages" in payload:
        messages = payload.get("messages")
        if isinstance(messages, list):
            for message in messages:
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                for text in _collect_texts_from_content(content):
                    total += _estimate_text_tokens(text, upstream_model)
                image_count += _count_images_in_content(content)
                if message.get("role") in {"tool", "function"}:
                    tool_content = message.get("content")
                    if isinstance(tool_content, str):
                        total += _estimate_text_tokens(tool_content, upstream_model)

    elif "input" in payload:
        input_value = payload.get("input")
        if isinstance(input_value, str):
            total += _estimate_text_tokens(input_value, upstream_model)
        elif isinstance(input_value, list):
            for item in input_value:
                if not isinstance(item, dict):
                    if isinstance(item, str):
                        total += _estimate_text_tokens(item, upstream_model)
                    continue
                item_type = item.get("type")
                if item_type in {"message", None}:
                    content = item.get("content")
                    for text in _collect_texts_from_content(content):
                        total += _estimate_text_tokens(text, upstream_model)
                    image_count += _count_images_in_content(content)
                elif item_type in {"text", "input_text", "output_text"}:
                    text = item.get("text")
                    if isinstance(text, str):
                        total += _estimate_text_tokens(text, upstream_model)
                elif item_type in {"function_call_output", "tool_output"}:
                    output = item.get("output")
                    if isinstance(output, str):
                        total += _estimate_text_tokens(output, upstream_model)
                elif item_type == "function_call":
                    total += _estimate_text_tokens(
                        json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                        upstream_model,
                    )

    elif "prompt" in payload:
        prompt = payload["prompt"]
        if isinstance(prompt, str):
            total += _estimate_text_tokens(prompt, upstream_model)
        elif isinstance(prompt, list) and prompt and isinstance(prompt[0], str):
            for p in prompt:
                total += _estimate_text_tokens(p, upstream_model)

    total += image_count * _APPROX_IMAGE_INPUT_TOKENS
    return max(0, total)


def estimate_output_tokens_from_text(text: str, upstream_model: str | None) -> int:
    """Estimate output tokens from generated text."""
    if not text:
        return 0
    return _estimate_text_tokens(text, upstream_model)


def estimate_output_tokens_from_payload(payload: Mapping[str, Any], upstream_model: str | None) -> int:
    """Estimate output tokens from a non-streaming response payload."""
    if not isinstance(payload, Mapping):
        return 0

    total = 0

    # Chat completions response
    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    total += estimate_output_tokens_from_text(content, upstream_model)
                reasoning = message.get("reasoning_content")
                if isinstance(reasoning, str):
                    total += estimate_output_tokens_from_text(reasoning, upstream_model)
                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list):
                    for tool_call in tool_calls:
                        if not isinstance(tool_call, dict):
                            continue
                        function = tool_call.get("function")
                        if isinstance(function, dict):
                            arguments = function.get("arguments")
                            if isinstance(arguments, str):
                                total += estimate_output_tokens_from_text(arguments, upstream_model)
        return total

    # Responses API response
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "message":
                content = item.get("content")
                for text in _collect_texts_from_content(content):
                    total += estimate_output_tokens_from_text(text, upstream_model)
            elif item_type == "reasoning":
                summary = item.get("summary")
                if isinstance(summary, list):
                    for part in summary:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            total += estimate_output_tokens_from_text(part["text"], upstream_model)
            elif item_type == "function_call":
                arguments = item.get("arguments")
                if isinstance(arguments, str):
                    total += estimate_output_tokens_from_text(arguments, upstream_model)
        return total

    # Completions response
    if isinstance(payload.get("text"), str):
        return estimate_output_tokens_from_text(payload["text"], upstream_model)

    return 0


__all__ = [
    "estimate_input_tokens",
    "estimate_output_tokens_from_text",
    "estimate_output_tokens_from_payload",
]
