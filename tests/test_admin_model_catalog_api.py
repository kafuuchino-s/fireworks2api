from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.control.repository import AppRepository
from app.platform.config import Settings
from app.platform.runtime_config import ensure_affinity_hash_secret
from app.platform.storage.db import init_db
import app.platform.auth as auth
import app.products.admin.fireworks as fireworks


client = TestClient(app)


def _require_auth(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))


@pytest.fixture(autouse=True)
def admin_test_app_state(tmp_path, monkeypatch) -> Iterator[None]:
    previous_settings = getattr(app.state, "settings", None)
    previous_repository = getattr(app.state, "repository", None)
    db_path = tmp_path / "admin-model-catalog.sqlite3"
    init_db(db_path)
    repository = AppRepository(db_path)
    settings = Settings(database_path=db_path, admin_token="token", fireworks_api_keys=["token"], proxy_api_keys=["token"])
    ensure_affinity_hash_secret(settings, repository)
    repository.bootstrap_default_models()
    monkeypatch.setattr(app.state, "settings", settings, raising=False)
    monkeypatch.setattr(app.state, "repository", repository, raising=False)
    try:
        yield
    finally:
        if previous_settings is not None:
            app.state.settings = previous_settings
        if previous_repository is not None:
            app.state.repository = previous_repository


def test_admin_fireworks_models_default_official_registry(monkeypatch):
    _require_auth(monkeypatch)
    response = client.get("/admin/fireworks/models", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    body = response.json()
    assert body["supported"] is True
    assert body["source"] == "official_registry"
    assert body["source_type"] == "official_registry"
    assert body["count"] == len(body["items"])
    assert any(item["upstream_model"] == "accounts/fireworks/models/kimi-k2p6" for item in body["items"])


def test_admin_fireworks_models_live_inference_requires_key(monkeypatch):
    _require_auth(monkeypatch)

    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self):
            return {"models": [{"name": "accounts/fireworks/models/test-model"}]}

    class FakeClient:
        def __init__(self, settings, api_key):
            self.api_key = api_key

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get_json(self, path, params=None):
            return FakeResponse()

    monkeypatch.setattr(fireworks, "FireworksClient", FakeClient)
    response = client.get("/admin/fireworks/models", headers={"Authorization": "Bearer token"}, params={"source": "inference"})
    assert response.status_code == 200
    assert response.json()["source_type"] == "inference"


def test_admin_fireworks_models_unknown_source_rejected(monkeypatch):
    _require_auth(monkeypatch)
    response = client.get("/admin/fireworks/models", headers={"Authorization": "Bearer token"}, params={"source": "bogus"})
    assert response.status_code == 400
