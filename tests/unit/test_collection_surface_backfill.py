from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pytest
from geo_platform.collection.legacy_surface_backfill import (
    AUDIT_INSERT_SQL,
    LEGACY_CONTRACT_VERSION,
    SQL_PLAN,
    STRICT_SELECTOR_PREDICATE,
    SURFACE_ASSIGNMENT_BASIS,
    TARGET_PLANS,
    BackfillRequest,
    SurfaceBackfillError,
    confirmation_token,
    run_collection_surface_backfill,
)

from tools.backfill_collection_surface import _arguments


@dataclass
class FakeResult:
    rows: Sequence[Mapping[str, Any]] = ()
    rowcount: int = 0

    def fetchall(self) -> Sequence[Mapping[str, Any]]:
        return self.rows


class FakeConnection:
    def __init__(
        self,
        *,
        surface: str | None = None,
        basis: str | None = None,
        contract: str | None = None,
        orphan_count: int = 0,
        update_rowcount: int = 0,
    ) -> None:
        self.surface = surface
        self.basis = basis
        self.contract = contract
        self.orphan_count = orphan_count
        self.update_rowcount = update_rowcount
        self.calls: list[tuple[str, Mapping[str, Any] | None]] = []
        self.commits = 0
        self.rollbacks = 0

    @staticmethod
    def _schema_rows() -> list[dict[str, str]]:
        from geo_platform.collection import legacy_surface_backfill as module

        return [
            {"table_schema": schema, "table_name": table, "column_name": column}
            for (schema, table), columns in module._REQUIRED_COLUMNS.items()
            for column in columns
        ]

    @staticmethod
    def _run() -> dict[str, Any]:
        return {
            "run_id": "00000000-0000-0000-0000-000000000001",
            "run_pub_id": "run_legacy_1",
            "tenant_id": "00000000-0000-0000-0000-000000000002",
            "tenant_pub_id": "ten_1",
            "project_id": "00000000-0000-0000-0000-000000000003",
            "project_pub_id": "prj_1",
        }

    def execute(self, query: str, params: Mapping[str, Any] | None = None) -> FakeResult:
        self.calls.append((query, params))
        if ":schema-check" in query:
            return FakeResult(self._schema_rows())
        if ":tenant-id" in query:
            assert params is not None
            if params["tenant_pub_id"] != "ten_1":
                return FakeResult()
            return FakeResult([{"id": self._run()["tenant_id"]}])
        if ":selected-runs" in query:
            assert params is not None
            if str(params["tenant_id"]) != self._run()["tenant_id"]:
                return FakeResult()
            return FakeResult([self._run()])
        if ":snapshot:collection_run" in query:
            run = self._run()
            return FakeResult(
                [
                    {
                        "tenant_id": run["tenant_id"],
                        "project_id": run["project_id"],
                        "target_pub_id": run["run_pub_id"],
                        "collection_surface": self.surface,
                        "surface_assignment_basis": self.basis,
                        "legacy_contract_version": self.contract,
                    }
                ]
            )
        if ":snapshot:" in query:
            return FakeResult()
        if ":orphan-counts" in query and self.orphan_count:
            run = self._run()
            return FakeResult(
                [
                    {
                        "category": "answer_tenant_mismatch",
                        "tenant_id": run["tenant_id"],
                        "project_id": run["project_id"],
                        "orphan_count": self.orphan_count,
                        "sample_pub_ids": ["ans_orphan"],
                    }
                ]
            )
        if ":excluded-counts" in query:
            return FakeResult()
        if ":update:collection_run" in query:
            return FakeResult(rowcount=self.update_rowcount)
        return FakeResult()

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _dry_run(connection: FakeConnection) -> dict[str, Any]:
    return run_collection_surface_backfill(connection, BackfillRequest(tenant_pub_id="ten_1"))


def _apply_request(preview: Mapping[str, Any]) -> BackfillRequest:
    selection_hash = str(preview["selection_hash"])
    return BackfillRequest(
        tenant_pub_id="ten_1",
        apply=True,
        expected_selection_hash=selection_hash,
        confirm_token=confirmation_token(selection_hash),
        requested_by_pub_id="usr_operator",
        batch_key="surface-backfill-20260824",
    )


