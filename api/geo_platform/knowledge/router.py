"""HTTP API v1 for the evidence-driven knowledge-evolution middleware."""

from __future__ import annotations

# ruff: noqa: B008
from dataclasses import asdict
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from domain.knowledge_evolution.contracts import ObservationDraft
from domain.knowledge_evolution.events import event_envelope
from domain.knowledge_evolution.release import KnowledgeReleaseError, KnowledgeReleaseStore
from domain.knowledge_evolution.runtime import ReasoningError

from ..config import get_settings
from ..identity.policy import Principal, get_principal
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.repository import TenantRepository
from . import service
from .models import Candidate, Evidence, Proposal
from .repository import KnowledgeConflict, KnowledgeNotFound, KnowledgeRepository
from .schemas import (
    ActivationRequest,
    AdjudicationCreate,
    AdjudicationView,
    AuditEventView,
    CandidatePage,
    CandidateReopen,
    CandidateView,
    ChangeSetCreate,
    ChangeSetView,
    ConnectorRunCreate,
    ConnectorRunView,
    EvidenceCreate,
    EvidenceView,
    IngestReceipt,
    KnowledgeEventView,
    MetricsView,
    ObservationBatchRequest,
    ProposalCreate,
    ProposalView,
    ReleaseCreate,
    ReleaseView,
    RuntimeResolveRequest,
    RuntimeResolveResponse,
    ServiceStatus,
)

router = APIRouter(prefix="/api/v2/knowledge/v1", tags=["knowledge-evolution-v1"])


def _actor(principal: Principal) -> str:
    return principal.user_pub_id or principal.subject


def _repository(session: Session, principal: Principal) -> KnowledgeRepository:
    # Principal validation normally sets RLS context.  Reassert it here so the
    # service remains safe under tests, service accounts, and future auth modes.
    TenantRepository(session, principal.tenant_pub_id)
    return KnowledgeRepository(session, principal.tenant_pub_id)


def _candidate_view(row: Candidate) -> CandidateView:
    return CandidateView(
        pub_id=row.pub_id,
        namespace=row.namespace,
        domain=row.domain,
        aggregation_key=row.aggregation_key,
        surface_forms=list(row.surface_forms),
        observation_count=row.observation_count,
        source_count=row.source_count,
        state=row.state,
        priority=row.priority,
        policy_version=row.policy_version,
        evidence_version=row.evidence_version,
        reopen_reason=row.reopen_reason,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
    )


def _proposal_view(session: Session, row: Proposal) -> ProposalView:
    candidate = session.get(Candidate, row.candidate_id) if row.candidate_id else None
    return ProposalView(
        pub_id=row.pub_id,
        namespace=row.namespace,
        domain=row.domain,
        candidate_pub_id=candidate.pub_id if candidate else None,
        operation=row.operation,
        target_stable_id=row.target_stable_id,
        payload=dict(row.payload),
        alternatives=list(row.alternatives),
        confidence=dict(row.confidence),
        model_provider=row.model_provider,
        model_name=row.model_name,
        model_version=row.model_version,
        prompt_id=row.prompt_id,
        prompt_version=row.prompt_version,
        policy_version=row.policy_version,
        state=row.state,
        created_by=row.created_by,
        created_at=row.created_at,
    )


def _evidence_view(session: Session, row: Evidence) -> EvidenceView:
    candidate = session.get(Candidate, row.candidate_id) if row.candidate_id else None
    proposal = session.get(Proposal, row.proposal_id) if row.proposal_id else None
    return EvidenceView(
        pub_id=row.pub_id,
        namespace=row.namespace,
        domain=row.domain,
        candidate_pub_id=candidate.pub_id if candidate else None,
        proposal_pub_id=proposal.pub_id if proposal else None,
        source_uri=row.source_uri,
        content_hash=row.content_hash,
        publisher=row.publisher,
        claim=row.claim,
        stance=row.stance,
        summary=row.summary,
        trust_tier=row.trust_tier,
        visibility=row.visibility,
        data_classification=row.data_classification,
        acquired_at=row.acquired_at,
        created_by=row.created_by,
        created_at=row.created_at,
    )


