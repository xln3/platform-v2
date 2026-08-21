from __future__ import annotations

from domain.source_analysis.content_strategy import (
    ContentStrategySignal,
    build_content_strategy,
    observable_page_features,
)


def _signal(
    occurrence: str,
    *,
    v_state: str,
    w_state: str,
    w_score: float | None,
    source_text: str | None,
    u_state: str = "observed",
) -> ContentStrategySignal:
    return ContentStrategySignal(
        occurrence_pub_id=occurrence,
        u_state=u_state,  # type: ignore[arg-type]
        v_state=v_state,  # type: ignore[arg-type]
        w_state=w_state,  # type: ignore[arg-type]
        w_score=w_score,
        source_text=source_text,
    )


def test_service5_builds_both_uvw_comparisons_without_truncating_u() -> None:
    rich = """# 数据结论

- 来源：https://example.test/report
- 2025 年样本为 1200 个。
- 参考公开统计，覆盖率为 98%。
"""
    plain = "一段很短的普通说明。"
    signals = [
        _signal("uoc_high", v_state="entered", w_state="confirmed", w_score=0.9, source_text=rich),
        _signal(
            "uoc_low", v_state="entered", w_state="no_evidence", w_score=None, source_text=plain
        ),
        _signal(
            "uoc_not_v",
            v_state="not_entered",
            w_state="unobserved",
            w_score=None,
            source_text=plain,
        ),
    ]

    analysis = build_content_strategy(signals)

    assert analysis.status == "ready"
    assert analysis.cohort_counts == {
        "source_occurrence_records": 3,
        "u_occurrences": 3,
        "u_observation_unavailable": 0,
        "snapshot_available": 3,
        "snapshot_unavailable": 0,
        "v_entered": 2,
        "u_not_v": 1,
        "v_unobserved": 0,
        "high_w": 1,
        "low_w": 1,
        "w_pending_or_unobserved": 0,
    }
    assert analysis.feature_comparison["selection"]["left_n"] == 2
    assert analysis.feature_comparison["selection"]["right_n"] == 1
    assert analysis.feature_comparison["content_contribution"]["left_n"] == 1
    assert analysis.feature_comparison["content_contribution"]["right_n"] == 1
    assert {row["basis"] for row in analysis.recommendations} == {
        "v_vs_u_not_v",
        "high_w_vs_low_w",
    }
    assert all("不证明因果" in row["causal_boundary"] for row in analysis.recommendations)


def test_unknown_u_and_missing_snapshot_remain_unknown_instead_of_zero() -> None:
    analysis = build_content_strategy(
        [
            _signal(
                "uoc_observed",
                v_state="unobserved",
                w_state="unobserved",
                w_score=None,
                source_text=None,
            ),
            _signal(
                "uoc_legacy",
                u_state="unobserved",
                v_state="unobserved",
                w_state="unobserved",
                w_score=None,
                source_text=None,
            ),
        ]
    )

    assert analysis.status == "insufficient"
    assert analysis.cohort_counts["source_occurrence_records"] == 2
    assert analysis.cohort_counts["u_occurrences"] == 1
    assert analysis.cohort_counts["u_observation_unavailable"] == 1
    assert analysis.cohort_counts["snapshot_unavailable"] == 1
    assert analysis.recommendations == ()


def test_observable_features_are_deterministic_and_content_only() -> None:
    first = observable_page_features("标题：\n- 来源：https://example.test\n- 2026 年数据 42")
    second = observable_page_features("标题：\n- 来源：https://example.test\n- 2026 年数据 42")

    assert first == second
    assert first["list_line_rate"] > 0
    assert first["digit_rate"] > 0
    assert first["source_markers_per_kchars"] > 0
