from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


SOURCE_URL = "https://docs.fireworks.ai/serverless/pricing"
SOURCE_CHECKED_AT = datetime(2026, 6, 13, tzinfo=timezone.utc)
DEEPSEEK_V4_FLASH_SOURCE_URL = "https://fireworks.ai/models/deepseek-ai/deepseek-v4-flash"


@dataclass(frozen=True)
class OfficialFireworksModel:
    model_id: str
    label: str
    kind: str
    recommended: bool
    upstream_model: str
    aliases: tuple[str, ...]
    supported_functionality: dict[str, bool | None]
    pricing: dict[str, dict[str, Any]]
    source_url: str = SOURCE_URL
    source_checked_at: datetime = SOURCE_CHECKED_AT


def normalize_model_id(value: str) -> str:
    return str(value or "").strip()


def model_basename(model_id: str) -> str:
    return normalize_model_id(model_id).rsplit("/", 1)[-1]


def _pricing(input_: float, cached_input: float, output: float) -> dict[str, Any]:
    return {
        "source": "fireworks_serverless_pricing",
        "unit": "usd_per_1m_tokens",
        "input": input_,
        "cached_input": cached_input,
        "output": output,
    }


def _functionality(context_length: int | None = None, *, tools: bool | None = None, image: bool | None = None, serverless: bool = True) -> dict[str, bool | int | None]:
    return {
        "serverless": serverless,
        "context_length": context_length,
        "function_calling": tools,
        "image_input": image,
        "fine_tuning": False,
        "embeddings": False,
        "rerankers": False,
    }


