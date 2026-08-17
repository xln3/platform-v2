"""relay 巡检（collection/relay_probe.py + business_metrics 15s 循环钩子）单测。

- probe：httpx 缝 ``relay_probe._fetch_exit_ip`` 与推送缝
  ``relay_probe.push_captcha_assist`` 一律 monkeypatch——不出网、不真推。
- business_metrics 钩子：SessionLocal 指 fake CM，probe 缝 monkeypatch，
  验证「每 region 每 10 分钟巡一次」的内存节流与失败不拖垮主循环。
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime
from typing import Any

import pytest
from geo_platform import business_metrics
from geo_platform.collection import relay_probe
from geo_platform.collection.account_models import (
    CollectionAccountEvent,
    CollectionRegion,
)
from geo_platform.collection.relay_probe import probe_collection_region
from geo_platform.tenancy.ids import new_pub_id

_NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


class _FakeSession:
    """governor/probe 查询的最小假面（equality where）。"""

    def __init__(self) -> None:
        self.rows: dict[type, list[Any]] = {}
        self._ids: dict[type, itertools.count] = {}
        self.committed = 0
        self.rolled_back = 0

    def scalar(self, stmt: Any) -> Any | None:
        rows = self._select(stmt)
        return rows[0] if rows else None

    def scalars(self, stmt: Any) -> list[Any]:
        return list(self._select(stmt))

    def _select(self, stmt: Any) -> list[Any]:
        cls = stmt.column_descriptions[0]["entity"]
        rows = list(self.rows.get(cls, []))
        for criterion in stmt._where_criteria:
            rows = [
                row for row in rows if getattr(row, criterion.left.key) == criterion.right.value
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

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def _seed_region(session: _FakeSession, **over: Any) -> CollectionRegion:
    fields: dict[str, Any] = {
        "pub_id": new_pub_id("rgn"),
        "region_gb": "110000",
        "state": "ok",
        "proxy_env_key": "GEO_PROXY_BJ",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    fields.update(over)
    row = CollectionRegion(**fields)
    session.add(row)
    session.flush()
    return row


def _events(session: _FakeSession) -> list[CollectionAccountEvent]:
    return list(session.rows.get(CollectionAccountEvent, []))


# ── probe_collection_region ─────────────────────────────────────────────────


def test_probe_region_not_found() -> None:
    with pytest.raises(LookupError, match="region_not_found"):
        probe_collection_region(_FakeSession(), "659999")  # type: ignore[arg-type]


def test_probe_proxy_env_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    region = _seed_region(session, proxy_env_key=None)
    result = probe_collection_region(session, "110000")  # type: ignore[arg-type]
    assert result == {
        "region_gb": "110000",
        "ok": False,
        "exit_ip": None,
        "note": "proxy_env_missing",
        "alerted": False,
    }
    assert region.state == "down"  # governor 落库：ok=False → down
    assert region.last_probe_at is not None
    # env 键配了但值缺失同口径
    region2 = _seed_region(session, region_gb="120000", proxy_env_key="GEO_PROXY_MISSING")
    monkeypatch.delenv("GEO_PROXY_MISSING", raising=False)
    result2 = probe_collection_region(session, "120000")  # type: ignore[arg-type]
    assert result2["ok"] is False
    assert result2["note"] == "proxy_env_missing"
    assert region2.state == "down"


def test_probe_success_records_exit_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    region = _seed_region(session, state="down")  # 从 down 翻回 ok 应记事件
    monkeypatch.setenv("GEO_PROXY_BJ", "http://127.0.0.1:17890")
    monkeypatch.setattr(relay_probe, "_fetch_exit_ip", lambda proxy: "106.37.143.183")
    result = probe_collection_region(session, "110000")  # type: ignore[arg-type]
    assert result == {
        "region_gb": "110000",
        "ok": True,
        "exit_ip": "106.37.143.183",
        "note": None,
        "alerted": False,
    }
    assert region.state == "ok"
    assert region.exit_ip_last == "106.37.143.183"
    events = _events(session)
    assert len(events) == 1  # 状态翻转 → relay_probe 事件
    assert events[0].event_type == "relay_probe"
    assert events[0].old_value == {"state": "down"}
    assert events[0].new_value == {"state": "ok", "exit_ip": "106.37.143.183"}


def test_probe_failure_pushes_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    region = _seed_region(session)
    monkeypatch.setenv("GEO_PROXY_BJ", "http://127.0.0.1:17890")
    monkeypatch.setenv("GEO_ASSIST_NOTIFY_URL", "https://sctapi.ftqq.com/KEY.send")
    monkeypatch.setenv("GEO_ASSIST_NOTIFY_FLAVOR", "serverchan")

    def boom(proxy: str) -> str:
        raise TimeoutError("connect timeout")

    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(relay_probe, "_fetch_exit_ip", boom)
    monkeypatch.setattr(
        relay_probe, "push_captcha_assist", lambda **kw: sent.append(kw) is None or True
    )
    result = probe_collection_region(session, "110000")  # type: ignore[arg-type]
    assert result["ok"] is False
    assert result["note"] == "probe_failed:TimeoutError"
    assert result["alerted"] is True
    assert region.state == "down"
    assert len(sent) == 1
    assert sent[0]["flavor"] == "serverchan"
    assert "110000" in sent[0]["title"]
    events = _events(session)
    assert events[0].event_type == "relay_probe"
    assert events[0].evidence == "probe_failed:TimeoutError"


def test_probe_failure_alert_unconfigured_only_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    _seed_region(session)
    monkeypatch.setenv("GEO_PROXY_BJ", "http://127.0.0.1:17890")
    monkeypatch.delenv("GEO_ASSIST_NOTIFY_URL", raising=False)

    def boom(proxy: str) -> str:
        raise OSError("refused")

    monkeypatch.setattr(relay_probe, "_fetch_exit_ip", boom)
    result = probe_collection_region(session, "110000")  # type: ignore[arg-type]
    assert result["ok"] is False
    assert result["alerted"] is False  # 未配置只日志，如实返回


def test_probe_repeated_failure_does_not_repeat_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    region = _seed_region(session, state="down")
    monkeypatch.setenv("GEO_PROXY_BJ", "http://127.0.0.1:17890")
    monkeypatch.setenv("GEO_ASSIST_NOTIFY_URL", "https://sctapi.ftqq.com/KEY.send")

    def boom(proxy: str) -> str:
        raise TimeoutError("still down")

    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(relay_probe, "_fetch_exit_ip", boom)
    monkeypatch.setattr(
        relay_probe, "push_captcha_assist", lambda **kw: sent.append(kw) is None or True
    )

    result = probe_collection_region(session, "110000")  # type: ignore[arg-type]

    assert result["ok"] is False
    assert result["note"] == "probe_failed:TimeoutError"
    assert result["alerted"] is False
    assert region.state == "down"
    assert sent == []
    assert _events(session) == []  # 无状态翻转，不重复写事件或推送


def test_probe_arrears_not_overwritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    region = _seed_region(session, state="arrears")  # 人工标注欠费不被探测覆盖
    monkeypatch.setenv("GEO_PROXY_BJ", "http://127.0.0.1:17890")
    monkeypatch.setattr(relay_probe, "_fetch_exit_ip", lambda proxy: "1.2.3.4")
    result = probe_collection_region(session, "110000")  # type: ignore[arg-type]
    assert result["ok"] is True
    assert region.state == "arrears"
    assert _events(session) == []  # 无翻转无事件


# ── business_metrics 循环钩子 ────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_probe_clock() -> Any:
    business_metrics._region_probe_last.clear()
    yield
    business_metrics._region_probe_last.clear()


def test_metrics_hook_probes_each_region_once_per_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    _seed_region(session, region_gb="110000")
    _seed_region(session, region_gb="120000")
    monkeypatch.setattr(business_metrics, "SessionLocal", lambda: session)
    probed: list[str] = []
    monkeypatch.setattr(
        business_metrics,
        "probe_collection_region",
        lambda conn, region_gb: probed.append(region_gb) or {"ok": True},
    )
    business_metrics.probe_due_collection_regions(1000.0)
    assert sorted(probed) == ["110000", "120000"]
    assert session.committed == 2
    # 间隔内再巡 = 全部跳过
    business_metrics.probe_due_collection_regions(1000.0 + 60)
    assert len(probed) == 2
    # 过 10 分钟窗口 → 重巡
    business_metrics.probe_due_collection_regions(1000.0 + 601)
    assert len(probed) == 4


def test_metrics_hook_region_failure_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    _seed_region(session, region_gb="110000")
    _seed_region(session, region_gb="120000")
    monkeypatch.setattr(business_metrics, "SessionLocal", lambda: session)

    def flaky(conn: Any, region_gb: str) -> dict[str, Any]:
        if region_gb == "110000":
            raise RuntimeError("db down")
        return {"ok": True}

    monkeypatch.setattr(business_metrics, "probe_collection_region", flaky)
    business_metrics.probe_due_collection_regions(2000.0)  # 不抛
    assert session.rolled_back == 1  # 失败 region 回滚
    assert session.committed == 1  # 另一 region 照常提交


def test_metrics_hook_db_unreachable_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def dead_factory() -> Any:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(business_metrics, "SessionLocal", dead_factory)
    business_metrics.probe_due_collection_regions(3000.0)  # 不抛，只日志
