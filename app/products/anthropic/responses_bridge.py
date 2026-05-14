from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


def _is_text_block(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)


def _text_from_content(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [block.get("text") for block in content if _is_text_block(block)]
        text = "".join(part for part in parts if isinstance(part, str))
        return text or None
    return None


def _tool_id_from_anthropic_id(tool_id: str) -> str:
    # Conservative normalization: preserve Anthropic ids when possible; only
    # synthesize OpenAI-style ids when the upstream shape needs one.
    if tool_id.startswith(("toolu_", "call_", "fc_")):
        return tool_id
    return f"fc_{tool_id}"


def _map_tool_choice(tool_choice: Any) -> Any:
    if isinstance(tool_choice, str):
        return tool_choice
    if not isinstance(tool_choice, dict):
        return None
    choice_type = tool_choice.get("type")
    if choice_type == "auto":
        return "auto"
    if choice_type == "any":
        return "required"
    if choice_type == "none":
        return "none"
    if choice_type == "tool" and isinstance(tool_choice.get("name"), str):
        return {"type": "function", "name": tool_choice["name"]}
    return None


def _map_message_block(block: dict[str, Any]) -> dict[str, Any] | None:
    block_type = block.get("type")
    if block_type == "text" and isinstance(block.get("text"), str):
        return {"type": "input_text", "text": block["text"]}
    if block_type == "image":
        source = block.get("source")
        if isinstance(source, dict) and source.get("type") == "base64" and isinstance(source.get("data"), str):
            return {"type": "input_image", "image_url": f"data:{source.get('media_type', 'image/png')};base64,{source['data']}"}
        if isinstance(source, dict) and source.get("type") == "url" and isinstance(source.get("url"), str):
            return {"type": "input_image", "image_url": source["url"]}
    if block_type == "tool_result" and isinstance(block.get("tool_use_id"), str):
        content = block.get("content")
        text = _text_from_content(content)
        return {
            "type": "function_call_output",
            "call_id": _tool_id_from_anthropic_id(block["tool_use_id"]),
            "output": text if text is not None else json.dumps(content, ensure_ascii=False),
        }
    if block_type == "tool_use" and isinstance(block.get("id"), str) and isinstance(block.get("name"), str):
        return {
            "type": "function_call",
            "call_id": _tool_id_from_anthropic_id(block["id"]),
            "name": block["name"],
            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
        }
    return None


def _map_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "system":
            text = _text_from_content(content)
            if text:
                items.append({"role": "system", "content": text})
            continue
        if role not in {"user", "assistant"}:
            continue
        if isinstance(content, str):
            items.append({"role": role, "content": content})
            continue
        if isinstance(content, list):
            mapped_blocks = [mapped for block in content if isinstance(block, dict) and (mapped := _map_message_block(block))]
            if mapped_blocks:
                items.append({"role": role, "content": mapped_blocks})
    return items


def bridge_requires_stored_response(body: dict[str, Any], previous_response_id: str | None = None) -> bool:
    """Return whether bridge mode should ask Fireworks to store the response.

    Fireworks continuations require a stored upstream response.  Keep the
    default close to sub2api (`store=false`) for simple one-shot text requests,
    but store tool-capable or continued bridge turns so `previous_response_id`
    can be used safely on the following request.
    """

    if previous_response_id:
        return True
    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        return True
    messages = body.get("messages")
    if isinstance(messages, list):
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list) and any(isinstance(block, dict) and block.get("type") in {"tool_use", "tool_result"} for block in content):
                return True
    return False


