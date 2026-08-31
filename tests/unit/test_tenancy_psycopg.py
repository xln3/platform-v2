from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from geo_platform.tenancy import psycopg as tenancy_psycopg


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def execute(self, statement: str, parameters: tuple[str, ...]) -> None:
        self.calls.append((statement, parameters))


def test_tenant_connection_sets_public_and_internal_rls_context(monkeypatch: Any) -> None:
    connection = _Connection()

    @contextmanager
    def fake_connect(*_args: Any, **_kwargs: Any):
        yield connection

    monkeypatch.setattr(tenancy_psycopg.psycopg, "connect", fake_connect)

    with tenancy_psycopg.tenant_connection("dsn", "tnt_example") as opened:
        assert opened is connection

    assert len(connection.calls) == 1
    statement, parameters = connection.calls[0]
    assert "set_config('app.tenant_pub_id'" in statement
    assert "set_config(" in statement
    assert "'app.tenant_id'" in statement
    assert "FROM platform.tenant" in statement
    assert "COALESCE" in statement
    assert parameters == ("tnt_example", "tnt_example")
