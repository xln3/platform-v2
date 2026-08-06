"""W5 查询变体集成测试（dev PG 55433）。

s06_0008 由并行 worker 建设、alembic 链尚不能跑到 s06_0009，本文件用与迁移
逐字一致的 DDL（IF NOT EXISTS）自检表 + RLS 策略，部署时仍以 alembic 为准。
覆盖：种子聚合 → 生成（幂等回放/冲突）→ INV-25 确认门（draft 只出 confirmed）→
状态机 → 零提及闭环（recycled + verified）→ 覆盖率前后对比 → 权限/租户隔离。
"""

from __future__ import annotations

import json
import secrets
import uuid

import pytest
from fastapi.testclient import TestClient
from geo_platform.main import app
from geo_platform.tenancy.database import SessionLocal
from sqlalchemy import text as sql_text

_DDL = """
CREATE TABLE IF NOT EXISTS platform.variant_seed (
  id UUID PRIMARY KEY,
  pub_id VARCHAR(30) NOT NULL,
  tenant_id UUID NOT NULL REFERENCES platform.tenant(id),
  project_id UUID NOT NULL REFERENCES platform.project(id),
  version INTEGER NOT NULL DEFAULT 1,
  text TEXT NOT NULL,
  normalized TEXT NOT NULL,
  source_type VARCHAR(40) NOT NULL,
  source_ref VARCHAR(500) NOT NULL DEFAULT '',
  usage_count INTEGER NOT NULL DEFAULT 1,
  last_seen_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  UNIQUE (project_id, normalized)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_platform_variant_seed_pub_id
  ON platform.variant_seed (pub_id);
CREATE INDEX IF NOT EXISTS ix_platform_variant_seed_tenant_id
  ON platform.variant_seed (tenant_id);
CREATE INDEX IF NOT EXISTS ix_platform_variant_seed_project_id
  ON platform.variant_seed (project_id);
CREATE TABLE IF NOT EXISTS platform.query_variant (
  id UUID PRIMARY KEY,
  pub_id VARCHAR(30) NOT NULL,
  tenant_id UUID NOT NULL REFERENCES platform.tenant(id),
  project_id UUID NOT NULL REFERENCES platform.project(id),
  version INTEGER NOT NULL DEFAULT 1,
  text TEXT NOT NULL,
  normalized TEXT NOT NULL,
  source_type VARCHAR(40) NOT NULL,
  source_ref VARCHAR(500) NOT NULL DEFAULT '',
  intent VARCHAR(20) NOT NULL DEFAULT '未分类',
  audience VARCHAR(80) NOT NULL DEFAULT '通用',
  region VARCHAR(80) NOT NULL DEFAULT '通用',
  product_line VARCHAR(200) NOT NULL DEFAULT '通用',
  marginal_coverage_cell TEXT NOT NULL DEFAULT '{}',
  cluster_id VARCHAR(30),
  cluster_size INTEGER NOT NULL DEFAULT 1,
  verified BOOLEAN NOT NULL DEFAULT FALSE,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  model VARCHAR(120),
  prompt_version VARCHAR(40),
  confirmed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  UNIQUE (project_id, normalized)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_platform_query_variant_pub_id
  ON platform.query_variant (pub_id);
CREATE INDEX IF NOT EXISTS ix_platform_query_variant_tenant_id
  ON platform.query_variant (tenant_id);
CREATE INDEX IF NOT EXISTS ix_platform_query_variant_project_id
  ON platform.query_variant (project_id);
CREATE INDEX IF NOT EXISTS ix_platform_query_variant_status
  ON platform.query_variant (status);
"""

_RLS = """
ALTER TABLE platform.{name} ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.{name} FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON platform.{name};
CREATE POLICY tenant_isolation ON platform.{name}
USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
"""


def _ensure_tables() -> None:
    with SessionLocal() as session:
        for statement in _DDL.split(";"):
            if statement.strip():
                session.execute(sql_text(statement))
        for name in ("variant_seed", "query_variant"):
            for statement in _RLS.format(name=name).split(";"):
                if statement.strip():
                    session.execute(sql_text(statement))
        session.commit()


