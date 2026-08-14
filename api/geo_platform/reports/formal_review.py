"""Formal-layout review report facts for quotation services 1, 2 and 3.

The module deliberately separates measurement facts from document rendering.  The
platform application service, Temporal workflow and supported CLI all call this same
fact layer, so the metric definitions cannot drift between online and offline output.

Important disciplines:

* report release state is explicit and cannot imply approval;
* the service-1 main sample is balanced by independent run in each
  question/platform/region cell, while every excluded observation remains in the
  audit snapshot;
* service-1 question groups come from a pre-sampling registration; historical data
  without one can only produce a clearly limited, non-signable candidate;
* service-3 answer citation rates and fetched-document ratios are different metrics;
* transcript accuracy never masquerades as website-content adoption.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time
from typing import Any

from psycopg.rows import dict_row

from domain.brandrank import adapter, metrics
from domain.brandrank.rules import load_domain
from domain.reporting.service1_governance import (
    assign_repeats,
    question_group_hash,
    resolve_scope_registration,
)
from geo_platform.analytics.service import AnalyticsService, _platform_tenant_connection
from geo_platform.brandrank import service as brandrank_service
from geo_platform.reports import fact_suggestions
from geo_platform.tenancy.psycopg import tenant_connection

PRIMARY_MODE = "deep_think"
PRIMARY_MODELS = ("doubao", "deepseek", "yiyan")
PRIMARY_REGIONS = ("北京", "上海")
TOP_NS = (1, 3, 5)
QUOTATION_REPETITIONS_PER_CELL = 2
MAX_FORMAL_ANSWER_ROWS = 2_000
MAX_FORMAL_RESPONSE_BYTES = 32 * 1024 * 1024

MODEL_LABELS = {
    "doubao": "豆包",
    "deepseek": "DeepSeek",
    "yiyan": "文心一言",
    "tongyi": "通义千问",
    "yuanbao": "腾讯元宝",
}


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    return {}


def _group_title(index: int, first_question: str) -> str:
    q = first_question
    if "高校" in q or "双非" in q or "影子资产" in q:
        return "高校双非/影子资产排查"
    if "攻击面" in q or "暴露面" in q or "ASM" in q:
        return "攻击面管理（ASM）"
    if "漏洞" in q and "资产" in q:
        return "资产与漏洞一体化治理"
    if "空间" in q or "测绘" in q or "搜索引擎" in q:
        return "网络空间资产搜索"
    compact = re.sub(r"\s+", "", q)
    return compact[:18] or f"候选问题组 {index}"


def candidate_groups_from_snapshot(snapshot: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """Return report candidate groups and whether grouping had to be inferred.

    New configurations preserve blank-line-separated groups in ``query_groups``.  Old
    Shengbang snapshots put all 16 items in one group; when one long group is divisible
    by four we transparently infer the quotation's base+three-variant quartets and keep
    an explicit ``inferred`` marker in the fact snapshot.
    """

    raw_groups: list[tuple[str, list[str], dict[str, Any]]] = []
    for raw_group in snapshot.get("query_groups", []):
        if not isinstance(raw_group, dict):
            continue
        questions = [
            str(item["text"]).strip()
            for item in raw_group.get("items", [])
            if isinstance(item, dict)
            and isinstance(item.get("text"), str)
            and str(item["text"]).strip()
        ]
        if questions:
            raw_groups.append(
                (
                    str(raw_group.get("name") or "").strip(),
                    questions,
                    {
                        "service_number": raw_group.get("service_number"),
                        "quotation_appendix": raw_group.get("quotation_appendix"),
                        "business_line": raw_group.get("business_line"),
                    },
                )
            )
    if not raw_groups:
        return [], False

    inferred = (
        len(raw_groups) == 1 and len(raw_groups[0][1]) >= 8 and len(raw_groups[0][1]) % 4 == 0
    )
    if inferred:
        questions = raw_groups[0][1]
        raw_groups = [
            ("", questions[offset : offset + 4], {}) for offset in range(0, len(questions), 4)
        ]

    groups: list[dict[str, Any]] = []
    for index, (name, questions, metadata) in enumerate(raw_groups, 1):
        title = name if name and not inferred else _group_title(index, questions[0])
        groups.append(
            {
                "id": f"candidate_{index:02d}",
                "index": index,
                "title": title,
                "questions": questions,
                "inferred": inferred,
                "question_group_hash": question_group_hash(questions),
                **metadata,
            }
        )
    return groups, inferred


def balance_primary_answers(
    answers: list[dict[str, Any]],
    *,
    candidate_groups: list[dict[str, Any]],
    repetitions_per_cell: int = 1,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pick the latest bounded repetitions in each quotation-comparable cell.

    Returns ``(balanced, excluded_primary_duplicates)``.  Normal-mode observations and
    non-quotation platforms are not duplicates; callers retain them in the all-answer
    appendix separately.  The default preserves the historical one-observation helper
    contract; formal fact production explicitly requests the quotation's two independent
    repetitions.
    """

    if repetitions_per_cell < 1:
        raise ValueError("repetitions_per_cell_must_be_positive")
    question_to_group = {
        question: group["id"] for group in candidate_groups for question in group["questions"]
    }
    candidates: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    duplicates: list[dict[str, Any]] = []
    for answer in answers:
        question = str(answer.get("query_text") or "")
        if (
            question not in question_to_group
            or answer.get("mode") != PRIMARY_MODE
            or answer.get("model") not in PRIMARY_MODELS
            or answer.get("region") not in PRIMARY_REGIONS
        ):
            continue
        key = (question, str(answer["model"]), str(answer["region"]))
        candidates[key].append(answer)
    selected: list[dict[str, Any]] = []
    for rows in candidates.values():
        newest_first = sorted(
            rows,
            key=lambda row: (row["capture_time"], str(row["pub_id"])),
            reverse=True,
        )
        # A repeated answer must come from a different collection run.  Missing run
        # IDs remain individually visible for historical candidates, but the formal
        # governance gate rejects them as unproven independence.
        newest_by_run: list[dict[str, Any]] = []
        seen_runs: set[str] = set()
        for row in newest_first:
            run_id = str(row.get("run_pub_id") or "").strip()
            run_key = run_id or f"missing:{row.get('pub_id')}"
            if run_key in seen_runs:
                duplicates.append(row)
                continue
            seen_runs.add(run_key)
            newest_by_run.append(row)
        selected.extend(newest_by_run[:repetitions_per_cell])
        duplicates.extend(newest_by_run[repetitions_per_cell:])
    balanced = sorted(
        selected,
        key=lambda row: (
            question_to_group[str(row["query_text"])],
            str(row["query_text"]),
            str(row["model"]),
            str(row["region"]),
            row["capture_time"],
            str(row["pub_id"]),
        ),
    )
    for answer in balanced:
        answer["candidate_group_id"] = question_to_group[str(answer["query_text"])]
    return balanced, duplicates


