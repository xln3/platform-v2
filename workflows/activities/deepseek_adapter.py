"""DeepSeek 网页采集适配器 v1（chat.deepseek.com；注册表统一包 ``@activity.defn``）。

结构严格镜像 ``doubao_adapter.py``（已 live 验证的同款 v1 契约）。DeepSeek 平台知识
移植自旧链（``server/proxyllm/engines/deepseek.py``、``server/geosys/collector_deepseek.py``），
关键面 2026-07-27 已 live 校准：输入框 placeholder / 回答气泡
``div.ds-markdown.ds-assistant-message-main-content`` / SSE JSON-patch 增量流 schema
（见 ``_collect_event_text``，含 answer_len=1 根因记录）；登录墙 /sign_in 跳转为旧链
CONFIRMED 信号。仍未校准项在各常量行内标注（发送按钮锚点、尾部噪声词表等）。

DeepSeek 特性：``/api/v0/chat/completion`` 每请求带 WASM PoW（``x-ds-pow-*``），
真实浏览器管线自动解题——故坚持 DOM/浏览器路径，绝不直连 API、绝不重实现 wasm。

v1 边界（与 doubao v1 对齐）：

- 仅 ``mode='normal'``；``mode='deep_think'`` →
  ``ApplicationError("deep_think not enabled in adapter v1", type="unsupported_mode",
  non_retryable=True)``。深度思考(R1) 开关点击超出 v1（selector 未校准，误点即脏数据）。
- 联网搜索(联网搜索)开关 v1 不点击（selector 未校准；诚实声明 answer 为默认会话口径）。
- 配置全走 env（秘密绝不进 task payload）：
  ``GEO_DEEPSEEK_PROFILE_DIR``（必填，persistent profile 目录；缺失/不存在 →
  ``adapter_not_configured`` non_retryable）；``GEO_DEEPSEEK_PROXY_URL``（可选，
  形如 http://user:pass@host:port——日志只出现打码后的 scheme://host:port）；
  ``GEO_ADAPTER_EVIDENCE_DIR``（五平台共享截图目录 env，缺省
  ``platform-v2/runtime/adapter-evidence/deepseek/``，自动建目录）；
  ``GEO_DEEPSEEK_HEADLESS``（默认 1 headless；0=headed 需 DISPLAY）。
- 执行模型：sync 浏览器驱动包在 ``asyncio.to_thread`` 里跑（sync PW 绝不能进事件
  循环——旧系统 greenlet 坑）。每次执行全新 context、结束即关。协程侧每 10s 泵一次
  heartbeat（workflow heartbeat_timeout=30s）。
- 浏览器驱动首选 patchright（旧链生产同款反检测补丁版）；vanilla playwright 仅兜底。
- 墙分类（先截屏存证再抛，错误 message 带证据路径、绝不含秘密）：
  登录墙（未登录访问 ``/`` 自动跳 ``/sign_in``，旧链 CONFIRMED 信号）/实名墙 →
  ``wall_login_required`` non_retryable；验证码 → ``wall_captcha`` non_retryable；
  发送墙/限流 → ``wall_send`` non_retryable（重试只是再撞）。
- 成功判据（零合成）：提交被接受（输入框清空）且 completion 流真正 loadingFinished
  且解析出非空正文且不含墙特征——缺一都不得返回成功。流截断/空答案/无流 →
  ``answer_capture_incomplete``（可重试的诚实失败）。
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

ENV_PROFILE_DIR = "GEO_DEEPSEEK_PROFILE_DIR"
ENV_PROXY_URL = "GEO_DEEPSEEK_PROXY_URL"
ENV_EVIDENCE_DIR = "GEO_ADAPTER_EVIDENCE_DIR"  # 五平台共享 env；缺省落 deepseek 子目录
ENV_HEADLESS = "GEO_DEEPSEEK_HEADLESS"

_DEFAULT_EVIDENCE_DIR = (
    Path(__file__).resolve().parents[2] / "runtime" / "adapter-evidence" / "deepseek"
)
_HEARTBEAT_INTERVAL_S = 10.0  # workflow heartbeat_timeout=30s，泵频 ≤15s 硬约束
_NAV_TIMEOUT_MS = 25_000
_CHAT_TIMEOUT_S = 120.0  # normal 模式流式完成预算（workflow 总预算 5 分钟）

_CHAT_URL = "https://chat.deepseek.com/"
_SIGN_IN_PATH = "/sign_in"  # 未登录访问 / 自动跳 /sign_in（旧链 CONFIRMED 信号）

# 旧链 deepseek.py 同款 UA / locale / 时区
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# completion 流端点（旧链 recon CONFIRMED：POST /api/v0/chat/completion，SSE）
_COMPLETION_URL_HINTS: tuple[str, ...] = (
    "/api/v0/chat/completion",
    "/chat/completion",
)

# 聊天输入框（textarea[placeholder*="给 DeepSeek"] 2026-07-27 live 校准 CONFIRMED：
# placeholder="给 DeepSeek 发送消息"；其余为防御兜底）
_INPUT_SELECTORS: tuple[str, ...] = (
    "textarea#chat-input",
    'textarea[placeholder*="给 DeepSeek"]',
    'textarea[placeholder*="发消息"]',
    'div[contenteditable="true"]',
    "textarea",
)

# 助手消息气泡（最后一个为准；div[class*="ds-markdown"] 2026-07-27 live 校准
# CONFIRMED：class="ds-markdown ds-assistant-message-main-content"；
# div.markdown / .markdown-body 实测不存在，留作防御兜底）
_ASSISTANT_SELECTORS: tuple[str, ...] = (
    'div[class*="ds-markdown"]',
    "div.markdown",
    "[class*='message'][class*='assistant']",
    ".markdown-body",
)

# 阻断交互的登录模态（⚠ GUESS；主信号是 /sign_in 跳转，这里做防御兜底）
_LOGIN_WALL_HINTS: tuple[str, ...] = (
    'div[role="dialog"]:has-text("扫码登录")',
    'div[role="dialog"]:has-text("手机号登录")',
    'div[role="dialog"]:has-text("微信扫码登录")',
    'div[class*="login-modal"]:visible',
)

# 登录页正文标志词（旧链 SIGN_IN_TEXT_MARKERS，CONFIRMED 自 sign_in 页）
_SIGN_IN_TEXT_MARKERS: tuple[str, ...] = (
    "微信扫码登录",
    "发送验证码",
    "手机号登录",
    "密码登录",
)

# 验证码组件（login_state.CAPTCHA_SELECTORS 权威词表 + geetest 系 GUESS）
_CAPTCHA_SELECTORS: tuple[str, ...] = (
    'iframe[src*="captcha"]',
    'iframe[src*="verify"]',
    'iframe[src*="geetest"]',
    'div[class*="captcha"]:visible',
    'div[id*="verify"]:visible',
    'div[class*="verify-wrap"]:visible',
    'div[class*="geetest"]:visible',
    'div[id*="geetest"]:visible',
)

# DOM 层系统通知词表（限流提示 / 实名墙）——命中判定 gated by not has_answer，
# 出了真答案的运行绝不误判（答案正文提及「过频/实名」不翻标记）
_SOFTBAN_DOM_PHRASES: tuple[str, ...] = (
    "今日请求过频",
    "请求过于频繁",
    "请求太频繁",
    "操作过于频繁",
    "操作太频繁",
    "发送频率过高",
    "发送太频繁",
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

# 推理/搜索痕迹行（正文在其后；旧链 _REASONING_TRACE_MARKERS，⚠ GUESS zh）
_REASONING_TRACE_MARKERS: tuple[str, ...] = (
    "已深度思考",
    "已思考",
    "搜索到",  # "搜索到N个网页"
    "浏览了",  # "浏览了N个页面"
)

# DOM 兜底抽取后裁剪尾部 UI 噪声（⚠ GUESS，未校准；只收 UI 专属文案，防误裁正文）
_TRAILING_NOISE_MARKERS: tuple[str, ...] = (
    "内容由 AI 生成",
    "给 DeepSeek 发送消息",
)

# JS：在输入框右侧/下方找方形带 svg 的发送按钮并打 data 标记（照 doubao _TAG_JS 改输入框锚点）
_TAG_JS = """() => {
    document.querySelectorAll('[data-geo-send]').forEach(
        e => e.removeAttribute('data-geo-send'));
    const ta = document.querySelector('textarea#chat-input')
        || document.querySelector('textarea[placeholder*="给 DeepSeek"]')
        || document.querySelector('textarea[placeholder*="发消息"]')
        || document.querySelector('div[contenteditable="true"]')
        || document.querySelector('textarea');
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
    scored[0].el.setAttribute('data-geo-send', 'true');
    return true;
}"""

_INPUT_VALUE_JS = (
    "el => (el.value !== undefined && el.value !== null) ? el.value : (el.textContent || '')"
)

# 整页截图前把内部 overflow 滚动容器压平进文档流（照 doubao _FLATTEN_FOR_SCREENSHOT_JS）
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
class DeepseekAdapterConfig:
    """env 配置。proxy_url 原文只在启动浏览器时使用，绝不落日志/payload。"""

    profile_dir: Path
    proxy_url: str | None
    evidence_dir: Path
    headless: bool

    @classmethod
    def from_env(cls) -> DeepseekAdapterConfig:
        raw_profile = os.environ.get(ENV_PROFILE_DIR, "").strip()
        if not raw_profile:
            raise ApplicationError(
                f"{ENV_PROFILE_DIR} is not set — deepseek adapter requires a persistent "
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


SessionFactory = Callable[[DeepseekAdapterConfig, Path, str], _BrowserSession]


def _default_heartbeat() -> Callable[[dict[str, Any]], None]:
    """activity 上下文内用真 heartbeat；脱离上下文（live 冒烟脚本）退化为 no-op。"""
    try:
        activity.info()
    except RuntimeError:
        return lambda payload: None
    return activity.heartbeat


# ---------------------------------------------------------------------------
# activity 核心入口与异步泵（注册表统一包 @activity.defn，本文件不自带）
# ---------------------------------------------------------------------------


async def run_deepseek_collection(
    item: CollectionTaskInput,
    *,
    session_factory: SessionFactory | None = None,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
    attempt: int = 1,
) -> CollectionTaskResult:
    """activity 核心：配置门 → mode 门 → to_thread 跑浏览器 → 墙/结果映射。

    与 activity 上下文解耦（heartbeat/attempt 注入），测试全程 mock 浏览器层。
    """
    if item.mode != "normal":
        raise ApplicationError(
            "deep_think not enabled in adapter v1",
            type="unsupported_mode",
            non_retryable=True,
        )
    if session_factory is None:
        session_factory = _PlaywrightDeepseekSession
    if heartbeat is None:
        heartbeat = _default_heartbeat()
    config = DeepseekAdapterConfig.from_env()
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    file_stem = f"{_safe_stem(item.business_key)}-a{attempt}"
    bound = log.bind(
        business_key=item.business_key,
        attempt=attempt,
        proxy=mask_proxy_url(config.proxy_url),
    )
    progress = {"stage": "browser_launch"}

    def _blocking() -> CollectedAnswer:
        assert session_factory is not None
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
        await bound.ainfo("deepseek_wall", wall_type=wall.wall_type, stage=progress["stage"])
        raise ApplicationError(
            f"{wall}{evidence}", type=wall.wall_type, non_retryable=True
        ) from wall
    except _IncompleteCapture as inc:
        evidence = f"; evidence={inc.evidence_path}" if inc.evidence_path else ""
        await bound.ainfo("deepseek_capture_incomplete", reason=str(inc), stage=progress["stage"])
        raise ApplicationError(f"{inc}{evidence}", type="answer_capture_incomplete") from inc
    await bound.ainfo(
        "deepseek_collect_ok",
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


class _PlaywrightDeepseekSession:
    """DeepSeek 网页采集的 sync Playwright 实现（persistent context，每次全新、结束即关）。"""

    def __init__(self, config: DeepseekAdapterConfig, evidence_dir: Path, file_stem: str) -> None:
        self._config = config
        self._evidence_dir = evidence_dir
        self._file_stem = file_stem

    def collect(self, query: str, on_stage: Callable[[str], None]) -> CollectedAnswer:
        # 延迟导入：模块加载不硬依赖浏览器驱动。驱动首选 patchright（旧链生产同款，
        # 反检测补丁版）；vanilla playwright 的 webdriver 指纹有风控静默吞发送前科
        # （豆包旧链 2026-07-15 live 实证），仅作开发兜底。
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
                    page.goto(_CHAT_URL, wait_until="load", timeout=_NAV_TIMEOUT_MS)
                page.wait_for_timeout(6_000)  # SPA + 未登录 /sign_in 跳转 settle（旧链同款）
                _try_close_overlays(page)
                if _detect_login_wall(page):
                    raise _WallError(
                        "wall_login_required",
                        "deepseek login wall detected right after navigation "
                        "(redirect to /sign_in)",
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

                # 异步验证码窗口：challenge 发送后才挂载（豆包旧链实测 ~2.2s），轮询至多 12s；
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
                    # SSE 捕获/解析失败时的 DOM 兜底（推理链剥离后取正文）
                    answer_text = _extract_response_text(page)
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
                        "but no completion stream fired within timeout — likely "
                        "content-filter or silent server-side drop",
                        self._shot(page, "no_stream"),
                    )
                if not meta.get("finished"):
                    raise _IncompleteCapture(
                        "stream-open-at-timeout: completion stream still open after "
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
    """CDP Network 层捕获 completion 事件流（doubao 同款机制的 DeepSeek 端点移植）。

    DeepSeek 走 fetch/XHR 流式（未经 SharedWorker 实证，但 CDP 层两种都看得到）。
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
                if any(h in url for h in _COMPLETION_URL_HINTS) and "event-stream" in (
                    resp.get("mimeType") or ""
                ):
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
# 页面交互助手（旧链 deepseek.py 移植 + doubao 同款机制）
# ---------------------------------------------------------------------------


