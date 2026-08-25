"""Encrypted, owner-local physical WAL for authorization and send truth.

The store is intentionally independent of PostgreSQL and Temporal.  It writes
the exact pre-CAS authorization record to an owner-local filesystem before the
database claim may reference its evidence hash.  After CAS it appends one
immutable send-boundary record before transport and, if transport returns, one
immutable outcome record.  Records are envelope-encrypted, atomically replaced
into place, fsynced, immutable by dispatch reference, and re-read from disk for
every authorization, submission, and reconciliation check.

Deletion is deliberately absent.  The sealed retention floor is exposed for a
future terminal-proof-aware GC, but this store never treats wall-clock expiry as
permission to remove evidence that may still be required to reconcile SENDING.
"""

from __future__ import annotations

import base64
import fcntl
import os
import re
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol, Self
from uuid import UUID

from cryptography.exceptions import InvalidTag
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from domain.collection.submission import OpaqueId, Sha256Hex, authority_digest, canonical_json
from domain.collection.surface import CollectionSurface

from ..config import Settings
from .resource_owner_gateway_v2 import (
    FreshSubmissionOwnerSendBoundary,
    ResourceOwnerGatewayError,
    SubmissionOwnerAuthorizationWalRecord,
    SubmissionOwnerSendBoundaryRecord,
    SubmissionOwnerSendJournalSnapshot,
    SubmissionOwnerSendOutcomeRecord,
)
from .vault import (
    KmsUnavailableError,
    LocalKms,
    ProfileVault,
    SealedProfile,
    VaultTransitKms,
    profile_aad,
)

OWNER_AUTHORIZATION_WAL_ENVELOPE_SCHEMA: Literal[
    "collection-owner-authorization-wal-envelope-v1"
] = "collection-owner-authorization-wal-envelope-v1"
MAX_OWNER_AUTHORIZATION_WAL_PLAINTEXT_BYTES = 512 * 1024
MAX_OWNER_AUTHORIZATION_WAL_ENVELOPE_BYTES = 1024 * 1024

_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_WAL_FILENAME_RE = re.compile(r"^(?P<lookup>[0-9a-f]{64})\.wal$")
_SEND_BOUNDARY_FILENAME_RE = re.compile(r"^(?P<lookup>[0-9a-f]{64})\.send-boundary\.wal$")
_SEND_OUTCOME_FILENAME_RE = re.compile(r"^(?P<lookup>[0-9a-f]{64})\.send-outcome\.wal$")
_LOCK_FILENAME_RE = re.compile(r"^\.(?P<lookup>[0-9a-f]{64})\.lock$")
_MAX_RETENTION = timedelta(days=3650)


class OwnerAuthorizationWalVault(Protocol):
    """Envelope cipher used by the physical WAL; production supplies KMS."""

    def seal(self, plaintext: bytes, aad: bytes) -> SealedProfile: ...

    def open(self, sealed: SealedProfile, aad: bytes) -> bytes: ...


class OwnerAuthorizationWalRetentionMetadata(BaseModel):
    """Integrity-bound lifecycle floor; it is not permission to delete a WAL."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    policy_revision: OpaqueId
    retain_until: datetime
    evidence_sha256: Sha256Hex

    @field_validator("retain_until")
    @classmethod
    def retain_until_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("owner_authorization_wal_retention_time_must_be_aware")
        return value


class OwnerAuthorizationWalStartupAudit(BaseModel):
    """Bounded proof that every retained physical record was readable."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    schema_version: Literal["collection-owner-wal-startup-audit-v2"] = (
        "collection-owner-wal-startup-audit-v2"
    )
    record_count: int = Field(strict=True, ge=0)
    authorization_record_count: int = Field(strict=True, ge=0)
    send_boundary_record_count: int = Field(strict=True, ge=0)
    send_outcome_record_count: int = Field(strict=True, ge=0)
    record_set_sha256: Sha256Hex
    earliest_retain_until: datetime | None = None
    latest_retain_until: datetime | None = None

    @model_validator(mode="after")
    def retention_range_matches_count(self) -> Self:
        if self.record_count != (
            self.authorization_record_count
            + self.send_boundary_record_count
            + self.send_outcome_record_count
        ):
            raise ValueError("owner_wal_audit_record_count_mismatch")
        if self.send_outcome_record_count > self.send_boundary_record_count:
            raise ValueError("owner_wal_audit_outcome_count_invalid")
        if self.record_count == 0:
            if self.earliest_retain_until is not None or self.latest_retain_until is not None:
                raise ValueError("empty_owner_wal_audit_cannot_have_retention_range")
            return self
        if self.earliest_retain_until is None or self.latest_retain_until is None:
            raise ValueError("owner_wal_audit_retention_range_missing")
        for value in (self.earliest_retain_until, self.latest_retain_until):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("owner_wal_audit_retention_time_must_be_aware")
        if self.latest_retain_until < self.earliest_retain_until:
            raise ValueError("owner_wal_audit_retention_range_invalid")
        return self


