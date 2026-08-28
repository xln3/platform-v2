from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest
from geo_platform.metrics_v2.repository import MetricsV2Repository

from .metrics_v2_fixtures import snapshot_row, snapshot_set_row

pytestmark = pytest.mark.isolated_postgres

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


def test_child_failure_rolls_back_snapshot_set_and_every_sibling() -> None:
    token = uuid4().hex
    tenant = f"tnt_{token}"
    repository = MetricsV2Repository(POSTGRES_DSN)
    with pytest.raises(ValueError, match="contribution_snapshot_mismatch"):
        repository.persist_snapshot_set_atomic(
            tenant_pub_id=tenant,
            project_pub_id=f"prj_{token}",
            snapshot_set=snapshot_set_row(token),
            snapshots=[snapshot_row(token)],
            design_contributions=[
                {
                    "snapshot_pub_id": f"msn_wrong_{token}",
                    "query_key": "q1",
                    "model": "model",
                    "region": "cn",
                    "mode": "api",
                    "planned_repeat_count": 1,
                    "valid_repeat_count": 1,
                    "failed_repeat_count": 0,
                    "known_repeat_count": 1,
                    "cell_weight": 1,
                    "state": "ready",
                    "reason_codes": [],
                    "contribution_hash": "a" * 64,
                }
            ],
        )
    with psycopg.connect(POSTGRES_DSN) as connection:
        assert connection.execute(
            """
            SELECT count(*) FROM analytics.metric_snapshot_set_v2
            WHERE tenant_pub_id=%s AND pub_id=%s
            """,
            (tenant, f"mss_{token}"),
        ).fetchone() == (0,)
        assert connection.execute(
            """
            SELECT count(*) FROM analytics.metric_snapshot_v2
            WHERE tenant_pub_id=%s AND pub_id=%s
            """,
            (tenant, f"msn_{token}"),
        ).fetchone() == (0,)
