from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.dataplane.fireworks import proxy as proxy_mod


class _Repo:
    def __init__(self):
        self.request_logs = []
        self.transform_debug = []
        self.snapshots = {
            "fp-a1": SimpleNamespace(account_id="acct-a"),
            "fp-a2": SimpleNamespace(account_id="acct-a"),
            "fp-b1": SimpleNamespace(account_id="acct-b"),
        }
        self.keys = [
            SimpleNamespace(name="k-a1", fingerprint="fp-a1"),
            SimpleNamespace(name="k-a2", fingerprint="fp-a2"),
            SimpleNamespace(name="k-b1", fingerprint="fp-b1"),
        ]

    def get_fireworks_key_snapshot(self, fingerprint):
        return self.snapshots.get(fingerprint)

    def set_account_cooldown(self, *args, **kwargs):
        self.account_cooldown = (args, kwargs)

    def set_key_cooldown(self, *args, **kwargs):
        self.key_cooldown = (args, kwargs)

    def set_key_enabled(self, *args, **kwargs):
        self.key_enabled = (args, kwargs)
        self.key_enabled_calls = getattr(self, "key_enabled_calls", []) + [(args, kwargs)]

    def list_keys(self, include_disabled: bool = True):
        return list(self.keys)

    def insert_request_log(self, payload, retention):
        self.request_logs.append(payload)
        return f"log-{len(self.request_logs)}"

    def record_transform_debug(self, payload, retention):
        self.transform_debug.append((payload, retention))


class _Response(httpx.Response):
    pass


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def post_json(self, *args, **kwargs):
        response = self.responses[self.calls]
        self.calls += 1
        return response

    async def post_stream(self, *args, **kwargs):
        return await self.post_json(*args, **kwargs)

    async def get_json(self, *args, **kwargs):
        return await self.post_json(*args, **kwargs)

    async def delete_json(self, *args, **kwargs):
        return await self.post_json(*args, **kwargs)


def _resp(status_code, body, fingerprint):
    return httpx.Response(status_code, content=body, headers={"x-request-id": f"req-{fingerprint}"}, request=httpx.Request("POST", "https://example.com"))


@pytest.mark.asyncio
async def test_quota_account_failure_skips_sibling_and_succeeds_on_other_account(monkeypatch):
    context = SimpleNamespace(
        settings=SimpleNamespace(transform_debug_enabled=False, transform_debug_retention=0, request_log_retention=0),
        repository=_Repo(),
        selected_keys=[
            SimpleNamespace(name="k-a1", api_key="a1", fingerprint="fp-a1"),
            SimpleNamespace(name="k-a2", api_key="a2", fingerprint="fp-a2"),
            SimpleNamespace(name="k-b1", api_key="b1", fingerprint="fp-b1"),
        ],
        model_name="alias",
        resolved_model=SimpleNamespace(upstream_model="upstream-model"),
        stable_key_hash_value="stable-hash",
    )
    clients = [_Client([_resp(429, b'{"error":{"type":"quota_exhausted"}}', "fp-a1")]), _Client([_resp(200, b"{}", "fp-b1")])]
    monkeypatch.setattr(proxy_mod, "FireworksClient", lambda settings, api_key: clients.pop(0))

    response = await proxy_mod.proxy_fireworks_request(context, endpoint="chat_completions", upstream_path="chat/completions", payload={"stream": False}, headers={})

    assert response.status_code == 200
    assert len(context.repository.request_logs) >= 1


@pytest.mark.asyncio
async def test_auth_failure_disables_bad_key_and_succeeds_on_next_key(monkeypatch):
    context = SimpleNamespace(
        settings=SimpleNamespace(transform_debug_enabled=False, transform_debug_retention=0, request_log_retention=0),
        repository=_Repo(),
        selected_keys=[
            SimpleNamespace(name="k-bad", api_key="bad", fingerprint="fp-bad"),
            SimpleNamespace(name="k-good", api_key="good", fingerprint="fp-b1"),
        ],
        model_name="alias",
        resolved_model=SimpleNamespace(upstream_model="upstream-model"),
        stable_key_hash_value="stable-hash",
    )
    clients = [_Client([_resp(401, b'{"error":{"message":"invalid api key"}}', "fp-bad")]), _Client([_resp(200, b"{}", "fp-b1")])]
    monkeypatch.setattr(proxy_mod, "FireworksClient", lambda settings, api_key: clients.pop(0))

    response = await proxy_mod.proxy_fireworks_request(context, endpoint="chat_completions", upstream_path="chat/completions", payload={"stream": False}, headers={})

    assert response.status_code == 200
    args, _kwargs = context.repository.key_enabled
    assert args[:4] == ("k-bad", False, "upstream_auth_failed", "auth_error")


@pytest.mark.asyncio
async def test_suspended_account_disables_sibling_keys_and_succeeds_on_other_account(monkeypatch):
    repo = _Repo()
    context = SimpleNamespace(
        settings=SimpleNamespace(transform_debug_enabled=False, transform_debug_retention=0, request_log_retention=0),
        repository=repo,
        selected_keys=[
            SimpleNamespace(name="k-a1", api_key="a1", fingerprint="fp-a1"),
            SimpleNamespace(name="k-a2", api_key="a2", fingerprint="fp-a2"),
            SimpleNamespace(name="k-b1", api_key="b1", fingerprint="fp-b1"),
        ],
        model_name="alias",
        resolved_model=SimpleNamespace(upstream_model="upstream-model"),
        stable_key_hash_value="stable-hash",
    )
    clients = [
        _Client([_resp(412, b'{"error":{"message":"Account acct-a is suspended, possibly due to reaching the monthly spending limit or failure to pay past invoices."}}', "fp-a1")]),
        _Client([_resp(200, b"{}", "fp-b1")]),
    ]
    monkeypatch.setattr(proxy_mod, "FireworksClient", lambda settings, api_key: clients.pop(0))

    response = await proxy_mod.proxy_fireworks_request(context, endpoint="chat_completions", upstream_path="chat/completions", payload={"stream": False}, headers={})

    assert response.status_code == 200
    disabled_names = [call[0][0] for call in repo.key_enabled_calls]
    assert disabled_names == ["k-a1", "k-a2"]
