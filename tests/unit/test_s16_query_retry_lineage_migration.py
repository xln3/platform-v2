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
MIGRATION = ROOT / "migrations/versions/s16_0001_query_retry_lineage.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("s16_query_retry_lineage", MIGRATION)
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


def test_revision_is_the_single_head_after_s15() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision("s16_0001_query_retry_lineage")
    assert revision is not None
    assert revision.down_revision == "s15_0001_integrity_retry_queue"
    assert scripts.get_current_head() == revision.revision


def test_query_terminal_time_and_logical_retry_identity_are_additive(upgrade_sql: str) -> None:
    assert "ADD COLUMN IF NOT EXISTS catalog_revision" in upgrade_sql
    assert "ADD COLUMN IF NOT EXISTS audit_completeness" in upgrade_sql
    assert "ADD COLUMN terminal_at" in upgrade_sql
    for column in (
        "root_run_id",
        "root_run_pub_id",
        "business_key",
        "retry_depth",
        "resolved_task_terminal_at",
    ):
        assert f"ADD COLUMN {column}" in upgrade_sql
    assert "fk_service2_batch_query_root_run_scope" in upgrade_sql
    assert "uq_service2_batch_query_logical_query" in upgrade_sql
    assert "task_state IN ('done','completed','failed')" in upgrade_sql
    assert "task_state IN ('done','completed')" in upgrade_sql
    assert "(state,not_before,priority DESC,created_at,tenant_id)" in upgrade_sql
    assert "UPDATE platform.collection_task" not in upgrade_sql
    assert "UPDATE platform.service2_corpus_batch_query" not in upgrade_sql


def test_head_migration_reconciles_both_runtime_roles_from_closed_manifest(
    upgrade_sql: str,
) -> None:
    for role in ("geo_api", "geo_worker"):
        assert f"rolname='{role}'" in upgrade_sql
        assert f'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA "platform" FROM "{role}"' in (
            upgrade_sql
        )
        assert f'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA "platform" FROM "{role}"' in (
            upgrade_sql
        )
    assert "ALTER DEFAULT PRIVILEGES REVOKE ALL ON FUNCTIONS FROM PUBLIC" in upgrade_sql
    assert 'GRANT INSERT,SELECT,UPDATE ON TABLE "platform"."service2_model_call"' in upgrade_sql


def test_downgrade_refuses_to_drop_new_lineage_history(downgrade_sql: str) -> None:
    refusal = downgrade_sql.index("query_retry_lineage_history_present_downgrade_refused")
    assert refusal < downgrade_sql.index("DROP")
