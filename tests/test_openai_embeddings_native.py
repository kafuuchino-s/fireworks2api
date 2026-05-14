from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.products.openai.fireworks_native.embeddings import build_embeddings_adapter
from app.products.openai.errors import OpenAIRequestError


def _context(body: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(body=body, settings=SimpleNamespace(affinity_hash_secret="a", log_hash_secret="b"), request_headers={}, stable_key="stable", resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"))


def test_embeddings_native_forwards_supported_fields() -> None:
    payload, _, _ = build_embeddings_adapter(_context({"model": "test", "input": "hello", "prompt_template": "Embed: {text}", "dimensions": 16, "return_logits": [], "normalize": True}))

    assert payload["prompt_template"] == "Embed: {text}"
    assert payload["dimensions"] == 16
    assert payload["return_logits"] == []


@pytest.mark.parametrize("input_value", ["hello", ["a", "b"], [1, 2, 3], [[1, 2], [3, 4]], {"text": "hello"}, [{"text": "hello"}]])
def test_embeddings_native_accepts_valid_input_shapes(input_value) -> None:
    payload, _, _ = build_embeddings_adapter(_context({"model": "test", "input": input_value}))
    assert payload["input"] == input_value


@pytest.mark.parametrize("field,value", [("encoding_format", "base64"), ("user", "u")])
def test_embeddings_native_rejects_unsupported_fields(field, value) -> None:
    if field == "user":
        payload, _, _ = build_embeddings_adapter(_context({"model": "test", "input": "hello", field: value}))
        assert "user" not in payload
        return
    with pytest.raises(OpenAIRequestError) as excinfo:
        build_embeddings_adapter(_context({"model": "test", "input": "hello", field: value}))
    assert excinfo.value.code == "unsupported_parameter"


def test_embeddings_native_rejects_non_string_user() -> None:
    with pytest.raises(OpenAIRequestError) as excinfo:
        build_embeddings_adapter(_context({"model": "test", "input": "hello", "user": 123}))

    assert excinfo.value.code == "invalid_request_error"
    assert excinfo.value.param == "user"


def test_embeddings_native_rejects_base64_encoding_format_shape() -> None:
    with pytest.raises(OpenAIRequestError) as excinfo:
        build_embeddings_adapter(_context({"model": "test", "input": "hello", "encoding_format": "base64"}))

    assert excinfo.value.code == "unsupported_parameter"
    assert excinfo.value.param == "encoding_format"


def test_embeddings_native_accepts_float_encoding_format_and_drops_it() -> None:
    payload, _, _ = build_embeddings_adapter(_context({"model": "test", "input": "hello", "encoding_format": "float", "user": "abc"}))

    assert "encoding_format" not in payload
    assert "user" not in payload
