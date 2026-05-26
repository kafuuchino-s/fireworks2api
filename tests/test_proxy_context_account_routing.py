from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.dataplane.routing.affinity import client_identity_from_request, extract_stable_key
from app.products.openai.context import build_proxy_context_from_body, build_proxy_context_optional_model


class _Repo:
    def __init__(self, keys, snapshots=None, cooldowns=None):
        self._keys = keys
        self._snapshots = snapshots or []
        self._cooldowns = cooldowns or []

    def list_keys(self, include_disabled=True):
        return self._keys

    def list_fireworks_key_snapshots(self):
        return self._snapshots

    def list_account_cooldowns(self):
        return self._cooldowns


def _key(name: str, fingerprint: str, enabled: bool = True):
    return SimpleNamespace(name=name, fingerprint=fingerprint, enabled=enabled, cooldown_until=None)


def _snapshot(fp: str, account_id: str, *, stale_after: datetime, quota_status: str = "ok"):
    return SimpleNamespace(key_fingerprint=fp, account_id=account_id, stale_after=stale_after.isoformat(), quota_status=quota_status)


def _context(repo, body=None, headers=None):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(allow_unknown_model_passthrough=True, affinity_hash_secret="a", log_hash_secret="b", max_upstream_attempts=5), repository=repo)),
        client=SimpleNamespace(host="127.0.0.1", port=1234),
        headers=headers or {},
        json=lambda: body,
    )


def test_client_identity_ignores_ephemeral_client_port_for_fallback_affinity() -> None:
    identity_a = client_identity_from_request({}, "127.0.0.1", 1111)
    identity_b = client_identity_from_request({}, "127.0.0.1", 2222)

    assert identity_a == identity_b == "127.0.0.1"


def test_previous_response_id_used_as_stable_key_after_headers() -> None:
    stable_key, source = extract_stable_key({"previous_response_id": "resp_prev"}, {}, "model", "client")

    assert stable_key == "resp_prev"
    assert source == "body.previous_response_id"


@pytest.mark.asyncio
async def test_build_proxy_context_keeps_route_key_and_metadata():
    repo = _Repo([_key("k1", "fp-1"), _key("k2", "fp-2")])
    request = _context(repo, body={"model": "alias", "messages": []})

    ctx = await build_proxy_context_from_body(request, {"model": "alias", "messages": []})

    assert ctx.route_key
    assert ctx.routing_metadata["stable_key_source"]
    assert ctx.routing_metadata["affinity_header"]
    assert ctx.routing_metadata["selected_key_count"] == len(ctx.selected_keys)


@pytest.mark.asyncio
async def test_missing_snapshot_methods_still_works():
    class RepoNoSnapshots:
        def list_keys(self, include_disabled=True):
            return [_key("k1", "fp-1")]

    request = _context(RepoNoSnapshots(), body={"model": "alias", "messages": []})
    ctx = await build_proxy_context_from_body(request, {"model": "alias", "messages": []})
    assert ctx.selected_keys
    assert ctx.routing_metadata["selected_key_count"] == 1


@pytest.mark.asyncio
async def test_fresh_exhausted_snapshot_removes_sibling_keys():
    now = datetime.now(UTC)
    repo = _Repo(
        [_key("k1", "fp-1"), _key("k2", "fp-2")],
        snapshots=[_snapshot("fp-1", "acct-a", stale_after=now + timedelta(minutes=5), quota_status="exhausted"), _snapshot("fp-2", "acct-a", stale_after=now + timedelta(minutes=5))],
    )
    request = _context(repo, body={"model": "alias", "messages": []})
    ctx = await build_proxy_context_from_body(request, {"model": "alias", "messages": []})
    assert ctx.selected_keys == []
    assert ctx.routing_metadata["skipped_account_count"] == 1


@pytest.mark.asyncio
async def test_stale_exhausted_snapshot_removes_candidates():
    now = datetime.now(UTC)
    repo = _Repo(
        [_key("k1", "fp-1"), _key("k2", "fp-2")],
        snapshots=[_snapshot("fp-1", "acct-a", stale_after=now - timedelta(minutes=5), quota_status="exhausted")],
    )
    request = _context(repo, body={"model": "alias", "messages": []})
    ctx = await build_proxy_context_from_body(request, {"model": "alias", "messages": []})
    assert {k.fingerprint for k in ctx.selected_keys} == {"fp-2"}


@pytest.mark.asyncio
async def test_optional_model_context_has_safe_routing_metadata():
    repo = _Repo([_key("k1", "fp-1")])
    request = _context(repo, body={})
    ctx = await build_proxy_context_optional_model(request, {}, route_seed="seed")
    assert ctx.routing_metadata["routing_mode"]
    assert "route_key" not in ctx.routing_metadata
    assert "route_key" not in str(ctx.routing_metadata)
