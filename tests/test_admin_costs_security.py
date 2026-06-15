from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from pytest import MonkeyPatch

from app.control.repository import AppRepository
from app.main import app
from app.platform.config import Settings
from app.platform.runtime_config import ensure_affinity_hash_secret
from app.platform.storage.db import init_db
import app.platform.auth as auth


client = TestClient(app)


@pytest.fixture(autouse=True)
def admin_test_app_state(tmp_path, monkeypatch: MonkeyPatch) -> Iterator[None]:
    previous_settings = getattr(app.state, "settings", None)
    previous_repository = getattr(app.state, "repository", None)

    db_path = tmp_path / "admin-costs-security.sqlite3"
    init_db(db_path)
    repository = AppRepository(db_path)
    settings = Settings(
        database_path=db_path,
        admin_token="token",
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


def _require_auth(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token="token"))


def test_admin_cost_estimate_and_security_posture(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    headers = {"Authorization": "Bearer token"}

    client.post(
        "/admin/keys/bulk",
        headers=headers,
        json={"api_keys": ["fw-cost-a", "fw-cost-b"], "validate_with_fireworks": False},
    )

    repository = app.state.repository
    for payload in (
        {"endpoint": "/v1/chat/completions", "model_alias": "kimi-k2.6", "key_fingerprint": "abc", "input_tokens": 1000, "output_tokens": 250, "cached_tokens": 100, "status_code": 200},
        {"endpoint": "/v1/responses", "model_alias": "kimi-k2.6", "key_fingerprint": "abc", "input_tokens": 500, "output_tokens": 100, "cached_tokens": 0, "status_code": 200},
        {"endpoint": "/v1/chat/completions", "model_alias": "glm-5.1", "key_fingerprint": "def", "input_tokens": 700, "output_tokens": 300, "cached_tokens": 50, "status_code": 500, "error_type": "upstream_5xx"},
    ):
        repository.insert_request_log(payload, retention=100)

    response = client.get("/admin/usage/cost-estimate", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["totals"]["request_count"] >= 3
    assert body["totals"]["input_tokens"] >= 2200
    assert body["totals"]["output_tokens"] >= 650
    assert body["totals"]["cached_tokens"] >= 150
    assert body["rates"]["currency"] == "USD"
    assert {"kimi-k2.6", "glm-5.1"}.issubset({item["model_alias"] for item in body["by_model"] if item["model_alias"]})
    assert {"/v1/chat/completions", "/v1/responses"}.issubset({item["endpoint"] for item in body["by_endpoint"] if item["endpoint"]})

    response = client.get("/admin/security/posture", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["admin_token_configured"] is True
    assert body["proxy_keys_configured"] is True
    assert isinstance(body["admin_static_enabled"], bool)
    assert body["key_count"] >= 2
    assert body["full_prompt_logging_disabled"] is True
    assert body["keys_masked"] is True
