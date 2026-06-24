from __future__ import annotations

from typing import Any

from app.dataplane.fireworks.contracts import OPENAI_TO_FIREWORKS_RESPONSES_FIELDS
from app.dataplane.fireworks.paths import resolve_inference_path
from app.dataplane.fireworks.reasoning_capabilities import normalize_responses_reasoning_effort
from app.products.openai.contracts import OPENAI_NOT_RESPONSES
from app.products.openai.errors import raise_openai_error
from app.dataplane.fireworks.contracts import FIREWORKS_RESPONSES_SUPPORTED_FIELDS

from .common import RESPONSES_PUBLIC_FIELDS, _copy_allowed, _require_present, _validate_bool, _validate_float_range, _validate_int_range, _validate_object, _reject_unknown_or_unsupported
from .common import build_adapter_headers


_RESPONSES_TOOL_TYPES = {"function", "mcp", "sse", "python", "web_search"}
_RESPONSES_INPUT_PART_TYPES = {"text", "input_text", "output_text", "input_image", "image"}
_RESPONSES_TEXT_PART_TYPES = {"text", "input_text", "output_text"}
_RESPONSES_OUTPUT_ITEM_TYPES = {"tool_output", "function_call"}
_OPENAI_RESPONSES_ACCEPT_DROP_FIELDS = {"stream_options"}


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_empty_text_part(part: Any) -> bool:
    return (
        isinstance(part, dict)
        and part.get("type") in _RESPONSES_TEXT_PART_TYPES
        and not _is_nonempty_str(part.get("text"))
    )


def _normalize_responses_input_part(part: dict[str, Any]) -> dict[str, Any]:
    part_type = part.get("type")
    if part_type == "output_text":
        # text is optional on input (Fireworks MessageContent.text is nullable);
        # default to empty string so a missing field does not KeyError.
        return {"type": "input_text", "text": part.get("text", "")}
    if part_type in {"input_image", "image"}:
        # Fireworks Responses API accepts the OpenAI-compatible shape where
        # image_url is a string URL (https://... or data:image/...).  The
        # object form {"url": ..., "detail": ...} is rejected by Fireworks,
        # so we drop "detail" and forward the bare URL string.
        image_url = part.get("image_url")
        if isinstance(image_url, str):
            url = image_url
        elif isinstance(image_url, dict):
            url = image_url.get("url")
        else:
            # Also support the "image" field used by some Anthropic-style shapes.
            image = part.get("image")
            if isinstance(image, str):
                url = image
            elif isinstance(image, dict):
                url = image.get("url")
            else:
                url = None
        if not isinstance(url, str):
            return part
        return {"type": "input_image", "image_url": url}
    return part


def _normalize_responses_input_item(
    item: dict[str, Any], *, drop_empty_text_parts: bool = False
) -> tuple[dict[str, Any], int]:
    dropped_empty_text_parts = 0
    if item.get("type") == "output_text":
        # text is optional on input (Fireworks MessageContent.text is nullable);
        # default to empty string so a missing field does not KeyError.
        return (
            {"role": "assistant", "content": [{"type": "input_text", "text": item.get("text", "")}]},
            dropped_empty_text_parts,
        )
    if item.get("type") == "function_call_output":
        return item, dropped_empty_text_parts
    if item.get("type") == "function_call":
        return item, dropped_empty_text_parts
    if item.get("type") == "message":
        item = {key: value for key, value in item.items() if key != "type"}
    content = item.get("content")
    if isinstance(content, list):
        normalized_content = []
        changed = False
        for part in content:
            if drop_empty_text_parts and _is_empty_text_part(part):
                dropped_empty_text_parts += 1
                changed = True
                continue
            if isinstance(part, dict) and part.get("type") in {"input_image", "image", "output_text"}:
                normalized_content.append(_normalize_responses_input_part(part))
                changed = True
            else:
                normalized_content.append(part)
        if changed:
            item = dict(item)
            item["content"] = normalized_content
    return item, dropped_empty_text_parts


