"""Structural regression tests for the V15 client-language service-1 delivery DOCX."""

from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

from domain.reporting.formal_service1_delivery_docx import render_service1_delivery_docx


def _metric(name: str, answers: int, mentions: int, ranks: list[int]) -> dict:
    from statistics import mean

    top_counts = {str(value): sum(rank <= value for rank in ranks) for value in (1, 3, 5)}
    return {
        "canonical_name": name,
        "answers": answers,
        "mentions": mentions,
        "mention_rate": round(mentions / answers * 100, 1) if answers else 0.0,
        "mention_rate_fraction": f"{mentions}/{answers}",
        "mention_rate_wilson_95": [30.0, 50.0],
        "avg_rank": round(mean(ranks), 1) if ranks else None,
        "best_rank": min(ranks) if ranks else None,
        "top_counts": top_counts,
        "top_rates": {
            key: round(count / answers * 100, 1) if answers else 0.0
            for key, count in top_counts.items()
        },
    }


def _question_row(
    group_title: str,
    group_index: int,
    question_index: int,
    question: str,
    intent: str,
) -> dict:
    return {
        "group_title": group_title,
        "group_index": group_index,
        "question_index": question_index,
        "question": question,
        "query_intent": intent,
        **_metric("示例品牌", 12, 4, [1, 2, 2, 3]),
        "answers_with_citation": 10,
    }


def _facts() -> dict:
    groups = [
        {
            "index": 1,
            "title": "高校双非资产排查可以找什么公司做",
            "questions": [
                "高校双非资产排查可以找什么公司做",
                "高校非传统IT资产与影子资产排查服务商推荐",
                "高校信息化部门如何选择未备案资产排查供应商",
                "我们学校好多没报备的IP和系统，找谁能帮忙查一遍？",
            ],
        },
        {
            "index": 2,
            "title": "企业网络资产暴露面管理系统推荐",
            "questions": [
                "企业网络资产暴露面管理系统推荐",
                "互联网暴露面资产收敛与攻击面管理平台选型",
                "甲方安全团队评估攻击面管理（ASM）产品应关注哪些指标",
                "公司外网暴露的资产太多了，有没有好用的管理工具推荐？",
            ],
        },
        {
            "index": 3,
            "title": "资产漏洞一体化融合治理平台厂商",
            "questions": [
                "资产漏洞一体化融合治理平台厂商",
                "网络资产管理与漏洞治理一体化平台主流供应商",
                "采购资产与漏洞联动治理平台时如何评估厂商能力",
                "资产台账和漏洞管理想一个平台搞定，国内谁家做得好？",
            ],
        },
    ]
    intents = [
        ["recommend", "recommend", "selection", "recommend"],
        ["recommend", "selection", "knowledge", "recommend"],
        ["recommend", "recommend", "selection", "recommend"],
    ]
    question_rows = [
        _question_row(group["title"], group["index"], offset, question, intent)
        for group, group_intents in zip(groups, intents, strict=True)
        for offset, (question, intent) in enumerate(
            zip(group["questions"], group_intents, strict=True), 1
        )
    ]
    representatives = [
        {
            "display_number": index,
            "group_title": group["title"],
            "question": group["questions"][0],
            "platform": platform,
            "platform_label": label,
            "region": "北京",
            "capture_time": "2026-08-12T12:00:00+08:00",
            "answer_pub_id": f"ans_{index}",
            "target_rank": 2,
            "citation_count": 2,
            "preferred_image_kind": "share_image",
            "answer_anchor": None,
            "answer_excerpt": "……回答中提到示例品牌，位于第二位……",
            "citations": [
                {
                    "ordinal": 1,
                    "host": "example.com",
                    "title": "示例页面",
                    "url": "https://example.com/page",
                },
                {
                    "ordinal": 2,
                    "host": "example.org",
                    "title": "另一页面",
                    "url": "https://example.org/page",
                },
            ],
        }
        for index, (group, platform, label) in enumerate(
            zip(
                groups,
                ("doubao", "deepseek", "yiyan"),
                ("豆包", "DeepSeek", "文心一言"),
                strict=True,
            ),
            1,
        )
    ]
    registry = [
        {
            "answer_pub_id": f"ans_{index}",
            "sample_id": f"S1-{index:04d}",
            "response_text": "完整回答原文，提到示例品牌。" * 20,
        }
        for index in (1, 2, 3)
    ]
    delivery = {
        "scope": {
            "selected_groups": 3,
            "questions": 12,
            "platforms": 3,
            "regions": 2,
            "current_repetitions": 2,
            "answers": 143,
            "extract_ok": 143,
            "answer_screenshots": 143,
            "share_images": 100,
            "answers_with_citation": 122,
            "citation_references": 500,
            "brands_observed": 20,
            "scope_label": "网空线三类资产治理场景",
            "entity_rows_audited": 186,
            "competitor_entities": 37,
            "unclassified_entities": 12,
            "expected_answers": 144,
        },
        "selected_groups": groups,
        "target": _metric("示例品牌", 143, 59, [1, 2, 2, 3, 4]),
        "by_platform": {
            "doubao": _metric("示例品牌", 47, 12, [2, 3]),
            "deepseek": _metric("示例品牌", 48, 19, [1, 2]),
            "yiyan": _metric("示例品牌", 48, 28, [1, 2, 3]),
        },
        "by_region": {
            "北京": _metric("示例品牌", 72, 28, [1, 2]),
            "上海": _metric("示例品牌", 71, 31, [2, 3]),
        },
        "by_group": {
            groups[0]["title"]: _metric("示例品牌", 47, 20, [1, 2]),
            groups[1]["title"]: _metric("示例品牌", 48, 7, [3]),
            groups[2]["title"]: _metric("示例品牌", 48, 32, [1, 2, 3]),
        },
        "question_rows": question_rows,
        "intent_breakdown": {
            "recommend": {
                "questions": 9,
                "answers": 108,
                "mentions": 50,
                "mention_rate": 46.3,
                "mention_rate_fraction": "50/108",
            },
            "selection": {
                "questions": 2,
                "answers": 24,
                "mentions": 9,
                "mention_rate": 37.5,
                "mention_rate_fraction": "9/24",
            },
            "knowledge": {
                "questions": 1,
                "answers": 11,
                "mentions": 0,
                "mention_rate": 0.0,
                "mention_rate_fraction": "0/11",
            },
        },
        "incomplete_cells": [
            {
                "question": groups[0]["questions"][0],
                "platform": "doubao",
                "platform_label": "豆包",
                "region": "上海",
                "observed": 1,
                "required": 2,
            }
        ],
        "target_aliases": {
            "registered": ["示例品牌", "示例", "EXAMPLE"],
            "observed": ["示例品牌"],
        },
        "entity_type_counts": {
            "company": 30,
            "product": 5,
            "tool": 4,
            "institution": 3,
            "unknown": 12,
        },
        "rank_distribution": [
            {"label": "第 1 位", "count": 20},
            {"label": "第 2–3 位", "count": 20},
            {"label": "第 4–5 位", "count": 8},
            {"label": "第 6 位以后", "count": 11},
            {"label": "未提及", "count": 84},
        ],
        "entity_ranking": [
            {
                "canonical_name": "示例品牌",
                "raw_aliases": ["示例品牌"],
                "entity_type": "company",
            },
            {
                "canonical_name": "对比甲",
                "raw_aliases": ["对比甲", "对比甲科技"],
                "entity_type": "company",
            },
        ],
        "competitor_comparison": {
            "target": _metric("示例品牌", 143, 59, [1, 2, 2, 3, 4]),
            "competitors": [_metric("对比甲", 143, 80, [1, 1, 2])],
            "same_question_platform": [
                {
                    "question": groups[0]["questions"][0],
                    "platform": "doubao",
                    "answers": 4,
                    "competitor": "对比甲",
                    "target_mentions": 1,
                    "competitor_mentions": 4,
                    "mention_rate_gap_pp": -75.0,
                    "top3_rate_gap_pp": -50.0,
                    "avg_rank_gap": 1.0,
                }
            ],
        },
        "repeat_consistency": {
            "complete_pairs": 71,
            "expected_pairs": 72,
            "mention_agreement_pairs": 62,
            "mention_agreement_rate": 87.3,
            "both_mentioned_pairs": 25,
            "mean_absolute_rank_delta": 0.4,
            "details": [],
        },
        "representative_answers": representatives,
        "representative_platforms_complete": True,
        "sample_registry": registry,
        "quotation_gate": {"status": "blocked", "reasons": ["scope_not_preregistered"]},
    }
    return {
        "target_brand": "示例品牌",
        "project_name": "示例项目",
        "window": {"start": "2026-08-12", "end": "2026-08-13"},
        "generated_at": "2026-08-14T02:00:00+00:00",
        "document_status": "internal_review",
        "document_governance": {"version": "V1.0", "prepared_by": "GEO 项目组"},
        "service1": {
            "primary_models": ["doubao", "deepseek", "yiyan"],
            "primary_regions": ["北京", "上海"],
            "quotation_required_repetitions_per_cell": 2,
            "delivery_v3": delivery,
        },
    }


