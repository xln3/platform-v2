"""Auditable facts for Service 4: GEO pilot design and before/after review.

The comparison is intentionally stricter than a generic date-window delta.  The
builder first proves that both arms used the same observable test matrix and only
then allows an optimization-effect interpretation.  Missing lineage, incomplete
brand extraction, or an unmatched matrix is disclosed and fails closed; observed
values may still be shown as descriptive measurements, but are never described as
caused by the pilot.

Candidate question groups are ranked solely by evidence completeness.  Brand
mentions, ranks, competitors, and the direction of the before/after delta are not
inputs to selection.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time
from statistics import mean, stdev
from typing import Any

from psycopg.rows import dict_row

from domain.brandrank import adapter, metrics
from domain.brandrank.rules import load_domain, normalize_brand_list
from geo_platform.brandrank import service as brandrank_service
from geo_platform.reports.formal_review import candidate_groups_from_snapshot
from geo_platform.tenancy.psycopg import tenant_connection

SERVICE4_SCHEMA_VERSION = "service4-formal-review-v2"
SERVICE4_METRIC_VERSION = "service4-brandrank-comparison-v2"
DEFAULT_REQUIRED_REPETITIONS = 2
REQUIRED_MAIN_GROUPS = 3
MAX_ARM_ANSWERS = 20_000
TOP_NS = (1, 3, 5)

MODEL_LABELS = {
    "doubao": "豆包",
    "deepseek": "DeepSeek",
    "yiyan": "文心一言",
    "tongyi": "通义千问",
    "yuanbao": "腾讯元宝",
}


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except ValueError:
            return {}
        return dict(loaded) if isinstance(loaded, dict) else {}
    return {}


def _normal_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _group_signature(questions: list[str]) -> tuple[str, ...]:
    """Order-insensitive question-set identity used only to align both arms."""

    return tuple(sorted(_normal_text(question) for question in questions if _normal_text(question)))


def _snapshot_groups(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for metadata in snapshots:
        snapshot = _json_object(metadata.get("snapshot"))
        current, inferred = candidate_groups_from_snapshot(snapshot)
        for group in current:
            questions = [str(value) for value in group.get("questions") or []]
            signature = _group_signature(questions)
            if not signature or signature in seen:
                continue
            seen.add(signature)
            groups.append(
                {
                    "title": str(group.get("title") or f"候选问题组 {len(groups) + 1}"),
                    "questions": questions,
                    "grouping_inferred": bool(inferred),
                    "signature": signature,
                }
            )
    return groups


def merge_candidate_groups(
    before_snapshots: list[dict[str, Any]], after_snapshots: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Align candidate groups without using any measurement outcome."""

    before = _snapshot_groups(before_snapshots)
    after = _snapshot_groups(after_snapshots)
    before_by_signature = {row["signature"]: row for row in before}
    after_by_signature = {row["signature"]: row for row in after}
    ordered_signatures = [row["signature"] for row in before]
    ordered_signatures.extend(
        row["signature"] for row in after if row["signature"] not in before_by_signature
    )
    groups: list[dict[str, Any]] = []
    for index, signature in enumerate(ordered_signatures, 1):
        before_row = before_by_signature.get(signature)
        after_row = after_by_signature.get(signature)
        preferred = before_row or after_row or {}
        groups.append(
            {
                "id": f"candidate_{index:02d}",
                "index": index,
                "title": str(preferred.get("title") or f"候选问题组 {index}"),
                "questions": list(preferred.get("questions") or signature),
                "present_before": before_row is not None,
                "present_after": after_row is not None,
                "grouping_inferred": bool(
                    (before_row or {}).get("grouping_inferred")
                    or (after_row or {}).get("grouping_inferred")
                ),
            }
        )
    return groups


