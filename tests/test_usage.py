from __future__ import annotations

from app.dataplane.usage import UsageStats, extract_usage, extract_usage_from_headers, merge_usage, maybe_estimate_usage


def test_extract_usage_merges_body_and_perf_metrics() -> None:
    usage = extract_usage(
        {
            "usage": {"input_tokens": 10, "output_tokens": 3},
            "perf_metrics": {"promptTokens": 12, "cachedPromptTokens": 4},
        }
    )

    assert usage.input_tokens == 12
    assert usage.output_tokens == 3
    assert usage.cached_tokens == 4


def test_extract_usage_supports_snake_case_perf_metrics() -> None:
    usage = extract_usage({"perf_metrics": {"prompt-tokens": 7, "cached-prompt-tokens": 2}})

    assert usage.input_tokens == 7
    assert usage.cached_tokens == 2


def test_extract_usage_from_headers_maps_fireworks_metrics() -> None:
    usage = extract_usage_from_headers(
        {
            "fireworks-prompt-tokens": "9",
            "fireworks-cached-prompt-tokens": "5",
        }
    )

    assert usage.input_tokens == 9
    assert usage.cached_tokens == 5


def test_merge_usage_keeps_maximum_values() -> None:
    merged = merge_usage(
        extract_usage({"usage": {"input_tokens": 10, "cached_tokens": 1}}),
        extract_usage_from_headers({"fireworks-prompt-tokens": "12", "fireworks-cached-prompt-tokens": "4"}),
    )

    assert merged.input_tokens == 12
    assert merged.cached_tokens == 4


def test_merge_usage_flags_estimated_if_any_part_is_estimated() -> None:
    upstream = extract_usage({"usage": {"input_tokens": 10, "output_tokens": 3}})
    estimated = UsageStats(input_tokens=5, output_tokens=1, estimated=True)
    merged = merge_usage(upstream, estimated)

    assert merged.estimated is True
    assert merged.input_tokens == 10
    assert merged.output_tokens == 3


def test_extract_usage_supports_anthropic_cached_tokens_shape() -> None:
    usage = extract_usage({"usage": {"input_tokens": 6, "output_tokens": 2, "cache_creation_input_tokens": 1, "cache_read_input_tokens": 3}})

    assert usage.input_tokens == 6
    assert usage.cached_tokens == 4


def test_extract_usage_supports_responses_terminal_usage_shape() -> None:
    usage = extract_usage({"usage": {"input_tokens": 8, "output_tokens": 2, "cached_tokens": 5}})

    assert usage.input_tokens == 8
    assert usage.cached_tokens == 5


def test_maybe_estimate_usage_does_not_estimate_output_from_request_text() -> None:
    """When output_tokens is zero and no response payload is available, do not
    fabricate output tokens from the request text. That fallback caused streaming
    responses to report output equal to input, which is wrong for the admin log
    and downstream clients.
    """
    usage = maybe_estimate_usage(
        UsageStats(input_tokens=3, output_tokens=0, estimated=True),
        {"messages": [{"role": "user", "content": "hello world this is a test"}]},
        None,
        upstream_model="accounts/fireworks/routers/kimi-k2p7-code-fast",
    )

    assert usage.output_tokens == 0
    assert usage.estimated is True


def test_maybe_estimate_usage_fills_zero_output_tokens_from_response_payload() -> None:
    """When output_tokens is zero but a response payload is available, estimate
    output from the response text.
    """
    usage = maybe_estimate_usage(
        UsageStats(input_tokens=3, output_tokens=0, estimated=True),
        {"input": "hello world this is a test"},
        {"output": [{"type": "message", "content": [{"type": "output_text", "text": "hello world this is generated text"}]}]},
        upstream_model="accounts/fireworks/routers/kimi-k2p7-code-fast",
    )

    assert usage.output_tokens > 0
    assert usage.estimated is True
