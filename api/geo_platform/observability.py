from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, Response
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor

from .config import Settings

HTTP_REQUESTS = Counter(
    "geo_http_requests_total",
    "GEO API HTTP requests",
    ("method", "route", "status"),
)
HTTP_DURATION = Histogram(
    "geo_http_request_duration_seconds",
    "GEO API HTTP request duration",
    ("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)


def configure_tracing(settings: Settings, *, service_name: str | None = None) -> None:
    if not settings.otel_exporter_otlp_endpoint:
        return
    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        return
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": service_name or settings.otel_service_name,
                "service.version": settings.version,
                "deployment.environment.name": settings.env,
            }
        )
    )
    endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces"
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)


def instrument_app(app: FastAPI, settings: Settings) -> None:
    configure_tracing(settings)
    if settings.otel_exporter_otlp_endpoint:
        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls="/metrics,/api/v2/health,/api/v2/readiness",
        )

    @app.middleware("http")
    async def prometheus_metrics(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            HTTP_REQUESTS.labels(request.method, route_path, str(status)).inc()
            HTTP_DURATION.labels(request.method, route_path).observe(time.perf_counter() - started)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def connect_temporal(settings: Settings, **kwargs: Any) -> Client:
    configure_tracing(settings)
    interceptors = list(kwargs.pop("interceptors", ()))
    interceptors.append(TracingInterceptor())
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        interceptors=interceptors,
        **kwargs,
    )
