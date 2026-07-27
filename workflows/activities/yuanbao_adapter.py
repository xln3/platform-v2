"""腾讯元宝网页采集适配器 v1（yuanbao.tencent.com，ADR-0003 五平台上线）。

结构对照 ``doubao_adapter.py``（已 live 验证的模板）；元宝平台知识移植自旧链
``server/proxyllm/engines/yuanbao.py``（recon 实测 + GUESS 标记，参考不盲信）：

- CONFIRMED（旧链 recon 实测）：聊天 URL、Next.js 重 hydration（≥10s settle）、
  匿名可渲染输入框、发送后弹登录模态（微信扫码/手机号/QQ）、Quill
  ``div.ql-editor[contenteditable]`` 输入框（contenteditable → 只能 keyboard.type，
  不能 fill）、``a/button[class*='send']`` 发送按钮、POST ``/api/chat/`` 流式回答。
- GUESS（login-gated 未实测）：助手气泡/引用卡片 DOM 选择器——仅作 DOM 兜底，
  命中与否如实记日志，绝不臆造正文。

v1 边界（与豆包适配器对齐）：

- 仅 ``mode='normal'``；``deep_think`` → ``unsupported_mode`` non_retryable。
- 配置全走 env（秘密绝不进 task payload）：``GEO_YUANBAO_PROFILE_DIR``（必填，
  persistent profile 目录，缺失/不存在 → ``adapter_not_configured`` non_retryable）；
  ``GEO_YUANBAO_PROXY_URL``（可选，日志只落打码后的 scheme://host:port）；
  ``GEO_ADAPTER_EVIDENCE_DIR``（证据目录，五平台共享 env，默认
  ``platform-v2/runtime/adapter-evidence/yuanbao/``）；``GEO_YUANBAO_HEADLESS``
  （默认 1；0=headed 需 DISPLAY）。
- 执行模型：sync 浏览器驱动包在 ``asyncio.to_thread`` 里跑；activity 协程侧每 10s
  泵一次 heartbeat。浏览器驱动首选 patchright（反检测补丁版），vanilla playwright
  仅作开发兜底。
- 墙分类（先截屏存证再抛，错误 message 带证据路径、绝不含秘密）：
  登录墙（含匿名发送后弹出的登录模态）→ ``wall_login_required`` non_retryable；
  验证码 → ``wall_captcha`` non_retryable；发送被吞/限流 → ``wall_send``
  non_retryable（重试只是再撞）。
- 成功判据（零合成）：``/api/chat/`` 流真正 loadingFinished 且 DOM 抽取到非空正文
  且无墙特征——缺一都不得返回成功。流未出现/截断/空答案 →
  ``answer_capture_incomplete``（可重试的诚实失败）。
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import structlog
from temporalio.exceptions import ApplicationError

from domain.evidence.dlp import assert_secret_free
from workflows.activities.collection import CollectionTaskInput, CollectionTaskResult

log = structlog.get_logger()

ENV_PROFILE_DIR = "GEO_YUANBAO_PROFILE_DIR"
ENV_PROXY_URL = "GEO_YUANBAO_PROXY_URL"
ENV_EVIDENCE_DIR = "GEO_ADAPTER_EVIDENCE_DIR"
ENV_HEADLESS = "GEO_YUANBAO_HEADLESS"

_DEFAULT_EVIDENCE_DIR = (
    Path(__file__).resolve().parents[2] / "runtime" / "adapter-evidence" / "yuanbao"
)
_HEARTBEAT_INTERVAL_S = 10.0  # workflow heartbeat_timeout=30s，泵频 ≤15s 硬约束
_NAV_TIMEOUT_MS = 25_000
_CHAT_TIMEOUT_S = 120.0  # normal 模式流式完成预算（workflow 总预算 5 分钟）
_HYDRATION_SETTLE_MS = 11_000  # 旧链实测：Next.js 重 hydration，必须 ≥10s

_CHAT_URL = "https://yuanbao.tencent.com/chat"
_HOME_URL = "https://yuanbao.tencent.com"

# 旧链 yuanbao.py 同款 UA / locale / 时区
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 聊天输入框（CONFIRMED：Quill editor，contenteditable，全页唯一可见）
_INPUT_SELECTORS: tuple[str, ...] = (
    "div.ql-editor[contenteditable='true']",
    "div[contenteditable='true']",
)

# 发送按钮（CONFIRMED：a/button class 含 send / send-btn；Enter 亦可提交，作键盘兜底）
_SEND_SELECTORS: tuple[str, ...] = (
    "a[class*='send-btn']",
    "button[class*='send-btn']",
    "a[class*='send']",
    "button[class*='send']",
    "[class*='send-btn']",
)

# 登录模态（CONFIRMED：匿名发送后弹出，微信扫码/手机号/QQ；dialog 文案匹配抗 class 哈希轮换）
_LOGIN_WALL_HINTS: tuple[str, ...] = (
    "div[role='dialog']:has-text('微信登录')",
    "div[role='dialog']:has-text('手机号登录')",
    "div[role='dialog']:has-text('扫码登录')",
    "div[role='dialog']:has-text('登录后')",
    "div[class*='login-modal']:visible",
    "div[class*='LoginModal']:visible",
    "iframe[src*='login']",
    "iframe[src*='passport']",
)

# 验证码组件（旧链 login_state.CAPTCHA_SELECTORS 权威词表，通用）
_CAPTCHA_SELECTORS: tuple[str, ...] = (
    'iframe[src*="captcha"]',
    'iframe[src*="verify"]',
    'div[class*="captcha"]:visible',
    'div[id*="verify"]:visible',
    'div[class*="verify-wrap"]:visible',
)

# 助手回答气泡（GUESS：login-gated 未实测；流结束后取最后一个可见气泡）
_ASSISTANT_SELECTORS: tuple[str, ...] = (
    "div[class*='agent-chat__bubble--ai']",
    "div[class*='hyc-content']",
    "div[class*='hyc-common-markdown']",
    "div[class*='bubble'][class*='ai']",
    "div[class*='answer'] .markdown-body",
    ".markdown-body",
    "div[class*='message'][class*='assistant']",
)

# 引用卡片（GUESS：login-gated 未实测；只收真实 http(s) href，绝不臆造，按 URL 去重）
_REFERENCE_SELECTORS: tuple[str, ...] = (
    "div[class*='hyc-card-box'] a[href]",
    "div[class*='references'] a[href]",
    "div[class*='reference'] a[href]",
    "div[class*='search-result'] a[href]",
    "a[class*='ref-link'][href]",
    "div[class*='source'] a[href]",
)

# DOM 层系统通知词表（softban 过频提示 / 实名墙）——命中判定 gated by not has_answer
_SOFTBAN_DOM_PHRASES: tuple[str, ...] = (
    "今日请求过频",
    "请求过于频繁",
    "请求太频繁",
    "操作过于频繁",
    "操作太频繁",
    "发送频率过高",
    "请求频率过高",
    "今日对话次数已达",
    "当前请求人数过多",
)
_REALNAME_DOM_PHRASES: tuple[str, ...] = (
    "完成实名认证",
    "请先实名",
    "实名认证后才能",
    "未实名认证",
    "进行实名认证",
    "实名验证",
)

# DOM 兜底抽取后裁剪尾部 UI 噪声（建议 chips / 工具栏 / 输入区占位）
_TRAILING_NOISE_MARKERS: tuple[str, ...] = (
    "继续追问",
    "你可能想问",
    "换一批",
    "深度思考",
    "联网搜索",
)

# contenteditable 输入框取值（Quill：textContent；value 不存在）
_INPUT_VALUE_JS = (
    "el => (el.value !== undefined && el.value !== null && el.value !== '') "
    "? el.value : (el.textContent || '')"
)

# 整页截图前把内部 overflow 滚动容器压平进文档流（与豆包适配器同款 flatten）
_FLATTEN_FOR_SCREENSHOT_JS = r"""
() => {
  const beforeBodyClientH = document.body ? document.body.clientHeight : 0;
  const beforeBodyScrollH = document.body ? document.body.scrollHeight : 0;
  const cands = [];
  for (const el of document.querySelectorAll('div, main, section, article, aside, nav, form')) {
    const cs = getComputedStyle(el);
    if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll')
        && el.scrollHeight > el.clientHeight + 100) {
      cands.push(el);
    }
  }
  let main = null;
  let fullHeight = 0;
  if (cands.length) {
    cands.sort((a, b) => b.scrollHeight - a.scrollHeight);
    main = cands[0];
    fullHeight = main.scrollHeight;
    let cur = main;
    while (cur) {
      if (cur === main) {
        cur.style.setProperty('height', fullHeight + 'px', 'important');
      } else {
        cur.style.setProperty('height', 'auto', 'important');
      }
      cur.style.setProperty('max-height', 'none', 'important');
      cur.style.setProperty('min-height', '0', 'important');
      cur.style.setProperty('overflow', 'visible', 'important');
      cur.style.setProperty('flex', '0 0 auto', 'important');
      cur.style.setProperty('position', 'static', 'important');
      cur.style.setProperty('transform', 'none', 'important');
      cur.style.setProperty('contain', 'none', 'important');
      if (cur === document.documentElement) break;
      cur = cur.parentElement;
    }
  }
  for (const el of document.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    if (cs.transform && cs.transform !== 'none') {
      el.style.setProperty('transform', 'none', 'important');
    }
    if (cs.position === 'fixed') {
      el.style.setProperty('position', 'absolute', 'important');
    }
  }
  const targetH = Math.max(fullHeight, beforeBodyScrollH, beforeBodyClientH);
  document.body.style.setProperty('height', 'auto', 'important');
  document.body.style.setProperty('min-height', targetH + 'px', 'important');
  document.body.style.setProperty('overflow', 'visible', 'important');
  document.body.style.setProperty('transform', 'none', 'important');
  document.documentElement.style.setProperty('height', 'auto', 'important');
  document.documentElement.style.setProperty('min-height', targetH + 'px', 'important');
  document.documentElement.style.setProperty('overflow', 'visible', 'important');
  document.documentElement.style.setProperty('transform', 'none', 'important');
  void document.body.offsetHeight;
  const afterBodyScrollH = document.body ? document.body.scrollHeight : 0;
  const afterDocScrollH = document.documentElement ? document.documentElement.scrollHeight : 0;
  return {
    ok: !!main,
    scroller_full_height: fullHeight,
    body_scroll_height_after: afterBodyScrollH,
    doc_scroll_height_after: afterDocScrollH,
    viewport_height: window.innerHeight,
  };
}
"""


# ---------------------------------------------------------------------------
# 配置 / 错误类型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class YuanbaoAdapterConfig:
    """env 配置。proxy_url 原文只在启动浏览器时使用，绝不落日志/payload。"""

    profile_dir: Path
    proxy_url: str | None
    evidence_dir: Path
    headless: bool

    @classmethod
    def from_env(cls) -> YuanbaoAdapterConfig:
        raw_profile = os.environ.get(ENV_PROFILE_DIR, "").strip()
        if not raw_profile:
            raise ApplicationError(
                f"{ENV_PROFILE_DIR} is not set — yuanbao adapter requires a persistent "
                "browser profile directory",
                type="adapter_not_configured",
                non_retryable=True,
            )
        profile_dir = Path(raw_profile)
        if not profile_dir.is_dir():
            raise ApplicationError(
                f"{ENV_PROFILE_DIR} is not an existing directory: {profile_dir}",
                type="adapter_not_configured",
                non_retryable=True,
            )
        proxy_url = os.environ.get(ENV_PROXY_URL, "").strip() or None
        if proxy_url is not None and _parse_proxy(proxy_url) is None:
            raise ApplicationError(
                f"{ENV_PROXY_URL} is not a valid proxy URL (expected scheme://[user:pass@]host:port)",
                type="adapter_not_configured",
                non_retryable=True,
            )
        raw_evidence = os.environ.get(ENV_EVIDENCE_DIR, "").strip()
        evidence_dir = Path(raw_evidence) if raw_evidence else _DEFAULT_EVIDENCE_DIR
        headless = os.environ.get(ENV_HEADLESS, "1").strip() != "0"
        return cls(
            profile_dir=profile_dir,
            proxy_url=proxy_url,
            evidence_dir=evidence_dir,
            headless=headless,
        )


_PROXY_RE = re.compile(
    r"^(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*)://"
    r"(?:(?P<user>[^:@/]+)(?::(?P<password>[^@/]*))?@)?"
    r"(?P<host>[^/@]+)$"
)


def _parse_proxy(proxy_url: str) -> dict[str, str] | None:
    """把 env 代理 URL 拆成 Playwright proxy dict；不匹配返回 None。"""
    m = _PROXY_RE.match(proxy_url.strip())
    if not m:
        return None
    out: dict[str, str] = {"server": f"{m.group('scheme')}://{m.group('host')}"}
    if m.group("user"):
        out["username"] = m.group("user")
        out["password"] = m.group("password") or ""
    return out


def mask_proxy_url(proxy_url: str | None) -> str | None:
    """日志打码：只保留 scheme://host:port，绝不落 user:pass。"""
    if not proxy_url:
        return None
    m = _PROXY_RE.match(proxy_url.strip())
    if not m:
        return "<invalid-proxy-url>"
    return f"{m.group('scheme')}://{m.group('host')}"


