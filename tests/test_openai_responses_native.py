from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse
from app.products.openai.errors import OpenAIRequestError

from app.main import app
import app.products.openai.fireworks_native.responses as native_responses


client = TestClient(app)


@pytest.mark.parametrize(
    ("body", "param"),
    [
        ({"model": "test", "input": 1}, "input"),
        ({"model": "test", "input": []}, "input"),
        ({"model": "test", "input": "hello", "service_tier": "priority"}, "service_tier"),
        ({"model": "test", "input": "hello", "tools": [{}]}, "tools[0].type"),
        ({"model": "test", "input": "hello", "tools": [{"type": "function"}]}, "tools[0].name"),
        ({"model": "test", "input": "hello", "previous_response_id": ""}, "previous_response_id"),
        ({"model": "test", "input": "hello", "tool_choice": 1}, "tool_choice"),
        ({"model": "test", "input": "hello", "tool_choice": {"type": 1}}, "tool_choice.type"),
        ({"model": "test", "input": "hello", "tools": [{"type": "function", "function": {"name": "lookup", "extra": True}}]}, "tools[0].function.extra"),
        ({"model": "test", "input": "hello", "tools": [{"type": "function", "name": "lookup", "parameters": []}]}, "tools[0].parameters"),
        ({"model": "test", "input": "hello", "tools": [{"type": "mcp"}]}, "tools[0].server_url"),
        ({"model": "test", "input": "hello", "tools": [{"type": "mcp", "server_url": ""}]}, "tools[0].server_url"),
        ({"model": "test", "input": "hello", "tools": [{"type": "mcp", "server_url": "https://example.com", "extra": True}]}, "tools[0].extra"),
        ({"model": "test", "input": "hello", "tools": [{"type": "mcp", "server_url": "https://example.com", "headers": [1]}]}, "tools[0].headers"),
        ({"model": "test", "input": "hello", "tools": [{"type": "mcp", "server_url": "https://example.com", "headers": {"Authorization": 1}}]}, "tools[0].headers.Authorization"),
        ({"model": "test", "input": "hello", "tools": [{"type": "mcp", "server_url": "https://example.com", "allowed_tools": []}]}, "tools[0].allowed_tools"),
        ({"model": "test", "input": "hello", "tools": [{"type": "sse"}]}, "tools[0].server_url"),
        ({"model": "test", "input": "hello", "tools": [{"type": "python", "name": ""}]}, "tools[0].name"),
        ({"model": "test", "input": "hello", "user": 1}, "user"),
    ],
)
def test_validate_responses_body_rejects_invalid_native_fields(body, param) -> None:
    with pytest.raises(OpenAIRequestError) as exc:
        native_responses.validate_responses_body(body)

    assert isinstance(exc.value, OpenAIRequestError)


def test_validate_responses_body_accepts_minimal_native_tools() -> None:
    native_responses.validate_responses_body(
        {
            "model": "test",
            "input": [{"role": "user", "content": "hello"}],
            "stream_options": {"include_usage": True},
            "tools": [
                {"type": "function", "function": {"name": "lookup", "description": "d", "parameters": {}, "schema": {}, "strict": True}},
                {"type": "function", "name": "lookup", "description": "d", "parameters": {}, "strict": True},
                {"type": "mcp", "server_url": "https://example.com", "label": "l", "name": "n"},
                {"type": "mcp", "server_url": "https://example.com", "server_label": "dmcp", "server_description": "desc", "allowed_tools": ["tool_a"], "headers": {"Authorization": "secret"}, "require_approval": "never"},
                {"type": "sse", "server_url": "https://example.com"},
                {"type": "python"},
                {"type": "web_search"},
            ],
        }
    )


