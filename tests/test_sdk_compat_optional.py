from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import app.platform.auth as auth
import app.products.anthropic.router as anthropic_router
import app.products.openai.responses as openai_responses


def _require_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))


def _local_app() -> FastAPI:
    app = FastAPI()
    app.include_router(openai_responses.router)
    app.include_router(anthropic_router.router)
    return app


def _responses_mcp_payload() -> dict[str, object]:
    return {
        "model": "kimi-k2.6",
        "input": "Use the docs tool to answer in one short sentence.",
        "tools": [
            {
                "type": "mcp",
                "server_label": "docs",
                "server_url": "https://example.invalid/mcp",
            }
        ],
        "tool_choice": "required",
    }


def _responses_input_image_payload() -> dict[str, object]:
    return {
        "model": "kimi-k2.6",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Describe this image."},
                    {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                ],
            }
        ],
    }


def _responses_function_call_output_payload(response_id: str = "resp-1", call_id: str = "call-1") -> dict[str, object]:
    return {
        "model": "kimi-k2.6",
        "previous_response_id": response_id,
        "input": [
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": "{\"answer\":\"42\"}",
            }
        ],
    }


def _anthropic_tool_use_payload() -> dict[str, object]:
    return {
        "model": "kimi-k2.6",
        "max_tokens": 64,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Use the calculator tool."},
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": "42"},
                ],
            }
        ],
    }


def _anthropic_stream_payload() -> dict[str, object]:
    return {
        "model": "kimi-k2.6",
        "max_tokens": 32,
        "stream": True,
        "messages": [{"role": "user", "content": "Say hello in one short sentence."}],
    }


def _sdk_smoke_import(name: str):
    import importlib

    return importlib.import_module(name)


