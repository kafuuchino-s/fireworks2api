from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.products.openai.fireworks_native.rerank import build_rerank_adapter
from app.products.openai.errors import OpenAIRequestError


def _context(body: dict[str, object], upstream_model: str | None = "accounts/fireworks/models/test") -> SimpleNamespace:
    return SimpleNamespace(body=body, settings=SimpleNamespace(affinity_hash_secret="a", log_hash_secret="b"), request_headers={}, stable_key="stable", resolved_model=SimpleNamespace(upstream_model=upstream_model))


def test_rerank_native_omits_model_when_missing() -> None:
    payload, _, _ = build_rerank_adapter(_context({"query": "q", "documents": ["a"]}, upstream_model=None))
    assert "model" not in payload


def test_rerank_native_forwards_optional_fields() -> None:
    payload, _, _ = build_rerank_adapter(_context({"model": "test", "query": "q", "documents": ["a"], "top_n": 3, "return_documents": True, "task": None}))
    assert payload["top_n"] == 3
    assert payload["return_documents"] is True
    assert "task" in payload and payload["task"] is None


@pytest.mark.parametrize("field,value", [("query", ""), ("documents", []), ("top_n", 0)])
def test_rerank_native_validates_required_values(field, value) -> None:
    body = {"query": "q", "documents": ["a"]}
    body[field] = value
    with pytest.raises(OpenAIRequestError) as excinfo:
        build_rerank_adapter(_context(body))
    assert excinfo.value.code == "invalid_request_error"
