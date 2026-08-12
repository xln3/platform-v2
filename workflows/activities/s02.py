from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from geo_platform.analytics.service import AnalyticsService
from geo_platform.brandrank.service import fetch_project_brandrank_domain
from geo_platform.config import get_settings
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.evidence.service import EvidenceService
from geo_platform.evidence.session_gateway import SessionGatewayClient
from geo_platform.intelligence.service import IntelligenceService
from geo_platform.reports.formal_production import (
    FormalProductionInvalid,
    FormalReportProductionService,
)
from geo_platform.reports.service import ReportService
from geo_platform.tenancy.ids import new_pub_id
from geo_platform.tenancy.psycopg import tenant_connection
from temporalio import activity
from temporalio.exceptions import ApplicationError

from domain.brandrank import extract
from domain.brandrank.rules import load_domain
from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from domain.intelligence.core import EvidenceRelation, SourceAssessment, score_investigation
from domain.metrics.core import MetricRegistry
from domain.reporting.freeze import freeze_report
from domain.reporting.libreoffice import ReportRuntimeDependencyError, report_runtime_preflight
from domain.scoring.analyzer import CitationInput, analyze_answer


def _temporal_json_safe(value: Any) -> Any:
    """Convert formal activity results to Temporal's default JSON value set."""
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _temporal_json_safe(child) for key, child in value.items()}
    if isinstance(value, list | tuple):
        return [_temporal_json_safe(child) for child in value]
    return value


def _formal_activity_result(value: dict[str, Any]) -> dict[str, Any]:
    return {key: _temporal_json_safe(child) for key, child in value.items()}


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


_ERROR_CLASS_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Timeout)\b")


def _sanitize_extract_error(message: str, *, limit: int = 500) -> str:
    """落库 error 消毒：异常类名（ReadTimeout/ConnectError/...）一律抹为 <exc>。

    纪律（W3 任务书）：异常类名不落值——error 列只留稳定错误类别
    （api_error/bad_json/bad_shape 前缀）与冒号后语义，实现细节不进审计账目。
    """
    return _ERROR_CLASS_RE.sub("<exc>", message)[:limit]


def _record_brand_extract(
    dsn: str,
    *,
    tenant_pub_id: str,
    answer_pub_id: str,
    domain: str,
    brands: list[str],
    status: str,
    model: str,
    error: str | None,
) -> None:
    """answer_brand_extract 幂等落账：UNIQUE(tenant,answer,domain)+ON CONFLICT 重写。

    Temporal activity 重试/重放安全：同键重抽覆盖旧行，绝不重复落行。"""
    with tenant_connection(dsn, tenant_pub_id) as connection:
        connection.execute(
            """
            INSERT INTO analytics.answer_brand_extract
              (pub_id,tenant_pub_id,answer_pub_id,domain,brands,status,model,error,extracted_at)
            VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,now())
            ON CONFLICT (tenant_pub_id,answer_pub_id,domain) DO UPDATE SET
              brands=EXCLUDED.brands, status=EXCLUDED.status, model=EXCLUDED.model,
              error=EXCLUDED.error, extracted_at=EXCLUDED.extracted_at
            """,
            (
                new_pub_id("abx"),
                tenant_pub_id,
                answer_pub_id,
                domain,
                json.dumps(list(brands), ensure_ascii=False),
                status,
                model,
                error,
            ),
        )


