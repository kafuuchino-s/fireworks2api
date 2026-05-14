from __future__ import annotations

from fastapi import APIRouter, Query, Request

from .deps import _repository, _request_payload
from app.platform.redaction import redact_secret

router = APIRouter()


@router.get("/requests")
async def list_requests(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    model: str | None = Query(default=None),
    model_alias: str | None = Query(default=None),
    key_fingerprint: str | None = Query(default=None),
    error_type: str | None = Query(default=None),
    status_code: int | None = Query(default=None),
):
    repository = _repository(request)
    selected_model = model_alias or model
    filters = {
        "model_alias": selected_model,
        "key_fingerprint": key_fingerprint,
        "error_type": error_type,
        "status_code": status_code,
    }
    key_by_fingerprint = {key.fingerprint: key for key in repository.list_keys()}
    raw_items = repository.list_request_logs(limit=limit, filters=filters)
    items = []
    for item in raw_items:
        fingerprint = item.get("key_fingerprint")
        key = key_by_fingerprint.get(fingerprint)
        if key:
            item = {
                **item,
                "key_name": key.name,
                "masked_key": redact_secret(key.api_key, visible=6),
                "key_label": key.name,
            }
        else:
            item = {**item, "key_label": item.get("key_label") or item.get("masked_key") or "unknown"}
        items.append(_request_payload(item))
    return {"items": items, "count": len(items), "limit": limit, "filters": {k: v for k, v in filters.items() if v not in (None, "")}}
