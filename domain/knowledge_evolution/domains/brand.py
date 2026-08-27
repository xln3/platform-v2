"""Brand/entity-resolution domain pack; all brand semantics stay outside the core."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from math import isfinite
from typing import Any
from urllib.parse import urlsplit

from domain.brandrank.entities import (
    EntityIdentity,
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
    entity_positions = {record.entity_id: index for index, record in enumerate(entities)}
    records_by_id = {record.entity_id: record for record in master.entities}
    alias_index = dict(master.alias_index)
    relationship_index = dict(master.relationship_index)
    identity_index = dict(master.identity_index)
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
        roll_up_id = str(roll_up.get("entity_id") or "")
        base = records_by_id.get(roll_up_id) or records_by_id.get(entity_id)
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
                origin="request_model_inference",
                sync_status="request_only",
            )
            entities.append(record)
            entity_positions[record.entity_id] = len(entities) - 1
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
                origin="request_model_inference",
                sync_status="request_only",
            )
            entities[entity_positions[base.entity_id]] = record
        records_by_id[record.entity_id] = record
        alias_index[_key(decision.input_value)] = record
        relationship_index[_key(decision.input_value)] = str(relation.get("type") or "uncertain")
        identity_index[_key(decision.input_value)] = EntityIdentity(
            entity_id=(
                entity_id
                or "request-inferred-object:"
                + hashlib.sha256(
                    f"{master.source_release_id}|{decision.input_id}|identity".encode()
                ).hexdigest()[:24]
            ),
            canonical_name=str(identity.get("canonical_name") or decision.input_value),
            entity_type=str(identity.get("entity_type") or "unknown"),
        )
    return EntityMaster(
        domain=master.domain,
        schema_version=master.schema_version,
        revision=master.revision,
        aggregation_level=master.aggregation_level,
        entities=tuple(entities),
        alias_index=alias_index,
        relationship_index=relationship_index,
        identity_index=identity_index,
        resolution_policy=master.resolution_policy,
        source_system=master.source_system,
        source_release_id=master.source_release_id,
        source_content_hash=master.source_content_hash,
        source_mode=master.source_mode,
        source_error=master.source_error,
    )


class BrandEntityResolutionPack:
    domain_id = "brand/entity-resolution"
    policy_version = "brand-governance-v2"
    prompt_id = "brand-entity-resolution"
    prompt_version = "brand-entity-resolution-v5"
    tool_version = "brand-tools-v2"
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
    _IDENTITY_DECISIONS = {"existing", "propose_new", "ambiguous", "non_entity"}
    _RELATION_TYPES = {
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
    }
    _IMPACT_LEVELS = {"low", "medium", "high", "critical"}

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
            request.expected_release_id,
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
            raw_status = str(row.get("knowledge_status") or "unresolved")
            status = (
                KnowledgeStatus(raw_status)
                if reviewed and raw_status in {"published", "reviewed_local"}
                else KnowledgeStatus.UNRESOLVED
            )
            reason = str(row.get("eligibility_note") or row.get("classification_source") or "")
            decisions.append(
                Decision(
                    input_id=input_id,
                    input_value=value,
                    value={
                        "identity": {
                            "entity_id": row.get("identity_entity_id"),
                            "canonical_name": row.get("identity_canonical_name"),
                            "entity_type": row.get("identity_entity_type"),
                            "review_status": row.get("review_status"),
                            "origin": row.get("origin"),
                            "sync_status": row.get("sync_status"),
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
                    evidence_refs=tuple(
                        str(value) for value in row.get("evidence_urls", []) if str(value)
                    ),
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
        catalog_by_id: dict[str, dict[str, Any]] = {}
        for entity in master.entities:
            catalog_by_id[entity.entity_id] = {
                "entity_id": entity.entity_id,
                "canonical_name": entity.canonical_name,
                "aliases": list(entity.aliases),
                "entity_type": entity.entity_type,
                "brand_level": entity.brand_level,
                "parent_brand": entity.parent_brand,
                "review_status": entity.review_status,
                "roll_up_entity_id": entity.entity_id,
                "comparison": {
                    "eligible": entity.competitor_eligible,
                    "mode": entity.eligibility_mode,
                    "scopes": list(entity.competitor_scopes),
                },
            }
        for alias_key, identity in master.identity_index.items():
            if identity.entity_id in catalog_by_id:
                continue
            owner = master.alias_index[alias_key]
            catalog_by_id[identity.entity_id] = {
                "entity_id": identity.entity_id,
                "canonical_name": identity.canonical_name,
                "aliases": sorted(
                    alias
                    for alias in owner.aliases
                    if master.identity_index.get(_key(alias)) == identity
                ),
                "entity_type": identity.entity_type,
                "brand_level": identity.entity_type,
                "parent_brand": owner.canonical_name,
                "review_status": owner.review_status,
                "roll_up_entity_id": owner.entity_id,
                "comparison": {
                    "eligible": owner.competitor_eligible,
                    "mode": owner.eligibility_mode,
                    "scopes": list(owner.competitor_scopes),
                },
            }
        catalog = list(catalog_by_id.values())
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
                            "applicability",
                            "confidence",
                            "reasons",
                            "alternative_hypotheses",
                            "uncertainty",
                            "missing_evidence",
                            "impact_if_wrong",
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
                            "applicability": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "tasks",
                                    "industries",
                                    "regions",
                                    "audiences",
                                    "valid_from",
                                    "valid_until",
                                    "counterexamples",
                                ],
                                "properties": {
                                    "tasks": {"type": "array", "items": {"type": "string"}},
                                    "industries": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "regions": {"type": "array", "items": {"type": "string"}},
                                    "audiences": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "valid_from": {"type": ["string", "null"]},
                                    "valid_until": {"type": ["string", "null"]},
                                    "counterexamples": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
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
                            "missing_evidence": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "impact_if_wrong": {
                                "type": "string",
                                "enum": ["low", "medium", "high", "critical"],
                            },
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
                    "State task, industry, region, audience and time applicability; use empty "
                    "lists or null only when no narrower condition is supported.",
                    "List counterexamples, missing evidence and the impact if the judgment "
                    "is wrong.",
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
        if set(payload) != {"decisions"}:
            raise ValueError("brand_model_top_level_fields_invalid")
        raw_rows = payload.get("decisions")
        if not isinstance(raw_rows, list):
            raise ValueError("brand_model_decisions_required")
        requested = {
            input_id: value
            for index, item in enumerate(request.items)
            for input_id, value in [_item(item, index)]
        }
        expected = (
            {
                decision.input_id
                for decision in deterministic
                if decision.knowledge_status == KnowledgeStatus.UNRESOLVED
            }
            if request.policy == ReasoningPolicy.LLM_ASSISTED
            else set(requested)
        )
        master = self._master(request)
        known_rollups = {entity.entity_id: entity for entity in master.entities}
        known_identities: dict[str, EntityIdentity | EntityRecord] = dict(known_rollups)
        identity_rollups = {entity_id: entity_id for entity_id in known_rollups}
        known_identities.update(
            {identity.entity_id: identity for identity in master.identity_index.values()}
        )
        for alias_key, governed_identity in master.identity_index.items():
            owner_id = master.alias_index[alias_key].entity_id
            previous_owner = identity_rollups.setdefault(governed_identity.entity_id, owner_id)
            if previous_owner != owner_id:
                raise ValueError("brand_identity_has_multiple_rollups")
        allowed_refs = {
            str(value) for value in request.context.get("allowed_evidence_refs", []) if str(value)
        }

        def exact_fields(value: Mapping[str, Any], expected_fields: set[str], code: str) -> None:
            if set(value) != expected_fields:
                raise ValueError(code)

        def string_or_none(value: Any, code: str, *, maximum: int = 500) -> str | None:
            if value is None:
                return None
            if not isinstance(value, str):
                raise ValueError(code)
            rendered = value.strip()
            if not rendered or len(rendered) > maximum:
                raise ValueError(code)
            return rendered

        def string_list(
            value: Any,
            code: str,
            *,
            maximum_items: int = 20,
            maximum_length: int = 500,
        ) -> tuple[str, ...]:
            if not isinstance(value, list) or len(value) > maximum_items:
                raise ValueError(code)
            output_values: list[str] = []
            for item in value:
                if not isinstance(item, str):
                    raise ValueError(code)
                rendered = item.strip()
                if not rendered or len(rendered) > maximum_length:
                    raise ValueError(code)
                output_values.append(rendered)
            if len(set(output_values)) != len(output_values):
                raise ValueError(code)
            return tuple(output_values)

        def iso_date(value: Any, code: str) -> str | None:
            rendered = string_or_none(value, code, maximum=64)
            if rendered is None:
                return None
            try:
                datetime.fromisoformat(rendered.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(code) from exc
            return rendered

        seen: set[str] = set()
        output: list[Decision] = []
        for raw in raw_rows:
            if not isinstance(raw, dict):
                raise ValueError("brand_model_decision_invalid")
            exact_fields(
                raw,
                {
                    "input_id",
                    "identity",
                    "relation",
                    "roll_up",
                    "comparison",
                    "applicability",
                    "confidence",
                    "reasons",
                    "alternative_hypotheses",
                    "uncertainty",
                    "missing_evidence",
                    "impact_if_wrong",
                    "evidence_refs",
                },
                "brand_model_decision_fields_invalid",
            )
            input_id = raw.get("input_id")
            if not isinstance(input_id, str) or input_id not in expected or input_id in seen:
                raise ValueError("brand_model_input_set_invalid")
            seen.add(input_id)
            identity = raw.get("identity")
            relation = raw.get("relation")
            roll_up = raw.get("roll_up")
            comparison = raw.get("comparison")
            applicability = raw.get("applicability")
            confidence = raw.get("confidence")
            if not isinstance(identity, dict) or not isinstance(relation, dict):
                raise ValueError("brand_model_dimensions_required")
            if not isinstance(roll_up, dict) or not isinstance(comparison, dict):
                raise ValueError("brand_model_dimensions_required")
            if not isinstance(applicability, dict) or not isinstance(confidence, dict):
                raise ValueError("brand_model_dimensions_required")
            exact_fields(
                identity,
                {"decision", "entity_id", "canonical_name", "entity_type"},
                "brand_model_identity_fields_invalid",
            )
            exact_fields(relation, {"type"}, "brand_model_relation_fields_invalid")
            exact_fields(
                roll_up,
                {"entity_id", "display_name"},
                "brand_model_roll_up_fields_invalid",
            )
            exact_fields(
                comparison,
                {"eligible", "scopes"},
                "brand_model_comparison_fields_invalid",
            )
            exact_fields(
                applicability,
                {
                    "tasks",
                    "industries",
                    "regions",
                    "audiences",
                    "valid_from",
                    "valid_until",
                    "counterexamples",
                },
                "brand_model_applicability_fields_invalid",
            )
            exact_fields(
                confidence,
                {"identity", "relation", "roll_up", "eligibility"},
                "brand_model_confidence_fields_invalid",
            )

            identity_decision = identity.get("decision")
            if identity_decision not in self._IDENTITY_DECISIONS:
                raise ValueError("brand_model_identity_decision_invalid")
            entity_id = identity.get("entity_id")
            canonical_name = string_or_none(
                identity.get("canonical_name"), "brand_model_canonical_name_invalid"
            )
            entity_type = identity.get("entity_type")
            if not isinstance(entity_type, str) or entity_type not in {
                *self._OBJECT_TYPES,
                "unknown",
            }:
                raise ValueError("brand_model_entity_type_invalid")
            if identity_decision == "existing":
                if not isinstance(entity_id, str) or entity_id not in known_identities:
                    raise ValueError("brand_model_unknown_entity_id")
                governed = known_identities[entity_id]
                if canonical_name != governed.canonical_name or entity_type != governed.entity_type:
                    raise ValueError("brand_model_existing_identity_contradiction")
            elif entity_id is not None:
                raise ValueError("brand_model_new_entity_id_forbidden")
            elif identity_decision == "propose_new" and (
                canonical_name is None or entity_type == "unknown"
            ):
                raise ValueError("brand_model_new_identity_incomplete")

            relation_type = relation.get("type")
            if not isinstance(relation_type, str) or relation_type not in self._RELATION_TYPES:
                raise ValueError("brand_model_relation_invalid")
            roll_up_id = roll_up.get("entity_id")
            if roll_up_id is not None and (
                not isinstance(roll_up_id, str) or roll_up_id not in known_rollups
            ):
                raise ValueError("brand_model_unknown_roll_up_id")
            display_name = string_or_none(
                roll_up.get("display_name"), "brand_model_roll_up_display_invalid"
            )
            if roll_up_id is not None and display_name != known_rollups[roll_up_id].canonical_name:
                raise ValueError("brand_model_roll_up_contradiction")
            if identity_decision == "existing" and roll_up_id is None:
                raise ValueError("brand_model_existing_roll_up_required")
            if (
                identity_decision == "existing"
                and isinstance(entity_id, str)
                and roll_up_id != identity_rollups[entity_id]
            ):
                raise ValueError("brand_model_identity_roll_up_contradiction")
            if identity_decision == "existing" and isinstance(entity_id, str):
                governed_type = known_identities[entity_id].entity_type
                relation_type_requirements = {
                    "product_of": {"product", "tool"},
                    "business_unit_of": {"business_unit"},
                    "subsidiary_of": {"legal_entity", "company"},
                    "same_legal_entity": {"legal_entity", "company"},
                }
                allowed_types = relation_type_requirements.get(str(relation_type))
                if allowed_types is not None and governed_type not in allowed_types:
                    raise ValueError("brand_model_identity_relation_contradiction")

            eligible = comparison.get("eligible")
            if eligible is not None and not isinstance(eligible, bool):
                raise ValueError("brand_model_eligibility_invalid")
            scopes = string_list(
                comparison.get("scopes"),
                "brand_model_comparison_scopes_invalid",
                maximum_length=120,
            )
            applicability_value = {
                "tasks": list(
                    string_list(
                        applicability.get("tasks"),
                        "brand_model_applicability_invalid",
                        maximum_length=120,
                    )
                ),
                "industries": list(
                    string_list(
                        applicability.get("industries"),
                        "brand_model_applicability_invalid",
                        maximum_length=120,
                    )
                ),
                "regions": list(
                    string_list(
                        applicability.get("regions"),
                        "brand_model_applicability_invalid",
                        maximum_length=120,
                    )
                ),
                "audiences": list(
                    string_list(
                        applicability.get("audiences"),
                        "brand_model_applicability_invalid",
                        maximum_length=120,
                    )
                ),
                "valid_from": iso_date(
                    applicability.get("valid_from"), "brand_model_valid_time_invalid"
                ),
                "valid_until": iso_date(
                    applicability.get("valid_until"), "brand_model_valid_time_invalid"
                ),
                "counterexamples": list(
                    string_list(
                        applicability.get("counterexamples"),
                        "brand_model_applicability_invalid",
                    )
                ),
            }
            if (
                applicability_value["valid_from"] is not None
                and applicability_value["valid_until"] is not None
            ):
                valid_from = datetime.fromisoformat(
                    str(applicability_value["valid_from"]).replace("Z", "+00:00")
                )
                valid_until = datetime.fromisoformat(
                    str(applicability_value["valid_until"]).replace("Z", "+00:00")
                )
                if valid_from.tzinfo is None:
                    valid_from = valid_from.replace(tzinfo=UTC)
                if valid_until.tzinfo is None:
                    valid_until = valid_until.replace(tzinfo=UTC)
                if valid_from > valid_until:
                    raise ValueError("brand_model_valid_time_order_invalid")
            if identity_decision == "ambiguous" and (
                relation_type != "uncertain" or roll_up_id is not None or eligible is not None
            ):
                raise ValueError("brand_model_ambiguous_contradiction")
            if identity_decision == "non_entity" and (
                relation_type not in {"non_vendor", "uncertain"}
                or roll_up_id is not None
                or eligible not in {False, None}
            ):
                raise ValueError("brand_model_non_entity_contradiction")

            refs = raw.get("evidence_refs")
            evidence_refs = string_list(
                refs,
                "brand_model_evidence_ref_invalid",
                maximum_items=50,
                maximum_length=500,
            )
            if any(value not in allowed_refs for value in evidence_refs):
                raise ValueError("brand_model_evidence_ref_invalid")
            reasons = string_list(raw.get("reasons"), "brand_model_explanation_invalid")
            alternatives = string_list(
                raw.get("alternative_hypotheses"), "brand_model_explanation_invalid"
            )
            uncertainty = string_list(raw.get("uncertainty"), "brand_model_explanation_invalid")
            missing_evidence = string_list(
                raw.get("missing_evidence"), "brand_model_missing_evidence_invalid"
            )
            impact_if_wrong = raw.get("impact_if_wrong")
            if impact_if_wrong not in self._IMPACT_LEVELS:
                raise ValueError("brand_model_impact_invalid")
            scores = []
            for key in ("identity", "relation", "roll_up", "eligibility"):
                raw_score = confidence.get(key)
                if isinstance(raw_score, bool) or not isinstance(raw_score, int | float):
                    raise ValueError("brand_model_confidence_invalid")
                score = float(raw_score)
                if not isfinite(score) or not 0 <= score <= 1:
                    raise ValueError("brand_model_confidence_invalid")
                scores.append(score)
            output.append(
                Decision(
                    input_id=input_id,
                    input_value=requested[input_id],
                    value={
                        "identity": identity,
                        "relation": relation,
                        "roll_up": {"entity_id": roll_up_id, "display_name": display_name},
                        "comparison": {"eligible": eligible, "scopes": list(scopes)},
                        "applicability": applicability_value,
                        "missing_evidence": list(missing_evidence),
                        "impact_if_wrong": impact_if_wrong,
                        "requires_governance": True,
                    },
                    knowledge_status=KnowledgeStatus.MODEL_INFERRED,
                    decision_scope=DecisionScope.REQUEST,
                    confidence=min(scores),
                    reasons=reasons,
                    alternative_hypotheses=alternatives,
                    uncertainty=uncertainty,
                    evidence_refs=evidence_refs,
                )
            )
        if seen != expected:
            raise ValueError("brand_model_all_prompted_items_required")
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
            # Never persist caller prose merely because it was labelled "safe".
            # Keep only controlled routing fields plus an irreversible receipt for
            # any caller-supplied summary. This preserves cross-project signal
            # without retaining customer questions or answers.
            caller_summary = str(
                item.get("safe_context") or request.context.get("safe_context") or ""
            ).strip()
            safe_context = json.dumps(
                {
                    "analysis_domain": _analysis_domain(request),
                    "comparison_scopes": sorted(
                        {
                            str(value).strip()
                            for value in request.context.get("comparison_scopes", [])
                            if str(value).strip()
                        }
                    ),
                    "task": request.task,
                    "region": str(request.context.get("region") or "").strip() or None,
                    "audience": str(request.context.get("audience") or "").strip() or None,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
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
                    safe_context=safe_context[:500],
                    data_classification=request.data_classification,
                    visibility="private" if request.data_classification != "public" else "public",
                    payload={
                        "knowledge_status": decision.knowledge_status.value,
                        "confidence": decision.confidence,
                        "model_provider": decision.model_provider,
                        "model": decision.model_name,
                        "prompt_version": decision.prompt_version,
                        "caller_safe_context_hash": (
                            "sha256:" + hashlib.sha256(caller_summary.encode()).hexdigest()
                            if caller_summary
                            else None
                        ),
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
                identities = attributes.get("alias_identities") or {}
                if not isinstance(identities, dict):
                    issues.append(f"alias_identities_invalid:{stable_id}")
                else:
                    identity_definitions: dict[str, tuple[str, str]] = {}
                    for alias, identity in identities.items():
                        if not isinstance(alias, str) or not isinstance(identity, dict):
                            issues.append(f"alias_identity_invalid:{stable_id}")
                            continue
                        identity_id = str(identity.get("entity_id") or "").strip()
                        identity_name = str(identity.get("canonical_name") or "").strip()
                        identity_type = str(identity.get("entity_type") or "").strip()
                        identity_evidence = identity.get("evidence_urls")
                        if (
                            not identity_id
                            or not identity_name
                            or identity_type not in self._OBJECT_TYPES
                        ):
                            issues.append(f"alias_identity_invalid:{stable_id}:{alias}")
                        previous = identity_definitions.setdefault(
                            identity_id, (identity_name, identity_type)
                        )
                        if previous != (identity_name, identity_type):
                            issues.append(f"alias_identity_conflict:{identity_id}")
                        if not isinstance(identity_evidence, list) or not identity_evidence:
                            issues.append(f"alias_identity_evidence_required:{identity_id}")
                        elif any(
                            not isinstance(source, str)
                            or (parsed := urlsplit(source)).scheme != "https"
                            or not parsed.hostname
                            or parsed.username is not None
                            or parsed.password is not None
                            for source in identity_evidence
                        ):
                            issues.append(f"alias_identity_evidence_invalid:{identity_id}")
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

    def validate_release_impact(
        self,
        changes: Iterable[Mapping[str, Any]],
        quality_report: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Require reproducible historical replay before brand knowledge publication."""

        change_rows = list(changes)
        replay = quality_report.get("historical_replay")
        issues: list[str] = []
        if not isinstance(replay, Mapping):
            issues.append("historical_replay_required")
            replay = {}

        if replay.get("schema_version") != "historical-replay-v1":
            issues.append("historical_replay_schema_invalid")
        dataset_hash = str(replay.get("evaluation_set_hash") or "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", dataset_hash):
            issues.append("historical_replay_dataset_hash_invalid")
        cutoff = str(replay.get("time_cutoff") or "")
        try:
            parsed_cutoff = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
            if parsed_cutoff.tzinfo is None:
                raise ValueError
        except ValueError:
            issues.append("historical_replay_time_cutoff_invalid")

        integer_fields = (
            "evaluated_request_count",
            "baseline_error_count",
            "candidate_error_count",
            "corrected_error_count",
            "new_error_count",
            "allowed_new_error_count",
        )
        counts: dict[str, int] = {}
        for field in integer_fields:
            value = replay.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                issues.append(f"historical_replay_{field}_invalid")
            else:
                counts[field] = value
        if counts.get("evaluated_request_count", 0) < 1:
            issues.append("historical_replay_empty")
        if counts.get("new_error_count", 0) > counts.get("allowed_new_error_count", -1):
            issues.append("historical_replay_regression_budget_exceeded")
        if replay.get("passed") is not True:
            issues.append("historical_replay_not_passed")

        return {
            "passed": not issues,
            "replay_required": True,
            "issues": sorted(set(issues)),
            "change_count": len(change_rows),
            "evaluation_set_hash": dataset_hash or None,
            "evaluated_request_count": counts.get("evaluated_request_count", 0),
            "new_error_count": counts.get("new_error_count"),
            "allowed_new_error_count": counts.get("allowed_new_error_count"),
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
            entity["origin"] = str(item.get("origin") or "governed_change_set")
            entity["sync_status"] = str(item.get("sync_status") or "local_ahead")
            entity["knowledge_status"] = (
                "published"
                if item.get("visibility") == "public" and entity["sync_status"] == "reconciled"
                else "reviewed_local"
            )
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
