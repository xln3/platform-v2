# ruff: noqa: B008
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.evidence.service import EvidenceService
from geo_platform.identity.policy import Principal, get_principal
from geo_platform.tenancy.ids import new_pub_id

from ..config import get_settings
from .repository import MetricsV2Repository
from .schemas import (
    ContributionPageView,
    DecisionOverrideRequest,
    DecisionOverrideView,
    ExportCreate,
    ExportView,
    JobView,
    MetricCatalogView,
    MetricSnapshotDetailView,
    PublicationView,
    PublishRequest,
    QueryContributionPageView,
    RecomputeRequest,
    SemanticDecisionDetailView,
    SemanticEventDetailView,
    SnapshotRequest,
    SnapshotRequestAccepted,
    SnapshotSetView,
)
from .service import (
    MetricsV2Conflict,
    MetricsV2Invalid,
    MetricsV2RepositoryProtocol,
    MetricsV2Service,
)

router = APIRouter(prefix="/api/v2/metrics", tags=["metrics-v2"])

PublicIdPath = Annotated[
    str,
    Path(min_length=5, max_length=120, pattern=r"^[a-z][a-z0-9]*_[A-Za-z0-9_-]+$"),
]


def _dsn() -> str:
    settings = get_settings()
    return (settings.runtime_postgres_dsn or settings.postgres_dsn).replace(
        "postgresql+psycopg://", "postgresql://", 1
    )


def _repository() -> MetricsV2Repository:
    return MetricsV2Repository(dsn=_dsn())


def _service() -> MetricsV2Service:
    return MetricsV2Service(repository=cast(MetricsV2RepositoryProtocol, _repository()))


def _evidence_service() -> EvidenceService:
    settings = get_settings()
    return EvidenceService(
        dsn=_dsn(),
        store=ContentAddressedObjectStore(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
        ),
    )


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Vary"] = "Cookie, Authorization"
    response.headers["X-Content-Type-Options"] = "nosniff"


def _not_found(exc: LookupError) -> HTTPException:
    # Never reveal whether an object belongs to another tenant.
    return HTTPException(status_code=404, detail={"code": "metrics_v2_resource_not_found"})


@router.get("/catalog", response_model=MetricCatalogView, operation_id="getMetricCatalogV2")
def metric_catalog_v2(
    response: Response,
    principal: Principal = Depends(get_principal),
) -> MetricCatalogView:
    principal.require("project:read")
    _private(response)
    return _service().catalog()


