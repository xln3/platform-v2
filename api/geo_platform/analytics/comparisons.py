"""analytics.run_comparison CRUD 与 run 归属校验（报价单服务 4 显式实体，s06_0016）。

读写走 tenant_connection（analytics schema，RLS ``app.tenant_pub_id`` 谓词）；
run 归属校验走 analytics.service._platform_tenant_connection（platform schema 双
selector 口径，照 fact_suggestions 先例）——所有 run 必须存在且属于本
tenant+project，否则 UnknownRunPubId（API 400 unknown_run_pub_id；跨项目/跨租户
的 run 同样报 unknown，绝不泄露存在性）。

对比计算不在本模块：GET 单体的 result 由 brandrank/compare.py
compute_run_comparison 现场产出（与报告 before_after 扩展组同一份代码）。
"""
from __future__ import annotations

import json
from typing import Any

from psycopg.rows import dict_row

from ..tenancy.ids import new_pub_id
from ..tenancy.psycopg import tenant_connection
from . import service as analytics_service

# 实体对外投影列（不含 tenant_pub_id——行已按租户 RLS 隔离，响应不重复携带）
_ENTITY_COLUMNS = ("pub_id, project_pub_id, name, baseline_run_pub_ids, "
                   "optimized_run_pub_ids, note, created_by, created_at")


class UnknownRunPubId(ValueError):
    """任一 run pub id 不存在或不属于该 tenant+project → API 400 unknown_run_pub_id。"""

    def __init__(self, unknown: list[str]) -> None:
        super().__init__(f"unknown_run_pub_id: {unknown}")
        self.unknown = unknown


def validate_project_runs(
    dsn: str, tenant_pub_id: str, project_pub_id: str, run_pub_ids: list[str]
) -> None:
    """所有 run 必须存在且属于该 tenant+project（platform.collection_run 经
    project join 归属判定），否则 UnknownRunPubId（逐个列出未知 id）。"""
    requested = list(dict.fromkeys(run_pub_ids))
    with analytics_service._platform_tenant_connection(dsn, tenant_pub_id) as connection:
        rows = connection.execute(
            """
            SELECT r.pub_id
            FROM platform.collection_run r
            JOIN platform.project p ON p.id = r.project_id
            WHERE r.tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
              AND p.pub_id = %s
              AND r.pub_id = ANY(%s::text[])
            """,
            (project_pub_id, requested),
        ).fetchall()
    found = {row["pub_id"] for row in rows}
    unknown = [pub_id for pub_id in requested if pub_id not in found]
    if unknown:
        raise UnknownRunPubId(unknown)


def create_comparison(
    dsn: str, tenant_pub_id: str, *, project_pub_id: str, name: str,
    baseline_run_pub_ids: list[str], optimized_run_pub_ids: list[str],
    note: str | None, created_by: str | None,
) -> dict[str, Any]:
    """校验两臂 run 归属后落库（pub_id=new_pub_id("rcmp")），返回实体投影。

    run pub id 形状校验在 API 层（422）；本层只做归属校验（400 语义）。
    """
    validate_project_runs(
        dsn, tenant_pub_id, project_pub_id,
        baseline_run_pub_ids + optimized_run_pub_ids)
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        row = connection.execute(
            f"""
            INSERT INTO analytics.run_comparison
              (pub_id, tenant_pub_id, project_pub_id, name,
               baseline_run_pub_ids, optimized_run_pub_ids, note, created_by)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
            RETURNING {_ENTITY_COLUMNS}
            """,
            (
                new_pub_id("rcmp"), tenant_pub_id, project_pub_id, name,
                json.dumps(baseline_run_pub_ids),
                json.dumps(optimized_run_pub_ids),
                note, created_by,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("run_comparison_insert_empty")  # INSERT...RETURNING 恒有一行
    return dict(row)


def list_comparisons(
    dsn: str, tenant_pub_id: str, project_pub_id: str, limit: int
) -> list[dict[str, Any]]:
    """项目下全部对比实体，created_at 倒序（pub_id 倒序决胜，稳定分页外语义）。"""
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            f"""
            SELECT {_ENTITY_COLUMNS}
            FROM analytics.run_comparison
            WHERE tenant_pub_id=%s AND project_pub_id=%s
            ORDER BY created_at DESC, pub_id DESC
            LIMIT %s
            """,
            (tenant_pub_id, project_pub_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_comparison(
    dsn: str, tenant_pub_id: str, comparison_pub_id: str
) -> dict[str, Any] | None:
    """单体读取；不存在/跨租户 → None（API 404 comparison_not_found）。"""
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        row = connection.execute(
            f"""
            SELECT {_ENTITY_COLUMNS}
            FROM analytics.run_comparison
            WHERE tenant_pub_id=%s AND pub_id=%s
            """,
            (tenant_pub_id, comparison_pub_id),
        ).fetchone()
    return dict(row) if row is not None else None
