from __future__ import annotations

from domain.collection.uvw import normalize_retrieval_events, occurrence_rows


def _event(
    ordinal: int,
    query: str,
    candidates: list[dict[str, object]],
    *,
    v_observation: str = "unobserved",
    opened_pages: list[dict[str, object]] | None = None,
    final_references: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "ordinal": ordinal,
        "queries": [query],
        "u_observation": "observed",
        "v_observation": v_observation,
        "final_reference_observation": (
            "observed" if final_references is not None else "unobserved"
        ),
        "candidates": candidates,
        "opened_pages": opened_pages or [],
        "final_references": final_references or [],
    }


def test_same_url_from_two_queries_keeps_two_occurrences_and_one_identity() -> None:
    events = normalize_retrieval_events(
        [
            _event(1, "问题一", [{"url": "https://example.com/a#first", "u_rank": 1}]),
            _event(2, "问题二", [{"url": "https://example.com/a", "u_rank": 2}]),
        ]
    )

    rows = occurrence_rows(events)

    assert len(rows) == 2
    assert [row.query for row in rows] == ["问题一", "问题二"]
    assert [row.u_rank for row in rows] == [1, 2]
    assert len({row.canonical_url for row in rows}) == 1


def test_repeated_candidate_in_one_event_is_not_deduplicated() -> None:
    rows = occurrence_rows(
        [
            _event(
                1,
                "同一检索",
                [
                    {"url": "https://example.com/a", "u_rank": 1},
                    {"url": "https://example.com/a", "u_rank": 2},
                ],
            )
        ]
    )

    assert len(rows) == 2
    assert [row.u_rank for row in rows] == [1, 2]


def test_opened_page_stays_with_its_retrieval_event_when_url_repeats() -> None:
    rows = occurrence_rows(
        [
            _event(
                1,
                "首次检索",
                [{"url": "https://example.com/shared", "u_rank": 1}],
                v_observation="observed",
            ),
            _event(
                2,
                "再次检索",
                [{"url": "https://example.com/shared", "u_rank": 1}],
                v_observation="observed",
                opened_pages=[{"url": "https://example.com/shared", "v_open_order": 1}],
            ),
        ]
    )

    assert [row.retrieval_event_ordinal for row in rows] == [1, 2]
    assert [row.v_state for row in rows] == ["not_entered", "entered"]


def test_more_than_historical_candidate_cap_remains_complete() -> None:
    candidates = [
        {"url": f"https://source-{index}.example.com/article", "u_rank": index + 1}
        for index in range(750)
    ]

    rows = occurrence_rows([_event(1, "大规模检索", candidates)])

    assert len(rows) == 750
    assert rows[-1].u_rank == 750


def test_unobserved_v_is_not_encoded_as_not_entered_or_zero() -> None:
    row = occurrence_rows([_event(1, "不可观察", [{"url": "https://example.com/a", "u_rank": 1}])])[
        0
    ]

    assert row.u_state == "observed"
    assert row.v_state == "unobserved"
    assert row.v_open_order is None
    assert row.w_state == "unobserved"


def test_final_reference_without_exact_content_does_not_become_w() -> None:
    row = occurrence_rows(
        [
            {
                "ordinal": 1,
                "queries": [],
                "u_observation": "unobserved",
                "v_observation": "unobserved",
                "final_reference_observation": "observed",
                "candidates": [],
                "opened_pages": [],
                "final_references": [
                    {"url": "https://example.com/reference", "final_reference_ordinal": 1}
                ],
            }
        ]
    )[0]

    assert row.u_state == "unobserved"
    assert row.v_state == "unobserved"
    assert row.final_reference_state == "referenced"
    assert row.w_state == "unobserved"


def test_observed_open_page_is_pending_content_level_w_analysis() -> None:
    url = "https://example.com/opened"
    row = occurrence_rows(
        [
            _event(
                1,
                "已打开",
                [{"url": url, "u_rank": 1}],
                v_observation="observed",
                opened_pages=[{"url": url, "v_open_order": 1}],
            )
        ]
    )[0]

    assert row.v_state == "entered"
    assert row.v_open_order == 1
    assert row.w_state == "pending"
