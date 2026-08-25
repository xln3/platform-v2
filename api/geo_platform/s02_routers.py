"""Additive S02 router bundle for the S04 shared-main/OpenAPI integration handoff."""

from fastapi import APIRouter

from .analytics.router import router as analytics_router
from .evidence.router import router as evidence_router
from .exports.router import router as exports_router
from .intelligence.router import router as intelligence_router
from .reports.fact_suggestions_router import router as report_fact_suggestions_router
from .reports.formal_production_router import router as formal_production_router
from .reports.router import router as reports_router
from .service2_corpus.router import router as service2_corpus_router
from .sop.router import router as sop_router

router = APIRouter()
router.include_router(analytics_router)
router.include_router(evidence_router)
router.include_router(exports_router)
router.include_router(formal_production_router)
router.include_router(reports_router)
router.include_router(report_fact_suggestions_router)
router.include_router(intelligence_router)
router.include_router(service2_corpus_router)
router.include_router(sop_router)
