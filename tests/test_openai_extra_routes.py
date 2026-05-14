from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from pytest import MonkeyPatch

from app.main import app
import app.platform.auth as auth
import app.products.openai.completions as completions_router
import app.products.openai.embeddings as embeddings_router
import app.products.openai.rerank as rerank_router
from app.dataplane.fireworks.contracts import FIREWORKS_COMPLETIONS_SUPPORTED_FIELDS, FIREWORKS_EMBEDDINGS_SUPPORTED_FIELDS, FIREWORKS_RERANK_SUPPORTED_FIELDS
from app.products.openai.adapters import build_chat_adapter, build_completions_adapter, build_embeddings_adapter, build_rerank_adapter
from app.products.openai.errors import OpenAIRequestError
from app.products.openai.contracts import OPENAI_COMPLETIONS_REQUIRED, OPENAI_CHAT_REQUIRED, OPENAI_RERANK_REQUIRED


client = TestClient(app)


def _require_auth(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))


def _install_proxy_stub(monkeypatch: MonkeyPatch, module, captured: dict[str, object]) -> None:
    async def fake_proxy_fireworks_request(context, *, endpoint, upstream_path, payload, headers, route_trace=None):
        captured.update({"endpoint": endpoint, "upstream_path": upstream_path, "payload": payload, "headers": headers, "route_trace": route_trace})
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(module, "proxy_fireworks_request", fake_proxy_fireworks_request)


def _install_context_stub(monkeypatch: MonkeyPatch, module, model: str = "accounts/fireworks/models/test") -> None:
    async def fake_load_json_body(request):
        body = {"model": "test"}
        if module is completions_router:
            body["prompt"] = "hello"
        elif module is embeddings_router:
            body["input"] = "hello"
        else:
            body.update({"query": "a", "documents": ["b"]})
        return body

    async def fake_build_proxy_context_from_body(request, body, **kwargs):
        return SimpleNamespace(
            body=body,
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", upstream_base_url="https://api.fireworks.ai/inference"),
            request_headers={"x-session-affinity": "affinity"},
            stable_key="stable",
            resolved_model=SimpleNamespace(upstream_model=model),
        )

    if hasattr(module, "load_json_body"):
        monkeypatch.setattr(module, "load_json_body", fake_load_json_body)
    if hasattr(module, "build_proxy_context_from_body"):
        monkeypatch.setattr(module, "build_proxy_context_from_body", fake_build_proxy_context_from_body)
    if hasattr(module, "build_proxy_context_optional_model"):
        monkeypatch.setattr(module, "build_proxy_context_optional_model", fake_build_proxy_context_from_body)


def _adapter_context(body: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        body=body,
        settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret"),
        request_headers={},
        stable_key="stable",
        resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
    )


