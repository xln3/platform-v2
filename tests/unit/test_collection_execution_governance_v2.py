from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from geo_platform.collection import quota_v2
from geo_platform.collection.execution_governance_v2 import (
    ExecutionGrantIssueRequest,
    GatewayAuthorizationSnapshot,
    acquire_resource_lease,
    authorize_irreversible_action,
    heartbeat_resource_lease,
    issue_execution_grant,
    replace_snapshot,
)
from geo_platform.collection.identity_v2 import (
    CampaignActors,
    CampaignFreezeRequest,
    ConfigFreezeRequest,
    FrozenCampaign,
    QuestionSlotRef,
    activate_frozen_config,
    campaign_membership_digest_at_cursor,
    campaign_slot_at,
    freeze_campaign,
    freeze_config,
)
from geo_platform.collection.owner_authorization_wal_v2 import (
    EncryptedFileSubmissionOwnerAuthorizationWalStore,
)
from geo_platform.collection.resource_owner_gateway_v2 import (
    AuthorizedSubmitOnceGateway,
    DurableSubmissionOwnerAuthorizationLoader,
    ResourceOwnerGatewayError,
    SubmissionOwnerAuthorization,
    SubmissionOwnerAuthorizationWalRecord,
    authorize_submission_owner,
    build_submission_owner_authorization_wal_record,
    prepare_submission_owner_turn,
)
from geo_platform.collection.vault import LocalKms, ProfileVault
from pydantic import ValidationError

from domain.collection.execution_governance import (
    ApiExecutionGrant,
    AppExecutionGrant,
    BindingApproval,
    BindingLifecycleState,
    BindingQuotaScopeRef,
    BindingReadiness,
    BindingResourceRef,
    BindingRevision,
    BindingTargetRef,
    ConsumerAppBinding,
    ConsumerWebBinding,
    ExecutionAction,
    ExecutionCompatibility,
    ExecutionGovernanceError,
    GatewayKind,
    ProviderApiBinding,
    QuotaReservationEffectSnapshot,
    QuotaReservationEffectState,
    QuotaReservationSnapshot,
    QuotaReservationState,
    ReadinessState,
    ResourceCapacityPoolSnapshot,
    ResourceCapacityUnitSnapshot,
    ResourceKind,
    ResourceLeaseAcquireRequest,
    ResourceLeaseHeartbeatDisposition,
    ResourceLeaseHeartbeatRequest,
    ResourceLeaseSnapshot,
    ResourceLeaseState,
    ResourceOwnerSnapshot,
    ResourceOwnerState,
    SecretReferenceMetadata,
    SideEffectAuthorization,
    SubmissionOperationSnapshot,
    WebExecutionGrant,
    assert_governance_payload_safe,
    quota_reservation_effect_set_hash,
    revoke_execution_grant,
    transition_binding_lifecycle,
)
from domain.collection.submission import (
    FreshSubmissionClaim,
    OperationRef,
    OwnerClaimTruth,
    RequestManifest,
    SubmitDisposition,
    SubmitOnceCommand,
    TerminalReason,
    WorkflowOperationInput,
    authority_digest,
    deterministic_dispatch_key,
    request_manifest_digest,
)
from domain.collection.surface import (
    QUOTA_SCOPE_KIND_LOCK_ORDER,
    CapabilityDeclaration,
    CapabilityRegistry,
    CapabilityStatus,
    CollectionConfigV2,
    CollectionSurface,
    CollectionTarget,
    QuotaScopeDeclaration,
    QuotaScopeKind,
    QuotaWindowPolicy,
    QuotaWindowUnit,
    SendState,
)

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
PROJECT_ID = UUID("00000000-0000-0000-0000-000000000002")
QUOTA_REGISTRY_ID = UUID("00000000-0000-0000-0000-000000000003")
READY = ReadinessState.READY


def _replace_model(model: Any, **changes: object) -> Any:
    payload = model.model_dump(mode="python")
    payload.update(changes)
    return type(model).model_validate(payload)


def _target(surface: CollectionSurface) -> CollectionTarget:
    variants = {
        CollectionSurface.PROVIDER_API: ("provider", "responses"),
        CollectionSurface.CONSUMER_WEB: ("doubao", "web-chat"),
        CollectionSurface.CONSUMER_APP: ("doubao", "mobile-app"),
    }
    platform, product_variant = variants[surface]
    return CollectionTarget(
        platform=platform,
        collection_surface=surface,
        product_variant=product_variant,
        interaction_modes=("normal",),
    )


def _active_identity(surface: CollectionSurface) -> tuple[Any, Any, FrozenCampaign]:
    target = _target(surface)
    config = CollectionConfigV2(
        question_set_revision="question-set-v1",
        collection_targets=(target,),
        province_codes=("110000",),
        samples_per_cell=1,
        comparison_policy_revision="comparison-v1",
    )
    registry = CapabilityRegistry(
        registry_revision="capability-registry-v1",
        capabilities=(
            CapabilityDeclaration(
                capability_revision=f"{surface.value}-normal-v1",
                platform=target.platform,
                collection_surface=surface,
                product_variant=target.product_variant,
                interaction_mode="normal",
                status=CapabilityStatus.SUPPORTED,
                production_allowed=True,
            ),
        ),
    )
    frozen = freeze_config(
        ConfigFreezeRequest(
            revision_pub_id=f"config-{surface.value}",
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            revision=1,
            config=config,
            capability_registry=registry,
            change_reason="stage-two-test",
            approved_by_pub_id="reviewer-1",
            frozen_at=NOW - timedelta(hours=1),
        )
    )
    active = activate_frozen_config(
        frozen,
        activated_at=NOW - timedelta(minutes=50),
        readiness_passed=True,
    )
    blueprint = freeze_campaign(
        CampaignFreezeRequest(
            campaign_pub_id=f"campaign-{surface.value}",
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            config_revision=active,
            question_slots=(
                QuestionSlotRef(question_slot_id="question-1", question_revision="qrev-1"),
            ),
            time_window_key="2026-08-24/2026-08-25",
            run_trigger_source="manual",
            trigger_idempotency_key=f"trigger-{surface.value}",
            actors=CampaignActors(
                created_by_pub_id="user-1",
                approved_by_pub_id="reviewer-1",
                triggered_by_pub_id="user-1",
            ),
            binding_policy_revision="binding-policy-v1",
            frozen_at=NOW - timedelta(minutes=40),
        )
    )
    campaign = FrozenCampaign(
        id=blueprint.id,
        campaign_pub_id=blueprint.campaign_pub_id,
        tenant_id=blueprint.tenant_id,
        project_id=blueprint.project_id,
        config_revision_id=blueprint.config_revision_id,
        config_revision_pub_id=blueprint.config_revision_pub_id,
        config_revision_hash=blueprint.config_revision_hash,
        specification_hash=blueprint.specification_hash,
        expected_slot_count=blueprint.expected_slot_count,
        materialized_slot_count=blueprint.expected_slot_count,
        materialization_cursor=blueprint.expected_slot_count,
        membership_hash=campaign_membership_digest_at_cursor(
            blueprint,
            cursor=blueprint.expected_slot_count,
        ),
        frozen_at=blueprint.requested_frozen_at,
    )
    return active, blueprint, campaign


def _binding_payload(surface: CollectionSurface) -> Any:
    if surface is CollectionSurface.PROVIDER_API:
        return ProviderApiBinding(
            provider_gateway_handle="gateway-api",
            provider_tenant_ref="provider-tenant-1",
            provider_account_ref="provider-account-1",
            provider_contract_ref="provider-contract-1",
            credential_slot_ref="credential-slot-1",
            endpoint_catalog_id="provider-responses",
            endpoint_catalog_revision="endpoint-catalog-v1",
            api_version="api-v1",
            entitlement_revision="entitlement-v1",
            credential_rotation_revision="rotation-v1",
            egress_policy_revision="egress-v1",
            credential_state=READY,
            entitlement_state=READY,
            provider_account_state=READY,
        )
    if surface is CollectionSurface.CONSUMER_WEB:
        return ConsumerWebBinding(
            governed_account_ref="account-web-1",
            browser_owner_handle="gateway-web",
            browser_profile_ref="browser-profile-1",
            browser_profile_revision="profile-v1",
            web_session_ref="web-session-1",
            web_session_revision="session-v1",
            approved_host_catalog_id="doubao-web",
            approved_host_catalog_revision="host-catalog-v1",
            relay_policy_revision="relay-v1",
            constraints_revision="constraints-v1",
            login_state=READY,
            captcha_state=READY,
            risk_state=READY,
            human_assist_state=READY,
        )
    return ConsumerAppBinding(
        governed_account_ref="account-app-1",
        device_owner_handle="gateway-app",
        managed_device_ref="managed-device-1",
        app_package_id="com.example.assistant",
        app_build_version="build-v1",
        distribution_channel="managed",
        app_install_ref="app-install-1",
        app_profile_revision="app-profile-v1",
        app_session_ref="app-session-1",
        app_session_revision="app-session-v1",
        automation_agent_revision="agent-v1",
        attestation_policy_revision="attestation-v1",
        relay_policy_revision="relay-v1",
        session_state=READY,
        attestation_state=READY,
        device_health_state=READY,
        human_assist_state=READY,
    )


def _required_resources(surface: CollectionSurface) -> tuple[ResourceKind, ...]:
    if surface is CollectionSurface.PROVIDER_API:
        return (ResourceKind.PROVIDER_TENANT, ResourceKind.CREDENTIAL_SLOT)
    if surface is CollectionSurface.CONSUMER_WEB:
        return (
            ResourceKind.GOVERNED_ACCOUNT,
            ResourceKind.BROWSER_OWNER,
            ResourceKind.BROWSER_PROFILE,
            ResourceKind.WEB_SESSION,
            ResourceKind.RELAY_CAPACITY,
        )
    return (
        ResourceKind.GOVERNED_ACCOUNT,
        ResourceKind.DEVICE_OWNER,
        ResourceKind.APP_INSTALL,
        ResourceKind.APP_SESSION,
        ResourceKind.RELAY_CAPACITY,
    )


def _resource_pub_id(surface: CollectionSurface, kind: ResourceKind) -> str:
    values = {
        CollectionSurface.PROVIDER_API: {
            ResourceKind.PROVIDER_TENANT: "provider-tenant-1",
            ResourceKind.CREDENTIAL_SLOT: "credential-slot-1",
        },
        CollectionSurface.CONSUMER_WEB: {
            ResourceKind.GOVERNED_ACCOUNT: "account-web-1",
            ResourceKind.BROWSER_OWNER: "gateway-web",
            ResourceKind.BROWSER_PROFILE: "browser-profile-1",
            ResourceKind.WEB_SESSION: "web-session-1",
            ResourceKind.RELAY_CAPACITY: "relay-web-1",
        },
        CollectionSurface.CONSUMER_APP: {
            ResourceKind.GOVERNED_ACCOUNT: "account-app-1",
            ResourceKind.DEVICE_OWNER: "managed-device-1",
            ResourceKind.APP_INSTALL: "app-install-1",
            ResourceKind.APP_SESSION: "app-session-1",
            ResourceKind.RELAY_CAPACITY: "relay-app-1",
        },
    }
    return values[surface][kind]


def _resource_mappings(surface: CollectionSurface) -> tuple[BindingResourceRef, ...]:
    surface_offset = tuple(CollectionSurface).index(surface) * 100
    return tuple(
        BindingResourceRef(
            resource_registration_id=UUID(int=1000 + surface_offset + ordinal),
            resource_pub_id=_resource_pub_id(surface, kind),
            resource_kind=kind,
            resource_role=kind.value,
            ordinal=0,
            mapping_revision="resource-mapping-v1",
        )
        for ordinal, kind in enumerate(_required_resources(surface), start=1)
    )


