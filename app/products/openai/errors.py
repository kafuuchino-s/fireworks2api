from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse


_ERROR_TYPES = {"unsupported_parameter", "unknown_parameter", "missing_required_parameter", "invalid_request_error", "invalid_api_key"}


class OpenAIRequestError(Exception):
    def __init__(self, message: str, *, param: str | None = None, code: str = "invalid_request_error", status_code: int = status.HTTP_400_BAD_REQUEST) -> None:
        self.message = message
        self.param = param
        self.code = code if code in _ERROR_TYPES else "invalid_request_error"
        self.status_code = status_code
        super().__init__(message)


def openai_error_response(message: str, *, param: str | None = None, code: str = "invalid_request_error") -> dict[str, Any]:
    return {"error": {"message": message, "type": "invalid_request_error", "param": param, "code": code if code in _ERROR_TYPES else "invalid_request_error"}}


def openai_error_json(status_code: int, message: str, *, param: str | None = None, code: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(status_code=status_code, content=openai_error_response(message, param=param, code=code))


def openai_http_exception(message: str, *, param: str | None = None, code: str = "invalid_request_error") -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=openai_error_response(message, param=param, code=code))


def raise_openai_error(message: str, *, param: str | None = None, code: str = "invalid_request_error") -> None:
    raise OpenAIRequestError(message, param=param, code=code)


def openai_error_response_json(message: str, *, param: str | None = None, code: str = "invalid_request_error", status_code: int = status.HTTP_400_BAD_REQUEST) -> JSONResponse:
    return openai_error_json(status_code, message, param=param, code=code)


def openai_http_exception_json(exc: HTTPException, *, param: str | None = None, code: str = "invalid_request_error") -> JSONResponse | None:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        return JSONResponse(status_code=exc.status_code, content=detail)
    if isinstance(detail, str):
        message = detail if isinstance(detail, str) else "invalid request"
        return openai_error_response_json(message, param=param, code=code, status_code=exc.status_code)
    return None


def openai_invalid_request_response(message: str, *, param: str | None = None, code: str = "invalid_request_error") -> JSONResponse:
    return openai_error_response_json(message, param=param, code=code)
