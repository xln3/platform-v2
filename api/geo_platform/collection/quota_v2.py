"""Atomic PostgreSQL quota reservations for collection-v2 submission operations.

The production entry points in this module deliberately accept only durable IDs.
Limits and scope declarations are loaded from the authoritative binding/registry
tables in the same transaction; callers cannot supply a smaller or more generous
scope set.  All SQL identifiers are constants and all values are parameters.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Protocol
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from domain.collection.surface import (
    QUOTA_SCOPE_LOCK_ORDER_VERSION,
    CollectionSurface,
    QuotaScopeDeclaration,
    QuotaScopeKind,
    QuotaWindowPolicy,
    QuotaWindowUnit,
    SendState,
    canonical_quota_lock_order,
    transition_send_state,
)

QUOTA_PROTOCOL_VERSION = "collection-quota-postgres-v1"
BUCKET_KEY_VERSION = "collection-quota-bucket-v1"
RESERVATION_SET_VERSION = "collection-quota-reservation-set-v1"
_REASON_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,119}$")
_OWNER_PROOF_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# SQL is intentionally centralized: table/column names never depend on input.
SET_TENANT_SQL = "SELECT set_config('app.tenant_id', CAST(%(tenant_id)s AS text), true)"

RECORD_NOT_SENT_PROOF_SQL = """
SELECT platform.record_collection_not_sent_proof_v2(
  %(tenant_id)s,
  %(project_id)s,
  %(operation_id)s,
  %(owner_gateway_revision)s,
  %(owner_evidence_ref)s,
  %(evidence_hash)s,
  %(reason_code)s
)
"""

LOAD_OPERATION_AND_BINDING_SQL = """
SELECT op.send_state,
       op.send_state_version,
       op.operation_key,
       op.prepared_at,
       op.reconcile_after,
       op.platform,
       op.collection_surface,
       op.product_variant,
       op.interaction_mode,
       binding.quota_registry_revision,
       binding.quota_policy_revision
FROM platform.collection_submission_operation AS op
JOIN platform.collection_binding_revision_v2 AS binding
  ON binding.id = %(binding_id)s
 AND binding.tenant_id = op.tenant_id
 AND binding.project_id = op.project_id
 AND binding.platform = op.platform
 AND binding.collection_surface = op.collection_surface
 AND binding.product_variant = op.product_variant
WHERE op.id = %(operation_id)s
  AND op.tenant_id = %(tenant_id)s
  AND op.project_id = %(project_id)s
  AND binding.quota_registry_id = %(registry_id)s
  AND binding.lifecycle_state = 'active'
  AND binding.activated_at IS NOT NULL
  AND binding.effective_from <= CURRENT_TIMESTAMP
  AND (binding.expires_at IS NULL OR binding.expires_at > CURRENT_TIMESTAMP)
  AND binding.suspended_at IS NULL
  AND binding.revoked_at IS NULL
  AND binding.superseded_at IS NULL
  AND EXISTS (
      SELECT 1
      FROM platform.collection_binding_capability AS capability
      WHERE capability.binding_revision_id = binding.id
        AND capability.tenant_id = binding.tenant_id
        AND capability.project_id = binding.project_id
        AND capability.interaction_mode = op.interaction_mode
        AND capability.requirement_state = 'required'
  )
FOR UPDATE OF op
"""

LOAD_OPERATION_SQL = """
SELECT send_state, send_state_version, operation_key, prepared_at, reconcile_after,
       platform, collection_surface, product_variant, interaction_mode
FROM platform.collection_submission_operation
WHERE id = %(operation_id)s
  AND tenant_id = %(tenant_id)s
  AND project_id = %(project_id)s
FOR UPDATE
"""

LOAD_AUTHORITATIVE_SCOPES_SQL = """
SELECT registry.registry_revision,
       registry.lock_order_version,
       policy.id,
       policy.schema_version,
       policy.scope_policy_key,
       policy.selector_key,
       policy.policy_revision,
       policy.scope_kind,
       policy.scope_subject_id,
       policy.platform,
       policy.collection_surface,
       policy.product_variant,
       policy.interaction_mode,
       policy.window_schema_version,
       policy.window_unit,
       policy.window_size,
       policy.window_timezone,
       policy.window_boundary_revision,
       policy.provider_window_code,
       policy.limit_units,
       policy.lock_order_ordinal,
       binding_scope.quota_units,
       binding_scope.ordinal,
       binding_scope.scope_policy_key,
       binding_scope.scope_kind,
       binding_scope.scope_subject_id,
       binding_scope.policy_revision
FROM platform.collection_quota_registry_revision AS registry
JOIN platform.collection_binding_quota_scope AS binding_scope
  ON binding_scope.quota_registry_id = registry.id
 AND binding_scope.tenant_id = registry.tenant_id
 AND binding_scope.project_id = registry.project_id
JOIN platform.collection_binding_revision_v2 AS binding
  ON binding.id = binding_scope.binding_revision_id
 AND binding.tenant_id = binding_scope.tenant_id
 AND binding.project_id = binding_scope.project_id
 AND binding.quota_registry_id = registry.id
JOIN platform.collection_quota_scope_policy AS policy
  ON policy.id = binding_scope.quota_scope_policy_id
 AND policy.registry_revision_id = registry.id
 AND policy.tenant_id = registry.tenant_id
 AND policy.project_id = registry.project_id
WHERE registry.id = %(registry_id)s
  AND registry.tenant_id = %(tenant_id)s
  AND registry.project_id = %(project_id)s
  AND registry.lifecycle_state = 'active'
  AND binding.id = %(binding_id)s
  AND binding.lifecycle_state = 'active'
  AND binding.activated_at IS NOT NULL
  AND binding.effective_from <= CURRENT_TIMESTAMP
  AND (binding.expires_at IS NULL OR binding.expires_at > CURRENT_TIMESTAMP)
  AND binding.suspended_at IS NULL
  AND binding.revoked_at IS NULL
  AND binding.superseded_at IS NULL
ORDER BY policy.lock_order_ordinal, policy.scope_policy_key
"""

LOAD_RESERVATION_SQL = """
SELECT id, reservation_state, requested_units, expected_effect_count, effect_set_hash,
       binding_revision_id, quota_registry_id
FROM platform.collection_quota_reservation
WHERE tenant_id = %(tenant_id)s
  AND project_id = %(project_id)s
  AND operation_id = %(operation_id)s
FOR UPDATE
"""

LOAD_RESERVATION_EFFECTS_SQL = """
SELECT effect.id,
       effect.quota_bucket_id,
       effect.quota_scope_policy_id,
       effect.units,
       effect.effect_state,
       bucket.bucket_hash,
       bucket.bucket_key,
       bucket.scope_kind
FROM platform.collection_quota_reservation_effect AS effect
JOIN platform.collection_quota_bucket AS bucket
  ON bucket.id = effect.quota_bucket_id
 AND bucket.tenant_id = effect.tenant_id
 AND bucket.project_id = effect.project_id
WHERE effect.tenant_id = %(tenant_id)s
  AND effect.project_id = %(project_id)s
  AND effect.reservation_id = %(reservation_id)s
ORDER BY CASE bucket.scope_kind
           WHEN 'provider' THEN 0
           WHEN 'account' THEN 1
           WHEN 'credential' THEN 2
           WHEN 'project' THEN 3
           WHEN 'contract' THEN 4
           WHEN 'platform_surface' THEN 5
           WHEN 'mode' THEN 6
           ELSE 2147483647
         END,
         bucket.bucket_key
"""

ADVISORY_BUCKET_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended(%(bucket_key)s, 0))"

INSERT_BUCKET_SQL = """
INSERT INTO platform.collection_quota_bucket
  (id, pub_id, tenant_id, project_id, registry_revision_id,
   quota_scope_policy_id, scope_policy_key, scope_kind, scope_subject_id,
   policy_revision, bucket_key, bucket_hash, window_start, window_end,
   limit_units, reserved_units, settled_consumed_units, settled_unknown_units,
   bucket_state, fence_version)
VALUES
  (%(id)s, %(pub_id)s, %(tenant_id)s, %(project_id)s, %(registry_id)s,
   %(scope_policy_id)s, %(scope_policy_key)s, %(scope_kind)s, %(scope_subject_id)s,
   %(policy_revision)s, %(bucket_key)s, %(bucket_hash)s, %(window_start)s, %(window_end)s,
   %(limit_units)s, 0, 0, 0, 'open', 1)