_OFFICIAL_MODELS: tuple[OfficialFireworksModel, ...] = (
    OfficialFireworksModel(
        model_id="accounts/fireworks/models/kimi-k2p7-code",
        label="Kimi K2.7 Code",
        kind="text",
        recommended=True,
        upstream_model="accounts/fireworks/models/kimi-k2p7-code",
        aliases=("kimi-k2.7-code",),
        supported_functionality=_functionality(262144, tools=True, image=True),
        pricing={"standard": _pricing(0.95, 0.19, 4.0), "priority": _pricing(1.425, 0.285, 6.0)},
    ),
    OfficialFireworksModel(
        model_id="accounts/fireworks/routers/kimi-k2p7-code-fast",
        label="Kimi K2.7 Code Fast router",
        kind="text",
        recommended=True,
        upstream_model="accounts/fireworks/routers/kimi-k2p7-code-fast",
        aliases=("kimi-k2.7-code-fast",),
        supported_functionality=_functionality(262144, tools=True, image=True),
        pricing={"fast": _pricing(1.9, 0.38, 8.0)},
    ),
    OfficialFireworksModel(
        model_id="accounts/fireworks/models/kimi-k2p6",
        label="Kimi K2.6",
        kind="text",
        recommended=True,
        upstream_model="accounts/fireworks/models/kimi-k2p6",
        aliases=("kimi-k2.6",),
        supported_functionality=_functionality(262144, tools=True, image=True),
        pricing={"standard": _pricing(0.95, 0.16, 4.0), "priority": _pricing(1.5, 0.22, 6.0)},
    ),
    OfficialFireworksModel(
        model_id="accounts/fireworks/routers/kimi-k2p6-turbo",
        label="Kimi K2.6 Turbo router",
        kind="text",
        recommended=True,
        upstream_model="accounts/fireworks/routers/kimi-k2p6-turbo",
        aliases=("kimi-k2.6-turbo",),
        supported_functionality=_functionality(262144, tools=True, image=True),
        pricing={"fast": _pricing(2.0, 0.30, 8.0)},
    ),
    OfficialFireworksModel("accounts/fireworks/models/kimi-k2p5", "Kimi K2.5", "text", True, "accounts/fireworks/models/kimi-k2p5", ("kimi-k2.5",), _functionality(262144, tools=True, image=True), {"standard": _pricing(0.6, 0.1, 3.0)}),
    OfficialFireworksModel("accounts/fireworks/models/deepseek-v4-pro", "DeepSeek V4 Pro", "text", True, "accounts/fireworks/models/deepseek-v4-pro", ("deepseek-v4-pro",), _functionality(1048576, tools=True, image=False), {"standard": _pricing(1.74, 0.145, 3.48), "priority": _pricing(2.61, 0.218, 5.22)}),
    OfficialFireworksModel(
        "accounts/fireworks/models/deepseek-v4-flash",
        "DeepSeek V4 Flash",
        "text",
        True,
        "accounts/fireworks/models/deepseek-v4-flash",
        ("deepseek-v4-flash",),
        _functionality(1040000, tools=True, image=False),
        {"standard": _pricing(0.14, 0.028, 0.28)},
        source_url=DEEPSEEK_V4_FLASH_SOURCE_URL,
    ),
    OfficialFireworksModel("accounts/fireworks/models/deepseek-v3", "DeepSeek V3 family", "text", True, "accounts/fireworks/models/deepseek-v3", ("deepseek-v3",), _functionality(), {"standard": _pricing(0.56, 0.28, 1.68)}),
    OfficialFireworksModel("accounts/fireworks/models/glm-5p1", "GLM 5.1", "text", True, "accounts/fireworks/models/glm-5p1", ("glm-5.1",), _functionality(202752, tools=True, image=False), {"standard": _pricing(1.4, 0.26, 4.4), "priority": _pricing(2.1, 0.39, 6.6)}),
    OfficialFireworksModel("accounts/fireworks/routers/glm-5p1-fast", "GLM 5.1 Fast router", "text", True, "accounts/fireworks/routers/glm-5p1-fast", ("glm-5.1-fast",), _functionality(202752, tools=True, image=False), {"fast": _pricing(2.8, 0.52, 8.8)}),
    OfficialFireworksModel("accounts/fireworks/models/glm-5", "GLM 5", "text", True, "accounts/fireworks/models/glm-5", ("glm-5",), _functionality(202752, tools=True, image=False), {"standard": _pricing(1.0, 0.2, 3.2)}),
    OfficialFireworksModel("accounts/fireworks/models/glm-4p7", "GLM 4.7", "text", True, "accounts/fireworks/models/glm-4p7", ("glm-4.7",), _functionality(), {"standard": _pricing(0.6, 0.3, 2.2)}),
    OfficialFireworksModel("accounts/fireworks/models/minimax-m3", "MiniMax M3", "text", True, "accounts/fireworks/models/minimax-m3", ("MiniMax-M3",), _functionality(524288, tools=True, image=True), {"standard": _pricing(0.3, 0.06, 1.2), "priority": _pricing(0.45, 0.09, 1.8)}),
    OfficialFireworksModel("accounts/fireworks/models/minimax-m2p7", "MiniMax 2.7", "text", True, "accounts/fireworks/models/minimax-m2p7", ("MiniMax-M2.7",), _functionality(196608, tools=True, image=False), {"standard": _pricing(0.3, 0.06, 1.2), "priority": _pricing(0.45, 0.09, 1.8)}),
    OfficialFireworksModel("accounts/fireworks/models/minimax-m2p5", "MiniMax 2.5", "text", True, "accounts/fireworks/models/minimax-m2p5", ("MiniMax-M2.5",), _functionality(), {"standard": _pricing(0.3, 0.03, 1.2)}),
    OfficialFireworksModel("accounts/fireworks/models/qwen3p7-plus", "Qwen 3.7 Plus", "text", True, "accounts/fireworks/models/qwen3p7-plus", ("qwen-3.7-plus",), _functionality(262144, tools=True, image=True), {"standard": _pricing(0.4, 0.08, 1.6)}),
    OfficialFireworksModel("accounts/fireworks/models/qwen3p6-plus", "Qwen 3.6 Plus", "text", True, "accounts/fireworks/models/qwen3p6-plus", ("qwen-3.6-plus",), _functionality(262144, tools=True, image=True), {"standard": _pricing(0.5, 0.10, 3.0)}),
    OfficialFireworksModel("accounts/fireworks/models/qwen3-vl-30b-a3b-thinking", "Qwen3 VL 30B A3B", "vision", True, "accounts/fireworks/models/qwen3-vl-30b-a3b-thinking", ("qwen3-vl-30b-a3b-thinking",), _functionality(image=True), {"standard": _pricing(0.15, 0.075, 0.6)}),
    OfficialFireworksModel("accounts/fireworks/models/nemotron-3-ultra-nvfp4", "NVIDIA Nemotron 3 Ultra", "text", True, "accounts/fireworks/models/nemotron-3-ultra-nvfp4", ("nemotron-3-ultra",), _functionality(262144, tools=True, image=False), {"standard": _pricing(0.6, 0.12, 2.4)}),
    OfficialFireworksModel("accounts/fireworks/models/gpt-oss-120b", "GPT OSS 120B", "text", True, "accounts/fireworks/models/gpt-oss-120b", ("gpt-oss-120b",), _functionality(), {"standard": _pricing(0.15, 0.015, 0.6), "priority": _pricing(0.18, 0.018, 0.72)}),
    OfficialFireworksModel("accounts/fireworks/models/gpt-oss-20b", "GPT OSS 20B", "text", True, "accounts/fireworks/models/gpt-oss-20b", ("gpt-oss-20b",), _functionality(), {"standard": _pricing(0.07, 0.035, 0.3)}),
)

