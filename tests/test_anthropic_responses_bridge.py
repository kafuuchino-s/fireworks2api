from __future__ import annotations

import pytest

from app.products.anthropic.responses_bridge import (
    ResponsesToAnthropicStreamAdapter,
    build_responses_bridge_payload,
    trim_responses_input_to_latest_turn,
)


def test_build_responses_bridge_payload_maps_messages_tools_and_defaults() -> None:
    payload, report = build_responses_bridge_payload(
        {
            "system": [{"type": "text", "text": "Be concise."}],
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": [{"type": "text", "text": "use tool"}, {"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {"q": "x"}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "done"}]},
            ],
            "tools": [{"name": "lookup", "description": "search", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "lookup"},
            "output_config": {"effort": "high"},
            "max_tokens": 64,
        },
        "accounts/fireworks/models/test",
    )

    assert payload["model"] == "accounts/fireworks/models/test"
    assert payload["stream"] is True
    assert payload["store"] is True
    assert payload["parallel_tool_calls"] is True
    assert payload["include"] == ["reasoning.encrypted_content"]
    assert payload["text"] == {"verbosity": "medium"}
    assert payload["reasoning"] == {"effort": "high", "summary": "auto"}
    assert payload["instructions"] == "Be concise."
    assert payload["max_output_tokens"] == 128
    assert payload["tool_choice"] == {"type": "function", "name": "lookup"}
    assert payload["tools"][0]["name"] == "lookup"
    assert payload["input"][0] == {"role": "user", "content": "hello"}
    assert payload["input"][1]["content"][1]["type"] == "function_call"
    assert payload["input"][2]["content"][0]["type"] == "function_call_output"
    assert report["tool_choice"] == {"type": "function", "name": "lookup"}


@pytest.mark.parametrize(
    ("upstream_model", "effort", "expected"),
    [
        ("accounts/fireworks/models/glm-5p1", "max", "high"),
        ("accounts/fireworks/routers/glm-5p1-fast", "xhigh", "high"),
        ("accounts/fireworks/models/minimax-m2p7", "max", "high"),
        ("accounts/fireworks/models/deepseek-v4-pro", "max", "max"),
        ("accounts/fireworks/models/kimi-k2p6", "xhigh", "xhigh"),
    ],
)
def test_build_responses_bridge_payload_normalizes_reasoning_effort(
    upstream_model: str,
    effort: str,
    expected: str,
) -> None:
    payload, report = build_responses_bridge_payload(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "output_config": {"effort": effort},
            "max_tokens": 512,
        },
        upstream_model,
    )

    assert payload["reasoning"]["effort"] == expected
    changed = expected != effort
    assert any(change["field"] == "reasoning.effort" for change in report["field_changes"]) is changed


def test_trim_responses_input_to_latest_turn_keeps_latest_and_following_outputs() -> None:
    payload = {
        "previous_response_id": "resp_1",
        "input": [
            {"role": "user", "content": "old"},
            {"type": "function_call_output", "call_id": "call_1", "output": "done"},
            {"type": "function_call_output", "call_id": "call_2", "output": "done2"},
        ],
    }

    trimmed = trim_responses_input_to_latest_turn(payload)

    assert trimmed["input"] == [
        {"role": "user", "content": "old"},
        {"type": "function_call_output", "call_id": "call_1", "output": "done"},
        {"type": "function_call_output", "call_id": "call_2", "output": "done2"},
    ]


def test_responses_to_anthropic_stream_adapter_converts_text_and_tool_calls() -> None:
    adapter = ResponsesToAnthropicStreamAdapter()

    text_events = adapter.feed({"type": "response.output_text.delta", "output_index": 0, "delta": "hello"})
    tool_events = adapter.feed({"type": "response.function_call_arguments.delta", "output_index": 1, "name": "lookup", "call_id": "call_1", "delta": '{"q":"x"}'})
    final_events = adapter.feed({"type": "response.completed", "response": {"usage": {"input_tokens": 3, "output_tokens": 2}}})

    assert text_events[0]["type"] == "message_start"
    assert text_events[-1]["type"] == "content_block_delta"
    assert text_events[-1]["delta"] == {"type": "text_delta", "text": "hello"}
    assert tool_events[-1]["type"] == "content_block_delta"
    assert tool_events[-1]["delta"]["type"] == "input_json_delta"
    assert final_events[-1]["type"] == "message_stop"


def test_responses_to_anthropic_stream_adapter_starts_and_stops_blocks() -> None:
    adapter = ResponsesToAnthropicStreamAdapter()

    start_events = adapter.feed({"type": "response.output_text.delta", "output_index": 0, "delta": "hello"})
    stop_events = adapter.feed({"type": "response.output_item.done", "output_index": 0, "item": {"type": "message", "id": "msg_1"}})

    assert start_events[0]["type"] == "message_start"
    assert start_events[1]["type"] == "content_block_start"
    assert stop_events[0]["type"] == "content_block_stop"
