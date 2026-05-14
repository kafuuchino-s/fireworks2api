from __future__ import annotations

import pytest

from app.dataplane.fireworks.paths import resolve_inference_path


@pytest.mark.parametrize(
    ("upstream_base_url", "endpoint", "expected"),
    [
        ("https://api.fireworks.ai/inference", "chat_completions", "v1/chat/completions"),
        ("https://api.fireworks.ai/inference/", "responses", "v1/responses"),
        ("https://api.fireworks.ai/inference/v1", "completions", "completions"),
        ("https://api.fireworks.ai/inference/v1/", "embeddings", "embeddings"),
        ("https://custom.example/api", "rerank", "v1/rerank"),
        ("https://custom.example/api/v1", "anthropic_messages", "messages"),
        ("https://custom.example/api/v1/", "models", "models"),
    ],
)
def test_resolve_inference_path_variants(upstream_base_url: str, endpoint: str, expected: str) -> None:
    assert resolve_inference_path(upstream_base_url, endpoint) == expected


def test_canonical_fireworks_inference_paths_are_versioned() -> None:
    assert resolve_inference_path("https://api.fireworks.ai/inference", "chat_completions") == "v1/chat/completions"
    assert resolve_inference_path("https://api.fireworks.ai/inference", "responses_lifecycle") == "v1/responses"
