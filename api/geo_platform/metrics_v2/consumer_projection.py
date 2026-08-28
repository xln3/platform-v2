"""Read-only projections shared by every formal metrics consumer.

This module is deliberately downstream of :class:`MetricsV2Repository`.  It
never imports a V1 calculator, ``answer_analysis`` model, or a model client.
All values are copied from an ``official`` immutable snapshot set or derived
from that set's persisted contribution rows.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any, Literal, Protocol


class OfficialMetricsRepository(Protocol):
    def current_snapshot_set(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        start: str | None = None,
        end: str | None = None,
        models: Sequence[str] = (),
        regions: Sequence[str] = (),
        modes: Sequence[str] = (),
        focal_entity_ids: Sequence[str] = (),
        publication_channel: str = "official",
    ) -> dict[str, Any]: ...

    def list_contributions(
        self,
        *,
        tenant_pub_id: str,
        snapshot_pub_id: str,
        cursor: str | None = None,
        limit: int = 50,
        eligibility_status: str | None = None,
        reason_code: str | None = None,
        query: str | None = None,
        model: str | None = None,
        region: str | None = None,
        mode: str | None = None,
        hit: bool | None = None,
    ) -> dict[str, Any]: ...

    def list_query_contributions(
        self,
        *,
        tenant_pub_id: str,
        snapshot_pub_id: str,
        cursor: str | None = None,
        limit: int = 50,
        query: str | None = None,
    ) -> dict[str, Any]: ...


def _canonical_hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _decimal(value: object) -> Decimal:
    if value is None:
        return Decimal(0)
    return Decimal(str(value))


def _serialized(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _serialized(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_serialized(item) for item in value]
    return value


@dataclass(frozen=True)
class OfficialScope:
    tenant_pub_id: str
    project_pub_id: str
    start: date | None = None
    end: date | None = None
    models: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    modes: tuple[str, ...] = ()
    focal_entity_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.start is None) != (self.end is None):
            raise ValueError("official_metrics_window_incomplete")
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("official_metrics_window_invalid")


class OfficialMetricsConsumer:
    """Project formal views from one V2 publication channel.

    There is no fallback argument by design.  Missing official publication is
    surfaced as ``LookupError`` by the repository and must be rendered as
    building/insufficient by the caller.
    """

    def __init__(self, repository: OfficialMetricsRepository) -> None:
        self.repository = repository

    def snapshot_set(self, scope: OfficialScope) -> dict[str, Any]:
        return self.repository.current_snapshot_set(
            tenant_pub_id=scope.tenant_pub_id,
            project_pub_id=scope.project_pub_id,
            start=scope.start.isoformat() if scope.start else None,
            end=scope.end.isoformat() if scope.end else None,
            models=tuple(sorted(set(scope.models))),
            regions=tuple(sorted(set(scope.regions))),
            modes=tuple(sorted(set(scope.modes))),
            focal_entity_ids=tuple(sorted(set(scope.focal_entity_ids))),
            publication_channel="official",
        )

    @staticmethod
    def _binding(snapshot_set: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "publication_channel": "official",
            "snapshot_set_pub_id": snapshot_set["snapshot_set_pub_id"],
            "snapshot_set_hash": snapshot_set["snapshot_set_hash"],
            "scope_hash": snapshot_set["scope_hash"],
            "dependency_bundle_hash": snapshot_set["dependency_bundle_hash"],
            "as_of": snapshot_set["as_of"],
            "window": snapshot_set["window"],
            "filters": snapshot_set["filters"],
            "aggregation_method": snapshot_set["aggregation_method"],
            "design_basis": snapshot_set["design_basis"],
            "state": snapshot_set["state"],
        }

    def overview(self, scope: OfficialScope) -> dict[str, Any]:
        snapshot_set = self.snapshot_set(scope)
        return {
            "schema_version": "official-metric-overview-v2",
            "project_pub_id": scope.project_pub_id,
            "binding": self._binding(snapshot_set),
            "metrics": list(snapshot_set["metrics"]),
        }

    def brandrank(self, scope: OfficialScope) -> dict[str, Any]:
        overview = self.overview(scope)
        by_entity: dict[str, list[dict[str, Any]]] = {}
        for raw_metric in overview["metrics"]:
            metric = dict(raw_metric)
            name = str(metric["metric_name"])
            if not any(
                token in name
                for token in ("mention", "recommend", "rank", "entity_share", "pairwise")
            ):
                continue
            by_entity.setdefault(str(metric["focal_entity_id"]), []).append(metric)
        return {
            "schema_version": "brand-visibility-official-v2",
            "project_pub_id": scope.project_pub_id,
            "binding": overview["binding"],
            "entities": [
                {"focal_entity_id": entity, "metrics": metrics}
                for entity, metrics in sorted(by_entity.items())
            ],
        }

    def _all_pages(
        self,
        *,
        tenant_pub_id: str,
        snapshot_pub_id: str,
        query_level: bool,
    ) -> list[dict[str, Any]]:
        cursor: str | None = None
        rows: list[dict[str, Any]] = []
        for _page_number in range(1_000):
            if query_level:
                page = self.repository.list_query_contributions(
                    tenant_pub_id=tenant_pub_id,
                    snapshot_pub_id=snapshot_pub_id,
                    cursor=cursor,
                    limit=100,
                    query=None,
                )
            else:
                page = self.repository.list_contributions(
                    tenant_pub_id=tenant_pub_id,
                    snapshot_pub_id=snapshot_pub_id,
                    cursor=cursor,
                    limit=100,
                    eligibility_status=None,
                    reason_code=None,
                    query=None,
                    model=None,
                    region=None,
                    mode=None,
                    hit=None,
                )
            rows.extend(dict(item) for item in page["data"])
            if not page["has_more"]:
                return rows
            cursor = page.get("next_cursor")
            if not cursor:
                raise RuntimeError("official_metrics_cursor_missing")
        raise RuntimeError("official_metrics_projection_page_limit")

    @staticmethod
    def _answer_group_key(
        contribution: Mapping[str, Any], group_by: str
    ) -> tuple[str, dict[str, Any]]:
        if group_by == "day":
            capture_time = contribution["capture_time"]
            key = (
                capture_time.date().isoformat()
                if isinstance(capture_time, datetime)
                else str(capture_time)[:10]
            )
            return key, {"day": key}
        if group_by == "model":
            value = str(contribution.get("model") or "unknown")
            return value, {"model": value}
        if group_by == "region_mode":
            region = str(contribution.get("region") or "unknown")
            mode = str(contribution.get("mode") or "unknown")
            return f"{region}\0{mode}", {"region": region, "mode": mode}
        raise ValueError("official_metrics_breakdown_invalid_group")

    def breakdown(
        self,
        scope: OfficialScope,
        *,
        group_by: Literal["day", "model", "region_mode", "question"],
    ) -> dict[str, Any]:
        snapshot_set = self.snapshot_set(scope)
        rows: list[dict[str, Any]] = []
        for metric in snapshot_set["metrics"]:
            snapshot_pub_id = str(metric["snapshot_pub_id"])
            if group_by == "question":
                contributions = self._all_pages(
                    tenant_pub_id=scope.tenant_pub_id,
                    snapshot_pub_id=snapshot_pub_id,
                    query_level=True,
                )
                for contribution in contributions:
                    rows.append(
                        {
                            "snapshot_pub_id": snapshot_pub_id,
                            "metric_name": metric["metric_name"],
                            "metric_version": metric["metric_version"],
                            "focal_entity_id": metric["focal_entity_id"],
                            "group_by": group_by,
                            "group": {
                                "query_key": contribution["query_key"],
                                "query_pub_id": contribution.get("query_pub_id"),
                                "query_text": contribution.get("query_text"),
                            },
                            "value": contribution.get("value"),
                            "numerator": contribution["numerator"],
                            "denominator": contribution["denominator"],
                            "unknown_weight": contribution["unknown_weight"],
                            "answer_count": contribution["answer_count"],
                            "contribution_hash": contribution["contribution_hash"],
                        }
                    )
                continue
            contributions = self._all_pages(
                tenant_pub_id=scope.tenant_pub_id,
                snapshot_pub_id=snapshot_pub_id,
                query_level=False,
            )
            groups: dict[str, dict[str, Any]] = {}
            for contribution in contributions:
                key, dimensions = self._answer_group_key(contribution, group_by)
                bucket = groups.setdefault(
                    key,
                    {
                        "group": dimensions,
                        "weighted_numerator": Decimal(0),
                        "weighted_denominator": Decimal(0),
                        "candidate_answer_count": 0,
                        "known_answer_count": 0,
                        "unknown_answer_count": 0,
                        "failed_answer_count": 0,
                        "contribution_hashes": [],
                    },
                )
                bucket["weighted_numerator"] += _decimal(contribution.get("weighted_numerator"))
                bucket["weighted_denominator"] += _decimal(contribution.get("weighted_denominator"))
                bucket["candidate_answer_count"] += 1
                if contribution.get("eligibility_status") == "analysis_unknown":
                    bucket["unknown_answer_count"] += 1
                elif contribution.get("eligibility_status") == "analysis_failed":
                    bucket["failed_answer_count"] += 1
                elif contribution.get("eligibility_status") in {
                    "included_hit",
                    "included_miss",
                }:
                    bucket["known_answer_count"] += 1
                bucket["contribution_hashes"].append(contribution["contribution_hash"])
            for key in sorted(groups):
                bucket = groups[key]
                denominator = bucket["weighted_denominator"]
                rows.append(
                    {
                        "snapshot_pub_id": snapshot_pub_id,
                        "metric_name": metric["metric_name"],
                        "metric_version": metric["metric_version"],
                        "focal_entity_id": metric["focal_entity_id"],
                        "group_by": group_by,
                        "group": bucket["group"],
                        "value": (
                            float(bucket["weighted_numerator"] / denominator)
                            if denominator
                            else None
                        ),
                        "weighted_numerator": float(bucket["weighted_numerator"]),
                        "weighted_denominator": float(denominator),
                        "candidate_answer_count": bucket["candidate_answer_count"],
                        "known_answer_count": bucket["known_answer_count"],
                        "unknown_answer_count": bucket["unknown_answer_count"],
                        "failed_answer_count": bucket["failed_answer_count"],
                        "contribution_hash": _canonical_hash(sorted(bucket["contribution_hashes"])),
                    }
                )
        return {
            "schema_version": "official-metric-breakdown-v2",
            "project_pub_id": scope.project_pub_id,
            "binding": self._binding(snapshot_set),
            "group_by": group_by,
            "rows": rows,
        }

    def delta(self, scope: OfficialScope) -> dict[str, Any]:
        if scope.start is None or scope.end is None:
            raise ValueError("official_metrics_delta_window_required")
        days = (scope.end - scope.start).days + 1
        previous_end = scope.start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=days - 1)
        left_scope = OfficialScope(
            tenant_pub_id=scope.tenant_pub_id,
            project_pub_id=scope.project_pub_id,
            start=previous_start,
            end=previous_end,
            models=scope.models,
            regions=scope.regions,
            modes=scope.modes,
            focal_entity_ids=scope.focal_entity_ids,
        )
        left = self.snapshot_set(left_scope)
        right = self.snapshot_set(scope)
        left_metrics = {
            (
                row["metric_name"],
                row["metric_version"],
                row["metric_definition_hash"],
                row["focal_entity_id"],
            ): row
            for row in left["metrics"]
        }
        right_metrics = {
            (
                row["metric_name"],
                row["metric_version"],
                row["metric_definition_hash"],
                row["focal_entity_id"],
            ): row
            for row in right["metrics"]
        }
        deltas: list[dict[str, Any]] = []
        for key in sorted(set(left_metrics) | set(right_metrics)):
            left_metric = left_metrics.get(key)
            right_metric = right_metrics.get(key)
            reason_codes: list[str] = []
            if left_metric is None:
                reason_codes.append("metric_missing_in_baseline")
            if right_metric is None:
                reason_codes.append("metric_missing_in_retest")
            if left_metric is not None and right_metric is not None:
                if left_metric["state"] != "ready" or right_metric["state"] != "ready":
                    reason_codes.append("metric_not_ready")
                # The current public contract exposes a frozen design-cell set
                # hash but not an ad-hoc pairing API.  Equality is the only
                # condition under which this read path can prove common support.
                if (
                    left_metric["design_contribution_set_hash"]
                    != right_metric["design_contribution_set_hash"]
                ):
                    reason_codes.append("common_support_not_identical")
            left_value = left_metric.get("value") if left_metric else None
            right_value = right_metric.get("value") if right_metric else None
            delta_value = (
                float(_decimal(right_value) - _decimal(left_value))
                if not reason_codes and left_value is not None and right_value is not None
                else None
            )
            common_support_hash = (
                _canonical_hash(
                    {
                        "design_contribution_set_hash": left_metric["design_contribution_set_hash"],
                        "metric": key,
                    }
                )
                if left_metric is not None and right_metric is not None and not reason_codes
                else None
            )
            deltas.append(
                {
                    "metric_name": key[0],
                    "metric_version": key[1],
                    "metric_definition_hash": key[2],
                    "focal_entity_id": key[3],
                    "baseline_snapshot_pub_id": (
                        left_metric["snapshot_pub_id"] if left_metric else None
                    ),
                    "retest_snapshot_pub_id": (
                        right_metric["snapshot_pub_id"] if right_metric else None
                    ),
                    "baseline_value": left_value,
                    "retest_value": right_value,
                    "paired_delta": delta_value,
                    "common_support_hash": common_support_hash,
                    "state": "ready" if not reason_codes else "incompatible",
                    "reason_codes": reason_codes,
                }
            )
        return {
            "schema_version": "official-metric-delta-v2",
            "project_pub_id": scope.project_pub_id,
            "baseline_binding": self._binding(left),
            "retest_binding": self._binding(right),
            "paired_metric_delta": deltas,
            "composition_changed": any(
                "common_support_not_identical" in row["reason_codes"] for row in deltas
            ),
            "comparison_hash": _canonical_hash(_serialized(deltas)),
        }


__all__ = ["OfficialMetricsConsumer", "OfficialMetricsRepository", "OfficialScope"]
