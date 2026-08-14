from domain.reporting.service1_metrics import (
    comparable_competitors,
    entity_metric,
    ranked_entities,
    repeat_consistency,
)


def _sample(sample_id: str, repeat: int, entities: list[tuple[str, int, bool]]) -> dict:
    return {
        "sample_id": sample_id,
        "question": "同一问题",
        "platform": "doubao",
        "region": "北京",
        "repeat_no": repeat,
        "entities": [
            {
                "canonical_name": name,
                "answer_rank": rank,
                "entity_type": "company" if eligible else "tool",
                "competitor_eligible": eligible,
                "brand_level": "brand" if eligible else "tool",
                "raw_aliases": [name],
            }
            for name, rank, eligible in entities
        ],
        "citations": [],
    }


def test_metrics_keep_answer_denominator_and_exact_counts() -> None:
    samples = [
        _sample("a1", 1, [("目标", 2, True), ("竞品", 1, True), ("Nmap", 3, False)]),
        _sample("a2", 2, [("目标", 1, True)]),
    ]
    target = entity_metric(samples, "目标")
    assert target["mention_rate_fraction"] == "2/2"
    assert target["top_counts"] == {"1": 1, "3": 2, "5": 2}
    assert target["avg_rank"] == 1.5
    assert "不跨批比较" in target["visibility_index_scope"]

    ranking = ranked_entities(samples)
    assert (
        next(row for row in ranking if row["canonical_name"] == "Nmap")["competitor_eligible"]
        is False
    )


def test_same_question_platform_comparison_and_repeat_consistency() -> None:
    samples = [
        _sample("a1", 1, [("目标", 2, True), ("竞品", 1, True)]),
        _sample("a2", 2, [("目标", 3, True), ("竞品", 4, True)]),
    ]
    comparison = comparable_competitors(samples, target_brand="目标", limit=1)
    detail = comparison["same_question_platform"][0]
    assert detail["answers"] == 2
    assert detail["target_mentions"] == 2
    assert detail["competitor_mentions"] == 2
    assert detail["avg_rank_gap"] == 0.0

    consistency = repeat_consistency(samples, target_brand="目标")
    assert consistency["complete_pairs"] == 1
    assert consistency["mention_agreement_rate"] == 100.0
    assert consistency["mean_absolute_rank_delta"] == 1
