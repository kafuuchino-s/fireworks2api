from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.dataplane.fireworks.proxy import failover_on_error
from app.dataplane.routing.failover import AppliedFailure, FailureDecision, apply_failure_to_candidate, classify_upstream_failure


class DummyRepository:
    def __init__(self) -> None:
        self.enabled_calls: list[tuple] = []
        self.cooldown_calls: list[tuple] = []
        self.account_cooldown_calls: list[tuple] = []
        self.snapshots: dict[str, object | None] = {}

    def set_key_enabled(self, *args, **kwargs) -> None:
        self.enabled_calls.append((args, kwargs))

    def set_key_cooldown(self, *args, **kwargs) -> None:
        self.cooldown_calls.append((args, kwargs))

    def set_account_cooldown(self, *args, **kwargs) -> None:
        self.account_cooldown_calls.append((args, kwargs))

    def get_fireworks_key_snapshot(self, fingerprint: str):
        return self.snapshots.get(fingerprint)

    def list_keys(self, include_disabled: bool = True):
        return [SimpleNamespace(name=f"key-{fingerprint}", fingerprint=fingerprint) for fingerprint in self.snapshots]


@pytest.mark.parametrize(
    ("status_code", "body_text", "expected"),
    [
        (400, None, ("validation_error", False, None, False, None)),
        (401, None, ("auth_error", True, None, True, "upstream_auth_failed")),
        (403, None, ("auth_error", True, None, True, "upstream_auth_failed")),
        (403, "invalid api key", ("auth_error", True, None, True, "upstream_auth_failed")),
        (402, None, ("quota_exhausted", True, 120, True, "upstream_account_unavailable")),
        (404, None, ("model_not_found", False, None, False, None)),
        (408, None, ("timeout_error", True, 15, False, None)),
        (429, None, ("rate_limit", True, 120, False, None)),
        (412, "Account acct is suspended, possibly due to reaching the monthly spending limit or failure to pay past invoices.", ("quota_exhausted", True, 120, True, "upstream_account_unavailable")),
        (500, None, ("server_error", True, 15, False, None)),
        (503, None, ("server_error", True, 15, False, None)),
    ],
)
def test_failover_classifier_matrix(
    status_code: int,
    body_text: str | None,
    expected: tuple[str, bool, int | None, bool, str | None],
) -> None:
    decision = classify_upstream_failure(status_code, body_text)

    assert decision == FailureDecision(
        error_type=expected[0],
        retryable=expected[1],
        cooldown_seconds=expected[2],
        disable_key=expected[3],
        reason=expected[4],
        last_error_type=expected[0] if expected[3] else None,
    )


def test_timeout_and_network_failures_cooldown_and_retry() -> None:
    timeout_decision = classify_upstream_failure(None, exception=httpx.TimeoutException("x"))
    network_decision = classify_upstream_failure(None, exception=httpx.ConnectError("x"))

    assert timeout_decision.retryable is True
    assert timeout_decision.error_type == "timeout_error"
    assert timeout_decision.cooldown_seconds == 15
    assert network_decision.retryable is True
    assert network_decision.error_type == "network_error"
    assert network_decision.cooldown_seconds == 15


@pytest.mark.parametrize(
    ("status_code", "body_text", "expected_enabled", "expected_cooldown"),
    [
        (400, None, False, False),
        (401, None, True, False),
        (403, "invalid api key", True, False),
        (429, None, False, True),
        (500, None, False, True),
    ],
)
def test_failover_applies_classified_action(
    status_code: int,
    body_text: str | None,
    expected_enabled: bool,
    expected_cooldown: bool,
) -> None:
    repository = DummyRepository()

    error_type, retryable = failover_on_error(repository, "fw-test", status_code, body_text=body_text)

    assert retryable is (status_code != 400)
    assert error_type
    assert bool(repository.enabled_calls) is expected_enabled
    assert bool(repository.cooldown_calls) is expected_cooldown


def test_auth_failure_disables_key_and_uses_last_error_metadata() -> None:
    repository = DummyRepository()

    error_type, retryable = failover_on_error(repository, "fw-test", 401, body_text="unauthorized")

    assert retryable is True
    assert error_type == "auth_error"
    assert repository.enabled_calls
    args, _kwargs = repository.enabled_calls[0]
    assert args[0] == "fw-test"
    assert args[1] is False
    assert args[2] == "upstream_auth_failed"
    assert args[3] == "auth_error"


