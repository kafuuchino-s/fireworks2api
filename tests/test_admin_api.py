from __future__ import annotations

from collections.abc import Iterator
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from pytest import MonkeyPatch

from app.main import app
from app.control.repository import AppRepository
from app.platform.config import Settings
from app.platform.runtime_config import ensure_affinity_hash_secret
from app.platform.storage.db import init_db
import app.platform.auth as auth
import app.products.admin.fireworks as fireworks
import app.products.admin.keys as admin_keys


client = TestClient(app)


def _require_auth(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))


@pytest.fixture(autouse=True)
def admin_test_app_state(tmp_path, monkeypatch: MonkeyPatch) -> Iterator[None]:
    previous_settings = getattr(app.state, "settings", None)
    previous_repository = getattr(app.state, "repository", None)

    db_path = tmp_path / "admin-test.sqlite3"
    init_db(db_path)
    repository = AppRepository(db_path)
    settings = Settings(
        database_path=db_path,
        admin_token="token",
        fireworks_api_keys=["token"],
        proxy_api_keys=["token"],
        request_timeout_seconds=120.0,
        allow_unknown_model_passthrough=False,
    )
    ensure_affinity_hash_secret(settings, repository)
    repository.bootstrap_default_models()

    monkeypatch.setattr(app.state, "settings", settings, raising=False)
    monkeypatch.setattr(app.state, "repository", repository, raising=False)

    try:
        yield
    finally:
        if previous_settings is not None:
            app.state.settings = previous_settings
        elif hasattr(app.state, "settings"):
            delattr(app.state, "settings")

        if previous_repository is not None:
            app.state.repository = previous_repository
        elif hasattr(app.state, "repository"):
            delattr(app.state, "repository")


