from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

from domain.metrics.customer import (
    CustomerAnswerFact,
    CustomerCitationFact,
    CustomerRiskFact,
    CustomerSourceAuditFact,
    assert_customer_projection_safe,
    build_customer_metric_bundle,
    infer_competitor_mentions,
    infer_recommendation,
    is_own_site,
    own_site_host,
)


@contextmanager
def _customer_connection(dsn: str, tenant_pub_id: str) -> Iterator[psycopg.Connection[Any]]:
    """Open one read transaction with both analytics and platform RLS selectors."""

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        tenant = connection.execute(
            "SELECT id FROM platform.tenant WHERE pub_id=%s", (tenant_pub_id,)
        ).fetchone()
        if tenant is None:
            raise LookupError("tenant_not_found")
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true), "
            "set_config('app.tenant_pub_id', %s, true)",
            (str(tenant["id"]), tenant_pub_id),
        )
        yield connection


def _safe_competitor_ranks(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        key: rank
        for key, rank in value.items()
        if isinstance(key, str)
        and 0 < len(key) <= 200
        and isinstance(rank, int)
        and not isinstance(rank, bool)
        and 1 <= rank <= 10_000
    }


class CustomerDashboardService:
    def __init__(self, *, dsn: str) -> None:
        self.dsn = dsn

    def dashboard(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        start: date,
        end: date,
        model: str | None = None,
        region: str | None = None,
        mode: str | None = None,
        generated_at: datetime | None = None,
    ) -> dict[str, Any]:
        with _customer_connection(self.dsn, tenant_pub_id) as connection:
            project = connection.execute(
                """
                SELECT p.id,p.name,
                       b.name AS brand_name,b.website AS brand_website,
                       acv.website AS confirmed_website
                FROM platform.project p
                LEFT JOIN LATERAL (
                  SELECT name,website FROM platform.brand
                  WHERE project_id=p.id
                  ORDER BY updated_at DESC,pub_id DESC LIMIT 1
                ) b ON true
                LEFT JOIN LATERAL (
                  SELECT website FROM platform.asset_confirmation_version
                  WHERE project_id=p.id
                  ORDER BY revision DESC,created_at DESC,pub_id DESC LIMIT 1
                ) acv ON true
                WHERE p.pub_id=%s
                """,
                (project_pub_id,),
            ).fetchone()
            if project is None:
                raise LookupError("project_not_found")

            brand_name = str(project["brand_name"] or project["name"]).strip() or "未命名品牌"
            official_host = own_site_host(project["confirmed_website"] or project["brand_website"])
            competitors = [
                str(row["name"])
                for row in connection.execute(
                    """
                    SELECT name FROM platform.competitor
                    WHERE project_id=%s
                    ORDER BY updated_at DESC,pub_id
                    """,
                    (project["id"],),
                ).fetchall()
                if str(row["name"]).strip()
            ]
            query_rows = connection.execute(
                """
                SELECT qi.pub_id,qi.text,qg.name AS group_name
                FROM platform.query_item qi
                JOIN platform.query_group qg ON qg.id=qi.group_id
                WHERE qg.project_id=%s
                ORDER BY qg.name,qi.priority,qi.pub_id
                """,
                (project["id"],),
            ).fetchall()
            query_lookup = {
                str(row["pub_id"]): (str(row["text"]), str(row["group_name"])) for row in query_rows
            }

            answer_rows = connection.execute(
                """
                SELECT a.pub_id,a.capture_time,a.model,a.region,a.mode,
                       a.query_pub_id,a.query_text,a.response_text,
                       aa.analysis_run_pub_id,aa.mentioned,aa.rank,aa.sentiment,
                       aa.recommended,aa.competitor_ranks
                FROM analytics.answer a
                JOIN LATERAL (
                  SELECT analysis_run_pub_id,mentioned,rank,sentiment,recommended,competitor_ranks
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
                ORDER BY a.capture_time,a.pub_id
                """,
                (
                    tenant_pub_id,
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
            answer_ids = [str(row["pub_id"]) for row in answer_rows]
            latest_analysis = {
                str(row["pub_id"]): str(row["analysis_run_pub_id"]) for row in answer_rows
            }
            citation_rows = (
                connection.execute(
                    """
                    SELECT answer_pub_id,analysis_run_pub_id,ordinal,canonical_url,host,
                           title,cited_text,own_source
                    FROM analytics.citation_fact
                    WHERE tenant_pub_id=%s AND answer_pub_id=ANY(%s::text[])
                    ORDER BY answer_pub_id,ordinal,id
                    """,
                    (tenant_pub_id, answer_ids),
                ).fetchall()
                if answer_ids
                else []
            )
            citations: dict[str, list[CustomerCitationFact]] = defaultdict(list)
            for row in citation_rows:
                answer_pub_id = str(row["answer_pub_id"])
                if str(row["analysis_run_pub_id"]) != latest_analysis.get(answer_pub_id):
                    continue
                host = str(row["host"] or "").strip().lower()
                if not host:
                    continue
                citations[answer_pub_id].append(
                    CustomerCitationFact(
                        canonical_url=str(row["canonical_url"]),
                        host=host,
                        own_source=bool(row["own_source"]) or is_own_site(host, official_host),
                        cited_text=row["cited_text"],
                        title=row["title"],
                    )
                )

            document_rows = connection.execute(
                """
                SELECT id,host FROM platform.source_document
                WHERE project_id=%s AND fetched_at::date BETWEEN %s AND %s
                ORDER BY fetched_at DESC,pub_id
                """,
                (project["id"], start, end),
            ).fetchall()
            document_hosts = {row["id"]: str(row["host"] or "").lower() for row in document_rows}
            audit_rows = (
                connection.execute(
                    """
                    SELECT DISTINCT ON (source_document_id,dimension)
                           source_document_id,dimension,verdict,audit_status
                    FROM platform.source_audit
                    WHERE source_document_id=ANY(%s::uuid[])
                    ORDER BY source_document_id,dimension,prompt_version DESC,pub_id DESC
                    """,
                    ([row["id"] for row in document_rows],),
                ).fetchall()
                if document_rows
                else []
            )
            source_audits = tuple(
                CustomerSourceAuditFact(
                    host=document_hosts.get(row["source_document_id"], ""),
                    dimension=str(row["dimension"]),
                    verdict=str(row["verdict"]) if row["verdict"] is not None else None,
                    audit_status=str(row["audit_status"]),
                    own_source=is_own_site(
                        document_hosts.get(row["source_document_id"], ""), official_host
                    ),
                )
                for row in audit_rows
            )

            risk_rows = connection.execute(
                """
                SELECT platform,subject_brand,target_brand,attitude,disparagement,
                       confidence,created_at
                FROM platform.disparagement_judgment
                WHERE project_id=%s AND created_at::date BETWEEN %s AND %s
                  AND judgment_status='ok' AND disparagement IS NOT NULL
                ORDER BY created_at,pub_id
                """,
                (project["id"], start, end),
            ).fetchall()

        answer_facts: list[CustomerAnswerFact] = []
        for row in answer_rows:
            query_pub_id = str(row["query_pub_id"]) if row["query_pub_id"] else None
            query_definition = query_lookup.get(query_pub_id or "")
            response_text = str(row["response_text"])
            query_text = (
                query_definition[0]
                if query_definition
                else (str(row["query_text"]) if row["query_text"] else None)
            )
            rank = int(row["rank"]) if row["rank"] is not None else None
            mentioned = bool(row["mentioned"])
            recommended = (
                bool(row["recommended"])
                if row["recommended"] is not None
                else infer_recommendation(
                    query_text=query_text,
                    response_text=response_text,
                    brand_name=brand_name,
                    mentioned=mentioned,
                    rank=rank,
                )
            )
            answer_facts.append(
                CustomerAnswerFact(
                    answer_pub_id=str(row["pub_id"]),
                    capture_time=row["capture_time"],
                    model=str(row["model"]),
                    region=str(row["region"]),
                    mode=str(row["mode"]),
                    query_pub_id=query_pub_id,
                    query_text=query_text,
                    query_group=query_definition[1] if query_definition else None,
                    response_text=response_text,
                    mentioned=mentioned,
                    rank=rank,
                    sentiment=str(row["sentiment"]) if row["sentiment"] is not None else None,
                    recommended=recommended,
                    competitor_mentions=infer_competitor_mentions(response_text, competitors),
                    competitor_ranks=_safe_competitor_ranks(row["competitor_ranks"]),
                    citations=tuple(citations.get(str(row["pub_id"]), ())),
                )
            )
        risk_facts = tuple(
            CustomerRiskFact(
                model=str(row["platform"] or "未知平台"),
                subject_brand=str(row["subject_brand"] or ""),
                target_brand=str(row["target_brand"]),
                attitude=str(row["attitude"] or "neutral"),
                disparagement=bool(row["disparagement"]),
                confidence=(
                    Decimal(str(row["confidence"])) if row["confidence"] is not None else None
                ),
                created_at=row["created_at"],
            )
            for row in risk_rows
            if str(row["target_brand"]).casefold() == brand_name.casefold()
        )
        filters = {
            key: value
            for key, value in {"model": model, "region": region, "mode": mode}.items()
            if value is not None
        }
        bundle = build_customer_metric_bundle(
            project_pub_id=project_pub_id,
            brand_name=brand_name,
            competitor_names=competitors,
            answers=answer_facts,
            source_audits=source_audits,
            risks=risk_facts,
            generated_at=generated_at or datetime.now(UTC),
            window_start=start,
            window_end=end,
            filters=filters,
        )
        assert_customer_projection_safe(bundle)
        return bundle

    def answer_page(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        start: date,
        end: date,
        search: str | None = None,
        model: str | None = None,
        region: str | None = None,
        mode: str | None = None,
        mentioned: bool | None = None,
        sentiment: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return customer facts from eligible answers and their latest analysis."""

        normalized_search = search.strip() if search and search.strip() else None
        with _customer_connection(self.dsn, tenant_pub_id) as connection:
            project = connection.execute(
                """
                SELECT p.id,p.name,b.name AS brand_name
                FROM platform.project p
                LEFT JOIN LATERAL (
                  SELECT name FROM platform.brand
                  WHERE project_id=p.id
                  ORDER BY updated_at DESC,pub_id DESC LIMIT 1
                ) b ON true
                WHERE p.pub_id=%s
                """,
                (project_pub_id,),
            ).fetchone()
            if project is None:
                raise LookupError("project_not_found")
            brand_name = str(project["brand_name"] or project["name"]).strip() or "未命名品牌"

            common_parameters = (
                project["id"],
                tenant_pub_id,
                project_pub_id,
                start,
                end,
                model,
                model,
                region,
                region,
                mode,
                mode,
                mentioned,
                mentioned,
                sentiment,
                sentiment,
                normalized_search,
                normalized_search,
                normalized_search,
            )
            answer_relation = """
                WITH query_definition AS (
                  SELECT qi.pub_id,qi.text
                  FROM platform.query_item qi
                  JOIN platform.query_group qg ON qg.id=qi.group_id
                  WHERE qg.project_id=%s
                ), customer_answer AS (
                  SELECT a.pub_id,a.query_pub_id,
                         COALESCE(NULLIF(BTRIM(a.query_text),''),qd.text) AS query_text,
                         a.response_text,a.model,a.region,a.mode,a.capture_time,
                         aa.analysis_run_pub_id,aa.mentioned,aa.rank,aa.sentiment,aa.recommended
                  FROM analytics.answer a
                  JOIN LATERAL (
                    SELECT analysis_run_pub_id,mentioned,rank,sentiment,recommended
                    FROM analytics.answer_analysis
                    WHERE tenant_pub_id=a.tenant_pub_id AND answer_pub_id=a.pub_id
                    ORDER BY created_at DESC,id DESC LIMIT 1
                  ) aa ON true
                  LEFT JOIN query_definition qd ON qd.pub_id=a.query_pub_id
                  WHERE a.tenant_pub_id=%s AND a.project_pub_id=%s
                    AND a.capture_time::date BETWEEN %s AND %s
                    AND a.eligible
                    AND (%s::text IS NULL OR a.model=%s::text)
                    AND (%s::text IS NULL OR a.region=%s::text)
                    AND (%s::text IS NULL OR a.mode=%s::text)
                    AND (%s::boolean IS NULL OR aa.mentioned=%s::boolean)
                    AND (%s::text IS NULL OR aa.sentiment=%s::text)
                    AND (
                      %s::text IS NULL
                      OR STRPOS(LOWER(COALESCE(NULLIF(BTRIM(a.query_text),''),qd.text,'')),
                                LOWER(%s::text)) > 0
                      OR STRPOS(LOWER(a.response_text),LOWER(%s::text)) > 0
                    )
                )
            """
            count_row = connection.execute(
                answer_relation + "SELECT count(*) AS total FROM customer_answer",
                common_parameters,
            ).fetchone()
            total = int(count_row["total"]) if count_row is not None else 0
            rows = connection.execute(
                answer_relation
                + """
                SELECT ca.*,
                       (SELECT count(*)
                        FROM analytics.citation_fact cf
                        WHERE cf.tenant_pub_id=%s
                          AND cf.answer_pub_id=ca.pub_id
                          AND cf.analysis_run_pub_id=ca.analysis_run_pub_id) AS citation_count
                FROM customer_answer ca
                ORDER BY ca.capture_time DESC,ca.pub_id DESC
                OFFSET %s LIMIT %s
                """,
                common_parameters + (tenant_pub_id, offset, limit),
            ).fetchall()

        data: list[dict[str, Any]] = []
        for row in rows:
            query_text = str(row["query_text"]) if row["query_text"] is not None else None
            response_text = str(row["response_text"] or "")
            rank = int(row["rank"]) if row["rank"] is not None else None
            mentioned_value = bool(row["mentioned"])
            recommended = (
                bool(row["recommended"])
                if row["recommended"] is not None
                else infer_recommendation(
                    query_text=query_text,
                    response_text=response_text,
                    brand_name=brand_name,
                    mentioned=mentioned_value,
                    rank=rank,
                )
            )
            data.append(
                {
                    "answer_pub_id": str(row["pub_id"]),
                    "query_pub_id": (
                        str(row["query_pub_id"]) if row["query_pub_id"] is not None else None
                    ),
                    "query_text": query_text,
                    "response_text": response_text,
                    "model": str(row["model"]),
                    "region": str(row["region"]),
                    "mode": str(row["mode"]),
                    "capture_time": row["capture_time"],
                    "mentioned": mentioned_value,
                    "rank": rank,
                    "sentiment": (str(row["sentiment"]) if row["sentiment"] is not None else None),
                    "recommended": recommended,
                    "citation_count": int(row["citation_count"] or 0),
                }
            )
        document = {
            "schema_version": "customer-answer-page-v1",
            "project_pub_id": project_pub_id,
            "data": data,
            "page": {
                "total": total,
                "offset": offset,
                "limit": limit,
                "has_more": offset + len(data) < total,
            },
        }
        assert_customer_projection_safe(document)
        return document
