"""Lightweight live smoke checklist for Fireworks inference base.

Usage:
  set FIREWORKS2API_BASE_URL=http://127.0.0.1:8000
  set FIREWORKS2API_PROXY_KEY=sk-local-dev
  set FIREWORKS2API_CHAT_MODEL=kimi-k2.6
  set FIREWORKS2API_RESPONSES_MODEL=kimi-k2.6
  set FIREWORKS2API_COMPLETIONS_MODEL=kimi-k2.6
  set FIREWORKS2API_EMBEDDINGS_MODEL=<optional-embedding-alias>
  set FIREWORKS2API_RERANK_MODEL=<optional-rerank-alias>
  set FIREWORKS2API_VISION_MODEL=<optional-vision-alias>
  set FIREWORKS2API_REASONING_MODEL=<optional-reasoning-alias>
  set FIREWORKS2API_MESSAGES_MODEL=kimi-k2.6
  .venv/Scripts/python.exe scripts/fireworks_inference_smoke.py

The script only calls the local fireworks2api proxy. It exercises a small
subset of inference endpoints against the running server and redacts secrets
from output.

Environment:
  FIREWORKS2API_BASE_URL    Proxy base URL, default http://127.0.0.1:8000
  FIREWORKS2API_PROXY_KEY   Bearer token for /v1/* calls
  FIREWORKS2API_CHAT_MODEL  Chat/completions model alias, default kimi-k2.6
  FIREWORKS2API_RESPONSES_MODEL  Responses model alias, default kimi-k2.6
  FIREWORKS2API_COMPLETIONS_MODEL Completions model alias, default kimi-k2.6
  FIREWORKS2API_MESSAGES_MODEL Messages model alias, default kimi-k2.6
  FIREWORKS2API_EMBEDDINGS_MODEL  Optional embeddings alias; skipped if unset
  FIREWORKS2API_RERANK_MODEL      Optional rerank alias; skipped if unset
  FIREWORKS2API_VISION_MODEL      Optional vision-capable alias; skipped if unset
  FIREWORKS2API_REASONING_MODEL   Optional reasoning-capable alias; skipped if unset
  FIREWORKS2API_SMOKE_ADVANCED    Enable optional advanced-field smoke cases; default false
  FIREWORKS2API_SMOKE_DELETE_RESPONSE  Delete smoke-created response after GET; default false
  FIREWORKS2API_SMOKE_ERRORS      Enable safe negative local smoke checks; default false
  FIREWORKS2API_SMOKE_VERBOSE     Print full successful JSON responses; default false
  FIREWORKS2API_SMOKE_TOOLS       Enable opt-in Responses tool smoke cases; default false
  FIREWORKS2API_SMOKE_MCP         Enable opt-in Responses MCP smoke cases; default false
  FIREWORKS2API_MCP_SERVER_URL    Optional MCP server URL for Responses MCP smoke
  FIREWORKS2API_SMOKE_ANTHROPIC_TOOLS  Enable opt-in Anthropic tool round-trip smoke cases; default false
  FIREWORKS2API_ANTHROPIC_TOOL_MODEL   Anthropic Messages model alias for tool smoke; default FIREWORKS2API_MESSAGES_MODEL
  FIREWORKS2API_SMOKE_STRICT_ANTHROPIC_TOOLS  Fail when Anthropic tool smoke cannot complete; default false
  FIREWORKS2API_MCP_SERVER_URLS   CSV of MCP server URLs for bounded multi-server smoke
  FIREWORKS2API_MCP_ATTEMPTS      Maximum MCP smoke attempts across servers; default 3
  FIREWORKS2API_MCP_TIMEOUT_SECONDS  Timeout budget per MCP attempt; default 30
  FIREWORKS2API_SMOKE_STRICT_MCP  Fail when MCP smoke cannot complete; default false
  FIREWORKS2API_SMOKE_IMAGES      Enable opt-in multimodal image smoke cases; default false
  FIREWORKS2API_IMAGE_URL         Optional image URL for multimodal smoke
  FIREWORKS2API_SMOKE_REASONING   Enable opt-in reasoning smoke cases; default false
"""

