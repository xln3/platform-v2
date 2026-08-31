from __future__ import annotations

from scripts.backfill_existing_citations import (
    build_deepseek_plan,
    build_tongyi_plan,
    build_yuanbao_plan,
)


def _stream(*, include_second_card: bool = True) -> str:
    cards = (
        '{"url":"https://example.com/a","title":"来源A","cite_index":1},'
        + (
            '{"url":"https://example.com/b","title":"来源B","cite_index":2}'
            if include_second_card
            else '{"url":"https://example.com/c","title":"来源C","cite_index":3}'
        )
    )
    return (
        'data: {"v":{"response":{"fragments":['
        '{"id":1,"type":"SEARCH","queries":[{"query":"查询词"}],"results":[]}'
        ']}}}\n\n'
        f'data: {{"p":"response/fragments/-1/results","v":[{cards}]}}\n\n'
        'data: {"p":"response/fragments","o":"APPEND","v":['
        '{"id":2,"type":"RESPONSE","content":"正文[citation:2]及补充[citation:1]"}'
        ']}\n\n'
    )


def test_build_deepseek_plan_preserves_all_candidates_and_all_cited_indexes() -> None:
    plan = build_deepseek_plan(
        _stream(), answer_text="正文[citation:2]及补充[citation:1]"
    )

    assert plan.citation_indexes == [2, 1]
    assert [row["platform_ordinal"] for row in plan.citations] == [2, 1]
    assert [row["url"] for row in plan.citations] == [
        "https://example.com/b",
        "https://example.com/a",
    ]
    assert plan.candidate_count == 2
    assert plan.occurrence_count == 2


def test_build_deepseek_plan_reports_one_unresolved_without_dropping_resolved() -> None:
    plan = build_deepseek_plan(
        _stream(include_second_card=False),
        answer_text="正文[citation:2]及补充[citation:1]",
    )

    assert plan.unresolved_citation_indexes == [2]
    assert [row["platform_ordinal"] for row in plan.citations] == [1]
    assert [row["url"] for row in plan.citations] == ["https://example.com/a"]
    assert plan.candidate_count == 2


def test_build_tongyi_plan_preserves_panel_order_and_tolerates_missing_url() -> None:
    plan = build_tongyi_plan(
        {
            "status": "completed",
            "displayed_source_count": 3,
            "raw_source_count": 3,
            "display_count_matches": True,
            "unresolved_source_ordinals": [2],
            "citations": [
                {
                    "url": "https://example.com/a",
                    "title": "来源 A",
                    "cited_text": "摘要 A",
                    "platform_ordinal": 1,
                    "ordinal_base": 1,
                },
                {
                    "url": "https://example.com/a",
                    "title": "同 URL 的第三张卡",
                    "cited_text": "摘要 C",
                    "platform_ordinal": 3,
                    "ordinal_base": 1,
                },
            ],
        },
        answer_text="通义答案没有正文 citation 标记",
    )

    assert plan.unresolved_source_ordinals == [2]
    assert [row["platform_ordinal"] for row in plan.citations] == [1, 3]
    assert [row["url"] for row in plan.citations] == [
        "https://example.com/a",
        "https://example.com/a",
    ]
    assert plan.retrieval_events[0]["u_observation"] == "unobserved"
    assert [
        row["final_reference_ordinal"]
        for row in plan.retrieval_events[0]["final_references"]
    ] == [1, 3]
    assert plan.occurrence_count == 2


def test_build_yuanbao_plan_keeps_candidates_and_only_marker_references() -> None:
    raw = (
        'data: {"type":"searchGuid","docs":['
        '{"index":1,"url":"https://example.com/a","title":"A"},'
        '{"index":2,"url":"https://example.com/b","title":"B"},'
        '{"index":3,"url":"https://example.com/c","title":"C"}'
        ']}\n\n'
        'data: {"type":"text","msg":"正文[citation:3][citation:1]"}\n\n'
    )

    plan = build_yuanbao_plan(raw, answer_text="正文")

    assert plan.candidate_count == 3
    assert plan.citation_indexes == [3, 1]
    assert [row["platform_ordinal"] for row in plan.citations] == [3, 1]
    assert [row["url"] for row in plan.citations] == [
        "https://example.com/c",
        "https://example.com/a",
    ]
    assert plan.occurrence_count == 3
