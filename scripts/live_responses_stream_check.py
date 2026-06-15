"""Live check for streaming /v1/responses usage estimation.

Uses the local running server with the real .env configuration. Sends requests
concurrently with httpx.AsyncClient, then validates that every stream ends with
a terminal event carrying non-zero, reasonable usage.
"""
from __future__ import annotations

import asyncio
import json

import httpx


BASE_URL = "http://127.0.0.1:8001"
ADMIN_TOKEN = "admin-local"
MODEL = "kimi-k2.7-code-fast"


PROMPTS = [
    "Say hello in one short sentence.",
    "Explain what a tokenizer is, in no more than 30 words.",
    "List three colors.",
    "Write a haiku about the moon.",
    "What is 2+2? Answer with one word.",
    "Write a 500-word essay about the history of artificial intelligence.",
    "Generate a long Python docstring explaining a function that sorts a list of dictionaries by multiple keys.",
    "Summarize the following in about 300 words: machine learning, deep learning, and neural networks.",
]


async def fetch_proxy_key(client: httpx.AsyncClient) -> str:
    resp = await client.get(
        f"{BASE_URL}/admin/config/runtime",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    resp.raise_for_status()
    keys = resp.json()["config"].get("proxy_api_keys", [])
    if not keys:
        raise RuntimeError("no proxy keys configured")
    return keys[0]


def parse_stream(response_text: str) -> tuple[str | None, dict[str, object] | None, list[str]]:
    """Return (terminal_event_name, terminal_payload, text_parts)."""
    completed = None
    incomplete = None
    text_parts: list[str] = []
    for event in response_text.split("\n\n"):
        lines = event.strip().split("\n")
        if not lines:
            continue
        data_line = next((line for line in lines if line.startswith("data:")), "")
        if not data_line:
            continue
        data = data_line[5:].strip()
        if data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        event_type = payload.get("type")
        if event_type == "response.output_text.delta":
            delta = payload.get("delta") or payload.get("text")
            if isinstance(delta, str):
                text_parts.append(delta)
        elif event_type == "response.completed":
            completed = payload
        elif event_type == "response.incomplete":
            incomplete = payload
    if completed:
        return "completed", completed, text_parts
    if incomplete:
        return "incomplete", incomplete, text_parts
    return None, None, text_parts


async def run_one(client: httpx.AsyncClient, proxy_key: str, prompt: str, index: int) -> dict[str, object]:
    headers = {"Authorization": f"Bearer {proxy_key}", "Content-Type": "application/json"}
    body = {"model": MODEL, "input": prompt, "stream": True, "max_output_tokens": 1024}
    try:
        resp = await client.post(f"{BASE_URL}/v1/responses", headers=headers, json=body, timeout=180.0)
    except Exception as exc:
        return {"index": index, "prompt": prompt, "status": "error", "error": str(exc)}
    if resp.status_code != 200:
        return {"index": index, "prompt": prompt, "status": "http_error", "status_code": resp.status_code, "text": resp.text[:500]}
    event_name, terminal, text_parts = parse_stream(resp.text)
    if terminal is None:
        return {"index": index, "prompt": prompt, "status": "no_terminal", "text_len": len("".join(text_parts))}
    response_data = terminal.get("response", terminal)
    usage = response_data.get("usage", {}) if isinstance(response_data, dict) else {}
    inp = usage.get("input_tokens") if isinstance(usage, dict) else 0
    out = usage.get("output_tokens") if isinstance(usage, dict) else 0
    return {
        "index": index,
        "prompt": prompt,
        "status": "ok",
        "terminal": event_name,
        "text_len": len("".join(text_parts)),
        "input_tokens": inp,
        "output_tokens": out,
        "usage": usage,
    }


async def main() -> int:
    limits = httpx.Limits(max_connections=50, max_keepalive_connections=20)
    async with httpx.AsyncClient(limits=limits, timeout=httpx.Timeout(180.0)) as client:
        proxy_key = await fetch_proxy_key(client)
        print(f"using proxy key: {proxy_key[:10]}...")

        tasks = []
        for iteration in range(5):
            for i, prompt in enumerate(PROMPTS):
                index = iteration * len(PROMPTS) + i + 1
                tasks.append(run_one(client, proxy_key, prompt, index))

        print(f"running {len(tasks)} requests concurrently...")
        results = await asyncio.gather(*tasks)

    total = len(results)
    ok = 0
    output_zero = 0
    output_equals_input = 0
    errors = 0
    incomplete_count = 0

    category_counts: dict[str, dict[str, int]] = {
        "ok": {},
        "output_zero": {},
        "output_equals_input": {},
        "error": {},
        "incomplete": {},
    }

    for result in results:
        prompt = result["prompt"]
        if result["status"] != "ok":
            errors += 1
            category_counts["error"][prompt] = category_counts["error"].get(prompt, 0) + 1
            print(f"[{result['index']:03d}] {result['status']}: {prompt[:60]!r}...")
            if result["status"] == "http_error":
                print(f"       status_code={result.get('status_code')}, text={result.get('text')}")
            elif result["status"] == "error":
                print(f"       error={result.get('error')}")
            elif result["status"] == "no_terminal":
                print(f"       text_len={result.get('text_len')}")
            continue

        ok += 1
        category_counts["ok"][prompt] = category_counts["ok"].get(prompt, 0) + 1
        out = result["output_tokens"]
        inp = result["input_tokens"]
        if out == 0:
            output_zero += 1
            category_counts["output_zero"][prompt] = category_counts["output_zero"].get(prompt, 0) + 1
        elif inp and out == inp:
            output_equals_input += 1
            category_counts["output_equals_input"][prompt] = category_counts["output_equals_input"].get(prompt, 0) + 1
        if result["terminal"] == "incomplete":
            incomplete_count += 1
            category_counts["incomplete"][prompt] = category_counts["incomplete"].get(prompt, 0) + 1

    print("\n=== Summary ===")
    print(f"total: {total}, ok: {ok}, errors: {errors}")
    print(f"output_zero: {output_zero}, output_equals_input: {output_equals_input}, incomplete: {incomplete_count}")
    for category, counts in category_counts.items():
        if counts:
            print(f"\n{category}:")
            for prompt, count in sorted(counts.items(), key=lambda x: -x[1]):
                print(f"  [{count}x] {prompt[:60]!r}...")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
