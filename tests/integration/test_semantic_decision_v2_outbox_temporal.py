from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest
from geo_platform.metrics_v2.repository import MetricsV2Repository

from tools.seed_metrics_v2_definitions import build_seed_bundle, seed

from .metrics_v2_fixtures import digest

pytestmark = pytest.mark.isolated_postgres

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


def test_decision_request_writes_job_event_and_temporal_command_once() -> None:
    token = uuid4().hex
    artifacts = build_seed_bundle()
    seed(POSTGRES_DSN, artifacts)
    task = next(item for item in artifacts if item.kind == "decision_task")
    task_ref = f"{task.name}@{task.version}"
    policy = next(
        item
        for item in artifacts
        if item.kind == "judge_policy" and task_ref in item.document["compatible_task_refs"]
    )
    tenant = f"tnt_{token}"
    project = f"prj_{token}"
    repository = MetricsV2Repository(POSTGRES_DSN)
    arguments = {
        "tenant_pub_id": tenant,
        "project_pub_id": project,
        "task_ref": task_ref,
        "subject_ref": {"answer_pub_id": f"ans_{token}"},
        "input_snapshot_ref": f"answer:{token}",
        "input_hash": digest(f"input:{token}"),
        "context_hash": digest(f"context:{token}"),
        "judge_policy_hash": policy.content_hash,
        "idempotency_key": f"decision-temporal-{token}",
    }
    requested = repository.create_decision_request(**arguments)
    replay = repository.create_decision_request(
        **{**arguments, "idempotency_key": f"decision-temporal-replay-{token}"}
    )
    assert replay["decision_job_pub_id"] == requested["decision_job_pub_id"]
    assert replay["reused"] is True

    job_id = requested["decision_job_pub_id"]
    with psycopg.connect(POSTGRES_DSN) as connection:
        row = connection.execute(
            """
            SELECT job.status,command.workflow_type,command.workflow_id,
                   command.task_queue,command.payload,count(outbox.event_id)
            FROM analytics.semantic_decision_job_v2 job
            JOIN integration.workflow_start_command command
              ON command.tenant_pub_id=job.tenant_pub_id
             AND command.workflow_id='decision-v2:' || job.pub_id
            JOIN integration.outbox_event outbox
              ON outbox.tenant_pub_id=job.tenant_pub_id
             AND outbox.aggregate_pub_id=job.pub_id
             AND outbox.event_type='semantic.decision.requested.v2'
            WHERE job.tenant_pub_id=%s AND job.pub_id=%s
            GROUP BY job.status,command.workflow_type,command.workflow_id,
                     command.task_queue,command.payload
            """,
            (tenant, job_id),
        ).fetchone()
    assert row is not None
    assert row[:4] == (
        "pending",
        "semantic_decision_v2",
        f"decision-v2:{job_id}",
        "geo-platform-v2-decision",
    )
    assert row[4]["decision_job_pub_id"] == job_id
    assert row[4]["task_ref"] == task_ref
    assert row[5] == 1
