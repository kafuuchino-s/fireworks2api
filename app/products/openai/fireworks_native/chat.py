from __future__ import annotations

from typing import Any

from app.dataplane.fireworks.contracts import FIREWORKS_CHAT_SUPPORTED_FIELDS, OPENAI_TO_FIREWORKS_CHAT_FIELDS
from app.dataplane.fireworks.reasoning_capabilities import classify_reasoning_model
from app.dataplane.fireworks.sampling_defaults import apply_model_sampling_defaults
from app.products.openai.contracts import OPENAI_NOT_CHAT
from .common import CHAT_NESTED_FIELDS, CHAT_PUBLIC_FIELDS, _copy_allowed, _reject_unknown_or_unsupported, _validate_bool, _validate_float_range, _validate_int_range, _validate_list, _validate_object, _validate_object_or_string
from app.products.openai.errors import raise_openai_error
from .common import build_adapter_headers


_OPENAI_CHAT_ACCEPT_DROP_FIELDS = {"store", "modalities"}


def validate_chat_body(body: dict[str, Any]) -> None:
    if not isinstance(body.get("messages"), list):
        raise_openai_error("'messages' is required", param="messages", code="missing_required_parameter")
    _validate_int_range(body, "n", min_value=1, max_value=128)
    _validate_float_range(body, "temperature", min_value=0, max_value=2)
    _validate_float_range(body, "top_p", min_value=0, max_value=1)
    if body.get("top_k") is not None:
        _validate_int_range(body, "top_k", min_value=0, max_value=100)
    _validate_int_range(body, "max_tokens", positive=True)
    _validate_int_range(body, "max_completion_tokens", positive=True)
    _validate_bool(body, "stream")
    _validate_tools(body.get("tools"))
    _validate_legacy_functions(body.get("functions"))
    _validate_legacy_function_call(body.get("function_call"))
    _validate_tool_choice(body.get("tool_choice"))
    _validate_modalities(body.get("modalities"))
    _validate_object(body, "response_format")
    _validate_object(body, "stream_options")
    _validate_object(body, "thinking")
    _validate_object(body, "metadata")
    _validate_object(body, "reasoning")
    _validate_object(body, "text")
    if "user" in body and not isinstance(body.get("user"), str):
        raise_openai_error("'user' must be a string", param="user", code="invalid_request_error")
    if body.get("stream_options") is not None:
        _validate_stream_options(body.get("stream_options"))
    _validate_messages(body.get("messages"))
    if "max_completion_tokens" in body and "max_tokens" in body:
        raise_openai_error("'max_tokens' and 'max_completion_tokens' are mutually exclusive", param="max_completion_tokens", code="unsupported_parameter")
    if body.get("thinking") is not None and body.get("reasoning_effort") is not None:
        raise_openai_error("'thinking' and 'reasoning_effort' are mutually exclusive", param="reasoning_effort", code="unsupported_parameter")
    _validate_thinking(body.get("thinking"))
    service_tier = body.get("service_tier")
    if isinstance(service_tier, str):
        tier = service_tier.strip().lower()
        if tier not in {"priority", "auto", "default", "flex", "scale"}:
            raise_openai_error("unsupported service_tier", param="service_tier", code="unsupported_parameter")
    elif service_tier is not None:
        raise_openai_error("unsupported service_tier", param="service_tier", code="unsupported_parameter")
    unknown = sorted(set(body) - (CHAT_PUBLIC_FIELDS | CHAT_NESTED_FIELDS | {"model"}))
    for field in unknown:
        _reject_unknown_or_unsupported(field, public_fields=CHAT_PUBLIC_FIELDS, unsupported_fields=OPENAI_NOT_CHAT)
    for field in sorted((set(body) & CHAT_PUBLIC_FIELDS) - FIREWORKS_CHAT_SUPPORTED_FIELDS - set(OPENAI_TO_FIREWORKS_CHAT_FIELDS) - _OPENAI_CHAT_ACCEPT_DROP_FIELDS):
        if field in {"model", "max_completion_tokens", "max_tokens", "service_tier", "functions", "function_call"} | CHAT_NESTED_FIELDS:
            continue
        _reject_unknown_or_unsupported(field, public_fields=CHAT_PUBLIC_FIELDS, unsupported_fields=OPENAI_NOT_CHAT)


def _validate_messages(messages: Any) -> None:
    if not isinstance(messages, list):
        raise_openai_error("'messages' is required", param="messages", code="missing_required_parameter")
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise_openai_error("each message must be an object", param=f"messages[{index}]", code="invalid_request_error")
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            raise_openai_error("unsupported message role", param=f"messages[{index}].role", code="unsupported_parameter")
        if role == "assistant" and "tool_calls" in message:
            _validate_tool_calls(message["tool_calls"], index=index)
        if role == "tool":
            if not isinstance(message.get("tool_call_id"), str) or not message["tool_call_id"].strip():
                raise_openai_error("tool messages require tool_call_id", param=f"messages[{index}].tool_call_id", code="invalid_request_error")
        content = message.get("content")
        if isinstance(content, str) or content is None:
            pass
        elif isinstance(content, list):
            for part_index, part in enumerate(content):
                _validate_message_part(part, index=index, part_index=part_index)
        else:
            raise_openai_error("message content must be a string or list of content parts", param=f"messages[{index}].content", code="invalid_request_error")


