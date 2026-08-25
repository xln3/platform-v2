from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256
from uuid import UUID

import pytest
from pydantic import ValidationError

from domain.collection.submission import (
    AnalysisCommand,
    AnalysisDisposition,
    AnalysisExistingCapturePort,
    CaptureChannel,
    CaptureDataClassification,
    CaptureDisposition,
    CaptureExistingCommand,
    CaptureExistingPort,
    CaptureNormalizationDecision,
    CaptureProvenance,
    CaptureStagingIntent,
    CaptureStagingRef,
    CaptureTruth,
    ClaimPlan,
    FreshSubmissionClaim,
    LeaseFenceRef,
    NoSubmitDecision,
    OperationIdentity,
    OperationKeyMaterial,
    OwnerAuthorityRef,
    OwnerClaimCasObservation,
    OwnerClaimCasStatus,
    PreflightCommand,
    PreflightDecision,
    PreflightObservation,
    PrepareDisposition,
    PrepareSubmissionCommand,
    QuotaTerminalEffect,
    ReconciliationDisposition,
    RecoveryDecision,
    RequestManifest,
    SendingReconciliationCommand,
    SlotOutcome,
    SubmissionOperationTruth,
    SubmissionProtocolError,
    SubmitDisposition,
    SubmitOnceCommand,
    SubmitOncePort,
    SurfaceProductRef,
    TerminalReason,
    VerifiedPreflight,
    WorkflowOperationInput,
    apply_analysis_disposition,
    apply_capture_disposition,
    apply_preflight_not_sent,
    apply_submit_disposition,
    authority_digest,
    begin_capture,
    canonical_json,
    confirm_owner_claim,
    derive_slot_outcome,
    deterministic_capture_staging_intent,
    deterministic_operation_key,
    deterministic_outbox_key,
    deterministic_provider_idempotency_key,
    initial_analysis_truth,
    initial_capture_truth,
    lease_fence_set_digest,
    link_immutable_capture,
    normalize_capture,
    operation_ref,
    plan_owner_claim,
    prepare_submission,
    queue_analysis,
    reconcile_sending,
    request_manifest_digest,
    start_analysis,
    verify_preflight,
)
from domain.collection.surface import AnalysisState, CaptureState, CollectionSurface, SendState

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
HASH_A = sha256(b"a").hexdigest()
HASH_B = sha256(b"b").hexdigest()
TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
PROJECT_ID = UUID("00000000-0000-0000-0000-000000000002")


def _identity(*, generation: int = 1, request_hash: str = HASH_A) -> OperationIdentity:
    target_key = (
        "collection-target-v1|platform=doubao|collection_surface=consumer_web|"
        "product_variant=web-chat"
    )
    material = OperationKeyMaterial(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        campaign_pub_id="campaign-1",
        slot_pub_id="slot-1",
        target_key=target_key,
        leg_key=(
            "collection-leg-v1|campaign_id=campaign-1|platform=doubao|"
            "collection_surface=consumer_web|product_variant=web-chat|"
            "province_code=110000|interaction_mode=normal"
        ),
        logical_item_key=(
            "collection-slot-v1|campaign_id=campaign-1|question_slot_id=question-1|"
            "platform=doubao|collection_surface=consumer_web|product_variant=web-chat|"
            "province_code=110000|interaction_mode=normal|sample_ordinal=1|"
            "slot_role=primary"
        ),
        generation=generation,
        operation_policy_revision="operation-policy-v1",
    )
    manifest = RequestManifest(
        request_protocol_version="provider-request-v1",
        request_schema_revision="request-schema-v1",
        request_payload_ref="request-payload-1",
        request_payload_sha256=request_hash,
    )
    operation_key = deterministic_operation_key(material)
    return OperationIdentity(
        material=material,
        surface_product=SurfaceProductRef(
            platform="doubao",
            collection_surface=CollectionSurface.CONSUMER_WEB,
            product_variant="web-chat",
            target_key=target_key,
        ),
        operation_pub_id=f"operation-{generation}",
        operation_key=operation_key,
        request_manifest=manifest,
        request_manifest_sha256=request_manifest_digest(manifest),
        provider_idempotency_key=deterministic_provider_idempotency_key(operation_key),
    )


def _prepared() -> SubmissionOperationTruth:
    result = prepare_submission(PrepareSubmissionCommand(identity=_identity(), prepared_at=NOW))
    assert result.disposition is PrepareDisposition.CREATED
    return result.operation


