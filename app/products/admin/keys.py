from __future__ import annotations

from typing import Any
import sqlite3
import json
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import APIRouter, HTTPException, Request, status

from app.control.repository import AppRepository
from app.dataplane.fireworks.client import FireworksClient
from app.dataplane.fireworks.management import FireworksManagementClient
from app.platform.redaction import fingerprint_secret

from .deps import _key_payload, _repository, _settings
from .schemas import KeyCreate, KeyPatch, KeysBulkCreate

router = APIRouter()


@router.get("/keys")
async def list_keys(request: Request):
    repository = _repository(request)
    items = [_key_payload(repository, record) for record in repository.list_keys()]
    return {"items": items, "count": len(items)}


@router.post("/keys", status_code=status.HTTP_201_CREATED)
async def create_key(request: Request, payload: KeyCreate):
    repository = _repository(request)
    name = payload.name or _auto_key_name(payload.api_key)
    name = _unique_key_name(repository, name)
    if repository.get_key(name):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="key already exists")
    validation = None
    enrichment = None
    enabled = payload.enabled
    if payload.validate_with_fireworks:
        settings = _settings(request)
        validation, enrichment = await _probe_fireworks_key(settings, payload.api_key)
        if not validation["valid"]:
            enabled = False
    try:
        repository.upsert_key(name=name, api_key=payload.api_key, enabled=enabled)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="key fingerprint already exists") from exc
    record = repository.get_key(name)
    if not record:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="failed to create key")
    item = _key_payload(repository, record)
    if validation is not None:
        item["validation"] = validation
    if enrichment is not None:
        item["enrichment"] = enrichment
    if enrichment and item.get("enabled", enabled):
        repository.upsert_fireworks_key_snapshot({
            "key_fingerprint": record.fingerprint,
            "account_id": enrichment.get("account_id"),
            "account_label": enrichment.get("account_label"),
            "account_state": enrichment.get("account_state"),
            "suspend_state": enrichment.get("suspend_state"),
            "quota_supported": None,
            "quota_status": "unavailable",
            "quota_status_code": None,
            "quota_summary_json": json.dumps({"count": 0}),
            "quota_items_json": json.dumps([]),
            "account_refreshed_at": datetime.now(UTC).isoformat(),
            "quota_refreshed_at": None,
            "stale_after": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
            "refresh_status": "partial",
            "last_refresh_error_type": None,
            "last_refresh_error": None,
        })
    return item


@router.post("/keys/bulk", status_code=status.HTTP_201_CREATED)
async def bulk_create_keys(request: Request, payload: KeysBulkCreate):
    repository = _repository(request)
    settings = _settings(request)
    seen_input: set[str] = set()
    results = []
    created = 0
    duplicates = 0
    invalid = 0
    for raw in payload.api_keys:
        api_key = raw.strip()
        if not api_key:
            continue
        fingerprint = fingerprint_secret(api_key)
        if fingerprint in seen_input:
            duplicates += 1
            results.append({"status": "duplicate", "fingerprint": fingerprint, "reason": "duplicate_in_request"})
            continue
        seen_input.add(fingerprint)

        if any(record.fingerprint == fingerprint for record in repository.list_keys()):
            duplicates += 1
            results.append({"status": "duplicate", "fingerprint": fingerprint, "reason": "already_exists"})
            continue

        validation = None
        enrichment = None
        enabled = payload.enabled
        if payload.validate_with_fireworks:
            validation, enrichment = await _probe_fireworks_key(settings, api_key)
            if not validation["valid"]:
                invalid += 1
                enabled = False

        name = _unique_key_name(repository, _auto_key_name(api_key))
        try:
            repository.upsert_key(name=name, api_key=api_key, enabled=enabled)
        except sqlite3.IntegrityError:
            duplicates += 1
            results.append({"status": "duplicate", "fingerprint": fingerprint, "reason": "already_exists"})
            continue
        record = repository.get_key(name)
        if not record:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="failed to create key")
        created += 1
        item = {"status": "created", "key": _key_payload(repository, record)}
        if validation is not None:
            item["validation"] = validation
        if enrichment is not None:
            item["enrichment"] = enrichment
        if enrichment and item["key"]["enabled"]:
            repository.upsert_fireworks_key_snapshot({
                "key_fingerprint": record.fingerprint,
                "account_id": enrichment.get("account_id"),
                "account_label": enrichment.get("account_label"),
                "account_state": enrichment.get("account_state"),
                "suspend_state": enrichment.get("suspend_state"),
                "quota_supported": None,
                "quota_status": "unavailable",
                "quota_status_code": None,
                "quota_summary_json": json.dumps({"count": 0}),
                "quota_items_json": json.dumps([]),
                "account_refreshed_at": datetime.now(UTC).isoformat(),
                "quota_refreshed_at": None,
                "stale_after": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
                "refresh_status": "partial",
                "last_refresh_error_type": None,
                "last_refresh_error": None,
            })
        results.append(item)

    return {"created": created, "duplicates": duplicates, "invalid": invalid, "items": results}


