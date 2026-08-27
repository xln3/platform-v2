#!/usr/bin/env python3
"""Create the initial local brand release and optionally import its lineage."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.geo_platform.config import get_settings  # noqa: E402
from api.geo_platform.knowledge.models import (  # noqa: E402
    Adjudication,
    Candidate,
    ChangeSet,
    Evidence,
    KnowledgeObject,
    KnowledgeRelease,
    Proposal,
    ReleaseActivation,
)
from api.geo_platform.knowledge.repository import KnowledgeRepository  # noqa: E402
from api.geo_platform.tenancy.database import SessionLocal  # noqa: E402
from api.geo_platform.tenancy.ids import new_pub_id  # noqa: E402
from api.geo_platform.tenancy.repository import TenantRepository  # noqa: E402
from domain.knowledge_evolution.contracts import ObservationDraft  # noqa: E402
from domain.knowledge_evolution.domains.brand import BrandEntityResolutionPack  # noqa: E402
from domain.knowledge_evolution.release import KnowledgeReleaseStore  # noqa: E402


def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _key(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _entity_evidence_claims(entity: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """Return claim-specific references for a roll-up and its concrete identities."""

    stable_id = str(entity.get("entity_id") or "").strip()
    claims_by_uri: dict[str, set[str]] = {}
    for uri in entity.get("evidence_urls", []):
        rendered = str(uri).strip()
        if rendered:
            claims_by_uri.setdefault(rendered, set()).add(stable_id)
    identities = entity.get("alias_identities") or {}
    if not isinstance(identities, dict):
        raise SystemExit(f"projection_alias_identities_invalid:{stable_id}")
    for identity in identities.values():
        if not isinstance(identity, dict):
            raise SystemExit(f"projection_alias_identity_invalid:{stable_id}")
        identity_id = str(identity.get("entity_id") or "").strip()
        if not identity_id:
            raise SystemExit(f"projection_alias_identity_id_invalid:{stable_id}")
        evidence_urls = identity.get("evidence_urls")
        if not isinstance(evidence_urls, list) or not evidence_urls:
            raise SystemExit(f"reviewed_identity_without_evidence:{identity_id}")
        for uri in evidence_urls:
            rendered = str(uri).strip()
            if not rendered:
                raise SystemExit(f"reviewed_identity_evidence_invalid:{identity_id}")
            claims_by_uri.setdefault(rendered, set()).add(identity_id)
    return tuple(
        (
            uri,
            "Supports reviewed object(s): " + ", ".join(sorted(object_ids)) + ".",
        )
        for uri, object_ids in sorted(claims_by_uri.items())
    )


def _load_projection(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("entities"), list):
        raise SystemExit("invalid_projection")
    if value.get("source_system") != "siliconindex":
        raise SystemExit("projection_must_come_from_siliconindex")
    if not value.get("source_release_id") or not value.get("source_content_hash"):
        raise SystemExit("projection_source_release_required")
    entity_ids: set[str] = set()
    for row in value["entities"]:
        if not isinstance(row, dict):
            raise SystemExit("projection_entity_invalid")
        entity_id = str(row.get("entity_id") or "")
        if not entity_id or entity_id in entity_ids:
            raise SystemExit("projection_entity_id_invalid")
        entity_ids.add(entity_id)
        if row.get("review_status") == "reviewed" and not row.get("evidence_urls"):
            raise SystemExit(f"reviewed_entity_without_evidence:{entity_id}")
        if row.get("review_status") == "reviewed":
            _entity_evidence_claims(row)
    return value


def _document(projection: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    analysis_domain = str(projection["domain"])
    reviewed = [
        {
            **dict(row),
            "knowledge_status": "published",
            "origin": "siliconindex_import",
            "sync_status": "reconciled",
        }
        for row in projection["entities"]
        if row.get("review_status") == "reviewed"
    ]
    pending = [row for row in projection["entities"] if row.get("review_status") != "reviewed"]
    domain_projection = {
        key: value for key, value in projection.items() if key not in {"entities", "source_url"}
    }
    domain_projection["entities"] = reviewed
    document = {
        "schema_version": "brand-knowledge-v1",
        "domain": "brand/entity-resolution",
        "source_system": "siliconindex",
        "source_release_id": projection["source_release_id"],
        "source_content_hash": projection["source_content_hash"],
        "analysis_domains": {analysis_domain: domain_projection},
    }
    quality = {
        "quality_gate": "passed",
        "source_system": "siliconindex",
        "source_release_id": projection["source_release_id"],
        "source_content_hash": projection["source_content_hash"],
        "analysis_domain": analysis_domain,
        "entity_total": len(projection["entities"]),
        "reviewed_published": len(reviewed),
        "pending_candidates": len(pending),
        "reviewed_without_evidence": 0,
    }
    return document, quality


def _validated_historical_replay(
    path: Path,
    *,
    projection: Mapping[str, Any],
    baseline_release_id: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    report = json.loads(path.read_text(encoding="utf-8"))
    replay = report.get("historical_replay") if isinstance(report, dict) else None
    if not isinstance(replay, dict):
        raise SystemExit("historical_replay_report_invalid")
    if replay.get("candidate_release_id") != projection.get("source_release_id"):
        raise SystemExit("historical_replay_candidate_release_mismatch")
    if replay.get("candidate_content_hash") != projection.get("source_content_hash"):
        raise SystemExit("historical_replay_candidate_hash_mismatch")
    if baseline_release_id and replay.get("baseline_release_id") != baseline_release_id:
        raise SystemExit("historical_replay_baseline_release_mismatch")
    impact_gate = dict(
        BrandEntityResolutionPack().validate_release_impact(
            ({"kind": "knowledge_object"},),
            {"historical_replay": replay},
        )
    )
    if impact_gate.get("passed") is not True:
        raise SystemExit("historical_replay_gate_failed")
    return replay, impact_gate


def _verify_lineage_only_successor(
    *,
    parent_quality_report: dict[str, Any],
    projection: dict[str, Any],
    current_reviewed_objects: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Verify published objects are unchanged and describe the upstream transition."""

    previous_source_release = str(parent_quality_report.get("source_release_id") or "")
    previous_source_hash = str(parent_quality_report.get("source_content_hash") or "")
    if not previous_source_release or not previous_source_hash:
        raise RuntimeError("lineage_only_parent_source_lineage_missing")

    source_release = str(projection["source_release_id"])
    source_content_hash = str(projection["source_content_hash"])
    reviewed_entities = {
        str(entity["entity_id"]): {
            "analysis_domain": projection["domain"],
            **dict(entity),
        }
        for entity in projection["entities"]
        if entity.get("review_status") == "reviewed"
    }
    if set(current_reviewed_objects) != set(reviewed_entities):
        raise RuntimeError("lineage_only_governed_object_set_changed")
    if any(
        dict(current_reviewed_objects[stable_id]) != attributes
        for stable_id, attributes in reviewed_entities.items()
    ):
        raise RuntimeError("lineage_only_governed_object_content_changed")

    return {
        "reviewed_objects_verified": len(reviewed_entities),
        "previous_source_release_id": previous_source_release,
        "source_release_id": source_release,
        "source_release_changed": previous_source_release != source_release,
        "previous_source_content_hash": previous_source_hash,
        "source_content_hash": source_content_hash,
        "source_content_hash_changed": previous_source_hash != source_content_hash,
    }


