import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from geo_platform.analytics.clickhouse import ClickHouseWriter
from geo_platform.analytics.outbox import OutboxConsumer
from geo_platform.analytics.projection import AnalyticsProjection
from geo_platform.analytics.service import AnalyticsService
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.evidence.service import EvidenceService
from geo_platform.intelligence.service import IntelligenceService
from geo_platform.reports.service import ReportService
from psycopg.rows import dict_row

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from domain.intelligence.core import EvidenceRelation
from domain.scoring.analyzer import CitationInput

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


def services() -> tuple[
    AnalyticsService, EvidenceService, ReportService, IntelligenceService, ClickHouseWriter
]:
    store = ContentAddressedObjectStore(
        endpoint="http://127.0.0.1:19000",
        access_key="geo",
        secret_key="geo_dev_only_password",
    )
    store.ensure_bucket()
    evidence = EvidenceService(dsn=POSTGRES_DSN, store=store)
    clickhouse = ClickHouseWriter(
        endpoint="http://127.0.0.1:18123", user="geo", password="geo_dev_only"
    )
    return (
        AnalyticsService(dsn=POSTGRES_DSN),
        evidence,
        ReportService(dsn=POSTGRES_DSN, evidence=evidence),
        IntelligenceService(dsn=POSTGRES_DSN),
        clickhouse,
    )


def public_provenance() -> RedactedProvenance:
    return RedactedProvenance(
        platform_account_pub_id=None,
        browser_profile_version_pub_id=None,
        session_event_pub_id=None,
        channel=CaptureChannel.API,
        authorization_scope=("read",),
        adapter_version="integration-v1",
        capture_time=datetime.now(UTC),
        access_class=AccessClass.PUBLIC,
    )


