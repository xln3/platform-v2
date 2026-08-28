from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def snapshot_set_row(token: str, *, snapshot_count: int = 1) -> dict[str, object]:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    return {
        "pub_id": f"mss_{token}",
        "window_start": start,
        "window_end": start + timedelta(days=1),
        "as_of": start + timedelta(days=2),
        "focal_entity_ids": [f"entity-{token}"],
        "filters": {"model": [], "region": [], "mode": []},
        "filter_hash": digest(f"filter:{token}"),
        "scope_hash": digest(f"scope:{token}"),
        "aggregation_method": "query_macro",
        "design_basis": "planned_cells",
        "query_set_hash": digest(f"queries:{token}"),
        "design_set_hash": digest(f"design:{token}"),
        "dependency_bundle": {
            "canonicalization_version": "canonical-json-v1",
            "engine_version": "test-v1",
        },
        "dependency_bundle_hash": digest(f"dependency:{token}"),
        "state": "ready",
        "failure_codes": [],
        "snapshot_count": snapshot_count,
        "snapshot_set_hash": digest(f"set:{token}"),
    }


def snapshot_row(token: str) -> dict[str, object]:
    zero = Decimal("0")
    one = Decimal("1")
    return {
        "pub_id": f"msn_{token}",
        "focal_entity_id": f"entity-{token}",
        "metric_name": "organic_mention_rate_v2",
        "metric_version": "2.0.0",
        "metric_definition_hash": digest(f"definition:{token}"),
        "state": "ready",
        "state_reason_codes": [],
        "value": one,
        "observed_value": one,
        "answer_weighted_value": one,
        "lower_bound": one,
        "upper_bound": one,
        "semantic_lower_bound": one,
        "semantic_upper_bound": one,
        "weighted_numerator": one,
        "weighted_denominator": one,
        "raw_numerator": one,
        "raw_denominator": one,
        "candidate_answer_count": 1,
        "known_answer_count": 1,
        "unknown_answer_count": 0,
        "decision_abstained_count": 0,
        "decision_review_required_count": 0,
        "not_applicable_answer_count": 0,
        "excluded_answer_count": 0,
        "unique_query_count": 1,
        "design_cell_count": 1,
        "effective_sample_size": one,
        "collection_coverage": one,
        "query_context_coverage": one,
        "semantic_coverage": one,
        "evidence_coverage": one,
        "semantic_coverage_by_capability": {"entity_mention": 1},
        "decision_method_mix": {"deterministic": 1},
        "bootstrap_low": None,
        "bootstrap_high": None,
        "bootstrap_method": None,
        "bootstrap_seed": None,
        "adjudication_sensitivity_low": zero,
        "adjudication_sensitivity_high": zero,
        "calibration_artifact_hashes": [],
        "contribution_set_hash": digest(f"answers:{token}"),
        "query_contribution_set_hash": digest(f"query-contributions:{token}"),
        "design_contribution_set_hash": digest(f"design-contributions:{token}"),
        "snapshot_hash": digest(f"snapshot:{token}"),
    }
