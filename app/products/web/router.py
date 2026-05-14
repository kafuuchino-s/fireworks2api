from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


STATIC_ROOT = Path(__file__).resolve().parents[2] / "statics"
ADMIN_ROOT = STATIC_ROOT / "admin"

router = APIRouter(include_in_schema=False)


def _admin_file(name: str) -> FileResponse:
    return FileResponse(ADMIN_ROOT / name)


@router.get("/")
async def root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/admin/account", status_code=307)


@router.get("/admin")
async def admin_redirect() -> RedirectResponse:
    return RedirectResponse(url="/admin/account", status_code=307)


@router.get("/admin/login")
async def admin_login() -> FileResponse:
    return _admin_file("login.html")


@router.get("/admin/account")
async def admin_account() -> FileResponse:
    return _admin_file("account.html")


@router.get("/admin/config")
async def admin_config() -> FileResponse:
    return _admin_file("config.html")


@router.get("/admin/model")
async def admin_model() -> FileResponse:
    return _admin_file("model.html")


@router.get("/admin/status")
async def admin_status() -> FileResponse:
    return _admin_file("status.html")


@router.get("/admin/cache")
async def admin_cache() -> FileResponse:
    return _admin_file("cache.html")


@router.get("/favicon.ico")
async def favicon() -> FileResponse:
    return FileResponse(STATIC_ROOT / "favicon.ico")


static_mount = StaticFiles(directory=str(STATIC_ROOT), html=False)


__all__ = ["router", "static_mount"]
