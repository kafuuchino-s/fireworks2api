from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.main import app
import app.platform.auth as auth


client = TestClient(app)


def _require_auth(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(proxy_api_keys=["token"], admin_token=None))


def test_v1_models_api_shape(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    response = client.get("/v1/models", headers={"Authorization": "Bearer token"})

    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "list"
    assert "data" in data
    assert isinstance(data["data"], list)
    if data["data"]:
        item = data["data"][0]
        assert set(item) == {"id", "object", "created", "owned_by"}


def test_v1_model_api_shape(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    list_response = client.get("/v1/models", headers={"Authorization": "Bearer token"})
    assert list_response.status_code == 200
    models = list_response.json()["data"]
    assert models

    response = client.get(f"/v1/models/{models[0]['id']}", headers={"Authorization": "Bearer token"})

    assert response.status_code == 200
    item = response.json()
    assert set(item) == {"id", "object", "created", "owned_by"}


def test_unversioned_models_alias_is_not_registered(monkeypatch: MonkeyPatch) -> None:
    _require_auth(monkeypatch)
    response = client.get("/models", headers={"Authorization": "Bearer token"})

    assert response.status_code == 404


def test_root_is_not_required_to_be_200() -> None:
    response = client.get("/")

    assert response.status_code in {200, 404, 405}