def _change_set_view(row: Any) -> ChangeSetView:
    return ChangeSetView(
        pub_id=row.pub_id,
        namespace=row.namespace,
        domain=row.domain,
        base_release_id=row.base_release_id,
        changes=list(row.changes),
        dependency_ids=list(row.dependency_ids),
        conflicts=list(row.conflicts),
        visibility=row.visibility,
        state=row.state,
        created_by=row.created_by,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        created_at=row.created_at,
    )


def _release_view(row: Any) -> ReleaseView:
    return ReleaseView(
        pub_id=row.pub_id,
        namespace=row.namespace,
        domain=row.domain,
        release_id=row.release_id,
        parent_release_id=row.parent_release_id,
        schema_version=row.schema_version,
        content_hash=row.content_hash,
        artifact_uri=row.artifact_uri,
        quality_report=dict(row.quality_report),
        state=row.state,
        created_by=row.created_by,
        created_at=row.created_at,
    )


def _connector_view(row: Any) -> ConnectorRunView:
    return ConnectorRunView(
        pub_id=row.pub_id,
        namespace=row.namespace,
        domain=row.domain,
        adapter=row.adapter,
        operation=row.operation,
        status=row.status,
        base_release_id=row.base_release_id,
        upstream_release_id=row.upstream_release_id,
        local_release_id=row.local_release_id,
        cursor=dict(row.cursor),
        result=dict(row.result),
        error_code=row.error_code,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _translate_error(exc: Exception) -> HTTPException:
    code = str(exc)
    if isinstance(exc, KnowledgeNotFound | KeyError):
        return HTTPException(status_code=404, detail={"code": code.strip("'")})
    if isinstance(exc, KnowledgeConflict):
        return HTTPException(status_code=409, detail={"code": code})
    if isinstance(exc, KnowledgeReleaseError):
        return HTTPException(status_code=503, detail={"code": code})
    if isinstance(exc, ReasoningError):
        status = 503 if code.startswith(("model_", "invalid_model", "tool_", "provider_")) else 409
        return HTTPException(status_code=status, detail={"code": code})
    return HTTPException(status_code=422, detail={"code": code})


@router.post("/runtime/resolve", response_model=RuntimeResolveResponse)
def resolve_runtime(
    request: Request,
    body: RuntimeResolveRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> RuntimeResolveResponse:
    principal.require("knowledge:resolve")
    _repository(session, principal)
    try:
        value = service.resolve(
            session=session,
            settings=get_settings(),
            tenant_pub_id=principal.tenant_pub_id,
            request_id=str(request.state.request_id),
            body=body,
        )
    except (KeyError, ValueError, ReasoningError, KnowledgeReleaseError) as exc:
        session.rollback()
        raise _translate_error(exc) from exc
    return RuntimeResolveResponse.model_validate(value)


@router.post("/observations:ingest", response_model=IngestReceipt, status_code=202)
def ingest_observations(
    body: ObservationBatchRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> IngestReceipt:
    principal.require("knowledge:observe")
    repository = _repository(session, principal)
    drafts = tuple(ObservationDraft(**row.model_dump()) for row in body.observations)
    inserted = repository.record_observations(principal.tenant_pub_id, drafts)
    receipt_id = new_pub_id("kir")
    first = body.observations[0]
    repository.audit(
        namespace=first.namespace,
        domain=first.domain,
        actor=_actor(principal),
        action="observations.ingested",
        resource_type="ingest_receipt",
        resource_pub_id=receipt_id,
        receipt={"accepted": inserted, "submitted": len(drafts)},
    )
    session.commit()
    return IngestReceipt(
        accepted=inserted,
        duplicate=len(drafts) - inserted,
        receipt_id=receipt_id,
    )


@router.get("/candidates", response_model=CandidatePage)
def list_candidates(
    namespace: str | None = Query(default=None, max_length=120),
    domain: str | None = Query(default=None, max_length=160),
    state: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> CandidatePage:
    principal.require("knowledge:read")
    rows, total = _repository(session, principal).list_candidates(
        namespace=namespace,
        domain=domain,
        state=state,
        limit=limit,
        offset=offset,
    )
    return CandidatePage(data=[_candidate_view(row) for row in rows], total=total)


@router.post("/candidates/{candidate_pub_id}/reopen", response_model=CandidateView)
def reopen_candidate(
    candidate_pub_id: str,
    body: CandidateReopen,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> CandidateView:
    principal.require("knowledge:review")
    repository = _repository(session, principal)
    try:
        row = repository.reopen_candidate(
            candidate_pub_id,
            reason=body.reason,
            policy_version=body.policy_version,
            evidence_version=body.evidence_version,
            manual_override=body.manual_override,
            actor=_actor(principal),
        )
        session.commit()
    except (KnowledgeConflict, KnowledgeNotFound) as exc:
        session.rollback()
        raise _translate_error(exc) from exc
    return _candidate_view(row)


@router.post("/proposals", response_model=ProposalView, status_code=201)
def create_proposal(
    body: ProposalCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ProposalView:
    principal.require("knowledge:propose")
    repository = _repository(session, principal)
    try:
        row = repository.create_proposal(body.model_dump(), actor=_actor(principal))
        session.commit()
    except (KnowledgeConflict, KnowledgeNotFound) as exc:
        session.rollback()
        raise _translate_error(exc) from exc
    return _proposal_view(session, row)


@router.get("/proposals", response_model=list[ProposalView])
def list_proposals(
    namespace: str = Query(max_length=120),
    domain: str = Query(max_length=160),
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[ProposalView]:
    principal.require("knowledge:read")
    rows = _repository(session, principal).list_proposals(
        namespace=namespace, domain=domain, limit=limit
    )
    return [_proposal_view(session, row) for row in rows]


@router.post("/evidence", response_model=EvidenceView, status_code=201)
def create_evidence(
    body: EvidenceCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> EvidenceView:
    principal.require("knowledge:evidence")
    repository = _repository(session, principal)
    try:
        row = repository.create_evidence(body.model_dump(), actor=_actor(principal))
        session.commit()
    except (KnowledgeConflict, KnowledgeNotFound) as exc:
        session.rollback()
        raise _translate_error(exc) from exc
    return _evidence_view(session, row)


@router.get("/evidence", response_model=list[EvidenceView])
def list_evidence(
    namespace: str = Query(max_length=120),
    domain: str = Query(max_length=160),
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[EvidenceView]:
    principal.require("knowledge:read")
    rows = _repository(session, principal).list_evidence(
        namespace=namespace, domain=domain, limit=limit
    )
    return [_evidence_view(session, row) for row in rows]


@router.post(
    "/proposals/{proposal_pub_id}/adjudications",
    response_model=AdjudicationView,
    status_code=201,
)
def adjudicate_proposal(
    proposal_pub_id: str,
    body: AdjudicationCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> AdjudicationView:
    principal.require("knowledge:review")
    repository = _repository(session, principal)
    try:
        row = repository.adjudicate(proposal_pub_id, body.model_dump(), actor=_actor(principal))
        session.commit()
    except (KnowledgeConflict, KnowledgeNotFound) as exc:
        session.rollback()
        raise _translate_error(exc) from exc
    proposal = repository.proposal(proposal_pub_id)
    return AdjudicationView(
        pub_id=row.pub_id,
        proposal_pub_id=proposal.pub_id,
        decision=row.decision,
        reason=row.reason,
        policy_version=row.policy_version,
        before_value=dict(row.before_value),
        after_value=dict(row.after_value),
        decided_by=row.decided_by,
        decided_at=row.decided_at,
    )


@router.post("/change-sets", response_model=ChangeSetView, status_code=201)
def create_change_set(
    body: ChangeSetCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ChangeSetView:
    principal.require("knowledge:review")
    repository = _repository(session, principal)
    try:
        row = repository.create_change_set(body.model_dump(), actor=_actor(principal))
        session.commit()
    except (KnowledgeConflict, KnowledgeNotFound) as exc:
        session.rollback()
        raise _translate_error(exc) from exc
    return _change_set_view(row)


@router.post("/change-sets/{change_set_pub_id}/approve", response_model=ChangeSetView)
def approve_change_set(
    change_set_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ChangeSetView:
    principal.require("knowledge:review")
    repository = _repository(session, principal)
    try:
        row = repository.approve_change_set(change_set_pub_id, actor=_actor(principal))
        session.commit()
    except (KnowledgeConflict, KnowledgeNotFound) as exc:
        session.rollback()
        raise _translate_error(exc) from exc
    return _change_set_view(row)


@router.post("/releases", response_model=ReleaseView, status_code=201)
def publish_release(
    body: ReleaseCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ReleaseView:
    principal.require("knowledge:publish")
    _repository(session, principal)
    try:
        row = service.publish_release(
            session=session,
            settings=get_settings(),
            tenant_pub_id=principal.tenant_pub_id,
            actor=_actor(principal),
            body=body,
        )
    except (KnowledgeConflict, KnowledgeNotFound, KnowledgeReleaseError, ValueError) as exc:
        session.rollback()
        raise _translate_error(exc) from exc
    return _release_view(row)


@router.get("/releases", response_model=list[ReleaseView])
def list_releases(
    namespace: str = Query(max_length=120),
    domain: str = Query(max_length=160),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[ReleaseView]:
    principal.require("knowledge:read")
    rows = _repository(session, principal).list_releases(namespace=namespace, domain=domain)
    return [_release_view(row) for row in rows]


@router.post(
    "/releases/{release_id}/activate",
    status_code=204,
    response_class=Response,
    response_model=None,
)
def activate_release(
    release_id: str,
    body: ActivationRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> Response:
    principal.require("knowledge:publish")
    _repository(session, principal)
    try:
        service.activate_release(
            session=session,
            settings=get_settings(),
            tenant_pub_id=principal.tenant_pub_id,
            actor=_actor(principal),
            namespace=body.namespace,
            domain=body.domain,
            release_id=release_id,
            action="activate",
        )
    except (KnowledgeConflict, KnowledgeNotFound, KnowledgeReleaseError) as exc:
        session.rollback()
        raise _translate_error(exc) from exc
    return Response(status_code=204)


@router.post(
    "/releases/{release_id}/rollback",
    status_code=204,
    response_class=Response,
    response_model=None,
)
def rollback_release(
    release_id: str,
    body: ActivationRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> Response:
    principal.require("knowledge:publish")
    _repository(session, principal)
    try:
        service.activate_release(
            session=session,
            settings=get_settings(),
            tenant_pub_id=principal.tenant_pub_id,
            actor=_actor(principal),
            namespace=body.namespace,
            domain=body.domain,
            release_id=release_id,
            action="rollback",
        )
    except (KnowledgeConflict, KnowledgeNotFound, KnowledgeReleaseError) as exc:
        session.rollback()
        raise _translate_error(exc) from exc
    return Response(status_code=204)


@router.post("/connector-runs", response_model=ConnectorRunView, status_code=202)
def enqueue_connector_run(
    body: ConnectorRunCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ConnectorRunView:
    principal.require("knowledge:connector")
    repository = _repository(session, principal)
    row = repository.create_connector_run(body.model_dump())
    repository.audit(
        namespace=row.namespace,
        domain=row.domain,
        actor=_actor(principal),
        action="connector_run.queued",
        resource_type="connector_run",
        resource_pub_id=row.pub_id,
        receipt={"adapter": row.adapter, "operation": row.operation},
    )
    session.commit()
    return _connector_view(row)


@router.get("/connector-runs", response_model=list[ConnectorRunView])
def list_connector_runs(
    namespace: str = Query(max_length=120),
    domain: str = Query(max_length=160),
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[ConnectorRunView]:
    principal.require("knowledge:read")
    rows = _repository(session, principal).list_connector_runs(
        namespace=namespace, domain=domain, limit=limit
    )
    return [_connector_view(row) for row in rows]


@router.get("/audit-events", response_model=list[AuditEventView])
def list_audit_events(
    namespace: str = Query(max_length=120),
    domain: str = Query(max_length=160),
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[AuditEventView]:
    principal.require("knowledge:audit")
    rows = _repository(session, principal).list_audit_events(
        namespace=namespace, domain=domain, limit=limit
    )
    return [
        AuditEventView(
            pub_id=row.pub_id,
            namespace=row.namespace,
            domain=row.domain,
            actor=row.actor,
            action=row.action,
            resource_type=row.resource_type,
            resource_pub_id=row.resource_pub_id,
            receipt=dict(row.receipt),
            occurred_at=row.occurred_at,
        )
        for row in rows
    ]


@router.get("/events", response_model=list[KnowledgeEventView])
def list_events(
    namespace: str = Query(max_length=120),
    domain: str = Query(max_length=160),
    after: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[KnowledgeEventView]:
    """Return ordered, tenant-isolated event envelopes for pull-based consumers."""

    principal.require("knowledge:audit")
    rows = _repository(session, principal).list_audit_events(
        namespace=namespace,
        domain=domain,
        after=after,
        ascending=True,
        limit=limit,
    )
    return [
        KnowledgeEventView.model_validate(
            asdict(
                event_envelope(
                    event_id=row.pub_id,
                    event_type=row.action,
                    occurred_at=row.occurred_at,
                    tenant=row.tenant_pub_id,
                    namespace=row.namespace,
                    domain=row.domain,
                    resource_type=row.resource_type,
                    resource_id=row.resource_pub_id,
                    payload=dict(row.receipt),
                )
            )
        )
        for row in rows
    ]


@router.get("/metrics", response_model=MetricsView)
def metrics(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> MetricsView:
    principal.require("knowledge:read")
    return MetricsView.model_validate(_repository(session, principal).metrics())


def _status(*, readiness: bool, session: Session) -> ServiceStatus:
    settings = get_settings()
    domains = list(service.registry(settings).list())
    store = KnowledgeReleaseStore(settings.knowledge_release_dir)
    current: str | None = None
    previous: str | None = None
    verified = False
    checks: dict[str, str] = {
        "database": "unknown",
        "model_gateway": "configured" if service.gateway(settings) is not None else "disabled",
    }
    try:
        session.execute(text("SELECT 1"))
        checks["database"] = "reachable"
    except SQLAlchemyError:
        checks["database"] = "unreachable"
    try:
        current = store.current_release_id()
        previous = store.previous_release_id()
        if current is not None:
            store.verify(current)
            verified = True
        checks["release"] = "verified" if verified else "not_initialized"
    except (KnowledgeReleaseError, OSError, UnicodeError) as exc:
        checks["release"] = f"invalid:{type(exc).__name__}"
    ready = verified and checks["database"] == "reachable"
    status: Literal["ok", "degraded", "not_ready"] = (
        "ok" if ready else ("not_ready" if readiness else "degraded")
    )
    return ServiceStatus(
        status=status,
        domains=domains,
        active_release=current,
        previous_release=previous,
        release_verified=verified,
        checks=checks,
    )


@router.get("/health", response_model=ServiceStatus)
def health(session: Session = Depends(get_db)) -> ServiceStatus:
    return _status(readiness=False, session=session)


@router.get("/readiness", response_model=ServiceStatus)
def readiness(session: Session = Depends(get_db)) -> ServiceStatus:
    return _status(readiness=True, session=session)


@router.get("/domains", response_model=list[str])
def domains(
    principal: Principal = Depends(get_principal),
) -> list[str]:
    principal.require("knowledge:read")
    return list(service.registry(get_settings()).list())


__all__ = ["router"]
