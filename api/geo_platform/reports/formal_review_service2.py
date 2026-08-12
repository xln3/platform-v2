"""Service-2 V2 facts: distinct-content coverage and customer-readable cases.

The first pre-formal report accidentally presented W3 judgment *rows* as AI-answer
and source-document counts.  A W3 row is one brand/window/model/prompt execution;
the same answer can legitimately create many rows and can be re-judged.  This module
keeps those executions for audit, but reports distinct content and the full citation
to fetch to evidence funnel in the customer-facing snapshot.

Service 2 only evaluates collected AI answers and their public sources.  It does not
expect, mention or synthesize customer-authored "GEO articles".
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from hashlib import sha256
from pathlib import Path
from statistics import median
from typing import Any
from urllib.parse import unquote, urlsplit

from geo_platform.analytics.service import _platform_tenant_connection
from geo_platform.brandrank import service as brandrank_service

MODEL_LABELS = {
    "doubao": "豆包",
    "deepseek": "DeepSeek",
    "yiyan": "文心一言",
    "tongyi": "通义千问",
    "yuanbao": "腾讯元宝",
}

PLATFORM_ENTRY_URLS = {
    "doubao": "https://www.doubao.com/chat/",
    "deepseek": "https://chat.deepseek.com/",
    "yiyan": "https://yiyan.baidu.com/",
    "tongyi": "https://tongyi.aliyun.com/",
    "yuanbao": "https://yuanbao.tencent.com/",
}

_SOURCE_HIGHLIGHT_TERMS: dict[str, list[str]] = {
    "https://www.venustech.com.cn/new_type/cpdt/20240710/27715.html": ["11.6%", "第一"],
    "https://www.huafoun.com/": ["网络资产", "网络空间"],
    "https://www.nsfocus.com.cn/html/1/": ["绿盟科技"],
    "https://www.venustech.com.cn/": ["启明星辰"],
    "https://www.163.com/dy/article/KVIQNL7M0530TV08.html": ["双非"],
    "https://www.qianxin.com/service/detail/pid/46": ["互联网资产", "天眼"],
    # 页面把具体能力放在轮播卡中；“情报融合”是可稳定逐字定位的可见卡片标题。
    # “多源融合”“攻击路径”并非每次首屏均可见，不伪造同屏截图。
    "https://www.webray.com.cn/RayTBD.html": ["情报融合"],
}


def _json(value: object, default: Any) -> Any:
    if isinstance(value, type(default)):
        return value
    if not isinstance(value, str) or not value:
        return default
    try:
        loaded = json.loads(value)
    except (TypeError, ValueError):
        return default
    return loaded if isinstance(loaded, type(default)) else default


def _compact(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _integer(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float | str):
        return int(value)
    return 0


def _context_excerpt(response: str, quote: str, *, radius: int = 360) -> str:
    response_compact = _compact(response)
    quote_compact = _compact(quote).strip("| ")
    needles = [quote_compact, *[part.strip("* |") for part in quote_compact.split("|")]]
    position = next(
        (
            response_compact.find(needle)
            for needle in needles
            if needle and needle in response_compact
        ),
        -1,
    )
    if position < 0:
        return response_compact[: radius * 2] + ("…" if len(response_compact) > radius * 2 else "")
    start = max(0, position - radius)
    end = min(len(response_compact), position + len(quote_compact) + radius)
    return (
        ("…" if start else "")
        + response_compact[start:end]
        + ("…" if end < len(response_compact) else "")
    )


def _quote_text_range(response: str, quote: str) -> tuple[int, int] | None:
    """Locate a judgment quote in the persisted answer without inventing offsets."""

    candidates = [
        quote,
        *sorted(
            (part.strip("* |") for part in quote.split("|")),
            key=len,
            reverse=True,
        ),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        direct = response.find(candidate)
        if direct >= 0:
            return direct, direct + len(candidate)
        compact_answer: list[str] = []
        offsets: list[int] = []
        for index, character in enumerate(response):
            if character.isspace() or character in {"\u200b", "\ufeff"}:
                continue
            compact_answer.append(character.casefold())
            offsets.append(index)
        compact_candidate = "".join(
            character.casefold()
            for character in candidate
            if not character.isspace() and character not in {"\u200b", "\ufeff"}
        )
        if not compact_candidate:
            continue
        compact_start = "".join(compact_answer).find(compact_candidate)
        if compact_start >= 0:
            compact_end = compact_start + len(compact_candidate)
            return offsets[compact_start], offsets[compact_end - 1] + 1
    return None


def _answer_anchor(
    answer_pub_id: str,
    quote: str,
    overrides: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    """Resolve an explicit historical-review anchor; never infer pixel coordinates.

    Long-term evidence must persist a DOM/OCR anchor at collection time.  The optional
    sidecar is only for repairing already-captured historical evidence after a human
    has visually checked the rectangle.
    """

    for item in overrides.get(answer_pub_id, []):
        needle = str(item.get("quote_contains") or "")
        bbox = item.get("bbox")
        if needle and needle in quote and isinstance(bbox, list) and len(bbox) == 4:
            return {
                "bbox": [int(value) for value in bbox],
                "method": "manual_reviewed_bbox",
                "label": str(item.get("label") or "AI 回答命中表述"),
                "reviewed_by": str(item.get("reviewed_by") or "人工复核"),
                "reviewed_at": item.get("reviewed_at"),
            }
    return None


def _statement_type(quote: str) -> str:
    if "不如" in quote:
        return "无依据的负向比较"
    if "梯队" in quote or ">" in quote:
        return "无依据的排序/梯队比较"
    if "安装终端代理" in quote:
        return "部署条件与能力范围陈述"
    if quote.rstrip().endswith("弱") or " 是 弱" in quote:
        return "语义不完整的负向标签"
    return "负向或比较性风险表述"


def _case_interpretation(quote: str, verdict: str, summary: str) -> dict[str, str]:
    if verdict == "refuted":
        return {
            "expression_verdict": "错误比较风险成立",
            "fact_verdict": "公开证据冲突，且原回答未说明口径",
            "customer_conclusion": (
                "AI 回答给出无来源的梯队和份额数字；现有公开数据与该数字冲突。"
                "应纠正该表述，但不能据此认定任何竞品参与撰写或投放。"
            ),
            "why": (
                f"{summary} 证据口径为网络安全硬件总体，并非 ASM 专项份额；"
                "因此能确定的是原回答未交代口径且“5%以下”缺乏支持，不能把 11.6%"
                "直接替换成 ASM 市场份额。"
            ),
        }
    if " 是 弱" in quote or quote.rstrip().endswith("弱"):
        return {
            "expression_verdict": "线索命中，暂不定性",
            "fact_verdict": "无法核验：原表头/比较维度缺失",
            "customer_conclusion": (
                "截取文本无法说明“弱”评价对应哪个指标，也无法确定比较对象。"
                "本条保留为复采线索，不进入确定风险清单。"
            ),
            "why": summary,
        }
    if "安装终端代理" in quote:
        return {
            "expression_verdict": "能力限制陈述需复核",
            "fact_verdict": "无法核验：公开资料不足以支持完整组合结论",
            "customer_conclusion": (
                "公开资料只能支持部分产品能力，不能证明“整体必须安装代理”及“高校全场景”"
                "这一完整结论。当前不能把它作为已证实的拉踩事实。"
            ),
            "why": summary,
        }
    return {
        "expression_verdict": "拉踩式/贬低性比较表达成立",
        "fact_verdict": "无法核验：缺少同口径公开比较证据",
        "customer_conclusion": (
            "AI 回答确实使用了高低排序或“不如”等贬低性比较，但没有给出指标、样本、"
            "时间和权威发布者。真实性未获证实，也无证据将该内容归因给竞品。"
        ),
        "why": summary,
    }


def _preferred_summary(summaries: list[str]) -> str:
    if not summaries:
        return "未形成可复核的事实核查说明。"
    # Duplicated re-judgments may produce three prose variants.  A customer report
    # needs one complete explanation, while all raw rows remain in the JSON audit map.
    return max(summaries, key=lambda value: (len(value), value))


def _comparison_direction(
    *,
    quote: str,
    target_brand: str,
    platform_label: str,
    customer_brand: str = "",
) -> str:
    """Turn model-centric judgment fields into a direct customer-readable direction."""

    if "不如" in quote:
        comparison = quote.strip(" |。；").removeprefix("但")
        left, right = comparison.split("不如", 1)
        left = left.rstrip("可能 ").replace("的深度", "深度")
        right = right.strip(" |。；")
        if customer_brand and (
            customer_brand.startswith(right) or right.startswith(customer_brand)
        ):
            right = customer_brand
        return f"AI 回答称{target_brand}的{left}可能不如{right}"
    if " 是 弱" in quote or quote.rstrip().endswith("弱"):
        capability = quote.split(" 是 弱", 1)[0].strip()
        capability = capability.removeprefix(target_brand).strip()
        return (
            f"{platform_label} AI 回答将{target_brand}的{capability or '相关能力'}"
            "标为“弱”（比较对象未显示）"
        )
    if target_brand and target_brand in quote:
        return f"{platform_label} AI 回答对{target_brand}作出负向评价"
    return f"{platform_label} AI 回答中的比较对象：{target_brand}"


def _group_cases(
    rows: list[dict[str, Any]],
    *,
    answer_anchor_overrides: dict[str, list[dict[str, Any]]] | None = None,
    customer_brand: str = "",
) -> list[dict[str, Any]]:
    answer_anchor_overrides = answer_anchor_overrides or {}
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        quote = _compact(row.get("evidence_quote"))
        grouped[
            (
                str(row.get("subject_type") or "answer"),
                str(row.get("subject_pub_id") or ""),
                quote,
                str(row.get("target_brand") or ""),
            )
        ].append(row)

    cases: list[dict[str, Any]] = []
    for (subject_type, subject_id, quote, target_brand), members in grouped.items():
        first = members[0]
        verdicts = [str(row.get("factcheck_verdict") or "not_checked") for row in members]
        verdict = (
            "refuted"
            if "refuted" in verdicts
            else (
                "supported"
                if "supported" in verdicts
                else ("unverifiable" if "unverifiable" in verdicts else "not_checked")
            )
        )
        summaries = sorted(
            {
                _compact(row.get("factcheck_summary"))
                for row in members
                if row.get("factcheck_summary")
            }
        )
        summary = _preferred_summary(summaries)
        source_urls = sorted(
            {
                _compact(row.get("factcheck_source_url"))
                for row in members
                if row.get("factcheck_source_url")
            }
        )
        platform = str(first.get("answer_model") or first.get("platform") or "")
        platform_label = MODEL_LABELS.get(platform, platform or "未知平台")
        interpretation = _case_interpretation(quote, verdict, summary)
        is_source = subject_type == "source_document"
        subject_brand = str(first.get("subject_brand") or "")
        source_url = str(first.get("source_url") or "")
        response_text = str(first.get("response_text") or "")
        cases.append(
            {
                "subject_type": subject_type,
                "subject_pub_id": subject_id,
                "answer_pub_id": "" if is_source else subject_id,
                "platform": platform,
                "platform_label": (platform or "未知网站" if is_source else platform_label),
                "region": str(first.get("answer_region") or ""),
                "mode": str(first.get("answer_mode") or ""),
                "capture_time": first.get("answer_capture_time"),
                "question": str(first.get("query_text") or ""),
                "answer_context": ("" if is_source else _context_excerpt(response_text, quote)),
                "_answer_quote_range": (
                    None if is_source else _quote_text_range(response_text, quote)
                ),
                "_answer_text": "" if is_source else response_text,
                "answer_screenshot_ref": (
                    "" if is_source else str(first.get("screenshot_ref") or "")
                ),
                "answer_anchor": (
                    None
                    if is_source
                    else _answer_anchor(subject_id, quote, answer_anchor_overrides)
                ),
                "platform_entry_url": "" if is_source else PLATFORM_ENTRY_URLS.get(platform, ""),
                "platform_url_note": (
                    "原始信源网页" if is_source else "平台入口（采集端未获得可复现的公开会话 URL）"
                ),
                "source_url": source_url,
                "subject_brand": subject_brand,
                "target_brand": target_brand,
                "direction": (
                    f"信源网页正文：{subject_brand or '页面表述'} → {target_brand}"
                    if is_source
                    else _comparison_direction(
                        quote=quote,
                        target_brand=target_brand,
                        platform_label=platform_label,
                        customer_brand=customer_brand,
                    )
                ),
                "attribution": (
                    "该表述逐字来自已抓取的公开信源正文；只归因该网页，不外推其作者、"
                    "账号运营方或投放主体。"
                    if is_source
                    else "表述由 AI 平台回答生成；没有证据证明由目标品牌、竞品或第三方撰写/投放。"
                ),
                "evidence_quote": quote,
                "statement_type": _statement_type(quote),
                "judgment_executions": len(members),
                "factcheck_verdict": verdict,
                "factcheck_summary": summary,
                "factcheck_summary_variants": summaries,
                "factcheck_sources": [
                    {
                        "url": url,
                        "highlight_terms": _SOURCE_HIGHLIGHT_TERMS.get(url, []),
                        "role": (
                            "公开反证"
                            if verdict == "refuted"
                            else "能力范围核查；不足以证明高低比较"
                        ),
                    }
                    for url in source_urls
                ],
                **interpretation,
                # Internal IDs are intentionally excluded from customer-render fields.
                "audit_refs": [str(row["judgment_pub_id"]) for row in members],
            }
        )
    cases.sort(
        key=lambda row: (
            0 if row["subject_type"] == "answer" else 1,
            0 if row["factcheck_verdict"] == "refuted" else 1,
            str(row["platform_label"]),
            str(row["target_brand"]),
            str(row["evidence_quote"]),
        )
    )
    for index, case in enumerate(cases, 1):
        case["case_id"] = f"C-{index:02d}"
    return cases


def _case_is_target_brand_relevant(case: dict[str, Any], target_brand: str) -> bool:
    """Keep only cases that directly concern the customer's target brand."""

    aliases = {target_brand.strip()}
    for suffix in ("安全", "科技", "集团", "股份", "有限公司"):
        if target_brand.endswith(suffix) and len(target_brand) > len(suffix):
            aliases.add(target_brand[: -len(suffix)])
    searchable = " ".join(
        str(case.get(field) or "") for field in ("subject_brand", "target_brand", "evidence_quote")
    )
    return any(alias and alias in searchable for alias in aliases)


