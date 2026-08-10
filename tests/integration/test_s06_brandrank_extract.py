"""s06_0014 集成测试（W3）：analytics.answer_brand_extract 表/RLS/幂等 +
platform.project.brandrank_domain API 读写回环。

只打 55433 开发库（S02_POSTGRES_DSN 缺省值），严禁对生产库跑。
"""
from __future__ import annotations

import os
import secrets
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from geo_platform.main import app

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)

_UPSERT = """
INSERT INTO analytics.answer_brand_extract
  (pub_id,tenant_pub_id,answer_pub_id,domain,brands,status,model,error,extracted_at)
VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,now())
ON CONFLICT (tenant_pub_id,answer_pub_id,domain) DO UPDATE SET
  brands=EXCLUDED.brands, status=EXCLUDED.status, model=EXCLUDED.model,
  error=EXCLUDED.error, extracted_at=EXCLUDED.extracted_at
"""


def _insert(tenant: str, answer: str, domain: str, brands: list[str],
            status: str = "ok", model: str = "m-test", error: str | None = None) -> None:
    import json

    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_pub_id', %s, true)", (tenant,))
        connection.execute(
            _UPSERT,
            (f"abx_{uuid4().hex[:26]}", tenant, answer, domain,
             json.dumps(brands, ensure_ascii=False), status, model, error),
        )


def test_table_idempotent_upsert_and_domain_unset_marker() -> None:
    """同 (tenant,answer,domain) 重抽覆盖旧行（重放安全）；'' 占位行与真 domain 行并存。"""
    suffix = uuid4().hex
    tenant = f"tnt_abx_{suffix}"
    answer = f"ans_abx_{suffix}"
    _insert(tenant, answer, "insurance", ["中意人寿"])
    _insert(tenant, answer, "insurance", ["中意人寿", "中国平安"], model="m-v2")
    _insert(tenant, answer, "", [], status="failed", model="", error="domain_unset")
    _insert(tenant, answer, "", [], status="failed", model="", error="domain_unset")
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_pub_id', %s, true)", (tenant,))
        rows = connection.execute(
            """
            SELECT domain, brands, status, model, error
            FROM analytics.answer_brand_extract
            WHERE tenant_pub_id=%s AND answer_pub_id=%s
            ORDER BY domain
            """,
            (tenant, answer),
        ).fetchall()
    assert len(rows) == 2                                   # 重抽未重复落行
    assert rows[0][0] == "" and rows[0][2] == "failed"      # domain_unset 占位行
    assert rows[0][4] == "domain_unset"
    assert rows[1][0] == "insurance" and rows[1][2] == "ok"
    assert rows[1][1] == ["中意人寿", "中国平安"]             # ON CONFLICT 覆盖生效
    assert rows[1][3] == "m-v2"


def test_table_status_check_constraint() -> None:
    """status 词表外值被拒（CHECK 硬约束，诚实两态 ok/failed）。"""
    suffix = uuid4().hex
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(f"tnt_abx_{suffix}", f"ans_abx_{suffix}", "insurance", [],
                status="partial")


