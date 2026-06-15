from __future__ import annotations

import time
import json
from typing import Any, Callable, Mapping

import httpx
from fastapi import HTTPException, status
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.dataplane.usage import UsageStats, extract_usage, extract_usage_from_headers, maybe_estimate_usage, merge_usage
from app.dataplane.fireworks.client import FireworksClient
from app.dataplane.fireworks.stream_proxy import StreamUsageCollector, build_passthrough_response, build_passthrough_stream_response
from app.dataplane.routing.failover import AppliedFailure, apply_failure_to_candidate, apply_failure_to_key, classify_upstream_failure

from app.dataplane.fireworks.context import ProxyRequestContext
from app.dataplane.fireworks.logging import prepare_log_payload


async def close_quietly(*objects: Any) -> None:
    for obj in objects:
        if obj is None:
            continue
        close = getattr(obj, "aclose", None)
        if close is None:
            continue
        try:
            await close()
        except Exception:
            continue


async def read_response_text(response: httpx.Response) -> str:
    return (await response.aread()).decode("utf-8", errors="ignore")


def safe_upstream_request_id(headers: Mapping[str, Any]) -> str | None:
    for name in ("x-request-id", "request-id", "fireworks-request-id", "x-fireworks-request-id"):
        value = headers.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def failover_on_error(repository, key_name: str, status_code: int | None, body_text: str | None = None, exc: Exception | None = None) -> tuple[str, bool]:
    decision = classify_upstream_failure(status_code, body_text=body_text, exception=exc)
    apply_failure_to_key(repository, key_name, decision)
    return decision.error_type, decision.retryable


def apply_candidate_failure(context, key, status_code: int | None, body_text: str | None = None, exc: Exception | None = None) -> AppliedFailure:
    decision = classify_upstream_failure(status_code, body_text=body_text, exception=exc)
    return apply_failure_to_candidate(context.repository, key, decision)


def _merge_request_usage(response: httpx.Response, body_usage: UsageStats) -> UsageStats:
    return merge_usage(body_usage, extract_usage_from_headers(response.headers))


def _response_id_from_body(body_text: str) -> str | None:
    try:
        payload = json.loads(body_text)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and isinstance(payload.get("id"), str) and payload["id"].strip():
        return payload["id"].strip()
    return None


def _record_transform_debug_if_enabled(
    context: ProxyRequestContext,
    route_trace: dict[str, Any] | None,
    result: dict[str, Any],
    *,
    routing_events: list[dict[str, Any]] | None = None,
    blocked_account_ids: set[str] | None = None,
) -> None:
    if not route_trace or not getattr(context.settings, "transform_debug_enabled", False):
        return
    completed = dict(route_trace)
    completed["result"] = _route_result_with_routing(context, result, routing_events or [], blocked_account_ids)
    context.repository.record_transform_debug({"route_trace": completed}, getattr(context.settings, "transform_debug_retention", 0) or 0)


def _route_trace_result(*, request_log_id: str | None, status_code: int, error_type: str | None, latency_ms: int | None, upstream_request_id: str | None, selected_key, usage: UsageStats) -> dict[str, Any]:
    return {
        "request_log_id": request_log_id,
        "status_code": status_code,
        "error_type": error_type,
        "latency_ms": latency_ms,
        "upstream_request_id": upstream_request_id,
        "selected_key_fingerprint": getattr(selected_key, "fingerprint", None),
        "usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cached_tokens": usage.cached_tokens,
            "estimated": usage.estimated,
        },
    }


def _routing_observation(context: ProxyRequestContext) -> dict[str, Any]:
    metadata = getattr(context, "routing_metadata", None)
    if isinstance(metadata, Mapping):
        return {
            "mode": metadata.get("routing_mode"),
            "primary_account_bucket": metadata.get("primary_account_bucket"),
            "selected_account_count": metadata.get("selected_account_count"),
            "skipped_account_count": metadata.get("skipped_account_count"),
            "selected_key_count": metadata.get("selected_key_count"),
        }
    return {}


def _safe_account_id_for_key(context: ProxyRequestContext, key: Any) -> str | None:
    snapshot = context.repository.get_fireworks_key_snapshot(getattr(key, "fingerprint", "")) if hasattr(context.repository, "get_fireworks_key_snapshot") else None
    account_id = getattr(snapshot, "account_id", None) if snapshot else None
    return str(account_id).strip() if account_id else None


