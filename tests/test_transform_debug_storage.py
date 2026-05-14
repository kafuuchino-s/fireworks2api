from __future__ import annotations

import sqlite3
from pathlib import Path

from app.control.repository import AppRepository
from app.platform.storage.db import init_db


def _repo(tmp_path: Path) -> AppRepository:
    db_path = tmp_path / "transform-debug.sqlite3"
    init_db(db_path)
    return AppRepository(db_path)


def test_transform_debug_write_read_route_trace(tmp_path: Path) -> None:
    repository = _repo(tmp_path)

    repository.record_transform_debug(
        {
            "endpoint": "/v1/chat/completions",
            "route_trace": {"steps": [{"name": "route", "selected": "k1"}]},
            "payload_fields": ["model", "messages"],
        },
        retention=10,
    )

    logs = repository.list_transform_debug_logs()
    assert len(logs) == 1
    assert logs[0]["route_trace"] == {"steps": [{"name": "route", "selected": "k1"}]}
    assert logs[0]["payload_fields"] == ["model", "messages"]


def test_transform_debug_migrates_existing_table_with_route_trace(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE transform_debug_logs (
              id TEXT PRIMARY KEY,
              timestamp TEXT NOT NULL,
              endpoint TEXT,
              upstream_endpoint TEXT,
              model_alias TEXT,
              upstream_model TEXT,
              stream INTEGER DEFAULT 0,
              service_tier TEXT,
              stable_key_source TEXT,
              payload_fields_json TEXT,
              forwarded_headers_json TEXT,
              field_changes_json TEXT,
              blocked_fields_json TEXT,
              warnings_json TEXT,
              request_preview_json TEXT,
              response_status_code INTEGER,
              error_type TEXT,
              latency_ms INTEGER,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO transform_debug_logs(
              id, timestamp, endpoint, stream, payload_fields_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("legacy-1", "2026-01-01T00:00:00+00:00", "/v1/chat/completions", 1, '["a"]', "2026-01-01T00:00:00+00:00"),
        )

    init_db(db_path)
    repository = AppRepository(db_path)

    logs = repository.list_transform_debug_logs()
    assert len(logs) == 1
    assert logs[0]["id"] == "legacy-1"
    assert logs[0]["route_trace"] is None
    assert logs[0]["payload_fields"] == ["a"]


def test_transform_debug_retention_applies(tmp_path: Path) -> None:
    repository = _repo(tmp_path)

    for idx in range(3):
        repository.record_transform_debug({"id": f"log-{idx}", "timestamp": f"2026-01-01T00:00:0{idx}+00:00"}, retention=2)

    logs = repository.list_transform_debug_logs(limit=10)
    assert [item["id"] for item in logs] == ["log-2", "log-1"]


def test_transform_debug_handles_missing_route_trace_on_old_record(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    repository.record_transform_debug({"id": "old-1"}, retention=10)

    logs = repository.list_transform_debug_logs()
    assert logs[0]["route_trace"] is None


def test_transform_debug_falls_back_on_malformed_route_trace_json(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    with repository._connect() as conn:
        conn.execute(
            """
            INSERT INTO transform_debug_logs(
              id, timestamp, endpoint, route_trace_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("bad-1", "2026-01-01T00:00:00+00:00", "/v1/chat/completions", "not-json", "2026-01-01T00:00:00+00:00"),
        )

    logs = repository.list_transform_debug_logs()
    assert logs[0]["route_trace"] is None
