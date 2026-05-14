from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from types import SimpleNamespace

from app.control.model_resolver import ModelResolutionError, ResolvedModel, resolve_model
from app.control.repository import AppRepository, KeyRecord
from app.dataplane.routing.affinity import (
    build_route_key,
    client_identity_from_request,
    extract_stable_key,
    stable_key_hash,
)
from app.dataplane.routing.sticky_router import select_candidate_keys
from app.platform.auth import require_proxy_auth
from app.platform.config import Settings
from app.products.openai.errors import OpenAIRequestError, openai_error_response


def _openai_http_error(message: str, status_code: int = 400) -> HTTPException:
    code = "invalid_api_key" if status_code == status.HTTP_401_UNAUTHORIZED else "invalid_request_error"
    return OpenAIRequestError(message, code=code, status_code=status_code)


@dataclass(frozen=True)
class ProxyRequestContext:
    settings: Settings
    repository: AppRepository
    request_headers: Mapping[str, Any]
    body: dict[str, Any]
    model_name: str
    resolved_model: ResolvedModel
    client_identity: str
    stable_key: str
    stable_key_source: str
    stable_key_hash_value: str
    affinity_header: str
    route_key: str
    selected_keys: list[KeyRecord]
    routing_metadata: dict[str, Any] | None = None


async def ensure_proxy_auth(request: Request) -> None:
    try:
        await require_proxy_auth(authorization=request.headers.get("authorization"), x_api_key=request.headers.get("x-api-key"))
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            raise OpenAIRequestError("invalid proxy token", code="invalid_api_key", status_code=exc.status_code) from exc
        raise


async def load_json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise OpenAIRequestError("invalid JSON body") from exc

    if not isinstance(body, dict):
        raise OpenAIRequestError("JSON body must be an object")

    return body


def get_settings_and_repository(request: Request) -> tuple[Settings, AppRepository]:
    return request.app.state.settings, request.app.state.repository


def get_model_name(body: dict[str, Any]) -> str:
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise OpenAIRequestError("model is required", param="model", code="missing_required_parameter")
    return model.strip()


def build_affinity_header(request: Request, stable_key_hash_value: str) -> str:
    existing = request.headers.get("x-session-affinity")
    return existing.strip() if isinstance(existing, str) and existing.strip() else stable_key_hash_value


async def build_proxy_context(request: Request) -> ProxyRequestContext:
    settings, repository = get_settings_and_repository(request)
    body = await load_json_body(request)
    return await build_proxy_context_from_body(request, body)


async def build_proxy_context_from_body(request: Request, body: dict[str, Any]) -> ProxyRequestContext:
    settings, repository = get_settings_and_repository(request)
    model_name = get_model_name(body)

    try:
        resolved_model = resolve_model(repository, model_name, settings.allow_unknown_model_passthrough)
    except ModelResolutionError as exc:
        raise OpenAIRequestError(str(exc), param="model") from exc

    client = request.client
    client_identity = client_identity_from_request(request.headers, client.host if client else None, client.port if client else None)
    route_model = resolved_model.upstream_model
    stable_key, stable_key_source = extract_stable_key(body, request.headers, route_model, client_identity)
    route_key = build_route_key(route_model, stable_key, client_identity if stable_key_source == "fallback" else None)
    affinity_secret = settings.affinity_hash_secret or settings.log_hash_secret
    key_hash = stable_key_hash(stable_key, affinity_secret)
    affinity_header = build_affinity_header(request, key_hash)
    keys = repository.list_keys(include_disabled=True)
    snapshots_by_fingerprint = {
        snapshot.key_fingerprint: snapshot
        for snapshot in getattr(repository, "list_fireworks_key_snapshots", lambda: [])()
        if getattr(snapshot, "key_fingerprint", None)
    }
    account_cooldowns_by_account_id = {
        cooldown.account_id: cooldown
        for cooldown in getattr(repository, "list_account_cooldowns", lambda: [])()
        if getattr(cooldown, "account_id", None)
    }
    selection = select_candidate_keys(
        keys,
        route_key,
        max(1, settings.max_upstream_attempts),
        snapshots_by_fingerprint=snapshots_by_fingerprint,
        account_cooldowns_by_account_id=account_cooldowns_by_account_id,
    )
    selected_keys = selection.selected_keys
    routing_metadata = dict(selection.metadata)
    routing_metadata.update(
        {
            "stable_key_source": stable_key_source,
            "stable_key_hash_value": key_hash,
            "affinity_header": affinity_header,
            "selected_key_count": len(selected_keys),
        }
    )

    return ProxyRequestContext(
        settings=settings,
        repository=repository,
        request_headers=request.headers,
        body=body,
        model_name=model_name,
        resolved_model=resolved_model,
        client_identity=client_identity,
        stable_key=stable_key,
        stable_key_source=stable_key_source,
        stable_key_hash_value=key_hash,
        affinity_header=affinity_header,
        route_key=route_key,
        selected_keys=selected_keys,
        routing_metadata=routing_metadata,
    )


