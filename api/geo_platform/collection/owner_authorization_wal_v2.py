"""Encrypted, owner-local physical WAL for collection submit authorization.

The store is intentionally independent of PostgreSQL and Temporal.  It writes
the exact pre-CAS authorization record to an owner-local filesystem before the
database claim may reference its evidence hash.  Records are envelope-encrypted,
atomically replaced into place, fsynced, immutable by dispatch reference, and
re-read from disk for every post-CAS authorization check.

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
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol, Self
from uuid import UUID

from cryptography.exceptions import InvalidTag
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from domain.collection.submission import OpaqueId, Sha256Hex, canonical_json
from domain.collection.surface import CollectionSurface

from .resource_owner_gateway_v2 import (
    ResourceOwnerGatewayError,
    SubmissionOwnerAuthorizationWalRecord,
)
from .vault import KmsUnavailableError, SealedProfile, profile_aad

OWNER_AUTHORIZATION_WAL_ENVELOPE_SCHEMA: Literal[
    "collection-owner-authorization-wal-envelope-v1"
] = "collection-owner-authorization-wal-envelope-v1"
MAX_OWNER_AUTHORIZATION_WAL_PLAINTEXT_BYTES = 512 * 1024
MAX_OWNER_AUTHORIZATION_WAL_ENVELOPE_BYTES = 1024 * 1024

_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
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


class EncryptedFileSubmissionOwnerAuthorizationWalStore:
    """Secure physical implementation of the authorization WAL store protocol.

    ``root`` must be a dedicated absolute directory whose parent already exists.
    A vault is mandatory: there is no plaintext or production-degrading mode.
    Exact replay returns the existing record; a different record for the same
    owner dispatch reference fails before the caller can perform its claim CAS.
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
            raise ResourceOwnerGatewayError(
                "resource_owner_authorization_wal_write_invalid"
            )
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
                raise ResourceOwnerGatewayError(
                    "resource_owner_authorization_wal_write_invalid"
                )
            metadata = self._metadata(record, lookup_sha256=lookup_sha256)
            try:
                sealed = self._vault.seal(payload, _envelope_aad(metadata))
            except (InvalidTag, KmsUnavailableError, OSError, ValueError) as exc:
                raise ResourceOwnerGatewayError(
                    "resource_owner_authorization_wal_encrypt_failed"
                ) from exc
            if not isinstance(sealed, SealedProfile):
                raise ResourceOwnerGatewayError(
                    "resource_owner_authorization_wal_encrypt_failed"
                )
            envelope = _OwnerAuthorizationWalEnvelope(
                **metadata.model_dump(mode="python"),
                ciphertext_b64=base64.b64encode(sealed.ciphertext).decode("ascii"),
                nonce_b64=base64.b64encode(sealed.nonce).decode("ascii"),
                wrapped_dek_b64=base64.b64encode(sealed.wrapped_dek).decode("ascii"),
                ciphertext_sha256=sealed.sha256,
            )
            encoded = canonical_json(envelope).encode()
            if not encoded or len(encoded) > MAX_OWNER_AUTHORIZATION_WAL_ENVELOPE_BYTES:
                raise ResourceOwnerGatewayError(
                    "resource_owner_authorization_wal_write_invalid"
                )
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

    def _load_entry(
        self,
        owner_dispatch_ref: str,
    ) -> tuple[
        SubmissionOwnerAuthorizationWalRecord,
        _OwnerAuthorizationWalEnvelope,
    ] | None:
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
            raise ResourceOwnerGatewayError(
                "resource_owner_authorization_wal_record_invalid"
            )
        try:
            record = SubmissionOwnerAuthorizationWalRecord.model_validate_json(payload)
            if payload != canonical_json(record).encode():
                raise ValueError("owner_authorization_wal_payload_noncanonical")
            authorization = record.owner_authorization
            if (
                record.owner_dispatch_ref != owner_dispatch_ref
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

    @contextmanager
    def _lock(self, lookup_sha256: str) -> Iterator[None]:
        path = self._root / f".{lookup_sha256}.lock"
        descriptor: int | None = None
        try:
            descriptor = os.open(
                path,
                os.O_CREAT
                | os.O_RDWR
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
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
                raise ResourceOwnerGatewayError(
                    "resource_owner_authorization_wal_record_invalid"
                )
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
                raise ResourceOwnerGatewayError(
                    "resource_owner_authorization_wal_record_invalid"
                )
            return payload
        finally:
            os.close(descriptor)

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


__all__ = [
    "EncryptedFileSubmissionOwnerAuthorizationWalStore",
    "MAX_OWNER_AUTHORIZATION_WAL_ENVELOPE_BYTES",
    "MAX_OWNER_AUTHORIZATION_WAL_PLAINTEXT_BYTES",
    "OWNER_AUTHORIZATION_WAL_ENVELOPE_SCHEMA",
    "OwnerAuthorizationWalRetentionMetadata",
    "OwnerAuthorizationWalVault",
]
