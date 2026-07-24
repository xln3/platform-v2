from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .collection.capability_router import router as capability_router
from .collection.customer_account_router import router as customer_account_router
from .collection.governance_router import router as governance_router
from .collection.router import router as collection_router
from .config import get_settings
from .contracts import ApiError, Health, Readiness
from .identity.router import router as identity_router
from .logging import configure_logging
from .projects.catalog_router import router as project_catalog_router
from .projects.customer_router import router as customer_router
from .projects.router import router as projects_router
from .s02_routers import router as s02_router

settings = get_settings()
configure_logging(settings.log_level)
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await log.ainfo("api_started", env=settings.env, version=settings.version)
    yield
    await log.ainfo("api_stopped")


app = FastAPI(
    title="GEO Platform V2 API",
    version=settings.version,
    summary="Authoritative contract for GEO Platform V2",
    lifespan=lifespan,
    responses={400: {"model": ApiError}, 401: {"model": ApiError}, 403: {"model": ApiError}},
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:45101",
        "http://127.0.0.1:45102",
        "http://127.0.0.1:45103",
        "http://127.0.0.1:45104",
        "http://127.0.0.1:45112",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else f"req_{uuid4().hex}"


@app.middleware("http")
async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
    supplied = request.headers.get("X-Request-Id", "")
    request.state.request_id = (
        supplied if 1 <= len(supplied) <= 128 and supplied.isascii() else f"req_{uuid4().hex}"
    )
    response = await call_next(request)
    response.headers["X-Request-Id"] = request.state.request_id
    return response


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
    await log.aerror(
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
app.include_router(project_catalog_router)
app.include_router(customer_router)
app.include_router(collection_router)
app.include_router(governance_router)
app.include_router(capability_router)
app.include_router(customer_account_router)
app.include_router(s02_router)


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
