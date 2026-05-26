from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from dataclasses import dataclass
from typing import Any, Iterable

from app.control.repository import KeyRecord


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def is_key_healthy(key: KeyRecord, now: datetime | None = None) -> bool:
    if not key.enabled:
        return False

    if not key.cooldown_until:
        return True

    cooldown_until = _parse_iso_datetime(key.cooldown_until)
    if cooldown_until is None:
        return True

    current = now or datetime.now(UTC)
    return cooldown_until <= current


def healthy_keys(keys: Iterable[KeyRecord], now: datetime | None = None) -> list[KeyRecord]:
    return [key for key in keys if is_key_healthy(key, now)]


@dataclass(frozen=True)
class AccountGate:
    account_id: str | None
    blocked: bool
    reason: str | None = None


@dataclass(frozen=True)
class CandidateSelection:
    selected_keys: list[KeyRecord]
    metadata: dict[str, Any]


def normalize_account_id(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if normalized.startswith("accounts/"):
        normalized = normalized.removeprefix("accounts/").strip()
    return normalized or None


def _snapshot_value(snapshot: Any, name: str, default: Any = None) -> Any:
    if snapshot is None:
        return default
    if isinstance(snapshot, dict):
        return snapshot.get(name, default)
    return getattr(snapshot, name, default)


def _cooldown_until(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        return _parse_iso_datetime(value)
    return _parse_iso_datetime(_snapshot_value(value, "cooldown_until"))


def _snapshot_stale_after(snapshot: Any) -> datetime | None:
    value = _snapshot_value(snapshot, "stale_after")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def snapshot_is_fresh(snapshot: Any, now: datetime | None = None) -> bool:
    stale_after = _snapshot_stale_after(snapshot)
    if stale_after is None:
        return False
    current = now or datetime.now(UTC)
    return stale_after > current


def account_id_for_key(key: KeyRecord, snapshots_by_fingerprint: dict[str, Any] | None) -> str | None:
    if not snapshots_by_fingerprint:
        return None
    snapshot = snapshots_by_fingerprint.get(key.fingerprint)
    return normalize_account_id(_snapshot_value(snapshot, "account_id"))


def account_bucket_for_key(key: KeyRecord, snapshots_by_fingerprint: dict[str, Any] | None) -> str:
    account_id = account_id_for_key(key, snapshots_by_fingerprint)
    if account_id:
        return f"account:{account_id}"
    return f"key:{key.fingerprint or key.name}"


def rendezvous_identity_score(route_key: str, identity: str) -> int:
    digest = sha256(f"{route_key}:{identity}".encode("utf-8")).digest()
    return int.from_bytes(digest, "big")


def rendezvous_score(route_key: str, key: KeyRecord) -> int:
    return rendezvous_identity_score(route_key, f"{key.fingerprint}:{key.name}")


def order_keys(keys: Iterable[KeyRecord], route_key: str) -> list[KeyRecord]:
    return sorted(keys, key=lambda key: rendezvous_score(route_key, key), reverse=True)


def candidate_keys(
    keys: Iterable[KeyRecord],
    route_key: str,
    max_attempts: int,
) -> list[KeyRecord]:
    return select_candidate_keys(keys, route_key, max_attempts).selected_keys


def _account_gate_for_snapshot(snapshot: Any, now: datetime | None = None) -> AccountGate:
    account_id = normalize_account_id(_snapshot_value(snapshot, "account_id"))
    quota_status = str(_snapshot_value(snapshot, "quota_status") or "").strip().casefold()
    quota_summary = _snapshot_value(snapshot, "quota_summary_json")
    quota_status_code = _snapshot_value(snapshot, "quota_status_code")
    account_state = str(_snapshot_value(snapshot, "account_state") or "").strip().casefold()
    suspend_state = str(_snapshot_value(snapshot, "suspend_state") or "").strip().casefold()
    fresh = snapshot_is_fresh(snapshot, now)
    exhausted = False
    unusable = False
    if quota_status in {"quota_exhausted", "exhausted", "over_quota", "billing_required", "auth_error"}:
        exhausted = True
    if quota_status in {"unusable", "disabled", "suspended"} or account_state in {"disabled", "suspended", "closed"} or suspend_state in {"suspended", "disabled"}:
        unusable = True
    if quota_status_code in {402, 412}:
        exhausted = True
    if quota_summary:
        try:
            summary = json.loads(quota_summary) if isinstance(quota_summary, str) else quota_summary
        except (TypeError, ValueError):
            summary = {}
        if isinstance(summary, dict):
            remaining = summary.get("monthly_remaining")
            budget = summary.get("monthly_budget")
            used = summary.get("monthly_used")
            if remaining is not None:
                try:
                    exhausted = exhausted or float(remaining) <= 0
                except (TypeError, ValueError):
                    pass
            elif budget is not None and used is not None:
                try:
                    exhausted = exhausted or float(budget) - float(used) <= 0
                except (TypeError, ValueError):
                    pass
    return AccountGate(account_id=account_id, blocked=fresh and (exhausted or unusable) or (not fresh and (exhausted or unusable)), reason=quota_status or None)


def select_candidate_keys(
    keys: Iterable[KeyRecord],
    route_key: str,
    max_attempts: int,
    snapshots_by_fingerprint: dict[str, Any] | None = None,
    account_cooldowns_by_account_id: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> CandidateSelection:
    current = now or datetime.now(UTC)
    healthy = healthy_keys(keys, current)
    keyed_snapshots = snapshots_by_fingerprint or {}
    grouped: dict[str, list[KeyRecord]] = {}
    for key in healthy:
        grouped.setdefault(account_bucket_for_key(key, keyed_snapshots), []).append(key)

    gated_accounts: set[str] = set()
    for bucket, bucket_keys in grouped.items():
        if not bucket.startswith("account:"):
            continue
        account_id = bucket.removeprefix("account:")
        cooldown = account_cooldowns_by_account_id.get(account_id) if account_cooldowns_by_account_id else None
        cooldown_until = _cooldown_until(cooldown)
        if cooldown_until is not None and cooldown_until > current:
            gated_accounts.add(bucket)
            continue
        if any(_account_gate_for_snapshot(keyed_snapshots.get(key.fingerprint), current).blocked for key in bucket_keys):
            gated_accounts.add(bucket)

    ordered_groups = []
    for bucket, bucket_keys in grouped.items():
        blocked = bucket in gated_accounts
        if blocked:
            continue
        ordered_groups.append((False, rendezvous_identity_score(route_key, bucket), bucket, order_keys(bucket_keys, route_key)))
    ordered_groups.sort(key=lambda item: (item[0], -item[1]))

    flattened: list[KeyRecord] = []
    max_len = max((len(bucket_keys) for _, _, _, bucket_keys in ordered_groups), default=0)
    for index in range(max_len):
        for degraded, _, bucket, bucket_keys in ordered_groups:
            if degraded:
                if index == 0:
                    flattened.extend(bucket_keys)
                continue
            if index < len(bucket_keys):
                flattened.append(bucket_keys[index])

    selected = flattened if max_attempts <= 0 else flattened[:max_attempts]
    metadata = {
        "routing_mode": "account_aware_sticky",
        "selected_account_count": sum(1 for bucket in grouped if bucket.startswith("account:")),
        "skipped_account_count": len(gated_accounts),
        "degraded_account_count": len(gated_accounts),
        "primary_account_bucket": ordered_groups[0][2] if ordered_groups else None,
        "selected_key_count": len(selected),
    }
    return CandidateSelection(selected_keys=selected, metadata=metadata)


__all__ = [
    "AccountGate",
    "CandidateSelection",
    "candidate_keys",
    "account_bucket_for_key",
    "account_id_for_key",
    "healthy_keys",
    "is_key_healthy",
    "order_keys",
    "normalize_account_id",
    "rendezvous_identity_score",
    "rendezvous_score",
    "select_candidate_keys",
]
