from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.products.openai.errors import raise_openai_error


_SUPPORTED_TOP_LEVEL_FIELDS = {
    "model",
    "input",
    "instructions",
    "temperature",
    "top_p",
    "max_output_tokens",
    "max_tokens",
    "user",
    "metadata",
    "prompt_cache_key",
    "prompt_cache_isolation_key",
    "perf_metrics_in_response",
    "service_tier",
}


def _simple_text_from_message_content(content: Any, *, index: int) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part_index, part in enumerate(content):
            if not isinstance(part, dict):
                raise_openai_error("message content parts must be objects", param=f"input[{index}].content[{part_index}]", code="invalid_request_error")
            part_type = part.get("type")
            if part_type not in {"text", "input_text"}:
                raise_openai_error("only simple text messages are supported", param=f"input[{index}].content[{part_index}].type", code="unsupported_parameter")
            text = part.get("text")
            if not isinstance(text, str):
                raise_openai_error("text message parts require text", param=f"input[{index}].content[{part_index}].text", code="invalid_request_error")
            parts.append(text)
        return "\n".join(parts)
    raise_openai_error("only simple text messages are supported", param=f"input[{index}].content", code="unsupported_parameter")


def is_priority_responses_fallback_eligible(body: Mapping[str, Any]) -> bool:
    service_tier = body.get("service_tier")
    return isinstance(service_tier, str) and service_tier.strip().lower() == "priority"


def validate_priority_responses_fallback_body(body: Mapping[str, Any]) -> None:
    if not isinstance(body.get("input"), (str, list)):
        raise_openai_error("'input' must be a string or list of text messages", param="input", code="invalid_request_error")
    if "stream" in body and body["stream"] is True:
        raise_openai_error("streaming is not supported for the priority fallback", param="stream", code="unsupported_parameter")
    for field in ("previous_response_id", "store", "tools", "tool_choice", "max_tool_calls", "parallel_tool_calls", "reasoning", "include", "truncation"):
        if body.get(field) not in (None, False):
            raise_openai_error(f"'{field}' is not supported for the priority fallback", param=field, code="unsupported_parameter")
    if "text" in body:
        raise_openai_error("structured text output is not supported for the priority fallback", param="text", code="unsupported_parameter")
    for field in sorted(set(body) - _SUPPORTED_TOP_LEVEL_FIELDS):
        raise_openai_error(f"unknown parameter '{field}'", param=field, code="unknown_parameter")

    input_value = body["input"]
    if isinstance(input_value, list):
        for index, item in enumerate(input_value):
            if not isinstance(item, dict):
                raise_openai_error("input list items must be objects", param=f"input[{index}]", code="invalid_request_error")
            item_type = item.get("type")
            if item_type not in (None, "message"):
                raise_openai_error(f"unsupported input item type '{item_type}'", param=f"input[{index}].type", code="unsupported_parameter")
            if item.get("role") not in {"system", "user", "assistant"}:
                raise_openai_error("unsupported message role", param=f"input[{index}].role", code="unsupported_parameter")
            _simple_text_from_message_content(item.get("content"), index=index)
    if "instructions" in body and not isinstance(body["instructions"], str):
        raise_openai_error("'instructions' must be a string", param="instructions", code="invalid_request_error")
    if "max_output_tokens" in body and "max_tokens" in body:
        raise_openai_error("'max_output_tokens' and 'max_tokens' are mutually exclusive", param="max_tokens", code="unsupported_parameter")
    if "user" in body and not isinstance(body["user"], str):
        raise_openai_error("'user' must be a string", param="user", code="invalid_request_error")
    if "metadata" in body and not isinstance(body["metadata"], dict):
        raise_openai_error("'metadata' must be an object", param="metadata", code="invalid_request_error")


def build_priority_chat_payload(body: Mapping[str, Any], *, upstream_model: str) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_priority_responses_fallback_body(body)
    messages: list[dict[str, str]] = []
    instructions = body.get("instructions")
    if isinstance(instructions, str) and instructions:
        messages.append({"role": "system", "content": instructions})
    input_value = body["input"]
    if isinstance(input_value, str):
        messages.append({"role": "user", "content": input_value})
    else:
        for index, item in enumerate(input_value):
            messages.append({"role": item["role"], "content": _simple_text_from_message_content(item.get("content"), index=index)})
    payload: dict[str, Any] = {"model": upstream_model, "messages": messages, "service_tier": "priority"}
    if "max_output_tokens" in body:
        payload["max_tokens"] = body["max_output_tokens"]
    elif "max_tokens" in body:
        payload["max_tokens"] = body["max_tokens"]
    for field in ("temperature", "top_p", "user", "metadata", "prompt_cache_key", "prompt_cache_isolation_key", "perf_metrics_in_response"):
        if field in body:
            payload[field] = body[field]
    report = {"field_changes": [{"field": "input", "action": "map", "to": "messages"}, {"field": "service_tier", "action": "preserve"}], "warnings": []}
    return payload, report


def synthesize_responses_from_chat(chat_response: Mapping[str, Any], *, model: str, upstream_model: str, perf_metrics_in_response: bool | None = None) -> dict[str, Any]:
    choices = chat_response.get("choices") or []
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first, dict) else {}
    content = message.get("content") if isinstance(message, dict) else None
    finish_reason = first.get("finish_reason") if isinstance(first, dict) else None
    output = []
    if isinstance(content, str):
        output = [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": content}]}]
    usage = chat_response.get("usage") if isinstance(chat_response.get("usage"), dict) else {}
    input_details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    output_details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
    response_usage = {
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }
    if input_details:
        response_usage["input_tokens_details"] = input_details
    if output_details:
        response_usage["output_tokens_details"] = output_details
    chat_id = chat_response.get("id") if isinstance(chat_response.get("id"), str) else "unknown"
    response: dict[str, Any] = {
        "id": f"resp_fallback_{chat_id}",
        "object": "response",
        "status": "incomplete" if finish_reason == "length" else "completed",
        "model": model,
        "output": output,
        "usage": response_usage,
        "service_tier": "priority",
        "store": False,
        "provider": {"name": "fireworks", "endpoint": "chat_completions", "upstream_model": upstream_model},
    }
    if finish_reason == "length":
        response["incomplete_details"] = {"reason": "max_output_tokens"}
    if perf_metrics_in_response is not None:
        response["perf_metrics_in_response"] = perf_metrics_in_response
    return response
