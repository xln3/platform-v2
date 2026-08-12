from __future__ import annotations

import argparse
import hashlib
import json
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).parents[1]
DEFAULT_OUTPUT = ROOT / "tests/s04-evidence/production-release-s04-0029.json"
DEFAULT_POSTGRES_BACKUP = ROOT / ".production-backups/20260727T035755Z/postgres-pre-s04-0029.dump"
DEFAULT_STATIC_BACKUP = ROOT / ".production-backups/20260727T035755Z/static-builds-pre-s04-0029.tar"
SYSTEMD_UNITS = (
    "geo-platform-v2-api",
    "geo-platform-v2-worker",
    "geo-platform-v2-s02-worker",
    "geo-platform-v2-outbox-worker",
    "geo-platform-v2-business-metrics",
    "geo-platform-v2-alert-receiver",
    "geosys",
)
PRODUCTION_CONTAINERS = frozenset(
    {
        "geo-platform-v2-production-alertmanager-1",
        "geo-platform-v2-production-alloy-1",
        "geo-platform-v2-production-clickhouse-1",
        "geo-platform-v2-production-grafana-1",
        "geo-platform-v2-production-loki-1",
        "geo-platform-v2-production-minio-1",
        "geo-platform-v2-production-otel-collector-1",
        "geo-platform-v2-production-postgres-1",
        "geo-platform-v2-production-prometheus-1",
        "geo-platform-v2-production-redis-1",
        "geo-platform-v2-production-temporal-1",
        "geo-platform-v2-production-temporal-postgres-1",
        "geo-platform-v2-production-vault-1",
    }
)
ROUTE_EXPECTATIONS = {
    "/platform/customer/": 200,
    "/platform/operations/": 200,
    "/platform/reports/": 200,
    "/platform/intelligence/": 200,
    "/api/v2/health": 200,
    "/portal": 200,
    "/ops": 200,
    "/client": 200,
    "/api/health": 200,
    "/geo": 401,
}
PRODUCTION_BROWSER_TOTAL = 48
PRODUCTION_MOCK_SCAN_TOTAL = 29


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def acceptance_summary_passed(report: dict[str, Any], *, expected_total: int) -> bool:
    return report.get("summary") == {
        "total": expected_total,
        "passed": expected_total,
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_evidence(path: Path) -> dict[str, object]:
    metadata = path.stat()
    return {
        "path": str(path.relative_to(ROOT)),
        "bytes": metadata.st_size,
        "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
        "sha256": sha256(path),
    }


def service_states() -> dict[str, str]:
    states: dict[str, str] = {}
    for unit in SYSTEMD_UNITS:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            check=False,
            capture_output=True,
            text=True,
        )
        states[unit] = result.stdout.strip()
    return states


def container_states() -> dict[str, object]:
    result = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            "name=geo-platform-v2-production-",
            "--format",
            "{{json .}}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    names = {str(row["Names"]) for row in rows}
    return {
        "expected": len(PRODUCTION_CONTAINERS),
        "running": sum(row.get("State") == "running" for row in rows),
        "healthy": sum("(healthy)" in str(row.get("Status", "")) for row in rows),
        "without_healthcheck": sum("(healthy)" not in str(row.get("Status", "")) for row in rows),
        "names": sorted(names),
        "exact_inventory": names == PRODUCTION_CONTAINERS,
    }


