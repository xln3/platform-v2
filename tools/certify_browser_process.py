"""Read-only production smoke check: isolated driver attaches to resident CDP.

Does not navigate, submit questions, close tabs, or close the resident browser.
"""

import argparse
import asyncio
import json
from typing import Any

from workflows.activities.browser_process import run_isolated_browser


def probe(cdp_url: str, *, events: Any, stop: Any) -> dict[str, Any]:
    from patchright.sync_api import sync_playwright

    events.put(("stage", "cdp_attach"))
    driver = sync_playwright().start()
    try:
        browser = driver.chromium.connect_over_cdp(cdp_url, timeout=20000)
        return {"connected": browser.is_connected(), "version": browser.version}
    finally:
        # Disconnect the test driver only; browser.close() is deliberately absent.
        driver.stop()


async def certify(cdp_url: str) -> None:
    stopped: list[str] = []

    async def on_stopped(holder: str) -> None:
        stopped.append(holder)

    result = await asyncio.wait_for(
        run_isolated_browser(probe, (cdp_url,), on_event=lambda *_: None, on_stopped=on_stopped),
        timeout=30,
    )
    assert result["connected"] and len(stopped) == 1
    print(json.dumps({**result, "child_stopped": True}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-url", required=True)
    asyncio.run(certify(parser.parse_args().cdp_url))


if __name__ == "__main__":
    main()
