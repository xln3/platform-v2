from tools.topup_sbaq_g0134_20260816 import (
    GROUPS,
    LEGS,
    TARGET,
    _api_groups,
    coverage_modes,
    deficit_groups,
    requested_mode,
)


def _counts(value: int) -> dict[str, int]:
    return {query: value for _, _, queries in GROUPS for query in queries}


def test_formal_scope_has_all_34_groups_and_six_legs() -> None:
    assert [number for number, _, _ in GROUPS] == list(range(1, 35))
    assert all(len(queries) == 4 for _, _, queries in GROUPS)
    assert set(LEGS) == {
        "doubao-bj",
        "doubao-sh",
        "deepseek-bj",
        "deepseek-sh",
        "yiyan-bj",
        "yiyan-sh",
    }


def test_doubao_topups_use_quick_mode_but_count_prior_expert_answers() -> None:
    assert requested_mode("doubao-bj") == "normal"
    assert requested_mode("doubao-sh") == "normal"
    assert coverage_modes("doubao-bj") == ("deep_think", "normal")
    assert coverage_modes("doubao-sh") == ("deep_think", "normal")

    for leg in ("deepseek-bj", "deepseek-sh", "yiyan-bj", "yiyan-sh"):
        assert requested_mode(leg) == "deep_think"
        assert coverage_modes(leg) == ("deep_think",)


def test_deficit_planner_caps_batch_and_preserves_original_group_number() -> None:
    counts = _counts(TARGET)
    first_g04_query = GROUPS[3][2][0]
    second_g04_query = GROUPS[3][2][1]
    counts[first_g04_query] = 0
    counts[second_g04_query] = 1

    groups = deficit_groups(counts, max_queries=2)

    assert groups == [
        {
            "name": GROUPS[3][1],
            "group_number": 4,
            "items": [
                {"text": first_g04_query, "priority": 1},
                {"text": second_g04_query, "priority": 2},
            ],
        }
    ]
    assert _api_groups(groups) == [{"name": GROUPS[3][1], "items": groups[0]["items"]}]


def test_deficit_planner_refuses_to_invent_work_when_complete() -> None:
    assert deficit_groups(_counts(TARGET), max_queries=4) == []
