from __future__ import annotations

from tools.compare_knowledge_update_methods import _decision_from_patch, _statistics


def test_llm_patch_eligibility_respects_declared_scope() -> None:
    patch = {
        "identity_entity_id": "entity:newland",
        "identity_entity_type": "brand_family",
        "roll_up_entity_id": "entity:newland",
        "relationship": "self",
        "eligible": True,
        "scopes": ["ctid", "digital_identity"],
        "confidence": 0.9,
    }

    general = _decision_from_patch(patch, requested_scopes=("cybersecurity",))
    identity = _decision_from_patch(patch, requested_scopes=("ctid",))

    assert general["eligible"] is False
    assert identity["eligible"] is True


def test_paired_statistics_report_project_minus_method() -> None:
    method = {"case_pass": [False, True, False, True]}
    project = {"case_pass": [True, True, True, True]}

    result = _statistics(method, project, seed=1)

    assert result["project_minus_method_case_accuracy_delta"] == 0.5
    assert result["discordant_method_only_correct"] == 0
    assert result["discordant_project_only_correct"] == 2
