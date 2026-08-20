from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any
from zipfile import ZipFile

from geo_platform.reports.formal_review import (
    MAX_FORMAL_ANSWER_ROWS,
    PRIMARY_MODELS,
    PRIMARY_REGIONS,
    balance_primary_answers,
    candidate_groups_from_snapshot,
    score_candidate_groups,
)
from geo_platform.reports.service3_review_v2 import (
    adoption_status,
    answer_narrative,
    longest_common_evidence,
)
from PIL import Image

from domain.reporting.formal_review_service3_docx import render_service3_v2_docx


def _snapshot() -> dict[str, object]:
    return {
        "query_groups": [
            {
                "name": "旧版 16 题",
                "items": [
                    {"text": f"候选组{group}-问题{question}"}
                    for group in range(1, 5)
                    for question in range(1, 5)
                ],
            }
        ]
    }


def test_old_single_group_snapshot_is_inferred_as_four_quartets() -> None:
    groups, inferred = candidate_groups_from_snapshot(_snapshot())
    assert inferred is True
    assert len(groups) == 4
    assert [len(group["questions"]) for group in groups] == [4, 4, 4, 4]
    assert groups[0]["questions"][0] == "候选组1-问题1"
    assert all(group["inferred"] is True for group in groups)


def test_formal_answer_volume_has_a_hard_non_truncating_bound() -> None:
    # The bound is comfortably above the quotation matrix (four candidate groups,
    # four variants, three platforms, two regions and two repetitions = 192), but
    # finite enough to prevent a year-wide request from exhausting a worker.
    assert MAX_FORMAL_ANSWER_ROWS >= 192
    assert MAX_FORMAL_ANSWER_ROWS <= 2_000


def test_blank_line_preserved_groups_do_not_need_inference() -> None:
    snapshot = {
        "query_groups": [
            {"name": "候选组 1", "items": [{"text": "Q1"}, {"text": "Q1A"}]},
            {"name": "候选组 2", "items": [{"text": "Q2"}, {"text": "Q2A"}]},
        ]
    }
    groups, inferred = candidate_groups_from_snapshot(snapshot)
    assert inferred is False
    assert [group["title"] for group in groups] == ["候选组 1", "候选组 2"]


def test_balancing_uses_latest_cell_and_selection_uses_only_completeness() -> None:
    groups, _ = candidate_groups_from_snapshot(_snapshot())
    base_time = datetime(2026, 8, 10, tzinfo=UTC)
    answers: list[dict[str, Any]] = []
    for group in groups:
        for question in group["questions"]:
            for model in PRIMARY_MODELS:
                for region in PRIMARY_REGIONS:
                    pub_id = f"ans_{len(answers):03d}"
                    answers.append(
                        {
                            "pub_id": pub_id,
                            "query_text": question,
                            "response_text": "完整回答" * 60,
                            "model": model,
                            "region": region,
                            "mode": "deep_think",
                            "capture_time": base_time,
                        }
                    )
    duplicate = {
        **answers[0],
        "pub_id": "ans_latest",
        "capture_time": base_time + timedelta(hours=1),
    }
    answers.append(duplicate)
    balanced, excluded = balance_primary_answers(answers, candidate_groups=groups)
    assert len(balanced) == 96
    assert {row["pub_id"] for row in balanced}.issuperset({"ans_latest"})
    assert [row["pub_id"] for row in excluded] == ["ans_000"]

    extracts = {row["pub_id"]: {"status": "ok", "brands": []} for row in balanced}
    # 组 2/3 的引用覆盖最高；组 1/4 同分时按原始组序稳定选组 1。
    cited_limits = {"candidate_01": 19, "candidate_02": 21, "candidate_03": 20, "candidate_04": 19}
    seen = {group_id: 0 for group_id in cited_limits}
    citations = {}
    for row in balanced:
        group_id = row["candidate_group_id"]
        if seen[group_id] < cited_limits[group_id]:
            citations[row["pub_id"]] = [{"host": "example.com"}]
        seen[group_id] += 1
    scored = score_candidate_groups(groups, balanced, extracts, citations)
    selected = [row["id"] for row in scored if row["selected_for_main_report"]]
    assert selected == ["candidate_01", "candidate_02", "candidate_03"]
    assert all("no brand outcome input" in row["selection_basis"] for row in scored)


