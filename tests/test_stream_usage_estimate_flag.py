from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.dataplane.fireworks.stream_proxy import StreamUsageCollector, build_passthrough_stream_response
from app.dataplane.usage import UsageStats, maybe_estimate_usage


class _FakeResponse:
    def __init__(self, chunks: list[bytes], status_code: int = 200) -> None:
        self._chunks = chunks
        self.status_code = status_code
        self.headers = {}

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        pass


def test_maybe_estimate_usage_flags_estimated_when_output_present() -> None:
    """If upstream only gives output_tokens, estimating input must set the flag."""
    usage = maybe_estimate_usage(
        UsageStats(output_tokens=1248),
        {"input": "hello"},
        None,
        upstream_model="accounts/fireworks/routers/kimi-k2p7-code-fast",
    )
    assert usage.output_tokens == 1248
    assert usage.input_tokens > 0
    assert usage.estimated is True


def test_stream_usage_collector_extracts_partial_usage_from_completed_event() -> None:
    """Ensure collector sees output_tokens injected by transform but not input_tokens."""
    collector = StreamUsageCollector()
    chunk = (
        b'event: response.completed\n'
        b'data: {"type":"response.completed","response":{"id":"resp_1","object":"response",'
        b'"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"hi"}]}],'
        b'"usage":{"output_tokens":1248}}}\n\n'
    )
    collector.feed(chunk)
    assert collector.usage.output_tokens == 1248
    assert collector.usage.input_tokens == 0
    assert collector.usage.estimated is False


@pytest.mark.asyncio
async def test_stream_finalize_estimates_missing_input_and_flags() -> None:
    """A stream that reports only output_tokens should still have estimated input in the log."""
    captured: dict | None = None

    async def finalize(collector, error_type):
        nonlocal captured
        usage = maybe_estimate_usage(
            collector.usage,
            {"input": "hello"},
            None,
            upstream_model="accounts/fireworks/routers/kimi-k2p7-code-fast",
        )
        captured = {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "estimated": usage.estimated,
        }

    response = _FakeResponse([
        b'event: response.completed\n'
        b'data: {"type":"response.completed","response":{"id":"resp_1","object":"response",'
        b'"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"hi"}]}],'
        b'"usage":{"output_tokens":1248}}}\n\n',
    ])
    stream_response = build_passthrough_stream_response(
        response,
        response.aiter_bytes(),
        b"",
        collector=None,
        finalize=finalize,
    )
    body = b""
    async for part in stream_response.body_iterator:
        body += part

    assert captured is not None
    assert captured["output_tokens"] == 1248
    assert captured["input_tokens"] > 0
    assert captured["estimated"] is True
