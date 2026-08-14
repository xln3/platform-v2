from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from domain.reporting.formal_review_docx import FormalDocument, add_native_toc
from domain.reporting.formal_review_service1_docx import _hyperlink
from domain.reporting.formal_service1_delivery_docx import _clean_answer
from domain.reporting.libreoffice import refresh_docx_and_export_pdf
from domain.reporting.publication_qa import (
    _find_mojibake,
    _orphan_figure_caption_pages,
    compare_reexport,
    inspect_publication,
)


def test_customer_excerpt_repairs_mojibake_and_markdown_without_changing_raw_evidence() -> None:
    raw = "# 标题\n| 类型 | 对象 |\n|---|---|\n- 基线 â†’ 过滤\n天眼 Â·无代理"

    cleaned = _clean_answer(raw)

    assert "标题" in cleaned
    assert "类型 ｜ 对象" in cleaned
    assert "• 基线 → 过滤" in cleaned
    assert "天眼 ·无代理" in cleaned
    assert "|---|" not in cleaned
    assert "â†’" not in cleaned
    assert "Â·" not in cleaned


def test_publication_qa_detects_visible_mojibake() -> None:
    assert _find_mojibake("路径 â†’ 结果；天眼 Â·无代理") == ["Â·", "â†’"]


def test_publication_qa_detects_caption_page_without_image() -> None:
    pages = ["正文", "图 6-2 平台官方分享图片", "图 6-3 平台官方分享图片"]

    assert _orphan_figure_caption_pages(pages, {3}) == [2]


@pytest.mark.skipif(shutil.which("libreoffice") is None, reason="LibreOffice is unavailable")
def test_libreoffice_export_is_tagged_clickable_and_reexport_stable() -> None:
    title = "三个业务场景品牌 GEO 推荐结果评测报告"
    facts = {
        "project_name": "测试客户",
        "generated_at": datetime(2026, 8, 14, tzinfo=UTC),
        "document_status": "internal_review",
        "document_governance": {"prepared_by": "测试项目组"},
    }
    report = FormalDocument(title=title, subtitle="服务1", facts=facts)
    report.heading(title)
    add_native_toc(report, heading_levels="1-2")
    report.heading("1. 测试正文")
    report.paragraph("内部审核稿 · 中国标准时间（UTC+8） · 客户机密—仅限指定项目组")
    report.paragraph("AI生成原文，未经事实核验，不代表评测方结论")
    paragraph = report.document.add_paragraph()
    _hyperlink(paragraph, "https://example.com/a?b=1", text="https://example.com/a?b=1")

    docx, pdf = refresh_docx_and_export_pdf(report.save())
    qa = inspect_publication(
        docx=docx,
        pdf=pdf,
        expected_title=title,
        expected_status_label="内部审核稿",
        expected_urls=["https://example.com/a?b=1"],
        page_range=(1, 3),
    )
    assert qa["status"] == "passed", qa
    assert qa["pdf"]["tagged"] == "yes"
    assert qa["pdf"]["external_url_annotations"] >= 1

    second_docx, second_pdf = refresh_docx_and_export_pdf(docx)
    stability = compare_reexport(
        first_docx=docx,
        first_pdf=pdf,
        second_docx=second_docx,
        second_pdf=second_pdf,
    )
    assert stability["status"] == "passed", stability


def test_reviewed_legacy_report_is_detected_as_not_deliverable() -> None:
    workspace = Path(__file__).resolve().parents[3]
    root = (
        workspace
        / "client-sbaq/formal-reports-20260813/frp_94387df8ba4df75d3b26a9903e"
    )
    docx_path = root / "服务1_品牌GEO推荐结果评测报告_正式_20260813.docx"
    pdf_path = root / "服务1_品牌GEO推荐结果评测报告_正式_20260813.pdf"
    if not docx_path.is_file() or not pdf_path.is_file():
        pytest.skip("reviewed legacy fixture is not present")

    qa = inspect_publication(
        docx=docx_path.read_bytes(),
        pdf=pdf_path.read_bytes(),
        expected_title="品牌 GEO 推荐结果评测报告",
        expected_status_label="客户交付候选稿",
        expected_urls=["https://example.invalid/required-link"],
    )
    assert qa["status"] == "failed"
    assert qa["pdf"]["pages"] == 55
    assert qa["checks"]["tagged_pdf"] is False
    assert qa["checks"]["all_displayed_urls_clickable"] is False
    assert qa["diagnostics"]["blank_pages"]
