from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from workflows.activities.deepseek_adapter import DeepseekAdapterConfig
from workflows.activities.doubao_adapter import DoubaoAdapterConfig
from workflows.activities.region_proxy_router import RegionProxyError, RegionProxyRouter
from workflows.activities.tongyi_adapter import TongyiAdapterConfig
from workflows.activities.yiyan_adapter import YiyanAdapterConfig
from workflows.activities.yuanbao_adapter import YuanbaoAdapterConfig


class FakeWiring:
    _CITY_BY_GB = {
        "110000": "北京",
        "310000": "上海",
        "510100": "成都",
    }
    _CAPITAL_GB_BY_PROVINCE = {"510000": "510100"}

    @staticmethod
    def normalize_region(region: str) -> str:
        return {
            "北京": "110000",
            "上海": "310000",
            "成都": "510100",
            "CN-BJ": "110000",
            "CN-SC": "510000",
        }.get(region, "")

    @staticmethod
    def _verify_and_lease(
        proxy_url: str, region_gb: str, expected: str, *, log: Any
    ) -> tuple[Any | None, str | None]:
        del log
        if "wrong-region" in proxy_url:
            return None, "310000"
        assert region_gb == expected
        return SimpleNamespace(proxy_url=proxy_url), region_gb


class PoolFactory:
    def __init__(self, *, lease: Any = None, action: str = "reused") -> None:
        self.lease = lease
        self.action = action
        self.constructed: list[dict[str, Any]] = []
        self.acquired: list[dict[str, Any]] = []
        self.cleared: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> PoolFactory:
        self.constructed.append(kwargs)
        return self

    def acquire(self, city: str, **kwargs: Any) -> tuple[Any, str]:
        self.acquired.append({"city": city, **kwargs})
        return self.lease, self.action

    def clear_purchase_intent(self, city: str, **kwargs: Any) -> str:
        self.cleared.append({"city": city, **kwargs})
        return self.action


