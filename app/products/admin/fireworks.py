from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
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

router = APIRouter()


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
        lowered = {str(key).replace("-", "_").casefold(): value for key, value in item.items()}
        quota_name = str(item.get("name") or item.get("id") or item.get("quotaId") or "").casefold()
        value = _quota_number(item.get("value"))
        usage = _quota_number(item.get("usage"))
        max_value = _quota_number(item.get("maxValue") or item.get("max_value"))
        if quota_name.endswith("monthly-spend-usd"):
            budget = value if value is not None else max_value
            summary["monthly_budget"] = budget
            summary["monthly_used"] = usage
            if budget is not None and usage is not None:
                summary["monthly_remaining"] = max(0, float(budget) - float(usage))
        elif quota_name.endswith("serverless-inference-rpm"):
            summary["serverless_rpm_limit"] = value if value is not None else max_value
            summary["serverless_rpm_usage"] = usage
    return summary


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


def _response_error_text(response) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text:
        return text
    try:
        return json.dumps(response.json(), ensure_ascii=False)
    except Exception:
        return ""


def _apply_management_error(payload: dict[str, Any], response, *, scope: str) -> None:
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
    elif decision.error_type == "auth_error":
        payload["quota_status"] = "auth_error"
    else:
        payload["quota_status"] = "unavailable"
    payload["quota_refreshed_at"] = datetime.now(UTC).isoformat()
    payload["stale_after"] = (datetime.now(UTC) + timedelta(seconds=QUOTA_TTL_SECONDS)).isoformat()


def _disable_keys_for_snapshot_error(repository, key, payload: dict[str, Any]) -> None:
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
    quota_summary = json.loads(quota_summary_json) if quota_summary_json else {"count": 0}
    quota_items = json.loads(quota_items_json) if quota_items_json else []
    return {
        "key_name": key.name,
        "masked_key": redact_secret(key.api_key, visible=6),
        "account_id": _snapshot_value(snapshot, "account_id"),
        "account_label": _snapshot_value(snapshot, "account_label"),
        "account_state": _snapshot_value(snapshot, "account_state"),
        "suspend_state": _snapshot_value(snapshot, "suspend_state"),
        "quota_supported": bool(_snapshot_value(snapshot, "quota_supported")) if _snapshot_value(snapshot, "quota_supported") is not None else False,
        "quota_status_code": _snapshot_value(snapshot, "quota_status_code"),
        "quota_status": _snapshot_value(snapshot, "quota_status", "unavailable"),
        "quota_items": quota_items,
        "quota_summary": quota_summary,
        "source": source,
        "stale": stale,
        "last_refreshed_at": _snapshot_value(snapshot, "quota_refreshed_at"),
        "stale_after": _snapshot_value(snapshot, "stale_after"),
        "refresh_status": _snapshot_value(snapshot, "refresh_status"),
        "last_refresh_error_type": _snapshot_value(snapshot, "last_refresh_error_type"),
        "last_refresh_error": _snapshot_value(snapshot, "last_refresh_error"),
        "error": _snapshot_value(snapshot, "last_refresh_error") if _snapshot_value(snapshot, "last_refresh_error") else None,
    }


