import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
from geo_platform.business_metrics import collect_business_metrics

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


def test_business_metrics_aggregate_stale_state_without_tenant_labels() -> None:
    suffix = uuid.uuid4().hex
    tenant_id = uuid.uuid4()
    tenant_pub_id = f"tnt_alert_{suffix[:16]}"
    event_id = f"evt_business_alert_{suffix}"
    workflow_id = f"workflow-business-alert-{suffix}"
    signal_workflow_id = f"signal-business-alert-{suffix}"
    old = datetime.now(UTC) - timedelta(hours=2)
    baseline = collect_business_metrics(POSTGRES_DSN)
    try:
        with psycopg.connect(POSTGRES_DSN) as connection:
            connection.execute(
                """
                INSERT INTO platform.tenant (id,pub_id,name,state,created_at,updated_at)
                VALUES (%s,%s,'Business alert integration','active',%s,%s)
                """,
                (tenant_id, tenant_pub_id, old, old),
            )
            connection.execute(
                """
                INSERT INTO integration.workflow_start_command (
                  command_id,tenant_pub_id,workflow_type,workflow_id,task_queue,payload,
                  state,created_at,updated_at
                ) VALUES (%s,%s,'answer_analysis',%s,'test','{}','pending',%s,%s)
                """,
                (uuid.uuid4(), tenant_pub_id, workflow_id, old, old),
            )
            connection.execute(
                """
                INSERT INTO integration.workflow_signal_command (
                  command_id,tenant_pub_id,workflow_id,signal_name,args,trace_context,
                  state,created_at,updated_at,idempotency_key_hash,contract_hash
                ) VALUES (%s,%s,%s,'cancel','[]','{}','pending',%s,%s,%s,%s)
                """,
                (
                    uuid.uuid4(),
                    tenant_pub_id,
                    signal_workflow_id,
                    old,
                    old,
                    hashlib.sha256(f"key-{suffix}".encode()).hexdigest(),
                    hashlib.sha256(f"contract-{suffix}".encode()).hexdigest(),
                ),
            )
            connection.execute(
                """
                INSERT INTO integration.outbox_event (
                  event_id,tenant_pub_id,event_type,aggregate_pub_id,trace_id,payload,
                  occurred_at
                ) VALUES (
                  %s,%s,'collection.run.completed',%s,'test',
                  '{"analysis_admission":"missing_brand"}',%s
                )
                """,
                (event_id, tenant_pub_id, f"run_{suffix}", old),
            )
        observed = collect_business_metrics(POSTGRES_DSN)
        assert observed.tenant_count == baseline.tenant_count + 1
        assert observed.workflow_start_stale == baseline.workflow_start_stale + 1
        assert observed.workflow_signal_stale == baseline.workflow_signal_stale + 1
        assert (
            observed.analysis_admission_backlog["missing_brand"]
            == baseline.analysis_admission_backlog["missing_brand"] + 1
        )
        assert set(observed.analysis_admission_backlog) == {
            "not_requested",
            "missing_brand",
            "missing_completed_answers",
            "partial_fanout",
            "unknown",
        }
    finally:
        with psycopg.connect(POSTGRES_DSN) as connection:
            connection.execute(
                "DELETE FROM integration.workflow_signal_command WHERE tenant_pub_id=%s",
                (tenant_pub_id,),
            )
            connection.execute(
                "DELETE FROM integration.workflow_start_command WHERE tenant_pub_id=%s",
                (tenant_pub_id,),
            )
            connection.execute(
                "DELETE FROM integration.outbox_event WHERE tenant_pub_id=%s",
                (tenant_pub_id,),
            )
            connection.execute("DELETE FROM platform.tenant WHERE id=%s", (tenant_id,))


def test_business_alert_query_indexes_exist() -> None:
    expected = {
        "ix_collection_run_business_alert",
        "ix_session_lease_business_alert",
        "ix_revocation_request_business_alert",
        "ix_report_delivery_business_alert",
        "ix_completion_outbox_business_alert",
    }
    with psycopg.connect(POSTGRES_DSN) as connection:
        indexes = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT indexname FROM pg_indexes
                WHERE indexname=ANY(%s)
                """,
                (list(expected),),
            ).fetchall()
        }
    assert indexes == expected


def test_business_alert_snapshot_is_fixed_aggregate_and_worker_only() -> None:
    with psycopg.connect(POSTGRES_DSN) as connection:
        function = connection.execute(
            """
            SELECT procedure.prosecdef,procedure.proconfig
            FROM pg_proc procedure
            JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace
            WHERE namespace.nspname='integration'
              AND procedure.proname='business_alert_snapshot'
            """
        ).fetchone()
        public_execute_row = connection.execute(
            """
            SELECT has_function_privilege(
              'public','integration.business_alert_snapshot()','EXECUTE'
            )
            """
        ).fetchone()
        assert public_execute_row is not None
        public_execute = public_execute_row[0]
        rows = connection.execute(
            """
            SELECT metric,dimension,value
            FROM integration.business_alert_snapshot()
            """
        ).fetchall()
        existing_roles = {
            str(row[0])
            for row in connection.execute(
                "SELECT rolname FROM pg_roles WHERE rolname IN ('geo_api','geo_worker')"
            ).fetchall()
        }
        role_privileges: dict[str, bool] = {}
        for role in existing_roles:
            privilege_row = connection.execute(
                """
                SELECT has_function_privilege(
                  %s,'integration.business_alert_snapshot()','EXECUTE'
                )
                """,
                (role,),
            ).fetchone()
            assert privilege_row is not None
            role_privileges[role] = bool(privilege_row[0])
    assert function == (True, ["search_path=pg_catalog"])
    assert public_execute is False
    assert role_privileges.get("geo_worker", True) is True
    assert role_privileges.get("geo_api", False) is False
    assert len(rows) == 13
    assert {row[0] for row in rows} == {
        "tenant_count",
        "workflow_start_stale",
        "workflow_signal_stale",
        "collection_run_stalled",
        "revocation_stalled",
        "expired_session_leases",
        "report_delivery_overdue",
        "analysis_admission_backlog",
        "analytics_outbox_quarantined",
    }
    assert all(isinstance(row[1], str) and isinstance(row[2], int) for row in rows)
