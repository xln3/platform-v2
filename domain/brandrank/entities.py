"""Governed entity normalization for customer-facing brand comparisons.

The historical brand-rank rules were built to reproduce an old extraction script.
They intentionally kept duplicate aliases inside one answer and had no concept of
companies, products, tools, or institutions.  That behaviour remains available in
``domain.brandrank.rules`` for compatibility, while this module is the stricter
boundary used by customer-facing rankings and delivery reports.

Entity masters are domain data, not report-template conditionals.  Unknown extracted
names are retained as ``unknown`` audit rows but are fail-closed out of competitor
tables until a reviewer classifies them.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from domain.knowledge_evolution.release import KnowledgeReleaseError, KnowledgeReleaseStore
from domain.siliconindex import validate_snapshot

from .rules import DomainRules

_DATA_DIR = Path(__file__).resolve().parent / "rules_data"
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class EntityRecord:
    entity_id: str
    canonical_name: str
    aliases: tuple[str, ...]
    entity_type: str
    competitor_eligible: bool
    eligibility_mode: str
    brand_level: str
    parent_brand: str | None = None
    industry_fit: str | None = None
    competitor_scopes: tuple[str, ...] = ()
    eligibility_note: str | None = None
    evidence_urls: tuple[str, ...] = ()
    review_status: str = "reviewed"
    knowledge_status: str = "published"
    origin: str = "unknown"
    sync_status: str = "unknown"


@dataclass(frozen=True, slots=True)
class EntityIdentity:
    """Concrete object named by a surface form before any ranking roll-up."""

    entity_id: str
    canonical_name: str
    entity_type: str
    evidence_urls: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EntityMaster:
    domain: str
    schema_version: str
    revision: str
    aggregation_level: str
    entities: tuple[EntityRecord, ...]
    alias_index: dict[str, EntityRecord]
    relationship_index: dict[str, str]
    identity_index: dict[str, EntityIdentity]
    resolution_policy: str = ""
    source_system: str = ""
    source_release_id: str = ""
    source_content_hash: str = ""
    source_mode: str = ""
    source_error: str = ""

    def relationship_for(self, value: str) -> str | None:
        """Return the reviewed relationship for one surface name, if known."""

        return self.relationship_index.get(_key(value))

    def identity_for(self, value: str) -> EntityIdentity | None:
        """Return the concrete reviewed object denoted by a surface name."""

        return self.identity_index.get(_key(value))


def _key(value: str) -> str:
    """Canonical lookup key without changing customer-visible spelling."""

    # NFKC only removes typography-level variation (full-width ASCII/brackets,
    # compatibility forms).  It deliberately does not perform substring or edit-
    # distance matching: deciding that two real-world brands are the same entity is
    # a governed semantic decision, not a string-similarity side effect.
    normalized = unicodedata.normalize("NFKC", value)
    return _SPACE_RE.sub("", normalized).casefold()


def _empty_master(domain: str) -> EntityMaster:
    return EntityMaster(
        domain=domain,
        schema_version="entity-master-none",
        revision="",
        aggregation_level="legacy",
        entities=(),
        alias_index={},
        relationship_index={},
        identity_index={},
    )


_RELATIONSHIP_BY_MENTION_TYPE = {
    "canonical": "self",
    "official_name": "same_legal_entity",
    "company_name": "same_legal_entity",
    "common_alias": "trade_name",
    "english": "english_name",
    "abbreviation": "official_abbreviation",
    "product_line": "product_of",
    "historical_name": "historical_name",
}


def _siliconindex_data_dir(snapshot_dir: str | Path) -> Path:
    root = Path(snapshot_dir).resolve()
    pointer = root / "CURRENT"
    if pointer.is_file():
        release = pointer.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", release) is None:
            raise ValueError("invalid_siliconindex_current_pointer")
        root = root / release
    for candidate in (root, root / "data" / "v1", root / "public" / "data" / "v1"):
        if (candidate / "brands.json").is_file() and (candidate / "mentions.json").is_file():
            return candidate
    raise ValueError(f"siliconindex_snapshot_missing:{snapshot_dir}")


def _siliconindex_document(domain: str, snapshot_dir: str | Path) -> dict[str, Any]:
    """Project one immutable SiliconIndex snapshot into the ranking read model."""

    data_dir = _siliconindex_data_dir(snapshot_dir)
    # Do not trust a directory merely because it contains two plausible JSON files.
    # The synchronizer validates before promotion, and this second boundary also
    # protects direct/local snapshot use and detects post-sync corruption.
    validate_snapshot(data_dir)
    brands = json.loads((data_dir / "brands.json").read_text(encoding="utf-8"))
    mentions = json.loads((data_dir / "mentions.json").read_text(encoding="utf-8"))
    if not isinstance(brands, list) or not isinstance(mentions, list):
        raise ValueError("invalid_siliconindex_snapshot_shape")
    metadata: dict[str, Any] = {}
    for filename in ("manifest.json", "snapshot-meta.json"):
        path = data_dir / filename
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                metadata = value
            break
    release_id = str(metadata.get("release_id") or metadata.get("data_version") or "unknown")
    by_brand: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mention in mentions:
        if isinstance(mention, dict):
            by_brand[str(mention.get("brand_id") or "")].append(mention)

    entities: list[dict[str, Any]] = []
    for brand in brands:
        if not isinstance(brand, dict) or brand.get("status") != "active":
            continue
        profiles = [
            value
            for value in brand.get("comparison_profiles", [])
            if isinstance(value, dict) and value.get("domain") == domain
        ]
        if not profiles:
            continue
        if len(profiles) != 1:
            raise ValueError(f"duplicate_siliconindex_profile:{brand.get('brand_id')}")
        profile = profiles[0]
        brand_id = str(brand.get("brand_id") or "").strip()
        canonical = str(brand.get("canonical_name") or "").strip()
        review_status = str(brand.get("review_status") or "pending")
        aliases: list[str] = []
        relationships: dict[str, str] = {}
        alias_identities: dict[str, dict[str, Any]] = {}
        identity_objects = {
            str(value.get("object_id") or ""): value
            for value in brand.get("identity_objects", [])
            if isinstance(value, dict) and value.get("review_status") == "reviewed"
        }
        for mention in sorted(
            by_brand.get(brand_id, []), key=lambda row: str(row.get("mention_id") or "")
        ):
            mention_status = str(mention.get("status") or "pending")
            if review_status == "reviewed" and mention_status != "reviewed":
                continue
            if mention_status not in {"reviewed", "pending"}:
                continue
            if mention.get("match_mode") not in {"exact", "normalized_exact"}:
                # This read model has no answer-context matcher.  Ambiguous and
                # manual-only mentions must stay outside deterministic scoring.
                continue
            text = str(mention.get("text") or "").strip()
            if not text or _key(text) == _key(canonical):
                continue
            if text not in aliases:
                aliases.append(text)
            relationships[text] = str(mention.get("relationship_to_brand") or "") or (
                _RELATIONSHIP_BY_MENTION_TYPE.get(
                    str(mention.get("mention_type") or ""), "official_abbreviation"
                )
            )
            identity_object_id = str(mention.get("identity_object_id") or "")
            if identity_object_id:
                identity_object = identity_objects.get(identity_object_id)
                if identity_object is None:
                    raise ValueError(
                        f"invalid_siliconindex_identity_ref:{mention.get('mention_id')}"
                    )
                alias_identities[text] = {
                    "entity_id": identity_object_id,
                    "canonical_name": str(identity_object.get("canonical_name") or ""),
                    "entity_type": str(identity_object.get("object_type") or ""),
                    "relationship_to_brand": str(
                        identity_object.get("relationship_to_brand") or ""
                    ),
                    "parent_object_id": identity_object.get("parent_object_id"),
                    "evidence_urls": list(identity_object.get("evidence_urls") or []),
                }
        reviewed = review_status == "reviewed"
        # The request read model is deliberately a projection of published
        # knowledge. Pending and draft objects remain available in the source
        # snapshot/governance database, but must not leak into deterministic
        # matching, model catalogs, ranking, or reports.
        if not reviewed:
            continue
        entities.append(
            {
                "entity_id": brand_id,
                "canonical_name": canonical,
                "aliases": aliases,
                "alias_relationships": relationships,
                "alias_identities": alias_identities,
                "entity_type": str(brand.get("entity_type") or "company"),
                "competitor_eligible": bool(profile.get("competitor_eligible")) and reviewed,
                "eligibility_mode": str(profile.get("eligibility_mode") or "always"),
                "brand_level": str(brand.get("brand_level") or "brand"),
                "parent_brand": brand.get("parent_brand_name"),
                "industry_fit": profile.get("industry_fit"),
                "competitor_scopes": profile.get("competitor_scopes") or [],
                "eligibility_note": profile.get("eligibility_note")
                or ("SiliconIndex 实体仍在待审状态，不进入正式竞品榜。" if not reviewed else None),
                "evidence_urls": profile.get("evidence_urls") or [],
                "review_status": review_status,
                "knowledge_status": "published",
                "origin": "siliconindex_snapshot",
                "sync_status": "reconciled",
            }
        )
    if not entities:
        raise ValueError(f"siliconindex_domain_unavailable:{domain}:{release_id}")
    return {
        "schema_version": "entity-master-v3",
        "domain": domain,
        "revision": f"siliconindex:{release_id}:{domain}",
        "aggregation_level": "brand_family",
        "source_system": "siliconindex",
        "source_release_id": release_id,
        "source_content_hash": str(metadata.get("content_hash") or ""),
        "resolution_policy": (
            "实体身份、别名关系、证据和审核状态来自同一 SiliconIndex 发布快照；"
            "项目观测只能产生全局待审候选，不能创建项目级实体真相。"
        ),
        "entities": entities,
    }


def _local_knowledge_document(
    domain: str,
    release_dir: str | Path,
    release_id: str | None = None,
) -> dict[str, Any]:
    store = KnowledgeReleaseStore(release_dir)
    if release_id is None:
        document, manifest, degraded = store.load_domain_resilient("brand/entity-resolution")
    else:
        document, manifest = store.load_domain("brand/entity-resolution", release_id)
        degraded = False
    analysis_domains = document.get("analysis_domains")
    if not isinstance(analysis_domains, dict) or not isinstance(analysis_domains.get(domain), dict):
        raise ValueError(f"local_knowledge_domain_unavailable:{domain}")
    projected = dict(analysis_domains[domain])
    projected.update(
        {
            "source_system": "knowledge_evolution",
            "source_release_id": manifest.get("release_id"),
            "source_content_hash": manifest.get("content_hash"),
            "source_degraded": degraded,
        }
    )
    return projected


def _parse_master(
    document: dict[str, Any],
    *,
    domain: str,
    source_label: str,
    source_mode: str,
    source_error: str = "",
) -> EntityMaster:
    if document.get("domain") != domain or not isinstance(document.get("entities"), list):
        raise ValueError(f"invalid entity master: {source_label}")
    schema_version = str(document.get("schema_version") or "").strip()
    if schema_version not in {"entity-master-v1", "entity-master-v2", "entity-master-v3"}:
        raise ValueError(f"unsupported entity master schema: {source_label}")
    entities: list[EntityRecord] = []
    alias_index: dict[str, EntityRecord] = {}
    relationship_index: dict[str, str] = {}
    identity_index: dict[str, EntityIdentity] = {}
    entity_ids: set[str] = set()
    for raw in document["entities"]:
        if not isinstance(raw, dict):
            raise ValueError(f"invalid entity record: {source_label}")
        canonical = str(raw.get("canonical_name") or "").strip()
        entity_type = str(raw.get("entity_type") or "").strip()
        aliases = tuple(
            dict.fromkeys(
                [canonical]
                + [str(value).strip() for value in raw.get("aliases", []) if str(value).strip()]
            )
        )
        raw_relationships = raw.get("alias_relationships") or {}
        if not isinstance(raw_relationships, dict):
            raise ValueError(f"invalid alias_relationships: {source_label}")
        raw_identities = raw.get("alias_identities") or {}
        if not isinstance(raw_identities, dict):
            raise ValueError(f"invalid alias_identities: {source_label}")
        allowed_relationships = {
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
        }
        alias_keys = {_key(alias) for alias in aliases}
        for alias, relationship in raw_relationships.items():
            if _key(str(alias)) not in alias_keys or relationship not in allowed_relationships:
                raise ValueError(f"invalid alias relationship {alias!r}: {source_label}")
        for alias, identity in raw_identities.items():
            if _key(str(alias)) not in alias_keys or not isinstance(identity, dict):
                raise ValueError(f"invalid alias identity {alias!r}: {source_label}")
        if not canonical or entity_type not in {
            "legal_entity",
            "group",
            "company",
            "brand",
            "brand_family",
            "sub_brand",
            "business_unit",
            "product",
            "tool",
            "institution",
        }:
            raise ValueError(f"invalid entity record: {source_label}")
        entity_id = str(raw.get("entity_id") or f"{domain}:{_key(canonical)}").strip()
        if not entity_id or entity_id in entity_ids:
            raise ValueError(f"duplicate/empty entity_id {entity_id!r}: {source_label}")
        entity_ids.add(entity_id)
        review_status = str(raw.get("review_status") or "reviewed").strip()
        if review_status not in {"reviewed", "pending", "draft"}:
            raise ValueError(f"invalid review_status {review_status!r}: {source_label}")
        eligibility_mode = str(raw.get("eligibility_mode") or "always").strip()
        if eligibility_mode not in {"always", "scope_required", "never"}:
            raise ValueError(f"invalid eligibility_mode {eligibility_mode!r}: {source_label}")
        explicit_knowledge_status = str(raw.get("knowledge_status") or "").strip()
        if explicit_knowledge_status:
            if explicit_knowledge_status not in {
                "published",
                "reviewed_local",
                "model_inferred",
                "unresolved",
            }:
                raise ValueError(
                    f"invalid knowledge_status {explicit_knowledge_status!r}: {source_label}"
                )
            knowledge_status = explicit_knowledge_status
        elif review_status != "reviewed":
            knowledge_status = "unresolved"
        elif source_mode in {
            "synced_siliconindex_snapshot",
            "bundled_siliconindex_projection",
        }:
            knowledge_status = "published"
        else:
            knowledge_status = "reviewed_local"
        record = EntityRecord(
            entity_id=entity_id,
            canonical_name=canonical,
            aliases=aliases,
            entity_type=entity_type,
            competitor_eligible=bool(raw.get("competitor_eligible"))
            and review_status == "reviewed",
            eligibility_mode=eligibility_mode,
            brand_level=str(raw.get("brand_level") or "brand"),
            parent_brand=(str(raw["parent_brand"]).strip() if raw.get("parent_brand") else None),
            industry_fit=(str(raw["industry_fit"]).strip() if raw.get("industry_fit") else None),
            competitor_scopes=tuple(
                str(value).strip()
                for value in raw.get("competitor_scopes", [])
                if str(value).strip()
            ),
            eligibility_note=(
                str(raw["eligibility_note"]).strip() if raw.get("eligibility_note") else None
            ),
            evidence_urls=tuple(
                str(value).strip() for value in raw.get("evidence_urls", []) if str(value).strip()
            ),
            review_status=review_status,
            knowledge_status=knowledge_status,
            origin=str(raw.get("origin") or document.get("source_system") or "unknown"),
            sync_status=str(raw.get("sync_status") or "unknown"),
        )
        if review_status != "reviewed" or knowledge_status == "unresolved":
            continue
        entities.append(record)
        for alias in aliases:
            lookup = _key(alias)
            existing = alias_index.get(lookup)
            if existing is not None and existing != record:
                raise ValueError(f"entity alias conflict: {alias!r}")
            alias_index[lookup] = record
            relationship_index[lookup] = str(
                raw_relationships.get(
                    alias,
                    "self" if lookup == _key(canonical) else "official_abbreviation",
                )
            )
            raw_identity = raw_identities.get(alias)
            if raw_identity is None:
                identity = EntityIdentity(
                    entity_id=record.entity_id,
                    canonical_name=record.canonical_name,
                    entity_type=(
                        record.brand_level
                        if record.brand_level
                        in {"brand", "brand_family", "product", "tool", "institution"}
                        else record.entity_type
                    ),
                )
            else:
                raw_identity_evidence = raw_identity.get("evidence_urls", [])
                if not isinstance(raw_identity_evidence, list) or not all(
                    isinstance(value, str) and value.strip() for value in raw_identity_evidence
                ):
                    raise ValueError(f"invalid alias identity evidence {alias!r}: {source_label}")
                identity = EntityIdentity(
                    entity_id=str(raw_identity.get("entity_id") or "").strip(),
                    canonical_name=str(raw_identity.get("canonical_name") or "").strip(),
                    entity_type=str(raw_identity.get("entity_type") or "").strip(),
                    evidence_urls=tuple(
                        str(value).strip() for value in raw_identity_evidence if str(value).strip()
                    ),
                )
                if (
                    not identity.entity_id
                    or not identity.canonical_name
                    or identity.entity_type
                    not in {
                        "legal_entity",
                        "group",
                        "company",
                        "brand",
                        "brand_family",
                        "sub_brand",
                        "business_unit",
                        "product",
                        "tool",
                        "institution",
                    }
                ):
                    raise ValueError(f"invalid alias identity {alias!r}: {source_label}")
            existing_identity = identity_index.get(lookup)
            if existing_identity is not None and existing_identity != identity:
                raise ValueError(f"entity identity conflict: {alias!r}")
            identity_index[lookup] = identity
    return EntityMaster(
        domain=domain,
        schema_version=schema_version,
        revision=str(document.get("revision") or "").strip(),
        aggregation_level=str(document.get("aggregation_level") or "brand").strip(),
        entities=tuple(entities),
        alias_index=alias_index,
        relationship_index=relationship_index,
        identity_index=identity_index,
        resolution_policy=str(document.get("resolution_policy") or "").strip(),
        source_system=str(document.get("source_system") or "").strip(),
        source_release_id=str(document.get("source_release_id") or "").strip(),
        source_content_hash=str(document.get("source_content_hash") or "").strip(),
        source_mode=source_mode,
        source_error=source_error,
    )


def _snapshot_revision_key(snapshot_dir: str) -> str:
    """Return the publisher revision used to invalidate the in-process read cache."""

    if not snapshot_dir:
        return "not-configured"
    try:
        data_dir = _siliconindex_data_dir(snapshot_dir)
        metadata_path = (
            data_dir / "manifest.json"
            if (data_dir / "manifest.json").is_file()
            else data_dir / "snapshot-meta.json"
        )
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            return "invalid-metadata"
        return ":".join(
            [
                str(metadata.get("release_id") or metadata.get("data_version") or "unknown"),
                str(metadata.get("content_hash") or "no-hash"),
            ]
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        # A missing directory and a subsequently installed CURRENT pointer get
        # different keys, so a long-running API process sees the new release.
        return f"unavailable:{exc}"


def _knowledge_revision_key(release_dir: str, release_id: str | None = None) -> str:
    if not release_dir:
        return "not-configured"
    try:
        store = KnowledgeReleaseStore(release_dir)
        manifest = store.manifest(release_id)
        return f"{manifest.get('release_id', 'unknown')}:{manifest.get('content_hash', 'no-hash')}"
    except (KnowledgeReleaseError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        return f"unavailable:{exc}"


@lru_cache(maxsize=32)
def _load_entity_master_cached(
    domain: str,
    configured_knowledge_release: str,
    requested_release_id: str,
    knowledge_revision_key: str,
    configured_snapshot: str,
    snapshot_revision_key: str,
) -> EntityMaster:
    del knowledge_revision_key, snapshot_revision_key  # cache identity only
    knowledge_error = ""
    if configured_knowledge_release:
        try:
            document = _local_knowledge_document(
                domain,
                configured_knowledge_release,
                requested_release_id or None,
            )
            return _parse_master(
                document,
                domain=domain,
                source_label=configured_knowledge_release,
                source_mode="local_knowledge_release",
                source_error=("last_known_good" if document.get("source_degraded") else ""),
            )
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
            KnowledgeReleaseError,
        ) as exc:
            if requested_release_id:
                raise ValueError(
                    f"requested_knowledge_release_unavailable:{requested_release_id}"
                ) from exc
            knowledge_error = str(exc)
    snapshot_error = ""
    if configured_snapshot:
        try:
            document = _siliconindex_document(domain, configured_snapshot)
            return _parse_master(
                document,
                domain=domain,
                source_label=configured_snapshot,
                source_mode="synced_siliconindex_snapshot",
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            snapshot_error = str(exc)

    path = _DATA_DIR / f"siliconindex_projection_{domain}.json"
    if not path.is_file():
        return _empty_master(domain)
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("source_system") != "siliconindex" and domain == "cybersecurity":
        raise ValueError("cybersecurity bundled entity projection must come from SiliconIndex")
    return _parse_master(
        document,
        domain=domain,
        source_label=str(path),
        source_mode="bundled_siliconindex_projection",
        source_error=";".join(value for value in (knowledge_error, snapshot_error) if value),
    )


def load_entity_master(
    domain: str,
    snapshot_dir: str | None = None,
    knowledge_release_dir: str | None = None,
    release_id: str | None = None,
) -> EntityMaster:
    """Load one global entity projection, preferring the active local knowledge release.

    A validated SiliconIndex snapshot is the second choice.  The generated
    ``siliconindex_projection_<domain>.json`` is the last-known-good fallback;
    it is not an independently maintained project/domain truth source.
    """

    configured_snapshot: str = (
        snapshot_dir or os.environ.get("GEO_SILICONINDEX_SNAPSHOT_DIR", "") or ""
    )
    configured_knowledge_release = (
        knowledge_release_dir or os.environ.get("GEO_KNOWLEDGE_RELEASE_DIR", "") or ""
    )
    return _load_entity_master_cached(
        domain,
        configured_knowledge_release,
        release_id or "",
        _knowledge_revision_key(configured_knowledge_release, release_id),
        configured_snapshot,
        _snapshot_revision_key(configured_snapshot),
    )


def _eligible_for_scopes(record: EntityRecord, comparison_scopes: Iterable[str]) -> bool:
    if not record.competitor_eligible or record.eligibility_mode == "never":
        return False
    if record.eligibility_mode == "always":
        return True
    requested = {str(value).strip().casefold() for value in comparison_scopes if str(value).strip()}
    applicable = {value.casefold() for value in record.competitor_scopes}
    return bool(requested.intersection(applicable))


def _record_projection(
    record: EntityRecord,
    *,
    identity: EntityIdentity,
    relationship: str,
    comparison_scopes: Iterable[str],
) -> dict[str, Any]:
    """Customer-safe, auditable metadata shared by every resolved mention."""

    return {
        "entity_id": record.entity_id,
        "canonical_name": record.canonical_name,
        "entity_type": record.entity_type,
        "identity_entity_id": identity.entity_id,
        "identity_canonical_name": identity.canonical_name,
        "identity_entity_type": identity.entity_type,
        "competitor_eligible": _eligible_for_scopes(record, comparison_scopes),
        "competitor_eligible_base": record.competitor_eligible,
        "eligibility_mode": record.eligibility_mode,
        "brand_level": record.brand_level,
        "parent_brand": record.parent_brand,
        "industry_fit": record.industry_fit,
        "competitor_scopes": list(record.competitor_scopes),
        "eligibility_note": record.eligibility_note,
        "review_status": record.review_status,
        "knowledge_status": record.knowledge_status,
        "origin": record.origin,
        "sync_status": record.sync_status,
        "evidence_urls": list(dict.fromkeys((*identity.evidence_urls, *record.evidence_urls))),
        "relationship_to_canonical": relationship,
    }


def classify_entity(
    value: str,
    *,
    rules: DomainRules,
    master: EntityMaster,
    target_brand: str | None = None,
    named_competitors: Iterable[str] = (),
    comparison_scopes: Iterable[str] = (),
) -> dict[str, Any]:
    """Normalize one extracted value and return an auditable entity row.

    A project-declared target/competitor can seed a global review candidate, but it
    cannot mint a second entity identity or bypass SiliconIndex review.  Unknown and
    project-only names are retained for audit and fail closed out of formal charts.
    """

    raw = str(value or "").strip()
    raw_key = _key(raw)
    record = master.alias_index.get(raw_key)
    project_names = [target_brand, *named_competitors]
    project_lookup = {
        _key(str(name)): str(name).strip() for name in project_names if str(name or "").strip()
    }
    project_canonical = project_lookup.get(raw_key)
    if record is not None:
        identity = master.identity_index.get(
            raw_key,
            EntityIdentity(record.entity_id, record.canonical_name, record.entity_type),
        )
        return {
            "raw_name": raw,
            **_record_projection(
                record,
                identity=identity,
                relationship=master.relationship_index.get(
                    raw_key,
                    "self" if raw_key == _key(record.canonical_name) else "official_abbreviation",
                ),
                comparison_scopes=comparison_scopes,
            ),
            "classification_source": (
                "model_inferred"
                if record.knowledge_status == "model_inferred"
                else (
                    "governed_reviewed"
                    if record.review_status == "reviewed"
                    else "governed_pending"
                )
            ),
        }
    if project_canonical:
        return {
            "raw_name": raw,
            "entity_id": f"unresolved-project:{_key(project_canonical)}",
            "canonical_name": project_canonical,
            "entity_type": "unknown",
            "identity_entity_id": f"unresolved-project:{_key(project_canonical)}",
            "identity_canonical_name": project_canonical,
            "identity_entity_type": "unknown",
            "competitor_eligible": False,
            "competitor_eligible_base": False,
            "eligibility_mode": "never",
            "brand_level": "unclassified",
            "parent_brand": None,
            "industry_fit": None,
            "competitor_scopes": [],
            "eligibility_note": "项目显式名称尚未绑定到已审核 SiliconIndex 实体",
            "review_status": "pending",
            "knowledge_status": "unresolved",
            "relationship_to_canonical": "project_declared",
            "classification_source": "project_unresolved",
        }
    if raw in rules.exclude_terms:
        return {
            "raw_name": raw,
            "entity_id": f"excluded:{raw_key}",
            "canonical_name": raw,
            "entity_type": "institution",
            "identity_entity_id": f"excluded:{raw_key}",
            "identity_canonical_name": raw,
            "identity_entity_type": "institution",
            "competitor_eligible": False,
            "competitor_eligible_base": False,
            "eligibility_mode": "never",
            "brand_level": "institution",
            "parent_brand": None,
            "industry_fit": None,
            "competitor_scopes": [],
            "eligibility_note": "非商业竞品主体",
            "review_status": "reviewed",
            "knowledge_status": "published",
            "relationship_to_canonical": "non_vendor",
            "classification_source": "domain_exclusion_policy",
        }
    return {
        "raw_name": raw,
        "entity_id": f"unclassified:{raw_key}",
        "canonical_name": raw,
        "entity_type": "unknown",
        "identity_entity_id": f"unclassified:{raw_key}",
        "identity_canonical_name": raw,
        "identity_entity_type": "unknown",
        "competitor_eligible": False,
        "competitor_eligible_base": False,
        "eligibility_mode": "never",
        "brand_level": "unclassified",
        "parent_brand": None,
        "industry_fit": None,
        "competitor_scopes": [],
        "eligibility_note": "待语义消歧与人工确认，不进入正式竞品榜",
        "review_status": "pending",
        "knowledge_status": "unresolved",
        "relationship_to_canonical": "unresolved",
        "classification_source": "unclassified",
    }


def normalize_answer_entities(
    values: Iterable[str],
    *,
    rules: DomainRules,
    master: EntityMaster,
    target_brand: str | None = None,
    named_competitors: Iterable[str] = (),
    comparison_scopes: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Classify and de-duplicate canonical entities within one answer, preserving rank.

    The first occurrence defines the canonical entity's answer-level rank.  Later
    aliases of the same entity remain visible in ``raw_aliases`` but never increment
    occurrences or shift another entity's position.
    """

    comparison_scopes = tuple(comparison_scopes)
    output: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}
    for value in values:
        row = classify_entity(
            str(value),
            rules=rules,
            master=master,
            target_brand=target_brand,
            named_competitors=named_competitors,
            comparison_scopes=comparison_scopes,
        )
        canonical = str(row["canonical_name"] or "").strip()
        if not canonical:
            continue
        lookup = _key(canonical)
        existing = by_name.get(lookup)
        if existing is not None:
            if row["raw_name"] not in existing["raw_aliases"]:
                existing["raw_aliases"].append(row["raw_name"])
                existing["raw_relationships"][row["raw_name"]] = row["relationship_to_canonical"]
            continue
        governed = {
            **row,
            "raw_aliases": [row["raw_name"]],
            "raw_relationships": {row["raw_name"]: row["relationship_to_canonical"]},
            "answer_rank": len(output) + 1,
        }
        by_name[lookup] = governed
        output.append(governed)
    return output


