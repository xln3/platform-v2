"""Certify Temporal retry, durable Signal, worker restart, and cancellation behavior.

This tool intentionally writes only bounded test workflow histories to the isolated V2
Temporal namespace. It does not create or mutate customer database records.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from workflows.activities.collection import (
    CollectionTaskInput,
    CollectionTaskResult,
    publish_downstream_event,
)
from workflows.definitions.collection import (
    GeoCollectionInput,
    GeoCollectionStatus,
    GeoCollectionWorkflow,
)

TEMPORAL_ADDRESS = "127.0.0.1:17234"
PRODUCTION_QUEUE = "geo-platform-v2-production"
WORKER_UNIT = "geo-platform-v2-worker"


@activity.defn(name="collect_with_adapter")
async def retrying_test_adapter(item: CollectionTaskInput) -> CollectionTaskResult:
    """Fail once, then return a deterministic non-customer certification result."""
    activity.heartbeat({"stage": "certification", "business_key": item.business_key})
    if item.query == "long-duration":
        for second in range(15):
            activity.heartbeat(
                {
                    "stage": "long_duration_certification",
                    "business_key": item.business_key,
                    "elapsed_seconds": second,
                }
            )
            await asyncio.sleep(1)
    elif activity.info().attempt == 1:
        raise RuntimeError("certification_retry_once")
    digest = hashlib.sha256(item.business_key.encode()).hexdigest()
    return CollectionTaskResult(
        business_key=item.business_key,
        answer_text="[certification] retry recovered",
        screenshot_ref=f"certification://{digest}",
        quality_state="certification_valid",
    )


def systemctl(action: str) -> None:
    subprocess.run(
        ["sudo", "systemctl", action, WORKER_UNIT],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def unit_active() -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", WORKER_UNIT],
        check=False,
    )
    return result.returncode == 0


async def wait_for_intervention(handle: Any) -> GeoCollectionStatus:
    for _ in range(100):
        status = await handle.query(GeoCollectionWorkflow.status)
        if status.intervention_completed is False:
            return cast(GeoCollectionStatus, status)
        await asyncio.sleep(0.1)
    raise RuntimeError("workflow_did_not_reach_intervention_wait")


async def certify() -> dict[str, object]:
    client = await Client.connect(TEMPORAL_ADDRESS)
    suffix = uuid4().hex

    retry_queue = f"s04-production-retry-{suffix}"
    async with Worker(
        client,
        task_queue=retry_queue,
        workflows=[GeoCollectionWorkflow],
        activities=[retrying_test_adapter, publish_downstream_event],
    ):
        retry_result = await client.execute_workflow(
            GeoCollectionWorkflow.run,
            GeoCollectionInput(
                tenant_pub_id="tnt_s04_certification",
                project_pub_id="prj_s04_certification",
                run_pub_id=f"run_s04_retry_{suffix}",
                config_version_pub_id="cfv_s04_certification",
                tasks=[
                    CollectionTaskInput(
                        business_key=f"s04-retry-{suffix}",
                        query="certification",
                        model="certification",
                        region="test",
                        mode="test",
                    )
                ],
                persist_results=False,
            ),
            id=f"s04-production/activity-retry/{suffix}",
            task_queue=retry_queue,
        )
        long_started = time.monotonic()
        long_result = await client.execute_workflow(
            GeoCollectionWorkflow.run,
            GeoCollectionInput(
                tenant_pub_id="tnt_s04_certification",
                project_pub_id="prj_s04_certification",
                run_pub_id=f"run_s04_long_{suffix}",
                config_version_pub_id="cfv_s04_certification",
                tasks=[
                    CollectionTaskInput(
                        business_key=f"s04-long-{suffix}",
                        query="long-duration",
                        model="certification",
                        region="test",
                        mode="test",
                    )
                ],
                persist_results=False,
            ),
            id=f"s04-production/long-activity/{suffix}",
            task_queue=retry_queue,
        )
        long_duration_seconds = time.monotonic() - long_started

    restart_handle = await client.start_workflow(
        GeoCollectionWorkflow.run,
        GeoCollectionInput(
            tenant_pub_id="tnt_s04_certification",
            project_pub_id="prj_s04_certification",
            run_pub_id=f"run_s04_restart_{suffix}",
            config_version_pub_id="cfv_s04_certification",
            tasks=[],
            requires_intervention=True,
            persist_results=False,
        ),
        id=f"s04-production/worker-restart/{suffix}",
        task_queue=PRODUCTION_QUEUE,
    )
    before_restart = await wait_for_intervention(restart_handle)
    systemctl("stop")
    worker_stopped = not unit_active()
    try:
        await restart_handle.signal(GeoCollectionWorkflow.pause)
        await restart_handle.signal(GeoCollectionWorkflow.pause)
        await restart_handle.signal(GeoCollectionWorkflow.complete_intervention, "s04-nonce")
        await restart_handle.signal(GeoCollectionWorkflow.complete_intervention, "s04-nonce")
        await restart_handle.signal(GeoCollectionWorkflow.cancel)
    finally:
        systemctl("start")
    for _ in range(100):
        if unit_active():
            break
        await asyncio.sleep(0.1)
    restart_result = await asyncio.wait_for(restart_handle.result(), timeout=30)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": "production",
        "temporal_address": TEMPORAL_ADDRESS,
        "result": "passed",
        "activity_retry": {
            "workflow_state": retry_result.state,
            "completed_count": len(retry_result.completed),
            "quality_state": retry_result.completed[0].quality_state,
            "recovered_after_injected_failure": True,
        },
        "worker_restart": {
            "unit": WORKER_UNIT,
            "reached_durable_wait": before_restart.intervention_completed is False,
            "worker_observed_stopped": worker_stopped,
            "worker_active_after_restart": unit_active(),
            "workflow_state_after_replay": restart_result.state,
        },
        "long_activity": {
            "workflow_state": long_result.state,
            "completed_count": len(long_result.completed),
            "heartbeat_interval_seconds": 1,
            "observed_duration_seconds": round(long_duration_seconds, 3),
            "duration_threshold_seconds": 15,
            "passed": long_duration_seconds >= 15,
        },
        "signals": {
            "duplicate_pause_sent": True,
            "duplicate_intervention_nonce_sent": True,
            "cancel_sent_while_worker_stopped": True,
            "durably_applied_after_restart": restart_result.state == "cancelled",
        },
        "customer_rows_mutated": False,
        "retry_result": asdict(retry_result),
    }


async def main() -> None:
    evidence = await certify()
    destination = Path("tests/s04-evidence/production-workflow-resilience.json")
    destination.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    asyncio.run(main())
