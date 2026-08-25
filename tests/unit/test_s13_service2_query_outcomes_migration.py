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
MIGRATION = ROOT / "migrations/versions/s13_0001_service2_query_outcomes.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("s13_service2_query_outcomes", MIGRATION)
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


def _table_block(sql: str) -> str:
    match = re.search(
        r"CREATE TABLE platform\.service2_corpus_batch_query \((.*?)\n\);",
        sql,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def test_revision_belongs_to_the_single_repository_chain() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision("s13_0001_service2_query_outcomes")
    assert revision is not None
    assert revision.down_revision == "s11_0001_execution_partitions"
    assert len(scripts.get_heads()) == 1
    assert revision.revision in {candidate.revision for candidate in scripts.walk_revisions()}


def test_query_ledger_freezes_terminal_task_truth_and_scoped_lineage(upgrade_sql: str) -> None:
    block = _table_block(upgrade_sql)
    for column in (
        "tenant_id",
        "project_id",
        "batch_id",
        "run_id",
        "run_pub_id",
        "answer_task_id",
        "answer_task_pub_id",
        "task_state",
        "outcome",
        "failure_code",
        "answer_present",
        "u_occurrence_count",
    ):
        assert re.search(rf"\b{column}\b", block)
    assert "fk_service2_batch_query_batch_scope" in block
    assert "fk_service2_batch_query_run_scope" in block
    assert "fk_service2_batch_query_task_scope" in block
    assert "task_state IN ('done','failed')" in block
    assert "outcome IN ('succeeded','failed')" in block
    assert "outcome='succeeded' AND task_state='done' AND answer_present" in block
    assert "outcome <> 'failed' OR failure_code IS NOT NULL" in block


def test_query_ledger_forces_rls_and_becomes_immutable_with_its_batch(
    upgrade_sql: str,
) -> None:
    assert (
        "ALTER TABLE platform.service2_corpus_batch_query ENABLE ROW LEVEL SECURITY" in upgrade_sql
    )
    assert (
        "ALTER TABLE platform.service2_corpus_batch_query FORCE ROW LEVEL SECURITY" in upgrade_sql
    )
    assert "CREATE POLICY tenant_isolation ON platform.service2_corpus_batch_query" in upgrade_sql
    assert "current_setting('app.tenant_id', true)" in upgrade_sql
    assert "trg_service2_batch_query_frozen_guard" in upgrade_sql
    assert "service2_guard_frozen_batch" in upgrade_sql
    assert "GRANT SELECT,INSERT" in upgrade_sql
    assert "GRANT UPDATE" not in upgrade_sql
    assert "GRANT DELETE" not in upgrade_sql


def test_upgrade_never_rewrites_collection_or_existing_service2_facts(upgrade_sql: str) -> None:
    forbidden = (
        r"UPDATE\s+platform\.collection_run",
        r"UPDATE\s+platform\.collection_task",
        r"UPDATE\s+platform\.answer_source_occurrence",
        r"DELETE\s+FROM\s+platform\.answer_source_occurrence",
        r"UPDATE\s+platform\.service2_corpus_batch",
    )
    assert all(
        re.search(pattern, upgrade_sql, flags=re.IGNORECASE) is None for pattern in forbidden
    )


def test_downgrade_refuses_to_discard_query_history(downgrade_sql: str) -> None:
    assert "service2_query_history_present_downgrade_refused" in downgrade_sql
    refusal = downgrade_sql.index("service2_query_history_present_downgrade_refused")
    assert refusal < downgrade_sql.index("DROP TABLE")
