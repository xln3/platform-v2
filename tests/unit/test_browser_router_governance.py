"""browser_router × 采集账号治理消费单测（2026-08-14 起，caiji-0813 §1.3 派题链 / §6.2）。

决策矩阵覆盖：命中（用绑定实例，含 lazy resume）/ 无账号行 env 回退 / 全忙或
region_down → account_unavailable / 地域IP不匹配 fail-closed / 治理层 DB 异常
env 回退 / GEO_ACCOUNT_GOVERNANCE=off 跳过治理层。

fake DB 先例照 test_account_governor.py 的 _FakeSession（select 结构路由到内存
行；governor 全部查询都是这个形状），外加 Session 上下文管理器/事务假面。
真实 AccountGovernor 在 fake 行集上跑——路由×治理的契约按真代码全链路验证。
纯 env 路由行为的全分支覆盖在 test_browser_router.py（闸门 off 缺省，不受影响）。
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from geo_platform.collection.account_models import (
    CollectionBrowser,
    CollectionPlatformAccount,
    CollectionRegion,
)
from geo_platform.tenancy.ids import new_pub_id
from temporalio.exceptions import ApplicationError

from workflows.activities import browser_router
from workflows.activities.browser_router import (
    ENV_BROWSER_INSTANCES,
    resolve_batch_instance,
    resolve_browser_instance,
)
from workflows.activities.collection import CollectionTaskInput


def _instances(monkeypatch: pytest.MonkeyPatch, keys: list[str]) -> None:
    monkeypatch.setenv(ENV_BROWSER_INSTANCES, ",".join(keys))


def _instance(
    monkeypatch: pytest.MonkeyPatch, key: str, *, port: int = 19222, exit_gb: str = "310000"
) -> None:
    monkeypatch.setenv(f"GEO_BROWSER_{key.upper()}_CDP_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv(f"GEO_BROWSER_{key.upper()}_EXIT_GB", exit_gb)


@pytest.fixture()
def _prod_topology(monkeypatch: pytest.MonkeyPatch):
    """生产五实例拓扑（与 tests/unit/conftest.py 全局缺省同形）。"""
    _instances(monkeypatch, ["doubao_sh", "deepseek_tj", "tongyi_bj", "yiyan_sh", "yuanbao_tj"])
    _instance(monkeypatch, "doubao_sh", port=19222, exit_gb="310000")
    _instance(monkeypatch, "deepseek_tj", port=19224, exit_gb="120000")
    _instance(monkeypatch, "tongyi_bj", port=19225, exit_gb="110000")
    _instance(monkeypatch, "yiyan_sh", port=19226, exit_gb="310000")
    _instance(monkeypatch, "yuanbao_tj", port=19227, exit_gb="120000")


def _task(key: str, adapter: str, region: str) -> CollectionTaskInput:
    return CollectionTaskInput(
        business_key=key,
        query=f"q-{key}",
        model="m",
        region=region,
        mode="normal",
        adapter=adapter,
    )


class _FakeGovernanceDb:
    """browser_router._worker_session seam 的 fake（不起真 PG）。"""

    def __init__(self) -> None:
        self.rows: dict[type, list[Any]] = {}
        self._ids: dict[type, itertools.count] = {}

    def __enter__(self) -> _FakeGovernanceDb:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def get(self, cls: type, pk: int) -> Any | None:
        for row in self.rows.get(cls, []):
            if row.id == pk:
                return row
        return None

    def scalar(self, stmt: Any) -> Any | None:
        rows = self._select(stmt)
        return rows[0] if rows else None

    def scalars(self, stmt: Any) -> list[Any]:
        return list(self._select(stmt))

    def _select(self, stmt: Any) -> list[Any]:
        cls = stmt.column_descriptions[0]["entity"]
        rows = list(self.rows.get(cls, []))
        for criterion in stmt._where_criteria:
            key = criterion.left.key
            value = criterion.right.value
            rows = [row for row in rows if getattr(row, key) == value]
        for order in stmt._order_by_clauses:
            key = order.element.key
            desc = "desc" in str(order.modifier)
            rows.sort(key=lambda row: getattr(row, key), reverse=desc)
        if stmt._limit is not None:
            rows = rows[: stmt._limit]
        return rows

    def add(self, obj: Any) -> None:
        self.rows.setdefault(type(obj), []).append(obj)

    def flush(self) -> None:
        for cls, rows in self.rows.items():
            counter = self._ids.setdefault(cls, itertools.count(1))
            for row in rows:
                if getattr(row, "id", None) is None:
                    row.id = next(counter)


@pytest.fixture()
def governance_db(monkeypatch: pytest.MonkeyPatch) -> _FakeGovernanceDb:
    """开启治理消费（GEO_ACCOUNT_GOVERNANCE=db）并把 worker session seam 换成 fake。"""
    db = _FakeGovernanceDb()
    monkeypatch.setenv("GEO_ACCOUNT_GOVERNANCE", "db")
    monkeypatch.setattr(browser_router, "_worker_session", lambda: db)
    return db


def _seed_gov_account(db: _FakeGovernanceDb, **overrides: Any) -> CollectionPlatformAccount:
    fields: dict[str, Any] = {
        "pub_id": new_pub_id("pac"),
        "phone_account_id": 1,
        "platform": "doubao",
        "region_gb": "310000",
        "runtime_state": "idle",
        "used_today": 0,
        "used_week": 0,
        "used_year": 0,
        "browser_instance_key": "doubao_sh",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    fields.update(overrides)
    row = CollectionPlatformAccount(**fields)
    db.add(row)
    db.flush()
    return row


def _seed_gov_browser(db: _FakeGovernanceDb, **overrides: Any) -> CollectionBrowser:
    fields: dict[str, Any] = {
        "pub_id": new_pub_id("brw"),
        "instance_key": "doubao_sh",
        "platform": "doubao",
        "region_gb": "310000",
        "activity": "idle",
        "error_streak": 0,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    fields.update(overrides)
    row = CollectionBrowser(**fields)
    db.add(row)
    db.flush()
    return row


def _seed_gov_region(db: _FakeGovernanceDb, **overrides: Any) -> CollectionRegion:
    fields: dict[str, Any] = {
        "pub_id": new_pub_id("rgn"),
        "region_gb": "310000",
        "state": "ok",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    fields.update(overrides)
    row = CollectionRegion(**fields)
    db.add(row)
    db.flush()
    return row


@pytest.mark.usefixtures("_prod_topology")
def test_governor_hit_uses_bound_instance_not_env_first(
    monkeypatch: pytest.MonkeyPatch, governance_db: _FakeGovernanceDb
) -> None:
    """命中：用账号绑定的实例（doubao_sh2），而非 env 清单序首个（doubao_sh）。"""
    monkeypatch.setenv(ENV_BROWSER_INSTANCES, "doubao_sh,doubao_sh2")
    _instance(monkeypatch, "doubao_sh2", port=19230, exit_gb="310000")
    _seed_gov_region(governance_db)
    _seed_gov_browser(governance_db, instance_key="doubao_sh2")
    _seed_gov_account(governance_db, browser_instance_key="doubao_sh2")
    route = resolve_browser_instance("doubao", "CN-SH")
    assert route.instance_key == "doubao_sh2"
    assert route.cdp_url == "http://127.0.0.1:19230"


@pytest.mark.usefixtures("_prod_topology")
def test_governor_hit_region_mismatch_fail_closed(
    monkeypatch: pytest.MonkeyPatch, governance_db: _FakeGovernanceDb
) -> None:
    """命中但实例 env 出口省码 ≠ 派题地域：「地域IP不匹配」fail-closed（绝不带
    错地域出口硬采）。"""
    _seed_gov_region(governance_db)
    _seed_gov_browser(governance_db)  # DB 行 region=310000，governor 判定命中
    _seed_gov_account(governance_db)
    # env 真源与治理绑定不一致（部署被改成天津出口）
    monkeypatch.setenv("GEO_BROWSER_DOUBAO_SH_EXIT_GB", "120000")
    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "CN-SH")
    assert exc_info.value.type == "region_ip_mismatch"
    assert exc_info.value.non_retryable is True


@pytest.mark.usefixtures("_prod_topology")
def test_governor_hit_unbound_browser_is_account_unavailable(
    governance_db: _FakeGovernanceDb,
) -> None:
    """治理行存在但派题链不完整（未绑浏览器）→ account_unavailable，不回退 env。"""
    _seed_gov_region(governance_db)
    _seed_gov_account(governance_db, browser_instance_key=None)
    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "CN-SH")
    assert exc_info.value.type == "account_unavailable"
    assert "no_bound_browser" in str(exc_info.value)


def test_governor_hit_key_not_in_instance_list_fail_closed(
    monkeypatch: pytest.MonkeyPatch, governance_db: _FakeGovernanceDb
) -> None:
    """治理绑定键不在 GEO_BROWSER_INSTANCES 清单：治理与部署真源不一致，fail-closed。"""
    _instances(monkeypatch, ["tongyi_bj"])
    _seed_gov_region(governance_db)
    _seed_gov_account(governance_db, browser_instance_key="doubao_sh")
    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "CN-SH")
    assert exc_info.value.type == "browser_instances_invalid"


@pytest.mark.usefixtures("_prod_topology")
def test_governor_no_account_registered_env_fallback(
    governance_db: _FakeGovernanceDb,
) -> None:
    """该平台该地域一行账号都没有 → env 清单回退（过渡期保命，行为同旧版）。"""
    route = resolve_browser_instance("doubao", "CN-SH")
    assert route.instance_key == "doubao_sh"


@pytest.mark.usefixtures("_prod_topology")
def test_governor_all_accounts_busy_account_unavailable(
    governance_db: _FakeGovernanceDb,
) -> None:
    """有账号但全部不可用（running）→ account_unavailable（reason 机读），绝不
    回退 env 硬撞、绝不抛非治理错误拖垮整批。"""
    _seed_gov_region(governance_db)
    _seed_gov_browser(governance_db)
    _seed_gov_account(governance_db, runtime_state="running", current_run_pub_id="run_X")
    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "CN-SH")
    assert exc_info.value.type == "account_unavailable"
    assert exc_info.value.non_retryable is True
    assert "no_collectable_account" in str(exc_info.value)
    # batch 入口同样传播该信号（workflow 侧转成等长占位）
    items = [_task("a", "doubao", "CN-SH"), _task("b", "doubao", "CN-SH")]
    with pytest.raises(ApplicationError) as batch_exc:
        resolve_batch_instance(items)
    assert batch_exc.value.type == "account_unavailable"


@pytest.mark.usefixtures("_prod_topology")
def test_governor_region_down_unavailable_no_env_fallback(
    governance_db: _FakeGovernanceDb,
) -> None:
    """region_down：有无账号行都不 env 回退——该地域出口已不可信。"""
    _seed_gov_region(governance_db, state="down")
    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "CN-SH")
    assert exc_info.value.type == "account_unavailable"
    assert "region_down" in str(exc_info.value)
    _seed_gov_browser(governance_db)
    _seed_gov_account(governance_db)
    with pytest.raises(ApplicationError) as exc_info2:
        resolve_browser_instance("doubao", "CN-SH")
    assert exc_info2.value.type == "account_unavailable"


@pytest.mark.usefixtures("_prod_topology")
def test_governor_error_env_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """治理层 DB 异常 → env 回退（治理故障不阻断采集主链；表未迁移同路）。"""
    monkeypatch.setenv("GEO_ACCOUNT_GOVERNANCE", "db")

    def _boom() -> Any:
        raise RuntimeError("pg unreachable")

    monkeypatch.setattr(browser_router, "_worker_session", _boom)
    route = resolve_browser_instance("doubao", "CN-SH")
    assert route.instance_key == "doubao_sh"


@pytest.mark.usefixtures("_prod_topology")
def test_governance_gate_off_skips_governor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GEO_ACCOUNT_GOVERNANCE=off（单测/应急 kill switch）：治理层不被触及。"""
    monkeypatch.setenv("GEO_ACCOUNT_GOVERNANCE", "off")

    def _boom() -> Any:
        raise AssertionError("governor must not be consulted when gate is off")

    monkeypatch.setattr(browser_router, "_worker_session", _boom)
    route = resolve_browser_instance("doubao", "CN-SH")
    assert route.instance_key == "doubao_sh"


@pytest.mark.usefixtures("_prod_topology")
def test_governor_lazy_resume_expired_mute_hits(
    governance_db: _FakeGovernanceDb,
) -> None:
    """过点禁言账号经 resolve 的 lazy resume 回 idle 并命中（全链路真实 governor）。"""
    _seed_gov_region(governance_db)
    _seed_gov_browser(governance_db)
    account = _seed_gov_account(
        governance_db,
        runtime_state="muted",
        muted_until=datetime.now(UTC) - timedelta(minutes=1),
    )
    route = resolve_browser_instance("doubao", "CN-SH")
    assert route.instance_key == "doubao_sh"
    assert account.runtime_state == "idle"


@pytest.mark.usefixtures("_prod_topology")
def test_governor_unmapped_region_keeps_region_exit_mismatch(
    governance_db: _FakeGovernanceDb,
) -> None:
    """region 无法归一 → 治理层无从匹配（不消费），env 路径照旧 region_exit_mismatch。"""
    _seed_gov_region(governance_db)
    _seed_gov_account(governance_db)
    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "Atlantis")
    assert exc_info.value.type == "region_exit_mismatch"
