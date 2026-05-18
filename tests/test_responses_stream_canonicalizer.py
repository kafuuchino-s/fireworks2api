from __future__ import annotations

import json

from app.products.openai.responses_stream import ResponsesSSECanonicalizer


def _events(raw: bytes) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for block in raw.decode("utf-8").strip().split("\n\n"):
        data_line = next(line for line in block.split("\n") if line.startswith("data:"))
        events.append(json.loads(data_line[5:].strip()))
    return events


def test_canonicalizer_injects_output_item_added_before_function_delta() -> None:
    canonicalizer = ResponsesSSECanonicalizer()

    output = canonicalizer.feed(b'event: response.function_call_arguments.delta\ndata: {"output_index":0,"call_id":"toolu_1","name":"Write","delta":"{\\"content\\":"}\n\n')

    events = _events(output)
    assert events[0]["type"] == "response.output_item.added"
    assert events[0]["item"] == {"type": "function_call", "id": "fc_0", "call_id": "toolu_1", "name": "Write", "status": "in_progress"}
    assert events[1]["type"] == "response.function_call_arguments.delta"


def test_canonicalizer_injects_output_item_added_before_reasoning_delta() -> None:
    canonicalizer = ResponsesSSECanonicalizer()

    output = canonicalizer.feed(b'event: response.reasoning_summary_text.delta\ndata: {"output_index":1,"delta":"thinking"}\n\n')

    events = _events(output)
    assert events[0]["type"] == "response.output_item.added"
    assert events[0]["item"] == {"type": "reasoning", "id": "rs_1", "status": "in_progress"}
    assert events[1]["type"] == "response.reasoning_summary_text.delta"


def test_canonicalizer_can_suppress_reasoning_events_for_anthropic_bridges() -> None:
    canonicalizer = ResponsesSSECanonicalizer(suppress_reasoning=True)

    reasoning_start = canonicalizer.feed(b'event: response.output_item.added\ndata: {"type":"response.output_item.added","output_index":0,"item":{"type":"reasoning","id":"rs_1"}}\n\n')
    reasoning_delta = canonicalizer.feed(b'event: response.reasoning_summary_text.delta\ndata: {"output_index":0,"delta":"thinking"}\n\n')
    text = canonicalizer.feed(b'event: response.output_text.delta\ndata: {"output_index":1,"delta":"hello"}\n\n')

    assert reasoning_start == b""
    assert reasoning_delta == b""
    assert _events(text)[0]["type"] == "response.output_text.delta"


def test_suppress_reasoning_strips_reasoning_from_completed_response_output() -> None:
    canonicalizer = ResponsesSSECanonicalizer(suppress_reasoning=True)

    output = canonicalizer.feed(
        b'event: response.completed\n'
        b'data: {"id":"resp_1","object":"response","status":"completed","output":[{"type":"reasoning","id":"rs_1"},{"type":"message","id":"msg_1"}]}\n\n'
    )

    events = _events(output)
    assert events[0]["response"]["output"] == [{"type": "message", "id": "msg_1"}]


def test_bridge_compat_preserves_reasoning_when_not_suppressed() -> None:
    canonicalizer = ResponsesSSECanonicalizer(sub2api_bridge_compat=True)

    output = canonicalizer.feed(
        b'event: response.output_item.added\n'
        b'data: {"type":"response.output_item.added","output_index":0,"item":{"type":"reasoning","id":"rs_1"}}\n\n'
    )
    output += canonicalizer.feed(
        b'event: response.reasoning_summary_text.delta\n'
        b'data: {"type":"response.reasoning_summary_text.delta","output_index":0,"delta":"thinking"}\n\n'
    )

    events = _events(output)
    assert [event["type"] for event in events] == ["response.output_item.added", "response.reasoning_summary_text.delta"]


def test_suppressed_reasoning_can_fallback_to_text_delta() -> None:
    canonicalizer = ResponsesSSECanonicalizer(
        suppress_reasoning=True,
        reasoning_fallback_to_text=True,
    )

    reasoning_start = canonicalizer.feed(
        b'event: response.output_item.added\n'
        b'data: {"type":"response.output_item.added","output_index":0,"item":{"type":"reasoning","id":"rs_1"}}\n\n'
    )
    reasoning_delta = canonicalizer.feed(
        b'event: response.reasoning_summary_text.delta\n'
        b'data: {"type":"response.reasoning_summary_text.delta","output_index":0,"delta":"thinking"}\n\n'
    )

    assert reasoning_start == b""
    events = _events(reasoning_delta)
    assert events[0]["type"] == "response.output_text.delta"
    assert events[0]["delta"] == "thinking"