STANDARD_MODEL_ALIASES = {m.model_id: list(m.aliases) for m in _OFFICIAL_MODELS if m.aliases}
OFFICIAL_SERVERLESS_PRICING = {m.model_id: {tier: dict(price) for tier, price in m.pricing.items()} for m in _OFFICIAL_MODELS}


def list_official_models() -> list[OfficialFireworksModel]:
    return list(_OFFICIAL_MODELS)


def get_official_model(model_id: str) -> OfficialFireworksModel | None:
    needle = normalize_model_id(model_id)
    for model in _OFFICIAL_MODELS:
        if needle in {model.model_id, model.upstream_model, *model.aliases, model_basename(model.model_id)}:
            return model
    return None


def official_model_metadata(model_id: str) -> dict[str, Any] | None:
    model = get_official_model(model_id)
    if model is None:
        return None
    return {
        "model_id": model.model_id,
        "label": model.label,
        "kind": model.kind,
        "recommended": model.recommended,
        "upstream_model": model.upstream_model,
        "aliases": list(model.aliases),
        "supported_functionality": dict(model.supported_functionality),
        "pricing": {tier: dict(value) for tier, value in model.pricing.items()},
        "source_url": model.source_url,
        "source_checked_at": model.source_checked_at,
    }


def suggest_aliases_for_model(model_id: str) -> list[str]:
    model = get_official_model(model_id)
    return list(model.aliases) if model and model.aliases else []


def lookup_official_pricing(model_id: str, tier: str | None = None) -> dict[str, Any] | None:
    model = get_official_model(model_id)
    if model is None:
        return None
    if tier is not None:
        price = model.pricing.get(tier)
        return dict(price) if price is not None else None
    return {name: dict(price) for name, price in model.pricing.items()}


def build_official_model_catalog(existing_aliases: set[str], existing_upstreams: set[str]) -> list[dict[str, Any]]:
    existing_alias_keys = {alias.casefold() for alias in existing_aliases}
    catalog: list[dict[str, Any]] = []
    for model in _OFFICIAL_MODELS:
        missing_aliases = [alias for alias in model.aliases if alias.casefold() not in existing_alias_keys]
        catalog.append({
            "upstream_model": model.upstream_model,
            "label": model.label,
            "kind": model.kind,
            "supported_functionality": dict(model.supported_functionality),
            "suggested_alias": model.aliases[0] if model.aliases else None,
            "aliases": list(model.aliases),
            "missing_aliases": missing_aliases,
            "recommended": model.recommended,
            "already_mapped": (model.upstream_model in existing_upstreams) or (bool(model.aliases) and not missing_aliases),
            "pricing": {tier: dict(price) for tier, price in model.pricing.items()},
            "source_url": model.source_url,
            "source_checked_at": model.source_checked_at,
        })
    return catalog


def default_model_mapping_specs() -> list[dict[str, Any]]:
    return [{"alias": m.aliases[0], "upstream_model": m.upstream_model, "enabled": True} for m in _OFFICIAL_MODELS if m.aliases]
