import sqlite3
from pathlib import Path

import pytest

from tools.migration.backup_legacy_sqlite import backup


def test_backup_is_consistent_read_only_and_non_overwriting(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "snapshot.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO sample(value) VALUES ('sensitive')")
        connection.commit()

    result = backup(source, target)

    assert result["sqlite_integrity"] == "ok"
    assert result["raw_paths_included"] is False
    assert result["contains_secret_values"] is False
    assert target.stat().st_mode & 0o777 == 0o400
    with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT count(*) FROM sample").fetchone() == (1,)
    with pytest.raises(FileExistsError):
        backup(source, target)
