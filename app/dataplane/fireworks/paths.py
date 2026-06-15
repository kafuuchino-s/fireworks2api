from __future__ import annotations

from urllib.parse import urlsplit



_CANONICAL_INFERENCE_PATHS = {
    "chat_completions": "v1/chat/completions",
    "completions": "v1/completions",
    "responses": "v1/responses",
    "responses_lifecycle": "v1/responses",
    "embeddings": "v1/embeddings",
    "rerank": "v1/rerank",
    "anthropic_messages": "v1/messages",
    "models": "v1/models",
}


def resolve_inference_path(upstream_base_url: str, endpoint: str) -> str:
    canonical_path = _CANONICAL_INFERENCE_PATHS[endpoint].lstrip("/")
    base_path = urlsplit(str(upstream_base_url)).path.rstrip("/")
    if base_path.endswith("/v1"):
        return canonical_path.removeprefix("v1/")
    return canonical_path
