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

EXPECTED_TASK_REFS = {
    "query-intent@2.0.0",
    "query-brand-entity-resolution@2.0.0",
    "answer-entity-resolution@2.0.0",
    "substantive-entity-mention@2.0.0",
    "recommendation-relation@2.0.0",
    "rank-semantics@2.0.0",
    "stance-and-pairwise@2.0.0",
    "requested-dimension-applicability@2.0.0",
    "answer-dimension-coverage@2.0.0",
    "claim-extraction@2.0.0",
    "claim-verifiability@2.0.0",
    "claim-evidence-verdict@2.0.0",
    "citation-claim-support@2.0.0",
    "risk-adjudication@2.0.0",
}


def test_builtin_registry_covers_every_required_semantic_capability() -> None:
    registry = load_builtin_task_definitions()

    assert {definition.task_ref for definition in registry.definitions} == EXPECTED_TASK_REFS
    extraction_index = registry.topological_refs.index("claim-extraction@2.0.0")
    verifiability_index = registry.topological_refs.index("claim-verifiability@2.0.0")
    assert extraction_index < verifiability_index
    assert registry.topological_refs.index(
        "claim-verifiability@2.0.0"
    ) < registry.topological_refs.index("claim-evidence-verdict@2.0.0")


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
