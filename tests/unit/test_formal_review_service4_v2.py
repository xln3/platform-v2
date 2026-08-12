from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any
from zipfile import ZipFile

import pytest
from geo_platform.reports.formal_review_service4 import (
    assemble_service4_review_facts,
    score_service4_candidate_groups,
)

from domain.reporting.formal_review_service4_docx import render_service4_review_docx

MODELS = ("doubao", "deepseek", "yiyan")
REGIONS = ("北京", "上海")
NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)


def _snapshot() -> dict[str, Any]:
    return {
        "query_groups": [
            {
                "name": f"业务问题组 {group}",
                "items": [
                    {"text": f"业务问题组{group}语义变体{variant}", "priority": variant}
                    for variant in range(1, 5)
                ],
            }
            for group in range(1, 5)
        ],
        "models": list(MODELS),
        "regions": list(REGIONS),
        "modes": ["deep_think"],
        "repetitions_per_cell": 2,
        "frequency": "frozen_pilot",
    }


def _snapshot_rows(arm: str) -> list[dict[str, Any]]:
    return [
        {
            "revision": 1 if arm == "before" else 2,
            "frozen_at": NOW,
            "snapshot_hash": ("a" if arm == "before" else "b") * 64,
            "snapshot": _snapshot(),
            "_config_version_pub_id": f"cfg_{arm}",
        }
    ]


def _fixture() -> dict[str, Any]:
    before: list[dict[str, Any]] = []
    after: list[dict[str, Any]] = []
    extracts: dict[str, dict[str, Any]] = {}
    citations: dict[str, list[dict[str, Any]]] = {}
    visuals: dict[str, dict[str, bool]] = {}
    provenance: dict[str, dict[str, Any]] = {}
    for arm, output in (("before", before), ("after", after)):
        for group in range(1, 5):
            for variant in range(1, 5):
                question = f"业务问题组{group}语义变体{variant}"
                for model in MODELS:
                    for region in REGIONS:
                        for repetition in range(1, 3):
                            answer_id = (
                                f"ans_{arm}_g{group}_v{variant}_{model}_{region}_{repetition}"
                            )
                            output.append(
                                {
                                    "pub_id": answer_id,
                                    "query_text": question,
                                    "response_text": (
                                        "这是一段完整且可核验的回答正文。" * 18
                                        if group < 4
                                        else "短回答"
                                    ),
                                    "model": model,
                                    "region": region,
                                    "mode": "deep_think",
                                    "capture_time": NOW,
                                    "config_version_pub_id": f"cfg_{arm}",
                                    "adapter_version": "adapter-v2",
                                }
                            )
                            # Group 4 has the strongest target-brand outcome, but the
                            # least complete presentation evidence. It must stay out.
                            if group == 4:
                                brands = ["盛邦安全", "奇安信"]
                            elif arm == "before":
                                brands = ["奇安信", "盛邦安全"] if repetition == 1 else ["奇安信"]
                            else:
                                brands = ["盛邦安全", "奇安信"]
                            extracts[answer_id] = {
                                "status": "ok",
                                "brands": brands,
                                "model": "brand-extract-v2",
                            }
                            if group <= 3:
                                citations[answer_id] = [
                                    {
                                        "ordinal": 1,
                                        "host": (
                                            "www.webray.com.cn"
                                            if arm == "after"
                                            else "industry.example.com"
                                        ),
                                        "canonical_url": f"https://example.com/{answer_id}",
                                        "original_url": f"https://example.com/{answer_id}",
                                        "own_source": arm == "after",
                                    }
                                ]
                            if group == 1 or (group == 2 and repetition == 1):
                                visuals[answer_id] = {"answer_screenshot": True}
                            provenance[answer_id] = {
                                "platform_account_pub_id": f"account_{arm}_{model}",
                                "browser_profile_version_pub_id": f"profile_{arm}_{model}",
                            }
    return {
        "project": {
            "pub_id": "prj_fixture",
            "name": "动态服务四项目",
            "brandrank_domain": "cybersecurity",
            "brand_names": ["盛邦安全"],
            "competitor_names": ["奇安信"],
        },
        "before_answers": before,
        "after_answers": after,
        "before_snapshots": _snapshot_rows("before"),
        "after_snapshots": _snapshot_rows("after"),
        "extracts": extracts,
        "citations": citations,
        "visuals": visuals,
        "provenance": provenance,
        "before_start": date(2026, 7, 1),
        "before_end": date(2026, 7, 7),
        "after_start": date(2026, 8, 1),
        "after_end": date(2026, 8, 7),
        "generated_at": NOW,
    }


def test_candidate_selection_uses_evidence_not_brand_outcomes() -> None:
    fixture = _fixture()
    # Assemble once to obtain the aligned candidates and configured dimensions.
    facts = assemble_service4_review_facts(**fixture)
    selected = [
        row["title"] for row in facts["candidate_groups"] if row["selected_for_main_report"]
    ]
    assert selected == ["业务问题组 1", "业务问题组 2", "业务问题组 3"]
    assert "业务问题组 4" not in selected
    assert all("不读取品牌提及" in row["selection_basis"] for row in facts["candidate_groups"])
    # Group 4 is 100% mentioned in both arms, proving performance did not rescue it.
    group4 = next(row for row in facts["candidate_groups"] if row["title"] == "业务问题组 4")
    assert group4["selection_rank"] == 4


