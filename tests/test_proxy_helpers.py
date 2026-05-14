from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.products.openai.proxy_common import (
    build_chat_upstream_payload,
    build_proxy_context_from_body,
    build_proxy_context_optional_model,
    build_responses_upstream_payload,
)
from app.products.openai.errors import OpenAIRequestError
from app.products.openai.fireworks_native import responses as native_responses
from app.products.openai.transform_debug import build_transform_debug_summary, record_transform_debug
from app.dataplane.routing.failover import classify_upstream_failure
from app.dataplane.usage import extract_usage


def _context(**overrides):
    settings = SimpleNamespace(
        log_hash_secret="secret",
        affinity_hash_secret="affinity-secret",
        responses_cache_fields_enabled=False,
    )
    resolved_model = SimpleNamespace(
        upstream_model="accounts/fireworks/models/test",
    )
    base = SimpleNamespace(
        settings=settings,
        body={"model": "test", "messages": []},
        model_name="test",
        resolved_model=resolved_model,
        stable_key="stable",
        route_key="test:stable",
        affinity_header="affinity",
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_chat_payload_does_not_inject_priority_from_model_mode() -> None:
    context = _context(
        resolved_model=SimpleNamespace(
            upstream_model="accounts/fireworks/models/test",
        ),
        body={
            "model": "test",
            "messages": [],
            "thinking": {"enabled": True},
            "prompt_cache_key": "keep-me",
        },
    )

    payload = build_chat_upstream_payload(context)

    assert payload["model"] == "accounts/fireworks/models/test"
    assert "service_tier" not in payload
    assert payload["prompt_cache_key"] == "keep-me"
    assert payload["thinking"] == {"enabled": True}


def test_chat_payload_rejects_conflicting_reasoning_fields() -> None:
    context = _context(
        body={
            "model": "test",
            "messages": [],
            "thinking": {"enabled": True},
            "reasoning_effort": "high",
        },
    )

    with pytest.raises(Exception):
        build_chat_upstream_payload(context)


class _DummyRequest:
    def __init__(self) -> None:
        self.headers = {}
        self.client = SimpleNamespace(host="127.0.0.1", port=1234)
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    log_hash_secret="secret",
                    affinity_hash_secret="affinity-secret",
                    max_upstream_attempts=1,
                    allow_unknown_model_passthrough=False,
                ),
                repository=SimpleNamespace(
                    list_keys=lambda include_disabled=True: [SimpleNamespace(name="k1", fingerprint="abc123", enabled=True, cooldown_until=None, disabled=False)],
                ),
            )
        )

    async def json(self):
        raise AssertionError("json() should not be called")


@pytest.mark.asyncio
async def test_build_proxy_context_from_body_skips_request_json() -> None:
    request = _DummyRequest()
    from app.products.openai import context as context_module

    def _resolve_model(repository, model_name, allow_unknown_model_passthrough):
        return SimpleNamespace(upstream_model="accounts/fireworks/models/test", requested_model=model_name, alias="test")

    original = context_module.resolve_model
    context_module.resolve_model = _resolve_model
    try:
        context = await build_proxy_context_from_body(request, {"model": "test", "messages": []})
    finally:
        context_module.resolve_model = original

    assert context.model_name == "test"


@pytest.mark.asyncio
async def test_optional_model_missing_does_not_raise() -> None:
    request = _DummyRequest()

    context = await build_proxy_context_optional_model(request, {}, route_seed="seed-model")

    assert context.model_name == "seed-model"
    assert context.selected_keys


@pytest.mark.asyncio
async def test_optional_model_none_does_not_raise() -> None:
    request = _DummyRequest()

    context = await build_proxy_context_optional_model(request, {"model": None}, route_seed="seed-model")

    assert context.model_name == "seed-model"
    assert context.selected_keys