def _auto_key_name(api_key: str) -> str:
    return f"fw-{fingerprint_secret(api_key, 8)}"


def _unique_key_name(repository, base_name: str) -> str:
    if not repository.get_key(base_name):
        return base_name
    idx = 2
    while repository.get_key(f"{base_name}-{idx}"):
        idx += 1
    return f"{base_name}-{idx}"


def _is_locally_malformed_fireworks_key(api_key: str) -> bool:
    return AppRepository.is_locally_malformed_fireworks_key(api_key)


async def _probe_fireworks_key(settings, api_key: str) -> tuple[dict[str, object], dict[str, object] | None]:
    try:
        async with FireworksManagementClient(settings, api_key) as client:
            response = await client.get_json("/v1/accounts")
    except (httpx.HTTPError, TimeoutError, RuntimeError):
        try:
            async with FireworksClient(settings, api_key) as client:
                response = await client.get_json("/models")
        except (httpx.HTTPError, TimeoutError, RuntimeError) as fallback_exc:
            return {"valid": False, "status_code": None, "error": fallback_exc.__class__.__name__}, None
    if response.status_code == 200:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        accounts = payload.get("data") or payload.get("accounts") or []
        account = accounts[0] if accounts and isinstance(accounts[0], dict) else {}
        account_id = str(account.get("id") or account.get("name") or response.headers.get("x-fireworks-account-id") or "")
        enrichment: dict[str, Any] = {}
        if account_id:
            enrichment["account_id"] = account_id.removeprefix("accounts/")
        if account.get("label") or account.get("name") or account.get("id"):
            enrichment["account_label"] = account.get("label") or account.get("name") or account.get("id")
        if account.get("state") or account.get("status"):
            enrichment["account_state"] = account.get("state") or account.get("status")
        if account.get("suspend_state") or account.get("suspended"):
            enrichment["suspend_state"] = account.get("suspend_state") or account.get("suspended")
        return {"valid": True, "status_code": response.status_code}, enrichment or None
    return {"valid": False, "status_code": response.status_code}, None


@router.patch("/keys/{name}")
async def patch_key(request: Request, name: str, payload: KeyPatch):
    repository = _repository(request)
    record = repository.get_key(name)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key not found")
    updated = record
    if payload.name is not None or payload.api_key is not None:
        try:
            updated = repository.update_key_identity(name, name=payload.name, api_key=payload.api_key) or updated
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="key name or fingerprint already exists") from exc
    if payload.enabled is not None:
        repository.set_key_enabled(updated.name, payload.enabled, None if payload.enabled else "admin_disabled")
        refreshed = repository.get_key(updated.name)
        if refreshed:
            updated = refreshed
    return _key_payload(repository, updated)


@router.post("/keys/{name}/enable")
async def enable_key(request: Request, name: str):
    repository = _repository(request)
    record = repository.get_key(name)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key not found")
    repository.set_key_enabled(name, True, None)
    refreshed = repository.get_key(name) or record
    return _key_payload(repository, refreshed)


@router.post("/keys/{name}/disable")
async def disable_key(request: Request, name: str):
    repository = _repository(request)
    record = repository.get_key(name)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key not found")
    repository.set_key_enabled(name, False, "admin_disabled")
    refreshed = repository.get_key(name) or record
    return _key_payload(repository, refreshed)


@router.post("/keys/{name}/clear-cooldown")
async def clear_key_cooldown(request: Request, name: str):
    repository = _repository(request)
    record = repository.get_key(name)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key not found")
    repository.clear_key_cooldown(name)
    refreshed = repository.get_key(name) or record
    return _key_payload(repository, refreshed)


