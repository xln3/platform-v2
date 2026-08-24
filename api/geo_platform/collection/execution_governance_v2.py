"""Pure v2 binding/grant issuance and resource-owner authorization services.

The functions here never query or mutate a database and never perform external
I/O.  Callers must construct the snapshots from rows locked by the persistence
boundary.  The resource owner must persist the returned NOT_SENT -> SENDING
transition before invoking its one physical submit and must not hand the result
to an adapter as a reusable capability.  Lease transition helpers are pure
compare-and-set specifications: the persistence owner must serialize or
atomically CAS the referenced pool row; calling a pure function alone does not
close a database race.
"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any, NoReturn

from pydantic import BaseModel, ConfigDict, Field, field_validator

from domain.collection.execution_governance import (
    ApiExecutionGrant,
    AppExecutionGrant,
    BindingExecutionRef,
    BindingRevision,
    CampaignSlotExecutionRef,
    ConfigTargetExecutionRef,
    ConsumerAppBinding,
    ConsumerWebBinding,
    ExecutionAction,
    ExecutionCompatibility,
    ExecutionGovernanceError,
    ExecutionGrantEnvelope,
    ExecutionGrantPayload,
    GatewayKind,
    GrantDimensions,
    ProviderApiBinding,
    QuotaReservationEffectState,
    QuotaReservationSnapshot,
    QuotaReservationState,
    ResourceCapacityPoolSnapshot,
    ResourceCapacityUnitSnapshot,
    ResourceKind,
    ResourceLeaseAcquireRequest,
    ResourceLeaseAcquireResult,
    ResourceLeaseHeartbeatDisposition,
    ResourceLeaseHeartbeatRequest,
    ResourceLeaseHeartbeatResult,
    ResourceLeaseSnapshot,
    ResourceLeaseState,
    ResourceOwnerSnapshot,
    ResourceOwnerState,
    SideEffectAuthorization,
    SubmissionOperationRef,
    SubmissionOperationSnapshot,
    WebExecutionGrant,
    assert_binding_usable,
    assert_governance_payload_safe,
    assert_grant_usable,
)
from domain.collection.surface import ConfigLifecycleState, SendState

from .identity_v2 import (
    FrozenCampaign,
    FrozenCampaignSlot,
    FrozenCampaignTarget,
    FrozenConfigRevision,
    FrozenSamplingLeg,
)


class _FrozenServiceModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class ExecutionGrantIssueRequest(_FrozenServiceModel):
    grant_pub_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    grant_revision: int = Field(strict=True, ge=1)
    config_revision: FrozenConfigRevision
    campaign: FrozenCampaign
    campaign_target: FrozenCampaignTarget
    sampling_leg: FrozenSamplingLeg
    slot: FrozenCampaignSlot
    binding: BindingRevision
    operation: SubmissionOperationSnapshot
    quota_reservation: QuotaReservationSnapshot
    resource_leases: tuple[ResourceLeaseSnapshot, ...] = Field(min_length=1)
    payload: ExecutionGrantPayload
    compatibility: ExecutionCompatibility
    allowed_actions: tuple[ExecutionAction, ...] = (ExecutionAction.SUBMIT_QUERY,)
    issued_by_pub_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    issuance_reason: str = Field(pattern=r"^[a-z0-9][a-z0-9._:-]{0,127}$")
    issued_at: datetime
    expires_at: datetime

    @field_validator("issued_at", "expires_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime, info: Any) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{info.field_name}_must_be_timezone_aware")
        return value


class GatewayAuthorizationSnapshot(_FrozenServiceModel):
    """Current, owner-local view acquired immediately before a side effect."""

    checked_at: datetime
    action: ExecutionAction
    owner: ResourceOwnerSnapshot
    grant: ExecutionGrantEnvelope
    binding: BindingRevision
    operation: SubmissionOperationSnapshot
    quota_reservation: QuotaReservationSnapshot
    resource_leases: tuple[ResourceLeaseSnapshot, ...] = Field(min_length=1)

    @field_validator("checked_at")
    @classmethod
    def checked_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("checked_at_must_be_timezone_aware")
        return value


def _fail(code: str, **context: str | int | bool | None) -> NoReturn:
    raise ExecutionGovernanceError(code, **context)


def _same_scope(
    *,
    tenant_id: object,
    project_id: object,
    expected_tenant_id: object,
    expected_project_id: object,
    code: str,
) -> None:
    if tenant_id != expected_tenant_id or project_id != expected_project_id:
        _fail(code)


def _payload_owner_handle(payload: ExecutionGrantPayload) -> str:
    if isinstance(payload, ApiExecutionGrant):
        return payload.provider_gateway_handle
    if isinstance(payload, WebExecutionGrant):
        return payload.browser_owner_handle
    return payload.device_owner_handle


def _main_owner_resource_kind(payload: ExecutionGrantPayload) -> ResourceKind:
    if isinstance(payload, ApiExecutionGrant):
        return ResourceKind.CREDENTIAL_SLOT
    if isinstance(payload, WebExecutionGrant):
        return ResourceKind.BROWSER_OWNER
    return ResourceKind.DEVICE_OWNER


def _main_mapping_matches_payload(
    mapping_resource_pub_id: str,
    payload: ExecutionGrantPayload,
) -> bool:
    """Bind typed physical references without treating a business role as a kind."""

    if isinstance(payload, ApiExecutionGrant):
        return mapping_resource_pub_id == payload.credential_slot_handle
    if isinstance(payload, AppExecutionGrant):
        return mapping_resource_pub_id == payload.managed_device_ref
    # The web subtype identifies the browser owner by its opaque owner handle,
    # which is asserted against the lease below; it does not expose the backing
    # resource registration's public id.
    return True


def _surface_route_policy_revision(binding: BindingRevision) -> str:
    if isinstance(binding.payload, ProviderApiBinding):
        return binding.payload.egress_policy_revision
    return binding.payload.relay_policy_revision


def _validate_payload_matches_binding(
    binding: BindingRevision, payload: ExecutionGrantPayload
) -> None:
    binding_payload = binding.payload
    if isinstance(binding_payload, ProviderApiBinding):
        if not isinstance(payload, ApiExecutionGrant):
            _fail("grant_binding_subtype_mismatch")
        if (
            payload.provider_gateway_handle != binding_payload.provider_gateway_handle
            or payload.credential_slot_handle != binding_payload.credential_slot_ref
            or payload.provider_endpoint_catalog_id != binding_payload.endpoint_catalog_id
            or payload.provider_api_version != binding_payload.api_version
            or payload.provider_tenant_context_ref != binding_payload.provider_tenant_ref
        ):
            _fail("api_grant_binding_payload_mismatch")
        if not any(
            subject.subject_pub_id == payload.provider_quota_subject_ref
            for subject in binding.quota_subjects
        ):
            _fail("api_grant_quota_subject_mismatch")
        return
    if isinstance(binding_payload, ConsumerWebBinding):
        if not isinstance(payload, WebExecutionGrant):
            _fail("grant_binding_subtype_mismatch")
        if (
            payload.browser_owner_handle != binding_payload.browser_owner_handle
            or payload.governed_account_ref != binding_payload.governed_account_ref
            or payload.browser_profile_ref != binding_payload.browser_profile_ref
            or payload.browser_profile_revision != binding_payload.browser_profile_revision
            or payload.web_session_ref != binding_payload.web_session_ref
            or payload.web_session_revision != binding_payload.web_session_revision
            or payload.approved_host_catalog_id != binding_payload.approved_host_catalog_id
        ):
            _fail("web_grant_binding_payload_mismatch")
        return
    if not isinstance(binding_payload, ConsumerAppBinding) or not isinstance(
        payload, AppExecutionGrant
    ):
        _fail("grant_binding_subtype_mismatch")
    if (
        payload.device_owner_handle != binding_payload.device_owner_handle
        or payload.governed_account_ref != binding_payload.governed_account_ref
        or payload.managed_device_ref != binding_payload.managed_device_ref
        or payload.app_package_id != binding_payload.app_package_id
        or payload.app_build_version != binding_payload.app_build_version
        or payload.distribution_channel != binding_payload.distribution_channel
        or payload.app_install_ref != binding_payload.app_install_ref
        or payload.app_session_ref != binding_payload.app_session_ref
        or payload.app_session_revision != binding_payload.app_session_revision
        or payload.automation_agent_revision != binding_payload.automation_agent_revision
    ):
        _fail("app_grant_binding_payload_mismatch")


def _validate_identity_chain(request: ExecutionGrantIssueRequest) -> None:
    config = request.config_revision
    campaign = request.campaign
    campaign_target = request.campaign_target
    leg = request.sampling_leg
    slot = request.slot
    binding = request.binding

    if config.lifecycle_state is not ConfigLifecycleState.ACTIVE:
        _fail("grant_requires_active_config")
    if config.activated_at is None or request.issued_at < config.activated_at:
        _fail("grant_precedes_config_activation")
    if request.issued_at < campaign.frozen_at:
        _fail("grant_precedes_campaign_freeze")
    _same_scope(
        tenant_id=campaign.tenant_id,
        project_id=campaign.project_id,
        expected_tenant_id=config.tenant_id,
        expected_project_id=config.project_id,
        code="campaign_config_scope_mismatch",
    )
    _same_scope(
        tenant_id=binding.tenant_id,
        project_id=binding.project_id,
        expected_tenant_id=config.tenant_id,
        expected_project_id=config.project_id,
        code="binding_config_scope_mismatch",
    )
    if (
        campaign.config_revision_id != config.id
        or campaign.config_revision_pub_id != config.revision_pub_id
        or campaign.config_revision_hash != config.revision_hash
    ):
        _fail("campaign_config_revision_mismatch")
    # ``FrozenCampaign`` is deliberately a compact persisted freeze proof.  It
    # does not carry every target, leg, or slot in memory.  The caller loads
    # these exact member rows under the campaign's tenant/project scope and the
    # checks below validate their complete local lineage before issuance.
    if slot.ordinal >= campaign.expected_slot_count:
        _fail("campaign_slot_ordinal_out_of_membership")
    if (
        leg.campaign_target_id != campaign_target.id
        or leg.campaign_target_pub_id != campaign_target.pub_id
        or slot.campaign_target_id != campaign_target.id
        or slot.campaign_target_pub_id != campaign_target.pub_id
        or slot.sampling_leg_id != leg.id
        or slot.sampling_leg_pub_id != leg.pub_id
    ):
        _fail("campaign_slot_lineage_mismatch")

    config_target = next(
        (target for target in config.targets if target.id == campaign_target.config_target_id),
        None,
    )
    if config_target is None or config_target.target != campaign_target.target:
        _fail("campaign_config_target_mismatch")
    dimensions = slot.identity
    if (
        dimensions.campaign_id != campaign.campaign_pub_id
        or dimensions.platform != leg.platform
        or dimensions.collection_surface is not leg.collection_surface
        or dimensions.product_variant != leg.product_variant
        or dimensions.province_code != leg.province_code
        or dimensions.interaction_mode != leg.interaction_mode
    ):
        _fail("slot_leg_dimensions_mismatch")
    if slot.slot_identity_hash != sha256(slot.slot_key.encode("utf-8")).hexdigest():
        _fail("slot_identity_hash_mismatch")
    if (
        binding.target.target_key != campaign_target.target_key
        or binding.target.platform != leg.platform
        or binding.target.collection_surface is not leg.collection_surface
        or binding.target.product_variant != leg.product_variant
        or binding.target.interaction_mode != leg.interaction_mode
    ):
        _fail("binding_campaign_target_mismatch")
    capability_revision = campaign_target.capability_revision_mapping.get(leg.interaction_mode)
    if capability_revision is None or binding.target.capability_revision != capability_revision:
        _fail("binding_capability_revision_mismatch")
    if binding.binding_policy_revision != campaign_target.binding_policy_revision:
        _fail("binding_policy_revision_mismatch")


def _validate_operation(request: ExecutionGrantIssueRequest) -> None:
    operation = request.operation
    slot = request.slot
    _same_scope(
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        expected_tenant_id=request.config_revision.tenant_id,
        expected_project_id=request.config_revision.project_id,
        code="operation_scope_mismatch",
    )
    if operation.slot_pub_id != slot.pub_id or operation.logical_item_key != slot.slot_key:
        _fail("operation_slot_mismatch")
    if operation.generation != operation.current_generation:
        _fail(
            "operation_generation_stale",
            generation=operation.generation,
            current_generation=operation.current_generation,
        )
    if operation.send_state is not SendState.NOT_SENT:
        _fail("operation_not_sendable", send_state=operation.send_state.value)


def _validate_quota_snapshot(request: ExecutionGrantIssueRequest) -> None:
    binding = request.binding
    operation = request.operation
    reservation = request.quota_reservation
    _same_scope(
        tenant_id=reservation.tenant_id,
        project_id=reservation.project_id,
        expected_tenant_id=binding.tenant_id,
        expected_project_id=binding.project_id,
        code="quota_reservation_scope_mismatch",
    )
    if reservation.operation_pub_id != operation.operation_pub_id:
        _fail("quota_reservation_operation_mismatch")
    if (
        reservation.binding_pub_id != binding.binding_pub_id
        or reservation.binding_revision != binding.revision
        or reservation.binding_hash != binding.binding_hash
    ):
        _fail("quota_reservation_binding_mismatch")
    if reservation.state is not QuotaReservationState.RESERVED:
        _fail(
            "quota_reservation_not_reserved",
            reservation_id=str(reservation.reservation_id),
            state=reservation.state.value,
        )
    if (
        reservation.quota_registry_id != binding.quota_registry_id
        or reservation.scope_registry_revision != binding.quota_scope_registry_revision
    ):
        _fail("quota_scope_registry_revision_mismatch")

    expected_by_policy_id = {scope.quota_scope_policy_id: scope for scope in binding.quota_scopes}
    effects_by_policy_id = {effect.quota_scope_policy_id: effect for effect in reservation.effects}
    if set(effects_by_policy_id) != set(expected_by_policy_id):
        _fail("binding_quota_effect_set_mismatch")
    for policy_id, expected in expected_by_policy_id.items():
        effect = effects_by_policy_id[policy_id]
        if effect.state is not QuotaReservationEffectState.RESERVED:
            _fail(
                "quota_reservation_effect_not_reserved",
                effect_id=str(effect.effect_id),
                state=effect.state.value,
            )
        if effect.scope_kind is not expected.scope_kind:
            _fail("quota_reservation_effect_scope_mismatch")
        if effect.scope_key != expected.scope_key:
            _fail("quota_reservation_effect_scope_key_mismatch")
        expected_units = expected.quota_units * reservation.requested_units
        if effect.units != expected_units:
            _fail(
                "quota_reservation_effect_units_mismatch",
                expected=expected_units,
                actual=effect.units,
            )


def _validate_resource_snapshots(request: ExecutionGrantIssueRequest) -> None:
    binding = request.binding
    operation = request.operation
    bound_by_identity = {mapping.identity: mapping for mapping in binding.resource_mappings}
    seen_mapping_ids: set[tuple[object, ...]] = set()
    seen_lease_ids: set[str] = set()
    for lease in request.resource_leases:
        _same_scope(
            tenant_id=lease.tenant_id,
            project_id=lease.project_id,
            expected_tenant_id=binding.tenant_id,
            expected_project_id=binding.project_id,
            code="resource_lease_scope_mismatch",
        )
        if lease.operation_pub_id != operation.operation_pub_id:
            _fail("resource_lease_operation_mismatch")
        if (
            lease.binding_pub_id != binding.binding_pub_id
            or lease.binding_revision != binding.revision
            or lease.binding_hash != binding.binding_hash
        ):
            _fail("resource_lease_binding_mismatch")
        if lease.state is not ResourceLeaseState.ACTIVE:
            _fail(
                "resource_lease_not_active",
                lease_pub_id=lease.lease_pub_id,
                state=lease.state.value,
            )
        if lease.fence_generation != lease.current_fence_generation:
            _fail(
                "resource_fence_stale",
                resource_kind=lease.resource_kind.value,
                expected=lease.current_fence_generation,
                actual=lease.fence_generation,
            )
        if lease.acquired_at > request.issued_at:
            _fail(
                "resource_lease_acquired_in_future",
                resource_kind=lease.resource_kind.value,
            )
        if lease.expires_at < request.expires_at:
            _fail("grant_outlives_resource_lease", resource_kind=lease.resource_kind.value)
        if lease.lease_pub_id in seen_lease_ids:
            _fail("duplicate_resource_lease")
        seen_lease_ids.add(lease.lease_pub_id)
        mapping_identity = lease.binding_mapping_identity
        if mapping_identity in seen_mapping_ids:
            _fail("duplicate_resource_lease_mapping")
        seen_mapping_ids.add(mapping_identity)
        if mapping_identity not in bound_by_identity:
            _fail(
                "resource_lease_binding_mapping_mismatch",
                resource_kind=lease.resource_kind.value,
            )
    required_mapping_ids = {
        mapping.identity for mapping in binding.resource_mappings if mapping.required
    }
    missing = required_mapping_ids.difference(seen_mapping_ids)
    if missing:
        _fail("required_resource_lease_missing", missing_count=len(missing))

    main_kind = _main_owner_resource_kind(request.payload)
    main_mappings = tuple(
        mapping
        for mapping in binding.resource_mappings
        if mapping.required
        and mapping.resource_kind is main_kind
        and _main_mapping_matches_payload(mapping.resource_pub_id, request.payload)
    )
    if len(main_mappings) != 1:
        _fail("grant_primary_resource_mapping_mismatch")
    main_leases = tuple(
        lease
        for lease in request.resource_leases
        if lease.binding_mapping_identity == main_mappings[0].identity
    )
    if len(main_leases) != 1 or main_leases[0].owner_gateway_pub_id != _payload_owner_handle(
        request.payload
    ):
        _fail("grant_owner_handle_mismatch")


def issue_execution_grant(request: ExecutionGrantIssueRequest) -> ExecutionGrantEnvelope:
    """Validate the complete frozen chain and construct one secret-free grant."""

    # Recheck the complete aggregate at the service boundary even though every
    # governance domain model also validates itself. This protects callers that
    # hydrate trusted snapshots without Pydantic validation.
    assert_governance_payload_safe(request)
    if request.expires_at <= request.issued_at:
        _fail("grant_expiry_must_follow_issuance")
    if request.expires_at > request.binding.expires_at:
        _fail("grant_outlives_binding")
    assert_binding_usable(request.binding, at=request.issued_at)
    _validate_identity_chain(request)
    _validate_operation(request)
    _validate_payload_matches_binding(request.binding, request.payload)
    _validate_quota_snapshot(request)
    _validate_resource_snapshots(request)
    if isinstance(request.payload, AppExecutionGrant) and (
        request.compatibility.agent_revision != request.payload.automation_agent_revision
    ):
        _fail("app_agent_compatibility_mismatch")

    slot_identity = request.slot.identity
    campaign_target = request.campaign_target
    grant = ExecutionGrantEnvelope(
        grant_pub_id=request.grant_pub_id,
        grant_revision=request.grant_revision,
        tenant_id=request.config_revision.tenant_id,
        project_id=request.config_revision.project_id,
        issued_at=request.issued_at,
        expires_at=request.expires_at,
        config_target=ConfigTargetExecutionRef(
            config_revision_pub_id=request.config_revision.revision_pub_id,
            config_revision_hash=request.config_revision.revision_hash,
            config_target_pub_id=campaign_target.config_target_pub_id,
            target_key=campaign_target.target_key,
            capability_revision=request.binding.target.capability_revision,
        ),
        campaign_slot=CampaignSlotExecutionRef(
            campaign_pub_id=request.campaign.campaign_pub_id,
            campaign_membership_hash=request.campaign.membership_hash,
            campaign_target_pub_id=campaign_target.pub_id,
            sampling_leg_pub_id=request.sampling_leg.pub_id,
            slot_pub_id=request.slot.pub_id,
            slot_key=request.slot.slot_key,
            question_slot_id=slot_identity.question_slot_id,
            question_revision=request.slot.question_revision,
        ),
        operation=SubmissionOperationRef(
            operation_pub_id=request.operation.operation_pub_id,
            logical_item_key=request.operation.logical_item_key,
            generation=request.operation.generation,
        ),
        dimensions=GrantDimensions(
            platform=slot_identity.platform,
            collection_surface=slot_identity.collection_surface,
            product_variant=slot_identity.product_variant,
            interaction_mode=slot_identity.interaction_mode,
            province_code=slot_identity.province_code,
        ),
        binding=BindingExecutionRef(
            binding_pub_id=request.binding.binding_pub_id,
            binding_revision=request.binding.revision,
            binding_hash=request.binding.binding_hash,
            binding_policy_revision=request.binding.binding_policy_revision,
            quota_policy_revision=request.binding.quota_policy_revision,
            quota_registry_id=request.binding.quota_registry_id,
            region_policy_revision=request.binding.region_policy_revision,
            route_policy_revision=request.binding.route_policy_revision,
            surface_route_policy_revision=_surface_route_policy_revision(request.binding),
            required_quota_scopes=request.binding.quota_scopes,
            required_resource_kinds=request.binding.required_resource_kinds,
            resource_mappings=request.binding.resource_mappings,
        ),
        quota_registry_id=request.binding.quota_registry_id,
        quota_scope_registry_revision=request.binding.quota_scope_registry_revision,
        quota_reservation=request.quota_reservation.grant_ref,
        resource_fences=tuple(lease.grant_ref for lease in request.resource_leases),
        compatibility=request.compatibility,
        allowed_actions=request.allowed_actions,
        issued_by_pub_id=request.issued_by_pub_id,
        issuance_reason=request.issuance_reason,
        payload=request.payload,
    )
    assert_governance_payload_safe(grant)
    return grant


def _validate_current_operation(snapshot: GatewayAuthorizationSnapshot) -> None:
    grant = snapshot.grant
    operation = snapshot.operation
    _same_scope(
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        expected_tenant_id=grant.tenant_id,
        expected_project_id=grant.project_id,
        code="gateway_operation_scope_mismatch",
    )
    if (
        operation.operation_pub_id != grant.operation.operation_pub_id
        or operation.slot_pub_id != grant.campaign_slot.slot_pub_id
        or operation.logical_item_key != grant.operation.logical_item_key
    ):
        _fail("gateway_operation_mismatch")
    if (
        operation.generation != grant.operation.generation
        or operation.generation != operation.current_generation
    ):
        _fail(
            "operation_generation_stale",
            generation=operation.generation,
            current_generation=operation.current_generation,
        )
    if operation.send_state is not SendState.NOT_SENT:
        _fail("operation_not_sendable", send_state=operation.send_state.value)


def _validate_current_quota(snapshot: GatewayAuthorizationSnapshot) -> None:
    grant = snapshot.grant
    current = snapshot.quota_reservation
    reference = grant.quota_reservation
    _same_scope(
        tenant_id=current.tenant_id,
        project_id=current.project_id,
        expected_tenant_id=grant.tenant_id,
        expected_project_id=grant.project_id,
        code="gateway_quota_scope_mismatch",
    )
    if current.operation_pub_id != grant.operation.operation_pub_id:
        _fail("gateway_quota_operation_mismatch")
    if (
        current.binding_pub_id != grant.binding.binding_pub_id
        or current.binding_revision != grant.binding.binding_revision
        or current.binding_hash != grant.binding.binding_hash
    ):
        _fail("gateway_quota_binding_mismatch")
    if current.state is not QuotaReservationState.RESERVED:
        _fail(
            "quota_reservation_not_reserved",
            reservation_id=str(current.reservation_id),
            state=current.state.value,
        )
    if current.grant_ref != reference:
        _fail("gateway_quota_reservation_drift")
    if (
        current.quota_registry_id != grant.quota_registry_id
        or current.scope_registry_revision != grant.quota_scope_registry_revision
    ):
        _fail("gateway_quota_registry_mismatch")
    for effect in current.effects:
        if effect.state is not QuotaReservationEffectState.RESERVED:
            _fail(
                "quota_reservation_effect_not_reserved",
                effect_id=str(effect.effect_id),
                state=effect.state.value,
            )


def _validate_current_resources(snapshot: GatewayAuthorizationSnapshot) -> None:
    grant = snapshot.grant
    current_by_lease = {lease.lease_pub_id: lease for lease in snapshot.resource_leases}
    if len(current_by_lease) != len(snapshot.resource_leases):
        _fail("duplicate_current_resource_lease")
    expected_ids = {reference.lease_pub_id for reference in grant.resource_fences}
    if set(current_by_lease) != expected_ids:
        _fail("gateway_resource_lease_set_mismatch")
    for reference in grant.resource_fences:
        current = current_by_lease[reference.lease_pub_id]
        _same_scope(
            tenant_id=current.tenant_id,
            project_id=current.project_id,
            expected_tenant_id=grant.tenant_id,
            expected_project_id=grant.project_id,
            code="gateway_resource_scope_mismatch",
        )
        if current.operation_pub_id != grant.operation.operation_pub_id:
            _fail("gateway_resource_operation_mismatch")
        if (
            current.binding_pub_id != grant.binding.binding_pub_id
            or current.binding_revision != grant.binding.binding_revision
            or current.binding_hash != grant.binding.binding_hash
        ):
            _fail("gateway_resource_binding_mismatch")
        if current.state is not ResourceLeaseState.ACTIVE:
            _fail(
                "resource_lease_not_active",
                lease_pub_id=current.lease_pub_id,
                state=current.state.value,
            )
        if current.fence_generation != current.current_fence_generation:
            _fail(
                "resource_fence_stale",
                resource_kind=current.resource_kind.value,
                expected=current.current_fence_generation,
                actual=current.fence_generation,
            )
        if current.acquired_at > snapshot.checked_at:
            _fail(
                "resource_lease_acquired_in_future",
                resource_kind=current.resource_kind.value,
            )
        if current.expires_at <= snapshot.checked_at:
            _fail("resource_lease_expired", resource_kind=current.resource_kind.value)
        if (
            current.resource_registration_id != reference.resource_registration_id
            or current.capacity_unit_id != reference.capacity_unit_id
            or current.resource_kind is not reference.resource_kind
            or current.resource_pub_id != reference.resource_pub_id
            or current.resource_role != reference.resource_role
            or current.resource_ordinal != reference.resource_ordinal
            or current.binding_resource_mapping_revision
            != reference.binding_resource_mapping_revision
            or current.lease_pub_id != reference.lease_pub_id
            or current.owner_gateway_pub_id != reference.owner_gateway_pub_id
            or current.fence_generation != reference.fence_generation
            or current.capacity_unit != reference.capacity_unit
            or current.acquired_at != reference.acquired_at
            or current.expires_at < reference.expires_at
        ):
            _fail("gateway_resource_fence_drift")


def authorize_irreversible_action(
    snapshot: GatewayAuthorizationSnapshot,
) -> SideEffectAuthorization:
    """Authorize exactly one owner-controlled NOT_SENT -> SENDING transition.

    This function must be called from inside the unique resource owner's
    serialization gate immediately before it durably writes ``SENDING``.  A
    returned value is an assertion set, not a bearer token and not permission for
    an adapter to retain or reuse a physical handle.
    """

    grant = snapshot.grant
    binding = snapshot.binding
    owner = snapshot.owner
    assert_binding_usable(binding, at=snapshot.checked_at)
    assert_grant_usable(grant, at=snapshot.checked_at)
    _same_scope(
        tenant_id=binding.tenant_id,
        project_id=binding.project_id,
        expected_tenant_id=grant.tenant_id,
        expected_project_id=grant.project_id,
        code="gateway_binding_scope_mismatch",
    )
    if (
        binding.binding_pub_id != grant.binding.binding_pub_id
        or binding.revision != grant.binding.binding_revision
        or binding.binding_hash != grant.binding.binding_hash
    ):
        _fail("gateway_binding_revision_mismatch")
    if (
        binding.binding_policy_revision != grant.binding.binding_policy_revision
        or binding.quota_policy_revision != grant.binding.quota_policy_revision
        or binding.quota_registry_id != grant.binding.quota_registry_id
        or binding.quota_registry_id != grant.quota_registry_id
        or binding.quota_scope_registry_revision != grant.quota_scope_registry_revision
        or binding.region_policy_revision != grant.binding.region_policy_revision
        or binding.route_policy_revision != grant.binding.route_policy_revision
        or _surface_route_policy_revision(binding) != grant.binding.surface_route_policy_revision
        or binding.quota_scopes != grant.binding.required_quota_scopes
        or binding.required_resource_kinds != grant.binding.required_resource_kinds
        or binding.resource_mappings != grant.binding.resource_mappings
    ):
        _fail("gateway_binding_policy_mismatch")
    if (
        binding.target.target_key != grant.config_target.target_key
        or binding.target.platform != grant.dimensions.platform
        or binding.target.collection_surface is not grant.dimensions.collection_surface
        or binding.target.product_variant != grant.dimensions.product_variant
        or binding.target.interaction_mode != grant.dimensions.interaction_mode
        or binding.target.capability_revision != grant.config_target.capability_revision
    ):
        _fail("gateway_binding_target_mismatch")
    _validate_payload_matches_binding(binding, grant.payload)

    if owner.state is not ResourceOwnerState.READY:
        _fail("resource_owner_not_ready", state=owner.state.value)
    if owner.collection_surface is not grant.dimensions.collection_surface:
        _fail("grant_surface_gateway_mismatch")
    expected_gateway_kind = {
        "provider_api": GatewayKind.PROVIDER_REQUEST,
        "consumer_web": GatewayKind.RESIDENT_BROWSER,
        "consumer_app": GatewayKind.MANAGED_APP_SESSION,
    }[grant.payload.grant_type]
    if owner.gateway_kind is not expected_gateway_kind:
        _fail("grant_surface_gateway_mismatch")
    if owner.owner_gateway_pub_id != _payload_owner_handle(grant.payload):
        _fail("grant_owner_gateway_mismatch")
    if owner.protocol_revision != grant.compatibility.gateway_protocol_revision:
        _fail("gateway_protocol_revision_mismatch")
    if snapshot.action not in grant.allowed_actions:
        _fail("grant_action_not_allowed", action=snapshot.action.value)

    _validate_current_operation(snapshot)
    _validate_current_quota(snapshot)
    _validate_current_resources(snapshot)
    main_kind = _main_owner_resource_kind(grant.payload)
    main_mappings = tuple(
        mapping
        for mapping in grant.binding.resource_mappings
        if mapping.required
        and mapping.resource_kind is main_kind
        and _main_mapping_matches_payload(mapping.resource_pub_id, grant.payload)
    )
    if len(main_mappings) != 1:
        _fail("gateway_primary_resource_mapping_mismatch")
    main_leases = tuple(
        lease
        for lease in snapshot.resource_leases
        if lease.binding_mapping_identity == main_mappings[0].identity
    )
    if len(main_leases) != 1 or main_leases[0].owner_gateway_pub_id != owner.owner_gateway_pub_id:
        _fail("gateway_does_not_own_primary_resource")

    result = SideEffectAuthorization(
        grant_pub_id=grant.grant_pub_id,
        operation_pub_id=grant.operation.operation_pub_id,
        operation_generation=grant.operation.generation,
        owner_gateway_pub_id=owner.owner_gateway_pub_id,
        action=snapshot.action,
        checked_at=snapshot.checked_at,
        quota_reservation_id=grant.quota_reservation.reservation_id,
        quota_effect_ids=tuple(effect.effect_id for effect in grant.quota_reservation.effects),
        fence_assertions=grant.resource_fences,
    )
    assert_governance_payload_safe(result)
    return result


def _replace_capacity_unit(
    pool: ResourceCapacityPoolSnapshot,
    replacement: ResourceCapacityUnitSnapshot,
) -> ResourceCapacityPoolSnapshot:
    values = pool.model_dump(mode="python")
    values["units"] = tuple(
        replacement if unit.capacity_unit == replacement.capacity_unit else unit
        for unit in pool.units
    )
    return ResourceCapacityPoolSnapshot.model_validate(values)


def acquire_resource_lease(
    pool: ResourceCapacityPoolSnapshot,
    request: ResourceLeaseAcquireRequest,
) -> ResourceLeaseAcquireResult:
    """Acquire one explicit unit without coupling unrelated resource pools.

    Persistence/owner code must serialize this compare-and-set for one pool.
    Reusing the same lease id with exactly the same immutable request is an
    idempotent replay.  Every genuine takeover increments that unit's fence;
    an old generation can therefore never become current again.
    """

    _same_scope(
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        expected_tenant_id=pool.tenant_id,
        expected_project_id=pool.project_id,
        code="resource_acquire_scope_mismatch",
    )
    mapping = request.binding_resource
    if (
        mapping.resource_registration_id != pool.resource_registration_id
        or mapping.resource_kind is not pool.resource_kind
        or mapping.resource_pub_id != pool.resource_pub_id
    ):
        _fail("resource_acquire_binding_mapping_mismatch")

    for unit in pool.units:
        lease = unit.lease
        if lease is None or lease.lease_pub_id != request.lease_pub_id:
            continue
        exact_replay = (
            lease.operation_pub_id == request.operation_pub_id
            and lease.binding_pub_id == request.binding_pub_id
            and lease.binding_revision == request.binding_revision
            and lease.binding_hash == request.binding_hash
            and lease.binding_mapping_identity == mapping.identity
            and lease.acquired_at == request.acquired_at
            and lease.expires_at == request.expires_at
            and lease.state is ResourceLeaseState.ACTIVE
            and lease.fence_generation == unit.current_fence_generation
        )
        if not exact_replay:
            _fail("resource_lease_idempotency_conflict")
        return ResourceLeaseAcquireResult(pool=pool, lease=lease, replayed=True)

    for unit in pool.units:
        lease = unit.lease
        if (
            lease is not None
            and lease.operation_pub_id == request.operation_pub_id
            and lease.state is ResourceLeaseState.ACTIVE
            and lease.expires_at > request.acquired_at
        ):
            _fail("operation_already_holds_resource")

    selected = next(
        (
            unit
            for unit in pool.units
            if not unit.quarantined
            and (
                unit.lease is None
                or unit.lease.state is not ResourceLeaseState.ACTIVE
                or unit.lease.expires_at <= request.acquired_at
            )
        ),
        None,
    )
    if selected is None:
        _fail(
            "resource_capacity_exhausted",
            resource_kind=pool.resource_kind.value,
            capacity=pool.capacity,
        )

    next_generation = selected.current_fence_generation + 1
    lease = ResourceLeaseSnapshot(
        tenant_id=pool.tenant_id,
        project_id=pool.project_id,
        operation_pub_id=request.operation_pub_id,
        binding_pub_id=request.binding_pub_id,
        binding_revision=request.binding_revision,
        binding_hash=request.binding_hash,
        resource_registration_id=pool.resource_registration_id,
        capacity_unit_id=selected.capacity_unit_id,
        resource_kind=pool.resource_kind,
        resource_pub_id=pool.resource_pub_id,
        resource_role=mapping.resource_role,
        resource_ordinal=mapping.ordinal,
        binding_resource_mapping_revision=mapping.mapping_revision,
        lease_pub_id=request.lease_pub_id,
        owner_gateway_pub_id=pool.owner_gateway_pub_id,
        fence_generation=next_generation,
        current_fence_generation=next_generation,
        capacity_unit=selected.capacity_unit,
        state=ResourceLeaseState.ACTIVE,
        acquired_at=request.acquired_at,
        expires_at=request.expires_at,
    )
    replacement = ResourceCapacityUnitSnapshot(
        capacity_unit_id=selected.capacity_unit_id,
        capacity_unit=selected.capacity_unit,
        current_fence_generation=next_generation,
        lease=lease,
    )
    updated_pool = _replace_capacity_unit(pool, replacement)
    return ResourceLeaseAcquireResult(pool=updated_pool, lease=lease)


def heartbeat_resource_lease(
    pool: ResourceCapacityPoolSnapshot,
    request: ResourceLeaseHeartbeatRequest,
) -> ResourceLeaseHeartbeatResult:
    """Specify a heartbeat CAS; stale input returns the unchanged pool.

    The persistence owner must lock or CAS the pool snapshot before committing
    this result.  This pure transition does not itself serialize concurrent
    database writers.
    """

    selected = next(
        (
            unit
            for unit in pool.units
            if unit.lease is not None and unit.lease.lease_pub_id == request.lease_pub_id
        ),
        None,
    )
    if selected is None or selected.lease is None:
        return ResourceLeaseHeartbeatResult(
            pool=pool,
            disposition=ResourceLeaseHeartbeatDisposition.STALE_LEASE,
        )
    lease = selected.lease
    if (
        request.fence_generation != selected.current_fence_generation
        or request.fence_generation != lease.fence_generation
        or lease.current_fence_generation != selected.current_fence_generation
    ):
        return ResourceLeaseHeartbeatResult(
            pool=pool,
            disposition=ResourceLeaseHeartbeatDisposition.STALE_GENERATION,
            lease=lease,
        )
    if lease.state is not ResourceLeaseState.ACTIVE:
        return ResourceLeaseHeartbeatResult(
            pool=pool,
            disposition=ResourceLeaseHeartbeatDisposition.LEASE_NOT_ACTIVE,
            lease=lease,
        )
    if lease.expires_at <= request.heartbeat_at:
        return ResourceLeaseHeartbeatResult(
            pool=pool,
            disposition=ResourceLeaseHeartbeatDisposition.LEASE_EXPIRED,
            lease=lease,
        )
    if request.extend_expires_at <= lease.expires_at:
        return ResourceLeaseHeartbeatResult(
            pool=pool,
            disposition=ResourceLeaseHeartbeatDisposition.NON_EXTENDING_EXPIRY,
            lease=lease,
        )

    lease_values = lease.model_dump(mode="python")
    lease_values["expires_at"] = request.extend_expires_at
    extended = ResourceLeaseSnapshot.model_validate(lease_values)
    replacement = ResourceCapacityUnitSnapshot(
        capacity_unit_id=selected.capacity_unit_id,
        capacity_unit=selected.capacity_unit,
        current_fence_generation=selected.current_fence_generation,
        lease=extended,
    )
    updated_pool = _replace_capacity_unit(pool, replacement)
    return ResourceLeaseHeartbeatResult(
        pool=updated_pool,
        disposition=ResourceLeaseHeartbeatDisposition.APPLIED,
        lease=extended,
    )


def replace_snapshot(
    snapshot: GatewayAuthorizationSnapshot, **changes: object
) -> GatewayAuthorizationSnapshot:
    """Validated helper for deterministic fake-gateway/fault-injection tests."""

    payload = snapshot.model_dump(mode="python")
    payload.update(changes)
    return GatewayAuthorizationSnapshot.model_validate(payload)
