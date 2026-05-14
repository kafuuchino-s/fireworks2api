from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class KeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    api_key: str = Field(min_length=1)
    enabled: bool = True
    validate_with_fireworks: bool = True


class KeysBulkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_keys: list[str] = Field(min_length=1)
    enabled: bool = True
    validate_with_fireworks: bool = True


class KeyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    api_key: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None


class ModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str = Field(min_length=1)
    upstream_model: str = Field(min_length=1)
    enabled: bool = True


class ModelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str | None = Field(default=None, min_length=1)
    upstream_model: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None


class ModelImportItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str | None = Field(default=None, min_length=1)
    aliases: list[str] | None = None
    upstream_model: str = Field(min_length=1)
    enabled: bool = True


class ModelImportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[ModelImportItem] = Field(min_length=1)
