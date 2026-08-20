"""OTP 迁库旁路（collection/otp_bridge.py + otp/router.py 两个钩子）单测。

- otp_bridge 语义：fake Session 直测 upsert/回填。
- router 钩子：TestClient + monkeypatch ``otp_router.SessionLocal`` 指向 fake
  session（绝不连真 PG）；另验证 best-effort——DB 挂掉推送/注册照常 200。
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from geo_platform.collection.account_models import CollectionPhoneAccount
from geo_platform.collection.otp_bridge import record_sms_received, upsert_phone_account
from geo_platform.main import app
from geo_platform.otp import router as otp_router

_NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

PHONE = "13121622231"
SLOT = f"SIM1_中国联通_+86{PHONE}"
SMS = "【豆包】你的验证码 458213，5分钟内有效。"

client = TestClient(app)


class _FakeSession:
    def __init__(self) -> None:
        self.rows: dict[type, list[Any]] = {}
        self._ids: dict[type, itertools.count] = {}
        self.committed = 0

    def scalar(self, stmt: Any) -> Any | None:
        cls = stmt.column_descriptions[0]["entity"]
        rows = list(self.rows.get(cls, []))
        for criterion in stmt._where_criteria:
            rows = [
                row for row in rows if getattr(row, criterion.left.key) == criterion.right.value
            ]
        return rows[0] if rows else None

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

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


@pytest.fixture
def session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture(autouse=True)
def _otp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """照 test_otp_router.py：收件箱/注册表指 tmp、双 token 配好、频控桶清空。"""
    monkeypatch.setenv("GEO_OTP_INBOX_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_OTP_REGISTRY_PATH", str(tmp_path / "reg" / "registered.json"))
    monkeypatch.setenv("GEO_OTP_RELAY_TOKEN", "relay-secret")
    monkeypatch.setenv("GEO_OTP_OPERATOR_TOKEN", "operator-secret")
    with otp_router._rate_lock:
        otp_router._rate_buckets.clear()
    yield


# ── otp_bridge 语义 ─────────────────────────────────────────────────────────


def test_upsert_creates_row_with_owner_note(session: _FakeSession) -> None:
    row = upsert_phone_account(session, phone=PHONE, owner_note="SIM1 中国联通")  # type: ignore[arg-type]
    assert row.phone == PHONE
    assert row.owner_note == "SIM1 中国联通"
    assert row.state == "active"
    assert row.sms_link_state == "untested"
    assert row.push_link_state == "untested"
    assert row.pub_id.startswith("pha_")


def test_upsert_existing_updates_only_owner_note(session: _FakeSession) -> None:
    row = upsert_phone_account(session, phone=PHONE, owner_note="旧备注")  # type: ignore[arg-type]
    row.sms_link_state = "ok"  # 链路事实不能被注册动作覆盖
    row.last_sms_at = _NOW
    again = upsert_phone_account(session, phone=PHONE, owner_note="新备注")  # type: ignore[arg-type]
    assert again is row  # phone 唯一，不新建
    assert row.owner_note == "新备注"
    assert row.sms_link_state == "ok"
    assert row.last_sms_at == _NOW
    assert len(session.rows[CollectionPhoneAccount]) == 1


def test_record_sms_received_backfills(session: _FakeSession) -> None:
    upsert_phone_account(session, phone=PHONE, owner_note=None)  # type: ignore[arg-type]
    assert record_sms_received(session, phone=PHONE) is True  # type: ignore[arg-type]
    row = session.rows[CollectionPhoneAccount][0]
    assert row.sms_link_state == "ok"
    assert row.last_sms_at is not None


def test_record_sms_received_unknown_phone_no_create(session: _FakeSession) -> None:
    assert record_sms_received(session, phone="15510162660") is False  # type: ignore[arg-type]
    assert session.rows.get(CollectionPhoneAccount, []) == []  # 不建档


# ── router 钩子（TestClient 端到端，SessionLocal 指 fake）────────────────────


def test_push_backfills_phone_account(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(otp_router, "SessionLocal", lambda: session)
    resp = client.post(
        "/api/v2/otp/push",
        content=json.dumps({"slot": SLOT, "sms": SMS}, ensure_ascii=False),
        headers={"X-Relay-Token": "relay-secret", "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["routed"] is True
    rows = session.rows.get(CollectionPhoneAccount, [])
    # 未在册号 → 不建档（回填不是注册）
    assert rows == []


def test_push_backfills_registered_phone(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    upsert_phone_account(session, phone=PHONE, owner_note="SIM1")  # type: ignore[arg-type]
    monkeypatch.setattr(otp_router, "SessionLocal", lambda: session)
    resp = client.post(
        "/api/v2/otp/push",
        content=json.dumps({"slot": SLOT, "sms": SMS}, ensure_ascii=False),
        headers={"X-Relay-Token": "relay-secret", "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    row = session.rows[CollectionPhoneAccount][0]
    assert row.sms_link_state == "ok"
    assert row.last_sms_at is not None
    assert session.committed >= 1


def test_push_db_failure_still_200(monkeypatch: pytest.MonkeyPatch) -> None:
    def dead() -> Any:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(otp_router, "SessionLocal", dead)
    resp = client.post(
        "/api/v2/otp/push",
        content=json.dumps({"slot": SLOT, "sms": SMS}, ensure_ascii=False),
        headers={"X-Relay-Token": "relay-secret", "Content-Type": "application/json"},
    )
    assert resp.status_code == 200  # best-effort：文件链路不受影响
    assert resp.json()["have_code"] is True


def test_register_upserts_phone_account(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(otp_router, "SessionLocal", lambda: session)
    resp = client.post(
        "/api/v2/otp/register",
        content=json.dumps({"phone": PHONE, "carrier": "中国联通", "slot": "SIM1"}),
        headers={"X-Operator-Token": "operator-secret", "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["created"] is True
    rows = session.rows.get(CollectionPhoneAccount, [])
    assert len(rows) == 1
    assert rows[0].phone == PHONE
    assert rows[0].owner_note == "SIM1 中国联通"  # slot/carrier 拼接
    # 同号再注册 = 更新备注，不新建
    resp2 = client.post(
        "/api/v2/otp/register",
        content=json.dumps({"phone": PHONE, "carrier": "中国移动", "slot": "SIM2"}),
        headers={"X-Operator-Token": "operator-secret", "Content-Type": "application/json"},
    )
    assert resp2.status_code == 200
    assert len(session.rows[CollectionPhoneAccount]) == 1
    assert rows[0].owner_note == "SIM2 中国移动"


def test_register_db_failure_still_200(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def dead() -> Any:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(otp_router, "SessionLocal", dead)
    resp = client.post(
        "/api/v2/otp/register",
        content=json.dumps({"phone": PHONE}),
        headers={"X-Operator-Token": "operator-secret", "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["created"] is True  # 文件注册表照常落
