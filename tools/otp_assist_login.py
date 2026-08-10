#!/usr/bin/env python3
"""登录/OTP 人工接管会话 CLI（复用 captcha-assist-v1 骨架，2026-08-07 起）。

用途：OTP 自动链路断（SmsForwarder 停推，物理修不了）后，开户/登录需要人工
在手机上收码、并在平台登录页完成交互。本工具 attach 平台常驻浏览器（supervisor
管理生命周期，绝不 launch 新进程、绝不杀浏览器），把页面实时画面（/frame JPEG）
与触摸/键盘输入（/input，键盘走 playwright keyboard——text 落在页面当前焦点，
即登录/OTP 表单）中继到管理员手机浏览器；人工完成登录后运维确认，工具做
best-effort 登录态验证并干净退出。

用法（生产 yiyan 文心一言，15510162660 开户）::

    set -a; . /etc/geo-platform-v2/worker-adapters.env; set +a
    .venv/bin/python tools/otp_assist_login.py --platform yiyan_sh \
        --goto https://yiyan.baidu.com/ --ttl-min 60 --note "155开户"

配置门（fail-fast，exit 3）：

- ``--platform`` 收**常驻实例键**（2026-08-09 起，浏览器矩阵化：``yiyan_sh``
  等；第一段恒为平台 slug，特征/页面逻辑按 slug 反解）。CDP 按实例键解析
  ``GEO_BROWSER_<KEY>_CDP_URL``（未设置回退 ``GEO_<PLATFORM>_CDP_URL``——
  传旧平台 slug 仍可用，行为与升级前一致）。指向 supervisor 常驻浏览器
  CDP 端口。工具独立运行，不经 worker env 文件；运维按上行 source 加载。
- 推送未配齐（``GEO_ASSIST_PUBLIC_BASE`` / ``GEO_ASSIST_NOTIFY_URL``）时必须显式
  ``--no-notify``：只本地打印 ticket/链接；``GEO_ASSIST_PUBLIC_BASE`` 也缺时会
  如实说明拼不出公网链接（ticket 明文照打，运维自行拼接
  ``/api/v2/assist/<ticket>``）。推送 flavor 走 ``GEO_ASSIST_NOTIFY_FLAVOR``
  （bark|serverchan|wecom|ntfy|raw，缺省 raw）。
- 平台互斥锁走 ``browser_lock(platform)``（进程内 + DB fencing；单 worker
  开发/测试可 ``GEO_BROWSER_FENCING=local`` 纯进程内锁）——会话全程持锁，
  防 batch 抢页；锁忙/DB 不可达 fail-closed → exit 1。

done 感知的诚实现状（读 captcha_assist/assist_router 代码确认）：

- workflow 撞码路径的 done = 手机页按钮 → ``POST /api/v2/assist/<ticket>/done``
  → 按注册表 ``run_pub_id`` 查 DB CollectionRun → workflow signal outbox。
  **CLI 会话没有 workflow run，该端点对本工具会话恒 404 run_not_found——
  手机页 done 按钮对 CLI 会话不可用**（assist_router.py 只读不改）。
- 因此本工具的 done 通道：运维在终端按 **Enter**（stdin 非 TTY 时退化纯轮询）；
  同时轮询注册表 ``state``（active/solved/closed，与 captcha_assist 词表一致）
  兜底——外部把 state 标 solved/closed（如未来 router 支持无 run done、或人工
  改文件）都能被正确感知。手机页 frame/input/status 全可用（纯注册表驱动，
  不依赖 DB）。CLI 确认 done 后会镜像 router 的 done 写（state=solved +
  solved_at），手机页随即显示"已解决"。

退出码：0=人工完成；2=TTL 超时；3=配置门；1=attach/bridge/会话异常；130=Ctrl+C。

纪律（继承 captcha_assist）：绝不杀常驻浏览器（退出只断 CDP 连接）；ticket 明文
只出现在 stdout/推送 URL，绝不进日志/注册表文件；不打印任何 env 秘密（推送
URL 含 key，只记 flavor）。patchright ``connect_over_cdp`` 会读 http_proxy env
（本机 mihomo 7890）导致 /json/version 400——本工具启动时进程内 pop 掉全部
proxy env（不动 shell 环境）。
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
import threading
import time
from typing import Any, TextIO, cast

import structlog
from temporalio.exceptions import ApplicationError

from workflows.activities import captcha_assist
from workflows.activities.assist_notify import push_captcha_assist

log = structlog.get_logger()

EXIT_OK = 0
EXIT_ATTACH_FAILED = 1
EXIT_TTL_EXPIRED = 2
EXIT_CONFIG = 3
EXIT_INTERRUPTED = 130

_POLL_INTERVAL_S = 2.0  # 注册表/done 轮询间隔（测试 monkeypatch 加速）
_SESSION_GRACE_S = 60  # 会话自杀比 CLI 等待晚 60s：CLI 先判 TTL，自杀只兜底
_PROXY_ENV_VARS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)
_PLATFORM_LABELS = {
    "doubao": "豆包",
    "deepseek": "DeepSeek",
    "yiyan": "文心一言",
    "tongyi": "通义千问",
    "yuanbao": "腾讯元宝",
}


# ---------------------------------------------------------------------------
# 参数与环境
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="登录/OTP 人工接管会话：attach 常驻浏览器，中继画面/输入到手机，"
        "人工完成登录后干净退出（绝不杀浏览器）。",
        epilog="示例: set -a; . /etc/geo-platform-v2/worker-adapters.env; set +a && "
        ".venv/bin/python tools/otp_assist_login.py --platform yiyan_sh "
        "--goto https://yiyan.baidu.com/ --ttl-min 60 --note 155开户",
    )
    parser.add_argument(
        "--platform",
        required=True,
        help="常驻实例键（如 yiyan_sh；第一段恒为平台 slug，"
        "决定 GEO_BROWSER_<KEY>_CDP_URL 解析与特征/页面逻辑）。"
        "兼容旧用法：直接传平台 slug（如 yiyan）。",
    )
    parser.add_argument(
        "--goto",
        default="",
        help="启动后把当前标签页导航到该 URL（缺省不动页面——运维可提前手动开好登录页）",
    )
    parser.add_argument(
        "--ttl-min",
        type=float,
        default=60.0,
        help="接管会话寿命（分钟，缺省 60；支持小数值，测试/联调用）",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="不推送（GEO_ASSIST_PUBLIC_BASE/NOTIFY_URL 未配齐时必须显式开启）",
    )
    parser.add_argument("--note", default="", help="事项备注（写进推送文案与注册表 business_key）")
    parser.add_argument(
        "--expect-selector",
        default="",
        help="done 后 best-effort 验证：该 CSS 选择器应可见（只报告，不改退出码）",
    )
    parser.add_argument(
        "--expect-url-regex",
        default="",
        help="done 后 best-effort 验证：页面 URL 应匹配该正则（只报告，不改退出码）",
    )
    args = parser.parse_args(argv)
    if args.goto and not args.goto.startswith(("http://", "https://")):
        parser.error("--goto 必须是 http(s) URL")
    return args


_INSTANCE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def _resolve_instance(raw: str) -> tuple[str, str | None]:
    """--platform 实参 → (平台 slug, 实例键|None)。

    实例键 = ``{platform}_{regiontag}``（浏览器矩阵化）：锁/CDP 按实例键，
    特征/页面逻辑按第一段反解的平台 slug。旧用法传纯 slug → instance_key=None
    （锁/CDP 按 slug，行为与升级前一致）。键非法/平台表外 → SystemExit(3)
    （配置门语义，与 argparse parser.error 的 exit 2 区分）。
    """
    key = raw.strip().lower()
    if not _INSTANCE_KEY_RE.fullmatch(key):
        print(f"[配置错误] --platform 不是合法的实例键/平台 slug: {raw!r}", file=sys.stderr)
        raise SystemExit(EXIT_CONFIG)
    platform = key.split("_", 1)[0]
    if platform not in _PLATFORM_LABELS:
        print(f"[配置错误] 未知平台 slug: {platform!r}（实例键 {key!r} 的第一段）", file=sys.stderr)
        raise SystemExit(EXIT_CONFIG)
    return platform, (key if key != platform else None)


class _StderrRelay:
    """写入时动态解析 sys.stderr 的文件对象。

    structlog PrintLogger 构造时缓存 file——直接传 sys.stderr 会把句柄绑死
    （pytest capsys 替换/关闭 stderr 后，缓存句柄再写即 ValueError）；中继对象
    每次写都找当前 stderr，长缓存也安全。
    """

    def write(self, message: str) -> None:
        sys.stderr.write(message)

    def flush(self) -> None:
        sys.stderr.flush()


def _configure_logging() -> None:
    # structlog 落 stderr：stdout 只留给 ticket/链接/操作提示（运维要复制转发）。
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(
            cast("TextIO", _StderrRelay())
        ),  # PrintLogger 只用 write/flush
    )


def _scrub_proxy_env() -> None:
    """进程内 pop 全部 proxy env（值绝不进日志）。

    patchright connect_over_cdp 会读 http_proxy（本机 mihomo 7890）导致
    /json/version 400（生产排障实证）；进程内 pop 对运维友好——不改 shell 环境。
    """
    dropped = [name for name in _PROXY_ENV_VARS if os.environ.pop(name, None) is not None]
    if dropped:
        log.info("otp_assist.proxy_env_scrubbed", count=len(dropped))


# ---------------------------------------------------------------------------
# 注册表（schema 与 captcha_assist_start 产物一字不差，assist_router 可服务）
# ---------------------------------------------------------------------------


def _build_registry_record(
    *,
    platform: str,
    instance_key: str | None,
    run_pub_id: str,
    session_id: str,
    ticket_hash: str,
    port: int,
    note: str,
    ttl_s: int,
    now: int | None = None,
) -> dict[str, Any]:
    """CLI 会话的注册表记录：字段集与 captcha_assist_start 完全一致。

    唯一语义差别是 ``run_pub_id`` 指向 CLI 会话标识而非 DB CollectionRun——
    frame/status/input 端点纯注册表驱动不受影响；done 端点因此 404（见模块
    docstring「done 感知的诚实现状」）。``platform``=平台 slug（特征语义），
    ``instance_key``=实际 attach 的常驻实例键（锁/CDP 口径；None→平台 slug）。
    """
    label = _PLATFORM_LABELS.get(platform, platform)
    created = int(now if now is not None else time.time())
    return {
        "version": 1,
        "run_pub_id": run_pub_id,
        "session_id": session_id,
        "ticket_hash": ticket_hash,
        "port": port,
        "platform": platform,
        "instance_key": instance_key or platform,
        "state": "active",
        "business_key": note or f"登录/OTP 人工接管（{label}）",
        "evidence_ref": None,
        "created_at": created,
        "expires_at": created + ttl_s,
        "push_sent": False,
        "solved_at": None,
    }


def _first_page(browser: Any) -> Any:
    """登录/OTP 接管选页：无撞码概念，直接取常驻浏览器首个标签页。

    多标签时运维用 --goto 强制该标签页到登录页。无页可 attach → assist_no_page
    （与 captcha_assist._pick_page 的口径一致，可重试）。
    """
    contexts = list(getattr(browser, "contexts", None) or [])
    pages = list(getattr(contexts[0], "pages", None) or []) if contexts else []
    if pages:
        return pages[0]
    raise ApplicationError(
        "resident browser has no page to attach (otp_assist_login)",
        type="assist_no_page",
    )


# ---------------------------------------------------------------------------
# done 等待与验证
# ---------------------------------------------------------------------------


def _stdin_watch(done_evt: threading.Event) -> None:
    try:
        line = sys.stdin.readline()
    except Exception:  # noqa: BLE001 — stdin 异常 = 没有输入通道，安静退出
        return
    if line:  # EOF 返回 "" 不算；任何真实输入（含裸 Enter 的 "\n"）算 done
        done_evt.set()


def _start_done_watcher(done_evt: threading.Event) -> threading.Thread | None:
    """stdin Enter → done。非 TTY（nohup/CI/测试）不启用——纯注册表轮询。"""
    if not sys.stdin.isatty():
        return None
    thread = threading.Thread(
        target=_stdin_watch, args=(done_evt,), daemon=True, name="otp-assist-stdin"
    )
    thread.start()
    return thread


def _wait_for_done(
    sess: captcha_assist.AssistSession,
    ticket_hash: str,
    *,
    done_evt: threading.Event,
    deadline: float,
) -> str:
    """返回 done | ttl | registry_lost | session_lost。"""
    while True:
        if done_evt.is_set():
            return "done"
        rec = captcha_assist._read_registry(ticket_hash)
        if rec is None:
            return "registry_lost"  # 自己写的文件没了 = 外部干预，如实上报
        state = rec.get("state")
        if state == "solved":
            return "done"  # 外部标 solved（captcha_assist 词表）
        if state == "closed" or not sess.alive:
            return "session_lost"  # 会话线程死了（含崩溃/自杀抢跑）
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "ttl"
        done_evt.wait(min(_POLL_INTERVAL_S, remaining))


def _read_page_state(sess: captcha_assist.AssistSession) -> tuple[str, str] | None:
    try:
        url = str(sess.run_on_page(lambda page: page.url))
        title = str(sess.run_on_page(lambda page: page.title()))
    except Exception as exc:  # noqa: BLE001 — best-effort，失败如实报告
        log.warning("otp_assist.page_state_unreadable", error=str(exc))
        return None
    return url, title


def _report_verification(
    sess: captcha_assist.AssistSession, *, expect_selector: str, expect_url_regex: str
) -> None:
    """done 后 best-effort 登录态验证：只如实报告，不改变退出码（人工已判断）。"""
    state = _read_page_state(sess)
    if state is None:
        print("[警告] 无法读取页面状态——请在桌面浏览器人工核对登录态")
        return
    url, title = state
    print(f"当前页面: {title!r} | {url}")
    if expect_url_regex:
        ok = re.search(expect_url_regex, url) is not None
        print(f"登录态验证[url ~ /{expect_url_regex}/]: {'PASS' if ok else 'FAIL——请人工核对页面'}")
    if expect_selector:
        try:
            visible = bool(
                sess.run_on_page(
                    lambda page: page.locator(expect_selector).first.is_visible(timeout=3000)
                )
            )
        except Exception:  # noqa: BLE001 — 探测异常按不可见如实报 FAIL
            visible = False
        print(
            f"登录态验证[selector {expect_selector!r}]: "
            f"{'PASS' if visible else 'FAIL——请人工核对页面'}"
        )
    if not expect_url_regex and not expect_selector:
        print("（未配置 --expect-*，跳过自动验证——请按上面的 URL/title 人工判断登录态）")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _configure_logging()
    _scrub_proxy_env()
    platform, instance_key = _resolve_instance(args.platform)
    lock_key = instance_key or platform
    label = _PLATFORM_LABELS.get(platform, platform)
    if instance_key:
        label = f"{label}（{instance_key}）"

    # ── 配置门 1：常驻浏览器 CDP（实例键优先 GEO_BROWSER_<KEY>_CDP_URL） ──
    try:
        cdp_url = captcha_assist.resident_cdp_url(lock_key)
    except ValueError as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        return EXIT_CONFIG
    if not cdp_url:
        print(
            f"[配置错误] GEO_BROWSER_{lock_key.upper()}_CDP_URL / "
            f"GEO_{lock_key.upper()}_CDP_URL 均未配置——"
            f"工具独立运行，请先加载 worker env：\n"
            f"  set -a; . /etc/geo-platform-v2/worker-adapters.env; set +a",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    # ── 配置门 2：推送（未配齐必须显式 --no-notify） ──
    public_base = os.environ.get("GEO_ASSIST_PUBLIC_BASE", "").strip()
    notify_url = os.environ.get("GEO_ASSIST_NOTIFY_URL", "").strip()
    flavor = os.environ.get("GEO_ASSIST_NOTIFY_FLAVOR", "raw").strip() or "raw"
    if not args.no_notify and (not public_base or not notify_url):
        print(
            "[配置错误] GEO_ASSIST_PUBLIC_BASE / GEO_ASSIST_NOTIFY_URL 未配齐，"
            "无法推送接管链接。配齐后重跑，或显式 --no-notify（仅本地打印链接）。",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    ttl_s = max(1, int(args.ttl_min * 60))
    ticket = secrets.token_urlsafe(32)
    session_id = secrets.token_urlsafe(24)
    ticket_hash = captcha_assist._ticket_hash(ticket)
    run_pub_id = f"otp-assist-{lock_key}-{int(time.time())}"  # CLI 会话，无 workflow run
    assist_url = f"{public_base.rstrip('/')}/api/v2/assist/{ticket}" if public_base else ""

    # ── attach 常驻浏览器 + 起 bridge（全程持实例锁；绝不 launch/杀浏览器） ──
    sess = captcha_assist.AssistSession(
        platform=platform,
        run_pub_id=run_pub_id,
        session_id=session_id,
        ticket_hash=ticket_hash,
        max_lifetime_s=ttl_s + _SESSION_GRACE_S,
        instance_key=instance_key,
        page_picker=_first_page,
        cleared_check=None,
    )
    try:
        port = sess.start()
    except KeyboardInterrupt:
        # start 窗口内 Ctrl+C：线程可能已 attach/持锁，必须显式 stop 清理
        # （DB fence 租约否则要等 TTL 兜底回收）。
        sess.stop()
        print("\n已中断（Ctrl+C）——会话清理中。", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as exc:  # noqa: BLE001 — 无 CDP/无页/锁忙，全部如实上报
        print(f"[接管失败] 无法 attach {label} 常驻浏览器：{exc}", file=sys.stderr)
        log.warning("otp_assist.start_failed", platform=platform, error=str(exc))
        return EXIT_ATTACH_FAILED

    try:
        # ── 注册表先于推送/打印：手机页能服务的前提 ──
        record = _build_registry_record(
            platform=platform,
            instance_key=instance_key,
            run_pub_id=run_pub_id,
            session_id=session_id,
            ticket_hash=ticket_hash,
            port=int(port),
            note=args.note.strip(),
            ttl_s=ttl_s,
        )
        captcha_assist._write_registry(record)

        state = _read_page_state(sess)
        if state is not None:
            print(f"已接管页面: {state[1]!r} | {state[0]}")

        if args.goto:
            try:
                sess.run_on_page(lambda page: page.goto(args.goto, wait_until="domcontentloaded"))
                print(f"已导航到: {args.goto}")
            except Exception as exc:  # noqa: BLE001 — headed 浏览器，运维可桌面手动导航
                log.warning("otp_assist.goto_failed", error=str(exc))
                print(
                    f"[警告] 导航 {args.goto} 失败：{exc}（页面保持原状，可在桌面手动打开登录页）"
                )

        print("=" * 72)
        print(f"登录/OTP 人工接管会话已就绪（平台: {label}，{ttl_s // 60} 分钟内有效）")
        if assist_url:
            print(f"接管链接: {assist_url}")
        else:
            print(
                "GEO_ASSIST_PUBLIC_BASE 未配置——拼不出公网链接；"
                "请自行拼接 /api/v2/assist/<ticket>："
            )
        print(f"ticket: {ticket}")  # 明文只进 stdout/推送，绝不进日志
        print("完成登录后回到本终端按 Enter 结束。")
        print(
            "注意：手机页「我已完成」按钮对 CLI 会话不可用（无 workflow run，"
            "done 端点 404）——人工完成后须由运维在本终端确认。"
        )
        print("=" * 72)

        # ── 推送（失败不废会话：链接已在 stdout，运维可手动转发） ──
        if not args.no_notify:
            title = f"[GEO] {label}登录/OTP 人工接管"
            body = (
                f"平台: {label}\n"
                f"事项: {args.note.strip() or '开户/登录'}\n"
                f"有效期: {round(ttl_s / 60)} 分钟\n"
                f"接管链接: {assist_url}"
            )
            pushed = push_captcha_assist(flavor=flavor, url=notify_url, title=title, body=body)
            if pushed:
                captcha_assist._patch_registry(ticket_hash, push_sent=True)
                print(f"推送已发出（flavor={flavor}）")
            else:
                log.warning("otp_assist.push_failed", platform=platform, flavor=flavor)
                print("[警告] 推送失败——请手动转发上面的接管链接")

        log.info(
            "otp_assist.session_started",
            platform=platform,
            session_id=session_id,
            port=port,
            pushed=not args.no_notify,
        )

        # ── 等待人工 ──
        done_evt = threading.Event()
        _start_done_watcher(done_evt)
        outcome = _wait_for_done(
            sess, ticket_hash, done_evt=done_evt, deadline=time.monotonic() + ttl_s
        )

        if outcome == "done":
            # 镜像 router 的 done 写（assist_router.py /done）：手机页轮询 status
            # 看到 solved → 显示"已解决"，给手机端人工即时反馈。
            rec = captcha_assist._read_registry(ticket_hash)
            if rec is not None and rec.get("state") != "solved":
                captcha_assist._patch_registry(
                    ticket_hash, state="solved", solved_at=int(time.time())
                )
            _report_verification(
                sess, expect_selector=args.expect_selector, expect_url_regex=args.expect_url_regex
            )
            print("人工已确认完成，会话清理中（常驻浏览器保持运行）。")
            log.info("otp_assist.done", platform=platform, session_id=session_id)
            return EXIT_OK

        if outcome == "ttl":
            print(f"TTL {args.ttl_min:g} 分钟到期，人工未完成——会话已清理。", file=sys.stderr)
            log.warning("otp_assist.ttl_expired", platform=platform, session_id=session_id)
            return EXIT_TTL_EXPIRED

        print(f"会话异常终止（{outcome}）：{sess.error or '无详情'}", file=sys.stderr)
        log.warning(
            "otp_assist.session_lost",
            platform=platform,
            session_id=session_id,
            outcome=outcome,
            error=str(sess.error) if sess.error else None,
        )
        return EXIT_ATTACH_FAILED
    except KeyboardInterrupt:
        print("\n已中断（Ctrl+C）——会话清理中。", file=sys.stderr)
        return EXIT_INTERRUPTED
    finally:
        # 线程 finally：bridge.stop → browser.close(仅断 CDP) → pw.stop → 放锁 → 注册表 closed
        sess.stop()


if __name__ == "__main__":
    sys.exit(main())
