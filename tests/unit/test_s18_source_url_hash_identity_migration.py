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
MIGRATION = ROOT / "migrations/versions/s18_0004_source_url_hash_identity.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("s18_0004_source_url_hash_identity", MIGRATION)
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


def test_revision_branches_off_production_line_s18_0003() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision("s18_0004_url_hash_identity")
    assert revision is not None
    assert revision.down_revision == "s18_0003_metrics_v2_failure"
    assert revision.revision in {
        item.revision for item in scripts.iterate_revisions("heads", "base")
    }


def test_constraint_swap_sql_targets_hash_identity() -> None:
    upgrade_sql = _render("upgrade")
    assert "DROP CONSTRAINT uq_source_url_identity" in upgrade_sql
    assert "UNIQUE (tenant_id, canonical_url_hash)" in upgrade_sql
    downgrade_sql = _render("downgrade")
    assert "UNIQUE (tenant_id, canonical_url_hash, canonical_url)" in downgrade_sql