def _detect_login_wall(page: Any) -> bool:
    """/sign_in 跳转（旧链 CONFIRMED 主信号）+ 登录模态/正文标志词兜底。"""
    try:
        url = page.url or ""
    except Exception:
        url = ""
    if _SIGN_IN_PATH in url:
        return True
    for sel in _LOGIN_WALL_HINTS:
        try:
            if page.locator(sel).first.is_visible(timeout=500):
                return True
        except Exception:
            continue
    try:
        body = page.locator("body").inner_text(timeout=1200)
    except Exception:
        body = ""
    return any(marker in (body or "") for marker in _SIGN_IN_TEXT_MARKERS)


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
    """轮询输入框可见（SPA 会在 domcontentloaded 后继续换 DOM）。"""
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
    """JS 打标发送按钮后经 Locator 点击（hover+mousedown+mouseup 完整事件链）。"""
    try:
        tagged = page.evaluate(_TAG_JS)
    except Exception:
        return False
    if not tagged:
        return False
    try:
        loc = page.locator('[data-geo-send="true"]').first
        loc.scroll_into_view_if_needed(timeout=2000)
        loc.click(timeout=4000, force=False)
        return True
    except Exception:
        return False


def _send_via_keyboard(page: Any, input_loc: Any) -> bool:
    """DeepSeek 回车即发送（换行为 Shift+Enter，旧链注释）；输入框清空即服务端已受理。"""
    for shortcut in ("Enter", "Control+Enter", "Meta+Enter"):
        try:
            page.keyboard.press(shortcut)
            page.wait_for_timeout(250)
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
    """输入框清空 = 提交被受理的 ground-truth 信号（豆包 live 实证同口径）。"""
    try:
        return str(input_loc.evaluate(_INPUT_VALUE_JS) or "").strip() == ""
    except Exception:
        return False