class _WallError(RuntimeError):
    """已识别的平台墙（non_retryable）。evidence_path 指向截屏存证，绝不含秘密。"""

    def __init__(self, wall_type: str, message: str, evidence_path: Path | None = None) -> None:
        super().__init__(message)
        self.wall_type = wall_type
        self.evidence_path = evidence_path


class _IncompleteCapture(RuntimeError):
    """采集未完成的诚实失败（可重试）：流截断 / 空答案 / 无流等。"""

    def __init__(self, message: str, evidence_path: Path | None = None) -> None:
        super().__init__(message)
        self.evidence_path = evidence_path


@dataclass
class CollectedAnswer:
    answer_text: str
    references: list[dict[str, Any]]
    screenshot_path: Path
    meta: dict[str, Any] = field(default_factory=dict)


class _BrowserSession(Protocol):
    """Playwright 交互隔离面：测试注入 fake，绝不启动真浏览器。"""

    def collect(self, query: str, on_stage: Callable[[str], None]) -> CollectedAnswer: ...


SessionFactory = Callable[[YuanbaoAdapterConfig, Path, str], _BrowserSession]


def _noop_heartbeat(payload: dict[str, Any]) -> None:
    """activity 上下文之外（测试/冒烟脚本）的默认 heartbeat：什么都不做。"""


