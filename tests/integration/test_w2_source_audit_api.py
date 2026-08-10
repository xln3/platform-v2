"""W2 信源抓取 + 核对集成测试（dev PG 55433 / MinIO 19000 / CH 18123）。

真实 loader/sink/CAS/outbox/projection/ClickHouse，抓取与 LLM 判定注入 fake
（不打外网/真 LLM）。覆盖：source_document 幂等落库、CAS 正文可回读、
source_audit 两口径落库 + outbox 事件、口径B 官网语料事实基底（确认官网域命中 /
外域快照 host 过滤剔除）、口径分版本幂等（口径A v1 / 口径B v2）、重跑不重复、
CH source_audit_fact 投影。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest
from geo_platform.analytics.clickhouse import ClickHouseWriter
from geo_platform.analytics.outbox import OutboxConsumer
from geo_platform.analytics.projection import AnalyticsProjection
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.evidence.service import EvidenceService
from geo_platform.tenancy.ids import new_pub_id
from geo_platform.tenancy.psycopg import tenant_connection
from psycopg.rows import dict_row

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from workflows.activities.source_audit import (
    PROMPT_VERSION_FACTUAL,
    PROMPT_VERSION_TRANSCRIPT,
    AuditLlmConfig,
    JudgeOutcome,
    SourceAuditInput,
    _MinioSourceTextStore,
    _PostgresAuditLoader,
    _PostgresAuditSink,
    execute_source_audit,
)
from workflows.activities.source_fetch import (
    HttpAttempt,
    SourceFetchConfig,
    SourceFetchInput,
    _EvidenceServiceSink,
    _PostgresSourceLoader,
    execute_source_fetch,
)

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)
CLICKHOUSE_ENDPOINT = os.getenv("S02_CLICKHOUSE_ENDPOINT", "http://127.0.0.1:18123")
MINIO_ENDPOINT = os.getenv("S02_MINIO_ENDPOINT", "http://127.0.0.1:19000")

_TEXT = (
    "中意人寿保险有限公司成立于二零零二年，注册资本三十七亿元人民币，"
    "由中国石油天然气集团与意大利忠利保险合资组建，是国内颇具规模的合资寿险公司之一。"
    "公司最新推出的重疾险覆盖一百二十种疾病，包含轻症豁免保费责任，面向全国销售，"
    "旨在提升家庭健康保障水平，满足人民群众日益增长的健康管理需求，"
    "为客户提供全生命周期的风险保障与财富管理服务。"
    "公司业务覆盖全国多个省市自治区，服务客户数以百万计，"
    "长期保持稳健的经营风格与充足的偿付能力水平。"
)
_CITED = "中意人寿的重疾险覆盖一百二十种疾病，包含轻症豁免保费责任。"

_URL_OK = "https://sources.example.com/zyrs-profile"
_URL_404 = "https://sources.example.com/gone"

# 官网语料（口径B 事实基底第二级）：确认官网域 + 外域各一页，host 过滤必须剔除外域
_OWN_SITE = "https://www.zyrs-ins.example.com/"
_OWN_SITE_TEXT = "中意人寿官网首页：重疾险覆盖一百二十种疾病，轻症豁免保费，注册资本三十七亿元。"
_FOREIGN_SITE = "https://foreign.example.com/x"
_FOREIGN_SITE_TEXT = "外站正文，绝不应混入官网语料。"


class _StubFetcher:
    """集成测试抓取替身：一个 URL 返回正文，另一个返回 404（不打外网）。"""

    def fetch_httpx(self, url: str) -> HttpAttempt:
        if url == _URL_OK:
            return HttpAttempt(url, 200, _TEXT, "density-extract-v1", None, None)
        return HttpAttempt(url, 404, "", None, None, None)

    def fetch_browser(self, url: str) -> HttpAttempt:  # pragma: no cover - 不触发
        raise AssertionError("browser fallback should not run in this scenario")

    def close(self) -> None:
        pass


class _StubJudge:
    """verbatim 恒通过的判定替身（quote 直接取自真实正文/引述，不打真 LLM）。"""

    def __init__(self) -> None:
        self.factual_blobs: list[str] = []

    def judge(
        self, *, dimension: str, url: str, source_text: str, answer_blob: str
    ) -> JudgeOutcome:
        if dimension == "factual":
            self.factual_blobs.append(answer_blob)
        return JudgeOutcome(
            verdict="accurate",
            quote_source="重疾险覆盖一百二十种疾病，包含轻症豁免保费责任",
            quote_answer="重疾险覆盖一百二十种疾病",
            rationale="引述与正文一致。",
        )


@pytest.fixture()
def seeded_run() -> Any:
    """造一个完整 platform 数据链：tenant→customer→project→config→run→task×2→intake。"""
    suffix = uuid.uuid4().hex[:12]
    tenant_id = uuid.uuid4()
    tenant_pub = f"tnt_w2_{suffix}"
    project_pub = f"prj_w2_{suffix}"
    run_pub = f"run_w2_{suffix}"
    project_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    config_id = uuid.uuid4()
    config_version_id = uuid.uuid4()
    run_id = uuid.uuid4()
    now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    citations = json.dumps(
        [
            {"url": _URL_OK, "title": "中意人寿官网", "cited_text": _CITED},
            {"url": _URL_404, "title": "失效页面", "cited_text": "失效引述"},
        ],
        ensure_ascii=False,
    )
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            "INSERT INTO platform.tenant (id,pub_id,name,state,created_at,updated_at) "
            "VALUES (%s,%s,'W2 integration','active',now(),now())",
            (tenant_id, tenant_pub),
        )
        connection.execute(
            "INSERT INTO platform.customer (id,pub_id,tenant_id,version,created_at,updated_at,"
            "name) VALUES (%s,%s,%s,1,now(),now(),'W2 customer')",
            (customer_id, new_pub_id("cus"), tenant_id),
        )
        connection.execute(
            "INSERT INTO platform.project (id,pub_id,tenant_id,version,created_at,updated_at,"
            "customer_id,name,state) VALUES (%s,%s,%s,1,now(),now(),%s,'W2 project','active')",
            (project_id, project_pub, tenant_id, customer_id),
        )
        connection.execute(
            "INSERT INTO platform.monitoring_config (id,pub_id,tenant_id,version,created_at,"
            "updated_at,project_id,state,current_version) "
            "VALUES (%s,%s,%s,1,now(),now(),%s,'frozen',1)",
            (config_id, new_pub_id("mcg"), tenant_id, project_id),
        )
        connection.execute(
            "INSERT INTO platform.monitoring_config_version (id,pub_id,tenant_id,version,"
            "created_at,updated_at,config_id,revision,effective_at,snapshot_json,snapshot_hash) "
            "VALUES (%s,%s,%s,1,now(),now(),%s,1,%s,'{}',%s)",
            (
                config_version_id,
                new_pub_id("mcv"),
                tenant_id,
                config_id,
                now,
                "a" * 64,
            ),
        )
        connection.execute(
            "INSERT INTO platform.collection_run (id,pub_id,tenant_id,version,created_at,"
            "updated_at,project_id,config_version_id,idempotency_key,workflow_id,state,"
            "total_tasks,completed_tasks,failed_tasks,paused) "
            "VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,'completed',1,1,0,false)",
            (
                run_id,
                run_pub,
                tenant_id,
                now,
                now,
                project_id,
                config_version_id,
                f"w2-{suffix}",
                f"w2/workflow/{suffix}",
            ),
        )
        connection.execute(
            "INSERT INTO platform.collection_task (id,pub_id,tenant_id,version,created_at,"
            "updated_at,run_id,business_key,matrix_json,state,attempt_count,answer_text,"
            "citations_json) "
            "VALUES (%s,%s,%s,1,%s,%s,%s,%s,'{}','done',1,'answer',%s)",
            (uuid.uuid4(), new_pub_id("tsk"), tenant_id, now, now, run_id, "q1", citations),
        )
        connection.execute(
            "INSERT INTO platform.intake_profile (id,pub_id,tenant_id,project_id,version,"
            "created_at,updated_at,truth_confirmed,selling_points,licenses) "
            "VALUES (%s,%s,%s,%s,1,now(),now(),true,%s,%s)",
            (
                uuid.uuid4(),
                new_pub_id("inp"),
                tenant_id,
                project_id,
                "重疾险覆盖一百二十种疾病，轻症豁免保费。",
                json.dumps([{"name": "保险许可证", "no": "P10001"}]),
            ),
        )
        connection.execute(
            "INSERT INTO platform.asset_confirmation_version (id,pub_id,tenant_id,project_id,"
            "version,revision,brand_name,website,product_name,competitor_name,"
            "prohibited_claim,declared_by,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,1,1,'中意人寿',%s,'重疾险','友邦','无','test',now(),now())",
            (uuid.uuid4(), new_pub_id("acv"), tenant_id, project_id, _OWN_SITE),
        )
    yield SimpleNamespace(tenant=tenant_pub, project=project_pub, run=run_pub)
    with psycopg.connect(POSTGRES_DSN) as connection:
        for table, column in (
            ("integration.outbox_event", "tenant_pub_id"),
            ("evidence.evidence_relation", "tenant_pub_id"),
            ("evidence.evidence_asset", "tenant_pub_id"),
            ("platform.source_audit", "tenant_id"),
            ("platform.source_document", "tenant_id"),
            ("platform.collection_task", "tenant_id"),
            ("platform.collection_run", "tenant_id"),
            ("platform.monitoring_config_version", "tenant_id"),
            ("platform.monitoring_config", "tenant_id"),
            ("platform.asset_confirmation_version", "tenant_id"),
            ("platform.intake_profile", "tenant_id"),
            ("platform.project", "tenant_id"),
            ("platform.customer", "tenant_id"),
        ):
            value: object = tenant_pub if column == "tenant_pub_id" else tenant_id
            connection.execute(f"DELETE FROM {table} WHERE {column}=%s", (value,))
        connection.execute("DELETE FROM platform.tenant WHERE id=%s", (tenant_id,))


def _services() -> tuple[EvidenceService, ContentAddressedObjectStore, ClickHouseWriter]:
    store = ContentAddressedObjectStore(
        endpoint=MINIO_ENDPOINT,
        access_key="geo",
        secret_key="geo_dev_only_password",
    )
    store.ensure_bucket()
    return (
        EvidenceService(dsn=POSTGRES_DSN, store=store),
        store,
        ClickHouseWriter(endpoint=CLICKHOUSE_ENDPOINT, user="geo", password="geo_dev_only"),
    )


def _seed_own_site_snapshot(
    *,
    evidence: EvidenceService,
    tenant: str,
    project: str,
    run: str,
    url: str,
    text: str,
) -> None:
    """模拟 W4 产物：own_site_snapshot 正文 JSON 资产 + own_site_page relation（挂 run）。"""
    payload = json.dumps(
        {
            "url": url,
            "final_url": url,
            "title": "快照页",
            "fetched_at": "2026-08-05T12:00:00+00:00",
            "text": text,
            "text_bytes": len(text.encode("utf-8")),
            "extractor": "innertext-v1",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    pub_id = new_pub_id("evd")
    provenance = RedactedProvenance(
        platform_account_pub_id=None,
        browser_profile_version_pub_id=None,
        session_event_pub_id=None,
        channel=CaptureChannel.WEB,
        authorization_scope=(),
        adapter_version="test-own-site",
        capture_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        access_class=AccessClass.CUSTOMER_PRIVATE,
    )
    with tenant_connection(POSTGRES_DSN, tenant) as connection:
        evidence.capture(
            evidence_pub_id=pub_id,
            tenant_pub_id=tenant,
            project_pub_id=project,
            kind="own_site_snapshot",
            payload=payload,
            mime_type="application/json",
            source_url=url,
            provenance=provenance,
            db_connection=connection,
        )
        connection.execute(
            "INSERT INTO evidence.evidence_relation "
            "(tenant_pub_id,from_pub_id,to_pub_id,relation_type) "
            "VALUES (%s,%s,%s,'own_site_page') ON CONFLICT DO NOTHING",
            (tenant, run, pub_id),
        )
        connection.commit()


def test_w2_fetch_then_audit_end_to_end(seeded_run: Any) -> None:
    evidence, store, clickhouse = _services()
    item = SourceFetchInput(
        tenant_pub_id=seeded_run.tenant,
        project_pub_id=seeded_run.project,
        run_pub_id=seeded_run.run,
    )
    fetch_result = execute_source_fetch(
        item,
        config=SourceFetchConfig(enabled=True, limit=5),
        loader=_PostgresSourceLoader(POSTGRES_DSN),
        fetcher=_StubFetcher(),
        sink=_EvidenceServiceSink(dsn=POSTGRES_DSN, service=evidence),
        sleep=lambda _s: None,
    )
    assert fetch_result.failures == []
    by_url = {entry.url: entry for entry in fetch_result.fetched}
    assert by_url[_URL_OK].extract_status == "ok"
    assert by_url[_URL_OK].bytes == len(_TEXT.encode("utf-8"))
    assert by_url[_URL_404].extract_status == "http_error"

    # PG source_document 行 + CAS 正文可回读
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as connection:
        documents = connection.execute(
            "SELECT * FROM platform.source_document ORDER BY url"
        ).fetchall()
    assert len(documents) == 2
    ok_doc = next(row for row in documents if row["url"] == _URL_OK)
    assert ok_doc["extract_status"] == "ok"
    assert ok_doc["http_status"] == 200
    assert ok_doc["extractor"] == "density-extract-v1"
    assert ok_doc["text_cas_key"] and ok_doc["text_sha256"]
    assert (
        store.get_verified(ok_doc["text_cas_key"], ok_doc["text_sha256"]).decode("utf-8") == _TEXT
    )
    bad_doc = next(row for row in documents if row["url"] == _URL_404)
    assert bad_doc["extract_status"] == "http_error"
    assert bad_doc["text_cas_key"] is None

    # 重跑幂等：不新增行、不重复 capture
    again = execute_source_fetch(
        item,
        config=SourceFetchConfig(enabled=True, limit=5),
        loader=_PostgresSourceLoader(POSTGRES_DSN),
        fetcher=_StubFetcher(),
        sink=_EvidenceServiceSink(dsn=POSTGRES_DSN, service=evidence),
        sleep=lambda _s: None,
    )
    assert {e.url: e.source_document_pub_id for e in again.fetched} == {
        e.url: e.source_document_pub_id for e in fetch_result.fetched
    }
    with psycopg.connect(POSTGRES_DSN) as connection:
        assert connection.execute("SELECT count(*) FROM platform.source_document").fetchone() == (
            2,
        )
        assert connection.execute(
            "SELECT count(*) FROM evidence.evidence_asset WHERE kind='source_text'"
        ).fetchone() == (1,)

    # 官网快照（模拟 W4 产物）：确认官网域一页 + 外域一页（host 过滤必须剔除外域）
    _seed_own_site_snapshot(
        evidence=evidence,
        tenant=seeded_run.tenant,
        project=seeded_run.project,
        run=seeded_run.run,
        url=_OWN_SITE,
        text=_OWN_SITE_TEXT,
    )
    _seed_own_site_snapshot(
        evidence=evidence,
        tenant=seeded_run.tenant,
        project=seeded_run.project,
        run=seeded_run.run,
        url=_FOREIGN_SITE,
        text=_FOREIGN_SITE_TEXT,
    )

    # 核对层：ok 文档两口径 ok，404 文档两口径 unverifiable
    judge = _StubJudge()
    audit_result = execute_source_audit(
        SourceAuditInput(
            tenant_pub_id=seeded_run.tenant,
            project_pub_id=seeded_run.project,
            run_pub_id=seeded_run.run,
        ),
        enabled=True,
        llm=AuditLlmConfig(api_key="test-key", model="gpt-5.6-luna", base_url="https://x"),
        judge=judge,
        loader=_PostgresAuditLoader(POSTGRES_DSN),
        text_store=_MinioSourceTextStore(store),
        sink=_PostgresAuditSink(POSTGRES_DSN),
    )
    assert audit_result.failures == []
    status_by = {(a.url, a.dimension): a for a in audit_result.audited}
    assert len(status_by) == 4
    assert status_by[(_URL_OK, "transcript")].audit_status == "ok"
    assert status_by[(_URL_OK, "factual")].audit_status == "ok"
    assert status_by[(_URL_404, "transcript")].audit_status == "unverifiable"
    assert status_by[(_URL_404, "factual")].audit_status == "unverifiable"

    # 口径B 事实基底 = 已确认事实 + 官网语料（外域快照已被 host 过滤剔除）
    assert len(judge.factual_blobs) == 1
    factual_blob = judge.factual_blobs[0]
    assert "【客户已确认事实】" in factual_blob
    assert "【客户官网公开信息】" in factual_blob
    assert _OWN_SITE_TEXT in factual_blob
    assert _FOREIGN_SITE_TEXT not in factual_blob

    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as connection:
        audits = connection.execute("SELECT * FROM platform.source_audit").fetchall()
        events = connection.execute(
            "SELECT * FROM integration.outbox_event "
            "WHERE event_type='source_audit.recorded' AND tenant_pub_id=%s",
            (seeded_run.tenant,),
        ).fetchall()
    assert len(audits) == 4
    assert len(events) == 4
    ok_audit = next(
        row for row in audits if row["audit_status"] == "ok" and row["dimension"] == "transcript"
    )
    assert ok_audit["verdict"] == "accurate"
    assert ok_audit["model"] == "gpt-5.6-luna"
    assert ok_audit["prompt_version"] == PROMPT_VERSION_TRANSCRIPT
    factual_audit = next(
        row for row in audits if row["audit_status"] == "ok" and row["dimension"] == "factual"
    )
    assert factual_audit["prompt_version"] == PROMPT_VERSION_FACTUAL

    # 重跑幂等：全部 skipped，无新行/新事件
    rerun = execute_source_audit(
        SourceAuditInput(
            tenant_pub_id=seeded_run.tenant,
            project_pub_id=seeded_run.project,
            run_pub_id=seeded_run.run,
        ),
        enabled=True,
        llm=AuditLlmConfig(api_key="test-key", model="gpt-5.6-luna", base_url="https://x"),
        judge=_StubJudge(),
        loader=_PostgresAuditLoader(POSTGRES_DSN),
        text_store=_MinioSourceTextStore(store),
        sink=_PostgresAuditSink(POSTGRES_DSN),
    )
    assert len(rerun.audited) == 0
    assert len(rerun.skipped) == 4
    with psycopg.connect(POSTGRES_DSN) as connection:
        assert connection.execute("SELECT count(*) FROM platform.source_audit").fetchone() == (4,)

    # outbox → projection → CH source_audit_fact
    suffix = uuid.uuid4().hex[:8]
    consumer = OutboxConsumer(
        dsn=POSTGRES_DSN,
        consumer_name=f"w2-test-{suffix}",
        publish=AnalyticsProjection(clickhouse).publish,
    )
    assert consumer.drain() >= 4
    rows = clickhouse._post(
        "SELECT dimension, verdict, audit_status, model, prompt_version "
        "FROM geo_analytics.source_audit_fact "
        f"WHERE tenant_pub_id = '{seeded_run.tenant}' ORDER BY dimension FORMAT JSONEachRow"
    ).text.strip()
    facts = [json.loads(line) for line in rows.splitlines() if line.strip()]
    assert len(facts) == 4
    ok_facts = [f for f in facts if f["audit_status"] == "ok"]
    assert len(ok_facts) == 2
    for fact in ok_facts:
        assert fact["verdict"] == "accurate"
        assert fact["model"] == "gpt-5.6-luna"
        assert fact["prompt_version"] == (
            PROMPT_VERSION_TRANSCRIPT
            if fact["dimension"] == "transcript"
            else PROMPT_VERSION_FACTUAL
        )
    unverifiable = [f for f in facts if f["audit_status"] == "unverifiable"]
    assert len(unverifiable) == 2
