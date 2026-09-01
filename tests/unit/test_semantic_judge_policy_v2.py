from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest
from decision_v2_fixtures import policy_for, task
from pydantic import ValidationError

from domain.analysis.v2.decision_task_loader import (
    load_builtin_judge_policies,
    load_builtin_task_definitions,
)
from domain.analysis.v2.decision_task_schema import (
    JudgePolicyDefinition,
    validate_policy_compatibility,
)


def test_builtin_policies_preserve_v2_and_add_dormant_single_model_v21() -> None:
    tasks = load_builtin_task_definitions()
    policies = load_builtin_judge_policies(tasks=tasks)

    assert {policy.name for policy in policies} == {
        "semantic-v2-primary-hybrid",
        "semantic-v2-primary-model",
        "semantic-v2-shadow-hybrid",
        "semantic-v2-shadow-model",
    }
    assert all(policy.status.value == "experimental" for policy in policies)
    assert all(policy.calibration_artifact_hash is None for policy in policies)
    assert all(policy.fallback_policy.action in {"abstain", "review"} for policy in policies)
    assert all(
        route.resolved_revision and route.retention_policy
        for policy in policies
        for route in policy.model_routes
    )
    assert all(len(policy.policy_hash) == 64 for policy in policies)
    primary = tuple(policy for policy in policies if policy.version == "2.1.0")
    assert all(
        route.provider == "openai-compatible" for policy in primary for route in policy.model_routes
    )


def test_policy_hash_changes_with_resolved_model_revision() -> None:
    original = policy_for("recommendation-relation")
    payload = original.model_dump(mode="python", exclude={"policy_hash"})
    changed_routes = deepcopy(payload["model_routes"])
    changed_routes[0]["resolved_revision"] = "fixture-2026-08-28"

    changed = JudgePolicyDefinition.model_validate(payload | {"model_routes": changed_routes})

    assert changed.policy_hash != original.policy_hash


def test_policy_rejects_secrets_keyword_fallback_and_unresolved_route() -> None:
    original = policy_for("recommendation-relation")
    payload = original.model_dump(mode="python", exclude={"policy_hash"})

    with pytest.raises(ValidationError, match="judge_policy_contains_secret"):
        JudgePolicyDefinition.model_validate(
            payload | {"inference_configs": {"semantic-llm-primary-v2": {"api_key": "secret"}}}
        )
    with pytest.raises(ValidationError, match="fallback_action_forbidden"):
        JudgePolicyDefinition.model_validate(
            payload | {"fallback_policy": {"action": "keyword_heuristic"}}
        )

    pipeline = deepcopy(payload["method_pipeline"])
    pipeline[0]["route_name"] = "missing-route"
    with pytest.raises(ValidationError, match="method_pipeline_references_unknown_route"):
        JudgePolicyDefinition.model_validate(payload | {"method_pipeline": pipeline})


def test_published_policy_allows_versioned_uncalibrated_operation() -> None:
    original = policy_for("recommendation-relation")
    payload = original.model_dump(mode="python", exclude={"policy_hash"})

    published = JudgePolicyDefinition.model_validate(
        payload
        | {
            "status": "published",
            "published_at": datetime(2026, 8, 27, tzinfo=UTC),
            "calibration_artifact_hash": None,
        }
    )

    assert published.status.value == "published"
    assert published.calibration_artifact_hash is None
    assert published.policy_hash == original.policy_hash


def test_policy_cannot_be_reused_for_incompatible_task_method() -> None:
    model_policy = policy_for("recommendation-relation")

    with pytest.raises(ValueError, match="judge_policy_incompatible_task"):
        validate_policy_compatibility(task("rank-semantics"), model_policy)
