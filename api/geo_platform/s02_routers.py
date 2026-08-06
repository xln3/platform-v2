"""Additive S02 router bundle for the S04 shared-main/OpenAPI integration handoff."""

from fastapi import APIRouter

from .analytics.router import router as analytics_router
from .evidence.router import router as evidence_router
from .exports.router import router as exports_router
from .intelligence.router import router as intelligence_router
from .reports.router import router as reports_router
from .sop.router import router as sop_router

router = APIRouter()
router.include_router(analytics_router)
router.include_router(evidence_router)
router.include_router(exports_router)
router.include_router(reports_router)
router.include_router(intelligence_router)
router.include_router(sop_router)
