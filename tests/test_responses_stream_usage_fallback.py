from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch
import respx

import app.platform.auth as auth
from app.main import app
from app.platform.config import Settings, get_settings
import app.products.openai.responses as responses_mod
from types import SimpleNamespace


client = TestClient(app)


@pytest.fixture
def client_fixture(monkeypatch: MonkeyPatch):
    get_settings.cache_clear()
    settings = Settings(
        proxy_api_keys=["token"],
        admin_token="token",
        affinity_hash_secret="secret",
        log_hash_secret="secret",
        upstream_base_url="https://api.fireworks.ai/inference/v1",
        max_upstream_attempts=1,
        request_timeout_seconds=120.0,
        responses_cache_fields_enabled=False,
        allow_unknown_model_passthrough=False,
        request_log_retention=30,
    )
    monkeypatch.setattr(app.state, "settings", settings, raising=False)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    return TestClient(app)


def _context(body: dict[str, object], upstream_model: str) -> SimpleNamespace:
    return SimpleNamespace(
        settings=SimpleNamespace(
            affinity_hash_secret="secret",
            log_hash_secret="secret",
            upstream_base_url="https://api.fireworks.ai/inference/v1",
            max_upstream_attempts=1,
            request_timeout_seconds=120.0,
            responses_cache_fields_enabled=False,
            allow_unknown_model_passthrough=False,
            request_log_retention=30,
        ),
        repository=SimpleNamespace(
            insert_request_log=lambda *args, **kwargs: None,
            get_response_key_route=lambda response_id: None,
            upsert_response_key_route=lambda response_id, key: None,
            delete_response_key_route=lambda response_id: None,
            get_fireworks_key_snapshot=lambda fp: None,
        ),
        body=body,
        model_name=body.get("model", "test"),
        resolved_model=SimpleNamespace(upstream_model=upstream_model),
        stable_key="stable",
        stable_key_hash_value="hash123",
        route_key="route",
        affinity_header="affinity",
        request_headers={"authorization": "Bearer token"},
        selected_keys=[SimpleNamespace(name="key-1", api_key="fw-test-key", fingerprint="fp-1")],
        routing_metadata={
            "stable_key_source": "session",
            "stable_key_hash_value": "hash123",
            "affinity_header": "aff",
        },
    )


_NATIVE_RESPONSES_BODY = "".join(
    [
        'event: response.created\ndata: {"id":"resp_1","object":"response","status":"in_progress","model":"kimi-k2.7-code-fast","output":[]}\n\n',
        'event: response.output_text.delta\ndata: {"output_index":0,"delta":"hello world this is generated text"}\n\n',
        'event: response.output_item.done\ndata: {"output_index":0,"item":{"type":"message","id":"msg_1","role":"assistant","status":"completed","content":[{"type":"output_text","text":"hello world this is generated text"}]}}\n\n',
        'event: response.completed\ndata: {"id":"resp_1","object":"response","status":"completed","model":"kimi-k2.7-code-fast","output":[{"type":"message","id":"msg_1","role":"assistant","content":[{"type":"output_text","text":"hello world this is generated text"}]}],"usage":{"input_tokens":49513,"output_tokens":0,"total_tokens":49513}}\n\n',
    ]
)


@respx.mock
def test_responses_native_estimates_usage_when_tokenizer_unavailable(
    client_fixture: TestClient, monkeypatch: MonkeyPatch
) -> None:
    """Upstream native Responses stream reports output_tokens=0 and the tokenizer
    cannot be loaded (e.g. network restricted). The returned response.completed
    must still contain a non-zero estimated output_tokens so sub2api sees usage.
    """
    route = respx.post("https://api.fireworks.ai/inference/v1/responses").respond(
        status_code=200,
        headers={"content-type": "text/event-stream", "x-request-id": "req-1"},
        text=_NATIVE_RESPONSES_BODY,
    )

    async def fake_build_proxy_context(request, body):
        return _context(body, upstream_model="accounts/fireworks/routers/kimi-k2p7-code-fast")

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)

    response = client_fixture.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={"model": "kimi-k2.7-code-fast", "input": "hello", "stream": True},
    )
    assert response.status_code == 200, response.text
    assert route.called

    completed_event = None
    for line in response.text.strip().split("\n"):
        if line.startswith("data:"):
            payload = json.loads(line[5:].strip())
            if payload.get("type") == "response.completed":
                completed_event = payload

    assert completed_event is not None
    usage = completed_event["response"]["usage"]
    assert usage["output_tokens"] > 0, f"expected non-zero output_tokens, got {usage}"
    assert usage["input_tokens"] > 0
    assert usage.get("estimated") is True


