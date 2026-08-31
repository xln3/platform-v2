# ruff: noqa: B008
from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, field_validator

from domain.brandrank.rules import available_domains
from domain.evidence.dlp import assert_secret_free

from ..brandrank import compare as brandrank_compare
from ..brandrank import service as brandrank_service
from ..config import get_settings
from ..identity.policy import Principal, get_principal
from ..metrics_v2.consumer_projection import OfficialMetricsConsumer, OfficialScope
from ..metrics_v2.repository import MetricsV2Repository
from ..pagination import decode_keyset_cursor, encode_keyset_cursor, numbered_page
from ..tenancy.psycopg import tenant_connection
from . import comparisons
from .pagination_policy import (
    SAMPLING_PROGRESS_DEFAULT_PAGE_NUMBER,
    SAMPLING_PROGRESS_DEFAULT_PAGE_SIZE,
    SAMPLING_PROGRESS_MAX_PAGE_NUMBER,
    SAMPLING_PROGRESS_MAX_PAGE_SIZE,
    SAMPLING_PROGRESS_MIN_PAGE_NUMBER,
    SAMPLING_PROGRESS_MIN_PAGE_SIZE,
)
from .sampling_progress import (
    parse_sampling_configs,
    sampling_columns,
    sampling_plan_items,
    select_sampling_campaign,
    uses_quotation_appendices,
    variant_label,
)
from .service import AnalyticsService

router = APIRouter(prefix="/api/v2/analytics", tags=["analytics"])

# POST 可选幂等头（sop 同款轻量口径：校验 16–128 可打印 ASCII + 响应头回显，不去重）
IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=16,
        max_length=128,
        pattern=r"^[\x20-\x7e]+$",
    ),
]

_RUN_PUB_ID_RE = re.compile(r"run_[A-Za-z0-9_-]{1,116}")
AnalysisState = Literal[
    "not_requested",
    "queued",
    "running",
    "completed",
    "partial",
    "failed",
    "skipped",
]


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


class CompetitorView(StrictModel):
    competitor: str
    mention_count: int
    answer_count: int
    average_rank: float
    top1_count: int
    top3_count: int
    top10_count: int
    mention_rate: float
    top1_rate: float
    top3_rate: float
    top10_rate: float
    metric_version: str


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
    # Eligibility is an analysis result. It is honestly null while a freshly
    # captured answer is waiting for that independent job.
    eligible: bool | None
    degraded: bool | None
    capture_time: datetime
    capture_state: Literal["completed", "legacy"]
    answer_analysis_state: AnalysisState
    source_analysis_state: AnalysisState
    risk_analysis_state: AnalysisState
    mentioned: bool | None
    rank: int | None
    sentiment: str | None
    recommendation_state: str | None
    citation_count: int


class AnswerPage(StrictModel):
    data: list[AnswerView]
    page: dict[str, str | bool | None]


class SamplingProgressColumnView(StrictModel):
    key: str
    model: str
    region: str
    # Planned mode retained for backward-compatible clients. ``modes`` contains
    # every effective mode accepted into this formal sampling leg.
    mode: str
    modes: list[str]


class SamplingProgressModeBreakdownView(StrictModel):
    mode: str
    completed_samples: int
    latest_capture_time: datetime
    answer_pub_ids: list[str]


class SamplingProgressCellView(StrictModel):
    column_key: str
    completed_samples: int
    latest_capture_time: datetime
    answer_pub_ids: list[str]
    mode_breakdown: list[SamplingProgressModeBreakdownView]


class SamplingProgressRowView(StrictModel):
    appendix: str | None
    group: str
    group_name: str
    expression: str
    query_text: str
    cells: list[SamplingProgressCellView]


class NumberedPageView(StrictModel):
    page: int
    page_size: int
    total_count: int
    total_pages: int


class SamplingProgressView(StrictModel):
    project_pub_id: str
    config_revision_start: int | None
    config_revision_end: int | None
    columns: list[SamplingProgressColumnView]
    rows: list[SamplingProgressRowView]
    page: NumberedPageView
    observed_cells: int
    total_cells: int
    answer_count: int
    latest_capture_time: datetime | None
    live_runs: int


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
    platform_ordinal: int = 1
    ordinal_base: Literal[0, 1] = 1
    canonical_url: str
    host: str
    title: str | None
    cited_text: str | None
    own_source: bool
    content_hash: str | None
    source_document_pub_id: str | None = None
    published_at_raw: str | None = None
    published_at: datetime | None = None
    published_at_timezone: str | None = None
    published_at_precision: str | None = None
    published_at_source: str | None = None
    published_at_confidence: Literal[
        "verified_structured", "structured_only", "visible_only", "inferred_low", "unknown"
    ] = "unknown"
    support: CitationSupportView


class CitationSupportView(StrictModel):
    mapping_status: Literal["mapped", "unmapped", "ambiguous"]
    mapping_basis: str | None = None
    answer_text_start: int | None = None
    answer_text_end: int | None = None
    answer_ast_path: list[str | int] | None = None
    answer_sentence: str | None = None
    source_quote: str | None = None
    source_text_start: int | None = None
    source_text_end: int | None = None
    source_quote_hash: str | None = None
    source_match_status: Literal["exact", "normalized", "not_found", "not_checked"]
    source_match_version: str | None = None
    relation: Literal["supports", "contradicts", "background", "unverified"]
    relevance_confidence: float | None = None
    classifier_version: str | None = None
    review_status: Literal["unreviewed", "approved", "rejected", "needs_review"]


class AnswerShareArtifactView(StrictModel):
    platform: str
    status: Literal["available", "missing", "unsupported", "invalid"]
    share_url: str | None = None
    final_url: str | None = None
    availability_status: Literal["reachable", "redirected", "blocked", "unreachable", "unchecked"]
    http_status: int | None = None
    checked_at: datetime | None = None
    last_accessible_at: datetime | None = None
    embed_status: Literal["allowed", "blocked", "unknown"]
    embed_reason: str | None = None


