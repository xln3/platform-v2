from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any, cast

import pytest

from tools import configure_api_runtime_role as runtime_roles
from tools.configure_api_runtime_role import SCHEMAS


class _FetchOneResult:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self._row = row

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row


class _RecordingConnection:
    def __init__(
        self,
        *,
        stage3_absent: bool = False,
        missing_stage3_table: str | None = None,
        missing_stage3_function: str | None = None,
        rls_row: tuple[object, ...] = (True, True, 1, 1),
        unexpected_stage3_overloads: int = 0,
    ) -> None:
        self.statements: list[tuple[str, tuple[object, ...] | None]] = []
        self.stage3_absent = stage3_absent
        self.missing_stage3_table = missing_stage3_table
        self.missing_stage3_function = missing_stage3_function
        self.rls_row = rls_row
        self.unexpected_stage3_overloads = unexpected_stage3_overloads

    def execute(self, query: Any, params: tuple[object, ...] | None = None) -> _FetchOneResult:
        rendered = query.as_string() if hasattr(query, "as_string") else str(query)
        self.statements.append((rendered, params))
        if rendered == "SELECT to_regclass(%s)":
            assert params is not None
            table = str(params[0]).removeprefix("platform.")
            if table in runtime_roles.STAGE3_TABLES and (
                self.stage3_absent or table == self.missing_stage3_table
            ):
                return _FetchOneResult((None,))
            return _FetchOneResult((params[0],))
        if rendered == "SELECT to_regprocedure(%s)":
            assert params is not None
            function = str(params[0])
            if function in runtime_roles.STAGE3_FUNCTIONS and (
                self.stage3_absent or function == self.missing_stage3_function
            ):
                return _FetchOneResult((None,))
            return _FetchOneResult((params[0],))
        if "relation.relrowsecurity" in rendered:
            return _FetchOneResult(self.rls_row)
        if "NOT procedure.oid=ANY" in rendered:
            return _FetchOneResult((self.unexpected_stage3_overloads,))
        if "information_schema.columns" in rendered:
            return _FetchOneResult(None)
        return _FetchOneResult(None)


class _VerificationConnection(_RecordingConnection):
    def __init__(
        self,
        role: str,
        *,
        stage3_update_allowed: bool = False,
        operation_insert_allowed: bool = False,
        rls_row: tuple[object, ...] = (True, True, 1, 1),
    ) -> None:
        super().__init__(rls_row=rls_row)
        self.role = role
        self.stage3_update_allowed = stage3_update_allowed
        self.operation_insert_allowed = operation_insert_allowed

    def execute(self, query: Any, params: tuple[object, ...] | None = None) -> _FetchOneResult:
        rendered = query.as_string() if hasattr(query, "as_string") else str(query)
        self.statements.append((rendered, params))
        if rendered in {"SELECT to_regclass(%s)", "SELECT to_regprocedure(%s)"}:
            assert params is not None
            return _FetchOneResult((params[0],))
        if "relation.relrowsecurity" in rendered:
            return _FetchOneResult(self.rls_row)
        if "NOT procedure.oid=ANY" in rendered:
            return _FetchOneResult((self.unexpected_stage3_overloads,))
        if "has_table_privilege" in rendered:
            assert params is not None
            if len(params) == 12:
                return _FetchOneResult(
                    (True, False, self.stage3_update_allowed, False, False, False)
                )
            return _FetchOneResult((True, self.operation_insert_allowed, False, False, False))
        if "aclexplode" in rendered:
            return _FetchOneResult((False,))
        if rendered == "SELECT has_function_privilege(%s,%s,'EXECUTE')":
            assert params is not None
            expected = self.role == runtime_roles.WORKER_ROLE and (
                params[1] in runtime_roles.STAGE3_WORKER_FUNCTIONS
            )
            return _FetchOneResult((expected,))
        return _FetchOneResult(None)


def _rendered(statements: list[tuple[str, tuple[object, ...] | None]]) -> str:
    return "\n".join(statement for statement, _ in statements)


