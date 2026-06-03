from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.dataplane.fireworks.client import FireworksClient
from app.dataplane.fireworks.error_contracts import classify_fireworks_error
from app.dataplane.fireworks.management import (
    FireworksManagementClient,
    build_model_catalog_item,
    build_official_model_catalog,
    select_fireworks_api_key,
)
from app.platform.redaction import redact_secret
from app.platform.redaction import fingerprint_secret

from .deps import _repository, _settings

QUOTA_TTL_SECONDS = 30 * 60

logger = logging.getLogger(__name__)
router = APIRouter()

_quota_refresh_lock: asyncio.Lock | None = None
_account_refresh_locks: dict[str, asyncio.Lock] = {}
_refresh_state: dict[str, Any] = {
    "refresh_started_at": None,
    "refresh_in_progress": False,
    "last_successful_refresh_at": None,
    "consecutive_refresh_failures": 0,
    "next_refresh_after": None,
}


def _quota_ttl_seconds(settings) -> int:
    return max(1, int(getattr(settings, "fireworks_quota_ttl_seconds", QUOTA_TTL_SECONDS) or QUOTA_TTL_SECONDS))


def _quota_refresh_concurrency(settings) -> int:
    return max(1, int(getattr(settings, "fireworks_quota_refresh_concurrency", 4) or 4))


def _quota_auto_disable_enabled(settings) -> bool:
    return bool(getattr(settings, "fireworks_auto_disable_exhausted_accounts", True))


def _get_quota_refresh_lock() -> asyncio.Lock:
    global _quota_refresh_lock
    if _quota_refresh_lock is None:
        _quota_refresh_lock = asyncio.Lock()
    return _quota_refresh_lock


def _get_account_refresh_lock(account_id: str) -> asyncio.Lock:
    normalized = _normalize_fireworks_account_id(account_id) or account_id
    if normalized not in _account_refresh_locks:
        _account_refresh_locks[normalized] = asyncio.Lock()
    return _account_refresh_locks[normalized]


def _refresh_state_payload() -> dict[str, Any]:
    return dict(_refresh_state)


def _set_global_refresh_started() -> None:
    now = datetime.now(UTC).isoformat()
    _refresh_state["refresh_started_at"] = now
    _refresh_state["refresh_in_progress"] = True


def _set_global_refresh_finished(*, success: bool, next_refresh_after: str | None = None) -> None:
    now = datetime.now(UTC).isoformat()
    _refresh_state["refresh_in_progress"] = False
    if success:
        _refresh_state["last_successful_refresh_at"] = now
        _refresh_state["consecutive_refresh_failures"] = 0
    else:
        _refresh_state["consecutive_refresh_failures"] = int(_refresh_state.get("consecutive_refresh_failures") or 0) + 1
    _refresh_state["next_refresh_after"] = next_refresh_after


def _normalize_fireworks_account_id(account_id: str) -> str:
    value = str(account_id or "").strip()
    if value.startswith("accounts/"):
        return value.removeprefix("accounts/")
    return value


def _fireworks_quota_items(payload: dict[str, Any]) -> list[Any]:
    items = payload.get("data") or payload.get("quotas") or payload.get("items") or []
    return items if isinstance(items, list) else []


def _quota_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return int(value) if value.isdigit() else float(value)
        except ValueError:
            return None
    return None