def summarize_entity_resolution(
    value_lists: Iterable[Iterable[str]],
    *,
    rules: DomainRules,
    master: EntityMaster,
    target_brand: str | None = None,
    named_competitors: Iterable[str] = (),
    comparison_scopes: Iterable[str] = (),
    pending_limit: int = 30,
) -> dict[str, Any]:
    """Summarize governed resolution without hiding unresolved names.

    Unknown candidates are excluded from the formal competitor ranking, but they
    remain visible here as a bounded review queue.  This prevents both failure
    modes: silently treating every LLM string as a company and silently deleting
    names that still need semantic research.
    """

    comparison_scopes = tuple(comparison_scopes)
    raw_names: set[str] = set()
    canonical_names: set[str] = set()
    type_counts: Counter[str] = Counter()
    excluded_by_type: Counter[str] = Counter()
    pending_mentions: Counter[str] = Counter()
    pending_aliases: dict[str, set[str]] = defaultdict(set)
    pending_status: dict[str, str] = {}
    unclassified_mentions: Counter[str] = Counter()
    mapping_mentions: Counter[str] = Counter()
    mapping_aliases: dict[str, set[str]] = defaultdict(set)
    mapping_relationships: dict[str, set[str]] = defaultdict(set)
    raw_mentions = canonical_mentions = eligible_mentions = alias_collapses = 0

    for values in value_lists:
        raw_values = [str(value).strip() for value in values if str(value).strip()]
        raw_mentions += len(raw_values)
        raw_names.update(raw_values)
        rows = normalize_answer_entities(
            raw_values,
            rules=rules,
            master=master,
            target_brand=target_brand,
            named_competitors=named_competitors,
            comparison_scopes=comparison_scopes,
        )
        canonical_mentions += len(rows)
        alias_collapses += max(0, len(raw_values) - len(rows))
        for row in rows:
            canonical = str(row["canonical_name"])
            aliases = {str(alias) for alias in row.get("raw_aliases", []) if str(alias)}
            canonical_names.add(canonical)
            entity_type = str(row["entity_type"])
            type_counts[entity_type] += 1
            if row["competitor_eligible"]:
                eligible_mentions += 1
            else:
                excluded_by_type[entity_type] += 1
            if entity_type == "unknown":
                unclassified_mentions[canonical] += 1
            if row.get("review_status") != "reviewed" or entity_type == "unknown":
                pending_mentions[canonical] += 1
                pending_aliases[canonical].update(aliases)
                source = str(row.get("classification_source") or "")
                pending_status[canonical] = {
                    "governed_pending": "pending_governance_review",
                    "project_unresolved": "pending_global_entity_binding",
                    "model_inferred": "model_inferred_request_scope",
                }.get(source, "pending_semantic_review")
            if aliases != {canonical}:
                mapping_mentions[canonical] += 1
                mapping_aliases[canonical].update(aliases)
                mapping_relationships[canonical].update(
                    str(value) for value in row.get("raw_relationships", {}).values()
                )

    pending = [
        {
            "observed_name": name,
            "answer_mentions": count,
            "raw_aliases": sorted(pending_aliases[name]),
            "status": pending_status[name],
        }
        for name, count in sorted(pending_mentions.items(), key=lambda item: (-item[1], item[0]))[
            : max(0, pending_limit)
        ]
    ]
    mappings = [
        {
            "canonical_name": name,
            "answer_mentions": mapping_mentions[name],
            "raw_aliases": sorted(mapping_aliases[name]),
            "relationships": sorted(mapping_relationships[name]),
        }
        for name in sorted(mapping_aliases, key=lambda value: (-mapping_mentions[value], value))
    ]
    return {
        "mode": (
            "siliconindex_entity_governance_v1"
            if master.source_system == "siliconindex"
            else "governed_hybrid_v2"
        ),
        "master": {
            "domain": master.domain,
            "schema_version": master.schema_version,
            "revision": master.revision,
            "aggregation_level": master.aggregation_level,
            "source_system": master.source_system or None,
            "source_release_id": master.source_release_id or None,
            "source_content_hash": master.source_content_hash or None,
            "source_mode": master.source_mode or None,
            "source_error": master.source_error or None,
            "comparison_scopes": sorted(
                {str(value).strip() for value in comparison_scopes if str(value).strip()}
            ),
        },
        "counts": {
            "raw_mentions": raw_mentions,
            "canonical_answer_mentions": canonical_mentions,
            "eligible_answer_mentions": eligible_mentions,
            "excluded_answer_mentions": canonical_mentions - eligible_mentions,
            "alias_collapses_within_answers": alias_collapses,
            "distinct_raw_names": len(raw_names),
            "distinct_canonical_entities": len(canonical_names),
            "pending_review_answer_mentions": sum(pending_mentions.values()),
            "pending_review_distinct_names": len(pending_mentions),
            "unclassified_answer_mentions": sum(unclassified_mentions.values()),
            "unclassified_distinct_names": len(unclassified_mentions),
        },
        "entity_type_mentions": dict(sorted(type_counts.items())),
        "excluded_by_type": dict(sorted(excluded_by_type.items())),
        "applied_mappings": mappings,
        "pending_review": pending,
        "pending_review_truncated": len(pending_mentions) > len(pending),
        "resolution_policy": master.resolution_policy,
    }


__all__ = [
    "EntityMaster",
    "EntityRecord",
    "classify_entity",
    "load_entity_master",
    "normalize_answer_entities",
    "summarize_entity_resolution",
]
