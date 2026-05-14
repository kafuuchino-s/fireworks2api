from __future__ import annotations

import logging
import os
import sys

from fastapi import FastAPI
from fastapi.requests import Request
from fastapi.middleware.cors import CORSMiddleware

from app.platform.bootstrap import bootstrap_app_state
from app.platform.config import get_settings
from app.platform.logging import configure_logging
from app.products.anthropic import messages_router
from app.products import health
from app.products.admin import router as admin
from app.products.openai.router import chat_completions_router, completions_router, embeddings_router, models_router, rerank_router, responses_router
from app.products.openai.errors import OpenAIRequestError, openai_error_response_json
from app.products.web.router import router as web_router, static_mount

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    repository = bootstrap_app_state(settings)
    keys = repository.list_keys(include_disabled=True) if hasattr(repository, "list_keys") else []
    malformed_key_count = (
        sum(1 for key in keys if repository.is_locally_malformed_fireworks_key(key.api_key))
        if hasattr(repository, "is_locally_malformed_fireworks_key")
        else 0
    )
    db_path = getattr(settings, "db_path", None)
    data_dir = getattr(settings, "data_dir", None)
    logger.info(
        "startup runtime identity pid=%s cwd=%s executable=%s db_path=%s data_dir=%s sync_env_keys_on_startup=%s env_key_count=%s env_json_key_count=%s db_key_count=%s malformed_key_count=%s",
        os.getpid(),
        os.getcwd(),
        sys.executable,
        db_path.resolve() if hasattr(db_path, "resolve") else db_path,
        data_dir.resolve() if hasattr(data_dir, "resolve") else data_dir,
        getattr(settings, "sync_env_keys_on_startup", None),
        len(getattr(settings, "fireworks_api_keys", []) or []),
        len(getattr(settings, "fireworks_api_keys_json", []) or []),
        len(keys),
        malformed_key_count,
    )

    app = FastAPI(title=settings.app_name)

    @app.exception_handler(OpenAIRequestError)
    async def _openai_request_error_handler(_request: Request, exc: OpenAIRequestError):
        return openai_error_response_json(exc.message, param=exc.param, code=exc.code, status_code=exc.status_code)

    app.state.settings = settings
    app.state.repository = repository

    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Admin-Token"],
        )

    app.include_router(health.router)
    app.include_router(chat_completions_router)
    app.include_router(completions_router)
    app.include_router(embeddings_router)
    app.include_router(rerank_router)
    app.include_router(responses_router)
    app.include_router(models_router)
    app.include_router(messages_router)
    app.include_router(admin.router)
    if settings.enable_admin_static:
        app.include_router(web_router)
        app.mount("/static", static_mount, name="static")
    return app


app = create_app()