def _normalize_responses_input_items(
    input_items: list[Any], *, drop_empty_text_parts: bool = False
) -> tuple[list[Any], int, int]:
    normalized: list[Any] = []
    dropped_reasoning = 0
    dropped_empty_text_parts = 0
    for item in input_items:
        if isinstance(item, dict) and item.get("type") == "reasoning":
            dropped_reasoning += 1
            continue
        if isinstance(item, dict):
            normalized_item, item_dropped_empty_text_parts = _normalize_responses_input_item(
                item, drop_empty_text_parts=drop_empty_text_parts
            )
            normalized.append(normalized_item)
            dropped_empty_text_parts += item_dropped_empty_text_parts
        else:
            normalized.append(item)
    return normalized, dropped_reasoning, dropped_empty_text_parts


def _normalize_previous_response_tool_replay(input_items: Any, previous_response_id: Any) -> Any:
    if not isinstance(previous_response_id, str) or not isinstance(input_items, list):
        return input_items
    normalized: list[Any] = []
    index = 0
    while index < len(input_items):
        current = input_items[index]
        next_item = input_items[index + 1] if index + 1 < len(input_items) else None
        if (
            isinstance(current, dict)
            and isinstance(next_item, dict)
            and current.get("type") == "function_call"
            and next_item.get("type") == "function_call_output"
            and current.get("call_id") == next_item.get("call_id")
        ):
            normalized.append(next_item)
            index += 2
            continue
        normalized.append(current)
        index += 1
    return normalized


def _normalize_responses_tool_choice(tool_choice: Any) -> Any:
    if not isinstance(tool_choice, dict):
        return tool_choice
    if tool_choice.get("type") == "function" and isinstance(tool_choice.get("name"), str) and tool_choice["name"].strip() and "function" not in tool_choice:
        return {"type": "function", "function": {"name": tool_choice["name"].strip()}}
    return tool_choice


def is_sub2api_bridge_shape(body: dict[str, Any]) -> bool:
    include = body.get("include")
    if isinstance(include, list) and "reasoning.encrypted_content" in include:
        return True
    text = body.get("text")
    if isinstance(text, dict) and text.get("verbosity") == "medium" and body.get("parallel_tool_calls") is True:
        return True
    return False


def _responses_stream_needs_continuation_storage(body: dict[str, Any]) -> bool:
    if body.get("stream") is not True:
        return False
    if is_sub2api_bridge_shape(body):
        return True
    if isinstance(body.get("previous_response_id"), str):
        return True
    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        return True
    input_items = body.get("input")
    if isinstance(input_items, list):
        return any(isinstance(item, dict) and item.get("type") in {"function_call", "function_call_output"} for item in input_items)
    return False


def _normalize_reasoning_effort_for_upstream(
    payload: dict[str, Any],
    upstream_model: str,
) -> tuple[dict[str, Any] | None, str | None]:
    reasoning = payload.get("reasoning")
    if not isinstance(reasoning, dict):
        return None, None
    original = reasoning.get("effort")
    normalized, reason = normalize_responses_reasoning_effort(upstream_model, original)
    if normalized == original:
        return None, None
    updated = dict(reasoning)
    updated["effort"] = normalized
    payload["reasoning"] = updated
    return {
        "field": "reasoning.effort",
        "from": original,
        "to": normalized,
        "reason": reason or "reasoning_effort_normalized",
    }, reason


def _validate_responses_input_part(part: Any, *, item_index: int, part_index: int) -> None:
    if not isinstance(part, dict) or not part:
        raise_openai_error("input content parts must be objects", param=f"input[{item_index}].content[{part_index}]", code="invalid_request_error")
    part_type = part.get("type")
    if not _is_nonempty_str(part_type):
        raise_openai_error("input content parts must include 'type'", param=f"input[{item_index}].content[{part_index}].type", code="invalid_request_error")
    if part_type not in _RESPONSES_INPUT_PART_TYPES:
        raise_openai_error(f"unsupported input content part type '{part_type}'", param=f"input[{item_index}].content[{part_index}].type", code="unsupported_parameter")
    if part_type in {"text", "input_text", "output_text"}:
        if "text" in part and not isinstance(part["text"], str):
            raise_openai_error("text input part text must be a string", param=f"input[{item_index}].content[{part_index}].text", code="invalid_request_error")
        return
    # input_image: Fireworks MessageContent only requires "type"; image_url is
    # not a required field and the URL scheme is not constrained by the schema.
    # The sub2api reference drops parts whose image_url is empty rather than
    # rejecting the request. Forward the part as-is (the normaliser still
    # normalises the image_url shape when a URL is present) and let upstream
    # decide; only reject a present image_url that is not a string/dict.
    image_url = part.get("image_url")
    if image_url is not None and not isinstance(image_url, (str, dict)):
        raise_openai_error("input_image image_url must be a string or object", param=f"input[{item_index}].content[{part_index}].image_url", code="invalid_request_error")


