from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from .deps import _repository

router = APIRouter()


@router.get("/cache/analysis")
async def cache_analysis(request: Request) -> dict[str, Any]:
    repository = _repository(request)
    return repository.cache_analysis()