def score_candidate_groups(
    groups: list[dict[str, Any]],
    balanced_answers: list[dict[str, Any]],
    extracts: dict[str, dict[str, Any]],
    citations: dict[str, list[dict[str, Any]]],
    *,
    required_repetitions: int = 1,
) -> list[dict[str, Any]]:
    """Score evidence completeness only; target/competitor outcomes are not inputs."""

    rows: list[dict[str, Any]] = []
    expected_per_question = len(PRIMARY_MODELS) * len(PRIMARY_REGIONS)
    for group in groups:
        members = [
            answer for answer in balanced_answers if answer.get("candidate_group_id") == group["id"]
        ]
        expected = len(group["questions"]) * expected_per_question * required_repetitions
        coverage = len(members) / expected if expected else 0.0
        extracted = sum(
            1 for answer in members if extracts.get(str(answer["pub_id"]), {}).get("status") == "ok"
        )
        extract_rate = extracted / len(members) if members else 0.0
        cited = sum(1 for answer in members if citations.get(str(answer["pub_id"])))
        citation_rate = cited / len(members) if members else 0.0
        complete = sum(
            len(str(answer.get("response_text") or "").strip()) >= 200 for answer in members
        )
        complete_rate = complete / len(members) if members else 0.0
        observed_pairs = {(answer.get("model"), answer.get("region")) for answer in members}
        dimension_rate = (
            len(observed_pairs) / expected_per_question if expected_per_question else 0.0
        )
        score = round(
            45 * coverage
            + 20 * extract_rate
            + 20 * citation_rate
            + 10 * complete_rate
            + 5 * dimension_rate,
            2,
        )
        rows.append(
            {
                **group,
                "expected_cells": expected,
                "observed_cells": len(members),
                "extract_ok": extracted,
                "answers_with_citation": cited,
                "complete_answers": complete,
                "coverage_rate": round(coverage, 4),
                "extract_rate": round(extract_rate, 4),
                "citation_answer_rate": round(citation_rate, 4),
                "complete_answer_rate": round(complete_rate, 4),
                "dimension_rate": round(dimension_rate, 4),
                "selection_score": score,
                "selection_basis": (
                    "45% cell coverage + 20% extraction coverage + 20% cited-answer coverage + "
                    "10% answer completeness + 5% platform/region breadth; no brand outcome input"
                ),
            }
        )
    ranked = sorted(rows, key=lambda row: (-float(row["selection_score"]), int(row["index"])))
    selected_ids = {row["id"] for row in ranked[:3]}
    for row in rows:
        row["selected_for_main_report"] = row["id"] in selected_ids
        row["selection_rank"] = next(
            index for index, ranked_row in enumerate(ranked, 1) if ranked_row["id"] == row["id"]
        )
    return rows