def _binding(surface: CollectionSurface, *, active: bool = True) -> BindingRevision:
    target = _target(surface)
    return BindingRevision(
        binding_pub_id=f"binding-{surface.value}",
        revision=1,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        lifecycle_state=(
            BindingLifecycleState.ACTIVE if active else BindingLifecycleState.CANDIDATE
        ),
        effective_from=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(hours=4),
        activated_at=NOW - timedelta(minutes=30) if active else None,
        lifecycle_reason="approved-for-test" if active else "candidate-review",
        binding_policy_revision="binding-policy-v1",
        target=BindingTargetRef(
            target_key=target.target_key,
            platform=target.platform,
            collection_surface=surface,
            product_variant=target.product_variant,
            interaction_mode="normal",
            capability_revision=f"{surface.value}-normal-v1",
        ),
        quota_policy_revision="quota-policy-v1",
        quota_registry_id=QUOTA_REGISTRY_ID,
        quota_scope_registry_revision="quota-registry-v1",
        quota_scopes=(
            BindingQuotaScopeRef(
                quota_scope_policy_id=UUID(int=202),
                scope_kind=QuotaScopeKind.PROJECT,
                scope_key="quota-scope-project-v1",
                subject_pub_id="quota-project-1",
                policy_revision="quota-policy-v1",
                quota_units=2,
            ),
            BindingQuotaScopeRef(
                quota_scope_policy_id=UUID(int=201),
                scope_kind=QuotaScopeKind.PROVIDER,
                scope_key="quota-scope-provider-v1",
                subject_pub_id="quota-provider-1",
                policy_revision="quota-policy-v1",
            ),
        ),
        required_resource_kinds=_required_resources(surface),
        resource_mappings=_resource_mappings(surface),
        region_policy_revision="region-policy-v1",
        route_policy_revision="route-policy-v1",
        approval=BindingApproval(
            owner_pub_id="owner-1",
            approved_by_pub_id="reviewer-1",
            approval_pub_id="approval-1",
            reason="production-binding",
            approved_at=NOW - timedelta(hours=1),
        ),
        readiness=BindingReadiness(
            assessment_revision="readiness-v1",
            assessed_at=NOW - timedelta(minutes=35),
            resources=READY,
            quota=READY,
            route=READY,
        ),
        secret_references=(
            (
                SecretReferenceMetadata(
                    secret_ref_pub_id="provider-secret-ref-1",
                    secret_version="secret-version-v1",
                    secret_fingerprint_sha256="a" * 64,
                    rotated_at=NOW - timedelta(days=1),
                )
            ),
        )
        if surface is CollectionSurface.PROVIDER_API
        else (),
        payload=_binding_payload(surface),
    )


def _grant_payload(surface: CollectionSurface) -> Any:
    if surface is CollectionSurface.PROVIDER_API:
        return ApiExecutionGrant(
            provider_gateway_handle="gateway-api",
            credential_slot_handle="credential-slot-1",
            provider_endpoint_catalog_id="provider-responses",
            provider_api_version="api-v1",
            provider_tenant_context_ref="provider-tenant-1",
            provider_quota_subject_ref="quota-provider-1",
        )
    if surface is CollectionSurface.CONSUMER_WEB:
        return WebExecutionGrant(
            browser_owner_handle="gateway-web",
            governed_account_ref="account-web-1",
            browser_profile_ref="browser-profile-1",
            browser_profile_revision="profile-v1",
            web_session_ref="web-session-1",
            web_session_revision="session-v1",
            approved_host_catalog_id="doubao-web",
        )
    return AppExecutionGrant(
        device_owner_handle="gateway-app",
        governed_account_ref="account-app-1",
        managed_device_ref="managed-device-1",
        app_package_id="com.example.assistant",
        app_build_version="build-v1",
        distribution_channel="managed",
        app_install_ref="app-install-1",
        app_session_ref="app-session-1",
        app_session_revision="app-session-v1",
        automation_agent_revision="agent-v1",
    )


def _reservation_for_scopes(
    operation_pub_id: str,
    scopes: tuple[BindingQuotaScopeRef, ...],
    binding: BindingRevision,
    *,
    requested_units: int = 2,
) -> QuotaReservationSnapshot:
    # Deliberately reverse persistence input; the envelope canonicalizes to the
    # same order used by quota_v2's effect-set hash.
    effects = tuple(
        QuotaReservationEffectSnapshot(
            effect_id=UUID(int=3000 + index),
            quota_bucket_id=UUID(int=4000 + index),
            quota_scope_policy_id=scope.quota_scope_policy_id,
            scope_key=scope.scope_key,
            bucket_hash=f"{5000 + index:064x}",
            bucket_key=f"bucket-{scope.scope_key}-2026-08-24",
            scope_kind=scope.scope_kind,
            units=scope.quota_units * requested_units,
            state=QuotaReservationEffectState.RESERVED,
        )
        for index, scope in enumerate(reversed(scopes), start=1)
    )
    effect_set_hash = quota_reservation_effect_set_hash(
        tuple(effect.grant_ref for effect in effects),
        requested_units=requested_units,
    )
    return QuotaReservationSnapshot(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        operation_pub_id=operation_pub_id,
        binding_pub_id=binding.binding_pub_id,
        binding_revision=binding.revision,
        binding_hash=binding.binding_hash,
        reservation_id=UUID(int=2999),
        quota_registry_id=binding.quota_registry_id,
        scope_registry_revision=binding.quota_scope_registry_revision,
        requested_units=requested_units,
        expected_effect_count=len(effects),
        effect_set_hash=effect_set_hash,
        state=QuotaReservationState.RESERVED,
        effects=effects,
    )


def _quota_snapshot(operation_pub_id: str, binding: BindingRevision) -> QuotaReservationSnapshot:
    return _reservation_for_scopes(operation_pub_id, binding.quota_scopes, binding)


def _same_subject_window_scopes() -> tuple[BindingQuotaScopeRef, ...]:
    return tuple(
        BindingQuotaScopeRef(
            quota_scope_policy_id=UUID(int=6000 + index),
            scope_kind=QuotaScopeKind.PROJECT,
            scope_key=f"quota-scope-project-{window}-v1",
            subject_pub_id="quota-project-shared",
            policy_revision=f"quota-policy-{window}-v1",
        )
        for index, window in enumerate(("year", "day", "week"), start=1)
    )


def _reservations_for_scopes(
    operation_pub_id: str,
    scopes: tuple[BindingQuotaScopeRef, ...],
    binding: BindingRevision,
) -> QuotaReservationSnapshot:
    return _reservation_for_scopes(operation_pub_id, scopes, binding)


def test_governance_effect_hash_matches_quota_v2_actual_multiscope_plan() -> None:
    window = QuotaWindowPolicy(
        unit=QuotaWindowUnit.DAY,
        timezone="UTC",
        boundary_revision="calendar-v1",
    )
    declarations = (
        QuotaScopeDeclaration(
            policy_revision="quota-policy-z-v1",
            scope_kind=QuotaScopeKind.PROJECT,
            scope_subject_id="project-z",
            limit=100,
            window=window,
        ),
        QuotaScopeDeclaration(
            policy_revision="quota-policy-provider-v1",
            scope_kind=QuotaScopeKind.PROVIDER,
            scope_subject_id="provider-a",
            limit=100,
            window=window,
        ),
        QuotaScopeDeclaration(
            policy_revision="quota-policy-a-v1",
            scope_kind=QuotaScopeKind.PROJECT,
            scope_subject_id="project-a",
            limit=100,
            window=window,
        ),
    )
    buckets = quota_v2.materialize_quota_buckets(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        scopes=declarations,
        occurred_at=NOW,
    )
    requested_units = 3
    policy_by_scope = {
        declaration.scope_key: UUID(int=6100 + index)
        for index, declaration in enumerate(declarations, start=1)
    }
    quota_units_by_scope = {
        declaration.scope_key: index for index, declaration in enumerate(declarations, start=1)
    }
    planned = tuple(
        quota_v2._PlannedBucket(
            bucket=bucket,
            scope_policy_id=policy_by_scope[bucket.scope.scope_key],
            units=quota_units_by_scope[bucket.scope.scope_key] * requested_units,
        )
        for bucket in buckets
    )
    effects = tuple(
        QuotaReservationEffectSnapshot(
            effect_id=UUID(int=6200 + index),
            quota_bucket_id=UUID(int=6300 + index),
            quota_scope_policy_id=policy_by_scope[bucket.scope.scope_key],
            scope_key=bucket.scope.scope_key,
            bucket_hash=bucket.bucket_hash,
            bucket_key=bucket.bucket_key,
            scope_kind=bucket.scope.scope_kind,
            units=quota_units_by_scope[bucket.scope.scope_key] * requested_units,
            state=QuotaReservationEffectState.RESERVED,
        )
        for index, bucket in enumerate(reversed(buckets), start=1)
    )

    actual_quota_v2_hash = quota_v2._planned_effect_set_hash(planned, requested_units)
    governance_hash = quota_reservation_effect_set_hash(
        tuple(effect.grant_ref for effect in effects),
        requested_units=requested_units,
    )

    assert governance_hash == actual_quota_v2_hash


def _owner_handle(surface: CollectionSurface) -> str:
    return {
        CollectionSurface.PROVIDER_API: "gateway-api",
        CollectionSurface.CONSUMER_WEB: "gateway-web",
        CollectionSurface.CONSUMER_APP: "gateway-app",
    }[surface]


def _lease_snapshots(
    surface: CollectionSurface,
    operation_pub_id: str,
    binding: BindingRevision,
) -> tuple[ResourceLeaseSnapshot, ...]:
    main_owner = _owner_handle(surface)
    main_kind = {
        CollectionSurface.PROVIDER_API: ResourceKind.CREDENTIAL_SLOT,
        CollectionSurface.CONSUMER_WEB: ResourceKind.BROWSER_OWNER,
        CollectionSurface.CONSUMER_APP: ResourceKind.DEVICE_OWNER,
    }[surface]
    return tuple(
        ResourceLeaseSnapshot(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            operation_pub_id=operation_pub_id,
            binding_pub_id=binding.binding_pub_id,
            binding_revision=binding.revision,
            binding_hash=binding.binding_hash,
            resource_registration_id=mapping.resource_registration_id,
            capacity_unit_id=UUID(int=7000 + index),
            resource_kind=mapping.resource_kind,
            resource_pub_id=mapping.resource_pub_id,
            resource_role=mapping.resource_role,
            resource_ordinal=mapping.ordinal,
            binding_resource_mapping_revision=mapping.mapping_revision,
            lease_pub_id=f"lease-{mapping.resource_kind.value}",
            owner_gateway_pub_id=(
                main_owner
                if mapping.resource_kind is main_kind
                else (
                    "gateway-relay"
                    if mapping.resource_kind is ResourceKind.RELAY_CAPACITY
                    else main_owner
                )
            ),
            fence_generation=7,
            current_fence_generation=7,
            state=ResourceLeaseState.ACTIVE,
            acquired_at=NOW - timedelta(minutes=10),
            expires_at=NOW + timedelta(hours=2),
        )
        for index, mapping in enumerate(binding.resource_mappings, start=1)
    )


