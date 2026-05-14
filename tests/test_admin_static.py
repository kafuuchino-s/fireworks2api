from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

import app.platform.config as config
import app.main as main


def test_admin_static_routes(monkeypatch: MonkeyPatch) -> None:
    config.get_settings.cache_clear()
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(
            app_name="fireworks2api",
            data_dir=SimpleNamespace(),
            db_path=SimpleNamespace(),
            upstream_base_url="https://api.fireworks.ai/inference/v1",
            cors_allow_origins=[],
            enable_admin_static=True,
            admin_token="admin-local",
            proxy_api_keys=["sk-local-dev"],
        ),
    )
    monkeypatch.setattr(
        main,
        "bootstrap_app_state",
        lambda settings: SimpleNamespace(
            overview=lambda: {
                "request_count": 0,
                "error_count": 0,
                "key_total": 1,
                "healthy_key_count": 1,
                "cooldown_key_count": 0,
                "disabled_key_count": 0,
            }
        ),
    )

    app = main.create_app()
    client = TestClient(app)

    for path in ("/admin/login", "/admin/account", "/admin/model", "/admin/config", "/admin/cache", "/admin/status"):
        response = client.get(path)
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    response = client.get("/static/css/app.css")
    assert response.status_code == 200

    response = client.get("/favicon.ico")
    assert response.status_code == 200

    response = client.get("/admin/overview")
    assert response.status_code == 401

    response = client.get("/admin/overview", headers={"Authorization": "Bearer admin-local"})
    assert response.status_code == 200
