from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from app.control.repository import AppRepository, KeyRecord
from app.dataplane.usage import UsageStats
from app.platform.config import Settings


@runtime_checkable
class ProxyRequestContext(Protocol):
    settings: Settings
    repository: AppRepository
    request_headers: Mapping[str, Any]
    body: dict[str, Any]
    model_name: str
    resolved_model: Any
    client_identity: str
    stable_key: str
    stable_key_source: str
    stable_key_hash_value: str
    affinity_header: str
    route_key: str
    selected_keys: list[KeyRecord]
    routing_metadata: Mapping[str, Any] | None


class RequestLogPayloadBuilder(Protocol):
    def __call__(
        self,
        *,
        endpoint: str,
        context: ProxyRequestContext,
        selected_key: KeyRecord | None,
        stream: bool,
        service_tier: str | None,
        usage: UsageStats,
        latency_ms: int | None,
        status_code: int,
        error_type: str | None,
        upstream_request_id: str | None,
    ) -> dict[str, Any]: ...


__all__ = ["ProxyRequestContext", "RequestLogPayloadBuilder"]