def _issue_request(surface: CollectionSurface) -> ExecutionGrantIssueRequest:
    config, blueprint, campaign = _active_identity(surface)
    slot = campaign_slot_at(blueprint, 0)
    campaign_target = next(
        target for target in blueprint.targets if target.id == slot.campaign_target_id
    )
    sampling_leg = next(leg for leg in blueprint.legs if leg.id == slot.sampling_leg_id)
    operation = SubmissionOperationSnapshot(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        operation_pub_id=f"operation-{surface.value}",
        slot_pub_id=slot.pub_id,
        logical_item_key=slot.slot_key,
        generation=1,
        current_generation=1,
        send_state=SendState.NOT_SENT,
        send_state_version=1,
    )
    binding = _binding(surface)
    return ExecutionGrantIssueRequest(
        grant_pub_id=f"grant-{surface.value}",
        grant_revision=1,
        config_revision=config,
        campaign=campaign,
        campaign_target=campaign_target,
        sampling_leg=sampling_leg,
        slot=slot,
        binding=binding,
        operation=operation,
        quota_reservation=_quota_snapshot(operation.operation_pub_id, binding),
        resource_leases=_lease_snapshots(surface, operation.operation_pub_id, binding),
        payload=_grant_payload(surface),
        compatibility=ExecutionCompatibility(
            workflow_contract_version="collection-workflow-v2",
            adapter_revision="adapter-v2",
            gateway_protocol_revision="owner-gateway-v1",
            worker_build_id="collector-v2",
            agent_revision="agent-v1" if surface is CollectionSurface.CONSUMER_APP else None,
        ),
        issued_by_pub_id="grant-issuer-1",
        issuance_reason="campaign-dispatch",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=30),
    )


def _owner(surface: CollectionSurface) -> ResourceOwnerSnapshot:
    return ResourceOwnerSnapshot(
        owner_gateway_pub_id=_owner_handle(surface),
        gateway_kind={
            CollectionSurface.PROVIDER_API: GatewayKind.PROVIDER_REQUEST,
            CollectionSurface.CONSUMER_WEB: GatewayKind.RESIDENT_BROWSER,
            CollectionSurface.CONSUMER_APP: GatewayKind.MANAGED_APP_SESSION,
        }[surface],
        collection_surface=surface,
        state=ResourceOwnerState.READY,
        protocol_revision="owner-gateway-v1",
    )


def _authorization_snapshot(surface: CollectionSurface) -> GatewayAuthorizationSnapshot:
    request = _issue_request(surface)
    grant = issue_execution_grant(request)
    return GatewayAuthorizationSnapshot(
        checked_at=NOW + timedelta(seconds=1),
        action=ExecutionAction.SUBMIT_QUERY,
        owner=_owner(surface),
        grant=grant,
        binding=request.binding,
        operation=request.operation,
        quota_reservation=request.quota_reservation,
        resource_leases=request.resource_leases,
    )


@pytest.mark.parametrize("surface", tuple(CollectionSurface))
def test_three_typed_subtypes_issue_and_authorize_one_side_effect(
    surface: CollectionSurface,
) -> None:
    snapshot = _authorization_snapshot(surface)
    authorization = authorize_irreversible_action(snapshot)

    assert authorization.operation_pub_id == snapshot.operation.operation_pub_id
    assert authorization.expected_send_state is SendState.NOT_SENT
    assert authorization.expected_send_state_version == snapshot.operation.send_state_version
    assert authorization.required_next_send_state is SendState.SENDING
    assert authorization.owner_gateway_pub_id == _owner_handle(surface)
    assert len(authorization.fence_assertions) == len(_required_resources(surface))
    assert [item.scope_kind for item in snapshot.grant.quota_reservation.effects] == [
        kind
        for kind in QUOTA_SCOPE_KIND_LOCK_ORDER
        if kind in {QuotaScopeKind.PROVIDER, QuotaScopeKind.PROJECT}
    ]


def test_custom_business_resource_role_is_valid_and_primary_mapping_stays_exact() -> None:
    request = _issue_request(CollectionSurface.CONSUMER_WEB)
    browser_mapping = next(
        mapping
        for mapping in request.binding.resource_mappings
        if mapping.resource_kind is ResourceKind.BROWSER_OWNER
    )
    mappings = tuple(
        _replace_model(mapping, resource_role="primary_browser")
        if mapping == browser_mapping
        else mapping
        for mapping in request.binding.resource_mappings
    )
    binding = _replace_model(request.binding, resource_mappings=mappings)
    reservation = _replace_model(
        request.quota_reservation,
        binding_hash=binding.binding_hash,
    )
    leases = tuple(
        _replace_model(
            lease,
            binding_hash=binding.binding_hash,
            resource_role=(
                "primary_browser"
                if lease.resource_registration_id == browser_mapping.resource_registration_id
                else lease.resource_role
            ),
        )
        for lease in request.resource_leases
    )
    adjusted = _replace_model(
        request,
        binding=binding,
        quota_reservation=reservation,
        resource_leases=leases,
    )

    grant = issue_execution_grant(adjusted)
    authorization = authorize_irreversible_action(
        GatewayAuthorizationSnapshot(
            checked_at=NOW + timedelta(seconds=1),
            action=ExecutionAction.SUBMIT_QUERY,
            owner=_owner(CollectionSurface.CONSUMER_WEB),
            grant=grant,
            binding=binding,
            operation=adjusted.operation,
            quota_reservation=reservation,
            resource_leases=leases,
        )
    )

    primary = next(
        fence
        for fence in grant.resource_fences
        if fence.resource_kind is ResourceKind.BROWSER_OWNER
    )
    assert primary.resource_role == "primary_browser"
    assert authorization.owner_gateway_pub_id == "gateway-web"


def test_web_grant_is_rejected_by_app_gateway() -> None:
    snapshot = _authorization_snapshot(CollectionSurface.CONSUMER_WEB)
    wrong_owner = _owner(CollectionSurface.CONSUMER_APP)
    snapshot = replace_snapshot(snapshot, owner=wrong_owner)

    with pytest.raises(ExecutionGovernanceError) as exc_info:
        authorize_irreversible_action(snapshot)
    assert exc_info.value.code == "grant_surface_gateway_mismatch"


def test_grant_model_rejects_surface_subtype_drift() -> None:
    grant = _authorization_snapshot(CollectionSurface.CONSUMER_WEB).grant
    dimensions = _replace_model(
        grant.dimensions,
        collection_surface=CollectionSurface.CONSUMER_APP,
    )
    payload = grant.model_dump(mode="python")
    payload["dimensions"] = dimensions

    with pytest.raises(ValidationError, match="grant_surface_subtype_mismatch"):
        type(grant).model_validate(payload)


def test_issue_rejects_binding_from_another_surface_chain() -> None:
    request = _issue_request(CollectionSurface.CONSUMER_WEB)
    payload = request.model_dump(mode="python")
    payload["binding"] = _binding(CollectionSurface.CONSUMER_APP)
    payload["payload"] = _grant_payload(CollectionSurface.CONSUMER_APP)
    bad_request = ExecutionGrantIssueRequest.model_validate(payload)

    with pytest.raises(ExecutionGovernanceError) as exc_info:
        issue_execution_grant(bad_request)
    assert exc_info.value.code == "binding_campaign_target_mismatch"


def test_binding_activation_requires_complete_web_readiness() -> None:
    candidate = _binding(CollectionSurface.CONSUMER_WEB, active=False)
    web_payload = candidate.payload
    assert isinstance(web_payload, ConsumerWebBinding)
    unavailable_payload = _replace_model(
        web_payload,
        captcha_state=ReadinessState.CHALLENGE_REQUIRED,
    )
    candidate = _replace_model(candidate, payload=unavailable_payload)

    with pytest.raises(ExecutionGovernanceError) as exc_info:
        transition_binding_lifecycle(
            candidate,
            target=BindingLifecycleState.ACTIVE,
            at=NOW,
            reason="readiness-check",
        )
    assert exc_info.value.code == "binding_not_ready"


def test_binding_time_chain_rejects_invalid_approval_readiness_and_activation() -> None:
    binding = _binding(CollectionSurface.CONSUMER_WEB)
    late_approval = _replace_model(
        binding.approval,
        approved_at=binding.effective_from + timedelta(seconds=1),
    )
    with pytest.raises(ValidationError, match="binding_effective_time_precedes_approval"):
        _replace_model(binding, approval=late_approval)

    early_readiness = _replace_model(
        binding.readiness,
        assessed_at=binding.approval.approved_at - timedelta(seconds=1),
    )
    with pytest.raises(ValidationError, match="binding_readiness_precedes_approval"):
        _replace_model(binding, readiness=early_readiness)

    later_readiness = _replace_model(
        binding.readiness,
        assessed_at=binding.activated_at + timedelta(seconds=1),
    )
    with pytest.raises(ValidationError, match="binding_activation_precedes_readiness"):
        _replace_model(binding, readiness=later_readiness)


def test_active_binding_with_future_activation_cannot_issue_grant() -> None:
    request = _issue_request(CollectionSurface.CONSUMER_WEB)
    binding = _replace_model(
        request.binding,
        activated_at=NOW + timedelta(minutes=1),
    )
    reservation = _replace_model(
        request.quota_reservation,
        binding_hash=binding.binding_hash,
    )
    leases = tuple(
        _replace_model(lease, binding_hash=binding.binding_hash)
        for lease in request.resource_leases
    )
    future_activation = _replace_model(
        request,
        binding=binding,
        quota_reservation=reservation,
        resource_leases=leases,
    )

    with pytest.raises(ExecutionGovernanceError) as exc_info:
        issue_execution_grant(future_activation)
    assert exc_info.value.code == "binding_not_activated"


def test_binding_lifecycle_is_versioned_and_terminal_revocation_is_fail_closed() -> None:
    candidate = _binding(CollectionSurface.PROVIDER_API, active=False)
    active = transition_binding_lifecycle(
        candidate,
        target=BindingLifecycleState.ACTIVE,
        at=NOW,
        reason="readiness-passed",
    )
    original_hash = active.binding_hash
    revoked = transition_binding_lifecycle(
        active,
        target=BindingLifecycleState.REVOKED,
        at=NOW + timedelta(minutes=1),
        reason="operator-revocation",
    )
    assert revoked.binding_hash == original_hash
    with pytest.raises(ExecutionGovernanceError) as exc_info:
        transition_binding_lifecycle(
            revoked,
            target=BindingLifecycleState.ACTIVE,
            at=NOW + timedelta(minutes=2),
            reason="illegal-revival",
        )
    assert exc_info.value.code == "binding_lifecycle_transition_invalid"


def test_revoked_binding_blocks_next_side_effect_but_history_grant_remains_readable() -> None:
    snapshot = _authorization_snapshot(CollectionSurface.PROVIDER_API)
    revoked = transition_binding_lifecycle(
        snapshot.binding,
        target=BindingLifecycleState.REVOKED,
        at=snapshot.checked_at,
        reason="security-revocation",
    )
    snapshot = replace_snapshot(snapshot, binding=revoked)

    with pytest.raises(ExecutionGovernanceError) as exc_info:
        authorize_irreversible_action(snapshot)
    assert exc_info.value.code == "binding_not_active"
    assert snapshot.grant.campaign_slot.slot_key


def test_expired_binding_and_revoked_grant_both_block_side_effect() -> None:
    snapshot = _authorization_snapshot(CollectionSurface.CONSUMER_WEB)
    after_expiry = replace_snapshot(
        snapshot,
        checked_at=snapshot.binding.expires_at,
    )
    with pytest.raises(ExecutionGovernanceError) as binding_error:
        authorize_irreversible_action(after_expiry)
    assert binding_error.value.code == "binding_expired"

    revoked_grant = revoke_execution_grant(
        snapshot.grant,
        revoked_at=snapshot.checked_at,
        reason="manual-revocation",
    )
    with pytest.raises(ExecutionGovernanceError) as grant_error:
        authorize_irreversible_action(replace_snapshot(snapshot, grant=revoked_grant))
    assert grant_error.value.code == "grant_revoked"


def test_old_fence_holder_cannot_authorize_even_if_it_retains_handle() -> None:
    snapshot = _authorization_snapshot(CollectionSurface.CONSUMER_WEB)
    browser_index = next(
        index
        for index, lease in enumerate(snapshot.resource_leases)
        if lease.resource_kind is ResourceKind.BROWSER_OWNER
    )
    leases = list(snapshot.resource_leases)
    leases[browser_index] = _replace_model(
        leases[browser_index],
        current_fence_generation=leases[browser_index].fence_generation + 1,
    )

    with pytest.raises(ExecutionGovernanceError) as exc_info:
        authorize_irreversible_action(replace_snapshot(snapshot, resource_leases=tuple(leases)))
    assert exc_info.value.code == "resource_fence_stale"


