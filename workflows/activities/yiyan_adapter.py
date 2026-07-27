"""文心一言（yiyan.baidu.com）网页采集适配器 v1。

20260727 按 ADR-0003（五平台同日上线）全新实现——旧系统没有文心一言适配代码，
无可移植资产；结构严格对齐 ``workflows/activities/doubao_adapter.py``（同一公开表面、
同一执行模型、同一墙分类与零合成纪律）。

选择器校准状态（20260727 live 校准完毕，headed patchright + 上海代理 + 真页面）：

- composer = ``textarea.ci-textarea.ci-scroll-style``（placeholder 为轮换热点话题）；
  发送按钮 = ``span.ci-submit-button``（img 图标，无 svg）；答案容器 =
  ``div.conversation-flow-answer-container``（最后一个为本轮答案，流式增长后稳定），
  正文只取其内 ``chat-search-answer-generate`` 子块（``answer-ask-container``
  建议 chips / ``answer-tips-wrapper`` 工具栏是尾部噪声，已剔除）；
  「生成中」瞬态指示器 = ``[class*="markdown-loading"]`` / ``[class*="thinking-loading"]``
  （流式期间可见、结束即消失）；百度 pass 登录弹层 = ``#TANGRAM__PSP_11__*`` 短信
  表单 + ``span.switch-item`` tab。校准脚本与截图证据在 ``platform-v2/runtime/``
  （gitignored）。
- 流传输实况：页面装 Service Worker（``wenxin.baidu.com/sw.js``），completion
  请求被 SW 中转，CDP Network 层抓不到事件流（probe_transport 实证）——流信号
  用 DOM 观测：答案容器出现=流开始；瞬态指示器消失且正文连续 2.5s 不变=流结束。
  零合成：正文即 DOM 实渲染文本，绝不解析/猜测未公开协议。
- yiyan.baidu.com 落地跳转到 wenxin.baidu.com（实测）；未登录可答首问（实测），
  登录墙在后续交互/刷新时出现。
- 参考来源：DOM best-effort 抽取（`参考`/`来源` 容器内锚点），抽不到即空列表——
  不合成。

v1 边界：

- 仅 ``mode='normal'``；``mode='deep_think'`` →
  ``ApplicationError(..., type="unsupported_mode", non_retryable=True)``。
- 配置全走 env（秘密绝不进 task payload）：
  ``GEO_YIYAN_PROFILE_DIR``（必填，persistent profile 目录；缺失/不存在 →
  ``adapter_not_configured`` non_retryable）；``GEO_YIYAN_PROXY_URL``（可选，
  形如 http://user:pass@host:port——日志只出现打码后的 scheme://host:port）；
  ``GEO_ADAPTER_EVIDENCE_DIR``（截图目录，全适配器共享 env，默认
  ``platform-v2/runtime/adapter-evidence/yiyan/``，自动建目录）；
  ``GEO_YIYAN_HEADLESS``（默认 1 headless；0=headed 需 DISPLAY）。
- 执行模型：sync 浏览器驱动（patchright 首选，vanilla playwright 兜底）包在
  ``asyncio.to_thread`` 里跑；activity 协程侧每 10s 泵一次 heartbeat。
- 墙分类（先截屏存证再抛，错误 message 带证据路径、绝不含秘密）：
  登录墙/实名墙 → ``wall_login_required`` non_retryable；验证码 → ``wall_captcha``
  non_retryable；发送墙/限流 → ``wall_send`` non_retryable。
- 成功判据（零合成）：提交被接受（输入框清空）且答案容器出现且「生成中」
  指示器消失、正文静默稳定且非空且不含墙特征——缺一都不得返回成功。
  流截断/空答案/无流 → ``answer_capture_incomplete``（可重试的诚实失败）。
- 已知未了：登录开户未完成——开户短信百度侧已发出（countdown 实证），但 OTP
  收件链路（末端 SmsForwarder 手机）自 20260725 16:28 起无推送（
  ``server/results/otp_inbox/_rawpush.jsonl``），收件箱无新条目；未登录态首问
  可采集（live 实证），登录后多轮/历史同步未验证。
"""

from __future__ import annotations

import asyncio
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

ENV_PROFILE_DIR = "GEO_YIYAN_PROFILE_DIR"
ENV_PROXY_URL = "GEO_YIYAN_PROXY_URL"
ENV_EVIDENCE_DIR = "GEO_ADAPTER_EVIDENCE_DIR"  # 全适配器共享
ENV_HEADLESS = "GEO_YIYAN_HEADLESS"

