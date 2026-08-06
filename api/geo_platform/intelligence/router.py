# ruff: noqa: B008
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from domain.intelligence.core import EvidenceRelation

from ..config import get_settings
from ..identity.policy import Principal, get_principal
from ..tenancy.psycopg import tenant_connection
from .evaluation_service import (
    DatasetCaseInput,
    EvaluationAdmissionService,
    PredictionInput,
    required_explanation_fields,
)
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
    verdict: Literal["likely", "unlikely", "uncertain", "insufficient"]
    rationale: str
    workflow_operation_id: str | None = None


class AppealCreate(StrictModel):
    reason: str


class AppealResolution(StrictModel):
    resolution: Literal["upheld", "rejected", "corrected"]
    corrected_verdict: Literal["likely", "unlikely", "uncertain", "insufficient"] | None = None
    rationale: str


class EvaluationDatasetCaseCreate(StrictModel):
    case_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    propagation_cluster_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    actual_positive: bool


class EvaluationDatasetCreate(StrictModel):
    version: str = Field(min_length=1, max_length=100)
    source_artifact_pub_id: str = Field(min_length=1, max_length=100)
    source_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    label_policy_version: str = Field(min_length=1, max_length=100)
    labeler_count: int = Field(ge=2, le=100)
    cases: list[EvaluationDatasetCaseCreate] = Field(min_length=20, max_length=10_000)


class EvaluationDatasetApprove(StrictModel):
    rationale: str = Field(min_length=5, max_length=2_000)


class EvaluationPredictionCreate(StrictModel):
    case_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    probability: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    predicted_positive: bool
    explanation_fields: list[str] = Field(max_length=50)


class EvaluationRunCreate(StrictModel):
    scorer_version: str = Field(min_length=1, max_length=100)
    decision_threshold: Decimal = Field(
        default=Decimal("0.5"),
        gt=Decimal("0"),
        lt=Decimal("1"),
    )
    calibration_bins: int = Field(default=10, ge=2, le=100)
    training_propagation_cluster_digests: list[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]] = (
        Field(default_factory=list, max_length=50_000)
    )
    predictions: list[EvaluationPredictionCreate] = Field(min_length=20, max_length=10_000)


class ModelAdmissionCreate(StrictModel):
    rationale: str = Field(min_length=5, max_length=2_000)


class EvaluationDatasetView(StrictModel):
    pub_id: str
    version: str
    dataset_sha256: str
    state: str
    case_count: int
    positive_count: int
    labeler_count: int
    submitted_at: datetime
    approved_at: datetime | None


class EvaluationDatasetPage(StrictModel):
    data: list[EvaluationDatasetView]
    page: dict[str, str | bool | None]


class EvaluationMetricsView(StrictModel):
    precision: Decimal | None
    recall: Decimal | None
    false_positive_rate: Decimal | None
    brier_score: Decimal
    expected_calibration_error: Decimal
    explanation_completeness_rate: Decimal
    sample_count: int
    positive_count: int
    negative_count: int
    dataset_version: str
    scorer_version: str
    evaluation_sha256: str


class EvaluationRunView(StrictModel):
    pub_id: str
    dataset_pub_id: str
    scorer_version: str
    decision_threshold: Decimal
    calibration_bins: int
    training_cluster_manifest_sha256: str
    training_cluster_count: int
    sample_count: int
    admission_policy_version: str
    admission_checks: dict[str, bool]
    admission_passed: bool
    model_admission_state: str | None = None
    metrics: EvaluationMetricsView
    required_explanation_fields: list[str]
    created_at: datetime


class ModelAdmissionView(StrictModel):
    pub_id: str
    evaluation_run_pub_id: str
    scorer_version: str
    state: str
    rationale: str
    admitted_at: datetime
    revoked_at: datetime | None


class EvaluationRunPage(StrictModel):
    data: list[EvaluationRunView]
    page: dict[str, str | bool | None]


class ModelAdmissionPage(StrictModel):
    data: list[ModelAdmissionView]
    page: dict[str, str | bool | None]


_DATASET_VIEW_FIELDS = (
    "pub_id",
    "version",
    "dataset_sha256",
    "state",
    "case_count",
    "positive_count",
    "labeler_count",
    "submitted_at",
    "approved_at",
)

