"""Authoritative Service 2 all-U relation and evidence contract.

This module deliberately does not reinterpret the legacy boolean
``platform.disparagement_judgment`` rows.  New Service 2 processing uses a
versioned entity-relation taxonomy, keeps statement/exposure ledgers separate,
and validates every customer-facing quote against an immutable page snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import Any
from urllib.parse import urlsplit

CORPUS_POLICY_VERSION = "service2-all-u-occurrence-v1"
JUDGMENT_POLICY_VERSION = "service2-entity-relation-v1"
FACT_SCHEMA_VERSION = "formal-service2-source-corpus-v2"


class Ledger(StrEnum):
    """A is a quotable statement; B is exposure context and is not disparagement."""

    STATEMENT = "statement"
    EXPOSURE = "exposure"


class DisparagementLevel(StrEnum):
    L0 = "L0"
    L1 = "L1"
    L2A = "L2a"
    L2B = "L2b"
    L3A = "L3a"
    L3B = "L3b"
    L4 = "L4"


class RelationDirection(StrEnum):
    TARGET_NEGATIVE = "target_negative"
    TARGET_DEGRADED = "target_degraded"
    TARGET_COMPARED = "target_compared"
    TARGET_OMITTED = "target_omitted"
    CONTEXT_ONLY = "context_only"


class FactAnchorState(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    DISPUTED = "disputed"
    NOT_APPLICABLE = "not_applicable"


class AttributionConfidence(StrEnum):
    VERIFIED = "verified"
    PROBABLE = "probable"
    WEAK = "weak"
    UNKNOWN = "unknown"


class ValidationStatus(StrEnum):
    EXACT = "exact"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"
    EXPERIMENTAL = "experimental"


class VisualValidationStatus(StrEnum):
    VERIFIED = "verified"
    UNAVAILABLE = "unavailable"
    MISMATCH = "mismatch"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True, slots=True)
class OrthogonalFlags:
    comparison_present: bool = False
    peer_elevated: bool = False
    scope_narrowed: bool = False
    industry_wide: bool = False
    direct_target_negative: bool = False
    secondary_position: bool = False
    comparison_manipulated: bool = False
    key_fact_omitted: bool = False


@dataclass(frozen=True, slots=True)
class RelationFindingCandidate:
    ledger: Ledger
    level: DisparagementLevel
    relation_direction: RelationDirection
    textual_speaker: str
    target_entity: str
    beneficiary_entity: str | None
    quote: str
    quote_start: int | None
    quote_end: int | None
    context: str
    context_start: int | None
    context_end: int | None
    snapshot_text_sha256: str
    is_disparagement: bool
    fact_anchor_state: FactAnchorState
    flags: OrthogonalFlags
    comparison_dimensions: tuple[str, ...] = ()
    omitted_facts: tuple[str, ...] = ()
    publisher_party: str | None = None
    publisher_confidence: AttributionConfidence = AttributionConfidence.UNKNOWN
    publisher_evidence: tuple[dict[str, Any], ...] = ()
    commissioner_party: str | None = None
    commissioner_confidence: AttributionConfidence = AttributionConfidence.UNKNOWN
    commissioner_evidence: tuple[dict[str, Any], ...] = ()

    @property
    def quote_sha256(self) -> str | None:
        return sha256(self.quote.encode("utf-8")).hexdigest() if self.quote else None


def _clean_required(value: str, field: str, failures: list[str]) -> None:
    if not value.strip():
        failures.append(f"{field}_required")


def has_reviewable_evidence(evidence: tuple[dict[str, Any], ...]) -> bool:
    """Accept only a stable public reference or an HTTP(S) source, never a marker dict."""

    public_reference_keys = (
        "evidence_pub_id",
        "source_pub_id",
        "document_pub_id",
        "account_pub_id",
        "approval_pub_id",
    )
    for row in evidence:
        if any(
            isinstance(row.get(key), str) and bool(str(row[key]).strip())
            for key in public_reference_keys
        ):
            return True
        for key in ("url", "source_url"):
            value = row.get(key)
            if isinstance(value, str):
                try:
                    parsed = urlsplit(value.strip())
                    if parsed.scheme.lower() in {"http", "https"} and parsed.hostname:
                        return True
                except ValueError:
                    continue
    return False


def validate_relation_finding(
    candidate: RelationFindingCandidate,
    *,
    source_text: str,
    snapshot_text_sha256: str,
) -> tuple[str, ...]:
    """Fail-closed validation for a versioned Service 2 relation finding.

    Offsets are Python/Unicode character offsets.  A caller that starts from
    byte offsets must convert them before constructing the candidate.
    """

    failures: list[str] = []
    actual_snapshot_hash = sha256(source_text.encode("utf-8")).hexdigest()
    if snapshot_text_sha256 != actual_snapshot_hash:
        failures.append("snapshot_text_hash_mismatch")
    if candidate.snapshot_text_sha256 != snapshot_text_sha256:
        failures.append("finding_snapshot_hash_mismatch")

    _clean_required(candidate.target_entity, "target_entity", failures)
    _clean_required(candidate.textual_speaker, "textual_speaker", failures)
    if candidate.ledger is Ledger.STATEMENT:
        _clean_required(candidate.quote, "quote", failures)
        if candidate.quote_start is None or candidate.quote_end is None:
            failures.append("quote_offsets_required")
        elif not (0 <= candidate.quote_start < candidate.quote_end <= len(source_text)):
            failures.append("quote_offsets_out_of_range")
        elif source_text[candidate.quote_start : candidate.quote_end] != candidate.quote:
            failures.append("quote_not_exact_snapshot_substring")
        if candidate.context_start is None or candidate.context_end is None:
            failures.append("context_offsets_required")
        elif not (0 <= candidate.context_start < candidate.context_end <= len(source_text)):
            failures.append("context_offsets_out_of_range")
        elif source_text[candidate.context_start : candidate.context_end] != candidate.context:
            failures.append("context_not_exact_snapshot_substring")
        elif (
            candidate.quote_start is not None
            and candidate.quote_end is not None
            and not (
                candidate.context_start <= candidate.quote_start
                and candidate.quote_end <= candidate.context_end
            )
        ):
            failures.append("context_does_not_contain_quote")
    else:
        if candidate.is_disparagement:
            failures.append("exposure_ledger_cannot_be_disparagement")
        if candidate.level is not DisparagementLevel.L0:
            failures.append("exposure_ledger_level_must_be_l0")
        if candidate.relation_direction is not RelationDirection.CONTEXT_ONLY:
            failures.append("exposure_ledger_direction_must_be_context_only")

    if candidate.level in {DisparagementLevel.L0, DisparagementLevel.L1}:
        if candidate.is_disparagement:
            failures.append("l0_l1_cannot_be_disparagement")
    elif candidate.ledger is not Ledger.STATEMENT:
        failures.append("disparagement_level_requires_statement_ledger")
    elif not candidate.is_disparagement:
        failures.append("l2_plus_requires_disparagement_true")

    flags = candidate.flags
    if candidate.level is DisparagementLevel.L2A:
        if not flags.direct_target_negative:
            failures.append("l2a_requires_direct_target_negative")
        if candidate.relation_direction is not RelationDirection.TARGET_NEGATIVE:
            failures.append("l2a_requires_target_negative_direction")
    if candidate.level is DisparagementLevel.L2B:
        if not flags.secondary_position:
            failures.append("l2b_requires_secondary_position")
        if candidate.fact_anchor_state is not FactAnchorState.ABSENT:
            failures.append("l2b_requires_missing_fact_anchor")
        if candidate.relation_direction is not RelationDirection.TARGET_DEGRADED:
            failures.append("l2b_requires_target_degraded_direction")
    if candidate.level is DisparagementLevel.L3A:
        if not (flags.comparison_present and flags.comparison_manipulated):
            failures.append("l3a_requires_manipulated_comparison")
        if not any(value.strip() for value in candidate.comparison_dimensions):
            failures.append("l3a_requires_comparison_dimensions")
        if candidate.relation_direction is not RelationDirection.TARGET_COMPARED:
            failures.append("l3a_requires_target_compared_direction")
    if candidate.level is DisparagementLevel.L3B:
        if not flags.key_fact_omitted:
            failures.append("l3b_requires_key_fact_omission")
        if not any(value.strip() for value in candidate.omitted_facts):
            failures.append("l3b_requires_omitted_facts")
        if candidate.relation_direction is not RelationDirection.TARGET_OMITTED:
            failures.append("l3b_requires_target_omitted_direction")
    if candidate.level is DisparagementLevel.L4:
        # The repository only retains the summary label "defamatory assertion";
        # the referenced authoritative L4 element mapping is not present.  Keep
        # the wire/database value reserved, but never invent an acceptance rule.
        failures.append("l4_authoritative_taxonomy_mapping_unavailable")

    substantive_target_behavior = any(
        (
            flags.direct_target_negative,
            flags.secondary_position,
            flags.comparison_manipulated,
            flags.key_fact_omitted,
        )
    )
    if (
        candidate.level not in {DisparagementLevel.L0, DisparagementLevel.L1}
        and not substantive_target_behavior
    ):
        failures.append("peer_elevation_or_observation_flag_alone_is_not_disparagement")
    if flags.scope_narrowed and not substantive_target_behavior and candidate.is_disparagement:
        failures.append("scope_narrowed_alone_is_not_disparagement")
    if flags.industry_wide and not substantive_target_behavior and candidate.is_disparagement:
        failures.append("industry_wide_alone_is_not_disparagement")

    for party, confidence, evidence, prefix in (
        (
            candidate.publisher_party,
            candidate.publisher_confidence,
            candidate.publisher_evidence,
            "publisher",
        ),
        (
            candidate.commissioner_party,
            candidate.commissioner_confidence,
            candidate.commissioner_evidence,
            "commissioner",
        ),
    ):
        if confidence is AttributionConfidence.UNKNOWN and party:
            failures.append(f"{prefix}_party_requires_attribution_evidence")
        if confidence is not AttributionConfidence.UNKNOWN and not has_reviewable_evidence(
            evidence
        ):
            failures.append(f"{prefix}_{confidence}_requires_reviewable_evidence")

    return tuple(dict.fromkeys(failures))


def customer_case_eligible(
    *,
    ledger: Ledger,
    level: DisparagementLevel,
    validation_status: ValidationStatus,
    visual_status: VisualValidationStatus,
    review_state: str,
    factcheck_verdict: str | None,
    factcheck_evidence: tuple[dict[str, Any], ...],
    factcheck_boundary: str | None,
) -> bool:
    """Customer cases require text, visual, fact-check and review evidence gates."""

    return (
        ledger is Ledger.STATEMENT
        and level is not DisparagementLevel.L0
        and validation_status is ValidationStatus.EXACT
        and visual_status is VisualValidationStatus.VERIFIED
        and review_state == "accepted"
        and factcheck_case_ready(
            verdict=factcheck_verdict,
            evidence=factcheck_evidence,
            boundary=factcheck_boundary,
        )
    )


def factcheck_case_ready(
    *, verdict: str | None, evidence: tuple[dict[str, Any], ...], boundary: str | None
) -> bool:
    """Require either reviewable support or an explicit unverifiable boundary."""

    if verdict == "unverifiable":
        return bool(boundary and boundary.strip())
    if verdict in {"supported", "refuted", "mixed"}:
        return has_reviewable_evidence(evidence)
    return False


def attribution_wording_allowed(
    confidence: AttributionConfidence, evidence: tuple[dict[str, Any], ...]
) -> bool:
    """Party-specific wording is allowed only behind a reviewable evidence gate."""

    return confidence in {
        AttributionConfidence.VERIFIED,
        AttributionConfidence.PROBABLE,
    } and has_reviewable_evidence(evidence)


def validated_visual_bbox(
    value: object, *, image_width: int, image_height: int
) -> tuple[float, float, float, float] | None:
    """Return a source-coordinate bbox only when it is inside the real image."""

    if (
        not isinstance(value, dict)
        or image_width <= 0
        or image_height <= 0
        or image_width > 100_000
        or image_height > 100_000
    ):
        return None
    raw = tuple(value.get(name) for name in ("x", "y", "width", "height"))
    numbers: list[float] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int | float):
            return None
        numbers.append(float(item))
    x, y, width, height = numbers
    if not all(isfinite(item) for item in (x, y, width, height)):
        return None
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        return None
    if x + width > image_width or y + height > image_height:
        return None
    return x, y, width, height


def visual_anchor_matches_quote(
    *,
    anchor_quote_hash: object,
    anchor_text_start: object,
    anchor_text_end: object,
    quote_hash: str | None,
    quote_start: int | None,
    quote_end: int | None,
) -> bool:
    """Bind a visual box to the exact repeated-text occurrence, not hash alone."""

    return (
        isinstance(anchor_quote_hash, str)
        and quote_hash is not None
        and anchor_quote_hash == quote_hash
        and isinstance(anchor_text_start, int)
        and not isinstance(anchor_text_start, bool)
        and isinstance(anchor_text_end, int)
        and not isinstance(anchor_text_end, bool)
        and anchor_text_start == quote_start
        and anchor_text_end == quote_end
    )


__all__ = [
    "AttributionConfidence",
    "CORPUS_POLICY_VERSION",
    "DisparagementLevel",
    "FACT_SCHEMA_VERSION",
    "FactAnchorState",
    "JUDGMENT_POLICY_VERSION",
    "Ledger",
    "OrthogonalFlags",
    "RelationDirection",
    "RelationFindingCandidate",
    "ValidationStatus",
    "VisualValidationStatus",
    "attribution_wording_allowed",
    "customer_case_eligible",
    "factcheck_case_ready",
    "has_reviewable_evidence",
    "validated_visual_bbox",
    "validate_relation_finding",
    "visual_anchor_matches_quote",
]
