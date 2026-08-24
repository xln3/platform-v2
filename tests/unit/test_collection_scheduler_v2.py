from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from geo_platform.collection import scheduler_v2
from geo_platform.collection.identity_v2 import (
    MAX_CAMPAIGN_EXECUTION_PAGE_SIZE,
    CampaignWorkflowReference,
    FrozenCampaign,
)
from geo_platform.collection.scheduler_v2 import (
    CampaignExecutionPartition,
    CampaignExecutionPlan,
    CampaignWorkflowLaunchContext,
    CampaignWorkflowStartCommand,
    SchedulerV2Error,
    build_campaign_workflow_start_command,
    execution_partition_at,
    iter_execution_partitions,
    plan_campaign_execution,
)
from pydantic import ValidationError

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
CAMPAIGN_ID = UUID("00000000-0000-0000-0000-000000000101")
TENANT_ID = UUID("00000000-0000-0000-0000-000000000102")
PROJECT_ID = UUID("00000000-0000-0000-0000-000000000103")
CONFIG_ID = UUID("00000000-0000-0000-0000-000000000104")


def _campaign(
    expected_slot_count: int,
    *,
    membership_hash: str = "3" * 64,
) -> FrozenCampaign:
    return FrozenCampaign(
        id=CAMPAIGN_ID,
        campaign_pub_id="campaign-stage4",
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        config_revision_id=CONFIG_ID,
        config_revision_pub_id="config-stage4",
        config_revision_hash="1" * 64,
        specification_hash="2" * 64,
        expected_slot_count=expected_slot_count,
        materialized_slot_count=expected_slot_count,
        materialization_cursor=expected_slot_count,
        membership_hash=membership_hash,
        frozen_at=NOW,
    )


def _launch() -> CampaignWorkflowLaunchContext:
    return CampaignWorkflowLaunchContext(
        tenant_pub_id="tnt_stage4",
        project_pub_id="prj_stage4",
        canonical_enumeration_version="collection-enumeration-v1",
        checkpoint_ref="checkpoint-0",
        checkpoint_digest="6" * 64,
        reconciliation_checkpoint_ref="reconciliation-0",
        capability_policy_revision="capability-policy-v1",
        control_policy_revision="control-policy-v1",
        comparison_policy_revision="comparison-policy-v1",
        scheduling_window_start_utc="2026-08-24T00:00:00Z",
        scheduling_window_end_utc="2026-08-25T00:00:00Z",
        idempotency_key="campaign-partition-stage4",
    )


def _start_command(
    campaign: FrozenCampaign,
    plan: CampaignExecutionPlan,
    partition: CampaignExecutionPartition,
    *,
    launch: CampaignWorkflowLaunchContext | None = None,
) -> CampaignWorkflowStartCommand:
    return build_campaign_workflow_start_command(
        campaign,
        plan,
        partition,
        launch=launch or _launch(),
    )


def _error_code(caught: pytest.ExceptionInfo[SchedulerV2Error]) -> str:
    return caught.value.code


def _all_json_keys(value: object) -> set[str]:
    if isinstance(value, Mapping):
        keys = {str(key) for key in value}
        for child in value.values():
            keys.update(_all_json_keys(child))
        return keys
    if isinstance(value, list):
        child_keys: set[str] = set()
        for child in value:
            child_keys.update(_all_json_keys(child))
        return child_keys
    return set()


def _contains_list(value: object) -> bool:
    if isinstance(value, list):
        return True
    if isinstance(value, Mapping):
        return any(_contains_list(child) for child in value.values())
    return False


def test_planning_requires_a_complete_persisted_frozen_campaign() -> None:
    campaign = _campaign(11)
    assembling = campaign.model_copy(update={"state": "assembling"})
    incomplete = campaign.model_copy(
        update={
            "materialized_slot_count": 10,
            "materialization_cursor": 10,
        }
    )
    invalid_digest = campaign.model_copy(update={"membership_hash": "f" * 63})

    with pytest.raises(SchedulerV2Error) as assembling_error:
        plan_campaign_execution(
            assembling,
            execution_partition_size=4,
            workflow_page_size=2,
        )
    assert _error_code(assembling_error) == "scheduler_requires_persisted_frozen_campaign"

    with pytest.raises(SchedulerV2Error) as count_error:
        plan_campaign_execution(
            incomplete,
            execution_partition_size=4,
            workflow_page_size=2,
        )
    assert _error_code(count_error) == "scheduler_campaign_count_drift"

    with pytest.raises(SchedulerV2Error) as digest_error:
        plan_campaign_execution(
            invalid_digest,
            execution_partition_size=4,
            workflow_page_size=2,
        )
    assert _error_code(digest_error) == "scheduler_campaign_digest_invalid"
    assert digest_error.value.context == {"field": "membership_hash"}


