from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import product
from typing import Any


@dataclass(frozen=True)
class SamplingPlanItem:
    group_index: int
    group_name: str
    variant_index: int
    query_text: str


@dataclass(frozen=True)
class SamplingConfig:
    pub_id: str
    revision: int
    items: tuple[SamplingPlanItem, ...]
    models: tuple[str, ...]
    regions: tuple[str, ...]
    modes: tuple[str, ...]

    @property
    def query_texts(self) -> frozenset[str]:
        return frozenset(item.query_text for item in self.items)


@dataclass(frozen=True)
class SamplingColumn:
    key: str
    model: str
    region: str
    mode: str


_MODEL_ORDER = {
    "doubao": 0,
    "deepseek": 1,
    "yiyan": 2,
    "tongyi": 3,
    "yuanbao": 4,
}
_MODE_ORDER = {"normal": 0, "deep_think": 1}


def _string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip() and item not in result:
            result.append(item)
    return tuple(result)


def parse_sampling_configs(rows: Iterable[Mapping[str, Any]]) -> list[SamplingConfig]:
    """Project frozen-config rows (newest first) -> sampling plans.

    Frozen snapshots are internal data, but the read path still treats malformed legacy
    JSON as unavailable instead of letting one bad revision break the whole page.
    """

    configs: list[SamplingConfig] = []
    for row in rows:
        raw = row.get("snapshot_json")
        try:
            snapshot = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        if not isinstance(snapshot, dict):
            continue
        raw_groups = snapshot.get("query_groups")
        if not isinstance(raw_groups, list):
            continue
        items: list[SamplingPlanItem] = []
        for group_index, raw_group in enumerate(raw_groups, start=1):
            if not isinstance(raw_group, dict):
                continue
            group_name_value = raw_group.get("name")
            group_name = (
                group_name_value
                if isinstance(group_name_value, str) and group_name_value.strip()
                else f"候选组 {group_index}"
            )
            raw_items = raw_group.get("items")
            if not isinstance(raw_items, list):
                continue
            for variant_index, raw_item in enumerate(raw_items):
                if not isinstance(raw_item, dict):
                    continue
                query_text = raw_item.get("text")
                if not isinstance(query_text, str) or not query_text.strip():
                    continue
                items.append(
                    SamplingPlanItem(
                        group_index=group_index,
                        group_name=group_name,
                        variant_index=variant_index,
                        query_text=query_text,
                    )
                )
        pub_id = row.get("pub_id")
        revision = row.get("revision")
        if (
            not items
            or not isinstance(pub_id, str)
            or not isinstance(revision, int)
            or isinstance(revision, bool)
        ):
            continue
        configs.append(
            SamplingConfig(
                pub_id=pub_id,
                revision=revision,
                items=tuple(items),
                models=_string_list(snapshot.get("models")),
                regions=_string_list(snapshot.get("regions")),
                modes=_string_list(snapshot.get("modes")),
            )
        )
    return configs


def select_sampling_campaign(
    configs: list[SamplingConfig],
) -> tuple[SamplingConfig | None, list[SamplingConfig]]:
    """Select the latest logical sampling batch from revision-ordered configs.

    A normal launch has one full plan. Formal collection can split that plan by platform/
    region, then add nested or disjoint small top-up configs. Within the current mode block,
    the largest revision whose query set contains every newer query is the canonical plan.
    Older adjacent copies of that exact plan are the other sampling legs. Small top-ups may
    deliberately change mode (for example, 豆包专家额度耗尽后改走快速)，so mode alone
    cannot end the baseline search: containment still ties such a top-up to the full plan.
    Once the baseline is found, only its exact-plan/mode peers are extended further backward.
    """

    if not configs:
        return None, []
    latest = configs[0]
    accumulated: set[str] = set()
    baseline_index = 0
    baseline_size = len(latest.query_texts)
    for index, candidate in enumerate(configs):
        candidate_queries = set(candidate.query_texts)
        accumulated.update(candidate_queries)
        if accumulated.issubset(candidate_queries) and len(candidate_queries) > baseline_size:
            baseline_index = index
            baseline_size = len(candidate_queries)

    baseline = configs[baseline_index]
    baseline_modes = frozenset(baseline.modes)
    selected = list(configs[: baseline_index + 1])
    for candidate in configs[baseline_index + 1 :]:
        if (
            frozenset(candidate.modes) != baseline_modes
            or candidate.query_texts != baseline.query_texts
        ):
            break
        selected.append(candidate)
    return baseline, selected


def sampling_plan_items(config: SamplingConfig) -> list[SamplingPlanItem]:
    """Return display rows while removing indistinguishable duplicate query texts."""

    seen: set[str] = set()
    items: list[SamplingPlanItem] = []
    for item in config.items:
        if item.query_text in seen:
            continue
        seen.add(item.query_text)
        items.append(item)
    return items


def sampling_columns(configs: Iterable[SamplingConfig]) -> list[SamplingColumn]:
    combinations: set[tuple[str, str, str]] = set()
    region_order: dict[str, int] = {}
    unknown_model_order: dict[str, int] = {}
    unknown_mode_order: dict[str, int] = {}
    # Oldest full-plan legs usually preserve the user's region order most faithfully.
    for config in reversed(list(configs)):
        for region in config.regions:
            region_order.setdefault(region, len(region_order))
        for model in config.models:
            if model not in _MODEL_ORDER:
                unknown_model_order.setdefault(model, len(unknown_model_order))
        for mode in config.modes:
            if mode not in _MODE_ORDER:
                unknown_mode_order.setdefault(mode, len(unknown_mode_order))
        combinations.update(product(config.models, config.regions, config.modes))

    def sort_key(value: tuple[str, str, str]) -> tuple[int, int, int, int, int]:
        model, region, mode = value
        return (
            _MODEL_ORDER.get(model, len(_MODEL_ORDER)),
            unknown_model_order.get(model, 0),
            region_order.get(region, len(region_order)),
            _MODE_ORDER.get(mode, len(_MODE_ORDER)),
            unknown_mode_order.get(mode, 0),
        )

    return [
        SamplingColumn(key=f"leg-{index}", model=model, region=region, mode=mode)
        for index, (model, region, mode) in enumerate(sorted(combinations, key=sort_key), start=1)
    ]


def variant_label(index: int) -> str:
    if index == 0:
        return "原词/优化句"
    if 1 <= index <= 26:
        return f"变体{chr(ord('A') + index - 1)}"
    return f"变体{index}"


def uses_quotation_appendices(items: list[SamplingPlanItem]) -> bool:
    group_sizes: dict[int, int] = {}
    for item in items:
        group_sizes[item.group_index] = group_sizes.get(item.group_index, 0) + 1
    return len(group_sizes) == 34 and all(size == 4 for size in group_sizes.values())