def _authority() -> OwnerAuthorityRef:
    fence = LeaseFenceRef(
        lease_pub_id="lease-1",
        binding_resource_pub_id="binding-resource-1",
        resource_role="primary-browser",
        owner_handle="web-owner-1",
        generation=7,
        acquired_at=NOW - timedelta(minutes=2),
        expires_at=NOW + timedelta(minutes=10),
    )
    fences = (fence,)
    return OwnerAuthorityRef(
        grant_pub_id="grant-1",
        grant_revision=3,
        binding_revision_pub_id="binding-1",
        owner_handle="web-owner-1",
        checked_at=NOW - timedelta(minutes=1),
        valid_until=NOW + timedelta(minutes=5),
        lease_fences=fences,
        fence_set_sha256=lease_fence_set_digest(fences),
    )


def _verified_ready(operation: SubmissionOperationTruth) -> VerifiedPreflight:
    authority = _authority()
    command = PreflightCommand(
        operation=operation_ref(operation.identity),
        expected_state_version=operation.state_version,
        authority=authority,
    )
    observation = PreflightObservation(
        operation=operation_ref(operation.identity),
        authority_sha256=authority_digest(authority),
        decision=PreflightDecision.READY,
        observed_at=NOW,
        evidence_ref="preflight-evidence-1",
        evidence_sha256=HASH_A,
    )
    return verify_preflight(operation, command, observation)


def _fresh_claim(
    operation: SubmissionOperationTruth,
) -> tuple[SubmissionOperationTruth, FreshSubmissionClaim]:
    plan = plan_owner_claim(
        operation,
        _verified_ready(operation),
        claim_pub_id="claim-1",
        owner_dispatch_ref="owner-dispatch-1",
        owner_wal_evidence_sha256=HASH_A,
        claimed_at=NOW + timedelta(seconds=1),
    )
    assert isinstance(plan, ClaimPlan)
    sending = SubmissionOperationTruth(
        identity=operation.identity,
        send_state=SendState.SENDING,
        state_version=plan.cas.next_state_version,
        prepared_at=operation.prepared_at,
        claim=plan.cas.claim,
    )
    confirmed = confirm_owner_claim(
        plan,
        OwnerClaimCasObservation(
            status=OwnerClaimCasStatus.FRESHLY_APPLIED,
            persisted=sending,
        ),
    )
    assert isinstance(confirmed, FreshSubmissionClaim)
    return sending, confirmed


def _submit_command(claim: FreshSubmissionClaim, identity: OperationIdentity) -> SubmitOnceCommand:
    return SubmitOnceCommand(
        fresh_claim=claim,
        request_manifest=identity.request_manifest,
        request_manifest_sha256=identity.request_manifest_sha256,
        provider_idempotency_key=identity.provider_idempotency_key,
    )


def _confirmed_sent() -> SubmissionOperationTruth:
    prepared = _prepared()
    sending, claim = _fresh_claim(prepared)
    transition = apply_submit_disposition(
        sending,
        _submit_command(claim, sending.identity),
        SubmitDisposition(
            send_state=SendState.CONFIRMED_SENT,
            reason=TerminalReason.SUBMITTED,
            boundary_entered=True,
            evidence_ref="provider-evidence-1",
            evidence_sha256=HASH_A,
            provider_submission_ref="provider-submission-1",
            resolved_at=NOW + timedelta(seconds=2),
        ),
    )
    return transition.operation


def _capture_command(
    capture: CaptureTruth,
    *,
    attempt_ref: str,
    requested_at: datetime,
) -> CaptureExistingCommand:
    authority = _authority()
    return CaptureExistingCommand(
        operation=capture.operation,
        source_send_state=capture.source_send_state,
        expected_capture_version=capture.state_version,
        attempt_ref=attempt_ref,
        staging_intent=deterministic_capture_staging_intent(
            operation=capture.operation,
            attempt_ref=attempt_ref,
        ),
        capture_policy_revision="capture-policy-v1",
        requested_surface_product=capture.expected_surface_product,
        authority=authority,
        authority_sha256=authority_digest(authority),
        requested_at=requested_at,
    )


def _capture_provenance(
    surface: CollectionSurface,
    *,
    observed_at: datetime,
) -> CaptureProvenance:
    channel = {
        CollectionSurface.PROVIDER_API: CaptureChannel.PROVIDER_PAYLOAD,
        CollectionSurface.CONSUMER_WEB: CaptureChannel.WEB_DOM,
        CollectionSurface.CONSUMER_APP: CaptureChannel.APP_ACCESSIBILITY,
    }[surface]
    return CaptureProvenance(
        capture_channel=channel,
        capture_protocol_revision=f"capture-protocol-{surface.value}-v1",
        observed_product_version=f"observed-product-{surface.value}-20260824",
        capture_adapter_revision=f"capture-adapter-{surface.value}-v1",
        data_classification=CaptureDataClassification.CUSTOMER_PRIVATE,
        dlp_policy_revision="dlp-policy-v1",
        retention_until=observed_at + timedelta(days=30),
    )