def _validate_responses_input_message(
    message: Any, *, index: int
) -> None:
    if not isinstance(message, dict) or not message:
        raise_openai_error("input list items must be message objects", param=f"input[{index}]", code="invalid_request_error")
    item_type = message.get("type")
    if item_type is not None:
        if item_type == "message":
            pass
        elif item_type == "output_text":
            # Open input schema; forward as-is (the normaliser defaults a
            # missing text to "" and tolerates any present value type).
            return
        elif item_type == "function_call_output":
            # Fireworks treats CreateResponse.input as an open object array
            # (additionalProperties: true) with no required fields and no field
            # type constraints. OpenAI's spec also allows output to be a string
            # or an array of image/file objects, so do not type-check it here;
            # forward as-is and let upstream decide.
            return
        elif item_type == "function_call":
            # Same rationale: input-side function_call fields are not required
            # by Fireworks and the input schema is open. Forward as-is.
            return
        elif item_type == "reasoning":
            return
        elif item_type not in _RESPONSES_OUTPUT_ITEM_TYPES:
            raise_openai_error(f"unsupported input item type '{item_type}'", param=f"input[{index}].type", code="unsupported_parameter")
        else:
            # tool_output: open input schema, forward as-is.
            return
    # Fireworks CreateResponse.input is an open object array (additionalProperties:
    # true) with no required fields, and the sub2api reference defaults a missing
    # role to "user" and tolerates empty/missing/non-list content. Validate types
    # of present fields only, and forward as-is so upstream decides.
    role = message.get("role")
    if role is not None and not isinstance(role, str):
        raise_openai_error("message role must be a string", param=f"input[{index}].role", code="invalid_request_error")
    content = message.get("content")
    if content is None or isinstance(content, str):
        return
    if isinstance(content, list):
        for part_index, part in enumerate(content):
            _validate_responses_input_part(part, item_index=index, part_index=part_index)
        return
    # Non-string, non-list content: Fireworks' open input schema accepts
    # arbitrary object shapes, so forward as-is rather than rejecting.
    return



def _validate_responses_input(value: Any) -> None:
    if isinstance(value, str):
        if not value:
            raise_openai_error("'input' must not be empty", param="input", code="invalid_request_error")
        return
    if isinstance(value, list):
        if not value:
            raise_openai_error("'input' must not be empty", param="input", code="invalid_request_error")
        for index, item in enumerate(value):
            _validate_responses_input_message(item, index=index)
        return
    raise_openai_error("'input' must be a string or non-empty list of objects/messages", param="input", code="invalid_request_error")


