from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg
from geo_platform.analytics.service import AnalyticsService
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.evidence.service import EvidenceService
from geo_platform.evidence.session_gateway import SessionGatewayClient
from geo_platform.intelligence.service import IntelligenceService
from geo_platform.reports.service import ReportService
from temporalio import activity
from temporalio.exceptions import ApplicationError

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from domain.intelligence.core import EvidenceRelation, SourceAssessment, score_investigation
from domain.metrics.core import MetricRegistry
from domain.reporting.freeze import freeze_report
from domain.scoring.analyzer import CitationInput, analyze_answer


@activity.defn
async def analyze_answer_activity(payload: dict[str, Any]) -> dict[str, Any]:
    fail_until_attempt = int(payload.get("fail_until_attempt", 0))
    if activity.info().attempt <= fail_until_attempt:
        raise RuntimeError("injected retryable analysis failure")
    result = analyze_answer(
        answer_pub_id=payload["answer_pub_id"],
        text=payload["text"],
        brand=payload["brand"],
        competitors=tuple(payload.get("competitors", [])),
        citations=tuple(CitationInput(**item) for item in payload.get("citations", [])),
        dimensions=payload.get("dimensions", {}),
        own_domains=tuple(payload.get("own_domains", [])),
    )
    registry = MetricRegistry(
        metric_version=payload["metric_version"], scorer_version=payload["scorer_version"]
    )
    metrics = {
        name: _kpi_to_dict(
            registry.compute(
                name,
                [result.fact],
                filters=payload.get("filters", {}),
                recommendation_calibrated=payload.get("recommendation_calibrated", False),
            )
        )
        for name in (
            "mention_rate",
            "average_rank",
            "top1_rate",
            "top3_rate",
            "top10_rate",
            "citation_coverage",
            "recommendation_rate",
        )
    }
    response = {
        "answer_pub_id": result.fact.answer_pub_id,
        "input_hash": result.input_hash,
        "fact": {
            "mentioned": result.fact.mentioned,
            "rank": result.fact.rank,
            "sentiment": result.fact.sentiment,
            "recommended": result.fact.recommended,
            "competitor_ranks": dict(result.fact.competitor_ranks),
            "citation_count": result.fact.citation_count,
            "dimensions": dict(result.fact.dimensions),
        },
        "citations": list(result.citations),
        "metrics": metrics,
    }
    if payload.get("persist"):
        persisted = AnalyticsService(dsn=_postgres_dsn()).analyze_and_persist(
            tenant_pub_id=payload["tenant_pub_id"],
            project_pub_id=payload["project_pub_id"],
            answer_pub_id=payload["answer_pub_id"],
            answer_text=payload["text"],
            brand=payload["brand"],
            competitors=tuple(payload.get("competitors", [])),
            citations=tuple(CitationInput(**item) for item in payload.get("citations", [])),
            dimensions=payload.get("dimensions", {}),
            own_domains=tuple(payload.get("own_domains", [])),
            provenance=_provenance_from_payload(payload),
            scorer_version=payload["scorer_version"],
            metric_version=payload["metric_version"],
            model_version=payload["model_version"],
        )
        response["persistence"] = {
            "analysis_pub_id": persisted["analysis_pub_id"],
            "analysis_run_pub_id": persisted["analysis_run_pub_id"],
            "outbox_event_id": persisted["outbox_event_id"],
        }
    return response


