"""常驻浏览器实例路由（browser_router）全分支单测（无浏览器、无 DB、无 Temporal）。

覆盖：region 归一各形态 / 正常匹配（含省级前缀与清单序确定性）/ 平台无实例 /
地域与出口不符 / region 无法归一 / 清单未配置 / 清单与实例配置畸形 /
resolve_batch_instance 的匀段、混段、空段；以及 resident_cdp_url 的实例键优先
解析（旧 slug 回退逐字节不变）。

env 全部由 monkeypatch 显式搭建（conftest 的全局缺省清单在这些用例里按需
覆写/删除，互不泄漏）。
"""

from __future__ import annotations

import pytest
from temporalio.exceptions import ApplicationError

from workflows.activities.browser_router import (
    ENV_BROWSER_INSTANCES,
    normalize_region_gb,
    resolve_batch_instance,
    resolve_browser_instance,
)
from workflows.activities.collection import CollectionTaskInput
from workflows.activities.resident_browser import resident_cdp_url


def _instances(monkeypatch: pytest.MonkeyPatch, keys: list[str]) -> None:
    monkeypatch.setenv(ENV_BROWSER_INSTANCES, ",".join(keys))


def _instance(
    monkeypatch: pytest.MonkeyPatch, key: str, *, port: int = 19222, exit_gb: str = "310000"
) -> None:
    monkeypatch.setenv(f"GEO_BROWSER_{key.upper()}_CDP_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv(f"GEO_BROWSER_{key.upper()}_EXIT_GB", exit_gb)


@pytest.fixture()
def _prod_topology(monkeypatch: pytest.MonkeyPatch):
    """生产五实例拓扑（与 /etc/geo-platform-v2/worker-adapters.env 同形）。"""
    _instances(monkeypatch, ["doubao_sh", "deepseek_tj", "tongyi_bj", "yiyan_sh", "yuanbao_tj"])
    _instance(monkeypatch, "doubao_sh", port=19222, exit_gb="310000")
    _instance(monkeypatch, "deepseek_tj", port=19224, exit_gb="120000")
    _instance(monkeypatch, "tongyi_bj", port=19225, exit_gb="110000")
    _instance(monkeypatch, "yiyan_sh", port=19226, exit_gb="310000")
    _instance(monkeypatch, "yuanbao_tj", port=19227, exit_gb="120000")


def _task(key: str, adapter: str, region: str) -> CollectionTaskInput:
    return CollectionTaskInput(
        business_key=key, query=f"q-{key}", model="m",
        region=region, mode="normal", adapter=adapter,
    )


# ── region 归一（与旧链 normalize_region 同口径） ──────────────────────────────


def test_normalize_region_forms() -> None:
    assert normalize_region_gb("CN-BJ") == "110000"       # ISO 省码
    assert normalize_region_gb("cn-bj") == "110000"       # 大小写不敏感
    assert normalize_region_gb(" CN-SH ") == "310000"     # 空白容忍
    assert normalize_region_gb("310000") == "310000"      # 6 位 GB 原样透传
    assert normalize_region_gb("上海") == "310000"         # 中文城市名
    assert normalize_region_gb("深圳") == "440300"         # 市级码保持市粒度
    assert normalize_region_gb("") == ""
    assert normalize_region_gb("Atlantis") == ""          # 未识别 → 空（诚实）
    assert normalize_region_gb("31000") == ""             # 非 6 位数字不归一


# ── resolve_browser_instance ──────────────────────────────────────────────────


@pytest.mark.usefixtures("_prod_topology")
def test_resolve_matches_instance_by_platform_and_province() -> None:
    route = resolve_browser_instance("doubao", "CN-SH")
    assert route.instance_key == "doubao_sh"
    assert route.platform == "doubao"
    assert route.exit_gb == "310000"
    assert route.cdp_url == "http://127.0.0.1:19222"
    # 同省市级 region 也命中（省级粒度匹配）
    assert resolve_browser_instance("tongyi", "北京").instance_key == "tongyi_bj"
    assert resolve_browser_instance("deepseek", "120000").instance_key == "deepseek_tj"


@pytest.mark.usefixtures("_prod_topology")
def test_resolve_platform_without_instance_fails() -> None:
    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("fixed", "CN-SH")
    assert exc_info.value.type == "browser_instance_unavailable"
    assert exc_info.value.non_retryable is True


@pytest.mark.usefixtures("_prod_topology")
def test_resolve_region_not_served_by_any_instance_fails() -> None:
    # doubao 只有上海实例——北京任务绝不拿上海出口顶替（诚实失败核心语义）
    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "CN-BJ")
    assert exc_info.value.type == "region_exit_mismatch"
    assert exc_info.value.non_retryable is True


