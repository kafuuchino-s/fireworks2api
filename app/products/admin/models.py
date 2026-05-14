from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request, status

from app.control.repository import ModelMapping
from .deps import _model_payload, _optional_model_metadata, _repository
from .schemas import ModelCreate, ModelPatch

router = APIRouter()


def _alias_key(alias: str) -> str:
    return str(alias or "").strip().casefold()


def _find_model_case_insensitive(repository, alias: str) -> ModelMapping | None:
    finder = getattr(repository, "get_model_case_insensitive", None)
    if callable(finder):
        return finder(alias)
    key = _alias_key(alias)
    if not key:
        return None
    for model in repository.list_models():
        if _alias_key(model.alias) == key:
            return model
    return None


@router.get("/models")
async def list_models(request: Request):
    repository = _repository(request)
    items = []
    for model in repository.list_models():
        payload = _model_payload(model)
        payload.update(_optional_model_metadata(request, model.upstream_model))
        items.append(payload)
    return {"items": items, "count": len(items)}


@router.post("/models", status_code=status.HTTP_201_CREATED)
async def create_model(request: Request, payload: ModelCreate):
    repository = _repository(request)
    if _find_model_case_insensitive(repository, payload.alias):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="model already exists")
    repository.upsert_model(ModelMapping(**payload.model_dump()))
    created = repository.get_model(payload.alias)
    if not created:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="failed to create model")
    result = _model_payload(created)
    result.update(_optional_model_metadata(request, created.upstream_model))
    return result


@router.patch("/models/{alias}")
async def patch_model(request: Request, alias: str, payload: ModelPatch):
    repository = _repository(request)
    existing = repository.get_model(alias) or _find_model_case_insensitive(repository, alias)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="model not found")
    updated = ModelMapping(
        alias=payload.alias or existing.alias,
        upstream_model=payload.upstream_model or existing.upstream_model,
        enabled=existing.enabled if payload.enabled is None else payload.enabled,
    )
    if _alias_key(updated.alias) != _alias_key(existing.alias):
        conflict = _find_model_case_insensitive(repository, updated.alias)
        if conflict:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="model alias already exists")
        repository.delete_model(existing.alias)
    elif updated.alias != existing.alias:
        repository.delete_model(existing.alias)
    repository.upsert_model(updated)
    refreshed = repository.get_model(updated.alias)
    if not refreshed:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="failed to update model")
    result = _model_payload(refreshed)
    result.update(_optional_model_metadata(request, refreshed.upstream_model))
    return result


@router.delete("/models/{alias}")
async def delete_model(request: Request, alias: str):
    repository = _repository(request)
    existing = repository.get_model(alias) or _find_model_case_insensitive(repository, alias)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="model not found")
    repository.delete_model(existing.alias)
    return {"deleted": True, "alias": existing.alias}


@router.post("/models/import", status_code=status.HTTP_201_CREATED)
async def import_models(request: Request, payload: Any = Body(...)):
    repository = _repository(request)
    created = 0
    updated = 0
    items = []
    models = payload.get("models") if isinstance(payload, dict) else None
    if models is None and isinstance(payload, dict) and payload.get("upstream_model"):
        models = [payload]
    if not isinstance(models, list) or not models:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="models must be a non-empty list")
    for item in models:
        if isinstance(item, str):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="models import items must be objects with alias or aliases")
        if not isinstance(item, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="models import items must be objects with alias or aliases")
        upstream_model = str(item.get("upstream_model") or "").strip()
        if not upstream_model:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="models import items must contain non-empty upstream_model")
        aliases_value = item.get("aliases")
        if isinstance(aliases_value, list):
            requested_aliases = [str(alias).strip() for alias in aliases_value if str(alias).strip()]
        elif item.get("alias") is not None:
            alias = str(item.get("alias") or "").strip()
            requested_aliases = [alias] if alias else []
        else:
            requested_aliases = []
        if not requested_aliases:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="models import items must contain alias or aliases")
        seen_aliases: set[str] = set()
        for alias in requested_aliases:
            alias_key = _alias_key(alias)
            if alias_key in seen_aliases:
                continue
            seen_aliases.add(alias_key)
            existing = _find_model_case_insensitive(repository, alias)
            if existing and existing.upstream_model != upstream_model:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"model alias already exists: {existing.alias}")
            if existing and existing.alias != alias:
                repository.delete_model(existing.alias)
            mapping = ModelMapping(alias=alias, upstream_model=upstream_model, enabled=existing.enabled if existing else True)
            existed = existing is not None
            repository.upsert_model(mapping)
            model = repository.get_model(mapping.alias)
            model_payload = _model_payload(model) if model else None
            if model_payload is not None:
                model_payload.update(_optional_model_metadata(request, mapping.upstream_model))
            items.append({"status": "updated" if existed else "created", "model": model_payload})
            created += 0 if existed else 1
            updated += 1 if existed else 0
    return {"created": created, "updated": updated, "items": items}