class _OwnerAuthorizationWalEnvelopeMetadata(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    schema_version: Literal["collection-owner-authorization-wal-envelope-v1"] = (
        OWNER_AUTHORIZATION_WAL_ENVELOPE_SCHEMA
    )
    lookup_sha256: Sha256Hex
    tenant_id: UUID
    project_id: UUID
    owner_gateway_pub_id: OpaqueId
    collection_surface: CollectionSurface
    encryption_key_revision: int = Field(strict=True, ge=1, le=1_000_000)
    evidence_sha256: Sha256Hex
    retention: OwnerAuthorizationWalRetentionMetadata

    @model_validator(mode="after")
    def retention_matches_record(self) -> Self:
        if self.retention.evidence_sha256 != self.evidence_sha256:
            raise ValueError("owner_authorization_wal_retention_evidence_mismatch")
        return self


class _OwnerAuthorizationWalEnvelope(_OwnerAuthorizationWalEnvelopeMetadata):
    ciphertext_b64: str = Field(min_length=1, max_length=MAX_OWNER_AUTHORIZATION_WAL_ENVELOPE_BYTES)
    nonce_b64: str = Field(min_length=1, max_length=64)
    wrapped_dek_b64: str = Field(
        min_length=1,
        max_length=MAX_OWNER_AUTHORIZATION_WAL_ENVELOPE_BYTES,
    )
    ciphertext_sha256: Sha256Hex

    @model_validator(mode="after")
    def sealed_payload_is_bounded(self) -> Self:
        ciphertext = _decode_base64(self.ciphertext_b64)
        nonce = _decode_base64(self.nonce_b64)
        wrapped_dek = _decode_base64(self.wrapped_dek_b64)
        if (
            not ciphertext
            or len(ciphertext) > MAX_OWNER_AUTHORIZATION_WAL_PLAINTEXT_BYTES + 32
            or len(nonce) != 12
            or not wrapped_dek
            or len(wrapped_dek) > MAX_OWNER_AUTHORIZATION_WAL_ENVELOPE_BYTES
        ):
            raise ValueError("owner_authorization_wal_sealed_payload_invalid")
        if sha256(ciphertext).hexdigest() != self.ciphertext_sha256:
            raise ValueError("owner_authorization_wal_ciphertext_digest_mismatch")
        return self


class _OwnerSendJournalEnvelopeMetadata(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    schema_version: Literal["collection-owner-send-journal-envelope-v1"] = (
        "collection-owner-send-journal-envelope-v1"
    )
    record_kind: Literal["boundary", "outcome"]
    lookup_sha256: Sha256Hex
    tenant_id: UUID
    project_id: UUID
    owner_gateway_pub_id: OpaqueId
    collection_surface: CollectionSurface
    encryption_key_revision: int = Field(strict=True, ge=1, le=1_000_000)
    owner_authorization_evidence_sha256: Sha256Hex
    record_evidence_sha256: Sha256Hex
    retention: OwnerAuthorizationWalRetentionMetadata

    @model_validator(mode="after")
    def retention_matches_record(self) -> Self:
        if self.retention.evidence_sha256 != self.record_evidence_sha256:
            raise ValueError("owner_send_journal_retention_evidence_mismatch")
        return self


class _OwnerSendJournalEnvelope(_OwnerSendJournalEnvelopeMetadata):
    ciphertext_b64: str = Field(min_length=1, max_length=MAX_OWNER_AUTHORIZATION_WAL_ENVELOPE_BYTES)
    nonce_b64: str = Field(min_length=1, max_length=64)
    wrapped_dek_b64: str = Field(
        min_length=1,
        max_length=MAX_OWNER_AUTHORIZATION_WAL_ENVELOPE_BYTES,
    )
    ciphertext_sha256: Sha256Hex

    @model_validator(mode="after")
    def sealed_payload_is_bounded(self) -> Self:
        ciphertext = _decode_base64(self.ciphertext_b64)
        nonce = _decode_base64(self.nonce_b64)
        wrapped_dek = _decode_base64(self.wrapped_dek_b64)
        if (
            not ciphertext
            or len(ciphertext) > MAX_OWNER_AUTHORIZATION_WAL_PLAINTEXT_BYTES + 32
            or len(nonce) != 12
            or not wrapped_dek
            or len(wrapped_dek) > MAX_OWNER_AUTHORIZATION_WAL_ENVELOPE_BYTES
        ):
            raise ValueError("owner_send_journal_sealed_payload_invalid")
        if sha256(ciphertext).hexdigest() != self.ciphertext_sha256:
            raise ValueError("owner_send_journal_ciphertext_digest_mismatch")
        return self


def _decode_base64(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("owner_authorization_wal_base64_invalid") from exc
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError("owner_authorization_wal_base64_noncanonical")
    return decoded


def _lookup_sha256(owner_dispatch_ref: str) -> str:
    if _OPAQUE_ID_RE.fullmatch(owner_dispatch_ref) is None:
        raise ResourceOwnerGatewayError("resource_owner_authorization_wal_dispatch_ref_invalid")
    return sha256(
        f"collection-owner-authorization-wal-lookup-v1|{owner_dispatch_ref}".encode()
    ).hexdigest()


def _envelope_aad(metadata: _OwnerAuthorizationWalEnvelopeMetadata) -> bytes:
    metadata_view = _OwnerAuthorizationWalEnvelopeMetadata(
        schema_version=metadata.schema_version,
        lookup_sha256=metadata.lookup_sha256,
        tenant_id=metadata.tenant_id,
        project_id=metadata.project_id,
        owner_gateway_pub_id=metadata.owner_gateway_pub_id,
        collection_surface=metadata.collection_surface,
        encryption_key_revision=metadata.encryption_key_revision,
        evidence_sha256=metadata.evidence_sha256,
        retention=metadata.retention,
    )
    metadata_sha256 = sha256(canonical_json(metadata_view).encode()).hexdigest()
    return profile_aad(
        str(metadata.tenant_id),
        f"collection-owner-wal-{metadata_sha256}",
        metadata.collection_surface.value,
        metadata.owner_gateway_pub_id,
        metadata.encryption_key_revision,
    )


def _send_journal_envelope_aad(metadata: _OwnerSendJournalEnvelopeMetadata) -> bytes:
    metadata_view = _OwnerSendJournalEnvelopeMetadata(
        schema_version=metadata.schema_version,
        record_kind=metadata.record_kind,
        lookup_sha256=metadata.lookup_sha256,
        tenant_id=metadata.tenant_id,
        project_id=metadata.project_id,
        owner_gateway_pub_id=metadata.owner_gateway_pub_id,
        collection_surface=metadata.collection_surface,
        encryption_key_revision=metadata.encryption_key_revision,
        owner_authorization_evidence_sha256=(metadata.owner_authorization_evidence_sha256),
        record_evidence_sha256=metadata.record_evidence_sha256,
        retention=metadata.retention,
    )
    metadata_sha256 = sha256(canonical_json(metadata_view).encode()).hexdigest()
    return profile_aad(
        str(metadata.tenant_id),
        f"collection-owner-send-journal-{metadata.record_kind}-{metadata_sha256}",
        metadata.collection_surface.value,
        metadata.owner_gateway_pub_id,
        metadata.encryption_key_revision,
    )


class EncryptedFileSubmissionOwnerAuthorizationWalStore:
    """Secure physical implementation of authorization and send journal ports.

    ``root`` must be a dedicated absolute directory whose parent already exists.
    A vault is mandatory: there is no plaintext or production-degrading mode.
    Exact replay returns the existing record; a different record for the same
    owner dispatch reference fails before the caller can perform its claim CAS.
    A send boundary is never replayable: once its file exists, no later gateway
    invocation can receive a fresh-boundary capability or call transport again.
    """

    def __init__(
        self,
        root: Path,
        *,
        vault: OwnerAuthorizationWalVault,
        retention_period: timedelta,
        retention_policy_revision: str,
        encryption_key_revision: int = 1,
    ) -> None:
        if (
            not root.is_absolute()
            or root == root.parent
            or retention_period <= timedelta(0)
            or retention_period > _MAX_RETENTION
            or _OPAQUE_ID_RE.fullmatch(retention_policy_revision) is None
            or isinstance(encryption_key_revision, bool)
            or not 1 <= encryption_key_revision <= 1_000_000
        ):
            raise ResourceOwnerGatewayError(
                "resource_owner_authorization_wal_configuration_invalid"
            )
        self._root = root
        self._vault = vault
        self._retention_period = retention_period
        self._retention_policy_revision = retention_policy_revision
        self._encryption_key_revision = encryption_key_revision
        self._prepare_root()

    def put(
        self,
        record: SubmissionOwnerAuthorizationWalRecord,
    ) -> SubmissionOwnerAuthorizationWalRecord:
        if not isinstance(record, SubmissionOwnerAuthorizationWalRecord):
            raise ResourceOwnerGatewayError("resource_owner_authorization_wal_write_invalid")
        lookup_sha256 = _lookup_sha256(record.owner_dispatch_ref)
        path = self._record_path(lookup_sha256)
        with self._lock(lookup_sha256):
            existing = self._load_entry(record.owner_dispatch_ref)
            if existing is not None:
                persisted, _envelope = existing
                if persisted != record:
                    raise ResourceOwnerGatewayError(
                        "resource_owner_authorization_wal_write_conflict"
                    )
                self._fsync_directory()
                return persisted

            payload = canonical_json(record).encode()
            if not payload or len(payload) > MAX_OWNER_AUTHORIZATION_WAL_PLAINTEXT_BYTES:
                raise ResourceOwnerGatewayError("resource_owner_authorization_wal_write_invalid")
            metadata = self._metadata(record, lookup_sha256=lookup_sha256)
            try:
                sealed = self._vault.seal(payload, _envelope_aad(metadata))
            except (InvalidTag, KmsUnavailableError, OSError, ValueError) as exc:
                raise ResourceOwnerGatewayError(
                    "resource_owner_authorization_wal_encrypt_failed"
                ) from exc
            if not isinstance(sealed, SealedProfile):
                raise ResourceOwnerGatewayError("resource_owner_authorization_wal_encrypt_failed")
            envelope = _OwnerAuthorizationWalEnvelope(
                **metadata.model_dump(mode="python"),
                ciphertext_b64=base64.b64encode(sealed.ciphertext).decode("ascii"),
                nonce_b64=base64.b64encode(sealed.nonce).decode("ascii"),
                wrapped_dek_b64=base64.b64encode(sealed.wrapped_dek).decode("ascii"),
                ciphertext_sha256=sealed.sha256,
            )
            encoded = canonical_json(envelope).encode()
            if not encoded or len(encoded) > MAX_OWNER_AUTHORIZATION_WAL_ENVELOPE_BYTES:
                raise ResourceOwnerGatewayError("resource_owner_authorization_wal_write_invalid")
            self._atomic_write(path, encoded)
            return record

    def load(
        self,
        *,
        owner_dispatch_ref: str,
    ) -> SubmissionOwnerAuthorizationWalRecord | None:
        entry = self._load_entry(owner_dispatch_ref)
        return None if entry is None else entry[0]

    def retention_metadata(
        self,
        *,
        owner_dispatch_ref: str,
    ) -> OwnerAuthorizationWalRetentionMetadata | None:
        """Return integrity-checked metadata for future terminal-aware GC."""

        entry = self._load_entry(owner_dispatch_ref)
        return None if entry is None else entry[1].retention

    def append_send_boundary(
        self,
        record: SubmissionOwnerSendBoundaryRecord,
    ) -> FreshSubmissionOwnerSendBoundary:
        """Append the one-way boundary marker before invoking a transport."""

        if not isinstance(record, SubmissionOwnerSendBoundaryRecord):
            raise ResourceOwnerGatewayError("resource_owner_send_boundary_write_invalid")
        lookup_sha256 = _lookup_sha256(record.owner_dispatch_ref)
        with self._lock(lookup_sha256):
            authorization_entry = self._load_entry(record.owner_dispatch_ref)
            if authorization_entry is None:
                raise ResourceOwnerGatewayError(
                    "resource_owner_send_boundary_authorization_missing"
                )
            authorization_record, authorization_envelope = authorization_entry
            self._validate_boundary_authorization(record, authorization_record)
            existing = self._load_send_event(
                owner_dispatch_ref=record.owner_dispatch_ref,
                record_kind="boundary",
            )
            if existing is not None:
                self._validate_send_envelope_authorization(
                    existing[1],
                    authorization_record,
                    authorization_envelope,
                )
                if existing[0] != record:
                    raise ResourceOwnerGatewayError("resource_owner_send_boundary_write_conflict")
                raise ResourceOwnerGatewayError("resource_owner_send_boundary_already_entered")
            if (
                self._load_send_event(
                    owner_dispatch_ref=record.owner_dispatch_ref,
                    record_kind="outcome",
                )
                is not None
            ):
                raise ResourceOwnerGatewayError("resource_owner_send_journal_chain_invalid")
            encoded = self._encode_send_event(
                record,
                authorization_record=authorization_record,
                authorization_envelope=authorization_envelope,
                lookup_sha256=lookup_sha256,
                record_kind="boundary",
            )
            self._atomic_write(self._send_boundary_path(lookup_sha256), encoded)
            return FreshSubmissionOwnerSendBoundary(record=record)

    def append_send_outcome(
        self,
        record: SubmissionOwnerSendOutcomeRecord,
    ) -> SubmissionOwnerSendOutcomeRecord:
        """Append or exactly replay a terminal owner-local transport result."""

        if not isinstance(record, SubmissionOwnerSendOutcomeRecord):
            raise ResourceOwnerGatewayError("resource_owner_send_outcome_write_invalid")
        lookup_sha256 = _lookup_sha256(record.owner_dispatch_ref)
        with self._lock(lookup_sha256):
            authorization_entry = self._load_entry(record.owner_dispatch_ref)
            if authorization_entry is None:
                raise ResourceOwnerGatewayError("resource_owner_send_outcome_authorization_missing")
            authorization_record, authorization_envelope = authorization_entry
            boundary_entry = self._load_send_event(
                owner_dispatch_ref=record.owner_dispatch_ref,
                record_kind="boundary",
            )
            if boundary_entry is None:
                raise ResourceOwnerGatewayError("resource_owner_send_outcome_boundary_missing")
            boundary = boundary_entry[0]
            if not isinstance(boundary, SubmissionOwnerSendBoundaryRecord):
                raise ResourceOwnerGatewayError("resource_owner_send_journal_chain_invalid")
            self._validate_boundary_authorization(boundary, authorization_record)
            self._validate_send_envelope_authorization(
                boundary_entry[1],
                authorization_record,
                authorization_envelope,
            )
            self._validate_outcome_boundary(record, boundary)
            existing = self._load_send_event(
                owner_dispatch_ref=record.owner_dispatch_ref,
                record_kind="outcome",
            )
            if existing is not None:
                self._validate_send_envelope_authorization(
                    existing[1],
                    authorization_record,
                    authorization_envelope,
                )
                if existing[0] != record:
                    raise ResourceOwnerGatewayError("resource_owner_send_outcome_write_conflict")
                self._fsync_directory()
                return record
            encoded = self._encode_send_event(
                record,
                authorization_record=authorization_record,
                authorization_envelope=authorization_envelope,
                lookup_sha256=lookup_sha256,
                record_kind="outcome",
            )
            self._atomic_write(self._send_outcome_path(lookup_sha256), encoded)
            return record

    def load_send_journal(
        self,
        *,
        owner_dispatch_ref: str,
    ) -> SubmissionOwnerSendJournalSnapshot:
        """Read and validate the complete owner-local chain without caching."""

        lookup_sha256 = _lookup_sha256(owner_dispatch_ref)
        with self._lock(lookup_sha256):
            authorization_entry = self._load_entry(owner_dispatch_ref)
            if authorization_entry is None:
                raise ResourceOwnerGatewayError("resource_owner_authorization_wal_missing")
            authorization_record, authorization_envelope = authorization_entry
            boundary_entry = self._load_send_event(
                owner_dispatch_ref=owner_dispatch_ref,
                record_kind="boundary",
            )
            outcome_entry = self._load_send_event(
                owner_dispatch_ref=owner_dispatch_ref,
                record_kind="outcome",
            )
            boundary = None if boundary_entry is None else boundary_entry[0]
            outcome = None if outcome_entry is None else outcome_entry[0]
            if boundary is not None and not isinstance(boundary, SubmissionOwnerSendBoundaryRecord):
                raise ResourceOwnerGatewayError("resource_owner_send_journal_chain_invalid")
            if outcome is not None and not isinstance(outcome, SubmissionOwnerSendOutcomeRecord):
                raise ResourceOwnerGatewayError("resource_owner_send_journal_chain_invalid")
            if boundary is not None:
                self._validate_boundary_authorization(boundary, authorization_record)
                assert boundary_entry is not None
                self._validate_send_envelope_authorization(
                    boundary_entry[1],
                    authorization_record,
                    authorization_envelope,
                )
            if outcome is not None:
                if boundary is None:
                    raise ResourceOwnerGatewayError("resource_owner_send_journal_chain_invalid")
                self._validate_outcome_boundary(outcome, boundary)
                assert outcome_entry is not None
                self._validate_send_envelope_authorization(
                    outcome_entry[1],
                    authorization_record,
                    authorization_envelope,
                )
            try:
                return SubmissionOwnerSendJournalSnapshot(
                    owner_dispatch_ref=owner_dispatch_ref,
                    owner_authorization_evidence_sha256=(authorization_record.evidence_sha256),
                    boundary=boundary,
                    outcome=outcome,
                )
            except ValidationError as exc:
                raise ResourceOwnerGatewayError(
                    "resource_owner_send_journal_chain_invalid"
                ) from exc

    def verify_retained_records(self) -> OwnerAuthorizationWalStartupAudit:
        """Decrypt and validate every retained WAL before an owner reports ready."""

        try:
            paths = tuple(sorted(self._root.iterdir(), key=lambda item: item.name))
        except OSError as exc:
            raise ResourceOwnerGatewayError(
                "resource_owner_authorization_wal_startup_audit_failed"
            ) from exc
        retained: list[tuple[str, str, str, datetime]] = []
        authorization_records: dict[str, SubmissionOwnerAuthorizationWalRecord] = {}
        authorization_envelopes: dict[str, _OwnerAuthorizationWalEnvelope] = {}
        boundary_records: dict[str, SubmissionOwnerSendBoundaryRecord] = {}
        boundary_envelopes: dict[str, _OwnerSendJournalEnvelope] = {}
        outcome_records: dict[str, SubmissionOwnerSendOutcomeRecord] = {}
        outcome_envelopes: dict[str, _OwnerSendJournalEnvelope] = {}
        for path in paths:
            wal_match = _WAL_FILENAME_RE.fullmatch(path.name)
            if wal_match is not None:
                lookup_sha256 = wal_match.group("lookup")
                try:
                    with self._lock(lookup_sha256):
                        encoded = self._read_file(path)
                        record, envelope = self._decode_entry(
                            encoded,
                            lookup_sha256=lookup_sha256,
                            expected_owner_dispatch_ref=None,
                        )
                except FileNotFoundError as exc:
                    raise ResourceOwnerGatewayError(
                        "resource_owner_authorization_wal_startup_audit_failed"
                    ) from exc
                retained.append(
                    (
                        "authorization",
                        lookup_sha256,
                        record.evidence_sha256,
                        envelope.retention.retain_until.astimezone(UTC),
                    )
                )
                authorization_records[lookup_sha256] = record
                authorization_envelopes[lookup_sha256] = envelope
                continue
            boundary_match = _SEND_BOUNDARY_FILENAME_RE.fullmatch(path.name)
            outcome_match = _SEND_OUTCOME_FILENAME_RE.fullmatch(path.name)
            if boundary_match is not None or outcome_match is not None:
                record_kind: Literal["boundary", "outcome"] = (
                    "boundary" if boundary_match is not None else "outcome"
                )
                match = boundary_match if boundary_match is not None else outcome_match
                assert match is not None
                lookup_sha256 = match.group("lookup")
                try:
                    with self._lock(lookup_sha256):
                        encoded = self._read_file(path)
                        send_record, send_envelope = self._decode_send_event(
                            encoded,
                            lookup_sha256=lookup_sha256,
                            expected_owner_dispatch_ref=None,
                            record_kind=record_kind,
                        )
                except FileNotFoundError as exc:
                    raise ResourceOwnerGatewayError(
                        "resource_owner_authorization_wal_startup_audit_failed"
                    ) from exc
                retained.append(
                    (
                        record_kind,
                        lookup_sha256,
                        send_record.evidence_sha256,
                        send_envelope.retention.retain_until.astimezone(UTC),
                    )
                )
                if isinstance(send_record, SubmissionOwnerSendBoundaryRecord):
                    boundary_records[lookup_sha256] = send_record
                    boundary_envelopes[lookup_sha256] = send_envelope
                else:
                    outcome_records[lookup_sha256] = send_record
                    outcome_envelopes[lookup_sha256] = send_envelope
                continue
            if _LOCK_FILENAME_RE.fullmatch(path.name) is not None:
                self._validate_lock_file(path)
                continue
            raise ResourceOwnerGatewayError("resource_owner_authorization_wal_startup_audit_failed")

        try:
            for lookup_sha256, boundary in boundary_records.items():
                authorization = authorization_records[lookup_sha256]
                self._validate_boundary_authorization(boundary, authorization)
                self._validate_send_envelope_authorization(
                    boundary_envelopes[lookup_sha256],
                    authorization,
                    authorization_envelopes[lookup_sha256],
                )
            for lookup_sha256, outcome in outcome_records.items():
                boundary = boundary_records[lookup_sha256]
                self._validate_outcome_boundary(outcome, boundary)
                self._validate_send_envelope_authorization(
                    outcome_envelopes[lookup_sha256],
                    authorization_records[lookup_sha256],
                    authorization_envelopes[lookup_sha256],
                )
        except (KeyError, ResourceOwnerGatewayError) as exc:
            raise ResourceOwnerGatewayError(
                "resource_owner_authorization_wal_startup_audit_failed"
            ) from exc

        digest = sha256(b"collection-owner-wal-startup-audit-v2\n")
        for retained_kind, lookup_sha256, evidence_sha256, retain_until in retained:
            digest.update(
                canonical_json(
                    {
                        "evidence_sha256": evidence_sha256,
                        "lookup_sha256": lookup_sha256,
                        "record_kind": retained_kind,
                        "retain_until": retain_until,
                    }
                ).encode()
            )
            digest.update(b"\n")
        retention_times = [item[3] for item in retained]
        return OwnerAuthorizationWalStartupAudit(
            record_count=len(retained),
            authorization_record_count=len(authorization_records),
            send_boundary_record_count=len(boundary_records),
            send_outcome_record_count=len(outcome_records),
            record_set_sha256=digest.hexdigest(),
            earliest_retain_until=min(retention_times) if retention_times else None,
            latest_retain_until=max(retention_times) if retention_times else None,
        )

    def _metadata(
        self,
        record: SubmissionOwnerAuthorizationWalRecord,
        *,
        lookup_sha256: str,
    ) -> _OwnerAuthorizationWalEnvelopeMetadata:
        authorization = record.owner_authorization
        try:
            retain_until = max(
                authorization.authority.valid_until,
                record.recorded_at + self._retention_period,
            ).astimezone(UTC)
        except (OverflowError, ValueError) as exc:
            raise ResourceOwnerGatewayError(
                "resource_owner_authorization_wal_configuration_invalid"
            ) from exc
        return _OwnerAuthorizationWalEnvelopeMetadata(
            lookup_sha256=lookup_sha256,
            tenant_id=authorization.tenant_id,
            project_id=authorization.project_id,
            owner_gateway_pub_id=authorization.authority.owner_handle,
            collection_surface=authorization.collection_surface,
            encryption_key_revision=self._encryption_key_revision,
            evidence_sha256=record.evidence_sha256,
            retention=OwnerAuthorizationWalRetentionMetadata(
                policy_revision=self._retention_policy_revision,
                retain_until=retain_until,
                evidence_sha256=record.evidence_sha256,
            ),
        )

    def _send_metadata(
        self,
        record: SubmissionOwnerSendBoundaryRecord | SubmissionOwnerSendOutcomeRecord,
        *,
        authorization_record: SubmissionOwnerAuthorizationWalRecord,
        authorization_envelope: _OwnerAuthorizationWalEnvelope,
        lookup_sha256: str,
        record_kind: Literal["boundary", "outcome"],
    ) -> _OwnerSendJournalEnvelopeMetadata:
        authorization = authorization_record.owner_authorization
        record_evidence_sha256 = record.evidence_sha256
        recorded_at = (
            record.entered_at
            if isinstance(record, SubmissionOwnerSendBoundaryRecord)
            else record.disposition.resolved_at
        )
        try:
            retain_until = max(
                authorization_envelope.retention.retain_until,
                recorded_at + self._retention_period,
            ).astimezone(UTC)
        except (OverflowError, ValueError) as exc:
            raise ResourceOwnerGatewayError(
                "resource_owner_authorization_wal_configuration_invalid"
            ) from exc
        return _OwnerSendJournalEnvelopeMetadata(
            record_kind=record_kind,
            lookup_sha256=lookup_sha256,
            tenant_id=authorization.tenant_id,
            project_id=authorization.project_id,
            owner_gateway_pub_id=authorization.authority.owner_handle,
            collection_surface=authorization.collection_surface,
            encryption_key_revision=self._encryption_key_revision,
            owner_authorization_evidence_sha256=authorization_record.evidence_sha256,
            record_evidence_sha256=record_evidence_sha256,
            retention=OwnerAuthorizationWalRetentionMetadata(
                policy_revision=self._retention_policy_revision,
                retain_until=retain_until,
                evidence_sha256=record_evidence_sha256,
            ),
        )

    def _encode_send_event(
        self,
        record: SubmissionOwnerSendBoundaryRecord | SubmissionOwnerSendOutcomeRecord,
        *,
        authorization_record: SubmissionOwnerAuthorizationWalRecord,
        authorization_envelope: _OwnerAuthorizationWalEnvelope,
        lookup_sha256: str,
        record_kind: Literal["boundary", "outcome"],
    ) -> bytes:
        if (
            record_kind == "boundary" and not isinstance(record, SubmissionOwnerSendBoundaryRecord)
        ) or (
            record_kind == "outcome" and not isinstance(record, SubmissionOwnerSendOutcomeRecord)
        ):
            raise ResourceOwnerGatewayError("resource_owner_send_journal_record_invalid")
        payload = canonical_json(record).encode()
        if not payload or len(payload) > MAX_OWNER_AUTHORIZATION_WAL_PLAINTEXT_BYTES:
            raise ResourceOwnerGatewayError("resource_owner_send_journal_record_invalid")
        metadata = self._send_metadata(
            record,
            authorization_record=authorization_record,
            authorization_envelope=authorization_envelope,
            lookup_sha256=lookup_sha256,
            record_kind=record_kind,
        )
        try:
            sealed = self._vault.seal(payload, _send_journal_envelope_aad(metadata))
        except (InvalidTag, KmsUnavailableError, OSError, ValueError) as exc:
            raise ResourceOwnerGatewayError("resource_owner_send_journal_encrypt_failed") from exc
        if not isinstance(sealed, SealedProfile):
            raise ResourceOwnerGatewayError("resource_owner_send_journal_encrypt_failed")
        envelope = _OwnerSendJournalEnvelope(
            **metadata.model_dump(mode="python"),
            ciphertext_b64=base64.b64encode(sealed.ciphertext).decode("ascii"),
            nonce_b64=base64.b64encode(sealed.nonce).decode("ascii"),
            wrapped_dek_b64=base64.b64encode(sealed.wrapped_dek).decode("ascii"),
            ciphertext_sha256=sealed.sha256,
        )
        encoded = canonical_json(envelope).encode()
        if not encoded or len(encoded) > MAX_OWNER_AUTHORIZATION_WAL_ENVELOPE_BYTES:
            raise ResourceOwnerGatewayError("resource_owner_send_journal_record_invalid")
        return encoded

    def _load_send_event(
        self,
        *,
        owner_dispatch_ref: str,
        record_kind: Literal["boundary", "outcome"],
    ) -> (
        tuple[
            SubmissionOwnerSendBoundaryRecord | SubmissionOwnerSendOutcomeRecord,
            _OwnerSendJournalEnvelope,
        ]
        | None
    ):
        lookup_sha256 = _lookup_sha256(owner_dispatch_ref)
        path = (
            self._send_boundary_path(lookup_sha256)
            if record_kind == "boundary"
            else self._send_outcome_path(lookup_sha256)
        )
        try:
            encoded = self._read_file(path)
        except FileNotFoundError:
            return None
        except ResourceOwnerGatewayError:
            raise
        except OSError as exc:
            raise ResourceOwnerGatewayError(
                "resource_owner_authorization_wal_storage_unavailable"
            ) from exc
        return self._decode_send_event(
            encoded,
            lookup_sha256=lookup_sha256,
            expected_owner_dispatch_ref=owner_dispatch_ref,
            record_kind=record_kind,
        )

    def _decode_send_event(
        self,
        encoded: bytes,
        *,
        lookup_sha256: str,
        expected_owner_dispatch_ref: str | None,
        record_kind: Literal["boundary", "outcome"],
    ) -> tuple[
        SubmissionOwnerSendBoundaryRecord | SubmissionOwnerSendOutcomeRecord,
        _OwnerSendJournalEnvelope,
    ]:
        try:
            envelope = _OwnerSendJournalEnvelope.model_validate_json(encoded)
            if encoded != canonical_json(envelope).encode():
                raise ValueError("owner_send_journal_envelope_noncanonical")
            if envelope.lookup_sha256 != lookup_sha256 or envelope.record_kind != record_kind:
                raise ValueError("owner_send_journal_envelope_identity_mismatch")
        except (UnicodeError, ValueError, ValidationError) as exc:
            raise ResourceOwnerGatewayError("resource_owner_send_journal_record_invalid") from exc

        sealed = SealedProfile(
            ciphertext=_decode_base64(envelope.ciphertext_b64),
            nonce=_decode_base64(envelope.nonce_b64),
            wrapped_dek=_decode_base64(envelope.wrapped_dek_b64),
            sha256=envelope.ciphertext_sha256,
        )
        try:
            payload = self._vault.open(sealed, _send_journal_envelope_aad(envelope))
        except (InvalidTag, KmsUnavailableError, OSError, ValueError) as exc:
            raise ResourceOwnerGatewayError("resource_owner_send_journal_decrypt_failed") from exc
        if not payload or len(payload) > MAX_OWNER_AUTHORIZATION_WAL_PLAINTEXT_BYTES:
            raise ResourceOwnerGatewayError("resource_owner_send_journal_record_invalid")
        model = (
            SubmissionOwnerSendBoundaryRecord
            if record_kind == "boundary"
            else SubmissionOwnerSendOutcomeRecord
        )
        try:
            record = model.model_validate_json(payload)
            if payload != canonical_json(record).encode():
                raise ValueError("owner_send_journal_payload_noncanonical")
            if (
                _lookup_sha256(record.owner_dispatch_ref) != lookup_sha256
                or (
                    expected_owner_dispatch_ref is not None
                    and record.owner_dispatch_ref != expected_owner_dispatch_ref
                )
                or record.evidence_sha256 != envelope.record_evidence_sha256
            ):
                raise ValueError("owner_send_journal_envelope_payload_mismatch")
            if isinstance(record, SubmissionOwnerSendBoundaryRecord) and (
                record.tenant_id != envelope.tenant_id
                or record.project_id != envelope.project_id
                or record.command.fresh_claim.claim.owner_handle != envelope.owner_gateway_pub_id
                or record.collection_surface is not envelope.collection_surface
                or record.owner_authorization_evidence_sha256
                != envelope.owner_authorization_evidence_sha256
            ):
                raise ValueError("owner_send_journal_boundary_envelope_mismatch")
        except (UnicodeError, ValueError, ValidationError) as exc:
            raise ResourceOwnerGatewayError("resource_owner_send_journal_record_invalid") from exc
        return record, envelope

    @staticmethod
    def _validate_boundary_authorization(
        boundary: SubmissionOwnerSendBoundaryRecord,
        authorization_record: SubmissionOwnerAuthorizationWalRecord,
    ) -> None:
        authorization = authorization_record.owner_authorization
        claim = boundary.command.fresh_claim.claim
        authority = authorization.authority
        if (
            boundary.tenant_id != authorization.tenant_id
            or boundary.project_id != authorization.project_id
            or boundary.collection_surface is not authorization.collection_surface
            or boundary.gateway_kind is not authorization.gateway_kind
            or boundary.owner_protocol_revision != authorization.owner_protocol_revision
            or boundary.command.fresh_claim.operation != authorization.workflow.operation
            or boundary.command.fresh_claim.claimed_state_version
            != authorization.workflow.expected_state_version + 1
            or boundary.owner_dispatch_ref != authorization_record.owner_dispatch_ref
            or boundary.owner_authorization_evidence_sha256 != authorization_record.evidence_sha256
            or claim.claim_pub_id != authorization_record.claim_pub_id
            or claim.dispatch_key != authorization_record.dispatch_key
            or claim.owner_handle != authority.owner_handle
            or claim.grant_pub_id != authority.grant_pub_id
            or claim.grant_revision != authority.grant_revision
            or claim.authority_sha256 != authority_digest(authority)
            or claim.fence_set_sha256 != authority.fence_set_sha256
            or boundary.entered_at >= authority.valid_until
        ):
            raise ResourceOwnerGatewayError("resource_owner_send_boundary_authorization_mismatch")

    @staticmethod
    def _validate_send_envelope_authorization(
        envelope: _OwnerSendJournalEnvelope,
        authorization_record: SubmissionOwnerAuthorizationWalRecord,
        authorization_envelope: _OwnerAuthorizationWalEnvelope,
    ) -> None:
        authorization = authorization_record.owner_authorization
        if (
            envelope.lookup_sha256 != _lookup_sha256(authorization_record.owner_dispatch_ref)
            or envelope.tenant_id != authorization.tenant_id
            or envelope.project_id != authorization.project_id
            or envelope.owner_gateway_pub_id != authorization.authority.owner_handle
            or envelope.collection_surface is not authorization.collection_surface
            or envelope.owner_authorization_evidence_sha256 != authorization_record.evidence_sha256
            or envelope.retention.retain_until < authorization_envelope.retention.retain_until
        ):
            raise ResourceOwnerGatewayError("resource_owner_send_journal_authorization_mismatch")

    @staticmethod
    def _validate_outcome_boundary(
        outcome: SubmissionOwnerSendOutcomeRecord,
        boundary: SubmissionOwnerSendBoundaryRecord,
    ) -> None:
        if (
            outcome.owner_dispatch_ref != boundary.owner_dispatch_ref
            or outcome.boundary_evidence_sha256 != boundary.evidence_sha256
            or outcome.disposition.resolved_at < boundary.entered_at
        ):
            raise ResourceOwnerGatewayError("resource_owner_send_outcome_boundary_mismatch")

    def _load_entry(
        self,
        owner_dispatch_ref: str,
    ) -> (
        tuple[
            SubmissionOwnerAuthorizationWalRecord,
            _OwnerAuthorizationWalEnvelope,
        ]
        | None
    ):
        lookup_sha256 = _lookup_sha256(owner_dispatch_ref)
        try:
            encoded = self._read_file(self._record_path(lookup_sha256))
        except FileNotFoundError:
            return None
        except ResourceOwnerGatewayError:
            raise
        except OSError as exc:
            raise ResourceOwnerGatewayError(
                "resource_owner_authorization_wal_storage_unavailable"
            ) from exc
        return self._decode_entry(
            encoded,
            lookup_sha256=lookup_sha256,
            expected_owner_dispatch_ref=owner_dispatch_ref,
        )

    def _decode_entry(
        self,
        encoded: bytes,
        *,
        lookup_sha256: str,
        expected_owner_dispatch_ref: str | None,
    ) -> tuple[
        SubmissionOwnerAuthorizationWalRecord,
        _OwnerAuthorizationWalEnvelope,
    ]:
        try:
            envelope = _OwnerAuthorizationWalEnvelope.model_validate_json(encoded)
            if encoded != canonical_json(envelope).encode():
                raise ValueError("owner_authorization_wal_envelope_noncanonical")
            if envelope.lookup_sha256 != lookup_sha256:
                raise ValueError("owner_authorization_wal_lookup_mismatch")
        except (UnicodeError, ValueError, ValidationError) as exc:
            raise ResourceOwnerGatewayError(
                "resource_owner_authorization_wal_record_invalid"
            ) from exc

        sealed = SealedProfile(
            ciphertext=_decode_base64(envelope.ciphertext_b64),
            nonce=_decode_base64(envelope.nonce_b64),
            wrapped_dek=_decode_base64(envelope.wrapped_dek_b64),
            sha256=envelope.ciphertext_sha256,
        )
        try:
            payload = self._vault.open(sealed, _envelope_aad(envelope))
        except (InvalidTag, KmsUnavailableError, OSError, ValueError) as exc:
            raise ResourceOwnerGatewayError(
                "resource_owner_authorization_wal_decrypt_failed"
            ) from exc
        if not payload or len(payload) > MAX_OWNER_AUTHORIZATION_WAL_PLAINTEXT_BYTES:
            raise ResourceOwnerGatewayError("resource_owner_authorization_wal_record_invalid")
        try:
            record = SubmissionOwnerAuthorizationWalRecord.model_validate_json(payload)
            if payload != canonical_json(record).encode():
                raise ValueError("owner_authorization_wal_payload_noncanonical")
            authorization = record.owner_authorization
            if (
                _lookup_sha256(record.owner_dispatch_ref) != lookup_sha256
                or (
                    expected_owner_dispatch_ref is not None
                    and record.owner_dispatch_ref != expected_owner_dispatch_ref
                )
                or record.evidence_sha256 != envelope.evidence_sha256
                or authorization.tenant_id != envelope.tenant_id
                or authorization.project_id != envelope.project_id
                or authorization.authority.owner_handle != envelope.owner_gateway_pub_id
                or authorization.collection_surface is not envelope.collection_surface
                or envelope.retention.retain_until < authorization.authority.valid_until
            ):
                raise ValueError("owner_authorization_wal_envelope_payload_mismatch")
        except (UnicodeError, ValueError, ValidationError) as exc:
            raise ResourceOwnerGatewayError(
                "resource_owner_authorization_wal_record_invalid"
            ) from exc
        return record, envelope

    def _prepare_root(self) -> None:
        created = False
        try:
            if not self._root.parent.is_dir():
                raise OSError("owner WAL parent is unavailable")
            try:
                self._root.mkdir(mode=0o700)
                created = True
            except FileExistsError:
                pass
            metadata = self._root.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise OSError("owner WAL root is not a directory")
            if metadata.st_uid != os.geteuid():
                raise OSError("owner WAL root has a different owner")
            os.chmod(self._root, 0o700)
            if created:
                self._fsync_path(self._root.parent)
            self._fsync_directory()
        except OSError as exc:
            raise ResourceOwnerGatewayError(
                "resource_owner_authorization_wal_storage_unavailable"
            ) from exc

    def _record_path(self, lookup_sha256: str) -> Path:
        return self._root / f"{lookup_sha256}.wal"

    def _send_boundary_path(self, lookup_sha256: str) -> Path:
        return self._root / f"{lookup_sha256}.send-boundary.wal"

    def _send_outcome_path(self, lookup_sha256: str) -> Path:
        return self._root / f"{lookup_sha256}.send-outcome.wal"

    @contextmanager
    def _lock(self, lookup_sha256: str) -> Iterator[None]:
        path = self._root / f".{lookup_sha256}.lock"
        descriptor: int | None = None
        try:
            descriptor = os.open(
                path,
                os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
                raise OSError("owner WAL lock is unsafe")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            raise ResourceOwnerGatewayError(
                "resource_owner_authorization_wal_storage_unavailable"
            ) from exc
        try:
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _read_file(self, path: Path) -> bytes:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or metadata.st_mode & 0o077
                or metadata.st_nlink != 1
                or metadata.st_size <= 0
                or metadata.st_size > MAX_OWNER_AUTHORIZATION_WAL_ENVELOPE_BYTES
            ):
                raise ResourceOwnerGatewayError("resource_owner_authorization_wal_record_invalid")
            chunks: list[bytes] = []
            remaining = metadata.st_size
            while remaining:
                chunk = os.read(descriptor, min(remaining, 65_536))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) != metadata.st_size:
                raise ResourceOwnerGatewayError("resource_owner_authorization_wal_record_invalid")
            return payload
        finally:
            os.close(descriptor)

    @staticmethod
    def _validate_lock_file(path: Path) -> None:
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.geteuid()
                    or metadata.st_mode & 0o077
                    or metadata.st_nlink != 1
                ):
                    raise OSError("owner WAL lock is unsafe")
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise ResourceOwnerGatewayError(
                "resource_owner_authorization_wal_startup_audit_failed"
            ) from exc

    def _atomic_write(self, destination: Path, payload: bytes) -> None:
        temporary_path: str | None = None
        try:
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{destination.stem}.",
                suffix=".tmp",
                dir=self._root,
            )
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, destination)
            temporary_path = None
            self._fsync_directory()
        except OSError as exc:
            raise ResourceOwnerGatewayError(
                "resource_owner_authorization_wal_storage_unavailable"
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

    def _fsync_directory(self) -> None:
        self._fsync_path(self._root)

    @staticmethod
    def _fsync_path(path: Path) -> None:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


@dataclass(frozen=True, slots=True)
class ConfiguredOwnerAuthorizationWalRuntime:
    """Fail-closed owner WAL runtime paired with its complete chain audit."""

    store: EncryptedFileSubmissionOwnerAuthorizationWalStore
    startup_audit: OwnerAuthorizationWalStartupAudit


def _validate_owner_wal_vault_token_file(path_text: str) -> None:
    path = Path(path_text)
    if not path.is_absolute():
        raise ResourceOwnerGatewayError("resource_owner_authorization_wal_kms_unavailable")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            metadata = os.fstat(descriptor)
            raw = os.read(descriptor, 4097)
        finally:
            os.close(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o077
            or not raw
            or len(raw) > 4096
        ):
            raise OSError("owner WAL Vault token is unsafe")
        token = raw.decode("utf-8").strip()
        if not token or "\n" in token or "\r" in token:
            raise OSError("owner WAL Vault token is invalid")
    except (OSError, UnicodeError) as exc:
        raise ResourceOwnerGatewayError("resource_owner_authorization_wal_kms_unavailable") from exc


def _configured_owner_wal_vault(settings: Settings) -> ProfileVault:
    production = settings.env.strip().lower() in {"production", "prod"}
    if settings.kms_provider == "vault_transit":
        _validate_owner_wal_vault_token_file(settings.vault_transit_token_file)
        try:
            return ProfileVault(
                VaultTransitKms(
                    settings.vault_transit_address,
                    settings.vault_transit_token_file,
                    settings.vault_transit_key_name,
                )
            )
        except ValueError as exc:
            raise ResourceOwnerGatewayError(
                "resource_owner_authorization_wal_kms_unavailable"
            ) from exc
    if production or not settings.kms_master_key:
        raise ResourceOwnerGatewayError("resource_owner_authorization_wal_kms_unavailable")
    return ProfileVault(LocalKms(settings.kms_master_key))


def configure_owner_authorization_wal_runtime(
    settings: Settings,
) -> ConfiguredOwnerAuthorizationWalRuntime:
    """Build an explicit runtime store and audit every retained record.

    This does not wire any submit transport.  Production refuses LocalKms and
    refuses to return a runtime unless its durable root, policy, token file, and
    every retained authorization/boundary/outcome chain pass their gates.
    """

    root_text = settings.collection_owner_wal_dir.strip()
    retention_days = settings.collection_owner_wal_retention_days
    if (
        not root_text
        or isinstance(retention_days, bool)
        or not 1 <= retention_days <= _MAX_RETENTION.days
    ):
        raise ResourceOwnerGatewayError("resource_owner_authorization_wal_configuration_invalid")
    try:
        retention_period = timedelta(days=retention_days)
    except OverflowError as exc:
        raise ResourceOwnerGatewayError(
            "resource_owner_authorization_wal_configuration_invalid"
        ) from exc
    store = EncryptedFileSubmissionOwnerAuthorizationWalStore(
        Path(root_text),
        vault=_configured_owner_wal_vault(settings),
        retention_period=retention_period,
        retention_policy_revision=(settings.collection_owner_wal_retention_policy_revision),
        encryption_key_revision=(settings.collection_owner_wal_encryption_key_revision),
    )
    return ConfiguredOwnerAuthorizationWalRuntime(
        store=store,
        startup_audit=store.verify_retained_records(),
    )


__all__ = [
    "ConfiguredOwnerAuthorizationWalRuntime",
    "EncryptedFileSubmissionOwnerAuthorizationWalStore",
    "MAX_OWNER_AUTHORIZATION_WAL_ENVELOPE_BYTES",
    "MAX_OWNER_AUTHORIZATION_WAL_PLAINTEXT_BYTES",
    "OWNER_AUTHORIZATION_WAL_ENVELOPE_SCHEMA",
    "OwnerAuthorizationWalRetentionMetadata",
    "OwnerAuthorizationWalStartupAudit",
    "OwnerAuthorizationWalVault",
    "configure_owner_authorization_wal_runtime",
]