_MODEL_ADMISSION_VIEW_FIELDS = (
    "pub_id",
    "evaluation_run_pub_id",
    "scorer_version",
    "state",
    "rationale",
    "admitted_at",
    "revoked_at",
)


def _evaluation_dataset_view(row: dict[str, Any]) -> EvaluationDatasetView:
    return EvaluationDatasetView(**{field: row[field] for field in _DATASET_VIEW_FIELDS})


def _model_admission_view(row: dict[str, Any]) -> ModelAdmissionView:
    return ModelAdmissionView(**{field: row[field] for field in _MODEL_ADMISSION_VIEW_FIELDS})


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


class PageHistoryView(StrictModel):
    content_pub_id: str
    version_pub_id: str
    canonical_url: str
    title: str | None
    version_number: int
    body_hash: str
    evidence_pub_id: str | None
    captured_at: datetime
    published_at: datetime | None
    snapshot_pub_id: str | None
    snapshot_number: int | None
    normalized_text_hash: str | None
    perceptual_hash: str | None


class VisualDiffView(StrictModel):
    pub_id: str
    content_pub_id: str
    before_version_pub_id: str
    after_version_pub_id: str
    before_evidence_pub_id: str
    after_evidence_pub_id: str
    text_diff: dict[str, Any] | None
    similarity: Decimal | None
    visual_diff_available: bool
    created_at: datetime


def _dsn() -> str:
    settings = get_settings()
    return (settings.runtime_postgres_dsn or settings.postgres_dsn).replace(
        "postgresql+psycopg://", "postgresql://"
    )


def _evaluation_run_view(row: dict[str, Any]) -> EvaluationRunView:
    return EvaluationRunView(
        pub_id=row["pub_id"],
        dataset_pub_id=row["dataset_pub_id"],
        scorer_version=row["scorer_version"],
        decision_threshold=row["decision_threshold"],
        calibration_bins=row["calibration_bins"],
        training_cluster_manifest_sha256=row["training_cluster_manifest_sha256"],
        training_cluster_count=row["training_cluster_count"],
        sample_count=row["sample_count"],
        admission_policy_version=row["admission_policy_version"],
        admission_checks=row["admission_checks"],
        admission_passed=row["admission_passed"],
        model_admission_state=row.get("model_admission_state"),
        metrics=EvaluationMetricsView(**row["metrics"]),
        required_explanation_fields=list(required_explanation_fields()),
        created_at=row["created_at"],
    )


_CONFLICT_CODES = {
    "dataset_idempotency_conflict",
    "dataset_version_or_hash_exists",
    "dataset_already_approved",
    "dataset_not_approvable",
    "evaluation_dataset_not_approved",
    "evaluation_idempotency_conflict",
    "model_admission_idempotency_conflict",
    "scorer_version_already_admitted",
}


def _raise_evaluation_error(error: Exception) -> None:
    code = str(error)
    if isinstance(error, LookupError):
        raise HTTPException(status_code=404, detail={"code": code}) from error
    if isinstance(error, PermissionError):
        raise HTTPException(status_code=403, detail={"code": code}) from error
    if code in _CONFLICT_CODES:
        raise HTTPException(status_code=409, detail={"code": code}) from error
    raise HTTPException(status_code=422, detail={"code": "evaluation_contract_invalid"}) from error


