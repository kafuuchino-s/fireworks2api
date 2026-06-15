from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any


_TOOL_NAME_MAP = {
    "apply_patch": "edit",
    "applyPatch": "edit",
    "update_plan": "todowrite",
    "updatePlan": "todowrite",
    "read_plan": "todoread",
    "readPlan": "todoread",
    "search_files": "grep",
    "searchFiles": "grep",
    "list_files": "glob",
    "listFiles": "glob",
    "read_file": "read",
    "readFile": "read",
    "write_file": "write",
    "writeFile": "write",
    "execute_bash": "bash",
    "executeBash": "bash",
    "exec_bash": "bash",
    "execBash": "bash",
    "fetch": "webfetch",
    "web_fetch": "webfetch",
    "webFetch": "webfetch",
}


def strip_reasoning_output_items(payload: dict[str, Any]) -> dict[str, Any]:
    updated = _strip_reasoning_from_response_object(payload)
    response = updated.get("response")
    if isinstance(response, dict):
        stripped_response = _strip_reasoning_from_response_object(response)
        if stripped_response is not response:
            updated = dict(updated)
            updated["response"] = stripped_response
    return updated


def _strip_reasoning_from_response_object(response: dict[str, Any]) -> dict[str, Any]:
    output = response.get("output")
    if not isinstance(output, list):
        return response
    stripped = [item for item in output if not (isinstance(item, dict) and item.get("type") == "reasoning")]
    if len(stripped) == len(output):
        return response
    updated = dict(response)
    updated["output"] = stripped
    return updated