_NATIVE_RESPONSES_BODY_TEXT_FIELD = "".join(
    [
        'event: response.created\ndata: {"id":"resp_1","object":"response","status":"in_progress","model":"kimi-k2.7-code-fast","output":[]}\n\n',
        'event: response.output_text.delta\ndata: {"output_index":0,"text":"hello world this is generated text"}\n\n',
        'event: response.output_item.done\ndata: {"output_index":0,"item":{"type":"message","id":"msg_1","role":"assistant","status":"completed","content":[{"type":"output_text","text":"hello world this is generated text"}]}}\n\n',
        'event: response.completed\ndata: {"id":"resp_1","object":"response","status":"completed","model":"kimi-k2.7-code-fast","output":[{"type":"message","id":"msg_1","role":"assistant","content":[{"type":"output_text","text":"hello world this is generated text"}]}],"usage":{"input_tokens":49513,"output_tokens":0,"total_tokens":49513}}\n\n',
    ]
)


@respx.mock
def test_responses_native_estimates_usage_when_fireworks_uses_text_field(
    client_fixture: TestClient, monkeypatch: MonkeyPatch
) -> None:
    """Fireworks native Responses sometimes uses `text` instead of `delta` in
    output_text.delta events. Ensure usage estimation still works.
    """
    route = respx.post("https://api.fireworks.ai/inference/v1/responses").respond(
        status_code=200,
        headers={"content-type": "text/event-stream", "x-request-id": "req-2"},
        text=_NATIVE_RESPONSES_BODY_TEXT_FIELD,
    )

    async def fake_build_proxy_context(request, body):
        return _context(body, upstream_model="accounts/fireworks/routers/kimi-k2p7-code-fast")

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)

    response = client_fixture.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={"model": "kimi-k2.7-code-fast", "input": "hello", "stream": True},
    )
    assert response.status_code == 200, response.text
    assert route.called

    completed_event = None
    for line in response.text.strip().split("\n"):
        if line.startswith("data:"):
            payload = json.loads(line[5:].strip())
            if payload.get("type") == "response.completed":
                completed_event = payload

    assert completed_event is not None
    usage = completed_event["response"]["usage"]
    assert usage["output_tokens"] > 0, f"expected non-zero output_tokens, got {usage}"
    assert usage["input_tokens"] > 0
    assert usage.get("estimated") is True


_NATIVE_RESPONSES_BODY_DONE_ONLY = "".join(
    [
        'event: response.created\ndata: {"id":"resp_1","object":"response","status":"in_progress","model":"kimi-k2.7-code-fast","output":[]}\n\n',
        'event: response.output_text.done\ndata: {"output_index":0,"text":"hello world this is generated text"}\n\n',
        'event: response.output_item.done\ndata: {"output_index":0,"item":{"type":"message","id":"msg_1","role":"assistant","status":"completed","content":[{"type":"output_text","text":"hello world this is generated text"}]}}\n\n',
        'event: response.completed\ndata: {"id":"resp_1","object":"response","status":"completed","model":"kimi-k2.7-code-fast","output":[{"type":"message","id":"msg_1","role":"assistant","content":[{"type":"output_text","text":"hello world this is generated text"}]}],"usage":{"input_tokens":49513,"output_tokens":0,"total_tokens":49513}}\n\n',
    ]
)


