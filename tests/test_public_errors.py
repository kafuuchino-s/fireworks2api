from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.main import app as main_app
import app.platform.auth as auth
import app.products.anthropic.router as anthropic_router


client = TestClient(main_app)


def _require_auth(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))


def test_openai_chat_invalid_json_uses_openai_envelope(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    response = client.post("/v1/chat/completions", headers={"Authorization": "Bearer token"}, data="{")

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "invalid JSON body"


def test_openai_chat_non_object_json_uses_openai_envelope(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    response = client.post("/v1/chat/completions", headers={"Authorization": "Bearer token"}, json=[1, 2])

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "JSON body must be an object"


def test_anthropic_messages_accepts_x_api_key(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    async def fake_build_proxy_context_from_body(request, body):
        return SimpleNamespace(
            body=body,
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
            request_headers={},
            stable_key="stable",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )

    async def fake_proxy_fireworks_request(*args, **kwargs):
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post("/v1/messages", headers={"x-api-key": "token", "anthropic-version": "2023-06-01"}, json={"model": "kimi-k2.6", "messages": [], "max_tokens": 1})

    assert response.status_code == 200


def test_anthropic_messages_missing_version_returns_anthropic_envelope(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    response = client.post("/v1/messages", headers={"Authorization": "Bearer token"}, json={"model": "kimi-k2.6", "messages": []})

    assert response.status_code == 400
    assert response.json()["type"] == "error"
