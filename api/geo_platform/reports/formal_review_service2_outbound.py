"""Facts for quotation service 2: customer-owned content disparagement audit.

The legacy formal Service 2 evaluates AI answers and cited public pages.  It must
not be reused for the new outbound service: this builder reads only finalized SOP
article versions bound to an explicit tenant-scoped SOP project and only judgments
whose ``content_origin`` is ``own_content``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from psycopg.rows import dict_row

from domain.scoring.disparagement import dedupe_windows, extract_windows
from geo_platform.tenancy.psycopg import tenant_connection


def _profile_names(value: object, key: str) -> tuple[str, ...]:
    raw = value
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}
    if not isinstance(raw, Mapping):
        return ()
    candidates = raw.get(key)
    if not isinstance(candidates, list):
        return ()
    output: list[str] = []
    for candidate in candidates:
        cleaned = str(candidate).strip() if isinstance(candidate, str) else ""
        if cleaned and cleaned not in output:
            output.append(cleaned)
    return tuple(output)


def build_outbound_disparagement_facts(
    *,
    dsn: str,
    tenant_pub_id: str,
    sop_project_pub_id: str,
    start: date,
    end: date,
    generated_at: datetime,
) -> dict[str, Any]:
    """Build a bounded, attribution-safe view of finalized customer content."""

    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        project = connection.execute(
            """
            SELECT pub_id,name,brand_standard_name,brand_profile
            FROM sop.project
            WHERE tenant_pub_id=%s AND pub_id=%s
            """,
            (tenant_pub_id, sop_project_pub_id),
        ).fetchone()
        if project is None:
            raise ValueError("service2_sop_project_not_found")
        rows = connection.execute(
            """
            SELECT av.pub_id AS article_version_pub_id,av.version_no,av.title,av.body,
                   av.body_sha256,av.publication_ready,av.created_at AS version_created_at,
                   judgment.pub_id AS judgment_pub_id,judgment.subject_brand,
                   judgment.target_brand,judgment.attitude,judgment.disparagement,
                   judgment.evidence_quote,judgment.confidence,judgment.method,
                   judgment.model,judgment.prompt_version,judgment.judgment_status,
                   judgment.window_hash,
                   judgment.created_at AS judgment_created_at,
                   factcheck.verdict AS factcheck_verdict,
                   factcheck.summary AS factcheck_summary,
                   factcheck.source_url AS factcheck_source_url
            FROM sop.article_version av
            JOIN sop.article article
              ON article.tenant_pub_id=av.tenant_pub_id
             AND article.pub_id=av.article_pub_id
            LEFT JOIN platform.disparagement_judgment judgment
              ON judgment.tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid
             AND judgment.subject_pub_id=av.pub_id
             AND judgment.subject_type='own_content'
             AND judgment.content_origin='own_content'
             AND judgment.created_at::date BETWEEN %s AND %s
            LEFT JOIN platform.disparagement_factcheck factcheck
              ON factcheck.judgment_pub_id=judgment.pub_id
            WHERE av.tenant_pub_id=%s
              AND article.project_pub_id=%s
              AND av.publication_ready IS TRUE
              AND av.created_at::date BETWEEN %s AND %s
            ORDER BY av.created_at,av.pub_id,judgment.created_at,judgment.pub_id
            """,
            (start, end, tenant_pub_id, sop_project_pub_id, start, end),
        ).fetchall()
        publication_rows = connection.execute(
            """
            SELECT publication.article_version_pub_id,publication.platform,
                   publication.public_url,publication.published_at,publication.status
            FROM sop.publication publication
            WHERE publication.tenant_pub_id=%s
              AND publication.project_pub_id=%s
              AND publication.status IN ('published','public')
              AND publication.public_url <> ''
              AND publication.published_at::date BETWEEN %s AND %s
            ORDER BY publication.published_at,publication.pub_id
            """,
            (tenant_pub_id, sop_project_pub_id, start, end),
        ).fetchall()

    aliases = _profile_names(project["brand_profile"], "aliases")
    competitors = _profile_names(project["brand_profile"], "competitors")
    competitor_keys = {value.casefold() for value in competitors}
    publications_by_version: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for publication in publication_rows:
        publications_by_version[str(publication["article_version_pub_id"])].append(
            {
                "platform": str(publication["platform"] or ""),
                "public_url": str(publication["public_url"] or ""),
                "published_at": publication["published_at"],
                "status": str(publication["status"] or ""),
            }
        )

    target_brand = str(project["brand_standard_name"] or "").strip()
    versions: dict[str, dict[str, Any]] = {}
    expected_by_version: dict[str, set[tuple[str, str]]] = {}
    completed_by_version: dict[str, set[tuple[str, str]]] = defaultdict(set)
    validation_failures = 0
    cases: list[dict[str, Any]] = []
    for row in rows:
        version_key = str(row["article_version_pub_id"])
        version = versions.get(version_key)
        if version is None:
            expected_windows = (
                dedupe_windows(
                    extract_windows(
                        subject_type="own_content",
                        subject_pub_id=version_key,
                        text=str(row["body"] or ""),
                        brand=target_brand or None,
                        competitors=competitors,
                        platform="own_content",
                    )
                )
                if competitors
                else []
            )
            expected_by_version[version_key] = {
                (window.window_hash, window.target_brand.strip().casefold())
                for window in expected_windows
            }
            version = {
                "title": str(row["title"] or ""),
                "version_no": int(row["version_no"]),
                "body_sha256": str(row["body_sha256"]),
                "finalized": bool(row["publication_ready"]),
                "created_at": row["version_created_at"],
                "publications": publications_by_version.get(version_key, []),
                "judgments": 0,
                "expected_windows": len(expected_by_version[version_key]),
                "completed_windows": 0,
                "judgment_coverage_complete": False,
                "validation_failures": 0,
                "unexpected_judgments": 0,
            }
            versions[version_key] = version
        if row["judgment_pub_id"] is None:
            continue
        target = str(row["target_brand"] or "").strip()
        judgment_key = (str(row["window_hash"] or ""), target.casefold())
        if judgment_key not in expected_by_version[version_key]:
            version["unexpected_judgments"] = int(version["unexpected_judgments"]) + 1
            continue
        if str(row["judgment_status"] or "") != "ok":
            validation_failures += 1
            version["validation_failures"] = int(version["validation_failures"]) + 1
            continue
        version["judgments"] = int(version["judgments"]) + 1
        completed_by_version[version_key].add(judgment_key)
        if not row["disparagement"] or (
            competitor_keys and target.casefold() not in competitor_keys
        ):
            continue
        cases.append(
            {
                "case_id": f"OUT-{len(cases) + 1:03d}",
                "article_title": version["title"],
                "article_version": version["version_no"],
                "body_sha256": version["body_sha256"],
                "ownership_evidence": "客户 SOP 项目内已定稿版本（publication_ready=true）",
                "publications": version["publications"],
                "subject_brand": str(row["subject_brand"] or "").strip(),
                "target_brand": target,
                "attitude": str(row["attitude"] or ""),
                "evidence_quote": str(row["evidence_quote"] or "").strip(),
                "confidence": float(row["confidence"] or 0),
                "method": str(row["method"] or ""),
                "model": str(row["model"] or ""),
                "prompt_version": str(row["prompt_version"] or ""),
                "judged_at": row["judgment_created_at"],
                "factcheck": (
                    {
                        "verdict": str(row["factcheck_verdict"]),
                        "summary": str(row["factcheck_summary"] or ""),
                        "source_url": str(row["factcheck_source_url"] or ""),
                    }
                    if row["factcheck_verdict"] is not None
                    else None
                ),
            }
        )

    for version_key, version in versions.items():
        expected_count = len(expected_by_version[version_key])
        completed_count = len(completed_by_version[version_key])
        version["completed_windows"] = completed_count
        version["judgment_coverage_complete"] = (
            expected_count > 0 and completed_count == expected_count
        )
    version_rows = list(versions.values())
    judged_versions = sum(bool(row["judgment_coverage_complete"]) for row in version_rows)
    ready_versions = len(version_rows)
    coverage_complete = ready_versions > 0 and judged_versions == ready_versions
    expected_windows_total = sum(int(row["expected_windows"]) for row in version_rows)
    completed_windows_total = sum(int(row["completed_windows"]) for row in version_rows)
    return {
        "schema_version": "formal-outbound-disparagement-v1",
        "service_code": "outbound_disparagement_audit",
        "project_name": str(project["name"] or ""),
        "target_brand": target_brand,
        "brand_aliases": aliases,
        "competitors": competitors,
        "generated_at": generated_at,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "scope": {
            "finalized_content_versions": ready_versions,
            "judged_content_versions": judged_versions,
            "judgment_coverage_complete": coverage_complete,
            "expected_windows": expected_windows_total,
            "completed_windows": completed_windows_total,
            "validation_failures": validation_failures,
            "risk_cases": len(cases),
        },
        "content_versions": version_rows,
        "cases": cases,
        "evidence_gate": {
            "status": "ready" if coverage_complete and not validation_failures else "insufficient",
            "reasons": [
                *([] if ready_versions else ["finalized_customer_content_missing"]),
                *([] if competitors else ["configured_competitor_scope_missing"]),
                *(
                    []
                    if ready_versions and expected_windows_total > 0
                    else ["own_content_expected_windows_unproven"]
                ),
                *([] if coverage_complete else ["own_content_judgment_coverage_incomplete"]),
                *([] if not validation_failures else ["own_content_judgment_validation_failed"]),
            ],
        },
        "limitations": [
            "只核查显式绑定 SOP 项目中已定稿的己方内容，不把 AI 回答或第三方网页冒充己方稿件。",
            "拉踩表达命中不自动等同于事实虚假；事实核查缺失时仅作为整改线索。",
            "未进入本次时间窗、未定稿或判定未完成的内容不属于本报告的无风险结论范围。",
        ],
    }


__all__ = ["build_outbound_disparagement_facts"]