def _compact_special(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        key: value.get(key)
        for key in (
            "brand",
            "brand_input",
            "mentions",
            "appearance_rate",
            "avg_rank",
            "best_rank",
            "top_rates",
            "overall_rank",
            "by_query",
            "denominators",
        )
    }


def _visibility_snapshot(
    answers: list[dict[str, Any]],
    *,
    extracts: dict[str, dict[str, Any]],
    citations: dict[str, list[dict[str, Any]]],
    domain: str,
    target_brand: str,
    competitors: list[str],
) -> dict[str, Any]:
    rules = load_domain(domain)
    records: list[dict[str, Any]] = []
    source_records: list[dict[str, Any]] = []
    for answer in answers:
        extract = extracts.get(str(answer["pub_id"]))
        if (
            extract is not None
            and extract.get("status") == "ok"
            and isinstance(extract.get("brands"), list)
        ):
            records.append(adapter.answer_to_brand_record(answer, list(extract["brands"])))
        for citation in citations.get(str(answer["pub_id"]), []):
            source = adapter.citation_to_source_entry(citation)
            source.update(
                {
                    "thinking_mode": adapter.mode_label(str(answer.get("mode") or "")),
                    "ip": str(answer.get("region") or ""),
                }
            )
            source_records.append(source)
    result = metrics.analyze(
        records,
        source_records,
        rules=rules,
        target_brand=target_brand,
        competitors=competitors,
        top_ns=TOP_NS,
    )
    target = _compact_special(result.get("target_brand"))
    ranks = list((result.get("target_brand") or {}).get("ranks") or [])
    n_answers = len(answers)
    rank_distribution = {
        "top1": sum(rank == 1 for rank in ranks),
        "rank_2_3": sum(2 <= rank <= 3 for rank in ranks),
        "rank_4_5": sum(4 <= rank <= 5 for rank in ranks),
        "rank_6_plus": sum(rank >= 6 for rank in ranks),
        "not_mentioned": n_answers - len(ranks),
    }
    return {
        "answers": n_answers,
        "extract_ok": len(records),
        "answers_with_citation": sum(
            1 for answer in answers if citations.get(str(answer["pub_id"]))
        ),
        "citation_references": sum(
            len(citations.get(str(answer["pub_id"]), [])) for answer in answers
        ),
        "target": target,
        "competitors": [_compact_special(row) for row in result.get("competitors", [])],
        "rank_distribution": rank_distribution,
    }


