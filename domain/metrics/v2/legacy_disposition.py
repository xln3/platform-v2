"""Governance manifest for every V1 customer metric and report alias."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .definition_schema import DefinitionValidationError

DEFAULT_MANIFEST_PATH = Path(__file__).with_name("legacy_disposition.json")
DISPOSITION_CLASSES = frozenset({"published", "diagnostic", "experimental", "legacy"})


def load_legacy_dispositions(
    path: str | Path = DEFAULT_MANIFEST_PATH,
) -> Mapping[str, str]:
    manifest_path = Path(path)
    try:
        raw: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DefinitionValidationError(
            f"cannot load legacy metric disposition {manifest_path}: {exc}"
        ) from exc
    if not isinstance(raw, Mapping) or raw.get("manifest_version") != "2.0.0":
        raise DefinitionValidationError("legacy disposition manifest version is invalid")
    dispositions = raw.get("dispositions")
    if not isinstance(dispositions, Mapping):
        raise DefinitionValidationError("legacy disposition manifest requires an object")
    result: dict[str, str] = {}
    for name, disposition in dispositions.items():
        if not isinstance(name, str) or not name:
            raise DefinitionValidationError("legacy metric name must be a non-empty string")
        if disposition not in DISPOSITION_CLASSES:
            raise DefinitionValidationError(
                f"legacy metric {name} has invalid disposition: {disposition}"
            )
        result[name] = disposition
    return result


def validate_legacy_catalog(
    catalog_metric_names: Iterable[str],
    dispositions: Mapping[str, str] | None = None,
) -> None:
    governed = dict(dispositions or load_legacy_dispositions())
    invalid = sorted(
        name for name, disposition in governed.items() if disposition not in DISPOSITION_CLASSES
    )
    if invalid:
        raise DefinitionValidationError(
            "legacy metrics have invalid dispositions: " + ", ".join(invalid)
        )
    missing = sorted(set(catalog_metric_names) - set(governed))
    if missing:
        raise DefinitionValidationError(
            "customer metric catalog contains ungoverned names: " + ", ".join(missing)
        )
