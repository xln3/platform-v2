from __future__ import annotations

import hmac
import json
import secrets
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

import psycopg
from psycopg.rows import dict_row

from domain.evidence.diff import OcrSpan, compare_evidence, validate_ocr_spans
from domain.evidence.provenance import AccessClass, RedactedProvenance

from ..tenancy.psycopg import tenant_connection
from .object_store import ContentAddressedObjectStore, StoredObject


class EvidenceService:
    def __init__(self, *, dsn: str, store: ContentAddressedObjectStore) -> None:
        self.dsn = dsn
        self.store = store

    def capture(
        self,
        *,
        evidence_pub_id: str,
        tenant_pub_id: str,
        project_pub_id: str | None,
        kind: str,
        payload: bytes,
        mime_type: str,
        source_url: str | None,
        provenance: RedactedProvenance,
        validated_lease_pub_id: str | None = None,
        inject_db_failure: bool = False,
        db_connection: psycopg.Connection[Any] | None = None,
    ) -> StoredObject:
        if provenance.authorized_session_capture and not validated_lease_pub_id:
            raise PermissionError(
                "authorized session evidence requires a validated capability lease"
            )
        if validated_lease_pub_id is not None and not provenance.authorized_session_capture:
            raise ValueError("capability lease may only be attached to authorized session capture")
        stored = self.store.put_redacted(payload, mime_type=mime_type)

        def persist_metadata(connection: psycopg.Connection[Any]) -> str:
            if inject_db_failure:
                raise RuntimeError("injected DB failure after object write")
            # The object is content-addressed, but evidence metadata represents a
            # capture occurrence. Idempotency is therefore scoped to the stable
            # evidence public ID, never merely to matching bytes.
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{tenant_pub_id}|{evidence_pub_id}",),
            )
            existing = connection.execute(
                """
                SELECT pub_id,project_pub_id,kind,access_class,sha256,object_key,mime_type,
                       byte_size,source_url,dlp_findings,platform_account_pub_id,
                       browser_profile_version_pub_id,session_event_pub_id,channel,
                       authorization_scope,adapter_version,capture_time,
                       authorized_session_capture
                FROM evidence.evidence_asset
                WHERE tenant_pub_id=%s AND pub_id=%s
                FOR KEY SHARE
                """,
                (tenant_pub_id, evidence_pub_id),
            ).fetchone()
            if existing is not None:
                expected = (
                    evidence_pub_id,
                    project_pub_id,
                    kind,
                    provenance.access_class.value,
                    stored.sha256,
                    stored.key,
                    mime_type,
                    stored.byte_size,
                    source_url,
                    list(stored.dlp_findings),
                    provenance.platform_account_pub_id,
                    provenance.browser_profile_version_pub_id,
                    provenance.session_event_pub_id,
                    provenance.channel.value,
                    list(provenance.authorization_scope),
                    provenance.adapter_version,
                    provenance.capture_time,
                    provenance.authorized_session_capture,
                )
                existing_values = (
                    tuple(
                        existing[column]
                        for column in (
                            "pub_id",
                            "project_pub_id",
                            "kind",
                            "access_class",
                            "sha256",
                            "object_key",
                            "mime_type",
                            "byte_size",
                            "source_url",
                            "dlp_findings",
                            "platform_account_pub_id",
                            "browser_profile_version_pub_id",
                            "session_event_pub_id",
                            "channel",
                            "authorization_scope",
                            "adapter_version",
                            "capture_time",
                            "authorized_session_capture",
                        )
                    )
                    if isinstance(existing, Mapping)
                    else tuple(existing)
                )
                if existing_values != expected:
                    raise ValueError("evidence replay payload drifted")
                return str(existing["pub_id"] if isinstance(existing, Mapping) else existing[0])
            row = connection.execute(
                """
                INSERT INTO evidence.evidence_asset
                  (pub_id,tenant_pub_id,project_pub_id,kind,access_class,sha256,object_key,
                   mime_type,byte_size,source_url,dlp_findings,platform_account_pub_id,
                   browser_profile_version_pub_id,session_event_pub_id,channel,
                   authorization_scope,adapter_version,capture_time,authorized_session_capture)
                VALUES
                  (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING pub_id
                """,
                (
                    evidence_pub_id,
                    tenant_pub_id,
                    project_pub_id,
                    kind,
                    provenance.access_class.value,
                    stored.sha256,
                    stored.key,
                    mime_type,
                    stored.byte_size,
                    source_url,
                    list(stored.dlp_findings),
                    provenance.platform_account_pub_id,
                    provenance.browser_profile_version_pub_id,
                    provenance.session_event_pub_id,
                    provenance.channel.value,
                    list(provenance.authorization_scope),
                    provenance.adapter_version,
                    provenance.capture_time,
                    provenance.authorized_session_capture,
                ),
            ).fetchone()
            assert row is not None
            metadata_pub_id = str(row["pub_id"] if isinstance(row, Mapping) else row[0])
            return metadata_pub_id

        # CAS-first intentionally leaves a detectable orphan if this transaction fails.
        # Deleting immediately is unsafe because another idempotent writer may share the hash.
        if db_connection is not None:
            metadata_pub_id = persist_metadata(db_connection)
        else:
            with tenant_connection(self.dsn, tenant_pub_id) as connection:
                metadata_pub_id = persist_metadata(connection)
                connection.commit()
        return replace(stored, metadata_pub_id=metadata_pub_id)

    def find_orphan(self, stored: StoredObject, *, tenant_pub_id: str) -> bool:
        with tenant_connection(self.dsn, tenant_pub_id) as connection:
            exists = connection.execute(
                """
                SELECT 1 FROM evidence.evidence_asset
                WHERE tenant_pub_id=%s AND object_key=%s
                """,
                (tenant_pub_id, stored.key),
            ).fetchone()
        return exists is None

    def create_package(
        self,
        *,
        package_pub_id: str,
        tenant_pub_id: str,
        evidence_pub_ids: list[str],
        public: bool,
        expires_at: datetime | None,
        customer_visible_only: bool = False,
    ) -> StoredObject:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT pub_id,sha256,kind,access_class,capture_time
                FROM evidence.evidence_asset
                WHERE tenant_pub_id=%s AND pub_id=ANY(%s) AND deleted_at IS NULL
                  AND (NOT %s OR customer_visible)
                ORDER BY pub_id
                """,
                (tenant_pub_id, evidence_pub_ids, customer_visible_only),
            ).fetchall()
            if len(rows) != len(set(evidence_pub_ids)):
                raise LookupError("one or more evidence assets are missing")
            if public and any(row["access_class"] != AccessClass.PUBLIC.value for row in rows):
                raise PermissionError("private/paid evidence cannot enter a public package")
            manifest = {
                "version": "1.0",
                "tenant_pub_id": tenant_pub_id,
                "package_pub_id": package_pub_id,
                "access_class": "public" if public else "customer_private",
                "evidence": [
                    {
                        **row,
                        "capture_time": row["capture_time"].astimezone(UTC).isoformat(),
                    }
                    for row in rows
                ],
            }
            stored = self.store.put_redacted(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode(),
                mime_type="application/json",
                namespace="packages",
            )
            connection.execute(
                """
                INSERT INTO evidence.evidence_package
                  (pub_id,tenant_pub_id,manifest_sha256,object_key,state,access_class,expires_at)
                VALUES (%s,%s,%s,%s,'ready',%s,%s)
                """,
                (
                    package_pub_id,
                    tenant_pub_id,
                    stored.sha256,
                    stored.key,
                    manifest["access_class"],
                    expires_at,
                ),
            )
        return stored

    def grant(self, *, grant_pub_id: str, package_pub_id: str, tenant_pub_id: str) -> str:
        token = f"{tenant_pub_id}.{secrets.token_urlsafe(32)}"
        token_hash = sha256(token.encode()).hexdigest()
        with tenant_connection(self.dsn, tenant_pub_id) as connection:
            connection.execute(
                """
                INSERT INTO evidence.evidence_access_grant
                  (pub_id,tenant_pub_id,package_pub_id,token_hash,expires_at)
                VALUES (%s,%s,%s,%s,%s)
                """,
                (
                    grant_pub_id,
                    tenant_pub_id,
                    package_pub_id,
                    token_hash,
                    datetime.now(UTC) + timedelta(minutes=15),
                ),
            )
        return token

    def authorize_package_access(self, *, token: str, request_id: str) -> dict[str, Any]:
        tenant_pub_id, separator, _ = token.partition(".")
        if separator != "." or not tenant_pub_id.startswith("tnt_") or len(tenant_pub_id) > 120:
            raise PermissionError("evidence package grant is invalid, expired or revoked")
        token_hash = sha256(token.encode()).hexdigest()
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT p.*,g.tenant_pub_id AS grant_tenant,g.expires_at AS grant_expires,
                       g.revoked_at AS grant_revoked
                FROM evidence.evidence_access_grant g
                JOIN evidence.evidence_package p ON p.pub_id=g.package_pub_id
                WHERE g.token_hash=%s
                """,
                (token_hash,),
            ).fetchone()
            allowed = bool(
                row
                and hmac.compare_digest(token_hash, sha256(token.encode()).hexdigest())
                and row["grant_tenant"] == row["tenant_pub_id"]
                and row["grant_revoked"] is None
                and row["revoked_at"] is None
                and row["state"] == "ready"
                and row["grant_expires"] > datetime.now(UTC)
                and (row["expires_at"] is None or row["expires_at"] > datetime.now(UTC))
            )
            connection.execute(
                """
                INSERT INTO evidence.evidence_access_audit
                  (tenant_pub_id,resource_pub_id,action,outcome,request_id)
                VALUES (%s,%s,'download',%s,%s)
                """,
                (
                    tenant_pub_id,
                    row["pub_id"] if row else "pkg_unknown",
                    "allowed" if allowed else "denied",
                    request_id,
                ),
            )
            connection.commit()
            if not allowed:
                raise PermissionError("evidence package grant is invalid, expired or revoked")
            assert row is not None
            return dict(row)

    def revoke_package(self, package_pub_id: str, tenant_pub_id: str) -> None:
        with tenant_connection(self.dsn, tenant_pub_id) as connection:
            updated = connection.execute(
                """
                UPDATE evidence.evidence_package
                SET state='revoked',revoked_at=now()
                WHERE pub_id=%s AND tenant_pub_id=%s AND state <> 'revoked'
                RETURNING pub_id
                """,
                (package_pub_id, tenant_pub_id),
            ).fetchone()
            if updated is None:
                raise LookupError("package not found or already revoked")

    def persist_ocr(
        self,
        *,
        tenant_pub_id: str,
        evidence_pub_id: str,
        text: str,
        spans: list[OcrSpan],
        ocr_version: str,
    ) -> list[str]:
        validate_ocr_spans(text, spans)
        anchor_ids: list[str] = []
        with tenant_connection(self.dsn, tenant_pub_id) as connection:
            owner = connection.execute(
                """
                SELECT 1 FROM evidence.evidence_asset
                WHERE tenant_pub_id=%s AND pub_id=%s AND deleted_at IS NULL
                """,
                (tenant_pub_id, evidence_pub_id),
            ).fetchone()
            if owner is None:
                raise LookupError("evidence asset not found")
            for span in spans:
                anchor_pub_id = f"anch_{secrets.token_hex(16)}"
                connection.execute(
                    """
                    INSERT INTO evidence.evidence_anchor
                      (pub_id,tenant_pub_id,evidence_pub_id,text_start,text_end,bbox,quote_hash)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        anchor_pub_id,
                        tenant_pub_id,
                        evidence_pub_id,
                        span.start,
                        span.end,
                        json.dumps(
                            {
                                "x": span.bbox.x,
                                "y": span.bbox.y,
                                "width": span.bbox.width,
                                "height": span.bbox.height,
                                "confidence": span.confidence,
                                "ocr_version": ocr_version,
                            }
                        ),
                        sha256(span.text.encode()).hexdigest(),
                    ),
                )
                anchor_ids.append(anchor_pub_id)
        return anchor_ids

    def record_snapshot(
        self,
        *,
        tenant_pub_id: str,
        subject_pub_id: str,
        evidence_pub_id: str,
        normalized_text: str | None,
        perceptual_hash: str | None,
    ) -> dict[str, Any]:
        snapshot_pub_id = f"snap_{secrets.token_hex(16)}"
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{tenant_pub_id}:{subject_pub_id}",),
            )
            asset = connection.execute(
                """
                SELECT 1 FROM evidence.evidence_asset
                WHERE tenant_pub_id=%s AND pub_id=%s AND deleted_at IS NULL
                """,
                (tenant_pub_id, evidence_pub_id),
            ).fetchone()
            if asset is None:
                raise LookupError("snapshot evidence asset not found")
            sequence = connection.execute(
                """
                SELECT COALESCE(MAX(snapshot_number),0)+1 AS next_number
                FROM evidence.evidence_snapshot
                WHERE tenant_pub_id=%s AND subject_pub_id=%s
                """,
                (tenant_pub_id, subject_pub_id),
            ).fetchone()
            assert sequence is not None
            normalized_text_hash = (
                sha256(normalized_text.encode()).hexdigest()
                if normalized_text is not None
                else None
            )
            connection.execute(
                """
                INSERT INTO evidence.evidence_snapshot
                  (pub_id,tenant_pub_id,subject_pub_id,evidence_pub_id,snapshot_number,
                   normalized_text_hash,perceptual_hash)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    snapshot_pub_id,
                    tenant_pub_id,
                    subject_pub_id,
                    evidence_pub_id,
                    sequence["next_number"],
                    normalized_text_hash,
                    perceptual_hash,
                ),
            )
        return {
            "snapshot_pub_id": snapshot_pub_id,
            "snapshot_number": sequence["next_number"],
            "normalized_text_hash": normalized_text_hash,
            "perceptual_hash": perceptual_hash,
        }

    def persist_diff(
        self,
        *,
        tenant_pub_id: str,
        before_evidence_pub_id: str,
        after_evidence_pub_id: str,
        before_text: str,
        after_text: str,
        before_perceptual_hash: str | None = None,
        after_perceptual_hash: str | None = None,
    ) -> str:
        result = compare_evidence(
            before_text,
            after_text,
            before_perceptual_hash=before_perceptual_hash,
            after_perceptual_hash=after_perceptual_hash,
        )
        diff_pub_id = f"diff_{secrets.token_hex(16)}"
        with tenant_connection(self.dsn, tenant_pub_id) as connection:
            assets_row = connection.execute(
                """
                SELECT count(*) FROM evidence.evidence_asset
                WHERE tenant_pub_id=%s AND pub_id=ANY(%s)
                """,
                (tenant_pub_id, [before_evidence_pub_id, after_evidence_pub_id]),
            ).fetchone()
            assert assets_row is not None
            assets = assets_row[0]
            if assets != 2:
                raise LookupError("diff assets must exist in the same tenant")
            connection.execute(
                """
                INSERT INTO evidence.evidence_diff
                  (pub_id,tenant_pub_id,before_evidence_pub_id,after_evidence_pub_id,
                   text_diff,similarity)
                VALUES (%s,%s,%s,%s,%s,%s)
                """,
                (
                    diff_pub_id,
                    tenant_pub_id,
                    before_evidence_pub_id,
                    after_evidence_pub_id,
                    json.dumps(
                        {
                            "unified": result.unified_text_diff,
                            "before_hash": result.before_hash,
                            "after_hash": result.after_hash,
                            "visual_similarity": result.visual_similarity,
                        }
                    ),
                    result.text_similarity,
                ),
            )
        return diff_pub_id
