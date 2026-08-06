from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from certify_production_workflow_start_outbox import cleanup, dsn, insert_fixture
from geo_platform.analytics.clickhouse import ClickHouseWriter
from geo_platform.analytics.outbox import OutboxConsumer
from geo_platform.analytics.projection import AnalyticsProjection
from geo_platform.collection.workflow_outbox import WorkflowStartOutbox
from geo_platform.config import get_settings
from temporalio.client import Client
from temporalio.exceptions import ApplicationError

from workflows.activities.collection import (
    CollectionTaskInput,
    CollectionTaskResult,
    persist_collection_result,
    publish_downstream_event,
)

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "tests/s04-evidence/production-collection-analysis-fanout.json"


async def main() -> None:
    database_dsn = dsn()
    suffix = uuid.uuid4().hex[:10]
    tenant_pub_id, collection_workflow_id, payload = insert_fixture(
        database_dsn, suffix, enqueue=False
    )
    run_pub_id = str(payload["run_pub_id"])
    task = CollectionTaskInput(**payload["tasks"][0])
    analysis_workflow_id: str | None = None
    clickhouse: ClickHouseWriter | None = None
    try:
        with psycopg.connect(database_dsn) as connection:
            tenant_project = connection.execute(
                """
                SELECT run.tenant_id,run.project_id
                FROM platform.collection_run run WHERE run.pub_id=%s
                """,
                (run_pub_id,),
            ).fetchone()
            assert tenant_project is not None
            connection.execute(
                """
                INSERT INTO platform.brand
                  (id,pub_id,tenant_id,project_id,name,version,created_at,updated_at)
                VALUES (%s,%s,%s,%s,'Acme',1,%s,%s)
                """,
                (
                    uuid.uuid4(),
                    f"brd_fanout_{suffix}",
                    tenant_project[0],
                    tenant_project[1],
                    datetime.now(UTC),
                    datetime.now(UTC),
                ),
            )
        dlp_rejected = False
        try:
            persist_collection_result(
                tenant_pub_id,
                run_pub_id,
                CollectionTaskResult(
                    business_key=f"secret-{suffix}",
                    answer_text="Authorization: Bearer forbidden-production-probe",
                    screenshot_ref="probe://redacted",
                    quality_state="rejected",
                ),
                CollectionTaskInput(
                    business_key=f"secret-{suffix}",
                    query="secret probe",
                    model="fixed",
                    region="isolated",
                    mode="fast",
                ),
            )
        except ApplicationError:
            dlp_rejected = True
        persist_collection_result(
            tenant_pub_id,
            run_pub_id,
            CollectionTaskResult(
                business_key=task.business_key,
                answer_text="Acme is recommended in the controlled fanout probe.",
                screenshot_ref="probe://redacted",
                quality_state="accepted",
            ),
            task,
        )
        publish_result = publish_downstream_event(run_pub_id, tenant_pub_id, [task])
        with psycopg.connect(database_dsn) as connection:
            command = connection.execute(
                """
                SELECT workflow_id,payload,state
                FROM integration.workflow_start_command
                WHERE tenant_pub_id=%s AND workflow_type='answer_analysis'
                """,
                (tenant_pub_id,),
            ).fetchone()
            event = connection.execute(
                """
                SELECT payload,published_at IS NOT NULL FROM integration.outbox_event
                WHERE tenant_pub_id=%s AND event_type='collection.run.completed'
                """,
                (tenant_pub_id,),
            ).fetchone()
        assert command is not None and event is not None
        analysis_workflow_id = str(command[0])
        settings = get_settings()
        temporal = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        dispatcher = WorkflowStartOutbox(dsn=database_dsn, temporal=temporal)
        dispatched = await dispatcher.dispatch_one(analysis_workflow_id)
        analysis_result = await temporal.get_workflow_handle(analysis_workflow_id).result()
        with psycopg.connect(database_dsn) as connection:
            persisted = connection.execute(
                """
                SELECT
                  (SELECT count(*) FROM analytics.answer
                   WHERE tenant_pub_id=%s),
                  (SELECT count(*) FROM analytics.answer_analysis
                   WHERE tenant_pub_id=%s),
                  (SELECT count(*) FROM integration.outbox_event
                   WHERE tenant_pub_id=%s AND event_type='analytics.answer.analyzed')
                """,
                (tenant_pub_id, tenant_pub_id, tenant_pub_id),
            ).fetchone()
            lineage = connection.execute(
                """
                SELECT run_pub_id,config_version_pub_id,query_text
                FROM analytics.answer WHERE tenant_pub_id=%s
                """,
                (tenant_pub_id,),
            ).fetchone()
            analysis_event = connection.execute(
                """
                SELECT event_id,payload FROM integration.outbox_event
                WHERE tenant_pub_id=%s AND event_type='analytics.answer.analyzed'
                """,
                (tenant_pub_id,),
            ).fetchone()
        assert analysis_event is not None
        clickhouse = ClickHouseWriter(
            endpoint=settings.clickhouse_url,
            user=settings.clickhouse_user,
            password=settings.clickhouse_password,
        )
        projection_consumer = OutboxConsumer(
            dsn=database_dsn,
            consumer_name=f"s04-fanout-{suffix}",
            publish=AnalyticsProjection(clickhouse).publish,
        )
        projection_consumer.drain()
        safe_event_id = str(analysis_event[0]).replace("'", "''")
        projected_lineage = clickhouse._post(  # noqa: SLF001
            "SELECT run_pub_id FROM geo_analytics.answer_fact FINAL "
            f"WHERE event_id='{safe_event_id}' FORMAT TabSeparated"
        ).text.strip()
        assertions = {
            "collection_completion_event_persisted": publish_result.startswith(
                f"collection.completed:{run_pub_id}:"
            ),
            "analysis_admission_enqueued": event[0]["analysis_admission"] == "enqueued"
            and event[0]["analysis_commands"] == event[0]["analysis_expected"] == 1
            and event[1] is True,
            "one_answer_analysis_command": command[2] == "pending"
            and command[1]["answer_pub_id"].startswith("tsk_"),
            "answer_analysis_workflow_dispatched": dispatched,
            "analysis_persisted_end_to_end": persisted == (1, 1, 1)
            and bool(analysis_result["persistence"]["analysis_pub_id"]),
            "postgres_answer_lineage_preserved": lineage
            == (
                run_pub_id,
                str(payload["config_version_pub_id"]),
                task.query,
            ),
            "analysis_outbox_lineage_preserved": analysis_event[1]["run_pub_id"] == run_pub_id
            and analysis_event[1]["config_version_pub_id"] == str(payload["config_version_pub_id"]),
            "clickhouse_answer_lineage_preserved": projected_lineage == run_pub_id,
            "secret_bearing_collection_result_rejected_before_storage": dlp_rejected
            and persisted[0] == 1,
        }
        evidence = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "result": "passed" if all(assertions.values()) else "failed",
            "database_revision": "s04_0022",
            "assertions": assertions,
            "synthetic_fixture": True,
            "synthetic_fixture_removed": True,
            "sensitive_values_recorded": False,
        }
        OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        if evidence["result"] != "passed":
            raise RuntimeError("production_collection_analysis_fanout_failed")
        print(json.dumps({"result": "passed", "assertions": len(assertions)}))
    finally:
        with psycopg.connect(database_dsn) as connection:
            event_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT event_id FROM integration.outbox_event WHERE tenant_pub_id=%s",
                    (tenant_pub_id,),
                ).fetchall()
            ]
            if event_ids:
                connection.execute(
                    "DELETE FROM integration.consumer_receipt WHERE event_id=ANY(%s)",
                    (event_ids,),
                )
            connection.execute(
                "DELETE FROM integration.outbox_event WHERE tenant_pub_id=%s", (tenant_pub_id,)
            )
            for table in (
                "metric_daily",
                "metric_trace",
                "citation_fact",
                "answer_analysis",
                "analysis_run",
                "answer",
            ):
                connection.execute(
                    f"DELETE FROM analytics.{table} WHERE tenant_pub_id=%s",  # noqa: S608
                    (tenant_pub_id,),
                )
            connection.execute(
                "DELETE FROM integration.workflow_start_command WHERE tenant_pub_id=%s",
                (tenant_pub_id,),
            )
            connection.execute(
                """
                DELETE FROM platform.brand
                WHERE tenant_id=(SELECT id FROM platform.tenant WHERE pub_id=%s)
                """,
                (tenant_pub_id,),
            )
        cleanup(database_dsn, tenant_pub_id, collection_workflow_id)
        if clickhouse is not None:
            safe_tenant = tenant_pub_id.replace("'", "''")
            for table in ("answer_fact", "citation_fact", "run_event", "metric_daily"):
                clickhouse._post(  # noqa: SLF001
                    f"ALTER TABLE geo_analytics.{table} "
                    f"DELETE WHERE tenant_pub_id='{safe_tenant}' SETTINGS mutations_sync=1"
                )


if __name__ == "__main__":
    asyncio.run(main())