def test_same_generation_heartbeat_extension_keeps_lease_authorized() -> None:
    snapshot = _authorization_snapshot(CollectionSurface.CONSUMER_WEB)
    renewed = tuple(
        _replace_model(lease, expires_at=lease.expires_at + timedelta(minutes=5))
        for lease in snapshot.resource_leases
    )
    authorization = authorize_irreversible_action(
        replace_snapshot(snapshot, resource_leases=renewed)
    )
    assert authorization.operation_pub_id == snapshot.operation.operation_pub_id


def test_nonreserved_quota_blocks_every_surface_before_side_effect() -> None:
    snapshot = _authorization_snapshot(CollectionSurface.CONSUMER_APP)
    reservation = _replace_model(
        snapshot.quota_reservation,
        state=QuotaReservationState.RELEASED,
    )

    with pytest.raises(ExecutionGovernanceError) as exc_info:
        authorize_irreversible_action(replace_snapshot(snapshot, quota_reservation=reservation))
    assert exc_info.value.code == "quota_reservation_not_reserved"


def test_same_subject_day_week_year_requires_every_exact_scope_and_canonicalizes() -> None:
    request = _issue_request(CollectionSurface.CONSUMER_WEB)
    scopes = _same_subject_window_scopes()
    binding = _replace_model(request.binding, quota_scopes=scopes)
    reservation = _reservations_for_scopes(request.operation.operation_pub_id, scopes, binding)
    leases = tuple(
        _replace_model(
            lease,
            binding_pub_id=binding.binding_pub_id,
            binding_revision=binding.revision,
            binding_hash=binding.binding_hash,
        )
        for lease in request.resource_leases
    )
    complete = ExecutionGrantIssueRequest.model_validate(
        request.model_dump(mode="python")
        | {
            "binding": binding,
            "quota_reservation": reservation,
            "resource_leases": leases,
        }
    )
    grant = issue_execution_grant(complete)

    assert len(binding.quota_subjects) == 1
    assert tuple(item.quota_scope_policy_id for item in grant.quota_reservation.effects) == tuple(
        scope.quota_scope_policy_id for scope in sorted(scopes, key=lambda value: value.order_key)
    )
    for missing_index in range(len(scopes)):
        incomplete_scopes = scopes[:missing_index] + scopes[missing_index + 1 :]
        incomplete = _reservations_for_scopes(
            request.operation.operation_pub_id,
            incomplete_scopes,
            binding,
        )
        with pytest.raises(ExecutionGovernanceError) as exc_info:
            issue_execution_grant(
                ExecutionGrantIssueRequest.model_validate(
                    request.model_dump(mode="python")
                    | {
                        "binding": binding,
                        "quota_reservation": incomplete,
                        "resource_leases": leases,
                    }
                )
            )
        assert exc_info.value.code == "binding_quota_effect_set_mismatch"


def test_binding_scope_policy_accepts_next_day_materialized_window() -> None:
    request = _issue_request(CollectionSurface.CONSUMER_WEB)
    binding = _replace_model(request.binding, expires_at=NOW + timedelta(days=3))
    next_operation_pub_id = "operation-consumer_web-next-day"
    operation = _replace_model(
        request.operation,
        operation_pub_id=next_operation_pub_id,
        generation=2,
        current_generation=2,
    )
    effects = tuple(
        _replace_model(
            effect,
            bucket_hash=f"{8000 + index:064x}",
            bucket_key=f"materialized-window-{index}-2026-08-25",
        )
        for index, effect in enumerate(request.quota_reservation.effects, start=1)
    )
    reservation = _replace_model(
        request.quota_reservation,
        operation_pub_id=next_operation_pub_id,
        binding_hash=binding.binding_hash,
        reservation_id=UUID(int=8100),
        effects=effects,
        effect_set_hash=quota_reservation_effect_set_hash(
            tuple(effect.grant_ref for effect in effects),
            requested_units=request.quota_reservation.requested_units,
        ),
    )
    leases = tuple(
        _replace_model(
            lease,
            operation_pub_id=next_operation_pub_id,
            binding_hash=binding.binding_hash,
            expires_at=NOW + timedelta(days=2),
        )
        for lease in request.resource_leases
    )
    next_day_request = ExecutionGrantIssueRequest.model_validate(
        request.model_dump(mode="python")
        | {
            "binding": binding,
            "operation": operation,
            "quota_reservation": reservation,
            "resource_leases": leases,
            "issued_at": NOW + timedelta(days=1),
            "expires_at": NOW + timedelta(days=1, minutes=30),
        }
    )
    grant = issue_execution_grant(next_day_request)
    assert all("2026-08-25" in item.bucket_key for item in grant.quota_reservation.effects)
    assert grant.binding.required_quota_scopes == binding.quota_scopes


def test_quota_reservation_envelope_rejects_effect_count_and_digest_tampering() -> None:
    reservation = _issue_request(CollectionSurface.PROVIDER_API).quota_reservation
    raw = reservation.model_dump(mode="python")

    with pytest.raises(ValidationError, match="quota_reservation_effect_count_mismatch"):
        QuotaReservationSnapshot.model_validate(
            raw | {"expected_effect_count": reservation.expected_effect_count + 1}
        )
    with pytest.raises(ValidationError, match="quota_reservation_effect_set_hash_mismatch"):
        QuotaReservationSnapshot.model_validate(raw | {"effect_set_hash": "f" * 64})


@pytest.mark.parametrize(
    ("effect_changes", "expected_code"),
    [
        ({"units": 999}, "quota_reservation_effect_units_mismatch"),
        ({"scope_key": "quota-scope-wrong-v1"}, "quota_reservation_effect_scope_key_mismatch"),
    ],
)
def test_issue_rejects_effects_that_do_not_match_authoritative_binding_scope(
    effect_changes: dict[str, object],
    expected_code: str,
) -> None:
    request = _issue_request(CollectionSurface.PROVIDER_API)
    changed_effect = _replace_model(
        request.quota_reservation.effects[0],
        **effect_changes,
    )
    effects = (changed_effect, *request.quota_reservation.effects[1:])
    reservation = _replace_model(
        request.quota_reservation,
        effects=effects,
        effect_set_hash=quota_reservation_effect_set_hash(
            tuple(effect.grant_ref for effect in effects),
            requested_units=request.quota_reservation.requested_units,
        ),
    )

    with pytest.raises(ExecutionGovernanceError) as exc_info:
        issue_execution_grant(_replace_model(request, quota_reservation=reservation))
    assert exc_info.value.code == expected_code


def test_gateway_rejects_terminal_effect_inside_still_reserved_envelope() -> None:
    snapshot = _authorization_snapshot(CollectionSurface.CONSUMER_WEB)
    terminal = _replace_model(
        snapshot.quota_reservation.effects[0],
        state=QuotaReservationEffectState.SETTLED_CONSUMED,
    )
    reservation = _replace_model(
        snapshot.quota_reservation,
        effects=(terminal, *snapshot.quota_reservation.effects[1:]),
    )

    with pytest.raises(ExecutionGovernanceError) as exc_info:
        authorize_irreversible_action(replace_snapshot(snapshot, quota_reservation=reservation))
    assert exc_info.value.code == "quota_reservation_effect_not_reserved"


def test_extra_or_wrong_policy_quota_scope_is_rejected() -> None:
    request = _issue_request(CollectionSurface.PROVIDER_API)
    extra = QuotaReservationEffectSnapshot(
        effect_id=UUID(int=8201),
        quota_bucket_id=UUID(int=8202),
        quota_scope_policy_id=UUID(int=8203),
        scope_key="quota-scope-extra-mode-v1",
        bucket_hash="c" * 64,
        bucket_key="bucket-extra-mode-v1",
        scope_kind=QuotaScopeKind.MODE,
        units=request.quota_reservation.requested_units,
        state=QuotaReservationEffectState.RESERVED,
    )
    extra_effects = request.quota_reservation.effects + (extra,)
    extra_reservation = _replace_model(
        request.quota_reservation,
        expected_effect_count=len(extra_effects),
        effects=extra_effects,
        effect_set_hash=quota_reservation_effect_set_hash(
            tuple(effect.grant_ref for effect in extra_effects),
            requested_units=request.quota_reservation.requested_units,
        ),
    )
    with pytest.raises(ExecutionGovernanceError) as extra_error:
        issue_execution_grant(
            ExecutionGrantIssueRequest.model_validate(
                request.model_dump(mode="python") | {"quota_reservation": extra_reservation}
            )
        )
    assert extra_error.value.code == "binding_quota_effect_set_mismatch"

    wrong_effect = _replace_model(
        request.quota_reservation.effects[0],
        quota_scope_policy_id=UUID(int=8299),
    )
    wrong_effects = (wrong_effect, *request.quota_reservation.effects[1:])
    wrong_reservation = _replace_model(
        request.quota_reservation,
        effects=wrong_effects,
        effect_set_hash=quota_reservation_effect_set_hash(
            tuple(effect.grant_ref for effect in wrong_effects),
            requested_units=request.quota_reservation.requested_units,
        ),
    )
    with pytest.raises(ExecutionGovernanceError) as policy_error:
        issue_execution_grant(
            ExecutionGrantIssueRequest.model_validate(
                request.model_dump(mode="python")
                | {
                    "quota_reservation": wrong_reservation,
                }
            )
        )
    assert policy_error.value.code == "binding_quota_effect_set_mismatch"


def test_issue_rejects_lease_or_reservation_acquired_for_old_binding() -> None:
    request = _issue_request(CollectionSurface.PROVIDER_API)
    old_quota = _replace_model(
        request.quota_reservation,
        binding_pub_id="binding-provider_api-old",
    )
    with pytest.raises(ExecutionGovernanceError) as quota_error:
        issue_execution_grant(
            ExecutionGrantIssueRequest.model_validate(
                request.model_dump(mode="python")
                | {
                    "quota_reservation": old_quota,
                }
            )
        )
    assert quota_error.value.code == "quota_reservation_binding_mismatch"

    old_lease = _replace_model(
        request.resource_leases[0],
        binding_revision=request.binding.revision + 1,
    )
    with pytest.raises(ExecutionGovernanceError) as lease_error:
        issue_execution_grant(
            ExecutionGrantIssueRequest.model_validate(
                request.model_dump(mode="python")
                | {"resource_leases": (old_lease, *request.resource_leases[1:])}
            )
        )
    assert lease_error.value.code == "resource_lease_binding_mismatch"


def test_issue_and_gateway_reject_wrong_bound_resource_identity() -> None:
    request = _issue_request(CollectionSurface.CONSUMER_WEB)
    wrong_lease = _replace_model(
        request.resource_leases[0],
        resource_pub_id="wrong-bound-resource",
    )
    with pytest.raises(ExecutionGovernanceError) as issue_error:
        issue_execution_grant(
            _replace_model(
                request,
                resource_leases=(wrong_lease, *request.resource_leases[1:]),
            )
        )
    assert issue_error.value.code == "resource_lease_binding_mapping_mismatch"

    snapshot = _authorization_snapshot(CollectionSurface.CONSUMER_WEB)
    current_wrong = _replace_model(
        snapshot.resource_leases[0],
        resource_pub_id="wrong-current-resource",
    )
    with pytest.raises(ExecutionGovernanceError) as gateway_error:
        authorize_irreversible_action(
            replace_snapshot(
                snapshot,
                resource_leases=(current_wrong, *snapshot.resource_leases[1:]),
            )
        )
    assert gateway_error.value.code == "gateway_resource_fence_drift"