def _load_answers(
    dsn: str,
    tenant_pub_id: str,
    project_pub_id: str,
    start_at: datetime,
    end_at: datetime,
) -> list[dict[str, Any]]:
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        volume = connection.execute(
            """
            SELECT count(*) AS answer_count,
                   coalesce(sum(octet_length(response_text)),0) AS response_bytes
            FROM analytics.answer
            WHERE tenant_pub_id=%s AND project_pub_id=%s
              AND eligible AND NOT degraded
              AND capture_time >= %s AND capture_time <= %s
            """,
            (tenant_pub_id, project_pub_id, start_at, end_at),
        ).fetchone()
        answer_count = int(volume["answer_count"] if volume else 0)
        response_bytes = int(volume["response_bytes"] if volume else 0)
        if answer_count > MAX_FORMAL_ANSWER_ROWS or response_bytes > MAX_FORMAL_RESPONSE_BYTES:
            raise ValueError("formal_answer_volume_exceeded")
        rows = connection.execute(
            """
            SELECT pub_id, query_text, response_text, model, region, mode, capture_time,
                   run_pub_id, config_version_pub_id, eligible, degraded
            FROM analytics.answer
            WHERE tenant_pub_id=%s AND project_pub_id=%s
              AND eligible AND NOT degraded
              AND capture_time >= %s AND capture_time <= %s
            ORDER BY capture_time, pub_id
            LIMIT %s
            """,
            (
                tenant_pub_id,
                project_pub_id,
                start_at,
                end_at,
                MAX_FORMAL_ANSWER_ROWS + 1,
            ),
        ).fetchall()
    output = [dict(row) for row in rows]
    if (
        len(output) > MAX_FORMAL_ANSWER_ROWS
        or sum(len(str(row.get("response_text") or "").encode()) for row in output)
        > MAX_FORMAL_RESPONSE_BYTES
    ):
        # Catch rows inserted between the aggregate and bounded read without ever
        # silently truncating a customer report.
        raise ValueError("formal_answer_volume_exceeded")
    return output


def _latest_snapshot(
    dsn: str,
    tenant_pub_id: str,
    project_pub_id: str,
    answers: list[dict[str, Any]],
) -> tuple[int | None, dict[str, Any]]:
    """Load the registered configuration, with a legacy descriptive fallback.

    Different platform runs may use separate config-version IDs while sharing the same
    evaluation questions.  Conversely, a newly-created one-question pilot can be the
    latest revision without explaining any completed answer.  We therefore score every
    project snapshot by the number of distinct primary answer cells whose question it
    contains; direct config-version usage and revision are deterministic tie-breakers.
    """

    config_counts = Counter(
        str(row.get("config_version_pub_id") or "")
        for row in answers
        if row.get("config_version_pub_id")
    )
    with _platform_tenant_connection(dsn, tenant_pub_id) as connection:
        rows = connection.execute(
            """
            SELECT v.pub_id, v.revision, v.snapshot_json, v.created_at
            FROM platform.monitoring_config_version v
            JOIN platform.monitoring_config m ON m.id = v.config_id
            JOIN platform.project p ON p.id = m.project_id
            WHERE p.pub_id = %s
            ORDER BY v.revision DESC, v.created_at DESC, v.pub_id DESC
            """,
            (project_pub_id,),
        ).fetchall()
    if not rows:
        return None, {}

    def snapshot_score(row: dict[str, Any]) -> tuple[int, int, int, str]:
        snapshot = _json_object(row["snapshot_json"])
        groups, _inferred = candidate_groups_from_snapshot(snapshot)
        questions = {
            question for group in groups for question in list(group.get("questions") or [])
        }
        matched_cells = {
            (str(answer["query_text"]), str(answer["model"]), str(answer["region"]))
            for answer in answers
            if str(answer.get("query_text") or "") in questions
            and answer.get("mode") == PRIMARY_MODE
            and answer.get("model") in PRIMARY_MODELS
            and answer.get("region") in PRIMARY_REGIONS
        }
        return (
            len(matched_cells),
            int(config_counts.get(str(row["pub_id"]), 0)),
            int(row["revision"]),
            str(row["created_at"]),
        )

    def registered_score(row: dict[str, Any]) -> tuple[int, int, int, int, str]:
        snapshot = _json_object(row["snapshot_json"])
        registration = snapshot.get("service1_scope_registration")
        registered = int(
            isinstance(registration, dict)
            and registration.get("schema_version") == "service1-scope-registration-v1"
        )
        matched, direct, revision, created = snapshot_score(row)
        return registered, matched, direct, revision, created

    selected = max((dict(row) for row in rows), key=registered_score)
    return int(selected["revision"]), _json_object(selected["snapshot_json"])


