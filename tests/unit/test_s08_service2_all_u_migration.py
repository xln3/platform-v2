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
MIGRATION = ROOT / "migrations/versions/s08_0001_service2_all_u_corpus.py"
TABLES = (
    "service2_corpus_batch",
    "service2_corpus_batch_run",
    "service2_corpus_item",
    "service2_analysis_attempt",
    "service2_relation_finding",
    "service2_finding_review",
    "service2_batch_event",
    "service2_fact_manifest",
)


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("s08_service2_all_u", MIGRATION)
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
    assert match is not None, f"missing platform.{table}"
    return match.group(1)


def test_revision_is_additive_after_execution_governance() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision("s08_0001_service2_all_u")
    assert revision is not None
    assert revision.down_revision == "s07_0002_execution_governance"


def test_all_service2_tables_are_tenant_project_scoped_and_force_rls(upgrade_sql: str) -> None:
    for table in TABLES:
        block = _table_block(upgrade_sql, table)
        for column in ("id", "pub_id", "tenant_id", "project_id"):
            assert re.search(rf"\b{column}\b", block), (table, column)
        assert f'ALTER TABLE platform."{table}" ENABLE ROW LEVEL SECURITY;' in upgrade_sql
        assert f'ALTER TABLE platform."{table}" FORCE ROW LEVEL SECURITY;' in upgrade_sql
        assert f'CREATE POLICY tenant_isolation ON platform."{table}"' in upgrade_sql
        assert f'REVOKE ALL ON platform."{table}" FROM PUBLIC;' in upgrade_sql
    assert upgrade_sql.count("current_setting('app.tenant_id', true)") == len(TABLES) * 2
    assert "GRANT SELECT,INSERT,UPDATE ON platform.service2_corpus_batch" in upgrade_sql
    assert "GRANT DELETE" not in upgrade_sql


def test_batch_and_item_preserve_all_u_scope_and_public_lineage(upgrade_sql: str) -> None:
    batch = _table_block(upgrade_sql, "service2_corpus_batch")
    item = _table_block(upgrade_sql, "service2_corpus_item")
    for column in (
        "scope_selector_hash",
        "source_snapshot_boundary",
        "corpus_policy_version",
        "judgment_policy_version",
        "expected_occurrence_count",
        "distinct_url_count",
        "materialized_item_count",
        "service_entitlement_revision",
    ):
        assert re.search(rf"\b{column}\b", batch)
    assert "distinct_url_count <= expected_occurrence_count" in batch
    assert "materialized_item_count <= expected_occurrence_count" in batch
    for column in (
        "occurrence_pub_id",
        "run_pub_id",
        "answer_task_pub_id",
        "source_url_pub_id",
        "snapshot_pub_id",
        "source_document_pub_id",
        "fetch_attempt_pub_id",
        "processing_state",
        "manual_evidence_state",
    ):
        assert re.search(rf"\b{column}\b", item)
    assert "fk_service2_item_occurrence_scope" in item
    assert "fk_service2_item_snapshot_scope" in item
    assert "fk_service2_item_document_scope" in item
    assert "fk_service2_item_attempt_scope" in item
    assert "manual_evidence_required" in item
    assert "unobservable" in item


def test_relation_finding_keeps_taxonomy_evidence_and_attribution_separate(
    upgrade_sql: str,
) -> None:
    finding = _table_block(upgrade_sql, "service2_relation_finding")
    for level in ("L0", "L1", "L2a", "L2b", "L3a", "L3b", "L4"):
        assert f"'{level}'" in finding
    for column in (
        "ledger",
        "relation_direction",
        "textual_speaker",
        "target_entity",
        "beneficiary_entity",
        "fact_anchor_state",
        "evidence_quote_hash",
        "quote_start",
        "quote_end",
        "context_start",
        "context_end",
        "snapshot_text_sha256",
        "visual_validation_status",
        "comparison_present",
        "peer_elevated",
        "scope_narrowed",
        "industry_wide",
        "comparison_manipulated",
        "key_fact_omitted",
        "publisher_confidence",
        "commissioner_confidence",
        "factcheck_verdict",
        "policy_version",
    ):
        assert re.search(rf"\b{column}\b", finding)
    assert "ledger='exposure' AND level='L0' AND is_disparagement IS FALSE" in finding
    assert "level IN ('L0','L1') AND is_disparagement IS FALSE" in finding
    assert "unknown_attribution_has_no_party" in finding


def test_reviews_attempts_events_and_manifests_are_append_only(upgrade_sql: str) -> None:
    assert "trg_service2_analysis_attempt_append_only" in upgrade_sql
    assert "trg_service2_review_append_only" in upgrade_sql
    assert "trg_service2_event_append_only" in upgrade_sql
    assert "trg_service2_manifest_append_only" in upgrade_sql
    assert "service2_append_only_fact_immutable" in upgrade_sql
    assert "trg_service2_batch_frozen_guard" in upgrade_sql
    assert "trg_service2_item_frozen_guard" in upgrade_sql
    assert "trg_service2_finding_frozen_guard" in upgrade_sql
    assert "service2_frozen_batch_immutable" in upgrade_sql
    review = _table_block(upgrade_sql, "service2_finding_review")
    assert "idempotency_key" in review
    assert "based_on_version" in review and "resulting_version" in review
    assert "resulting_version=based_on_version+1" in review


def test_upgrade_does_not_rewrite_legacy_u_judgment_or_formal_facts(upgrade_sql: str) -> None:
    forbidden = (
        r"UPDATE\s+platform\.answer_source_occurrence",
        r"DELETE\s+FROM\s+platform\.answer_source_occurrence",
        r"UPDATE\s+platform\.disparagement_judgment",
        r"DELETE\s+FROM\s+platform\.disparagement_judgment",
        r"UPDATE\s+reporting\.",
        r"DELETE\s+FROM\s+reporting\.",
    )
    assert all(
        re.search(pattern, upgrade_sql, flags=re.IGNORECASE) is None for pattern in forbidden
    )


def test_downgrade_refuses_to_destroy_service2_history(downgrade_sql: str) -> None:
    assert "service2_history_present_downgrade_refused" in downgrade_sql
    refusal = downgrade_sql.index("service2_history_present_downgrade_refused")
    first_drop = downgrade_sql.index("DROP FUNCTION")
    assert refusal < first_drop
