from __future__ import annotations

import csv
import io
import json
import uuid
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.platform.config import Settings
from app.platform.redaction import fingerprint_secret
from app.platform.redaction import redact_secret
from app.platform.storage.db import connect


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class KeyRecord:
    name: str
    api_key: str
    fingerprint: str
    enabled: bool
    cooldown_until: str | None = None
    disabled_reason: str | None = None
    last_error_type: str | None = None
    last_error_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class ModelMapping:
    alias: str
    upstream_model: str
    enabled: bool = True


@dataclass(frozen=True)
class FireworksKeySnapshot:
    key_fingerprint: str
    account_id: str | None = None
    account_label: str | None = None
    account_state: str | None = None
    suspend_state: str | None = None
    quota_supported: bool | None = None
    quota_status: str | None = None
    quota_status_code: int | None = None
    quota_summary_json: str | None = None
    quota_items_json: str | None = None
    account_refreshed_at: str | None = None
    quota_refreshed_at: str | None = None
    stale_after: str | None = None
    refresh_status: str | None = None
    last_refresh_error_type: str | None = None
    last_refresh_error: str | None = None
    refresh_started_at: str | None = None
    last_successful_refresh_at: str | None = None
    consecutive_refresh_failures: int | None = None
    next_refresh_after: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class FireworksAccountQuotaSnapshot:
    account_id: str
    quota_supported: bool | None = None
    quota_status: str | None = None
    quota_status_code: int | None = None
    quota_summary_json: str | None = None
    quota_items_json: str | None = None
    quota_refreshed_at: str | None = None
    stale_after: str | None = None
    refresh_status: str | None = None
    last_refresh_error_type: str | None = None
    last_refresh_error: str | None = None
    refresh_started_at: str | None = None
    last_successful_refresh_at: str | None = None
    consecutive_refresh_failures: int = 0
    next_refresh_after: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class AccountCooldownRecord:
    account_id: str
    cooldown_until: str | None = None
    last_error_type: str | None = None
    last_error_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class ResponseSessionBindingRecord:
    scope: str
    model: str
    session_hash: str
    response_id: str
    key_name: str | None = None
    key_fingerprint: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


DEFAULT_MODELS = [
    ModelMapping("kimi-k2.6", "accounts/fireworks/models/kimi-k2p6"),
    ModelMapping("kimi-k2.6-turbo", "accounts/fireworks/routers/kimi-k2p6-turbo"),
    ModelMapping("glm-5.1", "accounts/fireworks/models/glm-5p1"),
    ModelMapping("glm-5.1-fast", "accounts/fireworks/routers/glm-5p1-fast"),
    ModelMapping("deepseek-v4-pro", "accounts/fireworks/models/deepseek-v4-pro"),
    ModelMapping("deepseek-v4-flash", "accounts/fireworks/models/deepseek-v4-flash"),
    ModelMapping("MiniMax-M2.7", "accounts/fireworks/models/minimax-m2p7"),
]


class AppRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    @staticmethod
    def _normalize_account_id(account_id: str) -> str:
        normalized = str(account_id or "").strip()
        if normalized.startswith("accounts/"):
            normalized = normalized[len("accounts/") :]
        normalized = normalized.strip()
        if not normalized:
            raise ValueError("account_id must not be empty")
        return normalized

    def bootstrap_from_env(self, settings: Settings) -> None:
        if self.list_keys() and not settings.sync_env_keys_on_startup:
            return
        named_keys: list[tuple[str, str]] = []
        named_keys.extend((f"fw-{idx}", key) for idx, key in enumerate(settings.fireworks_api_keys, start=1))
        named_keys.extend((item["name"], item["key"]) for item in settings.fireworks_api_keys_json)
        for name, key in named_keys:
            self.upsert_key(name=name, api_key=key, enabled=True)

    @staticmethod
    def is_locally_malformed_fireworks_key(api_key: str) -> bool:
        stripped = str(api_key or "").strip()
        return not stripped.startswith("fw_") or len(stripped) < 16

    def list_settings(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM settings ORDER BY key").fetchall()
        return [{"key": row["key"], "value": json.loads(row["value_json"]), "updated_at": row["updated_at"]} for row in rows]

    def get_setting(self, key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM settings WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        return {"key": row["key"], "value": json.loads(row["value_json"]), "updated_at": row["updated_at"]}

    def upsert_setting(self, key: str, value: Any) -> dict[str, Any]:
        ts = now_iso()
        value_json = json.dumps(value, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO settings(key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                  value_json=excluded.value_json,
                  updated_at=excluded.updated_at
                """,
                (key, value_json, ts),
            )
        return {"key": key, "value": value, "updated_at": ts}

    def bootstrap_default_models(self) -> None:
        if self.list_models():
            return
        for model in DEFAULT_MODELS:
            self.upsert_model(model)

    def upsert_key(self, name: str, api_key: str, enabled: bool = True) -> None:
        ts = now_iso()
        fingerprint = fingerprint_secret(api_key)
        with self._connect() as conn:
            old = conn.execute("SELECT fingerprint FROM keys WHERE name=?", (name,)).fetchone()
            conn.execute(
                """
                INSERT INTO keys(name, api_key_ciphertext, fingerprint, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                  api_key_ciphertext=excluded.api_key_ciphertext,
                  fingerprint=excluded.fingerprint,
                  enabled=excluded.enabled,
                  updated_at=excluded.updated_at
                """,
                (name, api_key, fingerprint, int(enabled), ts, ts),
            )
            if old and old["fingerprint"] != fingerprint:
                conn.execute("DELETE FROM fireworks_key_snapshots WHERE key_fingerprint=?", (old["fingerprint"],))
                conn.execute("DELETE FROM response_key_routes WHERE key_fingerprint=?", (old["fingerprint"],))

    def list_keys(self, include_disabled: bool = True) -> list[KeyRecord]:
        query = (
            "SELECT * FROM keys ORDER BY name"
            if include_disabled
            else "SELECT * FROM keys WHERE enabled=1 ORDER BY name"
        )
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        return [
            KeyRecord(
                name=row["name"],
                api_key=row["api_key_ciphertext"],
                fingerprint=row["fingerprint"],
                enabled=bool(row["enabled"]),
                cooldown_until=row["cooldown_until"],
                disabled_reason=row["disabled_reason"],
                last_error_type=row["last_error_type"],
                last_error_at=row["last_error_at"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def get_key(self, name: str) -> KeyRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM keys WHERE name=?", (name,)).fetchone()
        if not row:
            return None
        return KeyRecord(
            name=row["name"],
            api_key=row["api_key_ciphertext"],
            fingerprint=row["fingerprint"],
            enabled=bool(row["enabled"]),
            cooldown_until=row["cooldown_until"],
            disabled_reason=row["disabled_reason"],
            last_error_type=row["last_error_type"],
            last_error_at=row["last_error_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def set_key_enabled(
        self,
        name: str,
        enabled: bool,
        reason: str | None = None,
        last_error_type: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE keys
                SET enabled=?, disabled_reason=?, last_error_type=?, last_error_at=?, updated_at=?
                WHERE name=?
                """,
                (
                    int(enabled),
                    None if enabled else reason,
                    last_error_type,
                    now_iso() if last_error_type else None,
                    now_iso(),
                    name,
                ),
            )

    def delete_key(self, name: str) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT fingerprint FROM keys WHERE name=?", (name,)).fetchone()
            conn.execute("DELETE FROM keys WHERE name=?", (name,))
            if row:
                conn.execute("DELETE FROM fireworks_key_snapshots WHERE key_fingerprint=?", (row["fingerprint"],))
                conn.execute("DELETE FROM response_key_routes WHERE key_fingerprint=?", (row["fingerprint"],))

    def update_key_identity(
        self,
        current_name: str,
        *,
        name: str | None = None,
        api_key: str | None = None,
    ) -> KeyRecord | None:
        key = self.get_key(current_name)
        if not key:
            return None

        next_name = name or key.name
        next_api_key = api_key or key.api_key
        with self._connect() as conn:
            old = conn.execute("SELECT fingerprint FROM keys WHERE name=?", (current_name,)).fetchone()
            conn.execute(
                """
                UPDATE keys
                SET name=?, api_key_ciphertext=?, fingerprint=?, updated_at=?
                WHERE name=?
                """,
                (next_name, next_api_key, fingerprint_secret(next_api_key), now_iso(), current_name),
            )
            if old and old["fingerprint"] != fingerprint_secret(next_api_key):
                conn.execute("DELETE FROM fireworks_key_snapshots WHERE key_fingerprint=?", (old["fingerprint"],))
                conn.execute("DELETE FROM response_key_routes WHERE key_fingerprint=?", (old["fingerprint"],))
        return self.get_key(next_name)

    def upsert_response_key_route(self, response_id: str, key: KeyRecord) -> None:
        response_id = str(response_id or "").strip()
        if not response_id:
            return
        ts = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO response_key_routes(response_id, key_name, key_fingerprint, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(response_id) DO UPDATE SET
                  key_name=excluded.key_name,
                  key_fingerprint=excluded.key_fingerprint,
                  updated_at=excluded.updated_at
                """,
                (response_id, key.name, key.fingerprint, ts, ts),
            )

    def get_response_key_route(self, response_id: str) -> KeyRecord | None:
        response_id = str(response_id or "").strip()
        if not response_id:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT key_name FROM response_key_routes WHERE response_id=?", (response_id,)).fetchone()
        if not row:
            return None
        return self.get_key(row["key_name"])

    def delete_response_key_route(self, response_id: str) -> None:
        response_id = str(response_id or "").strip()
        if not response_id:
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM response_key_routes WHERE response_id=?", (response_id,))

    def upsert_response_session_binding(
        self,
        scope: str,
        model: str,
        session_hash: str,
        response_id: str,
        key_name: str | None = None,
        key_fingerprint: str | None = None,
    ) -> None:
        scope = str(scope or "").strip()
        model = str(model or "").strip()
        session_hash = str(session_hash or "").strip()
        response_id = str(response_id or "").strip()
        if not scope or not model or not session_hash or not response_id:
            return
        ts = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO response_session_bindings(
                  scope, model, session_hash, response_id, key_name, key_fingerprint, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, model, session_hash) DO UPDATE SET
                  response_id=excluded.response_id,
                  key_name=excluded.key_name,
                  key_fingerprint=excluded.key_fingerprint,
                  updated_at=excluded.updated_at
                """,
                (scope, model, session_hash, response_id, key_name, key_fingerprint, ts, ts),
            )

    def get_response_session_binding(
        self,
        scope: str,
        model: str,
        session_hash: str,
    ) -> ResponseSessionBindingRecord | None:
        scope = str(scope or "").strip()
        model = str(model or "").strip()
        session_hash = str(session_hash or "").strip()
        if not scope or not model or not session_hash:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM response_session_bindings WHERE scope=? AND model=? AND session_hash=?",
                (scope, model, session_hash),
            ).fetchone()
        return ResponseSessionBindingRecord(**dict(row)) if row else None

    def delete_response_session_binding(self, scope: str, model: str, session_hash: str) -> None:
        scope = str(scope or "").strip()
        model = str(model or "").strip()
        session_hash = str(session_hash or "").strip()
        if not scope or not model or not session_hash:
            return
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM response_session_bindings WHERE scope=? AND model=? AND session_hash=?",
                (scope, model, session_hash),
            )

    @staticmethod
    def _key_snapshot_from_row(row: sqlite3.Row | None) -> FireworksKeySnapshot | None:
        if not row:
            return None
        payload = dict(row)
        if payload.get("quota_supported") is not None:
            payload["quota_supported"] = bool(payload["quota_supported"])
        if payload.get("consecutive_refresh_failures") is not None:
            payload["consecutive_refresh_failures"] = int(payload["consecutive_refresh_failures"])
        return FireworksKeySnapshot(**payload)

    @staticmethod
    def _account_quota_snapshot_from_row(row: sqlite3.Row | None) -> FireworksAccountQuotaSnapshot | None:
        if not row:
            return None
        payload = dict(row)
        if payload.get("quota_supported") is not None:
            payload["quota_supported"] = bool(payload["quota_supported"])
        if payload.get("consecutive_refresh_failures") is not None:
            payload["consecutive_refresh_failures"] = int(payload["consecutive_refresh_failures"])
        return FireworksAccountQuotaSnapshot(**payload)

    @staticmethod
    def _merged_key_snapshot_select(where_clause: str = "") -> str:
        return f"""
            SELECT
              k.key_fingerprint,
              k.account_id,
              k.account_label,
              k.account_state,
              k.suspend_state,
              COALESCE(a.quota_supported, k.quota_supported) AS quota_supported,
              COALESCE(a.quota_status, k.quota_status) AS quota_status,
              COALESCE(a.quota_status_code, k.quota_status_code) AS quota_status_code,
              COALESCE(a.quota_summary_json, k.quota_summary_json) AS quota_summary_json,
              COALESCE(a.quota_items_json, k.quota_items_json) AS quota_items_json,
              k.account_refreshed_at,
              COALESCE(a.quota_refreshed_at, k.quota_refreshed_at) AS quota_refreshed_at,
              COALESCE(a.stale_after, k.stale_after) AS stale_after,
              COALESCE(a.refresh_status, k.refresh_status) AS refresh_status,
              COALESCE(a.last_refresh_error_type, k.last_refresh_error_type) AS last_refresh_error_type,
              COALESCE(a.last_refresh_error, k.last_refresh_error) AS last_refresh_error,
              a.refresh_started_at,
              a.last_successful_refresh_at,
              a.consecutive_refresh_failures,
              a.next_refresh_after,
              COALESCE(a.updated_at, k.updated_at) AS updated_at
            FROM fireworks_key_snapshots k
            LEFT JOIN fireworks_account_quota_snapshots a
              ON a.account_id = CASE
                WHEN k.account_id LIKE 'accounts/%' THEN SUBSTR(k.account_id, 10)
                ELSE k.account_id
              END
            {where_clause}
        """

    def upsert_fireworks_key_snapshot(self, snapshot: dict[str, Any]) -> None:
        ts = now_iso()
        columns = {
            **snapshot,
            "updated_at": snapshot.get("updated_at") or ts,
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO fireworks_key_snapshots(
                  key_fingerprint, account_id, account_label, account_state, suspend_state,
                  quota_supported, quota_status, quota_status_code, quota_summary_json, quota_items_json,
                  account_refreshed_at, quota_refreshed_at, stale_after, refresh_status,
                  last_refresh_error_type, last_refresh_error, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key_fingerprint) DO UPDATE SET
                  account_id=excluded.account_id,
                  account_label=excluded.account_label,
                  account_state=excluded.account_state,
                  suspend_state=excluded.suspend_state,
                  quota_supported=excluded.quota_supported,
                  quota_status=excluded.quota_status,
                  quota_status_code=excluded.quota_status_code,
                  quota_summary_json=excluded.quota_summary_json,
                  quota_items_json=excluded.quota_items_json,
                  account_refreshed_at=excluded.account_refreshed_at,
                  quota_refreshed_at=excluded.quota_refreshed_at,
                  stale_after=excluded.stale_after,
                  refresh_status=excluded.refresh_status,
                  last_refresh_error_type=excluded.last_refresh_error_type,
                  last_refresh_error=excluded.last_refresh_error,
                  updated_at=excluded.updated_at
                """,
                (
                    columns.get("key_fingerprint"), columns.get("account_id"), columns.get("account_label"), columns.get("account_state"),
                    columns.get("suspend_state"), int(columns.get("quota_supported")) if columns.get("quota_supported") is not None else None,
                    columns.get("quota_status"), columns.get("quota_status_code"), columns.get("quota_summary_json"), columns.get("quota_items_json"),
                    columns.get("account_refreshed_at"), columns.get("quota_refreshed_at"), columns.get("stale_after"), columns.get("refresh_status"),
                    columns.get("last_refresh_error_type"), columns.get("last_refresh_error"), columns["updated_at"],
                ),
            )
        account_id = str(columns.get("account_id") or "").strip()
        if account_id:
            quota_fields = {
                name: columns.get(name)
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
                )
                if name in columns
            }
            if any(value is not None for value in quota_fields.values()):
                self.upsert_fireworks_account_quota_snapshot(account_id, quota_fields)

    def upsert_fireworks_account_quota_snapshot(self, account_id: str, snapshot: dict[str, Any]) -> None:
        account_id = self._normalize_account_id(account_id)
        ts = now_iso()
        refresh_status = snapshot.get("refresh_status")
        last_successful = snapshot.get("last_successful_refresh_at")
        if not last_successful and refresh_status == "ok":
            last_successful = snapshot.get("quota_refreshed_at")
        consecutive_failures = snapshot.get("consecutive_refresh_failures")
        if consecutive_failures is None:
            consecutive_failures = 0 if refresh_status == "ok" else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO fireworks_account_quota_snapshots(
                  account_id, quota_supported, quota_status, quota_status_code,
                  quota_summary_json, quota_items_json, quota_refreshed_at, stale_after,
                  refresh_status, last_refresh_error_type, last_refresh_error,
                  refresh_started_at, last_successful_refresh_at, consecutive_refresh_failures,
                  next_refresh_after, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 0), ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                  quota_supported=excluded.quota_supported,
                  quota_status=excluded.quota_status,
                  quota_status_code=excluded.quota_status_code,
                  quota_summary_json=excluded.quota_summary_json,
                  quota_items_json=excluded.quota_items_json,
                  quota_refreshed_at=excluded.quota_refreshed_at,
                  stale_after=excluded.stale_after,
                  refresh_status=excluded.refresh_status,
                  last_refresh_error_type=excluded.last_refresh_error_type,
                  last_refresh_error=excluded.last_refresh_error,
                  refresh_started_at=excluded.refresh_started_at,
                  last_successful_refresh_at=COALESCE(excluded.last_successful_refresh_at, fireworks_account_quota_snapshots.last_successful_refresh_at),
                  consecutive_refresh_failures=excluded.consecutive_refresh_failures,
                  next_refresh_after=excluded.next_refresh_after,
                  updated_at=excluded.updated_at
                """,
                (
                    account_id,
                    int(snapshot["quota_supported"]) if snapshot.get("quota_supported") is not None else None,
                    snapshot.get("quota_status"),
                    snapshot.get("quota_status_code"),
                    snapshot.get("quota_summary_json"),
                    snapshot.get("quota_items_json"),
                    snapshot.get("quota_refreshed_at"),
                    snapshot.get("stale_after"),
                    refresh_status,
                    snapshot.get("last_refresh_error_type"),
                    snapshot.get("last_refresh_error"),
                    snapshot.get("refresh_started_at"),
                    last_successful,
                    consecutive_failures,
                    snapshot.get("next_refresh_after"),
                    snapshot.get("updated_at") or ts,
                ),
            )

    def get_fireworks_key_snapshot(self, fingerprint: str) -> FireworksKeySnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                self._merged_key_snapshot_select("WHERE k.key_fingerprint=?"),
                (fingerprint,),
            ).fetchone()
        return self._key_snapshot_from_row(row)

    def list_fireworks_key_snapshots(self) -> list[FireworksKeySnapshot]:
        with self._connect() as conn:
            rows = conn.execute(
                self._merged_key_snapshot_select("ORDER BY k.key_fingerprint")
            ).fetchall()
        return [snapshot for row in rows if (snapshot := self._key_snapshot_from_row(row)) is not None]

    def get_fireworks_account_quota_snapshot(self, account_id: str) -> FireworksAccountQuotaSnapshot | None:
        account_id = self._normalize_account_id(account_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM fireworks_account_quota_snapshots WHERE account_id=?",
                (account_id,),
            ).fetchone()
        return self._account_quota_snapshot_from_row(row)

    def list_fireworks_account_quota_snapshots(self) -> list[FireworksAccountQuotaSnapshot]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM fireworks_account_quota_snapshots ORDER BY account_id").fetchall()
        return [snapshot for row in rows if (snapshot := self._account_quota_snapshot_from_row(row)) is not None]

    def delete_fireworks_key_snapshot(self, fingerprint: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM fireworks_key_snapshots WHERE key_fingerprint=?", (fingerprint,))

    def set_account_cooldown(self, account_id: str, cooldown_until: str | None, error_type: str | None) -> None:
        account_id = self._normalize_account_id(account_id)
        ts = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO fireworks_account_cooldowns(account_id, cooldown_until, last_error_type, last_error_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                  cooldown_until=excluded.cooldown_until,
                  last_error_type=excluded.last_error_type,
                  last_error_at=excluded.last_error_at,
                  updated_at=excluded.updated_at
                """,
                (account_id, cooldown_until, error_type, ts, ts),
            )

    def clear_account_cooldown(self, account_id: str) -> None:
        account_id = self._normalize_account_id(account_id)
        with self._connect() as conn:
            conn.execute("DELETE FROM fireworks_account_cooldowns WHERE account_id=?", (account_id,))

    def get_account_cooldown(self, account_id: str) -> AccountCooldownRecord | None:
        account_id = self._normalize_account_id(account_id)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM fireworks_account_cooldowns WHERE account_id=?", (account_id,)).fetchone()
        if not row:
            return None
        return AccountCooldownRecord(
            account_id=row["account_id"],
            cooldown_until=row["cooldown_until"],
            last_error_type=row["last_error_type"],
            last_error_at=row["last_error_at"],
            updated_at=row["updated_at"],
        )

    def list_account_cooldowns(self) -> list[AccountCooldownRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM fireworks_account_cooldowns ORDER BY account_id").fetchall()
        return [
            AccountCooldownRecord(
                account_id=row["account_id"],
                cooldown_until=row["cooldown_until"],
                last_error_type=row["last_error_type"],
                last_error_at=row["last_error_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def set_key_cooldown(self, name: str, cooldown_until: str | None, error_type: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE keys
                SET cooldown_until=?, last_error_type=?, last_error_at=?, updated_at=?
                WHERE name=?
                """,
                (cooldown_until, error_type, now_iso() if error_type else None, now_iso(), name),
            )

    def clear_key_cooldown(self, name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE keys SET cooldown_until=NULL, updated_at=? WHERE name=?",
                (now_iso(), name),
            )

    def upsert_model(self, model: ModelMapping) -> None:
        ts = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO model_mappings(
                  alias, upstream_model, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(alias) DO UPDATE SET
                  upstream_model=excluded.upstream_model,
                  enabled=excluded.enabled,
                  updated_at=excluded.updated_at
                """,
                (
                    model.alias,
                    model.upstream_model,
                    int(model.enabled),
                    ts,
                    ts,
                ),
            )

    def list_models(self) -> list[ModelMapping]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM model_mappings ORDER BY alias").fetchall()
        return [self._row_to_model(row) for row in rows]

    def get_model(self, alias: str) -> ModelMapping | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM model_mappings WHERE alias=?", (alias,)).fetchone()
        return self._row_to_model(row) if row else None

    def get_model_case_insensitive(self, alias: str) -> ModelMapping | None:
        alias_key = str(alias or "").strip().casefold()
        if not alias_key:
            return None
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM model_mappings").fetchall()
        for row in rows:
            if str(row["alias"] or "").strip().casefold() == alias_key:
                return self._row_to_model(row)
        return None

    def delete_model(self, alias: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM model_mappings WHERE alias=?", (alias,))

    def _row_to_model(self, row: sqlite3.Row) -> ModelMapping:
        return ModelMapping(
            alias=row["alias"],
            upstream_model=row["upstream_model"],
            enabled=bool(row["enabled"]),
        )

    @staticmethod
    def _request_log_is_error(data: dict[str, Any]) -> bool:
        if data.get("error_type") is not None:
            return True
        status_code = data.get("status_code")
        if status_code is None:
            return True
        try:
            normalized = int(status_code)
        except (TypeError, ValueError):
            return True
        return normalized < 200 or normalized >= 300

    def insert_request_log(self, data: dict[str, Any], retention: int) -> str:
        log_id = data.get("id") or str(uuid.uuid4())
        latency_ms = data.get("latency_ms")
        columns = {
            "id": log_id,
            "timestamp": data.get("timestamp") or now_iso(),
            "endpoint": data.get("endpoint"),
            "model_alias": data.get("model_alias"),
            "upstream_model": data.get("upstream_model"),
            "key_fingerprint": data.get("key_fingerprint"),
            "stable_key_hash": data.get("stable_key_hash"),
            "stream": int(bool(data.get("stream"))),
            "service_tier": data.get("service_tier"),
            "input_tokens": data.get("input_tokens", 0),
            "output_tokens": data.get("output_tokens", 0),
            "cached_tokens": data.get("cached_tokens", 0),
            "cache_hit_ratio": data.get("cache_hit_ratio", 0),
            "latency_ms": data.get("latency_ms"),
            "status_code": data.get("status_code"),
            "error_type": data.get("error_type"),
            "upstream_request_id": data.get("upstream_request_id"),
        }
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO request_logs({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                tuple(columns.values()),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO request_log_totals(id, updated_at)
                VALUES (1, ?)
                """,
                (now_iso(),),
            )
            conn.execute(
                """
                UPDATE request_log_totals
                SET
                  request_count=request_count + 1,
                  error_count=error_count + ?,
                  input_tokens=input_tokens + ?,
                  output_tokens=output_tokens + ?,
                  cached_tokens=cached_tokens + ?,
                  latency_ms_total=latency_ms_total + ?,
                  latency_ms_count=latency_ms_count + ?,
                  updated_at=?
                WHERE id=1
                """,
                (
                    1 if self._request_log_is_error(data) else 0,
                    int(data.get("input_tokens") or 0),
                    int(data.get("output_tokens") or 0),
                    int(data.get("cached_tokens") or 0),
                    int(latency_ms or 0) if latency_ms is not None else 0,
                    1 if latency_ms is not None else 0,
                    now_iso(),
                ),
            )
            conn.execute(
                """
                DELETE FROM request_logs
                WHERE id NOT IN (
                    SELECT id FROM request_logs ORDER BY timestamp DESC LIMIT ?
                )
                """,
                (retention,),
            )
        return log_id

    def request_log_totals(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                  request_count,
                  error_count,
                  input_tokens,
                  output_tokens,
                  cached_tokens,
                  latency_ms_total,
                  latency_ms_count
                FROM request_log_totals
                WHERE id=1
                """
            ).fetchone()
        if not row:
            return {
                "request_count": 0,
                "error_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "avg_latency_ms": 0,
            }
        latency_count = int(row["latency_ms_count"] or 0)
        latency_total = int(row["latency_ms_total"] or 0)
        return {
            "request_count": int(row["request_count"] or 0),
            "error_count": int(row["error_count"] or 0),
            "input_tokens": int(row["input_tokens"] or 0),
            "output_tokens": int(row["output_tokens"] or 0),
            "cached_tokens": int(row["cached_tokens"] or 0),
            "avg_latency_ms": int(latency_total / latency_count) if latency_count else 0,
        }

    def record_transform_debug(self, payload: dict[str, Any], retention: int) -> None:
        log_id = payload.get("id") or str(uuid.uuid4())

        def _json_value(value: Any) -> str | None:
            if value is None:
                return None
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

        columns = {
            "id": log_id,
            "timestamp": payload.get("timestamp") or now_iso(),
            "endpoint": payload.get("endpoint"),
            "upstream_endpoint": payload.get("upstream_endpoint"),
            "model_alias": payload.get("model_alias"),
            "upstream_model": payload.get("upstream_model"),
            "stream": int(bool(payload.get("stream"))),
            "service_tier": payload.get("service_tier"),
            "stable_key_source": payload.get("stable_key_source"),
            "route_trace_json": _json_value(payload.get("route_trace_json", payload.get("route_trace"))),
            "payload_fields_json": _json_value(payload.get("payload_fields_json", payload.get("payload_fields"))),
            "forwarded_headers_json": _json_value(payload.get("forwarded_headers_json", payload.get("forwarded_headers"))),
            "field_changes_json": _json_value(payload.get("field_changes_json", payload.get("field_changes"))),
            "blocked_fields_json": _json_value(payload.get("blocked_fields_json", payload.get("blocked_fields"))),
            "warnings_json": _json_value(payload.get("warnings_json", payload.get("warnings"))),
            "request_preview_json": _json_value(payload.get("request_preview_json")),
            "response_status_code": payload.get("response_status_code"),
            "error_type": payload.get("error_type"),
            "latency_ms": payload.get("latency_ms"),
            "created_at": payload.get("created_at") or now_iso(),
        }
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO transform_debug_logs({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                tuple(columns.values()),
            )
            conn.execute(
                """
                DELETE FROM transform_debug_logs
                WHERE id NOT IN (
                    SELECT id FROM transform_debug_logs ORDER BY timestamp DESC, created_at DESC LIMIT ?
                )
                """,
                (retention,),
            )

    def list_transform_debug_logs(self, limit: int = 50, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        filters = filters or {}
        where: list[str] = []
        params: list[Any] = []
        for field in ("endpoint", "model_alias"):
            if filters.get(field) not in (None, ""):
                where.append(f"{field}=?")
                params.append(filters[field])
        if filters.get("error_only"):
            where.append("error_type IS NOT NULL")
        if filters.get("has_route_trace"):
            where.append("route_trace_json IS NOT NULL")
        sql = "SELECT * FROM transform_debug_logs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY timestamp DESC, created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._transform_debug_payload(dict(row)) for row in rows]

    @staticmethod
    def _transform_debug_payload(row: dict[str, Any]) -> dict[str, Any]:
        def _parse(value: Any, fallback: Any):
            if value in (None, ""):
                return fallback
            if isinstance(value, (list, dict)):
                return value
            try:
                return json.loads(value)
            except Exception:
                return fallback

        return {
            "id": row.get("id"),
            "timestamp": row.get("timestamp"),
            "endpoint": row.get("endpoint"),
            "upstream_endpoint": row.get("upstream_endpoint"),
            "model_alias": row.get("model_alias"),
            "upstream_model": row.get("upstream_model"),
            "stream": bool(row.get("stream")),
            "service_tier": row.get("service_tier"),
            "stable_key_source": row.get("stable_key_source"),
            "route_trace": _parse(row.get("route_trace_json"), None),
            "payload_fields": _parse(row.get("payload_fields_json"), []),
            "forwarded_headers": _parse(row.get("forwarded_headers_json"), []),
            "field_changes": _parse(row.get("field_changes_json"), []),
            "blocked_fields": _parse(row.get("blocked_fields_json"), []),
            "warnings": _parse(row.get("warnings_json"), []),
            "request_preview": _parse(row.get("request_preview_json"), None),
            "response_status_code": row.get("response_status_code"),
            "error_type": row.get("error_type"),
            "latency_ms": row.get("latency_ms"),
            "created_at": row.get("created_at"),
        }

    def clear_transform_debug_logs(self) -> int:
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS count FROM transform_debug_logs").fetchone()["count"]
            conn.execute("DELETE FROM transform_debug_logs")
        return int(count or 0)

    def list_request_logs(self, limit: int = 100, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        filters = filters or {}
        where: list[str] = []
        params: list[Any] = []
        for field in ("model_alias", "key_fingerprint", "error_type", "status_code"):
            if filters.get(field) not in (None, ""):
                where.append(f"{field}=?")
                params.append(filters[field])
        sql = "SELECT * FROM request_logs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def key_usage_summary(self, fingerprint: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                  COUNT(*) AS request_count,
                  SUM(CASE WHEN status_code BETWEEN 200 AND 299 AND error_type IS NULL THEN 1 ELSE 0 END) AS success_count,
                  SUM(CASE WHEN error_type IS NOT NULL OR status_code IS NULL OR status_code < 200 OR status_code >= 300 THEN 1 ELSE 0 END) AS failure_count,
                  COALESCE(SUM(input_tokens), 0) AS input_tokens,
                  COALESCE(SUM(output_tokens), 0) AS output_tokens,
                  COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
                  COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
                FROM request_logs
                WHERE key_fingerprint=?
                """,
                (fingerprint,),
            ).fetchone()
        input_tokens = int(row["input_tokens"] or 0)
        cached_tokens = int(row["cached_tokens"] or 0)
        return {
            "request_count": int(row["request_count"] or 0),
            "success_count": int(row["success_count"] or 0),
            "failure_count": int(row["failure_count"] or 0),
            "input_tokens": input_tokens,
            "output_tokens": int(row["output_tokens"] or 0),
            "cached_tokens": cached_tokens,
            "cache_hit_ratio": cached_tokens / input_tokens if input_tokens else 0,
            "avg_latency_ms": int(row["avg_latency_ms"] or 0),
        }

    def key_by_fingerprint(self, fingerprint: str) -> KeyRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM keys WHERE fingerprint=?", (fingerprint,)).fetchone()
        if not row:
            return None
        return KeyRecord(
            name=row["name"],
            api_key=row["api_key_ciphertext"],
            fingerprint=row["fingerprint"],
            enabled=bool(row["enabled"]),
            cooldown_until=row["cooldown_until"],
            disabled_reason=row["disabled_reason"],
            last_error_type=row["last_error_type"],
            last_error_at=row["last_error_at"],
        )

    def overview(self) -> dict[str, Any]:
        keys = self.list_keys()
        now = now_iso()
        healthy = [key for key in keys if key.enabled and (not key.cooldown_until or key.cooldown_until <= now)]
        cooldown = [key for key in keys if key.enabled and key.cooldown_until and key.cooldown_until > now]
        with self._connect() as conn:
            stats = conn.execute(
                """
                SELECT
                  COUNT(*) AS request_count,
                  SUM(CASE WHEN error_type IS NOT NULL OR status_code IS NULL OR status_code < 200 OR status_code >= 300 THEN 1 ELSE 0 END) AS error_count,
                  COALESCE(SUM(input_tokens), 0) AS input_tokens,
                  COALESCE(SUM(output_tokens), 0) AS output_tokens,
                  COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
                  COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
                FROM request_logs
                """
            ).fetchone()
        totals = self.request_log_totals()
        input_tokens = int(totals["input_tokens"] or 0)
        cached_tokens = int(totals["cached_tokens"] or 0)
        retained_input_tokens = int(stats["input_tokens"] or 0)
        retained_cached_tokens = int(stats["cached_tokens"] or 0)
        return {
            "key_total": len(keys),
            "healthy_key_count": len(healthy),
            "cooldown_key_count": len(cooldown),
            "disabled_key_count": len([key for key in keys if not key.enabled]),
            "request_count": int(totals["request_count"] or 0),
            "error_count": int(totals["error_count"] or 0),
            "input_tokens": input_tokens,
            "output_tokens": int(totals["output_tokens"] or 0),
            "cached_tokens": cached_tokens,
            "cache_hit_ratio": cached_tokens / input_tokens if input_tokens else 0,
            "avg_latency_ms": int(totals["avg_latency_ms"] or 0),
            "retained_request_count": int(stats["request_count"] or 0),
            "retained_error_count": int(stats["error_count"] or 0),
            "retained_input_tokens": retained_input_tokens,
            "retained_output_tokens": int(stats["output_tokens"] or 0),
            "retained_cached_tokens": retained_cached_tokens,
            "retained_cache_hit_ratio": retained_cached_tokens / retained_input_tokens if retained_input_tokens else 0,
            "retained_avg_latency_ms": int(stats["avg_latency_ms"] or 0),
        }

    def cache_analysis(self) -> dict[str, Any]:
        with self._connect() as conn:
            summary_row = conn.execute(
                """
                SELECT
                  COUNT(*) AS request_count,
                  SUM(CASE WHEN error_type IS NOT NULL OR status_code IS NULL OR status_code < 200 OR status_code >= 300 THEN 1 ELSE 0 END) AS error_count,
                  SUM(CASE WHEN cached_tokens > 0 THEN 1 ELSE 0 END) AS cache_hit_request_count,
                  COALESCE(SUM(input_tokens), 0) AS input_tokens,
                  COALESCE(SUM(output_tokens), 0) AS output_tokens,
                  COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
                  COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
                FROM request_logs
                """
            ).fetchone()
            by_model_rows = conn.execute(
                """
                SELECT
                  model_alias,
                  MIN(upstream_model) AS upstream_model,
                  COUNT(DISTINCT upstream_model) AS distinct_upstream_count,
                  COUNT(*) AS request_count,
                  SUM(CASE WHEN cached_tokens > 0 THEN 1 ELSE 0 END) AS cache_hit_request_count,
                  SUM(CASE WHEN error_type IS NOT NULL OR status_code IS NULL OR status_code < 200 OR status_code >= 300 THEN 1 ELSE 0 END) AS error_count,
                  COALESCE(SUM(input_tokens), 0) AS input_tokens,
                  COALESCE(SUM(output_tokens), 0) AS output_tokens,
                  COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
                  COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
                FROM request_logs
                WHERE model_alias IS NOT NULL AND model_alias != ''
                GROUP BY model_alias
                ORDER BY request_count DESC, model_alias ASC
                """
            ).fetchall()
            by_key_rows = conn.execute(
                """
                SELECT
                  key_fingerprint,
                  COUNT(*) AS request_count,
                  SUM(CASE WHEN cached_tokens > 0 THEN 1 ELSE 0 END) AS cache_hit_request_count,
                  SUM(CASE WHEN error_type IS NOT NULL OR status_code IS NULL OR status_code < 200 OR status_code >= 300 THEN 1 ELSE 0 END) AS error_count,
                  COALESCE(SUM(input_tokens), 0) AS input_tokens,
                  COALESCE(SUM(output_tokens), 0) AS output_tokens,
                  COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
                  COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
                FROM request_logs
                WHERE key_fingerprint IS NOT NULL AND key_fingerprint != ''
                GROUP BY key_fingerprint
                ORDER BY request_count DESC, key_fingerprint ASC
                """
            ).fetchall()
            sticky_rows = conn.execute(
                """
                SELECT
                  stable_key_hash,
                  COALESCE(model_alias, '') AS model_alias,
                  MIN(upstream_model) AS upstream_model,
                  COUNT(*) AS request_count,
                  SUM(CASE WHEN cached_tokens > 0 THEN 1 ELSE 0 END) AS cache_hit_request_count,
                  SUM(CASE WHEN error_type IS NOT NULL OR status_code IS NULL OR status_code < 200 OR status_code >= 300 THEN 1 ELSE 0 END) AS error_count,
                  COALESCE(SUM(input_tokens), 0) AS input_tokens,
                  COALESCE(SUM(output_tokens), 0) AS output_tokens,
                  COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
                  COALESCE(AVG(latency_ms), 0) AS avg_latency_ms,
                  COUNT(DISTINCT key_fingerprint) AS distinct_key_count
                FROM request_logs
                WHERE stable_key_hash IS NOT NULL AND stable_key_hash != ''
                GROUP BY stable_key_hash, COALESCE(model_alias, '')
                ORDER BY request_count DESC, stable_key_hash ASC, model_alias ASC
                """
            ).fetchall()

        key_status = {}
        now = now_iso()
        for key in self.list_keys():
            if not key.enabled:
                status = "disabled"
            elif key.cooldown_until and key.cooldown_until > now:
                status = "cooldown"
            else:
                status = "active"
            key_status[key.fingerprint] = status

        def _payload(row: sqlite3.Row, *, include_distinct_keys: bool = False) -> dict[str, Any]:
            request_count = int(row["request_count"] or 0)
            cache_hit_request_count = int(row["cache_hit_request_count"] or 0) if "cache_hit_request_count" in row.keys() else 0
            input_tokens = int(row["input_tokens"] or 0)
            cached_tokens = int(row["cached_tokens"] or 0)
            token_cache_hit_rate = cached_tokens / input_tokens if input_tokens else 0
            request_cache_hit_rate = cache_hit_request_count / request_count if request_count else 0
            payload = {
                "request_count": request_count,
                "cache_hit_request_count": cache_hit_request_count,
                "error_count": int(row["error_count"] or 0),
                "input_tokens": input_tokens,
                "prompt_tokens": input_tokens,
                "output_tokens": int(row["output_tokens"] or 0),
                "cached_tokens": cached_tokens,
                "cache_hit_ratio": token_cache_hit_rate,
                "token_cache_hit_rate": token_cache_hit_rate,
                "request_cache_hit_rate": request_cache_hit_rate,
                "average_latency_ms": int(row["avg_latency_ms"] or 0),
                "avg_latency_ms": int(row["avg_latency_ms"] or 0),
            }
            if include_distinct_keys:
                payload["distinct_key_count"] = int(row["distinct_key_count"] or 0)
            return payload

        def _summary(row: sqlite3.Row) -> dict[str, Any]:
            return _payload(row)

        def _model_item(row: sqlite3.Row) -> dict[str, Any]:
            return {
                "model_alias": row["model_alias"],
                "upstream_model": row["upstream_model"],
                "distinct_upstream_count": int(row["distinct_upstream_count"] or 0),
                **_payload(row),
            }

        def _key_item(row: sqlite3.Row) -> dict[str, Any]:
            fingerprint = row["key_fingerprint"]
            key = self.key_by_fingerprint(fingerprint)
            masked_key = redact_secret(key.api_key, visible=6) if key else None
            return {
                "key_fingerprint": fingerprint,
                "key_name": key.name if key else None,
                "masked_key": masked_key,
                "key_label": key.name if key else masked_key or "unknown",
                "status": key_status.get(fingerprint, "unknown"),
                **_payload(row),
            }

        def _sticky_item(row: sqlite3.Row) -> dict[str, Any]:
            base = _payload(row, include_distinct_keys=True)
            key_count = base["distinct_key_count"]
            status = "stable" if key_count <= 1 else "dispersed"
            return {
                "stable_key_hash": row["stable_key_hash"],
                "model_alias": row["model_alias"] or None,
                "upstream_model": row["upstream_model"],
                "key_count": key_count,
                "status": status,
                "key_label": None,
                **base,
            }

        summary = _summary(summary_row)
        by_model_list = [_model_item(row) for row in by_model_rows]
        by_key_list = [_key_item(row) for row in by_key_rows]
        sticky_list = [_sticky_item(row) for row in sticky_rows]

        return {
            "summary": summary,
            "by_model_list": by_model_list,
            "by_key_list": by_key_list,
            "sticky": sticky_list,
        }

    def request_cost_estimate(
        self,
        *,
        input_token_rate: float,
        output_token_rate: float,
        cached_token_rate: float,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            totals = conn.execute(
                """
                SELECT
                  COUNT(*) AS request_count,
                  COALESCE(SUM(input_tokens), 0) AS input_tokens,
                  COALESCE(SUM(output_tokens), 0) AS output_tokens,
                  COALESCE(SUM(cached_tokens), 0) AS cached_tokens
                FROM request_logs
                """
            ).fetchone()
            by_model = conn.execute(
                """
                SELECT
                  COALESCE(model_alias, '') AS model_alias,
                  COUNT(*) AS request_count,
                  COALESCE(SUM(input_tokens), 0) AS input_tokens,
                  COALESCE(SUM(output_tokens), 0) AS output_tokens,
                  COALESCE(SUM(cached_tokens), 0) AS cached_tokens
                FROM request_logs
                GROUP BY COALESCE(model_alias, '')
                ORDER BY request_count DESC, model_alias ASC
                """
            ).fetchall()
            by_key = conn.execute(
                """
                SELECT
                  COALESCE(key_fingerprint, '') AS key_fingerprint,
                  COUNT(*) AS request_count,
                  COALESCE(SUM(input_tokens), 0) AS input_tokens,
                  COALESCE(SUM(output_tokens), 0) AS output_tokens,
                  COALESCE(SUM(cached_tokens), 0) AS cached_tokens
                FROM request_logs
                GROUP BY COALESCE(key_fingerprint, '')
                ORDER BY request_count DESC, key_fingerprint ASC
                """
            ).fetchall()
            by_endpoint = conn.execute(
                """
                SELECT
                  COALESCE(endpoint, '') AS endpoint,
                  COUNT(*) AS request_count,
                  COALESCE(SUM(input_tokens), 0) AS input_tokens,
                  COALESCE(SUM(output_tokens), 0) AS output_tokens,
                  COALESCE(SUM(cached_tokens), 0) AS cached_tokens
                FROM request_logs
                GROUP BY COALESCE(endpoint, '')
                ORDER BY request_count DESC, endpoint ASC
                """
            ).fetchall()

        def _estimate(row: sqlite3.Row) -> dict[str, Any]:
            input_tokens = int(row["input_tokens"] or 0)
            output_tokens = int(row["output_tokens"] or 0)
            cached_tokens = int(row["cached_tokens"] or 0)
            estimated_cost = (
                (input_tokens * input_token_rate)
                + (output_tokens * output_token_rate)
                + (cached_tokens * cached_token_rate)
            )
            return {
                "request_count": int(row["request_count"] or 0),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_tokens": cached_tokens,
                "estimated_cost": estimated_cost,
            }

        return {
            "rates": {
                "input_token_rate": input_token_rate,
                "output_token_rate": output_token_rate,
                "cached_token_rate": cached_token_rate,
                "currency": "USD",
                "note": "Estimated locally from request logs using configurable/default local estimate rates.",
            },
            "totals": {
                **_estimate(totals),
                "estimated_cost": float(
                    int(totals["input_tokens"] or 0) * input_token_rate
                    + int(totals["output_tokens"] or 0) * output_token_rate
                    + int(totals["cached_tokens"] or 0) * cached_token_rate
                ),
            },
            "by_model": [
                {"model_alias": row["model_alias"] or None, **_estimate(row)} for row in by_model
            ],
            "by_key": [
                {"key_fingerprint": row["key_fingerprint"] or None, **_estimate(row)} for row in by_key
            ],
            "by_endpoint": [
                {"endpoint": row["endpoint"] or None, **_estimate(row)} for row in by_endpoint
            ],
        }

    def import_billing_metrics_csv(self, *, csv_text: str, source_ref: str | None = None) -> dict[str, Any]:
        reader = csv.DictReader(io.StringIO(csv_text))
        required = {
            "email",
            "start_time",
            "end_time",
            "usage_type",
            "accelerator_type",
            "accelerator_seconds",
            "base_model_name",
            "model_bucket",
            "parameter_count",
            "prompt_tokens",
            "completion_tokens",
        }
        headers = set(reader.fieldnames or [])
        missing = sorted(required - headers)
        if missing:
            raise ValueError(f"missing required columns: {', '.join(missing)}")

        rows: list[dict[str, Any]] = []
        for idx, row in enumerate(reader, start=1):
            rows.append(
                {
                    "row_index": idx,
                    "email": (row.get("email") or "").strip() or None,
                    "start_time": (row.get("start_time") or "").strip() or None,
                    "end_time": (row.get("end_time") or "").strip() or None,
                    "usage_type": (row.get("usage_type") or "").strip() or None,
                    "accelerator_type": (row.get("accelerator_type") or "").strip() or None,
                    "accelerator_seconds": self._to_float(row.get("accelerator_seconds")),
                    "base_model_name": (row.get("base_model_name") or "").strip() or None,
                    "model_bucket": (row.get("model_bucket") or "").strip() or None,
                    "parameter_count": self._to_int(row.get("parameter_count")),
                    "prompt_tokens": self._to_int(row.get("prompt_tokens")),
                    "completion_tokens": self._to_int(row.get("completion_tokens")),
                }
            )

        import_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO billing_imports(id, source_type, source_ref, row_count, imported_at) VALUES (?, ?, ?, ?, ?)",
                (import_id, "csv", source_ref, len(rows), now_iso()),
            )
            conn.executemany(
                """
                INSERT INTO billing_metrics_rows(
                  import_id, row_index, email, start_time, end_time, usage_type, accelerator_type,
                  accelerator_seconds, base_model_name, model_bucket, parameter_count, prompt_tokens, completion_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        import_id,
                        row["row_index"],
                        row["email"],
                        row["start_time"],
                        row["end_time"],
                        row["usage_type"],
                        row["accelerator_type"],
                        row["accelerator_seconds"],
                        row["base_model_name"],
                        row["model_bucket"],
                        row["parameter_count"],
                        row["prompt_tokens"],
                        row["completion_tokens"],
                    )
                    for row in rows
                ],
            )
        return {"import_id": import_id, "row_count": len(rows)}

    def billing_metrics_summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            totals = conn.execute(
                """
                SELECT
                  COUNT(*) AS row_count,
                  COALESCE(SUM(accelerator_seconds), 0) AS accelerator_seconds,
                  COALESCE(SUM(parameter_count), 0) AS parameter_count,
                  COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                  COALESCE(SUM(completion_tokens), 0) AS completion_tokens
                FROM billing_metrics_rows
                """
            ).fetchone()
            by_usage = conn.execute(
                """
                SELECT usage_type, COUNT(*) AS row_count,
                       COALESCE(SUM(accelerator_seconds), 0) AS accelerator_seconds,
                       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens
                FROM billing_metrics_rows
                GROUP BY usage_type
                ORDER BY row_count DESC, usage_type ASC
                """
            ).fetchall()
            by_model = conn.execute(
                """
                SELECT base_model_name, COUNT(*) AS row_count,
                       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens
                FROM billing_metrics_rows
                GROUP BY base_model_name
                ORDER BY row_count DESC, base_model_name ASC
                """
            ).fetchall()
            recent_imports = conn.execute(
                "SELECT * FROM billing_imports ORDER BY imported_at DESC LIMIT 10"
            ).fetchall()

        return {
            "totals": {
                "row_count": int(totals["row_count"] or 0),
                "accelerator_seconds": float(totals["accelerator_seconds"] or 0),
                "parameter_count": int(totals["parameter_count"] or 0),
                "prompt_tokens": int(totals["prompt_tokens"] or 0),
                "completion_tokens": int(totals["completion_tokens"] or 0),
            },
            "by_usage_type": [dict(row) for row in by_usage],
            "by_base_model_name": [dict(row) for row in by_model],
            "imports": [dict(row) for row in recent_imports],
        }

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        return float(value)

    @staticmethod
    def _to_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        return int(float(value))


__all__ = [
    "KeyRecord",
    "ModelMapping",
    "FireworksKeySnapshot",
    "FireworksAccountQuotaSnapshot",
    "AccountCooldownRecord",
    "DEFAULT_MODELS",
    "AppRepository",
    "now_iso",
]
