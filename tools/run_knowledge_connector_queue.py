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
    KnowledgeRelease,
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
    SiliconIndexSynchronizer,
    preview_change_bundle,
    project_brand_domain,
    publish_change_bundle,
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


def _trusted_connector_artifact(root: Path, value: Any) -> Path:
    path = Path(str(value or "")).resolve()
    trusted_root = (root / "connector-artifacts").resolve()
    if trusted_root not in path.parents or not path.is_file():
        raise SiliconIndexSyncError("connector_artifact_path_untrusted")
    return path


def _release_lineage(
    repository: KnowledgeRepository,
    *,
    release_id: str,
) -> list[KnowledgeRelease]:
    releases = {
        row.release_id: row for row in repository.list_releases(namespace=NAMESPACE, domain=DOMAIN)
    }
    lineage: list[KnowledgeRelease] = []
    current_id: str | None = release_id
    visited: set[str] = set()
    while current_id is not None:
        if current_id in visited:
            raise SiliconIndexSyncError("knowledge_release_lineage_cycle")
        visited.add(current_id)
        release = releases.get(current_id)
        if release is None:
            raise SiliconIndexSyncError("knowledge_release_lineage_incomplete")
        lineage.append(release)
        current_id = release.parent_release_id
    return lineage


def _lineage_change_sets(
    repository: KnowledgeRepository,
    session: Session,
    *,
    release_id: str,
) -> list[ChangeSet]:
    pub_ids: list[str] = []
    for release in _release_lineage(repository, release_id=release_id):
        values = release.quality_report.get("change_set_pub_ids")
        if isinstance(values, list):
            pub_ids.extend(str(value) for value in values if value)
    unique_ids = list(dict.fromkeys(pub_ids))
    if not unique_ids:
        return []
    rows = list(
        session.scalars(
            select(ChangeSet).where(
                ChangeSet.tenant_pub_id == repository.tenant_pub_id,
                ChangeSet.pub_id.in_(unique_ids),
            )
        )
    )
    if len(rows) != len(unique_ids):
        raise SiliconIndexSyncError("published_change_set_lineage_incomplete")
    by_pub_id = {row.pub_id: row for row in rows}
    return [by_pub_id[value] for value in unique_ids]


def _retirement_exports(
    repository: KnowledgeRepository,
    session: Session,
    *,
    local_release_id: str,
    current_object_ids: set[str],
) -> dict[str, dict[str, Any]]:
    retirements: dict[str, dict[str, Any]] = {}
    for change_set in _lineage_change_sets(
        repository,
        session,
        release_id=local_release_id,
    ):
        for change in change_set.changes:
            if (
                str(change.get("kind") or "") not in {"object", "knowledge_object"}
                or str(change.get("operation") or "") != "retire"
            ):
                continue
            stable_id = str(change.get("stable_id") or "").strip()
            if not stable_id or stable_id in current_object_ids or stable_id in retirements:
                continue
            retirements[stable_id] = {
                "operation": "retire",
                "stable_id": stable_id,
                "object_type": str(change.get("object_type") or "brand"),
                "visibility": str(change.get("visibility") or ""),
                "review_status": str(change.get("review_status") or ""),
                "evidence_refs": list(change.get("evidence_refs") or []),
                "origin": str(change.get("origin") or "governed_change_set"),
            }
    return retirements