def _validate_responses_tool(tool: Any, *, index: int) -> None:
    # Fireworks tools schema is items: {additionalProperties: true, type: object}
    # — fully open, upstream does not validate tool internal fields and accepts
    # arbitrary extra fields. OpenAI also keeps adding new tool fields (e.g.
    # web_search.external_web_access / return_token_budget / image_settings),
    # so a static allowlist would go stale and reject valid client requests.
    # We therefore do NOT reject unknown tool fields. We only guard the tool
    # shape (non-empty object with a known "type"), require function.name, and
    # type-check the fields we recognise — forwarding everything else as-is.
    if not isinstance(tool, dict) or not tool:
        raise_openai_error("tool object must include 'type'", param=f"tools[{index}].type", code="invalid_request_error")
    tool_type = tool.get("type")
    if not isinstance(tool_type, str) or not tool_type:
        raise_openai_error("tool object must include 'type'", param=f"tools[{index}].type", code="invalid_request_error")
    if tool_type not in _RESPONSES_TOOL_TYPES:
        raise_openai_error(f"unsupported tool type '{tool_type}'", param=f"tools[{index}].type", code="unsupported_parameter")
    if tool_type == "function":
        function = tool.get("function")
        if isinstance(function, dict):
            if "name" not in function:
                raise_openai_error("function tools require function.name", param=f"tools[{index}].function.name", code="invalid_request_error")
            if function.get("name") is not None and not isinstance(function.get("name"), str):
                raise_openai_error("function.name must be a string", param=f"tools[{index}].function.name", code="invalid_request_error")
            if "parameters" in function and function["parameters"] is not None and not isinstance(function["parameters"], dict):
                raise_openai_error("function.parameters must be an object", param=f"tools[{index}].function.parameters", code="invalid_request_error")
            if "description" in function and function["description"] is not None and not isinstance(function["description"], str):
                raise_openai_error("function.description must be a string", param=f"tools[{index}].function.description", code="invalid_request_error")
            if "strict" in function and function["strict"] is not None and not isinstance(function["strict"], bool):
                raise_openai_error("function.strict must be a boolean", param=f"tools[{index}].function.strict", code="invalid_request_error")
        else:
            if "name" not in tool:
                raise_openai_error("function tools require name", param=f"tools[{index}].name", code="invalid_request_error")
            if tool.get("name") is not None and not isinstance(tool.get("name"), str):
                raise_openai_error("function.name must be a string", param=f"tools[{index}].name", code="invalid_request_error")
            if "parameters" in tool and tool["parameters"] is not None and not isinstance(tool["parameters"], dict):
                raise_openai_error("function.parameters must be an object", param=f"tools[{index}].parameters", code="invalid_request_error")
            if "description" in tool and tool["description"] is not None and not isinstance(tool["description"], str):
                raise_openai_error("function.description must be a string", param=f"tools[{index}].description", code="invalid_request_error")
            if "strict" in tool and tool["strict"] is not None and not isinstance(tool["strict"], bool):
                raise_openai_error("function.strict must be a boolean", param=f"tools[{index}].strict", code="invalid_request_error")
    elif tool_type == "mcp":
        # Type-check known string fields only; unknown fields are forwarded.
        for key in ("server_url", "url", "label", "name", "server_label", "server_description"):
            if key in tool and tool[key] is not None and not isinstance(tool[key], str):
                raise_openai_error(f"mcp tools {key} must be a string when provided", param=f"tools[{index}].{key}", code="invalid_request_error")
        if "allowed_tools" in tool and tool["allowed_tools"] is not None:
            if not isinstance(tool["allowed_tools"], list):
                raise_openai_error("mcp.allowed_tools must be a list", param=f"tools[{index}].allowed_tools", code="invalid_request_error")
            for allowed_index, allowed_tool in enumerate(tool["allowed_tools"]):
                if allowed_tool is not None and not isinstance(allowed_tool, str):
                    raise_openai_error("mcp.allowed_tools entries must be strings", param=f"tools[{index}].allowed_tools[{allowed_index}]", code="invalid_request_error")
        if "headers" in tool and tool["headers"] is not None:
            headers = tool["headers"]
            if not isinstance(headers, dict):
                raise_openai_error("mcp.headers must be an object", param=f"tools[{index}].headers", code="invalid_request_error")
            for header_name, header_value in headers.items():
                if not isinstance(header_name, str):
                    raise_openai_error("mcp.headers keys must be strings", param=f"tools[{index}].headers", code="invalid_request_error")
                if header_value is not None and not isinstance(header_value, str):
                    raise_openai_error("mcp.headers values must be strings", param=f"tools[{index}].headers.{header_name}", code="invalid_request_error")
        if "require_approval" in tool and tool["require_approval"] is not None and not isinstance(tool["require_approval"], (bool, str, dict)):
            raise_openai_error("mcp.require_approval must be a boolean, string, or object", param=f"tools[{index}].require_approval", code="invalid_request_error")
    elif tool_type == "sse":
        for key in ("server_url", "url"):
            if key in tool and tool[key] is not None and not isinstance(tool[key], str):
                raise_openai_error(f"sse tools {key} must be a string when provided", param=f"tools[{index}].{key}", code="invalid_request_error")
    elif tool_type == "python":
        if "name" in tool and tool["name"] is not None and not isinstance(tool["name"], str):
            raise_openai_error("python tools name must be a string when provided", param=f"tools[{index}].name", code="invalid_request_error")
    elif tool_type == "web_search":
        # OpenAI keeps extending web_search (external_web_access,
        # return_token_budget, search_content_types, image_settings, ...).
        # Fireworks' open tools schema does not constrain these, so forward any
        # field as-is. Only type-check search_context_size when it is present.
        if "search_context_size" in tool and tool["search_context_size"] is not None and not isinstance(tool["search_context_size"], str):
            raise_openai_error("web_search.search_context_size must be a string", param=f"tools[{index}].search_context_size", code="invalid_request_error")



