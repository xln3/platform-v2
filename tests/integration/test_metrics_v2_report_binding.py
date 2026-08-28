from __future__ import annotations

from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from typing import Any, cast

import pytest
from geo_platform.reports.formal_production import (
    FormalProductionInvalid,
    FormalReportProductionService,
    FormalWindow,
)

HASH = "a" * 64
FILTERS: dict[str, list[str]] = {"model": [], "region": [], "mode": []}


def _snapshot_set() -> dict[str, Any]:
    return {
        "schema_version": "metric-snapshot-set-v2",
        "snapshot_set_pub_id": "mss_report_integration",
        "snapshot_set_hash": HASH,
        "project_pub_id": "prj_report_integration",
        "state": "ready",
        "window": {"start": "2026-08-01", "end": "2026-08-27"},
        "filters": FILTERS,
        "aggregation_method": "query_macro",
        "metrics": [
            {
                "snapshot_pub_id": "msn_report_integration",
                "snapshot_hash": HASH,
                "focal_entity_id": "entity_target",
                "metric_name": "ai_recommendation_organic_mention_rate_v2",
                "metric_version": "2.0.0",
                "metric_definition_hash": HASH,
                "state": "ready",
                "value": 0.5,
                "observed_value": 0.5,
                "raw_numerator": 2,
                "raw_denominator": 4,
                "unique_query_count": 2,
                "coverage": {"semantic": 1.0},
                "contribution_set_hash": HASH,
                "query_contribution_set_hash": HASH,
                "design_contribution_set_hash": HASH,
            }
        ],
    }


class _SnapshotReader:
    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document

    def get_snapshot_set(self, *, tenant_pub_id: str, set_pub_id: str) -> dict[str, Any]:
        assert tenant_pub_id == "tnt_report_integration"
        assert set_pub_id == "mss_report_integration"
        return deepcopy(self.document)

    def export_bundle(self, *, tenant_pub_id: str, set_pub_id: str) -> dict[str, Any]:
        del tenant_pub_id, set_pub_id
        raise AssertionError("binding validation must not generate an export")


def _service(document: dict[str, Any]) -> FormalReportProductionService:
    return FormalReportProductionService(
        dsn="postgresql://integration-not-opened",
        evidence=cast(Any, SimpleNamespace(store=SimpleNamespace())),
        metric_snapshots=_SnapshotReader(document),
    )


def _validate(service: FormalReportProductionService):  # type: ignore[no-untyped-def]
    return service.validate_metric_snapshot_binding(
        tenant_pub_id="tnt_report_integration",
        project_pub_id="prj_report_integration",
        window=FormalWindow(date(2026, 8, 1), date(2026, 8, 27)),
        snapshot_set_pub_id="mss_report_integration",
        snapshot_set_hash=HASH,
        filters=FILTERS,
    )


def test_formal_service_reads_and_freezes_the_exact_v2_snapshot_identity() -> None:
    binding = _validate(_service(_snapshot_set()))

    assert binding.snapshot_set_pub_id == "mss_report_integration"
    assert binding.snapshot_set_hash == HASH
    assert binding.snapshots[0].snapshot_hash == HASH
    assert binding.snapshots[0].contribution_set_hash == HASH


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ({"snapshot_set_hash": "b" * 64}, "metric_snapshot_set_hash_mismatch"),
        ({"state": "failed"}, "metric_snapshot_set_not_ready"),
    ],
)
def test_formal_service_fails_closed_for_snapshot_drift_or_failure(
    mutation: dict[str, Any], error: str
) -> None:
    document = _snapshot_set() | mutation

    with pytest.raises(FormalProductionInvalid, match=error):
        _validate(_service(document))
