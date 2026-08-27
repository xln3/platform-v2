from __future__ import annotations

from geo_platform.identity.policy import Principal, Role
from geo_platform.main import app


def test_versioned_openapi_exposes_runtime_governance_release_and_operations() -> None:
    paths = app.openapi()["paths"]
    required = {
        "/api/v2/knowledge/v1/runtime/resolve",
        "/api/v2/knowledge/v1/observations:ingest",
        "/api/v2/knowledge/v1/candidates",
        "/api/v2/knowledge/v1/proposals",
        "/api/v2/knowledge/v1/evidence",
        "/api/v2/knowledge/v1/change-sets",
        "/api/v2/knowledge/v1/releases",
        "/api/v2/knowledge/v1/connector-runs",
        "/api/v2/knowledge/v1/audit-events",
        "/api/v2/knowledge/v1/health",
        "/api/v2/knowledge/v1/readiness",
        "/api/v2/knowledge/v1/metrics",
    }
    assert required <= set(paths)
    request_schema = app.openapi()["components"]["schemas"]["RuntimeResolveRequest"]
    policy_schema = request_schema["properties"]["policy"]
    policy_ref = policy_schema.get("$ref") or policy_schema["allOf"][0]["$ref"]
    policy_name = str(policy_ref).rsplit("/", 1)[-1]
    assert set(app.openapi()["components"]["schemas"][policy_name]["enum"]) == {
        "deterministic_only",
        "llm_assisted",
        "llm_required",
        "exploratory",
    }


def test_rbac_separates_runtime_submission_review_and_publication() -> None:
    customer = Principal("customer", Role.CUSTOMER, "tenant")
    operator = Principal("operator", Role.OPERATOR, "tenant")
    analyst = Principal("analyst", Role.ANALYST, "tenant")
    reviewer = Principal("reviewer", Role.REVIEWER, "tenant")
    worker = Principal("worker", Role.WORKER, "tenant")
    admin = Principal("admin", Role.ADMIN, "tenant")

    assert customer.allows("knowledge:resolve")
    assert customer.allows("knowledge:observe")
    assert not customer.allows("knowledge:review")
    assert operator.allows("knowledge:propose")
    assert analyst.allows("knowledge:evidence")
    assert reviewer.allows("knowledge:review")
    assert not reviewer.allows("knowledge:publish")
    assert worker.allows("knowledge:observe")
    assert not worker.allows("knowledge:read")
    assert admin.allows("knowledge:publish")
    assert admin.allows("knowledge:connector")