def validate_responses_body(body: dict[str, Any]) -> None:
    _require_present(body, "model")
    _validate_responses_input(_require_present(body, "input"))
    if "previous_response_id" in body and not isinstance(body["previous_response_id"], str):
        raise_openai_error("'previous_response_id' must be a string", param="previous_response_id", code="invalid_request_error")
    if "previous_response_id" in body and not body["previous_response_id"].strip():
        raise_openai_error("'previous_response_id' must not be empty", param="previous_response_id", code="invalid_request_error")
    if "user" in body and not isinstance(body["user"], str):
        raise_openai_error("'user' must be a string", param="user", code="invalid_request_error")
    _validate_int_range(body, "max_output_tokens", positive=True)
    _validate_int_range(body, "max_tokens", positive=True)
    _validate_int_range(body, "max_tool_calls", positive=True)
    _validate_float_range(body, "temperature", min_value=0, max_value=2)
    _validate_float_range(body, "top_p", min_value=0, max_value=1)
    _validate_bool(body, "stream")
    _validate_bool(body, "parallel_tool_calls")
    _validate_bool(body, "store")
    if "tools" in body:
        if not isinstance(body["tools"], list):
            raise_openai_error("'tools' must be a list", param="tools", code="invalid_request_error")
        for index, tool in enumerate(body["tools"]):
            _validate_responses_tool(tool, index=index)
    if "tool_choice" in body:
        tool_choice = body["tool_choice"]
        if isinstance(tool_choice, str):
            if not tool_choice.strip():
                raise_openai_error("'tool_choice' must not be empty", param="tool_choice", code="invalid_request_error")
        elif isinstance(tool_choice, dict):
            if "type" in tool_choice and not isinstance(tool_choice["type"], str):
                raise_openai_error("'tool_choice.type' must be a string", param="tool_choice.type", code="invalid_request_error")
        else:
            raise_openai_error("'tool_choice' must be a string or object", param="tool_choice", code="invalid_request_error")
    _validate_object(body, "text")
    _validate_object(body, "reasoning")
    _validate_object(body, "metadata")
    _validate_object(body, "stream_options")
    if "max_tokens" in body:
        if "max_output_tokens" in body:
            raise_openai_error("'max_output_tokens' and 'max_tokens' are mutually exclusive", param="max_tokens", code="unsupported_parameter")
    if "service_tier" in body:
        raise_openai_error("service_tier is not supported for responses", param="service_tier", code="unsupported_parameter")
    unknown = sorted(set(body) - (RESPONSES_PUBLIC_FIELDS | {"model"}))
    for field in unknown:
        _reject_unknown_or_unsupported(field, public_fields=RESPONSES_PUBLIC_FIELDS, unsupported_fields=OPENAI_NOT_RESPONSES)
    for field in sorted((set(body) & RESPONSES_PUBLIC_FIELDS) - FIREWORKS_RESPONSES_SUPPORTED_FIELDS - set(OPENAI_TO_FIREWORKS_RESPONSES_FIELDS) - _OPENAI_RESPONSES_ACCEPT_DROP_FIELDS):
        if field in {"model", "max_tokens"}:
            continue
        _reject_unknown_or_unsupported(field, public_fields=RESPONSES_PUBLIC_FIELDS, unsupported_fields=OPENAI_NOT_RESPONSES)