@router.delete("/keys/{name}")
async def delete_key(request: Request, name: str):
    repository = _repository(request)
    if not repository.get_key(name):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key not found")
    repository.delete_key(name)
    return {"deleted": True, "name": name}


@router.post("/keys/cleanup-invalid")
async def cleanup_invalid_keys(request: Request):
    repository = _repository(request)
    settings = _settings(request)
    items: list[dict[str, Any]] = []
    deleted = 0
    kept = 0

    # 额度已用尽的账号集合（来自账号级额度快照）+ key 指纹到 account_id 的映射。
    # account_id 统一去掉 "accounts/" 前缀，以便与额度快照表（已 normalize）对齐。
    exhausted_accounts: set[str] = {
        snap.account_id
        for snap in repository.list_fireworks_account_quota_snapshots()
        if snap.quota_status == "quota_exhausted"
    }
    fingerprint_to_account: dict[str, str] = {}
    for snap in repository.list_fireworks_key_snapshots():
        account_id = (snap.account_id or "").strip()
        if account_id.startswith("accounts/"):
            account_id = account_id[len("accounts/") :]
        account_id = account_id.strip()
        if account_id:
            fingerprint_to_account[snap.key_fingerprint] = account_id

    def _is_quota_exhausted_key(record) -> bool:
        account_id = fingerprint_to_account.get(record.fingerprint)
        if account_id and account_id in exhausted_accounts:
            return True
        # 兜底：本地禁用标记为额度用尽（即便额度快照缺失或状态不一致）。
        # 注意排除 admin_disabled 等人为禁用的情况。
        return (
            record.enabled is False
            and record.disabled_reason == "upstream_account_unavailable"
            and record.last_error_type == "quota_exhausted"
        )

    cleaned_accounts: set[str] = set()

    for record in repository.list_keys(include_disabled=True):
        if _is_locally_malformed_fireworks_key(record.api_key):
            repository.delete_key(record.name)
            deleted += 1
            items.append({
                "name": record.name,
                "masked_key": _key_payload(repository, record)["masked_key"],
                "fingerprint": record.fingerprint,
                "status": "deleted",
                "reason": "malformed_key",
                "status_code": None,
            })
            continue
        if _is_quota_exhausted_key(record):
            account_id = fingerprint_to_account.get(record.fingerprint)
            if account_id:
                cleaned_accounts.add(account_id)
            repository.delete_key(record.name)
            deleted += 1
            items.append({
                "name": record.name,
                "masked_key": _key_payload(repository, record)["masked_key"],
                "fingerprint": record.fingerprint,
                "status": "deleted",
                "reason": "quota_exhausted",
                "status_code": None,
                "account_id": account_id,
            })
            continue
        try:
            validation, _ = await _probe_fireworks_key(settings, record.api_key)
            status_code = validation.get("status_code")
        except Exception as exc:
            status_code = None
            items.append({
                "name": record.name,
                "masked_key": _key_payload(repository, record)["masked_key"],
                "fingerprint": record.fingerprint,
                "status": "kept",
                "reason": exc.__class__.__name__,
                "status_code": None,
            })
            kept += 1
            continue

        if status_code in {401, 403}:
            repository.delete_key(record.name)
            deleted += 1
            items.append({
                "name": record.name,
                "masked_key": _key_payload(repository, record)["masked_key"],
                "fingerprint": record.fingerprint,
                "status": "deleted",
                "reason": "invalid_credentials",
                "status_code": status_code,
            })
        else:
            kept += 1
            reason = "ok" if status_code == 200 else "non_invalid"
            items.append({
                "name": record.name,
                "masked_key": _key_payload(repository, record)["masked_key"],
                "fingerprint": record.fingerprint,
                "status": "kept",
                "reason": reason,
                "status_code": status_code,
            })

    # 清理孤儿：删光某额度用尽账号下所有 key 后，移除其额度快照与冷却记录。
    for account_id in cleaned_accounts:
        if not repository.keys_for_account(account_id):
            repository.delete_fireworks_account_quota_snapshot(account_id)
            repository.clear_account_cooldown(account_id)

    return {"checked": len(items), "deleted": deleted, "kept": kept, "items": items}
