from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReasoningModelCapabilities:
    supports_reasoning_effort: bool | None = None
    supports_thinking: bool | None = None
    supported_efforts: tuple[str, ...] = ()
    supports_disable: bool | None = None
    min_thinking_budget_tokens: int | None = None
    reasoning_history_values: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
    enforcement: str = "advisory"


_DEFAULT_CAPABILITIES = ReasoningModelCapabilities()


def _family_capabilities(upstream_model: str) -> ReasoningModelCapabilities:
    model = (upstream_model or "").strip().lower()
    base = model.rsplit("/", 1)[-1]

    if base.endswith("-thinking") or "thinking" in base:
        return ReasoningModelCapabilities(
            supports_thinking=True,
            supports_disable=True,
            min_thinking_budget_tokens=1024,
            reasoning_history_values=("disabled", "interleaved", "preserved"),
            notes=("Docs-backed family with native thinking support; keep validation advisory.",),
        )

    if base.startswith("gpt-oss-"):
        return ReasoningModelCapabilities(
            supports_reasoning_effort=True,
            supported_efforts=("low", "medium", "high"),
            supports_thinking=False,
            supports_disable=False,
            reasoning_history_values=("disabled", "interleaved", "preserved"),
            notes=("Docs-backed OpenAI OSS family; reasoning_effort is advisory only.",),
        )

    if base.startswith("qwen3") or base.startswith("deepseek-v3") or base.startswith("deepseek-v4") or base.startswith("glm-") or base.startswith("minimax-"):
        return ReasoningModelCapabilities(
            supports_reasoning_effort=True,
            supports_thinking=True,
            supports_disable=True,
            min_thinking_budget_tokens=1024,
            reasoning_history_values=("disabled", "interleaved", "preserved"),
            notes=("Docs-backed reasoning-capable family; keep enforcement advisory.",),
        )

    return _DEFAULT_CAPABILITIES


def classify_reasoning_model(upstream_model: str) -> ReasoningModelCapabilities:
    return _family_capabilities(upstream_model)