def test_embeddings_route_proxies_to_upstream(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    captured: dict[str, object] = {}
    _install_context_stub(monkeypatch, embeddings_router)
    _install_proxy_stub(monkeypatch, embeddings_router, captured)

    response = client.post("/v1/embeddings", headers={"Authorization": "Bearer token"}, json={"model": "test", "input": "hello"})

    assert response.status_code == 200
    assert captured["endpoint"] == "embeddings"
    assert captured["upstream_path"] == "v1/embeddings"
    assert captured["payload"]["model"] == "accounts/fireworks/models/test"


def test_completions_route_proxies_to_upstream(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    captured: dict[str, object] = {}
    _install_context_stub(monkeypatch, completions_router)
    _install_proxy_stub(monkeypatch, completions_router, captured)

    response = client.post("/v1/completions", headers={"Authorization": "Bearer token"}, json={"model": "test", "prompt": "hello"})

    assert response.status_code == 200
    assert captured["endpoint"] == "completions"
    assert captured["upstream_path"] == "completions"
    assert captured["payload"]["model"] == "accounts/fireworks/models/test"


def test_rerank_route_proxies_to_upstream(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    captured: dict[str, object] = {}
    _install_context_stub(monkeypatch, rerank_router)
    _install_proxy_stub(monkeypatch, rerank_router, captured)

    response = client.post("/v1/rerank", headers={"Authorization": "Bearer token"}, json={"model": "test", "query": "a", "documents": ["b"]})
    assert response.status_code == 200
    assert captured["endpoint"] == "rerank"
    assert captured["upstream_path"] == "v1/rerank"
    assert captured["payload"]["model"] == "accounts/fireworks/models/test"


def test_unversioned_rerank_alias_is_not_registered(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)

    response = client.post("/rerank", headers={"Authorization": "Bearer token"}, json={"model": "test", "query": "a", "documents": ["b"]})

    assert response.status_code == 404


def test_contracts_separate_openai_and_fireworks() -> None:
    assert "modalities" not in FIREWORKS_COMPLETIONS_SUPPORTED_FIELDS
    assert "prompt_cache_key" not in OPENAI_COMPLETIONS_REQUIRED
    assert "input" not in OPENAI_CHAT_REQUIRED
    assert "prompt_cache_key" not in OPENAI_RERANK_REQUIRED


def test_completions_adapter_forwards_documented_fireworks_fields() -> None:
    payload, _, _ = build_completions_adapter(
        _adapter_context(
            {
                "model": "test",
                "prompt": "hello",
                "response_format": {"type": "json_schema"},
                "thinking": {"enabled": True},
                "metadata": {"tag": "x"},
                "reasoning": {"effort": "low"},
                "stream": True,
                "prompt_cache_key": "cache-1",
                "context_length_exceeded_behavior": "error",
                "echo_last": 5,
                "return_token_ids": True,
                "top_k": 20,
            }
        )
    )

    assert payload["model"] == "accounts/fireworks/models/test"
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["thinking"]["enabled"] is True
    assert payload["metadata"]["tag"] == "x"
    assert payload["reasoning"]["effort"] == "low"
    assert payload["stream"] is True
    assert payload["prompt_cache_key"] == "cache-1"
    assert payload["context_length_exceeded_behavior"] == "error"
    assert payload["echo_last"] == 5
    assert payload["return_token_ids"] is True
    assert payload["top_k"] == 20


def test_completions_adapter_accepts_token_prompt_and_image_shapes() -> None:
    payload, _, _ = build_completions_adapter(
        _adapter_context(
            {
                "model": "test",
                "prompt": [[1, 2], [3, 4]],
                "images": [["data:image/png;base64,abc"], ["data:image/png;base64,def"]],
                "reasoning_history": "preserved",
                "service_tier": "priority",
            }
        )
    )

    assert payload["prompt"] == [[1, 2], [3, 4]]
    assert payload["images"] == [["data:image/png;base64,abc"], ["data:image/png;base64,def"]]
    assert payload["reasoning_history"] == "preserved"
    assert payload["service_tier"] == "priority"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("n", 129),
        ("context_length_exceeded_behavior", "keep"),
        ("echo", "yes"),
    ],
)
def test_completions_adapter_validates_top_level_values(field, value) -> None:
    response_context = _adapter_context({"model": "test", "prompt": "hello", field: value})

    try:
        build_completions_adapter(response_context)
    except OpenAIRequestError as exc:
        assert exc.code == "invalid_request_error"
    else:
        raise AssertionError(f"expected {field} to be rejected")


def test_completions_adapter_accepts_perf_metrics_in_response_passthrough() -> None:
    payload, _, _ = build_completions_adapter(_adapter_context({"model": "test", "prompt": "hello", "perf_metrics_in_response": True}))

    assert payload["perf_metrics_in_response"] is True


def test_completions_adapter_accepts_reasoning_history_and_prediction() -> None:
    payload, _, _ = build_completions_adapter(_adapter_context({"model": "test", "prompt": "hello", "reasoning_history": None, "prediction": "hello"}))

    assert payload["reasoning_history"] is None
    assert payload["prediction"] == "hello"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tools", {}),
        ("tool_choice", 1),
        ("response_format", "json_object"),
        ("thinking", []),
        ("metadata", []),
        ("reasoning", []),
        ("text", []),
    ],
)
def test_chat_adapter_validates_nested_advanced_fields(field, value) -> None:
    context = _adapter_context({"model": "test", "messages": [], field: value})

    with pytest.raises(Exception) as excinfo:
        build_chat_adapter(context)

    assert getattr(excinfo.value, "code", None) == "invalid_request_error"


def test_chat_adapter_priority_forwards_service_tier() -> None:
    payload, _, report = build_chat_adapter(_adapter_context({"model": "test", "messages": [{"role": "user", "content": "hi"}], "service_tier": "priority"}))

    assert payload["service_tier"] == "priority"
    assert report["warnings"] == []


@pytest.mark.parametrize("tier", ["auto", "default", "flex"])
def test_chat_adapter_omits_non_priority_service_tiers(tier) -> None:
    payload, _, report = build_chat_adapter(_adapter_context({"model": "test", "messages": [{"role": "user", "content": "hi"}], "service_tier": tier}))

    assert "service_tier" not in payload
    assert report["warnings"] == ["service_tier omitted for chat"]


