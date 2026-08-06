"""Resolve a collection task's region to a verified worker-local proxy.

The task already carries a public region identifier. Proxy credentials never
enter the task, a workflow payload, an activity heartbeat, or a result: this
module resolves them inside the worker immediately before browser launch.

Modes:

``static``
    Preserve the previous per-platform ``GEO_<PLATFORM>_PROXY_URL`` behavior.
``wukong``
    Reconcile/reuse a live Wukong lease for the requested region, otherwise
    fail without changing provider state. Paid acquisition is deliberately
    unavailable from collection execution and requires the explicit CLI path.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import structlog
from temporalio.exceptions import ApplicationError

log = structlog.get_logger()

ENV_ROUTING_MODE = "GEO_REGION_PROXY_MODE"
ENV_MODULE_ROOT = "GEO_WUKONG_MODULE_ROOT"
ENV_CACHE = "GEO_WUKONG_CACHE"
ENV_REGION_GB_MAP = "GEO_REGION_GB_MAP"
ENV_MIN_REMAINING = "GEO_WUKONG_MIN_REMAINING_MIN"

_PLATFORMS = frozenset({"doubao", "deepseek", "yuanbao", "tongyi", "yiyan"})


@dataclass(frozen=True)
class ResolvedRegionProxy:
    proxy_url: str | None
    source: str
    requested_region: str
    region_gb: str | None
    city: str | None
    provider_action: str
    observed_gb: str | None = None


class RegionProxyError(RuntimeError):
    def __init__(self, code: str, message: str, *, non_retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.non_retryable = non_retryable


def _masked_proxy(proxy_url: str | None) -> str | None:
    if not proxy_url:
        return None
    try:
        parsed = urlsplit(proxy_url)
        if not parsed.scheme or not parsed.hostname:
            return "<invalid-proxy-url>"
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        return f"{parsed.scheme}://{host}" + (f":{parsed.port}" if parsed.port else "")
    except ValueError:
        return "<invalid-proxy-url>"


def _custom_region_map() -> dict[str, str]:
    """Parse ``alias:GB,alias:GB`` without allowing malformed partial maps."""
    raw = os.environ.get(ENV_REGION_GB_MAP, "").strip()
    if not raw:
        return {}
    result: dict[str, str] = {}
    for item in raw.split(","):
        alias, sep, gb = item.strip().partition(":")
        if not sep or not alias.strip() or not (gb.strip().isdigit() and len(gb.strip()) == 6):
            raise RegionProxyError(
                "proxy_region_map_invalid",
                f"{ENV_REGION_GB_MAP} must use alias:6-digit-gb entries",
                non_retryable=True,
            )
        result[alias.strip().upper()] = gb.strip()
    return result


def _legacy_modules() -> tuple[type[Any], Any]:
    root = Path(os.environ.get(ENV_MODULE_ROOT, "").strip())
    if not root.is_dir() or not (root / "proxyllm" / "wukong_pool.py").is_file():
        raise RegionProxyError(
            "proxy_provider_not_configured",
            f"{ENV_MODULE_ROOT} must point to the server module root",
            non_retryable=True,
        )
    root_text = str(root.resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    try:
        from geosys import wiring  # type: ignore[import-not-found]
        from proxyllm.wukong_pool import WukongLeasePool  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001 - boundary converted to a typed error
        raise RegionProxyError(
            "proxy_provider_not_configured",
            f"Wukong provider modules are unavailable: {type(exc).__name__}",
            non_retryable=True,
        ) from exc
    return WukongLeasePool, wiring


def _region_to_gb(region: str, wiring: Any) -> str:
    raw = str(region or "").strip()
    mapped = _custom_region_map().get(raw.upper())
    region_gb = mapped or wiring.normalize_region(raw)
    if not region_gb:
        raise RegionProxyError(
            "proxy_region_unmapped",
            f"collection region {raw!r} has no Wukong/GB mapping",
            non_retryable=True,
        )
    return str(region_gb)


def _city_for_gb(region_gb: str, wiring: Any) -> str:
    city = wiring._CITY_BY_GB.get(region_gb, "")
    if not city and region_gb.endswith("0000"):
        city = wiring._CITY_BY_GB.get(wiring._CAPITAL_GB_BY_PROVINCE.get(region_gb, ""), "")
    if not city:
        raise RegionProxyError(
            "proxy_region_unmapped",
            f"GB region {region_gb!r} has no Wukong representative city",
            non_retryable=True,
        )
    return str(city)


def _cache_path() -> Path:
    raw = os.environ.get(ENV_CACHE, "").strip()
    if not raw:
        raise RegionProxyError(
            "proxy_provider_not_configured",
            f"{ENV_CACHE} is required in Wukong routing mode",
            non_retryable=True,
        )
    return Path(raw)


class RegionProxyRouter:
    def __init__(
        self,
        *,
        pool_factory: Callable[..., Any] | None = None,
        wiring: Any | None = None,
    ) -> None:
        self._pool_factory = pool_factory
        self._wiring = wiring

    def _dependencies(self) -> tuple[Callable[..., Any], Any]:
        if self._pool_factory is not None:
            if self._wiring is None:
                raise RuntimeError("injected pool_factory requires wiring")
            return self._pool_factory, self._wiring
        return _legacy_modules()

    def resolve(self, platform: str, region: str) -> ResolvedRegionProxy:
        slug = platform.strip().lower()
        if slug not in _PLATFORMS:
            raise RegionProxyError(
                "proxy_platform_unsupported",
                f"platform {platform!r} is not eligible for regional proxy routing",
                non_retryable=True,
            )
        mode = os.environ.get(ENV_ROUTING_MODE, "static").strip().lower() or "static"
        static_proxy = os.environ.get(f"GEO_{slug.upper()}_PROXY_URL", "").strip() or None
        if mode == "static":
            return ResolvedRegionProxy(
                proxy_url=static_proxy,
                source="static_env",
                requested_region=region,
                region_gb=None,
                city=None,
                provider_action="static",
            )
        if mode != "wukong":
            raise RegionProxyError(
                "proxy_routing_mode_invalid",
                f"{ENV_ROUTING_MODE} must be static or wukong",
                non_retryable=True,
            )

        pool_factory, wiring = self._dependencies()
        region_gb = _region_to_gb(region, wiring)
        city = _city_for_gb(region_gb, wiring)
        cache = _cache_path()
        try:
            min_remaining = float(os.environ.get(ENV_MIN_REMAINING, "20"))
        except ValueError as exc:
            raise RegionProxyError(
                "proxy_provider_not_configured",
                f"{ENV_MIN_REMAINING} must be numeric",
                non_retryable=True,
            ) from exc
        try:
            pool = pool_factory(cache_path=cache)
            lease, action = pool.acquire(
                city,
                buy=False,
                min_remaining_min=min_remaining,
                validate=True,
            )
        except RegionProxyError:
            raise
        except Exception as exc:  # noqa: BLE001 - vendor/network errors are retryable
            raise RegionProxyError(
                "proxy_provider_unavailable",
                f"Wukong acquire failed for {city}: {type(exc).__name__}",
                non_retryable=False,
            ) from exc
        if lease is None:
            raise RegionProxyError(
                "proxy_lease_unavailable",
                f"no reusable Wukong lease for {city}; provider_action={action}; "
                "collection execution never creates orders",
                non_retryable=True,
            )
        if action == "validate_failed":
            raise RegionProxyError(
                "proxy_validation_failed",
                f"Wukong lease for {city} failed reachability validation",
                non_retryable=False,
            )
        proxy, observed_gb = wiring._verify_and_lease(
            lease.proxy_url,
            region_gb,
            region_gb,
            log=lambda message: log.warning("region_proxy_probe", detail=str(message)[:240]),
        )
        if proxy is None:
            raise RegionProxyError(
                "proxy_region_mismatch",
                f"Wukong exit for {city} did not verify as {region_gb}; "
                f"observed={observed_gb or 'unknown'}",
                non_retryable=True,
            )
        return ResolvedRegionProxy(
            proxy_url=str(proxy.proxy_url),
            source="wukong",
            requested_region=region,
            region_gb=region_gb,
            city=city,
            provider_action=str(action),
            observed_gb=str(observed_gb) if observed_gb else None,
        )

    def acquire_paid(self, region: str, *, confirm_spend: bool) -> ResolvedRegionProxy:
        """Explicit operator-only path. Collection execution never calls it."""
        if not confirm_spend:
            raise RegionProxyError(
                "proxy_spend_confirmation_required",
                "paid Wukong acquisition requires confirm_spend=true",
                non_retryable=True,
            )
        pool_factory, wiring = self._dependencies()
        region_gb = _region_to_gb(region, wiring)
        city = _city_for_gb(region_gb, wiring)
        cache = _cache_path()
        try:
            pool = pool_factory(cache_path=cache)
            lease, action = pool.acquire(city, buy=True, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise RegionProxyError(
                "proxy_paid_acquire_failed",
                f"confirmed Wukong purchase failed for {city}: {type(exc).__name__}",
                non_retryable=False,
            ) from exc
        if lease is None:
            code = (
                "proxy_purchase_reconciliation_required"
                if action in {"purchase_uncertain", "purchase_reconciliation_required"}
                else "proxy_paid_acquire_failed"
            )
            raise RegionProxyError(
                code,
                f"confirmed Wukong purchase did not yield a usable lease for {city}; "
                f"action={action}",
                non_retryable=code == "proxy_purchase_reconciliation_required",
            )
        if action == "validate_failed":
            raise RegionProxyError(
                "proxy_validation_failed",
                f"paid Wukong lease for {city} exists but failed reachability validation; "
                "do not purchase again",
                non_retryable=False,
            )
        proxy, observed_gb = wiring._verify_and_lease(
            lease.proxy_url,
            region_gb,
            region_gb,
            log=lambda message: log.warning("region_proxy_probe", detail=str(message)[:240]),
        )
        if proxy is None:
            raise RegionProxyError(
                "proxy_region_mismatch",
                f"paid Wukong exit for {city} did not verify as {region_gb}; "
                f"observed={observed_gb or 'unknown'}",
                non_retryable=True,
            )
        return ResolvedRegionProxy(
            proxy_url=str(proxy.proxy_url),
            source="wukong_paid",
            requested_region=region,
            region_gb=region_gb,
            city=city,
            provider_action=str(action),
            observed_gb=str(observed_gb) if observed_gb else None,
        )

    def clear_purchase_intent(
        self,
        region: str,
        *,
        confirm_no_order: bool,
    ) -> ResolvedRegionProxy:
        """Operator recovery after provider-console verification found no order."""
        if not confirm_no_order:
            raise RegionProxyError(
                "proxy_no_order_confirmation_required",
                "clearing a purchase intent requires confirm_no_order=true",
                non_retryable=True,
            )
        pool_factory, wiring = self._dependencies()
        region_gb = _region_to_gb(region, wiring)
        city = _city_for_gb(region_gb, wiring)
        try:
            pool = pool_factory(cache_path=_cache_path())
            action = pool.clear_purchase_intent(city, confirm_no_order=True)
        except Exception as exc:  # noqa: BLE001
            raise RegionProxyError(
                "proxy_purchase_reconciliation_failed",
                f"purchase-intent reconciliation failed for {city}: {type(exc).__name__}",
                non_retryable=False,
            ) from exc
        return ResolvedRegionProxy(
            proxy_url=None,
            source="wukong_operator",
            requested_region=region,
            region_gb=region_gb,
            city=city,
            provider_action=str(action),
        )


async def resolve_region_proxy(platform: str, region: str) -> ResolvedRegionProxy:
    try:
        result = await asyncio.to_thread(RegionProxyRouter().resolve, platform, region)
    except RegionProxyError as exc:
        raise ApplicationError(
            str(exc),
            type=exc.code,
            non_retryable=exc.non_retryable,
        ) from exc
    log.info(
        "region_proxy_resolved",
        platform=platform,
        requested_region=region,
        region_gb=result.region_gb,
        city=result.city,
        source=result.source,
        provider_action=result.provider_action,
        proxy=_masked_proxy(result.proxy_url),
    )
    return result