def _publication_approval(
    repository: KnowledgeRepository,
    session: Session,
    *,
    knowledge_release_dir: Path,
    bundle_path: Path,
    target_release_id: str,
    repository_url: str,
    branch: str,
) -> Path:
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    if not isinstance(bundle, dict):
        raise SiliconIndexSyncError("change_bundle_invalid")
    local_release_id = str(bundle.get("local_knowledge_release_id") or "")
    if repository.active_release_id(namespace=NAMESPACE, domain=DOMAIN) != local_release_id:
        raise SiliconIndexSyncError("publication_requires_active_local_release")
    release = repository.scoped_release(
        namespace=NAMESPACE,
        domain=DOMAIN,
        release_id=local_release_id,
    )
    if release is None:
        raise SiliconIndexSyncError("local_release_record_missing")
    quality_report = dict(release.quality_report)
    impact_gate = quality_report.get("impact_gate")
    if (
        not isinstance(impact_gate, dict)
        or impact_gate.get("passed") is not True
        or impact_gate.get("execution") != "server"
        or impact_gate.get("candidate_release_id") != local_release_id
    ):
        raise SiliconIndexSyncError("trusted_historical_replay_gate_missing")
    change_sets = _lineage_change_sets(
        repository,
        session,
        release_id=local_release_id,
    )
    if not change_sets:
        raise SiliconIndexSyncError("published_change_set_lineage_missing")
    bundle_hash = _hash(bundle)
    reconciliations = list(
        session.scalars(
            select(ConnectorRun).where(
                ConnectorRun.tenant_pub_id == repository.tenant_pub_id,
                ConnectorRun.namespace == NAMESPACE,
                ConnectorRun.domain == DOMAIN,
                ConnectorRun.operation == "reconcile",
                ConnectorRun.status == "success",
                ConnectorRun.local_release_id == local_release_id,
                ConnectorRun.upstream_release_id == bundle.get("base_upstream_release_id"),
            )
        )
    )
    reconciliation = next(
        (
            row
            for row in reconciliations
            if not row.result.get("conflicts")
            and row.result.get("can_prepare_merge") is True
            and isinstance(row.result.get("local_export"), dict)
            and row.result["local_export"].get("content_hash") == bundle_hash
        ),
        None,
    )
    if reconciliation is None:
        raise SiliconIndexSyncError("successful_reconciliation_lineage_missing")
    reviewers = sorted(
        {
            str(value)
            for value in [release.created_by, *(row.approved_by for row in change_sets)]
            if value
        }
    )
    if len(reviewers) < 2:
        raise SiliconIndexSyncError("independent_publication_reviewers_required")
    preview = preview_change_bundle(
        repository_url=repository_url,
        branch=branch,
        bundle_path=bundle_path,
        release_id=target_release_id,
    )
    approval = {
        "schema_version": "siliconindex-change-bundle-approval-v1",
        "decision": "approved",
        "bundle_hash": bundle_hash,
        "base_upstream_release_id": bundle.get("base_upstream_release_id"),
        "local_knowledge_release_id": local_release_id,
        "target_release_id": target_release_id,
        "result_content_hash": preview.get("result_content_hash"),
        "historical_replay_report_hash": _hash(impact_gate),
        "reviewers": reviewers,
        "review_basis": [
            f"knowledge_release:{local_release_id}",
            *[
                f"change_set:{row.pub_id}"
                for row in sorted(change_sets, key=lambda row: row.pub_id)
            ],
            f"server_replay:{impact_gate.get('evaluation_set_hash')}",
            f"reconciliation:{reconciliation.pub_id}",
        ],
    }
    approval_path, _ = _write_artifact(
        knowledge_release_dir,
        "siliconindex-approval",
        approval,
    )
    return approval_path


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


def _assert_activation_consistent(
    repository: KnowledgeRepository,
    store: KnowledgeReleaseStore,
) -> str:
    artifact_release_id = store.current_release_id()
    database_release_id = repository.active_release_id(namespace=NAMESPACE, domain=DOMAIN)
    if artifact_release_id is None or database_release_id != artifact_release_id:
        raise SiliconIndexSyncError(
            "knowledge_activation_state_mismatch:"
            f"artifact={artifact_release_id or 'none'}:database={database_release_id or 'none'}"
        )
    store.verify(artifact_release_id)
    return artifact_release_id


def _entity_map(source: Path) -> dict[str, dict[str, Any]]:
    projection = project_brand_domain(source, analysis_domain=ANALYSIS_DOMAIN)
    return {str(row["entity_id"]): dict(row) for row in projection["entities"]}


