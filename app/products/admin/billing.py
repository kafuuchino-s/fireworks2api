from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from app.platform.auth import require_admin_auth
from app.products.admin.deps import _repository


router = APIRouter(tags=["admin"], dependencies=[Depends(require_admin_auth)])


class BillingImportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    import_id: str
    row_count: int


class BillingSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    totals: dict
    by_usage_type: list[dict]
    by_base_model_name: list[dict]
    imports: list[dict]


def _read_csv_content(csv_text: str | None, path: str | None) -> str:
    sources = [item is not None for item in (csv_text, path)]
    if sum(sources) != 1:
        raise HTTPException(status_code=400, detail="Provide exactly one of file, csv_text, or file_path")
    if csv_text is not None:
        return csv_text
    assert path is not None
    safe_path = Path(path)
    if not safe_path.is_file():
        raise HTTPException(status_code=400, detail="file_path must reference an existing file")
    try:
        return safe_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise HTTPException(status_code=400, detail="unable to read file_path") from exc


@router.post("/billing/import-csv", response_model=BillingImportResponse, status_code=201)
async def import_billing_csv(
    payload: dict = Body(...),
    repo=Depends(_repository),
):
    csv_text = payload.get("csv_text")
    file_path = payload.get("file_path")
    content = _read_csv_content(csv_text, file_path)
    try:
        return repo.import_billing_metrics_csv(csv_text=content, source_ref=file_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/billing/summary", response_model=BillingSummaryResponse)
def billing_summary(repo=Depends(_repository)):
    return repo.billing_metrics_summary()