def test_canonical_operation_request_and_outbox_identities_are_stable() -> None:
    identity = _identity()
    assert canonical_json(identity.material) == canonical_json(
        identity.material.model_dump(mode="json")
    )
    assert deterministic_operation_key(identity.material) == identity.operation_key
    assert request_manifest_digest(identity.request_manifest) == identity.request_manifest_sha256

    next_generation = _identity(generation=2)
    assert next_generation.operation_key != identity.operation_key
    key = deterministic_outbox_key(
        event_type="collection.submission.terminal",
        aggregate_ref="operation-1",
        aggregate_version=3,
        payload_sha256=HASH_A,
    )
    assert key == deterministic_outbox_key(
        event_type="collection.submission.terminal",
        aggregate_ref="operation-1",
        aggregate_version=3,
        payload_sha256=HASH_A,
    )
    assert key != deterministic_outbox_key(
        event_type="collection.submission.terminal",
        aggregate_ref="operation-1",
        aggregate_version=4,
        payload_sha256=HASH_A,
    )


def test_canonical_json_normalizes_nested_datetimes_to_utc_without_shape_drift() -> None:
    east_eight = timezone(timedelta(hours=8))
    utc_payload: dict[str, object] = {
        "observed_at": NOW,
        "nested": ({"expires_at": NOW + timedelta(minutes=5)},),
        "tenant_id": TENANT_ID,
        "send_state": SendState.SENDING,
    }
    offset_payload: dict[str, object] = {
        "observed_at": NOW.astimezone(east_eight),
        "nested": ({"expires_at": (NOW + timedelta(minutes=5)).astimezone(east_eight)},),
        "tenant_id": TENANT_ID,
        "send_state": SendState.SENDING,
    }

    assert canonical_json(offset_payload) == canonical_json(utc_payload)
    assert canonical_json(offset_payload) == (
        '{"nested":[{"expires_at":"2026-08-24T12:05:00Z"}],'
        '"observed_at":"2026-08-24T12:00:00Z",'
        '"send_state":"SENDING","tenant_id":"00000000-0000-0000-0000-000000000001"}'
    )


def test_authority_hash_is_stable_after_database_utc_roundtrip() -> None:
    authority = _authority()
    east_eight = timezone(timedelta(hours=8))
    offset_fences = tuple(
        fence.model_copy(
            update={
                "acquired_at": fence.acquired_at.astimezone(east_eight),
                "expires_at": fence.expires_at.astimezone(east_eight),
            }
        )
        for fence in authority.lease_fences
    )
    offset_authority = authority.model_copy(
        update={
            "checked_at": authority.checked_at.astimezone(east_eight),
            "valid_until": authority.valid_until.astimezone(east_eight),
            "lease_fences": offset_fences,
        }
    )
    database_roundtrip = OwnerAuthorityRef.model_validate(
        {
            **offset_authority.model_dump(mode="python"),
            "checked_at": offset_authority.checked_at.astimezone(UTC),
            "valid_until": offset_authority.valid_until.astimezone(UTC),
            "lease_fences": tuple(
                {
                    **fence.model_dump(mode="python"),
                    "acquired_at": fence.acquired_at.astimezone(UTC),
                    "expires_at": fence.expires_at.astimezone(UTC),
                }
                for fence in offset_authority.lease_fences
            ),
        }
    )

    assert canonical_json(offset_authority) == canonical_json(database_roundtrip)
    assert authority_digest(offset_authority) == authority_digest(database_roundtrip)


def test_logical_key_and_workflow_inputs_reject_attempt_worker_resource_and_task_arrays() -> None:
    with pytest.raises(ValidationError, match="attempt"):
        OperationKeyMaterial.model_validate(
            {**_identity().material.model_dump(mode="python"), "attempt": 2}
        )
    workflow = WorkflowOperationInput(
        operation=operation_ref(_identity()), expected_state_version=1
    )
    assert len(workflow.model_dump_json()) < 1024
    with pytest.raises(ValidationError, match="tasks"):
        WorkflowOperationInput.model_validate(
            {**workflow.model_dump(mode="python"), "tasks": ["x"] * 1000}
        )


def test_identity_validators_fail_closed_on_key_or_manifest_drift() -> None:
    payload = _identity().model_dump(mode="python")
    with pytest.raises(ValidationError, match="operation_key_mismatch"):
        OperationIdentity.model_validate({**payload, "operation_key": "wrong-key"})
    with pytest.raises(ValidationError, match="request_manifest_digest_mismatch"):
        OperationIdentity.model_validate({**payload, "request_manifest_sha256": HASH_B})


