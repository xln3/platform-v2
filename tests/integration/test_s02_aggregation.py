import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import psycopg
from geo_platform.analytics.service import AnalyticsService

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


def _seed_platform_tenant(tenant_pub_id: str) -> None:
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            """
            INSERT INTO platform.tenant (id,pub_id,name,state,created_at,updated_at)
            VALUES (%s,%s,%s,'active',now(),now())
            ON CONFLICT (pub_id) DO NOTHING
            """,
            (uuid4(), tenant_pub_id, tenant_pub_id),
        )


def test_full_and_incremental_aggregation_match_and_replay_does_not_drift() -> None:
    service = AnalyticsService(dsn=POSTGRES_DSN)
    suffix = uuid4().hex
    tenant = f"tnt_{suffix[:26]}"
    project = f"prj_{suffix}"
    _seed_platform_tenant(tenant)
    captured = datetime.now(UTC)
    provenance = RedactedProvenance(
        platform_account_pub_id=None,
        browser_profile_version_pub_id=None,
        session_event_pub_id=None,
        channel=CaptureChannel.API,
        authorization_scope=("read",),
        adapter_version="aggregation-v1",
        capture_time=captured,
        access_class=AccessClass.PUBLIC,
    )
    inputs = (
        (f"ans_{uuid4().hex}", "Acme 排第一，Beta 排第二。"),
        (f"ans_{uuid4().hex}", "Beta 排第一，本回答未提及目标品牌。"),
    )
    for answer_pub_id, text in inputs:
        service.analyze_and_persist(
            tenant_pub_id=tenant,
            project_pub_id=project,
            answer_pub_id=answer_pub_id,
            answer_text=text,
            brand="Acme",
            competitors=("Beta",),
            citations=(),
            dimensions={"model": "test", "region": "all", "mode": "normal"},
            own_domains=(),
            provenance=provenance,
            scorer_version="scorer-v2",
            metric_version="metrics-v2",
            model_version="rules-v1",
        )
    # Replay the first input: unique analysis and contribution keys must keep totals unchanged.
    service.analyze_and_persist(
        tenant_pub_id=tenant,
        project_pub_id=project,
        answer_pub_id=inputs[0][0],
        answer_text=inputs[0][1],
        brand="Acme",
        competitors=("Beta",),
        citations=(),
        dimensions={"model": "test", "region": "all", "mode": "normal"},
        own_domains=(),
        provenance=provenance,
        scorer_version="scorer-v2",
        metric_version="metrics-v2",
        model_version="rules-v1",
    )
    aggregate = service.aggregate(
        tenant_pub_id=tenant,
        project_pub_id=project,
        start=captured.date(),
        end=captured.date(),
    )
    mention = next(row for row in aggregate if row["metric_name"] == "mention_rate")
    assert mention["numerator"] == 1
    assert mention["denominator"] == 2
    assert mention["value"] == Decimal("0.5")
    assert mention["state"] == "ready"
    assert len(mention["filter_hash"]) == 64
    average_rank = next(row for row in aggregate if row["metric_name"] == "average_rank")
    assert average_rank["value"] == Decimal("1")
    assert average_rank["denominator"] == 1
    competitors = service.aggregate_competitors(
        tenant_pub_id=tenant,
        project_pub_id=project,
        start=captured.date(),
        end=captured.date(),
        dimensions={"model": "test"},
    )
    beta = next(row for row in competitors if row["competitor"] == "Beta")
    assert beta["mention_count"] == 2
    assert beta["mention_rate"] == Decimal("1")
    assert beta["average_rank"] == Decimal("1.5")
    assert beta["top1_rate"] == Decimal("0.5")
    with psycopg.connect(POSTGRES_DSN) as connection:
        full = connection.execute(
            """
            SELECT count(*) FILTER (WHERE mentioned)::numeric / count(*)
            FROM analytics.answer_analysis
            WHERE tenant_pub_id=%s AND answer_pub_id=ANY(%s)
            """,
            (tenant, [item[0] for item in inputs]),
        ).fetchone()[0]
        contributions = connection.execute(
            """
            SELECT count(*) FROM analytics.metric_trace
            WHERE tenant_pub_id=%s AND metric_name='mention_rate'
            """,
            (tenant,),
        ).fetchone()[0]
    assert full == mention["value"]
    assert contributions == 2
    try:
        service.aggregate(
            tenant_pub_id=tenant,
            project_pub_id=project,
            start=captured.date(),
            end=captured.date(),
            include_account_dimension=True,
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("customer aggregate leaked account-level dimension")