def _bootstrap(client: TestClient, subject: str) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/api/v2/identity/bootstrap",
        headers={"X-Bootstrap-Secret": "development-bootstrap"},
        json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
    )
    assert response.status_code == 201, response.text
    tenant = str(response.json()["tenant_pub_id"])
    return tenant, {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": subject,
        "X-Actor-Role": "admin",
    }


def _create_member(
    client: TestClient, admin_headers: dict[str, str], role: str
) -> dict[str, str]:
    subject = f"w5-{role}-" + secrets.token_hex(8)
    response = client.post(
        "/api/v2/identity/members",
        headers={**admin_headers, "Idempotency-Key": "member-" + secrets.token_hex(16)},
        json={"subject": subject, "display_name": role.title(), "role": role},
    )
    assert response.status_code == 201, response.text
    return {
        "X-Tenant-Id": admin_headers["X-Tenant-Id"],
        "X-Actor-Id": subject,
        "X-Actor-Role": role,
    }


def _onboard(client: TestClient, headers: dict[str, str]) -> str:
    response = client.post(
        "/api/v2/onboarding",
        headers={**headers, "Idempotency-Key": "onb-" + secrets.token_hex(16)},
        json={
            "customer_name": "中意人寿",
            "project_name": "W5 变体项目",
            "contact_role": "品牌经理",
            "audience": "关注家庭保障的个人消费者群体。",
            "public_statement": "我们提供可独立验证的保险服务。",
            "brand_name": "中意人寿",
            "website": "https://www.example.com",
            "product_name": "重疾险",
            "competitors": ["友邦保险"],
            "prohibited_claim": "不得承诺收益率。",
            "goal": "提升 AI 搜索曝光。",
            "questions": ["上海重疾险推荐有哪些", "重疾险怎么选"],
            "models": ["doubao"],
            "regions": ["上海"],
            "frequency": "weekly",
            "truth_confirmed": True,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["project_pub_id"])


def _tenant_scope(tenant_pub_id: str, project_pub_id: str) -> tuple[str, str, str]:
    """解析 (tenant_id, project_id, config_version_id)；RLS 下先声明租户上下文。"""
    with SessionLocal() as session:
        session.execute(
            sql_text(
                "SELECT set_config('app.tenant_pub_id', :pub, false)"
            ),
            {"pub": tenant_pub_id},
        )
        tenant_id = session.execute(
            sql_text("SELECT id FROM platform.tenant WHERE pub_id = :pub"),
            {"pub": tenant_pub_id},
        ).scalar_one()
        session.execute(
            sql_text("SELECT set_config('app.tenant_id', :tid, false)"),
            {"tid": str(tenant_id)},
        )
        row = session.execute(
            sql_text(
                "SELECT p.id, v.id FROM platform.project p "
                "JOIN platform.monitoring_config c ON c.project_id = p.id "
                "JOIN platform.monitoring_config_version v ON v.config_id = c.id "
                "WHERE p.pub_id = :pub"
            ),
            {"pub": project_pub_id},
        ).one()
        session.rollback()
    return str(tenant_id), str(row[0]), str(row[1])


def _seed_collection(tenant_id: str, project_id: str, config_version_id: str) -> None:
    with SessionLocal() as session:
        session.execute(
            sql_text("SELECT set_config('app.tenant_id', :tid, false)"), {"tid": tenant_id}
        )
        run_id = uuid.uuid4()
        session.execute(
            sql_text(
                "INSERT INTO platform.collection_run "
                "(id, pub_id, tenant_id, project_id, config_version_id, idempotency_key,"
                " workflow_id, state, total_tasks, completed_tasks, failed_tasks, paused,"
                " version, created_at, updated_at) "
                "VALUES (:id, :pub, :tid, :pid, :cvid, :idem, :wf, 'completed', 2, 2, 0,"
                " false, 1, now(), now())"
            ),
            {
                "id": str(run_id),
                "pub": "run_" + secrets.token_hex(8),
                "tid": tenant_id,
                "pid": project_id,
                "cvid": config_version_id,
                "idem": "idem-" + secrets.token_hex(8),
                "wf": "wf-" + secrets.token_hex(12),
            },
        )
        tasks = [
            (
                "k1",
                json.dumps(
                    [
                        {"query": "中意人寿重疾险怎么样", "ordinal": 1},
                        {"query": "上海重疾险怎么选", "ordinal": 2},
                    ],
                    ensure_ascii=False,
                ),
                "可以分情况看。重疾险怎么选？首先看保障范围。哪家保险公司靠谱？这是常见疑问。",
            ),
            ("k2", "[]", None),
        ]
        for business_key, search_queries, answer_text in tasks:
            session.execute(
                sql_text(
                    "INSERT INTO platform.collection_task "
                    "(id, pub_id, tenant_id, run_id, business_key, matrix_json, state,"
                    " attempt_count, search_queries_json, answer_text,"
                    " version, created_at, updated_at) "
                    "VALUES (:id, :pub, :tid, :run, :bk, '{}', 'completed', 1, :sq, :answer,"
                    " 1, now(), now())"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "pub": "tsk_" + secrets.token_hex(8),
                    "tid": tenant_id,
                    "run": str(run_id),
                    "bk": business_key,
                    "sq": search_queries,
                    "answer": answer_text,
                },
            )
        session.commit()


def _seed_analytics(
    tenant_pub_id: str, project_pub_id: str, mentioned_text: str, zero_text: str
) -> None:
    """一条 mentioned=true、一条 mentioned=false 的 answer+analysis（闭环判定数据源）。"""
    with SessionLocal() as session:
        session.execute(
            sql_text("SELECT set_config('app.tenant_pub_id', :pub, false)"),
            {"pub": tenant_pub_id},
        )
        run_pub = "anr_" + secrets.token_hex(8)
        session.execute(
            sql_text(
                "INSERT INTO analytics.analysis_run "
                "(pub_id, tenant_pub_id, input_hash, scorer_version, metric_version,"
                " model_version, status) VALUES (:pub, :tp, :ih, 'v1', 'v1', 'v1', 'ready')"
            ),
            {"pub": run_pub, "tp": tenant_pub_id, "ih": secrets.token_hex(16)},
        )
        for query_text, mentioned in ((mentioned_text, True), (zero_text, False)):
            answer_pub = "ans_" + secrets.token_hex(8)
            session.execute(
                sql_text(
                    "INSERT INTO analytics.answer "
                    "(pub_id, tenant_pub_id, project_pub_id, query_text, response_text,"
                    " model, region, mode, channel, adapter_version, capture_time) "
                    "VALUES (:pub, :tp, :pp, :qt, 'r', 'doubao', '上海', 'web', 'web',"
                    " 'v1', now())"
                ),
                {"pub": answer_pub, "tp": tenant_pub_id, "pp": project_pub_id, "qt": query_text},
            )
            session.execute(
                sql_text(
                    "INSERT INTO analytics.answer_analysis "
                    "(pub_id, tenant_pub_id, answer_pub_id, analysis_run_pub_id, mentioned,"
                    " channel, adapter_version, capture_time) "
                    "VALUES (:pub, :tp, :ap, :rp, :m, 'web', 'v1', now())"
                ),
                {
                    "pub": "ana_" + secrets.token_hex(8),
                    "tp": tenant_pub_id,
                    "ap": answer_pub,
                    "rp": run_pub,
                    "m": mentioned,
                },
            )
        session.commit()


def _generate(
    client: TestClient, headers: dict[str, str], project: str, key: str, body: dict
) -> tuple[int, dict]:
    response = client.post(
        f"/api/v2/projects/{project}/variants/generate",
        headers={**headers, "Idempotency-Key": key},
        json=body,
    )
    return response.status_code, response.json()


@pytest.fixture()
def w5_project() -> dict:
    _ensure_tables()
    client = TestClient(app)
    tenant, admin_headers = _bootstrap(client, "w5-admin-" + secrets.token_hex(6))
    project = _onboard(client, admin_headers)
    tenant_id, project_id, config_version_id = _tenant_scope(tenant, project)
    _seed_collection(tenant_id, project_id, config_version_id)
    return {
        "client": client,
        "tenant": tenant,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "admin": admin_headers,
        "project": project,
    }


def test_generate_list_confirm_draft_and_coverage(w5_project: dict) -> None:
    client = w5_project["client"]
    headers = w5_project["admin"]
    project = w5_project["project"]
    body = {"window_days": None, "use_llm": False, "max_variants": 100}

    status, payload = _generate(client, headers, project, "gen-" + secrets.token_hex(16), body)
    assert status == 201, payload
    assert payload["seeds_upserted"] >= 3  # 2 检索词 + ≥1 回答问句（去重后）
    assert payload["variants_created"] > 0
    assert payload["gap_variants_created"] > 0
    assert payload["llm_note"] == "not_requested"
    before = payload["coverage_before"]
    after = payload["coverage_after"]
    assert after["covered_cells"] > before["covered_cells"]
    assert after["coverage_ratio"] > before["coverage_ratio"]

    # 幂等回放：同 key 同 body → 相同回执；同 key 不同 body → 409。
    key = "gen-" + secrets.token_hex(16)
    first = _generate(client, headers, project, key, body)
    replay = _generate(client, headers, project, key, body)
    assert first[0] == replay[0] == 201
    assert first[1] == replay[1]
    conflict = _generate(client, headers, project, key, {**body, "max_variants": 50})
    assert conflict[0] == 409

    # 变体清单按意图簇分组；检索词种子落真实 source_type/source_ref。
    listing = client.get(
        f"/api/v2/projects/{project}/variants?status=pending", headers=headers
    )
    assert listing.status_code == 200
    groups = {group["intent"]: group["variants"] for group in listing.json()["groups"]}
    all_variants = [v for variants in groups.values() for v in variants]
    by_text = {v["text"]: v for v in all_variants}
    assert "中意人寿重疾险怎么样" in by_text
    seed_variant = by_text["中意人寿重疾险怎么样"]
    assert seed_variant["source_type"] == "search_query"
    assert seed_variant["source_ref"].startswith("tsk_")
    assert seed_variant["intent"] == "口碑"
    # 检索词与池内文本归一化不同 → 真实种子变体（选购意图），且与矩阵模板去重只此一行。
    search_seed = by_text["上海重疾险怎么选"]
    assert search_seed["source_type"] == "search_query"
    assert search_seed["intent"] == "选购"
    assert "重疾险怎么选" not in by_text  # 与现有池完全同文，不重复建
    mined = by_text.get("哪家保险公司靠谱？")
    assert mined is not None and mined["source_type"] == "answer_mining"
    gap_variants = [v for v in all_variants if v["source_type"] == "matrix_gap"]
    assert gap_variants
    assert set(gap_variants[0]["marginal_coverage_cell"]) == {
        "intent",
        "audience",
        "region",
        "product_line",
    }

    # INV-25：未确认时 draft 为空；确认后才可进 config draft。
    draft = client.get(f"/api/v2/projects/{project}/variants/draft", headers=headers)
    assert draft.status_code == 200 and draft.json()["items"] == []
    confirm_ids = [v["pub_id"] for v in all_variants[:5]]
    confirm_key = "cfm-" + secrets.token_hex(16)
    confirmed = client.post(
        f"/api/v2/projects/{project}/variants/confirm",
        headers={**headers, "Idempotency-Key": confirm_key},
        json={"variant_pub_ids": confirm_ids, "decision": "confirmed"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["updated"] == len(confirm_ids)
    replay = client.post(
        f"/api/v2/projects/{project}/variants/confirm",
        headers={**headers, "Idempotency-Key": confirm_key},
        json={"variant_pub_ids": confirm_ids, "decision": "confirmed"},
    )
    assert replay.status_code == 200 and replay.json() == confirmed.json()
    # 状态机：重复确认（新 key）→ 全部 skipped，不二次迁移。
    again = client.post(
        f"/api/v2/projects/{project}/variants/confirm",
        headers={**headers, "Idempotency-Key": "cfm-" + secrets.token_hex(16)},
        json={"variant_pub_ids": confirm_ids, "decision": "confirmed"},
    )
    assert again.json()["updated"] == 0 and again.json()["skipped"] == len(confirm_ids)
    draft = client.get(f"/api/v2/projects/{project}/variants/draft", headers=headers)
    draft_texts = {item["text"] for item in draft.json()["items"]}
    assert draft_texts == {v["text"] for v in all_variants[:5]}

    # 覆盖率端点：变体后覆盖率不低于现有池，空格清单可读。
    coverage = client.get(f"/api/v2/projects/{project}/variants/coverage", headers=headers)
    assert coverage.status_code == 200
    report = coverage.json()
    assert report["existing_pool"]["total_cells"] > 0
    assert report["with_variants"]["coverage_ratio"] >= report["existing_pool"]["coverage_ratio"]
    assert isinstance(report["gaps"], list)


def test_zero_mention_recycle_and_verified(w5_project: dict) -> None:
    client = w5_project["client"]
    headers = w5_project["admin"]
    project = w5_project["project"]
    body = {"window_days": None, "use_llm": False, "max_variants": 100}
    status, _ = _generate(client, headers, project, "gen-" + secrets.token_hex(16), body)
    assert status == 201

    listing = client.get(
        f"/api/v2/projects/{project}/variants?status=pending&limit=1000", headers=headers
    )
    variants = [
        v for group in listing.json()["groups"] for v in group["variants"]
    ]
    by_text = {v["text"]: v for v in variants}
    mentioned_variant = by_text["中意人寿重疾险怎么样"]
    zero_variant = by_text["哪家保险公司靠谱？"]
    client.post(
        f"/api/v2/projects/{project}/variants/confirm",
        headers={**headers, "Idempotency-Key": "cfm-" + secrets.token_hex(16)},
        json={
            "variant_pub_ids": [mentioned_variant["pub_id"], zero_variant["pub_id"]],
            "decision": "confirmed",
        },
    )
    _seed_analytics(
        w5_project["tenant"],
        project,
        mentioned_text=mentioned_variant["text"],
        zero_text=zero_variant["text"],
    )
    status, summary = _generate(client, headers, project, "gen-" + secrets.token_hex(16), body)
    assert status == 201
    assert summary["verified_marked"] >= 1
    assert summary["recycled_zero_mention"] >= 1

    # verified 标落到 confirmed 变体；零提及种子回炉（source_ref 可回溯原变体）。
    confirmed_listing = client.get(
        f"/api/v2/projects/{project}/variants?status=confirmed&limit=1000", headers=headers
    )
    confirmed_variants = {
        v["text"]: v
        for group in confirmed_listing.json()["groups"]
        for v in group["variants"]
    }
    assert confirmed_variants[mentioned_variant["text"]]["verified"] is True
    assert confirmed_variants[zero_variant["text"]]["verified"] is False
    with SessionLocal() as session:
        session.execute(
            sql_text("SELECT set_config('app.tenant_id', :tid, false)"),
            {"tid": w5_project["tenant_id"]},
        )
        row = session.execute(
            sql_text(
                "SELECT source_type, source_ref, usage_count FROM platform.variant_seed "
                "WHERE normalized = '哪家保险公司靠谱' AND project_id = :pid"
            ),
            {"pid": w5_project["project_id"]},
        ).one()
    assert row[0] == "recycled_zero_mention"
    assert row[1] == zero_variant["pub_id"]
    assert row[2] >= 1


def test_permissions_and_tenant_isolation(w5_project: dict) -> None:
    client = w5_project["client"]
    admin_headers = w5_project["admin"]
    project = w5_project["project"]
    analyst = _create_member(client, admin_headers, "analyst")
    denied = client.post(
        f"/api/v2/projects/{project}/variants/generate",
        headers={**analyst, "Idempotency-Key": "gen-" + secrets.token_hex(16)},
        json={"window_days": None, "use_llm": False, "max_variants": 10},
    )
    assert denied.status_code == 403
    allowed = client.get(f"/api/v2/projects/{project}/variants", headers=analyst)
    assert allowed.status_code == 200
    other_tenant, other_admin = _bootstrap(client, "w5-other-" + secrets.token_hex(6))
    invisible = client.get(f"/api/v2/projects/{project}/variants", headers=other_admin)
    assert invisible.status_code == 404
    missing_key = client.post(
        f"/api/v2/projects/{project}/variants/confirm",
        headers=admin_headers,
        json={"variant_pub_ids": ["var_x"], "decision": "confirmed"},
    )
    assert missing_key.status_code == 422