def _status_counts(
    dsn: str, tenant_pub_id: str, project_pub_id: str, start_at: datetime, end_at: datetime
) -> tuple[dict[str, int], dict[str, int]]:
    with _platform_tenant_connection(dsn, tenant_pub_id) as connection:
        status_rows = connection.execute(
            """
            SELECT j.judgment_status, count(*)::bigint AS n
            FROM platform.disparagement_judgment j
            JOIN platform.project p ON p.id = j.project_id
            WHERE p.pub_id = %s
              AND j.content_origin = 'collection'
              AND j.created_at >= %s AND j.created_at <= %s
            GROUP BY j.judgment_status
            """,
            (project_pub_id, start_at, end_at),
        ).fetchall()
        origin_rows = connection.execute(
            """
            SELECT j.content_origin, count(*)::bigint AS n
            FROM platform.disparagement_judgment j
            JOIN platform.project p ON p.id = j.project_id
            WHERE p.pub_id = %s
              AND j.content_origin = 'collection'
              AND j.created_at >= %s AND j.created_at <= %s
            GROUP BY j.content_origin
            """,
            (project_pub_id, start_at, end_at),
        ).fetchall()
    return (
        {str(row["judgment_status"]): int(row["n"]) for row in status_rows},
        {str(row["content_origin"]): int(row["n"]) for row in origin_rows},
    )


def _service2_snapshot(
    dsn: str,
    tenant_pub_id: str,
    project_pub_id: str,
    start_at: datetime,
    end_at: datetime,
) -> dict[str, Any]:
    judgments, truncated = fact_suggestions.fetch_disparagement_judgments(
        dsn, tenant_pub_id, project_pub_id, start_at, end_at
    )
    status_counts, origin_counts = _status_counts(
        dsn, tenant_pub_id, project_pub_id, start_at, end_at
    )
    flagged = [row for row in judgments if row.get("disparagement")]
    factchecks = fact_suggestions.fetch_disparagement_factchecks(
        dsn,
        tenant_pub_id,
        project_pub_id,
        [str(row["judgment_pub_id"]) for row in flagged],
    )
    verdict_counts = Counter(
        str((factchecks or {}).get(str(row["judgment_pub_id"]), {}).get("verdict") or "not_checked")
        for row in flagged
    )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in flagged:
        quote = re.sub(r"\s+", " ", str(row.get("evidence_quote") or "")).strip()
        grouped[quote or f"[{row['judgment_pub_id']}]"].append(row)
    cases: list[dict[str, Any]] = []
    for quote, members in grouped.items():
        checks = [(factchecks or {}).get(str(member["judgment_pub_id"])) for member in members]
        valid_checks = [check for check in checks if isinstance(check, dict)]
        cases.append(
            {
                "evidence_quote": quote,
                "occurrences": len(members),
                "platforms": sorted({str(member.get("platform") or "") for member in members}),
                "subject_types": sorted(
                    {str(member.get("subject_type") or "") for member in members}
                ),
                "target_brands": sorted(
                    {
                        str(member.get("target_brand") or "")
                        for member in members
                        if member.get("target_brand")
                    }
                ),
                "content_origins": sorted(
                    {str(member.get("content_origin") or "") for member in members}
                ),
                "judgment_pub_ids": [str(member["judgment_pub_id"]) for member in members],
                "answer_refs": [
                    str(member["subject_pub_id"])
                    for member in members
                    if member.get("subject_type") == "answer"
                ],
                "factcheck_verdicts": sorted(
                    {str(check.get("verdict") or "not_checked") for check in valid_checks}
                ),
                "factcheck_summaries": sorted(
                    {
                        str(check.get("summary") or "")
                        for check in valid_checks
                        if check.get("summary")
                    }
                ),
                "factcheck_sources": sorted(
                    {
                        str(check.get("source_url") or "")
                        for check in valid_checks
                        if check.get("source_url")
                    }
                ),
            }
        )
    cases.sort(key=lambda row: (-int(row["occurrences"]), str(row["evidence_quote"])))
    by_subject_type = Counter(str(row.get("subject_type") or "unknown") for row in judgments)
    flagged_by_subject_type = Counter(str(row.get("subject_type") or "unknown") for row in flagged)
    return {
        "judgments_ok": len(judgments),
        "status_counts": status_counts,
        "content_origin_counts": origin_counts,
        "flagged_signals": len(flagged),
        "unique_signal_patterns": len(cases),
        "flagged_signal_rate": round(len(flagged) / len(judgments), 4) if judgments else None,
        "by_subject_type": dict(by_subject_type),
        "flagged_by_subject_type": dict(flagged_by_subject_type),
        "factcheck_verdict_counts": dict(verdict_counts),
        "supported_cases": int(verdict_counts.get("supported", 0)),
        "refuted_cases": int(verdict_counts.get("refuted", 0)),
        "unverifiable_cases": int(verdict_counts.get("unverifiable", 0)),
        "truncated": truncated,
        "cases": cases,
    }


