from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

import psycopg
from psycopg.rows import dict_row

from domain.evidence.provenance import RedactedProvenance
from domain.reporting.artifacts import render_docx, render_html, render_pdf, render_xlsx
from domain.reporting.diff import ReportVersionDiff, compare_report_versions
from domain.reporting.freeze import freeze_report
from domain.reporting.policy import assert_customer_report_safe
from geo_platform.evidence.service import EvidenceService
from geo_platform.tenancy.ids import new_pub_id
from geo_platform.tenancy.psycopg import tenant_connection


class ReportRevisionIdempotencyConflict(ValueError):
    pass


class ReportRevisionIncomplete(RuntimeError):
    pass


class ReportService:
    def __init__(self, *, dsn: str, evidence: EvidenceService) -> None:
        self.dsn = dsn
        self.evidence = evidence

    def produce(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        title: str,
        window_start: datetime,
        window_end: datetime,
        filters: Mapping[str, Any],
        metric_version: str,
        scorer_version: str,
        fact_rows: Sequence[Mapping[str, Any]],
        sections: Sequence[Mapping[str, object]],
        created_by_pub_id: str,
        provenance: RedactedProvenance,
        workflow_operation_id: str | None = None,
    ) -> dict[str, Any]:
        assert_customer_report_safe([*fact_rows, *sections])
        frozen = freeze_report(
            window_start=window_start,
            window_end=window_end,
            filters=filters,
            metric_version=metric_version,
            scorer_version=scorer_version,
            fact_rows=fact_rows,
        )
        report_pub_id = new_pub_id("rpt")
        version_pub_id = new_pub_id("rptv")
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            inserted = connection.execute(
                """
                INSERT INTO reporting.report
                  (pub_id,tenant_pub_id,project_pub_id,title,state,workflow_operation_id)
                VALUES (%s,%s,%s,%s,'review',%s)
                ON CONFLICT (tenant_pub_id,workflow_operation_id)
                  WHERE workflow_operation_id IS NOT NULL
                DO NOTHING
                RETURNING pub_id
                """,
                (report_pub_id, tenant_pub_id, project_pub_id, title, workflow_operation_id),
            ).fetchone()
            if inserted is None:
                existing = connection.execute(
                    """
                    SELECT r.pub_id,r.project_pub_id,r.title,rv.pub_id AS version_pub_id,
                           rv.filter_hash,rv.fact_snapshot_hash,rv.metric_version,
                           rv.scorer_version
                    FROM reporting.report r
                    JOIN reporting.report_version rv ON rv.report_pub_id=r.pub_id
                    WHERE r.tenant_pub_id=%s AND r.workflow_operation_id=%s
                    ORDER BY rv.version_number DESC LIMIT 1
                    """,
                    (tenant_pub_id, workflow_operation_id),
                ).fetchone()
                if existing is None:
                    raise RuntimeError("idempotent report exists without a version")
                expected = (
                    project_pub_id,
                    title,
                    frozen.filter_hash,
                    frozen.fact_snapshot_hash,
                    metric_version,
                    scorer_version,
                )
                actual = (
                    existing["project_pub_id"],
                    existing["title"],
                    existing["filter_hash"],
                    existing["fact_snapshot_hash"],
                    existing["metric_version"],
                    existing["scorer_version"],
                )
                if actual != expected:
                    raise ValueError("workflow operation replay payload drifted")
                report_pub_id = existing["pub_id"]
                version_pub_id = existing["version_pub_id"]
            else:
                connection.execute(
                    """
                    INSERT INTO reporting.report_version
                      (pub_id,tenant_pub_id,report_pub_id,version_number,window_start,window_end,
                       filters,filter_hash,metric_version,scorer_version,fact_snapshot_hash,status,
                       ai_draft_hash,created_by_pub_id)
                    VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,'review',%s,%s)
                    """,
                    (
                        version_pub_id,
                        tenant_pub_id,
                        report_pub_id,
                        frozen.window_start,
                        frozen.window_end,
                        json.dumps(filters),
                        frozen.filter_hash,
                        metric_version,
                        scorer_version,
                        frozen.fact_snapshot_hash,
                        sha256(json.dumps(list(sections), default=str).encode()).hexdigest(),
                        created_by_pub_id,
                    ),
                )
                for ordinal, section in enumerate(sections):
                    component_type, source, component_payload = _normalize_component(
                        section, default_source="ai"
                    )
                    connection.execute(
                        """
                        INSERT INTO reporting.report_component
                          (pub_id,tenant_pub_id,report_version_pub_id,component_type,ordinal,
                           payload,source)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            new_pub_id("rptc"),
                            tenant_pub_id,
                            version_pub_id,
                            component_type,
                            ordinal,
                            json.dumps(component_payload, ensure_ascii=False),
                            source,
                        ),
                    )
                self._persist_evidence_references(
                    connection=connection,
                    tenant_pub_id=tenant_pub_id,
                    version_pub_id=version_pub_id,
                    values=[*fact_rows, *sections],
                )
            self._persist_frozen_facts(
                connection=connection,
                tenant_pub_id=tenant_pub_id,
                version_pub_id=version_pub_id,
                fact_rows=fact_rows,
            )
        artifact_payloads = {
            "html": (
                render_html(title, sections),
                "text/html",
            ),
            "docx": (
                render_docx(title, sections),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            "pdf": (render_pdf(title, sections), "application/pdf"),
            "xlsx": (
                render_xlsx(fact_rows),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        }
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            existing_artifacts = connection.execute(
                """
                SELECT format,evidence_pub_id FROM reporting.report_artifact
                WHERE tenant_pub_id=%s AND report_version_pub_id=%s
                """,
                (tenant_pub_id, version_pub_id),
            ).fetchall()
        artifacts = {row["format"]: row["evidence_pub_id"] for row in existing_artifacts}
        for format_name, (payload, mime_type) in artifact_payloads.items():
            if format_name in artifacts:
                continue
            evidence_pub_id = new_pub_id("evd")
            with tenant_connection(self.dsn, tenant_pub_id) as connection:
                stored = self.evidence.capture(
                    evidence_pub_id=evidence_pub_id,
                    tenant_pub_id=tenant_pub_id,
                    project_pub_id=project_pub_id,
                    kind=f"report_{format_name}",
                    payload=payload,
                    mime_type=mime_type,
                    source_url=None,
                    provenance=provenance,
                    db_connection=connection,
                )
                artifact_evidence_pub_id = stored.metadata_pub_id or evidence_pub_id
                connection.execute(
                    """
                    INSERT INTO reporting.report_artifact
                      (pub_id,tenant_pub_id,report_version_pub_id,format,evidence_pub_id)
                    VALUES (%s,%s,%s,%s,%s)
                    """,
                    (
                        new_pub_id("rpta"),
                        tenant_pub_id,
                        version_pub_id,
                        format_name,
                        artifact_evidence_pub_id,
                    ),
                )
            artifacts[format_name] = artifact_evidence_pub_id
        return {
            "report_pub_id": report_pub_id,
            "report_version_pub_id": version_pub_id,
            "state": "review",
            "freeze": frozen,
            "artifacts": artifacts,
        }

    def create_revision(
        self,
        *,
        tenant_pub_id: str,
        report_pub_id: str,
        fact_rows: Sequence[Mapping[str, Any]] | None,
        sections: Sequence[Mapping[str, object]],
        created_by_pub_id: str,
        provenance: RedactedProvenance,
        idempotency_key_hash: str | None = None,
    ) -> dict[str, Any]:
        if idempotency_key_hash is not None and (
            len(idempotency_key_hash) != 64
            or any(character not in "0123456789abcdef" for character in idempotency_key_hash)
        ):
            raise ValueError("idempotency key hash must be lowercase SHA-256")
        replay = False
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            report = connection.execute(
                """
                SELECT project_pub_id,title,state FROM reporting.report
                WHERE tenant_pub_id=%s AND pub_id=%s FOR UPDATE
                """,
                (tenant_pub_id, report_pub_id),
            ).fetchone()
            if report is None:
                raise LookupError("report not found")

            existing = None
            if idempotency_key_hash is not None:
                existing = connection.execute(
                    """
                    SELECT * FROM reporting.report_version
                    WHERE tenant_pub_id=%s AND report_pub_id=%s
                      AND authoring_operation_hash=%s
                    """,
                    (tenant_pub_id, report_pub_id, idempotency_key_hash),
                ).fetchone()
            if existing is not None:
                replay = True
                previous = existing
                fact_rows = [
                    row["payload"]
                    for row in connection.execute(
                        """
                        SELECT payload FROM reporting.report_frozen_fact
                        WHERE tenant_pub_id=%s AND report_version_pub_id=%s
                        ORDER BY ordinal
                        """,
                        (tenant_pub_id, existing["pub_id"]),
                    ).fetchall()
                ]
            else:
                if report["state"] == "published":
                    raise PermissionError("published report is immutable; create a new report")
                previous = connection.execute(
                    """
                    SELECT * FROM reporting.report_version
                    WHERE tenant_pub_id=%s AND report_pub_id=%s
                    ORDER BY version_number DESC LIMIT 1 FOR UPDATE
                    """,
                    (tenant_pub_id, report_pub_id),
                ).fetchone()
                if previous is None:
                    raise LookupError("previous report version not found")
                artifact_count = connection.execute(
                    """
                    SELECT count(*) FROM reporting.report_artifact
                    WHERE tenant_pub_id=%s AND report_version_pub_id=%s
                    """,
                    (tenant_pub_id, previous["pub_id"]),
                ).fetchone()
                if artifact_count is None or int(artifact_count["count"]) != 4:
                    raise ReportRevisionIncomplete(
                        "previous report revision has incomplete artifacts"
                    )
                if fact_rows is None:
                    fact_rows = [
                        row["payload"]
                        for row in connection.execute(
                            """
                            SELECT payload FROM reporting.report_frozen_fact
                            WHERE tenant_pub_id=%s AND report_version_pub_id=%s
                            ORDER BY ordinal
                            """,
                            (tenant_pub_id, previous["pub_id"]),
                        ).fetchall()
                    ]

            assert fact_rows is not None
            assert_customer_report_safe([*fact_rows, *sections])
            canonical_fact_rows = sorted(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                for row in fact_rows
            )
            contract_hash = sha256(
                json.dumps(
                    {"fact_rows": canonical_fact_rows, "sections": list(sections)},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode()
            ).hexdigest()
            if replay:
                if previous["authoring_contract_hash"] != contract_hash:
                    raise ReportRevisionIdempotencyConflict(
                        "report revision idempotency payload drifted"
                    )
                version_pub_id = previous["pub_id"]
                version_number = previous["version_number"]
            else:
                version_pub_id = new_pub_id("rptv")
                version_number = previous["version_number"] + 1

            frozen = freeze_report(
                window_start=previous["window_start"],
                window_end=previous["window_end"],
                filters=previous["filters"],
                metric_version=previous["metric_version"],
                scorer_version=previous["scorer_version"],
                fact_rows=fact_rows,
            )
            if not replay:
                connection.execute(
                    """
                    INSERT INTO reporting.report_version
                      (pub_id,tenant_pub_id,report_pub_id,version_number,window_start,window_end,
                       filters,filter_hash,metric_version,scorer_version,fact_snapshot_hash,status,
                       ai_draft_hash,human_edit_hash,created_by_pub_id,
                       authoring_operation_hash,authoring_contract_hash)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'review',NULL,%s,%s,%s,%s)
                    """,
                    (
                        version_pub_id,
                        tenant_pub_id,
                        report_pub_id,
                        version_number,
                        frozen.window_start,
                        frozen.window_end,
                        json.dumps(frozen.filters),
                        frozen.filter_hash,
                        frozen.metric_version,
                        frozen.scorer_version,
                        frozen.fact_snapshot_hash,
                        sha256(
                            json.dumps(
                                list(sections),
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                                default=str,
                            ).encode()
                        ).hexdigest(),
                        created_by_pub_id,
                        idempotency_key_hash,
                        contract_hash if idempotency_key_hash is not None else None,
                    ),
                )
                for ordinal, section in enumerate(sections):
                    component_type, source, component_payload = _normalize_component(
                        section, default_source="human"
                    )
                    connection.execute(
                        """
                        INSERT INTO reporting.report_component
                          (pub_id,tenant_pub_id,report_version_pub_id,component_type,ordinal,
                           payload,source)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            new_pub_id("rptc"),
                            tenant_pub_id,
                            version_pub_id,
                            component_type,
                            ordinal,
                            json.dumps(component_payload, ensure_ascii=False),
                            source,
                        ),
                    )
                self._persist_evidence_references(
                    connection=connection,
                    tenant_pub_id=tenant_pub_id,
                    version_pub_id=version_pub_id,
                    values=[*fact_rows, *sections],
                )
                self._persist_frozen_facts(
                    connection=connection,
                    tenant_pub_id=tenant_pub_id,
                    version_pub_id=version_pub_id,
                    fact_rows=fact_rows,
                )
                connection.execute(
                    """
                    INSERT INTO reporting.report_event
                      (pub_id,tenant_pub_id,report_pub_id,report_version_pub_id,event_type,
                       actor_pub_id,data)
                    VALUES (%s,%s,%s,%s,'revision_created',%s,%s)
                    """,
                    (
                        new_pub_id("evt"),
                        tenant_pub_id,
                        report_pub_id,
                        version_pub_id,
                        created_by_pub_id,
                        json.dumps({"supersedes_version_pub_id": previous["pub_id"]}),
                    ),
                )
                connection.execute(
                    """
                    UPDATE reporting.report SET state='review',updated_at=now()
                    WHERE tenant_pub_id=%s AND pub_id=%s
                    """,
                    (tenant_pub_id, report_pub_id),
                )
        artifacts = self._render_revision_artifacts(
            tenant_pub_id=tenant_pub_id,
            project_pub_id=report["project_pub_id"],
            title=report["title"],
            version_pub_id=version_pub_id,
            fact_rows=fact_rows,
            sections=sections,
            provenance=provenance,
        )
        return {
            "report_pub_id": report_pub_id,
            "report_version_pub_id": version_pub_id,
            "version_number": version_number,
            "freeze": frozen,
            "artifacts": artifacts,
        }

    def diff_versions(
        self,
        *,
        tenant_pub_id: str,
        report_pub_id: str,
        before_version: int,
        after_version: int,
    ) -> ReportVersionDiff:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT rv.version_number,rc.payload
                FROM reporting.report_version rv
                JOIN reporting.report_component rc ON rc.report_version_pub_id=rv.pub_id
                WHERE rv.tenant_pub_id=%s AND rv.report_pub_id=%s
                  AND rv.version_number=ANY(%s)
                ORDER BY rv.version_number,rc.ordinal
                """,
                (tenant_pub_id, report_pub_id, [before_version, after_version]),
            ).fetchall()
        components = {
            version: [row["payload"] for row in rows if row["version_number"] == version]
            for version in (before_version, after_version)
        }
        if not all(components.values()):
            raise LookupError("both report versions must exist and contain components")
        return compare_report_versions(
            before_version=before_version,
            after_version=after_version,
            before_components=components[before_version],
            after_components=components[after_version],
        )

    def _render_revision_artifacts(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        title: str,
        version_pub_id: str,
        fact_rows: Sequence[Mapping[str, Any]],
        sections: Sequence[Mapping[str, object]],
        provenance: RedactedProvenance,
    ) -> dict[str, str]:
        payloads = {
            "html": (render_html(title, sections), "text/html"),
            "docx": (
                render_docx(title, sections),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            "pdf": (render_pdf(title, sections), "application/pdf"),
            "xlsx": (
                render_xlsx(fact_rows),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        }
        artifacts: dict[str, str] = {}
        for format_name, (payload, mime_type) in payloads.items():
            with tenant_connection(self.dsn, tenant_pub_id) as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (f"{tenant_pub_id}:{version_pub_id}:{format_name}",),
                )
                existing = connection.execute(
                    """
                    SELECT evidence_pub_id FROM reporting.report_artifact
                    WHERE tenant_pub_id=%s AND report_version_pub_id=%s AND format=%s
                    """,
                    (tenant_pub_id, version_pub_id, format_name),
                ).fetchone()
                if existing is not None:
                    artifacts[format_name] = str(existing[0])
                    continue
                evidence_pub_id = new_pub_id("evd")
                stored = self.evidence.capture(
                    evidence_pub_id=evidence_pub_id,
                    tenant_pub_id=tenant_pub_id,
                    project_pub_id=project_pub_id,
                    kind=f"report_{format_name}",
                    payload=payload,
                    mime_type=mime_type,
                    source_url=None,
                    provenance=provenance,
                    db_connection=connection,
                )
                artifact_evidence_pub_id = stored.metadata_pub_id or evidence_pub_id
                connection.execute(
                    """
                    INSERT INTO reporting.report_artifact
                      (pub_id,tenant_pub_id,report_version_pub_id,format,evidence_pub_id)
                    VALUES (%s,%s,%s,%s,%s)
                    """,
                    (
                        new_pub_id("rpta"),
                        tenant_pub_id,
                        version_pub_id,
                        format_name,
                        artifact_evidence_pub_id,
                    ),
                )
            artifacts[format_name] = artifact_evidence_pub_id
        return artifacts

    def _persist_evidence_references(
        self,
        *,
        connection: psycopg.Connection[Any],
        tenant_pub_id: str,
        version_pub_id: str,
        values: Sequence[Mapping[str, Any]],
    ) -> None:
        evidence_pub_ids = _extract_evidence_pub_ids(values)
        if not evidence_pub_ids:
            return
        rows = connection.execute(
            """
            SELECT pub_id FROM evidence.evidence_asset
            WHERE tenant_pub_id=%s AND pub_id=ANY(%s) AND deleted_at IS NULL
            """,
            (tenant_pub_id, sorted(evidence_pub_ids)),
        ).fetchall()
        found = {str(row["pub_id"] if isinstance(row, Mapping) else row[0]) for row in rows}
        if found != evidence_pub_ids:
            raise LookupError("report references missing or cross-tenant evidence")
        for evidence_pub_id in sorted(found):
            connection.execute(
                """
                INSERT INTO reporting.report_evidence_reference
                  (pub_id,tenant_pub_id,report_version_pub_id,evidence_pub_id,purpose)
                VALUES (%s,%s,%s,%s,'frozen_fact_or_component')
                ON CONFLICT (tenant_pub_id,report_version_pub_id,evidence_pub_id) DO NOTHING
                """,
                (
                    new_pub_id("rptev"),
                    tenant_pub_id,
                    version_pub_id,
                    evidence_pub_id,
                ),
            )

    def _persist_frozen_facts(
        self,
        *,
        connection: psycopg.Connection[Any],
        tenant_pub_id: str,
        version_pub_id: str,
        fact_rows: Sequence[Mapping[str, Any]],
    ) -> None:
        serialized = sorted(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            for row in fact_rows
        )
        for ordinal, payload in enumerate(serialized):
            connection.execute(
                """
                INSERT INTO reporting.report_frozen_fact
                  (pub_id,tenant_pub_id,report_version_pub_id,ordinal,payload,payload_hash)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_pub_id,report_version_pub_id,ordinal) DO NOTHING
                """,
                (
                    new_pub_id("rptf"),
                    tenant_pub_id,
                    version_pub_id,
                    ordinal,
                    payload,
                    sha256(payload.encode()).hexdigest(),
                ),
            )

    def record_human_edit(
        self,
        *,
        tenant_pub_id: str,
        report_pub_id: str,
        version_pub_id: str,
        actor_pub_id: str,
        before: str,
        after: str,
    ) -> None:
        with tenant_connection(self.dsn, tenant_pub_id) as connection:
            connection.execute(
                """
                UPDATE reporting.report_version SET human_edit_hash=%s
                WHERE pub_id=%s AND tenant_pub_id=%s
                """,
                (sha256(after.encode()).hexdigest(), version_pub_id, tenant_pub_id),
            )
            connection.execute(
                """
                INSERT INTO reporting.report_event
                  (pub_id,tenant_pub_id,report_pub_id,report_version_pub_id,event_type,
                   actor_pub_id,data)
                VALUES (%s,%s,%s,%s,'human_edited',%s,%s)
                """,
                (
                    new_pub_id("evt"),
                    tenant_pub_id,
                    report_pub_id,
                    version_pub_id,
                    actor_pub_id,
                    json.dumps(
                        {
                            "before_hash": sha256(before.encode()).hexdigest(),
                            "after_hash": sha256(after.encode()).hexdigest(),
                        }
                    ),
                ),
            )

    def publish(
        self,
        *,
        tenant_pub_id: str,
        report_pub_id: str,
        version_pub_id: str,
        reviewer_pub_id: str,
    ) -> None:
        with tenant_connection(self.dsn, tenant_pub_id) as connection:
            version = connection.execute(
                """
                SELECT 1 FROM reporting.report_version
                WHERE tenant_pub_id=%s AND pub_id=%s AND report_pub_id=%s
                """,
                (tenant_pub_id, version_pub_id, report_pub_id),
            ).fetchone()
            if version is None:
                raise LookupError("report version not found")
            approved = connection.execute(
                """
                SELECT 1 FROM reporting.report_review
                WHERE tenant_pub_id=%s AND report_version_pub_id=%s AND decision='approved'
                ORDER BY id DESC LIMIT 1
                """,
                (tenant_pub_id, version_pub_id),
            ).fetchone()
            if approved is None:
                raise PermissionError("report publication requires an approved human review")
            updated = connection.execute(
                """
                UPDATE reporting.report SET state='published',updated_at=now()
                WHERE pub_id=%s AND tenant_pub_id=%s AND state IN ('review','approved')
                RETURNING pub_id
                """,
                (report_pub_id, tenant_pub_id),
            ).fetchone()
            if updated is None:
                raise LookupError("report is not publishable")
            connection.execute(
                """
                UPDATE reporting.report_version SET status='published'
                WHERE pub_id=%s AND tenant_pub_id=%s
                """,
                (version_pub_id, tenant_pub_id),
            )
            connection.execute(
                """
                INSERT INTO reporting.report_event
                  (pub_id,tenant_pub_id,report_pub_id,report_version_pub_id,event_type,
                   actor_pub_id,data)
                VALUES (%s,%s,%s,%s,'published',%s,'{}')
                """,
                (
                    new_pub_id("evt"),
                    tenant_pub_id,
                    report_pub_id,
                    version_pub_id,
                    reviewer_pub_id,
                ),
            )

    def review(
        self,
        *,
        tenant_pub_id: str,
        report_pub_id: str,
        version_pub_id: str,
        reviewer_pub_id: str,
        decision: str,
        rationale: str,
        workflow_operation_id: str | None = None,
    ) -> str:
        review_pub_id = new_pub_id("rvw")
        with tenant_connection(self.dsn, tenant_pub_id) as connection:
            version = connection.execute(
                """
                SELECT 1 FROM reporting.report_version
                WHERE tenant_pub_id=%s AND pub_id=%s AND report_pub_id=%s
                """,
                (tenant_pub_id, version_pub_id, report_pub_id),
            ).fetchone()
            if version is None:
                raise LookupError("report version not found")
            persisted = connection.execute(
                """
                INSERT INTO reporting.report_review
                  (pub_id,tenant_pub_id,report_version_pub_id,reviewer_pub_id,decision,rationale,
                   workflow_operation_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_pub_id,workflow_operation_id)
                  WHERE workflow_operation_id IS NOT NULL
                DO UPDATE SET pub_id=reporting.report_review.pub_id
                RETURNING pub_id,report_version_pub_id,reviewer_pub_id,decision,rationale
                """,
                (
                    review_pub_id,
                    tenant_pub_id,
                    version_pub_id,
                    reviewer_pub_id,
                    decision,
                    rationale,
                    workflow_operation_id,
                ),
            ).fetchone()
            assert persisted is not None
            if persisted[1:] != (version_pub_id, reviewer_pub_id, decision, rationale):
                raise ValueError("workflow report review replay payload drifted")
            state = "approved" if decision == "approved" else "review"
            connection.execute(
                "UPDATE reporting.report SET state=%s,updated_at=now() "
                "WHERE pub_id=%s AND tenant_pub_id=%s",
                (state, report_pub_id, tenant_pub_id),
            )
        return str(persisted[0])

    def comment(
        self,
        *,
        tenant_pub_id: str,
        report_pub_id: str,
        version_pub_id: str,
        author_pub_id: str,
        body: str,
        parent_pub_id: str | None = None,
    ) -> str:
        comment_pub_id = new_pub_id("cmt")
        with tenant_connection(self.dsn, tenant_pub_id) as connection:
            version = connection.execute(
                """
                SELECT 1 FROM reporting.report_version
                WHERE tenant_pub_id=%s AND pub_id=%s AND report_pub_id=%s
                """,
                (tenant_pub_id, version_pub_id, report_pub_id),
            ).fetchone()
            if version is None:
                raise LookupError("report version not found")
            if parent_pub_id is not None:
                parent = connection.execute(
                    """
                    SELECT 1 FROM reporting.report_comment
                    WHERE tenant_pub_id=%s AND pub_id=%s AND report_version_pub_id=%s
                    """,
                    (tenant_pub_id, parent_pub_id, version_pub_id),
                ).fetchone()
                if parent is None:
                    raise LookupError("parent comment not found")
            connection.execute(
                """
                INSERT INTO reporting.report_comment
                  (pub_id,tenant_pub_id,report_version_pub_id,parent_pub_id,author_pub_id,body)
                VALUES (%s,%s,%s,%s,%s,%s)
                """,
                (
                    comment_pub_id,
                    tenant_pub_id,
                    version_pub_id,
                    parent_pub_id,
                    author_pub_id,
                    body,
                ),
            )
        return comment_pub_id

    def deliver(
        self,
        *,
        tenant_pub_id: str,
        report_pub_id: str,
        recipient_pub_id: str,
        delivered_by_pub_id: str,
    ) -> str:
        delivery_pub_id = new_pub_id("dlv")
        now = datetime.now(UTC)
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            published = connection.execute(
                """
                SELECT 1 FROM reporting.report
                WHERE pub_id=%s AND tenant_pub_id=%s AND state='published'
                """,
                (report_pub_id, tenant_pub_id),
            ).fetchone()
            if published is None:
                raise PermissionError("only published reports can be delivered")
            delivery = connection.execute(
                """
                INSERT INTO reporting.report_delivery
                  (pub_id,tenant_pub_id,report_pub_id,recipient_pub_id,delivered_at,
                   confirmed_at,confirmation_comment)
                VALUES (%s,%s,%s,%s,%s,NULL,NULL)
                ON CONFLICT (tenant_pub_id,report_pub_id,recipient_pub_id)
                DO UPDATE SET pub_id=reporting.report_delivery.pub_id
                RETURNING pub_id,delivered_at
                """,
                (
                    delivery_pub_id,
                    tenant_pub_id,
                    report_pub_id,
                    recipient_pub_id,
                    now,
                ),
            ).fetchone()
            assert delivery is not None
            connection.execute(
                """
                INSERT INTO reporting.report_event
                  (pub_id,tenant_pub_id,report_pub_id,report_version_pub_id,event_type,
                   actor_pub_id,data,created_at)
                VALUES (%s,%s,%s,NULL,'delivered',%s,%s,%s)
                ON CONFLICT DO NOTHING
                """,
                (
                    new_pub_id("evt"),
                    tenant_pub_id,
                    report_pub_id,
                    delivered_by_pub_id,
                    json.dumps(
                        {
                            "delivery_pub_id": delivery["pub_id"],
                            "recipient_pub_id": recipient_pub_id,
                        }
                    ),
                    delivery["delivered_at"],
                ),
            )
        return str(delivery["pub_id"])

    def confirm_delivery(
        self,
        *,
        tenant_pub_id: str,
        report_pub_id: str,
        delivery_pub_id: str,
        recipient_pub_id: str,
        confirmation_comment: str,
    ) -> str:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            delivery = connection.execute(
                """
                SELECT pub_id,recipient_pub_id,confirmed_at,confirmation_comment
                FROM reporting.report_delivery
                WHERE tenant_pub_id=%s AND report_pub_id=%s AND pub_id=%s
                FOR UPDATE
                """,
                (tenant_pub_id, report_pub_id, delivery_pub_id),
            ).fetchone()
            if delivery is None:
                raise LookupError("report delivery not found")
            if delivery["recipient_pub_id"] != recipient_pub_id:
                raise PermissionError("only the report recipient can confirm delivery")
            if delivery["confirmed_at"] is not None:
                if delivery["confirmation_comment"] != confirmation_comment:
                    raise ValueError("delivery confirmation replay payload drifted")
                confirmed_at = delivery["confirmed_at"]
            else:
                updated = connection.execute(
                    """
                    UPDATE reporting.report_delivery
                    SET confirmed_at=now(),confirmation_comment=%s
                    WHERE tenant_pub_id=%s AND pub_id=%s
                    RETURNING confirmed_at
                    """,
                    (confirmation_comment, tenant_pub_id, delivery_pub_id),
                ).fetchone()
                assert updated is not None
                confirmed_at = updated["confirmed_at"]
            connection.execute(
                """
                INSERT INTO reporting.report_event
                  (pub_id,tenant_pub_id,report_pub_id,report_version_pub_id,event_type,
                   actor_pub_id,data,created_at)
                VALUES (%s,%s,%s,NULL,'delivery_confirmed',%s,%s,%s)
                ON CONFLICT DO NOTHING
                """,
                (
                    new_pub_id("evt"),
                    tenant_pub_id,
                    report_pub_id,
                    recipient_pub_id,
                    json.dumps(
                        {
                            "delivery_pub_id": delivery_pub_id,
                            "recipient_pub_id": recipient_pub_id,
                        }
                    ),
                    confirmed_at,
                ),
            )
        return delivery_pub_id

    def list_deliveries(
        self,
        *,
        tenant_pub_id: str,
        report_pub_id: str,
        recipient_pub_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            report = connection.execute(
                """
                SELECT 1 FROM reporting.report
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (tenant_pub_id, report_pub_id),
            ).fetchone()
            if report is None:
                raise LookupError("report not found")
            return list(
                connection.execute(
                    """
                    SELECT pub_id,report_pub_id,recipient_pub_id,delivered_at,confirmed_at,
                           confirmation_comment
                    FROM reporting.report_delivery
                    WHERE tenant_pub_id=%s AND report_pub_id=%s
                      AND (%s::text IS NULL OR recipient_pub_id=%s)
                    ORDER BY delivered_at,pub_id
                    """,
                    (tenant_pub_id, report_pub_id, recipient_pub_id, recipient_pub_id),
                ).fetchall()
            )

    def deliver_and_confirm(
        self,
        *,
        tenant_pub_id: str,
        report_pub_id: str,
        recipient_pub_id: str,
        confirmation_comment: str,
    ) -> str:
        """Compatibility helper for tests; production APIs keep the actors separate."""
        delivery_pub_id = self.deliver(
            tenant_pub_id=tenant_pub_id,
            report_pub_id=report_pub_id,
            recipient_pub_id=recipient_pub_id,
            delivered_by_pub_id=recipient_pub_id,
        )
        return self.confirm_delivery(
            tenant_pub_id=tenant_pub_id,
            report_pub_id=report_pub_id,
            delivery_pub_id=delivery_pub_id,
            recipient_pub_id=recipient_pub_id,
            confirmation_comment=confirmation_comment,
        )

    def create_optimization_action(
        self,
        *,
        tenant_pub_id: str,
        report_pub_id: str,
        description: str,
        owner_pub_id: str | None,
        baseline: Mapping[str, Any],
    ) -> str:
        action_pub_id = new_pub_id("act")
        with tenant_connection(self.dsn, tenant_pub_id) as connection:
            report = connection.execute(
                "SELECT 1 FROM reporting.report WHERE pub_id=%s AND tenant_pub_id=%s",
                (report_pub_id, tenant_pub_id),
            ).fetchone()
            if report is None:
                raise LookupError("report not found")
            connection.execute(
                """
                INSERT INTO reporting.optimization_action
                  (pub_id,tenant_pub_id,report_pub_id,description,owner_pub_id,state,baseline)
                VALUES (%s,%s,%s,%s,%s,'proposed',%s)
                """,
                (
                    action_pub_id,
                    tenant_pub_id,
                    report_pub_id,
                    description,
                    owner_pub_id,
                    json.dumps(baseline),
                ),
            )
        return action_pub_id

    def update_optimization_action(
        self,
        *,
        tenant_pub_id: str,
        action_pub_id: str,
        state: str,
        outcome: Mapping[str, Any] | None = None,
    ) -> None:
        allowed = {"accepted", "in_progress", "done", "rejected"}
        if state not in allowed:
            raise ValueError(f"invalid optimization action state: {state}")
        if state == "done" and outcome is None:
            raise ValueError("completed optimization action requires an outcome review")
        with tenant_connection(self.dsn, tenant_pub_id) as connection:
            updated = connection.execute(
                """
                UPDATE reporting.optimization_action
                SET state=%s,outcome=COALESCE(%s,outcome),updated_at=now()
                WHERE pub_id=%s AND tenant_pub_id=%s
                RETURNING pub_id
                """,
                (
                    state,
                    json.dumps(outcome) if outcome is not None else None,
                    action_pub_id,
                    tenant_pub_id,
                ),
            ).fetchone()
            if updated is None:
                raise LookupError("optimization action not found")

    def record_effect_retest(
        self,
        *,
        tenant_pub_id: str,
        action_pub_id: str,
        measured_at: datetime,
        result: Mapping[str, Any],
        recorded_by_pub_id: str,
    ) -> str:
        retest_pub_id = new_pub_id("rts")
        with tenant_connection(self.dsn, tenant_pub_id) as connection:
            action = connection.execute(
                """
                SELECT 1 FROM reporting.optimization_action
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (tenant_pub_id, action_pub_id),
            ).fetchone()
            if action is None:
                raise LookupError("optimization action not found")
            connection.execute(
                """
                INSERT INTO reporting.effect_retest
                  (pub_id,tenant_pub_id,action_pub_id,measured_at,result,recorded_by_pub_id)
                VALUES (%s,%s,%s,%s,%s,%s)
                """,
                (
                    retest_pub_id,
                    tenant_pub_id,
                    action_pub_id,
                    measured_at,
                    json.dumps(result, ensure_ascii=False),
                    recorded_by_pub_id,
                ),
            )
        return retest_pub_id


def _extract_evidence_pub_ids(values: object) -> set[str]:
    found: set[str] = set()
    if isinstance(values, Mapping):
        for key, value in values.items():
            if key == "evidence_pub_id" and isinstance(value, str):
                found.add(value)
            elif (
                key == "evidence_pub_ids"
                and isinstance(value, Sequence)
                and not isinstance(value, str | bytes)
            ):
                found.update(item for item in value if isinstance(item, str))
            else:
                found.update(_extract_evidence_pub_ids(value))
    elif isinstance(values, Sequence) and not isinstance(values, str | bytes):
        for value in values:
            found.update(_extract_evidence_pub_ids(value))
    return found


def _normalize_component(
    value: Mapping[str, object], *, default_source: str
) -> tuple[str, str, dict[str, object]]:
    component_type = str(value.get("component_type", "section"))
    source = str(value.get("source", default_source))
    if component_type not in {"kpi", "chart", "section", "evidence", "recommendation"}:
        raise ValueError(f"unsupported report component type: {component_type}")
    if source not in {"system", "ai", "human"}:
        raise ValueError(f"unsupported report component source: {source}")
    payload = {
        key: child for key, child in value.items() if key not in {"component_type", "source"}
    }
    return component_type, source, payload
