"""Pure route-health, probe, capacity, and send-boundary decisions.

The models in this module contain no database or network behavior.  A persistence
adapter must serialize probe claims and capacity mutations, commit them, and run
network observations only after the claim transaction has closed.  Returned
observations are applied with exact compare-and-swap semantics, so a slower old
probe can never overwrite newer route truth or a manual override.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from domain.collection.surface import SendState, validate_province_code

OpaqueId = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"),
]
ReasonCode = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._:-]{0,127}$"),
]


class RouteDecisionError(ValueError):
    """Fail-closed route protocol error with a stable, non-secret code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _aware(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RouteDecisionError(f"{field}_must_be_timezone_aware")
    return value


class FrozenRouteModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    @field_validator("*", mode="after")
    @classmethod
    def datetimes_are_aware(cls, value: object) -> object:
        if isinstance(value, datetime):
            _aware(value, field="datetime")
        return value


class RouteHealthState(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class EffectiveHealthSource(StrEnum):
    MANUAL_OVERRIDE = "manual_override"
    PROBE = "probe"
    NONE = "none"


class ProbeApplyStatus(StrEnum):
    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"
    STALE_NOOP = "stale_noop"


class OverrideApplyStatus(StrEnum):
    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"
    STALE_NOOP = "stale_noop"


class ProbeClaim(FrozenRouteModel):
    route_resource_id: OpaqueId
    province_code: str
    generation: int = Field(strict=True, ge=1)
    claim_ref: OpaqueId
    claimed_at: datetime
    expires_at: datetime

    @field_validator("province_code")
    @classmethod
    def province_is_canonical(cls, value: str) -> str:
        return validate_province_code(value)

    @model_validator(mode="after")
    def window_is_positive(self) -> Self:
        if self.expires_at <= self.claimed_at:
            raise ValueError("probe_claim_expiry_must_follow_claim")
        return self


class ProbeObservation(FrozenRouteModel):
    route_resource_id: OpaqueId
    province_code: str
    observed_province_code: str
    generation: int = Field(strict=True, ge=1)
    claim_ref: OpaqueId
    health_state: RouteHealthState
    reason_code: ReasonCode
    observed_at: datetime
    valid_until: datetime

    @field_validator("province_code", "observed_province_code")
    @classmethod
    def provinces_are_canonical(cls, value: str) -> str:
        return validate_province_code(value)

    @model_validator(mode="after")
    def validity_window_is_positive(self) -> Self:
        if self.valid_until <= self.observed_at:
            raise ValueError("probe_observation_validity_must_follow_observation")
        return self


class ManualRouteOverride(FrozenRouteModel):
    route_resource_id: OpaqueId
    province_code: str
    revision: int = Field(strict=True, ge=1)
    override_ref: OpaqueId
    health_state: RouteHealthState
    reason_code: ReasonCode
    effective_at: datetime
    expires_at: datetime

    @field_validator("province_code")
    @classmethod
    def province_is_canonical(cls, value: str) -> str:
        return validate_province_code(value)

    @model_validator(mode="after")
    def override_is_bounded(self) -> Self:
        if self.health_state is RouteHealthState.UNKNOWN:
            raise ValueError("manual_override_cannot_be_unknown")
        if self.expires_at <= self.effective_at:
            raise ValueError("manual_override_expiry_must_follow_effective_at")
        return self


class RouteResourceSnapshot(FrozenRouteModel):
    route_resource_id: OpaqueId
    province_code: str
    route_policy_revision: OpaqueId
    probe_generation: int = Field(default=0, strict=True, ge=0)
    active_probe: ProbeClaim | None = None
    last_observation: ProbeObservation | None = None
    manual_override: ManualRouteOverride | None = None

    @field_validator("province_code")
    @classmethod
    def province_is_canonical(cls, value: str) -> str:
        return validate_province_code(value)

    @model_validator(mode="after")
    def nested_truth_matches_resource(self) -> Self:
        if self.active_probe is not None:
            if (
                self.active_probe.route_resource_id != self.route_resource_id
                or self.active_probe.province_code != self.province_code
            ):
                raise ValueError("active_probe_resource_mismatch")
            if self.active_probe.generation != self.probe_generation:
                raise ValueError("active_probe_generation_mismatch")
        if self.last_observation is not None:
            if (
                self.last_observation.route_resource_id != self.route_resource_id
                or self.last_observation.province_code != self.province_code
            ):
                raise ValueError("probe_observation_resource_mismatch")
            if self.last_observation.generation > self.probe_generation:
                raise ValueError("probe_observation_generation_ahead")
        if self.manual_override is not None and (
            self.manual_override.route_resource_id != self.route_resource_id
            or self.manual_override.province_code != self.province_code
        ):
            raise ValueError("manual_override_resource_mismatch")
        return self


class ProbeClaimResult(FrozenRouteModel):
    snapshot: RouteResourceSnapshot
    claim: ProbeClaim


class ProbeApplyResult(FrozenRouteModel):
    status: ProbeApplyStatus
    snapshot: RouteResourceSnapshot


class OverrideApplyResult(FrozenRouteModel):
    status: OverrideApplyStatus
    snapshot: RouteResourceSnapshot


class EffectiveRouteHealth(FrozenRouteModel):
    route_resource_id: OpaqueId
    province_code: str
    health_state: RouteHealthState
    source: EffectiveHealthSource
    source_revision: int = Field(strict=True, ge=0)
    valid_until: datetime | None = None
    reason_code: ReasonCode

    @field_validator("province_code")
    @classmethod
    def province_is_canonical(cls, value: str) -> str:
        return validate_province_code(value)

    @property
    def ready(self) -> bool:
        return self.health_state is RouteHealthState.READY


def claim_route_probe(
    snapshot: RouteResourceSnapshot,
    *,
    claim_ref: str,
    claimed_at: datetime,
    expires_at: datetime,
) -> ProbeClaimResult:
    """Claim a new generation; the caller persists it before doing network I/O."""

    _aware(claimed_at, field="claimed_at")
    _aware(expires_at, field="expires_at")
    claim = ProbeClaim(
        route_resource_id=snapshot.route_resource_id,
        province_code=snapshot.province_code,
        generation=snapshot.probe_generation + 1,
        claim_ref=claim_ref,
        claimed_at=claimed_at,
        expires_at=expires_at,
    )
    updated = RouteResourceSnapshot.model_validate(
        {
            **snapshot.model_dump(mode="python"),
            "probe_generation": claim.generation,
            "active_probe": claim,
        }
    )
    return ProbeClaimResult(snapshot=updated, claim=claim)


def _same_observation(left: ProbeObservation, right: ProbeObservation) -> bool:
    return left == right


def _normalize_observed_province(observation: ProbeObservation) -> ProbeObservation:
    if observation.observed_province_code == observation.province_code:
        return observation
    return ProbeObservation.model_validate(
        {
            **observation.model_dump(mode="python"),
            "health_state": RouteHealthState.UNAVAILABLE,
            "reason_code": "observed_province_mismatch",
        }
    )


def apply_route_probe_observation(
    snapshot: RouteResourceSnapshot,
    observation: ProbeObservation,
    *,
    received_at: datetime,
) -> ProbeApplyResult:
    """CAS a lock-free observation, returning an explicit no-op for stale work."""

    _aware(received_at, field="received_at")
    if (
        observation.route_resource_id != snapshot.route_resource_id
        or observation.province_code != snapshot.province_code
    ):
        raise RouteDecisionError("probe_observation_resource_mismatch")
    normalized = _normalize_observed_province(observation)
    if snapshot.last_observation is not None and _same_observation(
        snapshot.last_observation, normalized
    ):
        return ProbeApplyResult(status=ProbeApplyStatus.ALREADY_APPLIED, snapshot=snapshot)
    claim = snapshot.active_probe
    exact_claim = (
        claim is not None
        and snapshot.probe_generation == observation.generation
        and claim.generation == observation.generation
        and claim.claim_ref == observation.claim_ref
    )
    observation_is_timely = (
        claim is not None
        and claim.claimed_at <= observation.observed_at <= received_at
        and received_at < claim.expires_at
        and received_at < observation.valid_until
    )
    if not exact_claim or not observation_is_timely:
        return ProbeApplyResult(status=ProbeApplyStatus.STALE_NOOP, snapshot=snapshot)
    updated = RouteResourceSnapshot.model_validate(
        {
            **snapshot.model_dump(mode="python"),
            "active_probe": None,
            "last_observation": normalized,
        }
    )
    return ProbeApplyResult(status=ProbeApplyStatus.APPLIED, snapshot=updated)


def apply_manual_route_override(
    snapshot: RouteResourceSnapshot,
    override: ManualRouteOverride,
    *,
    received_at: datetime,
) -> OverrideApplyResult:
    """Apply a separately versioned bounded override without touching probe truth."""

    _aware(received_at, field="received_at")
    if (
        override.route_resource_id != snapshot.route_resource_id
        or override.province_code != snapshot.province_code
    ):
        raise RouteDecisionError("manual_override_resource_mismatch")
    if override.effective_at > received_at:
        raise RouteDecisionError("manual_override_not_yet_effective")
    if override.expires_at <= received_at:
        raise RouteDecisionError("manual_override_expired")
    current = snapshot.manual_override
    if current == override:
        return OverrideApplyResult(status=OverrideApplyStatus.ALREADY_APPLIED, snapshot=snapshot)
    if current is not None and override.revision <= current.revision:
        return OverrideApplyResult(status=OverrideApplyStatus.STALE_NOOP, snapshot=snapshot)
    updated = RouteResourceSnapshot.model_validate(
        {**snapshot.model_dump(mode="python"), "manual_override": override}
    )
    return OverrideApplyResult(status=OverrideApplyStatus.APPLIED, snapshot=updated)


def resolve_effective_route_health(
    snapshot: RouteResourceSnapshot, *, at: datetime
) -> EffectiveRouteHealth:
    """Resolve override priority and TTL without mutating either source of truth."""

    _aware(at, field="at")
    override = snapshot.manual_override
    if override is not None and override.effective_at <= at < override.expires_at:
        return EffectiveRouteHealth(
            route_resource_id=snapshot.route_resource_id,
            province_code=snapshot.province_code,
            health_state=override.health_state,
            source=EffectiveHealthSource.MANUAL_OVERRIDE,
            source_revision=override.revision,
            valid_until=override.expires_at,
            reason_code=override.reason_code,
        )
    observation = snapshot.last_observation
    if observation is not None and observation.observed_at <= at < observation.valid_until:
        return EffectiveRouteHealth(
            route_resource_id=snapshot.route_resource_id,
            province_code=snapshot.province_code,
            health_state=observation.health_state,
            source=EffectiveHealthSource.PROBE,
            source_revision=observation.generation,
            valid_until=observation.valid_until,
            reason_code=observation.reason_code,
        )
    return EffectiveRouteHealth(
        route_resource_id=snapshot.route_resource_id,
        province_code=snapshot.province_code,
        health_state=RouteHealthState.UNKNOWN,
        source=EffectiveHealthSource.NONE,
        source_revision=0,
        valid_until=None,
        reason_code="route_health_not_current",
    )


class RouteSelectionRequest(FrozenRouteModel):
    operation_pub_id: OpaqueId
    slot_pub_id: OpaqueId
    province_code: str
    required_route_policy_revision: OpaqueId
    approved_route_resource_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=256)

    @field_validator("province_code")
    @classmethod
    def province_is_canonical(cls, value: str) -> str:
        return validate_province_code(value)

    @model_validator(mode="after")
    def approved_routes_are_unique(self) -> Self:
        if len(set(self.approved_route_resource_ids)) != len(self.approved_route_resource_ids):
            raise ValueError("approved_route_resource_ids_must_be_unique")
        return self


class RouteAssignment(FrozenRouteModel):
    operation_pub_id: OpaqueId
    slot_pub_id: OpaqueId
    business_province_code: str
    route_resource_id: OpaqueId
    route_policy_revision: OpaqueId
    health_source: EffectiveHealthSource
    health_source_revision: int = Field(strict=True, ge=1)
    health_valid_until: datetime

    @field_validator("business_province_code")
    @classmethod
    def province_is_canonical(cls, value: str) -> str:
        return validate_province_code(value)


def select_ready_route(
    request: RouteSelectionRequest,
    snapshot: RouteResourceSnapshot,
    *,
    at: datetime,
) -> RouteAssignment:
    """Select one approved ready resource without rewriting the slot province."""

    if snapshot.route_resource_id not in request.approved_route_resource_ids:
        raise RouteDecisionError("route_resource_not_approved")
    if snapshot.route_policy_revision != request.required_route_policy_revision:
        raise RouteDecisionError("route_policy_revision_mismatch")
    if snapshot.province_code != request.province_code:
        raise RouteDecisionError("route_province_mismatch")
    effective = resolve_effective_route_health(snapshot, at=at)
    if not effective.ready or effective.valid_until is None or effective.source_revision < 1:
        raise RouteDecisionError("route_not_ready")
    return RouteAssignment(
        operation_pub_id=request.operation_pub_id,
        slot_pub_id=request.slot_pub_id,
        business_province_code=request.province_code,
        route_resource_id=snapshot.route_resource_id,
        route_policy_revision=snapshot.route_policy_revision,
        health_source=effective.source,
        health_source_revision=effective.source_revision,
        health_valid_until=effective.valid_until,
    )


class CapacityRequestStatus(StrEnum):
    GRANTED = "granted"
    ALREADY_GRANTED = "already_granted"
    QUEUED = "queued"
    ALREADY_QUEUED = "already_queued"
    BACKPRESSURE = "backpressure"


class CapacityReleaseStatus(StrEnum):
    RELEASED = "released"
    STALE_NOOP = "stale_noop"


class CapacityLease(FrozenRouteModel):
    lease_ref: OpaqueId
    request_ref: OpaqueId
    operation_pub_id: OpaqueId
    route_resource_id: OpaqueId
    province_code: str
    capacity_unit_ref: OpaqueId
    fence_generation: int = Field(strict=True, ge=1)
    acquired_at: datetime
    expires_at: datetime

    @field_validator("province_code")
    @classmethod
    def province_is_canonical(cls, value: str) -> str:
        return validate_province_code(value)

    @model_validator(mode="after")
    def lease_window_is_positive(self) -> Self:
        if self.expires_at <= self.acquired_at:
            raise ValueError("capacity_lease_expiry_must_follow_acquisition")
        return self


class CapacityLeaseToken(FrozenRouteModel):
    lease_ref: OpaqueId
    capacity_unit_ref: OpaqueId
    fence_generation: int = Field(strict=True, ge=1)


class CapacityUnit(FrozenRouteModel):
    capacity_unit_ref: OpaqueId
    route_resource_id: OpaqueId
    province_code: str
    fence_generation: int = Field(default=0, strict=True, ge=0)
    active_lease: CapacityLease | None = None

    @field_validator("province_code")
    @classmethod
    def province_is_canonical(cls, value: str) -> str:
        return validate_province_code(value)

    @model_validator(mode="after")
    def lease_matches_unit(self) -> Self:
        if self.active_lease is None:
            return self
        lease = self.active_lease
        if (
            lease.capacity_unit_ref != self.capacity_unit_ref
            or lease.route_resource_id != self.route_resource_id
            or lease.province_code != self.province_code
        ):
            raise ValueError("capacity_lease_unit_mismatch")
        if lease.fence_generation != self.fence_generation:
            raise ValueError("capacity_lease_fence_mismatch")
        return self


class CapacityWaiter(FrozenRouteModel):
    request_ref: OpaqueId
    operation_pub_id: OpaqueId
    route_resource_id: OpaqueId
    province_code: str
    enqueued_at: datetime
    queue_expires_at: datetime
    lease_ttl_seconds: int = Field(strict=True, ge=1, le=86_400)

    @field_validator("province_code")
    @classmethod
    def province_is_canonical(cls, value: str) -> str:
        return validate_province_code(value)

    @model_validator(mode="after")
    def queue_window_is_positive(self) -> Self:
        if self.queue_expires_at <= self.enqueued_at:
            raise ValueError("capacity_queue_expiry_must_follow_enqueue")
        return self


class RouteCapacityPool(FrozenRouteModel):
    route_resource_id: OpaqueId
    province_code: str
    max_waiters: int = Field(strict=True, ge=0)
    units: tuple[CapacityUnit, ...] = Field(min_length=1, max_length=1024)
    wait_queue: tuple[CapacityWaiter, ...] = ()

    @field_validator("province_code")
    @classmethod
    def province_is_canonical(cls, value: str) -> str:
        return validate_province_code(value)

    @model_validator(mode="after")
    def capacity_state_is_canonical(self) -> Self:
        unit_refs = tuple(unit.capacity_unit_ref for unit in self.units)
        if tuple(sorted(unit_refs)) != unit_refs or len(set(unit_refs)) != len(unit_refs):
            raise ValueError("capacity_units_must_be_unique_and_sorted")
        for unit in self.units:
            if (
                unit.route_resource_id != self.route_resource_id
                or unit.province_code != self.province_code
            ):
                raise ValueError("capacity_unit_pool_mismatch")
        queue_keys = tuple((waiter.enqueued_at, waiter.request_ref) for waiter in self.wait_queue)
        if tuple(sorted(queue_keys)) != queue_keys:
            raise ValueError("capacity_wait_queue_must_be_ordered")
        for waiter in self.wait_queue:
            if (
                waiter.route_resource_id != self.route_resource_id
                or waiter.province_code != self.province_code
            ):
                raise ValueError("capacity_waiter_pool_mismatch")
        active_request_refs = {
            unit.active_lease.request_ref for unit in self.units if unit.active_lease is not None
        }
        queued_request_refs = [waiter.request_ref for waiter in self.wait_queue]
        if len(self.wait_queue) > self.max_waiters:
            raise ValueError("capacity_wait_queue_exceeds_resource_policy")
        if len(set(queued_request_refs)) != len(queued_request_refs):
            raise ValueError("capacity_wait_queue_request_must_be_unique")
        if active_request_refs.intersection(queued_request_refs):
            raise ValueError("capacity_request_cannot_be_active_and_queued")
        return self


class CapacityRequest(FrozenRouteModel):
    request_ref: OpaqueId
    operation_pub_id: OpaqueId
    route_resource_id: OpaqueId
    province_code: str
    requested_at: datetime
    queue_expires_at: datetime
    lease_ttl_seconds: int = Field(strict=True, ge=1, le=86_400)

    @field_validator("province_code")
    @classmethod
    def province_is_canonical(cls, value: str) -> str:
        return validate_province_code(value)

    @model_validator(mode="after")
    def queue_window_is_positive(self) -> Self:
        if self.queue_expires_at <= self.requested_at:
            raise ValueError("capacity_queue_expiry_must_follow_request")
        return self


class CapacityRequestResult(FrozenRouteModel):
    status: CapacityRequestStatus
    pool: RouteCapacityPool
    lease: CapacityLease | None = None
    queue_position: int | None = Field(default=None, strict=True, ge=1)
    backpressure_reason: ReasonCode | None = None

    @model_validator(mode="after")
    def result_shape_matches_status(self) -> Self:
        granted = self.status in {
            CapacityRequestStatus.GRANTED,
            CapacityRequestStatus.ALREADY_GRANTED,
        }
        if granted != (self.lease is not None):
            raise ValueError("capacity_request_result_lease_shape_mismatch")
        queued = self.status in {
            CapacityRequestStatus.QUEUED,
            CapacityRequestStatus.ALREADY_QUEUED,
        }
        if queued != (self.queue_position is not None):
            raise ValueError("capacity_request_result_queue_shape_mismatch")
        backpressured = self.status is CapacityRequestStatus.BACKPRESSURE
        if backpressured != (self.backpressure_reason is not None):
            raise ValueError("capacity_request_result_backpressure_shape_mismatch")
        return self


class CapacityReleaseResult(FrozenRouteModel):
    status: CapacityReleaseStatus
    pool: RouteCapacityPool
    promoted_leases: tuple[CapacityLease, ...] = ()


def capacity_lease_token(lease: CapacityLease) -> CapacityLeaseToken:
    return CapacityLeaseToken(
        lease_ref=lease.lease_ref,
        capacity_unit_ref=lease.capacity_unit_ref,
        fence_generation=lease.fence_generation,
    )


def _capacity_lease_ref(
    *, route_resource_id: str, capacity_unit_ref: str, request_ref: str, generation: int
) -> str:
    material = "|".join(
        (
            "route-capacity-lease-v1",
            route_resource_id,
            capacity_unit_ref,
            request_ref,
            str(generation),
        )
    )
    return f"rcl-{sha256(material.encode()).hexdigest()}"


def _lease_for_waiter(
    unit: CapacityUnit, waiter: CapacityWaiter, *, acquired_at: datetime
) -> CapacityLease:
    generation = unit.fence_generation + 1
    return CapacityLease(
        lease_ref=_capacity_lease_ref(
            route_resource_id=unit.route_resource_id,
            capacity_unit_ref=unit.capacity_unit_ref,
            request_ref=waiter.request_ref,
            generation=generation,
        ),
        request_ref=waiter.request_ref,
        operation_pub_id=waiter.operation_pub_id,
        route_resource_id=unit.route_resource_id,
        province_code=unit.province_code,
        capacity_unit_ref=unit.capacity_unit_ref,
        fence_generation=generation,
        acquired_at=acquired_at,
        expires_at=acquired_at + timedelta(seconds=waiter.lease_ttl_seconds),
    )


def _advance_capacity(pool: RouteCapacityPool, *, at: datetime) -> RouteCapacityPool:
    """Expire old leases/waiters and promote the stable FIFO queue."""

    units = [
        CapacityUnit.model_validate(
            {
                **unit.model_dump(mode="python"),
                "active_lease": (
                    unit.active_lease
                    if unit.active_lease is not None and unit.active_lease.expires_at > at
                    else None
                ),
            }
        )
        for unit in pool.units
    ]
    active_requests = {
        unit.active_lease.request_ref for unit in units if unit.active_lease is not None
    }
    waiters = [
        waiter
        for waiter in pool.wait_queue
        if waiter.queue_expires_at > at and waiter.request_ref not in active_requests
    ]
    for index, unit in enumerate(units):
        if unit.active_lease is not None or not waiters:
            continue
        waiter = waiters.pop(0)
        lease = _lease_for_waiter(unit, waiter, acquired_at=at)
        units[index] = CapacityUnit.model_validate(
            {
                **unit.model_dump(mode="python"),
                "fence_generation": lease.fence_generation,
                "active_lease": lease,
            }
        )
    return RouteCapacityPool(
        route_resource_id=pool.route_resource_id,
        province_code=pool.province_code,
        max_waiters=pool.max_waiters,
        units=tuple(units),
        wait_queue=tuple(waiters),
    )


def request_route_capacity(
    pool: RouteCapacityPool,
    request: CapacityRequest,
    *,
    at: datetime,
) -> CapacityRequestResult:
    """Grant one resource-local unit or queue without a province-wide mutex."""

    _aware(at, field="at")
    if (
        request.route_resource_id != pool.route_resource_id
        or request.province_code != pool.province_code
    ):
        raise RouteDecisionError("capacity_request_pool_mismatch")
    if request.requested_at > at:
        raise RouteDecisionError("capacity_request_from_future")
    if request.queue_expires_at <= at:
        raise RouteDecisionError("capacity_request_expired")
    advanced = _advance_capacity(pool, at=at)
    for unit in advanced.units:
        lease = unit.active_lease
        if lease is not None and lease.request_ref == request.request_ref:
            if lease.operation_pub_id != request.operation_pub_id:
                raise RouteDecisionError("capacity_request_replay_mismatch")
            return CapacityRequestResult(
                status=CapacityRequestStatus.ALREADY_GRANTED,
                pool=advanced,
                lease=lease,
            )
    for index, waiter in enumerate(advanced.wait_queue, 1):
        if waiter.request_ref == request.request_ref:
            if waiter.operation_pub_id != request.operation_pub_id:
                raise RouteDecisionError("capacity_request_replay_mismatch")
            return CapacityRequestResult(
                status=CapacityRequestStatus.ALREADY_QUEUED,
                pool=advanced,
                queue_position=index,
            )
    if len(advanced.wait_queue) >= advanced.max_waiters and all(
        unit.active_lease is not None for unit in advanced.units
    ):
        return CapacityRequestResult(
            status=CapacityRequestStatus.BACKPRESSURE,
            pool=advanced,
            backpressure_reason="route_capacity_wait_queue_full",
        )
    waiter = CapacityWaiter(
        request_ref=request.request_ref,
        operation_pub_id=request.operation_pub_id,
        route_resource_id=request.route_resource_id,
        province_code=request.province_code,
        enqueued_at=request.requested_at,
        queue_expires_at=request.queue_expires_at,
        lease_ttl_seconds=request.lease_ttl_seconds,
    )
    for index, unit in enumerate(advanced.units):
        if unit.active_lease is not None:
            continue
        lease = _lease_for_waiter(unit, waiter, acquired_at=at)
        units = list(advanced.units)
        units[index] = CapacityUnit.model_validate(
            {
                **unit.model_dump(mode="python"),
                "fence_generation": lease.fence_generation,
                "active_lease": lease,
            }
        )
        granted = RouteCapacityPool(
            route_resource_id=advanced.route_resource_id,
            province_code=advanced.province_code,
            max_waiters=advanced.max_waiters,
            units=tuple(units),
            wait_queue=advanced.wait_queue,
        )
        return CapacityRequestResult(
            status=CapacityRequestStatus.GRANTED,
            pool=granted,
            lease=lease,
        )
    queued = RouteCapacityPool(
        route_resource_id=advanced.route_resource_id,
        province_code=advanced.province_code,
        max_waiters=advanced.max_waiters,
        units=advanced.units,
        wait_queue=tuple(
            sorted(
                (*advanced.wait_queue, waiter),
                key=lambda item: (item.enqueued_at, item.request_ref),
            )
        ),
    )
    promoted = _advance_capacity(queued, at=at)
    for unit in promoted.units:
        lease = unit.active_lease
        if lease is not None and lease.request_ref == request.request_ref:
            return CapacityRequestResult(
                status=CapacityRequestStatus.GRANTED,
                pool=promoted,
                lease=lease,
            )
    position = next(
        index
        for index, queued_waiter in enumerate(promoted.wait_queue, 1)
        if queued_waiter.request_ref == request.request_ref
    )
    return CapacityRequestResult(
        status=CapacityRequestStatus.QUEUED,
        pool=promoted,
        queue_position=position,
    )


def release_route_capacity(
    pool: RouteCapacityPool,
    token: CapacityLeaseToken,
    *,
    at: datetime,
) -> CapacityReleaseResult:
    """Release an exact fenced lease and promote queued work in FIFO order."""

    _aware(at, field="at")
    advanced = _advance_capacity(pool, at=at)
    before_leases = {
        unit.active_lease.lease_ref for unit in advanced.units if unit.active_lease is not None
    }
    matched = False
    units: list[CapacityUnit] = []
    for unit in advanced.units:
        lease = unit.active_lease
        if (
            lease is not None
            and lease.lease_ref == token.lease_ref
            and unit.capacity_unit_ref == token.capacity_unit_ref
            and unit.fence_generation == token.fence_generation
        ):
            matched = True
            units.append(
                CapacityUnit.model_validate(
                    {**unit.model_dump(mode="python"), "active_lease": None}
                )
            )
        else:
            units.append(unit)
    if not matched:
        return CapacityReleaseResult(status=CapacityReleaseStatus.STALE_NOOP, pool=advanced)
    released = RouteCapacityPool(
        route_resource_id=advanced.route_resource_id,
        province_code=advanced.province_code,
        max_waiters=advanced.max_waiters,
        units=tuple(units),
        wait_queue=advanced.wait_queue,
    )
    promoted = _advance_capacity(released, at=at)
    promoted_leases = tuple(
        unit.active_lease
        for unit in promoted.units
        if unit.active_lease is not None and unit.active_lease.lease_ref not in before_leases
    )
    return CapacityReleaseResult(
        status=CapacityReleaseStatus.RELEASED,
        pool=promoted,
        promoted_leases=promoted_leases,
    )


class RouteUseAction(StrEnum):
    CONTINUE_CURRENT_ROUTE = "continue_current_route"
    RELEASE_AND_RESELECT_BEFORE_SEND = "release_and_reselect_before_send"
    RELEASE_TERMINAL_NOT_SENT = "release_terminal_not_sent"
    CAPTURE_OR_RECONCILE_NO_RESEND = "capture_or_reconcile_no_resend"


class RouteUseDecision(FrozenRouteModel):
    action: RouteUseAction
    business_province_code: str
    release_capacity: bool
    allow_current_submit: bool
    allow_same_province_reselection: bool
    require_reconciliation_or_capture: bool
    no_resend: bool

    @field_validator("business_province_code")
    @classmethod
    def province_is_canonical(cls, value: str) -> str:
        return validate_province_code(value)


def decide_route_use(
    *, send_state: SendState, route_ready: bool, business_province_code: str
) -> RouteUseDecision:
    """Separate pre-submit route replacement from post-boundary no-resend truth."""

    province = validate_province_code(business_province_code)
    if route_ready and send_state is SendState.NOT_SENT:
        return RouteUseDecision(
            action=RouteUseAction.CONTINUE_CURRENT_ROUTE,
            business_province_code=province,
            release_capacity=False,
            allow_current_submit=True,
            allow_same_province_reselection=False,
            require_reconciliation_or_capture=False,
            no_resend=False,
        )
    if not route_ready and send_state is SendState.NOT_SENT:
        return RouteUseDecision(
            action=RouteUseAction.RELEASE_AND_RESELECT_BEFORE_SEND,
            business_province_code=province,
            release_capacity=True,
            allow_current_submit=False,
            allow_same_province_reselection=True,
            require_reconciliation_or_capture=False,
            no_resend=False,
        )
    if send_state is SendState.CONFIRMED_NOT_SENT:
        return RouteUseDecision(
            action=RouteUseAction.RELEASE_TERMINAL_NOT_SENT,
            business_province_code=province,
            release_capacity=True,
            allow_current_submit=False,
            allow_same_province_reselection=False,
            require_reconciliation_or_capture=False,
            no_resend=True,
        )
    return RouteUseDecision(
        action=RouteUseAction.CAPTURE_OR_RECONCILE_NO_RESEND,
        business_province_code=province,
        release_capacity=not route_ready,
        allow_current_submit=False,
        allow_same_province_reselection=False,
        require_reconciliation_or_capture=True,
        no_resend=True,
    )