@activity.defn
async def extract_brands_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """fanout 品牌抽取侧车（AnswerAnalysisWorkflow patch 门 brandrank-extract-v1 之后）。

    诚实纪律（INV-32 零合成，与 brand-visibility 端点同口径）：
    - 项目未设 brandrank_domain 真源 → 跳过 LLM，落 failed/domain_unset 标记行
      （选"落账留痕"而非"静默跳过"：每条 fanout 答案都有可审计的抽取账目，
      domain 列以 '' 占位保唯一键幂等，项目补设真源后按真 domain 另起新行）；
    - LLM 未配 key → failed/llm_disabled；LLM 单条失败 → failed+消毒后错误类别；
    - 绝不把失败伪装成空品牌列表，绝不阻塞分析主链（本 activity 自身不抛 LLM 类
      失败；仅 DB 等基础设施异常上抛，由 workflow 侧捕获降级为 warning）。
    - 非 fanout 持久化载荷（persist 缺省/缺 tenant/answer 标识，如直接起 workflow
      的调试载荷）→ skipped 不落账不烧 LLM。
    """
    tenant_pub_id = str(payload.get("tenant_pub_id") or "")
    answer_pub_id = str(payload.get("answer_pub_id") or "")
    project_pub_id = str(payload.get("project_pub_id") or "")
    text = str(payload.get("text") or "")
    if not payload.get("persist") or not tenant_pub_id or not answer_pub_id:
        return {"state": "skipped", "reason": "missing_context"}
    dsn = _postgres_dsn()

    def _failed(domain: str, model: str, error: str) -> dict[str, Any]:
        _record_brand_extract(
            dsn,
            tenant_pub_id=tenant_pub_id,
            answer_pub_id=answer_pub_id,
            domain=domain,
            brands=[],
            status="failed",
            model=model,
            error=error,
        )
        return {"state": "failed", "error": error, "domain": domain}

    domain = fetch_project_brandrank_domain(dsn, tenant_pub_id, project_pub_id)
    if not domain:
        return _failed("", "", "domain_unset")
    try:
        rules = load_domain(domain)
    except ValueError:
        # 真源列值非法（绕过 API 词表校验的直写）：fail-loud 落账，绝不臆造规则包
        return _failed(domain, "", "unknown_domain")
    cfg = extract.load_config()
    if cfg is None:
        return _failed(domain, "", "llm_disabled")
    model = cfg[3]
    try:
        # httpx 同步 client 阻塞调用挪 to_thread：activity 事件循环不被 60s LLM 冻结
        brands = await asyncio.to_thread(
            extract.extract_brands_with_llm,
            extract.default_client(),
            text,
            rules.category,
            model=model,
            rules=rules,
        )
    except extract.ExtractError as exc:
        return _failed(domain, model, _sanitize_extract_error(str(exc)))
    _record_brand_extract(
        dsn,
        tenant_pub_id=tenant_pub_id,
        answer_pub_id=answer_pub_id,
        domain=domain,
        brands=brands,
        status="ok",
        model=model,
        error=None,
    )
    return {
        "state": "ok",
        "domain": domain,
        "model": model,
        "brand_count": len(brands),
    }


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
async def preflight_formal_report_runtime_activity(payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    runtime = await asyncio.to_thread(report_runtime_preflight)
    return {"state": "ready", "libreoffice_version": runtime.version}


@activity.defn
async def produce_formal_report_activity(payload: dict[str, Any]) -> dict[str, Any]:
    activity.heartbeat({"stage": "formal_report_production_started"})
    service = _formal_report_service()
    tenant_pub_id = str(payload["tenant_pub_id"])
    production_pub_id = str(payload["formal_production_pub_id"])
    try:
        result = await asyncio.to_thread(
            service.produce,
            tenant_pub_id=tenant_pub_id,
            production_pub_id=production_pub_id,
        )
    except FormalProductionInvalid as exc:
        error_code = {
            "formal_evidence_requirements_not_met": "formal_evidence_requirements_not_met",
            "formal_fact_volume_exceeded": "formal_fact_volume_exceeded",
        }.get(str(exc), "production_failed")
        return _formal_activity_result(
            await asyncio.to_thread(
                service.mark_failed,
                tenant_pub_id=tenant_pub_id,
                production_pub_id=production_pub_id,
                error_code=error_code,
            )
        )
    except ReportRuntimeDependencyError:
        return _formal_activity_result(
            await asyncio.to_thread(
                service.mark_failed,
                tenant_pub_id=tenant_pub_id,
                production_pub_id=production_pub_id,
                error_code="libreoffice_dependency_missing",
            )
        )
    activity.heartbeat({"stage": "formal_report_production_persisted"})
    return _formal_activity_result(result)


@activity.defn
async def fail_formal_report_activity(payload: dict[str, Any]) -> dict[str, Any]:
    return _formal_activity_result(
        await asyncio.to_thread(
            _formal_report_service(ensure_bucket=False).mark_failed,
            tenant_pub_id=str(payload["tenant_pub_id"]),
            production_pub_id=str(payload["formal_production_pub_id"]),
            error_code=str(payload.get("error_code") or "production_failed"),
        )
    )


@activity.defn
async def finalize_formal_report_activity(payload: dict[str, Any]) -> dict[str, Any]:
    review = payload["review"]
    return _formal_activity_result(
        await asyncio.to_thread(
            _formal_report_service().finalize,
            tenant_pub_id=str(payload["tenant_pub_id"]),
            production_pub_id=str(payload["formal_production_pub_id"]),
            reviewer_pub_id=str(review["reviewer_pub_id"]),
            approved=bool(review["approved"]),
            rationale=str(review.get("rationale") or "Formal report review"),
            workflow_operation_id=f"{activity.info().workflow_id}/review",
        )
    )


@activity.defn
async def finalize_report_activity(payload: dict[str, Any]) -> dict[str, Any]:
    service = _report_service()
    decision = payload["review"]
    with tenant_connection(_postgres_dsn(), payload["tenant_pub_id"]) as connection:
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
        workflow_operation_id=f"{activity.info().workflow_id}/review",
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
    settings = get_settings()
    return os.getenv("S02_POSTGRES_DSN") or (
        settings.worker_postgres_dsn or settings.postgres_dsn
    ).replace("postgresql+psycopg://", "postgresql://")


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


def _formal_report_service(*, ensure_bucket: bool = True) -> FormalReportProductionService:
    store = ContentAddressedObjectStore(
        endpoint=os.getenv("GEO_MINIO_ENDPOINT", "http://127.0.0.1:19000"),
        access_key=os.getenv("GEO_MINIO_ACCESS_KEY", "geo"),
        secret_key=os.getenv("GEO_MINIO_SECRET_KEY", "geo_dev_only_password"),
    )
    if ensure_bucket:
        store.ensure_bucket()
    evidence = EvidenceService(dsn=_postgres_dsn(), store=store)
    return FormalReportProductionService(dsn=_postgres_dsn(), evidence=evidence)


def _audit_evidence_access(
    *,
    tenant_pub_id: str,
    resource_pub_id: str,
    request_id: str,
    action: str,
    outcome: str,
    reason: str,
) -> None:
    with tenant_connection(_postgres_dsn(), tenant_pub_id) as connection:
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
