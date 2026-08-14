"""Governed entity normalization for customer-facing brand comparisons.

The historical brand-rank rules were built to reproduce an old extraction script.
They intentionally kept duplicate aliases inside one answer and had no concept of
companies, products, tools, or institutions.  That behaviour remains available in
``domain.brandrank.rules`` for compatibility, while this module is the stricter
boundary used by delivery reports.

Entity masters are domain data, not report-template conditionals.  Unknown extracted
names are retained as ``unknown`` audit rows but are fail-closed out of competitor
tables until a reviewer classifies them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .rules import DomainRules, normalize_brand

_DATA_DIR = Path(__file__).resolve().parent / "rules_data"
_SPACE_RE = re.compile(r"\s+")
_INSTITUTION_PATTERNS = (
    "会计师事务所",
    "测评中心",
    "认证中心",
    "研究院",
    "协会",
    "委员会",
    "企业管理咨询",
    "财务咨询",
)


@dataclass(frozen=True, slots=True)
class EntityRecord:
    canonical_name: str
    aliases: tuple[str, ...]
    entity_type: str
    competitor_eligible: bool
    brand_level: str
    parent_brand: str | None = None


@dataclass(frozen=True, slots=True)
class EntityMaster:
    domain: str
    entities: tuple[EntityRecord, ...]
    alias_index: dict[str, EntityRecord]


def _key(value: str) -> str:
    """Canonical lookup key without changing customer-visible spelling."""

    return _SPACE_RE.sub("", value).casefold()


@lru_cache(maxsize=8)
def load_entity_master(domain: str) -> EntityMaster:
    path = _DATA_DIR / f"entity_master_{domain}.json"
    if not path.is_file():
        # A missing master is an honest empty master: legacy metrics can still be
        # calculated, but no unknown entity silently becomes a customer competitor.
        return EntityMaster(domain=domain, entities=(), alias_index={})
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("domain") != domain or not isinstance(document.get("entities"), list):
        raise ValueError(f"invalid entity master: {path}")
    entities: list[EntityRecord] = []
    alias_index: dict[str, EntityRecord] = {}
    for raw in document["entities"]:
        if not isinstance(raw, dict):
            raise ValueError(f"invalid entity record: {path}")
        canonical = str(raw.get("canonical_name") or "").strip()
        entity_type = str(raw.get("entity_type") or "").strip()
        aliases = tuple(
            dict.fromkeys(
                [canonical]
                + [str(value).strip() for value in raw.get("aliases", []) if str(value).strip()]
            )
        )
        if not canonical or entity_type not in {"company", "product", "tool", "institution"}:
            raise ValueError(f"invalid entity record: {path}")
        record = EntityRecord(
            canonical_name=canonical,
            aliases=aliases,
            entity_type=entity_type,
            competitor_eligible=bool(raw.get("competitor_eligible")),
            brand_level=str(raw.get("brand_level") or "brand"),
            parent_brand=(str(raw["parent_brand"]).strip() if raw.get("parent_brand") else None),
        )
        entities.append(record)
        for alias in aliases:
            lookup = _key(alias)
            existing = alias_index.get(lookup)
            if existing is not None and existing != record:
                raise ValueError(f"entity alias conflict: {alias!r}")
            alias_index[lookup] = record
    return EntityMaster(domain=domain, entities=tuple(entities), alias_index=alias_index)


def classify_entity(
    value: str,
    *,
    rules: DomainRules,
    master: EntityMaster,
    target_brand: str | None = None,
    named_competitors: Iterable[str] = (),
) -> dict[str, Any]:
    """Normalize one extracted value and return an auditable entity row.

    Project target/competitor names are explicit business master data and therefore
    remain eligible even if a new industry master has not yet been updated.  All
    other unknown names are retained but excluded from customer competitor charts.
    """

    raw = str(value or "").strip()
    legacy_normalized = normalize_brand(raw, rules) if raw else ""
    record = master.alias_index.get(_key(raw)) or master.alias_index.get(_key(legacy_normalized))
    project_names = [target_brand, *named_competitors]
    project_lookup = {
        _key(str(name)): str(name).strip() for name in project_names if str(name or "").strip()
    }
    project_canonical = project_lookup.get(_key(raw)) or project_lookup.get(_key(legacy_normalized))
    if record is not None:
        return {
            "raw_name": raw,
            "canonical_name": record.canonical_name,
            "entity_type": record.entity_type,
            "competitor_eligible": record.competitor_eligible,
            "brand_level": record.brand_level,
            "parent_brand": record.parent_brand,
            "classification_source": "entity_master",
        }
    if project_canonical:
        return {
            "raw_name": raw,
            "canonical_name": project_canonical,
            "entity_type": "company",
            "competitor_eligible": True,
            "brand_level": "brand",
            "parent_brand": None,
            "classification_source": "project_master",
        }
    if legacy_normalized in rules.exclude_terms or any(
        marker in legacy_normalized for marker in _INSTITUTION_PATTERNS
    ):
        return {
            "raw_name": raw,
            "canonical_name": legacy_normalized,
            "entity_type": "institution",
            "competitor_eligible": False,
            "brand_level": "institution",
            "parent_brand": None,
            "classification_source": "governed_pattern",
        }
    return {
        "raw_name": raw,
        "canonical_name": legacy_normalized,
        "entity_type": "unknown",
        "competitor_eligible": False,
        "brand_level": "unclassified",
        "parent_brand": None,
        "classification_source": "unclassified",
    }


def normalize_answer_entities(
    values: Iterable[str],
    *,
    rules: DomainRules,
    master: EntityMaster,
    target_brand: str | None = None,
    named_competitors: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Classify and de-duplicate canonical entities within one answer, preserving rank.

    The first occurrence defines the canonical entity's answer-level rank.  Later
    aliases of the same entity remain visible in ``raw_aliases`` but never increment
    occurrences or shift another entity's position.
    """

    output: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}
    for value in values:
        row = classify_entity(
            str(value),
            rules=rules,
            master=master,
            target_brand=target_brand,
            named_competitors=named_competitors,
        )
        canonical = str(row["canonical_name"] or "").strip()
        if not canonical:
            continue
        lookup = _key(canonical)
        existing = by_name.get(lookup)
        if existing is not None:
            if row["raw_name"] not in existing["raw_aliases"]:
                existing["raw_aliases"].append(row["raw_name"])
            continue
        governed = {**row, "raw_aliases": [row["raw_name"]], "answer_rank": len(output) + 1}
        by_name[lookup] = governed
        output.append(governed)
    return output


__all__ = [
    "EntityMaster",
    "EntityRecord",
    "classify_entity",
    "load_entity_master",
    "normalize_answer_entities",
]
