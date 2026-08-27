from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path
from types import ModuleType

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations" / "versions"


def _module(name: str) -> ModuleType:
    path = MIGRATIONS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render(name: str, operation: str) -> str:
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        getattr(_module(name), operation)()
    return output.getvalue()


def test_s17_revisions_form_the_current_additive_lineage() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    first = scripts.get_revision("s17_0001_knowledge_evolution")
    second = scripts.get_revision("s17_0002_knowledge_trace_details")
    assert first is not None and first.down_revision == "s16_0001_query_retry_lineage"
    assert second is not None and second.down_revision == first.revision
    assert second.revision in {item.revision for item in scripts.iterate_revisions("heads", "base")}


def test_s17_core_schema_is_tenant_isolated_and_history_preserving() -> None:
    sql = _render("s17_0001_knowledge_evolution", "upgrade")
    tables = (
        "observation",
        "candidate",
        "candidate_observation",
        "knowledge_object",
        "assertion",
        "proposal",
        "evidence",
        "adjudication",
        "change_set",
        "knowledge_release",
        "release_activation",
        "connector_run",
        "inference_trace",
        "semantic_cache",
        "audit_event",
    )
    for table in tables:
        assert f'ALTER TABLE knowledge."{table}" ENABLE ROW LEVEL SECURITY' in sql
        assert f'ALTER TABLE knowledge."{table}" FORCE ROW LEVEL SECURITY' in sql
    for table in (
        "observation",
        "evidence",
        "adjudication",
        "knowledge_release",
        "release_activation",
        "inference_trace",
        "audit_event",
    ):
        assert f"CREATE TRIGGER trg_{table}_append_only" in sql
    assert "current_setting('app.tenant_pub_id', true)" in sql
    assert "GRANT SELECT,INSERT,UPDATE" in sql


def test_s17_core_downgrade_refuses_to_drop_governance_history() -> None:
    sql = _render("s17_0001_knowledge_evolution", "downgrade")
    refusal = sql.index("knowledge_history_present_downgrade_refused")
    assert refusal < sql.index("DROP TABLE")


def test_s17_trace_extension_is_additive_and_history_safe() -> None:
    upgrade = _render("s17_0002_knowledge_trace_details", "upgrade")
    assert "ADD COLUMN adopt_model_inferred" in upgrade
    assert "ADD COLUMN tool_summary" in upgrade
    downgrade = _render("s17_0002_knowledge_trace_details", "downgrade")
    refusal = downgrade.index("knowledge_trace_history_present_downgrade_refused")
    assert refusal < downgrade.index("DROP COLUMN")