@router.get("/investigations", response_model=InvestigationPage)
def list_investigations(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> InvestigationPage:
    principal.require("intelligence:read")
    with tenant_connection(_dsn(), principal.tenant_pub_id, row_factory=dict_row) as connection:
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
    principal.require("intelligence:read")
    with tenant_connection(_dsn(), principal.tenant_pub_id, row_factory=dict_row) as connection:
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
                SELECT pub_id,state,submitted_by_pub_id,reason,resolution,
                       resolved_by_pub_id,resolution_rationale,resolved_at,created_at,updated_at
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


@router.get(
    "/investigations/{investigation_pub_id}/page-history",
    response_model=list[PageHistoryView],
)
def page_history(
    investigation_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> list[PageHistoryView]:
    principal.require("intelligence:read")
    with tenant_connection(_dsn(), principal.tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT ci.pub_id AS content_pub_id,cv.pub_id AS version_pub_id,ci.canonical_url,
                   cv.title,cv.version_number,cv.body_hash,cv.evidence_pub_id,cv.captured_at,
                   cv.published_at,es.pub_id AS snapshot_pub_id,es.snapshot_number,
                   es.normalized_text_hash,es.perceptual_hash
            FROM intelligence.content_item ci
            JOIN intelligence.content_version cv
              ON cv.content_pub_id=ci.pub_id AND cv.tenant_pub_id=ci.tenant_pub_id
            LEFT JOIN evidence.evidence_snapshot es
              ON es.tenant_pub_id=cv.tenant_pub_id
             AND es.subject_pub_id=ci.pub_id
             AND es.evidence_pub_id=cv.evidence_pub_id
            WHERE ci.tenant_pub_id=%s AND ci.investigation_pub_id=%s
            ORDER BY ci.pub_id,cv.version_number
            """,
            (principal.tenant_pub_id, investigation_pub_id),
        ).fetchall()
        exists = connection.execute(
            """
            SELECT 1 FROM intelligence.investigation
            WHERE tenant_pub_id=%s AND pub_id=%s
            """,
            (principal.tenant_pub_id, investigation_pub_id),
        ).fetchone()
    if exists is None:
        raise HTTPException(status_code=404, detail={"code": "investigation_not_found"})
    return [PageHistoryView(**dict(row)) for row in rows]


@router.get(
    "/investigations/{investigation_pub_id}/visual-diffs",
    response_model=list[VisualDiffView],
)
def visual_diffs(
    investigation_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> list[VisualDiffView]:
    principal.require("intelligence:read")
    with tenant_connection(_dsn(), principal.tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT ed.pub_id,ci.pub_id AS content_pub_id,
                   before_cv.pub_id AS before_version_pub_id,
                   after_cv.pub_id AS after_version_pub_id,
                   ed.before_evidence_pub_id,ed.after_evidence_pub_id,ed.text_diff,
                   ed.similarity,(ed.visual_diff_object_key IS NOT NULL) AS visual_diff_available,
                   ed.created_at
            FROM evidence.evidence_diff ed
            JOIN intelligence.content_version before_cv
              ON before_cv.tenant_pub_id=ed.tenant_pub_id
             AND before_cv.evidence_pub_id=ed.before_evidence_pub_id
            JOIN intelligence.content_version after_cv
              ON after_cv.tenant_pub_id=ed.tenant_pub_id
             AND after_cv.evidence_pub_id=ed.after_evidence_pub_id
             AND after_cv.content_pub_id=before_cv.content_pub_id
            JOIN intelligence.content_item ci
              ON ci.tenant_pub_id=ed.tenant_pub_id
             AND ci.pub_id=before_cv.content_pub_id
            WHERE ed.tenant_pub_id=%s AND ci.investigation_pub_id=%s
            ORDER BY ci.pub_id,before_cv.version_number,after_cv.version_number
            """,
            (principal.tenant_pub_id, investigation_pub_id),
        ).fetchall()
        exists = connection.execute(
            """
            SELECT 1 FROM intelligence.investigation
            WHERE tenant_pub_id=%s AND pub_id=%s
            """,
            (principal.tenant_pub_id, investigation_pub_id),
        ).fetchone()
    if exists is None:
        raise HTTPException(status_code=404, detail={"code": "investigation_not_found"})
    return [VisualDiffView(**dict(row)) for row in rows]


@router.post("/investigations", status_code=201)
def create_investigation(
    body: InvestigationCreate,
    principal: Principal = Depends(get_principal),
) -> dict[str, str]:
    principal.require("intelligence:write")
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
    principal.require("intelligence:write")
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
    principal.require("intelligence:write")
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
    principal.require("intelligence:write")
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
    principal.require("intelligence:review")
    try:
        verdict_pub_id = IntelligenceService(dsn=_dsn()).verdict(
            tenant_pub_id=principal.tenant_pub_id,
            investigation_pub_id=investigation_pub_id,
            verdict=body.verdict,
            reviewer_pub_id=principal.actor_pub_id,
            rationale=body.rationale,
            workflow_operation_id=body.workflow_operation_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "investigation_not_found"}) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail={"code": "verdict_idempotency_conflict"}
        ) from exc
    return {"verdict_pub_id": verdict_pub_id}


