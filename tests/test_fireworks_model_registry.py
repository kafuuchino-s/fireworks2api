from __future__ import annotations

from app.control.fireworks_model_registry import (
    build_official_model_catalog,
    get_official_model,
    lookup_official_pricing,
    official_model_metadata,
    suggest_aliases_for_model,
)


def test_aliases_and_lookup() -> None:
    assert suggest_aliases_for_model("accounts/fireworks/models/kimi-k2p6") == ["kimi-k2.6"]
    assert suggest_aliases_for_model("accounts/fireworks/routers/kimi-k2p6-turbo") == ["kimi-k2.6-turbo"]
    assert get_official_model("kimi-k2.6").model_id == "accounts/fireworks/models/kimi-k2p6"
    assert official_model_metadata("accounts/fireworks/models/gpt-oss-120b")["label"] == "GPT OSS 120B"


def test_registry_includes_verified_capability_metadata() -> None:
    kimi = official_model_metadata("accounts/fireworks/models/kimi-k2p6")
    assert kimi["supported_functionality"]["context_length"] == 262144
    assert kimi["supported_functionality"]["function_calling"] is True
    assert kimi["supported_functionality"]["image_input"] is True

    glm_fast = official_model_metadata("accounts/fireworks/routers/glm-5p1-fast")
    assert glm_fast["supported_functionality"]["context_length"] == 202752
    assert glm_fast["supported_functionality"]["function_calling"] is True
    assert glm_fast["supported_functionality"]["image_input"] is False


def test_registry_includes_deepseek_v4_flash_serverless_metadata() -> None:
    flash = official_model_metadata("deepseek-v4-flash")
    assert flash["upstream_model"] == "accounts/fireworks/models/deepseek-v4-flash"
    assert flash["label"] == "DeepSeek V4 Flash"
    assert flash["recommended"] is True
    assert flash["supported_functionality"]["serverless"] is True
    assert flash["supported_functionality"]["context_length"] == 1040000
    assert flash["supported_functionality"]["function_calling"] is True
    assert flash["supported_functionality"]["image_input"] is False
    assert flash["pricing"]["standard"]["input"] == 0.14
    assert flash["pricing"]["standard"]["cached_input"] == 0.03
    assert flash["pricing"]["standard"]["output"] == 0.28
    assert flash["source_url"] == "https://fireworks.ai/models/deepseek-ai/deepseek-v4-flash"
    assert suggest_aliases_for_model("accounts/fireworks/models/deepseek-v4-flash") == ["deepseek-v4-flash"]


def test_pricing_tiers_are_distinct_and_copied() -> None:
    standard = lookup_official_pricing("accounts/fireworks/models/kimi-k2p6", tier="standard")
    priority = lookup_official_pricing("accounts/fireworks/models/kimi-k2p6", tier="priority")
    fast = lookup_official_pricing("accounts/fireworks/routers/kimi-k2p6-turbo", tier="fast")
    assert standard["input"] == 0.95
    assert priority["input"] == 1.5
    assert fast["input"] == 2.0
    assert fast["output"] == 8.0
    assert lookup_official_pricing("accounts/fireworks/models/deepseek-v4-flash", tier="standard")["input"] == 0.14
    assert lookup_official_pricing("accounts/fireworks/routers/glm-5p1-fast", tier="fast")["input"] == 2.8
    standard["input"] = 123
    assert lookup_official_pricing("accounts/fireworks/models/kimi-k2p6", tier="standard")["input"] == 0.95


def test_unknown_model_has_no_alias_or_pricing() -> None:
    assert suggest_aliases_for_model("accounts/fireworks/models/unknown") == []
    assert lookup_official_pricing("accounts/fireworks/models/unknown") is None


def test_official_catalog_marks_mapped_and_missing_aliases() -> None:
    catalog = build_official_model_catalog(existing_aliases={"kimi-k2.6"}, existing_upstreams={"accounts/fireworks/models/gpt-oss-20b"})
    kimi = next(item for item in catalog if item["upstream_model"] == "accounts/fireworks/models/kimi-k2p6")
    gpt = next(item for item in catalog if item["upstream_model"] == "accounts/fireworks/models/gpt-oss-20b")
    assert kimi["already_mapped"] is True
    assert kimi["missing_aliases"] == []
    assert gpt["already_mapped"] is True
