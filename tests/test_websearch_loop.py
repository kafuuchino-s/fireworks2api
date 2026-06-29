from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import respx
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

import app.platform.auth as auth
from app.main import app
from app.platform.config import Settings, get_settings
import app.products.openai.responses as responses_mod
from app.dataplane.websearch.grok_search import grok_web_search
from app.dataplane.websearch.sources import split_answer_and_sources


# --------------------------------------------------------------------------- #
# split_answer_and_sources — pure function unit tests
# --------------------------------------------------------------------------- #


def test_split_markdown_link_sources() -> None:
    text = "The sky is blue.\n\nSources:\n- [Wikipedia](https://en.wikipedia.org/sky)\n- https://example.com/sky"
    answer, sources = split_answer_and_sources(text)
    assert "The sky is blue." in answer
    assert "Sources" not in answer
    assert len(sources) == 2
    assert sources[0] == {"title": "Wikipedia", "url": "https://en.wikipedia.org/sky"}
    assert sources[1] == {"url": "https://example.com/sky"}


def test_split_function_call_sources() -> None:
    text = "Paris is the capital.\n\nsources([\n  [\"Wiki\", \"https://wiki.example/paris\"],\n  {\"url\": \"https://news.example/paris\"}\n])"
    answer, sources = split_answer_and_sources(text)
    assert answer.startswith("Paris is the capital.")
    urls = [s["url"] for s in sources]
    assert "https://wiki.example/paris" in urls
    assert "https://news.example/paris" in urls


def test_split_tail_link_block_sources() -> None:
    text = "Some answer.\n\nhttps://a.example.com/x\nhttps://b.example.com/y"
    answer, sources = split_answer_and_sources(text)
    assert answer.strip() == "Some answer."
    assert {s["url"] for s in sources} == {"https://a.example.com/x", "https://b.example.com/y"}


def test_split_no_sources_returns_full_text() -> None:
    text = "Just an answer with no links at all."
    answer, sources = split_answer_and_sources(text)
    assert answer == text
    assert sources == []


def test_split_empty_text() -> None:
    assert split_answer_and_sources("") == ("", [])
    assert split_answer_and_sources("   ") == ("", [])


# --------------------------------------------------------------------------- #
# grok_web_search — mocked Grok streaming endpoint
# --------------------------------------------------------------------------- #


def _settings(**overrides) -> Settings:
    base = dict(
        grok_api_url="https://grok.example.com/v1",
        grok_api_key="grok-key",
        grok_model="grok-4-fast",
        web_search_enabled=True,
        web_search_max_iterations=3,
        web_search_timeout_seconds=30.0,
    )
    base.update(overrides)
    return Settings(**base)