def _submit_and_confirm(
    page: Any, input_loc: Any, *, attempts: int = 2, settle_ms: int = 1600, poll_ms: int = 200
) -> dict[str, Any]:
    """发送并确认提交真正生效（DeepSeek 优先回车，按钮兜底；被吞时重试一次）。"""
    used = 0
    for i in range(max(1, attempts)):
        used = i + 1
        if not _send_via_keyboard(page, input_loc):
            _click_send_button(page)
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


def _extract_response_text(page: Any) -> str:
    """DOM 兜底抽取：助手气泡选择器取最后一个，剥离推理/搜索痕迹行。"""
    for sel in _ASSISTANT_SELECTORS:
        try:
            elements = page.locator(sel).all()
            if not elements:
                continue
            text = elements[-1].inner_text(timeout=2000)
            if text and text.strip():
                return _trim_response(_strip_reasoning_trace(text.strip()))
        except Exception:
            continue
    try:
        body = page.locator("body").inner_text(timeout=2000)
    except Exception:
        body = ""
    return _trim_response(_strip_reasoning_trace((body or "").strip()))


def _strip_reasoning_trace(text: str) -> str:
    """正文在最后一个推理/搜索痕迹行之后（"已思考(用时N秒)"/"搜索到N个网页"）。"""
    if not text:
        return text
    cut = -1
    for marker in _REASONING_TRACE_MARKERS:
        idx = text.rfind(marker)
        if idx > cut:
            cut = idx
    if cut < 0:
        return text.strip()
    tail = text[cut:]
    nl = tail.find("\n")
    return (tail[nl + 1 :] if nl >= 0 else "").strip() or text.strip()


