from __future__ import annotations

from pathlib import Path

from app.control.repository import AppRepository, ResponseSessionBindingRecord
from app.platform.storage.db import init_db


def _repo(tmp_path: Path) -> AppRepository:
    db_path = tmp_path / "db.sqlite3"
    init_db(db_path)
    return AppRepository(db_path)


def test_upsert_get_delete_response_session_binding(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    repo.upsert_response_session_binding("anthropic", "claude-3.5", "session-hash-1", "resp-1", "key-a", "fp-a")

    record = repo.get_response_session_binding("anthropic", "claude-3.5", "session-hash-1")
    assert record == ResponseSessionBindingRecord(
        scope="anthropic",
        model="claude-3.5",
        session_hash="session-hash-1",
        response_id="resp-1",
        key_name="key-a",
        key_fingerprint="fp-a",
        created_at=record.created_at,
        updated_at=record.updated_at,
    )

    repo.delete_response_session_binding("anthropic", "claude-3.5", "session-hash-1")

    assert repo.get_response_session_binding("anthropic", "claude-3.5", "session-hash-1") is None


def test_response_session_binding_upsert_replaces_latest_values(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    repo.upsert_response_session_binding("anthropic", "claude-3.5", "session-hash-1", "resp-1", "key-a", "fp-a")
    first = repo.get_response_session_binding("anthropic", "claude-3.5", "session-hash-1")
    repo.upsert_response_session_binding("anthropic", "claude-3.5", "session-hash-1", "resp-2")
    second = repo.get_response_session_binding("anthropic", "claude-3.5", "session-hash-1")

    assert second is not None
    assert second.response_id == "resp-2"
    assert second.key_name is None
    assert second.key_fingerprint is None
    assert first is not None and second.updated_at >= first.updated_at


def test_response_session_binding_isolated_by_scope_model_and_hash(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    repo.upsert_response_session_binding("anthropic", "claude-3.5", "session-hash-1", "resp-1")
    repo.upsert_response_session_binding("anthropic", "claude-3.5", "session-hash-2", "resp-2")
    repo.upsert_response_session_binding("anthropic", "claude-4", "session-hash-1", "resp-3")
    repo.upsert_response_session_binding("openai", "claude-3.5", "session-hash-1", "resp-4")

    assert repo.get_response_session_binding("anthropic", "claude-3.5", "session-hash-1").response_id == "resp-1"
    assert repo.get_response_session_binding("anthropic", "claude-3.5", "session-hash-2").response_id == "resp-2"
    assert repo.get_response_session_binding("anthropic", "claude-4", "session-hash-1").response_id == "resp-3"
    assert repo.get_response_session_binding("openai", "claude-3.5", "session-hash-1").response_id == "resp-4"


def test_response_session_binding_empty_inputs_noop(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    repo.upsert_response_session_binding(" ", "claude-3.5", "session-hash-1", "resp-1")
    repo.delete_response_session_binding("anthropic", "", "session-hash-1")

    assert repo.get_response_session_binding("anthropic", "claude-3.5", "session-hash-1") is None
