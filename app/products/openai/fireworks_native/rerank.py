from __future__ import annotations

from typing import Any

from app.dataplane.fireworks.contracts import FIREWORKS_RERANK_SUPPORTED_FIELDS
from app.products.openai.errors import raise_openai_error

from .common import RERANK_PUBLIC_FIELDS, _copy_allowed, _validate_bool, _validate_int_range, _validate_list_of_strings, build_adapter_headers


def validate_rerank_body(body: dict[str, Any]) -> None:
    if "query" not in body or "documents" not in body:
        raise_openai_error("'query' and 'documents' are required", param="query", code="missing_required_parameter")
    if "model" not in body:
        body["model"] = None
    if not isinstance(body["query"], str):
        raise_openai_error("'query' must be a string", param="query", code="invalid_request_error")
    _validate_list_of_strings(body["documents"], field="documents")
    _validate_int_range(body, "top_n", positive=True)
    _validate_bool(body, "return_documents")
    if "task" in body and body["task"] is not None and not isinstance(body["task"], str):
        raise_openai_error("'task' must be a string or null", param="task", code="invalid_request_error")
    unknown = sorted(set(body) - RERANK_PUBLIC_FIELDS)
    for field in unknown:
        raise_openai_error(f"unknown parameter '{field}'", param=field, code="unknown_parameter")
    for field in sorted((set(body) & RERANK_PUBLIC_FIELDS) - FIREWORKS_RERANK_SUPPORTED_FIELDS):
        raise_openai_error(f"'{field}' is not supported by Fireworks rerank", param=field, code="unsupported_parameter")


def build_rerank_adapter(context) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    body = context.body
    validate_rerank_body(body)
    payload = _copy_allowed(body, FIREWORKS_RERANK_SUPPORTED_FIELDS)
    if getattr(context.resolved_model, "upstream_model", None):
        payload["model"] = context.resolved_model.upstream_model
    else:
        payload.pop("model", None)
    headers = build_adapter_headers(context)
    return payload, headers, {"field_changes": [{"field": "model", "to": "model"}], "warnings": []}
