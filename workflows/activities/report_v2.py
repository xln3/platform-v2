"""Report-worker activities for fail-closed Metrics V2 report binding."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import date
from typing import Any

from geo_platform.reports.formal_production import (
    FormalProductionInvalid,
    FormalReportProductionService,
    FormalWindow,
)
from temporalio import activity
from temporalio.exceptions import ApplicationError

from workflows.activities.s02 import _formal_report_service


@activity.defn
async def validate_formal_metric_snapshot_binding_activity(
    payload: dict[str, Any],
) -> dict[str, Any]:
    required = {
        "tenant_pub_id",
        "project_pub_id",
        "window_start",
        "window_end",
        "metric_snapshot_set_pub_id",
        "metric_snapshot_set_hash",
        "metric_snapshot_filters",
        "metric_snapshot_dependency_hash",
    }
    if any(payload.get(key) is None for key in required):
        raise ApplicationError(
            "formal metric snapshot binding is required",
            type="metric_snapshot_binding_required",
            non_retryable=True,
        )
    raw_filters = payload["metric_snapshot_filters"]
    if not isinstance(raw_filters, Mapping):
        raise ApplicationError(
            "formal metric snapshot filters are invalid",
            type="metric_snapshot_filters_invalid",
            non_retryable=True,
        )
    service: FormalReportProductionService = _formal_report_service(ensure_bucket=False)
    try:
        binding = await asyncio.to_thread(
            service.validate_metric_snapshot_binding,
            tenant_pub_id=str(payload["tenant_pub_id"]),
            project_pub_id=str(payload["project_pub_id"]),
            window=FormalWindow(
                date.fromisoformat(str(payload["window_start"])),
                date.fromisoformat(str(payload["window_end"])),
            ),
            snapshot_set_pub_id=str(payload["metric_snapshot_set_pub_id"]),
            snapshot_set_hash=str(payload["metric_snapshot_set_hash"]),
            filters=dict(raw_filters),
        )
    except (FormalProductionInvalid, ValueError) as exc:
        raise ApplicationError(
            str(exc),
            type=str(exc),
            non_retryable=True,
        ) from exc
    if binding.dependency_hash != str(payload["metric_snapshot_dependency_hash"]):
        raise ApplicationError(
            "metric snapshot dependency hash mismatch",
            type="metric_snapshot_dependency_hash_mismatch",
            non_retryable=True,
        )
    return {
        "state": "bound",
        "metric_snapshot_set_pub_id": binding.snapshot_set_pub_id,
        "metric_snapshot_set_hash": binding.snapshot_set_hash,
        "metric_snapshot_dependency_hash": binding.dependency_hash,
        "member_snapshot_pub_ids": sorted(item.snapshot_pub_id for item in binding.snapshots),
    }


__all__ = ["validate_formal_metric_snapshot_binding_activity"]
