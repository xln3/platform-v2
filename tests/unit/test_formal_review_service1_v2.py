from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile

from geo_platform.reports import formal_review_service1
from geo_platform.reports.formal_review_service1 import (
    _answer_excerpt,
    _load_native_answer_anchors,
)
from PIL import Image

from domain.reporting.formal_review_docx import FormalDocument, build_report_code
from domain.reporting.formal_review_service1_docx import (
    _mention_view,
    _metrics_explanation,
    _native_toc,
    _source_share_donut,
)


def _png(width: int = 1000, height: int = 2000) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format="PNG")
    return output.getvalue()


def test_mention_view_removes_sidebar_and_only_draws_reviewed_box() -> None:
    crop, label, anchored = _mention_view(
        _png(),
        "doubao",
        image_kind="answer_screenshot",
        anchor={"bboxes": [[300, 900, 120, 40]]},
    )

    with Image.open(crop) as image:
        assert image.width < 1000  # runtime sidebar was removed
        red_pixels = [
            pixel
            for pixel in image.get_flattened_data()
            if pixel[0] > 150 and pixel[0] > pixel[1] * 1.4 and pixel[0] > pixel[2] * 1.4
        ]
        assert red_pixels
    assert anchored is True
    assert label.startswith("被提及位置")


def test_share_image_retains_clean_horizontal_canvas_without_fake_frame() -> None:
    crop, label, anchored = _mention_view(
        _png(),
        "yiyan",
        image_kind="share_image",
        anchor=None,
    )
    with Image.open(crop) as image:
        assert image.width == 1000
        assert image.getpixel((2, 2)) == (255, 255, 255)
    assert anchored is False
    assert "无可复核" in label


def test_clean_answer_evidence_retains_canvas_and_labels_native_dom_anchor() -> None:
    crop, label, anchored = _mention_view(
        _png(),
        "yiyan",
        image_kind="answer_excerpt_screenshot",
        anchor={"bboxes": [[120, 800, 220, 60]], "method": "dom_text_block_v1"},
    )

    with Image.open(crop) as image:
        assert image.width == 1000
    assert anchored is True
    assert "DOM" in label


def test_answer_excerpt_keeps_target_context_instead_of_only_the_answer_prefix() -> None:
    text = "前置分析" * 100 + "盛邦安全的核心能力说明" + "后续分析" * 100

    excerpt = _answer_excerpt(text, "盛邦安全", limit=180)

    assert len(excerpt) <= 182
    assert "盛邦安全" in excerpt
    assert excerpt.startswith("…")
    assert excerpt.endswith("…")


def test_answer_excerpt_removes_presentation_markup() -> None:
    excerpt = _answer_excerpt(
        "### 主流方案<br>**盛邦安全** | `能力说明`",
        "盛邦安全",
    )

    assert excerpt == "主流方案 盛邦安全 ； 能力说明"


def test_native_answer_anchor_rechecks_quote_hash_and_image_geometry(monkeypatch) -> None:
    answer_text = "前文。盛邦安全具备验证能力。后文。"
    anchored_text = "盛邦安全具备验证能力。"
    start = answer_text.index(anchored_text)
    row = {
        "from_pub_id": "ans_1",
        "evidence_pub_id": "evi_1",
        "capture_time": "2026-08-12T12:00:00+08:00",
        "text_start": start,
        "text_end": start + len(anchored_text),
        "quote_hash": sha256(anchored_text.encode()).hexdigest(),
        "bbox": {
            "x": 100,
            "y": 200,
            "width": 300,
            "height": 50,
            "image_width": 1000,
            "image_height": 2000,
            "anchor_method": "dom_text_block_v1",
        },
    }

    class _Result:
        def fetchall(self):
            return [row]

    class _Connection:
        def execute(self, *_args, **_kwargs):
            return _Result()

    @contextmanager
    def _connection(*_args, **_kwargs):
        yield _Connection()

    monkeypatch.setattr(formal_review_service1, "tenant_connection", _connection)
    answers = [{"pub_id": "ans_1", "response_text": answer_text}]

    anchors = _load_native_answer_anchors("postgresql://unused", "tnt_1", answers, "盛邦安全")
    assert anchors["ans_1"] == {
        "bboxes": [[100, 200, 300, 50]],
        "method": "dom_text_block_v1",
        "evidence_pub_id": "evi_1",
    }

    row["quote_hash"] = "0" * 64
    assert _load_native_answer_anchors("postgresql://unused", "tnt_1", answers, "盛邦安全") == {}


def test_source_share_donut_accounts_for_top_sites_and_other_sites() -> None:
    stream = _source_share_donut(
        ["a.example", "b.example"],
        [45, 25],
        total=100,
        title="全部引用的网站来源占比",
    )

    with Image.open(stream) as image:
        assert image.size == (1500, 820)
        assert image.getbbox() is not None


def test_native_toc_is_clickable_page_number_field_and_requests_refresh() -> None:
    facts = {
        "target_brand": "盛邦安全",
        "project_name": "测试项目",
        "window": {"start": "2026-08-10", "end": "2026-08-12"},
        "generated_at": "2026-08-12T12:00:00+08:00",
    }
    document = FormalDocument(title="测试报告", subtitle="测试", facts=facts)

    _native_toc(document)

    body_xml = document.document.element.xml
    settings_xml = document.document.settings.element.xml
    assert 'TOC \\o "1-3" \\h \\z \\u' in body_xml
    assert "w:updateFields" in settings_xml
    assert 'w:val="true"' in settings_xml


def _all_word_xml(payload: bytes) -> str:
    with ZipFile(BytesIO(payload)) as archive:
        return "\n".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("word/") and name.endswith(".xml")
        )


def test_formal_document_chrome_uses_dynamic_brand_and_has_no_review_warning() -> None:
    facts = {
        "target_brand": "示例客户品牌",
        "project_name": "正式评估项目",
        "window": {"start": "2026-08-10", "end": "2026-08-12"},
        "generated_at": "2026-08-12T12:00:00+08:00",
        "document_status": "formal",
    }
    document = FormalDocument(title="测试报告", subtitle="服务 1", facts=facts)
    report_code = build_report_code(facts, service_number=1, version="V2")
    document.cover(report_code=report_code)
    _metrics_explanation(document, target_brand=facts["target_brand"])

    xml = _all_word_xml(document.save())
    assert report_code == "GEO-S1-V2-FORMAL-20260812"
    assert "示例客户品牌" in xml
    assert "正式报告" in xml
    for forbidden in ("盛邦安全", "SBAQ", "预正式", "禁止外发", "禁外发", "内部审阅"):
        assert forbidden not in xml


def test_legacy_review_status_keeps_clear_review_warning_without_customer_code() -> None:
    facts = {
        "target_brand": "另一客户品牌",
        "project_name": "审阅项目",
        "window": {"start": "2026-08-10", "end": "2026-08-12"},
        "generated_at": "2026-08-12T12:00:00+08:00",
        "document_status": "pre_formal_review_nonproduction_data",
    }
    document = FormalDocument(title="测试报告", subtitle="服务 1", facts=facts)
    report_code = build_report_code(facts, service_number=1, version="V2")
    document.cover(report_code=report_code)

    xml = _all_word_xml(document.save())
    assert report_code == "GEO-S1-V2-REVIEW-20260812"
    assert "另一客户品牌" in xml
    assert "预正式审阅稿" in xml
    assert "禁止外发" in xml
    assert "SBAQ" not in xml
    assert "盛邦安全" not in xml
