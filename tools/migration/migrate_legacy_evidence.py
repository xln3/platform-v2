"""Verify, redact and migrate admissible legacy CAS evidence to MinIO/V2."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import psycopg
from geo_platform.config import get_settings
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.evidence.service import EvidenceService
from psycopg.rows import dict_row

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from tools.migration.migrate_legacy_core import CoreMigrator, _dsn, parse_time, row_hash

ADMISSIBLE_KINDS = frozenset({"screenshot", "share_image", "share_link", "sse"})
QUARANTINED_KINDS = frozenset({"har", "captcha"})


def blob_path(root: Path, digest: str) -> Path:
    return root / "blob" / digest[:2] / digest[2:4] / digest


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def migrate(source_path: Path, cas_root: Path, *, dsn: str) -> dict[str, object]:
    migrator = CoreMigrator(source_path, dsn=dsn)
    settings = get_settings()
    store = ContentAddressedObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
    )
    store.ensure_bucket()
    service = EvidenceService(dsn=dsn, store=store)
    counts = {
        "seen": 0,
        "migrated": 0,
        "already_mapped": 0,
        "quarantined": 0,
        "source_bytes_verified": 0,
        "target_bytes_verified": 0,
        "failed": 0,
    }
    quarantine = {"har": 0, "captcha": 0}
    with (
        sqlite3.connect(f"file:{source_path.resolve(strict=True)}?mode=ro", uri=True) as source,
        psycopg.connect(dsn, row_factory=dict_row) as target,
    ):
        source.row_factory = sqlite3.Row
        rows = source.execute(
            """
            SELECT e.id,e.answer_id,e.kind,e.sha256,e.redaction_status,e.created_at,
                   b.size,b.media_type,a.tenant_id,a.tick_time,w.monitoring_config_id
            FROM evidence_ref e
            JOIN cas_blob b ON b.sha256=e.sha256
            JOIN answer a ON a.id=e.answer_id
            JOIN work_item w ON w.id=a.work_item_id
            ORDER BY e.id
            """
        ).fetchall()
        for raw in rows:
            row = dict(raw)
            counts["seen"] += 1
            kind = str(row["kind"])
            if kind in QUARANTINED_KINDS:
                quarantine[kind] += 1
                counts["quarantined"] += 1
                continue
            if kind not in ADMISSIBLE_KINDS:
                raise RuntimeError("unknown evidence kind fails closed")
            path = blob_path(cas_root.resolve(strict=True), row["sha256"])
            if not path.is_file() or path.stat().st_size != row["size"]:
                counts["failed"] += 1
                raise RuntimeError("legacy CAS blob missing or size mismatch")
            if hash_file(path) != row["sha256"]:
                counts["failed"] += 1
                raise RuntimeError("legacy CAS source hash mismatch")
            counts["source_bytes_verified"] += row["size"]
            source_row = {
                key: row[key]
                for key in (
                    "id",
                    "answer_id",
                    "kind",
                    "sha256",
                    "redaction_status",
                    "created_at",
                    "size",
                    "media_type",
                )
            }
            evidence_pub_id = migrator._pub("evd", "evidence_ref", row["id"])
            existing = target.execute(
                """
                SELECT target_pub_id,source_hash FROM integration.legacy_id_map
                WHERE run_id=%s AND source_system=%s AND entity_type='evidence_ref'
                  AND source_pk=%s
                """,
                (migrator.run_id, "legacy-geosys-sqlite", str(row["id"])),
            ).fetchone()
            if existing:
                if existing["source_hash"] != row_hash(source_row):
                    raise RuntimeError("evidence source drift detected")
                asset = target.execute(
                    "SELECT object_key,sha256,byte_size FROM evidence.evidence_asset "
                    "WHERE pub_id=%s",
                    (existing["target_pub_id"],),
                ).fetchone()
                if asset is None:
                    raise RuntimeError("mapped evidence asset is missing")
                store.get_verified(asset["object_key"], asset["sha256"])
                counts["target_bytes_verified"] += asset["byte_size"]
                counts["already_mapped"] += 1
                continue
            payload = path.read_bytes()
            stored = service.capture(
                evidence_pub_id=evidence_pub_id,
                tenant_pub_id=migrator._pub("tnt", "tenant", row["tenant_id"]),
                project_pub_id=migrator._pub("prj", "project", row["monitoring_config_id"]),
                kind=kind,
                payload=payload,
                mime_type=row["media_type"],
                source_url=None,
                provenance=RedactedProvenance(
                    platform_account_pub_id=None,
                    browser_profile_version_pub_id=None,
                    session_event_pub_id=None,
                    channel=CaptureChannel.API,
                    authorization_scope=("historical:migrated-read",),
                    adapter_version="legacy-migration-v1",
                    capture_time=parse_time(row["tick_time"]),
                    access_class=AccessClass.CUSTOMER_PRIVATE,
                ),
            )
            store.get_verified(stored.key, stored.sha256)
            counts["target_bytes_verified"] += stored.byte_size
            actual_pub_id = stored.metadata_pub_id or evidence_pub_id
            target.execute(
                """
                INSERT INTO evidence.evidence_relation
                  (tenant_pub_id,from_pub_id,to_pub_id,relation_type)
                SELECT %s,%s,%s,'answer_evidence'
                WHERE NOT EXISTS (
                  SELECT 1 FROM evidence.evidence_relation
                  WHERE tenant_pub_id=%s AND from_pub_id=%s AND to_pub_id=%s
                    AND relation_type='answer_evidence'
                )
                """,
                (
                    migrator._pub("tnt", "tenant", row["tenant_id"]),
                    migrator._pub("ans", "answer", row["answer_id"]),
                    actual_pub_id,
                    migrator._pub("tnt", "tenant", row["tenant_id"]),
                    migrator._pub("ans", "answer", row["answer_id"]),
                    actual_pub_id,
                ),
            )
            migrator._record_map(
                target,
                entity="evidence_ref",
                source_pk=row["id"],
                source_row=source_row,
                target_pub_id=actual_pub_id,
            )
            target.commit()
            counts["migrated"] += 1
        migrator._watermark(
            target,
            "evidence_ref",
            rows[-1]["id"] if rows else None,
            counts["seen"],
            counts["migrated"] + counts["already_mapped"],
        )
        target.commit()
    return {
        "schema_version": "1.0",
        "source_snapshot_sha256": migrator.snapshot_hash,
        "source_hash_verified_before_upload": True,
        "target_hash_verified_after_upload": True,
        "quarantine_policy": sorted(QUARANTINED_KINDS),
        "quarantine_counts": quarantine,
        "raw_paths_included": False,
        "secret_values_included": False,
        "counts": counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--cas-root", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    result = migrate(args.source, args.cas_root, dsn=_dsn())
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
