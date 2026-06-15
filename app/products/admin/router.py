from __future__ import annotations

from fastapi import APIRouter, Depends

from app.platform.auth import require_admin_auth

from .billing import router as billing_router
from .cache import router as cache_router
from .config import router as config_router
from .costs import router as costs_router
from .fireworks import router as fireworks_router
from .keys import router as keys_router
from .models import router as models_router
from .overview import router as overview_router
from .requests import router as requests_router
from .security import router as security_router
from .transform_debug import router as transform_debug_router

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_auth)])

router.include_router(overview_router)
router.include_router(config_router)
router.include_router(billing_router)
router.include_router(cache_router)
router.include_router(costs_router)
router.include_router(keys_router)
router.include_router(models_router)
router.include_router(fireworks_router)
router.include_router(security_router)
router.include_router(requests_router)
router.include_router(transform_debug_router)
