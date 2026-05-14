from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from .deps import _security_posture

router = APIRouter()


@router.get("/security/posture")
async def security_posture(request: Request) -> dict[str, Any]:
    return _security_posture(request)