class ChatCompletionsToResponsesSSE:
    def __init__(
        self,
        *,
        model: str,
        upstream_model: str,
        perf_metrics_in_response: bool | None = None,
        service_tier: str | None = None,
        sub2api_bridge_compat: bool = False,
        request_payload: dict[str, Any] | None = None,
    ) -> None:
        self._buffer = ""
        self._model = model
        self._upstream_model = upstream_model
        self._perf_metrics_in_response = perf_metrics_in_response
        self._service_tier = service_tier
        self._sub2api_bridge_compat = sub2api_bridge_compat
        self._request_payload = request_payload
        self._chat_id = "chatcmpl_unknown"
        self._response_id = "resp_fallback_chatcmpl_unknown"
        self._message_id = "msg_fallback_chatcmpl_unknown"
        self._created = False
        self._message_started = False
        self._message_index: int | None = None
        self._content_started = False
        self._completed = False
        self._next_output_index = 0
        self._reasoning_started = False
        self._reasoning_open = False
        self._reasoning_done = False
        self._reasoning_id = "rs_fallback_chatcmpl_unknown"
        self._reasoning_index: int | None = None
        self._reasoning_parts: list[str] = []
        self._text_parts: list[str] = []
        self._tool_calls: dict[int, dict[str, Any]] = {}
        self._usage: dict[str, Any] = {}
        self._finish_reason: str | None = None

    def feed(self, chunk: bytes) -> bytes:
        self._buffer += chunk.decode("utf-8", errors="ignore").replace("\r\n", "\n")
        out: list[str] = []
        while "\n\n" in self._buffer:
            raw_event, self._buffer = self._buffer.split("\n\n", 1)
            out.extend(self._convert_event(raw_event))
        return "".join(out).encode("utf-8")

    def flush(self) -> bytes:
        out: list[str] = []
        if self._buffer.strip():
            out.extend(self._convert_event(self._buffer))
            self._buffer = ""
        if not self._completed:
            out.extend(self._complete_events())
        return "".join(out).encode("utf-8")

    def _convert_event(self, raw_event: str) -> list[str]:
        data_lines = [line[5:].strip() for line in raw_event.split("\n") if line.startswith("data:")]
        if not data_lines:
            return []
        data = "\n".join(data_lines).strip()
        if not data:
            return []
        if data == "[DONE]":
            return self._complete_events()
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, dict):
            return []
        self._capture_ids(payload)
        if isinstance(payload.get("usage"), dict):
            self._usage = dict(payload["usage"])

        out: list[str] = []
        choices = payload.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                finish_reason = choice.get("finish_reason")
                if isinstance(finish_reason, str):
                    self._finish_reason = finish_reason
                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    continue
                reasoning = delta.get("reasoning_content")
                if isinstance(reasoning, str) and reasoning:
                    out.extend(self._ensure_reasoning_started())
                    self._reasoning_parts.append(reasoning)
                    out.append(
                        self._json_event(
                            "response.reasoning_summary_text.delta",
                            {
                                "type": "response.reasoning_summary_text.delta",
                                "item_id": self._reasoning_id,
                                "output_index": self._reasoning_index,
                                "summary_index": 0,
                                "delta": reasoning,
                            },
                        )
                    )
                content = delta.get("content")
                if isinstance(content, str) and content:
                    out.extend(self._close_reasoning())
                    out.extend(self._ensure_text_started())
                    self._text_parts.append(content)
                    out.append(
                        self._json_event(
                            "response.output_text.delta",
                            {
                                "type": "response.output_text.delta",
                                "item_id": self._message_id,
                                "output_index": self._message_index,
                                "content_index": 0,
                                "delta": content,
                            },
                        )
                    )
                tool_calls = delta.get("tool_calls")
                if isinstance(tool_calls, list):
                    out.extend(self._close_reasoning())
                    for tool_call in tool_calls:
                        if isinstance(tool_call, dict):
                            out.extend(self._apply_tool_call_delta(tool_call))
        return out

    def _capture_ids(self, payload: dict[str, Any]) -> None:
        chat_id = payload.get("id")
        if isinstance(chat_id, str) and chat_id.strip():
            self._chat_id = chat_id.strip()
            self._response_id = f"resp_fallback_{self._chat_id}"
            self._message_id = f"msg_fallback_{self._chat_id}"
            self._reasoning_id = f"rs_fallback_{self._chat_id}"

    def _ensure_created(self) -> list[str]:
        if self._created:
            return []
        self._created = True
        response = {
            "id": self._response_id,
            "object": "response",
            "status": "in_progress",
            "model": self._model,
            "output": [],
            "store": False,
            "provider": {"name": "fireworks", "endpoint": "chat_completions", "upstream_model": self._upstream_model},
        }
        if self._service_tier is not None:
            response["service_tier"] = self._service_tier
        return [self._json_event("response.created", {"type": "response.created", "response": response})]

    def _ensure_text_started(self) -> list[str]:
        out = self._ensure_created()
        if not self._message_started:
            self._message_started = True
            self._message_index = self._alloc_output_index()
            out.append(
                self._json_event(
                    "response.output_item.added",
                    {
                        "type": "response.output_item.added",
                        "output_index": self._message_index,
                        "item": {
                            "id": self._message_id,
                            "type": "message",
                            "role": "assistant",
                            "status": "in_progress",
                            "content": [],
                        },
                    },
                )
            )
        if not self._content_started:
            self._content_started = True
            if not self._sub2api_bridge_compat:
                out.append(
                    self._json_event(
                        "response.content_part.added",
                        {
                            "type": "response.content_part.added",
                            "item_id": self._message_id,
                            "output_index": self._message_index,
                            "content_index": 0,
                            "part": {"type": "output_text", "text": ""},
                        },
                    )
                )
        return out

    def _complete_events(self) -> list[str]:
        if self._completed:
            return []
        self._completed = True
        out = self._ensure_created()
        out.extend(self._close_reasoning())
        text = "".join(self._text_parts)
        if self._usage.get("completion_tokens") is None:
            try:
                from app.dataplane.usage_estimator import estimate_output_tokens_from_text
                estimated_output = estimate_output_tokens_from_text(
                    "".join(self._text_parts + self._reasoning_parts + [tool_call["arguments"] for tool_call in self._tool_calls.values()]),
                    self._upstream_model,
                )
                if estimated_output:
                    self._usage["completion_tokens"] = estimated_output
            except Exception:
                pass
        if self._message_started:
            if not self._sub2api_bridge_compat:
                out.append(
                    self._json_event(
                        "response.output_text.done",
                        {
                            "type": "response.output_text.done",
                            "item_id": self._message_id,
                            "output_index": self._message_index,
                            "content_index": 0,
                            "text": text,
                        },
                    )
                )
                out.append(
                    self._json_event(
                        "response.content_part.done",
                        {
                            "type": "response.content_part.done",
                            "item_id": self._message_id,
                            "output_index": self._message_index,
                            "content_index": 0,
                            "part": {"type": "output_text", "text": text},
                        },
                    )
                )
            # Always emit output_item.done for message so sub2api can close the
            # downstream Anthropic text content block.  Without this event,
            # sub2api never sees content_block_stop for text and subsequent
            # block indexing breaks ("Content block not found").
            out.append(
                self._json_event(
                    "response.output_item.done",
                    {
                        "type": "response.output_item.done",
                        "output_index": self._message_index,
                        "item": {
                            "id": self._message_id,
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": text}],
                        },
                    },
                )
            )
        out.extend(self._close_tool_items())
        out.append(self._json_event("response.completed", {"type": "response.completed", "response": self._completed_response(text)}))
        return out

    def _alloc_output_index(self) -> int:
        index = self._next_output_index
        self._next_output_index += 1
        return index

    def _ensure_reasoning_started(self) -> list[str]:
        if self._reasoning_started or self._reasoning_done:
            return []
        self._reasoning_started = True
        self._reasoning_open = True
        self._reasoning_index = self._alloc_output_index()
        out = self._ensure_created()
        out.append(
            self._json_event(
                "response.output_item.added",
                {
                    "type": "response.output_item.added",
                    "output_index": self._reasoning_index,
                    "item": {"id": self._reasoning_id, "type": "reasoning", "status": "in_progress"},
                },
            )
        )
        out.append(
            self._json_event(
                "response.reasoning_summary_part.added",
                {
                    "type": "response.reasoning_summary_part.added",
                    "item_id": self._reasoning_id,
                    "output_index": self._reasoning_index,
                    "summary_index": 0,
                    "part": {"type": "summary_text", "text": ""},
                },
            )
        )
        return out

    def _close_reasoning(self) -> list[str]:
        if not self._reasoning_open:
            return []
        self._reasoning_open = False
        self._reasoning_done = True
        reasoning = "".join(self._reasoning_parts)
        out = [
            self._json_event(
                "response.reasoning_summary_text.done",
                {
                    "type": "response.reasoning_summary_text.done",
                    "item_id": self._reasoning_id,
                    "output_index": self._reasoning_index,
                    "summary_index": 0,
                    "text": reasoning,
                },
            )
        ]
        out.append(
            self._json_event(
                "response.reasoning_summary_part.done",
                {
                    "type": "response.reasoning_summary_part.done",
                    "item_id": self._reasoning_id,
                    "output_index": self._reasoning_index,
                    "summary_index": 0,
                    "part": {"type": "summary_text", "text": reasoning},
                },
            )
        )
        out.append(
            self._json_event(
                "response.output_item.done",
                {
                    "type": "response.output_item.done",
                    "output_index": self._reasoning_index,
                    "item": {
                        "id": self._reasoning_id,
                        "type": "reasoning",
                        "status": "completed",
                        "summary": [{"type": "summary_text", "text": reasoning}],
                    },
                },
            )
        )
        return out

    def _apply_tool_call_delta(self, tool_call: dict[str, Any]) -> list[str]:
        index = tool_call.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            index = 0
        function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        stored = self._tool_calls.get(index)
        out: list[str] = []
        if stored is None:
            item_id = f"fc_fallback_{index}_{self._chat_id}"
            call_id = tool_call.get("id") if isinstance(tool_call.get("id"), str) and tool_call.get("id") else f"call_fallback_{index}"
            name = function.get("name") if isinstance(function.get("name"), str) else ""
            # Correct tool name for sub2api compatibility (e.g. apply_patch -> edit)
            corrected_name = _TOOL_NAME_MAP.get(name.strip(), name.strip()) if name.strip() else name
            stored = {
                "id": item_id,
                "call_id": call_id,
                "name": corrected_name,
                "arguments": "",
                "output_index": self._alloc_output_index(),
            }
            self._tool_calls[index] = stored
            out.extend(self._ensure_created())
            out.append(
                self._json_event(
                    "response.output_item.added",
                    {
                        "type": "response.output_item.added",
                        "output_index": stored["output_index"],
                        "item": {
                            "id": item_id,
                            "type": "function_call",
                            "call_id": call_id,
                            "name": corrected_name,
                            "status": "in_progress",
                        },
                    },
                )
            )
        else:
            if isinstance(tool_call.get("id"), str) and tool_call["id"]:
                stored["call_id"] = tool_call["id"]
            if isinstance(function.get("name"), str) and function["name"]:
                corrected_name = _TOOL_NAME_MAP.get(function["name"].strip(), function["name"].strip())
                stored["name"] = corrected_name

        arguments_delta = function.get("arguments")
        if isinstance(arguments_delta, str) and arguments_delta:
            stored["arguments"] += arguments_delta
            out.append(
                self._json_event(
                    "response.function_call_arguments.delta",
                    {
                        "type": "response.function_call_arguments.delta",
                        "item_id": stored["id"],
                        "output_index": stored["output_index"],
                        "delta": arguments_delta,
                        "call_id": stored["call_id"],
                        "name": stored["name"],
                    },
                )
            )
        return out

    def _close_tool_items(self) -> list[str]:
        out: list[str] = []
        for index in sorted(self._tool_calls):
            tool_call = self._tool_calls[index]
            arguments = tool_call["arguments"] if str(tool_call["arguments"]).strip() else "{}"
            # Correct tool argument names for sub2api compatibility
            corrected_arguments = _correct_tool_arguments(arguments, tool_call["name"])
            out.append(
                self._json_event(
                    "response.function_call_arguments.done",
                    {
                        "type": "response.function_call_arguments.done",
                        "item_id": tool_call["id"],
                        "output_index": tool_call["output_index"],
                        "arguments": corrected_arguments,
                        "call_id": tool_call["call_id"],
                        "name": tool_call["name"],
                    },
                )
            )
            out.append(
                self._json_event(
                    "response.output_item.done",
                    {
                        "type": "response.output_item.done",
                        "output_index": tool_call["output_index"],
                        "item": {
                            "id": tool_call["id"],
                            "type": "function_call",
                            "call_id": tool_call["call_id"],
                            "name": tool_call["name"],
                            "arguments": corrected_arguments,
                            "status": "completed",
                        },
                    },
                )
            )
        return out

    def _completed_response(self, text: str) -> dict[str, Any]:
        status = "incomplete" if self._finish_reason == "length" else "completed"
        output = []
        reasoning = "".join(self._reasoning_parts)
        if reasoning:
            output.append(
                {
                    "id": self._reasoning_id,
                    "type": "reasoning",
                    "status": "completed",
                    "summary": [{"type": "summary_text", "text": reasoning}],
                }
            )
        if self._message_started or not self._tool_calls:
            output.append(
                {
                    "id": self._message_id,
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": text}],
                }
            )
        for index in sorted(self._tool_calls):
            tool_call = self._tool_calls[index]
            arguments = tool_call["arguments"] if str(tool_call["arguments"]).strip() else "{}"
            corrected_arguments = _correct_tool_arguments(arguments, tool_call["name"])
            output.append(
                {
                    "id": tool_call["id"],
                    "type": "function_call",
                    "call_id": tool_call["call_id"],
                    "name": tool_call["name"],
                    "arguments": corrected_arguments,
                    "status": "completed",
                }
            )
        response: dict[str, Any] = {
            "id": self._response_id,
            "object": "response",
            "status": status,
            "model": self._model,
            "output": output,
            "usage": self._responses_usage(),
            "store": False,
            "provider": {"name": "fireworks", "endpoint": "chat_completions", "upstream_model": self._upstream_model},
        }
        if self._service_tier is not None:
            response["service_tier"] = self._service_tier
        if self._finish_reason == "length":
            response["incomplete_details"] = {"reason": "max_output_tokens"}
        if self._perf_metrics_in_response is not None:
            response["perf_metrics_in_response"] = self._perf_metrics_in_response
        return response

    def _responses_usage(self, *, fallback_output_tokens: int | None = None) -> dict[str, Any]:
        usage = self._usage
        input_details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
        output_details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
        output_tokens = usage.get("completion_tokens")
        if output_tokens is None and fallback_output_tokens:
            output_tokens = fallback_output_tokens
        input_tokens = usage.get("prompt_tokens")
        if input_tokens is None and self._request_payload is not None:
            try:
                from app.dataplane.usage_estimator import estimate_input_tokens
                estimated_input = estimate_input_tokens(self._request_payload, self._upstream_model)
                if estimated_input:
                    input_tokens = estimated_input
            except Exception:
                pass
        total_tokens = usage.get("total_tokens")
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens
        response_usage: dict[str, Any] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }
        if input_details:
            response_usage["input_tokens_details"] = input_details
        if output_details:
            response_usage["output_tokens_details"] = output_details
        # Mark usage as estimated if any value was synthesized because the
        # upstream chat completion stream did not carry that field.
        if input_tokens is not None or output_tokens is not None:
            response_usage["estimated"] = True
        return response_usage

    def estimated_output_tokens(self, estimator: Callable[[str], int]) -> int:
        """Return an output-token estimate from generated text when upstream omits usage."""
        text = "".join(self._text_parts)
        reasoning = "".join(self._reasoning_parts)
        tool_arguments = "".join(tool_call["arguments"] for tool_call in self._tool_calls.values())
        total = estimator(text) + estimator(reasoning) + estimator(tool_arguments)
        return max(0, total)

    @staticmethod
    def _json_event(event_type: str, payload: dict[str, Any]) -> str:
        return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


