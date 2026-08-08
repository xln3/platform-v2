"""P2 分析包集成测试（2026-08-08）：INV-1 写读路径 + outbox 毒消息兜底 + metric_daily 竞态。

只打 55433 开发库（S02_POSTGRES_DSN 缺省值），严禁对生产库跑。
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import psycopg
from geo_platform.analytics.outbox import OUTBOX_MAX_ATTEMPTS, OutboxConsumer
from geo_platform.analytics.service import AnalyticsService

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)

_GOOD_PROVENANCE = {
    "captcha_mode": "not_challenged",
    "geo_source": "observed_gb_code",
    "account_source": "self_pool",
    "rate_policy": "pool_burn",
    "degraded_flag": "0",
    "observed_gb_code": "310000",
}


def _provenance(captured: datetime) -> RedactedProvenance:
    return RedactedProvenance(
        platform_account_pub_id=None,
        browser_profile_version_pub_id=None,
        session_event_pub_id=None,
        channel=CaptureChannel.API,
        authorization_scope=("read",),
        adapter_version="inv1-integration-v1",
        capture_time=captured,
        access_class=AccessClass.PUBLIC,
    )


def _persist(
    service: AnalyticsService,
    *,
    tenant: str,
    project: str,
    answer_pub_id: str,
    text: str,
    captured: datetime,
    dimensions: dict[str, str],
) -> None:
    service.analyze_and_persist(
        tenant_pub_id=tenant,
        project_pub_id=project,
        answer_pub_id=answer_pub_id,
        answer_text=text,
        brand="Acme",
        competitors=("Beta",),
        citations=(),
        dimensions=dimensions,
        own_domains=(),
        provenance=_provenance(captured),
        scorer_version="scorer-v2",
        metric_version="metrics-v2",
        model_version="rules-v1",
    )


def test_eligible_write_path_and_unified_read_filters() -> None:
    """五元负载真实计算 eligible；overview/competitors 与 breakdown 同口径排除。"""
    suffix = uuid4().hex
    tenant = f"tnt_inv1_{suffix}"
    project = f"prj_inv1_{suffix}"
    captured = datetime.now(UTC)
    service = AnalyticsService(dsn=POSTGRES_DSN)
    base_dimensions = {"model": "doubao", "region": "上海", "mode": "normal"}
    _persist(
        service,
        tenant=tenant,
        project=project,
        answer_pub_id=f"ans_ok_{suffix}",
        text="Acme 排第一。",
        captured=captured,
        dimensions={**base_dimensions, **_GOOD_PROVENANCE},
    )
    _persist(
        service,
        tenant=tenant,
        project=project,
        answer_pub_id=f"ans_wall_{suffix}",
        text="Acme 排第一。",
        captured=captured,
        dimensions={
            **base_dimensions,
            **_GOOD_PROVENANCE,
            "captcha_mode": "wall_captcha",
        },
    )
    with psycopg.connect(POSTGRES_DSN) as connection:
        flags = connection.execute(
            """
            SELECT pub_id,eligible,degraded FROM analytics.answer
            WHERE tenant_pub_id=%s ORDER BY pub_id
            """,
            (tenant,),
        ).fetchall()
        daily_dimensions = connection.execute(
            """
            SELECT dimensions->>'eligible' AS eligible_flag, denominator
            FROM analytics.metric_daily
            WHERE tenant_pub_id=%s AND metric_name='mention_rate'
            ORDER BY eligible_flag
            """,
            (tenant,),
        ).fetchall()
    assert [(row[0], row[1], row[2]) for row in flags] == [
        (f"ans_ok_{suffix}", True, False),
        (f"ans_wall_{suffix}", False, False),
    ]
    # eligible 标记进入 metric rollup 快照（两条答案分列两行）
    assert [(row[0], row[1]) for row in daily_dimensions] == [("false", 1), ("true", 1)]
    mention = next(
        row
        for row in service.aggregate(
            tenant_pub_id=tenant,
            project_pub_id=project,
            start=captured.date(),
            end=captured.date(),
        )
        if row["metric_name"] == "mention_rate"
    )
    assert mention["denominator"] == 1  # 只有合格答案入账（与 breakdown 的 a.eligible 同口径）
    assert mention["value"] == Decimal("1")
    competitors = service.aggregate_competitors(
        tenant_pub_id=tenant,
        project_pub_id=project,
        start=captured.date(),
        end=captured.date(),
    )
    assert competitors == [] or all(row["answer_count"] == 1 for row in competitors)


def test_legacy_payload_without_provenance_inherits_status_quo() -> None:
    """无五元键的负载（历史形状）：eligible 缺省 true，dimensions 快照不补键。"""
    suffix = uuid4().hex
    tenant = f"tnt_inv1_legacy_{suffix}"
    project = f"prj_inv1_legacy_{suffix}"
    captured = datetime.now(UTC)
    service = AnalyticsService(dsn=POSTGRES_DSN)
    _persist(
        service,
        tenant=tenant,
        project=project,
        answer_pub_id=f"ans_legacy_{suffix}",
        text="Acme 排第一。",
        captured=captured,
        dimensions={"model": "probe", "region": "cn", "mode": "normal"},
    )
    with psycopg.connect(POSTGRES_DSN) as connection:
        row = connection.execute(
            "SELECT eligible FROM analytics.answer WHERE tenant_pub_id=%s",
            (tenant,),
        ).fetchone()
        trace_dimensions = connection.execute(
            """
            SELECT dimensions FROM analytics.metric_trace
            WHERE tenant_pub_id=%s AND metric_name='mention_rate'
            """,
            (tenant,),
        ).fetchone()
    assert row == (True,)
    assert "eligible" not in trace_dimensions[0]  # 历史 dimensions_hash 零漂移


def test_metric_daily_concurrent_persist_does_not_lose_updates() -> None:
    """同 run 多题并发 fanout（每题独立连接）：advisory 锁串行化临界区，
    metric_daily 必须吃到两条 trace（无锁时后提交者用旧快照覆盖 = 丢更新）。"""
    suffix = uuid4().hex
    tenant = f"tnt_race_{suffix}"
    project = f"prj_race_{suffix}"
    captured = datetime.now(UTC)
    dimensions = {
        "model": "doubao",
        "region": "上海",
        "mode": "normal",
        **_GOOD_PROVENANCE,
    }
    service = AnalyticsService(dsn=POSTGRES_DSN)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                _persist,
                service,
                tenant=tenant,
                project=project,
                answer_pub_id=f"ans_race_{index}_{suffix}",
                text="Acme 排第一，Beta 排第二。",
                captured=captured,
                dimensions=dimensions,
            )
            for index in range(2)
        ]
        for future in futures:
            future.result()
    with psycopg.connect(POSTGRES_DSN) as connection:
        row = connection.execute(
            """
            SELECT numerator,denominator FROM analytics.metric_daily
            WHERE tenant_pub_id=%s AND metric_name='mention_rate'
            """,
            (tenant,),
        ).fetchall()
    assert row == [(2, 2)]


def test_outbox_poison_event_isolated_and_healthy_events_proceed() -> None:
    """真实 PG：队头毒事件不再堵住健康事件；失败记账落库；达阈值后隔离。"""
    suffix = uuid4().hex
    poison_id = f"evt_poison_{suffix}"
    healthy_ids = [f"evt_ok_{index}_{suffix}" for index in range(2)]
    event_type = f"probe.poison.{suffix}"  # 专属类型：与其他测试的存量事件零干扰
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            """
            INSERT INTO integration.outbox_event
              (event_id,tenant_pub_id,event_type,aggregate_pub_id,trace_id,payload,
               occurred_at)
            VALUES
              (%s,%s,%s,'agg','trace','{"probe":true}',now()),
              (%s,%s,%s,'agg','trace','{"probe":true}',now()),
              (%s,%s,%s,'agg','trace','{"probe":true}',now())
            """,
            (
                poison_id,
                f"tnt_{suffix}",
                event_type,
                healthy_ids[0],
                f"tnt_{suffix}",
                event_type,
                healthy_ids[1],
                f"tnt_{suffix}",
                event_type,
            ),
        )
    published: list[str] = []

    def publish(event: dict) -> None:
        if event["event_id"] == poison_id:
            raise RuntimeError("synthetic poison")
        published.append(event["event_id"])

    consumer = OutboxConsumer(
        dsn=POSTGRES_DSN,
        consumer_name=f"probe-{suffix}",
        publish=publish,
        event_types=(event_type,),
    )
    try:
        assert consumer.drain() == 2  # 毒事件在队头也不堵健康事件
        assert published == healthy_ids
        with psycopg.connect(POSTGRES_DSN) as connection:
            poison = connection.execute(
                """
                SELECT attempts,published_at IS NULL,last_error
                FROM integration.outbox_event WHERE event_id=%s
                """,
                (poison_id,),
            ).fetchone()
        assert poison is not None
        assert poison[0] == 1  # attempts+1 持久化（旧实现整事务回滚落不了库）
        assert poison[1] is True
        assert poison[2] is not None and poison[2].startswith("RuntimeError:")
        # 达隔离阈值后不再被选中
        with psycopg.connect(POSTGRES_DSN) as connection:
            connection.execute(
                "UPDATE integration.outbox_event SET attempts=%s WHERE event_id=%s",
                (OUTBOX_MAX_ATTEMPTS, poison_id),
            )
        assert consumer.drain() == 0
        with psycopg.connect(POSTGRES_DSN) as connection:
            state = connection.execute(
                """
                SELECT attempts,published_at IS NULL
                FROM integration.outbox_event WHERE event_id=%s
                """,
                (poison_id,),
            ).fetchone()
        assert state == (OUTBOX_MAX_ATTEMPTS, True)
    finally:
        with psycopg.connect(POSTGRES_DSN) as connection:
            connection.execute(
                "DELETE FROM integration.consumer_receipt WHERE consumer_name=%s",
                (f"probe-{suffix}",),
            )
            connection.execute(
                "DELETE FROM integration.outbox_event WHERE event_id=ANY(%s)",
                ([poison_id, *healthy_ids],),
            )


def test_answer_agg_blind_view_columns_and_rls() -> None:
    """视图列集钉死 + security_invoker RLS：无 GUC 零行，有 GUC 只见本租户合格行。"""
    suffix = uuid4().hex
    tenant = f"tnt_view_{suffix}"
    project = f"prj_view_{suffix}"
    captured = datetime.now(UTC)
    service = AnalyticsService(dsn=POSTGRES_DSN)
    _persist(
        service,
        tenant=tenant,
        project=project,
        answer_pub_id=f"ans_view_ok_{suffix}",
        text="Acme 排第一。",
        captured=captured,
        dimensions={"model": "doubao", **_GOOD_PROVENANCE},
    )
    _persist(
        service,
        tenant=tenant,
        project=project,
        answer_pub_id=f"ans_view_bad_{suffix}",
        text="Acme 排第一。",
        captured=captured,
        dimensions={"model": "doubao", **_GOOD_PROVENANCE, "degraded_flag": "1"},
    )
    role = f"s06_view_probe_{suffix}"
    with psycopg.connect(POSTGRES_DSN) as connection:
        columns = [
            row[0]
            for row in connection.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='analytics' AND table_name='answer_agg_blind'
                ORDER BY ordinal_position
                """
            ).fetchall()
        ]
    assert columns == [
        "id",
        "pub_id",
        "tenant_pub_id",
        "project_pub_id",
        "query_pub_id",
        "query_text",
        "response_text",
        "model",
        "region",
        "mode",
        "eligible",
        "degraded",
        "channel",
        "adapter_version",
        "capture_time",
        "created_at",
        "run_pub_id",
        "config_version_pub_id",
    ]
    with psycopg.connect(POSTGRES_DSN) as connection:
        with connection.transaction():
            connection.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
            connection.execute(f'GRANT USAGE ON SCHEMA analytics TO "{role}"')
            # security_invoker=on：调用者对基表也需 SELECT（与 geo/geo_worker 一致）
            connection.execute(f'GRANT SELECT ON analytics.answer TO "{role}"')
            connection.execute(f'GRANT SELECT ON analytics.answer_agg_blind TO "{role}"')
            connection.execute(f'SET LOCAL ROLE "{role}"')
            assert connection.execute(
                "SELECT count(*) FROM analytics.answer_agg_blind"
            ).fetchone() == (0,)
            connection.execute(
                "SELECT set_config('app.tenant_pub_id', %s, true)",
                (tenant,),
            )
            rows = connection.execute(
                "SELECT pub_id FROM analytics.answer_agg_blind ORDER BY pub_id"
            ).fetchall()
            # 合格行可见；degraded_flag=1 行被视图过滤（eligible 也必然 false）
            assert rows == [(f"ans_view_ok_{suffix}",)]