def test_validate_responses_body_accepts_sub2api_anthropic_bridge_payload() -> None:
    native_responses.validate_responses_body(
        {
            "model": "kimi-k2.6",
            "input": [
                {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "system prompt"}]},
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "look"}, {"type": "input_image", "image_url": "data:image/png;base64,AAAA"}]},
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "I will call a tool."}]},
                {"type": "function_call", "call_id": "toolu_123", "name": "Read", "arguments": "{\"file_path\":\"README.md\"}"},
                {"type": "function_call_output", "call_id": "toolu_123", "output": "done"},
            ],
            "tools": [{"type": "function", "name": "Read", "parameters": {"type": "object", "properties": {}}, "strict": False}],
            "include": ["reasoning.encrypted_content"],
            "store": False,
            "parallel_tool_calls": True,
            "text": {"verbosity": "medium"},
            "reasoning": {"effort": "medium", "summary": "auto"},
        }
    )


def test_validate_responses_body_accepts_sub2api_chat_bridge_payload() -> None:
    native_responses.validate_responses_body(
        {
            "model": "kimi-k2.6",
            "instructions": "global instruction",
            "input": [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": [{"type": "input_text", "text": "describe"}, {"type": "input_image", "image_url": "data:image/png;base64,AAAA"}]},
                {"role": "assistant", "content": [{"type": "output_text", "text": "I will call ping."}]},
                {"type": "function_call", "call_id": "call_1", "name": "ping", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call_1", "output": "pong"},
            ],
            "tools": [{"type": "function", "name": "ping", "parameters": {"type": "object", "properties": {}}, "strict": True}],
            "tool_choice": {"type": "function", "name": "ping"},
            "include": ["reasoning.encrypted_content"],
            "store": False,
            "stream": True,
        }
    )


def test_validate_responses_body_accepts_sub2api_empty_text_placeholder() -> None:
    native_responses.validate_responses_body(
        {
            "model": "kimi-k2.6",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": ""},
                        {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                    ],
                }
            ],
            "include": ["reasoning.encrypted_content"],
        }
    )


def test_validate_responses_body_rejects_plain_empty_text_part() -> None:
    with pytest.raises(OpenAIRequestError) as exc:
        native_responses.validate_responses_body(
            {
                "model": "test",
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": ""},
                            {"type": "text", "text": "hello"},
                        ],
                    }
                ],
            }
        )

    assert exc.value.param == "input[0].content[0].text"


def test_validate_responses_body_rejects_all_empty_sub2api_text_parts() -> None:
    with pytest.raises(OpenAIRequestError) as exc:
        native_responses.validate_responses_body(
            {
                "model": "test",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": ""}]}],
                "include": ["reasoning.encrypted_content"],
            }
        )

    assert exc.value.param == "input[0].content"


def test_validate_responses_body_rejects_invalid_mcp_require_approval_type() -> None:
    with pytest.raises(OpenAIRequestError) as exc:
        native_responses.validate_responses_body(
            {"model": "test", "input": "hello", "tools": [{"type": "mcp", "server_url": "https://example.com", "require_approval": 1}]}
        )

    assert exc.value.param == "tools[0].require_approval"


def test_validate_responses_body_rejects_non_boolean_function_tool_strict() -> None:
    with pytest.raises(OpenAIRequestError) as exc:
        native_responses.validate_responses_body(
            {"model": "test", "input": "hello", "tools": [{"type": "function", "name": "lookup", "parameters": {}, "strict": "true"}]}
        )

    assert exc.value.param == "tools[0].strict"


def test_validate_responses_body_accepts_text_message_list() -> None:
    native_responses.validate_responses_body(
        {"model": "test", "input": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]}
    )


def test_validate_responses_body_accepts_official_typed_message_item() -> None:
    native_responses.validate_responses_body(
        {"model": "test", "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}]}
    )


def test_validate_responses_body_accepts_output_text_continuation_parts() -> None:
    native_responses.validate_responses_body(
        {"model": "test", "input": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "prior answer"}]}]}
    )


def test_validate_responses_body_accepts_output_text_continuation_item() -> None:
    native_responses.validate_responses_body(
        {"model": "test", "input": [{"type": "output_text", "text": "prior answer"}]}
    )


