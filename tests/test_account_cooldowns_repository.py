from __future__ import annotations

from pathlib import Path

import pytest

from app.control.repository import AccountCooldownRecord, AppRepository
from app.platform.storage.db import init_db


def _repo(tmp_path: Path) -> AppRepository:
    db_path = tmp_path / "db.sqlite3"
    init_db(db_path)
    return AppRepository(db_path)


def test_set_get_account_cooldown(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    repo.set_account_cooldown("acc-1", "2099-01-01T00:00:00+00:00", "rate_limit")

    record = repo.get_account_cooldown("acc-1")
    assert record == AccountCooldownRecord(
        account_id="acc-1",
        cooldown_until="2099-01-01T00:00:00+00:00",
        last_error_type="rate_limit",
        last_error_at=record.last_error_at,
        updated_at=record.updated_at,
    )
    assert record.last_error_at is not None
    assert record.updated_at is not None


def test_reset_account_cooldown_updates_values(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    repo.set_account_cooldown("acc-1", "2099-01-01T00:00:00+00:00", "rate_limit")
    first = repo.get_account_cooldown("acc-1")
    repo.set_account_cooldown("acc-1", None, "server_error")
    second = repo.get_account_cooldown("acc-1")

    assert second is not None
    assert second.account_id == "acc-1"
    assert second.cooldown_until is None
    assert second.last_error_type == "server_error"
    assert second.last_error_at is not None
    assert second.updated_at is not None
    assert first is not None and second.updated_at >= first.updated_at


def test_list_account_cooldowns_returns_records(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.set_account_cooldown("acc-b", None, "server_error")
    repo.set_account_cooldown("acc-a", "2099-01-01T00:00:00+00:00", "rate_limit")

    records = repo.list_account_cooldowns()

    assert [record.account_id for record in records] == ["acc-a", "acc-b"]
    assert records[0].cooldown_until == "2099-01-01T00:00:00+00:00"
    assert records[1].cooldown_until is None


def test_clear_account_cooldown_removes_record(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.set_account_cooldown("acc-1", "2099-01-01T00:00:00+00:00", "rate_limit")

    repo.clear_account_cooldown("acc-1")

    assert repo.get_account_cooldown("acc-1") is None
    assert repo.list_account_cooldowns() == []


def test_account_prefix_normalization(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    repo.set_account_cooldown("accounts/acc-1", None, "rate_limit")

    assert repo.get_account_cooldown("acc-1") is not None
    assert repo.get_account_cooldown("accounts/acc-1").account_id == "acc-1"


def test_empty_account_id_raises(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    with pytest.raises(ValueError):
        repo.set_account_cooldown("", None, "rate_limit")
    with pytest.raises(ValueError):
        repo.get_account_cooldown("   ")
    with pytest.raises(ValueError):
        repo.clear_account_cooldown("accounts/")


def test_db_init_still_works(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite3"
    init_db(db_path)
    repo = AppRepository(db_path)
    repo.set_account_cooldown("acc-1", None, None)
    assert repo.get_account_cooldown("acc-1") is not None
