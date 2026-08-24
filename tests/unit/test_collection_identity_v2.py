from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import geo_platform.collection.identity_v2 as identity_v2
import pytest
from geo_platform.collection.identity_v2 import (
    MAX_CAMPAIGN_EXECUTION_PAGE_SIZE,
    MAX_CAMPAIGN_SLOT_CHUNK_SIZE,
    CampaignActors,
    CampaignAssemblyBlueprint,
    CampaignFreezeRequest,
    CampaignMaterializationCheckpoint,
    ConfigFreezeRequest,
    FrozenCampaign,
    IdentityV2Error,
    NonPrimarySlotRequest,
    PersistedCampaignFreezeConfirmation,
    QuestionSlotRef,
    activate_frozen_config,
    advance_campaign_checkpoint,
    assert_frozen_config_unchanged,
    build_campaign_workflow_reference,
    build_non_primary_slot_request,
    campaign_slot_at,
    confirm_campaign_frozen,
    freeze_campaign,
    freeze_config,
    iter_campaign_slot_chunks,
    slot_for_retry,
    transition_config_lifecycle,
)
from geo_platform.collection.models import (
    CollectionCampaign,
    CollectionCampaignTarget,
    CollectionConfigRevisionV2,
    CollectionConfigTargetV2,
    CollectionPrimarySlot,
    CollectionRun,
    CollectionSamplingLeg,
    CollectionTask,
)
from pydantic import ValidationError
from sqlalchemy import UniqueConstraint

from domain.collection.surface import (
    MAINLAND_PROVINCE_CODES,
    CapabilityDeclaration,
    CapabilityRegistry,
    CapabilityStatus,
    CollectionConfigV2,
    CollectionSurface,
    CollectionTarget,
    ConfigLifecycleState,
    SlotRole,
)

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
PROJECT_ID = UUID("00000000-0000-0000-0000-000000000002")


def _target(
    surface: CollectionSurface,
    *,
    platform: str = "doubao",
    product_variant: str = "default",
    modes: tuple[str, ...] = ("normal",),
) -> CollectionTarget:
    return CollectionTarget(
        platform=platform,
        collection_surface=surface,
        product_variant=product_variant,
        interaction_modes=modes,
    )


def _config(
    *targets: CollectionTarget,
    samples_per_cell: int = 1,
    province_codes: tuple[str, ...] = ("110000",),
    schedule_policy: dict[str, object] | None = None,
) -> CollectionConfigV2:
    return CollectionConfigV2.model_validate(
        {
            "question_set_revision": "questions-v1",
            "collection_targets": targets,
            "province_codes": province_codes,
            "samples_per_cell": samples_per_cell,
            "schedule_policy": schedule_policy or {},
            "comparison_policy_revision": "comparison-v1",
        }
    )


def _registry(
    config: CollectionConfigV2,
    *,
    status: CapabilityStatus = CapabilityStatus.SUPPORTED,
    production_allowed: bool = True,
    omit_last: bool = False,
) -> CapabilityRegistry:
    capabilities = [
        CapabilityDeclaration(
            capability_revision=(f"{target.platform}-{target.collection_surface.value}-{mode}-v1"),
            platform=target.platform,
            collection_surface=target.collection_surface,
            product_variant=target.product_variant,
            interaction_mode=mode,
            status=status,
            production_allowed=production_allowed,
            unsupported_reason=(
                "not-implemented" if status is CapabilityStatus.UNSUPPORTED else None
            ),
        )
        for target in config.collection_targets
        for mode in target.interaction_modes
    ]
    if omit_last:
        capabilities.pop()
    return CapabilityRegistry(registry_revision="capabilities-v1", capabilities=tuple(capabilities))


def _freeze(
    config: CollectionConfigV2,
    *,
    registry: CapabilityRegistry | None = None,
    revision_pub_id: str = "ccr2_revision_1",
):
    return freeze_config(
        ConfigFreezeRequest(
            revision_pub_id=revision_pub_id,
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            revision=1,
            config=config,
            capability_registry=registry or _registry(config),
            change_reason="initial-freeze",
            frozen_at=NOW,
        )
    )