def test_bridge_compat_drops_message_done_events_that_close_current_block_globally() -> None:
    canonicalizer = ResponsesSSECanonicalizer(sub2api_bridge_compat=True)

    text = canonicalizer.feed(b'event: response.output_text.delta\ndata: {"output_index":0,"delta":"hello"}\n\n')
    text_done = canonicalizer.feed(b'event: response.output_text.done\ndata: {"output_index":0,"text":"hello"}\n\n')
    part_done = canonicalizer.feed(b'event: response.content_part.done\ndata: {"output_index":0,"part":{"type":"output_text"}}\n\n')
    item_done = canonicalizer.feed(b'event: response.output_item.done\ndata: {"output_index":0,"item":{"type":"message","id":"msg_1"}}\n\n')

    assert _events(text)[0]["type"] == "response.output_text.delta"
    assert text_done == b""
    assert part_done == b""
    assert item_done == b""


def test_bridge_compat_drops_early_function_item_done_before_arguments_done() -> None:
    canonicalizer = ResponsesSSECanonicalizer(sub2api_bridge_compat=True)

    added = canonicalizer.feed(b'event: response.output_item.added\ndata: {"type":"response.output_item.added","output_index":1,"item":{"type":"function_call","id":"fc_1","call_id":"call_1","name":"Write"}}\n\n')
    early_done = canonicalizer.feed(b'event: response.output_item.done\ndata: {"type":"response.output_item.done","output_index":1,"item":{"type":"function_call","id":"fc_1","call_id":"call_1","name":"Write"}}\n\n')
    delta = canonicalizer.feed(b'event: response.function_call_arguments.delta\ndata: {"output_index":1,"delta":"{}"}\n\n')
    args_done = canonicalizer.feed(b'event: response.function_call_arguments.done\ndata: {"output_index":1,"arguments":"{}"}\n\n')
    final_done = canonicalizer.feed(b'event: response.output_item.done\ndata: {"type":"response.output_item.done","output_index":1,"item":{"type":"function_call","id":"fc_1","call_id":"call_1","name":"Write"}}\n\n')

    assert added == b""
    assert early_done == b""
    assert delta == b""
    args_done_events = _events(args_done)
    assert [event["type"] for event in args_done_events] == ["response.output_item.added", "response.function_call_arguments.done"]
    assert args_done_events[1]["arguments"] == "{}"
    assert _events(final_done)[0]["type"] == "response.output_item.done"


def test_bridge_compat_buffers_interleaved_function_calls_into_complete_blocks() -> None:
    canonicalizer = ResponsesSSECanonicalizer(sub2api_bridge_compat=True)

    assert canonicalizer.feed(b'event: response.output_item.added\ndata: {"type":"response.output_item.added","output_index":1,"item":{"type":"function_call","id":"fc_1","call_id":"call_1","name":"Search"}}\n\n') == b""
    assert canonicalizer.feed(b'event: response.output_item.added\ndata: {"type":"response.output_item.added","output_index":2,"item":{"type":"function_call","id":"fc_2","call_id":"call_2","name":"Read"}}\n\n') == b""
    assert canonicalizer.feed(b'event: response.function_call_arguments.delta\ndata: {"output_index":1,"delta":"{\\"pattern\\":"}\n\n') == b""
    assert canonicalizer.feed(b'event: response.function_call_arguments.delta\ndata: {"output_index":2,"delta":"{\\"file\\":"}\n\n') == b""
    first_done = canonicalizer.feed(b'event: response.function_call_arguments.done\ndata: {"output_index":1,"arguments":"{\\"pattern\\":\\"README\\"}"}\n\n')
    second_done = canonicalizer.feed(b'event: response.function_call_arguments.done\ndata: {"output_index":2,"arguments":"{\\"file\\":\\"README.md\\"}"}\n\n')

    first_events = _events(first_done)
    second_events = _events(second_done)
    assert first_events[0]["item"]["call_id"] == "call_1"
    assert first_events[1]["type"] == "response.function_call_arguments.done"
    assert second_events[0]["item"]["call_id"] == "call_2"
    assert second_events[1]["type"] == "response.function_call_arguments.done"


def test_canonicalizer_adds_type_from_event_name_and_wraps_response_payload() -> None:
    canonicalizer = ResponsesSSECanonicalizer()

    output = canonicalizer.feed(b'event: response.completed\ndata: {"id":"resp_1","object":"response","status":"completed"}\n\n')

    events = _events(output)
    assert events == [{"type": "response.completed", "response": {"id": "resp_1", "object": "response", "status": "completed"}}]