def test_issue_and_gateway_reject_lease_acquired_after_check_time() -> None:
    request = _issue_request(CollectionSurface.CONSUMER_APP)
    future_lease = _replace_model(
        request.resource_leases[0],
        acquired_at=request.issued_at + timedelta(seconds=1),
    )
    with pytest.raises(ExecutionGovernanceError) as issue_error:
        issue_execution_grant(
            _replace_model(
                request,
                resource_leases=(future_lease, *request.resource_leases[1:]),
            )
        )
    assert issue_error.value.code == "resource_lease_acquired_in_future"

    snapshot = _authorization_snapshot(CollectionSurface.CONSUMER_APP)
    future_current = _replace_model(
        snapshot.resource_leases[0],
        acquired_at=snapshot.checked_at + timedelta(seconds=1),
    )
    with pytest.raises(ExecutionGovernanceError) as gateway_error:
        authorize_irreversible_action(
            replace_snapshot(
                snapshot,
                resource_leases=(future_current, *snapshot.resource_leases[1:]),
            )
        )
    assert gateway_error.value.code == "resource_lease_acquired_in_future"


def test_gateway_requires_current_quota_set_to_equal_frozen_binding_scopes() -> None:
    snapshot = _authorization_snapshot(CollectionSurface.CONSUMER_APP)
    missing_effects = snapshot.quota_reservation.effects[:-1]
    missing = _replace_model(
        snapshot.quota_reservation,
        expected_effect_count=len(missing_effects),
        effects=missing_effects,
        effect_set_hash=quota_reservation_effect_set_hash(
            tuple(effect.grant_ref for effect in missing_effects),
            requested_units=snapshot.quota_reservation.requested_units,
        ),
    )
    with pytest.raises(ExecutionGovernanceError) as missing_error:
        authorize_irreversible_action(replace_snapshot(snapshot, quota_reservation=missing))
    assert missing_error.value.code == "gateway_quota_reservation_drift"

    current_effect = _replace_model(
        snapshot.quota_reservation.effects[0],
        bucket_hash="d" * 64,
        bucket_key="bucket-current-drift",
    )
    drift_effects = (current_effect, *snapshot.quota_reservation.effects[1:])
    drift = _replace_model(
        snapshot.quota_reservation,
        effects=drift_effects,
        effect_set_hash=quota_reservation_effect_set_hash(
            tuple(effect.grant_ref for effect in drift_effects),
            requested_units=snapshot.quota_reservation.requested_units,
        ),
    )
    with pytest.raises(ExecutionGovernanceError) as extra_error:
        authorize_irreversible_action(replace_snapshot(snapshot, quota_reservation=drift))
    assert extra_error.value.code == "gateway_quota_reservation_drift"


def test_gateway_rejects_current_lease_or_quota_from_old_binding() -> None:
    snapshot = _authorization_snapshot(CollectionSurface.CONSUMER_APP)
    old_quota = _replace_model(
        snapshot.quota_reservation,
        binding_hash="b" * 64,
    )
    with pytest.raises(ExecutionGovernanceError) as quota_error:
        authorize_irreversible_action(replace_snapshot(snapshot, quota_reservation=old_quota))
    assert quota_error.value.code == "gateway_quota_binding_mismatch"

    old_lease = _replace_model(
        snapshot.resource_leases[0],
        binding_pub_id="binding-consumer_app-old",
    )
    with pytest.raises(ExecutionGovernanceError) as lease_error:
        authorize_irreversible_action(
            replace_snapshot(
                snapshot,
                resource_leases=(old_lease, *snapshot.resource_leases[1:]),
            )
        )
    assert lease_error.value.code == "gateway_resource_binding_mismatch"


@pytest.mark.parametrize(
    ("send_state", "current_generation", "expected_code"),
    [
        (SendState.SENDING, 1, "operation_not_sendable"),
        (SendState.SEND_UNKNOWN, 1, "operation_not_sendable"),
        (SendState.NOT_SENT, 2, "operation_generation_stale"),
    ],
)
def test_old_or_already_sending_operation_cannot_be_authorized_again(
    send_state: SendState,
    current_generation: int,
    expected_code: str,
) -> None:
    snapshot = _authorization_snapshot(CollectionSurface.PROVIDER_API)
    operation = _replace_model(
        snapshot.operation,
        send_state=send_state,
        current_generation=current_generation,
    )
    with pytest.raises(ExecutionGovernanceError) as exc_info:
        authorize_irreversible_action(replace_snapshot(snapshot, operation=operation))
    assert exc_info.value.code == expected_code


def test_authorization_is_non_bearer_assertion_and_fresh_state_must_be_cas_persisted() -> None:
    snapshot = _authorization_snapshot(CollectionSurface.PROVIDER_API)
    authorization = authorize_irreversible_action(snapshot)

    assert authorization.expected_send_state is SendState.NOT_SENT
    assert authorization.expected_send_state_version == snapshot.operation.send_state_version
    assert authorization.required_next_send_state is SendState.SENDING
    assert "credential_slot_handle" not in authorization.model_dump(mode="json")

    persisted_sending = _replace_model(snapshot.operation, send_state=SendState.SENDING)
    with pytest.raises(ExecutionGovernanceError) as exc_info:
        authorize_irreversible_action(replace_snapshot(snapshot, operation=persisted_sending))
    assert exc_info.value.code == "operation_not_sendable"


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        ({"api_key": "sk-live-value"}, "governance_payload_forbidden_field"),
        ({"note": "https://127.0.0.1:9222/devtools"}, "governance_payload_bare_connection"),
        ({"note": "127.0.0.1:9222/devtools"}, "governance_payload_bare_endpoint"),
        ({"note": "localhost:5555"}, "governance_payload_bare_endpoint"),
        ({"note": "selenium:4444/wd/hub"}, "governance_payload_bare_endpoint"),
        ({"note": "selenium_grid:4444"}, "governance_payload_bare_endpoint"),
        ({"note": "localhost :9222"}, "governance_payload_bare_endpoint"),
        ({"note": "10.0.0.3"}, "governance_payload_bare_endpoint"),
        ({"note": "[::1]:9222"}, "governance_payload_bare_endpoint"),
        ({"note": "::1"}, "governance_payload_bare_endpoint"),
        ({"note": "2001:db8::1"}, "governance_payload_bare_endpoint"),
        ({"device_serial": "emulator-5554"}, "governance_payload_forbidden_field"),
        ({"note": "490154203237518"}, "governance_payload_hardware_id"),
        ({"cookie": "sid=reusable"}, "governance_payload_forbidden_field"),
    ],
)
def test_dlp_rejects_secret_bare_url_and_reusable_hardware_identity(
    payload: dict[str, str], expected_code: str
) -> None:
    with pytest.raises(ExecutionGovernanceError) as exc_info:
        assert_governance_payload_safe(payload)
    assert exc_info.value.code == expected_code


@pytest.mark.parametrize(
    "endpoint_shaped_handle",
    ("selenium_grid:4444", "localhost:9222", "2001:db8::1"),
)
def test_complete_grant_issue_rejects_endpoint_shaped_opaque_owner_handle(
    endpoint_shaped_handle: str,
) -> None:
    request = _issue_request(CollectionSurface.CONSUMER_WEB)
    binding_payload = request.binding.payload
    grant_payload = request.payload
    assert isinstance(binding_payload, ConsumerWebBinding)
    assert isinstance(grant_payload, WebExecutionGrant)
    # Simulate a persistence hydrator that bypassed Pydantic construction; the
    # issue boundary must still inspect the complete aggregate and fail closed.
    binding = request.binding.model_copy(
        update={
            "payload": binding_payload.model_copy(
                update={"browser_owner_handle": endpoint_shaped_handle}
            )
        }
    )
    reservation = request.quota_reservation.model_copy(
        update={"binding_hash": binding.binding_hash}
    )
    leases = tuple(
        lease.model_copy(
            update={
                "binding_hash": binding.binding_hash,
                "owner_gateway_pub_id": (
                    endpoint_shaped_handle
                    if lease.resource_kind is ResourceKind.BROWSER_OWNER
                    else lease.owner_gateway_pub_id
                ),
            }
        )
        for lease in request.resource_leases
    )
    unsafe_request = request.model_copy(
        update={
            "binding": binding,
            "quota_reservation": reservation,
            "resource_leases": leases,
            "payload": grant_payload.model_copy(
                update={"browser_owner_handle": endpoint_shaped_handle}
            ),
        }
    )

    with pytest.raises(ExecutionGovernanceError) as exc_info:
        issue_execution_grant(unsafe_request)
    assert exc_info.value.code == "governance_payload_bare_endpoint"


@pytest.mark.parametrize(
    "opaque_value",
    (
        "binding:revision-v1",
        "quota_scope:daily-v1",
        "urn:binding:revision:v1",
        "2026-08-24T12:00:00+00:00",
    ),
)
def test_dlp_allows_normal_non_endpoint_opaque_identifiers(opaque_value: str) -> None:
    assert_governance_payload_safe({"opaque_ref": opaque_value})


def test_strict_subtype_forbids_secret_or_bare_endpoint_fields() -> None:
    safe = _grant_payload(CollectionSurface.PROVIDER_API)
    raw = safe.model_dump(mode="python") | {
        "api_key": "sk-live-value",
        "endpoint_url": "https://provider.example/v1",
    }
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ApiExecutionGrant.model_validate(raw)


def test_crowd_assistant_apk_cannot_be_declared_as_app_collector() -> None:
    payload = _grant_payload(CollectionSurface.CONSUMER_APP).model_dump(mode="python")
    payload["app_package_id"] = "com.geosys.crowdassistant"
    with pytest.raises(ValidationError, match="crowd_assistant_apk_is_not_app_collector"):
        AppExecutionGrant.model_validate(payload)


def test_lost_owner_heartbeat_blocks_next_side_effect() -> None:
    snapshot = _authorization_snapshot(CollectionSurface.CONSUMER_WEB)
    lost_owner = _replace_model(snapshot.owner, state=ResourceOwnerState.LOST)
    with pytest.raises(ExecutionGovernanceError) as exc_info:
        authorize_irreversible_action(replace_snapshot(snapshot, owner=lost_owner))
    assert exc_info.value.code == "resource_owner_not_ready"


def test_issued_payload_is_safe_and_uses_catalog_ids_not_connection_addresses() -> None:
    for surface in CollectionSurface:
        snapshot = _authorization_snapshot(surface)
        dumped = snapshot.grant.model_dump(mode="json")
        assert_governance_payload_safe(dumped)
        encoded = json.dumps(dumped, sort_keys=True)
        assert "://" not in encoded
        assert "api_key" not in encoded
        assert "cookie" not in encoded.lower()
        assert "device_serial" not in encoded


def test_issue_rejects_stale_fence_nonreserved_quota_and_old_operation() -> None:
    request = _issue_request(CollectionSurface.CONSUMER_APP)

    leases = list(request.resource_leases)
    leases[0] = _replace_model(leases[0], current_fence_generation=leases[0].fence_generation + 1)
    with pytest.raises(ExecutionGovernanceError) as stale_fence:
        issue_execution_grant(
            ExecutionGrantIssueRequest.model_validate(
                request.model_dump(mode="python") | {"resource_leases": tuple(leases)}
            )
        )
    assert stale_fence.value.code == "resource_fence_stale"

    quota = _replace_model(
        request.quota_reservation,
        state=QuotaReservationState.SETTLED_CONSUMED,
    )
    with pytest.raises(ExecutionGovernanceError) as settled_quota:
        issue_execution_grant(
            ExecutionGrantIssueRequest.model_validate(
                request.model_dump(mode="python") | {"quota_reservation": quota}
            )
        )
    assert settled_quota.value.code == "quota_reservation_not_reserved"

    old_operation = _replace_model(request.operation, current_generation=2)
    with pytest.raises(ExecutionGovernanceError) as old_op:
        issue_execution_grant(
            ExecutionGrantIssueRequest.model_validate(
                request.model_dump(mode="python") | {"operation": old_operation}
            )
        )
    assert old_op.value.code == "operation_generation_stale"