class AnswerShareImageView(StrictModel):
    pub_id: str = Field(pattern=r"^evd_[A-Za-z0-9]{16,64}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mime_type: Literal["image/png"]
    byte_size: int = Field(gt=0, le=30 * 1024 * 1024)
    image_width: int | None = Field(default=None, gt=0, le=100_000)
    image_height: int | None = Field(default=None, gt=0, le=100_000)
    capture_time: datetime


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


class DisparagementFactCheckView(StrictModel):
    verdict: str
    summary: str | None
    source_url: str | None
    checked_at: datetime


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
    content_origin: str
    fact_check: DisparagementFactCheckView | None = None


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


class SourceCitationHostView(StrictModel):
    host: str
    answers: int
    references: int
    is_own_site: bool


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
    answers_total: int
    answers_with_citation: int
    citation_coverage_rate: float | None
    answers_with_own_site_citation: int
    own_site_answer_citation_rate: float | None
    own_site_share_of_cited_answers: float | None
    citation_references_total: int
    own_site_citation_references: int
    own_site_reference_share: float | None
    own_site_cited_text_answers: int
    own_site_cited_text_evidence_rate: float | None
    documents_total: int
    own_site_documents: int
    own_site_share: float | None
    own_site_transcript_total: int
    own_site_transcript_accurate: int
    own_site_transcript_accuracy_rate: float | None
    own_site_adoption_evaluated_answers: int
    own_site_adoption_verified_answers: int
    own_site_adoption_rate: float | None
    verdicts: SourceAuditVerdictsView
    answer_hosts: list[SourceCitationHostView]
    hosts: list[SourceAuditHostView]
    items: list[SourceAuditItemView]


class SiteAuditSuggestionView(StrictModel):
    category: str
    severity: str
    title: str
    detail: str
    evidence_document_pub_id: str | None


class SiteAuditSuggestionsView(StrictModel):
    batch_pub_id: str | None
    generated_at: datetime | None
    model: str | None
    suggestions: list[SiteAuditSuggestionView]


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
    image_width: int | None = None
    image_height: int | None = None
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
    share_artifact: AnswerShareArtifactView | None
    share_image: AnswerShareImageView | None
    # Explicit semantic collections. ``citations``/``evidence`` remain for
    # compatibility, but consumers no longer need to guess whether a reference
    # organized the answer or actually contains the target brand.
    answer_citations: list[CitationRelationView]
    brand_mention_evidence: list[AnswerEvidenceView]
    opened_source_previews: list[AnswerEvidenceView]
    citations: list[CitationRelationView]
    evidence: list[AnswerEvidenceView]
    history: list[EvidenceHistoryView]


def _dsn() -> str:
    settings = get_settings()
    return (settings.runtime_postgres_dsn or settings.postgres_dsn).replace(
        "postgresql+psycopg://", "postgresql://"
    )


def _official_consumer() -> OfficialMetricsConsumer:
    return OfficialMetricsConsumer(MetricsV2Repository(_dsn()))


def _official_scope(
    *,
    principal: Principal,
    project_pub_id: str,
    start: date,
    end: date,
    model: str | None,
    region: str | None,
    mode: str | None,
    focal_entity_id: str | None,
) -> OfficialScope:
    return OfficialScope(
        tenant_pub_id=principal.tenant_pub_id,
        project_pub_id=project_pub_id,
        start=start,
        end=end,
        models=(model,) if model else (),
        regions=(region,) if region else (),
        modes=(mode,) if mode else (),
        focal_entity_ids=(focal_entity_id,) if focal_entity_id else (),
    )


def _error(
    request: Request, status_code: int, code: str, details: dict[str, Any] | None = None
) -> JSONResponse:
    """与 main.py 全局错误体同形的 JSONResponse（details 本层自定义填充——
    全局 HTTPException handler 丢弃 details，照 brandrank/fact_suggestions 路由先例）。"""
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": code.replace("_", " "),
                "request_id": request_id if isinstance(request_id, str) else "",
                "details": details or {},
            }
        },
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


def _safe_official_share_url(value: object, platform: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2_048 or not isinstance(platform, str):
        return None
    platform_key = platform.casefold()
    allowed_hosts = {
        "deepseek": {"chat.deepseek.com"},
        "doubao": {"doubao.com", "www.doubao.com"},
        "yiyan": {"mr.baidu.com", "wenxin.baidu.com"},
    }.get(platform_key, set())
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.port not in {None, 443}
        or parsed.username
        or parsed.password
    ):
        return None
    if platform_key == "deepseek" and not parsed.path.startswith("/share/"):
        return None
    if platform_key == "doubao" and not parsed.path.startswith("/thread/"):
        return None
    return urlunsplit(("https", parsed.hostname, parsed.path, parsed.query, ""))


