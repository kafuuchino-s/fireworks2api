from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.control import fireworks_model_registry as _fireworks_model_registry
from app.control.fireworks_model_registry import (
    build_official_model_catalog,
)
from app.control.repository import AppRepository
from app.platform.config import Settings


FIREWORKS_API_BASE_URL = "https://api.fireworks.ai"


def classify_model_kind(model_id: str) -> str:
    base = _fireworks_model_registry.model_basename(model_id).lower()
    if base.startswith("flux-") or "kontext" in base:
        return "image"
    return "text"


def _raw_bool(raw: dict[str, Any], *names: str) -> bool | None:
    for name in names:
        if name in raw and raw[name] is not None:
            return bool(raw[name])
    return None


def _raw_int(raw: dict[str, Any], *names: str) -> int | None:
    for name in names:
        value = raw.get(name)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _is_non_empty_raw_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return len(value) > 0
    return True


def extract_model_pricing(raw: dict[str, Any]) -> dict[str, Any] | None:
    pricing_keys = (
        "pricing",
        "price",
        "prices",
        "cost",
        "costs",
        "rates",
        "billing",
        "inputPrice",
        "outputPrice",
        "promptTokenPrice",
        "completionTokenPrice",
        "pricePerInputToken",
        "pricePerOutputToken",
        "input_token_price",
        "output_token_price",
        "prompt_token_price",
        "completion_token_price",
    )
    extracted = {name: raw[name] for name in pricing_keys if name in raw and _is_non_empty_raw_value(raw[name])}
    if not extracted:
        return None
    return extracted


