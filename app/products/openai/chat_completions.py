from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from app.products.openai.errors import OpenAIRequestError, openai_error_response_json, openai_http_exception_json
from app.dataplane.fireworks.paths import resolve_inference_path
from app.dataplane.fireworks.route_trace import build_route_transform_trace

from app.products.openai.fireworks_native.chat import build_chat_adapter, validate_chat_body
from app.dataplane.fireworks.reasoning_capabilities import classify_reasoning_model
from app.products.openai.proxy_common import (
    ensure_proxy_auth,
    load_json_body,
    proxy_fireworks_request,
    record_proxy_transform_debug,
)
from app.products.openai.context import build_proxy_context_from_body

router = APIRouter()


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    await ensure_proxy_auth(request)
    body = await load_json_body(request)
    try:
        validate_chat_body(body)
    except OpenAIRequestError as exc:
        return openai_error_response_json(exc.message, param=exc.param, code=exc.code)
    except HTTPException as exc:
        response = openai_http_exception_json(exc, code="invalid_request_error")
        if response is not None:
            return response
        raise
    try:
        context = await build_proxy_context_from_body(request, body)
    except HTTPException as exc:
        response = openai_http_exception_json(exc, code="invalid_request_error")
        if response is not None:
            return response
        raise
    try:
        payload, headers, report = build_chat_adapter(context)
    except OpenAIRequestError as exc:
        return openai_error_response_json(exc.message, param=exc.param, code=exc.code)
    except HTTPException as exc:
        response = openai_http_exception_json(exc)
        if response is not None:
            return response
        raise
    upstream_path = resolve_inference_path(context.settings.upstream_base_url, "chat_completions")
    capabilities = classify_reasoning_model(getattr(getattr(context, "resolved_model", None), "upstream_model", ""))
    warnings = list(report["warnings"])
    if body.get("thinking") is not None and capabilities.supports_thinking is False:
        warnings.append("thinking is likely unsupported for this upstream model family")
    if body.get("reasoning_effort") is not None and capabilities.supports_reasoning_effort is False:
        warnings.append("reasoning_effort is likely unsupported for this upstream model family")
    route_trace = build_route_transform_trace(
        context,
        public_route="POST /v1/chat/completions",
        operation="create",
        adapter="app.products.openai.chat_completions.build_chat_adapter",
        fireworks_endpoint="chat_completions:chat/completions",
        payload={"field_names": tuple(sorted(payload.keys()))},
        headers={"header_names": tuple(sorted(headers.keys()))},
        request_shape={"payload_field_names": tuple(sorted(payload.keys())), "forwarded_headers": headers},
        field_actions=report["field_changes"],
        warnings=warnings,
        routing_metadata=getattr(context, "routing_metadata", None),
    )
    record_proxy_transform_debug(context, endpoint="chat_completions", upstream_endpoint=upstream_path, payload=payload, headers=headers, stream=bool(payload.get("stream")), service_tier=payload.get("service_tier") if isinstance(payload.get("service_tier"), str) else None, field_changes=report["field_changes"], warnings=warnings)
    return await proxy_fireworks_request(
        context,
        endpoint="chat_completions",
        upstream_path=upstream_path,
        payload=payload,
        headers=headers,
        route_trace=route_trace,
    )