def test_runtime_roles_are_granted_access_to_the_sop_schema() -> None:
    assert "sop" in SCHEMAS


def test_runtime_roles_are_granted_access_to_the_posting_schema() -> None:
    assert "posting" in SCHEMAS


def test_stage2_acl_is_reapplied_after_schema_wide_grants() -> None:
    install_source = inspect.getsource(runtime_roles.install_role)
    verify_source = inspect.getsource(runtime_roles.verify_role)

    assert "apply_stage2_minimum_acl(connection, role=role)" in install_source
    assert "verify_stage2_minimum_acl(connection, role=str(role[0]))" in verify_source


def test_stage3_acl_is_reapplied_after_stage2_and_verified() -> None:
    install_source = inspect.getsource(runtime_roles.install_role)
    verify_source = inspect.getsource(runtime_roles.verify_role)

    stage2_apply = "apply_stage2_minimum_acl(connection, role=role)"
    stage3_apply = "apply_stage3_minimum_acl(connection, role=role)"
    assert install_source.index(stage2_apply) < install_source.index(stage3_apply)
    assert "verify_stage3_minimum_acl(connection, role=str(role[0]))" in verify_source


def test_runtime_role_defaults_do_not_auto_authorize_future_objects() -> None:
    install_source = inspect.getsource(runtime_roles.install_role)

    assert "ALTER DEFAULT PRIVILEGES" in install_source
    assert "REVOKE ALL ON TABLES FROM" in install_source
    assert "REVOKE ALL ON SEQUENCES FROM" in install_source
    assert "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC" in install_source
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO" not in install_source
    assert "GRANT USAGE, SELECT ON SEQUENCES TO" not in install_source


def test_stage2_acl_separates_insert_from_mutable_update_columns() -> None:
    assert "collection_submission_reconciliation_proof" in runtime_roles.STAGE2_TABLES
    assert "tenant_id" not in {
        column
        for columns in runtime_roles.STAGE2_WORKER_UPDATE_COLUMNS.values()
        for column in columns
    }
    assert runtime_roles.STAGE2_WORKER_UPDATE_COLUMNS["collection_quota_bucket"] == (
        "reserved_units",
        "settled_consumed_units",
        "settled_unknown_units",
        "bucket_state",
        "fence_version",
        "version",
        "updated_at",
    )
    assert (
        "collection_submission_reconciliation_proof"
        not in runtime_roles.STAGE2_WORKER_INSERT_TABLES
    )


def test_stage2_internal_functions_are_not_regranted_by_runtime_provisioning() -> None:
    assert runtime_roles.RECONCILIATION_FUNCTION in runtime_roles.STAGE2_FUNCTIONS
    assert "platform.validate_collection_quota_conservation_v2()" in (
        runtime_roles.STAGE2_FUNCTIONS
    )
    source = inspect.getsource(runtime_roles.apply_stage2_minimum_acl)
    assert "REVOKE ALL ON FUNCTION" in source
    assert "function == RECONCILIATION_FUNCTION" in source


def test_stage3_constants_exactly_cover_current_s10_objects() -> None:
    migration = (
        Path(__file__).parents[2]
        / "migrations/versions/s10_0001_collection_submission_transactions.py"
    ).read_text(encoding="utf-8")
    declared_tables = set(re.findall(r'op\.create_table\(\s*"([^"]+)"', migration))
    declared_functions: set[str] = set()
    for name, raw_parameters in re.findall(
        r"CREATE FUNCTION platform\.(\w+)\s*\((.*?)\)\s*RETURNS",
        migration,
        flags=re.DOTALL,
    ):
        parameter_types = ",".join(
            parameter.strip().split()[-1]
            for parameter in raw_parameters.split(",")
            if parameter.strip()
        )
        declared_functions.add(f"platform.{name}({parameter_types})")

    assert len(runtime_roles.STAGE3_TABLES) == len(set(runtime_roles.STAGE3_TABLES))
    assert len(runtime_roles.STAGE3_FUNCTIONS) == len(set(runtime_roles.STAGE3_FUNCTIONS))
    assert set(runtime_roles.STAGE3_TABLES) == declared_tables
    assert set(runtime_roles.STAGE3_FUNCTIONS) == declared_functions
    assert set(runtime_roles.STAGE3_WORKER_FUNCTIONS).isdisjoint(
        runtime_roles.STAGE3_INTERNAL_FUNCTIONS
    )


