import pytest

from app.dataplane.fireworks.reasoning_capabilities import (
    classify_reasoning_model,
    normalize_responses_reasoning_effort,
)


def test_known_family_classification() -> None:
    caps = classify_reasoning_model("accounts/fireworks/models/qwen3-vl-30b-a3b-thinking")
    assert caps.supports_thinking is True
    assert caps.supports_disable is True
    assert caps.min_thinking_budget_tokens == 1024
    assert "disabled" in caps.reasoning_history_values
    assert caps.enforcement == "advisory"


def test_unknown_family_defaults_to_advisory() -> None:
    caps = classify_reasoning_model("accounts/fireworks/models/unknown-model")
    assert caps.supports_thinking is None
    assert caps.supports_reasoning_effort is None
    assert caps.enforcement == "advisory"


@pytest.mark.parametrize(
    ("upstream_model", "effort", "expected", "changed"),
    [
        ("accounts/fireworks/models/minimax-m2p7", "xhigh", "high", True),
        ("accounts/fireworks/models/minimax-m2p7", "max", "high", True),
        ("accounts/fireworks/models/glm-5p1", "xhigh", "high", True),
        ("accounts/fireworks/routers/glm-5p1-fast", "max", "high", True),
        ("accounts/fireworks/models/deepseek-v4-pro", "xhigh", "xhigh", False),
        ("accounts/fireworks/models/deepseek-v4-pro", "max", "max", False),
        ("accounts/fireworks/models/kimi-k2p6", "xhigh", "xhigh", False),
        ("accounts/fireworks/routers/kimi-k2p6-turbo", "max", "max", False),
    ],
)
def test_normalize_responses_reasoning_effort_for_smoked_models(
    upstream_model: str,
    effort: str,
    expected: str,
    changed: bool,
) -> None:
    normalized, reason = normalize_responses_reasoning_effort(upstream_model, effort)

    assert normalized == expected
    assert (reason is not None) is changed