def _trim_response(text: str) -> str:
    for marker in _TRAILING_NOISE_MARKERS:
        idx = text.find(marker)
        if 0 < idx < len(text):
            text = text[:idx].rstrip()
    return text.strip()


def _scan_dom_notices(page: Any) -> dict[str, list[str]]:
    """best-effort 读 body 文本扫系统通知词（限流 / 实名墙）。"""
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
# SSE 解析（DeepSeek completion 流 = JSON-patch 增量流，2026-07-27 live 校准；
# 解析不出即返回 None → DOM 兜底，绝不臆造正文）
# ---------------------------------------------------------------------------


def _rich_record_from_sse(body: str) -> dict[str, Any] | None:
    try:
        events = _parse_sse_events(body)
        return _assemble_deepseek_record(events)
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


def _parse_sse_events(body: str) -> list[dict[str, Any]]:
    """把原始 SSE body 切成 [{event, data, raw}]；data: [DONE] 跳过。"""
    if not body:
        return []
    out: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        block = block.strip("\r\n")
        if not block:
            continue
        ev_name: str | None = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev_name = line[len("event:") :].strip()
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
        out.append({"event": ev_name or "", "data": data, "raw": raw})
    return out


def _is_real_url(u: Any) -> bool:
    return isinstance(u, str) and (u.startswith("http://") or u.startswith("https://"))