ON CONFLICT DO NOTHING
"""

LOCK_BUCKET_SQL = """
SELECT id, quota_scope_policy_id, bucket_hash, window_start, window_end,
       limit_units, reserved_units, settled_consumed_units, settled_unknown_units,
       bucket_state
FROM platform.collection_quota_bucket
WHERE tenant_id = %(tenant_id)s
  AND project_id = %(project_id)s
  AND bucket_hash = %(bucket_hash)s
  AND bucket_key = %(bucket_key)s
FOR UPDATE
"""

INSERT_RESERVATION_SQL = """
INSERT INTO platform.collection_quota_reservation
  (id, pub_id, tenant_id, project_id, operation_id, binding_revision_id,
   quota_registry_id, reservation_key, idempotency_key, reservation_state,
   requested_units, expected_effect_count, effect_set_hash, reserved_at,
   reconcile_after, state_reason)
VALUES
  (%(id)s, %(pub_id)s, %(tenant_id)s, %(project_id)s, %(operation_id)s,
   %(binding_id)s, %(registry_id)s, %(reservation_key)s, %(idempotency_key)s,
   'preparing', %(requested_units)s, %(expected_effect_count)s,
   %(effect_set_hash)s, NULL, %(reconcile_after)s,
   'atomic_reserve_v1')
RETURNING id
"""

INSERT_RESERVATION_EFFECT_SQL = """
INSERT INTO platform.collection_quota_reservation_effect
  (id, pub_id, tenant_id, project_id, reservation_id, operation_id,
   quota_bucket_id, quota_scope_policy_id, effect_key, units, effect_state,
   state_reason, reserved_at)
VALUES
  (%(id)s, %(pub_id)s, %(tenant_id)s, %(project_id)s, %(reservation_id)s,
   %(operation_id)s, %(bucket_id)s, %(scope_policy_id)s, %(effect_key)s,
   %(units)s, 'reserved', 'atomic_reserve_v1', CURRENT_TIMESTAMP)
RETURNING id
"""

INCREMENT_BUCKET_RESERVED_SQL = """
UPDATE platform.collection_quota_bucket
SET reserved_units = reserved_units + %(units)s,
    fence_version = fence_version + 1,
    version = version + 1,
    updated_at = CURRENT_TIMESTAMP
WHERE id = %(bucket_id)s
  AND tenant_id = %(tenant_id)s
  AND project_id = %(project_id)s
  AND reserved_units + settled_consumed_units + settled_unknown_units + %(units)s
      <= limit_units
RETURNING id
"""

INSERT_LEDGER_EVENT_SQL = """
INSERT INTO platform.collection_quota_ledger_event
  (id, pub_id, tenant_id, project_id, reservation_effect_id, reservation_id,
   operation_id, quota_bucket_id, quota_scope_policy_id, event_key,
   idempotency_key, effect_kind, from_state, to_state, units, reason_code,
   actor_pub_id, occurred_at)
VALUES
  (%(id)s, %(pub_id)s, %(tenant_id)s, %(project_id)s, %(effect_id)s,
   %(reservation_id)s, %(operation_id)s, %(bucket_id)s, %(scope_policy_id)s,
   %(event_key)s, %(idempotency_key)s, %(effect_kind)s, %(from_state)s,
   %(to_state)s, %(units)s, %(reason_code)s, 'collection-quota-v2',
   CURRENT_TIMESTAMP)
RETURNING id
"""

MARK_RESERVATION_RESERVED_SQL = """
UPDATE platform.collection_quota_reservation
SET reservation_state = 'reserved',
    reserved_at = CURRENT_TIMESTAMP,
    version = version + 1,
    updated_at = CURRENT_TIMESTAMP
WHERE id = %(reservation_id)s
  AND tenant_id = %(tenant_id)s
  AND project_id = %(project_id)s
  AND reservation_state = 'preparing'
RETURNING id
"""

UPDATE_OPERATION_SEND_STATE_SQL = """
UPDATE platform.collection_submission_operation
SET send_state = CAST(%(target_send_state)s AS varchar(40)),
    send_state_version = send_state_version + 1,
    send_resolved_at = CURRENT_TIMESTAMP,
    reconciliation_state = CASE
      WHEN CAST(%(target_send_state)s AS varchar(40)) = 'SEND_UNKNOWN' THEN 'pending'
      ELSE 'resolved'
    END,
    state_reason = %(reason_code)s,
    version = version + 1,
    updated_at = CURRENT_TIMESTAMP
WHERE id = %(operation_id)s
  AND tenant_id = %(tenant_id)s
  AND project_id = %(project_id)s
  AND send_state = %(current_send_state)s
  AND send_state_version = %(send_state_version)s
RETURNING id
"""

LOCK_BUCKET_BY_ID_SQL = """
SELECT id, quota_scope_policy_id, bucket_hash, window_start, window_end,
       limit_units, reserved_units, settled_consumed_units, settled_unknown_units,
       bucket_state
FROM platform.collection_quota_bucket
WHERE id = %(bucket_id)s
  AND tenant_id = %(tenant_id)s
  AND project_id = %(project_id)s
FOR UPDATE
"""

UPDATE_EFFECT_TERMINAL_SQL = """
UPDATE platform.collection_quota_reservation_effect
SET effect_state = CAST(%(effect_state)s AS varchar(40)),
    state_reason = %(reason_code)s,
    settled_at = CASE
      WHEN CAST(%(effect_state)s AS varchar(40)) IN
           ('settled_consumed','settled_unknown')
                      THEN CURRENT_TIMESTAMP ELSE settled_at END,
    released_at = CASE WHEN CAST(%(effect_state)s AS varchar(40)) = 'released'
                       THEN CURRENT_TIMESTAMP ELSE released_at END,
    version = version + 1,
    updated_at = CURRENT_TIMESTAMP
WHERE id = %(effect_id)s
  AND tenant_id = %(tenant_id)s
  AND project_id = %(project_id)s
  AND effect_state = 'reserved'
RETURNING id
"""

SETTLE_BUCKET_SQL = """
UPDATE platform.collection_quota_bucket
SET reserved_units = reserved_units - %(units)s,
    settled_consumed_units = settled_consumed_units + %(consumed_delta)s,
    settled_unknown_units = settled_unknown_units + %(unknown_delta)s,
    fence_version = fence_version + 1,
    version = version + 1,
    updated_at = CURRENT_TIMESTAMP
WHERE id = %(bucket_id)s
  AND tenant_id = %(tenant_id)s
  AND project_id = %(project_id)s
  AND reserved_units >= %(units)s
RETURNING id
"""

MARK_RESERVATION_TERMINAL_SQL = """
UPDATE platform.collection_quota_reservation
SET reservation_state = %(reservation_state)s,
    finalized_at = CURRENT_TIMESTAMP,
    state_reason = %(reason_code)s,
    version = version + 1,
    updated_at = CURRENT_TIMESTAMP
WHERE id = %(reservation_id)s
  AND tenant_id = %(tenant_id)s
  AND project_id = %(project_id)s
  AND reservation_state IN ('reserved','reconciling')
