from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path
from types import ModuleType

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from api.geo_platform.knowledge.models import InferenceTrace, Proposal

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


def test_s17_revisions_and_s18_extensions_form_the_current_additive_lineage() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    first = scripts.get_revision("s17_0001_knowledge_evolution")
    second = scripts.get_revision("s17_0002_knowledge_trace_details")
    third = scripts.get_revision("s17_0003_knowledge_immutable")
    fourth = scripts.get_revision("s17_0004_release_membership")
    fifth = scripts.get_revision("s17_0005_credential_boundary")
    metrics_v2 = scripts.get_revision("s18_0001_geo_metrics_v2")
    model_lineage = scripts.get_revision("s18_0002_knowledge_model_lineage")
    assert first is not None and first.down_revision == "s16_0001_query_retry_lineage"
    assert second is not None and second.down_revision == first.revision
    assert third is not None and third.down_revision == second.revision
    assert fourth is not None and fourth.down_revision == third.revision
    assert fifth is not None and fifth.down_revision == fourth.revision
    assert metrics_v2 is not None and metrics_v2.down_revision == fifth.revision
    assert model_lineage is not None and model_lineage.down_revision == metrics_v2.revision
    assert model_lineage.revision in {
        item.revision for item in scripts.iterate_revisions("heads", "base")
    }


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


def test_s17_materialized_versions_are_append_only_and_uniquely_numbered() -> None:
    upgrade = _render("s17_0003_knowledge_version_immutability", "upgrade")
    assert "uq_knowledge_object_identity_version" in upgrade
    assert "uq_assertion_identity_version" in upgrade
    assert "ADD COLUMN assertion_key" in upgrade
    assert "CREATE TRIGGER trg_knowledge_object_append_only" in upgrade
    assert "CREATE TRIGGER trg_assertion_append_only" in upgrade
    downgrade = _render("s17_0003_knowledge_version_immutability", "downgrade")
    refusal = downgrade.index("knowledge_version_history_present_downgrade_refused")
    assert refusal < downgrade.index("DROP TRIGGER")


def test_s17_model_lineage_extension_is_additive_indexed_and_history_safe() -> None:
    upgrade = _render("s18_0002_knowledge_model_lineage", "upgrade")
    assert "ADD COLUMN requested_model_name" in upgrade
    assert "ADD COLUMN model_identity_source" in upgrade
    assert "ADD COLUMN model_catalog_revision" in upgrade
    assert "ADD COLUMN model_call_attempted" in upgrade
    assert "ix_inference_trace_tenant_requested_model" in upgrade
    assert "ix_inference_trace_tenant_actual_model" in upgrade
    downgrade = _render("s18_0002_knowledge_model_lineage", "downgrade")
    refusal = downgrade.index("knowledge_model_lineage_history_present_downgrade_refused")
    assert refusal < downgrade.index("DROP COLUMN")


def test_s17_model_lineage_orm_matches_the_migrated_trace_table() -> None:
    lineage = {
        "requested_model_name",
        "model_identity_source",
        "model_catalog_revision",
        "model_call_attempted",
    }
    assert lineage <= set(InferenceTrace.__table__.columns.keys())
    assert lineage.isdisjoint(Proposal.__table__.columns.keys())