@pytest.fixture
def wukong_env(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEO_REGION_PROXY_MODE", "wukong")
    monkeypatch.setenv("GEO_WUKONG_CACHE", str(tmp_path / "leases.json"))
    monkeypatch.setenv("GEO_WUKONG_MIN_REMAINING_MIN", "25")


def test_static_mode_preserves_platform_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEO_REGION_PROXY_MODE", "static")
    monkeypatch.setenv("GEO_DOUBAO_PROXY_URL", "http://user:pass@static.example:8080")
    result = RegionProxyRouter().resolve("doubao", "CN-BJ")
    assert result.source == "static_env"
    assert result.proxy_url == "http://user:pass@static.example:8080"
    assert result.region_gb is None


def test_wukong_reuses_region_lease_and_folds_province_to_capital(
    wukong_env: None,
) -> None:
    lease = SimpleNamespace(proxy_url="http://user:pass@gateway.example:8080")
    factory = PoolFactory(lease=lease, action="reused")
    router = RegionProxyRouter(pool_factory=factory, wiring=FakeWiring)

    result = router.resolve("deepseek", "CN-SC")

    assert result.region_gb == "510000"
    assert result.city == "成都"
    assert result.observed_gb == "510000"
    assert result.provider_action == "reused"
    assert set(factory.constructed[0]) == {"cache_path"}
    assert factory.acquired == [
        {
            "city": "成都",
            "buy": False,
            "min_remaining_min": 25.0,
            "validate": True,
        }
    ]


def test_collection_never_creates_order_when_no_reusable_lease(
    wukong_env: None,
) -> None:
    factory = PoolFactory(lease=None, action="empty_no_buy")
    router = RegionProxyRouter(pool_factory=factory, wiring=FakeWiring)
    with pytest.raises(RegionProxyError) as exc_info:
        router.resolve("yuanbao", "CN-BJ")
    assert exc_info.value.code == "proxy_lease_unavailable"
    assert exc_info.value.non_retryable is True
    assert factory.acquired[0]["buy"] is False
    assert "password" not in str(exc_info.value).lower()


def test_unknown_region_and_wrong_exit_fail_closed(wukong_env: None) -> None:
    good = SimpleNamespace(proxy_url="http://user:pass@gateway.example:8080")
    router = RegionProxyRouter(pool_factory=PoolFactory(lease=good), wiring=FakeWiring)
    with pytest.raises(RegionProxyError) as unmapped:
        router.resolve("doubao", "CN-UNKNOWN")
    assert unmapped.value.code == "proxy_region_unmapped"

    wrong = SimpleNamespace(proxy_url="http://user:pass@wrong-region.example:8080")
    router = RegionProxyRouter(pool_factory=PoolFactory(lease=wrong), wiring=FakeWiring)
    with pytest.raises(RegionProxyError) as mismatch:
        router.resolve("doubao", "CN-BJ")
    assert mismatch.value.code == "proxy_region_mismatch"


def test_paid_path_requires_confirmation_and_is_never_implicit(wukong_env: None) -> None:
    lease = SimpleNamespace(proxy_url="http://user:pass@gateway.example:8080")
    factory = PoolFactory(lease=lease, action="bought")
    router = RegionProxyRouter(pool_factory=factory, wiring=FakeWiring)
    with pytest.raises(RegionProxyError) as unconfirmed:
        router.acquire_paid("CN-BJ", confirm_spend=False)
    assert unconfirmed.value.code == "proxy_spend_confirmation_required"
    assert factory.constructed == []

    result = router.acquire_paid("CN-BJ", confirm_spend=True)
    assert result.source == "wukong_paid"
    assert set(factory.constructed[0]) == {"cache_path"}
    assert factory.acquired[0]["buy"] is True


def test_uncertain_purchase_blocks_retry_and_intent_clear_requires_confirmation(
    wukong_env: None,
) -> None:
    uncertain = PoolFactory(lease=None, action="purchase_uncertain")
    router = RegionProxyRouter(pool_factory=uncertain, wiring=FakeWiring)
    with pytest.raises(RegionProxyError) as blocked:
        router.acquire_paid("CN-BJ", confirm_spend=True)
    assert blocked.value.code == "proxy_purchase_reconciliation_required"
    assert blocked.value.non_retryable is True

    with pytest.raises(RegionProxyError) as unconfirmed:
        router.clear_purchase_intent("CN-BJ", confirm_no_order=False)
    assert unconfirmed.value.code == "proxy_no_order_confirmation_required"

    uncertain.action = "cleared"
    result = router.clear_purchase_intent("CN-BJ", confirm_no_order=True)
    assert result.provider_action == "cleared"
    assert uncertain.cleared == [{"city": "北京", "confirm_no_order": True}]


@pytest.mark.parametrize(
    ("config_cls", "profile_env", "proxy_env"),
    [
        (DoubaoAdapterConfig, "GEO_DOUBAO_PROFILE_DIR", "GEO_DOUBAO_PROXY_URL"),
        (DeepseekAdapterConfig, "GEO_DEEPSEEK_PROFILE_DIR", "GEO_DEEPSEEK_PROXY_URL"),
        (YuanbaoAdapterConfig, "GEO_YUANBAO_PROFILE_DIR", "GEO_YUANBAO_PROXY_URL"),
        (TongyiAdapterConfig, "GEO_TONGYI_PROFILE_DIR", "GEO_TONGYI_PROXY_URL"),
        (YiyanAdapterConfig, "GEO_YIYAN_PROFILE_DIR", "GEO_YIYAN_PROXY_URL"),
    ],
)
def test_adapter_runtime_override_precedes_static_env(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    config_cls: Any,
    profile_env: str,
    proxy_env: str,
) -> None:
    monkeypatch.setenv(profile_env, str(tmp_path))
    monkeypatch.setenv(proxy_env, "http://old:secret@static.example:8080")
    config = config_cls.from_env(proxy_url_override="http://dynamic:secret@regional.example:9090")
    assert config.proxy_url == "http://dynamic:secret@regional.example:9090"
