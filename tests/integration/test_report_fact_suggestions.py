"""report-fact-suggestions 集成测试：真 PG 回环（只打 55433 开发库，严禁对生产库跑）。

覆盖：bootstrap 租户 → API 建项目（brandrank_domain）→ SQL 落 brand/competitor、
analytics.answer、answer_brand_extract（ok/failed）→ 端点 200 四指标草稿
（分组/分母/双分母实测）；未设真源 400 domain_unset；跨租户 404（RLS 不泄露存在性）。
"""
from __future__ import annotations

import json
import os
import secrets
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from geo_platform.main import app

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


def _bootstrap(client: TestClient, subject: str) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/api/v2/identity/bootstrap",
        headers={"X-Bootstrap-Secret": "development-bootstrap"},
        json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
    )
    tenant = str(response.json()["tenant_pub_id"])
    return tenant, {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": subject,
        "X-Actor-Role": "admin",
    }


def _tenant_uuid(tenant_pub_id: str) -> str:
    with psycopg.connect(POSTGRES_DSN) as connection:
        row = connection.execute(
            "SELECT id FROM platform.tenant WHERE pub_id=%s", (tenant_pub_id,)
        ).fetchone()
    assert row is not None
    return str(row[0])


def _seed_brand_competitor(tenant_pub_id: str, project_pub_id: str) -> None:
    """platform.brand/competitor 各一行（RLS 走 app.tenant_id 上下文，照 service 口径）。"""
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true), "
            "set_config('app.tenant_pub_id', %s, true)",
            (_tenant_uuid(tenant_pub_id), tenant_pub_id),
        )
        project = connection.execute(
            "SELECT id, tenant_id FROM platform.project WHERE pub_id=%s", (project_pub_id,)
        ).fetchone()
        assert project is not None
        project_id, tenant_id = str(project[0]), str(project[1])
        for table, name in (("brand", "中意人寿"), ("competitor", "中国平安")):
            connection.execute(
                f"""
                INSERT INTO platform.{table}
                  (id, pub_id, tenant_id, project_id, name, version, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, 1, now(), now())
                """,
                (str(uuid4()), f"{table[:3]}_{uuid4().hex[:24]}", tenant_id, project_id,
                 name),
            )


def _seed_answer(tenant_pub_id: str, project_pub_id: str, pub_id: str, *,
                 model: str, region: str, query: str) -> None:
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_pub_id', %s, true)", (tenant_pub_id,))
        connection.execute(
            """
            INSERT INTO analytics.answer
              (pub_id, tenant_pub_id, project_pub_id, query_text, response_text,
               model, region, mode, channel, adapter_version, capture_time)
            VALUES (%s, %s, %s, %s, 'r', %s, %s, 'normal', 'web', 'v1', now())
            """,
            (pub_id, tenant_pub_id, project_pub_id, query, model, region),
        )


def _seed_extract(tenant_pub_id: str, answer_pub_id: str, brands: list[str],
                  *, status: str = "ok", domain: str = "insurance") -> None:
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_pub_id', %s, true)", (tenant_pub_id,))
        connection.execute(
            """
            INSERT INTO analytics.answer_brand_extract
              (pub_id, tenant_pub_id, answer_pub_id, domain, brands, status, model,
               error, extracted_at)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s, 'm-test',
                    %s, now())
            """,
            (
                f"abx_{uuid4().hex[:26]}", tenant_pub_id, answer_pub_id, domain,
                json.dumps(brands, ensure_ascii=False), status,
                None if status == "ok" else "api_error: timeout",
            ),
        )