def _validate_tool_calls(tool_calls: Any, *, index: int) -> None:
    if not isinstance(tool_calls, list) or not tool_calls:
        raise_openai_error("assistant tool_calls must be a non-empty list", param=f"messages[{index}].tool_calls", code="invalid_request_error")
    for tool_index, tool_call in enumerate(tool_calls):
        if not isinstance(tool_call, dict):
            raise_openai_error("assistant tool_calls entries must be objects", param=f"messages[{index}].tool_calls[{tool_index}]", code="invalid_request_error")
        if tool_call.get("type") != "function":
            raise_openai_error("assistant tool_calls only support function type", param=f"messages[{index}].tool_calls[{tool_index}].type", code="unsupported_parameter")
        function = tool_call.get("function")
        if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not function["name"].strip():
            raise_openai_error("assistant tool_calls require function.name", param=f"messages[{index}].tool_calls[{tool_index}].function.name", code="invalid_request_error")
        if not isinstance(tool_call.get("id"), str) or not tool_call["id"].strip():
            raise_openai_error("assistant tool_calls require id", param=f"messages[{index}].tool_calls[{tool_index}].id", code="invalid_request_error")


def _validate_message_part(part: Any, *, index: int, part_index: int) -> None:
    if not isinstance(part, dict):
        raise_openai_error("content part must be an object", param=f"messages[{index}].content[{part_index}]", code="invalid_request_error")
    part_type = part.get("type")
    if part_type == "text":
        if not isinstance(part.get("text"), str):
            raise_openai_error("text content part must include text", param=f"messages[{index}].content[{part_index}].text", code="invalid_request_error")
        return
    if part_type == "refusal":
        if not isinstance(part.get("refusal"), str):
            raise_openai_error("refusal content part must include refusal", param=f"messages[{index}].content[{part_index}].refusal", code="invalid_request_error")
        return
    if part_type == "image_url":
        image_url = part.get("image_url")
        url = image_url.get("url") if isinstance(image_url, dict) else None
        if not isinstance(image_url, dict) or not isinstance(url, str) or not url.strip() or not (url.startswith("https://") or (url.startswith("data:image/") and ";base64," in url)):
            raise_openai_error("image_url content part must include image_url.url", param=f"messages[{index}].content[{part_index}].image_url", code="invalid_request_error")
        if "detail" in image_url and not isinstance(image_url["detail"], str):
            raise_openai_error("image_url.detail must be a string", param=f"messages[{index}].content[{part_index}].image_url.detail", code="invalid_request_error")
        return
    raise_openai_error("unsupported content part type", param=f"messages[{index}].content[{part_index}].type", code="invalid_request_error")


def _validate_tools(tools: Any) -> None:
    if tools is None:
        return
    if not isinstance(tools, list):
        raise_openai_error("'tools' must be a list", param="tools", code="invalid_request_error")
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict) or tool.get("type") != "function":
            raise_openai_error("Chat tools only support type=function", param=f"tools[{index}].type", code="unsupported_parameter")
        function = tool.get("function")
        if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not function["name"].strip():
            raise_openai_error("function tools require function.name", param=f"tools[{index}].function.name", code="invalid_request_error")
        if "parameters" in function and not isinstance(function["parameters"], dict):
            raise_openai_error("function.parameters must be an object", param=f"tools[{index}].function.parameters", code="invalid_request_error")
        if "strict" in function and not isinstance(function["strict"], bool):
            raise_openai_error("function.strict must be a boolean", param=f"tools[{index}].function.strict", code="invalid_request_error")
        if any(k in tool for k in {"mcp", "sse", "python"}):
            raise_openai_error("unsupported tool configuration", param=f"tools[{index}]", code="unsupported_parameter")


def _validate_legacy_functions(functions: Any) -> None:
    if functions is None:
        return
    if not isinstance(functions, list):
        raise_openai_error("'functions' must be a list", param="functions", code="invalid_request_error")
    for index, function in enumerate(functions):
        if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not function["name"].strip():
            raise_openai_error("functions require name", param=f"functions[{index}].name", code="invalid_request_error")
        if "parameters" in function and not isinstance(function["parameters"], dict):
            raise_openai_error("function.parameters must be an object", param=f"functions[{index}].parameters", code="invalid_request_error")
        if "description" in function and function["description"] is not None and not isinstance(function["description"], str):
            raise_openai_error("function.description must be a string", param=f"functions[{index}].description", code="invalid_request_error")


def _validate_legacy_function_call(function_call: Any) -> None:
    if function_call is None:
        return
    if isinstance(function_call, str):
        if function_call not in {"auto", "none"}:
            raise_openai_error("unsupported function_call", param="function_call", code="unsupported_parameter")
        return
    if isinstance(function_call, dict):
        if not isinstance(function_call.get("name"), str) or not function_call["name"].strip():
            raise_openai_error("function_call.name must be provided", param="function_call.name", code="invalid_request_error")
        return
    raise_openai_error("'function_call' must be a string or object", param="function_call", code="invalid_request_error")


