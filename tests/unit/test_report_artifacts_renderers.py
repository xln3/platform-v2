"""报告产物渲染器（domain/reporting/artifacts.py）单元测试。

覆盖：PDF reportlab 重写（CJK 字体嵌入 + fail-loud 无字体分支）、
DOCX python-docx 化（chart 表格降级）、HTML 增强（inline SVG + 转义）、
组件类型分发与未知类型/空 series 的诚实降级。
"""

from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import pytest

from domain.reporting import artifacts
from domain.reporting.artifacts import (
    RenderingError,
    render_docx,
    render_html,
    render_pdf,
)

CHINESE_SECTIONS = [
    {"component_type": "section", "title": "摘要", "body": "盛邦安全在本窗口被提及 3 次。"},
]

FULL_COMPONENTS = [
    {"component_type": "section", "title": "摘要", "body": "中文正文第一段。\n第二行。"},
    {
        "component_type": "kpi",
        "title": "提及率",
        "body": "42.5%",
        "trace_token": "trc_unit_kpi",
    },
    {
        "component_type": "chart",
        "title": "趋势图",
        "body": "近两日趋势。",
        "series": [
            {"date": "2026-08-08", "value": "1"},
            {"date": "2026-08-09", "value": 3},
        ],
    },
    {"component_type": "evidence", "title": "截图证据", "body": "回答截图与分析事实联动。"},
    {"component_type": "recommendation", "title": "行动建议", "body": "加大官网技术文档投入。"},
]


def test_pdf_embeds_cjk_font_for_chinese_content() -> None:
    pdf = render_pdf("盛邦安全 GEO 报告", CHINESE_SECTIONS)
    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF-")
    assert pdf.rstrip().endswith(b"%%EOF")
    # TTF 子集嵌入标记：中文字形真实落进内容流，不是手搓 PDF 的 metadata 侧信道。
    assert b"/FontFile2" in pdf


def test_pdf_without_font_and_non_latin1_content_fails_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(artifacts._PDF_FONT_ENV, raising=False)
    monkeypatch.setattr(artifacts, "_PDF_FONT_FALLBACKS", ())
    monkeypatch.setattr(artifacts, "_pdf_font_resolution", None)
    with pytest.raises(RenderingError, match="no usable CJK font"):
        render_pdf("中文标题", CHINESE_SECTIONS)


def test_pdf_ascii_content_uses_helvetica_fallback() -> None:
    pdf = render_pdf("GEO report", [{"title": "Summary", "body": "plain ascii body"}])
    assert pdf.startswith(b"%PDF-")
    assert pdf.rstrip().endswith(b"%%EOF")


def test_docx_contains_chinese_text_and_chart_table() -> None:
    payload = render_docx("盛邦安全 GEO 报告", FULL_COMPONENTS)
    with ZipFile(BytesIO(payload)) as archive:
        assert "word/document.xml" in archive.namelist()
        document_xml = archive.read("word/document.xml").decode()
    assert "盛邦安全 GEO 报告" in document_xml
    assert "提及率" in document_xml
    assert "trc_unit_kpi" in document_xml
    # chart 组件诚实降级为 date/value 表格
    assert "<w:tbl" in document_xml
    assert "2026-08-08" in document_xml


def test_html_renders_svg_chart_kpi_card_and_escapes_injection() -> None:
    html = render_html(
        "GEO 报告",
        [
            *FULL_COMPONENTS,
            {"component_type": "section", "title": "<script>alert(1)</script>", "body": "x"},
        ],
    ).decode()
    assert html.startswith("<!doctype html>")
    assert "<svg" in html
    assert "kpi-card" in html and "42.5%" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_chart_with_empty_series_degrades_honestly_in_all_formats() -> None:
    sections = [
        {"component_type": "chart", "title": "空图", "body": "没有数据。", "series": []},
    ]
    html = render_html("报告", sections).decode()
    assert "图表数据不可用" in html
    with ZipFile(BytesIO(render_docx("报告", sections))) as archive:
        document_xml = archive.read("word/document.xml").decode()
    assert "图表数据不可用" in document_xml
    # PDF 不崩即合格（占位说明文本经 TTF 子集编码，不断言原文）
    assert render_pdf("报告", sections).startswith(b"%PDF-")


def test_unknown_component_type_degrades_to_title_and_body() -> None:
    sections = [{"component_type": "mystery", "title": "未知组件", "body": "降级渲染。"}]
    html = render_html("报告", sections).decode()
    assert "未知组件" in html and "降级渲染。" in html
    with ZipFile(BytesIO(render_docx("报告", sections))) as archive:
        document_xml = archive.read("word/document.xml").decode()
    assert "未知组件" in document_xml
    assert render_pdf("报告", sections).startswith(b"%PDF-")
