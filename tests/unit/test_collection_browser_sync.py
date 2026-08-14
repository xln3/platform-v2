"""collection_browser_sync（GEO_BROWSER_INSTANCES → collection_browser 镜像）单测。

fake Session 照 test_account_governor.py 同款最小假面；env 一律 monkeypatch。
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime
from typing import Any

import pytest
from geo_platform.collection.account_models import CollectionBrowser
from geo_platform.collection.collection_browser_sync import sync_collection_browsers

_NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


class _FakeSession:
    def __init__(self) -> None:
        self.rows: dict[type, list[Any]] = {}
        self._ids: dict[type, itertools.count] = {}

    def scalar(self, stmt: Any) -> Any | None:
        rows = self._select(stmt)
        return rows[0] if rows else None

    def _select(self, stmt: Any) -> list[Any]:
        cls = stmt.column_descriptions[0]["entity"]
        rows = list(self.rows.get(cls, []))
        for criterion in stmt._where_criteria:
            rows = [
                row
                for row in rows
                if getattr(row, criterion.left.key) == criterion.right.value
            ]
        return rows

    def add(self, obj: Any) -> None:
        self.rows.setdefault(type(obj), []).append(obj)

    def flush(self) -> None:
        for cls, rows in self.rows.items():
            counter = self._ids.setdefault(cls, itertools.count(1))
            for row in rows:
                if getattr(row, "id", None) is None:
                    row.id = next(counter)


def _set_instances(monkeypatch: pytest.MonkeyPatch, *keys: str) -> None:
    monkeypatch.setenv("GEO_BROWSER_INSTANCES", ",".join(keys))


def _set_instance_env(
    monkeypatch: pytest.MonkeyPatch, key: str, *, cdp: str = "", exit_gb: str = ""
) -> None:
    upper = key.upper()
    if cdp:
        monkeypatch.setenv(f"GEO_BROWSER_{upper}_CDP_URL", cdp)
    if exit_gb:
        monkeypatch.setenv(f"GEO_BROWSER_{upper}_EXIT_GB", exit_gb)


def test_sync_creates_rows_with_cdp_port(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_instances(monkeypatch, "doubao_bj", "deepseek_sh")
    _set_instance_env(
        monkeypatch, "doubao_bj", cdp="http://127.0.0.1:19233", exit_gb="110000"
    )
    _set_instance_env(
        monkeypatch, "deepseek_sh", cdp="http://127.0.0.1:19234", exit_gb="310000"
    )
    session = _FakeSession()
    summary = sync_collection_browsers(session)  # type: ignore[arg-type]
    assert summary == {
        "synced": 2,
        "created": 2,
        "updated": 0,
        "errors": [],
        "instances": ["doubao_bj", "deepseek_sh"],
    }
    rows = session.rows[CollectionBrowser]
    assert [row.instance_key for row in rows] == ["doubao_bj", "deepseek_sh"]
    doubao = rows[0]
    assert doubao.platform == "doubao"
    assert doubao.region_gb == "110000"
    assert doubao.cdp_port == 19233
    assert doubao.systemd_unit == "geo-platform-v2-browser@doubao_bj.service"
    assert doubao.activity == "idle"


def test_sync_idempotent_no_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_instances(monkeypatch, "doubao_bj")
    _set_instance_env(monkeypatch, "doubao_bj", cdp="http://127.0.0.1:19233", exit_gb="110000")
    session = _FakeSession()
    sync_collection_browsers(session)  # type: ignore[arg-type]
    summary = sync_collection_browsers(session)  # type: ignore[arg-type]
    assert summary["created"] == 0
    assert summary["updated"] == 1
    assert len(session.rows[CollectionBrowser]) == 1


def test_sync_updates_env_truth(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_instances(monkeypatch, "doubao_bj")
    _set_instance_env(monkeypatch, "doubao_bj", cdp="http://127.0.0.1:19233", exit_gb="110000")
    session = _FakeSession()
    sync_collection_browsers(session)  # type: ignore[arg-type]
    # env 改了端口/出口 → 再同步覆盖（env 是部署真源）
    monkeypatch.setenv("GEO_BROWSER_DOUBAO_BJ_CDP_URL", "http://127.0.0.1:19333")
    monkeypatch.setenv("GEO_BROWSER_DOUBAO_BJ_EXIT_GB", "120000")
    summary = sync_collection_browsers(session)  # type: ignore[arg-type]
    assert summary["updated"] == 1
    row = session.rows[CollectionBrowser][0]
    assert row.cdp_port == 19333
    assert row.region_gb == "120000"


def test_sync_missing_list_fail_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEO_BROWSER_INSTANCES", raising=False)
    with pytest.raises(ValueError, match="browser_instances_not_configured"):
        sync_collection_browsers(_FakeSession())  # type: ignore[arg-type]


def test_sync_malformed_entries_skipped_with_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_instances(monkeypatch, "doubao_bj,Bad-Key,deepseek_sh,yiyan_tj")
    _set_instance_env(monkeypatch, "doubao_bj", cdp="http://127.0.0.1:19233", exit_gb="110000")
    # Bad-Key：键非法；deepseek_sh：缺 CDP/EXIT_GB；yiyan_tj：EXIT_GB 非 6 位
    _set_instance_env(monkeypatch, "yiyan_tj", cdp="http://127.0.0.1:19240", exit_gb="12")
    session = _FakeSession()
    summary = sync_collection_browsers(session)  # type: ignore[arg-type]
    assert summary["synced"] == 1
    assert summary["instances"] == ["doubao_bj"]
    assert sorted(summary["errors"]) == [
        "invalid_cdp_url:deepseek_sh",
        "invalid_exit_gb:yiyan_tj",
        "invalid_instance_key:Bad-Key",
    ]


def test_sync_cdp_port_default_by_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_instances(monkeypatch, "doubao_bj")
    _set_instance_env(monkeypatch, "doubao_bj", cdp="http://browser.internal", exit_gb="110000")
    session = _FakeSession()
    sync_collection_browsers(session)  # type: ignore[arg-type]
    assert session.rows[CollectionBrowser][0].cdp_port == 80