@respx.mock
def test_responses_native_estimates_usage_when_only_done_events_arrive(
    client_fixture: TestClient, monkeypatch: MonkeyPatch
) -> None:
    """Fireworks native Responses may omit output_text.delta and only emit done
    events. Under bridge_compat these events are dropped, but the proxy log must
    still estimate output from the actual generated text.
    """
    route = respx.post("https://api.fireworks.ai/inference/v1/responses").respond(
        status_code=200,
        headers={"content-type": "text/event-stream", "x-request-id": "req-3"},
        text=_NATIVE_RESPONSES_BODY_DONE_ONLY,
    )

    async def fake_build_proxy_context(request, body):
        return _context(body, upstream_model="accounts/fireworks/routers/kimi-k2p7-code-fast")

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)

    response = client_fixture.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={"model": "kimi-k2.7-code-fast", "input": "hello", "stream": True},
    )
    assert response.status_code == 200, response.text
    assert route.called

    completed_event = None
    for line in response.text.strip().split("\n"):
        if line.startswith("data:"):
            payload = json.loads(line[5:].strip())
            if payload.get("type") == "response.completed":
                completed_event = payload

    assert completed_event is not None
    usage = completed_event["response"]["usage"]
    assert usage["output_tokens"] > 0, f"expected non-zero output_tokens, got {usage}"
    assert usage["input_tokens"] > 0
    assert usage.get("estimated") is True


_NATIVE_RESPONSES_BODY_INCOMPLETE = "".join(
    [
        'event: response.created\ndata: {"id":"resp_1","object":"response","status":"in_progress","model":"kimi-k2.7-code-fast","output":[]}\n\n',
        'event: response.output_text.delta\ndata: {"output_index":0,"delta":"hello world this is generated text"}\n\n',
        'event: response.output_item.done\ndata: {"output_index":0,"item":{"type":"message","id":"msg_1","role":"assistant","status":"completed","content":[{"type":"output_text","text":"hello world this is generated text"}]}}\n\n',
        'event: response.incomplete\ndata: {"id":"resp_1","object":"response","status":"incomplete","model":"kimi-k2.7-code-fast","output":[{"type":"message","id":"msg_1","role":"assistant","content":[{"type":"output_text","text":"hello world this is generated text"}]}],"usage":{"input_tokens":49513,"output_tokens":0,"total_tokens":49513},"incomplete_details":{"reason":"max_output_tokens"}}\n\n',
    ]
)


@respx.mock
def test_responses_native_estimates_usage_on_incomplete_event(
    client_fixture: TestClient, monkeypatch: MonkeyPatch
) -> None:
    """Long outputs may hit max_output_tokens and end with response.incomplete
    instead of response.completed. The incomplete event must still receive an
    estimated output_tokens so downstream clients see non-zero usage.
    """
    route = respx.post("https://api.fireworks.ai/inference/v1/responses").respond(
        status_code=200,
        headers={"content-type": "text/event-stream", "x-request-id": "req-4"},
        text=_NATIVE_RESPONSES_BODY_INCOMPLETE,
    )

    async def fake_build_proxy_context(request, body):
        return _context(body, upstream_model="accounts/fireworks/routers/kimi-k2p7-code-fast")

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context)

    response = client_fixture.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={"model": "kimi-k2.7-code-fast", "input": "hello", "stream": True},
    )
    assert response.status_code == 200, response.text
    assert route.called

    incomplete_event = None
    for line in response.text.strip().split("\n"):
        if line.startswith("data:"):
            payload = json.loads(line[5:].strip())
            if payload.get("type") == "response.incomplete":
                incomplete_event = payload

    assert incomplete_event is not None
    usage = incomplete_event["response"]["usage"]
    assert usage["output_tokens"] > 0, f"expected non-zero output_tokens, got {usage}"
    assert usage["input_tokens"] > 0
    assert usage.get("estimated") is True
