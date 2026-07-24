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
        assert_customer_report_safe(sections)
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
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
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
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
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
            with psycopg.connect(self.dsn) as connection:
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
        fact_rows: Sequence[Mapping[str, Any]],
        sections: Sequence[Mapping[str, object]],
        created_by_pub_id: str,
        provenance: RedactedProvenance,
    ) -> dict[str, Any]:
        assert_customer_report_safe(sections)
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            previous = connection.execute(
                """
                SELECT * FROM reporting.report_version
                WHERE tenant_pub_id=%s AND report_pub_id=%s
                ORDER BY version_number DESC LIMIT 1 FOR UPDATE
                """,
                (tenant_pub_id, report_pub_id),
            ).fetchone()
            report = connection.execute(
                """
                SELECT project_pub_id,title,state FROM reporting.report
                WHERE tenant_pub_id=%s AND pub_id=%s FOR UPDATE
                """,
                (tenant_pub_id, report_pub_id),
            ).fetchone()
            if previous is None or report is None:
                raise LookupError("report or previous version not found")
            if report["state"] == "published":
                raise PermissionError("published report is immutable; create a new report")
            frozen = freeze_report(
                window_start=previous["window_start"],
                window_end=previous["window_end"],
                filters=previous["filters"],
                metric_version=previous["metric_version"],
                scorer_version=previous["scorer_version"],
                fact_rows=fact_rows,
            )
            version_pub_id = new_pub_id("rptv")
            version_number = previous["version_number"] + 1
            connection.execute(
                """
                INSERT INTO reporting.report_version
                  (pub_id,tenant_pub_id,report_pub_id,version_number,window_start,window_end,
                   filters,filter_hash,metric_version,scorer_version,fact_snapshot_hash,status,
                   ai_draft_hash,created_by_pub_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'review',%s,%s)
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
                    sha256(json.dumps(list(sections), default=str).encode()).hexdigest(),
                    created_by_pub_id,
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
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
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
            evidence_pub_id = new_pub_id("evd")
            with psycopg.connect(self.dsn) as connection:
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
        with psycopg.connect(self.dsn) as connection:
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
        with psycopg.connect(self.dsn) as connection:
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
    ) -> str:
        review_pub_id = new_pub_id("rvw")
        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                """
                INSERT INTO reporting.report_review
                  (pub_id,tenant_pub_id,report_version_pub_id,reviewer_pub_id,decision,rationale)
                VALUES (%s,%s,%s,%s,%s,%s)
                """,
                (
                    review_pub_id,
                    tenant_pub_id,
                    version_pub_id,
                    reviewer_pub_id,
                    decision,
                    rationale,
                ),
            )
            state = "approved" if decision == "approved" else "review"
            connection.execute(
                "UPDATE reporting.report SET state=%s,updated_at=now() "
                "WHERE pub_id=%s AND tenant_pub_id=%s",
                (state, report_pub_id, tenant_pub_id),
            )
        return review_pub_id

    def comment(
        self,
        *,
        tenant_pub_id: str,
        version_pub_id: str,
        author_pub_id: str,
        body: str,
        parent_pub_id: str | None = None,
    ) -> str:
        comment_pub_id = new_pub_id("cmt")
        with psycopg.connect(self.dsn) as connection:
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

    def deliver_and_confirm(
        self,
        *,
        tenant_pub_id: str,
        report_pub_id: str,
        recipient_pub_id: str,
        confirmation_comment: str,
    ) -> str:
        delivery_pub_id = new_pub_id("dlv")
        now = datetime.now(UTC)
        with psycopg.connect(self.dsn) as connection:
            published = connection.execute(
                """
                SELECT 1 FROM reporting.report
                WHERE pub_id=%s AND tenant_pub_id=%s AND state='published'
                """,
                (report_pub_id, tenant_pub_id),
            ).fetchone()
            if published is None:
                raise PermissionError("only published reports can be delivered")
            connection.execute(
                """
                INSERT INTO reporting.report_delivery
                  (pub_id,tenant_pub_id,report_pub_id,recipient_pub_id,delivered_at,
                   confirmed_at,confirmation_comment)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    delivery_pub_id,
                    tenant_pub_id,
                    report_pub_id,
                    recipient_pub_id,
                    now,
                    now,
                    confirmation_comment,
                ),
            )
        return delivery_pub_id

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
        with psycopg.connect(self.dsn) as connection:
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
        with psycopg.connect(self.dsn) as connection:
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
