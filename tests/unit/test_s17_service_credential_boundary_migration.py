from __future__ import annotations

from pathlib import Path


def test_service_credential_boundary_keeps_hash_table_private() -> None:
    migration = (
        Path(__file__).parents[2] / "migrations/versions/s17_0005_service_credential_boundary.py"
    ).read_text(encoding="utf-8")

    assert "SECURITY DEFINER" in migration
    assert migration.count("SET search_path = pg_catalog") == 3
    assert "REVOKE ALL ON FUNCTION" in migration
    assert "GRANT EXECUTE ON FUNCTION" in migration
    assert "GRANT SELECT" not in migration
    assert "GRANT INSERT" not in migration
    assert "GRANT UPDATE" not in migration
    assert "current_setting('app.tenant_pub_id', true)" in migration
    assert "credential.secret_hash = p_secret_hash" in migration
