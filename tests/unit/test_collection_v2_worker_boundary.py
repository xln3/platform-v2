"""Registration and fail-closed guards for the isolated collection v2 worker."""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Callable
from typing import Any, cast

import pytest
from temporalio.exceptions import ApplicationError

import workflows.workers.collection_v2 as collection_v2_worker
import workflows.workers.main as collection_v1_worker
from workflows.activities.collection_v2 import (
    COLLECTION_V2_PAGE_REQUEST_SCHEMA,
    COLLECTION_V2_RECONCILIATION_REQUEST_SCHEMA,
    MAX_COLLECTION_V2_PAGE_SIZE,
    CollectionV2PageRequest,
    CollectionV2ReconciliationRequest,
    execute_collection_v2_page,
    reconcile_collection_v2_partition,
)
from workflows.definitions.collection_v2 import (
    COLLECTION_V2_OUTBOX_TYPE,
    COLLECTION_V2_PAYLOAD_SCHEMA,
    COLLECTION_V2_TASK_QUEUE,
    COLLECTION_V2_WORKFLOW_TYPE,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _activity_names(items: tuple[Callable[..., Any], ...]) -> set[str]:
    return {cast(Any, item).__temporal_activity_definition.name for item in items}


def _workflow_names(items: tuple[type[Any], ...]) -> set[str]:
    return {cast(Any, item).__temporal_workflow_definition.name for item in items}


def _page_request() -> CollectionV2PageRequest:
    return CollectionV2PageRequest(
        schema_version=COLLECTION_V2_PAGE_REQUEST_SCHEMA,
        tenant_pub_id="tnt_test",
        campaign_pub_id="cmp_test",
        partition_pub_id="partition-test",
        partition_digest=_digest("partition"),
        membership_digest=_digest("membership"),
        cursor=0,
        page_size=10,
        checkpoint_ref="checkpoint-0",
        checkpoint_digest=_digest("checkpoint-0"),
        reconciliation_checkpoint_ref="reconciliation-0",
        capability_policy_revision="capability-policy-v1",
        control_policy_revision="control-policy-v1",
    )


def test_v2_worker_names_and_registrations_are_physically_isolated_from_v1() -> None:
    v1_workflows = _workflow_names(collection_v1_worker.COLLECTION_WORKFLOWS)
    v2_workflows = _workflow_names(collection_v2_worker.COLLECTION_V2_WORKFLOWS)
    v1_activities = _activity_names(collection_v1_worker.COLLECTION_ACTIVITIES)
    v2_activities = _activity_names(collection_v2_worker.COLLECTION_V2_ACTIVITIES)

    assert COLLECTION_V2_OUTBOX_TYPE == "geo_collection_v2"
    assert COLLECTION_V2_WORKFLOW_TYPE == "GeoCollectionV2Workflow"
    assert COLLECTION_V2_PAYLOAD_SCHEMA == "collection-workflow-v2"
    assert COLLECTION_V2_TASK_QUEUE == "geo-platform-v2-collection-v2"
    assert collection_v2_worker.COLLECTION_V2_TASK_QUEUE == COLLECTION_V2_TASK_QUEUE
    assert collection_v2_worker.COLLECTION_V2_TASK_QUEUE != "geo-platform-v2"
    assert v2_workflows == {"GeoCollectionV2Workflow"}
    assert v2_activities == {
        "execute_collection_v2_page",
        "reconcile_collection_v2_partition",
    }
    assert "GeoCollectionWorkflow" in v1_workflows
    assert "GeoCollectionV2Workflow" not in v1_workflows
    assert v1_workflows.isdisjoint(v2_workflows)
    assert v1_activities.isdisjoint(v2_activities)

    old_worker_source = inspect.getsource(collection_v1_worker)
    new_worker_source = inspect.getsource(collection_v2_worker)
    assert "collection_v2" not in old_worker_source
    assert "workflows.definitions.collection import" not in new_worker_source
    assert "workflows.activities.collection import" not in new_worker_source


async def test_production_page_and_reconciliation_activities_fail_closed_without_io() -> None:
    request = _page_request()
    with pytest.raises(ApplicationError) as page_failure:
        await execute_collection_v2_page(request)
    assert page_failure.value.type == "collection_v2_partition_executor_not_configured"
    assert page_failure.value.non_retryable is True

    reconciliation = CollectionV2ReconciliationRequest(
        schema_version=COLLECTION_V2_RECONCILIATION_REQUEST_SCHEMA,
        tenant_pub_id=request.tenant_pub_id,
        campaign_pub_id=request.campaign_pub_id,
        partition_pub_id=request.partition_pub_id,
        partition_digest=request.partition_digest,
        membership_digest=request.membership_digest,
        cursor=request.cursor,
        checkpoint_ref=request.checkpoint_ref,
        checkpoint_digest=request.checkpoint_digest,
        reconciliation_checkpoint_ref=request.reconciliation_checkpoint_ref,
        control_policy_revision=request.control_policy_revision,
    )
    with pytest.raises(ApplicationError) as reconciliation_failure:
        await reconcile_collection_v2_partition(reconciliation)
    assert reconciliation_failure.value.type == "collection_v2_reconciliation_not_configured"
    assert reconciliation_failure.value.non_retryable is True


def test_v2_page_limit_has_one_truth_at_stage1_boundary() -> None:
    from geo_platform.collection.identity_v2 import MAX_CAMPAIGN_EXECUTION_PAGE_SIZE

    assert MAX_COLLECTION_V2_PAGE_SIZE == MAX_CAMPAIGN_EXECUTION_PAGE_SIZE == 2_048
