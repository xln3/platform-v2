from datetime import UTC, datetime, timedelta

from domain.reporting.service1_governance import (
    SERVICE1_MODELS,
    SERVICE1_REGIONS,
    assign_repeats,
    question_group_hash,
    quotation_gate,
    resolve_scope_registration,
)


def _groups() -> list[dict]:
    return [
        {
            "id": f"candidate_{index:02d}",
            "title": f"服务1场景{index}",
            "questions": [f"服务1问题{index}-{variant}" for variant in range(4)],
            "service_number": 1,
            "quotation_appendix": 2,
            "question_group_hash": question_group_hash(
                [f"服务1问题{index}-{variant}" for variant in range(4)]
            ),
        }
        for index in range(1, 4)
    ]


def _answers(groups: list[dict]) -> list[dict]:
    start = datetime(2026, 8, 12, tzinfo=UTC)
    rows = []
    for group in groups:
        for question in group["questions"]:
            for model in SERVICE1_MODELS:
                for region in SERVICE1_REGIONS:
                    for repeat in (1, 2):
                        rows.append(
                            {
                                "pub_id": f"ans_{len(rows):03}",
                                "query_text": question,
                                "model": model,
                                "region": region,
                                "capture_time": start + timedelta(minutes=len(rows)),
                                "run_pub_id": f"run_{repeat}",
                            }
                        )
    return rows


def test_registered_scope_must_precede_sampling_and_exclude_service4() -> None:
    groups = _groups()
    answers = _answers(groups)
    snapshot = {
        "service1_scope_registration": {
            "schema_version": "service1-scope-registration-v1",
            "group_hashes": [group["question_group_hash"] for group in groups],
            "frozen_at": "2026-08-11T00:00:00+00:00",
            "selection_basis": "客户在采样前确认的三个业务场景",
            "confirmed_by": "客户项目负责人",
            "scope_label": "三个跨产品线业务场景",
            "represents_overall_brand": False,
        }
    }

    resolved = resolve_scope_registration(
        snapshot=snapshot, candidate_groups=groups, answers=answers
    )
    assert resolved["status"] == "registered"
    assert resolved["ready_for_approval"] is True

    groups[2]["service_number"] = 4
    resolved = resolve_scope_registration(
        snapshot=snapshot, candidate_groups=groups, answers=answers
    )
    assert resolved["ready_for_approval"] is False
    assert "service1_service4_boundary_unverified" in resolved["reasons"]


def test_unregistered_scope_uses_order_only_and_never_becomes_approvable() -> None:
    groups = _groups() + [
        {
            "id": "candidate_04",
            "title": "服务4问题",
            "questions": ["网证问题"] * 4,
            "service_number": 4,
            "quotation_appendix": 3,
        }
    ]
    resolved = resolve_scope_registration(snapshot={}, candidate_groups=groups, answers=[])

    assert resolved["selected_group_hashes"] == [
        question_group_hash(group["questions"]) for group in groups[:3]
    ]
    assert resolved["status"] == "historical_unregistered"
    assert resolved["ready_for_approval"] is False
    assert "scope_not_preregistered" in resolved["reasons"]


def test_repeat_independence_requires_distinct_runs() -> None:
    rows = _answers(_groups())[:2]
    rows[1]["query_text"] = rows[0]["query_text"]
    rows[1]["model"] = rows[0]["model"]
    rows[1]["region"] = rows[0]["region"]
    repeats, reasons = assign_repeats(rows)
    assert repeats == {rows[0]["pub_id"]: 1, rows[1]["pub_id"]: 2}
    assert reasons == []

    rows[1]["run_pub_id"] = rows[0]["run_pub_id"]
    _, reasons = assign_repeats(rows)
    assert any(reason.startswith("repeat_run_independence_unproven") for reason in reasons)


def test_quotation_gate_fails_closed_for_missing_account_and_egress_ledger() -> None:
    groups = _groups()
    answers = _answers(groups)
    repeat_map, _ = assign_repeats(answers)
    samples = [
        {
            "sample_id": row["pub_id"],
            "repeat_no": repeat_map[row["pub_id"]],
            "capture_time": row["capture_time"],
            "question": row["query_text"],
            "platform": row["model"],
            "region": row["region"],
            "run_id": row["run_pub_id"],
            "response_text": "完整回答",
            "independent_repeat": True,
            "answer_evidence": [{"sha256": "a" * 64}],
            "screenshot_evidence": [{"sha256": "b" * 64}],
            "account_id_masked": None,
            "browser_instance": None,
            "egress_region_gb": None,
            "egress_audit": None,
        }
        for row in answers
    ]
    ready, reasons = quotation_gate(
        selected_groups=groups,
        sample_rows=samples,
        scope_registration={"reasons": []},
    )
    assert ready is False
    assert "region_account_ledger_missing" in reasons
    assert "browser_instance_ledger_missing" in reasons
    assert "egress_region_audit_missing" in reasons
