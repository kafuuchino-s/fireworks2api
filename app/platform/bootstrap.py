from __future__ import annotations

from app.control.repository import AppRepository
from app.platform.config import Settings
from app.platform.storage.db import init_db
from app.platform.runtime_config import apply_persisted_config_overrides, ensure_affinity_hash_secret


def bootstrap_app_state(settings: Settings) -> AppRepository:
    init_db(settings.db_path)
    repository = AppRepository(settings.db_path)
    apply_persisted_config_overrides(settings, repository)
    ensure_affinity_hash_secret(settings, repository)
    repository.bootstrap_from_env(settings)
    repository.bootstrap_default_models()
    return repository
