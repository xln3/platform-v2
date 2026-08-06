from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

from domain.evidence.provenance import RedactedProvenance
from domain.reporting.artifacts import render_xlsx
from domain.reporting.freeze import freeze_report
from geo_platform.analytics.service import AnalyticsService
from geo_platform.evidence.service import EvidenceService
from geo_platform.tenancy.ids import new_pub_id
from geo_platform.tenancy.psycopg import tenant_connection


class ExportService:
    def __init__(
        self,
        *,
        dsn: str,
        analytics: AnalyticsService,
        evidence: EvidenceService,
    ) -> None:
        self.dsn = dsn
        self.analytics = analytics
        self.evidence = evidence

    def export_metrics_xlsx(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        start: date,
        end: date,
        dimensions: Mapping[str, str],
        created_by_pub_id: str,
        provenance: RedactedProvenance,
    ) -> dict[str, Any]:
        rows = self.analytics.aggregate(
            tenant_pub_id=tenant_pub_id,
            project_pub_id=project_pub_id,
            start=start,
            end=end,
            dimensions=dict(dimensions),
        )
        if not rows:
            raise LookupError("no metric facts match the export window")
        metric_versions = {str(row["metric_version"]) for row in rows}
        scorer_versions = {str(row["scorer_version"]) for row in rows}
        if len(metric_versions) != 1 or len(scorer_versions) != 1:
            raise ValueError("one export cannot mix metric or scorer versions")
        frozen = freeze_report(
            window_start=datetime.combine(start, datetime.min.time(), tzinfo=UTC),
            window_end=datetime.combine(end, datetime.max.time(), tzinfo=UTC),
            filters={"project_pub_id": project_pub_id, "dimensions": dict(dimensions)},
            metric_version=next(iter(metric_versions)),
            scorer_version=next(iter(scorer_versions)),
            fact_rows=rows,
        )
        export_pub_id = new_pub_id("exp")
        requested_evidence_pub_id = new_pub_id("evd")
        with tenant_connection(self.dsn, tenant_pub_id) as connection:
            stored = self.evidence.capture(
                evidence_pub_id=requested_evidence_pub_id,
                tenant_pub_id=tenant_pub_id,
                project_pub_id=project_pub_id,
                kind="analytics_export_xlsx",
                payload=render_xlsx(rows),
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                source_url=None,
                provenance=provenance,
                db_connection=connection,
            )
            evidence_pub_id = stored.metadata_pub_id or requested_evidence_pub_id
            connection.execute(
                """
                INSERT INTO reporting.data_export
                  (pub_id,tenant_pub_id,project_pub_id,export_type,window_start,window_end,
                   filters,filter_hash,metric_version,scorer_version,fact_snapshot_hash,
                   evidence_pub_id,created_by_pub_id)
                VALUES (%s,%s,%s,'metric_xlsx',%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    export_pub_id,
                    tenant_pub_id,
                    project_pub_id,
                    start,
                    end,
                    json.dumps(dict(dimensions)),
                    frozen.filter_hash,
                    frozen.metric_version,
                    frozen.scorer_version,
                    frozen.fact_snapshot_hash,
                    evidence_pub_id,
                    created_by_pub_id,
                ),
            )
        return {
            "export_pub_id": export_pub_id,
            "evidence_pub_id": evidence_pub_id,
            "format": "xlsx",
            "row_count": len(rows),
            "filter_hash": frozen.filter_hash,
            "fact_snapshot_hash": frozen.fact_snapshot_hash,
            "metric_version": frozen.metric_version,
            "scorer_version": frozen.scorer_version,
        }
