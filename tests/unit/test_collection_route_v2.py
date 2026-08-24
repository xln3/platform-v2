from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from domain.collection.route_v2 import (
    CapacityReleaseStatus,
    CapacityRequest,
    CapacityRequestStatus,
    CapacityUnit,
    EffectiveHealthSource,
    ManualRouteOverride,
    OverrideApplyStatus,
    ProbeApplyStatus,
    ProbeClaim,
    ProbeObservation,
    RouteCapacityPool,
    RouteDecisionError,
    RouteHealthState,
    RouteResourceSnapshot,
    RouteSelectionRequest,
    RouteUseAction,
    apply_manual_route_override,
    apply_route_probe_observation,
    capacity_lease_token,
    claim_route_probe,
    decide_route_use,
    release_route_capacity,
    request_route_capacity,
    resolve_effective_route_health,
    select_ready_route,
)
from domain.collection.surface import SendState

NOW = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)


def _snapshot(
    *, route_resource_id: str = "route-bj-a", province_code: str = "110000"
) -> RouteResourceSnapshot:
    return RouteResourceSnapshot(
        route_resource_id=route_resource_id,
        province_code=province_code,
        route_policy_revision="route-policy-r1",
    )


def _claim(
    snapshot: RouteResourceSnapshot,
    *,
    claim_ref: str,
    claimed_at: datetime,
) -> tuple[RouteResourceSnapshot, ProbeClaim]:
    result = claim_route_probe(
        snapshot,
        claim_ref=claim_ref,
        claimed_at=claimed_at,
        expires_at=claimed_at + timedelta(minutes=10),
    )
    return result.snapshot, result.claim


def _observation(
    claim: ProbeClaim,
    *,
    observed_at: datetime,
    health_state: RouteHealthState = RouteHealthState.READY,
    observed_province_code: str | None = None,
    valid_for: timedelta = timedelta(minutes=20),
) -> ProbeObservation:
    return ProbeObservation(
        route_resource_id=claim.route_resource_id,
        province_code=claim.province_code,
        observed_province_code=observed_province_code or claim.province_code,
        generation=claim.generation,
        claim_ref=claim.claim_ref,
        health_state=health_state,
        reason_code="probe_ok" if health_state is RouteHealthState.READY else "probe_unavailable",
        observed_at=observed_at,
        valid_until=observed_at + valid_for,
    )


def _ready_snapshot(
    *, route_resource_id: str = "route-bj-a", province_code: str = "110000"
) -> RouteResourceSnapshot:
    snapshot, claim = _claim(
        _snapshot(route_resource_id=route_resource_id, province_code=province_code),
        claim_ref=f"probe-{route_resource_id}",
        claimed_at=NOW,
    )
    result = apply_route_probe_observation(
        snapshot,
        _observation(claim, observed_at=NOW + timedelta(seconds=1)),
        received_at=NOW + timedelta(seconds=2),
    )
    assert result.status is ProbeApplyStatus.APPLIED
    return result.snapshot


def _pool(
    *, route_resource_id: str, province_code: str = "110000", max_waiters: int = 8
) -> RouteCapacityPool:
    return RouteCapacityPool(
        route_resource_id=route_resource_id,
        province_code=province_code,
        max_waiters=max_waiters,
        units=(
            CapacityUnit(
                capacity_unit_ref=f"unit-{route_resource_id}",
                route_resource_id=route_resource_id,
                province_code=province_code,
            ),
        ),
    )


def _capacity_request(*, route_resource_id: str, request_ref: str, second: int) -> CapacityRequest:
    requested_at = NOW + timedelta(seconds=second)
    return CapacityRequest(
        request_ref=request_ref,
        operation_pub_id=f"operation-{request_ref}",
        route_resource_id=route_resource_id,
        province_code="110000",
        requested_at=requested_at,
        queue_expires_at=requested_at + timedelta(minutes=10),
        lease_ttl_seconds=300,
    )