def test_table_tenant_rls_isolation() -> None:
    """RLS FORCE：探测角色跨租户不可见、错租户写入被 WITH CHECK 拒绝。"""
    suffix = uuid4().hex
    role = f"s06_abx_probe_{suffix[:20]}"
    tenant_a = f"tnt_abx_a_{suffix}"
    tenant_b = f"tnt_abx_b_{suffix}"
    _insert(tenant_a, f"ans_{suffix}", "insurance", ["Acme"])
    with psycopg.connect(POSTGRES_DSN) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
                cursor.execute(f'GRANT USAGE ON SCHEMA analytics TO "{role}"')
                cursor.execute(
                    f'GRANT SELECT, INSERT, UPDATE ON analytics.answer_brand_extract TO "{role}"')
                cursor.execute(
                    f'GRANT USAGE, SELECT ON SEQUENCE '
                    f'analytics.answer_brand_extract_id_seq TO "{role}"')
                cursor.execute(f'SET LOCAL ROLE "{role}"')
                # 未设租户上下文 → 零行
                cursor.execute(
                    "SELECT count(*) FROM analytics.answer_brand_extract")
                assert cursor.fetchone() == (0,)
                # 设为租户 B → 看不到租户 A 的行
                cursor.execute(
                    "SELECT set_config('app.tenant_pub_id', %s, true)", (tenant_b,))
                cursor.execute(
                    "SELECT count(*) FROM analytics.answer_brand_extract "
                    "WHERE tenant_pub_id=%s",
                    (tenant_a,),
                )
                assert cursor.fetchone() == (0,)
                # 以租户 B 上下文写租户 A 的行 → WITH CHECK 拒绝
                with pytest.raises(psycopg.errors.InsufficientPrivilege) as excinfo:
                    cursor.execute(
                        _UPSERT,
                        (f"abx_{uuid4().hex[:26]}", tenant_a, f"ans_x_{suffix}",
                         "insurance", "[]", "ok", "m", None),
                    )
                assert "row-level security" in str(excinfo.value).lower(
                ) or "row violates" in str(excinfo.value).lower()


# ── platform.project.brandrank_domain：API 读写回环 ─────────────────────────
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


def test_project_brandrank_domain_create_patch_clear_and_400() -> None:
    client = TestClient(app)
    subject = "abx-" + secrets.token_hex(5)
    _tenant, headers = _bootstrap(client, subject)
    idem = {"Idempotency-Key": "idem-" + secrets.token_hex(16)}

    # 创建即带真源
    created = client.post(
        "/api/v2/projects", headers=headers | idem,
        json={"name": "ABX Project", "customer_name": "ABX Customer",
              "brandrank_domain": "insurance"},
    )
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["brandrank_domain"] == "insurance"

    # 读路径（list）带回字段
    listed = client.get("/api/v2/projects", headers=headers)
    assert listed.status_code == 200
    mine = [p for p in listed.json()["data"] if p["pub_id"] == project["pub_id"]]
    assert mine and mine[0]["brandrank_domain"] == "insurance"

    # patch 改真源（乐观锁 version=1）
    patched = client.patch(
        f"/api/v2/projects/{project['pub_id']}", headers=headers,
        json={"brandrank_domain": "legal", "expected_version": 1},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["brandrank_domain"] == "legal"

    # patch 显式 null 清除（version=2）
    cleared = client.patch(
        f"/api/v2/projects/{project['pub_id']}", headers=headers,
        json={"brandrank_domain": None, "expected_version": 2},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["brandrank_domain"] is None

    # patch 不带该字段：不动真源列（先设回 insurance，再不传字段 patch name）
    client.patch(
        f"/api/v2/projects/{project['pub_id']}", headers=headers,
        json={"brandrank_domain": "insurance", "expected_version": 3},
    )
    renamed = client.patch(
        f"/api/v2/projects/{project['pub_id']}", headers=headers,
        json={"name": "ABX Renamed", "expected_version": 4},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["brandrank_domain"] == "insurance"

    # 非法词表值 400（create 与 patch 两侧）
    bad_create = client.post(
        "/api/v2/projects", headers=headers | {"Idempotency-Key": "idem-" + secrets.token_hex(16)},
        json={"name": "Bad", "customer_name": "Bad", "brandrank_domain": "不存在的领域"},
    )
    assert bad_create.status_code == 400
    assert bad_create.json()["error"]["code"] == "unknown_brandrank_domain"
    bad_patch = client.patch(
        f"/api/v2/projects/{project['pub_id']}", headers=headers,
        json={"brandrank_domain": "不存在的领域", "expected_version": 5},
    )
    assert bad_patch.status_code == 400
    assert bad_patch.json()["error"]["code"] == "unknown_brandrank_domain"
