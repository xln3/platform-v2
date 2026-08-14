from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass, field

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


def _psycopg_dsn(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def collect_business_metrics(dsn: str) -> BusinessMetricsSnapshot:
    snapshot = BusinessMetricsSnapshot()
    with psycopg.connect(_psycopg_dsn(dsn), row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        rows = connection.execute(
            "SELECT metric,dimension,value FROM integration.business_alert_snapshot()"
        ).fetchall()
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
