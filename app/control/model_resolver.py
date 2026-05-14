from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.control.repository import ModelMapping
from app.platform.config import Settings


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _mapping_like(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dumped

    dict_method = getattr(value, "dict", None)
    if callable(dict_method):
        dumped = dict_method()
        if isinstance(dumped, Mapping):
            return dumped

    if hasattr(value, "__dict__"):
        return vars(value)

    return None


def _value(value: Any, key: str, default: Any = None) -> Any:
    mapping = _mapping_like(value)
    if mapping is not None and key in mapping:
        return mapping[key]
    return getattr(value, key, default)


def _is_enabled(mapping: Any) -> bool:
    return bool(_value(mapping, "enabled", True))


def _get_model(repository: Any, alias: str) -> ModelMapping | None:
    alias = _text(alias)
    if not alias:
        return None

    get_model = getattr(repository, "get_model", None)
    if callable(get_model):
        model = get_model(alias)
        if model is not None:
            return model

    get_model_case_insensitive = getattr(repository, "get_model_case_insensitive", None)
    if callable(get_model_case_insensitive):
        model = get_model_case_insensitive(alias)
        if model is not None:
            return model

    list_models = getattr(repository, "list_models", None)
    if callable(list_models):
        for model in list_models():
            if _text(_value(model, "alias")).casefold() == alias.casefold():
                return model

    if isinstance(repository, Mapping):
        model = repository.get(alias)
        if model is not None:
            return model

    if isinstance(repository, (list, tuple, set)):
        for model in repository:
            if _text(_value(model, "alias")).casefold() == alias.casefold():
                return model

    return None


@dataclass(frozen=True)
class ResolvedModel:
    requested_model: str
    alias: str
    upstream_model: str
    service_tier: str | None = None
    enabled: bool = True
    exact_alias: bool = False
    passthrough: bool = False

    @property
    def resolved_alias(self) -> str:
        return self.alias


class ModelResolutionError(ValueError):
    def __init__(self, model: str, message: str | None = None) -> None:
        self.model = model
        super().__init__(message or f"Unknown model: {model}")


def _from_mapping(
    requested_model: str,
    mapping: ModelMapping,
    *,
    alias: str | None = None,
    exact_alias: bool,
    passthrough: bool = False,
) -> ResolvedModel:
    return ResolvedModel(
        requested_model=requested_model,
        alias=alias or _text(_value(mapping, "alias", requested_model)) or requested_model,
        upstream_model=_text(_value(mapping, "upstream_model", requested_model)) or requested_model,
        service_tier=None,
        enabled=bool(_value(mapping, "enabled", True)),
        exact_alias=exact_alias,
        passthrough=passthrough,
    )


class ModelResolver:
    def __init__(
        self,
        repository: Any,
        settings: Settings | bool | None = None,
        allow_unknown_model_passthrough: bool | None = None,
        allow_unknown_passthrough: bool | None = None,
    ) -> None:
        if isinstance(settings, bool) and allow_unknown_model_passthrough is None and allow_unknown_passthrough is None:
            allow_unknown_model_passthrough = settings
            settings = None

        if allow_unknown_model_passthrough is None:
            allow_unknown_model_passthrough = allow_unknown_passthrough

        self.repository = repository
        self.settings = settings if not isinstance(settings, bool) else None
        if allow_unknown_model_passthrough is None:
            allow_unknown_model_passthrough = bool(getattr(self.settings, "allow_unknown_model_passthrough", False))
        self.allow_unknown_model_passthrough = bool(allow_unknown_model_passthrough)

    def resolve(self, model: str) -> ResolvedModel:
        if not isinstance(model, str):
            raise TypeError("model must be a string")

        requested_model = model.strip()
        if not requested_model:
            raise ModelResolutionError(requested_model, "model is required")

        exact = _get_model(self.repository, requested_model)
        if exact is not None:
            if not _is_enabled(exact):
                raise ModelResolutionError(requested_model, f"model '{requested_model}' is disabled")
            return _from_mapping(requested_model, exact, exact_alias=True)

        if self.allow_unknown_model_passthrough:
            return ResolvedModel(
                requested_model=requested_model,
                alias=requested_model,
                upstream_model=requested_model,
                service_tier=None,
                enabled=True,
                exact_alias=False,
                passthrough=True,
            )

        raise ModelResolutionError(requested_model)


def resolve_model(
    repository: Any,
    model: str,
    settings: Settings | bool | None = None,
    allow_unknown_model_passthrough: bool | None = None,
    allow_unknown_passthrough: bool | None = None,
) -> ResolvedModel:
    if allow_unknown_model_passthrough is None:
        allow_unknown_model_passthrough = allow_unknown_passthrough

    resolver = ModelResolver(
        repository,
        settings=settings,
        allow_unknown_model_passthrough=allow_unknown_model_passthrough,
    )
    return resolver.resolve(model)


__all__ = [
    "ModelResolutionError",
    "ModelResolver",
    "ResolvedModel",
    "resolve_model",
]