def _quota_summary(items: list[Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {"count": len(items)}
    for item in items:
        if not isinstance(item, dict):
            continue
        quota_name = str(item.get("name") or item.get("id") or item.get("quotaId") or "").casefold()
        value = _quota_number(item.get("value"))
        usage = _quota_number(item.get("usage"))
        max_value = _quota_number(item.get("maxValue") or item.get("max_value"))
        update_time = item.get("updateTime") or item.get("update_time")
        if quota_name.endswith("monthly-spend-usd"):
            budget = value if value is not None else max_value
            summary["monthly_budget"] = budget
            summary["monthly_used"] = usage
            if budget is not None and usage is not None:
                summary["monthly_remaining"] = max(0, float(budget) - float(usage))
            if update_time:
                summary["monthly_spend_updated_at"] = update_time
        elif quota_name.endswith("serverless-inference-rpm"):
            summary["serverless_rpm_limit"] = value if value is not None else max_value
            summary["serverless_rpm_usage"] = usage
            if update_time:
                summary["serverless_rpm_updated_at"] = update_time
    return summary


def _quota_summary_from_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {"count": 0}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {"count": 0}
    return parsed if isinstance(parsed, dict) else {"count": 0}


def _normalize_exhausted_quota_summary(summary: dict[str, Any], quota_status: Any) -> dict[str, Any]:
    status_value = str(quota_status or "").casefold()
    if status_value not in {"quota_exhausted", "exhausted"}:
        return summary
    budget = _quota_number(summary.get("monthly_budget"))
    if budget is None:
        return summary
    normalized = dict(summary)
    used = _quota_number(normalized.get("monthly_used"))
    if used is None or float(used) < float(budget):
        normalized["monthly_used"] = budget
    normalized["monthly_remaining"] = 0
    return normalized


def _normalize_payload_quota_summary(payload: dict[str, Any]) -> None:
    summary = _quota_summary_from_json(payload.get("quota_summary_json"))
    normalized = _normalize_exhausted_quota_summary(summary, payload.get("quota_status"))
    if normalized is not summary:
        payload["quota_summary_json"] = json.dumps(normalized, sort_keys=True)


def _carry_forward_previous_quota(payload: dict[str, Any], previous_snapshot) -> None:
    previous_summary = _quota_summary_from_json(_snapshot_value(previous_snapshot, "quota_summary_json"))
    if _quota_number(previous_summary.get("monthly_budget")) is None:
        return
    payload["quota_summary_json"] = json.dumps(previous_summary, sort_keys=True)
    previous_items = _snapshot_value(previous_snapshot, "quota_items_json")
    if previous_items:
        payload["quota_items_json"] = previous_items
    if not payload.get("last_successful_refresh_at"):
        payload["last_successful_refresh_at"] = (
            _snapshot_value(previous_snapshot, "last_successful_refresh_at")
            or _snapshot_value(previous_snapshot, "quota_refreshed_at")
        )
    _normalize_payload_quota_summary(payload)


async def _fireworks_account_and_quota_payload(request: Request, account_id: str, api_key: str) -> dict[str, Any]:
    settings = _settings(request)
    normalized_account_id = _normalize_fireworks_account_id(account_id)
    async with FireworksManagementClient(settings, api_key) as client:
        account_response = await client.get_json(f"/v1/accounts/{normalized_account_id}")
        try:
            account_payload = account_response.json()
        except ValueError:
            account_payload = {"raw": account_response.text}
        quota_response = await client.get_json(f"/v1/accounts/{normalized_account_id}/quotas")
        try:
            quota_payload = quota_response.json()
        except ValueError:
            quota_payload = {"raw": quota_response.text}
    quota_items = _fireworks_quota_items(quota_payload if isinstance(quota_payload, dict) else {})
    return {
        "account_response": account_response,
        "account_payload": account_payload,
        "quota_response": quota_response,
        "quota_payload": quota_payload,
        "quota_items": quota_items,
        "quota_summary": _quota_summary(quota_items),
        "normalized_account_id": normalized_account_id,
    }


def _snapshot_is_stale(snapshot) -> bool:
    stale_after = _snapshot_value(snapshot, "stale_after")
    if not snapshot or not stale_after:
        return False
    try:
        return datetime.fromisoformat(stale_after) <= datetime.now(UTC)
    except Exception:
        return True


def _snapshot_needs_quota_refresh(snapshot) -> bool:
    if not snapshot:
        return True
    refresh_status = str(_snapshot_value(snapshot, "refresh_status") or "").strip().casefold()
    quota_refreshed_at = _snapshot_value(snapshot, "quota_refreshed_at")
    quota_status = str(_snapshot_value(snapshot, "quota_status") or "").strip().casefold()
    return (
        _snapshot_is_stale(snapshot)
        or refresh_status in {"partial", "pending"}
        or not quota_refreshed_at
        or quota_status in {"", "unavailable"}
    )


def _snapshot_value(snapshot, name: str, default=None):
    if snapshot is None:
        return default
    if isinstance(snapshot, dict):
        return snapshot.get(name, default)
    return getattr(snapshot, name, default)


def _parse_snapshot_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _snapshot_was_refreshed_since(snapshot, started_at: str) -> bool:
    started = _parse_snapshot_time(started_at)
    if not snapshot or started is None:
        return False
    for name in ("quota_refreshed_at", "last_successful_refresh_at", "refresh_started_at"):
        refreshed_at = _parse_snapshot_time(_snapshot_value(snapshot, name))
        if refreshed_at is not None and refreshed_at >= started:
            return True
    return False


def _copy_account_quota_snapshot(payload: dict[str, Any], snapshot) -> None:
    for name in (
        "quota_supported",
        "quota_status",
        "quota_status_code",
        "quota_summary_json",
        "quota_items_json",
        "quota_refreshed_at",
        "stale_after",
        "refresh_status",
        "last_refresh_error_type",
        "last_refresh_error",
        "refresh_started_at",
        "last_successful_refresh_at",
        "consecutive_refresh_failures",
        "next_refresh_after",
    ):
        payload[name] = _snapshot_value(snapshot, name)


def _response_error_text(response) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text:
        return text
    try:
        return json.dumps(response.json(), ensure_ascii=False)
    except Exception:
        return ""


def _apply_management_error(
    payload: dict[str, Any],
    response,
    *,
    scope: str,
    ttl_seconds: int = QUOTA_TTL_SECONDS,
    previous_snapshot=None,
) -> None:
    status_code = getattr(response, "status_code", None)
    body_text = _response_error_text(response)
    decision = classify_fireworks_error(status_code=status_code, body=body_text)
    payload["quota_supported"] = False
    payload["quota_status_code"] = status_code
    payload["refresh_status"] = "error"
    payload["last_refresh_error_type"] = decision.error_type
    payload["last_refresh_error"] = body_text or f"Fireworks {scope} request failed with status {status_code}"
    if decision.error_type in {"quota_exhausted", "rate_limit"}:
        payload["quota_status"] = decision.error_type
        if decision.error_type == "quota_exhausted":
            payload["suspend_state"] = payload.get("suspend_state") or "suspended"
            payload["account_state"] = payload.get("account_state") or "suspended"
            _carry_forward_previous_quota(payload, previous_snapshot)
    elif decision.error_type == "auth_error":
        payload["quota_status"] = "auth_error"
    else:
        payload["quota_status"] = "unavailable"
    payload["quota_refreshed_at"] = datetime.now(UTC).isoformat()
    payload["stale_after"] = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat()
    payload["next_refresh_after"] = payload["stale_after"]


def _disable_keys_for_snapshot_error(repository, key, payload: dict[str, Any], *, auto_disable: bool = True) -> None:
    if not auto_disable:
        return
    error_type = str(payload.get("last_refresh_error_type") or "")
    status_code = payload.get("quota_status_code")
    if error_type == "auth_error":
        repository.set_key_enabled(key.name, False, "upstream_auth_failed", "auth_error")
        return
    if error_type != "quota_exhausted" or status_code not in {402, 412}:
        return
    account_id = str(payload.get("account_id") or "").strip()
    if not account_id:
        repository.set_key_enabled(key.name, False, "upstream_account_unavailable", "quota_exhausted")
        return
    get_snapshot = getattr(repository, "get_fireworks_key_snapshot", None)
    list_keys = getattr(repository, "list_keys", None)
    if not callable(get_snapshot) or not callable(list_keys):
        repository.set_key_enabled(key.name, False, "upstream_account_unavailable", "quota_exhausted")
        return
    for candidate in list_keys(include_disabled=True):
        snapshot = get_snapshot(getattr(candidate, "fingerprint", ""))
        candidate_account_id = _snapshot_value(snapshot, "account_id")
        if str(candidate_account_id or "").strip() == account_id or getattr(candidate, "name", None) == key.name:
            repository.set_key_enabled(candidate.name, False, "upstream_account_unavailable", "quota_exhausted")


def _snapshot_response_item(key, snapshot, *, source: str, stale: bool) -> dict[str, Any]:
    quota_summary_json = _snapshot_value(snapshot, "quota_summary_json")
    quota_items_json = _snapshot_value(snapshot, "quota_items_json")
    quota_status = _snapshot_value(snapshot, "quota_status", "unavailable")
    quota_summary = _normalize_exhausted_quota_summary(_quota_summary_from_json(quota_summary_json), quota_status)
    quota_items = json.loads(quota_items_json) if quota_items_json else []
    return {
        "key_name": key.name,
        "masked_key": redact_secret(key.api_key, visible=6),
        "enabled": getattr(key, "enabled", None),
        "disabled_reason": getattr(key, "disabled_reason", None),
        "last_error_type": getattr(key, "last_error_type", None),
        "last_error_at": getattr(key, "last_error_at", None),
        "account_id": _snapshot_value(snapshot, "account_id"),
        "account_label": _snapshot_value(snapshot, "account_label"),
        "account_state": _snapshot_value(snapshot, "account_state"),
        "suspend_state": _snapshot_value(snapshot, "suspend_state"),
        "quota_supported": bool(_snapshot_value(snapshot, "quota_supported")) if _snapshot_value(snapshot, "quota_supported") is not None else False,
        "quota_status_code": _snapshot_value(snapshot, "quota_status_code"),
        "quota_status": quota_status,
        "quota_items": quota_items,
        "quota_summary": quota_summary,
        "source": source,
        "stale": stale,
        "last_refreshed_at": _snapshot_value(snapshot, "quota_refreshed_at"),
        "stale_after": _snapshot_value(snapshot, "stale_after"),
        "refresh_status": _snapshot_value(snapshot, "refresh_status"),
        "last_refresh_error_type": _snapshot_value(snapshot, "last_refresh_error_type"),
        "last_refresh_error": _snapshot_value(snapshot, "last_refresh_error"),
        "refresh_started_at": _snapshot_value(snapshot, "refresh_started_at"),
        "last_successful_refresh_at": _snapshot_value(snapshot, "last_successful_refresh_at"),
        "consecutive_refresh_failures": _snapshot_value(snapshot, "consecutive_refresh_failures", 0),
        "next_refresh_after": _snapshot_value(snapshot, "next_refresh_after"),
        "error": _snapshot_value(snapshot, "last_refresh_error") if _snapshot_value(snapshot, "last_refresh_error") else None,
    }


def _pool_group_key(item: dict[str, Any]) -> tuple[str, str]:
    account_id = _normalize_fireworks_account_id(str(item.get("account_id") or "").strip())
    if account_id:
        return f"account:{account_id}", "account"
    key_name = str(item.get("key_name") or item.get("masked_key") or "").strip()
    return f"key:{key_name}", "key"


def _pool_item_score(item: dict[str, Any]) -> int:
    summary = item.get("quota_summary") if isinstance(item.get("quota_summary"), dict) else {}
    status = str(item.get("quota_status") or "").casefold()
    score = 0
    if item.get("quota_supported") is True:
        score += 4
    if status == "ok":
        score += 4
    if not item.get("stale"):
        score += 2
    if item.get("refresh_status") != "error" and not item.get("last_refresh_error_type"):
        score += 2
    if any(_quota_number(summary.get(name)) is not None for name in ("monthly_budget", "monthly_used", "monthly_remaining")):
        score += 3
    return score


def _pool_number(summary: dict[str, Any], name: str) -> float | int | None:
    return _quota_number(summary.get(name))


def _quota_auto_disabled_item(item: dict[str, Any]) -> bool:
    return (
        item.get("enabled") is False
        and item.get("disabled_reason") == "upstream_account_unavailable"
        and item.get("last_error_type") == "quota_exhausted"
    )


def _has_pool_monthly_budget(item: dict[str, Any]) -> bool:
    summary = item.get("quota_summary") if isinstance(item.get("quota_summary"), dict) else {}
    return _pool_number(summary, "monthly_budget") is not None


def _pool_accounting_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accounting_items = []
    for item in items:
        if item.get("enabled") is not False:
            accounting_items.append(item)
        elif _quota_auto_disabled_item(item) and _has_pool_monthly_budget(item):
            accounting_items.append(item)
    return accounting_items


def _fireworks_key_pool_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    enabled_items = [item for item in items if item.get("enabled") is not False]
    accounting_items = _pool_accounting_items(items)
    quota_disabled_items = [item for item in accounting_items if _quota_auto_disabled_item(item)]
    groups: dict[str, dict[str, Any]] = {}
    group_kinds: dict[str, str] = {}
    grouped_key_counts: dict[str, int] = {}
    for item in accounting_items:
        group_key, kind = _pool_group_key(item)
        grouped_key_counts[group_key] = grouped_key_counts.get(group_key, 0) + 1
        if group_key not in groups or _pool_item_score(item) > _pool_item_score(groups[group_key]):
            groups[group_key] = item
            group_kinds[group_key] = kind

    monthly_budget = 0.0
    monthly_used = 0.0
    monthly_remaining = 0.0
    has_budget = False
    has_used = False
    has_remaining = False
    stale_count = 0
    refresh_error_count = 0
    unavailable_count = 0
    blocking_count = 0
    status_counts: dict[str, int] = {}

    blocking_statuses = {"quota_exhausted", "billing_required", "suspended", "auth_error", "disabled", "unusable"}
    for item in groups.values():
        summary = item.get("quota_summary") if isinstance(item.get("quota_summary"), dict) else {}
        status_value = str(item.get("quota_status") or summary.get("quota_status") or "unavailable").casefold()
        status_counts[status_value] = status_counts.get(status_value, 0) + 1
        if item.get("stale"):
            stale_count += 1
        if item.get("refresh_status") == "error" or item.get("last_refresh_error_type"):
            refresh_error_count += 1
        if item.get("quota_supported") is False or status_value in {"", "unavailable"}:
            unavailable_count += 1
        if status_value in blocking_statuses:
            blocking_count += 1

        budget = _pool_number(summary, "monthly_budget")
        used = _pool_number(summary, "monthly_used")
        remaining = _pool_number(summary, "monthly_remaining")
        if remaining is None and budget is not None and used is not None:
            remaining = max(0.0, float(budget) - float(used))
        if budget is not None:
            monthly_budget += float(budget)
            has_budget = True
        if used is not None:
            monthly_used += float(used)
            has_used = True
        if remaining is not None:
            monthly_remaining += float(remaining)
            has_remaining = True

    usage_ratio = monthly_used / monthly_budget if has_budget and monthly_budget > 0 and has_used else None
    if not groups:
        quota_status = "unavailable"
    elif blocking_count == len(groups):
        quota_status = "quota_exhausted" if status_counts.get("quota_exhausted") else "unavailable"
    elif blocking_count:
        quota_status = "degraded"
    elif stale_count or refresh_error_count:
        quota_status = "stale"
    elif usage_ratio is not None and usage_ratio >= 0.9:
        quota_status = "near_exhausted"
    elif usage_ratio is not None and usage_ratio >= 0.7:
        quota_status = "watch"
    elif has_budget or has_used or has_remaining:
        quota_status = "ok"
    else:
        quota_status = "unavailable"

    return {
        "key_count": len(items),
        "enabled_key_count": len(enabled_items),
        "accounting_key_count": len(accounting_items),
        "quota_disabled_key_count": len(quota_disabled_items),
        "quota_source_count": len(groups),
        "account_count": sum(1 for kind in group_kinds.values() if kind == "account"),
        "unknown_account_source_count": sum(1 for kind in group_kinds.values() if kind != "account"),
        "deduplicated_key_count": sum(max(count - 1, 0) for count in grouped_key_counts.values()),
        "monthly_budget": monthly_budget if has_budget else None,
        "monthly_used": monthly_used if has_used else None,
        "monthly_remaining": monthly_remaining if has_remaining else None,
        "usage_ratio": usage_ratio,
        "quota_status": quota_status,
        "quota_status_counts": status_counts,
        "stale_count": stale_count,
        "refresh_error_count": refresh_error_count,
        "unavailable_count": unavailable_count,
        "blocking_count": blocking_count,
    }


async def _refresh_fireworks_key_snapshot(request: Request, key, *, refresh_quota: bool) -> dict[str, Any]:
    settings = _settings(request)
    repository = _repository(request)
    ttl_seconds = _quota_ttl_seconds(settings)
    refresh_started_at = datetime.now(UTC).isoformat()
    payload: dict[str, Any] = {
        "key_fingerprint": getattr(key, "fingerprint", None) or fingerprint_secret(getattr(key, "api_key", "")),
        "account_id": None,
        "account_label": None,
        "account_state": None,
        "suspend_state": None,
        "quota_supported": None,
        "quota_status": "unavailable",
        "quota_status_code": None,
        "quota_summary_json": json.dumps({"count": 0}),
        "quota_items_json": json.dumps([]),
        "account_refreshed_at": None,
        "quota_refreshed_at": None,
        "stale_after": None,
        "refresh_status": "ok",
        "last_refresh_error_type": None,
        "last_refresh_error": None,
        "refresh_started_at": refresh_started_at,
        "last_successful_refresh_at": None,
        "consecutive_refresh_failures": 0,
        "next_refresh_after": None,
    }
    try:
        async with FireworksManagementClient(settings, key.api_key) as client:
            account_response = await client.get_json("/v1/accounts")
            if account_response.status_code >= 400:
                existing = getattr(repository, "get_fireworks_key_snapshot", lambda _fingerprint: None)(payload["key_fingerprint"])
                _apply_management_error(payload, account_response, scope="account", ttl_seconds=ttl_seconds, previous_snapshot=existing)
                account_id = _snapshot_value(existing, "account_id")
                if account_id:
                    payload["account_id"] = account_id
                payload["consecutive_refresh_failures"] = int(_snapshot_value(existing, "consecutive_refresh_failures", 0) or 0) + 1
                upsert_snapshot = getattr(repository, "upsert_fireworks_key_snapshot", None)
                if callable(upsert_snapshot):
                    upsert_snapshot(payload)
                _disable_keys_for_snapshot_error(repository, key, payload, auto_disable=_quota_auto_disable_enabled(settings))
                return payload
            account_payload = account_response.json()
            accounts = account_payload.get("data") or account_payload.get("accounts") or []
            account = accounts[0] if accounts and isinstance(accounts[0], dict) else {}
            account_id = _normalize_fireworks_account_id(str(account.get("id") or account.get("name") or ""))
            payload.update({
                "account_id": account_id or None,
                "account_label": account.get("label") or account.get("name") or account.get("id"),
                "account_state": account.get("state") or account.get("status"),
                "suspend_state": account.get("suspend_state") or account.get("suspended"),
                "account_refreshed_at": datetime.now(UTC).isoformat(),
            })
            if refresh_quota and account_id:
                account_lock = _get_account_refresh_lock(account_id)
                async with account_lock:
                    get_account_snapshot = getattr(repository, "get_fireworks_account_quota_snapshot", None)
                    account_snapshot = get_account_snapshot(account_id) if callable(get_account_snapshot) else None
                    if _snapshot_was_refreshed_since(account_snapshot, refresh_started_at):
                        _copy_account_quota_snapshot(payload, account_snapshot)
                    else:
                        quota_response = await client.get_json(f"/v1/accounts/{account_id}/quotas")
                        if quota_response.status_code >= 400:
                            existing = getattr(repository, "get_fireworks_key_snapshot", lambda _fingerprint: None)(payload["key_fingerprint"])
                            _apply_management_error(payload, quota_response, scope="quota", ttl_seconds=ttl_seconds, previous_snapshot=existing)
                            payload["consecutive_refresh_failures"] = int(_snapshot_value(existing, "consecutive_refresh_failures", 0) or 0) + 1
                            upsert_snapshot = getattr(repository, "upsert_fireworks_key_snapshot", None)
                            if callable(upsert_snapshot):
                                upsert_snapshot(payload)
                            _disable_keys_for_snapshot_error(repository, key, payload, auto_disable=_quota_auto_disable_enabled(settings))
                            return payload
                        payload["quota_supported"] = quota_response.status_code == 200
                        payload["quota_status_code"] = quota_response.status_code
                        payload["quota_status"] = "ok" if quota_response.status_code == 200 else "unavailable"
                        quota_payload = quota_response.json()
                        quota_items = _fireworks_quota_items(quota_payload if isinstance(quota_payload, dict) else {})
                        payload["quota_items_json"] = json.dumps(quota_items)
                        payload["quota_summary_json"] = json.dumps(_quota_summary(quota_items), sort_keys=True)
                        payload["quota_refreshed_at"] = datetime.now(UTC).isoformat()
                        payload["last_successful_refresh_at"] = payload["quota_refreshed_at"]
                        payload["consecutive_refresh_failures"] = 0
                        payload["stale_after"] = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat()
                        payload["next_refresh_after"] = payload["stale_after"]
    except Exception as exc:
        existing = getattr(repository, "get_fireworks_key_snapshot", lambda _fingerprint: None)(payload["key_fingerprint"])
        already_classified = payload.get("refresh_status") == "error" and payload.get("last_refresh_error_type")
        if existing:
            payload.update({
                "account_id": _snapshot_value(existing, "account_id"),
                "account_label": _snapshot_value(existing, "account_label"),
                "account_state": _snapshot_value(existing, "account_state"),
                "suspend_state": _snapshot_value(existing, "suspend_state"),
                "quota_supported": _snapshot_value(existing, "quota_supported"),
                "quota_status": _snapshot_value(existing, "quota_status"),
                "quota_status_code": _snapshot_value(existing, "quota_status_code"),
                "quota_summary_json": _snapshot_value(existing, "quota_summary_json") or json.dumps({"count": 0}),
                "quota_items_json": _snapshot_value(existing, "quota_items_json") or json.dumps([]),
                "account_refreshed_at": _snapshot_value(existing, "account_refreshed_at"),
                "quota_refreshed_at": _snapshot_value(existing, "quota_refreshed_at"),
                "stale_after": _snapshot_value(existing, "stale_after"),
                "last_successful_refresh_at": _snapshot_value(existing, "last_successful_refresh_at"),
            })
        if already_classified:
            payload["refresh_status"] = "error"
        else:
            decision = classify_fireworks_error(body=str(exc))
            if decision.error_type in {"quota_exhausted", "rate_limit", "auth_error"}:
                payload["quota_status"] = decision.error_type
                payload["quota_supported"] = False
                payload["quota_refreshed_at"] = datetime.now(UTC).isoformat()
                payload["stale_after"] = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat()
                if decision.error_type == "quota_exhausted":
                    _normalize_payload_quota_summary(payload)
        payload["refresh_status"] = "error"
        payload["last_refresh_error_type"] = payload.get("last_refresh_error_type") or exc.__class__.__name__
        payload["last_refresh_error"] = payload.get("last_refresh_error") or str(exc)
        payload["consecutive_refresh_failures"] = int(_snapshot_value(existing, "consecutive_refresh_failures", 0) or 0) + 1
        payload["next_refresh_after"] = payload.get("stale_after") or (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat()
    upsert_snapshot = getattr(repository, "upsert_fireworks_key_snapshot", None)
    if callable(upsert_snapshot):
        upsert_snapshot(payload)
    return payload

def _fireworks_context(request: Request):
    settings = _settings(request)
    repository = _repository(request)
    return select_fireworks_api_key(settings, repository)


async def _fireworks_get_json(request: Request, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = _fireworks_context(request)
    if not ctx.api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"supported": False, "reason": "not_configured"})
    settings = _settings(request)
    async with FireworksManagementClient(settings, ctx.api_key) as client:
        response = await client.get_json(path, params=params)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text}
    return {"source": ctx.source, "status_code": response.status_code, "payload": payload}


@router.get("/fireworks/accounts")
async def list_fireworks_accounts(request: Request):
    ctx = _fireworks_context(request)
    if not ctx.api_key:
        return {"supported": False, "reason": "not_configured", "items": []}
    data = await _fireworks_get_json(request, "/v1/accounts")
    return {"supported": True, **data, "items": data["payload"].get("data") or data["payload"].get("accounts") or []}


@router.get("/fireworks/accounts/{account_id}")
async def get_fireworks_account(request: Request, account_id: str):
    ctx = _fireworks_context(request)
    if not ctx.api_key:
        return {"supported": False, "reason": "not_configured", "item": None}
    data = await _fireworks_get_json(request, f"/v1/accounts/{_normalize_fireworks_account_id(account_id)}")
    return {"supported": True, **data, "item": data["payload"]}


@router.get("/fireworks/quotas")
async def list_fireworks_quotas(request: Request, account_id: str | None = None):
    if not account_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="account_id is required")
    ctx = _fireworks_context(request)
    if not ctx.api_key:
        return {"supported": False, "reason": "not_configured", "items": []}
    data = await _fireworks_get_json(request, f"/v1/accounts/{_normalize_fireworks_account_id(account_id)}/quotas")
    return {"supported": True, **data, "items": _fireworks_quota_items(data["payload"]) }


