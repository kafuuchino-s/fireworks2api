from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.main import app
import app.platform.auth as auth
import app.products.openai.responses as responses_mod
from app.products.openai.responses_priority_fallback import synthesize_responses_from_chat


client = TestClient(app)


def _require_auth(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))


def _context(body: dict[str, object], upstream_model: str = "accounts/fireworks/models/test") -> SimpleNamespace:
    return SimpleNamespace(
        settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", upstream_base_url="https://api.fireworks.ai/inference", responses_cache_fields_enabled=True, request_log_retention=30),
        repository=SimpleNamespace(
            insert_request_log=lambda *args, **kwargs: None,
            get_response_key_route=lambda response_id: None,
            upsert_response_key_route=lambda response_id, key: None,
            delete_response_key_route=lambda response_id: None,
        ),
        body=body,
        model_name=body.get("model", "test"),
        resolved_model=SimpleNamespace(upstream_model=upstream_model),
        stable_key="stable",
        route_key="route",
        affinity_header="affinity",
        request_headers={"authorization": "Bearer token"},
        selected_keys=[SimpleNamespace(name="key-1", api_key="fw-test-key", fingerprint="fp-1")],
        routing_metadata={"stable_key_source": "session", "stable_key_hash_value": "hash123", "affinity_header": "aff"},
    )


def _chat_completion_body() -> dict[str, object]:
    return {
        "id": "chatcmpl_1",
        "object": "chat.completion",
        "choices": [{"message": {"content": "fallback text"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5, "prompt_tokens_details": {"cached_tokens": 1}},
    }


def _fallback_proxy_response(kwargs: dict[str, object]) -> JSONResponse:
    body = _chat_completion_body()
    transform = kwargs.get("response_transform")
    if callable(transform):
        body = transform(body)
    return JSONResponse(body)


def test_synthesize_responses_from_chat_keeps_reasoning_and_tool_calls_structured() -> None:
    response = synthesize_responses_from_chat(
        {
            "id": "chatcmpl_1",
            "choices": [
                {
                    "message": {
                        "reasoning_content": "plan",
                        "content": "answer",
                        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": "{\"q\":\"k\"}"}}],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        },
        model="deepseek-v4-pro",
        upstream_model="accounts/fireworks/models/deepseek-v4-pro",
        service_tier=None,
    )

    assert response["output"][0]["type"] == "reasoning"
    assert response["output"][1]["content"][0]["text"] == "answer"
    assert response["output"][2] == {
        "type": "function_call",
        "id": "fc_fallback_0_chatcmpl_1",
        "call_id": "call_1",
        "name": "lookup",
        "arguments": "{\"q\":\"k\"}",
        "status": "completed",
    }


def test_responses_priority_falls_back_to_chat_completions(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        return _context(body)

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        captured["context"] = context
        return _fallback_proxy_response(kwargs)

    def fake_build_route_transform_trace(*args, **kwargs):
        captured["route_trace"] = kwargs
        return {
            "public_route": kwargs["public_route"],
            "fireworks_endpoint": kwargs["fireworks_endpoint"],
            "cross_endpoint_fallback": True,
            "field_actions": kwargs["field_actions"],
            "warnings": kwargs["warnings"],
        }

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)
    monkeypatch.setattr(responses_mod, "build_route_transform_trace", fake_build_route_transform_trace)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={"model": "test", "input": "hello", "service_tier": "priority"},
    )

    assert response.status_code == 200
    assert response.json()["object"] == "response"
    assert response.json()["id"] == "resp_fallback_chatcmpl_1"
    assert response.json()["status"] == "completed"
    assert response.json()["output"][0]["content"][0]["text"] == "fallback text"
    assert response.json()["usage"]["input_tokens"] == 3
    assert response.json()["usage"]["output_tokens"] == 2
    assert response.json()["usage"]["input_tokens_details"]["cached_tokens"] == 1
    assert response.json()["store"] is False
    assert captured["upstream_path"] == "v1/chat/completions"
    assert captured["payload"]["messages"][0]["role"] == "user"
    assert captured["payload"]["messages"][0]["content"] == "hello"
    assert captured["payload"]["service_tier"] == "priority"
    assert "id" not in captured["payload"]
    assert "response_id" not in captured["payload"]
    assert captured["route_trace"]["public_route"] == "POST /v1/responses"
    assert "cross_endpoint_fallback" in captured["route_trace"]["fireworks_endpoint"]


def test_responses_priority_fallback_maps_and_forwards_fields(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        return _context(body)

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        return _fallback_proxy_response(kwargs)

    def fake_build_route_transform_trace(*args, **kwargs):
        captured["route_trace"] = kwargs
        return {
            "public_route": kwargs["public_route"],
            "fireworks_endpoint": kwargs["fireworks_endpoint"],
            "cross_endpoint_fallback": True,
            "field_actions": kwargs["field_actions"],
            "warnings": kwargs["warnings"],
        }

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)
    monkeypatch.setattr(responses_mod, "build_route_transform_trace", fake_build_route_transform_trace)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={
            "model": "test",
            "input": "hello",
            "service_tier": "priority",
            "max_output_tokens": 11,
            "instructions": "be brief",
            "prompt_cache_key": "cache-1",
            "prompt_cache_isolation_key": "iso-1",
        },
    )

    assert response.status_code == 200
    payload = captured["payload"]
    assert payload["max_tokens"] == 11
    assert payload["messages"][0] == {"role": "system", "content": "be brief"}
    assert payload["messages"][1] == {"role": "user", "content": "hello"}
    assert payload["prompt_cache_key"] == "cache-1"
    assert payload["prompt_cache_isolation_key"] == "iso-1"


def test_responses_priority_fallback_injects_reasoning_top_k_default(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        return _context(body, upstream_model="accounts/fireworks/models/deepseek-v4-pro")

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        return _fallback_proxy_response(kwargs)

    def fake_build_route_transform_trace(*args, **kwargs):
        captured["route_trace"] = kwargs
        return {
            "public_route": kwargs["public_route"],
            "fireworks_endpoint": kwargs["fireworks_endpoint"],
            "cross_endpoint_fallback": True,
            "field_actions": kwargs["field_actions"],
            "warnings": kwargs["warnings"],
        }

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)
    monkeypatch.setattr(responses_mod, "build_route_transform_trace", fake_build_route_transform_trace)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={"model": "deepseek-v4-pro", "input": "hello", "service_tier": "priority"},
    )

    assert response.status_code == 200
    assert captured["payload"]["top_k"] == 40
    assert {"field": "top_k", "action": "default", "reason": "fireworks_reasoning_sampling_stability"} in captured["route_trace"]["field_actions"]
    assert "top_k injected default 40 for Fireworks reasoning stability" in captured["route_trace"]["warnings"]