def _safe_bbox(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    projected: dict[str, float] = {}
    for key in (
        "x",
        "y",
        "width",
        "height",
        "confidence",
        "image_width",
        "image_height",
    ):
        candidate = value.get(key)
        if (
            isinstance(candidate, int | float)
            and not isinstance(candidate, bool)
            and math.isfinite(candidate)
            and abs(float(candidate)) <= 1_000_000
        ):
            projected[key] = float(candidate)
    return projected or None


def _has_valid_brand_bbox(anchor: EvidenceAnchorView) -> bool:
    """Require a complete box proven to fit inside the decoded source PNG."""

    bbox = anchor.bbox
    if not isinstance(bbox, dict):
        return False
    for key in ("x", "y", "width", "height", "image_width", "image_height"):
        value = bbox.get(key)
        if (
            not isinstance(value, int | float)
            or isinstance(value, bool)
            or not math.isfinite(value)
            or abs(float(value)) > 1_000_000
        ):
            return False
    if (
        bbox["x"] < 0
        or bbox["y"] < 0
        or bbox["width"] <= 0
        or bbox["height"] <= 0
        or bbox["image_width"] <= 0
        or bbox["image_height"] <= 0
        or bbox["x"] + bbox["width"] > bbox["image_width"]
        or bbox["y"] + bbox["height"] > bbox["image_height"]
    ):
        return False
    confidence = bbox.get("confidence")
    return confidence is None or (
        isinstance(confidence, int | float)
        and not isinstance(confidence, bool)
        and math.isfinite(confidence)
        and 0 <= confidence <= 1
    )


def _is_brand_mention_evidence(item: AnswerEvidenceView) -> bool:
    return (
        item.relation_type == "brand_mention_source_snapshot"
        and item.kind == "source_screenshot"
        and item.mime_type == "image/png"
        and item.byte_size >= 128
        and any(_has_valid_brand_bbox(anchor) for anchor in item.anchors)
    )


def _project_fact_check(value: object) -> DisparagementFactCheckView | None:
    """T1 factcheck 行 → 视图；verdict 不合法（缺失/超长/非串）时整行降级为 None。

    verdict 词表（supported/refuted/unverifiable）由写入方约束，读路径不发明
    兜底词——宁可不外露也不伪造判定。
    """
    if not isinstance(value, dict):
        return None
    verdict = _safe_optional_text(value.get("verdict"), 40)
    checked_at = value.get("checked_at")
    if verdict is None or not isinstance(checked_at, datetime):
        return None
    return DisparagementFactCheckView(
        verdict=verdict,
        summary=_safe_optional_text(value.get("summary"), 2000),
        source_url=_safe_source_url(value.get("source_url")),
        checked_at=checked_at,
    )


@router.get("/sampling-progress", response_model=SamplingProgressView)
def sampling_progress(
    project_pub_id: str,
    page: int = Query(
        default=SAMPLING_PROGRESS_DEFAULT_PAGE_NUMBER,
        ge=SAMPLING_PROGRESS_MIN_PAGE_NUMBER,
        le=SAMPLING_PROGRESS_MAX_PAGE_NUMBER,
    ),
    page_size: int = Query(
        default=SAMPLING_PROGRESS_DEFAULT_PAGE_SIZE,
        ge=SAMPLING_PROGRESS_MIN_PAGE_SIZE,
        le=SAMPLING_PROGRESS_MAX_PAGE_SIZE,
    ),
    principal: Principal = Depends(get_principal),
) -> SamplingProgressView:
    """Latest logical sampling batch as query × formal sampling-leg coverage.

    Formal runs may be split into one frozen config per sampling leg and followed by
    small top-up configs. ``select_sampling_campaign`` joins those revisions back to
    their latest complete query plan; ``sampling_columns`` derives target legs only from
    complete configs. Effective fallback modes remain traceable inside each cell instead
    of inflating the target denominator.
    """

    principal.require("project:read")
    with tenant_connection(_dsn(), principal.tenant_pub_id, row_factory=dict_row) as connection:
        tenant_row = connection.execute(
            "SELECT id FROM platform.tenant WHERE pub_id=%s", (principal.tenant_pub_id,)
        ).fetchone()
        # analytics tables key RLS by tenant_pub_id; platform configuration tables key it by
        # tenant UUID. Establish both fail-closed contexts before reading across the schemas.
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)",
            (str(tenant_row["id"]) if tenant_row is not None else "",),
        )
        catalog_row = connection.execute(
            """
            SELECT version.pub_id AS catalog_config_pub_id,catalog.campaign_started_at
            FROM platform.answer_library_catalog catalog
            JOIN platform.project project ON project.id=catalog.project_id
            JOIN platform.monitoring_config_version version
              ON version.id=catalog.catalog_config_version_id
            JOIN platform.tenant tenant ON tenant.id=catalog.tenant_id
            WHERE tenant.pub_id=%s AND project.pub_id=%s
              AND catalog.activated_at<=now()
              AND (catalog.retired_at IS NULL OR catalog.retired_at>now())
            ORDER BY catalog.activated_at DESC,catalog.created_at DESC,catalog.pub_id DESC
            LIMIT 1
            """,
            (principal.tenant_pub_id, project_pub_id),
        ).fetchone()
        catalog_config_pub_id = (
            str(catalog_row["catalog_config_pub_id"]) if catalog_row is not None else None
        )
        campaign_started_at = (
            catalog_row["campaign_started_at"] if catalog_row is not None else None
        )
        config_rows = connection.execute(
            """
            SELECT version.pub_id,version.revision,version.snapshot_json
            FROM platform.monitoring_config_version version
            JOIN platform.monitoring_config config ON config.id=version.config_id
            JOIN platform.project project ON project.id=config.project_id
            JOIN platform.tenant tenant ON tenant.id=version.tenant_id
            WHERE tenant.pub_id=%s AND project.pub_id=%s
              AND (%s::timestamptz IS NULL OR version.frozen_at>=%s::timestamptz)
            ORDER BY version.revision DESC
            """,
            (
                principal.tenant_pub_id,
                project_pub_id,
                campaign_started_at,
                campaign_started_at,
            ),
        ).fetchall()
        baseline, campaign = select_sampling_campaign(
            parse_sampling_configs(config_rows), baseline_pub_id=catalog_config_pub_id
        )
        if baseline is None:
            empty_page = numbered_page(
                requested_page=page,
                page_size=page_size,
                total_count=0,
            )
            return SamplingProgressView(
                project_pub_id=project_pub_id,
                config_revision_start=None,
                config_revision_end=None,
                columns=[],
                rows=[],
                page=NumberedPageView(
                    page=empty_page.page,
                    page_size=empty_page.page_size,
                    total_count=empty_page.total_count,
                    total_pages=empty_page.total_pages,
                ),
                observed_cells=0,
                total_cells=0,
                answer_count=0,
                latest_capture_time=None,
                live_runs=0,
            )

        campaign_pub_ids = [config.pub_id for config in campaign]
        answer_rows = connection.execute(
            """
            SELECT query_text,model,region,mode,count(*)::bigint AS completed_samples,
                   max(capture_time) AS latest_capture_time,
                   array_agg(pub_id ORDER BY capture_time DESC,pub_id DESC) AS answer_pub_ids,
                   array_agg(capture_time ORDER BY capture_time DESC,pub_id DESC)
                     AS answer_capture_times
            FROM analytics.answer
            WHERE tenant_pub_id=%s AND project_pub_id=%s
              AND config_version_pub_id=ANY(%s::text[])
              -- INV-1: quarantined, pending and degraded captures are not progress.
              AND eligible IS TRUE AND degraded IS FALSE
            GROUP BY query_text,model,region,mode
            """,
            (principal.tenant_pub_id, project_pub_id, campaign_pub_ids),
        ).fetchall()
        live_run_row = connection.execute(
            """
            SELECT count(*)::bigint AS live_runs
            FROM platform.collection_run run
            JOIN platform.monitoring_config_version version ON version.id=run.config_version_id
            JOIN platform.tenant tenant ON tenant.id=run.tenant_id
            WHERE tenant.pub_id=%s AND version.pub_id=ANY(%s::text[])
              AND run.state NOT IN
                ('completed','completed_with_failures','failed','cancelled','skipped')
            """,
            (principal.tenant_pub_id, campaign_pub_ids),
        ).fetchone()
        live_runs = int(live_run_row["live_runs"]) if live_run_row is not None else 0

    plan_items = sampling_plan_items(baseline)
    columns = sampling_columns(campaign, baseline=baseline)
    column_keys = {
        (column.model, column.region, effective_mode): column.key
        for column in columns
        for effective_mode in column.modes
    }
    columns_by_key = {column.key: column for column in columns}
    column_order = {column.key: index for index, column in enumerate(columns)}
    plan_queries = {item.query_text for item in plan_items}
    mode_rows_by_cell: dict[tuple[str, str], list[dict[str, Any]]] = {}
    answer_count = 0
    latest_capture_time: datetime | None = None
    for answer_row in answer_rows:
        query_text = answer_row["query_text"]
        mode = answer_row["mode"]
        column_key = column_keys.get((answer_row["model"], answer_row["region"], mode))
        captured_at = answer_row["latest_capture_time"]
        if (
            not isinstance(query_text, str)
            or query_text not in plan_queries
            or not isinstance(mode, str)
            or column_key is None
            or not isinstance(captured_at, datetime)
        ):
            continue
        completed_samples = int(answer_row["completed_samples"])
        mode_rows_by_cell.setdefault((query_text, column_key), []).append(
            {
                "mode": mode,
                "completed_samples": completed_samples,
                "latest_capture_time": captured_at,
                "answer_pub_ids": [str(pub_id) for pub_id in answer_row["answer_pub_ids"]],
                "answer_capture_times": list(answer_row["answer_capture_times"]),
            }
        )
        answer_count += completed_samples
        if latest_capture_time is None or captured_at > latest_capture_time:
            latest_capture_time = captured_at

    cells_by_query: dict[str, list[SamplingProgressCellView]] = {}
    for (query_text, column_key), mode_rows in mode_rows_by_cell.items():
        column = columns_by_key[column_key]
        mode_order = {mode: index for index, mode in enumerate(column.modes)}
        ordered_mode_rows = sorted(
            mode_rows,
            key=lambda row: mode_order.get(str(row["mode"]), len(mode_order)),
        )
        newest_first_answers = sorted(
            (
                (answer_capture_time, str(answer_pub_id))
                for row in mode_rows
                for answer_capture_time, answer_pub_id in zip(
                    row["answer_capture_times"], row["answer_pub_ids"], strict=True
                )
            ),
            reverse=True,
        )
        cells_by_query.setdefault(query_text, []).append(
            SamplingProgressCellView(
                column_key=column_key,
                completed_samples=sum(int(row["completed_samples"]) for row in mode_rows),
                latest_capture_time=max(
                    row["latest_capture_time"]
                    for row in mode_rows
                    if isinstance(row["latest_capture_time"], datetime)
                ),
                answer_pub_ids=[pub_id for _, pub_id in newest_first_answers],
                mode_breakdown=[
                    SamplingProgressModeBreakdownView(
                        mode=str(row["mode"]),
                        completed_samples=int(row["completed_samples"]),
                        latest_capture_time=row["latest_capture_time"],
                        answer_pub_ids=[str(pub_id) for pub_id in row["answer_pub_ids"]],
                    )
                    for row in ordered_mode_rows
                ],
            )
        )
    for cells in cells_by_query.values():
        cells.sort(key=lambda cell: column_order[cell.column_key])
    observed_cells = len(mode_rows_by_cell)

    quotation_appendices = uses_quotation_appendices(plan_items)
    page_meta = numbered_page(
        requested_page=page,
        page_size=page_size,
        total_count=len(plan_items),
    )
    rows = [
        SamplingProgressRowView(
            appendix=("附录二" if item.group_index <= 18 else "附录三")
            if quotation_appendices
            else None,
            group=f"G{item.group_index:02d}",
            group_name=item.group_name,
            expression=variant_label(item.variant_index),
            query_text=item.query_text,
            cells=cells_by_query.get(item.query_text, []),
        )
        for item in plan_items[page_meta.offset : page_meta.offset + page_meta.page_size]
    ]
    revisions = [config.revision for config in campaign]
    return SamplingProgressView(
        project_pub_id=project_pub_id,
        config_revision_start=min(revisions),
        config_revision_end=max(revisions),
        columns=[
            SamplingProgressColumnView(
                key=column.key,
                model=column.model,
                region=column.region,
                mode=column.mode,
                modes=list(column.modes),
            )
            for column in columns
        ],
        rows=rows,
        page=NumberedPageView(
            page=page_meta.page,
            page_size=page_meta.page_size,
            total_count=page_meta.total_count,
            total_pages=page_meta.total_pages,
        ),
        observed_cells=observed_cells,
        total_cells=len(plan_items) * len(columns),
        answer_count=answer_count,
        latest_capture_time=latest_capture_time,
        live_runs=int(live_runs),
    )


