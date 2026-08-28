"""Version-controlled DecisionTask and JudgePolicy artifact loaders."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from pydantic import ValidationError

from domain.analysis.v2.decision_task_schema import (
    DecisionTaskDefinition,
    JudgePolicyDefinition,
    validate_policy_compatibility,
)
from domain.analysis.v2.output_validation import validate_schema_definition

_PACKAGE_ROOT = Path(__file__).resolve().parent
BUILTIN_TASK_DIRECTORY = _PACKAGE_ROOT / "decision_tasks"
BUILTIN_POLICY_DIRECTORY = _PACKAGE_ROOT / "judge_policies"


class DefinitionLoadError(ValueError):
    def __init__(self, code: str, *, source: Path | None = None, detail: str | None = None) -> None:
        self.code = code
        self.source = source
        self.detail = detail
        message = code
        if source is not None:
            message += f":{source}"
        if detail:
            message += f":{detail}"
        super().__init__(message)


class DecisionTaskRegistry:
    """Immutable indexed task collection with a validated dependency DAG."""

    def __init__(self, definitions: Iterable[DecisionTaskDefinition]) -> None:
        ordered = tuple(sorted(definitions, key=lambda item: item.task_ref))
        by_ref = {definition.task_ref: definition for definition in ordered}
        if len(by_ref) != len(ordered):
            raise DefinitionLoadError("duplicate_task_ref")
        hashes = [definition.definition_hash for definition in ordered]
        if len(hashes) != len(set(hashes)):
            raise DefinitionLoadError("duplicate_task_definition_hash")
        self._definitions = ordered
        self._by_ref = by_ref
        self._topological_refs = _topological_order(by_ref)

    @property
    def definitions(self) -> tuple[DecisionTaskDefinition, ...]:
        return self._definitions

    @property
    def topological_refs(self) -> tuple[str, ...]:
        return self._topological_refs

    def get(self, task_ref: str) -> DecisionTaskDefinition:
        try:
            return self._by_ref[task_ref]
        except KeyError as error:
            raise KeyError(f"unknown_decision_task:{task_ref}") from error

    def by_name(self, name: str) -> tuple[DecisionTaskDefinition, ...]:
        return tuple(item for item in self._definitions if item.name == name)

    def validate_policy(self, policy: JudgePolicyDefinition) -> None:
        for task_ref in policy.compatible_task_refs:
            validate_policy_compatibility(self.get(task_ref), policy)


def load_task_definitions(directory: Path) -> DecisionTaskRegistry:
    definitions: list[DecisionTaskDefinition] = []
    for source, payload in _artifact_payloads(directory):
        try:
            definition = DecisionTaskDefinition.model_validate(payload)
            validate_schema_definition(definition.subject_ref_schema)
            validate_schema_definition(definition.input_schema)
            validate_schema_definition(definition.output_schema)
        except (ValidationError, ValueError, TypeError) as error:
            raise DefinitionLoadError(
                "decision_task_definition_invalid", source=source, detail=str(error)
            ) from error
        definitions.append(definition)
    if not definitions:
        raise DefinitionLoadError("decision_task_definitions_missing", source=directory)
    return DecisionTaskRegistry(definitions)


def load_judge_policies(
    directory: Path,
    *,
    tasks: DecisionTaskRegistry | None = None,
) -> tuple[JudgePolicyDefinition, ...]:
    policies: list[JudgePolicyDefinition] = []
    for source, payload in _artifact_payloads(directory):
        try:
            policy = JudgePolicyDefinition.model_validate(payload)
            if tasks is not None:
                tasks.validate_policy(policy)
        except (ValidationError, ValueError, KeyError) as error:
            raise DefinitionLoadError(
                "semantic_judge_policy_invalid", source=source, detail=str(error)
            ) from error
        policies.append(policy)
    if not policies:
        raise DefinitionLoadError("semantic_judge_policies_missing", source=directory)
    refs = [policy.policy_ref for policy in policies]
    hashes = [policy.policy_hash for policy in policies]
    if len(refs) != len(set(refs)):
        raise DefinitionLoadError("duplicate_judge_policy_ref")
    if len(hashes) != len(set(hashes)):
        raise DefinitionLoadError("duplicate_judge_policy_hash")
    return tuple(sorted(policies, key=lambda item: item.policy_ref))


def load_builtin_task_definitions() -> DecisionTaskRegistry:
    return load_task_definitions(BUILTIN_TASK_DIRECTORY)


def load_builtin_judge_policies(
    *, tasks: DecisionTaskRegistry | None = None
) -> tuple[JudgePolicyDefinition, ...]:
    task_registry = tasks or load_builtin_task_definitions()
    return load_judge_policies(BUILTIN_POLICY_DIRECTORY, tasks=task_registry)


def _artifact_payloads(directory: Path) -> Iterable[tuple[Path, dict[str, object]]]:
    if not directory.is_dir():
        raise DefinitionLoadError("definition_directory_missing", source=directory)
    for source in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DefinitionLoadError("definition_json_invalid", source=source) from error
        payloads = raw if isinstance(raw, list) else [raw]
        if not all(isinstance(payload, dict) for payload in payloads):
            raise DefinitionLoadError("definition_json_root_invalid", source=source)
        for payload in payloads:
            yield source, payload


def _topological_order(
    definitions: dict[str, DecisionTaskDefinition],
) -> tuple[str, ...]:
    state: dict[str, int] = {}
    ordered: list[str] = []

    def visit(task_ref: str, path: tuple[str, ...]) -> None:
        if task_ref not in definitions:
            raise DefinitionLoadError("dependency_task_missing", detail=task_ref)
        if state.get(task_ref) == 1:
            cycle = " -> ".join((*path, task_ref))
            raise DefinitionLoadError("decision_task_dependency_cycle", detail=cycle)
        if state.get(task_ref) == 2:
            return
        state[task_ref] = 1
        for dependency in sorted(definitions[task_ref].dependency_task_refs):
            visit(dependency, (*path, task_ref))
        state[task_ref] = 2
        ordered.append(task_ref)

    for ref in sorted(definitions):
        visit(ref, ())
    return tuple(ordered)
