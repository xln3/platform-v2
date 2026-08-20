from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CustomerMetricView(StrictModel):
    code: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    group: str = Field(min_length=1, max_length=40)
    format: Literal["percentage", "score", "rank", "count", "decimal"]
    direction: Literal["higher", "lower", "neutral"]
    value: float | int | None
    state: Literal["ready", "not_ready"]
    version: str = Field(min_length=1, max_length=80)


class CustomerMetricSpecView(StrictModel):
    code: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    group: str = Field(min_length=1, max_length=40)
    format: Literal["percentage", "score", "rank", "count", "decimal"]
    direction: Literal["higher", "lower", "neutral"]
    description: str = Field(min_length=1, max_length=500)
    version: str = Field(min_length=1, max_length=80)


class CustomerMetricCatalogView(StrictModel):
    schema_version: Literal["customer-metric-catalog-v1"]
    metrics: list[CustomerMetricSpecView]


class CustomerWindowView(StrictModel):
    start: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    filters: dict[str, str]


class CustomerDimensionView(StrictModel):
    key: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=160)
    metrics: list[CustomerMetricView]


class CustomerCompetitorView(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    metrics: list[CustomerMetricView]


class CustomerQuestionView(StrictModel):
    query_pub_id: str = Field(min_length=1, max_length=120)
    query_text: str = Field(min_length=1, max_length=2000)
    query_group: str | None = Field(default=None, max_length=200)
    metrics: list[CustomerMetricView]


class CustomerSourceView(StrictModel):
    host: str = Field(min_length=1, max_length=255)
    references: int = Field(ge=0)
    share: float | int | None = Field(default=None, ge=0, le=1)
    own_source: bool
    answers: int = Field(ge=0)


class CustomerTrendView(StrictModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    metrics: list[CustomerMetricView]


class CustomerRiskView(StrictModel):
    metrics: list[CustomerMetricView]
    by_model: list[CustomerDimensionView]


class CustomerSourceAuditView(StrictModel):
    metrics: list[CustomerMetricView]
    verdicts: dict[str, int]


class CustomerAnswerView(StrictModel):
    answer_pub_id: str = Field(pattern=r"^ans_[A-Za-z0-9_-]{1,116}$")
    query_pub_id: str | None = Field(
        default=None,
        pattern=r"^qry_(?:hash_)?[A-Za-z0-9_-]{1,116}$",
    )
    query_text: str | None = Field(default=None, max_length=20_000)
    response_text: str = Field(max_length=200_000)
    model: str = Field(min_length=1, max_length=120)
    region: str = Field(min_length=1, max_length=120)
    mode: str = Field(min_length=1, max_length=80)
    capture_time: datetime
    mentioned: bool
    rank: int | None = Field(default=None, ge=1)
    sentiment: Literal["positive", "neutral", "negative", "unknown"] | None = None
    recommended: bool | None = None
    citation_count: int = Field(ge=0)


class CustomerAnswerPageMetaView(StrictModel):
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=50)
    has_more: bool


class CustomerAnswerPageView(StrictModel):
    schema_version: Literal["customer-answer-page-v1"]
    project_pub_id: str = Field(pattern=r"^prj_[A-Za-z0-9_-]{1,116}$")
    data: list[CustomerAnswerView] = Field(max_length=50)
    page: CustomerAnswerPageMetaView


class CustomerAnswerLibraryDimensionView(StrictModel):
    label: str = Field(min_length=1, max_length=120)
    answer_count: int = Field(ge=0)


class CustomerAnswerLibraryTotalsView(StrictModel):
    meta_query_count: int = Field(ge=0)
    question_count: int = Field(ge=0)
    answer_count: int = Field(ge=0)
    cited_answer_count: int = Field(ge=0)
    citation_count: int = Field(ge=0)
    mentioned_answer_count: int = Field(ge=0)
    unmapped_answer_count: int = Field(ge=0)


class CustomerAnswerLibraryChoiceView(StrictModel):
    question_id: str = Field(pattern=r"^aq_[0-9a-f]{24}$")
    ordinal: int = Field(ge=1, le=500)
    variant_label: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=2_000)
    answer_count: int = Field(ge=0)


class CustomerAnswerLibraryMetaQueryView(StrictModel):
    meta_query_id: str = Field(pattern=r"^amq_[0-9a-f]{24}$")
    ordinal: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=2_000)
    question_count: int = Field(ge=1, le=500)
    answer_count: int = Field(ge=0)
    cited_answer_count: int = Field(ge=0)
    citation_count: int = Field(ge=0)
    mentioned_answer_count: int = Field(ge=0)
    latest_capture_time: datetime | None = None
    models: list[CustomerAnswerLibraryDimensionView] = Field(max_length=100)
    regions: list[CustomerAnswerLibraryDimensionView] = Field(max_length=100)
    modes: list[CustomerAnswerLibraryDimensionView] = Field(max_length=100)
    questions: list[CustomerAnswerLibraryChoiceView] = Field(min_length=1, max_length=500)


class CustomerAnswerLibraryPageMetaView(StrictModel):
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=50)
    has_more: bool