@router.get("/answers", response_model=AnswerPage)
def answers(
    project_pub_id: str,
    answer_pub_id: str | None = Query(default=None, pattern=r"^ans_[A-Za-z0-9_-]{1,116}$"),
    cursor: str | None = Query(default=None, min_length=16, max_length=2_048),
    limit: int = Query(default=50, ge=1, le=100),
    model: str | None = None,
    region: str | None = None,
    mode: str | None = None,
    run_pub_id: str | None = None,
    principal: Principal = Depends(get_principal),
) -> AnswerPage:
    principal.require("project:read")
    filters = {
        "project_pub_id": project_pub_id,
        "answer_pub_id": answer_pub_id,
        "model": model,
        "region": region,
        "mode": mode,
        "run_pub_id": run_pub_id,
    }
    anchor = (
        decode_keyset_cursor(
            cursor,
            kind="analytics-answers",
            tenant_pub_id=principal.tenant_pub_id,
            filters=filters,
        )
        if cursor is not None
        else None
    )
    with tenant_connection(_dsn(), principal.tenant_pub_id, row_factory=dict_row) as connection:
        tenant_row = connection.execute(
            "SELECT id FROM platform.tenant WHERE pub_id=%s",
            (principal.tenant_pub_id,),
        ).fetchone()
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)",
            (str(tenant_row["id"]) if tenant_row is not None else "",),
        )
        rows = connection.execute(
            """
            WITH captured AS (
              SELECT task.pub_id,project.pub_id AS project_pub_id,
                     run.pub_id AS run_pub_id,config.pub_id AS config_version_pub_id,
                     answer.query_pub_id,
                     COALESCE(task.matrix_json::jsonb->>'query',answer.query_text) AS query_text,
                     COALESCE(
                       task.response_markdown_normalized,task.answer_text,''
                     ) AS response_text,
                     COALESCE(task.matrix_json::jsonb->>'model',answer.model,'unknown') AS model,
                     COALESCE(task.matrix_json::jsonb->>'region',answer.region,'unknown') AS region,
                     COALESCE(task.matrix_json::jsonb->>'mode',answer.mode,'unknown') AS mode,
                     answer.eligible,answer.degraded,task.created_at AS capture_time,
                     'completed'::text AS capture_state,run.id AS internal_run_id,
                     jsonb_array_length(COALESCE(task.citations_json,'[]')::jsonb)
                       AS captured_citation_count
              FROM platform.collection_task task
              JOIN platform.collection_run run ON run.id=task.run_id
              JOIN platform.project project ON project.id=run.project_id
              JOIN platform.monitoring_config_version config ON config.id=run.config_version_id
              JOIN platform.tenant tenant ON tenant.id=task.tenant_id
              LEFT JOIN analytics.answer answer
                ON answer.tenant_pub_id=tenant.pub_id AND answer.pub_id=task.pub_id
              WHERE tenant.pub_id=%s AND project.pub_id=%s AND task.state='completed'
            ), legacy AS (
              SELECT answer.pub_id,answer.project_pub_id,answer.run_pub_id,
                     answer.config_version_pub_id,answer.query_pub_id,answer.query_text,
                     answer.response_text,answer.model,answer.region,answer.mode,
                     answer.eligible,answer.degraded,answer.capture_time,
                     'legacy'::text AS capture_state,NULL::uuid AS internal_run_id,
                     (SELECT count(*)::int FROM analytics.citation_fact citation
                      WHERE citation.tenant_pub_id=answer.tenant_pub_id
                        AND citation.answer_pub_id=answer.pub_id) AS captured_citation_count
              FROM analytics.answer answer
              WHERE answer.tenant_pub_id=%s AND answer.project_pub_id=%s
                AND NOT EXISTS (
                  SELECT 1 FROM platform.collection_task task WHERE task.pub_id=answer.pub_id
                )
            ), answer_base AS (
              SELECT * FROM captured
              UNION ALL
              SELECT * FROM legacy
            )
            SELECT a.pub_id,a.project_pub_id,a.run_pub_id,a.config_version_pub_id,
                   a.query_pub_id,a.query_text,a.response_text,a.model,a.region,a.mode,
                   a.eligible,a.degraded,a.capture_time,a.capture_state,
                   COALESCE(answer_job.state,
                     CASE WHEN aa.analysis_run_pub_id IS NOT NULL THEN 'completed'
                          ELSE 'not_requested' END) AS answer_analysis_state,
                   COALESCE(source_jobs.state,'not_requested') AS source_analysis_state,
                   COALESCE(risk_jobs.state,'not_requested') AS risk_analysis_state,
                   aa.mentioned,aa.rank,aa.sentiment,aa.recommendation_state,
                   a.captured_citation_count AS citation_count
            FROM answer_base a
            LEFT JOIN LATERAL (
              SELECT analysis_run_pub_id,mentioned,rank,sentiment,recommendation_state
              FROM analytics.answer_analysis
              WHERE tenant_pub_id=%s AND answer_pub_id=a.pub_id
              ORDER BY created_at DESC LIMIT 1
            ) aa ON true
            LEFT JOIN LATERAL (
              SELECT state FROM platform.analysis_job
              WHERE subject_type='answer' AND subject_pub_id=a.pub_id
                AND analyzer_kind='answer_basic'
              ORDER BY created_at DESC LIMIT 1
            ) answer_job ON true
            LEFT JOIN LATERAL (
              SELECT CASE
                WHEN count(*)=0 THEN 'not_requested'
                WHEN bool_or(state='running') THEN 'running'
                WHEN bool_or(state='queued') THEN 'queued'
                WHEN bool_or(state='failed') THEN 'failed'
                WHEN bool_or(state='partial') THEN 'partial'
                WHEN bool_and(state='not_requested') THEN 'not_requested'
                WHEN bool_and(state='skipped') THEN 'skipped'
                WHEN bool_or(state IN ('not_requested','skipped')) THEN 'partial'
                ELSE 'completed' END AS state
              FROM platform.analysis_job
              WHERE run_id=a.internal_run_id
                AND analyzer_kind IN (
                  'own_site_snapshot','source_fetch','source_audit','page_inspection',
                  'site_suggestions'
                )
            ) source_jobs ON true
            LEFT JOIN LATERAL (
              SELECT CASE
                WHEN count(*)=0 THEN 'not_requested'
                WHEN bool_or(state='running') THEN 'running'
                WHEN bool_or(state='queued') THEN 'queued'
                WHEN bool_or(state='failed') THEN 'failed'
                WHEN bool_or(state='partial') THEN 'partial'
                WHEN bool_and(state='not_requested') THEN 'not_requested'
                WHEN bool_and(state='skipped') THEN 'skipped'
                WHEN bool_or(state IN ('not_requested','skipped')) THEN 'partial'
                ELSE 'completed' END AS state
              FROM platform.analysis_job
              WHERE run_id=a.internal_run_id
                AND analyzer_kind IN ('risk_disparagement','risk_factcheck')
            ) risk_jobs ON true
            WHERE (%s::text IS NULL OR a.pub_id=%s::text)
              AND (%s::text IS NULL OR a.pub_id>%s::text)
              AND (%s::text IS NULL OR a.model=%s::text)
              AND (%s::text IS NULL OR a.region=%s::text)
              AND (%s::text IS NULL OR a.mode=%s::text)
              AND (%s::text IS NULL OR a.run_pub_id=%s::text)
            ORDER BY a.pub_id LIMIT %s
            """,
            (
                principal.tenant_pub_id,
                project_pub_id,
                principal.tenant_pub_id,
                project_pub_id,
                principal.tenant_pub_id,
                answer_pub_id,
                answer_pub_id,
                anchor.pub_id if anchor else None,
                anchor.pub_id if anchor else None,
                model,
                model,
                region,
                region,
                mode,
                mode,
                run_pub_id,
                run_pub_id,
                limit + 1,
            ),
        ).fetchall()
    has_more = len(rows) > limit
    data = rows[:limit]
    next_cursor = None
    if has_more and data:
        last = data[-1]
        next_cursor = encode_keyset_cursor(
            kind="analytics-answers",
            tenant_pub_id=principal.tenant_pub_id,
            filters=filters,
            created_at=last["capture_time"],
            pub_id=last["pub_id"],
        )
    return AnswerPage(
        data=[AnswerView(**dict(row)) for row in data],
        page={
            "next_cursor": next_cursor,
            "has_more": has_more,
        },
    )


