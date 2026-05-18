from __future__ import annotations

import json
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


class ResponsesSSECanonicalizer:
    """Canonicalize Responses SSE for clients that translate it back to Anthropic.

    Some upstreams emit Fireworks/OpenAI-compatible events with small shape or
    ordering differences. Claude Code, through sub2api, is strict about the
    Anthropic block lifecycle generated from Responses events: function and
    reasoning deltas need a preceding output_item.added so sub2api can emit a
    content_block_start before content_block_delta.
    """

    def __init__(self, *, suppress_reasoning: bool = False, sub2api_bridge_compat: bool = False) -> None:
        self._buffer = ""
        self._started_indexes: set[int] = set()
        self._closed_indexes: set[int] = set()
        self._function_indexes: set[int] = set()
        self._function_args_done_indexes: set[int] = set()
        self._buffered_function_added: dict[int, dict[str, Any]] = {}
        self._buffered_function_args: dict[int, str] = {}
        self._function_names: dict[int, str] = {}
        self._reasoning_indexes: set[int] = set()
        self._suppress_reasoning = suppress_reasoning
        self._sub2api_bridge_compat = sub2api_bridge_compat or suppress_reasoning

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

        synthetic.append(self._format_json_event(event_type, payload))
        return synthetic

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
