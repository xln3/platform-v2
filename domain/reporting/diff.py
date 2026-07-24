from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import unified_diff


@dataclass(frozen=True, slots=True)
class ReportVersionDiff:
    before_version: int
    after_version: int
    unified_diff: str
    changed_component_count: int


def compare_report_versions(
    *,
    before_version: int,
    after_version: int,
    before_components: Sequence[Mapping[str, object]],
    after_components: Sequence[Mapping[str, object]],
) -> ReportVersionDiff:
    if after_version <= before_version:
        raise ValueError("report version diff must move forward")
    before_lines = [
        json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        for item in before_components
    ]
    after_lines = [
        json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        for item in after_components
    ]
    changed = sum(
        before != after for before, after in zip(before_lines, after_lines, strict=False)
    ) + abs(len(before_lines) - len(after_lines))
    return ReportVersionDiff(
        before_version=before_version,
        after_version=after_version,
        unified_diff="\n".join(
            unified_diff(
                before_lines,
                after_lines,
                fromfile=f"version-{before_version}",
                tofile=f"version-{after_version}",
                lineterm="",
            )
        ),
        changed_component_count=changed,
    )
