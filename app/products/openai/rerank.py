from __future__ import annotations

from fastapi import APIRouter, Request

from app.dataplane.fireworks.paths import resolve_inference_path
from app.dataplane.fireworks.route_trace import build_route_transform_trace
from app.products.openai.fireworks_native.rerank import build_rerank_adapter
from app.products.openai.context import build_proxy_context_optional_model, ensure_proxy_auth, load_json_body
from app.products.openai.errors import OpenAIRequestError, openai_error_response_json
from app.products.openai.proxy_common import build_responses_upstream_headers, proxy_fireworks_request, record_proxy_transform_debug

router = APIRouter()


async def _handle_rerank(request: Request):
    await ensure_proxy_auth(request)
    body = await load_json_body(request)
    context = await build_proxy_context_optional_model(request, body, route_seed="rerank")
    try:
        payload, _, report = build_rerank_adapter(context)
    except OpenAIRequestError as exc:
        return openai_error_response_json(exc.message, param=exc.param, code=exc.code)
    headers = build_responses_upstream_headers(context)
    upstream_path = resolve_inference_path(context.settings.upstream_base_url, "rerank")
    route_trace = build_route_transform_trace(
        context,
        public_route="POST /v1/rerank",
        operation="create",
        adapter="app.products.openai.rerank.build_rerank_adapter",
        fireworks_endpoint="rerank:rerank",
        payload={"field_names": tuple(sorted(payload.keys()))},
        headers={"header_names": tuple(sorted(headers.keys()))},
        request_shape={"payload_field_names": tuple(sorted(payload.keys())), "forwarded_headers": headers},
        field_actions=report["field_changes"],
        warnings=report["warnings"],
        routing_metadata=getattr(context, "routing_metadata", None),
    )
    record_proxy_transform_debug(context, endpoint="rerank", upstream_endpoint=upstream_path, payload=payload, headers=headers, stream=bool(payload.get("stream")), service_tier=payload.get("service_tier") if isinstance(payload.get("service_tier"), str) else None, field_changes=report["field_changes"], warnings=report["warnings"])
    return await proxy_fireworks_request(
        context,
        endpoint="rerank",
        upstream_path=upstream_path,
        payload=payload,
        headers=headers,
        route_trace=route_trace,
    )


@router.post("/v1/rerank")
async def rerank(request: Request):
    return await _handle_rerank(request)
