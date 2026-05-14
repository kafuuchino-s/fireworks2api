from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.main import app
import app.platform.auth as auth
import app.products.openai.responses as responses_mod


client = TestClient(app)


def _require_auth(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))


def _context(body: dict[str, object]) -> SimpleNamespace:
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
        resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
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
        return {"public_route": kwargs["public_route"], "fireworks_endpoint": kwargs["fireworks_endpoint"], "cross_endpoint_fallback": True}

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
        return {"public_route": kwargs["public_route"], "fireworks_endpoint": kwargs["fireworks_endpoint"], "cross_endpoint_fallback": True}

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
