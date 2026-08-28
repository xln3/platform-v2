from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from geo_platform.metrics_v2.repository import MetricsV2Repository

from .metrics_v2_fixtures import digest

pytestmark = pytest.mark.isolated_postgres

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


def test_metrics_backfill_dry_run_counts_unknowns_and_apply_skips_them() -> None:
    token = uuid4().hex
    tenant = f"tnt_{token}"
    project = f"prj_{token}"
    captured_at = datetime.now(UTC) - timedelta(minutes=1)
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            """
            INSERT INTO analytics.answer
              (pub_id,tenant_pub_id,project_pub_id,query_pub_id,query_text,
               response_text,model,region,mode,eligible,degraded,channel,
               adapter_version,capture_time,response_raw,
               response_markdown_normalized,response_ast,response_html_sanitized,
               response_plain_text,response_hash,render_parser_version)
            VALUES
              (%s,%s,%s,%s,%s,%s,'fixture-model','cn','api',true,false,'api',
               'fixture-v1',%s,'{}',%s,'[]'::jsonb,%s,%s,%s,'fixture-parser-v1')
            """,
            (
                f"ans_{token}",
                tenant,
                project,
                f"qry_{token}",
                f"metrics backfill query {token}",
                f"metrics backfill answer {token}",
                captured_at,
                f"metrics backfill answer {token}",
                f"metrics backfill answer {token}",
                f"metrics backfill answer {token}",
                digest(f"answer:{token}"),
            ),
        )
    repository = MetricsV2Repository(POSTGRES_DSN)
    arguments = {
        "tenant_pub_id": tenant,
        "project_pub_id": project,
        "cursor": None,
        "limit": 10,
        "as_of": (captured_at + timedelta(seconds=10)).isoformat(),
    }

    dry_run = repository.load_metrics_backfill_batch(**arguments, dry_run=True)
    apply = repository.load_metrics_backfill_batch(**arguments, dry_run=False)

    assert dry_run["candidate_count"] == 1
    assert dry_run["page_count"] == 1
    assert dry_run["unknown_count"] == 1
    assert dry_run["subjects"] == []
    assert dry_run["items"][0]["preparation_state"] == "unknown"
    assert apply["items"] == []
    assert apply["subjects"] == []
    assert apply["unknown_count"] == 1
