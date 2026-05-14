from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.responses import Response
from fastapi.testclient import TestClient

from app.main import app
import app.products.openai.chat_completions as chat_router
import app.products.openai.completions as completions_router
import app.products.openai.embeddings as embeddings_router
import app.products.openai.responses as responses_router
import app.products.openai.rerank as rerank_router
import app.platform.auth as auth


client = TestClient(app)


def _require_auth(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))


def _context(body: dict[str, object], upstream_model: str = "accounts/fireworks/models/upstream") -> SimpleNamespace:
    return SimpleNamespace(
        body=body,
        settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", upstream_base_url="https://api.fireworks.ai/inference"),
        request_headers={"x-session-affinity": "aff"},
        stable_key="stable",
        resolved_model=SimpleNamespace(upstream_model=upstream_model),
        routing_metadata={
            "stable_key_source": "session",
            "stable_key_hash_value": "hash123",
            "affinity_header": "aff",
            "routing_mode": "account_aware_sticky",
            "primary_account_bucket": "account:acct-a",
            "selected_account_count": 2,
            "selected_key_count": 3,
        },
    )


def _install_stub(monkeypatch, module, body, captured):
    async def fake_load_json_body(request):
        return body

    async def fake_build_proxy_context_from_body(request, body_arg, **kwargs):
        return _context(body_arg)

    async def fake_build_proxy_context_optional_model(request, body_arg, **kwargs):
        return _context(body_arg)

    async def fake_proxy(context, *, endpoint, upstream_path, payload, headers, route_trace=None, **kwargs):
        captured.update({"endpoint": endpoint, "upstream_path": upstream_path, "payload": payload, "headers": headers, "route_trace": route_trace})
        return SimpleNamespace(status_code=200)

    if hasattr(module, "load_json_body"):
        monkeypatch.setattr(module, "load_json_body", fake_load_json_body)
    if hasattr(module, "build_proxy_context_from_body"):
        monkeypatch.setattr(module, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    if hasattr(module, "build_proxy_context_optional_model"):
        monkeypatch.setattr(module, "build_proxy_context_optional_model", fake_build_proxy_context_optional_model)
    monkeypatch.setattr(module, "proxy_fireworks_request", fake_proxy)


@pytest.mark.parametrize(
    ("module", "path", "body", "expected_route", "expected_endpoint"),
    [
        (chat_router, "/v1/chat/completions", {"model": "alias", "messages": [{"role": "user", "content": "hi"}], "tools": [{"type": "function", "function": {"name": "lookup"}}]}, "POST /v1/chat/completions", "chat_completions"),
        (completions_router, "/v1/completions", {"model": "alias", "prompt": "hello"}, "POST /v1/completions", "completions"),
        (responses_router, "/v1/responses", {"model": "alias", "input": "hello"}, "POST /v1/responses", "responses"),
        (embeddings_router, "/v1/embeddings", {"model": "alias", "input": "hello"}, "POST /v1/embeddings", "embeddings"),
        (rerank_router, "/v1/rerank", {"model": "alias", "query": "q", "documents": ["d"]}, "POST /v1/rerank", "rerank"),
    ],
)
def test_openai_adapters_attach_route_trace(monkeypatch, module, path, body, expected_route, expected_endpoint):
    _require_auth(monkeypatch)
    captured = {}
    _install_stub(monkeypatch, module, body, captured)

    response = client.post(path, headers={"Authorization": "Bearer token"}, json=body)

    assert response.status_code == 200
    assert captured["endpoint"] == expected_endpoint
    assert captured["route_trace"]["public_route"] == expected_route
    assert captured["route_trace"]["product"] == "openai"
    assert captured["route_trace"]["adapter"]
    assert captured["route_trace"]["fireworks_endpoint"]
    assert captured["route_trace"]["routing"]["routing_mode"] == "account_aware_sticky"
    assert captured["route_trace"]["routing"]["primary_account_bucket"] == "account:acct-a"
    assert captured["route_trace"]["field_actions"] == () or isinstance(captured["route_trace"]["field_actions"], tuple)
    route_trace_text = str(captured["route_trace"])
    assert "tool_calls" not in route_trace_text
    assert "image_url" not in route_trace_text


def test_responses_lifecycle_uses_template_route_trace(monkeypatch):
    _require_auth(monkeypatch)
    captured = {}

    async def fake_build_proxy_context_from_body(request, body, **kwargs):
        return _context(body)

    async def fake_build_proxy_key_context(request, route_seed):
        ctx = _context({"model": "alias"})
        ctx.repository = SimpleNamespace(get_response_key_route=lambda response_id: None)
        return ctx

    async def fake_proxy_json(context, *, endpoint, method, upstream_path, headers, params=None, route_trace=None):
        captured.update({"endpoint": endpoint, "method": method, "upstream_path": upstream_path, "route_trace": route_trace})
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(responses_router, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    monkeypatch.setattr(responses_router, "build_proxy_key_context", fake_build_proxy_key_context)
    monkeypatch.setattr(responses_router, "proxy_fireworks_json_request", fake_proxy_json)

    response = client.get("/v1/responses/resp_123", headers={"Authorization": "Bearer token"})

    assert response.status_code == 200
    assert captured["upstream_path"] == "v1/responses/resp_123"
    assert captured["route_trace"]["public_route"] == "GET /v1/responses/{response_id}"
    assert captured["route_trace"]["product"] == "openai"


def test_responses_delete_uses_template_route_trace(monkeypatch):
    _require_auth(monkeypatch)
    captured = {}

    async def fake_build_proxy_key_context(request, route_seed):
        ctx = _context({"model": "alias"})
        ctx.repository = SimpleNamespace(get_response_key_route=lambda response_id: None)
        return ctx

    async def fake_proxy_json(context, *, endpoint, method, upstream_path, headers, params=None, route_trace=None):
        captured.update({"endpoint": endpoint, "method": method, "upstream_path": upstream_path, "route_trace": route_trace})
        return Response(status_code=204)

    monkeypatch.setattr(responses_router, "build_proxy_key_context", fake_build_proxy_key_context)
    monkeypatch.setattr(responses_router, "proxy_fireworks_json_request", fake_proxy_json)

    response = client.delete("/v1/responses/resp_123", headers={"Authorization": "Bearer token"})

    assert response.status_code == 204
    assert captured["method"] == "DELETE"
    assert captured["route_trace"]["public_route"] == "DELETE /v1/responses/{response_id}"