def _validate_modalities(modalities: Any) -> None:
    if modalities is None:
        return
    if not isinstance(modalities, list) or not modalities or not all(isinstance(item, str) for item in modalities):
        raise_openai_error("'modalities' must be a non-empty list of strings", param="modalities", code="invalid_request_error")
    unsupported = sorted(set(modalities) - {"text"})
    if unsupported:
        raise_openai_error("audio modalities are not supported by Fireworks chat", param="modalities", code="unsupported_parameter")


def _validate_tool_choice(tool_choice: Any) -> None:
    if tool_choice is None:
        return
    if isinstance(tool_choice, str):
        if tool_choice == "any":
            raise_openai_error("unsupported tool_choice", param="tool_choice", code="unsupported_parameter")
        if tool_choice not in {"auto", "none", "required"}:
            raise_openai_error("unsupported tool_choice", param="tool_choice", code="unsupported_parameter")
        return
    if isinstance(tool_choice, dict):
        if tool_choice.get("type") != "function":
            raise_openai_error("tool_choice object must select a function", param="tool_choice.type", code="invalid_request_error")
        function = tool_choice.get("function")
        if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not function["name"].strip():
            raise_openai_error("tool_choice.function.name must be provided", param="tool_choice.function.name", code="invalid_request_error")
        return
    raise_openai_error("'tool_choice' must be a string or object", param="tool_choice", code="invalid_request_error")


def _validate_stream_options(stream_options: Any) -> None:
    if not isinstance(stream_options, dict):
        raise_openai_error("'stream_options' must be an object", param="stream_options", code="invalid_request_error")
    if "include_usage" in stream_options and not isinstance(stream_options["include_usage"], bool):
        raise_openai_error("'stream_options.include_usage' must be a boolean", param="stream_options.include_usage", code="invalid_request_error")


def _validate_thinking(thinking: Any) -> None:
    if thinking is None:
        return
    if not isinstance(thinking, dict):
        raise_openai_error("'thinking' must be an object", param="thinking", code="invalid_request_error")
    if "type" in thinking and not isinstance(thinking["type"], str):
        raise_openai_error("'thinking.type' must be a string", param="thinking.type", code="invalid_request_error")
    if "budget_tokens" in thinking:
        _validate_int_range(thinking, "budget_tokens", min_value=1024)


def build_chat_adapter(context) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    body = context.body
    validate_chat_body(body)
    payload: dict[str, Any] = _copy_allowed(body, FIREWORKS_CHAT_SUPPORTED_FIELDS)
    payload.pop("service_tier", None)
    field_changes: list[dict[str, Any]] = []
    warnings: list[str] = []
    capabilities = classify_reasoning_model(getattr(getattr(context, "resolved_model", None), "upstream_model", ""))
    if body.get("thinking") is not None and capabilities.supports_thinking is False:
        warnings.append("thinking is likely unsupported for this upstream model family")
    if body.get("reasoning_effort") is not None and capabilities.supports_reasoning_effort is False:
        warnings.append("reasoning_effort is likely unsupported for this upstream model family")
    if "max_completion_tokens" in body:
        target = OPENAI_TO_FIREWORKS_CHAT_FIELDS["max_completion_tokens"]
        payload[target] = body["max_completion_tokens"]
        field_changes.append({"field": "max_completion_tokens", "to": target})
    elif "max_tokens" in body:
        warnings.append("max_tokens is deprecated for OpenAI Chat; forwarded to Fireworks max_tokens")
    service_tier = body.get("service_tier")
    if isinstance(service_tier, str):
        tier = service_tier.strip().lower()
        if tier == "priority":
            payload["service_tier"] = "priority"
        elif tier in {"auto", "default", "flex", "scale"}:
            warnings.append("service_tier omitted for chat")
    if "functions" in body and "tools" not in payload:
        payload["tools"] = [{"type": "function", "function": function} for function in body["functions"]]
        field_changes.append({"field": "functions", "to": "tools"})
        warnings.append("legacy functions mapped to tools")
    if "function_call" in body and "tool_choice" not in payload:
        function_call = body["function_call"]
        if isinstance(function_call, str):
            payload["tool_choice"] = function_call
        else:
            payload["tool_choice"] = {"type": "function", "function": {"name": function_call["name"]}}
        field_changes.append({"field": "function_call", "to": "tool_choice"})
        warnings.append("legacy function_call mapped to tool_choice")
    for field in sorted(set(body) & _OPENAI_CHAT_ACCEPT_DROP_FIELDS):
        field_changes.append({"field": field, "action": "dropped"})
        warnings.append(f"{field} accepted for OpenAI compatibility but not forwarded to Fireworks chat")
    upstream_model = context.resolved_model.upstream_model
    payload["model"] = upstream_model
    sampling_changes, sampling_warnings = apply_model_sampling_defaults(payload, upstream_model)
    field_changes.extend(sampling_changes)
    warnings.extend(sampling_warnings)
    headers = build_adapter_headers(context)
    return payload, headers, {"field_changes": field_changes, "warnings": warnings}
