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
              AND (batch.window_start AT TIME ZONE 'Asia/Shanghai')::date=%s
              AND (batch.window_end AT TIME ZONE 'Asia/Shanghai')::date=%s
            ORDER BY batch.frozen_at DESC,manifest.revision DESC,manifest.pub_id DESC
            LIMIT 1
            """,
            (project_pub_id, start, end),
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
            "manual_evidence_required",
            "blocked",
            "gone",
            "unobservable",
            "failed",
        )
        if int(processing.get(state) or 0) > 0
    }
    reasons = []
    if not coverage.get("coverage_complete"):
        reasons.append("all_u_occurrence_materialization_incomplete")
    if incomplete_states:
        reasons.append("source_or_evidence_coverage_incomplete")
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
        "cases": list(facts.get("cases") or []),
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
