"""Read-only adapter from frozen Service 2 corpus facts to formal-report facts."""

from __future__ import annotations

import json
from datetime import date, datetime
from hashlib import sha256
from typing import Any

from psycopg.rows import dict_row

from geo_platform.tenancy.psycopg import tenant_connection


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


def build_service2_source_corpus_facts(
    *,
    dsn: str,
    tenant_pub_id: str,
    project_pub_id: str,
    start: date,
    end: date,
    generated_at: datetime,
    manifest_pub_id: str,
    manifest_hash: str,
) -> dict[str, Any]:
    """Load one immutable v2 manifest; never fetch a page or call a model."""

    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        row = connection.execute(
            """
            SELECT manifest.pub_id AS manifest_pub_id,manifest.revision,
                   manifest.manifest_hash,manifest.facts,manifest.case_count,
                   manifest.evidence_reference_count,manifest.created_at,
                   batch.pub_id AS batch_pub_id,batch.corpus_policy_version,
                   batch.judgment_policy_version,project.name AS project_name,
                   COALESCE(
                     (SELECT brand.name FROM platform.brand brand
                      WHERE brand.tenant_id=batch.tenant_id
                        AND brand.project_id=batch.project_id
                      ORDER BY brand.created_at,brand.pub_id LIMIT 1),
                     project.name
                   ) AS target_brand
            FROM platform.service2_fact_manifest manifest
            JOIN platform.service2_corpus_batch batch ON batch.id=manifest.batch_id
            JOIN platform.project project ON project.id=batch.project_id
            WHERE project.pub_id=%s AND batch.status='frozen'
              AND manifest.pub_id=%s AND manifest.manifest_hash=%s
              AND (batch.window_start AT TIME ZONE 'Asia/Shanghai')::date=%s
              AND (batch.window_end AT TIME ZONE 'Asia/Shanghai')::date=%s
            """,
            (project_pub_id, manifest_pub_id, manifest_hash, start, end),
        ).fetchone()
    if row is None:
        raise ValueError("service2_frozen_manifest_required")
    facts = row["facts"]
    if not isinstance(facts, dict) or facts.get("schema_version") != (
        "formal-service2-source-corpus-v2"
    ):
        raise ValueError("service2_frozen_manifest_schema_invalid")
    if _canonical_hash(facts) != row["manifest_hash"]:
        raise ValueError("service2_frozen_manifest_integrity_failed")
    raw_coverage = facts.get("coverage")
    coverage: dict[str, Any] = raw_coverage if isinstance(raw_coverage, dict) else {}
    raw_processing = coverage.get("processing_states")
    processing: dict[str, Any] = raw_processing if isinstance(raw_processing, dict) else {}
    incomplete_states = {
        state: int(processing.get(state) or 0)
        for state in (
            "queued",
            "fetching",
            "retry_wait",
            "partial",
            "manual_evidence_required",
            "blocked",
            "gone",
            "unobservable",
            "failed",
            "cancelled",
        )
        if int(processing.get(state) or 0) > 0
    }
    for state, count in processing.items():
        if state != "processed" and int(count or 0) > 0:
            incomplete_states[str(state)] = int(count)
    reasons = []
    if not coverage.get("coverage_complete"):
        reasons.append("all_u_occurrence_materialization_incomplete")
    if not coverage.get("query_outcomes_complete"):
        reasons.append("query_outcomes_incomplete")
    if not coverage.get("query_coverage_complete"):
        reasons.append("failed_queries_require_retry")
    if incomplete_states:
        reasons.append("source_or_evidence_coverage_incomplete")
    if not incomplete_states and int(processing.get("processed") or 0) != int(
        coverage.get("materialized_items") or 0
    ):
        reasons.append("processing_coverage_incomplete")
    raw_cases = list(facts.get("cases") or [])
    visual_ids = {
        str(case.get("visual_evidence_pub_id"))
        for case in raw_cases
        if isinstance(case, dict) and case.get("visual_evidence_pub_id")
    }
    visual_page_ids = {
        str(case.get("visual_page_snapshot_evidence_pub_id"))
        for case in raw_cases
        if isinstance(case, dict) and case.get("visual_page_snapshot_evidence_pub_id")
    }
    fact_evidence = [
        evidence
        for case in raw_cases
        if isinstance(case, dict)
        for evidence in (case.get("factcheck_evidence") or [])
        if isinstance(evidence, dict) and evidence.get("evidence_pub_id")
    ]
    evidence_ids = (
        visual_ids | visual_page_ids | {str(row["evidence_pub_id"]) for row in fact_evidence}
    )
    asset_rows = []
    if evidence_ids:
        with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
            asset_rows = connection.execute(
                """
                SELECT asset.pub_id,asset.object_key,asset.sha256,asset.mime_type,asset.kind
                FROM evidence.evidence_asset asset
                WHERE asset.tenant_pub_id=%s AND asset.project_pub_id=%s
                  AND asset.pub_id=ANY(%s::text[]) AND asset.deleted_at IS NULL
                  AND asset.object_key IS NOT NULL AND asset.sha256 ~ '^[0-9a-f]{64}$'
                """,
                (tenant_pub_id, project_pub_id, sorted(evidence_ids)),
            ).fetchall()
    assets = {str(asset["pub_id"]): dict(asset) for asset in asset_rows}
    if set(assets) != evidence_ids:
        raise ValueError("service2_frozen_evidence_asset_missing")
    for evidence in fact_evidence:
        asset = assets[str(evidence["evidence_pub_id"])]
        if (
            evidence.get("verification_status") != "verified"
            or evidence.get("content_sha256") != asset["sha256"]
            or asset["kind"] != "service2_factcheck_source"
        ):
            raise ValueError("service2_factcheck_evidence_integrity_failed")
    cases: list[dict[str, Any]] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise ValueError("service2_frozen_case_invalid")
        case = dict(raw_case)
        visual_id = str(case.get("visual_evidence_pub_id") or "")
        page_id = str(case.get("visual_page_snapshot_evidence_pub_id") or "")
        if not visual_id or not page_id:
            raise ValueError("service2_visual_evidence_asset_missing")
        asset = assets[visual_id]
        page_asset = assets[page_id]
        if (
            case.get("visual_evidence_sha256") != asset["sha256"]
            or asset["kind"] != "service2_exact_quote_screenshot"
        ):
            raise ValueError("service2_visual_evidence_integrity_failed")
        if (
            case.get("visual_page_snapshot_sha256") != page_asset["sha256"]
            or page_asset["kind"] != "service2_visual_page_snapshot"
        ):
            raise ValueError("service2_visual_page_snapshot_integrity_failed")
        case["visual_screenshot"] = {
            "pub_id": visual_id,
            "object_key": str(asset["object_key"]),
            "sha256": str(asset["sha256"]),
            "mime_type": str(asset["mime_type"]),
        }
        case["visual_page_snapshot"] = {
            "pub_id": page_id,
            "object_key": str(page_asset["object_key"]),
            "sha256": str(page_asset["sha256"]),
            "mime_type": str(page_asset["mime_type"]),
        }
        cases.append(case)
    return {
        "schema_version": "formal-service2-source-corpus-v2",
        "service_code": "outbound_disparagement_audit",
        "project_name": str(row["project_name"] or ""),
        "target_brand": str(row["target_brand"] or row["project_name"] or ""),
        "generated_at": generated_at,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "manifest": {
            "batch_pub_id": str(row["batch_pub_id"]),
            "manifest_pub_id": str(row["manifest_pub_id"]),
            "revision": int(row["revision"]),
            "manifest_hash": str(row["manifest_hash"]),
            "frozen_at": row["created_at"],
            "corpus_policy_version": str(row["corpus_policy_version"]),
            "judgment_policy_version": str(row["judgment_policy_version"]),
        },
        "scope": facts.get("scope") or {},
        "coverage": coverage,
        "cases": cases,
        "evidence_pub_ids": list(facts.get("evidence_pub_ids") or []),
        "evidence_urls": list(facts.get("evidence_urls") or []),
        "evidence_gate": {
            "status": "ready" if not reasons else "insufficient",
            "reasons": reasons,
            "incomplete_processing_states": incomplete_states,
        },
        "rendering_boundary": "frozen_facts_only_no_network_or_model",
        "limitations": [
            "入池总体为冻结运行与时间窗内的全部 U occurrence；URL 抓取复用不缩小分母。",
            "L1 是事实性负面核查信息，不计为拉踩；B 暴露账不冒充逐字拉踩言论。",
            "publisher/commissioner 归属与文本关系分列；"
            "unknown 不支持竞品委托、水军或组织攻击归因。",
            "只有逐字 quote、页面 hash、视觉证据、事实核查和人工审核均通过的 finding 才进入案例。",
        ],
    }


__all__ = ["build_service2_source_corpus_facts"]
