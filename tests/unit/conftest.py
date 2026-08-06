"""unit 层全局缺省 GEO_BROWSER_FENCING=local（2026-08-06 起）。

生产缺省是 db（跨 worker PG lease fencing，见 workflows/activities/resident_browser.py），
但 unit 层绝不起真 PG——dev 库的 platform.browser_fence 表可能尚未迁移，
db 缺省会让一切经过 platform_browser 的用例 fail-closed 抛 BrowserBusyError。
local = 纯进程内锁，正是这些用例一贯测试的语义；db fencing 行为本身由
tests/unit/test_resident_browser.py 用 fake seam / fake session 显式覆盖
（该文件自行 setenv("GEO_BROWSER_FENCING", "db")，不受此缺省影响）。
"""

import os

os.environ.setdefault("GEO_BROWSER_FENCING", "local")