def _capacity_pool(
    resource_pub_id: str,
    *,
    capacity: int = 1,
) -> ResourceCapacityPoolSnapshot:
    registration_id = UUID(int=8000 + sum(resource_pub_id.encode("utf-8")))
    return ResourceCapacityPoolSnapshot(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        resource_registration_id=registration_id,
        resource_kind=ResourceKind.CREDENTIAL_SLOT,
        resource_pub_id=resource_pub_id,
        owner_gateway_pub_id="gateway-api",
        units=tuple(
            ResourceCapacityUnitSnapshot(
                capacity_unit_id=UUID(int=9000 + registration_id.int + ordinal),
                capacity_unit=ordinal,
                current_fence_generation=0,
            )
            for ordinal in range(1, capacity + 1)
        ),
    )


def _lease_request(
    *,
    operation_pub_id: str,
    lease_pub_id: str,
    acquired_at: datetime = NOW,
    ttl: timedelta = timedelta(minutes=5),
    resource_pub_id: str = "credential-capacity-a",
) -> ResourceLeaseAcquireRequest:
    binding = _binding(CollectionSurface.PROVIDER_API)
    mapping = BindingResourceRef(
        resource_registration_id=UUID(int=8000 + sum(resource_pub_id.encode("utf-8"))),
        resource_pub_id=resource_pub_id,
        resource_kind=ResourceKind.CREDENTIAL_SLOT,
        resource_role=ResourceKind.CREDENTIAL_SLOT.value,
        ordinal=0,
        mapping_revision="resource-mapping-v1",
    )
    return ResourceLeaseAcquireRequest(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        operation_pub_id=operation_pub_id,
        binding_pub_id=binding.binding_pub_id,
        binding_revision=binding.revision,
        binding_hash=binding.binding_hash,
        binding_resource=mapping,
        lease_pub_id=lease_pub_id,
        acquired_at=acquired_at,
        expires_at=acquired_at + ttl,
    )


def test_assembling_campaign_cannot_issue_execution_grant() -> None:
    request = _issue_request(CollectionSurface.PROVIDER_API)
    _, blueprint, _ = _active_identity(CollectionSurface.PROVIDER_API)

    with pytest.raises(ValidationError):
        ExecutionGrantIssueRequest.model_validate(
            request.model_dump(mode="python") | {"campaign": blueprint}
        )


def test_resource_lease_acquire_is_idempotent_and_generation_is_monotonic() -> None:
    initial = _capacity_pool("credential-capacity-a")
    first_request = _lease_request(
        operation_pub_id="operation-capacity-1",
        lease_pub_id="lease-capacity-1",
        ttl=timedelta(minutes=1),
    )
    first = acquire_resource_lease(initial, first_request)
    assert first.lease.fence_generation == 1
    assert first.pool.units[0].current_fence_generation == 1

    replay = acquire_resource_lease(first.pool, first_request)
    assert replay.replayed is True
    assert replay.pool == first.pool
    assert replay.lease == first.lease

    takeover_at = NOW + timedelta(minutes=2)
    takeover = acquire_resource_lease(
        first.pool,
        _lease_request(
            operation_pub_id="operation-capacity-2",
            lease_pub_id="lease-capacity-2",
            acquired_at=takeover_at,
        ),
    )
    assert takeover.lease.fence_generation == 2
    assert takeover.pool.units[0].current_fence_generation == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operation_pub_id", "operation-capacity-other"),
        ("binding_pub_id", "binding-provider-api-other"),
        ("binding_revision", 2),
        ("binding_hash", "b" * 64),
        ("acquired_at", NOW + timedelta(seconds=1)),
        ("expires_at", NOW + timedelta(minutes=6)),
    ],
)
def test_resource_lease_replay_rejects_every_immutable_request_drift(
    field: str,
    value: object,
) -> None:
    request = _lease_request(
        operation_pub_id="operation-capacity-idempotent",
        lease_pub_id="lease-capacity-idempotent",
        resource_pub_id="credential-capacity-idempotent",
    )
    acquired = acquire_resource_lease(_capacity_pool("credential-capacity-idempotent"), request)
    changed = _replace_model(request, **{field: value})

    with pytest.raises(ExecutionGovernanceError) as conflict:
        acquire_resource_lease(acquired.pool, changed)
    assert conflict.value.code == "resource_lease_idempotency_conflict"


@pytest.mark.parametrize(
    ("mapping_field", "value"),
    [
        ("resource_role", "primary_credential"),
        ("ordinal", 1),
        ("mapping_revision", "resource-mapping-v2"),
    ],
)
def test_resource_lease_replay_rejects_binding_mapping_identity_drift(
    mapping_field: str,
    value: object,
) -> None:
    request = _lease_request(
        operation_pub_id="operation-capacity-mapping-replay",
        lease_pub_id="lease-capacity-mapping-replay",
        resource_pub_id="credential-capacity-mapping-replay",
    )
    acquired = acquire_resource_lease(_capacity_pool("credential-capacity-mapping-replay"), request)
    changed_mapping = _replace_model(
        request.binding_resource,
        **{mapping_field: value},
    )

    with pytest.raises(ExecutionGovernanceError) as conflict:
        acquire_resource_lease(
            acquired.pool,
            _replace_model(request, binding_resource=changed_mapping),
        )
    assert conflict.value.code == "resource_lease_idempotency_conflict"


def test_resource_lease_acquire_rejects_cross_scope_request() -> None:
    request = _lease_request(
        operation_pub_id="operation-capacity-scope",
        lease_pub_id="lease-capacity-scope",
        resource_pub_id="credential-capacity-scope",
    )
    changed = _replace_model(
        request,
        tenant_id=UUID("00000000-0000-0000-0000-000000000099"),
    )

    with pytest.raises(ExecutionGovernanceError) as mismatch:
        acquire_resource_lease(_capacity_pool("credential-capacity-scope"), changed)
    assert mismatch.value.code == "resource_acquire_scope_mismatch"


def test_stale_resource_heartbeat_is_noop_and_current_generation_can_extend() -> None:
    first = acquire_resource_lease(
        _capacity_pool("credential-capacity-heartbeat"),
        _lease_request(
            operation_pub_id="operation-heartbeat-1",
            lease_pub_id="lease-heartbeat-1",
            ttl=timedelta(minutes=1),
            resource_pub_id="credential-capacity-heartbeat",
        ),
    )
    takeover_at = NOW + timedelta(minutes=2)
    takeover = acquire_resource_lease(
        first.pool,
        _lease_request(
            operation_pub_id="operation-heartbeat-2",
            lease_pub_id="lease-heartbeat-2",
            acquired_at=takeover_at,
            resource_pub_id="credential-capacity-heartbeat",
        ),
    )

    stale = heartbeat_resource_lease(
        takeover.pool,
        ResourceLeaseHeartbeatRequest(
            lease_pub_id=first.lease.lease_pub_id,
            fence_generation=first.lease.fence_generation,
            heartbeat_at=takeover_at + timedelta(seconds=1),
            extend_expires_at=takeover_at + timedelta(minutes=10),
        ),
    )
    assert stale.applied is False
    assert stale.disposition is ResourceLeaseHeartbeatDisposition.STALE_LEASE
    assert stale.pool == takeover.pool

    wrong_generation = heartbeat_resource_lease(
        takeover.pool,
        ResourceLeaseHeartbeatRequest(
            lease_pub_id=takeover.lease.lease_pub_id,
            fence_generation=takeover.lease.fence_generation + 1,
            heartbeat_at=takeover_at + timedelta(seconds=1),
            extend_expires_at=takeover_at + timedelta(minutes=10),
        ),
    )
    assert wrong_generation.applied is False
    assert wrong_generation.disposition is ResourceLeaseHeartbeatDisposition.STALE_GENERATION
    assert wrong_generation.pool == takeover.pool

    extended_expiry = takeover_at + timedelta(minutes=10)
    current = heartbeat_resource_lease(
        takeover.pool,
        ResourceLeaseHeartbeatRequest(
            lease_pub_id=takeover.lease.lease_pub_id,
            fence_generation=takeover.lease.fence_generation,
            heartbeat_at=takeover_at + timedelta(seconds=1),
            extend_expires_at=extended_expiry,
        ),
    )
    assert current.applied is True
    assert current.lease is not None
    assert current.lease.expires_at == extended_expiry
    assert current.lease.fence_generation == takeover.lease.fence_generation


def test_resource_capacity_is_per_resource_not_region_global_mutex() -> None:
    first_pool = _capacity_pool("credential-capacity-a")
    second_pool = _capacity_pool("credential-capacity-b")
    first = acquire_resource_lease(
        first_pool,
        _lease_request(
            operation_pub_id="operation-resource-a",
            lease_pub_id="lease-resource-a",
        ),
    )
    second = acquire_resource_lease(
        second_pool,
        _lease_request(
            operation_pub_id="operation-resource-b",
            lease_pub_id="lease-resource-b",
            resource_pub_id="credential-capacity-b",
        ),
    )
    assert first.lease.resource_pub_id != second.lease.resource_pub_id
    assert first.lease.fence_generation == second.lease.fence_generation == 1

    with pytest.raises(ExecutionGovernanceError) as exhausted:
        acquire_resource_lease(
            first.pool,
            _lease_request(
                operation_pub_id="operation-resource-a-contender",
                lease_pub_id="lease-resource-a-contender",
            ),
        )
    assert exhausted.value.code == "resource_capacity_exhausted"

    shared = _capacity_pool("credential-capacity-shared", capacity=2)
    one = acquire_resource_lease(
        shared,
        _lease_request(
            operation_pub_id="operation-shared-1",
            lease_pub_id="lease-shared-1",
            resource_pub_id="credential-capacity-shared",
        ),
    )
    two = acquire_resource_lease(
        one.pool,
        _lease_request(
            operation_pub_id="operation-shared-2",
            lease_pub_id="lease-shared-2",
            resource_pub_id="credential-capacity-shared",
        ),
    )
    assert {one.lease.capacity_unit, two.lease.capacity_unit} == {1, 2}
    with pytest.raises(ExecutionGovernanceError) as shared_exhausted:
        acquire_resource_lease(
            two.pool,
            _lease_request(
                operation_pub_id="operation-shared-3",
                lease_pub_id="lease-shared-3",
                resource_pub_id="credential-capacity-shared",
            ),
        )
    assert shared_exhausted.value.code == "resource_capacity_exhausted"


def _submission_workflow(
    snapshot: GatewayAuthorizationSnapshot,
) -> tuple[WorkflowOperationInput, RequestManifest]:
    manifest = RequestManifest(
        request_protocol_version="owner-gateway-test-v1",
        request_schema_revision="request-schema-v1",
        request_payload_ref=f"payload-{snapshot.owner.collection_surface.value}",
        request_payload_sha256="1" * 64,
    )
    operation = OperationRef(
        operation_pub_id=snapshot.operation.operation_pub_id,
        operation_key=f"operation-key-{snapshot.owner.collection_surface.value}",
        generation=snapshot.operation.generation,
        request_manifest_sha256=request_manifest_digest(manifest),
        provider_idempotency_key=(f"provider-key-{snapshot.owner.collection_surface.value}"),
    )
    return (
        WorkflowOperationInput(
            operation=operation,
            expected_state_version=snapshot.operation.send_state_version,
        ),
        manifest,
    )