def test_probe_n_is_stale_noop_when_n_plus_one_finishes_first() -> None:
    after_n, claim_n = _claim(_snapshot(), claim_ref="probe-n", claimed_at=NOW)
    after_n_plus_one, claim_n_plus_one = _claim(
        after_n,
        claim_ref="probe-n-plus-one",
        claimed_at=NOW + timedelta(seconds=1),
    )

    newest = apply_route_probe_observation(
        after_n_plus_one,
        _observation(claim_n_plus_one, observed_at=NOW + timedelta(seconds=2)),
        received_at=NOW + timedelta(seconds=3),
    )
    slow_old = apply_route_probe_observation(
        newest.snapshot,
        _observation(
            claim_n,
            observed_at=NOW + timedelta(seconds=4),
            health_state=RouteHealthState.UNAVAILABLE,
        ),
        received_at=NOW + timedelta(seconds=5),
    )

    assert claim_n_plus_one.generation == claim_n.generation + 1
    assert newest.status is ProbeApplyStatus.APPLIED
    assert slow_old.status is ProbeApplyStatus.STALE_NOOP
    assert slow_old.snapshot == newest.snapshot
    assert slow_old.snapshot.last_observation is not None
    assert slow_old.snapshot.last_observation.generation == claim_n_plus_one.generation
    assert slow_old.snapshot.last_observation.health_state is RouteHealthState.READY


def test_probe_apply_is_exact_idempotent_and_expired_claim_is_stale() -> None:
    snapshot, claim = _claim(_snapshot(), claim_ref="probe-exact", claimed_at=NOW)
    observation = _observation(claim, observed_at=NOW + timedelta(seconds=1))
    first = apply_route_probe_observation(
        snapshot,
        observation,
        received_at=NOW + timedelta(seconds=2),
    )
    replay = apply_route_probe_observation(
        first.snapshot,
        observation,
        received_at=NOW + timedelta(seconds=3),
    )
    assert first.status is ProbeApplyStatus.APPLIED
    assert replay.status is ProbeApplyStatus.ALREADY_APPLIED
    assert replay.snapshot == first.snapshot

    expired_snapshot, expired_claim = _claim(
        first.snapshot,
        claim_ref="probe-expired",
        claimed_at=NOW + timedelta(minutes=30),
    )
    expired = apply_route_probe_observation(
        expired_snapshot,
        _observation(expired_claim, observed_at=NOW + timedelta(minutes=31)),
        received_at=NOW + timedelta(minutes=41),
    )
    assert expired.status is ProbeApplyStatus.STALE_NOOP
    assert expired.snapshot == expired_snapshot


def test_manual_override_has_independent_revision_ttl_and_probe_cannot_overwrite_it() -> None:
    ready = _ready_snapshot()
    probing, claim = _claim(
        ready,
        claim_ref="probe-during-override",
        claimed_at=NOW + timedelta(minutes=1),
    )
    override = ManualRouteOverride(
        route_resource_id=probing.route_resource_id,
        province_code=probing.province_code,
        revision=7,
        override_ref="override-r7",
        health_state=RouteHealthState.UNAVAILABLE,
        reason_code="operator_quarantine",
        effective_at=NOW + timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
    )
    overridden = apply_manual_route_override(
        probing,
        override,
        received_at=NOW + timedelta(minutes=1, seconds=1),
    )

    probe_return = apply_route_probe_observation(
        overridden.snapshot,
        _observation(
            claim,
            observed_at=NOW + timedelta(minutes=2),
            valid_for=timedelta(minutes=20),
        ),
        received_at=NOW + timedelta(minutes=2, seconds=1),
    )
    during_override = resolve_effective_route_health(
        probe_return.snapshot,
        at=NOW + timedelta(minutes=3),
    )

    assert overridden.status is OverrideApplyStatus.APPLIED
    assert probe_return.status is ProbeApplyStatus.APPLIED
    assert probe_return.snapshot.manual_override == override
    assert during_override.source is EffectiveHealthSource.MANUAL_OVERRIDE
    assert during_override.source_revision == 7
    assert during_override.health_state is RouteHealthState.UNAVAILABLE

    after_expiry = resolve_effective_route_health(
        probe_return.snapshot,
        at=NOW + timedelta(minutes=6),
    )
    assert after_expiry.source is EffectiveHealthSource.PROBE
    assert after_expiry.health_state is RouteHealthState.READY

    stale_override = override.model_copy(update={"revision": 6, "override_ref": "override-r6"})
    stale_result = apply_manual_route_override(
        probe_return.snapshot,
        stale_override,
        received_at=NOW + timedelta(minutes=2, seconds=2),
    )
    assert stale_result.status is OverrideApplyStatus.STALE_NOOP
    assert stale_result.snapshot == probe_return.snapshot


