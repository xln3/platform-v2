from __future__ import annotations

import importlib
from typing import Any


class _Operations:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: Any) -> None:
        self.statements.append(str(statement))


def _migration() -> Any:
    return importlib.import_module("migrations.versions.s14_0001_sop_runtime_acl")


def test_sop_runtime_acl_is_part_of_the_linear_migration_chain() -> None:
    migration = _migration()

    assert migration.revision == "s14_0001_sop_runtime_acl"
    assert migration.down_revision == "s13_0001_service2_query_outcomes"


def test_upgrade_grants_api_write_and_worker_read_without_delete(monkeypatch: Any) -> None:
    migration = _migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    statement = "\n".join(operations.statements)
    assert "GRANT USAGE ON SCHEMA sop TO geo_api" in statement
    assert "GRANT SELECT,INSERT,UPDATE ON TABLE" in statement
    assert "GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA sop TO geo_api" in statement
    assert "GRANT USAGE ON SCHEMA sop TO geo_worker" in statement
    assert "TO geo_worker" in statement
    assert "GRANT SELECT ON TABLE" in statement
    assert "GRANT DELETE" not in statement
    for table in migration._SOP_TABLES:
        assert f"sop.{table}" in statement


def test_downgrade_revokes_only_the_runtime_roles(monkeypatch: Any) -> None:
    migration = _migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.downgrade()

    statement = "\n".join(operations.statements)
    assert "REVOKE ALL ON TABLE" in statement
    assert "FROM geo_api" in statement
    assert "FROM geo_worker" in statement
    assert "FROM PUBLIC" not in statement