def test_chat_adapter_rejects_thinking_and_reasoning_effort() -> None:
    with pytest.raises(Exception) as excinfo:
        build_chat_adapter(_adapter_context({"model": "test", "messages": [{"role": "user", "content": "hi"}], "thinking": {"enabled": True}, "reasoning_effort": "high"}))

    assert getattr(excinfo.value, "code", None) == "unsupported_parameter"


def test_chat_adapter_accepts_image_url_part() -> None:
    payload, _, _ = build_chat_adapter(_adapter_context({"model": "test", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}}]}]}))

    assert payload["messages"][0]["content"][0]["image_url"]["url"] == "https://example.com/cat.png"


def test_chat_adapter_rejects_invalid_image_url_part() -> None:
    with pytest.raises(Exception) as excinfo:
        build_chat_adapter(_adapter_context({"model": "test", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}]}))

    assert getattr(excinfo.value, "code", None) == "invalid_request_error"


def test_chat_route_uses_base_resolver_and_single_body_load(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr("app.platform.auth.get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))

    calls = {"load": 0, "context": 0, "resolver": 0}

    async def fake_load_json_body(request):
        calls["load"] += 1
        return {"model": "test", "messages": [{"role": "user", "content": "hi"}]}

    async def fake_context(request, body):
        calls["context"] += 1
        return SimpleNamespace(
            body=body,
            settings=SimpleNamespace(affinity_hash_secret="affinity-secret", log_hash_secret="log-secret", upstream_base_url="https://api.fireworks.ai/v1"),
            request_headers={"x-session-affinity": "affinity"},
            stable_key="stable",
            resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
        )

    def fake_resolve(upstream_base_url, endpoint):
        calls["resolver"] += 1
        assert upstream_base_url == "https://api.fireworks.ai/v1"
        assert endpoint == "chat_completions"
        return "chat/completions"

    async def fake_proxy(context, **kwargs):
        assert kwargs["headers"]["x-session-affinity"] == "affinity"
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr("app.products.openai.chat_completions.load_json_body", fake_load_json_body)
    monkeypatch.setattr("app.products.openai.chat_completions.build_proxy_context_from_body", fake_context)
    monkeypatch.setattr("app.products.openai.chat_completions.resolve_inference_path", fake_resolve)
    monkeypatch.setattr("app.products.openai.chat_completions.proxy_fireworks_request", fake_proxy)

    response = client.post("/v1/chat/completions", headers={"Authorization": "Bearer token"}, json={"model": "test", "messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 200
    assert calls == {"load": 1, "context": 1, "resolver": 1}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("response_format", "json_schema"),
        ("thinking", []),
        ("metadata", []),
        ("reasoning_history", {}),
        ("prediction", 1),
        ("prompt", ["ok", 1]),
        ("images", ["data:image/png;base64,abc", 1]),
    ],
)
def test_completions_adapter_validates_nested_advanced_fields(field, value) -> None:
    response_context = _adapter_context({"model": "test", "prompt": "hello", field: value})

    with pytest.raises(OpenAIRequestError) as excinfo:
        build_completions_adapter(response_context)

    assert excinfo.value.code == "invalid_request_error"


def test_completions_adapter_rejects_openai_field_not_supported_by_fireworks() -> None:
    response_context = _adapter_context({"model": "test", "prompt": "hello", "best_of": 2})

    try:
        build_completions_adapter(response_context)
    except OpenAIRequestError as exc:
        assert exc.code == "unsupported_parameter"
    else:
        raise AssertionError("expected best_of to be rejected")


def test_completions_adapter_error_shape_for_service_tier() -> None:
    payload, _, _ = build_completions_adapter(_adapter_context({"model": "test", "prompt": "hello", "service_tier": "flex"}))

    assert "service_tier" not in payload


def test_completions_adapter_error_shape_for_unknown_field() -> None:
    with pytest.raises(OpenAIRequestError) as excinfo:
        build_completions_adapter(_adapter_context({"model": "test", "prompt": "hello", "foo_vendor_option": True}))

    assert excinfo.value.code == "unknown_parameter"


def test_embeddings_adapter_forwards_documented_fireworks_fields() -> None:
    payload, _, _ = build_embeddings_adapter(
        _adapter_context(
            {
                "model": "test",
                "input": {"text": "hello"},
                "prompt_template": "Embed: {text}",
                "normalize": True,
                "return_logits": [1, 2],
            }
        )
    )

    assert payload["model"] == "accounts/fireworks/models/test"
    assert payload["prompt_template"] == "Embed: {text}"
    assert payload["normalize"] is True
    assert payload["return_logits"] == [1, 2]


def test_embeddings_adapter_accepts_documented_string_array_input() -> None:
    payload, _, _ = build_embeddings_adapter(_adapter_context({"model": "test", "input": ["hello", "world"]}))

    assert payload["input"] == ["hello", "world"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("input", []),
        ("dimensions", 0),
        ("return_logits", [1, "x"]),
    ],
)
def test_embeddings_adapter_validates_top_level_values(field, value) -> None:
    body = {"model": "test", "input": {"text": "hello"}}
    body[field] = value
    response_context = _adapter_context(body)

    try:
        build_embeddings_adapter(response_context)
    except OpenAIRequestError as exc:
        assert exc.code == "invalid_request_error"
    else:
        raise AssertionError(f"expected {field} to be rejected")


def test_embeddings_adapter_rejects_openai_field_not_supported_by_fireworks() -> None:
    response_context = _adapter_context({"model": "test", "input": "hello", "encoding_format": "base64"})

    try:
        build_embeddings_adapter(response_context)
    except OpenAIRequestError as exc:
        assert exc.code == "unsupported_parameter"
    else:
        raise AssertionError("expected encoding_format to be rejected")


def test_embeddings_adapter_rejects_schema_error_shape() -> None:
    with pytest.raises(OpenAIRequestError) as excinfo:
        build_embeddings_adapter(_adapter_context({"model": "test", "input": "hello", "dimensions": 0}))

    assert excinfo.value.code == "invalid_request_error"


def test_rerank_adapter_forwards_documented_fireworks_fields() -> None:
    payload, _, _ = build_rerank_adapter(_adapter_context({"model": "test", "query": "q", "documents": ["a"], "task": "search", "top_n": 1, "return_documents": False}))

    assert payload["model"] == "accounts/fireworks/models/test"
    assert payload["task"] == "search"
    assert payload["top_n"] == 1
    assert payload["return_documents"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("query", ""),
        ("documents", []),
        ("top_n", 0),
    ],
)
def test_rerank_adapter_validates_top_level_values(field, value) -> None:
    body = {"model": "test", "query": "q", "documents": ["a"]}
    body[field] = value
    response_context = _adapter_context(body)

    try:
        build_rerank_adapter(response_context)
    except OpenAIRequestError as exc:
        assert exc.code == "invalid_request_error"
    else:
        raise AssertionError(f"expected {field} to be rejected")


