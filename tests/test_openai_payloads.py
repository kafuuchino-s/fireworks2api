from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from fastapi.responses import Response, StreamingResponse

from app.main import app
from app.products.openai.adapters import build_chat_adapter, build_responses_adapter
from app.products.openai.fireworks_native.completions import build_completions_adapter
from app.dataplane.fireworks.contracts import (
    FIREWORKS_CHAT_EXTENSION_FIELDS,
    FIREWORKS_CHAT_SUPPORTED_FIELDS,
)
from app.products.openai.contracts import (
    OPENAI_CHAT_STANDARD_OPTIONAL,
    OPENAI_NOT_CHAT,
    OPENAI_RESPONSES_STANDARD_OPTIONAL,
)
from app.products.openai.proxy_common import (
    build_chat_upstream_payload,
    build_responses_upstream_payload,
    record_proxy_transform_debug,
)


def _context(**overrides):
    settings = SimpleNamespace(
        log_hash_secret="secret",
        upstream_base_url="https://api.fireworks.ai/inference/v1",
        responses_cache_fields_enabled=True,
    )
    resolved_model = SimpleNamespace(
        upstream_model="accounts/fireworks/models/test",
    )
    base = SimpleNamespace(
        settings=settings,
        body={
            "model": "test",
            "messages": [],
            "thinking": {"enabled": True},
            "reasoning_effort": "high",
            "user": "user-1",
            "prompt_cache_key": "keep-me",
            "prompt_cache_isolation_key": "iso-1",
        },
        model_name="test",
        resolved_model=resolved_model,
        stable_key="stable",
        route_key="test:stable",
        affinity_header="affinity",
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_contracts_keep_openai_and_fireworks_fields_separate() -> None:
    assert "modalities" in OPENAI_CHAT_STANDARD_OPTIONAL
    assert "modalities" not in FIREWORKS_CHAT_SUPPORTED_FIELDS
    assert "prompt_cache_key" in FIREWORKS_CHAT_EXTENSION_FIELDS
    assert "prompt_cache_key" not in OPENAI_CHAT_STANDARD_OPTIONAL
    assert "max_output_tokens" in OPENAI_RESPONSES_STANDARD_OPTIONAL


def test_chat_payload_uses_adapter_allowlist_and_model_rewrite() -> None:
    context = _context(body={"model": "test", "messages": [{"role": "user", "content": "hi"}], "thinking": {"enabled": True}, "user": "user-1", "prompt_cache_key": "keep-me", "prompt_cache_isolation_key": "iso-1"})

    payload = build_chat_upstream_payload(context)

    assert payload["model"] == "accounts/fireworks/models/test"
    assert payload["thinking"] == {"enabled": True}
    assert "reasoning_effort" not in payload
    assert payload["user"] == "user-1"
    assert payload["prompt_cache_key"] == "keep-me"
    assert payload["prompt_cache_isolation_key"] == "iso-1"


def test_chat_adapter_copies_standard_fields_and_drops_unknown() -> None:
    context = _context(body={"model": "test", "messages": [], "temperature": 0.2, "unknown": 1})

    with pytest.raises(Exception):
        build_chat_adapter(context)


def test_chat_adapter_records_advisory_reasoning_warning_without_prompts() -> None:
    context = _context(body={"model": "test", "messages": [{"role": "user", "content": "secret prompt"}], "thinking": {"type": "enabled", "budget_tokens": 1024}}, resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/gpt-oss-20b"))

    payload, _, report = build_chat_adapter(context)

    assert payload["thinking"]["type"] == "enabled"
    assert any("unsupported" in warning for warning in report["warnings"])
    assert all("secret prompt" not in warning for warning in report["warnings"])


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("n", 0, "invalid_request_error"),
        ("temperature", 2.5, "invalid_request_error"),
        ("stream", "yes", "invalid_request_error"),
    ],
)
def test_chat_adapter_validates_top_level_values(field, value, code) -> None:
    context = _context(body={"model": "test", "messages": [], field: value})

    with pytest.raises(Exception) as excinfo:
        build_chat_adapter(context)

    assert getattr(excinfo.value, "code", None) == code


def test_chat_adapter_maps_max_completion_tokens() -> None:
    context = _context(body={"model": "test", "messages": [{"role": "user", "content": "hi"}], "max_completion_tokens": 42})

    payload, _, report = build_chat_adapter(context)

    assert payload["max_tokens"] == 42
    assert report["field_changes"][0]["field"] == "max_completion_tokens"


