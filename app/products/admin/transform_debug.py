from __future__ import annotations

from fastapi import APIRouter, Query, Request

from .deps import _repository

router = APIRouter()


@router.get("/transform-debug")
async def list_transform_debug(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    endpoint: str | None = Query(default=None),
    model_alias: str | None = Query(default=None),
    error_only: bool = Query(default=False),
    has_route_trace: bool = Query(default=False),
):
    repository = _repository(request)
    items = repository.list_transform_debug_logs(
        limit=limit,
        filters={
            "endpoint": endpoint,
            "model_alias": model_alias,
            "error_only": error_only,
            "has_route_trace": has_route_trace,
        },
    )
    return {
        "items": items,
        "count": len(items),
        "limit": limit,
        "filters": {
            k: v
            for k, v in {
                "endpoint": endpoint,
                "model_alias": model_alias,
                "error_only": error_only,
                "has_route_trace": has_route_trace,
            }.items()
            if v not in (None, "", False)
        },
    }


@router.delete("/transform-debug")
async def clear_transform_debug(request: Request):
    repository = _repository(request)
    deleted = repository.clear_transform_debug_logs()
    return {"deleted": deleted}
