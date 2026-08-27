"""Tenant-isolated durable storage for runtime feedback and governance."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from functools import wraps
from typing import Any

from sqlalchemy import Text, distinct, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from domain.knowledge_evolution.contracts import ObservationDraft

from ..tenancy.ids import new_pub_id
from .models import (
    Adjudication,
    Assertion,
    AuditEvent,
    Candidate,
    CandidateObservation,
    ChangeSet,
    ConnectorRun,
    Evidence,
    InferenceTrace,
    KnowledgeObject,
    KnowledgeRelease,
    KnowledgeReleaseAssertion,
    KnowledgeReleaseObject,
    Observation,
    Proposal,
    ReleaseActivation,
    SemanticCache,
)


class KnowledgeNotFound(LookupError):
    pass


class KnowledgeConflict(RuntimeError):
    pass


def _aggregation_key(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _savepoint(method: Any) -> Any:
    """Keep optional runtime persistence failures from aborting the request transaction."""

    @wraps(method)
    def wrapped(self: KnowledgeRepository, *args: Any, **kwargs: Any) -> Any:
        with self.session.begin_nested():
            return method(self, *args, **kwargs)

    return wrapped


class KnowledgeRepository:
    def __init__(
        self,
        session: Session,
        tenant_pub_id: str,
        *,
        namespace: str = "runtime",
        domain: str = "runtime",
    ) -> None:
        self.session = session
        self.tenant_pub_id = tenant_pub_id
        self.namespace = namespace
        self.domain = domain

    @_savepoint
    def cache_get(self, key: str) -> dict[str, Any] | None:
        row = self.session.scalar(
            select(SemanticCache).where(
                SemanticCache.tenant_pub_id == self.tenant_pub_id,
                SemanticCache.cache_key == key,
            )
        )
        if row is None:
            return None
        row.last_hit_at = datetime.now(UTC)
        row.hit_count += 1
        return dict(row.value)

    @_savepoint
    def cache_put(self, key: str, value: Mapping[str, Any]) -> None:
        statement = insert(SemanticCache).values(
            tenant_pub_id=self.tenant_pub_id,
            namespace=self.namespace,
            domain=self.domain,
            cache_key=key,
            value=dict(value),
        )
        statement = statement.on_conflict_do_nothing(constraint="uq_semantic_cache_key")
        self.session.execute(statement)

    @_savepoint
    def record_observations(self, tenant: str, observations: tuple[ObservationDraft, ...]) -> int:
        if tenant != self.tenant_pub_id:
            raise KnowledgeConflict("observation_tenant_mismatch")
        inserted = 0
        for draft in observations:
            statement = (
                insert(Observation)
                .values(
                    pub_id=new_pub_id("kob"),
                    tenant_pub_id=tenant,
                    namespace=draft.namespace,
                    domain=draft.domain,
                    task=draft.task,
                    surface_form=draft.surface_form,
                    normalized_key=draft.normalized_key,
                    source_type=draft.source_type,
                    source_ref_hash=draft.source_ref_hash,
                    idempotency_key=draft.idempotency_key,
                    safe_context=draft.safe_context,
                    data_classification=draft.data_classification,
                    visibility=draft.visibility,
                    payload=draft.payload,
                )
                .on_conflict_do_nothing(constraint="uq_observation_tenant_domain_idempotency")
                .returning(Observation.id)
            )
            observation_id = self.session.execute(statement).scalar_one_or_none()
            if observation_id is None:
                continue
            inserted += 1
            key = _aggregation_key(draft.normalized_key)
            candidate_insert = (
                insert(Candidate)
                .values(
                    pub_id=new_pub_id("kca"),
                    tenant_pub_id=tenant,
                    namespace=draft.namespace,
                    domain=draft.domain,
                    aggregation_key=key,
                    surface_forms=[],
                    observation_count=0,
                    source_count=0,
                    state="aggregated",
                    priority=0,
                    policy_version=str(draft.payload.get("policy_version") or "unknown"),
                    evidence_version="none",
                )
                .on_conflict_do_nothing(constraint="uq_candidate_tenant_domain_key")
            )
            self.session.execute(candidate_insert)
            candidate = self.session.scalar(
                select(Candidate)
                .where(
                    Candidate.tenant_pub_id == tenant,
                    Candidate.namespace == draft.namespace,
                    Candidate.domain == draft.domain,
                    Candidate.aggregation_key == key,
                )
                .with_for_update()
            )
            if candidate is None:
                raise KnowledgeConflict("candidate_aggregation_failed")
            link = (
                insert(CandidateObservation)
                .values(
                    tenant_pub_id=tenant,
                    candidate_id=candidate.id,
                    observation_id=observation_id,
                )
                .on_conflict_do_nothing(constraint="uq_candidate_observation_pair")
                .returning(CandidateObservation.id)
            )
            link_id = self.session.execute(link).scalar_one_or_none()
            if link_id is None:
                continue
            candidate.surface_forms = sorted(
                {*candidate.surface_forms, draft.surface_form}, key=str.casefold
            )
            candidate.observation_count += 1
            candidate.last_seen_at = datetime.now(UTC)
            source_count = self.session.scalar(
                select(func.count(distinct(Observation.source_ref_hash)))
                .join(
                    CandidateObservation,
                    CandidateObservation.observation_id == Observation.id,
                )
                .where(CandidateObservation.candidate_id == candidate.id)
            )
            candidate.source_count = int(source_count or 0)
            candidate.priority = min(
                candidate.observation_count * 10 + candidate.source_count * 25,
                1_000,
            )
        return inserted

    @_savepoint
    def record_trace(self, tenant: str, trace: Mapping[str, Any]) -> None:
        if tenant != self.tenant_pub_id:
            raise KnowledgeConflict("trace_tenant_mismatch")
        cost = trace.get("cost_usd")
        statement = (
            insert(InferenceTrace)
            .values(
                pub_id=new_pub_id("kit"),
                tenant_pub_id=tenant,
                namespace=str(trace["namespace"]),
                domain=str(trace["domain"]),
                request_id=str(trace["request_id"]),
                task=str(trace["task"]),
                input_hash=str(trace["input_hash"]),
                reasoning_policy=str(trace["reasoning_policy"]),
                policy_id=str(trace["policy_id"]),
                policy_version=str(trace["policy_version"]),
                knowledge_release_id=str(trace["knowledge_release_id"]),
                knowledge_content_hash=str(trace["knowledge_content_hash"]),
                prompt_id=trace.get("prompt_id"),
                prompt_version=trace.get("prompt_version"),
                model_provider=trace.get("model_provider"),
                model_name=trace.get("model"),
                model_version=trace.get("model_version"),
                tool_version=str(trace["tool_version"]),
                adopt_model_inferred=bool(trace["adopt_model_inferred"]),
                adopted_model_decisions=int(trace["adopted_model_decisions"]),
                latency_ms=int(trace["latency_ms"]),
                model_latency_ms=trace.get("model_latency_ms"),
                input_tokens=trace.get("input_tokens"),
                output_tokens=trace.get("output_tokens"),
                cost_microusd=(int(round(float(cost) * 1_000_000)) if cost is not None else None),
                cache_status=str(trace["cache_status"]),
                degradation=list(trace.get("degradation") or []),
                tool_summary=list(trace.get("tool_summary") or []),
                data_classification=str(trace["data_classification"]),
            )
            .on_conflict_do_nothing(constraint="uq_inference_trace_tenant_request")
        )
        self.session.execute(statement)

    def audit(
        self,
        *,
        namespace: str,
        domain: str,
        actor: str,
        action: str,
        resource_type: str,
        resource_pub_id: str,
        receipt: Mapping[str, Any] | None = None,
    ) -> None:
        self.session.add(
            AuditEvent(
                pub_id=new_pub_id("kau"),
                tenant_pub_id=self.tenant_pub_id,
                namespace=namespace,
                domain=domain,
                actor=actor,
                action=action,
                resource_type=resource_type,
                resource_pub_id=resource_pub_id,
                receipt=dict(receipt or {}),
            )
        )

    def list_candidates(
        self,
        *,
        namespace: str | None,
        domain: str | None,
        state: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Candidate], int]:
        predicates = [Candidate.tenant_pub_id == self.tenant_pub_id]
        if namespace is not None:
            predicates.append(Candidate.namespace == namespace)
        if domain is not None:
            predicates.append(Candidate.domain == domain)
        if state is not None:
            predicates.append(Candidate.state == state)
        total = int(
            self.session.scalar(select(func.count()).select_from(Candidate).where(*predicates)) or 0
        )
        rows = self.session.scalars(
            select(Candidate)
            .where(*predicates)
            .order_by(Candidate.priority.desc(), Candidate.last_seen_at, Candidate.pub_id)
            .offset(offset)
            .limit(limit)
        ).all()
        return list(rows), total

    def candidate(self, pub_id: str, *, lock: bool = False) -> Candidate:
        statement = select(Candidate).where(
            Candidate.tenant_pub_id == self.tenant_pub_id,
            Candidate.pub_id == pub_id,
        )
        if lock:
            statement = statement.with_for_update()
        row = self.session.scalar(statement)
        if row is None:
            raise KnowledgeNotFound("candidate_not_found")
        return row

    def create_proposal(self, values: Mapping[str, Any], *, actor: str) -> Proposal:
        candidate_id = None
        candidate_pub_id = values.get("candidate_pub_id")
        if candidate_pub_id:
            candidate = self.candidate(str(candidate_pub_id), lock=True)
            if candidate.namespace != values["namespace"] or candidate.domain != values["domain"]:
                raise KnowledgeConflict("candidate_scope_mismatch")
            if candidate.state in {"rejected", "deferred"}:
                raise KnowledgeConflict("candidate_requires_explicit_reopen")
            candidate_id = candidate.id
            candidate.state = "proposed"
        proposal = Proposal(
            pub_id=new_pub_id("kpr"),
            tenant_pub_id=self.tenant_pub_id,
            namespace=str(values["namespace"]),
            domain=str(values["domain"]),
            candidate_id=candidate_id,
            operation=str(values["operation"]),
            target_stable_id=values.get("target_stable_id"),
            payload=dict(values["payload"]),
            alternatives=list(values.get("alternatives") or []),
            confidence=dict(values.get("confidence") or {}),
            model_provider=values.get("model_provider"),
            model_name=values.get("model_name"),
            model_version=values.get("model_version"),
            prompt_id=values.get("prompt_id"),
            prompt_version=values.get("prompt_version"),
            policy_version=str(values["policy_version"]),
            created_by=actor,
        )
        self.session.add(proposal)
        self.session.flush()
        self.audit(
            namespace=proposal.namespace,
            domain=proposal.domain,
            actor=actor,
            action="proposal.created",
            resource_type="proposal",
            resource_pub_id=proposal.pub_id,
        )
        return proposal

    def proposal(self, pub_id: str, *, lock: bool = False) -> Proposal:
        statement = select(Proposal).where(
            Proposal.tenant_pub_id == self.tenant_pub_id,
            Proposal.pub_id == pub_id,
        )
        if lock:
            statement = statement.with_for_update()
        row = self.session.scalar(statement)
        if row is None:
            raise KnowledgeNotFound("proposal_not_found")
        return row

    def list_proposals(self, *, namespace: str, domain: str, limit: int) -> list[Proposal]:
        return list(
            self.session.scalars(
                select(Proposal)
                .where(
                    Proposal.tenant_pub_id == self.tenant_pub_id,
                    Proposal.namespace == namespace,
                    Proposal.domain == domain,
                )
                .order_by(Proposal.created_at.desc(), Proposal.pub_id.desc())
                .limit(limit)
            ).all()
        )

    def create_evidence(self, values: Mapping[str, Any], *, actor: str) -> Evidence:
        candidate_id = None
        proposal_id = None
        namespace = str(values["namespace"])
        domain = str(values["domain"])
        if values.get("candidate_pub_id"):
            candidate = self.candidate(str(values["candidate_pub_id"]))
            if candidate.namespace != namespace or candidate.domain != domain:
                raise KnowledgeConflict("candidate_scope_mismatch")
            candidate_id = candidate.id
        if values.get("proposal_pub_id"):
            proposal = self.proposal(str(values["proposal_pub_id"]), lock=True)
            if proposal.namespace != namespace or proposal.domain != domain:
                raise KnowledgeConflict("proposal_scope_mismatch")
            if proposal.state in {"approved", "rejected", "deferred"}:
                raise KnowledgeConflict("terminal_proposal_evidence_requires_candidate_reopen")
            proposal_id = proposal.id
            proposal.state = "review_ready"
            if proposal.candidate_id is not None:
                linked_candidate = self.session.get(Candidate, proposal.candidate_id)
                if linked_candidate is not None:
                    linked_candidate.state = "review_ready"
        if candidate_id is None and proposal_id is None:
            raise KnowledgeConflict("evidence_target_required")
        evidence = Evidence(
            pub_id=new_pub_id("kev"),
            tenant_pub_id=self.tenant_pub_id,
            namespace=namespace,
            domain=domain,
            candidate_id=candidate_id,
            proposal_id=proposal_id,
            source_uri=values.get("source_uri"),
            content_hash=str(values["content_hash"]),
            publisher=str(values["publisher"]),
            claim=str(values["claim"]),
            stance=str(values["stance"]),
            summary=str(values["summary"]),
            trust_tier=str(values["trust_tier"]),
            visibility=str(values["visibility"]),
            data_classification=str(values["data_classification"]),
            acquired_at=values["acquired_at"],
            created_by=actor,
        )
        self.session.add(evidence)
        self.session.flush()
        self.audit(
            namespace=namespace,
            domain=domain,
            actor=actor,
            action="evidence.appended",
            resource_type="evidence",
            resource_pub_id=evidence.pub_id,
        )
        return evidence

    def list_evidence(self, *, namespace: str, domain: str, limit: int) -> list[Evidence]:
        return list(
            self.session.scalars(
                select(Evidence)
                .where(
                    Evidence.tenant_pub_id == self.tenant_pub_id,
                    Evidence.namespace == namespace,
                    Evidence.domain == domain,
                )
                .order_by(Evidence.created_at.desc(), Evidence.pub_id.desc())
                .limit(limit)
            ).all()
        )

    def adjudicate(
        self, proposal_pub_id: str, values: Mapping[str, Any], *, actor: str
    ) -> Adjudication:
        proposal = self.proposal(proposal_pub_id, lock=True)
        if proposal.created_by == actor:
            raise KnowledgeConflict("four_eyes_review_required")
        if proposal.state in {"approved", "rejected", "deferred"}:
            raise KnowledgeConflict("proposal_already_adjudicated")
        decision = str(values["decision"])
        if decision == "approved":
            qualifying_support = self.session.scalar(
                select(func.count())
                .select_from(Evidence)
                .where(
                    Evidence.tenant_pub_id == self.tenant_pub_id,
                    Evidence.proposal_id == proposal.id,
                    Evidence.stance == "supports",
                    Evidence.trust_tier.in_(("authoritative", "primary")),
                )
            )
            if not qualifying_support:
                raise KnowledgeConflict("authoritative_supporting_evidence_required")
            qualifying_opposition = self.session.scalar(
                select(func.count())
                .select_from(Evidence)
                .where(
                    Evidence.tenant_pub_id == self.tenant_pub_id,
                    Evidence.proposal_id == proposal.id,
                    Evidence.stance == "opposes",
                    Evidence.trust_tier.in_(("authoritative", "primary")),
                )
            )
            if qualifying_opposition:
                raise KnowledgeConflict("contradictory_authoritative_evidence_requires_resolution")
        adjudication = Adjudication(
            pub_id=new_pub_id("kad"),
            tenant_pub_id=self.tenant_pub_id,
            namespace=proposal.namespace,
            domain=proposal.domain,
            proposal_id=proposal.id,
            decision=decision,
            reason=str(values["reason"]),
            policy_version=str(values["policy_version"]),
            before_value=dict(values.get("before_value") or {}),
            after_value=dict(values.get("after_value") or {}),
            decided_by=actor,
        )
        self.session.add(adjudication)
        proposal.state = decision
        if proposal.candidate_id is not None:
            candidate = self.session.get(Candidate, proposal.candidate_id)
            if candidate is not None:
                candidate.state = decision
        self.session.flush()
        self.audit(
            namespace=proposal.namespace,
            domain=proposal.domain,
            actor=actor,
            action=f"proposal.{decision}",
            resource_type="adjudication",
            resource_pub_id=adjudication.pub_id,
        )
        return adjudication

    def reopen_candidate(
        self,
        pub_id: str,
        *,
        reason: str,
        policy_version: str | None,
        evidence_version: str | None,
        manual_override: bool,
        actor: str,
    ) -> Candidate:
        candidate = self.candidate(pub_id, lock=True)
        if candidate.state not in {
            "rejected",
            "deferred",
            "local_published",
            "exported",
            "externally_published",
            "reconciled",
            "superseded",
        }:
            raise KnowledgeConflict("candidate_not_reopenable")
        policy_changed = bool(policy_version and policy_version != candidate.policy_version)
        evidence_changed = bool(evidence_version and evidence_version != candidate.evidence_version)
        if not policy_changed and not evidence_changed and not manual_override:
            raise KnowledgeConflict("new_evidence_policy_or_manual_override_required")
        candidate.state = "aggregated"
        candidate.reopen_reason = reason
        if policy_version:
            candidate.policy_version = policy_version
        if evidence_version:
            candidate.evidence_version = evidence_version
        self.audit(
            namespace=candidate.namespace,
            domain=candidate.domain,
            actor=actor,
            action="candidate.reopened",
            resource_type="candidate",
            resource_pub_id=candidate.pub_id,
            receipt={
                "reason": reason,
                "trigger": (
                    "policy_version"
                    if policy_changed
                    else "evidence_version"
                    if evidence_changed
                    else "manual_override"
                ),
            },
        )
        return candidate

    def create_change_set(self, values: Mapping[str, Any], *, actor: str) -> ChangeSet:
        namespace = str(values["namespace"])
        domain = str(values["domain"])
        changes = list(values["changes"])
        for change in changes:
            self._validate_change_lineage(
                namespace=namespace,
                domain=domain,
                change=change,
            )
        change_set = ChangeSet(
            pub_id=new_pub_id("kcs"),
            tenant_pub_id=self.tenant_pub_id,
            namespace=namespace,
            domain=domain,
            base_release_id=values.get("base_release_id"),
            changes=changes,
            dependency_ids=list(values.get("dependency_ids") or []),
            conflicts=list(values.get("conflicts") or []),
            visibility=str(values["visibility"]),
            created_by=actor,
        )
        self.session.add(change_set)
        self.session.flush()
        self.audit(
            namespace=namespace,
            domain=domain,
            actor=actor,
            action="change_set.created",
            resource_type="change_set",
            resource_pub_id=change_set.pub_id,
        )
        return change_set

    def _validate_change_lineage(
        self,
        *,
        namespace: str,
        domain: str,
        change: Mapping[str, Any],
    ) -> None:
        """Bind the materialized change to the exact approved proposal and evidence set."""

        proposal_pub_id = str(change.get("proposal_pub_id") or "")
        if not proposal_pub_id:
            raise KnowledgeConflict("change_proposal_required")
        proposal = self.proposal(proposal_pub_id)
        if (
            proposal.namespace != namespace
            or proposal.domain != domain
            or proposal.state != "approved"
        ):
            raise KnowledgeConflict("change_requires_approved_proposal")

        evidence_pub_ids = change.get("evidence_pub_ids")
        if (
            not isinstance(evidence_pub_ids, list)
            or not evidence_pub_ids
            or not all(isinstance(value, str) and value for value in evidence_pub_ids)
            or len(set(evidence_pub_ids)) != len(evidence_pub_ids)
        ):
            raise KnowledgeConflict("change_evidence_lineage_required")
        linked_evidence = list(
            self.session.scalars(
                select(Evidence).where(
                    Evidence.tenant_pub_id == self.tenant_pub_id,
                    Evidence.proposal_id == proposal.id,
                )
            ).all()
        )
        if set(evidence_pub_ids) != {row.pub_id for row in linked_evidence}:
            raise KnowledgeConflict("change_evidence_lineage_mismatch")
        qualifying_support = any(
            row.stance == "supports" and row.trust_tier in {"authoritative", "primary"}
            for row in linked_evidence
        )
        qualifying_opposition = any(
            row.stance == "opposes" and row.trust_tier in {"authoritative", "primary"}
            for row in linked_evidence
        )
        if not qualifying_support:
            raise KnowledgeConflict("authoritative_supporting_evidence_required")
        if qualifying_opposition:
            raise KnowledgeConflict("contradictory_authoritative_evidence_requires_resolution")

        adjudication = self.session.scalar(
            select(Adjudication)
            .where(
                Adjudication.tenant_pub_id == self.tenant_pub_id,
                Adjudication.proposal_id == proposal.id,
                Adjudication.decision == "approved",
            )
            .order_by(Adjudication.decided_at.desc(), Adjudication.pub_id.desc())
        )
        if adjudication is None:
            raise KnowledgeConflict("approved_adjudication_required")

        kind = str(change.get("kind") or "")
        operation = str(change.get("operation") or "")
        proposal_payload = dict(proposal.payload)
        if kind in {"object", "knowledge_object"}:
            expected_operation = "retire" if proposal.operation == "retire" else "upsert"
            if operation != expected_operation:
                raise KnowledgeConflict("change_operation_proposal_mismatch")
            stable_id = str(change.get("stable_id") or "")
            if not stable_id or stable_id != str(proposal.target_stable_id or ""):
                raise KnowledgeConflict("change_target_proposal_mismatch")
            proposed_attributes = proposal_payload.get("attributes", proposal_payload)
            if not isinstance(proposed_attributes, dict):
                raise KnowledgeConflict("proposal_payload_invalid")
            if operation != "retire" and dict(change.get("attributes") or {}) != dict(
                proposed_attributes
            ):
                raise KnowledgeConflict("change_content_proposal_mismatch")
            proposed_type = proposal_payload.get("object_type")
            if proposed_type is None and isinstance(proposed_attributes, dict):
                proposed_type = proposed_attributes.get("entity_type")
            if proposed_type is not None and str(change.get("object_type") or "") != str(
                proposed_type
            ):
                raise KnowledgeConflict("change_object_type_proposal_mismatch")
            after_value = dict(adjudication.after_value)
            if after_value and any(
                proposed_attributes.get(key) != value for key, value in after_value.items()
            ):
                raise KnowledgeConflict("change_adjudication_mismatch")
            return

        if kind in {"relation", "assertion"}:
            if operation not in {"append", "assert"}:
                raise KnowledgeConflict("change_operation_proposal_mismatch")
            proposed_assertion = proposal_payload.get("assertion", proposal_payload)
            if not isinstance(proposed_assertion, dict):
                raise KnowledgeConflict("proposal_payload_invalid")
            semantic_fields = {
                "assertion_key",
                "subject_stable_id",
                "predicate",
                "object_stable_id",
                "object_value",
                "scope",
                "epistemic_status",
                "review_status",
                "confidence_ppm",
                "valid_from",
                "valid_until",
            }
            if not {"subject_stable_id", "predicate"} <= set(proposed_assertion):
                raise KnowledgeConflict("proposal_assertion_incomplete")
            for field in semantic_fields.intersection(proposed_assertion):
                if change.get(field) != proposed_assertion.get(field):
                    raise KnowledgeConflict("change_content_proposal_mismatch")
            return
        raise KnowledgeConflict("unsupported_change_kind")

    def _validate_change_set_lineage(self, change_set: ChangeSet) -> None:
        for change in change_set.changes:
            self._validate_change_lineage(
                namespace=change_set.namespace,
                domain=change_set.domain,
                change=change,
            )

    def change_set(self, pub_id: str, *, lock: bool = False) -> ChangeSet:
        statement = select(ChangeSet).where(
            ChangeSet.tenant_pub_id == self.tenant_pub_id,
            ChangeSet.pub_id == pub_id,
        )
        if lock:
            statement = statement.with_for_update()
        row = self.session.scalar(statement)
        if row is None:
            raise KnowledgeNotFound("change_set_not_found")
        return row

    def approve_change_set(self, pub_id: str, *, actor: str) -> ChangeSet:
        change_set = self.change_set(pub_id, lock=True)
        if change_set.created_by == actor:
            raise KnowledgeConflict("four_eyes_approval_required")
        if change_set.state != "draft":
            raise KnowledgeConflict("change_set_not_draft")
        if change_set.conflicts:
            raise KnowledgeConflict("change_set_has_conflicts")
        self._validate_change_set_lineage(change_set)
        change_set.state = "approved"
        change_set.approved_by = actor
        change_set.approved_at = datetime.now(UTC)
        self.audit(
            namespace=change_set.namespace,
            domain=change_set.domain,
            actor=actor,
            action="change_set.approved",
            resource_type="change_set",
            resource_pub_id=change_set.pub_id,
        )
        return change_set

    def approved_change_sets(self, pub_ids: Sequence[str]) -> list[ChangeSet]:
        rows = self.session.scalars(
            select(ChangeSet)
            .where(
                ChangeSet.tenant_pub_id == self.tenant_pub_id,
                ChangeSet.pub_id.in_(pub_ids),
            )
            .with_for_update()
        ).all()
        by_id = {row.pub_id: row for row in rows}
        if set(by_id) != set(pub_ids):
            raise KnowledgeNotFound("change_set_not_found")
        ordered = [by_id[pub_id] for pub_id in pub_ids]
        if any(row.state != "approved" for row in ordered):
            raise KnowledgeConflict("change_set_not_approved")
        for row in ordered:
            if row.conflicts:
                raise KnowledgeConflict("change_set_has_conflicts")
            self._validate_change_set_lineage(row)
        return ordered

    def mark_change_sets_published(
        self,
        change_sets: Sequence[ChangeSet],
        *,
        release_id: str,
        actor: str,
    ) -> None:
        """Close every proposal/candidate lineage represented by a published release."""

        proposal_ids = {
            str(change.get("proposal_pub_id") or "")
            for row in change_sets
            for change in row.changes
            if str(change.get("proposal_pub_id") or "")
        }
        for row in change_sets:
            row.state = "local_published"
        for proposal_pub_id in sorted(proposal_ids):
            proposal = self.proposal(proposal_pub_id, lock=True)
            if proposal.state != "approved":
                raise KnowledgeConflict("published_proposal_state_invalid")
            proposal.state = "local_published"
            if proposal.candidate_id is None:
                continue
            candidate = self.session.get(Candidate, proposal.candidate_id)
            if candidate is None:
                raise KnowledgeConflict("published_candidate_missing")
            candidate.state = "local_published"
            self.audit(
                namespace=candidate.namespace,
                domain=candidate.domain,
                actor=actor,
                action="candidate.local_published",
                resource_type="candidate",
                resource_pub_id=candidate.pub_id,
                receipt={"release_id": release_id, "proposal_pub_id": proposal.pub_id},
            )

    def active_release_id(self, *, namespace: str, domain: str) -> str | None:
        return self.session.scalar(
            select(ReleaseActivation.release_id)
            .where(
                ReleaseActivation.tenant_pub_id == self.tenant_pub_id,
                ReleaseActivation.namespace == namespace,
                ReleaseActivation.domain == domain,
            )
            .order_by(
                ReleaseActivation.occurred_at.desc(),
                ReleaseActivation.pub_id.desc(),
            )
            .limit(1)
        )

    def scoped_release(
        self,
        *,
        namespace: str,
        domain: str,
        release_id: str | None,
    ) -> KnowledgeRelease | None:
        selected = release_id or self.active_release_id(namespace=namespace, domain=domain)
        if selected is None:
            return None
        row = self.session.scalar(
            select(KnowledgeRelease).where(
                KnowledgeRelease.tenant_pub_id == self.tenant_pub_id,
                KnowledgeRelease.namespace == namespace,
                KnowledgeRelease.domain == domain,
                KnowledgeRelease.release_id == selected,
            )
        )
        if row is None:
            raise KnowledgeConflict("active_release_database_record_missing")
        return row

    def current_objects(
        self,
        *,
        namespace: str,
        domain: str,
        release_id: str | None = None,
    ) -> list[KnowledgeObject]:
        release = self.scoped_release(
            namespace=namespace,
            domain=domain,
            release_id=release_id,
        )
        if release is None:
            return []
        return list(
            self.session.scalars(
                select(KnowledgeObject)
                .join(
                    KnowledgeReleaseObject,
                    KnowledgeReleaseObject.knowledge_object_id == KnowledgeObject.id,
                )
                .where(
                    KnowledgeReleaseObject.tenant_pub_id == self.tenant_pub_id,
                    KnowledgeReleaseObject.knowledge_release_id == release.id,
                )
                .order_by(KnowledgeReleaseObject.stable_id)
            ).all()
        )

    def assertions(
        self,
        *,
        namespace: str,
        domain: str,
        release_id: str | None = None,
    ) -> list[Assertion]:
        release = self.scoped_release(
            namespace=namespace,
            domain=domain,
            release_id=release_id,
        )
        if release is None:
            return []
        return list(
            self.session.scalars(
                select(Assertion)
                .join(
                    KnowledgeReleaseAssertion,
                    KnowledgeReleaseAssertion.assertion_id == Assertion.id,
                )
                .where(
                    KnowledgeReleaseAssertion.tenant_pub_id == self.tenant_pub_id,
                    KnowledgeReleaseAssertion.knowledge_release_id == release.id,
                )
                .order_by(KnowledgeReleaseAssertion.assertion_key)
            ).all()
        )

    def materialize_changes(
        self,
        *,
        namespace: str,
        domain: str,
        changes: Sequence[Mapping[str, Any]],
        release: KnowledgeRelease,
        base_release_id: str | None,
    ) -> None:
        if release.namespace != namespace or release.domain != domain:
            raise KnowledgeConflict("release_scope_mismatch")
        current = {
            row.stable_id: row
            for row in self.current_objects(
                namespace=namespace,
                domain=domain,
                release_id=base_release_id,
            )
        }
        current_assertions = {
            row.assertion_key: row
            for row in self.assertions(
                namespace=namespace,
                domain=domain,
                release_id=base_release_id,
            )
        }
        for change in changes:
            kind = str(change.get("kind") or "")
            operation = str(change.get("operation") or "")
            if kind in {"object", "knowledge_object"}:
                if operation not in {"upsert", "retire"}:
                    raise KnowledgeConflict("unsupported_object_change")
                stable_id = str(change.get("stable_id") or "").strip()
                object_type = str(change.get("object_type") or "").strip()
                if not stable_id or not object_type:
                    raise KnowledgeConflict("object_identity_required")
                previous = current.get(stable_id)
                attributes = dict(change.get("attributes") or {})
                if operation == "retire" and previous is not None:
                    attributes = dict(previous.attributes)
                latest_version = self.session.scalar(
                    select(func.max(KnowledgeObject.version)).where(
                        KnowledgeObject.tenant_pub_id == self.tenant_pub_id,
                        KnowledgeObject.namespace == namespace,
                        KnowledgeObject.domain == domain,
                        KnowledgeObject.stable_id == stable_id,
                    )
                )
                row = KnowledgeObject(
                    pub_id=new_pub_id("kno"),
                    tenant_pub_id=self.tenant_pub_id,
                    namespace=namespace,
                    domain=domain,
                    stable_id=stable_id,
                    object_type=object_type,
                    attributes=attributes,
                    origin=str(change.get("origin") or "governed_change_set"),
                    review_status=(
                        "retired"
                        if operation == "retire"
                        else str(change.get("review_status") or "reviewed")
                    ),
                    visibility=str(change.get("visibility") or "tenant"),
                    sync_status=str(change.get("sync_status") or "local_ahead"),
                    valid_from=change.get("valid_from"),
                    valid_until=change.get("valid_until"),
                    version=int(latest_version or 0) + 1,
                )
                self.session.add(row)
                if operation == "retire":
                    current.pop(stable_id, None)
                else:
                    current[stable_id] = row
                continue
            if kind in {"relation", "assertion"}:
                if operation not in {"append", "assert"}:
                    raise KnowledgeConflict("unsupported_assertion_change")
                subject = str(change.get("subject_stable_id") or "").strip()
                predicate = str(change.get("predicate") or "").strip()
                if not subject or not predicate:
                    raise KnowledgeConflict("assertion_identity_required")
                assertion_key = str(change.get("assertion_key") or "").strip()
                if not assertion_key:
                    identity = "|".join(
                        (
                            subject,
                            predicate,
                            str(change.get("object_stable_id") or ""),
                            json.dumps(
                                change.get("scope") or {},
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        )
                    )
                    assertion_key = "assertion:" + hashlib.sha256(identity.encode()).hexdigest()
                latest_assertion_version = self.session.scalar(
                    select(func.max(Assertion.version)).where(
                        Assertion.tenant_pub_id == self.tenant_pub_id,
                        Assertion.namespace == namespace,
                        Assertion.domain == domain,
                        Assertion.assertion_key == assertion_key,
                    )
                )
                assertion = Assertion(
                    pub_id=new_pub_id("kas"),
                    tenant_pub_id=self.tenant_pub_id,
                    namespace=namespace,
                    domain=domain,
                    assertion_key=assertion_key,
                    subject_stable_id=subject,
                    predicate=predicate,
                    object_stable_id=change.get("object_stable_id"),
                    object_value=dict(change.get("object_value") or {}),
                    scope=dict(change.get("scope") or {}),
                    evidence_refs=list(change.get("evidence_refs") or []),
                    epistemic_status=str(change.get("epistemic_status") or "reviewed_local"),
                    review_status=str(change.get("review_status") or "reviewed"),
                    confidence_ppm=int(change.get("confidence_ppm") or 1_000_000),
                    valid_from=change.get("valid_from"),
                    valid_until=change.get("valid_until"),
                    version=int(latest_assertion_version or 0) + 1,
                )
                self.session.add(assertion)
                current_assertions[assertion_key] = assertion
                continue
            raise KnowledgeConflict("unsupported_change_kind")
        self.session.flush()
        for stable_id, object_row in sorted(current.items()):
            self.session.add(
                KnowledgeReleaseObject(
                    tenant_pub_id=self.tenant_pub_id,
                    namespace=namespace,
                    domain=domain,
                    knowledge_release_id=release.id,
                    knowledge_object_id=object_row.id,
                    stable_id=stable_id,
                )
            )
        for assertion_key, assertion_row in sorted(current_assertions.items()):
            self.session.add(
                KnowledgeReleaseAssertion(
                    tenant_pub_id=self.tenant_pub_id,
                    namespace=namespace,
                    domain=domain,
                    knowledge_release_id=release.id,
                    assertion_id=assertion_row.id,
                    assertion_key=assertion_key,
                )
            )

    def add_release(
        self,
        *,
        namespace: str,
        domain: str,
        release_id: str,
        parent_release_id: str | None,
        schema_version: str,
        content_hash: str,
        artifact_uri: str,
        quality_report: Mapping[str, Any],
        actor: str,
    ) -> KnowledgeRelease:
        release = KnowledgeRelease(
            pub_id=new_pub_id("krl"),
            tenant_pub_id=self.tenant_pub_id,
            namespace=namespace,
            domain=domain,
            release_id=release_id,
            parent_release_id=parent_release_id,
            schema_version=schema_version,
            content_hash=content_hash,
            artifact_uri=artifact_uri,
            quality_report=dict(quality_report),
            created_by=actor,
        )
        self.session.add(release)
        self.session.flush()
        self.audit(
            namespace=namespace,
            domain=domain,
            actor=actor,
            action="release.published",
            resource_type="knowledge_release",
            resource_pub_id=release.pub_id,
            receipt={"release_id": release_id, "content_hash": content_hash},
        )
        return release

    def activate_release(
        self,
        *,
        namespace: str,
        domain: str,
        release_id: str,
        previous_release_id: str | None,
        action: str,
        actor: str,
    ) -> ReleaseActivation:
        activation = ReleaseActivation(
            pub_id=new_pub_id("kac"),
            tenant_pub_id=self.tenant_pub_id,
            namespace=namespace,
            domain=domain,
            release_id=release_id,
            previous_release_id=previous_release_id,
            action=action,
            actor=actor,
        )
        self.session.add(activation)
        self.session.flush()
        self.audit(
            namespace=namespace,
            domain=domain,
            actor=actor,
            action=f"release.{action}",
            resource_type="release_activation",
            resource_pub_id=activation.pub_id,
            receipt={"release_id": release_id, "previous_release_id": previous_release_id},
        )
        return activation

    def list_releases(self, *, namespace: str, domain: str) -> list[KnowledgeRelease]:
        return list(
            self.session.scalars(
                select(KnowledgeRelease)
                .where(
                    KnowledgeRelease.tenant_pub_id == self.tenant_pub_id,
                    KnowledgeRelease.namespace == namespace,
                    KnowledgeRelease.domain == domain,
                )
                .order_by(KnowledgeRelease.created_at.desc(), KnowledgeRelease.pub_id.desc())
            ).all()
        )

    def release(self, release_id: str) -> KnowledgeRelease:
        row = self.session.scalar(
            select(KnowledgeRelease).where(
                KnowledgeRelease.tenant_pub_id == self.tenant_pub_id,
                KnowledgeRelease.release_id == release_id,
            )
        )
        if row is None:
            raise KnowledgeNotFound("release_not_found")
        return row

    def create_connector_run(self, values: Mapping[str, Any]) -> ConnectorRun:
        row = ConnectorRun(
            pub_id=new_pub_id("kcr"),
            tenant_pub_id=self.tenant_pub_id,
            namespace=str(values["namespace"]),
            domain=str(values["domain"]),
            adapter=str(values["adapter"]),
            operation=str(values["operation"]),
            status=str(values.get("status") or "queued"),
            base_release_id=values.get("base_release_id"),
            upstream_release_id=values.get("upstream_release_id"),
            local_release_id=values.get("local_release_id"),
            cursor=dict(values.get("cursor") or {}),
            result=dict(values.get("result") or {}),
            error_code=values.get("error_code"),
            finished_at=values.get("finished_at"),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_connector_runs(self, *, namespace: str, domain: str, limit: int) -> list[ConnectorRun]:
        return list(
            self.session.scalars(
                select(ConnectorRun)
                .where(
                    ConnectorRun.tenant_pub_id == self.tenant_pub_id,
                    ConnectorRun.namespace == namespace,
                    ConnectorRun.domain == domain,
                )
                .order_by(ConnectorRun.started_at.desc(), ConnectorRun.pub_id.desc())
                .limit(limit)
            ).all()
        )

    def list_audit_events(
        self,
        *,
        namespace: str,
        domain: str,
        limit: int,
        after: datetime | None = None,
        ascending: bool = False,
    ) -> list[AuditEvent]:
        filters = [
            AuditEvent.tenant_pub_id == self.tenant_pub_id,
            AuditEvent.namespace == namespace,
            AuditEvent.domain == domain,
        ]
        if after is not None:
            filters.append(AuditEvent.occurred_at > after)
        order = (
            (AuditEvent.occurred_at.asc(), AuditEvent.pub_id.asc())
            if ascending
            else (AuditEvent.occurred_at.desc(), AuditEvent.pub_id.desc())
        )
        return list(
            self.session.scalars(
                select(AuditEvent).where(*filters).order_by(*order).limit(limit)
            ).all()
        )

    def metrics(self) -> dict[str, Any]:
        tenant = self.tenant_pub_id
        now = datetime.now(UTC)
        observations = self.session.scalar(
            select(func.count()).select_from(Observation).where(Observation.tenant_pub_id == tenant)
        )
        backlog = self.session.scalar(
            select(func.count())
            .select_from(Candidate)
            .where(
                Candidate.tenant_pub_id == tenant,
                Candidate.state.in_(("aggregated", "proposed", "evidence_pending")),
            )
        )
        review_ready = self.session.scalar(
            select(func.count())
            .select_from(Candidate)
            .where(Candidate.tenant_pub_id == tenant, Candidate.state == "review_ready")
        )
        conflicts = self.session.scalar(
            select(func.count())
            .select_from(ChangeSet)
            .where(
                ChangeSet.tenant_pub_id == tenant,
                ChangeSet.state.in_(("draft", "conflict")),
                func.jsonb_array_length(ChangeSet.conflicts) > 0,
            )
        )
        traces = self.session.scalar(
            select(func.count())
            .select_from(InferenceTrace)
            .where(InferenceTrace.tenant_pub_id == tenant)
        )
        model_errors = self.session.scalar(
            select(func.count())
            .select_from(InferenceTrace)
            .where(
                InferenceTrace.tenant_pub_id == tenant,
                InferenceTrace.degradation.cast(Text).like('%"model\\_%', escape="\\"),
            )
        )
        cache_hits = self.session.scalar(
            select(func.coalesce(func.sum(SemanticCache.hit_count), 0)).where(
                SemanticCache.tenant_pub_id == tenant
            )
        )
        last_success = self.session.scalar(
            select(func.max(ConnectorRun.finished_at)).where(
                ConnectorRun.tenant_pub_id == tenant,
                ConnectorRun.status == "success",
            )
        )
        last_attempt = self.session.scalar(
            select(func.max(ConnectorRun.started_at)).where(ConnectorRun.tenant_pub_id == tenant)
        )
        last_export = self.session.scalar(
            select(func.max(ConnectorRun.finished_at)).where(
                ConnectorRun.tenant_pub_id == tenant,
                ConnectorRun.operation.in_(("export", "publish", "reconcile")),
                ConnectorRun.status == "success",
            )
        )
        latest_release = self.session.scalar(
            select(KnowledgeRelease)
            .where(KnowledgeRelease.tenant_pub_id == tenant)
            .order_by(KnowledgeRelease.created_at.desc(), KnowledgeRelease.pub_id.desc())
            .limit(1)
        )
        latest_activation = self.session.scalar(
            select(ReleaseActivation)
            .where(ReleaseActivation.tenant_pub_id == tenant)
            .order_by(ReleaseActivation.occurred_at.desc(), ReleaseActivation.pub_id.desc())
            .limit(1)
        )
        oldest_backlog = self.session.scalar(
            select(func.min(Candidate.first_seen_at)).where(
                Candidate.tenant_pub_id == tenant,
                Candidate.state.in_(("aggregated", "proposed", "evidence_pending", "review_ready")),
            )
        )
        model_calls = self.session.scalar(
            select(func.count())
            .select_from(InferenceTrace)
            .where(
                InferenceTrace.tenant_pub_id == tenant,
                InferenceTrace.model_provider.is_not(None),
            )
        )
        model_latency = self.session.scalar(
            select(func.avg(InferenceTrace.model_latency_ms)).where(
                InferenceTrace.tenant_pub_id == tenant,
                InferenceTrace.model_latency_ms.is_not(None),
            )
        )
        model_cost_microusd = self.session.scalar(
            select(func.coalesce(func.sum(InferenceTrace.cost_microusd), 0)).where(
                InferenceTrace.tenant_pub_id == tenant
            )
        )
        release_age = (
            max(0, int((now - latest_release.created_at).total_seconds()))
            if latest_release is not None
            else None
        )
        oldest_candidate_age = (
            max(0, int((now - oldest_backlog).total_seconds()))
            if oldest_backlog is not None
            else None
        )
        export_lag = None
        if latest_release is not None:
            exported_at = last_export
            export_lag = (
                max(0, int((now - latest_release.created_at).total_seconds()))
                if exported_at is None or exported_at < latest_release.created_at
                else 0
            )
        return {
            "observations": int(observations or 0),
            "candidate_backlog": int(backlog or 0),
            "review_ready": int(review_ready or 0),
            "conflicts": int(conflicts or 0),
            "inference_count": int(traces or 0),
            "model_error_count": int(model_errors or 0),
            "model_call_count": int(model_calls or 0),
            "model_latency_avg_ms": float(model_latency) if model_latency is not None else None,
            "model_cost_usd": float(model_cost_microusd or 0) / 1_000_000,
            "cache_hits": int(cache_hits or 0),
            "active_release_id": latest_activation.release_id if latest_activation else None,
            "release_age_seconds": release_age,
            "oldest_candidate_age_seconds": oldest_candidate_age,
            "connector_last_attempt_at": last_attempt,
            "connector_last_success_at": last_success,
            "export_lag_seconds": export_lag,
        }


__all__ = ["KnowledgeConflict", "KnowledgeNotFound", "KnowledgeRepository"]
