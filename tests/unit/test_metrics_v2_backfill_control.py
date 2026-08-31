from __future__ import annotations

from typing import Any

from geo_platform.config import Settings
from geo_platform.metrics_v2.backfill_control import backfill_options, build_backfill_plan
from geo_platform.metrics_v2.schemas import SemanticBackfillPlanRequest


class FakeRepository:
    def load_decision_backfill_batch(self, **kwargs: Any) -> dict[str, Any]:
        answer_ids = list(kwargs.get("answer_pub_ids") or ("ans_a", "ans_b"))
        items = []
        for answer_id in answer_ids:
            items.append(
                {
                    "answer_pub_id": answer_id,
                    "capture_time": "2026-08-10T00:00:00Z",
                    "preparation_state": "ready",
                    "reason_codes": [],
                    "display": {
                        "query_text": f"问题 {answer_id}",
                        "model": "doubao",
                        "region": "北京",
                        "mode": "deep",
                        "channel": "web",
                        "source_char_count": 3_000,
                    },
                    "workflow_payload": {
                        "decision_tasks": [{}, {}, {}],
                        "query_context_request": {
                            "decision_tasks": [{}, {}],
                            "candidate_input": {
                                "candidates": [
                                    {"candidate_id": "brd_target"},
                                    {"candidate_id": "cmp_peer"},
                                ]
                            },
                        },
                    },
                }
            )
        return {
            "as_of": kwargs.get("as_of") or "2026-08-28T00:00:00Z",
            "candidate_count": len(items),
            "items": items,
            "next_cursor": None,
            "batch_hash": "a" * 64,
        }


def _settings() -> Settings:
    return Settings(
        semantic_decision_llm_model="gpt-5.6-sol",
        semantic_decision_llm_models="glm-5.3-flash,gpt-5.6-luna,gpt-5.6-sol",
        semantic_decision_daily_budget=100,
        semantic_decision_backfill_batch_size=100,
    )


def test_options_recommend_the_low_cost_governed_model() -> None:
    options = backfill_options(
        FakeRepository(),  # type: ignore[arg-type]
        _settings(),
        tenant_pub_id="tnt_test",
        project_pub_id="prj_test",
        cursor=None,
        limit=100,
        as_of=None,
    )

    assert options.default_model == "glm-5.3-flash"
    assert options.max_batch_size == 100
    assert options.candidate_count == 2
    assert options.models[1].model == "glm-5.3-flash"
    assert options.models[1].recommended is True
    assert options.candidates[0].query_text.startswith("问题")


def test_plan_revalidates_selection_and_estimates_model_specific_cost() -> None:
    common = {
        "answer_pub_ids": ["ans_a", "ans_b"],
        "as_of": "2026-08-28T00:00:00Z",
    }
    glm = build_backfill_plan(
        FakeRepository(),  # type: ignore[arg-type]
        _settings(),
        tenant_pub_id="tnt_test",
        project_pub_id="prj_test",
        request=SemanticBackfillPlanRequest(model="glm-5.3-flash", **common),
    )
    sol = build_backfill_plan(
        FakeRepository(),  # type: ignore[arg-type]
        _settings(),
        tenant_pub_id="tnt_test",
        project_pub_id="prj_test",
        request=SemanticBackfillPlanRequest(model="gpt-5.6-sol", **common),
    )

    assert glm.selected_answer_count == 2
    assert glm.window.start.isoformat() == "2026-08-10"
    assert glm.focal_entity_ids == ["brd_target", "cmp_peer"]
    assert glm.estimated_atomic_decisions == 10
    assert glm.estimated_input_tokens > 0
    assert glm.estimated_cost_high_usd > glm.estimated_cost_usd
    assert glm.estimated_cost_high_usd < sol.estimated_cost_high_usd
    assert glm.start_allowed is True
    assert len(glm.selection_hash) == 64
    assert len(glm.confirmation_token) == 64
    assert glm.selection_hash != sol.selection_hash