@pytest.mark.asyncio
async def test_openai_sdk_compat_path_and_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    openai = pytest.importorskip("openai")
    _require_auth(monkeypatch)
    captured: dict[str, object] = {}

    async def fake_build_proxy_context_from_body(request, body):
        captured["path"] = request.url.path
        captured["authorization"] = request.headers.get("authorization")
        captured["x_api_key"] = request.headers.get("x-api-key")
        return SimpleNamespace(
            body=body,
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/glm-5p1"),
            request_headers={"x-session-affinity": "session-affinity"},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log", upstream_base_url="https://api.fireworks.ai/inference", anthropic_messages_mode="native"),
        )

    async def fake_proxy_fireworks_request(context, *, endpoint, upstream_path, payload, headers, route_trace=None):
        captured["endpoint"] = endpoint
        captured["upstream_path"] = upstream_path
        captured["payload_model"] = payload.get("model")
        return SimpleNamespace(status_code=200, json=lambda: {"id": "chatcmpl-test", "object": "chat.completion", "choices": []})

    monkeypatch.setattr(openai_responses, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    monkeypatch.setattr(openai_responses, "proxy_fireworks_request", fake_proxy_fireworks_request)
    monkeypatch.setattr(openai_responses, "validate_responses_body", lambda body: None)

    transport = httpx.ASGITransport(app=_local_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        sdk = openai.AsyncOpenAI(api_key="token", base_url="http://testserver/v1", http_client=http_client)
        await sdk.responses.create(model="kimi-k2.6", input="hi")

    assert captured["path"] == "/v1/responses"
    assert captured["authorization"] == "Bearer token"
    assert captured["endpoint"] == "responses"
    assert captured["upstream_path"] == "v1/responses"
    assert captured["payload_model"] == "accounts/fireworks/models/glm-5p1"


@pytest.mark.asyncio
async def test_anthropic_sdk_compat_version_and_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    anthropic = pytest.importorskip("anthropic")
    _require_auth(monkeypatch)
    captured: dict[str, object] = {}

    async def fake_build_proxy_context_from_body(request, body):
        captured["path"] = request.url.path
        captured["authorization"] = request.headers.get("authorization")
        captured["x_api_key"] = request.headers.get("x-api-key")
        captured["anthropic_version"] = request.headers.get("anthropic-version")
        return SimpleNamespace(
            body=body,
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/glm-5p1"),
            request_headers={"x-session-affinity": "session-affinity"},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log", upstream_base_url="https://api.fireworks.ai/inference"),
        )

    async def fake_proxy_fireworks_request(context, *, endpoint, upstream_path, payload, headers, route_trace=None):
        captured["endpoint"] = endpoint
        captured["upstream_path"] = upstream_path
        captured["payload_model"] = payload.get("model")
        return SimpleNamespace(status_code=200, json=lambda: {"id": "msg-test", "type": "message", "content": []})

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    transport = httpx.ASGITransport(app=_local_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        sdk = anthropic.AsyncAnthropic(api_key="token", base_url="http://testserver", http_client=http_client)
        await sdk.messages.create(model="kimi-k2.6", max_tokens=1, messages=[{"role": "user", "content": "hi"}])

    assert captured["path"] == "/v1/messages"
    assert captured["authorization"] == "Bearer token" or captured["x_api_key"] == "token"
    assert captured["anthropic_version"] == "2023-06-01"
    assert captured["endpoint"] == "messages"
    assert captured["upstream_path"] == "v1/messages"
    assert captured["payload_model"] == "accounts/fireworks/models/glm-5p1"


@pytest.mark.asyncio
async def test_openai_responses_sdk_shaped_fixtures(monkeypatch: pytest.MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    seen: list[dict[str, object]] = []

    async def fake_build_proxy_context_from_body(request, body):
        seen.append({"path": request.url.path, "body": body})
        return SimpleNamespace(
            body=body,
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/glm-5p1"),
            request_headers={"x-session-affinity": "session-affinity"},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log", upstream_base_url="https://api.fireworks.ai/inference"),
        )

    async def fake_proxy_fireworks_request(context, *, endpoint, upstream_path, payload, headers, route_trace=None):
        seen.append({"endpoint": endpoint, "upstream_path": upstream_path, "payload": payload})
        return SimpleNamespace(status_code=200, json=lambda: {"id": "resp-1", "object": "response", "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]})

    monkeypatch.setattr(openai_responses, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    monkeypatch.setattr(openai_responses, "proxy_fireworks_request", fake_proxy_fireworks_request)

    transport = httpx.ASGITransport(app=_local_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        response = await http_client.post("/v1/responses", headers={"Authorization": "Bearer token"}, json=_responses_mcp_payload())
        assert response.status_code == 200

    assert seen[0]["path"] == "/v1/responses"
    assert any(item.get("endpoint") == "responses" for item in seen)
    assert _responses_input_image_payload()["input"][0]["content"][1]["type"] == "input_image"
    assert _responses_function_call_output_payload()["input"][0]["type"] == "function_call_output"


@pytest.mark.asyncio
async def test_anthropic_sdk_shaped_continuation_and_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    seen: list[dict[str, object]] = []

    async def fake_build_proxy_context_from_body(request, body):
        seen.append({"path": request.url.path, "body": body, "content_type": request.headers.get("content-type")})
        return SimpleNamespace(
            body=body,
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={"x-session-affinity": "session-affinity"},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log", upstream_base_url="https://api.fireworks.ai/inference"),
        )

    async def fake_proxy_fireworks_request(context, *, endpoint, upstream_path, payload, headers):
        seen.append({"endpoint": endpoint, "upstream_path": upstream_path, "payload": payload})
        if payload.get("stream"):
            return SimpleNamespace(status_code=200, headers={"content-type": "text/event-stream"}, text="event: message\ndata: {\"type\":\"message\"}\n\n")
        return SimpleNamespace(status_code=200, json=lambda: {"id": "msg-1", "type": "message", "content": []})

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    transport = httpx.ASGITransport(app=_local_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        response = await http_client.post("/v1/messages", headers={"Authorization": "Bearer token", "anthropic-version": "2023-06-01"}, json=_anthropic_tool_use_payload())
        assert response.status_code == 200
        response = await http_client.post("/v1/messages", headers={"Authorization": "Bearer token", "anthropic-version": "2023-06-01"}, json=_anthropic_stream_payload())
        assert response.status_code == 200

    assert seen[0]["path"] == "/v1/messages"
    assert seen[0]["body"]["messages"][0]["content"][1]["type"] == "tool_result"
    assert any(item.get("payload", {}).get("stream") is True for item in seen)
