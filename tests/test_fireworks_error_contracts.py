from __future__ import annotations

from app.dataplane.fireworks.error_contracts import classify_fireworks_error


def test_validation_400_does_not_failover_or_cooldown() -> None:
    decision = classify_fireworks_error(status_code=400, body={"error": {"message": "invalid request", "code": "validation_error"}})

    assert decision.error_type == "validation_error"
    assert decision.should_failover is False
    assert decision.should_cooldown is False
    assert decision.should_disable_key is False
    assert decision.client_status == 400


def test_auth_errors_disable_key() -> None:
    decision = classify_fireworks_error(status_code=401, body='{"error": {"message": "invalid api key", "code": "unauthorized"}}')

    assert decision.error_type == "auth_error"
    assert decision.should_failover is True
    assert decision.should_cooldown is False
    assert decision.should_disable_key is True
    assert decision.client_status == 401


def test_payment_required_402_is_quota_exhausted() -> None:
    decision = classify_fireworks_error(status_code=402, body={"error": {"message": "Account is not on a paid plan or has exceeded usage limits."}})

    assert decision.error_type == "quota_exhausted"
    assert decision.should_failover is True
    assert decision.should_cooldown is True
    assert decision.should_disable_key is False
    assert decision.client_status == 429


def test_model_not_found_is_not_failover_or_disable() -> None:
    decision = classify_fireworks_error(status_code=404, body={"error": {"message": "model not found", "code": "not_found"}})

    assert decision.error_type == "model_not_found"
    assert decision.should_failover is False
    assert decision.should_cooldown is False
    assert decision.should_disable_key is False
    assert decision.client_status == 404


def test_rate_limit_and_quota_exhausted_failover_and_cooldown() -> None:
    decision = classify_fireworks_error(status_code=429, body={"error": {"message": "quota exhausted", "code": "quota_exhausted"}})

    assert decision.error_type == "quota_exhausted"
    assert decision.should_failover is True
    assert decision.should_cooldown is True
    assert decision.should_disable_key is False
    assert decision.client_status == 429


def test_suspended_account_billing_412_is_quota_exhausted() -> None:
    decision = classify_fireworks_error(
        status_code=412,
        body={
            "error": {
                "message": "Account eharper187-o3ewh8xwf is suspended, possibly due to reaching the monthly spending limit or failure to pay past invoices.",
                "type": "api_error",
            }
        },
    )

    assert decision.error_type == "quota_exhausted"
    assert decision.should_failover is True
    assert decision.should_cooldown is True
    assert decision.should_disable_key is False
    assert decision.client_status == 429


def test_server_and_timeout_errors_failover_and_cooldown() -> None:
    server_decision = classify_fireworks_error(status_code=503, body={"error": {"message": "capacity overloaded"}})
    request_timeout_decision = classify_fireworks_error(status_code=408, body={"error": {"message": "request timeout"}})
    timeout_decision = classify_fireworks_error(exc=TimeoutError("upstream timed out"))

    assert server_decision.error_type == "capacity_error"
    assert server_decision.should_failover is True
    assert server_decision.should_cooldown is True
    assert server_decision.should_disable_key is False
    assert server_decision.client_status == 503

    assert request_timeout_decision.error_type == "timeout_error"
    assert request_timeout_decision.should_failover is True
    assert request_timeout_decision.should_cooldown is True
    assert request_timeout_decision.should_disable_key is False
    assert request_timeout_decision.client_status == 504

    assert timeout_decision.error_type == "timeout_error"
    assert timeout_decision.should_failover is True
    assert timeout_decision.should_cooldown is True
    assert timeout_decision.should_disable_key is False
    assert timeout_decision.client_status == 504


def test_unknown_4xx_is_safe_no_action() -> None:
    decision = classify_fireworks_error(status_code=422, body={"error": {"message": "weird upstream issue", "code": "strange"}})

    assert decision.error_type == "upstream_error"
    assert decision.should_failover is False
    assert decision.should_cooldown is False
    assert decision.should_disable_key is False
    assert decision.client_status == 422


def test_unknown_upstream_error_defaults_to_failover_and_cooldown() -> None:
    decision = classify_fireworks_error(body='{"error": {"message": "something broke"}}')

    assert decision.error_type == "upstream_error"
    assert decision.should_failover is True
    assert decision.should_cooldown is True
    assert decision.should_disable_key is False
    assert decision.client_status == 502