def test_stage3_after_stage2_makes_operation_table_read_only() -> None:
    connection = _RecordingConnection()
    runtime_roles.apply_stage2_minimum_acl(
        cast(Any, connection),
        role=runtime_roles.WORKER_ROLE,
    )
    stage3_start = len(connection.statements)
    runtime_roles.apply_stage3_minimum_acl(
        cast(Any, connection),
        role=runtime_roles.WORKER_ROLE,
    )
    stage2_sql = _rendered(connection.statements[:stage3_start])
    stage3_sql = _rendered(connection.statements[stage3_start:])

    assert (
        'REVOKE ALL ON TABLE platform.collection_submission_operation FROM "geo_worker"'
        in stage3_sql
    )
    assert (
        'REVOKE UPDATE ("send_state","send_state_version","send_started_at",'
        '"send_resolved_at","reconciliation_state","reconcile_after","state_reason",'
        '"version","updated_at") ON TABLE platform.collection_submission_operation '
        'FROM "geo_worker"' in stage3_sql
    )
    assert (
        "GRANT SELECT ON TABLE platform.collection_submission_operation "
        'TO "geo_worker"' in stage3_sql
    )
    assert (
        "GRANT SELECT, INSERT ON TABLE platform.collection_submission_operation" not in stage3_sql
    )
    assert 'GRANT INSERT ON TABLE platform."collection_quota_reservation"' in stage2_sql
    assert "collection_quota_reservation" not in stage3_sql
    assert "collection_quota_reservation_effect" not in stage3_sql
    assert "collection_quota_bucket" not in stage3_sql


@pytest.mark.parametrize("role", [runtime_roles.API_ROLE, runtime_roles.WORKER_ROLE])
def test_stage3_tables_are_read_only_for_runtime_roles(role: str) -> None:
    connection = _RecordingConnection()
    runtime_roles.apply_stage3_minimum_acl(cast(Any, connection), role=role)
    statements = [statement for statement, _ in connection.statements]

    for table in runtime_roles.STAGE3_TABLES:
        qualified = f'platform."{table}"'
        assert f'REVOKE ALL ON TABLE {qualified} FROM "{role}"' in statements
        assert f'GRANT SELECT ON TABLE {qualified} TO "{role}"' in statements
        assert not any(
            statement.startswith(("GRANT INSERT", "GRANT UPDATE", "GRANT DELETE"))
            and qualified in statement
            for statement in statements
        )


@pytest.mark.parametrize("role", [runtime_roles.API_ROLE, runtime_roles.WORKER_ROLE])
def test_stage3_function_execute_matrix(role: str) -> None:
    connection = _RecordingConnection()
    runtime_roles.apply_stage3_minimum_acl(cast(Any, connection), role=role)
    statements = [statement for statement, _ in connection.statements]
    worker_entrypoints = frozenset(runtime_roles.STAGE3_WORKER_FUNCTIONS)

    for function in runtime_roles.STAGE3_FUNCTIONS:
        assert f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC" in statements
        assert f'REVOKE ALL ON FUNCTION {function} FROM "{role}"' in statements
        grant = f'GRANT EXECUTE ON FUNCTION {function} TO "{role}"'
        assert (grant in statements) is (
            role == runtime_roles.WORKER_ROLE and function in worker_entrypoints
        )


def test_stage3_rejects_unknown_runtime_role() -> None:
    connection = _RecordingConnection()
    with pytest.raises(ValueError, match="unsupported Stage 3 runtime role"):
        runtime_roles.apply_stage3_minimum_acl(cast(Any, connection), role="not-a-runtime-role")


