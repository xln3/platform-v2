from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

from domain.collection.answer_content import extract_answer_citation_anchors, project_answer_content
from domain.evidence.provenance import RedactedProvenance
from domain.metrics.core import MetricRegistry
from domain.scoring.analyzer import CitationInput, analyze_answer, canonicalize_url
from domain.scoring.eligibility import resolve_measurement_eligibility
from geo_platform.tenancy.ids import new_pub_id
from geo_platform.tenancy.psycopg import tenant_connection

# W3 拉踩聚合维度白名单（platform.disparagement_judgment 列名，禁外部拼接）
_DISPARAGEMENT_DIMENSIONS = {
    "target_brand": "target_brand",
    "subject_brand": "subject_brand",
    "platform": "platform",
}

# W2 信源审计聚合词表（platform.source_audit.dimension / verdict 的既定取值）
_SOURCE_AUDIT_DIMENSIONS = ("transcript", "factual")
_SOURCE_AUDIT_VERDICTS = ("accurate", "inaccurate", "unsupported", "unverifiable")


def _host_from_website(value: object) -> str | None:
    """官网 URL → host（小写、去 scheme/路径/端口）；缺 scheme 裸串按 https 解析。

    urlsplit 的 hostname 已小写且不含端口；无法解析（非串/超长/非法）→ None。
    """
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    candidate = value if "://" in value else f"https://{value}"
    try:
        hostname = urlsplit(candidate).hostname
    except ValueError:
        return None
    return hostname or None


def _is_own_site(host: object, own_site_host: str | None) -> bool:
    """own_site 判定（大小写不敏感）：与官网 host 相同、互为 www./裸域变体、
    或为官网裸域的子域；官网 host 未知（None）时一律 False。"""
    if not isinstance(host, str) or not host or not own_site_host:
        return False
    candidate = host.lower()
    apex = own_site_host[4:] if own_site_host.startswith("www.") else own_site_host
    if not apex:
        return candidate == own_site_host
    return (
        candidate == own_site_host
        or candidate == apex
        or candidate == f"www.{apex}"
        or candidate.endswith(f".{apex}")
    )


# overview/competitors 读路径的 eligible 口径（与 breakdown 的 ``a.eligible`` 过滤一致）：
# 只排除显式标记 ineligible 的 rollup；无标记的历史行（2026-08-08 前写入，
# dimensions 无 eligible 键）继承现状视为 eligible。
_INELIGIBLE_DIMENSIONS_FILTER = '{"eligible":"false"}'


def _metric_daily_lock_key(
    *,
    tenant_pub_id: str,
    project_pub_id: str,
    metric_date: date,
    metric_name: str,
    dimensions_hash: str,
    metric_version: str,
    scorer_version: str,
) -> str:
    """metric_daily 并发 upsert 的 advisory 锁键=唯一约束列的稳定拼接。

    与 ``analytics.metric_daily`` 的 ON CONFLICT 列组逐字对齐；列值内部不含
    分隔符 ``|``（pub_id/date/hash/版本串均受控），拼接无歧义。
    """
    return "|".join(
        (
            tenant_pub_id,
            project_pub_id,
            metric_date.isoformat(),
            metric_name,
            dimensions_hash,
            metric_version,
            scorer_version,
        )
    )


@contextmanager
def _platform_tenant_connection(dsn: str, tenant_pub_id: str) -> Iterator[psycopg.Connection[Any]]:
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


def _table_exists(connection: psycopg.Connection[Any], qualified_name: str) -> bool:
    """契约表存在性探测（to_regclass）：未迁移上线时返回 False，调用方优雅降级。

    不用 try/except UndefinedTable——PG 中语句报错会中止整个事务，污染同一
    连接上的后续查询；to_regclass 对缺失表返回 NULL，无副作用。
    """
    row = connection.execute("SELECT to_regclass(%s) AS reg", (qualified_name,)).fetchone()
    return row is not None and row["reg"] is not None


