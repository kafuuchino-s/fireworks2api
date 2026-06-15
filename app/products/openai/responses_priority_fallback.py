from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.dataplane.fireworks.sampling_defaults import apply_model_sampling_defaults, needs_reasoning_top_k_default
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
_REASONING_STABILITY_ACCEPT_DROP_FIELDS = {
    "include",
    "parallel_tool_calls",
    "reasoning",
    "store",
    "stream_options",
    "text",
    "truncation",
    "previous_response_id",
    "max_tool_calls",
}
_REASONING_STABILITY_EXTRA_FIELDS = {
    "stream",
    "thinking",
    "top_k",
    "tools",
    "tool_choice",
}
_CHAT_SERVICE_TIERS = {"priority", "auto", "default", "flex", "scale"}


def _json_string(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _chat_tool_from_responses_tool(tool: Any, *, index: int, allow_drop: bool = False) -> dict[str, Any] | None:
    if not isinstance(tool, dict) or tool.get("type") != "function":
        if allow_drop:
            return None
        raise_openai_error("Responses to Chat fallback only supports function tools", param=f"tools[{index}].type", code="unsupported_parameter")
    function = dict(tool["function"]) if isinstance(tool.get("function"), dict) else {key: value for key, value in tool.items() if key in {"name", "description", "parameters", "strict"}}
    name = function.get("name")
    if not isinstance(name, str) or not name.strip():
        raise_openai_error("function tools require name", param=f"tools[{index}].name", code="invalid_request_error")
    if "parameters" in function and not isinstance(function["parameters"], dict):
        raise_openai_error("function.parameters must be an object", param=f"tools[{index}].parameters", code="invalid_request_error")
    if "strict" in function and not isinstance(function["strict"], bool):
        raise_openai_error("function.strict must be a boolean", param=f"tools[{index}].strict", code="invalid_request_error")
    return {"type": "function", "function": function}


def _chat_tool_choice_from_responses(tool_choice: Any, *, allow_drop: bool = False) -> Any:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        if tool_choice not in {"auto", "none", "required"}:
            if allow_drop:
                return None
            raise_openai_error("unsupported tool_choice", param="tool_choice", code="unsupported_parameter")
        return tool_choice
    if not isinstance(tool_choice, dict):
        raise_openai_error("'tool_choice' must be a string or object", param="tool_choice", code="invalid_request_error")
    if tool_choice.get("type") != "function":
        if allow_drop:
            return None
        raise_openai_error("tool_choice object must select a function", param="tool_choice.type", code="unsupported_parameter")
    function = tool_choice.get("function") if isinstance(tool_choice.get("function"), dict) else {}
    name = function.get("name") or tool_choice.get("name")
    if not isinstance(name, str) or not name.strip():
        raise_openai_error("tool_choice function name is required", param="tool_choice.function.name", code="invalid_request_error")
    return {"type": "function", "function": {"name": name.strip()}}


def _record_lossy_drop(
    field_changes: list[dict[str, Any]] | None,
    warnings: list[str] | None,
    *,
    field: str,
    reason: str,
    detail: str,
    type_name: str | None = None,
) -> None:
    if field_changes is not None:
        change: dict[str, Any] = {"field": field, "action": "dropped", "reason": reason}
        if type_name is not None:
            change["type"] = type_name
        field_changes.append(change)
    if warnings is not None:
        warnings.append(detail)


def _fallback_public_fields(*, allow_stream: bool, allow_bridge_drops: bool) -> set[str]:
    public_fields = set(_SUPPORTED_TOP_LEVEL_FIELDS)
    if allow_stream:
        public_fields.add("stream")
    if allow_bridge_drops:
        public_fields.update(_REASONING_STABILITY_ACCEPT_DROP_FIELDS)
        public_fields.update(_REASONING_STABILITY_EXTRA_FIELDS)
    return public_fields


def _simple_text_from_message_content(
    content: Any,
    *,
    index: int,
    drop_empty_text_parts: bool = False,
    drop_unsupported_parts: bool = False,
    field_changes: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    fallback_reason: str = "fireworks_reasoning_stability",
) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part_index, part in enumerate(content):
            if not isinstance(part, dict):
                if drop_unsupported_parts:
                    _record_lossy_drop(
                        field_changes,
                        warnings,
                        field=f"input[{index}].content[{part_index}]",
                        reason=fallback_reason,
                        detail="non-object content part was dropped in the Responses to Chat fallback",
                        type_name=type(part).__name__,
                    )
                    continue
                raise_openai_error("message content parts must be objects", param=f"input[{index}].content[{part_index}]", code="invalid_request_error")
            part_type = part.get("type")
            if part_type not in {"text", "input_text", "output_text"}:
                if drop_unsupported_parts:
                    _record_lossy_drop(
                        field_changes,
                        warnings,
                        field=f"input[{index}].content[{part_index}]",
                        reason=fallback_reason,
                        detail="non-text content part was dropped in the Responses to Chat fallback",
                        type_name=str(part_type),
                    )
                    continue
                raise_openai_error("only simple text messages are supported", param=f"input[{index}].content[{part_index}].type", code="unsupported_parameter")
            text = part.get("text")
            if not isinstance(text, str):
                if drop_unsupported_parts:
                    _record_lossy_drop(
                        field_changes,
                        warnings,
                        field=f"input[{index}].content[{part_index}].text",
                        reason=fallback_reason,
                        detail="content part without text was dropped in the Responses to Chat fallback",
                        type_name=str(part_type),
                    )
                    continue
                raise_openai_error("text message parts require text", param=f"input[{index}].content[{part_index}].text", code="invalid_request_error")
            if drop_empty_text_parts and not text.strip():
                _record_lossy_drop(
                    field_changes,
                    warnings,
                    field=f"input[{index}].content[{part_index}]",
                    reason=fallback_reason,
                    detail="empty text content part was dropped in the Responses to Chat fallback",
                    type_name=str(part_type),
                )
                continue
            parts.append(text)
        return "\n".join(parts)
    if drop_unsupported_parts:
        _record_lossy_drop(
            field_changes,
            warnings,
            field=f"input[{index}].content",
            reason=fallback_reason,
            detail="non-text message content was dropped in the Responses to Chat fallback",
            type_name=type(content).__name__,
        )
        return ""
    raise_openai_error("only simple text messages are supported", param=f"input[{index}].content", code="unsupported_parameter")


def _extract_responses_reasoning_text(item: Mapping[str, Any]) -> str:
    parts: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str) and value:
            parts.append(value)
            return
        if not isinstance(value, list):
            return
        for part in value:
            if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"]:
                parts.append(part["text"])

    collect(item.get("summary"))
    if not parts:
        collect(item.get("content"))
    return "\n".join(parts)


