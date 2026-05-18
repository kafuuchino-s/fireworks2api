from __future__ import annotations

from dataclasses import is_dataclass, replace
from types import SimpleNamespace

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, ValidationError, field_validator
from app.dataplane.fireworks.paths import resolve_inference_path
from app.dataplane.fireworks.route_trace import build_route_transform_trace

from app.products.openai.fireworks_native.responses import build_responses_adapter, is_sub2api_bridge_shape, validate_responses_body
from app.products.openai.responses_priority_fallback import build_priority_chat_payload, is_priority_responses_fallback_eligible, synthesize_responses_from_chat
from app.products.openai.responses_stream import ResponsesSSECanonicalizer, strip_reasoning_output_items
from app.products.openai.errors import OpenAIRequestError, openai_error_response_json
from app.products.openai.proxy_common import (
    build_proxy_context_from_body,
    ensure_proxy_auth,
    load_json_body,
    proxy_fireworks_request,
    record_proxy_transform_debug,
)
from app.dataplane.fireworks.proxy import proxy_fireworks_json_request
from app.dataplane.fireworks.headers import build_upstream_headers
from app.products.openai.context import build_proxy_key_context

router = APIRouter()


def _with_selected_keys(context, selected_keys):
    if is_dataclass(context):
        return replace(context, selected_keys=selected_keys)
    values = dict(vars(context))
    values["selected_keys"] = selected_keys
    return SimpleNamespace(**values)


def _is_previous_response_not_found(status_code: int, body_text: str) -> bool:
    if status_code not in {400, 404}:
        return False
    lowered = body_text.lower()
    return "previous" in lowered and "response" in lowered and "not found" in lowered


def _retry_without_previous_response_id_factory(context, payload: dict):
    previous_response_id = payload.get("previous_response_id")
    if not isinstance(previous_response_id, str) or not previous_response_id.strip():
        return None
    attempted = False

    def _retry(status_code: int, body_text: str) -> dict | None:
        nonlocal attempted
        if attempted or not _is_previous_response_not_found(status_code, body_text):
            return None
        attempted = True
        delete_route = getattr(context.repository, "delete_response_key_route", None)
        if callable(delete_route):
            delete_route(previous_response_id)
        retry_payload = dict(payload)
        retry_payload.pop("previous_response_id", None)
        return retry_payload

    return _retry


