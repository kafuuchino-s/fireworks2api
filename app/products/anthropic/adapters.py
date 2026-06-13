from __future__ import annotations

from typing import Any

from app.dataplane.fireworks.headers import build_upstream_headers
from app.dataplane.fireworks.contracts import FIREWORKS_ANTHROPIC_MESSAGES_SUPPORTED_FIELDS
from app.dataplane.fireworks.paths import resolve_inference_path
from app.dataplane.fireworks.sampling_defaults import apply_model_sampling_defaults

from .contracts import ANTHROPIC_MESSAGES_PUBLIC_FIELDS
from .errors import anthropic_error


def _is_text_block(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)


def _validate_image_block(block: Any) -> None:
    if not isinstance(block, dict) or block.get("type") != "image":
        raise anthropic_error("invalid image content block", param="messages", code="invalid_request_error")
    for key in block:
        if key not in {"type", "source", "cache_control"}:
            raise anthropic_error("invalid image content block", param="messages", code="invalid_request_error")
    if "cache_control" in block and not isinstance(block.get("cache_control"), dict):
        raise anthropic_error("invalid image content block", param="messages", code="invalid_request_error")
    source = block.get("source")
    if not isinstance(source, dict):
        raise anthropic_error("invalid image content block", param="messages", code="invalid_request_error")
    source_type = source.get("type")
    if source_type == "base64":
        if set(source) != {"type", "media_type", "data"}:
            raise anthropic_error("invalid image content block", param="messages", code="invalid_request_error")
        if source.get("media_type") not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
            raise anthropic_error("invalid image content block", param="messages", code="invalid_request_error")
        if not isinstance(source.get("data"), str) or not source.get("data"):
            raise anthropic_error("invalid image content block", param="messages", code="invalid_request_error")
        return
    if source_type == "url":
        if set(source) != {"type", "url"}:
            raise anthropic_error("invalid image content block", param="messages", code="invalid_request_error")
        url = source.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise anthropic_error("invalid image content block", param="messages", code="invalid_request_error")
        return
    if source_type == "file":
        if set(source) != {"type", "file_id"}:
            raise anthropic_error("invalid image content block", param="messages", code="invalid_request_error")
        if not isinstance(source.get("file_id"), str) or not source.get("file_id"):
            raise anthropic_error("invalid image content block", param="messages", code="invalid_request_error")
        return
    raise anthropic_error("invalid image content block", param="messages", code="invalid_request_error")


def _validate_tool_result_block(block: Any) -> None:
    if not isinstance(block, dict) or block.get("type") != "tool_result":
        raise anthropic_error("invalid message content block", param="messages", code="invalid_request_error")
    if "tool_use_id" not in block or not isinstance(block.get("tool_use_id"), str) or not block.get("tool_use_id"):
        raise anthropic_error("invalid message content block", param="messages", code="invalid_request_error")
    if "content" in block and not (isinstance(block.get("content"), str) or isinstance(block.get("content"), list)):
        raise anthropic_error("invalid message content block", param="messages", code="invalid_request_error")
    if "is_error" in block and not isinstance(block.get("is_error"), bool):
        raise anthropic_error("invalid message content block", param="messages", code="invalid_request_error")


def _validate_tool_use_block(block: Any) -> None:
    if not isinstance(block, dict) or block.get("type") != "tool_use":
        raise anthropic_error("invalid message content block", param="messages", code="invalid_request_error")
    if not isinstance(block.get("id"), str) or not block.get("id"):
        raise anthropic_error("invalid message content block", param="messages", code="invalid_request_error")
    if not isinstance(block.get("name"), str) or not block.get("name"):
        raise anthropic_error("invalid message content block", param="messages", code="invalid_request_error")
    if not isinstance(block.get("input"), dict):
        raise anthropic_error("invalid message content block", param="messages", code="invalid_request_error")


def _message_contains_tool_block(content: Any, block_type: str) -> bool:
    return isinstance(content, list) and any(isinstance(block, dict) and block.get("type") == block_type for block in content)


def _validate_message_content(content: Any) -> None:
    if isinstance(content, str):
        return
    if not isinstance(content, list):
        raise anthropic_error("message content must be a string or list of blocks", param="messages", code="invalid_request_error")
    for block in content:
        if _is_text_block(block):
            continue
        if isinstance(block, dict) and block.get("type") == "image":
            _validate_image_block(block)
            continue
        if isinstance(block, dict) and block.get("type") == "tool_result":
            _validate_tool_result_block(block)
            continue
        if isinstance(block, dict) and block.get("type") == "tool_use":
            _validate_tool_use_block(block)
            continue
        raise anthropic_error("invalid message content block", param="messages", code="invalid_request_error")