def _routing_event(key: Any, account_id: str | None, *, action: str, error_type: str | None = None, scope: str | None = None) -> dict[str, Any]:
    event: dict[str, Any] = {
        "action": action,
        "key_fingerprint": getattr(key, "fingerprint", None),
    }
    if account_id:
        event["account_bucket"] = f"account:{account_id}"
    if error_type:
        event["error_type"] = error_type
    if scope:
        event["scope"] = scope
    return event


def _route_result_with_routing(context: ProxyRequestContext, base: dict[str, Any], routing_events: list[dict[str, Any]], blocked_account_ids: set[str] | None = None) -> dict[str, Any]:
    result = dict(base)
    routing = _routing_observation(context)
    if routing_events:
        routing["attempts"] = list(routing_events)
    if blocked_account_ids:
        routing["blocked_account_buckets"] = [f"account:{account_id}" for account_id in sorted(blocked_account_ids)]
    if routing:
        result["routing"] = routing
    return result


async def proxy_fireworks_request(
    context: ProxyRequestContext,
    *,
    endpoint: str,
    upstream_path: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    route_trace: dict[str, Any] | None = None,
    response_transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    stream_transform_factory: Callable[[], Any] | None = None,
    bind_response_key_route: bool = True,
    response_id_callback: Callable[[str, Any], None] | None = None,
    retry_payload_on_error: Callable[[int, str], dict[str, Any] | None] | None = None,
) -> Response | StreamingResponse:
    stream = bool(payload.get("stream"))
    service_tier = payload.get("service_tier") if isinstance(payload.get("service_tier"), str) else None
    started_at = time.perf_counter()
    if not context.selected_keys:
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        request_log_id = context.repository.insert_request_log(prepare_log_payload(endpoint=endpoint, context=context, selected_key=None, stream=stream, service_tier=service_tier, usage=UsageStats(), latency_ms=latency_ms, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, error_type="no_healthy_keys", upstream_request_id=None), context.settings.request_log_retention)
        _record_transform_debug_if_enabled(context, route_trace, _route_trace_result(request_log_id=request_log_id, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, error_type="no_healthy_keys", latency_ms=latency_ms, upstream_request_id=None, selected_key=None, usage=UsageStats()))
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="upstream unavailable")
    last_error_type = None
    last_key = None
    blocked_account_ids: set[str] = set()
    routing_events: list[dict[str, Any]] = []
    for index, key in enumerate(context.selected_keys):
        account_id = _safe_account_id_for_key(context, key)
        if account_id and account_id in blocked_account_ids:
            routing_events.append(_routing_event(key, account_id, action="skip", scope="account"))
            continue
        routing_events.append(_routing_event(key, account_id, action="attempt"))
        client = FireworksClient(context.settings, key.api_key)
        response = None
        keep_open = False
        try:
            if stream:
                response = await client.post_stream(upstream_path, payload, headers=headers)
                last_key = key
                if response.status_code >= 400:
                    body_text = await read_response_text(response)
                    retry_payload = retry_payload_on_error(response.status_code, body_text) if retry_payload_on_error is not None else None
                    if retry_payload is not None:
                        await close_quietly(response)
                        response = await client.post_stream(upstream_path, retry_payload, headers=headers)
                        if response.status_code < 400:
                            payload = retry_payload
                            last_key = key
                        else:
                            body_text = await read_response_text(response)
                    if response.status_code >= 400:
                        applied = apply_candidate_failure(context, key, response.status_code, body_text=body_text)
                        last_error_type = applied.error_type
                        if applied.scope == "account" and applied.account_id:
                            blocked_account_ids.add(applied.account_id)
                        routing_events.append(_routing_event(key, account_id, action="failover", error_type=applied.error_type, scope=applied.scope))
                        if applied.retryable and index + 1 < len(context.selected_keys):
                            continue
                        if applied.retryable:
                            latency_ms = int((time.perf_counter() - started_at) * 1000)
                            request_log_id = context.repository.insert_request_log(prepare_log_payload(endpoint=endpoint, context=context, selected_key=key, stream=True, service_tier=service_tier, usage=UsageStats(), latency_ms=latency_ms, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, error_type=applied.error_type, upstream_request_id=safe_upstream_request_id(response.headers)), context.settings.request_log_retention)
                            _record_transform_debug_if_enabled(context, route_trace, _route_trace_result(request_log_id=request_log_id, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, error_type=applied.error_type, latency_ms=latency_ms, upstream_request_id=safe_upstream_request_id(response.headers), selected_key=key, usage=UsageStats()), routing_events=routing_events, blocked_account_ids=blocked_account_ids)
                            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="upstream unavailable")
                        latency_ms = int((time.perf_counter() - started_at) * 1000)
                        context.repository.insert_request_log(prepare_log_payload(endpoint=endpoint, context=context, selected_key=key, stream=True, service_tier=service_tier, usage=UsageStats(), latency_ms=latency_ms, status_code=response.status_code, error_type=applied.error_type, upstream_request_id=safe_upstream_request_id(response.headers)), context.settings.request_log_retention)
                        return Response(content=body_text, status_code=response.status_code, media_type=response.headers.get("content-type"))
                iterator = response.aiter_bytes()
                try:
                    first_chunk = await iterator.__anext__()
                except StopAsyncIteration:
                    first_chunk = b""
                collector = StreamUsageCollector()
                collector.upstream_request_id = safe_upstream_request_id(response.headers)
                stream_transform = stream_transform_factory() if stream_transform_factory is not None else None
                async def finalize_stream(log_collector: StreamUsageCollector, error_type: str | None) -> None:
                    log_collector.merge_headers(response.headers)
                    if not log_collector.usage.input_tokens and not log_collector.usage.output_tokens:
                        log_collector.usage = maybe_estimate_usage(
                            log_collector.usage, payload, None, upstream_model=context.resolved_model.upstream_model
                        )
                    if endpoint == "responses" and bind_response_key_route and log_collector.response_id:
                        context.repository.upsert_response_key_route(log_collector.response_id, key)
                    if log_collector.response_id and response_id_callback is not None:
                        response_id_callback(log_collector.response_id, key)
                    latency_ms = int((time.perf_counter() - started_at) * 1000)
                    request_log_id = context.repository.insert_request_log(prepare_log_payload(endpoint=endpoint, context=context, selected_key=key, stream=True, service_tier=service_tier, usage=log_collector.usage, latency_ms=latency_ms, status_code=response.status_code, error_type=error_type or log_collector.error_type, upstream_request_id=log_collector.upstream_request_id), context.settings.request_log_retention)
                    _record_transform_debug_if_enabled(context, route_trace, _route_trace_result(request_log_id=request_log_id, status_code=response.status_code, error_type=error_type or log_collector.error_type, latency_ms=latency_ms, upstream_request_id=log_collector.upstream_request_id, selected_key=key, usage=log_collector.usage), routing_events=routing_events, blocked_account_ids=blocked_account_ids)
                    await close_quietly(response, client)
                keep_open = True
                return build_passthrough_stream_response(
                    response,
                    iterator,
                    first_chunk,
                    finalize=finalize_stream,
                    collector=collector,
                    chunk_transform=getattr(stream_transform, "feed", None) if stream_transform is not None else None,
                    final_chunk_transform=getattr(stream_transform, "flush", None) if stream_transform is not None else None,
                )
            response = await client.post_json(upstream_path, payload, headers=headers)
            last_key = key
            if 200 <= response.status_code < 300:
                body_text = await read_response_text(response)
                upstream_usage = _merge_request_usage(response, extract_usage(body_text))
                try:
                    parsed_body = json.loads(body_text)
                except json.JSONDecodeError:
                    parsed_body = None
                usage = maybe_estimate_usage(upstream_usage, payload, parsed_body, upstream_model=context.resolved_model.upstream_model)
                if endpoint == "responses" and bind_response_key_route:
                    response_id = _response_id_from_body(body_text)
                    if response_id:
                        context.repository.upsert_response_key_route(response_id, key)
                        if response_id_callback is not None:
                            response_id_callback(response_id, key)
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                request_log_id = context.repository.insert_request_log(prepare_log_payload(endpoint=endpoint, context=context, selected_key=key, stream=False, service_tier=service_tier, usage=usage, latency_ms=latency_ms, status_code=response.status_code, error_type=None, upstream_request_id=safe_upstream_request_id(response.headers)), context.settings.request_log_retention)
                _record_transform_debug_if_enabled(context, route_trace, _route_trace_result(request_log_id=request_log_id, status_code=response.status_code, error_type=None, latency_ms=latency_ms, upstream_request_id=safe_upstream_request_id(response.headers), selected_key=key, usage=usage), routing_events=routing_events, blocked_account_ids=blocked_account_ids)
                if response_transform is not None:
                    try:
                        transformed = response_transform(parsed_body if parsed_body is not None else json.loads(body_text))
                    except json.JSONDecodeError:
                        return build_passthrough_response(response)
                    return JSONResponse(status_code=response.status_code, content=transformed)
                return build_passthrough_response(response)
            body_text = await read_response_text(response)
            retry_payload = retry_payload_on_error(response.status_code, body_text) if retry_payload_on_error is not None else None
            if retry_payload is not None:
                await close_quietly(response)
                response = await client.post_json(upstream_path, retry_payload, headers=headers)
                if 200 <= response.status_code < 300:
                    payload = retry_payload
                    body_text = await read_response_text(response)
                    upstream_usage = _merge_request_usage(response, extract_usage(body_text))
                    try:
                        parsed_body = json.loads(body_text)
                    except json.JSONDecodeError:
                        parsed_body = None
                    usage = maybe_estimate_usage(upstream_usage, payload, parsed_body, upstream_model=context.resolved_model.upstream_model)
                    if endpoint == "responses" and bind_response_key_route:
                        response_id = _response_id_from_body(body_text)
                        if response_id:
                            context.repository.upsert_response_key_route(response_id, key)
                            if response_id_callback is not None:
                                response_id_callback(response_id, key)
                    latency_ms = int((time.perf_counter() - started_at) * 1000)
                    request_log_id = context.repository.insert_request_log(prepare_log_payload(endpoint=endpoint, context=context, selected_key=key, stream=False, service_tier=service_tier, usage=usage, latency_ms=latency_ms, status_code=response.status_code, error_type=None, upstream_request_id=safe_upstream_request_id(response.headers)), context.settings.request_log_retention)
                    _record_transform_debug_if_enabled(context, route_trace, _route_trace_result(request_log_id=request_log_id, status_code=response.status_code, error_type=None, latency_ms=latency_ms, upstream_request_id=safe_upstream_request_id(response.headers), selected_key=key, usage=usage), routing_events=routing_events, blocked_account_ids=blocked_account_ids)
                    if response_transform is not None:
                        try:
                            transformed = response_transform(parsed_body if parsed_body is not None else json.loads(body_text))
                        except json.JSONDecodeError:
                            return build_passthrough_response(response)
                        return JSONResponse(status_code=response.status_code, content=transformed)
                    return Response(content=body_text, status_code=response.status_code, media_type=response.headers.get("content-type"))
                body_text = await read_response_text(response)
            applied = apply_candidate_failure(context, key, response.status_code, body_text=body_text)
            last_error_type = applied.error_type
            if applied.scope == "account" and applied.account_id:
                blocked_account_ids.add(applied.account_id)
            routing_events.append(_routing_event(key, account_id, action="failover", error_type=applied.error_type, scope=applied.scope))
            if applied.retryable and index + 1 < len(context.selected_keys):
                continue
            if applied.retryable:
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                request_log_id = context.repository.insert_request_log(prepare_log_payload(endpoint=endpoint, context=context, selected_key=key, stream=False, service_tier=service_tier, usage=UsageStats(), latency_ms=latency_ms, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, error_type=applied.error_type, upstream_request_id=safe_upstream_request_id(response.headers)), context.settings.request_log_retention)
                _record_transform_debug_if_enabled(context, route_trace, _route_trace_result(request_log_id=request_log_id, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, error_type=applied.error_type, latency_ms=latency_ms, upstream_request_id=safe_upstream_request_id(response.headers), selected_key=key, usage=UsageStats()), routing_events=routing_events, blocked_account_ids=blocked_account_ids)
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="upstream unavailable")
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            request_log_id = context.repository.insert_request_log(prepare_log_payload(endpoint=endpoint, context=context, selected_key=key, stream=False, service_tier=service_tier, usage=UsageStats(), latency_ms=latency_ms, status_code=response.status_code, error_type=applied.error_type, upstream_request_id=safe_upstream_request_id(response.headers)), context.settings.request_log_retention)
            _record_transform_debug_if_enabled(context, route_trace, _route_trace_result(request_log_id=request_log_id, status_code=response.status_code, error_type=applied.error_type, latency_ms=latency_ms, upstream_request_id=safe_upstream_request_id(response.headers), selected_key=key, usage=UsageStats()), routing_events=routing_events, blocked_account_ids=blocked_account_ids)
            return build_passthrough_response(response)
        except httpx.HTTPError as exc:
            applied = apply_candidate_failure(context, key, None, exc=exc)
            last_error_type = applied.error_type
            if applied.scope == "account" and applied.account_id:
                blocked_account_ids.add(applied.account_id)
            routing_events.append(_routing_event(key, account_id, action="failover", error_type=applied.error_type, scope=applied.scope))
            if applied.retryable and index + 1 < len(context.selected_keys):
                continue
            if applied.retryable:
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                request_log_id = context.repository.insert_request_log(prepare_log_payload(endpoint=endpoint, context=context, selected_key=key, stream=False, service_tier=service_tier, usage=UsageStats(), latency_ms=latency_ms, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, error_type=applied.error_type, upstream_request_id=None), context.settings.request_log_retention)
                _record_transform_debug_if_enabled(context, route_trace, _route_trace_result(request_log_id=request_log_id, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, error_type=applied.error_type, latency_ms=latency_ms, upstream_request_id=None, selected_key=key, usage=UsageStats()), routing_events=routing_events, blocked_account_ids=blocked_account_ids)
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="upstream unavailable") from exc
            raise
        finally:
            if not keep_open:
                await close_quietly(response, client)
    latency_ms = int((time.perf_counter() - started_at) * 1000)
    request_log_id = context.repository.insert_request_log(prepare_log_payload(endpoint=endpoint, context=context, selected_key=last_key, stream=stream, service_tier=service_tier, usage=UsageStats(), latency_ms=latency_ms, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, error_type=last_error_type or "upstream_unavailable", upstream_request_id=None), context.settings.request_log_retention)
    _record_transform_debug_if_enabled(context, route_trace, _route_trace_result(request_log_id=request_log_id, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, error_type=last_error_type or "upstream_unavailable", latency_ms=latency_ms, upstream_request_id=None, selected_key=last_key, usage=UsageStats()), routing_events=routing_events, blocked_account_ids=blocked_account_ids)
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="upstream unavailable")