@pytest.mark.asyncio
async def test_optional_model_alias_resolves_upstream_model(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _DummyRequest()

    from app.products.openai import context as context_module

    def _resolve_model(repository, model_name, allow_unknown_model_passthrough):
        return SimpleNamespace(upstream_model="accounts/fireworks/models/upstream", requested_model=model_name, alias="alias")

    monkeypatch.setattr(context_module, "resolve_model", _resolve_model)

    context = await build_proxy_context_optional_model(request, {"model": "alias"}, route_seed="seed-model")

    assert context.resolved_model.upstream_model == "accounts/fireworks/models/upstream"


def test_chat_payload_rejects_unsupported_service_tier() -> None:
    context = _context(body={"model": "test", "messages": [], "service_tier": "  flex  "})

    payload = build_chat_upstream_payload(context)

    assert "service_tier" not in payload


def test_chat_payload_rejects_invalid_service_tier() -> None:
    context = _context(body={"model": "test", "messages": [], "service_tier": "invalid-tier"})

    with pytest.raises(Exception):
        build_chat_upstream_payload(context)


def test_responses_payload_preserves_user_fields() -> None:
    context = _context(
        body={
            "model": "test",
            "input": "hello",
            "prompt_cache_key": "keep-me-out",
            "prompt_cache_isolation_key": "iso",
            "user": "user-1",
        },
    )

    payload = build_responses_upstream_payload(context)

    assert payload["model"] == "accounts/fireworks/models/test"
    assert payload["input"] == "hello"
    assert payload["prompt_cache_key"] == "keep-me-out"
    assert payload["prompt_cache_isolation_key"] == "iso"
    assert payload["user"] == "user-1"


def test_priority_responses_are_rejected() -> None:
    context = _context(
        resolved_model=SimpleNamespace(
            upstream_model="accounts/fireworks/models/test",
        ),
        body={"model": "test", "input": "hello", "service_tier": "priority"},
    )

    with pytest.raises(OpenAIRequestError) as exc:
        native_responses.validate_responses_body(context.body)

    assert exc.value.code == "unsupported_parameter"
    assert "service_tier" in str(exc.value)


def test_usage_parser_handles_cached_tokens() -> None:
    usage = extract_usage(
        {
            "usage": {
                "input_tokens": 10,
                "output_tokens": 3,
                "prompt_tokens_details": {"cached_tokens": 4},
            }
        }
    )

    assert usage.input_tokens == 10
    assert usage.output_tokens == 3
    assert usage.cached_tokens == 4


@pytest.mark.parametrize(
    ("status_code", "retryable"),
    [
        (400, False),
        (429, True),
        (500, True),
    ],
)
def test_failover_classifier(status_code: int, retryable: bool) -> None:
    decision = classify_upstream_failure(status_code)
    assert decision.retryable is retryable


def test_transform_debug_disabled_does_not_log() -> None:
    repo = SimpleNamespace(record_transform_debug=lambda *args: (_ for _ in ()).throw(AssertionError("should not log")))
    settings = SimpleNamespace(transform_debug_enabled=False, transform_debug_retention=7)
    record_transform_debug(repo, settings, {"endpoint": "chat_completions"})


def test_transform_debug_summary_sanitizes_fields() -> None:
    summary = build_transform_debug_summary(
        endpoint="chat_completions",
        upstream_endpoint="chat/completions",
        model_alias="alias",
        upstream_model="upstream-model",
        stream=True,
        service_tier="priority",
        stable_key_source="route",
        payload={"model": "alias", "messages": [{"content": "secret"}], "stream": True},
        forwarded_headers={"x-session-affinity": "aff", "authorization": "nope"},
        field_changes=[{"field": "model", "from": "alias", "to": "upstream-model"}],
        warnings=["warn"],
        response_status_code=200,
        error_type=None,
        latency_ms=12,
    )

    assert summary["payload_fields"] == ["messages", "model", "stream"]
    assert summary["forwarded_headers"] == ["x-session-affinity"]
    assert summary["field_changes"][0]["field"] == "model"
    assert "secret" not in repr(summary)


def test_transform_debug_enabled_records_summary() -> None:
    captured = {}

    def _record(payload, retention):
        captured["payload"] = payload
        captured["retention"] = retention

    repo = SimpleNamespace(record_transform_debug=_record)
    settings = SimpleNamespace(transform_debug_enabled=True, transform_debug_retention=9)
    record_transform_debug(repo, settings, {"endpoint": "chat_completions"})

    assert captured["payload"]["endpoint"] == "chat_completions"
    assert captured["retention"] == 9
