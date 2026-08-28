from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from domain.metrics.v2.canonical_hash import (
    CanonicalizationError,
    canonical_hash,
    canonical_json,
    canonical_set_hash,
)
from domain.metrics.v2.models import EligibilityStatus, MetricEvaluation


def _evaluation() -> MetricEvaluation:
    return MetricEvaluation(
        answer_pub_id="ans_中文_😀",
        query_key="qry_1",
        focal_entity_id="brand_sbang",
        metric_name="fixture_rate_v2",
        metric_version="2.0.0",
        metric_definition_hash="a" * 64,
        eligibility_status=EligibilityStatus.INCLUDED_HIT,
        reason_codes=("recommendation_positive",),
        outcome_value=True,
        numerator_contribution=Decimal("1.000000000000"),
        denominator_contribution=Decimal("1"),
        supporting_event_pub_ids=("evt_1",),
        supporting_decision_pub_ids=("dec_1",),
    )


def test_canonical_json_v1_normalizes_order_time_and_data_types() -> None:
    left = {
        "decimal": Decimal("1.2300"),
        "time": datetime(2026, 8, 27, 8, tzinfo=timezone(timedelta(hours=8))),
        "set": {"中文", "😀"},
        "nested": {"b": 2, "a": 1},
    }
    right = {
        "nested": {"a": 1, "b": 2},
        "set": {"😀", "中文"},
        "time": datetime(2026, 8, 27, 0, tzinfo=UTC),
        "decimal": Decimal("1.2300"),
    }
    assert canonical_json(left) == canonical_json(right)
    assert canonical_hash(left) == canonical_hash(right)
    assert '"decimal":"1.2300"' in canonical_json(left)
    assert '"time":"2026-08-27T00:00:00.000000Z"' in canonical_json(left)


def test_canonical_set_hash_is_independent_of_database_read_order() -> None:
    values = [_evaluation(), replace(_evaluation(), answer_pub_id="ans_2")]
    assert canonical_set_hash(values) == canonical_set_hash(reversed(values))


@pytest.mark.parametrize(
    "mutation",
    [
        {"reason_codes": ("recommendation_conditional_positive",)},
        {"supporting_event_pub_ids": ("evt_2",)},
        {"supporting_decision_pub_ids": ("dec_2",)},
        {"numerator_contribution": Decimal("0")},
    ],
)
def test_every_audited_contribution_dependency_changes_hash(
    mutation: dict[str, object],
) -> None:
    original = _evaluation()
    assert canonical_hash(original) != canonical_hash(replace(original, **mutation))


def test_unsupported_or_ambiguous_runtime_values_fail_closed() -> None:
    with pytest.raises(CanonicalizationError, match="floats"):
        canonical_json({"value": 0.1})
    with pytest.raises(CanonicalizationError, match="timezone-aware"):
        canonical_json({"at": datetime(2026, 8, 27)})


def test_unicode_and_emoji_strings_are_utf8_not_ascii_escaped() -> None:
    rendered = canonical_json({"evidence": "盛邦安全 😀 e\u0301"})
    assert "盛邦安全" in rendered and "😀" in rendered and "e\u0301" in rendered
