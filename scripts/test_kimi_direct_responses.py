"""Verify kimi-k2.6-turbo stability on the Fireworks /v1/responses endpoint
without top_k (direct path) vs with top_k via /v1/chat/completions (fallback path).

Usage:
  set FIREWORKS_API_KEY=fw_your_key
  .venv/Scripts/python.exe scripts/test_kimi_direct_responses.py

This script calls Fireworks APIs directly (not through fireworks2api) to
compare the two paths.  It runs 3 iterations per path and checks for:
  - Response completeness (no early truncation)
  - Reasoning output present (thinking content)
  - Stable, coherent text output
  - No error responses
"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

MODEL = "accounts/fireworks/routers/kimi-k2p6-turbo"
BASE_URL = "https://api.fireworks.ai"
ITERATIONS = 3
PROMPT = "Explain why the sky is blue in 2-3 sentences. Think step by step."


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def test_responses_direct(client: httpx.Client, iteration: int) -> dict:
    """Test kimi on /v1/responses without top_k."""
    payload = {
        "model": MODEL,
        "input": PROMPT,
        "stream": False,
        "store": False,
        "reasoning": {"effort": "medium", "summary": "auto"},
        "include": ["reasoning.encrypted_content"],
    }
    t0 = time.perf_counter()
    resp = client.post("/inference/v1/responses", json=payload)
    elapsed = time.perf_counter() - t0
    body = resp.json()
    result = {
        "iteration": iteration,
        "path": "responses_direct",
        "status_code": resp.status_code,
        "elapsed_s": round(elapsed, 2),
        "error": None,
        "has_reasoning": False,
        "has_text": False,
        "output_types": [],
        "text_preview": None,
        "reasoning_preview": None,
    }
    if resp.status_code != 200:
        result["error"] = json.dumps(body, ensure_ascii=False)[:300]
        return result
    output = body.get("output", [])
    result["output_types"] = [item.get("type") for item in output if isinstance(item, dict)]
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "reasoning":
            result["has_reasoning"] = True
            summary = item.get("summary", [])
            for s in summary:
                if isinstance(s, dict) and isinstance(s.get("text"), str):
                    result["reasoning_preview"] = s["text"][:200]
                    break
        if item.get("type") == "message":
            content = item.get("content", [])
            for c in content:
                if isinstance(c, dict) and isinstance(c.get("text"), str):
                    result["has_text"] = True
                    result["text_preview"] = c["text"][:300]
                    break
    return result


def test_chat_fallback(client: httpx.Client, iteration: int) -> dict:
    """Test kimi on /v1/chat/completions with top_k=40 (fallback path behavior)."""
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": 256,
        "stream": False,
        "top_k": 40,
        "temperature": 1.0,
        "top_p": 0.95,
    }
    t0 = time.perf_counter()
    resp = client.post("/inference/v1/chat/completions", json=payload)
    elapsed = time.perf_counter() - t0
    body = resp.json()
    result = {
        "iteration": iteration,
        "path": "chat_completions_with_top_k",
        "status_code": resp.status_code,
        "elapsed_s": round(elapsed, 2),
        "error": None,
        "has_reasoning": False,
        "has_text": False,
        "text_preview": None,
        "reasoning_preview": None,
    }
    if resp.status_code != 200:
        result["error"] = json.dumps(body, ensure_ascii=False)[:300]
        return result
    choices = body.get("choices", [])
    if choices and isinstance(choices[0], dict):
        msg = choices[0].get("message", {})
        content = msg.get("content")
        if isinstance(content, str):
            result["has_text"] = True
            result["text_preview"] = content[:300]
        reasoning = msg.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning.strip():
            result["has_reasoning"] = True
            result["reasoning_preview"] = reasoning[:200]
    return result


def test_responses_streaming(client: httpx.Client, iteration: int) -> dict:
    """Test kimi on /v1/responses with streaming (no top_k)."""
    payload = {
        "model": MODEL,
        "input": PROMPT,
        "stream": True,
        "store": False,
        "reasoning": {"effort": "medium", "summary": "auto"},
        "include": ["reasoning.encrypted_content"],
    }
    t0 = time.perf_counter()
    event_types: list[str] = []
    has_text_delta = False
    has_reasoning_delta = False
    error_events: list[str] = []
    with client.stream("POST", "/inference/v1/responses", json=payload) as resp:
        if resp.status_code != 200:
            body = resp.read().decode("utf-8", errors="replace")[:300]
            return {
                "iteration": iteration,
                "path": "responses_streaming",
                "status_code": resp.status_code,
                "elapsed_s": round(time.perf_counter() - t0, 2),
                "error": body,
                "event_types": [],
                "has_text_delta": False,
                "has_reasoning_delta": False,
                "error_events": [],
            }
        for line in resp.iter_lines():
            if not line or line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_types.append(line[6:].strip())
            elif line.startswith("data:"):
                data = line[5:].strip()
                if data == "[DONE]":
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                et = payload.get("type", "")
                if et == "response.output_text.delta":
                    has_text_delta = True
                if et == "response.reasoning_summary_text.delta":
                    has_reasoning_delta = True
                if "error" in et or payload.get("error"):
                    error_events.append(et)
    elapsed = time.perf_counter() - t0
    return {
        "iteration": iteration,
        "path": "responses_streaming",
        "status_code": 200,
        "elapsed_s": round(elapsed, 2),
        "error": None,
        "event_types": sorted(set(event_types)),
        "has_text_delta": has_text_delta,
        "has_reasoning_delta": has_reasoning_delta,
        "error_events": error_events,
    }


def main() -> int:
    api_key = env("FIREWORKS_API_KEY")
    if not api_key:
        # Try loading from .env
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("FIREWORKS_API_KEYS="):
                        api_key = line.split("=", 1)[1].strip()
                        break
    if not api_key:
        print("FIREWORKS_API_KEY is required. Set it or ensure .env has FIREWORKS_API_KEYS.")
        return 1

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    timeout = httpx.Timeout(120)

    print("=" * 72)
    print(f"Model: {MODEL}")
    print(f"Prompt: {PROMPT}")
    print(f"Iterations: {ITERATIONS}")
    print("=" * 72)

    all_results: list[dict] = []

    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=timeout) as client:
        for i in range(1, ITERATIONS + 1):
            print(f"\n--- Iteration {i}/{ITERATIONS} ---")

            # Test 1: Responses direct (no top_k)
            print("  Testing /v1/responses (no top_k)...")
            r1 = test_responses_direct(client, i)
            all_results.append(r1)
            _print_result(r1)

            # Test 2: Chat completions with top_k=40
            print("  Testing /v1/chat/completions (top_k=40)...")
            r2 = test_chat_fallback(client, i)
            all_results.append(r2)
            _print_result(r2)

            # Test 3: Responses streaming (no top_k)
            print("  Testing /v1/responses streaming (no top_k)...")
            r3 = test_responses_streaming(client, i)
            all_results.append(r3)
            _print_result(r3)

    # Summary
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)

    for path in ["responses_direct", "chat_completions_with_top_k", "responses_streaming"]:
        path_results = [r for r in all_results if r["path"] == path]
        successes = [r for r in path_results if r["status_code"] == 200 and not r.get("error")]
        avg_elapsed = sum(r["elapsed_s"] for r in path_results) / len(path_results) if path_results else 0
        print(f"\n  {path}:")
        print(f"    Success: {len(successes)}/{len(path_results)}")
        print(f"    Avg time: {avg_elapsed:.2f}s")
        if path != "responses_streaming":
            has_reasoning = sum(1 for r in successes if r.get("has_reasoning"))
            has_text = sum(1 for r in successes if r.get("has_text"))
            print(f"    Has reasoning: {has_reasoning}/{len(successes)}")
            print(f"    Has text: {has_text}/{len(successes)}")
        else:
            has_text = sum(1 for r in successes if r.get("has_text_delta"))
            has_reasoning = sum(1 for r in successes if r.get("has_reasoning_delta"))
            has_errors = sum(1 for r in path_results if r.get("error_events"))
            print(f"    Has text delta: {has_text}/{len(successes)}")
            print(f"    Has reasoning delta: {has_reasoning}/{len(successes)}")
            print(f"    Error events: {has_errors}")

    # Verdict
    direct_successes = [r for r in all_results if r["path"] == "responses_direct" and r["status_code"] == 200]
    chat_successes = [r for r in all_results if r["path"] == "chat_completions_with_top_k" and r["status_code"] == 200]

    print("\n" + "=" * 72)
    if len(direct_successes) == ITERATIONS and len(chat_successes) == ITERATIONS:
        print("VERDICT: Both paths work. kimi-k2.6-turbo can use /v1/responses directly.")
        print("  Recommendation: Consider removing reasoning stability fallback for kimi.")
    elif len(direct_successes) == ITERATIONS:
        print("VERDICT: Direct responses works, chat with top_k may have issues.")
    elif len(chat_successes) == ITERATIONS:
        print("VERDICT: Chat with top_k=40 works, but direct responses may be unstable.")
        print("  Recommendation: Keep fallback for now.")
    else:
        print("VERDICT: Both paths have some failures. Check Fireworks API status.")
    print("=" * 72)

    return 0


def _print_result(r: dict) -> None:
    if r.get("error"):
        print(f"    FAIL: status={r['status_code']} error={r['error'][:200]}")
        return
    print(f"    OK: status={r['status_code']} time={r['elapsed_s']}s")
    if r["path"] != "responses_streaming":
        if r.get("has_reasoning"):
            print(f"    Reasoning: {r.get('reasoning_preview', '(empty)')[:150]}...")
        if r.get("has_text"):
            print(f"    Text: {r.get('text_preview', '(empty)')[:150]}...")
    else:
        et_count = len(r.get("event_types", []))
        print(f"    Events: {et_count} types, text_delta={r.get('has_text_delta')}, reasoning_delta={r.get('has_reasoning_delta')}")


if __name__ == "__main__":
    raise SystemExit(main())
