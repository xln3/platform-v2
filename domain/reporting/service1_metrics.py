"""Answer-denominator metrics for governed Service-1 entities."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from statistics import mean
from typing import Any


def wilson_interval(successes: int, total: int, *, z: float = 1.96) -> tuple[float, float] | None:
    if total <= 0:
        return None
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return round(max(0.0, centre - margin) * 100, 1), round(min(1.0, centre + margin) * 100, 1)


def _entity_map(sample: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(row.get("canonical_name") or ""): row
        for row in sample.get("entities", [])
        if isinstance(row, Mapping) and row.get("canonical_name")
    }


def entity_metric(samples: Sequence[Mapping[str, Any]], canonical_name: str) -> dict[str, Any]:
    ranks: list[int] = []
    for sample in samples:
        row = _entity_map(sample).get(canonical_name)
        if row is not None and isinstance(row.get("answer_rank"), int):
            ranks.append(int(row["answer_rank"]))
    total = len(samples)
    mentions = len(ranks)
    top_counts = {str(value): sum(rank <= value for rank in ranks) for value in (1, 3, 5)}
    interval = wilson_interval(mentions, total)
    return {
        "canonical_name": canonical_name,
        "answers": total,
        "mentions": mentions,
        "mention_rate": round(mentions / total * 100, 1) if total else 0.0,
        "mention_rate_fraction": f"{mentions}/{total}",
        "mention_rate_wilson_95": list(interval) if interval else None,
        "avg_rank": round(mean(ranks), 1) if ranks else None,
        "best_rank": min(ranks) if ranks else None,
        "top_counts": top_counts,
        "top_rates": {
            key: round(count / total * 100, 1) if total else 0.0
            for key, count in top_counts.items()
        },
        "visibility_index": (
            round((mentions / total) / mean(ranks) * 100, 1) if total and ranks else 0.0
        ),
        "visibility_index_formula": "100 × (提及回答数 ÷ 全部回答数) ÷ 提及时平均位次",
        "visibility_index_scope": "本批自定义可见性指数；仅用于本批同口径实体，不跨批比较",
    }


def ranked_entities(samples: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    occurrences: Counter[str] = Counter()
    ranks: dict[str, list[int]] = defaultdict(list)
    metadata: dict[str, Mapping[str, Any]] = {}
    for sample in samples:
        for row in _entity_map(sample).values():
            canonical = str(row["canonical_name"])
            rank = row.get("answer_rank")
            if not isinstance(rank, int):
                continue
            occurrences[canonical] += 1
            ranks[canonical].append(rank)
            metadata.setdefault(canonical, row)
    rows = []
    for canonical, _count in occurrences.items():
        metric = entity_metric(samples, canonical)
        meta = metadata[canonical]
        rows.append(
            {
                **metric,
                "entity_type": str(meta.get("entity_type") or "unknown"),
                "competitor_eligible": bool(meta.get("competitor_eligible")),
                "brand_level": str(meta.get("brand_level") or "unclassified"),
                "parent_brand": meta.get("parent_brand"),
                "raw_aliases": sorted(
                    {
                        str(alias)
                        for sample in samples
                        for entity in sample.get("entities", [])
                        if isinstance(entity, Mapping) and entity.get("canonical_name") == canonical
                        for alias in entity.get("raw_aliases", [])
                    }
                ),
            }
        )
    rows.sort(
        key=lambda row: (
            -int(row["mentions"]),
            float(row["avg_rank"] if row["avg_rank"] is not None else 10**9),
            str(row["canonical_name"]),
        )
    )
    for rank, row in enumerate(rows, 1):
        row["batch_rank"] = rank
    return rows


def comparable_competitors(
    samples: Sequence[Mapping[str, Any]],
    *,
    target_brand: str,
    limit: int = 5,
) -> dict[str, Any]:
    all_rows = ranked_entities(samples)
    competitors = [
        row
        for row in all_rows
        if row["competitor_eligible"] and row["canonical_name"] != target_brand
    ][:limit]
    names = [target_brand, *[str(row["canonical_name"]) for row in competitors]]
    scope_buckets: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for sample in samples:
        scope_buckets[
            (str(sample.get("question") or ""), str(sample.get("platform") or ""))
        ].append(sample)
    detail = []
    for (question, platform), scoped in sorted(scope_buckets.items()):
        target = entity_metric(scoped, target_brand)
        for competitor in names[1:]:
            other = entity_metric(scoped, competitor)
            detail.append(
                {
                    "question": question,
                    "platform": platform,
                    "answers": len(scoped),
                    "target_brand": target_brand,
                    "competitor": competitor,
                    "target_mentions": target["mentions"],
                    "competitor_mentions": other["mentions"],
                    "mention_rate_gap_pp": round(
                        float(target["mention_rate"]) - float(other["mention_rate"]), 1
                    ),
                    "target_top3": target["top_counts"]["3"],
                    "competitor_top3": other["top_counts"]["3"],
                    "top3_rate_gap_pp": round(
                        float(target["top_rates"]["3"]) - float(other["top_rates"]["3"]), 1
                    ),
                    "target_avg_rank": target["avg_rank"],
                    "competitor_avg_rank": other["avg_rank"],
                    "avg_rank_gap": (
                        round(float(target["avg_rank"]) - float(other["avg_rank"]), 1)
                        if target["avg_rank"] is not None and other["avg_rank"] is not None
                        else None
                    ),
                }
            )
    return {
        "target": entity_metric(samples, target_brand),
        "competitors": [entity_metric(samples, name) for name in names[1:]],
        "same_question_platform": detail,
    }


def repeat_consistency(
    samples: Sequence[Mapping[str, Any]], *, target_brand: str
) -> dict[str, Any]:
    cells: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for sample in samples:
        cells[
            (
                str(sample.get("question") or ""),
                str(sample.get("platform") or ""),
                str(sample.get("region") or ""),
            )
        ].append(sample)
    complete = 0
    mention_agree = 0
    both_mentioned = 0
    rank_deltas: list[int] = []
    details = []
    for cell, rows in sorted(cells.items()):
        ordered = sorted(rows, key=lambda row: int(row.get("repeat_no") or 0))
        if len(ordered) != 2 or {row.get("repeat_no") for row in ordered} != {1, 2}:
            continue
        complete += 1
        target_rows = [_entity_map(row).get(target_brand) for row in ordered]
        flags = [row is not None for row in target_rows]
        agrees = flags[0] == flags[1]
        mention_agree += int(agrees)
        delta = None
        repeat_1, repeat_2 = target_rows
        if repeat_1 is not None and repeat_2 is not None:
            both_mentioned += 1
            delta = abs(int(repeat_1["answer_rank"]) - int(repeat_2["answer_rank"]))
            rank_deltas.append(delta)
        details.append(
            {
                "question": cell[0],
                "platform": cell[1],
                "region": cell[2],
                "mention_agreement": agrees,
                "repeat_1_rank": target_rows[0].get("answer_rank") if target_rows[0] else None,
                "repeat_2_rank": target_rows[1].get("answer_rank") if target_rows[1] else None,
                "absolute_rank_delta": delta,
            }
        )
    expected = len(cells)
    return {
        "complete_pairs": complete,
        "expected_pairs": expected,
        "mention_agreement_pairs": mention_agree,
        "mention_agreement_rate": round(mention_agree / complete * 100, 1) if complete else None,
        "both_mentioned_pairs": both_mentioned,
        "mean_absolute_rank_delta": round(mean(rank_deltas), 1) if rank_deltas else None,
        "details": details,
    }


def source_cooccurrence(
    samples: Sequence[Mapping[str, Any]], *, target_brand: str
) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for sample in samples:
        mentioned = target_brand in _entity_map(sample)
        hosts = {
            str(citation.get("host") or "").lower()
            for citation in sample.get("citations", [])
            if isinstance(citation, Mapping) and citation.get("host")
        }
        for host in hosts:
            bucket = rows.setdefault(
                host,
                {"host": host, "target_mentioned_answers": 0, "target_not_mentioned_answers": 0},
            )
            bucket["target_mentioned_answers" if mentioned else "target_not_mentioned_answers"] += 1
    output = list(rows.values())
    output.sort(
        key=lambda row: (
            -int(row["target_mentioned_answers"]),
            -int(row["target_not_mentioned_answers"]),
            str(row["host"]),
        )
    )
    return output


__all__ = [
    "comparable_competitors",
    "entity_metric",
    "ranked_entities",
    "repeat_consistency",
    "source_cooccurrence",
    "wilson_interval",
]
