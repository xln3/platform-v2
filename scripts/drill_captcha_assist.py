"""captcha-assist 真机演练驱动（一次性脚本，非产品代码）。

流程：attach 常驻浏览器 → 新标签页打开本地滑块页（class 含 captcha，assist
选页逻辑会选中它）→ 调 captcha_assist_start 起真会话（注册表+推送全走生产
路径）→ 轮询页面 __solved → 用户手机滑过去后自动 stop 会话、关标签、退出。

2026-08-09 起（浏览器矩阵化）：演练目标改为**常驻实例键** ``doubao_sh``
（CDP 走 GEO_BROWSER_DOUBAO_SH_CDP_URL；特征/页面逻辑仍按平台 slug doubao）。

用法：sudo bash -c 'set -a; . /etc/geo-platform-v2/worker-adapters.env; set +a;
      .venv/bin/python scripts/drill_captcha_assist.py'
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflows.activities.captcha_assist import CaptchaAssistInput, captcha_assist_start

SLIDER_URL = "file:///home/xln/geo-system/platform-v2/runtime/drill/slider.html"
TIMEOUT_S = int(os.environ.get("DRILL_TIMEOUT_S", "2700"))  # 演练窗口默认 45min
# 演练目标常驻实例键（浏览器矩阵化）；特征表平台 = 实例键第一段。
DRILL_INSTANCE = os.environ.get("DRILL_INSTANCE", "doubao_sh").strip() or "doubao_sh"
DRILL_PLATFORM = DRILL_INSTANCE.split("_", 1)[0]


async def main() -> None:
    from workflows.activities.browser_driver import load_sync_browser_driver
    from workflows.activities.resident_browser import resident_cdp_url

    _driver, sync_playwright, _PWTimeout = load_sync_browser_driver()
    cdp_url = resident_cdp_url(DRILL_INSTANCE)
    assert cdp_url, f"GEO_BROWSER_{DRILL_INSTANCE.upper()}_CDP_URL 未配置"

    pw = await asyncio.to_thread(sync_playwright().start)
    browser = await asyncio.to_thread(pw.chromium.connect_over_cdp, cdp_url)
    context = browser.contexts[0]
    page = await asyncio.to_thread(context.new_page)
    await asyncio.to_thread(page.goto, SLIDER_URL)
    print("[drill] slider tab opened", flush=True)

    run_pub_id = f"drill-manual-{int(time.time())}"
    started = await captcha_assist_start(
        CaptchaAssistInput(
            tenant_pub_id="drill",
            run_pub_id=run_pub_id,
            platform=DRILL_PLATFORM,
            business_key="drill-slider",
            evidence_ref=None,
            instance_key=DRILL_INSTANCE,
        )
    )
    print(
        f"[drill] assist session started: pushed={started.pushed} url={started.assist_url}",
        flush=True,
    )

    deadline = time.monotonic() + TIMEOUT_S
    solved = False
    while time.monotonic() < deadline:
        try:
            solved = bool(await asyncio.to_thread(page.evaluate, "window.__solved"))
        except Exception as exc:
            print(f"[drill] poll error: {type(exc).__name__}", flush=True)
        if solved:
            break
        await asyncio.sleep(2)
    print(f"[drill] solved={solved}", flush=True)

    from workflows.activities.captcha_assist import CaptchaAssistStopInput, captcha_assist_stop

    await captcha_assist_stop(
        CaptchaAssistStopInput(run_pub_id=run_pub_id, session_id=started.session_id)
    )
    print("[drill] assist session stopped", flush=True)
    try:
        await asyncio.to_thread(page.close)
    except Exception:
        pass
    try:
        await asyncio.to_thread(browser.close)
        await asyncio.to_thread(pw.stop)
    except Exception:
        pass
    print("[drill] done", flush=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
