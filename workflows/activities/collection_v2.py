"""Constant-size activity contracts for the isolated collection v2 worker.

The production entry points deliberately fail closed.  Stage 4 can exercise the
Temporal protocol with deterministic fixture activities, but no real collection
I/O is authorized until a persisted execution partition and its restricted
database adapters are wired to these exact contracts.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

from temporalio import activity
from temporalio.exceptions import ApplicationError

COLLECTION_V2_PAGE_REQUEST_SCHEMA = "collection-page-request-v2"
COLLECTION_V2_PAGE_RECEIPT_SCHEMA = "collection-page-receipt-v2"
COLLECTION_V2_RECONCILIATION_REQUEST_SCHEMA = "collection-reconciliation-request-v2"
COLLECTION_V2_RECONCILIATION_RECEIPT_SCHEMA = "collection-reconciliation-receipt-v2"

MAX_COLLECTION_V2_PAGE_SIZE = 2_048
MAX_COLLECTION_V2_ACTIVITY_PAYLOAD_BYTES = 8_192

_OPAQUE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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


def _canonical_payload(instance: Any) -> str:
    return json.dumps(asdict(instance), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _require_bounded_payload(instance: Any) -> None:
    if len(_canonical_payload(instance).encode("utf-8")) > MAX_COLLECTION_V2_ACTIVITY_PAYLOAD_BYTES:
        raise CollectionV2ContractError("activity_payload_too_large")


@dataclass(frozen=True)
class CollectionV2PageRequest:
    """A bounded page command containing references, never slots or query text."""

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
    reconciliation_checkpoint_ref: str
    capability_policy_revision: str
    control_policy_revision: str

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
        for field in ("partition_digest", "membership_digest", "checkpoint_digest"):
            _require_digest(getattr(self, field), field=field)
        _require_int(self.cursor, field="cursor", minimum=0)
        _require_int(
            self.page_size,
            field="page_size",
            minimum=1,
            maximum=MAX_COLLECTION_V2_PAGE_SIZE,
        )
        _require_bounded_payload(self)

    @property
    def payload_json(self) -> str:
        return _canonical_payload(self)


@dataclass(frozen=True)
class CollectionV2PageReceipt:
    """Constant-size durable receipt for one bounded partition page."""

    schema_version: str
    campaign_pub_id: str
    partition_pub_id: str
    partition_digest: str
    prior_cursor: int
    next_cursor: int
    page_item_count: int
    page_digest: str
    checkpoint_ref: str
    checkpoint_digest: str
    reconciliation_checkpoint_ref: str
    requires_reconciliation: bool = False

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, COLLECTION_V2_PAGE_RECEIPT_SCHEMA)
        for field in (
            "campaign_pub_id",
            "partition_pub_id",
            "checkpoint_ref",
            "reconciliation_checkpoint_ref",
        ):
            _require_ref(getattr(self, field), field=field)
        for field in ("partition_digest", "page_digest", "checkpoint_digest"):
            _require_digest(getattr(self, field), field=field)
        _require_int(self.prior_cursor, field="prior_cursor", minimum=0)
        _require_int(self.next_cursor, field="next_cursor", minimum=1)
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
        if not isinstance(self.requires_reconciliation, bool):
            raise CollectionV2ContractError("boolean_required:requires_reconciliation")
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
    reconciliation_checkpoint_ref: str
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
        for field in ("partition_digest", "membership_digest", "checkpoint_digest"):
            _require_digest(getattr(self, field), field=field)
        _require_int(self.cursor, field="cursor", minimum=0)
        _require_bounded_payload(self)

    @property
    def payload_json(self) -> str:
        return _canonical_payload(self)


@dataclass(frozen=True)
class CollectionV2ReconciliationReceipt:
    """Constant-size reconciliation outcome; no operation state map is returned."""

    schema_version: str
    campaign_pub_id: str
    partition_pub_id: str
    partition_digest: str
    cursor: int
    checkpoint_ref: str
    checkpoint_digest: str
    reconciliation_checkpoint_ref: str
    state: Literal["settled", "pending"]
    outcome_ref: str | None = None

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, COLLECTION_V2_RECONCILIATION_RECEIPT_SCHEMA)
        for field in (
            "campaign_pub_id",
            "partition_pub_id",
            "checkpoint_ref",
            "reconciliation_checkpoint_ref",
        ):
            _require_ref(getattr(self, field), field=field)
        for field in ("partition_digest", "checkpoint_digest"):
            _require_digest(getattr(self, field), field=field)
        _require_int(self.cursor, field="cursor", minimum=0)
        if self.state not in {"settled", "pending"}:
            raise CollectionV2ContractError("invalid_reconciliation_state")
        if self.outcome_ref is not None:
            _require_ref(self.outcome_ref, field="outcome_ref")
        if self.state == "settled" and self.outcome_ref is None:
            raise CollectionV2ContractError("settled_reconciliation_requires_outcome_ref")
        if self.state == "pending" and self.outcome_ref is not None:
            raise CollectionV2ContractError("pending_reconciliation_forbids_outcome_ref")
        _require_bounded_payload(self)

    @property
    def payload_json(self) -> str:
        return _canonical_payload(self)


@activity.defn(name="execute_collection_v2_page")
async def execute_collection_v2_page(
    request: CollectionV2PageRequest,
) -> CollectionV2PageReceipt:
    """Fail closed until the persisted Stage 4 partition adapter is installed."""

    del request
    raise ApplicationError(
        "collection v2 partition executor is not configured",
        type="collection_v2_partition_executor_not_configured",
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


__all__ = [
    "COLLECTION_V2_PAGE_RECEIPT_SCHEMA",
    "COLLECTION_V2_PAGE_REQUEST_SCHEMA",
    "COLLECTION_V2_RECONCILIATION_RECEIPT_SCHEMA",
    "COLLECTION_V2_RECONCILIATION_REQUEST_SCHEMA",
    "MAX_COLLECTION_V2_ACTIVITY_PAYLOAD_BYTES",
    "MAX_COLLECTION_V2_PAGE_SIZE",
    "CollectionV2ContractError",
    "CollectionV2PageReceipt",
    "CollectionV2PageRequest",
    "CollectionV2ReconciliationReceipt",
    "CollectionV2ReconciliationRequest",
    "execute_collection_v2_page",
    "reconcile_collection_v2_partition",
]
