"""豆包网页采集适配器 v1（Temporal activity：``collect_with_adapter``）。

由审视会话按用户直接指令实现（见 docs/contract-gaps/S01-003）。选择器、等待逻辑、
墙识别特征、SSE 组装全部移植自旧系统 live 验证过的代码：

- ``server/proxyllm/doubao_client.py``（1771 行：输入框/发送按钮、提交确认、cloak/登录墙、
  DOM 通知词表、成功门、整页截图 flatten）
- ``server/proxyllm/capture.py``（CDP Network 捕获 /chat/completion 事件流——豆包用
  SharedWorker 发请求，page.on("response") 看不到，必须走 CDP）
- ``server/proxyllm/sse_parser.py``（SSE 事件切分、块组装、references 抽取、mojibake 修复）
- ``server/proxyllm/login_state.py``（CAPTCHA_SELECTORS 验证码权威词表）

只移植 happy path + 墙分类：自愈（session_heal）、分享导出、软禁打标、HAR 落盘等外围不搬。

v1 边界：

- 仅 ``mode='normal'``；``mode='deep_think'`` →
  ``ApplicationError("deep_think not enabled in adapter v1", type="unsupported_mode",
  non_retryable=True)``。workflow 5 分钟预算放不下深度思考流，诚实拒绝。
- 配置全走 env（秘密绝不进 task payload）：
  ``GEO_DOUBAO_PROFILE_DIR``（必填，浏览器 persistent profile 目录；缺失/不存在 →
  ``adapter_not_configured`` non_retryable）；``GEO_DOUBAO_PROXY_URL``（可选，
  形如 http://user:pass@host:port——只从 env 读，日志只出现打码后的 scheme://host:port）；
  ``GEO_DOUBAO_EVIDENCE_DIR``（截图目录，默认 ``platform-v2/runtime/doubao-evidence/``，
  自动建目录）；``GEO_DOUBAO_HEADLESS``（默认 1 headless；0=headed 需 DISPLAY）。
- 执行模型：sync 浏览器驱动包在 ``asyncio.to_thread`` 里跑（activity 是 async；sync PW
  绝不能进事件循环——旧系统 greenlet 坑）。每次执行全新 context、结束即关。activity
  协程侧每 10s 泵一次 heartbeat（workflow heartbeat_timeout=30s）。注意：activity 被取消时
  to_thread 内的浏览器线程无法强杀，会随 context 关闭自然收场（v1 接受）。
- 浏览器驱动首选 patchright（旧链生产同款反检测补丁版）；vanilla playwright 的
  webdriver 指纹会触发豆包风控静默吞发送（旧链 2026-07-15 live 实证），仅作开发兜底。
- 墙分类（先截屏存证再抛，错误 message 带证据路径、绝不含秘密）：
  登录墙/实名墙 → ``wall_login_required`` non_retryable；验证码 → ``wall_captcha``
  non_retryable；发送墙/限流/cloak → ``wall_send`` non_retryable（重试只是再撞）。
- 成功判据（零合成）：提交被接受（输入框清空）且 /chat/completion 流真正
  loadingFinished 且解析出非空正文且不含墙特征——缺一都不得返回成功。
  流截断/空答案/无流 → ``answer_capture_incomplete``（可重试的诚实失败）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import structlog
from temporalio import activity
from temporalio.exceptions import ApplicationError

from domain.evidence.dlp import assert_secret_free
from workflows.activities.collection import CollectionTaskInput, CollectionTaskResult

log = structlog.get_logger()

ENV_PROFILE_DIR = "GEO_DOUBAO_PROFILE_DIR"
ENV_PROXY_URL = "GEO_DOUBAO_PROXY_URL"
ENV_EVIDENCE_DIR = "GEO_DOUBAO_EVIDENCE_DIR"
ENV_SHARED_EVIDENCE_DIR = "GEO_ADAPTER_EVIDENCE_DIR"  # 多平台共享证据目录（兜底于专属项之后）
ENV_HEADLESS = "GEO_DOUBAO_HEADLESS"

_DEFAULT_EVIDENCE_DIR = Path(__file__).resolve().parents[2] / "runtime" / "doubao-evidence"
_HEARTBEAT_INTERVAL_S = 10.0  # workflow heartbeat_timeout=30s，泵频 ≤15s 硬约束
_NAV_TIMEOUT_MS = 25_000
_CHAT_TIMEOUT_S = 120.0  # normal 模式流式完成预算（workflow 总预算 5 分钟）

_CHAT_URL = "https://www.doubao.com/chat/"
_HOME_URL = "https://www.doubao.com/"

# 旧链 doubao_client.py 实测 UA / locale / 时区
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 聊天输入框（Semi UI textarea；placeholder 是最稳定信号，scripts/inspect_via_proxy.py 发现）
_INPUT_SELECTORS: tuple[str, ...] = (
    'textarea.semi-input-textarea[placeholder*="发消息"]',
    'textarea[placeholder*="发消息"]',
    "textarea.semi-input-textarea-autosize",
    'textarea[placeholder*="问豆包"]',
    'textarea[placeholder*="message"]',
    'div[contenteditable="true"][placeholder*="发消息"]',
    'div[contenteditable="true"]',
    "textarea",
)

# 助手消息气泡（最后一个为准）
_ASSISTANT_SELECTORS: tuple[str, ...] = (
    'div[data-testid="message_text_content"]',
    "[data-testid*='assistant']",
    "[data-message-author-role='assistant']",
    ".message-bubble.assistant",
    ".markdown-body",
    ".message-content",
)

# 阻断交互的登录模态（非导航栏登录按钮）：含二维码或手机号输入的居中 dialog
_LOGIN_WALL_HINTS: tuple[str, ...] = (
    'div[role="dialog"]:has-text("扫码登录")',
    'div[role="dialog"]:has-text("手机号登录")',
    'div[role="dialog"]:has-text("登录后体验完整功能")',
    'div[class*="login-modal"]:visible',
    'div[class*="LoginModal"]:visible',
)

# 验证码组件（login_state.CAPTCHA_SELECTORS 权威词表）
_CAPTCHA_SELECTORS: tuple[str, ...] = (
    'iframe[src*="captcha"]',
    'iframe[src*="verify"]',
    'div[class*="captcha"]:visible',
    'div[id*="verify"]:visible',
    'div[class*="verify-wrap"]:visible',
)

_CLOAK_TEXT_MARKERS: tuple[str, ...] = ("页面暂时不可用", "页面不可用")

# DOM 层系统通知词表（softban 过频提示 / 实名墙）——命中判定 gated by not has_answer，
# 出了真答案的运行绝不误判（答案正文提及「过频/实名」不翻标记）
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
    "Want me to ",
    "你想",
    "需要我",
    "继续追问",
    "下载电脑版",
    "\n快速\n",
    "\n超能模式\n",
    "PPT 生成",
    "AI 表格",
    "图像生成",
    "帮我写作",
    "发消息",
)

# JS：在 textarea 右侧/下方找方形带 svg 的发送按钮并打 data 标记（旧链 _TAG_JS）
_TAG_JS = """() => {
    document.querySelectorAll('[data-proxyllm-send]').forEach(
        e => e.removeAttribute('data-proxyllm-send'));
    const ta = document.querySelector('textarea[placeholder*="发消息"]');
    if (!ta) return false;
    const tar = ta.getBoundingClientRect();
    const cands = Array.from(document.querySelectorAll('button, [role="button"]'));
    const scored = [];
    for (const el of cands) {
        if (el.disabled) continue;
        if (el.offsetParent === null) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 24 || r.height < 24) continue;
        if (r.width > 80 || r.height > 80) continue;
        const ratio = Math.min(r.width, r.height) / Math.max(r.width, r.height);
        if (ratio < 0.7) continue;
        if (!el.querySelector('svg')) continue;
        if (r.x < tar.x + tar.width * 0.6) continue;
        if (r.y < tar.y - 30) continue;
        if (r.y > tar.y + tar.height + 240) continue;
        scored.push({el, score: r.x * 1000 + r.y});
    }
    scored.sort((a, b) => b.score - a.score);
    if (scored.length === 0) return false;
    scored[0].el.setAttribute('data-proxyllm-send', 'true');
    return true;
}"""

_INPUT_VALUE_JS = (
    "el => (el.value !== undefined && el.value !== null) ? el.value : (el.textContent || '')"
)

# 整页截图前把豆包内部 overflow 滚动容器压平进文档流（旧链 _FLATTEN_FOR_SCREENSHOT_JS）
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
class DoubaoAdapterConfig:
    """env 配置。proxy_url 原文只在启动浏览器时使用，绝不落日志/payload。"""

    profile_dir: Path
    proxy_url: str | None
    evidence_dir: Path
    headless: bool

    @classmethod
    def from_env(cls) -> DoubaoAdapterConfig:
        raw_profile = os.environ.get(ENV_PROFILE_DIR, "").strip()
        if not raw_profile:
            raise ApplicationError(
                f"{ENV_PROFILE_DIR} is not set — doubao adapter requires a persistent "
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
        raw_evidence = (
            os.environ.get(ENV_EVIDENCE_DIR, "").strip()
            or os.environ.get(ENV_SHARED_EVIDENCE_DIR, "").strip()
        )
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


class _Cloaked(RuntimeError):
    """豆包反爬 cloak（“页面暂时不可用”）——采集线程内部信号，调用方转 _WallError。"""


@dataclass
class CollectedAnswer:
    answer_text: str
    references: list[dict[str, Any]]
    screenshot_path: Path
    meta: dict[str, Any] = field(default_factory=dict)


class _BrowserSession(Protocol):
    """Playwright 交互隔离面：测试注入 fake，绝不启动真浏览器。"""

    def collect(self, query: str, on_stage: Callable[[str], None]) -> CollectedAnswer: ...


SessionFactory = Callable[[DoubaoAdapterConfig, Path, str], _BrowserSession]


# ---------------------------------------------------------------------------
# activity 入口与异步泵
# ---------------------------------------------------------------------------


@activity.defn(name="collect_with_adapter")
async def collect_with_adapter(item: CollectionTaskInput) -> CollectionTaskResult:
    """豆包 live 适配器注册实现（workers/main.py 按 GEO_COLLECTION_ADAPTER 门控选择）。"""
    try:
        attempt = activity.info().attempt
    except RuntimeError:
        attempt = 1
    return await run_doubao_collection(
        item,
        session_factory=_PlaywrightDoubaoSession,
        heartbeat=activity.heartbeat,
        attempt=attempt,
    )


async def run_doubao_collection(
    item: CollectionTaskInput,
    *,
    session_factory: SessionFactory | None = None,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
    attempt: int = 1,
) -> CollectionTaskResult:
    """activity 核心：配置门 → mode 门 → to_thread 跑浏览器 → 墙/结果映射。

    与 activity 上下文解耦（heartbeat/attempt 注入），测试全程 mock 浏览器层。
    session_factory/heartbeat 缺省用真实实现与 no-op——platform_registry dispatcher
    只传 ``(item, heartbeat=...)``，与本签名对齐。
    """
    if session_factory is None:
        session_factory = _PlaywrightDoubaoSession
    if heartbeat is None:

        def heartbeat(payload: dict[str, Any]) -> None:
            del payload

    if item.mode != "normal":
        raise ApplicationError(
            "deep_think not enabled in adapter v1",
            type="unsupported_mode",
            non_retryable=True,
        )
    config = DoubaoAdapterConfig.from_env()
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    file_stem = f"{_safe_stem(item.business_key)}-a{attempt}"
    bound = log.bind(
        business_key=item.business_key,
        attempt=attempt,
        proxy=mask_proxy_url(config.proxy_url),
    )
    progress = {"stage": "browser_launch"}

    def _blocking() -> CollectedAnswer:
        session = session_factory(config, config.evidence_dir, file_stem)
        return session.collect(item.query, on_stage=lambda s: progress.__setitem__("stage", s))

    thread = asyncio.ensure_future(asyncio.to_thread(_blocking))
    while True:
        heartbeat({"business_key": item.business_key, "stage": progress["stage"]})
        done, _pending = await asyncio.wait({thread}, timeout=_HEARTBEAT_INTERVAL_S)
        if done:
            break
    try:
        collected = thread.result()
    except _WallError as wall:
        evidence = f"; evidence={wall.evidence_path}" if wall.evidence_path else ""
        await bound.ainfo("doubao_wall", wall_type=wall.wall_type, stage=progress["stage"])
        raise ApplicationError(
            f"{wall}{evidence}", type=wall.wall_type, non_retryable=True
        ) from wall
    except _IncompleteCapture as inc:
        evidence = f"; evidence={inc.evidence_path}" if inc.evidence_path else ""
        await bound.ainfo("doubao_capture_incomplete", reason=str(inc), stage=progress["stage"])
        raise ApplicationError(f"{inc}{evidence}", type="answer_capture_incomplete") from inc
    await bound.ainfo(
        "doubao_collect_ok",
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


class _PlaywrightDoubaoSession:
    """豆包网页采集的 sync Playwright 实现（persistent context，每次全新、结束即关）。"""

    def __init__(self, config: DoubaoAdapterConfig, evidence_dir: Path, file_stem: str) -> None:
        self._config = config
        self._evidence_dir = evidence_dir
        self._file_stem = file_stem

    def collect(self, query: str, on_stage: Callable[[str], None]) -> CollectedAnswer:
        # 延迟导入：模块加载不硬依赖浏览器驱动（worker 未装依赖时仍可注册 fail-closed 实现）。
        # 驱动首选 patchright（旧链生产同款，反检测补丁版）：vanilla playwright 的
        # webdriver 指纹会触发豆包风控静默吞发送（composer 不清空、/completion 不触发，
        # 旧链 2026-07-15 live 实证）——这正是 v1 冒烟 send-not-accepted 的根因。
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
                capture = _CompletionCapture(context, page)

                on_stage("navigate")
                try:
                    page.goto(_CHAT_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
                except PWTimeout:
                    page.goto(_HOME_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
                page.wait_for_timeout(6_000)  # SPA + 反爬 JS 挂载（旧链同款 settle）
                _try_close_overlays(page)
                if _detect_login_wall(page):
                    raise _WallError(
                        "wall_login_required",
                        "doubao login wall detected right after navigation",
                        self._shot(page, "login"),
                    )

                on_stage("await_input")
                try:
                    input_loc = _wait_for_input_or_cloak(page, timeout_ms=15_000)
                except _Cloaked as exc:
                    raise _WallError(
                        "wall_send",
                        f"doubao_cloaked: {exc}",
                        self._shot(page, "cloaked"),
                    ) from exc
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
                input_loc.type(query, delay=20)
                page.wait_for_timeout(800)

                submit = _submit_and_confirm(page, input_loc)
                if not submit.get("submitted"):
                    raise _WallError(
                        "wall_send",
                        "send-not-accepted: composer still populated after "
                        f"{submit.get('attempts', '?')} send attempts (submission swallowed)",
                        self._shot(page, "send_wall"),
                    )
                on_stage("submitted")

                # 异步验证码窗口：challenge 发送后 ~2.2s 才挂载（旧链实测），轮询至多 12s；
                # 流已开始且过 3.5s settle 窗即快走（迟到 captcha 不会藏在截断 stub 后面）
                challenge_start = time.monotonic()
                while time.monotonic() < challenge_start + 12.0:
                    hit = _captcha_hit(page)
                    if hit:
                        raise _WallError(
                            "wall_captcha",
                            f"captcha challenge appeared post-send ({hit})",
                            self._shot(page, "captcha"),
                        )
                    if (
                        capture.has_completion_started()
                        and time.monotonic() - challenge_start >= 3.5
                    ):
                        break
                    page.wait_for_timeout(500)

                on_stage("await_stream")
                meta = capture.wait_finish(
                    page, appearance_timeout_s=20.0, timeout_s=_CHAT_TIMEOUT_S
                )
                answer_text = ""
                references: list[dict[str, Any]] = []
                sse_body = capture.latest_body()
                if sse_body:
                    rich = _rich_record_from_sse(sse_body)
                    if rich is not None:
                        answer_text = str(rich.get("answer_text") or "").strip()
                        references = list(rich.get("references") or [])
                if not answer_text and meta.get("found"):
                    # SSE 捕获竞态失败时的 DOM 兜底（旧链同款回退路径）
                    answer_text = _extract_response_text(page, query)
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
                        "send-accepted-no-completion: composer cleared (submission accepted) "
                        "but no /chat/completion stream fired within timeout — likely "
                        "content-filter or silent server-side drop",
                        self._shot(page, "no_stream"),
                    )
                if not meta.get("finished"):
                    raise _IncompleteCapture(
                        "stream-open-at-timeout: /chat/completion stream still open after "
                        f"budget ({meta.get('bytes_received', 0)} bytes captured) — answer "
                        "would be truncated; failing honestly",
                        self._shot(page, "truncated"),
                    )
                if not answer_text:
                    raise _IncompleteCapture(
                        "answer-empty-after-finished-stream: neither SSE assembly nor DOM "
                        "fallback produced answer text",
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
                        "sse_body_bytes": len(sse_body),
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


class _CompletionCapture:
    """CDP Network 层捕获 /chat/completion 事件流（旧链 capture.py 的 v1 精简移植）。

    豆包经 SharedWorker 发请求，page.on("response") 看不到，必须 CDP。
    loadingFinished 同步拉 getResponseBody（Chromium 只短暂保留缓冲）。
    """

    def __init__(self, context: Any, page: Any) -> None:
        self._cdp = context.new_cdp_session(page)
        self._cdp.send("Network.enable")
        self._url_by_request_id: dict[str, str] = {}
        self._completion_request_ids: list[str] = []
        self._loading_finished: set[str] = set()
        self._loading_failed: set[str] = set()
        self._bytes: dict[str, int] = {}
        self._bodies: dict[str, str] = {}
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
                self._url_by_request_id[req_id] = (payload.get("request") or {}).get("url", "")
            elif name == "Network.responseReceived":
                resp = payload.get("response") or {}
                url = self._url_by_request_id.get(req_id, "")
                if "/chat/completion" in url and "event-stream" in (resp.get("mimeType") or ""):
                    if req_id not in self._completion_request_ids:
                        self._completion_request_ids.append(req_id)
            elif name == "Network.loadingFinished":
                self._loading_finished.add(req_id)
                if req_id in self._completion_request_ids:
                    self._fetch_body(req_id)
            elif name == "Network.loadingFailed":
                self._loading_failed.add(req_id)
            elif name == "Network.dataReceived":
                self._bytes[req_id] = self._bytes.get(req_id, 0) + int(
                    payload.get("dataLength", 0) or 0
                )
        except Exception:
            pass

    def _fetch_body(self, req_id: str) -> None:
        if req_id in self._bodies:
            return
        try:
            result = self._cdp.send("Network.getResponseBody", {"requestId": req_id})
        except Exception:
            return
        body = result.get("body", "") or ""
        if result.get("base64Encoded"):
            try:
                body = base64.b64decode(body).decode("utf-8", "replace")
            except Exception:
                return
        self._bodies[req_id] = _recover_mojibake(body)

    def has_completion_started(self) -> bool:
        return bool(self._completion_request_ids)

    def latest_body(self) -> str:
        for rid in reversed(self._completion_request_ids):
            if rid in self._bodies:
                return self._bodies[rid]
        return ""

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
            if self._completion_request_ids:
                target = self._completion_request_ids[0]
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
# 页面交互助手（旧链 doubao_client.py 移植）
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


def _wait_for_input_or_cloak(page: Any, *, timeout_ms: int) -> Any | None:
    """轮询输入框可见 OR cloak 文本出现（反爬 JS 会在 domcontentloaded 后换 DOM）。"""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            body_text = page.locator("body").inner_text(timeout=1500)
        except Exception:
            body_text = ""
        if body_text and any(marker in body_text for marker in _CLOAK_TEXT_MARKERS):
            raise _Cloaked(f"bot-detection cloak (body={len(body_text)} chars)")
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
    """JS 打标发送按钮后经 Locator 点击（hover+mousedown+mouseup 完整事件链）。"""
    try:
        tagged = page.evaluate(_TAG_JS)
    except Exception:
        return False
    if not tagged:
        return False
    try:
        loc = page.locator('[data-proxyllm-send="true"]').first
        loc.scroll_into_view_if_needed(timeout=2000)
        loc.click(timeout=4000, force=False)
        return True
    except Exception:
        return False


def _send_via_keyboard(page: Any, input_loc: Any) -> bool:
    """Meta+Enter / Control+Enter / Enter 逐个试，输入框清空即服务端已受理。"""
    for shortcut in ("Meta+Enter", "Control+Enter", "Enter"):
        try:
            page.keyboard.press(shortcut)
            page.wait_for_timeout(200)
        except Exception:
            continue
        try:
            current = input_loc.evaluate(_INPUT_VALUE_JS)
        except Exception:
            current = None
        if current is not None and str(current).strip() == "":
            return True
    return False


def _composer_cleared(input_loc: Any) -> bool:
    """输入框清空 = 提交被受理的 ground-truth 信号（live 实证：清空 ⇔ /completion 发出）。"""
    try:
        return str(input_loc.evaluate(_INPUT_VALUE_JS) or "").strip() == ""
    except Exception:
        return False


def _submit_and_confirm(
    page: Any, input_loc: Any, *, attempts: int = 2, settle_ms: int = 1600, poll_ms: int = 200
) -> dict[str, Any]:
    """点击发送并确认提交真正生效，被吞时重试一次（2026-07-15 live 风控间歇性吞点击）。"""
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


def _extract_response_text(page: Any, query: str = "") -> str:
    """DOM 兜底抽取：先助手气泡选择器，再按「query 最后出现位置之后」切正文。"""
    for sel in _ASSISTANT_SELECTORS:
        try:
            elements = page.locator(sel).all()
            if not elements:
                continue
            text = elements[-1].inner_text(timeout=2000)
            if text and text.strip():
                return _trim_response(text.strip())
        except Exception:
            continue
    try:
        body = page.locator("body").inner_text(timeout=2000)
    except Exception:
        body = ""
    if query and query in body:
        idx = body.rfind(query)
        after = body[idx + len(query) :].strip()
        return _trim_response(after)
    return body.strip()


def _trim_response(text: str) -> str:
    for marker in _TRAILING_NOISE_MARKERS:
        idx = text.find(marker)
        if 0 < idx < len(text):
            text = text[:idx].rstrip()
    return text.strip()


def _scan_dom_notices(page: Any) -> dict[str, list[str]]:
    """best-effort 读 body 文本扫系统通知词（softban 过频 / 实名墙）。"""
    try:
        body = page.locator("body").inner_text(timeout=2000)
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


# ---------------------------------------------------------------------------
# SSE 解析（旧链 sse_parser.py 移植：事件切分 → 块组装 → answer/references 扁平化）
# ---------------------------------------------------------------------------


def _rich_record_from_sse(body: str) -> dict[str, Any] | None:
    try:
        events = _parse_sse_events(body)
        assembled = _assemble_final_message(events)
        return _build_rich_record(assembled)
    except Exception:
        return None


_MOJIBAKE_SENTINELS = (
    "ä",
    "æ",
    "å",
    "ç",
    "è",
    "é",
    "ê",
    "ë",
    "ï",
    "Ã",
    "Â",
    "ã",
    "â",
    "à",
    "á",
    "ô",
    "ö",
    "ü",
    "ñ",
)


def _looks_recovered(decoded: str, original: str) -> bool:
    if len(decoded) > len(original):
        return False
    for ch in decoded:
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF or 0x3000 <= cp <= 0x303F or 0xFF00 <= cp <= 0xFFEF:
            return True
        if 0x2010 <= cp <= 0x2027:
            return True
    return False


def _recover_mojibake(s: Any) -> Any:
    """CDP getResponseBody 的 cp1252 误解码 UTF-8 修复（混合文本逐字符重建字节流）。"""
    if not isinstance(s, str) or not s:
        return s
    if not any(c in s for c in _MOJIBAKE_SENTINELS):
        return s
    try:
        out = bytearray()
        for c in s:
            try:
                out.extend(c.encode("cp1252"))
            except UnicodeEncodeError:
                cp = ord(c)
                if cp < 256:
                    out.append(cp)
                else:
                    out.extend(c.encode("utf-8"))
        decoded = out.decode("utf-8", errors="replace")
        if decoded.count("�") <= s.count("�") and _looks_recovered(decoded, s):
            return decoded
    except Exception:
        pass
    return s


def _recover_in_place(obj: Any) -> Any:
    if isinstance(obj, str):
        return _recover_mojibake(obj)
    if isinstance(obj, dict):
        return {k: _recover_in_place(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_recover_in_place(x) for x in obj]
    return obj


def _parse_sse_events(body: str) -> list[dict[str, Any]]:
    """把原始 SSE body 切成 [{event, data, id, raw}]；data: [DONE] 跳过。"""
    if not body:
        return []
    out: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        block = block.strip("\r\n")
        if not block:
            continue
        ev_name: str | None = None
        ev_id: int | None = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev_name = line[len("event:") :].strip()
            elif line.startswith("id:"):
                try:
                    ev_id = int(line[len("id:") :].strip())
                except ValueError:
                    ev_id = None
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
        if ev_name is None and not data_lines:
            continue
        raw = "\n".join(data_lines).strip()
        data: Any = None
        if raw and raw != "[DONE]":
            try:
                data = json.loads(raw)
            except Exception:
                data = None
        out.append({"event": ev_name or "", "id": ev_id, "data": data, "raw": raw})
    return out


def _walk_block_snapshots(node: Any, sink: list[dict[str, Any]]) -> None:
    """收集 node 下所有 content_block 列表里的块。"""
    if isinstance(node, dict):
        cb = node.get("content_block")
        if isinstance(cb, list):
            for b in cb:
                if isinstance(b, dict):
                    sink.append(b)
        for v in node.values():
            _walk_block_snapshots(v, sink)
    elif isinstance(node, list):
        for x in node:
            _walk_block_snapshots(x, sink)


def _assemble_final_message(events: list[dict[str, Any]]) -> dict[str, Any]:
    """把 STREAM_*/CHUNK_DELTA 事件重放成单条消息（parent_id=="" 为主回答）。"""
    blocks_by_id: dict[str, dict[str, Any]] = {}
    first_seen: dict[str, int] = {}
    delta_text: dict[str, list[str]] = {}
    active_text_block_id: str | None = None
    message_id: str | None = None
    conversation_id: str | None = None
    section_id: str | None = None

    for idx, ev in enumerate(events):
        e_name = ev.get("event") or ""
        d = ev.get("data")
        if e_name == "SSE_ACK" and isinstance(d, dict):
            ack = d.get("ack_client_meta") or {}
            conversation_id = conversation_id or ack.get("conversation_id")
            section_id = section_id or ack.get("section_id")
        elif e_name in ("FULL_MSG_NOTIFY", "STREAM_MSG_NOTIFY") and isinstance(d, dict):
            msg = d.get("message") or {}
            meta = d.get("meta") or {}
            content_root = d.get("content") if isinstance(d.get("content"), dict) else None
            mid = msg.get("message_id") or meta.get("message_id")
            cid = msg.get("conversation_id") or meta.get("conversation_id")
            sid = msg.get("section_id") or meta.get("section_id")
            if msg.get("user_type") == 1:
                continue
            if msg.get("user_type") == 2 or e_name == "STREAM_MSG_NOTIFY":
                if mid:
                    message_id = mid
                if cid:
                    conversation_id = cid
                if sid:
                    section_id = sid
                snap_blocks: list[dict[str, Any]] = []
                _walk_block_snapshots(content_root or msg, snap_blocks)
                for b in snap_blocks:
                    bid = b.get("block_id")
                    if not bid:
                        continue
                    first_seen.setdefault(bid, idx)
                    blocks_by_id[bid] = b
                    if b.get("block_type") == 10000:
                        active_text_block_id = bid
                        text_in = ((b.get("content") or {}).get("text_block") or {}).get(
                            "text"
                        ) or ""
                        if text_in:
                            delta_text.setdefault(bid, []).append(_recover_mojibake(text_in))
        elif e_name == "STREAM_CHUNK" and isinstance(d, dict):
            mid = d.get("message_id")
            if mid:
                message_id = mid
            for po in d.get("patch_op") or []:
                pv = po.get("patch_value") or {}
                snap_blocks = []
                _walk_block_snapshots(pv, snap_blocks)
                for b in snap_blocks:
                    bid = b.get("block_id")
                    if not bid:
                        continue
                    first_seen.setdefault(bid, idx)
                    blocks_by_id[bid] = b
                    if b.get("block_type") == 10000:
                        active_text_block_id = bid
                        text_in = ((b.get("content") or {}).get("text_block") or {}).get(
                            "text"
                        ) or ""
                        if text_in:
                            delta_text.setdefault(bid, []).append(_recover_mojibake(text_in))
        elif e_name == "CHUNK_DELTA" and isinstance(d, dict):
            t = d.get("text") or ""
            if t and active_text_block_id:
                delta_text.setdefault(active_text_block_id, []).append(_recover_mojibake(t))

    final_blocks: list[dict[str, Any]] = []
    for bid in sorted(blocks_by_id.keys(), key=lambda b: first_seen.get(b, 0)):
        b = _recover_in_place(blocks_by_id[bid])
        if b.get("block_type") == 10000:
            inner = b.get("content") or {}
            if not isinstance(inner, dict):
                inner = {}
            tb = dict(inner.get("text_block") or {})
            joined = "".join(delta_text.get(bid, []))
            if joined:
                tb["text"] = joined
            elif tb.get("text"):
                tb["text"] = _recover_mojibake(tb["text"])
            inner["text_block"] = tb
            b["content"] = inner
        final_blocks.append(b)

    return {
        "conversation_id": conversation_id,
        "section_id": section_id,
        "message_id": message_id,
        "content_block": final_blocks,
    }


def _block_text(b: dict[str, Any]) -> str:
    inner = b.get("content") or {}
    tb = (inner.get("text_block") or {}) if isinstance(inner, dict) else {}
    return tb.get("text") or ""


def _is_real_url(u: Any) -> bool:
    return isinstance(u, str) and (u.startswith("http://") or u.startswith("https://"))


def _build_rich_record(assembled: dict[str, Any]) -> dict[str, Any]:
    """扁平化成 answer_text + references（去重 by URL，旧链同口径）。"""
    blocks = assembled.get("content_block") or []
    thinking_root: dict[str, Any] | None = None
    for b in blocks:
        if b.get("block_type") == 10040:
            thinking_root = b
            break
    thinking_id = thinking_root.get("block_id") if thinking_root else None
    references: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    answer_parts: list[str] = []

    for b in blocks:
        bt = b.get("block_type")
        pid = b.get("parent_id") or ""
        if bt == 10000:
            text = _block_text(b)
            if not text:
                continue
            if thinking_id and pid == thinking_id:
                continue  # 思考链不进正文
            if pid == "":
                answer_parts.append(text)
        elif bt == 10025:
            inner = b.get("content") or {}
            sqr = (inner.get("search_query_result_block") or {}) if isinstance(inner, dict) else {}
            for res in sqr.get("results") or []:
                if not isinstance(res, dict):
                    continue
                tc = res.get("text_card") or {}
                if not isinstance(tc, dict):
                    continue
                url = tc.get("url")
                if not _is_real_url(url):
                    continue
                key = str(url).split("?")[0]
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                references.append(
                    {
                        "url": url,
                        "title": tc.get("title"),
                        "summary": tc.get("summary"),
                        "sitename": tc.get("sitename"),
                        "index": tc.get("index", res.get("index")),
                    }
                )

    answer_text = "\n\n".join(p.strip() for p in answer_parts if p and p.strip())
    return {
        "answer_text": answer_text,
        "references": references,
        "conversation_id": assembled.get("conversation_id"),
        "section_id": assembled.get("section_id"),
        "message_id": assembled.get("message_id"),
    }