def test_prepare_exact_replay_never_resets_existing_truth() -> None:
    prepared = _prepared()
    sending, _ = _fresh_claim(prepared)
    replay = prepare_submission(
        PrepareSubmissionCommand(identity=sending.identity, prepared_at=sending.prepared_at),
        existing=sending,
    )
    assert replay.disposition is PrepareDisposition.EXACT_REPLAY
    assert replay.operation is sending
    assert replay.operation.send_state is SendState.SENDING

    with pytest.raises(SubmissionProtocolError, match="prepare_identity_drift"):
        prepare_submission(
            PrepareSubmissionCommand(
                identity=_identity(generation=2), prepared_at=sending.prepared_at
            ),
            existing=sending,
        )


def test_authority_requires_exact_fence_digest_primary_owner_and_live_interval() -> None:
    authority = _authority()
    assert authority.fence_set_sha256 == lease_fence_set_digest(authority.lease_fences)
    payload = authority.model_dump(mode="python")
    with pytest.raises(ValidationError, match="fence_set_digest_mismatch"):
        OwnerAuthorityRef.model_validate({**payload, "fence_set_sha256": HASH_B})
    bad_fence = authority.lease_fences[0].model_copy(update={"owner_handle": "other-owner"})
    with pytest.raises(ValidationError, match="authority_owner_fence_missing"):
        OwnerAuthorityRef.model_validate(
            {
                **payload,
                "lease_fences": (bad_fence,),
                "fence_set_sha256": lease_fence_set_digest((bad_fence,)),
            }
        )

    relay_fence = LeaseFenceRef(
        lease_pub_id="lease-relay-1",
        binding_resource_pub_id="binding-resource-relay-1",
        resource_role="relay",
        owner_handle="relay-owner-1",
        generation=4,
        acquired_at=NOW - timedelta(minutes=2),
        expires_at=NOW + timedelta(minutes=10),
    )
    multi_owner_fences = (*authority.lease_fences, relay_fence)
    multi_owner = OwnerAuthorityRef.model_validate(
        {
            **payload,
            "lease_fences": multi_owner_fences,
            "fence_set_sha256": lease_fence_set_digest(multi_owner_fences),
        }
    )
    assert multi_owner.owner_handle == authority.owner_handle
    assert {fence.owner_handle for fence in multi_owner.lease_fences} == {
        "web-owner-1",
        "relay-owner-1",
    }


def test_preflight_exact_binding_and_preflight_not_sent_terminal() -> None:
    operation = _prepared()
    authority = _authority()
    command = PreflightCommand(
        operation=operation_ref(operation.identity),
        expected_state_version=1,
        authority=authority,
    )
    observation = PreflightObservation(
        operation=command.operation,
        authority_sha256=authority_digest(authority),
        decision=PreflightDecision.CONFIRMED_NOT_SENT,
        not_sent_reason=TerminalReason.UNAVAILABLE,
        observed_at=NOW,
        evidence_ref="unavailable-evidence-1",
        evidence_sha256=HASH_A,
    )
    verified = verify_preflight(operation, command, observation)
    transition = apply_preflight_not_sent(operation, verified)
    assert transition.operation.send_state is SendState.CONFIRMED_NOT_SENT
    assert transition.operation.claim is None
    assert transition.quota_effect is QuotaTerminalEffect.RELEASE
    assert derive_slot_outcome(transition.operation) is SlotOutcome.UNAVAILABLE

    mismatched = observation.model_copy(
        update={"operation": operation_ref(_identity(generation=2))}
    )
    with pytest.raises(SubmissionProtocolError, match="preflight_operation_mismatch"):
        verify_preflight(operation, command, mismatched)


def test_fresh_cas_is_the_only_path_to_submit_capability() -> None:
    prepared = _prepared()
    verified = _verified_ready(prepared)
    plan = plan_owner_claim(
        prepared,
        verified,
        claim_pub_id="claim-1",
        owner_dispatch_ref="owner-dispatch-1",
        owner_wal_evidence_sha256=HASH_A,
        claimed_at=NOW + timedelta(seconds=1),
    )
    assert isinstance(plan, ClaimPlan)
    sending = SubmissionOperationTruth(
        identity=prepared.identity,
        send_state=SendState.SENDING,
        state_version=2,
        prepared_at=prepared.prepared_at,
        claim=plan.cas.claim,
    )

    failed_cas = confirm_owner_claim(
        plan,
        OwnerClaimCasObservation(
            status=OwnerClaimCasStatus.NOT_APPLIED,
            persisted=sending,
        ),
    )
    assert isinstance(failed_cas, NoSubmitDecision)
    assert failed_cas.decision is RecoveryDecision.CAS_NOT_APPLIED

    fresh = confirm_owner_claim(
        plan,
        OwnerClaimCasObservation(
            status=OwnerClaimCasStatus.FRESHLY_APPLIED,
            persisted=sending,
        ),
    )
    assert isinstance(fresh, FreshSubmissionClaim)


