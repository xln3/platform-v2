import os
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from fastapi.testclient import TestClient
from geo_platform.analytics.service import AnalyticsService
from geo_platform.main import app
from geo_platform.tenancy.ids import new_pub_id
from psycopg.rows import dict_row

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from tools.migration.migrate_legacy_core import CoreMigrator

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


def _create_legacy_completion() -> tuple[str, str, str, str]:
    subject = "migration-lineage-" + secrets.token_hex(8)
    with TestClient(app) as client:
        bootstrap = client.post(
            "/api/v2/identity/bootstrap",
            headers={"X-Bootstrap-Secret": "development-bootstrap"},
            json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
        )
        assert bootstrap.status_code == 201
        tenant_pub_id = str(bootstrap.json()["tenant_pub_id"])
        headers = {
            "X-Tenant-Id": tenant_pub_id,
            "X-Actor-Id": subject,
            "X-Actor-Role": "admin",
            "Idempotency-Key": "project-" + secrets.token_hex(16),
        }
        project = client.post(
            "/api/v2/projects",
            headers=headers,
            json={"name": "Migration lineage", "customer_name": "Migration lineage"},
        )
        assert project.status_code == 201
        project_pub_id = str(project.json()["pub_id"])
        headers["Idempotency-Key"] = "freeze-" + secrets.token_hex(16)
        config = client.post(
            f"/api/v2/projects/{project_pub_id}/config/freeze",
            headers=headers,
            json={
                "query_groups": [{"name": "Core", "items": [{"text": "What is GEO?"}]}],
                "regions": ["CN-BJ"],
                "models": ["fixed"],
                "modes": ["fast"],
                "frequency": "manual",
                "effective_at": datetime.now(UTC).isoformat(),
            },
        )
        assert config.status_code == 201
        config_pub_id = str(config.json()["pub_id"])
        headers["Idempotency-Key"] = "run-" + secrets.token_hex(16)
        accepted = client.post(
            "/api/v2/collection/runs",
            headers=headers,
            json={
                "project_pub_id": project_pub_id,
                "config_version_pub_id": config_pub_id,
                "requires_intervention": False,
            },
        )
        assert accepted.status_code == 202
        run_pub_id = str(accepted.json()["workflow_id"]).rsplit("/", 1)[-1]

    event_id = new_pub_id("evt")
    with psycopg.connect(POSTGRES_DSN) as connection:
        run = connection.execute(
            "SELECT id,tenant_id,workflow_id FROM platform.collection_run WHERE pub_id=%s",
            (run_pub_id,),
        ).fetchone()
        assert run is not None
        connection.execute(
            "DELETE FROM integration.workflow_start_command WHERE workflow_id=%s",
            (run[2],),
        )
        connection.execute(
            """
            UPDATE platform.collection_run
            SET workflow_id=%s,state='completed',total_tasks=1,completed_tasks=1
            WHERE pub_id=%s
            """,
            (f"legacy-history/{run_pub_id}", run_pub_id),
        )
        connection.execute(
            """
            INSERT INTO platform.collection_task (
              id,pub_id,tenant_id,version,created_at,updated_at,run_id,business_key,
              matrix_json,state,attempt_count,answer_text
            ) VALUES (%s,%s,%s,1,now(),now(),%s,%s,'{}','done',1,'Migrated answer')
            """,
            (uuid.uuid4(), new_pub_id("tsk"), run[1], run[0], f"legacy:{run_pub_id}"),
        )
        connection.execute(
            """
            INSERT INTO integration.outbox_event (
              event_id,tenant_pub_id,event_type,aggregate_pub_id,trace_id,payload,occurred_at
            ) VALUES (%s,%s,'collection.run.completed',%s,'migration-test','{}',now())
            """,
            (event_id, tenant_pub_id, run_pub_id),
        )
    return tenant_pub_id, project_pub_id, config_pub_id, run_pub_id


def _reconcile(migrator: CoreMigrator) -> dict[str, int]:
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as connection:
        connection.execute(
            """
            INSERT INTO integration.migration_run (
              id,pub_id,source_system,source_snapshot_sha256,source_snapshot_at,state,
              started_at,summary
            ) VALUES (%s,%s,'test-migration',%s,now(),'running',now(),'{}')
            ON CONFLICT (id) DO NOTHING
            """,
            (migrator.run_id, migrator.run_pub_id, migrator.snapshot_hash),
        )
        return migrator.reconcile_migrated_completion_events(connection)


def test_migrated_completion_waits_for_full_v2_rebuild_then_converges(tmp_path: Path) -> None:
    tenant_pub_id, project_pub_id, config_pub_id, run_pub_id = _create_legacy_completion()
    snapshot = tmp_path / "legacy-snapshot.db"
    snapshot.write_bytes(b"migration-lineage-contract")
    migrator = CoreMigrator(snapshot, dsn=POSTGRES_DSN)

    assert _reconcile(migrator) == {"seen": 1, "written": 0, "skipped": 1}

    answer_pub_id = new_pub_id("ans")
    AnalyticsService(dsn=POSTGRES_DSN).analyze_and_persist(
        tenant_pub_id=tenant_pub_id,
        project_pub_id=project_pub_id,
        answer_pub_id=answer_pub_id,
        answer_text="Migrated answer",
        brand="Migration lineage",
        competitors=(),
        citations=(),
        dimensions={
            "question_pub_id": new_pub_id("qry"),
            "query_text": "What is GEO?",
            "model": "fixed",
            "region": "CN-BJ",
            "mode": "fast",
            "run_pub_id": run_pub_id,
            "config_version_pub_id": config_pub_id,
        },
        own_domains=(),
        provenance=RedactedProvenance(
            platform_account_pub_id=None,
            browser_profile_version_pub_id=None,
            session_event_pub_id=None,
            channel=CaptureChannel.API,
            authorization_scope=("historical:migrated-read",),
            adapter_version="legacy-migration-v1",
            capture_time=datetime.now(UTC),
            access_class=AccessClass.CUSTOMER_PRIVATE,
        ),
        scorer_version="migration-reconciliation-test",
        metric_version="migration-reconciliation-test",
        model_version="migration-reconciliation-test",
    )

    assert _reconcile(migrator) == {"seen": 1, "written": 1, "skipped": 0}
    assert _reconcile(migrator) == {"seen": 0, "written": 0, "skipped": 0}
    with psycopg.connect(POSTGRES_DSN) as connection:
        event = connection.execute(
            """
            SELECT payload,published_at IS NOT NULL,attempts
            FROM integration.outbox_event
            WHERE tenant_pub_id=%s AND aggregate_pub_id=%s
              AND event_type='collection.run.completed'
            """,
            (tenant_pub_id, run_pub_id),
        ).fetchone()
    assert event is not None
    assert event[0]["analysis_admission"] == "migrated_v2_rebuild"
    assert event[0]["analysis_expected"] == event[0]["analysis_rebuilt"] == 1
    assert event[0]["analysis_commands"] == 0
    assert event[1:] == (True, 1)
