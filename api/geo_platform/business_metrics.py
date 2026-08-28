from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass, field
from typing import Any

import psycopg
import structlog
from prometheus_client import Gauge, start_http_server
from psycopg.rows import dict_row
from sqlalchemy import select

from .analytics.outbox import ANALYTICS_EVENT_TYPES
from .collection.account_models import CollectionRegion
from .collection.relay_probe import probe_collection_region
from .config import get_settings
from .logging import configure_logging
from .tenancy.database import SessionLocal

log = structlog.get_logger()

# relay 巡检（2026-08-13，采集账号治理 s06_0022）：借 15s 指标循环做调度，
# 每 region 每 10 分钟巡一次（内存记 last 时间，进程重启即全量重巡——可接受，
# 单次巡检与手工 probe 同语义）。失败才推送（推送在 probe_collection_region 内）。
_REGION_PROBE_INTERVAL_S = 600.0
_region_probe_last: dict[str, float] = {}

ADMISSION_REASONS = (
    "not_requested",
    "missing_brand",
    "missing_completed_answers",
    "partial_fanout",
    "unknown",
)

# 分析 outbox 告警 label 词表：事件类型单源=analytics/outbox.py
# ANALYTICS_EVENT_TYPES；"unknown" 是词表外事件的钳位桶（防 label 基数失控）。
OUTBOX_EVENT_TYPE_LABELS = (*ANALYTICS_EVENT_TYPES, "unknown")

