#!/usr/bin/env python3
"""OTP 取码轮询 CLI（开户自动化 glue）：轮询 V2 ``GET /api/v2/otp/latest`` 直到拿到新鲜验证码。

用法::

    set -a; . /etc/geo-platform-v2/worker-adapters.env; set +a
    .venv/bin/python tools/otp_wait.py --phone 15510162660 --timeout 180 \
        --base https://127.0.0.1:8443 --token-env GEO_OTP_OPERATOR_TOKEN

每 ``--interval`` 秒（缺省 2）轮询一次；拿到码打印**明文码**到 stdout 并 exit 0
（码是 operator 机密，调用方自行保管；stderr 只出现诊断）。退出码：

- 0  拿到码；
- 2  超时（``--timeout`` 秒内服务端始终没有 within 窗内的新鲜码）；
- 3  配置门：token env 缺失 / phone 非法 / 服务端 401（token 错）/ 403 /
     503（服务端 GEO_OTP_OPERATOR_TOKEN 未配，功能 fail-closed）。

网络瞬时错误（连接拒/超时）不判死，重试到 ``--timeout`` 为止。

环境卫生（mihomo 代理教训）：本机常有 http_proxy/https_proxy/all_proxy 指向
系统代理，打 127.0.0.1/内网绝不能走代理——启动时把六个大小写代理 env 全部
``os.environ.pop``，且 httpx 一律 ``trust_env=False``。8443 是自签证书 →
``verify=False``（本机回环/内网，token 已在 header 鉴权）。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections.abc import Callable

import httpx

PHONE_RE = re.compile(r"^1[0-9]{10}$")  # 11 位中国大陆手机号（与 api/geo_platform/otp 同口径）

_PROXY_ENV = ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")

_DEFAULT_BASE = "https://127.0.0.1:8443"
_REQUEST_TIMEOUT_S = 10.0


class OtpWaitConfigError(Exception):
    """401/403/503 等重试无意义的鉴权/配置失败（→ exit 3）。"""


def strip_proxy_env() -> None:
    """剥掉大小写六个代理 env——本机 mihomo 系统代理会把 127.0.0.1 请求劫走出网。"""
    for key in _PROXY_ENV:
        os.environ.pop(key, None)


def wait_for_code(
    *,
    timeout_s: float,
    interval_s: float,
    fetch: Callable[[], str | None],
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str | None:
    """每 ``interval_s`` 秒调 ``fetch()``，拿到非空码即返回；``timeout_s`` 内没有 → None。

    ``fetch`` 返回 None = 暂无新鲜码（含瞬时网络错误）；抛 OtpWaitConfigError 直接上抛。
    sleep/clock 可注入（测试免真等）。"""
    deadline = clock() + timeout_s
    while True:
        code = fetch()
        if code:
            return code
        remaining = deadline - clock()
        if remaining <= 0:
            return None
        sleep(min(interval_s, remaining))


def make_fetcher(*, base: str, token: str, phone: str, within: int) -> Callable[[], str | None]:
    """真 fetcher：GET ``<base>/api/v2/otp/latest``（X-Operator-Token 门）。

    200+found → code；200+!found → None；401/403/503 → OtpWaitConfigError；
    其他状态码/网络错误 → 记 stderr 后当 None（重试到超时）。"""
    url = f"{base.rstrip('/')}/api/v2/otp/latest"
    client = httpx.Client(trust_env=False, verify=False, timeout=_REQUEST_TIMEOUT_S)

    def fetch() -> str | None:
        try:
            resp = client.get(
                url, params={"phone": phone, "within": within}, headers={"X-Operator-Token": token}
            )
        except httpx.HTTPError as e:
            print(
                f"[otp-wait] 网络错误（重试到超时）: {type(e).__name__}",
                file=sys.stderr,
                flush=True,
            )
            return None
        if resp.status_code in (401, 403, 503):
            raise OtpWaitConfigError(f"GET {url} → {resp.status_code}（token 错/服务端未配）")
        if resp.status_code != 200:
            print(f"[otp-wait] HTTP {resp.status_code}（按无码重试）", file=sys.stderr, flush=True)
            return None
        try:
            body = resp.json()
        except ValueError:
            print("[otp-wait] 响应非 JSON（按无码重试）", file=sys.stderr, flush=True)
            return None
        if isinstance(body, dict) and body.get("ok") and body.get("found"):
            code = str(body.get("code") or "").strip()
            if code:
                return code
        return None

    return fetch


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="轮询 V2 OTP 取码口直到拿到新鲜验证码（开户自动化 glue）"
    )
    ap.add_argument("--phone", required=True, help="11 位手机号（路由键，1XXXXXXXXXX）")
    ap.add_argument(
        "--timeout", type=float, default=180.0, help="总轮询预算秒数（缺省 180）；超时 exit 2"
    )
    ap.add_argument("--interval", type=float, default=2.0, help="轮询间隔秒（缺省 2）")
    ap.add_argument(
        "--within", type=int, default=0, help="服务端鲜度窗秒（缺省=--timeout，服务端硬夹 1..900）"
    )
    ap.add_argument(
        "--base",
        default=os.environ.get("GEO_OTP_BASE_URL") or _DEFAULT_BASE,
        help=f"V2 API 基址（缺省 ${_DEFAULT_BASE} 或 GEO_OTP_BASE_URL）",
    )
    ap.add_argument(
        "--token-env",
        default="GEO_OTP_OPERATOR_TOKEN",
        help="operator token 的 env 名（缺省 GEO_OTP_OPERATOR_TOKEN）",
    )
    args = ap.parse_args(argv)

    strip_proxy_env()
    token = os.environ.get(args.token_env, "") or ""
    if not token:
        print(
            f"[otp-wait] 配置缺失：env {args.token_env} 未配（operator token）",
            file=sys.stderr,
            flush=True,
        )
        return 3
    phone = (args.phone or "").strip()
    if not PHONE_RE.match(phone):
        print(
            f"[otp-wait] 配置缺失：--phone 须为 11 位手机号（收到 {args.phone!r}）",
            file=sys.stderr,
            flush=True,
        )
        return 3
    within = args.within if args.within > 0 else max(1, min(int(args.timeout), 900))
    fetch = make_fetcher(base=args.base, token=token, phone=phone, within=within)
    print(
        f"[otp-wait] polling {args.base} phone={phone[:3]}***{phone[-4:]} "
        f"timeout={args.timeout}s within={within}s",
        file=sys.stderr,
        flush=True,
    )
    try:
        code = wait_for_code(timeout_s=args.timeout, interval_s=args.interval, fetch=fetch)
    except OtpWaitConfigError as e:
        print(f"[otp-wait] 配置门失败：{type(e).__name__}", file=sys.stderr, flush=True)
        return 3
    except KeyboardInterrupt:
        print("[otp-wait] 已中断（按超时退出码）", file=sys.stderr, flush=True)
        return 2
    if code is None:
        print(f"[otp-wait] 超时 {args.timeout}s 内无新鲜码", file=sys.stderr, flush=True)
        return 2
    print(code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