def _campaign_request(
    frozen_config,
    *,
    question_slots: tuple[QuestionSlotRef, ...] = (
        QuestionSlotRef(question_slot_id="question-1", question_revision="qrev-1"),
    ),
    supplementary_slots: tuple[NonPrimarySlotRequest, ...] = (),
) -> CampaignFreezeRequest:
    return CampaignFreezeRequest(
        campaign_pub_id="campaign_1",
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        config_revision=frozen_config,
        question_slots=question_slots,
        time_window_key="2026-08-24/2026-08-25",
        run_trigger_source="manual",
        trigger_idempotency_key="campaign-idempotency-1",
        actors=CampaignActors(
            created_by_pub_id="user-1",
            approved_by_pub_id="reviewer-1",
            triggered_by_pub_id="user-1",
        ),
        binding_policy_revision="binding-policy-v1",
        supplementary_slots=supplementary_slots,
        frozen_at=NOW,
    )


def _unique_columns(model: type) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_three_surfaces_generate_distinct_slot_and_business_keys() -> None:
    config = _config(*(_target(surface) for surface in CollectionSurface))

    campaign = freeze_campaign(_campaign_request(_freeze(config)))
    slots = tuple(campaign_slot_at(campaign, ordinal) for ordinal in range(3))

    assert campaign.expected_primary_slot_count == 3
    assert {slot.identity.collection_surface for slot in slots} == set(CollectionSurface)
    assert len({slot.slot_key for slot in slots}) == 3
    assert all(slot.slot_key == slot.identity.business_key for slot in slots)


def test_campaign_only_expands_explicitly_configured_surface() -> None:
    campaign = freeze_campaign(
        _campaign_request(_freeze(_config(_target(CollectionSurface.CONSUMER_WEB))))
    )

    assert len(campaign.targets) == 1
    assert len(campaign.legs) == 1
    assert campaign.expected_primary_slot_count == 1
    assert (
        campaign_slot_at(campaign, 0).identity.collection_surface is CollectionSurface.CONSUMER_WEB
    )
    specification = json.loads(campaign.membership_specification_json)
    assert [target["target_key"] for target in specification["targets"]] == [
        campaign.targets[0].target_key
    ]
    assert "slots" not in specification


@pytest.mark.parametrize(
    ("status", "production_allowed", "omit_last", "expected_code"),
    [
        (CapabilityStatus.SUPPORTED, True, True, "capability_not_declared"),
        (
            CapabilityStatus.UNSUPPORTED,
            False,
            False,
            "capability_unsupported",
        ),
    ],
)
def test_freeze_rejects_missing_or_unsupported_capability(
    status: CapabilityStatus,
    production_allowed: bool,
    omit_last: bool,
    expected_code: str,
) -> None:
    config = _config(_target(CollectionSurface.CONSUMER_WEB))

    with pytest.raises(IdentityV2Error) as caught:
        _freeze(
            config,
            registry=_registry(
                config,
                status=status,
                production_allowed=production_allowed,
                omit_last=omit_last,
            ),
        )

    assert caught.value.code == expected_code
    assert caught.value.as_dict()["context"]


def test_samples_expand_to_one_through_three_and_retry_reuses_same_slot() -> None:
    campaign = freeze_campaign(
        _campaign_request(
            _freeze(
                _config(
                    _target(CollectionSurface.CONSUMER_WEB),
                    samples_per_cell=3,
                )
            )
        )
    )

    slots = tuple(campaign_slot_at(campaign, ordinal) for ordinal in range(3))
    assert [slot.ordinal for slot in slots] == [0, 1, 2]
    assert [slot.identity.sample_ordinal for slot in slots] == [1, 2, 3]
    retried = slot_for_retry(campaign, slot_ordinal=2, slot_key=slots[-1].slot_key)
    assert retried.id == slots[-1].id
    assert retried.slot_key == slots[-1].slot_key
    assert retried.identity.sample_ordinal == 3
    assert {slot.identity.sample_ordinal for slot in slots} == {1, 2, 3}


