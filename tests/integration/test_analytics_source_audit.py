"""W2 信源审计只读端点集成测试（dev PG 55433）。

GET /api/v2/analytics/source-audit：真实 FastAPI app（TestClient）+ 真实 PG，
数据链=API bootstrap/建项目 + 直连 PG 造 config/run/source_document/source_audit/
asset_confirmation_version。覆盖：空项目全零+own_site_share null、聚合口径
（引用能效占比/verdict 分桶/host 标记/www·裸域·子域匹配/大小写不敏感）、
项目隔离（run join）、窗口校验 422、未认证 401。
"""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from geo_platform.main import app
from geo_platform.tenancy.ids import new_pub_id
from psycopg.rows import dict_row

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)

_WINDOW = {"start": "2026-08-01", "end": "2026-08-10"}
_OWN_URL = "https://www.cyberpeace.cn/"
_LONG_RATIONALE = "判" * 600  # 触发 rationale 截断 500 字符口径


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


def _create_project(client: TestClient, headers: dict[str, str], name: str) -> str:
    response = client.post(
        "/api/v2/projects",
        headers=headers | {"Idempotency-Key": "idem-" + secrets.token_hex(16)},
        json={"name": name, "customer_name": name},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["pub_id"])


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


class _Seeder:
    """直连 PG 造数（RLS 双 selector 置位后按 tenant_id 落行）。"""

    def __init__(self, tenant_pub: str) -> None:
        self.tenant_pub = tenant_pub
        with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT id FROM platform.tenant WHERE pub_id=%s", (tenant_pub,)
            ).fetchone()
        assert row is not None
        self.tenant_id = row["id"]

    def __enter__(self) -> _Seeder:
        self.connection = psycopg.connect(POSTGRES_DSN, row_factory=dict_row)
        self.connection.execute(
            "SELECT set_config('app.tenant_id', %s, true), "
            "set_config('app.tenant_pub_id', %s, true)",
            (str(self.tenant_id), self.tenant_pub),
        )
        return self

    def __exit__(self, *exc: object) -> None:
        if exc[0] is None:
            self.connection.commit()
        self.connection.close()

    def project_id(self, project_pub: str) -> uuid.UUID:
        row = self.connection.execute(
            "SELECT id FROM platform.project WHERE pub_id=%s", (project_pub,)
        ).fetchone()
        assert row is not None
        return uuid.UUID(str(row["id"]))

    def make_run(self, project_pub: str, key: str) -> uuid.UUID:
        project_id = self.project_id(project_pub)
        now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
        config_id = uuid.uuid4()
        self.connection.execute(
            "INSERT INTO platform.monitoring_config (id,pub_id,tenant_id,version,"
            "created_at,updated_at,project_id,state,current_version) "
            "VALUES (%s,%s,%s,1,now(),now(),%s,'frozen',1)",
            (config_id, new_pub_id("mcg"), self.tenant_id, project_id),
        )
        config_version_id = uuid.uuid4()
        self.connection.execute(
            "INSERT INTO platform.monitoring_config_version (id,pub_id,tenant_id,version,"
            "created_at,updated_at,config_id,revision,effective_at,snapshot_json,"
            "snapshot_hash) VALUES (%s,%s,%s,1,now(),now(),%s,1,%s,'{}',%s)",
            (config_version_id, new_pub_id("mcv"), self.tenant_id, config_id, now, "a" * 64),
        )
        run_id = uuid.uuid4()
        self.connection.execute(
            "INSERT INTO platform.collection_run (id,pub_id,tenant_id,version,created_at,"
            "updated_at,project_id,config_version_id,idempotency_key,workflow_id,state,"
            "total_tasks,completed_tasks,failed_tasks,paused) "
            "VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,'completed',1,1,0,false)",
            (
                run_id,
                new_pub_id("run"),
                self.tenant_id,
                now,
                now,
                project_id,
                config_version_id,
                f"sau-{key}",
                f"sau/workflow/{key}",
            ),
        )
        return run_id

    def add_confirmation(self, project_pub: str, revision: int, website: str) -> None:
        self.connection.execute(
            "INSERT INTO platform.asset_confirmation_version (id,pub_id,tenant_id,"
            "project_id,version,created_at,updated_at,revision,brand_name,website,"
            "product_name,competitor_name,prohibited_claim,declared_by) "
            "VALUES (%s,%s,%s,%s,1,now(),now(),%s,'盛邦安全',%s,'产品','竞品',"
            "'禁止条款','usr_test')",
            (
                uuid.uuid4(),
                new_pub_id("acv"),
                self.tenant_id,
                self.project_id(project_pub),
                revision,
                website,
            ),
        )

    def add_document(
        self,
        project_pub: str,
        run_id: uuid.UUID,
        *,
        url: str,
        host: str,
        fetched_at: datetime,
        final_url: str | None = None,
        http_status: int | None = 200,
        extract_status: str = "ok",
    ) -> uuid.UUID:
        document_id = uuid.uuid4()
        self.connection.execute(
            "INSERT INTO platform.source_document (id,pub_id,tenant_id,project_id,run_id,"
            "url,url_hash,host,final_url,http_status,fetched_at,extract_status,extractor,"
            "bytes,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'density-extract-v1',100,"
            "now(),now())",
            (
                document_id,
                new_pub_id("srd"),
                self.tenant_id,
                self.project_id(project_pub),
                run_id,
                url,
                _url_hash(url),
                host,
                final_url,
                http_status,
                fetched_at,
                extract_status,
            ),
        )
        return document_id

    def add_audit(
        self,
        project_pub: str,
        document_id: uuid.UUID,
        *,
        dimension: str,
        verdict: str | None,
        audit_status: str,
        rationale: str | None = None,
        model: str = "m-test",
    ) -> None:
        self.connection.execute(
            "INSERT INTO platform.source_audit (id,pub_id,tenant_id,project_id,"
            "source_document_id,dimension,verdict,quote_source,quote_answer,rationale,"
            "audit_status,model,prompt_version,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,NULL,NULL,%s,%s,%s,'audit-v1',now(),now())",
            (
                uuid.uuid4(),
                new_pub_id("sau"),
                self.tenant_id,
                self.project_id(project_pub),
                document_id,
                dimension,
                verdict,
                rationale,
                audit_status,
                model,
            ),
        )