def test_chat_route_accepts_text_only_modalities_and_drops_store(monkeypatch) -> None:
    client = TestClient(app)

    captured = {}

    async def fake_build_proxy_context(request):
        return SimpleNamespace(
            body={"model": "test", "messages": [{"role": "user", "content": "hi"}], "modalities": ["text"], "store": True},
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", upstream_base_url="https://api.fireworks.ai/inference/v1"),
            request_headers={},
            stable_key="stable",
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
        )

    async def fake_proxy(context, **kwargs):
        captured.update(kwargs)
        return Response(content=b"{}", media_type="application/json")

    monkeypatch.setattr("app.products.openai.chat_completions.build_proxy_context_from_body", lambda request, body: fake_build_proxy_context(request))
    monkeypatch.setattr("app.products.openai.chat_completions.proxy_fireworks_request", fake_proxy)
    monkeypatch.setattr("app.platform.auth.get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))

    response = client.post("/v1/chat/completions", headers={"Authorization": "Bearer token"}, json={"model": "test", "messages": [{"role": "user", "content": "hi"}], "modalities": ["text"], "store": True})

    assert response.status_code == 200
    assert "modalities" not in captured["payload"]
    assert "store" not in captured["payload"]


def test_chat_route_rejects_audio_modalities(monkeypatch) -> None:
    client = TestClient(app)

    def fail_proxy(*args, **kwargs):
        raise AssertionError("upstream should not be called")

    monkeypatch.setattr("app.products.openai.chat_completions.proxy_fireworks_request", fail_proxy)
    monkeypatch.setattr("app.platform.auth.get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))

    response = client.post("/v1/chat/completions", headers={"Authorization": "Bearer token"}, json={"model": "test", "messages": [{"role": "user", "content": "hi"}], "modalities": ["text", "audio"]})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["param"] == "modalities"
    assert error["code"] == "unsupported_parameter"


def test_chat_adapter_maps_legacy_functions_and_function_call() -> None:
    context = _context(body={"model": "test", "messages": [{"role": "user", "content": "hi"}], "functions": [{"name": "lookup", "description": "d", "parameters": {}}], "function_call": {"name": "lookup"}, "service_tier": "scale"})

    payload, _, report = build_chat_adapter(context)

    assert payload["tools"] == [{"type": "function", "function": {"name": "lookup", "description": "d", "parameters": {}}}]
    assert payload["tool_choice"] == {"type": "function", "function": {"name": "lookup"}}
    assert "service_tier" not in payload
    assert any(change["field"] == "functions" for change in report["field_changes"])


def test_chat_route_unknown_field_returns_unknown_parameter(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr("app.platform.auth.get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))

    response = client.post("/v1/chat/completions", headers={"Authorization": "Bearer token"}, json={"model": "test", "messages": [{"role": "user", "content": "hi"}], "foo_vendor_option": True})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error == {"message": "unknown parameter 'foo_vendor_option'", "type": "invalid_request_error", "param": "foo_vendor_option", "code": "unknown_parameter"}


def test_chat_route_stream_flag_forwards_and_preserves_streaming_response(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr("app.platform.auth.get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))

    captured = {}

    async def fake_build_proxy_context(request):
        return SimpleNamespace(
            body={"model": "test", "messages": [{"role": "user", "content": "hi"}], "stream": True},
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", upstream_base_url="https://api.fireworks.ai/inference/v1"),
            request_headers={"x-session-affinity": "affinity"},
            stable_key="stable",
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
        )

    async def fake_proxy(context, **kwargs):
        captured.update(kwargs)
        return StreamingResponse(iter([b"data: ok\n\n"]), media_type="text/event-stream")

    monkeypatch.setattr("app.products.openai.chat_completions.build_proxy_context_from_body", lambda request, body: fake_build_proxy_context(request))
    monkeypatch.setattr("app.products.openai.chat_completions.proxy_fireworks_request", fake_proxy)

    response = client.post("/v1/chat/completions", headers={"Authorization": "Bearer token"}, json={"model": "test", "messages": [{"role": "user", "content": "hi"}], "stream": True})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert captured["endpoint"] == "chat_completions"
    assert captured["upstream_path"] == "chat/completions"
    assert captured["payload"]["stream"] is True


def test_chat_route_preserves_preopen_http_error_response(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr("app.platform.auth.get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))

    async def fake_build_proxy_context(request):
        return SimpleNamespace(
            body={"model": "test", "messages": [{"role": "user", "content": "hi"}]},
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", upstream_base_url="https://api.fireworks.ai/inference/v1"),
            request_headers={"x-session-affinity": "affinity"},
            stable_key="stable",
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
        )

    async def fake_proxy(context, **kwargs):
        from fastapi.responses import JSONResponse

        return JSONResponse({"error": {"message": "rate limited"}}, status_code=429)

    monkeypatch.setattr("app.products.openai.chat_completions.build_proxy_context_from_body", lambda request, body: fake_build_proxy_context(request))
    monkeypatch.setattr("app.products.openai.chat_completions.proxy_fireworks_request", fake_proxy)

    response = client.post("/v1/chat/completions", headers={"Authorization": "Bearer token"}, json={"model": "test", "messages": [{"role": "user", "content": "hi"}], "tools": [{"type": "function", "function": {"name": "lookup"}}], "tool_choice": "auto", "response_format": {"type": "json_object"}, "thinking": {"enabled": True}, "metadata": {"tag": "x"}, "reasoning": {"effort": "low"}, "text": {"format": "plain"}})

    assert response.status_code == 429
    assert response.json()["error"]["message"] == "rate limited"


def test_chat_route_legacy_max_tokens_forwarded(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr("app.platform.auth.get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))

    captured = {}
    async def fake_build_proxy_context(request):
        return SimpleNamespace(
            body={"model": "test", "prompt": "hi", "max_tokens": 5},
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", upstream_base_url="https://api.fireworks.ai/inference/v1"),
            request_headers={"x-session-affinity": "affinity"},
            stable_key="stable",
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
        )

    async def fake_proxy(context, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status_code=200)
    monkeypatch.setattr("app.products.openai.completions.build_proxy_context_from_body", lambda request, body: fake_build_proxy_context(request))
    monkeypatch.setattr("app.products.openai.completions.proxy_fireworks_request", fake_proxy)

    response = client.post("/v1/completions", headers={"Authorization": "Bearer token"}, json={"model": "test", "prompt": "hi", "max_tokens": 5})

    assert response.status_code == 200
    assert captured["payload"]["max_tokens"] == 5


def test_chat_route_forwards_nested_advanced_fields(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr("app.platform.auth.get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))

    captured = {}

    async def fake_build_proxy_context(request):
        return SimpleNamespace(
            body={
                "model": "test",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [{"type": "function", "function": {"name": "lookup"}}],
                "tool_choice": "auto",
                "response_format": {"type": "json_object"},
                "thinking": {"enabled": True},
                "metadata": {"tag": "x"},
                "reasoning_history": "preserved",
            },
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", upstream_base_url="https://api.fireworks.ai/inference/v1"),
            request_headers={"x-session-affinity": "affinity"},
            stable_key="stable",
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
        )

    async def fake_proxy(context, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr("app.products.openai.chat_completions.build_proxy_context_from_body", lambda request, body: fake_build_proxy_context(request))
    monkeypatch.setattr("app.products.openai.chat_completions.proxy_fireworks_request", fake_proxy)

    response = client.post("/v1/chat/completions", headers={"Authorization": "Bearer token"}, json={"model": "test", "messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 200
    assert captured["payload"]["tools"][0]["function"]["name"] == "lookup"
    assert captured["payload"]["tool_choice"] == "auto"
    assert captured["payload"]["response_format"]["type"] == "json_object"
    assert captured["payload"]["thinking"]["enabled"] is True
    assert captured["payload"]["metadata"]["tag"] == "x"
    assert captured["payload"]["reasoning_history"] == "preserved"


@pytest.mark.parametrize(
    ("body", "param"),
    [
        ({"model": "test", "messages": [{"role": "tool", "content": "hi"}]}, "messages[0].tool_call_id"),
        ({"model": "test", "messages": [{"role": "assistant", "content": "hi", "tool_calls": []}]}, "messages[0].tool_calls"),
        ({"model": "test", "messages": [{"role": "user", "content": [{"type": "audio"}]}]}, "messages[0].content[0].type"),
        ({"model": "test", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "http://x"}}]}]}, "messages[0].content[0].image_url"),
    ],
)
def test_chat_adapter_rejects_invalid_message_shapes(body, param) -> None:
    context = _context(body=body)

    with pytest.raises(Exception) as excinfo:
        build_chat_adapter(context)

    assert getattr(excinfo.value, "param", None) == param


def test_chat_adapter_accepts_function_tools_and_object_tool_choice() -> None:
    context = _context(body={"model": "test", "messages": [{"role": "user", "content": "hi"}], "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}], "tool_choice": {"type": "function", "function": {"name": "lookup"}}})

    payload, _, _ = build_chat_adapter(context)

    assert payload["tools"][0]["function"]["name"] == "lookup"


def test_chat_adapter_accepts_tool_result_message_and_forwards_content_parts() -> None:
    context = _context(body={"model": "test", "messages": [{"role": "tool", "tool_call_id": "call_1", "content": [{"type": "text", "text": "done"}]}]})

    payload, _, _ = build_chat_adapter(context)

    assert payload["messages"][0]["role"] == "tool"
    assert payload["messages"][0]["tool_call_id"] == "call_1"
    assert payload["messages"][0]["content"][0]["text"] == "done"


def test_chat_adapter_validates_tool_message_requires_tool_call_id() -> None:
    context = _context(body={"model": "test", "messages": [{"role": "tool", "content": "done"}]})

    with pytest.raises(Exception) as excinfo:
        build_chat_adapter(context)

    assert getattr(excinfo.value, "param", None) == "messages[0].tool_call_id"


def test_chat_adapter_validates_user_is_string() -> None:
    context = _context(body={"model": "test", "messages": [{"role": "user", "content": "hi"}], "user": 123})

    with pytest.raises(Exception) as excinfo:
        build_chat_adapter(context)

    assert getattr(excinfo.value, "param", None) == "user"


def test_chat_adapter_validates_stream_options_include_usage_boolean() -> None:
    context = _context(body={"model": "test", "messages": [{"role": "user", "content": "hi"}], "stream_options": {"include_usage": "yes"}})

    with pytest.raises(Exception) as excinfo:
        build_chat_adapter(context)

    assert getattr(excinfo.value, "param", None) == "stream_options.include_usage"


def test_chat_adapter_rejects_tool_choice_any() -> None:
    context = _context(body={"model": "test", "messages": [{"role": "user", "content": "hi"}], "tool_choice": "any"})

    with pytest.raises(Exception) as excinfo:
        build_chat_adapter(context)

    assert getattr(excinfo.value, "param", None) == "tool_choice"


@pytest.mark.parametrize(
    "body",
    [
        {"model": "test", "messages": [{"role": "user", "content": "hi"}], "tools": [{"type": "mcp"}]},
        {"model": "test", "messages": [{"role": "user", "content": "hi"}], "tools": [{"type": "function", "function": {"name": ""}}]},
        {"model": "test", "messages": [{"role": "user", "content": "hi"}], "tools": [{"type": "function", "function": {"name": "lookup", "parameters": []}}]},
        {"model": "test", "messages": [{"role": "user", "content": "hi"}], "tool_choice": "mcp"},
    ],
)
def test_chat_adapter_rejects_unsupported_tools_and_choice(body) -> None:
    context = _context(body=body)

    with pytest.raises(Exception):
        build_chat_adapter(context)


def test_completions_route_schema_validation_error_shape(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr("app.platform.auth.get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))

    async def fake_build_proxy_context(request):
        return SimpleNamespace(
            body={"model": "test", "prompt": "hi", "n": 0},
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret"),
            request_headers={},
            stable_key="stable",
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
        )

    monkeypatch.setattr("app.products.openai.completions.build_proxy_context_from_body", lambda request, body: fake_build_proxy_context(request))

    response = client.post("/v1/completions", headers={"Authorization": "Bearer token"}, json={"model": "test", "prompt": "hi", "n": 0})

    assert response.status_code == 400
    assert "n" in response.text
    assert "invalid_request_error" in response.text


def test_completions_route_stream_and_perf_metrics_forwarded(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr("app.platform.auth.get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))

    captured = {}

    async def fake_build_proxy_context(request):
        return SimpleNamespace(
            body={"model": "test", "prompt": "hi", "stream": True, "perf_metrics_in_response": True},
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", upstream_base_url="https://api.fireworks.ai/inference/v1"),
            request_headers={"x-session-affinity": "affinity"},
            stable_key="stable",
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
        )

    async def fake_proxy(context, **kwargs):
        captured.update(kwargs)
        return StreamingResponse(iter([b"data: ok\n\n"]), media_type="text/event-stream")

    monkeypatch.setattr("app.products.openai.completions.build_proxy_context_from_body", lambda request, body: fake_build_proxy_context(request))
    monkeypatch.setattr("app.products.openai.completions.proxy_fireworks_request", fake_proxy)

    response = client.post("/v1/completions", headers={"Authorization": "Bearer token"}, json={"model": "test", "prompt": "hi", "stream": True, "perf_metrics_in_response": True})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert captured["endpoint"] == "completions"
    assert captured["upstream_path"] == "completions"
    assert captured["payload"]["stream"] is True
    assert captured["payload"]["perf_metrics_in_response"] is True


def test_completions_route_preserves_terminal_stream_error_event(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr("app.platform.auth.get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))

    async def fake_build_proxy_context(request):
        return SimpleNamespace(
            body={"model": "test", "prompt": "hi", "stream": True},
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", upstream_base_url="https://api.fireworks.ai/inference/v1"),
            request_headers={"x-session-affinity": "affinity"},
            stable_key="stable",
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
        )

    async def fake_proxy(context, **kwargs):
        return StreamingResponse(iter([b'event: error\ndata: {"message":"boom"}\n\n']), media_type="text/event-stream")

    monkeypatch.setattr("app.products.openai.completions.build_proxy_context_from_body", lambda request, body: fake_build_proxy_context(request))
    monkeypatch.setattr("app.products.openai.completions.proxy_fireworks_request", fake_proxy)

    response = client.post("/v1/completions", headers={"Authorization": "Bearer token"}, json={"model": "test", "prompt": "hi", "stream": True})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: error" in response.text


def test_completions_route_unsupported_field_error_shape(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr("app.platform.auth.get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))

    async def fake_build_proxy_context(request):
        return SimpleNamespace(
            body={"model": "test", "prompt": "hi", "best_of": 2},
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret"),
            request_headers={},
            stable_key="stable",
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
        )

    monkeypatch.setattr("app.products.openai.completions.build_proxy_context_from_body", lambda request, body: fake_build_proxy_context(request))

    response = client.post("/v1/completions", headers={"Authorization": "Bearer token"}, json={"model": "test", "prompt": "hi", "best_of": 2})

    assert response.status_code == 400
    assert "best_of" in response.text


def test_completions_route_forwards_nested_advanced_fields(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr("app.platform.auth.get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))

    captured = {}

    async def fake_build_proxy_context(request):
        return SimpleNamespace(
            body={
                "model": "test",
                "prompt": "hi",
                "response_format": {"type": "json_schema"},
                "thinking": {"enabled": True},
                "metadata": {"tag": "x"},
                "reasoning": {"effort": "low"},
                "reasoning_history": "interleaved",
                "prediction": {"type": "content", "content": "hello"},
            },
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", upstream_base_url="https://api.fireworks.ai/inference/v1"),
            request_headers={"x-session-affinity": "affinity"},
            stable_key="stable",
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
        )

    async def fake_proxy(context, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr("app.products.openai.completions.build_proxy_context_from_body", lambda request, body: fake_build_proxy_context(request))
    monkeypatch.setattr("app.products.openai.completions.proxy_fireworks_request", fake_proxy)

    response = client.post("/v1/completions", headers={"Authorization": "Bearer token"}, json={"model": "test", "prompt": "hi", "response_format": {"type": "json_schema"}, "thinking": {"enabled": True}, "metadata": {"tag": "x"}, "reasoning": {"effort": "low"}, "reasoning_history": ["step1"], "prediction": {"type": "content", "content": "hello"}})

    assert response.status_code == 200
    assert captured["payload"]["response_format"]["type"] == "json_schema"
    assert captured["payload"]["thinking"]["enabled"] is True
    assert captured["payload"]["metadata"]["tag"] == "x"
    assert captured["payload"]["reasoning"]["effort"] == "low"
    assert captured["payload"]["reasoning_history"] == "interleaved"
    assert captured["payload"]["prediction"]["content"] == "hello"


def test_completions_adapter_rejects_thinking_and_reasoning_effort_conflict() -> None:
    context = _context(body={"model": "test", "prompt": "hi", "thinking": {"enabled": True}, "reasoning_effort": "high"})

    with pytest.raises(Exception) as excinfo:
        build_completions_adapter(context)

    assert getattr(excinfo.value, "code", None) == "unsupported_parameter"


def test_completions_adapter_warns_on_prompt_image_marker_mismatch() -> None:
    payload, _, report = build_completions_adapter(_context(body={"model": "test", "prompt": "hello <image>", "images": [["data:image/png;base64,abc"], ["data:image/png;base64,def"]]}))

    assert payload["images"][0][0].startswith("data:image/png;base64,")
    assert any("prompt image marker count" in warning for warning in report["warnings"])


@pytest.mark.parametrize("images", [[], ["https://example.com/cat.png"], [["https://example.com/cat.png"]], [["data:image/gif;base64,abc"]]])
def test_completions_adapter_rejects_invalid_images(images) -> None:
    context = _context(body={"model": "test", "prompt": "hello", "images": images})

    with pytest.raises(Exception) as excinfo:
        build_completions_adapter(context)

    assert getattr(excinfo.value, "code", None) == "invalid_request_error"


def test_completions_adapter_accepts_valid_image_lists() -> None:
    payload, _, _ = build_completions_adapter(_context(body={"model": "test", "prompt": ["hello"], "images": [["data:image/jpeg;base64,abc"], ["data:image/webp;base64,def"]]}))

    assert payload["images"][0][0].startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize("thinking", [{"type": 1}, {"budget_tokens": 512}])
def test_completions_adapter_validates_thinking_shape(thinking) -> None:
    context = _context(body={"model": "test", "prompt": "hi", "thinking": thinking})

    with pytest.raises(Exception) as excinfo:
        build_completions_adapter(context)

    assert getattr(excinfo.value, "code", None) == "invalid_request_error"


@pytest.mark.parametrize("field", ["return_token_ids", "raw_output", "perf_metrics_in_response", "ignore_eos", "echo"])
def test_completions_adapter_validates_boolean_fields(field) -> None:
    context = _context(body={"model": "test", "prompt": "hi", field: "yes"})

    with pytest.raises(Exception) as excinfo:
        build_completions_adapter(context)

    assert getattr(excinfo.value, "code", None) == "invalid_request_error"


def test_completions_adapter_reports_max_completion_tokens_transform() -> None:
    payload, _, report = build_completions_adapter(_context(body={"model": "test", "prompt": "hi", "max_completion_tokens": 42}))

    assert payload["max_tokens"] == 42
    assert report["field_changes"][0]["field"] == "model"
    assert any(change["field"] == "max_completion_tokens" for change in report["field_changes"])


def test_contracts_distinguish_chat_public_and_fireworks() -> None:
    assert "modalities" in OPENAI_CHAT_STANDARD_OPTIONAL
    assert "modalities" not in FIREWORKS_CHAT_SUPPORTED_FIELDS
    assert "prompt_cache_key" not in OPENAI_NOT_CHAT


def test_responses_payload_uses_adapter_allowlist_and_model_rewrite() -> None:
    context = _context(
        body={
            "model": "test",
            "input": "hello",
            "metadata": {"trace": "ok"},
            "reasoning": {"effort": "high"},
            "text": {"format": {"type": "text"}},
            "tool_choice": "auto",
            "tools": [{"type": "function", "function": {"name": "lookup"}}],
            "user": "user-1",
            "prompt_cache_key": "keep-me",
            "prompt_cache_isolation_key": "iso-1",
        }
    )

    payload = build_responses_upstream_payload(context)

    assert payload["model"] == "accounts/fireworks/models/test"
    assert payload["input"] == "hello"
    assert payload["metadata"] == {"trace": "ok"}
    assert payload["reasoning"] == {"effort": "high"}
    assert payload["text"] == {"format": {"type": "text"}}
    assert payload["tool_choice"] == "auto"
    assert payload["tools"] == [{"type": "function", "function": {"name": "lookup"}}]
    assert payload["user"] == "user-1"
    assert payload["prompt_cache_key"] == "keep-me"
    assert payload["prompt_cache_isolation_key"] == "iso-1"


def test_responses_payload_forwards_stream_and_perf_metrics_fields() -> None:
    context = _context(
        body={
            "model": "test",
            "input": "hello",
            "stream": True,
            "perf_metrics_in_response": True,
            "prompt_cache_key": "keep-me",
            "prompt_cache_isolation_key": "iso-1",
        }
    )

    payload = build_responses_upstream_payload(context)

    assert payload["stream"] is True
    assert payload["perf_metrics_in_response"] is True
    assert payload["prompt_cache_key"] == "keep-me"
    assert payload["prompt_cache_isolation_key"] == "iso-1"


def test_responses_adapter_maps_max_tokens() -> None:
    context = _context(body={"model": "test", "input": "hello", "max_tokens": 11})

    payload, _, report = build_responses_adapter(context)

    assert payload["max_output_tokens"] == 11
    assert report["field_changes"][0]["field"] == "max_tokens"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_output_tokens", 0),
        ("parallel_tool_calls", "no"),
        ("service_tier", "priority"),
    ],
)
def test_responses_adapter_validates_top_level_values(field, value) -> None:
    context = _context(body={"model": "test", "input": "hello", field: value})

    with pytest.raises(Exception) as excinfo:
        build_responses_adapter(context)

    assert getattr(excinfo.value, "code", None) in {"invalid_request_error", "unsupported_parameter"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tools", "not-a-list"),
        ("tool_choice", 123),
        ("text", "plain"),
        ("reasoning", []),
        ("metadata", []),
    ],
)
def test_responses_adapter_rejects_invalid_nested_advanced_field_types(field, value) -> None:
    context = _context(body={"model": "test", "input": "hello", field: value})

    with pytest.raises(Exception) as excinfo:
        build_responses_adapter(context)

    assert getattr(excinfo.value, "code", None) == "invalid_request_error"


def test_responses_adapter_accepts_object_tool_choice_and_nested_objects() -> None:
    context = _context(
        body={
            "model": "test",
            "input": "hello",
            "tools": [{"type": "function", "function": {"name": "lookup"}}],
            "tool_choice": {"type": "function", "function": {"name": "lookup"}},
            "text": {"format": {"type": "text"}},
            "reasoning": {"effort": "high"},
            "metadata": {"trace": "ok"},
        }
    )

    payload, _, _ = build_responses_adapter(context)

    assert payload["tools"][0]["type"] == "function"
    assert payload["tool_choice"]["type"] == "function"
    assert payload["text"] == {"format": {"type": "text"}}
    assert payload["reasoning"] == {"effort": "high"}
    assert payload["metadata"] == {"trace": "ok"}


def test_responses_adapter_normalizes_official_image_and_function_outputs() -> None:
    context = _context(
        body={
            "model": "test",
            "input": [
                {"role": "user", "content": [{"type": "input_image", "image_url": {"url": "https://example.com/cat.png"}}]},
                {"type": "function_call_output", "call_id": "call_1", "output": "done"},
            ],
        }
    )

    payload = build_responses_upstream_payload(context)

    assert payload["input"][0]["content"][0] == {"type": "input_image", "image_url": "https://example.com/cat.png"}
    assert payload["input"][1] == {"type": "function_call_output", "call_id": "call_1", "output": "done"}


def test_responses_route_returns_top_level_error_for_non_priority_service_tier(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr("app.platform.auth.get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))

    response = client.post("/v1/responses", headers={"Authorization": "Bearer token"}, json={"model": "test", "input": "hello", "service_tier": "standard"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_parameter"


def test_chat_transform_debug_records_model_change() -> None:
    captured = {}

    def _record(payload, retention):
        captured["payload"] = payload

    settings = SimpleNamespace(transform_debug_enabled=True, transform_debug_retention=1)
    repo = SimpleNamespace(record_transform_debug=_record)
    context = _context()
    context.settings = settings
    context.repository = repo

    record_proxy_transform_debug(
        context,
        endpoint="chat_completions",
        upstream_endpoint="chat/completions",
        payload={"model": "accounts/fireworks/models/test", "messages": [], "stream": False},
        headers={"x-session-affinity": "a"},
        field_changes=[{"field": "model", "from": "test", "to": "accounts/fireworks/models/test"}],
    )

    assert captured["payload"]["endpoint"] == "chat_completions"
    assert captured["payload"]["field_changes"][0]["field"] == "model"