def test_supplementary_and_topup_are_explicit_and_do_not_replace_primary() -> None:
    target = _target(CollectionSurface.CONSUMER_APP)
    frozen_config = _freeze(_config(target, samples_per_cell=2))
    topup = build_non_primary_slot_request(
        question_slot_id="question-1",
        target=target,
        province_code="110000",
        interaction_mode="normal",
        sample_ordinal=2,
        slot_role=SlotRole.TOPUP,
        reason="primary-capture-failed",
    )

    campaign = freeze_campaign(_campaign_request(frozen_config, supplementary_slots=(topup,)))
    slots = tuple(campaign_slot_at(campaign, ordinal) for ordinal in range(3))
    primary_slots = tuple(slot for slot in slots if slot.identity.slot_role is SlotRole.PRIMARY)
    non_primary_slots = tuple(
        slot for slot in slots if slot.identity.slot_role is not SlotRole.PRIMARY
    )

    assert campaign.expected_primary_slot_count == 2
    assert campaign.expected_non_primary_slot_count == 1
    extra = non_primary_slots[0]
    assert extra.identity.slot_role is SlotRole.TOPUP
    assert extra.role_reason == "primary-capture-failed"
    assert extra.related_primary_slot_key in {slot.slot_key for slot in primary_slots}
    assert extra.slot_key not in {slot.slot_key for slot in primary_slots}

    with pytest.raises(IdentityV2Error) as caught:
        build_non_primary_slot_request(
            question_slot_id="question-1",
            target=target,
            province_code="110000",
            interaction_mode="normal",
            sample_ordinal=2,
            slot_role=SlotRole.PRIMARY,
            reason="not-allowed",
        )
    assert caught.value.code == "non_primary_slot_role_required"


def test_route_and_resource_cannot_enter_logical_slot_input_or_key() -> None:
    target = _target(CollectionSurface.CONSUMER_WEB)
    campaign = freeze_campaign(_campaign_request(_freeze(_config(target))))
    slot = campaign_slot_at(campaign, 0)

    assert "route" not in type(slot).model_fields
    assert "resource" not in type(slot).model_fields
    assert "route" not in slot.slot_key
    assert "resource" not in slot.slot_key
    with pytest.raises(ValidationError):
        NonPrimarySlotRequest.model_validate(
            {
                "question_slot_id": "question-1",
                "platform": "doubao",
                "collection_surface": "consumer_web",
                "product_variant": "default",
                "province_code": "110000",
                "interaction_mode": "normal",
                "sample_ordinal": 1,
                "slot_role": "topup",
                "reason": "retry-capture",
                "route_id": "relay-a",
            }
        )


def test_config_and_campaign_hashes_are_stable_across_semantic_ordering() -> None:
    web = _target(CollectionSurface.CONSUMER_WEB, modes=("research", "normal"))
    app = _target(CollectionSurface.CONSUMER_APP)
    first_config = _config(
        web,
        app,
        province_codes=("310000", "110000"),
        schedule_policy={"timezone": "Asia/Shanghai", "enabled": True},
    )
    second_config = _config(
        app,
        _target(CollectionSurface.CONSUMER_WEB, modes=("normal", "research")),
        province_codes=("110000", "310000"),
        schedule_policy={"enabled": True, "timezone": "Asia/Shanghai"},
    )
    first_frozen = _freeze(first_config)
    second_frozen = _freeze(second_config)
    questions_forward = (
        QuestionSlotRef(question_slot_id="question-2", question_revision="qrev-2"),
        QuestionSlotRef(question_slot_id="question-1", question_revision="qrev-1"),
    )
    questions_reverse = tuple(reversed(questions_forward))

    first_campaign = freeze_campaign(
        _campaign_request(first_frozen, question_slots=questions_forward)
    )
    second_campaign = freeze_campaign(
        _campaign_request(second_frozen, question_slots=questions_reverse)
    )

    assert first_config.canonical_json == second_config.canonical_json
    assert first_frozen.revision_hash == second_frozen.revision_hash
    assert first_frozen.target_row_values == second_frozen.target_row_values
    assert (
        first_campaign.membership_specification_json
        == second_campaign.membership_specification_json
    )
    assert first_campaign.specification_hash == second_campaign.specification_hash
    assert first_campaign.expected_slot_count == second_campaign.expected_slot_count
    assert [
        campaign_slot_at(first_campaign, ordinal).slot_key
        for ordinal in range(first_campaign.expected_slot_count)
    ] == [
        campaign_slot_at(second_campaign, ordinal).slot_key
        for ordinal in range(second_campaign.expected_slot_count)
    ]