class ResponsesLifecycleQuery(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=100)
    after: str | None = None
    before: str | None = None

    @field_validator("after", "before")
    @classmethod
    def _non_empty_cursor(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must be a non-empty string")
        return value


async def _handle_responses(request: Request):
    await ensure_proxy_auth(request)
    body = await load_json_body(request)
    priority_fallback = is_priority_responses_fallback_eligible(body)
    if priority_fallback:
        try:
            context = await build_proxy_context_from_body(request, body)
            payload, report = build_priority_chat_payload(body, upstream_model=context.resolved_model.upstream_model)
        except OpenAIRequestError as exc:
            return openai_error_response_json(exc.message, param=exc.param, code=exc.code)
        upstream_path = resolve_inference_path(getattr(context.settings, "upstream_base_url", ""), "chat_completions")
        route_trace = build_route_transform_trace(
            context,
            public_route="POST /v1/responses",
            operation="create",
            adapter="app.products.openai.responses_priority_fallback.build_priority_chat_payload",
            fireworks_endpoint="cross_endpoint_fallback/priority:chat_completions",
            payload={"field_names": tuple(sorted(payload.keys()))},
            headers={"header_names": tuple()},
            request_shape={"payload_field_names": tuple(sorted(payload.keys())), "forwarded_headers": {}},
            field_actions=report["field_changes"],
            warnings=report["warnings"],
            routing_metadata=getattr(context, "routing_metadata", None),
        )
        record_proxy_transform_debug(context, endpoint="responses", upstream_endpoint=upstream_path, payload=payload, headers={}, stream=False, service_tier="priority", field_changes=report["field_changes"], warnings=report["warnings"])
        return await proxy_fireworks_request(
            context,
            endpoint="responses",
            upstream_path=upstream_path,
            payload=payload,
            headers={},
            route_trace=route_trace,
            response_transform=lambda data: synthesize_responses_from_chat(
                data,
                model=body.get("model", ""),
                upstream_model=context.resolved_model.upstream_model,
                perf_metrics_in_response=body.get("perf_metrics_in_response") if isinstance(body.get("perf_metrics_in_response"), bool) else None,
            ),
            bind_response_key_route=False,
        )
    try:
        validate_responses_body(body)
    except OpenAIRequestError as exc:
        return openai_error_response_json(exc.message, param=exc.param, code=exc.code)
    context = await build_proxy_context_from_body(request, body)
    previous_response_id = body.get("previous_response_id")
    if isinstance(previous_response_id, str) and previous_response_id.strip():
        routed_key = context.repository.get_response_key_route(previous_response_id)
        if routed_key is not None:
            context = _with_selected_keys(context, [routed_key])
    try:
        payload, headers, report = build_responses_adapter(context)
    except OpenAIRequestError as exc:
        return openai_error_response_json(exc.message, param=exc.param, code=exc.code)
    upstream_path = resolve_inference_path(getattr(context.settings, "upstream_base_url", ""), "responses")
    route_trace = build_route_transform_trace(
        context,
        public_route="POST /v1/responses",
        operation="create",
        adapter="app.products.openai.responses.build_responses_adapter",
        fireworks_endpoint="responses:responses",
        payload={"field_names": tuple(sorted(payload.keys()))},
        headers={"header_names": tuple(sorted(headers.keys()))},
        request_shape={"payload_field_names": tuple(sorted(payload.keys())), "forwarded_headers": headers},
        field_actions=report["field_changes"],
        warnings=report["warnings"],
        routing_metadata=getattr(context, "routing_metadata", None),
    )
    record_proxy_transform_debug(context, endpoint="responses", upstream_endpoint=upstream_path, payload=payload, headers=headers, stream=bool(payload.get("stream")), service_tier=payload.get("service_tier") if isinstance(payload.get("service_tier"), str) else None, field_changes=report["field_changes"], warnings=report["warnings"])
    proxy_kwargs = {
        "endpoint": "responses",
        "upstream_path": upstream_path,
        "payload": payload,
        "headers": headers,
        "route_trace": route_trace,
    }
    retry_payload_on_error = _retry_without_previous_response_id_factory(context, payload)
    if retry_payload_on_error is not None:
        proxy_kwargs["retry_payload_on_error"] = retry_payload_on_error
    bridge_compat = is_sub2api_bridge_shape(body)
    if bool(payload.get("stream")):
        suppress_reasoning = isinstance(payload.get("metadata"), dict) and payload["metadata"].pop("fireworks2api_suppress_reasoning_stream", False) is True
        proxy_kwargs["stream_transform_factory"] = (
            lambda: ResponsesSSECanonicalizer(
                suppress_reasoning=suppress_reasoning,
                sub2api_bridge_compat=bridge_compat,
                reasoning_fallback_to_text=suppress_reasoning,
            )
        )
    elif bridge_compat:
        proxy_kwargs["response_transform"] = strip_reasoning_output_items
    return await proxy_fireworks_request(context, **proxy_kwargs)


async def _handle_responses_lifecycle(request: Request, *, method: str, response_id: str | None = None):
    await ensure_proxy_auth(request)
    context = await build_proxy_key_context(request, route_seed="responses")
    if response_id:
        routed_key = context.repository.get_response_key_route(response_id)
        if routed_key is not None:
            context = _with_selected_keys(context, [routed_key])
    try:
        query = ResponsesLifecycleQuery.model_validate(dict(request.query_params))
    except ValidationError as exc:
        detail = exc.errors()[0] if exc.errors() else None
        param = None
        if detail and isinstance(detail.get("loc"), tuple) and detail["loc"]:
            param = str(detail["loc"][-1])
        return openai_error_response_json("Invalid responses lifecycle query parameters.", param=param, code="invalid_request_error")
    params = {k: v for k, v in query.model_dump().items() if v is not None}
    upstream_path = resolve_inference_path(getattr(context.settings, "upstream_base_url", ""), "responses_lifecycle")
    if response_id is not None:
        upstream_path = f"{upstream_path.rstrip('/')}/{response_id}"
    headers = build_upstream_headers(context.request_headers, stable_key=context.stable_key, affinity_hash_secret=context.settings.affinity_hash_secret or context.settings.log_hash_secret)
    public_route = f"{method} /v1/responses/{{response_id}}" if response_id is not None else "GET /v1/responses"
    route_trace = build_route_transform_trace(
        context,
        public_route=public_route,
        operation="lifecycle",
        adapter="app.products.openai.responses._handle_responses_lifecycle",
        fireworks_endpoint="responses:responses/{response_id}" if response_id is not None else "responses:responses",
        request_shape={"payload_field_names": (), "forwarded_headers": headers},
        field_actions=(),
        routing_metadata=getattr(context, "routing_metadata", None),
    )
    response = await proxy_fireworks_json_request(
        context,
        endpoint="responses",
        method=method,
        upstream_path=upstream_path,
        headers=headers,
        params=params,
        route_trace=route_trace,
    )
    if method == "DELETE" and response_id is not None and getattr(response, "status_code", 200) < 300:
        delete_route = getattr(context.repository, "delete_response_key_route", None)
        if callable(delete_route):
            delete_route(response_id)
    return response


@router.post("/v1/responses")
async def responses(request: Request):
    return await _handle_responses(request)


@router.get("/v1/responses")
async def list_responses(request: Request):
    return await _handle_responses_lifecycle(request, method="GET")


@router.get("/v1/responses/{response_id}")
async def get_response(request: Request, response_id: str):
    return await _handle_responses_lifecycle(request, method="GET", response_id=response_id)


@router.delete("/v1/responses/{response_id}")
async def delete_response(request: Request, response_id: str):
    return await _handle_responses_lifecycle(request, method="DELETE", response_id=response_id)
