# ruff: noqa: B008
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict

from domain.intelligence.core import EvidenceRelation

from ..config import get_settings
from ..identity.policy import Principal, get_principal
from .service import IntelligenceService

router = APIRouter(prefix="/api/v2/intelligence", tags=["intelligence"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InvestigationCreate(StrictModel):
    title: str
    access_class: str = "customer_private"


class ContentIngest(StrictModel):
    canonical_url: str
    title: str
    body_text: str
    embedding: list[float]
    access_class: str
    captured_at: datetime
    published_at: datetime | None = None
    evidence_pub_id: str | None = None
    author_pub_id: str | None = None
    domain_pub_id: str | None = None


class ClaimEvidenceCreate(StrictModel):
    claim_pub_id: str
    evidence_pub_id: str
    relation: EvidenceRelation
    source_cluster: str
    independence_weight: Decimal
    rationale: str
    from_pub_id: str


class ScoreCreate(StrictModel):
    content_feature_score: Decimal
    propagation_feature_score: Decimal
    circular_citation_risk: Decimal
    workflow_operation_id: str | None = None


class VerdictCreate(StrictModel):
    verdict: str
    rationale: str
    workflow_operation_id: str | None = None


class AppealCreate(StrictModel):
    reason: str


class AppealResolution(StrictModel):
    resolution: str
    corrected_verdict: str | None = None
    rationale: str | None = None


class InvestigationSummary(StrictModel):
    pub_id: str
    title: str
    state: str
    access_class: str
    created_at: datetime
    updated_at: datetime
    claim_count: int
    source_cluster_count: int
    probability: Decimal | None
    latest_verdict: str | None


class InvestigationPage(StrictModel):
    data: list[InvestigationSummary]
    page: dict[str, str | bool | None]


def _dsn() -> str:
    return get_settings().postgres_dsn.replace("postgresql+psycopg://", "postgresql://")


@router.get("/investigations", response_model=InvestigationPage)
def list_investigations(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> InvestigationPage:
    principal.require("project:read")
    with psycopg.connect(_dsn(), row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT i.pub_id,i.title,i.state,i.access_class,i.created_at,i.updated_at,
                   (SELECT count(*) FROM intelligence.claim c
                    WHERE c.tenant_pub_id=i.tenant_pub_id
                      AND c.investigation_pub_id=i.pub_id) AS claim_count,
                   (SELECT count(DISTINCT cluster_id) FROM intelligence.source_independence s
                    WHERE s.tenant_pub_id=i.tenant_pub_id
                      AND s.investigation_pub_id=i.pub_id) AS source_cluster_count,
                   (SELECT probability FROM intelligence.detection_score d
                    WHERE d.tenant_pub_id=i.tenant_pub_id
                      AND d.investigation_pub_id=i.pub_id
                    ORDER BY d.created_at DESC LIMIT 1) AS probability,
                   (SELECT verdict FROM intelligence.human_verdict v
                    WHERE v.tenant_pub_id=i.tenant_pub_id
                      AND v.investigation_pub_id=i.pub_id
                    ORDER BY v.created_at DESC LIMIT 1) AS latest_verdict
            FROM intelligence.investigation i
            WHERE i.tenant_pub_id=%s AND (%s::text IS NULL OR i.pub_id>%s::text)
            ORDER BY i.pub_id LIMIT %s
            """,
            (principal.tenant_pub_id, cursor, cursor, limit + 1),
        ).fetchall()
    has_more = len(rows) > limit
    data = rows[:limit]
    return InvestigationPage(
        data=[InvestigationSummary(**dict(row)) for row in data],
        page={
            "next_cursor": data[-1]["pub_id"] if has_more else None,
            "has_more": has_more,
        },
    )


@router.get("/investigations/{investigation_pub_id}")
def investigation_detail(
    investigation_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("project:read")
    with psycopg.connect(_dsn(), row_factory=dict_row) as connection:
        investigation = connection.execute(
            """
            SELECT pub_id,title,state,access_class,created_at,updated_at
            FROM intelligence.investigation
            WHERE tenant_pub_id=%s AND pub_id=%s
            """,
            (principal.tenant_pub_id, investigation_pub_id),
        ).fetchone()
        if investigation is None:
            raise HTTPException(status_code=404, detail={"code": "investigation_not_found"})
        collections = {}
        for name, query in {
            "claims": """
                SELECT pub_id,normalized_text,predicate,object_text,verifiability,created_at
                FROM intelligence.claim
                WHERE tenant_pub_id=%s AND investigation_pub_id=%s ORDER BY pub_id
            """,
            "evidence_matrix": """
                SELECT ce.pub_id,ce.claim_pub_id,ce.evidence_pub_id,ce.relation,
                       ce.source_cluster,ce.independence_weight,ce.rationale
                FROM intelligence.claim_evidence ce
                JOIN intelligence.claim c ON c.pub_id=ce.claim_pub_id
                WHERE ce.tenant_pub_id=%s AND c.investigation_pub_id=%s ORDER BY ce.pub_id
            """,
            "source_independence": """
                SELECT pub_id,source_pub_id,cluster_id,independence_weight,
                       circular_citation_risk,reasons,rule_version
                FROM intelligence.source_independence
                WHERE tenant_pub_id=%s AND investigation_pub_id=%s ORDER BY pub_id
            """,
            "graph": """
                SELECT from_pub_id,to_pub_id,relation,weight,evidence_pub_id
                FROM intelligence.graph_edge
                WHERE tenant_pub_id=%s AND investigation_pub_id=%s ORDER BY id
            """,
            "scores": """
                SELECT pub_id,probability,evidence_sufficiency,independent_source_count,
                       uncertainty,rule_version,model_version,explanation,created_at
                FROM intelligence.detection_score
                WHERE tenant_pub_id=%s AND investigation_pub_id=%s ORDER BY created_at
            """,
            "verdicts": """
                SELECT pub_id,verdict,reviewer_pub_id,rationale,supersedes_pub_id,created_at
                FROM intelligence.human_verdict
                WHERE tenant_pub_id=%s AND investigation_pub_id=%s ORDER BY created_at
            """,
            "appeals": """
                SELECT pub_id,state,submitted_by_pub_id,reason,resolution,created_at,updated_at
                FROM intelligence.appeal
                WHERE tenant_pub_id=%s AND investigation_pub_id=%s ORDER BY created_at
            """,
        }.items():
            collections[name] = [
                dict(row)
                for row in connection.execute(
                    query, (principal.tenant_pub_id, investigation_pub_id)
                ).fetchall()
            ]
    return {**dict(investigation), **collections}


@router.post("/investigations", status_code=201)
def create_investigation(
    body: InvestigationCreate,
    principal: Principal = Depends(get_principal),
) -> dict[str, str]:
    principal.require("project:write")
    investigation_pub_id = IntelligenceService(dsn=_dsn()).create_investigation(
        tenant_pub_id=principal.tenant_pub_id,
        title=body.title,
        access_class=body.access_class,
    )
    return {"investigation_pub_id": investigation_pub_id, "state": "collecting"}


@router.post("/investigations/{investigation_pub_id}/contents", status_code=201)
def ingest_content(
    investigation_pub_id: str,
    body: ContentIngest,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("project:write")
    return IntelligenceService(dsn=_dsn()).ingest_content(
        tenant_pub_id=principal.tenant_pub_id,
        investigation_pub_id=investigation_pub_id,
        canonical_url=body.canonical_url,
        title=body.title,
        body_text=body.body_text,
        embedding=body.embedding,
        access_class=body.access_class,
        captured_at=body.captured_at,
        published_at=body.published_at,
        evidence_pub_id=body.evidence_pub_id,
        author_pub_id=body.author_pub_id,
        domain_pub_id=body.domain_pub_id,
    )


@router.post("/investigations/{investigation_pub_id}/claim-evidence", status_code=201)
def link_claim_evidence(
    investigation_pub_id: str,
    body: ClaimEvidenceCreate,
    principal: Principal = Depends(get_principal),
) -> dict[str, str]:
    principal.require("project:write")
    link_pub_id = IntelligenceService(dsn=_dsn()).add_claim_evidence(
        tenant_pub_id=principal.tenant_pub_id,
        investigation_pub_id=investigation_pub_id,
        claim_pub_id=body.claim_pub_id,
        evidence_pub_id=body.evidence_pub_id,
        relation=body.relation,
        source_cluster=body.source_cluster,
        independence_weight=body.independence_weight,
        rationale=body.rationale,
        from_pub_id=body.from_pub_id,
    )
    return {"claim_evidence_pub_id": link_pub_id}


@router.post("/investigations/{investigation_pub_id}/score", status_code=201)
def score_investigation(
    investigation_pub_id: str,
    body: ScoreCreate,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("project:write")
    result = IntelligenceService(dsn=_dsn()).score(
        tenant_pub_id=principal.tenant_pub_id,
        investigation_pub_id=investigation_pub_id,
        content_feature_score=body.content_feature_score,
        propagation_feature_score=body.propagation_feature_score,
        circular_citation_risk=body.circular_citation_risk,
        workflow_operation_id=body.workflow_operation_id,
    )
    score = result["result"]
    return {
        "score_pub_id": result["score_pub_id"],
        "probability": score.probability,
        "evidence_sufficiency": score.evidence_sufficiency,
        "independent_source_count": score.independent_source_count,
        "uncertainty": score.uncertainty,
        "rule_version": score.rule_version,
        "model_version": score.model_version,
        "explanation": score.explanation,
        "requires_human_verdict": score.requires_human_verdict,
    }


@router.post("/investigations/{investigation_pub_id}/verdicts", status_code=201)
def create_verdict(
    investigation_pub_id: str,
    body: VerdictCreate,
    principal: Principal = Depends(get_principal),
) -> dict[str, str]:
    principal.require("project:read")
    verdict_pub_id = IntelligenceService(dsn=_dsn()).verdict(
        tenant_pub_id=principal.tenant_pub_id,
        investigation_pub_id=investigation_pub_id,
        verdict=body.verdict,
        reviewer_pub_id=principal.subject,
        rationale=body.rationale,
        workflow_operation_id=body.workflow_operation_id,
    )
    return {"verdict_pub_id": verdict_pub_id}


@router.post("/investigations/{investigation_pub_id}/appeals", status_code=201)
def create_appeal(
    investigation_pub_id: str,
    body: AppealCreate,
    principal: Principal = Depends(get_principal),
) -> dict[str, str]:
    principal.require("project:read")
    appeal_pub_id = IntelligenceService(dsn=_dsn()).appeal(
        tenant_pub_id=principal.tenant_pub_id,
        investigation_pub_id=investigation_pub_id,
        submitted_by_pub_id=principal.subject,
        reason=body.reason,
    )
    return {"appeal_pub_id": appeal_pub_id}


@router.post(
    "/investigations/{investigation_pub_id}/appeals/{appeal_pub_id}/resolve",
    status_code=200,
)
def resolve_appeal(
    investigation_pub_id: str,
    appeal_pub_id: str,
    body: AppealResolution,
    principal: Principal = Depends(get_principal),
) -> dict[str, str | None]:
    principal.require("project:read")
    replacement_pub_id = IntelligenceService(dsn=_dsn()).resolve_appeal(
        tenant_pub_id=principal.tenant_pub_id,
        investigation_pub_id=investigation_pub_id,
        appeal_pub_id=appeal_pub_id,
        reviewer_pub_id=principal.subject,
        resolution=body.resolution,
        corrected_verdict=body.corrected_verdict,
        rationale=body.rationale,
    )
    return {"replacement_verdict_pub_id": replacement_pub_id}


@router.get("/investigations/{investigation_pub_id}/conclusion")
def conclusion(
    investigation_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("project:read")
    try:
        return IntelligenceService(dsn=_dsn()).public_conclusion(
            tenant_pub_id=principal.tenant_pub_id,
            investigation_pub_id=investigation_pub_id,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "investigation_conclusion_not_found"}
        ) from exc


@router.get("/search")
def search(
    q: str = Query(min_length=1, max_length=500),
    embedding: list[float] = Query(),
    limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    principal.require("project:read")
    return IntelligenceService(dsn=_dsn()).hybrid_search(
        tenant_pub_id=principal.tenant_pub_id,
        query=q,
        query_embedding=embedding,
        limit=limit,
        include_private=False,
    )