def test_quota_temporary_failure_cooldowns_without_disable() -> None:
    repository = DummyRepository()

    error_type, retryable = failover_on_error(repository, "fw-test", 402, body_text="quota exceeded")

    assert retryable is True
    assert error_type == "quota_exhausted"
    assert repository.enabled_calls
    assert not repository.cooldown_calls


def test_apply_failure_to_candidate_quota_uses_account_cooldown_when_snapshot_has_account() -> None:
    repository = DummyRepository()
    repository.snapshots["fp1"] = SimpleNamespace(account_id="acct-1")
    key = SimpleNamespace(name="fw-test", fingerprint="fp1")
    decision = FailureDecision(error_type="quota_exhausted", retryable=True, cooldown_seconds=None, disable_key=False, reason=None, last_error_type=None)

    applied = apply_failure_to_candidate(repository, key, decision)

    assert applied == AppliedFailure(error_type="quota_exhausted", retryable=True, scope="account", account_id="acct-1")
    assert repository.account_cooldown_calls
    assert not repository.cooldown_calls


def test_apply_failure_to_candidate_billing_failure_disables_account_sibling_keys() -> None:
    repository = DummyRepository()
    repository.snapshots["fp1"] = SimpleNamespace(account_id="acct-1")
    repository.snapshots["fp2"] = SimpleNamespace(account_id="acct-1")
    repository.snapshots["fp3"] = SimpleNamespace(account_id="acct-2")
    key = SimpleNamespace(name="key-fp1", fingerprint="fp1")
    decision = classify_upstream_failure(412, "Account acct is suspended, possibly due to reaching the monthly spending limit or failure to pay past invoices.")

    applied = apply_failure_to_candidate(repository, key, decision)

    assert applied == AppliedFailure(error_type="quota_exhausted", retryable=True, scope="account", account_id="acct-1")
    disabled_names = [call[0][0] for call in repository.enabled_calls]
    assert disabled_names == ["key-fp1", "key-fp2"]
    assert not repository.account_cooldown_calls
    assert not repository.cooldown_calls


def test_apply_failure_to_candidate_rate_limit_uses_account_cooldown_when_snapshot_has_account() -> None:
    repository = DummyRepository()
    repository.snapshots["fp1"] = SimpleNamespace(account_id="acct-1")
    key = SimpleNamespace(name="fw-test", fingerprint="fp1")
    decision = FailureDecision(error_type="rate_limit", retryable=True, cooldown_seconds=120, disable_key=False, reason=None, last_error_type=None)

    applied = apply_failure_to_candidate(repository, key, decision)

    assert applied == AppliedFailure(error_type="rate_limit", retryable=True, scope="account", account_id="acct-1")
    assert repository.account_cooldown_calls
    assert not repository.cooldown_calls


def test_apply_failure_to_candidate_suspended_billing_disables_account_key() -> None:
    repository = DummyRepository()
    repository.snapshots["fp1"] = SimpleNamespace(account_id="acct-suspended")
    key = SimpleNamespace(name="fw-test", fingerprint="fp1")
    decision = classify_upstream_failure(412, "Account acct is suspended, possibly due to reaching the monthly spending limit or failure to pay past invoices.")

    applied = apply_failure_to_candidate(repository, key, decision)

    assert applied == AppliedFailure(error_type="quota_exhausted", retryable=True, scope="account", account_id="acct-suspended")
    assert [call[0][0] for call in repository.enabled_calls] == ["key-fp1"]
    assert not repository.account_cooldown_calls
    assert not repository.cooldown_calls


def test_apply_failure_to_candidate_quota_falls_back_to_key_without_snapshot() -> None:
    repository = DummyRepository()
    key = SimpleNamespace(name="fw-test", fingerprint="fp1")
    decision = FailureDecision(error_type="quota_exhausted", retryable=True, cooldown_seconds=None, disable_key=False, reason=None, last_error_type=None)

    applied = apply_failure_to_candidate(repository, key, decision)

    assert applied.scope == "key"
    assert not repository.cooldown_calls


def test_timeout_exception_uses_short_cooldown() -> None:
    decision = classify_upstream_failure(None, exception=httpx.TimeoutException("x"))

    assert decision.retryable is True
    assert decision.error_type == "timeout_error"
    assert decision.cooldown_seconds == 15
