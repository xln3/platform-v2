"""W3 拉踩检测集成测试（dev PG 55433 / MinIO 19000 / CH 18123）。

真实 loader/sink/CAS/outbox/projection/ClickHouse，LLM 判定注入 fake（不打真 LLM）。
覆盖：答案+信源正文切窗判定落库、verbatim 失败丢弃、词典兜底标 experimental、
重跑幂等、CH disparagement_fact 投影、aggregation 端点（rate/cases）口径正确。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest
from geo_platform.analytics.clickhouse import ClickHouseWriter
from geo_platform.analytics.outbox import OutboxConsumer
from geo_platform.analytics.projection import AnalyticsProjection
from geo_platform.analytics.service import AnalyticsService
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.tenancy.ids import new_pub_id
from psycopg.rows import dict_row

from workflows.activities.disparagement import (
    DisparagementInput,
    LlmJudgment,
    _PostgresDisparagementLoader,
    _PostgresDisparagementSink,
    execute_disparagement,
)
from workflows.activities.source_audit import AuditLlmConfig, _MinioSourceTextStore

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)
CLICKHOUSE_ENDPOINT = os.getenv("S02_CLICKHOUSE_ENDPOINT", "http://127.0.0.1:18123")
MINIO_ENDPOINT = os.getenv("S02_MINIO_ENDPOINT", "http://127.0.0.1:19000")

_BRAND = "中意人寿"
_COMPETITOR = "友邦"
_ANSWER = (
    "在选择寿险时需要综合比较。中意人寿的重疾险价格明显偏贵，保障范围也不如友邦全面，"
    "性价比堪忧。友邦的重疾险覆盖一百二十种疾病，含轻症豁免，值得推荐。"
)
_DOC_TEXT = (
    "行业观察：中意人寿的就医绿通服务覆盖医院数量多，挂号协调便捷。"
    "友邦以高品质代理人团队著称，长期口碑不错。"
)
_DOC_URL = "https://sources.example.com/zyrs-review"


class _StubJudge:
    """verbatim 恒通过的判定替身（quote=target_brand 必然逐字命中窗文本）。"""

    def judge(
        self, *, window_text: str, target_brand: str, known_brands: tuple[str, ...]
    ) -> LlmJudgment:
        disparaging = target_brand == _BRAND and "偏贵" in window_text
        return LlmJudgment(
            subject="",
            target=target_brand,
            attitude="negative" if disparaging else "support",
            disparagement=disparaging,
            evidence_quote=target_brand,
            confidence=0.92 if disparaging else 0.8,
        )


@pytest.fixture()
def seeded_run() -> Any:
    """造完整 platform 数据链：tenant→customer→project→brand/competitor→config→run→task。"""
    suffix = uuid.uuid4().hex[:12]
    tenant_id = uuid.uuid4()
    tenant_pub = f"tnt_w3_{suffix}"
    project_pub = f"prj_w3_{suffix}"
    run_pub = f"run_w3_{suffix}"
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    matrix = json.dumps({"query": "寿险怎么选", "model": "doubao"}, ensure_ascii=False)
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            "INSERT INTO platform.tenant (id,pub_id,name,state,created_at,updated_at) "
            "VALUES (%s,%s,'W3 integration','active',now(),now())",
            (tenant_id, tenant_pub),
        )
        connection.execute(
            "INSERT INTO platform.customer (id,pub_id,tenant_id,version,created_at,updated_at,"
            "name) VALUES (%s,%s,%s,1,now(),now(),'W3 customer')",
            (uuid.uuid4(), new_pub_id("cus"), tenant_id),
        )
        connection.execute(
            "INSERT INTO platform.project (id,pub_id,tenant_id,version,created_at,updated_at,"
            "customer_id,name,state) "
            "SELECT %s,%s,%s,1,now(),now(),c.id,'W3 project','active' FROM platform.customer c "
            "WHERE c.tenant_id=%s",
            (project_id, project_pub, tenant_id, tenant_id),
        )
        connection.execute(
            "INSERT INTO platform.brand (id,pub_id,tenant_id,version,created_at,updated_at,"
            "project_id,name) VALUES (%s,%s,%s,1,now(),now(),%s,%s)",
            (uuid.uuid4(), new_pub_id("brd"), tenant_id, project_id, _BRAND),
        )
        connection.execute(
            "INSERT INTO platform.competitor (id,pub_id,tenant_id,version,created_at,"
            "updated_at,project_id,name) VALUES (%s,%s,%s,1,now(),now(),%s,%s)",
            (uuid.uuid4(), new_pub_id("cmp"), tenant_id, project_id, _COMPETITOR),
        )
        connection.execute(
            "INSERT INTO platform.monitoring_config (id,pub_id,tenant_id,version,created_at,"
            "updated_at,project_id,state,current_version) "
            "VALUES (%s,%s,%s,1,now(),now(),%s,'frozen',1)",
            (uuid.uuid4(), new_pub_id("mcg"), tenant_id, project_id),
        )
        connection.execute(
            "INSERT INTO platform.monitoring_config_version (id,pub_id,tenant_id,version,"
            "created_at,updated_at,config_id,revision,effective_at,snapshot_json,snapshot_hash) "
            "SELECT %s,%s,%s,1,now(),now(),c.id,1,%s,'{}',%s FROM platform.monitoring_config c "
            "WHERE c.tenant_id=%s",
            (uuid.uuid4(), new_pub_id("mcv"), tenant_id, now, "b" * 64, tenant_id),
        )
        connection.execute(
            "INSERT INTO platform.collection_run (id,pub_id,tenant_id,version,created_at,"
            "updated_at,project_id,config_version_id,idempotency_key,workflow_id,state,"
            "total_tasks,completed_tasks,failed_tasks,paused) "
            "SELECT %s,%s,%s,1,%s,%s,%s,v.id,%s,%s,'completed',1,1,0,false "
            "FROM platform.monitoring_config_version v WHERE v.tenant_id=%s",
            (
                run_id,
                run_pub,
                tenant_id,
                now,
                now,
                project_id,
                f"w3-{suffix}",
                f"w3/workflow/{suffix}",
                tenant_id,
            ),
        )
        connection.execute(
            "INSERT INTO platform.collection_task (id,pub_id,tenant_id,version,created_at,"
            "updated_at,run_id,business_key,matrix_json,state,attempt_count,answer_text) "
            "VALUES (%s,%s,%s,1,%s,%s,%s,'q1',%s,'done',1,%s)",
            (uuid.uuid4(), new_pub_id("ans"), tenant_id, now, now, run_id, matrix, _ANSWER),
        )
    yield SimpleNamespace(tenant=tenant_pub, project=project_pub, run=run_pub, tenant_id=tenant_id)
    with psycopg.connect(POSTGRES_DSN) as connection:
        for table, column in (
            ("integration.outbox_event", "tenant_pub_id"),
            ("platform.disparagement_judgment", "tenant_id"),
            ("platform.source_document", "tenant_id"),
            ("platform.collection_task", "tenant_id"),
            ("platform.collection_run", "tenant_id"),
            ("platform.monitoring_config_version", "tenant_id"),
            ("platform.monitoring_config", "tenant_id"),
            ("platform.brand", "tenant_id"),
            ("platform.competitor", "tenant_id"),
            ("platform.project", "tenant_id"),
            ("platform.customer", "tenant_id"),
        ):
            value: object = tenant_pub if column == "tenant_pub_id" else tenant_id
            connection.execute(f"DELETE FROM {table} WHERE {column}=%s", (value,))
        connection.execute("DELETE FROM platform.tenant WHERE id=%s", (tenant_id,))


def _store() -> ContentAddressedObjectStore:
    store = ContentAddressedObjectStore(
        endpoint=MINIO_ENDPOINT,
        access_key="geo",
        secret_key="geo_dev_only_password",
    )
    store.ensure_bucket()
    return store


def _seed_source_document(seeded_run: Any, store: ContentAddressedObjectStore) -> None:
    stored = store.put_redacted(_DOC_TEXT.encode("utf-8"), mime_type="text/plain")
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true), "
            "set_config('app.tenant_pub_id', %s, true)",
            (str(seeded_run.tenant_id), seeded_run.tenant),
        )
        connection.execute(
            "INSERT INTO platform.source_document (id,pub_id,tenant_id,project_id,run_id,"
            "url,url_hash,host,fetched_at,extract_status,extractor,bytes,text_cas_key,"
            "text_sha256,canonical_url,first_seen_at,last_verified_at,metadata_parser_version,"
            "created_at,updated_at) "
            "SELECT gen_random_uuid(),%s,%s,r.project_id,r.id,%s,%s,%s,now(),'ok',"
            "'density-extract-v1',%s,%s,%s,%s,now(),now(),'w3-fixture-v1',now(),now() "
            "FROM platform.collection_run r WHERE r.pub_id=%s",
            (
                new_pub_id("srd"),
                seeded_run.tenant_id,
                _DOC_URL,
                "c" * 64,
                "sources.example.com",
                len(_DOC_TEXT.encode("utf-8")),
                stored.key,
                stored.sha256,
                _DOC_URL,
                seeded_run.run,
            ),
        )


def _execute(seeded_run: Any, store: ContentAddressedObjectStore, *, llm_key: bool) -> Any:
    return execute_disparagement(
        DisparagementInput(
            tenant_pub_id=seeded_run.tenant,
            project_pub_id=seeded_run.project,
            run_pub_id=seeded_run.run,
        ),
        enabled=True,
        window_limit=50,
        llm=AuditLlmConfig(
            api_key="test-key" if llm_key else "",
            model="gpt-5.6-luna",
            base_url="https://x",
        ),
        judge=_StubJudge(),
        loader=_PostgresDisparagementLoader(POSTGRES_DSN),
        text_store=_MinioSourceTextStore(store),
        sink=_PostgresDisparagementSink(POSTGRES_DSN),
    )


def test_w3_disparagement_end_to_end(seeded_run: Any) -> None:
    store = _store()
    _seed_source_document(seeded_run, store)

    result = _execute(seeded_run, store, llm_key=True)
    assert result.failures == [] and result.validation_failures == 0
    assert result.windows > 0 and result.judged == result.windows
    assert result.dictionary_fallback == 0

    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true), "
            "set_config('app.tenant_pub_id', %s, true)",
            (str(seeded_run.tenant_id), seeded_run.tenant),
        )
        judgments = connection.execute("SELECT * FROM platform.disparagement_judgment").fetchall()
        events = connection.execute(
            "SELECT * FROM integration.outbox_event "
            "WHERE event_type='disparagement.recorded' AND tenant_pub_id=%s",
            (seeded_run.tenant,),
        ).fetchall()
    assert len(judgments) == result.judged
    assert len(events) == result.judged
    assert {row["target_brand"] for row in judgments} == {_BRAND}
    by_subject = {row["subject_type"]: row for row in judgments}
    # 答案只切目标品牌窗：stub 判 negative+拉踩；竞品不生成独立判定。
    answer_brand = by_subject["answer"]
    assert answer_brand["attitude"] == "negative"
    assert answer_brand["disparagement"] is True
    assert answer_brand["method"] == "llm"
    assert answer_brand["model"] == "gpt-5.6-luna"
    assert answer_brand["prompt_version"] == "disparage-v2"
    assert answer_brand["platform"] == "doubao"
    # 信源正文窗：platform=host、source_url 落库
    doc_brand = by_subject["source_document"]
    assert doc_brand["platform"] == "sources.example.com"
    assert doc_brand["source_url"] == _DOC_URL
    assert doc_brand["disparagement"] is False

    # 重跑幂等：全部 skipped，无新行/新事件
    rerun = _execute(seeded_run, store, llm_key=True)
    assert rerun.judged == 0 and rerun.skipped == result.judged
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)",
            (str(seeded_run.tenant_id),),
        )
        count = connection.execute(
            "SELECT count(*) FROM platform.disparagement_judgment"
        ).fetchone()
    assert count == (result.judged,)

    # outbox → projection → CH disparagement_fact
    clickhouse = ClickHouseWriter(endpoint=CLICKHOUSE_ENDPOINT, user="geo", password="geo_dev_only")
    consumer = OutboxConsumer(
        dsn=POSTGRES_DSN,
        consumer_name=f"w3-test-{uuid.uuid4().hex[:8]}",
        publish=AnalyticsProjection(clickhouse).publish,
    )
    assert consumer.drain() >= result.judged
    rows = clickhouse._post(
        "SELECT subject_type, target_brand, attitude, disparagement, method, "
        "prompt_version, judgment_status FROM geo_analytics.disparagement_fact "
        f"WHERE tenant_pub_id = '{seeded_run.tenant}' FORMAT JSONEachRow"
    ).text.strip()
    facts = [json.loads(line) for line in rows.splitlines() if line.strip()]
    assert len(facts) == result.judged
    assert all(fact["judgment_status"] == "ok" for fact in facts)
    disparaging = [f for f in facts if f["disparagement"] == 1]
    assert len(disparaging) == 1
    assert disparaging[0]["target_brand"] == _BRAND
    assert disparaging[0]["subject_type"] == "answer"

    # aggregation：历史竞品判定也会在查询层排除，只返回目标品牌。
    service = AnalyticsService(dsn=POSTGRES_DSN)
    rates = service.aggregate_disparagement(
        tenant_pub_id=seeded_run.tenant,
        project_pub_id=seeded_run.project,
        start=date(2026, 8, 1),
        end=date(2026, 8, 31),
        dimension="target_brand",
    )
    rate_by_brand = {row["value"]: row for row in rates}
    assert set(rate_by_brand) == {_BRAND}
    brand_rate = rate_by_brand[_BRAND]
    assert brand_rate["disparagement_count"] == 1
    assert float(brand_rate["disparagement_rate"]) > 0

    # platform 维度：doubao（answer）与 sources.example.com（正文）各一组
    platform_rates = service.aggregate_disparagement(
        tenant_pub_id=seeded_run.tenant,
        project_pub_id=seeded_run.project,
        start=date(2026, 8, 1),
        end=date(2026, 8, 31),
        dimension="platform",
    )
    assert {row["value"] for row in platform_rates} == {"doubao", "sources.example.com"}

    # cases：disparagement=true 按 confidence 降序，含证据与出处
    cases = service.disparagement_cases(
        tenant_pub_id=seeded_run.tenant,
        project_pub_id=seeded_run.project,
        start=date(2026, 8, 1),
        end=date(2026, 8, 31),
        limit=10,
    )
    assert len(cases) == 1
    case = cases[0]
    assert case["target_brand"] == _BRAND
    assert case["evidence_quote"] == _BRAND
    assert case["subject_type"] == "answer"
    assert case["source_url"] is None  # answer 判定无信源链接，出处=subject_pub_id


def test_w3_dictionary_fallback_marks_experimental(seeded_run: Any) -> None:
    store = _store()
    _seed_source_document(seeded_run, store)
    result = _execute(seeded_run, store, llm_key=False)
    assert result.failures == []
    assert result.dictionary_fallback == result.judged > 0
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)",
            (str(seeded_run.tenant_id),),
        )
        rows = connection.execute(
            "SELECT method, prompt_version, judgment_status FROM platform.disparagement_judgment"
        ).fetchall()
    assert rows
    for row in rows:
        assert row["method"] == "dictionary_experimental"
        assert row["prompt_version"] == "dictionary-v1"
        assert row["judgment_status"] == "ok"