def test_279k_slots_stream_as_gapless_non_overlapping_ranges_without_total_cap() -> None:
    campaign = _campaign(279_000)
    plan = plan_campaign_execution(
        campaign,
        execution_partition_size=17_003,
        workflow_page_size=1024,
    )

    assert "partitions" not in CampaignExecutionPlan.model_fields
    assert "slots" not in CampaignExecutionPlan.model_fields
    assert "tasks" not in CampaignExecutionPlan.model_fields
    assert plan.partition_count == 17

    next_expected_ordinal = 0
    observed_slot_count = 0
    observed_partition_count = 0
    identities: set[str] = set()
    digests: set[str] = set()
    for partition in iter_execution_partitions(plan):
        assert partition.partition_index == observed_partition_count
        assert partition.start_slot_ordinal == next_expected_ordinal
        assert partition.end_slot_ordinal_exclusive > partition.start_slot_ordinal
        assert partition.slot_count <= plan.execution_partition_size
        assert partition.partition_pub_id not in identities
        assert partition.partition_digest not in digests
        identities.add(partition.partition_pub_id)
        digests.add(partition.partition_digest)
        next_expected_ordinal = partition.end_slot_ordinal_exclusive
        observed_slot_count += partition.slot_count
        observed_partition_count += 1

    assert observed_partition_count == plan.partition_count
    assert observed_slot_count == campaign.expected_slot_count
    assert next_expected_ordinal == campaign.expected_slot_count
    assert execution_partition_at(plan, plan.partition_count - 1).end_slot_ordinal_exclusive == (
        campaign.expected_slot_count
    )


def test_partition_identity_is_deterministic_and_not_a_runtime_concurrency_input() -> None:
    campaign = _campaign(279_000)
    first_plan = plan_campaign_execution(
        campaign,
        execution_partition_size=4097,
        workflow_page_size=257,
    )
    repeated_plan = plan_campaign_execution(
        campaign,
        execution_partition_size=4097,
        workflow_page_size=257,
    )
    assert repeated_plan == first_plan
    assert tuple(iter_execution_partitions(repeated_plan)) == tuple(
        iter_execution_partitions(first_plan)
    )

    different_page_plan = plan_campaign_execution(
        campaign,
        execution_partition_size=4097,
        workflow_page_size=1024,
    )
    first_partition = execution_partition_at(first_plan, 7)
    different_page_partition = execution_partition_at(different_page_plan, 7)
    assert first_partition.partition_pub_id == different_page_partition.partition_pub_id
    assert first_partition.partition_digest == different_page_partition.partition_digest
    assert first_plan.plan_digest != different_page_plan.plan_digest

    parameters = inspect.signature(plan_campaign_execution).parameters
    assert tuple(parameters) == (
        "campaign",
        "execution_partition_size",
        "workflow_page_size",
    )
    assert "materialization_chunk_size" not in parameters
    assert "runtime_concurrency" not in parameters
    source = Path(scheduler_v2.__file__).read_text(encoding="utf-8")
    assert "run_service" not in source
    assert "campaign_materialization_v2" not in source
    assert "10000" not in source
    assert "10_000" not in source


def test_workflow_start_command_is_reference_only_and_constant_size() -> None:
    small_campaign = _campaign(1000, membership_hash="3" * 64)
    large_campaign = _campaign(279_000, membership_hash="4" * 64)
    small_plan = plan_campaign_execution(
        small_campaign,
        execution_partition_size=1000,
        workflow_page_size=256,
    )
    large_plan = plan_campaign_execution(
        large_campaign,
        execution_partition_size=1000,
        workflow_page_size=256,
    )
    small_command = _start_command(
        small_campaign,
        small_plan,
        execution_partition_at(small_plan, 0),
    )
    large_first_command = _start_command(
        large_campaign,
        large_plan,
        execution_partition_at(large_plan, 0),
    )
    large_last_partition = execution_partition_at(
        large_plan,
        large_plan.partition_count - 1,
    )
    large_last_command = _start_command(
        large_campaign,
        large_plan,
        large_last_partition,
    )

    assert isinstance(small_command.campaign_reference, CampaignWorkflowReference)
    assert small_command.workflow_input.schema_version == "collection-workflow-v2"
    assert small_command.outbox_type == "geo_collection_v2"
    assert small_command.workflow_type == "GeoCollectionV2Workflow"
    assert small_command.task_queue == "geo-platform-v2-collection-v2"
    assert small_command.payload_size_bytes == large_first_command.payload_size_bytes
    assert abs(large_last_command.payload_size_bytes - small_command.payload_size_bytes) < 64
    assert (
        max(
            small_command.payload_size_bytes,
            large_first_command.payload_size_bytes,
            large_last_command.payload_size_bytes,
        )
        < 4096
    )

    payload = cast(object, json.loads(large_last_command.payload_json))
    assert not _contains_list(payload)
    keys = _all_json_keys(payload)
    assert "expected_slot_count" not in keys
    assert "materialized_slot_count" not in keys
    assert "membership_specification_json" not in keys
    assert "questions" not in keys
    assert "slots" not in keys
    assert "tasks" not in keys
    assert {
        "campaign_id",
        "campaign_pub_id",
        "partition_pub_id",
        "partition_digest",
        "plan_digest",
        "cursor",
        "campaign_reference",
        "workflow_input",
    }.issubset(keys)


