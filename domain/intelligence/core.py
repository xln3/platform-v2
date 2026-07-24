from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256


class EvidenceRelation(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True, slots=True)
class ClaimCandidate:
    text: str
    start: int
    end: int
    normalized_hash: str


@dataclass(frozen=True, slots=True)
class SourceAssessment:
    source_pub_id: str
    source_cluster: str
    relation: EvidenceRelation
    independence_weight: Decimal
    access_class: str = "public"


@dataclass(frozen=True, slots=True)
class DetectionResult:
    probability: Decimal
    evidence_sufficiency: Decimal
    independent_source_count: int
    uncertainty: Decimal
    rule_version: str
    model_version: str
    explanation: tuple[str, ...]
    requires_human_verdict: bool = True


def extract_claims(text: str) -> tuple[ClaimCandidate, ...]:
    claims: list[ClaimCandidate] = []
    for match in re.finditer(r"[^。！？!?\n]+[。！？!?]?", text):
        candidate = match.group().strip()
        if len(candidate) < 8:
            continue
        normalized = re.sub(r"\s+", " ", candidate).casefold()
        claims.append(
            ClaimCandidate(
                text=candidate,
                start=match.start(),
                end=match.end(),
                normalized_hash=sha256(normalized.encode()).hexdigest(),
            )
        )
    return tuple(claims)


def canonical_cluster(body_hash: str, semantic_fingerprint: tuple[int, ...]) -> str:
    material = f"{body_hash}:{','.join(map(str, semantic_fingerprint))}"
    return sha256(material.encode()).hexdigest()[:24]


def score_investigation(
    *,
    assessments: Iterable[SourceAssessment],
    content_feature_score: Decimal,
    propagation_feature_score: Decimal,
    circular_citation_risk: Decimal,
    rule_version: str = "anti-geo-rules-v1",
    model_version: str = "rules-only-experimental-v1",
) -> DetectionResult:
    items = tuple(assessments)
    public_items = tuple(item for item in items if item.access_class == "public")
    independent_clusters = {
        item.source_cluster
        for item in public_items
        if item.relation is not EvidenceRelation.INSUFFICIENT
    }
    support = sum(
        (
            item.independence_weight
            for item in public_items
            if item.relation is EvidenceRelation.SUPPORTS
        ),
        start=Decimal("0"),
    )
    contradict = sum(
        (
            item.independence_weight
            for item in public_items
            if item.relation is EvidenceRelation.CONTRADICTS
        ),
        start=Decimal("0"),
    )
    evidence_mass = support + contradict
    evidence_sufficiency = min(
        Decimal("1"), evidence_mass / Decimal("3") if evidence_mass else Decimal("0")
    )
    # Rules are deliberately conservative: no single post/source can cross a strong conclusion
    # boundary, and source circularity reduces rather than increases confidence.
    independence_factor = min(Decimal("1"), Decimal(len(independent_clusters)) / Decimal("3"))
    raw = (
        content_feature_score * Decimal("0.30")
        + propagation_feature_score * Decimal("0.35")
        + independence_factor * Decimal("0.20")
        + (Decimal("1") - circular_citation_risk) * Decimal("0.15")
    )
    if len(independent_clusters) < 2:
        raw = min(raw, Decimal("0.49"))
    contradiction_ratio = contradict / evidence_mass if evidence_mass else Decimal("0")
    probability = max(Decimal("0"), min(Decimal("1"), raw - contradiction_ratio * Decimal("0.3")))
    uncertainty = Decimal(str(1 / (1 + math.exp(float(evidence_mass - Decimal("1.5"))))))
    reasons = (
        f"content feature contribution={content_feature_score}",
        f"propagation feature contribution={propagation_feature_score}",
        f"independent public source clusters={len(independent_clusters)}",
        f"circular citation risk={circular_citation_risk}",
        "probability is advisory and requires a human verdict",
    )
    return DetectionResult(
        probability=probability.quantize(Decimal("0.000001")),
        evidence_sufficiency=evidence_sufficiency.quantize(Decimal("0.000001")),
        independent_source_count=len(independent_clusters),
        uncertainty=uncertainty.quantize(Decimal("0.000001")),
        rule_version=rule_version,
        model_version=model_version,
        explanation=reasons,
    )


def assert_cluster_split(train_cluster_ids: Iterable[str], test_cluster_ids: Iterable[str]) -> None:
    overlap = set(train_cluster_ids) & set(test_cluster_ids)
    if overlap:
        raise ValueError(f"propagation cluster leakage detected: {sorted(overlap)}")