@pytest.mark.parametrize("role", [runtime_roles.API_ROLE, runtime_roles.WORKER_ROLE])
def test_stage3_verifier_accepts_only_the_minimum_matrix(role: str) -> None:
    connection = _VerificationConnection(role)
    runtime_roles.verify_stage3_minimum_acl(cast(Any, connection), role=role)


def test_stage3_verifier_rejects_any_direct_update_on_s10_tables() -> None:
    connection = _VerificationConnection(runtime_roles.WORKER_ROLE, stage3_update_allowed=True)
    with pytest.raises(RuntimeError, match="stage3 table ACL mismatch"):
        runtime_roles.verify_stage3_minimum_acl(
            cast(Any, connection), role=runtime_roles.WORKER_ROLE
        )


@pytest.mark.parametrize("role", [runtime_roles.API_ROLE, runtime_roles.WORKER_ROLE])
def test_stage3_verifier_rejects_direct_operation_insert(role: str) -> None:
    connection = _VerificationConnection(
        role,
        operation_insert_allowed=True,
    )
    with pytest.raises(RuntimeError, match="stage3 operation ACL mismatch"):
        runtime_roles.verify_stage3_minimum_acl(cast(Any, connection), role=role)


def test_wholly_absent_stage3_does_not_revoke_stage2_operation_access() -> None:
    connection = _RecordingConnection(stage3_absent=True)
    runtime_roles.apply_stage3_minimum_acl(
        cast(Any, connection),
        role=runtime_roles.WORKER_ROLE,
    )
    sql = _rendered(connection.statements)
    assert "collection_submission_operation FROM" not in sql


@pytest.mark.parametrize("missing_kind", ["table", "function"])
def test_partial_stage3_catalog_is_rejected(missing_kind: str) -> None:
    kwargs: dict[str, str] = {}
    if missing_kind == "table":
        kwargs["missing_stage3_table"] = runtime_roles.STAGE3_TABLES[0]
    else:
        kwargs["missing_stage3_function"] = runtime_roles.STAGE3_FUNCTIONS[0]
    connection = _RecordingConnection(**kwargs)
    with pytest.raises(RuntimeError, match="partial Stage 3 catalog"):
        runtime_roles.apply_stage3_minimum_acl(
            cast(Any, connection),
            role=runtime_roles.WORKER_ROLE,
        )


def test_unexpected_stage3_function_overload_is_rejected() -> None:
    connection = _RecordingConnection(unexpected_stage3_overloads=1)
    with pytest.raises(RuntimeError, match="unexpected Stage 3 function overload"):
        runtime_roles.apply_stage3_minimum_acl(
            cast(Any, connection),
            role=runtime_roles.WORKER_ROLE,
        )


def test_decoy_overload_is_not_treated_as_a_wholly_absent_stage3_catalog() -> None:
    connection = _RecordingConnection(
        stage3_absent=True,
        unexpected_stage3_overloads=1,
    )
    with pytest.raises(RuntimeError, match="unexpected Stage 3 function overload"):
        runtime_roles.apply_stage3_minimum_acl(
            cast(Any, connection),
            role=runtime_roles.WORKER_ROLE,
        )


@pytest.mark.parametrize(
    "rls_row",
    [
        (False, True, 1, 1),
        (True, False, 1, 1),
        (True, True, 0, 0),
        (True, True, 1, 0),
        (True, True, 2, 1),
    ],
)
def test_stage3_verifier_rejects_rls_or_policy_drift(
    rls_row: tuple[object, ...],
) -> None:
    connection = _VerificationConnection(runtime_roles.WORKER_ROLE, rls_row=rls_row)
    with pytest.raises(RuntimeError, match="stage3 RLS policy mismatch"):
        runtime_roles.verify_stage3_minimum_acl(
            cast(Any, connection),
            role=runtime_roles.WORKER_ROLE,
        )