def test_lifecycle_immutability_and_production_admission_fail_closed() -> None:
    config = _config(_target(CollectionSurface.CONSUMER_WEB))
    pilot = _freeze(
        config,
        registry=_registry(
            config,
            status=CapabilityStatus.PILOT,
            production_allowed=False,
        ),
    )

    with pytest.raises(IdentityV2Error) as caught:
        activate_frozen_config(pilot, activated_at=NOW, readiness_passed=True)
    assert caught.value.code == "capability_not_production_allowed"

    production = _freeze(config)
    active = activate_frozen_config(production, activated_at=NOW, readiness_passed=True)
    assert active.lifecycle_state is ConfigLifecycleState.ACTIVE
    assert active.revision_hash == production.revision_hash

    changed = _config(
        _target(CollectionSurface.CONSUMER_WEB),
        samples_per_cell=2,
    )
    for state in (
        ConfigLifecycleState.FROZEN,
        ConfigLifecycleState.ACTIVE,
        ConfigLifecycleState.SUPERSEDED,
        ConfigLifecycleState.RETIRED,
    ):
        immutable = production.model_copy(update={"lifecycle_state": state})
        with pytest.raises(IdentityV2Error) as immutable_error:
            assert_frozen_config_unchanged(immutable, changed)
        assert immutable_error.value.code == "immutable_frozen_revision"

    with pytest.raises(IdentityV2Error) as transition_error:
        transition_config_lifecycle(
            ConfigLifecycleState.FROZEN,
            ConfigLifecycleState.CANDIDATE,
        )
    assert transition_error.value.code == "invalid_config_lifecycle_transition"


def test_campaign_rejects_duplicate_questions_and_non_frozen_config() -> None:
    frozen_config = _freeze(_config(_target(CollectionSurface.CONSUMER_WEB)))
    duplicate_questions = (
        QuestionSlotRef(question_slot_id="question-1", question_revision="qrev-1"),
        QuestionSlotRef(question_slot_id="question-1", question_revision="qrev-2"),
    )

    with pytest.raises(ValidationError, match="duplicate_question_slot"):
        _campaign_request(frozen_config, question_slots=duplicate_questions)

    candidate = frozen_config.model_copy(update={"lifecycle_state": ConfigLifecycleState.CANDIDATE})
    with pytest.raises(ValidationError, match="campaign_requires_frozen_or_active_config"):
        _campaign_request(candidate)


def test_row_values_are_complete_canonical_and_secret_free() -> None:
    campaign = freeze_campaign(
        _campaign_request(_freeze(_config(_target(CollectionSurface.CONSUMER_WEB))))
    )
    config = _freeze(_config(_target(CollectionSurface.CONSUMER_WEB)))

    revision_values = config.revision_row_values
    assert revision_values["revision_hash"] == config.revision_hash
    assert json.loads(str(revision_values["province_codes_json"])) == ["110000"]
    assert json.loads(str(config.target_row_values[0]["capability_revisions_json"])) == {
        "normal": "doubao-consumer_web-normal-v1"
    }
    assert campaign.campaign_row_values["time_window_key"] == "2026-08-24/2026-08-25"
    chunk = next(iter_campaign_slot_chunks(campaign, chunk_size=1))
    assert chunk.slot_row_values[0]["campaign_id"] == campaign.id
    serialized = json.dumps(
        {
            "campaign": campaign.campaign_row_values,
            "slots": chunk.slot_row_values,
        },
        default=str,
    ).lower()
    assert "cookie" not in serialized
    assert "access_token" not in serialized
    assert "cdp" not in serialized