def _attach_source_case_assets(
    dsn: str,
    tenant_pub_id: str,
    cases: list[dict[str, Any]],
) -> None:
    source_ids = [str(case["subject_pub_id"]) for case in cases]
    if not source_ids:
        return
    with _platform_tenant_connection(dsn, tenant_pub_id) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT ON (rel.from_pub_id)
                   rel.from_pub_id, ea.pub_id, ea.object_key, ea.sha256, ea.mime_type,
                   ea.source_url, ea.capture_time, anchor.bbox
            FROM evidence.evidence_relation rel
            JOIN evidence.evidence_asset ea
              ON ea.tenant_pub_id=rel.tenant_pub_id AND ea.pub_id=rel.to_pub_id
            LEFT JOIN LATERAL (
              SELECT a.bbox
              FROM evidence.evidence_anchor a
              WHERE a.tenant_pub_id=ea.tenant_pub_id AND a.evidence_pub_id=ea.pub_id
              ORDER BY a.created_at DESC, a.pub_id DESC
              LIMIT 1
            ) anchor ON TRUE
            WHERE rel.tenant_pub_id=%s AND rel.from_pub_id=ANY(%s::text[])
              AND rel.relation_type='brand_mention_source_snapshot'
              AND ea.kind='source_screenshot' AND ea.mime_type LIKE 'image/%%'
              AND ea.deleted_at IS NULL
            ORDER BY rel.from_pub_id, ea.capture_time DESC, ea.pub_id DESC
            """,
            (tenant_pub_id, source_ids),
        ).fetchall()
    by_source = {str(row["from_pub_id"]): dict(row) for row in rows}
    for case in cases:
        row = by_source.get(str(case["subject_pub_id"]))
        if row is None:
            case["source_screenshot"] = None
            continue
        raw_bbox = row.get("bbox")
        bbox: dict[str, Any] = raw_bbox if isinstance(raw_bbox, dict) else {}
        case["source_screenshot"] = {
            "pub_id": str(row["pub_id"]),
            "object_key": str(row["object_key"]),
            "sha256": str(row["sha256"]),
            "mime_type": str(row["mime_type"]),
            "source_url": str(row.get("source_url") or case.get("source_url") or ""),
            "capture_time": row.get("capture_time"),
            "bbox": [
                int(float(bbox.get("x") or 0)),
                int(float(bbox.get("y") or 0)),
                int(float(bbox.get("width") or 0)),
                int(float(bbox.get("height") or 0)),
            ],
        }


def _attach_answer_case_assets(
    dsn: str,
    tenant_pub_id: str,
    cases: list[dict[str, Any]],
) -> None:
    """Attach the clean CAS image and native DOM rectangle for each answer case."""

    answer_ids = sorted({str(case.get("answer_pub_id") or "") for case in cases} - {""})
    rows: list[dict[str, Any]] = []
    if answer_ids:
        with _platform_tenant_connection(dsn, tenant_pub_id) as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT rel.from_pub_id AS answer_pub_id,
                           ea.pub_id,ea.object_key,ea.sha256,ea.mime_type,ea.capture_time,
                           anchor.text_start,anchor.text_end,anchor.bbox,anchor.quote_hash
                    FROM evidence.evidence_relation rel
                    JOIN evidence.evidence_asset ea
                      ON ea.tenant_pub_id=rel.tenant_pub_id AND ea.pub_id=rel.to_pub_id
                    JOIN evidence.evidence_anchor anchor
                      ON anchor.tenant_pub_id=ea.tenant_pub_id
                     AND anchor.evidence_pub_id=ea.pub_id
                    WHERE rel.tenant_pub_id=%s
                      AND rel.from_pub_id=ANY(%s::text[])
                      AND rel.relation_type='answer_evidence_excerpt'
                      AND ea.kind='answer_excerpt_screenshot'
                      AND ea.mime_type LIKE 'image/%%' AND ea.deleted_at IS NULL
                    ORDER BY rel.from_pub_id,ea.capture_time DESC,ea.pub_id,
                             anchor.text_start,anchor.pub_id
                    """,
                    (tenant_pub_id, answer_ids),
                ).fetchall()
            ]
    by_answer: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_answer[str(row["answer_pub_id"])].append(row)

    for case in cases:
        quote_range = case.pop("_answer_quote_range", None)
        answer_text = str(case.pop("_answer_text", "") or "")
        case["answer_screenshot"] = None
        if (
            not isinstance(quote_range, tuple | list)
            or len(quote_range) != 2
            or not all(isinstance(value, int) for value in quote_range)
        ):
            continue
        quote_start, quote_end = int(quote_range[0]), int(quote_range[1])
        candidates = by_answer.get(str(case.get("answer_pub_id") or ""), [])
        if not candidates:
            continue
        # The ORDER BY makes the first asset the newest.  Never combine rectangles
        # from different screenshots because their coordinate systems may differ.
        asset_pub_id = str(candidates[0]["pub_id"])
        asset_rows = [row for row in candidates if str(row["pub_id"]) == asset_pub_id]
        matched = [
            row
            for row in asset_rows
            if isinstance(row.get("text_start"), int)
            and isinstance(row.get("text_end"), int)
            and int(row["text_start"]) < quote_end
            and int(row["text_end"]) > quote_start
            and isinstance(row.get("bbox"), dict)
            and sha256(
                answer_text[int(row["text_start"]) : int(row["text_end"])].encode()
            ).hexdigest()
            == str(row.get("quote_hash") or "")
        ]
        if not matched:
            continue
        boxes = [row["bbox"] for row in matched]
        try:
            x0 = min(float(box["x"]) for box in boxes)
            y0 = min(float(box["y"]) for box in boxes)
            x1 = max(float(box["x"]) + float(box["width"]) for box in boxes)
            y1 = max(float(box["y"]) + float(box["height"]) for box in boxes)
        except (KeyError, TypeError, ValueError):
            continue
        if x1 <= x0 or y1 <= y0:
            continue
        first = asset_rows[0]
        case["answer_screenshot"] = {
            "pub_id": asset_pub_id,
            "object_key": str(first["object_key"]),
            "sha256": str(first["sha256"]),
            "mime_type": str(first["mime_type"]),
            "capture_time": first.get("capture_time"),
        }
        case["answer_anchor"] = {
            "bbox": [round(x0), round(y0), max(1, round(x1 - x0)), max(1, round(y1 - y0))],
            "method": str(boxes[0].get("anchor_method") or "dom_text_block_v1"),
            "label": "AI 回答命中表述",
        }