@router.get("/fireworks/keys/quota-summaries")
async def list_fireworks_key_quota_summaries(request: Request, refresh: str = "auto"):
    repository = _repository(request)
    settings = _settings(request)
    refresh_mode = (refresh or "auto").casefold()
    if refresh_mode not in {"none", "auto", "force"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="refresh must be none, auto, or force")
    keys = repository.list_keys(include_disabled=True)
    list_snapshots = getattr(repository, "list_fireworks_key_snapshots", lambda: [])
    snapshots = {_snapshot_value(snapshot, "key_fingerprint"): snapshot for snapshot in list_snapshots()}

    def _build_response(*, supported: bool = True, reason: str | None = None, refresh_in_progress: bool = False) -> dict[str, Any]:
        items = []
        for key in keys:
            fingerprint = getattr(key, "fingerprint", None) or fingerprint_secret(getattr(key, "api_key", ""))
            snapshot = snapshots.get(fingerprint)
            stale = _snapshot_is_stale(snapshot)
            items.append(_snapshot_response_item(key, snapshot, source="snapshot" if snapshot else "missing", stale=stale))
        payload = {
            "supported": supported,
            "items": items,
            "pool_summary": _fireworks_key_pool_summary(items),
            **_refresh_state_payload(),
            "refresh_in_progress": refresh_in_progress or bool(_refresh_state.get("refresh_in_progress")),
        }
        if reason:
            payload["reason"] = reason
        return payload

    if refresh_mode == "none":
        return _build_response()

    has_refreshable_stored_key = any(
        getattr(key, "api_key", None) and (refresh_mode == "force" or getattr(key, "enabled", True))
        for key in keys
    )
    if not has_refreshable_stored_key and not _fireworks_context(request).api_key:
        return _build_response(supported=False, reason="not_configured")

    refresh_lock = _get_quota_refresh_lock()
    if refresh_mode == "auto" and refresh_lock.locked():
        return _build_response(refresh_in_progress=True)

    semaphore = asyncio.Semaphore(_quota_refresh_concurrency(settings))

    async def _maybe_refresh(key, refresh_quota: bool):
        async with semaphore:
            return await _refresh_fireworks_key_snapshot(request, key, refresh_quota=refresh_quota)

    async with refresh_lock:
        _set_global_refresh_started()
        refresh_tasks = []
        try:
            for key in keys:
                fingerprint = getattr(key, "fingerprint", None) or fingerprint_secret(getattr(key, "api_key", ""))
                snapshot = snapshots.get(fingerprint)
                needs_quota_refresh = _snapshot_needs_quota_refresh(snapshot)
                should_refresh = refresh_mode == "force" or (refresh_mode == "auto" and getattr(key, "enabled", True) and needs_quota_refresh)
                if should_refresh:
                    refresh_tasks.append((fingerprint, asyncio.create_task(_maybe_refresh(key, refresh_mode == "force" or needs_quota_refresh))))
            refresh_success = True
            for fingerprint, task in refresh_tasks:
                try:
                    snapshots[fingerprint] = await task
                except Exception:
                    refresh_success = False
                    raise
            next_refresh_after = min(
                (str(_snapshot_value(snapshot, "next_refresh_after") or _snapshot_value(snapshot, "stale_after")) for snapshot in snapshots.values() if _snapshot_value(snapshot, "next_refresh_after") or _snapshot_value(snapshot, "stale_after")),
                default=None,
            )
            _set_global_refresh_finished(success=refresh_success, next_refresh_after=next_refresh_after)
        except Exception:
            _set_global_refresh_finished(success=False)
            raise

    keys = repository.list_keys(include_disabled=True)
    return _build_response()


