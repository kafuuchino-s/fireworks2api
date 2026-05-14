from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from .deps import _repository

router = APIRouter()


@router.get("/usage/cost-estimate")
async def cost_estimate(
    request: Request,
    input_token_rate: float = Query(default=0.000002, ge=0),
    output_token_rate: float = Query(default=0.000006, ge=0),
    cached_token_rate: float = Query(default=0.0, ge=0),
) -> dict[str, Any]:
    repository = _repository(request)
    return repository.request_cost_estimate(
        input_token_rate=input_token_rate,
        output_token_rate=output_token_rate,
        cached_token_rate=cached_token_rate,
    )
