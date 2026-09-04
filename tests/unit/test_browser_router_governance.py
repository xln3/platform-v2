"""browser_router × 采集账号治理消费单测（2026-08-14 起，caiji-0813 §1.3 派题链 / §6.2）。

决策矩阵覆盖：命中（用绑定实例，含 lazy resume）/ 豆包无账号 fail-closed /
非豆包无账号 legacy env 回退 / 全忙或 region_down → account_unavailable /
地域IP不匹配 fail-closed / 治理层 DB 异常 fail-closed /
GEO_ACCOUNT_GOVERNANCE=off 显式跳过治理层。

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


def _task(key: str, adapter: str, region: str, *, mode: str = "normal") -> CollectionTaskInput:
    return CollectionTaskInput(
        business_key=key,
        query=f"q-{key}",
        model="m",
        region=region,
        mode=mode,
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
    """开启治理消费（GEO_ACCOUNT_GOVERNANCE=db）并把 worker session seam 换成 fake。

    竞争排队等待（2026-09-01 起）在本文件缺省关闭（WAIT_TIMEOUT=0 = 首 miss 即
    判 account_contention_timeout），等待行为由专门的用例显式开启。
    """
    db = _FakeGovernanceDb()
    monkeypatch.setenv("GEO_ACCOUNT_GOVERNANCE", "db")
    monkeypatch.setenv("GEO_ACCOUNT_WAIT_TIMEOUT_S", "0")
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
def test_run_claims_idle_account_and_reuses_it_across_questions(
    governance_db: _FakeGovernanceDb,
) -> None:
    """一题一个 Activity 仍须把账号粘在 run 上；第二题可复用 running owner。"""
    _seed_gov_region(governance_db)
    _seed_gov_browser(governance_db)
    account = _seed_gov_account(governance_db)

    first = resolve_batch_instance(
        [_task("first", "doubao", "CN-SH")],
        run_pub_id="run_sticky",
    )
    second = resolve_batch_instance(
        [_task("second", "doubao", "CN-SH")],
        run_pub_id="run_sticky",
    )

    assert first is not None and first.instance_key == "doubao_sh"
    assert second is not None and second.instance_key == "doubao_sh"
    assert account.runtime_state == "running"
    assert account.current_run_pub_id == "run_sticky"


@pytest.mark.usefixtures("_prod_topology")
def test_two_runs_claim_two_distinct_accounts(
    monkeypatch: pytest.MonkeyPatch, governance_db: _FakeGovernanceDb
) -> None:
    """第二个 run 不得复用第一个 run 已认领的账号，应领取同地域下一账号。"""
    monkeypatch.setenv(ENV_BROWSER_INSTANCES, "doubao_sh,doubao_sh2")
    _instance(monkeypatch, "doubao_sh2", port=19230, exit_gb="310000")
    _seed_gov_region(governance_db)
    _seed_gov_browser(governance_db, instance_key="doubao_sh")
    _seed_gov_browser(governance_db, instance_key="doubao_sh2")
    first_account = _seed_gov_account(governance_db, browser_instance_key="doubao_sh")
    second_account = _seed_gov_account(governance_db, browser_instance_key="doubao_sh2")

    first = resolve_browser_instance("doubao", "CN-SH", run_pub_id="run_one")
    second = resolve_browser_instance("doubao", "CN-SH", run_pub_id="run_two")

    assert first.instance_key == "doubao_sh"
    assert second.instance_key == "doubao_sh2"
    assert first_account.current_run_pub_id == "run_one"
    assert second_account.current_run_pub_id == "run_two"


@pytest.mark.usefixtures("_prod_topology")
def test_existing_run_owner_wins_over_earlier_idle_account(
    monkeypatch: pytest.MonkeyPatch, governance_db: _FakeGovernanceDb
) -> None:
    """run 已绑定账号后，即使排序更前的账号空闲，也不能在下一题漂移身份。"""
    monkeypatch.setenv(ENV_BROWSER_INSTANCES, "doubao_sh,doubao_sh2")
    _instance(monkeypatch, "doubao_sh2", port=19230, exit_gb="310000")
    _seed_gov_region(governance_db)
    _seed_gov_browser(governance_db, instance_key="doubao_sh")
    _seed_gov_browser(governance_db, instance_key="doubao_sh2")
    idle = _seed_gov_account(governance_db, browser_instance_key="doubao_sh")
    owner = _seed_gov_account(
        governance_db,
        browser_instance_key="doubao_sh2",
        runtime_state="running",
        current_run_pub_id="run_owner",
    )

    route = resolve_browser_instance("doubao", "CN-SH", run_pub_id="run_owner")

    assert route.instance_key == "doubao_sh2"
    assert idle.runtime_state == "idle"
    assert idle.current_run_pub_id is None
    assert owner.current_run_pub_id == "run_owner"


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
    _seed_gov_browser(governance_db)
    _seed_gov_account(governance_db, browser_instance_key="doubao_sh")
    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "CN-SH")
    assert exc_info.value.type == "browser_instances_invalid"


@pytest.mark.usefixtures("_prod_topology")
def test_governor_doubao_no_account_registered_is_fail_closed(
    governance_db: _FakeGovernanceDb,
) -> None:
    """豆包已正式治理：无账号行也不得被手工 run 绕过到 env 浏览器。"""
    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "CN-SH")
    assert exc_info.value.type == "account_unavailable"
    assert "account_unregistered" in str(exc_info.value)

    with pytest.raises(ApplicationError) as batch_exc:
        resolve_batch_instance([_task("missing", "doubao", "CN-SH")])
    assert batch_exc.value.type == "account_unavailable"


@pytest.mark.usefixtures("_prod_topology")
def test_governor_legacy_platform_no_account_registered_env_fallback(
    governance_db: _FakeGovernanceDb,
) -> None:
    """尚未迁移的 Yiyan 无账号行仍带审计日志走 legacy env，避免全腿饿死。"""
    route = resolve_browser_instance("yiyan", "CN-SH", "deep_think")
    assert route.instance_key == "yiyan_sh"


@pytest.mark.usefixtures("_prod_topology")
def test_governor_all_accounts_busy_account_unavailable(
    governance_db: _FakeGovernanceDb,
) -> None:
    """有账号但全部不可用（running 占用，租约有效未失活）→ 不排队（WAIT_TIMEOUT=0）
    立即判 account_contention_timeout（reason 机读），绝不回退 env 硬撞、绝不
    抛非治理错误拖垮整批。"""
    _seed_gov_region(governance_db)
    _seed_gov_browser(governance_db)
    _seed_gov_account(
        governance_db,
        runtime_state="running",
        current_run_pub_id="run_X",
        reservation_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "CN-SH")
    assert exc_info.value.type == "account_contention_timeout"
    assert exc_info.value.non_retryable is True
    assert "no_collectable_account" in str(exc_info.value)
    # batch 入口同样传播该信号（workflow 侧转成等长占位）
    items = [_task("a", "doubao", "CN-SH"), _task("b", "doubao", "CN-SH")]
    with pytest.raises(ApplicationError) as batch_exc:
        resolve_batch_instance(items)
    assert batch_exc.value.type == "account_contention_timeout"


@pytest.mark.usefixtures("_prod_topology")
@pytest.mark.parametrize("activity", ["busy", "captcha"])
def test_governor_non_idle_browser_is_account_unavailable(
    activity: str, governance_db: _FakeGovernanceDb
) -> None:
    _seed_gov_region(governance_db)
    _seed_gov_browser(governance_db, activity=activity)
    _seed_gov_account(governance_db)
    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "CN-SH", "normal")
    # 浏览器暂时性占用（busy/captcha）同属竞争等待词表：WAIT_TIMEOUT=0 立即判超时
    assert exc_info.value.type == "account_contention_timeout"
    assert f"browser_{activity}" in str(exc_info.value)


@pytest.mark.usefixtures("_prod_topology")
def test_governor_expired_breaker_with_unrecovered_failure_is_account_unavailable(
    governance_db: _FakeGovernanceDb,
) -> None:
    """Manual/generic scheduler runs bypass gradual health but not the worker gate."""
    _seed_gov_region(governance_db)
    _seed_gov_browser(
        governance_db,
        error_streak=41,
        breaker_until=datetime.now(UTC) - timedelta(minutes=1),
    )
    _seed_gov_account(governance_db)

    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "CN-SH", "normal")

    assert exc_info.value.type == "account_unavailable"
    assert "browser_failure_unrecovered" in str(exc_info.value)


@pytest.mark.usefixtures("_prod_topology")
def test_governor_mode_quota_block_allows_quick_fallback(
    governance_db: _FakeGovernanceDb,
) -> None:
    _seed_gov_region(governance_db)
    _seed_gov_browser(governance_db)
    _seed_gov_account(
        governance_db,
        quota_probe_json={
            "mode_quota_blocks": {
                "deep_think": {"resume_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
            }
        },
    )
    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "CN-SH", "deep_think")
    assert exc_info.value.type == "account_unavailable"
    assert "mode_quota_exhausted" in str(exc_info.value)
    route = resolve_browser_instance("doubao", "CN-SH", "normal")
    assert route.instance_key == "doubao_sh"

    # Batch 路由必须把 item.mode 传到治理层，不能退回无 mode 的全局阻断语义。
    quick = resolve_batch_instance([_task("quick", "doubao", "CN-SH", mode="normal")])
    assert quick is not None
    assert quick.instance_key == "doubao_sh"
    with pytest.raises(ApplicationError) as batch_exc:
        resolve_batch_instance([_task("expert", "doubao", "CN-SH", mode="deep_think")])
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
def test_governor_error_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """治理层 DB 异常 → fail-closed；未知健康状态不得隐式降级成 env 直派。"""
    monkeypatch.setenv("GEO_ACCOUNT_GOVERNANCE", "db")

    def _boom() -> Any:
        raise RuntimeError("pg unreachable")

    monkeypatch.setattr(browser_router, "_worker_session", _boom)
    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "CN-SH")
    assert exc_info.value.type == "account_unavailable"
    assert "governor_error" in str(exc_info.value)


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


# ---------------------------------------------------------------------------
# 竞争排队等待（采集账号占用模型，2026-09-01 起）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_prod_topology")
def test_contention_wait_acquires_account_on_later_poll(
    monkeypatch: pytest.MonkeyPatch, governance_db: _FakeGovernanceDb
) -> None:
    """账号暂时全忙 → 有界轮询等待；第 2 次轮询时持有者已释放 → 认领成功。"""
    monkeypatch.setenv("GEO_ACCOUNT_WAIT_TIMEOUT_S", "300")
    monkeypatch.setenv("GEO_ACCOUNT_WAIT_POLL_S", "1")
    _seed_gov_region(governance_db)
    _seed_gov_browser(governance_db)
    account = _seed_gov_account(
        governance_db,
        runtime_state="running",
        current_run_pub_id="run_holder",
        reservation_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    sleeps: list[float] = []
    heartbeats: list[dict[str, Any]] = []

    def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        # 首次等待期间持有 run 终态释放（release_run_reservations 语义）
        account.runtime_state = "idle"
        account.current_run_pub_id = None
        account.reservation_expires_at = None

    monkeypatch.setattr(browser_router.time, "sleep", _fake_sleep)
    monkeypatch.setattr(browser_router, "_wait_heartbeat", heartbeats.append)

    route = resolve_browser_instance("doubao", "CN-SH", run_pub_id="run_new")

    assert route.instance_key == "doubao_sh"
    assert sleeps == [1.0]
    assert account.runtime_state == "running"
    assert account.current_run_pub_id == "run_new"
    assert account.reservation_expires_at is not None  # 认领写租约
    assert len(heartbeats) == 1
    assert heartbeats[0]["stage"] == "account_contention_wait"
    assert heartbeats[0]["run_pub_id"] == "run_new"


@pytest.mark.usefixtures("_prod_topology")
def test_contention_wait_timeout_returns_distinct_error(
    monkeypatch: pytest.MonkeyPatch, governance_db: _FakeGovernanceDb
) -> None:
    """等满预算仍全忙 → account_contention_timeout（区别于账号不存在的
    account_unavailable），non_retryable，message 带预算与机读 reason。"""
    monkeypatch.setenv("GEO_ACCOUNT_WAIT_TIMEOUT_S", "5")
    monkeypatch.setenv("GEO_ACCOUNT_WAIT_POLL_S", "2")
    _seed_gov_region(governance_db)
    _seed_gov_browser(governance_db)
    _seed_gov_account(
        governance_db,
        runtime_state="running",
        current_run_pub_id="run_holder",
        reservation_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    now = [1000.0]
    sleeps: list[float] = []
    monkeypatch.setattr(browser_router.time, "monotonic", lambda: now[0])

    def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(browser_router.time, "sleep", _fake_sleep)

    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "CN-SH", run_pub_id="run_new")

    assert exc_info.value.type == "account_contention_timeout"
    assert exc_info.value.non_retryable is True
    message = str(exc_info.value)
    assert "budget=5" in message
    assert "no_collectable_account" in message
    # 末段按剩余预算截断（2,2,1），不 overshoot
    assert sleeps == [2.0, 2.0, 1.0]


@pytest.mark.usefixtures("_prod_topology")
def test_expired_lease_is_reaped_during_contention_wait(
    monkeypatch: pytest.MonkeyPatch, governance_db: _FakeGovernanceDb
) -> None:
    """占用卡死（租约过期）的账号在首次治理判定即被惰性回收——等待循环根本不
    需要进入（首次解析直接命中）。"""
    monkeypatch.setenv("GEO_ACCOUNT_WAIT_TIMEOUT_S", "300")
    monkeypatch.setenv("GEO_ACCOUNT_WAIT_POLL_S", "1")
    _seed_gov_region(governance_db)
    _seed_gov_browser(governance_db)
    account = _seed_gov_account(
        governance_db,
        runtime_state="running",
        current_run_pub_id="run_terminated",
        reservation_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    monkeypatch.setattr(
        browser_router.time,
        "sleep",
        lambda seconds: pytest.fail("expired lease must be reaped before any wait"),
    )
    route = resolve_browser_instance("doubao", "CN-SH", run_pub_id="run_new")
    assert route.instance_key == "doubao_sh"
    assert account.current_run_pub_id == "run_new"


def test_wait_budget_capped_by_activity_start_to_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """等待预算被 activity start_to_close 截断（itemwise 段 15min 装不下缺省
    3600s 等待）——让活动以可分辨的 account_contention_timeout 失败而非被
    Temporal 超时砍掉。"""
    monkeypatch.setenv("GEO_ACCOUNT_WAIT_TIMEOUT_S", "3600")

    class _FakeInfo:
        start_to_close_timeout = timedelta(minutes=15)

    monkeypatch.setattr(browser_router.activity, "in_activity", lambda: True)
    monkeypatch.setattr(browser_router.activity, "info", lambda: _FakeInfo())
    assert browser_router._wait_budget_s() == 15 * 60 - 45
    # activity 上下文外（CLI/直调）= 纯 env 预算
    monkeypatch.setattr(browser_router.activity, "in_activity", lambda: False)
    assert browser_router._wait_budget_s() == 3600.0


def test_wait_poll_clamped_below_heartbeat_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """轮询间隔硬夹 ≤25s：batch activity 的 heartbeat_timeout=30s 必须始终满足。"""
    monkeypatch.setenv("GEO_ACCOUNT_WAIT_POLL_S", "120")
    assert browser_router._wait_poll_s() == 25.0
    monkeypatch.setenv("GEO_ACCOUNT_WAIT_POLL_S", "garbage")
    assert browser_router._wait_poll_s() == 20.0
