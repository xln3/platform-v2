"""relay 巡检：经各地域代理探测出口 IP（设计文档 caiji-0813 §6.3 欠费/失效检测）。

- ``probe_collection_region``：从 ``collection_region`` 行取 ``proxy_env_key``
  → 读该 env 键指向的代理地址（凭证明文在 env，不落库）→ 经代理请求 IP echo
  服务 → 结果经 ``AccountGovernor.record_region_probe`` 落库（状态翻转自动记
  relay_probe 事件；人工标注 arrears 不被探测覆盖）。
- 失败才推送方糖告警（复用 captcha-assist 推送通道 env
  ``GEO_ASSIST_NOTIFY_URL``/``GEO_ASSIST_NOTIFY_FLAVOR``，缺省 serverchan；
  未配置只记日志——同 assist_notify 的「绝不抛异常」纪律）。
- HTTP 用 httpx 显式 ``proxy=`` 参数 + ``trust_env=False``：免疫 shell/进程
  环境里的 http_proxy 系变量（本机 mihomo 代理 env 污染教训，2026-08-10）。
- 调度：API 端点（立即巡检）与 business_metrics 15s 循环（每 region 10min）
  两处调用；本模块本身不挂定时器。
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from workflows.activities.assist_notify import push_captcha_assist

from .account_governor import AccountGovernor
from .account_models import CollectionRegion

log = structlog.get_logger()

PROBE_URL = "https://ifconfig.me/ip"
PROBE_TIMEOUT_S = 8.0
_ALERT_TIMEOUT_S = 5.0


def _fetch_exit_ip(proxy_url: str, timeout_s: float = PROBE_TIMEOUT_S) -> str:
    """经指定代理取出口 IP（文本）。trust_env=False：显式代理是唯一生效代理。"""
    with httpx.Client(proxy=proxy_url, trust_env=False, timeout=timeout_s) as client:
        response = client.get(PROBE_URL)
        response.raise_for_status()
        return response.text.strip()


def _push_relay_alert(*, region_gb: str, note: str) -> bool:
    """巡检失败方糖告警。通道未配置 → 只日志返回 False（绝不抛）。"""
    notify_url = os.environ.get("GEO_ASSIST_NOTIFY_URL", "").strip()
    if not notify_url:
        log.warning("relay_probe_alert_unconfigured", region_gb=region_gb, note=note)
        return False
    flavor = os.environ.get("GEO_ASSIST_NOTIFY_FLAVOR", "").strip() or "serverchan"
    return push_captcha_assist(
        flavor=flavor,
        url=notify_url,
        title=f"[GEO采集] relay 巡检失败 {region_gb}",
        body=f"地域 {region_gb} 代理出口探测失败：{note}。该地域已标记 down，账号停派。",
        timeout_s=_ALERT_TIMEOUT_S,
    )


def probe_collection_region(conn: Session, region_gb: str) -> dict[str, Any]:
    """巡检一个地域的 relay 出口。只 flush，事务边界由调用方持有。

    返回 dict：{region_gb, ok, exit_ip, note, alerted}。region 行不存在 →
    LookupError（端点映射 404）。proxy env 缺失 = 配置问题，记 ok=False +
    note='proxy_env_missing'（不推送——配置面问题走配置修正，不刷告警）。
    """
    region = conn.scalar(
        select(CollectionRegion).where(CollectionRegion.region_gb == region_gb)
    )
    if region is None:
        raise LookupError("region_not_found")
    governor = AccountGovernor(conn)
    proxy_env_key = (region.proxy_env_key or "").strip()
    proxy_url = os.environ.get(proxy_env_key, "").strip() if proxy_env_key else ""
    if not proxy_url:
        governor.record_region_probe(region_gb=region_gb, ok=False, note="proxy_env_missing")
        return {
            "region_gb": region_gb,
            "ok": False,
            "exit_ip": None,
            "note": "proxy_env_missing",
            "alerted": False,
        }
    try:
        exit_ip = _fetch_exit_ip(proxy_url)
    except Exception as exc:  # noqa: BLE001 — 任何探测失败都如实落库 + 告警
        note = f"probe_failed:{type(exc).__name__}"
        governor.record_region_probe(region_gb=region_gb, ok=False, note=note)
        alerted = _push_relay_alert(region_gb=region_gb, note=note)
        return {
            "region_gb": region_gb,
            "ok": False,
            "exit_ip": None,
            "note": note,
            "alerted": alerted,
        }
    governor.record_region_probe(region_gb=region_gb, ok=True, exit_ip=exit_ip)
    log.info("relay_probe_ok", region_gb=region_gb, exit_ip=exit_ip)
    return {"region_gb": region_gb, "ok": True, "exit_ip": exit_ip, "note": None, "alerted": False}
