from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi.responses import Response
from fastapi.responses import StreamingResponse

from app.dataplane.usage import UsageStats, extract_usage, extract_usage_from_headers, merge_usage


_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "content-encoding",
}


def sanitize_passthrough_headers(
    headers: Mapping[str, str],
) -> tuple[str | None, dict[str, str]]:
    content_type: str | None = None
    passthrough: dict[str, str] = {}

    for name, value in headers.items():
        lower_name = name.lower()
        if lower_name == "content-type":
            content_type = value
            continue
        if lower_name in _HOP_BY_HOP_HEADERS:
            continue
        passthrough[name] = value

    return content_type, passthrough


def build_passthrough_response(response: httpx.Response) -> Response:
    content_type, headers = sanitize_passthrough_headers(response.headers)
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=headers,
        media_type=content_type,
    )


@dataclass
class StreamUsageCollector:
    usage: UsageStats = field(default_factory=UsageStats)
    buffer: str = ""
    chunks_sent: int = 0
    bytes_sent: int = 0
    error_type: str | None = None
    upstream_request_id: str | None = None
    response_id: str | None = None

    def feed(self, chunk: bytes) -> None:
        self.chunks_sent += 1
        self.bytes_sent += len(chunk)
        text = chunk.decode("utf-8", errors="ignore")
        self.buffer += text

        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            line = line.rstrip("\r")
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                continue
            self._capture_response_id(parsed)
            self.usage = merge_usage(self.usage, extract_usage(parsed))

    def _capture_response_id(self, payload: Any) -> None:
        if self.response_id or not isinstance(payload, dict):
            return
        direct_id = payload.get("id")
        if isinstance(direct_id, str) and direct_id.strip() and (direct_id.startswith("resp_") or payload.get("object") == "response"):
            self.response_id = direct_id.strip()
            return
        nested = payload.get("response")
        if isinstance(nested, dict):
            nested_id = nested.get("id")
            if isinstance(nested_id, str) and nested_id.strip():
                self.response_id = nested_id.strip()

    def merge_headers(self, headers: Mapping[str, str]) -> None:
        self.usage = merge_usage(self.usage, extract_usage_from_headers(headers))


FinalizeCallback = Callable[[StreamUsageCollector, str | None], Awaitable[None]]
ChunkTransform = Callable[[bytes], bytes]


def build_passthrough_stream_response(
    response: httpx.Response,
    iterator: AsyncIterator[bytes],
    first_chunk: bytes,
    *,
    finalize: FinalizeCallback | None = None,
    collector: StreamUsageCollector | None = None,
    chunk_transform: ChunkTransform | None = None,
    final_chunk_transform: Callable[[], bytes] | None = None,
) -> StreamingResponse:
    collector = collector or StreamUsageCollector()
    content_type, headers = sanitize_passthrough_headers(response.headers)

    async def body() -> AsyncIterator[bytes]:
        error_type: str | None = None
        try:
            if first_chunk:
                out_chunk = chunk_transform(first_chunk) if chunk_transform else first_chunk
                if out_chunk:
                    collector.feed(out_chunk)
                    yield out_chunk
            async for chunk in iterator:
                out_chunk = chunk_transform(chunk) if chunk_transform else chunk
                if out_chunk:
                    collector.feed(out_chunk)
                    yield out_chunk
            if final_chunk_transform is not None:
                final_chunk = final_chunk_transform()
                if final_chunk:
                    collector.feed(final_chunk)
                    yield final_chunk
        except asyncio.CancelledError:
            error_type = "client_cancelled"
            collector.error_type = error_type
            raise
        except Exception:
            error_type = "stream_error"
            collector.error_type = error_type
            raise
        finally:
            await response.aclose()
            if finalize is not None:
                await finalize(collector, error_type)

    return StreamingResponse(
        body(),
        status_code=response.status_code,
        headers=headers,
        media_type=content_type,
    )


__all__ = [
    "FinalizeCallback",
    "ChunkTransform",
    "StreamUsageCollector",
    "build_passthrough_response",
    "build_passthrough_stream_response",
    "sanitize_passthrough_headers",
]
