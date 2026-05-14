from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

import app.platform.auth as auth
import app.products.anthropic.router as anthropic_router


app = FastAPI()
app.include_router(anthropic_router.router)
client = TestClient(app)


def _require_auth(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))


def test_anthropic_messages_route_builds_route_trace(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    captured = {}

    async def fake_build_proxy_context_from_body(request, body):
        return SimpleNamespace(
            body=body,
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={"x-session-affinity": "session-affinity", "authorization": "Bearer secret-key"},
            stable_key="stable-key",
            stable_key_source="session",
            stable_key_hash_value="hash123",
            affinity_header="x-session-affinity",
            selected_key_count=2,
            route_key="route-secret",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log", upstream_base_url="https://api.fireworks.ai"),
        )

    async def fake_proxy_fireworks_request(context, *, endpoint, upstream_path, payload, headers, route_trace=None):
        captured.update(
            {
                "endpoint": endpoint,
                "upstream_path": upstream_path,
                "payload": payload,
                "headers": headers,
                "route_trace": route_trace,
            }
        )
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post(
        "/v1/messages",
        headers={"Authorization": "Bearer token", "anthropic-version": "2023-06-01"},
        json={
            "model": "kimi-k2.6",
            "max_tokens": 2048,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello secret text"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "secret-image-data"}},
                    ],
                },
                {"role": "assistant", "content": [{"type": "tool_use", "id": "tool-1", "name": "calc", "input": {"api_key": "secret"}}]},
            ],
            "tools": [{"name": "calc", "description": "secret tool", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "calc"},
            "thinking": {"type": "enabled", "budget_tokens": 1024},
        },
    )

    assert response.status_code == 200
    trace = captured["route_trace"]
    assert trace["public_route"] == "/v1/messages"
    assert trace["adapter"] == "app.products.anthropic.adapters"
    assert trace["fireworks_endpoint"] == "anthropic_messages"
    assert "anthropic_messages" in trace["fireworks_endpoint"]
    assert "image" in trace["capability_tags"]
    assert "thinking" in trace["capability_tags"]
    assert "tools" in trace["capability_tags"]
    assert trace["request_shape"]["payload_field_names"]
    assert "secret-image-data" not in repr(trace)
    assert "secret text" not in repr(trace)
    assert "secret tool" not in repr(trace)
    assert "secret" not in repr(trace)
    assert captured["endpoint"] == "messages"
    assert captured["upstream_path"] == "v1/messages"


def test_anthropic_messages_bridge_route_trace_uses_responses_endpoint(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    captured = {}

    async def fake_build_proxy_context_from_body(request, body):
        return SimpleNamespace(
            body=body,
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={"x-session-affinity": "session-affinity"},
            stable_key="stable-key",
            stable_key_source="session",
            stable_key_hash_value="hash123",
            affinity_header="x-session-affinity",
            selected_key_count=2,
            route_key="route-secret",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log", upstream_base_url="https://api.fireworks.ai"),
        )

    async def fake_proxy_fireworks_request(context, *, endpoint, upstream_path, payload, headers, route_trace=None, stream_transform_factory=None, response_id_callback=None):
        captured.update({"route_trace": route_trace, "upstream_path": upstream_path})
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post(
        "/v1/messages",
        headers={"Authorization": "Bearer token", "anthropic-version": "2023-06-01", "x-fireworks2api-messages-mode": "responses_bridge"},
        json={"model": "kimi-k2.6", "max_tokens": 2048, "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    trace = captured["route_trace"]
    assert trace["public_route"] == "/v1/messages"
    assert trace["adapter"] == "app.products.anthropic.responses_bridge"
    assert trace["fireworks_endpoint"] == "responses"
    assert captured["upstream_path"] == "v1/responses"


def test_anthropic_messages_route_trace_preserves_tool_stream_passthrough(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    captured = {}

    async def fake_build_proxy_context_from_body(request, body):
        return SimpleNamespace(
            body=body,
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={"anthropic-beta": "tools-2024-01-01"},
            stable_key="stable-key",
            stable_key_source="session",
            stable_key_hash_value="hash123",
            affinity_header="x-session-affinity",
            selected_key_count=2,
            route_key="route-secret",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log", upstream_base_url="https://api.fireworks.ai"),
        )

    async def fake_proxy_fireworks_request(context, *, endpoint, upstream_path, payload, headers, route_trace=None):
        captured.update({"payload": payload, "route_trace": route_trace, "headers": headers})
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post(
        "/v1/messages",
        headers={"Authorization": "Bearer token", "anthropic-version": "2023-06-01", "x-fireworks2api-messages-mode": "native"},
        json={
            "model": "kimi-k2.6",
            "max_tokens": 2048,
            "stream": True,
            "messages": [
                {"role": "assistant", "content": [{"type": "tool_use", "id": "tool-1", "name": "calc", "input": {"value": 1}}]},
            ],
            "tools": [{"name": "calc", "input_schema": {"type": "object"}}],
        },
    )

    assert response.status_code == 200
    assert captured["payload"]["stream"] is True
    assert captured["payload"]["messages"][0]["content"][0]["type"] == "tool_use"
    assert "anthropic-beta" not in captured["headers"]
