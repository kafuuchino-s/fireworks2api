from __future__ import annotations

from types import SimpleNamespace

from app.dataplane.fireworks.route_trace import build_route_transform_trace, complete_route_transform_trace


PROMPT_SECRET_SENTINEL = "PROMPT_SECRET_SENTINEL"
IMAGE_URL_SECRET_SENTINEL = "IMAGE_URL_SECRET_SENTINEL"
BASE64_SECRET_SENTINEL = "BASE64_SECRET_SENTINEL"
TOOL_ARGUMENT_SECRET_SENTINEL = "TOOL_ARGUMENT_SECRET_SENTINEL"
AUTH_SECRET_SENTINEL = "AUTH_SECRET_SENTINEL"
FW_KEY_SECRET_SENTINEL = "FW_KEY_SECRET_SENTINEL"


def _context(*, model_name: str, upstream_model: str, body: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        body=body,
        model_name=model_name,
        resolved_model=SimpleNamespace(upstream_model=upstream_model),
    )


def _assert_trace_safe(trace: dict[str, object], *, public_route: str, adapter: str, endpoint: str, model_alias: str, upstream_model: str, tags: tuple[str, ...]) -> None:
    serialized = repr(trace)

    for sentinel in (
        PROMPT_SECRET_SENTINEL,
        IMAGE_URL_SECRET_SENTINEL,
        BASE64_SECRET_SENTINEL,
        TOOL_ARGUMENT_SECRET_SENTINEL,
        AUTH_SECRET_SENTINEL,
        FW_KEY_SECRET_SENTINEL,
    ):
        assert sentinel not in serialized

    assert trace["public_route"] == public_route
    assert trace["adapter"] == adapter
    assert trace["fireworks_endpoint"] == endpoint
    assert trace["model_alias"] == model_alias
    assert trace["upstream_model"] == upstream_model
    assert trace["capability_tags"] == tags
    assert "request_shape" in trace
    assert "field_actions" in trace


def test_route_transform_trace_redacts_chat_payload() -> None:
    trace = build_route_transform_trace(
        _context(
            model_name="alias-chat",
            upstream_model="accounts/fireworks/models/chat-upstream",
            body={
                "messages": [
                    {"role": "user", "content": PROMPT_SECRET_SENTINEL},
                    {"role": "user", "content": [{"type": "image_url", "image_url": {"url": IMAGE_URL_SECRET_SENTINEL}}]},
                ],
                "stream": True,
                "tools": [{"type": "function", "function": {"name": "tool", "arguments": TOOL_ARGUMENT_SECRET_SENTINEL}}],
                "authorization": AUTH_SECRET_SENTINEL,
                "api_key": FW_KEY_SECRET_SENTINEL,
            },
        ),
        public_route="/v1/chat/completions",
        adapter="chat",
        fireworks_endpoint="chat/completions",
        request_shape={
            "payload_field_names": ["messages", "stream", "tools", "authorization", "api_key"],
            "forwarded_headers": {"x-request-id": "rid-1", "authorization": AUTH_SECRET_SENTINEL},
        },
        field_actions=[{"field": "messages", "action": "preserve", "to": "upstream", "reason": "chat"}],
        routing_metadata={"stable_key_source": "model", "stable_key_hash_value": "hash-chat", "affinity_header": "x-session-affinity", "route_key": "hidden"},
    )

    _assert_trace_safe(
        trace,
        public_route="/v1/chat/completions",
        adapter="chat",
        endpoint="chat/completions",
        model_alias="alias-chat",
        upstream_model="accounts/fireworks/models/chat-upstream",
        tags=("chat", "function", "sse", "tools"),
    )
    assert trace["request_shape"]["payload_field_names"] == ("api_key", "authorization", "messages", "stream", "tools")
    assert trace["request_shape"]["forwarded_header_names"] == ("x-request-id",)
    assert trace["routing"] == {"stable_key_source": "model", "stable_key_hash_value": "hash-chat", "affinity_header": "x-session-affinity"}