async def _refresh_fireworks_key_snapshot(request: Request, key, *, refresh_quota: bool) -> dict[str, Any]:
    settings = _settings(request)
    repository = _repository(request)
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
    }
    try:
        async with FireworksManagementClient(settings, key.api_key) as client:
            account_response = await client.get_json("/v1/accounts")
            if account_response.status_code >= 400:
                _apply_management_error(payload, account_response, scope="account")
                account_id = _snapshot_value(getattr(repository, "get_fireworks_key_snapshot", lambda _fingerprint: None)(payload["key_fingerprint"]), "account_id")
                if account_id:
                    payload["account_id"] = account_id
                upsert_snapshot = getattr(repository, "upsert_fireworks_key_snapshot", None)
                if callable(upsert_snapshot):
                    upsert_snapshot(payload)
                _disable_keys_for_snapshot_error(repository, key, payload)
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
                quota_response = await client.get_json(f"/v1/accounts/{account_id}/quotas")
                if quota_response.status_code >= 400:
                    _apply_management_error(payload, quota_response, scope="quota")
                    upsert_snapshot = getattr(repository, "upsert_fireworks_key_snapshot", None)
                    if callable(upsert_snapshot):
                        upsert_snapshot(payload)
                    _disable_keys_for_snapshot_error(repository, key, payload)
                    return payload
                payload["quota_supported"] = quota_response.status_code == 200
                payload["quota_status_code"] = quota_response.status_code
                payload["quota_status"] = "ok" if quota_response.status_code == 200 else "unavailable"
                quota_payload = quota_response.json()
                quota_items = _fireworks_quota_items(quota_payload if isinstance(quota_payload, dict) else {})
                payload["quota_items_json"] = json.dumps(quota_items)
                payload["quota_summary_json"] = json.dumps(_quota_summary(quota_items), sort_keys=True)
                payload["quota_refreshed_at"] = datetime.now(UTC).isoformat()
                payload["stale_after"] = (datetime.now(UTC) + timedelta(seconds=QUOTA_TTL_SECONDS)).isoformat()
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
            })
        if already_classified:
            payload["refresh_status"] = "error"
        else:
            decision = classify_fireworks_error(body=str(exc))
            if decision.error_type in {"quota_exhausted", "rate_limit", "auth_error"}:
                payload["quota_status"] = decision.error_type
                payload["quota_supported"] = False
                payload["quota_refreshed_at"] = datetime.now(UTC).isoformat()
                payload["stale_after"] = (datetime.now(UTC) + timedelta(seconds=QUOTA_TTL_SECONDS)).isoformat()
        payload["refresh_status"] = "error"
        payload["last_refresh_error_type"] = payload.get("last_refresh_error_type") or exc.__class__.__name__
        payload["last_refresh_error"] = payload.get("last_refresh_error") or str(exc)
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
    ctx = _fireworks_context(request)
    if not ctx.api_key:
        return {"supported": False, "reason": "not_configured", "items": []}
    repository = _repository(request)
    refresh_mode = (refresh or "auto").casefold()
    if refresh_mode not in {"none", "auto", "force"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="refresh must be none, auto, or force")
    keys = repository.list_keys(include_disabled=True)
    list_snapshots = getattr(repository, "list_fireworks_key_snapshots", lambda: [])
    snapshots = {_snapshot_value(snapshot, "key_fingerprint"): snapshot for snapshot in list_snapshots()}
    semaphore = asyncio.Semaphore(4)

    async def _maybe_refresh(key, refresh_quota: bool):
        async with semaphore:
            return await _refresh_fireworks_key_snapshot(request, key, refresh_quota=refresh_quota)

    refresh_tasks = []
    for key in keys:
        fingerprint = getattr(key, "fingerprint", None) or fingerprint_secret(getattr(key, "api_key", ""))
        snapshot = snapshots.get(fingerprint)
        needs_quota_refresh = _snapshot_needs_quota_refresh(snapshot)
        should_refresh = refresh_mode == "force" or (refresh_mode == "auto" and getattr(key, "enabled", True) and needs_quota_refresh)
        if should_refresh:
            refresh_tasks.append((fingerprint, asyncio.create_task(_maybe_refresh(key, refresh_mode == "force" or needs_quota_refresh))))
    if refresh_tasks:
        for fingerprint, task in refresh_tasks:
            snapshots[fingerprint] = await task

    items = []
    for key in keys:
        fingerprint = getattr(key, "fingerprint", None) or fingerprint_secret(getattr(key, "api_key", ""))
        snapshot = snapshots.get(fingerprint)
        stale = _snapshot_is_stale(snapshot)
        items.append(_snapshot_response_item(key, snapshot, source="snapshot" if snapshot else "missing", stale=stale))
    return {"supported": True, "items": items}


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