def _source_ref_url(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    return str(
        value.get("url") or value.get("canonical_url") or value.get("original_url") or ""
    ).strip()


def _load_raw_collection_stats(
    connection: Any, project_pub_id: str, start_at: datetime, end_at: datetime
) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT ct.pub_id, ct.citations_json
        FROM platform.collection_task ct
        JOIN platform.collection_run cr ON cr.id=ct.run_id
        JOIN platform.project p ON p.id=cr.project_id
        WHERE p.pub_id=%s AND ct.created_at >= %s AND ct.created_at <= %s
        ORDER BY ct.created_at, ct.pub_id
        """,
        (project_pub_id, start_at, end_at),
    ).fetchall()
    counts: list[int] = []
    urls: list[str] = []
    for row in rows:
        citations = _json(row["citations_json"], [])
        current = [_source_ref_url(value) for value in citations]
        current = [value for value in current if value]
        counts.append(len(current))
        urls.extend(current)
    return {
        "tasks": len(rows),
        "tasks_with_citation": sum(value > 0 for value in counts),
        "citation_references": sum(counts),
        "unique_url_strings": len(set(urls)),
        "avg_per_task": round(sum(counts) / len(counts), 2) if counts else 0.0,
        "median_per_task": float(median(counts)) if counts else 0.0,
        "max_per_task": max(counts, default=0),
    }


def _load_post_analysis_state(connection: Any, *, target_brand: str) -> dict[str, Any]:
    task_row = connection.execute(
        """
        SELECT count(*)::bigint AS tasks,
               count(*) FILTER (WHERE target_brand=%s)::bigint AS target_tasks
        FROM platform.post_analysis_task
        """,
        (target_brand,),
    ).fetchone()
    item_row = connection.execute(
        """
        SELECT count(*)::bigint AS items,
               count(*) FILTER (WHERE screenshot_cas_key IS NOT NULL)::bigint AS screenshots,
               count(*) FILTER (WHERE annotated_cas_key IS NOT NULL)::bigint AS annotated
        FROM platform.post_analysis_item
        """
    ).fetchone()
    return {
        "tasks": int(task_row["tasks"]),
        "target_project_tasks": int(task_row["target_tasks"]),
        "items": int(item_row["items"]),
        "screenshots": int(item_row["screenshots"]),
        "annotated": int(item_row["annotated"]),
        "project_linked": False,
        "diagnosis": (
            "post_analysis 是独立 URL 取证流程，表结构没有 project_id；当前存量任务不属于本项目，"
            "W3 判定和正式报告均未消费其截图/标注结果。"
        ),
    }


def enrich_service2_v2_facts(
    *,
    dsn: str,
    tenant_pub_id: str,
    facts: dict[str, Any],
    answer_anchor_overrides: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Attach a corrected Service-2 delivery snapshot at ``service2.delivery_v2``.

    Calling signature intentionally mirrors ``enrich_service1_v2_facts`` so the
    shared generator can enrich each service after ``build_formal_review_facts``.
    """

    project_pub_id = str(facts["project_pub_id"])
    start = date.fromisoformat(str(facts["window"]["start"]))
    end = date.fromisoformat(str(facts["window"]["end"]))
    start_at = datetime.combine(start, time.min, tzinfo=UTC)
    end_at = datetime.combine(end, time.max, tzinfo=UTC)

    with _platform_tenant_connection(dsn, tenant_pub_id) as connection:
        answers = connection.execute(
            """
            SELECT pub_id
            FROM analytics.answer
            WHERE tenant_pub_id=%s AND project_pub_id=%s
              AND eligible AND NOT degraded
              AND capture_time >= %s AND capture_time <= %s
            ORDER BY capture_time, pub_id
            """,
            (tenant_pub_id, project_pub_id, start_at, end_at),
        ).fetchall()
        answer_ids = [str(row["pub_id"]) for row in answers]
        citations = brandrank_service.fetch_citations(dsn, tenant_pub_id, answer_ids)
        per_answer = [len(citations.get(answer_id, [])) for answer_id in answer_ids]
        citation_urls = [
            str(row.get("canonical_url") or row.get("original_url") or "")
            for answer_id in answer_ids
            for row in citations.get(answer_id, [])
            if row.get("canonical_url") or row.get("original_url")
        ]

        raw_stats = _load_raw_collection_stats(connection, project_pub_id, start_at, end_at)
        fetch_rows = connection.execute(
            """
            SELECT sd.extract_status, count(*)::bigint AS n
            FROM platform.source_document sd
            JOIN platform.collection_run cr ON cr.id=sd.run_id
            JOIN platform.project p ON p.id=cr.project_id
            WHERE p.pub_id=%s AND sd.fetched_at >= %s AND sd.fetched_at <= %s
            GROUP BY sd.extract_status
            ORDER BY sd.extract_status
            """,
            (project_pub_id, start_at, end_at),
        ).fetchall()
        fetch_run_row = connection.execute(
            """
            SELECT count(DISTINCT sd.run_id)::bigint AS runs
            FROM platform.source_document sd
            JOIN platform.collection_run cr ON cr.id=sd.run_id
            JOIN platform.project p ON p.id=cr.project_id
            WHERE p.pub_id=%s AND sd.fetched_at >= %s AND sd.fetched_at <= %s
            """,
            (project_pub_id, start_at, end_at),
        ).fetchone()
        fetch_relation_row = connection.execute(
            """
            SELECT count(*)::bigint AS relations,
                   count(DISTINCT rel.from_pub_id)::bigint AS answers,
                   count(DISTINCT rel.to_pub_id)::bigint AS documents
            FROM evidence.evidence_relation rel
            JOIN platform.source_document sd ON sd.pub_id=rel.to_pub_id
            JOIN platform.collection_run cr ON cr.id=sd.run_id
            JOIN platform.project p ON p.id=cr.project_id
            WHERE rel.tenant_pub_id=%s
              AND rel.relation_type='cited_source_document'
              AND p.pub_id=%s AND sd.fetched_at >= %s AND sd.fetched_at <= %s
            """,
            (tenant_pub_id, project_pub_id, start_at, end_at),
        ).fetchone()
        brand_source_evidence_row = connection.execute(
            """
            SELECT count(DISTINCT sd.pub_id)::bigint AS documents,
                   count(DISTINCT ea.pub_id)::bigint AS screenshots
            FROM platform.source_document sd
            JOIN platform.collection_run cr ON cr.id=sd.run_id
            JOIN platform.project p ON p.id=cr.project_id
            JOIN evidence.evidence_relation rel
              ON rel.tenant_pub_id=%s AND rel.from_pub_id=sd.pub_id
             AND rel.relation_type='brand_mention_source_snapshot'
            JOIN evidence.evidence_asset ea
              ON ea.tenant_pub_id=rel.tenant_pub_id AND ea.pub_id=rel.to_pub_id
             AND ea.kind='source_screenshot' AND ea.deleted_at IS NULL
            WHERE p.pub_id=%s AND sd.fetched_at >= %s AND sd.fetched_at <= %s
            """,
            (tenant_pub_id, project_pub_id, start_at, end_at),
        ).fetchone()

        judgment_rows = connection.execute(
            """
            SELECT j.judgment_status, j.subject_type,
                   count(*)::bigint AS executions,
                   count(DISTINCT j.subject_pub_id)::bigint AS distinct_subjects
            FROM platform.disparagement_judgment j
            JOIN platform.project p ON p.id=j.project_id
            WHERE p.pub_id=%s AND j.created_at >= %s AND j.created_at <= %s
            GROUP BY j.judgment_status, j.subject_type
            ORDER BY j.judgment_status, j.subject_type
            """,
            (project_pub_id, start_at, end_at),
        ).fetchall()
        flagged_rows = connection.execute(
            """
            SELECT j.pub_id AS judgment_pub_id, j.subject_type, j.subject_pub_id,
                   j.platform, j.subject_brand, j.target_brand, j.evidence_quote,
                   j.confidence, j.created_at,
                   f.verdict AS factcheck_verdict, f.summary AS factcheck_summary,
                   f.source_url AS factcheck_source_url,
                   COALESCE(j.source_url, sd.url) AS source_url,
                   a.query_text, a.response_text, a.model AS answer_model,
                   a.region AS answer_region, a.mode AS answer_mode,
                   a.capture_time AS answer_capture_time,
                   ct.screenshot_ref
            FROM platform.disparagement_judgment j
            JOIN platform.project p ON p.id=j.project_id
            LEFT JOIN platform.disparagement_factcheck f ON f.judgment_pub_id=j.pub_id
            LEFT JOIN platform.source_document sd
              ON sd.tenant_id=j.tenant_id AND sd.pub_id=j.subject_pub_id
             AND j.subject_type='source_document'
            LEFT JOIN analytics.answer a
              ON a.tenant_pub_id=%s AND a.pub_id=j.subject_pub_id
            LEFT JOIN platform.collection_task ct
              ON ct.tenant_id=j.tenant_id AND ct.pub_id=j.subject_pub_id
            WHERE p.pub_id=%s AND j.created_at >= %s AND j.created_at <= %s
              AND j.judgment_status='ok' AND j.disparagement IS TRUE
            ORDER BY j.created_at, j.pub_id
            """,
            (tenant_pub_id, project_pub_id, start_at, end_at),
        ).fetchall()
        post_analysis = _load_post_analysis_state(
            connection, target_brand=str(facts["target_brand"])
        )

    all_cases = _group_cases(
        [dict(row) for row in flagged_rows],
        answer_anchor_overrides=answer_anchor_overrides,
        customer_brand=str(facts["target_brand"]),
    )
    target_brand = str(facts["target_brand"])
    target_cases = [
        case for case in all_cases if _case_is_target_brand_relevant(case, target_brand)
    ]
    answer_cases = [case for case in target_cases if case["subject_type"] == "answer"]
    source_cases = [case for case in target_cases if case["subject_type"] == "source_document"]
    for case in source_cases:
        case.pop("_answer_quote_range", None)
        case.pop("_answer_text", None)
    for index, case in enumerate(answer_cases, 1):
        case["case_id"] = f"A-{index:02d}"
    for index, case in enumerate(source_cases, 1):
        case["case_id"] = f"S-{index:02d}"
    _attach_source_case_assets(dsn, tenant_pub_id, source_cases)
    excluded_cases = [case for case in all_cases if case not in target_cases]
    # A refuted competitor-only comparison can still be useful as a transparent
    # methodology example.  Keep it outside every target-brand count and label it
    # explicitly as supplementary context; never let it displace the customer cases.
    supplemental_cases = [
        case
        for case in excluded_cases
        if case["subject_type"] == "answer"
        and case["factcheck_verdict"] == "refuted"
        and case.get("factcheck_sources")
    ][:1]
    for index, case in enumerate(supplemental_cases, 1):
        case["case_id"] = f"X-{index:02d}"
        case["customer_scope_note"] = (
            f"本例不直接涉及{target_brand}，仅用于展示公开数字冲突的核查方法；"
            "不计入客户主报告的风险线索、结论或 KPI。"
        )
    _attach_answer_case_assets(dsn, tenant_pub_id, [*answer_cases, *supplemental_cases])
    fetch_status = {str(row["extract_status"]): int(row["n"]) for row in fetch_rows}
    judgment_matrix = [
        {
            "status": str(row["judgment_status"]),
            "subject_type": str(row["subject_type"]),
            "executions": int(row["executions"]),
            "distinct_subjects": int(row["distinct_subjects"]),
        }
        for row in judgment_rows
    ]
    ok_answer = next(
        (
            row
            for row in judgment_matrix
            if row["status"] == "ok" and row["subject_type"] == "answer"
        ),
        {"executions": 0, "distinct_subjects": 0},
    )
    ok_source = next(
        (
            row
            for row in judgment_matrix
            if row["status"] == "ok" and row["subject_type"] == "source_document"
        ),
        {"executions": 0, "distinct_subjects": 0},
    )
    validation_answer = next(
        (
            row
            for row in judgment_matrix
            if row["status"] == "validation_failure" and row["subject_type"] == "answer"
        ),
        {"executions": 0, "distinct_subjects": 0},
    )
    distinct_urls_per_answer = [
        len(
            {
                str(row.get("canonical_url") or row.get("original_url") or "")
                for row in citations.get(answer_id, [])
                if row.get("canonical_url") or row.get("original_url")
            }
        )
        for answer_id in answer_ids
    ]
    unique_canonical_urls = len(set(citation_urls))
    cases_by_verdict = Counter(
        str(case["factcheck_verdict"]) for case in [*answer_cases, *source_cases]
    )
    source_relation_count = _integer(
        fetch_relation_row["relations"] if fetch_relation_row is not None else 0
    )
    answer_level_fetch_planner = source_relation_count > 0
    delivery = {
        "schema_version": "service2-delivery-v2",
        "scope_definition": (
            "窗内 eligible 且非 degraded 的独立 AI 回答，以及其采集到的公开引用 URL。"
        ),
        "citation_funnel": {
            "eligible_answers": len(answer_ids),
            "answers_with_citation": sum(value > 0 for value in per_answer),
            "citation_references": sum(per_answer),
            "unique_canonical_urls": unique_canonical_urls,
            "sum_distinct_urls_per_answer": sum(distinct_urls_per_answer),
            "avg_refs_all_answers": round(sum(per_answer) / len(per_answer), 2)
            if per_answer
            else 0.0,
            "avg_refs_cited_answers": round(
                sum(per_answer) / sum(value > 0 for value in per_answer), 2
            )
            if any(per_answer)
            else 0.0,
            "median_refs_all_answers": float(median(per_answer)) if per_answer else 0.0,
            "max_refs_one_answer": max(per_answer, default=0),
        },
        "raw_collection": raw_stats,
        "source_fetch": {
            "documents": sum(fetch_status.values()),
            "statuses": fetch_status,
            "ok": int(fetch_status.get("ok", 0)),
            "runs_with_documents": _integer(
                fetch_run_row["runs"] if fetch_run_row is not None else 0
            ),
            "planner_mode": (
                "answer_level_v2" if answer_level_fetch_planner else "legacy_global_top_n"
            ),
            "implemented_strategy": (
                "每份回答独立规划；优先有 cited_text 的来源；跨回答 URL 去重并扇出关系"
                if answer_level_fetch_planner
                else "历史实现按整个 collection run 截断，导致大量回答的来源正文未抓取"
            ),
            "answer_document_relations": source_relation_count,
            "answers_with_planned_documents": _integer(
                fetch_relation_row["answers"] if fetch_relation_row is not None else 0
            ),
            "documents_with_answer_relation": _integer(
                fetch_relation_row["documents"] if fetch_relation_row is not None else 0
            ),
            "diagnosis": (
                (
                    "URL 在分析层完整保留；正文抓取已按回答规划，并持久化回答—文档关系。"
                    "报告继续披露实际成功量，不把计划量冒充完成量。"
                )
                if answer_level_fetch_planner
                else (
                    "URL 在分析层完整保留；历史正文抓取层却按 run 全局截断，而不是"
                    "逐回答覆盖。"
                    f"因此实际抓取量远低于 {unique_canonical_urls:,} 个唯一 URL。"
                )
            ),
        },
        "judgment_funnel": {
            "rows": judgment_matrix,
            "ok_answer_executions": _integer(ok_answer["executions"]),
            "ok_distinct_answers": _integer(ok_answer["distinct_subjects"]),
            "validation_failure_answer_executions": _integer(validation_answer["executions"]),
            "validation_failure_distinct_answers": _integer(validation_answer["distinct_subjects"]),
            "ok_source_executions": _integer(ok_source["executions"]),
            "ok_distinct_source_documents": _integer(ok_source["distinct_subjects"]),
            "flagged_executions": sum(
                int(case["judgment_executions"]) for case in [*answer_cases, *source_cases]
            ),
            "flagged_distinct_answers": len({case["answer_pub_id"] for case in answer_cases}),
            "unique_cases": len(answer_cases) + len(source_cases),
            "answer_cases": len(answer_cases),
            "source_cases": len(source_cases),
            "operator_all_brand_cases": len(all_cases),
            "excluded_competitor_only_cases": len(excluded_cases),
            "explanation": (
                "执行行按答案切窗、目标品牌、模型和 prompt 版本落库；重跑会新增判定版本。"
                f"{_integer(ok_answer['executions'])}/{_integer(ok_source['executions'])} "
                "只能称为执行行，不能称为 AI 回答数或信源文档数。"
            ),
        },
        "case_verdict_counts": dict(cases_by_verdict),
        "cases": answer_cases,
        "source_cases": source_cases,
        "supplemental_factcheck_cases": supplemental_cases,
        "excluded_competitor_only_case_count": len(excluded_cases),
        "source_content_audit": {
            "successful_documents": int(fetch_status.get("ok", 0)),
            "documents_with_target_brand_visual_anchor": _integer(
                brand_source_evidence_row["documents"]
                if brand_source_evidence_row is not None
                else 0
            ),
            "target_brand_source_screenshots": _integer(
                brand_source_evidence_row["screenshots"]
                if brand_source_evidence_row is not None
                else 0
            ),
            "judged_distinct_documents": _integer(ok_source["distinct_subjects"]),
            "flagged_target_brand_cases": len(source_cases),
            "method": (
                f"只对正文逐字提及{target_brand}的公开信源切取品牌所在段落；"
                "再判定该段中目标品牌是否贬低他人或被他人贬低。"
            ),
        },
        "answer_visual_coverage": {
            "cases_with_screenshot_ref": sum(
                bool(case["answer_screenshot_ref"]) for case in answer_cases
            ),
            "cases_with_reviewed_bbox": sum(bool(case["answer_anchor"]) for case in answer_cases),
            "cases_with_native_dom_anchor": sum(
                str((case.get("answer_anchor") or {}).get("method") or "").startswith("dom_")
                for case in answer_cases
            ),
            "cases_with_native_ocr_anchor": sum(
                str((case.get("answer_anchor") or {}).get("method") or "").startswith("ocr_")
                for case in answer_cases
            ),
            "unique_answers_with_screenshot_ref": len(
                {case["answer_pub_id"] for case in answer_cases if case["answer_screenshot_ref"]}
            ),
        },
        "post_analysis_wiring": post_analysis,
        "customer_render_policy": {
            "show_internal_judgment_ids": False,
            "show_ai_platform_as_speaker": True,
            "claim_competitor_authorship": False,
            "unverifiable_is_true": False,
            "source_screenshot_required": True,
        },
        "limitations": [
            (
                "正文抓取已按回答规划并保存回答—文档关系；正式风险核查仍应披露"
                "发现、抓取成功、品牌提及、段落判定和截图标注各级覆盖。"
                if answer_level_fetch_planner
                else "本窗历史来源正文抓取被旧的全局截断逻辑严重漏抓；该实现属于"
                "系统缺陷，不能把旧数据声称为已完成信源正文核查。"
            ),
            "AI 平台未提供可分享的会话级公开 URL；原回答以采集截图、原文和时间戳存证。",
            "“无法核验”表示缺少同口径比较证据，不表示原表述为真。",
            "现有证据只能确认 AI 回答中的表述，不能归因竞品或第三方作者。",
            "新版采集会把回答正文的干净证据图和 DOM/OCR 文本坐标一起保存；"
            "本窗口未具备原生坐标的历史截图仅使用已经人工复核的坐标，不自动猜框。",
        ],
    }
    facts["service2"]["delivery_v2"] = delivery
    return facts


def load_service2_local_answer_screenshots(
    facts: dict[str, Any], *, allowed_root: Path | None = None
) -> dict[str, bytes]:
    """Load current file-backed answer screenshots without accepting arbitrary paths."""

    root = (allowed_root or Path.cwd()).resolve()
    delivery = facts["service2"]["delivery_v2"]
    payloads: dict[str, bytes] = {}
    cases = [
        *delivery["cases"],
        *(delivery.get("supplemental_factcheck_cases") or []),
    ]
    for case in cases:
        ref = str(case.get("answer_screenshot_ref") or "")
        if not ref.startswith("file://"):
            continue
        path = Path(unquote(urlsplit(ref).path)).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if path.is_file():
            payloads[str(case["answer_pub_id"])] = path.read_bytes()
    return payloads


def load_service2_answer_screenshots(
    facts: dict[str, Any],
    *,
    blob_loader: Callable[[str, str], bytes],
    allowed_root: Path | None = None,
) -> dict[str, bytes]:
    """Load native clean CAS images first, then historical local screenshots."""

    payloads = load_service2_local_answer_screenshots(facts, allowed_root=allowed_root)
    delivery = facts["service2"]["delivery_v2"]
    for case in [
        *delivery["cases"],
        *(delivery.get("supplemental_factcheck_cases") or []),
    ]:
        descriptor = case.get("answer_screenshot")
        if not isinstance(descriptor, dict):
            continue
        object_key = str(descriptor.get("object_key") or "")
        digest = str(descriptor.get("sha256") or "")
        answer_pub_id = str(case.get("answer_pub_id") or "")
        if object_key and digest and answer_pub_id:
            payloads[answer_pub_id] = blob_loader(object_key, digest)
    return payloads


def load_service2_source_case_screenshots(
    facts: dict[str, Any],
    *,
    blob_loader: Callable[[str, str], bytes],
) -> dict[str, bytes]:
    """Load integrity-checked source paragraph screenshots selected by customer cases."""

    payloads: dict[str, bytes] = {}
    for case in facts["service2"]["delivery_v2"].get("source_cases") or []:
        descriptor = case.get("source_screenshot")
        if not isinstance(descriptor, dict):
            continue
        pub_id = str(descriptor.get("pub_id") or "")
        object_key = str(descriptor.get("object_key") or "")
        digest = str(descriptor.get("sha256") or "")
        if pub_id and object_key and digest:
            payloads[pub_id] = blob_loader(object_key, digest)
    return payloads


__all__ = [
    "enrich_service2_v2_facts",
    "load_service2_answer_screenshots",
    "load_service2_local_answer_screenshots",
    "load_service2_source_case_screenshots",
]