def test_persistence_plans_stage_children_before_atomic_freeze() -> None:
    config = _freeze(_config(_target(CollectionSurface.CONSUMER_WEB)))
    config_plan = config.persistence_plan

    assert [step.table for step in config_plan.ordered_steps] == [
        "collection_config_revision_v2",
        "collection_config_target_v2",
        "collection_config_revision_v2",
    ]
    assert config_plan.parent_insert_values["lifecycle_state"] == "candidate"
    assert config_plan.parent_insert_values["frozen_at"] is None
    assert len(config_plan.target_insert_values) == 1
    assert config_plan.finalization_match_values["lifecycle_state"] == "candidate"
    assert config_plan.finalization_values == {
        "lifecycle_state": "frozen",
        "frozen_at": NOW,
    }

    campaign = freeze_campaign(_campaign_request(config))
    campaign_plan = campaign.persistence_plan
    assert [step.table for step in campaign_plan.ordered_steps] == [
        "collection_campaign",
        "collection_campaign_target",
        "collection_sampling_leg",
    ]
    assert campaign_plan.parent_insert_values["state"] == "assembling"
    assert campaign_plan.parent_insert_values["materialization_cursor"] == 0
    assert campaign_plan.parent_insert_values["materialized_slot_count"] == 0
    assert campaign_plan.parent_insert_values["membership_hash"] is None
    assert (
        campaign_plan.parent_insert_values["membership_specification_json"]
        == campaign.membership_specification_json
    )
    assert campaign_plan.parent_insert_values["specification_hash"] == campaign.specification_hash
    assert campaign_plan.parent_insert_values["frozen_at"] is None
    assert len(campaign_plan.campaign_target_insert_values) == len(campaign.targets)
    assert len(campaign_plan.leg_insert_values) == len(campaign.legs)
    assert not hasattr(campaign_plan, "slot_insert_values")

    checkpoint = campaign_plan.initial_checkpoint
    chunk = next(campaign_plan.iter_slot_chunks(chunk_size=1))
    checkpoint = advance_campaign_checkpoint(campaign, checkpoint, chunk)
    finalization = campaign_plan.validate_complete(checkpoint)
    assert finalization.finalization_match_values["state"] == "assembling"
    assert finalization.finalization_match_values["materialization_state"] == "complete"
    assert finalization.finalization_values == {
        "membership_hash": checkpoint.membership_chain_hash,
        "frozen_at": NOW,
        "state": "frozen",
    }
    assert campaign.requires_persisted_freeze_confirmation is True


def _stream_summary(
    campaign: CampaignAssemblyBlueprint,
    *,
    chunk_size: int,
    sampled_ordinals: frozenset[int] = frozenset(),
) -> tuple[CampaignMaterializationCheckpoint, int, int, dict[int, tuple[str, str]]]:
    checkpoint = campaign.persistence_plan.initial_checkpoint
    materialized_count = 0
    peak_chunk_size = 0
    samples: dict[int, tuple[str, str]] = {}
    for chunk in iter_campaign_slot_chunks(campaign, chunk_size=chunk_size):
        assert chunk.start_slot_ordinal == materialized_count
        materialized_count += chunk.slot_count
        peak_chunk_size = max(peak_chunk_size, chunk.slot_count)
        for slot in chunk.slots:
            if slot.ordinal in sampled_ordinals:
                samples[slot.ordinal] = (slot.slot_key, slot.slot_identity_hash)
        checkpoint = chunk.checkpoint
    return checkpoint, materialized_count, peak_chunk_size, samples