async def run_quota_refresh_once(app, *, refresh: str = "auto") -> dict[str, Any]:
    request_like = SimpleNamespace(app=app)
    return await list_fireworks_key_quota_summaries(request_like, refresh=refresh)


async def _quota_background_refresh_loop(app) -> None:
    settings = app.state.settings
    interval = max(1, int(getattr(settings, "fireworks_quota_refresh_interval_seconds", 900) or 900))
    jitter = max(0, int(getattr(settings, "fireworks_quota_refresh_jitter_seconds", 120) or 0))

    async def _sleep_until_next() -> None:
        delay = interval + (random.randint(0, jitter) if jitter else 0)
        _refresh_state["next_refresh_after"] = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
        await asyncio.sleep(delay)

    if bool(getattr(settings, "fireworks_quota_refresh_on_startup", True)):
        try:
            await run_quota_refresh_once(app, refresh="auto")
        except Exception:
            logger.exception("background Fireworks quota refresh on startup failed")

    while True:
        await _sleep_until_next()
        try:
            await run_quota_refresh_once(app, refresh="auto")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("background Fireworks quota refresh failed")


def configure_quota_background_refresh(app) -> None:
    settings = app.state.settings
    if not bool(getattr(settings, "fireworks_quota_background_refresh_enabled", False)):
        return

    @app.on_event("startup")
    async def _start_quota_background_refresh() -> None:
        task = getattr(app.state, "quota_refresh_task", None)
        if task and not task.done():
            return
        app.state.quota_refresh_task = asyncio.create_task(_quota_background_refresh_loop(app))

    @app.on_event("shutdown")
    async def _stop_quota_background_refresh() -> None:
        task = getattr(app.state, "quota_refresh_task", None)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@router.get("/fireworks/models")