def _validate_message(message: dict[str, Any]) -> None:
    role = message.get("role")
    content = message.get("content")
    _validate_message_content(content)
    if role == "user":
        if _message_contains_tool_block(content, "tool_use"):
            raise anthropic_error("user messages cannot contain tool_use blocks", param="messages", code="invalid_request_error")
    elif role == "assistant":
        if _message_contains_tool_block(content, "tool_result"):
            raise anthropic_error("assistant messages cannot contain tool_result blocks", param="messages", code="invalid_request_error")
        if _message_contains_tool_block(content, "tool_use"):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    _validate_tool_use_block(block)
    else:
        raise anthropic_error("message role must be user or assistant", param="messages", code="invalid_request_error")


def _validate_system(system: Any) -> None:
    if isinstance(system, str):
        return
    if not isinstance(system, list) or any(not _is_text_block(block) for block in system):
        raise anthropic_error("'system' must be a string or list of text blocks", param="system", code="invalid_request_error")


def validate_messages_body(body: dict[str, Any]) -> None:
    for field in ("model", "messages"):
        if field not in body:
            raise anthropic_error(f"'{field}' is required", param=field, code="missing_required_parameter")
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise anthropic_error("'model' must be a non-empty string", param="model", code="invalid_request_error")
    messages = body.get("messages")
    if not isinstance(messages, list):
        raise anthropic_error("'messages' must be a list", param="messages", code="invalid_request_error")
    for message in messages:
        if not isinstance(message, dict):
            raise anthropic_error("each message must be an object", param="messages", code="invalid_request_error")
        if message.get("role") not in {"user", "assistant"}:
            raise anthropic_error("message role must be user or assistant", param="messages", code="invalid_request_error")
        if "content" not in message:
            raise anthropic_error("message content is required", param="messages", code="invalid_request_error")
        _validate_message(message)
    if "max_tokens" in body:
        max_tokens = body.get("max_tokens")
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
            raise anthropic_error("'max_tokens' must be a positive integer", param="max_tokens", code="invalid_request_error")
    if "system" in body:
        _validate_system(body.get("system"))
    unknown = sorted(set(body) - ANTHROPIC_MESSAGES_PUBLIC_FIELDS)
    if unknown:
        field = unknown[0]
        raise anthropic_error(f"unknown parameter '{field}'", param=field, code="unknown_parameter")
    if "stream" in body and not isinstance(body.get("stream"), bool):
        raise anthropic_error("'stream' must be a boolean", param="stream", code="invalid_request_error")
    if "temperature" in body:
        temperature = body.get("temperature")
        if not isinstance(temperature, (int, float)) or isinstance(temperature, bool) or not 0 <= float(temperature) <= 1:
            raise anthropic_error("'temperature' must be between 0 and 1", param="temperature", code="invalid_request_error")
    if "top_p" in body:
        top_p = body.get("top_p")
        if not isinstance(top_p, (int, float)) or isinstance(top_p, bool) or not 0 <= float(top_p) <= 1:
            raise anthropic_error("'top_p' must be between 0 and 1", param="top_p", code="invalid_request_error")
    if "top_k" in body and body.get("top_k") is not None:
        top_k = body.get("top_k")
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 0:
            raise anthropic_error("'top_k' must be a non-negative integer", param="top_k", code="invalid_request_error")
    if "stop_sequences" in body:
        stop_sequences = body.get("stop_sequences")
        if not isinstance(stop_sequences, list) or any(not isinstance(item, str) for item in stop_sequences):
            raise anthropic_error("'stop_sequences' must be a list of strings", param="stop_sequences", code="invalid_request_error")
    if "thinking" in body and not isinstance(body.get("thinking"), dict):
        raise anthropic_error("'thinking' must be an object", param="thinking", code="invalid_request_error")
    if "output_config" in body and not isinstance(body.get("output_config"), dict):
        raise anthropic_error("'output_config' must be an object", param="output_config", code="invalid_request_error")
    if "metadata" in body and not isinstance(body.get("metadata"), dict):
        raise anthropic_error("'metadata' must be an object", param="metadata", code="invalid_request_error")
    if "tools" in body and not isinstance(body.get("tools"), list):
        raise anthropic_error("'tools' must be a list", param="tools", code="invalid_request_error")
    if "tools" in body:
        for tool in body.get("tools"):
            if not isinstance(tool, dict):
                raise anthropic_error("each tool must be an object", param="tools", code="invalid_request_error")
            if not isinstance(tool.get("name"), str) or not tool.get("name"):
                raise anthropic_error("each tool must have a name", param="tools", code="invalid_request_error")
            if "description" in tool and not isinstance(tool.get("description"), str):
                raise anthropic_error("each tool description must be a string", param="tools", code="invalid_request_error")
            if "input_schema" in tool and not isinstance(tool.get("input_schema"), dict):
                raise anthropic_error("each tool input_schema must be an object", param="tools", code="invalid_request_error")
    if "tool_choice" in body and not isinstance(body.get("tool_choice"), (str, dict)):
        raise anthropic_error("'tool_choice' must be a string or object", param="tool_choice", code="invalid_request_error")
    if isinstance(body.get("tool_choice"), dict):
        choice = body.get("tool_choice")
        allowed_choice_keys = {"type", "name", "disable_parallel_tool_use"}
        if set(choice) - allowed_choice_keys:
            raise anthropic_error("'tool_choice' must be a string or object", param="tool_choice", code="invalid_request_error")
        if "disable_parallel_tool_use" in choice and not isinstance(choice.get("disable_parallel_tool_use"), bool):
            raise anthropic_error("'tool_choice.disable_parallel_tool_use' must be a boolean", param="tool_choice", code="invalid_request_error")
        if choice.get("type") == "tool" and isinstance(choice.get("name"), str) and choice.get("name"):
            pass
        elif choice.get("type") in {"auto", "any", "none"} and "name" not in choice:
            pass
        else:
            raise anthropic_error("'tool_choice' must be a string or object", param="tool_choice", code="invalid_request_error")
    if "raw_output" in body and not isinstance(body.get("raw_output"), bool):
        raise anthropic_error("'raw_output' must be a boolean", param="raw_output", code="invalid_request_error")
    service_tier = body.get("service_tier")
    if isinstance(service_tier, str):
        tier = service_tier.strip().lower()
        if tier not in {"priority", "auto", "default"}:
            raise anthropic_error("unsupported service_tier", param="service_tier", code="unsupported_parameter")
    elif service_tier is not None:
        raise anthropic_error("unsupported service_tier", param="service_tier", code="unsupported_parameter")
    if "thinking" in body:
        thinking = body.get("thinking")
        if thinking.get("type") not in {"enabled", "disabled"}:
            raise anthropic_error("'thinking' has invalid type", param="thinking", code="invalid_request_error")
        budget_tokens = thinking.get("budget_tokens")
        if budget_tokens is not None:
            if not isinstance(budget_tokens, int) or isinstance(budget_tokens, bool) or budget_tokens < 1024:
                raise anthropic_error("'thinking.budget_tokens' must be at least 1024", param="thinking", code="invalid_request_error")
            max_tokens = body.get("max_tokens")
            if isinstance(max_tokens, int) and not isinstance(max_tokens, bool) and budget_tokens >= max_tokens:
                raise anthropic_error("'thinking.budget_tokens' must be less than 'max_tokens'", param="thinking", code="invalid_request_error")