def _submit_command(
    authorization: SubmissionOwnerAuthorization,
    manifest: RequestManifest,
    *,
    owner_dispatch_ref: str | None = None,
    owner_wal_evidence_sha256: str = "2" * 64,
) -> SubmitOnceCommand:
    authority = authorization.authority
    operation = authorization.workflow.operation
    claim = OwnerClaimTruth(
        claim_pub_id=f"claim-{authorization.collection_surface.value}",
        owner_handle=authority.owner_handle,
        grant_pub_id=authority.grant_pub_id,
        grant_revision=authority.grant_revision,
        authority_sha256=authority_digest(authority),
        fence_set_sha256=authority.fence_set_sha256,
        dispatch_key=deterministic_dispatch_key(operation),
        owner_dispatch_ref=(
            owner_dispatch_ref or f"owner-dispatch-{authorization.collection_surface.value}"
        ),
        owner_wal_evidence_sha256=owner_wal_evidence_sha256,
        claimed_at=authority.checked_at + timedelta(seconds=1),
    )
    return SubmitOnceCommand(
        fresh_claim=FreshSubmissionClaim(
            operation=operation,
            claim=claim,
            claimed_state_version=authorization.workflow.expected_state_version + 1,
        ),
        request_manifest=manifest,
        request_manifest_sha256=operation.request_manifest_sha256,
        provider_idempotency_key=operation.provider_idempotency_key,
    )


class _AuthorizationLoader:
    def __init__(self, authorization: SubmissionOwnerAuthorization) -> None:
        self.authorization = authorization
        self.calls = 0

    def load(self, command: SubmitOnceCommand) -> SubmissionOwnerAuthorization:
        assert command.fresh_claim.operation == self.authorization.workflow.operation
        self.calls += 1
        return self.authorization


class _AuthorizationWalReader:
    def __init__(self, record: SubmissionOwnerAuthorizationWalRecord | None) -> None:
        self.record = record
        self.calls: list[str] = []

    def load(
        self,
        *,
        owner_dispatch_ref: str,
    ) -> SubmissionOwnerAuthorizationWalRecord | None:
        self.calls.append(owner_dispatch_ref)
        return self.record


class _AuthorizationWalStore(_AuthorizationWalReader):
    def __init__(
        self,
        record: SubmissionOwnerAuthorizationWalRecord | None = None,
        *,
        conflict: SubmissionOwnerAuthorizationWalRecord | None = None,
    ) -> None:
        super().__init__(record)
        self.conflict = conflict
        self.put_calls: list[SubmissionOwnerAuthorizationWalRecord] = []

    def put(
        self,
        record: SubmissionOwnerAuthorizationWalRecord,
    ) -> SubmissionOwnerAuthorizationWalRecord:
        self.put_calls.append(record)
        if self.conflict is not None:
            return self.conflict
        if self.record is not None and self.record != record:
            return self.record
        self.record = record
        return record


class _OwnerTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[SubmitOnceCommand, object]] = []

    def submit_once(
        self,
        command: SubmitOnceCommand,
        *,
        authorization: SideEffectAuthorization,
    ) -> SubmitDisposition:
        self.calls.append((command, authorization))
        return SubmitDisposition(
            send_state=SendState.CONFIRMED_SENT,
            reason=TerminalReason.SUBMITTED,
            boundary_entered=True,
            evidence_ref=f"submit-evidence-{len(self.calls)}",
            evidence_sha256="3" * 64,
            resolved_at=command.fresh_claim.claim.claimed_at + timedelta(seconds=1),
            provider_submission_ref=f"provider-submission-{len(self.calls)}",
        )


@pytest.mark.parametrize("surface", tuple(CollectionSurface))
def test_three_surface_owner_gateway_bridges_governance_to_one_submit(
    surface: CollectionSurface,
) -> None:
    snapshot = _authorization_snapshot(surface)
    workflow, manifest = _submission_workflow(snapshot)
    authorization = authorize_submission_owner(snapshot, workflow)
    command = _submit_command(authorization, manifest)
    loader = _AuthorizationLoader(authorization)
    transport = _OwnerTransport()
    gateway = AuthorizedSubmitOnceGateway(
        collection_surface=surface,
        gateway_kind=snapshot.owner.gateway_kind,
        owner_gateway_pub_id=snapshot.owner.owner_gateway_pub_id,
        owner_protocol_revision=snapshot.owner.protocol_revision,
        authorization_loader=loader,
        transport=transport,
        clock=lambda: command.fresh_claim.claim.claimed_at + timedelta(milliseconds=1),
    )

    result = gateway.submit_once(command)

    assert result.send_state is SendState.CONFIRMED_SENT
    assert_governance_payload_safe(authorization)
    assert loader.calls == 1
    assert len(transport.calls) == 1
    assert transport.calls[0][1] == authorization.assertion
    assert authorization.tenant_id == TENANT_ID
    assert authorization.project_id == PROJECT_ID
    assert authorization.assertion.expected_send_state_version + 1 == (
        command.fresh_claim.claimed_state_version
    )
    assert authorization.authority.fence_set_sha256 == (command.fresh_claim.claim.fence_set_sha256)


@pytest.mark.parametrize("surface", tuple(CollectionSurface))
def test_durable_owner_wal_loader_binds_pre_cas_authorization_to_fresh_claim(
    surface: CollectionSurface,
) -> None:
    snapshot = _authorization_snapshot(surface)
    workflow, manifest = _submission_workflow(snapshot)
    authorization = authorize_submission_owner(snapshot, workflow)
    owner_dispatch_ref = f"owner-dispatch-{surface.value}"
    record = build_submission_owner_authorization_wal_record(
        authorization,
        claim_pub_id=f"claim-{surface.value}",
        owner_dispatch_ref=owner_dispatch_ref,
        recorded_at=authorization.authority.checked_at + timedelta(milliseconds=500),
    )
    command = _submit_command(
        authorization,
        manifest,
        owner_dispatch_ref=owner_dispatch_ref,
        owner_wal_evidence_sha256=record.evidence_sha256,
    )
    reader = _AuthorizationWalReader(record)
    loader = DurableSubmissionOwnerAuthorizationLoader(reader)
    transport = _OwnerTransport()
    gateway = AuthorizedSubmitOnceGateway(
        collection_surface=surface,
        gateway_kind=snapshot.owner.gateway_kind,
        owner_gateway_pub_id=snapshot.owner.owner_gateway_pub_id,
        owner_protocol_revision=snapshot.owner.protocol_revision,
        authorization_loader=loader,
        transport=transport,
        clock=lambda: command.fresh_claim.claim.claimed_at + timedelta(milliseconds=1),
    )

    result = gateway.submit_once(command)

    assert result.send_state is SendState.CONFIRMED_SENT
    assert reader.calls == [owner_dispatch_ref]
    assert len(transport.calls) == 1
    assert record.dispatch_key == command.fresh_claim.claim.dispatch_key
    assert record.evidence_sha256 == command.fresh_claim.claim.owner_wal_evidence_sha256
    assert record.owner_authorization == authorization


@pytest.mark.parametrize("surface", tuple(CollectionSurface))
def test_prepare_owner_turn_persists_exact_wal_before_post_cas_submit(
    surface: CollectionSurface,
) -> None:
    snapshot = _authorization_snapshot(surface)
    workflow, manifest = _submission_workflow(snapshot)
    store = _AuthorizationWalStore()
    transport = _OwnerTransport()
    owner_dispatch_ref = f"owner-dispatch-{surface.value}"

    turn = prepare_submission_owner_turn(
        snapshot,
        workflow,
        claim_pub_id=f"claim-{surface.value}",
        owner_dispatch_ref=owner_dispatch_ref,
        wal_store=store,
        transport=transport,
        clock=lambda: snapshot.checked_at + timedelta(seconds=2),
    )

    assert store.put_calls == [turn.wal_record]
    assert store.calls == []
    assert turn.authority == turn.authorization.authority
    replay = prepare_submission_owner_turn(
        snapshot,
        workflow,
        claim_pub_id=f"claim-{surface.value}",
        owner_dispatch_ref=owner_dispatch_ref,
        wal_store=store,
        transport=_OwnerTransport(),
        clock=lambda: snapshot.checked_at + timedelta(seconds=2),
    )
    assert replay.wal_record == turn.wal_record
    assert store.put_calls == [turn.wal_record, turn.wal_record]
    command = _submit_command(
        turn.authorization,
        manifest,
        owner_dispatch_ref=owner_dispatch_ref,
        owner_wal_evidence_sha256=turn.owner_wal_evidence_sha256,
    )

    result = turn.submit_gateway.submit_once(command)

    assert result.send_state is SendState.CONFIRMED_SENT
    assert store.calls == [owner_dispatch_ref]
    assert len(transport.calls) == 1


def test_prepare_owner_turn_rejects_conflicting_wal_before_gateway_exists() -> None:
    snapshot = _authorization_snapshot(CollectionSurface.CONSUMER_APP)
    workflow, _manifest = _submission_workflow(snapshot)
    authorization = authorize_submission_owner(snapshot, workflow)
    conflict = build_submission_owner_authorization_wal_record(
        authorization,
        claim_pub_id="claim-consumer_app-other",
        owner_dispatch_ref="owner-dispatch-consumer_app",
        recorded_at=snapshot.checked_at,
    )
    store = _AuthorizationWalStore(conflict=conflict)
    transport = _OwnerTransport()

    with pytest.raises(ResourceOwnerGatewayError) as exc_info:
        prepare_submission_owner_turn(
            snapshot,
            workflow,
            claim_pub_id="claim-consumer_app",
            owner_dispatch_ref="owner-dispatch-consumer_app",
            wal_store=store,
            transport=transport,
            clock=lambda: snapshot.checked_at,
        )

    assert exc_info.value.code == "resource_owner_authorization_wal_write_conflict"
    assert len(store.put_calls) == 1
    assert store.calls == []
    assert transport.calls == []


@pytest.mark.parametrize("surface", tuple(CollectionSurface))
def test_encrypted_file_owner_wal_round_trip_drives_one_submit(
    tmp_path: Path,
    surface: CollectionSurface,
) -> None:
    snapshot = _authorization_snapshot(surface)
    workflow, manifest = _submission_workflow(snapshot)
    root = tmp_path / f"owner-wal-{surface.value}"
    store = EncryptedFileSubmissionOwnerAuthorizationWalStore(
        root,
        vault=ProfileVault(LocalKms("owner-wal-test-master-key")),
        retention_period=timedelta(days=30),
        retention_policy_revision="owner-wal-retention-v1",
    )
    transport = _OwnerTransport()
    owner_dispatch_ref = f"owner-dispatch-{surface.value}"
    turn = prepare_submission_owner_turn(
        snapshot,
        workflow,
        claim_pub_id=f"claim-{surface.value}",
        owner_dispatch_ref=owner_dispatch_ref,
        wal_store=store,
        transport=transport,
        clock=lambda: snapshot.checked_at + timedelta(seconds=2),
    )

    wal_files = list(root.glob("*.wal"))
    assert len(wal_files) == 1
    wal_bytes = wal_files[0].read_bytes()
    assert turn.wal_record.claim_pub_id.encode() not in wal_bytes
    assert turn.wal_record.owner_dispatch_ref.encode() not in wal_bytes
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(wal_files[0].stat().st_mode) == 0o600
    assert store.load(owner_dispatch_ref=owner_dispatch_ref) == turn.wal_record
    retention = store.retention_metadata(owner_dispatch_ref=owner_dispatch_ref)
    assert retention is not None
    assert retention.policy_revision == "owner-wal-retention-v1"
    assert retention.evidence_sha256 == turn.wal_record.evidence_sha256
    assert retention.retain_until == snapshot.checked_at + timedelta(days=30)

    restarted_store = EncryptedFileSubmissionOwnerAuthorizationWalStore(
        root,
        vault=ProfileVault(LocalKms("owner-wal-test-master-key")),
        retention_period=timedelta(days=60),
        retention_policy_revision="owner-wal-retention-v2",
    )
    assert restarted_store.load(owner_dispatch_ref=owner_dispatch_ref) == turn.wal_record
    restarted_retention = restarted_store.retention_metadata(
        owner_dispatch_ref=owner_dispatch_ref
    )
    assert restarted_retention == retention
    replay = restarted_store.put(turn.wal_record)
    assert replay == turn.wal_record
    assert wal_files[0].read_bytes() == wal_bytes
    command = _submit_command(
        turn.authorization,
        manifest,
        owner_dispatch_ref=owner_dispatch_ref,
        owner_wal_evidence_sha256=turn.owner_wal_evidence_sha256,
    )

    result = turn.submit_gateway.submit_once(command)

    assert result.send_state is SendState.CONFIRMED_SENT
    assert len(transport.calls) == 1