def _is_own_host(host: object, own_site_host: str | None) -> bool:
    if not isinstance(host, str) or not host or not own_site_host:
        return False
    candidate = host.lower()
    base = own_site_host.lower()
    apex = base[4:] if base.startswith("www.") else base
    return candidate in {base, apex, f"www.{apex}"} or candidate.endswith(f".{apex}")


def _site_breakdowns(
    answers: list[dict[str, Any]],
    citations: dict[str, list[dict[str, Any]]],
    own_site_host: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    host_answer_ids: dict[str, set[str]] = defaultdict(set)
    host_reference_counts: Counter[str] = Counter()
    for answer in answers:
        key = (str(answer["model"]), str(answer["region"]), str(answer["mode"]))
        bucket = buckets.setdefault(
            key,
            {
                "model": key[0],
                "model_label": MODEL_LABELS.get(key[0], key[0]),
                "region": key[1],
                "mode": key[2],
                "answers": 0,
                "answers_with_citation": 0,
                "answers_with_own_site_citation": 0,
            },
        )
        bucket["answers"] += 1
        answer_citations = citations.get(str(answer["pub_id"]), [])
        if answer_citations:
            bucket["answers_with_citation"] += 1
        if any(_is_own_host(row.get("host"), own_site_host) for row in answer_citations):
            bucket["answers_with_own_site_citation"] += 1
        for row in answer_citations:
            host = str(row.get("host") or "").lower()
            if not host:
                continue
            host_answer_ids[host].add(str(answer["pub_id"]))
            host_reference_counts[host] += 1
    breakdowns = []
    for row in sorted(
        buckets.values(), key=lambda value: (value["model"], value["mode"], value["region"])
    ):
        row["citation_coverage_rate"] = round(row["answers_with_citation"] / row["answers"], 4)
        row["own_site_answer_citation_rate"] = round(
            row["answers_with_own_site_citation"] / row["answers"], 4
        )
        breakdowns.append(row)
    hosts = [
        {
            "host": host,
            "answers": len(answer_ids),
            "references": int(host_reference_counts[host]),
            "is_own_site": _is_own_host(host, own_site_host),
        }
        for host, answer_ids in host_answer_ids.items()
    ]
    hosts.sort(
        key=lambda row: (
            -int(str(row["answers"])),
            -int(str(row["references"])),
            str(row["host"]),
        )
    )
    return breakdowns, hosts[:20]


def _service3_snapshot(
    dsn: str,
    tenant_pub_id: str,
    project_pub_id: str,
    start: date,
    end: date,
    answers: list[dict[str, Any]],
    citations: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    analytics = AnalyticsService(dsn=dsn)
    overview = analytics.source_audit_overview(
        tenant_pub_id=tenant_pub_id,
        project_pub_id=project_pub_id,
        start=start,
        end=end,
    )
    suggestions = analytics.site_audit_suggestions(
        tenant_pub_id=tenant_pub_id, project_pub_id=project_pub_id
    )
    breakdowns, hosts = _site_breakdowns(answers, citations, overview.get("own_site_host"))
    suggestion_evidence: dict[str, dict[str, Any]] = {}
    evidence_ids = [
        str(row["evidence_document_pub_id"])
        for row in suggestions.get("suggestions", [])
        if row.get("evidence_document_pub_id")
    ]
    if evidence_ids:
        with _platform_tenant_connection(dsn, tenant_pub_id) as connection:
            evidence_rows = connection.execute(
                """
                SELECT pub_id, url, fetched_at, extract_status
                FROM platform.source_document
                WHERE pub_id = ANY(%s::text[])
                """,
                (evidence_ids,),
            ).fetchall()
        suggestion_evidence = {str(row["pub_id"]): dict(row) for row in evidence_rows}
    suggestion_rows = []
    for row in suggestions.get("suggestions", []):
        evidence_id = row.get("evidence_document_pub_id")
        suggestion_rows.append(
            {
                **row,
                "evidence": suggestion_evidence.get(str(evidence_id)) if evidence_id else None,
            }
        )
    return {
        **overview,
        "platform_region_breakdown": breakdowns,
        "answer_level_hosts": hosts,
        "suggestion_batch_pub_id": suggestions.get("batch_pub_id"),
        "suggestion_generated_at": suggestions.get("generated_at"),
        "suggestion_model": suggestions.get("model"),
        "suggestions": suggestion_rows,
    }


def build_formal_review_facts(
    *,
    dsn: str,
    tenant_pub_id: str,
    project_pub_id: str,
    start: date,
    end: date,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build one auditable fact snapshot for service 1/2/3 review reports."""

    generated_at = generated_at or datetime.now(UTC)
    start_at = datetime.combine(start, time.min, tzinfo=UTC)
    end_at = datetime.combine(end, time.max, tzinfo=UTC)
    project = brandrank_service.fetch_project(dsn, tenant_pub_id, project_pub_id)
    if project is None:
        raise LookupError("project_not_found")
    domain = str(project.get("brandrank_domain") or "").strip()
    if not domain:
        raise ValueError("brandrank_domain_unset")
    target_brand = str((project.get("brand_names") or [""])[0])
    competitors = [str(value) for value in project.get("competitor_names", [])]

    answers = _load_answers(dsn, tenant_pub_id, project_pub_id, start_at, end_at)
    answer_ids = [str(row["pub_id"]) for row in answers]
    extracts = brandrank_service.fetch_brand_extracts(dsn, tenant_pub_id, answer_ids, domain)
    citations = brandrank_service.fetch_citations(dsn, tenant_pub_id, answer_ids)
    config_revision, snapshot = _latest_snapshot(dsn, tenant_pub_id, project_pub_id, answers)
    groups, inferred = candidate_groups_from_snapshot(snapshot)
    scope_registration = resolve_scope_registration(
        snapshot=snapshot,
        candidate_groups=groups,
        answers=answers,
    )
    balanced, duplicate_primary = balance_primary_answers(
        answers,
        candidate_groups=groups,
        repetitions_per_cell=QUOTATION_REPETITIONS_PER_CELL,
    )
    scored_groups = score_candidate_groups(
        groups,
        balanced,
        extracts,
        citations,
        required_repetitions=QUOTATION_REPETITIONS_PER_CELL,
    )
    selected_hashes = set(scope_registration["selected_group_hashes"])
    for group in scored_groups:
        group["selected_for_main_report"] = group["question_group_hash"] in selected_hashes
        group["selection_basis"] = scope_registration["selection_basis"]
    selected_ids = {str(row["id"]) for row in scored_groups if row["selected_for_main_report"]}
    selected_answers = [row for row in balanced if row.get("candidate_group_id") in selected_ids]

    overall = _visibility_snapshot(
        selected_answers,
        extracts=extracts,
        citations=citations,
        domain=domain,
        target_brand=target_brand,
        competitors=competitors,
    )
    by_model = {}
    for model in PRIMARY_MODELS:
        by_model[model] = _visibility_snapshot(
            [row for row in selected_answers if row.get("model") == model],
            extracts=extracts,
            citations=citations,
            domain=domain,
            target_brand=target_brand,
            competitors=competitors,
        )
    by_group = {}
    for group in scored_groups:
        by_group[str(group["id"])] = _visibility_snapshot(
            [row for row in balanced if row.get("candidate_group_id") == group["id"]],
            extracts=extracts,
            citations=citations,
            domain=domain,
            target_brand=target_brand,
            competitors=competitors,
        )

    selected_answer_ids = {str(row["pub_id"]) for row in selected_answers}
    balanced_answer_ids = {str(row["pub_id"]) for row in balanced}
    selected_cell_counts = Counter(
        (str(row["query_text"]), str(row["model"]), str(row["region"])) for row in selected_answers
    )
    selected_questions = [
        question
        for group in scored_groups
        if group["selected_for_main_report"]
        for question in group["questions"]
    ]
    expected_selected_cells = [
        (question, model, region)
        for question in selected_questions
        for model in PRIMARY_MODELS
        for region in PRIMARY_REGIONS
    ]
    repetition_counts = [selected_cell_counts.get(cell, 0) for cell in expected_selected_cells]
    current_repetitions = min(repetition_counts, default=0)
    maximum_repetitions = max(repetition_counts, default=0)
    expected_formal_answers = len(expected_selected_cells) * QUOTATION_REPETITIONS_PER_CELL
    formal_sample_complete = bool(expected_selected_cells) and all(
        count == QUOTATION_REPETITIONS_PER_CELL for count in repetition_counts
    )
    repeat_numbers, repeat_independence_reasons = assign_repeats(selected_answers)
    service1 = {
        "config_revision": config_revision,
        "scope_registration": scope_registration,
        "candidate_grouping_inferred": inferred,
        "candidate_groups": scored_groups,
        "primary_mode": PRIMARY_MODE,
        "primary_models": list(PRIMARY_MODELS),
        "primary_regions": list(PRIMARY_REGIONS),
        "all_eligible_answers": len(answers),
        "balanced_answers_all_groups": len(balanced),
        "balanced_answers_selected_groups": len(selected_answers),
        "primary_duplicate_observations_excluded": len(duplicate_primary),
        "supplementary_or_nonprimary_answers": len(
            [row for row in answers if str(row["pub_id"]) not in balanced_answer_ids]
        ),
        "current_repetitions_per_cell": current_repetitions,
        "maximum_repetitions_per_cell": maximum_repetitions,
        "quotation_required_repetitions_per_cell": QUOTATION_REPETITIONS_PER_CELL,
        "expected_formal_answers": expected_formal_answers,
        "formal_sample_complete": formal_sample_complete,
        "repeat_independence_ready": not repeat_independence_reasons,
        "repeat_independence_reasons": repeat_independence_reasons,
        "formal_sample_gap": (
            None
            if formal_sample_complete
            else "The selected primary matrix is incomplete; formal delivery requires "
            f"{QUOTATION_REPETITIONS_PER_CELL} independent repetitions in every cell."
        ),
        "overall": overall,
        "by_model": by_model,
        "by_group": by_group,
        "answer_registry": [
            {
                "answer_pub_id": str(row["pub_id"]),
                "query": str(row["query_text"]),
                "model": str(row["model"]),
                "region": str(row["region"]),
                "mode": str(row["mode"]),
                "capture_time": row["capture_time"],
                "run_pub_id": str(row.get("run_pub_id") or ""),
                "config_version_pub_id": str(row.get("config_version_pub_id") or ""),
                "repeat_no": repeat_numbers.get(str(row["pub_id"])),
                "candidate_group_id": row.get("candidate_group_id"),
                "selected_for_main_report": str(row["pub_id"]) in selected_answer_ids,
                "extract_status": extracts.get(str(row["pub_id"]), {}).get("status"),
                "citation_count": len(citations.get(str(row["pub_id"]), [])),
            }
            for row in balanced
        ],
    }

    service2 = _service2_snapshot(dsn, tenant_pub_id, project_pub_id, start_at, end_at)
    service3 = _service3_snapshot(
        dsn,
        tenant_pub_id,
        project_pub_id,
        start,
        end,
        answers,
        citations,
    )
    captured = [row["capture_time"] for row in answers]
    return {
        "schema_version": "formal-review-facts-v1",
        "document_status": "internal_review",
        "project_pub_id": project_pub_id,
        "project_name": str(project.get("name") or ""),
        "target_brand": target_brand,
        "competitors": competitors,
        "domain": domain,
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "first_capture_at": min(captured) if captured else None,
            "last_capture_at": max(captured) if captured else None,
        },
        "generated_at": generated_at,
        "limitations": [
            "报告结论只适用于披露的问题文本、平台、地域标签与采集窗口。",
            "地域标签只有在账号、浏览器实例和出口审计台账齐全时才能作为地域独立观测。",
            "服务 1 的正式签发要求问题组在首次采样前完成冻结和确认。",
            "回答列出的引用未经服务 2 事实核查，不代表引用支持回答中的每项陈述。",
        ],
        "service1": service1,
        "service2": service2,
        "service3": service3,
    }
