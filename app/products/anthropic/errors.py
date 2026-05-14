from __future__ import annotations

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse


def anthropic_error(message: str, *, param: str | None = None, code: str | None = None, status_code: int = 400) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"type": "error", "error": {"type": "invalid_request_error", "message": message, "param": param, "code": code}},
    )


def anthropic_error_response(message: str, *, param: str | None = None, code: str | None = None, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"type": "error", "error": {"type": "invalid_request_error", "message": message, "param": param, "code": code}},
    )


def raise_anthropic_error(message: str, *, param: str | None = None, code: str | None = None, status_code: int = 400) -> None:
    raise anthropic_error(message, param=param, code=code, status_code=status_code)