async def list_fireworks_models(request: Request, account_id: str | None = None, source: str | None = None):
    source_value = (source or ("account" if account_id else "official")).casefold()
    repository = _repository(request)
    existing = repository.list_models()
    existing_aliases = {model.alias for model in existing}
    existing_upstreams = {model.upstream_model for model in existing}
    if source_value == "official":
        items = build_official_model_catalog(existing_aliases, existing_upstreams)
        return {"supported": True, "source": "official_registry", "source_type": "official_registry", "count": len(items), "items": items}

    ctx = _fireworks_context(request)
    if not ctx.api_key:
        return {"supported": False, "reason": "not_configured", "items": []}

    if source_value == "account":
        if not account_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="account_id is required")
        data = await _fireworks_get_json(request, f"/v1/accounts/{account_id}/models")
        raw_items = data["payload"].get("data") or data["payload"].get("models") or []
        source_type = "account"
    elif source_value == "inference":
        settings = _settings(request)
        async with FireworksClient(settings, ctx.api_key) as client:
            response = await client.get_json("/models")
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}
        data = {"source": ctx.source, "status_code": response.status_code, "payload": payload}
        raw_items = payload.get("data") or payload.get("models") or []
        source_type = "inference"
        for item in raw_items:
            if isinstance(item, dict) and "supports_serverless" not in item and "supportsServerless" not in item:
                item["supports_serverless"] = True
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown source")
    items = [build_model_catalog_item(item, existing_aliases, existing_upstreams) for item in raw_items]
    return {"supported": True, **data, "source_type": source_type, "count": len(items), "items": items}


@router.get("/fireworks/routers")
async def list_fireworks_routers(request: Request):
    ctx = _fireworks_context(request)
    return {"supported": False, "reason": "endpoint_not_confirmed", "source": ctx.source, "items": []}