def test_responses_fallback_preserves_explicit_top_k_zero(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        return _context(body, upstream_model="accounts/fireworks/models/deepseek-v4-pro")

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        return _fallback_proxy_response(kwargs)

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={"model": "deepseek-v4-pro", "input": "hello", "top_k": 0},
    )

    assert response.status_code == 200
    assert captured["payload"]["top_k"] == 0


def test_reasoning_model_responses_falls_back_to_chat_completions(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        return _context(body, upstream_model="accounts/fireworks/models/deepseek-v4-pro")

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        return _fallback_proxy_response(kwargs)

    def fake_build_route_transform_trace(*args, **kwargs):
        captured["route_trace"] = kwargs
        return {
            "public_route": kwargs["public_route"],
            "fireworks_endpoint": kwargs["fireworks_endpoint"],
            "cross_endpoint_fallback": True,
            "field_actions": kwargs["field_actions"],
            "warnings": kwargs["warnings"],
        }

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)
    monkeypatch.setattr(responses_mod, "build_route_transform_trace", fake_build_route_transform_trace)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={"model": "deepseek-v4-pro", "input": "用中文回答：你好"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "response"
    assert "service_tier" not in body
    assert captured["upstream_path"] == "v1/chat/completions"
    assert captured["payload"]["messages"] == [{"role": "user", "content": "用中文回答：你好"}]
    assert captured["payload"]["top_k"] == 40
    assert "service_tier" not in captured["payload"]
    assert "reasoning_stability" in captured["route_trace"]["fireworks_endpoint"]


def test_reasoning_model_responses_fallback_drops_sub2api_bridge_fields(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        return _context(body, upstream_model="accounts/fireworks/models/deepseek-v4-flash")

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        return _fallback_proxy_response(kwargs)

    def fake_build_route_transform_trace(*args, **kwargs):
        captured["route_trace"] = kwargs
        return {
            "public_route": kwargs["public_route"],
            "fireworks_endpoint": kwargs["fireworks_endpoint"],
            "cross_endpoint_fallback": True,
            "field_actions": kwargs["field_actions"],
            "warnings": kwargs["warnings"],
        }

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)
    monkeypatch.setattr(responses_mod, "build_route_transform_trace", fake_build_route_transform_trace)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={
            "model": "deepseek-v4-flash",
            "input": [
                {"type": "reasoning", "summary": []},
                {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "系统"}]},
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "中文测试"}]},
            ],
            "include": ["reasoning.encrypted_content"],
            "reasoning": {"effort": "high"},
            "text": {"verbosity": "medium"},
            "parallel_tool_calls": True,
            "store": False,
        },
    )

    assert response.status_code == 200
    assert captured["payload"]["messages"] == [
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "中文测试"},
    ]
    assert "include" not in captured["payload"]
    assert "reasoning" not in captured["payload"]
    assert captured["payload"]["reasoning_effort"] == "high"
    assert "text" not in captured["payload"]
    dropped_fields = {change["field"] for change in captured["route_trace"]["field_actions"] if change.get("action") == "dropped"}
    assert {"include", "parallel_tool_calls", "store", "text"}.issubset(dropped_fields)
    assert "reasoning" not in dropped_fields
    assert {"field": "reasoning.effort", "action": "map", "to": "reasoning_effort"} in captured["route_trace"]["field_actions"]


