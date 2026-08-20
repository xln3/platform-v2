"""常驻浏览器实例清单 → ``collection_browser`` 运行时镜像同步（设计文档 caiji-0813 §3.4）。

env/systemd 仍是部署真源，表 = 运行时镜像。本模块解析
``GEO_BROWSER_INSTANCES``（env 契约镜像 ``workflows/activities/browser_router.py``：
键 ``{platform}_{regiontag}``、每实例 ``GEO_BROWSER_<KEY>_CDP_URL`` /
``GEO_BROWSER_<KEY>_EXIT_GB``）→ upsert ``collection_browser`` 行。

不 import browser_router（它依赖 temporalio，api 侧保持无 temporal 依赖面，
同 assist_notify 的取舍）；解析逻辑只是同款 env 读取 + 同一实例键正则。

- 幂等：按 ``instance_key`` upsert，重复跑零漂移（返回 created/updated 计数）。
- 诚实：清单未配置 → ValueError（端点映射 503）；单条畸形（键非法/CDP URL
  缺端口/EXIT_GB 非 6 位）跳过该条并如实记进 ``errors``，不拖垮整批
  （采集侧 browser_router 仍 fail-closed，镜像层不放大故障面）。
- DB 访问模式照 account_governor：``conn`` 是 SQLAlchemy ``Session``，
  本函数只 flush，事务边界由调用方持有。
"""

from __future__ import annotations

import os
import re
import urllib.parse
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..tenancy.ids import new_pub_id
from ..tenancy.models import now_utc
from .account_models import CollectionBrowser

# 与 browser_router._INSTANCE_KEY_RE 同值（systemd 实例名安全）。
_INSTANCE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

ENV_BROWSER_INSTANCES = "GEO_BROWSER_INSTANCES"


def _cdp_port(cdp_url: str) -> int | None:
    """从 CDP URL 抽端口（缺省 80/443 按 scheme 补；解析失败/无端口 → None 如实）。"""
    try:
        parsed = urllib.parse.urlparse(cdp_url)
    except ValueError:
        return None
    if parsed.port is not None:
        return parsed.port
    if parsed.scheme == "http":
        return 80
    if parsed.scheme == "https":
        return 443
    return None


def sync_collection_browsers(conn: Session) -> dict[str, Any]:
    """把 GEO_BROWSER_INSTANCES 镜像进 collection_browser。返回同步摘要 dict。"""
    raw = os.environ.get(ENV_BROWSER_INSTANCES, "").strip()
    if not raw:
        raise ValueError("browser_instances_not_configured")
    now = now_utc()
    created = 0
    updated = 0
    errors: list[str] = []
    synced_keys: list[str] = []
    for item in raw.split(","):
        key = item.strip().lower()
        if not _INSTANCE_KEY_RE.fullmatch(key):
            errors.append(f"invalid_instance_key:{item.strip()}")
            continue
        upper = key.upper()
        cdp_url = os.environ.get(f"GEO_BROWSER_{upper}_CDP_URL", "").strip()
        exit_gb = os.environ.get(f"GEO_BROWSER_{upper}_EXIT_GB", "").strip()
        if not cdp_url.startswith(("http://", "https://")):
            errors.append(f"invalid_cdp_url:{key}")
            continue
        if not (exit_gb.isdigit() and len(exit_gb) == 6):
            errors.append(f"invalid_exit_gb:{key}")
            continue
        platform = key.split("_", 1)[0]
        row = conn.scalar(select(CollectionBrowser).where(CollectionBrowser.instance_key == key))
        if row is None:
            row = CollectionBrowser(
                pub_id=new_pub_id("brw"),
                instance_key=key,
                platform=platform,
                region_gb=exit_gb,
                cdp_port=_cdp_port(cdp_url),
                systemd_unit=f"geo-platform-v2-browser@{key}.service",
                activity="idle",
                error_streak=0,
                created_at=now,
                updated_at=now,
            )
            conn.add(row)
            conn.flush()
            created += 1
        else:
            row.platform = platform
            row.region_gb = exit_gb
            row.cdp_port = _cdp_port(cdp_url)
            row.systemd_unit = f"geo-platform-v2-browser@{key}.service"
            row.updated_at = now
            conn.flush()
            updated += 1
        synced_keys.append(key)
    return {
        "synced": len(synced_keys),
        "created": created,
        "updated": updated,
        "errors": errors,
        "instances": synced_keys,
    }
