from pathlib import Path

import pytest
from geo_platform.config import Settings


def test_default_clickhouse_credentials_match_local_compose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEO_CLICKHOUSE_USER", raising=False)
    monkeypatch.delenv("GEO_CLICKHOUSE_PASSWORD", raising=False)
    settings = Settings(_env_file=None)
    compose = (Path(__file__).parents[2] / "compose.yaml").read_text(encoding="utf-8")

    assert settings.clickhouse_user == "geo"
    assert f"CLICKHOUSE_PASSWORD: {settings.clickhouse_password}" in compose


def test_dev_script_starts_pgvector_and_migrates_before_application_processes() -> None:
    script = (Path(__file__).parents[2] / "scripts/dev.sh").read_text(encoding="utf-8")

    assert "deploy/s02/compose.pgvector.yaml" in script
    assert '"${compose[@]}" up -d --wait' in script
    assert script.index(".venv/bin/alembic upgrade head") < script.index(".venv/bin/uvicorn")