# ---------------------------------------------------------------------------
# activity 入口与异步泵
# ---------------------------------------------------------------------------


async def run_yuanbao_collection(
    item: CollectionTaskInput,
    *,
    session_factory: SessionFactory | None = None,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
    attempt: int = 1,
) -> CollectionTaskResult:
    """activity 核心：配置门 → mode 门 → to_thread 跑浏览器 → 墙/结果映射。

    与 activity 上下文解耦（session/heartbeat/attempt 注入），测试全程 mock 浏览器层。
    注册层（workers/main.py）按平台门控调用本函数并注入 activity.heartbeat。
    """
    if item.mode != "normal":
        raise ApplicationError(
            "deep_think not enabled in adapter v1",
            type="unsupported_mode",
            non_retryable=True,
        )
    factory = session_factory or _PlaywrightYuanbaoSession
    beat = heartbeat or _noop_heartbeat
    config = YuanbaoAdapterConfig.from_env()
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    file_stem = f"{_safe_stem(item.business_key)}-a{attempt}"
    bound = log.bind(
        business_key=item.business_key,
        attempt=attempt,
        proxy=mask_proxy_url(config.proxy_url),
    )
    progress = {"stage": "browser_launch"}

    def _blocking() -> CollectedAnswer:
        session = factory(config, config.evidence_dir, file_stem)
        return session.collect(item.query, on_stage=lambda s: progress.__setitem__("stage", s))

    thread = asyncio.ensure_future(asyncio.to_thread(_blocking))
    while True:
        beat({"business_key": item.business_key, "stage": progress["stage"]})
        done, _pending = await asyncio.wait({thread}, timeout=_HEARTBEAT_INTERVAL_S)
        if done:
            break
    try:
        collected = thread.result()
    except _WallError as wall:
        evidence = f"; evidence={wall.evidence_path}" if wall.evidence_path else ""
        await bound.ainfo("yuanbao_wall", wall_type=wall.wall_type, stage=progress["stage"])
        raise ApplicationError(
            f"{wall}{evidence}", type=wall.wall_type, non_retryable=True
        ) from wall
    except _IncompleteCapture as inc:
        evidence = f"; evidence={inc.evidence_path}" if inc.evidence_path else ""
        await bound.ainfo("yuanbao_capture_incomplete", reason=str(inc), stage=progress["stage"])
        raise ApplicationError(f"{inc}{evidence}", type="answer_capture_incomplete") from inc
    await bound.ainfo(
        "yuanbao_collect_ok",
        answer_len=len(collected.answer_text),
        references=len(collected.references),
        stage=progress["stage"],
    )
    answer_text = _compose_answer_text(collected.answer_text, collected.references)
    screenshot_ref = f"file://{collected.screenshot_path}"
    # 出界前 DLP 自检：persist 层对两字段 assert_secret_free，这里提前到同语义 fail-closed
    try:
        assert_secret_free(answer_text)
        assert_secret_free(screenshot_ref)
    except ValueError as error:
        raise ApplicationError(
            "collection result rejected by DLP",
            type="collection_result_dlp_rejected",
            non_retryable=True,
        ) from error
    return CollectionTaskResult(
        business_key=item.business_key,
        answer_text=answer_text,
        screenshot_ref=screenshot_ref,
        quality_state="live_valid",
    )


