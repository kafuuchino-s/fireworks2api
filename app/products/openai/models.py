from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from app.products.openai.proxy_common import ensure_proxy_auth

router = APIRouter()


def _repository(request: Request):
    return request.app.state.repository


def _model_payload(model) -> dict[str, object]:
    return {
        "id": model.alias,
        "object": "model",
        "created": 0,
        "owned_by": "fireworks",
    }


def _openai_404(model: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={
            "error": {
                "message": f"The model '{model}' does not exist.",
                "type": "invalid_request_error",
                "param": "model",
                "code": "model_not_found",
            }
        },
    )


async def _list_models(request: Request):
    await ensure_proxy_auth(request)
    repository = _repository(request)
    items = [_model_payload(model) for model in repository.list_models() if model.enabled]
    return {"object": "list", "data": items}


async def _get_model(request: Request, model: str):
    await ensure_proxy_auth(request)
    repository = _repository(request)
    record = repository.get_model(model)
    if not record:
        finder = getattr(repository, "get_model_case_insensitive", None)
        record = finder(model) if callable(finder) else None
    if not record or not record.enabled:
        return _openai_404(model)
    return _model_payload(record)


@router.get("/v1/models")
async def v1_models(request: Request):
    return await _list_models(request)


@router.get("/v1/models/{model}")
async def v1_model(request: Request, model: str):
    return await _get_model(request, model)
