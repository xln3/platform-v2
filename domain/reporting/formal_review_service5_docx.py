"""DOCX renderer for quotation service 5: publishing plus same-matrix retest."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from domain.reporting.formal_review_docx import FormalDocument, add_native_toc, build_report_code
from domain.reporting.service1_governance import release_state_label


def _value(value: object, unit: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, Decimal | float):
        number = float(value)
        return f"{number:.2%}" if unit == "rate" else f"{number:.2f}"
    return str(value)


def render_publishing_pilot_docx(facts: dict[str, Any]) -> bytes:
    """Render publication evidence and the retest as one explicitly bounded report."""

    publishing = facts.get("publication_evidence")
    if not isinstance(publishing, dict) or publishing.get("schema_version") != (
        "formal-publishing-evidence-v1"
    ):
        raise ValueError("service5_publication_evidence_missing")
    governance = facts.get("document_governance") or {}
    version = str(governance.get("version") or "V1.0")
    summary = publishing["summary"]
    comparison = facts.get("comparability") or {}
    metrics = list(facts.get("metrics") or [])
    uvw_strategy = facts.get("uvw_content_strategy") or {}
    doc = FormalDocument(
        title="内容发布与排名提升试点报告",
        subtitle="服务 5 · 发布台账与服务 1 同口径前后复测",
        facts=facts,
    )
    doc.cover(report_code=build_report_code(facts, service_number=5, version=version))
    add_native_toc(doc)

    doc.heading("1. 执行摘要")
    doc.kpis(
        [
            ("公开内容", str(summary["publications"]), "当前报告窗口内"),
            (
                "夹在两次测量之间",
                str(summary["between_measurement_arms"]),
                "前测结束后、后测开始前",
            ),
            (
                "发布证据",
                "完整" if summary["evidence_complete"] else "不足",
                "URL + 定稿 + 发布证据/归因",
            ),
            (
                "前后矩阵",
                "可比" if comparison.get("status") == "comparable" else "不可比/待补",
                f"{sum(bool(row.get('passed')) for row in comparison.get('checks', []))}/"
                f"{len(comparison.get('checks', []))} 项检查通过",
            ),
            (
                "UVW 内容依据",
                {
                    "ready": "完整",
                    "partial": "部分可观察",
                    "insufficient": "不足",
                }.get(str(uvw_strategy.get("status") or ""), "未知"),
                "V 对 U−V；高 W 对低 W",
            ),
        ]
    )
    ready = summary["evidence_complete"] and comparison.get("status") == "comparable" and metrics
    doc.callout(
        "试点结论",
        (
            "发布执行台账和同矩阵前后测量均已绑定，可报告本窗口内的描述性变化。"
            "这些变化仍不是随机对照因果结论，不承诺发帖一定提升排名。"
            if ready
            else "发布证据、执行时序或前后矩阵至少一项不足；本稿只能披露已完成事实与缺口，"
            "不能形成排名提升结论。"
        ),
        kind="success" if ready else "warning",
    )
    doc.paragraph(str(publishing["causal_boundary"]), bold_lead="因果边界：")

    doc.heading("2. 发布执行台账")
    publications = list(publishing.get("publications") or [])
    if publications:
        doc.table(
            ["标题", "平台", "发布时间", "公开 URL", "证据状态", "测量时序"],
            [
                (
                    row["title"],
                    row["platform"],
                    str(row["published_at"] or "—"),
                    row["public_url"],
                    (
                        "完整"
                        if row["has_approved_distribution"]
                        and (row["has_publication_evidence"] or row["has_publication_attribution"])
                        else "缺审批或发布证据"
                    ),
                    "两次测量之间" if row["between_measurement_arms"] else "不在干预窗口",
                )
                for row in publications
            ],
            widths=(34, 22, 31, 45, 22, 28),
            font_size=6.8,
        )
    else:
        doc.callout(
            "没有可交付的发布记录",
            "显式绑定的 SOP 项目在当前窗口内没有同时满足 public 状态和公开 URL 的内容。",
            kind="warning",
        )
    doc.numbered(
        [
            "只有前测结束后、后测开始前公开的内容，才进入本次干预时序判断。",
            "正文哈希、定稿状态、公开 URL 和发布/归因证据共同限定发布事实。",
            "媒体费用、流量或外部事件可能同时影响结果；本报告不把时间先后等同于因果。",
        ]
    )

    doc.heading("3. UVW 内容策略依据")
    cohort_counts = uvw_strategy.get("cohort_counts") or {}
    if uvw_strategy.get("schema_version") != "formal-uvw-content-strategy-v1":
        doc.callout(
            "UVW 分析事实缺失",
            "报告输入未绑定版本化 UVW 分析，不展示推测值，也不生成内容建议。",
            kind="warning",
        )
    else:
        doc.table(
            ["口径", "样本量", "说明"],
            [
                (
                    "全部可观察 U",
                    _value(cohort_counts.get("u_occurrences")),
                    "所有实际检索候选 occurrence",
                ),
                ("进入 V", _value(cohort_counts.get("v_entered")), "模型实际打开或读取"),
                ("U−V", _value(cohort_counts.get("u_not_v")), "可观察 U 中未进入 V"),
                ("高 W", _value(cohort_counts.get("high_w")), "逐字证据贡献分达到版本阈值"),
                ("低 W", _value(cohort_counts.get("low_w")), "低于阈值或未找到逐字贡献"),
                (
                    "不可观察",
                    _value(cohort_counts.get("u_observation_unavailable")),
                    "未知不是 0；不并入 U 分母",
                ),
            ],
            widths=(38, 30, 104),
            font_size=7.5,
        )
        comparisons = uvw_strategy.get("feature_comparison") or {}
        comparison_rows = []
        for key, label in (
            ("selection", "V 对 U−V"),
            ("content_contribution", "高 W 对低 W"),
        ):
            item = comparisons.get(key) or {}
            comparison_rows.append(
                (
                    label,
                    _value(item.get("left_n")),
                    _value(item.get("right_n")),
                    "可比较" if item.get("left_n") and item.get("right_n") else "样本不足",
                )
            )
        doc.table(
            ["对照", "左组 n", "右组 n", "状态"],
            comparison_rows,
            widths=(58, 30, 30, 54),
            font_size=7.5,
        )
        recommendations = list(uvw_strategy.get("recommendations") or [])
        if recommendations:
            doc.table(
                ["依据", "观察", "下一轮实验", "边界"],
                [
                    (
                        "V / U−V" if row.get("basis") == "v_vs_u_not_v" else "高 W / 低 W",
                        row.get("observation") or "—",
                        row.get("experiment") or "—",
                        row.get("causal_boundary") or "—",
                    )
                    for row in recommendations
                ],
                widths=(27, 55, 55, 35),
                font_size=6.8,
            )
        else:
            doc.callout(
                "暂不形成内容建议",
                "至少一组对照缺少可观察样本。缺失值保持未知，不以 0 补齐。",
                kind="warning",
            )
        doc.paragraph(
            str(uvw_strategy.get("causal_boundary") or "未知状态不得推断。"),
            bold_lead="解释边界：",
        )

    doc.heading("4. 前后测量矩阵")
    windows = facts.get("windows") or {}
    doc.table(
        ["测量臂", "窗口", "合格回答", "品牌抽取", "带引用", "可视证据"],
        [
            (
                label,
                f"{windows.get(label, {}).get('start', '—')} 至 "
                f"{windows.get(label, {}).get('end', '—')}",
                (facts.get("arms") or {}).get(label, {}).get("answers_selected_groups", 0),
                (facts.get("arms") or {}).get(label, {}).get("extract_ok_selected", 0),
                (facts.get("arms") or {}).get(label, {}).get("answers_with_citation_selected", 0),
                (facts.get("arms") or {}).get(label, {}).get("answers_with_visual_selected", 0),
            )
            for label in ("before", "after")
        ],
        widths=(24, 45, 26, 26, 25, 26),
        font_size=7.5,
    )
    checks = list(comparison.get("checks") or [])
    doc.heading("4.1 可比性检查", level=2)
    doc.table(
        ["检查项", "状态", "说明"],
        [
            (
                row.get("label") or row.get("name") or row.get("check") or "检查项",
                "通过" if row.get("passed") else "未通过",
                row.get("detail") or row.get("reason") or "—",
            )
            for row in checks
        ],
        widths=(45, 23, 104),
        font_size=7.5,
    )

    doc.heading("5. 同口径复测结果")
    if metrics and comparison.get("status") == "comparable":
        doc.table(
            ["指标", "前测", "后测", "绝对变化", "样本量/稳定性"],
            [
                (
                    row.get("label") or row.get("metric") or row.get("name") or "指标",
                    _value(row.get("before"), str(row.get("unit") or "")),
                    _value(row.get("after"), str(row.get("unit") or "")),
                    _value(row.get("absolute_change"), str(row.get("unit") or "")),
                    f"before n={row.get('before_n', '—')} / after n={row.get('after_n', '—')} / "
                    f"{row.get('stability', '—')}",
                )
                for row in metrics
            ],
            widths=(37, 27, 27, 28, 53),
            font_size=7.2,
        )
    else:
        doc.callout(
            "不输出变化数字",
            "前后矩阵不可比或指标证据不足。空缺不按 0 处理，也不形成提升/下降结论。",
            kind="warning",
        )
    doc.bullets(
        [
            "绝对变化按后测减前测计算；比例与位次必须结合单位解释。",
            "同口径复测由服务 1 提供，服务 5 负责绑定发布记录、解释边界和试点结论。",
            "结果为负或无变化时仍如实交付，不将服务名称解释为效果承诺。",
        ]
    )

    doc.heading("附录 A · 版本、审批与边界")
    doc.table(
        ["治理字段", "记录"],
        [
            ("项目与服务", f"{facts.get('project_name') or '—'} · 服务 5 · 发帖提排名"),
            (
                "版本与状态",
                f"{version} · {release_state_label(str(facts.get('document_status') or ''))}",
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
            ("结果承诺", "不承诺一定提升；只交付发布证据与同口径复测结果"),
        ],
        widths=(38, 134),
        font_size=8,
    )
    return bytes(doc.save())


__all__ = ["render_publishing_pilot_docx"]