def _fetched(day: int) -> datetime:
    return datetime(2026, 8, day, 10, 0, tzinfo=UTC)


@pytest.fixture()
def seeded() -> Any:
    """项目 A：双确认版本（最新者生效）+ 窗口内 5 文档 + 窗口外 1 文档；
    项目 B（同租户）：窗口内 1 文档（证明 run join 的项目隔离）。"""
    client = TestClient(app)
    suffix = secrets.token_hex(5)
    tenant, headers = _bootstrap(client, f"sau-{suffix}")
    project_a = _create_project(client, headers, f"SAU A {suffix}")
    project_b = _create_project(client, headers, f"SAU B {suffix}")
    docs: dict[str, uuid.UUID] = {}
    with _Seeder(tenant) as seeder:
        run_a = seeder.make_run(project_a, f"a{suffix}")
        run_b = seeder.make_run(project_b, f"b{suffix}")
        # 旧确认版本（rev1）应被最新版本（rev2）覆盖
        seeder.add_confirmation(project_a, 1, "https://www.legacy-example.com/")
        seeder.add_confirmation(project_a, 2, _OWN_URL)
        docs["d1"] = seeder.add_document(
            project_a,
            run_a,
            url="https://www.cyberpeace.cn/about",
            host="www.cyberpeace.cn",
            final_url="https://www.cyberpeace.cn/about-us",
            fetched_at=_fetched(5),
        )
        docs["d2"] = seeder.add_document(
            project_a,
            run_a,
            url="https://www.cyberpeace.cn/products",
            host="www.cyberpeace.cn",
            fetched_at=_fetched(6),
        )
        docs["d3"] = seeder.add_document(
            project_a,
            run_a,
            url="https://cyberpeace.cn/",
            host="cyberpeace.cn",
            fetched_at=_fetched(4),
        )
        # host 大小写混杂：own_site 判定必须大小写不敏感
        docs["d4"] = seeder.add_document(
            project_a,
            run_a,
            url="https://m.cyberpeace.cn/news",
            host="M.CyberPeace.cn",
            fetched_at=_fetched(3),
        )
        docs["d5"] = seeder.add_document(
            project_a,
            run_a,
            url="https://sources.example.com/article",
            host="sources.example.com",
            fetched_at=_fetched(2),
            http_status=404,
            extract_status="http_error",
        )
        # 窗口外文档：任何聚合都不得计入
        docs["d6"] = seeder.add_document(
            project_a,
            run_a,
            url="https://www.cyberpeace.cn/old",
            host="www.cyberpeace.cn",
            fetched_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        )
        seeder.add_audit(
            project_a, docs["d1"], dimension="transcript", verdict="accurate",
            audit_status="ok", rationale="转述与正文一致。",
        )
        seeder.add_audit(
            project_a, docs["d1"], dimension="factual", verdict="inaccurate",
            audit_status="ok", rationale=_LONG_RATIONALE,
        )
        seeder.add_audit(
            project_a, docs["d2"], dimension="transcript", verdict="accurate",
            audit_status="ok",
        )
        seeder.add_audit(
            project_a, docs["d3"], dimension="transcript", verdict="unsupported",
            audit_status="ok",
        )
        seeder.add_audit(
            project_a, docs["d4"], dimension="factual", verdict="accurate",
            audit_status="ok",
        )
        seeder.add_audit(
            project_a, docs["d5"], dimension="transcript", verdict="unverifiable",
            audit_status="ok",
        )
        # llm_error 行：verdict NULL，只出现在 items.audits，绝不入 verdicts 分布
        seeder.add_audit(
            project_a, docs["d5"], dimension="factual", verdict=None,
            audit_status="llm_error",
        )
        docs["d7"] = seeder.add_document(
            project_b,
            run_b,
            url="https://www.cyberpeace.cn/other-project",
            host="www.cyberpeace.cn",
            fetched_at=_fetched(5),
        )
        seeder.add_audit(
            project_b, docs["d7"], dimension="transcript", verdict="accurate",
            audit_status="ok",
        )
        tenant_id = seeder.tenant_id
    yield SimpleNamespace(
        client=client, headers=headers, tenant=tenant, tenant_id=tenant_id,
        project_a=project_a, project_b=project_b, docs=docs,
    )
    with psycopg.connect(POSTGRES_DSN) as connection:
        for table in (
            "platform.source_audit",
            "platform.source_document",
            "platform.asset_confirmation_version",
            "platform.collection_run",
            "platform.monitoring_config_version",
            "platform.monitoring_config",
            "platform.project",
            "platform.customer",
        ):
            connection.execute(f"DELETE FROM {table} WHERE tenant_id=%s", (tenant_id,))