def test_sending_and_terminal_recovery_never_produce_resend_permission() -> None:
    prepared = _prepared()
    verified = _verified_ready(prepared)
    sending, _ = _fresh_claim(prepared)
    sending_recovery = plan_owner_claim(
        sending,
        verified,
        claim_pub_id="claim-replay",
        owner_dispatch_ref="owner-dispatch-replay",
        owner_wal_evidence_sha256=HASH_A,
        claimed_at=NOW + timedelta(seconds=3),
    )
    assert isinstance(sending_recovery, NoSubmitDecision)
    assert sending_recovery.decision is RecoveryDecision.NO_SUBMIT_SENDING

    sent = _confirmed_sent()
    sent_recovery = plan_owner_claim(
        sent,
        verified,
        claim_pub_id="claim-replay",
        owner_dispatch_ref="owner-dispatch-replay",
        owner_wal_evidence_sha256=HASH_A,
        claimed_at=NOW + timedelta(seconds=3),
    )
    assert isinstance(sent_recovery, NoSubmitDecision)
    assert sent_recovery.decision is RecoveryDecision.NO_RESEND_CONFIRMED_SENT


def test_submit_terminal_truth_quota_and_outbox_are_deterministic() -> None:
    prepared = _prepared()
    sending, claim = _fresh_claim(prepared)
    command = _submit_command(claim, prepared.identity)
    disposition = SubmitDisposition(
        send_state=SendState.SEND_UNKNOWN,
        reason=TerminalReason.SEND_UNKNOWN,
        boundary_entered=True,
        evidence_ref="ambiguous-provider-result-1",
        evidence_sha256=HASH_A,
        resolved_at=NOW + timedelta(seconds=2),
    )
    first = apply_submit_disposition(sending, command, disposition)
    second = apply_submit_disposition(sending, command, disposition)
    assert first == second
    assert first.operation.send_state is SendState.SEND_UNKNOWN
    assert first.quota_effect is QuotaTerminalEffect.SETTLE_UNKNOWN
    assert derive_slot_outcome(first.operation) is SlotOutcome.SEND_UNKNOWN

    with pytest.raises(ValidationError, match="send_unknown_truth_invalid"):
        SubmitDisposition(
            send_state=SendState.SEND_UNKNOWN,
            reason=TerminalReason.SEND_UNKNOWN,
            boundary_entered=False,
            evidence_ref="evidence-1",
            evidence_sha256=HASH_A,
            resolved_at=NOW,
        )
    with pytest.raises(ValidationError, match="non_submission_proof"):
        SubmitDisposition(
            send_state=SendState.CONFIRMED_NOT_SENT,
            reason=TerminalReason.POST_CLAIM_NOT_SENT,
            boundary_entered=False,
            evidence_ref="evidence-1",
            evidence_sha256=HASH_A,
            resolved_at=NOW,
            terminated_fence_set_sha256=HASH_B,
        )


class _CountingSubmitPort(SubmitOncePort):
    def __init__(self) -> None:
        self.calls = 0

    def submit_once(self, command: SubmitOnceCommand) -> SubmitDisposition:
        self.calls += 1
        return SubmitDisposition(
            send_state=SendState.CONFIRMED_SENT,
            reason=TerminalReason.SUBMITTED,
            boundary_entered=True,
            evidence_ref="provider-evidence-1",
            evidence_sha256=HASH_A,
            provider_submission_ref="provider-submission-1",
            resolved_at=NOW + timedelta(seconds=2),
        )


class _CountingCapturePort(CaptureExistingPort):
    def __init__(self) -> None:
        self.calls = 0

    def capture_existing(self, command: CaptureExistingCommand) -> CaptureDisposition:
        self.calls += 1
        observed_at = command.requested_at + timedelta(seconds=1)
        return CaptureDisposition(
            capture_state=CaptureState.FAILED,
            attempt_ref=command.attempt_ref,
            evidence_ref="capture-failed-evidence",
            evidence_sha256=HASH_B,
            observed_at=observed_at,
            observed_surface_product=command.requested_surface_product,
            provenance=_capture_provenance(
                command.requested_surface_product.collection_surface,
                observed_at=observed_at,
            ),
        )


