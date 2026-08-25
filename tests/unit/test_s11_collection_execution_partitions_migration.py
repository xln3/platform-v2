from __future__ import annotations

import importlib.util
import re
from io import StringIO
from pathlib import Path
from types import ModuleType

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations/versions/s11_0001_collection_execution_partitions.py"
TABLES = (
    "collection_execution_plan_v2",
    "collection_execution_partition_v2",
    "collection_execution_start_outbox_v2",
)
ENTRYPOINTS = (
    "create_collection_execution_plan_v2",
    "create_collection_execution_partition_v2",
    "finalize_collection_execution_plan_v2",
    "stage_collection_partition_workflow_start_v2",
    "claim_collection_execution_start_outbox_v2",
    "finalize_collection_execution_start_outbox_v2",
    "read_collection_execution_control_v2",
    "advance_collection_execution_partition_v2",
    "claim_collection_execution_reconciliation_v2",
    "cancel_collection_execution_partition_v2",
    "finalize_collection_execution_partition_v2",
)


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("s11_execution_partitions", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render(operation: str) -> str:
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        getattr(_module(), operation)()
    return output.getvalue()


@pytest.fixture(scope="module")
def upgrade_sql() -> str:
    return _render("upgrade")


@pytest.fixture(scope="module")
def downgrade_sql() -> str:
    return _render("downgrade")


def _table_block(sql: str, table: str) -> str:
    match = re.search(
        rf"CREATE TABLE platform\.{re.escape(table)} \((.*?)\n\);",
        sql,
        flags=re.DOTALL,
    )
    assert match is not None, table
    return match.group(1)


def test_revision_descends_from_final_stage3_head() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision("s11_0001_execution_partitions")
    assert revision is not None
    assert revision.down_revision == "s10_0001_submission_transactions"
    assert revision.revision == "s11_0001_execution_partitions"


def test_additive_plane_has_compact_plan_partition_and_start_outbox(upgrade_sql: str) -> None:
    for table in TABLES:
        block = _table_block(upgrade_sql, table)
        for column in ("id", "pub_id", "tenant_id", "project_id", "version"):
            assert re.search(rf"\b{column}\b", block), (table, column)
    plan = _table_block(upgrade_sql, "collection_execution_plan_v2")
    for column in (
        "campaign_id",
        "expected_slot_count",
        "execution_partition_size",
        "workflow_page_size",
        "expected_partition_count",
        "materialized_partition_count",
        "materialization_cursor",
        "plan_digest",
        "last_partition_digest",
    ):
        assert re.search(rf"\b{column}\b", plan)
    for forbidden in (
        "membership_specification_json",
        "slot_rows_json",
        "task_array",
        "question_slots",
    ):
        assert forbidden not in plan

    partition = _table_block(upgrade_sql, "collection_execution_partition_v2")
    for column in (
        "partition_index",
        "start_slot_ordinal",
        "end_slot_ordinal_exclusive",
        "partition_digest",
        "cursor",
        "cursor_version",
        "checkpoint_digest",
        "reconciliation_ref",
        "cancellation_ref",
        "workflow_command_id",
    ):
        assert re.search(rf"\b{column}\b", partition)


def test_every_new_table_forces_tenant_rls_and_has_no_direct_runtime_dml(
    upgrade_sql: str,
) -> None:
    for table in TABLES:
        assert f'ALTER TABLE platform."{table}" ENABLE ROW LEVEL SECURITY;' in upgrade_sql
        assert f'ALTER TABLE platform."{table}" FORCE ROW LEVEL SECURITY;' in upgrade_sql
        assert f'CREATE POLICY tenant_isolation ON platform."{table}"' in upgrade_sql
        revoke_public = "REVOKE ALL ON TABLE platform.%%I FROM PUBLIC".replace(" ", "")
        assert revoke_public in upgrade_sql.replace(" ", "")
    assert "GRANT SELECT ON TABLE platform.%%I TO %%I" in upgrade_sql
    assert "GRANT INSERT ON TABLE platform.%%I" not in upgrade_sql
    assert "GRANT UPDATE ON TABLE platform.%%I" not in upgrade_sql
    assert "GRANT DELETE ON TABLE platform.%%I" not in upgrade_sql


def test_only_exactly_frozen_campaign_can_create_or_advance_plan(upgrade_sql: str) -> None:
    create_plan = upgrade_sql.index("CREATE FUNCTION platform.create_collection_execution_plan_v2")
    create_partition = upgrade_sql.index(
        "CREATE FUNCTION platform.create_collection_execution_partition_v2"
    )
    finalize = upgrade_sql.index("CREATE FUNCTION platform.finalize_collection_execution_plan_v2")
    plan_sql = upgrade_sql[create_plan:create_partition]
    partition_sql = upgrade_sql[create_partition:finalize]
    for fragment in (
        "campaign_row.state<>'frozen'",
        "campaign_row.materialization_state<>'complete'",
        "campaign_row.materialized_slot_count<>campaign_row.expected_slot_count",
        "campaign_row.materialization_cursor<>campaign_row.expected_slot_count",
        "campaign_row.membership_hash IS NULL",
        "campaign_row.config_lifecycle_state<>'active'",
    ):
        assert fragment in plan_sql
    for fragment in (
        "campaign_row.state<>'frozen'",
        "campaign_row.expected_slot_count<>plan_row.expected_slot_count",
        "campaign_row.specification_hash<>plan_row.specification_hash",
        "campaign_row.membership_hash<>plan_row.membership_hash",
        "config.lifecycle_state='active'",
        "config.revision_hash=plan_row.config_revision_hash",
    ):
        assert fragment in partition_sql


def test_partition_materialization_is_streamable_gapless_and_short_cas(
    upgrade_sql: str,
) -> None:
    assert "p_expected_prior_cursor bigint" in upgrade_sql
    assert "p_expected_plan_version integer" in upgrade_sql
    assert "plan_row.materialization_cursor<>p_partition_index" in upgrade_sql
    assert "calculated_start := p_partition_index*plan_row.execution_partition_size" in upgrade_sql
    assert "calculated_end := least(" in upgrade_sql
    assert "materialized_partition_count=next_cursor" in upgrade_sql
    assert "materialization_cursor=next_cursor" in upgrade_sql
    assert "ordered.partition_index<>ordered.row_number-1" in upgrade_sql
    assert "ordered.start_slot_ordinal<>coalesce(ordered.previous_end,0)" in upgrade_sql
    assert "final_ordinal<>plan_row.expected_slot_count" in upgrade_sql
    assert "p_execution_partition_size bigint" in upgrade_sql
    plan = _table_block(upgrade_sql, "collection_execution_plan_v2")
    assert "expected_slot_count BIGINT NOT NULL" in plan
    assert "expected_slot_count <=" not in plan
    assert ((279_000 - 1) // 100_000) + 1 == 3
    migration_source = MIGRATION.read_text(encoding="utf-8")
    assert "execution_partition_size=10000" not in migration_source.replace(" ", "")
    assert "execution_partition_size=10_000" not in migration_source.replace(" ", "")


def test_database_chunk_runtime_concurrency_and_partition_are_not_conflated() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "materialization_chunk_size" not in source
    assert "runtime_concurrency" not in source
    assert "worker_capacity" not in source
    assert "route_id" not in source
    assert "resource_lease" not in source
    assert "proxy" not in source.lower()
    assert "create_collection_execution_partition_v2" in source
    assert "execution_partition_size" in source


def test_plan_freeze_is_one_short_irreversible_cas(upgrade_sql: str) -> None:
    for fragment in (
        "OLD.state='assembling' AND NEW.state='frozen'",
        "NEW.materialization_state<>'complete'",
        "NEW.last_partition_digest IS NULL",
        "collection_execution_plan_frozen_immutable",
        "state='frozen',last_partition_digest=final_digest",
        "AND plan.state='assembling'",
        "collection_execution_plan_freeze_cas_lost",
    ):
        assert fragment in upgrade_sql


def test_partition_and_outbox_state_are_guarded_and_history_is_immutable(
    upgrade_sql: str,
) -> None:
    for fragment in (
        "collection_execution_plan_delete_forbidden",
        "collection_execution_partition_delete_forbidden",
        "collection_execution_partition_identity_immutable",
        "collection_execution_partition_transition_forbidden",
        "collection_execution_start_outbox_identity_immutable",
        "collection_execution_start_outbox_terminal_immutable",
        "collection_execution_start_outbox_transition_forbidden",
        "BEFORE INSERT OR UPDATE OR DELETE ON platform.collection_execution_plan_v2",
        "BEFORE INSERT OR UPDATE OR DELETE ON platform.collection_execution_partition_v2",
        "BEFORE INSERT OR UPDATE OR DELETE ON platform.collection_execution_start_outbox_v2",
    ):
        assert fragment in upgrade_sql


def test_workflow_start_payload_is_constant_size_and_reference_only(upgrade_sql: str) -> None:
    outbox = _table_block(upgrade_sql, "collection_execution_start_outbox_v2")
    compact_outbox = re.sub(r"\s+", "", outbox)
    assert "octet_length(command_json::text)<=8192" in compact_outbox
    assert "jsonb_typeof(command_json)='object'" in compact_outbox
    assert "jsonb_typeof(command_json->'workflow_input')='object'" in compact_outbox
    for key in (
        "schema_version",
        "outbox_type",
        "workflow_type",
        "task_queue",
        "payload_schema_version",
        "campaign_id",
        "campaign_pub_id",
        "partition_pub_id",
        "partition_digest",
        "plan_digest",
        "cursor",
        "campaign_reference",
        "workflow_input",
    ):
        assert f"'{key}'" in outbox
    for forbidden in (
        "membership_specification_json",
        "question_slots",
        "slot_rows",
        "task_array",
        "jsonb_agg",
        "array_agg",
    ):
        assert forbidden not in outbox
    assert "p_command_json<>expected_command" in upgrade_sql
    assert "collection_execution_start_command_drift" in upgrade_sql
    for constant in (
        "geo_collection_v2",
        "GeoCollectionV2Workflow",
        "geo-platform-v2-collection-v2",
        "collection-workflow-v2",
    ):
        assert constant in outbox
    for workflow_input_key in (
        "tenant_pub_id",
        "project_pub_id",
        "checkpoint_ref",
        "checkpoint_digest",
        "reconciliation_checkpoint_ref",
        "capability_policy_revision",
        "control_policy_revision",
        "comparison_policy_revision",
        "scheduling_window_start_utc",
        "scheduling_window_end_utc",
        "continue_as_new_after_pages",
    ):
        assert f"'{workflow_input_key}'" in outbox


def test_start_is_initial_admission_only_and_later_cursor_is_drift(
    upgrade_sql: str,
) -> None:
    assert "p_expected_cursor<>partition_row.start_slot_ordinal" in upgrade_sql
    assert "'cursor',partition_row.start_slot_ordinal" in upgrade_sql
    assert "(workflow_input->>'cursor')::bigint<>partition_row.start_slot_ordinal" in upgrade_sql
    assert "partition_row.control_revision<>p_expected_control_revision" in upgrade_sql
    assert "collection_execution_start_lineage_drift" in upgrade_sql


def test_outbox_has_exact_claim_publish_failure_fence_cas(upgrade_sql: str) -> None:
    outbox = _table_block(upgrade_sql, "collection_execution_start_outbox_v2")
    for column in (
        "outbox_state",
        "attempt_count",
        "claim_ref",
        "claim_fence",
        "claimed_at",
        "published_at",
        "temporal_run_id",
        "last_error_code",
    ):
        assert re.search(rf"\b{column}\b", outbox)
    for fragment in (
        "OLD.outbox_state IN ('pending','failed')",
        "NEW.outbox_state='claimed'",
        "NEW.claim_fence=OLD.claim_fence+1",
        "OLD.outbox_state='claimed'",
        "NEW.outbox_state IN ('published','failed')",
        "collection_execution_outbox_claim_cas_failed",
        "collection_execution_outbox_finalize_cas_failed",
    ):
        assert fragment in upgrade_sql


def test_restricted_entrypoints_have_exact_cas_and_no_external_io(upgrade_sql: str) -> None:
    for function in ENTRYPOINTS:
        assert f"CREATE FUNCTION platform.{function}(" in upgrade_sql
        function_start = upgrade_sql.index(f"CREATE FUNCTION platform.{function}(")
        next_function = upgrade_sql.find("CREATE FUNCTION platform.", function_start + 1)
        block = upgrade_sql[function_start : next_function if next_function >= 0 else None]
        assert "SECURITY DEFINER" in block
        assert "SET row_security = on" in block
        assert "SET timezone = 'UTC'" in block
        assert "assert_collection_execution_context_s11" in block
    assert "exact_replay_drift" in upgrade_sql
    assert "_cas_failed" in upgrade_sql
    assert "_cas_lost" in upgrade_sql
    for forbidden in (
        "http://",
        "https://",
        "temporalio",
        "playwright",
        "requests.",
        "run_service",
    ):
        assert forbidden not in upgrade_sql.lower()


def test_page_reconciliation_and_cancel_boundaries_are_fail_closed(upgrade_sql: str) -> None:
    for fragment in (
        "p_new_cursor-p_expected_cursor>plan_row.workflow_page_size",
        "partition_row.state NOT IN ('start_staged','running')",
        (
            "partition_row.state NOT IN (\n"
            "               'planned','start_staged','running','reconciling'"
        ),
        "p_now<partition_row.reconcile_after",
        "p_expected_control_revision",
        "control_revision=partition.control_revision+1",
        "THEN 'awaiting_terminal' ELSE 'running' END",
        "collection_execution_cancel_cas_failed",
        "collection_execution_reconciliation_cas_failed",
    ):
        assert fragment in upgrade_sql


def test_completion_requires_separate_terminal_database_proof(upgrade_sql: str) -> None:
    for fragment in (
        "OLD.state='awaiting_terminal' AND NEW.state='completed'",
        "partition_row.state<>'awaiting_terminal'",
        "partition_row.cursor<>partition_row.end_slot_ordinal_exclusive",
        "terminal_ref=p_terminal_ref",
        "terminal_digest=p_terminal_digest",
        "terminal_state=p_terminal_state",
        "collection_execution_terminal_cas_failed",
    ):
        assert fragment in upgrade_sql


def test_acl_denies_helpers_and_grants_only_restricted_worker_entrypoints(
    upgrade_sql: str,
) -> None:
    assert "procedure.proname LIKE '%%\\_s11' ESCAPE '\\'" in upgrade_sql
    assert "REVOKE ALL ON FUNCTION " in upgrade_sql
    assert " FROM PUBLIC" in upgrade_sql
    assert " FROM '||quote_ident(role_name)" in upgrade_sql
    assert "GRANT EXECUTE ON FUNCTION " in upgrade_sql
    assert " TO geo_worker" in upgrade_sql
    for entrypoint in ENTRYPOINTS:
        assert f"'{entrypoint}'" in upgrade_sql
    assert "GRANT EXECUTE ON FUNCTION '||function_identity||' TO geo_api" not in upgrade_sql


def test_downgrade_refuses_history_then_removes_the_additive_plane(
    downgrade_sql: str,
) -> None:
    refusal = downgrade_sql.index("collection_execution_history_present_downgrade_refused")
    first_drop = downgrade_sql.index("DROP TABLE")
    assert refusal < first_drop
    for table in reversed(TABLES):
        assert f"DROP TABLE platform.{table};" in downgrade_sql
    for entrypoint in ENTRYPOINTS:
        assert f"DROP FUNCTION IF EXISTS platform.{entrypoint}" in downgrade_sql