@router.post("/investigations/{investigation_pub_id}/appeals", status_code=201)
def create_appeal(
    investigation_pub_id: str,
    body: AppealCreate,
    principal: Principal = Depends(get_principal),
) -> dict[str, str]:
    principal.require("intelligence:write")
    try:
        appeal_pub_id = IntelligenceService(dsn=_dsn()).appeal(
            tenant_pub_id=principal.tenant_pub_id,
            investigation_pub_id=investigation_pub_id,
            submitted_by_pub_id=principal.actor_pub_id,
            reason=body.reason,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail={"code": "verdict_required"}) from exc
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
    principal.require("intelligence:review")
    try:
        replacement_pub_id = IntelligenceService(dsn=_dsn()).resolve_appeal(
            tenant_pub_id=principal.tenant_pub_id,
            investigation_pub_id=investigation_pub_id,
            appeal_pub_id=appeal_pub_id,
            reviewer_pub_id=principal.actor_pub_id,
            resolution=body.resolution,
            corrected_verdict=body.corrected_verdict,
            rationale=body.rationale,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": "appeal_independent_reviewer_required"},
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "appeal_not_found"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_appeal_resolution"}) from exc
    return {"replacement_verdict_pub_id": replacement_pub_id}


@router.get("/investigations/{investigation_pub_id}/conclusion")
def conclusion(
    investigation_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("intelligence:read")
    try:
        return IntelligenceService(dsn=_dsn()).public_conclusion(
            tenant_pub_id=principal.tenant_pub_id,
            investigation_pub_id=investigation_pub_id,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "investigation_conclusion_not_found"}
        ) from exc


@router.post(
    "/evaluation-datasets",
    response_model=EvaluationDatasetView,
    status_code=201,
)
def register_evaluation_dataset(
    body: EvaluationDatasetCreate,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=16,
        max_length=200,
    ),
    principal: Principal = Depends(get_principal),
) -> EvaluationDatasetView:
    principal.require("intelligence:write")
    try:
        result = EvaluationAdmissionService(dsn=_dsn()).register_dataset(
            tenant_pub_id=principal.tenant_pub_id,
            actor_pub_id=principal.actor_pub_id,
            idempotency_key=idempotency_key,
            version=body.version,
            source_artifact_pub_id=body.source_artifact_pub_id,
            source_artifact_sha256=body.source_artifact_sha256,
            label_policy_version=body.label_policy_version,
            labeler_count=body.labeler_count,
            cases=tuple(
                DatasetCaseInput(
                    case_digest=item.case_digest,
                    propagation_cluster_digest=item.propagation_cluster_digest,
                    actual_positive=item.actual_positive,
                )
                for item in body.cases
            ),
        )
    except (LookupError, PermissionError, ValueError) as error:
        _raise_evaluation_error(error)
    return _evaluation_dataset_view(result)