def build_supported_functionality(model_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    kind = str(raw.get("kind") or "").upper()
    base = model_basename(model_id).lower()
    fine_tuning = any(
        bool(raw.get(name))
        for name in (
            "tunable",
            "supportsLora",
            "supports_lora",
            "supervisedLoraTunable",
            "supervised_lora_tunable",
            "supervisedFullParameterTunable",
            "supervised_full_parameter_tunable",
            "rlLoraTunable",
            "rl_lora_tunable",
            "rlFullParameterTunable",
            "rl_full_parameter_tunable",
        )
    )
    return {
        "serverless": _raw_bool(raw, "supportsServerless", "supports_serverless"),
        "context_length": _raw_int(raw, "contextLength", "context_length"),
        "function_calling": _raw_bool(raw, "supportsTools", "supports_tools"),
        "image_input": _raw_bool(raw, "supportsImageInput", "supports_image_input"),
        "fine_tuning": fine_tuning,
        "embeddings": kind == "EMBEDDING_MODEL" or "embedding" in base or "embed" in base,
        "rerankers": "RERANK" in kind or "rerank" in base,
    }


def _pricing_lookup_keys(model_id: str) -> list[str]:
    model_id = _fireworks_model_registry.normalize_model_id(model_id)
    base = _fireworks_model_registry.model_basename(model_id)
    keys = [model_id]
    if model_id.startswith("accounts/"):
        keys.append(model_id.removeprefix("accounts/"))
    if base:
        keys.extend([
            f"accounts/fireworks/models/{base}",
            f"accounts/fireworks/routers/{base}",
            base,
        ])
    return list(dict.fromkeys(keys))


def lookup_official_pricing(model_id: str) -> dict[str, Any] | None:
    return _fireworks_model_registry.lookup_official_pricing(model_id)


def normalize_model_id(value: str) -> str:
    return _fireworks_model_registry.normalize_model_id(value)


def model_basename(model_id: str) -> str:
    return _fireworks_model_registry.model_basename(model_id)


def _base_model_for_fast_variant(model_id: str) -> str | None:
    base = _fireworks_model_registry.model_basename(model_id)
    if base.endswith("-fast"):
        return f"accounts/fireworks/models/{base.removesuffix('-fast')}"
    if base.endswith("-turbo"):
        return f"accounts/fireworks/models/{base.removesuffix('-turbo')}"
    return None


@dataclass(frozen=True)
class FireworksManagementContext:
    source: str
    api_key: str | None


def select_fireworks_api_key(settings: Settings, repository: AppRepository) -> FireworksManagementContext:
    stored = repository.list_keys(include_disabled=False)
    if stored:
        return FireworksManagementContext(source=f"stored:{stored[0].name}", api_key=stored[0].api_key)
    if settings.fireworks_api_keys:
        return FireworksManagementContext(source="env:fireworks_api_keys", api_key=settings.fireworks_api_keys[0])
    if settings.fireworks_api_keys_json:
        return FireworksManagementContext(source="env:fireworks_api_keys_json", api_key=settings.fireworks_api_keys_json[0].get("key"))
    return FireworksManagementContext(source="not_configured", api_key=None)


def build_model_mapping_from_fireworks_model_id(model_id: str) -> dict[str, Any]:
    alias = (_fireworks_model_registry.suggest_aliases_for_model(model_id) or [_fireworks_model_registry.model_basename(model_id)])[0]
    return {
        "alias": alias,
        "upstream_model": model_id,
        "enabled": True,
    }


def build_model_catalog_item(model: str | dict[str, Any], existing_aliases: set[str], existing_upstreams: set[str]) -> dict[str, Any]:
    if isinstance(model, str):
        upstream_model = _fireworks_model_registry.normalize_model_id(model)
        raw: dict[str, Any] = {}
    else:
        upstream_model = _fireworks_model_registry.normalize_model_id(
            str(model.get("id") or model.get("name") or model.get("model") or model.get("resource_name") or "")
        )
        raw = model
    aliases = _fireworks_model_registry.suggest_aliases_for_model(upstream_model)
    existing_alias_keys = {alias.casefold() for alias in existing_aliases}
    missing_aliases = [alias for alias in aliases if alias.casefold() not in existing_alias_keys]
    already_mapped = bool(aliases) and not missing_aliases
    if not aliases:
        already_mapped = upstream_model in existing_upstreams
    item = {
        "upstream_model": upstream_model,
        "kind": classify_model_kind(upstream_model),
        "supported_functionality": build_supported_functionality(upstream_model, raw),
        "suggested_alias": aliases[0] if aliases else None,
        "aliases": aliases,
        "missing_aliases": missing_aliases,
        "recommended": bool(aliases) and classify_model_kind(upstream_model) == "text",
        "already_mapped": already_mapped,
        "raw": raw,
    }
    if isinstance(raw, dict):
        base_model = _base_model_for_fast_variant(upstream_model)
        if base_model:
            base_raw = raw if raw.get("kind") or raw.get("supported_functionality") else {}
            base_supported = build_supported_functionality(base_model, base_raw)
            for field in ("serverless", "context_length", "function_calling", "image_input", "fine_tuning"):
                if item["supported_functionality"].get(field) is None and base_supported.get(field) is not None:
                    item["supported_functionality"][field] = base_supported[field]
    pricing = extract_model_pricing(raw)
    if pricing is None:
        pricing = lookup_official_pricing(upstream_model)
    if pricing is not None:
        item["pricing"] = pricing
    return item


def build_model_catalog(existing_aliases: set[str], existing_upstreams: set[str]) -> list[dict[str, Any]]:
    return build_official_model_catalog(existing_aliases, existing_upstreams)


class FireworksManagementClient:
    """Small client for Fireworks account/control-plane APIs.

    These APIs live at ``https://api.fireworks.ai/v1/...`` while inference
    uses the configured ``UPSTREAM_BASE_URL`` (normally
    ``https://api.fireworks.ai/inference/v1``). Keep them separate to avoid
    accidentally calling ``/inference/v1/v1/...``.
    """

    def __init__(self, settings: Settings, api_key: str):
        self._client = httpx.AsyncClient(
            base_url=f"{FIREWORKS_API_BASE_URL}/",
            timeout=settings.request_timeout_seconds,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        return await self._client.get(path.lstrip("/"), params=params)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "FireworksManagementClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()