COLLECTION_ANALYSIS_ADMISSION = Gauge(
    "geo_business_collection_analysis_admission_backlog",
    "Completed collection events awaiting an admitted analysis fan-out",
    ("reason",),
)
ANALYTICS_OUTBOX_BACKLOG = Gauge(
    "geo_business_analytics_outbox_backlog",
    "Analytics outbox events unpublished for at least fifteen minutes",
    ("event_type",),
)
ANALYTICS_OUTBOX_QUARANTINED = Gauge(
    "geo_business_analytics_outbox_quarantined",
    "Analytics outbox events quarantined after repeated projection failures",
    ("event_type",),
)
WORKFLOW_START_STALE = Gauge(
    "geo_business_workflow_start_stale",
    "Workflow start commands not dispatched within five minutes",
)
WORKFLOW_SIGNAL_STALE = Gauge(
    "geo_business_workflow_signal_stale",
    "Workflow signal commands not delivered within five minutes",
)
COLLECTION_RUN_STALLED = Gauge(
    "geo_business_collection_run_stalled",
    "Nonterminal collection runs unchanged for at least one hour",
)
REVOCATION_STALLED = Gauge(
    "geo_business_revocation_stalled",
    "Account revocation requests unchanged for at least fifteen minutes",
)
EXPIRED_SESSION_LEASES = Gauge(
    "geo_business_expired_session_leases",
    "Expired session leases that have not been released",
)
REPORT_DELIVERY_OVERDUE = Gauge(
    "geo_business_report_delivery_confirmation_overdue",
    "Published report deliveries unconfirmed for at least seven days",
)
METRICS_V2_OUTBOX_BACKLOG = Gauge(
    "geo_metrics_v2_outbox_backlog",
    "Pending or dispatching V2 metrics workflow commands",
)
METRICS_V2_EVALUATION_LAG = Gauge(
    "geo_metrics_v2_evaluation_lag_seconds",
    "Age in seconds of the oldest pending V2 recompute job",
)
METRICS_V2_SNAPSHOT_DURATION = Gauge(
    "geo_metrics_v2_snapshot_build_duration_seconds",
    "Most recent successful V2 snapshot build duration",
)
METRICS_V2_SNAPSHOT_FAILURES = Gauge(
    "geo_metrics_v2_snapshot_build_failures_total",
    "Cumulative failed V2 snapshot jobs retained in PostgreSQL",
)
METRICS_V2_BACKFILL_REMAINING = Gauge(
    "geo_metrics_v2_backfill_remaining_answers",
    "Captured answers without any V2 semantic manifest",
)
METRICS_V2_PUBLICATION_GENERATION = Gauge(
    "geo_metrics_v2_publication_generation",
    "Highest V2 publication pointer generation",
)
METRICS_V2_HASH_MISMATCH = Gauge(
    "geo_metrics_v2_hash_mismatch_total",
    "Persisted V2 jobs failed with a hash or reconciliation mismatch",
)
METRICS_V2_UNKNOWN_RATIO = Gauge(
    "geo_metrics_v2_unknown_ratio",
    "Aggregate unknown-answer ratio across latest V2 snapshots",
)
METRICS_V2_COLLECTION_COVERAGE = Gauge(
    "geo_metrics_v2_collection_coverage",
    "Minimum collection coverage across latest V2 snapshots",
)
METRICS_V2_SEMANTIC_COVERAGE = Gauge(
    "geo_metrics_v2_semantic_coverage",
    "Minimum semantic coverage across latest V2 snapshots",
)
METRICS_V2_LEGACY_CONSUMER_ATTEMPTS = Gauge(
    "geo_metrics_v2_legacy_consumer_attempt_total",
    "Detected official V2 consumer attempts to use a legacy calculation path",
)
METRICS_V2_OFFICIAL_POINTER_INVALID = Gauge(
    "geo_metrics_v2_official_pointer_invalid",
    "Official pointers that do not reference a complete ready snapshot set",
)
SEMANTIC_DECISION_V2_BACKLOG = Gauge(
    "geo_semantic_decision_v2_backlog",
    "Pending or running V2 semantic decision jobs",
)
SEMANTIC_DECISION_V2_DURATION = Gauge(
    "geo_semantic_decision_v2_duration_seconds",
    "Average retained V2 semantic decision attempt duration",
)
SEMANTIC_DECISION_V2_ATTEMPTS = Gauge(
    "geo_semantic_decision_v2_attempts_total",
    "Cumulative V2 semantic decision attempts retained in PostgreSQL",
)
SEMANTIC_DECISION_V2_ABSTENTION_RATIO = Gauge(
    "geo_semantic_decision_v2_abstention_ratio",
    "Abstained share of terminal V2 semantic decision jobs",
)
SEMANTIC_DECISION_V2_DISAGREEMENT_RATIO = Gauge(
    "geo_semantic_decision_v2_disagreement_ratio",
    "Judge-disagreement share of retained V2 semantic decisions",
)
SEMANTIC_DECISION_V2_INVALID_OUTPUT = Gauge(
    "geo_semantic_decision_v2_invalid_output_total",
    "Cumulative invalid structured V2 judge outputs",
)
SEMANTIC_DECISION_V2_EVIDENCE_FAILURE_RATIO = Gauge(
    "geo_semantic_decision_v2_evidence_failure_ratio",
    "Evidence-retrieval-failure share of retained V2 decisions",
)
SEMANTIC_DECISION_V2_CALIBRATION_DRIFT = Gauge(
    "geo_semantic_decision_v2_calibration_drift",
    "Maximum recorded semantic decision calibration drift",
)
SEMANTIC_DECISION_V2_COST = Gauge(
    "geo_semantic_decision_v2_cost_total",
    "Cumulative retained semantic judge cost in the recorded currency basis",
)
SEMANTIC_DECISION_V2_FALLBACK_BLOCKED = Gauge(
    "geo_semantic_decision_v2_fallback_blocked_total",
    "Attempts blocked instead of using an uncalibrated semantic fallback",
)
REPORT_V2_VALIDATION_FAILURES = Gauge(
    "geo_report_v2_snapshot_validation_failures_total",
    "Formal report attempts rejected by V2 snapshot binding validation",
)
COLLECTION_SUCCESS = Gauge(
    "geo_business_metrics_collection_success",
    "Whether the most recent business metrics collection completed successfully",
)
COLLECTION_LAST_SUCCESS = Gauge(
    "geo_business_metrics_collection_last_success_unixtime",
    "Unix timestamp of the most recent successful business metrics collection",
)


@dataclass
class BusinessMetricsSnapshot:
    tenant_count: int = 0
    workflow_start_stale: int = 0
    workflow_signal_stale: int = 0
    collection_run_stalled: int = 0
    revocation_stalled: int = 0
    expired_session_leases: int = 0
    report_delivery_overdue: int = 0
    analysis_admission_backlog: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(ADMISSION_REASONS, 0)
    )
    analytics_outbox_backlog: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(OUTBOX_EVENT_TYPE_LABELS, 0)
    )
    analytics_outbox_quarantined: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(OUTBOX_EVENT_TYPE_LABELS, 0)
    )
    metrics_v2: dict[str, float] = field(default_factory=dict)


