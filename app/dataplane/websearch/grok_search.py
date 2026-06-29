"""Grok web search backend.

Implements the search call that powers the server-side ``web_search`` built-in tool:
a streaming POST to a Grok OpenAI-compatible ``/chat/completions`` endpoint using a
search-oriented system prompt, followed by answer/source splitting. This is a lean
port of ``GuDaStudio/GrokSearch``'s ``GrokSearchProvider.search`` (MIT) — no MCP,
FastMCP, or tenacity dependency is required.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import httpx

from app.dataplane.websearch.prompts import SEARCH_PROMPT, get_local_time_info, needs_time_context
from app.dataplane.websearch.sources import split_answer_and_sources

if TYPE_CHECKING:
    from app.platform.config import Settings

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def _grok_endpoint(settings: "Settings") -> str:
    base = (settings.grok_api_url or "").rstrip("/")
    if not base:
        raise ValueError("grok_api_url is not configured")
    return f"{base}/chat/completions"


async def _parse_streaming_response(response: httpx.Response) -> str:
    """Concatenate streamed ``delta.content`` chunks into the full answer text.

    Tolerates both ``data: {...}`` and ``data:{...}`` SSE framing. If no SSE chunks
    yield content, attempts to parse the full body as a non-streaming completion.
    """
    content = ""
    full_body_buffer: list[str] = []
    async for line in response.aiter_lines():
        line = line.strip()
        if not line:
            continue
        full_body_buffer.append(line)
        if not line.startswith("data:"):
            continue
        payload_text = line[5:].lstrip()
        if payload_text == "[DONE]":
            continue
        try:
            data = json.loads(payload_text)
        except json.JSONDecodeError:
            continue
        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            continue
        delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
        if isinstance(delta, dict) and isinstance(delta.get("content"), str):
            content += delta["content"]

    if not content and full_body_buffer:
        try:
            data = json.loads("".join(full_body_buffer))
            choices = data.get("choices") if isinstance(data, dict) else None
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                message = choices[0].get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    content = message["content"]
        except json.JSONDecodeError:
            pass
    return content


async def _stream_chat_completion(
    *, endpoint: str, api_key: str, payload: dict, timeout: httpx.Timeout
) -> str:
    """Issue one streaming completion attempt, raising on retryable failures.

    Raises ``httpx.HTTPStatusError`` for non-2xx so the caller can decide to retry;
    the response body is consumed and closed on error.
    """
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("POST", endpoint, headers=headers, json=payload) as response:
            if response.status_code >= 400:
                # Drain so the connection can be reused/closed cleanly.
                await response.aread()
                response.raise_for_status()
            return await _parse_streaming_response(response)


async def grok_web_search(
    settings: "Settings",
    query: str,
    *,
    platform: str = "",
) -> tuple[str, list[dict]]:
    """Run a web search via Grok and return ``(answer_text, sources_list)``.

    ``sources_list`` is a list of ``{"url": str, "title"?: str, "description"?: str}``.
    On any unrecoverable failure the answer text describes the error and the source
    list is empty, so the calling agent loop can feed a graceful degradation back to
    the Fireworks model instead of aborting the whole request.
    """
    api_key = settings.grok_api_key
    if not api_key:
        raise ValueError("grok_api_key is not configured")

    endpoint = _grok_endpoint(settings)
    platform_prompt = ""
    if platform:
        platform_prompt = "\n\nFocus the search on this platform: " + platform + "\n"
    user_content = query + platform_prompt
    if needs_time_context(query):
        user_content = get_local_time_info() + "\n" + user_content

    payload = {
        "model": settings.grok_model,
        "messages": [
            {"role": "system", "content": SEARCH_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "stream": True,
    }
    timeout = httpx.Timeout(
        connect=6.0,
        read=max(10.0, settings.web_search_timeout_seconds),
        write=10.0,
        pool=None,
    )

    max_attempts = max(1, settings.web_search_max_iterations)
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            text = await _stream_chat_completion(
                endpoint=endpoint, api_key=api_key, payload=payload, timeout=timeout
            )
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if exc.response.status_code in _RETRYABLE_STATUS and attempt + 1 < max_attempts:
                await asyncio.sleep(min(2.0 * (attempt + 1), 10.0))
                continue
            logger.warning("grok web search HTTP %s: %s", exc.response.status_code, exc.response.text[:200])
            return f"[web search failed: upstream returned HTTP {exc.response.status_code}]", []
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            last_error = exc
            if attempt + 1 < max_attempts:
                await asyncio.sleep(min(2.0 * (attempt + 1), 10.0))
                continue
            logger.warning("grok web search network error: %s", exc)
            return f"[web search failed: {exc.__class__.__name__}]", []

        answer, sources = split_answer_and_sources(text)
        if not answer.strip() and not sources:
            # Empty response — retry once more if budget allows.
            if attempt + 1 < max_attempts:
                await asyncio.sleep(min(2.0 * (attempt + 1), 10.0))
                continue
        return answer, sources

    return f"[web search failed: {last_error}]", []