def _configured_dimensions(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    signatures: list[dict[str, tuple[str, ...]]] = []
    explicit_repetitions: set[int] = set()
    for metadata in snapshots:
        snapshot = _json_object(metadata.get("snapshot"))
        signatures.append(
            {
                "models": tuple(sorted(str(value) for value in snapshot.get("models") or [])),
                "regions": tuple(sorted(str(value) for value in snapshot.get("regions") or [])),
                "modes": tuple(sorted(str(value) for value in snapshot.get("modes") or [])),
            }
        )
        for key in ("repetitions_per_cell", "repeat_count", "repetitions"):
            raw = snapshot.get(key)
            if isinstance(raw, int) and raw > 0:
                explicit_repetitions.add(raw)
    unique = {(row["models"], row["regions"], row["modes"]) for row in signatures}
    first = signatures[0] if signatures else {"models": (), "regions": (), "modes": ()}
    repetitions = (
        next(iter(explicit_repetitions))
        if len(explicit_repetitions) == 1
        else DEFAULT_REQUIRED_REPETITIONS
    )
    return {
        "models": list(first["models"]),
        "regions": list(first["regions"]),
        "modes": list(first["modes"]),
        "consistent": len(unique) <= 1 and bool(signatures),
        "required_repetitions": repetitions,
        "repetition_source": (
            "frozen_config" if len(explicit_repetitions) == 1 else "quotation_default"
        ),
        "repetition_config_consistent": len(explicit_repetitions) <= 1,
    }


def _answer_cell(answer: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        _normal_text(answer.get("query_text")),
        str(answer.get("model") or ""),
        str(answer.get("region") or ""),
        str(answer.get("mode") or ""),
    )


def _group_answer_rows(
    group: dict[str, Any], answers: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    questions = {_normal_text(value) for value in group.get("questions") or []}
    return [row for row in answers if _normal_text(row.get("query_text")) in questions]


def _evidence_summary(
    group: dict[str, Any],
    answers: list[dict[str, Any]],
    *,
    extracts: dict[str, dict[str, Any]],
    citations: dict[str, list[dict[str, Any]]],
    visuals: dict[str, dict[str, bool]],
    configured: dict[str, Any],
) -> dict[str, Any]:
    members = _group_answer_rows(group, answers)
    expected_cells = (
        len(group.get("questions") or [])
        * len(configured.get("models") or [])
        * len(configured.get("regions") or [])
        * len(configured.get("modes") or [])
    )
    required_repetitions = int(configured.get("required_repetitions") or 0)
    expected_observations = expected_cells * required_repetitions
    counts = Counter(_answer_cell(row) for row in members)
    covered_observations = sum(min(count, required_repetitions) for count in counts.values())
    observed = len(members)
    extract_ok = sum(
        (extracts.get(str(row.get("pub_id"))) or {}).get("status") == "ok"
        and isinstance((extracts.get(str(row.get("pub_id"))) or {}).get("brands"), list)
        for row in members
    )
    cited = sum(bool(citations.get(str(row.get("pub_id")))) for row in members)
    visual = sum(
        bool((visuals.get(str(row.get("pub_id"))) or {}).get("share_image"))
        or bool((visuals.get(str(row.get("pub_id"))) or {}).get("answer_screenshot"))
        for row in members
    )
    complete = sum(len(str(row.get("response_text") or "").strip()) >= 200 for row in members)
    denominator = max(expected_observations, 1)
    return {
        "expected_cells": expected_cells,
        "expected_observations": expected_observations,
        "observed_answers": observed,
        "covered_observations": covered_observations,
        "extract_ok": extract_ok,
        "answers_with_citation": cited,
        "answers_with_visual": visual,
        "complete_answers": complete,
        "cell_coverage_rate": round(covered_observations / denominator, 4),
        "extract_rate": round(extract_ok / denominator, 4),
        "citation_rate": round(cited / denominator, 4),
        "visual_rate": round(visual / denominator, 4),
        "response_complete_rate": round(complete / denominator, 4),
        "cell_repetitions": sorted(counts.values()),
    }


def score_service4_candidate_groups(
    groups: list[dict[str, Any]],
    before_answers: list[dict[str, Any]],
    after_answers: list[dict[str, Any]],
    *,
    extracts: dict[str, dict[str, Any]],
    citations: dict[str, list[dict[str, Any]]],
    visuals: dict[str, dict[str, bool]],
    before_configured: dict[str, Any],
    after_configured: dict[str, Any],
) -> list[dict[str, Any]]:
    """Select three groups using evidence completeness and no performance values."""

    scored: list[dict[str, Any]] = []
    for group in groups:
        before = _evidence_summary(
            group,
            before_answers,
            extracts=extracts,
            citations=citations,
            visuals=visuals,
            configured=before_configured,
        )
        after = _evidence_summary(
            group,
            after_answers,
            extracts=extracts,
            citations=citations,
            visuals=visuals,
            configured=after_configured,
        )
        combined_denominator = max(
            int(before["expected_observations"]) + int(after["expected_observations"]), 1
        )
        combined = {
            "cell_coverage_rate": round(
                (int(before["covered_observations"]) + int(after["covered_observations"]))
                / combined_denominator,
                4,
            ),
            "extract_rate": round(
                (int(before["extract_ok"]) + int(after["extract_ok"])) / combined_denominator,
                4,
            ),
            "citation_rate": round(
                (int(before["answers_with_citation"]) + int(after["answers_with_citation"]))
                / combined_denominator,
                4,
            ),
            "visual_rate": round(
                (int(before["answers_with_visual"]) + int(after["answers_with_visual"]))
                / combined_denominator,
                4,
            ),
            "response_complete_rate": round(
                (int(before["complete_answers"]) + int(after["complete_answers"]))
                / combined_denominator,
                4,
            ),
        }
        score = round(
            35 * combined["cell_coverage_rate"]
            + 25 * combined["extract_rate"]
            + 15 * combined["visual_rate"]
            + 15 * combined["citation_rate"]
            + 10 * combined["response_complete_rate"],
            2,
        )
        scored.append(
            {
                **group,
                "eligible_for_selection": bool(
                    group.get("present_before") and group.get("present_after")
                ),
                "before_evidence": before,
                "after_evidence": after,
                "combined_evidence": combined,
                "selection_score": score,
                "selection_basis": (
                    "35% 单元观测覆盖 + 25% 品牌抽取覆盖 + 15% 图片证据覆盖 + "
                    "15% 引用覆盖 + 10% 回答正文完整度；不读取品牌提及、排名、"
                    "竞品结果或前后变化方向"
                ),
            }
        )
    ranked = sorted(
        [row for row in scored if row["eligible_for_selection"]],
        key=lambda row: (-float(row["selection_score"]), int(row["index"])),
    )
    ranks = {str(row["id"]): index for index, row in enumerate(ranked, 1)}
    selected = {str(row["id"]) for row in ranked[:REQUIRED_MAIN_GROUPS]}
    for row in scored:
        row["selection_rank"] = ranks.get(str(row["id"]))
        row["selected_for_main_report"] = str(row["id"]) in selected
    return scored


def _set_summary(values: set[str]) -> str:
    if not values:
        return "未留存"
    return "、".join(sorted(values))


def _account_strategy(
    answers: list[dict[str, Any]], provenance: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    by_platform: dict[str, dict[str, Any]] = {}
    missing = 0
    for answer in answers:
        platform = str(answer.get("model") or "")
        row = provenance.get(str(answer.get("pub_id"))) or {}
        account = str(row.get("platform_account_pub_id") or "")
        profile = str(row.get("browser_profile_version_pub_id") or "")
        bucket = by_platform.setdefault(
            platform, {"accounts": set(), "profiles": set(), "missing": 0}
        )
        if account:
            accounts = bucket["accounts"]
            assert isinstance(accounts, set)
            accounts.add(account)
        else:
            bucket["missing"] = int(bucket["missing"]) + 1
            missing += 1
        if profile:
            profiles = bucket["profiles"]
            assert isinstance(profiles, set)
            profiles.add(profile)
        else:
            bucket["missing"] = int(bucket["missing"]) + 1
            missing += 1
    signature = {
        platform: {
            "account_count": len(bucket["accounts"]),
            "profile_count": len(bucket["profiles"]),
            "missing_rows": int(bucket["missing"]),
        }
        for platform, bucket in sorted(by_platform.items())
    }
    return {
        "verifiable": bool(answers) and missing == 0,
        "signature": signature,
        "summary": "；".join(
            f"{MODEL_LABELS.get(platform, platform)}：{row['account_count']} 个账号、"
            f"{row['profile_count']} 个浏览器配置、缺失字段 {row['missing_rows']} 个"
            for platform, row in signature.items()
        )
        or "未留存账号策略",
    }


def _snapshot_contract(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(snapshots),
        "all_frozen": bool(snapshots)
        and all(metadata.get("frozen_at") is not None for metadata in snapshots),
        "snapshot_hashes": sorted(
            str(metadata.get("snapshot_hash"))
            for metadata in snapshots
            if metadata.get("snapshot_hash")
        ),
        "loaded_config_ids": {
            str(metadata.get("_config_version_pub_id"))
            for metadata in snapshots
            if metadata.get("_config_version_pub_id")
        },
    }


def _check(
    key: str,
    label: str,
    before: object,
    after: object,
    *,
    passed: bool,
    missing: bool = False,
    disclosure: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "before": before,
        "after": after,
        "status": "unverifiable" if missing else "matched" if passed else "mismatched",
        "passed": passed and not missing,
        "disclosure": disclosure,
    }


def build_comparability_contract(
    *,
    groups: list[dict[str, Any]],
    selected_group_ids: set[str],
    before_answers: list[dict[str, Any]],
    after_answers: list[dict[str, Any]],
    before_configured: dict[str, Any],
    after_configured: dict[str, Any],
    before_snapshots: list[dict[str, Any]],
    after_snapshots: list[dict[str, Any]],
    extracts: dict[str, dict[str, Any]],
    provenance: dict[str, dict[str, Any]],
    truncated: dict[str, bool],
) -> dict[str, Any]:
    all_before_questions = {
        _normal_text(row.get("query_text")) for row in before_answers if row.get("query_text")
    }
    all_after_questions = {
        _normal_text(row.get("query_text")) for row in after_answers if row.get("query_text")
    }
    configured_before_questions = {
        _normal_text(question)
        for group in groups
        if group.get("present_before")
        for question in group.get("questions") or []
    }
    configured_after_questions = {
        _normal_text(question)
        for group in groups
        if group.get("present_after")
        for question in group.get("questions") or []
    }
    selected_questions = {
        _normal_text(question)
        for group in groups
        if str(group.get("id")) in selected_group_ids
        for question in group.get("questions") or []
    }
    before_selected = [
        row for row in before_answers if _normal_text(row.get("query_text")) in selected_questions
    ]
    after_selected = [
        row for row in after_answers if _normal_text(row.get("query_text")) in selected_questions
    ]
    before_cells = Counter(_answer_cell(row) for row in before_selected)
    after_cells = Counter(_answer_cell(row) for row in after_selected)
    before_all_cells = Counter(_answer_cell(row) for row in before_answers)
    after_all_cells = Counter(_answer_cell(row) for row in after_answers)
    before_actual = {
        "models": sorted({str(row.get("model") or "") for row in before_answers}),
        "regions": sorted({str(row.get("region") or "") for row in before_answers}),
        "modes": sorted({str(row.get("mode") or "") for row in before_answers}),
    }
    after_actual = {
        "models": sorted({str(row.get("model") or "") for row in after_answers}),
        "regions": sorted({str(row.get("region") or "") for row in after_answers}),
        "modes": sorted({str(row.get("mode") or "") for row in after_answers}),
    }
    before_account = _account_strategy(before_answers, provenance)
    after_account = _account_strategy(after_answers, provenance)
    before_extract_versions = {
        str((extracts.get(str(row.get("pub_id"))) or {}).get("model") or "")
        for row in before_answers
        if (extracts.get(str(row.get("pub_id"))) or {}).get("model")
    }
    after_extract_versions = {
        str((extracts.get(str(row.get("pub_id"))) or {}).get("model") or "")
        for row in after_answers
        if (extracts.get(str(row.get("pub_id"))) or {}).get("model")
    }
    before_adapters = {str(row.get("adapter_version") or "") for row in before_answers}
    after_adapters = {str(row.get("adapter_version") or "") for row in after_answers}
    before_freeze = _snapshot_contract(before_snapshots)
    after_freeze = _snapshot_contract(after_snapshots)
    before_config_ids = {
        str(row.get("config_version_pub_id"))
        for row in before_answers
        if row.get("config_version_pub_id")
    }
    after_config_ids = {
        str(row.get("config_version_pub_id"))
        for row in after_answers
        if row.get("config_version_pub_id")
    }
    before_lineage_complete = (
        bool(before_config_ids) and before_config_ids == before_freeze["loaded_config_ids"]
    )
    after_lineage_complete = (
        bool(after_config_ids) and after_config_ids == after_freeze["loaded_config_ids"]
    )
    required_before = int(before_configured.get("required_repetitions") or 0)
    required_after = int(after_configured.get("required_repetitions") or 0)
    repeats_exact = (
        bool(before_all_cells)
        and before_all_cells == after_all_cells
        and required_before == required_after
        and all(count == required_before for count in before_all_cells.values())
    )
    account_known = bool(before_account["verifiable"] and after_account["verifiable"])
    extraction_known = bool(before_extract_versions and after_extract_versions)
    checks = [
        _check(
            "candidate_question_matrix",
            "全部候选问题矩阵",
            f"{len(all_before_questions)} 个问题",
            f"{len(all_after_questions)} 个问题",
            passed=(
                all_before_questions == all_after_questions
                and all_before_questions == configured_before_questions
                and all_after_questions == configured_after_questions
                and bool(all_before_questions)
            ),
            missing=not all_before_questions or not all_after_questions,
            disclosure="双臂全部候选问题文本必须完全相同；差异题不会被解释为优化效果。",
        ),
        _check(
            "platforms",
            "AI 平台",
            "、".join(before_actual["models"]) or "未采集",
            "、".join(after_actual["models"]) or "未采集",
            passed=before_actual["models"] == after_actual["models"]
            and before_actual["models"] == before_configured.get("models")
            and after_actual["models"] == after_configured.get("models"),
            missing=not before_actual["models"] or not after_actual["models"],
            disclosure="实际答案的平台集合还必须与各自冻结配置一致。",
        ),
        _check(
            "modes",
            "采集模式",
            "、".join(before_actual["modes"]) or "未采集",
            "、".join(after_actual["modes"]) or "未采集",
            passed=before_actual["modes"] == after_actual["modes"]
            and before_actual["modes"] == before_configured.get("modes")
            and after_actual["modes"] == after_configured.get("modes"),
            missing=not before_actual["modes"] or not after_actual["modes"],
            disclosure="普通/深度思考等模式不同会改变回答行为，不能混作同一实验。",
        ),
        _check(
            "regions",
            "采样地域",
            "、".join(before_actual["regions"]) or "未采集",
            "、".join(after_actual["regions"]) or "未采集",
            passed=before_actual["regions"] == after_actual["regions"]
            and before_actual["regions"] == before_configured.get("regions")
            and after_actual["regions"] == after_configured.get("regions"),
            missing=not before_actual["regions"] or not after_actual["regions"],
            disclosure="地域集合必须相同且与冻结配置一致。",
        ),
        _check(
            "repetitions",
            "逐单元重复次数",
            (
                f"{min(before_cells.values()) if before_cells else 0}–"
                f"{max(before_cells.values()) if before_cells else 0} 次"
            ),
            (
                f"{min(after_cells.values()) if after_cells else 0}–"
                f"{max(after_cells.values()) if after_cells else 0} 次"
            ),
            passed=repeats_exact,
            missing=not before_cells or not after_cells,
            disclosure=(
                f"每个问题×平台×模式×地域单元均须恰好完成 {required_before} 次，"
                "并在双臂逐单元相等。"
            ),
        ),
        _check(
            "account_strategy",
            "账号与浏览器配置策略",
            before_account["summary"],
            after_account["summary"],
            passed=before_account["signature"] == after_account["signature"],
            missing=not account_known,
            disclosure="仅比较账号数量/浏览器配置数量策略，不在客户报告暴露内部账号标识。",
        ),
        _check(
            "metric_version",
            "指标版本",
            SERVICE4_METRIC_VERSION,
            SERVICE4_METRIC_VERSION,
            passed=True,
            disclosure="双臂在同一次冻结事实构建中使用同一指标实现。",
        ),
        _check(
            "extraction_version",
            "品牌抽取版本",
            _set_summary(before_extract_versions),
            _set_summary(after_extract_versions),
            passed=before_extract_versions == after_extract_versions,
            missing=not extraction_known,
            disclosure="抽取模型版本缺失或不同会使品牌序列不可直接比较。",
        ),
        _check(
            "adapter_version",
            "采集适配器版本",
            _set_summary(before_adapters - {""}),
            _set_summary(after_adapters - {""}),
            passed=before_adapters == after_adapters and "" not in before_adapters,
            missing=(
                not before_adapters or not after_adapters or "" in before_adapters | after_adapters
            ),
            disclosure="适配器版本作为额外稳定性检查；缺失时不能证明采集行为一致。",
        ),
        _check(
            "freeze_rule",
            "冻结规则",
            f"{before_freeze['count']} 份配置，全部冻结={before_freeze['all_frozen']}",
            f"{after_freeze['count']} 份配置，全部冻结={after_freeze['all_frozen']}",
            passed=bool(
                before_freeze["all_frozen"]
                and after_freeze["all_frozen"]
                and before_lineage_complete
                and after_lineage_complete
            ),
            missing=(
                not before_snapshots
                or not after_snapshots
                or not before_lineage_complete
                or not after_lineage_complete
            ),
            disclosure="两臂均使用闭区间窗口、冻结配置和同一生成时点读取的事实。",
        ),
        _check(
            "config_consistency",
            "臂内冻结配置一致性",
            "一致" if before_configured.get("consistent") else "存在多套矩阵",
            "一致" if after_configured.get("consistent") else "存在多套矩阵",
            passed=bool(
                before_configured.get("consistent")
                and after_configured.get("consistent")
                and before_configured.get("repetition_config_consistent")
                and after_configured.get("repetition_config_consistent")
            ),
            disclosure="同一臂内若同时出现多套平台/地域/模式或重复配置，则不可归为单一试点臂。",
        ),
        _check(
            "complete_read",
            "事实读取完整性",
            "完整" if not truncated.get("before") else "达到安全上限",
            "完整" if not truncated.get("after") else "达到安全上限",
            passed=not truncated.get("before") and not truncated.get("after"),
            disclosure="达到读取安全上限时停止效果签发，不能静默截断样本。",
        ),
    ]
    failed = [row for row in checks if not row["passed"]]
    return {
        "status": "comparable" if not failed else "not_comparable",
        "all_checks_passed": not failed,
        "checks": checks,
        "failed_checks": [str(row["key"]) for row in failed],
        "selected_question_count": len(selected_questions),
        "before_selected_answers": len(before_selected),
        "after_selected_answers": len(after_selected),
        "cell_registry": [
            {
                "question": key[0],
                "platform": key[1],
                "region": key[2],
                "mode": key[3],
                "before_n": before_cells.get(key, 0),
                "after_n": after_cells.get(key, 0),
                "matched": before_cells.get(key, 0) == after_cells.get(key, 0),
            }
            for key in sorted(set(before_cells) | set(after_cells))
        ],
        "before_selected_answer_ids": [str(row.get("pub_id")) for row in before_selected],
        "after_selected_answer_ids": [str(row.get("pub_id")) for row in after_selected],
    }


def _wilson_interval(successes: int, total: int) -> tuple[float, float] | None:
    if total <= 0:
        return None
    z = 1.96
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    spread = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return round(max(0.0, center - spread) * 100, 2), round(min(1.0, center + spread) * 100, 2)


def _mean_interval(values: list[int]) -> tuple[float, float] | None:
    if len(values) < 2:
        return None
    average = mean(values)
    margin = 1.96 * stdev(values) / math.sqrt(len(values))
    return round(max(1.0, average - margin), 2), round(average + margin, 2)


def _relative_change(before: float | None, after: float | None) -> float | None:
    if before is None or after is None or before == 0:
        return None
    return round((after - before) / abs(before) * 100, 2)


def _metric_row(
    *,
    key: str,
    label: str,
    unit: str,
    direction: str,
    before: float | None,
    after: float | None,
    before_numerator: int | None,
    after_numerator: int | None,
    before_denominator: int,
    after_denominator: int,
    before_interval: tuple[float, float] | None,
    after_interval: tuple[float, float] | None,
    stability: str,
) -> dict[str, Any]:
    absolute = round(after - before, 2) if before is not None and after is not None else None
    return {
        "key": key,
        "label": label,
        "unit": unit,
        "direction": direction,
        "before": before,
        "after": after,
        "absolute_change": absolute,
        "relative_change_percent": _relative_change(before, after),
        "before_numerator": before_numerator,
        "after_numerator": after_numerator,
        "before_n": before_denominator,
        "after_n": after_denominator,
        "before_interval_95": before_interval,
        "after_interval_95": after_interval,
        "stability": stability,
    }


def _analyze_arm(
    answers: list[dict[str, Any]],
    *,
    extracts: dict[str, dict[str, Any]],
    citations: dict[str, list[dict[str, Any]]],
    domain: str,
    target_brand: str,
    competitors: list[str],
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    source_records: list[dict[str, Any]] = []
    for answer in answers:
        answer_id = str(answer.get("pub_id"))
        extract = extracts.get(answer_id) or {}
        if extract.get("status") == "ok" and isinstance(extract.get("brands"), list):
            records.append(adapter.answer_to_brand_record(answer, list(extract["brands"])))
        for citation in citations.get(answer_id, []):
            source = adapter.citation_to_source_entry(citation)
            source["thinking_mode"] = adapter.mode_label(str(answer.get("mode") or ""))
            source["ip"] = str(answer.get("region") or "")
            source_records.append(source)
    return metrics.analyze(
        records,
        source_records,
        rules=load_domain(domain),
        target_brand=target_brand,
        competitors=competitors,
        top_ns=TOP_NS,
    )


def _stability_note(
    comparability: dict[str, Any],
    before_n: int,
    after_n: int,
    *,
    before_interval: tuple[float, float] | None = None,
    after_interval: tuple[float, float] | None = None,
) -> str:
    if comparability["status"] != "comparable":
        return "矩阵或版本检查未全部通过；仅作描述性观察，不归因于优化。"
    minimum = min(
        (min(row["before_n"], row["after_n"]) for row in comparability["cell_registry"]),
        default=0,
    )
    if before_interval is None or after_interval is None:
        interval_note = "样本不足以形成双臂区间，不判定变化稳定。"
    elif before_interval[1] >= after_interval[0] and after_interval[1] >= before_interval[0]:
        interval_note = "双臂 95% 区间重叠，不宣称稳定改善。"
    else:
        interval_note = "双臂 95% 区间未重叠，但这仍不是因果或显著性检验。"
    return (
        f"双臂逐单元均完成 {minimum} 次重复；before n={before_n}、after n={after_n}。"
        f"{interval_note}"
    )


def build_metric_comparison(
    before_analysis: dict[str, Any],
    after_analysis: dict[str, Any],
    *,
    comparability: dict[str, Any],
) -> list[dict[str, Any]]:
    before_target = before_analysis.get("target_brand") or {}
    after_target = after_analysis.get("target_brand") or {}
    before_n = int((before_analysis.get("denominators") or {}).get("n_answers") or 0)
    after_n = int((after_analysis.get("denominators") or {}).get("n_answers") or 0)
    rows: list[dict[str, Any]] = []
    mention_before = int(before_target.get("mentions") or 0)
    mention_after = int(after_target.get("mentions") or 0)
    mention_before_interval = _wilson_interval(mention_before, before_n)
    mention_after_interval = _wilson_interval(mention_after, after_n)
    rows.append(
        _metric_row(
            key="mention_rate",
            label="品牌提及率",
            unit="percentage_point",
            direction="higher_is_better",
            before=float(before_target.get("appearance_rate") or 0),
            after=float(after_target.get("appearance_rate") or 0),
            before_numerator=mention_before,
            after_numerator=mention_after,
            before_denominator=before_n,
            after_denominator=after_n,
            before_interval=mention_before_interval,
            after_interval=mention_after_interval,
            stability=_stability_note(
                comparability,
                before_n,
                after_n,
                before_interval=mention_before_interval,
                after_interval=mention_after_interval,
            ),
        )
    )
    before_ranks = [int(value) for value in before_target.get("ranks") or []]
    after_ranks = [int(value) for value in after_target.get("ranks") or []]
    before_rank_interval = _mean_interval(before_ranks)
    after_rank_interval = _mean_interval(after_ranks)
    rows.append(
        _metric_row(
            key="avg_rank",
            label="平均推荐位次",
            unit="rank",
            direction="lower_is_better",
            before=before_target.get("avg_rank"),
            after=after_target.get("avg_rank"),
            before_numerator=len(before_ranks),
            after_numerator=len(after_ranks),
            before_denominator=before_n,
            after_denominator=after_n,
            before_interval=before_rank_interval,
            after_interval=after_rank_interval,
            stability=_stability_note(
                comparability,
                before_n,
                after_n,
                before_interval=before_rank_interval,
                after_interval=after_rank_interval,
            ),
        )
    )
    for top_n in TOP_NS:
        before_rate = (before_target.get("top_rates") or {}).get(str(top_n), {})
        after_rate = (after_target.get("top_rates") or {}).get(str(top_n), {})
        before_successes = sum(rank <= top_n for rank in before_ranks)
        after_successes = sum(rank <= top_n for rank in after_ranks)
        before_interval = _wilson_interval(before_successes, before_n)
        after_interval = _wilson_interval(after_successes, after_n)
        rows.append(
            _metric_row(
                key=f"top{top_n}_rate",
                label=f"Top{top_n} 出现率",
                unit="percentage_point",
                direction="higher_is_better",
                before=float(before_rate.get("of_total") or 0),
                after=float(after_rate.get("of_total") or 0),
                before_numerator=before_successes,
                after_numerator=after_successes,
                before_denominator=before_n,
                after_denominator=after_n,
                before_interval=before_interval,
                after_interval=after_interval,
                stability=_stability_note(
                    comparability,
                    before_n,
                    after_n,
                    before_interval=before_interval,
                    after_interval=after_interval,
                ),
            )
        )
    return rows


def _brand_lookup(analysis: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rules = load_domain(str(analysis.get("domain") or ""))
    answer_count = int((analysis.get("denominators") or {}).get("n_answers") or 0)
    rows = list((analysis.get("overall") or {}).get("merged") or [])
    # ``overall.merged`` already contains all observed brands and their ranks.  The
    # target/competitor objects add exact Top-N fields; only fields needed here are
    # copied to keep the fact snapshot compact.
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("brand") or "")
        if not name:
            continue
        output[name] = {
            "brand": name,
            "mentions": int(row.get("occurrences") or 0),
            "appearance_rate": (
                round(int(row.get("occurrences") or 0) / answer_count * 100, 2)
                if answer_count
                else None
            ),
            "avg_rank": row.get("avg_rank"),
            "overall_rank": row.get("rank"),
            "normalized": normalize_brand_list([name], rules)[0],
        }
    return output


def build_landscape_comparison(
    before_analysis: dict[str, Any], after_analysis: dict[str, Any], *, target_brand: str
) -> list[dict[str, Any]]:
    before = _brand_lookup(before_analysis)
    after = _brand_lookup(after_analysis)
    rules = load_domain(str(before_analysis.get("domain") or after_analysis.get("domain") or ""))
    normalized_target = normalize_brand_list([target_brand], rules)[0]
    rows: list[dict[str, Any]] = []
    for brand in set(before) | set(after):
        left = before.get(brand) or {}
        right = after.get(brand) or {}
        before_rate = float(left.get("appearance_rate") or 0)
        after_rate = float(right.get("appearance_rate") or 0)
        rows.append(
            {
                "brand": brand,
                "is_target": brand == normalized_target,
                "before_mentions": int(left.get("mentions") or 0),
                "after_mentions": int(right.get("mentions") or 0),
                "before_appearance_rate": before_rate,
                "after_appearance_rate": after_rate,
                "absolute_change": round(after_rate - before_rate, 2),
                "relative_change_percent": _relative_change(before_rate, after_rate),
                "before_avg_rank": left.get("avg_rank"),
                "after_avg_rank": right.get("avg_rank"),
            }
        )
    rows.sort(
        key=lambda row: (
            not bool(row["is_target"]),
            -max(float(row["before_appearance_rate"]), float(row["after_appearance_rate"])),
            str(row["brand"]),
        )
    )
    return rows


def build_source_comparison(
    before_analysis: dict[str, Any], after_analysis: dict[str, Any]
) -> list[dict[str, Any]]:
    before_source = (before_analysis.get("sources") or {}).get("overall") or {}
    after_source = (after_analysis.get("sources") or {}).get("overall") or {}
    before_counts = {
        str(key): int(value) for key, value in (before_source.get("sitename_counts") or {}).items()
    }
    after_counts = {
        str(key): int(value) for key, value in (after_source.get("sitename_counts") or {}).items()
    }
    before_total = int(before_source.get("total") or 0)
    after_total = int(after_source.get("total") or 0)

    def exact_shares(counts: dict[str, int], total: int) -> dict[str, float]:
        if total <= 0:
            return {site: 0.0 for site in counts}
        shares = {site: round(count / total * 100, 2) for site, count in counts.items()}
        if shares:
            anchor = max(counts, key=lambda site: (counts[site], site))
            shares[anchor] = round(shares[anchor] + 100.0 - sum(shares.values()), 2)
        return shares

    before_shares = exact_shares(before_counts, before_total)
    after_shares = exact_shares(after_counts, after_total)
    rows: list[dict[str, Any]] = []
    for site in set(before_counts) | set(after_counts):
        before_count = before_counts.get(site, 0)
        after_count = after_counts.get(site, 0)
        before_share = before_shares.get(site, 0.0)
        after_share = after_shares.get(site, 0.0)
        rows.append(
            {
                "site": site,
                "before_count": before_count,
                "after_count": after_count,
                "before_share": before_share,
                "after_share": after_share,
                "absolute_change": round(after_share - before_share, 2),
                "relative_change_percent": _relative_change(before_share, after_share),
                "before_total_references": before_total,
                "after_total_references": after_total,
            }
        )
    rows.sort(
        key=lambda row: (-max(int(row["before_count"]), int(row["after_count"])), str(row["site"]))
    )
    return rows


def _own_site_extension(
    before_answers: list[dict[str, Any]],
    after_answers: list[dict[str, Any]],
    citations: dict[str, list[dict[str, Any]]],
    comparability: dict[str, Any],
) -> dict[str, Any]:
    def arm(rows: list[dict[str, Any]]) -> dict[str, int | float]:
        with_own = sum(
            any(
                bool(citation.get("own_source"))
                for citation in citations.get(str(row["pub_id"]), [])
            )
            for row in rows
        )
        refs = sum(
            bool(citation.get("own_source"))
            for row in rows
            for citation in citations.get(str(row["pub_id"]), [])
        )
        return {
            "answers": len(rows),
            "answers_with_own_site": with_own,
            "own_site_references": refs,
            "answer_citation_rate": round(with_own / len(rows) * 100, 2) if rows else 0.0,
        }

    before = arm(before_answers)
    after = arm(after_answers)
    before_rate = float(before["answer_citation_rate"])
    after_rate = float(after["answer_citation_rate"])
    return {
        "status": "evaluated" if before_answers and after_answers else "insufficient",
        "evidence_basis": "回答级 citation_fact.own_source；不从已抓取文档数反推",
        "before": before,
        "after": after,
        "absolute_change": round(after_rate - before_rate, 2),
        "relative_change_percent": _relative_change(before_rate, after_rate),
        "stability": _stability_note(
            comparability,
            len(before_answers),
            len(after_answers),
            before_interval=_wilson_interval(
                int(before["answers_with_own_site"]), len(before_answers)
            ),
            after_interval=_wilson_interval(
                int(after["answers_with_own_site"]), len(after_answers)
            ),
        ),
        "boundary": "只评价最终可见官网引用；不推断平台未暴露的候选或打开阶段。",
    }


def _group_results(
    groups: list[dict[str, Any]],
    before_answers: list[dict[str, Any]],
    after_answers: list[dict[str, Any]],
    *,
    extracts: dict[str, dict[str, Any]],
    citations: dict[str, list[dict[str, Any]]],
    domain: str,
    target_brand: str,
    competitors: list[str],
    comparability: dict[str, Any],
) -> list[dict[str, Any]]:
    output = []
    for group in groups:
        if not group.get("selected_for_main_report"):
            continue
        before_members = _group_answer_rows(group, before_answers)
        after_members = _group_answer_rows(group, after_answers)
        before_analysis = _analyze_arm(
            before_members,
            extracts=extracts,
            citations=citations,
            domain=domain,
            target_brand=target_brand,
            competitors=competitors,
        )
        after_analysis = _analyze_arm(
            after_members,
            extracts=extracts,
            citations=citations,
            domain=domain,
            target_brand=target_brand,
            competitors=competitors,
        )
        output.append(
            {
                "group_id": group["id"],
                "title": group["title"],
                "questions": group["questions"],
                "metrics": build_metric_comparison(
                    before_analysis, after_analysis, comparability=comparability
                ),
            }
        )
    return output


def _pilot_plan(
    *,
    target_brand: str,
    selected_groups: list[dict[str, Any]],
    before_analysis: dict[str, Any] | None,
    required_repetitions: int,
    configured: dict[str, Any],
) -> dict[str, Any]:
    group_names = "、".join(str(row["title"]) for row in selected_groups) or "待补齐的三组业务问题"
    baseline_target = (before_analysis or {}).get("target_brand") or {}
    source = ((before_analysis or {}).get("sources") or {}).get("overall") or {}
    baseline_evidence = (
        f"基线品牌提及率 {float(baseline_target.get('appearance_rate') or 0):.2f}%，"
        f"提及时平均位次 {baseline_target.get('avg_rank') or '未形成'}；"
        f"共观察到 {int(source.get('total') or 0)} 条最终引用。"
        if before_analysis
        else "基线品牌抽取证据不足，先补齐采集后再制定具体内容优先级。"
    )
    return {
        "status": "proposed_not_execution_record",
        "scope": group_names,
        "objective": (
            f"围绕 {group_names} 建立可被 AI 回答核验和引用的公开信息，使 {target_brand} "
            "在固定矩阵复测中获得可审计的可见性变化。"
        ),
        "baseline_evidence": baseline_evidence,
        "actions": [
            {
                "number": 1,
                "owner": "试点执行方",
                "object": "外部公开信息源建设",
                "basis": "基线最终引用的网站结构与目标品牌可见性结果",
                "work": (
                    "选择与三组业务问题直接相关、允许公开核验的第三方页面，补齐产品能力、"
                    "适用边界、案例条件和发布日期；不购买不可审计的虚假背书。"
                ),
                "boundary": (
                    "该项为待执行方案；只有留存发布 URL、版本、时间和页面快照后才记为完成。"
                ),
            },
            {
                "number": 2,
                "owner": "试点执行方与客户内容审核方",
                "object": "官网内容优化",
                "basis": "候选问题文本、基线回答表述和官网最终引用情况",
                "work": (
                    "按问题意图组织可独立引用的事实段落，明确主体、能力条件、证据出处和更新时间；"
                    "禁止加入无法公开核验的第一、唯一或绝对效果承诺。"
                ),
                "boundary": "审核、发布和留证完成前，不把草案记作已上线内容。",
            },
            {
                "number": 3,
                "owner": "评测执行方",
                "object": "冻结复测",
                "basis": "与基线相同的候选问题矩阵和采集契约",
                "work": (
                    f"固定 {len(configured.get('models') or [])} 个平台、"
                    f"{len(configured.get('regions') or [])} 个地域、"
                    f"{len(configured.get('modes') or [])} 种模式，每个单元重复 "
                    f"{required_repetitions} 次；冻结配置与证据后再生成效果报告。"
                ),
                "boundary": "任一可比性检查失败即停止效果归因，先补采或重建双臂。",
            },
        ],
        "acceptance_metrics": [
            "品牌提及率、平均推荐位次、Top1/Top3/Top5 出现率",
            "竞品品牌格局与全部最终引用的网站结构",
            "有回答级官网引用证据时，扩展比较官网引用率",
        ],
        "stop_conditions": [
            "双臂问题、平台、模式、地域或逐单元重复次数不一致",
            "账号/浏览器配置策略、指标版本、抽取版本或冻结状态无法核验",
            "所选三组存在品牌抽取缺口，或读取达到安全上限",
            "发布内容没有 URL、版本、发布时间和快照等执行留证",
        ],
    }


def assemble_service4_review_facts(
    *,
    project: dict[str, Any],
    before_answers: list[dict[str, Any]],
    after_answers: list[dict[str, Any]],
    before_snapshots: list[dict[str, Any]],
    after_snapshots: list[dict[str, Any]],
    extracts: dict[str, dict[str, Any]],
    citations: dict[str, list[dict[str, Any]]],
    visuals: dict[str, dict[str, bool]],
    provenance: dict[str, dict[str, Any]],
    before_start: date,
    before_end: date,
    after_start: date,
    after_end: date,
    generated_at: datetime,
    truncated: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Pure assembly seam used by tests and by the PostgreSQL-backed entrypoint."""

    if before_start > before_end or after_start > after_end:
        raise ValueError("service4_window_reversed")
    if before_end >= after_start:
        raise ValueError("service4_windows_overlap_or_out_of_order")
    domain = str(project.get("brandrank_domain") or "").strip()
    if not domain:
        raise ValueError("brandrank_domain_unset")
    load_domain(domain)
    target_brand = str((project.get("brand_names") or [""])[0]).strip()
    if not target_brand:
        raise ValueError("target_brand_unset")
    competitors = [str(value) for value in project.get("competitor_names") or []]
    truncated = truncated or {"before": False, "after": False}
    before_configured = _configured_dimensions(before_snapshots)
    after_configured = _configured_dimensions(after_snapshots)
    groups = merge_candidate_groups(before_snapshots, after_snapshots)
    scored_groups = score_service4_candidate_groups(
        groups,
        before_answers,
        after_answers,
        extracts=extracts,
        citations=citations,
        visuals=visuals,
        before_configured=before_configured,
        after_configured=after_configured,
    )
    selected_groups = [row for row in scored_groups if row["selected_for_main_report"]]
    selected_group_ids = {str(row["id"]) for row in selected_groups}
    comparability = build_comparability_contract(
        groups=scored_groups,
        selected_group_ids=selected_group_ids,
        before_answers=before_answers,
        after_answers=after_answers,
        before_configured=before_configured,
        after_configured=after_configured,
        before_snapshots=before_snapshots,
        after_snapshots=after_snapshots,
        extracts=extracts,
        provenance=provenance,
        truncated=truncated,
    )
    before_ids = list(comparability.pop("before_selected_answer_ids"))
    after_ids = list(comparability.pop("after_selected_answer_ids"))
    before_by_id = {str(row.get("pub_id")): row for row in before_answers}
    after_by_id = {str(row.get("pub_id")): row for row in after_answers}
    before_selected = [before_by_id[value] for value in before_ids if value in before_by_id]
    after_selected = [after_by_id[value] for value in after_ids if value in after_by_id]
    selected_extract_complete = bool(before_selected and after_selected) and all(
        (extracts.get(str(row.get("pub_id"))) or {}).get("status") == "ok"
        and isinstance((extracts.get(str(row.get("pub_id"))) or {}).get("brands"), list)
        for row in [*before_selected, *after_selected]
    )
    selected_sample_complete = bool(selected_groups) and all(
        int(row[arm]["expected_observations"]) > 0
        and int(row[arm]["covered_observations"]) == int(row[arm]["expected_observations"])
        for row in selected_groups
        for arm in ("before_evidence", "after_evidence")
    )
    insufficient_reasons: list[str] = []
    if not before_answers:
        insufficient_reasons.append("before_no_answers")
    if not after_answers:
        insufficient_reasons.append("after_no_answers")
    if len(selected_groups) < REQUIRED_MAIN_GROUPS:
        insufficient_reasons.append("fewer_than_three_common_candidate_groups")
    if selected_groups and not selected_extract_complete:
        insufficient_reasons.append("selected_group_extraction_incomplete")
    if selected_groups and not selected_sample_complete:
        insufficient_reasons.append("selected_group_sample_incomplete")
    if truncated.get("before") or truncated.get("after"):
        insufficient_reasons.append("answer_read_truncated")

    before_analysis: dict[str, Any] | None = None
    after_analysis: dict[str, Any] | None = None
    metric_rows: list[dict[str, Any]] = []
    landscape: list[dict[str, Any]] = []
    source_structure: list[dict[str, Any]] = []
    group_results: list[dict[str, Any]] = []
    own_site: dict[str, Any] = {
        "status": "insufficient",
        "boundary": "品牌指标证据不足，未计算官网引用扩展结果。",
    }
    if not insufficient_reasons:
        before_analysis = _analyze_arm(
            before_selected,
            extracts=extracts,
            citations=citations,
            domain=domain,
            target_brand=target_brand,
            competitors=competitors,
        )
        after_analysis = _analyze_arm(
            after_selected,
            extracts=extracts,
            citations=citations,
            domain=domain,
            target_brand=target_brand,
            competitors=competitors,
        )
        metric_rows = build_metric_comparison(
            before_analysis, after_analysis, comparability=comparability
        )
        landscape = build_landscape_comparison(
            before_analysis, after_analysis, target_brand=target_brand
        )
        source_structure = build_source_comparison(before_analysis, after_analysis)
        group_results = _group_results(
            scored_groups,
            before_answers,
            after_answers,
            extracts=extracts,
            citations=citations,
            domain=domain,
            target_brand=target_brand,
            competitors=competitors,
            comparability=comparability,
        )
        own_site = _own_site_extension(before_selected, after_selected, citations, comparability)

    evidence_status = "insufficient" if insufficient_reasons else "sufficient_for_description"
    attribution_allowed = not insufficient_reasons and comparability["status"] == "comparable"
    conclusion = (
        "证据不足：不计算前后变化，也不形成优化效果结论。"
        if insufficient_reasons
        else "双臂矩阵与版本检查通过，可报告同口径变化；因本事实链未包含干预执行台账，"
        "变化仍不单独证明由优化导致。"
        if attribution_allowed
        else "已计算双臂描述值，但存在不可比项；不得把变化归因于 GEO 优化。"
    )
    required_repetitions = int(before_configured.get("required_repetitions") or 0)
    return {
        "schema_version": SERVICE4_SCHEMA_VERSION,
        "document_status": "pre_formal_review",
        "project_pub_id": str(project.get("pub_id") or ""),
        "project_name": str(project.get("name") or ""),
        "target_brand": target_brand,
        "competitors": competitors,
        "domain": domain,
        "generated_at": generated_at,
        "window": {
            "start": before_start.isoformat(),
            "end": after_end.isoformat(),
        },
        "windows": {
            "before": {"start": before_start.isoformat(), "end": before_end.isoformat()},
            "after": {"start": after_start.isoformat(), "end": after_end.isoformat()},
        },
        "design": {
            "required_main_groups": REQUIRED_MAIN_GROUPS,
            "required_repetitions_per_cell": required_repetitions,
            "repetition_source": before_configured.get("repetition_source"),
            "before_configured": before_configured,
            "after_configured": after_configured,
            "metric_version": SERVICE4_METRIC_VERSION,
            "freeze_evidence": {
                "before": {
                    "snapshot_count": len(before_snapshots),
                    "all_frozen": bool(before_snapshots)
                    and all(row.get("frozen_at") is not None for row in before_snapshots),
                    "snapshot_hashes": sorted(
                        str(row["snapshot_hash"])
                        for row in before_snapshots
                        if row.get("snapshot_hash")
                    ),
                },
                "after": {
                    "snapshot_count": len(after_snapshots),
                    "all_frozen": bool(after_snapshots)
                    and all(row.get("frozen_at") is not None for row in after_snapshots),
                    "snapshot_hashes": sorted(
                        str(row["snapshot_hash"])
                        for row in after_snapshots
                        if row.get("snapshot_hash")
                    ),
                },
            },
            "selection_policy": (
                "只按单元、抽取、图片、引用和回答正文完整度选择三组；不使用目标品牌或竞品表现。"
            ),
            "freeze_rule": "闭区间窗口 + 冻结配置 + 单次事实生成时点",
        },
        "evidence_gate": {
            "status": evidence_status,
            "insufficient_reasons": insufficient_reasons,
            "attribution_allowed_by_matrix": attribution_allowed,
            "causal_claim_allowed": False,
            "causal_claim_blocker": "缺少独立的干预执行与发布时间台账",
            "conclusion": conclusion,
        },
        "comparability": comparability,
        "candidate_groups": scored_groups,
        "selected_group_ids": sorted(selected_group_ids),
        "arms": {
            "before": {
                "answers_all_candidates": len(before_answers),
                "answers_selected_groups": len(before_selected),
                "extract_ok_selected": sum(
                    (extracts.get(str(row.get("pub_id"))) or {}).get("status") == "ok"
                    for row in before_selected
                ),
                "answers_with_citation_selected": sum(
                    bool(citations.get(str(row.get("pub_id")))) for row in before_selected
                ),
                "answers_with_visual_selected": sum(
                    bool((visuals.get(str(row.get("pub_id"))) or {}).get("share_image"))
                    or bool((visuals.get(str(row.get("pub_id"))) or {}).get("answer_screenshot"))
                    for row in before_selected
                ),
            },
            "after": {
                "answers_all_candidates": len(after_answers),
                "answers_selected_groups": len(after_selected),
                "extract_ok_selected": sum(
                    (extracts.get(str(row.get("pub_id"))) or {}).get("status") == "ok"
                    for row in after_selected
                ),
                "answers_with_citation_selected": sum(
                    bool(citations.get(str(row.get("pub_id")))) for row in after_selected
                ),
                "answers_with_visual_selected": sum(
                    bool((visuals.get(str(row.get("pub_id"))) or {}).get("share_image"))
                    or bool((visuals.get(str(row.get("pub_id"))) or {}).get("answer_screenshot"))
                    for row in after_selected
                ),
            },
        },
        "metrics": metric_rows,
        "group_results": group_results,
        "competitor_landscape": landscape,
        "source_structure": source_structure,
        "own_site_extension": own_site,
        "pilot_plan": _pilot_plan(
            target_brand=target_brand,
            selected_groups=selected_groups,
            before_analysis=before_analysis,
            required_repetitions=required_repetitions,
            configured=before_configured,
        ),
        "limitations": [
            conclusion,
            "相对变化以前测值为分母；前测为 0 或任一臂无可评估值时不计算相对变化。",
            "比例指标附 Wilson 95% 区间，平均位次附正态近似区间；区间用于表达抽样不确定性，"
            "不是显著性检验或因果证明。",
            "官网扩展指标只基于回答最终引用的 own_source 标记，不推断隐藏检索过程。",
        ],
    }


def _load_arm_answers(
    dsn: str,
    tenant_pub_id: str,
    project_pub_id: str,
    start: date,
    end: date,
) -> tuple[list[dict[str, Any]], bool]:
    start_at = datetime.combine(start, time.min, tzinfo=UTC)
    end_at = datetime.combine(end, time.max, tzinfo=UTC)
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT pub_id, query_text, response_text, model, region, mode, capture_time,
                   run_pub_id, config_version_pub_id, adapter_version, channel
            FROM analytics.answer
            WHERE tenant_pub_id=%s AND project_pub_id=%s
              AND eligible AND NOT degraded
              AND capture_time >= %s AND capture_time <= %s
            ORDER BY capture_time, pub_id
            LIMIT %s
            """,
            (tenant_pub_id, project_pub_id, start_at, end_at, MAX_ARM_ANSWERS + 1),
        ).fetchall()
    return [dict(row) for row in rows[:MAX_ARM_ANSWERS]], len(rows) > MAX_ARM_ANSWERS


def _load_config_snapshots(
    dsn: str, tenant_pub_id: str, config_version_pub_ids: list[str]
) -> list[dict[str, Any]]:
    if not config_version_pub_ids:
        return []
    with brandrank_service._platform_tenant_connection(dsn, tenant_pub_id) as connection:
        rows = connection.execute(
            """
            SELECT pub_id, revision, frozen_at, snapshot_json, snapshot_hash
            FROM platform.monitoring_config_version
            WHERE pub_id=ANY(%s::text[])
            ORDER BY revision, pub_id
            """,
            (config_version_pub_ids,),
        ).fetchall()
    return [
        {
            "revision": int(row["revision"]),
            "frozen_at": row["frozen_at"],
            "snapshot_hash": str(row["snapshot_hash"]),
            "snapshot": _json_object(row["snapshot_json"]),
            # Kept transiently for mapping only; assembly never exposes it.
            "_config_version_pub_id": str(row["pub_id"]),
        }
        for row in rows
    ]


def _load_visuals_and_provenance(
    dsn: str, tenant_pub_id: str, answer_pub_ids: list[str]
) -> tuple[dict[str, dict[str, bool]], dict[str, dict[str, Any]]]:
    if not answer_pub_ids:
        return {}, {}
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        image_rows = connection.execute(
            """
            SELECT er.from_pub_id, ea.kind
            FROM evidence.evidence_relation er
            JOIN evidence.evidence_asset ea
              ON ea.tenant_pub_id=er.tenant_pub_id AND ea.pub_id=er.to_pub_id
            WHERE er.tenant_pub_id=%s AND er.from_pub_id=ANY(%s::text[])
              AND ea.kind IN ('share_image','answer_screenshot')
              AND ea.mime_type LIKE 'image/%%' AND ea.deleted_at IS NULL
            """,
            (tenant_pub_id, answer_pub_ids),
        ).fetchall()
        provenance_rows = connection.execute(
            """
            SELECT DISTINCT ON (aa.answer_pub_id)
                   aa.answer_pub_id, aa.platform_account_pub_id,
                   aa.browser_profile_version_pub_id, ar.metric_version,
                   ar.scorer_version, ar.model_version
            FROM analytics.answer_analysis aa
            JOIN analytics.analysis_run ar ON ar.pub_id=aa.analysis_run_pub_id
            WHERE aa.tenant_pub_id=%s AND aa.answer_pub_id=ANY(%s::text[])
            ORDER BY aa.answer_pub_id, aa.created_at DESC, aa.id DESC
            """,
            (tenant_pub_id, answer_pub_ids),
        ).fetchall()
    visuals: dict[str, dict[str, bool]] = defaultdict(dict)
    for row in image_rows:
        visuals[str(row["from_pub_id"])][str(row["kind"])] = True
    provenance = {str(row["answer_pub_id"]): dict(row) for row in provenance_rows}
    return dict(visuals), provenance


def build_service4_review_facts(
    *,
    dsn: str,
    tenant_pub_id: str,
    project_pub_id: str,
    before_start: date,
    before_end: date,
    after_start: date,
    after_end: date,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the complete, dynamic Service-4 review fact snapshot."""

    generated_at = generated_at or datetime.now(UTC)
    if before_start > before_end or after_start > after_end:
        raise ValueError("service4_window_reversed")
    if before_end >= after_start:
        raise ValueError("service4_windows_overlap_or_out_of_order")
    project = brandrank_service.fetch_project(dsn, tenant_pub_id, project_pub_id)
    if project is None:
        raise LookupError("project_not_found")
    domain = str(project.get("brandrank_domain") or "").strip()
    if not domain:
        raise ValueError("brandrank_domain_unset")
    load_domain(domain)
    before_answers, before_truncated = _load_arm_answers(
        dsn, tenant_pub_id, project_pub_id, before_start, before_end
    )
    after_answers, after_truncated = _load_arm_answers(
        dsn, tenant_pub_id, project_pub_id, after_start, after_end
    )
    all_answers = [*before_answers, *after_answers]
    answer_ids = [str(row["pub_id"]) for row in all_answers]
    extracts = brandrank_service.fetch_brand_extracts(dsn, tenant_pub_id, answer_ids, domain)
    citations = brandrank_service.fetch_citations(dsn, tenant_pub_id, answer_ids)
    visuals, provenance = _load_visuals_and_provenance(dsn, tenant_pub_id, answer_ids)
    before_config_ids = sorted(
        {
            str(row["config_version_pub_id"])
            for row in before_answers
            if row.get("config_version_pub_id")
        }
    )
    after_config_ids = sorted(
        {
            str(row["config_version_pub_id"])
            for row in after_answers
            if row.get("config_version_pub_id")
        }
    )
    before_snapshots = _load_config_snapshots(dsn, tenant_pub_id, before_config_ids)
    after_snapshots = _load_config_snapshots(dsn, tenant_pub_id, after_config_ids)
    return assemble_service4_review_facts(
        project=project,
        before_answers=before_answers,
        after_answers=after_answers,
        before_snapshots=before_snapshots,
        after_snapshots=after_snapshots,
        extracts=extracts,
        citations=citations,
        visuals=visuals,
        provenance=provenance,
        before_start=before_start,
        before_end=before_end,
        after_start=after_start,
        after_end=after_end,
        generated_at=generated_at,
        truncated={"before": before_truncated, "after": after_truncated},
    )