def test_comparable_service4_facts_include_deltas_samples_intervals_and_structures() -> None:
    facts = assemble_service4_review_facts(**_fixture())
    assert facts["schema_version"] == "service4-formal-review-v2"
    assert facts["comparability"]["status"] == "comparable"
    assert facts["evidence_gate"]["status"] == "sufficient_for_description"
    assert facts["evidence_gate"]["causal_claim_allowed"] is False
    assert facts["evidence_gate"]["causal_claim_blocker"]

    rows = {row["key"]: row for row in facts["metrics"]}
    assert set(rows) == {"mention_rate", "avg_rank", "top1_rate", "top3_rate", "top5_rate"}
    mention = rows["mention_rate"]
    assert mention["before"] == 50.0
    assert mention["after"] == 100.0
    assert mention["absolute_change"] == 50.0
    assert mention["relative_change_percent"] == 100.0
    assert mention["before_n"] == mention["after_n"] == 144
    assert mention["before_numerator"] == 72
    assert mention["after_numerator"] == 144
    assert mention["before_interval_95"] and mention["after_interval_95"]
    assert "逐单元均完成 2 次" in mention["stability"]

    assert facts["competitor_landscape"]
    assert facts["competitor_landscape"][0]["brand"] == "盛邦安全"
    before_share = sum(row["before_share"] for row in facts["source_structure"])
    after_share = sum(row["after_share"] for row in facts["source_structure"])
    assert before_share == 100.0
    assert after_share == 100.0
    assert facts["own_site_extension"]["before"]["answer_citation_rate"] == 0.0
    assert facts["own_site_extension"]["after"]["answer_citation_rate"] == 100.0
    assert facts["pilot_plan"]["status"] == "proposed_not_execution_record"


def test_matrix_mismatch_is_prominent_and_never_attributed() -> None:
    fixture = _fixture()
    changed = deepcopy(fixture["after_answers"])
    changed[0]["region"] = "广州"
    fixture["after_answers"] = changed
    facts = assemble_service4_review_facts(**fixture)
    assert facts["metrics"]  # observed values remain available for transparent description
    assert facts["comparability"]["status"] == "not_comparable"
    assert "regions" in facts["comparability"]["failed_checks"]
    assert "repetitions" in facts["comparability"]["failed_checks"]
    assert facts["evidence_gate"]["attribution_allowed_by_matrix"] is False
    assert "不得把变化归因" in facts["evidence_gate"]["conclusion"]
    assert all("不归因于优化" in row["stability"] for row in facts["metrics"])


def test_missing_arm_fails_closed_without_fake_zero_or_delta() -> None:
    fixture = _fixture()
    fixture["after_answers"] = []
    facts = assemble_service4_review_facts(**fixture)
    assert facts["evidence_gate"]["status"] == "insufficient"
    assert "after_no_answers" in facts["evidence_gate"]["insufficient_reasons"]
    assert facts["metrics"] == []
    assert facts["competitor_landscape"] == []
    assert facts["source_structure"] == []


def test_partial_selected_cell_fails_closed_instead_of_treating_missing_as_zero() -> None:
    fixture = _fixture()
    fixture["after_answers"] = fixture["after_answers"][1:]
    facts = assemble_service4_review_facts(**fixture)
    assert "selected_group_sample_incomplete" in facts["evidence_gate"]["insufficient_reasons"]
    assert facts["metrics"] == []


def test_windows_must_be_ordered_and_non_overlapping() -> None:
    fixture = _fixture()
    fixture["after_start"] = date(2026, 7, 7)
    with pytest.raises(ValueError, match="service4_windows_overlap_or_out_of_order"):
        assemble_service4_review_facts(**fixture)


def test_renderer_has_shared_native_toc_two_parts_and_no_internal_ids() -> None:
    facts = assemble_service4_review_facts(**_fixture())
    payload = render_service4_review_docx(facts)
    with ZipFile(BytesIO(payload)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
        settings_xml = archive.read("word/settings.xml").decode("utf-8")
        header_xml = "".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("word/header") and name.endswith(".xml")
        )
    assert "TOC \\o" in document_xml
    assert 'w:updateFields w:val="true"' in settings_xml
    assert "第一部分 GEO 优化试点方案" in document_xml
    assert "第二部分 优化前后效果对比" in document_xml
    assert "绝对变化" in document_xml and "相对变化" in document_xml
    assert "样本量" in document_xml and "稳定性" in document_xml
    assert "附录 A：全部候选问题组与选择评分" in document_xml
    assert "附录 B：逐单元样本量登记" in document_xml
    assert "附录 C：完整品牌格局与网站结构" in document_xml
    assert "附录 D：签发限制" in document_xml
    assert "w:tblHeader" in document_xml and 'w:tblLayout w:type="fixed"' in document_xml
    assert "动态服务四项目" in document_xml
    assert "盛邦安全  |  GEO 试点与效果验证" in header_xml
    assert "ans_before" not in document_xml
    assert "cfg_before" not in document_xml
    assert "prj_fixture" not in document_xml


def test_formal_renderer_uses_dynamic_formal_chrome_and_code() -> None:
    facts = assemble_service4_review_facts(**_fixture())
    facts["document_status"] = "formal"

    payload = render_service4_review_docx(facts)

    with ZipFile(BytesIO(payload)) as archive:
        combined_xml = "".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name == "word/document.xml"
            or name.startswith("word/header")
            or name.startswith("word/footer")
        )
    assert "GEO-S4-V2-FORMAL-20260812" in combined_xml
    assert "正式报告" in combined_xml
    assert "预正式" not in combined_xml
    assert "禁止外发" not in combined_xml


def test_scoring_function_does_not_accept_performance_inputs() -> None:
    # Guard the public pure seam: it has no target-brand/rank/metric parameter.
    assert "target_brand" not in score_service4_candidate_groups.__annotations__
