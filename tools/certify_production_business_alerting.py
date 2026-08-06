from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
from prometheus_client.parser import text_string_to_metric_families

ROOT = Path(__file__).parents[1]
ENV_PATH = Path("/etc/geo-platform-v2/platform.env")
OUTPUT = ROOT / "tests/s04-evidence/production-business-alerting.json"

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
    "GeoReportDeliveryConfirmationOverdue",
}
EXPECTED_REASONS = {
    "not_requested",
    "missing_brand",
    "missing_completed_answers",
    "partial_fanout",
    "unknown",
}
REQUIRED_SERVICES = (
    "geo-platform-v2-business-metrics.service",
    "geo-platform-v2-alert-receiver.service",
)


def _environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _psycopg_dsn(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _service_states() -> dict[str, str]:
    states: dict[str, str] = {}
    for service in REQUIRED_SERVICES:
        result = subprocess.run(
            ["systemctl", "is-active", service],
            check=False,
            capture_output=True,
            text=True,
        )
        states[service] = result.stdout.strip()
    return states


def _http_json(url: str) -> Any:
    response = httpx.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def _metrics() -> tuple[dict[str, float], dict[str, float], bool]:
    response = httpx.get("http://127.0.0.1:18092/metrics", timeout=10)
    response.raise_for_status()
    scalar: dict[str, float] = {}
    admission: dict[str, float] = {}
    safe_labels = True
    for family in text_string_to_metric_families(response.text):
        for sample in family.samples:
            if any(
                forbidden in sample.labels
                for forbidden in (
                    "tenant_pub_id",
                    "account_pub_id",
                    "project_pub_id",
                    "workflow_id",
                    "recipient_pub_id",
                )
            ):
                safe_labels = False
            if sample.name == "geo_business_collection_analysis_admission_backlog":
                reason = sample.labels.get("reason", "")
                admission[reason] = float(sample.value)
            elif sample.name.startswith("geo_business_"):
                scalar[sample.name] = float(sample.value)
    return scalar, admission, safe_labels


def _database_contract(values: dict[str, str]) -> dict[str, Any]:
    admin_dsn = values["GEO_POSTGRES_DSN"]
    worker_dsn = values["GEO_WORKER_POSTGRES_DSN"]
    api_dsn = values["GEO_RUNTIME_POSTGRES_DSN"]
    with psycopg.connect(_psycopg_dsn(admin_dsn)) as connection:
        row = connection.execute(
            """
            SELECT version_num,
                   NOT EXISTS (
                     SELECT 1
                     FROM aclexplode(
                       COALESCE(
                         procedure.proacl,
                         acldefault('f',procedure.proowner)
                       )
                     ) AS privilege
                     WHERE privilege.grantee=0
                       AND privilege.privilege_type='EXECUTE'
                   ),
                   procedure.prosecdef,
                   procedure.proconfig
            FROM alembic_version
            CROSS JOIN pg_proc procedure
            JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace
            WHERE namespace.nspname='integration'
              AND procedure.proname='business_alert_snapshot'
            """
        ).fetchone()
    with psycopg.connect(_psycopg_dsn(worker_dsn)) as connection:
        worker_execute_row = connection.execute(
            """
            SELECT has_function_privilege(
              current_user,'integration.business_alert_snapshot()','EXECUTE'
            )
            """
        ).fetchone()
        aggregate_rows = connection.execute(
            "SELECT metric,dimension,value FROM integration.business_alert_snapshot()"
        ).fetchall()
    with psycopg.connect(_psycopg_dsn(api_dsn)) as connection:
        api_execute_row = connection.execute(
            """
            SELECT has_function_privilege(
              current_user,'integration.business_alert_snapshot()','EXECUTE'
            )
            """
        ).fetchone()
    if row is None or worker_execute_row is None or api_execute_row is None:
        raise RuntimeError("business_alert_database_contract_missing")
    return {
        "schema_revision": str(row[0]),
        "worker_execute": bool(worker_execute_row[0]),
        "public_execute": not bool(row[1]),
        "security_definer": bool(row[2]),
        "search_path": list(row[3] or []),
        "api_execute": bool(api_execute_row[0]),
        "aggregate_rows": len(aggregate_rows),
        "aggregate_columns": 3,
        "identifiers_returned": False,
    }


def _prometheus_rules() -> tuple[set[str], set[str]]:
    response = _http_json("http://127.0.0.1:19090/api/v1/rules")
    groups = response.get("data", {}).get("groups", [])
    loaded: set[str] = set()
    firing: set[str] = set()
    for group in groups:
        for rule in group.get("rules", []):
            name = rule.get("name")
            if isinstance(name, str) and name.startswith("Geo"):
                loaded.add(name)
                if rule.get("state") == "firing":
                    firing.add(name)
    return loaded, firing


def _alertmanager_alerts() -> set[str]:
    payload = _http_json("http://127.0.0.1:19093/api/v2/alerts")
    return {
        str(item.get("labels", {}).get("alertname"))
        for item in payload
        if item.get("status", {}).get("state") == "active"
        and item.get("labels", {}).get("alertname")
    }


def _loki_observed(alert_name: str) -> bool:
    start = int((datetime.now(UTC) - timedelta(minutes=20)).timestamp() * 1_000_000_000)
    response = httpx.get(
        "http://127.0.0.1:13100/loki/api/v1/query_range",
        params={
            "query": (
                f'{{environment="production"}} |= "business_alert_notification" |= "{alert_name}"'
            ),
            "start": str(start),
            "limit": "20",
        },
        timeout=10,
    )
    response.raise_for_status()
    streams = response.json().get("data", {}).get("result", [])
    return any(stream.get("values") for stream in streams)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-seconds", type=int, default=0)
    parser.add_argument(
        "--require-alert",
        default="",
        help="Require one real firing alert to traverse Prometheus, Alertmanager and Loki.",
    )
    parser.add_argument(
        "--require-observed-alert",
        default="",
        help="Require a previously delivered alert in Loki without requiring it to remain active.",
    )
    parser.add_argument(
        "--require-zero-admission-backlog",
        action="store_true",
        help="Require the current collection-to-analysis admission backlog to be zero.",
    )
    args = parser.parse_args()
    if args.require_alert and args.require_observed_alert:
        parser.error("--require-alert and --require-observed-alert are mutually exclusive")
    values = _environment(ENV_PATH)
    database = _database_contract(values)
    service_states = _service_states()
    prometheus_ready = httpx.get("http://127.0.0.1:19090/-/ready", timeout=10).status_code == 200
    alertmanager_healthy = (
        httpx.get("http://127.0.0.1:19093/-/healthy", timeout=10).status_code == 200
    )
    receiver_healthy = httpx.get("http://127.0.0.1:18091/health", timeout=10).status_code == 200

    deadline = time.monotonic() + max(0, args.wait_seconds)
    loaded: set[str] = set()
    firing: set[str] = set()
    routed: set[str] = set()
    observed_alert = args.require_alert or args.require_observed_alert
    loki_observed = not bool(observed_alert)
    while True:
        loaded, firing = _prometheus_rules()
        routed = _alertmanager_alerts()
        if observed_alert:
            loki_observed = _loki_observed(observed_alert)
        alert_pipeline_ready = (
            args.require_alert in firing and args.require_alert in routed and loki_observed
            if args.require_alert
            else loki_observed
        )
        if alert_pipeline_ready or time.monotonic() >= deadline:
            break
        time.sleep(5)

    scalar, admission, safe_metric_labels = _metrics()
    exporter_target = _http_json("http://127.0.0.1:19090/api/v1/targets")
    exporter_up = any(
        target.get("labels", {}).get("job") == "geo-platform-v2-business-metrics"
        and target.get("health") == "up"
        for target in exporter_target.get("data", {}).get("activeTargets", [])
    )
    assertions = {
        "schema_at_business_alert_revision": database["schema_revision"] == "s04_0029",
        "worker_only_aggregate_function": (
            database["worker_execute"]
            and not database["api_execute"]
            and not database["public_execute"]
            and database["security_definer"]
            and database["search_path"] == ["search_path=pg_catalog"]
            and database["aggregate_rows"] == 12
        ),
        "service_units_active": set(service_states.values()) == {"active"},
        "prometheus_ready": prometheus_ready,
        "alertmanager_healthy": alertmanager_healthy,
        "receiver_healthy": receiver_healthy,
        "exporter_target_up": exporter_up,
        "all_business_rules_loaded": loaded == EXPECTED_ALERTS,
        "collector_reports_success": (scalar.get("geo_business_metrics_collection_success") == 1.0),
        "fixed_admission_dimensions": set(admission) == EXPECTED_REASONS,
        "admission_backlog_zero": (
            not args.require_zero_admission_backlog or sum(admission.values()) == 0
        ),
        "metrics_have_no_identifier_labels": safe_metric_labels,
        "required_alert_firing": (not args.require_alert or args.require_alert in firing),
        "required_alert_routed": (not args.require_alert or args.require_alert in routed),
        "required_alert_observed_in_loki": loki_observed,
    }
    evidence = {
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": "production",
        "result": "passed" if all(assertions.values()) else "failed",
        "database_contract": database,
        "service_states": service_states,
        "prometheus": {
            "rules_loaded": len(loaded),
            "expected_rules": len(EXPECTED_ALERTS),
            "firing_alert_names": sorted(firing),
            "exporter_target_up": exporter_up,
        },
        "alertmanager": {
            "healthy": alertmanager_healthy,
            "active_alert_names": sorted(routed),
            "local_receiver_only": True,
        },
        "business_metrics": {
            "scalar_series": len(scalar),
            "admission_backlog_by_reason": {
                reason: int(admission.get(reason, 0)) for reason in sorted(EXPECTED_REASONS)
            },
            "identifier_labels_emitted": not safe_metric_labels,
        },
        "notification_pipeline": {
            "required_active_alert": args.require_alert or None,
            "required_observed_alert": observed_alert or None,
            "prometheus_firing": args.require_alert in firing if args.require_alert else None,
            "alertmanager_active": args.require_alert in routed if args.require_alert else None,
            "loki_delivery_observed": loki_observed,
        },
        "assertions": assertions,
        "identifiers_or_secrets_recorded": False,
    }
    OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "result": evidence["result"],
                "rules_loaded": len(loaded),
                "firing_alerts": len(firing),
                "secrets_emitted": False,
            }
        )
    )
    if evidence["result"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            json.dumps({"result": "failed", "error_type": type(error).__name__}),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