def _psycopg_dsn(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def collect_business_metrics(dsn: str) -> BusinessMetricsSnapshot:
    snapshot = BusinessMetricsSnapshot()
    with psycopg.connect(_psycopg_dsn(dsn), row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        rows = connection.execute(
            "SELECT metric,dimension,value FROM integration.business_alert_snapshot()"
        ).fetchall()
        _collect_metrics_v2(connection, snapshot)
    scalar_fields = {
        "tenant_count": "tenant_count",
        "workflow_start_stale": "workflow_start_stale",
        "workflow_signal_stale": "workflow_signal_stale",
        "collection_run_stalled": "collection_run_stalled",
        "revocation_stalled": "revocation_stalled",
        "expired_session_leases": "expired_session_leases",
        "report_delivery_overdue": "report_delivery_overdue",
    }
    for row in rows:
        metric = str(row["metric"])
        value = int(row["value"])
        if metric == "analysis_admission_backlog":
            reason = str(row["dimension"])
            if reason not in ADMISSION_REASONS:
                reason = "unknown"
            snapshot.analysis_admission_backlog[reason] += value
        elif metric in ("analytics_outbox_backlog", "analytics_outbox_quarantined"):
            event_type = str(row["dimension"])
            if event_type not in OUTBOX_EVENT_TYPE_LABELS:
                event_type = "unknown"
            getattr(snapshot, metric)[event_type] += value
        elif metric in scalar_fields:
            setattr(snapshot, scalar_fields[metric], value)
    return snapshot


def _collect_metrics_v2(
    connection: psycopg.Connection[Any], snapshot: BusinessMetricsSnapshot
) -> None:
    """Collect low-cardinality operational aggregates; never customer KPI values."""

    tables = connection.execute(
        """
        SELECT to_regclass('analytics.metric_snapshot_v2') IS NOT NULL AS snapshots,
               to_regclass('analytics.metric_recompute_job_v2') IS NOT NULL AS jobs,
               to_regclass('analytics.metric_publication_v2') IS NOT NULL AS publications,
               to_regclass('analytics.semantic_decision_job_v2') IS NOT NULL AS decisions,
               to_regclass('analytics.semantic_decision_attempt_v2') IS NOT NULL AS attempts,
               to_regclass('analytics.semantic_decision_record_v2') IS NOT NULL AS records,
               to_regclass('analytics.answer_semantic_manifest_v2') IS NOT NULL AS manifests,
               to_regclass('reporting.formal_report_production') IS NOT NULL AS reports
        """
    ).fetchone()
    assert tables is not None
    values: dict[str, float] = {
        "outbox_backlog": 0,
        "evaluation_lag_seconds": 0,
        "snapshot_duration_seconds": 0,
        "snapshot_failures_total": 0,
        "backfill_remaining_answers": 0,
        "publication_generation": 0,
        "hash_mismatch_total": 0,
        "unknown_ratio": 0,
        "collection_coverage": 1,
        "semantic_coverage": 1,
        "legacy_consumer_attempt_total": 0,
        "official_pointer_invalid": 0,
        "decision_backlog": 0,
        "decision_duration_seconds": 0,
        "decision_attempts_total": 0,
        "decision_abstention_ratio": 0,
        "decision_disagreement_ratio": 0,
        "decision_invalid_output_total": 0,
        "decision_evidence_failure_ratio": 0,
        "decision_calibration_drift": 0,
        "decision_cost_total": 0,
        "decision_fallback_blocked_total": 0,
        "report_validation_failures_total": 0,
    }
    outbox = connection.execute(
        """
        SELECT count(*)::float8 AS value
        FROM integration.workflow_start_command
        WHERE workflow_type IN ('metric_snapshot_set_v2','metrics_backfill_v2')
          AND state IN ('pending','dispatching')
        """
    ).fetchone()
    values["outbox_backlog"] = float(outbox["value"] if outbox else 0)
    if tables["jobs"]:
        row = connection.execute(
            """
            SELECT COALESCE(EXTRACT(EPOCH FROM now()-
                            min(created_at) FILTER (WHERE status='pending')),0)::float8 AS lag,
                   COALESCE((SELECT EXTRACT(EPOCH FROM completed_at-started_at)
                             FROM analytics.metric_recompute_job_v2
                             WHERE status='succeeded' AND completed_at IS NOT NULL
                             ORDER BY completed_at DESC,pub_id DESC LIMIT 1),0)::float8 AS duration,
                   count(*) FILTER (WHERE status='failed')::float8 AS failures,
                   count(*) FILTER (
                     WHERE status='failed' AND failure_codes &&
                       ARRAY['hash_mismatch','reconciliation_failed','snapshot_set_hash_mismatch']
                   )::float8 AS hash_mismatches
            FROM analytics.metric_recompute_job_v2
            """
        ).fetchone()
        assert row is not None
        values.update(
            evaluation_lag_seconds=float(row["lag"]),
            snapshot_duration_seconds=float(row["duration"]),
            snapshot_failures_total=float(row["failures"]),
            hash_mismatch_total=float(row["hash_mismatches"]),
        )
    if tables["snapshots"]:
        row = connection.execute(
            """
            SELECT COALESCE(sum(unknown_answer_count)::numeric/
                            NULLIF(sum(candidate_answer_count),0),0)::float8 AS unknown_ratio,
                   COALESCE(min(collection_coverage),1)::float8 AS collection_coverage,
                   COALESCE(min(semantic_coverage),1)::float8 AS semantic_coverage
            FROM analytics.metric_snapshot_v2
            WHERE created_at >= now()-interval '30 days'
            """
        ).fetchone()
        assert row is not None
        values.update(
            unknown_ratio=float(row["unknown_ratio"]),
            collection_coverage=float(row["collection_coverage"]),
            semantic_coverage=float(row["semantic_coverage"]),
        )
    if tables["publications"]:
        row = connection.execute(
            """
            SELECT COALESCE(max(publication.generation),0)::float8 AS generation,
                   count(*) FILTER (
                     WHERE publication.publication_channel='official'
                       AND (snapshot_set.pub_id IS NULL OR snapshot_set.state<>'ready')
                   )::float8 AS invalid
            FROM analytics.metric_publication_v2 publication
            LEFT JOIN analytics.metric_snapshot_set_v2 snapshot_set
              ON snapshot_set.tenant_pub_id=publication.tenant_pub_id
             AND snapshot_set.pub_id=publication.snapshot_set_pub_id
            """
        ).fetchone()
        assert row is not None
        values["publication_generation"] = float(row["generation"])
        values["official_pointer_invalid"] = float(row["invalid"])
    if tables["manifests"]:
        row = connection.execute(
            """
            SELECT count(*)::float8 AS value
            FROM analytics.answer answer
            WHERE NOT EXISTS (
              SELECT 1 FROM analytics.answer_semantic_manifest_v2 manifest
              WHERE manifest.tenant_pub_id=answer.tenant_pub_id
                AND manifest.answer_pub_id=answer.pub_id
            )
            """
        ).fetchone()
        values["backfill_remaining_answers"] = float(row["value"] if row else 0)
    if tables["decisions"]:
        row = connection.execute(
            """
            SELECT count(*) FILTER (WHERE status IN ('pending','running'))::float8 AS backlog,
                   COALESCE(count(*) FILTER (WHERE status='abstained')::numeric/
                     NULLIF(count(*) FILTER (
                       WHERE status IN ('succeeded','abstained','review_required','failed')
                     ),0),0)::float8 AS abstention
            FROM analytics.semantic_decision_job_v2
            """
        ).fetchone()
        assert row is not None
        values["decision_backlog"] = float(row["backlog"])
        values["decision_abstention_ratio"] = float(row["abstention"])
    if tables["attempts"]:
        row = connection.execute(
            """
            SELECT count(*)::float8 AS attempts,
                   COALESCE(avg(latency_ms),0)::float8/1000 AS duration,
                   count(*) FILTER (WHERE validation_status='invalid')::float8 AS invalid,
                   COALESCE(sum(cost_amount),0)::float8 AS cost,
                   count(*) FILTER (
                     WHERE reason_codes && ARRAY['heuristic_fallback_forbidden',
                                                  'model_unavailable_for_policy',
                                                  'model_budget_exhausted']
                   )::float8 AS fallback_blocked
            FROM analytics.semantic_decision_attempt_v2
            """
        ).fetchone()
        assert row is not None
        values.update(
            decision_attempts_total=float(row["attempts"]),
            decision_duration_seconds=float(row["duration"]),
            decision_invalid_output_total=float(row["invalid"]),
            decision_cost_total=float(row["cost"]),
            decision_fallback_blocked_total=float(row["fallback_blocked"]),
        )
    if tables["records"]:
        row = connection.execute(
            """
            SELECT COALESCE(count(*) FILTER (WHERE 'judge_disagreement'=ANY(reason_codes))::numeric/
                            NULLIF(count(*),0),0)::float8 AS disagreement,
                   COALESCE(count(*) FILTER (
                     WHERE 'evidence_retrieval_failed'=ANY(reason_codes)
                   )::numeric/NULLIF(count(*),0),0)::float8 AS evidence_failure
            FROM analytics.semantic_decision_record_v2
            """
        ).fetchone()
        assert row is not None
        values["decision_disagreement_ratio"] = float(row["disagreement"])
        values["decision_evidence_failure_ratio"] = float(row["evidence_failure"])
    if tables["reports"]:
        row = connection.execute(
            """
            SELECT count(*) FILTER (
              WHERE error_code LIKE 'metric_snapshot_%'
                 OR error_code LIKE 'legacy_metric_%'
            )::float8 AS value
            FROM reporting.formal_report_production
            """
        ).fetchone()
        values["report_validation_failures_total"] = float(row["value"] if row else 0)
    snapshot.metrics_v2 = values


def apply_business_metrics(snapshot: BusinessMetricsSnapshot) -> None:
    WORKFLOW_START_STALE.set(snapshot.workflow_start_stale)
    WORKFLOW_SIGNAL_STALE.set(snapshot.workflow_signal_stale)
    COLLECTION_RUN_STALLED.set(snapshot.collection_run_stalled)
    REVOCATION_STALLED.set(snapshot.revocation_stalled)
    EXPIRED_SESSION_LEASES.set(snapshot.expired_session_leases)
    REPORT_DELIVERY_OVERDUE.set(snapshot.report_delivery_overdue)
    for reason in ADMISSION_REASONS:
        COLLECTION_ANALYSIS_ADMISSION.labels(reason=reason).set(
            snapshot.analysis_admission_backlog[reason]
        )
    for event_type in OUTBOX_EVENT_TYPE_LABELS:
        ANALYTICS_OUTBOX_BACKLOG.labels(event_type=event_type).set(
            snapshot.analytics_outbox_backlog[event_type]
        )
        ANALYTICS_OUTBOX_QUARANTINED.labels(event_type=event_type).set(
            snapshot.analytics_outbox_quarantined[event_type]
        )
    v2 = snapshot.metrics_v2
    METRICS_V2_OUTBOX_BACKLOG.set(v2.get("outbox_backlog", 0))
    METRICS_V2_EVALUATION_LAG.set(v2.get("evaluation_lag_seconds", 0))
    METRICS_V2_SNAPSHOT_DURATION.set(v2.get("snapshot_duration_seconds", 0))
    METRICS_V2_SNAPSHOT_FAILURES.set(v2.get("snapshot_failures_total", 0))
    METRICS_V2_BACKFILL_REMAINING.set(v2.get("backfill_remaining_answers", 0))
    METRICS_V2_PUBLICATION_GENERATION.set(v2.get("publication_generation", 0))
    METRICS_V2_HASH_MISMATCH.set(v2.get("hash_mismatch_total", 0))
    METRICS_V2_UNKNOWN_RATIO.set(v2.get("unknown_ratio", 0))
    METRICS_V2_COLLECTION_COVERAGE.set(v2.get("collection_coverage", 1))
    METRICS_V2_SEMANTIC_COVERAGE.set(v2.get("semantic_coverage", 1))
    METRICS_V2_LEGACY_CONSUMER_ATTEMPTS.set(v2.get("legacy_consumer_attempt_total", 0))
    METRICS_V2_OFFICIAL_POINTER_INVALID.set(v2.get("official_pointer_invalid", 0))
    SEMANTIC_DECISION_V2_BACKLOG.set(v2.get("decision_backlog", 0))
    SEMANTIC_DECISION_V2_DURATION.set(v2.get("decision_duration_seconds", 0))
    SEMANTIC_DECISION_V2_ATTEMPTS.set(v2.get("decision_attempts_total", 0))
    SEMANTIC_DECISION_V2_ABSTENTION_RATIO.set(v2.get("decision_abstention_ratio", 0))
    SEMANTIC_DECISION_V2_DISAGREEMENT_RATIO.set(v2.get("decision_disagreement_ratio", 0))
    SEMANTIC_DECISION_V2_INVALID_OUTPUT.set(v2.get("decision_invalid_output_total", 0))
    SEMANTIC_DECISION_V2_EVIDENCE_FAILURE_RATIO.set(v2.get("decision_evidence_failure_ratio", 0))
    SEMANTIC_DECISION_V2_CALIBRATION_DRIFT.set(v2.get("decision_calibration_drift", 0))
    SEMANTIC_DECISION_V2_COST.set(v2.get("decision_cost_total", 0))
    SEMANTIC_DECISION_V2_FALLBACK_BLOCKED.set(v2.get("decision_fallback_blocked_total", 0))
    REPORT_V2_VALIDATION_FAILURES.set(v2.get("report_validation_failures_total", 0))
    COLLECTION_SUCCESS.set(1)
    COLLECTION_LAST_SUCCESS.set(time.time())


def probe_due_collection_regions(now_mono: float) -> None:
    """对到期的 collection_region 逐条巡检（best-effort：任何失败只日志，绝不
    影响指标导出主链路；表未迁移/DB 不可达时整轮跳过）。"""
    try:
        with SessionLocal() as session:
            regions = list(session.scalars(select(CollectionRegion)))
            for region in regions:
                last = _region_probe_last.get(region.region_gb)
                if last is not None and now_mono - last < _REGION_PROBE_INTERVAL_S:
                    continue
                _region_probe_last[region.region_gb] = now_mono
                try:
                    probe_collection_region(session, region.region_gb)
                    session.commit()
                except Exception as exc:  # noqa: BLE001 — 单 region 失败不拖垮整轮
                    session.rollback()
                    log.warning(
                        "region_probe_failed",
                        region_gb=region.region_gb,
                        error_type=type(exc).__name__,
                        error=str(exc)[:200],
                    )
    except Exception as exc:  # noqa: BLE001 — 巡检是旁路，绝不影响 metrics 循环
        log.warning("region_probe_sweep_failed", error_type=type(exc).__name__)


async def run_exporter() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = structlog.get_logger()
    address = os.getenv("GEO_BUSINESS_METRICS_ADDRESS", "127.0.0.1")
    port = int(os.getenv("GEO_BUSINESS_METRICS_PORT", "18092"))
    interval = max(5.0, float(os.getenv("GEO_BUSINESS_METRICS_INTERVAL_SECONDS", "15")))
    dsn = settings.worker_postgres_dsn or settings.postgres_dsn
    start_http_server(port, addr=address)
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopping.set)
    log.info("business_metrics_exporter_started", address=address, port=port)
    while not stopping.is_set():
        try:
            snapshot = await asyncio.to_thread(collect_business_metrics, dsn)
            apply_business_metrics(snapshot)
        except Exception as error:
            COLLECTION_SUCCESS.set(0)
            log.error(
                "business_metrics_collection_failed",
                error_type=type(error).__name__,
            )
        try:  # relay 巡检钩子：独立 try，绝不影响 COLLECTION_SUCCESS 口径
            await asyncio.to_thread(probe_due_collection_regions, time.monotonic())
        except Exception as error:
            log.warning(
                "region_probe_hook_failed",
                error_type=type(error).__name__,
            )
        try:
            await asyncio.wait_for(stopping.wait(), timeout=interval)
        except TimeoutError:
            pass
    log.info("business_metrics_exporter_stopped")


if __name__ == "__main__":
    asyncio.run(run_exporter())
