from contextlib import contextmanager
from typing import Any

import pytest
from fastapi import HTTPException
from geo_platform.evidence import router as evidence_router
from geo_platform.identity.policy import Principal, Role


class _Result:
    def fetchall(self) -> list[dict[str, object]]:
        return []

    def fetchone(self) -> None:
        return None


@pytest.mark.parametrize(
    ("role", "include_internal"),
    ((Role.CUSTOMER, False), (Role.OPERATOR, True)),
)
def test_evidence_list_applies_asset_visibility_without_breaking_customer_reads(
    monkeypatch: pytest.MonkeyPatch,
    role: Role,
    include_internal: bool,
) -> None:
    calls: list[tuple[str, Any]] = []

    class _Connection:
        def execute(self, sql: str, params: Any = None) -> _Result:
            calls.append((sql, params))
            return _Result()

    @contextmanager
    def fake_connection(*_args: object, **_kwargs: object):
        yield _Connection()

    monkeypatch.setattr(evidence_router, "tenant_connection", fake_connection)
    monkeypatch.setattr(evidence_router, "_dsn", lambda: "postgresql://unused")
    result = evidence_router.list_assets(
        limit=50,
        principal=Principal(role.value, role, "tnt_test"),
    )

    assert result["data"] == []
    sql, params = calls[0]
    assert "(%s OR customer_visible)" in sql
    assert params[1] is include_internal


def test_customer_direct_internal_asset_lookup_is_indistinguishable_from_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any]] = []

    class _Connection:
        def execute(self, sql: str, params: Any = None) -> _Result:
            calls.append((sql, params))
            return _Result()

    @contextmanager
    def fake_connection(*_args: object, **_kwargs: object):
        yield _Connection()

    monkeypatch.setattr(evidence_router, "tenant_connection", fake_connection)
    monkeypatch.setattr(evidence_router, "_dsn", lambda: "postgresql://unused")
    with pytest.raises(HTTPException) as denied:
        evidence_router.asset_content(
            "evd_0123456789abcdef",
            principal=Principal("customer", Role.CUSTOMER, "tnt_test"),
        )

    assert denied.value.status_code == 404
    sql, params = calls[0]
    assert "(%s OR customer_visible)" in sql
    assert params[2] is False
