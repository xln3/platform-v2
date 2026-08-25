# ruff: noqa: B008
"""Tenant-safe internal API for the Service 2 all-U corpus and fact manifest."""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from domain.evidence.dlp import assert_secret_free
from geo_platform.config import get_settings
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.identity.policy import Principal, get_principal
from geo_platform.pagination import decode_keyset_cursor, encode_keyset_cursor, set_cursor_headers
from geo_platform.projects.models import Project
from geo_platform.tenancy.database import get_db
from geo_platform.tenancy.models import Tenant
from geo_platform.tenancy.repository import set_tenant_context

from .analysis_models import (
    AnalysisModelNotAllowed,
    configured_model_ids,
    model_catalog,
    resolve_model,
)
from .pagination_policy import (
    SERVICE2_CORPUS_DEFAULT_PAGE_SIZE,
    SERVICE2_CORPUS_MAX_PAGE_SIZE,
    SERVICE2_CORPUS_MIN_PAGE_SIZE,
)
from .schemas import (
    AnalysisModelCatalogView,
    AnalysisModelOptionView,
    BatchCreate,
    BatchView,
    CorpusFetchState,
    CorpusItemView,
    CorpusPage,
    CorpusProcessingState,
    CorpusReviewState,
    FindingCreate,
    FindingLevel,
    FindingPage,
    FindingReviewCreate,
    FindingReviewState,
    FindingView,
    FrozenManifestOptionView,
    FrozenManifestView,
    LifecycleReceipt,
)
from .service import (
    Conflict,
    EvidenceInvalid,
    Invalid,
    NotFound,
    PreconditionFailed,
    Service2CorpusService,
    manifest_view,
)

router = APIRouter(
    prefix="/api/v2/internal/service2-source-corpus/projects/{project_pub_id}",
    tags=["internal-service2-source-corpus"],
)

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=16,
        max_length=128,
        pattern=r"^[\x20-\x7e]+$",
    ),
]


def _service() -> Service2CorpusService:
    settings = get_settings()
    return Service2CorpusService(
        store=ContentAddressedObjectStore(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
        ),
        allowed_analysis_models=configured_model_ids(settings),
    )


def _project_context(
    session: Session, *, principal: Principal, project_pub_id: str
) -> tuple[Tenant, Project]:
    tenant = session.scalar(select(Tenant).where(Tenant.pub_id == principal.tenant_pub_id))
    if tenant is None:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"})
    set_tenant_context(session, tenant_id=tenant.id, tenant_pub_id=tenant.pub_id)
    project = session.scalar(
        select(Project).where(Project.tenant_id == tenant.id, Project.pub_id == project_pub_id)
    )
    if project is None:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"})
    return tenant, project


def _require_any(principal: Principal, *permissions: str) -> None:
    if not any(principal.allows(permission) for permission in permissions):
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})


def _read(principal: Principal) -> None:
    _require_any(principal, "formal_report:read", "intelligence:read")


def _control(principal: Principal) -> None:
    _require_any(principal, "formal_report:produce", "intelligence:write")


def _review(principal: Principal) -> None:
    _require_any(principal, "intelligence:review", "report:review")


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(status_code=404, detail={"code": str(exc)})
    if isinstance(exc, PreconditionFailed):
        return HTTPException(status_code=412, detail={"code": str(exc)})
    if isinstance(exc, Conflict):
        return HTTPException(status_code=409, detail={"code": str(exc)})
    return HTTPException(status_code=422, detail={"code": str(exc)})


