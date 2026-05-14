from __future__ import annotations

from types import SimpleNamespace

from app.dataplane.fireworks.headers import build_upstream_headers


def _context(**overrides):
    settings = SimpleNamespace(
        responses_cache_fields_enabled=True,
        affinity_hash_secret="affinity-secret",
        log_hash_secret="log-secret",
    )
    base = SimpleNamespace(
        settings=settings,
        request_headers={
            "x-session-affinity": "affinity-token",
            "x-multi-turn-session-id": "session-123",
            "x-prompt-cache-isolation-key": "iso-123",
            "x-unrelated": "drop-me",
        },
        stable_key="stable-key",
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_chat_headers_allowlist_fireworks_affinity_headers() -> None:
    context = _context()

    headers = build_upstream_headers(
        context.request_headers,
        stable_key=context.stable_key,
        affinity_hash_secret=context.settings.affinity_hash_secret,
    )

    assert headers == {
        "x-session-affinity": "affinity-token",
        "x-multi-turn-session-id": "session-123",
        "x-prompt-cache-isolation-key": "iso-123",
    }


def test_responses_headers_allowlist_fireworks_affinity_headers() -> None:
    context = _context()

    headers = build_upstream_headers(
        context.request_headers,
        stable_key=context.stable_key,
        affinity_hash_secret=context.settings.log_hash_secret,
    )

    assert headers == {
        "x-session-affinity": "affinity-token",
        "x-multi-turn-session-id": "session-123",
        "x-prompt-cache-isolation-key": "iso-123",
    }
