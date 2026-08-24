"""Pure deterministic execution-partition planning for collection V2.

This module is deliberately smaller than a scheduler.  It does not persist an
outbox row, start a workflow, inspect worker capacity, or lease a collection
resource.  It only turns a persisted :class:`FrozenCampaign` proof into stable
ordinal ranges and constant-size workflow-start commands.

Database materialization chunks, execution partitions, workflow page reads,
and runtime concurrency are separate controls.  Consequently this API accepts
an execution partition size and a page size, but no materialization or
concurrency setting.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from hashlib import sha256
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from workflows.activities.collection_v2 import CollectionV2ContractError
from workflows.definitions.collection_v2 import (
    COLLECTION_V2_OUTBOX_TYPE,
    COLLECTION_V2_PAYLOAD_SCHEMA,
    COLLECTION_V2_TASK_QUEUE,
    COLLECTION_V2_WORKFLOW_TYPE,
    MAX_COLLECTION_V2_PAGES_PER_RUN,
    CollectionV2WorkflowInput,
    build_collection_v2_workflow_input,
)

from .identity_v2 import (
    CAMPAIGN_MEMBERSHIP_DIGEST_VERSION,
    CAMPAIGN_SLOT_GENERATOR_VERSION,
    IDENTITY_V2_SCHEMA_VERSION,
    MAX_CAMPAIGN_EXECUTION_PAGE_SIZE,
    CampaignWorkflowReference,
    FrozenCampaign,
    IdentityV2Error,
    build_campaign_workflow_reference,
)

EXECUTION_PLAN_VERSION: Literal["collection-execution-plan-v1"] = "collection-execution-plan-v1"
EXECUTION_PARTITION_VERSION: Literal["collection-execution-partition-v1"] = (
    "collection-execution-partition-v1"
)
WORKFLOW_START_COMMAND_VERSION: Literal["collection-workflow-start-command-v1"] = (
    "collection-workflow-start-command-v1"
)

_OPAQUE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class SchedulerV2Error(ValueError):
    """Fail-closed scheduler-boundary error with a stable code."""

    code: str
    context: Mapping[str, str | int | bool | None]

    def __init__(self, code: str, **context: str | int | bool | None) -> None:
        self.code = code
        self.context = dict(sorted(context.items()))
        suffix = ":" + ":".join(f"{key}={value}" for key, value in self.context.items())
        super().__init__(f"{code}{suffix}" if self.context else code)

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "context": dict(self.context)}


class _FrozenSchedulerModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class CampaignExecutionPlan(_FrozenSchedulerModel):
    """Constant-size campaign proof and deterministic partitioning policy."""

    schema_version: Literal["collection-execution-plan-v1"] = EXECUTION_PLAN_VERSION
    campaign_id: UUID
    campaign_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN, max_length=30)
    tenant_id: UUID
    project_id: UUID
    config_revision_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN, max_length=30)
    config_revision_hash: str = Field(pattern=_SHA256_PATTERN)
    specification_hash: str = Field(pattern=_SHA256_PATTERN)
    slot_generator_version: Literal["collection-slot-generator-v1"] = (
        CAMPAIGN_SLOT_GENERATOR_VERSION
    )
    membership_digest_version: Literal["collection-membership-chain-v1"] = (
        CAMPAIGN_MEMBERSHIP_DIGEST_VERSION
    )
    membership_hash: str = Field(pattern=_SHA256_PATTERN)
    expected_slot_count: int = Field(strict=True, ge=1)
    execution_partition_size: int = Field(strict=True, ge=1)
    workflow_page_size: int = Field(
        strict=True,
        ge=1,
        le=MAX_CAMPAIGN_EXECUTION_PAGE_SIZE,
    )
    partition_count: int = Field(strict=True, ge=1)
    plan_digest: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def count_and_digest_are_exact(self) -> Self:
        expected_partition_count = _ceiling_division(
            self.expected_slot_count,
            self.execution_partition_size,
        )
        if self.partition_count != expected_partition_count:
            raise ValueError("execution_partition_count_drift")
        if self.plan_digest != _execution_plan_digest(
            campaign_id=self.campaign_id,
            campaign_pub_id=self.campaign_pub_id,
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            config_revision_pub_id=self.config_revision_pub_id,
            config_revision_hash=self.config_revision_hash,
            specification_hash=self.specification_hash,
            slot_generator_version=self.slot_generator_version,
            membership_digest_version=self.membership_digest_version,
            membership_hash=self.membership_hash,
            expected_slot_count=self.expected_slot_count,
            execution_partition_size=self.execution_partition_size,
            workflow_page_size=self.workflow_page_size,
        ):
            raise ValueError("execution_plan_digest_drift")
        return self


class CampaignExecutionPartition(_FrozenSchedulerModel):
    """One deterministic ordinal range; it contains no materialized slot rows."""

    schema_version: Literal["collection-execution-partition-v1"] = EXECUTION_PARTITION_VERSION
    campaign_id: UUID
    campaign_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN, max_length=30)
    specification_hash: str = Field(pattern=_SHA256_PATTERN)
    slot_generator_version: Literal["collection-slot-generator-v1"] = (
        CAMPAIGN_SLOT_GENERATOR_VERSION
    )
    membership_digest_version: Literal["collection-membership-chain-v1"] = (
        CAMPAIGN_MEMBERSHIP_DIGEST_VERSION
    )
    membership_hash: str = Field(pattern=_SHA256_PATTERN)
    plan_digest: str = Field(pattern=_SHA256_PATTERN)
    partition_index: int = Field(strict=True, ge=0)
    partition_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN, max_length=30)
    start_slot_ordinal: int = Field(strict=True, ge=0)
    end_slot_ordinal_exclusive: int = Field(strict=True, ge=1)
    partition_digest: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def range_identity_and_digest_are_exact(self) -> Self:
        if self.end_slot_ordinal_exclusive <= self.start_slot_ordinal:
            raise ValueError("execution_partition_range_empty")
        expected_digest = _execution_partition_digest(
            campaign_id=self.campaign_id,
            campaign_pub_id=self.campaign_pub_id,
            specification_hash=self.specification_hash,
            slot_generator_version=self.slot_generator_version,
            membership_digest_version=self.membership_digest_version,
            membership_hash=self.membership_hash,
            partition_index=self.partition_index,
            start_slot_ordinal=self.start_slot_ordinal,
            end_slot_ordinal_exclusive=self.end_slot_ordinal_exclusive,
        )
        if self.partition_digest != expected_digest:
            raise ValueError("execution_partition_digest_drift")
        if self.partition_pub_id != _partition_pub_id(expected_digest):
            raise ValueError("execution_partition_identity_drift")
        return self

    @property
    def slot_count(self) -> int:
        return self.end_slot_ordinal_exclusive - self.start_slot_ordinal


class CampaignWorkflowLaunchContext(_FrozenSchedulerModel):
    """Constant-size admission facts not owned by the campaign reference."""

    tenant_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    project_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN)
    canonical_enumeration_version: str = Field(pattern=_OPAQUE_ID_PATTERN)
    checkpoint_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    checkpoint_digest: str = Field(pattern=_SHA256_PATTERN)
    reconciliation_checkpoint_ref: str = Field(pattern=_OPAQUE_ID_PATTERN)
    capability_policy_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    control_policy_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    comparison_policy_revision: str = Field(pattern=_OPAQUE_ID_PATTERN)
    scheduling_window_start_utc: str = Field(min_length=1, max_length=40)
    scheduling_window_end_utc: str = Field(min_length=1, max_length=40)
    idempotency_key: str = Field(pattern=_OPAQUE_ID_PATTERN)
    continue_as_new_after_pages: int = Field(
        default=25,
        strict=True,
        ge=1,
        le=MAX_COLLECTION_V2_PAGES_PER_RUN,
    )


class CampaignWorkflowStartCommand(_FrozenSchedulerModel):
    """Exact constant-size outbox value for the isolated Temporal V2 worker."""

    schema_version: Literal["collection-workflow-start-command-v1"] = WORKFLOW_START_COMMAND_VERSION
    outbox_type: Literal["geo_collection_v2"] = COLLECTION_V2_OUTBOX_TYPE
    workflow_type: Literal["GeoCollectionV2Workflow"] = COLLECTION_V2_WORKFLOW_TYPE
    task_queue: Literal["geo-platform-v2-collection-v2"] = COLLECTION_V2_TASK_QUEUE
    payload_schema_version: Literal["collection-workflow-v2"] = COLLECTION_V2_PAYLOAD_SCHEMA
    workflow_id: str = Field(pattern=_OPAQUE_ID_PATTERN, max_length=30)
    command_digest: str = Field(pattern=_SHA256_PATTERN)
    campaign_id: UUID
    campaign_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN, max_length=30)
    partition_pub_id: str = Field(pattern=_OPAQUE_ID_PATTERN, max_length=30)
    partition_digest: str = Field(pattern=_SHA256_PATTERN)
    plan_digest: str = Field(pattern=_SHA256_PATTERN)
    cursor: int = Field(strict=True, ge=0)
    campaign_reference: CampaignWorkflowReference
    workflow_input: CollectionV2WorkflowInput

    @model_validator(mode="after")
    def references_and_digest_are_exact(self) -> Self:
        reference = self.campaign_reference
        workflow_input = self.workflow_input
        if (
            self.campaign_pub_id != reference.campaign_pub_id
            or self.partition_pub_id != reference.partition_pub_id
            or self.cursor != reference.cursor
            or workflow_input.schema_version != self.payload_schema_version
            or workflow_input.config_revision_pub_id != reference.config_revision_pub_id
            or workflow_input.config_revision_hash != reference.config_revision_hash
            or workflow_input.campaign_pub_id != reference.campaign_pub_id
            or workflow_input.specification_hash != reference.specification_hash
            or workflow_input.partition_pub_id != reference.partition_pub_id
            or workflow_input.partition_digest != self.partition_digest
            or workflow_input.membership_digest_version != reference.membership_digest_version
            or workflow_input.membership_digest != reference.membership_hash
            or workflow_input.slot_generator_version != reference.slot_generator_version
            or workflow_input.start_slot_ordinal != reference.start_slot_ordinal
            or workflow_input.end_slot_ordinal_exclusive != reference.end_slot_ordinal_exclusive
            or workflow_input.cursor != reference.cursor
            or workflow_input.page_size != reference.page_size
        ):
            raise ValueError("workflow_start_reference_drift")
        if self.workflow_id != _workflow_id(
            campaign_id=self.campaign_id,
            partition_digest=self.partition_digest,
        ):
            raise ValueError("workflow_start_identity_drift")
        if self.command_digest != _workflow_start_command_digest(
            campaign_id=self.campaign_id,
            campaign_pub_id=self.campaign_pub_id,
            partition_pub_id=self.partition_pub_id,
            partition_digest=self.partition_digest,
            plan_digest=self.plan_digest,
            cursor=self.cursor,
            campaign_reference=reference,
            workflow_input=workflow_input,
        ):
            raise ValueError("workflow_start_command_digest_drift")
        return self

    @property
    def payload_json(self) -> str:
        return _canonical_json(self.model_dump(mode="json"))

    @property
    def payload_size_bytes(self) -> int:
        return len(self.payload_json.encode("utf-8"))


def plan_campaign_execution(
    campaign: FrozenCampaign,
    *,
    execution_partition_size: int,
    workflow_page_size: int,
) -> CampaignExecutionPlan:
    """Create a compact plan from one complete persisted campaign proof."""

    frozen = _require_complete_frozen_campaign(campaign)
    _require_positive_integer(
        execution_partition_size,
        field="execution_partition_size",
    )
    _require_positive_integer(workflow_page_size, field="workflow_page_size")
    if workflow_page_size > MAX_CAMPAIGN_EXECUTION_PAGE_SIZE:
        raise SchedulerV2Error(
            "scheduler_workflow_page_size_too_large",
            maximum=MAX_CAMPAIGN_EXECUTION_PAGE_SIZE,
        )
    partition_count = _ceiling_division(
        frozen.expected_slot_count,
        execution_partition_size,
    )
    plan_digest = _execution_plan_digest(
        campaign_id=frozen.id,
        campaign_pub_id=frozen.campaign_pub_id,
        tenant_id=frozen.tenant_id,
        project_id=frozen.project_id,
        config_revision_pub_id=frozen.config_revision_pub_id,
        config_revision_hash=frozen.config_revision_hash,
        specification_hash=frozen.specification_hash,
        slot_generator_version=frozen.slot_generator_version,
        membership_digest_version=frozen.membership_digest_version,
        membership_hash=frozen.membership_hash,
        expected_slot_count=frozen.expected_slot_count,
        execution_partition_size=execution_partition_size,
        workflow_page_size=workflow_page_size,
    )
    return CampaignExecutionPlan(
        campaign_id=frozen.id,
        campaign_pub_id=frozen.campaign_pub_id,
        tenant_id=frozen.tenant_id,
        project_id=frozen.project_id,
        config_revision_pub_id=frozen.config_revision_pub_id,
        config_revision_hash=frozen.config_revision_hash,
        specification_hash=frozen.specification_hash,
        slot_generator_version=frozen.slot_generator_version,
        membership_digest_version=frozen.membership_digest_version,
        membership_hash=frozen.membership_hash,
        expected_slot_count=frozen.expected_slot_count,
        execution_partition_size=execution_partition_size,
        workflow_page_size=workflow_page_size,
        partition_count=partition_count,
        plan_digest=plan_digest,
    )


def iter_execution_partitions(
    plan: CampaignExecutionPlan,
) -> Iterator[CampaignExecutionPartition]:
    """Stream exact adjacent ranges without allocating a partition collection."""

    validated = _validate_plan(plan)
    for partition_index in range(validated.partition_count):
        yield _partition_at_validated_plan(validated, partition_index)


def execution_partition_at(
    plan: CampaignExecutionPlan,
    partition_index: int,
) -> CampaignExecutionPartition:
    """Address one partition directly without generating its predecessors."""

    validated = _validate_plan(plan)
    _require_non_negative_integer(partition_index, field="partition_index")
    if partition_index >= validated.partition_count:
        raise SchedulerV2Error(
            "scheduler_partition_index_out_of_range",
            partition_count=validated.partition_count,
            partition_index=partition_index,
        )
    return _partition_at_validated_plan(validated, partition_index)


def build_campaign_workflow_start_command(
    campaign: FrozenCampaign,
    plan: CampaignExecutionPlan,
    partition: CampaignExecutionPartition,
    *,
    launch: CampaignWorkflowLaunchContext,
    cursor: int | None = None,
) -> CampaignWorkflowStartCommand:
    """Build one exact start command after rechecking campaign and plan lineage."""

    frozen = _require_complete_frozen_campaign(campaign)
    validated_plan = _validate_plan(plan)
    validated_launch = _validate_launch_context(launch)
    _assert_campaign_matches_plan(frozen, validated_plan)
    expected_partition = execution_partition_at(
        validated_plan,
        partition.partition_index,
    )
    validated_partition = _validate_partition(partition)
    if validated_partition != expected_partition:
        raise SchedulerV2Error(
            "scheduler_execution_partition_drift",
            partition_index=expected_partition.partition_index,
        )
    start_cursor = expected_partition.start_slot_ordinal if cursor is None else cursor
    _require_non_negative_integer(start_cursor, field="cursor")
    if not (
        expected_partition.start_slot_ordinal
        <= start_cursor
        < expected_partition.end_slot_ordinal_exclusive
    ):
        raise SchedulerV2Error(
            "scheduler_cursor_out_of_partition",
            cursor=start_cursor,
            partition_index=expected_partition.partition_index,
        )
    try:
        campaign_reference = build_campaign_workflow_reference(
            frozen,
            partition_pub_id=expected_partition.partition_pub_id,
            start_slot_ordinal=expected_partition.start_slot_ordinal,
            end_slot_ordinal_exclusive=expected_partition.end_slot_ordinal_exclusive,
            cursor=start_cursor,
            page_size=validated_plan.workflow_page_size,
        )
    except IdentityV2Error as exc:
        raise SchedulerV2Error(
            "scheduler_workflow_reference_rejected",
            identity_error=exc.code,
        ) from exc
    try:
        workflow_input = build_collection_v2_workflow_input(
            campaign_reference,
            tenant_pub_id=validated_launch.tenant_pub_id,
            project_pub_id=validated_launch.project_pub_id,
            partition_digest=expected_partition.partition_digest,
            canonical_enumeration_version=validated_launch.canonical_enumeration_version,
            checkpoint_ref=validated_launch.checkpoint_ref,
            checkpoint_digest=validated_launch.checkpoint_digest,
            reconciliation_checkpoint_ref=validated_launch.reconciliation_checkpoint_ref,
            capability_policy_revision=validated_launch.capability_policy_revision,
            control_policy_revision=validated_launch.control_policy_revision,
            comparison_policy_revision=validated_launch.comparison_policy_revision,
            scheduling_window_start_utc=validated_launch.scheduling_window_start_utc,
            scheduling_window_end_utc=validated_launch.scheduling_window_end_utc,
            idempotency_key=validated_launch.idempotency_key,
            continue_as_new_after_pages=validated_launch.continue_as_new_after_pages,
        )
    except CollectionV2ContractError as exc:
        raise SchedulerV2Error(
            "scheduler_workflow_input_rejected",
            contract_error=str(exc),
        ) from exc
    command_digest = _workflow_start_command_digest(
        campaign_id=frozen.id,
        campaign_pub_id=frozen.campaign_pub_id,
        partition_pub_id=expected_partition.partition_pub_id,
        partition_digest=expected_partition.partition_digest,
        plan_digest=validated_plan.plan_digest,
        cursor=start_cursor,
        campaign_reference=campaign_reference,
        workflow_input=workflow_input,
    )
    return CampaignWorkflowStartCommand(
        workflow_id=_workflow_id(
            campaign_id=frozen.id,
            partition_digest=expected_partition.partition_digest,
        ),
        command_digest=command_digest,
        campaign_id=frozen.id,
        campaign_pub_id=frozen.campaign_pub_id,
        partition_pub_id=expected_partition.partition_pub_id,
        partition_digest=expected_partition.partition_digest,
        plan_digest=validated_plan.plan_digest,
        cursor=start_cursor,
        campaign_reference=campaign_reference,
        workflow_input=workflow_input,
    )


def _require_complete_frozen_campaign(campaign: object) -> FrozenCampaign:
    if not isinstance(campaign, FrozenCampaign) or getattr(campaign, "state", None) != "frozen":
        raise SchedulerV2Error("scheduler_requires_persisted_frozen_campaign")
    expected_slot_count = getattr(campaign, "expected_slot_count", None)
    materialized_slot_count = getattr(campaign, "materialized_slot_count", None)
    materialization_cursor = getattr(campaign, "materialization_cursor", None)
    if (
        isinstance(expected_slot_count, bool)
        or not isinstance(expected_slot_count, int)
        or expected_slot_count < 1
        or materialized_slot_count != expected_slot_count
        or materialization_cursor != expected_slot_count
        or getattr(campaign, "materialization_state", None) != "complete"
    ):
        raise SchedulerV2Error(
            "scheduler_campaign_count_drift",
            expected_slot_count=(
                expected_slot_count if isinstance(expected_slot_count, int) else None
            ),
            materialized_slot_count=(
                materialized_slot_count if isinstance(materialized_slot_count, int) else None
            ),
            materialization_cursor=(
                materialization_cursor if isinstance(materialization_cursor, int) else None
            ),
        )
    for field in ("config_revision_hash", "specification_hash", "membership_hash"):
        value = getattr(campaign, field, None)
        if not isinstance(value, str) or re.fullmatch(_SHA256_PATTERN, value) is None:
            raise SchedulerV2Error("scheduler_campaign_digest_invalid", field=field)
    if (
        getattr(campaign, "schema_version", None) != IDENTITY_V2_SCHEMA_VERSION
        or getattr(campaign, "slot_generator_version", None) != CAMPAIGN_SLOT_GENERATOR_VERSION
        or getattr(campaign, "membership_digest_version", None)
        != CAMPAIGN_MEMBERSHIP_DIGEST_VERSION
    ):
        raise SchedulerV2Error("scheduler_campaign_version_drift")
    try:
        return FrozenCampaign.model_validate(campaign.model_dump(mode="python"))
    except (AttributeError, ValidationError) as exc:
        raise SchedulerV2Error("scheduler_campaign_proof_invalid") from exc


def _validate_plan(plan: object) -> CampaignExecutionPlan:
    if not isinstance(plan, CampaignExecutionPlan):
        raise SchedulerV2Error("scheduler_execution_plan_required")
    try:
        return CampaignExecutionPlan.model_validate(plan.model_dump(mode="python"))
    except (AttributeError, ValidationError) as exc:
        raise SchedulerV2Error("scheduler_execution_plan_drift") from exc


def _validate_partition(partition: object) -> CampaignExecutionPartition:
    if not isinstance(partition, CampaignExecutionPartition):
        raise SchedulerV2Error("scheduler_execution_partition_required")
    try:
        return CampaignExecutionPartition.model_validate(partition.model_dump(mode="python"))
    except (AttributeError, ValidationError) as exc:
        raise SchedulerV2Error("scheduler_execution_partition_drift") from exc


def _validate_launch_context(launch: object) -> CampaignWorkflowLaunchContext:
    if not isinstance(launch, CampaignWorkflowLaunchContext):
        raise SchedulerV2Error("scheduler_workflow_launch_context_required")
    try:
        return CampaignWorkflowLaunchContext.model_validate(launch.model_dump(mode="python"))
    except (AttributeError, ValidationError) as exc:
        raise SchedulerV2Error("scheduler_workflow_launch_context_drift") from exc


def _assert_campaign_matches_plan(
    campaign: FrozenCampaign,
    plan: CampaignExecutionPlan,
) -> None:
    exact_fields = {
        "campaign_id": campaign.id,
        "campaign_pub_id": campaign.campaign_pub_id,
        "tenant_id": campaign.tenant_id,
        "project_id": campaign.project_id,
        "config_revision_pub_id": campaign.config_revision_pub_id,
        "config_revision_hash": campaign.config_revision_hash,
        "specification_hash": campaign.specification_hash,
        "slot_generator_version": campaign.slot_generator_version,
        "membership_digest_version": campaign.membership_digest_version,
        "membership_hash": campaign.membership_hash,
        "expected_slot_count": campaign.expected_slot_count,
    }
    for field, campaign_value in exact_fields.items():
        if getattr(plan, field) != campaign_value:
            raise SchedulerV2Error("scheduler_campaign_plan_drift", field=field)


def _partition_at_validated_plan(
    plan: CampaignExecutionPlan,
    partition_index: int,
) -> CampaignExecutionPartition:
    start_slot_ordinal = partition_index * plan.execution_partition_size
    end_slot_ordinal_exclusive = min(
        start_slot_ordinal + plan.execution_partition_size,
        plan.expected_slot_count,
    )
    partition_digest = _execution_partition_digest(
        campaign_id=plan.campaign_id,
        campaign_pub_id=plan.campaign_pub_id,
        specification_hash=plan.specification_hash,
        slot_generator_version=plan.slot_generator_version,
        membership_digest_version=plan.membership_digest_version,
        membership_hash=plan.membership_hash,
        partition_index=partition_index,
        start_slot_ordinal=start_slot_ordinal,
        end_slot_ordinal_exclusive=end_slot_ordinal_exclusive,
    )
    return CampaignExecutionPartition(
        campaign_id=plan.campaign_id,
        campaign_pub_id=plan.campaign_pub_id,
        specification_hash=plan.specification_hash,
        slot_generator_version=plan.slot_generator_version,
        membership_digest_version=plan.membership_digest_version,
        membership_hash=plan.membership_hash,
        plan_digest=plan.plan_digest,
        partition_index=partition_index,
        partition_pub_id=_partition_pub_id(partition_digest),
        start_slot_ordinal=start_slot_ordinal,
        end_slot_ordinal_exclusive=end_slot_ordinal_exclusive,
        partition_digest=partition_digest,
    )


def _require_positive_integer(value: object, *, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchedulerV2Error("scheduler_integer_required", field=field)
    if value < 1:
        raise SchedulerV2Error("scheduler_positive_integer_required", field=field)


def _require_non_negative_integer(value: object, *, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchedulerV2Error("scheduler_integer_required", field=field)
    if value < 0:
        raise SchedulerV2Error("scheduler_non_negative_integer_required", field=field)


def _ceiling_division(dividend: int, divisor: int) -> int:
    return (dividend + divisor - 1) // divisor


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_json(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _execution_plan_digest(
    *,
    campaign_id: UUID,
    campaign_pub_id: str,
    tenant_id: UUID,
    project_id: UUID,
    config_revision_pub_id: str,
    config_revision_hash: str,
    specification_hash: str,
    slot_generator_version: str,
    membership_digest_version: str,
    membership_hash: str,
    expected_slot_count: int,
    execution_partition_size: int,
    workflow_page_size: int,
) -> str:
    return _sha256_json(
        {
            "campaign_id": str(campaign_id),
            "campaign_pub_id": campaign_pub_id,
            "config_revision_hash": config_revision_hash,
            "config_revision_pub_id": config_revision_pub_id,
            "execution_partition_size": execution_partition_size,
            "expected_slot_count": expected_slot_count,
            "membership_digest_version": membership_digest_version,
            "membership_hash": membership_hash,
            "project_id": str(project_id),
            "specification_hash": specification_hash,
            "tenant_id": str(tenant_id),
            "version": EXECUTION_PLAN_VERSION,
            "slot_generator_version": slot_generator_version,
            "workflow_page_size": workflow_page_size,
        }
    )


def _execution_partition_digest(
    *,
    campaign_id: UUID,
    campaign_pub_id: str,
    specification_hash: str,
    slot_generator_version: str,
    membership_digest_version: str,
    membership_hash: str,
    partition_index: int,
    start_slot_ordinal: int,
    end_slot_ordinal_exclusive: int,
) -> str:
    return _sha256_json(
        {
            "campaign_id": str(campaign_id),
            "campaign_pub_id": campaign_pub_id,
            "end_slot_ordinal_exclusive": end_slot_ordinal_exclusive,
            "membership_digest_version": membership_digest_version,
            "membership_hash": membership_hash,
            "partition_index": partition_index,
            "specification_hash": specification_hash,
            "start_slot_ordinal": start_slot_ordinal,
            "version": EXECUTION_PARTITION_VERSION,
            "slot_generator_version": slot_generator_version,
        }
    )


def _partition_pub_id(partition_digest: str) -> str:
    return f"cpt2_{partition_digest[:24]}"


def _workflow_id(*, campaign_id: UUID, partition_digest: str) -> str:
    digest = _sha256_json(
        {
            "campaign_id": str(campaign_id),
            "partition_digest": partition_digest,
            "version": WORKFLOW_START_COMMAND_VERSION,
        }
    )
    return f"cwf2_{digest[:24]}"


def _workflow_start_command_digest(
    *,
    campaign_id: UUID,
    campaign_pub_id: str,
    partition_pub_id: str,
    partition_digest: str,
    plan_digest: str,
    cursor: int,
    campaign_reference: CampaignWorkflowReference,
    workflow_input: CollectionV2WorkflowInput,
) -> str:
    return _sha256_json(
        {
            "campaign_id": str(campaign_id),
            "campaign_pub_id": campaign_pub_id,
            "cursor": cursor,
            "partition_digest": partition_digest,
            "partition_pub_id": partition_pub_id,
            "plan_digest": plan_digest,
            "version": WORKFLOW_START_COMMAND_VERSION,
            "outbox_type": COLLECTION_V2_OUTBOX_TYPE,
            "workflow_type": COLLECTION_V2_WORKFLOW_TYPE,
            "task_queue": COLLECTION_V2_TASK_QUEUE,
            "payload_schema_version": COLLECTION_V2_PAYLOAD_SCHEMA,
            "campaign_reference": campaign_reference.model_dump(mode="json"),
            "workflow_input": json.loads(workflow_input.payload_json),
        }
    )


__all__ = [
    "CampaignExecutionPartition",
    "CampaignExecutionPlan",
    "CampaignWorkflowLaunchContext",
    "CampaignWorkflowStartCommand",
    "EXECUTION_PARTITION_VERSION",
    "EXECUTION_PLAN_VERSION",
    "SchedulerV2Error",
    "WORKFLOW_START_COMMAND_VERSION",
    "build_campaign_workflow_start_command",
    "execution_partition_at",
    "iter_execution_partitions",
    "plan_campaign_execution",
]