def test_observed_province_mismatch_is_unavailable_and_never_rewrites_business_province() -> None:
    snapshot, claim = _claim(_snapshot(), claim_ref="probe-wrong-province", claimed_at=NOW)
    result = apply_route_probe_observation(
        snapshot,
        _observation(
            claim,
            observed_at=NOW + timedelta(seconds=1),
            observed_province_code="310000",
        ),
        received_at=NOW + timedelta(seconds=2),
    )
    assert result.status is ProbeApplyStatus.APPLIED
    assert result.snapshot.province_code == "110000"
    assert result.snapshot.last_observation is not None
    assert result.snapshot.last_observation.observed_province_code == "310000"
    assert result.snapshot.last_observation.health_state is RouteHealthState.UNAVAILABLE
    assert result.snapshot.last_observation.reason_code == "observed_province_mismatch"

    request = RouteSelectionRequest(
        operation_pub_id="operation-1",
        slot_pub_id="slot-1",
        province_code="110000",
        required_route_policy_revision="route-policy-r1",
        approved_route_resource_ids=("route-bj-a",),
    )
    with pytest.raises(RouteDecisionError, match="route_not_ready"):
        select_ready_route(request, result.snapshot, at=NOW + timedelta(seconds=3))


def test_route_selection_preserves_slot_province_and_rejects_cross_province_route() -> None:
    request = RouteSelectionRequest(
        operation_pub_id="operation-1",
        slot_pub_id="slot-1",
        province_code="110000",
        required_route_policy_revision="route-policy-r1",
        approved_route_resource_ids=("route-bj-a", "route-sh-a"),
    )
    assignment = select_ready_route(
        request,
        _ready_snapshot(),
        at=NOW + timedelta(seconds=3),
    )
    assert assignment.business_province_code == request.province_code == "110000"
    assert assignment.route_resource_id == "route-bj-a"

    with pytest.raises(RouteDecisionError, match="route_province_mismatch"):
        select_ready_route(
            request,
            _ready_snapshot(route_resource_id="route-sh-a", province_code="310000"),
            at=NOW + timedelta(seconds=3),
        )


def test_two_independent_route_resources_in_same_province_grant_concurrently() -> None:
    first = request_route_capacity(
        _pool(route_resource_id="route-bj-a"),
        _capacity_request(route_resource_id="route-bj-a", request_ref="request-a", second=0),
        at=NOW,
    )
    second = request_route_capacity(
        _pool(route_resource_id="route-bj-b"),
        _capacity_request(route_resource_id="route-bj-b", request_ref="request-b", second=0),
        at=NOW,
    )

    assert first.status is CapacityRequestStatus.GRANTED
    assert second.status is CapacityRequestStatus.GRANTED
    assert first.lease is not None and second.lease is not None
    assert first.lease.route_resource_id != second.lease.route_resource_id
    assert first.lease.province_code == second.lease.province_code == "110000"


def test_shared_capacity_one_grants_one_queues_one_and_promotes_with_new_fence() -> None:
    pool = _pool(route_resource_id="route-shared")
    first = request_route_capacity(
        pool,
        _capacity_request(route_resource_id="route-shared", request_ref="request-1", second=0),
        at=NOW,
    )
    assert first.status is CapacityRequestStatus.GRANTED
    assert first.lease is not None

    second_request = _capacity_request(
        route_resource_id="route-shared", request_ref="request-2", second=1
    )
    second = request_route_capacity(
        first.pool,
        second_request,
        at=NOW + timedelta(seconds=1),
    )
    assert second.status is CapacityRequestStatus.QUEUED
    assert second.lease is None
    assert second.queue_position == 1
    assert len([unit for unit in second.pool.units if unit.active_lease is not None]) == 1

    queue_replay = request_route_capacity(
        second.pool,
        second_request,
        at=NOW + timedelta(seconds=2),
    )
    assert queue_replay.status is CapacityRequestStatus.ALREADY_QUEUED
    assert queue_replay.pool == second.pool

    released = release_route_capacity(
        second.pool,
        capacity_lease_token(first.lease),
        at=NOW + timedelta(seconds=3),
    )
    assert released.status is CapacityReleaseStatus.RELEASED
    assert len(released.promoted_leases) == 1
    promoted = released.promoted_leases[0]
    assert promoted.request_ref == "request-2"
    assert promoted.fence_generation == first.lease.fence_generation + 1
    assert released.pool.wait_queue == ()

    stale_release = release_route_capacity(
        released.pool,
        capacity_lease_token(first.lease),
        at=NOW + timedelta(seconds=4),
    )
    assert stale_release.status is CapacityReleaseStatus.STALE_NOOP
    assert stale_release.pool == released.pool


