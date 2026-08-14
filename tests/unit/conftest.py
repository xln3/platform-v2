"""unit 层全局缺省 GEO_BROWSER_FENCING=local（2026-08-06 起）。

生产缺省是 db（跨 worker PG lease fencing，见 workflows/activities/resident_browser.py），
但 unit 层绝不起真 PG——dev 库的 platform.browser_fence 表可能尚未迁移，
db 缺省会让一切经过 platform_browser 的用例 fail-closed 抛 BrowserBusyError。
local = 纯进程内锁，正是这些用例一贯测试的语义；db fencing 行为本身由
tests/unit/test_resident_browser.py 用 fake seam / fake session 显式覆盖
（该文件自行 setenv("GEO_BROWSER_FENCING", "db")，不受此缺省影响）。

2026-08-09 起（浏览器矩阵化）再加全局缺省常驻实例清单：batch activity 入口
（run_<slug>_batch）一律经 browser_router 解析 (adapter, region)→实例，
清单未配置 = fail-closed。这里镜像生产五实例拓扑（实例键/出口省码/CDP 端口），
batch 用例只需让任务 region 落在该平台的实例省份（如 doubao→CN-SH）；
路由自身的全分支行为由 tests/unit/test_browser_router.py 显式覆盖
（该文件用 monkeypatch 覆写/删除这些缺省，互不影响）。
"""

import os

os.environ.setdefault("GEO_BROWSER_FENCING", "local")

# 采集账号治理消费（2026-08-14 起，browser_router GEO_ACCOUNT_GOVERNANCE）：
# 生产缺省 db（先读 AccountGovernor 实体表），但 unit 层绝不起真 PG（dev 栈
# 2026-08-13 已下线）——缺省 off 让既有路由/适配器用例保持纯 env 语义零 DB
# 探测；治理消费行为由 test_browser_router_governance.py /
# test_collection_governance_wiring.py 用 fake session seam 显式覆盖（自行
# setenv("GEO_ACCOUNT_GOVERNANCE", "db")）。
os.environ.setdefault("GEO_ACCOUNT_GOVERNANCE", "off")

for _key, _port, _exit_gb in (
    ("doubao_sh", 19222, "310000"),
    ("deepseek_tj", 19224, "120000"),
    ("tongyi_bj", 19225, "110000"),
    ("yiyan_sh", 19226, "310000"),
    ("yuanbao_tj", 19227, "120000"),
):
    os.environ.setdefault(f"GEO_BROWSER_{_key.upper()}_CDP_URL", f"http://127.0.0.1:{_port}")
    os.environ.setdefault(f"GEO_BROWSER_{_key.upper()}_EXIT_GB", _exit_gb)
os.environ.setdefault(
    "GEO_BROWSER_INSTANCES", "doubao_sh,deepseek_tj,tongyi_bj,yiyan_sh,yuanbao_tj"
)
