from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from tools import sync_wukong_relay_env as sync_relay


def _write_private(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def _lease(*, city: str = "北京") -> dict[str, object]:
    return {
        "city": city,
        "endtime": "2099-09-28 11:14:37",
        "username": "user:name",
        "password": "secret/pass",
        "server": "127.0.0.2",
        "port": 3128,
    }


def test_rewrite_env_is_dry_run_by_default_and_preserves_metadata_on_apply(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / "relay.env"
    original = "UPSTREAM_PROXY_URL=http://old.example:3128\nRELAY_PORT=19325\nEXTRA=value\n"
    _write_private(env_path, original)
    before = env_path.stat()

    assert sync_relay._rewrite_env(env_path, "http://new.example:3128", apply=False) == 19325
    assert env_path.read_text(encoding="utf-8") == original

    assert sync_relay._rewrite_env(env_path, "http://new.example:3128", apply=True) == 19325
    assert env_path.read_text(encoding="utf-8") == (
        "UPSTREAM_PROXY_URL=http://new.example:3128\nRELAY_PORT=19325\nEXTRA=value\n"
    )
    after = env_path.stat()
    assert after.st_mode & 0o777 == before.st_mode & 0o777 == 0o600
    assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)


def test_load_lease_requires_one_live_city_match_and_private_cache(tmp_path: Path) -> None:
    cache_path = tmp_path / "leases.json"
    _write_private(cache_path, json.dumps({"leases": [_lease(), _lease()]}))
    with pytest.raises(SystemExit, match="expected exactly one live lease"):
        sync_relay._load_lease(cache_path, "北京市")

    cache_path.chmod(0o644)
    with pytest.raises(SystemExit, match="must not be group/world accessible"):
        sync_relay._load_lease(cache_path, "北京")


def test_main_prints_only_secret_free_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache_path = tmp_path / "leases.json"
    env_path = tmp_path / "relay.env"
    _write_private(cache_path, json.dumps({"leases": [_lease()]}))
    _write_private(env_path, "UPSTREAM_PROXY_URL=http://old.example:3128\nRELAY_PORT=19325\n")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sync_wukong_relay_env.py",
            "--city",
            "北京",
            "--cache",
            os.fspath(cache_path),
            "--env-file",
            os.fspath(env_path),
        ],
    )

    assert sync_relay.main() == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["action"] == "checked"
    assert payload["relay_port"] == 19325
    assert "user:name" not in output
    assert "secret/pass" not in output
    assert env_path.read_text(encoding="utf-8").startswith(
        "UPSTREAM_PROXY_URL=http://old.example:3128"
    )
