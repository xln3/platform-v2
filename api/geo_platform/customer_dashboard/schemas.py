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
