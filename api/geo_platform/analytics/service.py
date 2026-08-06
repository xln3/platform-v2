from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
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
from geo_platform.tenancy.psycopg import tenant_connection

# W3 拉踩聚合维度白名单（platform.disparagement_judgment 列名，禁外部拼接）
_DISPARAGEMENT_DIMENSIONS = {
    "target_brand": "target_brand",
    "subject_brand": "subject_brand",
    "platform": "platform",
}


@contextmanager
def _platform_tenant_connection(
    dsn: str, tenant_pub_id: str
) -> Iterator[psycopg.Connection[Any]]:
    """platform schema 读连接：解析 tenant uuid 并置 app.tenant_id + app.tenant_pub_id。

    platform.* 表 RLS 按 app.tenant_id（uuid），tenant_connection 只置 pub_id
    selector，不能直接读 platform schema（W2 loader 同款双 selector 口径）。
    """
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        tenant_row = connection.execute(
            "SELECT id FROM platform.tenant WHERE pub_id=%s", (tenant_pub_id,)
        ).fetchone()
        if tenant_row is None:
            raise LookupError("tenant_not_found")
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true), "
            "set_config('app.tenant_pub_id', %s, true)",
            (str(tenant_row["id"]), tenant_pub_id),
        )
        yield connection


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
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            persisted_answer = connection.execute(
                """
                INSERT INTO analytics.answer
                  (pub_id,tenant_pub_id,project_pub_id,query_pub_id,query_text,response_text,
                   model,region,mode,eligible,degraded,channel,adapter_version,capture_time,
                   run_pub_id,config_version_pub_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_pub_id,pub_id)
                DO UPDATE SET pub_id=analytics.answer.pub_id
                RETURNING project_pub_id,query_pub_id,query_text,response_text,model,region,mode,
                          eligible,degraded,channel,adapter_version,capture_time
                          ,run_pub_id,config_version_pub_id
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
                    dimensions.get("run_pub_id"),
                    dimensions.get("config_version_pub_id"),
                ),
            ).fetchone()
            assert persisted_answer is not None
            expected_answer = (
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
                dimensions.get("run_pub_id"),
                dimensions.get("config_version_pub_id"),
            )
            if tuple(persisted_answer.values()) != expected_answer:
                raise ValueError("answer replay payload drifted")
            analysis_run = connection.execute(
                """
                INSERT INTO analytics.analysis_run
                  (pub_id,tenant_pub_id,input_hash,scorer_version,metric_version,
                   model_version,status,advisory,confidence)
                VALUES (%s,%s,%s,%s,%s,%s,'ready',false,1)
                ON CONFLICT (tenant_pub_id,input_hash,scorer_version,metric_version,model_version)
                DO UPDATE SET pub_id=analytics.analysis_run.pub_id
                RETURNING pub_id
                """,
                (
                    analysis_run_pub_id,
                    tenant_pub_id,
                    result.input_hash,
                    scorer_version,
                    metric_version,
                    model_version,
                ),
            ).fetchone()
            assert analysis_run is not None
            analysis_run_pub_id = analysis_run["pub_id"]
            inserted = connection.execute(
                """
                INSERT INTO analytics.answer_analysis
                  (pub_id,tenant_pub_id,answer_pub_id,analysis_run_pub_id,mentioned,rank,
                   sentiment,recommended,recommendation_state,competitor_ranks,feature_payload,
                   platform_account_pub_id,browser_profile_version_pub_id,session_event_pub_id,
                   channel,authorization_scope,adapter_version,capture_time)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'experimental',%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_pub_id,answer_pub_id,analysis_run_pub_id)
                DO UPDATE SET pub_id=analytics.answer_analysis.pub_id
                RETURNING pub_id,(xmax=0) AS inserted
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
            assert inserted is not None
            analysis_pub_id = inserted["pub_id"]
            if inserted["inserted"]:
                payload = {
                    "project_pub_id": project_pub_id,
                    "answer_pub_id": answer_pub_id,
                    "analysis_pub_id": analysis_pub_id,
                    "analysis_run_pub_id": analysis_run_pub_id,
                    "run_pub_id": dimensions.get("run_pub_id"),
                    "config_version_pub_id": dimensions.get("config_version_pub_id"),
                    "query_pub_id": dimensions.get("question_pub_id"),
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
            persisted_event = connection.execute(
                """
                SELECT event_id FROM integration.outbox_event
                WHERE tenant_pub_id=%s AND event_type='analytics.answer.analyzed'
                  AND payload->>'analysis_pub_id'=%s
                ORDER BY occurred_at,event_id LIMIT 1
                """,
                (tenant_pub_id, analysis_pub_id),
            ).fetchone()
            persisted_event_id: str | None = (
                str(persisted_event["event_id"]) if persisted_event is not None else None
            )
        return {
            "analysis_pub_id": analysis_pub_id,
            "analysis_run_pub_id": analysis_run_pub_id,
            "input_hash": result.input_hash,
            "fact": result.fact,
            "citations": result.citations,
            "metrics": metrics,
            "outbox_event_id": persisted_event_id,
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
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
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
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
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

    def aggregate_disparagement(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        start: date,
        end: date,
        dimension: str,
    ) -> list[dict[str, Any]]:
        """W3 disparagement_rate 聚合：按 品牌(target)/拉踩方(subject)/平台 分组。

        只统计 judgment_status='ok' 的窗级判定（validation_failure 判分已丢弃，
        绝不入分布）；experimental_count 暴露词典兜底行占比，便于消费方区分
        LLM 判定与 experimental 弱判定混口径。
        """
        column = _DISPARAGEMENT_DIMENSIONS.get(dimension)
        if column is None:
            raise ValueError(f"unsupported disparagement dimension: {dimension!r}")
        with _platform_tenant_connection(self.dsn, tenant_pub_id) as connection:
            rows = connection.execute(
                f"""
                SELECT j.{column} AS value,
                       count(*)::bigint AS judgments,
                       count(*) FILTER (WHERE j.disparagement)::bigint
                         AS disparagement_count,
                       count(*) FILTER (WHERE j.attitude='negative')::bigint
                         AS negative_count,
                       count(*) FILTER (WHERE j.attitude='support')::bigint
                         AS support_count,
                       count(*) FILTER (WHERE j.method='dictionary_experimental')::bigint
                         AS experimental_count
                FROM platform.disparagement_judgment j
                JOIN platform.project p ON p.id = j.project_id
                WHERE j.tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                  AND p.pub_id = %s
                  AND j.judgment_status = 'ok'
                  AND j.created_at::date BETWEEN %s AND %s
                GROUP BY j.{column}
                ORDER BY disparagement_count DESC, value
                """,
                (project_pub_id, start, end),
            ).fetchall()
        return [
            {
                **row,
                "disparagement_rate": (
                    Decimal(row["disparagement_count"]) / Decimal(row["judgments"])
                    if row["judgments"]
                    else None
                ),
                "metric_version": "disparagement-aggregation-v1",
            }
            for row in rows
        ]

    def disparagement_cases(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        start: date,
        end: date,
        limit: int,
    ) -> list[dict[str, Any]]:
        """W3 典型案例清单：disparagement=true 按 confidence 降序（证据+出处链接）。

        出处链接：subject_type=source_document 时取 source_document.url；answer
        判定的出处是答案本身（subject_pub_id，即 collection_task/answer pub_id）。
        """
        with _platform_tenant_connection(self.dsn, tenant_pub_id) as connection:
            rows = connection.execute(
                """
                SELECT j.pub_id AS judgment_pub_id, j.subject_type, j.subject_pub_id,
                       j.platform, j.subject_brand, j.target_brand, j.attitude,
                       j.evidence_quote, j.confidence, j.method, j.model,
                       j.prompt_version, j.created_at,
                       d.url AS source_url
                FROM platform.disparagement_judgment j
                JOIN platform.project p ON p.id = j.project_id
                LEFT JOIN platform.source_document d
                  ON d.tenant_id = j.tenant_id AND d.pub_id = j.subject_pub_id
                 AND j.subject_type = 'source_document'
                WHERE j.tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                  AND p.pub_id = %s
                  AND j.judgment_status = 'ok'
                  AND j.disparagement
                  AND j.created_at::date BETWEEN %s AND %s
                ORDER BY j.confidence DESC, j.created_at DESC, j.pub_id
                LIMIT %s
                """,
                (project_pub_id, start, end, limit),
            ).fetchall()
        return [dict(row) for row in rows]