class ResponsesSSECanonicalizer:
    """Canonicalize Responses SSE for clients that translate it back to Anthropic.

    Some upstreams emit Fireworks/OpenAI-compatible events with small shape or
    ordering differences. Claude Code, through sub2api, is strict about the
    Anthropic block lifecycle generated from Responses events: function and
    reasoning deltas need a preceding output_item.added so sub2api can emit a
    content_block_start before content_block_delta.
    """

    def __init__(self, *, suppress_reasoning: bool = False, sub2api_bridge_compat: bool = False, upstream_model: str | None = None, request_payload: dict[str, Any] | None = None) -> None:
        self._buffer = ""
        self._started_indexes: set[int] = set()
        self._closed_indexes: set[int] = set()
        self._function_indexes: set[int] = set()
        self._function_args_done_indexes: set[int] = set()
        self._buffered_function_added: dict[int, dict[str, Any]] = {}
        self._buffered_function_args: dict[int, str] = {}
        self._function_names: dict[int, str] = {}
        self._reasoning_indexes: set[int] = set()
        self._text_parts: list[str] = []
        self._reasoning_parts: list[str] = []
        self._function_arguments: list[str] = []
        self._suppress_reasoning = suppress_reasoning
        self._sub2api_bridge_compat = sub2api_bridge_compat or suppress_reasoning
        self._upstream_model = upstream_model
        self._request_payload = request_payload

    def feed(self, chunk: bytes) -> bytes:
        self._buffer += chunk.decode("utf-8", errors="ignore").replace("\r\n", "\n")
        out: list[str] = []
        while "\n\n" in self._buffer:
            raw_event, self._buffer = self._buffer.split("\n\n", 1)
            out.extend(self._canonicalize_event(raw_event))
        return "".join(out).encode("utf-8")

    def flush(self) -> bytes:
        if not self._buffer:
            return b""
        raw_event = self._buffer
        self._buffer = ""
        return "".join(self._canonicalize_event(raw_event)).encode("utf-8")

    def _canonicalize_event(self, raw_event: str) -> list[str]:
        if not raw_event.strip():
            return []
        event_name: str | None = None
        data_lines: list[str] = []
        for line in raw_event.split("\n"):
            if line.startswith("event:"):
                event_name = line[6:].strip() or None
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            return [raw_event + "\n\n"]
        data = "\n".join(data_lines).strip()
        if not data or data == "[DONE]":
            return [self._format_event(event_name, data)]
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return [self._format_event(event_name, data)]
        if not isinstance(payload, dict):
            return [self._format_event(event_name, data)]

        event_type = payload.get("type") if isinstance(payload.get("type"), str) else event_name
        if event_type:
            payload = dict(payload)
            payload.setdefault("type", event_type)
        payload = self._wrap_top_level_response(event_type, payload)
        payload = self._correct_tool_payload(event_type, payload)
        if self._suppress_reasoning:
            payload = strip_reasoning_output_items(payload)

        synthetic: list[str] = []
        output_index = payload.get("output_index")
        if self._suppress_reasoning and self._is_reasoning_event(event_type, payload):
            if isinstance(output_index, int):
                self._reasoning_indexes.add(output_index)
                self._started_indexes.add(output_index)
                if event_type == "response.output_item.done":
                    self._closed_indexes.add(output_index)
            return []
        if isinstance(output_index, int):
            if self._sub2api_bridge_compat and self._is_unsafe_bridge_done_event(event_type, payload, output_index):
                return []
            if self._sub2api_bridge_compat and event_type == "response.output_item.added":
                item = payload.get("item")
                if isinstance(item, dict) and item.get("type") == "function_call":
                    self._buffered_function_added[output_index] = payload
                    self._function_indexes.add(output_index)
                    name = item.get("name")
                    if isinstance(name, str) and name:
                        self._function_names[output_index] = name
                    self._started_indexes.add(output_index)
                    self._closed_indexes.discard(output_index)
                    return []
            if self._sub2api_bridge_compat and event_type == "response.function_call_arguments.delta":
                self._buffered_function_args[output_index] = self._buffered_function_args.get(output_index, "") + str(payload.get("delta") or "")
                return []
            if event_type in {"response.function_call_arguments.delta", "response.function_call_arguments.done"}:
                if output_index in self._closed_indexes:
                    return []
                if output_index not in self._started_indexes:
                    synthetic.append(self._format_json_event("response.output_item.added", self._function_call_added_payload(payload)))
                    self._started_indexes.add(output_index)
            elif event_type in {"response.reasoning_summary_text.delta", "response.reasoning_summary_text.done"}:
                if output_index in self._closed_indexes:
                    return []
                if output_index not in self._started_indexes:
                    synthetic.append(self._format_json_event("response.output_item.added", self._reasoning_added_payload(payload)))
                    self._started_indexes.add(output_index)
            if event_type == "response.function_call_arguments.done":
                self._function_args_done_indexes.add(output_index)
                if self._sub2api_bridge_compat and output_index in self._buffered_function_added:
                    args = payload.get("arguments")
                    if not isinstance(args, str) or args == "":
                        args = self._buffered_function_args.get(output_index, "")
                    added_payload = self._buffered_function_added.pop(output_index)
                    args_done_payload = dict(payload)
                    args_done_payload["arguments"] = args
                    args_done_payload = self._correct_function_arguments(output_index, args_done_payload)
                    self._buffered_function_args.pop(output_index, None)
                    synthetic.append(self._format_json_event("response.output_item.added", added_payload))
                    synthetic.append(self._format_json_event("response.function_call_arguments.done", args_done_payload))
                    return synthetic

        if event_type == "response.output_item.added":
            item = payload.get("item")
            if isinstance(output_index, int) and isinstance(item, dict) and item.get("type") in {"function_call", "reasoning", "message"}:
                self._started_indexes.add(output_index)
                self._closed_indexes.discard(output_index)
                if item.get("type") == "function_call":
                    self._function_indexes.add(output_index)
                    name = item.get("name")
                    if isinstance(name, str) and name:
                        self._function_names[output_index] = name
                elif item.get("type") == "reasoning":
                    self._reasoning_indexes.add(output_index)
        elif event_type == "response.output_item.done" and isinstance(output_index, int):
            self._closed_indexes.add(output_index)
        elif event_type == "response.output_text.delta":
            delta = payload.get("delta")
            if isinstance(delta, str):
                self._text_parts.append(delta)
        elif event_type == "response.reasoning_summary_text.delta":
            delta = payload.get("delta")
            if isinstance(delta, str):
                self._reasoning_parts.append(delta)
        elif event_type == "response.function_call_arguments.delta":
            delta = payload.get("delta")
            if isinstance(delta, str):
                self._function_arguments.append(delta)

        if event_type == "response.completed" and self._upstream_model:
            payload = self._inject_usage(payload)

        synthetic.append(self._format_json_event(event_type, payload))
        return synthetic

    def _inject_usage(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = payload.get("response")
        if not isinstance(response, dict):
            return payload
        usage = response.get("usage")
        if isinstance(usage, dict) and usage.get("output_tokens") not in (None, 0, ""):
            return payload
        if not isinstance(usage, dict):
            usage = {}
        try:
            from app.dataplane.usage_estimator import estimate_output_tokens_from_text
            estimated_output = estimate_output_tokens_from_text(
                "".join(self._text_parts + self._reasoning_parts + self._function_arguments),
                self._upstream_model,
            )
        except Exception:
            estimated_output = 0
        if not estimated_output:
            return payload
        updated_usage = dict(usage)
        updated_usage["output_tokens"] = estimated_output
        if updated_usage.get("input_tokens") in (None, 0, "") and self._request_payload is not None:
            try:
                from app.dataplane.usage_estimator import estimate_input_tokens
                estimated_input = estimate_input_tokens(self._request_payload, self._upstream_model)
                if estimated_input:
                    updated_usage["input_tokens"] = estimated_input
            except Exception:
                pass
        if isinstance(updated_usage.get("input_tokens"), int) and isinstance(updated_usage.get("total_tokens"), int):
            updated_usage["total_tokens"] = max(updated_usage["total_tokens"], updated_usage["input_tokens"] + estimated_output)
        elif isinstance(updated_usage.get("input_tokens"), int):
            updated_usage["total_tokens"] = updated_usage["input_tokens"] + estimated_output
        updated_usage["estimated"] = True
        updated_response = dict(response)
        updated_response["usage"] = updated_usage
        updated_payload = dict(payload)
        updated_payload["response"] = updated_response
        return updated_payload

    def _correct_tool_payload(self, event_type: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        item = payload.get("item")
        if isinstance(item, dict) and item.get("type") == "function_call":
            corrected_item = self._correct_function_item(item)
            if corrected_item is not item:
                payload = dict(payload)
                payload["item"] = corrected_item
        output_index = payload.get("output_index")
        if event_type == "response.function_call_arguments.done" and isinstance(output_index, int):
            payload = self._correct_function_arguments(output_index, payload)
        return payload

    def _correct_function_item(self, item: dict[str, Any]) -> dict[str, Any]:
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            return item
        corrected_name = _TOOL_NAME_MAP.get(name.strip())
        if not corrected_name:
            return item
        corrected = dict(item)
        corrected["name"] = corrected_name
        return corrected

    def _correct_function_arguments(self, output_index: int, payload: dict[str, Any]) -> dict[str, Any]:
        tool_name = payload.get("name") if isinstance(payload.get("name"), str) else self._function_names.get(output_index)
        corrected_tool_name = _TOOL_NAME_MAP.get(tool_name.strip(), tool_name.strip()) if isinstance(tool_name, str) and tool_name.strip() else None
        if not corrected_tool_name:
            return payload
        arguments = payload.get("arguments")
        corrected_arguments = _correct_tool_arguments(arguments, corrected_tool_name)
        if corrected_arguments is arguments:
            return payload
        corrected = dict(payload)
        corrected["arguments"] = corrected_arguments
        return corrected

    @staticmethod
    def _is_reasoning_event(event_type: str | None, payload: dict[str, Any]) -> bool:
        if event_type and event_type.startswith("response.reasoning"):
            return True
        item = payload.get("item")
        return isinstance(item, dict) and item.get("type") == "reasoning"

    def _is_unsafe_bridge_done_event(self, event_type: str | None, payload: dict[str, Any], output_index: int) -> bool:
        if event_type in {"response.output_text.done", "response.content_part.added", "response.content_part.done"}:
            return True
        if event_type != "response.output_item.done":
            return False
        item = payload.get("item")
        item_type = item.get("type") if isinstance(item, dict) else None
        if item_type == "message":
            return True
        if item_type == "function_call" and output_index not in self._function_args_done_indexes:
            return True
        return False

    @staticmethod
    def _wrap_top_level_response(event_type: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        if not event_type or not event_type.startswith("response."):
            return payload
        if "response" in payload:
            return payload
        if payload.get("object") == "response" or (isinstance(payload.get("id"), str) and str(payload.get("id")).startswith("resp_")):
            response = {key: value for key, value in payload.items() if key not in {"type", "sequence_number"}}
            wrapped = {"type": event_type, "response": response}
            if "sequence_number" in payload:
                wrapped["sequence_number"] = payload["sequence_number"]
            return wrapped
        return payload

    @staticmethod
    def _function_call_added_payload(payload: dict[str, Any]) -> dict[str, Any]:
        output_index = payload.get("output_index", 0)
        item_id = payload.get("item_id") if isinstance(payload.get("item_id"), str) else f"fc_{output_index}"
        call_id = payload.get("call_id") if isinstance(payload.get("call_id"), str) else str(item_id)
        name = payload.get("name") if isinstance(payload.get("name"), str) and payload.get("name") else "tool"
        name = _TOOL_NAME_MAP.get(name, name)
        return {
            "type": "response.output_item.added",
            "output_index": output_index,
            "item": {"type": "function_call", "id": item_id, "call_id": call_id, "name": name, "status": "in_progress"},
        }

    @staticmethod
    def _reasoning_added_payload(payload: dict[str, Any]) -> dict[str, Any]:
        output_index = payload.get("output_index", 0)
        item_id = payload.get("item_id") if isinstance(payload.get("item_id"), str) else f"rs_{output_index}"
        return {
            "type": "response.output_item.added",
            "output_index": output_index,
            "item": {"type": "reasoning", "id": item_id, "status": "in_progress"},
        }

    def _format_json_event(self, event_type: str | None, payload: dict[str, Any]) -> str:
        event = event_type or (payload.get("type") if isinstance(payload.get("type"), str) else None)
        return self._format_event(event, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    def estimated_output_tokens(self, estimator: Callable[[str], int]) -> int:
        """Return an output-token estimate from generated text when upstream omits usage."""
        text = "".join(self._text_parts)
        reasoning = "".join(self._reasoning_parts)
        tool_arguments = "".join(self._function_arguments)
        total = estimator(text) + estimator(reasoning) + estimator(tool_arguments)
        return max(0, total)

    @staticmethod
    def _format_event(event_name: str | None, data: str) -> str:
        prefix = f"event: {event_name}\n" if event_name else ""
        return f"{prefix}data: {data}\n\n"


def _correct_tool_arguments(arguments: Any, tool_name: str) -> Any:
    if tool_name not in {"bash", "edit"} or not isinstance(arguments, str) or not arguments.strip():
        return arguments
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return arguments
    if not isinstance(parsed, dict):
        return arguments
    changed = False
    if tool_name == "bash":
        if "workdir" not in parsed and "work_dir" in parsed:
            parsed["workdir"] = parsed.pop("work_dir")
            changed = True
        elif "workdir" in parsed and "work_dir" in parsed:
            parsed.pop("work_dir", None)
            changed = True
    elif tool_name == "edit":
        for source in ("file_path", "path", "file"):
            if "filePath" not in parsed and source in parsed:
                parsed["filePath"] = parsed.pop(source)
                changed = True
                break
        for source, target in (("old_string", "oldString"), ("new_string", "newString"), ("replace_all", "replaceAll")):
            if source in parsed:
                parsed[target] = parsed.pop(source)
                changed = True
    if not changed:
        return arguments
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