_SAFE_STEM_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_stem(business_key: str) -> str:
    """business_key 安全化成文件名片段（路径里绝不可含账号/口令等敏感字符）。"""
    stem = _SAFE_STEM_RE.sub("-", business_key).strip("-.")
    return (stem or "task")[:80]


def _compose_answer_text(answer_text: str, references: list[dict[str, Any]]) -> str:
    """正文 + 参考来源追加段（沿用旧链 render_transcript 的参考资料口径）。"""
    text = answer_text.strip()
    if not references:
        return text
    lines = [f"{text}", "", "参考来源："]
    for i, ref in enumerate(references, 1):
        title = str(ref.get("title") or "(无标题)").strip()
        site = str(ref.get("sitename") or "").strip()
        head = f"{i}. {title}" + (f" — {site}" if site else "")
        lines.append(head)
        url = str(ref.get("url") or "").strip()
        if url:
            lines.append(f"   {url}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Playwright 实现（sync，全部跑在 to_thread 线程里）
# ---------------------------------------------------------------------------


class _PlaywrightYuanbaoSession:
    """元宝网页采集的 sync Playwright 实现（persistent context，每次全新、结束即关）。"""

    def __init__(self, config: YuanbaoAdapterConfig, evidence_dir: Path, file_stem: str) -> None:
        self._config = config
        self._evidence_dir = evidence_dir
        self._file_stem = file_stem

    def collect(self, query: str, on_stage: Callable[[str], None]) -> CollectedAnswer:
        # 延迟导入：模块加载不硬依赖浏览器驱动。驱动首选 patchright（旧链生产同款
        # 反检测补丁版）；vanilla playwright 仅作开发兜底。
        driver = "patchright"
        try:
            from patchright.sync_api import TimeoutError as PWTimeout
            from patchright.sync_api import sync_playwright
        except ImportError:
            driver = "playwright"
            from playwright.sync_api import TimeoutError as PWTimeout
            from playwright.sync_api import sync_playwright

        on_stage("browser_launch")
        with sync_playwright() as pw:
            try:
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=str(self._config.profile_dir),
                    headless=self._config.headless,
                    proxy=_parse_proxy(self._config.proxy_url) if self._config.proxy_url else None,
                    args=["--lang=zh-CN"],
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                    extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5"},
                    user_agent=_USER_AGENT,
                )
            except Exception as exc:
                raise _IncompleteCapture(
                    f"browser-launch-failed({driver}): {type(exc).__name__}: {exc}"
                ) from exc
            try:
                context.set_default_timeout(_NAV_TIMEOUT_MS)
                page = context.pages[0] if context.pages else context.new_page()
                capture = _ChatStreamCapture(context, page)

                on_stage("navigate")
                try:
                    page.goto(_CHAT_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
                except PWTimeout:
                    page.goto(_HOME_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
                page.wait_for_timeout(_HYDRATION_SETTLE_MS)  # Next.js 重 hydration（实测 ≥10s）
                _try_close_overlays(page)
                if _detect_login_wall(page):
                    raise _WallError(
                        "wall_login_required",
                        "yuanbao login wall detected right after navigation",
                        self._shot(page, "login"),
                    )

                on_stage("await_input")
                input_loc = _wait_for_input(page, timeout_ms=15_000)
                if input_loc is None:
                    hit = _captcha_hit(page)
                    if hit:
                        raise _WallError(
                            "wall_captcha",
                            f"captcha widget visible before input ({hit})",
                            self._shot(page, "captcha"),
                        )
                    if _detect_login_wall(page):
                        raise _WallError(
                            "wall_login_required",
                            "login wall surfaced while awaiting chat input",
                            self._shot(page, "login"),
                        )
                    raise _IncompleteCapture(
                        "could-not-find-chat-input",
                        self._shot(page, "no_input"),
                    )

                on_stage("typing")
                try:
                    input_loc.click(timeout=8_000)
                except Exception:
                    try:
                        bb = input_loc.bounding_box()
                        if bb:
                            page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
                    except Exception:
                        input_loc.click()  # last resort：抛出去即诚实失败
                page.wait_for_timeout(200)
                # contenteditable（Quill）→ 只能 keyboard.type，不能 fill
                page.keyboard.type(query, delay=18)
                page.wait_for_timeout(800)

                submit = _submit_and_confirm(page, input_loc)
                if not submit.get("submitted"):
                    # 匿名发送会弹登录模态而非清空输入框——先查墙再判发送失败
                    if _detect_login_wall(page):
                        raise _WallError(
                            "wall_login_required",
                            "login modal popped after send (anonymous session)",
                            self._shot(page, "login"),
                        )
                    raise _WallError(
                        "wall_send",
                        "send-not-accepted: composer still populated after "
                        f"{submit.get('attempts', '?')} send attempts (submission swallowed)",
                        self._shot(page, "send_wall"),
                    )
                on_stage("submitted")

                # 登录态下发送后仍可能弹墙（session 过期/风控）：短窗检测
                page.wait_for_timeout(2_500)
                if _detect_login_wall(page):
                    raise _WallError(
                        "wall_login_required",
                        "login modal surfaced post-send",
                        self._shot(page, "login"),
                    )

                # 异步验证码窗口：轮询至多 12s；流已开始且过 3.5s settle 窗即快走
                challenge_start = time.monotonic()
                while time.monotonic() < challenge_start + 12.0:
                    hit = _captcha_hit(page)
                    if hit:
                        raise _WallError(
                            "wall_captcha",
                            f"captcha challenge appeared post-send ({hit})",
                            self._shot(page, "captcha"),
                        )
                    if capture.has_stream_started() and time.monotonic() - challenge_start >= 3.5:
                        break
                    page.wait_for_timeout(500)

                on_stage("await_stream")
                meta = capture.wait_finish(
                    page, appearance_timeout_s=20.0, timeout_s=_CHAT_TIMEOUT_S
                )
                # 元宝答案正文以渲染 DOM 为准（旧链 confirmed 路径）：等气泡文本静默
                answer_text = _wait_answer_stable(page, max_seconds=30.0, quiet_seconds=2.5)
                references = _references_from_dom(page)
                on_stage("answer_extracted")

                if not answer_text:
                    notices = _scan_dom_notices(page)
                    if notices["softban"]:
                        raise _WallError(
                            "wall_send",
                            "rate-limit notice in DOM: " + ",".join(notices["softban"]),
                            self._shot(page, "send_wall"),
                        )
                    if notices["realname"]:
                        raise _WallError(
                            "wall_login_required",
                            "realname wall notice in DOM: " + ",".join(notices["realname"]),
                            self._shot(page, "realname"),
                        )
                if not meta.get("found"):
                    raise _IncompleteCapture(
                        "send-accepted-no-stream: composer cleared (submission accepted) "
                        "but no /api/chat/ stream fired within timeout — likely "
                        "content-filter or silent server-side drop",
                        self._shot(page, "no_stream"),
                    )
                if not meta.get("finished"):
                    raise _IncompleteCapture(
                        "stream-open-at-timeout: /api/chat/ stream still open after "
                        f"budget ({meta.get('bytes_received', 0)} bytes captured) — answer "
                        "would be truncated; failing honestly",
                        self._shot(page, "truncated"),
                    )
                if not answer_text:
                    raise _IncompleteCapture(
                        "answer-empty-after-finished-stream: DOM extraction produced no "
                        "answer text after stream finished",
                        self._shot(page, "empty_answer"),
                    )

                on_stage("screenshot")
                shot_path = self._evidence_dir / f"{self._file_stem}.png"
                _capture_full_page(page, shot_path)
                if not shot_path.exists():
                    raise _IncompleteCapture("evidence-screenshot-failed: no file written")
                return CollectedAnswer(
                    answer_text=answer_text,
                    references=references,
                    screenshot_path=shot_path,
                    meta={
                        "stream": meta,
                        "driver": driver,
                    },
                )
            finally:
                try:
                    context.close()
                except Exception:
                    pass

    def _shot(self, page: Any, suffix: str) -> Path | None:
        """墙/失败存证截图（viewport 即可）。best-effort，失败返回 None。"""
        path = self._evidence_dir / f"{self._file_stem}-{suffix}.png"
        try:
            page.screenshot(path=str(path))
            return path
        except Exception:
            return None


class _ChatStreamCapture:
    """CDP Network 层捕获 POST /api/chat/ 事件流（元宝流式回答的完成度 ground-truth）。

    loadingFinished 同步拉 getResponseBody（Chromium 只短暂保留缓冲）；正文仍以
    渲染 DOM 为准，body 只用于完成度/字节数审计。
    """

    def __init__(self, context: Any, page: Any) -> None:
        self._cdp = context.new_cdp_session(page)
        self._cdp.send("Network.enable")
        self._url_by_request_id: dict[str, str] = {}
        self._method_by_request_id: dict[str, str] = {}
        self._stream_request_ids: list[str] = []
        self._loading_finished: set[str] = set()
        self._loading_failed: set[str] = set()
        self._bytes: dict[str, int] = {}
        for name in (
            "Network.requestWillBeSent",
            "Network.responseReceived",
            "Network.loadingFinished",
            "Network.loadingFailed",
            "Network.dataReceived",
        ):
            self._cdp.on(name, lambda payload, n=name: self._handle(n, payload))

    def _handle(self, name: str, payload: dict[str, Any]) -> None:
        try:
            req_id = payload.get("requestId") or ""
            if not req_id:
                return
            if name == "Network.requestWillBeSent":
                req = payload.get("request") or {}
                self._url_by_request_id[req_id] = req.get("url", "")
                self._method_by_request_id[req_id] = req.get("method", "")
            elif name == "Network.responseReceived":
                url = self._url_by_request_id.get(req_id, "")
                method = self._method_by_request_id.get(req_id, "")
                if "/api/chat/" in url and method == "POST":
                    if req_id not in self._stream_request_ids:
                        self._stream_request_ids.append(req_id)
            elif name == "Network.loadingFinished":
                self._loading_finished.add(req_id)
            elif name == "Network.loadingFailed":
                self._loading_failed.add(req_id)
            elif name == "Network.dataReceived":
                self._bytes[req_id] = self._bytes.get(req_id, 0) + int(
                    payload.get("dataLength", 0) or 0
                )
        except Exception:
            pass

    def has_stream_started(self) -> bool:
        return bool(self._stream_request_ids)

    def wait_finish(
        self,
        page: Any,
        *,
        appearance_timeout_s: float,
        timeout_s: float,
        dom_quiet_s: float = 2.0,
    ) -> dict[str, Any]:
        """两段等待：先等流出现，再等 loadingFinished/Failed，最后 DOM 静默 settle。"""
        t0 = time.monotonic()
        appear_deadline = t0 + appearance_timeout_s
        overall_deadline = t0 + timeout_s
        target: str | None = None
        while time.monotonic() < appear_deadline and target is None:
            if self._stream_request_ids:
                target = self._stream_request_ids[0]
                break
            page.wait_for_timeout(150)
        if target is None:
            return {
                "found": False,
                "finished": False,
                "failed": False,
                "bytes_received": 0,
                "elapsed_ms": int((time.monotonic() - t0) * 1000),
            }
        while time.monotonic() < overall_deadline:
            if target in self._loading_finished or target in self._loading_failed:
                page.wait_for_timeout(int(dom_quiet_s * 1000))
                break
            page.wait_for_timeout(150)
        return {
            "found": True,
            "finished": target in self._loading_finished,
            "failed": target in self._loading_failed,
            "bytes_received": self._bytes.get(target, 0),
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }


# ---------------------------------------------------------------------------
# 页面交互助手（旧链 yuanbao.py 移植 + 豆包适配器同款模式）
# ---------------------------------------------------------------------------


def _detect_login_wall(page: Any) -> bool:
    for sel in _LOGIN_WALL_HINTS:
        try:
            if page.locator(sel).first.is_visible(timeout=500):
                return True
        except Exception:
            continue
    return False


def _captcha_hit(page: Any) -> str | None:
    for sel in _CAPTCHA_SELECTORS:
        try:
            if page.locator(sel).first.is_visible(timeout=250):
                return sel
        except Exception:
            continue
    return None


def _try_close_overlays(page: Any) -> None:
    """best-effort 关 cookie 横幅/「我知道了」等遮罩。"""
    for sel in (
        'button:has-text("我知道了")',
        'button:has-text("知道了")',
        'button:has-text("Got it")',
        'button:has-text("Accept")',
        '[aria-label="关闭"]',
        '[aria-label="close"]',
    ):
        try:
            page.locator(sel).first.click(timeout=400)
        except Exception:
            continue


def _wait_for_input(page: Any, *, timeout_ms: int) -> Any | None:
    """轮询输入框可见（旧链 _wait_for_input 移植）。"""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for sel in _INPUT_SELECTORS:
            try:
                loc = page.locator(sel).first
                loc.wait_for(state="visible", timeout=500)
                return loc
            except Exception:
                continue
        page.wait_for_timeout(300)
    return None


def _click_send_button(page: Any) -> bool:
    """第一个可见的 send 按钮点击（scroll_into_view + 完整事件链）。"""
    for sel in _SEND_SELECTORS:
        try:
            loc = page.locator(sel).first
            if not loc.is_visible(timeout=800):
                continue
            loc.scroll_into_view_if_needed(timeout=1_500)
            loc.click(timeout=3_000)
            return True
        except Exception:
            continue
    return False


def _send_via_keyboard(page: Any, input_loc: Any) -> bool:
    """Enter 提交（元宝 confirmed：Enter 即发送）；输入框清空即服务端已受理。"""
    try:
        input_loc.click()
        page.keyboard.press("Enter")
        page.wait_for_timeout(200)
    except Exception:
        return False
    return _composer_cleared(input_loc)


def _composer_cleared(input_loc: Any) -> bool:
    """输入框清空 = 提交被受理的 ground-truth 信号。"""
    try:
        return str(input_loc.evaluate(_INPUT_VALUE_JS) or "").strip() == ""
    except Exception:
        return False


def _submit_and_confirm(
    page: Any, input_loc: Any, *, attempts: int = 2, settle_ms: int = 1600, poll_ms: int = 200
) -> dict[str, Any]:
    """点击发送并确认提交真正生效，被吞时重试一次。"""
    used = 0
    for i in range(max(1, attempts)):
        used = i + 1
        if not _click_send_button(page):
            _send_via_keyboard(page, input_loc)
        waited = 0
        while waited < settle_ms:
            page.wait_for_timeout(poll_ms)
            waited += poll_ms
            if _composer_cleared(input_loc):
                return {"submitted": True, "attempts": used}
        if used < attempts:
            try:
                input_loc.click()
                page.wait_for_timeout(200)
            except Exception:
                pass
    return {"submitted": False, "attempts": used}


def _extract_answer_text(page: Any) -> str:
    """DOM 抽取助手回答文本（最后一个可见气泡为准）。"""
    for sel in _ASSISTANT_SELECTORS:
        try:
            elements = page.locator(sel).all()
            if not elements:
                continue
            text = elements[-1].inner_text(timeout=1_500)
            if text and text.strip():
                return _trim_response(text.strip())
        except Exception:
            continue
    return ""


def _wait_answer_stable(page: Any, *, max_seconds: float, quiet_seconds: float = 2.5) -> str:
    """等流式渲染的气泡文本停止变化后返回（旧链 _wait_answer_stable 移植）。"""
    last = ""
    last_change = time.monotonic()
    deadline = time.monotonic() + max_seconds
    while time.monotonic() < deadline:
        cur = _extract_answer_text(page)
        if cur != last:
            last = cur
            last_change = time.monotonic()
        if cur and (time.monotonic() - last_change) > quiet_seconds:
            return cur
        try:
            page.wait_for_timeout(400)
        except Exception:
            break
    return last


def _trim_response(text: str) -> str:
    for marker in _TRAILING_NOISE_MARKERS:
        idx = text.find(marker)
        if 0 < idx < len(text):
            text = text[:idx].rstrip()
    return text.strip()


def _references_from_dom(page: Any) -> list[dict[str, Any]]:
    """best-effort 抓引用卡片（GUESS 选择器）：只收真实 http(s) href，按 URL 去重。"""
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sel in _REFERENCE_SELECTORS:
        try:
            elements = page.locator(sel).all()
        except Exception:
            continue
        for el in elements:
            try:
                url = el.get_attribute("href") or ""
                if not url.startswith(("http://", "https://")) or url in seen:
                    continue
                title = (el.inner_text(timeout=800) or "").strip()
                seen.add(url)
                refs.append(
                    {
                        "url": url,
                        "title": title or None,
                        "sitename": None,
                        "summary": None,
                        "index": len(refs),
                    }
                )
            except Exception:
                continue
        if refs:
            break  # 第一组有产出的选择器即胜
    return refs


def _scan_dom_notices(page: Any) -> dict[str, list[str]]:
    """best-effort 读 body 文本扫系统通知词（softban 过频 / 实名墙）。"""
    try:
        body = page.locator("body").inner_text(timeout=2_000)
    except Exception:
        body = ""
    text = body or ""
    return {
        "softban": [p for p in _SOFTBAN_DOM_PHRASES if p in text],
        "realname": [p for p in _REALNAME_DOM_PHRASES if p in text],
    }


def _capture_full_page(page: Any, out_path: Path) -> None:
    """flatten 内部滚动容器后整页截图：CDP captureBeyondViewport 优先，full_page 兜底。"""
    metrics: dict[str, Any] = {}
    try:
        raw = page.evaluate(_FLATTEN_FOR_SCREENSHOT_JS)
        page.wait_for_timeout(300)
        if isinstance(raw, dict):
            metrics = raw
    except Exception:
        metrics = {}
    target_height = max(
        int(metrics.get("body_scroll_height_after") or 0),
        int(metrics.get("doc_scroll_height_after") or 0),
        int(metrics.get("scroller_full_height") or 0),
    )
    viewport_h = int(metrics.get("viewport_height") or 0)
    if target_height and target_height > viewport_h + 50:
        try:
            cdp = page.context.new_cdp_session(page)
            layout = cdp.send("Page.getLayoutMetrics")
            css_size = layout.get("cssContentSize") or layout.get("contentSize") or {}
            width = int(css_size.get("width") or 0) or 1280
            height = max(target_height, int(css_size.get("height") or 0))
            result = cdp.send(
                "Page.captureScreenshot",
                {
                    "format": "png",
                    "captureBeyondViewport": True,
                    "fromSurface": True,
                    "clip": {"x": 0, "y": 0, "width": width, "height": height, "scale": 1},
                },
            )
            png_b64 = result.get("data")
            if png_b64:
                out_path.write_bytes(base64.b64decode(png_b64))
                return
        except Exception:
            pass
    page.screenshot(path=str(out_path), full_page=True)
