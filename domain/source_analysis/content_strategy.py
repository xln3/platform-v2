"""Deterministic UVW cohort comparisons for service-5 content experiments.

The analysis describes associations in one captured corpus.  It deliberately
does not infer why a model selected a page and never turns temporal or cohort
differences into a causal ranking promise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import fmean
from typing import Any, Literal

POLICY_VERSION = "uvw-content-strategy-v1"
ALGORITHM_VERSION = "observable-page-features-v1"
HIGH_W_THRESHOLD = 0.60

_LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)、]\s*)")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+|^\s*[^\n]{1,40}[：:]\s*$")
_DIGIT_RE = re.compile(r"\d")
_SOURCE_MARKER_RE = re.compile(r"来源|数据|参考|据.{0,8}(?:显示|统计|报道)|https?://", re.I)

FEATURE_LABELS = {
    "characters": "正文字符量",
    "paragraphs": "段落数",
    "average_paragraph_characters": "平均段落长度",
    "list_line_rate": "列表行占比",
    "heading_line_rate": "标题式行占比",
    "digit_rate": "数字字符占比",
    "source_markers_per_kchars": "每千字来源/数据标记数",
}


@dataclass(frozen=True, slots=True)
class ContentStrategySignal:
    occurrence_pub_id: str
    u_state: Literal["observed", "unobserved"]
    v_state: Literal["entered", "not_entered", "unobserved"]
    w_state: Literal["pending", "confirmed", "no_evidence", "unobserved"]
    w_score: float | None
    source_text: str | None


@dataclass(frozen=True, slots=True)
class ContentStrategyAnalysis:
    status: Literal["ready", "partial", "insufficient"]
    cohort_counts: dict[str, int]
    feature_comparison: dict[str, Any]
    recommendations: tuple[dict[str, Any], ...]


def observable_page_features(text: str) -> dict[str, float]:
    cleaned = text.strip()
    lines = [line for line in cleaned.splitlines() if line.strip()]
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n+", cleaned) if item.strip()]
    characters = len(cleaned)
    line_denominator = max(1, len(lines))
    return {
        "characters": float(characters),
        "paragraphs": float(len(paragraphs)),
        "average_paragraph_characters": (
            float(characters) / len(paragraphs) if paragraphs else 0.0
        ),
        "list_line_rate": sum(bool(_LIST_RE.search(line)) for line in lines) / line_denominator,
        "heading_line_rate": sum(bool(_HEADING_RE.search(line)) for line in lines)
        / line_denominator,
        "digit_rate": len(_DIGIT_RE.findall(cleaned)) / max(1, characters),
        "source_markers_per_kchars": len(_SOURCE_MARKER_RE.findall(cleaned))
        * 1000
        / max(1, characters),
    }


def _means(signals: list[ContentStrategySignal]) -> dict[str, float]:
    rows = [observable_page_features(item.source_text or "") for item in signals]
    if not rows:
        return {}
    return {feature: round(fmean(row[feature] for row in rows), 6) for feature in FEATURE_LABELS}


def _comparison(
    left: list[ContentStrategySignal],
    right: list[ContentStrategySignal],
    *,
    left_name: str,
    right_name: str,
) -> dict[str, Any]:
    left_means = _means(left)
    right_means = _means(right)
    deltas = {
        feature: round(left_means[feature] - right_means[feature], 6)
        for feature in FEATURE_LABELS
        if feature in left_means and feature in right_means
    }
    return {
        "left": left_name,
        "right": right_name,
        "left_n": len(left),
        "right_n": len(right),
        "left_means": left_means,
        "right_means": right_means,
        "deltas": deltas,
    }


def _recommendations(
    comparison: dict[str, Any], *, basis: Literal["v_vs_u_not_v", "high_w_vs_low_w"]
) -> list[dict[str, Any]]:
    if not comparison["left_n"] or not comparison["right_n"]:
        return []
    rows: list[tuple[float, dict[str, Any]]] = []
    left_means = comparison["left_means"]
    right_means = comparison["right_means"]
    for feature, delta in comparison["deltas"].items():
        baseline = max(abs(float(right_means[feature])), 0.01)
        relative_delta = abs(float(delta)) / baseline
        # Suppress only numerically indistinguishable features.  This bounds
        # noisy advice, not the source corpus or the cohort denominators.
        if relative_delta < 0.05:
            continue
        direction = "更高" if delta > 0 else "更低"
        cohort = "进入 V" if basis == "v_vs_u_not_v" else "高 W"
        suggestion_target = "进入 V" if basis == "v_vs_u_not_v" else "进入答案 W"
        rows.append(
            (
                relative_delta,
                {
                    "basis": basis,
                    "feature": feature,
                    "feature_label": FEATURE_LABELS[feature],
                    "observation": (
                        f"本批可观察样本中，{cohort}页面的{FEATURE_LABELS[feature]}平均值"
                        f"{direction}（{left_means[feature]} 对 {right_means[feature]}）。"
                    ),
                    "experiment": (
                        f"后续同题、同平台、同地域试验可优先验证{FEATURE_LABELS[feature]}"
                        f"{direction}是否提高内容{suggestion_target}的概率。"
                    ),
                    "causal_boundary": "这是观察性关联，仅用于形成待验证假设，不证明因果。",
                },
            )
        )
    return [row for _score, row in sorted(rows, key=lambda item: (-item[0], item[1]["feature"]))]


def build_content_strategy(signals: list[ContentStrategySignal]) -> ContentStrategyAnalysis:
    """Compare every analyzable U occurrence without truncating the corpus."""

    observed_u = [item for item in signals if item.u_state == "observed"]
    with_snapshot = [item for item in observed_u if item.source_text is not None]
    viewed = [item for item in with_snapshot if item.v_state == "entered"]
    not_viewed = [item for item in with_snapshot if item.v_state == "not_entered"]
    high_w = [
        item
        for item in viewed
        if item.w_state == "confirmed"
        and item.w_score is not None
        and item.w_score >= HIGH_W_THRESHOLD
    ]
    low_w = [
        item
        for item in viewed
        if item.w_state == "no_evidence"
        or (
            item.w_state == "confirmed"
            and item.w_score is not None
            and item.w_score < HIGH_W_THRESHOLD
        )
    ]
    counts = {
        "source_occurrence_records": len(signals),
        "u_occurrences": len(observed_u),
        "u_observation_unavailable": len(signals) - len(observed_u),
        "snapshot_available": len(with_snapshot),
        "snapshot_unavailable": len(observed_u) - len(with_snapshot),
        "v_entered": sum(item.v_state == "entered" for item in observed_u),
        "u_not_v": sum(item.v_state == "not_entered" for item in observed_u),
        "v_unobserved": sum(item.v_state == "unobserved" for item in observed_u),
        "high_w": len(high_w),
        "low_w": len(low_w),
        "w_pending_or_unobserved": sum(
            item.v_state == "entered" and item.w_state in {"pending", "unobserved"}
            for item in observed_u
        ),
    }
    selection = _comparison(viewed, not_viewed, left_name="v", right_name="u_not_v")
    contribution = _comparison(high_w, low_w, left_name="high_w", right_name="low_w")
    selection_ready = bool(viewed and not_viewed)
    contribution_ready = bool(high_w and low_w)
    observation_complete = (
        len(with_snapshot) == len(observed_u)
        and counts["v_unobserved"] == 0
        and counts["w_pending_or_unobserved"] == 0
        and counts["u_observation_unavailable"] == 0
    )
    status: Literal["ready", "partial", "insufficient"] = (
        "ready"
        if selection_ready and contribution_ready and observation_complete
        else "partial"
        if selection_ready or contribution_ready
        else "insufficient"
    )
    recommendations = (
        *_recommendations(selection, basis="v_vs_u_not_v"),
        *_recommendations(contribution, basis="high_w_vs_low_w"),
    )
    return ContentStrategyAnalysis(
        status=status,
        cohort_counts=counts,
        feature_comparison={
            "selection": selection,
            "content_contribution": contribution,
            "high_w_threshold": HIGH_W_THRESHOLD,
        },
        recommendations=recommendations,
    )


__all__ = [
    "ALGORITHM_VERSION",
    "HIGH_W_THRESHOLD",
    "POLICY_VERSION",
    "ContentStrategyAnalysis",
    "ContentStrategySignal",
    "build_content_strategy",
    "observable_page_features",
]
