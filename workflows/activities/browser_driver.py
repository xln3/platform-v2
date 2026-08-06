"""Lazy sync browser driver selection without leaking provider-specific types."""

from typing import Any


def load_sync_browser_driver() -> tuple[str, Any, Any]:
    try:
        from patchright import sync_api as patchright_api

        return "patchright", patchright_api.sync_playwright, patchright_api.TimeoutError
    except ImportError:
        from playwright import sync_api as playwright_api

        return "playwright", playwright_api.sync_playwright, playwright_api.TimeoutError
