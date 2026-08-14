"""待办 A live 校准：新代码 _download_yiyan_share_image 在 yiyan_bj 真实页面跑通。

打开历史会话 → 分享 → 分享图片 → 预览窗 → 新捕获函数（拦截 BOS 上传 PUT 分片重组）
→ 落盘 /tmp/yiyan-share-download-live.png 校验 PNG/尺寸。完成后 dismiss 还原。
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

for var in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(var, None)

from patchright.sync_api import sync_playwright  # noqa: E402

CDP = "http://127.0.0.1:19232"
OUT = Path("/tmp/yiyan-share-download-live.png")
MODULE_PATH = (
    Path(__file__).resolve().parent.parent / "workflows" / "activities" / "official_share.py"
)

spec = importlib.util.spec_from_file_location("official_share_probe", MODULE_PATH)
official_share = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = official_share
spec.loader.exec_module(official_share)


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        page = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if "wenxin.baidu.com" in pg.url:
                    page = pg
                    break
            if page:
                break
        if page is None:
            print("NO_WENXIN_PAGE")
            return

        try:
            history = page.get_by_text("国内漏洞无效化/虚拟补丁头部厂商", exact=True).first
            history.click(timeout=8_000)
            page.wait_for_selector("#conversation-flow-container", timeout=15_000)
            page.wait_for_timeout(2_000)

            share = official_share._prepare_yiyan_share_button(page)
            share.click(timeout=8_000)
            image = official_share._first_enabled(
                page,
                (
                    '.share-footer button:has-text("分享图片")',
                    '.share-footer [role="button"]:has-text("分享图片")',
                ),
            )
            image.click(timeout=8_000)
            official_share._first_visible(
                page, ('div[class^="_share-wrapper_"] > div',), timeout_ms=30_000
            )
            page.wait_for_timeout(1_000)

            audit = official_share._download_yiyan_share_image(page, OUT)
            from PIL import Image

            with Image.open(OUT) as im:
                im.load()
                dims = {"width": im.width, "height": im.height, "format": im.format}
            print(json.dumps({"audit": audit, "dims": dims}, ensure_ascii=False, indent=1))
        finally:
            official_share._dismiss_yiyan_share_ui(page)


if __name__ == "__main__":
    main()
