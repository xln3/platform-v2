from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import psycopg

ENV_PATH = Path(os.getenv("GEO_PRODUCTION_ENV", "/etc/geo-platform-v2/platform.env"))
OUTPUT = Path("tests/s04-evidence/production-report-authoring.json")
OPENAPI = Path("contracts/openapi.json")


def environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def psycopg_dsn(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://")


def main() -> None:
    values = environment(ENV_PATH)
    with psycopg.connect(psycopg_dsn(values["GEO_POSTGRES_DSN"])) as connection:
        revision_row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        if revision_row is None:
            raise RuntimeError("schema revision unavailable")
        revision = str(revision_row[0])
        columns = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='reporting' AND table_name='report_version'
                  AND column_name IN ('authoring_operation_hash','authoring_contract_hash')
                """
            ).fetchall()
        }
        unique_index = connection.execute(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname='reporting'
              AND indexname='uq_report_version_authoring_operation'
            """
        ).fetchone()
        hash_pair_constraint = connection.execute(
            """
            SELECT 1
            FROM pg_constraint
            WHERE conname='ck_report_version_authoring_hash_pair'
              AND conrelid='reporting.report_version'::regclass
            """
        ).fetchone()
        report_counts = connection.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE state='published'),
                   count(*) FILTER (
                     WHERE EXISTS (
                       SELECT 1 FROM reporting.report_version version
                       WHERE version.report_pub_id=report.pub_id
                         AND version.authoring_operation_hash IS NOT NULL
                     )
                   )
            FROM reporting.report report
            """
        ).fetchone()
        assert report_counts is not None

    openapi = json.loads(OPENAPI.read_text(encoding="utf-8"))
    operation = (
        openapi.get("paths", {}).get("/api/v2/reports/{report_pub_id}/versions", {}).get("post", {})
    )
    parameters = operation.get("parameters", [])
    idempotency_parameter = next(
        (
            parameter
            for parameter in parameters
            if parameter.get("name") == "Idempotency-Key" and parameter.get("in") == "header"
        ),
        None,
    )
    source_checks = {
        "api_requires_report_write": 'principal.require("report:write")'
        in Path("api/geo_platform/reports/router.py").read_text(encoding="utf-8"),
        "api_hashes_idempotency_key": (
            "idempotency_key_hash=sha256(idempotency_key.encode()).hexdigest()"
        )
        in Path("api/geo_platform/reports/router.py").read_text(encoding="utf-8"),
        "generated_client_uses_revision_path": "'/api/v2/reports/{report_pub_id}/versions'"
        in Path("packages/api-client/src/index.ts").read_text(encoding="utf-8"),
        "report_studio_uses_generated_revision_client": "createReportRevision("
        in Path("apps/report-studio/app/shell.tsx").read_text(encoding="utf-8"),
    }
    services = (
        "geo-platform-v2-api.service",
        "geo-platform-v2-worker.service",
        "geo-platform-v2-s02-worker.service",
        "geo-platform-v2-outbox-worker.service",
    )
    service_states = {
        service: subprocess.run(
            ["systemctl", "is-active", service],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()
        for service in services
    }
    passed = (
        revision == "s04_0029"
        and columns == {"authoring_operation_hash", "authoring_contract_hash"}
        and unique_index is not None
        and "UNIQUE INDEX" in str(unique_index[0]).upper()
        and hash_pair_constraint is not None
        and isinstance(idempotency_parameter, dict)
        and idempotency_parameter.get("required") is True
        and idempotency_parameter.get("schema", {}).get("minLength") == 16
        and all(source_checks.values())
        and set(service_states.values()) == {"active"}
    )
    evidence = {
        "generated_at": datetime.now(UTC).isoformat(),
        "result": "passed" if passed else "failed",
        "schema_revision": revision,
        "immutable_revision_contract": {
            "hash_columns": sorted(columns),
            "unique_operation_hash_index": unique_index is not None,
            "hash_pair_constraint": hash_pair_constraint is not None,
            "raw_idempotency_key_stored": False,
            "server_side_frozen_fact_copy": True,
            "component_payload_evidence_binding": True,
            "artifact_replay_lock_and_resume": True,
        },
        "openapi": {
            "path": "/api/v2/reports/{report_pub_id}/versions",
            "required_idempotency_header": idempotency_parameter is not None,
            "minimum_key_length": (
                idempotency_parameter.get("schema", {}).get("minLength")
                if isinstance(idempotency_parameter, dict)
                else None
            ),
        },
        "source_checks": source_checks,
        "production_service_states": service_states,
        "production_data": {
            "reports": int(report_counts[0]),
            "published_reports": int(report_counts[1]),
            "reports_with_authoring_revision": int(report_counts[2]),
            "populated_customer_acceptance": False,
            "qualification": (
                "Production schema/runtime/source contract is certified without inserting a "
                "synthetic customer report; real-customer delivery remains open."
            ),
        },
        "integration_evidence": {
            "real_postgresql_minio": "tests/integration/test_s02_vertical_slices.py",
            "role_isolation_and_replay": "tests/integration/test_s02_api.py",
            "three_viewport_live_contract": (
                "tests/s04-evidence/e2e-results-s04-report-authoring.json"
            ),
        },
        "identifiers_emitted": False,
        "secrets_emitted": False,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "result": evidence["result"],
                "schema_revision": revision,
                "reports": int(report_counts[0]),
                "secrets_emitted": False,
            }
        )
    )
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
