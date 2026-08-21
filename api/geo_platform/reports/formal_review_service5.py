"""Publication evidence for quotation service 5.

The comparison builder supplies a frozen before/after measurement matrix.  This
module adds the missing intervention ledger from an explicitly bound SOP project so
the new publishing service is never represented by the legacy plan-only report.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from psycopg.rows import dict_row

from geo_platform.tenancy.psycopg import tenant_connection


def _brand_aliases(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = {}
    if not isinstance(value, Mapping) or not isinstance(value.get("aliases"), list):
        return ()
    return tuple(
        dict.fromkeys(
            str(alias).strip()
            for alias in value["aliases"]
            if isinstance(alias, str) and alias.strip()
        )
    )


def build_publishing_evidence(
    *,
    dsn: str,
    tenant_pub_id: str,
    sop_project_pub_id: str,
    before_end: date,
    after_start: date,
    window_start: date,
    window_end: date,
    generated_at: datetime,
) -> dict[str, Any]:
    """Return only customer-readable publication records and explicit gate facts."""

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
            raise ValueError("service5_sop_project_not_found")
        rows = connection.execute(
            """
            SELECT publication.platform,publication.title,publication.public_url,
                   publication.published_at,publication.public_checked_at,
                   publication.public_http_status,publication.status,
                   publication.body_sha256,
                   article_version.version_no,article_version.publication_ready,
                   (publication.evidence <> '{}'::jsonb) AS has_publication_evidence,
                   EXISTS (
                     SELECT 1 FROM posting.batch batch
                     WHERE batch.tenant_pub_id=publication.tenant_pub_id
                       AND batch.sop_project_pub_id=publication.project_pub_id
                       AND batch.article_version_pub_id=publication.article_version_pub_id
                       AND batch.approval_state='approved'
                   ) AS has_approved_distribution,
                   EXISTS (
                     SELECT 1
                     FROM posting.attribution attribution
                     JOIN posting.batch batch
                       ON batch.tenant_pub_id=attribution.tenant_pub_id
                      AND batch.pub_id=attribution.batch_pub_id
                     WHERE attribution.tenant_pub_id=publication.tenant_pub_id
                       AND batch.sop_project_pub_id=publication.project_pub_id
                       AND batch.article_version_pub_id=publication.article_version_pub_id
                       AND attribution.sop_publication_pub_id=publication.pub_id
                       AND attribution.public_url=publication.public_url
                       AND attribution.relation_type='published_as'
                       AND attribution.public_url <> ''
                   ) AS has_publication_attribution
            FROM sop.publication publication
            JOIN sop.article_version article_version
              ON article_version.tenant_pub_id=publication.tenant_pub_id
             AND article_version.pub_id=publication.article_version_pub_id
            WHERE publication.tenant_pub_id=%s
              AND publication.project_pub_id=%s
              AND publication.status='public'
              AND publication.public_url <> ''
              AND publication.published_at::date BETWEEN %s AND %s
            ORDER BY publication.published_at,publication.pub_id
            """,
            (tenant_pub_id, sop_project_pub_id, window_start, window_end),
        ).fetchall()

    publications: list[dict[str, Any]] = []
    for row in rows:
        published_at = row["published_at"]
        published_date = published_at.date() if published_at is not None else None
        # Windows are day-granular while publication timestamps are not paired with
        # the exact first after-sample timestamp.  A publication on ``after_start``
        # is therefore temporally ambiguous and must fail closed.
        between_arms = bool(
            published_date is not None and before_end < published_date < after_start
        )
        publications.append(
            {
                "title": str(row["title"] or ""),
                "platform": str(row["platform"] or ""),
                "public_url": str(row["public_url"] or ""),
                "published_at": published_at,
                "public_checked_at": row["public_checked_at"],
                "public_http_status": row["public_http_status"],
                "status": str(row["status"] or ""),
                "body_sha256": str(row["body_sha256"] or ""),
                "article_version": int(row["version_no"]),
                "article_finalized": bool(row["publication_ready"]),
                "has_publication_evidence": bool(row["has_publication_evidence"]),
                "has_approved_distribution": bool(row["has_approved_distribution"]),
                "has_publication_attribution": bool(row["has_publication_attribution"]),
                "between_measurement_arms": between_arms,
            }
        )
    interventions = [row for row in publications if row["between_measurement_arms"]]
    evidence_complete = bool(interventions) and all(
        row["article_finalized"]
        and row["public_url"]
        and row["has_approved_distribution"]
        and (row["has_publication_evidence"] or row["has_publication_attribution"])
        for row in interventions
    )
    approval_complete = bool(interventions) and all(
        row["has_approved_distribution"] for row in interventions
    )
    reasons = [
        *([] if publications else ["public_content_missing"]),
        *([] if interventions else ["publication_not_between_measurement_arms"]),
        *([] if approval_complete else ["approved_distribution_missing"]),
        *([] if evidence_complete else ["publication_evidence_incomplete"]),
    ]
    return {
        "schema_version": "formal-publishing-evidence-v1",
        "service_code": "content_publishing_pilot",
        "project_name": str(project["name"] or ""),
        "target_brand": str(project["brand_standard_name"] or ""),
        "brand_aliases": _brand_aliases(project["brand_profile"]),
        "generated_at": generated_at,
        "publication_window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "required_intervention_after": before_end.isoformat(),
            "required_intervention_before": after_start.isoformat(),
        },
        "publications": publications,
        "summary": {
            "publications": len(publications),
            "between_measurement_arms": len(interventions),
            "evidence_complete": evidence_complete,
        },
        "evidence_gate": {
            "status": "ready" if evidence_complete else "insufficient",
            "reasons": list(dict.fromkeys(reasons)),
        },
        "causal_boundary": (
            "发布台账可证明内容在两次测量之间公开；前后指标变化仍是描述性关联，"
            "不能单凭时间先后证明由发帖导致。"
        ),
    }


__all__ = ["build_publishing_evidence"]
