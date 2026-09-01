"""Application service for runtime reasoning, governed release, and activation."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sqlalchemy.orm import Session

from domain.knowledge_evolution.contracts import RuntimeRequest
from domain.knowledge_evolution.domains.brand import BrandEntityResolutionPack
from domain.knowledge_evolution.domains.source_type_fixture import SourceTypeFixturePack
from domain.knowledge_evolution.gateway import OpenAICompatibleGateway
from domain.knowledge_evolution.registry import DomainRegistry
from domain.knowledge_evolution.release import KnowledgeReleaseError, KnowledgeReleaseStore
from domain.knowledge_evolution.runtime import ReasoningEngine, ReasoningError

from ..config import Settings
from .inference_models import (
    KnowledgeModelError,
    KnowledgeModelNotApplicable,
    catalog_revision,
    resolve_model,
)
from .models import Assertion, KnowledgeObject, KnowledgeRelease
from .repository import KnowledgeConflict, KnowledgeRepository
from .schemas import ReleaseCreate, RuntimeResolveRequest


def registry(settings: Settings) -> DomainRegistry:
    value = DomainRegistry()
    value.register(
        BrandEntityResolutionPack(
            snapshot_dir=settings.siliconindex_snapshot_dir,
            knowledge_release_dir=settings.knowledge_release_dir,
        )
    )
    value.register(SourceTypeFixturePack(knowledge_release_dir=settings.knowledge_release_dir))
    return value


def gateway(settings: Settings, model: str | None = None) -> OpenAICompatibleGateway | None:
    api_key = settings.knowledge_llm_api_key or settings.research_llm_api_key
    base_url = settings.knowledge_llm_base_url or settings.research_llm_base_url
    fallback = settings.knowledge_llm_base_url_fallback or settings.research_llm_base_url_fallback
    try:
        resolved_model = resolve_model(settings, model)
    except KnowledgeModelError:
        return None
    if not api_key or not base_url or not resolved_model:
        return None
    return OpenAICompatibleGateway(
        api_key=api_key,
        base_url=base_url,
        base_url_fallback=fallback,
        provider=settings.knowledge_llm_provider,
        model=resolved_model,
        model_version=settings.knowledge_llm_model_version,
        catalog_revision=catalog_revision(settings),
        timeout_seconds=settings.knowledge_llm_timeout_seconds,
        max_retries=settings.knowledge_llm_max_retries,
    )


def resolve(
    *,
    session: Session,
    settings: Settings,
    tenant_pub_id: str,
    request_id: str,
    body: RuntimeResolveRequest,
) -> dict[str, Any]:
    if body.policy.value == "deterministic_only" and body.model is not None:
        raise KnowledgeModelNotApplicable()
    selected_model = (
        None if body.policy.value == "deterministic_only" else resolve_model(settings, body.model)
    )
    selected_catalog_revision = catalog_revision(settings) if selected_model is not None else None
    repository = KnowledgeRepository(
        session,
        tenant_pub_id,
        namespace=body.namespace,
        domain=body.domain,
    )
    runtime = RuntimeRequest(
        request_id=body.request_id or request_id,
        tenant=tenant_pub_id,
        namespace=body.namespace,
        domain=body.domain,
        task=body.task,
        items=tuple(body.items),
        context=body.context,
        policy=body.policy,
        policy_id=body.policy_id,
        policy_version=body.policy_version,
        adopt_model_inferred=body.adopt_model_inferred,
        on_model_failure=body.on_model_failure,
        expected_release_id=body.expected_release_id,
        data_classification=body.data_classification,
        allow_external_model=body.allow_external_model,
        max_latency_ms=body.max_latency_ms,
        max_cost_usd=body.max_cost_usd,
        model=selected_model,
        model_catalog_revision=selected_catalog_revision,
    )
    try:
        response = ReasoningEngine(
            registry(settings),
            repository,
            gateway(settings, selected_model) if selected_model is not None else None,
        ).decide(runtime)
    except ReasoningError:
        # Fail-fast model policy still produces a sanitized inference trace. The
        # router's subsequent rollback must not erase that operational evidence.
        session.commit()
        raise
    session.commit()
    return asdict(response)


def _object_mapping(row: KnowledgeObject) -> dict[str, Any]:
    return {
        "stable_id": row.stable_id,
        "object_type": row.object_type,
        "attributes": dict(row.attributes),
        "origin": row.origin,
        "review_status": row.review_status,
        "visibility": row.visibility,
        "sync_status": row.sync_status,
        "version": row.version,
    }


def _assertion_mapping(row: Assertion) -> dict[str, Any]:
    return {
        "subject_stable_id": row.subject_stable_id,
        "predicate": row.predicate,
        "object_stable_id": row.object_stable_id,
        "object_value": dict(row.object_value),
        "scope": dict(row.scope),
        "evidence_refs": list(row.evidence_refs),
        "epistemic_status": row.epistemic_status,
        "review_status": row.review_status,
        "confidence_ppm": row.confidence_ppm,
        "version": row.version,
    }


def _preview_state(
    repository: KnowledgeRepository,
    *,
    namespace: str,
    domain: str,
    release_id: str | None,
    changes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    objects = {
        row.stable_id: _object_mapping(row)
        for row in repository.current_objects(
            namespace=namespace,
            domain=domain,
            release_id=release_id,
        )
    }
    assertions = [
        _assertion_mapping(row)
        for row in repository.assertions(
            namespace=namespace,
            domain=domain,
            release_id=release_id,
        )
    ]
    for change in changes:
        kind = str(change.get("kind") or "")
        operation = str(change.get("operation") or "")
        if kind in {"object", "knowledge_object"}:
            stable_id = str(change.get("stable_id") or "").strip()
            object_type = str(change.get("object_type") or "").strip()
            if not stable_id or not object_type:
                raise KnowledgeConflict("object_identity_required")
            if operation == "retire":
                objects.pop(stable_id, None)
            elif operation == "upsert":
                objects[stable_id] = {
                    "stable_id": stable_id,
                    "object_type": object_type,
                    "attributes": dict(change.get("attributes") or {}),
                    "origin": str(change.get("origin") or "governed_change_set"),
                    "review_status": str(change.get("review_status") or "reviewed"),
                    "visibility": str(change.get("visibility") or "tenant"),
                    "sync_status": str(change.get("sync_status") or "local_ahead"),
                }
            else:
                raise KnowledgeConflict("unsupported_object_change")
        elif kind in {"relation", "assertion"}:
            if operation not in {"append", "assert"}:
                raise KnowledgeConflict("unsupported_assertion_change")
            assertions.append(dict(change))
        else:
            raise KnowledgeConflict("unsupported_change_kind")
    return list(objects.values()), assertions


def verify_release_materialization(
    *,
    repository: KnowledgeRepository,
    settings: Settings,
    namespace: str,
    domain: str,
    release_id: str,
    release_document: dict[str, Any],
) -> None:
    objects = [
        _object_mapping(row)
        for row in repository.current_objects(
            namespace=namespace,
            domain=domain,
            release_id=release_id,
        )
    ]
    assertions = [
        _assertion_mapping(row)
        for row in repository.assertions(
            namespace=namespace,
            domain=domain,
            release_id=release_id,
        )
    ]
    pack = registry(settings).get(domain)
    gate = pack.validate_release(objects, assertions)
    if gate.get("passed") is not True:
        raise KnowledgeConflict("release_materialization_quality_failed")
    try:
        database_document = pack.project_release(objects, assertions)
        artifact_view = pack.materialization_view(release_document)
        database_view = pack.materialization_view(database_document)
    except (TypeError, ValueError) as exc:
        raise KnowledgeConflict("release_materialization_invalid") from exc
    if artifact_view != database_view:
        raise KnowledgeConflict("release_materialization_mismatch")


def publish_release(
    *,
    session: Session,
    settings: Settings,
    tenant_pub_id: str,
    actor: str,
    body: ReleaseCreate,
) -> KnowledgeRelease:
    repository = KnowledgeRepository(session, tenant_pub_id)
    change_sets = repository.approved_change_sets(body.change_set_pub_ids)
    if any(row.namespace != body.namespace or row.domain != body.domain for row in change_sets):
        raise KnowledgeConflict("change_set_scope_mismatch")
    if any(actor in {row.created_by, row.approved_by} for row in change_sets):
        raise KnowledgeConflict("independent_publisher_required")

    store = KnowledgeReleaseStore(settings.knowledge_release_dir)
    parent = store.current_release_id()
    database_parent = repository.active_release_id(
        namespace=body.namespace,
        domain=body.domain,
    )
    if database_parent != parent:
        raise KnowledgeConflict("knowledge_activation_state_mismatch")
    if any(
        row.base_release_id is not None and row.base_release_id != parent for row in change_sets
    ):
        raise KnowledgeConflict("change_set_base_release_mismatch")
    changes = [change for row in change_sets for change in row.changes]
    objects, assertions = _preview_state(
        repository,
        namespace=body.namespace,
        domain=body.domain,
        release_id=parent,
        changes=changes,
    )
    pack = registry(settings).get(body.domain)
    domain_gate = dict(pack.validate_release(objects, assertions))
    if domain_gate.get("passed") is not True:
        raise KnowledgeConflict("domain_quality_gate_failed")
    domain_document = pack.project_release(objects, assertions)
    impact_gate = dict(
        pack.evaluate_release_impact(
            changes=changes,
            candidate_document=domain_document,
            parent_release_id=parent,
            candidate_release_id=body.release_id,
        )
    )
    if impact_gate.get("passed") is not True:
        raise KnowledgeConflict("historical_replay_gate_failed")
    documents: dict[str, Any] = {}
    if parent is not None:
        documents, _ = store.load_documents(parent)
    documents[body.domain] = domain_document
    quality_report = {
        **body.quality_report,
        "quality_gate": "passed",
        "domain_gate": domain_gate,
        "impact_gate": impact_gate,
        "object_count": len(objects),
        "assertion_count": len(assertions),
        "change_set_count": len(change_sets),
        "change_count": len(changes),
        "change_set_pub_ids": [row.pub_id for row in change_sets],
    }
    manifest = store.publish(
        release_id=body.release_id,
        schema_version=body.schema_version,
        documents=documents,
        parent_release_id=parent,
        quality_report=quality_report,
        activate=False,
    )
    release = repository.add_release(
        namespace=body.namespace,
        domain=body.domain,
        release_id=body.release_id,
        parent_release_id=parent,
        schema_version=body.schema_version,
        content_hash=str(manifest["content_hash"]),
        artifact_uri=str(store.root / body.release_id),
        quality_report=quality_report,
        actor=actor,
    )
    repository.materialize_changes(
        namespace=body.namespace,
        domain=body.domain,
        changes=changes,
        release=release,
        base_release_id=parent,
    )
    repository.mark_change_sets_published(
        change_sets,
        release_id=body.release_id,
        actor=actor,
    )
    session.commit()

    if body.activate:
        activate_release(
            session=session,
            settings=settings,
            tenant_pub_id=tenant_pub_id,
            actor=actor,
            namespace=body.namespace,
            domain=body.domain,
            release_id=body.release_id,
            action="activate",
        )
    return release


def activate_release(
    *,
    session: Session,
    settings: Settings,
    tenant_pub_id: str,
    actor: str,
    namespace: str,
    domain: str,
    release_id: str,
    action: str,
) -> None:
    repository = KnowledgeRepository(session, tenant_pub_id)
    release = repository.release(release_id)
    if release.namespace != namespace or release.domain != domain:
        raise KnowledgeConflict("release_scope_mismatch")
    store = KnowledgeReleaseStore(settings.knowledge_release_dir)
    previous = store.current_release_id()
    documents, manifest = store.load_documents(release_id)
    if manifest["content_hash"] != release.content_hash:
        raise KnowledgeReleaseError("release_database_hash_mismatch")
    release_document = documents.get(domain)
    if not isinstance(release_document, dict):
        raise KnowledgeConflict("release_materialization_quality_failed")
    verify_release_materialization(
        repository=repository,
        settings=settings,
        namespace=namespace,
        domain=domain,
        release_id=release_id,
        release_document=release_document,
    )
    store.activate(release_id)
    try:
        repository.activate_release(
            namespace=namespace,
            domain=domain,
            release_id=release_id,
            previous_release_id=previous,
            action=action,
            actor=actor,
        )
        session.commit()
    except Exception:
        session.rollback()
        if previous is not None:
            store.activate(previous)
        raise


__all__ = [
    "activate_release",
    "gateway",
    "publish_release",
    "registry",
    "resolve",
    "verify_release_materialization",
]