@router.get("/analysis-models", response_model=AnalysisModelCatalogView)
def analysis_models(
    project_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> AnalysisModelCatalogView:
    _read(principal)
    _project_context(session, principal=principal, project_pub_id=project_pub_id)
    settings = get_settings()
    return AnalysisModelCatalogView(
        default_model=resolve_model(settings, None),
        models=[AnalysisModelOptionView.model_validate(row) for row in model_catalog(settings)],
    )


@router.post("/batches", response_model=BatchView, status_code=201)
def create_batch(
    project_pub_id: str,
    body: BatchCreate,
    response: Response,
    idempotency_key: IdempotencyKey,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> BatchView:
    _control(principal)
    tenant, project = _project_context(session, principal=principal, project_pub_id=project_pub_id)
    service = _service()
    try:
        body = body.model_copy(
            update={"analysis_model": resolve_model(get_settings(), body.analysis_model)}
        )
        receipt = service.create_batch(
            session,
            tenant_id=tenant.id,
            tenant_pub_id=tenant.pub_id,
            project_id=project.id,
            project_pub_id=project.pub_id,
            actor_pub_id=principal.actor_pub_id,
            idempotency_key=idempotency_key,
            body=body,
        )
        row = service.batch_row(
            session,
            tenant_id=tenant.id,
            project_id=project.id,
            batch_pub_id=receipt.batch_pub_id,
        )
        view = BatchView.model_validate(service.batch_view(session, row))
        session.commit()
    except AnalysisModelNotAllowed as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail={"code": "analysis_model_not_allowed"}) from exc
    except (Conflict, Invalid, NotFound) as exc:
        session.rollback()
        raise _http_error(exc) from exc
    if receipt.replayed:
        response.status_code = 200
    response.headers["Idempotency-Key"] = idempotency_key
    return view


@router.get("/batches/current", response_model=BatchView)
def current_batch(
    project_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> BatchView:
    _read(principal)
    tenant, project = _project_context(session, principal=principal, project_pub_id=project_pub_id)
    row = (
        session.execute(
            text(
                """
                SELECT batch.*,project.pub_id AS project_pub_id,
                       ARRAY(
                         SELECT link.run_pub_id FROM platform.service2_corpus_batch_run link
                         WHERE link.batch_id=batch.id ORDER BY link.ordinal
                       ) AS run_pub_ids
                FROM platform.service2_corpus_batch batch
                JOIN platform.project project ON project.id=batch.project_id
                WHERE batch.tenant_id=:tenant_id AND batch.project_id=:project_id
                ORDER BY batch.created_at DESC,batch.pub_id DESC LIMIT 1
                """
            ),
            {"tenant_id": tenant.id, "project_id": project.id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "service2_batch_not_found"})
    return BatchView.model_validate(_service().batch_view(session, row))


@router.get("/batches/{batch_pub_id}", response_model=BatchView)
def get_batch(
    project_pub_id: str,
    batch_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> BatchView:
    _read(principal)
    tenant, project = _project_context(session, principal=principal, project_pub_id=project_pub_id)
    service = _service()
    try:
        row = service.batch_row(
            session,
            tenant_id=tenant.id,
            project_id=project.id,
            batch_pub_id=batch_pub_id,
        )
    except NotFound as exc:
        raise _http_error(exc) from exc
    return BatchView.model_validate(service.batch_view(session, row))


def _item_view(row: dict[str, object]) -> CorpusItemView:
    return CorpusItemView(
        item_pub_id=str(row["pub_id"]),
        occurrence_pub_id=str(row["occurrence_pub_id"]),
        run_pub_id=str(row["run_pub_id"]),
        answer_pub_id=str(row["answer_task_pub_id"]),
        source_url_pub_id=str(row["source_url_pub_id"]),
        snapshot_pub_id=(str(row["snapshot_pub_id"]) if row["snapshot_pub_id"] else None),
        source_document_pub_id=(
            str(row["source_document_pub_id"]) if row["source_document_pub_id"] else None
        ),
        fetch_attempt_pub_id=(
            str(row["fetch_attempt_pub_id"]) if row["fetch_attempt_pub_id"] else None
        ),
        raw_url=str(row["raw_url"]),
        canonical_url=str(row["canonical_url"]),
        site_host=str(row["site_host"]),
        occurrence_ordinal=int(str(row["occurrence_ordinal"])),
        u_rank=int(str(row["u_rank"])) if row["u_rank"] is not None else None,
        captured_at=row["captured_at"],  # type: ignore[arg-type]
        platform=str(row["platform"]),
        model=str(row["model"]),
        region=str(row["region"]),
        collection_surface=(str(row["collection_surface"]) if row["collection_surface"] else None),
        question=str(row["question"]),
        retrieval_query=(str(row["retrieval_query"]) if row["retrieval_query"] else None),
        u_state=str(row["u_state"]),
        fetch_state=cast(CorpusFetchState, str(row["fetch_state"])),
        processing_state=cast(CorpusProcessingState, str(row["processing_state"])),
        entity_state=str(row["entity_state"]),
        judgment_state=str(row["judgment_state"]),
        review_state=cast(CorpusReviewState, str(row["review_state"])),
        entered_judgment=bool(row["entered_judgment"]),
        finding_count=int(str(row["finding_count"])),
        retry_count=int(str(row["retry_count"])),
        failure_code=str(row["failure_code"]) if row["failure_code"] else None,
        manual_evidence_state=str(row["manual_evidence_state"]),
        version=int(str(row["version"])),
    )


@router.get("/batches/{batch_pub_id}/items", response_model=CorpusPage)
def list_corpus_items(
    project_pub_id: str,
    batch_pub_id: str,
    response: Response,
    cursor: str | None = None,
    page_size: int = Query(
        default=SERVICE2_CORPUS_DEFAULT_PAGE_SIZE,
        ge=SERVICE2_CORPUS_MIN_PAGE_SIZE,
        le=SERVICE2_CORPUS_MAX_PAGE_SIZE,
    ),
    processing_state: CorpusProcessingState | None = None,
    fetch_state: CorpusFetchState | None = None,
    review_state: CorpusReviewState | None = None,
    attribution_confidence: Literal["verified", "probable", "weak", "unknown"] | None = None,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> CorpusPage:
    _read(principal)
    tenant, project = _project_context(session, principal=principal, project_pub_id=project_pub_id)
    service = _service()
    try:
        batch = service.batch_row(
            session,
            tenant_id=tenant.id,
            project_id=project.id,
            batch_pub_id=batch_pub_id,
        )
    except NotFound as exc:
        raise _http_error(exc) from exc
    filters = {
        "batch_pub_id": batch_pub_id,
        "processing_state": processing_state,
        "fetch_state": fetch_state,
        "review_state": review_state,
        "attribution_confidence": attribution_confidence,
    }
    anchor = (
        decode_keyset_cursor(
            cursor,
            kind="service2-corpus-items",
            tenant_pub_id=tenant.pub_id,
            filters=filters,
        )
        if cursor
        else None
    )
    attribution_clause = ""
    if attribution_confidence in {"verified", "probable", "weak"}:
        attribution_clause = """
          AND EXISTS (
            SELECT 1 FROM platform.service2_relation_finding finding
            WHERE finding.corpus_item_id=item.id
              AND (:attribution= finding.publisher_confidence
                   OR :attribution=finding.commissioner_confidence)
          )
        """
    elif attribution_confidence == "unknown":
        attribution_clause = """
          AND NOT EXISTS (
            SELECT 1 FROM platform.service2_relation_finding finding
            WHERE finding.corpus_item_id=item.id
              AND (finding.publisher_confidence<>'unknown'
                   OR finding.commissioner_confidence<>'unknown')
          )
        """
    where_sql = (
        """
        item.tenant_id=:tenant_id AND item.project_id=:project_id AND item.batch_id=:batch_id
        AND (CAST(:processing_state AS text) IS NULL
             OR item.processing_state=:processing_state)
        AND (CAST(:fetch_state AS text) IS NULL OR item.fetch_state=:fetch_state)
        AND (CAST(:review_state AS text) IS NULL OR item.review_state=:review_state)
        """
        + attribution_clause
    )
    params = {
        "tenant_id": tenant.id,
        "project_id": project.id,
        "batch_id": batch["id"],
        "processing_state": processing_state,
        "fetch_state": fetch_state,
        "review_state": review_state,
        "attribution": attribution_confidence,
        "cursor_time": anchor.created_at if anchor else None,
        "cursor_pub_id": anchor.pub_id if anchor else None,
        "limit": page_size + 1,
    }
    filtered_count = session.execute(
        text(f"SELECT count(*) FROM platform.service2_corpus_item item WHERE {where_sql}"),
        params,
    ).scalar_one()
    rows = (
        session.execute(
            text(
                f"""
                SELECT item.* FROM platform.service2_corpus_item item
                WHERE {where_sql}
                  AND (CAST(:cursor_time AS timestamptz) IS NULL
                       OR (item.captured_at,item.pub_id)<(
                            CAST(:cursor_time AS timestamptz),
                            CAST(:cursor_pub_id AS text)))
                ORDER BY item.captured_at DESC,item.pub_id DESC LIMIT :limit
                """
            ),
            params,
        )
        .mappings()
        .all()
    )
    has_more = len(rows) > page_size
    visible = rows[:page_size]
    next_cursor = (
        encode_keyset_cursor(
            kind="service2-corpus-items",
            tenant_pub_id=tenant.pub_id,
            filters=filters,
            created_at=visible[-1]["captured_at"],
            pub_id=str(visible[-1]["pub_id"]),
        )
        if has_more and visible
        else None
    )
    set_cursor_headers(
        response,
        next_cursor=next_cursor,
        has_more=has_more,
        total_count=int(filtered_count),
        extra_counts={"X-All-U-Total": int(batch["expected_occurrence_count"])},
    )
    return CorpusPage(
        batch_pub_id=batch_pub_id,
        data=[_item_view(dict(row)) for row in visible],
        filtered_count=int(filtered_count),
        all_u_total=int(batch["expected_occurrence_count"]),
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/batches/{batch_pub_id}/findings", response_model=FindingPage)
def list_findings(
    project_pub_id: str,
    batch_pub_id: str,
    response: Response,
    cursor: str | None = None,
    page_size: int = Query(
        default=SERVICE2_CORPUS_DEFAULT_PAGE_SIZE,
        ge=SERVICE2_CORPUS_MIN_PAGE_SIZE,
        le=SERVICE2_CORPUS_MAX_PAGE_SIZE,
    ),
    review_state: FindingReviewState | None = None,
    level: FindingLevel | None = None,
    ledger: Literal["statement", "exposure"] | None = None,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> FindingPage:
    _read(principal)
    tenant, project = _project_context(session, principal=principal, project_pub_id=project_pub_id)
    service = _service()
    try:
        batch = service.batch_row(
            session,
            tenant_id=tenant.id,
            project_id=project.id,
            batch_pub_id=batch_pub_id,
        )
    except NotFound as exc:
        raise _http_error(exc) from exc
    filters = {
        "batch_pub_id": batch_pub_id,
        "review_state": review_state,
        "level": level,
        "ledger": ledger,
    }
    anchor = (
        decode_keyset_cursor(
            cursor,
            kind="service2-findings",
            tenant_pub_id=tenant.pub_id,
            filters=filters,
        )
        if cursor
        else None
    )
    where_sql = """
      finding.tenant_id=:tenant_id AND finding.project_id=:project_id
      AND finding.batch_id=:batch_id
      AND (CAST(:review_state AS text) IS NULL
           OR finding.current_review_state=:review_state)
      AND (CAST(:level AS text) IS NULL OR finding.level=:level)
      AND (CAST(:ledger AS text) IS NULL OR finding.ledger=:ledger)
    """
    params = {
        "tenant_id": tenant.id,
        "project_id": project.id,
        "batch_id": batch["id"],
        "review_state": review_state,
        "level": level,
        "ledger": ledger,
        "cursor_time": anchor.created_at if anchor else None,
        "cursor_pub_id": anchor.pub_id if anchor else None,
        "limit": page_size + 1,
    }
    filtered = session.execute(
        text(f"SELECT count(*) FROM platform.service2_relation_finding finding WHERE {where_sql}"),
        params,
    ).scalar_one()
    total = session.execute(
        text(
            "SELECT count(*) FROM platform.service2_relation_finding "
            "WHERE tenant_id=:tenant_id AND batch_id=:batch_id"
        ),
        params,
    ).scalar_one()
    rows = (
        session.execute(
            text(
                f"""
                SELECT finding.*,batch.pub_id AS batch_pub_id,item.pub_id AS item_pub_id,
                       item.occurrence_pub_id,item.canonical_url,
                       snapshot.pub_id AS snapshot_pub_id
                FROM platform.service2_relation_finding finding
                JOIN platform.service2_corpus_batch batch ON batch.id=finding.batch_id
                JOIN platform.service2_corpus_item item ON item.id=finding.corpus_item_id
                JOIN platform.source_page_snapshot snapshot ON snapshot.id=finding.snapshot_id
                WHERE {where_sql}
                  AND (CAST(:cursor_time AS timestamptz) IS NULL
                       OR (finding.created_at,finding.pub_id)<(
                            CAST(:cursor_time AS timestamptz),
                            CAST(:cursor_pub_id AS text)))
                ORDER BY finding.created_at DESC,finding.pub_id DESC LIMIT :limit
                """
            ),
            params,
        )
        .mappings()
        .all()
    )
    has_more = len(rows) > page_size
    visible = rows[:page_size]
    next_cursor = (
        encode_keyset_cursor(
            kind="service2-findings",
            tenant_pub_id=tenant.pub_id,
            filters=filters,
            created_at=visible[-1]["created_at"],
            pub_id=str(visible[-1]["pub_id"]),
        )
        if has_more and visible
        else None
    )
    set_cursor_headers(
        response,
        next_cursor=next_cursor,
        has_more=has_more,
        total_count=int(filtered),
        extra_counts={"X-All-Findings-Total": int(total)},
    )
    return FindingPage(
        batch_pub_id=batch_pub_id,
        data=[FindingView.model_validate(service.finding_view(row)) for row in visible],
        filtered_count=int(filtered),
        all_findings_total=int(total),
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.post("/batches/{batch_pub_id}/findings", response_model=FindingView, status_code=201)
def create_finding(
    project_pub_id: str,
    batch_pub_id: str,
    body: FindingCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> FindingView:
    _require_any(principal, "intelligence:write")
    tenant, project = _project_context(session, principal=principal, project_pub_id=project_pub_id)
    try:
        assert_secret_free(body.model_dump_json())
        row = _service().create_finding(
            session,
            tenant_id=tenant.id,
            project_id=project.id,
            batch_pub_id=batch_pub_id,
            body=body,
        )
        view = FindingView.model_validate(_service().finding_view(row))
        session.commit()
        return view
    except EvidenceInvalid as exc:
        # Fail-closed findings are rejected, but their bounded validation-attempt
        # row is part of the audit trail and must survive the 422 response.
        session.commit()
        raise _http_error(exc) from exc
    except (Conflict, Invalid, NotFound) as exc:
        session.rollback()
        raise _http_error(exc) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail={"code": "sensitive_input_rejected"}) from exc


@router.get("/batches/{batch_pub_id}/findings/{finding_pub_id}", response_model=FindingView)
def get_finding(
    project_pub_id: str,
    batch_pub_id: str,
    finding_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> FindingView:
    _read(principal)
    tenant, project = _project_context(session, principal=principal, project_pub_id=project_pub_id)
    try:
        row = _service().finding_row(
            session,
            tenant_id=tenant.id,
            project_id=project.id,
            finding_pub_id=finding_pub_id,
        )
    except NotFound as exc:
        raise _http_error(exc) from exc
    if row["batch_pub_id"] != batch_pub_id:
        raise HTTPException(status_code=404, detail={"code": "service2_finding_not_found"})
    return FindingView.model_validate(_service().finding_view(row))


def _if_match_version(value: str) -> int:
    matched = re.fullmatch(r'(?:W/)?"?([1-9]\d*)"?', value.strip())
    if matched is None:
        raise HTTPException(status_code=422, detail={"code": "invalid_if_match"})
    return int(matched.group(1))


@router.post(
    "/batches/{batch_pub_id}/findings/{finding_pub_id}/reviews",
    response_model=FindingView,
)
def review_finding(
    project_pub_id: str,
    batch_pub_id: str,
    finding_pub_id: str,
    body: FindingReviewCreate,
    response: Response,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> FindingView:
    _review(principal)
    tenant, project = _project_context(session, principal=principal, project_pub_id=project_pub_id)
    try:
        assert_secret_free(body.rationale)
        row, replayed = _service().review_finding(
            session,
            tenant_id=tenant.id,
            project_id=project.id,
            tenant_pub_id=tenant.pub_id,
            finding_pub_id=finding_pub_id,
            expected_version=_if_match_version(if_match),
            idempotency_key=idempotency_key,
            reviewer_pub_id=principal.actor_pub_id,
            body=body,
        )
        if row["batch_pub_id"] != batch_pub_id:
            raise NotFound("service2_finding_not_found")
        view = FindingView.model_validate(_service().finding_view(row))
        session.commit()
    except (Conflict, Invalid, NotFound, PreconditionFailed) as exc:
        session.rollback()
        raise _http_error(exc) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail={"code": "sensitive_input_rejected"}) from exc
    response.headers["Idempotency-Replayed"] = "true" if replayed else "false"
    response.headers["ETag"] = f'"{view.version}"'
    return view


@router.post(
    "/batches/{batch_pub_id}/actions/{action}",
    response_model=LifecycleReceipt,
    status_code=202,
)
def lifecycle_action(
    project_pub_id: str,
    batch_pub_id: str,
    action: Literal["start", "pause", "resume", "retry", "cancel"],
    idempotency_key: IdempotencyKey,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> LifecycleReceipt:
    _control(principal)
    tenant, project = _project_context(session, principal=principal, project_pub_id=project_pub_id)
    settings = get_settings()
    try:
        status, version, replayed = _service().transition(
            session,
            tenant_id=tenant.id,
            project_id=project.id,
            tenant_pub_id=tenant.pub_id,
            project_pub_id=project.pub_id,
            batch_pub_id=batch_pub_id,
            actor_pub_id=principal.actor_pub_id,
            idempotency_key=idempotency_key,
            action=action,
            task_queue=settings.analysis_temporal_task_queue,
            source_task_queue=settings.temporal_task_queue,
        )
        session.commit()
    except (Conflict, Invalid, NotFound) as exc:
        session.rollback()
        raise _http_error(exc) from exc
    return LifecycleReceipt(
        batch_pub_id=batch_pub_id,
        status=status,  # type: ignore[arg-type]
        version=version,
        replayed=replayed,
    )


@router.post(
    "/batches/{batch_pub_id}/freeze",
    response_model=FrozenManifestView,
)
def freeze_batch(
    project_pub_id: str,
    batch_pub_id: str,
    response: Response,
    idempotency_key: IdempotencyKey,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> FrozenManifestView:
    _review(principal)
    tenant, project = _project_context(session, principal=principal, project_pub_id=project_pub_id)
    try:
        manifest, replayed = _service().freeze(
            session,
            tenant_id=tenant.id,
            project_id=project.id,
            tenant_pub_id=tenant.pub_id,
            batch_pub_id=batch_pub_id,
            actor_pub_id=principal.actor_pub_id,
            idempotency_key=idempotency_key,
        )
        view = FrozenManifestView.model_validate(manifest_view(manifest, batch_pub_id))
        session.commit()
    except (Conflict, Invalid, NotFound) as exc:
        session.rollback()
        raise _http_error(exc) from exc
    response.headers["Idempotency-Replayed"] = "true" if replayed else "false"
    return view


@router.get("/batches/{batch_pub_id}/manifest", response_model=FrozenManifestView)
def get_manifest(
    project_pub_id: str,
    batch_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> FrozenManifestView:
    _read(principal)
    tenant, project = _project_context(session, principal=principal, project_pub_id=project_pub_id)
    row = (
        session.execute(
            text(
                """
                SELECT manifest.*
                FROM platform.service2_fact_manifest manifest
                JOIN platform.service2_corpus_batch batch ON batch.id=manifest.batch_id
                WHERE manifest.tenant_id=:tenant_id AND manifest.project_id=:project_id
                  AND batch.pub_id=:batch_pub_id AND batch.status='frozen'
                ORDER BY manifest.revision DESC LIMIT 1
                """
            ),
            {
                "tenant_id": tenant.id,
                "project_id": project.id,
                "batch_pub_id": batch_pub_id,
            },
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "service2_manifest_not_found"})
    return FrozenManifestView.model_validate(manifest_view(row, batch_pub_id))


@router.get("/manifests", response_model=list[FrozenManifestOptionView])
def list_manifests(
    project_pub_id: str,
    window_start: date | None = Query(default=None),
    window_end: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[FrozenManifestOptionView]:
    """List exact immutable Service 2 fact sets available to formal production."""

    _read(principal)
    tenant, project = _project_context(session, principal=principal, project_pub_id=project_pub_id)
    rows = session.execute(
        text(
            """
            SELECT manifest.pub_id AS manifest_pub_id,manifest.revision,
                   manifest.manifest_hash,manifest.case_count,
                   manifest.evidence_reference_count,manifest.created_at,
                   batch.pub_id AS batch_pub_id,batch.window_start,batch.window_end
            FROM platform.service2_fact_manifest manifest
            JOIN platform.service2_corpus_batch batch ON batch.id=manifest.batch_id
            WHERE manifest.tenant_id=:tenant_id AND manifest.project_id=:project_id
              AND batch.status='frozen'
              AND (
                CAST(:window_start AS date) IS NULL
                OR (batch.window_start AT TIME ZONE 'Asia/Shanghai')::date=:window_start
              )
              AND (
                CAST(:window_end AS date) IS NULL
                OR (batch.window_end AT TIME ZONE 'Asia/Shanghai')::date=:window_end
              )
            ORDER BY manifest.created_at DESC,manifest.pub_id DESC
            LIMIT :limit
            """
        ),
        {
            "tenant_id": tenant.id,
            "project_id": project.id,
            "window_start": window_start,
            "window_end": window_end,
            "limit": limit,
        },
    ).mappings()
    return [FrozenManifestOptionView.model_validate(dict(row)) for row in rows]


__all__ = ["router"]