def build_messages_adapter(context) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    body = context.body
    validate_messages_body(body)
    payload = {k: v for k, v in body.items() if k in FIREWORKS_ANTHROPIC_MESSAGES_SUPPORTED_FIELDS}
    if isinstance(payload.get("service_tier"), str):
        tier = payload["service_tier"].strip().lower()
        if tier == "priority":
            payload["service_tier"] = "priority"
        else:
            payload.pop("service_tier", None)
    else:
        payload.pop("service_tier", None)
    field_changes: list[dict[str, Any]] = []
    warnings: list[str] = []
    upstream_model = context.resolved_model.upstream_model
    payload["model"] = upstream_model
    sampling_changes, sampling_warnings = apply_model_sampling_defaults(payload, upstream_model)
    field_changes.extend(sampling_changes)
    warnings.extend(sampling_warnings)
    headers = build_upstream_headers(
        {k: v for k, v in getattr(context, "request_headers", {}).items() if k.lower() != "anthropic-beta"},
        stable_key=getattr(context, "stable_key", ""),
        affinity_hash_secret=getattr(getattr(context, "settings", None), "affinity_hash_secret", None) or getattr(getattr(context, "settings", None), "log_hash_secret", None),
    )
    upstream_base_url = getattr(getattr(context, "settings", None), "upstream_base_url", None)
    if upstream_base_url:
        headers["x-fireworks-upstream-path"] = resolve_inference_path(upstream_base_url, "anthropic_messages")
    return payload, headers, {"field_changes": field_changes, "warnings": warnings}
