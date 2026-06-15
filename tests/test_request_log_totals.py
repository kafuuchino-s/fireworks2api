from __future__ import annotations

import sqlite3
from pathlib import Path

from app.control.repository import AppRepository
from app.platform.storage.db import init_db


def _repo(tmp_path: Path) -> AppRepository:
    db_path = tmp_path / "request-log-totals.sqlite3"
    init_db(db_path)
    return AppRepository(db_path)


def test_overview_request_totals_continue_past_log_retention(tmp_path: Path) -> None:
    repository = _repo(tmp_path)

    repository.insert_request_log(
        {"endpoint": "chat_completions", "status_code": 200, "input_tokens": 10, "latency_ms": 20},
        retention=2,
    )
    repository.insert_request_log(
        {
            "endpoint": "chat_completions",
            "status_code": 500,
            "input_tokens": 5,
            "cached_tokens": 2,
            "latency_ms": 40,
        },
        retention=2,
    )
    repository.insert_request_log(
        {"endpoint": "responses", "status_code": 200, "output_tokens": 7, "latency_ms": 60},
        retention=2,
    )

    overview = repository.overview()

    assert len(repository.list_request_logs(limit=10)) == 2
    assert overview["retained_request_count"] == 2
    assert overview["request_count"] == 3
    assert overview["error_count"] == 1
    assert overview["input_tokens"] == 15
    assert overview["output_tokens"] == 7
    assert overview["cached_tokens"] == 2
    assert overview["avg_latency_ms"] == 40


def test_request_log_totals_seed_from_existing_logs_on_migration(tmp_path: Path) -> None:
    db_path = tmp_path / "request-log-totals-migration.sqlite3"
    init_db(db_path)
    repository = AppRepository(db_path)
    repository.insert_request_log({"endpoint": "chat_completions", "status_code": 200}, retention=10)
    repository.insert_request_log({"endpoint": "chat_completions", "status_code": 500}, retention=10)

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE request_log_totals")

    init_db(db_path)
    overview = AppRepository(db_path).overview()

    assert overview["request_count"] == 2
    assert overview["error_count"] == 1


def test_request_log_tracks_estimated_tokens_separately(tmp_path: Path) -> None:
    repository = _repo(tmp_path)

    repository.insert_request_log(
        {
            "endpoint": "chat_completions",
            "status_code": 200,
            "input_tokens": 10,
            "output_tokens": 3,
            "cached_tokens": 1,
            "estimated": True,
        },
        retention=10,
    )
    repository.insert_request_log(
        {
            "endpoint": "chat_completions",
            "status_code": 200,
            "input_tokens": 4,
            "output_tokens": 2,
            "cached_tokens": 0,
            "estimated": False,
        },
        retention=10,
    )

    totals = repository.request_log_totals()
    assert totals["input_tokens"] == 14
    assert totals["output_tokens"] == 5
    assert totals["cached_tokens"] == 1
    assert totals["estimated_input_tokens"] == 10
    assert totals["estimated_output_tokens"] == 3
    assert totals["estimated_cached_tokens"] == 1

    logs = repository.list_request_logs(limit=10)
    estimated_logs = [log for log in logs if log.get("estimated")]
    assert len(estimated_logs) == 1
    assert estimated_logs[0]["input_tokens"] == 10
