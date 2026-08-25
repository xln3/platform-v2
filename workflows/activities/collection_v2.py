"""Constant-size activity contracts for the isolated collection v2 worker.

The production entry points deliberately fail closed. Stage 4 can exercise the
Temporal protocol with deterministic fixture activities, but no real collection
I/O is authorized until persisted partition, per-item control, checkpoint, and
restricted database adapters are wired to these exact contracts.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any, Literal

from temporalio import activity
from temporalio.exceptions import ApplicationError

COLLECTION_V2_PAGE_REQUEST_SCHEMA = "collection-page-request-v2"
COLLECTION_V2_PAGE_RECEIPT_SCHEMA = "collection-page-receipt-v2"
COLLECTION_V2_RECONCILIATION_REQUEST_SCHEMA = "collection-reconciliation-request-v2"
COLLECTION_V2_RECONCILIATION_RECEIPT_SCHEMA = "collection-reconciliation-receipt-v2"
COLLECTION_V2_FINALIZATION_REQUEST_SCHEMA = "collection-finalization-request-v2"
COLLECTION_V2_FINALIZATION_RECEIPT_SCHEMA = "collection-finalization-receipt-v2"
COLLECTION_V2_ACTIVE_PAGE_CONTROL_GATE = "collection-active-page-control-unwired-v1"

MAX_COLLECTION_V2_PAGE_SIZE = 2_048
MAX_COLLECTION_V2_ACTIVITY_PAYLOAD_BYTES = 8_192
MAX_COLLECTION_V2_CHECKPOINT_VERSION = 2**63 - 1

_OPAQUE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

CheckpointEventKind = Literal["page", "reconciliation"]
ReconciliationStateMarker = Literal[
    "page_clear",
    "page_requires_reconciliation",
    "reconciliation_pending",
    "reconciliation_settled",
]


class CollectionV2ContractError(ValueError):
    """Raised when a versioned v2 DTO is not exact and bounded."""


def _require_schema(actual: str, expected: str) -> None:
    if actual != expected:
        raise CollectionV2ContractError(f"unsupported_schema:{actual}")


def _require_ref(value: str, *, field: str) -> None:
    if not isinstance(value, str) or _OPAQUE_REF.fullmatch(value) is None:
        raise CollectionV2ContractError(f"invalid_reference:{field}")


def _require_digest(value: str, *, field: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CollectionV2ContractError(f"invalid_sha256:{field}")


def _require_int(value: int, *, field: str, minimum: int, maximum: int | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CollectionV2ContractError(f"integer_required:{field}")
    if value < minimum or (maximum is not None and value > maximum):
        raise CollectionV2ContractError(f"integer_out_of_range:{field}")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _canonical_payload(instance: Any) -> str:
    return _canonical_json(asdict(instance))


def _payload_digest(value: dict[str, object]) -> str:
    return sha256(_canonical_json(value).encode()).hexdigest()


def _require_bounded_payload(instance: Any) -> None:
    if len(_canonical_payload(instance).encode("utf-8")) > MAX_COLLECTION_V2_ACTIVITY_PAYLOAD_BYTES:
        raise CollectionV2ContractError("activity_payload_too_large")


def initial_collection_v2_reconciliation_checkpoint_digest(
    *, partition_digest: str, cursor: int, reconciliation_checkpoint_ref: str
) -> str:
    """Derive a deterministic bootstrap digest until DB-owned launch facts exist."""

    _require_digest(partition_digest, field="partition_digest")
    _require_int(cursor, field="cursor", minimum=0, maximum=MAX_COLLECTION_V2_CHECKPOINT_VERSION)
    _require_ref(reconciliation_checkpoint_ref, field="reconciliation_checkpoint_ref")
    return _payload_digest(
        {
            "cursor": cursor,
            "partition_digest": partition_digest,
            "reconciliation_checkpoint_ref": reconciliation_checkpoint_ref,
            "schema_version": "collection-reconciliation-checkpoint-bootstrap-v1",
        }
    )


def seed_collection_v2_checkpoint_chain(
    *,
    partition_digest: str,
    cursor: int,
    checkpoint_ref: str,
    checkpoint_digest: str,
    checkpoint_version: int,
) -> str:
    _require_digest(partition_digest, field="partition_digest")
    _require_int(cursor, field="cursor", minimum=0, maximum=MAX_COLLECTION_V2_CHECKPOINT_VERSION)
    _require_ref(checkpoint_ref, field="checkpoint_ref")
    _require_digest(checkpoint_digest, field="checkpoint_digest")
    _require_int(
        checkpoint_version,
        field="checkpoint_version",
        minimum=0,
        maximum=MAX_COLLECTION_V2_CHECKPOINT_VERSION,
    )
    return _payload_digest(
        {
            "checkpoint_digest": checkpoint_digest,
            "checkpoint_ref": checkpoint_ref,
            "checkpoint_version": checkpoint_version,
            "cursor": cursor,
            "partition_digest": partition_digest,
            "schema_version": "collection-checkpoint-chain-seed-v1",
        }
    )


def seed_collection_v2_reconciliation_chain(
    *,
    partition_digest: str,
    cursor: int,
    reconciliation_checkpoint_ref: str,
    reconciliation_checkpoint_digest: str,
    reconciliation_checkpoint_version: int,
) -> str:
    _require_digest(partition_digest, field="partition_digest")
    _require_int(cursor, field="cursor", minimum=0, maximum=MAX_COLLECTION_V2_CHECKPOINT_VERSION)
    _require_ref(reconciliation_checkpoint_ref, field="reconciliation_checkpoint_ref")
    _require_digest(
        reconciliation_checkpoint_digest,
        field="reconciliation_checkpoint_digest",
    )
    _require_int(
        reconciliation_checkpoint_version,
        field="reconciliation_checkpoint_version",
        minimum=0,
        maximum=MAX_COLLECTION_V2_CHECKPOINT_VERSION,
    )
    return _payload_digest(
        {
            "cursor": cursor,
            "partition_digest": partition_digest,
            "reconciliation_checkpoint_digest": reconciliation_checkpoint_digest,
            "reconciliation_checkpoint_ref": reconciliation_checkpoint_ref,
            "reconciliation_checkpoint_version": reconciliation_checkpoint_version,
            "schema_version": "collection-reconciliation-chain-seed-v1",
        }
    )


def advance_collection_v2_checkpoint_chain(
    *,
    prior_chain_digest: str,
    partition_digest: str,
    prior_cursor: int,
    next_cursor: int,
    prior_version: int,
    next_version: int,
    checkpoint_ref: str,
    checkpoint_digest: str,
    evidence_digest: str,
    event_kind: CheckpointEventKind,
) -> str:
    return _payload_digest(
        {
            "checkpoint_digest": checkpoint_digest,
            "checkpoint_ref": checkpoint_ref,
            "event_kind": event_kind,
            "evidence_digest": evidence_digest,
            "next_cursor": next_cursor,
            "next_version": next_version,
            "partition_digest": partition_digest,
            "prior_chain_digest": prior_chain_digest,
            "prior_cursor": prior_cursor,
            "prior_version": prior_version,
            "schema_version": "collection-checkpoint-chain-step-v1",
        }
    )


def advance_collection_v2_reconciliation_chain(
    *,
    prior_chain_digest: str,
    partition_digest: str,
    cursor: int,
    prior_version: int,
    next_version: int,
    reconciliation_checkpoint_ref: str,
    reconciliation_checkpoint_digest: str,
    evidence_digest: str,
    state_marker: ReconciliationStateMarker,
) -> str:
    return _payload_digest(
        {
            "cursor": cursor,
            "evidence_digest": evidence_digest,
            "next_version": next_version,
            "partition_digest": partition_digest,
            "prior_chain_digest": prior_chain_digest,
            "prior_version": prior_version,
            "reconciliation_checkpoint_digest": reconciliation_checkpoint_digest,
            "reconciliation_checkpoint_ref": reconciliation_checkpoint_ref,
            "schema_version": "collection-reconciliation-chain-step-v1",
            "state_marker": state_marker,
        }
    )


def collection_v2_terminal_chain_digest(
    *,
    partition_digest: str,
    cursor: int,
    checkpoint_chain_digest: str,
    reconciliation_chain_digest: str,
    terminal_proof_ref: str,
    terminal_proof_digest: str,
    terminal_proof_version: int,
) -> str:
    return _payload_digest(
        {
            "checkpoint_chain_digest": checkpoint_chain_digest,
            "cursor": cursor,
            "partition_digest": partition_digest,
            "reconciliation_chain_digest": reconciliation_chain_digest,
            "schema_version": "collection-partition-terminal-chain-v1",
            "terminal_proof_digest": terminal_proof_digest,
            "terminal_proof_ref": terminal_proof_ref,
            "terminal_proof_version": terminal_proof_version,
        }
    )


@dataclass(frozen=True)
class CollectionV2PageRequest:
    """A bounded reference-only page command with an explicit unsatisfied control gate."""

    schema_version: str
    tenant_pub_id: str
    campaign_pub_id: str
    partition_pub_id: str
    partition_digest: str
    membership_digest: str
    cursor: int
    page_size: int
    checkpoint_ref: str
    checkpoint_digest: str
    checkpoint_version: int
    checkpoint_chain_digest: str
    reconciliation_checkpoint_ref: str
    reconciliation_checkpoint_digest: str
    reconciliation_checkpoint_version: int
    reconciliation_chain_digest: str
    capability_policy_revision: str
    control_policy_revision: str
    active_page_control_gate: str = COLLECTION_V2_ACTIVE_PAGE_CONTROL_GATE

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, COLLECTION_V2_PAGE_REQUEST_SCHEMA)
        for field in (
            "tenant_pub_id",
            "campaign_pub_id",
            "partition_pub_id",
            "checkpoint_ref",
            "reconciliation_checkpoint_ref",
            "capability_policy_revision",
            "control_policy_revision",
        ):
            _require_ref(getattr(self, field), field=field)
        for field in (
            "partition_digest",
            "membership_digest",
            "checkpoint_digest",
            "checkpoint_chain_digest",
            "reconciliation_checkpoint_digest",
            "reconciliation_chain_digest",
        ):
            _require_digest(getattr(self, field), field=field)
        _require_int(
            self.cursor,
            field="cursor",
            minimum=0,
            maximum=MAX_COLLECTION_V2_CHECKPOINT_VERSION,
        )
        _require_int(
            self.page_size,
            field="page_size",
            minimum=1,
            maximum=MAX_COLLECTION_V2_PAGE_SIZE,
        )
        for field in ("checkpoint_version", "reconciliation_checkpoint_version"):
            _require_int(
                getattr(self, field),
                field=field,
                minimum=0,
                maximum=MAX_COLLECTION_V2_CHECKPOINT_VERSION,
            )
        if self.active_page_control_gate != COLLECTION_V2_ACTIVE_PAGE_CONTROL_GATE:
            raise CollectionV2ContractError("active_page_control_gate_drift")
        _require_bounded_payload(self)

    @property
    def payload_json(self) -> str:
        return _canonical_payload(self)


@dataclass(frozen=True)
class CollectionV2PageReceipt:
    """Exact prior-to-next durable receipt for one bounded partition page."""

    schema_version: str
    campaign_pub_id: str
    partition_pub_id: str
    partition_digest: str
    prior_cursor: int
    next_cursor: int
    page_item_count: int
    page_digest: str
    prior_checkpoint_ref: str
    prior_checkpoint_digest: str
    prior_checkpoint_version: int
    prior_checkpoint_chain_digest: str
    checkpoint_ref: str
    checkpoint_digest: str
    checkpoint_version: int
    checkpoint_chain_digest: str
    prior_reconciliation_checkpoint_ref: str
    prior_reconciliation_checkpoint_digest: str
    prior_reconciliation_checkpoint_version: int
    prior_reconciliation_chain_digest: str
    reconciliation_checkpoint_ref: str
    reconciliation_checkpoint_digest: str
    reconciliation_checkpoint_version: int
    reconciliation_chain_digest: str
    requires_reconciliation: bool = False

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, COLLECTION_V2_PAGE_RECEIPT_SCHEMA)
        for field in (
            "campaign_pub_id",
            "partition_pub_id",
            "prior_checkpoint_ref",
            "checkpoint_ref",
            "prior_reconciliation_checkpoint_ref",
            "reconciliation_checkpoint_ref",
        ):
            _require_ref(getattr(self, field), field=field)
        for field in (
            "partition_digest",
            "page_digest",
            "prior_checkpoint_digest",
            "prior_checkpoint_chain_digest",
            "checkpoint_digest",
            "checkpoint_chain_digest",
            "prior_reconciliation_checkpoint_digest",
            "prior_reconciliation_chain_digest",
            "reconciliation_checkpoint_digest",
            "reconciliation_chain_digest",
        ):
            _require_digest(getattr(self, field), field=field)
        for field in (
            "prior_cursor",
            "next_cursor",
            "prior_checkpoint_version",
            "checkpoint_version",
            "prior_reconciliation_checkpoint_version",
            "reconciliation_checkpoint_version",
        ):
            _require_int(
                getattr(self, field),
                field=field,
                minimum=0,
                maximum=MAX_COLLECTION_V2_CHECKPOINT_VERSION,
            )
        _require_int(
            self.page_item_count,
            field="page_item_count",
            minimum=1,
            maximum=MAX_COLLECTION_V2_PAGE_SIZE,
        )
        if self.next_cursor <= self.prior_cursor:
            raise CollectionV2ContractError("page_cursor_did_not_advance")
        if self.next_cursor - self.prior_cursor != self.page_item_count:
            raise CollectionV2ContractError("page_count_cursor_mismatch")
        if self.checkpoint_version != self.prior_checkpoint_version + 1:
            raise CollectionV2ContractError("checkpoint_version_not_monotonic")
        if (
            self.reconciliation_checkpoint_version
            != self.prior_reconciliation_checkpoint_version + 1
        ):
            raise CollectionV2ContractError("reconciliation_version_not_monotonic")
        if not isinstance(self.requires_reconciliation, bool):
            raise CollectionV2ContractError("boolean_required:requires_reconciliation")
        expected_checkpoint_chain = advance_collection_v2_checkpoint_chain(
            prior_chain_digest=self.prior_checkpoint_chain_digest,
            partition_digest=self.partition_digest,
            prior_cursor=self.prior_cursor,
            next_cursor=self.next_cursor,
            prior_version=self.prior_checkpoint_version,
            next_version=self.checkpoint_version,
            checkpoint_ref=self.checkpoint_ref,
            checkpoint_digest=self.checkpoint_digest,
            evidence_digest=self.page_digest,
            event_kind="page",
        )
        if self.checkpoint_chain_digest != expected_checkpoint_chain:
            raise CollectionV2ContractError("checkpoint_chain_drift")
        state_marker: ReconciliationStateMarker = (
            "page_requires_reconciliation" if self.requires_reconciliation else "page_clear"
        )
        expected_reconciliation_chain = advance_collection_v2_reconciliation_chain(
            prior_chain_digest=self.prior_reconciliation_chain_digest,
            partition_digest=self.partition_digest,
            cursor=self.next_cursor,
            prior_version=self.prior_reconciliation_checkpoint_version,
            next_version=self.reconciliation_checkpoint_version,
            reconciliation_checkpoint_ref=self.reconciliation_checkpoint_ref,
            reconciliation_checkpoint_digest=self.reconciliation_checkpoint_digest,
            evidence_digest=self.page_digest,
            state_marker=state_marker,
        )
        if self.reconciliation_chain_digest != expected_reconciliation_chain:
            raise CollectionV2ContractError("reconciliation_chain_drift")
        _require_bounded_payload(self)

    @property
    def payload_json(self) -> str:
        return _canonical_payload(self)


@dataclass(frozen=True)
class CollectionV2ReconciliationRequest:
    """A compact request to settle send truth before dispatch may continue or stop."""

    schema_version: str
    tenant_pub_id: str
    campaign_pub_id: str
    partition_pub_id: str
    partition_digest: str
    membership_digest: str
    cursor: int
    checkpoint_ref: str
    checkpoint_digest: str
    checkpoint_version: int
    checkpoint_chain_digest: str
    reconciliation_checkpoint_ref: str
    reconciliation_checkpoint_digest: str
    reconciliation_checkpoint_version: int
    reconciliation_chain_digest: str
    control_policy_revision: str

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, COLLECTION_V2_RECONCILIATION_REQUEST_SCHEMA)
        for field in (
            "tenant_pub_id",
            "campaign_pub_id",
            "partition_pub_id",
            "checkpoint_ref",
            "reconciliation_checkpoint_ref",
            "control_policy_revision",
        ):
            _require_ref(getattr(self, field), field=field)
        for field in (
            "partition_digest",
            "membership_digest",
            "checkpoint_digest",
            "checkpoint_chain_digest",
            "reconciliation_checkpoint_digest",
            "reconciliation_chain_digest",
        ):
            _require_digest(getattr(self, field), field=field)
        _require_int(
            self.cursor,
            field="cursor",
            minimum=0,
            maximum=MAX_COLLECTION_V2_CHECKPOINT_VERSION,
        )
        for field in ("checkpoint_version", "reconciliation_checkpoint_version"):
            _require_int(
                getattr(self, field),
                field=field,
                minimum=0,
                maximum=MAX_COLLECTION_V2_CHECKPOINT_VERSION,
            )
        _require_bounded_payload(self)

    @property
    def payload_json(self) -> str:
        return _canonical_payload(self)


@dataclass(frozen=True)
class CollectionV2ReconciliationReceipt:
    """Exact reconciliation transition; pending is durable, never workflow-terminal."""

    schema_version: str
    campaign_pub_id: str
    partition_pub_id: str
    partition_digest: str
    cursor: int
    prior_checkpoint_ref: str
    prior_checkpoint_digest: str
    prior_checkpoint_version: int
    prior_checkpoint_chain_digest: str
    checkpoint_ref: str
    checkpoint_digest: str
    checkpoint_version: int
    checkpoint_chain_digest: str
    prior_reconciliation_checkpoint_ref: str
    prior_reconciliation_checkpoint_digest: str
    prior_reconciliation_checkpoint_version: int
    prior_reconciliation_chain_digest: str
    reconciliation_checkpoint_ref: str
    reconciliation_checkpoint_digest: str
    reconciliation_checkpoint_version: int
    reconciliation_chain_digest: str
    reconciliation_evidence_digest: str
    state: Literal["settled", "pending"]
    outcome_ref: str | None = None

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, COLLECTION_V2_RECONCILIATION_RECEIPT_SCHEMA)
        for field in (
            "campaign_pub_id",
            "partition_pub_id",
            "prior_checkpoint_ref",
            "checkpoint_ref",
            "prior_reconciliation_checkpoint_ref",
            "reconciliation_checkpoint_ref",
        ):
            _require_ref(getattr(self, field), field=field)
        for field in (
            "partition_digest",
            "prior_checkpoint_digest",
            "prior_checkpoint_chain_digest",
            "checkpoint_digest",
            "checkpoint_chain_digest",
            "prior_reconciliation_checkpoint_digest",
            "prior_reconciliation_chain_digest",
            "reconciliation_checkpoint_digest",
            "reconciliation_chain_digest",
            "reconciliation_evidence_digest",
        ):
            _require_digest(getattr(self, field), field=field)
        for field in (
            "cursor",
            "prior_checkpoint_version",
            "checkpoint_version",
            "prior_reconciliation_checkpoint_version",
            "reconciliation_checkpoint_version",
        ):
            _require_int(
                getattr(self, field),
                field=field,
                minimum=0,
                maximum=MAX_COLLECTION_V2_CHECKPOINT_VERSION,
            )
        if self.state not in {"settled", "pending"}:
            raise CollectionV2ContractError("invalid_reconciliation_state")
        if self.outcome_ref is not None:
            _require_ref(self.outcome_ref, field="outcome_ref")
        if self.state == "settled" and self.outcome_ref is None:
            raise CollectionV2ContractError("settled_reconciliation_requires_outcome_ref")
        if self.state == "pending" and self.outcome_ref is not None:
            raise CollectionV2ContractError("pending_reconciliation_forbids_outcome_ref")
        if self.checkpoint_version != self.prior_checkpoint_version + 1:
            raise CollectionV2ContractError("checkpoint_version_not_monotonic")
        if (
            self.reconciliation_checkpoint_version
            != self.prior_reconciliation_checkpoint_version + 1
        ):
            raise CollectionV2ContractError("reconciliation_version_not_monotonic")
        expected_checkpoint_chain = advance_collection_v2_checkpoint_chain(
            prior_chain_digest=self.prior_checkpoint_chain_digest,
            partition_digest=self.partition_digest,
            prior_cursor=self.cursor,
            next_cursor=self.cursor,
            prior_version=self.prior_checkpoint_version,
            next_version=self.checkpoint_version,
            checkpoint_ref=self.checkpoint_ref,
            checkpoint_digest=self.checkpoint_digest,
            evidence_digest=self.reconciliation_evidence_digest,
            event_kind="reconciliation",
        )
        if self.checkpoint_chain_digest != expected_checkpoint_chain:
            raise CollectionV2ContractError("checkpoint_chain_drift")
        state_marker: ReconciliationStateMarker = (
            "reconciliation_settled" if self.state == "settled" else "reconciliation_pending"
        )
        expected_reconciliation_chain = advance_collection_v2_reconciliation_chain(
            prior_chain_digest=self.prior_reconciliation_chain_digest,
            partition_digest=self.partition_digest,
            cursor=self.cursor,
            prior_version=self.prior_reconciliation_checkpoint_version,
            next_version=self.reconciliation_checkpoint_version,
            reconciliation_checkpoint_ref=self.reconciliation_checkpoint_ref,
            reconciliation_checkpoint_digest=self.reconciliation_checkpoint_digest,
            evidence_digest=self.reconciliation_evidence_digest,
            state_marker=state_marker,
        )
        if self.reconciliation_chain_digest != expected_reconciliation_chain:
            raise CollectionV2ContractError("reconciliation_chain_drift")
        _require_bounded_payload(self)

    @property
    def payload_json(self) -> str:
        return _canonical_payload(self)


@dataclass(frozen=True)
class CollectionV2FinalizationRequest:
    """Constant-size request for the DB-owned terminal partition proof."""

    schema_version: str
    tenant_pub_id: str
    campaign_pub_id: str
    partition_pub_id: str
    partition_digest: str
    membership_digest: str
    cursor: int
    end_slot_ordinal_exclusive: int
    checkpoint_ref: str
    checkpoint_digest: str
    checkpoint_version: int
    checkpoint_chain_digest: str
    reconciliation_checkpoint_ref: str
    reconciliation_checkpoint_digest: str
    reconciliation_checkpoint_version: int
    reconciliation_chain_digest: str

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, COLLECTION_V2_FINALIZATION_REQUEST_SCHEMA)
        for field in (
            "tenant_pub_id",
            "campaign_pub_id",
            "partition_pub_id",
            "checkpoint_ref",
            "reconciliation_checkpoint_ref",
        ):
            _require_ref(getattr(self, field), field=field)
        for field in (
            "partition_digest",
            "membership_digest",
            "checkpoint_digest",
            "checkpoint_chain_digest",
            "reconciliation_checkpoint_digest",
            "reconciliation_chain_digest",
        ):
            _require_digest(getattr(self, field), field=field)
        for field in (
            "cursor",
            "end_slot_ordinal_exclusive",
            "checkpoint_version",
            "reconciliation_checkpoint_version",
        ):
            _require_int(
                getattr(self, field),
                field=field,
                minimum=0,
                maximum=MAX_COLLECTION_V2_CHECKPOINT_VERSION,
            )
        if self.cursor != self.end_slot_ordinal_exclusive:
            raise CollectionV2ContractError("finalization_cursor_not_at_partition_end")
        _require_bounded_payload(self)

    @property
    def payload_json(self) -> str:
        return _canonical_payload(self)


@dataclass(frozen=True)
class CollectionV2FinalizationReceipt:
    """DB proof that the exact partition/checkpoint chain is terminal."""

    schema_version: str
    campaign_pub_id: str
    partition_pub_id: str
    partition_digest: str
    cursor: int
    checkpoint_ref: str
    checkpoint_digest: str
    checkpoint_version: int
    checkpoint_chain_digest: str
    reconciliation_checkpoint_ref: str
    reconciliation_checkpoint_digest: str
    reconciliation_checkpoint_version: int
    reconciliation_chain_digest: str
    terminal_proof_ref: str
    terminal_proof_digest: str
    terminal_proof_version: int
    terminal_chain_digest: str

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, COLLECTION_V2_FINALIZATION_RECEIPT_SCHEMA)
        for field in (
            "campaign_pub_id",
            "partition_pub_id",
            "checkpoint_ref",
            "reconciliation_checkpoint_ref",
            "terminal_proof_ref",
        ):
            _require_ref(getattr(self, field), field=field)
        for field in (
            "partition_digest",
            "checkpoint_digest",
            "checkpoint_chain_digest",
            "reconciliation_checkpoint_digest",
            "reconciliation_chain_digest",
            "terminal_proof_digest",
            "terminal_chain_digest",
        ):
            _require_digest(getattr(self, field), field=field)
        for field in (
            "cursor",
            "checkpoint_version",
            "reconciliation_checkpoint_version",
            "terminal_proof_version",
        ):
            _require_int(
                getattr(self, field),
                field=field,
                minimum=1 if field == "terminal_proof_version" else 0,
                maximum=MAX_COLLECTION_V2_CHECKPOINT_VERSION,
            )
        expected_terminal_chain = collection_v2_terminal_chain_digest(
            partition_digest=self.partition_digest,
            cursor=self.cursor,
            checkpoint_chain_digest=self.checkpoint_chain_digest,
            reconciliation_chain_digest=self.reconciliation_chain_digest,
            terminal_proof_ref=self.terminal_proof_ref,
            terminal_proof_digest=self.terminal_proof_digest,
            terminal_proof_version=self.terminal_proof_version,
        )
        if self.terminal_chain_digest != expected_terminal_chain:
            raise CollectionV2ContractError("terminal_chain_drift")
        _require_bounded_payload(self)

    @property
    def payload_json(self) -> str:
        return _canonical_payload(self)


@activity.defn(name="execute_collection_v2_page")
async def execute_collection_v2_page(
    request: CollectionV2PageRequest,
) -> CollectionV2PageReceipt:
    """Fail closed while durable per-item active-page control is absent."""

    del request
    raise ApplicationError(
        "collection v2 active-page control is not wired",
        type="collection_v2_active_page_control_not_wired",
        non_retryable=True,
    )


@activity.defn(name="reconcile_collection_v2_partition")
async def reconcile_collection_v2_partition(
    request: CollectionV2ReconciliationRequest,
) -> CollectionV2ReconciliationReceipt:
    """Fail closed until durable Stage 3/4 reconciliation is wired."""

    del request
    raise ApplicationError(
        "collection v2 reconciliation adapter is not configured",
        type="collection_v2_reconciliation_not_configured",
        non_retryable=True,
    )


@activity.defn(name="verify_collection_v2_partition_complete")
async def verify_collection_v2_partition_complete(
    request: CollectionV2FinalizationRequest,
) -> CollectionV2FinalizationReceipt:
    """Fail closed until a restricted DB terminal-proof entry point exists."""

    del request
    raise ApplicationError(
        "collection v2 terminal proof adapter is not configured",
        type="collection_v2_terminal_proof_not_configured",
        non_retryable=True,
    )


__all__ = [
    "COLLECTION_V2_ACTIVE_PAGE_CONTROL_GATE",
    "COLLECTION_V2_FINALIZATION_RECEIPT_SCHEMA",
    "COLLECTION_V2_FINALIZATION_REQUEST_SCHEMA",
    "COLLECTION_V2_PAGE_RECEIPT_SCHEMA",
    "COLLECTION_V2_PAGE_REQUEST_SCHEMA",
    "COLLECTION_V2_RECONCILIATION_RECEIPT_SCHEMA",
    "COLLECTION_V2_RECONCILIATION_REQUEST_SCHEMA",
    "MAX_COLLECTION_V2_ACTIVITY_PAYLOAD_BYTES",
    "MAX_COLLECTION_V2_CHECKPOINT_VERSION",
    "MAX_COLLECTION_V2_PAGE_SIZE",
    "CollectionV2ContractError",
    "CollectionV2FinalizationReceipt",
    "CollectionV2FinalizationRequest",
    "CollectionV2PageReceipt",
    "CollectionV2PageRequest",
    "CollectionV2ReconciliationReceipt",
    "CollectionV2ReconciliationRequest",
    "advance_collection_v2_checkpoint_chain",
    "advance_collection_v2_reconciliation_chain",
    "collection_v2_terminal_chain_digest",
    "execute_collection_v2_page",
    "initial_collection_v2_reconciliation_checkpoint_digest",
    "reconcile_collection_v2_partition",
    "seed_collection_v2_checkpoint_chain",
    "seed_collection_v2_reconciliation_chain",
    "verify_collection_v2_partition_complete",
]
