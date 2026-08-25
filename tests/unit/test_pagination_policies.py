from __future__ import annotations

from collections.abc import Mapping

import pytest
from fastapi.testclient import TestClient
from geo_platform.analytics.pagination_policy import SAMPLING_PROGRESS_PAGINATION
from geo_platform.collection.pagination_policy import (
    COLLECTION_RUNS_CURSOR_PAGINATION,
    COLLECTION_RUNS_PAGINATION,
)
from geo_platform.identity.policy import Principal, Role, get_principal
from geo_platform.main import app
from geo_platform.pagination import CursorPaginationPolicy, PaginationPolicy
from geo_platform.service2_corpus.pagination_policy import SERVICE2_CORPUS_PAGINATION
from geo_platform.sop.pagination_policy import SOP_PAGINATION
from geo_platform.tenancy.database import get_db

NUMBERED_POLICIES = (
    SAMPLING_PROGRESS_PAGINATION,
    SOP_PAGINATION,
    COLLECTION_RUNS_PAGINATION,
)
CURSOR_POLICIES = (
    SERVICE2_CORPUS_PAGINATION,
    COLLECTION_RUNS_CURSOR_PAGINATION,
)


def _parameter(path: str, name: str) -> Mapping[str, object]:
    operation = app.openapi()["paths"][path]["get"]
    return next(
        parameter["schema"] for parameter in operation["parameters"] if parameter["name"] == name
    )


def test_module_pagination_policies_are_validated_and_independent() -> None:
    for policy in NUMBERED_POLICIES:
        assert policy.min_page_size <= policy.default_page_size <= policy.max_page_size
        assert policy.min_page_number <= policy.default_page_number <= policy.max_page_number
        assert policy.max_page_size * (policy.max_page_number - 1) <= 1_000_000
    for policy in CURSOR_POLICIES:
        assert policy.min_page_size <= policy.default_page_size <= policy.max_page_size

    assert SAMPLING_PROGRESS_PAGINATION.default_page_size == 4
    assert SOP_PAGINATION.default_page_size == 4
    assert COLLECTION_RUNS_PAGINATION.default_page_size == 4
    assert SERVICE2_CORPUS_PAGINATION.default_page_size == 4
    assert SAMPLING_PROGRESS_PAGINATION.max_page_size == 25
    assert SOP_PAGINATION.max_page_size == 50
    assert COLLECTION_RUNS_CURSOR_PAGINATION.default_page_size == 50
    assert COLLECTION_RUNS_CURSOR_PAGINATION.max_page_size == 100


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "default_page_size": 0,
            "min_page_size": 1,
            "max_page_size": 25,
            "default_page_number": 1,
            "min_page_number": 1,
            "max_page_number": 100,
        },
        {
            "default_page_size": 4,
            "min_page_size": 5,
            "max_page_size": 25,
            "default_page_number": 1,
            "min_page_number": 1,
            "max_page_number": 100,
        },
        {
            "default_page_size": 100,
            "min_page_size": 1,
            "max_page_size": 100,
            "default_page_number": 1,
            "min_page_number": 1,
            "max_page_number": 10_002,
        },
    ],
)
def test_invalid_numbered_policy_fails_during_module_initialization(
    kwargs: dict[str, int],
) -> None:
    with pytest.raises(RuntimeError, match="pagination_policy"):
        PaginationPolicy(**kwargs)


def test_invalid_cursor_policy_fails_during_module_initialization() -> None:
    with pytest.raises(RuntimeError, match="invalid_cursor_pagination_policy"):
        CursorPaginationPolicy(default_page_size=4, min_page_size=5, max_page_size=25)


@pytest.mark.parametrize(
    ("path", "size_name", "size_policy", "has_page"),
    [
        (
            "/api/v2/analytics/sampling-progress",
            "page_size",
            SAMPLING_PROGRESS_PAGINATION,
            True,
        ),
        ("/api/v2/sop/projects", "page_size", SOP_PAGINATION, True),
        ("/api/v2/collection/runs", "page_size", COLLECTION_RUNS_PAGINATION, True),
        (
            "/api/v2/internal/service2-source-corpus/projects/{project_pub_id}/batches/{batch_pub_id}/items",
            "page_size",
            SERVICE2_CORPUS_PAGINATION,
            False,
        ),
        (
            "/api/v2/collection/runs/cursor",
            "limit",
            COLLECTION_RUNS_CURSOR_PAGINATION,
            False,
        ),
    ],
)
def test_openapi_is_generated_from_each_module_policy(
    path: str,
    size_name: str,
    size_policy: PaginationPolicy | CursorPaginationPolicy,
    has_page: bool,
) -> None:
    size_schema = _parameter(path, size_name)
    assert size_schema["type"] == "integer"
    assert size_schema["default"] == size_policy.default_page_size
    assert size_schema["minimum"] == size_policy.min_page_size
    assert size_schema["maximum"] == size_policy.max_page_size
    if has_page:
        assert isinstance(size_policy, PaginationPolicy)
        page_schema = _parameter(path, "page")
        assert page_schema["type"] == "integer"
        assert page_schema["default"] == size_policy.default_page_number
        assert page_schema["minimum"] == size_policy.min_page_number
        assert page_schema["maximum"] == size_policy.max_page_number


@pytest.mark.parametrize(
    ("path", "parameter", "invalid_values"),
    [
        (
            "/api/v2/collection/runs",
            "page_size",
            ("0", "51", "1e2", "not-an-integer", "9" * 200),
        ),
        (
            "/api/v2/collection/runs",
            "page",
            ("0", "20001", "1e2", "not-an-integer", "9" * 200),
        ),
        (
            "/api/v2/analytics/sampling-progress?project_pub_id=prj_validation",
            "page_size",
            ("0", "26", "1e2", "not-an-integer", "9" * 200),
        ),
        (
            "/api/v2/sop/projects",
            "page_size",
            ("0", "51", "1e2", "not-an-integer", "9" * 200),
        ),
        (
            "/api/v2/internal/service2-source-corpus/projects/prj_validation/batches/s2b_validation/items",
            "page_size",
            ("0", "26", "1e2", "not-an-integer", "9" * 200),
        ),
    ],
)
def test_invalid_pagination_inputs_fail_with_bounded_422_response(
    path: str,
    parameter: str,
    invalid_values: tuple[str, ...],
) -> None:
    app.dependency_overrides[get_principal] = lambda: Principal(
        "pagination-validator", Role.ADMIN, "tnt_validation"
    )
    app.dependency_overrides[get_db] = lambda: object()
    try:
        client = TestClient(app)
        separator = "&" if "?" in path else "?"
        for value in invalid_values:
            response = client.get(f"{path}{separator}{parameter}={value}")
            assert response.status_code == 422, response.text
            body = response.text.lower()
            assert "traceback" not in body
            assert "postgresql" not in body
            assert len(response.content) < 8_192
    finally:
        app.dependency_overrides.clear()