def test_large_campaign_streams_279000_slots_with_chunk_independent_identity_and_digest() -> None:
    config = _config(
        *(_target(surface) for surface in CollectionSurface),
        samples_per_cell=3,
        province_codes=tuple(sorted(MAINLAND_PROVINCE_CODES)),
    )
    questions = tuple(
        QuestionSlotRef(
            question_slot_id=f"question-{index:04d}",
            question_revision=f"qrev-{index:04d}",
        )
        for index in range(1000)
    )
    campaign = freeze_campaign(_campaign_request(_freeze(config), question_slots=questions))
    sampled_ordinals = frozenset({0, 92_999, 93_000, 185_999, 186_000, 278_999})

    assert campaign.expected_primary_slot_count == 279_000
    assert campaign.expected_non_primary_slot_count == 0
    assert campaign.expected_slot_count == 279_000
    assert "slots" not in type(campaign).model_fields
    assert "membership_json" not in type(campaign).model_fields
    assert "slots" not in json.loads(campaign.membership_specification_json)
    assert campaign_slot_at(campaign, 278_999).identity.sample_ordinal == 3

    first = _stream_summary(
        campaign,
        chunk_size=257,
        sampled_ordinals=sampled_ordinals,
    )
    second = _stream_summary(
        campaign,
        chunk_size=MAX_CAMPAIGN_SLOT_CHUNK_SIZE,
        sampled_ordinals=sampled_ordinals,
    )

    assert first[1] == second[1] == 279_000
    assert first[2] == 257
    assert second[2] == MAX_CAMPAIGN_SLOT_CHUNK_SIZE
    assert first[0].next_slot_ordinal == second[0].next_slot_ordinal == 279_000
    assert first[0].membership_chain_hash == second[0].membership_chain_hash
    assert first[3] == second[3]


def test_slot_order_is_target_question_province_mode_sample_then_role() -> None:
    app = _target(CollectionSurface.CONSUMER_APP, modes=("research", "normal"))
    web = _target(CollectionSurface.CONSUMER_WEB)
    config = _config(
        web,
        app,
        samples_per_cell=2,
        province_codes=("310000", "110000"),
    )
    frozen = _freeze(config)
    questions = (
        QuestionSlotRef(question_slot_id="question-2", question_revision="qrev-2"),
        QuestionSlotRef(question_slot_id="question-1", question_revision="qrev-1"),
    )
    campaign = freeze_campaign(_campaign_request(frozen, question_slots=questions))

    first = campaign_slot_at(campaign, 0)
    second_sample = campaign_slot_at(campaign, 1)
    second_mode = campaign_slot_at(campaign, 2)
    second_question = campaign_slot_at(campaign, 8)
    second_target = campaign_slot_at(campaign, 16)
    assert first.identity.question_slot_id == "question-1"
    assert first.identity.province_code == "110000"
    assert first.identity.interaction_mode == "normal"
    assert second_sample.identity.sample_ordinal == 2
    assert second_mode.identity.interaction_mode == "research"
    assert second_question.identity.question_slot_id == "question-2"
    assert second_target.identity.collection_surface is CollectionSurface.CONSUMER_WEB

    supplementary = build_non_primary_slot_request(
        question_slot_id="question-1",
        target=app,
        province_code="110000",
        interaction_mode="normal",
        sample_ordinal=1,
        slot_role=SlotRole.SUPPLEMENTARY,
        reason="manual-audit",
    )
    topup = supplementary.model_copy(
        update={"slot_role": SlotRole.TOPUP, "reason": "capture-topup"}
    )
    with_roles = freeze_campaign(
        _campaign_request(
            frozen,
            question_slots=questions,
            supplementary_slots=(topup, supplementary),
        )
    )
    assert [campaign_slot_at(with_roles, ordinal).identity.slot_role for ordinal in range(4)] == [
        SlotRole.PRIMARY,
        SlotRole.SUPPLEMENTARY,
        SlotRole.TOPUP,
        SlotRole.PRIMARY,
    ]