def test_admin_base_paths(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    for path in ("/admin/overview", "/admin/keys", "/admin/models", "/admin/requests", "/admin/fireworks/accounts", "/admin/fireworks/routers"):
        response = client.get(path, headers=headers)
        assert response.status_code == 200


def test_admin_bulk_create_keys(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    response = client.post(
        "/admin/keys/bulk",
        headers=headers,
        json={"api_keys": ["fw_test_bulk_key_1", "fw_test_bulk_key_2", "fw_test_bulk_key_1"]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["created"] == 2
    assert body["duplicates"] == 1
    created_names = [item["key"]["name"] for item in body["items"] if item["status"] == "created"]
    created_keys = [item["key"] for item in body["items"] if item["status"] == "created"]
    assert len(created_names) == 2
    assert all(name.startswith("fw-") for name in created_names)
    assert all("masked_key" in item for item in created_keys)
    assert all("****" in item["masked_key"] for item in created_keys)
    assert all(item["created_at"] for item in created_keys)
    assert all(item["updated_at"] for item in created_keys)

    response = client.get("/admin/keys", headers=headers)
    assert response.status_code == 200
    names = {item["name"] for item in response.json()["items"]}
    assert set(created_names).issubset(names)

    for name in created_names:
        delete_response = client.delete(f"/admin/keys/{name}", headers=headers)
        assert delete_response.status_code == 200


def test_admin_create_key_defaults_to_validation_and_enrichment(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.headers = {}
            self.text = str(payload)

        def json(self):
            return self._payload

    class FakeMgmtClient:
        def __init__(self, settings, api_key):
            self.api_key = api_key

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get_json(self, path, params=None):
            assert path == "/v1/accounts"
            return FakeResponse(200, {"data": [{"id": "accounts/e990e03e", "label": "Primary", "state": "active", "suspend_state": "none"}]})

    monkeypatch.setattr(admin_keys, "FireworksManagementClient", FakeMgmtClient)

    response = client.post("/admin/keys", headers=headers, json={"api_key": "fw_create_secret_validate"})
    assert response.status_code == 201
    body = response.json()
    assert body["enabled"] is True
    assert body["validation"]["valid"] is True
    assert body["validation"]["status_code"] == 200
    assert body["enrichment"]["account_id"] == "e990e03e"
    assert body["enrichment"]["account_label"] == "Primary"
    assert "fw_create_secret_validate" not in response.text
    client.delete(f"/admin/keys/{body['name']}", headers=headers)


def test_admin_cleanup_invalid_keys(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    class FakeResponse:
        def __init__(self, status_code: int):
            self.status_code = status_code
            self.headers = {}
            self.text = "x"
        def json(self):
            return {"data": []}

    class FakeClient:
        def __init__(self, settings, api_key):
            self.api_key = api_key
        async def __aenter__(self): return self
        async def __aexit__(self, exc_type, exc, tb): return None
        async def get_json(self, path, params=None):
            if self.api_key == "fw_401_valid_shape":
                return FakeResponse(401)
            if self.api_key == "fw_403_valid_shape":
                return FakeResponse(403)
            if self.api_key == "fw_500_valid_shape":
                return FakeResponse(500)
            if self.api_key == "fw_200_valid_shape":
                return FakeResponse(200)
            raise TimeoutError("timeout")

    monkeypatch.setattr(admin_keys, "FireworksManagementClient", FakeClient)
    monkeypatch.setattr(admin_keys, "FireworksClient", FakeClient)

    created = []
    for key in ["fw_401_valid_shape", "fw_403_valid_shape", "fw_500_valid_shape", "fw_200_valid_shape", "fw_timeout_valid_shape"]:
        resp = client.post("/admin/keys", headers=headers, json={"api_key": key})
        assert resp.status_code == 201
        created.append(resp.json()["name"])
    repo = app.state.repository
    repo.upsert_key(name="historical-bad", api_key="bad", enabled=False)
    created.append("historical-bad")

    response = client.post("/admin/keys/cleanup-invalid", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["checked"] == 6
    assert body["deleted"] == 3
    assert body["kept"] == 3
    statuses = {item["name"]: item for item in body["items"]}
    assert statuses[created[0]]["status"] == "deleted"
    assert statuses[created[1]]["status"] == "deleted"
    assert statuses[created[5]]["status"] == "deleted"
    assert statuses[created[5]]["reason"] == "malformed_key"
    assert statuses[created[2]]["status"] == "kept"
    assert statuses[created[3]]["status"] == "kept"
    assert statuses[created[4]]["status"] == "kept"
    assert "fw_401_valid_shape" not in response.text
    for name in [created[0], created[1], created[5]]:
        assert repo.get_key(name) is None
    for name in created[2:5]:
        assert repo.get_key(name) is not None
    for name in created[2:5]:
        client.delete(f"/admin/keys/{name}", headers=headers)


def test_admin_cleanup_invalid_keys_removes_quota_exhausted(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    class FakeResponse:
        def __init__(self, status_code: int):
            self.status_code = status_code
            self.headers = {}
            self.text = "x"
        def json(self):
            return {"data": []}

    class FakeClient:
        def __init__(self, settings, api_key):
            self.api_key = api_key
        async def __aenter__(self): return self
        async def __aexit__(self, exc_type, exc, tb): return None
        async def get_json(self, path, params=None):
            # 额度用尽的 key 在账号探测时仍返回 200（账号仍存在、认证仍有效），
            # 验证旧逻辑不会误删、新逻辑会按本地快照删除。
            return FakeResponse(200)

    monkeypatch.setattr(admin_keys, "FireworksManagementClient", FakeClient)
    monkeypatch.setattr(admin_keys, "FireworksClient", FakeClient)

    repo = app.state.repository

    # 1) 正常 key —— 应保留。
    ok = client.post("/admin/keys", headers=headers, json={"api_key": "fw_ok_valid_shape"})
    assert ok.status_code == 201
    ok_name = ok.json()["name"]

    # 2) 额度已用尽账号下的 key —— 应删除。
    exhausted = client.post("/admin/keys", headers=headers, json={"api_key": "fw_exhausted_valid_shape"})
    assert exhausted.status_code == 201
    exhausted_name = exhausted.json()["name"]
    exhausted_fp = repo.get_key(exhausted_name).fingerprint
    repo.upsert_fireworks_key_snapshot({
        "key_fingerprint": exhausted_fp,
        "account_id": "accounts/test-exhausted-acct",
        "account_label": "exhausted",
        "quota_supported": True,
        "quota_status": "quota_exhausted",
        "quota_status_code": 402,
        "quota_summary_json": json.dumps({"count": 1, "monthly_budget": 100, "monthly_used": 100}),
        "quota_items_json": json.dumps([]),
    })
    # 标记为额度用尽被禁用（与 failover 真实路径一致）。
    repo.set_key_enabled(exhausted_name, False, "upstream_account_unavailable", "quota_exhausted")
    # 该账号设一个冷却记录，验证孤儿清理。
    repo.set_account_cooldown("test-exhausted-acct", "2099-01-01T00:00:00+00:00", "quota_exhausted")

    # 3) 管理员手动禁用的 key（非额度用尽）—— 应保留。
    admin_disabled = client.post("/admin/keys", headers=headers, json={"api_key": "fw_admin_disabled_valid_shape"})
    assert admin_disabled.status_code == 201
    admin_disabled_name = admin_disabled.json()["name"]
    repo.set_key_enabled(admin_disabled_name, False, "admin_disabled", None)

    response = client.post("/admin/keys/cleanup-invalid", headers=headers)
    assert response.status_code == 200
    body = response.json()
    statuses = {item["name"]: item for item in body["items"]}

    # 额度用尽 key 被删除，reason 为 quota_exhausted。
    assert statuses[exhausted_name]["status"] == "deleted"
    assert statuses[exhausted_name]["reason"] == "quota_exhausted"
    assert statuses[exhausted_name].get("account_id") == "test-exhausted-acct"
    assert repo.get_key(exhausted_name) is None

    # 正常 key 与管理员手动禁用的 key 都保留。
    assert statuses[ok_name]["status"] == "kept"
    assert repo.get_key(ok_name) is not None
    assert statuses[admin_disabled_name]["status"] == "kept"
    assert repo.get_key(admin_disabled_name) is not None
    assert repo.get_key(admin_disabled_name).enabled is False

    # 孤儿清理：该账号下已无 key，额度快照与冷却记录应被删除。
    assert repo.get_fireworks_account_quota_snapshot("test-exhausted-acct") is None
    assert repo.get_account_cooldown("test-exhausted-acct") is None

    # 计数对账：3 个 key 检查，1 个删除，2 个保留。
    assert body["checked"] == 3
    assert body["deleted"] == 1
    assert body["kept"] == 2
    assert "fw_exhausted_valid_shape" not in response.text

    # 清理剩余测试 key。
    for name in (ok_name, admin_disabled_name):
        client.delete(f"/admin/keys/{name}", headers=headers)


def test_bootstrap_from_env_does_not_resync_when_disabled(tmp_path) -> None:
    db_path = tmp_path / "bootstrap.sqlite3"
    init_db(db_path)
    repository = AppRepository(db_path)
    repository.upsert_key(name="existing", api_key="fw_existing_valid_shape", enabled=True)

    settings = Settings(
        database_path=db_path,
        fireworks_api_keys=["fw_env_valid_shape"],
        sync_env_keys_on_startup=False,
    )

    repository.bootstrap_from_env(settings)

    names = {record.name for record in repository.list_keys(include_disabled=True)}
    assert names == {"existing"}


def test_admin_bulk_create_defaults_to_validation_and_disable_on_error(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.headers = {}
            self.text = str(payload)

        def json(self):
            return self._payload

    class FakeMgmtClient:
        def __init__(self, settings, api_key):
            self.api_key = api_key

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get_json(self, path, params=None):
            if self.api_key == "fw_good_validate_key":
                return FakeResponse(200, {"data": [{"id": "accounts/e990e03e", "label": "Primary"}]})
            return FakeResponse(500, {"error": "boom"})

    monkeypatch.setattr(fireworks, "FireworksManagementClient", FakeMgmtClient)
    monkeypatch.setattr(admin_keys, "FireworksManagementClient", FakeMgmtClient)

    response = client.post(
        "/admin/keys/bulk",
        headers=headers,
        json={"api_keys": ["fw_good_validate_key", "fw_bad_validate_key"]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["created"] == 2
    created = {item["key"]["name"]: item for item in body["items"] if item["status"] == "created"}
    assert created
    good = next(item for item in body["items"] if item.get("validation", {}).get("valid") is True)
    bad = next(item for item in body["items"] if item.get("validation", {}).get("valid") is False)
    assert good["validation"]["valid"] is True
    assert good["enrichment"]["account_id"] == "e990e03e"
    assert bad["validation"]["valid"] is False
    assert bad["key"]["enabled"] is False
    for item in body["items"]:
        if item.get("status") == "created":
            client.delete(f"/admin/keys/{item['key']['name']}", headers=headers)


def test_admin_fireworks_models_and_import(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self):
            return {
                "models": [
                    {"name": "accounts/fireworks/models/test-model"},
                    {"name": "accounts/fireworks/models/kimi-k2p5"},
                ]
            }

    class FakeClient:
        def __init__(self, settings, api_key):
            self.settings = settings
            self.api_key = api_key

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get_json(self, path, params=None):
            return FakeResponse()

    monkeypatch.setattr(fireworks, "FireworksManagementClient", FakeClient)
    monkeypatch.setattr(fireworks, "FireworksClient", FakeClient)

    response = client.get("/admin/fireworks/models", headers=headers)
    assert response.status_code == 200
    assert response.json()["supported"] is True
    assert response.json()["source_type"] == "official_registry"
    assert response.json()["count"] == len(response.json()["items"])

    response = client.get("/admin/fireworks/models", headers=headers, params={"source": "inference"})
    assert response.status_code == 200
    assert response.json()["source_type"] == "inference"

    response = client.get("/admin/fireworks/models", headers=headers, params={"source": "account", "account_id": "acct_123"})
    assert response.status_code == 200
    assert response.json()["source_type"] == "account"

    response = client.get("/admin/fireworks/quotas", headers=headers, params={"account_id": "acct_123"})
    assert response.status_code == 200
    assert response.json()["supported"] is True

    response = client.get("/admin/fireworks/accounts", headers=headers)
    assert response.status_code == 200
    assert response.json()["supported"] is True

    response = client.get("/admin/fireworks/quotas", headers=headers)
    assert response.status_code == 400

    response = client.get("/admin/fireworks/routers", headers=headers)
    assert response.status_code == 200
    assert response.json()["supported"] is False

    response = client.post(
        "/admin/models/import",
        headers=headers,
        json={"aliases": ["test-model"], "upstream_model": "accounts/fireworks/models/test-model"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["created"] == 1
    assert body["items"][0]["model"]["alias"] == "test-model"
    assert "upstream_endpoint" not in body["items"][0]["model"]
    client.delete("/admin/models/test-model", headers=headers)

    response = client.post(
        "/admin/models/import",
        headers=headers,
        json={"upstream_model": "accounts/fireworks/models/test-case", "aliases": ["tmp-case-model", "TMP-CASE-MODEL"]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["created"] == 1
    assert body["updated"] == 0
    aliases = {item["model"]["alias"] for item in body["items"]}
    assert aliases == {"tmp-case-model"}
    client.delete("/admin/models/tmp-case-model", headers=headers)

    response = client.post(
        "/admin/models",
        headers=headers,
        json={"alias": "CaseFold-Test", "upstream_model": "accounts/fireworks/models/casefold-test"},
    )
    assert response.status_code == 201
    response = client.post(
        "/admin/models",
        headers=headers,
        json={"alias": "casefold-test", "upstream_model": "accounts/fireworks/models/casefold-test"},
    )
    assert response.status_code == 409
    client.delete("/admin/models/CaseFold-Test", headers=headers)

    response = client.post(
        "/admin/models",
        headers=headers,
        json={"alias": "tmp-official-case", "upstream_model": "accounts/fireworks/models/tmp-official"},
    )
    assert response.status_code == 201
    response = client.post(
        "/admin/models/import",
        headers=headers,
        json={"upstream_model": "accounts/fireworks/models/tmp-official", "aliases": ["TMP-OFFICIAL-CASE"]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["updated"] == 1
    aliases = {item["alias"] for item in client.get("/admin/models", headers=headers).json()["items"]}
    assert "TMP-OFFICIAL-CASE" in aliases
    assert "tmp-official-case" not in aliases
    client.delete("/admin/models/TMP-OFFICIAL-CASE", headers=headers)


def test_admin_fireworks_missing_account_id(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}
    response = client.get("/admin/fireworks/quotas", headers=headers)
    assert response.status_code == 400
    response = client.get("/admin/fireworks/models", headers=headers)
    assert response.status_code == 200
    assert response.json()["source_type"] == "official_registry"


def test_admin_models_include_optional_fireworks_metadata(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    monkeypatch.setattr(
        app.state,
        "fireworks_model_catalog",
        {
            "items": [
                {
                    "id": "accounts/fireworks/models/kimi-k2p6",
                    "kind": "text",
                    "supported_functionality": {"serverless": True, "function_calling": True},
                    "pricing": None,
                }
            ]
        },
        raising=False,
    )

    try:
        response = client.get("/admin/models", headers=headers)
        assert response.status_code == 200
        items = response.json()["items"]
        kimi = next(item for item in items if item["alias"] == "kimi-k2.6")
        assert kimi["upstream_model"] == "accounts/fireworks/models/kimi-k2p6"
        assert kimi["enabled"] is True
        assert kimi["kind"] == "text"
        assert kimi["supported_functionality"]["serverless"] is True
        assert kimi["supported_functionality"]["function_calling"] is True
        assert kimi["supported_functionality"]["context_length"] == 262144
        assert kimi["supported_functionality"]["image_input"] is True
        assert kimi["pricing"]["standard"]["source"] == "fireworks_serverless_pricing"
    finally:
        pass


def test_admin_models_uses_registry_pricing_before_live_metadata(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    monkeypatch.setattr(
        app.state,
        "fireworks_model_catalog",
        {
            "items": [
                {
                    "id": "accounts/fireworks/models/kimi-k2p6",
                    "pricing": {
                        "inputPrice": "0.25",
                        "output_token_price": 0.75,
                        "billing": {"unit": "token"},
                    },
                },
                {
                    "id": "accounts/fireworks/models/glm-5",
                    "price": None,
                    "costs": [],
                    "rates": "",
                },
            ]
        },
        raising=False,
    )

    try:
        response = client.get("/admin/models", headers=headers)
        assert response.status_code == 200
        items = response.json()["items"]
        kimi = next(item for item in items if item["alias"] == "kimi-k2.6")
        assert kimi["pricing"]["standard"]["input"] == 0.95
    finally:
        pass


def test_admin_models_inherit_fast_variant_metadata(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    monkeypatch.setattr(
        app.state,
        "fireworks_model_catalog",
        {
            "items": [
                {"id": "accounts/fireworks/models/glm-5p1", "kind": "text", "supported_functionality": {"serverless": True, "function_calling": True}},
                {"id": "accounts/fireworks/routers/glm-5p1-fast"},
                {"id": "accounts/fireworks/models/kimi-k2p6", "kind": "text", "supported_functionality": {"serverless": True, "function_calling": False}},
                {"id": "accounts/fireworks/routers/kimi-k2p6-turbo"},
            ]
        },
        raising=False,
    )

    response = client.get("/admin/models", headers=headers)
    assert response.status_code == 200
    items = {item["alias"]: item for item in response.json()["items"]}
    fast = items["glm-5.1-fast"]
    assert fast["kind"] == "text"
    assert fast["supported_functionality"]["serverless"] is True
    assert fast["supported_functionality"]["function_calling"] is True
    assert fast["supported_functionality"]["context_length"] == 202752
    assert set(fast["pricing"].keys()) == {"fast"}
    assert fast["pricing"]["fast"]["input"] == 2.8
    assert fast["pricing"]["fast"]["cached_input"] == 0.52
    assert fast["pricing"]["fast"]["output"] == 8.8
    turbo = items["kimi-k2.6-turbo"]
    assert turbo["kind"] == "text"
    assert turbo["supported_functionality"]["serverless"] is True
    assert turbo["supported_functionality"]["function_calling"] is True
    assert turbo["supported_functionality"]["image_input"] is True
    assert turbo["supported_functionality"]["context_length"] == 262144
    assert set(turbo["pricing"].keys()) == {"fast"}
    assert turbo["pricing"]["fast"]["input"] == 2.0
    assert turbo["pricing"]["fast"]["cached_input"] == 0.3
    assert turbo["pricing"]["fast"]["output"] == 8.0


def test_admin_fireworks_key_quota_summaries(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    class FakeKey:
        def __init__(self, name: str, api_key: str, masked_key: str) -> None:
            self.name = name
            self.api_key = api_key
            self.masked_key = masked_key

    class FakeRepo:
        def list_keys(self, include_disabled: bool = False):
            return [FakeKey("key-1", "fw-secret-full-key", "fw-secr****key")]
        def list_fireworks_key_snapshots(self):
            return []
        def upsert_fireworks_key_snapshot(self, snapshot):
            self.snapshot = snapshot

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.text = str(payload)
            self.headers = {}

        def json(self):
            return self._payload

    captured_paths: list[str] = []

    class FakeClient:
        def __init__(self, settings, api_key):
            self.api_key = api_key

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get_json(self, path, params=None):
            captured_paths.append(path)
            if path == "/v1/accounts":
                return FakeResponse(200, {"data": [{"id": "accounts/e990e03e", "label": "Primary", "state": "active", "suspend_state": "none"}]})
            if path == "/v1/accounts/e990e03e/quotas":
                return FakeResponse(200, {"data": [
                    {"name": "accounts/e990e03e/quotas/monthly-spend-usd", "value": "50", "usage": 5, "updateTime": "2026-05-26T19:51:17Z"},
                    {"name": "accounts/e990e03e/quotas/serverless-inference-rpm", "value": "6000", "usage": 0, "updateTime": "2026-05-26T19:51:18Z"},
                ]})
            raise AssertionError(path)

    monkeypatch.setattr(fireworks, "_repository", lambda request: FakeRepo())
    monkeypatch.setattr(fireworks, "FireworksManagementClient", FakeClient)

    response = client.get("/admin/fireworks/keys/quota-summaries", headers=headers, params={"refresh": "force"})
    assert response.status_code == 200
    body = response.json()
    assert body["supported"] is True
    assert captured_paths == ["/v1/accounts", "/v1/accounts/e990e03e/quotas"]
    item = body["items"][0]
    assert item["key_name"] == "key-1"
    assert item["masked_key"]
    assert "fw-secret-full-key" not in item["masked_key"]
    assert item["account_id"] == "e990e03e"
    assert item["quota_summary"]["count"] == 2
    assert item["quota_summary"]["monthly_budget"] == 50
    assert item["quota_summary"]["monthly_used"] == 5
    assert item["quota_summary"]["monthly_remaining"] == 45.0
    assert item["quota_summary"]["monthly_spend_updated_at"] == "2026-05-26T19:51:17Z"
    assert item["quota_summary"]["serverless_rpm_limit"] == 6000
    assert item["quota_summary"]["serverless_rpm_usage"] == 0
    assert item["quota_summary"]["serverless_rpm_updated_at"] == "2026-05-26T19:51:18Z"
    assert "reset_at" not in item["quota_summary"]
    assert "expires_at" not in item["quota_summary"]
    assert item["quota_items"][0]["name"] == "accounts/e990e03e/quotas/monthly-spend-usd"
    assert "fw-secret-full-key" not in response.text


def test_admin_fireworks_key_quota_summaries_refresh_none_uses_snapshots(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    class FakeKey:
        def __init__(self, name: str, api_key: str, fingerprint: str) -> None:
            self.name = name
            self.api_key = api_key
            self.fingerprint = fingerprint
            self.enabled = True

    class FakeSnapshot:
        def __init__(self, fingerprint: str) -> None:
            self.key_fingerprint = fingerprint
            self.account_id = "e990e03e"
            self.account_label = "Primary"
            self.account_state = "active"
            self.suspend_state = "none"
            self.quota_supported = True
            self.quota_status = "ok"
            self.quota_status_code = 200
            self.quota_summary_json = '{"count": 1}'
            self.quota_items_json = '[{"name":"q"}]'
            self.account_refreshed_at = "2026-05-06T00:00:00+00:00"
            self.quota_refreshed_at = "2026-05-06T00:00:00+00:00"
            self.stale_after = "2026-05-06T01:00:00+00:00"
            self.refresh_status = "ok"
            self.last_refresh_error_type = None
            self.last_refresh_error = None

    class FakeRepo:
        def list_keys(self, include_disabled: bool = True):
            return [FakeKey("key-1", "fw-secret-full-key", "fp-1")]
        def list_fireworks_key_snapshots(self):
            return [FakeSnapshot("fp-1")]

    captured: list[str] = []

    class FakeClient:
        def __init__(self, settings, api_key):
            self.api_key = api_key
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return None
        async def get_json(self, path, params=None):
            captured.append(path)
            raise AssertionError("no live calls expected")

    monkeypatch.setattr(fireworks, "_repository", lambda request: FakeRepo())
    monkeypatch.setattr(fireworks, "_fireworks_context", lambda request: (_ for _ in ()).throw(AssertionError("no management key lookup expected")))
    monkeypatch.setattr(fireworks, "FireworksManagementClient", FakeClient)

    response = client.get("/admin/fireworks/keys/quota-summaries", headers=headers, params={"refresh": "none"})
    assert response.status_code == 200
    assert captured == []
    item = response.json()["items"][0]
    assert item["source"] == "snapshot"
    assert item["stale"] is True
    assert item["quota_summary"]["count"] == 1
    assert "fw-secret-full-key" not in response.text


def test_admin_fireworks_key_quota_pool_summary_deduplicates_accounts(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    class FakeKey:
        def __init__(self, name: str, api_key: str, fingerprint: str, enabled: bool = True) -> None:
            self.name = name
            self.api_key = api_key
            self.fingerprint = fingerprint
            self.enabled = enabled

    class FakeSnapshot:
        def __init__(self, fingerprint: str, account_id: str, budget: int, used: int) -> None:
            self.key_fingerprint = fingerprint
            self.account_id = account_id
            self.account_label = "Primary"
            self.account_state = "active"
            self.suspend_state = "none"
            self.quota_supported = True
            self.quota_status = "ok"
            self.quota_status_code = 200
            self.quota_summary_json = json.dumps(
                {"count": 1, "monthly_budget": budget, "monthly_used": used, "monthly_remaining": budget - used}
            )
            self.quota_items_json = "[]"
            self.account_refreshed_at = "2026-05-06T00:00:00+00:00"
            self.quota_refreshed_at = "2026-05-06T00:00:00+00:00"
            self.stale_after = "2099-01-01T00:00:00+00:00"
            self.refresh_status = "ok"
            self.last_refresh_error_type = None
            self.last_refresh_error = None

    class FakeRepo:
        def list_keys(self, include_disabled: bool = True):
            return [
                FakeKey("key-1", "fw-secret-one", "fp-1", True),
                FakeKey("key-2", "fw-secret-two", "fp-2", True),
                FakeKey("disabled", "fw-secret-disabled", "fp-3", False),
            ]

        def list_fireworks_key_snapshots(self):
            return [
                FakeSnapshot("fp-1", "e990e03e", 50, 5),
                FakeSnapshot("fp-2", "accounts/e990e03e", 50, 5),
                FakeSnapshot("fp-3", "other-account", 999, 1),
            ]

    monkeypatch.setattr(fireworks, "_repository", lambda request: FakeRepo())

    response = client.get("/admin/fireworks/keys/quota-summaries", headers=headers, params={"refresh": "none"})

    assert response.status_code == 200
    summary = response.json()["pool_summary"]
    assert summary["key_count"] == 3
    assert summary["enabled_key_count"] == 2
    assert summary["quota_source_count"] == 1
    assert summary["account_count"] == 1
    assert summary["deduplicated_key_count"] == 1
    assert summary["monthly_budget"] == 50
    assert summary["monthly_used"] == 5
    assert summary["monthly_remaining"] == 45
    assert summary["usage_ratio"] == 0.1
    assert summary["quota_status"] == "ok"
    assert "fw-secret-one" not in response.text
    assert "fw-secret-two" not in response.text


def test_admin_fireworks_key_quota_pool_summary_includes_quota_disabled_accounts(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    class FakeKey:
        def __init__(
            self,
            name: str,
            fingerprint: str,
            *,
            enabled: bool,
            disabled_reason: str | None = None,
            last_error_type: str | None = None,
        ) -> None:
            self.name = name
            self.api_key = f"fw-secret-{name}"
            self.fingerprint = fingerprint
            self.enabled = enabled
            self.disabled_reason = disabled_reason
            self.last_error_type = last_error_type
            self.last_error_at = None

    class FakeSnapshot:
        def __init__(self, fingerprint: str, account_id: str, status: str, budget: int | None, used: int | None) -> None:
            self.key_fingerprint = fingerprint
            self.account_id = account_id
            self.account_label = "Primary"
            self.account_state = "active" if status == "ok" else "suspended"
            self.suspend_state = "none" if status == "ok" else "suspended"
            self.quota_supported = status == "ok"
            self.quota_status = status
            self.quota_status_code = 200 if status == "ok" else 412
            summary = {"count": 1}
            if budget is not None:
                summary["monthly_budget"] = budget
            if used is not None:
                summary["monthly_used"] = used
                if budget is not None:
                    summary["monthly_remaining"] = max(0, budget - used)
            self.quota_summary_json = json.dumps(summary)
            self.quota_items_json = "[]"
            self.account_refreshed_at = "2026-05-06T00:00:00+00:00"
            self.quota_refreshed_at = "2026-05-06T00:00:00+00:00"
            self.stale_after = "2099-01-01T00:00:00+00:00"
            self.refresh_status = "ok" if status == "ok" else "error"
            self.last_refresh_error_type = None if status == "ok" else "quota_exhausted"
            self.last_refresh_error = None

    class FakeRepo:
        def list_keys(self, include_disabled: bool = True):
            return [
                FakeKey("enabled", "fp-enabled", enabled=True),
                FakeKey(
                    "quota-disabled",
                    "fp-quota-disabled",
                    enabled=False,
                    disabled_reason="upstream_account_unavailable",
                    last_error_type="quota_exhausted",
                ),
                FakeKey(
                    "quota-disabled-empty",
                    "fp-quota-disabled-empty",
                    enabled=False,
                    disabled_reason="upstream_account_unavailable",
                    last_error_type="quota_exhausted",
                ),
                FakeKey("admin-disabled", "fp-admin-disabled", enabled=False, disabled_reason="admin_disabled"),
                FakeKey("auth-disabled", "fp-auth-disabled", enabled=False, disabled_reason="upstream_auth_failed", last_error_type="auth_error"),
            ]

        def list_fireworks_key_snapshots(self):
            return [
                FakeSnapshot("fp-enabled", "acct-enabled", "ok", 100, 20),
                FakeSnapshot("fp-quota-disabled", "acct-exhausted", "quota_exhausted", 50, 40),
                FakeSnapshot("fp-quota-disabled-empty", "acct-empty", "quota_exhausted", None, None),
                FakeSnapshot("fp-admin-disabled", "acct-admin", "ok", 999, 1),
                FakeSnapshot("fp-auth-disabled", "acct-auth", "auth_error", 999, 1),
            ]

    monkeypatch.setattr(fireworks, "_repository", lambda request: FakeRepo())

    response = client.get("/admin/fireworks/keys/quota-summaries", headers=headers, params={"refresh": "none"})

    assert response.status_code == 200
    body = response.json()
    summary = body["pool_summary"]
    assert summary["key_count"] == 5
    assert summary["enabled_key_count"] == 1
    assert summary["accounting_key_count"] == 2
    assert summary["quota_disabled_key_count"] == 1
    assert summary["quota_source_count"] == 2
    assert summary["monthly_budget"] == 150
    assert summary["monthly_used"] == 70
    assert summary["monthly_remaining"] == 80
    assert summary["usage_ratio"] == 70 / 150
    assert summary["quota_status"] == "degraded"
    exhausted = next(item for item in body["items"] if item["key_name"] == "quota-disabled")
    assert exhausted["quota_summary"]["monthly_budget"] == 50
    assert exhausted["quota_summary"]["monthly_used"] == 50
    assert exhausted["quota_summary"]["monthly_remaining"] == 0


def test_admin_fireworks_key_quota_summaries_stale_auto_refresh(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    class FakeKey:
        def __init__(self, name: str, api_key: str, fingerprint: str, enabled: bool = True) -> None:
            self.name = name
            self.api_key = api_key
            self.fingerprint = fingerprint
            self.enabled = enabled

    class FakeSnapshot:
        def __init__(self, fingerprint: str) -> None:
            self.key_fingerprint = fingerprint
            self.account_id = "e990e03e"
            self.account_label = "Primary"
            self.account_state = "active"
            self.suspend_state = "none"
            self.quota_supported = True
            self.quota_status = "ok"
            self.quota_status_code = 200
            self.quota_summary_json = '{"count": 1}'
            self.quota_items_json = '[{"name":"old"}]'
            self.account_refreshed_at = "2026-05-06T00:00:00+00:00"
            self.quota_refreshed_at = "2026-05-06T00:00:00+00:00"
            self.stale_after = "2020-01-01T00:00:00+00:00"
            self.refresh_status = "ok"
            self.last_refresh_error_type = None
            self.last_refresh_error = None

    class FakeRepo:
        def __init__(self):
            self.saved = []
        def list_keys(self, include_disabled: bool = True):
            return [FakeKey("key-1", "fw-secret-full-key", "fp-1", True)]
        def list_fireworks_key_snapshots(self):
            return [FakeSnapshot("fp-1")]
        def upsert_fireworks_key_snapshot(self, snapshot):
            self.saved.append(snapshot)

    captured: list[str] = []
    repo = FakeRepo()

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.text = str(payload)
            self.headers = {}
        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, settings, api_key):
            self.api_key = api_key
        async def __aenter__(self): return self
        async def __aexit__(self, exc_type, exc, tb): return None
        async def get_json(self, path, params=None):
            captured.append(path)
            if path == "/v1/accounts":
                return FakeResponse(200, {"data": [{"id": "accounts/e990e03e", "label": "Primary"}]})
            if path == "/v1/accounts/e990e03e/quotas":
                return FakeResponse(200, {"data": [{"name": "accounts/e990e03e/quotas/monthly-spend-usd", "value": "50", "usage": 5}]})
            raise AssertionError(path)

    monkeypatch.setattr(fireworks, "_repository", lambda request: repo)
    monkeypatch.setattr(fireworks, "FireworksManagementClient", FakeClient)

    response = client.get("/admin/fireworks/keys/quota-summaries", headers=headers)
    assert response.status_code == 200
    assert captured == ["/v1/accounts", "/v1/accounts/e990e03e/quotas"]
    assert repo.saved
    item = response.json()["items"][0]
    assert item["stale"] is False
    assert item["refresh_status"] == "ok"


def test_admin_fireworks_key_quota_summaries_partial_snapshot_auto_refreshes(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    class FakeKey:
        name = "key-1"
        api_key = "fw-secret-full-key"
        fingerprint = "fp-1"
        enabled = True

    class FakeSnapshot:
        key_fingerprint = "fp-1"
        account_id = "e990e03e"
        account_label = "Primary"
        account_state = "active"
        suspend_state = "none"
        quota_supported = None
        quota_status = "unavailable"
        quota_status_code = None
        quota_summary_json = '{"count": 0}'
        quota_items_json = "[]"
        account_refreshed_at = "2026-05-06T00:00:00+00:00"
        quota_refreshed_at = None
        stale_after = "2026-05-06T01:00:00+00:00"
        refresh_status = "partial"
        last_refresh_error_type = None
        last_refresh_error = None

    class FakeRepo:
        def __init__(self):
            self.saved = []
            self.enabled_calls = []
        def list_keys(self, include_disabled: bool = True):
            return [FakeKey()]
        def list_fireworks_key_snapshots(self):
            return [FakeSnapshot()]
        def get_fireworks_key_snapshot(self, fingerprint: str):
            return FakeSnapshot() if fingerprint == "fp-1" else None
        def upsert_fireworks_key_snapshot(self, snapshot):
            self.saved.append(snapshot)
        def set_key_enabled(self, *args):
            self.enabled_calls.append(args)

    class FakeResponse:
        status_code = 200
        text = "{}"
        headers = {}
        def __init__(self, payload: dict):
            self._payload = payload
        def json(self):
            return self._payload

    captured: list[str] = []
    repo = FakeRepo()

    class FakeClient:
        def __init__(self, settings, api_key):
            self.api_key = api_key
        async def __aenter__(self): return self
        async def __aexit__(self, exc_type, exc, tb): return None
        async def get_json(self, path, params=None):
            captured.append(path)
            if path == "/v1/accounts":
                return FakeResponse({"data": [{"id": "accounts/e990e03e", "label": "Primary"}]})
            if path == "/v1/accounts/e990e03e/quotas":
                return FakeResponse({"data": [{"name": "accounts/e990e03e/quotas/monthly-spend-usd", "value": "50", "usage": 5}]})
            raise AssertionError(path)

    monkeypatch.setattr(fireworks, "_repository", lambda request: repo)
    monkeypatch.setattr(fireworks, "FireworksManagementClient", FakeClient)

    response = client.get("/admin/fireworks/keys/quota-summaries", headers=headers)
    assert response.status_code == 200
    assert captured == ["/v1/accounts", "/v1/accounts/e990e03e/quotas"]
    assert repo.saved
    item = response.json()["items"][0]
    assert item["quota_status"] == "ok"
    assert item["refresh_status"] == "ok"
    assert item["last_refresh_error_type"] is None
    assert item["quota_summary"]["monthly_budget"] == 50


def test_admin_fireworks_key_quota_summaries_force_refreshes_disabled_key(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    class FakeKey:
        name = "disabled-key"
        api_key = "fw-secret-disabled-key"
        fingerprint = "fp-disabled"
        enabled = False

    class FakeRepo:
        def __init__(self):
            self.saved = []

        def list_keys(self, include_disabled: bool = True):
            return [FakeKey()]

        def list_fireworks_key_snapshots(self):
            return []

        def upsert_fireworks_key_snapshot(self, snapshot):
            self.saved.append(snapshot)

    class FakeResponse:
        status_code = 200
        text = "{}"
        headers = {}

        def __init__(self, payload: dict):
            self._payload = payload

        def json(self):
            return self._payload

    captured: list[str] = []
    repo = FakeRepo()

    class FakeClient:
        def __init__(self, settings, api_key):
            self.api_key = api_key

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get_json(self, path, params=None):
            captured.append(path)
            if path == "/v1/accounts":
                return FakeResponse({"data": [{"id": "accounts/e990e03e", "label": "Primary"}]})
            if path == "/v1/accounts/e990e03e/quotas":
                return FakeResponse({"data": [{"name": "accounts/e990e03e/quotas/monthly-spend-usd", "value": "50", "usage": 5}]})
            raise AssertionError(path)

    monkeypatch.setattr(fireworks, "_repository", lambda request: repo)
    monkeypatch.setattr(fireworks, "_fireworks_context", lambda request: (_ for _ in ()).throw(AssertionError("force should use the disabled stored key directly")))
    monkeypatch.setattr(fireworks, "FireworksManagementClient", FakeClient)

    response = client.get("/admin/fireworks/keys/quota-summaries", headers=headers, params={"refresh": "force"})

    assert response.status_code == 200
    assert captured == ["/v1/accounts", "/v1/accounts/e990e03e/quotas"]
    assert repo.saved
    item = response.json()["items"][0]
    assert item["enabled"] is False
    assert item["quota_status"] == "ok"
    assert item["quota_summary"]["monthly_budget"] == 50


def test_admin_fireworks_quota_412_marks_snapshot_quota_exhausted(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    class FakeKey:
        name = "key-1"
        api_key = "fw-secret-full-key"
        fingerprint = "fp-1"
        enabled = True

    class FakeSnapshot:
        key_fingerprint = "fp-1"
        account_id = "e990e03e"
        account_label = "Primary"
        account_state = "active"
        suspend_state = "none"
        quota_supported = True
        quota_status = "ok"
        quota_status_code = 200
        quota_summary_json = '{"count": 1, "monthly_budget": 50}'
        quota_items_json = '[{"name":"old"}]'
        account_refreshed_at = "2026-05-06T00:00:00+00:00"
        quota_refreshed_at = "2026-05-06T00:00:00+00:00"
        stale_after = "2020-01-01T00:00:00+00:00"
        refresh_status = "ok"
        last_refresh_error_type = None
        last_refresh_error = None

    class FakeRepo:
        def __init__(self):
            self.saved = []
            self.enabled_calls = []
        def list_keys(self, include_disabled: bool = True):
            return [FakeKey()]
        def list_fireworks_key_snapshots(self):
            return [FakeSnapshot()]
        def get_fireworks_key_snapshot(self, fingerprint: str):
            return FakeSnapshot() if fingerprint == "fp-1" else None
        def upsert_fireworks_key_snapshot(self, snapshot):
            self.saved.append(snapshot)
        def set_key_enabled(self, *args):
            self.enabled_calls.append(args)

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)
            self.headers = {}
        def json(self):
            return self._payload

    repo = FakeRepo()

    class FakeClient:
        def __init__(self, settings, api_key):
            self.api_key = api_key
        async def __aenter__(self): return self
        async def __aexit__(self, exc_type, exc, tb): return None
        async def get_json(self, path, params=None):
            if path == "/v1/accounts":
                return FakeResponse(200, {"data": [{"id": "accounts/e990e03e", "label": "Primary"}]})
            if path == "/v1/accounts/e990e03e/quotas":
                return FakeResponse(412, {"error": {"message": "Account e990e03e is suspended, possibly due to reaching the monthly spending limit or failure to pay past invoices.", "type": "api_error"}})
            raise AssertionError(path)

    monkeypatch.setattr(fireworks, "_repository", lambda request: repo)
    monkeypatch.setattr(fireworks, "FireworksManagementClient", FakeClient)

    response = client.get("/admin/fireworks/keys/quota-summaries", headers=headers, params={"refresh": "force"})

    assert response.status_code == 200
    assert repo.saved
    saved = repo.saved[0]
    assert saved["quota_status"] == "quota_exhausted"
    assert saved["refresh_status"] == "error"
    assert saved["last_refresh_error_type"] == "quota_exhausted"
    saved_summary = json.loads(saved["quota_summary_json"])
    assert saved_summary["monthly_budget"] == 50
    assert saved_summary["monthly_used"] == 50
    assert saved_summary["monthly_remaining"] == 0
    assert saved["quota_items_json"] == '[{"name":"old"}]'
    assert repo.enabled_calls == [("key-1", False, "upstream_account_unavailable", "quota_exhausted")]
    body = response.json()
    item = body["items"][0]
    assert item["quota_status"] == "quota_exhausted"
    assert item["account_state"] == "suspended"
    assert item["suspend_state"] == "suspended"
    assert item["quota_summary"]["monthly_budget"] == 50
    assert item["quota_summary"]["monthly_used"] == 50
    assert item["quota_summary"]["monthly_remaining"] == 0
    assert body["pool_summary"]["monthly_budget"] == 50
    assert body["pool_summary"]["monthly_used"] == 50
    assert body["pool_summary"]["monthly_remaining"] == 0


def test_admin_fireworks_quota_412_respects_auto_disable_config(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}
    monkeypatch.setattr(app.state.settings, "fireworks_auto_disable_exhausted_accounts", False, raising=False)

    class FakeKey:
        name = "key-1"
        api_key = "fw-secret-full-key"
        fingerprint = "fp-1"
        enabled = True

    class FakeSnapshot:
        key_fingerprint = "fp-1"
        account_id = "e990e03e"
        account_label = "Primary"
        account_state = "active"
        suspend_state = "none"
        quota_supported = True
        quota_status = "ok"
        quota_status_code = 200
        quota_summary_json = '{"count": 1, "monthly_budget": 50}'
        quota_items_json = '[{"name":"old"}]'
        account_refreshed_at = "2026-05-06T00:00:00+00:00"
        quota_refreshed_at = "2026-05-06T00:00:00+00:00"
        stale_after = "2020-01-01T00:00:00+00:00"
        refresh_status = "ok"
        last_refresh_error_type = None
        last_refresh_error = None

    class FakeRepo:
        def __init__(self):
            self.saved = []
            self.enabled_calls = []

        def list_keys(self, include_disabled: bool = True):
            return [FakeKey()]

        def list_fireworks_key_snapshots(self):
            return [FakeSnapshot()]

        def get_fireworks_key_snapshot(self, fingerprint: str):
            return FakeSnapshot() if fingerprint == "fp-1" else None

        def upsert_fireworks_key_snapshot(self, snapshot):
            self.saved.append(snapshot)

        def set_key_enabled(self, *args):
            self.enabled_calls.append(args)

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)
            self.headers = {}

        def json(self):
            return self._payload

    repo = FakeRepo()

    class FakeClient:
        def __init__(self, settings, api_key):
            self.api_key = api_key

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get_json(self, path, params=None):
            if path == "/v1/accounts":
                return FakeResponse(200, {"data": [{"id": "accounts/e990e03e", "label": "Primary"}]})
            if path == "/v1/accounts/e990e03e/quotas":
                return FakeResponse(412, {"error": {"message": "Account e990e03e is suspended, possibly due to reaching the monthly spending limit.", "type": "api_error"}})
            raise AssertionError(path)

    monkeypatch.setattr(fireworks, "_repository", lambda request: repo)
    monkeypatch.setattr(fireworks, "FireworksManagementClient", FakeClient)

    response = client.get("/admin/fireworks/keys/quota-summaries", headers=headers, params={"refresh": "force"})

    assert response.status_code == 200
    assert repo.saved
    assert repo.saved[0]["quota_status"] == "quota_exhausted"
    assert repo.enabled_calls == []


def test_fireworks_account_quota_snapshot_merges_into_key_snapshot(tmp_path) -> None:
    db_path = tmp_path / "quota-snapshots.sqlite3"
    init_db(db_path)
    repository = AppRepository(db_path)
    repository.upsert_key("key-1", "fw_merge_snapshot_key_123456", enabled=True)
    key = repository.get_key("key-1")
    assert key is not None

    repository.upsert_fireworks_key_snapshot(
        {
            "key_fingerprint": key.fingerprint,
            "account_id": "accounts/acct-1",
            "account_label": "Primary",
            "account_state": "active",
            "suspend_state": "none",
            "quota_supported": True,
            "quota_status": "ok",
            "quota_status_code": 200,
            "quota_summary_json": '{"count": 1, "monthly_budget": 10}',
            "quota_items_json": '[{"name": "old"}]',
            "account_refreshed_at": "2026-05-26T00:00:00+00:00",
            "quota_refreshed_at": "2026-05-26T00:00:00+00:00",
            "stale_after": "2026-05-26T00:30:00+00:00",
            "refresh_status": "ok",
        }
    )
    account_snapshot = repository.get_fireworks_account_quota_snapshot("accounts/acct-1")
    assert account_snapshot is not None
    assert account_snapshot.account_id == "acct-1"
    assert account_snapshot.quota_status == "ok"

    repository.upsert_fireworks_account_quota_snapshot(
        "acct-1",
        {
            "quota_supported": False,
            "quota_status": "quota_exhausted",
            "quota_status_code": 412,
            "quota_summary_json": '{"count": 1, "monthly_budget": 10, "monthly_used": 10, "monthly_remaining": 0}',
            "quota_items_json": '[{"name": "new"}]',
            "quota_refreshed_at": "2026-05-26T01:00:00+00:00",
            "stale_after": "2026-05-26T01:30:00+00:00",
            "refresh_status": "error",
            "last_refresh_error_type": "quota_exhausted",
            "last_refresh_error": "quota exhausted",
            "consecutive_refresh_failures": 3,
            "next_refresh_after": "2026-05-26T01:30:00+00:00",
        },
    )
    merged = repository.get_fireworks_key_snapshot(key.fingerprint)
    assert merged is not None
    assert merged.account_id == "accounts/acct-1"
    assert merged.quota_status == "quota_exhausted"
    assert merged.quota_status_code == 412
    assert merged.quota_summary_json and "monthly_remaining" in merged.quota_summary_json
    assert merged.consecutive_refresh_failures == 3


def test_admin_key_delete_removes_snapshot(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}
    response = client.post("/admin/keys", headers=headers, json={"api_key": "fw_delete_snapshot"})
    assert response.status_code == 201
    body = response.json()
    repo = app.state.repository
    assert repo.get_fireworks_key_snapshot(body["fingerprint"]) is None or True
    delete = client.delete(f"/admin/keys/{body['name']}", headers=headers)
    assert delete.status_code == 200
    assert repo.get_fireworks_key_snapshot(body["fingerprint"]) is None


def test_admin_fireworks_quota_path_normalization(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}
    monkeypatch.setattr(app.state, "repository", SimpleNamespace(list_keys=lambda include_disabled=False: []), raising=False)

    captured: list[str] = []

    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self):
            return {"data": []}

    class FakeClient:
        def __init__(self, settings, api_key):
            self.api_key = api_key

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get_json(self, path, params=None):
            captured.append(path)
            return FakeResponse()

    monkeypatch.setattr(fireworks, "FireworksManagementClient", FakeClient)

    response = client.get("/admin/fireworks/quotas", headers=headers, params={"account_id": "accounts/e990e03e"})
    assert response.status_code == 200
    assert captured == ["/v1/accounts/e990e03e/quotas"]

    captured.clear()
    response = client.get("/admin/fireworks/accounts/e990e03e", headers=headers)
    assert response.status_code == 200
    assert captured == ["/v1/accounts/e990e03e"]


def test_admin_fireworks_key_quota_summaries_partial_failure(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    class FakeKey:
        def __init__(self, name: str, api_key: str, masked_key: str) -> None:
            self.name = name
            self.api_key = api_key
            self.masked_key = masked_key

    class FakeRepo:
        def list_keys(self, include_disabled: bool = False):
            return [FakeKey("good", "fw-good-secret", "fw-goo****ret"), FakeKey("bad", "fw-bad-secret", "fw-bad****ret")]

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.text = str(payload)
            self.headers = {}

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, settings, api_key):
            self.api_key = api_key

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get_json(self, path, params=None):
            if self.api_key == "fw-bad-secret":
                raise RuntimeError("boom")
            if path == "/v1/accounts":
                return FakeResponse(200, {"data": [{"id": "accounts/e990e03e", "label": "Primary"}]})
            if path == "/v1/accounts/e990e03e/quotas":
                return FakeResponse(200, {"data": [{"name": "accounts/e990e03e/quotas/monthly-spend-usd", "value": "50", "usage": 5}]})
            raise AssertionError(path)

    monkeypatch.setattr(fireworks, "_repository", lambda request: FakeRepo())
    monkeypatch.setattr(fireworks, "FireworksManagementClient", FakeClient)

    response = client.get("/admin/fireworks/keys/quota-summaries", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    good = next(item for item in body["items"] if item["key_name"] == "good")
    bad = next(item for item in body["items"] if item["key_name"] == "bad")
    assert good["quota_status"] == "ok"
    assert good["quota_summary"]["monthly_budget"] == 50
    assert bad["quota_status"] == "unavailable"
    assert bad["error"] == "boom"


def test_admin_billing_import_and_summary(monkeypatch: MonkeyPatch, tmp_path) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    csv_content = (
        "email,start_time,end_time,usage_type,accelerator_type,accelerator_seconds,base_model_name,model_bucket,parameter_count,prompt_tokens,completion_tokens\n"
        "alice@example.com,2026-05-01T00:00:00Z,2026-05-01T01:00:00Z,serverless,A100,12.5,model-a,bucket-a,10,100,25\n"
        "bob@example.com,2026-05-02T00:00:00Z,2026-05-02T02:00:00Z,deploy,H100,8,model-b,bucket-b,20,50,75\n"
    )
    csv_path = tmp_path / "billing.csv"
    csv_path.write_text(csv_content, encoding="utf-8")

    response = client.post(
        "/admin/billing/import-csv",
        headers=headers,
        json={"file_path": str(csv_path)},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["row_count"] == 2
    assert body["import_id"]

    response = client.get("/admin/billing/summary", headers=headers)
    assert response.status_code == 200
    summary = response.json()
    assert summary["totals"]["row_count"] >= 2
    assert summary["totals"]["prompt_tokens"] >= 150
    assert summary["totals"]["completion_tokens"] >= 100
    assert {row["usage_type"] for row in summary["by_usage_type"]} >= {"serverless", "deploy"}
    assert {row["base_model_name"] for row in summary["by_base_model_name"]} >= {"model-a", "model-b"}


def test_admin_config_get_patch_and_token_rotation(monkeypatch: MonkeyPatch, tmp_path) -> None:
    db_path = tmp_path / "admin-config.sqlite3"
    init_db(db_path)
    repository = AppRepository(db_path)
    settings = Settings(
        database_path=db_path,
        admin_token="token-old",
        fireworks_api_keys=[],
        fireworks_api_keys_json=[],
        proxy_api_keys=["proxy-old"],
        request_timeout_seconds=120.0,
        allow_unknown_model_passthrough=False,
    )
    ensure_affinity_hash_secret(settings, repository)
    monkeypatch.setattr(app.state, "settings", settings, raising=False)
    monkeypatch.setattr(app.state, "repository", repository, raising=False)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)

    headers_old = {"Authorization": "Bearer token-old"}
    response = client.get("/admin/config/runtime", headers=headers_old)
    assert response.status_code == 200
    payload = response.json()
    body = payload["config"]
    diagnostics = payload["runtime_diagnostics"]
    assert body["admin_token_configured"] is True
    assert body["admin_token_masked"]
    assert body["proxy_api_keys"] == ["proxy-old"]
    assert body["proxy_api_keys_count"] == 1
    assert body["transform_debug_enabled"] is False
    assert body["transform_debug_retention"] == 50
    assert body["transform_debug_level"] == "summary"
    assert body["fireworks_quota_ttl_seconds"] == 1800
    assert body["fireworks_quota_refresh_concurrency"] == 4
    assert body["fireworks_auto_disable_exhausted_accounts"] is True
    assert body["fireworks_quota_background_refresh_enabled"] is True
    assert body["fireworks_quota_refresh_interval_seconds"] == 900
    assert body["fireworks_quota_refresh_jitter_seconds"] == 120
    assert body["fireworks_quota_refresh_on_startup"] is True
    assert "cors_allow_origins" not in body
    assert "enable_admin_static" not in body
    assert "sync_env_keys_on_startup" not in body
    assert "log_hash_secret" not in body
    assert "affinity_hash_secret" not in body
    assert "affinity_hash_secret_configured" not in body
    assert body["proxy_api_keys"][0] == "proxy-old"
    assert settings.affinity_hash_secret
    assert repository.get_setting("affinity_hash_secret")
    assert diagnostics["database_path"] == str(db_path.resolve())
    assert diagnostics["db_key_count"] == 0
    assert diagnostics["malformed_key_count"] == 0
    assert diagnostics["env_fireworks_api_keys_count"] == 0
    assert diagnostics["sync_env_keys_on_startup"] is False

    response = client.patch(
        "/admin/config/runtime",
        headers=headers_old,
        json={
            "request_timeout_seconds": 33,
            "allow_unknown_model_passthrough": True,
            "transform_debug_enabled": True,
            "transform_debug_retention": 12,
            "transform_debug_level": "summary",
            "fireworks_quota_ttl_seconds": 60,
            "fireworks_quota_refresh_concurrency": 2,
            "fireworks_auto_disable_exhausted_accounts": False,
            "fireworks_quota_background_refresh_enabled": False,
            "fireworks_quota_refresh_interval_seconds": 30,
            "fireworks_quota_refresh_jitter_seconds": 5,
            "fireworks_quota_refresh_on_startup": False,
        },
    )
    assert response.status_code == 200
    assert settings.request_timeout_seconds == 33
    assert settings.allow_unknown_model_passthrough is True
    assert settings.transform_debug_enabled is True
    assert settings.transform_debug_retention == 12
    assert settings.transform_debug_level == "summary"
    assert settings.fireworks_quota_ttl_seconds == 60
    assert settings.fireworks_quota_refresh_concurrency == 2
    assert settings.fireworks_auto_disable_exhausted_accounts is False
    assert settings.fireworks_quota_background_refresh_enabled is False
    assert settings.fireworks_quota_refresh_interval_seconds == 30
    assert settings.fireworks_quota_refresh_jitter_seconds == 5
    assert settings.fireworks_quota_refresh_on_startup is False

    response = client.patch(
        "/admin/config/runtime",
        headers=headers_old,
        json={"cors_allow_origins": ["https://example.com"]},
    )
    assert response.status_code == 422

    response = client.patch(
        "/admin/config/runtime",
        headers=headers_old,
        json={"affinity_hash_secret": "user-should-not-set-this"},
    )
    assert response.status_code == 422

    response = client.patch(
        "/admin/config/runtime",
        headers=headers_old,
        json={"admin_token": "token-new"},
    )
    assert response.status_code == 200
    assert settings.admin_token == "token-new"

    response = client.get("/admin/config/runtime", headers=headers_old)
    assert response.status_code == 401
    response = client.get("/admin/config/runtime", headers={"Authorization": "Bearer token-new"})
    assert response.status_code == 200

    response = client.patch(
        "/admin/config/runtime",
        headers={"Authorization": "Bearer token-new"},
        json={"proxy_api_keys": ["proxy-new-1", "proxy-new-2"]},
    )
    assert response.status_code == 200
    assert settings.proxy_api_keys == ["proxy-new-1", "proxy-new-2"]
    assert response.json()["config"]["proxy_api_keys_count"] == 2

    response = client.post("/admin/config/proxy-keys/generate", headers={"Authorization": "Bearer token-new"})
    assert response.status_code == 201
    generated_key = response.json()["generated_key"]
    assert generated_key.startswith("sk-fw2api-")
    assert generated_key in settings.proxy_api_keys

    response = client.delete(f"/admin/config/proxy-keys/{generated_key}", headers={"Authorization": "Bearer token-new"})
    assert response.status_code == 200
    assert generated_key not in settings.proxy_api_keys
    assert response.json()["config"]["proxy_api_keys_count"] == 2


def test_admin_transform_debug_list_and_clear(monkeypatch: MonkeyPatch, tmp_path) -> None:
    db_path = tmp_path / "admin-transform-debug.sqlite3"
    init_db(db_path)
    repository = AppRepository(db_path)
    settings = Settings(database_path=db_path, admin_token="token", proxy_api_keys=["token"])
    ensure_affinity_hash_secret(settings, repository)
    monkeypatch.setattr(app.state, "settings", settings, raising=False)
    monkeypatch.setattr(app.state, "repository", repository, raising=False)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)

    repository.record_transform_debug(
        {
            "endpoint": "/v1/chat/completions",
            "upstream_endpoint": "/inference/v1/chat/completions",
            "model_alias": "kimi-k2.6",
            "upstream_model": "accounts/fireworks/models/kimi-k2p6",
            "stream": False,
            "service_tier": "priority",
            "stable_key_source": "key_fingerprint",
            "payload_fields_json": ["model", "messages"],
            "forwarded_headers_json": {"x-test": "1"},
            "field_changes_json": [{"field": "model", "action": "mapped"}],
            "blocked_fields_json": [],
            "warnings_json": [],
            "response_status_code": 200,
            "latency_ms": 12,
        },
        retention=50,
    )

    headers = {"Authorization": "Bearer token"}
    response = client.get("/admin/transform-debug", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    item = body["items"][0]
    assert item["endpoint"] == "/v1/chat/completions"
    assert item["request_preview"] is None
    assert item["payload_fields"] == ["model", "messages"]
    assert item["field_changes"] == [{"field": "model", "action": "mapped"}]
    assert "request_preview_json" not in item
    assert "authorization" not in response.text.lower()
    assert "prompt" not in response.text.lower()
    assert "body" not in response.text.lower()

    response = client.delete("/admin/transform-debug", headers=headers)
    assert response.status_code == 200
    assert response.json()["deleted"] == 1
    assert repository.list_transform_debug_logs() == []


def test_transform_debug_retention(monkeypatch: MonkeyPatch, tmp_path) -> None:
    db_path = tmp_path / "admin-transform-debug-retention.sqlite3"
    init_db(db_path)
    repository = AppRepository(db_path)

    for idx in range(3):
        repository.record_transform_debug({"endpoint": f"/e{idx}", "payload_fields_json": []}, retention=2)

    items = repository.list_transform_debug_logs(limit=10)
    assert len(items) == 2
    assert {item["endpoint"] for item in items} == {"/e1", "/e2"}