def test_validate_responses_body_accepts_codex_reasoning_input_item() -> None:
    native_responses.validate_responses_body(
        {
            "model": "test",
            "input": [
                {"type": "reasoning", "id": "rs_1", "summary": []},
                {"role": "user", "content": "continue"},
            ],
        }
    )


def test_validate_responses_body_accepts_valid_input_image_part() -> None:
    native_responses.validate_responses_body(
        {
            "model": "test",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "describe this"},
                        {"type": "input_image", "image_url": {"url": "https://example.com/cat.png", "detail": "high"}},
                    ],
                }
            ],
        }
    )


@pytest.mark.parametrize(
    ("body", "param"),
    [
        ({"model": "test", "input": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}}]}]}, "input[0].content[0].type"),
        ({"model": "test", "input": [{"role": "user", "content": [{"type": "input_image", "image_url": {}}]}]}, "input[0].content[0].image_url.url"),
        ({"model": "test", "input": [{"role": "user", "content": [{"type": "input_image", "image_url": {"url": "http://example.com/cat.png"}}]}]}, "input[0].content[0].image_url.url"),
        ({"model": "test", "input": [{"role": "user", "content": [{"type": "unsupported", "text": "x"}]}]}, "input[0].content[0].type"),
    ],
)
def test_validate_responses_body_rejects_invalid_multimodal_input(body, param) -> None:
    with pytest.raises(OpenAIRequestError) as exc:
        native_responses.validate_responses_body(body)

    assert exc.value.param == param


def test_validate_responses_body_accepts_sse_legacy_url() -> None:
    native_responses.validate_responses_body(
        {"model": "test", "input": "hello", "tools": [{"type": "sse", "url": "https://example.com"}]}
    )


def test_validate_responses_body_accepts_tool_output_continuation() -> None:
    native_responses.validate_responses_body(
        {"model": "test", "input": [{"type": "tool_output", "tool_call_id": "call_1", "output": "done"}]}
    )


def test_validate_responses_body_accepts_function_call_output_continuation() -> None:
    native_responses.validate_responses_body(
        {"model": "test", "input": [{"type": "function_call_output", "call_id": "call_1", "output": "done"}]}
    )


@pytest.mark.parametrize(
    ("body", "param"),
    [
        ({"model": "test", "input": [{"type": "tool_output", "tool_call_id": "", "output": "done"}]}, "input[0].tool_call_id"),
        ({"model": "test", "input": [{"type": "tool_output", "output": "done"}]}, "input[0].tool_call_id"),
    ],
)
def test_validate_responses_body_rejects_malformed_tool_output(body, param) -> None:
    with pytest.raises(OpenAIRequestError) as exc:
        native_responses.validate_responses_body(body)

    assert exc.value.param == param


def test_build_responses_adapter_normalizes_official_image_and_function_outputs() -> None:
    context = SimpleNamespace(
        body={
            "model": "test",
            "input": [
                {"type": "message", "role": "user", "content": [{"type": "input_image", "image_url": {"url": "https://example.com/cat.png", "detail": "high"}}]},
                {"type": "function_call_output", "call_id": "call_1", "output": "done"},
            ],
        },
        settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret"),
        request_headers={},
        stable_key="stable",
        resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
    )

    payload, _, report = native_responses.build_responses_adapter(context)

    assert "type" not in payload["input"][0]
    assert payload["input"][0]["role"] == "user"
    assert payload["input"][0]["content"][0] == {"type": "image", "image_url": {"url": "https://example.com/cat.png", "detail": "high"}}
    assert payload["input"][1] == {"type": "function_call_output", "call_id": "call_1", "output": "done"}


def test_build_responses_adapter_normalizes_output_text_continuation() -> None:
    context = SimpleNamespace(
        body={
            "model": "test",
            "input": [
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "prior answer"}]},
                {"type": "output_text", "text": "top level prior answer"},
            ],
        },
        settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret"),
        request_headers={},
        stable_key="stable",
        resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
    )

    payload, _, report = native_responses.build_responses_adapter(context)

    assert payload["input"][0] == {"role": "assistant", "content": [{"type": "input_text", "text": "prior answer"}]}
    assert payload["input"][1] == {"role": "assistant", "content": [{"type": "input_text", "text": "top level prior answer"}]}


