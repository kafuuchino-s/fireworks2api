from __future__ import annotations

import inspect
from dataclasses import is_dataclass, replace
from types import SimpleNamespace
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.dataplane.fireworks.paths import resolve_inference_path
from app.dataplane.fireworks.reasoning_capabilities import classify_reasoning_model
from app.dataplane.fireworks.route_trace import build_route_transform_trace
from app.dataplane.routing.affinity import stable_key_hash
from app.products.anthropic.adapters import build_messages_adapter, validate_messages_body
from app.products.anthropic.responses_bridge import ResponsesToAnthropicStreamAdapter, build_responses_bridge_payload, trim_responses_input_to_latest_turn
from app.products.anthropic.errors import anthropic_error_response
from app.products.openai.proxy_common import (
    build_proxy_context,
    build_proxy_context_from_body,
    ensure_proxy_auth,
    proxy_fireworks_request,
    record_proxy_transform_debug,
)

router = APIRouter()


_MESSAGES_MODE_HEADER = "x-fireworks2api-messages-mode"
_BRIDGE_SCOPE = "anthropic_messages_bridge"


def _with_selected_keys(context, selected_keys):
    if is_dataclass(context):
        return replace(context, selected_keys=selected_keys)
    values = dict(vars(context))
    values["selected_keys"] = selected_keys
    return SimpleNamespace(**values)


def _bridge_session_hash(context) -> str | None:
    stable_key = getattr(context, "stable_key", "")
    settings = getattr(context, "settings", None)
    secret = getattr(settings, "affinity_hash_secret", None) or getattr(settings, "log_hash_secret", None)
    if not stable_key or not secret:
        return None
    return stable_key_hash(stable_key, secret, length=24)


def _repository_supports_bridge_sessions(context) -> bool:
    repository = getattr(context, "repository", None)
    return repository is not None and all(
        hasattr(repository, name)
        for name in ("get_response_session_binding", "upsert_response_session_binding", "get_response_key_route")
    )


def _message_has_image(body: dict) -> bool:
    messages = body.get("messages")
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image":
                return True
    return False


async def _handle_messages(request: Request):
    if not isinstance(request.headers.get("anthropic-version"), str) or not request.headers.get("anthropic-version").strip():
        return anthropic_error_response("anthropic-version header is required", code="invalid_request")
    await ensure_proxy_auth(request)
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        return anthropic_error_response("invalid JSON body", code="invalid_request")
    if not isinstance(body, dict):
        return anthropic_error_response("JSON body must be an object", code="invalid_request")
    if "max_tokens" not in body:
        return anthropic_error_response("'max_tokens' is required", param="max_tokens", code="missing_required_parameter")
    try:
        validate_messages_body(body)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_400_BAD_REQUEST and isinstance(exc.detail, dict):
            detail = exc.detail.get("error") if isinstance(exc.detail.get("error"), dict) else None
            if detail is not None:
                return anthropic_error_response(detail.get("message", "invalid request"), param=detail.get("param"), code=detail.get("code"), status_code=exc.status_code)
        raise
    try:
        context = await build_proxy_context_from_body(request, body)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_400_BAD_REQUEST:
            return anthropic_error_response("invalid request", status_code=exc.status_code)
        raise
    settings = getattr(context, "settings", None)
    request_mode = request.headers.get(_MESSAGES_MODE_HEADER, "").strip().lower()
    mode = request_mode or getattr(settings, "anthropic_messages_mode", "native")
    mode = str(mode or "native").strip().lower().replace("-", "_")
    if mode == "responses_bridge":
        return await _handle_messages_responses_bridge(request, context, body)
    return await _handle_messages_native(request, context, body)


