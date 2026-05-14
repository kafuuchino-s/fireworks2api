from scripts.fireworks_inference_smoke import (
    collect_sse_events,
    extract_anthropic_tool_use,
    extract_anthropic_tool_use_id,
    extract_anthropic_text_preview,
    extract_responses_output_items,
    extract_responses_text,
    extract_responses_tool_calls,
    extract_tool_call_id,
    parse_csv_env,
    is_error_event,
    is_terminal_event,
    parse_sse_event_data,
)


def test_extract_responses_helpers_and_tool_ids():
    payload = {
        "id": "resp_1",
        "output": [
            {"type": "function_call", "id": "call_1", "name": "calculator", "arguments": "{}"},
            {"type": "message", "content": [{"type": "output_text", "text": "The answer is 42."}]},
        ],
    }
    items = extract_responses_output_items(payload)
    assert len(items) == 2
    assert extract_tool_call_id(items[0]) == "call_1"
    assert extract_responses_tool_calls(payload)[0]["id"] == "call_1"
    assert extract_responses_text(payload) == "The answer is 42."


def test_extract_anthropic_preview_and_sse_helpers():
    anthropic = {"content": [{"type": "text", "text": "Hello from Anthropic."}]}
    assert extract_anthropic_text_preview(anthropic) == "Hello from Anthropic."

    events = collect_sse_events(
        iter(
            [
                "event: response.output_text.delta\n",
                'data: {"type":"response.output_text.delta","text":"hel"}\n',
                "\n",
                "event: done\n",
                'data: {"type":"response.completed"}\n',
                "\n",
            ]
        )
    )
    assert parse_sse_event_data(events[0])["text"] == "hel"
    assert not is_error_event(events[0])
    assert is_terminal_event(events[1])


def test_error_event_detection():
    assert is_error_event({"data": '{"type":"error","message":"boom"}'})
    assert not is_terminal_event({"data": '{"type":"error","message":"boom"}'})


def test_collect_sse_events_handles_multiline_data():
    events = collect_sse_events(iter(["data: line1\n", "data: line2\n", "\n"]))
    assert events == [{"data": "line1\nline2"}]


def test_responses_image_smoke_default_shape_is_image():
    from scripts import fireworks_inference_smoke as smoke

    assert smoke.env("FIREWORKS2API_RESPONSES_IMAGE_SHAPE", "image") == "image"


def test_extract_anthropic_tool_use_helpers_and_csv_env(monkeypatch):
    payload = {"content": [{"type": "tool_use", "id": "tool_1", "name": "calculator", "input": {}}]}
    tool_uses = extract_anthropic_tool_use(payload)
    assert len(tool_uses) == 1
    assert extract_anthropic_tool_use_id(tool_uses[0]) == "tool_1"

    monkeypatch.setenv("FIREWORKS2API_MCP_SERVER_URLS", " http://a , ,http://b ")
    assert parse_csv_env("FIREWORKS2API_MCP_SERVER_URLS") == ["http://a", "http://b"]
