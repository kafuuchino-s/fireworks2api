"""Server-side agentic loop that implements the OpenAI Responses API built-in
``web_search`` tool for Fireworks models.

Fireworks does not host a web search tool, so when a client sends
``tools=[{"type": "web_search"}]`` to ``/v1/responses`` the proxy:

1. strips the ``web_search`` built-in tool from the request,
2. injects an equivalent ``function`` tool (``web_search(query)``) that the Fireworks
   model can decide to call,
3. on each Fireworks response, looks for a ``function_call`` output item whose name is
   ``web_search``, runs the search via the Grok backend, and feeds the result back as a
   ``function_call_output`` input item for the next Fireworks call,
4. once the model produces a final answer (no further ``web_search`` call), it decorates
   the response with OpenAI-style ``web_search_call`` items and a sources block.

Only the non-streaming path is implemented. A streaming request that carries
``web_search`` is transparently downgraded to a non-streaming run and returned as a
single JSON response.
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any

from app.dataplane.fireworks.client import FireworksClient
from app.dataplane.websearch.grok_search import grok_web_search
from app.products.openai.errors import OpenAIRequestError

logger = logging.getLogger(__name__)

_WEB_SEARCH_TOOL_TYPES = {"web_search", "web_search_preview"}
_WEB_SEARCH_FUNCTION = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the public web for up-to-date information. Use this for recent "
            "events, current facts, documentation, or anything that may have changed "
            "after the training cutoff. Returns a concise textual summary plus a list "
            "of source URLs."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A clear, self-contained natural-language search query.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}


def _extract_web_search_tools(tools: list[Any]) -> list[dict[str, Any]]:
    return [
        tool for tool in tools
        if isinstance(tool, dict) and tool.get("type") in _WEB_SEARCH_TOOL_TYPES
    ]


def _contains_web_search(body: dict[str, Any]) -> bool:
    tools = body.get("tools")
    if not isinstance(tools, list):
        return False
    return bool(_extract_web_search_tools(tools))


def _strip_web_search_tools(tools: list[Any]) -> list[Any]:
    return [
        tool for tool in tools
        if not (isinstance(tool, dict) and tool.get("type") in _WEB_SEARCH_TOOL_TYPES)
    ]


def _tool_choice_for_search(body: dict[str, Any]) -> Any:
    choice = body.get("tool_choice")
    # Respect an explicit "none" (client asked not to use any tools), otherwise let the
    # model decide. String "auto"/"required" pass through; objects pass through.
    if isinstance(choice, str) and choice.strip() == "none":
        return "none"
    if choice is None:
        return "auto"
    return choice


def _find_web_search_calls(output: list[Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    if not isinstance(output, list):
        return calls
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "function_call":
            continue
        name = item.get("name")
        if isinstance(name, str) and name == "web_search":
            calls.append(item)
    return calls


def _parse_query(call: dict[str, Any]) -> str | None:
    arguments = call.get("arguments")
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return arguments.strip() or None
    elif isinstance(arguments, dict):
        parsed = arguments
    else:
        return None
    query = parsed.get("query") if isinstance(parsed, dict) else None
    if isinstance(query, str) and query.strip():
        return query.strip()
    return None


def _platform_from_search_tool(search_tools: list[dict[str, Any]]) -> str:
    # OpenAI web_search has no "platform" concept; reserved for future mapping.
    return ""


def _format_sources_block(sources: list[dict[str, Any]]) -> str:
    lines: list[str] = ["Sources:"]
    for source in sources:
        url = (source.get("url") or "").strip()
        if not url:
            continue
        title = (source.get("title") or "").strip()
        if title:
            lines.append(f"- [{title}]({url})")
        else:
            lines.append(f"- {url}")
    return "\n".join(lines)


def _build_web_search_call_items(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for run in runs:
        queries = [run["query"]] if run.get("query") else []
        sources = run.get("sources") or []
        item: dict[str, Any] = {
            "type": "web_search_call",
            "id": run.get("id") or f"ws_{secrets.token_hex(12)}",
            "queries": queries,
        }
        if sources:
            item["sources"] = [
                {
                    "url": s.get("url"),
                    **({"title": s["title"]} if s.get("title") else {}),
                }
                for s in sources
                if isinstance(s, dict) and s.get("url")
            ]
        items.append(item)
    return items


def _inject_search_results_into_response(
    response: dict[str, Any], runs: list[dict[str, Any]]
) -> dict[str, Any]:
    """Decorate a final Fireworks Responses object with web_search_call items.

    The ``web_search_call`` items are inserted at the head of ``output`` (mirroring
    OpenAI ordering), and a compact sources list is appended to the assistant message's
    output_text so plain-text clients still see citations.
    """
    if not runs:
        return response
    response = dict(response)
    output = response.get("output")
    if not isinstance(output, list):
        output = []
    web_search_items = _build_web_search_call_items(runs)
    all_sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for run in runs:
        for source in run.get("sources") or []:
            url = (source.get("url") or "").strip() if isinstance(source, dict) else ""
            if url and url not in seen:
                seen.add(url)
                all_sources.append(source)

    new_output: list[Any] = list(web_search_items)
    for item in output:
        if isinstance(item, dict) and item.get("type") == "message":
            content = item.get("content")
            if isinstance(content, list) and all_sources:
                content = list(content)
                for part_index, part in enumerate(content):
                    if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                        block = _format_sources_block(all_sources)
                        content[part_index] = {**part, "text": part["text"].rstrip() + "\n\n" + block}
                item = {**item, "content": content}
        new_output.append(item)
    response["output"] = new_output
    return response


async def _call_fireworks_responses(
    context, *, upstream_path: str, payload: dict[str, Any], headers: dict[str, str]
) -> dict[str, Any]:
    """One non-streaming Fireworks Responses call using the first selected key.

    Mirrors the happy-path of ``proxy_fireworks_request`` for the non-streaming case
    enough to return the parsed response body. Raises ``OpenAIRequestError`` on any
    upstream failure so the loop can surface a clean OpenAI-style error.
    """
    if not context.selected_keys:
        raise OpenAIRequestError("upstream unavailable", code="invalid_request_error", status_code=503)
    key = context.selected_keys[0]
    client = FireworksClient(context.settings, key.api_key)
    try:
        response = await client.post_json(upstream_path, payload, headers=headers)
        body_text = (await response.aread()).decode("utf-8", errors="ignore")
        if response.status_code >= 400:
            raise OpenAIRequestError(
                f"upstream responses call failed (HTTP {response.status_code})",
                code="invalid_request_error",
                status_code=response.status_code,
            )
        try:
            parsed = json.loads(body_text)
        except json.JSONDecodeError as exc:
            raise OpenAIRequestError("upstream returned invalid JSON", code="invalid_request_error") from exc
        return parsed
    finally:
        await client.aclose()


async def run_responses_web_search_loop(
    context,
    body: dict[str, Any],
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    upstream_path: str,
) -> dict[str, Any] | None:
    """Run the server-side web_search loop, or return ``None`` to defer to normal proxying.

    Returns ``None`` when the request does not carry a ``web_search`` built-in tool, so
    callers can keep the existing passthrough path untouched (zero regression risk).
    On success returns the final Responses object (already decorated with
    ``web_search_call`` items + sources). Raises ``OpenAIRequestError`` for
    configuration / upstream errors so the route handler can emit an OpenAI-style error.
    """
    if not _contains_web_search(body):
        return None

    settings = context.settings
    if not getattr(settings, "web_search_enabled", False):
        raise OpenAIRequestError(
            "web_search tool is not enabled on this proxy",
            param="tools",
            code="unsupported_parameter",
        )
    if not settings.grok_api_key:
        raise OpenAIRequestError(
            "web_search tool requires grok_api_key configuration",
            param="tools",
            code="unsupported_parameter",
        )

    search_tools = _extract_web_search_tools(body.get("tools") or [])
    platform = _platform_from_search_tool(search_tools)

    # Build the forwarded payload: drop built-in web_search tools, inject the function
    # tool, and force a non-streaming run (the loop is non-streaming by design).
    forwarded = dict(payload)
    remaining_tools = _strip_web_search_tools(payload.get("tools") or [])
    forwarded["tools"] = [*remaining_tools, _WEB_SEARCH_FUNCTION]
    forwarded["tool_choice"] = _tool_choice_for_search(body)
    forwarded["stream"] = False
    forwarded.pop("stream_options", None)
    # Continuation storage is not needed for the non-streaming loop.
    forwarded.pop("store", None)

    max_iterations = max(1, int(getattr(settings, "web_search_max_iterations", 3)))
    runs: list[dict[str, Any]] = []

    for _iteration in range(max_iterations):
        parsed = await _call_fireworks_responses(
            context, upstream_path=upstream_path, payload=forwarded, headers=headers
        )
        output = parsed.get("output") if isinstance(parsed, dict) else None
        calls = _find_web_search_calls(output if isinstance(output, list) else [])
        if not calls:
            return _inject_search_results_into_response(parsed, runs)

        # Bind the upstream response id to the selected key so any client-side
        # previous_response_id continuation lands on the same key.
        response_id = parsed.get("id") if isinstance(parsed.get("id"), str) else None
        if response_id and context.selected_keys:
            bind = getattr(context.repository, "upsert_response_key_route", None)
            if callable(bind):
                bind(response_id, context.selected_keys[0])

        input_items = forwarded.get("input")
        if not isinstance(input_items, list):
            input_items = []
        # Carry over the assistant's full output so reasoning + the tool call are part of
        # the next request's context, then append each function_call_output.
        input_items = [*input_items, *output]
        for call in calls:
            call_id = call.get("call_id") if isinstance(call.get("call_id"), str) else None
            query = _parse_query(call)
            run_id = f"ws_{secrets.token_hex(12)}"
            if not query:
                answer, sources = "[web_search skipped: empty query]", []
            else:
                try:
                    answer, sources = await grok_web_search(settings, query, platform=platform)
                except Exception as exc:  # noqa: BLE001 — never let the loop crash the request
                    logger.warning("grok web search failed for query %r: %s", query, exc)
                    answer, sources = f"[web search failed: {exc}]", []
            runs.append({"id": run_id, "query": query or "", "sources": sources})
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": answer,
                }
            )
        forwarded = dict(forwarded)
        forwarded["input"] = input_items
        # Avoid re-binding previous_response_id across loop iterations; we replay context
        # explicitly via input items.
        forwarded.pop("previous_response_id", None)

    # Exhausted iterations: return the last response we have, decorated with whatever
    # searches did run.
    return _inject_search_results_into_response(parsed, runs)


__all__ = ["run_responses_web_search_loop"]
