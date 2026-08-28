from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest
from geo_platform.metrics_v2.repository import MetricsV2Repository

from .metrics_v2_fixtures import digest

pytestmark = pytest.mark.isolated_postgres

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


def test_snapshot_and_recompute_requests_persist_one_durable_workflow_command() -> None:
    token = uuid4().hex
    tenant = f"tnt_{token}"
    project = f"prj_{token}"
    repository = MetricsV2Repository(POSTGRES_DSN)
    scope = {
        "window": {"start": "2026-08-01", "end": "2026-08-02"},
        "filters": {"model": [], "region": [], "mode": []},
        "focal_entity_ids": [f"entity-{token}"],
    }
    snapshot = repository.request_snapshot(
        tenant_pub_id=tenant,
        project_pub_id=project,
        scope=scope,
        scope_hash=digest(f"snapshot-scope:{token}"),
        idempotency_key=f"snapshot-temporal-{token}",
        requested_by=f"usr_{token}",
    )
    replay = repository.request_snapshot(
        tenant_pub_id=tenant,
        project_pub_id=project,
        scope=scope,
        scope_hash=digest(f"snapshot-scope:{token}"),
        idempotency_key=f"snapshot-temporal-{token}",
        requested_by=f"usr_{token}",
    )
    recompute = repository.request_recompute(
        tenant_pub_id=tenant,
        project_pub_id=project,
        window={"start": "2026-08-01", "end": "2026-08-02"},
        focal_entity_ids=[f"entity-{token}"],
        trigger_reason="integration_test",
        idempotency_key=f"recompute-temporal-{token}",
        requested_by=f"usr_{token}",
    )
    recompute_replay = repository.request_recompute(
        tenant_pub_id=tenant,
        project_pub_id=project,
        window={"start": "2026-08-01", "end": "2026-08-02"},
        focal_entity_ids=[f"entity-{token}"],
        trigger_reason="integration_test",
        idempotency_key=f"recompute-temporal-{token}",
        requested_by=f"usr_{token}",
    )
    assert replay["job_pub_id"] == snapshot["job_pub_id"]
    assert replay["reused"] is True
    assert recompute_replay["job_pub_id"] == recompute["job_pub_id"]

    job_ids = [snapshot["job_pub_id"], recompute["job_pub_id"]]
    with psycopg.connect(POSTGRES_DSN) as connection:
        rows = connection.execute(
            """
            SELECT job.pub_id,command.workflow_type,command.workflow_id,
                   command.task_queue,command.payload,
                   count(outbox.event_id) AS outbox_count
            FROM analytics.metric_recompute_job_v2 job
            JOIN integration.workflow_start_command command
              ON command.tenant_pub_id=job.tenant_pub_id
             AND command.workflow_id='metrics-v2:' || job.pub_id
            JOIN integration.outbox_event outbox
              ON outbox.tenant_pub_id=job.tenant_pub_id
             AND outbox.aggregate_pub_id=job.pub_id
             AND outbox.event_type='metric.snapshot_set.requested.v2'
            WHERE job.tenant_pub_id=%s AND job.pub_id=ANY(%s::text[])
            GROUP BY job.pub_id,command.workflow_type,command.workflow_id,
                     command.task_queue,command.payload
            ORDER BY job.pub_id
            """,
            (tenant, job_ids),
        ).fetchall()
    assert len(rows) == 2
    for job_id, workflow_type, workflow_id, task_queue, payload, outbox_count in rows:
        assert workflow_type == "metric_snapshot_set_v2"
        assert workflow_id == f"metrics-v2:{job_id}"
        assert task_queue == "geo-platform-v2-metrics"
        assert payload["tenant_pub_id"] == tenant
        assert payload["project_pub_id"] == project
        assert payload["job_pub_id"] == job_id
        assert outbox_count == 1