def _import_database(
    *,
    tenant_pub_id: str,
    release_id: str,
    manifest: dict[str, Any],
    projection: dict[str, Any],
    artifact_uri: str,
) -> dict[str, Any]:
    namespace = "shared"
    domain = "brand/entity-resolution"
    source_release = str(projection["source_release_id"])
    import_actor = f"siliconindex:{source_release}"
    reviewer = "migration:reviewed-source"
    publisher = "migration:release-publisher"
    with SessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        existing = session.scalar(
            select(KnowledgeRelease).where(
                KnowledgeRelease.tenant_pub_id == tenant_pub_id,
                KnowledgeRelease.namespace == namespace,
                KnowledgeRelease.domain == domain,
                KnowledgeRelease.release_id == release_id,
            )
        )
        if existing is not None:
            return {"database": "already_imported", "release_pub_id": existing.pub_id}
        repository = KnowledgeRepository(session, tenant_pub_id)
        source_ref_hash = _hash(f"siliconindex:{source_release}")
        now = datetime.now(UTC)
        reviewed_changes: list[dict[str, Any]] = []
        reviewed_count = 0
        pending_count = 0
        for entity in projection["entities"]:
            entity_id = str(entity["entity_id"])
            canonical = str(entity["canonical_name"])
            status = str(entity.get("review_status") or "pending")
            observation = ObservationDraft(
                namespace=namespace,
                domain=domain,
                task="siliconindex_import",
                surface_form=canonical,
                normalized_key=_key(canonical),
                source_type="siliconindex_release",
                source_ref_hash=source_ref_hash,
                idempotency_key=hashlib.sha256(
                    f"{source_release}|{entity_id}".encode()
                ).hexdigest(),
                safe_context=None,
                data_classification="public",
                visibility="public",
                payload={
                    "source_release_id": source_release,
                    "source_content_hash": projection["source_content_hash"],
                    "review_status": status,
                    "policy_version": "siliconindex-schema-1.2.0",
                },
            )
            repository.record_observations(tenant_pub_id, (observation,))
            candidate = session.scalar(
                select(Candidate).where(
                    Candidate.tenant_pub_id == tenant_pub_id,
                    Candidate.namespace == namespace,
                    Candidate.domain == domain,
                    Candidate.aggregation_key == _hash(_key(canonical)),
                )
            )
            if candidate is None:
                raise RuntimeError("candidate_import_failed")
            if status != "reviewed":
                pending_count += 1
                continue
            reviewed_count += 1
            attributes = {"analysis_domain": projection["domain"], **dict(entity)}
            proposal = Proposal(
                pub_id=new_pub_id("kpr"),
                tenant_pub_id=tenant_pub_id,
                namespace=namespace,
                domain=domain,
                candidate_id=candidate.id,
                operation="create",
                target_stable_id=entity_id,
                payload=attributes,
                alternatives=[],
                confidence={"source_review_status": "reviewed"},
                policy_version="siliconindex-schema-1.2.0",
                state="approved",
                created_by=import_actor,
            )
            session.add(proposal)
            session.flush()
            evidence_pub_ids: list[str] = []
            for uri, claim in _entity_evidence_claims(entity):
                evidence = Evidence(
                    pub_id=new_pub_id("kev"),
                    tenant_pub_id=tenant_pub_id,
                    namespace=namespace,
                    domain=domain,
                    candidate_id=candidate.id,
                    proposal_id=proposal.id,
                    source_uri=str(uri),
                    content_hash=_hash(f"evidence-reference:{uri}"),
                    publisher=str(uri).split("/", 3)[2],
                    claim=claim,
                    stance="supports",
                    summary="Reviewed public source reference imported from SiliconIndex.",
                    trust_tier="primary",
                    visibility="public",
                    data_classification="public",
                    acquired_at=now,
                    created_by=import_actor,
                )
                session.add(evidence)
                session.flush()
                evidence_pub_ids.append(evidence.pub_id)
            session.add(
                Adjudication(
                    pub_id=new_pub_id("kad"),
                    tenant_pub_id=tenant_pub_id,
                    namespace=namespace,
                    domain=domain,
                    proposal_id=proposal.id,
                    decision="approved",
                    reason=(
                        "Imported from a reviewed SiliconIndex object with at least one "
                        "public evidence reference."
                    ),
                    policy_version="migration-policy-v1",
                    before_value={},
                    after_value=attributes,
                    decided_by=reviewer,
                )
            )
            session.add(
                KnowledgeObject(
                    pub_id=new_pub_id("kno"),
                    tenant_pub_id=tenant_pub_id,
                    namespace=namespace,
                    domain=domain,
                    stable_id=entity_id,
                    object_type=str(entity.get("entity_type") or "company"),
                    attributes=attributes,
                    origin="siliconindex_import",
                    review_status="reviewed",
                    visibility="public",
                    sync_status="reconciled",
                    version=1,
                )
            )
            candidate.state = "local_published"
            candidate.evidence_version = source_release
            reviewed_changes.append(
                {
                    "kind": "knowledge_object",
                    "operation": "upsert",
                    "proposal_pub_id": proposal.pub_id,
                    "stable_id": entity_id,
                    "object_type": str(entity.get("entity_type") or "company"),
                    "attributes": attributes,
                    "review_status": "reviewed",
                    "visibility": "public",
                    "evidence_pub_ids": evidence_pub_ids,
                }
            )
        change_set = ChangeSet(
            pub_id=new_pub_id("kcs"),
            tenant_pub_id=tenant_pub_id,
            namespace=namespace,
            domain=domain,
            base_release_id=None,
            changes=reviewed_changes,
            dependency_ids=[],
            conflicts=[],
            visibility="public",
            state="local_published",
            created_by=import_actor,
            approved_by=reviewer,
            approved_at=now,
        )
        session.add(change_set)
        release = repository.add_release(
            namespace=namespace,
            domain=domain,
            release_id=release_id,
            parent_release_id=None,
            schema_version=str(manifest["schema_version"]),
            content_hash=str(manifest["content_hash"]),
            artifact_uri=artifact_uri,
            quality_report=dict(manifest["quality_report"]),
            actor=publisher,
        )
        repository.activate_release(
            namespace=namespace,
            domain=domain,
            release_id=release_id,
            previous_release_id=None,
            action="activate",
            actor=publisher,
        )
        connector = repository.create_connector_run(
            {
                "namespace": namespace,
                "domain": domain,
                "adapter": "siliconindex-static-v2",
                "operation": "import",
                "status": "success",
                "upstream_release_id": source_release,
                "local_release_id": release_id,
                "result": {
                    "reviewed_imported": reviewed_count,
                    "pending_candidates": pending_count,
                },
                "finished_at": now,
            }
        )
        repository.audit(
            namespace=namespace,
            domain=domain,
            actor=publisher,
            action="connector_run.completed",
            resource_type="connector_run",
            resource_pub_id=connector.pub_id,
            receipt=dict(connector.result),
        )
        session.commit()
        return {
            "database": "imported",
            "release_pub_id": release.pub_id,
            "reviewed_imported": reviewed_count,
            "pending_candidates": pending_count,
        }