async def build_proxy_context_optional_model(request: Request, body: dict[str, Any], *, route_seed: str) -> ProxyRequestContext:
    settings, repository = get_settings_and_repository(request)
    client = request.client
    client_identity = client_identity_from_request(request.headers, client.host if client else None, client.port if client else None)
    model_value = body.get("model")
    if isinstance(model_value, str) and model_value.strip():
        model_name = model_value.strip()
        try:
            resolved_model = resolve_model(repository, model_name, settings.allow_unknown_model_passthrough)
        except ModelResolutionError as exc:
            raise OpenAIRequestError(str(exc), param="model") from exc
        route_model = resolved_model.upstream_model
    else:
        model_name = route_seed
        resolved_model = SimpleNamespace(upstream_model=None, requested_model=route_seed, alias=None)
        route_model = route_seed

    stable_key, stable_key_source = extract_stable_key(body, request.headers, route_model, client_identity)
    route_key = build_route_key(route_model, stable_key, client_identity if stable_key_source == "fallback" else None)
    affinity_secret = settings.affinity_hash_secret or settings.log_hash_secret
    key_hash = stable_key_hash(stable_key, affinity_secret)
    affinity_header = build_affinity_header(request, key_hash)
    keys = repository.list_keys(include_disabled=True)
    snapshots_by_fingerprint = {
        snapshot.key_fingerprint: snapshot
        for snapshot in getattr(repository, "list_fireworks_key_snapshots", lambda: [])()
        if getattr(snapshot, "key_fingerprint", None)
    }
    account_cooldowns_by_account_id = {
        cooldown.account_id: cooldown
        for cooldown in getattr(repository, "list_account_cooldowns", lambda: [])()
        if getattr(cooldown, "account_id", None)
    }
    selection = select_candidate_keys(
        keys,
        route_key,
        max(1, settings.max_upstream_attempts),
        snapshots_by_fingerprint=snapshots_by_fingerprint,
        account_cooldowns_by_account_id=account_cooldowns_by_account_id,
    )
    selected_keys = selection.selected_keys
    routing_metadata = dict(selection.metadata)
    routing_metadata.update(
        {
            "stable_key_source": stable_key_source,
            "stable_key_hash_value": key_hash,
            "affinity_header": affinity_header,
            "selected_key_count": len(selected_keys),
        }
    )
    return ProxyRequestContext(
        settings=settings,
        repository=repository,
        request_headers=request.headers,
        body=body,
        model_name=route_seed,
        resolved_model=resolved_model,
        client_identity=client_identity,
        stable_key=stable_key,
        stable_key_source=stable_key_source,
        stable_key_hash_value=key_hash,
        affinity_header=affinity_header,
        route_key=route_key,
        selected_keys=selected_keys,
        routing_metadata=routing_metadata,
    )


async def build_proxy_key_context(request: Request, *, route_seed: str) -> ProxyRequestContext:
    return await build_proxy_context_optional_model(request, {}, route_seed=route_seed)


def copy_body(body: dict[str, Any]) -> dict[str, Any]:
    return dict(body)