def _sse_chunks(text: str) -> str:
    """Build a minimal SSE body of chat.completions chunks carrying delta.content."""
    chunks = []
    piece = text[: len(text) // 2] or text
    rest = text[len(text) // 2 :] if piece else ""
    for part in (piece, rest):
        if not part:
            continue
        chunks.append("data: " + json.dumps({"choices": [{"delta": {"content": part}}]}) + "\n\n")
    chunks.append("data: [DONE]\n\n")
    return "".join(chunks)


@respx.mock
@pytest.mark.asyncio
async def test_grok_web_search_parses_answer_and_sources() -> None:
    body = "The model is grok-4.\n\nSources:\n- [Docs](https://docs.example/grok)"
    route = respx.post("https://grok.example.com/v1/chat/completions").mock(
        return_value=respx.MockResponse(status_code=200, text=_sse_chunks(body), headers={"content-type": "text/event-stream"})
    )
    answer, sources = await grok_web_search(_settings(), "what is grok")
    assert route.called
    assert "The model is grok-4." in answer
    assert "Sources" not in answer
    assert sources == [{"title": "Docs", "url": "https://docs.example/grok"}]


@respx.mock
@pytest.mark.asyncio
async def test_grok_web_search_missing_api_key_raises() -> None:
    with pytest.raises(ValueError):
        await grok_web_search(_settings(grok_api_key=None), "q")


@respx.mock
@pytest.mark.asyncio
async def test_grok_web_search_retries_then_fails_gracefully() -> None:
    respx.post("https://grok.example.com/v1/chat/completions").mock(
        return_value=respx.MockResponse(status_code=503, text="busy")
    )
    answer, sources = await grok_web_search(_settings(web_search_max_iterations=1), "q")
    assert "failed" in answer
    assert sources == []


# --------------------------------------------------------------------------- #
# run_responses_web_search_loop / route integration
# --------------------------------------------------------------------------- #


def _context(body: dict, *, settings: Settings) -> SimpleNamespace:
    key = SimpleNamespace(name="key-1", api_key="fw-test-key", fingerprint="fp-1")
    return SimpleNamespace(
        settings=settings,
        repository=SimpleNamespace(
            insert_request_log=lambda *a, **k: None,
            get_response_key_route=lambda rid: None,
            upsert_response_key_route=lambda rid, k: None,
            delete_response_key_route=lambda rid: None,
        ),
        body=body,
        model_name=body.get("model", "test"),
        resolved_model=SimpleNamespace(upstream_model="accounts/fireworks/models/test"),
        client_identity="client",
        stable_key="stable",
        stable_key_source="model",
        stable_key_hash_value="hash",
        affinity_header="affinity",
        route_key="route",
        selected_keys=[key],
        routing_metadata=None,
    )


@pytest.fixture
def websearch_client(monkeypatch: MonkeyPatch) -> TestClient:
    get_settings.cache_clear()
    settings = Settings(
        proxy_api_keys=["token"],
        admin_token="token",
        affinity_hash_secret="secret",
        log_hash_secret="secret",
        upstream_base_url="https://api.fireworks.ai/inference/v1",
        max_upstream_attempts=1,
        request_timeout_seconds=120.0,
        allow_unknown_model_passthrough=True,
        request_log_retention=30,
        web_search_enabled=True,
        grok_api_url="https://grok.example.com/v1",
        grok_api_key="grok-key",
        grok_model="grok-4-fast",
        web_search_max_iterations=3,
        web_search_timeout_seconds=30.0,
    )
    monkeypatch.setattr(app.state, "settings", settings, raising=False)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    return TestClient(app)


def _function_call_response(query: str, *, response_id: str = "resp_1") -> dict:
    return {
        "id": response_id,
        "object": "response",
        "status": "completed",
        "model": "accounts/fireworks/models/test",
        "output": [
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "web_search",
                "arguments": json.dumps({"query": query}),
            }
        ],
    }


def _final_response(text: str, *, response_id: str = "resp_2") -> dict:
    return {
        "id": response_id,
        "object": "response",
        "status": "completed",
        "model": "accounts/fireworks/models/test",
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
    }


@respx.mock
def test_web_search_loop_end_to_end(websearch_client: TestClient, monkeypatch: MonkeyPatch) -> None:
    # First Fireworks call -> function_call(web_search); second -> final message.
    respx.post("https://api.fireworks.ai/inference/v1/responses").mock(
        side_effect=[
            respx.MockResponse(status_code=200, content_type="application/json", json=_function_call_response("latest grok news")),
            respx.MockResponse(status_code=200, content_type="application/json", json=_final_response("Grok 4 was released.")),
        ]
    )
    grok_answer = "Grok 4 launched.\n\nSources:\n- [Blog](https://blog.example/grok4)"
    respx.post("https://grok.example.com/v1/chat/completions").mock(
        return_value=respx.MockResponse(status_code=200, text=_sse_chunks(grok_answer), headers={"content-type": "text/event-stream"})
    )

    async def fake_build_proxy_context_from_body(request, body):
        return _context(body, settings=app.state.settings)

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context_from_body)

    response = websearch_client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={
            "model": "test",
            "input": "What is the latest grok news?",
            "tools": [{"type": "web_search"}],
            "stream": False,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    ws_calls = [item for item in data["output"] if item.get("type") == "web_search_call"]
    assert ws_calls, "expected a web_search_call item in the output"
    assert ws_calls[0]["queries"] == ["latest grok news"]
    assert any(s["url"] == "https://blog.example/grok4" for s in ws_calls[0]["sources"])
    # The sources block must be appended to the assistant message text.
    msg = next(item for item in data["output"] if item.get("type") == "message")
    assert "https://blog.example/grok4" in msg["content"][0]["text"]


@respx.mock
def test_web_search_loop_without_web_search_tool_pasessthrough(websearch_client: TestClient, monkeypatch: MonkeyPatch) -> None:
    respx.post("https://api.fireworks.ai/inference/v1/responses").mock(
        return_value=respx.MockResponse(status_code=200, content_type="application/json", json=_final_response("plain answer"))
    )

    async def fake_build_proxy_context_from_body(request, body):
        return _context(body, settings=app.state.settings)

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context_from_body)

    response = websearch_client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={"model": "test", "input": "hi", "tools": [{"type": "function", "name": "noop"}], "stream": False},
    )
    # No web_search tool -> normal passthrough (function tool without params is rejected
    # by validation upstream of the loop, so this asserts the loop did not engage and the
    # request either errors or proxies). The key invariant: no web_search_call present.
    assert response.status_code in (200, 400)
    if response.status_code == 200:
        data = response.json()
        assert not [i for i in data.get("output", []) if i.get("type") == "web_search_call"]


def test_web_search_loop_disabled_errors(websearch_client: TestClient, monkeypatch: MonkeyPatch) -> None:
    # Disable web search on the live app settings for this test.
    monkeypatch.setattr(app.state.settings, "web_search_enabled", False)

    async def fake_build_proxy_context_from_body(request, body):
        return _context(body, settings=app.state.settings)

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context_from_body)

    response = websearch_client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={"model": "test", "input": "hi", "tools": [{"type": "web_search"}], "stream": False},
    )
    assert response.status_code == 400
    assert "web_search" in response.json()["error"]["message"]


@respx.mock
def test_web_search_loop_streaming_downgraded_to_non_streaming(websearch_client: TestClient, monkeypatch: MonkeyPatch) -> None:
    respx.post("https://api.fireworks.ai/inference/v1/responses").mock(
        side_effect=[
            respx.MockResponse(status_code=200, content_type="application/json", json=_function_call_response("query")),
            respx.MockResponse(status_code=200, content_type="application/json", json=_final_response("done")),
        ]
    )
    respx.post("https://grok.example.com/v1/chat/completions").mock(
        return_value=respx.MockResponse(status_code=200, text=_sse_chunks("answer.\n\nSources:\n- https://x.example"), headers={"content-type": "text/event-stream"})
    )

    async def fake_build_proxy_context_from_body(request, body):
        return _context(body, settings=app.state.settings)

    monkeypatch.setattr(responses_mod, "build_proxy_context_from_body", fake_build_proxy_context_from_body)

    response = websearch_client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer token"},
        json={"model": "test", "input": "hi", "tools": [{"type": "web_search"}], "stream": True},
    )
    # Stream=True with web_search is downgraded to a single JSON response.
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    data = response.json()
    assert [i for i in data["output"] if i.get("type") == "web_search_call"]
