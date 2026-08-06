# ruff: noqa: B008
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from geo_platform.analytics.service import AnalyticsService
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.evidence.service import EvidenceService

from ..config import get_settings
from ..identity.policy import Principal, get_principal
from .service import ExportService

router = APIRouter(prefix="/api/v2/exports", tags=["exports"])


class MetricExportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_pub_id: str
    start: date
    end: date
    dimensions: dict[str, str] = Field(default_factory=dict)


def _dsn() -> str:
    settings = get_settings()
    return (settings.runtime_postgres_dsn or settings.postgres_dsn).replace(
        "postgresql+psycopg://", "postgresql://"
    )


def _service() -> ExportService:
    settings = get_settings()
    store = ContentAddressedObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
    )
    evidence = EvidenceService(dsn=_dsn(), store=store)
    return ExportService(
        dsn=_dsn(),
        analytics=AnalyticsService(dsn=_dsn()),
        evidence=evidence,
    )


@router.post("/metrics", status_code=201)
def create_metric_export(
    body: MetricExportCreate,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("project:read")
    try:
        return _service().export_metrics_xlsx(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=body.project_pub_id,
            start=body.start,
            end=body.end,
            dimensions=body.dimensions,
            created_by_pub_id=principal.actor_pub_id,
            provenance=RedactedProvenance(
                platform_account_pub_id=None,
                browser_profile_version_pub_id=None,
                session_event_pub_id=None,
                channel=CaptureChannel.API,
                authorization_scope=("project:read",),
                adapter_version="exports-api-v1",
                capture_time=datetime.now(UTC),
                access_class=AccessClass.CUSTOMER_PRIVATE,
            ),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "export_facts_not_found"}) from exc
