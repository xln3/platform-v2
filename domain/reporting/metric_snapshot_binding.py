from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any, Literal

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class MetricSnapshotBindingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BoundMetricSnapshot:
    snapshot_pub_id: str
    snapshot_hash: str
    focal_entity_id: str
    metric_name: str
    metric_version: str
    state: Literal["ready", "limited", "insufficient", "experimental", "failed"]
    value: str | None
    observed_value: str | None
    raw_numerator: str
    raw_denominator: str
    unique_query_count: int
    semantic_coverage: str | None
    definition_hash: str
    contribution_set_hash: str
    query_contribution_set_hash: str
    design_contribution_set_hash: str


@dataclass(frozen=True, slots=True)
class MetricSnapshotSetBinding:
    snapshot_set_pub_id: str
    snapshot_set_hash: str
    snapshot_set_state: Literal["ready", "partial"]
    project_pub_id: str
    window_start: date
    window_end: date
    filters: Mapping[str, object]
    aggregation_method: Literal["query_macro"]
    snapshots: tuple[BoundMetricSnapshot, ...]

    def __post_init__(self) -> None:
        if not self.snapshot_set_pub_id.startswith("mss_"):
            raise MetricSnapshotBindingError("metric_snapshot_set_id_required")
        if _SHA256.fullmatch(self.snapshot_set_hash) is None:
            raise MetricSnapshotBindingError("metric_snapshot_set_hash_invalid")
        if self.snapshot_set_state not in {"ready", "partial"}:
            raise MetricSnapshotBindingError("metric_snapshot_set_not_renderable")
        if not self.project_pub_id.startswith("prj_"):
            raise MetricSnapshotBindingError("metric_snapshot_project_invalid")
        if self.window_start > self.window_end:
            raise MetricSnapshotBindingError("metric_snapshot_window_invalid")
        if not self.snapshots:
            raise MetricSnapshotBindingError("metric_snapshot_members_required")
        if len({item.snapshot_pub_id for item in self.snapshots}) != len(self.snapshots):
            raise MetricSnapshotBindingError("metric_snapshot_member_duplicate")
        for item in self.snapshots:
            if not item.snapshot_pub_id.startswith("msn_"):
                raise MetricSnapshotBindingError("metric_snapshot_member_id_invalid")
            if _SHA256.fullmatch(item.snapshot_hash) is None:
                raise MetricSnapshotBindingError("metric_snapshot_hash_invalid")
            if not item.focal_entity_id:
                raise MetricSnapshotBindingError("metric_snapshot_focal_entity_required")
            if not item.metric_name or not item.metric_version:
                raise MetricSnapshotBindingError("metric_snapshot_member_identity_invalid")
            if item.unique_query_count < 0:
                raise MetricSnapshotBindingError("metric_snapshot_query_count_invalid")
            try:
                numerator = Decimal(item.raw_numerator)
                denominator = Decimal(item.raw_denominator)
                value = Decimal(item.value) if item.value is not None else None
                observed = Decimal(item.observed_value) if item.observed_value is not None else None
                semantic_coverage = (
                    Decimal(item.semantic_coverage) if item.semantic_coverage is not None else None
                )
            except InvalidOperation as exc:
                raise MetricSnapshotBindingError("metric_snapshot_numeric_invalid") from exc
            numeric_values = (numerator, denominator, value, observed, semantic_coverage)
            if any(number is not None and not number.is_finite() for number in numeric_values):
                raise MetricSnapshotBindingError("metric_snapshot_numeric_invalid")
            if numerator < 0 or denominator < 0:
                raise MetricSnapshotBindingError("metric_snapshot_totals_invalid")
            if semantic_coverage is not None and not 0 <= semantic_coverage <= 1:
                raise MetricSnapshotBindingError("metric_snapshot_coverage_invalid")
            if item.state not in {
                "ready",
                "limited",
                "insufficient",
                "experimental",
                "failed",
            }:
                raise MetricSnapshotBindingError("metric_snapshot_member_state_invalid")
            if _SHA256.fullmatch(item.definition_hash) is None:
                raise MetricSnapshotBindingError("metric_definition_hash_invalid")
            if _SHA256.fullmatch(item.contribution_set_hash) is None:
                raise MetricSnapshotBindingError("metric_contribution_hash_invalid")
            if _SHA256.fullmatch(item.query_contribution_set_hash) is None:
                raise MetricSnapshotBindingError("metric_query_contribution_hash_invalid")
            if _SHA256.fullmatch(item.design_contribution_set_hash) is None:
                raise MetricSnapshotBindingError("metric_design_contribution_hash_invalid")
            if item.state in {"insufficient", "experimental", "failed"} and item.value is not None:
                raise MetricSnapshotBindingError("non_publishable_metric_value_present")
            if item.state in {"ready", "limited"} and item.value is None:
                raise MetricSnapshotBindingError("publishable_metric_value_missing")
            if item.state in {"ready", "limited"} and (
                denominator <= 0 or item.unique_query_count <= 0
            ):
                raise MetricSnapshotBindingError("publishable_metric_support_missing")

    @property
    def dependency_hash(self) -> str:
        return sha256(self.canonical_json.encode()).hexdigest()

    @property
    def canonical_json(self) -> str:
        value = {
            "aggregation_method": self.aggregation_method,
            "filters": self.filters,
            "project_pub_id": self.project_pub_id,
            "snapshot_set_hash": self.snapshot_set_hash,
            "snapshot_set_pub_id": self.snapshot_set_pub_id,
            "snapshot_set_state": self.snapshot_set_state,
            "snapshots": [
                {
                    "contribution_set_hash": item.contribution_set_hash,
                    "definition_hash": item.definition_hash,
                    "design_contribution_set_hash": item.design_contribution_set_hash,
                    "focal_entity_id": item.focal_entity_id,
                    "metric_name": item.metric_name,
                    "metric_version": item.metric_version,
                    "observed_value": item.observed_value,
                    "raw_denominator": item.raw_denominator,
                    "raw_numerator": item.raw_numerator,
                    "unique_query_count": item.unique_query_count,
                    "semantic_coverage": item.semantic_coverage,
                    "snapshot_hash": item.snapshot_hash,
                    "snapshot_pub_id": item.snapshot_pub_id,
                    "query_contribution_set_hash": item.query_contribution_set_hash,
                    "state": item.state,
                    "value": item.value,
                }
                for item in sorted(self.snapshots, key=lambda row: row.snapshot_pub_id)
            ],
            "window": {"end": self.window_end.isoformat(), "start": self.window_start.isoformat()},
        }
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def bind_metric_snapshot_set(
    document: Mapping[str, Any],
    *,
    expected_project_pub_id: str,
    expected_set_pub_id: str,
    expected_set_hash: str,
    expected_window_start: date,
    expected_window_end: date,
    expected_filters: Mapping[str, object],
    required_metric_names: Sequence[str] = (),
) -> MetricSnapshotSetBinding:
    if str(document.get("snapshot_set_pub_id")) != expected_set_pub_id:
        raise MetricSnapshotBindingError("metric_snapshot_set_id_mismatch")
    if str(document.get("snapshot_set_hash")) != expected_set_hash:
        raise MetricSnapshotBindingError("metric_snapshot_set_hash_mismatch")
    if str(document.get("project_pub_id")) != expected_project_pub_id:
        raise MetricSnapshotBindingError("metric_snapshot_set_project_mismatch")
    window = document.get("window")
    if not isinstance(window, Mapping):
        raise MetricSnapshotBindingError("metric_snapshot_window_missing")
    try:
        window_start = date.fromisoformat(str(window.get("start")))
        window_end = date.fromisoformat(str(window.get("end")))
    except ValueError as exc:
        raise MetricSnapshotBindingError("metric_snapshot_window_invalid") from exc
    if (window_start, window_end) != (expected_window_start, expected_window_end):
        raise MetricSnapshotBindingError("metric_snapshot_window_mismatch")
    filters = document.get("filters")
    if not isinstance(filters, Mapping) or json.dumps(
        filters, sort_keys=True, separators=(",", ":")
    ) != json.dumps(expected_filters, sort_keys=True, separators=(",", ":")):
        raise MetricSnapshotBindingError("metric_snapshot_filters_mismatch")
    if document.get("aggregation_method") != "query_macro":
        raise MetricSnapshotBindingError("metric_snapshot_aggregation_method_invalid")
    snapshot_set_state = str(document.get("state") or "")
    if snapshot_set_state not in {"ready", "partial"}:
        raise MetricSnapshotBindingError("metric_snapshot_set_not_renderable")
    raw_snapshots = document.get("metrics")
    if not isinstance(raw_snapshots, list):
        raise MetricSnapshotBindingError("metric_snapshot_members_missing")
    if not raw_snapshots or any(not isinstance(item, Mapping) for item in raw_snapshots):
        raise MetricSnapshotBindingError("metric_snapshot_members_invalid")
    try:
        snapshots = tuple(
            BoundMetricSnapshot(
                snapshot_pub_id=str(item["snapshot_pub_id"]),
                snapshot_hash=str(item["snapshot_hash"]),
                focal_entity_id=str(item["focal_entity_id"]),
                metric_name=str(item["metric_name"]),
                metric_version=str(item["metric_version"]),
                state=str(item["state"]),  # type: ignore[arg-type]
                value=None if item.get("value") is None else str(item["value"]),
                observed_value=(
                    None if item.get("observed_value") is None else str(item["observed_value"])
                ),
                raw_numerator=str(item["raw_numerator"]),
                raw_denominator=str(item["raw_denominator"]),
                unique_query_count=int(item["unique_query_count"]),
                semantic_coverage=(
                    None
                    if not isinstance(item.get("coverage"), Mapping)
                    or item["coverage"].get("semantic") is None
                    else str(item["coverage"]["semantic"])
                ),
                definition_hash=str(item["metric_definition_hash"]),
                contribution_set_hash=str(item["contribution_set_hash"]),
                query_contribution_set_hash=str(item["query_contribution_set_hash"]),
                design_contribution_set_hash=str(item["design_contribution_set_hash"]),
            )
            for item in raw_snapshots
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MetricSnapshotBindingError("metric_snapshot_members_invalid") from exc
    if any(
        item.state in {"insufficient", "experimental", "failed"} and item.value is not None
        for item in snapshots
    ):
        raise MetricSnapshotBindingError("non_publishable_metric_value_present")
    by_name = {
        name: tuple(item for item in snapshots if item.metric_name == name)
        for name in {item.metric_name for item in snapshots}
    }
    missing = sorted(set(required_metric_names) - set(by_name))
    if missing:
        raise MetricSnapshotBindingError(
            f"metric_snapshot_required_members_missing:{','.join(missing)}"
        )
    blocked = [
        name
        for name in required_metric_names
        if all(item.state in {"insufficient", "experimental", "failed"} for item in by_name[name])
    ]
    if blocked:
        raise MetricSnapshotBindingError(
            f"metric_snapshot_required_members_not_publishable:{','.join(sorted(blocked))}"
        )
    return MetricSnapshotSetBinding(
        snapshot_set_pub_id=expected_set_pub_id,
        snapshot_set_hash=expected_set_hash,
        snapshot_set_state=snapshot_set_state,  # type: ignore[arg-type]
        project_pub_id=expected_project_pub_id,
        window_start=window_start,
        window_end=window_end,
        filters=dict(filters),
        aggregation_method=str(document.get("aggregation_method")),  # type: ignore[arg-type]
        snapshots=snapshots,
    )


def frozen_metric_projection(binding: MetricSnapshotSetBinding) -> dict[str, object]:
    """Safe render-only input; it contains no answer rows or executable formula."""

    return {
        "schema_version": "formal-report-metric-projection-v2",
        "metric_snapshot_set_pub_id": binding.snapshot_set_pub_id,
        "metric_snapshot_set_hash": binding.snapshot_set_hash,
        "metric_snapshot_set_state": binding.snapshot_set_state,
        "metric_snapshot_dependency_hash": binding.dependency_hash,
        "aggregation_method": binding.aggregation_method,
        "window": {
            "start": binding.window_start.isoformat(),
            "end": binding.window_end.isoformat(),
        },
        "filters": dict(binding.filters),
        "metrics": [
            {
                "snapshot_pub_id": item.snapshot_pub_id,
                "snapshot_hash": item.snapshot_hash,
                "focal_entity_id": item.focal_entity_id,
                "metric_name": item.metric_name,
                "metric_version": item.metric_version,
                "state": item.state,
                "value": item.value,
                "observed_value": item.observed_value,
                "raw_numerator": item.raw_numerator,
                "raw_denominator": item.raw_denominator,
                "unique_query_count": item.unique_query_count,
                "semantic_coverage": item.semantic_coverage,
                "definition_hash": item.definition_hash,
                "contribution_set_hash": item.contribution_set_hash,
                "query_contribution_set_hash": item.query_contribution_set_hash,
                "design_contribution_set_hash": item.design_contribution_set_hash,
            }
            for item in binding.snapshots
        ],
    }


def bind_frozen_metric_projection(
    projection: Mapping[str, Any],
    *,
    expected_project_pub_id: str,
    expected_set_pub_id: str,
    expected_set_hash: str,
    expected_window_start: date,
    expected_window_end: date,
    expected_filters: Mapping[str, object],
    expected_dependency_hash: str | None = None,
    required_metric_names: Sequence[str] = (),
) -> MetricSnapshotSetBinding:
    """Revalidate persisted render input without consulting answers or formulas."""

    metrics = projection.get("metrics")
    if projection.get("schema_version") != "formal-report-metric-projection-v2":
        raise MetricSnapshotBindingError("metric_snapshot_projection_version_invalid")
    if not isinstance(metrics, list):
        raise MetricSnapshotBindingError("metric_snapshot_members_missing")
    document = {
        "snapshot_set_pub_id": projection.get("metric_snapshot_set_pub_id"),
        "snapshot_set_hash": projection.get("metric_snapshot_set_hash"),
        "state": projection.get("metric_snapshot_set_state"),
        "project_pub_id": expected_project_pub_id,
        "window": projection.get("window"),
        "filters": projection.get("filters"),
        "aggregation_method": projection.get("aggregation_method"),
        "metrics": [
            {
                **dict(item),
                "metric_definition_hash": item.get("definition_hash"),
                "coverage": {"semantic": item.get("semantic_coverage")},
            }
            if isinstance(item, Mapping)
            else item
            for item in metrics
        ],
    }
    binding = bind_metric_snapshot_set(
        document,
        expected_project_pub_id=expected_project_pub_id,
        expected_set_pub_id=expected_set_pub_id,
        expected_set_hash=expected_set_hash,
        expected_window_start=expected_window_start,
        expected_window_end=expected_window_end,
        expected_filters=expected_filters,
        required_metric_names=required_metric_names,
    )
    recorded_dependency_hash = str(projection.get("metric_snapshot_dependency_hash") or "")
    if recorded_dependency_hash != binding.dependency_hash:
        raise MetricSnapshotBindingError("metric_snapshot_dependency_hash_mismatch")
    if (
        expected_dependency_hash is not None
        and recorded_dependency_hash != expected_dependency_hash
    ):
        raise MetricSnapshotBindingError("metric_snapshot_dependency_hash_mismatch")
    return binding


__all__ = [
    "BoundMetricSnapshot",
    "MetricSnapshotBindingError",
    "MetricSnapshotSetBinding",
    "bind_frozen_metric_projection",
    "bind_metric_snapshot_set",
    "frozen_metric_projection",
]
