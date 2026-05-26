from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.control.repository import KeyRecord
from app.dataplane.routing.sticky_router import (
    account_bucket_for_key,
    account_id_for_key,
    candidate_keys,
    normalize_account_id,
    select_candidate_keys,
)


def _key(name: str, fingerprint: str, *, enabled: bool = True, cooldown_until: str | None = None) -> KeyRecord:
    return KeyRecord(name=name, api_key=f"sk-{name}", fingerprint=fingerprint, enabled=enabled, cooldown_until=cooldown_until)


def _snapshot(account_id: str | None, *, stale_after: datetime | None = None, quota_status: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        account_id=account_id,
        stale_after=stale_after.isoformat() if stale_after else None,
        quota_status=quota_status,
    )


def test_normalize_and_account_bucket() -> None:
    assert normalize_account_id(" accounts/acct_123 ") == "acct_123"
    assert normalize_account_id("   ") is None

    key = _key("k1", "fp-1")
    assert account_bucket_for_key(key, {}) == "key:fp-1"


def test_same_account_keys_group_and_missing_accounts_stay_independent() -> None:
    keys = [_key("k1", "fp-1"), _key("k2", "fp-2"), _key("k3", "fp-3")]
    snapshots = {"fp-1": _snapshot("accounts/acct-a"), "fp-2": _snapshot("acct-a")}

    assert account_id_for_key(keys[0], snapshots) == "acct-a"
    assert account_bucket_for_key(keys[0], snapshots) == "account:acct-a"
    assert account_bucket_for_key(keys[2], snapshots) == "key:fp-3"


def test_same_route_key_is_stable() -> None:
    keys = [_key("k1", "fp-1"), _key("k2", "fp-2")]
    first = candidate_keys(keys, "route-1", max_attempts=10)
    second = candidate_keys(keys, "route-1", max_attempts=10)
    assert [key.fingerprint for key in first] == [key.fingerprint for key in second]


def test_stale_exhausted_quota_stays_blocked() -> None:
    now = datetime.now(UTC)
    keys = [_key("k1", "fp-1"), _key("k2", "fp-2")]
    snapshots = {"fp-1": _snapshot("acct-a", stale_after=now - timedelta(minutes=1), quota_status="exhausted")}

    selected = select_candidate_keys(keys, "route", 10, snapshots_by_fingerprint=snapshots, now=now)
    assert {key.fingerprint for key in selected.selected_keys} == {"fp-2"}


def test_fresh_exhausted_quota_blocks_siblings() -> None:
    now = datetime.now(UTC)
    keys = [_key("k1", "fp-1"), _key("k2", "fp-2")]
    snapshots = {"fp-1": _snapshot("acct-a", stale_after=now + timedelta(minutes=5), quota_status="exhausted"), "fp-2": _snapshot("acct-a", stale_after=now + timedelta(minutes=5), quota_status="ok")}

    selected = select_candidate_keys(keys, "route", 10, snapshots_by_fingerprint=snapshots, now=now)
    assert selected.selected_keys == []
    assert selected.metadata["routing_mode"] == "account_aware_sticky"
    assert selected.metadata["selected_account_count"] == 1
    assert selected.metadata["skipped_account_count"] == 1


def test_unavailable_quota_does_not_block() -> None:
    now = datetime.now(UTC)
    keys = [_key("k1", "fp-1"), _key("k2", "fp-2")]
    snapshots = {"fp-1": _snapshot("acct-a", stale_after=now + timedelta(minutes=5), quota_status="unavailable")}

    selected = select_candidate_keys(keys, "route", 10, snapshots_by_fingerprint=snapshots, now=now)
    assert {key.fingerprint for key in selected.selected_keys} == {"fp-1", "fp-2"}


def test_account_cooldown_blocks_sibling_keys() -> None:
    now = datetime.now(UTC)
    keys = [_key("k1", "fp-1"), _key("k2", "fp-2")]
    snapshots = {"fp-1": _snapshot("acct-a", stale_after=now + timedelta(minutes=5), quota_status="ok"), "fp-2": _snapshot("acct-a", stale_after=now + timedelta(minutes=5), quota_status="ok")}

    selected = select_candidate_keys(keys, "route", 10, snapshots_by_fingerprint=snapshots, account_cooldowns_by_account_id={"acct-a": (now + timedelta(minutes=5)).isoformat()}, now=now)
    assert selected.selected_keys == []
    assert selected.metadata["skipped_account_count"] == 1


def test_max_attempts_respected() -> None:
    keys = [_key(f"k{i}", f"fp-{i}") for i in range(4)]
    selected = select_candidate_keys(keys, "route", 2)
    assert len(selected.selected_keys) == 2


def test_account_round_robin_spreads_attempts() -> None:
    now = datetime.now(UTC)
    keys = [_key("a1", "fp-a1"), _key("a2", "fp-a2"), _key("b1", "fp-b1")]
    snapshots = {
        "fp-a1": _snapshot("acct-a", stale_after=now + timedelta(minutes=5), quota_status="ok"),
        "fp-a2": _snapshot("acct-a", stale_after=now + timedelta(minutes=5), quota_status="ok"),
        "fp-b1": _snapshot("acct-b", stale_after=now + timedelta(minutes=5), quota_status="ok"),
    }

    selected = select_candidate_keys(keys, "route", 2, snapshots_by_fingerprint=snapshots, now=now)
    assert len(selected.selected_keys) == 2
    assert len({key.fingerprint for key in selected.selected_keys}) == 2
