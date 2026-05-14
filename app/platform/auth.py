from __future__ import annotations

from fastapi import Header, HTTPException, Request, status

from app.platform.config import get_settings
from app.platform.redaction import redact_secret


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def _extract_api_key(x_api_key: str | None) -> str | None:
    if not isinstance(x_api_key, str) or not x_api_key:
        return None
    token = x_api_key.strip()
    return token or None


async def require_proxy_auth(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> None:
    settings = get_settings()
    if not settings.proxy_api_keys:
        return
    token = _extract_bearer(authorization) or _extract_api_key(x_api_key)
    if token not in settings.proxy_api_keys:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid proxy token")


async def require_admin_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None),
) -> None:
    settings = get_settings()
    if not settings.admin_token:
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin token is not configured")

    token = _extract_bearer(authorization) or x_admin_token
    if token != settings.admin_token:
        shown = redact_secret(token) if token else "<missing>"
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid admin token {shown}")


__all__ = ["require_proxy_auth", "require_admin_auth"]
