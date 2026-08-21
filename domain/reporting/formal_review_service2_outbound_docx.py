"""DOCX renderer for quotation service 2 (customer-owned outbound content)."""

from __future__ import annotations

from typing import Any

from domain.reporting.formal_review_docx import (
    FormalDocument,
    add_native_toc,
    build_report_code,
    is_formal_document,
)
from domain.reporting.service1_governance import release_state_label


def _percent(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator:.1%}" if denominator else "—"


def render_outbound_disparagement_docx(facts: dict[str, Any]) -> bytes:
    """Render a separate report; never relabel the legacy inbound-risk DOCX."""

    if facts.get("schema_version") != "formal-outbound-disparagement-v1":
        raise ValueError("outbound_disparagement_facts_invalid")
    scope = facts["scope"]
    cases = list(facts.get("cases") or [])
    governance = facts.get("document_governance") or {}
    version = str(governance.get("version") or "V1.0")
    doc = FormalDocument(
        title="主动拉踩内容核查报告",
        subtitle="服务 2 · 仅核查有己方归属证据的已定稿内容",
        facts=facts,
    )
    doc.cover(report_code=build_report_code(facts, service_number=2, version=version))
    add_native_toc(doc)

    doc.heading("1. 执行摘要")
    doc.kpis(
        [
            ("已定稿内容", str(scope["finalized_content_versions"]), "显式绑定 SOP 项目"),
            (
                "完成判定内容",
                str(scope["judged_content_versions"]),
                _percent(scope["judged_content_versions"], scope["finalized_content_versions"]),
            ),
            ("疑似主动拉踩", str(scope["risk_cases"]), "按命中原句逐条展示"),
            ("判定校验失败", str(scope["validation_failures"]), "失败行不进入结论"),
        ]
    )
    if cases:
        doc.callout(
            "当前结论",
            f"本窗口在有明确己方归属的已定稿内容中发现 {len(cases)} 条疑似拉踩竞品线索。"
            "这些线索应先整改或补充同口径事实依据，再决定是否发布或继续传播。",
            kind="warning",
        )
    else:
        doc.callout(
            "当前结论",
            "本窗口已完成判定的己方定稿内容中未发现疑似主动拉踩线索。"
            "该结论只覆盖正文列示的项目、时间窗和完成判定的版本，不外推到其他内容。",
            kind="success",
        )
    doc.paragraph(
        "归属边界：本报告只把显式绑定客户 SOP 项目且 publication_ready=true 的版本视为"
        "己方内容。AI 回答、第三方网页和没有作者/委托/审批链的 URL 不进入服务 2。"
    )

    doc.heading("2. 核查范围与完整性")
    doc.table(
        ["范围项", "数量/状态", "解释"],
        [
            ("已定稿版本", scope["finalized_content_versions"], "客户 SOP 项目内的冻结正文"),
            (
                "完成判定版本",
                scope["judged_content_versions"],
                "全部确定性预期窗均有校验通过的判定",
            ),
            (
                "预期/完成判定窗",
                f"{scope.get('completed_windows', 0)}/{scope.get('expected_windows', 0)}",
                "按冻结正文、品牌与竞品清单重新确定性切窗",
            ),
            (
                "覆盖完整性",
                "完整" if scope["judgment_coverage_complete"] else "不足",
                "全部已定稿版本均需完成判定，才可签发完整范围结论",
            ),
            ("疑似风险案例", scope["risk_cases"], "仅保留针对已配置竞品的拉踩命中"),
        ],
        widths=(40, 38, 94),
        font_size=8,
    )
    versions = list(facts.get("content_versions") or [])
    doc.heading("2.1 已核查内容版本", level=2)
    doc.table(
        ["标题", "版本", "正文哈希", "完成/预期窗", "公开链接"],
        [
            (
                row["title"],
                row["version_no"],
                str(row["body_sha256"])[:16] + "…",
                f"{row.get('completed_windows', 0)}/{row.get('expected_windows', 0)}",
                "\n".join(
                    str(publication.get("public_url") or "")
                    for publication in row.get("publications", [])
                )
                or "未公开/未绑定",
            )
            for row in versions
        ],
        widths=(47, 16, 40, 20, 49),
        font_size=7.4,
    )

    doc.heading("3. 风险线索")
    if cases:
        doc.table(
            ["案例", "内容", "被比较竞品", "命中原句", "事实核查"],
            [
                (
                    case["case_id"],
                    f"{case['article_title']} v{case['article_version']}",
                    case["target_brand"],
                    case["evidence_quote"],
                    (
                        f"{case['factcheck']['verdict']}：{case['factcheck']['summary']}"
                        if case.get("factcheck")
                        else "尚无公开事实核查，不得宣称事实虚假已坐实"
                    ),
                )
                for case in cases
            ],
            widths=(18, 37, 27, 55, 35),
            font_size=7,
        )
        for case in cases:
            doc.heading(f"{case['case_id']} · {case['article_title']}", level=2)
            doc.callout("逐字命中原句", str(case["evidence_quote"]), kind="warning")
            doc.table(
                ["字段", "记录"],
                [
                    ("己方归属证据", case["ownership_evidence"]),
                    ("被比较对象", case["target_brand"]),
                    ("表达态度", case["attitude"] or "未标注"),
                    ("判定置信度", f"{float(case['confidence']):.2f}"),
                    (
                        "公开链接",
                        "\n".join(
                            str(row.get("public_url") or "") for row in case.get("publications", [])
                        )
                        or "未公开/未绑定",
                    ),
                ],
                widths=(42, 130),
                font_size=8,
            )
    else:
        doc.callout(
            "没有进入逐案展示的线索",
            "只表示已完成判定的当前范围内未命中；不代表未定稿、未提交或范围外内容没有风险。",
            kind="success",
        )

    doc.heading("4. 结论边界与整改建议")
    doc.bullets(
        [
            "发现拉踩表达时，优先删除无来源的高低排序、贬低标签或绝对化比较。",
            "必须比较竞品时，应补充同时间、同指标、同样本的公开证据，并明确适用范围。",
            "没有事实核查结果的案例只能作为内容合规整改线索，不得对外宣称竞品事实虚假。",
            *list(facts.get("limitations") or []),
        ]
    )

    doc.heading("附录 A · 版本与审批")
    doc.table(
        ["治理字段", "记录"],
        [
            ("项目与服务", f"{facts.get('project_name') or '—'} · 服务 2 · 主动拉踩内容核查"),
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
        ],
        widths=(38, 134),
        font_size=8,
    )
    if not is_formal_document(facts):
        doc.callout(
            "本稿状态",
            "本版为内部审核稿；完成范围覆盖、事实核查与人工审批后方可成为客户交付候选稿。",
            kind="warning",
        )
    return bytes(doc.save())


__all__ = ["render_outbound_disparagement_docx"]
