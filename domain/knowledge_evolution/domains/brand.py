"""Brand/entity-resolution domain pack; all brand semantics stay outside the core."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from typing import Any
from urllib.parse import urlsplit

from domain.brandrank.entities import (
    EntityMaster,
    EntityRecord,
    classify_entity,
    load_entity_master,
)
from domain.brandrank.rules import load_domain

from ..contracts import (
    Decision,
    DecisionScope,
    KnowledgeStatus,
    ModelPrompt,
    ObservationDraft,
    ReasoningPolicy,
    ReleaseRef,
    RuntimeRequest,
)


def _key(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _item(item: Mapping[str, Any], index: int) -> tuple[str, str]:
    input_id = str(item.get("id") or f"item-{index + 1}").strip()
    value = str(item.get("value") or item.get("text") or "").strip()
    if not input_id or not value:
        raise ValueError("brand_item_id_and_value_required")
    return input_id, value


def _analysis_domain(request: RuntimeRequest) -> str:
    value = str(request.context.get("analysis_domain") or "").strip()
    if not value:
        raise ValueError("brand_analysis_domain_required")
    return value


def apply_adopted_model_decisions(
    master: EntityMaster, decisions: Iterable[Decision]
) -> EntityMaster:
    """Build a request-scoped overlay; never mutate or publish the base master."""

    entities = list(master.entities)
    records_by_id = {record.entity_id: record for record in master.entities}
    alias_index = dict(master.alias_index)
    relationship_index = dict(master.relationship_index)
    for decision in decisions:
        if decision.knowledge_status != KnowledgeStatus.MODEL_INFERRED or not decision.adopted:
            continue
        identity = decision.value.get("identity")
        relation = decision.value.get("relation")
        roll_up = decision.value.get("roll_up")
        comparison = decision.value.get("comparison")
        if not isinstance(identity, dict) or not isinstance(relation, dict):
            continue
        if not isinstance(roll_up, dict) or not isinstance(comparison, dict):
            continue
        identity_decision = str(identity.get("decision") or "")
        if identity_decision == "ambiguous":
            continue
        entity_id = str(identity.get("entity_id") or "")
        base = records_by_id.get(entity_id)
        canonical = str(
            roll_up.get("display_name")
            or identity.get("canonical_name")
            or (base.canonical_name if base else decision.input_value)
        ).strip()
        if not canonical:
            continue
        scopes = tuple(
            str(value).strip() for value in comparison.get("scopes", []) if str(value).strip()
        )
        eligible = comparison.get("eligible") is True
        if base is None:
            request_entity_id = (
                "request-inferred:"
                + hashlib.sha256(
                    f"{master.source_release_id}|{decision.input_id}|{canonical}".encode()
                ).hexdigest()[:24]
            )
            entity_type = str(identity.get("entity_type") or "unknown")
            if identity_decision == "non_entity":
                entity_type = "institution"
                eligible = False
            record = EntityRecord(
                entity_id=request_entity_id,
                canonical_name=canonical,
                aliases=tuple(dict.fromkeys((canonical, decision.input_value))),
                entity_type=entity_type,
                competitor_eligible=eligible,
                eligibility_mode=(
                    "scope_required" if scopes else ("always" if eligible else "never")
                ),
                brand_level="request_inferred",
                competitor_scopes=scopes,
                eligibility_note="Request-scoped model inference; not published knowledge.",
                evidence_urls=decision.evidence_refs,
                review_status="model_inferred",
                knowledge_status="model_inferred",
            )
            entities.append(record)
        else:
            record = EntityRecord(
                entity_id=base.entity_id,
                canonical_name=canonical,
                aliases=tuple(dict.fromkeys((*base.aliases, decision.input_value))),
                entity_type=base.entity_type,
                competitor_eligible=eligible,
                eligibility_mode=(
                    "scope_required" if scopes else ("always" if eligible else "never")
                ),
                brand_level=base.brand_level,
                parent_brand=base.parent_brand,
                industry_fit=base.industry_fit,
                competitor_scopes=scopes,
                eligibility_note="Request-scoped model inference over a governed identity.",
                evidence_urls=tuple(dict.fromkeys((*base.evidence_urls, *decision.evidence_refs))),
                review_status="model_inferred",
                knowledge_status="model_inferred",
            )
            entities.append(record)
        alias_index[_key(decision.input_value)] = record
        relationship_index[_key(decision.input_value)] = str(relation.get("type") or "uncertain")
    return EntityMaster(
        domain=master.domain,
        schema_version=master.schema_version,
        revision=master.revision,
        aggregation_level=master.aggregation_level,
        entities=tuple(entities),
        alias_index=alias_index,
        relationship_index=relationship_index,
        resolution_policy=master.resolution_policy,
        source_system=master.source_system,
        source_release_id=master.source_release_id,
        source_content_hash=master.source_content_hash,
        source_mode=master.source_mode,
        source_error=master.source_error,
    )


class BrandEntityResolutionPack:
    domain_id = "brand/entity-resolution"
    policy_version = "brand-governance-v1"
    prompt_id = "brand-entity-resolution"
    prompt_version = "brand-entity-resolution-v3"
    tool_version = "brand-tools-v1"
    _OBJECT_TYPES = {
        "legal_entity",
        "company",
        "group",
        "brand",
        "brand_family",
        "sub_brand",
        "business_unit",
        "product",
        "tool",
        "institution",
    }
    _PREDICATES = {
        "same_legal_entity",
        "official_abbreviation",
        "english_name",
        "historical_name",
        "trade_name",
        "product_of",
        "business_unit_of",
        "subsidiary_of",
        "brand_family_member",
        "rolls_up_to",
        "comparison_eligible",
    }

    def __init__(
        self,
        *,
        snapshot_dir: str | None = None,
        knowledge_release_dir: str | None = None,
    ) -> None:
        self.snapshot_dir = snapshot_dir
        self.knowledge_release_dir = knowledge_release_dir

    def _master(self, request: RuntimeRequest) -> EntityMaster:
        return load_entity_master(
            _analysis_domain(request),
            self.snapshot_dir,
            self.knowledge_release_dir,
        )

    def release_ref(self, request: RuntimeRequest) -> ReleaseRef:
        master = self._master(request)
        return ReleaseRef(
            release_id=master.source_release_id or master.revision or "unversioned",
            content_hash=master.source_content_hash or "sha256:unknown",
            schema_version=master.schema_version,
            source=master.source_mode or master.source_system or "unavailable",
            degraded=bool(master.source_error),
        )

    def deterministic_resolve(self, request: RuntimeRequest) -> tuple[Decision, ...]:
        analysis_domain = _analysis_domain(request)
        rules = load_domain(analysis_domain)
        master = self._master(request)
        scopes = tuple(
            str(value).strip()
            for value in request.context.get("comparison_scopes", [])
            if str(value).strip()
        )
        competitors = tuple(
            str(value).strip()
            for value in request.context.get("named_competitors", [])
            if str(value).strip()
        )
        target = str(request.context.get("target_brand") or "").strip() or None
        decisions: list[Decision] = []
        for index, raw_item in enumerate(request.items):
            input_id, value = _item(raw_item, index)
            row = classify_entity(
                value,
                rules=rules,
                master=master,
                target_brand=target,
                named_competitors=competitors,
                comparison_scopes=scopes,
            )
            reviewed = (
                row.get("review_status") == "reviewed" and row.get("entity_type") != "unknown"
            )
            if reviewed:
                status = (
                    KnowledgeStatus.REVIEWED_LOCAL
                    if master.source_mode == "local_knowledge_release"
                    else KnowledgeStatus.PUBLISHED
                )
            else:
                status = KnowledgeStatus.UNRESOLVED
            reason = str(row.get("eligibility_note") or row.get("classification_source") or "")
            decisions.append(
                Decision(
                    input_id=input_id,
                    input_value=value,
                    value={
                        "identity": {
                            "entity_id": row.get("entity_id"),
                            "canonical_name": row.get("canonical_name"),
                            "entity_type": row.get("entity_type"),
                            "review_status": row.get("review_status"),
                        },
                        "relation": {
                            "type": row.get("relationship_to_canonical"),
                        },
                        "roll_up": {
                            "entity_id": row.get("entity_id") if reviewed else None,
                            "display_name": row.get("canonical_name") if reviewed else value,
                            "brand_level": row.get("brand_level"),
                            "parent_brand": row.get("parent_brand"),
                        },
                        "comparison": {
                            "eligible": bool(row.get("competitor_eligible")),
                            "eligibility_mode": row.get("eligibility_mode"),
                            "industry_fit": row.get("industry_fit"),
                            "scopes": row.get("competitor_scopes") or [],
                        },
                        "classification_source": row.get("classification_source"),
                    },
                    knowledge_status=status,
                    decision_scope=(
                        DecisionScope.GLOBAL_RELEASE
                        if status in {KnowledgeStatus.PUBLISHED, KnowledgeStatus.REVIEWED_LOCAL}
                        else (
                            DecisionScope.DOMAIN_CANDIDATE
                            if status == KnowledgeStatus.UNRESOLVED
                            else DecisionScope.PROJECT_STAGING
                        )
                    ),
                    confidence=0.99 if reviewed else 0.0,
                    reasons=(reason,) if reason else (),
                    uncertainty=() if reviewed else ("identity_or_scope_requires_governance",),
                    adopted=reviewed,
                )
            )
        return tuple(decisions)

    def build_model_prompt(
        self, request: RuntimeRequest, deterministic: tuple[Decision, ...]
    ) -> ModelPrompt:
        master = self._master(request)
        prompt_input_ids = (
            {
                decision.input_id
                for decision in deterministic
                if decision.knowledge_status == KnowledgeStatus.UNRESOLVED
            }
            if request.policy == ReasoningPolicy.LLM_ASSISTED
            else {decision.input_id for decision in deterministic}
        )
        catalog = [
            {
                "entity_id": entity.entity_id,
                "canonical_name": entity.canonical_name,
                "aliases": list(entity.aliases),
                "entity_type": entity.entity_type,
                "brand_level": entity.brand_level,
                "parent_brand": entity.parent_brand,
                "review_status": entity.review_status,
                "comparison": {
                    "eligible": entity.competitor_eligible,
                    "mode": entity.eligibility_mode,
                    "scopes": list(entity.competitor_scopes),
                },
            }
            for entity in master.entities
        ]
        safe_items = []
        for index, item in enumerate(request.items):
            input_id, value = _item(item, index)
            if input_id not in prompt_input_ids:
                continue
            contexts = item.get("contexts") or []
            if not isinstance(contexts, list):
                raise ValueError("brand_item_contexts_must_be_list")
            safe_items.append(
                {
                    "input_id": input_id,
                    "value": value,
                    "contexts": [str(context)[:800] for context in contexts[:4]],
                }
            )
        deterministic_payload = [
            {
                "input_id": decision.input_id,
                "status": decision.knowledge_status.value,
                "value": decision.value,
            }
            for decision in deterministic
            if decision.input_id in prompt_input_ids
        ]
        output_schema: dict[str, Any] = {
            "type": "object",
            "additionalProperties": False,
            "required": ["decisions"],
            "properties": {
                "decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "input_id",
                            "identity",
                            "relation",
                            "roll_up",
                            "comparison",
                            "confidence",
                            "reasons",
                            "alternative_hypotheses",
                            "uncertainty",
                            "evidence_refs",
                        ],
                        "properties": {
                            "input_id": {"type": "string"},
                            "identity": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "decision",
                                    "entity_id",
                                    "canonical_name",
                                    "entity_type",
                                ],
                                "properties": {
                                    "decision": {
                                        "type": "string",
                                        "enum": [
                                            "existing",
                                            "propose_new",
                                            "ambiguous",
                                            "non_entity",
                                        ],
                                    },
                                    "entity_id": {"type": ["string", "null"]},
                                    "canonical_name": {"type": ["string", "null"]},
                                    "entity_type": {
                                        "type": "string",
                                        "enum": [
                                            "legal_entity",
                                            "company",
                                            "group",
                                            "brand",
                                            "brand_family",
                                            "sub_brand",
                                            "business_unit",
                                            "product",
                                            "tool",
                                            "institution",
                                            "unknown",
                                        ],
                                    },
                                },
                            },
                            "relation": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["type"],
                                "properties": {
                                    "type": {
                                        "type": "string",
                                        "enum": [
                                            "self",
                                            "same_legal_entity",
                                            "official_abbreviation",
                                            "english_name",
                                            "historical_name",
                                            "trade_name",
                                            "product_of",
                                            "business_unit_of",
                                            "subsidiary_of",
                                            "brand_family_member",
                                            "independent",
                                            "non_vendor",
                                            "uncertain",
                                        ],
                                    }
                                },
                            },
                            "roll_up": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["entity_id", "display_name"],
                                "properties": {
                                    "entity_id": {"type": ["string", "null"]},
                                    "display_name": {"type": ["string", "null"]},
                                },
                            },
                            "comparison": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["eligible", "scopes"],
                                "properties": {
                                    "eligible": {"type": ["boolean", "null"]},
                                    "scopes": {"type": "array", "items": {"type": "string"}},
                                },
                            },
                            "confidence": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["identity", "relation", "roll_up", "eligibility"],
                                "properties": {
                                    "identity": {"type": "number", "minimum": 0, "maximum": 1},
                                    "relation": {"type": "number", "minimum": 0, "maximum": 1},
                                    "roll_up": {"type": "number", "minimum": 0, "maximum": 1},
                                    "eligibility": {"type": "number", "minimum": 0, "maximum": 1},
                                },
                            },
                            "reasons": {"type": "array", "items": {"type": "string"}},
                            "alternative_hypotheses": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "uncertainty": {"type": "array", "items": {"type": "string"}},
                            "evidence_refs": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                }
            },
        }
        user_message = json.dumps(
            {
                "task": request.task,
                "analysis_domain": _analysis_domain(request),
                "comparison_scopes": request.context.get("comparison_scopes", []),
                "policy": request.policy.value,
                "knowledge_release": asdict(self.release_ref(request)),
                "rules": [
                    "Identity, name relation, display roll-up and comparison "
                    "eligibility are separate decisions.",
                    "Do not merge from spelling similarity alone.",
                    "Use an existing entity_id only when it appears in the catalog.",
                    "For a new object, use propose_new and entity_id=null; "
                    "never invent a stable ID.",
                    "Evidence refs must come from allowed_evidence_refs in the request context.",
                    "Abstain with ambiguous/uncertain when evidence is insufficient.",
                ],
                "allowed_evidence_refs": request.context.get("allowed_evidence_refs", []),
                "catalog": catalog,
                "deterministic_results": deterministic_payload,
                "items": safe_items,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return ModelPrompt(
            prompt_id=self.prompt_id,
            prompt_version=self.prompt_version,
            system_message=(
                "You are an evidence-constrained brand knowledge reasoner. "
                "Return strict JSON only. "
                "Your result may affect this request, but it is never a published fact."
            ),
            user_message=user_message,
            output_schema=output_schema,
        )

    def validate_model_output(
        self,
        payload: Mapping[str, Any],
        *,
        request: RuntimeRequest,
        deterministic: tuple[Decision, ...],
    ) -> tuple[Decision, ...]:
        del deterministic
        raw_rows = payload.get("decisions")
        if not isinstance(raw_rows, list):
            raise ValueError("brand_model_decisions_required")
        requested = {
            input_id: value
            for index, item in enumerate(request.items)
            for input_id, value in [_item(item, index)]
        }
        known = {entity.entity_id for entity in self._master(request).entities}
        allowed_refs = {
            str(value) for value in request.context.get("allowed_evidence_refs", []) if str(value)
        }
        seen: set[str] = set()
        output: list[Decision] = []
        for raw in raw_rows:
            if not isinstance(raw, dict):
                raise ValueError("brand_model_decision_invalid")
            input_id = str(raw.get("input_id") or "")
            if input_id not in requested or input_id in seen:
                raise ValueError("brand_model_input_set_invalid")
            seen.add(input_id)
            identity = raw.get("identity")
            relation = raw.get("relation")
            roll_up = raw.get("roll_up")
            comparison = raw.get("comparison")
            confidence = raw.get("confidence")
            if not isinstance(identity, dict) or not isinstance(relation, dict):
                raise ValueError("brand_model_dimensions_required")
            if not isinstance(roll_up, dict) or not isinstance(comparison, dict):
                raise ValueError("brand_model_dimensions_required")
            if not isinstance(confidence, dict):
                raise ValueError("brand_model_dimensions_required")
            identity_decision = str(identity.get("decision") or "")
            entity_id = identity.get("entity_id")
            if identity_decision == "existing" and entity_id not in known:
                raise ValueError("brand_model_unknown_entity_id")
            if identity_decision != "existing" and entity_id is not None:
                raise ValueError("brand_model_new_entity_id_forbidden")
            refs = raw.get("evidence_refs")
            if not isinstance(refs, list) or any(str(value) not in allowed_refs for value in refs):
                raise ValueError("brand_model_evidence_ref_invalid")
            reasons = raw.get("reasons")
            alternatives = raw.get("alternative_hypotheses")
            uncertainty = raw.get("uncertainty")
            if not isinstance(reasons, list) or not isinstance(alternatives, list):
                raise ValueError("brand_model_explanation_invalid")
            if not isinstance(uncertainty, list):
                raise ValueError("brand_model_explanation_invalid")
            scores = []
            for key in ("identity", "relation", "roll_up", "eligibility"):
                raw_score = confidence.get(key)
                if not isinstance(raw_score, int | float):
                    raise ValueError("brand_model_confidence_invalid")
                score = float(raw_score)
                if not 0 <= score <= 1:
                    raise ValueError("brand_model_confidence_invalid")
                scores.append(score)
            output.append(
                Decision(
                    input_id=input_id,
                    input_value=requested[input_id],
                    value={
                        "identity": identity,
                        "relation": relation,
                        "roll_up": roll_up,
                        "comparison": comparison,
                        "requires_governance": True,
                    },
                    knowledge_status=KnowledgeStatus.MODEL_INFERRED,
                    decision_scope=DecisionScope.REQUEST,
                    confidence=min(scores),
                    reasons=tuple(str(value) for value in reasons if str(value).strip()),
                    alternative_hypotheses=tuple(
                        str(value) for value in alternatives if str(value).strip()
                    ),
                    uncertainty=tuple(str(value) for value in uncertainty if str(value).strip()),
                    evidence_refs=tuple(str(value) for value in refs),
                )
            )
        if request.policy == ReasoningPolicy.LLM_REQUIRED and seen != set(requested):
            raise ValueError("brand_model_all_items_required")
        if not output:
            raise ValueError("brand_model_empty_decisions")
        return tuple(output)

    def observations(
        self, request: RuntimeRequest, decisions: tuple[Decision, ...]
    ) -> tuple[ObservationDraft, ...]:
        item_by_id = {
            input_id: item
            for index, item in enumerate(request.items)
            for input_id, _value in [_item(item, index)]
        }
        selected: dict[str, Decision] = {}
        for decision in decisions:
            if decision.knowledge_status in {
                KnowledgeStatus.UNRESOLVED,
                KnowledgeStatus.MODEL_INFERRED,
            }:
                selected[decision.input_id] = decision
        observations: list[ObservationDraft] = []
        for input_id, decision in sorted(selected.items()):
            item = item_by_id[input_id]
            source_ref = str(
                item.get("source_ref") or request.context.get("source_ref") or request.request_id
            )
            source_hash = "sha256:" + hashlib.sha256(source_ref.encode()).hexdigest()
            explicit_key = str(item.get("idempotency_key") or "").strip()
            idempotency = (
                explicit_key
                or hashlib.sha256(
                    f"{request.tenant}|{request.namespace}|{request.domain}|{input_id}|{source_hash}".encode()
                ).hexdigest()
            )
            safe_context = str(
                item.get("safe_context") or request.context.get("safe_context") or ""
            ).strip()
            observations.append(
                ObservationDraft(
                    namespace=request.namespace,
                    domain=request.domain,
                    task=request.task,
                    surface_form=decision.input_value,
                    normalized_key=_key(decision.input_value),
                    source_type=str(item.get("source_type") or "runtime_inference"),
                    source_ref_hash=source_hash,
                    idempotency_key=idempotency,
                    safe_context=safe_context[:500] or None,
                    data_classification=request.data_classification,
                    visibility="private" if request.data_classification != "public" else "public",
                    payload={
                        "knowledge_status": decision.knowledge_status.value,
                        "confidence": decision.confidence,
                        "model_provider": decision.model_provider,
                        "model": decision.model_name,
                        "prompt_version": decision.prompt_version,
                    },
                )
            )
        return tuple(observations)

    def validate_release(
        self,
        objects: Iterable[Mapping[str, Any]],
        assertions: Iterable[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        object_rows = list(objects)
        assertion_rows = list(assertions)
        issues: list[str] = []
        ids: set[str] = set()
        for row in object_rows:
            stable_id = str(row.get("stable_id") or "").strip()
            if not stable_id or stable_id in ids:
                issues.append("duplicate_or_missing_stable_id")
            ids.add(stable_id)
            if str(row.get("object_type") or "") not in self._OBJECT_TYPES:
                issues.append(f"invalid_object_type:{stable_id}")
            attributes = row.get("attributes")
            if not isinstance(attributes, dict):
                issues.append(f"invalid_attributes:{stable_id}")
                continue
            if row.get("visibility") == "public" and row.get("review_status") == "reviewed":
                evidence = attributes.get("evidence_urls")
                if not isinstance(evidence, list) or not evidence:
                    issues.append(f"public_evidence_required:{stable_id}")
                elif any(
                    not isinstance(source, str)
                    or (parsed := urlsplit(source)).scheme != "https"
                    or not parsed.hostname
                    or parsed.username is not None
                    or parsed.password is not None
                    for source in evidence
                ):
                    issues.append(f"public_evidence_uri_invalid:{stable_id}")
        for row in assertion_rows:
            subject = str(row.get("subject_stable_id") or "")
            predicate = str(row.get("predicate") or "")
            if subject not in ids:
                issues.append(f"assertion_subject_missing:{subject}")
            if predicate not in self._PREDICATES:
                issues.append(f"invalid_predicate:{predicate}")
            confidence = int(row.get("confidence_ppm") or 0)
            if not 0 <= confidence <= 1_000_000:
                issues.append(f"invalid_confidence:{subject}:{predicate}")
        return {
            "passed": not issues,
            "issues": sorted(set(issues)),
            "object_count": len(object_rows),
            "assertion_count": len(assertion_rows),
        }

    def project_release(
        self,
        objects: Iterable[Mapping[str, Any]],
        assertions: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        del assertions
        by_analysis_domain: dict[str, list[dict[str, Any]]] = {}
        for item in objects:
            attributes = item.get("attributes")
            if not isinstance(attributes, dict):
                continue
            if item.get("review_status") not in {"reviewed", "published"}:
                continue
            analysis_domain = str(attributes.get("analysis_domain") or "").strip()
            if not analysis_domain:
                raise ValueError("brand_object_analysis_domain_required")
            entity = {key: value for key, value in attributes.items() if key != "analysis_domain"}
            by_analysis_domain.setdefault(analysis_domain, []).append(entity)
        projected_domains: dict[str, Any] = {}
        for analysis_domain, entities in sorted(by_analysis_domain.items()):
            entities.sort(key=lambda value: str(value.get("entity_id") or ""))
            projected_domains[analysis_domain] = {
                "schema_version": "entity-master-v3",
                "domain": analysis_domain,
                "aggregation_level": "brand_family",
                "revision": "knowledge-evolution-release",
                "entities": entities,
            }
        return {
            "schema_version": "brand-knowledge-v1",
            "domain": "brand/entity-resolution",
            "analysis_domains": projected_domains,
        }