from __future__ import annotations

import json
import os
import re
import base64
from collections.abc import Iterator
from typing import Any

import httpx


TOKEN_RE = re.compile(r"sk-[A-Za-z0-9_-]{8,}|Bearer\s+[A-Za-z0-9._-]+", re.IGNORECASE)


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return TOKEN_RE.sub("[REDACTED]", value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    return value


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def print_json(prefix: str, payload: Any) -> None:
    print(f"{prefix}: {json.dumps(redact(payload), ensure_ascii=False, indent=2, sort_keys=True)}")


def _text_preview(value: Any, limit: int = 160) -> str | None:
    if isinstance(value, str):
        compact = " ".join(value.split())
        return compact[:limit] + ("..." if len(compact) > limit else "")
    if isinstance(value, list):
        for item in value:
            preview = _text_preview(item, limit=limit)
            if preview:
                return preview
    if isinstance(value, dict):
        for key in ("text", "content", "output_text"):
            preview = _text_preview(value.get(key), limit=limit)
            if preview:
                return preview
        for key in ("message", "delta", "output", "data", "results"):
            preview = _text_preview(value.get(key), limit=limit)
            if preview:
                return preview
    return None


def extract_responses_text(payload: Any) -> str:
    parts: list[str] = []
    if isinstance(payload, dict):
        output = payload.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                for key in ("text", "output_text"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        parts.append(value.strip())
                content = item.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            text = block.get("text")
                            if isinstance(text, str) and text.strip():
                                parts.append(text.strip())
        if isinstance(payload.get("text"), str):
            parts.append(payload["text"].strip())
    return " ".join(parts).strip()


def extract_responses_output_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("output"), list):
        return [item for item in payload["output"] if isinstance(item, dict)]
    return []


def extract_tool_call_id(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    for key in ("id", "call_id", "tool_call_id"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    function = item.get("function")
    if isinstance(function, dict):
        value = function.get("call_id")
        if isinstance(value, str) and value.strip():
            return value
    return None


def extract_anthropic_tool_use(payload: Any) -> list[dict[str, Any]]:
    tool_uses: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        content = payload.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    tool_uses.append(item)
    return tool_uses


def extract_anthropic_tool_use_id(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    for key in ("id", "tool_use_id"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def parse_csv_env(name: str) -> list[str]:
    raw = env(name)
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def extract_responses_tool_calls(payload: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for item in extract_responses_output_items(payload):
        if item.get("type") in {"function_call", "tool_call", "mcp_call"} or "call_id" in item or "tool_call_id" in item:
            calls.append(item)
    return calls


def extract_anthropic_text_preview(payload: Any, limit: int = 160) -> str | None:
    if isinstance(payload, dict):
        content = payload.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                    return _text_preview(item["text"], limit=limit)
        return _text_preview(payload, limit=limit)
    return _text_preview(payload, limit=limit)


def collect_sse_events(lines: Iterator[str]) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    current: dict[str, list[str]] = {}
    for raw in lines:
        line = raw.rstrip("\n")
        if not line:
            if current:
                events.append({key: "\n".join(value) for key, value in current.items()})
                current = {}
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        current.setdefault(field, []).append(value.lstrip())
    if current:
        events.append({key: "\n".join(value) for key, value in current.items()})
    return events


def parse_sse_event_data(event: dict[str, str]) -> Any:
    data = event.get("data", "")
    try:
        return json.loads(data)
    except Exception:
        return data


def is_error_event(event: dict[str, str]) -> bool:
    data = parse_sse_event_data(event)
    if isinstance(data, dict):
        return data.get("type") in {"error", "response.error"} or "error" in data
    return False


def is_terminal_event(event: dict[str, str]) -> bool:
    data = parse_sse_event_data(event)
    if isinstance(data, dict):
        return data.get("type") in {"response.completed", "response.incomplete", "done", "completed"}
    return event.get("event") == "done"


def summarize_json(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"preview": _text_preview(payload) or type(payload).__name__}

    summary: dict[str, Any] = {}
    for key in ("id", "object", "type", "status", "model", "role", "stop_reason", "first_id", "last_id", "has_more"):
        if key in payload:
            summary[key] = payload[key]

    if isinstance(payload.get("data"), list):
        summary["data_count"] = len(payload["data"])
    if isinstance(payload.get("results"), list):
        summary["results_count"] = len(payload["results"])
    if isinstance(payload.get("output"), list):
        summary["output_count"] = len(payload["output"])
    if isinstance(payload.get("content"), list):
        summary["content_count"] = len(payload["content"])

    usage = payload.get("usage")
    if isinstance(usage, dict):
        summary["usage"] = usage

    preview = _text_preview(payload)
    if preview:
        summary["preview"] = preview
    if not summary:
        summary["keys"] = sorted(payload)[:12]
    return summary


def print_sse_preview(label: str, lines: Iterator[str], limit: int = 8) -> None:
    print(f"{label} SSE preview:")
    count = 0
    for line in lines:
        text = line.rstrip("\n")
        if text:
            print(f"  {redact(text)}")
        else:
            print("  ")
        count += 1
        if count >= limit:
            break


def _tiny_png_data_url() -> str:
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2bZ5kAAAAASUVORK5CYII="
    )
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")


def request_json(client: httpx.Client, method: str, path: str, *, verbose: bool = False, **kwargs: Any) -> httpx.Response:
    response = client.request(method, path, **kwargs)
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = response.json()
            if response.status_code >= 400 or verbose:
                print_json(f"{method} {path} -> {response.status_code}", payload)
            else:
                print_json(f"{method} {path} -> {response.status_code} summary", summarize_json(payload))
        except Exception:
            print(f"{method} {path} -> {response.status_code} (non-parseable JSON)")
    else:
        print(f"{method} {path} -> {response.status_code}")
    response.raise_for_status()
    return response


def request_stream_preview(client: httpx.Client, method: str, path: str, **kwargs: Any) -> None:
    with client.stream(method, path, **kwargs) as response:
        content_type = response.headers.get("content-type", "")
        print(f"{method} {path} -> {response.status_code} ({content_type or 'no content-type'})")
        response.raise_for_status()
        print_sse_preview(f"{method} {path}", response.iter_lines())


def maybe_call(label: str, fn) -> None:
    try:
        fn()
        print(f"[ok] {label}")
    except httpx.HTTPStatusError as exc:
        print(f"[fail] {label}: {exc.response.status_code} {redact(exc.response.text[:500])}")
    except httpx.HTTPError as exc:
        print(f"[fail] {label}: {redact(str(exc))}")


def expect_error(client: httpx.Client, label: str, method: str, path: str, *, status_codes: set[int], **kwargs: Any) -> None:
    try:
        response = client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        print(f"[fail] {label}: {redact(str(exc))}")
        return

    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            print_json(f"{method} {path} -> {response.status_code}", response.json())
        except Exception:
            print(f"{method} {path} -> {response.status_code} (non-parseable JSON)")
    else:
        print(f"{method} {path} -> {response.status_code}")

    if response.status_code in status_codes:
        print(f"[ok] {label}")
    else:
        print(f"[fail] {label}: expected {sorted(status_codes)}, got {response.status_code}")


def bad_auth_error(base_url: str, timeout: httpx.Timeout, label: str, method: str, path: str, *, status_codes: set[int], **kwargs: Any) -> None:
    with httpx.Client(base_url=base_url, headers={"Authorization": "Bearer [REDACTED]", "Content-Type": "application/json"}, timeout=timeout) as client:
        expect_error(client, label, method, path, status_codes=status_codes, **kwargs)


def main() -> int:
    base_url = env("FIREWORKS2API_BASE_URL", "http://127.0.0.1:8000")
    proxy_key = env("FIREWORKS2API_PROXY_KEY")
    if not proxy_key:
        print("FIREWORKS2API_PROXY_KEY is required for /v1/* smoke checks.")
        return 2

    chat_model = env("FIREWORKS2API_CHAT_MODEL", "kimi-k2.6")
    responses_model = env("FIREWORKS2API_RESPONSES_MODEL", chat_model or "kimi-k2.6")
    completions_model = env("FIREWORKS2API_COMPLETIONS_MODEL", chat_model or "kimi-k2.6")
    messages_model = env("FIREWORKS2API_MESSAGES_MODEL", chat_model or "kimi-k2.6")
    embeddings_model = env("FIREWORKS2API_EMBEDDINGS_MODEL")
    rerank_model = env("FIREWORKS2API_RERANK_MODEL")
    vision_model = env("FIREWORKS2API_VISION_MODEL")
    reasoning_model = env("FIREWORKS2API_REASONING_MODEL")
    smoke_stream = env_bool("FIREWORKS2API_SMOKE_STREAM", False)
    smoke_advanced = env_bool("FIREWORKS2API_SMOKE_ADVANCED", False)
    smoke_delete_response = env_bool("FIREWORKS2API_SMOKE_DELETE_RESPONSE", False)
    smoke_errors = env_bool("FIREWORKS2API_SMOKE_ERRORS", False)
    smoke_verbose = env_bool("FIREWORKS2API_SMOKE_VERBOSE", False)
    smoke_tools = env_bool("FIREWORKS2API_SMOKE_TOOLS", False)
    smoke_mcp = env_bool("FIREWORKS2API_SMOKE_MCP", False)
    smoke_anthropic_tools = env_bool("FIREWORKS2API_SMOKE_ANTHROPIC_TOOLS", False)
    anthropic_tool_model = env("FIREWORKS2API_ANTHROPIC_TOOL_MODEL", messages_model or "")
    smoke_strict_anthropic_tools = env_bool("FIREWORKS2API_SMOKE_STRICT_ANTHROPIC_TOOLS", False)
    smoke_strict_tool_signals = env_bool("FIREWORKS2API_SMOKE_STRICT_TOOL_SIGNALS", False)
    mcp_server_urls = parse_csv_env("FIREWORKS2API_MCP_SERVER_URLS")
    mcp_attempts = max(1, int(env("FIREWORKS2API_MCP_ATTEMPTS", "3") or "3"))
    mcp_timeout_seconds = float(env("FIREWORKS2API_MCP_TIMEOUT_SECONDS", "30") or "30")
    smoke_strict_mcp = env_bool("FIREWORKS2API_SMOKE_STRICT_MCP", False)
    smoke_images = env_bool("FIREWORKS2API_SMOKE_IMAGES", False)
    smoke_reasoning = env_bool("FIREWORKS2API_SMOKE_REASONING", False)
    mcp_server_url = env("FIREWORKS2API_MCP_SERVER_URL") or (mcp_server_urls[0] if mcp_server_urls else "")
    image_url = env("FIREWORKS2API_IMAGE_URL")
    responses_image_shape = env("FIREWORKS2API_RESPONSES_IMAGE_SHAPE", "image")

    headers = {"Authorization": f"Bearer {proxy_key}", "Content-Type": "application/json"}
    timeout = httpx.Timeout(float(env("FIREWORKS2API_SMOKE_TIMEOUT_SECONDS", "120")))

    with httpx.Client(base_url=base_url, headers=headers, timeout=timeout) as client:
        maybe_call("chat completions", lambda: request_json(client, "POST", "/v1/chat/completions", verbose=smoke_verbose, json={"model": chat_model, "messages": [{"role": "user", "content": "Say hello in one short sentence."}], "max_tokens": 32}))
        maybe_call("completions", lambda: request_json(client, "POST", "/v1/completions", verbose=smoke_verbose, json={"model": completions_model, "prompt": "Say hello in one short sentence.", "max_tokens": 32}))
        created_response_id: str | None = None

        def create_response_smoke() -> None:
            nonlocal created_response_id
            response = request_json(client, "POST", "/v1/responses", verbose=smoke_verbose, json={"model": responses_model, "input": "Say hello in one short sentence.", "store": True})
            try:
                payload = response.json()
            except Exception:
                payload = {}
            if isinstance(payload, dict) and isinstance(payload.get("id"), str):
                created_response_id = payload["id"]

        maybe_call("responses create", create_response_smoke)
        if created_response_id:
            maybe_call("responses get", lambda: request_json(client, "GET", f"/v1/responses/{created_response_id}", verbose=smoke_verbose))
            if smoke_delete_response:
                maybe_call("responses delete", lambda: request_json(client, "DELETE", f"/v1/responses/{created_response_id}", verbose=smoke_verbose))
            else:
                print("[skip] responses delete (FIREWORKS2API_SMOKE_DELETE_RESPONSE=false)")
        else:
            print("[skip] responses get/delete (create did not return an id)")
        maybe_call("responses list", lambda: request_json(client, "GET", "/v1/responses", verbose=smoke_verbose))

        if smoke_advanced:
            maybe_call(
                "chat completions advanced",
                lambda: request_json(
                    client,
                    "POST",
                    "/v1/chat/completions",
                    verbose=smoke_verbose,
                    json={
                        "model": chat_model,
                        "messages": [{"role": "user", "content": "Say hello in one short sentence."}],
                        "max_tokens": 32,
                        "metadata": {"smoke": "advanced"},
                        "response_format": {"type": "text"},
                    },
                ),
            )
            maybe_call(
                "responses advanced",
                lambda: request_json(
                    client,
                    "POST",
                    "/v1/responses",
                    verbose=smoke_verbose,
                    json={
                        "model": responses_model,
                        "input": "Say hello in one short sentence.",
                        "metadata": {"smoke": "advanced"},
                        "text": {"format": {"type": "text"}},
                    },
                ),
            )
            maybe_call(
                "messages advanced",
                lambda: request_json(
                    client,
                    "POST",
                    "/v1/messages",
                    verbose=smoke_verbose,
                    json={
                        "model": messages_model,
                        "messages": [{"role": "user", "content": "Say hello in one short sentence."}],
                        "max_tokens": 32,
                        "metadata": {"smoke": "advanced"},
                        "output_config": {"format": {"type": "text"}},
                        "thinking": {"type": "disabled"},
                    },
                ),
            )
        else:
            print("[skip] advanced smoke (FIREWORKS2API_SMOKE_ADVANCED=false)")

        if smoke_tools:
            if responses_model:
                def responses_tool_smoke() -> None:
                    first = request_json(
                        client,
                        "POST",
                        "/v1/responses",
                        verbose=smoke_verbose,
                        json={
                            "model": responses_model,
                            "input": "What is 6 * 7? Use the calculator tool.",
                            "tools": [
                                {
                                    "type": "function",
                                    "name": "calculator",
                                    "description": "Return the exact answer.",
                                    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                                }
                            ],
                            "tool_choice": "required",
                        },
                    )
                    payload = first.json()
                    assert first.status_code // 100 == 2
                    assert isinstance(payload.get("id"), str) and payload["id"]
                    call_items = extract_responses_tool_calls(payload)
                    assert call_items, "expected tool/function call output"
                    call_item = call_items[0]
                    call_id = extract_tool_call_id(call_item)
                    assert call_id, "expected usable tool call id/call_id"
                    tool_output = {"type": "tool_output", "tool_call_id": call_id, "output": json.dumps({"answer": "42"})}
                    cont = client.request(
                        "POST",
                        "/v1/responses",
                        json={"model": responses_model, "previous_response_id": payload["id"], "input": [tool_output]},
                    )
                    if cont.status_code == 400 and call_id:
                        print("[warn] tool_output continuation 400; retrying with function_call_output/call_id")
                        cont = client.request(
                            "POST",
                            "/v1/responses",
                            json={"model": responses_model, "previous_response_id": payload["id"], "input": [{"type": "function_call_output", "call_id": call_id, "output": json.dumps({"answer": "42"})}]},
                        )
                    cont.raise_for_status()
                    cont_payload = cont.json()
                    text = extract_responses_text(cont_payload)
                    assert "42" in text, text
                maybe_call("responses tools", responses_tool_smoke)
            else:
                print("[skip] responses tools (FIREWORKS2API_RESPONSES_MODEL not set)")
        else:
            print("[skip] responses tools (FIREWORKS2API_SMOKE_TOOLS=false)")

        if smoke_mcp:
            if responses_model:
                if mcp_server_url:
                    def responses_mcp_smoke() -> None:
                        first = request_json(
                            client,
                            "POST",
                            "/v1/responses",
                            verbose=smoke_verbose,
                            json={
                                "model": responses_model,
                                "input": "Use the docs tool and answer in one short sentence: what is reward-kit?",
                                "tools": [{"type": "sse", "server_label": "smoke", "server_url": mcp_server_url}],
                                "tool_choice": "required",
                                "max_tool_calls": 1,
                            },
                        )
                        payload = first.json()
                        assert isinstance(payload.get("id"), str) and payload["id"]
                        cont = request_json(
                            client,
                            "POST",
                            "/v1/responses",
                            verbose=smoke_verbose,
                            json={
                                "model": responses_model,
                                "previous_response_id": payload["id"],
                                "input": "Continue from the previous answer in one short sentence.",
                                "tools": [{"type": "sse", "server_label": "smoke", "server_url": mcp_server_url}],
                            },
                        )
                        text = extract_responses_text(cont.json())
                        assert text.strip()
                        if smoke_strict_tool_signals:
                            assert extract_responses_tool_calls(payload)
                    maybe_call("responses mcp", responses_mcp_smoke)
                else:
                    print("[skip] responses mcp (FIREWORKS2API_MCP_SERVER_URL not set)")
            else:
                print("[skip] responses mcp (FIREWORKS2API_RESPONSES_MODEL not set)")
        else:
            print("[skip] responses mcp (FIREWORKS2API_SMOKE_MCP=false)")

        if smoke_anthropic_tools:
            if anthropic_tool_model:
                def anthropic_tool_smoke() -> None:
                    first = request_json(
                        client,
                        "POST",
                        "/v1/messages",
                        verbose=smoke_verbose,
                        json={
                            "model": anthropic_tool_model,
                            "messages": [{"role": "user", "content": "Use the calculator tool now for 6*7. Do not answer in text before using the tool."}],
                            "tools": [{"name": "calculator", "description": "Return arithmetic answers. This tool must be used for multiplication requests.", "input_schema": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"], "additionalProperties": False}}],
                            "tool_choice": {"type": "tool", "name": "calculator"},
                            "thinking": {"type": "disabled"},
                            "max_tokens": 512,
                        },
                    )
                    payload = first.json()
                    tool_uses = extract_anthropic_tool_use(payload)
                    if not tool_uses:
                        msg = "[skip] anthropic tools (no tool_use returned)"
                        if smoke_strict_anthropic_tools:
                            raise AssertionError(msg)
                        print(msg)
                        return
                    tool_use_id = extract_anthropic_tool_use_id(tool_uses[0])
                    if not tool_use_id:
                        raise AssertionError("expected usable Anthropic tool_use id")
                    cont = request_json(
                        client,
                        "POST",
                        "/v1/messages",
                        verbose=smoke_verbose,
                        json={
                            "model": anthropic_tool_model,
                            "messages": [
                                {"role": "user", "content": "Use the calculator tool now for 6*7. Do not answer in text before using the tool."},
                                {"role": "assistant", "content": [{"type": "tool_use", "id": tool_use_id, "name": "calculator", "input": {"expression": "6*7"}}]},
                                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": "42"}]},
                            ],
                            "thinking": {"type": "disabled"},
                            "max_tokens": 256,
                        },
                    )
                    text = extract_anthropic_text_preview(cont.json()) or ""
                    if "42" not in text and smoke_strict_anthropic_tools:
                        raise AssertionError(f"expected 42 in continuation text, got {text!r}")
                maybe_call("anthropic tools", anthropic_tool_smoke)
            else:
                print("[skip] anthropic tools (FIREWORKS2API_MESSAGES_MODEL not set)")
        else:
            print("[skip] anthropic tools (FIREWORKS2API_SMOKE_ANTHROPIC_TOOLS=false)")

        if mcp_server_urls:
            def bounded_mcp_smoke() -> None:
                last_error: Exception | None = None
                for index, server_url in enumerate(mcp_server_urls[:mcp_attempts], start=1):
                    try:
                        with httpx.Client(base_url=base_url, headers=headers, timeout=httpx.Timeout(mcp_timeout_seconds)) as mcp_client:
                            first = request_json(mcp_client, "POST", "/v1/responses", verbose=smoke_verbose, json={"model": responses_model, "input": "Use the docs tool and answer in one short sentence.", "tools": [{"type": "sse", "server_label": f"smoke-{index}", "server_url": server_url}], "tool_choice": "required", "max_tool_calls": 1})
                        if extract_responses_tool_calls(first.json()):
                            return
                    except Exception as exc:
                        last_error = exc
                if smoke_strict_mcp and last_error:
                    raise last_error
            maybe_call("bounded mcp", bounded_mcp_smoke)

        if smoke_images:
            if vision_model:
                maybe_call(
                    "multimodal chat images",
                    lambda: request_json(client, "POST", "/v1/chat/completions", verbose=smoke_verbose, json={"model": vision_model, "messages": [{"role": "user", "content": [{"type": "text", "text": "Describe this image."}, {"type": "image_url", "image_url": {"url": image_url or _tiny_png_data_url()}}]}], "max_tokens": 32}),
                )
                if image_url:
                    maybe_call(
                        "responses image",
                        lambda: request_json(
                            client,
                            "POST",
                            "/v1/responses",
                            verbose=smoke_verbose,
                            json={
                                "model": vision_model,
                                "input": [
                                    {
                                        "role": "user",
                                        "content": [
                                            {"type": "input_text", "text": "Describe this image briefly."},
                                            {"type": responses_image_shape, "image_url": {"url": image_url, "detail": "low"}},
                                        ],
                                    }
                                ],
                            },
                        ),
                    )
                    maybe_call(
                        "anthropic image url",
                        lambda: request_json(client, "POST", "/v1/messages", verbose=smoke_verbose, json={"model": vision_model, "messages": [{"role": "user", "content": [{"type": "text", "text": "Describe this image."}, {"type": "image", "source": {"type": "url", "url": image_url}}]}], "max_tokens": 32}),
                    )
                maybe_call(
                    "anthropic image base64",
                    lambda: request_json(client, "POST", "/v1/messages", verbose=smoke_verbose, json={"model": vision_model, "messages": [{"role": "user", "content": [{"type": "text", "text": "Describe this image."}, {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _tiny_png_data_url().split(",", 1)[1]}}]}], "max_tokens": 32}),
                )
            else:
                print("[skip] multimodal images (FIREWORKS2API_VISION_MODEL not set)")
        else:
            print("[skip] multimodal images (FIREWORKS2API_SMOKE_IMAGES=false)")

        if smoke_reasoning:
            if reasoning_model:
                maybe_call(
                    "reasoning smoke",
                    lambda: request_json(
                        client,
                        "POST",
                        "/v1/chat/completions",
                        verbose=smoke_verbose,
                        json={
                            "model": reasoning_model,
                            "messages": [{"role": "user", "content": "Say hello in one short sentence."}],
                            "max_tokens": 64,
                            "reasoning_effort": "low",
                        },
                    ),
                )
            else:
                print("[skip] reasoning smoke (FIREWORKS2API_REASONING_MODEL not set)")
        else:
            print("[skip] reasoning smoke (FIREWORKS2API_SMOKE_REASONING=false)")

        if smoke_stream:
            maybe_call(
                "chat completions stream",
                lambda: request_stream_preview(
                    client,
                    "POST",
                    "/v1/chat/completions",
                    json={"model": chat_model, "messages": [{"role": "user", "content": "Say hello in one short sentence."}], "max_tokens": 32, "stream": True},
                ),
            )
            maybe_call(
                "completions stream",
                lambda: request_stream_preview(
                    client,
                    "POST",
                    "/v1/completions",
                    json={"model": completions_model, "prompt": "Say hello in one short sentence.", "max_tokens": 32, "stream": True},
                ),
            )
            maybe_call(
                "responses stream",
                lambda: request_stream_preview(
                    client,
                    "POST",
                    "/v1/responses",
                    json={"model": responses_model, "input": "Say hello in one short sentence.", "stream": True},
                ),
            )
            maybe_call(
                "messages stream",
                lambda: request_stream_preview(
                    client,
                    "POST",
                    "/v1/messages",
                    json={"model": messages_model, "messages": [{"role": "user", "content": "Say hello in one short sentence."}], "max_tokens": 32, "stream": True},
                ),
            )
        else:
            print("[skip] streaming smoke (FIREWORKS2API_SMOKE_STREAM=false)")

        if embeddings_model:
            maybe_call("embeddings", lambda: request_json(client, "POST", "/v1/embeddings", verbose=smoke_verbose, json={"model": embeddings_model, "input": ["hello", "world"]}))
        else:
            print("[skip] embeddings (FIREWORKS2API_EMBEDDINGS_MODEL not set)")

        if rerank_model:
            maybe_call("rerank", lambda: request_json(client, "POST", "/v1/rerank", verbose=smoke_verbose, json={"model": rerank_model, "query": "hello", "documents": ["hello world", "goodbye world"]}))
        else:
            print("[skip] rerank (FIREWORKS2API_RERANK_MODEL not set)")

        maybe_call("messages", lambda: request_json(client, "POST", "/v1/messages", verbose=smoke_verbose, json={"model": messages_model, "messages": [{"role": "user", "content": "Say hello in one short sentence."}], "max_tokens": 32}))

        if smoke_errors:
            expect_error(
                client,
                "local invalid schema error",
                "POST",
                "/v1/chat/completions",
                status_codes={400},
                json={"model": chat_model, "messages": [{"role": "user", "content": "Say hello in one short sentence."}], "max_tokens": "bad"},
            )
            expect_error(
                client,
                "local unsupported field error",
                "POST",
                "/v1/chat/completions",
                status_codes={400},
                json={
                    "model": chat_model,
                    "messages": [{"role": "user", "content": "Say hello in one short sentence."}],
                    "max_tokens": 32,
                    "nonexistent_local_field": True,
                },
            )
            bad_auth_error(
                base_url,
                timeout,
                "local bad auth error",
                "POST",
                "/v1/chat/completions",
                status_codes={401},
                json={"model": chat_model, "messages": [{"role": "user", "content": "Say hello in one short sentence."}], "max_tokens": 32},
            )
            expect_error(
                client,
                "unknown local model alias",
                "POST",
                "/v1/chat/completions",
                status_codes={400, 404},
                json={"model": "__unknown_local_alias__", "messages": [{"role": "user", "content": "Say hello in one short sentence."}], "max_tokens": 32},
            )
        else:
            print("[skip] smoke errors (FIREWORKS2API_SMOKE_ERRORS=false)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
