from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "live-capability-smoke"
LATEST_JSON = DATA_DIR / "latest.json"
CURRENT_ALIAS_JSON = DATA_DIR / "current-alias-matrix.json"
CURRENT_ALIAS_MD = DATA_DIR / "current-alias-matrix.md"

PASS_THROUGH_ENV_KEYS = {
    "FIREWORKS2API_BASE_URL",
    "FIREWORKS2API_PROXY_KEY",
    "FIREWORKS2API_CHAT_MODEL",
    "FIREWORKS2API_RESPONSES_MODEL",
    "FIREWORKS2API_COMPLETIONS_MODEL",
    "FIREWORKS2API_MESSAGES_MODEL",
    "FIREWORKS2API_VISION_MODEL",
    "FIREWORKS2API_REASONING_MODEL",
    "FIREWORKS2API_SMOKE_ADVANCED",
    "FIREWORKS2API_SMOKE_DELETE_RESPONSE",
    "FIREWORKS2API_SMOKE_ERRORS",
    "FIREWORKS2API_SMOKE_VERBOSE",
    "FIREWORKS2API_SMOKE_TOOLS",
    "FIREWORKS2API_SMOKE_MCP",
    "FIREWORKS2API_MCP_SERVER_URL",
    "FIREWORKS2API_MCP_SERVER_URLS",
    "FIREWORKS2API_MCP_ATTEMPTS",
    "FIREWORKS2API_MCP_TIMEOUT_SECONDS",
    "FIREWORKS2API_SMOKE_STRICT_MCP",
    "FIREWORKS2API_SMOKE_ANTHROPIC_TOOLS",
    "FIREWORKS2API_ANTHROPIC_TOOL_MODEL",
    "FIREWORKS2API_SMOKE_STRICT_ANTHROPIC_TOOLS",
    "FIREWORKS2API_SMOKE_IMAGES",
    "FIREWORKS2API_IMAGE_URL",
    "FIREWORKS2API_SMOKE_REASONING",
    "FIREWORKS2API_SMOKE_STREAM",
    "FIREWORKS2API_SMOKE_TIMEOUT_SECONDS",
}


@dataclass
class ServerHandle:
    process: subprocess.Popen[str]
    log_path: Path


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name)
    return default if not raw else raw.lower() in {"1", "true", "yes", "on"}


def base_url_reachable(base_url: str, timeout_seconds: float = 2.0) -> bool:
    for path in ("/health", "/"):
        try:
            with urlopen(f"{base_url.rstrip('/')}{path}", timeout=timeout_seconds) as response:
                if 200 <= getattr(response, "status", 200) < 500:
                    return True
        except Exception:
            pass
    return False


def wait_for_base_url(base_url: str, timeout_seconds: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if base_url_reachable(base_url):
            return True
        time.sleep(0.5)
    return False


def build_smoke_env(base_url: str) -> dict[str, str]:
    result = dict(os.environ)
    result["FIREWORKS2API_BASE_URL"] = base_url
    result.setdefault("FIREWORKS2API_CHAT_MODEL", env("FIREWORKS2API_CHAT_MODEL", "kimi-k2.6"))
    result.setdefault("FIREWORKS2API_RESPONSES_MODEL", env("FIREWORKS2API_RESPONSES_MODEL", result["FIREWORKS2API_CHAT_MODEL"]))
    result.setdefault("FIREWORKS2API_COMPLETIONS_MODEL", env("FIREWORKS2API_COMPLETIONS_MODEL", result["FIREWORKS2API_CHAT_MODEL"]))
    result.setdefault("FIREWORKS2API_MESSAGES_MODEL", env("FIREWORKS2API_MESSAGES_MODEL", result["FIREWORKS2API_CHAT_MODEL"]))
    result.pop("FIREWORKS2API_EMBEDDINGS_MODEL", None)
    result.pop("FIREWORKS2API_RERANK_MODEL", None)
    return result


def build_server_env() -> dict[str, str]:
    result = dict(os.environ)
    result.setdefault("ENABLE_ADMIN_STATIC", "true")
    result.setdefault("ADMIN_TOKEN", env("ADMIN_TOKEN", "admin-local"))
    result.setdefault("PROXY_API_KEYS", env("PROXY_API_KEYS", env("FIREWORKS2API_PROXY_KEY", "sk-local-dev")))
    return result


def start_local_server() -> ServerHandle:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_fd, log_name = tempfile.mkstemp(prefix="live-capability-smoke-", suffix=".log")
    os.close(log_fd)
    log_path = Path(log_name)
    handle = ServerHandle(
        process=subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app"],
            cwd=str(ROOT),
            env=build_server_env(),
            stdout=open(log_path, "a", encoding="utf-8"),
            stderr=subprocess.STDOUT,
            text=True,
        ),
        log_path=log_path,
    )
    return handle


def cleanup_server(handle: ServerHandle | None) -> None:
    if not handle:
        return
    if handle.process.poll() is None:
        handle.process.terminate()
        try:
            handle.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            handle.process.kill()
    try:
        handle.log_path.unlink(missing_ok=True)
    except Exception:
        pass


def sanitized_subprocess_env(base_url: str) -> dict[str, str]:
    env_map = build_smoke_env(base_url)
    env_map.pop("FIREWORKS2API_EMBEDDINGS_MODEL", None)
    env_map.pop("FIREWORKS2API_RERANK_MODEL", None)
    return env_map


def run_script(path: str, base_url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, path], cwd=str(ROOT), env=sanitized_subprocess_env(base_url), capture_output=True, text=True)


def write_results(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    CURRENT_ALIAS_JSON.write_text(json.dumps(payload.get("current_aliases", {}), indent=2, sort_keys=True), encoding="utf-8")
    lines = ["# Current Alias Matrix", "", "| alias | status |", "| --- | --- |"]
    for alias, status in sorted(payload.get("current_aliases", {}).items()):
        lines.append(f"| {alias} | {status} |")
    CURRENT_ALIAS_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    base_url = env("FIREWORKS2API_BASE_URL", "http://127.0.0.1:8000")
    handle: ServerHandle | None = None
    started_server = False
    if not base_url_reachable(base_url):
        handle = start_local_server()
        started_server = True
        atexit.register(cleanup_server, handle)
        if not wait_for_base_url(base_url):
            cleanup_server(handle)
            print("local server did not become ready")
            return 2

    try:
        inference = run_script("scripts/fireworks_inference_smoke.py", base_url)
        sdk = run_script("scripts/sdk_live_smoke.py", base_url)
        payload = {
            "base_url": base_url,
            "started_local_server": started_server,
            "inference": {"returncode": inference.returncode},
            "sdk": {"returncode": sdk.returncode},
            "current_aliases": {"chat": "pass" if inference.returncode == 0 else "fail", "sdk": "pass" if sdk.returncode == 0 else "fail"},
        }
        write_results(payload)
        return 0 if inference.returncode == 0 and sdk.returncode == 0 else 1
    finally:
        cleanup_server(handle)


if __name__ == "__main__":
    raise SystemExit(main())