def _get(client: TestClient, headers: dict[str, str], project_pub: str, **params: str) -> Any:
    return client.get(
        "/api/v2/analytics/source-audit",
        headers=headers,
        params={"project_pub_id": project_pub, **_WINDOW, **params},
    )


def test_empty_project_returns_all_zero(seeded: Any) -> None:
    """无确认版本且无文档的项目：全零 + own_site_share/own_site_host 为 null。"""
    project_c = _create_project(seeded.client, seeded.headers, "SAU C empty")
    response = _get(seeded.client, seeded.headers, project_c)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_pub_id"] == project_c
    assert body["start"] == _WINDOW["start"] and body["end"] == _WINDOW["end"]
    assert body["own_site_host"] is None
    assert body["documents_total"] == 0
    assert body["own_site_documents"] == 0
    assert body["own_site_share"] is None
    zero = {"accurate": 0, "inaccurate": 0, "unsupported": 0, "unverifiable": 0}
    assert body["verdicts"] == {"transcript": zero, "factual": zero}
    assert body["hosts"] == []
    assert body["items"] == []


def test_source_audit_aggregation_and_own_site_matching(seeded: Any) -> None:
    response = _get(seeded.client, seeded.headers, seeded.project_a)
    assert response.status_code == 200, response.text
    body = response.json()

    # 官网 host 取最新确认版本（rev2），不是 rev1 的 legacy-example
    assert body["own_site_host"] == "www.cyberpeace.cn"

    # 窗口/项目口径：5 文档（窗口外 d6 与项目 B 的 d7 均不计入）
    assert body["documents_total"] == 5
    # www / 裸域 / 子域 / 大小写混杂子域 全部命中官网；sources.example.com 不命中
    assert body["own_site_documents"] == 4
    assert body["own_site_share"] == 0.8

    # verdicts：只统计 audit_status='ok' 且 verdict 非 NULL（d5 的 llm_error 行排除）
    assert body["verdicts"]["transcript"] == {
        "accurate": 2, "inaccurate": 0, "unsupported": 1, "unverifiable": 1,
    }
    assert body["verdicts"]["factual"] == {
        "accurate": 1, "inaccurate": 1, "unsupported": 0, "unverifiable": 0,
    }

    # hosts：documents 降序，www 主站 2 文档居首
    hosts = {entry["host"]: entry for entry in body["hosts"]}
    assert body["hosts"][0]["host"] == "www.cyberpeace.cn"
    assert len(hosts) == 4
    assert hosts["www.cyberpeace.cn"] == {
        "host": "www.cyberpeace.cn", "is_own_site": True, "documents": 2,
        "transcript_total": 2, "transcript_accurate": 2,
    }
    assert hosts["cyberpeace.cn"]["is_own_site"] is True
    assert hosts["cyberpeace.cn"]["transcript_total"] == 1
    assert hosts["cyberpeace.cn"]["transcript_accurate"] == 0
    assert hosts["M.CyberPeace.cn"]["is_own_site"] is True  # 大小写不敏感
    assert hosts["M.CyberPeace.cn"]["transcript_total"] == 0
    assert hosts["sources.example.com"]["is_own_site"] is False
    assert hosts["sources.example.com"]["transcript_total"] == 1
    assert hosts["sources.example.com"]["transcript_accurate"] == 0

    # items：fetched_at 降序（d2 最新居首），窗口外/他项目文档不出现
    items = body["items"]
    assert len(items) == 5
    fetched = [item["fetched_at"] for item in items]
    assert fetched == sorted(fetched, reverse=True)
    assert items[0]["fetched_at"].startswith("2026-08-06T10:00:00")
    urls = {item["url"] for item in items}
    assert "https://www.cyberpeace.cn/old" not in urls
    assert "https://www.cyberpeace.cn/other-project" not in urls
    by_url = {item["url"]: item for item in items}
    d1 = by_url["https://www.cyberpeace.cn/about"]
    assert d1["is_own_site"] is True
    assert d1["http_status"] == 200
    assert d1["extract_status"] == "ok"
    assert d1["final_url"] == "https://www.cyberpeace.cn/about-us"
    # audits 按 dimension 升序（factual < transcript）
    assert [a["dimension"] for a in d1["audits"]] == ["factual", "transcript"]
    factual = d1["audits"][0]
    assert factual["verdict"] == "inaccurate" and factual["audit_status"] == "ok"
    assert len(factual["rationale"]) == 500  # 截断口径
    d5 = by_url["https://sources.example.com/article"]
    assert d5["is_own_site"] is False
    assert d5["final_url"] is None
    assert d5["http_status"] == 404
    assert d5["extract_status"] == "http_error"
    d5_audits = {(a["dimension"], a["audit_status"], a["verdict"]) for a in d5["audits"]}
    assert d5_audits == {
        ("transcript", "ok", "unverifiable"),
        ("factual", "llm_error", None),
    }

    # 项目 B：只见自己的 d7；无确认版本 → own_site_host null、own_site 全 false
    other = _get(seeded.client, seeded.headers, seeded.project_b)
    assert other.status_code == 200, other.text
    other_body = other.json()
    assert other_body["own_site_host"] is None
    assert other_body["documents_total"] == 1
    assert other_body["own_site_documents"] == 0
    assert other_body["own_site_share"] == 0.0
    assert other_body["items"][0]["url"] == "https://www.cyberpeace.cn/other-project"
    assert other_body["items"][0]["is_own_site"] is False