def test_submit_and_capture_ports_are_separate_and_capture_retry_never_resends() -> None:
    assert "submit_once" in SubmitOncePort.__dict__
    assert "capture_existing" not in SubmitOncePort.__dict__
    assert "capture_existing" in CaptureExistingPort.__dict__
    assert "submit_once" not in CaptureExistingPort.__dict__

    prepared = _prepared()
    sending, claim = _fresh_claim(prepared)
    submit_port = _CountingSubmitPort()
    terminal = apply_submit_disposition(
        sending,
        _submit_command(claim, prepared.identity),
        submit_port.submit_once(_submit_command(claim, prepared.identity)),
    ).operation
    capture = initial_capture_truth(terminal)
    capture_port = _CountingCapturePort()
    for attempt in ("capture-attempt-1", "capture-attempt-2"):
        command = _capture_command(
            capture,
            attempt_ref=attempt,
            requested_at=capture.updated_at + timedelta(seconds=1),
        )
        capture = begin_capture(capture, command)
        observation = capture_port.capture_existing(command)
        capture = apply_capture_disposition(capture, normalize_capture(command, observation))
    assert submit_port.calls == 1
    assert capture_port.calls == 2
    assert terminal.send_state is SendState.CONFIRMED_SENT


def test_capture_staging_intent_is_deterministic_and_raw_upload_must_match() -> None:
    capture = initial_capture_truth(_confirmed_sent())
    command = _capture_command(
        capture,
        attempt_ref="capture-attempt-intent",
        requested_at=NOW + timedelta(seconds=3),
    )
    replay = _capture_command(
        capture,
        attempt_ref="capture-attempt-intent",
        requested_at=NOW + timedelta(seconds=3),
    )
    other = _capture_command(
        capture,
        attempt_ref="capture-attempt-other",
        requested_at=NOW + timedelta(seconds=3),
    )

    assert replay.staging_intent == command.staging_intent
    assert other.staging_intent != command.staging_intent
    intent_hash = sha256(
        canonical_json(
            {
                "attempt_ref": command.attempt_ref,
                "operation": command.operation.model_dump(mode="json"),
                "version": "collection-capture-staging-intent-v1",
            }
        ).encode()
    ).hexdigest()
    assert command.staging_intent.staging_key == f"capture-staging-v1-{intent_hash}"
    assert command.staging_intent.object_ref == f"capture-object-v1-{intent_hash}"
    with pytest.raises(ValidationError, match="capture_staging_intent_not_deterministic"):
        CaptureExistingCommand.model_validate(
            {
                **command.model_dump(mode="python"),
                "staging_intent": CaptureStagingIntent(
                    staging_key="drifted-staging-key",
                    object_ref="drifted-object-ref",
                ),
            }
        )

    observed_at = command.requested_at + timedelta(seconds=1)
    observation = CaptureDisposition(
        capture_state=CaptureState.COMPLETED,
        attempt_ref=command.attempt_ref,
        evidence_ref="capture-intent-evidence",
        evidence_sha256=HASH_B,
        observed_at=observed_at,
        observed_surface_product=command.requested_surface_product,
        provenance=_capture_provenance(
            command.requested_surface_product.collection_surface,
            observed_at=observed_at,
        ),
        staging=CaptureStagingRef(
            staging_key="drifted-staging-key",
            object_ref=command.staging_intent.object_ref,
            content_sha256=HASH_A,
            byte_size=1,
            media_type="application/json",
            capture_schema_revision="capture-schema-v1",
            staged_at=observed_at + timedelta(seconds=1),
        ),
    )
    with pytest.raises(SubmissionProtocolError, match="capture_staging_intent_mismatch"):
        normalize_capture(command, observation)


def test_capture_staging_link_and_analysis_truth_are_independent() -> None:
    terminal = _confirmed_sent()
    capture = initial_capture_truth(terminal)
    command = _capture_command(
        capture,
        attempt_ref="capture-attempt-1",
        requested_at=NOW + timedelta(seconds=3),
    )
    capturing = begin_capture(capture, command)
    staging = CaptureStagingRef(
        staging_key=command.staging_intent.staging_key,
        object_ref=command.staging_intent.object_ref,
        content_sha256=HASH_B,
        byte_size=42,
        media_type="application/json",
        capture_schema_revision="capture-schema-v1",
        staged_at=NOW + timedelta(seconds=6),
    )
    observation = CaptureDisposition(
        capture_state=CaptureState.COMPLETED,
        attempt_ref="capture-attempt-1",
        evidence_ref="capture-evidence-1",
        evidence_sha256=HASH_A,
        observed_at=NOW + timedelta(seconds=5),
        observed_surface_product=command.requested_surface_product,
        provenance=_capture_provenance(
            command.requested_surface_product.collection_surface,
            observed_at=NOW + timedelta(seconds=5),
        ),
        staging=staging,
    )
    completed = apply_capture_disposition(capturing, normalize_capture(command, observation))
    assert completed.provenance == observation.provenance
    link = link_immutable_capture(completed, linked_at=NOW + timedelta(seconds=7))
    same_link = link_immutable_capture(completed, linked_at=NOW + timedelta(seconds=7))
    assert link == same_link

    analysis = initial_analysis_truth(link)
    analysis_command = AnalysisCommand(
        capture_link_key=link.capture_link_key,
        capture_content_sha256=link.content_sha256,
        expected_analysis_version=analysis.state_version,
        attempt_ref="analysis-attempt-1",
        analyzer_revision="analyzer-v1",
        analysis_policy_revision="analysis-policy-v1",
        requested_at=NOW + timedelta(seconds=8),
    )
    queued = queue_analysis(analysis, analysis_command)
    running = start_analysis(
        queued,
        attempt_ref="analysis-attempt-1",
        started_at=NOW + timedelta(seconds=9),
    )
    failed = apply_analysis_disposition(
        running,
        AnalysisDisposition(
            analysis_state=AnalysisState.FAILED,
            attempt_ref="analysis-attempt-1",
            evidence_sha256=HASH_B,
            completed_at=NOW + timedelta(seconds=10),
        ),
    )
    retry = queue_analysis(
        failed,
        analysis_command.model_copy(
            update={
                "expected_analysis_version": failed.state_version,
                "attempt_ref": "analysis-attempt-2",
                "requested_at": NOW + timedelta(seconds=11),
            }
        ),
    )
    assert retry.capture_link == link
    assert retry.analysis_state is AnalysisState.QUEUED
    assert (
        derive_slot_outcome(terminal, capture=completed, analysis=failed)
        is SlotOutcome.ANALYSIS_FAILED
    )


