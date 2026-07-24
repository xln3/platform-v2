from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any


@dataclass(frozen=True, slots=True)
class ReportFreeze:
    window_start: datetime
    window_end: datetime
    filters: Mapping[str, Any]
    metric_version: str
    scorer_version: str
    fact_snapshot_hash: str
    filter_hash: str


def freeze_report(
    *,
    window_start: datetime,
    window_end: datetime,
    filters: Mapping[str, Any],
    metric_version: str,
    scorer_version: str,
    fact_rows: Iterable[Mapping[str, Any]],
) -> ReportFreeze:
    if window_end <= window_start:
        raise ValueError("report window must be non-empty")
    filter_json = json.dumps(filters, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    rows = sorted(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        for row in fact_rows
    )
    snapshot_material = "\n".join([metric_version, scorer_version, filter_json, *rows])
    return ReportFreeze(
        window_start=window_start,
        window_end=window_end,
        filters=dict(filters),
        metric_version=metric_version,
        scorer_version=scorer_version,
        fact_snapshot_hash=sha256(snapshot_material.encode()).hexdigest(),
        filter_hash=sha256(filter_json.encode()).hexdigest(),
    )


def verify_frozen_report(freeze: ReportFreeze, fact_rows: Iterable[Mapping[str, Any]]) -> None:
    recomputed = freeze_report(
        window_start=freeze.window_start,
        window_end=freeze.window_end,
        filters=freeze.filters,
        metric_version=freeze.metric_version,
        scorer_version=freeze.scorer_version,
        fact_rows=fact_rows,
    )
    if recomputed.fact_snapshot_hash != freeze.fact_snapshot_hash:
        raise ValueError("report facts drifted after freeze")