@activity.defn
async def prepare_evidence_activity(payload: dict[str, Any]) -> dict[str, Any]:
    required = {
        "tenant_pub_id",
        "source_url",
        "kind",
        "mime_type",
        "capture_time",
        "adapter_version",
        "access_class",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError(f"missing evidence fields: {','.join(missing)}")
    forbidden = {
        "cookie",
        "authorization",
        "otp",
        "profile_object_key",
        "profile_path",
        "device_key",
        "proxy_password",
    }
    if forbidden & {key.lower() for key in payload}:
        _audit_evidence_access(
            tenant_pub_id=payload["tenant_pub_id"],
            resource_pub_id=str(payload.get("lease_pub_id") or "lease_missing"),
            request_id=str(payload.get("workflow_id") or "workflow_unknown"),
            action="capability_validate",
            outcome="denied",
            reason="secret_bearing_workflow_input",
        )
        raise ApplicationError(
            "secret-bearing evidence workflow input rejected",
            non_retryable=True,
        )
    lease = None
    if payload.get("requires_authenticated_session"):
        lease_pub_id = payload.get("lease_pub_id")
        if not lease_pub_id:
            raise PermissionError("authenticated evidence capture requires an S01 lease reference")
        try:
            lease = SessionGatewayClient(
                endpoint=os.getenv("GEO_SESSION_GATEWAY_URL", "http://127.0.0.1:45200"),
                service_token=os.getenv("GEO_SESSION_GATEWAY_SERVICE_TOKEN"),
            ).validate_capture_lease(
                lease_pub_id=str(lease_pub_id),
                tenant_pub_id=payload["tenant_pub_id"],
                platform_account_pub_id=payload["platform_account_pub_id"],
                target_url=payload["source_url"],
                action="capture_evidence",
                workflow_id=payload["workflow_id"],
                now=datetime.now(UTC),
            )
        except PermissionError as exc:
            _audit_evidence_access(
                tenant_pub_id=payload["tenant_pub_id"],
                resource_pub_id=str(lease_pub_id),
                request_id=payload["workflow_id"],
                action="capability_validate",
                outcome="denied",
                reason="lease_rejected",
            )
            raise ApplicationError("capability lease rejected", non_retryable=True) from exc
        _audit_evidence_access(
            tenant_pub_id=payload["tenant_pub_id"],
            resource_pub_id=lease.lease_pub_id,
            request_id=payload["workflow_id"],
            action="capability_validate",
            outcome="allowed",
            reason="scope_validated",
        )
    return {
        **payload,
        "prepared": True,
        "authorized_session_capture": bool(payload.get("requires_authenticated_session")),
        "authorization_scope": list(lease.authorization_scope) if lease else [],
        "session_lease_pub_id": lease.lease_pub_id if lease else None,
    }


@activity.defn
async def capture_evidence_activity(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("prepared"):
        raise PermissionError("evidence must pass capability preparation before capture")
    encoded = payload.get("capture_payload_b64")
    evidence_pub_id = payload.get("evidence_pub_id")
    if not isinstance(encoded, str) or not isinstance(evidence_pub_id, str):
        raise ValueError("capture payload and evidence public ID are required")
    try:
        content = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ValueError("capture payload must be valid base64") from exc
    provenance = RedactedProvenance(
        platform_account_pub_id=payload.get("platform_account_pub_id"),
        browser_profile_version_pub_id=payload.get("browser_profile_version_pub_id"),
        session_event_pub_id=payload.get("session_event_pub_id"),
        channel=CaptureChannel.WEB
        if payload.get("authorized_session_capture")
        else CaptureChannel.API,
        authorization_scope=tuple(payload.get("authorization_scope", [])),
        adapter_version=payload["adapter_version"],
        capture_time=datetime.fromisoformat(payload["capture_time"].replace("Z", "+00:00")),
        access_class=AccessClass(payload["access_class"]),
        authorized_session_capture=bool(payload.get("authorized_session_capture")),
    )
    store = ContentAddressedObjectStore(
        endpoint=os.getenv("GEO_MINIO_ENDPOINT", "http://127.0.0.1:19000"),
        access_key=os.getenv("GEO_MINIO_ACCESS_KEY", "geo"),
        secret_key=os.getenv("GEO_MINIO_SECRET_KEY", "geo_dev_only_password"),
    )
    store.ensure_bucket()
    service = EvidenceService(
        dsn=os.getenv(
            "S02_POSTGRES_DSN",
            "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform",
        ),
        store=store,
    )
    try:
        stored = service.capture(
            evidence_pub_id=evidence_pub_id,
            tenant_pub_id=payload["tenant_pub_id"],
            project_pub_id=payload.get("project_pub_id"),
            kind=payload["kind"],
            payload=content,
            mime_type=payload["mime_type"],
            source_url=payload["source_url"],
            provenance=provenance,
            validated_lease_pub_id=payload.get("session_lease_pub_id"),
        )
    except (PermissionError, ValueError) as exc:
        _audit_evidence_access(
            tenant_pub_id=payload["tenant_pub_id"],
            resource_pub_id=evidence_pub_id,
            request_id=str(payload.get("workflow_id") or "workflow_unknown"),
            action="capture",
            outcome="denied",
            reason="dlp_or_policy_rejected",
        )
        raise ApplicationError(
            "evidence capture rejected by DLP or policy",
            non_retryable=True,
        ) from exc
    return {
        "state": "captured",
        "captured": True,
        "evidence_pub_id": stored.metadata_pub_id or evidence_pub_id,
        "sha256": stored.sha256,
        "object_key": stored.key,
        "dlp_findings": list(stored.dlp_findings),
        "authorized_session_capture": provenance.authorized_session_capture,
    }


@activity.defn
async def freeze_report_activity(payload: dict[str, Any]) -> dict[str, Any]:
    frozen = freeze_report(
        window_start=datetime.fromisoformat(payload["window_start"].replace("Z", "+00:00")),
        window_end=datetime.fromisoformat(payload["window_end"].replace("Z", "+00:00")),
        filters=payload["filters"],
        metric_version=payload["metric_version"],
        scorer_version=payload["scorer_version"],
        fact_rows=payload["fact_rows"],
    )
    return {
        "window_start": frozen.window_start.astimezone(UTC).isoformat(),
        "window_end": frozen.window_end.astimezone(UTC).isoformat(),
        "filters": dict(frozen.filters),
        "metric_version": frozen.metric_version,
        "scorer_version": frozen.scorer_version,
        "fact_snapshot_hash": frozen.fact_snapshot_hash,
        "filter_hash": frozen.filter_hash,
    }


@activity.defn
async def produce_report_activity(payload: dict[str, Any]) -> dict[str, Any]:
    result = _report_service().produce(
        tenant_pub_id=payload["tenant_pub_id"],
        project_pub_id=payload["project_pub_id"],
        title=payload["title"],
        window_start=datetime.fromisoformat(payload["window_start"].replace("Z", "+00:00")),
        window_end=datetime.fromisoformat(payload["window_end"].replace("Z", "+00:00")),
        filters=payload["filters"],
        metric_version=payload["metric_version"],
        scorer_version=payload["scorer_version"],
        fact_rows=payload["fact_rows"],
        sections=payload["sections"],
        created_by_pub_id=payload["created_by_pub_id"],
        provenance=_provenance_from_payload(payload),
        workflow_operation_id=payload["workflow_operation_id"],
    )
    return {
        "report_pub_id": result["report_pub_id"],
        "report_version_pub_id": result["report_version_pub_id"],
        "artifacts": result["artifacts"],
        "fact_snapshot_hash": result["freeze"].fact_snapshot_hash,
    }


@activity.defn
async def finalize_report_activity(payload: dict[str, Any]) -> dict[str, Any]:
    service = _report_service()
    decision = payload["review"]
    with psycopg.connect(_postgres_dsn()) as connection:
        current_state = connection.execute(
            """
            SELECT state FROM reporting.report
            WHERE tenant_pub_id=%s AND pub_id=%s
            """,
            (payload["tenant_pub_id"], payload["report_pub_id"]),
        ).fetchone()
    if current_state == ("published",) and decision["approved"]:
        return {
            "state": "published",
            "report_pub_id": payload["report_pub_id"],
            "report_version_pub_id": payload["report_version_pub_id"],
        }
    service.review(
        tenant_pub_id=payload["tenant_pub_id"],
        report_pub_id=payload["report_pub_id"],
        version_pub_id=payload["report_version_pub_id"],
        reviewer_pub_id=decision["reviewer_pub_id"],
        decision="approved" if decision["approved"] else "changes_requested",
        rationale=decision.get("rationale", "Temporal human review"),
    )
    if decision["approved"]:
        service.publish(
            tenant_pub_id=payload["tenant_pub_id"],
            report_pub_id=payload["report_pub_id"],
            version_pub_id=payload["report_version_pub_id"],
            reviewer_pub_id=decision["reviewer_pub_id"],
        )
    return {
        "state": "published" if decision["approved"] else "changes_requested",
        "report_pub_id": payload["report_pub_id"],
        "report_version_pub_id": payload["report_version_pub_id"],
    }


@activity.defn
async def score_investigation_activity(payload: dict[str, Any]) -> dict[str, Any]:
    assessments = tuple(
        SourceAssessment(
            source_pub_id=item["source_pub_id"],
            source_cluster=item["source_cluster"],
            relation=EvidenceRelation(item["relation"]),
            independence_weight=Decimal(str(item["independence_weight"])),
            access_class=item.get("access_class", "public"),
        )
        for item in payload["assessments"]
    )
    result = score_investigation(
        assessments=assessments,
        content_feature_score=Decimal(str(payload["content_feature_score"])),
        propagation_feature_score=Decimal(str(payload["propagation_feature_score"])),
        circular_citation_risk=Decimal(str(payload["circular_citation_risk"])),
        rule_version=payload.get("rule_version", "anti-geo-rules-v1"),
        model_version=payload.get("model_version", "rules-only-experimental-v1"),
    )
    response = {
        "probability": str(result.probability),
        "evidence_sufficiency": str(result.evidence_sufficiency),
        "independent_source_count": result.independent_source_count,
        "uncertainty": str(result.uncertainty),
        "rule_version": result.rule_version,
        "model_version": result.model_version,
        "explanation": list(result.explanation),
        "requires_human_verdict": result.requires_human_verdict,
    }
    if payload.get("persist"):
        persisted = IntelligenceService(dsn=_postgres_dsn()).score(
            tenant_pub_id=payload["tenant_pub_id"],
            investigation_pub_id=payload["investigation_pub_id"],
            content_feature_score=Decimal(str(payload["content_feature_score"])),
            propagation_feature_score=Decimal(str(payload["propagation_feature_score"])),
            circular_citation_risk=Decimal(str(payload["circular_citation_risk"])),
            workflow_operation_id=f"{activity.info().workflow_id}/score",
        )
        persisted_result = persisted["result"]
        response = {
            "probability": str(persisted_result.probability),
            "evidence_sufficiency": str(persisted_result.evidence_sufficiency),
            "independent_source_count": persisted_result.independent_source_count,
            "uncertainty": str(persisted_result.uncertainty),
            "rule_version": persisted_result.rule_version,
            "model_version": persisted_result.model_version,
            "explanation": list(persisted_result.explanation),
            "requires_human_verdict": persisted_result.requires_human_verdict,
            "score_pub_id": persisted["score_pub_id"],
        }
    return response


@activity.defn
async def persist_investigation_verdict_activity(payload: dict[str, Any]) -> dict[str, Any]:
    verdict = payload["human_verdict"]
    verdict_pub_id = IntelligenceService(dsn=_postgres_dsn()).verdict(
        tenant_pub_id=payload["tenant_pub_id"],
        investigation_pub_id=payload["investigation_pub_id"],
        verdict=verdict["verdict"],
        reviewer_pub_id=verdict["reviewer_pub_id"],
        rationale=verdict.get("rationale", "Temporal human verdict"),
        workflow_operation_id=f"{activity.info().workflow_id}/verdict",
    )
    return {"verdict_pub_id": verdict_pub_id, "state": "decided"}


def _kpi_to_dict(cell: Any) -> dict[str, Any]:
    return {
        "metric": cell.metric,
        "value": str(cell.value) if cell.value is not None else None,
        "numerator": cell.numerator,
        "denominator": cell.denominator,
        "state": cell.state.value,
        "metric_version": cell.metric_version,
        "scorer_version": cell.scorer_version,
        "filter_hash": cell.filter_hash,
        "trace_token": cell.trace_token,
        "contributing_answer_pub_ids": list(cell.contributing_answer_pub_ids),
        "advisory": cell.advisory,
        "reason": cell.reason,
    }


def _postgres_dsn() -> str:
    return os.getenv(
        "S02_POSTGRES_DSN",
        "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform",
    )


def _provenance_from_payload(payload: dict[str, Any]) -> RedactedProvenance:
    return RedactedProvenance(
        platform_account_pub_id=payload.get("platform_account_pub_id"),
        browser_profile_version_pub_id=payload.get("browser_profile_version_pub_id"),
        session_event_pub_id=payload.get("session_event_pub_id"),
        channel=CaptureChannel(payload.get("channel", "api")),
        authorization_scope=tuple(payload.get("authorization_scope", [])),
        adapter_version=payload["adapter_version"],
        capture_time=datetime.fromisoformat(payload["capture_time"].replace("Z", "+00:00")),
        access_class=AccessClass(payload.get("access_class", "customer_private")),
        authorized_session_capture=bool(payload.get("authorized_session_capture")),
    )


def _report_service() -> ReportService:
    store = ContentAddressedObjectStore(
        endpoint=os.getenv("GEO_MINIO_ENDPOINT", "http://127.0.0.1:19000"),
        access_key=os.getenv("GEO_MINIO_ACCESS_KEY", "geo"),
        secret_key=os.getenv("GEO_MINIO_SECRET_KEY", "geo_dev_only_password"),
    )
    store.ensure_bucket()
    evidence = EvidenceService(dsn=_postgres_dsn(), store=store)
    return ReportService(dsn=_postgres_dsn(), evidence=evidence)


def _audit_evidence_access(
    *,
    tenant_pub_id: str,
    resource_pub_id: str,
    request_id: str,
    action: str,
    outcome: str,
    reason: str,
) -> None:
    with psycopg.connect(_postgres_dsn()) as connection:
        connection.execute(
            """
            INSERT INTO evidence.evidence_access_audit
              (tenant_pub_id,resource_pub_id,action,outcome,request_id,data)
            VALUES (%s,%s,%s,%s,%s,%s)
            """,
            (
                tenant_pub_id,
                resource_pub_id,
                action,
                outcome,
                request_id,
                json.dumps({"reason": reason}),
            ),
        )