def test_build_responses_adapter_drops_codex_reasoning_input_items() -> None:
    context = SimpleNamespace(
        body={
            "model": "test",
            "input": [
                {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "system"}]},
                {"type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": "hidden"}]},
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "continue"}]},
            ],
        },
        settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret"),
        request_headers={},
        stable_key="stable",
        resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
    )

    payload, _, report = native_responses.build_responses_adapter(context)

    assert payload["input"] == [
        {"role": "developer", "content": [{"type": "input_text", "text": "system"}]},
        {"role": "user", "content": [{"type": "input_text", "text": "continue"}]},
    ]
    assert any(change["field"] == "input" and change["type"] == "reasoning" for change in report["field_changes"])


def test_build_responses_adapter_normalizes_sub2api_bridge_payload() -> None:
    context = SimpleNamespace(
        body={
            "model": "test",
            "input": [
                {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "system prompt"}]},
                {"type": "message", "role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64,AAAA"}]},
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "prior"}]},
                {"type": "function_call", "call_id": "toolu_123", "name": "Read", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "toolu_123", "output": "done"},
            ],
        },
        settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret"),
        request_headers={},
        stable_key="stable",
        resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
    )

    payload, _, report = native_responses.build_responses_adapter(context)

    assert payload["input"][0] == {"role": "developer", "content": [{"type": "input_text", "text": "system prompt"}]}
    assert payload["input"][1] == {"role": "user", "content": [{"type": "image", "image_url": {"url": "data:image/png;base64,AAAA"}}]}
    assert payload["input"][2] == {"role": "assistant", "content": [{"type": "input_text", "text": "prior"}]}
    assert payload["input"][3] == {"type": "function_call", "call_id": "toolu_123", "name": "Read", "arguments": "{}"}
    assert payload["input"][4] == {"type": "function_call_output", "call_id": "toolu_123", "output": "done"}


def test_build_responses_adapter_drops_sub2api_empty_text_placeholders() -> None:
    context = SimpleNamespace(
        body={
            "model": "test",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": " "},
                        {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                        {"type": "text", "text": "describe"},
                    ],
                }
            ],
            "include": ["reasoning.encrypted_content"],
        },
        settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret"),
        request_headers={},
        stable_key="stable",
        resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
    )

    payload, _, report = native_responses.build_responses_adapter(context)

    assert payload["input"][0] == {
        "role": "user",
        "content": [
            {"type": "image", "image_url": {"url": "data:image/png;base64,AAAA"}},
            {"type": "text", "text": "describe"},
        ],
    }
    assert any(
        change["field"] == "input.content" and change["type"] == "empty_text"
        for change in report["field_changes"]
    )


