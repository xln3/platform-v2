from __future__ import annotations

from pathlib import Path

MIGRATION = (
    Path(__file__).parents[2] / "migrations" / "versions" / "s09_0001_operational_keyset_indexes.py"
)


def test_operational_keyset_migration_is_index_only_and_tracks_both_run_scopes() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'down_revision: str | Sequence[str] | None = "s08_0001_service2_all_u"' in source
    assert "ix_collection_run_tenant_created_pub" in source
    assert "ix_collection_run_project_created_pub" in source
    assert "ix_project_tenant_created_pub" in source
    assert "ix_collection_phone_account_created_pub" in source
    assert "ix_collection_browser_created_pub" in source
    assert "ix_collection_account_event_phone_created_pub" in source
    assert "ix_collection_account_event_platform_created_pub" in source
    assert "ix_posting_batch_tenant_created_pub" in source
    assert 'sa.text("created_at DESC")' in source
    assert 'sa.text("pub_id DESC")' in source
    assert "create_table" not in source
    assert "drop_table" not in source
    assert "UPDATE " not in source
    assert "DELETE " not in source
