from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_ELIGIBLE_CAPTCHA = frozenset({"not_challenged", "solved_as_human"})
_ELIGIBLE_ACCOUNT = frozenset({"self_pool", "partner_pool", "coop_supplied_under_riskcontrol"})

# INV-1 五元 provenance 键（measurement_eligible 的读取面）。dimensions 出现其中
# 任一键即视为「新路径」负载——由生产 fanout（activities/collection.py）盖章。
ELIGIBILITY_PROVENANCE_KEYS = (
    "captcha_mode",
    "geo_source",
    "account_source",
    "rate_policy",
    "degraded_flag",
    "observed_gb_code",
)


def measurement_eligible(provenance: Mapping[str, Any]) -> bool:
    """Framework-free extraction of the legacy INV-1 measurement eligibility invariant."""
    return bool(
        provenance.get("captcha_mode") in _ELIGIBLE_CAPTCHA
        and provenance.get("geo_source") == "observed_gb_code"
        and provenance.get("account_source") in _ELIGIBLE_ACCOUNT
        and provenance.get("rate_policy") == "pool_burn"
        and int(provenance.get("degraded_flag", 1)) == 0
        and provenance.get("observed_gb_code") not in (None, "")
    )


def _degraded_from_flag(value: Any) -> bool:
    """degraded_flag 判读：非法值 fail-closed 按 degraded 处理（INV-1 宁缺毋滥）。"""
    try:
        return int(value) != 0
    except (TypeError, ValueError):
        return True


def resolve_measurement_eligibility(
    dimensions: Mapping[str, Any],
) -> tuple[bool, bool, dict[str, str]]:
    """INV-1 落库判定唯一入口（analytics 写路径调用）。

    返回 ``(eligible, degraded, stamped_dimensions)``：

    - **新路径**（dimensions 含五元键任一，生产 fanout 盖章的负载）：
      ``eligible`` 由 :func:`measurement_eligible` 真实计算，``degraded`` 取
      ``degraded_flag``，并在返回的 dimensions 副本上盖 ``eligible`` 标记——
      metric_trace/metric_daily 的 dimensions 快照携带该标记，读路径（overview
      聚合）据此与 answer 行的 eligible 列保持同一口径。
    - **旧路径**（无五元键：2026-08-08 前的历史负载/存量 workflow 重放/探针
      工具）：继承现状——``eligible`` 取 ``dimensions['eligible']``（缺省
      ``true``），且**绝不**在 dimensions 上补任何键，保证 metric
      dimensions_hash 与历史写入逐字节一致（重放零漂移）。这是继承现状的
      结构保证：旧写入路径无五元输入，存量 completed 答案 eligible 恒 true。
    """
    if not any(key in dimensions for key in ELIGIBILITY_PROVENANCE_KEYS):
        eligible = str(dimensions.get("eligible", "true")).lower() == "true"
        degraded = str(dimensions.get("degraded", "false")).lower() == "true"
        return eligible, degraded, {str(key): str(value) for key, value in dimensions.items()}
    # degraded_flag 先按 fail-closed 判读成 int，再喂 measurement_eligible——
    # 原谓词对非法值会 int() 抛错，落库路径必须判成 degraded/ineligible 而非炸调用方。
    degraded = _degraded_from_flag(dimensions.get("degraded_flag", 1))
    eligible = measurement_eligible(
        {**dimensions, "degraded_flag": 1 if degraded else 0}
    )
    stamped = {str(key): str(value) for key, value in dimensions.items()}
    stamped["eligible"] = "true" if eligible else "false"
    return eligible, degraded, stamped
