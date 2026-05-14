from __future__ import annotations

from typing import Any

from app.dataplane.fireworks.headers import build_upstream_headers
from app.dataplane.fireworks.contracts import (
    FIREWORKS_CHAT_EXTENSION_FIELDS,
    FIREWORKS_CHAT_SUPPORTED_FIELDS,
    FIREWORKS_COMPLETIONS_EXTENSION_FIELDS,
    FIREWORKS_COMPLETIONS_SUPPORTED_FIELDS,
    FIREWORKS_EMBEDDINGS_EXTENSION_FIELDS,
    FIREWORKS_EMBEDDINGS_SUPPORTED_FIELDS,
    FIREWORKS_RERANK_EXTENSION_FIELDS,
    FIREWORKS_RERANK_SUPPORTED_FIELDS,
    FIREWORKS_RESPONSES_EXTENSION_FIELDS,
    FIREWORKS_RESPONSES_SUPPORTED_FIELDS,
    OPENAI_TO_FIREWORKS_CHAT_FIELDS,
    OPENAI_TO_FIREWORKS_RESPONSES_FIELDS,
)

from app.products.openai.contracts import OPENAI_CHAT_ALL, OPENAI_COMPLETIONS_PUBLIC, OPENAI_EMBEDDINGS_PUBLIC, OPENAI_NOT_CHAT, OPENAI_NOT_RESPONSES, OPENAI_RERANK_PUBLIC, OPENAI_RESPONSES_ALL
from app.products.openai.errors import raise_openai_error

CHAT_PUBLIC_FIELDS = OPENAI_CHAT_ALL | FIREWORKS_CHAT_EXTENSION_FIELDS
RESPONSES_PUBLIC_FIELDS = OPENAI_RESPONSES_ALL | FIREWORKS_RESPONSES_EXTENSION_FIELDS
COMPLETIONS_PUBLIC_FIELDS = OPENAI_COMPLETIONS_PUBLIC | FIREWORKS_COMPLETIONS_EXTENSION_FIELDS
EMBEDDINGS_PUBLIC_FIELDS = OPENAI_EMBEDDINGS_PUBLIC | FIREWORKS_EMBEDDINGS_EXTENSION_FIELDS
RERANK_PUBLIC_FIELDS = OPENAI_RERANK_PUBLIC | FIREWORKS_RERANK_EXTENSION_FIELDS

CHAT_NESTED_FIELDS = {"tools", "tool_choice", "response_format", "thinking", "metadata", "reasoning", "text"}
COMPLETIONS_NESTED_FIELDS = {"response_format", "thinking", "metadata", "reasoning", "reasoning_history", "prediction"}


def _copy_allowed(body: dict[str, Any], allowlist: set[str]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k in allowlist}


def _reject_unknown_or_unsupported(field: str, *, public_fields: set[str], unsupported_fields: set[str]) -> None:
    if field in unsupported_fields or field in public_fields:
        raise_openai_error(f"'{field}' is not supported", param=field, code="unsupported_parameter")
    raise_openai_error(f"unknown parameter '{field}'", param=field, code="unknown_parameter")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_present(body: dict[str, Any], field: str) -> Any:
    if field not in body:
        raise_openai_error(f"'{field}' is required", param=field, code="missing_required_parameter")
    return body[field]


def _validate_int_range(body: dict[str, Any], field: str, *, min_value: int | None = None, max_value: int | None = None, positive: bool = False) -> None:
    if field not in body:
        return
    value = body[field]
    if not _is_int(value):
        raise_openai_error(f"'{field}' must be an integer", param=field, code="invalid_request_error")
    if positive and value <= 0:
        raise_openai_error(f"'{field}' must be a positive integer", param=field, code="invalid_request_error")
    if min_value is not None and value < min_value:
        raise_openai_error(f"'{field}' must be at least {min_value}", param=field, code="invalid_request_error")
    if max_value is not None and value > max_value:
        raise_openai_error(f"'{field}' must be at most {max_value}", param=field, code="invalid_request_error")


def _validate_float_range(body: dict[str, Any], field: str, *, min_value: float, max_value: float) -> None:
    if field not in body:
        return
    value = body[field]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise_openai_error(f"'{field}' must be a number", param=field, code="invalid_request_error")
    if value < min_value or value > max_value:
        raise_openai_error(f"'{field}' must be between {min_value} and {max_value}", param=field, code="invalid_request_error")