class CustomerAnswerLibraryPageView(StrictModel):
    schema_version: Literal["customer-answer-library-v1"]
    project_pub_id: str = Field(pattern=r"^prj_[A-Za-z0-9_-]{1,116}$")
    snapshot_id: str = Field(pattern=r"^als_[0-9a-f]{24}$")
    snapshot_at: datetime
    totals: CustomerAnswerLibraryTotalsView
    models: list[CustomerAnswerLibraryDimensionView] = Field(max_length=100)
    regions: list[CustomerAnswerLibraryDimensionView] = Field(max_length=100)
    modes: list[CustomerAnswerLibraryDimensionView] = Field(max_length=100)
    data: list[CustomerAnswerLibraryMetaQueryView] = Field(max_length=20)
    page: CustomerAnswerLibraryPageMetaView


class CustomerAnswerLibraryQuestionView(StrictModel):
    question_id: str = Field(pattern=r"^aq_[0-9a-f]{24}$")
    ordinal: int = Field(ge=1, le=500)
    variant_label: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=2_000)
    answer_count: int = Field(ge=0)
    cited_answer_count: int = Field(ge=0)
    citation_count: int = Field(ge=0)
    mentioned_answer_count: int = Field(ge=0)
    latest_capture_time: datetime | None = None
    models: list[CustomerAnswerLibraryDimensionView] = Field(max_length=100)
    regions: list[CustomerAnswerLibraryDimensionView] = Field(max_length=100)
    modes: list[CustomerAnswerLibraryDimensionView] = Field(max_length=100)


class CustomerAnswerLibraryMetaDetailView(StrictModel):
    schema_version: Literal["customer-answer-library-meta-v1"]
    project_pub_id: str = Field(pattern=r"^prj_[A-Za-z0-9_-]{1,116}$")
    snapshot_id: str = Field(pattern=r"^als_[0-9a-f]{24}$")
    snapshot_at: datetime
    meta_query_id: str = Field(pattern=r"^amq_[0-9a-f]{24}$")
    ordinal: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=2_000)
    answer_count: int = Field(ge=0)
    cited_answer_count: int = Field(ge=0)
    citation_count: int = Field(ge=0)
    mentioned_answer_count: int = Field(ge=0)
    latest_capture_time: datetime | None = None
    questions: list[CustomerAnswerLibraryQuestionView] = Field(min_length=1, max_length=500)


class CustomerAnswerLibraryRunView(StrictModel):
    answer_pub_id: str = Field(pattern=r"^ans_[A-Za-z0-9_-]{1,116}$")
    repeat_index: int = Field(ge=1)
    model: str = Field(min_length=1, max_length=120)
    region: str = Field(min_length=1, max_length=120)
    mode: str = Field(min_length=1, max_length=80)
    capture_time: datetime
    analysis_state: Literal["ready", "pending"]
    mentioned: bool | None = None
    rank: int | None = Field(default=None, ge=1)
    sentiment: Literal["positive", "neutral", "negative", "unknown"] | None = None
    recommended: bool | None = None
    citation_count: int = Field(ge=0)


class CustomerAnswerLibraryQuestionRunsView(StrictModel):
    schema_version: Literal["customer-answer-library-runs-v1"]
    project_pub_id: str = Field(pattern=r"^prj_[A-Za-z0-9_-]{1,116}$")
    snapshot_id: str = Field(pattern=r"^als_[0-9a-f]{24}$")
    snapshot_at: datetime
    meta_query_id: str = Field(pattern=r"^amq_[0-9a-f]{24}$")
    meta_query_ordinal: int = Field(ge=1)
    meta_query_label: str = Field(min_length=1, max_length=2_000)
    question: CustomerAnswerLibraryQuestionView
    models: list[CustomerAnswerLibraryDimensionView] = Field(max_length=100)
    regions: list[CustomerAnswerLibraryDimensionView] = Field(max_length=100)
    modes: list[CustomerAnswerLibraryDimensionView] = Field(max_length=100)
    data: list[CustomerAnswerLibraryRunView] = Field(max_length=50)
    page: CustomerAnswerLibraryPageMetaView


class CustomerAnswerLibraryDetailView(StrictModel):
    schema_version: Literal["customer-answer-library-detail-v1"]
    project_pub_id: str = Field(pattern=r"^prj_[A-Za-z0-9_-]{1,116}$")
    snapshot_id: str = Field(pattern=r"^als_[0-9a-f]{24}$")
    snapshot_at: datetime
    meta_query_id: str = Field(pattern=r"^amq_[0-9a-f]{24}$")
    meta_query_ordinal: int = Field(ge=1)
    meta_query_label: str = Field(min_length=1, max_length=2_000)
    question_id: str = Field(pattern=r"^aq_[0-9a-f]{24}$")
    question_ordinal: int = Field(ge=1, le=500)
    variant_label: str = Field(min_length=1, max_length=40)
    question_text: str = Field(min_length=1, max_length=2_000)
    answer: CustomerAnswerLibraryRunView
    response_text: str = Field(max_length=200_000)


class CustomerDashboardView(StrictModel):
    schema_version: Literal["customer-dashboard-v1"]
    metric_version: Literal["customer-metrics-v1"]
    project_pub_id: str = Field(pattern=r"^prj_[A-Za-z0-9_-]{1,116}$")
    brand_name: str = Field(min_length=1, max_length=200)
    state: Literal["ready", "building"]
    generated_at: str
    as_of: str | None
    window: CustomerWindowView
    metrics: list[CustomerMetricView]
    models: list[CustomerDimensionView]
    competitors: list[CustomerCompetitorView]
    questions: list[CustomerQuestionView]
    sources: list[CustomerSourceView]
    regions: list[CustomerDimensionView]
    modes: list[CustomerDimensionView]
    trends: list[CustomerTrendView]
    risk: CustomerRiskView
    source_audit: CustomerSourceAuditView
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
