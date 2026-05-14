from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pytest import MonkeyPatch

from app.control.repository import AppRepository
from app.main import app
from app.platform.storage.db import init_db
import app.platform.auth as auth
import app.products.openai.responses as responses_mod


client = TestClient(app)


def _require_auth(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token=None))


def _mock_context(*, model: str = "test"):
    settings = SimpleNamespace(
        log_hash_secret="secret",
        affinity_hash_secret="affinity-secret",
        request_log_retention=30,
    )
    key = SimpleNamespace(name="key-1", api_key="fw-test-key", fingerprint="fp-1")
    repository = SimpleNamespace(
        insert_request_log=lambda *args, **kwargs: None,
        get_response_key_route=lambda response_id: None,
        upsert_response_key_route=lambda response_id, key: None,
        delete_response_key_route=lambda response_id: None,
    )
    resolved_model = SimpleNamespace(upstream_model="accounts/fireworks/models/test")
    return SimpleNamespace(
        settings=settings,
        repository=repository,
        body={"model": model},
        model_name=model,
        resolved_model=resolved_model,
        stable_key="stable",
        route_key="route",
        affinity_header="affinity",
        request_headers={"authorization": "Bearer token"},
        selected_keys=[key],
    )


def test_list_responses_proxies_get(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    captured: dict[str, object] = {}

    async def fake_build_proxy_key_context(request, *, route_seed):
        return _mock_context()

    async def fake_proxy_fireworks_json_request(context, **kwargs):
        captured.update(kwargs)
        captured["context"] = context
        return JSONResponse({"data": []})

    monkeypatch.setattr(responses_mod, "build_proxy_key_context", fake_build_proxy_key_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_json_request", fake_proxy_fireworks_json_request)

    response = client.get("/v1/responses?limit=2", headers={"Authorization": "Bearer token"})

    assert response.status_code == 200
    assert captured["method"] == "GET"
    assert captured["upstream_path"] == "v1/responses"
    assert captured["params"] == {"limit": 2}


def test_list_responses_validates_query_and_preserves_forwarding(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    captured: dict[str, object] = {}

    async def fake_build_proxy_key_context(request, *, route_seed):
        return _mock_context()

    async def fake_proxy_fireworks_json_request(context, **kwargs):
        captured.update(kwargs)
        return JSONResponse({"data": []})

    monkeypatch.setattr(responses_mod, "build_proxy_key_context", fake_build_proxy_key_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_json_request", fake_proxy_fireworks_json_request)

    response = client.get(
        "/v1/responses?limit=100&after=resp_1&before=resp_2",
        headers={"Authorization": "Bearer token"},
    )

    assert response.status_code == 200
    assert captured["params"] == {"limit": 100, "after": "resp_1", "before": "resp_2"}


def test_get_response_prefers_key_bound_to_response_id(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    routed_key = SimpleNamespace(name="key-routed", api_key="fw-routed", fingerprint="fp-routed")
    captured: dict[str, object] = {}

    async def fake_build_proxy_key_context(request, *, route_seed):
        context = _mock_context()
        context.repository.get_response_key_route = lambda response_id: routed_key
        return context

    async def fake_proxy_fireworks_json_request(context, **kwargs):
        captured.update(kwargs)
        captured["selected_keys"] = context.selected_keys
        return JSONResponse({"id": "resp_1", "object": "response"})

    monkeypatch.setattr(responses_mod, "build_proxy_key_context", fake_build_proxy_key_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_json_request", fake_proxy_fireworks_json_request)

    response = client.get("/v1/responses/resp_1", headers={"Authorization": "Bearer token"})

    assert response.status_code == 200
    assert captured["method"] == "GET"
    assert captured["upstream_path"] == "v1/responses/resp_1"
    assert captured["selected_keys"] == [routed_key]


def test_create_response_with_previous_response_id_prefers_bound_key(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    routed_key = SimpleNamespace(name="key-routed", api_key="fw-routed", fingerprint="fp-routed")
    original_key = SimpleNamespace(name="key-original", api_key="fw-original", fingerprint="fp-original")
    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        ctx = _mock_context()
        ctx.body = body
        ctx.selected_keys = [original_key]
        ctx.repository.get_response_key_route = lambda response_id: routed_key if response_id == "resp_prev" else None
        return ctx

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        captured["selected_keys"] = context.selected_keys
        return JSONResponse({"id": "resp_next", "object": "response"})

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post("/v1/responses", headers={"Authorization": "Bearer token"}, json={"model": "test", "input": "continue", "previous_response_id": "resp_prev"})

    assert response.status_code == 200
    assert captured["selected_keys"] == [routed_key]
    assert captured["payload"]["previous_response_id"] == "resp_prev"


def test_create_response_retries_without_missing_previous_response_id(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    deleted: list[str] = []
    captured_payloads: list[dict] = []

    async def fake_build_proxy_context(request, body):
        ctx = _mock_context()
        ctx.body = body
        ctx.repository.delete_response_key_route = deleted.append
        return ctx

    async def fake_proxy_fireworks_request(context, **kwargs):
        retry = kwargs["retry_payload_on_error"]
        first_payload = kwargs["payload"]
        captured_payloads.append(first_payload)
        retry_payload = retry(404, '{"error":{"message":"Previous response not found"}}')
        assert retry_payload is not None
        captured_payloads.append(retry_payload)
        return JSONResponse({"id": "resp_retry", "object": "response"})

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={"model": "test", "input": "continue", "previous_response_id": "resp_missing"},
    )

    assert response.status_code == 200
    assert captured_payloads[0]["previous_response_id"] == "resp_missing"
    assert "previous_response_id" not in captured_payloads[1]
    assert deleted == ["resp_missing"]


@pytest.mark.parametrize(
    ("upstream_base_url", "expected_base_path"),
    [
        ("https://api.fireworks.ai/inference", "v1/responses"),
        ("https://api.fireworks.ai/inference/v1", "responses"),
    ],
)
def test_responses_lifecycle_uses_resolve_inference_path_for_get_and_delete(monkeypatch: MonkeyPatch, upstream_base_url: str, expected_base_path: str) -> None:
    _require_auth(monkeypatch)

    captured: dict[str, object] = {}

    async def fake_build_proxy_key_context(request, *, route_seed):
        ctx = _mock_context()
        ctx.settings.upstream_base_url = upstream_base_url
        return ctx

    async def fake_proxy_fireworks_json_request(context, **kwargs):
        captured.update(kwargs)
        return Response(status_code=204)

    monkeypatch.setattr(responses_mod, "build_proxy_key_context", fake_build_proxy_key_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_json_request", fake_proxy_fireworks_json_request)

    response = client.get("/v1/responses/resp_123", headers={"Authorization": "Bearer token"})
    assert response.status_code == 204
    assert captured["upstream_path"] == f"{expected_base_path}/resp_123"

    captured.clear()
    response = client.delete("/v1/responses/resp_123", headers={"Authorization": "Bearer token"})
    assert response.status_code == 204
    assert captured["upstream_path"] == f"{expected_base_path}/resp_123"


def test_response_key_route_repository_crud(tmp_path) -> None:
    db_path = tmp_path / "responses-routes.sqlite3"
    init_db(db_path)
    repository = AppRepository(db_path)
    repository.upsert_key("key-1", "fw-test-key", enabled=True)
    key = repository.get_key("key-1")
    assert key is not None

    repository.upsert_response_key_route("resp_1", key)
    routed = repository.get_response_key_route("resp_1")

    assert routed is not None
    assert routed.name == "key-1"

    repository.delete_response_key_route("resp_1")

    assert repository.get_response_key_route("resp_1") is None


def test_list_responses_rejects_invalid_query_with_openai_error(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    async def fake_build_proxy_key_context(request, *, route_seed):
        return _mock_context()

    monkeypatch.setattr(responses_mod, "build_proxy_key_context", fake_build_proxy_key_context)

    response = client.get("/v1/responses?limit=101", headers={"Authorization": "Bearer token"})

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["param"] == "limit"


def test_list_responses_error_shape_is_openai_style(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    async def fake_build_proxy_key_context(request, *, route_seed):
        return _mock_context()

    monkeypatch.setattr(responses_mod, "build_proxy_key_context", fake_build_proxy_key_context)

    response = client.get("/v1/responses?limit=0", headers={"Authorization": "Bearer token"})

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert response.json()["error"]["param"] == "limit"


def test_create_responses_stream_forwards_stream_and_cache_fields(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        return SimpleNamespace(
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", responses_cache_fields_enabled=True, request_log_retention=30),
            repository=SimpleNamespace(insert_request_log=lambda *args, **kwargs: None),
            body={
                "model": "test",
                "input": "hello",
                "stream": True,
                "perf_metrics_in_response": True,
                "prompt_cache_key": "cache-1",
                "prompt_cache_isolation_key": "iso-1",
            },
            model_name="test",
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
            stable_key="stable",
            route_key="route",
            affinity_header="affinity",
            request_headers={"authorization": "Bearer token"},
            selected_keys=[SimpleNamespace(name="key-1", api_key="fw-test-key")],
        )

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        captured["context"] = context
        return StreamingResponse(iter([b"data: {\"id\":\"resp_1\"}\n\n"]), media_type="text/event-stream")

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={
            "model": "test",
            "input": "hello",
            "stream": True,
            "perf_metrics_in_response": True,
            "prompt_cache_key": "cache-1",
            "prompt_cache_isolation_key": "iso-1",
        },
    )

    assert response.status_code == 200
    assert captured["payload"]["stream"] is True
    stream_transform = captured["stream_transform_factory"]()
    assert stream_transform.__class__ is responses_mod.ResponsesSSECanonicalizer
    assert stream_transform._sub2api_bridge_compat is True
    assert captured["payload"]["perf_metrics_in_response"] is True
    assert captured["payload"]["prompt_cache_key"] == "cache-1"
    assert captured["payload"]["prompt_cache_isolation_key"] == "iso-1"
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.startswith("data: {\"id\":\"resp_1\"}")


def test_create_responses_stream_uses_sub2api_style_transform(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        return SimpleNamespace(
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", responses_cache_fields_enabled=True, request_log_retention=30),
            repository=SimpleNamespace(insert_request_log=lambda *args, **kwargs: None),
            body={"model": "test", "input": "hello", "stream": True},
            model_name="test",
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
            stable_key="stable",
            route_key="route",
            affinity_header="affinity",
            request_headers={"authorization": "Bearer token"},
            selected_keys=[SimpleNamespace(name="key-1", api_key="fw-test-key")],
        )

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        return StreamingResponse(iter([b"data: {}\n\n"]), media_type="text/event-stream")

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post("/v1/responses", headers={"Authorization": "Bearer token"}, json={"model": "test", "input": "hello", "stream": True})

    assert response.status_code == 200
    assert captured["stream_transform_factory"]()._sub2api_bridge_compat is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tools", "bad"),
        ("tool_choice", 1),
        ("text", "bad"),
        ("reasoning", []),
        ("metadata", []),
    ],
)
def test_create_responses_rejects_invalid_nested_advanced_fields(monkeypatch: MonkeyPatch, field, value) -> None:
    _require_auth(monkeypatch)

    async def fake_build_proxy_context(request, body):
        return SimpleNamespace(
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", responses_cache_fields_enabled=True, request_log_retention=30),
            repository=SimpleNamespace(insert_request_log=lambda *args, **kwargs: None),
            body={"model": "test", "input": "hello", field: value},
            model_name="test",
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
            stable_key="stable",
            route_key="route",
            affinity_header="affinity",
            request_headers={"authorization": "Bearer token"},
            selected_keys=[SimpleNamespace(name="key-1", api_key="fw-test-key")],
        )

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)

    response = client.post("/v1/responses", headers={"Authorization": "Bearer token"}, json={"model": "test", "input": "hello", field: value})

    assert response.status_code == 400
    assert response.json()["error"]["param"] == field


def test_create_responses_forwards_supported_nested_advanced_fields(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    captured: dict[str, object] = {}

    async def fake_build_proxy_context(request, body):
        return SimpleNamespace(
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", responses_cache_fields_enabled=True, request_log_retention=30),
            repository=SimpleNamespace(insert_request_log=lambda *args, **kwargs: None),
            body={
                "model": "test",
                "input": "hello",
                "tools": [{"type": "function", "function": {"name": "lookup"}}],
                "tool_choice": {"type": "function", "function": {"name": "lookup"}},
                "text": {"format": {"type": "text"}},
                "reasoning": {"effort": "high"},
                "metadata": {"trace": "ok"},
            },
            model_name="test",
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
            stable_key="stable",
            route_key="route",
            affinity_header="affinity",
            request_headers={"authorization": "Bearer token"},
            selected_keys=[SimpleNamespace(name="key-1", api_key="fw-test-key")],
        )

    async def fake_proxy_fireworks_request(context, **kwargs):
        captured.update(kwargs)
        return JSONResponse({"id": "resp_1"})

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={
            "model": "test",
            "input": "hello",
            "tools": [{"type": "function", "function": {"name": "lookup"}}],
            "tool_choice": {"type": "function", "function": {"name": "lookup"}},
            "text": {"format": {"type": "text"}},
            "reasoning": {"effort": "high"},
            "metadata": {"trace": "ok"},
        },
    )

    assert response.status_code == 200
    assert captured["payload"]["tools"][0]["function"]["name"] == "lookup"
    assert captured["payload"]["tool_choice"]["type"] == "function"
    assert captured["payload"]["text"] == {"format": {"type": "text"}}
    assert captured["payload"]["reasoning"] == {"effort": "high"}
    assert captured["payload"]["metadata"] == {"trace": "ok"}


def test_create_responses_non_priority_service_tier_error_shape(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    async def fake_build_proxy_context(request, body):
        return SimpleNamespace(
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", responses_cache_fields_enabled=True, request_log_retention=30),
            repository=SimpleNamespace(insert_request_log=lambda *args, **kwargs: None),
            body={"model": "test", "input": "hello", "service_tier": "standard"},
            model_name="test",
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
            stable_key="stable",
            route_key="route",
            affinity_header="affinity",
            request_headers={"authorization": "Bearer token"},
            selected_keys=[SimpleNamespace(name="key-1", api_key="fw-test-key")],
        )

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)

    response = client.post("/v1/responses", headers={"Authorization": "Bearer token"}, json={"model": "test", "input": "hello", "service_tier": "standard"})

    assert response.status_code == 400
    assert response.json()["error"] == {"message": "service_tier is not supported for responses", "type": "invalid_request_error", "param": "service_tier", "code": "unsupported_parameter"}


def test_create_responses_preserves_preopen_http_error_response(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    async def fake_build_proxy_context(request, body):
        return SimpleNamespace(
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", request_log_retention=30),
            repository=SimpleNamespace(insert_request_log=lambda *args, **kwargs: None),
            body={"model": "test", "input": "hello"},
            model_name="test",
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
            stable_key="stable",
            route_key="route",
            affinity_header="affinity",
            request_headers={"authorization": "Bearer token"},
            selected_keys=[SimpleNamespace(name="key-1", api_key="fw-test-key")],
        )

    async def fake_proxy_fireworks_request(context, **kwargs):
        return Response(status_code=502, content='{"error":{"message":"upstream down"}}', media_type="application/json")

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post("/v1/responses", headers={"Authorization": "Bearer token"}, json={"model": "test", "input": "hello"})

    assert response.status_code == 502
    assert response.json()["error"]["message"] == "upstream down"


def test_create_responses_preserves_terminal_stream_error_event(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    async def fake_build_proxy_context(request, body):
        return SimpleNamespace(
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", request_log_retention=30),
            repository=SimpleNamespace(insert_request_log=lambda *args, **kwargs: None),
            body={"model": "test", "input": "hello", "stream": True},
            model_name="test",
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
            stable_key="stable",
            route_key="route",
            affinity_header="affinity",
            request_headers={"authorization": "Bearer token"},
            selected_keys=[SimpleNamespace(name="key-1", api_key="fw-test-key")],
        )

    async def fake_proxy_fireworks_request(context, **kwargs):
        return StreamingResponse(iter([b'event: error\ndata: {"message":"boom"}\n\n']), media_type="text/event-stream")

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post("/v1/responses", headers={"Authorization": "Bearer token"}, json={"model": "test", "input": "hello", "stream": True})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: error" in response.text


def test_unversioned_responses_alias_is_not_registered(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    response = client.post("/responses", headers={"Authorization": "Bearer token"}, json={"model": "test", "input": "hello"})

    assert response.status_code == 404


def test_get_response_proxies_path(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    captured: dict[str, object] = {}

    async def fake_build_proxy_key_context(request, *, route_seed):
        return _mock_context()

    async def fake_proxy_fireworks_json_request(context, **kwargs):
        captured.update(kwargs)
        return JSONResponse({"id": "resp_123"})

    monkeypatch.setattr(responses_mod, "build_proxy_key_context", fake_build_proxy_key_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_json_request", fake_proxy_fireworks_json_request)

    response = client.get("/v1/responses/resp_123", headers={"Authorization": "Bearer token"})

    assert response.status_code == 200
    assert captured["method"] == "GET"
    assert captured["upstream_path"] == "v1/responses/resp_123"


def test_get_response_rejects_invalid_before_query(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    async def fake_build_proxy_key_context(request, *, route_seed):
        return _mock_context()

    monkeypatch.setattr(responses_mod, "build_proxy_key_context", fake_build_proxy_key_context)

    response = client.get("/v1/responses/resp_123?before=", headers={"Authorization": "Bearer token"})

    assert response.status_code == 400
    assert response.json()["error"]["param"] == "before"


def test_delete_response_proxies_delete(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    captured: dict[str, object] = {}

    async def fake_build_proxy_key_context(request, *, route_seed):
        return _mock_context()

    async def fake_proxy_fireworks_json_request(context, **kwargs):
        captured.update(kwargs)
        return Response(status_code=204)

    monkeypatch.setattr(responses_mod, "build_proxy_key_context", fake_build_proxy_key_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_json_request", fake_proxy_fireworks_json_request)

    response = client.delete("/v1/responses/resp_123", headers={"Authorization": "Bearer token"})

    assert response.status_code == 204
    assert captured["method"] == "DELETE"
    assert captured["upstream_path"] == "v1/responses/resp_123"


def test_delete_response_rejects_invalid_after_query(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    async def fake_build_proxy_key_context(request, *, route_seed):
        return _mock_context()

    monkeypatch.setattr(responses_mod, "build_proxy_key_context", fake_build_proxy_key_context)

    response = client.delete("/v1/responses/resp_123?after=", headers={"Authorization": "Bearer token"})

    assert response.status_code == 400
    assert response.json()["error"]["param"] == "after"


def test_delete_response_clears_response_key_route_on_success(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    deleted: list[str] = []

    async def fake_build_proxy_key_context(request, *, route_seed):
        ctx = _mock_context()
        ctx.repository.get_response_key_route = lambda response_id: SimpleNamespace(name="key-routed", api_key="fw-routed", fingerprint="fp-routed")
        ctx.repository.delete_response_key_route = lambda response_id: deleted.append(response_id)
        return ctx

    async def fake_proxy_fireworks_json_request(context, **kwargs):
        return Response(status_code=204)

    monkeypatch.setattr(responses_mod, "build_proxy_key_context", fake_build_proxy_key_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_json_request", fake_proxy_fireworks_json_request)

    response = client.delete("/v1/responses/resp_123", headers={"Authorization": "Bearer token"})

    assert response.status_code == 204
    assert deleted == ["resp_123"]


def test_responses_lifecycle_routes_do_not_require_json_body(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    captured: dict[str, object] = {}

    async def fake_build_proxy_key_context(request, *, route_seed):
        return SimpleNamespace(
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", request_log_retention=30),
            repository=SimpleNamespace(insert_request_log=lambda *args, **kwargs: None),
            request_headers={"x-session-affinity": "affinity"},
            body={},
            model_name=None,
            resolved_model=SimpleNamespace(upstream_model=route_seed),
            stable_key="stable",
            stable_key_source="route_seed",
            route_key="responses:stable",
            affinity_header="affinity",
            selected_keys=[SimpleNamespace(name="key-1", api_key="fw-test-key")],
        )

    async def fake_proxy_fireworks_json_request(context, **kwargs):
        captured.update(kwargs)
        captured["context"] = context
        return JSONResponse({"data": []})

    monkeypatch.setattr(responses_mod, "build_proxy_key_context", fake_build_proxy_key_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_json_request", fake_proxy_fireworks_json_request)

    response = client.get("/v1/responses?limit=2", headers={"Authorization": "Bearer token"})

    assert response.status_code == 200
    assert captured["method"] == "GET"


def test_responses_stream_preserves_terminal_usage(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    async def fake_build_proxy_context(request, body):
        return SimpleNamespace(
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", request_log_retention=30),
            repository=SimpleNamespace(insert_request_log=lambda *args, **kwargs: None),
            body={"model": "test", "input": "hello", "stream": True},
            model_name="test",
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
            stable_key="stable",
            route_key="route",
            affinity_header="affinity",
            request_headers={"authorization": "Bearer token"},
            selected_keys=[SimpleNamespace(name="key-1", api_key="fw-test-key")],
        )

    async def fake_proxy_fireworks_request(context, **kwargs):
        return StreamingResponse(iter([b'event: response.completed\ndata: {"usage":{"input_tokens":3,"output_tokens":1}}\n\n']), media_type="text/event-stream")

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post("/v1/responses", headers={"Authorization": "Bearer token"}, json={"model": "test", "input": "hello", "stream": True})

    assert response.status_code == 200
    assert "event: response.completed" in response.text


def test_responses_stream_preserves_event_names_and_bodies(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    async def fake_build_proxy_context(request, body):
        return SimpleNamespace(
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", request_log_retention=30),
            repository=SimpleNamespace(insert_request_log=lambda *args, **kwargs: None),
            body={"model": "test", "input": "hello", "stream": True},
            model_name="test",
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
            stable_key="stable",
            route_key="route",
            affinity_header="affinity",
            request_headers={"authorization": "Bearer token"},
            selected_keys=[SimpleNamespace(name="key-1", api_key="fw-test-key")],
        )

    async def fake_proxy_fireworks_request(context, **kwargs):
        chunks = [
            b'event: response.created\ndata: {"id":"resp_1","object":"response"}\n\n',
            b'event: response.output_text.delta\ndata: {"delta":"hello"}\n\n',
            b'event: response.completed\ndata: {"id":"resp_1","usage":{"input_tokens":3,"output_tokens":1}}\n\n',
        ]
        return StreamingResponse(iter(chunks), media_type="text/event-stream")

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)
    monkeypatch.setattr(responses_mod, "proxy_fireworks_request", fake_proxy_fireworks_request)

    response = client.post("/v1/responses", headers={"Authorization": "Bearer token"}, json={"model": "test", "input": "hello", "stream": True})

    assert response.status_code == 200
    assert "event: response.created" in response.text
    assert 'data: {"id":"resp_1","object":"response"}' in response.text
    assert "event: response.output_text.delta" in response.text
    assert 'data: {"delta":"hello"}' in response.text
    assert "event: response.completed" in response.text