def test_active_capacity_request_ref_replay_requires_exact_operation() -> None:
    request = _capacity_request(
        route_resource_id="route-exact", request_ref="request-exact", second=0
    )
    granted = request_route_capacity(
        _pool(route_resource_id="route-exact"),
        request,
        at=NOW,
    )
    collision = CapacityRequest(
        request_ref=request.request_ref,
        operation_pub_id="operation-other",
        route_resource_id=request.route_resource_id,
        province_code=request.province_code,
        requested_at=request.requested_at,
        queue_expires_at=request.queue_expires_at,
        lease_ttl_seconds=request.lease_ttl_seconds,
    )

    with pytest.raises(RouteDecisionError, match="capacity_request_replay_mismatch"):
        request_route_capacity(granted.pool, collision, at=NOW + timedelta(seconds=1))


def test_resource_waiter_policy_returns_local_backpressure_without_global_limit() -> None:
    constrained = _pool(route_resource_id="route-constrained", max_waiters=1)
    granted = request_route_capacity(
        constrained,
        _capacity_request(
            route_resource_id="route-constrained", request_ref="request-active", second=0
        ),
        at=NOW,
    )
    queued = request_route_capacity(
        granted.pool,
        _capacity_request(
            route_resource_id="route-constrained", request_ref="request-queued", second=1
        ),
        at=NOW + timedelta(seconds=1),
    )
    backpressured = request_route_capacity(
        queued.pool,
        _capacity_request(
            route_resource_id="route-constrained", request_ref="request-overflow", second=2
        ),
        at=NOW + timedelta(seconds=2),
    )

    assert backpressured.status is CapacityRequestStatus.BACKPRESSURE
    assert backpressured.backpressure_reason == "route_capacity_wait_queue_full"
    assert backpressured.pool == queued.pool
    assert backpressured.queue_position is None
    assert backpressured.lease is None

    independent = request_route_capacity(
        _pool(route_resource_id="route-independent", max_waiters=0),
        _capacity_request(
            route_resource_id="route-independent", request_ref="request-independent", second=2
        ),
        at=NOW + timedelta(seconds=2),
    )
    assert independent.status is CapacityRequestStatus.GRANTED
    assert independent.lease is not None


def test_invalid_route_before_send_releases_and_only_reselects_same_province() -> None:
    decision = decide_route_use(
        send_state=SendState.NOT_SENT,
        route_ready=False,
        business_province_code="110000",
    )
    assert decision.action is RouteUseAction.RELEASE_AND_RESELECT_BEFORE_SEND
    assert decision.release_capacity is True
    assert decision.allow_current_submit is False
    assert decision.allow_same_province_reselection is True
    assert decision.business_province_code == "110000"
    assert decision.no_resend is False


@pytest.mark.parametrize(
    "send_state",
    [SendState.SENDING, SendState.CONFIRMED_SENT, SendState.SEND_UNKNOWN],
)
def test_route_invalid_after_submit_boundary_requires_capture_or_reconcile_and_never_resends(
    send_state: SendState,
) -> None:
    decision = decide_route_use(
        send_state=send_state,
        route_ready=False,
        business_province_code="110000",
    )
    assert decision.action is RouteUseAction.CAPTURE_OR_RECONCILE_NO_RESEND
    assert decision.release_capacity is True
    assert decision.allow_current_submit is False
    assert decision.allow_same_province_reselection is False
    assert decision.require_reconciliation_or_capture is True
    assert decision.no_resend is True


def test_ready_route_only_allows_submit_from_not_sent() -> None:
    ready = decide_route_use(
        send_state=SendState.NOT_SENT,
        route_ready=True,
        business_province_code="110000",
    )
    sending = decide_route_use(
        send_state=SendState.SENDING,
        route_ready=True,
        business_province_code="110000",
    )
    assert ready.action is RouteUseAction.CONTINUE_CURRENT_ROUTE
    assert ready.allow_current_submit is True
    assert sending.action is RouteUseAction.CAPTURE_OR_RECONCILE_NO_RESEND
    assert sending.allow_current_submit is False
    assert sending.no_resend is True
