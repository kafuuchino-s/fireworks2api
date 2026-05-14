from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.platform.auth as auth
from app.control.repository import AppRepository
from app.main import app
from app.platform.config import Settings
from app.platform.runtime_config import ensure_affinity_hash_secret
from app.platform.storage.db import init_db


client = TestClient(app)


def _require_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))


@pytest.fixture(autouse=True)
def admin_test_app_state(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    previous_settings = getattr(app.state, "settings", None)
    previous_repository = getattr(app.state, "repository", None)

    db_path = tmp_path / "admin-transform-debug.sqlite3"
    init_db(db_path)
    repository = AppRepository(db_path)
    settings = Settings(database_path=db_path, admin_token="token", proxy_api_keys=["token"])
    ensure_affinity_hash_secret(settings, repository)

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


def test_transform_debug_auth_required() -> None:
    response = client.get("/admin/transform-debug")
    assert response.status_code == 401


def test_transform_debug_list_returns_route_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    repository = app.state.repository
    repository.record_transform_debug(
        {
            "endpoint": "/v1/chat/completions",
            "route_trace": [{"step": "picked_key", "key_name": "fw-1"}],
            "payload_fields_json": ["model"],
        },
        retention=50,
    )

    response = client.get("/admin/transform-debug", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["route_trace"] == [{"step": "picked_key", "key_name": "fw-1"}]


def test_transform_debug_clear_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    repository = app.state.repository
    repository.record_transform_debug({"endpoint": "/v1/chat/completions"}, retention=50)

    response = client.delete("/admin/transform-debug", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    assert response.json()["deleted"] == 1
    assert repository.list_transform_debug_logs() == []


def test_transform_debug_response_excludes_sensitive_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    repository = app.state.repository
    repository.record_transform_debug(
        {
            "endpoint": "/v1/chat/completions",
            "route_trace": [],
            "request_preview_json": {"prompt": "secret"},
        },
        retention=50,
    )

    response = client.get("/admin/transform-debug", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert "prompt" not in item
    assert "key" not in item
    assert "image" not in item
    assert "tool" not in item


def test_transform_debug_has_route_trace_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    repository = app.state.repository
    repository.record_transform_debug({"endpoint": "/v1/chat/completions", "route_trace": [{"step": "a"}]}, retention=50)
    repository.record_transform_debug({"endpoint": "/v1/chat/completions"}, retention=50)

    response = client.get("/admin/transform-debug?has_route_trace=true", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    assert response.json()["count"] == 1