def test_report_fact_suggestions_round_trip() -> None:
    client = TestClient(app)
    suffix = secrets.token_hex(5)
    tenant, headers = _bootstrap(client, f"rfs-{suffix}")
    created = client.post(
        "/api/v2/projects",
        headers={**headers, "Idempotency-Key": f"idem-{secrets.token_hex(16)}"},
        json={"name": f"RFS {suffix}", "customer_name": "RFS Customer",
              "brandrank_domain": "insurance"},
    )
    assert created.status_code == 201, created.text
    project = created.json()["pub_id"]
    _seed_brand_competitor(tenant, project)

    # 组A（doubao/北京/q1）：两条 ok；组B（deepseek/上海/q2）：一条 ok + 一条 failed
    for pub_id, model, region, query in (
        ("ans_a1", "doubao", "北京", "保险公司推荐"),
        ("ans_a2", "doubao", "北京", "保险公司推荐"),
        ("ans_b1", "deepseek", "上海", "保险产品对比"),
        ("ans_b2", "deepseek", "上海", "保险产品对比"),
    ):
        _seed_answer(tenant, project, f"{pub_id}_{suffix}", model=model,
                     region=region, query=query)
    _seed_extract(tenant, f"ans_a1_{suffix}", ["中意人寿保险", "中国平安"])
    _seed_extract(tenant, f"ans_a2_{suffix}", ["擎天柱11号", "中国平安"])
    _seed_extract(tenant, f"ans_b1_{suffix}", ["中国人寿"])
    _seed_extract(tenant, f"ans_b2_{suffix}", [], status="failed")

    resp = client.get(
        f"/api/v2/projects/{project}/report-fact-suggestions?window_days=1",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["insufficient"] is False and body["domain"] == "insurance"
    assert body["target_brand"] == "中意人寿" and body["competitors"] == ["中国平安"]
    assert body["coverage"]["n_answers"] == 4
    assert body["coverage"]["n_with_extract"] == 3      # failed 行不算覆盖
    assert body["coverage"]["n_groups"] == 2
    rows = body["fact_rows"]
    assert len(rows) == 2 * (5 + 1)                     # 每组 5 目标行 + 1 竞品行

    group_a = [r for r in rows
               if r["dimensions"] == {"platform": "doubao", "region": "北京",
                                      "query": "保险公司推荐"}]
    by_metric = {r["metric"]: r for r in group_a}
    # 品牌提及率：两条合并归并后均命中（中意人寿保险/擎天柱11号→中意人寿）
    assert by_metric["brand_appearance_rate"]["value"] == 100.0
    assert by_metric["brand_appearance_rate"]["numerator"] == 2
    assert by_metric["brand_appearance_rate"]["denominator"] == 2
    # 排名分布：均 rank1
    assert by_metric["rank_distribution"]["value"] == 1.0
    assert by_metric["rank_distribution"]["extra"]["ranks"] == [1, 1]
    # Top1 双分母成对
    assert by_metric["top1_appearance_rate"]["value"] == 100.0
    assert by_metric["top1_appearance_rate"]["extra"]["of_mentions"] == 100.0
    # 竞品出现率（归并后名）
    assert by_metric["competitor_appearance_rate"]["value"] == 100.0
    assert by_metric["competitor_appearance_rate"]["extra"]["competitor"] == "中国平安"
    # 行公共形状
    row = by_metric["brand_appearance_rate"]
    assert row["source"] == "system_computed" and row["method"] == "brandrank-llm-v1"
    assert row["domain"] == "insurance" and set(row["window"]) == {"start", "end"}

    # 组B：目标零提及诚实 0 值，分母=1（failed 条不进分母）
    group_b = {r["metric"]: r for r in rows if r["dimensions"]["platform"] == "deepseek"}
    assert group_b["brand_appearance_rate"]["value"] == 0.0
    assert group_b["brand_appearance_rate"]["denominator"] == 1
    assert group_b["rank_distribution"]["value"] is None

    # 未设真源 → 400 domain_unset
    created2 = client.post(
        "/api/v2/projects",
        headers={**headers, "Idempotency-Key": f"idem-{secrets.token_hex(16)}"},
        json={"name": f"RFS2 {suffix}", "customer_name": "RFS Customer"},
    )
    assert created2.status_code == 201, created2.text
    resp2 = client.get(
        f"/api/v2/projects/{created2.json()['pub_id']}/report-fact-suggestions",
        headers=headers,
    )
    assert resp2.status_code == 400
    assert resp2.json()["error"]["code"] == "domain_unset"

    # 跨租户 → 404（RLS 不泄露存在性）
    _other, other_headers = _bootstrap(client, f"rfs-other-{suffix}")
    resp3 = client.get(
        f"/api/v2/projects/{project}/report-fact-suggestions", headers=other_headers)
    assert resp3.status_code == 404
    assert resp3.json()["error"]["code"] == "project_not_found"