def test_latest_prompt_version_wins_per_dimension(seeded: Any) -> None:
    """口径升版重判后：同一文档同一口径只展示/统计最新 prompt_version 的判定行。"""
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as connection:
        project_id = connection.execute(
            "SELECT id FROM platform.project WHERE pub_id=%s", (seeded.project_a,)
        ).fetchone()["id"]
        connection.execute(
            "INSERT INTO platform.source_audit (id,pub_id,tenant_id,project_id,"
            "source_document_id,dimension,verdict,quote_source,quote_answer,rationale,"
            "audit_status,model,prompt_version,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,%s,'factual','accurate',NULL,NULL,'新版判定',"
            "'ok','m-test','audit-v2',now(),now())",
            (uuid.uuid4(), new_pub_id("sau"), seeded.tenant_id, project_id, seeded.docs["d1"]),
        )
        connection.commit()

    response = _get(seeded.client, seeded.headers, seeded.project_a)
    assert response.status_code == 200, response.text
    body = response.json()
    by_url = {item["url"]: item for item in body["items"]}
    d1 = by_url["https://www.cyberpeace.cn/about"]
    # 旧 v1 行（inaccurate / 长 rationale）被 v2 行替换，每口径仍各一行
    assert [a["dimension"] for a in d1["audits"]] == ["factual", "transcript"]
    factual = d1["audits"][0]
    assert factual["verdict"] == "accurate"
    assert factual["rationale"] == "新版判定"
    # 分布口径同步去重：d1 factual 的旧 inaccurate 不计入
    assert body["verdicts"]["factual"] == {
        "accurate": 2, "inaccurate": 0, "unsupported": 0, "unverifiable": 0,
    }


def test_window_validation_422(seeded: Any) -> None:
    inverted = _get(
        seeded.client, seeded.headers, seeded.project_a, start="2026-08-10", end="2026-08-01"
    )
    assert inverted.status_code == 422
    assert inverted.json()["error"]["code"] == "invalid_analytics_window"
    too_wide = _get(
        seeded.client, seeded.headers, seeded.project_a, start="2025-08-01", end="2026-08-10"
    )
    assert too_wide.status_code == 422
    assert too_wide.json()["error"]["code"] == "invalid_analytics_window"


def test_unauthenticated_401(seeded: Any) -> None:
    response = seeded.client.get(
        "/api/v2/analytics/source-audit",
        params={"project_pub_id": seeded.project_a, **_WINDOW},
    )
    assert response.status_code == 401
