"""Compact customer narrative for governed Service-1 delivery artifacts."""

from __future__ import annotations

import re
from typing import Any

from docx.shared import Pt

from .formal_review_docx import (
    FormalDocument,
    _fmt_datetime,
    _set_font,
    add_native_toc,
    build_report_code,
)
from .formal_review_service1_docx import _add_screenshot_panel, _hyperlink
from .service1_governance import release_state_label

_PLATFORM_LABELS = {"doubao": "豆包", "deepseek": "DeepSeek", "yiyan": "文心一言"}
_CLIENT_FORBIDDEN = (
    "本次审阅口径",
    "审阅判断",
    "当前试采",
    "正式复测与签发检查",
    "冻结矩阵",
    "mode=",
    "[特殊字符]",
)


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value))
    except ValueError:
        return None


def _pct(value: object) -> str:
    return f"{(_to_float(value) or 0.0):.1f}%"


def _rank(value: object) -> str:
    number = _to_float(value)
    return "—" if number is None else f"{number:.1f}"


def _fraction(row: dict[str, Any], key: str = "mentions") -> str:
    return f"{int(row.get(key) or 0)}/{int(row.get('answers') or 0)}"


def _clean_answer(value: object, limit: int = 1400) -> str:
    text = str(value or "")
    # Preserve the raw capture in the evidence package, but repair known UTF-8-as-
    # Windows-1252 display artefacts in the customer-readable excerpt.
    replacements = {
        "â†’": "→",
        "Â·": "·",
        "Â ": " ",
        "â€œ": "“",
        "â€\u009d": "”",
        "â€™": "’",
        "â€“": "–",
        "â€”": "—",
        "â€¦": "…",
    }
    for source, replacement in replacements.items():
        text = text.replace(source, replacement)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"(?m)^\s*[-*_]{3,}\s*$", "", text)
    text = re.sub(r"(?m)^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$", "", text)
    text = re.sub(r"(?m)^\s*[-*+]\s+", "• ", text)
    text = re.sub(
        r"(?m)^\s*\|(.+)\|\s*$",
        lambda match: " ｜ ".join(
            cell.strip() for cell in match.group(1).split("|") if cell.strip()
        ),
        text,
    )
    text = re.sub(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _page(doc: FormalDocument, title: str) -> None:
    doc.page_break()
    doc.heading(title)


def _ai_disclaimer(doc: FormalDocument) -> None:
    doc.callout(
        "重要说明",
        "以下为 AI 生成原文，未经事实核验，不代表评测方结论。报告仅评测品牌是否出现、"
        "出现位次及回答所列引用。",
        kind="warning",
    )


def _add_visible_url(doc: FormalDocument, *, ordinal: object, title: object, url: str) -> None:
    lead = doc.document.add_paragraph()
    lead.paragraph_format.space_after = Pt(1)
    run = lead.add_run(f"{ordinal or '—'}. {title or '（未捕获标题）'}")
    _set_font(run, size=8)
    run.bold = True
    link = doc.document.add_paragraph()
    link.paragraph_format.space_after = Pt(5)
    _hyperlink(link, url, text=url)
    for run in link.runs:
        _set_font(run, size=7.2)


def _representative_pages(
    doc: FormalDocument,
    item: dict[str, Any],
    sample: dict[str, Any],
    screenshot: bytes | None,
    *,
    target_brand: str,
    start_new_page: bool = True,
) -> None:
    number = int(item["display_number"])
    heading = f"9.{number} {item['platform_label']}代表回答 · {item['group_title']}"
    if start_new_page:
        _page(doc, heading)
    else:
        doc.heading(heading)
    _ai_disclaimer(doc)
    doc.table(
        ["字段", "实测内容"],
        [
            ("实际问题", item["question"]),
            ("平台与地域", f"{item['platform_label']} · {item['region']}"),
            ("采集时间", _fmt_datetime(item.get("capture_time"))),
            (
                "目标品牌表现",
                f"提及，位次第 {item['target_rank']} 位"
                if item.get("target_rank") is not None
                else "未提及",
            ),
            ("回答所列引用", f"{item.get('citation_count') or 0} 条"),
            ("样本定位", str(sample.get("sample_id") or "—")),
        ],
        widths=(31, 141),
        font_size=8.1,
    )
    if screenshot:
        # Keep the evidence image and its caption together on a dedicated page.
        # Otherwise a short crop can fit below the metadata table while Word pushes
        # only the caption to the next page, creating an unusable caption-only page.
        doc.page_break()
        _add_screenshot_panel(
            doc,
            screenshot,
            platform=str(item["platform"]),
            number=number,
            image_kind=str(item.get("preferred_image_kind") or "answer_screenshot"),
            anchor=item.get("answer_anchor"),
            figure_prefix="9",
        )
    else:
        doc.callout("图片状态", "该代表样本没有可载入的回答图片。", kind="warning")

    _page(doc, f"9.{number}.1 回答原文摘录及其所列引用")
    _ai_disclaimer(doc)
    doc.paragraph(_clean_answer(sample.get("response_text")))
    citations = list(item.get("citations") or [])[:3]
    doc.heading("回答所列引用（节选）", level=2)
    if not citations:
        doc.paragraph("该回答没有捕获到引用 URL；不据此推断信息来源。")
    for citation in citations:
        url = str(citation.get("url") or "")
        if url:
            _add_visible_url(
                doc,
                ordinal=citation.get("ordinal"),
                title=citation.get("title"),
                url=url,
            )
    doc.paragraph(
        "本页仅展示最多3条可读链接；该样本的全部回答、全部引用及网页快照状态见样本索引和证据包。"
    )


def render_service1_delivery_docx(
    facts: dict[str, Any], *, screenshots: dict[str, bytes] | None = None
) -> bytes:
    """Render the 25–32 page customer report; bulk audit data stay in sidecars."""

    service1 = facts["service1"]
    delivery = service1.get("delivery_v3")
    if not isinstance(delivery, dict):
        raise ValueError("service1.delivery_v3_missing")
    screenshots = screenshots or {}
    target_brand = str(facts["target_brand"])
    target = delivery["target"]
    scope = delivery["scope"]
    scope_label = str(scope.get("scope_label") or "本次三组已测业务场景")
    title = f"{scope_label}品牌 GEO 推荐结果评测报告"
    facts = {**facts, "report_title": title}
    doc = FormalDocument(
        title=title,
        subtitle="服务 1 · AI 推荐可见性、同口径竞品对比与样本证据",
        facts=facts,
    )
    version = str((facts.get("document_governance") or {}).get("version") or "V1.0")
    doc.cover(report_code=build_report_code(facts, service_number=1, version=version))
    add_native_toc(doc, heading_levels="1-2")

    _page(doc, "1. 执行摘要")
    doc.kpis(
        [
            (
                "品牌提及率",
                _pct(target["mention_rate"]),
                target["mention_rate_fraction"] + " 条回答",
            ),
            ("平均推荐位次", _rank(target["avg_rank"]), "仅在已提及回答内"),
            (
                "Top 3出现率",
                _pct(target["top_rates"]["3"]),
                f"{target['top_counts']['3']}/{target['answers']}",
            ),
            ("可复算主样本", str(scope["answers"]), "完整明细见样本索引"),
        ]
    )
    interval = target.get("mention_rate_wilson_95") or []
    interval_text = (
        f"；95% Wilson区间为 {interval[0]:.1f}%–{interval[1]:.1f}%" if len(interval) == 2 else ""
    )
    doc.paragraph(
        f"在{scope_label}的 {scope['answers']} 条可复算回答中，{target_brand}被提及 "
        f"{target['mentions']} 次，提及率 {_pct(target['mention_rate'])}{interval_text}。"
        f"提及时平均位次为 {_rank(target['avg_rank'])}；Top1/Top3/Top5分别为 "
        f"{target['top_counts']['1']}/{target['answers']}、{target['top_counts']['3']}/{target['answers']}、"
        f"{target['top_counts']['5']}/{target['answers']}。"
    )
    platform_rows = list(delivery["by_platform"].items())
    group_rows = list(delivery["by_group"].items())
    strongest_platform_slug, strongest_platform = max(
        platform_rows, key=lambda item: item[1]["mention_rate"]
    )
    focus_platform_slug, focus_platform = min(
        platform_rows, key=lambda item: item[1]["mention_rate"]
    )
    strongest_group_name, strongest_group = max(
        group_rows, key=lambda item: item[1]["mention_rate"]
    )
    focus_group_name, focus_group = min(group_rows, key=lambda item: item[1]["mention_rate"])
    doc.heading("1.1 本批三项核心判断", level=2)
    doc.table(
        ["判断", "数据依据", "业务含义"],
        [
            (
                "已形成可见度基础",
                f"提及 {target['mentions']}/{target['answers']}，"
                f"提及时平均位次 {_rank(target['avg_rank'])}",
                "下一步重点是扩大重点场景覆盖，并保持已进入推荐列表的靠前位置",
            ),
            (
                "优势组合清晰",
                f"{strongest_group_name} {_fraction(strongest_group)}；"
                f"{_PLATFORM_LABELS.get(strongest_platform_slug, strongest_platform_slug)} "
                f"{_fraction(strongest_platform)}",
                "优先巩固优势场景中的品牌、公司与产品归属表达",
            ),
            (
                "提升重点明确",
                f"{focus_group_name} {_fraction(focus_group)}；"
                f"{_PLATFORM_LABELS.get(focus_platform_slug, focus_platform_slug)} "
                f"{_fraction(focus_platform)}",
                "资源优先投入低提及场景与平台，再沿用原题复测变化",
            ),
        ],
        widths=(37, 68, 67),
        font_size=7.4,
    )
    doc.paragraph(
        f"阅读口径：本报告聚焦{scope_label}、披露的12个问题文本、三个平台、两个地域标签和本次采集窗口。"
        "更广范围的整体品牌表现需由扩展业务问题组另行评估。"
    )

    _page(doc, "2. 评测范围与下一步")
    doc.table(
        ["范围维度", "本批口径", "阅读方式"],
        [
            ("业务范围", scope_label, "结论围绕三类资产治理场景组织"),
            (
                "问题",
                f"{scope['selected_groups']}组、{scope['questions']}个实际文本",
                "每组原题+A/B/C三个变体",
            ),
            ("平台", "豆包、DeepSeek、文心一言", "同一平台内按同题比较"),
            ("地域", "北京、上海", "按采集标签分组；地域证明由样本台账承载"),
            (
                "重复",
                f"当前最小 {scope['current_repetitions']} 次/单元",
                "同口径复测以不同run及账号/浏览器台账确认独立性",
            ),
            ("样本规模", f"{scope['answers']}条可复算回答", "逐条明细和证据定位见样本索引"),
        ],
        widths=(30, 62, 80),
        font_size=8,
    )
    doc.heading("2.1 客户优先行动", level=2)
    weakest = sorted(
        delivery["question_rows"], key=lambda row: (row["mention_rate"], row["question"])
    )[:3]
    doc.table(
        ["优先级", "行动", "本批依据", "复测判定"],
        [
            (
                "高",
                "补强零提及或低提及问题对应的品牌说明与可公开验证材料",
                "；".join(
                    f"{row['group_index']}-{row['question_index']}：{_pct(row['mention_rate'])}"
                    for row in weakest
                ),
                "沿用原问题文本观察提及率与Top3变化",
            ),
            (
                "中",
                "针对平台差异优化结构化品牌介绍",
                "分平台结果见第5章",
                "保持问题、地域与窗口口径一致",
            ),
            (
                "中",
                "持续维护可被引用的公开页面",
                "引用共现用于识别公开页面线索",
                "内容准确性后续按服务2流程核验",
            ),
        ],
        widths=(18, 58, 54, 42),
        font_size=7.7,
    )

    _page(doc, "3. 问题组与实际提问文本")
    for group in delivery["selected_groups"]:
        doc.heading(f"3.{group['index']} {group['title']}", level=2)
        doc.table(
            ["变体", "实际提问文本"],
            [
                ("原题" if index == 1 else chr(63 + index), question)
                for index, question in enumerate(group["questions"], 1)
            ],
            widths=(18, 154),
            font_size=7.8,
        )
    doc.paragraph("问题组进入统计后保持不变；采集失败按原问题补采，并在样本索引中保留完整记录。")

    _page(doc, "4. 方法、指标与证据口径")
    doc.table(
        ["指标", "定义", "分子/分母原则"],
        [
            ("提及率", f"回答内出现规范实体“{target_brand}”", "提及回答数/全部合格回答"),
            ("平均位次", "回答内规范实体首次出现的1-based顺序", "只在已提及回答内求均值"),
            ("Top1/3/5", "规范实体位次不超过N", "TopN回答数/全部合格回答"),
            ("竞品对比", "规范实体去重并过滤非竞品工具/机构", "同题、同平台回答为比较单元"),
            ("重复一致性", "同一问题×平台×地域两次观测是否一致", "完整重复对为分母"),
            ("回答所列引用", "平台回答展示的URL", "记录展示内容；采纳与事实核验另行判断"),
        ],
        widths=(30, 84, 58),
        font_size=8,
    )
    doc.bullets(
        [
            "同一回答中同一品牌的不同别名只计一次，首次出现位置决定该回答内位次。",
            f"实体审计共 {scope['entity_rows_audited']} 个规范名，其中 "
            f"{scope['competitor_entities']} 个可进入竞品比较，"
            f"{scope['unclassified_entities']} 个未分类实体保留在审计表但不进入竞品榜。",
            "报告展示一位小数并保留分子/分母；区间和重复一致性用于评估本批波动，后续复测保持同口径。",
        ]
    )

    _page(doc, "5. 目标品牌总体结果")
    doc.chart(
        ["提及率", "Top1", "Top3", "Top5"],
        [
            target["mention_rate"],
            target["top_rates"]["1"],
            target["top_rates"]["3"],
            target["top_rates"]["5"],
        ],
        title=f"{target_brand}在全部主样本中的可见性",
    )
    doc.table(
        ["指标", "分子/分母", "结果", "解读"],
        [
            (
                "提及率",
                target["mention_rate_fraction"],
                _pct(target["mention_rate"]),
                "是否进入回答品牌序列",
            ),
            (
                "Top1",
                f"{target['top_counts']['1']}/{target['answers']}",
                _pct(target["top_rates"]["1"]),
                "回答将品牌置于首位",
            ),
            (
                "Top3",
                f"{target['top_counts']['3']}/{target['answers']}",
                _pct(target["top_rates"]["3"]),
                "进入优先比较范围",
            ),
            (
                "Top5",
                f"{target['top_counts']['5']}/{target['answers']}",
                _pct(target["top_rates"]["5"]),
                "进入较长候选清单",
            ),
            (
                "平均位次",
                f"{target['mentions']}条已提及回答",
                _rank(target["avg_rank"]),
                "数值越小越靠前",
            ),
        ],
        widths=(31, 38, 27, 76),
        font_size=8.2,
    )

    doc.heading("5.1 分平台表现")
    doc.table(
        ["平台", "样本", "提及", "提及率", "Top1", "Top3", "Top5", "平均位次"],
        [
            (
                _PLATFORM_LABELS.get(platform, platform),
                row["answers"],
                row["mentions"],
                _pct(row["mention_rate"]),
                f"{row['top_counts']['1']}/{row['answers']}",
                f"{row['top_counts']['3']}/{row['answers']}",
                f"{row['top_counts']['5']}/{row['answers']}",
                _rank(row["avg_rank"]),
            )
            for platform, row in delivery["by_platform"].items()
        ],
        widths=(26, 18, 18, 23, 20, 20, 20, 27),
        font_size=7.6,
    )
    doc.paragraph("平台差异用于定位本批提升重点；后续沿用同题、同地域和同重复口径确认变化稳定性。")

    doc.heading("5.2 分地域与分场景表现")
    doc.heading("地域标签对照", level=2)
    doc.table(
        ["地域", "样本", "提及", "提及率", "Top3", "平均位次"],
        [
            (
                region,
                row["answers"],
                row["mentions"],
                _pct(row["mention_rate"]),
                f"{row['top_counts']['3']}/{row['answers']}",
                _rank(row["avg_rank"]),
            )
            for region, row in delivery["by_region"].items()
        ],
        widths=(28, 24, 24, 30, 30, 36),
        font_size=8,
    )
    doc.heading("三类资产治理场景对照", level=2)
    doc.table(
        ["场景", "样本", "提及", "提及率", "Top3", "平均位次"],
        [
            (
                name,
                row["answers"],
                row["mentions"],
                _pct(row["mention_rate"]),
                f"{row['top_counts']['3']}/{row['answers']}",
                _rank(row["avg_rank"]),
            )
            for name, row in delivery["by_group"].items()
        ],
        widths=(62, 20, 20, 25, 23, 22),
        font_size=7.8,
    )

    for group_position, group in enumerate(delivery["selected_groups"]):
        group_title = f"6.{group['index']} {group['title']} · 逐题结果"
        if group_position % 2 == 0:
            _page(doc, group_title)
        else:
            doc.heading(group_title)
        rows = [row for row in delivery["question_rows"] if row["group_title"] == group["title"]]
        doc.table(
            ["变体", "实际问题", "提及", "提及率", "Top1/3/5", "平均位次", "引用回答"],
            [
                (
                    "原题" if row["question_index"] == 1 else chr(63 + row["question_index"]),
                    row["question"],
                    row["mention_rate_fraction"],
                    _pct(row["mention_rate"]),
                    f"{row['top_counts']['1']}/{row['top_counts']['3']}/{row['top_counts']['5']}",
                    _rank(row["avg_rank"]),
                    f"{row['answers_with_citation']}/{row['answers']}",
                )
                for row in rows
            ],
            widths=(14, 72, 21, 23, 26, 23, 23),
            font_size=7.2,
        )
        weakest_row = min(rows, key=lambda row: (row["mention_rate"], row["question"]))
        doc.callout(
            "本场景读数",
            f"最低提及问题为“{weakest_row['question']}”，提及 "
            f"{weakest_row['mention_rate_fraction']}（"
            f"{_pct(weakest_row['mention_rate'])}）。该结果需结合第7章重复一致性理解。",
        )

    _page(doc, "7. 重复观测一致性与不确定性")
    consistency = delivery["repeat_consistency"]
    doc.kpis(
        [
            (
                "完整重复对",
                f"{consistency['complete_pairs']}/{consistency['expected_pairs']}",
                "同题×平台×地域",
            ),
            (
                "提及一致率",
                _pct(consistency["mention_agreement_rate"]),
                f"{consistency['mention_agreement_pairs']}/{consistency['complete_pairs']}",
            ),
            ("两次均提及", str(consistency["both_mentioned_pairs"]), "可比较位次差"),
            ("平均绝对位次差", _rank(consistency["mean_absolute_rank_delta"]), "仅两次均提及时"),
        ]
    )
    detail_rows = [
        row
        for row in consistency["details"]
        if not row["mention_agreement"] or (row["absolute_rank_delta"] or 0) >= 3
    ][:12]
    doc.table(
        ["平台", "地域", "问题（截短）", "提及一致", "重复1位次", "重复2位次", "绝对差"],
        [
            (
                _PLATFORM_LABELS.get(row["platform"], row["platform"]),
                row["region"],
                row["question"][:28],
                "是" if row["mention_agreement"] else "否",
                row["repeat_1_rank"] or "—",
                row["repeat_2_rank"] or "—",
                row["absolute_rank_delta"] if row["absolute_rank_delta"] is not None else "—",
            )
            for row in detail_rows
        ],
        widths=(24, 18, 67, 20, 16, 16, 11),
        font_size=6.8,
    )
    doc.paragraph(
        "一致率描述两次观测是否同时提及或同时未提及；它不等于统计显著性，也不支持长期趋势判断。"
    )

    _page(doc, "8. 同口径竞品对比")
    comparison = delivery["competitor_comparison"]
    competitor_rows = [comparison["target"], *comparison["competitors"]]
    doc.table(
        ["规范实体", "身份", "提及", "提及率", "Top3", "平均位次"],
        [
            (
                row["canonical_name"],
                "目标品牌" if row["canonical_name"] == target_brand else "主要竞品",
                row["mention_rate_fraction"],
                _pct(row["mention_rate"]),
                f"{row['top_counts']['3']}/{row['answers']}",
                _rank(row["avg_rank"]),
            )
            for row in competitor_rows
        ],
        widths=(43, 25, 26, 27, 25, 26),
        font_size=8,
    )
    doc.paragraph(
        "本表是本批同口径实体对比，不是行业排名。Nmap、Amass及机构类名称不会进入竞品表；"
        "完整实体归一、别名和排除原因见样本索引XLSX的“实体排名”工作表。"
    )

    _page(doc, "8.1 同题、同平台差异")
    comparable = list(comparison["same_question_platform"])
    comparable.sort(
        key=lambda row: (
            abs(float(row["mention_rate_gap_pp"])),
            abs(float(row["top3_rate_gap_pp"])),
        ),
        reverse=True,
    )
    doc.table(
        ["平台", "同一问题（截短）", "竞品", "样本", "提及率差", "Top3差", "平均位次差"],
        [
            (
                _PLATFORM_LABELS.get(row["platform"], row["platform"]),
                row["question"][:30],
                row["competitor"],
                row["answers"],
                f"{row['mention_rate_gap_pp']:+.1f}个百分点",
                f"{row['top3_rate_gap_pp']:+.1f}个百分点",
                "—" if row["avg_rank_gap"] is None else f"{row['avg_rank_gap']:+.1f}",
            )
            for row in comparable[:12]
        ],
        widths=(22, 57, 29, 16, 22, 22, 20),
        font_size=6.5,
    )
    doc.paragraph(
        "差值=目标品牌−竞品；正的提及率/Top3差表示目标品牌更高，负的平均位次差表示目标品牌更靠前。"
        "全量组合见样本索引XLSX的“同题同平台”工作表。"
    )

    _page(doc, "8.2 回答所列引用与品牌共现")
    sources = delivery["sources"]
    doc.kpis(
        [
            ("引用记录", str(sources.get("total") or 0), "按回答列出的URL累计"),
            ("唯一URL", str(sources.get("unique_urls") or 0), "按规范URL去重"),
            ("Top3来源集中度", _pct(sources.get("top3_concentration")), "只描述列表分布"),
            (
                "引用核验状态",
                f"{scope['answers_with_citation']}/{scope['answers']}",
                "服务1记录URL；内容核验按服务2执行",
            ),
        ]
    )
    doc.table(
        ["网站域名", "目标品牌提及回答", "目标品牌未提及回答", "解读方式"],
        [
            (
                row["host"],
                row["target_mentioned_answers"],
                row["target_not_mentioned_answers"],
                "回答级共现，用于识别伴随出现的来源结构",
            )
            for row in delivery["source_cooccurrence"][:12]
        ],
        widths=(54, 32, 36, 50),
        font_size=7.5,
    )

    _page(doc, "9. 三个平台代表回答")
    doc.paragraph(
        "以下三条代表回答按固定平台轮换覆盖豆包、DeepSeek和文心一言。"
        "指标基于全量样本计算；完整长图、完整回答与全部引用均在证据包。"
    )
    doc.table(
        ["场景", "平台/地域", "目标品牌", "引用", "样本ID"],
        [
            (
                item["group_title"],
                f"{item['platform_label']}/{item['region']}",
                f"第{item['target_rank']}位" if item.get("target_rank") is not None else "未提及",
                item["citation_count"],
                next(
                    (
                        row["sample_id"]
                        for row in delivery["sample_registry"]
                        if row.get("answer_pub_id") == item.get("answer_pub_id")
                    ),
                    "—",
                ),
            )
            for item in delivery["representative_answers"]
        ],
        widths=(62, 32, 26, 18, 34),
        font_size=7.8,
    )
    if not delivery.get("representative_platforms_complete"):
        doc.callout(
            "代表证据缺口", "三平台代表回答未齐全，出版门应阻断交付候选状态。", kind="warning"
        )

    sample_by_answer = {str(row.get("answer_pub_id")): row for row in delivery["sample_registry"]}
    for representative_index, item in enumerate(delivery["representative_answers"]):
        answer_id = str(item.get("answer_pub_id") or "")
        _representative_pages(
            doc,
            item,
            sample_by_answer.get(answer_id, {}),
            screenshots.get(answer_id),
            target_brand=target_brand,
            start_new_page=representative_index > 0,
        )

    _page(doc, "10. 行动优先级与复测方法")
    doc.table(
        ["优先级", "建议动作", "服务1复测观察", "后续衔接"],
        [
            (
                "高",
                "为最低提及问题补充清晰品牌定位、产品能力和适用场景页面",
                "同一原题与三个变体的提及率、Top3及位次",
                "以复测判断AI推荐变化；内容准确性按服务2核验",
            ),
            (
                "高",
                "统一品牌简称、公司全称和产品归属表达",
                "别名合并后的规范实体提及与位次",
                "采用规范实体口径确认真实提及变化",
            ),
            (
                "中",
                "对平台差异较大的同题单元保持原题复测",
                "重复一致性与同题同平台差值",
                "用连续同口径批次确认差异稳定性",
            ),
            (
                "中",
                "维护可访问、标题明确的公开说明页面",
                "回答是否列出相应URL",
                "按服务2流程核验引用内容",
            ),
        ],
        widths=(18, 63, 50, 41),
        font_size=7.4,
    )

    _page(doc, "11. 结果使用说明、交付物与复算")
    doc.bullets(
        [
            f"适用范围：结论服务于{scope_label}和本次窗口；整体品牌判断可在扩展业务问题组后形成。",
            "地域口径：地域结论以采样时刻的脱敏账号、浏览器实例、出口地域/IP哈希与探测记录为依据。",
            "证据口径：服务1评价AI推荐可见性并记录回答所列引用；内容准确性由事实核验流程确认。",
            "统计口径：所有比例保留分子/分母，并结合Wilson区间和重复一致性理解本批差异。",
            "实体口径：竞品表采用已分类规范实体；待分类实体保留在XLSX实体审计表供后续治理。",
        ]
    )
    doc.table(
        ["交付物", "用途", "定位方式"],
        [
            ("主报告DOCX/PDF", "客户结论、范围、同口径对比和代表回答", "章节目录"),
            (
                "样本与审计索引XLSX",
                "逐条复算、证据定位、实体审计及全量同题同平台差值",
                "样本索引/实体排名/同题同平台工作表",
            ),
            ("证据包ZIP", "完整回答、截图、分享图、引用与快照索引", "sample_id目录"),
            ("manifest JSON", "状态、门禁、页数、大小和SHA-256", "唯一文件名"),
        ],
        widths=(39, 73, 60),
        font_size=7.8,
    )

    doc.heading("12. 文档治理与修订记录")
    governance = facts.get("document_governance") or {}
    doc.table(
        ["治理字段", "记录"],
        [
            (
                "项目与服务",
                f"{facts.get('project_name') or '—'} · 服务1 · 品牌GEO推荐结果评测",
            ),
            (
                "版本与状态",
                f"{governance.get('version') or 'V1.0'} · "
                f"{release_state_label(str(facts.get('document_status') or ''))}",
            ),
            (
                "编制",
                f"{governance.get('prepared_by') or 'GEO 项目组'} · "
                f"{governance.get('prepared_date') or str(facts['generated_at'])[:10]}",
            ),
            (
                "复核",
                f"{governance.get('reviewed_by') or '待复核'} · "
                f"{governance.get('reviewed_date') or '待定'}",
            ),
            (
                "批准",
                f"{governance.get('approved_by') or '待批准'} · "
                f"{governance.get('approved_date') or '待定'}",
            ),
            ("保密级别", "客户机密—仅限指定项目组"),
        ],
        widths=(36, 136),
        font_size=7.8,
    )
    doc.heading("12.1 修订记录", level=2)
    doc.table(
        ["版本", "日期", "修订说明", "状态"],
        [
            (
                governance.get("version") or "V1.0",
                governance.get("prepared_date") or str(facts["generated_at"])[:10],
                "首次生成：范围限定、实体治理、重复一致性、同口径竞品对比和证据索引",
                release_state_label(str(facts.get("document_status") or "")),
            )
        ],
        widths=(24, 31, 89, 28),
        font_size=8,
    )
    doc.paragraph(
        "版权与使用：本报告及其证据包仅供指定客户项目组在本项目范围内使用。未经书面许可，不得向无关第三方传播。"
    )

    payload = bytes(doc.save())
    # Renderer-level defence.  OOXML is compressed, so the publication QA repeats
    # this check on extracted document/PDF text after conversion.
    visible_values = " ".join(paragraph.text for paragraph in doc.document.paragraphs)
    found = [value for value in _CLIENT_FORBIDDEN if value in visible_values]
    if found:
        raise ValueError("customer_report_internal_language:" + ",".join(found))
    return payload


__all__ = ["render_service1_delivery_docx"]
