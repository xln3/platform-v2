from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from opentelemetry import context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from .brandrank.router import router as brandrank_router
from .collection.assist_router import router as assist_router
from .collection.capability_router import router as capability_router
from .collection.customer_account_router import router as customer_account_router
from .collection.governance_router import router as governance_router
from .collection.operations_router import router as operations_router
from .collection.router import router as collection_router
from .collection.schedule_router import router as schedule_router
from .collection.terminal_router import router as terminal_router
from .config import get_settings
from .contracts import ApiError, Health, Readiness
from .datasets.router import router as datasets_router
from .identity.router import router as identity_router
from .intake.router import public_router as intake_public_router
from .intake.router import router as intake_router
from .intake_form.router import router as intake_form_router
from .intake_form.router import token_router as intake_form_token_router
from .logging import configure_logging
from .observability import instrument_app
from .otp.router import router as otp_router
from .post_analysis.router import router as post_analysis_router
from .posting.router import router as posting_router
from .projects.catalog_router import router as project_catalog_router
from .projects.confirmation_router import router as confirmation_router
from .projects.customer_router import router as customer_router
from .projects.onboarding_router import router as onboarding_router
from .projects.router import router as projects_router
from .quotations.router import router as quotations_router
from .s02_routers import router as s02_router
from .variants.router import router as variants_router

settings = get_settings()
configure_logging(settings.log_level)
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    log.info("api_started", env=settings.env, version=settings.version)
    yield
    log.info("api_stopped")


app = FastAPI(
    title="GEO Platform V2 API",
    version=settings.version,
    summary="Authoritative contract for GEO Platform V2",
    lifespan=lifespan,
    responses={400: {"model": ApiError}, 401: {"model": ApiError}, 403: {"model": ApiError}},
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
instrument_app(app, settings)


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else f"req_{uuid4().hex}"


@app.middleware("http")
async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
    supplied = request.headers.get("X-Request-Id", "")
    request.state.request_id = (
        supplied if 1 <= len(supplied) <= 128 and supplied.isascii() else f"req_{uuid4().hex}"
    )
    carrier = {
        key: value
        for key in ("traceparent", "tracestate")
        if (value := request.headers.get(key)) is not None
    }
    token = context.attach(TraceContextTextMapPropagator().extract(carrier=carrier))
    try:
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response
    finally:
        context.detach(token)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    detail: dict[str, Any] = exc.detail if isinstance(exc.detail, dict) else {}
    code_value = detail.get("code")
    code = code_value if isinstance(code_value, str) else "http_error"
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={
            "error": {
                "code": code,
                "message": code.replace("_", " "),
                "request_id": _request_id(request),
                "details": {},
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = [
        {
            "location": [str(part) for part in error.get("loc", ())],
            "type": str(error.get("type", "validation_error")),
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "request validation failed",
                "request_id": _request_id(request),
                "details": {"fields": fields},
            }
        },
    )


@app.exception_handler(Exception)
async def internal_error(request: Request, exc: Exception) -> JSONResponse:
    request_id = _request_id(request)
    log.error(
        "request_failed",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        exception_type=type(exc).__name__,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "internal server error",
                "request_id": request_id,
                "details": {},
            }
        },
    )


app.include_router(identity_router)
app.include_router(projects_router)
app.include_router(quotations_router)
app.include_router(project_catalog_router)
app.include_router(confirmation_router)
app.include_router(customer_router)
app.include_router(onboarding_router)
app.include_router(collection_router)
app.include_router(assist_router)
app.include_router(otp_router)
app.include_router(schedule_router)
app.include_router(operations_router)
app.include_router(governance_router)
app.include_router(capability_router)
app.include_router(customer_account_router)
app.include_router(terminal_router)
app.include_router(s02_router)
app.include_router(datasets_router)
app.include_router(posting_router)
app.include_router(intake_public_router)
app.include_router(intake_router)
app.include_router(intake_form_router)
app.include_router(intake_form_token_router)
app.include_router(variants_router)
app.include_router(post_analysis_router)
app.include_router(brandrank_router)


@app.get("/api/v2/health", response_model=Health, tags=["platform"], operation_id="getHealth")
async def health() -> Health:
    return Health(status="ok", version=settings.version)


@app.get(
    "/api/v2/readiness",
    response_model=Readiness,
    tags=["platform"],
    operation_id="getReadiness",
)
async def readiness() -> Readiness:
    return Readiness(
        status="ready",
        checks={
            "postgres": "configured",
            "clickhouse": "configured",
            "temporal": "configured",
            "minio": "configured",
            "redis": "configured",
        },
    )