def test_route_transform_trace_redacts_responses_payload() -> None:
    trace = build_route_transform_trace(
        _context(
            model_name="alias-responses",
            upstream_model="accounts/fireworks/models/responses-upstream",
            body={
                "input": PROMPT_SECRET_SENTINEL,
                "stream": True,
                "reasoning": {"effort": "low"},
            },
        ),
        public_route="/v1/responses",
        adapter="responses",
        fireworks_endpoint="responses",
        request_shape={"payload_field_names": ["input", "stream", "reasoning"], "forwarded_headers": {"x-fireworks-request-id": "rid-2"}},
        field_actions=[{"field": "input", "action": "keep", "reason": "safe"}],
    )

    _assert_trace_safe(
        trace,
        public_route="/v1/responses",
        adapter="responses",
        endpoint="responses",
        model_alias="alias-responses",
        upstream_model="accounts/fireworks/models/responses-upstream",
        tags=("reasoning", "responses", "sse"),
    )


def test_route_transform_trace_redacts_mcp_headers() -> None:
    trace = build_route_transform_trace(
        _context(
            model_name="alias-responses",
            upstream_model="accounts/fireworks/models/responses-upstream",
            body={
                "input": "hello",
                "tools": [
                    {
                        "type": "mcp",
                        "server_url": "https://example.com",
                        "server_label": "example",
                        "headers": {"Authorization": "secret-token", "X-Token": "another-secret"},
                        "allowed_tools": ["tool_a"],
                        "require_approval": True,
                    }
                ],
            },
        ),
        public_route="/v1/responses",
        adapter="responses",
        fireworks_endpoint="responses",
        request_shape={"payload_field_names": ["input", "tools"], "forwarded_headers": {"x-fireworks-request-id": "rid-2"}},
        field_actions=[{"field": "tools", "action": "keep", "reason": "safe"}],
    )

    serialized = repr(trace)
    assert "secret-token" not in serialized
    assert "another-secret" not in serialized
    _assert_trace_safe(
        trace,
        public_route="/v1/responses",
        adapter="responses",
        endpoint="responses",
        model_alias="alias-responses",
        upstream_model="accounts/fireworks/models/responses-upstream",
        tags=("function", "mcp", "responses", "tools"),
    )


def test_route_transform_trace_redacts_completions_payload() -> None:
    trace = build_route_transform_trace(
        _context(
            model_name="alias-completions",
            upstream_model="accounts/fireworks/models/completions-upstream",
            body={"prompt": PROMPT_SECRET_SENTINEL, "stream": False},
        ),
        public_route="/v1/completions",
        adapter="completions",
        fireworks_endpoint="completions",
        request_shape={"payload_field_names": ["prompt", "stream"], "forwarded_headers": {"x-cache": "hit"}},
    )

    _assert_trace_safe(
        trace,
        public_route="/v1/completions",
        adapter="completions",
        endpoint="completions",
        model_alias="alias-completions",
        upstream_model="accounts/fireworks/models/completions-upstream",
        tags=("completions",),
    )


def test_route_transform_trace_redacts_anthropic_payload() -> None:
    trace = build_route_transform_trace(
        _context(
            model_name="alias-anthropic",
            upstream_model="accounts/fireworks/models/anthropic-upstream",
            body={
                "messages": [{"role": "user", "content": PROMPT_SECRET_SENTINEL}],
                "tools": [{"name": "tool", "input_schema": {"secret": TOOL_ARGUMENT_SECRET_SENTINEL}}],
                "stream": True,
            },
        ),
        public_route="/v1/messages",
        adapter="anthropic",
        fireworks_endpoint="messages",
        request_shape={"payload_field_names": ["messages", "tools", "stream"], "forwarded_headers": {"x-ratelimit-limit-tokens": "1", "authorization": AUTH_SECRET_SENTINEL}},
    )

    completed = complete_route_transform_trace(trace, result={"status": "ok", "request_id": "req-1"})

    _assert_trace_safe(
        completed,
        public_route="/v1/messages",
        adapter="anthropic",
        endpoint="messages",
        model_alias="alias-anthropic",
        upstream_model="accounts/fireworks/models/anthropic-upstream",
        tags=("anthropic_messages", "function", "sse", "tools"),
    )
    assert completed["result"] == {"status": "ok", "request_id": "req-1"}
