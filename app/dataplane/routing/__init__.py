"""Routing dataplane helpers."""

from .affinity import (
    build_route_key,
    client_identity_from_request,
    derived_cache_key,
    extract_stable_key,
    stable_key_hash,
)
from .failover import FailureDecision, apply_failure_to_key, classify_upstream_failure
from .sticky_router import (
    AccountGate,
    CandidateSelection,
    account_bucket_for_key,
    account_id_for_key,
    candidate_keys,
    healthy_keys,
    is_key_healthy,
    normalize_account_id,
    order_keys,
    rendezvous_identity_score,
    rendezvous_score,
    select_candidate_keys,
)

__all__ = [
    "FailureDecision",
    "AccountGate",
    "CandidateSelection",
    "account_bucket_for_key",
    "account_id_for_key",
    "apply_failure_to_key",
    "build_route_key",
    "candidate_keys",
    "client_identity_from_request",
    "derived_cache_key",
    "extract_stable_key",
    "healthy_keys",
    "is_key_healthy",
    "classify_upstream_failure",
    "normalize_account_id",
    "order_keys",
    "rendezvous_identity_score",
    "rendezvous_score",
    "select_candidate_keys",
    "stable_key_hash",
]