def test_raw_answer_to_kpi_trace_screenshot_clickhouse_and_published_report() -> None:
    analytics, evidence, reports, _, clickhouse = services()
    suffix = uuid4().hex
    tenant = f"tnt_{suffix}"
    project = f"prj_{suffix}"
    answer = f"ans_{suffix}"
    provenance = public_provenance()
    result = analytics.analyze_and_persist(
        tenant_pub_id=tenant,
        project_pub_id=project,
        answer_pub_id=answer,
        answer_text="推荐 Acme，它表现可靠。Beta 可作为备选。",
        brand="Acme",
        competitors=("Beta",),
        citations=(
            CitationInput(
                "https://example.com/review?utm_source=test",
                title="独立评测",
                cited_text="Acme 表现可靠",
            ),
        ),
        dimensions={
            "model": "doubao",
            "region": "beijing",
            "mode": "normal",
            "channel": "api",
        },
        own_domains=("acme.example",),
        provenance=provenance,
        scorer_version="scorer-v2",
        metric_version="metrics-v2",
        model_version="rules-v1",
    )
    screenshot_id = f"evd_{uuid4().hex}"
    evidence.capture(
        evidence_pub_id=screenshot_id,
        tenant_pub_id=tenant,
        project_pub_id=project,
        kind="answer_screenshot",
        payload=b"\x89PNG\r\n\x1a\nintegration-screenshot",
        mime_type="image/png",
        source_url="https://ai.example/answer",
        provenance=provenance,
    )
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            """
            INSERT INTO evidence.evidence_relation
              (tenant_pub_id,from_pub_id,to_pub_id,relation_type)
            VALUES (%s,%s,%s,'visualizes')
            """,
            (tenant, answer, screenshot_id),
        )
    projection = AnalyticsProjection(clickhouse)
    consumer = OutboxConsumer(
        dsn=POSTGRES_DSN,
        consumer_name=f"clickhouse-e2e-{suffix}",
        publish=projection.publish,
    )
    assert consumer.drain() >= 1
    assert clickhouse.count_event("geo_analytics.answer_fact", result["outbox_event_id"]) == 1
    assert clickhouse.count_event("geo_analytics.citation_fact", result["outbox_event_id"]) == 1
    assert clickhouse.count_event("geo_analytics.run_event", result["outbox_event_id"]) == 1
    aggregate = analytics.aggregate(
        tenant_pub_id=tenant,
        project_pub_id=project,
        start=provenance.capture_time.date(),
        end=provenance.capture_time.date(),
    )
    mention = next(row for row in aggregate if row["metric_name"] == "mention_rate")
    assert mention["value"] == Decimal("1")
    assert mention["trace_tokens"]
    assert clickhouse.count_trace("geo_analytics.metric_daily", mention["trace_tokens"][0]) == 1
    report_facts = [
        {
            "answer_pub_id": answer,
            "mention_rate": str(mention["value"]),
            "trace_token": mention["trace_tokens"][0],
            "evidence_pub_id": screenshot_id,
        }
    ]
    initial_sections = [
        {
            "component_type": "section",
            "title": "摘要",
            "body": "Acme 在本数据窗口内被提及；推荐分类器仍为实验状态。",
        },
        {
            "component_type": "kpi",
            "source": "system",
            "title": "提及率",
            "body": str(mention["value"]),
            "trace_token": mention["trace_tokens"][0],
        },
        {
            "component_type": "chart",
            "source": "system",
            "title": "趋势图",
            "body": "当前窗口提及率趋势。",
            "series": [{"date": provenance.capture_time.date().isoformat(), "value": "1"}],
        },
        {
            "component_type": "evidence",
            "source": "system",
            "title": "截图证据",
            "body": "回答截图与分析事实联动。",
            "evidence_pub_id": screenshot_id,
        },
        {
            "component_type": "recommendation",
            "title": "行动建议",
            "body": "补充高权威独立引用；推荐分类指标仍为实验状态。",
        },
    ]
    report = reports.produce(
        tenant_pub_id=tenant,
        project_pub_id=project,
        title="Acme GEO 监测报告",
        window_start=provenance.capture_time - timedelta(days=1),
        window_end=provenance.capture_time + timedelta(seconds=1),
        filters={"model": "doubao"},
        metric_version="metrics-v2",
        scorer_version="scorer-v2",
        fact_rows=report_facts,
        sections=initial_sections,
        created_by_pub_id="usr_analyst",
        provenance=provenance,
        workflow_operation_id=f"report-workflow/{suffix}",
    )
    replayed_report = reports.produce(
        tenant_pub_id=tenant,
        project_pub_id=project,
        title="Acme GEO 监测报告",
        window_start=provenance.capture_time - timedelta(days=1),
        window_end=provenance.capture_time + timedelta(seconds=1),
        filters={"model": "doubao"},
        metric_version="metrics-v2",
        scorer_version="scorer-v2",
        fact_rows=report_facts,
        sections=initial_sections,
        created_by_pub_id="usr_analyst",
        provenance=provenance,
        workflow_operation_id=f"report-workflow/{suffix}",
    )
    assert replayed_report["report_pub_id"] == report["report_pub_id"]
    assert replayed_report["report_version_pub_id"] == report["report_version_pub_id"]
    assert replayed_report["artifacts"] == report["artifacts"]
    revision = reports.create_revision(
        tenant_pub_id=tenant,
        report_pub_id=report["report_pub_id"],
        fact_rows=report_facts,
        sections=[
            *initial_sections,
            {"title": "人工复核", "body": "引用 trace 与截图证据已核验。"},
        ],
        created_by_pub_id="usr_reviewer",
        provenance=provenance,
    )
    version_diff = reports.diff_versions(
        tenant_pub_id=tenant,
        report_pub_id=report["report_pub_id"],
        before_version=1,
        after_version=2,
    )
    assert revision["version_number"] == 2
    assert version_diff.changed_component_count == 1
    report["report_version_pub_id"] = revision["report_version_pub_id"]
    report["artifacts"] = revision["artifacts"]
    reports.record_human_edit(
        tenant_pub_id=tenant,
        report_pub_id=report["report_pub_id"],
        version_pub_id=report["report_version_pub_id"],
        actor_pub_id="usr_reviewer",
        before="AI draft",
        after="Human reviewed draft",
    )
    reports.comment(
        tenant_pub_id=tenant,
        version_pub_id=report["report_version_pub_id"],
        author_pub_id="usr_reviewer",
        body="数字与 trace 已复核。",
    )
    reports.review(
        tenant_pub_id=tenant,
        report_pub_id=report["report_pub_id"],
        version_pub_id=report["report_version_pub_id"],
        reviewer_pub_id="usr_reviewer",
        decision="approved",
        rationale="事实冻结与证据链接通过",
    )
    reports.publish(
        tenant_pub_id=tenant,
        report_pub_id=report["report_pub_id"],
        version_pub_id=report["report_version_pub_id"],
        reviewer_pub_id="usr_reviewer",
    )
    reports.deliver_and_confirm(
        tenant_pub_id=tenant,
        report_pub_id=report["report_pub_id"],
        recipient_pub_id="usr_customer",
        confirmation_comment="客户已确认接收",
    )
    action_pub_id = reports.create_optimization_action(
        tenant_pub_id=tenant,
        report_pub_id=report["report_pub_id"],
        description="补充高权威独立引用",
        owner_pub_id="usr_owner",
        baseline={"citation_rate": 0.5},
    )
    reports.update_optimization_action(
        tenant_pub_id=tenant,
        action_pub_id=action_pub_id,
        state="done",
        outcome={"citation_rate": 0.75, "review": "improved"},
    )
    assert set(report["artifacts"]) == {"docx", "html", "pdf", "xlsx"}
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as connection:
        state = connection.execute(
            "SELECT state FROM reporting.report WHERE pub_id=%s",
            (report["report_pub_id"],),
        ).fetchone()
        linked = connection.execute(
            """
            SELECT count(*) AS count FROM evidence.evidence_relation
            WHERE tenant_pub_id=%s AND from_pub_id=%s AND to_pub_id=%s
            """,
            (tenant, answer, screenshot_id),
        ).fetchone()
        component_types = connection.execute(
            """
            SELECT DISTINCT component_type FROM reporting.report_component
            WHERE report_version_pub_id=%s
            """,
            (report["report_version_pub_id"],),
        ).fetchall()
    assert state["state"] == "published"
    assert linked["count"] == 1
    assert {row["component_type"] for row in component_types} == {
        "kpi",
        "chart",
        "section",
        "evidence",
        "recommendation",
    }
    with psycopg.connect(POSTGRES_DSN) as connection:
        with pytest.raises(psycopg.errors.RaiseException, match="retained"):
            connection.execute(
                "UPDATE evidence.evidence_asset SET deleted_at=now() WHERE pub_id=%s",
                (next(iter(report["artifacts"].values())),),
            )
    with psycopg.connect(POSTGRES_DSN) as connection:
        with pytest.raises(psycopg.errors.RaiseException, match="retained"):
            connection.execute(
                "UPDATE evidence.evidence_asset SET deleted_at=now() WHERE pub_id=%s",
                (screenshot_id,),
            )