def build_responses_adapter(context) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    body = context.body
    validate_responses_body(body)
    payload = _copy_allowed(body, FIREWORKS_RESPONSES_SUPPORTED_FIELDS)
    field_changes: list[dict[str, Any]] = []
    warnings: list[str] = []
    if isinstance(payload.get("input"), list):
        payload["input"], dropped_reasoning, dropped_empty_text_parts = (
            _normalize_responses_input_items(payload["input"], drop_empty_text_parts=True)
        )
        if dropped_reasoning:
            field_changes.append({"field": "input", "action": "dropped", "type": "reasoning", "count": dropped_reasoning})
            warnings.append("reasoning input items were dropped before forwarding to Fireworks Responses")
        if dropped_empty_text_parts:
            field_changes.append(
                {
                    "field": "input.content",
                    "action": "dropped",
                    "type": "empty_text",
                    "count": dropped_empty_text_parts,
                }
            )
            warnings.append(
                "empty text content parts were dropped for sub2api Responses compatibility"
            )
        normalized_replay = _normalize_previous_response_tool_replay(payload["input"], body.get("previous_response_id"))
        if normalized_replay != payload["input"]:
            payload["input"] = normalized_replay
    tools = body.get("tools")
    if isinstance(tools, list):
        for index, tool in enumerate(tools):
            if isinstance(tool, dict) and tool.get("type") == "function" and isinstance(tool.get("function"), dict):
                warnings.append("nested function tool shape is accepted for compatibility; flat function tools are preferred")
                break
            if isinstance(tool, dict) and tool.get("type") == "mcp":
                allowed_mcp_keys = {"type", "server_url", "url", "label", "name", "server_label", "server_description", "allowed_tools", "headers", "require_approval"}
                extras = sorted(set(tool) - allowed_mcp_keys)
                if extras:
                    raise_openai_error(f"unsupported mcp tool field '{extras[0]}'", param=f"tools[{index}].{extras[0]}", code="unsupported_parameter")
                break
    if isinstance(body.get("previous_response_id"), str) and isinstance(body.get("input"), list):
        if _normalize_previous_response_tool_replay(body["input"], body["previous_response_id"]) != body["input"]:
            field_changes.append({"field": "input", "action": "normalized", "reason": "previous_response_tool_replay"})
            warnings.append("replayed function_call was dropped from previous_response_id tool continuation")
    if isinstance(body.get("tool_choice"), str):
        payload["tool_choice"] = body["tool_choice"]
    elif isinstance(body.get("tool_choice"), dict):
        normalized_tool_choice = _normalize_responses_tool_choice(body["tool_choice"])
        if normalized_tool_choice != body["tool_choice"]:
            payload["tool_choice"] = normalized_tool_choice
            field_changes.append({"field": "tool_choice", "action": "normalized", "to": "function.name"})
            warnings.append("tool_choice function shorthand was normalized for Fireworks Responses compatibility")
    if _responses_stream_needs_continuation_storage(body):
        if body.get("store") is not True:
            payload["store"] = True
            field_changes.append({"field": "store", "from": body.get("store"), "to": True, "reason": "sub2api_previous_response_compat"})
            warnings.append("store was forced to true so streamed tool continuations can use previous_response_id")
    for field in sorted(set(body) & _OPENAI_RESPONSES_ACCEPT_DROP_FIELDS):
        field_changes.append({"field": field, "action": "dropped"})
        warnings.append(f"{field} is accepted for OpenAI compatibility but not forwarded to Fireworks Responses")
    if "max_tokens" in body:
        target = OPENAI_TO_FIREWORKS_RESPONSES_FIELDS["max_tokens"]
        payload[target] = body["max_tokens"]
        field_changes.append({"field": "max_tokens", "to": target})
        warnings.append("max_tokens is not a standard Responses field; mapped to max_output_tokens")
    upstream_model = context.resolved_model.upstream_model
    payload["model"] = upstream_model
    reasoning_change, reasoning_reason = _normalize_reasoning_effort_for_upstream(payload, upstream_model)
    if reasoning_change is not None:
        field_changes.append(reasoning_change)
        if reasoning_reason == "model_accepts_highest_effort_as_high":
            warnings.append("reasoning.effort was reduced to high for this Fireworks model family")
        else:
            warnings.append("reasoning.effort was normalized for Fireworks Responses compatibility")
    headers = build_adapter_headers(context)
    return payload, headers, {"field_changes": field_changes, "warnings": warnings}


def resolve_responses_upstream_path(upstream_base_url: str, endpoint: str = "responses") -> str:
    return resolve_inference_path(upstream_base_url, endpoint)