def test_reasoning_model_responses_fallback_streams_via_chat(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        return _context(body, upstream_model="accounts/fireworks/models/deepseek-v4-pro")

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        return Response(status_code=200)

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={
            "model": "deepseek-v4-pro",
            "input": "中文流式测试",
            "stream": True,
            "include": ["reasoning.encrypted_content"],
            "text": {"verbosity": "medium"},
            "parallel_tool_calls": True,
        },
    )

    assert response.status_code == 200
    assert captured["upstream_path"] == "v1/chat/completions"
    assert captured["payload"]["stream"] is True
    assert captured["payload"]["stream_options"] == {"include_usage": True}
    assert callable(captured["stream_transform_factory"])
    transform = captured["stream_transform_factory"]()
    assert transform.__class__ is responses_mod.ChatCompletionsToResponsesSSE


def test_reasoning_model_responses_fallback_maps_function_tools_and_replay(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        return _context(body, upstream_model="accounts/fireworks/models/deepseek-v4-pro")

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        return _fallback_proxy_response(kwargs)

    def fake_build_route_transform_trace(*args, **kwargs):
        captured["route_trace"] = kwargs
        return {"field_actions": kwargs["field_actions"], "warnings": kwargs["warnings"]}

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)
    monkeypatch.setattr(responses_mod, "build_route_transform_trace", fake_build_route_transform_trace)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={
            "model": "deepseek-v4-pro",
            "previous_response_id": "resp_1",
            "input": [
                {"type": "message", "role": "user", "content": "查一下"},
                {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{\"q\":\"k\"}"},
                {"type": "function_call_output", "call_id": "call_1", "output": {"ok": True}},
            ],
            "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
            "tool_choice": {"type": "function", "name": "lookup"},
        },
    )

    assert response.status_code == 200
    assert captured["payload"]["tools"] == [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]
    assert captured["payload"]["tool_choice"] == {"type": "function", "function": {"name": "lookup"}}
    assert captured["payload"]["messages"] == [
        {"role": "user", "content": "查一下"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": "{\"q\":\"k\"}"}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "{\"ok\":true}"},
    ]
    dropped_fields = {change["field"] for change in captured["route_trace"]["field_actions"] if change.get("action") == "dropped"}
    assert "previous_response_id" in dropped_fields


