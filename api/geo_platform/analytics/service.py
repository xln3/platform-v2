from __future__ import annotations

import json
from datetime import UTC, date, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any

import psycopg
from psycopg.rows import dict_row

from domain.evidence.dlp import assert_secret_free
from domain.evidence.provenance import RedactedProvenance
from domain.metrics.core import MetricRegistry
from domain.scoring.analyzer import CitationInput, analyze_answer
from geo_platform.tenancy.ids import new_pub_id


class AnalyticsService:
    def __init__(self, *, dsn: str) -> None:
        self.dsn = dsn

    def analyze_and_persist(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        answer_pub_id: str,
        answer_text: str,
        brand: str,
        competitors: tuple[str, ...],
        citations: tuple[CitationInput, ...],
        dimensions: dict[str, str],
        own_domains: tuple[str, ...],
        provenance: RedactedProvenance,
        scorer_version: str,
        metric_version: str,
        model_version: str,
    ) -> dict[str, Any]:
        assert_secret_free(answer_text)
        result = analyze_answer(
            answer_pub_id=answer_pub_id,
            text=answer_text,
            brand=brand,
            competitors=competitors,
            citations=citations,
            dimensions=dimensions,
            own_domains=own_domains,
        )
        registry = MetricRegistry(metric_version=metric_version, scorer_version=scorer_version)
        metrics = tuple(
            registry.compute(name, [result.fact], filters={})
            for name in (
                "mention_rate",
                "average_rank",
                "top1_rate",
                "top3_rate",
                "top10_rate",
                "citation_coverage",
                "recommendation_rate",
            )
        )
        analysis_run_pub_id = new_pub_id("arun")
        analysis_pub_id = new_pub_id("ana")
        event_id = new_pub_id("evt")
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            connection.execute(
                """
                INSERT INTO analytics.answer
                  (pub_id,tenant_pub_id,project_pub_id,query_pub_id,query_text,response_text,
                   model,region,mode,eligible,degraded,channel,adapter_version,capture_time)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_pub_id,pub_id) DO NOTHING
                """,
                (
                    answer_pub_id,
                    tenant_pub_id,
                    project_pub_id,
                    dimensions.get("question_pub_id"),
                    dimensions.get("query_text"),
                    answer_text,
                    dimensions.get("model", "unknown"),
                    dimensions.get("region", "unknown"),
                    dimensions.get("mode", "unknown"),
                    dimensions.get("eligible", "true").lower() == "true",
                    dimensions.get("degraded", "false").lower() == "true",
                    provenance.channel.value,
                    provenance.adapter_version,
                    provenance.capture_time,
                ),
            )
            existing = connection.execute(
                """
                SELECT pub_id FROM analytics.analysis_run
                WHERE tenant_pub_id=%s AND input_hash=%s AND scorer_version=%s
                  AND metric_version=%s AND model_version=%s
                """,
                (
                    tenant_pub_id,
                    result.input_hash,
                    scorer_version,
                    metric_version,
                    model_version,
                ),
            ).fetchone()
            if existing is not None:
                analysis_run_pub_id = existing["pub_id"]
            else:
                connection.execute(
                    """
                    INSERT INTO analytics.analysis_run
                      (pub_id,tenant_pub_id,input_hash,scorer_version,metric_version,
                       model_version,status,advisory,confidence)
                    VALUES (%s,%s,%s,%s,%s,%s,'ready',false,1)
                    """,
                    (
                        analysis_run_pub_id,
                        tenant_pub_id,
                        result.input_hash,
                        scorer_version,
                        metric_version,
                        model_version,
                    ),
                )
            inserted = connection.execute(
                """
                INSERT INTO analytics.answer_analysis
                  (pub_id,tenant_pub_id,answer_pub_id,analysis_run_pub_id,mentioned,rank,
                   sentiment,recommended,recommendation_state,competitor_ranks,feature_payload,
                   platform_account_pub_id,browser_profile_version_pub_id,session_event_pub_id,
                   channel,authorization_scope,adapter_version,capture_time)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'experimental',%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_pub_id,answer_pub_id,analysis_run_pub_id) DO NOTHING
                RETURNING pub_id
                """,
                (
                    analysis_pub_id,
                    tenant_pub_id,
                    answer_pub_id,
                    analysis_run_pub_id,
                    result.fact.mentioned,
                    result.fact.rank,
                    result.fact.sentiment,
                    result.fact.recommended,
                    json.dumps(dict(result.fact.competitor_ranks)),
                    json.dumps({"dimensions": dict(result.fact.dimensions)}),
                    provenance.platform_account_pub_id,
                    provenance.browser_profile_version_pub_id,
                    provenance.session_event_pub_id,
                    provenance.channel.value,
                    list(provenance.authorization_scope),
                    provenance.adapter_version,
                    provenance.capture_time,
                ),
            ).fetchone()
            for citation in result.citations:
                citation_pub_id = new_pub_id("cit")
                cited_text = (
                    str(citation["cited_text"]) if citation["cited_text"] is not None else None
                )
                connection.execute(
                    """
                    INSERT INTO analytics.citation_fact
                      (pub_id,tenant_pub_id,answer_pub_id,analysis_run_pub_id,ordinal,
                       original_url,canonical_url,host,title,cited_text,own_source,content_hash)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (tenant_pub_id,answer_pub_id,ordinal,analysis_run_pub_id)
                    DO NOTHING
                    """,
                    (
                        citation_pub_id,
                        tenant_pub_id,
                        answer_pub_id,
                        analysis_run_pub_id,
                        citation["ordinal"],
                        citation["original_url"],
                        citation["canonical_url"],
                        citation["host"],
                        citation["title"],
                        cited_text,
                        citation["own_source"],
                        sha256(cited_text.encode()).hexdigest() if cited_text else None,
                    ),
                )
            metric_date = provenance.capture_time.astimezone(UTC).date()
            projected_metrics: list[dict[str, Any]] = []
            for metric in metrics:
                dimensions_json = json.dumps(
                    dimensions, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                dimensions_hash = sha256(dimensions_json.encode()).hexdigest()
                value_sum = (
                    result.fact.rank
                    if metric.metric == "average_rank" and result.fact.rank is not None
                    else None
                )
                contribution_denominator = (
                    1 if metric.metric != "average_rank" or result.fact.rank is not None else 0
                )
                connection.execute(
                    """
                    INSERT INTO analytics.metric_trace
                      (tenant_pub_id,trace_token,metric_name,answer_pub_id,contribution,
                       project_pub_id,metric_date,dimensions,dimensions_hash,numerator,
                       denominator,value_sum,state,metric_version,scorer_version)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (tenant_pub_id,trace_token,answer_pub_id) DO NOTHING
                    """,
                    (
                        tenant_pub_id,
                        metric.trace_token,
                        metric.metric,
                        answer_pub_id,
                        json.dumps(
                            {
                                "numerator": metric.numerator,
                                "denominator": contribution_denominator,
                                "value_sum": value_sum,
                            }
                        ),
                        project_pub_id,
                        metric_date,
                        dimensions_json,
                        dimensions_hash,
                        metric.numerator,
                        contribution_denominator,
                        value_sum,
                        metric.state.value,
                        metric.metric_version,
                        metric.scorer_version,
                    ),
                )
                rollup = connection.execute(
                    """
                    SELECT SUM(numerator)::bigint AS numerator_sum,
                           SUM(denominator)::bigint AS denominator_sum,
                           SUM(value_sum) AS value_sum,
                           bool_or(state='experimental') AS is_experimental
                    FROM analytics.metric_trace
                    WHERE tenant_pub_id=%s AND project_pub_id=%s AND metric_date=%s
                      AND metric_name=%s AND dimensions_hash=%s
                      AND metric_version=%s AND scorer_version=%s
                    """,
                    (
                        tenant_pub_id,
                        project_pub_id,
                        metric_date,
                        metric.metric,
                        dimensions_hash,
                        metric.metric_version,
                        metric.scorer_version,
                    ),
                ).fetchone()
                assert rollup is not None
                rollup_numerator = rollup["numerator_sum"]
                rollup_denominator = rollup["denominator_sum"]
                rollup_value_sum = rollup["value_sum"]
                is_experimental = rollup["is_experimental"]
                if metric.metric == "average_rank":
                    rollup_value = (
                        Decimal(rollup_value_sum) / Decimal(rollup_denominator)
                        if rollup_value_sum is not None and rollup_denominator
                        else None
                    )
                else:
                    rollup_value = (
                        Decimal(rollup_numerator) / Decimal(rollup_denominator)
                        if rollup_numerator is not None and rollup_denominator
                        else None
                    )
                connection.execute(
                    """
                    INSERT INTO analytics.metric_daily
                      (tenant_pub_id,project_pub_id,metric_date,metric_name,dimensions,
                       dimensions_hash,value,numerator,denominator,state,metric_version,
                       scorer_version,trace_token)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (tenant_pub_id,project_pub_id,metric_date,metric_name,
                                 dimensions_hash,metric_version,scorer_version)
                    DO UPDATE SET value=EXCLUDED.value,numerator=EXCLUDED.numerator,
                      denominator=EXCLUDED.denominator,state=EXCLUDED.state,
                      trace_token=EXCLUDED.trace_token,updated_at=now()
                    """,
                    (
                        tenant_pub_id,
                        project_pub_id,
                        metric_date,
                        metric.metric,
                        dimensions_json,
                        dimensions_hash,
                        rollup_value,
                        rollup_numerator,
                        rollup_denominator,
                        "experimental" if is_experimental else metric.state.value,
                        metric.metric_version,
                        metric.scorer_version,
                        metric.trace_token,
                    ),
                )
                projected_metrics.append(
                    {
                        "metric_name": metric.metric,
                        "dimensions_hash": dimensions_hash,
                        "dimensions": dimensions,
                        "value": str(rollup_value) if rollup_value is not None else None,
                        "numerator": rollup_numerator,
                        "denominator": rollup_denominator,
                        "state": "experimental" if is_experimental else metric.state.value,
                        "trace_token": metric.trace_token,
                    }
                )
            if inserted is not None:
                payload = {
                    "project_pub_id": project_pub_id,
                    "answer_pub_id": answer_pub_id,
                    "analysis_pub_id": analysis_pub_id,
                    "analysis_run_pub_id": analysis_run_pub_id,
                    "event_time": provenance.capture_time.isoformat(),
                    "dimensions": dimensions,
                    "mentioned": result.fact.mentioned,
                    "rank": result.fact.rank,
                    "sentiment": result.fact.sentiment,
                    "citation_count": len(result.citations),
                    "scorer_version": scorer_version,
                    "metric_version": metric_version,
                    "input_hash": result.input_hash,
                    "citations": [
                        {
                            "citation_pub_id": f"{answer_pub_id}:{citation['ordinal']}",
                            "canonical_host": citation["host"],
                            "canonical_url": citation["canonical_url"],
                            "content_hash": (
                                sha256(str(citation["cited_text"]).encode()).hexdigest()
                                if citation["cited_text"] is not None
                                else None
                            ),
                            "own_source": citation["own_source"],
                        }
                        for citation in result.citations
                    ],
                    "metrics": projected_metrics,
                }
                connection.execute(
                    """
                    INSERT INTO integration.outbox_event
                      (event_id,tenant_pub_id,event_type,aggregate_pub_id,trace_id,payload,
                       occurred_at)
                    VALUES (%s,%s,'analytics.answer.analyzed',%s,%s,%s,%s)
                    """,
                    (
                        event_id,
                        tenant_pub_id,
                        answer_pub_id,
                        metrics[0].trace_token,
                        json.dumps(payload),
                        provenance.capture_time,
                    ),
                )
        return {
            "analysis_pub_id": analysis_pub_id,
            "analysis_run_pub_id": analysis_run_pub_id,
            "input_hash": result.input_hash,
            "fact": result.fact,
            "citations": result.citations,
            "metrics": metrics,
            "outbox_event_id": event_id if inserted is not None else None,
        }

    def aggregate(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        start: date,
        end: date,
        dimensions: dict[str, str] | None = None,
        include_account_dimension: bool = False,
    ) -> list[dict[str, Any]]:
        if include_account_dimension:
            raise PermissionError("customer aggregates cannot expose account dimensions")
        dimensions = dimensions or {}
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT metric_name,
                       SUM(numerator)::bigint AS numerator,
                       SUM(denominator)::bigint AS denominator,
                       SUM(value * denominator) AS weighted_value_sum,
                       bool_or(state='experimental') AS is_experimental,
                       metric_version,scorer_version,
                       array_agg(DISTINCT trace_token) AS trace_tokens
                FROM analytics.metric_daily
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                  AND metric_date BETWEEN %s AND %s
                  AND dimensions @> %s::jsonb
                GROUP BY metric_name,metric_version,scorer_version
                ORDER BY metric_name
                """,
                (
                    tenant_pub_id,
                    project_pub_id,
                    start,
                    end,
                    json.dumps(dimensions),
                ),
            ).fetchall()
        return [
            {
                **row,
                "value": (
                    Decimal(row["weighted_value_sum"]) / Decimal(row["denominator"])
                    if row["metric_name"] == "average_rank"
                    and row["weighted_value_sum"] is not None
                    and row["denominator"]
                    else (
                        Decimal(row["numerator"]) / Decimal(row["denominator"])
                        if row["numerator"] is not None and row["denominator"]
                        else None
                    )
                ),
                "state": (
                    "experimental"
                    if row["is_experimental"]
                    else ("ready" if row["denominator"] else "insufficient")
                ),
                "filter_hash": sha256(
                    json.dumps(
                        {
                            "project_pub_id": project_pub_id,
                            "start": start.isoformat(),
                            "end": end.isoformat(),
                            "dimensions": dimensions,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
            }
            for row in rows
        ]

    def previous_period_delta(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        start: date,
        end: date,
    ) -> dict[str, dict[str, Decimal | None]]:
        days = (end - start).days + 1
        previous_end = start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=days - 1)
        current = {
            row["metric_name"]: row["value"]
            for row in self.aggregate(
                tenant_pub_id=tenant_pub_id,
                project_pub_id=project_pub_id,
                start=start,
                end=end,
            )
        }
        previous = {
            row["metric_name"]: row["value"]
            for row in self.aggregate(
                tenant_pub_id=tenant_pub_id,
                project_pub_id=project_pub_id,
                start=previous_start,
                end=previous_end,
            )
        }
        return {
            metric: {
                "current": value,
                "previous": previous.get(metric),
                "delta": (
                    value - previous[metric]
                    if value is not None and previous.get(metric) is not None
                    else None
                ),
            }
            for metric, value in current.items()
        }

    def aggregate_competitors(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        start: date,
        end: date,
        dimensions: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        dimensions = dimensions or {}
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                WITH scoped_answers AS (
                  SELECT DISTINCT answer_pub_id
                  FROM analytics.metric_trace
                  WHERE tenant_pub_id=%s AND project_pub_id=%s
                    AND metric_date BETWEEN %s AND %s
                    AND dimensions @> %s::jsonb
                ), latest_analysis AS (
                  SELECT DISTINCT ON (aa.answer_pub_id)
                    aa.answer_pub_id,aa.competitor_ranks
                  FROM analytics.answer_analysis aa
                  JOIN scoped_answers sa ON sa.answer_pub_id=aa.answer_pub_id
                  WHERE aa.tenant_pub_id=%s
                  ORDER BY aa.answer_pub_id,aa.id DESC
                ), total AS (
                  SELECT count(*)::bigint AS answer_count FROM scoped_answers
                )
                SELECT ranks.key AS competitor,
                       count(*)::bigint AS mention_count,
                       total.answer_count,
                       avg((ranks.value)::integer) AS average_rank,
                       count(*) FILTER (WHERE (ranks.value)::integer <= 1)::bigint AS top1_count,
                       count(*) FILTER (WHERE (ranks.value)::integer <= 3)::bigint AS top3_count,
                       count(*) FILTER (WHERE (ranks.value)::integer <= 10)::bigint AS top10_count
                FROM latest_analysis
                CROSS JOIN LATERAL jsonb_each_text(competitor_ranks) AS ranks
                CROSS JOIN total
                GROUP BY ranks.key,total.answer_count
                ORDER BY mention_count DESC,ranks.key
                """,
                (
                    tenant_pub_id,
                    project_pub_id,
                    start,
                    end,
                    json.dumps(dimensions),
                    tenant_pub_id,
                ),
            ).fetchall()
        return [
            {
                **row,
                "mention_rate": (
                    Decimal(row["mention_count"]) / Decimal(row["answer_count"])
                    if row["answer_count"]
                    else None
                ),
                "top1_rate": Decimal(row["top1_count"]) / Decimal(row["answer_count"]),
                "top3_rate": Decimal(row["top3_count"]) / Decimal(row["answer_count"]),
                "top10_rate": Decimal(row["top10_count"]) / Decimal(row["answer_count"]),
                "metric_version": "competitor-aggregation-v1",
            }
            for row in rows
        ]