@router.get("/answers/{answer_pub_id}/relations", response_model=AnswerRelationsView)
def answer_relations(
    answer_pub_id: str,
    response: Response,
    project_pub_id: str | None = Query(
        default=None,
        pattern=r"^prj_[A-Za-z0-9_-]{1,116}$",
        description="Optional project binding for customer answer-detail reads.",
    ),
    snapshot_at: datetime | None = Query(
        default=None,
        description="Optional immutable customer-library cutoff for related evidence.",
    ),
    principal: Principal = Depends(get_principal),
) -> AnswerRelationsView:
    principal.require("project:read")
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Vary"] = "Cookie, Authorization"
    response.headers["X-Content-Type-Options"] = "nosniff"
    cutoff = snapshot_at.astimezone(UTC) if snapshot_at and snapshot_at.tzinfo else snapshot_at
    if snapshot_at is not None and (
        snapshot_at.tzinfo is None
        or cutoff is None
        or cutoff > datetime.now(UTC) + timedelta(minutes=1)
    ):
        raise HTTPException(status_code=422, detail={"code": "invalid_answer_snapshot"})
    with tenant_connection(_dsn(), principal.tenant_pub_id, row_factory=dict_row) as connection:
        tenant_row = connection.execute(
            "SELECT id FROM platform.tenant WHERE pub_id=%s",
            (principal.tenant_pub_id,),
        ).fetchone()
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)",
            (str(tenant_row["id"]) if tenant_row is not None else "",),
        )
        answer = connection.execute(
            """
            SELECT task.pub_id,project.pub_id AS project_pub_id,
                   task.citations_json::jsonb AS captured_citations,brand.website
            FROM platform.collection_task task
            JOIN platform.collection_run run ON run.id=task.run_id
            JOIN platform.project project ON project.id=run.project_id
            JOIN platform.tenant tenant ON tenant.id=task.tenant_id
            LEFT JOIN LATERAL (
              SELECT website FROM platform.brand
              WHERE project_id=project.id
              ORDER BY created_at,pub_id LIMIT 1
            ) brand ON true
            WHERE tenant.pub_id=%s AND task.pub_id=%s AND task.state='completed'
              AND (%s::text IS NULL OR project.pub_id=%s::text)
              AND (%s::timestamptz IS NULL OR task.created_at<=%s::timestamptz)
            UNION ALL
            SELECT analytics.pub_id,analytics.project_pub_id,NULL::jsonb,NULL::text
            FROM analytics.answer analytics
            WHERE analytics.tenant_pub_id=%s AND analytics.pub_id=%s
              AND (%s::text IS NULL OR analytics.project_pub_id=%s::text)
              AND (%s::timestamptz IS NULL OR analytics.created_at<=%s::timestamptz)
              AND NOT EXISTS (
                SELECT 1 FROM platform.collection_task task WHERE task.pub_id=analytics.pub_id
              )
            LIMIT 1
            """,
            (
                principal.tenant_pub_id,
                answer_pub_id,
                project_pub_id,
                project_pub_id,
                cutoff,
                cutoff,
                principal.tenant_pub_id,
                answer_pub_id,
                project_pub_id,
                project_pub_id,
                cutoff,
                cutoff,
            ),
        ).fetchone()
        if answer is None:
            raise HTTPException(status_code=404, detail={"code": "answer_not_found"})
        citations = connection.execute(
            """
            SELECT c.pub_id,c.ordinal,c.platform_ordinal,c.ordinal_base,c.canonical_url,c.host,
                   c.title,c.cited_text,c.own_source,c.content_hash,c.source_document_pub_id,
                   c.published_at_raw,c.published_at,c.published_at_timezone,
                   c.published_at_precision,c.published_at_source,c.published_at_confidence,
                   relation.mapping_status AS support_mapping_status,
                   relation.mapping_basis AS support_mapping_basis,
                   relation.answer_text_start AS support_answer_text_start,
                   relation.answer_text_end AS support_answer_text_end,
                   relation.answer_ast_path AS support_answer_ast_path,
                   relation.answer_sentence AS support_answer_sentence,
                   relation.source_quote AS support_source_quote,
                   relation.source_text_start AS support_source_text_start,
                   relation.source_text_end AS support_source_text_end,
                   relation.source_quote_hash AS support_source_quote_hash,
                   relation.source_match_status AS support_source_match_status,
                   relation.source_match_version AS support_source_match_version,
                   relation.relation AS support_relation,
                   relation.relevance_confidence AS support_relevance_confidence,
                   relation.classifier_version AS support_classifier_version,
                   relation.review_status AS support_review_status
            FROM analytics.citation_fact c
            LEFT JOIN analytics.answer_citation_relation relation
             ON relation.tenant_pub_id=c.tenant_pub_id
             AND relation.answer_pub_id=c.answer_pub_id
             AND relation.ordinal=c.ordinal
             AND (%s::timestamptz IS NULL OR (
                   relation.created_at<=%s::timestamptz
                   AND relation.updated_at<=%s::timestamptz
                 ))
            WHERE c.tenant_pub_id=%s AND c.answer_pub_id=%s
              AND (%s::timestamptz IS NULL OR c.created_at<=%s::timestamptz)
              AND c.analysis_run_pub_id=(
                SELECT aa.analysis_run_pub_id
                FROM analytics.answer_analysis aa
                WHERE aa.tenant_pub_id=%s AND aa.answer_pub_id=%s
                  AND (%s::timestamptz IS NULL OR aa.created_at<=%s::timestamptz)
                ORDER BY aa.created_at DESC,aa.id DESC
                LIMIT 1
              )
            ORDER BY c.ordinal,c.created_at,c.pub_id
            """,
            (
                cutoff,
                cutoff,
                cutoff,
                principal.tenant_pub_id,
                answer_pub_id,
                cutoff,
                cutoff,
                principal.tenant_pub_id,
                answer_pub_id,
                cutoff,
                cutoff,
            ),
        ).fetchall()
        if not citations and isinstance(answer.get("captured_citations"), list):
            website_host = None
            if isinstance(answer.get("website"), str):
                website_value = str(answer["website"])
                try:
                    website_host = urlsplit(
                        website_value if "://" in website_value else f"https://{website_value}"
                    ).hostname
                except ValueError:
                    website_host = None
            captured_rows: list[dict[str, Any]] = []
            for index, item in enumerate(answer["captured_citations"], 1):
                if not isinstance(item, dict):
                    continue
                canonical_url = _safe_source_url(item.get("url"))
                if canonical_url is None:
                    continue
                host = urlsplit(canonical_url).hostname or ""
                own_source = bool(
                    website_host
                    and (
                        host == website_host
                        or host.removeprefix("www.") == website_host.removeprefix("www.")
                    )
                )
                ordinal = int(item["ordinal"]) if item.get("ordinal") is not None else index
                platform_ordinal = (
                    int(item["platform_ordinal"])
                    if item.get("platform_ordinal") is not None
                    else ordinal
                )
                ordinal_base = (
                    int(item["ordinal_base"]) if item.get("ordinal_base") is not None else 1
                )
                stable_key = f"{answer_pub_id}|{ordinal}|{canonical_url}"
                captured_rows.append(
                    {
                        "pub_id": f"cit_capture_{sha256(stable_key.encode()).hexdigest()[:20]}",
                        "ordinal": ordinal,
                        "platform_ordinal": platform_ordinal,
                        "ordinal_base": ordinal_base,
                        "canonical_url": canonical_url,
                        "host": host,
                        "title": item.get("title"),
                        "cited_text": item.get("cited_text"),
                        "own_source": own_source,
                        "content_hash": None,
                        "source_document_pub_id": None,
                        "published_at_raw": None,
                        "published_at": None,
                        "published_at_timezone": None,
                        "published_at_precision": None,
                        "published_at_source": None,
                        "published_at_confidence": "unknown",
                        "support_mapping_status": "unmapped",
                        "support_mapping_basis": None,
                        "support_answer_text_start": None,
                        "support_answer_text_end": None,
                        "support_answer_ast_path": None,
                        "support_answer_sentence": None,
                        "support_source_quote": None,
                        "support_source_text_start": None,
                        "support_source_text_end": None,
                        "support_source_quote_hash": None,
                        "support_source_match_status": "not_checked",
                        "support_source_match_version": None,
                        "support_relation": "unverified",
                        "support_relevance_confidence": None,
                        "support_classifier_version": None,
                        "support_review_status": "unreviewed",
                    }
                )
            citations = captured_rows
        share_artifact = connection.execute(
            """
            SELECT platform,status,share_url,final_url,allowlist_valid,
                   availability_status,http_status,checked_at,last_accessible_at,
                   embed_status,embed_reason,
                   share_image.pub_id AS share_image_pub_id,
                   share_image.sha256 AS share_image_sha256,
                   share_image.mime_type AS share_image_mime_type,
                   share_image.byte_size AS share_image_byte_size,
                   share_image.image_width AS share_image_width,
                   share_image.image_height AS share_image_height,
                   share_image.capture_time AS share_image_capture_time
            FROM evidence.answer_share_artifact artifact
            LEFT JOIN LATERAL (
              SELECT asset.pub_id,asset.sha256,asset.mime_type,asset.byte_size,
                     asset.image_width,asset.image_height,asset.capture_time
              FROM evidence.evidence_asset asset
              JOIN evidence.evidence_relation relation
                ON relation.tenant_pub_id=asset.tenant_pub_id
               AND relation.to_pub_id=asset.pub_id
               AND relation.from_pub_id=artifact.answer_pub_id
               AND relation.relation_type='official_share_image'
              WHERE asset.tenant_pub_id=artifact.tenant_pub_id
                AND asset.project_pub_id=artifact.project_pub_id
                AND asset.pub_id=artifact.share_image_evidence_pub_id
                AND asset.kind='share_image'
                AND asset.customer_visible=true
                AND asset.deleted_at IS NULL
                AND asset.mime_type='image/png'
                AND asset.byte_size BETWEEN 1 AND 31457280
              LIMIT 1
            ) share_image ON true
            WHERE artifact.tenant_pub_id=%s AND artifact.answer_pub_id=%s
              AND (%s::timestamptz IS NULL OR (
                    created_at<=%s::timestamptz AND updated_at<=%s::timestamptz
                  ))
            """,
            (principal.tenant_pub_id, answer_pub_id, cutoff, cutoff, cutoff),
        ).fetchone()
        evidence_rows = (
            connection.execute(
                """
                SELECT ea.pub_id,er.relation_type,ea.kind,ea.access_class,ea.sha256,
                       ea.mime_type,ea.byte_size,ea.image_width,ea.image_height,
                       ea.source_url,ea.capture_time
                FROM evidence.evidence_relation er
                JOIN evidence.evidence_asset ea
                  ON ea.tenant_pub_id=er.tenant_pub_id AND ea.pub_id=er.to_pub_id
                WHERE er.tenant_pub_id=%s AND er.from_pub_id=%s AND ea.deleted_at IS NULL
                  AND (%s::timestamptz IS NULL OR (
                        er.created_at<=%s::timestamptz
                        AND ea.created_at<=%s::timestamptz
                      ))
                ORDER BY ea.capture_time,ea.pub_id
                """,
                (principal.tenant_pub_id, answer_pub_id, cutoff, cutoff, cutoff),
            ).fetchall()
            if principal.allows("evidence:read")
            else []
        )
        evidence_ids = [row["pub_id"] for row in evidence_rows]
        anchors = (
            connection.execute(
                """
                SELECT pub_id,evidence_pub_id,text_start,text_end,bbox,page_number,quote_hash
                FROM evidence.evidence_anchor
                WHERE tenant_pub_id=%s AND evidence_pub_id=ANY(%s::text[])
                  AND (%s::timestamptz IS NULL OR created_at<=%s::timestamptz)
                ORDER BY evidence_pub_id,page_number,text_start,pub_id
                """,
                (principal.tenant_pub_id, evidence_ids, cutoff, cutoff),
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
                  AND (%s::timestamptz IS NULL OR created_at<=%s::timestamptz)
                ORDER BY created_at,pub_id
                """,
                (principal.tenant_pub_id, evidence_ids, evidence_ids, cutoff, cutoff),
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
    citation_views = [
        CitationRelationView(
            **{
                key: value
                for key, value in dict(row).items()
                if key
                not in {
                    "canonical_url",
                    "title",
                    "cited_text",
                    "published_at_raw",
                    "published_at_source",
                    "support_mapping_status",
                    "support_mapping_basis",
                    "support_answer_text_start",
                    "support_answer_text_end",
                    "support_answer_ast_path",
                    "support_answer_sentence",
                    "support_source_quote",
                    "support_source_text_start",
                    "support_source_text_end",
                    "support_source_quote_hash",
                    "support_source_match_status",
                    "support_source_match_version",
                    "support_relation",
                    "support_relevance_confidence",
                    "support_classifier_version",
                    "support_review_status",
                }
            },
            canonical_url=_safe_source_url(row["canonical_url"]) or "",
            title=_safe_optional_text(row["title"], 300),
            cited_text=_safe_optional_text(row["cited_text"], 2000),
            published_at_raw=_safe_optional_text(row["published_at_raw"], 500),
            published_at_source=_safe_optional_text(row["published_at_source"], 120),
            support=CitationSupportView(
                mapping_status=row.get("support_mapping_status") or "unmapped",
                mapping_basis=_safe_optional_text(row.get("support_mapping_basis"), 80),
                answer_text_start=row.get("support_answer_text_start"),
                answer_text_end=row.get("support_answer_text_end"),
                answer_ast_path=row.get("support_answer_ast_path"),
                answer_sentence=_safe_optional_text(row.get("support_answer_sentence"), 4_000),
                source_quote=_safe_optional_text(row.get("support_source_quote"), 2_000),
                source_text_start=row.get("support_source_text_start"),
                source_text_end=row.get("support_source_text_end"),
                source_quote_hash=row.get("support_source_quote_hash"),
                source_match_status=row.get("support_source_match_status") or "not_checked",
                source_match_version=_safe_optional_text(
                    row.get("support_source_match_version"), 80
                ),
                relation=row.get("support_relation") or "unverified",
                relevance_confidence=(
                    float(row["support_relevance_confidence"])
                    if row.get("support_relevance_confidence") is not None
                    else None
                ),
                classifier_version=_safe_optional_text(row.get("support_classifier_version"), 80),
                review_status=row.get("support_review_status") or "unreviewed",
            ),
        )
        for row in citations
    ]
    evidence_views = [
        AnswerEvidenceView(
            **{key: value for key, value in dict(row).items() if key != "source_url"},
            source_url=_safe_source_url(row["source_url"]),
            anchors=anchors_by_evidence.get(row["pub_id"], []),
        )
        for row in evidence_rows
    ]
    brand_mention_evidence = [item for item in evidence_views if _is_brand_mention_evidence(item)]
    opened_source_previews = [
        item
        for item in evidence_views
        if item.relation_type == "ai_opened_source_preview" and item.kind == "source_screenshot"
    ]
    return AnswerRelationsView(
        answer_pub_id=answer_pub_id,
        share_artifact=(
            AnswerShareArtifactView(
                platform=_safe_optional_text(share_artifact["platform"], 40) or "unknown",
                status=share_artifact["status"],
                share_url=(
                    _safe_official_share_url(
                        share_artifact["share_url"], share_artifact["platform"]
                    )
                    if share_artifact["allowlist_valid"] and share_artifact["status"] == "available"
                    else None
                ),
                final_url=(
                    _safe_official_share_url(
                        share_artifact["final_url"], share_artifact["platform"]
                    )
                    if share_artifact["allowlist_valid"] and share_artifact["status"] == "available"
                    else None
                ),
                availability_status=share_artifact["availability_status"],
                http_status=share_artifact["http_status"],
                checked_at=share_artifact["checked_at"],
                last_accessible_at=share_artifact["last_accessible_at"],
                embed_status=share_artifact["embed_status"],
                embed_reason=_safe_optional_text(share_artifact["embed_reason"], 1_000),
            )
            if share_artifact is not None
            else None
        ),
        share_image=(
            AnswerShareImageView(
                pub_id=share_artifact["share_image_pub_id"],
                sha256=share_artifact["share_image_sha256"],
                mime_type=share_artifact["share_image_mime_type"],
                byte_size=share_artifact["share_image_byte_size"],
                image_width=share_artifact["share_image_width"],
                image_height=share_artifact["share_image_height"],
                capture_time=share_artifact["share_image_capture_time"],
            )
            if share_artifact is not None and share_artifact["share_image_pub_id"] is not None
            else None
        ),
        answer_citations=citation_views,
        brand_mention_evidence=brand_mention_evidence,
        opened_source_previews=opened_source_previews,
        citations=citation_views,
        evidence=evidence_views,
        history=[
            EvidenceHistoryView(
                **dict(row),
                similarity=float(row["similarity"]) if row["similarity"] is not None else None,
            )
            for row in history
        ],
    )


@router.get(
    "/official/overview",
    response_model=None,
    operation_id="getOfficialAnalyticsOverviewV2",
)
def official_overview(
    project_pub_id: str,
    start: date,
    end: date,
    model: str | None = None,
    region: str | None = None,
    mode: str | None = None,
    focal_entity_id: str | None = None,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Formal Analytics cards from one immutable V2 ``official`` set."""

    principal.require("project:read")
    try:
        return _official_consumer().overview(
            _official_scope(
                principal=principal,
                project_pub_id=project_pub_id,
                start=start,
                end=end,
                model=model,
                region=region,
                mode=mode,
                focal_entity_id=focal_entity_id,
            )
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "official_metric_snapshot_set_not_found"}
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_official_metric_scope"}
        ) from exc


@router.get(
    "/official/breakdown",
    response_model=None,
    operation_id="getOfficialAnalyticsBreakdownV2",
)
def official_breakdown(
    project_pub_id: str,
    start: date,
    end: date,
    group_by: Literal["day", "model", "region_mode", "question"],
    model: str | None = None,
    region: str | None = None,
    mode: str | None = None,
    focal_entity_id: str | None = None,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Breakdown of persisted V2 member contributions; no answer/rank SQL."""

    principal.require("project:read")
    try:
        return _official_consumer().breakdown(
            _official_scope(
                principal=principal,
                project_pub_id=project_pub_id,
                start=start,
                end=end,
                model=model,
                region=region,
                mode=mode,
                focal_entity_id=focal_entity_id,
            ),
            group_by=group_by,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "official_metric_snapshot_set_not_found"}
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_official_metric_scope"}
        ) from exc


@router.get(
    "/official/delta",
    response_model=None,
    operation_id="getOfficialAnalyticsDeltaV2",
)
def official_delta(
    project_pub_id: str,
    start: date,
    end: date,
    model: str | None = None,
    region: str | None = None,
    mode: str | None = None,
    focal_entity_id: str | None = None,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Paired-period view; incompatible support is an explicit null delta."""

    principal.require("project:read")
    try:
        return _official_consumer().delta(
            _official_scope(
                principal=principal,
                project_pub_id=project_pub_id,
                start=start,
                end=end,
                model=model,
                region=region,
                mode=mode,
                focal_entity_id=focal_entity_id,
            )
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "official_metric_snapshot_set_not_found"}
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_official_metric_scope"}
        ) from exc


@router.get(
    "/overview",
    response_model=list[MetricView],
    deprecated=True,
    tags=["analytics-legacy"],
)
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


@router.get(
    "/breakdown",
    response_model=list[BreakdownView],
    deprecated=True,
    tags=["analytics-legacy"],
)
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


@router.get("/delta", deprecated=True, tags=["analytics-legacy"])
def delta(
    project_pub_id: str,
    start: date,
    end: date,
    config_version: str | None = Query(default=None, pattern=r"^cfv_[A-Za-z0-9_-]{1,116}$"),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """前后等长窗口四指标 delta；config_version（冻结配置 pub_id）传入时两窗口
    只统计该配置产出的答案（报价单前后对比口径），不传时行为与旧版一致。

    口径标注：本端点为 metric_daily **启发式层**（casefold 子串判提及、正则排名、
    top1/3/10 单分母、0..1 比率）。品牌前后对比（报价单服务④口径：brandrank 层
    LLM 抽取 + 规则归并 + 双分母，与报告 before_after 扩展组同一份代码）请用
    POST/GET /api/v2/analytics/comparisons。"""
    principal.require("project:read")
    result = AnalyticsService(dsn=_dsn()).previous_period_delta(
        tenant_pub_id=principal.tenant_pub_id,
        project_pub_id=project_pub_id,
        start=start,
        end=end,
        config_version=config_version,
    )
    return {
        metric: {key: float(value) if value is not None else None for key, value in values.items()}
        for metric, values in result.items()
    }


class ComparisonCreate(StrictModel):
    project_pub_id: str = Field(min_length=5, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    baseline_run_pub_ids: list[str] = Field(min_length=1, max_length=100)
    optimized_run_pub_ids: list[str] = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("baseline_run_pub_ids", "optimized_run_pub_ids")
    @classmethod
    def run_pub_id_shape(cls, value: list[str]) -> list[str]:
        for item in value:
            if not _RUN_PUB_ID_RE.fullmatch(item):
                raise ValueError("invalid_run_pub_id")
        return value


@router.post("/comparisons", status_code=201, response_model=None)
def create_run_comparison(
    request: Request,
    body: ComparisonCreate,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any] | JSONResponse:
    """创建「基线 run 组 vs 优化后 run 组」对比实体（报价单服务④，brandrank 层口径）。

    两臂数组非空且元素须为合法 run pub id 形状（否则 422）；所有 run 必须存在
    且属于本 tenant+project，否则 400 unknown_run_pub_id（跨项目/跨租户同码，
    不泄露存在性）。幂等头照 sop 轻量口径：接受 + 校验 + 响应头回显，不去重。
    """
    principal.require("schedule:manage")
    try:
        entity = comparisons.create_comparison(
            _dsn(),
            principal.tenant_pub_id,
            project_pub_id=body.project_pub_id,
            name=body.name,
            baseline_run_pub_ids=body.baseline_run_pub_ids,
            optimized_run_pub_ids=body.optimized_run_pub_ids,
            note=body.note,
            created_by=principal.actor_pub_id,
        )
    except comparisons.UnknownRunPubId as exc:
        return _error(request, 400, "unknown_run_pub_id", {"unknown_run_pub_ids": exc.unknown})
    if idempotency_key is not None:
        response.headers["Idempotency-Key"] = idempotency_key
    return entity


@router.get("/comparisons")
def list_run_comparisons(
    project_pub_id: str,
    cursor: str | None = Query(default=None, min_length=16, max_length=2_048),
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """项目下 run 组对比实体；不透明游标绑定租户与项目过滤。"""
    principal.require("project:read")
    filters = {"project_pub_id": project_pub_id}
    anchor = (
        decode_keyset_cursor(
            cursor,
            kind="analytics-run-comparisons",
            tenant_pub_id=principal.tenant_pub_id,
            filters=filters,
        )
        if cursor is not None
        else None
    )
    rows = comparisons.list_comparisons(
        _dsn(),
        principal.tenant_pub_id,
        project_pub_id,
        limit + 1,
        cursor_created_at=anchor.created_at if anchor else None,
        cursor_pub_id=anchor.pub_id if anchor else None,
    )
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_keyset_cursor(
            kind="analytics-run-comparisons",
            tenant_pub_id=principal.tenant_pub_id,
            filters=filters,
            created_at=last["created_at"],
            pub_id=last["pub_id"],
        )
    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}


@router.get("/comparisons/{comparison_pub_id}", response_model=None)
def get_run_comparison(
    request: Request,
    comparison_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any] | JSONResponse:
    """对比实体 + 现场计算的 result（brandrank 层 compare.py，与报告 before_after
    扩展组同一份代码：同臂谓词、同 analyze 管线、同五指标行结构、同 insufficient 语义）。

    result.aggregate.metrics = 扩展组同构五行；result.questions = 逐题配对
    （query_text 配对键），只在一臂出现（答案级）的进 unpaired。
    """
    principal.require("project:read")
    entity = comparisons.fetch_comparison(_dsn(), principal.tenant_pub_id, comparison_pub_id)
    if entity is None:
        raise HTTPException(status_code=404, detail={"code": "comparison_not_found"})
    try:
        result = brandrank_compare.compute_run_comparison(
            dsn=_dsn(), tenant_pub_id=principal.tenant_pub_id, comparison=entity
        )
    except brandrank_service.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"}) from exc
    except brandrank_compare.DomainUnset:
        return _error(
            request,
            400,
            "domain_unset",
            {"why": "项目未设置 brandrank_domain（规则包真源），请先在项目设置中选择分析域"},
        )
    except brandrank_service.UnknownDomain as exc:
        return _error(
            request, 400, "unknown_domain", {"available": available_domains(), "why": str(exc)}
        )
    return {**entity, "result": result}


@router.get("/competitors", response_model=list[CompetitorView])
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
                float(row["disparagement_rate"]) if row["disparagement_rate"] is not None else None
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
            content_origin=row["content_origin"],
            fact_check=_project_fact_check(row["fact_check"]),
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
    """W2 官网引用能效：回答引用口径 + 抓取/审计口径分层返回。"""
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
        answers_total=overview["answers_total"],
        answers_with_citation=overview["answers_with_citation"],
        citation_coverage_rate=overview["citation_coverage_rate"],
        answers_with_own_site_citation=overview["answers_with_own_site_citation"],
        own_site_answer_citation_rate=overview["own_site_answer_citation_rate"],
        own_site_share_of_cited_answers=overview["own_site_share_of_cited_answers"],
        citation_references_total=overview["citation_references_total"],
        own_site_citation_references=overview["own_site_citation_references"],
        own_site_reference_share=overview["own_site_reference_share"],
        own_site_cited_text_answers=overview["own_site_cited_text_answers"],
        own_site_cited_text_evidence_rate=overview["own_site_cited_text_evidence_rate"],
        documents_total=overview["documents_total"],
        own_site_documents=overview["own_site_documents"],
        own_site_share=overview["own_site_share"],
        own_site_transcript_total=overview["own_site_transcript_total"],
        own_site_transcript_accurate=overview["own_site_transcript_accurate"],
        own_site_transcript_accuracy_rate=overview["own_site_transcript_accuracy_rate"],
        own_site_adoption_evaluated_answers=overview["own_site_adoption_evaluated_answers"],
        own_site_adoption_verified_answers=overview["own_site_adoption_verified_answers"],
        own_site_adoption_rate=overview["own_site_adoption_rate"],
        verdicts=SourceAuditVerdictsView(
            transcript=SourceAuditVerdictBucketView(**overview["verdicts"]["transcript"]),
            factual=SourceAuditVerdictBucketView(**overview["verdicts"]["factual"]),
        ),
        answer_hosts=[SourceCitationHostView(**row) for row in overview["answer_hosts"]],
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


@router.get("/source-audit/site-suggestions", response_model=SiteAuditSuggestionsView)
def site_audit_suggestions(
    project: str,
    principal: Principal = Depends(get_principal),
) -> SiteAuditSuggestionsView:
    """W2 官网内容问题与优化建议：契约表 T2 最新批次。

    T2 未迁移上线 / 无数据时批次字段全 null + suggestions=[]（不 404/500）。
    """
    principal.require("project:read")
    result = AnalyticsService(dsn=_dsn()).site_audit_suggestions(
        tenant_pub_id=principal.tenant_pub_id,
        project_pub_id=project,
    )
    return SiteAuditSuggestionsView(
        batch_pub_id=result["batch_pub_id"],
        generated_at=result["generated_at"],
        model=result["model"],
        suggestions=[
            SiteAuditSuggestionView(
                category=_safe_optional_text(row["category"], 40) or "other",
                severity=_safe_optional_text(row["severity"], 20) or "medium",
                title=_safe_optional_text(row["title"], 200) or "",
                detail=_safe_optional_text(row["detail"], 4000) or "",
                evidence_document_pub_id=(
                    row["evidence_document_pub_id"]
                    if isinstance(row["evidence_document_pub_id"], str)
                    and len(row["evidence_document_pub_id"]) <= 120
                    else None
                ),
            )
            for row in result["suggestions"]
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