def _record_database_lineage_only(
    *,
    tenant_pub_id: str,
    release_id: str,
    manifest: dict[str, Any],
    projection: dict[str, Any],
    artifact_uri: str,
) -> dict[str, Any]:
    """Record a metadata-only successor when the governed objects did not change."""

    namespace = "shared"
    domain = "brand/entity-resolution"
    source_release = str(projection["source_release_id"])
    parent_release_id = manifest.get("parent_release_id")
    if not isinstance(parent_release_id, str) or not parent_release_id:
        raise RuntimeError("lineage_only_parent_release_required")

    publisher = "migration:release-publisher"
    now = datetime.now(UTC)
    with SessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        existing = session.scalar(
            select(KnowledgeRelease).where(
                KnowledgeRelease.tenant_pub_id == tenant_pub_id,
                KnowledgeRelease.namespace == namespace,
                KnowledgeRelease.domain == domain,
                KnowledgeRelease.release_id == release_id,
            )
        )
        if existing is not None:
            if existing.content_hash != manifest["content_hash"]:
                raise RuntimeError("existing_release_content_hash_mismatch")
            return {"database": "already_recorded", "release_pub_id": existing.pub_id}

        parent = session.scalar(
            select(KnowledgeRelease).where(
                KnowledgeRelease.tenant_pub_id == tenant_pub_id,
                KnowledgeRelease.namespace == namespace,
                KnowledgeRelease.domain == domain,
                KnowledgeRelease.release_id == parent_release_id,
            )
        )
        if parent is None:
            raise RuntimeError("lineage_only_parent_release_not_found")
        latest_activation = session.scalar(
            select(ReleaseActivation)
            .where(
                ReleaseActivation.tenant_pub_id == tenant_pub_id,
                ReleaseActivation.namespace == namespace,
                ReleaseActivation.domain == domain,
            )
            .order_by(ReleaseActivation.occurred_at.desc(), ReleaseActivation.pub_id.desc())
            .limit(1)
        )
        if latest_activation is None or latest_activation.release_id != parent_release_id:
            raise RuntimeError("lineage_only_parent_is_not_active")

        repository = KnowledgeRepository(session, tenant_pub_id)
        current_objects = repository.current_objects(namespace=namespace, domain=domain)
        verification = _verify_lineage_only_successor(
            parent_quality_report=dict(parent.quality_report),
            projection=projection,
            current_reviewed_objects={
                row.stable_id: dict(row.attributes)
                for row in current_objects
                if row.review_status == "reviewed"
            },
        )

        release = repository.add_release(
            namespace=namespace,
            domain=domain,
            release_id=release_id,
            parent_release_id=parent_release_id,
            schema_version=str(manifest["schema_version"]),
            content_hash=str(manifest["content_hash"]),
            artifact_uri=artifact_uri,
            quality_report=dict(manifest["quality_report"]),
            actor=publisher,
        )
        repository.activate_release(
            namespace=namespace,
            domain=domain,
            release_id=release_id,
            previous_release_id=parent_release_id,
            action="activate",
            actor=publisher,
        )
        connector = repository.create_connector_run(
            {
                "namespace": namespace,
                "domain": domain,
                "adapter": "siliconindex-static-v2",
                "operation": "reconcile",
                "status": "success",
                "base_release_id": parent_release_id,
                "upstream_release_id": source_release,
                "local_release_id": release_id,
                "result": {
                    "mode": "lineage_only",
                    "outcome": "zero_data_change",
                    **verification,
                },
                "finished_at": now,
            }
        )
        repository.audit(
            namespace=namespace,
            domain=domain,
            actor=publisher,
            action="connector_run.completed",
            resource_type="connector_run",
            resource_pub_id=connector.pub_id,
            receipt=dict(connector.result),
        )
        session.commit()
        return {
            "database": "lineage_recorded",
            "release_pub_id": release.pub_id,
            "parent_release_id": parent_release_id,
            **verification,
            "outcome": "zero_data_change",
        }


