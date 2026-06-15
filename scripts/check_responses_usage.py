"""Print the usage field from a Fireworks Responses API call.

Usage:
  $env:FIREWORKS_API_KEY = "fw_..."
  .venv\Scripts\python.exe scripts\check_responses_usage.py
"""
from __future__ import annotations

import json
import os
import sys

import httpx


def main() -> int:
    key = os.environ.get("FIREWORKS_API_KEY")
    if not key:
        print("FIREWORKS_API_KEY is required", file=sys.stderr)
        return 1

    model = os.environ.get("CHECK_MODEL", "accounts/fireworks/models/kimi-k2p7-code")
    resp = httpx.post(
        "https://api.fireworks.ai/inference/v1/responses",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "input": "Say hello in one short sentence."},
        timeout=120,
    )
    print(f"status: {resp.status_code}")
    print(f"headers: {dict(resp.headers)}")
    try:
        body = resp.json()
    except Exception as exc:
        print(f"body parse error: {exc}")
        print(f"raw body: {resp.text[:500]}")
        return 1

    usage = body.get("usage")
    print(f"usage field: {json.dumps(usage, indent=2, ensure_ascii=False)}")
    print(f"top-level keys: {sorted(body.keys())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