async def _handle_messages_native(request: Request, context, body: dict):
    payload, headers = build_messages_adapter(context)
    capabilities = classify_reasoning_model(getattr(getattr(context, "resolved_model", None), "upstream_model", ""))
    warnings: list[str] = []
    if body.get("thinking") is not None and capabilities.supports_thinking is False:
        warnings.append("thinking is likely unsupported for this upstream model family")
    upstream_base_url = getattr(getattr(context, "settings", None), "upstream_base_url", "")
    upstream_path = resolve_inference_path(upstream_base_url, "anthropic_messages") if upstream_base_url else "v1/messages"
    route_trace = build_route_transform_trace(
        context,
        public_route="/v1/messages",
        adapter="app.products.anthropic.adapters",
        fireworks_endpoint="anthropic_messages",
        request_shape={
            "payload_field_names": tuple(payload.keys()),
            "forwarded_headers": headers,
        },
        routing_metadata={
            "stable_key_source": getattr(context, "stable_key_source", None),
            "stable_key_hash_value": getattr(context, "stable_key_hash_value", None),
            "affinity_header": getattr(context, "affinity_header", None),
            "selected_key_count": getattr(context, "selected_key_count", None),
            "route_key": getattr(context, "route_key", None),
        },
    )
    if _message_has_image(body):
        route_trace["capability_tags"] = tuple(sorted(set(route_trace.get("capability_tags", ())) | {"image"}))
    record_proxy_transform_debug(context, endpoint="messages", upstream_endpoint=upstream_path, payload=payload, headers=headers, stream=bool(payload.get("stream")), service_tier=payload.get("service_tier") if isinstance(payload.get("service_tier"), str) else None, warnings=warnings)
    proxy_call = proxy_fireworks_request
    if "route_trace" in inspect.signature(proxy_call).parameters:
        return await proxy_call(context, endpoint="messages", upstream_path=upstream_path, payload=payload, headers=headers, route_trace=route_trace)
    return await proxy_call(context, endpoint="messages", upstream_path=upstream_path, payload=payload, headers=headers)


async def _handle_messages_responses_bridge(request: Request, context, body: dict):
    upstream_model = getattr(getattr(context, "resolved_model", None), "upstream_model", None)
    if not upstream_model:
        return JSONResponse(status_code=status.HTTP_501_NOT_IMPLEMENTED, content={"error": {"message": "responses bridge unavailable"}})
    session_hash = _bridge_session_hash(context)
    previous_response_id = None
    repository_has_bridge_sessions = _repository_supports_bridge_sessions(context)
    if session_hash and repository_has_bridge_sessions:
        binding = context.repository.get_response_session_binding(_BRIDGE_SCOPE, upstream_model, session_hash)
        previous_response_id = getattr(binding, "response_id", None) if binding is not None else None
    if previous_response_id:
        routed_key = context.repository.get_response_key_route(previous_response_id)
        if routed_key is not None:
            context = _with_selected_keys(context, [routed_key])
    payload, _report = build_responses_bridge_payload(body, upstream_model, previous_response_id=previous_response_id)
    if previous_response_id:
        payload = trim_responses_input_to_latest_turn(payload)
    upstream_base_url = getattr(getattr(context, "settings", None), "upstream_base_url", "")
    upstream_path = resolve_inference_path(upstream_base_url, "responses") if upstream_base_url else "v1/responses"
    route_trace = build_route_transform_trace(
        context,
        public_route="/v1/messages",
        adapter="app.products.anthropic.responses_bridge",
        fireworks_endpoint="responses",
        request_shape={"payload_field_names": tuple(payload.keys()), "forwarded_headers": {}},
        routing_metadata={
            "stable_key_source": getattr(context, "stable_key_source", None),
            "stable_key_hash_value": getattr(context, "stable_key_hash_value", None),
            "affinity_header": getattr(context, "affinity_header", None),
            "selected_key_count": getattr(context, "selected_key_count", None),
            "route_key": getattr(context, "route_key", None),
        },
    )
    record_proxy_transform_debug(context, endpoint="messages", upstream_endpoint=upstream_path, payload=payload, headers={}, stream=True, service_tier=payload.get("service_tier") if isinstance(payload.get("service_tier"), str) else None, warnings=["anthropic messages bridge enabled"])

    def bind_bridge_response(response_id: str, key) -> None:
        if not session_hash or not repository_has_bridge_sessions:
            return
        context.repository.upsert_response_session_binding(
            _BRIDGE_SCOPE,
            upstream_model,
            session_hash,
            response_id,
            key_name=getattr(key, "name", None),
            key_fingerprint=getattr(key, "fingerprint", None),
        )

    def stream_transform_factory():
        return ResponsesToAnthropicStreamAdapter(model=body.get("model", ""))

    payload["stream"] = True
    proxy_call = proxy_fireworks_request
    params = inspect.signature(proxy_call).parameters
    kwargs = {
        "endpoint": "responses",
        "upstream_path": upstream_path,
        "payload": {**payload, "stream": True},
        "headers": {},
    }
    if "route_trace" in params:
        kwargs["route_trace"] = route_trace
    if "stream_transform_factory" in params:
        kwargs["stream_transform_factory"] = stream_transform_factory
    if "response_id_callback" in params:
        kwargs["response_id_callback"] = bind_bridge_response
    return await proxy_call(context, **kwargs)


@router.post("/v1/messages")
async def messages(request: Request):
    return await _handle_messages(request)