def _extract_reasoning_effort(body: Mapping[str, Any]) -> str | None:
    reasoning = body.get("reasoning")
    if not isinstance(reasoning, Mapping):
        return None
    effort = reasoning.get("effort")
    return effort.strip() if isinstance(effort, str) and effort.strip() else None


def _chat_content_blank(content: Any) -> bool:
    if content is None:
        return True
    if isinstance(content, str):
        return not content.strip()
    if isinstance(content, list):
        return not any(isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"].strip() for part in content)
    return False


def _normalize_chat_tool_messages(
    messages: list[dict[str, Any]],
    *,
    field_changes: list[dict[str, Any]],
    warnings: list[str],
    fallback_reason: str,
) -> list[dict[str, Any]]:
    replies: dict[str, dict[str, Any]] = {}
    for message in messages:
        if message.get("role") == "tool":
            tool_call_id = message.get("tool_call_id")
            if isinstance(tool_call_id, str) and tool_call_id:
                replies[tool_call_id] = message

    out: list[dict[str, Any]] = []
    used_replies: set[str] = set()
    for message in messages:
        if message.get("role") == "tool":
            continue
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            out.append(message)
            continue

        kept: list[dict[str, Any]] = []
        for tool_call in tool_calls:
            call_id = tool_call.get("id") if isinstance(tool_call, dict) else None
            if isinstance(call_id, str) and call_id in replies:
                kept.append(tool_call)
                continue
            _record_lossy_drop(
                field_changes,
                warnings,
                field=f"input.function_call[{call_id or 'unknown'}]",
                reason=fallback_reason,
                detail="unanswered function_call item was dropped in the Responses to Chat fallback",
            )

        if not kept:
            if _chat_content_blank(message.get("content")):
                _record_lossy_drop(
                    field_changes,
                    warnings,
                    field="input.assistant_tool_call_message",
                    reason=fallback_reason,
                    detail="assistant tool_call message without matched outputs was dropped in the Responses to Chat fallback",
                )
                continue
            message = {key: value for key, value in message.items() if key not in {"tool_calls", "reasoning_content"}}
            out.append(message)
            continue

        normalized = dict(message)
        normalized["tool_calls"] = kept
        out.append(normalized)
        for tool_call in kept:
            call_id = tool_call.get("id")
            if isinstance(call_id, str) and call_id in replies:
                out.append(replies[call_id])
                used_replies.add(call_id)

    for call_id in set(replies) - used_replies:
        _record_lossy_drop(
            field_changes,
            warnings,
            field=f"input.function_call_output[{call_id}]",
            reason=fallback_reason,
            detail="orphan function_call_output item was dropped in the Responses to Chat fallback",
        )
    return out


def is_priority_responses_fallback_eligible(body: Mapping[str, Any]) -> bool:
    service_tier = body.get("service_tier")
    return isinstance(service_tier, str) and service_tier.strip().lower() == "priority"


def is_reasoning_responses_fallback_eligible(upstream_model: str | None) -> bool:
    return needs_reasoning_top_k_default(upstream_model)


def validate_priority_responses_fallback_body(body: Mapping[str, Any], *, allow_stream: bool = False, allow_bridge_drops: bool = False) -> None:
    if not isinstance(body.get("input"), (str, list)):
        raise_openai_error("'input' must be a string or list of text messages", param="input", code="invalid_request_error")
    if not allow_stream and "stream" in body and body["stream"] is True:
        raise_openai_error("streaming is not supported for the priority fallback", param="stream", code="unsupported_parameter")
    hard_unsupported = () if allow_bridge_drops else ("previous_response_id", "tools", "tool_choice", "max_tool_calls")
    for field in hard_unsupported:
        if body.get(field) not in (None, False):
            raise_openai_error(f"'{field}' is not supported for the priority fallback", param=field, code="unsupported_parameter")
    if not allow_bridge_drops:
        for field in ("store", "parallel_tool_calls", "reasoning", "include", "truncation"):
            if body.get(field) not in (None, False):
                raise_openai_error(f"'{field}' is not supported for the priority fallback", param=field, code="unsupported_parameter")
    if "text" in body and not allow_bridge_drops:
        raise_openai_error("structured text output is not supported for the priority fallback", param="text", code="unsupported_parameter")
    public_fields = _fallback_public_fields(allow_stream=allow_stream, allow_bridge_drops=allow_bridge_drops)
    for field in sorted(set(body) - public_fields):
        if not allow_bridge_drops:
            raise_openai_error(f"unknown parameter '{field}'", param=field, code="unknown_parameter")

    input_value = body["input"]
    if isinstance(input_value, list):
        for index, item in enumerate(input_value):
            if not isinstance(item, dict):
                if allow_bridge_drops and isinstance(item, str):
                    continue
                if allow_bridge_drops:
                    continue
                raise_openai_error("input list items must be objects", param=f"input[{index}]", code="invalid_request_error")
            item_type = item.get("type")
            if allow_bridge_drops and item_type == "reasoning":
                continue
            if allow_bridge_drops and item_type in {"function_call", "function_call_output"}:
                continue
            if allow_bridge_drops and item_type not in (None, "message", "output_text", "input_text", "text"):
                continue
            if item_type not in (None, "message", "output_text"):
                raise_openai_error(f"unsupported input item type '{item_type}'", param=f"input[{index}].type", code="unsupported_parameter")
            if item_type == "output_text":
                if not isinstance(item.get("text"), str) or not item["text"].strip():
                    raise_openai_error("output_text items require text", param=f"input[{index}].text", code="invalid_request_error")
                continue
            if allow_bridge_drops and item_type in {"input_text", "text"}:
                if not isinstance(item.get("text"), str):
                    raise_openai_error("text items require text", param=f"input[{index}].text", code="invalid_request_error")
                continue
            role = item.get("role", "user" if allow_bridge_drops else None)
            if role not in {"developer", "system", "user", "assistant"}:
                raise_openai_error("unsupported message role", param=f"input[{index}].role", code="unsupported_parameter")
            _simple_text_from_message_content(item.get("content"), index=index, drop_empty_text_parts=allow_bridge_drops)
    if "instructions" in body and not isinstance(body["instructions"], str):
        raise_openai_error("'instructions' must be a string", param="instructions", code="invalid_request_error")
    if "max_output_tokens" in body and "max_tokens" in body:
        raise_openai_error("'max_output_tokens' and 'max_tokens' are mutually exclusive", param="max_tokens", code="unsupported_parameter")
    if "user" in body and not isinstance(body["user"], str):
        raise_openai_error("'user' must be a string", param="user", code="invalid_request_error")
    if "metadata" in body and not isinstance(body["metadata"], dict):
        raise_openai_error("'metadata' must be an object", param="metadata", code="invalid_request_error")
    if "previous_response_id" in body and not allow_bridge_drops:
        if not isinstance(body["previous_response_id"], str):
            raise_openai_error("'previous_response_id' must be a string", param="previous_response_id", code="invalid_request_error")
        if not body["previous_response_id"].strip():
            raise_openai_error("'previous_response_id' must not be empty", param="previous_response_id", code="invalid_request_error")
    if "max_tool_calls" in body and body["max_tool_calls"] is not None and not allow_bridge_drops:
        max_tool_calls = body["max_tool_calls"]
        if not isinstance(max_tool_calls, int) or isinstance(max_tool_calls, bool) or max_tool_calls <= 0:
            raise_openai_error("'max_tool_calls' must be a positive integer", param="max_tool_calls", code="invalid_request_error")
    service_tier = body.get("service_tier")
    if isinstance(service_tier, str) and allow_bridge_drops:
        if service_tier.strip().lower() not in _CHAT_SERVICE_TIERS:
            raise_openai_error("unsupported service_tier", param="service_tier", code="unsupported_parameter")
    elif service_tier is not None and not is_priority_responses_fallback_eligible(body):
        raise_openai_error("unsupported service_tier", param="service_tier", code="unsupported_parameter")
    if "stream" in body and not isinstance(body["stream"], bool):
        raise_openai_error("'stream' must be a boolean", param="stream", code="invalid_request_error")
    if "stream_options" in body and body["stream_options"] is not None and not isinstance(body["stream_options"], dict) and not allow_bridge_drops:
        raise_openai_error("'stream_options' must be an object", param="stream_options", code="invalid_request_error")
    if "thinking" in body and body["thinking"] is not None and not isinstance(body["thinking"], dict):
        raise_openai_error("'thinking' must be an object", param="thinking", code="invalid_request_error")
    if "reasoning" in body and body["reasoning"] is not None and not isinstance(body["reasoning"], dict):
        raise_openai_error("'reasoning' must be an object", param="reasoning", code="invalid_request_error")
    if isinstance(body.get("reasoning"), dict) and "effort" in body["reasoning"] and body["reasoning"]["effort"] is not None and not isinstance(body["reasoning"]["effort"], str):
        raise_openai_error("'reasoning.effort' must be a string", param="reasoning.effort", code="invalid_request_error")
    if "top_k" in body and body["top_k"] is not None:
        top_k = body["top_k"]
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 0 or top_k > 100:
            raise_openai_error("'top_k' must be an integer between 0 and 100", param="top_k", code="invalid_request_error")
    if "tools" in body and body["tools"] is not None:
        if not isinstance(body["tools"], list):
            raise_openai_error("'tools' must be a list", param="tools", code="invalid_request_error")
        for index, tool in enumerate(body["tools"]):
            _chat_tool_from_responses_tool(tool, index=index, allow_drop=allow_bridge_drops)
    if "tool_choice" in body and body["tool_choice"] is not None:
        _chat_tool_choice_from_responses(body["tool_choice"], allow_drop=allow_bridge_drops)


def build_priority_chat_payload(
    body: Mapping[str, Any],
    *,
    upstream_model: str,
    allow_stream: bool = False,
    allow_bridge_drops: bool = False,
    fallback_reason: str = "priority",
) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_priority_responses_fallback_body(body, allow_stream=allow_stream, allow_bridge_drops=allow_bridge_drops)
    report = {"field_changes": [{"field": "input", "action": "map", "to": "messages"}], "warnings": []}
    messages: list[dict[str, Any]] = []
    instructions = body.get("instructions")
    if isinstance(instructions, str) and instructions:
        messages.append({"role": "system", "content": instructions})
    input_value = body["input"]
    if isinstance(input_value, str):
        messages.append({"role": "user", "content": input_value})
    else:
        pending_reasoning = ""
        for index, item in enumerate(input_value):
            if not isinstance(item, dict):
                if allow_bridge_drops and isinstance(item, str):
                    messages.append({"role": "user", "content": item})
                    pending_reasoning = ""
                    continue
                if allow_bridge_drops:
                    _record_lossy_drop(
                        report["field_changes"],
                        report["warnings"],
                        field=f"input[{index}]",
                        reason=fallback_reason,
                        detail="non-object input item was dropped in the Responses to Chat fallback",
                        type_name=type(item).__name__,
                    )
                    pending_reasoning = ""
                    continue
            item_type = item.get("type")
            if allow_bridge_drops and item_type == "reasoning":
                pending_reasoning = _extract_responses_reasoning_text(item)
                continue
            if item_type == "output_text":
                messages.append({"role": "assistant", "content": item["text"]})
                continue
            if allow_bridge_drops and item_type in {"input_text", "text"}:
                messages.append({"role": "user", "content": item["text"]})
                pending_reasoning = ""
                continue
            if allow_bridge_drops and item_type == "function_call":
                call_id = item.get("call_id")
                name = item.get("name")
                if not isinstance(call_id, str) or not call_id.strip():
                    raise_openai_error("function_call items require call_id", param=f"input[{index}].call_id", code="invalid_request_error")
                if not isinstance(name, str) or not name.strip():
                    raise_openai_error("function_call items require name", param=f"input[{index}].name", code="invalid_request_error")
                arguments = item.get("arguments", "")
                if not isinstance(arguments, str):
                    raise_openai_error("function_call.arguments must be a string", param=f"input[{index}].arguments", code="invalid_request_error")
                if not arguments.strip():
                    arguments = "{}"
                tool_call = {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
                if messages and messages[-1].get("role") == "assistant":
                    messages[-1].setdefault("tool_calls", []).append(tool_call)
                    if pending_reasoning and not messages[-1].get("reasoning_content"):
                        messages[-1]["reasoning_content"] = pending_reasoning
                else:
                    message = {"role": "assistant", "content": None, "tool_calls": [tool_call]}
                    if pending_reasoning:
                        message["reasoning_content"] = pending_reasoning
                    messages.append(message)
                pending_reasoning = ""
                continue
            if allow_bridge_drops and item_type == "function_call_output":
                call_id = item.get("call_id")
                if not isinstance(call_id, str) or not call_id.strip():
                    raise_openai_error("function_call_output items require call_id", param=f"input[{index}].call_id", code="invalid_request_error")
                if "output" not in item:
                    raise_openai_error("function_call_output items require output", param=f"input[{index}].output", code="invalid_request_error")
                messages.append({"role": "tool", "tool_call_id": call_id, "content": _json_string(item["output"])})
                pending_reasoning = ""
                continue
            if allow_bridge_drops and item_type not in (None, "message"):
                _record_lossy_drop(
                    report["field_changes"],
                    report["warnings"],
                    field=f"input[{index}]",
                    reason=fallback_reason,
                    detail="unsupported input item was dropped in the Responses to Chat fallback",
                    type_name=str(item_type),
                )
                pending_reasoning = ""
                continue
            role = item.get("role", "user" if allow_bridge_drops else None)
            if role == "developer":
                role = "system"
            content = _simple_text_from_message_content(
                item.get("content"),
                index=index,
                drop_empty_text_parts=allow_bridge_drops,
                drop_unsupported_parts=allow_bridge_drops,
                field_changes=report["field_changes"],
                warnings=report["warnings"],
                fallback_reason=fallback_reason,
            )
            if allow_bridge_drops and not content.strip():
                pending_reasoning = ""
                continue
            messages.append({"role": role, "content": content})
            if role != "assistant":
                pending_reasoning = ""
    if allow_bridge_drops:
        messages = _normalize_chat_tool_messages(messages, field_changes=report["field_changes"], warnings=report["warnings"], fallback_reason=fallback_reason)
    if not messages:
        raise_openai_error("'input' did not contain any text messages that can be mapped to chat", param="input", code="unsupported_parameter")
    payload: dict[str, Any] = {"model": upstream_model, "messages": messages}
    service_tier = body.get("service_tier")
    if isinstance(service_tier, str) and service_tier.strip():
        payload["service_tier"] = service_tier
    if "max_output_tokens" in body:
        payload["max_tokens"] = body["max_output_tokens"]
    elif "max_tokens" in body:
        payload["max_tokens"] = body["max_tokens"]
    if body.get("stream") is True:
        payload["stream"] = True
        stream_options = dict(body.get("stream_options") or {}) if isinstance(body.get("stream_options"), dict) else {}
        stream_options["include_usage"] = True
        payload["stream_options"] = stream_options
    for field in ("temperature", "top_p", "top_k", "thinking", "user", "metadata", "prompt_cache_key", "prompt_cache_isolation_key", "perf_metrics_in_response"):
        if field in body:
            payload[field] = body[field]
    reasoning_effort = _extract_reasoning_effort(body)
    if reasoning_effort is not None:
        payload["reasoning_effort"] = reasoning_effort
        report["field_changes"].append({"field": "reasoning.effort", "action": "map", "to": "reasoning_effort"})
    if allow_bridge_drops and isinstance(body.get("tools"), list):
        tools = []
        for index, tool in enumerate(body["tools"]):
            mapped_tool = _chat_tool_from_responses_tool(tool, index=index, allow_drop=True)
            if mapped_tool is None:
                _record_lossy_drop(
                    report["field_changes"],
                    report["warnings"],
                    field=f"tools[{index}]",
                    reason=fallback_reason,
                    detail="non-function tool was dropped in the Responses to Chat fallback",
                    type_name=str(tool.get("type") if isinstance(tool, dict) else type(tool).__name__),
                )
                continue
            tools.append(mapped_tool)
        if tools:
            payload["tools"] = tools
    if allow_bridge_drops and body.get("tool_choice") is not None:
        tool_choice = _chat_tool_choice_from_responses(body["tool_choice"], allow_drop=True)
        if tool_choice is None:
            _record_lossy_drop(
                report["field_changes"],
                report["warnings"],
                field="tool_choice",
                reason=fallback_reason,
                detail="unsupported tool_choice was dropped in the Responses to Chat fallback",
            )
        else:
            payload["tool_choice"] = tool_choice
    if payload.get("service_tier") == "priority":
        report["field_changes"].append({"field": "service_tier", "action": "preserve"})
    if fallback_reason != "priority":
        report["field_changes"].append(
            {
                "field": "endpoint",
                "action": "fallback",
                "from": "responses",
                "to": "chat_completions",
                "reason": fallback_reason,
            }
        )
        report["warnings"].append("Responses request routed through Chat Completions for Fireworks reasoning stability")
    if allow_bridge_drops:
        known_fields = _fallback_public_fields(allow_stream=allow_stream, allow_bridge_drops=True)
        dropped_top_level_fields = (set(body) & _REASONING_STABILITY_ACCEPT_DROP_FIELDS) | (set(body) - known_fields)
        for field in sorted(dropped_top_level_fields):
            if field == "stream_options" and payload.get("stream") is True:
                continue
            if field == "reasoning" and payload.get("reasoning_effort") is not None:
                continue
            report["field_changes"].append({"field": field, "action": "dropped", "reason": fallback_reason})
            report["warnings"].append(f"{field} is not forwarded in the Responses to Chat fallback")
    sampling_changes, sampling_warnings = apply_model_sampling_defaults(payload, upstream_model)
    report["field_changes"].extend(sampling_changes)
    report["warnings"].extend(sampling_warnings)
    return payload, report


def synthesize_responses_from_chat(
    chat_response: Mapping[str, Any],
    *,
    model: str,
    upstream_model: str,
    perf_metrics_in_response: bool | None = None,
    service_tier: str | None = "priority",
) -> dict[str, Any]:
    choices = chat_response.get("choices") or []
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first, dict) else {}
    content = message.get("content") if isinstance(message, dict) else None
    reasoning_content = message.get("reasoning_content") if isinstance(message, dict) else None
    tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
    finish_reason = first.get("finish_reason") if isinstance(first, dict) else None
    output = []
    chat_id = chat_response.get("id") if isinstance(chat_response.get("id"), str) else "unknown"
    if isinstance(reasoning_content, str) and reasoning_content:
        output.append(
            {
                "type": "reasoning",
                "id": f"rs_fallback_{chat_id}",
                "status": "completed",
                "summary": [{"type": "summary_text", "text": reasoning_content}],
            }
        )
    if isinstance(content, str):
        output.append({"type": "message", "id": f"msg_fallback_{chat_id}", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": content}]})
    if isinstance(tool_calls, list):
        for index, tool_call in enumerate(tool_calls):
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
            name = function.get("name")
            if not isinstance(name, str) or not name:
                continue
            arguments = function.get("arguments")
            if not isinstance(arguments, str) or not arguments.strip():
                arguments = "{}"
            call_id = tool_call.get("id") if isinstance(tool_call.get("id"), str) and tool_call.get("id") else f"call_fallback_{index}"
            output.append(
                {
                    "type": "function_call",
                    "id": f"fc_fallback_{index}_{chat_id}",
                    "call_id": call_id,
                    "name": name,
                    "arguments": arguments,
                    "status": "completed",
                }
            )
    if not output:
        output.append({"type": "message", "id": f"msg_fallback_{chat_id}", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": ""}]})
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
    response: dict[str, Any] = {
        "id": f"resp_fallback_{chat_id}",
        "object": "response",
        "status": "incomplete" if finish_reason == "length" else "completed",
        "model": model,
        "output": output,
        "usage": response_usage,
        "store": False,
        "provider": {"name": "fireworks", "endpoint": "chat_completions", "upstream_model": upstream_model},
    }
    if service_tier is not None:
        response["service_tier"] = service_tier
    if finish_reason == "length":
        response["incomplete_details"] = {"reason": "max_output_tokens"}
    if perf_metrics_in_response is not None:
        response["perf_metrics_in_response"] = perf_metrics_in_response
    return response
