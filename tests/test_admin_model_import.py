from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.control.repository import AppRepository
from app.platform.config import Settings
from app.platform.runtime_config import ensure_affinity_hash_secret
from app.platform.storage.db import init_db
import app.platform.auth as auth



def _require_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))


@pytest.fixture(autouse=True)
def _admin_auth(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[None]:
    previous_settings = getattr(app.state, "settings", None)
    previous_repository = getattr(app.state, "repository", None)

    db_path = tmp_path / "admin-model-import-test.sqlite3"
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
    _require_auth(monkeypatch)
    yield

    if previous_settings is not None:
        app.state.settings = previous_settings
    elif hasattr(app.state, "settings"):
        delattr(app.state, "settings")

    if previous_repository is not None:
        app.state.repository = previous_repository
    elif hasattr(app.state, "repository"):
        delattr(app.state, "repository")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer token"}


def test_admin_model_import_explicit_aliases_create(client, auth_headers):
    response = client.post(
        "/admin/models/import",
        headers=auth_headers,
        json={"models": [{"upstream_model": "accounts/fireworks/models/test-model", "alias": "test-model"}]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["created"] == 1
    assert body["updated"] == 0
    assert body["items"][0]["status"] == "created"
    assert body["items"][0]["model"]["alias"] == "test-model"


def test_admin_model_import_duplicate_aliases_dedupe(client, auth_headers):
    response = client.post(
        "/admin/models/import",
        headers=auth_headers,
        json={"models": [{"upstream_model": "accounts/fireworks/models/dup-test", "aliases": ["dup-test", "DUP-TEST"]}]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["created"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "created"


def test_admin_model_import_conflict_to_different_upstream(client, auth_headers):
    client.post(
        "/admin/models",
        headers=auth_headers,
        json={"alias": "conflict-model", "upstream_model": "accounts/fireworks/models/one"},
    )
    response = client.post(
        "/admin/models/import",
        headers=auth_headers,
        json={"models": [{"upstream_model": "accounts/fireworks/models/two", "alias": "conflict-model"}]},
    )
    assert response.status_code == 409


def test_admin_model_import_same_upstream_alias_casing_updates(client, auth_headers):
    client.post(
        "/admin/models",
        headers=auth_headers,
        json={"alias": "CaseFold-Test", "upstream_model": "accounts/fireworks/models/casefold-test"},
    )
    response = client.post(
        "/admin/models/import",
        headers=auth_headers,
        json={"models": [{"upstream_model": "accounts/fireworks/models/casefold-test", "aliases": ["casefold-test"]}]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["updated"] == 1
    assert body["items"][0]["status"] == "updated"
    aliases = {item["alias"] for item in client.get("/admin/models", headers=auth_headers).json()["items"]}
    assert "casefold-test" in aliases
    assert "CaseFold-Test" not in aliases


def test_admin_model_import_rejects_string_item(client, auth_headers):
    response = client.post("/admin/models/import", headers=auth_headers, json={"models": ["accounts/fireworks/models/test-model"]})
    assert response.status_code == 400
    assert "alias or aliases" in response.json()["detail"]


def test_admin_model_import_rejects_object_without_alias(client, auth_headers):
    response = client.post(
        "/admin/models/import",
        headers=auth_headers,
        json={"models": [{"upstream_model": "accounts/fireworks/models/test-model"}]},
    )
    assert response.status_code == 400
    assert "alias or aliases" in response.json()["detail"]


def test_admin_model_import_has_no_basename_fallback(client, auth_headers):
    response = client.post(
        "/admin/models/import",
        headers=auth_headers,
        json={"models": [{"upstream_model": "accounts/fireworks/models/test-model", "aliases": []}]},
    )
    assert response.status_code == 400
    assert "alias or aliases" in response.json()["detail"]