def route_states(client: httpx.Client, base_url: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path, expected in ROUTE_EXPECTATIONS.items():
        response = client.get(f"{base_url}{path}")
        rows.append(
            {
                "path": path,
                "status": response.status_code,
                "expected_status": expected,
                "redirected": response.is_redirect,
                "passed": response.status_code == expected and not response.is_redirect,
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://127.0.0.1:8443")
    parser.add_argument("--public-base-url", default="https://39.105.175.14:8443")
    parser.add_argument("--postgres-backup", type=Path, default=DEFAULT_POSTGRES_BACKUP)
    parser.add_argument("--static-backup", type=Path, default=DEFAULT_STATIC_BACKUP)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    runtime = load_json(ROOT / "tests/s04-evidence/production-runtime-data-counts.json")
    rls = load_json(ROOT / "tests/s04-evidence/production-rls-certification.json")
    browser = load_json(ROOT / "tests/s04-evidence/production-browser-acceptance.json")
    mock_scan = load_json(ROOT / "tests/s04-evidence/production-mock-scan.json")
    quality = load_json(ROOT / "tests/s04-evidence/full-quality-certification.json")
    unified = load_json(ROOT / "tests/s04-evidence/unified-completion-audit.json")
    manifest = load_json(ROOT / "contracts/generated-manifest.json")
    openapi = load_json(ROOT / "contracts/openapi.json")

    with httpx.Client(verify=False, follow_redirects=False, timeout=15) as client:
        routes = route_states(client, args.base_url.rstrip("/"))
        health = client.get(f"{args.base_url.rstrip('/')}/api/v2/health").json()

    services = service_states()
    containers = container_states()
    postgres_backup = backup_evidence(args.postgres_backup)
    static_backup = backup_evidence(args.static_backup)
    certified_backup = rls["predeployment_backup"]
    assertions = {
        "api_version": (
            health.get("status") == "ok"
            and health.get("service") == "geo-platform-v2"
            and health.get("version") == "0.1.0-s04"
        ),
        "schema_revision": runtime["schema_version"] == rls["schema_revision"] == "s04_0029",
        "routes_and_legacy_coexistence": all(row["passed"] for row in routes),
        "all_services_active": set(services.values()) == {"active"},
        "exact_container_inventory_running": (
            containers["exact_inventory"] and containers["running"] == containers["expected"] == 13
        ),
        "postgres_backup_matches_rls_certificate": (
            postgres_backup["bytes"] == certified_backup["postgres_dump_bytes"]
            and postgres_backup["mode"] == certified_backup["backup_mode"]
            and postgres_backup["sha256"] == certified_backup["postgres_dump_sha256"]
            and certified_backup["postgres_16_catalog_validation"] == "passed"
        ),
        "static_backup_restricted": static_backup["mode"] == "0600",
        "openapi_97_paths_without_drift": (
            len(openapi["paths"]) == 97
            and manifest["contracts/openapi.json"] == sha256(ROOT / "contracts/openapi.json")
            and manifest["packages/api-client/src/schema.generated.ts"]
            == sha256(ROOT / "packages/api-client/src/schema.generated.ts")
        ),
        "quality_passed": quality["result"] == "passed",
        "production_browser_48_of_48": acceptance_summary_passed(
            browser, expected_total=PRODUCTION_BROWSER_TOTAL
        ),
        "production_mock_scan_29_of_29": acceptance_summary_passed(
            mock_scan, expected_total=PRODUCTION_MOCK_SCAN_TOTAL
        ),
        "unified_gate_truth_preserved": (
            unified["section_18"]["satisfied"] == 7
            and unified["section_18"]["total"] == 10
            and not unified["section_18"]["all_satisfied"]
            and len(unified["open_requirements"]) == 6
        ),
    }
    result = (
        "passed_source_owned_release_external_gates_open" if all(assertions.values()) else "failed"
    )
    evidence = {
        "generated_at": datetime.now(UTC).isoformat(),
        "result": result,
        "production_urls": {
            "customer": f"{args.public_base_url.rstrip('/')}/platform/customer/",
            "operations": f"{args.public_base_url.rstrip('/')}/platform/operations/",
            "reports": f"{args.public_base_url.rstrip('/')}/platform/reports/",
            "intelligence": f"{args.public_base_url.rstrip('/')}/platform/intelligence/",
            "api": f"{args.public_base_url.rstrip('/')}/api/v2/",
            "certification_base": args.base_url.rstrip("/"),
        },
        "api": health,
        "schema_revision": runtime["schema_version"],
        "migration_watermark_count": runtime["counts"]["watermarks"],
        "runtime_counts": runtime["counts"],
        "clickhouse_projection": runtime["clickhouse_projection"],
        "services": services,
        "containers": containers,
        "routes": routes,
        "legacy_route_switched": False,
        "backups": {
            "postgres": {
                **postgres_backup,
                "postgres_16_catalog_entries": certified_backup["postgres_16_catalog_entries"],
                "postgres_16_catalog_validation": certified_backup[
                    "postgres_16_catalog_validation"
                ],
            },
            "static": static_backup,
        },
        "openapi": {
            "paths": len(openapi["paths"]),
            "manifest": manifest,
        },
        "quality": {
            "python_tests": quality["static_and_unit_suite"]["python_tests_passed"],
            "typescript_node_tests": quality["static_and_unit_suite"]["typescript_tests_passed"],
            "browser_e2e": quality["browser_e2e"],
            "production_browser": browser["summary"],
            "production_mock_scan": mock_scan["summary"],
        },
        "unified_completion": {
            "result": unified["result"],
            "section_18": unified["section_18"],
            "open_requirement_ids": [
                requirement["id"] for requirement in unified["open_requirements"]
            ],
        },
        "assertions": assertions,
        "secret_material_in_evidence": False,
        "goal_status": "active",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    if result == "failed":
        raise RuntimeError("production_release_certification_failed")
    print(
        json.dumps(
            {
                "result": result,
                "schema_revision": evidence["schema_revision"],
                "containers": containers["running"],
                "services": len(services),
                "open_requirements": len(unified["open_requirements"]),
            }
        )
    )


if __name__ == "__main__":
    main()
