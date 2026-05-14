from __future__ import annotations

from .chat_completions import router as chat_completions_router
from .completions import router as completions_router
from .embeddings import router as embeddings_router
from .models import router as models_router
from .rerank import router as rerank_router
from .responses import router as responses_router

__all__ = [
    "chat_completions_router",
    "completions_router",
    "embeddings_router",
    "models_router",
    "rerank_router",
    "responses_router",
]
