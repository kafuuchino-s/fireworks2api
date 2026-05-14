from __future__ import annotations

from types import SimpleNamespace

from app.dataplane.fireworks.route_trace import (
    build_route_transform_trace,
    complete_route_transform_trace,
    derive_capability_tags,
    sanitize_field_actions,
)


def _context(**overrides):
    base = SimpleNamespace(
        body={
            "tools": [{"type": "function"}],
            "stream": True,
            "thinking": {"enabled": True},
            "reasoning": {"effort": "low"},
            "prompt_cache_key": "secret-cache-key",
            "images": ["data:image/png;base64,AAAA"],
            "mcp": {"server": "example"},
        },
        model_name="alias-model",
        resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/upstream-model"),
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_derive_capability_tags_detects_expected_features() -> None:
    tags = derive_capability_tags(_context())
    assert tags == ("function", "image", "mcp", "prompt_cache", "reasoning", "sse", "thinking", "tools")


def test_derive_capability_tags_adds_endpoint_base_tag() -> None:
    assert "responses" in derive_capability_tags(_context(body={}), endpoint="responses:responses")
    assert "chat" in derive_capability_tags(_context(body={}), endpoint="chat_completions:chat/completions")
    assert "responses:lifecycle" in derive_capability_tags(_context(body={}), endpoint="responses_lifecycle:responses/{id}")


def test_derive_capability_tags_splits_cross_endpoint_fallback() -> None:
    tags = set(derive_capability_tags(_context(body={"input": "hello", "service_tier": "priority"}), endpoint="cross_endpoint_fallback/priority:chat_completions"))

    assert {"responses", "priority", "chat_completions", "cross_endpoint_fallback"}.issubset(tags)
    assert "cross_endpoint_fallback/priority" not in tags


def test_sanitize_field_actions_preserves_only_safe_values() -> None:
    actions = sanitize_field_actions(
        [
            {"field": "model", "action": "map", "to": "upstream", "reason": "alias"},
            {"field": "prompt", "action": "drop", "to": "[REDACTED]", "reason": "contains prompt"},
            {"field": "api_key", "action": "keep", "value": "secret", "raw": "nope"},
            "ignore-me",
        ]
    )

    assert actions == (
        {"field": "model", "action": "map", "to": "upstream", "reason": "alias"},
        {"field": "prompt", "action": "drop", "to": "[REDACTED]", "reason": "contains prompt"},
        {"field": "api_key", "action": "keep"},
    )


def test_sanitize_field_actions_drops_unknown_values() -> None:
    actions = sanitize_field_actions([
        {"field": "unknown", "action": "transform", "to": "weird", "reason": "x", "extra": "y"},
    ])

    assert actions == ({"field": "unknown", "reason": "x"},)


def test_build_route_transform_trace_redacts_sensitive_inputs() -> None:
    trace = build_route_transform_trace(
        _context(),
        public_route="/v1/chat/completions",
        adapter="chat",
        fireworks_endpoint="chat_completions:chat/completions",
        request_shape={
            "payload_field_names": ["messages", "model", "prompt", "image_url", "base64", "tool_calls", "api_key"],
            "forwarded_headers": {
                "x-request-id": "1",
                "x-fireworks-request-id": "2",
                "x-session-affinity": "drop",
                "authorization": "Bearer secret",
                "x-ratelimit-limit-requests": "10",
            },
        },
        field_actions=[{"field": "model", "action": "map", "to": "upstream", "reason": "alias"}],
        routing_metadata={
            "stable_key_source": "session",
            "stable_key_hash_value": "hash123",
            "affinity_header": "aff",
            "routing_mode": "account_aware_sticky",
            "primary_account_bucket": "account:acct-a",
            "selected_account_count": 2,
            "skipped_account_count": 1,
            "selected_key_count": 3,
            "route_key": "hidden",
        },
    )

    assert trace["public_route"] == "/v1/chat/completions"
    assert trace["model_alias"] == "alias-model"
    assert trace["upstream_model"] == "accounts/fireworks/models/upstream-model"
    assert "chat" in trace["capability_tags"]
    assert trace["request_shape"]["payload_field_names"] == ("api_key", "base64", "image_url", "messages", "model", "prompt", "tool_calls")
    assert trace["request_shape"]["forwarded_header_names"] == ("x-fireworks-request-id", "x-ratelimit-limit-requests", "x-request-id")
    assert trace["field_actions"] == ({"field": "model", "action": "map", "to": "upstream", "reason": "alias"},)
    assert trace["routing"] == {
        "routing_mode": "account_aware_sticky",
        "selected_account_count": 2,
        "primary_account_bucket": "account:acct-a",
        "skipped_account_count": 1,
        "stable_key_source": "session",
        "stable_key_hash_value": "hash123",
        "affinity_header": "aff",
        "selected_key_count": 3,
    }
    assert "route_key" not in trace["routing"]
    assert "prompt" in trace["request_shape"]["payload_field_names"]
    assert "authorization" not in trace["request_shape"]["forwarded_header_names"]


def test_complete_route_transform_trace_adds_result() -> None:
    trace = build_route_transform_trace(_context(), public_route="/v1/responses", adapter="responses", fireworks_endpoint="responses")
    completed = complete_route_transform_trace(trace, result={"status": "ok"})

    assert completed["result"] == {"status": "ok"}
    assert completed["public_route"] == "/v1/responses"
