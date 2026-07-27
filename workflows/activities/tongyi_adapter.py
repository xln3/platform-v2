"""通义千问网页采集适配器 v1（tongyi.aliyun.com / www.tongyi.com）。

按用户直接指令实现（ADR-0003 五平台上线）。结构照 ``doubao_adapter.py``：
配置门 → mode 门 → to_thread 跑 sync 浏览器 → 墙/结果映射。与 doubao 的差异：

- 公开表面不注册 activity（无 ``@activity.defn``）：只暴露
  ``TongyiAdapterConfig.from_env()`` 与 ``run_tongyi_collection()``，
  由 workers 侧按 ``GEO_COLLECTION_ADAPTER=tongyi`` 门控自行包装注册（S04 集成）。
- 成功判据的「流式结束」两路取一：CDP Network 捕获到 ``text/event-stream``
  且 ``loadingFinished``（主路），或助手气泡 DOM 文本静默 settle 且停止按钮消失
  （兜底——通义部分会话走 WebSocket 时 CDP 看不到 event-stream）。正文抽取一律
  走渲染后 DOM（零合成：只取页面真实渲染文本，不解析/不臆造 SSE 协议格式）。

v1 边界：

- 仅 ``mode='normal'``；``mode='deep_think'`` →
  ``ApplicationError(..., type="unsupported_mode", non_retryable=True)``。
- 配置全走 env（秘密绝不进 task payload）：
  ``GEO_TONGYI_PROFILE_DIR``（必填，persistent profile 目录；缺失/不存在 →
  ``adapter_not_configured`` non_retryable）；``GEO_TONGYI_PROXY_URL``（可选，
  http://user:pass@host:port——日志只出现打码后的 scheme://host:port）；
  ``GEO_ADAPTER_EVIDENCE_DIR``（证据目录，默认
  ``platform-v2/runtime/adapter-evidence/tongyi/``，自动建目录）；
  ``GEO_TONGYI_HEADLESS``（默认 1；0=headed 需 DISPLAY）。
- 执行模型：sync 浏览器驱动包在 ``asyncio.to_thread`` 里跑；协程侧每 10s 泵一次
  heartbeat（workflow heartbeat_timeout=30s，泵频 ≤15s 硬约束）。
- 浏览器驱动 patchright（生产同款反检测补丁版）；vanilla playwright 仅开发兜底。
- 墙分类（先截屏存证再抛，错误 message 带证据路径、绝不含秘密）：
  登录墙/实名墙 → ``wall_login_required`` non_retryable；验证码（阿里滑块等）→
  ``wall_captcha`` non_retryable；发送墙/限流 → ``wall_send`` non_retryable。
- 成功判据（零合成）：提交被接受（输入框清空）且流式结束（CDP finished 或
  DOM 静默兜底成立）且正文非空且不含墙特征——缺一都不得返回成功。
  流截断/空答案/无流且 DOM 不静默 → ``answer_capture_incomplete``（可重试的诚实失败）。

选择器校准记录（全部 headed patchright + 北京租约代理 live 实测）：

- 2026-07-27 站点拓扑：tongyi.aliyun.com 现为「通义实验室」营销落地页（无聊天
  功能）；聊天应用入口 www.tongyi.com 301 → www.qianwen.com（title
  「千问-阿里 AI 助手」），故 ``_CHAT_URL`` 取 www.tongyi.com、
  ``_FALLBACK_URL`` 取 www.qianwen.com。
- 2026-07-27 输入框：``div[contenteditable="true"]``；**空 composer 的
  textContent = ``\\ufeff向千问提问``**（占位符以真实文本节点实现）——
  ``_composer_cleared`` 必须识别占位符，否则已成功的发送被误判为
  send-not-accepted（冒烟第一轮实证）。Enter 即发送。
- 2026-07-27 登录/墙：未登录可游客聊天（基础问答不需要账号）；登录表单在
  ``passport.qianwen.com/havanaone`` iframe（#fm-sms-login-id / #fm-smscode /
  #fm-agreement-checkbox），点「获取验证码」先弹「确认登录」协议 modal。
- 2026-07-27 回答气泡：正文 markdown 根 = ``div.qk-markdown``（流式收尾后追加
  ``qk-markdown-complete`` 类——DOM 完成信号），外层卡片
  ``div.answer-common-card``；class 链经探针 dump 实证。
- 2026-07-27 live 冒烟通过：游客通道真实查询「你好，请用一句话介绍你自己」→
  真实回答 33 字，quality_state=live_valid（证据
  runtime/adapter-evidence/tongyi/tongyi-smoke-live-1-a1.png）。
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

ENV_PROFILE_DIR = "GEO_TONGYI_PROFILE_DIR"
ENV_PROXY_URL = "GEO_TONGYI_PROXY_URL"
ENV_EVIDENCE_DIR = "GEO_ADAPTER_EVIDENCE_DIR"
ENV_HEADLESS = "GEO_TONGYI_HEADLESS"

_DEFAULT_EVIDENCE_DIR = (
    Path(__file__).resolve().parents[2] / "runtime" / "adapter-evidence" / "tongyi"
)
_HEARTBEAT_INTERVAL_S = 10.0  # workflow heartbeat_timeout=30s，泵频 ≤15s 硬约束
_NAV_TIMEOUT_MS = 25_000
_CHAT_TIMEOUT_S = 120.0  # normal 模式流式完成预算（workflow 总预算 5 分钟）

_CHAT_URL = "https://www.tongyi.com/"
_FALLBACK_URL = "https://www.qianwen.com/"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 聊天输入框（2026-07-27 live 校准：qianwen.com 输入区是 contenteditable 富文本 div，
# placeholder 为兄弟 span「向千问提问」；textarea 仅为兜底）
_INPUT_SELECTORS: tuple[str, ...] = (
    'div[contenteditable="true"]',
    'textarea[placeholder*="输入"]',
    'textarea[placeholder*="问"]',
    "textarea",
    '[class*="chat-input"] textarea',
    '[class*="editor"][contenteditable="true"]',
)

# 助手回答气泡（最后一个为准）。2026-07-27 live 校准（探针 dump class 链）：
# 正文 markdown 根 = div.qk-markdown（流式结束后追加 qk-markdown-complete 类），
# 外层卡片 = div.answer-common-card；前两条为实测精确选择器，其余为兜底猜测。
_ASSISTANT_SELECTORS: tuple[str, ...] = (
    ".qk-markdown",
    ".answer-common-card .qk-markdown",
    '[class*="markdown-body"]',
    '[class*="answer"][class*="content"]',
    '[class*="message"] [class*="markdown"]',
    '[class*="bubble"]',
)

# 流式完成 DOM 标记（2026-07-27 实测：回答渲染完毕时 markdown 根获得 complete 类）
_ANSWER_COMPLETE_SELECTOR = ".qk-markdown-complete"

# 阻断交互的登录模态 / 登录页特征（2026-07-27 探针实测：登录表单在
# passport.qianwen.com/havanaone iframe 内，含手机号/验证码输入与协议复选框）
_LOGIN_WALL_HINTS: tuple[str, ...] = (
    'iframe[src*="passport.qianwen.com"]',
    'iframe[src*="havanaone"]',
    'div[role="dialog"]:has-text("扫码登录")',
    'div[role="dialog"]:has-text("手机号登录")',
    'div[role="dialog"]:has-text("登录")',
    'div[class*="login-modal"]:visible',
    'div[class*="LoginModal"]:visible',
    'iframe[src*="login"]',
)

# 登录墙文本特征（未登录时发送/访问出现）
_LOGIN_TEXT_MARKERS: tuple[str, ...] = (
    "登录后使用",
    "登录后继续",
    "立即登录",
    "请登录",
    "登录以继续",
)

# 验证码组件（阿里滑块 / 智能验证通用特征）
_CAPTCHA_SELECTORS: tuple[str, ...] = (
    'iframe[src*="captcha"]',
    'iframe[src*="verify"]',
    "#nc_1_wrapper",
    'div[class*="nc_wrapper"]',
    '[id*="aliyunCaptcha"]',
    'div[class*="captcha"]:visible',
    ".slidetounlock",
)

# 「停止生成」按钮：流式进行中的信号，消失+文本静默 = DOM 兜底完成信号
_STOP_GENERATING_SELECTORS: tuple[str, ...] = (
    'button:has-text("停止")',
    '[class*="stop"]:visible',
    '[aria-label*="停止"]',
)

# DOM 层系统通知词表（限流 / 实名墙）——命中判定 gated by not has_answer
_SOFTBAN_DOM_PHRASES: tuple[str, ...] = (
    "今日请求过频",
    "请求过于频繁",
    "请求太频繁",
    "操作过于频繁",
    "发送频率过高",
    "请求频率过高",
    "当前请求人数过多",
    "今日对话次数已达",
)
_REALNAME_DOM_PHRASES: tuple[str, ...] = (
    "完成实名认证",
    "请先实名",
    "实名认证后才能",
    "未实名认证",
    "进行实名认证",
    "实名验证",
)

# DOM 抽取后裁剪尾部 UI 噪声（建议追问 / 工具栏文案；勿收常见词避免误裁正文）
_TRAILING_NOISE_MARKERS: tuple[str, ...] = (
    "继续追问",
    "相关问题",
    "你可能想问",
    "换个话题",
    "重新生成",
)

# JS：在输入框右侧/下方找方形带 svg/img 的发送按钮并打 data 标记。
# 2026-07-27 live 校准：排除工具栏伪候选（更多/关闭/语音/附件），aria-label 含「发送」加分。
_TAG_JS = """() => {
    document.querySelectorAll('[data-proxyllm-send]').forEach(
        e => e.removeAttribute('data-proxyllm-send'));
    const ta = document.querySelector('textarea')
        || document.querySelector('div[contenteditable="true"]');
    if (!ta) return false;
    const tar = ta.getBoundingClientRect();
    const EXCLUDE = /更多|关闭|语音|附件|加号|新建/;
    const cands = Array.from(
        document.querySelectorAll('button, [role="button"], [class*="send"]'));
    const scored = [];
    for (const el of cands) {
        if (el.disabled) continue;
        if (el.offsetParent === null) continue;
        const aria = el.getAttribute('aria-label') || '';
        if (EXCLUDE.test(aria)) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 20 || r.height < 20) continue;
        if (r.width > 96 || r.height > 96) continue;
        const ratio = Math.min(r.width, r.height) / Math.max(r.width, r.height);
        if (ratio < 0.5) continue;
        if (!el.querySelector('svg, img, i')) continue;
        if (r.x < tar.x + tar.width * 0.5) continue;
        if (r.y < tar.y - 40) continue;
        if (r.y > tar.y + tar.height + 240) continue;
        let score = r.x * 1000 + r.y;
        if (aria.includes('发送')) score += 10000000;
        scored.push({el, score});
    }
    scored.sort((a, b) => b.score - a.score);
    if (scored.length === 0) return false;
    scored[0].el.setAttribute('data-proxyllm-send', 'true');
    return true;
}"""

_INPUT_VALUE_JS = (
    "el => (el.value !== undefined && el.value !== null) ? el.value : (el.textContent || '')"
)

# 整页截图前把内部 overflow 滚动容器压平进文档流（与 doubao 同款）
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
class TongyiAdapterConfig:
    """env 配置。proxy_url 原文只在启动浏览器时使用，绝不落日志/payload。"""

    profile_dir: Path
    proxy_url: str | None
    evidence_dir: Path
    headless: bool

    @classmethod
    def from_env(cls) -> TongyiAdapterConfig:
        raw_profile = os.environ.get(ENV_PROFILE_DIR, "").strip()
        if not raw_profile:
            raise ApplicationError(
                f"{ENV_PROFILE_DIR} is not set — tongyi adapter requires a persistent "
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


SessionFactory = Callable[[TongyiAdapterConfig, Path, str], _BrowserSession]


# ---------------------------------------------------------------------------
# 异步入口与心跳泵
# ---------------------------------------------------------------------------


async def run_tongyi_collection(
    item: CollectionTaskInput,
    *,
    session_factory: SessionFactory | None = None,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
    attempt: int = 1,
) -> CollectionTaskResult:
    """activity 核心：配置门 → mode 门 → to_thread 跑浏览器 → 墙/结果映射。

    与 activity 上下文解耦（heartbeat/attempt 注入），测试全程 mock 浏览器层。
    不自行注册 ``@activity.defn``——workers 侧按 GEO_COLLECTION_ADAPTER 门控包装。
    """
    factory: SessionFactory = session_factory or _PlaywrightTongyiSession
    hb = heartbeat if heartbeat is not None else (lambda payload: None)
    if item.mode != "normal":
        raise ApplicationError(
            "deep_think not enabled in adapter v1",
            type="unsupported_mode",
            non_retryable=True,
        )
    config = TongyiAdapterConfig.from_env()
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
        hb({"business_key": item.business_key, "stage": progress["stage"]})
        done, _pending = await asyncio.wait({thread}, timeout=_HEARTBEAT_INTERVAL_S)
        if done:
            break
    try:
        collected = thread.result()
    except _WallError as wall:
        evidence = f"; evidence={wall.evidence_path}" if wall.evidence_path else ""
        await bound.ainfo("tongyi_wall", wall_type=wall.wall_type, stage=progress["stage"])
        raise ApplicationError(
            f"{wall}{evidence}", type=wall.wall_type, non_retryable=True
        ) from wall
    except _IncompleteCapture as inc:
        evidence = f"; evidence={inc.evidence_path}" if inc.evidence_path else ""
        await bound.ainfo("tongyi_capture_incomplete", reason=str(inc), stage=progress["stage"])
        raise ApplicationError(f"{inc}{evidence}", type="answer_capture_incomplete") from inc
    await bound.ainfo(
        "tongyi_collect_ok",
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
    """正文 + 参考来源追加段（与 doubao 同口径）。"""
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


class _PlaywrightTongyiSession:
    """通义网页采集的 sync Playwright 实现（persistent context，每次全新、结束即关）。"""

    def __init__(self, config: TongyiAdapterConfig, evidence_dir: Path, file_stem: str) -> None:
        self._config = config
        self._evidence_dir = evidence_dir
        self._file_stem = file_stem

    def collect(self, query: str, on_stage: Callable[[str], None]) -> CollectedAnswer:
        # 延迟导入：模块加载不硬依赖浏览器驱动。驱动首选 patchright（生产同款反检测
        # 补丁版）；vanilla playwright 仅开发兜底。
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
                capture = _EventStreamCapture(context, page)

                on_stage("navigate")
                try:
                    page.goto(_CHAT_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
                except PWTimeout:
                    page.goto(_FALLBACK_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
                page.wait_for_timeout(6_000)  # SPA + 反爬 JS 挂载 settle
                _try_close_overlays(page)
                if _detect_login_wall(page):
                    raise _WallError(
                        "wall_login_required",
                        "tongyi login wall detected right after navigation",
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
                    # 输入框未清空：未登录拦截 or 发送被吞——先看登录墙再定发送墙
                    if _detect_login_wall(page):
                        raise _WallError(
                            "wall_login_required",
                            "login wall surfaced on send (submission blocked by login)",
                            self._shot(page, "login"),
                        )
                    raise _WallError(
                        "wall_send",
                        "send-not-accepted: composer still populated after "
                        f"{submit.get('attempts', '?')} send attempts (submission swallowed)",
                        self._shot(page, "send_wall"),
                    )
                on_stage("submitted")

                # 异步验证码窗口：challenge 发送后延迟挂载，轮询至多 12s；
                # 流已开始且过 3.5s settle 窗即快走
                challenge_start = time.monotonic()
                while time.monotonic() < challenge_start + 12.0:
                    hit = _captcha_hit(page)
                    if hit:
                        raise _WallError(
                            "wall_captcha",
                            f"captcha challenge appeared post-send ({hit})",
                            self._shot(page, "captcha"),
                        )
                    if _detect_login_wall(page):
                        raise _WallError(
                            "wall_login_required",
                            "login wall surfaced post-send",
                            self._shot(page, "login"),
                        )
                    if capture.has_stream_started() and time.monotonic() - challenge_start >= 3.5:
                        break
                    page.wait_for_timeout(500)

                on_stage("await_stream")
                meta = capture.wait_finish(
                    page, appearance_timeout_s=20.0, timeout_s=_CHAT_TIMEOUT_S
                )
                stream_finished = bool(meta.get("found") and meta.get("finished"))
                if meta.get("found") and not meta.get("finished"):
                    raise _IncompleteCapture(
                        "stream-open-at-timeout: event-stream still open after budget "
                        f"({meta.get('bytes_received', 0)} bytes captured) — answer "
                        "would be truncated; failing honestly",
                        self._shot(page, "truncated"),
                    )
                if not stream_finished:
                    # CDP 看不到流（WebSocket 通道等）→ DOM 静默兜底：停止按钮消失
                    # 且助手文本 quiet_s 内不再增长
                    meta["dom_quiet"] = _wait_dom_quiet(
                        page, quiet_s=2.5, timeout_s=_CHAT_TIMEOUT_S
                    )
                    if not meta["dom_quiet"].get("quiet"):
                        raise _IncompleteCapture(
                            "no-stream-and-dom-not-quiet: neither CDP event-stream nor "
                            "DOM stability confirmed completion — failing honestly",
                            self._shot(page, "no_stream"),
                        )

                on_stage("answer_extract")
                answer_text, references = _extract_response(page)
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
                    if _detect_login_wall(page):
                        raise _WallError(
                            "wall_login_required",
                            "login wall detected after completed stream (empty answer)",
                            self._shot(page, "login"),
                        )
                    raise _IncompleteCapture(
                        "answer-empty-after-finished-stream: DOM extraction produced no "
                        "answer text after confirmed stream completion",
                        self._shot(page, "empty_answer"),
                    )
                if any(marker in answer_text for marker in _LOGIN_TEXT_MARKERS):
                    raise _WallError(
                        "wall_login_required",
                        "login-required text marker inside extracted answer",
                        self._shot(page, "login"),
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
                    meta={"stream": meta, "driver": driver},
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


class _EventStreamCapture:
    """CDP Network 层捕获 text/event-stream 响应（不绑定特定 URL——通义接口路径
    以 live 校准为准，v1 只把流当「完成信号 + 字节计数」，正文抽取走渲染后 DOM）。
    """

    def __init__(self, context: Any, page: Any) -> None:
        self._cdp = context.new_cdp_session(page)
        self._cdp.send("Network.enable")
        self._stream_request_ids: list[str] = []
        self._loading_finished: set[str] = set()
        self._loading_failed: set[str] = set()
        self._bytes: dict[str, int] = {}
        for name in (
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
            if name == "Network.responseReceived":
                resp = payload.get("response") or {}
                if "event-stream" in (resp.get("mimeType") or ""):
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
        dom_settle_s: float = 2.0,
    ) -> dict[str, Any]:
        """两段等待：先等流出现，再等 loadingFinished/Failed，最后 DOM settle。"""
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
                page.wait_for_timeout(int(dom_settle_s * 1000))
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
# 页面交互助手
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


def _stop_button_visible(page: Any) -> bool:
    for sel in _STOP_GENERATING_SELECTORS:
        try:
            if page.locator(sel).first.is_visible(timeout=250):
                return True
        except Exception:
            continue
    return False


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
    """轮询输入框可见。"""
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
        loc = page.locator('[data-proxyllm-send="true"]').first
        loc.scroll_into_view_if_needed(timeout=2000)
        loc.click(timeout=4000, force=False)
        return True
    except Exception:
        return False


def _send_via_keyboard(page: Any, input_loc: Any) -> bool:
    """Enter / Control+Enter / Meta+Enter 逐个试，输入框清空即服务端已受理。

    qianwen 实测 Enter 即发送（2026-07-27），故 Enter 排最前。
    """
    for shortcut in ("Enter", "Control+Enter", "Meta+Enter"):
        try:
            page.keyboard.press(shortcut)
            page.wait_for_timeout(200)
        except Exception:
            continue
        if _composer_cleared(page, input_loc):
            return True
    return False


# qianwen 空 composer 的 textContent = "\\ufeff向千问提问"（占位符以真实文本节点实现，
# 2026-07-27 live 探针实测）——清空判定必须识别占位符与 BOM
_COMPOSER_PLACEHOLDERS: tuple[str, ...] = ("向千问提问",)


def _composer_value_empty(raw: Any) -> bool:
    text = str(raw or "").replace("\ufeff", "").strip()
    return text == "" or text in _COMPOSER_PLACEHOLDERS


def _composer_cleared(page: Any, input_loc: Any) -> bool:
    """输入框清空 = 提交被受理的 ground-truth 信号。

    qianwen 发送成功后 composer 重渲染、占位符恢复为「\\ufeff向千问提问」
    （2026-07-27 live 实测）——空值/占位符都算已受理；旧节点失效时按当前可见
    输入框判定；输入框整体消失（进入对话视图）也视为已受理。
    """
    try:
        connected = bool(input_loc.evaluate("el => el.isConnected"))
    except Exception:
        connected = False
    if connected:
        try:
            return _composer_value_empty(input_loc.evaluate(_INPUT_VALUE_JS))
        except Exception:
            return False
    for sel in _INPUT_SELECTORS:
        try:
            cur = page.locator(sel).first
            if cur.is_visible(timeout=300):
                return _composer_value_empty(cur.evaluate(_INPUT_VALUE_JS))
        except Exception:
            continue
    return True


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
            if _composer_cleared(page, input_loc):
                return {"submitted": True, "attempts": used}
        if used < attempts:
            try:
                input_loc.click()
                page.wait_for_timeout(200)
            except Exception:
                pass
    return {"submitted": False, "attempts": used}


def _wait_dom_quiet(page: Any, *, quiet_s: float, timeout_s: float) -> dict[str, Any]:
    """DOM 兜底完成信号：停止按钮消失且文本静默 quiet_s；qk-markdown-complete
    类出现（2026-07-27 实测的流式收尾标记）即快速判定完成。"""
    t0 = time.monotonic()
    last_text: str | None = None
    last_change = time.monotonic()
    saw_text = False
    while time.monotonic() < t0 + timeout_s:
        text, _refs = _extract_response(page)
        if text and text != last_text:
            saw_text = True
            last_text = text
            last_change = time.monotonic()
        if _stop_button_visible(page):
            last_change = time.monotonic()  # 停止按钮在 = 还在流式，重置静默计时
        complete_marker = False
        try:
            complete_marker = page.locator(_ANSWER_COMPLETE_SELECTOR).first.is_visible(timeout=250)
        except Exception:
            complete_marker = False
        if saw_text and (complete_marker or time.monotonic() - last_change >= quiet_s):
            return {
                "quiet": True,
                "complete_marker": complete_marker,
                "answer_len": len(last_text or ""),
                "elapsed_ms": int((time.monotonic() - t0) * 1000),
            }
        page.wait_for_timeout(400)
    return {
        "quiet": False,
        "answer_len": len(last_text or ""),
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
    }


def _extract_response(page: Any) -> tuple[str, list[dict[str, Any]]]:
    """DOM 抽取：最后一个助手气泡正文 + best-effort 引用链接（a[href^=http]）。"""
    for sel in _ASSISTANT_SELECTORS:
        try:
            elements = page.locator(sel).all()
            if not elements:
                continue
            last = elements[-1]
            text = last.inner_text(timeout=2000)
            if text and text.strip():
                refs: list[dict[str, Any]] = []
                seen: set[str] = set()
                try:
                    anchors = last.locator('a[href^="http"]').all()
                    for a in anchors:
                        href = a.get_attribute("href") or ""
                        if not href or href in seen:
                            continue
                        seen.add(href)
                        refs.append(
                            {
                                "url": href,
                                "title": (a.inner_text(timeout=500) or "").strip() or None,
                                "sitename": None,
                            }
                        )
                except Exception:
                    refs = []
                return _trim_response(text.strip()), refs
        except Exception:
            continue
    return "", []


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
