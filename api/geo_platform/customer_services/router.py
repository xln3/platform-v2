# ruff: noqa: B008
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from domain.evidence.dlp import assert_secret_free

from ..config import get_settings
from ..identity.policy import Principal, get_principal

router = APIRouter(prefix="/api/v2/customer", tags=["customer-services"])

SERVICE_CATALOG = (
    (1, "ranking_test", "AI 推荐排名效果测试"),
    (2, "outbound_disparagement_audit", "主动拉踩内容核查"),
    (3, "inbound_disparagement_audit", "被拉踩内容核查"),
    (4, "official_site_audit", "官网内容 AI 引用效率分析"),
    (5, "content_publishing_pilot", "内容发布与排名提升试点"),
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CustomerServiceSummary(StrictModel):
    answer_count: int | None = Field(default=None, ge=0)
    official_site_stage: str | None = None
    official_site_u_occurrences: int | None = Field(default=None, ge=0)
    official_site_v_occurrences: int | None = Field(default=None, ge=0)
    official_site_w_occurrences: int | None = Field(default=None, ge=0)
    u_observation: Literal["observed", "partial", "unobserved"] | None = None
    v_observation: Literal["observed", "partial", "unobserved"] | None = None
    w_observation: Literal["observed", "partial", "unobserved", "not_applicable"] | None = None


class CustomerServiceDelivery(StrictModel):
    report_pub_id: str
    report_version_pub_id: str
    title: str
    published_at: datetime
    delivered_at: datetime | None
    confirmed_at: datetime | None


class CustomerServiceView(StrictModel):
    service_number: int = Field(ge=1, le=5)
    service_code: str
    name: str
    entitlement_state: Literal["inactive", "active", "suspended", "expired"]
    catalog_version: str | None
    summary: CustomerServiceSummary | None
    latest_delivery: CustomerServiceDelivery | None


class CustomerServicesView(StrictModel):
    schema_version: Literal["customer-five-services-v1"]
    project_pub_id: str
    services: list[CustomerServiceView] = Field(min_length=5, max_length=5)


def _safe_delivery(row: dict[str, Any] | None) -> CustomerServiceDelivery | None:
    if row is None:
        return None
    title = str(row.get("title") or "")
    try:
        assert_secret_free(title)
    except ValueError:
        # A signed report with a secret-bearing title is not customer safe.  Do
        # not return the unsafe row merely because the frontend could hide it.
        return None
    return CustomerServiceDelivery(
        report_pub_id=str(row["report_pub_id"]),
        report_version_pub_id=str(row["report_version_pub_id"]),
        title=title,
        published_at=row["published_at"],
        delivered_at=row.get("delivered_at"),
        confirmed_at=row.get("confirmed_at"),
    )


def _dsn() -> str:
    settings = get_settings()
    return (settings.runtime_postgres_dsn or settings.postgres_dsn).replace(
        "postgresql+psycopg://", "postgresql://"
    )


def _observation_state(
    *, observed: int, partial: int, unobserved: int
) -> Literal["observed", "partial", "unobserved"]:
    if partial > 0 or (observed > 0 and unobserved > 0):
        return "partial"
    return "observed" if observed > 0 else "unobserved"


def _official_site_stage(
    *,
    u_count: int,
    u_observation: str,
    v_count: int,
    v_observation: str,
    w_count: int,
    w_pending: int,
) -> str:
    if u_observation == "unobserved":
        return "u_unobserved"
    if u_observation == "partial":
        return "u_partially_observed"
    if u_count == 0:
        return "not_in_u"
    if v_observation == "unobserved":
        return "v_unobserved"
    if v_observation == "partial":
        return "v_partially_observed"
    if v_count == 0:
        return "u_not_v"
    if w_pending > 0:
        return "w_pending"
    return "v_not_w" if w_count == 0 else "entered_w"


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


@router.get(
    "/projects/{project_pub_id}/services",
    response_model=CustomerServicesView,
    operation_id="getCustomerFiveServices",
)
def get_customer_services(
    project_pub_id: str,
    response: Response,
    principal: Principal = Depends(get_principal),
) -> CustomerServicesView:
    principal.require("project:read")
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie, Authorization"
    with _connection(principal.tenant_pub_id) as connection:
        project = connection.execute(
            "SELECT id FROM platform.project WHERE pub_id=%s", (project_pub_id,)
        ).fetchone()
        if project is None:
            raise HTTPException(status_code=404, detail={"code": "project_not_found"})
        project_id = str(project["id"])
        entitlement_rows = connection.execute(
            """
            SELECT DISTINCT ON (service_code) service_code,catalog_version,state
            FROM platform.project_service_entitlement
            WHERE project_id=%s
            ORDER BY service_code,updated_at DESC,pub_id DESC
            """,
            (project_id,),
        ).fetchall()
        entitlement_by_code = {str(row["service_code"]): dict(row) for row in entitlement_rows}
        delivery_rows = connection.execute(
            """
            SELECT DISTINCT ON (output.service_number)
                   output.service_number,report.pub_id AS report_pub_id,
                   version.pub_id AS report_version_pub_id,report.title,
                   version.created_at AS published_at,
                   delivery.delivered_at,delivery.confirmed_at
            FROM reporting.formal_report_output output
            JOIN reporting.formal_report_production production
              ON production.pub_id=output.production_pub_id
             AND production.tenant_pub_id=output.tenant_pub_id
            JOIN reporting.report report ON report.pub_id=output.report_pub_id
            JOIN reporting.report_version version
              ON version.pub_id=output.report_version_pub_id
            LEFT JOIN LATERAL (
              SELECT item.delivered_at,item.confirmed_at
              FROM reporting.report_delivery item
              WHERE item.report_pub_id=report.pub_id
              ORDER BY item.delivered_at DESC,item.pub_id DESC LIMIT 1
            ) delivery ON true
            WHERE production.project_pub_id=%s
              AND production.tenant_pub_id=%s
              AND production.status='signed'
              AND report.state='published' AND version.status='published'
            ORDER BY output.service_number,output.created_at DESC,output.pub_id DESC
            """,
            (project_pub_id, principal.tenant_pub_id),
        ).fetchall()
        delivery_by_number = {int(row["service_number"]): dict(row) for row in delivery_rows}
        answer_count_row = connection.execute(
            """
            SELECT count(*) FROM platform.collection_task task
            JOIN platform.collection_run run ON run.id=task.run_id
            WHERE run.project_id=%s AND task.state='completed'
            """,
            (project_id,),
        ).fetchone()
        answer_count = int(answer_count_row[0] if answer_count_row is not None else 0)
        official = connection.execute(
            """
            WITH official_host AS (
              SELECT DISTINCT lower(
                substring(website from '^(?:https?://)?([^/:?#]+)')
              ) AS host
              FROM platform.brand WHERE project_id=%s AND website IS NOT NULL
            )
            SELECT
              count(*) FILTER (WHERE occurrence.u_state='observed')::int AS u_count,
              count(*) FILTER (WHERE occurrence.v_state='entered')::int AS v_count,
              count(*) FILTER (WHERE occurrence.w_state='confirmed')::int AS w_count,
              count(*) FILTER (WHERE event.v_observation='observed')::int AS v_observed,
              count(*) FILTER (WHERE event.v_observation='partial')::int AS v_partial,
              count(*) FILTER (WHERE event.v_observation='unobserved' OR event.id IS NULL)::int
                AS v_unobserved,
              count(*) FILTER (WHERE occurrence.w_state='pending')::int AS w_pending
            FROM platform.answer_source_occurrence occurrence
            JOIN platform.source_url url ON url.id=occurrence.source_url_id
            JOIN platform.source_site site ON site.id=url.site_id
            LEFT JOIN platform.answer_retrieval_event event
              ON event.id=occurrence.retrieval_event_id
            WHERE occurrence.project_id=%s
              AND EXISTS (
                SELECT 1 FROM official_host
                WHERE official_host.host IS NOT NULL
                  AND (site.host=official_host.host
                       OR site.host LIKE '%%.'||official_host.host)
              )
            """,
            (project_id, project_id),
        ).fetchone()
        project_u = connection.execute(
            """
            SELECT
              count(*) FILTER (WHERE u_observation='observed')::int AS observed,
              count(*) FILTER (WHERE u_observation='partial')::int AS partial,
              count(*) FILTER (WHERE u_observation='unobserved')::int AS unobserved
            FROM platform.answer_retrieval_event WHERE project_id=%s
            """,
            (project_id,),
        ).fetchone()
    official_values = (
        dict(official)
        if official is not None
        else {
            "u_count": 0,
            "v_count": 0,
            "w_count": 0,
            "v_observed": 0,
            "v_partial": 0,
            "v_unobserved": 0,
            "w_pending": 0,
        }
    )
    u_count = int(official_values["u_count"] or 0)
    v_count = int(official_values["v_count"] or 0)
    w_count = int(official_values["w_count"] or 0)
    v_observed = int(official_values["v_observed"] or 0)
    v_partial = int(official_values["v_partial"] or 0)
    v_unobserved = int(official_values["v_unobserved"] or 0)
    project_u_values = dict(project_u) if project_u is not None else {}
    project_u_observed = int(project_u_values.get("observed") or 0)
    project_u_partial = int(project_u_values.get("partial") or 0)
    project_u_unobserved = int(project_u_values.get("unobserved") or 0)
    u_observation = _observation_state(
        observed=project_u_observed,
        partial=project_u_partial,
        unobserved=project_u_unobserved,
    )
    v_observation = _observation_state(
        observed=v_observed,
        partial=v_partial,
        unobserved=v_unobserved,
    )
    w_pending = int(official_values["w_pending"] or 0)
    w_observation: Literal["observed", "partial", "unobserved", "not_applicable"] = (
        "unobserved"
        if v_observation == "unobserved"
        else "partial"
        if v_observation == "partial" or w_pending > 0
        else "not_applicable"
        if v_count == 0
        else "observed"
    )
    official_stage = _official_site_stage(
        u_count=u_count,
        u_observation=u_observation,
        v_count=v_count,
        v_observation=v_observation,
        w_count=w_count,
        w_pending=w_pending,
    )
    services: list[CustomerServiceView] = []
    for number, code, name in SERVICE_CATALOG:
        entitlement = entitlement_by_code.get(code)
        state = str(entitlement["state"]) if entitlement is not None else "inactive"
        active = state == "active"
        summary = None
        if active and number == 1:
            summary = CustomerServiceSummary(answer_count=answer_count)
        elif active and number == 4:
            summary = CustomerServiceSummary(
                official_site_stage=official_stage,
                official_site_u_occurrences=(
                    u_count if u_observation == "observed" or u_count > 0 else None
                ),
                official_site_v_occurrences=(
                    v_count if v_observation == "observed" or v_count > 0 else None
                ),
                official_site_w_occurrences=(
                    w_count if w_observation == "observed" or w_count > 0 else None
                ),
                u_observation=u_observation,
                v_observation=v_observation,
                w_observation=w_observation,
            )
        delivery = delivery_by_number.get(number) if active else None
        services.append(
            CustomerServiceView(
                service_number=number,
                service_code=code,
                name=name,
                entitlement_state=state,  # type: ignore[arg-type]
                catalog_version=(str(entitlement["catalog_version"]) if entitlement else None),
                summary=summary,
                latest_delivery=_safe_delivery(delivery),
            )
        )
    return CustomerServicesView(
        schema_version="customer-five-services-v1",
        project_pub_id=project_pub_id,
        services=services,
    )


__all__ = ["router"]
