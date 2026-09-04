"""POST /analytics/mention-metrics 的数据适配层：项目归属校验 + analytics 只读取数。

口径全部在 domain.reporting.mention_metrics（纯函数，mention-metrics-v1）；本模块
只做三件事：
1) platform.project 归属校验（``_platform_tenant_connection`` 双 selector 口径，
   照 site_audit_suggestions/comparisons 先例；项目不在本租户 → ProjectNotFound，
   跨租户同 404，不泄露存在性）；
2) analytics.answer 抓取（tenant_connection RLS；``eligible`` 过滤与
   ORDER BY capture_time 与重算源脚本逐字一致）；
3) analytics.citation_fact 抓取（按项目 eligible 答案 join；纯函数内部再按保留
   W1 答案过滤，与源脚本 ``WHERE answer_pub_id = ANY(w1_pubs)`` 等价）。
"""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

from domain.reporting.mention_metrics import (
    AnswerRow,
    CitationRow,
    MentionMetricsSpec,
    compute_mention_metrics,
)

from ..tenancy.psycopg import tenant_connection
from . import service as analytics_service


class ProjectNotFound(LookupError):
    """project 在本租户内不存在 → API 404 project_not_found（跨租户同 404，不泄露存在性）。"""


def fetch_answer_rows(dsn: str, tenant_pub_id: str, project_pub_id: str) -> list[AnswerRow]:
    """项目 eligible 答案（capture_time 升序），组装为纯函数输入行。"""
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT a.pub_id, a.run_pub_id, a.model, a.query_text,
                   a.response_plain_text, a.capture_time
            FROM analytics.answer a
            WHERE a.tenant_pub_id=%s AND a.project_pub_id=%s AND a.eligible
            ORDER BY a.capture_time
            """,
            (tenant_pub_id, project_pub_id),
        ).fetchall()
    return [
        AnswerRow(
            pub=str(row["pub_id"]),
            run=str(row["run_pub_id"]) if row["run_pub_id"] is not None else None,
            model=str(row["model"]),
            q=str(row["query_text"]),
            resp=str(row["response_plain_text"] or ""),
            cap=str(row["capture_time"]),
        )
        for row in rows
    ]


def fetch_citation_rows(dsn: str, tenant_pub_id: str, project_pub_id: str) -> list[CitationRow]:
    """项目 eligible 答案的 citation_fact 行（纯函数内再按保留 W1 答案过滤）。"""
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT c.answer_pub_id, c.host, c.canonical_url, c.title, c.cited_text
            FROM analytics.citation_fact c
            JOIN analytics.answer a
              ON a.tenant_pub_id=c.tenant_pub_id AND a.pub_id=c.answer_pub_id
            WHERE c.tenant_pub_id=%s AND a.project_pub_id=%s AND a.eligible
            """,
            (tenant_pub_id, project_pub_id),
        ).fetchall()
    return [
        CitationRow(
            answer_pub_id=str(row["answer_pub_id"]),
            host=str(row["host"]) if row["host"] is not None else None,
            canonical_url=(str(row["canonical_url"]) if row["canonical_url"] is not None else None),
            title=str(row["title"]) if row["title"] is not None else None,
            cited_text=str(row["cited_text"]) if row["cited_text"] is not None else None,
        )
        for row in rows
    ]


def compute_for_project(
    dsn: str,
    tenant_pub_id: str,
    *,
    project_pub_id: str,
    spec: MentionMetricsSpec,
) -> dict[str, Any]:
    """校验项目归属后取数并计算提及指标（mention-metrics-v1）。"""
    with analytics_service._platform_tenant_connection(dsn, tenant_pub_id) as connection:
        project = connection.execute(
            "SELECT id FROM platform.project WHERE pub_id=%s",
            (project_pub_id,),
        ).fetchone()
    if project is None:
        raise ProjectNotFound("project_not_found")
    return compute_mention_metrics(
        spec=spec,
        answers=fetch_answer_rows(dsn, tenant_pub_id, project_pub_id),
        citations=fetch_citation_rows(dsn, tenant_pub_id, project_pub_id),
    )