def test_selector_is_strict_and_includes_only_provenanced_legacy_history() -> None:
    selector = " ".join(STRICT_SELECTOR_PREDICATE.split()).lower()
    assert "workflow_id like 'geo-collection/%'" in selector
    assert "source in ('manual','schedule','retry')" in selector
    assert "workflow_id like 'legacy-history/%'" in selector
    assert "integration.legacy_id_map" in selector
    assert "integration.migration_run" in selector
    assert "source_system='legacy-geosys-sqlite'" in selector
    assert "entity_type='collection_run'" in selector
    assert "legacy_map.target_pub_id=r.pub_id" in selector
    assert "legacy_map.state='migrated'" in selector
    assert "migration.state='completed'" in selector
    assert "channel" not in selector
    selected_sql = next(sql for sql in SQL_PLAN if ":selected-runs" in sql)
    assert "r.tenant_id=%(tenant_id)s" in selected_sql


def test_sql_plan_updates_only_surface_overlay_and_relation_direction_is_strict() -> None:
    updates = "\n".join(plan.update_sql.lower() for plan in TARGET_PLANS)
    for forbidden in ("channel", "config", "canonical", "raw", "answer_text"):
        assert f"set {forbidden}" not in updates
        assert f", {forbidden}" not in updates
    assert "collection_surface=" in updates
    assert "surface_assignment_basis=" in updates
    assert "legacy_contract_version=" in updates

    evidence = next(
        plan.target_cte.lower() for plan in TARGET_PLANS if plan.name == "evidence_asset"
    )
    assert "relation.tenant_pub_id=answer.tenant_pub_id" in evidence
    assert "relation.from_pub_id=answer.pub_id" in evidence
    assert "asset.tenant_pub_id=relation.tenant_pub_id" in evidence
    assert "asset.pub_id=relation.to_pub_id" in evidence
    assert "answer.project_pub_id=selected.project_pub_id" in evidence
    assert "asset.project_pub_id=selected.project_pub_id" in evidence
    assert "source_document" not in evidence


def test_default_dry_run_is_read_only_and_returns_confirmation_material() -> None:
    connection = FakeConnection()
    result = _dry_run(connection)
    executed = "\n".join(sql for sql, _params in connection.calls).lower()
    assert result["mode"] == "dry_run"
    assert result["selected_run_count"] == 1
    assert result["candidate_count"] == 1
    assert result["assigned_count"] == 0
    assert result["confirmation_token"].startswith("APPLY-")
    assert ":update:" not in executed
    assert ":lock:" not in executed
    assert ":audit-insert" not in executed
    assert connection.commits == 0
    assert connection.rollbacks == 1
    tags = [sql.split("*/", 1)[0] for sql, _params in connection.calls]
    assert next(index for index, tag in enumerate(tags) if ":rls-context" in tag) < next(
        index for index, tag in enumerate(tags) if ":selected-runs" in tag
    )
    rls_call = next(call for call in connection.calls if ":rls-context" in call[0])
    assert "app.tenant_id" in rls_call[0]
    assert "app.tenant_pub_id" in rls_call[0]
    assert rls_call[1] == {
        "tenant_id": FakeConnection._run()["tenant_id"],
        "tenant_pub_id": "ten_1",
    }


def test_apply_refuses_conflict_before_any_update_or_commit() -> None:
    preview_connection = FakeConnection(surface="consumer_api")
    preview = _dry_run(preview_connection)
    connection = FakeConnection(surface="consumer_api")
    with pytest.raises(SurfaceBackfillError) as exc_info:
        run_collection_surface_backfill(connection, _apply_request(preview))
    assert exc_info.value.code == "surface_backfill_conflict_or_orphan"
    executed = "\n".join(sql for sql, _params in connection.calls).lower()
    assert ":update:" not in executed
    assert ":audit-insert" not in executed
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_apply_is_idempotent_when_every_fact_is_already_consistent() -> None:
    consistent = FakeConnection(
        surface="consumer_web",
        basis=SURFACE_ASSIGNMENT_BASIS,
        contract=LEGACY_CONTRACT_VERSION,
    )
    preview = _dry_run(consistent)
    connection = FakeConnection(
        surface="consumer_web",
        basis=SURFACE_ASSIGNMENT_BASIS,
        contract=LEGACY_CONTRACT_VERSION,
    )
    result = run_collection_surface_backfill(connection, _apply_request(preview))
    assert result["assigned_count"] == 0
    assert result["already_consistent_count"] == 1
    assert connection.commits == 1
    update_calls = [sql for sql, _params in connection.calls if ":update:" in sql]
    assert len(update_calls) == len(TARGET_PLANS)
    assert any(":audit-insert" in sql for sql, _params in connection.calls)


