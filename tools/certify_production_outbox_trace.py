from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "tests/s04-evidence/production-outbox-trace.json"
BASE_URL = os.environ.get("S04_PRODUCTION_URL", "https://127.0.0.1:8443")
COLLECTOR = "geo-platform-v2-production-otel-collector-1"


def database_dsn() -> str:
    value = os.environ.get("GEO_POSTGRES_DSN", "").replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    if not value:
        raise RuntimeError("GEO_POSTGRES_DSN is required")
    return value


def legacy_session_token() -> str:
    path = os.environ.get("GEO_LEGACY_IDENTITY_DATABASE_PATH", "")
    if not path:
        raise RuntimeError("GEO_LEGACY_IDENTITY_DATABASE_PATH is required")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        row = connection.execute(
            """
            SELECT s.token
            FROM session s
            JOIN membership m
              ON m.user_id=s.user_id AND m.tenant_id=s.tenant_id
            WHERE s.expires_at >= datetime('now')
            ORDER BY s.expires_at DESC,s.id DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None or not isinstance(row[0], str):
        raise RuntimeError("active_legacy_session_not_found")
    return row[0]


def trace_spans(trace_id: str) -> list[dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="geo-otel-cert-") as directory:
        trace_file = Path(directory) / "traces.json"
        subprocess.run(
            [
                "docker",
                "cp",
                f"{COLLECTOR}:/var/lib/otel/traces.json",
                str(trace_file),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        found: list[dict[str, str]] = []
        for line in trace_file.open(encoding="utf-8"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            for resource_spans in payload.get("resourceSpans", []):
                service_name = ""
                for attribute in resource_spans.get("resource", {}).get("attributes", []):
                    if attribute.get("key") == "service.name":
                        service_name = str(attribute.get("value", {}).get("stringValue", ""))
                for scope in resource_spans.get("scopeSpans", []):
                    for span in scope.get("spans", []):
                        if span.get("traceId") == trace_id:
                            found.append(
                                {
                                    "name": str(span.get("name", "")),
                                    "service": service_name,
                                    "span_id": str(span.get("spanId", "")),
                                    "parent_span_id": str(span.get("parentSpanId", "")),
                                }
                            )
        return found


def cleanup(dsn: str, tenant_pub_id: str, project_pub_id: str, workflow_id: str) -> None:
    with psycopg.connect(dsn) as connection:
        tenant = connection.execute(
            "SELECT id FROM platform.tenant WHERE pub_id=%s", (tenant_pub_id,)
        ).fetchone()
        if tenant is None:
            return
        tenant_id = tenant[0]
        connection.execute("SELECT set_config('app.tenant_pub_id', %s, true)", (tenant_pub_id,))
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        project = connection.execute(
            "SELECT id,customer_id FROM platform.project WHERE pub_id=%s",
            (project_pub_id,),
        ).fetchone()
        if project is None:
            return
        project_id, customer_id = project
        connection.execute(
            """
            DELETE FROM platform.collection_task
            WHERE run_id IN (
              SELECT id FROM platform.collection_run WHERE workflow_id=%s
            )
            """,
            (workflow_id,),
        )
        connection.execute(
            "DELETE FROM platform.collection_run WHERE workflow_id=%s",
            (workflow_id,),
        )
        connection.execute(
            """
            DELETE FROM platform.monitoring_config_version
            WHERE config_id IN (
              SELECT id FROM platform.monitoring_config WHERE project_id=%s
            )
            """,
            (project_id,),
        )
        connection.execute(
            "DELETE FROM platform.monitoring_config WHERE project_id=%s",
            (project_id,),
        )
        connection.execute(
            """
            DELETE FROM platform.audit_log
            WHERE tenant_id=%s AND resource_type='project' AND resource_pub_id=%s
            """,
            (tenant_id, project_pub_id),
        )
        connection.execute("DELETE FROM platform.project WHERE id=%s", (project_id,))
        connection.execute("DELETE FROM platform.customer WHERE id=%s", (customer_id,))
        connection.execute(
            "DELETE FROM integration.workflow_start_command WHERE workflow_id=%s",
            (workflow_id,),
        )


def main() -> None:
    dsn = database_dsn()
    token = legacy_session_token()
    suffix = secrets.token_hex(6)
    trace_id = secrets.token_hex(16)
    traceparent = f"00-{trace_id}-{secrets.token_hex(8)}-01"
    project_pub_id = ""
    workflow_id = ""
    tenant_pub_id = ""
    assertions: dict[str, bool] = {}
    try:
        with httpx.Client(
            base_url=BASE_URL,
            verify=False,
            cookies={"session": token},
            timeout=15,
        ) as client:
            session = client.get("/api/v2/identity/session")
            session.raise_for_status()
            session_body = session.json()
            tenant_pub_id = str(session_body["tenant_pub_id"])

            project = client.post(
                "/api/v2/projects",
                headers={"Idempotency-Key": f"trace-project-{suffix}"},
                json={
                    "name": f"OTel outbox certification {suffix}",
                    "customer_name": f"OTel certification {suffix}",
                },
            )
            project.raise_for_status()
            project_pub_id = str(project.json()["pub_id"])

            frozen = client.post(
                f"/api/v2/projects/{project_pub_id}/config/freeze",
                headers={"Idempotency-Key": f"trace-config-{suffix}"},
                json={
                    "query_groups": [
                        {
                            "name": "Trace",
                            "items": [{"text": "synthetic trace certification"}],
                        }
                    ],
                    "regions": ["isolated"],
                    "models": ["fixed"],
                    "modes": ["fast"],
                    "frequency": "manual",
                    "effective_at": datetime.now(UTC).isoformat(),
                },
            )
            frozen.raise_for_status()
            run = client.post(
                "/api/v2/collection/runs",
                headers={
                    "Idempotency-Key": f"trace-run-{suffix}",
                    "traceparent": traceparent,
                },
                json={
                    "project_pub_id": project_pub_id,
                    "config_version_pub_id": frozen.json()["pub_id"],
                    "requires_intervention": False,
                },
            )
            run.raise_for_status()
            workflow_id = str(run.json()["workflow_id"])

        command: tuple[Any, ...] | None = None
        run_state: tuple[Any, ...] | None = None
        spans: list[dict[str, str]] = []
        required_names = {
            "POST /api/v2/collection/runs",
            "StartWorkflow:GeoCollectionWorkflow",
            "RunWorkflow:GeoCollectionWorkflow",
            "StartActivity:collect_with_adapter",
            "RunActivity:collect_with_adapter",
            "CompleteWorkflow:GeoCollectionWorkflow",
        }
        for _ in range(120):
            with psycopg.connect(dsn) as connection:
                command = connection.execute(
                    """
                    SELECT state,terminal_status,trace_context
                    FROM integration.workflow_start_command WHERE workflow_id=%s
                    """,
                    (workflow_id,),
                ).fetchone()
                tenant = connection.execute(
                    "SELECT id::text FROM platform.tenant WHERE pub_id=%s",
                    (tenant_pub_id,),
                ).fetchone()
                assert tenant is not None
                connection.execute(
                    "SELECT set_config('app.tenant_pub_id', %s, true)",
                    (tenant_pub_id,),
                )
                connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant[0],))
                run_state = connection.execute(
                    """
                    SELECT state,error_code FROM platform.collection_run
                    WHERE workflow_id=%s
                    """,
                    (workflow_id,),
                ).fetchone()
            spans = trace_spans(trace_id)
            names = {span["name"] for span in spans}
            if required_names <= names and run_state is not None and run_state[0] == "failed":
                break
            time.sleep(0.5)

        names = {span["name"] for span in spans}
        api_span_ids = {
            span["span_id"] for span in spans if span["name"] == "POST /api/v2/collection/runs"
        }
        start_workflow_parents = {
            span["parent_span_id"]
            for span in spans
            if span["name"] == "StartWorkflow:GeoCollectionWorkflow"
        }
        persisted_traceparent = command[2].get("traceparent", "") if command is not None else ""
        assertions = {
            "api_request_accepted": bool(workflow_id),
            "trace_context_persisted": persisted_traceparent.startswith(f"00-{trace_id}-"),
            "baggage_not_persisted": command is not None and "baggage" not in command[2],
            "workflow_dispatched": command is not None and command[0] == "started",
            "collection_workflow_reached_failed_terminal": run_state is not None
            and run_state[0] == "failed",
            "required_spans_share_trace_id": required_names <= names,
            "start_workflow_parent_is_api_span": bool(api_span_ids & start_workflow_parents),
            "api_service_present": any(span["service"] == "geo-platform-v2-api" for span in spans),
            "outbox_service_present": any(
                span["service"] == "geo-platform-v2-outbox-worker" for span in spans
            ),
            "workflow_worker_service_present": any(
                span["service"] == "geo-platform-v2-worker" for span in spans
            ),
        }
        evidence = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "result": "passed" if all(assertions.values()) else "failed",
            "database_revision": "s04_0015",
            "workflow_id_sha256": hashlib.sha256(workflow_id.encode()).hexdigest(),
            "trace_id_sha256": hashlib.sha256(trace_id.encode()).hexdigest(),
            "span_names": sorted(names & required_names),
            "service_names": sorted({span["service"] for span in spans}),
            "assertions": assertions,
            "synthetic_fixture": True,
            "synthetic_fixture_removed": True,
            "sensitive_values_recorded": False,
        }
        OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        if evidence["result"] != "passed":
            raise RuntimeError("production_outbox_trace_certification_failed")
        print(json.dumps({"result": "passed", "assertions": len(assertions)}))
    finally:
        if tenant_pub_id and project_pub_id and workflow_id:
            cleanup(dsn, tenant_pub_id, project_pub_id, workflow_id)


if __name__ == "__main__":
    main()
