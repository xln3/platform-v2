#!/usr/bin/env python3
"""Process durable SiliconIndex connector requests outside request-time paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
API_ROOT = PROJECT_ROOT / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from geo_platform.config import get_settings  # noqa: E402
from geo_platform.knowledge.models import (  # noqa: E402
    Candidate,
    ChangeSet,
    ConnectorRun,
)
from geo_platform.knowledge.repository import KnowledgeRepository  # noqa: E402
from geo_platform.tenancy.database import SessionLocal  # noqa: E402
from geo_platform.tenancy.ids import new_pub_id  # noqa: E402
from geo_platform.tenancy.repository import TenantRepository  # noqa: E402

from domain.knowledge_evolution.contracts import ObservationDraft  # noqa: E402
from domain.knowledge_evolution.release import KnowledgeReleaseStore  # noqa: E402
from domain.siliconindex import (  # noqa: E402
    SiliconIndexAdapter,
    SiliconIndexSyncError,
    project_brand_domain,
)

NAMESPACE = "shared"
DOMAIN = "brand/entity-resolution"
ANALYSIS_DOMAIN = "cybersecurity"
ADAPTER_IDS = {"siliconindex-static", "siliconindex-static-v1", "siliconindex-static-v2"}
TERMINAL_CANDIDATE_STATES = {
    "rejected",
    "deferred",
    "local_published",
    "exported",
    "externally_published",
    "reconciled",
    "superseded",
}


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _hash(value: Any) -> str:
    data = value.encode() if isinstance(value, str) else _canonical(value)
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _normalized(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _write_artifact(root: Path, kind: str, value: dict[str, Any]) -> tuple[Path, str]:
    digest = _hash(value)
    target_root = root / "connector-artifacts" / kind
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / f"{digest.removeprefix('sha256:')}.json"
    if target.is_file():
        if target.read_bytes() != _canonical(value):
            raise RuntimeError("connector_artifact_hash_collision")
        return target, digest
    temporary = target_root / f".{target.name}.tmp"
    temporary.write_bytes(_canonical(value))
    os.replace(temporary, target)
    return target, digest


def _release_source(root: Path, release_id: str, current_release_id: str) -> Path:
    if release_id == current_release_id:
        return root
    for candidate in (root / release_id, root / "releases" / release_id):
        try:
            imported = SiliconIndexAdapter().import_release(str(candidate))
        except SiliconIndexSyncError:
            continue
        if imported.upstream_release_id == release_id:
            return candidate
    raise SiliconIndexSyncError(f"base_snapshot_missing:{release_id}")


def _knowledge_source_release(store: KnowledgeReleaseStore) -> str | None:
    if store.current_release_id() is None:
        return None
    document, _ = store.load_domain(DOMAIN)
    direct = document.get("source_release_id")
    if isinstance(direct, str) and direct:
        return direct
    domains = document.get("analysis_domains")
    if isinstance(domains, dict):
        projection = domains.get(ANALYSIS_DOMAIN)
        if isinstance(projection, dict):
            nested = projection.get("source_release_id")
            if isinstance(nested, str) and nested:
                return nested
    return None


def _object_values(repository: KnowledgeRepository) -> tuple[dict[str, Any], ...]:
    rows = repository.current_objects(namespace=NAMESPACE, domain=DOMAIN)
    return tuple(
        {
            "stable_id": row.stable_id,
            "object_type": row.object_type,
            "attributes": dict(row.attributes),
            "origin": row.origin,
            "review_status": row.review_status,
            "visibility": row.visibility,
            "sync_status": row.sync_status,
            "version": row.version,
        }
        for row in sorted(rows, key=lambda item: item.stable_id)
    )


def _entity_map(source: Path) -> dict[str, dict[str, Any]]:
    projection = project_brand_domain(source, analysis_domain=ANALYSIS_DOMAIN)
    return {str(row["entity_id"]): dict(row) for row in projection["entities"]}


def _local_map(
    base: dict[str, dict[str, Any]],
    local_objects: tuple[dict[str, Any], ...],
) -> dict[str, dict[str, Any]]:
    local = {key: dict(value) for key, value in base.items()}
    for value in local_objects:
        if value["review_status"] != "reviewed":
            continue
        stable_id = str(value["stable_id"])
        attributes = dict(value["attributes"])
        attributes.pop("analysis_domain", None)
        if attributes.get("entity_id") != stable_id:
            raise SiliconIndexSyncError(f"local_projection_identity_mismatch:{stable_id}")
        local[stable_id] = attributes
    return local


def _changed_ids(
    base: dict[str, dict[str, Any]],
    other: dict[str, dict[str, Any]],
) -> list[str]:
    return [
        stable_id
        for stable_id in sorted(set(base) | set(other))
        if base.get(stable_id) != other.get(stable_id)
    ]


def _changed_fields(before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[str]:
    left = before or {}
    right = after or {}
    return [key for key in sorted(set(left) | set(right)) if left.get(key) != right.get(key)]


def _ingest_upstream_changes(
    repository: KnowledgeRepository,
    session: Session,
    *,
    base: dict[str, dict[str, Any]],
    upstream: dict[str, dict[str, Any]],
    upstream_release_id: str,
    upstream_content_hash: str,
) -> dict[str, int]:
    drafts: list[ObservationDraft] = []
    changed = _changed_ids(base, upstream)
    for stable_id in changed:
        before = base.get(stable_id)
        after = upstream.get(stable_id)
        value = after or before or {}
        canonical_name = str(value.get("canonical_name") or stable_id)
        drafts.append(
            ObservationDraft(
                namespace=NAMESPACE,
                domain=DOMAIN,
                task="siliconindex_reconcile",
                surface_form=canonical_name,
                normalized_key=_normalized(canonical_name),
                source_type="siliconindex_release",
                source_ref_hash=_hash(
                    f"siliconindex:{upstream_release_id}:{upstream_content_hash}"
                ),
                idempotency_key=hashlib.sha256(
                    f"{upstream_release_id}|{stable_id}|reconcile".encode()
                ).hexdigest(),
                safe_context=None,
                data_classification="public",
                visibility="public",
                payload={
                    "stable_id": stable_id,
                    "change_kind": (
                        "created" if before is None else "retired" if after is None else "updated"
                    ),
                    "changed_fields": _changed_fields(before, after),
                    "source_release_id": upstream_release_id,
                    "source_content_hash": upstream_content_hash,
                    "policy_version": "siliconindex-reconcile-v2",
                },
            )
        )
    inserted = repository.record_observations(repository.tenant_pub_id, tuple(drafts))
    reopened = 0
    for draft in drafts:
        candidate = session.scalar(
            select(Candidate).where(
                Candidate.tenant_pub_id == repository.tenant_pub_id,
                Candidate.namespace == NAMESPACE,
                Candidate.domain == DOMAIN,
                Candidate.aggregation_key == _hash(draft.normalized_key),
            )
        )
        if (
            candidate is not None
            and candidate.state in TERMINAL_CANDIDATE_STATES
            and candidate.evidence_version != upstream_release_id
        ):
            repository.reopen_candidate(
                candidate.pub_id,
                reason="A newer verified SiliconIndex release changed this object.",
                policy_version="siliconindex-reconcile-v2",
                evidence_version=upstream_release_id,
                manual_override=False,
                actor="system:siliconindex-connector",
            )
            reopened += 1
    return {"changed": len(changed), "observations_inserted": inserted, "reopened": reopened}


def _conflict_values(conflicts: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [
        {
            "path": value.path,
            "base": value.base,
            "upstream": value.upstream,
            "local": value.local,
        }
        for value in conflicts
    ]


def _record_conflicts(
    repository: KnowledgeRepository,
    session: Session,
    *,
    base_release_id: str | None,
    conflicts: list[dict[str, Any]],
) -> str | None:
    existing = list(
        session.scalars(
            select(ChangeSet).where(
                ChangeSet.tenant_pub_id == repository.tenant_pub_id,
                ChangeSet.namespace == NAMESPACE,
                ChangeSet.domain == DOMAIN,
                ChangeSet.created_by == "system:siliconindex-connector",
                ChangeSet.state == "conflict",
            )
        )
    )
    for row in existing:
        if row.conflicts != conflicts:
            row.state = "superseded"
    if not conflicts:
        return None
    identical = next((row for row in existing if row.conflicts == conflicts), None)
    if identical is not None:
        return identical.pub_id
    row = ChangeSet(
        pub_id=new_pub_id("kcs"),
        tenant_pub_id=repository.tenant_pub_id,
        namespace=NAMESPACE,
        domain=DOMAIN,
        base_release_id=base_release_id,
        changes=[],
        dependency_ids=[],
        conflicts=conflicts,
        visibility="public",
        state="conflict",
        created_by="system:siliconindex-connector",
    )
    session.add(row)
    session.flush()
    repository.audit(
        namespace=NAMESPACE,
        domain=DOMAIN,
        actor="system:siliconindex-connector",
        action="change_set.conflict_detected",
        resource_type="change_set",
        resource_pub_id=row.pub_id,
        receipt={"conflict_count": len(conflicts)},
    )
    return row.pub_id


def reconcile_snapshot(
    session: Session,
    *,
    tenant_pub_id: str,
    snapshot_source: Path,
    knowledge_release_dir: Path,
    base_upstream_release_id: str | None = None,
) -> dict[str, Any]:
    """Compare last common, verified upstream, and governed local projections."""

    TenantRepository(session, tenant_pub_id)
    repository = KnowledgeRepository(session, tenant_pub_id)
    adapter = SiliconIndexAdapter()
    imported = adapter.import_release(str(snapshot_source))
    upstream_release_id = str(imported.upstream_release_id)
    upstream_hash = str(imported.result["content_hash"])
    store = KnowledgeReleaseStore(knowledge_release_dir)
    local_release_id = store.current_release_id()
    base_release_id = base_upstream_release_id or _knowledge_source_release(store)
    if base_release_id is None:
        base_release_id = upstream_release_id
    base_source = _release_source(snapshot_source, base_release_id, upstream_release_id)
    local_objects = _object_values(repository)
    base = _entity_map(base_source)
    upstream = _entity_map(snapshot_source)
    local = _local_map(base, local_objects)
    merge = adapter.reconcile_brand_projection(
        base_source=base_source,
        upstream_source=snapshot_source,
        analysis_domain=ANALYSIS_DOMAIN,
        local_objects=local_objects,
    )
    upstream_changes = _changed_ids(base, upstream)
    local_changes = _changed_ids(base, local)
    ingestion = _ingest_upstream_changes(
        repository,
        session,
        base=base,
        upstream=upstream,
        upstream_release_id=upstream_release_id,
        upstream_content_hash=upstream_hash,
    )
    exportable = tuple(value for value in local_objects if value["sync_status"] == "local_ahead")
    exported = adapter.export_changes(exportable)
    export_document = {
        "schema_version": "siliconindex-change-bundle-v1",
        "base_upstream_release_id": base_release_id,
        "local_knowledge_release_id": local_release_id,
        "changes": exported.result["changes"],
    }
    export_path, export_hash = _write_artifact(
        knowledge_release_dir,
        "siliconindex-export",
        export_document,
    )
    conflicts = _conflict_values(merge.conflicts)
    conflict_change_set = _record_conflicts(
        repository,
        session,
        base_release_id=local_release_id,
        conflicts=conflicts,
    )
    return {
        "base_upstream_release_id": base_release_id,
        "upstream_release_id": upstream_release_id,
        "upstream_content_hash": upstream_hash,
        "local_knowledge_release_id": local_release_id,
        "upstream_changed_ids": upstream_changes,
        "local_changed_ids": local_changes,
        "conflicts": conflicts,
        "conflict_change_set_pub_id": conflict_change_set,
        "review_required": bool(upstream_changes or local_changes or conflicts),
        "can_prepare_merge": not conflicts,
        "upstream_observations": ingestion,
        "local_export": {
            "count": exported.result["count"],
            "content_hash": export_hash,
            "artifact": str(export_path),
        },
        "merged_preview_hash": _hash(merge.merged),
    }


def _claim(tenant_pub_id: str, limit: int) -> list[str]:
    with SessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        rows = list(
            session.scalars(
                select(ConnectorRun)
                .where(
                    ConnectorRun.tenant_pub_id == tenant_pub_id,
                    ConnectorRun.namespace == NAMESPACE,
                    ConnectorRun.domain == DOMAIN,
                    ConnectorRun.adapter.in_(ADAPTER_IDS),
                    ConnectorRun.status == "queued",
                )
                .order_by(ConnectorRun.started_at, ConnectorRun.pub_id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for row in rows:
            row.status = "running"
        session.commit()
        return [row.pub_id for row in rows]


def _complete(
    tenant_pub_id: str,
    run_pub_id: str,
    *,
    status: str,
    result: dict[str, Any],
    error_code: str | None,
) -> None:
    with SessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        row = session.scalar(
            select(ConnectorRun)
            .where(
                ConnectorRun.tenant_pub_id == tenant_pub_id,
                ConnectorRun.pub_id == run_pub_id,
            )
            .with_for_update()
        )
        if row is None:
            raise RuntimeError("connector_run_missing")
        row.status = status
        row.result = result
        row.error_code = error_code
        row.finished_at = datetime.now(UTC)
        if result.get("upstream_release_id"):
            row.upstream_release_id = str(result["upstream_release_id"])
        if result.get("local_knowledge_release_id"):
            row.local_release_id = str(result["local_knowledge_release_id"])
        repository = KnowledgeRepository(session, tenant_pub_id)
        repository.audit(
            namespace=NAMESPACE,
            domain=DOMAIN,
            actor="system:siliconindex-connector",
            action=f"connector_run.{status}",
            resource_type="connector_run",
            resource_pub_id=row.pub_id,
            receipt={
                "adapter": row.adapter,
                "operation": row.operation,
                "error_code": error_code,
                "result_hash": _hash(result),
            },
        )
        session.commit()


def _execute(
    tenant_pub_id: str,
    run_pub_id: str,
    *,
    snapshot_source: Path,
    knowledge_release_dir: Path,
) -> tuple[str, dict[str, Any]]:
    with SessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        row = session.scalar(
            select(ConnectorRun).where(
                ConnectorRun.tenant_pub_id == tenant_pub_id,
                ConnectorRun.pub_id == run_pub_id,
            )
        )
        if row is None or row.status != "running":
            raise RuntimeError("connector_run_not_claimed")
        adapter = SiliconIndexAdapter()
        if row.operation == "import":
            imported = adapter.import_release(str(snapshot_source))
            result = {
                "upstream_release_id": imported.upstream_release_id,
                **dict(imported.result),
            }
        elif row.operation == "export":
            repository = KnowledgeRepository(session, tenant_pub_id)
            values = tuple(
                value
                for value in _object_values(repository)
                if value["sync_status"] == "local_ahead"
            )
            exported = adapter.export_changes(values)
            document = {
                "schema_version": "siliconindex-change-bundle-v1",
                "changes": exported.result["changes"],
            }
            path, digest = _write_artifact(
                knowledge_release_dir,
                "siliconindex-export",
                document,
            )
            result = {
                "local_knowledge_release_id": KnowledgeReleaseStore(
                    knowledge_release_dir
                ).current_release_id(),
                "count": exported.result["count"],
                "content_hash": digest,
                "artifact": str(path),
            }
        elif row.operation == "reconcile":
            result = reconcile_snapshot(
                session,
                tenant_pub_id=tenant_pub_id,
                snapshot_source=snapshot_source,
                knowledge_release_dir=knowledge_release_dir,
                base_upstream_release_id=row.base_release_id,
            )
        elif row.operation == "publish":
            imported = adapter.import_release(str(snapshot_source))
            expected_release = row.upstream_release_id or row.cursor.get("expected_release_id")
            expected_hash = row.cursor.get("expected_content_hash")
            if expected_release != imported.upstream_release_id:
                raise SiliconIndexSyncError("published_release_not_observed")
            if expected_hash != imported.result.get("content_hash"):
                raise SiliconIndexSyncError("published_content_hash_mismatch")
            result = {
                "upstream_release_id": imported.upstream_release_id,
                "content_hash": imported.result["content_hash"],
                "git_commit": row.cursor.get("git_commit"),
                "verified": True,
            }
        else:
            raise RuntimeError("unsupported_connector_operation")
        session.commit()
    return ("conflict" if result.get("conflicts") else "success"), result


def run_queue(
    *,
    tenant_pub_id: str,
    snapshot_source: Path,
    knowledge_release_dir: Path,
    limit: int,
) -> dict[str, int]:
    claimed = _claim(tenant_pub_id, limit)
    completed = 0
    conflicts = 0
    failed = 0
    for run_pub_id in claimed:
        try:
            status, result = _execute(
                tenant_pub_id,
                run_pub_id,
                snapshot_source=snapshot_source,
                knowledge_release_dir=knowledge_release_dir,
            )
        except Exception as exc:
            failed += 1
            _complete(
                tenant_pub_id,
                run_pub_id,
                status="failed",
                result={},
                error_code=str(exc).split(":", 1)[0] or type(exc).__name__,
            )
            continue
        completed += 1
        conflicts += int(status == "conflict")
        _complete(
            tenant_pub_id,
            run_pub_id,
            status=status,
            result=result,
            error_code=None,
        )
    return {
        "claimed": len(claimed),
        "completed": completed,
        "conflicts": conflicts,
        "failed": failed,
    }


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tenant-pub-id",
        default=settings.knowledge_governance_tenant_pub_id,
    )
    parser.add_argument(
        "--snapshot-source",
        type=Path,
        default=Path(settings.siliconindex_snapshot_dir),
    )
    parser.add_argument(
        "--knowledge-release-dir",
        type=Path,
        default=Path(settings.knowledge_release_dir),
    )
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    if not args.tenant_pub_id:
        raise SystemExit("GEO_KNOWLEDGE_GOVERNANCE_TENANT_PUB_ID is required")
    result = run_queue(
        tenant_pub_id=args.tenant_pub_id,
        snapshot_source=args.snapshot_source,
        knowledge_release_dir=args.knowledge_release_dir,
        limit=max(1, min(args.limit, 100)),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