@router.get(
    "/projects/{project_pub_id}/snapshot-sets/current",
    response_model=SnapshotSetView,
    operation_id="getCurrentMetricSnapshotSetV2",
)
def current_snapshot_set_v2(
    response: Response,
    project_pub_id: PublicIdPath,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    model: list[str] = Query(default_factory=list, max_length=100),
    region: list[str] = Query(default_factory=list, max_length=100),
    mode: list[str] = Query(default_factory=list, max_length=100),
    focal_entity_id: list[str] = Query(default_factory=list, max_length=100),
    publication_channel: Literal["official", "shadow"] = Query(default="official"),
    principal: Principal = Depends(get_principal),
) -> SnapshotSetView:
    principal.require("project:read")
    if publication_channel == "shadow" and not principal.allows("metrics:publish"):
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    if (start is None) != (end is None) or (start is not None and end is not None and start > end):
        raise HTTPException(status_code=422, detail={"code": "invalid_metric_window"})
    _private(response)
    try:
        return _service().current_snapshot_set(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            start=start.isoformat() if start else None,
            end=end.isoformat() if end else None,
            models=tuple(sorted(set(model))),
            regions=tuple(sorted(set(region))),
            modes=tuple(sorted(set(mode))),
            focal_entity_ids=tuple(sorted(set(focal_entity_id))),
            publication_channel=publication_channel,
        )
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/projects/{project_pub_id}/snapshot-requests",
    response_model=SnapshotRequestAccepted,
    status_code=202,
    operation_id="requestMetricSnapshotSetV2",
)
def request_snapshot_set_v2(
    response: Response,
    body: SnapshotRequest,
    project_pub_id: PublicIdPath,
    principal: Principal = Depends(get_principal),
) -> SnapshotRequestAccepted:
    principal.require("project:read")
    _private(response)
    try:
        return _service().request_snapshot(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project_pub_id,
            request=body,
            requested_by=principal.actor_pub_id,
        )
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.get(
    "/snapshot-jobs/{job_pub_id}",
    response_model=JobView,
    operation_id="getMetricSnapshotJobV2",
)
def snapshot_job_v2(
    response: Response,
    job_pub_id: PublicIdPath,
    principal: Principal = Depends(get_principal),
) -> JobView:
    principal.require("project:read")
    _private(response)
    try:
        return _service().snapshot_job(tenant_pub_id=principal.tenant_pub_id, job_pub_id=job_pub_id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.get(
    "/decision-jobs/{job_pub_id}",
    response_model=JobView,
    operation_id="getSemanticDecisionJobV2",
)
def decision_job_v2(
    response: Response,
    job_pub_id: PublicIdPath,
    principal: Principal = Depends(get_principal),
) -> JobView:
    principal.require("project:read")
    _private(response)
    try:
        return _service().decision_job(tenant_pub_id=principal.tenant_pub_id, job_pub_id=job_pub_id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.get(
    "/snapshot-sets/{set_pub_id}",
    response_model=SnapshotSetView,
    operation_id="getMetricSnapshotSetV2",
)
def snapshot_set_v2(
    response: Response,
    set_pub_id: PublicIdPath,
    principal: Principal = Depends(get_principal),
) -> SnapshotSetView:
    principal.require("project:read")
    _private(response)
    try:
        return _service().snapshot_set(tenant_pub_id=principal.tenant_pub_id, set_pub_id=set_pub_id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.get(
    "/snapshots/{snapshot_pub_id}",
    response_model=MetricSnapshotDetailView,
    operation_id="getMetricSnapshotV2",
)
def metric_snapshot_v2(
    response: Response,
    snapshot_pub_id: PublicIdPath,
    principal: Principal = Depends(get_principal),
) -> MetricSnapshotDetailView:
    principal.require("project:read")
    _private(response)
    try:
        return _service().snapshot(
            tenant_pub_id=principal.tenant_pub_id, snapshot_pub_id=snapshot_pub_id
        )
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.get(
    "/snapshots/{snapshot_pub_id}/queries",
    response_model=QueryContributionPageView,
    operation_id="listMetricQueryContributionsV2",
)
def metric_query_contributions_v2(
    response: Response,
    snapshot_pub_id: PublicIdPath,
    cursor: str | None = Query(default=None, min_length=8, max_length=2_000),
    limit: int = Query(default=50, ge=1, le=100),
    query: str | None = Query(default=None, min_length=1, max_length=200),
    principal: Principal = Depends(get_principal),
) -> QueryContributionPageView:
    principal.require("project:read")
    _private(response)
    try:
        return _service().query_contributions(
            tenant_pub_id=principal.tenant_pub_id,
            snapshot_pub_id=snapshot_pub_id,
            cursor=cursor,
            limit=limit,
            query=query,
        )
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.get(
    "/snapshots/{snapshot_pub_id}/contributions",
    response_model=ContributionPageView,
    operation_id="listMetricContributionsV2",
)
def metric_contributions_v2(
    response: Response,
    snapshot_pub_id: PublicIdPath,
    cursor: str | None = Query(default=None, min_length=8, max_length=2_000),
    limit: int = Query(default=50, ge=1, le=100),
    eligibility_status: Literal[
        "included_hit",
        "included_miss",
        "excluded",
        "not_applicable",
        "analysis_unknown",
        "analysis_failed",
    ]
    | None = Query(default=None),
    reason_code: str | None = Query(default=None, min_length=1, max_length=120),
    query: str | None = Query(default=None, min_length=1, max_length=200),
    model: str | None = Query(default=None, min_length=1, max_length=120),
    region: str | None = Query(default=None, min_length=1, max_length=120),
    mode: str | None = Query(default=None, min_length=1, max_length=80),
    hit: bool | None = Query(default=None),
    principal: Principal = Depends(get_principal),
) -> ContributionPageView:
    principal.require("project:read")
    _private(response)
    try:
        return _service().contributions(
            tenant_pub_id=principal.tenant_pub_id,
            snapshot_pub_id=snapshot_pub_id,
            cursor=cursor,
            limit=limit,
            eligibility_status=eligibility_status,
            reason_code=reason_code,
            query=query,
            model=model,
            region=region,
            mode=mode,
            hit=hit,
        )
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.get(
    "/semantic-events/{event_pub_id}",
    response_model=SemanticEventDetailView,
    operation_id="getSemanticEventV2",
)
def semantic_event_v2(
    response: Response,
    event_pub_id: PublicIdPath,
    principal: Principal = Depends(get_principal),
) -> SemanticEventDetailView:
    principal.require("project:read")
    _private(response)
    try:
        return _service().semantic_event(
            tenant_pub_id=principal.tenant_pub_id, event_pub_id=event_pub_id
        )
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.get(
    "/semantic-decisions/{decision_pub_id}",
    response_model=SemanticDecisionDetailView,
    operation_id="getSemanticDecisionV2",
)
def semantic_decision_v2(
    response: Response,
    decision_pub_id: PublicIdPath,
    principal: Principal = Depends(get_principal),
) -> SemanticDecisionDetailView:
    principal.require("project:read")
    _private(response)
    try:
        return _service().semantic_decision(
            tenant_pub_id=principal.tenant_pub_id, decision_pub_id=decision_pub_id
        )
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/snapshot-sets/{set_pub_id}/exports",
    response_model=ExportView,
    status_code=201,
    operation_id="createMetricSnapshotExportV2",
)
def metric_export_v2(
    response: Response,
    body: ExportCreate,
    set_pub_id: PublicIdPath,
    principal: Principal = Depends(get_principal),
) -> ExportView:
    principal.require("project:read")
    _private(response)
    try:
        service = _service()
        payload, mime_type, digest = service.render_export(
            tenant_pub_id=principal.tenant_pub_id,
            set_pub_id=set_pub_id,
            export_format=body.format,
        )
        snapshot_set = service.snapshot_set(
            tenant_pub_id=principal.tenant_pub_id,
            set_pub_id=set_pub_id,
        )
        evidence = _evidence_service()
        now = datetime.now(UTC)
        evidence_pub_id = new_pub_id("evd")
        stored = evidence.capture(
            evidence_pub_id=evidence_pub_id,
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=snapshot_set.project_pub_id,
            kind=f"metric_v2_export_{body.format}",
            payload=payload,
            mime_type=mime_type,
            source_url=None,
            provenance=RedactedProvenance(
                platform_account_pub_id=None,
                browser_profile_version_pub_id=None,
                session_event_pub_id=None,
                channel=CaptureChannel.API,
                authorization_scope=("project:read",),
                adapter_version="metric-export-v2",
                capture_time=now,
                access_class=AccessClass.CUSTOMER_PRIVATE,
            ),
        )
        export_pub_id = new_pub_id("mxe")
        _repository().persist_export_record(
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=snapshot_set.project_pub_id,
            export_pub_id=export_pub_id,
            snapshot_set_pub_id=set_pub_id,
            snapshot_set_hash=snapshot_set.snapshot_set_hash,
            window_start=snapshot_set.window.start,
            window_end=snapshot_set.window.end,
            export_format=body.format,
            evidence_pub_id=stored.metadata_pub_id or evidence_pub_id,
            created_by_pub_id=principal.actor_pub_id,
        )
        return ExportView(
            export_pub_id=export_pub_id,
            snapshot_set_pub_id=set_pub_id,
            status="succeeded",
            format=body.format,
            artifact_hash=digest,
            download_url=evidence.store.presign_get(stored.key, expires_seconds=300),
            expires_at=now + timedelta(minutes=5),
        )
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/operations/snapshot-sets/{set_pub_id}/publish",
    response_model=PublicationView,
    operation_id="publishMetricSnapshotSetV2",
)
def publish_metric_snapshot_set_v2(
    response: Response,
    body: PublishRequest,
    set_pub_id: PublicIdPath,
    principal: Principal = Depends(get_principal),
) -> PublicationView:
    principal.require("metrics:publish")
    _private(response)
    try:
        return _service().publish(
            tenant_pub_id=principal.tenant_pub_id,
            set_pub_id=set_pub_id,
            request=body,
            published_by=principal.actor_pub_id,
        )
    except LookupError as exc:
        raise _not_found(exc) from exc
    except MetricsV2Conflict as exc:
        raise HTTPException(
            status_code=409, detail={"code": "metric_publication_conflict"}
        ) from exc


@router.post(
    "/operations/recompute-jobs",
    response_model=JobView,
    status_code=202,
    operation_id="createMetricRecomputeJobV2",
)
def recompute_metrics_v2(
    response: Response,
    body: RecomputeRequest,
    principal: Principal = Depends(get_principal),
) -> JobView:
    principal.require("metrics:recompute")
    _private(response)
    try:
        return _service().recompute(
            tenant_pub_id=principal.tenant_pub_id,
            request=body,
            requested_by=principal.actor_pub_id,
        )
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/operations/semantic-decisions/{decision_pub_id}/overrides",
    response_model=DecisionOverrideView,
    status_code=201,
    operation_id="overrideSemanticDecisionV2",
)
def override_semantic_decision_v2(
    response: Response,
    body: DecisionOverrideRequest,
    decision_pub_id: PublicIdPath,
    principal: Principal = Depends(get_principal),
) -> DecisionOverrideView:
    principal.require("semantic:override")
    _private(response)
    try:
        return _service().override_decision(
            tenant_pub_id=principal.tenant_pub_id,
            decision_pub_id=decision_pub_id,
            request=body,
            actor_pub_id=principal.actor_pub_id,
            allow_official_publication=principal.allows("metrics:publish"),
        )
    except LookupError as exc:
        raise _not_found(exc) from exc
    except MetricsV2Conflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "semantic_decision_override_conflict"},
        ) from exc
    except MetricsV2Invalid as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc


__all__ = ["router"]
