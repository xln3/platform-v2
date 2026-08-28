from __future__ import annotations

import os
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import pytest
from geo_platform.metrics_v2.export import (
    artifact_sha256,
    build_metrics_csv_zip,
    build_metrics_xlsx,
)
from geo_platform.metrics_v2.repository import MetricsV2Repository

from .metrics_v2_fixtures import digest, snapshot_row, snapshot_set_row

pytestmark = pytest.mark.isolated_postgres

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


def test_export_bundle_is_bound_to_one_set_and_renders_all_nine_sheets() -> None:
    token = uuid4().hex
    tenant = f"tnt_{token}"
    project = f"prj_{token}"
    repository = MetricsV2Repository(POSTGRES_DSN)
    repository.persist_snapshot_set_atomic(
        tenant_pub_id=tenant,
        project_pub_id=project,
        snapshot_set=snapshot_set_row(token),
        snapshots=[snapshot_row(token)],
    )

    bundle = repository.export_bundle(tenant_pub_id=tenant, set_pub_id=f"mss_{token}")
    assert set(bundle) == {
        "readme",
        "metrics",
        "queries",
        "answers",
        "decisions",
        "events",
        "exclusions",
        "design_cells",
        "hashes",
    }
    assert bundle["readme"][0]["snapshot_set_hash"] == digest(f"set:{token}")
    assert bundle["metrics"][0]["snapshot_hash"] == digest(f"snapshot:{token}")
    assert {row["object_type"] for row in bundle["hashes"]} >= {
        "snapshot_set",
        "snapshot_hash",
        "contribution_set_hash",
        "query_contribution_set_hash",
        "design_contribution_set_hash",
    }

    workbook = build_metrics_xlsx(bundle)
    archive = build_metrics_csv_zip(bundle)
    assert len(artifact_sha256(workbook)) == 64
    assert len(artifact_sha256(archive)) == 64
    with ZipFile(BytesIO(archive)) as opened:
        assert set(opened.namelist()) == {
            "README.csv",
            "METRICS.csv",
            "QUERIES.csv",
            "ANSWERS.csv",
            "DECISIONS.csv",
            "EVENTS.csv",
            "EXCLUSIONS.csv",
            "DESIGN_CELLS.csv",
            "HASHES.csv",
        }
    with pytest.raises(LookupError):
        repository.export_bundle(tenant_pub_id=f"tnt_other_{token}", set_pub_id=f"mss_{token}")
