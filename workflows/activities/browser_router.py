"""常驻浏览器实例路由（浏览器矩阵化，2026-08-09 起）。

实例键 = ``{platform}_{regiontag}``（下划线小写，如 ``doubao_sh`` /
``deepseek_tj``；第一段恒为平台 slug）。实例键作为 opaque "platform" 喂进既有
机制——``resident_browser.platform_browser`` / ``browser_lock`` /
``resident_cdp_url``（实例键优先读 ``GEO_BROWSER_<KEY>_CDP_URL``）与
``platform.browser_fence``（键列 String(80) opaque，无需迁移）——本模块只做
「解析」：把 (adapter slug, task region) 映到具体实例。

数据源 = worker env 实例清单（**fail-closed**，绝不静默替换/静默直连）：

- ``GEO_BROWSER_INSTANCES=doubao_sh,deepseek_tj,...``（逗号分隔实例键）；
- 每实例 ``GEO_BROWSER_<KEY_UPPER>_CDP_URL``（http(s) URL，与
  ``resident_cdp_url`` 同款校验）——指向该实例的 supervisor 常驻浏览器；
- 每实例 ``GEO_BROWSER_<KEY_UPPER>_EXIT_GB``（6 位 GB 省码，实例静态住宅
  中继的出口省码；与 ``GEO_MEASUREMENT_EXIT_GB_MAP`` 同口径同真源）。

路由语义（诚实失败是核心需求，全部 ``ApplicationError`` non_retryable）：

- 清单未配置/为空 → ``browser_instances_not_configured``；
- 清单条目或实例配置畸形（键非法/重复、CDP URL 缺失或非法、GB 码非 6 位数字）
  → ``browser_instances_invalid``（不容忍半张图）；
- 平台在清单里零实例 → ``browser_instance_unavailable``；
- region 无法归一为 GB 码，或归一后与该平台的全部实例 ``exit_gb`` 省码不符
  → ``region_exit_mismatch``（有实例但地域对不上——绝不拿别的地域出口顶替）。

采集账号治理消费（2026-08-14 起，设计文档 caiji-0813 §1.3 派题链 / §6.2）：
resolve 先经 ``AccountGovernor.resolve_collectable(platform, region_gb)`` 读实体表——

- 命中 → 用其绑定实例键（env 仍是 CDP/出口真源，须仍在 ``GEO_BROWSER_INSTANCES``
  清单内），并校验出口省码与派题地域一致，不一致 fail-closed ``region_ip_mismatch``
  （配置错误，绝不带错地域出口硬采）；
- 豆包一行账号都没有 → ``account_unavailable(account_unregistered)``；豆包已进入
  正式治理，任何入口都不得绕过账号/模式额度墙；其他尚未迁移的平台才允许
  **env 清单回退**（过渡期保命，structlog 记 ``legacy_unmanaged``）；
- 有账号但全不可用（全忙/额度尽/禁言/region_down）→ ``account_unavailable``
  non_retryable（带 reason；workflow 侧转等长占位落库——绝不回退 env 硬撞，
  也绝不让整批 run failed）；
- 治理层 DB 异常 → ``account_unavailable(governor_error)`` fail-closed；只有显式
  ``GEO_ACCOUNT_GOVERNANCE=off`` 应急开关可以绕过治理。

开关 ``GEO_ACCOUNT_GOVERNANCE=db|off``（缺省 db；off=跳过治理层直走 env 清单，
单测/应急 kill switch——与 ``GEO_BROWSER_FENCING`` 同款闸门先例）。

地域匹配粒度 = 省级（GB 码前两位）：测量合格性（INV-1 geo provenance）本身
就是省码粒度；市级 region（如 深圳→440300）由同省实例（exit 44xxxx）服务
是正确的。同平台同省多实例（未来账号维度）取清单序首个，确定性选择。

region→GB 归一词表 vendored 自旧链 ``geosys.wiring.normalize_region`` 同源
（``_ISO_TO_GB_31`` + 中文城市名 + 6 位 GB 原样透传，2026-08-09 对齐），与
``region_proxy_router`` 的 wukong 路径同口径；本模块不 import 旧链——worker
env 可能无 ``GEO_WUKONG_MODULE_ROOT``。env 清单解析保持纯函数（无 IO/时钟）；
治理消费段新增 worker DB 依赖（``WorkerSessionLocal``，异常一律 fail-closed；
显式 ``GEO_ACCOUNT_GOVERNANCE=off`` 才走无治理 env 路径）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

import structlog
from geo_platform.collection.account_governor import AccountGovernor
from geo_platform.collection.account_models import (
    CollectionBrowser,
    CollectionPlatformAccount,
    CollectionRegion,
)
from geo_platform.tenancy.database import WorkerSessionLocal
from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio.exceptions import ApplicationError

log = structlog.get_logger()

ENV_BROWSER_INSTANCES = "GEO_BROWSER_INSTANCES"

# 采集账号治理消费开关（2026-08-14 起）：db=先消费 AccountGovernor 实体状态
# （缺省）；off=跳过治理层直走 env 清单（单测/应急 kill switch）。
ENV_ACCOUNT_GOVERNANCE = "GEO_ACCOUNT_GOVERNANCE"

# 豆包已进入正式账号治理；其他平台按账号表分阶段迁移，暂无账号行时仍可走
# legacy env 路由。显式 GEO_ACCOUNT_GOVERNANCE=off 仍是唯一应急绕过方式。
_GOVERNANCE_REQUIRED_PLATFORMS = frozenset({"doubao"})

# 与 tools/resident_browser.py 的 RESIDENT_PLATFORM 同一正则：实例键可以直接
# 当 opaque platform 进 browser-%i.env / fence / 锁（不含连字符，systemd 实例名安全）。
_INSTANCE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

# ── region→GB 归一词表（vendored：geosys.wiring._ISO_TO_GB_31 / _CITY_BY_GB 同源） ──
_ISO_TO_GB_31 = {
    "CN-BJ": "110000",
    "CN-TJ": "120000",
    "CN-HE": "130000",
    "CN-SX": "140000",
    "CN-NM": "150000",
    "CN-LN": "210000",
    "CN-JL": "220000",
    "CN-HL": "230000",
    "CN-SH": "310000",
    "CN-JS": "320000",
    "CN-ZJ": "330000",
    "CN-AH": "340000",
    "CN-FJ": "350000",
    "CN-JX": "360000",
    "CN-SD": "370000",
    "CN-HA": "410000",
    "CN-HB": "420000",
    "CN-HN": "430000",
    "CN-GD": "440000",
    "CN-GX": "450000",
    "CN-HI": "460000",
    "CN-CQ": "500000",
    "CN-SC": "510000",
    "CN-GZ": "520000",
    "CN-YN": "530000",
    "CN-XZ": "540000",
    "CN-SN": "610000",
    "CN-GS": "620000",
    "CN-QH": "630000",
    "CN-NX": "640000",
    "CN-XJ": "650000",
}

# 中文城市名 → GB（旧链 _CITY_BY_GB 的逆映射同源：直辖市/省会 + 深广杭等代表城）。
_CITY_NAME_TO_GB = {
    "北京": "110000",
    "上海": "310000",
    "深圳": "440300",
    "广州": "440100",
    "杭州": "330100",
    "成都": "510100",
    "重庆": "500000",
    "天津": "120000",
    "武汉": "420100",
    "南京": "320100",
    "西安": "610100",
    "长沙": "430100",
    "石家庄": "130100",
    "太原": "140100",
    "呼和浩特": "150100",
    "沈阳": "210100",
    "长春": "220100",
    "哈尔滨": "230100",
    "合肥": "340100",
    "福州": "350100",
    "南昌": "360100",
    "济南": "370100",
    "郑州": "410100",
    "南宁": "450100",
    "海口": "460100",
    "贵阳": "520100",
    "昆明": "530100",
    "拉萨": "540100",
    "兰州": "620100",
    "西宁": "630100",
    "银川": "640100",
    "乌鲁木齐": "650100",
}


def normalize_region_gb(region: str) -> str:
    """region 归一到 6 位 GB 码（与旧链 normalize_region 同口径）：

    已是 6 位数字 → 原样；CN-XX（大小写不敏感）→ 省码；中文城市名 → 市码；
    空/未识别 → ""。
    """
    s = str(region or "").strip()
    if not s:
        return ""
    if s.isdigit() and len(s) == 6:
        return s
    return _ISO_TO_GB_31.get(s.upper()) or _CITY_NAME_TO_GB.get(s, "")


@dataclass(frozen=True)
class InstanceRoute:
    """一次实例路由的结果：实例键 + 平台 slug + 出口省码 + CDP URL。"""

    instance_key: str
    platform: str
    exit_gb: str
    cdp_url: str


def _fail(error_type: str, message: str) -> ApplicationError:
    return ApplicationError(message, type=error_type, non_retryable=True)


def _instance_keys() -> list[str]:
    """解析 GEO_BROWSER_INSTANCES（保序）。未配置/为空/条目畸形一律 fail-closed。"""
    raw = os.environ.get(ENV_BROWSER_INSTANCES, "").strip()
    if not raw:
        raise _fail(
            "browser_instances_not_configured",
            f"{ENV_BROWSER_INSTANCES} is not set — batch collection requires an "
            "explicit resident-browser instance list (fail-closed)",
        )
    keys: list[str] = []
    for item in raw.split(","):
        key = item.strip().lower()
        if not _INSTANCE_KEY_RE.fullmatch(key):
            raise _fail(
                "browser_instances_invalid",
                f"{ENV_BROWSER_INSTANCES} entry {item.strip()!r} is not a valid "
                "instance key (expected ^[a-z][a-z0-9_]{0,31}$)",
            )
        if key in keys:
            raise _fail(
                "browser_instances_invalid",
                f"{ENV_BROWSER_INSTANCES} has duplicate instance key {key!r}",
            )
        keys.append(key)
    return keys


def _load_instance(key: str) -> InstanceRoute:
    """读单实例的 CDP/EXIT_GB 并校验；缺项/畸形 → browser_instances_invalid。"""
    upper = key.upper()
    cdp_url = os.environ.get(f"GEO_BROWSER_{upper}_CDP_URL", "").strip()
    if not cdp_url:
        raise _fail(
            "browser_instances_invalid",
            f"GEO_BROWSER_{upper}_CDP_URL is not set for instance {key!r}",
        )
    if not cdp_url.startswith(("http://", "https://")) or len(cdp_url) > 200:
        raise _fail(
            "browser_instances_invalid",
            f"GEO_BROWSER_{upper}_CDP_URL is not a valid http(s) URL",
        )
    exit_gb = os.environ.get(f"GEO_BROWSER_{upper}_EXIT_GB", "").strip()
    if not (exit_gb.isdigit() and len(exit_gb) == 6):
        raise _fail(
            "browser_instances_invalid",
            f"GEO_BROWSER_{upper}_EXIT_GB must be a 6-digit GB code for instance {key!r}",
        )
    platform = key.split("_", 1)[0]
    return InstanceRoute(instance_key=key, platform=platform, exit_gb=exit_gb, cdp_url=cdp_url)


def account_governance_enabled() -> bool:
    """GEO_ACCOUNT_GOVERNANCE=db|off（缺省 db）：off = 跳过治理层直走 env 清单。

    任何非 "off" 值按 db 处理；治理层自身的 DB 异常 fail-closed。
    """
    return os.environ.get(ENV_ACCOUNT_GOVERNANCE, "db").strip().lower() != "off"


def _worker_session() -> Session:
    """worker 侧 DB session（seam：单测替换为 fake session 工厂；生产 = WorkerSessionLocal）。"""
    return WorkerSessionLocal()


def _governor_decision(
    slug: str, region_gb: str, mode: str | None = None
) -> tuple[str, dict[str, Any] | None]:
    """治理层派题判定（读实体表；governor 的 lazy resume/reset 随之提交落库）。

    返回 (decision, payload)：

    - ``("hit", payload)``：resolve_collectable 命中（payload 含
      browser_instance_key / platform_account_pub_id / remaining_today）；
    - ``("no_account_registered", None)``：尚未强制治理的平台一行账号都没有
      → 调用方 env 回退（过渡期保命）；
    - 豆包一行账号都没有 → ``("unavailable", {"reason":
      "account_unregistered"})``，任何采集入口都不得绕过正式治理；
    - ``("unavailable", {"reason": ...})``：region_down / 有账号但全不可用
      （全忙/额度尽/禁言/实例熔断）→ 调用方 account_unavailable 信号，
      绝不 env 回退硬撞；
    - ``("unavailable", {"reason": "governor_error"})``：治理层 DB 异常也
      fail-closed；隐式故障绝不等价于显式关闭治理。
    """
    try:
        with _worker_session() as session:
            governor = AccountGovernor(session)
            resolved = governor.resolve_collectable(platform=slug, region_gb=region_gb, mode=mode)
            if resolved is not None:
                session.commit()
                return "hit", resolved
            reason = _unavailable_reason(session, slug, region_gb, mode=mode)
            session.commit()
    except Exception as exc:  # noqa: BLE001 — 治理状态未知必须 fail-closed
        log.warning(
            "account_governor_resolve_error",
            platform=slug,
            region_gb=region_gb,
            error=repr(exc),
        )
        return "unavailable", {"reason": "governor_error"}
    if reason == "no_account_registered":
        if slug in _GOVERNANCE_REQUIRED_PLATFORMS:
            log.warning(
                "account_resolve_required_account_missing",
                platform=slug,
                region_gb=region_gb,
                reason="account_unregistered",
            )
            return "unavailable", {"reason": "account_unregistered"}
        log.warning(
            "account_resolve_env_fallback",
            platform=slug,
            region_gb=region_gb,
            reason="legacy_unmanaged",
        )
        return "no_account_registered", None
    return "unavailable", {"reason": reason}


def _unavailable_reason(
    session: Session, slug: str, region_gb: str, *, mode: str | None = None
) -> str:
    """governor 返回 None 后的原因甄别（governor 读路径只落 structlog 不落审计，
    调度侧需要机器可读 reason 决定 env 回退还是 account_unavailable 占位）。

    与 resolve_collectable 的判定同序：region 行 state!='ok' 优先于账号存在性
    （region_down 时账号有无都不 env 回退——该地域出口已不可信）。
    """
    region = session.scalar(select(CollectionRegion).where(CollectionRegion.region_gb == region_gb))
    if region is not None and region.state != "ok":
        return "region_down"
    registered = list(
        session.scalars(
            select(CollectionPlatformAccount).where(
                CollectionPlatformAccount.platform == slug,
                CollectionPlatformAccount.region_gb == region_gb,
            )
        )
    )
    if not registered:
        return "no_account_registered"
    for account in registered:
        snapshot = account.quota_probe_json or {}
        raw_blocks = snapshot.get("mode_quota_blocks") if isinstance(snapshot, dict) else None
        if isinstance(raw_blocks, dict) and (mode in raw_blocks if mode else bool(raw_blocks)):
            return "mode_quota_exhausted"
        if not account.browser_instance_key:
            return "no_bound_browser"
        browser = session.scalar(
            select(CollectionBrowser).where(
                CollectionBrowser.instance_key == account.browser_instance_key
            )
        )
        if browser is None:
            return "browser_unregistered"
        if browser.activity != "idle":
            return f"browser_{browser.activity}"
    return "no_collectable_account"


def _route_from_governor_hit(slug: str, region_gb: str, payload: dict[str, Any]) -> InstanceRoute:
    """治理命中 → 实例路由。绑定键仍须过 env 校验（CDP/出口真源）+ 地域一致性。

    - 账号未绑定浏览器（key 为空）→ account_unavailable(no_bound_browser)：
      治理行存在但派题链不完整，诚实占位而非回退 env 撞别人的会话；
    - 键不在 GEO_BROWSER_INSTANCES 清单 / env 配置缺失畸形 → 治理与部署真源
      不一致，fail-closed browser_instances_invalid；
    - 键平台段 ≠ 派题平台 → 绑定错平台，fail-closed browser_instances_invalid；
    - 实例出口省码 ≠ 派题地域省码 → 「地域IP不匹配」硬约束，fail-closed
      region_ip_mismatch（设计文档 §1.2/§1.3）。
    """
    key = str(payload.get("browser_instance_key") or "").strip().lower()
    if not key:
        raise _fail(
            "account_unavailable",
            f"governed account {payload.get('platform_account_pub_id')!r} for platform "
            f"{slug!r} region gb={region_gb} has no bound browser instance "
            "(reason=no_bound_browser)",
        )
    if key not in _instance_keys():
        raise _fail(
            "browser_instances_invalid",
            f"governed browser instance {key!r} is not in {ENV_BROWSER_INSTANCES} "
            "(governance/deployment mismatch — fail-closed)",
        )
    route = _load_instance(key)
    if route.platform != slug:
        raise _fail(
            "browser_instances_invalid",
            f"governed browser instance {key!r} platform segment does not match {slug!r}",
        )
    if route.exit_gb[:2] != region_gb[:2]:
        raise _fail(
            "region_ip_mismatch",
            f"governed browser instance {key!r} exit gb={route.exit_gb} does not serve "
            f"task region gb={region_gb} for platform {slug!r} (region/IP mismatch — "
            "fail-closed, never collect through a wrong-region exit)",
        )
    return route


def resolve_browser_instance(platform: str, region: str, mode: str | None = None) -> InstanceRoute:
    """(adapter slug, task region, mode) → 常驻实例路由。

    派题链（2026-08-14 起）：先消费 AccountGovernor 实体状态（命中用其绑定实例；
    豆包无账号行直接 account_unavailable；尚未迁移的平台无账号行才回退 env；
    治理故障或有账号全不可用 → account_unavailable non_retryable，由 workflow
    侧转等长占位，绝不回退 env 硬撞、绝不拖垮整批）。
    env 路径保持纯函数：平台段匹配 = 实例键第一段（``{platform}_{regiontag}``）；
    地域匹配 = 省码（region 归一 GB 与实例 exit_gb 的前两位相等）。同省多实例
    取清单序首个。
    """
    slug = str(platform or "").strip().lower()
    region_gb = normalize_region_gb(region)
    if region_gb and account_governance_enabled():
        decision, payload = _governor_decision(slug, region_gb, mode)
        if decision == "hit" and payload is not None:
            return _route_from_governor_hit(slug, region_gb, payload)
        if decision == "unavailable":
            reason = (payload or {}).get("reason") or "no_collectable_account"
            raise _fail(
                "account_unavailable",
                f"no collectable governed account for platform {slug!r} region "
                f"gb={region_gb} (reason={reason}) — refusing env fallback "
                "(never burn another account's session)",
            )
        # 只有 no_account_registered → env 清单回退（fallback 已记日志）。
    candidates = [_load_instance(key) for key in _instance_keys() if key.split("_", 1)[0] == slug]
    if not candidates:
        raise _fail(
            "browser_instance_unavailable",
            f"no resident browser instance for platform {slug!r} in {ENV_BROWSER_INSTANCES}",
        )
    if not region_gb:
        raise _fail(
            "region_exit_mismatch",
            f"collection region {region!r} has no ISO/GB mapping — cannot verify "
            f"exit-province consistency for platform {slug!r}",
        )
    for route in candidates:
        if route.exit_gb[:2] == region_gb[:2]:
            return route
    raise _fail(
        "region_exit_mismatch",
        f"no {slug!r} instance serves region {region!r} (gb={region_gb}): "
        f"available exits: {sorted({r.exit_gb for r in candidates})}",
    )


def resolve_batch_instance(items: Any) -> InstanceRoute | None:
    """batch 段（同 adapter 同 region 的连续任务）→ 唯一实例路由。

    - 空段 → None（零浏览器交互，空 batch 契约不变）；
    - 段内逐题解析，全部命中同一实例 → 该路由；
    - 段内出现两个不同实例（v2 分组之外的混排，属 workflow/activity 失配）
      → ``batch_region_mixed`` non_retryable（fail loud，绝不静默选边）。
    """
    routes: dict[str, InstanceRoute] = {}
    for item in items:
        route = resolve_browser_instance(item.adapter, item.region, item.mode)
        routes.setdefault(route.instance_key, route)
    if not routes:
        return None
    if len(routes) > 1:
        raise _fail(
            "batch_region_mixed",
            f"batch segment spans multiple browser instances: {sorted(routes)} "
            "(expected a uniform (adapter, region, mode) segment)",
        )
    return next(iter(routes.values()))
