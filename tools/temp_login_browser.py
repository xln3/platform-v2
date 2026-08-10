"""临时登录启动器：用指定 relay 以 persistent context 拉起一个实例 profile，开 CDP 供接管驱动。"""

import sys

from patchright.sync_api import sync_playwright

profile_dir, relay_port, cdp_port = sys.argv[1], sys.argv[2], sys.argv[3]

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=False,
        proxy={"server": f"http://127.0.0.1:{relay_port}"},
        args=[f"--remote-debugging-port={cdp_port}", "--remote-debugging-address=127.0.0.1"],
    )
    print(f"TEMP_BROWSER_UP cdp={cdp_port}", flush=True)
    # 保持运行直到被外部终止
    import threading

    threading.Event().wait()