def test_capture_and_analysis_fail_closed_on_ambiguous_or_mixed_truth() -> None:
    prepared = _prepared()
    sending, _ = _fresh_claim(prepared)
    with pytest.raises(SubmissionProtocolError, match="sending_outcome_is_ambiguous"):
        derive_slot_outcome(sending)
    with pytest.raises(SubmissionProtocolError, match="capture_requires_sent_or_unknown"):
        initial_capture_truth(prepared)
    with pytest.raises(ValidationError, match="capture_staging_presence_mismatch"):
        CaptureDisposition(
            capture_state=CaptureState.COMPLETED,
            attempt_ref="capture-attempt-1",
            evidence_ref="evidence-1",
            evidence_sha256=HASH_A,
            observed_at=NOW,
            observed_surface_product=_identity().surface_product,
            provenance=_capture_provenance(
                CollectionSurface.CONSUMER_WEB,
                observed_at=NOW,
            ),
        )


def test_confirmed_send_without_capture_is_pending_not_failed_or_final() -> None:
    terminal = _confirmed_sent()

    assert derive_slot_outcome(terminal) is SlotOutcome.CONFIRMED_SENT_CAPTURE_PENDING


def test_stale_preflight_cannot_overwrite_a_concurrent_owner_claim() -> None:
    prepared = _prepared()
    verified = _verified_ready(prepared)
    sending, _ = _fresh_claim(prepared)
    not_sent_observation = PreflightObservation(
        operation=verified.observation.operation,
        authority_sha256=verified.observation.authority_sha256,
        decision=PreflightDecision.CONFIRMED_NOT_SENT,
        not_sent_reason=TerminalReason.PREFLIGHT_NOT_SENT,
        observed_at=verified.observation.observed_at,
        evidence_ref="not-sent-evidence",
        evidence_sha256=HASH_A,
    )
    stale_verified = verify_preflight(prepared, verified.command, not_sent_observation)
    with pytest.raises(SubmissionProtocolError, match="stale_preflight"):
        apply_preflight_not_sent(sending, stale_verified)


def test_sending_reconciliation_converges_without_submit_capability() -> None:
    prepared = _prepared()
    sending, _ = _fresh_claim(prepared)
    assert sending.claim is not None
    command = SendingReconciliationCommand(
        operation=operation_ref(sending.identity),
        expected_state_version=sending.state_version,
        claim_pub_id=sending.claim.claim_pub_id,
        owner_handle=sending.claim.owner_handle,
        authority_sha256=sending.claim.authority_sha256,
        dispatch_key=sending.claim.dispatch_key,
        owner_dispatch_ref=sending.claim.owner_dispatch_ref,
        owner_wal_evidence_sha256=sending.claim.owner_wal_evidence_sha256,
        durable_evidence_ref="durable-provider-ack-1",
        durable_evidence_sha256=HASH_B,
        observed_at=NOW + timedelta(seconds=2),
    )
    disposition = ReconciliationDisposition(
        send_state=SendState.CONFIRMED_SENT,
        reason=TerminalReason.SUBMITTED,
        boundary_entered=True,
        evidence_ref=command.durable_evidence_ref,
        evidence_sha256=command.durable_evidence_sha256,
        provider_submission_ref="provider-submission-1",
        resolved_at=NOW + timedelta(seconds=3),
    )
    transition = reconcile_sending(sending, command, disposition)
    assert transition.operation.send_state is SendState.CONFIRMED_SENT
    assert transition.quota_effect is QuotaTerminalEffect.SETTLE_CONSUMED
    assert "fresh_claim" not in type(command).model_fields
    recovery = plan_owner_claim(
        transition.operation,
        _verified_ready(prepared),
        claim_pub_id="recovery-claim",
        owner_dispatch_ref="recovery-dispatch",
        owner_wal_evidence_sha256=HASH_A,
        claimed_at=NOW + timedelta(seconds=4),
    )
    assert isinstance(recovery, NoSubmitDecision)
    assert recovery.decision is RecoveryDecision.NO_RESEND_CONFIRMED_SENT


