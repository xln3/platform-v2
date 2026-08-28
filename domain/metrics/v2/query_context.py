"""Query-context facts and focal-entity-relative exposure derivation."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

_WHITESPACE = re.compile(r"\s+")


class AnalysisLens(StrEnum):
    AI_IMPRESSION = "ai_impression"
    AI_RECOMMENDATION = "ai_recommendation"


class RequestedOperation(StrEnum):
    DESCRIBE = "describe"
    FACT_LOOKUP = "fact_lookup"
    EVALUATE = "evaluate"
    RECOMMEND = "recommend"
    COMPARE = "compare"
    RANK = "rank"
    EXPLAIN = "explain"


class BrandStructureType(StrEnum):
    BRAND_NEUTRAL = "brand_neutral"
    SINGLE_BRAND_NAMED = "single_brand_named"
    MULTI_BRAND_NAMED = "multi_brand_named"
    UNKNOWN = "unknown"


class ExposureRole(StrEnum):
    BRAND_NEUTRAL = "brand_neutral"
    FOCAL_NAMED_ONLY = "focal_named_only"
    FOCAL_NAMED_WITH_OTHERS = "focal_named_with_others"
    OTHER_BRAND_NAMED = "other_brand_named"
    UNKNOWN = "unknown"


class ClassificationState(StrEnum):
    READY = "ready"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"


class ClassificationSource(StrEnum):
    LIVE = "live"
    HISTORICAL_BACKFILL = "historical_backfill"
    MANUAL_OVERRIDE = "manual_override"


class DerivationMethod(StrEnum):
    DETERMINISTIC = "deterministic"
    MODEL = "model"
    HYBRID = "hybrid"
    HUMAN = "human"


def normalize_query_text(text: str) -> str:
    """NFKC-normalize and collapse whitespace without changing stored source text."""

    if not isinstance(text, str):
        raise TypeError("query text must be a string")
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def hash_query_text(text: str) -> str:
    """Hash the persisted original query text, not its matching projection."""

    if not isinstance(text, str):
        raise TypeError("query text must be a string")
    return sha256(text.encode("utf-8")).hexdigest()


def hash_normalized_query_text(text: str) -> str:
    return sha256(normalize_query_text(text).encode("utf-8")).hexdigest()


def derive_query_key(
    *,
    tenant_pub_id: str,
    project_pub_id: str,
    query_text: str,
    query_pub_id: str | None = None,
) -> str:
    """Derive the immutable query key prescribed by section 21.1."""

    if query_pub_id:
        return query_pub_id
    material = tenant_pub_id + project_pub_id + normalize_query_text(query_text)
    return "legacy:" + sha256(material.encode("utf-8")).hexdigest()


def derive_brand_structure(
    detected_entity_ids: frozenset[str] | set[str] | tuple[str, ...],
    *,
    has_unresolved_brand_surface: bool = False,
) -> BrandStructureType:
    entities = set(detected_entity_ids)
    if has_unresolved_brand_surface:
        return BrandStructureType.UNKNOWN
    if not entities:
        return BrandStructureType.BRAND_NEUTRAL
    if len(entities) == 1:
        return BrandStructureType.SINGLE_BRAND_NAMED
    return BrandStructureType.MULTI_BRAND_NAMED


def derive_exposure_role(
    detected_entity_ids: frozenset[str] | set[str] | tuple[str, ...],
    focal_entity_id: str,
    *,
    has_unresolved_brand_surface: bool = False,
) -> ExposureRole:
    entities = set(detected_entity_ids)
    if has_unresolved_brand_surface:
        return ExposureRole.UNKNOWN
    if not entities:
        return ExposureRole.BRAND_NEUTRAL
    if focal_entity_id not in entities:
        return ExposureRole.OTHER_BRAND_NAMED
    if len(entities) == 1:
        return ExposureRole.FOCAL_NAMED_ONLY
    return ExposureRole.FOCAL_NAMED_WITH_OTHERS


@dataclass(frozen=True, slots=True)
class QueryContextFact:
    query_key: str
    query_text_hash: str
    analysis_lenses: frozenset[AnalysisLens]
    requested_operations: frozenset[RequestedOperation]
    detected_entity_ids: frozenset[str]
    brand_structure_type: BrandStructureType
    classification_state: ClassificationState
    classifier_version: str
    decision_task_bundle_hash: str
    entity_dictionary_hash: str
    primary_lens: AnalysisLens | None = None
    query_subtypes: tuple[str, ...] = ()
    classification_source: ClassificationSource = ClassificationSource.LIVE
    derivation_method: DerivationMethod = DerivationMethod.HYBRID
    review_status: str = "not_required"
    override_reason: str | None = None
    decision_record_pub_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.query_text_hash) != 64:
            raise ValueError("query_text_hash must be a SHA-256 hex digest")
        try:
            int(self.query_text_hash, 16)
        except ValueError as exc:
            raise ValueError("query_text_hash must be a SHA-256 hex digest") from exc
        if self.classification_state is ClassificationState.READY:
            if not self.analysis_lenses:
                raise ValueError("ready query context requires at least one analysis lens")
            if not self.requested_operations:
                raise ValueError("ready query context requires at least one requested operation")
            expected = derive_brand_structure(self.detected_entity_ids)
            if self.brand_structure_type is not expected:
                raise ValueError("brand_structure_type contradicts detected entities")

    def exposure_for(
        self,
        focal_entity_id: str,
        *,
        has_unresolved_brand_surface: bool = False,
    ) -> ExposureRole:
        if self.classification_state is not ClassificationState.READY:
            return ExposureRole.UNKNOWN
        return derive_exposure_role(
            self.detected_entity_ids,
            focal_entity_id,
            has_unresolved_brand_surface=has_unresolved_brand_surface,
        )
