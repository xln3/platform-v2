from __future__ import annotations

import re
from io import BytesIO
from zipfile import ZipFile

from geo_platform.reports.formal_review_service2 import _group_cases
from PIL import Image

from domain.reporting.formal_review_docx import FormalDocument
from domain.reporting.formal_review_service2_docx import (
    _add_answer_screenshot,
    _answer_views,
    _source_capture_status,
    render_service2_v2_docx,
)


def _flagged_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "judgment_pub_id": "dpj_private",
        "subject_pub_id": "ans_01",
        "platform": "doubao",
        "target_brand": "绿盟科技",
        "evidence_quote": "预算有限/工具型产品 | 画方科技 > 绿盟科技",
        "factcheck_verdict": "unverifiable",
        "factcheck_summary": "没有同口径价格、性能和第三方评测。",
        "factcheck_source_url": "https://example.com/fact",
        "query_text": "测试问题",
        "response_text": "上下文 预算有限/工具型产品 | 画方科技 > 绿盟科技 后续",
        "answer_model": "doubao",
        "answer_region": "上海",
        "answer_mode": "deep_think",
        "answer_capture_time": "2026-08-12T10:00:00+08:00",
        "screenshot_ref": "file:///tmp/answer.png",
    }
    row.update(overrides)
    return row


def test_group_cases_collapses_rejudgments_and_keeps_ids_audit_only() -> None:
    rows = [
        _flagged_row(judgment_pub_id="dpj_one"),
        _flagged_row(judgment_pub_id="dpj_two"),
        _flagged_row(judgment_pub_id="dpj_three"),
    ]
    cases = _group_cases(rows)
    assert len(cases) == 1
    assert cases[0]["judgment_executions"] == 3
    assert cases[0]["direction"] == "豆包 AI 回答对绿盟科技作出负向评价"
    assert cases[0]["audit_refs"] == ["dpj_one", "dpj_two", "dpj_three"]
    assert "竞品或第三方" in cases[0]["attribution"]


def test_group_cases_never_guesses_bbox_without_sidecar() -> None:
    without_anchor = _group_cases([_flagged_row()])
    assert without_anchor[0]["answer_anchor"] is None

    with_anchor = _group_cases(
        [_flagged_row()],
        answer_anchor_overrides={
            "ans_01": [
                {
                    "quote_contains": "画方科技 > 绿盟科技",
                    "bbox": [10, 20, 100, 40],
                    "label": "人工复核",
                }
            ]
        },
    )
    assert with_anchor[0]["answer_anchor"]["bbox"] == [10, 20, 100, 40]
    assert with_anchor[0]["answer_anchor"]["method"] == "manual_reviewed_bbox"


def test_answer_view_only_draws_reviewed_anchor() -> None:
    image = Image.new("RGB", (800, 1000), "white")
    payload = BytesIO()
    image.save(payload, format="PNG")
    full, crop, note = _answer_views(
        payload.getvalue(),
        {"bbox": [100, 500, 400, 60], "label": "命中原句"},
    )
    assert full.getbuffer().nbytes > 0
    assert crop is not None and crop.getbuffer().nbytes > 0
    assert "人工复核" in note

    _, no_crop, no_anchor_note = _answer_views(payload.getvalue(), None)
    assert no_crop is None
    assert "没有" in no_anchor_note


def test_answer_evidence_card_omits_unreadable_full_page_thumbnail() -> None:
    image = Image.new("RGB", (800, 1000), "white")
    payload = BytesIO()
    image.save(payload, format="PNG")
    facts = {
        "target_brand": "盛邦安全",
        "project_name": "测试项目",
        "generated_at": "2026-08-12T10:00:00+08:00",
        "window": {"start": "2026-08-10", "end": "2026-08-12"},
    }
    document = FormalDocument(title="测试", subtitle="测试", facts=facts)

    _add_answer_screenshot(
        document,
        {"answer_anchor": {"bbox": [100, 500, 400, 60], "method": "dom_text_block_v1"}},
        payload.getvalue(),
    )
    rendered = document.save()
    with ZipFile(BytesIO(rendered)) as archive:
        media = [name for name in archive.namelist() if name.startswith("word/media/")]
        xml = archive.read("word/document.xml").decode("utf-8")
    assert len(media) == 1
    assert "完整原图缩略" not in xml
    assert "红框按采集时保存的文本位置绘制" in xml


def test_source_capture_status_rejects_404_and_unanchored_page() -> None:
    assert "不可作为" in _source_capture_status(
        {
            "capture_status": "captured",
            "content_status": "http_error",
            "http_status": 404,
        }
    )
    assert "未找到" in _source_capture_status(
        {
            "capture_status": "captured",
            "content_status": "ok",
            "matched_terms": [],
        }
    )


def _minimal_facts() -> dict[str, object]:
    case = _group_cases([_flagged_row()])[0]
    return {
        "target_brand": "测试客户品牌",
        "project_name": "测试项目",
        "generated_at": "2026-08-12T10:00:00+08:00",
        "window": {"start": "2026-08-10", "end": "2026-08-12"},
        "service2": {
            "delivery_v2": {
                "citation_funnel": {
                    "eligible_answers": 130,
                    "answers_with_citation": 81,
                    "citation_references": 2365,
                    "unique_canonical_urls": 1598,
                    "avg_refs_cited_answers": 29.2,
                    "max_refs_one_answer": 133,
                },
                "source_fetch": {
                    "documents": 36,
                    "runs_with_documents": 8,
                    "ok": 30,
                    "answer_document_relations": 28,
                    "answers_with_planned_documents": 12,
                },
                "judgment_funnel": {
                    "ok_answer_executions": 975,
                    "ok_distinct_answers": 100,
                    "ok_source_executions": 42,
                    "ok_distinct_source_documents": 4,
                    "flagged_executions": 3,
                    "flagged_distinct_answers": 1,
                    "unique_cases": 1,
                    "excluded_competitor_only_cases": 0,
                },
                "case_verdict_counts": {"unverifiable": 1},
                "cases": [case],
                "source_cases": [],
                "source_content_audit": {
                    "successful_documents": 30,
                    "documents_with_target_brand_visual_anchor": 3,
                    "target_brand_source_screenshots": 3,
                    "judged_distinct_documents": 4,
                    "flagged_target_brand_cases": 0,
                    "method": "只检查目标品牌所在段落。",
                },
                "post_analysis_wiring": {
                    "tasks": 3,
                    "items": 5,
                    "screenshots": 3,
                    "annotated": 2,
                },
                "limitations": ["当前不是正式运行数据。"],
            }
        },
    }


def test_renderer_is_customer_readable_and_omits_internal_ids_and_own_articles() -> None:
    payload = render_service2_v2_docx(_minimal_facts())
    with ZipFile(BytesIO(payload)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    visible = "\n".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))
    assert "130" in visible and "100" in visible
    assert "2,365" in visible and "1,598" in visible
    assert "975" not in visible
    assert "42 个" not in visible
    assert "口径修正" not in xml
    assert "执行行" not in xml
    assert "采集批次" not in xml
    assert "豆包 AI 回答对绿盟科技作出负向评价" in xml
    assert "dpj_" not in xml
    assert "己方 GEO 稿件" not in xml
    assert "盛邦安全" not in xml
    assert "测试客户品牌" in xml
    assert "无法核验" in xml
    assert 'TOC \\o "1-3" \\h \\z \\u' in xml
