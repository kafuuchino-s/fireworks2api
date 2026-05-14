from __future__ import annotations

from typing import Any

from app.dataplane.fireworks.contracts import FIREWORKS_EMBEDDINGS_SUPPORTED_FIELDS
from app.products.openai.errors import raise_openai_error

from .common import EMBEDDINGS_PUBLIC_FIELDS, _copy_allowed, _is_int, _validate_bool, _validate_embeddings_input, _validate_int_range, build_adapter_headers


def validate_embeddings_body(body: dict[str, Any]) -> None:
    if "model" not in body or "input" not in body:
        raise_openai_error("'model' and 'input' are required", param="model", code="missing_required_parameter")
    _validate_embeddings_input(body["input"])
    if "prompt_template" in body and (not isinstance(body["prompt_template"], str) or not body["prompt_template"].strip()):
        raise_openai_error("'prompt_template' must be a non-empty string", param="prompt_template", code="invalid_request_error")
    _validate_int_range(body, "dimensions", positive=True)
    _validate_bool(body, "normalize")
    if "user" in body and not isinstance(body["user"], str):
        raise_openai_error("'user' must be a string", param="user", code="invalid_request_error")
    if "encoding_format" in body:
        if body["encoding_format"] == "float":
            pass
        elif body["encoding_format"] == "base64":
            raise_openai_error("'encoding_format' is not supported by Fireworks embeddings", param="encoding_format", code="unsupported_parameter")
        else:
            raise_openai_error("'encoding_format' must be 'float' or 'base64'", param="encoding_format", code="invalid_request_error")
    if "return_logits" in body and (not isinstance(body["return_logits"], list) or not all(_is_int(item) for item in body["return_logits"])):
        raise_openai_error("'return_logits' must be a list of integers", param="return_logits", code="invalid_request_error")
    unknown = sorted(set(body) - EMBEDDINGS_PUBLIC_FIELDS)
    for field in unknown:
        raise_openai_error(f"unknown parameter '{field}'", param=field, code="unknown_parameter")
    for field in sorted((set(body) & EMBEDDINGS_PUBLIC_FIELDS) - FIREWORKS_EMBEDDINGS_SUPPORTED_FIELDS):
        if field in {"encoding_format", "user"}:
            continue
        raise_openai_error(f"'{field}' is not supported by Fireworks embeddings", param=field, code="unsupported_parameter")


def build_embeddings_adapter(context) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    body = context.body
    validate_embeddings_body(body)
    payload = _copy_allowed(body, FIREWORKS_EMBEDDINGS_SUPPORTED_FIELDS)
    payload["model"] = context.resolved_model.upstream_model
    headers = build_adapter_headers(context)
    return payload, headers, {"field_changes": [{"field": "model", "to": "model"}], "warnings": []}