def test_chunk_resume_is_exact_idempotent_and_incomplete_campaign_cannot_finalize() -> None:
    campaign = freeze_campaign(
        _campaign_request(
            _freeze(
                _config(
                    _target(CollectionSurface.CONSUMER_WEB, modes=("normal", "research")),
                    samples_per_cell=3,
                    province_codes=("110000", "310000"),
                )
            ),
            question_slots=(
                QuestionSlotRef(question_slot_id="question-1", question_revision="qrev-1"),
                QuestionSlotRef(question_slot_id="question-2", question_revision="qrev-2"),
            ),
        )
    )
    plan = campaign.persistence_plan
    checkpoint = plan.initial_checkpoint
    first = next(plan.iter_slot_chunks(chunk_size=5))
    checkpoint = advance_campaign_checkpoint(campaign, checkpoint, first)
    materialized_ordinals = [slot.ordinal for slot in first.slots]

    uncommitted = next(
        plan.iter_slot_chunks(
            start_cursor=checkpoint.next_slot_ordinal,
            chunk_size=5,
            checkpoint_digest=checkpoint.membership_chain_hash,
        )
    )
    resumed = next(
        plan.iter_slot_chunks(
            start_cursor=checkpoint.next_slot_ordinal,
            chunk_size=5,
            checkpoint_digest=checkpoint.membership_chain_hash,
        )
    )
    assert resumed == uncommitted
    next_checkpoint = advance_campaign_checkpoint(campaign, checkpoint, resumed)
    with pytest.raises(IdentityV2Error) as stale_replay:
        advance_campaign_checkpoint(campaign, next_checkpoint, resumed)
    assert stale_replay.value.code == "campaign_chunk_checkpoint_mismatch"
    materialized_ordinals.extend(slot.ordinal for slot in resumed.slots)

    with pytest.raises(IdentityV2Error) as incomplete:
        plan.validate_complete(next_checkpoint)
    assert incomplete.value.code == "campaign_materialization_incomplete"

    checkpoint = next_checkpoint
    while checkpoint.next_slot_ordinal < campaign.expected_slot_count:
        chunk = next(
            plan.iter_slot_chunks(
                start_cursor=checkpoint.next_slot_ordinal,
                chunk_size=5,
                checkpoint_digest=checkpoint.membership_chain_hash,
            )
        )
        assert chunk.start_slot_ordinal == checkpoint.next_slot_ordinal
        checkpoint = advance_campaign_checkpoint(campaign, checkpoint, chunk)
        materialized_ordinals.extend(slot.ordinal for slot in chunk.slots)
    finalization = plan.validate_complete(checkpoint)
    assert materialized_ordinals == list(range(campaign.expected_slot_count))
    assert finalization.finalization_values["membership_hash"] == checkpoint.membership_chain_hash

    corrupt = checkpoint.model_copy(update={"membership_chain_hash": "0" * 64})
    with pytest.raises(IdentityV2Error) as mismatch:
        plan.validate_complete(corrupt)
    assert mismatch.value.code == "campaign_membership_digest_mismatch"


def test_scheduler_reference_requires_persisted_freeze_and_payload_is_constant_size() -> None:
    assert MAX_CAMPAIGN_EXECUTION_PAGE_SIZE != MAX_CAMPAIGN_SLOT_CHUNK_SIZE
    campaign = freeze_campaign(
        _campaign_request(_freeze(_config(_target(CollectionSurface.CONSUMER_WEB))))
    )
    with pytest.raises(IdentityV2Error) as assembling:
        build_campaign_workflow_reference(  # type: ignore[arg-type]
            campaign,
            partition_pub_id="partition-1",
            start_slot_ordinal=0,
            end_slot_ordinal_exclusive=1,
            cursor=0,
            page_size=1,
        )
    assert assembling.value.code == "scheduler_requires_persisted_frozen_campaign"

    chunk = next(iter_campaign_slot_chunks(campaign, chunk_size=1))
    checkpoint = advance_campaign_checkpoint(
        campaign,
        campaign.persistence_plan.initial_checkpoint,
        chunk,
    )
    finalization = campaign.persistence_plan.validate_complete(checkpoint)
    confirmation = PersistedCampaignFreezeConfirmation(
        campaign_id=campaign.id,
        campaign_pub_id=campaign.campaign_pub_id,
        tenant_id=campaign.tenant_id,
        project_id=campaign.project_id,
        specification_hash=campaign.specification_hash,
        expected_slot_count=campaign.expected_slot_count,
        materialized_slot_count=campaign.expected_slot_count,
        materialization_cursor=campaign.expected_slot_count,
        membership_hash=str(finalization.finalization_values["membership_hash"]),
        frozen_at=NOW,
    )
    frozen = confirm_campaign_frozen(campaign, checkpoint, confirmation)
    assert isinstance(frozen, FrozenCampaign)
    small_payload = build_campaign_workflow_reference(
        frozen,
        partition_pub_id="partition-1",
        start_slot_ordinal=0,
        end_slot_ordinal_exclusive=1,
        cursor=0,
        page_size=1,
    ).payload_json
    large_frozen = frozen.model_copy(
        update={
            "expected_slot_count": 279_000,
            "materialized_slot_count": 279_000,
            "materialization_cursor": 279_000,
        }
    )
    large_payload = build_campaign_workflow_reference(
        large_frozen,
        partition_pub_id="partition-1",
        start_slot_ordinal=0,
        end_slot_ordinal_exclusive=1,
        cursor=0,
        page_size=1,
    ).payload_json
    assert large_payload == small_payload
    assert "question" not in large_payload
    assert "slots" not in large_payload


