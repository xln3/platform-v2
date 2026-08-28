from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from geo_platform.metrics_v2 import repository as repository_module
from geo_platform.metrics_v2.repository import MetricsV2Repository
from geo_platform.metrics_v2.schemas import ContributionPageView


class _Result:
    def __init__(
        self,
        *,
        one: dict[str, Any] | None = None,
        many: list[dict[str, Any]] | None = None,
    ) -> None:
        self.one = one
        self.many = many or []

    def fetchone(self) -> dict[str, Any] | None:
        return self.one

    def fetchall(self) -> list[dict[str, Any]]:
        return self.many


class _Connection:
    def __init__(self) -> None:
        captured_at = datetime(2026, 8, 27, tzinfo=UTC)
        self.snapshot = {
            "candidate_answer_count": 1,
            "raw_numerator": 1,
            "raw_denominator": 1,
            "weighted_numerator": 1,
            "weighted_denominator": 1,
            "contribution_set_hash": "a" * 64,
        }
        self.contribution = {
            "project_pub_id": "prj_supporting",
            "answer_pub_id": "ans_supporting",
            "query_pub_id": "qry_supporting",
            "query_key": "query-supporting",
            "query_text": "Which brand is substantively mentioned?",
            "analysis_lenses": ["entity_mention"],
            "requested_operations": ["classify"],
            "exposure_role": "focal",
            "model": "fixture-model",
            "region": "cn",
            "mode": "api",
            "capture_time": captured_at,
            "eligibility_status": "included_hit",
            "reason_codes": ["semantic_decision_accepted"],
            "outcome_value": {"hit": True},
            "numerator_contribution": 1,
            "denominator_contribution": 1,
            "query_weight": 1,
            "design_cell_weight": 1,
            "repeat_weight": 1,
            "final_weight": 1,
            "weighted_numerator": 1,
            "weighted_denominator": 1,
            "semantic_manifest_pub_id": "asm_supporting",
            "supporting_event_pub_ids": [],
            "supporting_decision_pub_ids": ["sdr_supporting"],
            "response_text": "The answer substantively mentions the focal brand.",
            "answer_detail_ref": "/answers/ans_supporting",
            "contribution_hash": "b" * 64,
        }
        self.decision = {
            "pub_id": "sdr_supporting",
            "task_name": "substantive-entity-mention",
            "task_version": "2.0.0",
            "method": "deterministic",
            "status": "accepted",
            "result": {"substantive": True, "matched_entity_ids": ["entity-brand"]},
            "calibrated_confidence": "0.990000000000",
            "rubric_hash": "c" * 64,
            "evidence_refs": [{"event_pub_id": "ase_supporting"}],
            "rationale_summary": "The named entity is part of the answer claim.",
            "decision_hash": "d" * 64,
        }

    def execute(self, query: str, parameters: object = None) -> _Result:
        del parameters
        normalized = " ".join(query.split())
        if "SELECT * FROM analytics.metric_snapshot_v2" in normalized:
            return _Result(one=self.snapshot)
        if "SELECT count(*) FROM analytics.metric_contribution_v2" in normalized:
            return _Result(one={"count": 1})
        if "SELECT contribution.*" in normalized:
            return _Result(many=[self.contribution])
        if "FROM analytics.semantic_decision_record_v2" in normalized:
            assert "decision_hash" in normalized
            assert "result" in normalized
            return _Result(many=[self.decision])
        raise AssertionError(f"unexpected SQL: {normalized}")


def test_contribution_projects_decision_identity_and_typed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection()

    @contextmanager
    def fake_tenant_connection(*args: object, **kwargs: object) -> Iterator[_Connection]:
        del args, kwargs
        yield connection

    monkeypatch.setattr(repository_module, "tenant_connection", fake_tenant_connection)

    document = MetricsV2Repository("postgresql://not-opened").list_contributions(
        tenant_pub_id="tnt_supporting",
        snapshot_pub_id="msn_supporting",
    )
    page = ContributionPageView.model_validate(document)
    supporting = page.data[0].supporting_decisions[0]

    assert supporting.decision_hash == "d" * 64
    assert supporting.result == {
        "substantive": True,
        "matched_entity_ids": ["entity-brand"],
    }