def test_raw_post_to_claim_multisource_score_and_human_verdict_public_projection() -> None:
    _, evidence, _, intelligence, clickhouse = services()
    suffix = uuid4().hex
    tenant = f"tnt_{suffix}"
    investigation = intelligence.create_investigation(tenant_pub_id=tenant, title="固定案件")
    author_pub_id, domain_pub_id = intelligence.register_source(
        tenant_pub_id=tenant,
        platform="fixture_forum",
        opaque_author_id=f"opaque_{suffix}",
        display_name="不可公开作者",
        host="post.example",
        ownership_cluster="publisher-a",
        authority_class="community",
        observed_at=datetime.now(UTC),
    )
    entity_pub_id = intelligence.register_entity(
        tenant_pub_id=tenant,
        entity_type="brand",
        canonical_name="Acme",
        aliases=("Acme Inc.",),
    )
    source_evidence: list[tuple[str, str]] = []
    for index, access in enumerate(
        (AccessClass.PUBLIC, AccessClass.PUBLIC, AccessClass.PAID_OR_ORGANIZATION), 1
    ):
        evidence_id = f"evd_{uuid4().hex}"
        source_evidence.append((evidence_id, access.value))
        evidence.capture(
            evidence_pub_id=evidence_id,
            tenant_pub_id=tenant,
            project_pub_id=None,
            kind="source_snapshot",
            payload=f"source-{index}-{suffix}".encode(),
            mime_type="text/plain",
            source_url=f"https://source{index}.example/case",
            provenance=RedactedProvenance(
                platform_account_pub_id=None,
                browser_profile_version_pub_id=None,
                session_event_pub_id=None,
                channel=CaptureChannel.API,
                authorization_scope=("read",),
                adapter_version="integration-v1",
                capture_time=datetime.now(UTC),
                access_class=access,
            ),
        )
    content = intelligence.ingest_content(
        tenant_pub_id=tenant,
        investigation_pub_id=investigation,
        canonical_url="https://post.example/item",
        title="品牌比较帖子",
        body_text="Acme 声称市场第一。该说法需要多源核验。",
        embedding=(0.9, 0.1, 0.2),
        access_class="public",
        captured_at=datetime.now(UTC),
        published_at=datetime.now(UTC) - timedelta(hours=1),
        evidence_pub_id=source_evidence[0][0],
        author_pub_id=author_pub_id,
        domain_pub_id=domain_pub_id,
    )
    revised_content = intelligence.ingest_content(
        tenant_pub_id=tenant,
        investigation_pub_id=investigation,
        canonical_url="https://post.example/item",
        title="品牌比较帖子（修订）",
        body_text="Acme 声称市场第一。该说法仍然需要多个独立来源核验。",
        embedding=(0.89, 0.11, 0.2),
        access_class="public",
        captured_at=datetime.now(UTC),
        published_at=datetime.now(UTC) - timedelta(minutes=50),
        evidence_pub_id=source_evidence[1][0],
        author_pub_id=author_pub_id,
        domain_pub_id=domain_pub_id,
    )
    intelligence.record_similarity(
        tenant_pub_id=tenant,
        investigation_pub_id=investigation,
        left_content_version_pub_id=content["version_pub_id"],
        right_content_version_pub_id=revised_content["version_pub_id"],
        body_hash_equal=False,
        semantic_similarity=Decimal("0.96"),
        same_source_cluster=True,
    )
    intelligence.link_graph(
        tenant_pub_id=tenant,
        investigation_pub_id=investigation,
        from_pub_id=content["version_pub_id"],
        to_pub_id=revised_content["version_pub_id"],
        relation="derived_from",
        weight=Decimal("1"),
        evidence_pub_id=source_evidence[1][0],
    )
    intelligence.link_graph(
        tenant_pub_id=tenant,
        investigation_pub_id=investigation,
        from_pub_id=content["content_pub_id"],
        to_pub_id=entity_pub_id,
        relation="mentions",
        weight=Decimal("1"),
    )
    claim = content["claims"][0]["claim_pub_id"]
    for index, (evidence_id, _) in enumerate(source_evidence):
        intelligence.add_claim_evidence(
            tenant_pub_id=tenant,
            investigation_pub_id=investigation,
            claim_pub_id=claim,
            evidence_pub_id=evidence_id,
            relation=(EvidenceRelation.SUPPORTS if index != 1 else EvidenceRelation.CONTRADICTS),
            source_cluster=f"cluster_{index + 1}",
            independence_weight=Decimal("1"),
            rationale="固定案件交叉验证",
            from_pub_id=content["version_pub_id"],
        )
    search = intelligence.hybrid_search(
        tenant_pub_id=tenant,
        query="Acme 市场第一",
        query_embedding=(0.9, 0.1, 0.2),
        include_private=False,
    )
    assert search and all(row["access_class"] == "public" for row in search)
    intelligence.record_source_independence(
        tenant_pub_id=tenant,
        investigation_pub_id=investigation,
        source_pub_id=source_evidence[0][0],
        cluster_id="independent-a",
        independence_weight=Decimal("0.95"),
        circular_citation_risk=Decimal("0.05"),
        reasons=("不同所有者", "早于传播峰值"),
        rule_version="independence-v1",
    )
    feature_pub_ids = []
    for family, name, value in (
        ("content", "promotional_language", "0.6"),
        ("source", "source_concentration", "0.4"),
        ("propagation", "burst_score", "0.7"),
        ("external_fact", "fact_conflict", "0.2"),
    ):
        feature_pub_ids.append(
            intelligence.record_feature(
                tenant_pub_id=tenant,
                investigation_pub_id=investigation,
                subject_pub_id=f"{content['content_pub_id']}_{family}",
                feature_family=family,
                feature_name=name,
                feature_value=Decimal(value),
                explanation=f"{family} 可解释特征",
                rule_version="feature-v1",
            )
        )
    feature_consumer = OutboxConsumer(
        dsn=POSTGRES_DSN,
        consumer_name=f"clickhouse-feature-e2e-{suffix}",
        publish=AnalyticsProjection(clickhouse).publish,
    )
    assert feature_consumer.drain() >= len(feature_pub_ids)
    with psycopg.connect(POSTGRES_DSN) as connection:
        feature_events = connection.execute(
            """
            SELECT event_id FROM integration.outbox_event
            WHERE tenant_pub_id=%s AND aggregate_pub_id=ANY(%s)
            ORDER BY event_id
            """,
            (tenant, feature_pub_ids),
        ).fetchall()
    assert all(
        clickhouse.count_event("geo_analytics.feature_fact", event_id) == 1
        for (event_id,) in feature_events
    )
    scored = intelligence.score(
        tenant_pub_id=tenant,
        investigation_pub_id=investigation,
        content_feature_score=Decimal("0.8"),
        propagation_feature_score=Decimal("0.7"),
        circular_citation_risk=Decimal("0.2"),
        workflow_operation_id=f"investigation-workflow/{suffix}/score",
    )
    replayed_score = intelligence.score(
        tenant_pub_id=tenant,
        investigation_pub_id=investigation,
        content_feature_score=Decimal("0.8"),
        propagation_feature_score=Decimal("0.7"),
        circular_citation_risk=Decimal("0.2"),
        workflow_operation_id=f"investigation-workflow/{suffix}/score",
    )
    assert replayed_score["score_pub_id"] == scored["score_pub_id"]
    assert scored["result"].requires_human_verdict
    verdict_pub_id = intelligence.verdict(
        tenant_pub_id=tenant,
        investigation_pub_id=investigation,
        verdict="uncertain",
        reviewer_pub_id="usr_reviewer",
        rationale="公开独立来源仍不足以作品牌级确定判断",
        workflow_operation_id=f"investigation-workflow/{suffix}/verdict",
    )
    assert (
        intelligence.verdict(
            tenant_pub_id=tenant,
            investigation_pub_id=investigation,
            verdict="uncertain",
            reviewer_pub_id="usr_reviewer",
            rationale="公开独立来源仍不足以作品牌级确定判断",
            workflow_operation_id=f"investigation-workflow/{suffix}/verdict",
        )
        == verdict_pub_id
    )
    appeal_pub_id = intelligence.appeal(
        tenant_pub_id=tenant,
        investigation_pub_id=investigation,
        submitted_by_pub_id="usr_customer",
        reason="补充材料后请求复核",
    )
    corrected_verdict_pub_id = intelligence.resolve_appeal(
        tenant_pub_id=tenant,
        investigation_pub_id=investigation,
        appeal_pub_id=appeal_pub_id,
        reviewer_pub_id="usr_second_reviewer",
        resolution="新材料仍不支持确定结论，但证据充分度需修正",
        corrected_verdict="insufficient",
        rationale="保持概率解释并更正人工裁决为证据不足",
    )
    assert corrected_verdict_pub_id is not None
    conclusion = intelligence.public_conclusion(
        tenant_pub_id=tenant, investigation_pub_id=investigation
    )
    assert conclusion["human_verdict"]["verdict"] == "insufficient"
    assert "概率性辅助结论" in conclusion["disclaimer"]
    assert len(conclusion["public_evidence"]) == 2
    assert source_evidence[2][0] not in {item["pub_id"] for item in conclusion["public_evidence"]}