@pytest.mark.usefixtures("_prod_topology")
def test_resolve_unmapped_region_fails_closed() -> None:
    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "Atlantis")
    assert exc_info.value.type == "region_exit_mismatch"
    assert exc_info.value.non_retryable is True


def test_resolve_instances_list_unset_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_BROWSER_INSTANCES, raising=False)
    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "CN-SH")
    assert exc_info.value.type == "browser_instances_not_configured"
    assert exc_info.value.non_retryable is True

    monkeypatch.setenv(ENV_BROWSER_INSTANCES, "   ")
    with pytest.raises(ApplicationError) as exc_info2:
        resolve_browser_instance("doubao", "CN-SH")
    assert exc_info2.value.type == "browser_instances_not_configured"


@pytest.mark.parametrize("bad_list", ["doubao-sh", "doubao_sh,,tongyi_bj",
                                      "doubao_sh,doubao_sh", "_doubao_sh"])
def test_resolve_malformed_instance_list_fails_closed(
    monkeypatch: pytest.MonkeyPatch, bad_list: str
) -> None:
    monkeypatch.setenv(ENV_BROWSER_INSTANCES, bad_list)
    _instance(monkeypatch, "doubao_sh")
    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "CN-SH")
    assert exc_info.value.type == "browser_instances_invalid"
    assert exc_info.value.non_retryable is True


def test_resolve_instance_missing_cdp_url_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _instances(monkeypatch, ["doubao_sh"])
    monkeypatch.setenv("GEO_BROWSER_DOUBAO_SH_EXIT_GB", "310000")
    monkeypatch.delenv("GEO_BROWSER_DOUBAO_SH_CDP_URL", raising=False)
    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "CN-SH")
    assert exc_info.value.type == "browser_instances_invalid"


def test_resolve_instance_bad_cdp_url_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _instances(monkeypatch, ["doubao_sh"])
    monkeypatch.setenv("GEO_BROWSER_DOUBAO_SH_CDP_URL", "ftp://bad")
    monkeypatch.setenv("GEO_BROWSER_DOUBAO_SH_EXIT_GB", "310000")
    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "CN-SH")
    assert exc_info.value.type == "browser_instances_invalid"


@pytest.mark.parametrize("bad_gb", ["", "31000", "3100000", "abcdef"])
def test_resolve_instance_malformed_exit_gb_fails_closed(
    monkeypatch: pytest.MonkeyPatch, bad_gb: str
) -> None:
    _instances(monkeypatch, ["doubao_sh"])
    monkeypatch.setenv("GEO_BROWSER_DOUBAO_SH_CDP_URL", "http://127.0.0.1:19222")
    monkeypatch.setenv("GEO_BROWSER_DOUBAO_SH_EXIT_GB", bad_gb)
    with pytest.raises(ApplicationError) as exc_info:
        resolve_browser_instance("doubao", "CN-SH")
    assert exc_info.value.type == "browser_instances_invalid"


