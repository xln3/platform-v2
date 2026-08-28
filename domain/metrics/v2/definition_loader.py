"""Load version-controlled metric protocols without executing their contents."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .canonical_hash import canonical_set_hash
from .definition_schema import (
    DefinitionValidationError,
    MetricDefinition,
    validate_metric_definition,
)

DEFAULT_DEFINITION_DIRECTORY = Path(__file__).with_name("definitions")


class DefinitionRegistry:
    def __init__(self, definitions: Iterable[MetricDefinition]) -> None:
        by_key: dict[tuple[str, str], MetricDefinition] = {}
        by_hash: dict[str, MetricDefinition] = {}
        for definition in definitions:
            key = (definition.name, definition.version)
            if key in by_key:
                raise DefinitionValidationError(f"duplicate metric definition: {key}")
            if definition.definition_hash in by_hash:
                other = by_hash[definition.definition_hash]
                raise DefinitionValidationError(
                    f"definition hash reused by {other.name} and {definition.name}"
                )
            by_key[key] = definition
            by_hash[definition.definition_hash] = definition
        self._by_key = by_key
        self._by_hash = by_hash

    def get(self, name: str, version: str | None = None) -> MetricDefinition:
        if version is not None:
            try:
                return self._by_key[(name, version)]
            except KeyError as exc:
                raise KeyError(f"unknown metric definition: {name}@{version}") from exc
        matches = [item for (item_name, _), item in self._by_key.items() if item_name == name]
        if len(matches) != 1:
            raise KeyError(f"metric version must be explicit for {name}")
        return matches[0]

    def all(self) -> tuple[MetricDefinition, ...]:
        return tuple(self._by_key[key] for key in sorted(self._by_key))

    @property
    def definition_set_hash(self) -> str:
        return canonical_set_hash(
            {"name": item.name, "version": item.version, "hash": item.definition_hash}
            for item in self._by_key.values()
        )

    def validate_published_dependencies(self, published_task_refs: Iterable[str]) -> None:
        published = set(published_task_refs)
        for definition in self._by_key.values():
            if definition.status.value != "published":
                continue
            required = {item.task_ref for item in definition.required_semantic_capabilities}
            required.update(definition.decision_task_refs)
            missing = sorted(required - published)
            if missing:
                raise DefinitionValidationError(
                    f"{definition.name}@{definition.version} references unpublished tasks: "
                    + ", ".join(missing)
                )


def load_definition(source: str | Path | Mapping[str, Any]) -> MetricDefinition:
    if isinstance(source, Mapping):
        return validate_metric_definition(source)
    path = Path(source)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DefinitionValidationError(f"cannot load metric definition {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise DefinitionValidationError(f"metric definition {path} must contain one object")
    return validate_metric_definition(raw)


def load_definitions(directory: str | Path = DEFAULT_DEFINITION_DIRECTORY) -> DefinitionRegistry:
    path = Path(directory)
    definitions: list[MetricDefinition] = []
    for definition_path in sorted(path.glob("*.json")):
        if definition_path.name == "legacy_disposition.json":
            continue
        try:
            raw = json.loads(definition_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DefinitionValidationError(
                f"cannot load metric definition {definition_path}: {exc}"
            ) from exc
        if isinstance(raw, list):
            definitions.extend(validate_metric_definition(item) for item in raw)
        elif isinstance(raw, Mapping):
            definitions.append(validate_metric_definition(raw))
        else:
            raise DefinitionValidationError(f"{definition_path} must contain an object or array")
    if not definitions:
        raise DefinitionValidationError(f"no metric definitions found in {path}")
    return DefinitionRegistry(definitions)