def test_fence_identity_survives_heartbeat_but_changes_on_generation() -> None:
    fence = _authority().lease_fences[0]
    extended = fence.model_copy(update={"expires_at": fence.expires_at + timedelta(minutes=5)})
    next_generation = fence.model_copy(update={"generation": fence.generation + 1})
    assert lease_fence_set_digest((extended,)) == lease_fence_set_digest((fence,))
    assert lease_fence_set_digest((next_generation,)) != lease_fence_set_digest((fence,))


def test_surface_mismatch_is_quarantined_and_cannot_be_linked_or_analyzed() -> None:
    terminal = _confirmed_sent()
    capture = initial_capture_truth(terminal)
    command = _capture_command(
        capture,
        attempt_ref="capture-attempt-mismatch",
        requested_at=NOW + timedelta(seconds=3),
    )
    capturing = begin_capture(capture, command)
    wrong_surface = SurfaceProductRef(
        platform="openai",
        collection_surface=CollectionSurface.PROVIDER_API,
        product_variant="responses",
        target_key=(
            "collection-target-v1|platform=openai|collection_surface=provider_api|"
            "product_variant=responses"
        ),
    )
    staging = CaptureStagingRef(
        staging_key=command.staging_intent.staging_key,
        object_ref=command.staging_intent.object_ref,
        content_sha256=HASH_B,
        byte_size=8,
        media_type="application/json",
        capture_schema_revision="capture-schema-v1",
        staged_at=NOW + timedelta(seconds=5),
    )
    raw = CaptureDisposition(
        capture_state=CaptureState.COMPLETED,
        attempt_ref=command.attempt_ref,
        evidence_ref="surface-mismatch-evidence",
        evidence_sha256=HASH_B,
        observed_at=NOW + timedelta(seconds=4),
        observed_surface_product=wrong_surface,
        provenance=_capture_provenance(
            wrong_surface.collection_surface,
            observed_at=NOW + timedelta(seconds=4),
        ),
        staging=staging,
    )
    normalized = normalize_capture(command, raw)
    assert normalized.normalization is CaptureNormalizationDecision.QUARANTINED_SURFACE_MISMATCH
    assert normalized.provenance == raw.provenance
    quarantined = apply_capture_disposition(capturing, normalized)
    assert quarantined.provenance == raw.provenance
    assert (
        derive_slot_outcome(terminal, capture=quarantined) is SlotOutcome.INVALID_SURFACE_OR_PRODUCT
    )
    with pytest.raises(SubmissionProtocolError, match="staged_capture"):
        link_immutable_capture(quarantined, linked_at=NOW + timedelta(seconds=6))


def test_capture_provenance_rejects_surface_channel_and_retention_drift() -> None:
    observed_product = _identity().surface_product
    observed_at = NOW + timedelta(seconds=4)
    wrong_channel = _capture_provenance(
        CollectionSurface.PROVIDER_API,
        observed_at=observed_at,
    )
    with pytest.raises(ValidationError, match="capture_channel_surface_mismatch"):
        CaptureDisposition(
            capture_state=CaptureState.FAILED,
            attempt_ref="capture-attempt-1",
            evidence_ref="capture-evidence-1",
            evidence_sha256=HASH_A,
            observed_at=observed_at,
            observed_surface_product=observed_product,
            provenance=wrong_channel,
        )

    expired_retention = _capture_provenance(
        CollectionSurface.CONSUMER_WEB,
        observed_at=observed_at,
    ).model_copy(update={"retention_until": observed_at - timedelta(microseconds=1)})
    with pytest.raises(
        ValidationError,
        match="capture_retention_before_durable_observation",
    ):
        CaptureDisposition(
            capture_state=CaptureState.FAILED,
            attempt_ref="capture-attempt-1",
            evidence_ref="capture-evidence-1",
            evidence_sha256=HASH_A,
            observed_at=observed_at,
            observed_surface_product=observed_product,
            provenance=expired_retention,
        )


def test_analysis_port_has_only_immutable_capture_analysis_capability() -> None:
    assert "analyze_existing_capture" in AnalysisExistingCapturePort.__dict__
    assert "submit_once" not in AnalysisExistingCapturePort.__dict__
