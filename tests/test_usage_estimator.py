from __future__ import annotations

import pytest

from app.dataplane.usage_estimator import (
    estimate_input_tokens,
    estimate_output_tokens_from_payload,
)


# Mark tests that require the optional transformers/tiktoken stack.
_transformers = pytest.importorskip("transformers")
_tiktoken = pytest.importorskip("tiktoken")


@pytest.mark.parametrize(
    ("upstream_model", "prompt", "expected_min", "expected_max"),
    [
        # Known Kimi models: tokenizer should give a non-zero, bounded estimate.
        ("accounts/fireworks/models/kimi-k2p7-code", "Say hello.", 1, 50),
        ("accounts/fireworks/routers/kimi-k2p7-code-fast", "Say hello.", 1, 50),
        ("accounts/fireworks/models/kimi-k2p6", "Say hello.", 1, 50),
        ("accounts/fireworks/routers/kimi-k2p6-turbo", "Say hello.", 1, 50),
        ("accounts/fireworks/models/kimi-k2p5", "Say hello.", 1, 50),
        # DeepSeek models.
        ("accounts/fireworks/models/deepseek-v4-pro", "Say hello.", 1, 50),
        ("accounts/fireworks/models/deepseek-v4-flash", "Say hello.", 1, 50),
        # GLM models.
        ("accounts/fireworks/models/glm-5p1", "Say hello.", 1, 50),
        ("accounts/fireworks/routers/glm-5p1-fast", "Say hello.", 1, 50),
    ],
)
def test_estimate_input_tokens_for_known_models(
    upstream_model: str, prompt: str, expected_min: int, expected_max: int
) -> None:
    payload = {"model": upstream_model, "input": prompt}
    result = estimate_input_tokens(payload, upstream_model)
    assert expected_min <= result <= expected_max


def test_estimate_input_tokens_handles_messages_list() -> None:
    payload = {
        "model": "accounts/fireworks/models/kimi-k2p7-code",
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Say hello."},
        ],
    }
    upstream_model = "accounts/fireworks/models/kimi-k2p7-code"
    result = estimate_input_tokens(payload, upstream_model)
    assert result > 0


def test_estimate_input_tokens_handles_image_parts() -> None:
    payload = {
        "model": "accounts/fireworks/models/kimi-k2p7-code",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "describe this"},
                    {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                ],
            }
        ],
    }
    upstream_model = "accounts/fireworks/models/kimi-k2p7-code"
    result = estimate_input_tokens(payload, upstream_model)
    # Image parts add a fixed overhead so the estimate should be larger than text alone.
    assert result > 256


def test_estimate_input_tokens_unknown_model_fallback() -> None:
    payload = {"model": "accounts/fireworks/models/unknown", "input": "Say hello."}
    result = estimate_input_tokens(payload, "accounts/fireworks/models/unknown")
    assert result > 0


def test_estimate_output_tokens_from_responses_payload() -> None:
    payload = {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Hello, how can I help you?"}],
            }
        ]
    }
    result = estimate_output_tokens_from_payload(payload, "accounts/fireworks/models/kimi-k2p7-code")
    assert result > 0


def test_estimate_output_tokens_from_chat_payload() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Hello, how can I help you?",
                }
            }
        ]
    }
    result = estimate_output_tokens_from_payload(payload, "accounts/fireworks/models/kimi-k2p7-code")
    assert result > 0