def test_reasoning_model_responses_fallback_drops_unmatched_tool_output(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        return _context(body, upstream_model="accounts/fireworks/models/deepseek-v4-pro")

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        return _fallback_proxy_response(kwargs)

    def fake_build_route_transform_trace(*args, **kwargs):
        captured["route_trace"] = kwargs
        return {"field_actions": kwargs["field_actions"], "warnings": kwargs["warnings"]}

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)
    monkeypatch.setattr(responses_mod, "build_route_transform_trace", fake_build_route_transform_trace)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={
            "model": "deepseek-v4-pro",
            "input": [
                {"type": "message", "role": "user", "content": "hello"},
                {"type": "function_call_output", "call_id": "call_1", "output": "done"},
            ],
        },
    )

    assert response.status_code == 200
    assert captured["payload"]["messages"] == [{"role": "user", "content": "hello"}]
    assert any(change.get("field") == "input.function_call_output[call_1]" and change.get("action") == "dropped" for change in captured["route_trace"]["field_actions"])


def test_reasoning_model_responses_fallback_drops_function_call_missing_name(monkeypatch: MonkeyPatch) -> None:
    # Fireworks does not require name/call_id on input-side function_call items
    # (CreateResponse.input is an open object array). When the Chat Completions
    # fallback cannot map such an item to a valid tool_call, it must drop it
    # lossy instead of rejecting the whole request with a 400.
    _require_auth(monkeypatch)
    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        return _context(body, upstream_model="accounts/fireworks/models/deepseek-v4-pro")

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        return _fallback_proxy_response(kwargs)

    def fake_build_route_transform_trace(*args, **kwargs):
        captured["route_trace"] = kwargs
        return {"field_actions": kwargs["field_actions"], "warnings": kwargs["warnings"]}

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)
    monkeypatch.setattr(responses_mod, "build_route_transform_trace", fake_build_route_transform_trace)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={
            "model": "deepseek-v4-pro",
            "input": [
                {"type": "message", "role": "user", "content": "hello"},
                {"type": "function_call", "call_id": "call_1", "arguments": "{}"},
            ],
        },
    )

    assert response.status_code == 200
    assert captured["payload"]["messages"] == [{"role": "user", "content": "hello"}]
    assert any(
        change.get("field") == "input.function_call[call_1]" and change.get("action") == "dropped"
        for change in captured["route_trace"]["field_actions"]
    )


def test_reasoning_model_responses_fallback_drops_function_call_output_missing_call_id(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        return _context(body, upstream_model="accounts/fireworks/models/deepseek-v4-pro")

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        return _fallback_proxy_response(kwargs)

    def fake_build_route_transform_trace(*args, **kwargs):
        captured["route_trace"] = kwargs
        return {"field_actions": kwargs["field_actions"], "warnings": kwargs["warnings"]}

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)
    monkeypatch.setattr(responses_mod, "build_route_transform_trace", fake_build_route_transform_trace)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={
            "model": "deepseek-v4-pro",
            "input": [
                {"type": "message", "role": "user", "content": "hello"},
                {"type": "function_call_output", "output": "done"},
            ],
        },
    )

    assert response.status_code == 200
    assert captured["payload"]["messages"] == [{"role": "user", "content": "hello"}]
    assert any(
        change.get("field") == "input.function_call_output[unknown]" and change.get("action") == "dropped"
        for change in captured["route_trace"]["field_actions"]
    )
    _require_auth(monkeypatch)
    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        return _context(body, upstream_model="accounts/fireworks/models/deepseek-v4-pro")

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        return _fallback_proxy_response(kwargs)

    def fake_build_route_transform_trace(*args, **kwargs):
        captured["route_trace"] = kwargs
        return {"field_actions": kwargs["field_actions"], "warnings": kwargs["warnings"]}

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)
    monkeypatch.setattr(responses_mod, "build_route_transform_trace", fake_build_route_transform_trace)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={
            "model": "deepseek-v4-pro",
            "input": "hello",
            "tools": [{"type": "mcp", "server_url": "https://example.com"}],
        },
    )

    assert response.status_code == 200
    assert "tools" not in captured["payload"]
    assert any(change.get("field") == "tools[0]" and change.get("action") == "dropped" for change in captured["route_trace"]["field_actions"])


