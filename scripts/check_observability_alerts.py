from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).parents[1]
OBSERVABILITY = ROOT / "deploy/production/observability"

EXPECTED_ALERTS = {
    "GeoPlatformV2ApiDown",
    "GeoBusinessMetricsExporterDown",
    "GeoBusinessMetricsCollectionFailed",
    "GeoApiServerErrorRateHigh",
    "GeoApiP95LatencyHigh",
    "GeoWorkflowStartBacklogStale",
    "GeoWorkflowSignalBacklogStale",
    "GeoCollectionRunStalled",
    "GeoRevocationStalled",
    "GeoExpiredSessionLease",
    "GeoCollectionAnalysisAdmissionBlocked",
    # 20260808 INV-1 接入（6a26a4a）新增的 outbox 毒消息兜底告警，基线同步补齐。
    "GeoAnalyticsOutboxBacklog",
    "GeoAnalyticsOutboxQuarantined",
    "GeoReportDeliveryConfirmationOverdue",
}
ALLOWED_RULE_LABELS = {"severity", "category", "service"}
FORBIDDEN_TEXT = (
    "tenant_pub_id",
    "account_pub_id",
    "project_pub_id",
    "workflow_id",
    "recipient_pub_id",
    "authorization",
    "cookie",
    "token",
)


def _yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path.name}_must_be_mapping")
    return value


def main() -> None:
    rules = _yaml(OBSERVABILITY / "business-alerts.yaml")
    alert_rules = [
        rule
        for group in rules.get("groups", [])
        for rule in group.get("rules", [])
        if isinstance(rule, dict) and "alert" in rule
    ]
    names = {str(rule["alert"]) for rule in alert_rules}
    if names != EXPECTED_ALERTS:
        raise SystemExit(
            f"business_alert_rule_drift missing={sorted(EXPECTED_ALERTS - names)} "
            f"extra={sorted(names - EXPECTED_ALERTS)}"
        )
    for rule in alert_rules:
        labels = rule.get("labels", {})
        annotations = rule.get("annotations", {})
        if set(labels) != ALLOWED_RULE_LABELS:
            raise SystemExit(f"business_alert_label_drift alert={rule['alert']}")
        serialized = json.dumps(
            {"labels": labels, "annotations": annotations},
            sort_keys=True,
        ).lower()
        if "{{" in serialized or any(value in serialized for value in FORBIDDEN_TEXT):
            raise SystemExit(f"business_alert_sensitive_template alert={rule['alert']}")

    prometheus = _yaml(OBSERVABILITY / "prometheus.yaml")
    rule_files = prometheus.get("rule_files", [])
    scrape_jobs = {item.get("job_name"): item for item in prometheus.get("scrape_configs", [])}
    alertmanager_targets = [
        target
        for manager in prometheus.get("alerting", {}).get("alertmanagers", [])
        for static in manager.get("static_configs", [])
        for target in static.get("targets", [])
    ]
    if "/etc/prometheus/rules/*.yaml" not in rule_files:
        raise SystemExit("prometheus_business_rules_not_loaded")
    if "geo-platform-v2-business-metrics" not in scrape_jobs:
        raise SystemExit("business_metrics_scrape_missing")
    if alertmanager_targets != ["127.0.0.1:19093"]:
        raise SystemExit("alertmanager_target_not_loopback")

    alertmanager = _yaml(OBSERVABILITY / "alertmanager.yaml")
    webhook_configs = [
        webhook
        for receiver in alertmanager.get("receivers", [])
        for webhook in receiver.get("webhook_configs", [])
    ]
    if webhook_configs != [{"url": "http://127.0.0.1:18091/alerts", "send_resolved": True}]:
        raise SystemExit("alert_receiver_route_drift")

    compose = (ROOT / "deploy/production/compose.yaml").read_text(encoding="utf-8")
    required_compose = (
        "prom/alertmanager:v0.28.1",
        "--web.listen-address=127.0.0.1:19093",
        "./observability/business-alerts.yaml:/etc/prometheus/rules/business-alerts.yaml:ro",
    )
    if any(marker not in compose for marker in required_compose):
        raise SystemExit("production_alertmanager_compose_drift")

    dashboard = json.loads(
        (OBSERVABILITY / "grafana-provisioning/dashboards/geo-platform-v2.json").read_text(
            encoding="utf-8"
        )
    )
    dashboard_expressions = {
        str(target.get("expr"))
        for panel in dashboard.get("panels", [])
        for target in panel.get("targets", [])
    }
    for marker in (
        'sum(ALERTS{alertstate="firing"})',
        "geo_business_expired_session_leases",
        "sum(geo_business_collection_analysis_admission_backlog)",
    ):
        if marker not in dashboard_expressions:
            raise SystemExit("grafana_business_alert_panel_missing")

    print(
        {
            "result": "passed",
            "alert_rules": len(names),
            "identifier_labels": 0,
            "notification_route": "loopback_alertmanager_to_loki_receiver",
        }
    )


if __name__ == "__main__":
    main()