def test_build_responses_adapter_normalizes_sub2api_chat_bridge_payload() -> None:
    context = SimpleNamespace(
        body={
            "model": "test",
            "input": [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64,AAAA"}]},
                {"role": "assistant", "content": [{"type": "output_text", "text": "prior"}]},
                {"type": "function_call", "call_id": "call_1", "name": "ping", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call_1", "output": "pong"},
            ],
            "tool_choice": {"type": "function", "name": "ping"},
        },
        settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret"),
        request_headers={},
        stable_key="stable",
        resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
    )

    payload, _, report = native_responses.build_responses_adapter(context)

    assert payload["input"][0] == {"role": "system", "content": "system prompt"}
    assert payload["input"][1] == {"role": "user", "content": [{"type": "image", "image_url": {"url": "data:image/png;base64,AAAA"}}]}
    assert payload["input"][2] == {"role": "assistant", "content": [{"type": "input_text", "text": "prior"}]}
    assert payload["input"][3] == {"type": "function_call", "call_id": "call_1", "name": "ping", "arguments": "{}"}
    assert payload["input"][4] == {"type": "function_call_output", "call_id": "call_1", "output": "pong"}
    assert payload["tool_choice"] == {"type": "function", "function": {"name": "ping"}}
    assert any(change["field"] == "tool_choice" for change in report["field_changes"])


def test_build_responses_adapter_keeps_sub2api_reasoning_structured() -> None:
    context = SimpleNamespace(
        body={
            "model": "test",
            "input": "hello",
            "stream": True,
            "include": ["reasoning.encrypted_content"],
            "parallel_tool_calls": True,
            "store": False,
            "text": {"verbosity": "medium"},
        },
        settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret"),
        request_headers={},
        stable_key="stable",
        resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
    )

    payload, _, report = native_responses.build_responses_adapter(context)

    assert payload["store"] is True
    assert "fireworks2api_suppress_reasoning_stream" not in (payload.get("metadata") or {})
    assert not any(change["field"] == "reasoning" and change.get("action") == "stream_suppressed" for change in report["field_changes"])
    assert any(change["field"] == "store" and change["to"] is True for change in report["field_changes"])


@pytest.mark.parametrize(
    ("upstream_model", "effort", "expected"),
    [
        ("accounts/fireworks/models/minimax-m2p7", "xhigh", "high"),
        ("accounts/fireworks/models/minimax-m2p7", "max", "high"),
        ("accounts/fireworks/models/glm-5p1", "xhigh", "high"),
        ("accounts/fireworks/routers/glm-5p1-fast", "max", "high"),
        ("accounts/fireworks/models/deepseek-v4-pro", "xhigh", "xhigh"),
        ("accounts/fireworks/models/deepseek-v4-pro", "max", "max"),
        ("accounts/fireworks/models/kimi-k2p6", "xhigh", "xhigh"),
        ("accounts/fireworks/routers/kimi-k2p6-turbo", "max", "max"),
    ],
)
def test_build_responses_adapter_normalizes_reasoning_effort_for_fireworks_models(
    upstream_model: str,
    effort: str,
    expected: str,
) -> None:
    context = SimpleNamespace(
        body={
            "model": "test",
            "input": "hello",
            "stream": True,
            "include": ["reasoning.encrypted_content"],
            "parallel_tool_calls": True,
            "text": {"verbosity": "medium"},
            "reasoning": {"effort": effort, "summary": "auto"},
        },
        settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret"),
        request_headers={},
        stable_key="stable",
        resolved_model=SimpleNamespace(upstream_model=upstream_model),
    )

    payload, _, report = native_responses.build_responses_adapter(context)

    assert payload["reasoning"]["effort"] == expected
    changed = expected != effort
    assert any(change["field"] == "reasoning.effort" for change in report["field_changes"]) is changed


def test_build_responses_adapter_preserves_plain_store_false() -> None:
    context = SimpleNamespace(
        body={"model": "test", "input": "hello", "stream": True, "store": False},
        settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret"),
        request_headers={},
        stable_key="stable",
        resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
    )

    payload, _, report = native_responses.build_responses_adapter(context)

    assert payload["store"] is False
    assert not any(change["field"] == "store" for change in report["field_changes"])


def test_build_responses_adapter_forces_store_for_streaming_tool_requests() -> None:
    context = SimpleNamespace(
        body={
            "model": "test",
            "input": "call a tool",
            "stream": True,
            "store": False,
            "tools": [{"type": "function", "name": "Search", "parameters": {"type": "object"}}],
        },
        settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret"),
        request_headers={},
        stable_key="stable",
        resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
    )

    payload, _, report = native_responses.build_responses_adapter(context)

    assert payload["store"] is True
    assert any(change["field"] == "store" for change in report["field_changes"])


def test_build_responses_adapter_drops_replayed_function_call_for_previous_response_tool_result() -> None:
    context = SimpleNamespace(
        body={
            "model": "test",
            "previous_response_id": "resp_1",
            "input": [
                {"type": "function_call", "call_id": "call_1", "name": "Search", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call_1", "output": "done"},
            ],
        },
        settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret"),
        request_headers={},
        stable_key="stable",
        resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
    )

    payload, _, report = native_responses.build_responses_adapter(context)

    assert payload["input"] == [{"type": "function_call_output", "call_id": "call_1", "output": "done"}]
    assert any(change["field"] == "input" and change["reason"] == "previous_response_tool_replay" for change in report["field_changes"])


def test_validate_responses_body_rejects_empty_previous_response_id() -> None:
    with pytest.raises(OpenAIRequestError) as exc:
        native_responses.validate_responses_body({"model": "test", "input": "hello", "previous_response_id": " "})

    assert exc.value.param == "previous_response_id"


def test_build_responses_adapter_reports_nested_function_tool_warning() -> None:
    context = SimpleNamespace(
        body={"model": "test", "input": "hello", "tools": [{"type": "function", "function": {"name": "lookup"}}]},
        settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret"),
        request_headers={},
        stable_key="stable",
        resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
    )

    _, _, report = native_responses.build_responses_adapter(context)

    assert any("nested function tool shape" in warning for warning in report["warnings"])


def test_build_responses_adapter_preserves_mcp_fields_and_redacts_headers_from_trace() -> None:
    context = SimpleNamespace(
        body={
            "model": "test",
            "input": "hello",
            "tools": [
                {
                    "type": "mcp",
                    "server_url": "https://example.com",
                    "server_label": "example",
                    "allowed_tools": ["tool_a"],
                    "headers": {"Authorization": "secret-token", "X-Token": "another-secret"},
                    "require_approval": True,
                }
            ],
        },
        settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret"),
        request_headers={},
        stable_key="stable",
        resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
    )

    payload, _, report = native_responses.build_responses_adapter(context)

    tool = payload["tools"][0]
    assert tool["headers"] == {"Authorization": "secret-token", "X-Token": "another-secret"}
    assert tool["allowed_tools"] == ["tool_a"]
    assert tool["require_approval"] is True
    assert report["warnings"] == []

    from app.dataplane.fireworks.route_trace import build_route_transform_trace

    trace = build_route_transform_trace(
        context,
        public_route="POST /v1/responses",
        adapter="responses",
        fireworks_endpoint="responses:responses",
        request_shape={"payload_field_names": tuple(sorted(payload.keys())), "forwarded_headers": {"authorization": "Bearer token"}},
        payload={"tools": payload["tools"]},
        headers={"header_names": tuple(sorted({"authorization"}))},
    )

    serialized = repr(trace)
    assert "secret-token" not in serialized
    assert "another-secret" not in serialized


def test_build_responses_adapter_preserves_string_tool_choice() -> None:
    context = SimpleNamespace(
        body={"model": "test", "input": "hello", "tool_choice": "required"},
        settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret"),
        request_headers={},
        stable_key="stable",
        resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
    )

    payload, _, _ = native_responses.build_responses_adapter(context)

    assert payload["tool_choice"] == "required"


def test_validate_responses_body_accepts_required_string_tool_choice() -> None:
    native_responses.validate_responses_body({"model": "test", "input": "hello", "tool_choice": "required"})


def test_validate_responses_body_rejects_empty_tool_choice_string() -> None:
    with pytest.raises(OpenAIRequestError) as exc:
        native_responses.validate_responses_body({"model": "test", "input": "hello", "tool_choice": ""})

    assert exc.value.param == "tool_choice"


def test_validate_responses_body_accepts_tool_choice_object() -> None:
    native_responses.validate_responses_body(
        {"model": "test", "input": "hello", "tool_choice": {"type": "function", "function": {"name": "lookup"}}}
    )


def test_resolve_responses_upstream_path_handles_inference_prefixes() -> None:
    assert native_responses.resolve_responses_upstream_path("https://example.com/inference", "responses") == "v1/responses"
    assert native_responses.resolve_responses_upstream_path("https://example.com/inference/v1", "responses") == "responses"
