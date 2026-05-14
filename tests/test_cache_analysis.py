from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.control.repository import AppRepository, ModelMapping
from app.platform.storage.db import init_db
from app.main import app
from app.products.openai.context import build_proxy_context
import app.platform.auth as auth


client = TestClient(app)


def _require_auth(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))


def _repo(tmp_path: Path) -> AppRepository:
    db_path = tmp_path / "db.sqlite3"
    init_db(db_path)
    return AppRepository(db_path)


def test_cache_analysis_endpoint_aggregates_logs(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    _require_auth(monkeypatch)
    repository = _repo(tmp_path)

    repository.upsert_key("k1", "sk-1")
    repository.upsert_key("k2", "sk-2")
    repository.upsert_model(ModelMapping("alias-a", "up-a"))
    repository.upsert_model(ModelMapping("alias-b", "up-b"))

    repository.insert_request_log(
        {
            "endpoint": "/v1/chat/completions",
            "model_alias": "alias-a",
            "upstream_model": "up-a",
            "key_fingerprint": repository.get_key("k1").fingerprint,
            "stable_key_hash": "stable-1",
            "input_tokens": 10,
            "output_tokens": 20,
            "cached_tokens": 5,
            "latency_ms": 100,
            "status_code": 200,
        },
        retention=100,
    )
    repository.insert_request_log(
        {
            "endpoint": "/v1/chat/completions",
            "model_alias": "alias-a",
            "upstream_model": "up-a",
            "key_fingerprint": repository.get_key("k2").fingerprint,
            "stable_key_hash": "stable-1",
            "input_tokens": 30,
            "output_tokens": 0,
            "cached_tokens": 15,
            "latency_ms": 300,
            "status_code": 500,
            "error_type": "upstream_server_error",
        },
        retention=100,
    )
    repository.insert_request_log(
        {
            "endpoint": "/v1/chat/completions",
            "model_alias": "alias-b",
            "upstream_model": "up-b",
            "key_fingerprint": repository.get_key("k1").fingerprint,
            "stable_key_hash": "stable-2",
            "input_tokens": 0,
            "output_tokens": 5,
            "cached_tokens": 0,
            "latency_ms": 50,
            "status_code": 200,
        },
        retention=100,
    )

    app.state.repository = repository
    response = client.get("/admin/cache/analysis", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    body = response.json()

    assert body["summary"]["request_count"] == 3
    assert body["summary"]["prompt_tokens"] == 40
    assert body["summary"]["cached_tokens"] == 20
    assert body["summary"]["token_cache_hit_rate"] == 0.5
    assert body["summary"]["request_cache_hit_rate"] == 2 / 3
    assert set(body) == {"summary", "by_model_list", "by_key_list", "sticky"}

    by_model = {item["model_alias"]: item for item in body["by_model_list"]}
    assert by_model["alias-a"]["upstream_model"] == "up-a"
    assert by_model["alias-a"]["request_count"] == 2
    assert by_model["alias-a"]["cache_hit_request_count"] == 2
    assert by_model["alias-a"]["request_cache_hit_rate"] == 1

    by_key = {item["key_fingerprint"]: item for item in body["by_key_list"]}
    k1 = by_key[repository.get_key("k1").fingerprint]
    assert k1["status"] == "active"
    assert k1["request_count"] == 2
    assert k1["key_name"] == "k1"
    assert k1["key_label"] == "k1"
    assert k1["masked_key"]

    sticky = {(item["stable_key_hash"], item["model_alias"]): item for item in body["sticky"]}
    assert sticky[("stable-1", "alias-a")]["key_count"] == 2
    assert sticky[("stable-1", "alias-a")]["status"] == "dispersed"
    assert "primary_key_share" not in sticky[("stable-1", "alias-a")]
    assert "primary_key_fingerprint" not in sticky[("stable-1", "alias-a")]
    assert "primary_key_share" not in body["sticky"][0]
    assert sticky[("stable-2", "alias-b")]["status"] == "stable"
    assert "by_model" not in body
    assert "by_key" not in body
    assert "by_stable_key" not in body
    assert "anomalies" not in body


def test_sticky_routing_uses_same_healthy_key_and_skips_cooldown(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    repository.upsert_key("k1", "sk-1")
    repository.upsert_key("k2", "sk-2")
    repository.upsert_model(ModelMapping("alias-a", "up-a"))

    keys = repository.list_keys(include_disabled=False)
    k1 = next(key for key in keys if key.name == "k1")
    k2 = next(key for key in keys if key.name == "k2")
    route_key = "alias-a:stable-1"

    from app.dataplane.routing.sticky_router import candidate_keys, order_keys

    first_order = order_keys([k1, k2], route_key)
    second_order = order_keys([k1, k2], route_key)
    assert first_order[0].fingerprint == second_order[0].fingerprint

    repository.set_key_cooldown(first_order[0].name, "2099-01-01T00:00:00+00:00", "rate_limit_or_capacity")
    refreshed = repository.list_keys(include_disabled=False)
    healthy_candidates = candidate_keys(refreshed, route_key, max_attempts=10)
    assert healthy_candidates[0].name != first_order[0].name


class _FakeRequest:
    def __init__(self, *, repository: AppRepository, body: dict, host: str = "127.0.0.1") -> None:
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    allow_unknown_model_passthrough=False,
                    max_upstream_attempts=2,
                    affinity_hash_secret="affinity-secret",
                    log_hash_secret="log-secret",
                ),
                repository=repository,
            )
        )
        self.headers = {}
        self.client = SimpleNamespace(host=host, port=12345)
        self._body = body

    async def json(self):
        return self._body


async def test_proxy_route_key_uses_upstream_model_for_alias_consistency(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    repository.upsert_key("k1", "sk-1")
    repository.upsert_key("k2", "sk-2")
    repository.upsert_model(ModelMapping("alias-a", "accounts/fireworks/models/shared"))
    repository.upsert_model(ModelMapping("alias-b", "accounts/fireworks/models/shared"))

    context_a = await build_proxy_context(_FakeRequest(repository=repository, body={"model": "alias-a", "messages": [], "user": "session-1"}, host="10.0.0.1"))
    context_b = await build_proxy_context(_FakeRequest(repository=repository, body={"model": "alias-b", "messages": [], "user": "session-1"}, host="10.0.0.2"))

    assert context_a.route_key == context_b.route_key
    assert context_a.route_key == "accounts/fireworks/models/shared:session-1"


async def test_proxy_route_key_only_uses_client_identity_for_fallback(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    repository.upsert_key("k1", "sk-1")
    repository.upsert_key("k2", "sk-2")
    repository.upsert_model(ModelMapping("alias-a", "accounts/fireworks/models/shared"))

    explicit_a = await build_proxy_context(_FakeRequest(repository=repository, body={"model": "alias-a", "messages": [], "prompt_cache_key": "cache-1"}, host="10.0.0.1"))
    explicit_b = await build_proxy_context(_FakeRequest(repository=repository, body={"model": "ALIAS-A", "messages": [], "prompt_cache_key": "cache-1"}, host="10.0.0.2"))
    fallback_a = await build_proxy_context(_FakeRequest(repository=repository, body={"model": "alias-a", "messages": []}, host="10.0.0.1"))
    fallback_b = await build_proxy_context(_FakeRequest(repository=repository, body={"model": "alias-a", "messages": []}, host="10.0.0.2"))

    assert explicit_a.route_key == explicit_b.route_key
    assert explicit_a.stable_key_source == "body.prompt_cache_key"
    assert fallback_a.route_key != fallback_b.route_key
    assert fallback_a.stable_key_source == "fallback"
