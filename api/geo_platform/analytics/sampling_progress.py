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
    # ``mode`` is the mode declared by the complete formal plan. ``modes`` also
    # includes any effective fallback modes observed in partial top-up configs.
    mode: str
    modes: tuple[str, ...]


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
    *,
    baseline_pub_id: str | None = None,
) -> tuple[SamplingConfig | None, list[SamplingConfig]]:
    """Select the latest logical sampling batch from revision-ordered configs.

    New projects register an explicit answer-library catalog. Its config version is the
    authoritative row definition, and every in-window config that intersects that definition
    can contribute observations. The containment walk below is only a legacy fallback.

    A normal launch has one full plan. Formal collection can split that plan by platform/
    region, then add nested or disjoint small top-up configs. Other short-lived campaigns can
    be interleaved before collection returns to that plan, so an unrelated revision must not
    poison the whole backward search. Starting from the newest config, follow only nested
    supersets until the canonical full plan is found, then retain revisions whose queries are
    contained by that plan. Older formal legs may be separated by small canary/top-up configs,
    so the backward walk crosses those revisions and stops only when it reaches another
    full-sized plan. Mode changes remain valid within a campaign (for example, 豆包专家额度
    耗尽后改走快速).
    """

    if not configs:
        return None, []
    if baseline_pub_id is not None:
        baseline = next((config for config in configs if config.pub_id == baseline_pub_id), None)
        if baseline is None:
            return None, []
        return baseline, [
            candidate
            for candidate in configs
            if not candidate.query_texts.isdisjoint(baseline.query_texts)
        ]

    latest = configs[0]
    baseline_index = 0
    lineage_queries = latest.query_texts
    for index, candidate in enumerate(configs):
        candidate_queries = candidate.query_texts
        if lineage_queries < candidate_queries:
            baseline_index = index
            lineage_queries = candidate_queries

    baseline = configs[baseline_index]
    baseline_modes = frozenset(baseline.modes)
    selected = [
        candidate
        for candidate in configs[: baseline_index + 1]
        if candidate.query_texts.issubset(baseline.query_texts)
    ]
    for candidate in configs[baseline_index + 1 :]:
        candidate_queries = candidate.query_texts
        if candidate_queries == baseline.query_texts:
            if frozenset(candidate.modes) != baseline_modes:
                break
            selected.append(candidate)
            continue
        if candidate_queries < baseline.query_texts:
            selected.append(candidate)
            continue
        # A small unrelated canary may be interleaved between formal legs. It must not
        # hide an older platform/region leg for the same complete query plan. A candidate
        # at least as large as the baseline is the first deterministic boundary available
        # to legacy projects that do not have an explicit answer-library catalog.
        if len(candidate_queries) >= len(baseline.query_texts):
            break
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


def sampling_columns(
    configs: Iterable[SamplingConfig],
    *,
    baseline: SamplingConfig | None = None,
) -> list[SamplingColumn]:
    """Return formal sampling legs while retaining effective fallback modes.

    A mode in a partial top-up config is an implementation detail, not a new target
    dimension. Formal columns therefore come only from configs containing the complete
    catalog query set. If a complete plan genuinely declares two modes, both remain
    distinct formal columns. A different mode from a partial config can be attached as a
    fallback only when the model/region has exactly one formal mode, which avoids guessing
    between real dual-mode targets.
    """

    config_list = list(configs)
    if not config_list:
        return []
    if baseline is None:
        baseline = max(config_list, key=lambda config: len(config.query_texts))
    full_configs = [config for config in config_list if config.query_texts == baseline.query_texts]

    planned_combinations: set[tuple[str, str, str]] = set()
    region_order: dict[str, int] = {}
    unknown_model_order: dict[str, int] = {}
    unknown_mode_order: dict[str, int] = {}
    # Oldest full-plan legs preserve the user's intended display order most faithfully.
    for config in reversed(full_configs):
        for region in config.regions:
            region_order.setdefault(region, len(region_order))
        for model in config.models:
            if model not in _MODEL_ORDER:
                unknown_model_order.setdefault(model, len(unknown_model_order))
        for mode in config.modes:
            if mode not in _MODE_ORDER:
                unknown_mode_order.setdefault(mode, len(unknown_mode_order))
        planned_combinations.update(product(config.models, config.regions, config.modes))

    planned_modes_by_leg: dict[tuple[str, str], set[str]] = {}
    for model, region, mode in planned_combinations:
        planned_modes_by_leg.setdefault((model, region), set()).add(mode)

    effective_modes: dict[tuple[str, str, str], list[str]] = {
        combination: [combination[2]] for combination in planned_combinations
    }
    for config in reversed(config_list):
        if config in full_configs:
            continue
        for model, region, effective_mode in product(config.models, config.regions, config.modes):
            planned_modes = planned_modes_by_leg.get((model, region), set())
            if effective_mode in planned_modes:
                target = (model, region, effective_mode)
            elif len(planned_modes) == 1:
                target = (model, region, next(iter(planned_modes)))
            else:
                # A partial config must neither create a formal leg nor be guessed into
                # one of multiple genuine planned modes.
                continue
            if effective_mode not in effective_modes[target]:
                effective_modes[target].append(effective_mode)

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
        SamplingColumn(
            key=f"leg-{index}",
            model=model,
            region=region,
            mode=mode,
            modes=tuple(effective_modes[(model, region, mode)]),
        )
        for index, (model, region, mode) in enumerate(
            sorted(planned_combinations, key=sort_key), start=1
        )
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
