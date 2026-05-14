from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.control.repository import AppRepository
from app.dataplane.fireworks.error_contracts import classify_fireworks_error


@dataclass(frozen=True)
class FailureDecision:
    error_type: str
    retryable: bool
    cooldown_seconds: int | None = None
    disable_key: bool = False
    reason: str | None = None
    last_error_type: str | None = None


@dataclass(frozen=True)
class AppliedFailure:
    error_type: str
    retryable: bool
    scope: str
    account_id: str | None = None


def _body_text(value: str | None) -> str:
    return (value or "").lower()


def classify_upstream_failure(
    status_code: int | None,
    body_text: str | None = None,
    exception: Exception | None = None,
) -> FailureDecision:
    decision = classify_fireworks_error(status_code=status_code, body=body_text, exc=exception)
    cooldown_seconds: int | None = None
    if decision.should_cooldown:
        if decision.error_type in {"rate_limit", "quota_exhausted"}:
            cooldown_seconds = 120
        elif decision.error_type in {"timeout_error", "network_error", "capacity_error", "server_error", "upstream_error"}:
            cooldown_seconds = 15
        else:
            cooldown_seconds = 3600
    disable_key = decision.should_disable_key or (decision.error_type == "quota_exhausted" and status_code in {402, 412})
    if decision.should_disable_key:
        reason = "upstream_auth_failed"
    elif disable_key:
        reason = "upstream_account_unavailable"
    else:
        reason = None
    return FailureDecision(
        decision.error_type,
        decision.should_failover,
        cooldown_seconds=cooldown_seconds,
        disable_key=disable_key,
        reason=reason,
        last_error_type=decision.error_type if disable_key else None,
    )


def _snapshot_account_id(repository: AppRepository, key) -> str | None:
    snapshot = repository.get_fireworks_key_snapshot(getattr(key, "fingerprint", "")) if hasattr(repository, "get_fireworks_key_snapshot") else None
    account_id = getattr(snapshot, "account_id", None) if snapshot else None
    return str(account_id).strip() if account_id else None


def _disable_account_keys(repository: AppRepository, account_id: str, decision: FailureDecision) -> None:
    list_keys = getattr(repository, "list_keys", None)
    get_snapshot = getattr(repository, "get_fireworks_key_snapshot", None)
    if not callable(list_keys) or not callable(get_snapshot):
        return
    for candidate in list_keys(include_disabled=True):
        snapshot = get_snapshot(getattr(candidate, "fingerprint", ""))
        candidate_account_id = getattr(snapshot, "account_id", None) if snapshot else None
        if str(candidate_account_id or "").strip() == account_id:
            repository.set_key_enabled(
                candidate.name,
                False,
                decision.reason or decision.error_type,
                decision.last_error_type or decision.error_type,
            )


def apply_failure_to_key(
    repository: AppRepository,
    key_name: str,
    decision: FailureDecision,
) -> None:
    if decision.disable_key:
        repository.set_key_enabled(
            key_name,
            False,
            decision.reason or decision.error_type,
            decision.last_error_type or decision.error_type,
        )
        return

    if not decision.cooldown_seconds:
        return

    cooldown_until = (
        datetime.now(UTC) + timedelta(seconds=decision.cooldown_seconds)
    ).isoformat()
    repository.set_key_cooldown(key_name, cooldown_until, decision.error_type)


def apply_failure_to_candidate(
    repository: AppRepository,
    key,
    decision: FailureDecision,
) -> AppliedFailure:
    account_id = _snapshot_account_id(repository, key) or getattr(key, "account_id", None)

    if decision.disable_key:
        if decision.error_type == "quota_exhausted" and account_id:
            _disable_account_keys(repository, account_id, decision)
            return AppliedFailure(error_type=decision.error_type, retryable=decision.retryable, scope="account", account_id=account_id)
        apply_failure_to_key(repository, key.name, decision)
        return AppliedFailure(error_type=decision.error_type, retryable=decision.retryable, scope="key", account_id=account_id)

    if decision.error_type in {"quota_exhausted", "rate_limit"} and account_id:
        cooldown_seconds = decision.cooldown_seconds or 120
        cooldown_until = (datetime.now(UTC) + timedelta(seconds=cooldown_seconds)).isoformat()
        repository.set_account_cooldown(account_id, cooldown_until, decision.error_type)
        return AppliedFailure(error_type=decision.error_type, retryable=True, scope="account", account_id=account_id)

    if decision.error_type == "quota_exhausted" or decision.cooldown_seconds:
        apply_failure_to_key(repository, key.name, decision)
        return AppliedFailure(error_type=decision.error_type, retryable=True, scope="key", account_id=account_id)

    if decision.disable_key:
        apply_failure_to_key(repository, key.name, decision)
        return AppliedFailure(error_type=decision.error_type, retryable=decision.retryable, scope="key", account_id=account_id)

    return AppliedFailure(error_type=decision.error_type, retryable=decision.retryable, scope="none", account_id=account_id)


__all__ = ["AppliedFailure", "FailureDecision", "apply_failure_to_candidate", "apply_failure_to_key", "classify_upstream_failure"]