def test_resolve_same_province_multiple_instances_picks_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同平台同省多实例（未来账号维度）：清单序首个，确定性选择。"""
    _instances(monkeypatch, ["doubao_sh", "doubao_sh2"])
    _instance(monkeypatch, "doubao_sh", port=19222, exit_gb="310000")
    _instance(monkeypatch, "doubao_sh2", port=19230, exit_gb="310000")
    assert resolve_browser_instance("doubao", "CN-SH").instance_key == "doubao_sh"


def test_resolve_instance_list_entry_case_and_space_tolerated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_BROWSER_INSTANCES, " Doubao_SH , tongyi_bj")
    _instance(monkeypatch, "doubao_sh", exit_gb="310000")
    _instance(monkeypatch, "tongyi_bj", port=19225, exit_gb="110000")
    assert resolve_browser_instance("doubao", "CN-SH").instance_key == "doubao_sh"


# ── resolve_batch_instance ────────────────────────────────────────────────────


@pytest.mark.usefixtures("_prod_topology")
def test_batch_uniform_segment_resolves_single_instance() -> None:
    items = [_task("a", "doubao", "CN-SH"), _task("b", "doubao", "上海")]
    route = resolve_batch_instance(items)
    assert route is not None and route.instance_key == "doubao_sh"


@pytest.mark.usefixtures("_prod_topology")
def test_batch_mixed_region_segment_fails_loud() -> None:
    """v2 分组之外的混排段（workflow/activity 失配）：绝不静默选边。"""
    items = [_task("a", "doubao", "CN-SH"), _task("b", "doubao", "CN-TJ")]
    # CN-TJ 先撞 region_exit_mismatch（doubao 无天津实例）；两个都有实例的混排
    # 才到 batch_region_mixed——用 yiyan(CN-SH)/yiyan 无津 不行，构造 doubao 双实例：
    with pytest.raises(ApplicationError) as exc_info:
        resolve_batch_instance(items)
    assert exc_info.value.type == "region_exit_mismatch"


def test_batch_mixed_segment_two_valid_instances_fails_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _instances(monkeypatch, ["doubao_sh", "doubao_bj"])
    _instance(monkeypatch, "doubao_sh", port=19222, exit_gb="310000")
    _instance(monkeypatch, "doubao_bj", port=19230, exit_gb="110000")
    items = [_task("a", "doubao", "CN-SH"), _task("b", "doubao", "CN-BJ")]
    with pytest.raises(ApplicationError) as exc_info:
        resolve_batch_instance(items)
    assert exc_info.value.type == "batch_region_mixed"
    assert exc_info.value.non_retryable is True


@pytest.mark.usefixtures("_prod_topology")
def test_batch_empty_segment_returns_none() -> None:
    assert resolve_batch_instance([]) is None


# ── resident_cdp_url 实例键优先（旧 slug 回退不变） ────────────────────────────


def test_cdp_url_instance_key_preferred_over_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEO_BROWSER_DOUBAO_SH_CDP_URL", "http://127.0.0.1:19222")
    monkeypatch.setenv("GEO_DOUBAO_SH_CDP_URL", "http://127.0.0.1:29999")
    assert resident_cdp_url("doubao_sh") == "http://127.0.0.1:19222"


def test_cdp_url_instance_key_falls_back_to_slug_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEO_BROWSER_DOUBAO_SH_CDP_URL", raising=False)
    monkeypatch.setenv("GEO_DOUBAO_SH_CDP_URL", "http://127.0.0.1:29999")
    assert resident_cdp_url("doubao_sh") == "http://127.0.0.1:29999"
    monkeypatch.delenv("GEO_DOUBAO_SH_CDP_URL", raising=False)
    assert resident_cdp_url("doubao_sh") is None


def test_cdp_url_plain_slug_unaffected_by_instance_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧 slug 解析逐字节不变：GEO_BROWSER_DOUBAO_CDP_URL 不参与 slug 解析。"""
    monkeypatch.setenv("GEO_BROWSER_DOUBAO_SH_CDP_URL", "http://127.0.0.1:19222")
    monkeypatch.delenv("GEO_BROWSER_DOUBAO_CDP_URL", raising=False)
    monkeypatch.setenv("GEO_DOUBAO_CDP_URL", "http://127.0.0.1:19000")
    assert resident_cdp_url("doubao") == "http://127.0.0.1:19000"
    monkeypatch.setenv("GEO_DOUBAO_CDP_URL", "not-a-url")
    with pytest.raises(ValueError, match="GEO_DOUBAO_CDP_URL"):
        resident_cdp_url("doubao")
