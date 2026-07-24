# ruff: noqa: B008
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict

from ..config import get_settings
from ..identity.policy import Principal, get_principal
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


def _dsn() -> str:
    return get_settings().postgres_dsn.replace("postgresql+psycopg://", "postgresql://")


@router.get("/answers", response_model=AnswerPage)
def answers(
    project_pub_id: str,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    model: str | None = None,
    region: str | None = None,
    mode: str | None = None,
    principal: Principal = Depends(get_principal),
) -> AnswerPage:
    principal.require("project:read")
    with psycopg.connect(_dsn(), row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT a.pub_id,a.project_pub_id,a.query_pub_id,a.query_text,a.response_text,
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
              AND (%s::text IS NULL OR a.pub_id>%s::text)
              AND (%s::text IS NULL OR a.model=%s::text)
              AND (%s::text IS NULL OR a.region=%s::text)
              AND (%s::text IS NULL OR a.mode=%s::text)
            ORDER BY a.pub_id LIMIT %s
            """,
            (
                principal.tenant_pub_id,
                project_pub_id,
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


@router.get("/trace/{trace_token}")
def trace(
    trace_token: str,
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("project:read")
    with psycopg.connect(_dsn(), row_factory=dict_row) as connection:
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
