from __future__ import annotations

import json
from pathlib import Path

from tools.migration.sensitive_inventory import build_inventory


def test_inventory_never_emits_raw_paths_or_secret_values(tmp_path: Path) -> None:
    secret = "Cookie: session=do-not-leak"
    candidate = tmp_path / "customer-name" / "capture.har"
    candidate.parent.mkdir()
    candidate.write_text(secret)
    (tmp_path / "ordinary.txt").write_text(secret)

    result = build_inventory(tmp_path)
    serialized = json.dumps(result)

    assert result["entries_total"] == 1
    assert result["secret_values_included"] is False
    assert result["raw_paths_included"] is False
    assert result["kinds"] == {"har": 1}
    assert "customer-name" not in serialized
    assert "capture.har" not in serialized
    assert secret not in serialized
    entry = result["entries"][0]
    assert entry["content_sha256"]
    assert entry["owner_resolution"] == "unresolved"
    assert entry["migration_disposition"] == "quarantine_pending_review"


def test_inventory_classifies_backup_and_database_suffixes(tmp_path: Path) -> None:
    (tmp_path / "geosys.db.bak-before-migration").write_bytes(b"backup")
    (tmp_path / "geosys.db").write_bytes(b"database")

    result = build_inventory(tmp_path)

    assert result["kinds"] == {"backup": 1, "legacy_database": 1}
