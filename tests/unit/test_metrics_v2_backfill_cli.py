from __future__ import annotations

from typing import Any

from geo_platform.metrics_v2.repository import MetricsV2Repository

from tools.run_metrics_v2_backfill import plan_backfill


class FakeRepository:
    def load_decision_backfill_batch(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["dry_run"] is True
        return {
            "as_of": "2026-08-27T00:00:00Z",
            "candidate_count": 3,
            "page_count": 2,
            "batch_hash": "a" * 64,
            "next_cursor": "next",
            "items": [
                {
                    "answer_pub_id": "ans_ready",
                    "workflow_payload": {
                        "decision_tasks": [{}, {}, {}],
                        "query_context_request": {"decision_tasks": [{}, {}]},
                    },
                },
                {
                    "answer_pub_id": "ans_unknown",
                    "reason_codes": ["analysis_run_missing"],
                },
            ],
        }

    def load_metrics_backfill_batch(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("wrong stage")


def test_semantic_backfill_plan_is_capacity_and_unknown_aware() -> None:
    report = plan_backfill(
        FakeRepository(),  # type: ignore[arg-type]
        tenant_pub_id="ten_fixture",
        project_pub_id="prj_fixture",
        stage="semantic",
        cursor=None,
        limit=100,
        as_of=None,
    )

    assert report["mode"] == "dry_run"
    assert report["candidate_count"] == 3
    assert report["prepared_count"] == 1
    assert report["preparation_unknown_count"] == 1
    assert report["preparation_reason_counts"] == {"analysis_run_missing": 1}
    assert report["estimated_atomic_decisions"] == 5
    assert report["official_activation"] is False
    assert len(report["selection_hash"]) == 64
    assert len(report["confirm_token"]) == 64


def test_metrics_repository_normalizes_sqlalchemy_psycopg_dsn() -> None:
    repository = MetricsV2Repository("postgresql+psycopg://geo:secret@database.example/geo")

    assert repository.dsn == "postgresql://geo:secret@database.example/geo"