def test_stage1_v2_has_no_legacy_run_service_or_fixed_ten_thousand_limit() -> None:
    sources = (
        Path(identity_v2.__file__),
        Path(identity_v2.__file__).with_name("campaign_materialization_v2.py"),
    )
    for source_path in sources:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        integer_constants = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, int)
            and not isinstance(node.value, bool)
        }
        assert not any(module.endswith("run_service") for module in imported_modules)
        assert 10_000 not in integer_constants


def test_orm_tables_express_project_scoped_identity_and_foreign_keys() -> None:
    assert CollectionConfigRevisionV2.__tablename__ == "collection_config_revision_v2"
    assert CollectionConfigTargetV2.__tablename__ == "collection_config_target_v2"
    assert CollectionCampaign.__tablename__ == "collection_campaign"
    assert CollectionCampaignTarget.__tablename__ == "collection_campaign_target"
    assert CollectionSamplingLeg.__tablename__ == "collection_sampling_leg"
    assert CollectionPrimarySlot.__tablename__ == "collection_primary_slot"

    assert ("tenant_id", "project_id", "revision") in _unique_columns(CollectionConfigRevisionV2)
    assert (
        "tenant_id",
        "project_id",
        "config_revision_id",
        "target_key",
    ) in _unique_columns(CollectionConfigTargetV2)
    assert (
        "tenant_id",
        "project_id",
        "campaign_id",
        "slot_key",
    ) in _unique_columns(CollectionPrimarySlot)

    for model in (
        CollectionConfigRevisionV2,
        CollectionConfigTargetV2,
        CollectionCampaign,
        CollectionCampaignTarget,
        CollectionSamplingLeg,
        CollectionPrimarySlot,
    ):
        assert ("id", "tenant_id", "project_id") in _unique_columns(model)

    slot_fk_pairs = {
        (element.parent.name, element.target_fullname)
        for constraint in CollectionPrimarySlot.__table__.foreign_key_constraints
        for element in constraint.elements
    }
    assert (
        "sampling_leg_id",
        "platform.collection_sampling_leg.id",
    ) in slot_fk_pairs
    assert (
        "campaign_target_id",
        "platform.collection_sampling_leg.campaign_target_id",
    ) in slot_fk_pairs
    assert (
        "campaign_id",
        "platform.collection_sampling_leg.campaign_id",
    ) in slot_fk_pairs
    assert "project_id" in CollectionPrimarySlot.__table__.columns

    assert {
        "collection_surface",
        "surface_assignment_basis",
        "legacy_contract_version",
        "config_revision_v2_id",
        "campaign_id",
    }.issubset(set(CollectionRun.__table__.columns.keys()))
    assert {
        "collection_surface",
        "surface_assignment_basis",
        "legacy_contract_version",
        "requested_surface",
        "observed_surface",
        "observed_product_variant",
        "campaign_target_id",
        "sampling_leg_id",
        "primary_slot_id",
    }.issubset(set(CollectionTask.__table__.columns.keys()))
