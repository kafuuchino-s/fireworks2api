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

    if base.startswith("kimi-"):
        return ReasoningModelCapabilities(
            supports_reasoning_effort=True,
            supports_thinking=True,
            supports_disable=True,
            min_thinking_budget_tokens=1024,
            reasoning_history_values=("disabled", "interleaved", "preserved"),
            notes=("Docs-backed Kimi reasoning family; keep enforcement advisory.",),
        )

    return _DEFAULT_CAPABILITIES


def classify_reasoning_model(upstream_model: str) -> ReasoningModelCapabilities:
    return _family_capabilities(upstream_model)


def normalize_responses_reasoning_effort(
    upstream_model: str,
    effort: object,
) -> tuple[object, str | None]:
    """Normalize Responses reasoning effort values that Fireworks rejects.

    Fireworks accepts ``xhigh`` as a generic value, but live smoke shows some
    model families still reject ``xhigh``/``max`` inside Responses streams.
    Keep known-compatible families untouched and only downgrade families whose
    model-specific validation accepts at most high.
    """

    if not isinstance(effort, str):
        return effort, None
    raw = effort.strip()
    if not raw:
        return effort, None

    value = raw.lower()
    value = value.replace("-", "").replace("_", "").replace(" ", "")
    if value == "extrahigh":
        value = "xhigh"

    if value not in {"xhigh", "max"}:
        return raw.lower() if raw.lower() != effort else effort, None

    base = (upstream_model or "").strip().lower().rsplit("/", 1)[-1]
    # Per Fireworks API reference (docs.fireworks.ai/api-reference/post-chatcompletions,
    # reasoning_effort model-specific behavior):
    #   GLM 5.2 has two tiers — 'high' (High) and 'max'/'xhigh' (Max, the default);
    #   'low'/'medium' collapse to 'high'. xhigh and max both select the Max tier.
    # Older GLM (4.5/4.6/4.7/5.1) is binary on/off and rejects max/xhigh, so we
    # downgrade those to 'high'. Keep GLM 5.2's max/xhigh intact so it runs at Max.
    # NOTE: local docs/fireworks/post-chatcompletions.md cache predates GLM 5.x;
    # the GLM 5.2 row is only on the live API reference page so far.
    if base.startswith("glm-") and not base.startswith("glm-5p2"):
        return "high", "model_accepts_highest_effort_as_high"
    if base.startswith(("minimax-m2", "gpt-oss-", "deepseek-v3")):
        return "high", "model_accepts_highest_effort_as_high"
    if value != effort:
        return value, "reasoning_effort_alias_normalized"

    return value, None