async def proxy_fireworks_json_request(
    context: ProxyRequestContext,
    *,
    endpoint: str,
    method: str,
    upstream_path: str,
    headers: dict[str, str],
    params: Mapping[str, Any] | None = None,
    route_trace: dict[str, Any] | None = None,
) -> Response:
    started_at = time.perf_counter()
    if not context.selected_keys:
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        request_log_id = context.repository.insert_request_log(prepare_log_payload(endpoint=endpoint, context=context, selected_key=None, stream=False, service_tier=None, usage=UsageStats(), latency_ms=latency_ms, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, error_type="no_healthy_keys", upstream_request_id=None), context.settings.request_log_retention)
        _record_transform_debug_if_enabled(context, route_trace, _route_trace_result(request_log_id=request_log_id, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, error_type="no_healthy_keys", latency_ms=latency_ms, upstream_request_id=None, selected_key=None, usage=UsageStats()))
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="upstream unavailable")

    last_error_type = None
    last_key = None
    blocked_account_ids: set[str] = set()
    routing_events: list[dict[str, Any]] = []
    for index, key in enumerate(context.selected_keys):
        account_id = _safe_account_id_for_key(context, key)
        if account_id and account_id in blocked_account_ids:
            routing_events.append(_routing_event(key, account_id, action="skip", scope="account"))
            continue
        routing_events.append(_routing_event(key, account_id, action="attempt"))
        client = FireworksClient(context.settings, key.api_key)
        response = None
        try:
            if method == "GET":
                response = await client.get_json(upstream_path, headers=headers, params=params)
            elif method == "DELETE":
                response = await client.delete_json(upstream_path, headers=headers, params=params)
            else:
                raise ValueError(f"unsupported method: {method}")
            last_key = key
            if 200 <= response.status_code < 300:
                if endpoint == "responses" and method == "DELETE":
                    maybe_response_id = upstream_path.rsplit("/", 1)[-1] if "/" in upstream_path else ""
                    context.repository.delete_response_key_route(maybe_response_id)
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                request_log_id = context.repository.insert_request_log(prepare_log_payload(endpoint=endpoint, context=context, selected_key=key, stream=False, service_tier=None, usage=UsageStats(), latency_ms=latency_ms, status_code=response.status_code, error_type=None, upstream_request_id=safe_upstream_request_id(response.headers)), context.settings.request_log_retention)
                _record_transform_debug_if_enabled(context, route_trace, _route_trace_result(request_log_id=request_log_id, status_code=response.status_code, error_type=None, latency_ms=latency_ms, upstream_request_id=safe_upstream_request_id(response.headers), selected_key=key, usage=UsageStats()), routing_events=routing_events, blocked_account_ids=blocked_account_ids)
                return build_passthrough_response(response)
            body_text = await read_response_text(response)
            applied = apply_candidate_failure(context, key, response.status_code, body_text=body_text)
            last_error_type = applied.error_type
            if applied.scope == "account" and applied.account_id:
                blocked_account_ids.add(applied.account_id)
            routing_events.append(_routing_event(key, account_id, action="failover", error_type=applied.error_type, scope=applied.scope))
            if applied.retryable and index + 1 < len(context.selected_keys):
                continue
            if applied.retryable:
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                request_log_id = context.repository.insert_request_log(prepare_log_payload(endpoint=endpoint, context=context, selected_key=key, stream=False, service_tier=None, usage=UsageStats(), latency_ms=latency_ms, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, error_type=applied.error_type, upstream_request_id=safe_upstream_request_id(response.headers)), context.settings.request_log_retention)
                _record_transform_debug_if_enabled(context, route_trace, _route_trace_result(request_log_id=request_log_id, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, error_type=applied.error_type, latency_ms=latency_ms, upstream_request_id=safe_upstream_request_id(response.headers), selected_key=key, usage=UsageStats()), routing_events=routing_events, blocked_account_ids=blocked_account_ids)
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="upstream unavailable")
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            request_log_id = context.repository.insert_request_log(prepare_log_payload(endpoint=endpoint, context=context, selected_key=key, stream=False, service_tier=None, usage=UsageStats(), latency_ms=latency_ms, status_code=response.status_code, error_type=applied.error_type, upstream_request_id=safe_upstream_request_id(response.headers)), context.settings.request_log_retention)
            _record_transform_debug_if_enabled(context, route_trace, _route_trace_result(request_log_id=request_log_id, status_code=response.status_code, error_type=applied.error_type, latency_ms=latency_ms, upstream_request_id=safe_upstream_request_id(response.headers), selected_key=key, usage=UsageStats()), routing_events=routing_events, blocked_account_ids=blocked_account_ids)
            return build_passthrough_response(response)
        except httpx.HTTPError as exc:
            applied = apply_candidate_failure(context, key, None, exc=exc)
            last_error_type = applied.error_type
            if applied.scope == "account" and applied.account_id:
                blocked_account_ids.add(applied.account_id)
            routing_events.append(_routing_event(key, account_id, action="failover", error_type=applied.error_type, scope=applied.scope))
            if applied.retryable and index + 1 < len(context.selected_keys):
                continue
            if applied.retryable:
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                request_log_id = context.repository.insert_request_log(prepare_log_payload(endpoint=endpoint, context=context, selected_key=key, stream=False, service_tier=None, usage=UsageStats(), latency_ms=latency_ms, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, error_type=applied.error_type, upstream_request_id=None), context.settings.request_log_retention)
                _record_transform_debug_if_enabled(context, route_trace, _route_trace_result(request_log_id=request_log_id, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, error_type=applied.error_type, latency_ms=latency_ms, upstream_request_id=None, selected_key=key, usage=UsageStats()), routing_events=routing_events, blocked_account_ids=blocked_account_ids)
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="upstream unavailable") from exc
            raise
        finally:
            await close_quietly(response, client)

    latency_ms = int((time.perf_counter() - started_at) * 1000)
    request_log_id = context.repository.insert_request_log(prepare_log_payload(endpoint=endpoint, context=context, selected_key=last_key, stream=False, service_tier=None, usage=UsageStats(), latency_ms=latency_ms, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, error_type=last_error_type or "upstream_unavailable", upstream_request_id=None), context.settings.request_log_retention)
    _record_transform_debug_if_enabled(context, route_trace, _route_trace_result(request_log_id=request_log_id, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, error_type=last_error_type or "upstream_unavailable", latency_ms=latency_ms, upstream_request_id=None, selected_key=last_key, usage=UsageStats()), routing_events=routing_events, blocked_account_ids=blocked_account_ids)
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="upstream unavailable")
