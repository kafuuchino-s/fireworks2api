from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from app.platform.config import Settings


class FireworksClient:
    def __init__(self, settings: Settings, api_key: str):
        self.settings = settings
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=f"{str(settings.upstream_base_url).rstrip('/')}/",
            timeout=settings.request_timeout_seconds,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    @staticmethod
    def _normalize_path(path: str) -> str:
        return path.lstrip("/")

    def _merge_headers(
        self,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        merged = {"Authorization": f"Bearer {self.api_key}"}
        if headers:
            for name, value in headers.items():
                if value is None:
                    continue
                if name.lower() == "authorization":
                    continue
                merged[name] = str(value)
        return merged

    async def post_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        request = self._client.build_request(
            "POST",
            self._normalize_path(path),
            json=payload,
            headers=self._merge_headers(headers),
            params=params,
        )
        return await self._client.send(request)

    async def get_json(
        self,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        request = self._client.build_request(
            "GET",
            self._normalize_path(path),
            headers=self._merge_headers(headers),
            params=params,
        )
        return await self._client.send(request)

    async def delete_json(
        self,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        request = self._client.build_request(
            "DELETE",
            self._normalize_path(path),
            headers=self._merge_headers(headers),
            params=params,
        )
        return await self._client.send(request)

    async def post_stream(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        request = self._client.build_request(
            "POST",
            self._normalize_path(path),
            json=payload,
            headers=self._merge_headers(headers),
            params=params,
        )
        return await self._client.send(request, stream=True)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "FireworksClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()


__all__ = ["FireworksClient"]