def test_encrypted_file_owner_wal_rejects_conflicting_immutable_dispatch(
    tmp_path: Path,
) -> None:
    snapshot = _authorization_snapshot(CollectionSurface.CONSUMER_WEB)
    workflow, _manifest = _submission_workflow(snapshot)
    authorization = authorize_submission_owner(snapshot, workflow)
    owner_dispatch_ref = "owner-dispatch-conflict"
    first = build_submission_owner_authorization_wal_record(
        authorization,
        claim_pub_id="claim-consumer-web-first",
        owner_dispatch_ref=owner_dispatch_ref,
        recorded_at=snapshot.checked_at,
    )
    conflict = build_submission_owner_authorization_wal_record(
        authorization,
        claim_pub_id="claim-consumer-web-conflict",
        owner_dispatch_ref=owner_dispatch_ref,
        recorded_at=snapshot.checked_at,
    )
    root = tmp_path / "owner-wal-conflict"
    store = EncryptedFileSubmissionOwnerAuthorizationWalStore(
        root,
        vault=ProfileVault(LocalKms("owner-wal-test-master-key")),
        retention_period=timedelta(days=30),
        retention_policy_revision="owner-wal-retention-v1",
    )
    store.put(first)
    before = next(root.glob("*.wal")).read_bytes()

    with pytest.raises(ResourceOwnerGatewayError) as exc_info:
        store.put(conflict)

    assert exc_info.value.code == "resource_owner_authorization_wal_write_conflict"
    assert store.load(owner_dispatch_ref=owner_dispatch_ref) == first
    assert next(root.glob("*.wal")).read_bytes() == before


def test_encrypted_file_owner_wal_fails_closed_on_sealed_metadata_or_key_drift(
    tmp_path: Path,
) -> None:
    snapshot = _authorization_snapshot(CollectionSurface.PROVIDER_API)
    workflow, _manifest = _submission_workflow(snapshot)
    authorization = authorize_submission_owner(snapshot, workflow)
    owner_dispatch_ref = "owner-dispatch-tamper"
    record = build_submission_owner_authorization_wal_record(
        authorization,
        claim_pub_id="claim-provider-api-tamper",
        owner_dispatch_ref=owner_dispatch_ref,
        recorded_at=snapshot.checked_at,
    )
    root = tmp_path / "owner-wal-tamper"
    store = EncryptedFileSubmissionOwnerAuthorizationWalStore(
        root,
        vault=ProfileVault(LocalKms("owner-wal-correct-master-key")),
        retention_period=timedelta(days=30),
        retention_policy_revision="owner-wal-retention-v1",
    )
    store.put(record)
    wal_path = next(root.glob("*.wal"))
    envelope = json.loads(wal_path.read_text(encoding="utf-8"))
    envelope["retention"]["policy_revision"] = "owner-wal-retention-tampered"
    wal_path.write_text(
        json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.chmod(wal_path, 0o600)

    with pytest.raises(ResourceOwnerGatewayError) as metadata_error:
        store.load(owner_dispatch_ref=owner_dispatch_ref)
    assert metadata_error.value.code == "resource_owner_authorization_wal_decrypt_failed"

    other_root = tmp_path / "owner-wal-key-drift"
    correct = EncryptedFileSubmissionOwnerAuthorizationWalStore(
        other_root,
        vault=ProfileVault(LocalKms("owner-wal-correct-master-key")),
        retention_period=timedelta(days=30),
        retention_policy_revision="owner-wal-retention-v1",
    )
    correct.put(record)
    wrong_key = EncryptedFileSubmissionOwnerAuthorizationWalStore(
        other_root,
        vault=ProfileVault(LocalKms("owner-wal-wrong-master-key")),
        retention_period=timedelta(days=30),
        retention_policy_revision="owner-wal-retention-v1",
    )

    with pytest.raises(ResourceOwnerGatewayError) as key_error:
        wrong_key.load(owner_dispatch_ref=owner_dispatch_ref)
    assert key_error.value.code == "resource_owner_authorization_wal_decrypt_failed"


def test_encrypted_file_owner_wal_configuration_and_missing_record_are_explicit(
    tmp_path: Path,
) -> None:
    vault = ProfileVault(LocalKms("owner-wal-test-master-key"))
    with pytest.raises(ResourceOwnerGatewayError) as relative_root:
        EncryptedFileSubmissionOwnerAuthorizationWalStore(
            Path("relative-owner-wal"),
            vault=vault,
            retention_period=timedelta(days=30),
            retention_policy_revision="owner-wal-retention-v1",
        )
    assert relative_root.value.code == "resource_owner_authorization_wal_configuration_invalid"

    store = EncryptedFileSubmissionOwnerAuthorizationWalStore(
        tmp_path / "owner-wal-missing",
        vault=vault,
        retention_period=timedelta(days=30),
        retention_policy_revision="owner-wal-retention-v1",
    )
    assert store.load(owner_dispatch_ref="owner-dispatch-missing") is None


def test_durable_owner_wal_loader_does_not_cache_a_removed_record() -> None:
    snapshot = _authorization_snapshot(CollectionSurface.CONSUMER_WEB)
    workflow, manifest = _submission_workflow(snapshot)
    authorization = authorize_submission_owner(snapshot, workflow)
    owner_dispatch_ref = "owner-dispatch-consumer-web"
    record = build_submission_owner_authorization_wal_record(
        authorization,
        claim_pub_id="claim-consumer_web",
        owner_dispatch_ref=owner_dispatch_ref,
        recorded_at=authorization.authority.checked_at,
    )
    command = _submit_command(
        authorization,
        manifest,
        owner_dispatch_ref=owner_dispatch_ref,
        owner_wal_evidence_sha256=record.evidence_sha256,
    )
    reader = _AuthorizationWalReader(record)
    loader = DurableSubmissionOwnerAuthorizationLoader(reader)

    assert loader.load(command) == authorization
    reader.record = None
    with pytest.raises(ResourceOwnerGatewayError) as exc_info:
        loader.load(command)

    assert exc_info.value.code == "resource_owner_authorization_wal_missing"
    assert reader.calls == [owner_dispatch_ref, owner_dispatch_ref]


def test_durable_owner_wal_loader_rejects_claim_or_digest_drift() -> None:
    snapshot = _authorization_snapshot(CollectionSurface.PROVIDER_API)
    workflow, manifest = _submission_workflow(snapshot)
    authorization = authorize_submission_owner(snapshot, workflow)
    record = build_submission_owner_authorization_wal_record(
        authorization,
        claim_pub_id="claim-provider_api",
        owner_dispatch_ref="owner-dispatch-provider-api",
        recorded_at=authorization.authority.checked_at,
    )
    command = _submit_command(
        authorization,
        manifest,
        owner_dispatch_ref="owner-dispatch-provider-api",
        owner_wal_evidence_sha256=record.evidence_sha256,
    )
    mismatched = record.model_copy(update={"claim_pub_id": "claim-provider_api-other"})
    loader = DurableSubmissionOwnerAuthorizationLoader(_AuthorizationWalReader(mismatched))

    with pytest.raises(ResourceOwnerGatewayError) as exc_info:
        loader.load(command)
    assert exc_info.value.code == "resource_owner_authorization_wal_claim_mismatch"

    with pytest.raises(ValidationError, match="owner_authorization_wal_digest_mismatch"):
        SubmissionOwnerAuthorizationWalRecord.model_validate(
            record.model_dump(mode="python") | {"evidence_sha256": "4" * 64}
        )


def test_owner_gateway_rejects_cross_surface_authorization_before_transport() -> None:
    snapshot = _authorization_snapshot(CollectionSurface.CONSUMER_WEB)
    workflow, manifest = _submission_workflow(snapshot)
    authorization = authorize_submission_owner(snapshot, workflow)
    command = _submit_command(authorization, manifest)
    loader = _AuthorizationLoader(authorization)
    transport = _OwnerTransport()
    gateway = AuthorizedSubmitOnceGateway(
        collection_surface=CollectionSurface.CONSUMER_APP,
        gateway_kind=GatewayKind.MANAGED_APP_SESSION,
        owner_gateway_pub_id=snapshot.owner.owner_gateway_pub_id,
        owner_protocol_revision=snapshot.owner.protocol_revision,
        authorization_loader=loader,
        transport=transport,
        clock=lambda: command.fresh_claim.claim.claimed_at,
    )

    with pytest.raises(ResourceOwnerGatewayError) as exc_info:
        gateway.submit_once(command)

    assert exc_info.value.code == "resource_owner_surface_mismatch"
    assert transport.calls == []


def test_owner_gateway_rejects_stale_workflow_version_before_authority_projection() -> None:
    snapshot = _authorization_snapshot(CollectionSurface.PROVIDER_API)
    workflow, _manifest = _submission_workflow(snapshot)
    stale = WorkflowOperationInput(
        operation=workflow.operation,
        expected_state_version=workflow.expected_state_version + 1,
    )

    with pytest.raises(ResourceOwnerGatewayError) as exc_info:
        authorize_submission_owner(snapshot, stale)

    assert exc_info.value.code == "submission_workflow_state_version_mismatch"


def test_owner_gateway_expiry_fails_closed_without_transport() -> None:
    snapshot = _authorization_snapshot(CollectionSurface.CONSUMER_APP)
    workflow, manifest = _submission_workflow(snapshot)
    authorization = authorize_submission_owner(snapshot, workflow)
    command = _submit_command(authorization, manifest)
    loader = _AuthorizationLoader(authorization)
    transport = _OwnerTransport()
    gateway = AuthorizedSubmitOnceGateway(
        collection_surface=CollectionSurface.CONSUMER_APP,
        gateway_kind=GatewayKind.MANAGED_APP_SESSION,
        owner_gateway_pub_id=snapshot.owner.owner_gateway_pub_id,
        owner_protocol_revision=snapshot.owner.protocol_revision,
        authorization_loader=loader,
        transport=transport,
        clock=lambda: authorization.authority.valid_until,
    )

    with pytest.raises(ResourceOwnerGatewayError) as exc_info:
        gateway.submit_once(command)

    assert exc_info.value.code == "resource_owner_authorization_expired"
    assert transport.calls == []


def test_owner_gateway_never_retries_a_transport_exception() -> None:
    snapshot = _authorization_snapshot(CollectionSurface.PROVIDER_API)
    workflow, manifest = _submission_workflow(snapshot)
    authorization = authorize_submission_owner(snapshot, workflow)
    command = _submit_command(authorization, manifest)
    loader = _AuthorizationLoader(authorization)

    class FailingTransport:
        def __init__(self) -> None:
            self.calls = 0

        def submit_once(
            self,
            command: SubmitOnceCommand,
            *,
            authorization: SideEffectAuthorization,
        ) -> SubmitDisposition:
            del command, authorization
            self.calls += 1
            raise RuntimeError("injected-owner-boundary-failure")

    transport = FailingTransport()
    gateway = AuthorizedSubmitOnceGateway(
        collection_surface=CollectionSurface.PROVIDER_API,
        gateway_kind=GatewayKind.PROVIDER_REQUEST,
        owner_gateway_pub_id=snapshot.owner.owner_gateway_pub_id,
        owner_protocol_revision=snapshot.owner.protocol_revision,
        authorization_loader=loader,
        transport=transport,
        clock=lambda: command.fresh_claim.claim.claimed_at,
    )

    with pytest.raises(RuntimeError, match="injected-owner-boundary-failure"):
        gateway.submit_once(command)

    assert loader.calls == 1
    assert transport.calls == 1