def test_rerank_adapter_rejects_openai_field_not_supported_by_fireworks() -> None:
    response_context = _adapter_context({"model": "test", "query": "q", "documents": ["a"], "rank_fields": ["title"]})

    try:
        build_rerank_adapter(response_context)
    except OpenAIRequestError as exc:
        assert exc.code == "unsupported_parameter"
    else:
        raise AssertionError("expected rank_fields to be rejected")


def test_rerank_adapter_rejects_schema_error_shape() -> None:
    with pytest.raises(OpenAIRequestError) as excinfo:
        build_rerank_adapter(_adapter_context({"model": "test", "query": "", "documents": ["a"]}))

    assert excinfo.value.code == "invalid_request_error"


def test_completions_adapter_rejects_plain_http_images() -> None:
    with pytest.raises(OpenAIRequestError) as excinfo:
        build_completions_adapter(_adapter_context({"model": "test", "prompt": "hello", "images": ["https://example.com/cat.png"]}))

    assert excinfo.value.code == "invalid_request_error"


def test_completions_adapter_rejects_plain_http_images() -> None:
    with pytest.raises(OpenAIRequestError) as excinfo:
        build_completions_adapter(_adapter_context({"model": "test", "prompt": "hello", "images": ["https://example.com/cat.png"]}))

    assert excinfo.value.code == "invalid_request_error"


def test_completions_adapter_validates_service_tier_and_token_alias_conflict() -> None:
    for body in (
        {"model": "test", "prompt": "hello", "max_tokens": 1, "max_completion_tokens": 1},
    ):
        try:
            build_completions_adapter(_adapter_context(body))
        except Exception as exc:  # noqa: BLE001
            assert getattr(exc, "code", None) == "unsupported_parameter"
        else:
            raise AssertionError(f"expected completions validation failure for {body}")


def test_completions_adapter_rejects_thinking_and_reasoning_effort() -> None:
    with pytest.raises(OpenAIRequestError) as excinfo:
        build_completions_adapter(_adapter_context({"model": "test", "prompt": "hello", "thinking": {"enabled": True}, "reasoning_effort": "high"}))

    assert excinfo.value.code == "unsupported_parameter"