def test_canonicalizer_drops_late_function_delta_after_item_done() -> None:
    canonicalizer = ResponsesSSECanonicalizer()

    first = canonicalizer.feed(b'event: response.output_item.added\ndata: {"type":"response.output_item.added","output_index":0,"item":{"type":"function_call","call_id":"toolu_1","name":"Write"}}\n\n')
    second = canonicalizer.feed(b'event: response.output_item.done\ndata: {"type":"response.output_item.done","output_index":0,"item":{"type":"function_call"}}\n\n')
    late = canonicalizer.feed(b'event: response.function_call_arguments.delta\ndata: {"output_index":0,"delta":"late"}\n\n')

    assert _events(first)[0]["type"] == "response.output_item.added"
    assert _events(second)[0]["type"] == "response.output_item.done"
    assert late == b""


def test_sub2api_facing_stream_keeps_anthropic_block_lifecycle_valid() -> None:
    canonicalizer = ResponsesSSECanonicalizer(sub2api_bridge_compat=True)

    raw_chunks = [
        b'event: response.created\ndata: {"id":"resp_1","object":"response","status":"in_progress"}\n\n',
        b'event: response.output_item.added\ndata: {"output_index":0,"item":{"type":"function_call","id":"fc_1","call_id":"call_1","name":"Search"}}\n\n',
        b'event: response.output_item.done\ndata: {"output_index":0,"item":{"type":"function_call","id":"fc_1","call_id":"call_1","name":"Search"}}\n\n',
        b'event: response.function_call_arguments.delta\ndata: {"output_index":0,"delta":"{\\"query\\":"}\n\n',
        b'event: response.function_call_arguments.delta\ndata: {"output_index":0,"delta":"\\"cache\\"}"}\n\n',
        b'event: response.function_call_arguments.done\ndata: {"output_index":0,"arguments":"{\\"query\\":\\"cache\\"}"}\n\n',
        b'event: response.output_text.done\ndata: {"output_index":1,"text":"ignored closer"}\n\n',
        b'event: response.completed\ndata: {"id":"resp_1","object":"response","status":"completed"}\n\n',
    ]

    output = b"".join(canonicalizer.feed(chunk) for chunk in raw_chunks) + canonicalizer.flush()
    events = _events(output)
    event_types = [event["type"] for event in events]

    assert event_types == [
        "response.created",
        "response.output_item.added",
        "response.function_call_arguments.done",
        "response.completed",
    ]
    assert events[0]["response"]["id"] == "resp_1"
    assert events[1]["item"]["type"] == "function_call"
    assert events[2]["arguments"] == '{"query":"cache"}'
    assert all(event_type not in event_types for event_type in ["response.output_text.done", "response.content_part.done"])


def test_sub2api_facing_stream_corrects_codex_tool_names_and_arguments() -> None:
    canonicalizer = ResponsesSSECanonicalizer(sub2api_bridge_compat=True)

    assert canonicalizer.feed(
        b'event: response.output_item.added\n'
        b'data: {"output_index":0,"item":{"type":"function_call","id":"fc_1","call_id":"call_1","name":"apply_patch"}}\n\n'
    ) == b""
    output = canonicalizer.feed(
        b'event: response.function_call_arguments.done\n'
        b'data: {"output_index":0,"arguments":"{\\"file_path\\":\\"a.txt\\",\\"old_string\\":\\"a\\",\\"new_string\\":\\"b\\",\\"replace_all\\":true}"}\n\n'
    )

    events = _events(output)
    assert events[0]["item"]["name"] == "edit"
    assert json.loads(events[1]["arguments"]) == {
        "filePath": "a.txt",
        "oldString": "a",
        "newString": "b",
        "replaceAll": True,
    }


def test_sub2api_facing_stream_corrects_bash_workdir_argument() -> None:
    canonicalizer = ResponsesSSECanonicalizer(sub2api_bridge_compat=True)

    assert canonicalizer.feed(
        b'event: response.output_item.added\n'
        b'data: {"output_index":0,"item":{"type":"function_call","id":"fc_1","call_id":"call_1","name":"execute_bash"}}\n\n'
    ) == b""
    output = canonicalizer.feed(
        b'event: response.function_call_arguments.done\n'
        b'data: {"output_index":0,"arguments":"{\\"command\\":\\"pwd\\",\\"work_dir\\":\\"/tmp\\"}"}\n\n'
    )

    events = _events(output)
    assert events[0]["item"]["name"] == "bash"
    assert json.loads(events[1]["arguments"]) == {"command": "pwd", "workdir": "/tmp"}