_DEFAULT_EVIDENCE_DIR = (
    Path(__file__).resolve().parents[2] / "runtime" / "adapter-evidence" / "yiyan"
)
_HEARTBEAT_INTERVAL_S = 10.0  # workflow heartbeat_timeout=30s，泵频 ≤15s 硬约束
_NAV_TIMEOUT_MS = 25_000
_CHAT_TIMEOUT_S = 120.0  # normal 模式流式完成预算（workflow 总预算 5 分钟）

_CHAT_URL = "https://yiyan.baidu.com/"

# 旧链 doubao 实测 UA / locale / 时区，同款沿用
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 聊天输入框候选梯度（20260727 live 校准：yiyan.baidu.com 未登录首页实测
# composer = textarea.ci-textarea.ci-scroll-style，placeholder 为轮换热点话题；
# 校准脚本 platform-v2/runtime/yiyan_calibrate.py，证据 runtime/adapter-evidence/yiyan/）
_INPUT_SELECTORS: tuple[str, ...] = (
    "textarea.ci-textarea",
    'div[contenteditable="true"][data-placeholder]',
    'div[contenteditable="true"]',
    'textarea[placeholder*="问"]',
    "textarea",
)

# 助手正文候选（最后一个为准；20260727 live 校准：答案容器 =
# div.conversation-flow-answer-container，其内 chat-search-answer-generate 为纯正文，
# answer-ask-container 是建议 chips、answer-tips-wrapper 是工具栏——必须只取
# generate 块，否则正文尾部带 chips/「深度思考」噪声；runtime/yiyan_probe_tree.py 实证）
_ASSISTANT_SELECTORS: tuple[str, ...] = (
    "div.conversation-flow-answer-container div.chat-search-answer-generate",
    "div.chat-search-answer-generate",
    "div.conversation-flow-answer-container",
    '[class*="answer-container"]',
    '[class*="markdown-body"]',
)

# 「生成中」瞬态指示器（20260727 live 校准：流式输出期间可见、结束即消失；
# chat-search-answer-generate* 常驻容器不是瞬态信号，勿用）
_LOADING_HINTS: tuple[str, ...] = (
    '[class*="markdown-loading"]:visible',
    '[class*="thinking-loading"]:visible',
)

# 阻断交互的登录模态（20260727 live 校准：百度 pass 登录弹层实测特征——
# TANGRAM 短信表单 ID / 短信登录 tab / 扫码标题；证据 login-1-sms-tab.png）
_LOGIN_WALL_HINTS: tuple[str, ...] = (
    "#TANGRAM__PSP_11__smsPhone:visible",
    "#TANGRAM__PSP_11__userName:visible",
    'span.switch-item:has-text("短信登录")',
    'p.tang-pass-qrcode-title:has-text("扫码登录")',
    'div[role="dialog"]:has-text("扫码登录")',
    'div[class*="login-modal"]:visible',
    'iframe[src*="passport.baidu.com"]:visible',
)

# 验证码组件（百度系：pass 滑块/点选 + 通用词表）
_CAPTCHA_SELECTORS: tuple[str, ...] = (
    'iframe[src*="captcha"]',
    'iframe[src*="verify"]',
    'div[class*="captcha"]:visible',
    'div[id*="vcode"]:visible',
    'div[class*="vcode"]:visible',
    'div[class*="pass-slide"]:visible',
    'div[class*="verify-wrap"]:visible',
)

