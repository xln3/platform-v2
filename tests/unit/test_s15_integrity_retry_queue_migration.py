from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path
from types import ModuleType

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations/versions/s15_0001_integrity_retry_queue.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("s15_integrity_retry_queue", MIGRATION)
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


def test_revision_is_in_the_single_chain_after_s14() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision("s15_0001_integrity_retry_queue")
    assert revision is not None
    assert revision.down_revision == "s14_0001_sop_runtime_acl"
    assert len(scripts.get_heads()) == 1
    assert revision.revision in {candidate.revision for candidate in scripts.walk_revisions()}


def test_paid_call_claim_and_exact_manifest_binding_are_durable(upgrade_sql: str) -> None:
    assert "CREATE TABLE platform.service2_model_call" in upgrade_sql
    assert "UNIQUE (call_key)" in upgrade_sql
    assert "state IN ('claimed','succeeded','failed','ambiguous')" in upgrade_sql
    assert "web_search_observed" in upgrade_sql
    assert "provider_request_id" in upgrade_sql
    assert "ADD COLUMN service2_manifest_pub_id" in upgrade_sql
    assert "ADD COLUMN service2_manifest_hash" in upgrade_sql
    assert "ck_formal_production_service2_manifest_pair" in upgrade_sql


def test_query_retry_is_a_leased_capability_queue_with_append_only_history(
    upgrade_sql: str,
) -> None:
    assert "CREATE TABLE platform.collection_query_retry_intent" in upgrade_sql
    for column in (
        "source_run_id",
        "source_task_id",
        "retry_run_id",
        "business_key",
        "capability_key",
        "priority",
        "not_before",
        "lease_owner",
        "lease_token",
        "lease_expires_at",
        "retry_depth",
        "max_auto_retries",
    ):
        assert column in upgrade_sql
    assert "ix_collection_query_retry_dispatch" in upgrade_sql
    assert "ix_collection_query_retry_fairness" in upgrade_sql
    assert "state='leased' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL" in upgrade_sql
    assert "state<>'leased' AND lease_owner IS NULL AND lease_token IS NULL" in upgrade_sql
    assert "CREATE TABLE platform.collection_query_execution_attempt" in upgrade_sql
    assert "CREATE TABLE platform.collection_failure_knowledge" in upgrade_sql
    assert "trg_collection_query_attempt_append_only" in upgrade_sql
    assert "trg_collection_failure_knowledge_append_only" in upgrade_sql
    for table in (
        "collection_query_retry_intent",
        "collection_query_execution_attempt",
        "collection_failure_knowledge",
    ):
        assert f"ALTER TABLE platform.{table} ENABLE ROW LEVEL SECURITY" in upgrade_sql
        assert f"ALTER TABLE platform.{table} FORCE ROW LEVEL SECURITY" in upgrade_sql


def test_downgrade_refuses_to_delete_integrity_or_retry_history(downgrade_sql: str) -> None:
    refusal = downgrade_sql.index("integrity_retry_history_present_downgrade_refused")
    assert refusal < downgrade_sql.index("DROP TABLE")
