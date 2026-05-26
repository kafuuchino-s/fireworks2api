from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_VERSION = 12


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS keys (
  name TEXT PRIMARY KEY,
  api_key_ciphertext TEXT NOT NULL,
  fingerprint TEXT NOT NULL UNIQUE,
  enabled INTEGER NOT NULL DEFAULT 1,
  cooldown_until TEXT,
  disabled_reason TEXT,
  last_error_type TEXT,
  last_error_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_mappings (
  alias TEXT PRIMARY KEY,
  upstream_model TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS request_logs (
  id TEXT PRIMARY KEY,
  timestamp TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  model_alias TEXT,
  upstream_model TEXT,
  key_fingerprint TEXT,
  stable_key_hash TEXT,
  stream INTEGER NOT NULL DEFAULT 0,
  service_tier TEXT,
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  cached_tokens INTEGER DEFAULT 0,
  cache_hit_ratio REAL DEFAULT 0,
  latency_ms INTEGER,
  status_code INTEGER,
  error_type TEXT,
  upstream_request_id TEXT
);

CREATE TABLE IF NOT EXISTS request_log_totals (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  request_count INTEGER NOT NULL DEFAULT 0,
  error_count INTEGER NOT NULL DEFAULT 0,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cached_tokens INTEGER NOT NULL DEFAULT 0,
  latency_ms_total INTEGER NOT NULL DEFAULT 0,
  latency_ms_count INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fireworks_key_snapshots (
  key_fingerprint TEXT PRIMARY KEY,
  account_id TEXT,
  account_label TEXT,
  account_state TEXT,
  suspend_state TEXT,
  quota_supported INTEGER,
  quota_status TEXT,
  quota_status_code INTEGER,
  quota_summary_json TEXT,
  quota_items_json TEXT,
  account_refreshed_at TEXT,
  quota_refreshed_at TEXT,
  stale_after TEXT,
  refresh_status TEXT,
  last_refresh_error_type TEXT,
  last_refresh_error TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fireworks_account_quota_snapshots (
  account_id TEXT PRIMARY KEY,
  quota_supported INTEGER,
  quota_status TEXT,
  quota_status_code INTEGER,
  quota_summary_json TEXT,
  quota_items_json TEXT,
  quota_refreshed_at TEXT,
  stale_after TEXT,
  refresh_status TEXT,
  last_refresh_error_type TEXT,
  last_refresh_error TEXT,
  refresh_started_at TEXT,
  last_successful_refresh_at TEXT,
  consecutive_refresh_failures INTEGER NOT NULL DEFAULT 0,
  next_refresh_after TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fireworks_account_cooldowns (
  account_id TEXT PRIMARY KEY,
  cooldown_until TEXT,
  last_error_type TEXT,
  last_error_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS billing_imports (
  id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  source_ref TEXT,
  row_count INTEGER NOT NULL,
  imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS billing_metrics_rows (
  import_id TEXT NOT NULL,
  row_index INTEGER NOT NULL,
  email TEXT,
  start_time TEXT,
  end_time TEXT,
  usage_type TEXT,
  accelerator_type TEXT,
  accelerator_seconds REAL,
  base_model_name TEXT,
  model_bucket TEXT,
  parameter_count INTEGER,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  FOREIGN KEY(import_id) REFERENCES billing_imports(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_billing_metrics_import_id ON billing_metrics_rows(import_id);
CREATE INDEX IF NOT EXISTS idx_billing_metrics_email ON billing_metrics_rows(email);
CREATE INDEX IF NOT EXISTS idx_billing_metrics_usage_type ON billing_metrics_rows(usage_type);

CREATE INDEX IF NOT EXISTS idx_request_logs_timestamp ON request_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_request_logs_model ON request_logs(model_alias);
CREATE INDEX IF NOT EXISTS idx_request_logs_key ON request_logs(key_fingerprint);
CREATE INDEX IF NOT EXISTS idx_fireworks_key_snapshots_stale_after ON fireworks_key_snapshots(stale_after);
CREATE INDEX IF NOT EXISTS idx_fireworks_account_quota_snapshots_stale_after ON fireworks_account_quota_snapshots(stale_after);
CREATE INDEX IF NOT EXISTS idx_fireworks_account_quota_snapshots_next_refresh_after ON fireworks_account_quota_snapshots(next_refresh_after);
CREATE INDEX IF NOT EXISTS idx_fireworks_account_cooldowns_cooldown_until ON fireworks_account_cooldowns(cooldown_until);

CREATE TABLE IF NOT EXISTS response_key_routes (
  response_id TEXT PRIMARY KEY,
  key_name TEXT NOT NULL,
  key_fingerprint TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_response_key_routes_key_fingerprint ON response_key_routes(key_fingerprint);

CREATE TABLE IF NOT EXISTS response_session_bindings (
  scope TEXT NOT NULL,
  model TEXT NOT NULL,
  session_hash TEXT NOT NULL,
  response_id TEXT NOT NULL,
  key_name TEXT,
  key_fingerprint TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(scope, model, session_hash)
);

CREATE INDEX IF NOT EXISTS idx_response_session_bindings_response_id ON response_session_bindings(response_id);
CREATE INDEX IF NOT EXISTS idx_response_session_bindings_key_fingerprint ON response_session_bindings(key_fingerprint);

CREATE TABLE IF NOT EXISTS transform_debug_logs (
  id TEXT PRIMARY KEY,
  timestamp TEXT NOT NULL,
  endpoint TEXT,
  upstream_endpoint TEXT,
  model_alias TEXT,
  upstream_model TEXT,
  stream INTEGER DEFAULT 0,
  service_tier TEXT,
  stable_key_source TEXT,
  route_trace_json TEXT,
  payload_fields_json TEXT,
  forwarded_headers_json TEXT,
  field_changes_json TEXT,
  blocked_fields_json TEXT,
  warnings_json TEXT,
  request_preview_json TEXT,
  response_status_code INTEGER,
  error_type TEXT,
  latency_ms INTEGER,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transform_debug_logs_timestamp ON transform_debug_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_transform_debug_logs_endpoint ON transform_debug_logs(endpoint);
CREATE INDEX IF NOT EXISTS idx_transform_debug_logs_model ON transform_debug_logs(model_alias);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate_model_mappings(conn: sqlite3.Connection) -> None:
    columns = _columns(conn, "model_mappings")
    expected_columns = {"alias", "upstream_model", "enabled", "created_at", "updated_at"}
    if not columns or columns == expected_columns:
        return

    conn.execute("ALTER TABLE model_mappings RENAME TO model_mappings_old")
    conn.execute(
        """
        CREATE TABLE model_mappings (
          alias TEXT PRIMARY KEY,
          upstream_model TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO model_mappings(alias, upstream_model, enabled, created_at, updated_at)
        SELECT alias, upstream_model, enabled, created_at, updated_at
        FROM model_mappings_old
        """
    )
    conn.execute("DROP TABLE model_mappings_old")


def _migrate_transform_debug_logs(conn: sqlite3.Connection) -> None:
    columns = _columns(conn, "transform_debug_logs")
    expected_columns = {
        "id",
        "timestamp",
        "endpoint",
        "upstream_endpoint",
        "model_alias",
        "upstream_model",
        "stream",
        "service_tier",
        "stable_key_source",
        "route_trace_json",
        "payload_fields_json",
        "forwarded_headers_json",
        "field_changes_json",
        "blocked_fields_json",
        "warnings_json",
        "request_preview_json",
        "response_status_code",
        "error_type",
        "latency_ms",
        "created_at",
    }
    if not columns or columns == expected_columns:
        return

    conn.execute("ALTER TABLE transform_debug_logs RENAME TO transform_debug_logs_old")
    conn.execute(
        """
        CREATE TABLE transform_debug_logs (
          id TEXT PRIMARY KEY,
          timestamp TEXT NOT NULL,
          endpoint TEXT,
          upstream_endpoint TEXT,
          model_alias TEXT,
          upstream_model TEXT,
          stream INTEGER DEFAULT 0,
          service_tier TEXT,
          stable_key_source TEXT,
          route_trace_json TEXT,
          payload_fields_json TEXT,
          forwarded_headers_json TEXT,
          field_changes_json TEXT,
          blocked_fields_json TEXT,
          warnings_json TEXT,
          request_preview_json TEXT,
          response_status_code INTEGER,
          error_type TEXT,
          latency_ms INTEGER,
          created_at TEXT NOT NULL
        )
        """
    )
    copy_columns = [column for column in expected_columns if column != "route_trace_json" or column in columns]
    conn.execute(
        f"INSERT INTO transform_debug_logs({','.join(copy_columns)}) SELECT {','.join(copy_columns)} FROM transform_debug_logs_old"
    )
    conn.execute("DROP TABLE transform_debug_logs_old")


def _seed_request_log_totals(conn: sqlite3.Connection) -> None:
    existing = conn.execute("SELECT 1 FROM request_log_totals WHERE id=1").fetchone()
    if existing:
        return

    row = conn.execute(
        """
        SELECT
          COUNT(*) AS request_count,
          SUM(CASE WHEN error_type IS NOT NULL OR status_code IS NULL OR status_code < 200 OR status_code >= 300 THEN 1 ELSE 0 END) AS error_count,
          COALESCE(SUM(input_tokens), 0) AS input_tokens,
          COALESCE(SUM(output_tokens), 0) AS output_tokens,
          COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
          COALESCE(SUM(CASE WHEN latency_ms IS NOT NULL THEN latency_ms ELSE 0 END), 0) AS latency_ms_total,
          SUM(CASE WHEN latency_ms IS NOT NULL THEN 1 ELSE 0 END) AS latency_ms_count
        FROM request_logs
        """
    ).fetchone()
    conn.execute(
        """
        INSERT INTO request_log_totals(
          id, request_count, error_count, input_tokens, output_tokens, cached_tokens,
          latency_ms_total, latency_ms_count, updated_at
        )
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            int(row["request_count"] or 0),
            int(row["error_count"] or 0),
            int(row["input_tokens"] or 0),
            int(row["output_tokens"] or 0),
            int(row["cached_tokens"] or 0),
            int(row["latency_ms_total"] or 0),
            int(row["latency_ms_count"] or 0),
        ),
    )


def _normalize_account_id(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    if normalized.startswith("accounts/"):
        normalized = normalized[len("accounts/") :]
    normalized = normalized.strip()
    return normalized or None


def _migrate_account_quota_snapshots(conn: sqlite3.Connection) -> None:
    key_columns = _columns(conn, "fireworks_key_snapshots")
    account_columns = _columns(conn, "fireworks_account_quota_snapshots")
    if not key_columns or not account_columns:
        return

    rows = conn.execute(
        """
        SELECT account_id, quota_supported, quota_status, quota_status_code,
               quota_summary_json, quota_items_json, quota_refreshed_at, stale_after,
               refresh_status, last_refresh_error_type, last_refresh_error, updated_at
        FROM fireworks_key_snapshots
        WHERE account_id IS NOT NULL AND TRIM(account_id) != ''
        ORDER BY COALESCE(quota_refreshed_at, updated_at) DESC
        """
    ).fetchall()
    for row in rows:
        account_id = _normalize_account_id(row["account_id"])
        if not account_id:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO fireworks_account_quota_snapshots(
              account_id, quota_supported, quota_status, quota_status_code,
              quota_summary_json, quota_items_json, quota_refreshed_at, stale_after,
              refresh_status, last_refresh_error_type, last_refresh_error,
              last_successful_refresh_at, consecutive_refresh_failures, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                row["quota_supported"],
                row["quota_status"],
                row["quota_status_code"],
                row["quota_summary_json"],
                row["quota_items_json"],
                row["quota_refreshed_at"],
                row["stale_after"],
                row["refresh_status"],
                row["last_refresh_error_type"],
                row["last_refresh_error"],
                row["quota_refreshed_at"] if row["refresh_status"] == "ok" else None,
                0 if row["refresh_status"] == "ok" else 1,
                row["updated_at"],
            ),
        )


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate_model_mappings(conn)
        _migrate_transform_debug_logs(conn)
        _migrate_account_quota_snapshots(conn)
        _seed_request_log_totals(conn)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, datetime('now'))",
            (SCHEMA_VERSION,),
        )


__all__ = ["SCHEMA_VERSION", "SCHEMA", "connect", "init_db"]
