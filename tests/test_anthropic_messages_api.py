from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
import pytest
from pytest import MonkeyPatch

import app.products.anthropic.router as anthropic_router
import app.platform.auth as auth
from app.products.anthropic.adapters import build_messages_adapter


app = FastAPI()
app.include_router(anthropic_router.router)
client = TestClient(app)


def _require_auth(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))


def _anthropic_headers() -> dict[str, str]:
    return {"Authorization": "Bearer token", "anthropic-version": "2023-06-01"}


def _messages_adapter_context(body: dict[str, object], upstream_model: str) -> SimpleNamespace:
    return SimpleNamespace(
        body=body,
        resolved_model=SimpleNamespace(upstream_model=upstream_model),
        request_headers={},
        stable_key="stable-key",
        settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
    )


def test_anthropic_messages_routes_accept_proxy_auth(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = _anthropic_headers()

    captured = {}

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", lambda request, body: fake_build_proxy_context(request))

    async def fake_build_proxy_context(request):
        return SimpleNamespace(
            body={"model": "kimi-k2.6", "messages": []},
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={"x-session-affinity": "session-affinity"},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )

    async def fake_proxy_fireworks_request(context, *, endpoint, upstream_path, payload, headers, route_trace=None):
        captured.update(
            {
                "endpoint": endpoint,
                "upstream_path": upstream_path,
                "payload": payload,
                "headers": headers,
            }
        )
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post("/v1/messages", headers=headers, json={"model": "kimi-k2.6", "messages": [], "max_tokens": 1})
    assert response.status_code == 200
    assert captured["endpoint"] == "messages"
    assert captured["upstream_path"] == "v1/messages"
    assert captured["payload"]["model"] == "accounts/fireworks/models/kimi-k2p6"
    assert captured["headers"]["x-session-affinity"] == "session-affinity"

    response = client.post("/messages", headers=headers, json={"model": "kimi-k2.6", "messages": []})
    assert response.status_code == 404


def test_anthropic_messages_native_mode_can_be_requested(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {**_anthropic_headers(), "x-fireworks2api-messages-mode": "native"}
    captured = {}

    async def fake_build_proxy_context(request):
        return SimpleNamespace(
            body={"model": "kimi-k2.6", "messages": []},
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={"x-session-affinity": "session-affinity"},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log", upstream_base_url="https://api.fireworks.ai"),
        )

    async def fake_proxy_fireworks_request(context, *, endpoint, upstream_path, payload, headers, route_trace=None):
        captured.update({"endpoint": endpoint, "upstream_path": upstream_path, "payload": payload, "headers": headers, "route_trace": route_trace})
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", lambda request, body: fake_build_proxy_context(request))
    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post("/v1/messages", headers=headers, json={"model": "kimi-k2.6", "messages": [], "max_tokens": 1})

    assert response.status_code == 200
    assert captured["endpoint"] == "messages"
    assert captured["upstream_path"] == "v1/messages"
    assert captured["headers"]["x-session-affinity"] == "session-affinity"


def test_anthropic_messages_bridge_mode_uses_responses_endpoint(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {**_anthropic_headers(), "x-fireworks2api-messages-mode": "responses_bridge"}

    captured = {}

    async def fake_build_proxy_context(request):
        return SimpleNamespace(
            body={"model": "kimi-k2.6", "messages": [], "max_tokens": 1},
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={"x-session-affinity": "session-affinity"},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log", upstream_base_url="https://api.fireworks.ai"),
        )

    async def fake_proxy_fireworks_request(context, *, endpoint, upstream_path, payload, headers, route_trace=None, stream_transform_factory=None, response_id_callback=None):
        captured.update({"endpoint": endpoint, "upstream_path": upstream_path, "payload": payload, "route_trace": route_trace, "stream_transform_factory": stream_transform_factory, "response_id_callback": response_id_callback})
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", lambda request, body: fake_build_proxy_context(request))
    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post("/v1/messages", headers=headers, json={"model": "kimi-k2.6", "messages": [], "max_tokens": 1})
    assert response.status_code == 200
    assert captured["endpoint"] == "responses"
    assert captured["upstream_path"] == "v1/responses"
    assert captured["payload"]["stream"] is True
    assert captured["route_trace"]["adapter"] == "app.products.anthropic.responses_bridge"
    assert captured["route_trace"]["fireworks_endpoint"] == "responses"


def test_anthropic_messages_bridge_binds_and_reuses_previous_response(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {**_anthropic_headers(), "x-fireworks2api-messages-mode": "responses_bridge"}
    key = SimpleNamespace(name="key-a", fingerprint="fp-a")

    class FakeRepository:
        def __init__(self) -> None:
            self.binding = None

        def get_response_session_binding(self, scope, model, session_hash):
            return self.binding

        def upsert_response_session_binding(self, scope, model, session_hash, response_id, key_name=None, key_fingerprint=None):
            self.binding = SimpleNamespace(response_id=response_id, key_name=key_name, key_fingerprint=key_fingerprint)

        def get_response_key_route(self, response_id):
            return key if response_id == "resp_first" else None

    repository = FakeRepository()
    captured_payloads = []
    captured_selected = []

    async def fake_build_proxy_context(request):
        return SimpleNamespace(
            body={"model": "kimi-k2.6", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 128},
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={"x-session-affinity": "session-affinity"},
            stable_key="stable-key",
            stable_key_source="session",
            stable_key_hash_value="hash123",
            affinity_header="x-session-affinity",
            selected_key_count=1,
            selected_keys=[SimpleNamespace(name="key-b", fingerprint="fp-b")],
            route_key="route-secret",
            repository=repository,
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log", upstream_base_url="https://api.fireworks.ai"),
        )

    async def fake_proxy_fireworks_request(context, *, endpoint, upstream_path, payload, headers, route_trace=None, stream_transform_factory=None, response_id_callback=None):
        captured_payloads.append(payload)
        captured_selected.append([getattr(item, "name", None) for item in getattr(context, "selected_keys", [])])
        if response_id_callback is not None:
            response_id_callback("resp_first", key)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", lambda request, body: fake_build_proxy_context(request))
    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    first = client.post("/v1/messages", headers=headers, json={"model": "kimi-k2.6", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 128})
    second = client.post("/v1/messages", headers=headers, json={"model": "kimi-k2.6", "messages": [{"role": "user", "content": "again"}], "max_tokens": 128})

    assert first.status_code == 200
    assert second.status_code == 200
    assert repository.binding.response_id == "resp_first"
    assert captured_payloads[0].get("previous_response_id") is None
    assert captured_payloads[1]["previous_response_id"] == "resp_first"
    assert captured_payloads[1]["store"] is True
    assert captured_selected[1] == ["key-a"]


def test_anthropic_messages_routes_forward_affinity_headers(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {**_anthropic_headers(), "x-fireworks2api-messages-mode": "native"}

    captured = {}

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", lambda request, body: fake_build_proxy_context(request))

    async def fake_build_proxy_context(request):
        return SimpleNamespace(
            body={"model": "kimi-k2.6", "messages": []},
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={"x-session-affinity": "session-affinity"},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )

    async def fake_proxy_fireworks_request(context, *, endpoint, upstream_path, payload, headers):
        captured.update({"headers": headers, "payload": payload})
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post("/v1/messages", headers=headers, json={"model": "kimi-k2.6", "messages": [], "max_tokens": 1})
    assert response.status_code == 200
    assert captured["headers"]["x-session-affinity"] == "session-affinity"


def test_anthropic_messages_routes_reject_unknown_fields_locally(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = _anthropic_headers()

    response = client.post("/v1/messages", headers=headers, json={"model": "kimi-k2.6", "messages": [], "max_tokens": 1, "vendor": "x"})
    assert response.status_code == 400
    assert response.json() == {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "unknown parameter 'vendor'",
            "param": "vendor",
            "code": "unknown_parameter",
        },
    }


def test_anthropic_messages_adapter_forwards_documented_fireworks_fields() -> None:
    payload, _, _ = build_messages_adapter(
        SimpleNamespace(
            body={
                "model": "kimi-k2.6",
                "messages": [{"role": "user", "content": "hello"}],
                "metadata": {"request_id": "abc"},
                "thinking": {"type": "enabled"},
                "output_config": {"effort": "high"},
                "raw_output": True,
                "tools": [{"name": "calculator"}],
                "tool_choice": "auto",
                "service_tier": "priority",
            },
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )
    )

    assert payload["model"] == "accounts/fireworks/models/kimi-k2p6"
    assert payload["metadata"] == {"request_id": "abc"}
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["output_config"] == {"effort": "high"}
    assert payload["raw_output"] is True
    assert payload["tools"] == [{"name": "calculator"}]
    assert payload["tool_choice"] == "auto"
    assert payload["service_tier"] == "priority"


@pytest.mark.parametrize(
    "upstream_model",
    [
        "accounts/fireworks/models/deepseek-v4-pro",
        "accounts/fireworks/models/deepseek-v4-flash",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    ],
)
def test_anthropic_messages_adapter_injects_reasoning_top_k_default(upstream_model: str) -> None:
    payload, _, report = build_messages_adapter(
        _messages_adapter_context(
            {"model": "kimi-k2.6", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1},
            upstream_model,
        )
    )

    assert payload["top_k"] == 40
    assert {"field": "top_k", "action": "default", "reason": "fireworks_reasoning_sampling_stability"} in report["field_changes"]
    assert "top_k injected default 40 for Fireworks reasoning stability" in report["warnings"]


def test_anthropic_messages_adapter_preserves_explicit_top_k() -> None:
    """Explicit top_k is preserved; Kimi K2.6 sampling defaults are not injected."""
    payload, _, report = build_messages_adapter(
        _messages_adapter_context(
            {"model": "kimi-k2.6", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1, "top_k": 50},
            "accounts/fireworks/models/kimi-k2p6",
        )
    )

    assert payload["top_k"] == 50
    assert "thinking" not in payload
    assert "temperature" not in payload
    assert "top_p" not in payload
    assert not any(change["field"] == "top_k" for change in report["field_changes"])


def test_anthropic_messages_adapter_accepts_official_tool_choice_object_shape(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    async def fake_build_proxy_context_from_body(request, body):
        return SimpleNamespace(
            body=body,
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )

    async def fake_proxy_fireworks_request(*args, **kwargs):
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post(
        "/v1/messages",
        headers=_anthropic_headers(),
        json={
            "model": "kimi-k2.6",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 1,
            "tools": [{"name": "calculator", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "calculator"},
        },
    )
    assert response.status_code == 200


def test_anthropic_messages_adapter_accepts_tool_choice_disable_parallel(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    async def fake_build_proxy_context_from_body(request, body):
        return SimpleNamespace(
            body=body,
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )

    async def fake_proxy_fireworks_request(*args, **kwargs):
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post(
        "/v1/messages",
        headers=_anthropic_headers(),
        json={
            "model": "kimi-k2.6",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 1,
            "tools": [{"name": "calculator", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
        },
    )

    assert response.status_code == 200


def test_anthropic_messages_adapter_accepts_file_image_and_cache_control(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    async def fake_build_proxy_context_from_body(request, body):
        return SimpleNamespace(
            body=body,
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )

    async def fake_proxy_fireworks_request(*args, **kwargs):
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post(
        "/v1/messages",
        headers=_anthropic_headers(),
        json={
            "model": "kimi-k2.6",
            "messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "file", "file_id": "file_123"}, "cache_control": {"type": "ephemeral"}}, {"type": "text", "text": "describe"}]}],
            "max_tokens": 1,
        },
    )

    assert response.status_code == 200


def test_anthropic_messages_adapter_rejects_nonempty_anthropic_version(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    response = client.post(
        "/v1/messages",
        headers={"Authorization": "Bearer token", "anthropic-version": "   "},
        json={"model": "kimi-k2.6", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1},
    )
    assert response.status_code == 400


def test_anthropic_messages_adapter_ignores_anthropic_version_header(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    captured = {}

    async def fake_build_proxy_context_from_body(request, body):
        return SimpleNamespace(
            body=body,
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={"anthropic-version": "2023-06-01"},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )

    async def fake_proxy_fireworks_request(context, *, endpoint, upstream_path, payload, headers):
        captured.update({"payload": payload, "headers": headers})
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post("/v1/messages", headers=_anthropic_headers(), json={"model": "kimi-k2.6", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1})
    assert response.status_code == 200
    assert "anthropic-version" not in captured["headers"]


def test_anthropic_messages_adapter_missing_anthropic_version_is_rejected(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    response = client.post(
        "/v1/messages",
        headers={"Authorization": "Bearer token"},
        json={"model": "kimi-k2.6", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1},
    )
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "anthropic-version header is required"


def test_anthropic_messages_adapter_rejects_invalid_json(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    response = client.post("/v1/messages", headers=_anthropic_headers(), data="{")
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "invalid JSON body"


def test_anthropic_messages_adapter_rejects_non_object_json(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    response = client.post("/v1/messages", headers=_anthropic_headers(), json=[1, 2])
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "JSON body must be an object"


def test_anthropic_messages_adapter_validates_image_blocks(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    async def fake_build_proxy_context_from_body(request, body):
        return SimpleNamespace(
            body=body,
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    async def fake_proxy_fireworks_request(*args, **kwargs):
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)
    valid = {"model": "kimi-k2.6", "messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc"}}]}], "max_tokens": 1}
    response = client.post("/v1/messages", headers=_anthropic_headers(), json=valid)
    assert response.status_code == 200

    invalid = {"model": "kimi-k2.6", "messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/bmp", "data": "abc"}}]}], "max_tokens": 1}
    response = client.post("/v1/messages", headers=_anthropic_headers(), json=invalid)
    assert response.status_code == 400


def test_anthropic_messages_adapter_rejects_malformed_image_block(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    invalid = {"model": "kimi-k2.6", "messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg"}}]}], "max_tokens": 1}
    response = client.post("/v1/messages", headers=_anthropic_headers(), json=invalid)
    assert response.status_code == 400


def test_anthropic_messages_adapter_supports_url_image_blocks(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    async def fake_build_proxy_context_from_body(request, body):
        return SimpleNamespace(
            body=body,
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )

    async def fake_proxy_fireworks_request(*args, **kwargs):
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post(
        "/v1/messages",
        headers=_anthropic_headers(),
        json={
            "model": "kimi-k2.6",
            "messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "url", "url": "https://example.com/image.png"}}]}],
            "max_tokens": 1,
        },
    )
    assert response.status_code == 200


def test_anthropic_messages_adapter_accepts_http_url_image_block(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    async def fake_build_proxy_context_from_body(request, body):
        return SimpleNamespace(
            body=body,
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )

    async def fake_proxy_fireworks_request(*args, **kwargs):
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post(
        "/v1/messages",
        headers=_anthropic_headers(),
        json={
            "model": "kimi-k2.6",
            "messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "url", "url": "http://example.com/image.png"}}]}],
            "max_tokens": 1,
        },
    )
    # Fireworks AnthropicURLImageSource.url is type: string with no scheme
    # constraint; an http:// URL is forwarded as-is and upstream decides.
    assert response.status_code == 200


def test_anthropic_messages_adapter_accepts_empty_base64_image_data(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    async def fake_build_proxy_context_from_body(request, body):
        return SimpleNamespace(
            body=body,
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )

    async def fake_proxy_fireworks_request(*args, **kwargs):
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post(
        "/v1/messages",
        headers=_anthropic_headers(),
        json={
            "model": "kimi-k2.6",
            "messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": ""}}]}],
            "max_tokens": 1,
        },
    )
    # Fireworks base64 image data is type: string with no minLength; an empty
    # data string is forwarded as-is and upstream decides.
    assert response.status_code == 200


def test_anthropic_messages_adapter_accepts_missing_max_tokens(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    async def fake_build_proxy_context_from_body(request, body):
        return SimpleNamespace(
            body=body,
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )

    async def fake_proxy_fireworks_request(*args, **kwargs):
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post(
        "/v1/messages",
        headers=_anthropic_headers(),
        json={"model": "kimi-k2.6", "messages": [{"role": "user", "content": "hello"}]},
    )
    # Fireworks Anthropic schema only requires model and messages; max_tokens
    # is optional and a missing max_tokens must not be rejected.
    assert response.status_code == 200


def test_anthropic_messages_adapter_omits_default_service_tier() -> None:
    payload, _, _ = build_messages_adapter(
        SimpleNamespace(
            body={"model": "kimi-k2.6", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1, "service_tier": "default"},
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )
    )

    assert "service_tier" not in payload


def test_anthropic_messages_adapter_omits_auto_service_tier() -> None:
    payload, _, _ = build_messages_adapter(
        SimpleNamespace(
            body={"model": "kimi-k2.6", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1, "service_tier": "auto"},
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )
    )

    assert "service_tier" not in payload


def test_anthropic_messages_adapter_rejects_invalid_top_level_values(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    base = {
        "model": "kimi-k2.6",
        "messages": [{"role": "user", "content": "hello"}],
    }
    invalid_cases = [
        ({"model": ""}, "model"),
        ({"messages": {}}, "messages"),
        ({"max_tokens": 0}, "max_tokens"),
        ({"stream": "yes"}, "stream"),
        ({"temperature": 1.1}, "temperature"),
        ({"top_p": -0.1}, "top_p"),
        ({"top_k": -1}, "top_k"),
        ({"stop_sequences": ["ok", 1]}, "stop_sequences"),
        ({"thinking": []}, "thinking"),
        ({"output_config": []}, "output_config"),
        ({"metadata": []}, "metadata"),
        ({"tools": {}}, "tools"),
        ({"tool_choice": 1}, "tool_choice"),
        ({"raw_output": "true"}, "raw_output"),
    ]

    for overrides, field in invalid_cases:
        response = client.post("/v1/messages", headers=_anthropic_headers(), json={**base, "max_tokens": 1, **overrides})
        assert response.status_code == 400
        assert response.json() == {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": {
                    "model": "'model' must be a non-empty string",
                    "messages": "'messages' must be a list",
                    "max_tokens": "'max_tokens' must be a positive integer",
                    "stream": "'stream' must be a boolean",
                    "temperature": "'temperature' must be between 0 and 1",
                    "top_p": "'top_p' must be between 0 and 1",
                    "top_k": "'top_k' must be a non-negative integer",
                    "stop_sequences": "'stop_sequences' must be a list of strings",
                    "thinking": "'thinking' must be an object",
                    "output_config": "'output_config' must be an object",
                    "metadata": "'metadata' must be an object",
                    "tools": "'tools' must be a list",
                    "tool_choice": "'tool_choice' must be a string or object",
                    "raw_output": "'raw_output' must be a boolean",
                }[field],
                "param": field,
                "code": "invalid_request_error",
            },
        }


def test_anthropic_messages_adapter_validates_tools_tool_choice_and_thinking(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    base = {"model": "kimi-k2.6", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1}

    async def fake_build_proxy_context_from_body(request, body):
        return SimpleNamespace(
            body=body,
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )

    async def fake_proxy_fireworks_request(*args, **kwargs):
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post(
        "/v1/messages",
        headers=_anthropic_headers(),
        json={**base, "tools": [{"name": "calc", "description": "x", "input_schema": {"type": "object"}}], "tool_choice": {"type": "tool", "name": "calc"}, "thinking": {"type": "enabled", "budget_tokens": 1024}, "max_tokens": 2048},
    )
    assert response.status_code == 200

    invalid_payloads = [
        {"tools": [{}]},
        {"tool_choice": {"type": "tool"}},
        {"tool_choice": {"bad": True}},
        {"tool_choice": {"type": "auto", "name": "x"}},
        {"thinking": {"type": "enabled", "budget_tokens": 512}},
        {"thinking": {"type": "enabled", "budget_tokens": 4096}, "max_tokens": 2048},
    ]
    for overrides in invalid_payloads:
        response = client.post("/v1/messages", headers=_anthropic_headers(), json={**base, **overrides})
        assert response.status_code == 400


def test_anthropic_messages_adapter_validates_tool_blocks(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    base = {"model": "kimi-k2.6", "max_tokens": 1}

    async def fake_build_proxy_context_from_body(request, body):
        return SimpleNamespace(
            body=body,
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )

    async def fake_proxy_fireworks_request(*args, **kwargs):
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    valid = {
        **base,
        "messages": [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "tool-1", "name": "calc", "input": {}}]},
            {"role": "user", "content": "ok"},
        ],
        "tools": [{"name": "calc", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "tool", "name": "calc"},
    }
    response = client.post("/v1/messages", headers=_anthropic_headers(), json=valid)
    assert response.status_code == 200

    invalid_cases = [
        ({"messages": [{"role": "assistant", "content": [{"type": "tool_use", "name": "calc", "input": {}}]}]}, "messages"),
        ({"messages": [{"role": "assistant", "content": [{"type": "tool_use", "id": "tool-1", "input": {}}]}]}, "messages"),
        ({"messages": [{"role": "assistant", "content": [{"type": "tool_use", "id": "tool-1", "name": "calc", "input": []}]}]}, "messages"),
        ({"messages": [{"role": "assistant", "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "ok"}]}]}, "messages"),
        ({"messages": [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool-1", "is_error": "no"}]}]}, "messages"),
        ({"messages": [{"role": "user", "content": [{"type": "tool_use", "id": "tool-1", "name": "calc", "input": {}}]}]}, "messages"),
    ]
    for overrides, param in invalid_cases:
        response = client.post("/v1/messages", headers=_anthropic_headers(), json={**base, **overrides})
        assert response.status_code == 400
        assert response.json()["error"]["param"] == param


def test_anthropic_messages_routes_forward_stream_and_preserve_streaming_response(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {**_anthropic_headers(), "x-fireworks2api-messages-mode": "native"}

    captured = {}

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", lambda request, body: fake_build_proxy_context(request))

    async def fake_build_proxy_context(request):
        return SimpleNamespace(
            body={"model": "kimi-k2.6", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1, "stream": True, "service_tier": "priority"},
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )

    async def fake_proxy_fireworks_request(context, *, endpoint, upstream_path, payload, headers):
        captured.update({"payload": payload})
        return StreamingResponse(iter([b"event: message\ndata: {}\n\n"]), media_type="text/event-stream")

    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post("/v1/messages", headers=headers, json={"model": "kimi-k2.6", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1, "stream": True, "service_tier": "priority"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text == "event: message\ndata: {}\n\n"
    assert captured["payload"]["stream"] is True
    assert captured["payload"]["service_tier"] == "priority"


def test_anthropic_messages_routes_forward_tool_round_trip_payload(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {**_anthropic_headers(), "x-fireworks2api-messages-mode": "native"}
    captured_payloads = []

    async def fake_build_proxy_context_from_body(request, body):
        return SimpleNamespace(
            body=body,
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={"anthropic-beta": "tools-2024-01-01", "x-session-affinity": "session-affinity"},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )

    async def fake_proxy_fireworks_request(context, *, endpoint, upstream_path, payload, headers):
        captured_payloads.append(payload)
        if len(captured_payloads) == 1:
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "id": "msg-1",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "tool-1", "name": "calculator", "input": {"expression": "1+1"}}],
                },
            )
        return SimpleNamespace(status_code=200, json=lambda: {"id": "msg-2", "type": "message", "role": "assistant", "content": [{"type": "text", "text": "done"}]})

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    first = client.post(
        "/v1/messages",
        headers=headers,
        json={
            "model": "kimi-k2.6",
            "messages": [{"role": "user", "content": "what is 1+1?"}],
            "max_tokens": 1,
            "tools": [{"name": "calculator", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "auto"},
        },
    )
    assert first.status_code == 200
    assert captured_payloads[0]["messages"] == [{"role": "user", "content": "what is 1+1?"}]
    assert "anthropic-beta" not in captured_payloads[0]

    second = client.post(
        "/v1/messages",
        headers=headers,
        json={
            "model": "kimi-k2.6",
            "messages": [
                {"role": "user", "content": "what is 1+1?"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "tool-1", "name": "calculator", "input": {"expression": "1+1"}}]},
            ],
            "max_tokens": 1,
        },
    )
    assert second.status_code == 200
    assert captured_payloads[1]["messages"][1]["content"] == [{"type": "tool_use", "id": "tool-1", "name": "calculator", "input": {"expression": "1+1"}}]
    assert "anthropic-beta" not in captured_payloads[1]


def test_anthropic_messages_routes_preserve_preopen_http_error_response(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = _anthropic_headers()

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", lambda request, body: fake_build_proxy_context(request))

    async def fake_build_proxy_context(request):
        return SimpleNamespace(
            body={"model": "kimi-k2.6", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1},
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )

    async def fake_proxy_fireworks_request(context, *, endpoint, upstream_path, payload, headers):
        return StreamingResponse(iter([b'{"error":{"message":"bad upstream"}}']), media_type="application/json", status_code=503)

    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post("/v1/messages", headers=headers, json={"model": "kimi-k2.6", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1})
    assert response.status_code == 503
    assert response.json()["error"]["message"] == "bad upstream"


def test_anthropic_messages_routes_preserve_terminal_stream_error_event(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = _anthropic_headers()

    monkeypatch.setattr(anthropic_router, "build_proxy_context_from_body", lambda request, body: fake_build_proxy_context(request))

    async def fake_build_proxy_context(request):
        return SimpleNamespace(
            body={"model": "kimi-k2.6", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1, "stream": True},
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/kimi-k2p6"),
            request_headers={},
            stable_key="stable-key",
            settings=SimpleNamespace(affinity_hash_secret="affinity", log_hash_secret="log"),
        )

    async def fake_proxy_fireworks_request(context, *, endpoint, upstream_path, payload, headers):
        return StreamingResponse(iter([b'event: error\ndata: {"message":"boom"}\n\n']), media_type="text/event-stream")

    monkeypatch.setattr(anthropic_router, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post("/v1/messages", headers=headers, json={"model": "kimi-k2.6", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1, "stream": True})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: error" in response.text


def test_anthropic_messages_adapter_rejects_non_boolean_stream(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    response = client.post(
        "/v1/messages",
        headers=_anthropic_headers(),
        json={"model": "kimi-k2.6", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1, "stream": "true"},
    )
    assert response.status_code == 400
    assert response.json() == {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "'stream' must be a boolean",
            "param": "stream",
            "code": "invalid_request_error",
        },
    }


def test_anthropic_messages_adapter_rejects_invalid_service_tier(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    response = client.post(
        "/v1/messages",
        headers=_anthropic_headers(),
        json={"model": "kimi-k2.6", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1, "service_tier": "super-fast"},
    )
    assert response.status_code == 400
    assert response.json() == {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "unsupported service_tier",
            "param": "service_tier",
            "code": "unsupported_parameter",
        },
    }


def test_anthropic_messages_errors_do_not_leak_sensitive_fireworks_keys(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    response = client.post(
        "/v1/messages",
        headers=_anthropic_headers(),
        json={"model": "kimi-k2.6", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1, "vendor": "x"},
    )
    assert response.status_code == 400
    assert "fw_" not in response.text
    assert "Authorization" not in response.text