def _all_word_xml(payload: bytes) -> str:
    with ZipFile(BytesIO(payload)) as archive:
        return "\n".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("word/") and name.endswith(".xml")
        )


def test_delivery_docx_uses_client_language_and_self_contained_appendices() -> None:
    xml = _all_word_xml(render_service1_delivery_docx(_facts()))

    for expected in (
        "前三出现率",
        "平均首次出现顺序",
        "提问意图",
        "知识型",
        "计划 144 条，当前 143 条",
        "内部审核稿",
        "AI 生成原文，未经事实核验",
        "附录 A · 计算方式、品牌别名与方法附注",
        "附录 B · 十二个问题完整结果",
        "附录 C · 品牌对比口径与同题差值明细",
        "附录 D · 代表回答关键片段与所列链接",
        "附录 E · 版本、审批与本批限制",
        "同一品牌的不同写法已合并",
        "不是行业排名",
        "不能外推到整体市场",
        "本节结论",
        "建议如何使用",
    ):
        assert expected in xml, expected


def test_delivery_docx_rejects_engineering_vocabulary() -> None:
    xml = _all_word_xml(render_service1_delivery_docx(_facts()))

    for forbidden in (
        "分子/分母",
        "规范实体",
        "1-based",
        "引用核验",
        "共现",
        "manifest",
        "工作表",
        "客户优先行动",
        "重复一致性",
        "样本索引",
        "平均推荐位次",
        "Top1/3/5",
        "本批指标已可复算",
        "未绘制未经复核的定位框",
        "无可复核像素坐标",
    ):
        assert forbidden not in xml, forbidden