def test_start_command_exact_replay_is_anchored_at_partition_start() -> None:
    campaign = _campaign(31)
    plan = plan_campaign_execution(
        campaign,
        execution_partition_size=10,
        workflow_page_size=4,
    )
    partition = execution_partition_at(plan, 1)
    first = _start_command(campaign, plan, partition)
    exact_replay = _start_command(campaign, plan, partition)

    assert exact_replay == first
    assert exact_replay.payload_json == first.payload_json
    assert first.cursor == partition.start_slot_ordinal
    assert first.campaign_reference.cursor == partition.start_slot_ordinal
    assert first.workflow_input.cursor == partition.start_slot_ordinal
    assert "cursor" not in inspect.signature(build_campaign_workflow_start_command).parameters


def test_start_command_binds_v2_routing_and_rejects_launch_contract_drift() -> None:
    campaign = _campaign(10)
    plan = plan_campaign_execution(
        campaign,
        execution_partition_size=10,
        workflow_page_size=4,
    )
    partition = execution_partition_at(plan, 0)
    command = _start_command(campaign, plan, partition)

    payload = command.model_dump(mode="python")
    payload["task_queue"] = "geo-platform-v2"
    with pytest.raises(ValidationError, match="geo-platform-v2-collection-v2"):
        CampaignWorkflowStartCommand.model_validate(payload)

    invalid_window = _launch().model_copy(update={"scheduling_window_start_utc": "not-a-timestamp"})
    with pytest.raises(SchedulerV2Error) as rejected_input:
        _start_command(
            campaign,
            plan,
            partition,
            launch=invalid_window,
        )
    assert _error_code(rejected_input) == "scheduler_workflow_input_rejected"

    with pytest.raises(SchedulerV2Error) as missing_context:
        build_campaign_workflow_start_command(
            campaign,
            plan,
            partition,
            launch=cast(CampaignWorkflowLaunchContext, object()),
        )
    assert _error_code(missing_context) == "scheduler_workflow_launch_context_required"


def test_campaign_plan_and_partition_drift_fail_closed() -> None:
    campaign = _campaign(25)
    plan = plan_campaign_execution(
        campaign,
        execution_partition_size=10,
        workflow_page_size=4,
    )
    partition = execution_partition_at(plan, 1)

    changed_campaign = campaign.model_copy(update={"membership_hash": "5" * 64})
    with pytest.raises(SchedulerV2Error) as campaign_drift:
        _start_command(
            changed_campaign,
            plan,
            partition,
        )
    assert _error_code(campaign_drift) == "scheduler_campaign_plan_drift"
    assert campaign_drift.value.context == {"field": "membership_hash"}

    changed_plan = plan.model_copy(update={"expected_slot_count": 26})
    with pytest.raises(SchedulerV2Error) as plan_drift:
        execution_partition_at(changed_plan, 0)
    assert _error_code(plan_drift) == "scheduler_execution_plan_drift"

    changed_partition = partition.model_copy(
        update={"end_slot_ordinal_exclusive": partition.end_slot_ordinal_exclusive + 1}
    )
    with pytest.raises(SchedulerV2Error) as partition_drift:
        _start_command(
            campaign,
            plan,
            changed_partition,
        )
    assert _error_code(partition_drift) == "scheduler_execution_partition_drift"

    command_payload = _start_command(campaign, plan, partition).model_dump(mode="python")
    command_payload["cursor"] = partition.start_slot_ordinal + 1
    with pytest.raises(ValidationError, match="workflow_start_reference_drift"):
        CampaignWorkflowStartCommand.model_validate(command_payload)


@pytest.mark.parametrize(
    ("partition_size", "page_size", "error_code"),
    [
        (True, 1, "scheduler_integer_required"),
        (0, 1, "scheduler_positive_integer_required"),
        (1, False, "scheduler_integer_required"),
        (1, 0, "scheduler_positive_integer_required"),
        (
            1,
            MAX_CAMPAIGN_EXECUTION_PAGE_SIZE + 1,
            "scheduler_workflow_page_size_too_large",
        ),
    ],
)
def test_partition_and_page_sizes_fail_closed(
    partition_size: object,
    page_size: object,
    error_code: str,
) -> None:
    with pytest.raises(SchedulerV2Error) as caught:
        plan_campaign_execution(
            _campaign(1),
            execution_partition_size=cast(int, partition_size),
            workflow_page_size=cast(int, page_size),
        )
    assert _error_code(caught) == error_code


def test_partition_lookup_rejects_non_integer_and_out_of_range_indexes() -> None:
    plan = plan_campaign_execution(
        _campaign(11),
        execution_partition_size=5,
        workflow_page_size=2,
    )
    with pytest.raises(SchedulerV2Error) as boolean_index:
        execution_partition_at(plan, cast(int, True))
    assert _error_code(boolean_index) == "scheduler_integer_required"

    with pytest.raises(SchedulerV2Error) as out_of_range:
        execution_partition_at(plan, plan.partition_count)
    assert _error_code(out_of_range) == "scheduler_partition_index_out_of_range"