def _local_map(
    base: dict[str, dict[str, Any]],
    local_objects: tuple[dict[str, Any], ...],
    *,
    retired_ids: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    local = {key: dict(value) for key, value in base.items()}
    for stable_id in retired_ids or set():
        local.pop(stable_id, None)
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
    local_release_id = _assert_activation_consistent(repository, store)
    base_release_id = base_upstream_release_id or _knowledge_source_release(store)
    if base_release_id is None:
        base_release_id = upstream_release_id
    base_source = _release_source(snapshot_source, base_release_id, upstream_release_id)
    local_objects = _object_values(repository)
    local_by_id = {str(value["stable_id"]): value for value in local_objects}
    retirements = _retirement_exports(
        repository,
        session,
        local_release_id=local_release_id,
        current_object_ids=set(local_by_id),
    )
    base = _entity_map(base_source)
    untraced_missing_reviewed_ids = sorted(
        stable_id
        for stable_id, value in base.items()
        if value.get("review_status") == "reviewed"
        and stable_id not in local_by_id
        and stable_id not in retirements
    )
    upstream = _entity_map(snapshot_source)
    local = _local_map(base, local_objects, retired_ids=set(retirements))
    merge = adapter.reconcile_brand_projection(
        base_source=base_source,
        upstream_source=snapshot_source,
        analysis_domain=ANALYSIS_DOMAIN,
        local_objects=local_objects,
        retired_ids=set(retirements),
    )
    upstream_changes = _changed_ids(base, upstream)
    local_changes_from_base = _changed_ids(base, local)
    local_changed_ids = [
        stable_id
        for stable_id in local_changes_from_base
        if upstream.get(stable_id) != local.get(stable_id)
    ]
    local_converged_ids = [
        stable_id
        for stable_id in local_changes_from_base
        if upstream.get(stable_id) == local.get(stable_id)
    ]
    ingestion = _ingest_upstream_changes(
        repository,
        session,
        base=base,
        upstream=upstream,
        upstream_release_id=upstream_release_id,
        upstream_content_hash=upstream_hash,
    )
    conflicts = _conflict_values(merge.conflicts)
    export_values: list[dict[str, Any]] = []
    unexportable_local_ids: list[str] = list(untraced_missing_reviewed_ids)
    merged = merge.merged if isinstance(merge.merged, dict) else {}
    if not conflicts:
        for stable_id in local_changed_ids:
            if stable_id not in local:
                retirement = retirements.get(stable_id)
                if retirement is None:
                    unexportable_local_ids.append(stable_id)
                else:
                    export_values.append(retirement)
                continue
            local_record = local_by_id.get(stable_id)
            merged_attributes = merged.get(stable_id)
            if (
                local_record is None
                or local_record.get("sync_status") != "local_ahead"
                or not isinstance(merged_attributes, dict)
            ):
                unexportable_local_ids.append(stable_id)
                continue
            export_values.append(
                {
                    **local_record,
                    "operation": "upsert",
                    "attributes": {
                        "analysis_domain": ANALYSIS_DOMAIN,
                        **dict(merged_attributes),
                    },
                }
            )
    exported = adapter.export_changes(tuple(export_values))
    export_document = {
        "schema_version": "siliconindex-change-bundle-v1",
        "base_upstream_release_id": upstream_release_id,
        "local_knowledge_release_id": local_release_id,
        "changes": exported.result["changes"],
    }
    export_path, export_hash = _write_artifact(
        knowledge_release_dir,
        "siliconindex-export",
        export_document,
    )
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
        "local_changes_from_base": local_changes_from_base,
        "local_changed_ids": local_changed_ids,
        "local_converged_ids": local_converged_ids,
        "unexportable_local_ids": unexportable_local_ids,
        "conflicts": conflicts,
        "conflict_change_set_pub_id": conflict_change_set,
        "review_required": bool(
            upstream_changes or local_changed_ids or conflicts or unexportable_local_ids
        ),
        "can_prepare_merge": not conflicts and not unexportable_local_ids,
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
            result = reconcile_snapshot(
                session,
                tenant_pub_id=tenant_pub_id,
                snapshot_source=snapshot_source,
                knowledge_release_dir=knowledge_release_dir,
                base_upstream_release_id=row.base_release_id,
            )
            result["operation_mode"] = "reconciled_export"
        elif row.operation == "reconcile":
            result = reconcile_snapshot(
                session,
                tenant_pub_id=tenant_pub_id,
                snapshot_source=snapshot_source,
                knowledge_release_dir=knowledge_release_dir,
                base_upstream_release_id=row.base_release_id,
            )
        elif row.operation == "publish":
            mode = str(row.cursor.get("mode") or "verify_only")
            if mode == "apply_and_publish":
                settings = get_settings()
                if not settings.siliconindex_publisher_enabled:
                    raise SiliconIndexSyncError("siliconindex_publisher_disabled")
                bundle_path = _trusted_connector_artifact(
                    knowledge_release_dir,
                    row.cursor.get("bundle_artifact"),
                )
                target_release_id = str(
                    row.upstream_release_id or row.cursor.get("target_release_id") or ""
                )
                repository = KnowledgeRepository(session, tenant_pub_id)
                if row.cursor.get("approval_artifact"):
                    raise SiliconIndexSyncError("caller_supplied_publication_approval_not_accepted")
                approval_path = _publication_approval(
                    repository,
                    session,
                    knowledge_release_dir=knowledge_release_dir,
                    bundle_path=bundle_path,
                    target_release_id=target_release_id,
                    repository_url=settings.siliconindex_publisher_repository_url,
                    branch=settings.siliconindex_publisher_branch,
                )
                publication = publish_change_bundle(
                    repository_url=settings.siliconindex_publisher_repository_url,
                    branch=settings.siliconindex_publisher_branch,
                    bundle_path=bundle_path,
                    approval_path=approval_path,
                    release_id=target_release_id,
                    public_base_url=settings.siliconindex_base_url,
                    deploy_timeout_seconds=(settings.siliconindex_publisher_deploy_timeout_seconds),
                    poll_seconds=settings.siliconindex_publisher_poll_seconds,
                )
                synchronized = SiliconIndexSynchronizer(
                    snapshot_source,
                    settings.siliconindex_base_url,
                ).sync()
                if synchronized.get("current") != target_release_id:
                    raise SiliconIndexSyncError("published_release_sync_mismatch")
                result = {
                    "upstream_release_id": target_release_id,
                    "local_knowledge_release_id": row.local_release_id,
                    "content_hash": publication["content_hash"],
                    "git_commit": publication["git_commit"],
                    "publication": publication,
                    "synchronization": synchronized,
                    "verified": True,
                }
            elif mode == "verify_only":
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
                raise SiliconIndexSyncError("unsupported_publish_mode")
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
