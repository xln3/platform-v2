# ruff: noqa: B008
from __future__ import annotations

import base64
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from ..config import get_settings
from ..identity.policy import Principal, get_principal

router = APIRouter(
    prefix="/api/v2/internal/source-intelligence",
    tags=["internal-source-intelligence"],
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CursorPage(StrictModel):
    next_cursor: str | None
    has_more: bool


class SiteSummary(StrictModel):
    site_pub_id: str
    host: str
    distinct_url_count: int = Field(ge=0)
    u_occurrence_count: int = Field(ge=0)
    distinct_answer_count: int = Field(ge=0)
    v_count: int = Field(ge=0)
    w_count: int = Field(ge=0)
    u_observation: Literal["observed", "partial", "unobserved"]
    v_observation: Literal["observed", "partial", "unobserved"]
    w_observation: Literal["observed", "partial", "unobserved"]
    latest_capture_at: datetime


class SitePage(StrictModel):
    schema_version: Literal["internal-source-sites-v1"]
    project_pub_id: str
    data: list[SiteSummary]
    page: CursorPage


class UrlSummary(StrictModel):
    url_pub_id: str
    canonical_url: str
    u_occurrence_count: int = Field(ge=0)
    distinct_answer_count: int = Field(ge=0)
    v_count: int = Field(ge=0)
    w_count: int = Field(ge=0)
    u_observation: Literal["observed", "partial", "unobserved"]
    v_observation: Literal["observed", "partial", "unobserved"]
    w_observation: Literal["observed", "partial", "unobserved"]
    latest_capture_at: datetime
    fetch_state: str
    analysis_state: str


class UrlPage(StrictModel):
    schema_version: Literal["internal-source-urls-v1"]
    project_pub_id: str
    site_pub_id: str
    data: list[UrlSummary]
    page: CursorPage


class SnapshotSummary(StrictModel):
    snapshot_pub_id: str
    state: str
    captured_at: datetime
    text_sha256: str | None
    extractor_version: str | None


class SnapshotDetailSummary(SnapshotSummary):
    final_url: str | None
    http_status: int | None
    title: str | None
    site_name: str | None
    author: str | None
    account_name: str | None
    published_at: datetime | None
    fetch_attempt_pub_id: str | None
    fetch_state: str | None
    fetch_error_code: str | None


class SnapshotPage(StrictModel):
    schema_version: Literal["internal-source-snapshots-v1"]
    project_pub_id: str
    url_pub_id: str
    data: list[SnapshotDetailSummary]
    page: CursorPage


class UrlDetail(StrictModel):
    schema_version: Literal["internal-source-url-detail-v1"]
    project_pub_id: str
    url_pub_id: str
    host: str
    canonical_url: str
    normalization_version: str
    u_occurrence_count: int = Field(ge=0)
    distinct_answer_count: int = Field(ge=0)
    v_count: int = Field(ge=0)
    w_count: int = Field(ge=0)
    u_observation: Literal["observed", "partial", "unobserved"]
    v_observation: Literal["observed", "partial", "unobserved"]
    w_observation: Literal["observed", "partial", "unobserved"]
    fetch_attempt_count: int = Field(ge=0)
    latest_snapshot: SnapshotSummary | None
    page_inspection_count: int = Field(ge=0)
    finding_count: int = Field(ge=0)


class OccurrenceView(StrictModel):
    occurrence_pub_id: str
    answer_pub_id: str
    url_pub_id: str
    canonical_url: str
    host: str
    captured_at: datetime
    platform: str
    model: str
    region: str
    mode: str
    question: str
    query: str | None
    u_state: str
    u_rank: int | None
    v_state: str
    v_open_order: int | None
    final_reference_state: str
    w_state: str
    w_weight: float | None
    evidence_state: str


class OccurrencePage(StrictModel):
    schema_version: Literal["internal-source-occurrences-v1"]
    project_pub_id: str
    url_pub_id: str
    data: list[OccurrenceView]
    page: CursorPage


class AnswerUvwView(StrictModel):
    schema_version: Literal["internal-answer-uvw-v1"]
    project_pub_id: str
    answer_pub_id: str
    question: str
    platform: str
    model: str
    region: str
    mode: str
    capture_time: datetime
    u_observation: str
    v_observation: str
    final_reference_observation: str
    occurrences: list[OccurrenceView]
    occurrences_page: CursorPage


class UrlInspectionSummary(StrictModel):
    inspection_pub_id: str
    source_document_pub_id: str
    status: str
    policy_version: str
    prompt_version: str
    model: str
    content_sha256: str
    finding_count: int = Field(ge=0)
    created_at: datetime


class UrlInspectionPage(StrictModel):
    schema_version: Literal["internal-source-url-inspections-v1"]
    project_pub_id: str
    url_pub_id: str
    data: list[UrlInspectionSummary]
    page: CursorPage


def _dsn() -> str:
    settings = get_settings()
    return (settings.runtime_postgres_dsn or settings.postgres_dsn).replace(
        "postgresql+psycopg://", "postgresql://"
    )


@contextmanager
def _connection(tenant_pub_id: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(_dsn(), row_factory=dict_row) as connection:
        tenant = connection.execute(
            "SELECT id FROM platform.tenant WHERE pub_id=%s", (tenant_pub_id,)
        ).fetchone()
        if tenant is None:
            raise HTTPException(status_code=404, detail={"code": "tenant_not_found"})
        connection.execute(
            "SELECT set_config('app.tenant_id',%s,true),set_config('app.tenant_pub_id',%s,true)",
            (str(tenant["id"]), tenant_pub_id),
        )
        yield connection


def _project_id(connection: psycopg.Connection[Any], project_pub_id: str) -> str:
    row = connection.execute(
        "SELECT id FROM platform.project WHERE pub_id=%s", (project_pub_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"})
    return str(row["id"])


def _offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        raw = cursor.removeprefix("c_")
        padding = "=" * (-len(raw) % 4)
        value = int(base64.urlsafe_b64decode(raw + padding).decode())
    except (ValueError, UnicodeError):
        raise HTTPException(status_code=422, detail={"code": "invalid_cursor"}) from None
    if value < 0:
        raise HTTPException(status_code=422, detail={"code": "invalid_cursor"})
    return value


def _cursor(value: int) -> str:
    encoded = base64.urlsafe_b64encode(str(value).encode()).decode().rstrip("=")
    return f"c_{encoded}"


def _page(rows: list[Any], *, limit: int, offset: int) -> tuple[list[Any], CursorPage]:
    has_more = len(rows) > limit
    visible = rows[:limit]
    return visible, CursorPage(
        next_cursor=_cursor(offset + limit) if has_more else None,
        has_more=has_more,
    )


@router.get("/projects/{project_pub_id}/sites", response_model=SitePage)
def list_sites(
    project_pub_id: str,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> SitePage:
    principal.require("intelligence:read")
    offset = _offset(cursor)
    with _connection(principal.tenant_pub_id) as connection:
        project_id = _project_id(connection, project_pub_id)
        rows = connection.execute(
            """
            SELECT site.pub_id AS site_pub_id,site.host,
                   count(DISTINCT occurrence.source_url_id)::int AS distinct_url_count,
                   count(*) FILTER (WHERE occurrence.u_state='observed')::int
                     AS u_occurrence_count,
                   count(DISTINCT occurrence.answer_task_id)::int AS distinct_answer_count,
                   count(*) FILTER (WHERE occurrence.v_state='entered')::int AS v_count,
                   count(*) FILTER (WHERE occurrence.w_state='confirmed')::int AS w_count,
                   CASE
                     WHEN bool_and(occurrence.u_state='unobserved') THEN 'unobserved'
                     WHEN bool_or(occurrence.u_state='unobserved') THEN 'partial'
                     ELSE 'observed'
                   END AS u_observation,
                   CASE
                     WHEN bool_and(occurrence.v_state='unobserved') THEN 'unobserved'
                     WHEN bool_or(occurrence.v_state='unobserved') THEN 'partial'
                     ELSE 'observed'
                   END AS v_observation,
                   CASE
                     WHEN bool_and(occurrence.w_state='unobserved') THEN 'unobserved'
                     WHEN bool_or(occurrence.w_state IN ('unobserved','pending')) THEN 'partial'
                     ELSE 'observed'
                   END AS w_observation,
                   max(occurrence.captured_at) AS latest_capture_at
            FROM platform.answer_source_occurrence occurrence
            JOIN platform.source_url url ON url.id=occurrence.source_url_id
            JOIN platform.source_site site ON site.id=url.site_id
            WHERE occurrence.project_id=%s
            GROUP BY site.id,site.pub_id,site.host
            ORDER BY distinct_url_count DESC,u_occurrence_count DESC,
                     latest_capture_at DESC,site.host ASC
            LIMIT %s OFFSET %s
            """,
            (project_id, limit + 1, offset),
        ).fetchall()
    visible, page = _page(rows, limit=limit, offset=offset)
    return SitePage(
        schema_version="internal-source-sites-v1",
        project_pub_id=project_pub_id,
        data=[SiteSummary(**dict(row)) for row in visible],
        page=page,
    )


@router.get("/projects/{project_pub_id}/sites/{site_pub_id}/urls", response_model=UrlPage)
def list_site_urls(
    project_pub_id: str,
    site_pub_id: str,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> UrlPage:
    principal.require("intelligence:read")
    offset = _offset(cursor)
    with _connection(principal.tenant_pub_id) as connection:
        project_id = _project_id(connection, project_pub_id)
        rows = connection.execute(
            """
            SELECT url.pub_id AS url_pub_id,url.canonical_url,
                   count(*) FILTER (WHERE occurrence.u_state='observed')::int
                     AS u_occurrence_count,
                   count(DISTINCT occurrence.answer_task_id)::int AS distinct_answer_count,
                   count(*) FILTER (WHERE occurrence.v_state='entered')::int AS v_count,
                   count(*) FILTER (WHERE occurrence.w_state='confirmed')::int AS w_count,
                   CASE
                     WHEN bool_and(occurrence.u_state='unobserved') THEN 'unobserved'
                     WHEN bool_or(occurrence.u_state='unobserved') THEN 'partial'
                     ELSE 'observed'
                   END AS u_observation,
                   CASE
                     WHEN bool_and(occurrence.v_state='unobserved') THEN 'unobserved'
                     WHEN bool_or(occurrence.v_state='unobserved') THEN 'partial'
                     ELSE 'observed'
                   END AS v_observation,
                   CASE
                     WHEN bool_and(occurrence.w_state='unobserved') THEN 'unobserved'
                     WHEN bool_or(occurrence.w_state IN ('unobserved','pending')) THEN 'partial'
                     ELSE 'observed'
                   END AS w_observation,
                   max(occurrence.captured_at) AS latest_capture_at,
                   COALESCE((SELECT attempt.state FROM platform.source_fetch_attempt attempt
                     WHERE attempt.source_url_id=url.id
                     ORDER BY attempt.attempt_ordinal DESC LIMIT 1),'queued') AS fetch_state,
                   CASE
                     WHEN count(*) FILTER (WHERE occurrence.w_state='confirmed')>0
                       THEN 'confirmed'
                     WHEN count(*) FILTER (WHERE occurrence.w_state='pending')>0
                       THEN 'pending'
                     WHEN bool_and(occurrence.w_state='unobserved') THEN 'unobserved'
                     WHEN bool_or(occurrence.w_state='unobserved') THEN 'partial'
                     ELSE 'no_evidence'
                   END AS analysis_state
            FROM platform.source_url url
            JOIN platform.source_site site ON site.id=url.site_id
            JOIN platform.answer_source_occurrence occurrence
              ON occurrence.source_url_id=url.id AND occurrence.project_id=%s
            WHERE site.pub_id=%s
            GROUP BY url.id,url.pub_id,url.canonical_url
            ORDER BY u_occurrence_count DESC,distinct_answer_count DESC,
                     latest_capture_at DESC,url.canonical_url ASC
            LIMIT %s OFFSET %s
            """,
            (project_id, site_pub_id, limit + 1, offset),
        ).fetchall()
    visible, page = _page(rows, limit=limit, offset=offset)
    return UrlPage(
        schema_version="internal-source-urls-v1",
        project_pub_id=project_pub_id,
        site_pub_id=site_pub_id,
        data=[UrlSummary(**dict(row)) for row in visible],
        page=page,
    )


@router.get("/projects/{project_pub_id}/urls/{url_pub_id}", response_model=UrlDetail)
def get_url_detail(
    project_pub_id: str,
    url_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> UrlDetail:
    principal.require("intelligence:read")
    with _connection(principal.tenant_pub_id) as connection:
        project_id = _project_id(connection, project_pub_id)
        row = connection.execute(
            """
            SELECT url.id,url.pub_id AS url_pub_id,site.host,url.canonical_url,
                   url.normalization_version,
                   count(*) FILTER (WHERE occurrence.u_state='observed')::int AS u_occurrence_count,
                   count(DISTINCT occurrence.answer_task_id)::int AS distinct_answer_count,
                   count(*) FILTER (WHERE occurrence.v_state='entered')::int AS v_count,
                   count(*) FILTER (WHERE occurrence.w_state='confirmed')::int AS w_count,
                   CASE
                     WHEN bool_and(occurrence.u_state='unobserved') THEN 'unobserved'
                     WHEN bool_or(occurrence.u_state='unobserved') THEN 'partial'
                     ELSE 'observed'
                   END AS u_observation,
                   CASE
                     WHEN bool_and(occurrence.v_state='unobserved') THEN 'unobserved'
                     WHEN bool_or(occurrence.v_state='unobserved') THEN 'partial'
                     ELSE 'observed'
                   END AS v_observation,
                   CASE
                     WHEN bool_and(occurrence.w_state='unobserved') THEN 'unobserved'
                     WHEN bool_or(occurrence.w_state IN ('unobserved','pending')) THEN 'partial'
                     ELSE 'observed'
                   END AS w_observation,
                   (SELECT count(*)::int FROM platform.source_fetch_attempt attempt
                    WHERE attempt.source_url_id=url.id) AS fetch_attempt_count,
                   (SELECT count(*)::int FROM platform.page_inspection inspection
                    JOIN platform.source_document document
                      ON document.id=inspection.source_document_id
                    WHERE document.source_url_id=url.id) AS page_inspection_count,
                   (SELECT count(*)::int FROM platform.page_inspection_finding finding
                    JOIN platform.page_inspection inspection ON inspection.id=finding.inspection_id
                    JOIN platform.source_document document
                      ON document.id=inspection.source_document_id
                    WHERE document.source_url_id=url.id) AS finding_count
            FROM platform.source_url url
            JOIN platform.source_site site ON site.id=url.site_id
            JOIN platform.answer_source_occurrence occurrence
              ON occurrence.source_url_id=url.id AND occurrence.project_id=%s
            WHERE url.pub_id=%s
            GROUP BY url.id,url.pub_id,site.host,url.canonical_url,url.normalization_version
            """,
            (project_id, url_pub_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "source_url_not_found"})
        snapshot = connection.execute(
            """
            SELECT pub_id AS snapshot_pub_id,snapshot_state AS state,captured_at,
                   text_sha256,extractor_version
            FROM platform.source_page_snapshot WHERE source_url_id=%s
            ORDER BY captured_at DESC,pub_id DESC LIMIT 1
            """,
            (row["id"],),
        ).fetchone()
    payload = {key: value for key, value in dict(row).items() if key != "id"}
    return UrlDetail(
        schema_version="internal-source-url-detail-v1",
        project_pub_id=project_pub_id,
        latest_snapshot=SnapshotSummary(**dict(snapshot)) if snapshot else None,
        **payload,
    )


@router.get(
    "/projects/{project_pub_id}/urls/{url_pub_id}/snapshots",
    response_model=SnapshotPage,
)
def list_url_snapshots(
    project_pub_id: str,
    url_pub_id: str,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> SnapshotPage:
    principal.require("intelligence:read")
    offset = _offset(cursor)
    with _connection(principal.tenant_pub_id) as connection:
        project_id = _project_id(connection, project_pub_id)
        rows = connection.execute(
            """
            SELECT snapshot.pub_id AS snapshot_pub_id,snapshot.snapshot_state AS state,
                   snapshot.captured_at,snapshot.text_sha256,snapshot.extractor_version,
                   snapshot.final_url,snapshot.http_status,snapshot.title,snapshot.site_name,
                   snapshot.author,snapshot.account_name,snapshot.published_at,
                   attempt.pub_id AS fetch_attempt_pub_id,attempt.state AS fetch_state,
                   attempt.error_code AS fetch_error_code
            FROM platform.source_page_snapshot snapshot
            JOIN platform.source_url url ON url.id=snapshot.source_url_id
            LEFT JOIN platform.source_fetch_attempt attempt ON attempt.id=snapshot.fetch_attempt_id
            WHERE url.pub_id=%s AND EXISTS (
              SELECT 1 FROM platform.answer_source_occurrence occurrence
              WHERE occurrence.source_url_id=url.id AND occurrence.project_id=%s
            )
            ORDER BY snapshot.captured_at DESC,snapshot.pub_id DESC
            LIMIT %s OFFSET %s
            """,
            (url_pub_id, project_id, limit + 1, offset),
        ).fetchall()
    visible, page = _page(rows, limit=limit, offset=offset)
    return SnapshotPage(
        schema_version="internal-source-snapshots-v1",
        project_pub_id=project_pub_id,
        url_pub_id=url_pub_id,
        data=[SnapshotDetailSummary(**dict(row)) for row in visible],
        page=page,
    )


@router.get(
    "/projects/{project_pub_id}/urls/{url_pub_id}/inspections",
    response_model=UrlInspectionPage,
)
def list_url_inspections(
    project_pub_id: str,
    url_pub_id: str,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> UrlInspectionPage:
    principal.require("intelligence:read")
    offset = _offset(cursor)
    with _connection(principal.tenant_pub_id) as connection:
        project_id = _project_id(connection, project_pub_id)
        rows = connection.execute(
            """
            SELECT inspection.pub_id AS inspection_pub_id,
                   document.pub_id AS source_document_pub_id,inspection.status,
                   inspection.policy_version,inspection.prompt_version,inspection.model,
                   inspection.content_sha256,
                   (SELECT count(*)::int FROM platform.page_inspection_finding finding
                    WHERE finding.inspection_id=inspection.id) AS finding_count,
                   inspection.created_at
            FROM platform.page_inspection inspection
            JOIN platform.source_document document ON document.id=inspection.source_document_id
            JOIN platform.source_url url ON url.id=document.source_url_id
            WHERE inspection.project_id=%s AND url.pub_id=%s
            ORDER BY inspection.created_at DESC,inspection.pub_id DESC
            LIMIT %s OFFSET %s
            """,
            (project_id, url_pub_id, limit + 1, offset),
        ).fetchall()
    visible, page = _page(rows, limit=limit, offset=offset)
    return UrlInspectionPage(
        schema_version="internal-source-url-inspections-v1",
        project_pub_id=project_pub_id,
        url_pub_id=url_pub_id,
        data=[UrlInspectionSummary(**dict(row)) for row in visible],
        page=page,
    )


_OCCURRENCE_SELECT = """
    SELECT occurrence.pub_id AS occurrence_pub_id,task.pub_id AS answer_pub_id,
           url.pub_id AS url_pub_id,url.canonical_url,site.host,
           occurrence.captured_at,COALESCE(matrix.value->>'adapter','') AS platform,
           COALESCE(matrix.value->>'model','') AS model,
           COALESCE(matrix.value->>'region','') AS region,
           COALESCE(matrix.value->>'mode','') AS mode,
           COALESCE(matrix.value->>'query','') AS question,occurrence.query_text AS query,
           occurrence.u_state,occurrence.u_rank,occurrence.v_state,occurrence.v_open_order,
           occurrence.final_reference_state,occurrence.w_state,
           (SELECT max(chunk.contribution_score)
            FROM platform.weighted_content_chunk chunk
            WHERE chunk.analysis_id=(
              SELECT analysis.id FROM platform.content_contribution_analysis analysis
              WHERE analysis.occurrence_id=occurrence.id
              ORDER BY analysis.created_at DESC,analysis.pub_id DESC LIMIT 1
            ) AND chunk.verification_state='exact'
              AND chunk.review_state<>'rejected') AS w_weight,
           CASE WHEN occurrence.evidence_pub_id IS NOT NULL THEN 'linked'
                ELSE 'unobserved' END AS evidence_state
    FROM platform.answer_source_occurrence occurrence
    JOIN platform.collection_task task ON task.id=occurrence.answer_task_id
    JOIN platform.source_url url ON url.id=occurrence.source_url_id
    JOIN platform.source_site site ON site.id=url.site_id
    CROSS JOIN LATERAL (SELECT task.matrix_json::jsonb AS value) matrix
"""


@router.get(
    "/projects/{project_pub_id}/urls/{url_pub_id}/occurrences",
    response_model=OccurrencePage,
)
def list_url_occurrences(
    project_pub_id: str,
    url_pub_id: str,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> OccurrencePage:
    principal.require("intelligence:read")
    offset = _offset(cursor)
    with _connection(principal.tenant_pub_id) as connection:
        project_id = _project_id(connection, project_pub_id)
        rows = connection.execute(
            _OCCURRENCE_SELECT
            + """
            WHERE occurrence.project_id=%s AND url.pub_id=%s
            ORDER BY occurrence.captured_at DESC,occurrence.pub_id DESC
            LIMIT %s OFFSET %s
            """,
            (project_id, url_pub_id, limit + 1, offset),
        ).fetchall()
    visible, page = _page(rows, limit=limit, offset=offset)
    return OccurrencePage(
        schema_version="internal-source-occurrences-v1",
        project_pub_id=project_pub_id,
        url_pub_id=url_pub_id,
        data=[OccurrenceView(**dict(row)) for row in visible],
        page=page,
    )


@router.get(
    "/projects/{project_pub_id}/answers/{answer_pub_id}/uvw",
    response_model=AnswerUvwView,
)
def get_answer_uvw(
    project_pub_id: str,
    answer_pub_id: str,
    cursor: str | None = None,
    limit: int = Query(default=100, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> AnswerUvwView:
    principal.require("intelligence:read")
    offset = _offset(cursor)
    with _connection(principal.tenant_pub_id) as connection:
        project_id = _project_id(connection, project_pub_id)
        answer = connection.execute(
            """
            SELECT task.id,task.pub_id,task.created_at,task.matrix_json::jsonb AS matrix,
                   CASE
                     WHEN count(event.id)=0 OR bool_and(event.u_observation='unobserved')
                       THEN 'unobserved'
                     WHEN bool_and(event.u_observation='observed') THEN 'observed'
                     ELSE 'partial'
                   END AS u_observation,
                   CASE
                     WHEN count(event.id)=0 OR bool_and(event.v_observation='unobserved')
                       THEN 'unobserved'
                     WHEN bool_and(event.v_observation='observed') THEN 'observed'
                     ELSE 'partial'
                   END AS v_observation,
                   CASE
                     WHEN count(event.id)=0
                       OR bool_and(event.final_reference_observation='unobserved')
                       THEN 'unobserved'
                     WHEN bool_and(event.final_reference_observation='observed') THEN 'observed'
                     ELSE 'partial'
                   END AS final_reference_observation
            FROM platform.collection_task task
            JOIN platform.collection_run run ON run.id=task.run_id
            LEFT JOIN platform.answer_retrieval_event event ON event.answer_task_id=task.id
            WHERE task.pub_id=%s AND run.project_id=%s
            GROUP BY task.id,task.pub_id,task.created_at,task.matrix_json
            """,
            (answer_pub_id, project_id),
        ).fetchone()
        if answer is None:
            raise HTTPException(status_code=404, detail={"code": "answer_not_found"})
        rows = connection.execute(
            _OCCURRENCE_SELECT
            + """
            WHERE occurrence.answer_task_id=%s
            ORDER BY occurrence.occurrence_ordinal
            LIMIT %s OFFSET %s
            """,
            (answer["id"], limit + 1, offset),
        ).fetchall()
    visible, page = _page(rows, limit=limit, offset=offset)
    matrix = answer["matrix"] if isinstance(answer["matrix"], dict) else {}
    return AnswerUvwView(
        schema_version="internal-answer-uvw-v1",
        project_pub_id=project_pub_id,
        answer_pub_id=answer_pub_id,
        question=str(matrix.get("query") or ""),
        platform=str(matrix.get("adapter") or ""),
        model=str(matrix.get("model") or ""),
        region=str(matrix.get("region") or ""),
        mode=str(matrix.get("mode") or ""),
        capture_time=answer["created_at"],
        u_observation=str(answer["u_observation"]),
        v_observation=str(answer["v_observation"]),
        final_reference_observation=str(answer["final_reference_observation"]),
        occurrences=[OccurrenceView(**dict(row)) for row in visible],
        occurrences_page=page,
    )


__all__ = ["router"]
