# ruff: noqa: B008
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict

from domain.evidence.dlp import assert_secret_free

from ..config import get_settings
from ..identity.policy import Principal, get_principal
from ..tenancy.psycopg import tenant_connection
from .service import AnalyticsService

router = APIRouter(prefix="/api/v2/analytics", tags=["analytics"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetricView(StrictModel):
    metric: str
    value: float | None
    numerator: int | None
    denominator: int
    state: str
    metric_version: str
    scorer_version: str
    filter_hash: str
    trace_tokens: list[str]


class AnswerView(StrictModel):
    pub_id: str
    project_pub_id: str
    run_pub_id: str | None
    config_version_pub_id: str | None
    query_pub_id: str | None
    query_text: str | None
    response_text: str
    model: str
    region: str
    mode: str
    eligible: bool
    degraded: bool
    capture_time: datetime
    mentioned: bool | None
    rank: int | None
    sentiment: str | None
    recommendation_state: str | None
    citation_count: int


class AnswerPage(StrictModel):
    data: list[AnswerView]
    page: dict[str, str | bool | None]


class BreakdownView(StrictModel):
    group_by: Literal["day", "model", "region_mode", "question"]
    day: date | None = None
    model: str | None = None
    region: str | None = None
    mode: str | None = None
    question_pub_id: str | None = None
    question_text: str | None = None
    answer_count: int
    mentioned_count: int
    mention_rate: float | None
    average_rank: float | None
    citation_coverage: float | None


class CitationRelationView(StrictModel):
    pub_id: str
    ordinal: int
    canonical_url: str
    host: str
    title: str | None
    cited_text: str | None
    own_source: bool
    content_hash: str | None


class DisparagementRateView(StrictModel):
    dimension: Literal["target_brand", "subject_brand", "platform"]
    value: str
    judgments: int
    disparagement_count: int
    disparagement_rate: float | None
    negative_count: int
    support_count: int
    experimental_count: int
    metric_version: str


class DisparagementCaseView(StrictModel):
    judgment_pub_id: str
    subject_type: str
    subject_pub_id: str
    platform: str
    subject_brand: str
    target_brand: str
    attitude: str
    evidence_quote: str | None
    confidence: float | None
    method: str
    model: str
    prompt_version: str
    source_url: str | None
    created_at: datetime


class SourceAuditVerdictBucketView(StrictModel):
    accurate: int
    inaccurate: int
    unsupported: int
    unverifiable: int


class SourceAuditVerdictsView(StrictModel):
    transcript: SourceAuditVerdictBucketView
    factual: SourceAuditVerdictBucketView


class SourceAuditHostView(StrictModel):
    host: str
    is_own_site: bool
    documents: int
    transcript_total: int
    transcript_accurate: int


class SourceAuditItemAuditView(StrictModel):
    dimension: str
    verdict: str | None
    audit_status: str
    rationale: str | None


class SourceAuditItemView(StrictModel):
    pub_id: str
    url: str
    host: str
    final_url: str | None
    http_status: int | None
    extract_status: str
    fetched_at: datetime
    is_own_site: bool
    audits: list[SourceAuditItemAuditView]


class SourceAuditOverviewView(StrictModel):
    project_pub_id: str
    start: date
    end: date
    own_site_host: str | None
    documents_total: int
    own_site_documents: int
    own_site_share: float | None
    own_site_transcript_total: int
    own_site_transcript_accurate: int
    own_site_adoption_rate: float | None
    verdicts: SourceAuditVerdictsView
    hosts: list[SourceAuditHostView]
    items: list[SourceAuditItemView]




class EvidenceAnchorView(StrictModel):
    pub_id: str
    text_start: int | None
    text_end: int | None
    bbox: dict[str, Any] | None
    page_number: int | None
    quote_hash: str | None


class AnswerEvidenceView(StrictModel):
    pub_id: str
    relation_type: str
    kind: str
    access_class: str
    sha256: str
    mime_type: str
    byte_size: int
    source_url: str | None
    capture_time: datetime
    anchors: list[EvidenceAnchorView]


class EvidenceHistoryView(StrictModel):
    pub_id: str
    before_evidence_pub_id: str
    after_evidence_pub_id: str
    similarity: float | None
    visual_diff_available: bool
    created_at: datetime


class AnswerRelationsView(StrictModel):
    answer_pub_id: str
    citations: list[CitationRelationView]
    evidence: list[AnswerEvidenceView]
    history: list[EvidenceHistoryView]


def _dsn() -> str:
    settings = get_settings()
    return (settings.runtime_postgres_dsn or settings.postgres_dsn).replace(
        "postgresql+psycopg://", "postgresql://"
    )


def _safe_dimension(value: object, fallback: str, max_length: int = 200) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        return fallback
    try:
        assert_secret_free(value)
    except ValueError:
        return fallback
    return value


def _safe_public_id(value: object, fallback: str) -> str:
    return (
        value
        if isinstance(value, str)
        and value.startswith("qry_")
        and len(value) <= 120
        and value.replace("_", "").isalnum()
        and value.isascii()
        else fallback
    )


def _safe_optional_text(value: object, max_length: int = 1000) -> str | None:
    if not isinstance(value, str) or not value or len(value) > max_length:
        return None
    try:
        assert_secret_free(value)
    except ValueError:
        return None
    return value


def _safe_source_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return urlunsplit((parsed.scheme, f"{parsed.hostname}{port}", parsed.path, "", ""))


def _safe_bbox(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    projected = {
        key: value[key]
        for key in ("x", "y", "width", "height", "confidence")
        if isinstance(value.get(key), int | float)
    }
    return projected or None


@router.get("/answers", response_model=AnswerPage)
def answers(
    project_pub_id: str,
    answer_pub_id: str | None = Query(default=None, pattern=r"^ans_[A-Za-z0-9_-]{1,116}$"),
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    model: str | None = None,
    region: str | None = None,
    mode: str | None = None,
    principal: Principal = Depends(get_principal),
) -> AnswerPage:
    principal.require("project:read")
    with tenant_connection(_dsn(), principal.tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT a.pub_id,a.project_pub_id,a.run_pub_id,a.config_version_pub_id,
                   a.query_pub_id,a.query_text,a.response_text,
                   a.model,a.region,a.mode,a.eligible,a.degraded,a.capture_time,
                   aa.mentioned,aa.rank,aa.sentiment,aa.recommendation_state,
                   (SELECT count(*) FROM analytics.citation_fact c
                    WHERE c.tenant_pub_id=a.tenant_pub_id AND c.answer_pub_id=a.pub_id)
                    AS citation_count
            FROM analytics.answer a
            LEFT JOIN LATERAL (
              SELECT mentioned,rank,sentiment,recommendation_state
              FROM analytics.answer_analysis
              WHERE tenant_pub_id=a.tenant_pub_id AND answer_pub_id=a.pub_id
              ORDER BY created_at DESC LIMIT 1
            ) aa ON true
            WHERE a.tenant_pub_id=%s AND a.project_pub_id=%s
              AND (%s::text IS NULL OR a.pub_id=%s::text)
              AND (%s::text IS NULL OR a.pub_id>%s::text)
              AND (%s::text IS NULL OR a.model=%s::text)
              AND (%s::text IS NULL OR a.region=%s::text)
              AND (%s::text IS NULL OR a.mode=%s::text)
            ORDER BY a.pub_id LIMIT %s
            """,
            (
                principal.tenant_pub_id,
                project_pub_id,
                answer_pub_id,
                answer_pub_id,
                cursor,
                cursor,
                model,
                model,
                region,
                region,
                mode,
                mode,
                limit + 1,
            ),
        ).fetchall()
    has_more = len(rows) > limit
    data = rows[:limit]
    return AnswerPage(
        data=[AnswerView(**dict(row)) for row in data],
        page={
            "next_cursor": data[-1]["pub_id"] if has_more else None,
            "has_more": has_more,
        },
    )


@router.get("/answers/{answer_pub_id}/relations", response_model=AnswerRelationsView)
def answer_relations(
    answer_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> AnswerRelationsView:
    principal.require("project:read")
    with tenant_connection(_dsn(), principal.tenant_pub_id, row_factory=dict_row) as connection:
        answer = connection.execute(
            """
            SELECT pub_id FROM analytics.answer
            WHERE tenant_pub_id=%s AND pub_id=%s
            """,
            (principal.tenant_pub_id, answer_pub_id),
        ).fetchone()
        if answer is None:
            raise HTTPException(status_code=404, detail={"code": "answer_not_found"})
        citations = connection.execute(
            """
            SELECT pub_id,ordinal,canonical_url,host,title,cited_text,own_source,content_hash
            FROM analytics.citation_fact
            WHERE tenant_pub_id=%s AND answer_pub_id=%s
            ORDER BY ordinal,created_at,pub_id
            """,
            (principal.tenant_pub_id, answer_pub_id),
        ).fetchall()
        evidence_rows = connection.execute(
            """
            SELECT ea.pub_id,er.relation_type,ea.kind,ea.access_class,ea.sha256,
                   ea.mime_type,ea.byte_size,ea.source_url,ea.capture_time
            FROM evidence.evidence_relation er
            JOIN evidence.evidence_asset ea
              ON ea.tenant_pub_id=er.tenant_pub_id AND ea.pub_id=er.to_pub_id
            WHERE er.tenant_pub_id=%s AND er.from_pub_id=%s AND ea.deleted_at IS NULL
            ORDER BY ea.capture_time,ea.pub_id
            """,
            (principal.tenant_pub_id, answer_pub_id),
        ).fetchall()
        evidence_ids = [row["pub_id"] for row in evidence_rows]
        anchors = (
            connection.execute(
                """
                SELECT pub_id,evidence_pub_id,text_start,text_end,bbox,page_number,quote_hash
                FROM evidence.evidence_anchor
                WHERE tenant_pub_id=%s AND evidence_pub_id=ANY(%s::text[])
                ORDER BY evidence_pub_id,page_number,text_start,pub_id
                """,
                (principal.tenant_pub_id, evidence_ids),
            ).fetchall()
            if evidence_ids
            else []
        )
        history = (
            connection.execute(
                """
                SELECT pub_id,before_evidence_pub_id,after_evidence_pub_id,similarity,
                       visual_diff_object_key IS NOT NULL AS visual_diff_available,created_at
                FROM evidence.evidence_diff
                WHERE tenant_pub_id=%s
                  AND (before_evidence_pub_id=ANY(%s::text[])
                       OR after_evidence_pub_id=ANY(%s::text[]))
                ORDER BY created_at,pub_id
                """,
                (principal.tenant_pub_id, evidence_ids, evidence_ids),
            ).fetchall()
            if evidence_ids
            else []
        )
    anchors_by_evidence: dict[str, list[EvidenceAnchorView]] = {}
    for row in anchors:
        evidence_id = row["evidence_pub_id"]
        anchors_by_evidence.setdefault(evidence_id, []).append(
            EvidenceAnchorView(
                **{
                    key: value
                    for key, value in dict(row).items()
                    if key not in {"evidence_pub_id", "bbox"}
                },
                bbox=_safe_bbox(row["bbox"]),
            )
        )
    return AnswerRelationsView(
        answer_pub_id=answer_pub_id,
        citations=[
            CitationRelationView(
                **{
                    key: value
                    for key, value in dict(row).items()
                    if key not in {"canonical_url", "title", "cited_text"}
                },
                canonical_url=_safe_source_url(row["canonical_url"]) or "",
                title=_safe_optional_text(row["title"], 300),
                cited_text=_safe_optional_text(row["cited_text"], 2000),
            )
            for row in citations
        ],
        evidence=[
            AnswerEvidenceView(
                **{key: value for key, value in dict(row).items() if key != "source_url"},
                source_url=_safe_source_url(row["source_url"]),
                anchors=anchors_by_evidence.get(row["pub_id"], []),
            )
            for row in evidence_rows
        ],
        history=[
            EvidenceHistoryView(
                **dict(row),
                similarity=float(row["similarity"]) if row["similarity"] is not None else None,
            )
            for row in history
        ],
    )


@router.get("/overview", response_model=list[MetricView])
def overview(
    project_pub_id: str,
    start: date,
    end: date,
    model: str | None = None,
    region: str | None = None,
    mode: str | None = None,
    principal: Principal = Depends(get_principal),
) -> list[MetricView]:
    principal.require("project:read")
    dimensions = {
        key: value
        for key, value in {"model": model, "region": region, "mode": mode}.items()
        if value is not None
    }
    rows = AnalyticsService(dsn=_dsn()).aggregate(
        tenant_pub_id=principal.tenant_pub_id,
        project_pub_id=project_pub_id,
        start=start,
        end=end,
        dimensions=dimensions,
    )
    return [
        MetricView(
            metric=row["metric_name"],
            value=float(row["value"]) if row["value"] is not None else None,
            numerator=row["numerator"],
            denominator=row["denominator"],
            state=row["state"],
            metric_version=row["metric_version"],
            scorer_version=row["scorer_version"],
            filter_hash=row["filter_hash"],
            trace_tokens=row["trace_tokens"],
        )
        for row in rows
    ]


@router.get("/breakdown", response_model=list[BreakdownView])
def breakdown(
    project_pub_id: str,
    start: date,
    end: date,
    group_by: Literal["day", "model", "region_mode", "question"],
    model: str | None = None,
    region: str | None = None,
    mode: str | None = None,
    principal: Principal = Depends(get_principal),
) -> list[BreakdownView]:
    principal.require("project:read")
    if start > end or (end - start).days > 366:
        raise HTTPException(status_code=422, detail={"code": "invalid_analytics_window"})
    group_expressions = {
        "day": ("a.capture_time::date",),
        "model": ("a.model",),
        "region_mode": ("a.region", "a.mode"),
        "question": ("a.query_pub_id", "a.query_text"),
    }[group_by]
    select_dimensions = ", ".join(group_expressions)
    with tenant_connection(_dsn(), principal.tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            f"""
            WITH facts AS (
              SELECT {select_dimensions},
                     aa.mentioned,aa.rank,
                     EXISTS (
                       SELECT 1 FROM analytics.citation_fact c
                       WHERE c.tenant_pub_id=a.tenant_pub_id
                         AND c.answer_pub_id=a.pub_id
                     ) AS has_citation
              FROM analytics.answer a
              LEFT JOIN LATERAL (
                SELECT mentioned,rank
                FROM analytics.answer_analysis
                WHERE tenant_pub_id=a.tenant_pub_id AND answer_pub_id=a.pub_id
                ORDER BY created_at DESC,id DESC LIMIT 1
              ) aa ON true
              WHERE a.tenant_pub_id=%s AND a.project_pub_id=%s
                AND a.capture_time::date BETWEEN %s AND %s
                AND a.eligible
                AND (%s::text IS NULL OR a.model=%s::text)
                AND (%s::text IS NULL OR a.region=%s::text)
                AND (%s::text IS NULL OR a.mode=%s::text)
            )
            SELECT {select_dimensions},
                   count(*)::bigint AS answer_count,
                   count(*) FILTER (WHERE mentioned)::bigint AS mentioned_count,
                   count(*) FILTER (WHERE mentioned)::numeric / NULLIF(count(*),0)
                     AS mention_rate,
                   avg(rank) FILTER (WHERE rank IS NOT NULL) AS average_rank,
                   count(*) FILTER (WHERE has_citation)::numeric / NULLIF(count(*),0)
                     AS citation_coverage
            FROM facts a
            GROUP BY {select_dimensions}
            ORDER BY {select_dimensions}
            """,
            (
                principal.tenant_pub_id,
                project_pub_id,
                start,
                end,
                model,
                model,
                region,
                region,
                mode,
                mode,
            ),
        ).fetchall()
    projections: list[BreakdownView] = []
    for row in rows:
        projections.append(
            BreakdownView(
                group_by=group_by,
                day=row.get("capture_time") if group_by == "day" else None,
                model=(
                    _safe_dimension(row.get("model"), "未知模型") if group_by == "model" else None
                ),
                region=(
                    _safe_dimension(row.get("region"), "未标注地域")
                    if group_by == "region_mode"
                    else None
                ),
                mode=(
                    _safe_dimension(row.get("mode"), "未标注模式")
                    if group_by == "region_mode"
                    else None
                ),
                question_pub_id=(
                    _safe_public_id(row.get("query_pub_id"), "未关联问题")
                    if group_by == "question"
                    else None
                ),
                question_text=(
                    _safe_dimension(row.get("query_text"), "问题内容已隐藏", 500)
                    if group_by == "question"
                    else None
                ),
                answer_count=row["answer_count"],
                mentioned_count=row["mentioned_count"],
                mention_rate=(
                    float(row["mention_rate"]) if row["mention_rate"] is not None else None
                ),
                average_rank=(
                    float(row["average_rank"]) if row["average_rank"] is not None else None
                ),
                citation_coverage=(
                    float(row["citation_coverage"])
                    if row["citation_coverage"] is not None
                    else None
                ),
            )
        )
    return projections


@router.get("/delta")
def delta(
    project_pub_id: str,
    start: date,
    end: date,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("project:read")
    result = AnalyticsService(dsn=_dsn()).previous_period_delta(
        tenant_pub_id=principal.tenant_pub_id,
        project_pub_id=project_pub_id,
        start=start,
        end=end,
    )
    return {
        metric: {key: float(value) if value is not None else None for key, value in values.items()}
        for metric, values in result.items()
    }


@router.get("/competitors")
def competitors(
    project_pub_id: str,
    start: date,
    end: date,
    model: str | None = None,
    region: str | None = None,
    mode: str | None = None,
    question_pub_id: str | None = None,
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    principal.require("project:read")
    dimensions = {
        key: value
        for key, value in {
            "model": model,
            "region": region,
            "mode": mode,
            "question_pub_id": question_pub_id,
        }.items()
        if value is not None
    }
    return AnalyticsService(dsn=_dsn()).aggregate_competitors(
        tenant_pub_id=principal.tenant_pub_id,
        project_pub_id=project_pub_id,
        start=start,
        end=end,
        dimensions=dimensions,
    )


@router.get("/disparagement/rate", response_model=list[DisparagementRateView])
def disparagement_rate(
    project_pub_id: str,
    start: date,
    end: date,
    dimension: Literal["target_brand", "subject_brand", "platform"] = "target_brand",
    principal: Principal = Depends(get_principal),
) -> list[DisparagementRateView]:
    """W3 拉踩率聚合：按 品牌/拉踩方/平台(source：answer 的 model，正文 的 host)。"""
    principal.require("project:read")
    if start > end or (end - start).days > 366:
        raise HTTPException(status_code=422, detail={"code": "invalid_analytics_window"})
    rows = AnalyticsService(dsn=_dsn()).aggregate_disparagement(
        tenant_pub_id=principal.tenant_pub_id,
        project_pub_id=project_pub_id,
        start=start,
        end=end,
        dimension=dimension,
    )
    return [
        DisparagementRateView(
            dimension=dimension,
            value=row["value"],
            judgments=row["judgments"],
            disparagement_count=row["disparagement_count"],
            disparagement_rate=(
                float(row["disparagement_rate"])
                if row["disparagement_rate"] is not None
                else None
            ),
            negative_count=row["negative_count"],
            support_count=row["support_count"],
            experimental_count=row["experimental_count"],
            metric_version=row["metric_version"],
        )
        for row in rows
    ]


@router.get("/disparagement/cases", response_model=list[DisparagementCaseView])
def disparagement_cases(
    project_pub_id: str,
    start: date,
    end: date,
    limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> list[DisparagementCaseView]:
    """W3 典型案例清单：disparagement=true 按 confidence 降序（证据+出处链接）。"""
    principal.require("project:read")
    if start > end or (end - start).days > 366:
        raise HTTPException(status_code=422, detail={"code": "invalid_analytics_window"})
    rows = AnalyticsService(dsn=_dsn()).disparagement_cases(
        tenant_pub_id=principal.tenant_pub_id,
        project_pub_id=project_pub_id,
        start=start,
        end=end,
        limit=limit,
    )
    return [
        DisparagementCaseView(
            judgment_pub_id=row["judgment_pub_id"],
            subject_type=row["subject_type"],
            subject_pub_id=row["subject_pub_id"],
            platform=row["platform"],
            subject_brand=row["subject_brand"],
            target_brand=row["target_brand"],
            attitude=row["attitude"],
            evidence_quote=_safe_optional_text(row["evidence_quote"], 2000),
            confidence=(float(row["confidence"]) if row["confidence"] is not None else None),
            method=row["method"],
            model=row["model"],
            prompt_version=row["prompt_version"],
            source_url=_safe_source_url(row["source_url"]),
            created_at=row["created_at"],
        )
        for row in rows
    ]


@router.get("/source-audit", response_model=SourceAuditOverviewView)
def source_audit_overview(
    project_pub_id: str,
    start: date,
    end: date,
    principal: Principal = Depends(get_principal),
) -> SourceAuditOverviewView:
    """W2 信源审计聚合（官网引用能效）：窗口内信源文档、官网命中占比与判定分布。"""
    principal.require("project:read")
    if start > end or (end - start).days > 366:
        raise HTTPException(status_code=422, detail={"code": "invalid_analytics_window"})
    overview = AnalyticsService(dsn=_dsn()).source_audit_overview(
        tenant_pub_id=principal.tenant_pub_id,
        project_pub_id=project_pub_id,
        start=start,
        end=end,
    )
    return SourceAuditOverviewView(
        project_pub_id=project_pub_id,
        start=start,
        end=end,
        own_site_host=overview["own_site_host"],
        documents_total=overview["documents_total"],
        own_site_documents=overview["own_site_documents"],
        own_site_share=overview["own_site_share"],
        own_site_transcript_total=overview["own_site_transcript_total"],
        own_site_transcript_accurate=overview["own_site_transcript_accurate"],
        own_site_adoption_rate=overview["own_site_adoption_rate"],
        verdicts=SourceAuditVerdictsView(
            transcript=SourceAuditVerdictBucketView(**overview["verdicts"]["transcript"]),
            factual=SourceAuditVerdictBucketView(**overview["verdicts"]["factual"]),
        ),
        hosts=[SourceAuditHostView(**row) for row in overview["hosts"]],
        items=[
            SourceAuditItemView(
                **{
                    key: value
                    for key, value in item.items()
                    if key not in {"url", "final_url", "audits"}
                },
                url=_safe_source_url(item["url"]) or "",
                final_url=_safe_source_url(item["final_url"]),
                audits=[
                    SourceAuditItemAuditView(
                        dimension=audit["dimension"],
                        verdict=audit["verdict"],
                        audit_status=audit["audit_status"],
                        rationale=_safe_optional_text(
                            (audit["rationale"] or "")[:500] or None, 500
                        ),
                    )
                    for audit in item["audits"]
                ],
            )
            for item in overview["items"]
        ],
    )


@router.get("/trace/{trace_token}")
def trace(
    trace_token: str,
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("project:read")
    with tenant_connection(_dsn(), principal.tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT metric_name,answer_pub_id,contribution,metric_version,scorer_version
            FROM analytics.metric_trace
            WHERE tenant_pub_id=%s AND trace_token=%s
            ORDER BY id LIMIT %s
            """,
            (principal.tenant_pub_id, trace_token, limit),
        ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail={"code": "trace_not_found"})
    return {"trace_token": trace_token, "contributions": [dict(row) for row in rows]}