def build_responses_bridge_payload(body: dict[str, Any], upstream_model: str, previous_response_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    output_config = body.get("output_config") if isinstance(body.get("output_config"), dict) else {}
    max_tokens = body.get("max_tokens")
    payload: dict[str, Any] = {
        "model": upstream_model,
        "stream": True,
        "store": bridge_requires_stored_response(body, previous_response_id),
        "parallel_tool_calls": True,
        "include": ["reasoning.encrypted_content"],
        "text": {"verbosity": "medium"},
        "reasoning": {"effort": output_config.get("effort") or "medium", "summary": "auto"},
    }
    if isinstance(body.get("system"), str):
        payload["instructions"] = body["system"]
    elif isinstance(body.get("system"), list):
        payload["instructions"] = "".join(block.get("text", "") for block in body["system"] if _is_text_block(block))
    if previous_response_id:
        payload["previous_response_id"] = previous_response_id
    if isinstance(max_tokens, int) and not isinstance(max_tokens, bool):
        payload["max_output_tokens"] = max(max_tokens, 128)
    if isinstance(body.get("messages"), list):
        payload["input"] = _map_messages(body["messages"])
    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        payload["tools"] = [
            {"type": "function", "name": tool["name"], **({"description": tool["description"]} if isinstance(tool.get("description"), str) else {}), **({"parameters": tool["input_schema"]} if isinstance(tool.get("input_schema"), dict) else {})}
            for tool in tools
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        ]
    tool_choice = _map_tool_choice(body.get("tool_choice"))
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    report = {"tool_choice": payload.get("tool_choice"), "message_count": len(payload.get("input", []))}
    return payload, report


def trim_responses_input_to_latest_turn(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("previous_response_id"):
        return payload
    items = payload.get("input")
    if not isinstance(items, list) or not items:
        return payload
    trimmed: list[dict[str, Any]] = []
    for item in items[::-1]:
        if isinstance(item, dict) and item.get("type") == "function_call_output":
            trimmed.insert(0, item)
            continue
        trimmed.insert(0, item)
        break
    new_payload = dict(payload)
    new_payload["input"] = trimmed
    return new_payload


@dataclass
class ResponsesToAnthropicStreamAdapter:
    model: str = ""
    _buffer: str = ""
    _message_started: bool = False
    _message_stopped: bool = False
    _response_id: str = ""
    _created: int | None = None
    _open_block_index: int | None = None
    _open_block_type: str = ""
    _next_block_index: int = 0
    _output_to_block: dict[int, int] = field(default_factory=dict)
    _tool_args: dict[int, str] = field(default_factory=dict)
    _tool_had_delta: set[int] = field(default_factory=set)
    _has_tool_call: bool = False
    _usage: dict[str, Any] = field(default_factory=dict)

    @property
    def response_id(self) -> str | None:
        return self._response_id or None

    def feed(self, chunk: bytes | dict[str, Any]) -> bytes | list[dict[str, Any]]:
        if isinstance(chunk, dict):
            return self._events_from_payload(chunk)
        self._buffer += chunk.decode("utf-8", errors="ignore").replace("\r\n", "\n")
        out: list[str] = []
        while "\n\n" in self._buffer:
            raw_event, self._buffer = self._buffer.split("\n\n", 1)
            out.extend(self._sse_from_raw_event(raw_event))
        return "".join(out).encode("utf-8")

    def flush(self) -> bytes:
        out: list[str] = []
        if self._buffer.strip():
            out.extend(self._sse_from_raw_event(self._buffer))
        self._buffer = ""
        if self._message_started and not self._message_stopped:
            out.extend(self._format_event(event) for event in self._final_events())
        return "".join(out).encode("utf-8")

    def _sse_from_raw_event(self, raw_event: str) -> list[str]:
        data_lines: list[str] = []
        event_name: str | None = None
        for line in raw_event.split("\n"):
            if line.startswith("event:"):
                event_name = line[6:].strip() or None
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            return []
        data = "\n".join(data_lines).strip()
        if not data or data == "[DONE]":
            return []
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, dict):
            return []
        if event_name and not isinstance(payload.get("type"), str):
            payload = {**payload, "type": event_name}
        return [self._format_event(event) for event in self._events_from_payload(payload)]

    def _events_from_payload(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        event_type = event.get("type")
        response = event.get("response") if isinstance(event.get("response"), dict) else None
        if response is not None:
            self._capture_response(response)
        out: list[dict[str, Any]] = []
        if event_type == "response.created":
            out.extend(self._ensure_message_start())
        elif event_type == "response.output_item.added":
            out.extend(self._handle_output_item_added(event))
        elif event_type == "response.output_text.delta":
            out.extend(self._handle_text_delta(event))
        elif event_type == "response.function_call_arguments.delta":
            out.extend(self._handle_function_delta(event))
        elif event_type == "response.function_call_arguments.done":
            out.extend(self._handle_function_done(event))
        elif event_type in {"response.output_text.done", "response.output_item.done"}:
            out.extend(self._close_current_block())
        elif event_type in {"response.completed", "response.done", "response.incomplete", "response.failed"}:
            out.extend(self._final_events(response=response, failed=event_type == "response.failed"))
        return out

    def _capture_response(self, response: dict[str, Any]) -> None:
        if isinstance(response.get("id"), str) and response["id"].strip():
            self._response_id = response["id"].strip()
        if isinstance(response.get("model"), str) and not self.model:
            self.model = response["model"]
        if isinstance(response.get("created"), int):
            self._created = response["created"]
        usage = response.get("usage")
        if isinstance(usage, dict):
            self._usage = usage

    def _ensure_message_start(self) -> list[dict[str, Any]]:
        if self._message_started:
            return []
        self._message_started = True
        return [{
            "type": "message_start",
            "message": {
                "id": self._response_id or "msg_fireworks2api_bridge",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": self.model,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        }]

    def _start_block(self, block_type: str, content_block: dict[str, Any], output_index: int | None = None) -> list[dict[str, Any]]:
        events = self._ensure_message_start()
        events.extend(self._close_current_block())
        index = self._next_block_index
        self._next_block_index += 1
        self._open_block_index = index
        self._open_block_type = block_type
        if output_index is not None:
            self._output_to_block[output_index] = index
        events.append({"type": "content_block_start", "index": index, "content_block": content_block})
        return events

    def _handle_output_item_added(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        item = event.get("item") if isinstance(event.get("item"), dict) else None
        output_index = event.get("output_index") if isinstance(event.get("output_index"), int) else None
        if not isinstance(item, dict):
            return []
        item_type = item.get("type")
        if item_type == "function_call":
            self._has_tool_call = True
            call_id = item.get("call_id") if isinstance(item.get("call_id"), str) else item.get("id")
            content_block = {"type": "tool_use", "id": call_id or f"toolu_{output_index or self._next_block_index}", "name": item.get("name") or "tool", "input": {}}
            return self._start_block("tool_use", content_block, output_index)
        if item_type == "message":
            return []
        return []

    def _handle_text_delta(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        delta = event.get("delta")
        if not isinstance(delta, str) or delta == "":
            return []
        output_index = event.get("output_index") if isinstance(event.get("output_index"), int) else None
        events: list[dict[str, Any]] = []
        if self._open_block_type != "text":
            events.extend(self._start_block("text", {"type": "text", "text": ""}, output_index))
        index = self._open_block_index if self._open_block_index is not None else 0
        events.append({"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": delta}})
        return events

    def _handle_function_delta(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        delta = event.get("delta")
        if not isinstance(delta, str) or delta == "":
            return []
        output_index = event.get("output_index") if isinstance(event.get("output_index"), int) else 0
        self._tool_args[output_index] = self._tool_args.get(output_index, "") + delta
        self._tool_had_delta.add(output_index)
        if output_index not in self._output_to_block:
            self._has_tool_call = True
            events = self._start_block("tool_use", {"type": "tool_use", "id": event.get("call_id") or f"toolu_{output_index}", "name": event.get("name") or "tool", "input": {}}, output_index)
        else:
            events = []
        index = self._output_to_block.get(output_index, self._open_block_index or 0)
        events.append({"type": "content_block_delta", "index": index, "delta": {"type": "input_json_delta", "partial_json": delta}})
        return events

    def _handle_function_done(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        output_index = event.get("output_index") if isinstance(event.get("output_index"), int) else 0
        args = event.get("arguments") if isinstance(event.get("arguments"), str) else self._tool_args.get(output_index, "")
        events: list[dict[str, Any]] = []
        if args and output_index not in self._tool_had_delta:
            if output_index not in self._output_to_block:
                events.extend(self._start_block("tool_use", {"type": "tool_use", "id": event.get("call_id") or f"toolu_{output_index}", "name": event.get("name") or "tool", "input": {}}, output_index))
            index = self._output_to_block.get(output_index, self._open_block_index or 0)
            events.append({"type": "content_block_delta", "index": index, "delta": {"type": "input_json_delta", "partial_json": args}})
        events.extend(self._close_current_block())
        return events

    def _close_current_block(self) -> list[dict[str, Any]]:
        if self._open_block_index is None:
            return []
        index = self._open_block_index
        self._open_block_index = None
        self._open_block_type = ""
        return [{"type": "content_block_stop", "index": index}]

    def _final_events(self, response: dict[str, Any] | None = None, failed: bool = False) -> list[dict[str, Any]]:
        if response:
            self._capture_response(response)
        if self._message_stopped:
            return []
        events = self._ensure_message_start()
        events.extend(self._close_current_block())
        stop_reason = "tool_use" if self._has_tool_call else "end_turn"
        if failed:
            stop_reason = "error"
        elif response and response.get("status") == "incomplete":
            details = response.get("incomplete_details") if isinstance(response.get("incomplete_details"), dict) else {}
            if details.get("reason") == "max_output_tokens":
                stop_reason = "max_tokens"
        events.append({"type": "message_delta", "delta": {"stop_reason": stop_reason}, "usage": self._anthropic_usage()})
        events.append({"type": "message_stop"})
        self._message_stopped = True
        return events

    def _anthropic_usage(self) -> dict[str, int]:
        return {
            "input_tokens": int(self._usage.get("input_tokens") or self._usage.get("prompt_tokens") or 0),
            "output_tokens": int(self._usage.get("output_tokens") or self._usage.get("completion_tokens") or 0),
            "cache_read_input_tokens": int(((self._usage.get("input_tokens_details") or self._usage.get("prompt_tokens_details") or {}) or {}).get("cached_tokens") or 0),
        }

    @staticmethod
    def _format_event(event: dict[str, Any]) -> str:
        event_type = event.get("type") or "message_delta"
        return f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