def test_formal_balancing_keeps_two_latest_independent_repetitions_per_cell() -> None:
    groups, _ = candidate_groups_from_snapshot(_snapshot())
    base_time = datetime(2026, 8, 10, tzinfo=UTC)
    first = {
        "pub_id": "ans_first",
        "query_text": groups[0]["questions"][0],
        "response_text": "完整回答" * 60,
        "model": PRIMARY_MODELS[0],
        "region": PRIMARY_REGIONS[0],
        "mode": "deep_think",
        "capture_time": base_time,
    }
    second = {**first, "pub_id": "ans_second", "capture_time": base_time + timedelta(hours=1)}
    third = {**first, "pub_id": "ans_third", "capture_time": base_time + timedelta(hours=2)}

    balanced, excluded = balance_primary_answers(
        [first, second, third], candidate_groups=groups, repetitions_per_cell=2
    )

    assert [row["pub_id"] for row in balanced] == ["ans_second", "ans_third"]
    assert [row["pub_id"] for row in excluded] == ["ans_first"]


def test_candidate_coverage_counts_quotation_repetitions_in_denominator() -> None:
    groups, _ = candidate_groups_from_snapshot(_snapshot())
    group = groups[0]
    base_time = datetime(2026, 8, 10, tzinfo=UTC)
    answers = [
        {
            "pub_id": f"ans_{model}_{region}",
            "query_text": group["questions"][0],
            "response_text": "完整回答" * 60,
            "model": model,
            "region": region,
            "mode": "deep_think",
            "capture_time": base_time,
            "candidate_group_id": group["id"],
        }
        for model in PRIMARY_MODELS
        for region in PRIMARY_REGIONS
    ]
    extracts = {row["pub_id"]: {"status": "ok", "brands": []} for row in answers}

    scored = score_candidate_groups(groups, answers, extracts, {}, required_repetitions=2)

    assert scored[0]["observed_cells"] == 6
    assert scored[0]["expected_cells"] == 48
    assert scored[0]["coverage_rate"] == 0.125


def test_service3_adoption_excludes_rendered_reference_list() -> None:
    answer = "主文只说该产品能处理资产风险。\n\n参考来源：\n1. 一段官网独特且很长的完整标题"
    source = "一段官网独特且很长的完整标题"
    assert "参考来源" not in answer_narrative(answer)
    match = longest_common_evidence(answer_narrative(answer), source)
    assert match["length"] < 20


def test_service3_adoption_thresholds_are_conservative() -> None:
    assert adoption_status(30, snapshot_available=False) == "not_evaluated"
    assert adoption_status(20, snapshot_available=True) == "confirmed"
    assert adoption_status(19, snapshot_available=True) == "weak"
    assert adoption_status(10, snapshot_available=True) == "weak"
    assert adoption_status(9, snapshot_available=True) == "no_direct_evidence"


def test_service3_longest_common_evidence_returns_readable_context() -> None:
    answer = "回答中说：盛邦安全入选攻击面管理产品市场分析报告核心能力代表厂商。"
    source = "官网文章记载，盛邦安全入选攻击面管理产品市场分析报告核心能力代表厂商。"
    match = longest_common_evidence(answer, source)
    assert match["length"] >= 20
    assert "盛邦安全" in match["left_excerpt"]
    assert "盛邦安全" in match["right_excerpt"]


