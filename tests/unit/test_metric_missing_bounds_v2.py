from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from domain.metrics.v2.definition_loader import load_definitions
from domain.metrics.v2.models import (
    DecisionStatus,
    EligibilityStatus,
    EvaluationInput,
    MetricEvaluation,
    MetricSnapshotState,
    SemanticCapabilityStatus,
    SemanticDecisionFact,
)
from domain.metrics.v2.query_context import (
    AnalysisLens,
    BrandStructureType,
    ClassificationState,
    ExposureRole,
    QueryContextFact,
    RequestedOperation,
    hash_query_text,
)
from domain.metrics.v2.snapshot_engine import MetricSnapshotEngine, SnapshotBuildRequest
from domain.metrics.v2.weighting import (
    WeightingInput,
    assign_query_macro_weights,
    calculate_missing_bounds,
)


def _evaluation(index: int, status: EligibilityStatus) -> MetricEvaluation:
    hit = index < 2
    return MetricEvaluation(
        answer_pub_id=f"ans_{index}",
        query_key=f"qry_{index}",
        focal_entity_id="brand_sbang",
        metric_name="fixture_rate_v2",
        metric_version="2.0.0",
        metric_definition_hash="a" * 64,
        eligibility_status=status,
        reason_codes=(
            "semantic_analysis_failed"
            if status is EligibilityStatus.ANALYSIS_UNKNOWN
            else "included",
        ),
        outcome_value=hit if status is not EligibilityStatus.ANALYSIS_UNKNOWN else None,
        numerator_contribution=(
            Decimal(int(hit)) if status is not EligibilityStatus.ANALYSIS_UNKNOWN else Decimal("0")
        ),
        denominator_contribution=(
            Decimal("1") if status is not EligibilityStatus.ANALYSIS_UNKNOWN else Decimal("0")
        ),
    )


def test_four_query_unknown_fixture_has_separate_point_and_missing_bounds() -> None:
    evaluations = tuple(
        _evaluation(
            index,
            EligibilityStatus.ANALYSIS_UNKNOWN
            if index == 3
            else EligibilityStatus.INCLUDED_HIT
            if index < 2
            else EligibilityStatus.INCLUDED_MISS,
        )
        for index in range(4)
    )
    weighted = assign_query_macro_weights(
        WeightingInput(item, design_cell_key=f"cell_{item.query_key}") for item in evaluations
    )
    bounds = calculate_missing_bounds(weighted)
    assert bounds.observed_value == Decimal(2) / Decimal(3)
    assert bounds.coverage == Decimal("0.75")
    assert bounds.lower_bound == Decimal("0.5")
    assert bounds.upper_bound == Decimal("0.75")


def _subject(index: int, *, failed: bool) -> EvaluationInput:
    query = QueryContextFact(
        query_key=f"qry_{index}",
        query_text_hash=hash_query_text(f"query {index}"),
        analysis_lenses=frozenset({AnalysisLens.AI_RECOMMENDATION}),
        requested_operations=frozenset({RequestedOperation.RECOMMEND}),
        detected_entity_ids=frozenset(),
        brand_structure_type=BrandStructureType.BRAND_NEUTRAL,
        classification_state=ClassificationState.READY,
        classifier_version="v2",
        decision_task_bundle_hash="a" * 64,
        entity_dictionary_hash="b" * 64,
    )
    task = "substantive-entity-mention@2.0.0"
    events = ()
    if index < 2:
        events = (
            {
                "event_pub_id": f"evt_{index}",
                "event_type": "entity_mention",
                "subject_entity_id": "brand_sbang",
                "event_value": {"substantive": True, "mention_role": "asserted_body"},
            },
        )
    return EvaluationInput(
        answer_pub_id=f"ans_{index}",
        query_context=query,
        focal_entity_id="brand_sbang",
        exposure_role=ExposureRole.BRAND_NEUTRAL,
        capability_statuses={
            "entity_mention": (
                SemanticCapabilityStatus.FAILED if failed else SemanticCapabilityStatus.READY
            )
        },
        events=events,
        decisions=(
            {}
            if failed
            else {
                task: SemanticDecisionFact(
                    task_ref=task,
                    status=DecisionStatus.ACCEPTED,
                    value={"substantive": index < 2},
                    decision_pub_id=f"dec_{index}",
                    calibrated=True,
                )
            }
        ),
    )


def test_snapshot_gate_nulls_formal_value_but_preserves_observed_and_unknown_trace() -> None:
    definition = load_definitions().get("ai_recommendation_organic_mention_rate_v2", "2.0.0")
    request = SnapshotBuildRequest(
        definitions=(definition,),
        subjects=tuple(_subject(index, failed=index == 3) for index in range(4)),
        as_of=datetime(2026, 8, 27, tzinfo=UTC),
        scope={"tenant": "ten_1", "project": "prj_1"},
        dependency_bundle={"answer_set_hash": "a" * 64},
        minimum_queries_for_ready=1,
    )
    snapshot_set = MetricSnapshotEngine().build_set(request)
    snapshot = snapshot_set.snapshots[0]
    assert snapshot.state is MetricSnapshotState.INSUFFICIENT
    assert snapshot.value is None
    assert snapshot.observed_value == Decimal(2) / Decimal(3)
    assert snapshot.lower_bound == Decimal("0.5")
    assert snapshot.upper_bound == Decimal("0.75")
    assert snapshot.unknown_answer_count == 1
    assert len(snapshot_set.answer_contributions) == 4
    assert len(snapshot_set.query_contributions) == 4
    assert len(snapshot_set.design_cell_contributions) == 4
    assert all(item.contribution_hash for item in snapshot_set.answer_contributions)


def test_snapshot_set_and_all_three_contribution_levels_are_input_order_invariant() -> None:
    definition = load_definitions().get("ai_recommendation_organic_mention_rate_v2", "2.0.0")
    subjects = tuple(_subject(index, failed=index == 3) for index in range(4))
    common = {
        "definitions": (definition,),
        "as_of": datetime(2026, 8, 27, tzinfo=UTC),
        "scope": {"tenant": "ten_1", "project": "prj_1"},
        "dependency_bundle": {"answer_set_hash": "a" * 64},
        "minimum_queries_for_ready": 1,
    }
    first = MetricSnapshotEngine().build_set(SnapshotBuildRequest(subjects=subjects, **common))
    reversed_result = MetricSnapshotEngine().build_set(
        SnapshotBuildRequest(subjects=tuple(reversed(subjects)), **common)
    )
    assert first.snapshot_set_hash == reversed_result.snapshot_set_hash
    assert first.snapshots == reversed_result.snapshots
    assert first.answer_contributions == reversed_result.answer_contributions
    assert first.query_contributions == reversed_result.query_contributions
    assert first.design_cell_contributions == reversed_result.design_cell_contributions
