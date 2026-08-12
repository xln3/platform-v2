"""确定性 GEO 报价单 DOCX renderer。

版心、字号、表格、页眉页脚和商务措辞均由代码固定；动态内容只能来自已校验的
QuotationPlan。布局参数按 ``client-sbaq/报价单-盛邦-final(2).docx`` 逐项复刻。
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Sequence
from datetime import date
from io import BytesIO
from typing import Literal, cast

from docx import Document
from docx.document import Document as DocumentObject
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor
from docx.table import Table, _Cell, _Row
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from .models import ExistingQueryVariants, OpportunityVariants, QuotationPlan

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ISSUER_COMPANY = "北京硅基守望科技有限公司"
VALID_WORKING_DAYS = 30

_FONT = "宋体"
_BLACK = RGBColor(0x00, 0x00, 0x00)
_BODY_GRAY = RGBColor(0x44, 0x44, 0x44)
_MUTED_GRAY = RGBColor(0x80, 0x80, 0x80)

_SEC_LABELS = {
    "search": "搜索型产品或服务",
    "experience": "体验型产品或服务",
    "trust": "专业信任型产品或服务",
    "mixed": "复合决策型产品或服务",
}

_SERVICE_ROWS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "品牌GEO推荐结果评测",
        (
            (
                "lead",
                "围绕品牌核心业务场景，全面评估品牌在AI回答中的认知覆盖状况，"
                "输出品牌AI可见性总体评测报告。",
            ),
            ("subhead", "1）评测流程说明："),
            (
                "bullet",
                "·核心业务查询设计：选3个业务问题，每个问题扩展3组语义变体问题"
                "（具体扩展说明见附录一、扩展后问题见附录二）",
            ),
            ("bullet", "·覆盖3个主流AI平台：豆包、DeepSeek、文心一言"),
            ("bullet", "·2个地域账号独立采样：北京、上海"),
            ("bullet", "·每个问题重复2次取统计结果"),
            ("subhead", "2）评测指标说明："),
            ("bullet", "·品牌提及率：品牌在 AI 回答中被主动提及的比例"),
            ("bullet", "·推荐排名分布：品牌在 AI 推荐结果中的排名位置分布"),
            ("bullet", "·Top1/Top3/Top5出现率：品牌进入核心推荐区域的比例"),
            ("bullet", "·竞品对比：品牌与主要竞品在AI推荐结果中的表现差异"),
        ),
    ),
    (
        "品牌GEO内容生态风险核查",
        (
            (
                "lead",
                "检测第三方GEO内容中的风险，通过识别竞品比较信息、推荐内容、来源链接等"
                "发掘潜在涉及“抹黑、拉踩”的推广内容。",
            ),
            ("subhead", "1）风险内容说明："),
            ("bullet", "· AI回答中涉及品牌、产品及竞品的负向比较与疑似“抹黑、拉踩”表述"),
            ("bullet", "· AI回答所列第三方信源页面中涉及己方品牌与产品的风险内容"),
            ("bullet", "· 逐条事实核查与交付证据链"),
        ),
    ),
    (
        "官网内容AI引用能效评估",
        (
            (
                "lead",
                "评估品牌官网作为AI信源的能效：分析AI检索到的官网内容是否被AI引用，"
                "定位影响品牌AI可见性的官网内容问题，并输出优化建议。",
            ),
            ("subhead", "1）测试说明："),
            ("bullet", "·官网引用率：AI回答引用官网URL作为信源的比例"),
            ("bullet", "·内容采纳率：官网内容被AI理解并用于生成回答的比例"),
        ),
    ),
    (
        "GEO试点与效果验证",
        (
            (
                "lead",
                "通过外部信息源建设与内容优化，提升品牌在AI搜索中的提及、引用和推荐表现，"
                "输出GEO优化试点方案及优化前后效果对比报告。",
            ),
            ("subhead", "1）测试说明："),
            (
                "bullet",
                "·GEO试点验证查询设计：3个业务问题，每个问题扩展3组语义变体"
                "（具体扩展说明见附录一、扩展后问题见附录三）",
            ),
            (
                "bullet",
                "·采用与服务项目1中品牌AI认知评测一致的评测方式，对优化前后 AI 品牌认知"
                "指标进行对比分析，验证GEO优化效果",
            ),
        ),
    ),
)

_TERMS = (
    "1. 本报价有效期为30个工作日，生效日期以报价日期为准。贵单位确认后，我司将以"
    "报价产品或服务的相关价格签订合同。",
    "2. 因AI平台算法更迭频繁，如项目在服务过程中因平台算法等调整无法按照既定方案"
    "执行，双方需签订补充协议对内容进行调整，并通过电话或邮件等方式进行确认。",
    "3. 因涉及行业及品类的专业性与合规性，相关产品或服务的内容基础材料均来源于"
    "品牌方，我方基于品牌方提供内容进行评测。",
    "4. 特定情况：（1）合作期间品牌方出现重大舆情事件，需重新评估合作及相关费用；"
    "（2）品牌方因自身原因需中途更新评测问题集的，需重新评估费用。",
    "5. 本报价单及附件内价格、方案、技术参数、商务条件均为我方保密商业信息。收件方"
    "仅可用于本次项目内部评审，不得向外部第三方、竞品、其他合作方进行披露、转发、"
    "摘抄。如因贵方泄密造成我方损失，我方有权取消本次报价效力并追究相关责任。"
    "本保密义务在报价失效后仍然持续有效。",
)


def _set_east_asia_font(run: Run, name: str = _FONT) -> None:
    run.font.name = name
    run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), name)


def _style_run(
    run: Run,
    *,
    size: float,
    bold: bool = False,
    underline: bool = False,
    color: RGBColor = _BLACK,
) -> Run:
    _set_east_asia_font(run)
    run.font.size = Pt(size)
    run.bold = bold
    run.underline = underline
    run.font.color.rgb = color
    return run


def _paragraph_spacing(
    paragraph: Paragraph,
    *,
    before: float = 0,
    after: float = 0,
    line: float = 1.0,
) -> None:
    paragraph.paragraph_format.space_before = Pt(before)
    paragraph.paragraph_format.space_after = Pt(after)
    paragraph.paragraph_format.line_spacing = line


def _add_text_paragraph(
    document: DocumentObject,
    text: str,
    *,
    size: float = 10.5,
    bold: bool = False,
    align: WD_ALIGN_PARAGRAPH | None = None,
    before: float = 0,
    after: float = 0,
    line: float = 1.0,
    keep_with_next: bool = False,
    color: RGBColor = _BLACK,
) -> Paragraph:
    paragraph = document.add_paragraph()
    if align is not None:
        paragraph.alignment = align
    _paragraph_spacing(paragraph, before=before, after=after, line=line)
    paragraph.paragraph_format.keep_with_next = keep_with_next
    _style_run(paragraph.add_run(text), size=size, bold=bold, color=color)
    return paragraph


def _add_heading(
    document: DocumentObject,
    text: str,
    *,
    level: Literal[1, 2, 3],
    centered: bool = False,
) -> Paragraph:
    paragraph = document.add_paragraph()
    paragraph.style = f"Heading {level}"
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER if centered else WD_ALIGN_PARAGRAPH.LEFT
    sizes = {1: 14.0, 2: 13.0, 3: 10.5}
    _paragraph_spacing(
        paragraph,
        before=0 if level == 1 else 5,
        after=4 if level != 3 else 2,
        line=1.0,
    )
    paragraph.paragraph_format.keep_with_next = True
    _style_run(paragraph.add_run(text), size=sizes[level], bold=True)
    return paragraph


def _set_cell_margins(
    cell: _Cell, *, top: int = 0, left: int = 108, bottom: int = 0, right: int = 108
) -> None:
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for margin, value in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        element = tc_mar.find(qn(f"w:{margin}"))
        if element is None:
            element = OxmlElement(f"w:{margin}")
            tc_mar.append(element)
        element.set(qn("w:w"), str(value))
        element.set(qn("w:type"), "dxa")


def _shade_cell(cell: _Cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)


def _cant_split(row: _Row) -> None:
    tr = row._tr
    tr_pr = tr.get_or_add_trPr()
    if tr_pr.find(qn("w:cantSplit")) is None:
        tr_pr.append(OxmlElement("w:cantSplit"))


def _repeat_table_header(row: _Row) -> None:
    tr = row._tr
    tr_pr = tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    tr_pr.append(header)


def _set_column_widths(table: Table, widths_mm: Sequence[float]) -> None:
    table.autofit = False
    for column, width in zip(table.columns, widths_mm, strict=True):
        column.width = Mm(width)
        for cell in column.cells:
            cell.width = Mm(width)


def _cell_paragraph(
    cell: _Cell,
    text: str,
    *,
    size: float,
    bold: bool = False,
    underline: bool = False,
    align: WD_ALIGN_PARAGRAPH | None = None,
    line: float = 1.0,
    after: float = 0,
) -> Paragraph:
    paragraph = (
        cell.paragraphs[0]
        if len(cell.paragraphs) == 1 and not cell.text
        else cast(Paragraph, cell.add_paragraph())
    )
    if align is not None:
        paragraph.alignment = align
    _paragraph_spacing(paragraph, after=after, line=line)
    _style_run(
        paragraph.add_run(text),
        size=size,
        bold=bold,
        underline=underline,
    )
    return paragraph


def _prepare_cell(cell: _Cell, *, vertical_center: bool = True) -> None:
    cell.text = ""
    cell.vertical_alignment = (
        WD_CELL_VERTICAL_ALIGNMENT.CENTER if vertical_center else WD_CELL_VERTICAL_ALIGNMENT.TOP
    )
    _set_cell_margins(cell)


def _add_service_table(document: DocumentObject) -> None:
    table = document.add_table(rows=6, cols=4)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_column_widths(table, (12.3, 21.6, 128.3, 17.8))

    for row in table.rows:
        _cant_split(row)
        for cell in row.cells:
            _prepare_cell(cell)

    headers = ("序号", "服务项目", "服务内容", "价格")
    _repeat_table_header(table.rows[0])
    for cell, text in zip(table.rows[0].cells, headers, strict=True):
        _shade_cell(cell, "F0F0F0")
        _cell_paragraph(
            cell,
            text,
            size=10.5,
            bold=True,
            align=WD_ALIGN_PARAGRAPH.CENTER,
            line=1.15,
        )

    for index, (service_name, content) in enumerate(_SERVICE_ROWS, start=1):
        row = table.rows[index]
        _cell_paragraph(
            row.cells[0],
            str(index),
            size=10.5,
            bold=True,
            align=WD_ALIGN_PARAGRAPH.CENTER,
            line=1.15,
        )
        _cell_paragraph(
            row.cells[1],
            service_name,
            size=10.5,
            bold=True,
            align=WD_ALIGN_PARAGRAPH.CENTER,
            line=1.15,
        )
        content_cell = row.cells[2]
        content_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        for kind, text in content:
            _cell_paragraph(
                content_cell,
                text,
                size=10.5,
                bold=kind == "lead",
                underline=kind == "subhead",
                line=1.15,
            )

    total = table.rows[5]
    _cell_paragraph(
        total.cells[1],
        "合计",
        size=10.5,
        bold=True,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        line=1.15,
    )


def _configure_styles(document: DocumentObject) -> None:
    normal = document.styles["Normal"]
    normal.font.name = _FONT
    normal.font.size = Pt(10.5)
    normal._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), _FONT)
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.line_spacing = 1.0
    for name, size in (("Heading 1", 14.0), ("Heading 2", 13.0), ("Heading 3", 10.5)):
        style = document.styles[name]
        style.font.name = _FONT
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = _BLACK
        style._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), _FONT)
    bullet = document.styles["List Bullet"]
    bullet.font.name = _FONT
    bullet._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), _FONT)
    bullet.paragraph_format.space_after = Pt(0)


def _add_page_break(document: DocumentObject) -> None:
    paragraph = document.add_paragraph()
    _paragraph_spacing(paragraph, line=1.0)
    run = paragraph.add_run()
    page_break = OxmlElement("w:br")
    page_break.set(qn("w:type"), "page")
    run._r.append(page_break)


def _add_page_field(paragraph: Paragraph) -> None:
    run = paragraph.add_run()
    _style_run(run, size=9.0)
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    placeholder = OxmlElement("w:t")
    placeholder.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for element in (begin, instruction, separate, placeholder, end):
        run._r.append(element)


def _configure_page(document: DocumentObject) -> None:
    section = document.sections[0]
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    section.top_margin = Mm(18)
    section.bottom_margin = Mm(18)
    section.left_margin = Mm(15)
    section.right_margin = Mm(15)
    section.header_distance = Mm(12.7)
    section.footer_distance = Mm(12.7)

    header = section.header
    paragraph = header.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _paragraph_spacing(paragraph, line=1.0)
    _style_run(
        paragraph.add_run(
            "本报价为保密商业资料，未经出具方书面同意，禁止对外泄露、转发。"
            "报价仅供本次合作评估使用。"
        ),
        size=7.5,
        color=_MUTED_GRAY,
    )

    footer = section.footer
    footer_paragraph = footer.paragraphs[0]
    footer_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _paragraph_spacing(footer_paragraph, line=1.0)
    _add_page_field(footer_paragraph)


def _format_date(value: date) -> str:
    return f"{value.year}年{value.month}月{value.day}日"


def _add_cover(document: DocumentObject, brand_name: str, quote_date: date) -> None:
    _add_text_paragraph(
        document,
        "GEO验证服务报价单",
        size=15.0,
        bold=True,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        after=1,
    )
    _add_text_paragraph(
        document,
        f"{brand_name}      报价日期：{_format_date(quote_date)}     "
        f"报价有效期：{VALID_WORKING_DAYS}个工作日",
        size=9.0,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        after=1,
        line=1.5,
    )
    _add_service_table(document)
    for index, term in enumerate(_TERMS):
        _add_text_paragraph(
            document,
            term,
            size=9.0,
            color=_BODY_GRAY,
            before=6 if index == 0 else 0,
            after=1,
            line=1.15,
        )
    _add_text_paragraph(document, "", size=10.5, after=1, line=1.15)
    signature = document.add_paragraph()
    signature.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    _paragraph_spacing(signature, after=10, line=1.15)
    _style_run(
        signature.add_run("报价人：__________________　　　　　　　　公司：" + ISSUER_COMPANY),
        size=10.5,
    )


def _appendix_body(document: DocumentObject, text: str, *, after: float = 3) -> Paragraph:
    return _add_text_paragraph(document, text, size=10.5, after=after, line=1.2)


def _add_labelled_paragraph(
    document: DocumentObject,
    label: str,
    text: str,
    *,
    size: float = 9.0,
    after: float = 0,
    keep_with_next: bool = False,
) -> Paragraph:
    paragraph = document.add_paragraph()
    _paragraph_spacing(paragraph, after=after, line=1.0)
    paragraph.paragraph_format.keep_with_next = keep_with_next
    _style_run(paragraph.add_run(label), size=size, bold=True)
    _style_run(paragraph.add_run(text), size=size)
    return paragraph


def _add_bullet(
    document: DocumentObject,
    label: str,
    text: str,
    *,
    size: float = 8.5,
    bold: bool = False,
    after: float = 0,
) -> Paragraph:
    paragraph = document.add_paragraph(style="List Bullet")
    _paragraph_spacing(paragraph, after=after, line=1.05)
    _style_run(paragraph.add_run(f"{label}  {text}"), size=size, bold=bold)
    return paragraph


def _add_validation_table(
    document: DocumentObject, opportunities: Sequence[OpportunityVariants]
) -> None:
    table = document.add_table(rows=1 + min(2, len(opportunities)), cols=6)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_column_widths(table, (49, 23, 23, 23, 23, 39))
    headers = (
        "拟新增目标词",
        "DeepSeek\n优化前",
        "DeepSeek\n优化后",
        "文心一言\n优化前",
        "文心一言\n优化后",
        "验证状态",
    )
    _repeat_table_header(table.rows[0])
    for cell, text in zip(table.rows[0].cells, headers, strict=True):
        _prepare_cell(cell)
        _shade_cell(cell, "F0F0F0")
        _cell_paragraph(
            cell,
            text,
            size=8.0,
            bold=True,
            align=WD_ALIGN_PARAGRAPH.CENTER,
            line=1.0,
        )
    for row, opportunity in zip(table.rows[1:], opportunities[:2], strict=True):
        _cant_split(row)
        for cell in row.cells:
            _prepare_cell(cell)
        values = (opportunity.keyword, "待实测", "待实测", "待实测", "待实测", "项目执行阶段验证")
        for cell, text in zip(row.cells, values, strict=True):
            _cell_paragraph(
                cell,
                text,
                size=7.5,
                align=WD_ALIGN_PARAGRAPH.CENTER,
                line=1.0,
            )


def _add_appendix_one(
    document: DocumentObject,
    *,
    brand_name: str,
    plan: QuotationPlan,
) -> None:
    _add_page_break(document)
    _add_heading(document, "附录一 Query优化方案说明", level=1, centered=True)
    _add_heading(document, "一、优化目标", level=2)
    _appendix_body(
        document,
        '本附录所展示的"Query优化"工作，是基于客户提供的业务关键词，将其改写为更贴近'
        "真实用户在AI平台上的提问方式。消费者在使用AI搜索时，通常不会直接输入产品名称"
        "或技术术语，而是以自然语言描述自己的需求场景。",
    )
    _appendix_body(
        document,
        "因此，我们将客户提供的关键词转化为模拟用户真实提问的检索语句，使其能够触发"
        "AI平台给出包含厂商推荐的回答。同时为每条优化后的提问生成多个语义变体（正式换述、"
        "换角度、口语化），覆盖不同用户群体的表达习惯，确保评测结果能够反映品牌在真实"
        "检索场景中的可见性。",
    )

    _add_heading(document, "二、优化方法论", level=2)
    _appendix_body(
        document,
        "本方案基于消费心理学中的搜索品-体验品-信任品（SEC）经典理论（Nelson, 1970; "
        "Darby & Karni, 1973），结合我司自研的九维消费认知量化模型，对品牌所属品类进行"
        "消费心理画像分析，进而完善Query优化。",
    )
    _appendix_body(
        document,
        f"结合本次目标词特征，{plan.category_label}更接近{_SEC_LABELS[plan.sec_profile]}。"
        f"{plan.category_analysis}",
    )
    _appendix_body(document, f"基于该画像，{plan.intent_diagnosis}")

    _add_heading(document, "三、优化示例", level=2)
    for opportunity in plan.opportunities[:3]:
        _add_labelled_paragraph(
            document,
            "拟新增目标词：",
            opportunity.keyword,
            keep_with_next=True,
        )
        _add_bullet(
            document,
            "优化",
            opportunity.optimized_query,
            size=9.0,
            bold=True,
        )
        _add_bullet(document, "改写目的", opportunity.rewrite_rationale, size=8.5)
        _add_bullet(
            document,
            "验证状态",
            "报价阶段未实测，实际效果以项目执行结果为准",
            size=8.5,
            after=2,
        )
    _add_text_paragraph(
        document,
        "*本节仅展示Query设计逻辑，不代表任何AI平台的实测推荐结果。",
        size=8.0,
        after=2,
        line=1.0,
    )

    _add_heading(document, "四、效果验证口径", level=2)
    _add_text_paragraph(
        document,
        "以下示例将在项目执行阶段，于相同平台、地域、账号与重复次数条件下开展优化前后"
        "对照测试；报价阶段不填入未经采样的推荐次数。",
        size=9.0,
        after=2,
        line=1.1,
    )
    _add_validation_table(document, plan.opportunities)
    _add_text_paragraph(
        document,
        f"结论：针对{brand_name}的实际优化效果，将以正式采样后的品牌提及率、推荐排名分布"
        "及厂商推荐次数为准。本报价单不将文案推演表述为实测提升数据。",
        size=9.0,
        bold=True,
        before=3,
        after=3,
        line=1.1,
    )
    _add_text_paragraph(
        document,
        "文献参考：Nelson, P. (1970). Information and Consumer Behavior. Journal of "
        "Political Economy, 78(2), 311–329.",
        size=8.0,
        color=_MUTED_GRAY,
        line=1.0,
    )
    _add_text_paragraph(
        document,
        "Darby, M. R., & Karni, E. (1973). Free Competition and the Optimal Amount of "
        "Fraud. Journal of Law and Economics, 16(1), 67–88.",
        size=8.0,
        line=1.0,
    )


def _selected_by_group(
    selected: Iterable[ExistingQueryVariants],
) -> OrderedDict[str, list[ExistingQueryVariants]]:
    ordered = sorted(selected, key=lambda row: int(row.source_id[1:]))
    groups: OrderedDict[str, list[ExistingQueryVariants]] = OrderedDict()
    for row in ordered:
        groups.setdefault(row.group, []).append(row)
    return groups


def _add_appendix_two(document: DocumentObject, plan: QuotationPlan) -> None:
    _add_page_break(document)
    _add_heading(document, "附录二 原推广Query与变体构建说明", level=1, centered=True)
    _add_heading(document, "核心检索问题库与语义变体", level=3)
    groups = _selected_by_group(plan.selected_queries)
    _add_text_paragraph(
        document,
        f"以下为围绕品牌{len(groups)}个核心业务方向设计的{len(plan.selected_queries)}条核心"
        "业务问题及其语义变体。变体A为正式换述，变体B为换角度表达，变体C为口语化表达，"
        "覆盖用户实际提问的多种表述方式。选取3条问题及其语义变体为项目1·品牌AI认知"
        "评测的查询集。",
        size=9.0,
        after=2,
        line=1.1,
    )
    for group, rows in groups.items():
        _add_text_paragraph(
            document,
            group,
            size=10.5,
            bold=True,
            before=3,
            after=1,
            keep_with_next=True,
        )
        for row in rows:
            _add_text_paragraph(
                document,
                row.original,
                size=9.0,
                bold=True,
                after=0,
                line=1.0,
                keep_with_next=True,
            )
            _add_bullet(document, "A", row.variant_a)
            _add_bullet(document, "B", row.variant_b)
            _add_bullet(document, "C", row.variant_c, after=1)


def _add_appendix_three(document: DocumentObject, plan: QuotationPlan) -> None:
    _add_page_break(document)
    _add_heading(document, "附录三 新增Query优化与语义变体全表", level=1, centered=True)
    _add_text_paragraph(
        document,
        f"以下为{len(plan.opportunities)}条拟新增机会词、推荐型优化问句及其语义变体。"
        "变体A为正式换述，变体B为换角度表达，变体C为口语化表达——覆盖用户实际提问的"
        "多种表述方式。选取3条提示词及其语义变体为项目4·GEO试点与效果验证的查询集。",
        size=9.0,
        after=2,
        line=1.1,
    )
    for row in plan.opportunities:
        _add_labelled_paragraph(
            document,
            "拟新增目标词：",
            row.keyword,
            size=9.0,
            keep_with_next=True,
        )
        _add_bullet(document, "优化", row.optimized_query, size=9.0, bold=True)
        _add_bullet(document, "A", row.variant_a)
        _add_bullet(document, "B", row.variant_b)
        _add_bullet(document, "C", row.variant_c, after=2)


def render_quotation_docx(
    *,
    brand_name: str,
    quote_date: date,
    plan: QuotationPlan,
) -> bytes:
    """按固定模板渲染可直接下载的 DOCX bytes。"""
    document = Document()
    _configure_styles(document)
    _configure_page(document)
    document.core_properties.title = f"{brand_name} GEO验证服务报价单"
    document.core_properties.subject = "GEO验证服务报价"
    document.core_properties.author = ISSUER_COMPANY
    document.core_properties.comments = "由GEO报价单生成服务生成；报价阶段未包含平台实测结果。"

    _add_cover(document, brand_name, quote_date)
    _add_appendix_one(document, brand_name=brand_name, plan=plan)
    _add_appendix_two(document, plan)
    _add_appendix_three(document, plan)

    output = BytesIO()
    document.save(output)
    return output.getvalue()