def test_service3_v2_renderer_contains_client_evidence_chain() -> None:
    case = {
        "answer_pub_id": "ans_1",
        "query": "攻击面管理平台怎么选？",
        "model_label": "豆包",
        "region": "北京",
        "mode": "deep_think",
        "capture_time": datetime(2026, 8, 10, tzinfo=UTC),
        "status": "confirmed",
        "status_basis": "回答主文与官网正文有长片段重合",
        "all_source_count": 2,
        "all_sources": [
            {
                "ordinal": 1,
                "host": "www.webray.com.cn",
                "url": "https://www.webray.com.cn/a",
                "is_own_site": True,
            },
            {
                "ordinal": 2,
                "host": "news.example.com",
                "url": "https://news.example.com/b",
                "is_own_site": False,
            },
        ],
        "official_sources": [
            {
                "ordinal": 1,
                "url": "https://www.webray.com.cn/a",
                "title": "攻击面管理",
                "has_cited_text": True,
                "has_current_text_snapshot": True,
                "has_current_screenshot": False,
                "direct_answer_relation": True,
            }
        ],
        "best_official_url": "https://www.webray.com.cn/a",
        "best_official_title": "攻击面管理",
        "answer_excerpt": "回答中直接复用了官网的攻击面管理能力表述。",
        "source_excerpt": "官网记载了攻击面管理能力表述。",
        "matched_phrase": "攻击面管理能力表述",
        "match_length": 22,
        "cited_text_source_match_length": 30,
        "surface_reasoning": "平台公开搜索了攻击面管理信息。",
        "answer_screenshot": {"pub_id": "evi_private_answer"},
        "official_screenshot": None,
        "snapshot_relation": "direct",
    }
    facts = {
        "target_brand": "盛邦安全",
        "project_name": "测试项目",
        "generated_at": datetime(2026, 8, 12, tzinfo=UTC),
        "document_status": "formal",
        "window": {"start": "2026-08-10", "end": "2026-08-12"},
        "metrics": {
            "answers_total": 10,
            "answers_with_citation": 8,
            "citation_coverage_rate": 0.8,
            "answers_with_own_site_citation": 2,
            "own_site_answer_citation_rate": 0.2,
            "own_site_share_of_cited_answers": 0.25,
            "citation_references_total": 20,
            "own_site_citation_references": 2,
            "adoption_evaluated_answers": 1,
            "adoption_verified_answers": 1,
            "conservative_adoption_rate": 1.0,
            "adoption_evaluation_coverage_rate": 0.5,
            "not_evaluated_answers": 1,
            "weak_evidence_answers": 0,
            "direct_snapshot_bound_answers": 1,
            "same_url_snapshot_covered_answers": 1,
        },
        "adoption_method": {
            "denominator": "有当前窗口快照的回答",
            "confirmed_rule": "至少 20 字符重合",
            "weak_rule": "10–19 字符重合",
            "boundary": "不推断隐藏思考。",
        },
        "platform_region_breakdown": [
            {
                "model_label": "豆包",
                "mode": "deep_think",
                "region": "北京",
                "answers": 10,
                "answers_with_citation": 8,
                "answers_with_own_site_citation": 2,
                "own_site_answer_citation_rate": 0.2,
            }
        ],
        "zero_citation_groups": [],
        "retrieval_observability_by_platform": [
            {
                "model_label": "豆包",
                "answers": 10,
                "trace_available": 10,
                "candidate_stage_observed": 10,
                "opened_stage_observed": 0,
                "final_citation_stage_observed": 10,
                "boundary": "候选与最终引用可观测；页面打开阶段不可观测",
            }
        ],
        "answer_source_domains": [
            {"host": "www.webray.com.cn", "answers": 2, "references": 2, "is_own_site": True}
        ],
        "evaluations": [case],
        "selected_evidence_cases": [case],
        "client_actions": [
            {
                "priority": "P1",
                "fact": "官网被引用",
                "action": "保留可引用表述",
                "owner": "官网内容",
            }
        ],
        "limitations": [
            "当前为联调/试采样数据，不是正式运行签发结论。",
            "结论只覆盖当前窗口。",
        ],
    }
    image_stream = BytesIO()
    Image.new("RGB", (320, 180), "white").save(image_stream, format="PNG")
    payload = render_service3_v2_docx(
        facts,
        evidence_assets={"evi_private_answer": image_stream.getvalue()},
    )
    with ZipFile(BytesIO(payload)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    assert 'TOC \\o "1-3" \\h \\z \\u' in xml
    assert "GEO-S3-V10-FORMAL-20260812" in xml
    assert "预正式" not in xml
    assert "禁止外发" not in xml
    assert "联调/试采样数据，不是正式运行签发结论" not in xml
    assert "结论只覆盖当前窗口" in xml
    assert "evi_private_answer" not in xml
    assert payload.startswith(b"PK")
    assert len(payload) > 30_000
