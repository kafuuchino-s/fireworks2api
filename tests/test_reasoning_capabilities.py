from app.dataplane.fireworks.reasoning_capabilities import classify_reasoning_model


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