def _answer_source_metadata(
    connection: psycopg.Connection[Any], answer_pub_id: str
) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT d.pub_id,d.url,d.final_url,d.canonical_url,d.published_at_raw,d.published_at,
               d.published_at_timezone,d.published_at_precision,d.published_at_source,
               d.published_at_confidence,d.published_at_candidates
        FROM evidence.evidence_relation relation
        JOIN platform.source_document d ON d.pub_id=relation.to_pub_id
        WHERE relation.from_pub_id=%s AND relation.relation_type='cited_source_document'
        ORDER BY d.fetched_at DESC,d.pub_id DESC
        """,
        (answer_pub_id,),
    ).fetchall()
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = dict(row)
        for raw_url in (row["url"], row["final_url"], row["canonical_url"]):
            if not raw_url:
                continue
            try:
                key = canonicalize_url(str(raw_url))
            except ValueError:
                continue
            output.setdefault(key, value)
    return output


def _lock_answer_source_metadata(
    connection: psycopg.Connection[Any], tenant_pub_id: str, answer_pub_id: str
) -> None:
    """Serialize citation creation with W2 source-document linking."""

    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"answer-source-metadata:{tenant_pub_id}:{answer_pub_id}",),
    )


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
        # 原始采集原则（2026-08-06 用户拍板）：answer_text 是公开平台输出=测量原料，
        # 原文入分析、零 DLP——营销稿/回答里含 400 电话等数字串属正常（20260810 实证：
        # 此处 assert_secret_free 曾把含手机号的真实答案挡在分析链外）。
        result = analyze_answer(
            answer_pub_id=answer_pub_id,
            text=answer_text,
            brand=brand,
            competitors=competitors,
            citations=citations,
            dimensions=dimensions,
            own_domains=own_domains,
        )
        answer_content = project_answer_content(answer_text, list(result.citations))
        citation_anchors = extract_answer_citation_anchors(answer_text, list(result.citations))
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
        # INV-1（2026-08-08 起）：eligible 不再默认 true——带五元 provenance 的
        # 负载由 measurement_eligible 真实计算；无五元键的历史/重放负载继承现状
        # （eligible 缺省 true、dimensions 不补键，metric dimensions_hash 零漂移）。
        # metric_dimensions 只用于 metric_trace/metric_daily 快照与 outbox 事件
        # payload；analyze_answer 的输入维持调用方原样。
        eligible, degraded, metric_dimensions = resolve_measurement_eligibility(dimensions)
        with _platform_tenant_connection(self.dsn, tenant_pub_id) as connection:
            _lock_answer_source_metadata(connection, tenant_pub_id, answer_pub_id)
            persisted_answer = connection.execute(
                """
                INSERT INTO analytics.answer
                  (pub_id,tenant_pub_id,project_pub_id,query_pub_id,query_text,response_text,
                   model,region,mode,eligible,degraded,channel,adapter_version,capture_time,
                   run_pub_id,config_version_pub_id,response_raw,response_markdown_normalized,
                   response_ast,response_html_sanitized,response_plain_text,response_hash,
                   render_parser_version)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,
                        %s,%s,%s,%s)
                ON CONFLICT (tenant_pub_id,pub_id)
                DO UPDATE SET pub_id=analytics.answer.pub_id
                RETURNING project_pub_id,query_pub_id,query_text,response_text,model,region,mode,
                          eligible,degraded,channel,adapter_version,capture_time
                          ,run_pub_id,config_version_pub_id,response_raw,
                          response_markdown_normalized,response_ast,response_html_sanitized,
                          response_plain_text,response_hash,render_parser_version
                """,
                (
                    answer_pub_id,
                    tenant_pub_id,
                    project_pub_id,
                    dimensions.get("question_pub_id"),
                    dimensions.get("query_text"),
                    answer_content.response_markdown_normalized,
                    dimensions.get("model", "unknown"),
                    dimensions.get("region", "unknown"),
                    dimensions.get("mode", "unknown"),
                    eligible,
                    degraded,
                    provenance.channel.value,
                    provenance.adapter_version,
                    provenance.capture_time,
                    dimensions.get("run_pub_id"),
                    dimensions.get("config_version_pub_id"),
                    answer_content.response_raw,
                    answer_content.response_markdown_normalized,
                    json.dumps(
                        answer_content.response_ast,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    answer_content.response_html_sanitized,
                    answer_content.response_plain_text,
                    answer_content.response_hash,
                    answer_content.render_parser_version,
                ),
            ).fetchone()
            assert persisted_answer is not None
            expected_answer = (
                project_pub_id,
                dimensions.get("question_pub_id"),
                dimensions.get("query_text"),
                answer_content.response_markdown_normalized,
                dimensions.get("model", "unknown"),
                dimensions.get("region", "unknown"),
                dimensions.get("mode", "unknown"),
                eligible,
                degraded,
                provenance.channel.value,
                provenance.adapter_version,
                provenance.capture_time,
                dimensions.get("run_pub_id"),
                dimensions.get("config_version_pub_id"),
                answer_content.response_raw,
                answer_content.response_markdown_normalized,
                answer_content.response_ast,
                answer_content.response_html_sanitized,
                answer_content.response_plain_text,
                answer_content.response_hash,
                answer_content.render_parser_version,
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
            source_metadata = _answer_source_metadata(connection, answer_pub_id)
            for citation in result.citations:
                citation_ordinal = citation["ordinal"]
                if isinstance(citation_ordinal, bool) or not isinstance(citation_ordinal, int):
                    raise ValueError("analyzed citation ordinal is invalid")
                citation_pub_id = new_pub_id("cit")
                cited_text = (
                    str(citation["cited_text"]) if citation["cited_text"] is not None else None
                )
                source = source_metadata.get(str(citation["canonical_url"]))
                candidates = source.get("published_at_candidates", []) if source else []
                persisted_citation = connection.execute(
                    """
                    INSERT INTO analytics.citation_fact
                      (pub_id,tenant_pub_id,answer_pub_id,analysis_run_pub_id,ordinal,
                       platform_ordinal,ordinal_base,original_url,canonical_url,host,title,
                       cited_text,own_source,content_hash,source_document_pub_id,published_at_raw,
                       published_at,published_at_timezone,published_at_precision,
                       published_at_source,published_at_confidence,published_at_candidates)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                            %s,%s::jsonb)
                    ON CONFLICT (tenant_pub_id,answer_pub_id,ordinal,analysis_run_pub_id)
                    DO UPDATE SET pub_id=analytics.citation_fact.pub_id
                    RETURNING pub_id
                    """,
                    (
                        citation_pub_id,
                        tenant_pub_id,
                        answer_pub_id,
                        analysis_run_pub_id,
                        citation_ordinal,
                        citation["platform_ordinal"],
                        citation["ordinal_base"],
                        citation["original_url"],
                        citation["canonical_url"],
                        citation["host"],
                        citation["title"],
                        cited_text,
                        citation["own_source"],
                        sha256(cited_text.encode()).hexdigest() if cited_text else None,
                        source.get("pub_id") if source else None,
                        source.get("published_at_raw") if source else None,
                        source.get("published_at") if source else None,
                        source.get("published_at_timezone") if source else None,
                        source.get("published_at_precision") if source else None,
                        source.get("published_at_source") if source else None,
                        (source.get("published_at_confidence") if source else None) or "unknown",
                        json.dumps(candidates, ensure_ascii=False, separators=(",", ":")),
                    ),
                ).fetchone()
                assert persisted_citation is not None
                citation_pub_id = str(persisted_citation["pub_id"])
                anchor = citation_anchors.get(citation_ordinal) or {
                    "mapping_status": "unmapped",
                    "mapping_basis": None,
                    "answer_text_start": None,
                    "answer_text_end": None,
                    "answer_ast_path": None,
                    "answer_sentence": None,
                }
                relation_pub_id = (
                    "acr_"
                    + sha256(
                        f"{tenant_pub_id}|{answer_pub_id}|{citation_ordinal}".encode()
                    ).hexdigest()[:26]
                )
                connection.execute(
                    """
                    INSERT INTO analytics.answer_citation_relation
                      (pub_id,tenant_pub_id,answer_pub_id,citation_pub_id,ordinal,
                       source_document_pub_id,mapping_status,mapping_basis,
                       answer_text_start,answer_text_end,answer_ast_path,answer_sentence,
                       source_match_status,relation,classifier_version,review_status,
                       first_cited_at,last_cited_at)
                    VALUES
                      (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,
                       'not_checked','unverified','answer-marker-v1','unreviewed',%s,%s)
                    ON CONFLICT (tenant_pub_id,answer_pub_id,ordinal) DO UPDATE SET
                      citation_pub_id=EXCLUDED.citation_pub_id,
                      source_document_pub_id=COALESCE(
                        EXCLUDED.source_document_pub_id,
                        analytics.answer_citation_relation.source_document_pub_id),
                      mapping_status=EXCLUDED.mapping_status,
                      mapping_basis=EXCLUDED.mapping_basis,
                      answer_text_start=EXCLUDED.answer_text_start,
                      answer_text_end=EXCLUDED.answer_text_end,
                      answer_ast_path=EXCLUDED.answer_ast_path,
                      answer_sentence=EXCLUDED.answer_sentence,
                      first_cited_at=LEAST(
                        analytics.answer_citation_relation.first_cited_at,
                        EXCLUDED.first_cited_at),
                      last_cited_at=GREATEST(
                        analytics.answer_citation_relation.last_cited_at,
                        EXCLUDED.last_cited_at),
                      updated_at=now()
                    """,
                    (
                        relation_pub_id,
                        tenant_pub_id,
                        answer_pub_id,
                        citation_pub_id,
                        citation_ordinal,
                        source.get("pub_id") if source else None,
                        anchor["mapping_status"],
                        anchor["mapping_basis"],
                        anchor["answer_text_start"],
                        anchor["answer_text_end"],
                        json.dumps(anchor["answer_ast_path"])
                        if anchor["answer_ast_path"] is not None
                        else None,
                        anchor["answer_sentence"],
                        provenance.capture_time,
                        provenance.capture_time,
                    ),
                )
            metric_date = provenance.capture_time.astimezone(UTC).date()
            projected_metrics: list[dict[str, Any]] = []
            for metric in metrics:
                dimensions_json = json.dumps(
                    metric_dimensions, ensure_ascii=False, sort_keys=True, separators=(",", ":")
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
                # 临界区串行化（2026-08-08）：「插 trace → SUM → upsert metric_daily」
                # 原实现无锁，同 run 多题并发（fanout 每题独立 workflow）时后提交者
                # 用旧快照覆盖先提交者 = 丢更新。按 metric_daily 唯一键取
                # 事务级 advisory 锁：后到者等先到者提交后再 SUM，必见全部 trace。
                # 锁获取顺序 = 固定 metric 迭代顺序（所有事务同序），无死锁。
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (
                        _metric_daily_lock_key(
                            tenant_pub_id=tenant_pub_id,
                            project_pub_id=project_pub_id,
                            metric_date=metric_date,
                            metric_name=metric.metric,
                            dimensions_hash=dimensions_hash,
                            metric_version=metric.metric_version,
                            scorer_version=metric.scorer_version,
                        ),
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
                        "dimensions": metric_dimensions,
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
                    "dimensions": metric_dimensions,
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
        """Deprecated metric_daily aggregation retained for explicit V1 audit."""
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
                  AND NOT (dimensions @> %s::jsonb)
                GROUP BY metric_name,metric_version,scorer_version
                ORDER BY metric_name
                """,
                (
                    tenant_pub_id,
                    project_pub_id,
                    start,
                    end,
                    json.dumps(dimensions),
                    _INELIGIBLE_DIMENSIONS_FILTER,
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
        config_version: str | None = None,
    ) -> dict[str, dict[str, Decimal | None]]:
        """本期窗口 vs 前一等长窗口的四指标 delta（数据源 metric_daily 聚合）。

        ``config_version``（monitoring_config_version 的 pub_id，报价单前后对比
        口径）传入时，两窗口都只统计该冻结配置产出的答案，防止同项目其他配置/
        探针的答案稀释对比。粒度核查结论（2026-08-10）：metric_daily.dimensions
        JSONB 自 INV-1 fanout 起携带 ``config_version_pub_id`` 键（activities/
        collection.py ``_analysis_dimensions`` 盖章），即 metric_daily 本身已按
        配置分桶，无需回退 analytics.answer 实时聚合——复用 ``aggregate`` 的
        ``dimensions @>`` 过滤即可，与不过滤时保持完全同一条聚合代码路径
        （含只排显式 ineligible 的读纪律）。未盖章的历史行（无该键）不匹配
        过滤器 = 如实不计入；不传参时行为与旧实现逐字节一致。
        """
        days = (end - start).days + 1
        previous_end = start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=days - 1)
        dimensions = (
            {"config_version_pub_id": config_version} if config_version is not None else None
        )
        current = {
            row["metric_name"]: row["value"]
            for row in self.aggregate(
                tenant_pub_id=tenant_pub_id,
                project_pub_id=project_pub_id,
                start=start,
                end=end,
                dimensions=dimensions,
            )
        }
        previous = {
            row["metric_name"]: row["value"]
            for row in self.aggregate(
                tenant_pub_id=tenant_pub_id,
                project_pub_id=project_pub_id,
                start=previous_start,
                end=previous_end,
                dimensions=dimensions,
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
        """Deprecated answer_analysis competitor rollup; never used by official V2 routes."""
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
                    AND NOT (dimensions @> %s::jsonb)
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
                    _INELIGIBLE_DIMENSIONS_FILTER,
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

        统计总体只包含项目首个 brand（目标品牌）的判定；历史上已经生成的竞品
        判定也在查询层排除，避免旧数据继续进入客户风险指标。
        只统计 judgment_status='ok' 的窗级判定（validation_failure 判分已丢弃，
        绝不入分布）；experimental_count 暴露词典兜底行占比，便于消费方区分
        LLM 判定与 experimental 弱判定混口径。
        只统计采集侧判定（content_origin='collection'）：己方稿件判定
        （own_content，project_id/run_id 为 NULL）是另一统计总体，不混入比率
        分母（project 内连接本就会排除，此处显式声明口径）。
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
                JOIN LATERAL (
                  SELECT b.name
                  FROM platform.brand b
                  WHERE b.project_id = p.id
                  ORDER BY b.created_at, b.pub_id
                  LIMIT 1
                ) target ON target.name = j.target_brand
                WHERE j.tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                  AND p.pub_id = %s
                  AND j.judgment_status = 'ok'
                  AND j.content_origin = 'collection'
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

        只返回项目目标品牌作为被核查对象的案例；历史竞品案例不外露。
        出处链接：subject_type=source_document 时取 source_document.url；answer
        判定的出处是答案本身（subject_pub_id，即 collection_task/answer pub_id）。
        fact_check：按 judgment_pub_id 左联契约表 T1（platform.disparagement_
        factcheck）最新一行；该表未迁移上线时优雅降级——全部案例 fact_check=None，
        绝不 500。T1 无 tenant 列：只查本页已确认归属本租户/项目的 judgment
        pub_id，租户边界由主查询保证。
        服务 2 只返回本项目采集产生的 AI 回答与公开信源判定，不混入与项目无关的
        内部内容工作流结果。
        """
        with _platform_tenant_connection(self.dsn, tenant_pub_id) as connection:
            rows = connection.execute(
                """
                SELECT j.pub_id AS judgment_pub_id, j.subject_type, j.subject_pub_id,
                       j.platform, j.subject_brand, j.target_brand, j.attitude,
                       j.evidence_quote, j.confidence, j.method, j.model,
                       j.prompt_version, j.created_at, j.content_origin,
                       d.url AS source_url
                FROM platform.disparagement_judgment j
                JOIN platform.project p ON p.id = j.project_id
                JOIN LATERAL (
                  SELECT b.name
                  FROM platform.brand b
                  WHERE b.project_id = p.id
                  ORDER BY b.created_at, b.pub_id
                  LIMIT 1
                ) target ON target.name = j.target_brand
                LEFT JOIN platform.source_document d
                  ON d.tenant_id = j.tenant_id AND d.pub_id = j.subject_pub_id
                 AND j.subject_type = 'source_document'
                WHERE j.tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                  AND p.pub_id = %s
                  AND j.content_origin = 'collection'
                  AND j.judgment_status = 'ok'
                  AND j.disparagement
                  AND j.created_at::date BETWEEN %s AND %s
                ORDER BY j.confidence DESC, j.created_at DESC, j.pub_id
                LIMIT %s
                """,
                (project_pub_id, start, end, limit),
            ).fetchall()
            fact_check_by_judgment: dict[str, dict[str, Any]] = {}
            judgment_ids = [row["judgment_pub_id"] for row in rows]
            if judgment_ids and _table_exists(connection, "platform.disparagement_factcheck"):
                for fact_check in connection.execute(
                    """
                    SELECT DISTINCT ON (f.judgment_pub_id)
                           f.judgment_pub_id, f.verdict, f.summary, f.source_url,
                           f.created_at AS checked_at
                    FROM platform.disparagement_factcheck f
                    WHERE f.judgment_pub_id = ANY(%s::text[])
                    ORDER BY f.judgment_pub_id, f.created_at DESC, f.id DESC
                    """,
                    (judgment_ids,),
                ).fetchall():
                    fact_check_by_judgment[fact_check["judgment_pub_id"]] = dict(fact_check)
        return [
            {
                **dict(row),
                "fact_check": fact_check_by_judgment.get(row["judgment_pub_id"]),
            }
            for row in rows
        ]

    def site_audit_suggestions(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
    ) -> dict[str, Any]:
        """W2 官网内容问题与优化建议：契约表 T2（platform.site_audit_suggestion）最新批次。

        降级纪律：T2 未迁移上线 / 项目不存在（或不可见=非本租户）→ 批次字段全
        None + suggestions=[]，绝不 404/500（与 source_audit_overview 未知项目
        同口径）。租户边界：T2 无 tenant 列，先经 platform.project（RLS 按
        app.tenant_id）确认项目归属本租户，再按 project_pub_id 取数。
        """
        empty: dict[str, Any] = {
            "batch_pub_id": None,
            "generated_at": None,
            "model": None,
            "suggestions": [],
        }
        with _platform_tenant_connection(self.dsn, tenant_pub_id) as connection:
            if not _table_exists(connection, "platform.site_audit_suggestion"):
                return empty
            project = connection.execute(
                "SELECT id FROM platform.project WHERE pub_id=%s",
                (project_pub_id,),
            ).fetchone()
            if project is None:
                return empty
            latest = connection.execute(
                """
                SELECT s.batch_pub_id, s.model, s.created_at
                FROM platform.site_audit_suggestion s
                WHERE s.project_pub_id = %s
                ORDER BY s.created_at DESC, s.id DESC
                LIMIT 1
                """,
                (project_pub_id,),
            ).fetchone()
            if latest is None:
                return empty
            suggestions = connection.execute(
                """
                SELECT s.category, s.severity, s.title, s.detail,
                       s.evidence_document_pub_id
                FROM platform.site_audit_suggestion s
                WHERE s.project_pub_id = %s AND s.batch_pub_id = %s
                ORDER BY CASE s.severity
                           WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2
                           ELSE 3
                         END, s.id
                """,
                (project_pub_id, latest["batch_pub_id"]),
            ).fetchall()
        return {
            "batch_pub_id": latest["batch_pub_id"],
            "generated_at": latest["created_at"],
            "model": latest["model"],
            "suggestions": [dict(row) for row in suggestions],
        }

    def source_audit_overview(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        start: date,
        end: date,
    ) -> dict[str, Any]:
        """W2 信源审计只读聚合（官网引用能效评估的数据面）。

        窗口=source_document.fetched_at::date ∈ [start,end] 且 run 属于该项目
        （join platform.collection_run）。own_site_host=该项目最新确认版本
        （asset_confirmation_version 最大 revision）官网的 host；判定分布只统计
        audit_status='ok' 且 verdict 非 NULL 的行（判分丢弃/未产生的行绝不入
        分布）；同一文档同一口径存在多版本判定（prompt 升版重判）时只取最新
        prompt_version 一行，旧版不重复展示、不重复计数；未知项目按全零 +
        own_site_host=None 如实返回（不 404，与 overview/disparagement 读路径
        同口径）。文本字段在此不清洗，输出清洗（URL/rationale）在 router 层与
        既有端点同款。

        口径分层：
        - 报价单“官网引用率”的分母是窗口内 eligible 且非 degraded
          的 AI 回答，分子是最新分析批次中至少引用一条官网 URL 的回答。
        - source_document 是为正文审计抓取的文档子集，其官网占比只能称为
          “抓取文档官网占比”，不得代替回答口径的引用率。
        - transcript 判定衡量引用转述与源文的一致性，不是“内容采纳率”。
          当系统没有回答级“已理解并用于生成”判定时，采纳率必须为
          None（0/0），不得用 transcript 准确率冒充。
        """
        with _platform_tenant_connection(self.dsn, tenant_pub_id) as connection:
            project = connection.execute(
                "SELECT id FROM platform.project WHERE pub_id=%s",
                (project_pub_id,),
            ).fetchone()
            documents: list[dict[str, Any]] = []
            audits: list[dict[str, Any]] = []
            answer_citations: list[dict[str, Any]] = []
            own_site_host: str | None = None
            if project is not None:
                website = connection.execute(
                    """
                    SELECT website FROM platform.asset_confirmation_version
                    WHERE project_id=%s
                    ORDER BY revision DESC, created_at DESC, pub_id DESC
                    LIMIT %s
                    """,
                    (project["id"], 1),
                ).fetchone()
                if website is not None:
                    own_site_host = _host_from_website(website["website"])
                answer_citations = [
                    dict(row)
                    for row in connection.execute(
                        """
                        WITH eligible_answers AS (
                          SELECT a.pub_id
                          FROM analytics.answer a
                          WHERE a.tenant_pub_id = %s
                            AND a.project_pub_id = %s
                            AND a.capture_time::date BETWEEN %s AND %s
                            AND a.eligible AND NOT a.degraded
                        ), latest_run AS (
                          SELECT DISTINCT ON (c.answer_pub_id)
                                 c.answer_pub_id, c.analysis_run_pub_id
                          FROM analytics.citation_fact c
                          JOIN eligible_answers a ON a.pub_id = c.answer_pub_id
                          WHERE c.tenant_pub_id = %s
                          ORDER BY c.answer_pub_id, c.id DESC
                        )
                        SELECT a.pub_id AS answer_pub_id, c.host, c.cited_text
                        FROM eligible_answers a
                        LEFT JOIN latest_run lr ON lr.answer_pub_id = a.pub_id
                        LEFT JOIN analytics.citation_fact c
                          ON c.tenant_pub_id = %s
                         AND c.answer_pub_id = lr.answer_pub_id
                         AND c.analysis_run_pub_id = lr.analysis_run_pub_id
                        ORDER BY a.pub_id, c.ordinal
                        """,
                        (
                            tenant_pub_id,
                            project_pub_id,
                            start,
                            end,
                            tenant_pub_id,
                            tenant_pub_id,
                        ),
                    ).fetchall()
                ]
                documents = [
                    dict(row)
                    for row in connection.execute(
                        """
                        SELECT d.id, d.pub_id, d.url, d.host, d.final_url, d.http_status,
                               d.extract_status, d.fetched_at
                        FROM platform.source_document d
                        JOIN platform.collection_run r ON r.id = d.run_id
                        WHERE d.tenant_id
                              = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                          AND r.project_id = %s
                          AND d.fetched_at::date BETWEEN %s AND %s
                        ORDER BY d.fetched_at DESC, d.pub_id
                        """,
                        (project["id"], start, end),
                    ).fetchall()
                ]
                audits = (
                    [
                        dict(row)
                        for row in connection.execute(
                            """
                            SELECT DISTINCT ON (a.source_document_id, a.dimension)
                                   a.source_document_id, a.dimension, a.verdict,
                                   a.audit_status, a.rationale
                            FROM platform.source_audit a
                            WHERE a.tenant_id
                                  = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                              AND a.source_document_id = ANY(%s::uuid[])
                            ORDER BY a.source_document_id, a.dimension,
                                     a.prompt_version DESC, a.pub_id
                            """,
                            ([row["id"] for row in documents],),
                        ).fetchall()
                    ]
                    if documents
                    else []
                )
        answer_ids = {row["answer_pub_id"] for row in answer_citations}
        cited_answer_ids = {row["answer_pub_id"] for row in answer_citations if row.get("host")}
        own_site_citation_rows = [
            row for row in answer_citations if _is_own_site(row.get("host"), own_site_host)
        ]
        own_site_answer_ids = {row["answer_pub_id"] for row in own_site_citation_rows}
        own_site_cited_text_answer_ids = {
            row["answer_pub_id"]
            for row in own_site_citation_rows
            if isinstance(row.get("cited_text"), str) and row["cited_text"].strip()
        }
        citation_references_total = sum(1 for row in answer_citations if row.get("host"))
        own_site_citation_references = len(own_site_citation_rows)
        answer_host_accumulator: dict[str, dict[str, Any]] = {}
        for row in answer_citations:
            host = str(row.get("host") or "").strip().lower()
            if not host:
                continue
            entry = answer_host_accumulator.setdefault(
                host,
                {"host": host, "answer_pub_ids": set(), "references": 0},
            )
            entry["answer_pub_ids"].add(str(row["answer_pub_id"]))
            entry["references"] += 1
        answer_hosts = sorted(
            (
                {
                    "host": entry["host"],
                    "answers": len(entry["answer_pub_ids"]),
                    "references": entry["references"],
                    "is_own_site": _is_own_site(entry["host"], own_site_host),
                }
                for entry in answer_host_accumulator.values()
            ),
            key=lambda entry: (-entry["answers"], -entry["references"], entry["host"]),
        )[:20]
        answers_total = len(answer_ids)
        answers_with_citation = len(cited_answer_ids)
        answers_with_own_site_citation = len(own_site_answer_ids)

        documents_total = len(documents)
        own_site_documents = sum(1 for row in documents if _is_own_site(row["host"], own_site_host))
        verdicts: dict[str, dict[str, int]] = {
            dimension: {key: 0 for key in _SOURCE_AUDIT_VERDICTS}
            for dimension in _SOURCE_AUDIT_DIMENSIONS
        }
        for row in audits:
            if row["audit_status"] != "ok" or row["verdict"] is None:
                continue
            bucket = verdicts.get(row["dimension"])
            if bucket is not None and row["verdict"] in bucket:
                bucket[row["verdict"]] += 1
        host_by_document = {row["id"]: row["host"] for row in documents}
        hosts: dict[str, dict[str, Any]] = {}
        for row in documents:
            entry = hosts.setdefault(
                row["host"],
                {
                    "host": row["host"],
                    "documents": 0,
                    "transcript_total": 0,
                    "transcript_accurate": 0,
                },
            )
            entry["documents"] += 1
        # 官网 transcript 准确率：只统计 own_site 文档的 transcript 判定
        # （audit_status='ok'），第三方 host 绝不混入分子分母。这是转述审计，
        # 不是报价单“内容采纳率”。own_site 判定
        # 与 host 榜单/文档明细共用同一 _is_own_site（www/裸域/子域互配）。
        own_site_transcript_total = 0
        own_site_transcript_accurate = 0
        for row in audits:
            if row["audit_status"] != "ok" or row["dimension"] != "transcript":
                continue
            audit_host = host_by_document.get(row["source_document_id"])
            if audit_host is None:
                continue
            hosts[audit_host]["transcript_total"] += 1
            if row["verdict"] == "accurate":
                hosts[audit_host]["transcript_accurate"] += 1
            if _is_own_site(audit_host, own_site_host):
                own_site_transcript_total += 1
                if row["verdict"] == "accurate":
                    own_site_transcript_accurate += 1
        host_list = sorted(hosts.values(), key=lambda item: (-item["documents"], item["host"]))[:20]
        for entry in host_list:
            entry["is_own_site"] = _is_own_site(entry["host"], own_site_host)
        audits_by_document: dict[Any, list[dict[str, Any]]] = {}
        for row in audits:
            audits_by_document.setdefault(row["source_document_id"], []).append(row)
        items = [
            {
                "pub_id": row["pub_id"],
                "url": row["url"],
                "host": row["host"],
                "final_url": row["final_url"],
                "http_status": row["http_status"],
                "extract_status": row["extract_status"],
                "fetched_at": row["fetched_at"],
                "is_own_site": _is_own_site(row["host"], own_site_host),
                "audits": [
                    {
                        "dimension": audit["dimension"],
                        "verdict": audit["verdict"],
                        "audit_status": audit["audit_status"],
                        "rationale": audit["rationale"],
                    }
                    for audit in audits_by_document.get(row["id"], [])
                ],
            }
            for row in documents[:100]
        ]
        return {
            "own_site_host": own_site_host,
            "answers_total": answers_total,
            "answers_with_citation": answers_with_citation,
            "citation_coverage_rate": (
                round(answers_with_citation / answers_total, 4) if answers_total else None
            ),
            "answers_with_own_site_citation": answers_with_own_site_citation,
            "own_site_answer_citation_rate": (
                round(answers_with_own_site_citation / answers_total, 4) if answers_total else None
            ),
            "own_site_share_of_cited_answers": (
                round(answers_with_own_site_citation / answers_with_citation, 4)
                if answers_with_citation
                else None
            ),
            "citation_references_total": citation_references_total,
            "own_site_citation_references": own_site_citation_references,
            "own_site_reference_share": (
                round(own_site_citation_references / citation_references_total, 4)
                if citation_references_total
                else None
            ),
            "own_site_cited_text_answers": len(own_site_cited_text_answer_ids),
            "own_site_cited_text_evidence_rate": (
                round(len(own_site_cited_text_answer_ids) / answers_with_own_site_citation, 4)
                if answers_with_own_site_citation
                else None
            ),
            "documents_total": documents_total,
            "own_site_documents": own_site_documents,
            "own_site_share": (
                round(own_site_documents / documents_total, 4) if documents_total else None
            ),
            "own_site_transcript_total": own_site_transcript_total,
            "own_site_transcript_accurate": own_site_transcript_accurate,
            "own_site_transcript_accuracy_rate": (
                round(own_site_transcript_accurate / own_site_transcript_total, 4)
                if own_site_transcript_total
                else None
            ),
            # 现有 source_audit 没有回答级采纳判定；明确 0/0 + None。
            "own_site_adoption_evaluated_answers": 0,
            "own_site_adoption_verified_answers": 0,
            "own_site_adoption_rate": None,
            "verdicts": verdicts,
            # AI 回答最新分析批次中实际出现的引用网站。与 hosts
            # （下游抓取文档域名）分开，防止前端把抓取子集冒充引用分布。
            "answer_hosts": answer_hosts,
            "hosts": host_list,
            "items": items,
        }