def _walk_references(node: Any, sink: list[dict[str, Any]], seen_urls: set[str]) -> None:
    """递归找所有 "references" 数组里的 {url,...} 卡片（旧链 _references_native 口径，
    ⚠ 字段名 GUESS：url|link / title|name / site_name|sitename|source），按 URL 去重。"""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "references" and isinstance(value, list):
                for card in value:
                    if not isinstance(card, dict):
                        continue
                    url = card.get("url") or card.get("link") or ""
                    if not _is_real_url(url):
                        continue
                    dedup = str(url).split("?")[0]
                    if dedup in seen_urls:
                        continue
                    seen_urls.add(dedup)
                    sink.append(
                        {
                            "url": url,
                            "title": card.get("title") or card.get("name"),
                            "sitename": (
                                card.get("site_name") or card.get("sitename") or card.get("source")
                            ),
                            "summary": card.get("snippet") or card.get("summary"),
                        }
                    )
            else:
                _walk_references(value, sink, seen_urls)
    elif isinstance(node, list):
        for item in node:
            _walk_references(item, sink, seen_urls)


def _walk_snapshot_fragments(node: Any, sink: list[str]) -> None:
    """初始快照 {"v":{"response":{...,"fragments":[...]}}} 里取 type=="RESPONSE" 的
    fragment.content 正文碎片（THINK/SEARCH 等痕迹碎片不进正文）。"""
    if isinstance(node, dict):
        fragments = node.get("fragments")
        if isinstance(fragments, list):
            for frag in fragments:
                if not isinstance(frag, dict):
                    continue
                if frag.get("type") != "RESPONSE":
                    continue
                content = frag.get("content")
                if isinstance(content, str) and content:
                    sink.append(content)
        for value in node.values():
            _walk_snapshot_fragments(value, sink)
    elif isinstance(node, list):
        for item in node:
            _walk_snapshot_fragments(item, sink)


def _collect_event_text(data: Any, sink: list[str]) -> None:
    """按 live 实测 schema（2026-07-27 校准）收集单个 SSE 事件的正文增量：

    - ``{"v":{"response":{...fragments...}}}`` 初始快照 → fragments[type=RESPONSE].content；
    - ``{"p":"response/fragments/-1/content","o":"APPEND","v":"..."}`` patch 追加；
    - ``{"v":"..."}`` 裸增量（无 p/o 单键——实测主流式形态；首版只认 APPEND 导致
      整流只抽到 patch 形式的 "！" 一个字符，answer_len=1 的根因）；
    - ``{"o":"SET"/"BATCH",...}`` 状态/批处理 op 一律跳过（防 "FINISHED" 混入正文）。
    """
    if not isinstance(data, dict):
        return
    v = data.get("v")
    if isinstance(v, dict):
        _walk_snapshot_fragments(v, sink)
        return
    if not (isinstance(v, str) and v):
        return
    o = data.get("o")
    p = data.get("p") or ""
    if o == "APPEND":
        if not p or p.endswith("/content"):
            sink.append(v)
    elif o is None and not p:
        sink.append(v)


def _assemble_deepseek_record(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """把 SSE 事件序列组装成 answer_text + references；零正文 → None（→ DOM 兜底）。"""
    parts: list[str] = []
    references: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for ev in events:
        data = ev.get("data")
        if data is None:
            continue
        _collect_event_text(data, parts)
        _walk_references(data, references, seen_urls)
    answer_text = _recover_mojibake("".join(parts)).strip()
    if not answer_text:
        return None
    return {"answer_text": answer_text, "references": references}