# DOM 层系统通知词表（限流 / 实名墙）——命中判定 gated by not has_answer，
# 出了真答案的运行绝不误判
_SOFTBAN_DOM_PHRASES: tuple[str, ...] = (
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

# DOM 兜底抽取后裁剪尾部 UI 噪声（工具栏/建议 chips；20260727 live 实测「深度思考」
# 按钮文本会挂在 generate 块正文尾部）
_TRAILING_NOISE_MARKERS: tuple[str, ...] = (
    "深度思考",
    "继续追问",
    "换个话题",
    "重新生成",
    "向我提问",
    "发送给文心一言",
)

# JS：在输入框右侧/下方找方形发送按钮并打 data 标记（旧链 _TAG_JS 泛化版，
# 锚定第一个可见的 textarea/contenteditable；svg 或 img 图标均认——文心实测发送
# 按钮是 span.ci-submit-button > img，20260727 校准）
_TAG_JS = """() => {
    document.querySelectorAll('[data-yiyan-send]').forEach(
        e => e.removeAttribute('data-yiyan-send'));
    const direct = document.querySelector('span.ci-submit-button');
    if (direct && direct.offsetParent !== null) {
        direct.setAttribute('data-yiyan-send', 'true');
        return true;
    }
    let ta = null;
    for (const el of document.querySelectorAll(
        'textarea, div[contenteditable="true"]')) {
        if (el.offsetParent !== null) { ta = el; break; }
    }
    if (!ta) return false;
    const tar = ta.getBoundingClientRect();
    const cands = Array.from(document.querySelectorAll(
        'button, [role="button"], span, div'));
    const scored = [];
    for (const el of cands) {
        if (el.disabled) continue;
        if (el.offsetParent === null) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 24 || r.height < 24) continue;
        if (r.width > 80 || r.height > 80) continue;
        const ratio = Math.min(r.width, r.height) / Math.max(r.width, r.height);
        if (ratio < 0.7) continue;
        if (!el.querySelector('svg') && !el.querySelector('img')) continue;
        if (r.x < tar.x + tar.width * 0.6) continue;
        if (r.y < tar.y - 30) continue;
        if (r.y > tar.y + tar.height + 240) continue;
        scored.push({el, score: r.x * 1000 + r.y});
    }
    scored.sort((a, b) => b.score - a.score);
    if (scored.length === 0) return false;
    scored[0].el.setAttribute('data-yiyan-send', 'true');
    return true;
}"""

_INPUT_VALUE_JS = (
    "el => (el.value !== undefined && el.value !== null && el.value !== '') "
    "? el.value : (el.textContent || '')"
)


# ---------------------------------------------------------------------------
# 配置 / 错误类型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class YiyanAdapterConfig:
    """env 配置。proxy_url 原文只在启动浏览器时使用，绝不落日志/payload。"""

    profile_dir: Path
    proxy_url: str | None
    evidence_dir: Path
    headless: bool

    @classmethod
    def from_env(cls) -> YiyanAdapterConfig:
        raw_profile = os.environ.get(ENV_PROFILE_DIR, "").strip()
        if not raw_profile:
            raise ApplicationError(
                f"{ENV_PROFILE_DIR} is not set — yiyan adapter requires a persistent "
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


SessionFactory = Callable[[YiyanAdapterConfig, Path, str], _BrowserSession]


def _noop_heartbeat(payload: dict[str, Any]) -> None:
    """activity 上下文之外的默认 heartbeat（测试/手工驱动时无副作用）。"""


# ---------------------------------------------------------------------------
# 异步泵（公开入口，故意不挂 @activity.defn——注册由 workers/main.py 门控完成）
# ---------------------------------------------------------------------------


async def run_yiyan_collection(
    item: CollectionTaskInput,
    *,
    session_factory: SessionFactory | None = None,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
    attempt: int = 1,
) -> CollectionTaskResult:
    """activity 核心：mode 门 → 配置门 → to_thread 跑浏览器 → 墙/结果映射。

    与 activity 上下文解耦（session_factory/heartbeat/attempt 注入），测试全程 mock
    浏览器层。session_factory 缺省 = 真 patchright 会话（worker 注册路径）。
    """
    if item.mode != "normal":
        raise ApplicationError(
            "deep_think not enabled in adapter v1",
            type="unsupported_mode",
            non_retryable=True,
        )
    config = YiyanAdapterConfig.from_env()
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    factory = session_factory or _PlaywrightYiyanSession
    beat = heartbeat or _noop_heartbeat
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
        await bound.ainfo("yiyan_wall", wall_type=wall.wall_type, stage=progress["stage"])
        raise ApplicationError(
            f"{wall}{evidence}", type=wall.wall_type, non_retryable=True
        ) from wall
    except _IncompleteCapture as inc:
        evidence = f"; evidence={inc.evidence_path}" if inc.evidence_path else ""
        await bound.ainfo("yiyan_capture_incomplete", reason=str(inc), stage=progress["stage"])
        raise ApplicationError(f"{inc}{evidence}", type="answer_capture_incomplete") from inc
    await bound.ainfo(
        "yiyan_collect_ok",
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


class _PlaywrightYiyanSession:
    """文心一言网页采集的 sync Playwright 实现（persistent context，每次全新、结束即关）。"""

    def __init__(self, config: YiyanAdapterConfig, evidence_dir: Path, file_stem: str) -> None:
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

                on_stage("navigate")
                try:
                    page.goto(_CHAT_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
                except PWTimeout:
                    page.goto(_CHAT_URL, wait_until="commit", timeout=_NAV_TIMEOUT_MS)
                page.wait_for_timeout(6_000)  # SPA + 反爬 JS 挂载（旧链同款 settle）
                _try_close_overlays(page)
                if _detect_login_wall(page):
                    raise _WallError(
                        "wall_login_required",
                        "yiyan login wall detected right after navigation",
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
                    # 发送被吞时优先识别登录墙（未登录点发送会弹 pass 登录层，
                    # 20260727 live 实测）——比笼统 wall_send 更诚实
                    if _detect_login_wall(page):
                        raise _WallError(
                            "wall_login_required",
                            "login wall surfaced on send (composer not cleared, "
                            "pass login dialog visible)",
                            self._shot(page, "login"),
                        )
                    raise _WallError(
                        "wall_send",
                        "send-not-accepted: composer still populated after "
                        f"{submit.get('attempts', '?')} send attempts (submission swallowed)",
                        self._shot(page, "send_wall"),
                    )
                on_stage("submitted")

                # 异步验证码窗口：challenge 发送后数秒内才挂载，轮询至多 12s；
                # 答案容器已出现且过 3.5s settle 窗即快走
                challenge_start = time.monotonic()
                while time.monotonic() < challenge_start + 12.0:
                    hit = _captcha_hit(page)
                    if hit:
                        raise _WallError(
                            "wall_captcha",
                            f"captcha challenge appeared post-send ({hit})",
                            self._shot(page, "captcha"),
                        )
                    if _dom_stream_started(page) and time.monotonic() - challenge_start >= 3.5:
                        break
                    page.wait_for_timeout(500)

                on_stage("await_stream")
                meta = _wait_dom_stream(page, appearance_timeout_s=20.0, timeout_s=_CHAT_TIMEOUT_S)
                answer_text = ""
                references: list[dict[str, Any]] = []
                if meta.get("found"):
                    answer_text = _extract_response_text(page, query)
                    references = _extract_references(page)
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
                        "but no answer container appeared within timeout — likely "
                        "content-filter or silent server-side drop",
                        self._shot(page, "no_stream"),
                    )
                if not meta.get("finished"):
                    raise _IncompleteCapture(
                        "stream-open-at-timeout: answer still generating after "
                        f"budget ({meta.get('bytes_received', 0)} chars observed) — answer "
                        "would be truncated; failing honestly",
                        self._shot(page, "truncated"),
                    )
                if not answer_text:
                    raise _IncompleteCapture(
                        "answer-empty-after-finished-stream: DOM extraction produced no "
                        "answer text despite finished stream",
                        self._shot(page, "empty_answer"),
                    )

                on_stage("screenshot")
                shot_path = self._evidence_dir / f"{self._file_stem}.png"
                page.screenshot(path=str(shot_path), full_page=True)
                if not shot_path.exists():
                    raise _IncompleteCapture("evidence-screenshot-failed: no file written")
                return CollectedAnswer(
                    answer_text=answer_text,
                    references=references,
                    screenshot_path=shot_path,
                    meta={
                        "stream": meta,
                        "transport": "dom-observed (sw-intercepted, 20260727 calibrated)",
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


def _loading_visible(page: Any) -> bool:
    """「生成中」瞬态指示器是否可见（20260727 live 校准：流式期间可见、结束即消失）。"""
    for sel in _LOADING_HINTS:
        try:
            if page.locator(sel).first.is_visible(timeout=200):
                return True
        except Exception:
            continue
    return False


def _last_answer_text(page: Any) -> str:
    """最后一个答案容器的正文（容器常驻，最后一个才是本轮答案）。"""
    try:
        els = page.locator(_ASSISTANT_SELECTORS[0]).all()
    except Exception:
        return ""
    for el in reversed(els):
        try:
            if el.is_visible(timeout=200):
                return (el.inner_text(timeout=1_000) or "").strip()
        except Exception:
            continue
    return ""


def _dom_stream_started(page: Any) -> bool:
    if _last_answer_text(page):
        return True
    try:
        return page.locator(_ASSISTANT_SELECTORS[0]).first.is_visible(timeout=200)
    except Exception:
        return False


def _wait_dom_stream(
    page: Any,
    *,
    appearance_timeout_s: float,
    timeout_s: float,
    quiet_s: float = 2.5,
    poll_ms: int = 500,
) -> dict[str, Any]:
    """文心流式完成等待（DOM 观测版）。

    传输层实况（20260727 live 校准，runtime/yiyan_probe_transport.py 实证）：
    页面装 Service Worker（wenxin.baidu.com/sw.js），completion 请求被 SW 中转，
    CDP Network 层抓不到事件流——故流信号改为 DOM 观测，语义等价：
    答案容器（div.conversation-flow-answer-container）出现 = 流开始；
    「生成中」瞬态指示器消失且正文连续 quiet_s 不变 = 流结束。
    零合成：正文即 DOM 实渲染文本，绝不从协议猜测拼装。
    """
    t0 = time.monotonic()
    appear_deadline = t0 + appearance_timeout_s
    overall_deadline = t0 + timeout_s
    while time.monotonic() < appear_deadline:
        if _dom_stream_started(page):
            break
        page.wait_for_timeout(poll_ms)
    else:
        return {
            "found": False,
            "finished": False,
            "failed": False,
            "bytes_received": 0,
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }
    last_text = _last_answer_text(page)
    stable_since = time.monotonic()
    while time.monotonic() < overall_deadline:
        page.wait_for_timeout(poll_ms)
        cur = _last_answer_text(page)
        if _loading_visible(page) or cur != last_text:
            last_text = cur
            stable_since = time.monotonic()
            continue
        if cur and time.monotonic() - stable_since >= quiet_s:
            return {
                "found": True,
                "finished": True,
                "failed": False,
                "bytes_received": len(cur),
                "elapsed_ms": int((time.monotonic() - t0) * 1000),
            }
    return {
        "found": True,
        "finished": False,
        "failed": False,
        "bytes_received": len(last_text),
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


def _try_close_overlays(page: Any) -> None:
    """best-effort 关 cookie 横幅/「我知道了」等遮罩。"""
    for sel in (
        'button:has-text("我知道了")',
        'button:has-text("知道了")',
        'button:has-text("同意")',
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
        loc = page.locator('[data-yiyan-send="true"]').first
        loc.scroll_into_view_if_needed(timeout=2000)
        loc.click(timeout=4000, force=False)
        return True
    except Exception:
        return False


def _send_via_keyboard(page: Any, input_loc: Any) -> bool:
    """Enter / Meta+Enter / Control+Enter 逐个试，输入框清空即服务端已受理。"""
    for shortcut in ("Enter", "Meta+Enter", "Control+Enter"):
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
    """输入框清空 = 提交被受理的 ground-truth 信号（旧链 live 实证同语义）。"""
    try:
        return str(input_loc.evaluate(_INPUT_VALUE_JS) or "").strip() == ""
    except Exception:
        return False


def _submit_and_confirm(
    page: Any, input_loc: Any, *, attempts: int = 2, settle_ms: int = 1600, poll_ms: int = 200
) -> dict[str, Any]:
    """点击发送并确认提交真正生效，被吞时重试一次（旧链 live 风控间歇性吞点击）。"""
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
    """DOM 抽取：先助手气泡选择器，再按「query 最后出现位置之后」切正文。"""
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


def _extract_references(page: Any) -> list[dict[str, Any]]:
    """参考来源 DOM best-effort 抽取：含「参考/来源」字样容器内的 http 锚点。

    零合成：抽不到即空列表，绝不从正文猜链接。
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    containers = (
        '[class*="reference"] a[href^="http"]',
        '[class*="source"] a[href^="http"]',
        'div:has-text("参考资料") a[href^="http"]',
        'div:has-text("参考来源") a[href^="http"]',
    )
    for sel in containers:
        try:
            anchors = page.locator(sel).all()
        except Exception:
            continue
        for a in anchors:
            try:
                url = a.get_attribute("href", timeout=500) or ""
                title = (a.inner_text(timeout=500) or "").strip()
            except Exception:
                continue
            if not url.startswith("http"):
                continue
            key = url.split("?")[0]
            if key in seen:
                continue
            seen.add(key)
            out.append({"url": url, "title": title or None, "sitename": None})
        if out:
            break
    return out


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
