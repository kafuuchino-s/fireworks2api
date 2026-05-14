from __future__ import annotations

from typing import Any

from app.dataplane.fireworks.contracts import FIREWORKS_COMPLETIONS_SUPPORTED_FIELDS
from app.dataplane.fireworks.reasoning_capabilities import classify_reasoning_model
from app.products.openai.errors import raise_openai_error

from .common import COMPLETIONS_NESTED_FIELDS, COMPLETIONS_PUBLIC_FIELDS, _copy_allowed, count_prompt_images, _validate_bool, _validate_float_range, _validate_int_range, _validate_object, _validate_prompt, _validate_images
from .common import build_adapter_headers


def validate_completions_body(body: dict[str, Any]) -> None:
    if "model" not in body or "prompt" not in body:
        raise_openai_error("'model' and 'prompt' are required", param="model", code="missing_required_parameter")
    _validate_int_range(body, "n", min_value=1, max_value=128)
    _validate_float_range(body, "temperature", min_value=0, max_value=2)
    _validate_float_range(body, "top_p", min_value=0, max_value=1)
    _validate_int_range(body, "top_k", min_value=0, max_value=100)
    _validate_int_range(body, "max_tokens", positive=True)
    _validate_int_range(body, "max_completion_tokens", positive=True)
    _validate_bool(body, "stream")
    _validate_bool(body, "echo")
    _validate_bool(body, "return_token_ids")
    _validate_bool(body, "raw_output")
    _validate_bool(body, "perf_metrics_in_response")
    _validate_bool(body, "ignore_eos")
    _validate_object(body, "response_format")
    if "thinking" in body:
        _validate_object(body, "thinking")
        thinking = body["thinking"]
        if isinstance(thinking, dict):
            if "type" in thinking and not isinstance(thinking["type"], str):
                raise_openai_error("'thinking.type' must be a string", param="thinking.type", code="invalid_request_error")
            if "budget_tokens" in thinking:
                _validate_int_range(thinking, "budget_tokens", min_value=1024)
    _validate_object(body, "metadata")
    _validate_object(body, "reasoning")
    _validate_prompt(body["prompt"])
    if "images" in body:
        _validate_images(body["images"])
    if "reasoning_history" in body and not (body["reasoning_history"] is None or (isinstance(body["reasoning_history"], str) and body["reasoning_history"] in {"disabled", "interleaved", "preserved"})):
        raise_openai_error("'reasoning_history' must be disabled, interleaved, preserved, or null", param="reasoning_history", code="invalid_request_error")
    if "prediction" in body and not isinstance(body["prediction"], (dict, str)):
        raise_openai_error("'prediction' must be a string or object", param="prediction", code="invalid_request_error")
    if "max_completion_tokens" in body and "max_tokens" in body:
        raise_openai_error("'max_tokens' and 'max_completion_tokens' are mutually exclusive", param="max_completion_tokens", code="unsupported_parameter")
    if body.get("thinking") is not None and body.get("reasoning_effort") is not None:
        raise_openai_error("'thinking' and 'reasoning_effort' are mutually exclusive", param="thinking", code="unsupported_parameter")
    service_tier = body.get("service_tier")
    if isinstance(service_tier, str):
        tier = service_tier.strip().lower()
        if tier not in {"priority", "auto", "default", "flex"}:
            raise_openai_error("unsupported service_tier", param="service_tier", code="unsupported_parameter")
    elif service_tier is not None:
        raise_openai_error("unsupported service_tier", param="service_tier", code="unsupported_parameter")
    if "context_length_exceeded_behavior" in body and body["context_length_exceeded_behavior"] not in {"error", "truncate"}:
        raise_openai_error("unsupported context_length_exceeded_behavior", param="context_length_exceeded_behavior", code="invalid_request_error")
    unknown = sorted(set(body) - (COMPLETIONS_PUBLIC_FIELDS | COMPLETIONS_NESTED_FIELDS))
    for field in unknown:
        raise_openai_error(f"unknown parameter '{field}'", param=field, code="unknown_parameter")
    for field in sorted((set(body) & COMPLETIONS_PUBLIC_FIELDS) - FIREWORKS_COMPLETIONS_SUPPORTED_FIELDS):
        if field in COMPLETIONS_NESTED_FIELDS:
            continue
        raise_openai_error(f"'{field}' is not supported by Fireworks completions", param=field, code="unsupported_parameter")


def build_completions_adapter(context) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    body = context.body
    validate_completions_body(body)
    payload = _copy_allowed(body, FIREWORKS_COMPLETIONS_SUPPORTED_FIELDS)
    if payload.get("service_tier") in {"auto", "default", "flex"}:
        payload.pop("service_tier", None)
    if "max_completion_tokens" in payload:
        payload["max_tokens"] = payload.pop("max_completion_tokens")
        field_changes = [{"field": "max_completion_tokens", "to": "max_tokens"}]
    else:
        field_changes = []
    warnings = []
    capabilities = classify_reasoning_model(getattr(getattr(context, "resolved_model", None), "upstream_model", ""))
    if body.get("thinking") is not None and capabilities.supports_thinking is False:
        warnings.append("thinking is likely unsupported for this upstream model family")
    if body.get("reasoning_effort") is not None and capabilities.supports_reasoning_effort is False:
        warnings.append("reasoning_effort is likely unsupported for this upstream model family")
    if isinstance(body.get("prompt"), str) and "images" in body:
        prompt_images = count_prompt_images(body["prompt"])
        image_count = len(body["images"])
        if prompt_images != image_count:
            warnings.append(f"prompt image marker count ({prompt_images}) does not match images count ({image_count})")
    for field in COMPLETIONS_NESTED_FIELDS:
        if field in body:
            payload[field] = body[field]
    payload["model"] = context.resolved_model.upstream_model
    headers = build_adapter_headers(context)
    field_changes.insert(0, {"field": "model", "to": "model"})
    return payload, headers, {"field_changes": field_changes, "warnings": warnings}
