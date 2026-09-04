"""采集账号治理 API（api/geo_platform/collection/account_admin_router.py）单元测试。

全 fake：TestClient 进程内 + dependency_overrides[get_principal]/[get_db]；
_FakeSession 照 test_account_governor.py 同款（select 结构路由到内存行），
扩展 in_ 谓词支持（events 端点的 platform_account_id.in_ 查询）。
systemd 实采缝（probe_browser_runtime）与推送缝（push_captcha_assist）一律
monkeypatch——不起 systemctl、不出网。
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from geo_platform.collection import account_admin_router
from geo_platform.collection.account_models import (
    CollectionAccountEvent,
    CollectionBrowser,
    CollectionPhoneAccount,
    CollectionPlatformAccount,
    CollectionRegion,
)
from geo_platform.collection.models import BrowserFence
from geo_platform.identity.policy import Principal, Role, get_principal
from geo_platform.main import app
from geo_platform.tenancy.database import get_db
from geo_platform.tenancy.ids import new_pub_id
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList, Grouping

_NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

client = TestClient(app)


class _FakeSession:
    """account_admin_router 用到的最小 Session 假面（equality + in_ + order_by + limit）。"""

    def __init__(self) -> None:
        self.rows: dict[type, list[Any]] = {}
        self._ids: dict[type, itertools.count] = {}
        self.committed = 0

    def get(self, cls: type, pk: Any) -> Any | None:
        for row in self.rows.get(cls, []):
            if row.id == pk:
                return row
        return None

    def scalar(self, stmt: Any) -> Any | None:
        rows = self._select(stmt)
        if getattr(stmt._raw_columns[0], "name", None) == "count":
            return len(rows)
        return rows[0] if rows else None

    def scalars(self, stmt: Any) -> list[Any]:
        return list(self._select(stmt))

    def _select(self, stmt: Any) -> list[Any]:
        cls = stmt.column_descriptions[0]["entity"]
        if cls is None:
            table_name = stmt.get_final_froms()[0].name
            cls = next(
                candidate
                for candidate in (
                    CollectionPhoneAccount,
                    CollectionPlatformAccount,
                    CollectionRegion,
                    CollectionBrowser,
                    CollectionAccountEvent,
                    BrowserFence,
                )
                if getattr(candidate, "__tablename__", None) == table_name
            )
        rows = list(self.rows.get(cls, []))
        for criterion in stmt._where_criteria:
            rows = [row for row in rows if self._matches(criterion, row)]
        for order in reversed(stmt._order_by_clauses):
            element = getattr(order, "element", order)  # 裸列（asc）无 .element 包装
            key = element.key
            desc = "desc" in str(getattr(order, "modifier", ""))
            rows.sort(key=lambda row: getattr(row, key), reverse=desc)
        if stmt._limit is not None:
            rows = rows[: stmt._limit]
        return rows

    def _matches(self, criterion: Any, row: Any) -> bool:
        if isinstance(criterion, Grouping):
            return self._matches(criterion.element, row)
        if isinstance(criterion, BooleanClauseList):
            values = [self._matches(clause, row) for clause in criterion.clauses]
            return any(values) if criterion.operator.__name__ == "or_" else all(values)
        if not isinstance(criterion, BinaryExpression):
            raise AssertionError(f"unsupported fake predicate: {criterion!s}")
        left = getattr(row, criterion.left.key)
        right = criterion.right.value
        operator = criterion.operator.__name__
        if operator == "eq":
            return left == right
        if operator == "lt":
            return left < right
        if operator == "in_op":
            return left in right
        raise AssertionError(f"unsupported fake operator: {operator}")

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
        pass


@pytest.fixture
def session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture(autouse=True)
def _cleanup_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.pop(get_principal, None)
    app.dependency_overrides.pop(get_db, None)


def _bind(session: _FakeSession, role: Role | None = Role.OPERATOR) -> None:
    if role is not None:
        app.dependency_overrides[get_principal] = lambda: Principal(
            subject="ops-1", role=role, tenant_pub_id="tnt_ops", user_pub_id="usr_ops"
        )
    app.dependency_overrides[get_db] = lambda: session


def _seed_phone(session: _FakeSession, **over: Any) -> CollectionPhoneAccount:
    fields: dict[str, Any] = {
        "pub_id": new_pub_id("pha"),
        "phone": "13121622231",
        "owner_note": "SIM1 联通",
        "state": "active",
        "sms_link_state": "untested",
        "push_link_state": "untested",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    fields.update(over)
    row = CollectionPhoneAccount(**fields)
    session.add(row)
    session.flush()
    return row


def _seed_platform(session: _FakeSession, phone_id: int, **over: Any) -> CollectionPlatformAccount:
    fields: dict[str, Any] = {
        "pub_id": new_pub_id("ppa"),
        "phone_account_id": phone_id,
        "platform": "doubao",
        "region_gb": "310000",
        "runtime_state": "idle",
        "used_today": 0,
        "used_week": 0,
        "used_year": 0,
        "browser_instance_key": "doubao_sh",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    fields.update(over)
    row = CollectionPlatformAccount(**fields)
    session.add(row)
    session.flush()
    return row


def _seed_browser(session: _FakeSession, **over: Any) -> CollectionBrowser:
    fields: dict[str, Any] = {
        "pub_id": new_pub_id("brw"),
        "instance_key": "doubao_sh",
        "platform": "doubao",
        "region_gb": "310000",
        "cdp_port": 19222,
        "systemd_unit": "geo-platform-v2-browser@doubao_sh.service",
        "activity": "idle",
        "error_streak": 0,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    fields.update(over)
    row = CollectionBrowser(**fields)
    session.add(row)
    session.flush()
    return row


def _seed_region(session: _FakeSession, **over: Any) -> CollectionRegion:
    fields: dict[str, Any] = {
        "pub_id": new_pub_id("rgn"),
        "region_gb": "310000",
        "state": "ok",
        "source": "wukong",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    fields.update(over)
    row = CollectionRegion(**fields)
    session.add(row)
    session.flush()
    return row


def _events(session: _FakeSession, event_type: str | None = None) -> list[CollectionAccountEvent]:
    rows = session.rows.get(CollectionAccountEvent, [])
    if event_type is None:
        return list(rows)
    return [row for row in rows if row.event_type == event_type]


def _seed_quota_observation(
    session: _FakeSession,
    browser_id: int,
    phone_id: int | None,
    *,
    created_at: datetime,
    payload: dict[str, Any],
) -> CollectionAccountEvent:
    row = CollectionAccountEvent(
        pub_id=new_pub_id("aev"),
        browser_id=browser_id,
        phone_account_id=phone_id,
        event_type="quota_observation",
        actor="quota_reconstruction",
        new_value=payload,
        evidence="raw evidence must stay server-side",
        created_at=created_at,
    )
    session.add(row)
    session.flush()
    return row


# ── 身份门 ──────────────────────────────────────────────────────────────────


def test_list_accounts_requires_identity_401(session: _FakeSession) -> None:
    _bind(session, role=None)  # 不 override get_principal → 真身份链 → 无头 401
    resp = client.get("/api/v2/collection-accounts")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "identity_headers_missing"


def test_list_accounts_forbidden_role_403(session: _FakeSession) -> None:
    _bind(session, role=Role.CUSTOMER)  # customer 无 account:read
    assert client.get("/api/v2/collection-accounts").status_code == 403


def test_quota_observations_latest_per_phone_platform_mode_and_safe_projection(
    session: _FakeSession,
) -> None:
    sh_phone = _seed_phone(session)
    bj_phone = _seed_phone(session, phone="18810936058", owner_note="北京豆包号")
    sh = _seed_browser(session)
    bj = _seed_browser(
        session,
        instance_key="doubao_bj",
        region_gb="110000",
        cdp_port=19233,
    )
    base = {
        "schema_version": 1,
        "mode": "deep_think",
        "account_tier": "free",
        "quota_state": "exhausted",
        "window_type": "rolling",
        "window_days": 7,
        "count_kind": "lower_bound",
        "reset_at": "2026-08-20T08:11:51.143+00:00",
        "source": "platform_and_logs",
        # 原始观测允许留在审计事件，但 API 必须只做白名单投影。
        "raw_sse": "SECRET_PLATFORM_RESPONSE",
        "phone": "18800006058",
    }
    _seed_quota_observation(
        session,
        bj.id,
        sh_phone.id,
        created_at=_NOW,
        payload={**base, "observed_window_count": 20},
    )
    latest = _seed_quota_observation(
        session,
        sh.id,
        sh_phone.id,
        created_at=_NOW + timedelta(minutes=1),
        payload={**base, "observed_window_count": 26},
    )
    _seed_quota_observation(
        session,
        bj.id,
        bj_phone.id,
        created_at=_NOW + timedelta(minutes=2),
        payload={
            "schema_version": 1,
            "mode": "deep_think",
            "account_tier": "subscriber",
            "quota_state": "available",
            "window_type": "rolling",
            "window_days": 7,
            "source": "platform",
        },
    )
    # 新协议才能展示；畸形/旧协议事件不能覆盖有效观测。
    _seed_quota_observation(
        session,
        sh.id,
        sh_phone.id,
        created_at=_NOW + timedelta(minutes=3),
        payload={"schema_version": 0, "mode": "deep_think"},
    )
    # 即使 browser/region 与有效观测相同，没有手机号归属也绝不能展示成账号额度。
    _seed_quota_observation(
        session,
        sh.id,
        None,
        created_at=_NOW + timedelta(minutes=4),
        payload={**base, "observed_window_count": 999},
    )

    _bind(session)
    resp = client.get("/api/v2/collection-account-quota-observations")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    sh_row = next(row for row in rows if row["phone_account_pub_id"] == sh_phone.pub_id)
    assert sh_row == {
        "observation_pub_id": latest.pub_id,
        "phone_account_pub_id": sh_phone.pub_id,
        "phone_masked": "131***2231",
        "platform": "doubao",
        "observed_browser_instance_key": "doubao_sh",
        "observed_region_gb": "310000",
        "mode": "deep_think",
        "account_tier": "free",
        "quota_state": "exhausted",
        "window_type": "rolling",
        "window_days": 7,
        "observed_window_count": 26,
        "daily_equivalent": 3.7,
        "count_kind": "lower_bound",
        "reset_at": "2026-08-20T08:11:51.143000Z",
        "observed_at": "2026-08-13T12:01:00Z",
        "source": "platform_and_logs",
    }
    bj_row = next(row for row in rows if row["phone_account_pub_id"] == bj_phone.pub_id)
    assert bj_row["phone_masked"] == "188***6058"
    assert bj_row["observed_browser_instance_key"] == "doubao_bj"
    assert bj_row["observed_region_gb"] == "110000"
    assert bj_row["account_tier"] == "subscriber"
    assert bj_row["quota_state"] == "available"
    assert bj_row["observed_window_count"] is None
    assert bj_row["daily_equivalent"] is None
    assert bj_row["count_kind"] == "unknown"
    assert "SECRET_PLATFORM_RESPONSE" not in resp.text
    assert "18800006058" not in resp.text
    assert "raw evidence" not in resp.text


def test_quota_observations_require_account_read(session: _FakeSession) -> None:
    _bind(session, role=Role.CUSTOMER)
    assert client.get("/api/v2/collection-account-quota-observations").status_code == 403


# ── GET/POST /collection-accounts ───────────────────────────────────────────


def test_list_accounts_fixed_five_platform_columns(session: _FakeSession) -> None:
    phone = _seed_phone(session)
    account = _seed_platform(session, phone.id, quota_day=50, used_today=3)
    _bind(session)
    resp = client.get("/api/v2/collection-accounts")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["phone_account_pub_id"] == phone.pub_id
    assert row["phone"] == "13121622231"
    assert row["phone_masked"] == "131***2231"
    assert row["owner_note"] == "SIM1 联通"
    assert row["state"] == "active"
    assert row["sms_link_state"] == "untested"
    assert row["last_sms_at"] is None
    assert row["push_link_state"] == "untested"
    assert row["last_push_test_at"] is None
    # 五平台固定列，无行 = null
    assert set(row["platforms"]) == {"doubao", "yiyan", "deepseek", "yuanbao", "tongyi"}
    cell = row["platforms"]["doubao"]
    assert cell["platform_account_pub_id"] == account.pub_id
    assert cell["region_gb"] == "310000"
    assert cell["quota_day"] == 50
    assert cell["used_today"] == 3
    assert cell["runtime_state"] == "idle"
    assert cell["browser_instance_key"] == "doubao_sh"
    for slug in ("yiyan", "deepseek", "yuanbao", "tongyi"):
        assert row["platforms"][slug] is None


def test_collection_account_cursor_reaches_fifth_and_ninth_rows(session: _FakeSession) -> None:
    expected = []
    for index in range(9):
        row = _seed_phone(
            session,
            pub_id=f"pha_page_{index:02d}",
            phone=f"1312162{index:04d}",
            created_at=_NOW,
        )
        expected.append(row.pub_id)
    expected.sort(reverse=True)
    _bind(session)

    first = client.get("/api/v2/collection-accounts", params={"limit": 4})
    assert first.status_code == 200, first.text
    assert [row["phone_account_pub_id"] for row in first.json()] == expected[:4]
    assert first.headers["X-Total-Count"] == "9"
    assert first.headers["X-Has-More"] == "true"

    second = client.get(
        "/api/v2/collection-accounts",
        params={"limit": 4, "cursor": first.headers["X-Next-Cursor"]},
    )
    assert [row["phone_account_pub_id"] for row in second.json()] == expected[4:8]
    third = client.get(
        "/api/v2/collection-accounts",
        params={"limit": 4, "cursor": second.headers["X-Next-Cursor"]},
    )
    assert [row["phone_account_pub_id"] for row in third.json()] == expected[8:]
    assert third.headers["X-Has-More"] == "false"
    assert "X-Next-Cursor" not in third.headers


def test_read_only_reviewer_keeps_phone_masked(session: _FakeSession) -> None:
    _seed_phone(session)
    _bind(session, role=Role.REVIEWER)

    resp = client.get("/api/v2/collection-accounts")

    assert resp.status_code == 200
    assert resp.json()[0]["phone"] is None
    assert resp.json()[0]["phone_masked"] == "131***2231"
    assert "13121622231" not in resp.text


def test_create_account_then_conflict_409(session: _FakeSession) -> None:
    _bind(session)
    resp = client.post("/api/v2/collection-accounts", json={"phone": "13121622231"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["phone"] == "13121622231"
    assert body["phone_masked"] == "131***2231"
    assert set(body["platforms"]) == {"doubao", "yiyan", "deepseek", "yuanbao", "tongyi"}
    assert all(cell is None for cell in body["platforms"].values())
    events = _events(session, "phone_account_created")
    assert len(events) == 1
    assert events[0].actor == "usr_ops"
    # 幂等冲突
    conflict = client.post("/api/v2/collection-accounts", json={"phone": "13121622231"})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "phone_already_exists"
    # 畸形号
    bad = client.post("/api/v2/collection-accounts", json={"phone": "123"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "bad_phone"


def test_patch_account_note_updates_clears_and_audits(session: _FakeSession) -> None:
    phone = _seed_phone(session, owner_note="旧备注")
    account = _seed_platform(session, phone.id)
    _bind(session)

    updated = client.patch(
        f"/api/v2/collection-accounts/{phone.pub_id}",
        json={"owner_note": "  张杰  "},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["owner_note"] == "张杰"
    assert body["platforms"]["doubao"]["platform_account_pub_id"] == account.pub_id
    assert phone.owner_note == "张杰"
    events = _events(session, "phone_account_note_changed")
    assert len(events) == 1
    assert events[0].actor == "usr_ops"
    assert events[0].old_value == {"owner_note": "旧备注"}
    assert events[0].new_value == {"owner_note": "张杰"}

    cleared = client.patch(
        f"/api/v2/collection-accounts/{phone.pub_id}",
        json={"owner_note": "   "},
    )
    assert cleared.status_code == 200
    assert cleared.json()["owner_note"] is None
    assert phone.owner_note is None
    assert len(_events(session, "phone_account_note_changed")) == 2


def test_patch_account_note_requires_operate_and_valid_payload(session: _FakeSession) -> None:
    phone = _seed_phone(session)
    _bind(session, role=Role.REVIEWER)
    forbidden = client.patch(
        f"/api/v2/collection-accounts/{phone.pub_id}",
        json={"owner_note": "陈亮"},
    )
    assert forbidden.status_code == 403

    _bind(session)
    missing = client.patch(
        "/api/v2/collection-accounts/pha_missing",
        json={"owner_note": "陈亮"},
    )
    assert missing.status_code == 404
    too_long = client.patch(
        f"/api/v2/collection-accounts/{phone.pub_id}",
        json={"owner_note": "x" * 201},
    )
    assert too_long.status_code == 422


def test_create_platform_account_requires_confirmation_and_binds_safely(
    session: _FakeSession,
) -> None:
    phone = _seed_phone(session)
    region = _seed_region(session)
    browser = _seed_browser(session)
    _bind(session)
    payload = {
        "platform": "doubao",
        "region_gb": "310000",
        "browser_instance_key": "doubao_sh",
        "quota_day": 30,
    }

    refused = client.post(
        f"/api/v2/collection-accounts/{phone.pub_id}/platform-accounts",
        json=payload,
    )
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "platform_account_binding_requires_confirmation"

    created = client.post(
        f"/api/v2/collection-accounts/{phone.pub_id}/platform-accounts",
        json=payload | {"confirm": True},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["phone_account_pub_id"] == phone.pub_id
    assert body["platform_account_pub_id"].startswith("pac_")
    assert body["region_gb"] == "310000"
    assert body["browser_instance_key"] == "doubao_sh"
    assert body["quota_day"] == 30
    assert body["runtime_state"] == "idle"
    event = _events(session, "platform_account_created")[0]
    assert event.phone_account_id == phone.id
    assert event.platform_account_id is not None
    assert event.browser_id == browser.id
    assert event.region_id == region.id
    assert event.actor == "usr_ops"
    assert event.new_value == {
        "platform": "doubao",
        "region_gb": "310000",
        "browser_instance_key": "doubao_sh",
        "quota_day": 30,
        "quota_week": None,
        "quota_year": None,
    }

    duplicate = client.post(
        f"/api/v2/collection-accounts/{phone.pub_id}/platform-accounts",
        json=payload | {"confirm": True},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "platform_account_already_exists"


def test_create_platform_account_maps_concurrent_browser_binding_to_409(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    phone = _seed_phone(session)
    _seed_region(session)
    _seed_browser(session)
    _bind(session)
    original_flush = session.flush
    rolled_back: list[bool] = []

    def conflicting_flush() -> None:
        pending = [row for row in session.rows.get(CollectionPlatformAccount, []) if row.id is None]
        if pending:
            original = RuntimeError("unique violation")
            original.diag = SimpleNamespace(  # type: ignore[attr-defined]
                constraint_name="uq_collection_platform_account_browser_instance_key"
            )
            raise IntegrityError("INSERT", {}, original)
        original_flush()

    monkeypatch.setattr(session, "flush", conflicting_flush)
    monkeypatch.setattr(session, "rollback", lambda: rolled_back.append(True))

    response = client.post(
        f"/api/v2/collection-accounts/{phone.pub_id}/platform-accounts",
        json={
            "platform": "doubao",
            "region_gb": "310000",
            "browser_instance_key": "doubao_sh",
            "confirm": True,
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "browser_already_bound"
    assert rolled_back == [True]


@pytest.mark.parametrize(
    ("setup", "expected_status", "expected_code"),
    [
        ("region_down", 400, "region_not_available"),
        ("browser_missing", 404, "browser_not_found"),
        ("platform_mismatch", 409, "browser_platform_mismatch"),
        ("region_mismatch", 409, "region_ip_mismatch"),
        ("browser_bound", 409, "browser_already_bound"),
        ("phone_suspended", 409, "phone_account_not_active"),
    ],
)
def test_create_platform_account_rejects_unsafe_binding(
    session: _FakeSession,
    setup: str,
    expected_status: int,
    expected_code: str,
) -> None:
    phone = _seed_phone(session, state="suspended" if setup == "phone_suspended" else "active")
    _seed_region(session, state="down" if setup == "region_down" else "ok")
    if setup != "browser_missing":
        _seed_browser(
            session,
            platform="yiyan" if setup == "platform_mismatch" else "doubao",
            region_gb="110000" if setup == "region_mismatch" else "310000",
        )
    if setup == "browser_bound":
        other = _seed_phone(session, phone="18810936058")
        _seed_platform(session, other.id)
    _bind(session)

    response = client.post(
        f"/api/v2/collection-accounts/{phone.pub_id}/platform-accounts",
        json={
            "platform": "doubao",
            "region_gb": "310000",
            "browser_instance_key": "doubao_sh",
            "confirm": True,
        },
    )

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code
    assert not [
        row
        for row in session.rows.get(CollectionPlatformAccount, [])
        if row.phone_account_id == phone.id
    ]


def test_sync_otp_registry_backfills_legacy_numbers_without_exposing_them(
    session: _FakeSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = tmp_path / "otp_registered_numbers.json"
    registry.write_text(
        '[{"phone":"18810936058","carrier":"中国移动","slot":"eSIM",'
        '"remark":"eSIM_中国移动_+8618810936058","ts":1}]',
        encoding="utf-8",
    )
    monkeypatch.setenv("GEO_OTP_REGISTRY_PATH", str(registry))
    _bind(session)

    first = client.post("/api/v2/collection-accounts/sync-otp-registry", json={})

    assert first.status_code == 200
    assert first.json() == {"scanned": 1, "created": 1, "updated": 0, "unchanged": 0}
    assert "18810936058" not in first.text
    phone = session.rows[CollectionPhoneAccount][0]
    assert phone.phone == "18810936058"
    assert phone.owner_note == "eSIM 中国移动"
    events = _events(session, "otp_registry_sync")
    assert len(events) == 1
    assert events[0].new_value == {
        "source": "otp_registry",
        "change": "created",
        "phone_masked": "188***6058",
    }

    second = client.post("/api/v2/collection-accounts/sync-otp-registry", json={})
    assert second.status_code == 200
    assert second.json() == {"scanned": 1, "created": 0, "updated": 0, "unchanged": 1}
    assert len(_events(session, "otp_registry_sync")) == 1


def test_sync_otp_registry_requires_operate_and_reports_corruption(
    session: _FakeSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = tmp_path / "otp_registered_numbers.json"
    registry.write_text("{broken", encoding="utf-8")
    monkeypatch.setenv("GEO_OTP_REGISTRY_PATH", str(registry))
    _bind(session, role=Role.REVIEWER)
    assert client.post("/api/v2/collection-accounts/sync-otp-registry", json={}).status_code == 403

    _bind(session)
    resp = client.post("/api/v2/collection-accounts/sync-otp-registry", json={})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "otp_registry_unreadable"


# ── PATCH /collection-platform-accounts/{pub_id} ────────────────────────────


def test_patch_region_change_requires_confirm(session: _FakeSession) -> None:
    phone = _seed_phone(session)
    account = _seed_platform(session, phone.id)
    _seed_region(session, region_gb="110000", state="ok")
    _bind(session)
    resp = client.patch(
        f"/api/v2/collection-platform-accounts/{account.pub_id}",
        json={"region_gb": "110000"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "region_change_requires_confirmation"
    assert account.region_gb == "310000"  # 未变
    assert _events(session) == []  # 未写事件


def test_patch_region_unavailable_400(session: _FakeSession) -> None:
    phone = _seed_phone(session)
    account = _seed_platform(session, phone.id)
    _seed_region(session, region_gb="120000", state="down")  # 非 ok 不可用
    _bind(session)
    resp = client.patch(
        f"/api/v2/collection-platform-accounts/{account.pub_id}",
        json={"region_gb": "120000", "confirm": True},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "region_not_available"
    missing = client.patch(
        f"/api/v2/collection-platform-accounts/{account.pub_id}",
        json={"region_gb": "440300", "confirm": True},
    )
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "region_not_available"


def test_patch_region_and_quota_ok_writes_audit(session: _FakeSession) -> None:
    phone = _seed_phone(session)
    account = _seed_platform(session, phone.id)
    _seed_region(session, region_gb="110000", state="ok")
    _seed_browser(session, instance_key="doubao_bj", region_gb="110000")
    _bind(session)
    resp = client.patch(
        f"/api/v2/collection-platform-accounts/{account.pub_id}",
        json={
            "region_gb": "110000",
            "browser_instance_key": "doubao_bj",
            "quota_day": 30,
            "quota_week": 200,
            "confirm": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["region_gb"] == "110000"
    assert body["quota_day"] == 30
    assert body["quota_week"] == 200
    assert body["quota_year"] is None
    assert body["phone_account_pub_id"] == phone.pub_id
    events = _events(session, "config_change")
    assert len(events) == 1
    event = events[0]
    assert event.actor == "usr_ops"
    assert event.phone_account_id == phone.id
    assert event.platform_account_id == account.id
    assert event.old_value == {
        "region_gb": "310000",
        "browser_instance_key": "doubao_sh",
        "quota_day": None,
        "quota_week": None,
    }
    assert event.new_value == {
        "region_gb": "110000",
        "browser_instance_key": "doubao_bj",
        "quota_day": 30,
        "quota_week": 200,
    }


def test_patch_region_validates_unchanged_browser_and_platform(session: _FakeSession) -> None:
    phone = _seed_phone(session)
    account = _seed_platform(session, phone.id)
    _seed_region(session, region_gb="110000", state="ok")
    _seed_browser(session, instance_key="doubao_sh", region_gb="310000")
    _bind(session)

    mismatch = client.patch(
        f"/api/v2/collection-platform-accounts/{account.pub_id}",
        json={"region_gb": "110000", "confirm": True},
    )

    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "region_ip_mismatch"
    assert account.region_gb == "310000"

    browser = session.rows[CollectionBrowser][0]
    browser.platform = "yiyan"
    platform_mismatch = client.patch(
        f"/api/v2/collection-platform-accounts/{account.pub_id}",
        json={"browser_instance_key": "doubao_sh"},
    )
    # No effective change means no new binding is attempted.
    assert platform_mismatch.status_code == 200

    _seed_browser(session, instance_key="wrong_platform", platform="yiyan", region_gb="310000")
    platform_mismatch = client.patch(
        f"/api/v2/collection-platform-accounts/{account.pub_id}",
        json={"browser_instance_key": "wrong_platform"},
    )
    assert platform_mismatch.status_code == 409
    assert platform_mismatch.json()["error"]["code"] == "browser_platform_mismatch"


def test_patch_browser_bind_region_ip_mismatch_409(session: _FakeSession) -> None:
    phone = _seed_phone(session)
    account = _seed_platform(session, phone.id)  # region 310000
    _seed_browser(session, instance_key="doubao_bj", region_gb="110000")
    _bind(session)
    resp = client.patch(
        f"/api/v2/collection-platform-accounts/{account.pub_id}",
        json={"browser_instance_key": "doubao_bj"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "region_ip_mismatch"
    assert account.browser_instance_key == "doubao_sh"  # 未变
    # 地域匹配的绑定成功
    ok = client.patch(
        f"/api/v2/collection-platform-accounts/{account.pub_id}",
        json={"browser_instance_key": "doubao_sh2"},
    )
    assert ok.status_code == 404  # 实例不存在 → browser_not_found
    _seed_browser(session, instance_key="doubao_sh2", region_gb="310000")
    ok = client.patch(
        f"/api/v2/collection-platform-accounts/{account.pub_id}",
        json={"browser_instance_key": "doubao_sh2"},
    )
    assert ok.status_code == 200
    assert ok.json()["browser_instance_key"] == "doubao_sh2"


def test_patch_unknown_account_404(session: _FakeSession) -> None:
    _bind(session)
    resp = client.patch("/api/v2/collection-platform-accounts/ppa_missing", json={"quota_day": 1})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "platform_account_not_found"


# ── link-test ────────────────────────────────────────────────────────────────


def test_link_test_push_not_configured_503(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    phone = _seed_phone(session)
    monkeypatch.delenv("GEO_ASSIST_NOTIFY_URL", raising=False)
    _bind(session)
    resp = client.post(
        f"/api/v2/collection-accounts/{phone.pub_id}/link-test", json={"channel": "push"}
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "push_channel_not_configured"


def test_link_test_push_success_marks_ok(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    phone = _seed_phone(session)
    monkeypatch.setenv("GEO_ASSIST_NOTIFY_URL", "https://sctapi.ftqq.com/KEY.send")
    monkeypatch.setenv("GEO_ASSIST_NOTIFY_FLAVOR", "serverchan")
    sent: list[dict[str, Any]] = []

    def fake_push(**kwargs: Any) -> bool:
        sent.append(kwargs)
        return True

    monkeypatch.setattr(account_admin_router, "push_captcha_assist", fake_push)
    _bind(session)
    resp = client.post(
        f"/api/v2/collection-accounts/{phone.pub_id}/link-test", json={"channel": "push"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "ok": True,
        "channel": "push",
        "sms_link_state": None,
        "push_link_state": "ok",
        "last_sms_at": None,
        "last_push_test_at": body["last_push_test_at"],
        "wait_window_s": None,
        "guidance": None,
        "detail": None,
    }
    assert body["last_push_test_at"] is not None
    assert sent and sent[0]["flavor"] == "serverchan"
    assert "131***2231" in sent[0]["title"]  # 掩码，不明文
    assert phone.push_link_state == "ok"
    assert phone.last_push_test_at is not None
    events = _events(session, "link_test")
    assert len(events) == 1
    assert events[0].new_value == {"channel": "push", "result": "ok"}


def test_link_test_push_failure_keeps_state(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    phone = _seed_phone(session)
    monkeypatch.setenv("GEO_ASSIST_NOTIFY_URL", "https://sctapi.ftqq.com/KEY.send")
    monkeypatch.setattr(account_admin_router, "push_captcha_assist", lambda **kw: False)
    _bind(session)
    resp = client.post(
        f"/api/v2/collection-accounts/{phone.pub_id}/link-test", json={"channel": "push"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["detail"] == "push_failed"
    assert phone.push_link_state == "untested"  # 失败如实，状态不动
    assert _events(session, "link_test")[0].new_value["result"] == "failed"


def test_link_test_sms_lazy_freshness(session: _FakeSession) -> None:
    phone = _seed_phone(session, last_sms_at=_NOW, updated_at=_NOW)
    _bind(session)
    resp = client.post(
        f"/api/v2/collection-accounts/{phone.pub_id}/link-test", json={"channel": "sms"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["channel"] == "sms"
    assert body["wait_window_s"] == 180
    assert body["guidance"]
    # last_sms_at 在窗内 → 惰性判联通
    # （_NOW 与真实 now 差距大——这里是陈旧场景）
    assert body["sms_link_state"] == "untested"
    assert phone.sms_link_state == "untested"


def test_link_test_sms_fresh_marks_ok(session: _FakeSession) -> None:
    fresh = datetime.now(UTC) - timedelta(seconds=30)
    phone = _seed_phone(session, last_sms_at=fresh, updated_at=_NOW)
    _bind(session)
    resp = client.post(
        f"/api/v2/collection-accounts/{phone.pub_id}/link-test", json={"channel": "sms"}
    )
    assert resp.status_code == 200
    assert resp.json()["sms_link_state"] == "ok"
    assert phone.sms_link_state == "ok"


# ── events ───────────────────────────────────────────────────────────────────


def test_events_merges_phone_and_platform_rows(session: _FakeSession) -> None:
    phone = _seed_phone(session)
    account = _seed_platform(session, phone.id)
    for index in range(3):
        session.add(
            CollectionAccountEvent(
                pub_id=new_pub_id("aev"),
                phone_account_id=phone.id,
                event_type="phone_event",
                actor="usr_ops",
                created_at=_NOW + timedelta(seconds=index),
            )
        )
    session.add(
        CollectionAccountEvent(
            pub_id=new_pub_id("aev"),
            platform_account_id=account.id,
            event_type="config_change",
            actor="usr_ops",
            new_value={"region_gb": "110000"},
            created_at=_NOW + timedelta(seconds=10),
        )
    )
    other = _seed_phone(session, phone="15510162660")
    session.add(
        CollectionAccountEvent(
            pub_id=new_pub_id("aev"),
            phone_account_id=other.id,
            event_type="unrelated",
            actor="usr_ops",
            created_at=_NOW + timedelta(seconds=20),
        )
    )
    session.flush()
    _bind(session)
    resp = client.get(f"/api/v2/collection-accounts/{phone.pub_id}/events")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 4  # 3 phone + 1 platform，不串号
    assert rows[0]["event_type"] == "config_change"  # 倒序：最新的在最前
    assert rows[0]["platform_account_pub_id"] == account.pub_id
    assert rows[0]["new_value"] == {"region_gb": "110000"}
    assert rows[0]["actor"] == "usr_ops"
    assert {row["event_type"] for row in rows} == {"phone_event", "config_change"}


def test_account_event_cursor_reaches_ninth_row_without_a_fifty_row_cap(
    session: _FakeSession,
) -> None:
    phone = _seed_phone(session)
    expected = []
    for index in range(9):
        pub_id = f"aev_page_{index:02d}"
        expected.append(pub_id)
        session.add(
            CollectionAccountEvent(
                pub_id=pub_id,
                phone_account_id=phone.id,
                event_type=f"event_{index}",
                actor="usr_ops",
                created_at=_NOW,
            )
        )
    session.flush()
    expected.sort(reverse=True)
    _bind(session)

    first = client.get(
        f"/api/v2/collection-accounts/{phone.pub_id}/events",
        params={"limit": 4},
    )
    assert first.status_code == 200, first.text
    assert [row["event_pub_id"] for row in first.json()] == expected[:4]
    assert first.headers["X-Total-Count"] == "9"
    second = client.get(
        f"/api/v2/collection-accounts/{phone.pub_id}/events",
        params={"limit": 4, "cursor": first.headers["X-Next-Cursor"]},
    )
    assert [row["event_pub_id"] for row in second.json()] == expected[4:8]
    third = client.get(
        f"/api/v2/collection-accounts/{phone.pub_id}/events",
        params={"limit": 4, "cursor": second.headers["X-Next-Cursor"]},
    )
    assert [row["event_pub_id"] for row in third.json()] == expected[8:]
    assert third.headers["X-Has-More"] == "false"


def test_events_unknown_phone_404(session: _FakeSession) -> None:
    _bind(session)
    resp = client.get("/api/v2/collection-accounts/pha_missing/events")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "phone_account_not_found"


# ── browsers ────────────────────────────────────────────────────────────────


def test_browsers_list_shape_with_bindings(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    phone = _seed_phone(session)
    _seed_platform(session, phone.id, browser_instance_key="doubao_sh")
    _seed_browser(session)
    started = datetime.now(UTC) - timedelta(hours=3)
    monkeypatch.setattr(
        account_admin_router,
        "probe_browser_runtime",
        lambda unit, key: {
            "started_at": started,
            "uptime_s": 3 * 3600,
            "rss_bytes": 512 * 1024 * 1024,
        },
    )
    _bind(session)
    resp = client.get("/api/v2/collection-browsers")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["instance_key"] == "doubao_sh"
    assert row["platform"] == "doubao"
    assert row["region_gb"] == "310000"
    assert row["cdp_port"] == 19222
    assert row["systemd_unit"] == "geo-platform-v2-browser@doubao_sh.service"
    assert row["activity"] == "idle"
    assert row["error_streak"] == 0
    assert row["uptime_s"] == 10800
    assert row["rss_bytes"] == 512 * 1024 * 1024
    assert row["started_at"] is not None
    assert row["bindings"] == {"doubao": phone.pub_id}


def test_restart_records_event_not_executed(session: _FakeSession) -> None:
    browser = _seed_browser(session)
    _bind(session)
    resp = client.post("/api/v2/collection-browsers/doubao_sh/restart")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "ok": True,
        "instance_key": "doubao_sh",
        "executed": False,
        "detail": "manual_restart_window_required",
    }
    events = _events(session, "browser_restart_requested")
    assert len(events) == 1
    assert events[0].browser_id == browser.id
    assert events[0].actor == "usr_ops"
    missing = client.post("/api/v2/collection-browsers/nope_xx/restart")
    assert missing.status_code == 404


def test_release_lock_sets_released_at_idempotent(session: _FakeSession) -> None:
    browser = _seed_browser(session)
    fence = BrowserFence(
        platform="doubao_sh",
        holder="worker-1",
        fencing_token=7,
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    session.add(fence)
    session.flush()
    _bind(session)
    resp = client.post("/api/v2/collection-browsers/doubao_sh/release-lock")
    assert resp.status_code == 200
    body = resp.json()
    assert body["released"] is True
    assert body["detail"] == "lock_released"
    assert fence.released_at is not None
    events = _events(session, "browser_lock_released")
    assert len(events) == 1
    assert events[0].browser_id == browser.id
    assert events[0].old_value == {
        "holder": "worker-1",
        "fencing_token": 7,
        "released_at": None,
    }
    # 幂等：再释放 = 无活动锁
    again = client.post("/api/v2/collection-browsers/doubao_sh/release-lock")
    assert again.status_code == 200
    assert again.json()["released"] is False
    assert again.json()["detail"] == "no_active_lock"


# ── regions ──────────────────────────────────────────────────────────────────


def test_regions_create_list_and_validation(session: _FakeSession) -> None:
    _bind(session)
    resp = client.post(
        "/api/v2/collection-regions",
        json={
            "region_gb": "110000",
            "name": "北京",
            "proxy_env_key": "GEO_PROXY_BJ",
            "relay_unit": "geo-platform-v2-proxy-relay@bj.service",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["region_gb"] == "110000"
    assert body["name"] == "北京"
    assert body["proxy_env_key"] == "GEO_PROXY_BJ"
    assert body["relay_unit"] == "geo-platform-v2-proxy-relay@bj.service"
    assert body["state"] == "ok"
    assert body["source"] == "wukong"
    assert _events(session, "region_created")[0].actor == "usr_ops"
    dup = client.post("/api/v2/collection-regions", json={"region_gb": "110000"})
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "region_already_exists"
    bad = client.post("/api/v2/collection-regions", json={"region_gb": "11"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "bad_region_gb"
    listed = client.get("/api/v2/collection-regions")
    assert listed.status_code == 200
    assert [row["region_gb"] for row in listed.json()] == ["110000"]


def test_region_probe_endpoint(session: _FakeSession, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_region(session, region_gb="110000")
    calls: list[str] = []

    def fake_probe(conn: Any, region_gb: str) -> dict[str, Any]:
        calls.append(region_gb)
        return {
            "region_gb": region_gb,
            "ok": True,
            "exit_ip": "106.37.143.183",
            "note": None,
            "alerted": False,
        }

    monkeypatch.setattr(account_admin_router, "probe_collection_region", fake_probe)
    _bind(session)
    resp = client.post("/api/v2/collection-regions/110000/probe")
    assert resp.status_code == 200
    assert resp.json() == {
        "region_gb": "110000",
        "ok": True,
        "exit_ip": "106.37.143.183",
        "note": None,
        "alerted": False,
    }
    assert calls == ["110000"]
    assert session.committed >= 1


def test_region_probe_unknown_404(session: _FakeSession) -> None:
    _bind(session)
    resp = client.post("/api/v2/collection-regions/659999/probe")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "region_not_found"


# ── sync 端点 ────────────────────────────────────────────────────────────────


def test_browser_sync_endpoint_idempotent(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEO_BROWSER_INSTANCES", "doubao_bj,deepseek_sh")
    monkeypatch.setenv("GEO_BROWSER_DOUBAO_BJ_CDP_URL", "http://127.0.0.1:19233")
    monkeypatch.setenv("GEO_BROWSER_DOUBAO_BJ_EXIT_GB", "110000")
    monkeypatch.setenv("GEO_BROWSER_DEEPSEEK_SH_CDP_URL", "http://127.0.0.1:19234")
    monkeypatch.setenv("GEO_BROWSER_DEEPSEEK_SH_EXIT_GB", "310000")
    _bind(session)
    resp = client.post("/api/v2/collection-browsers/sync")
    assert resp.status_code == 200
    assert resp.json() == {
        "synced": 2,
        "created": 2,
        "updated": 0,
        "errors": [],
        "instances": ["doubao_bj", "deepseek_sh"],
    }
    again = client.post("/api/v2/collection-browsers/sync")
    assert again.status_code == 200
    assert again.json()["created"] == 0
    assert again.json()["updated"] == 2  # 幂等：重复跑零新建
    rows = session.rows.get(CollectionBrowser, [])
    assert len(rows) == 2
    doubao = next(row for row in rows if row.instance_key == "doubao_bj")
    assert doubao.platform == "doubao"
    assert doubao.region_gb == "110000"
    assert doubao.cdp_port == 19233
    assert doubao.systemd_unit == "geo-platform-v2-browser@doubao_bj.service"


def test_browser_sync_env_missing_503(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEO_BROWSER_INSTANCES", raising=False)
    _bind(session)
    resp = client.post("/api/v2/collection-browsers/sync")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "browser_instances_not_configured"


# ---------------------------------------------------------------------------
# force-release（采集账号占用模型，2026-09-01 起）
# ---------------------------------------------------------------------------


def test_force_release_running_releases_and_audits(session: _FakeSession) -> None:
    phone = _seed_phone(session)
    account = _seed_platform(
        session,
        phone.id,
        runtime_state="running",
        current_run_pub_id="run_stuck",
        reservation_expires_at=_NOW + timedelta(hours=1),
    )
    _bind(session)
    resp = client.post(
        f"/api/v2/collection-platform-accounts/{account.pub_id}/force-release", json={}
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "platform_account_pub_id": account.pub_id,
        "released": True,
        "runtime_state": "idle",
        "detail": "operator_force_release",
    }
    assert account.runtime_state == "idle"
    assert account.current_run_pub_id is None
    assert account.reservation_expires_at is None
    events = [
        row
        for row in session.rows.get(CollectionAccountEvent, [])
        if row.event_type == "state_transition"
    ]
    assert len(events) == 1
    assert events[0].new_value["reason"] == "operator_force_release"
    assert events[0].actor == "usr_ops"
    assert events[0].run_pub_id == "run_stuck"


def test_force_release_captcha_and_error_require_clear_health(session: _FakeSession) -> None:
    phone = _seed_phone(session)
    for state in ("captcha", "error"):
        account = _seed_platform(
            session,
            phone.id,
            pub_id=f"ppa_{state}",
            platform=state[:6],
            runtime_state=state,
        )
        _bind(session)
        url = f"/api/v2/collection-platform-accounts/{account.pub_id}/force-release"
        denied = client.post(url, json={})
        assert denied.status_code == 409
        assert denied.json()["error"]["code"] == "illegal_state_transition"
        assert account.runtime_state == state
        ok = client.post(url, json={"clear_health": True})
        assert ok.status_code == 200
        assert ok.json()["released"] is True
        assert ok.json()["detail"] == "operator_force_release_clear_health"
        assert account.runtime_state == "idle"


def test_force_release_muted_and_quota_stay_409(session: _FakeSession) -> None:
    """muted/quota_exhausted 有自己的恢复语义（到期 lazy resume），force-release
    连 clear_health 也不得冲掉。"""
    phone = _seed_phone(session)
    for state in ("muted", "quota_exhausted"):
        account = _seed_platform(
            session,
            phone.id,
            pub_id=f"ppa_{state}",
            platform=state[:6],
            runtime_state=state,
        )
        _bind(session)
        resp = client.post(
            f"/api/v2/collection-platform-accounts/{account.pub_id}/force-release",
            json={"clear_health": True},
        )
        assert resp.status_code == 409
        assert account.runtime_state == state
        assert session.rows.get(CollectionAccountEvent, []) == []


def test_force_release_idle_is_idempotent_noop(session: _FakeSession) -> None:
    phone = _seed_phone(session)
    account = _seed_platform(session, phone.id)
    _bind(session)
    resp = client.post(
        f"/api/v2/collection-platform-accounts/{account.pub_id}/force-release", json={}
    )
    assert resp.status_code == 200
    assert resp.json()["released"] is False
    assert resp.json()["detail"] == "already_idle"
    assert session.rows.get(CollectionAccountEvent, []) == []


def test_force_release_unknown_account_404(session: _FakeSession) -> None:
    _bind(session)
    resp = client.post("/api/v2/collection-platform-accounts/ppa_ghost/force-release", json={})
    assert resp.status_code == 404


def test_force_release_requires_operate_role(session: _FakeSession) -> None:
    phone = _seed_phone(session)
    account = _seed_platform(session, phone.id, runtime_state="running")
    _bind(session, role=Role.CUSTOMER)
    resp = client.post(
        f"/api/v2/collection-platform-accounts/{account.pub_id}/force-release", json={}
    )
    assert resp.status_code == 403
    assert account.runtime_state == "running"
