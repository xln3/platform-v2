from __future__ import annotations

from copy import deepcopy

import pytest
from decision_v2_fixtures import task
from pydantic import ValidationError

from domain.analysis.v2.decision_task_loader import (
    DecisionTaskRegistry,
    DefinitionLoadError,
    load_builtin_task_definitions,
)
from domain.analysis.v2.decision_task_schema import DecisionTaskDefinition

TASK_NAMES = {
    "query-intent",
    "query-brand-entity-resolution",
    "answer-entity-resolution",
    "substantive-entity-mention",
    "recommendation-relation",
    "rank-semantics",
    "stance-and-pairwise",
    "requested-dimension-applicability",
    "answer-dimension-coverage",
    "claim-extraction",
    "claim-verifiability",
    "claim-evidence-verdict",
    "citation-claim-support",
    "risk-adjudication",
}
EXPECTED_TASK_REFS = {f"{name}@{version}" for name in TASK_NAMES for version in ("2.0.0", "2.1.0")}


def test_builtin_registry_covers_every_required_semantic_capability() -> None:
    registry = load_builtin_task_definitions()

    assert {definition.task_ref for definition in registry.definitions} == EXPECTED_TASK_REFS
    for version in ("2.0.0", "2.1.0"):
        extraction_index = registry.topological_refs.index(f"claim-extraction@{version}")
        verifiability_index = registry.topological_refs.index(f"claim-verifiability@{version}")
        assert extraction_index < verifiability_index
        assert verifiability_index < registry.topological_refs.index(
            f"claim-evidence-verdict@{version}"
        )


def test_definition_hash_is_key_order_independent_and_semantic_change_sensitive() -> None:
    original = task("recommendation-relation")
    payload = original.model_dump(mode="python", exclude={"definition_hash"})
    reversed_payload = dict(reversed(tuple(payload.items())))

    same = DecisionTaskDefinition.model_validate(reversed_payload)
    changed = DecisionTaskDefinition.model_validate(
        payload | {"business_question": original.business_question + " (revised)"}
    )

    assert same.definition_hash == original.definition_hash
    assert changed.definition_hash != original.definition_hash


def test_definition_rejects_tampered_declared_hash_and_mutation() -> None:
    original = task("rank-semantics")
    payload = original.model_dump(mode="python") | {"definition_hash": "0" * 64}

    with pytest.raises(ValidationError, match="definition_hash_mismatch"):
        DecisionTaskDefinition.model_validate(payload)
    with pytest.raises(ValidationError, match="frozen"):
        original.business_question = "mutated"  # type: ignore[misc]


def test_registry_rejects_missing_dependency_and_cycle() -> None:
    base = task("query-intent")
    payload = base.model_dump(mode="python", exclude={"definition_hash"})
    missing = DecisionTaskDefinition.model_validate(
        payload | {"name": "missing-parent", "dependency_task_refs": ("absent-task@2.0.0",)}
    )
    with pytest.raises(DefinitionLoadError, match="dependency_task_missing"):
        DecisionTaskRegistry((missing,))

    left_payload = deepcopy(payload)
    left_payload.update(name="cycle-left", dependency_task_refs=("cycle-right@2.0.0",))
    right_payload = deepcopy(payload)
    right_payload.update(name="cycle-right", dependency_task_refs=("cycle-left@2.0.0",))
    left = DecisionTaskDefinition.model_validate(left_payload)
    right = DecisionTaskDefinition.model_validate(right_payload)
    with pytest.raises(DefinitionLoadError, match="decision_task_dependency_cycle"):
        DecisionTaskRegistry((left, right))


def test_task_schema_and_policy_forbid_open_outputs_and_heuristic_fallback() -> None:
    original = task("query-intent")
    payload = original.model_dump(mode="python", exclude={"definition_hash"})
    open_schema = deepcopy(payload["output_schema"])
    open_schema["additionalProperties"] = True

    with pytest.raises(ValidationError, match="task_schema_must_forbid_additional_properties"):
        DecisionTaskDefinition.model_validate(payload | {"output_schema": open_schema})
    with pytest.raises(ValidationError, match="heuristic_fallback_forbidden"):
        DecisionTaskDefinition.model_validate(
            payload | {"abstention_policy": {"fallback_to_heuristics": True}}
        )


def test_online_tasks_require_only_one_proposer_without_nested_review_gates() -> None:
    definitions = tuple(
        item for item in load_builtin_task_definitions().definitions if item.version == "2.1.0"
    )

    assert all(item.adjudication_policy.required_roles == ("proposer",) for item in definitions)
    assert all(not item.evidence_requirements.requires_independent_verifier for item in definitions)
    assert all(item.adjudication_policy.high_severity_requires is None for item in definitions)


def test_frozen_v2_tasks_remain_available_with_their_original_review_gates() -> None:
    registry = load_builtin_task_definitions()

    assert registry.get(
        "claim-evidence-verdict@2.0.0"
    ).evidence_requirements.requires_independent_verifier
    assert (
        registry.get("risk-adjudication@2.0.0").adjudication_policy.high_severity_requires
        == "double_judge"
    )
