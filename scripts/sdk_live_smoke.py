from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass
from typing import Any

import httpx


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def redact(text: Any) -> str:
    value = str(text)
    for token in (env("FIREWORKS2API_PROXY_KEY"), env("FIREWORKS2API_BASE_URL")):
        if token:
            value = value.replace(token, "[REDACTED]")
    return value


@dataclass
class SDKLoad:
    module: Any | None
    reason: str | None = None


def load_sdk(name: str) -> SDKLoad:
    try:
        return SDKLoad(importlib.import_module(name))
    except Exception as exc:  # noqa: BLE001
        return SDKLoad(None, f"missing {name} ({exc.__class__.__name__})")


def _client(base_url: str, proxy_key: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {proxy_key}", "Content-Type": "application/json"}, timeout=httpx.Timeout(120.0))


def _summary(label: str, response: httpx.Response) -> None:
    payload = None
    if isinstance(response, dict):
        payload = response
    else:
        try:
            payload = response.json()
        except Exception:
            payload = None
    if isinstance(payload, dict):
        summary = {k: payload.get(k) for k in ("id", "object", "type", "model", "status") if k in payload}
        print(f"[ok] {label}: {summary or {'keys': sorted(payload)[:8]}}")
    else:
        status = getattr(response, "status_code", "unknown")
        print(f"[ok] {label}: status={status}")


def _maybe_stream(client: httpx.Client, method: str, path: str, *, label: str, **kwargs: Any) -> None:
    with client.stream(method, path, **kwargs) as response:
        response.raise_for_status()
        print(f"[ok] {label}: stream status={response.status_code}, content-type={response.headers.get('content-type', '')}")


def run_openai(base_url: str, proxy_key: str, chat_model: str, responses_model: str, stream: bool) -> None:
    sdk = load_sdk("openai")
    if sdk.module is None:
        print(f"[skip] openai sdk: {sdk.reason}")
        return
    if not hasattr(sdk.module, "AsyncOpenAI") and not hasattr(sdk.module, "OpenAI"):
        print("[skip] openai sdk: AsyncOpenAI/OpenAI client not available")
        return

    client = _client(base_url, proxy_key)
    try:
        openai = getattr(sdk.module, "OpenAI", None)
        if openai is not None:
            cli = openai(api_key=proxy_key, base_url=f"{base_url.rstrip('/')}/v1", http_client=client)
            chat = cli.chat.completions.create(model=chat_model, messages=[{"role": "user", "content": "Say hello in one short sentence."}])
            _summary("openai chat completions", chat)
            if stream:
                _maybe_stream(client, "POST", "/v1/chat/completions", label="openai chat completions stream", json={"model": chat_model, "messages": [{"role": "user", "content": "Say hello in one short sentence."}], "stream": True})
            if hasattr(cli, "responses") and hasattr(cli.responses, "create"):
                resp = cli.responses.create(model=responses_model, input="Say hello in one short sentence.")
                _summary("openai responses", resp)
        else:
            async_openai = sdk.module.AsyncOpenAI(api_key=proxy_key, base_url=f"{base_url.rstrip('/')}/v1", http_client=client)
            chat = async_openai.chat.completions.create(model=chat_model, messages=[{"role": "user", "content": "Say hello in one short sentence."}])
            _summary("openai chat completions", chat)
    finally:
        client.close()


def run_anthropic(base_url: str, proxy_key: str, messages_model: str, stream: bool) -> None:
    sdk = load_sdk("anthropic")
    if sdk.module is None:
        print(f"[skip] anthropic sdk: {sdk.reason}")
        return
    if not hasattr(sdk.module, "Anthropic") and not hasattr(sdk.module, "AsyncAnthropic"):
        print("[skip] anthropic sdk: Anthropic/AsyncAnthropic client not available")
        return
    client = _client(base_url, proxy_key)
    try:
        anthropic = getattr(sdk.module, "Anthropic", None)
        if anthropic is not None:
            cli = anthropic(api_key=proxy_key, base_url=base_url.rstrip("/"), http_client=client)
            msg = cli.messages.create(model=messages_model, max_tokens=16, messages=[{"role": "user", "content": "Say hello in one short sentence."}])
            _summary("anthropic messages", msg)
            if stream:
                _maybe_stream(client, "POST", "/v1/messages", label="anthropic messages stream", headers={"anthropic-version": "2023-06-01"}, json={"model": messages_model, "max_tokens": 16, "stream": True, "messages": [{"role": "user", "content": "Say hello in one short sentence."}]})
    finally:
        client.close()


def main() -> int:
    base_url = env("FIREWORKS2API_BASE_URL")
    proxy_key = env("FIREWORKS2API_PROXY_KEY")
    chat_model = env("FIREWORKS2API_CHAT_MODEL") or "kimi-k2.6"
    responses_model = env("FIREWORKS2API_RESPONSES_MODEL", chat_model)
    messages_model = env("FIREWORKS2API_MESSAGES_MODEL", chat_model)
    stream = env_bool("FIREWORKS2API_SDK_SMOKE_STREAM", False)
    if not base_url or not proxy_key:
        print("[skip] set FIREWORKS2API_BASE_URL and FIREWORKS2API_PROXY_KEY to run SDK live smoke")
        return 0
    if not chat_model or not responses_model or not messages_model:
        print("[skip] set FIREWORKS2API_CHAT_MODEL, FIREWORKS2API_RESPONSES_MODEL, and FIREWORKS2API_MESSAGES_MODEL")
        return 0
    run_openai(base_url, proxy_key, chat_model, responses_model, stream)
    run_anthropic(base_url, proxy_key, messages_model, stream)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
