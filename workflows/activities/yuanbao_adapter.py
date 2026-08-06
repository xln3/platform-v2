"""腾讯元宝网页采集适配器 v2（yuanbao.tencent.com，ADR-0003 五平台上线）。

结构对照 ``doubao_adapter.py``（已 live 验证的模板）；元宝平台知识移植自旧链
``server/proxyllm/engines/yuanbao.py``（recon 实测 + GUESS 标记，参考不盲信）：

- CONFIRMED（旧链 recon 实测）：聊天 URL、Next.js 重 hydration（≥10s settle）、
  匿名可渲染输入框、发送后弹登录模态（微信扫码/手机号/QQ）、Quill
  ``div.ql-editor[contenteditable]`` 输入框（contenteditable → 只能 keyboard.type，
  不能 fill）、``a/button[class*='send']`` 发送按钮、POST ``/api/chat/`` 流式回答。
- GUESS（login-gated 未实测）：助手气泡/引用卡片 DOM 选择器、「新对话」入口与
  新会话消息计数探针——命中与否如实记日志/走兜底，绝不臆造正文。

v2 边界（与豆包适配器对齐）：

- 仅 ``mode='normal'``；``deep_think`` → ``unsupported_mode`` non_retryable。
- 配置全走 env（秘密绝不进 task payload）：``GEO_YUANBAO_PROFILE_DIR``（必填，
  persistent profile 目录，缺失/不存在 → ``adapter_not_configured`` non_retryable）；
  ``GEO_YUANBAO_PROXY_URL``（可选，日志只落打码后的 scheme://host:port）；
  ``GEO_ADAPTER_EVIDENCE_DIR``（证据目录，五平台共享 env，默认
  ``platform-v2/runtime/adapter-evidence/yuanbao/``）；``GEO_YUANBAO_HEADLESS``
  （默认 1；0=headed 需 DISPLAY）；``GEO_YUANBAO_CDP_URL``（可选，常驻浏览器
  attach，见 resident_browser 契约）。
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

拟人化口径（2026-08-06 起，与豆包同标准——自动化交互序列本身即指纹）：

- 输入：composer 正文一律 ``human_like.human_type`` 逐字真实键盘事件
  （40-140ms 抖动 + 标点/空格后 15% 概率 250-800ms 停顿），绝不 insert_text/fill。
- 点击：所有业务点击（发送按钮、弹层清理、「新对话」、输入框聚焦）一律
  ``human_like.human_click``——贝塞尔移动 + 到位悬停 + 元素内随机偏移点击。
- 节奏：页面就绪 → 端详 0.6-1.8s → 点输入框 → 逐字输入 → 通读 0.5-1.5s → 发送。
- 机器路径不动：CDP 捕获、提交确认轮询、墙识别、截图等纯观测逻辑不产生
  输入事件，不构成行为指纹，保持原样（SSE/DOM 校准语义绝不改）。

新会话纪律（每个问题必须落在全新会话，绝不在旧会话里追问）：

- await_input 后 ``_ensure_fresh_chat`` 验证：composer 为空且页面无已存在
  消息节点 → 放行；否则优先点「新对话」按钮（GUESS 选择器组），仍不新则
  导航回 ``/chat`` 兜底（元宝 /chat 不带会话 id，导航即开全新会话）；
  最终验证不过 → ``_IncompleteCapture`` 诚实失败（可重试），绝不静默沿用旧会话。

run 级会话复用（2026-08-06 起，``collect_yuanbao_batch``，治本反风控）：

- 一个 run 的元宝任务在同一个常驻浏览器会话/同一标签页里顺序完成（一次
  ``launch_persistent_context``，绝不每题冷启全新 Chromium）。每题：
  fresh_chat 纪律 → 拟人输入/发送 → CDP 捕获/DOM 抽取/证据落盘（与 per-task
  共用 ``_collect_one`` 主体，绝无两套复制）→ 「阅读停顿」
  （human_like.human_read_pause：滚动 2-5 次 + 停留 8-25s 抖动）→ 下一题。
- 失败语义：题级墙/incomplete → 该题诚实记失败、后续题 aborted
  （aborted_after_failure，零浏览器交互——真人撞墙后会停下，不编造不硬闯）；
  结果列表与输入等长同序返回，绝不 raise 丢掉已完成题。session 建立阶段
  （launch/navigate/登录墙）异常=一题未发：wall 类成全题 wall 结果，
  临时故障（_IncompleteCapture）raise 走 batch 级重试。仅配置类错误
  （adapter_not_configured/unsupported_mode）允许 raise。

常驻浏览器 attach（resident_browser 契约）：

- ``GEO_YUANBAO_CDP_URL`` 非空 → ``platform_browser`` 走 ``connect_over_cdp``
  attach 到 supervisor 管理的常驻 Chromium：退出只断开 CDP 连接，不关 context、
  不做 profile 崩溃清理（profile/登录态归 supervisor 所有）。
- 未配置 → 回退 launch 路径（旧行为）：finally ``context.close()`` +
  ``_clean_profile_crash_state`` 启动前/close 后各幂等执行一次（根治
  「Restore pages?」崩溃标记，与豆包同款；实现 import 自 doubao_adapter，
  单一事实源）。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import random
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import structlog
from temporalio import activity
from temporalio.exceptions import ApplicationError

from workflows.activities.browser_driver import load_sync_browser_driver
from workflows.activities.collection import (
    CollectionBatchInput,
    CollectionBatchItemResult,
    CollectionBatchResult,
    CollectionTaskInput,
    CollectionTaskResult,
    batch_result_with_captcha_pause,
)

# profile 崩溃标记清理与豆包同一份实现（单一事实源，行为逐字一致）。
from workflows.activities.doubao_adapter import _clean_profile_crash_state
from workflows.activities.human_like import (
    human_click,
    human_pause,
    human_read_pause,
    human_type,
)
from workflows.activities.resident_browser import platform_browser, resident_cdp_url

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

# 「新对话」入口（命中第一个可见者即点，顺序即优先级）。
# 2026-08-07 headed live 校准（CDP attach yuanbao-tj 常驻浏览器，旧会话计数 4→0
# 实证）：元宝无文字「新对话」按钮，新建入口 = 左上角第二个图标触发器
# `div.yb-common-nav__trigger`（内嵌 `span.icon-yb-ic_newchat_20` iconfont，无文本
# 无 aria-label；icon 语义类名是锚点）——:has 组合选择器实证命中切新。
# 全部未命中时 _ensure_fresh_chat 回退导航 /chat——元宝 /chat 不带会话 id，
# 导航即开全新会话（旧链每题冷启导航即依赖此行为）。
_NEW_CHAT_SELECTORS: tuple[str, ...] = (
    "div.yb-common-nav__trigger:has(span.icon-yb-ic_newchat_20)",
    "span.icon-yb-ic_newchat_20",
    "[class*='icon-yb-ic_newchat']",
    'a:has-text("新对话")',
    'button:has-text("新对话")',
    '[role="button"]:has-text("新对话")',
    '[aria-label*="新对话"]',
    "[class*='new-chat']",
    "[class*='newChat']",
)

# 新会话验证：页面已存在消息节点计数（>0 = 旧会话/进行中的旧回答）。
# GUESS 选择器组（与 _ASSISTANT_SELECTORS 同源 + 用户气泡对称猜测；
# 匹配不到=0——此时「新对话」点按/导航兜底后的 composer 空校验仍是硬门）。
_CHAT_MESSAGE_COUNT_JS = r"""() => {
  const sels = [
    "div[class*='agent-chat__bubble--ai']",
    "div[class*='agent-chat__bubble--user']",
    "div[class*='agent-chat__bubble--human']",
    "div[class*='hyc-content']",
    "div[class*='bubble'][class*='user']",
    "div[class*='message'][class*='assistant']",
    "div[class*='message'][class*='user']"
  ];
  let n = 0;
  for (const s of sels) n += document.querySelectorAll(s).length;
  return n;
}"""

# 拟人化节奏区间（秒）——端详页面 / 发送前通读 / 新会话切换
_PACE_PAGE_READY_S = (0.6, 1.8)
_PACE_BEFORE_SEND_S = (0.5, 1.5)
_PACE_AFTER_NEW_CHAT_S = (0.6, 1.2)

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
    def from_env(cls, *, proxy_url_override: str | None = None) -> YuanbaoAdapterConfig:
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
        raw_proxy = (
            proxy_url_override
            if proxy_url_override is not None
            else os.environ.get(ENV_PROXY_URL, "")
        )
        proxy_url = raw_proxy.strip() or None
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


@dataclass(frozen=True)
class YuanbaoBatchItemSpec:
    """batch 内单题输入（session 层）：查询/mode + 证据文件名片段。"""

    business_key: str
    query: str
    mode: str
    file_stem: str


@dataclass
class YuanbaoBatchItemOutcome:
    """batch 内单题结果（session 层）：ok 携带 CollectedAnswer；失败/未执行
    携带 error_type/error_message/可选存证截图路径。status 词表与
    CollectionBatchItemResult 对齐（ok/wall/incomplete/aborted）。"""

    business_key: str
    status: str
    answer: CollectedAnswer | None = None
    error_type: str | None = None
    error_message: str | None = None
    evidence_path: Path | None = None


class _BrowserSession(Protocol):
    """Playwright 交互隔离面：测试注入 fake，绝不启动真浏览器。"""

    def collect(self, query: str, on_stage: Callable[[str], None]) -> CollectedAnswer: ...

    def collect_batch(
        self, items: list[YuanbaoBatchItemSpec], on_stage: Callable[[str], None]
    ) -> list[YuanbaoBatchItemOutcome]: ...


SessionFactory = Callable[[YuanbaoAdapterConfig, Path, str], _BrowserSession]


def _noop_heartbeat(payload: dict[str, Any]) -> None:
    """activity 上下文之外（测试/冒烟脚本）的默认 heartbeat：什么都不做。"""


# ---------------------------------------------------------------------------
# activity 入口与异步泵
# ---------------------------------------------------------------------------


@activity.defn(name="collect_yuanbao_batch")
async def collect_yuanbao_batch(batch: CollectionBatchInput) -> CollectionBatchResult:
    """元宝 batch 采集注册实现（workers/main.py 按 GEO_COLLECTION_ADAPTER 门控选择）。

    整个 batch 在同一个常驻浏览器会话里顺序完成（run 级会话复用）；墙/失败
    诚实记录在 per-item 结果里（本 activity 不因墙类失败 raise），仅配置类
    错误（adapter_not_configured/unsupported_mode）raise。
    """
    try:
        attempt = activity.info().attempt
    except RuntimeError:
        attempt = 1
    # 不传 session_factory：与 run_yuanbao_collection 的生产约定一致——缺省 None
    # 才走 to_thread 分支跑真实 sync 浏览器；显式传 _PlaywrightYuanbaoSession 会
    # 误判为注入 fake，在事件循环里直跑 sync API（豆包 2026-08-06 生产事故同款坑）。
    return await run_yuanbao_batch(
        batch,
        heartbeat=activity.heartbeat,
        attempt=attempt,
    )


async def run_yuanbao_batch(
    batch: CollectionBatchInput,
    *,
    session_factory: SessionFactory | None = None,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
    proxy_url_override: str | None = None,
    attempt: int = 1,
) -> CollectionBatchResult:
    """batch activity 核心：配置门 → mode 门 → to_thread 跑共享浏览器会话 →
    per-item outcome 映射。与 activity 上下文解耦（heartbeat/attempt 注入）。

    失败语义：题级墙/incomplete 由 session 转 outcome（后续题 aborted），本
    函数不 raise；session 级 _WallError（导航/登录墙，一题未发）成全题 wall
    结果（non_retryable 语义，重试只是再撞）；session 级 _IncompleteCapture
    （浏览器启动失败等临时故障，一题未发）raise 可重试 ApplicationError——
    结果全空时重试无已完成题损失。配置类错误一律 raise。
    """
    uses_default_session = session_factory is None
    if session_factory is None:
        session_factory = _PlaywrightYuanbaoSession
    if heartbeat is None:
        heartbeat = _noop_heartbeat

    for item in batch.items:
        if item.mode != "normal":
            raise ApplicationError(
                f"unsupported mode: {item.mode!r} (expected 'normal')",
                type="unsupported_mode",
                non_retryable=True,
            )
    config = YuanbaoAdapterConfig.from_env(proxy_url_override=proxy_url_override)
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    batch_stem = f"batch-{_safe_stem(batch.run_pub_id)}-a{attempt}"
    specs = [
        YuanbaoBatchItemSpec(
            business_key=item.business_key,
            query=item.query,
            mode=item.mode,
            file_stem=f"{_safe_stem(item.business_key)}-a{attempt}",
        )
        for item in batch.items
    ]
    bound = log.bind(
        run_pub_id=batch.run_pub_id,
        attempt=attempt,
        items=len(specs),
        proxy=mask_proxy_url(config.proxy_url),
    )
    progress: dict[str, Any] = {"stage": "browser_launch", "item": None}

    def _blocking() -> list[YuanbaoBatchItemOutcome]:
        session = session_factory(config, config.evidence_dir, batch_stem)

        def _on_stage(stage: str) -> None:
            progress["stage"] = stage
            if stage.startswith("item:"):
                progress["item"] = stage.removeprefix("item:")

        return session.collect_batch(specs, on_stage=_on_stage)

    def _heartbeat_payload() -> dict[str, Any]:
        return {
            "run_pub_id": batch.run_pub_id,
            "stage": progress["stage"],
            "item": progress["item"],
            "items_total": len(specs),
        }

    try:
        if uses_default_session:
            thread = asyncio.ensure_future(asyncio.to_thread(_blocking))
            while True:
                heartbeat(_heartbeat_payload())
                done, _pending = await asyncio.wait({thread}, timeout=_HEARTBEAT_INTERVAL_S)
                if done:
                    break
            outcomes = thread.result()
        else:
            heartbeat(_heartbeat_payload())
            outcomes = _blocking()
    except _WallError as wall:
        # session 级墙（导航后登录墙）：一题未发，全题诚实记 wall。
        evidence_suffix = f"; evidence={wall.evidence_path}" if wall.evidence_path else ""
        bound.info("yuanbao_batch_session_wall", wall_type=wall.wall_type, stage=progress["stage"])
        return CollectionBatchResult(
            results=[
                _failure_batch_item(
                    item,
                    status="wall",
                    error_type=wall.wall_type,
                    error_message=f"{wall}{evidence_suffix}",
                    evidence_path=wall.evidence_path,
                )
                for item in batch.items
            ]
        )
    except _IncompleteCapture as inc:
        # session 级临时故障（浏览器启动失败等）：一题未发，raise 走 batch 重试。
        evidence_suffix = f"; evidence={inc.evidence_path}" if inc.evidence_path else ""
        bound.info("yuanbao_batch_session_incomplete", reason=str(inc), stage=progress["stage"])
        raise ApplicationError(
            f"{inc}{evidence_suffix}", type="answer_capture_incomplete"
        ) from inc
    if len(outcomes) != len(batch.items):
        # session 契约：结果列表必须与输入等长（失败/未执行题也占位）。缺斤短两
        # 说明实现有 bug——fail-closed raise（编程错误，重试无意义）。
        raise ApplicationError(
            f"batch session returned {len(outcomes)} outcomes for {len(batch.items)} items",
            type="batch_outcome_contract_violation",
            non_retryable=True,
        )
    results = [
        _batch_item_result(item, outcome)
        for item, outcome in zip(batch.items, outcomes, strict=True)
    ]
    bound.info(
        "yuanbao_batch_done",
        ok=sum(1 for r in results if r.status == "ok"),
        failed=sum(1 for r in results if r.status != "ok"),
        stage=progress["stage"],
    )
    return batch_result_with_captcha_pause(results)


def _failure_batch_item(
    item: CollectionTaskInput,
    *,
    status: str,
    error_type: str,
    error_message: str,
    evidence_path: Path | None,
) -> CollectionBatchItemResult:
    """失败/未执行题 → CollectionBatchItemResult。DLP 由 persist 层统一脱敏。"""
    screenshot_ref = f"file://{evidence_path}" if evidence_path is not None else None
    return CollectionBatchItemResult(
        business_key=item.business_key,
        status=status,
        error_type=error_type,
        error_message=error_message,
        screenshot_ref=screenshot_ref,
        quality_state=error_type,
    )


def _batch_item_result(
    item: CollectionTaskInput, outcome: YuanbaoBatchItemOutcome
) -> CollectionBatchItemResult:
    """per-item outcome → CollectionBatchItemResult。ok 题复用 per-task 同款
    映射（_task_result_from_collected）；失败/未执行题携带诚实错误信息。"""
    if outcome.status == "ok":
        if outcome.answer is None:
            raise ApplicationError(
                f"batch outcome for {item.business_key!r} is ok but carries no answer",
                type="batch_outcome_contract_violation",
                non_retryable=True,
            )
        base = _task_result_from_collected(item, outcome.answer)
        return CollectionBatchItemResult(
            business_key=item.business_key,
            status="ok",
            answer_text=base.answer_text,
            screenshot_ref=base.screenshot_ref,
            quality_state=base.quality_state,
            citations=base.citations,
            evidence=base.evidence,
            search_queries=base.search_queries,
        )
    return _failure_batch_item(
        item,
        status=outcome.status,
        error_type=outcome.error_type or "unknown_failure",
        error_message=outcome.error_message or "",
        evidence_path=outcome.evidence_path,
    )


async def run_yuanbao_collection(
    item: CollectionTaskInput,
    *,
    session_factory: SessionFactory | None = None,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
    proxy_url_override: str | None = None,
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
    uses_default_session = session_factory is None
    factory = session_factory or _PlaywrightYuanbaoSession
    beat = heartbeat or _noop_heartbeat
    config = YuanbaoAdapterConfig.from_env(proxy_url_override=proxy_url_override)
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

    try:
        if uses_default_session:
            thread = asyncio.ensure_future(asyncio.to_thread(_blocking))
            while True:
                beat({"business_key": item.business_key, "stage": progress["stage"]})
                done, _pending = await asyncio.wait({thread}, timeout=_HEARTBEAT_INTERVAL_S)
                if done:
                    break
            collected = thread.result()
        else:
            beat({"business_key": item.business_key, "stage": progress["stage"]})
            collected = _blocking()
    except _WallError as wall:
        evidence = f"; evidence={wall.evidence_path}" if wall.evidence_path else ""
        bound.info("yuanbao_wall", wall_type=wall.wall_type, stage=progress["stage"])
        raise ApplicationError(
            f"{wall}{evidence}", type=wall.wall_type, non_retryable=True
        ) from wall
    except _IncompleteCapture as inc:
        evidence = f"; evidence={inc.evidence_path}" if inc.evidence_path else ""
        bound.info("yuanbao_capture_incomplete", reason=str(inc), stage=progress["stage"])
        raise ApplicationError(f"{inc}{evidence}", type="answer_capture_incomplete") from inc
    bound.info(
        "yuanbao_collect_ok",
        answer_len=len(collected.answer_text),
        references=len(collected.references),
        stage=progress["stage"],
    )
    return _task_result_from_collected(item, collected)


def _task_result_from_collected(
    item: CollectionTaskInput, collected: CollectedAnswer
) -> CollectionTaskResult:
    """CollectedAnswer → CollectionTaskResult 映射（answer 组装/出界 DLP 自检）。
    run_yuanbao_collection 与 batch per-item ok 映射共用。"""
    answer_text = _compose_answer_text(collected.answer_text, collected.references)
    screenshot_ref = f"file://{collected.screenshot_path}"
    # DLP 统一由 persist 层脱敏处理（单一权威边界，2026-08-06 起）。
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
    """元宝网页采集的 sync Playwright 实现（persistent context / 常驻 CDP attach）。

    单题（``collect``，per-task 老路径）与 run 级会话复用（``collect_batch``）
    共享同一套 per-item 主体 ``_collect_one``——绝不复制出两套：

    - ``collect``：一次会话、一题、收尾（老行为不变）；
    - ``collect_batch``：一次会话，N 题在同一常驻会话/同一标签页里顺序完成
      （真人在同一浏览器窗口里连续聊天——每题落在全新会话但绝不重开浏览器）；
      每题成功后做「阅读停顿」（拟人读完回答：滚动浏览 + 停留）。

    batch 失败语义：题级墙/incomplete 转 outcome——该题诚实失败、后续题
    aborted（零浏览器交互：真人撞墙后会停下，不编造不硬闯），结果列表与
    输入等长同序；session 建立阶段（launch/navigate/登录墙检查）的异常
    原样逃出，由 activity 层按 session 级语义处理（一题未发）。
    """

    def __init__(self, config: YuanbaoAdapterConfig, evidence_dir: Path, file_stem: str) -> None:
        self._config = config
        self._evidence_dir = evidence_dir
        self._file_stem = file_stem
        # 拟人化：本 session 专用 RNG（真随机；测试在 human_like 层 seeded）与
        # 光标位置追踪（连续轨迹，避免每次点击都从合成起点重新起跳）。
        self._rng = random.Random()
        self._mouse_pos: tuple[float, float] | None = None

    def collect(self, query: str, on_stage: Callable[[str], None]) -> CollectedAnswer:
        spec = YuanbaoBatchItemSpec(
            business_key=self._file_stem,
            query=query,
            mode="normal",
            file_stem=self._file_stem,
        )
        with self._browser_session(on_stage) as (context, page, driver):
            return self._collect_one(context, page, spec, on_stage, driver=driver)

    def collect_batch(
        self, items: list[YuanbaoBatchItemSpec], on_stage: Callable[[str], None]
    ) -> list[YuanbaoBatchItemOutcome]:
        outcomes: list[YuanbaoBatchItemOutcome] = []
        with self._browser_session(on_stage) as (context, page, driver):
            for index, spec in enumerate(items):
                on_stage(f"item:{spec.business_key}")
                try:
                    answer = self._collect_one(context, page, spec, on_stage, driver=driver)
                except _WallError as wall:
                    outcomes.append(self._failure_outcome(spec, "wall", wall.wall_type, wall))
                    outcomes.extend(
                        self._aborted_outcome(rest, spec, wall.wall_type)
                        for rest in items[index + 1 :]
                    )
                    return outcomes
                except _IncompleteCapture as inc:
                    outcomes.append(
                        self._failure_outcome(spec, "incomplete", "answer_capture_incomplete", inc)
                    )
                    outcomes.extend(
                        self._aborted_outcome(rest, spec, "answer_capture_incomplete")
                        for rest in items[index + 1 :]
                    )
                    return outcomes
                outcomes.append(
                    YuanbaoBatchItemOutcome(
                        business_key=spec.business_key, status="ok", answer=answer
                    )
                )
                # 阅读停顿：拟人读完回答（滚动浏览 + 停留 8-25s 抖动）——题间天然
                # 间隔，也产出真实浏览信号；最后一题同样停留（真人读完才关浏览器）。
                pause_s = self._reading_pause(page)
                log.info(
                    "yuanbao_read_pause",
                    business_key=spec.business_key,
                    seconds=round(pause_s, 2),
                )
        return outcomes

    @staticmethod
    def _failure_outcome(
        spec: YuanbaoBatchItemSpec,
        status: str,
        error_type: str,
        exc: _WallError | _IncompleteCapture,
    ) -> YuanbaoBatchItemOutcome:
        return YuanbaoBatchItemOutcome(
            business_key=spec.business_key,
            status=status,
            error_type=error_type,
            error_message=str(exc),
            evidence_path=exc.evidence_path,
        )

    @staticmethod
    def _aborted_outcome(
        spec: YuanbaoBatchItemSpec, failed_spec: YuanbaoBatchItemSpec, error_type: str | None
    ) -> YuanbaoBatchItemOutcome:
        # 真人撞墙后会停下：本题未执行（零浏览器交互），诚实标记不编造不硬闯。
        return YuanbaoBatchItemOutcome(
            business_key=spec.business_key,
            status="aborted",
            error_type="aborted_after_failure",
            error_message=(
                f"not executed: batch stopped after item {failed_spec.business_key!r} "
                f"failed ({error_type or 'unknown'}) — no browser interaction for this item"
            ),
        )

    def _reading_pause(self, page: Any) -> float:
        """拟人阅读停顿（human_like.human_read_pause，RNG 用本 session 实例）。"""
        return human_read_pause(page, self._rng)

    @contextlib.contextmanager
    def _browser_session(
        self, on_stage: Callable[[str], None]
    ) -> Iterator[tuple[Any, Any, str]]:
        """attach-or-launch + 导航 + 登录墙检查 → yield (context, page, driver)。

        经 resident_browser.platform_browser：``GEO_YUANBAO_CDP_URL`` 非空时
        attach 常驻浏览器（退出只断开 CDP，不关 context、不清理 profile——
        均归 supervisor）；否则回退 launch_persistent_context（旧行为），
        优雅关闭=finally context.close()（契约内）+ close 前后各一次
        _clean_profile_crash_state 幂等清理（根治「Restore pages?」崩溃标记）。
        """
        # 延迟导入：模块加载不硬依赖浏览器驱动（worker 未装依赖时仍可注册 fail-closed 实现）。
        # 驱动首选 patchright（旧链生产同款反检测补丁版）；vanilla playwright 仅作开发兜底。
        driver, sync_playwright, PWTimeout = load_sync_browser_driver()

        on_stage("browser_launch")
        with sync_playwright() as pw:
            try:
                resident_url = resident_cdp_url("yuanbao")
            except ValueError as exc:
                raise ApplicationError(
                    str(exc), type="adapter_not_configured", non_retryable=True
                ) from None
            if resident_url is None:
                # 启动前愈合前任进程的崩溃标记（activity 取消/SIGKILL 会绕过正常
                # close，Chromium 未写回 exit_type=Normal → 下次启动弹
                # 「Restore pages?」）。幂等纯文件操作，失败不阻塞启动（close 后
                # 还有一次兜底清理）。仅 launch 路径；attach 的 profile 归 supervisor。
                try:
                    _clean_profile_crash_state(self._config.profile_dir)
                except Exception:
                    pass

            def _launch() -> tuple[Any, Any]:
                try:
                    context = pw.chromium.launch_persistent_context(
                        user_data_dir=str(self._config.profile_dir),
                        headless=self._config.headless,
                        proxy=(
                            _parse_proxy(self._config.proxy_url)
                            if self._config.proxy_url
                            else None
                        ),
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
                page = context.pages[0] if context.pages else context.new_page()
                return context, page

            try:
                with platform_browser(pw, platform="yuanbao", launch=_launch) as (
                    context,
                    page,
                    _resident,
                ):
                    context.set_default_timeout(_NAV_TIMEOUT_MS)

                    on_stage("navigate")
                    try:
                        page.goto(_CHAT_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
                    except PWTimeout:
                        page.goto(_HOME_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
                    page.wait_for_timeout(_HYDRATION_SETTLE_MS)  # Next.js 重 hydration（实测 ≥10s）
                    _try_close_overlays(page, self._rng)
                    if _detect_login_wall(page):
                        raise _WallError(
                            "wall_login_required",
                            "yuanbao login wall detected right after navigation",
                            self._shot(page, "login"),
                        )
                    yield context, page, driver
            finally:
                # launch 路径：context.close() 由 platform_browser 契约执行；
                # close 后兜底清理崩溃标记（覆盖 close 期竞态），幂等纯文件操作。
                # attach 路径：profile 归 supervisor，绝不清理。
                if resident_url is None:
                    try:
                        _clean_profile_crash_state(self._config.profile_dir)
                    except Exception as exc:
                        log.warning(
                            "yuanbao_profile_crash_clean_failed",
                            business_key=self._file_stem,
                            error=f"{type(exc).__name__}: {exc}",
                        )

    def _collect_one(
        self,
        context: Any,
        page: Any,
        spec: YuanbaoBatchItemSpec,
        on_stage: Callable[[str], None],
        *,
        driver: str,
    ) -> CollectedAnswer:
        """单题主体：await_input → fresh_chat → 拟人输入/发送 → CDP 捕获/
        DOM 抽取/证据落盘。per-task 单题与 batch 每题共用。"""
        capture = _ChatStreamCapture(context, page)
        try:

            def _pace(lo: float, hi: float) -> float:
                # 节奏等待走 page.wait_for_timeout：停顿全部留在页面事件序列里
                # （可观测、可 fake），与 human_like 内部等待同口径。
                return human_pause(
                    self._rng, lo, hi, sleep=lambda s: page.wait_for_timeout(int(s * 1000))
                )

            on_stage("await_input")

            def _shot(suffix: str) -> Path | None:
                # 墙/失败存证截图：batch 内按 per-item stem 命名（逐题区分）。
                return self._shot(page, suffix, stem=spec.file_stem)

            input_loc = _wait_for_input(page, timeout_ms=15_000)
            if input_loc is None:
                hit = _captcha_hit(page)
                if hit:
                    raise _WallError(
                        "wall_captcha",
                        f"captcha widget visible before input ({hit})",
                        _shot("captcha"),
                    )
                if _detect_login_wall(page):
                    raise _WallError(
                        "wall_login_required",
                        "login wall surfaced while awaiting chat input",
                        _shot("login"),
                    )
                raise _IncompleteCapture(
                    "could-not-find-chat-input",
                    _shot("no_input"),
                )

            # 新会话纪律：每个问题必须落在全新会话，绝不在旧会话里追问。
            on_stage("fresh_chat")
            _ensure_fresh_chat(
                page,
                input_loc,
                self._rng,
                pace=_pace,
                shot=_shot,
            )

            on_stage("typing")
            # 页面就绪：真人先端详一眼再动手（零停顿直点输入框是机器人指纹）。
            _pace(*_PACE_PAGE_READY_S)
            # SPA settle 后可能异步弹业务弹层；输入框仍 visible 但弹层会截获
            # 发送按钮。await_input 后再收一次，覆盖迟到弹层。
            _try_close_overlays(page, self._rng)
            # 点输入框聚焦（贝塞尔移动 + 悬停 + 框内随机偏移点击）。human_click
            # 拿不到布局时内部回退原生 click；仍失败则原样抛出=诚实失败。
            clicked_at = human_click(input_loc, page, self._rng, start=self._mouse_pos)
            if clicked_at is not None:
                self._mouse_pos = clicked_at
            # contenteditable（Quill）→ 只能真实键盘事件逐字输入，不能 fill/insert_text
            human_type(input_loc, spec.query, self._rng)
            # 发送前通读一遍（原实现 type 后固定 800ms 即发送=秒发指纹）。
            _pace(*_PACE_BEFORE_SEND_S)

            submit = _submit_and_confirm(
                page, input_loc, self._rng, pace=_pace, start=self._mouse_pos
            )
            if not submit.get("submitted"):
                # 匿名发送会弹登录模态而非清空输入框——先查墙再判发送失败
                if _detect_login_wall(page):
                    raise _WallError(
                        "wall_login_required",
                        "login modal popped after send (anonymous session)",
                        _shot("login"),
                    )
                raise _WallError(
                    "wall_send",
                    "send-not-accepted: composer still populated after "
                    f"{submit.get('attempts', '?')} send attempts (submission swallowed)",
                    _shot("send_wall"),
                )
            on_stage("submitted")

            # 登录态下发送后仍可能弹墙（session 过期/风控）：短窗检测
            page.wait_for_timeout(2_500)
            if _detect_login_wall(page):
                raise _WallError(
                    "wall_login_required",
                    "login modal surfaced post-send",
                    _shot("login"),
                )

            # 异步验证码窗口：轮询至多 12s；流已开始且过 3.5s settle 窗即快走
            challenge_start = time.monotonic()
            while time.monotonic() < challenge_start + 12.0:
                hit = _captcha_hit(page)
                if hit:
                    raise _WallError(
                        "wall_captcha",
                        f"captcha challenge appeared post-send ({hit})",
                        _shot("captcha"),
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
                        _shot("send_wall"),
                    )
                if notices["realname"]:
                    raise _WallError(
                        "wall_login_required",
                        "realname wall notice in DOM: " + ",".join(notices["realname"]),
                        _shot("realname"),
                    )
            if not meta.get("found"):
                raise _IncompleteCapture(
                    "send-accepted-no-stream: composer cleared (submission accepted) "
                    "but no /api/chat/ stream fired within timeout — likely "
                    "content-filter or silent server-side drop",
                    _shot("no_stream"),
                )
            if not meta.get("finished"):
                raise _IncompleteCapture(
                    "stream-open-at-timeout: /api/chat/ stream still open after "
                    f"budget ({meta.get('bytes_received', 0)} bytes captured) — answer "
                    "would be truncated; failing honestly",
                    _shot("truncated"),
                )
            if not answer_text:
                raise _IncompleteCapture(
                    "answer-empty-after-finished-stream: DOM extraction produced no "
                    "answer text after stream finished",
                    _shot("empty_answer"),
                )

            on_stage("screenshot")
            shot_path = self._evidence_dir / f"{spec.file_stem}.png"
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
            # batch 内每题一个 CDP session：题末 best-effort detach，避免旧
            # session 挂着监听累积（下一题新建 capture，绝不串题读到旧流）。
            capture.detach()

    def _shot(self, page: Any, suffix: str, *, stem: str | None = None) -> Path | None:
        """墙/失败存证截图（viewport 即可）。best-effort，失败返回 None。

        ``stem`` 缺省用 session 级 file_stem（导航/登录墙）；batch 内题级
        存证由 _collect_one 传 per-item stem（逐题区分，绝不互相覆盖）。
        """
        path = self._evidence_dir / f"{stem or self._file_stem}-{suffix}.png"
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

    def detach(self) -> None:
        """best-effort 断开 CDP session（batch 内每题一个 capture，题末断开
        避免旧 session 挂着监听累积）。失败静默——页面可能已随 context 关闭。"""
        try:
            self._cdp.detach()
        except Exception:
            pass

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


def _try_close_overlays(page: Any, rng: random.Random) -> None:
    """best-effort 关 cookie 横幅/「我知道了」等遮罩（拟人化点击）。

    先 count/visible 粗筛（纯观测），只有真实存在的遮罩才 human_click——
    避免对候选选择器逐一发贝塞尔点击（那本身也是机器人指纹）。
    """
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
    except Exception:
        pass
    for sel in (
        'button:has-text("我知道了")',
        'button:has-text("知道了")',
        'button:has-text("Got it")',
        'button:has-text("Accept")',
        '[aria-label="关闭"]',
        '[aria-label="close"]',
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() == 0 or not loc.is_visible(timeout=400):
                continue
            human_click(loc, page, rng)
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


def _fresh_chat_ok(page: Any, input_loc: Any) -> bool:
    """新会话 ground truth：composer 为空 且 页面无已存在消息节点。

    探针异常一律按「不新」处理——宁可多走一步兜底，绝不静默沿用旧会话。
    """
    try:
        if str(input_loc.evaluate(_INPUT_VALUE_JS) or "").strip():
            return False
    except Exception:
        return False
    try:
        count = int(page.evaluate(_CHAT_MESSAGE_COUNT_JS) or 0)
    except Exception:
        return False
    return count == 0


def _ensure_fresh_chat(
    page: Any,
    input_loc: Any,
    rng: random.Random,
    *,
    pace: Callable[[float, float], float],
    shot: Callable[[str], Path | None],
) -> None:
    """每个问题必须落在全新会话：已是新会话直接放行；否则优先点「新对话」
    按钮，仍不新则导航回 ``/chat`` 兜底（元宝 /chat 不带会话 id，导航即开
    全新会话——旧链每题冷启导航即依赖此行为）；最终验证不过 →
    _IncompleteCapture 诚实失败（可重试），绝不静默沿用旧会话。
    """
    if _fresh_chat_ok(page, input_loc):
        return
    # 优先点「新对话」（真人在旧会话里想提新问题的标准动作）。
    for sel in _NEW_CHAT_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=500):
                human_click(loc, page, rng)
                pace(*_PACE_AFTER_NEW_CHAT_S)  # 等 SPA 切到新会话
                break
        except Exception:
            continue
    if _fresh_chat_ok(page, input_loc):
        return
    # 回退：导航到聊天首页（/chat 无会话 id = 全新聊天页）并等 composer 回来。
    try:
        page.goto(_CHAT_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
    except Exception:
        pass
    pace(1.0, 2.0)
    deadline = time.monotonic() + 15.0  # Next.js 重 hydration，与 await_input 同预算
    while time.monotonic() < deadline:
        try:
            if input_loc.count() > 0 and input_loc.is_visible(timeout=500):
                break
        except Exception:
            pass
        page.wait_for_timeout(400)
    if _fresh_chat_ok(page, input_loc):
        return
    raise _IncompleteCapture(
        "could-not-establish-fresh-chat: composer not empty or prior conversation "
        "still visible after 新对话 click + chat-home navigation fallback",
        shot("fresh_chat"),
    )


def _click_send_button(
    page: Any,
    rng: random.Random,
    *,
    start: tuple[float, float] | None = None,
) -> bool:
    """第一个可见的 send 按钮拟人化点击（贝塞尔移动 + 悬停 + 完整鼠标事件链）。"""
    for sel in _SEND_SELECTORS:
        try:
            loc = page.locator(sel).first
            if not loc.is_visible(timeout=800):
                continue
            human_click(loc, page, rng, start=start)
            return True
        except Exception:
            continue
    return False


def _send_via_keyboard(page: Any, input_loc: Any, rng: random.Random) -> bool:
    """Enter 提交（元宝 confirmed：Enter 即发送）；输入框清空即服务端已受理。"""
    try:
        human_click(input_loc, page, rng)
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
    page: Any,
    input_loc: Any,
    rng: random.Random,
    *,
    pace: Callable[[float, float], float],
    start: tuple[float, float] | None = None,
    attempts: int = 2,
    settle_ms: int = 1600,
    poll_ms: int = 200,
) -> dict[str, Any]:
    """拟人化点击发送并确认提交真正生效，被吞时像真人一样顿一下再试一次。"""
    used = 0
    for i in range(max(1, attempts)):
        used = i + 1
        _try_close_overlays(page, rng)
        if not _click_send_button(page, rng, start=start):
            _send_via_keyboard(page, input_loc, rng)
        waited = 0
        while waited < settle_ms:
            page.wait_for_timeout(poll_ms)
            waited += poll_ms
            if _composer_cleared(input_loc):
                return {"submitted": True, "attempts": used}
        if used < attempts:
            # 发送被吞：真人会愣一下、重新点回输入框再试（原实现 200ms 机械重击）。
            pace(0.5, 1.2)
            try:
                human_click(input_loc, page, rng)
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