def _database_import_mode(*, tenant_pub_id: str, projection: dict[str, Any]) -> str:
    """Select initial, metadata-only, or content-changing import without guessing."""

    with SessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        current = KnowledgeRepository(session, tenant_pub_id).current_objects(
            namespace="shared", domain="brand/entity-resolution"
        )
    if not current:
        return "initial"
    current_reviewed = {
        row.stable_id: dict(row.attributes) for row in current if row.review_status == "reviewed"
    }
    incoming_reviewed = {
        str(entity["entity_id"]): {
            "analysis_domain": projection["domain"],
            **dict(entity),
        }
        for entity in projection["entities"]
        if entity.get("review_status") == "reviewed"
    }
    return "lineage" if current_reviewed == incoming_reviewed else "successor"


def _import_database_successor(
    *,
    tenant_pub_id: str,
    release_id: str,
    manifest: dict[str, Any],
    projection: dict[str, Any],
    artifact_uri: str,
) -> dict[str, Any]:
    """Import a reviewed upstream content change as versioned, fully traced objects."""

    namespace = "shared"
    domain = "brand/entity-resolution"
    source_release = str(projection["source_release_id"])
    import_actor = f"siliconindex:{source_release}"
    reviewer = "migration:reviewed-source"
    publisher = "migration:release-publisher"
    now = datetime.now(UTC)
    with SessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        repository = KnowledgeRepository(session, tenant_pub_id)
        existing = session.scalar(
            select(KnowledgeRelease).where(
                KnowledgeRelease.tenant_pub_id == tenant_pub_id,
                KnowledgeRelease.namespace == namespace,
                KnowledgeRelease.domain == domain,
                KnowledgeRelease.release_id == release_id,
            )
        )
        if existing is not None:
            if existing.content_hash != manifest["content_hash"]:
                raise RuntimeError("existing_release_content_hash_mismatch")
            return {"database": "already_imported", "release_pub_id": existing.pub_id}

        activation = session.scalar(
            select(ReleaseActivation)
            .where(
                ReleaseActivation.tenant_pub_id == tenant_pub_id,
                ReleaseActivation.namespace == namespace,
                ReleaseActivation.domain == domain,
            )
            .order_by(ReleaseActivation.occurred_at.desc(), ReleaseActivation.pub_id.desc())
            .limit(1)
        )
        if activation is None or activation.release_id != manifest.get("parent_release_id"):
            raise RuntimeError("successor_parent_is_not_active")
        current = {
            row.stable_id: row
            for row in repository.current_objects(namespace=namespace, domain=domain)
        }
        incoming = {
            str(entity["entity_id"]): entity
            for entity in projection["entities"]
            if entity.get("review_status") == "reviewed"
        }
        removed = {
            stable_id
            for stable_id, row in current.items()
            if row.review_status == "reviewed" and stable_id not in incoming
        }
        if removed:
            raise RuntimeError(
                "reviewed_object_removal_requires_manual_change_set:" + ",".join(sorted(removed))
            )

        source_ref_hash = _hash(f"siliconindex:{source_release}")
        changes: list[dict[str, Any]] = []
        candidates: list[Candidate] = []
        for stable_id, entity in sorted(incoming.items()):
            attributes = {"analysis_domain": projection["domain"], **dict(entity)}
            previous = current.get(stable_id)
            if previous is not None and dict(previous.attributes) == attributes:
                continue
            canonical = str(entity["canonical_name"])
            repository.record_observations(
                tenant_pub_id,
                (
                    ObservationDraft(
                        namespace=namespace,
                        domain=domain,
                        task="siliconindex_successor_import",
                        surface_form=canonical,
                        normalized_key=_key(canonical),
                        source_type="siliconindex_release",
                        source_ref_hash=source_ref_hash,
                        idempotency_key=hashlib.sha256(
                            f"{source_release}|{stable_id}|successor".encode()
                        ).hexdigest(),
                        safe_context=None,
                        data_classification="public",
                        visibility="public",
                        payload={
                            "source_release_id": source_release,
                            "source_content_hash": projection["source_content_hash"],
                            "review_status": "reviewed",
                            "policy_version": "siliconindex-schema-1.2.0",
                        },
                    ),
                ),
            )
            candidate = session.scalar(
                select(Candidate).where(
                    Candidate.tenant_pub_id == tenant_pub_id,
                    Candidate.namespace == namespace,
                    Candidate.domain == domain,
                    Candidate.aggregation_key == _hash(_key(canonical)),
                )
            )
            if candidate is None:
                raise RuntimeError("candidate_import_failed")
            candidates.append(candidate)
            proposal = Proposal(
                pub_id=new_pub_id("kpr"),
                tenant_pub_id=tenant_pub_id,
                namespace=namespace,
                domain=domain,
                candidate_id=candidate.id,
                operation="update" if previous is not None else "create",
                target_stable_id=stable_id,
                payload=attributes,
                alternatives=[],
                confidence={"source_review_status": "reviewed"},
                policy_version="siliconindex-schema-1.2.0",
                state="approved",
                created_by=import_actor,
            )
            session.add(proposal)
            session.flush()
            evidence_pub_ids: list[str] = []
            for uri, claim in _entity_evidence_claims(entity):
                evidence = Evidence(
                    pub_id=new_pub_id("kev"),
                    tenant_pub_id=tenant_pub_id,
                    namespace=namespace,
                    domain=domain,
                    candidate_id=candidate.id,
                    proposal_id=proposal.id,
                    source_uri=str(uri),
                    content_hash=_hash(f"evidence-reference:{uri}"),
                    publisher=str(uri).split("/", 3)[2],
                    claim=claim,
                    stance="supports",
                    summary="Public primary reference imported from the reviewed static release.",
                    trust_tier="primary",
                    visibility="public",
                    data_classification="public",
                    acquired_at=now,
                    created_by=import_actor,
                )
                session.add(evidence)
                session.flush()
                evidence_pub_ids.append(evidence.pub_id)
            if not evidence_pub_ids:
                raise RuntimeError(f"reviewed_entity_without_evidence:{stable_id}")
            session.add(
                Adjudication(
                    pub_id=new_pub_id("kad"),
                    tenant_pub_id=tenant_pub_id,
                    namespace=namespace,
                    domain=domain,
                    proposal_id=proposal.id,
                    decision="approved",
                    reason=(
                        "The static release preserved the stable object and supplied public "
                        "primary evidence after relationship-level review."
                    ),
                    policy_version="migration-policy-v2",
                    before_value=(dict(previous.attributes) if previous is not None else {}),
                    after_value=attributes,
                    decided_by=reviewer,
                )
            )
            changes.append(
                {
                    "kind": "knowledge_object",
                    "operation": "upsert",
                    "proposal_pub_id": proposal.pub_id,
                    "stable_id": stable_id,
                    "object_type": str(entity.get("entity_type") or "company"),
                    "attributes": attributes,
                    "origin": "siliconindex_import",
                    "review_status": "reviewed",
                    "visibility": "public",
                    "sync_status": "reconciled",
                    "evidence_pub_ids": evidence_pub_ids,
                }
            )
        if not changes:
            raise RuntimeError("successor_has_no_content_changes")

        change_set = repository.create_change_set(
            {
                "namespace": namespace,
                "domain": domain,
                "base_release_id": activation.release_id,
                "changes": changes,
                "dependency_ids": [],
                "conflicts": [],
                "visibility": "public",
            },
            actor=import_actor,
        )
        repository.approve_change_set(change_set.pub_id, actor=reviewer)
        repository.materialize_changes(namespace=namespace, domain=domain, changes=changes)
        change_set.state = "local_published"
        for candidate in candidates:
            candidate.state = "local_published"
            candidate.evidence_version = source_release
        release = repository.add_release(
            namespace=namespace,
            domain=domain,
            release_id=release_id,
            parent_release_id=activation.release_id,
            schema_version=str(manifest["schema_version"]),
            content_hash=str(manifest["content_hash"]),
            artifact_uri=artifact_uri,
            quality_report={
                **dict(manifest["quality_report"]),
                "change_set_pub_ids": [change_set.pub_id],
            },
            actor=publisher,
        )
        repository.activate_release(
            namespace=namespace,
            domain=domain,
            release_id=release_id,
            previous_release_id=activation.release_id,
            action="activate",
            actor=publisher,
        )
        connector = repository.create_connector_run(
            {
                "namespace": namespace,
                "domain": domain,
                "adapter": "siliconindex-static-v2",
                "operation": "reconcile",
                "status": "success",
                "base_release_id": activation.release_id,
                "upstream_release_id": source_release,
                "local_release_id": release_id,
                "result": {
                    "mode": "content_successor",
                    "changed_objects": len(changes),
                    "change_set_pub_id": change_set.pub_id,
                    "conflicts": 0,
                },
                "finished_at": now,
            }
        )
        repository.audit(
            namespace=namespace,
            domain=domain,
            actor=publisher,
            action="connector_run.completed",
            resource_type="connector_run",
            resource_pub_id=connector.pub_id,
            receipt=dict(connector.result),
        )
        session.commit()
        return {
            "database": "successor_imported",
            "release_pub_id": release.pub_id,
            "parent_release_id": activation.release_id,
            "changed_objects": len(changes),
            "change_set_pub_id": change_set.pub_id,
            "outcome": "content_change",
        }


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--projection",
        type=Path,
        default=PROJECT_ROOT
        / "domain/brandrank/rules_data/siliconindex_projection_cybersecurity.json",
    )
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--release-dir", default=settings.knowledge_release_dir)
    parser.add_argument("--database", action="store_true")
    parser.add_argument("--historical-replay-report", type=Path)
    parser.add_argument(
        "--lineage-only",
        action="store_true",
        help="Record a zero-data-change successor without duplicating governed objects.",
    )
    parser.add_argument(
        "--tenant-pub-id",
        default=settings.knowledge_governance_tenant_pub_id,
    )
    args = parser.parse_args()

    if args.lineage_only and not args.database:
        raise SystemExit("lineage_only_requires_database")

    projection = _load_projection(args.projection)
    document, quality = _document(projection)
    store = KnowledgeReleaseStore(args.release_dir)
    if args.database and args.historical_replay_report is None:
        raise SystemExit("historical_replay_report_required_for_database_import")
    if args.historical_replay_report is not None:
        replay, impact_gate = _validated_historical_replay(
            args.historical_replay_report,
            projection=projection,
            baseline_release_id=store.current_release_id(),
        )
        quality["historical_replay"] = replay
        quality["impact_gate"] = impact_gate
    manifest = store.publish(
        release_id=args.release_id,
        schema_version="knowledge-release-v1",
        documents={"brand/entity-resolution": document},
        parent_release_id=store.current_release_id(),
        quality_report=quality,
        # Keep the last verified artifact active until the database import has
        # committed.  A failed migration must never move the runtime pointer.
        activate=not args.database,
    )
    result: dict[str, Any] = {
        "release_id": manifest["release_id"],
        "content_hash": manifest["content_hash"],
        "artifact": str(Path(args.release_dir) / args.release_id),
        "quality_report": quality,
    }
    if args.database:
        if not args.tenant_pub_id:
            raise SystemExit("tenant_pub_id_required_for_database_import")
        mode = _database_import_mode(
            tenant_pub_id=args.tenant_pub_id,
            projection=projection,
        )
        if args.lineage_only and mode != "lineage":
            raise SystemExit(f"lineage_only_rejected_for_{mode}_import")
        importer = {
            "initial": _import_database,
            "lineage": _record_database_lineage_only,
            "successor": _import_database_successor,
        }[mode]
        result["lineage"] = importer(
            tenant_pub_id=args.tenant_pub_id,
            release_id=args.release_id,
            manifest=manifest,
            projection=projection,
            artifact_uri=str(Path(args.release_dir) / args.release_id),
        )
        store.activate(args.release_id)
        result["database_import_mode"] = mode
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
