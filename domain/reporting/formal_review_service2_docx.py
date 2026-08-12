"""Customer-readable Service-2 V2 DOCX renderer with visual evidence."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from PIL import Image, ImageDraw, ImageFont

from domain.reporting.formal_review_docx import (
    FONT_BOLD,
    MUTED,
    FormalDocument,
    _fmt_datetime,
    _set_font,
    add_native_toc,
    build_report_code,
    is_formal_document,
)


def _caption(doc: FormalDocument, text: str) -> None:
    paragraph = doc.document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_after = Pt(5)
    run = paragraph.add_run(text)
    _set_font(run, size=7.8)
    run.font.italic = True
    run.font.color.rgb = RGBColor.from_string(MUTED)


def _hyperlink(paragraph: Any, url: str, text: str | None = None) -> None:
    relationship_id = paragraph.part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), relationship_id)
    run = OxmlElement("w:r")
    properties = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    properties.extend((color, underline))
    run.append(properties)
    node = OxmlElement("w:t")
    node.text = text or url
    run.append(node)
    link.append(run)
    paragraph._p.append(link)


def _image_stream(image: Image.Image) -> BytesIO:
    stream = BytesIO()
    image.save(stream, format="PNG", optimize=True)
    stream.seek(0)
    return stream


def _answer_views(
    payload: bytes, anchor: dict[str, Any] | None
) -> tuple[BytesIO, BytesIO | None, str]:
    with Image.open(BytesIO(payload)) as source:
        full = source.convert("RGB")
    full_stream = _image_stream(full)
    if not anchor or not isinstance(anchor.get("bbox"), list):
        return full_stream, None, "原图中没有经复核的可视命中坐标"

    raw_bbox = [int(value) for value in anchor["bbox"]]
    if len(raw_bbox) != 4:
        return full_stream, None, "历史标注坐标无效"
    x, y, width, height = raw_bbox
    x0 = max(0, min(x, full.width - 1))
    y0 = max(0, min(y, full.height - 1))
    x1 = max(x0 + 1, min(x + width, full.width))
    y1 = max(y0 + 1, min(y + height, full.height))

    horizontal_margin = max(90, int((x1 - x0) * 0.45))
    vertical_margin = max(150, int((y1 - y0) * 1.6))
    crop_left = max(0, x0 - horizontal_margin)
    crop_top = max(0, y0 - vertical_margin)
    crop_right = min(full.width, x1 + horizontal_margin)
    crop_bottom = min(full.height, y1 + vertical_margin)
    if crop_right - crop_left < 850:
        center = (x0 + x1) // 2
        crop_left = max(0, center - 520)
        crop_right = min(full.width, center + 520)
    crop = full.crop((crop_left, crop_top, crop_right, crop_bottom))
    draw = ImageDraw.Draw(crop)
    stroke = max(5, min(crop.size) // 120)
    draw.rectangle(
        (
            x0 - crop_left,
            y0 - crop_top,
            x1 - crop_left,
            y1 - crop_top,
        ),
        outline="#DC2626",
        width=stroke,
    )
    label = str(anchor.get("label") or "AI 回答命中表述")
    font = ImageFont.truetype(str(FONT_BOLD), max(24, crop.width // 36))
    padding = 12
    label_box = draw.textbbox((0, 0), label, font=font)
    label_width = min(crop.width - 24, label_box[2] - label_box[0] + padding * 2)
    label_height = label_box[3] - label_box[1] + padding * 2
    badge_y = max(8, y0 - crop_top - label_height - 12)
    draw.rounded_rectangle(
        (8, badge_y, 8 + label_width, badge_y + label_height),
        radius=8,
        fill="#FFFFFF",
        outline="#DC2626",
        width=max(3, stroke // 2),
    )
    draw.text((8 + padding, badge_y + padding // 2), label, font=font, fill="#991B1B")
    method = str(anchor.get("method") or "")
    if method.startswith("dom_"):
        anchor_note = "采集时 DOM 文本坐标"
    elif method.startswith("ocr_"):
        anchor_note = "采集时 OCR 文本坐标"
    else:
        anchor_note = "历史截图人工复核坐标"
    return full_stream, _image_stream(crop), f"{anchor_note}；红框仅标命中原句"


def _add_answer_screenshot(
    doc: FormalDocument,
    case: dict[str, Any],
    payload: bytes | None,
) -> None:
    if not payload:
        doc.callout(
            "回答截图缺口",
            "采集记录指向回答截图，但本次生成未能加载图像。正式签发前必须恢复资产。",
            kind="warning",
        )
        return
    full, crop, note = _answer_views(payload, case.get("answer_anchor"))
    if crop is None:
        doc.callout(
            "回答图未展示",
            "本条没有可复核的命中坐标。报告保留上方逐字原句和上下文，但不放入"
            "缩小后不可读、又无法准确标注的整页截图。原始图仍在受控证据库中留存。",
            kind="warning",
        )
        return

    del full
    with Image.open(crop) as crop_image:
        crop_ratio = crop_image.width / max(crop_image.height, 1)
    crop.seek(0)
    crop_width = min(16.2, 12.6 * crop_ratio)
    paragraph = doc.document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.add_run().add_picture(crop, width=Cm(max(7.0, crop_width)))
    _caption(
        doc,
        f"AI 回答命中区域及必要上下文；红框仅强调判定原句。{note}。",
    )


def _source_capture_status(row: dict[str, Any] | None) -> str:
    if not row:
        return "未执行网页快照"
    if row.get("capture_status") != "captured":
        return f"抓取失败：{row.get('error') or '未知错误'}"
    if row.get("content_status") != "ok":
        return (
            f"页面不可作为核查证据：content_status={row.get('content_status')}，"
            f"HTTP={row.get('http_status') or '—'}"
        )
    if not row.get("matched_terms"):
        return "页面已抓取，但未找到可逐字标注的核查锚点"
    return "核查页面已抓取且锚点可见"


def _add_source_capture(
    doc: FormalDocument,
    source: dict[str, Any],
    capture: dict[str, Any] | None,
    *,
    number: str,
    max_width_cm: float = 14.8,
) -> bool:
    url = str(source.get("url") or "")
    status = _source_capture_status(capture)
    if (
        capture is None
        or capture.get("capture_status") != "captured"
        or capture.get("content_status") != "ok"
        or not capture.get("matched_terms")
        or not isinstance(capture.get("payload"), bytes)
    ):
        # Broken, 404 or unanchored URLs remain in the operator capture manifest, not
        # in a customer evidence card.  Listing an unusable URL adds no evidence and
        # can be mistaken for support merely because it is visible in the report.
        return False
    paragraph = doc.document.add_paragraph()
    lead = paragraph.add_run("原网址：")
    _set_font(lead, size=8.5)
    lead.bold = True
    _hyperlink(paragraph, url)
    doc.paragraph(status, bold_lead="网页快照状态：")
    payload = capture.get("payload")
    assert isinstance(payload, bytes)
    paragraph = doc.document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.add_run().add_picture(BytesIO(payload), width=Cm(max_width_cm))
    fallback = capture.get("transport_fallback")
    fallback_note = "；HTTPS 拒绝连接后改用同主机 HTTP 页面，已在审计清单记录" if fallback else ""
    terms = "、".join(str(value) for value in capture.get("matched_terms") or [])
    _caption(
        doc,
        f"图 {number}  事实核查网页可见区域；红框/黄底为网页中逐字锚点：{terms}{fallback_note}。",
    )
    role = str(source.get("role") or "")
    if "反证" in role:
        doc.callout(
            "如何使用本图",
            "本图可核对公开页面中的具体数字和市场口径。应结合原回答口径阅读，"
            "不能把网络安全硬件总体份额直接替换成 ASM 专项份额。",
            kind="success",
        )
    else:
        doc.callout(
            "如何使用本图",
            "本图只能证明目标品牌官网存在相应产品/能力描述；它不能证明 AI 回答中的"
            "“弱”、高低顺序、价格优劣或“不如”结论，因此本条仍为证据不足。",
        )
    return True


def _toc(doc: FormalDocument, *, answer_cases: int, source_cases: int) -> None:
    del answer_cases, source_cases
    add_native_toc(doc)
    doc.page_break()


def _case_page(
    doc: FormalDocument,
    case: dict[str, Any],
    *,
    answer_screenshot: bytes | None,
    source_captures: dict[str, dict[str, Any]],
    page_break: bool = True,
    section_label: str | None = None,
) -> None:
    if page_break:
        doc.page_break()
    heading_label = section_label or f"5.{str(case['case_id']).removeprefix('A-')}"
    doc.heading(f"{heading_label} {case['case_id']} · {case['direction']}")
    if case.get("customer_scope_note"):
        doc.callout(
            "补充案例边界",
            str(case["customer_scope_note"]),
            kind="info",
        )
    verdict_kind = "warning" if case["factcheck_verdict"] != "refuted" else "warning"
    doc.callout(
        str(case["expression_verdict"]),
        str(case["customer_conclusion"]),
        kind=verdict_kind,
    )
    doc.table(
        ["字段", "实测内容"],
        [
            ("表述方向", case["direction"]),
            (
                "采样环境",
                f"{case['platform_label']} · {case['region']} · {case['mode']} · "
                f"{_fmt_datetime(case['capture_time'])}",
            ),
            ("原始问题", case["question"]),
            ("表述类型", case["statement_type"]),
            ("表达判定", case["expression_verdict"]),
            ("事实判定", case["fact_verdict"]),
            (
                "重复执行",
                f"同一回答/摘录经过 {case['judgment_executions']} 次复核；本报告合并为 1 案",
            ),
        ],
        widths=(29, 143),
        font_size=8.1,
    )
    doc.paragraph(str(case["attribution"]), bold_lead="归因边界：")
    doc.heading("原回答与命中表述", level=2)
    doc.callout("AI 回答原句", str(case["evidence_quote"]), kind="warning")
    doc.paragraph(str(case["answer_context"]), bold_lead="上下文摘录：")
    paragraph = doc.document.add_paragraph()
    lead = paragraph.add_run("平台入口：")
    _set_font(lead, size=8.5)
    lead.bold = True
    platform_url = str(case.get("platform_entry_url") or "")
    if platform_url:
        _hyperlink(paragraph, platform_url)
    note = paragraph.add_run(f"  （{case['platform_url_note']}）")
    _set_font(note, size=8)
    note.font.color.rgb = RGBColor.from_string(MUTED)
    _add_answer_screenshot(doc, case, answer_screenshot)

    doc.heading("事实核查解释", level=2)
    doc.paragraph(str(case["why"]), bold_lead="为什么这样判：")
    doc.paragraph(str(case["customer_conclusion"]), bold_lead="客户可用结论：")
    sources = list(case.get("factcheck_sources") or [])
    if not sources:
        doc.callout(
            "公开来源缺口",
            "本条没有形成公开核查 URL，不能进入确定风险清单。",
            kind="warning",
        )
    visible_sources = []
    for source in sources:
        capture = source_captures.get(str(source.get("url") or ""))
        if (
            capture
            and capture.get("capture_status") == "captured"
            and capture.get("content_status") == "ok"
            and capture.get("matched_terms")
            and isinstance(capture.get("payload"), bytes)
        ):
            visible_sources.append((source, capture))
    if sources and not visible_sources:
        doc.callout(
            "公开核查页缺口",
            "本案未取得可逐字定位的有效公开核查页；404、抓取失败和未命中锚点的"
            "链接不在本报告展示，也不作为支持或反证。",
            kind="warning",
        )
    for source_index, (source, capture) in enumerate(visible_sources, 1):
        doc.heading(f"核查来源 {source_index}", level=3)
        _add_source_capture(
            doc,
            source,
            capture,
            number=f"5-{case['case_id']}-{source_index}",
            max_width_cm=13.0 if len(sources) > 1 else 14.8,
        )


def _source_case_page(
    doc: FormalDocument,
    case: dict[str, Any],
    screenshot_payload: bytes | None,
) -> None:
    doc.heading(f"5.{case['case_id']} · 公开信源正文风险", level=2)
    doc.callout(
        str(case["expression_verdict"]),
        str(case["customer_conclusion"]),
        kind="warning",
    )
    doc.table(
        ["字段", "实测内容"],
        [
            ("表述方向", case["direction"]),
            ("信源网站", case["platform_label"]),
            ("命中原句", case["evidence_quote"]),
            ("表达判定", case["expression_verdict"]),
            ("事实判定", case["fact_verdict"]),
        ],
        widths=(29, 143),
        font_size=8.1,
    )
    url = str(case.get("source_url") or "")
    if url:
        paragraph = doc.document.add_paragraph()
        lead = paragraph.add_run("原信源网页：")
        _set_font(lead, size=8.5)
        lead.bold = True
        _hyperlink(paragraph, url, "打开原网页")
    descriptor = case.get("source_screenshot") or {}
    if screenshot_payload:
        anchor = {
            "bbox": descriptor.get("bbox"),
            "label": f"{case.get('target_brand') or '目标品牌'}所在段落",
        }
        full, crop, note = _answer_views(screenshot_payload, anchor)
        image = crop or full
        paragraph = doc.document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.add_run().add_picture(image, width=Cm(15.8))
        _caption(
            doc,
            f"信源网页中目标品牌所在段落的可视证据；{note}。完整原始网页截图继续保留在证据存储中。",
        )
    else:
        doc.callout(
            "信源截图缺口",
            "该信源案例尚未载入目标品牌段落截图；正式签发前必须补齐 DOM 锚点和可读上下文图。",
            kind="warning",
        )
    doc.numbered(
        [
            "该原句来自已成功抽取的信源正文，而不是 AI 回答文本。",
            "系统先确认正文提及目标品牌，再检查品牌所在上下文段落是否存在贬低别人或被贬低的表述。",
            str(case["attribution"]),
        ]
    )


def render_service2_v2_docx(
    facts: dict[str, Any],
    *,
    answer_screenshots: dict[str, bytes] | None = None,
    source_captures: dict[str, dict[str, Any]] | None = None,
    source_case_screenshots: dict[str, bytes] | None = None,
) -> bytes:
    """Render corrected Service-2 V2 facts and real visual evidence."""

    service2 = facts["service2"]
    delivery = service2.get("delivery_v2")
    if not isinstance(delivery, dict):
        raise ValueError("service2.delivery_v2_missing")
    answer_screenshots = answer_screenshots or {}
    source_captures = source_captures or {}
    source_case_screenshots = source_case_screenshots or {}
    citations = delivery["citation_funnel"]
    fetch = delivery["source_fetch"]
    judgments = delivery["judgment_funnel"]
    cases = list(delivery["cases"])
    source_cases = list(delivery.get("source_cases") or [])
    supplemental_cases = list(delivery.get("supplemental_factcheck_cases") or [])
    source_audit = delivery["source_content_audit"]
    verdicts = delivery["case_verdict_counts"]

    doc = FormalDocument(
        title="品牌 GEO 内容生态风险核查报告",
        subtitle="服务 2 · AI 拉踩表述、公开事实核查与可视证据",
        facts=facts,
    )
    doc.cover(report_code=build_report_code(facts, service_number=2, version="V2"))
    _toc(doc, answer_cases=len(cases), source_cases=len(source_cases))

    doc.heading("1. 执行摘要")
    doc.kpis(
        [
            ("独立合格回答", str(citations["eligible_answers"]), "不是判定执行次数"),
            (
                "带引用回答",
                str(citations["answers_with_citation"]),
                f"共 {citations['citation_references']:,} 条引用记录",
            ),
            (
                "去重风险线索",
                str(judgments["unique_cases"]),
                f"回答 {len(cases)} 项 / 信源正文 {len(source_cases)} 项",
            ),
            ("公开证据冲突", str(verdicts.get("refuted", 0)), "其余为证据不足/未定案"),
        ]
    )
    doc.callout(
        "核心结论",
        f"本报告只保留直接涉及{facts['target_brand']}的线索："
        f"{len(cases)} 项来自 AI 回答，{len(source_cases)} 项来自公开信源正文。"
        f"{verdicts.get('refuted', 0)} 项存在公开数据冲突，"
        f"{verdicts.get('unverifiable', 0)} 项缺少同口径公开证据。"
        "可以确认的是 AI 回答出现了无来源排序、贬低性比较或不完整负向标签；"
        "没有证据证明这些内容由竞品或第三方撰写、投放。",
        kind="warning",
    )
    doc.paragraph(
        "本报告把三件事分开回答：第一，AI 回答是否出现拉踩式或负向比较表达；"
        f"第二，被引用的公开信源是否提及{facts['target_brand']}，以及品牌所在段落是否"
        "存在贬低别人或被贬低的表述；第三，具体比较是否有公开事实支持。"
        "‘表达成立、事实无法核验’并不矛盾，"
        "也不等于该表述为真。"
    )
    doc.table(
        ["客户应采取的动作", "适用线索", "含义"],
        [
            ("立即纠正/平台反馈", "公开证据冲突", "原回答数字无来源且与公开数据冲突"),
            ("保留监测并补证", "拉踩表达成立、事实不足", "先保留截图与原句，不宣称虚假事实已坐实"),
            ("重新采集完整上下文", "表头/比较维度缺失", "未形成完整语义前不进入确定风险清单"),
        ],
        widths=(40, 56, 76),
        font_size=8.2,
    )

    doc.page_break()
    doc.heading("2. 数据与核查漏斗")
    doc.callout(
        (f"{judgments['ok_answer_executions']}/{judgments['ok_source_executions']} 口径修正"),
        f"旧稿中的 {judgments['ok_answer_executions']} 和 "
        f"{judgments['ok_source_executions']} 分别是成功的回答判定执行行与信源判定执行行；"
        f"对应的独立对象只有 {judgments['ok_distinct_answers']} 份回答和 "
        f"{judgments['ok_distinct_source_documents']} 份信源文档。"
        "同一回答可能经过多次复核，因此绝不能写成"
        f"‘{judgments['ok_answer_executions']} 条 AI 回答、"
        f"{judgments['ok_source_executions']} 个信源文档’。",
        kind="success",
    )
    doc.table(
        ["阶段", "实际数量", "去重/状态口径", "客户应如何理解"],
        [
            ("合格 AI 回答", citations["eligible_answers"], "独立回答", "本报告母体"),
            (
                "带引用回答",
                citations["answers_with_citation"],
                "独立回答",
                "回答中至少捕获 1 个 URL",
            ),
            (
                "回答引用记录",
                f"{citations['citation_references']:,}",
                "最新分析批次",
                f"{citations['unique_canonical_urls']:,} 个唯一规范化 URL",
            ),
            (
                "正文抓取立项",
                fetch["documents"],
                f"{fetch['runs_with_documents']} 个采集批次",
                f"成功 {fetch['ok']}；不是每回答全部抓取",
            ),
            (
                "回答表述判定",
                judgments["ok_distinct_answers"],
                f"{judgments['ok_answer_executions']} 次成功执行",
                "按独立回答报告覆盖",
            ),
            (
                "信源正文判定",
                judgments["ok_distinct_source_documents"],
                f"{judgments['ok_source_executions']} 次成功执行",
                f"其中目标品牌段落风险线索 {len(source_cases)} 项",
            ),
            (
                "风险线索",
                judgments["unique_cases"],
                f"{judgments['flagged_executions']} 次命中执行",
                f"回答 {len(cases)} 项 / 信源正文 {len(source_cases)} 项",
            ),
        ],
        widths=(35, 28, 53, 56),
        font_size=7.8,
    )
    doc.heading("2.1 为什么每份回答看起来有约 30 个 URL，却只抓了少量正文", level=2)
    doc.paragraph(
        f"分析层没有丢弃 URL：{citations['answers_with_citation']} 份带引用回答共保留 "
        f"{citations['citation_references']:,} 条引用，平均每份带引用回答 "
        f"{citations['avg_refs_cited_answers']:.2f} 条，单份最多 "
        f"{citations['max_refs_one_answer']} 条。问题发生在正文抓取规划层。"
    )
    doc.table(
        ["来源正文漏斗", "当前值", "含义"],
        [
            (
                "回答中发现 URL",
                f"{citations['citation_references']:,} 条 / "
                f"{citations['unique_canonical_urls']:,} 个唯一 URL",
                "分析层保留的引用母体",
            ),
            (
                "正文抓取立项",
                f"{fetch['documents']} 份",
                "进入网页正文抓取的历史子集",
            ),
            ("正文抓取成功", f"{fetch['ok']} 份", "可进入品牌提及和段落风险检查"),
            (
                "回答—文档关系",
                f"{fetch['answer_document_relations']} 条 / "
                f"{fetch['answers_with_planned_documents']} 份回答",
                "同一 URL 抓一次，但应关联全部引用它的回答",
            ),
        ],
        widths=(39, 63, 70),
        font_size=8,
    )
    doc.numbered(
        [
            "本窗口的历史正文抓取存在覆盖截断，不能视为客户约定的完整服务口径。",
            "当前规划按回答保留来源、跨回答 URL 去重，并关联全部引用回答；"
            "实际报告仍必须披露发现、成功、品牌提及、判定和截图覆盖。",
            "历史窗口尚未补齐的页面会明确标记为证据缺口，不追溯声称已经完成。",
        ]
    )

    doc.heading("2.2 信源正文如何检查拉踩", level=2)
    doc.table(
        ["检查阶段", "当前数量", "判定规则"],
        [
            ("正文抓取成功", source_audit["successful_documents"], "网页正文可读取"),
            (
                "目标品牌可视提及",
                source_audit["documents_with_target_brand_visual_anchor"],
                f"正文与网页 DOM 均逐字出现{facts['target_brand']}，并保存像素锚点",
            ),
            (
                "完成信源段落判定",
                source_audit["judged_distinct_documents"],
                "只检查目标品牌所在上下文段落",
            ),
            (
                "形成客户风险线索",
                source_audit["flagged_target_brand_cases"],
                "段落中存在目标品牌贬低别人或被贬低的可核对原句",
            ),
        ],
        widths=(42, 29, 101),
        font_size=7.8,
    )
    doc.numbered(
        [
            source_audit["method"],
            f"不含目标品牌的竞品间比较不会进入{facts['target_brand']}客户主报告，"
            "但可留在运营复核清单。",
            "本窗只抓取并成功解析了极小的信源子集，因此当前“没有信源正文风险线索”"
            "不能解释为全网没有风险。",
        ]
    )

    doc.heading("2.3 截图与文本坐标覆盖", level=2)
    doc.paragraph(
        "每条客户案例都应同时具备可核对原句、回答截图和文本坐标；信源正文案例还应"
        "具备网页截图及命中段落。缺少任一环节时，本报告明确披露证据缺口，不自动"
        "推测命中位置，也不把抓取失败页当作证据。"
    )
    doc.callout(
        "本次报告的处理",
        "报告优先使用采集时保存的 DOM 或 OCR 文本坐标；历史回答仅使用已人工复核的"
        "坐标。网页 404、抓取失败或未命中时只披露失败原因，旧截图不自动猜框。",
        kind="success",
    )

    doc.page_break()
    doc.heading("3. 判定方法与证据边界")
    doc.table(
        ["层级", "回答的问题", "本报告输出"],
        [
            ("表达判定", "是否存在高低排序、贬低、不如、弱等拉踩式表达", "成立 / 线索命中未定性"),
            ("事实核查", "公开资料能否支持或推翻具体比较", "公开冲突 / 有支持 / 无法核验"),
            ("主体归因", "谁生成了这句话、是否能认定竞品投放", "仅归因 AI 回答；无作者证据不外推"),
            ("可视证据", "原回答与核查网页能否逐字定位", "原图、红框、URL、抓取状态与证据边界"),
        ],
        widths=(31, 78, 63),
        font_size=8,
    )
    doc.heading("3.1 结论词的直白解释", level=2)
    doc.bullets(
        [
            "公开证据冲突：已有页面提供了相反数字或明确口径；仍需避免跨口径替换。",
            "无法核验：没有同指标、同场景、同时间、同样本的可靠比较；不代表原话真实。",
            "拉踩式表达成立：句子形式存在无依据的高低比较；不自动等同于事实虚假。",
            "暂不定性：原句或表头不完整，无法说明负向词对应的维度，必须复采。",
        ]
    )

    doc.heading("4. 风险线索总表")
    doc.heading("4.1 AI 回答中直接涉及目标品牌的线索", level=2)
    doc.table(
        ["案例", "对象与比较方向", "命中原句", "表达判定", "事实判定"],
        [
            (
                case["case_id"],
                case["direction"],
                str(case["evidence_quote"])[:78]
                + ("…" if len(str(case["evidence_quote"])) > 78 else ""),
                case["expression_verdict"],
                case["fact_verdict"],
            )
            for case in cases
        ],
        widths=(14, 39, 57, 32, 30),
        font_size=6.9,
    )
    doc.numbered(
        [
            f"主报告只呈现直接涉及{facts['target_brand']}的回答线索；"
            f"另有 {judgments['excluded_competitor_only_cases']} 项不直接涉及目标品牌的"
            f"运营复核线索，其中 {len(supplemental_cases)} 项仅作为核查方法附例，其余"
            "不在客户报告展示；这些线索均不计客户风险 KPI。",
            "同一回答、同一摘录的重复复核会合并为一个客户案例。",
        ]
    )

    doc.heading("4.2 被引用信源正文中的目标品牌段落线索", level=2)
    if source_cases:
        doc.table(
            ["案例", "来源网站", "命中原句", "表达判定", "事实判定"],
            [
                (
                    case["case_id"],
                    case["platform_label"],
                    str(case["evidence_quote"])[:86]
                    + ("…" if len(str(case["evidence_quote"])) > 86 else ""),
                    case["expression_verdict"],
                    case["fact_verdict"],
                )
                for case in source_cases
            ],
            widths=(14, 35, 61, 32, 30),
            font_size=6.9,
        )
    else:
        doc.callout(
            "当前没有可签发的信源正文案例",
            "这只表示已抓取并完成判定的历史小样本中未形成目标品牌段落风险线索；"
            "由于正文抓取覆盖严重不足，不能据此声称全部引用网页都没有风险。",
            kind="warning",
        )

    doc.heading("4.3 扩展行业事实核查（不计客户主结论）", level=2)
    if supplemental_cases:
        doc.table(
            ["案例", "比较对象", "命中原句", "事实判定", "与主报告关系"],
            [
                (
                    case["case_id"],
                    case["direction"],
                    str(case["evidence_quote"])[:86]
                    + ("…" if len(str(case["evidence_quote"])) > 86 else ""),
                    case["fact_verdict"],
                    f"仅展示核查方法，不计{facts['target_brand']}风险 KPI",
                )
                for case in supplemental_cases
            ],
            widths=(14, 44, 54, 31, 29),
            font_size=6.8,
        )
        doc.numbered(
            [
                "该扩展案例保留，是因为其公开数据冲突证据完整，可帮助客户审阅事实核查"
                "的写法与截图版式。",
                f"案例不直接涉及{facts['target_brand']}，不进入执行摘要、风险线索数量或"
                "客户处置建议。",
            ]
        )
    else:
        doc.numbered(["当前没有证据完整且适合展示的扩展行业事实核查案例。"])

    doc.heading("5. 逐案可视证据")
    doc.paragraph(
        "每个案例均按同一顺序展示：谁对谁、问题与采样环境、AI 原句、原回答截图、"
        "公开核查网址、网页截图/标注、证据为何充分或不足、客户可用结论。"
    )
    for case in cases:
        _case_page(
            doc,
            case,
            answer_screenshot=answer_screenshots.get(str(case["answer_pub_id"])),
            source_captures=source_captures,
            # Let Word flow the cases naturally.  An explicit break immediately
            # after a full evidence page can produce a blank page in LibreOffice.
            page_break=False,
        )
    for case in source_cases:
        descriptor = case.get("source_screenshot") or {}
        _source_case_page(
            doc,
            case,
            source_case_screenshots.get(str(descriptor.get("pub_id") or "")),
        )
    for index, case in enumerate(supplemental_cases, 1):
        _case_page(
            doc,
            case,
            answer_screenshot=answer_screenshots.get(str(case["answer_pub_id"])),
            source_captures=source_captures,
            page_break=False,
            section_label=f"5.S{index}",
        )

    doc.heading("A. 审计限制与正式签发条件")
    doc.bullets(delivery["limitations"])
    doc.heading("A.1 证据完备状态与正式运行验收", level=2)
    doc.table(
        ["状态", "证据能力", "验收标准"],
        [
            ("已具备", "按独立回答和案例统计", "不得把重复复核次数写成回答或文档数"),
            (
                "新采集生效",
                "按回答规划来源、跨回答去重并扇出关系",
                "披露发现→唯一→计划→成功→关系漏斗",
            ),
            (
                "已具备",
                "客户案例网页快照与逐字标注",
                "每案可追溯回答图、原 URL、网页图和锚点",
            ),
            ("新采集生效", "风险证据优先抓取", "有引用原句的来源优先于普通引用"),
            (
                "新采集生效",
                "回答正文证据图与原生文本坐标",
                "采集时保存文本区间和像素坐标；报告优先读取受控原图及原生框",
            ),
        ],
        widths=(30, 64, 78),
        font_size=7.8,
    )
    if is_formal_document(facts):
        doc.callout(
            "报告状态",
            "本报告基于已冻结的正式评估窗口事实签发。来源抓取与截图锚点覆盖仍以"
            "正文披露的实际数量为准，不将未覆盖页面解释为无风险。",
        )
    else:
        doc.callout(
            "本稿状态",
            "本报告基于当前联调/试采样数据和历史证据修复，用于检查内容与版式。"
            "在来源抓取覆盖和正式重复采样完成前，不应作为全网风险完备性结论；"
            "历史回答是否具备原生框以本报告披露的实际覆盖为准。",
            kind="warning",
        )
    return doc.save()


__all__ = ["render_service2_v2_docx"]