def test_apply_updates_pending_fact_once_and_audit_has_no_sensitive_fields() -> None:
    preview = _dry_run(FakeConnection())
    connection = FakeConnection(update_rowcount=1)
    result = run_collection_surface_backfill(connection, _apply_request(preview))
    assert result["assigned_count"] == 1
    audit_call = next(call for call in connection.calls if ":audit-insert" in call[0])
    audit_params = audit_call[1]
    assert audit_params is not None
    assert audit_params["candidate_count"] == 1
    assert audit_params["assigned_count"] == 1
    assert audit_params["conflict_count"] == 0
    assert audit_params["orphan_count"] == 0
    serialized = str(audit_params).lower()
    for forbidden in ("question", "query", "answer_text", "response_text", "prompt", "dsn"):
        assert forbidden not in serialized
    sample_ids = json.loads(str(audit_params["sample_fact_pub_ids_json"]))
    assert isinstance(sample_ids, list)
    assert len(sample_ids) <= 25
    audit_sql = AUDIT_INSERT_SQL.lower()
    assert "tenant_id" in audit_sql and "project_id" in audit_sql
    assert "execution_mode" in audit_sql
    assert "on conflict (tenant_id,project_id,idempotency_key) do nothing" in audit_sql


def test_apply_refuses_orphaned_lineage() -> None:
    preview = _dry_run(FakeConnection(orphan_count=1))
    connection = FakeConnection(orphan_count=1)
    with pytest.raises(SurfaceBackfillError) as exc_info:
        run_collection_surface_backfill(connection, _apply_request(preview))
    assert exc_info.value.code == "surface_backfill_conflict_or_orphan"
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_cli_defaults_to_dry_run_and_requires_double_apply_confirmation() -> None:
    arguments = _arguments(["--tenant-pub-id", "ten_1"])
    assert arguments.apply is False
    assert arguments.dry_run is False
    with pytest.raises(SystemExit):
        _arguments([])
    with pytest.raises(SystemExit):
        _arguments(["--tenant-pub-id", "tenant id with spaces"])
    with pytest.raises(SystemExit):
        _arguments(["--tenant-pub-id", "ten_1", "--apply"])
    with pytest.raises(SystemExit):
        _arguments(["--tenant-pub-id", "ten_1", "--dry-run", "--apply"])
    with pytest.raises(SystemExit):
        _arguments(["--tenant-pub-id", "ten_1", "--selection-hash", "abc"])
    confirmed = _arguments(
        [
            "--tenant-pub-id",
            "ten_1",
            "--apply",
            "--selection-hash",
            "a" * 64,
            "--confirm-token",
            "APPLY-token",
            "--requested-by-pub-id",
            "usr_operator",
            "--batch-key",
            "surface-backfill-20260824",
        ]
    )
    assert confirmed.apply is True


def test_request_without_tenant_and_cross_tenant_resolution_fail_closed() -> None:
    with pytest.raises(SurfaceBackfillError) as missing:
        run_collection_surface_backfill(FakeConnection(), BackfillRequest())
    assert missing.value.code == "surface_backfill_tenant_required"

    connection = FakeConnection()
    with pytest.raises(SurfaceBackfillError) as unknown:
        run_collection_surface_backfill(
            connection,
            BackfillRequest(tenant_pub_id="ten_other"),
        )
    assert unknown.value.code == "surface_backfill_tenant_not_found"
    assert not any(":selected-runs" in sql for sql, _params in connection.calls)
    assert connection.rollbacks == 1


def test_all_sql_and_counts_are_dynamic() -> None:
    sql = "\n".join(SQL_PLAN)
    for observed_baseline in ("498", "3104", "1492", "12399"):
        assert observed_baseline not in sql