@router.get("/evaluation-datasets", response_model=EvaluationDatasetPage)
def list_evaluation_datasets(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> EvaluationDatasetPage:
    principal.require("intelligence:read")
    rows, has_more = EvaluationAdmissionService(dsn=_dsn()).list_datasets(
        tenant_pub_id=principal.tenant_pub_id,
        cursor=cursor,
        limit=limit,
    )
    return EvaluationDatasetPage(
        data=[_evaluation_dataset_view(row) for row in rows],
        page={
            "next_cursor": rows[-1]["pub_id"] if has_more else None,
            "has_more": has_more,
        },
    )


@router.post(
    "/evaluation-datasets/{dataset_pub_id}/approve",
    response_model=EvaluationDatasetView,
)
def approve_evaluation_dataset(
    dataset_pub_id: str,
    body: EvaluationDatasetApprove,
    principal: Principal = Depends(get_principal),
) -> EvaluationDatasetView:
    principal.require("intelligence:review")
    try:
        result = EvaluationAdmissionService(dsn=_dsn()).approve_dataset(
            tenant_pub_id=principal.tenant_pub_id,
            actor_pub_id=principal.actor_pub_id,
            dataset_pub_id=dataset_pub_id,
            rationale=body.rationale,
        )
    except (LookupError, PermissionError, ValueError) as error:
        _raise_evaluation_error(error)
    return _evaluation_dataset_view(result)


@router.post(
    "/evaluation-datasets/{dataset_pub_id}/runs",
    response_model=EvaluationRunView,
    status_code=201,
)
def run_evaluation_dataset(
    dataset_pub_id: str,
    body: EvaluationRunCreate,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=16,
        max_length=200,
    ),
    principal: Principal = Depends(get_principal),
) -> EvaluationRunView:
    principal.require("intelligence:write")
    try:
        result = EvaluationAdmissionService(dsn=_dsn()).evaluate_dataset(
            tenant_pub_id=principal.tenant_pub_id,
            actor_pub_id=principal.actor_pub_id,
            dataset_pub_id=dataset_pub_id,
            idempotency_key=idempotency_key,
            scorer_version=body.scorer_version,
            decision_threshold=body.decision_threshold,
            calibration_bins=body.calibration_bins,
            training_propagation_cluster_digests=tuple(body.training_propagation_cluster_digests),
            predictions=tuple(
                PredictionInput(
                    case_digest=item.case_digest,
                    probability=item.probability,
                    predicted_positive=item.predicted_positive,
                    explanation_fields=frozenset(item.explanation_fields),
                )
                for item in body.predictions
            ),
        )
    except (LookupError, PermissionError, ValueError) as error:
        _raise_evaluation_error(error)
    return _evaluation_run_view(result)


@router.get("/evaluation-runs", response_model=EvaluationRunPage)
def list_evaluation_runs(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> EvaluationRunPage:
    principal.require("intelligence:read")
    rows, has_more = EvaluationAdmissionService(dsn=_dsn()).list_evaluation_runs(
        tenant_pub_id=principal.tenant_pub_id,
        cursor=cursor,
        limit=limit,
    )
    return EvaluationRunPage(
        data=[_evaluation_run_view(row) for row in rows],
        page={
            "next_cursor": rows[-1]["pub_id"] if has_more else None,
            "has_more": has_more,
        },
    )


@router.post(
    "/evaluation-runs/{evaluation_run_pub_id}/admit",
    response_model=ModelAdmissionView,
    status_code=201,
)
def admit_evaluated_model(
    evaluation_run_pub_id: str,
    body: ModelAdmissionCreate,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=16,
        max_length=200,
    ),
    principal: Principal = Depends(get_principal),
) -> ModelAdmissionView:
    principal.require("intelligence:review")
    try:
        result = EvaluationAdmissionService(dsn=_dsn()).admit_model(
            tenant_pub_id=principal.tenant_pub_id,
            actor_pub_id=principal.actor_pub_id,
            evaluation_run_pub_id=evaluation_run_pub_id,
            idempotency_key=idempotency_key,
            rationale=body.rationale,
        )
    except (LookupError, PermissionError, ValueError) as error:
        _raise_evaluation_error(error)
    return _model_admission_view(result)


@router.get("/model-admissions", response_model=ModelAdmissionPage)
def list_model_admissions(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> ModelAdmissionPage:
    principal.require("intelligence:read")
    rows, has_more = EvaluationAdmissionService(dsn=_dsn()).list_model_admissions(
        tenant_pub_id=principal.tenant_pub_id,
        cursor=cursor,
        limit=limit,
    )
    return ModelAdmissionPage(
        data=[_model_admission_view(row) for row in rows],
        page={
            "next_cursor": rows[-1]["pub_id"] if has_more else None,
            "has_more": has_more,
        },
    )


@router.get("/search")
def search(
    q: str = Query(min_length=1, max_length=500),
    embedding: list[float] = Query(),
    limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    principal.require("intelligence:read")
    return IntelligenceService(dsn=_dsn()).hybrid_search(
        tenant_pub_id=principal.tenant_pub_id,
        query=q,
        query_embedding=embedding,
        limit=limit,
        include_private=False,
    )