def test_reasoning_model_responses_fallback_drops_unknown_top_level_fields(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        return _context(body, upstream_model="accounts/fireworks/models/deepseek-v4-pro")

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        return _fallback_proxy_response(kwargs)

    def fake_build_route_transform_trace(*args, **kwargs):
        captured["route_trace"] = kwargs
        return {"field_actions": kwargs["field_actions"], "warnings": kwargs["warnings"]}

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)
    monkeypatch.setattr(responses_mod, "build_route_transform_trace", fake_build_route_transform_trace)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={
            "model": "deepseek-v4-pro",
            "input": "hello",
            "previous_response_id": "",
            "max_tool_calls": 0,
            "stream_options": "not-an-object",
            "future_responses_field": {"x": 1},
            "service_tier": "default",
        },
    )

    assert response.status_code == 200
    assert captured["payload"]["messages"] == [{"role": "user", "content": "hello"}]
    assert captured["payload"]["service_tier"] == "default"
    assert "future_responses_field" not in captured["payload"]
    dropped_fields = {change["field"] for change in captured["route_trace"]["field_actions"] if change.get("action") == "dropped"}
    assert {"previous_response_id", "max_tool_calls", "stream_options", "future_responses_field"}.issubset(dropped_fields)


def test_reasoning_model_responses_fallback_accepts_bare_string_input_items(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        return _context(body, upstream_model="accounts/fireworks/models/deepseek-v4-pro")

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        return _fallback_proxy_response(kwargs)

    def fake_build_route_transform_trace(*args, **kwargs):
        captured["route_trace"] = kwargs
        return {"field_actions": kwargs["field_actions"], "warnings": kwargs["warnings"]}

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)
    monkeypatch.setattr(responses_mod, "build_route_transform_trace", fake_build_route_transform_trace)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={"model": "deepseek-v4-pro", "input": ["hello", 123, {"type": "message", "role": "user", "content": "world"}]},
    )

    assert response.status_code == 200
    assert captured["payload"]["messages"] == [{"role": "user", "content": "hello"}, {"role": "user", "content": "world"}]
    assert any(change.get("field") == "input[1]" and change.get("action") == "dropped" for change in captured["route_trace"]["field_actions"])


def test_responses_priority_fallback_accepts_typed_text_message_item(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        return _context(body)

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        return _fallback_proxy_response(kwargs)

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={"model": "test", "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}], "service_tier": "priority"},
    )

    assert response.status_code == 200
    assert captured["payload"]["messages"] == [{"role": "user", "content": "hello"}]


@pytest.mark.parametrize(
    "field,value",
    [
        ("stream", True),
        ("tools", [{"type": "mcp", "server_url": "https://example.com"}]),
        ("tools", [{"type": "function", "function": {"name": "lookup"}}]),
        ("images", [{"url": "https://example.com/cat.png"}]),
        ("reasoning", {"effort": "high"}),
        ("previous_response_id", "resp_1"),
        ("store", True),
        ("input", [{"type": "function_call_output", "call_id": "call_1", "output": "done"}]),
    ],
)
def test_responses_priority_fallback_rejects_unsupported_native_fields(monkeypatch: MonkeyPatch, field, value) -> None:
    _require_auth(monkeypatch)

    async def fake_build_proxy_context(request, body):
        return _context(body)

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={"model": "test", "input": "hello", "service_tier": "priority", field: value},
    )

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_non_priority_responses_path_unchanged(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        return _context(body)

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        return JSONResponse({"id": "resp_1", "object": "response"})

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={"model": "test", "input": "hello"},
    )

    assert response.status_code == 200
    assert response.json()["object"] == "response"
    assert captured["upstream_path"] == "v1/responses"
    assert captured["payload"]["input"] == "hello"


def test_responses_priority_fallback_route_trace_marks_cross_endpoint(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        return _context(body)

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        return _fallback_proxy_response(kwargs)

    def fake_build_route_transform_trace(*args, **kwargs):
        captured["route_trace"] = kwargs
        return {"public_route": kwargs["public_route"], "fireworks_endpoint": kwargs["fireworks_endpoint"], "cross_endpoint_fallback": True}

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)
    monkeypatch.setattr(responses_mod, "build_route_transform_trace", fake_build_route_transform_trace)

    client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={"model": "test", "input": "hello", "service_tier": "priority"},
    )

    assert captured["route_trace"]["public_route"] == "POST /v1/responses"
    assert "cross_endpoint_fallback" in captured["route_trace"]["fireworks_endpoint"]
