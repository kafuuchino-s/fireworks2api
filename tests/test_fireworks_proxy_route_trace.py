from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.dataplane.fireworks import proxy as proxy_mod


class _Repo:
    def __init__(self):
        self.request_logs = []
        self.transform_debug = []
        self.snapshots = {}

    def insert_request_log(self, payload, retention):
        self.request_logs.append(payload)
        return f"log-{len(self.request_logs)}"

    def record_transform_debug(self, payload, retention):
        self.transform_debug.append((payload, retention))

    def get_fireworks_key_snapshot(self, fingerprint):
        return self.snapshots.get(fingerprint)

    def set_account_cooldown(self, account_id, cooldown_until, error_type):
        self.account_cooldown = (account_id, cooldown_until, error_type)


class _Client:
    def __init__(self, response):
        self._response = response

    async def get_json(self, *args, **kwargs):
        return self._response

    async def delete_json(self, *args, **kwargs):
        return self._response

    async def post_json(self, *args, **kwargs):
        return self._response

    async def post_stream(self, *args, **kwargs):
        return self._response


def _response(status_code=200, body=b"{}", headers=None):
    return httpx.Response(status_code, content=body, headers=headers or {}, request=httpx.Request("GET", "https://example.com"))


def _context(*, transform_debug_enabled=True, selected_keys=None):
    repo = _Repo()
    return SimpleNamespace(
        settings=SimpleNamespace(transform_debug_enabled=transform_debug_enabled, transform_debug_retention=7, request_log_retention=20),
        repository=repo,
        selected_keys=selected_keys if selected_keys is not None else [SimpleNamespace(name="k1", api_key="key1", fingerprint="fp1")],
        model_name="alias",
        resolved_model=SimpleNamespace(upstream_model="upstream-model"),
        stable_key_hash_value="stable-hash",
        routing_metadata={
            "routing_mode": "account_aware_sticky",
            "primary_account_bucket": "account:acct-a",
            "selected_account_count": 2,
            "skipped_account_count": 0,
            "selected_key_count": len(selected_keys) if selected_keys is not None else 1,
        },
    )


@pytest.mark.asyncio
async def test_route_trace_not_written_when_disabled(monkeypatch):
    context = _context(transform_debug_enabled=False)
    monkeypatch.setattr(proxy_mod, "FireworksClient", lambda settings, api_key: _Client(_response(200, b'{"usage":{"input_tokens":1,"output_tokens":2}}')))

    await proxy_mod.proxy_fireworks_json_request(context, endpoint="responses", method="GET", upstream_path="responses/1", headers={}, route_trace={"trace": 1})

    assert context.repository.transform_debug == []


@pytest.mark.asyncio
async def test_route_trace_written_on_success(monkeypatch):
    context = _context()
    monkeypatch.setattr(proxy_mod, "FireworksClient", lambda settings, api_key: _Client(_response(200, b'{"usage":{"input_tokens":1,"output_tokens":2,"prompt_tokens_details":{"cached_tokens":3}}}', {"x-request-id": "up-1"})))

    await proxy_mod.proxy_fireworks_json_request(context, endpoint="responses", method="GET", upstream_path="responses/1", headers={}, route_trace={"trace": 1})

    payload, retention = context.repository.transform_debug[0]
    assert retention == 7
    assert payload["route_trace"]["result"]["status_code"] == 200
    assert payload["route_trace"]["result"]["selected_key_fingerprint"] == "fp1"
    assert "usage" in payload["route_trace"]["result"]
    assert payload["route_trace"]["result"]["routing"]["mode"] == "account_aware_sticky"
    assert payload["route_trace"]["result"]["routing"]["attempts"] == [{"action": "attempt", "key_fingerprint": "fp1"}]


@pytest.mark.asyncio
async def test_route_trace_written_on_error(monkeypatch):
    context = _context()

    monkeypatch.setattr(proxy_mod, "FireworksClient", lambda settings, api_key: _Client(_response(400, b"error", {"x-request-id": "up-err"})))

    await proxy_mod.proxy_fireworks_json_request(context, endpoint="responses", method="GET", upstream_path="responses/1", headers={}, route_trace={"trace": 1})

    assert context.repository.transform_debug[0][0]["route_trace"]["result"]["error_type"] is not None


@pytest.mark.asyncio
async def test_no_route_trace_unchanged(monkeypatch):
    context = _context()
    monkeypatch.setattr(proxy_mod, "FireworksClient", lambda settings, api_key: _Client(_response(200)))

    await proxy_mod.proxy_fireworks_json_request(context, endpoint="responses", method="GET", upstream_path="responses/1", headers={})

    assert context.repository.transform_debug == []


@pytest.mark.asyncio
async def test_no_healthy_keys_error_path(monkeypatch):
    context = _context(selected_keys=[])

    with pytest.raises(Exception):
        await proxy_mod.proxy_fireworks_request(context, endpoint="chat_completions", upstream_path="chat/completions", payload={"stream": False}, headers={}, route_trace={"trace": 1})

    assert context.repository.transform_debug[0][0]["route_trace"]["result"]["status_code"] == 503


@pytest.mark.asyncio
async def test_route_trace_records_account_skip_on_quota_failover(monkeypatch):
    keys = [
        SimpleNamespace(name="k1", api_key="key1", fingerprint="fp1"),
        SimpleNamespace(name="k2", api_key="key2", fingerprint="fp2"),
        SimpleNamespace(name="k3", api_key="key3", fingerprint="fp3"),
    ]
    context = _context(selected_keys=keys)
    context.repository.snapshots = {
        "fp1": SimpleNamespace(account_id="acct-a"),
        "fp2": SimpleNamespace(account_id="acct-a"),
        "fp3": SimpleNamespace(account_id="acct-b"),
    }
    responses = [
        _response(429, b'{"error":{"message":"quota exhausted"}}'),
        _response(200, b'{"usage":{"input_tokens":1}}'),
    ]

    class SequencedClient:
        def __init__(self, settings, api_key):
            self.response = responses.pop(0)

        async def post_json(self, *args, **kwargs):
            return self.response

    monkeypatch.setattr(proxy_mod, "FireworksClient", SequencedClient)

    await proxy_mod.proxy_fireworks_request(context, endpoint="chat_completions", upstream_path="chat/completions", payload={"stream": False}, headers={}, route_trace={"trace": 1})

    result_routing = context.repository.transform_debug[0][0]["route_trace"]["result"]["routing"]
    assert result_routing["blocked_account_buckets"] == ["account:acct-a"]
    assert result_routing["attempts"] == [
        {"action": "attempt", "key_fingerprint": "fp1", "account_bucket": "account:acct-a"},
        {"action": "failover", "key_fingerprint": "fp1", "account_bucket": "account:acct-a", "error_type": "quota_exhausted", "scope": "account"},
        {"action": "skip", "key_fingerprint": "fp2", "account_bucket": "account:acct-a", "scope": "account"},
        {"action": "attempt", "key_fingerprint": "fp3", "account_bucket": "account:acct-b"},
    ]