RETURNING id
"""


class QuotaV2Error(RuntimeError):
    """Fail-closed error containing only a stable code and non-secret context."""

    def __init__(self, code: str, **context: str | int | bool | None) -> None:
        self.code = code
        self.context = dict(sorted(context.items()))
        super().__init__(code)

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "context": dict(self.context)}


class ReservationDisposition(StrEnum):
    RESERVED = "reserved"
    CONSUMED = "settled_consumed"
    UNKNOWN_CONSUMED = "settled_unknown"
    RELEASED = "released"


class LedgerEffectKind(StrEnum):
    RESERVE = "reserve"
    SETTLE_CONSUMED = "settle_consumed"
    SETTLE_UNKNOWN = "settle_unknown"
    RELEASE = "release"


class OwnerEvidence(StrEnum):
    """Evidence produced by the unique resource owner, never inferred from age."""

    NONE = "none"
    PROVED_NOT_SENT = "proved_not_sent"
    PROVED_SENT = "proved_sent"
    ACKNOWLEDGEMENT_UNKNOWN = "acknowledgement_unknown"


class ReconciliationAction(StrEnum):
    DEFER = "defer"
    RELEASE = "release"
    SETTLE_CONSUMED = "settle_consumed"
    SETTLE_UNKNOWN = "settle_unknown"


class CursorProtocol(Protocol):
    def fetchone(self) -> Sequence[object] | None: ...

    def fetchall(self) -> Sequence[Sequence[object]]: ...


class ConnectionProtocol(Protocol):
    def execute(
        self,
        query: str,
        params: Mapping[str, object] | None = None,
    ) -> CursorProtocol: ...

    def transaction(self) -> AbstractContextManager[None]: ...


@dataclass(frozen=True, slots=True)
class ExplicitProviderWindow:
    """Authoritative provider window supplied by the frozen policy resolver."""

    scope_key: str
    starts_at: datetime
    ends_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.starts_at, "provider_window_start")
        _require_aware(self.ends_at, "provider_window_end")
        if self.ends_at <= self.starts_at:
            raise QuotaV2Error("invalid_provider_window")


@dataclass(frozen=True, slots=True)
class ResolvedQuotaBucket:
    scope: QuotaScopeDeclaration
    starts_at: datetime
    ends_at: datetime
    bucket_key: str
    bucket_hash: str

    @property
    def lock_order_key(self) -> tuple[int, str]:
        return (self.scope.lock_order_key[0], self.bucket_key)


@dataclass(frozen=True, slots=True)
class AuthoritativeQuotaScope:
    scope_policy_id: UUID
    declaration: QuotaScopeDeclaration
    quota_units: int
    ordinal: int

    def __post_init__(self) -> None:
        if isinstance(self.quota_units, bool) or self.quota_units < 1:
            raise QuotaV2Error("authoritative_quota_units_invalid", ordinal=self.ordinal)
        if self.ordinal < 0:
            raise QuotaV2Error("authoritative_scope_ordinal_invalid")


@dataclass(frozen=True, slots=True)
class AuthoritativeScopeSet:
    registry_id: UUID
    registry_revision: str
    scopes: tuple[AuthoritativeQuotaScope, ...]

    def __post_init__(self) -> None:
        if not self.scopes:
            raise QuotaV2Error("quota_scope_set_empty")
        expected_ordinals = tuple(range(len(self.scopes)))
        if tuple(sorted(scope.ordinal for scope in self.scopes)) != expected_ordinals:
            raise QuotaV2Error("authoritative_scope_ordinals_not_contiguous")
        ordered = tuple(sorted(self.scopes, key=lambda scope: scope.declaration.lock_order_key))
        if self.scopes != ordered:
            raise QuotaV2Error("quota_scope_set_not_canonical")
        keys = tuple(scope.declaration.scope_key for scope in self.scopes)
        if len(keys) != len(set(keys)):
            raise QuotaV2Error("quota_scope_set_duplicate")


@dataclass(frozen=True, slots=True)
class _OperationContext:
    send_state: SendState
    send_state_version: int
    operation_key: str
    prepared_at: datetime
    reconcile_after: datetime | None
    platform: str
    collection_surface: str
    product_variant: str
    interaction_mode: str
    registry_revision: str
    quota_policy_revision: str


@dataclass(frozen=True, slots=True)
class _PlannedBucket:
    bucket: ResolvedQuotaBucket
    scope_policy_id: UUID
    units: int


@dataclass(frozen=True, slots=True)
class _BucketRow:
    id: UUID
    scope_policy_id: UUID
    bucket_hash: str
    starts_at: datetime
    ends_at: datetime
    limit_units: int
    reserved_units: int
    consumed_units: int
    unknown_units: int
    state: str


@dataclass(frozen=True, slots=True)
class _ReservationRow:
    id: UUID
    state: ReservationDisposition | str
    requested_units: int
    expected_effect_count: int
    effect_set_hash: str
    binding_id: UUID
    registry_id: UUID


class _QuotaBlocked(Exception):
    def __init__(self, blockers: tuple[QuotaBlocker, ...]) -> None:
        self.blockers = blockers
        super().__init__("quota_capacity_blocked")


@dataclass(frozen=True, slots=True)
class ReserveQuotaRequest:
    tenant_id: UUID
    project_id: UUID
    operation_id: UUID
    binding_id: UUID
    registry_id: UUID
    requested_units: int

    def __post_init__(self) -> None:
        if isinstance(self.requested_units, bool) or self.requested_units < 1:
            raise QuotaV2Error("requested_units_must_be_positive")


@dataclass(frozen=True, slots=True)
class SettleQuotaRequest:
    tenant_id: UUID
    project_id: UUID
    operation_id: UUID
    target_send_state: SendState
    reason_code: str

    def __post_init__(self) -> None:
        if self.target_send_state not in {
            SendState.CONFIRMED_SENT,
            SendState.SEND_UNKNOWN,
            SendState.CONFIRMED_NOT_SENT,
        }:
            raise QuotaV2Error("quota_settlement_requires_terminal_send_state")
        if not _REASON_CODE_RE.fullmatch(self.reason_code):
            raise QuotaV2Error("invalid_quota_reason_code")


@dataclass(frozen=True, slots=True)
class ReconcileQuotaRequest:
    tenant_id: UUID
    project_id: UUID
    operation_id: UUID
    owner_evidence: OwnerEvidence
    lease_terminated: bool
    reason_code: str
    owner_gateway_revision: str | None = None
    owner_evidence_ref: str | None = None
    evidence_hash: str | None = None

    def __post_init__(self) -> None:
        if not _REASON_CODE_RE.fullmatch(self.reason_code):
            raise QuotaV2Error("invalid_quota_reason_code")
        proof_fields = (
            self.owner_gateway_revision,
            self.owner_evidence_ref,
            self.evidence_hash,
        )
        if self.owner_evidence is OwnerEvidence.PROVED_NOT_SENT:
            if any(value is None for value in proof_fields):
                raise QuotaV2Error("not_sent_reconciliation_proof_required")
            assert self.owner_gateway_revision is not None
            assert self.owner_evidence_ref is not None
            assert self.evidence_hash is not None
            if not _OWNER_PROOF_REFERENCE_RE.fullmatch(self.owner_gateway_revision):
                raise QuotaV2Error("invalid_owner_gateway_revision")
            if not _OWNER_PROOF_REFERENCE_RE.fullmatch(self.owner_evidence_ref):
                raise QuotaV2Error("invalid_owner_evidence_ref")
            if not _SHA256_RE.fullmatch(self.evidence_hash):
                raise QuotaV2Error("invalid_owner_evidence_hash")
        elif any(value is not None for value in proof_fields):
            raise QuotaV2Error("unexpected_not_sent_reconciliation_proof")


@dataclass(frozen=True, slots=True)
class QuotaBlocker:
    """Capacity denial safe for logs/API responses; subject IDs are omitted."""

    bucket_hash: str
    scope_kind: QuotaScopeKind
    starts_at: datetime
    ends_at: datetime
    limit_units: int
    reserved_units: int
    consumed_units: int
    unknown_units: int
    requested_units: int

    @property
    def available_units(self) -> int:
        return max(
            0,
            self.limit_units - self.reserved_units - self.consumed_units - self.unknown_units,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "bucket_hash": self.bucket_hash,
            "scope_kind": self.scope_kind.value,
            "window_start": _utc_text(self.starts_at),
            "window_end": _utc_text(self.ends_at),
            "limit_units": self.limit_units,
            "reserved_units": self.reserved_units,
            "consumed_units": self.consumed_units,
            "unknown_units": self.unknown_units,
            "requested_units": self.requested_units,
            "available_units": self.available_units,
        }


@dataclass(frozen=True, slots=True)
class ReserveQuotaResult:
    reserved: bool
    idempotent: bool
    reservation_set_hash: str | None
    reservation_id: UUID | None = None
    blockers: tuple[QuotaBlocker, ...] = ()

    def __post_init__(self) -> None:
        if self.reserved == bool(self.blockers):
            raise QuotaV2Error("invalid_reserve_result")
        if self.reserved != (self.reservation_id is not None):
            raise QuotaV2Error("invalid_reserve_result_reservation_id")


@dataclass(frozen=True, slots=True)
class SettlementResult:
    disposition: ReservationDisposition
    idempotent: bool
    reservation_count: int


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    action: ReconciliationAction
    send_state: SendState
    settlement: SettlementResult | None = None


def reserve_quota(
    connection: ConnectionProtocol,
    request: ReserveQuotaRequest,
) -> ReserveQuotaResult:
    """Atomically reserve the authoritative complete bucket set.

    A capacity blocker is returned as data, but is raised internally so the
    transaction rolls back bucket upserts as well as reservation effects.
    """

    try:
        with connection.transaction():
            _set_tenant_context(connection, request.tenant_id)
            operation = _load_operation_and_binding(connection, request)
            if operation.send_state is not SendState.NOT_SENT:
                raise QuotaV2Error(
                    "quota_reserve_requires_not_sent_operation",
                    send_state=operation.send_state.value,
                )
            scope_set = _load_authoritative_scopes(connection, request, operation)
            planned = _plan_authoritative_buckets(request, operation, scope_set)
            set_hash = _planned_effect_set_hash(planned, request.requested_units)

            existing = _load_reservation(connection, request)
            if existing is not None:
                _validate_existing_reservation(
                    connection,
                    request=request,
                    reservation=existing,
                    planned=planned,
                    effect_set_hash=set_hash,
                )
                return ReserveQuotaResult(
                    reserved=True,
                    idempotent=True,
                    reservation_set_hash=set_hash,
                    reservation_id=existing.id,
                )

            for item in planned:
                connection.execute(
                    ADVISORY_BUCKET_LOCK_SQL,
                    {"bucket_key": item.bucket.bucket_key},
                )
            for item in planned:
                _insert_bucket_if_absent(connection, request, scope_set, item)
            locked: list[tuple[_PlannedBucket, _BucketRow]] = []
            blockers: list[QuotaBlocker] = []
            for item in planned:
                row = _lock_bucket(connection, request, item)
                locked.append((item, row))
                blocker = _capacity_blocker(item, row)
                if blocker is not None:
                    blockers.append(blocker)
            if blockers:
                raise _QuotaBlocked(tuple(blockers))

            reservation_id = _insert_reservation(
                connection,
                request=request,
                operation=operation,
                effect_set_hash=set_hash,
                effect_count=len(locked),
            )
            for item, bucket_row in locked:
                _insert_reserved_effect(
                    connection,
                    request=request,
                    reservation_id=reservation_id,
                    item=item,
                    bucket_row=bucket_row,
                )
            marked = connection.execute(
                MARK_RESERVATION_RESERVED_SQL,
                {
                    "reservation_id": reservation_id,
                    "tenant_id": request.tenant_id,
                    "project_id": request.project_id,
                },
            ).fetchone()
            if marked is None:
                raise QuotaV2Error("quota_reservation_finalize_race")
            return ReserveQuotaResult(
                reserved=True,
                idempotent=False,
                reservation_set_hash=set_hash,
                reservation_id=reservation_id,
            )
    except _QuotaBlocked as blocked:
        return ReserveQuotaResult(
            reserved=False,
            idempotent=False,
            reservation_set_hash=None,
            blockers=blocked.blockers,
        )


def settle_quota(
    connection: ConnectionProtocol,
    request: SettleQuotaRequest,
) -> SettlementResult:
    """Settle or release all operation buckets in one ordered transaction."""

    with connection.transaction():
        _set_tenant_context(connection, request.tenant_id)
        operation = _load_operation(
            connection, request.tenant_id, request.project_id, request.operation_id
        )
        return _settle_locked(
            connection,
            request,
            operation,
            allow_reconciled_sending_release=False,
        )


def reconcile_quota(
    connection: ConnectionProtocol,
    request: ReconcileQuotaRequest,
) -> ReconciliationResult:
    """Reconcile an operation using durable truth plus owner/lease evidence.

    There is deliberately no timeout/age argument.  A ``SENDING`` operation
    without conclusive owner evidence is returned as ``defer`` and retains all
    reservations.
    """

    with connection.transaction():
        _set_tenant_context(connection, request.tenant_id)
        operation = _load_operation(
            connection, request.tenant_id, request.project_id, request.operation_id
        )
        action = reconciliation_action(
            send_state=operation.send_state,
            owner_evidence=request.owner_evidence,
            lease_terminated=request.lease_terminated,
        )
        if action is ReconciliationAction.DEFER:
            return ReconciliationResult(action=action, send_state=operation.send_state)
        target_by_action = {
            ReconciliationAction.RELEASE: SendState.CONFIRMED_NOT_SENT,
            ReconciliationAction.SETTLE_CONSUMED: SendState.CONFIRMED_SENT,
            ReconciliationAction.SETTLE_UNKNOWN: SendState.SEND_UNKNOWN,
        }
        target = target_by_action[action]
        if action is ReconciliationAction.RELEASE and operation.send_state is SendState.SENDING:
            _record_not_sent_proof(connection, request)
        settlement = _settle_locked(
            connection,
            SettleQuotaRequest(
                tenant_id=request.tenant_id,
                project_id=request.project_id,
                operation_id=request.operation_id,
                target_send_state=target,
                reason_code=request.reason_code,
            ),
            operation,
            allow_reconciled_sending_release=(
                action is ReconciliationAction.RELEASE and operation.send_state is SendState.SENDING
            ),
        )
        return ReconciliationResult(action=action, send_state=target, settlement=settlement)


def _set_tenant_context(connection: ConnectionProtocol, tenant_id: UUID) -> None:
    connection.execute(SET_TENANT_SQL, {"tenant_id": tenant_id})


def _record_not_sent_proof(
    connection: ConnectionProtocol,
    request: ReconcileQuotaRequest,
) -> UUID:
    if (
        request.owner_gateway_revision is None
        or request.owner_evidence_ref is None
        or request.evidence_hash is None
    ):
        raise QuotaV2Error("not_sent_reconciliation_proof_required")
    row = connection.execute(
        RECORD_NOT_SENT_PROOF_SQL,
        {
            "tenant_id": request.tenant_id,
            "project_id": request.project_id,
            "operation_id": request.operation_id,
            "owner_gateway_revision": request.owner_gateway_revision,
            "owner_evidence_ref": request.owner_evidence_ref,
            "evidence_hash": request.evidence_hash,
            "reason_code": request.reason_code,
        },
    ).fetchone()
    if row is None or len(row) != 1:
        raise QuotaV2Error("not_sent_reconciliation_proof_rejected")
    return _uuid(row[0], "reconciliation_proof_id")


def _load_operation_and_binding(
    connection: ConnectionProtocol, request: ReserveQuotaRequest
) -> _OperationContext:
    row = connection.execute(
        LOAD_OPERATION_AND_BINDING_SQL,
        {
            "tenant_id": request.tenant_id,
            "project_id": request.project_id,
            "operation_id": request.operation_id,
            "binding_id": request.binding_id,
            "registry_id": request.registry_id,
        },
    ).fetchone()
    if row is None:
        raise QuotaV2Error("operation_binding_scope_mismatch")
    if len(row) != 11:
        raise QuotaV2Error("operation_binding_row_shape_invalid")
    return _OperationContext(
        send_state=_send_state(row[0]),
        send_state_version=_integer(row[1], "send_state_version"),
        operation_key=_text(row[2], "operation_key"),
        prepared_at=_aware_datetime(row[3], "prepared_at"),
        reconcile_after=_optional_aware_datetime(row[4], "reconcile_after"),
        platform=_text(row[5], "platform"),
        collection_surface=_text(row[6], "collection_surface"),
        product_variant=_text(row[7], "product_variant"),
        interaction_mode=_text(row[8], "interaction_mode"),
        registry_revision=_text(row[9], "quota_registry_revision"),
        quota_policy_revision=_text(row[10], "quota_policy_revision"),
    )


def _load_operation(
    connection: ConnectionProtocol,
    tenant_id: UUID,
    project_id: UUID,
    operation_id: UUID,
) -> _OperationContext:
    row = connection.execute(
        LOAD_OPERATION_SQL,
        {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "operation_id": operation_id,
        },
    ).fetchone()
    if row is None:
        raise QuotaV2Error("submission_operation_not_found")
    if len(row) != 9:
        raise QuotaV2Error("submission_operation_row_shape_invalid")
    return _OperationContext(
        send_state=_send_state(row[0]),
        send_state_version=_integer(row[1], "send_state_version"),
        operation_key=_text(row[2], "operation_key"),
        prepared_at=_aware_datetime(row[3], "prepared_at"),
        reconcile_after=_optional_aware_datetime(row[4], "reconcile_after"),
        platform=_text(row[5], "platform"),
        collection_surface=_text(row[6], "collection_surface"),
        product_variant=_text(row[7], "product_variant"),
        interaction_mode=_text(row[8], "interaction_mode"),
        registry_revision="",
        quota_policy_revision="",
    )


def _load_authoritative_scopes(
    connection: ConnectionProtocol,
    request: ReserveQuotaRequest,
    operation: _OperationContext,
) -> AuthoritativeScopeSet:
    rows = connection.execute(
        LOAD_AUTHORITATIVE_SCOPES_SQL,
        {
            "tenant_id": request.tenant_id,
            "project_id": request.project_id,
            "binding_id": request.binding_id,
            "registry_id": request.registry_id,
        },
    ).fetchall()
    if not rows:
        raise QuotaV2Error("authoritative_quota_scope_set_empty")
    parsed: list[AuthoritativeQuotaScope] = []
    registry_revision: str | None = None
    for row_index, row in enumerate(rows):
        if len(row) != 27:
            raise QuotaV2Error("authoritative_scope_row_shape_invalid", row=row_index)
        row_registry_revision = _text(row[0], "registry_revision")
        if registry_revision is None:
            registry_revision = row_registry_revision
        if (
            row_registry_revision != registry_revision
            or row_registry_revision != operation.registry_revision
        ):
            raise QuotaV2Error("quota_registry_revision_mismatch")
        if _text(row[1], "lock_order_version") != QUOTA_SCOPE_LOCK_ORDER_VERSION:
            raise QuotaV2Error("unsupported_quota_lock_order_version")
        if _text(row[3], "scope_schema_version") != "quota-scope-v1":
            raise QuotaV2Error("unsupported_quota_scope_schema")
        if _text(row[13], "window_schema_version") != "quota-window-v1":
            raise QuotaV2Error("unsupported_quota_window_schema")
        try:
            scope_kind = QuotaScopeKind(_text(row[7], "scope_kind"))
            surface_text = _optional_text(row[10], "surface")
            surface = CollectionSurface(surface_text) if surface_text is not None else None
            declaration = QuotaScopeDeclaration(
                policy_revision=_text(row[6], "policy_revision"),
                scope_kind=scope_kind,
                scope_subject_id=_text(row[8], "scope_subject_id"),
                limit=_integer(row[19], "limit_units"),
                window=QuotaWindowPolicy(
                    unit=QuotaWindowUnit(_text(row[14], "window_unit")),
                    size=_integer(row[15], "window_size"),
                    timezone=_text(row[16], "window_timezone"),
                    boundary_revision=_text(row[17], "window_boundary_revision"),
                    provider_window_code=_optional_text(row[18], "provider_window_code"),
                ),
                platform=_optional_text(row[9], "platform"),
                collection_surface=surface,
                product_variant=_optional_text(row[11], "product"),
                interaction_mode=_optional_text(row[12], "mode"),
            )
        except (TypeError, ValueError) as exc:
            raise QuotaV2Error("authoritative_scope_invalid", row=row_index) from exc
        lock_ordinal = _integer(row[20], "lock_order_ordinal")
        if lock_ordinal != declaration.lock_order_key[0]:
            raise QuotaV2Error("quota_scope_lock_ordinal_mismatch", row=row_index)
        policy_key = _text(row[4], "scope_policy_key")
        selector_key = _text(row[5], "selector_key")
        if policy_key != declaration.scope_key or selector_key != declaration.selector_key:
            raise QuotaV2Error("quota_scope_canonical_key_mismatch", row=row_index)
        if declaration.policy_revision != operation.quota_policy_revision:
            raise QuotaV2Error("binding_quota_policy_revision_mismatch", row=row_index)
        if (
            _text(row[23], "mapping_scope_policy_key") != policy_key
            or _text(row[24], "mapping_scope_kind") != scope_kind.value
            or _text(row[25], "mapping_scope_subject_id") != declaration.scope_subject_id
            or _text(row[26], "mapping_policy_revision") != declaration.policy_revision
        ):
            raise QuotaV2Error("binding_quota_scope_snapshot_mismatch", row=row_index)
        _validate_scope_applies_to_operation(declaration, operation, row_index)
        parsed.append(
            AuthoritativeQuotaScope(
                scope_policy_id=_uuid(row[2], "scope_policy_id"),
                declaration=declaration,
                quota_units=_integer(row[21], "quota_units"),
                ordinal=_integer(row[22], "mapping_ordinal"),
            )
        )
    assert registry_revision is not None
    ordered = tuple(sorted(parsed, key=lambda item: item.declaration.lock_order_key))
    return AuthoritativeScopeSet(
        registry_id=request.registry_id,
        registry_revision=registry_revision,
        scopes=ordered,
    )


def _validate_scope_applies_to_operation(
    declaration: QuotaScopeDeclaration,
    operation: _OperationContext,
    row_index: int,
) -> None:
    if declaration.platform is not None and declaration.platform != operation.platform:
        raise QuotaV2Error("quota_scope_platform_mismatch", row=row_index)
    if (
        declaration.collection_surface is not None
        and declaration.collection_surface.value != operation.collection_surface
    ):
        raise QuotaV2Error("quota_scope_surface_mismatch", row=row_index)
    if (
        declaration.product_variant is not None
        and declaration.product_variant != operation.product_variant
    ):
        raise QuotaV2Error("quota_scope_product_mismatch", row=row_index)
    if (
        declaration.interaction_mode is not None
        and declaration.interaction_mode != operation.interaction_mode
    ):
        raise QuotaV2Error("quota_scope_mode_mismatch", row=row_index)


def _plan_authoritative_buckets(
    request: ReserveQuotaRequest,
    operation: _OperationContext,
    scope_set: AuthoritativeScopeSet,
) -> tuple[_PlannedBucket, ...]:
    custom_count = sum(
        scope.declaration.window.unit is QuotaWindowUnit.PROVIDER_CUSTOM
        for scope in scope_set.scopes
    )
    if custom_count:
        raise QuotaV2Error(
            "provider_window_resolution_unavailable",
            scope_count=custom_count,
        )
    buckets = materialize_quota_buckets(
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        scopes=tuple(scope.declaration for scope in scope_set.scopes),
        occurred_at=operation.prepared_at,
    )
    by_key = {scope.declaration.scope_key: scope for scope in scope_set.scopes}
    planned: list[_PlannedBucket] = []
    for bucket in buckets:
        authoritative = by_key[bucket.scope.scope_key]
        units = authoritative.quota_units * request.requested_units
        if units > 2_147_483_647:
            raise QuotaV2Error("quota_requested_units_overflow")
        planned.append(
            _PlannedBucket(
                bucket=bucket,
                scope_policy_id=authoritative.scope_policy_id,
                units=units,
            )
        )
    return tuple(planned)


def _planned_effect_set_hash(planned: tuple[_PlannedBucket, ...], requested_units: int) -> str:
    if not planned:
        raise QuotaV2Error("quota_scope_set_empty")
    material = "\n".join(
        (
            RESERVATION_SET_VERSION,
            QUOTA_SCOPE_LOCK_ORDER_VERSION,
            f"requested_units={requested_units}",
            *(
                f"{item.bucket.bucket_hash}|scope_policy_id={item.scope_policy_id}|units={item.units}"
                for item in planned
            ),
        )
    )
    return sha256(material.encode("utf-8")).hexdigest()


def _load_reservation(
    connection: ConnectionProtocol,
    request: ReserveQuotaRequest | SettleQuotaRequest,
) -> _ReservationRow | None:
    row = connection.execute(
        LOAD_RESERVATION_SQL,
        {
            "tenant_id": request.tenant_id,
            "project_id": request.project_id,
            "operation_id": request.operation_id,
        },
    ).fetchone()
    if row is None:
        return None
    if len(row) != 7:
        raise QuotaV2Error("quota_reservation_row_shape_invalid")
    return _ReservationRow(
        id=_uuid(row[0], "reservation_id"),
        state=_text(row[1], "reservation_state"),
        requested_units=_integer(row[2], "requested_units"),
        expected_effect_count=_integer(row[3], "expected_effect_count"),
        effect_set_hash=_text(row[4], "effect_set_hash"),
        binding_id=_uuid(row[5], "binding_revision_id"),
        registry_id=_uuid(row[6], "quota_registry_id"),
    )


def _validate_existing_reservation(
    connection: ConnectionProtocol,
    *,
    request: ReserveQuotaRequest,
    reservation: _ReservationRow,
    planned: tuple[_PlannedBucket, ...],
    effect_set_hash: str,
) -> None:
    if (
        reservation.requested_units != request.requested_units
        or reservation.expected_effect_count != len(planned)
        or reservation.effect_set_hash != effect_set_hash
        or reservation.binding_id != request.binding_id
        or reservation.registry_id != request.registry_id
    ):
        raise QuotaV2Error("quota_reservation_idempotency_mismatch")
    if reservation.state != ReservationDisposition.RESERVED.value:
        raise QuotaV2Error("quota_operation_already_finalized")
    rows = _load_effect_rows(connection, request.tenant_id, request.project_id, reservation.id)
    if len(rows) != len(planned):
        raise QuotaV2Error("quota_reservation_effect_set_incomplete")
    expected = {item.bucket.bucket_hash: (item.scope_policy_id, item.units) for item in planned}
    for row in rows:
        bucket_hash = _text(row[5], "bucket_hash")
        actual = (
            _uuid(row[2], "scope_policy_id"),
            _integer(row[3], "effect_units"),
        )
        if expected.get(bucket_hash) != actual or _text(row[4], "effect_state") != "reserved":
            raise QuotaV2Error("quota_reservation_effect_set_mismatch")


def _load_effect_rows(
    connection: ConnectionProtocol,
    tenant_id: UUID,
    project_id: UUID,
    reservation_id: UUID,
) -> Sequence[Sequence[object]]:
    rows = connection.execute(
        LOAD_RESERVATION_EFFECTS_SQL,
        {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "reservation_id": reservation_id,
        },
    ).fetchall()
    if any(len(row) != 8 for row in rows):
        raise QuotaV2Error("quota_reservation_effect_row_shape_invalid")
    return rows


def _insert_bucket_if_absent(
    connection: ConnectionProtocol,
    request: ReserveQuotaRequest,
    scope_set: AuthoritativeScopeSet,
    item: _PlannedBucket,
) -> None:
    row_id, pub_id = _new_identity("qbk")
    connection.execute(
        INSERT_BUCKET_SQL,
        {
            "id": row_id,
            "pub_id": pub_id,
            "tenant_id": request.tenant_id,
            "project_id": request.project_id,
            "registry_id": scope_set.registry_id,
            "scope_policy_id": item.scope_policy_id,
            "scope_policy_key": item.bucket.scope.scope_key,
            "scope_kind": item.bucket.scope.scope_kind.value,
            "scope_subject_id": item.bucket.scope.scope_subject_id,
            "policy_revision": item.bucket.scope.policy_revision,
            "bucket_key": item.bucket.bucket_key,
            "bucket_hash": item.bucket.bucket_hash,
            "window_start": item.bucket.starts_at,
            "window_end": item.bucket.ends_at,
            "limit_units": item.bucket.scope.limit,
        },
    )


def _lock_bucket(
    connection: ConnectionProtocol,
    request: ReserveQuotaRequest,
    item: _PlannedBucket,
) -> _BucketRow:
    row = connection.execute(
        LOCK_BUCKET_SQL,
        {
            "tenant_id": request.tenant_id,
            "project_id": request.project_id,
            "bucket_hash": item.bucket.bucket_hash,
            "bucket_key": item.bucket.bucket_key,
        },
    ).fetchone()
    if row is None:
        raise QuotaV2Error("quota_bucket_hash_collision_or_insert_failed")
    bucket = _bucket_row(row)
    if (
        bucket.scope_policy_id != item.scope_policy_id
        or bucket.bucket_hash != item.bucket.bucket_hash
        or bucket.starts_at != item.bucket.starts_at
        or bucket.ends_at != item.bucket.ends_at
        or bucket.limit_units != item.bucket.scope.limit
        or bucket.state != "open"
    ):
        raise QuotaV2Error("quota_bucket_definition_drift")
    return bucket


def _capacity_blocker(item: _PlannedBucket, row: _BucketRow) -> QuotaBlocker | None:
    counters = (row.limit_units, row.reserved_units, row.consumed_units, row.unknown_units)
    if any(value < 0 for value in counters) or sum(counters[1:]) > row.limit_units:
        raise QuotaV2Error("quota_bucket_projection_invalid", bucket_hash=row.bucket_hash)
    if sum(counters[1:]) + item.units <= row.limit_units:
        return None
    return QuotaBlocker(
        bucket_hash=row.bucket_hash,
        scope_kind=item.bucket.scope.scope_kind,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        limit_units=row.limit_units,
        reserved_units=row.reserved_units,
        consumed_units=row.consumed_units,
        unknown_units=row.unknown_units,
        requested_units=item.units,
    )


def _insert_reservation(
    connection: ConnectionProtocol,
    *,
    request: ReserveQuotaRequest,
    operation: _OperationContext,
    effect_set_hash: str,
    effect_count: int,
) -> UUID:
    reservation_id, pub_id = _new_identity("qrs")
    reservation_key = (
        f"collection-quota-reservation-v1|operation_id={request.operation_id}"
        f"|effect_set_hash={effect_set_hash}"
    )
    row = connection.execute(
        INSERT_RESERVATION_SQL,
        {
            "id": reservation_id,
            "pub_id": pub_id,
            "tenant_id": request.tenant_id,
            "project_id": request.project_id,
            "operation_id": request.operation_id,
            "binding_id": request.binding_id,
            "registry_id": request.registry_id,
            "reservation_key": reservation_key,
            "idempotency_key": sha256(reservation_key.encode("utf-8")).hexdigest(),
            "requested_units": request.requested_units,
            "expected_effect_count": effect_count,
            "effect_set_hash": effect_set_hash,
            "reconcile_after": operation.reconcile_after,
        },
    ).fetchone()
    if row is None or _uuid(row[0], "reservation_id") != reservation_id:
        raise QuotaV2Error("quota_reservation_insert_failed")
    return reservation_id


def _insert_reserved_effect(
    connection: ConnectionProtocol,
    *,
    request: ReserveQuotaRequest,
    reservation_id: UUID,
    item: _PlannedBucket,
    bucket_row: _BucketRow,
) -> None:
    effect_id, effect_pub_id = _new_identity("qef")
    effect_key = sha256(
        (
            f"collection-quota-effect-v1|operation={request.operation_id}"
            f"|bucket={item.bucket.bucket_hash}"
        ).encode()
    ).hexdigest()
    inserted = connection.execute(
        INSERT_RESERVATION_EFFECT_SQL,
        {
            "id": effect_id,
            "pub_id": effect_pub_id,
            "tenant_id": request.tenant_id,
            "project_id": request.project_id,
            "reservation_id": reservation_id,
            "operation_id": request.operation_id,
            "bucket_id": bucket_row.id,
            "scope_policy_id": item.scope_policy_id,
            "effect_key": effect_key,
            "units": item.units,
        },
    ).fetchone()
    if inserted is None:
        raise QuotaV2Error("quota_reservation_effect_insert_failed")
    incremented = connection.execute(
        INCREMENT_BUCKET_RESERVED_SQL,
        {
            "bucket_id": bucket_row.id,
            "tenant_id": request.tenant_id,
            "project_id": request.project_id,
            "units": item.units,
        },
    ).fetchone()
    if incremented is None:
        raise QuotaV2Error("quota_capacity_changed_after_lock")
    _insert_ledger_event(
        connection,
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        reservation_id=reservation_id,
        operation_id=request.operation_id,
        effect_id=effect_id,
        bucket_id=bucket_row.id,
        scope_policy_id=item.scope_policy_id,
        effect_kind=LedgerEffectKind.RESERVE,
        from_state=None,
        to_state="reserved",
        units=item.units,
        reason_code="atomic_reserve_v1",
    )


def _settle_locked(
    connection: ConnectionProtocol,
    request: SettleQuotaRequest,
    operation: _OperationContext,
    *,
    allow_reconciled_sending_release: bool,
) -> SettlementResult:
    effect_kind, disposition = settlement_effect(request.target_send_state)
    if (
        disposition is ReservationDisposition.RELEASED
        and operation.send_state is SendState.SENDING
        and not allow_reconciled_sending_release
    ):
        raise QuotaV2Error("sending_quota_release_requires_reconciliation")
    if disposition is ReservationDisposition.RELEASED and operation.send_state in {
        SendState.CONFIRMED_SENT,
        SendState.SEND_UNKNOWN,
    }:
        raise QuotaV2Error(
            "no_resend_send_truth_cannot_release_quota",
            send_state=operation.send_state.value,
        )
    reservation = _load_reservation(connection, request)
    if reservation is None:
        raise QuotaV2Error("quota_reservation_not_found")
    rows = _load_effect_rows(
        connection,
        request.tenant_id,
        request.project_id,
        reservation.id,
    )
    if len(rows) != reservation.expected_effect_count or not rows:
        raise QuotaV2Error("quota_reservation_effect_set_incomplete")
    if reservation.state == disposition.value:
        if operation.send_state is not request.target_send_state:
            raise QuotaV2Error("operation_quota_terminal_state_mismatch")
        if any(_text(row[4], "effect_state") != disposition.value for row in rows):
            raise QuotaV2Error("quota_terminal_effect_set_mismatch")
        return SettlementResult(
            disposition=disposition,
            idempotent=True,
            reservation_count=len(rows),
        )
    if reservation.state not in {"reserved", "reconciling"}:
        raise QuotaV2Error("quota_reservation_terminal_conflict")

    if operation.send_state is not request.target_send_state:
        try:
            transition_send_state(operation.send_state, request.target_send_state)
        except ValueError as exc:
            raise QuotaV2Error(
                "operation_send_state_transition_invalid",
                current=operation.send_state.value,
                target=request.target_send_state.value,
            ) from exc
        transitioned = connection.execute(
            UPDATE_OPERATION_SEND_STATE_SQL,
            {
                "operation_id": request.operation_id,
                "tenant_id": request.tenant_id,
                "project_id": request.project_id,
                "target_send_state": request.target_send_state.value,
                "current_send_state": operation.send_state.value,
                "send_state_version": operation.send_state_version,
                "reason_code": request.reason_code,
            },
        ).fetchone()
        if transitioned is None:
            raise QuotaV2Error("operation_send_state_transition_race")

    locked_buckets: dict[UUID, _BucketRow] = {}
    for row in rows:
        bucket_id = _uuid(row[1], "bucket_id")
        bucket_row = connection.execute(
            LOCK_BUCKET_BY_ID_SQL,
            {
                "bucket_id": bucket_id,
                "tenant_id": request.tenant_id,
                "project_id": request.project_id,
            },
        ).fetchone()
        if bucket_row is None:
            raise QuotaV2Error("quota_bucket_missing_during_settlement")
        locked_buckets[bucket_id] = _bucket_row(bucket_row)

    for row in rows:
        effect_id = _uuid(row[0], "effect_id")
        bucket_id = _uuid(row[1], "bucket_id")
        scope_policy_id = _uuid(row[2], "scope_policy_id")
        units = _integer(row[3], "effect_units")
        if _text(row[4], "effect_state") != "reserved":
            raise QuotaV2Error("quota_effect_not_reserved")
        bucket = locked_buckets[bucket_id]
        if bucket.scope_policy_id != scope_policy_id or bucket.reserved_units < units:
            raise QuotaV2Error("quota_projection_conservation_failure")
        effect_updated = connection.execute(
            UPDATE_EFFECT_TERMINAL_SQL,
            {
                "effect_id": effect_id,
                "tenant_id": request.tenant_id,
                "project_id": request.project_id,
                "effect_state": disposition.value,
                "reason_code": request.reason_code,
            },
        ).fetchone()
        if effect_updated is None:
            raise QuotaV2Error("quota_effect_terminal_race")
        bucket_updated = connection.execute(
            SETTLE_BUCKET_SQL,
            {
                "bucket_id": bucket_id,
                "tenant_id": request.tenant_id,
                "project_id": request.project_id,
                "units": units,
                "consumed_delta": units if disposition is ReservationDisposition.CONSUMED else 0,
                "unknown_delta": (
                    units if disposition is ReservationDisposition.UNKNOWN_CONSUMED else 0
                ),
            },
        ).fetchone()
        if bucket_updated is None:
            raise QuotaV2Error("quota_bucket_settlement_race")
        _insert_ledger_event(
            connection,
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            reservation_id=reservation.id,
            operation_id=request.operation_id,
            effect_id=effect_id,
            bucket_id=bucket_id,
            scope_policy_id=scope_policy_id,
            effect_kind=effect_kind,
            from_state="reserved",
            to_state=disposition.value,
            units=units,
            reason_code=request.reason_code,
        )

    terminal = connection.execute(
        MARK_RESERVATION_TERMINAL_SQL,
        {
            "reservation_id": reservation.id,
            "tenant_id": request.tenant_id,
            "project_id": request.project_id,
            "reservation_state": disposition.value,
            "reason_code": request.reason_code,
        },
    ).fetchone()
    if terminal is None:
        raise QuotaV2Error("quota_reservation_terminal_race")
    return SettlementResult(
        disposition=disposition,
        idempotent=False,
        reservation_count=len(rows),
    )


def _insert_ledger_event(
    connection: ConnectionProtocol,
    *,
    tenant_id: UUID,
    project_id: UUID,
    reservation_id: UUID,
    operation_id: UUID,
    effect_id: UUID,
    bucket_id: UUID,
    scope_policy_id: UUID,
    effect_kind: LedgerEffectKind,
    from_state: str | None,
    to_state: str,
    units: int,
    reason_code: str,
) -> None:
    event_id, event_pub_id = _new_identity("qle")
    event_key = sha256(
        (f"collection-quota-ledger-v1|effect={effect_id}|kind={effect_kind.value}").encode()
    ).hexdigest()
    inserted = connection.execute(
        INSERT_LEDGER_EVENT_SQL,
        {
            "id": event_id,
            "pub_id": event_pub_id,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "effect_id": effect_id,
            "reservation_id": reservation_id,
            "operation_id": operation_id,
            "bucket_id": bucket_id,
            "scope_policy_id": scope_policy_id,
            "event_key": event_key,
            "idempotency_key": event_key,
            "effect_kind": effect_kind.value,
            "from_state": from_state,
            "to_state": to_state,
            "units": units,
            "reason_code": reason_code,
        },
    ).fetchone()
    if inserted is None:
        raise QuotaV2Error("quota_ledger_event_insert_failed")


def _bucket_row(row: Sequence[object]) -> _BucketRow:
    if len(row) != 10:
        raise QuotaV2Error("quota_bucket_row_shape_invalid")
    return _BucketRow(
        id=_uuid(row[0], "bucket_id"),
        scope_policy_id=_uuid(row[1], "scope_policy_id"),
        bucket_hash=_text(row[2], "bucket_hash"),
        starts_at=_aware_datetime(row[3], "window_start"),
        ends_at=_aware_datetime(row[4], "window_end"),
        limit_units=_integer(row[5], "limit_units"),
        reserved_units=_integer(row[6], "reserved_units"),
        consumed_units=_integer(row[7], "settled_consumed_units"),
        unknown_units=_integer(row[8], "settled_unknown_units"),
        state=_text(row[9], "bucket_state"),
    )


def _new_identity(prefix: str) -> tuple[UUID, str]:
    row_id = uuid4()
    return (row_id, f"{prefix}_{row_id.hex[:26]}")


def _uuid(value: object, field: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as exc:
            raise QuotaV2Error("quota_database_value_invalid", field=field) from exc
    raise QuotaV2Error("quota_database_value_invalid", field=field)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise QuotaV2Error("quota_database_value_invalid", field=field)
    return value


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, field)


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise QuotaV2Error("quota_database_value_invalid", field=field)
    return value


def _aware_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise QuotaV2Error("quota_database_value_invalid", field=field)
    _require_aware(value, field)
    return value


def _optional_aware_datetime(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    return _aware_datetime(value, field)


def _send_state(value: object) -> SendState:
    try:
        return SendState(_text(value, "send_state"))
    except ValueError as exc:
        raise QuotaV2Error("quota_database_send_state_invalid") from exc


def materialize_quota_buckets(
    *,
    tenant_id: UUID,
    project_id: UUID,
    scopes: tuple[QuotaScopeDeclaration, ...],
    occurred_at: datetime,
    provider_windows: tuple[ExplicitProviderWindow, ...] = (),
) -> tuple[ResolvedQuotaBucket, ...]:
    """Resolve every declared scope into a canonical, globally ordered bucket set.

    ``provider_windows`` is only a pure seam for a future trusted/provider-signed
    resolver.  The live PostgreSQL reserve path never accepts caller-declared
    boundaries and fails closed while no authoritative resolver is persisted.
    """

    _require_aware(occurred_at, "occurred_at")
    if not scopes:
        raise QuotaV2Error("quota_scope_set_empty")
    ordered_scopes = canonical_quota_lock_order(scopes)
    scope_keys = tuple(scope.scope_key for scope in ordered_scopes)
    if len(scope_keys) != len(set(scope_keys)):
        raise QuotaV2Error("quota_scope_set_duplicate")

    explicit = {window.scope_key: window for window in provider_windows}
    if len(explicit) != len(provider_windows):
        raise QuotaV2Error("provider_window_set_duplicate")
    known_provider_keys = {
        scope.scope_key
        for scope in ordered_scopes
        if scope.window.unit is QuotaWindowUnit.PROVIDER_CUSTOM
    }
    if set(explicit) != known_provider_keys:
        raise QuotaV2Error(
            "provider_window_set_mismatch",
            expected_count=len(known_provider_keys),
            actual_count=len(explicit),
        )

    buckets: list[ResolvedQuotaBucket] = []
    for scope in ordered_scopes:
        if scope.window.unit is QuotaWindowUnit.PROVIDER_CUSTOM:
            window = explicit[scope.scope_key]
            starts_at = window.starts_at.astimezone(UTC)
            ends_at = window.ends_at.astimezone(UTC)
            occurred_at_utc = occurred_at.astimezone(UTC)
            if not starts_at <= occurred_at_utc < ends_at:
                raise QuotaV2Error("provider_window_does_not_cover_occurrence")
        else:
            starts_at, ends_at = _calendar_window(scope, occurred_at)
        bucket_key = (
            f"{BUCKET_KEY_VERSION}"
            f"|tenant_id={tenant_id}"
            f"|project_id={project_id}"
            f"|scope={scope.scope_key}"
            f"|window_start={_utc_text(starts_at)}"
            f"|window_end={_utc_text(ends_at)}"
        )
        buckets.append(
            ResolvedQuotaBucket(
                scope=scope,
                starts_at=starts_at,
                ends_at=ends_at,
                bucket_key=bucket_key,
                bucket_hash=sha256(bucket_key.encode("utf-8")).hexdigest(),
            )
        )
    return tuple(sorted(buckets, key=lambda bucket: bucket.lock_order_key))


def reservation_set_hash(buckets: tuple[ResolvedQuotaBucket, ...], *, requested_units: int) -> str:
    if not buckets:
        raise QuotaV2Error("quota_scope_set_empty")
    material = "\n".join(
        (
            RESERVATION_SET_VERSION,
            QUOTA_SCOPE_LOCK_ORDER_VERSION,
            f"requested_units={requested_units}",
            *(bucket.bucket_hash for bucket in buckets),
        )
    )
    return sha256(material.encode("utf-8")).hexdigest()


def settlement_effect(send_state: SendState) -> tuple[LedgerEffectKind, ReservationDisposition]:
    """Map durable send truth to the only allowed quota terminal effect."""

    if send_state is SendState.CONFIRMED_SENT:
        return (LedgerEffectKind.SETTLE_CONSUMED, ReservationDisposition.CONSUMED)
    if send_state is SendState.SEND_UNKNOWN:
        return (LedgerEffectKind.SETTLE_UNKNOWN, ReservationDisposition.UNKNOWN_CONSUMED)
    if send_state is SendState.CONFIRMED_NOT_SENT:
        return (LedgerEffectKind.RELEASE, ReservationDisposition.RELEASED)
    raise QuotaV2Error("non_terminal_send_state_has_no_settlement", send_state=send_state.value)


def reconciliation_action(
    *,
    send_state: SendState,
    owner_evidence: OwnerEvidence,
    lease_terminated: bool,
) -> ReconciliationAction:
    """Choose an action without ever releasing merely because a timer elapsed."""

    if send_state is SendState.CONFIRMED_SENT:
        return ReconciliationAction.SETTLE_CONSUMED
    if send_state is SendState.SEND_UNKNOWN:
        return ReconciliationAction.SETTLE_UNKNOWN
    if send_state is SendState.CONFIRMED_NOT_SENT:
        return ReconciliationAction.RELEASE
    if owner_evidence is OwnerEvidence.PROVED_SENT:
        return ReconciliationAction.SETTLE_CONSUMED
    if owner_evidence is OwnerEvidence.ACKNOWLEDGEMENT_UNKNOWN:
        return ReconciliationAction.SETTLE_UNKNOWN
    if (
        owner_evidence is OwnerEvidence.PROVED_NOT_SENT
        and lease_terminated
        and send_state in {SendState.NOT_SENT, SendState.SENDING}
    ):
        return ReconciliationAction.RELEASE
    return ReconciliationAction.DEFER


def _calendar_window(
    scope: QuotaScopeDeclaration, occurred_at: datetime
) -> tuple[datetime, datetime]:
    policy = scope.window
    if policy.boundary_revision != "calendar-v1":
        raise QuotaV2Error(
            "unsupported_quota_boundary_revision",
            boundary_revision=policy.boundary_revision,
        )
    local = occurred_at.astimezone(ZoneInfo(policy.timezone))
    if policy.unit is QuotaWindowUnit.DAY:
        anchor = date(1970, 1, 1)
        elapsed = (local.date() - anchor).days
        start_date = anchor + timedelta(days=(elapsed // policy.size) * policy.size)
        end_date = start_date + timedelta(days=policy.size)
    elif policy.unit is QuotaWindowUnit.WEEK:
        anchor = date(1970, 1, 5)
        monday = local.date() - timedelta(days=local.weekday())
        elapsed_weeks = (monday - anchor).days // 7
        start_date = anchor + timedelta(weeks=(elapsed_weeks // policy.size) * policy.size)
        end_date = start_date + timedelta(weeks=policy.size)
    elif policy.unit is QuotaWindowUnit.YEAR:
        start_year = ((local.year - 1) // policy.size) * policy.size + 1
        start_date = date(start_year, 1, 1)
        end_date = date(start_year + policy.size, 1, 1)
    else:
        raise QuotaV2Error("provider_window_must_be_explicit")
    timezone = ZoneInfo(policy.timezone)
    starts_at = datetime.combine(start_date, datetime.min.time(), timezone).astimezone(UTC)
    ends_at = datetime.combine(end_date, datetime.min.time(), timezone).astimezone(UTC)
    return (starts_at, ends_at)


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise QuotaV2Error("quota_datetime_must_be_aware", field=field)


def _utc_text(value: datetime) -> str:
    _require_aware(value, "datetime")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


__all__ = [
    "AuthoritativeQuotaScope",
    "AuthoritativeScopeSet",
    "BUCKET_KEY_VERSION",
    "ConnectionProtocol",
    "ExplicitProviderWindow",
    "LedgerEffectKind",
    "OwnerEvidence",
    "QUOTA_PROTOCOL_VERSION",
    "RECORD_NOT_SENT_PROOF_SQL",
    "QuotaBlocker",
    "QuotaV2Error",
    "ReconcileQuotaRequest",
    "ReconciliationAction",
    "ReconciliationResult",
    "ReservationDisposition",
    "ReserveQuotaRequest",
    "ReserveQuotaResult",
    "ResolvedQuotaBucket",
    "SettleQuotaRequest",
    "SettlementResult",
    "materialize_quota_buckets",
    "reconcile_quota",
    "reconciliation_action",
    "reserve_quota",
    "reservation_set_hash",
    "settle_quota",
    "settlement_effect",
]
