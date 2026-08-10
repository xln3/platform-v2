"""projects API 的 brandrank_domain 真源字段校验单测（s06_0014）。

非法 domain 400 在 DB 访问之前 fail-fast（端点先校验后建 repository），
故本文件零 DB：TestClient + dependency_overrides[get_principal]。
字段读写回环（含真库）由 tests/integration/test_s01_project_catalog.py 覆盖。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from geo_platform.identity.policy import Principal, Role, get_principal
from geo_platform.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _principal() -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject="u-projects", role=Role.OPERATOR, tenant_pub_id="tnt_projects"
    )
    yield
    app.dependency_overrides.pop(get_principal, None)


def test_create_project_invalid_brandrank_domain_400() -> None:
    resp = client.post(
        "/api/v2/projects",
        headers={"Idempotency-Key": "idem-test-0000000000"},
        json={"name": "P", "customer_name": "C", "brandrank_domain": "不存在的领域"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "unknown_brandrank_domain"


def test_patch_project_invalid_brandrank_domain_400() -> None:
    resp = client.patch(
        "/api/v2/projects/prj_01ABCDEFGHIJKLMNOPQRSTUV",
        json={"brandrank_domain": "不存在的领域", "expected_version": 1},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unknown_brandrank_domain"


def test_brandrank_domain_validation_helper() -> None:
    """纯函数口径：None/空白 → None（清除）；合法词表原样；非法 → 400。"""
    from geo_platform.projects.router import _validate_brandrank_domain

    assert _validate_brandrank_domain(None) is None
    assert _validate_brandrank_domain("  ") is None
    assert _validate_brandrank_domain(" insurance ") == "insurance"
    assert _validate_brandrank_domain("legal") == "legal"
    with pytest.raises(HTTPException) as excinfo:
        _validate_brandrank_domain("餐饮")
    assert excinfo.value.status_code == 400