def _validate_bool(body: dict[str, Any], field: str) -> None:
    if field in body and not isinstance(body[field], bool):
        raise_openai_error(f"'{field}' must be a boolean", param=field, code="invalid_request_error")


def _validate_object(body: dict[str, Any], field: str) -> None:
    if field in body and not isinstance(body[field], dict):
        raise_openai_error(f"'{field}' must be an object", param=field, code="invalid_request_error")


def _validate_object_or_string(body: dict[str, Any], field: str) -> None:
    if field in body and not isinstance(body[field], (dict, str)):
        raise_openai_error(f"'{field}' must be a string or object", param=field, code="invalid_request_error")


def _validate_list(body: dict[str, Any], field: str) -> None:
    if field in body and not isinstance(body[field], list):
        raise_openai_error(f"'{field}' must be a list", param=field, code="invalid_request_error")


def _validate_list_of_ints(value: Any, *, field: str) -> None:
    if not isinstance(value, list) or not value or not all(_is_int(item) for item in value):
        raise_openai_error(f"'{field}' must be a list of integers", param=field, code="invalid_request_error")


def _validate_prompt(value: Any) -> None:
    if isinstance(value, str):
        return
    if isinstance(value, list) and value:
        if all(isinstance(item, str) for item in value):
            return
        if all(_is_int(item) for item in value):
            return
        if all(isinstance(item, list) and item and all(_is_int(subitem) for subitem in item) for item in value):
            return
    raise_openai_error("'prompt' must be a string or supported token array shape", param="prompt", code="invalid_request_error")


def _validate_images(value: Any) -> None:
    def _is_data_url(item: Any) -> bool:
        if not isinstance(item, str):
            return False
        return item.startswith("data:image/jpeg;base64,") or item.startswith("data:image/png;base64,") or item.startswith("data:image/webp;base64,")

    if not isinstance(value, list) or not value:
        raise_openai_error("'images' must be a list", param="images", code="invalid_request_error")
    if all(_is_data_url(item) for item in value):
        return
    if all(isinstance(item, list) and item and all(_is_data_url(subitem) for subitem in item) for item in value):
        return
    raise_openai_error("'images' must be a list of data URLs or list of lists of data URLs", param="images", code="invalid_request_error")


def count_prompt_images(prompt: Any) -> int:
    if isinstance(prompt, str):
        return prompt.count("<image>")
    if isinstance(prompt, list):
        count = 0
        for item in prompt:
            if isinstance(item, str):
                count += item.count("<image>")
        return count
    return 0


def _validate_list_of_strings(value: Any, *, field: str) -> None:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise_openai_error(f"'{field}' must be a non-empty list of strings", param=field, code="invalid_request_error")


def _validate_embeddings_input(value: Any) -> None:
    def _is_token_array(item: Any) -> bool:
        return isinstance(item, list) and item and all(_is_int(token) for token in item)

    if isinstance(value, str):
        if not value:
            raise_openai_error("'input' must not be empty", param="input", code="invalid_request_error")
        return
    if isinstance(value, dict):
        if not value:
            raise_openai_error("'input' must not be empty", param="input", code="invalid_request_error")
        return
    if isinstance(value, list):
        if not value or len(value) > 2048:
            raise_openai_error("'input' must not be empty", param="input", code="invalid_request_error")
        if all(isinstance(item, str) and item for item in value):
            return
        if all(_is_int(item) for item in value):
            return
        if all(_is_token_array(item) for item in value):
            return
        if all(isinstance(item, dict) and item for item in value):
            return
        raise_openai_error("'input' must be a string, object, list of strings, token array, token array batch, or list of objects", param="input", code="invalid_request_error")
        return
    raise_openai_error("'input' must be a string, object, list of strings, token array, token array batch, or list of objects", param="input", code="invalid_request_error")


def build_adapter_headers(context) -> dict[str, str]:
    return build_upstream_headers(getattr(context, "request_headers", {}), stable_key=getattr(context, "stable_key", ""), affinity_hash_secret=getattr(context.settings, "affinity_hash_secret", None) or getattr(context.settings, "log_hash_secret", None))
