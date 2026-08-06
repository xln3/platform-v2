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

v1 边界（2026-08-05 W1 起 deep_think 解锁）：

- ``mode='normal'`` 与 ``mode='deep_think'`` 均支持；其他 mode →
  ``ApplicationError(..., type="unsupported_mode", non_retryable=True)`` 诚实拒绝。
  deep_think 经 composer 模式 picker 的 UI toggle 启用（移植自旧链
  ``server/proxyllm/deep_think.py``，selector 漂移防护 + 后置校验原样保留）；
  无法确认启用 → ``deep_think_toggle_failed`` non_retryable，绝不静默回退 normal。
  历史教训：route patch 改写 /chat/completion 请求体（need_deep_think 0→1）已被否决——
  豆包客户端对请求体签名，改字节即签名失效、服务端静默吞发送（旧链 2026-07-15
  live 实证），UI toggle 是唯一合法机制。
- 配置全走 env（秘密绝不进 task payload）：
  ``GEO_DOUBAO_PROFILE_DIR``（必填，浏览器 persistent profile 目录；缺失/不存在 →
  ``adapter_not_configured`` non_retryable）；``GEO_DOUBAO_PROXY_URL``（可选，
  形如 http://user:pass@host:port——只从 env 读，日志只出现打码后的 scheme://host:port）；
  ``GEO_DOUBAO_EVIDENCE_DIR``（截图目录，默认 ``platform-v2/runtime/doubao-evidence/``，
  自动建目录）；``GEO_DOUBAO_HEADLESS``（默认 1 headless；0=headed 需 DISPLAY）；
  ``GEO_DOUBAO_CHAT_TIMEOUT_S``（/chat/completion 流式完成预算秒数，默认 600——
  deep_think 流远长于 normal 的 120s 旧预算，workflow 侧 start_to_close 同步放大）。
- SSE 结构化证据（W1）：流组装成功后把 thinking_chain/search_blocks/queries/stats
  等结构化产物（不存全量 SSE 原文）序列化为 JSON 落证据目录，随截图同款流程产出
  ``CollectionEvidenceRef(kind="sse", relation_type="answer_sse_trace")`` 进 CAS；
  解析失败保持诚实——不出证据、不编造。
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

拟人化口径（2026-08-06 起。背景：deep_think 模式 picker 连点+秒发被行为风控
稳定识别出 wall_captcha（00:44/00:46/01:14 三连），而人工同账号同代理发送
无验证码——自动化交互序列本身即指纹）：

- 输入：composer 正文一律 ``human_like.human_type`` 逐字真实键盘事件
  （40-140ms 抖动 + 标点/空格后 15% 概率 250-800ms 停顿），绝不 insert_text/fill。
- 点击：所有业务点击（发送按钮、弹层清理、模式 picker 与选项、「新对话」、
  分享导出）一律 ``human_like.human_click``——贝塞尔移动 + 到位悬停 +
  元素内随机偏移点击。legacy share_export（server/proxyllm/share_export.py，
  不在本仓库写边界内）的裸 mouse.click/locator.click 经 ``_HumanizedPageFacade``
  包装成同口径；其 JS ``e.click()`` 兜底路径无法包装，属已知残留指纹面。
- 节奏：页面就绪 → 端详 0.6-1.8s → 点输入框 → 逐字输入 → 通读 0.5-1.5s →
  发送；deep_think picker：悬停 300-900ms → 点击 → 等弹层 → 读菜单
  400-1000ms → 点选项。
- 机器路径不动：SSE/CDP 捕获、提交确认轮询、墙识别、截图等纯观测逻辑不产生
  输入事件，不构成行为指纹，保持原样。

新会话纪律（每个问题必须落在全新会话，绝不在旧会话里追问）：

- await_input 后 ``_ensure_fresh_chat`` 验证：composer 为空且页面无已存在
  消息节点 → 放行；否则优先点「新对话」按钮，仍不新则导航回聊天首页兜底；
  最终验证不过 → ``_IncompleteCapture`` 诚实失败（可重试），绝不静默沿用旧会话。

优雅关闭（profile 崩溃标记根治）：

- 根因：persistent context 的浏览器进程若未经 ``context.close()`` 走完正常退出
  （activity 取消时 to_thread 线程无法强杀、worker 进程被杀、close 异常被静默
  吞掉），Chromium 不会把 profile ``Preferences`` 里的 ``profile.exit_type``
  写回 ``"Normal"``，下次启动即弹「Restore pages? Chromium didn't shut down
  correctly」。
- 对策：所有退出路径（成功/墙/超时/异常）都经 finally ``context.close()`` 且
  异常如实记日志（不再静默 pass）；``_clean_profile_crash_state`` 在启动前
  （愈合被 SIGKILL 的前任进程）与 close 后（兜底 close 期竞态）各幂等执行
  一次，把 ``exit_type="Normal"`` / ``exited_cleanly=true`` 写回——真人正常
  关浏览器本就该是 Normal。

run 级会话复用（2026-08-06 起，``collect_doubao_batch``，治本反风控）：

- 背景：拟人化后每个 run 的第一问永远成功、第二问在发送瞬间必撞图片验证码
  （生产实证，25s 与 174s 间隔都撞）。风控抓的是「冷启动即发问+短时间再次
  冷启动」的会话结构，不是单消息交互细节——真人是在同一浏览器窗口里连续
  聊天的。
- 结构：一个 run 的豆包任务在同一个常驻浏览器会话/同一标签页里顺序完成
  （一次 ``launch_persistent_context``，绝不每题冷启全新 Chromium）。每题：
  fresh_chat 纪律（点「新对话」，绝不重开浏览器）→ [deep_think toggle] →
  拟人输入/发送 → SSE 捕获/组装/证据落盘（与 per-task 共用 ``_collect_one``
  主体，绝无两套复制）→ 「阅读停顿」（human_like.human_read_pause：滚动
  2-5 次 + 停留 8-25s 抖动——题间天然间隔，也产出真实浏览信号）→ 下一题。
- 失败语义：题级墙/incomplete → 该题诚实记失败、后续题 aborted
  （aborted_after_failure，零浏览器交互——真人撞墙后会停下，不编造不硬闯）；
  结果列表与输入等长同序返回，绝不 raise 丢掉已完成题。session 建立阶段
  （launch/navigate/登录墙）异常=一题未发：wall 类成全题 wall 结果，
  临时故障（_IncompleteCapture）raise 走 batch 级重试。仅配置类错误
  （adapter_not_configured/unsupported_mode）允许 raise。
- workflow 路由见 definitions/collection.py 的 ``doubao-batch-collect-v1``
  patch 门；批次之间的节奏仍由 workflow 层 inter-task pacing 承担。

跨 run 常驻浏览器（2026-08-06 起，W8：CDP attach，契约层在
``workflows/activities/resident_browser.py``）：

- ``GEO_DOUBAO_CDP_URL``（如 ``http://127.0.0.1:19222``）非空 → attach 到
  supervisor（``tools/resident_browser.py`` + systemd ``geo-platform-v2-browser@``）
  管理的常驻 Chromium，跨 run 复用同一会话（真人浏览器长期开着，采集 attach
  而不是冷启动——W6 拟人化 + W7 run 级复用后残留的最后一层机器指纹）。
  未配置 → 回退旧 launch 路径，语义一字不差（开发/测试不受影响）。
- attach 路径：导航/登录墙检查/采集主体与 launch 路径完全共用；退出只断开
  CDP 连接（契约管），**不做** ``context.close``、**不做**崩溃标记清理
  （profile 归 supervisor）。attach 断连/常驻崩溃 → 按 ``browser-launch-failed``
  诚实重试（``_IncompleteCapture``），supervisor 重启后自愈。
- 常驻侧用代理固定：换代理/换 profile 需重启常驻服务（人工运维动作）。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import json
import os
import random
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import structlog
from temporalio import activity
from temporalio.exceptions import ApplicationError

from workflows.activities.browser_driver import load_sync_browser_driver
from workflows.activities.collection import (
    CaptchaPause,
    CollectionBatchInput,
    CollectionBatchItemResult,
    CollectionBatchResult,
    CollectionEvidenceRef,
    CollectionTaskInput,
    CollectionTaskResult,
)
from workflows.activities.doubao_share_bridge import capture_share_image, capture_share_link
from workflows.activities.human_like import (
    human_click,
    human_move_to,
    human_pause,
    human_read_pause,
    human_type,
)
from workflows.activities.resident_browser import (
    BrowserBusyError,
    platform_browser,
    resident_cdp_url,
)

log = structlog.get_logger()

ENV_PROFILE_DIR = "GEO_DOUBAO_PROFILE_DIR"
ENV_PROXY_URL = "GEO_DOUBAO_PROXY_URL"
ENV_EVIDENCE_DIR = "GEO_DOUBAO_EVIDENCE_DIR"
ENV_SHARED_EVIDENCE_DIR = "GEO_ADAPTER_EVIDENCE_DIR"  # 多平台共享证据目录（兜底于专属项之后）
ENV_HEADLESS = "GEO_DOUBAO_HEADLESS"
ENV_SOURCE_SCREENSHOT_LIMIT = "GEO_DOUBAO_SOURCE_SCREENSHOT_LIMIT"
ENV_CHAT_TIMEOUT_S = "GEO_DOUBAO_CHAT_TIMEOUT_S"

# 平台 slug：常驻浏览器契约层（resident_browser）按它解析 GEO_<PLATFORM>_CDP_URL。
_PLATFORM_SLUG = "doubao"

_DEFAULT_EVIDENCE_DIR = Path(__file__).resolve().parents[2] / "runtime" / "doubao-evidence"
_HEARTBEAT_INTERVAL_S = 10.0  # workflow heartbeat_timeout=30s，泵频 ≤15s 硬约束
_NAV_TIMEOUT_MS = 25_000
_DEFAULT_CHAT_TIMEOUT_S = 600.0  # 流式完成预算缺省（deep_think 远长于 normal；env 可配）

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

# 「新对话」入口（新会话纪律；命中第一个可见者即点，顺序即优先级）
_NEW_CHAT_SELECTORS: tuple[str, ...] = (
    '[aria-label*="新对话"]',
    'button:has-text("新对话")',
    '[role="button"]:has-text("新对话")',
    'a:has-text("新对话")',
)

# 新会话验证：页面已存在消息节点计数（>0 = 旧会话/进行中的旧回答）。
# data-testid="message_text_content" 是实测的助手气泡选择器（权威信号）；
# 其余为保守补充（匹配不到=0，无害）。
_CHAT_MESSAGE_COUNT_JS = r"""() => {
  const sels = [
    '[data-testid="message_text_content"]',
    '[data-message-author-role="assistant"]',
    '[data-message-author-role="user"]'
  ];
  let n = 0;
  for (const s of sels) n += document.querySelectorAll(s).length;
  return n;
}"""

# 拟人化节奏区间（秒）——端详页面 / 发送前通读 / 切模式后回神 / 新会话切换 /
# picker 悬停 / 读弹层菜单
_PACE_PAGE_READY_S = (0.6, 1.8)
_PACE_BEFORE_SEND_S = (0.5, 1.5)
_PACE_AFTER_TOGGLE_S = (0.4, 1.0)
_PACE_AFTER_NEW_CHAT_S = (0.6, 1.2)
_PACE_PICKER_HOVER_S = (0.3, 0.9)
_PACE_MENU_READ_S = (0.4, 1.0)

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
    chat_timeout_s: float = _DEFAULT_CHAT_TIMEOUT_S

    @classmethod
    def from_env(cls, *, proxy_url_override: str | None = None) -> DoubaoAdapterConfig:
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
        # W8 常驻 CDP：URL 格式错误=配置错误（fail-closed 不重试，与 proxy 同款门）；
        # 合法非空时 _browser_session 走 attach，空/未设置走旧 launch。
        try:
            resident_cdp_url(_PLATFORM_SLUG)
        except ValueError as exc:
            raise ApplicationError(
                str(exc),
                type="adapter_not_configured",
                non_retryable=True,
            ) from None
        raw_evidence = (
            os.environ.get(ENV_EVIDENCE_DIR, "").strip()
            or os.environ.get(ENV_SHARED_EVIDENCE_DIR, "").strip()
        )
        evidence_dir = Path(raw_evidence) if raw_evidence else _DEFAULT_EVIDENCE_DIR
        headless = os.environ.get(ENV_HEADLESS, "1").strip() != "0"
        raw_timeout = os.environ.get(ENV_CHAT_TIMEOUT_S, "").strip()
        chat_timeout_s = _DEFAULT_CHAT_TIMEOUT_S
        if raw_timeout:
            try:
                chat_timeout_s = float(raw_timeout)
            except ValueError:
                raise ApplicationError(
                    f"{ENV_CHAT_TIMEOUT_S} is not a number: {raw_timeout!r}",
                    type="adapter_not_configured",
                    non_retryable=True,
                ) from None
            if not 30.0 <= chat_timeout_s <= 3_600.0:
                raise ApplicationError(
                    f"{ENV_CHAT_TIMEOUT_S} must be within [30, 3600] seconds",
                    type="adapter_not_configured",
                    non_retryable=True,
                )
        return cls(
            profile_dir=profile_dir,
            proxy_url=proxy_url,
            evidence_dir=evidence_dir,
            headless=headless,
            chat_timeout_s=chat_timeout_s,
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


class _DeepThinkToggleFailed(RuntimeError):
    """deep_think 模式 picker 无法确认启用（non_retryable；绝不静默回退 normal）。"""

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
    evidence: list[CollectionEvidenceRef] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    # 平台真实检索词（W1）：[{"query": ..., "ordinal": ...}]，按 SSE 出现顺序；
    # 无检索词/解析失败为空列表（诚实，不编造）。
    search_queries: list[dict[str, Any]] = field(default_factory=list)


class _BrowserSession(Protocol):
    """Playwright 交互隔离面：测试注入 fake，绝不启动真浏览器。"""

    def collect(
        self, query: str, on_stage: Callable[[str], None], *, mode: str = "normal"
    ) -> CollectedAnswer: ...

    def collect_batch(
        self, items: list[DoubaoBatchItemSpec], on_stage: Callable[[str], None]
    ) -> list[DoubaoBatchItemOutcome]: ...


SessionFactory = Callable[[DoubaoAdapterConfig, Path, str], _BrowserSession]


@dataclass(frozen=True)
class DoubaoBatchItemSpec:
    """batch 内单题输入（session 层）：查询/mode + 证据文件名片段。"""

    business_key: str
    query: str
    mode: str
    file_stem: str


@dataclass
class DoubaoBatchItemOutcome:
    """batch 内单题结果（session 层）：ok 携带 CollectedAnswer；失败/未执行
    携带 error_type/error_message/可选存证截图路径。status 词表与
    CollectionBatchItemResult 对齐（ok/wall/incomplete/aborted）。"""

    business_key: str
    status: str
    answer: CollectedAnswer | None = None
    error_type: str | None = None
    error_message: str | None = None
    evidence_path: Path | None = None


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


@activity.defn(name="collect_doubao_batch")
async def collect_doubao_batch(batch: CollectionBatchInput) -> CollectionBatchResult:
    """豆包 batch 采集注册实现（workers/main.py 按 GEO_COLLECTION_ADAPTER 门控选择）。

    整个 batch 在同一个常驻浏览器会话里顺序完成（run 级会话复用）；墙/失败
    诚实记录在 per-item 结果里（本 activity 不因墙类失败 raise），仅配置类
    错误（adapter_not_configured/unsupported_mode）raise。
    """
    try:
        attempt = activity.info().attempt
    except RuntimeError:
        attempt = 1
    # 不传 session_factory：与 run_doubao_collection 的生产约定一致（dispatcher
    # 只传业务参数）——缺省 None 才走 to_thread 分支跑真实 sync 浏览器；显式传
    # _PlaywrightDoubaoSession 会误判为注入 fake，在事件循环里直跑 sync API。
    return await run_doubao_batch(
        batch,
        heartbeat=activity.heartbeat,
        attempt=attempt,
    )


def _batch_result_with_pause(results: list[CollectionBatchItemResult]) -> CollectionBatchResult:
    """等长结果 → CollectionBatchResult；首个 wall_captcha 题标注 captcha_pause。

    captcha-assist-v1：撞码是可人工恢复的暂停点而非终局失败——workflow 见到
    pause 会挂起等人工接管、从 resume_index 起重采；results 仍保持等长全占
    位（未打补丁的旧 workflow 重放本结果，行为与今天完全一致）。非撞码失败
    （登录墙/incomplete/toggle）不产生 pause，维持现行语义。
    """
    for index, result in enumerate(results):
        if result.status == "wall" and result.error_type == "wall_captcha":
            return CollectionBatchResult(
                results=results,
                captcha_pause=CaptchaPause(
                    resume_index=index,
                    business_key=result.business_key,
                    wall_type=result.error_type,
                    evidence_ref=result.screenshot_ref,
                ),
            )
    return CollectionBatchResult(results=results)


async def run_doubao_batch(
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
        session_factory = _PlaywrightDoubaoSession
    if heartbeat is None:

        def heartbeat(payload: dict[str, Any]) -> None:
            del payload

    for item in batch.items:
        if item.mode not in ("normal", "deep_think"):
            raise ApplicationError(
                f"unsupported mode: {item.mode!r} (expected 'normal' or 'deep_think')",
                type="unsupported_mode",
                non_retryable=True,
            )
    config = DoubaoAdapterConfig.from_env(proxy_url_override=proxy_url_override)
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    batch_stem = f"batch-{_safe_stem(batch.run_pub_id)}-a{attempt}"
    specs = [
        DoubaoBatchItemSpec(
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

    def _blocking() -> list[DoubaoBatchItemOutcome]:
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
        # session 级墙（导航后登录墙/cloak）：一题未发，全题诚实记 wall。
        # wall_captcha 同样经 _batch_result_with_pause 标注 resume_index=0——
        # batch 开场即撞码（活性窗口外首发）也走人工接管续跑。
        evidence_suffix = f"; evidence={wall.evidence_path}" if wall.evidence_path else ""
        bound.info("doubao_batch_session_wall", wall_type=wall.wall_type, stage=progress["stage"])
        return _batch_result_with_pause(
            [
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
    except _DeepThinkToggleFailed as toggle:
        # 防御：toggle 失败应在题内转 outcome；逃出即按 session 级 wall 诚实记录。
        evidence_suffix = f"; evidence={toggle.evidence_path}" if toggle.evidence_path else ""
        bound.info("doubao_batch_session_toggle_failed", stage=progress["stage"])
        return CollectionBatchResult(
            results=[
                _failure_batch_item(
                    item,
                    status="wall",
                    error_type="deep_think_toggle_failed",
                    error_message=f"{toggle}{evidence_suffix}",
                    evidence_path=toggle.evidence_path,
                )
                for item in batch.items
            ]
        )
    except _IncompleteCapture as inc:
        # session 级临时故障（浏览器启动失败等）：一题未发，raise 走 batch 重试。
        evidence_suffix = f"; evidence={inc.evidence_path}" if inc.evidence_path else ""
        bound.info("doubao_batch_session_incomplete", reason=str(inc), stage=progress["stage"])
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
        "doubao_batch_done",
        ok=sum(1 for r in results if r.status == "ok"),
        failed=sum(1 for r in results if r.status != "ok"),
        stage=progress["stage"],
    )
    return _batch_result_with_pause(results)


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
    item: CollectionTaskInput, outcome: DoubaoBatchItemOutcome
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


async def run_doubao_collection(
    item: CollectionTaskInput,
    *,
    session_factory: SessionFactory | None = None,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
    proxy_url_override: str | None = None,
    attempt: int = 1,
) -> CollectionTaskResult:
    """activity 核心：配置门 → mode 门 → to_thread 跑浏览器 → 墙/结果映射。

    与 activity 上下文解耦（heartbeat/attempt 注入），测试全程 mock 浏览器层。
    session_factory/heartbeat 缺省用真实实现与 no-op——platform_registry dispatcher
    只传 ``(item, heartbeat=...)``，与本签名对齐。
    """
    uses_default_session = session_factory is None
    if session_factory is None:
        session_factory = _PlaywrightDoubaoSession
    if heartbeat is None:

        def heartbeat(payload: dict[str, Any]) -> None:
            del payload

    if item.mode not in ("normal", "deep_think"):
        raise ApplicationError(
            f"unsupported mode: {item.mode!r} (expected 'normal' or 'deep_think')",
            type="unsupported_mode",
            non_retryable=True,
        )
    config = DoubaoAdapterConfig.from_env(proxy_url_override=proxy_url_override)
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
        return session.collect(
            item.query, on_stage=lambda s: progress.__setitem__("stage", s), mode=item.mode
        )

    try:
        if uses_default_session:
            thread = asyncio.ensure_future(asyncio.to_thread(_blocking))
            while True:
                heartbeat({"business_key": item.business_key, "stage": progress["stage"]})
                done, _pending = await asyncio.wait({thread}, timeout=_HEARTBEAT_INTERVAL_S)
                if done:
                    break
            collected = thread.result()
        else:
            heartbeat({"business_key": item.business_key, "stage": progress["stage"]})
            collected = _blocking()
    except _WallError as wall:
        evidence_suffix = f"; evidence={wall.evidence_path}" if wall.evidence_path else ""
        bound.info("doubao_wall", wall_type=wall.wall_type, stage=progress["stage"])
        raise ApplicationError(
            f"{wall}{evidence_suffix}", type=wall.wall_type, non_retryable=True
        ) from wall
    except _DeepThinkToggleFailed as toggle:
        evidence_suffix = f"; evidence={toggle.evidence_path}" if toggle.evidence_path else ""
        bound.info("doubao_deep_think_toggle_failed", stage=progress["stage"])
        raise ApplicationError(
            f"{toggle}{evidence_suffix}", type="deep_think_toggle_failed", non_retryable=True
        ) from toggle
    except _IncompleteCapture as inc:
        evidence_suffix = f"; evidence={inc.evidence_path}" if inc.evidence_path else ""
        bound.info("doubao_capture_incomplete", reason=str(inc), stage=progress["stage"])
        raise ApplicationError(
            f"{inc}{evidence_suffix}", type="answer_capture_incomplete"
        ) from inc
    bound.info(
        "doubao_collect_ok",
        answer_len=len(collected.answer_text),
        references=len(collected.references),
        stage=progress["stage"],
    )
    return _task_result_from_collected(item, collected)


def _task_result_from_collected(
    item: CollectionTaskInput, collected: CollectedAnswer
) -> CollectionTaskResult:
    """CollectedAnswer → CollectionTaskResult 映射（answer 组装/citations/证据
    前置/出界 DLP 自检）。run_doubao_collection 与 batch per-item ok 映射共用。"""
    answer_text = _compose_answer_text(collected.answer_text, collected.references)
    screenshot_ref = f"file://{collected.screenshot_path}"
    citations = _citation_payloads(collected.references)
    evidence = list(collected.evidence)
    if not any(ref.kind == "answer_screenshot" for ref in evidence):
        evidence.insert(
            0,
            CollectionEvidenceRef(
                kind="answer_screenshot",
                path=str(collected.screenshot_path),
                relation_type="answer_page",
                mime_type="image/png",
                source_url=_CHAT_URL,
            ),
        )
    # DLP 统一由 persist 层脱敏处理（单一权威边界，2026-08-06 起）。
    return CollectionTaskResult(
        business_key=item.business_key,
        answer_text=answer_text,
        screenshot_ref=screenshot_ref,
        quality_state="live_valid",
        citations=citations,
        evidence=evidence,
        search_queries=collected.search_queries,
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


def _external_http_url(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 2_048:
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not host:
            return None
        if parsed.username or parsed.password:
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not address.is_global:
                return None
    except ValueError:
        return None
    return value


def _citation_payloads(references: list[dict[str, Any]]) -> list[dict[str, str | None]]:
    citations: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for reference in references[:100]:
        url = _external_http_url(reference.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        title = str(reference.get("title") or reference.get("sitename") or "").strip()
        cited_text = str(reference.get("summary") or "").strip()
        citations.append(
            {
                "url": url,
                "title": title[:300] or None,
                "cited_text": cited_text[:2_000] or None,
            }
        )
    return citations


# ---------------------------------------------------------------------------
# Playwright 实现（sync，全部跑在 to_thread 线程里）
# ---------------------------------------------------------------------------


def _clean_profile_crash_state(profile_dir: Path) -> bool:
    """幂等清理 Chromium profile 的异常退出标记。返回是否改写了 Preferences。

    真人正常关浏览器后 ``profile.exit_type`` 就是 ``"Normal"``；本函数把
    ``exit_type="Normal"`` / ``exited_cleanly=true`` 写回，其余键原样保留。
    Preferences 不存在 / JSON 损坏 / 结构异常 → 不动文件返回 False。
    原子写（同目录 tmp + os.replace），不截断原文件。
    """
    candidates = (profile_dir / "Default" / "Preferences", profile_dir / "Preferences")
    prefs = next((p for p in candidates if p.is_file()), None)
    if prefs is None:
        return False
    try:
        data = json.loads(prefs.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    profile = data.get("profile")
    if not isinstance(profile, dict):
        profile = {}
        data["profile"] = profile
    if profile.get("exit_type") == "Normal" and profile.get("exited_cleanly") is True:
        return False  # 幂等：已是干净状态
    profile["exit_type"] = "Normal"
    profile["exited_cleanly"] = True
    tmp = prefs.with_name(f"{prefs.name}.geo-tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, prefs)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


class _PlaywrightDoubaoSession:
    """豆包网页采集的 sync Playwright 实现（persistent context）。

    单题（``collect``，per-task 老路径）与 run 级会话复用（``collect_batch``）
    共享同一套 per-item 主体 ``_collect_one``——绝不复制出两套：

    - ``collect``：一次 launch、一题、关闭（老行为不变）；
    - ``collect_batch``：一次 launch_persistent_context，N 题在同一常驻会话/
      同一标签页里顺序完成（真人在同一浏览器窗口里连续聊天——每题落在全新
      会话但绝不重开浏览器）；每题成功后做「阅读停顿」（拟人读完回答：滚动
      浏览 + 停留）；结束统一 context.close() + 崩溃标记清理（全路径 finally）。

    batch 失败语义：题级墙/incomplete 转 outcome——该题诚实失败、后续题
    aborted（零浏览器交互：真人撞墙后会停下，不编造不硬闯），结果列表与
    输入等长同序；session 建立阶段（launch/navigate/登录墙检查）的异常
    原样逃出，由 activity 层按 session 级语义处理（一题未发）。
    """

    def __init__(self, config: DoubaoAdapterConfig, evidence_dir: Path, file_stem: str) -> None:
        self._config = config
        self._evidence_dir = evidence_dir
        self._file_stem = file_stem
        # 拟人化：本 session 专用 RNG（真随机；测试在 human_like 层 seeded）与
        # 光标位置追踪（连续轨迹，避免每次点击都从合成起点重新起跳）。
        self._rng = random.Random()
        self._mouse_pos: tuple[float, float] | None = None

    def collect(
        self, query: str, on_stage: Callable[[str], None], *, mode: str = "normal"
    ) -> CollectedAnswer:
        spec = DoubaoBatchItemSpec(
            business_key=self._file_stem,
            query=query,
            mode=mode,
            file_stem=self._file_stem,
        )
        with self._browser_session(on_stage) as (context, page, pw_timeout, driver):
            return self._collect_one(
                context, page, spec, on_stage, pw_timeout=pw_timeout, driver=driver
            )

    def collect_batch(
        self, items: list[DoubaoBatchItemSpec], on_stage: Callable[[str], None]
    ) -> list[DoubaoBatchItemOutcome]:
        outcomes: list[DoubaoBatchItemOutcome] = []
        with self._browser_session(on_stage) as (context, page, pw_timeout, driver):
            for index, spec in enumerate(items):
                on_stage(f"item:{spec.business_key}")
                try:
                    answer = self._collect_one(
                        context, page, spec, on_stage, pw_timeout=pw_timeout, driver=driver
                    )
                except _WallError as wall:
                    outcomes.append(self._failure_outcome(spec, "wall", wall.wall_type, wall))
                    outcomes.extend(
                        self._aborted_outcome(rest, spec, wall.wall_type)
                        for rest in items[index + 1 :]
                    )
                    return outcomes
                except _DeepThinkToggleFailed as toggle:
                    outcomes.append(
                        self._failure_outcome(spec, "wall", "deep_think_toggle_failed", toggle)
                    )
                    outcomes.extend(
                        self._aborted_outcome(rest, spec, "deep_think_toggle_failed")
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
                    DoubaoBatchItemOutcome(
                        business_key=spec.business_key, status="ok", answer=answer
                    )
                )
                # 阅读停顿：拟人读完回答（滚动浏览 + 停留 8-25s 抖动）——题间天然
                # 间隔，也产出真实浏览信号；最后一题同样停留（真人读完才关浏览器）。
                pause_s = self._reading_pause(page)
                log.info(
                    "doubao_read_pause",
                    business_key=spec.business_key,
                    seconds=round(pause_s, 2),
                )
        return outcomes

    @staticmethod
    def _failure_outcome(
        spec: DoubaoBatchItemSpec,
        status: str,
        error_type: str,
        exc: _WallError | _IncompleteCapture | _DeepThinkToggleFailed,
    ) -> DoubaoBatchItemOutcome:
        return DoubaoBatchItemOutcome(
            business_key=spec.business_key,
            status=status,
            error_type=error_type,
            error_message=str(exc),
            evidence_path=exc.evidence_path,
        )

    @staticmethod
    def _aborted_outcome(
        spec: DoubaoBatchItemSpec, failed_spec: DoubaoBatchItemSpec, error_type: str | None
    ) -> DoubaoBatchItemOutcome:
        # 真人撞墙后会停下：本题未执行（零浏览器交互），诚实标记不编造不硬闯。
        return DoubaoBatchItemOutcome(
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
    ) -> Iterator[tuple[Any, Any, type[Exception], str]]:
        """attach-or-launch + 导航 + 登录墙检查 → yield (context, page, PWTimeout, driver)。

        双路径（W8，契约层 ``resident_browser.platform_browser`` 提供互斥锁与
        attach/launch 分派）：

        - ``GEO_DOUBAO_CDP_URL`` 非空 → **attach**：连接 supervisor 管理的常驻
          Chromium；退出只断开 CDP 连接（契约管），**跳过** context.close 与
          崩溃标记清理（常驻进程/profile 不归本适配器）。
        - 未配置 → **launch**（旧行为一字不差）：启动前/close 后各一次
          _clean_profile_crash_state；成功/墙/超时/异常所有退出路径都经 finally
          context.close()（异常如实记日志，不静默吞）。契约层 finally 的兜底
          二次 close 对真实 patchright 是幂等 no-op（``_closing_or_closed`` 守卫）。

        attach 断连/常驻浏览器崩溃 → 按契约包装成 browser-launch-failed
        （_IncompleteCapture，诚实可重试）；supervisor 重启后自愈。
        """
        # 延迟导入：模块加载不硬依赖浏览器驱动（worker 未装依赖时仍可注册 fail-closed 实现）。
        # 驱动首选 patchright（旧链生产同款，反检测补丁版）：vanilla playwright 的
        # webdriver 指纹会触发豆包风控静默吞发送（composer 不清空、/completion 不触发，
        # 旧链 2026-07-15 live 实证）——这正是 v1 冒烟 send-not-accepted 的根因。
        driver, sync_playwright, PWTimeout = load_sync_browser_driver()

        on_stage("browser_launch")
        with sync_playwright() as pw:
            resident = resident_cdp_url(_PLATFORM_SLUG) is not None
            if not resident:
                # 启动前愈合前任进程的崩溃标记（activity 取消/SIGKILL 会绕过正常 close，
                # Chromium 未写回 exit_type=Normal → 下次启动弹「Restore pages?」）。
                # 幂等纯文件操作，失败不阻塞启动（close 后还有一次兜底清理）。
                # attach 路径跳过：常驻 profile 归 supervisor/launcher 管。
                try:
                    _clean_profile_crash_state(self._config.profile_dir)
                except Exception:
                    pass

            def _launch() -> tuple[Any, Any]:
                # 仅 launch 路径被契约调用；打开与 profile 清理仍归本适配器（旧语义）。
                try:
                    context = pw.chromium.launch_persistent_context(
                        user_data_dir=str(self._config.profile_dir),
                        headless=self._config.headless,
                        proxy=(
                            _parse_proxy(self._config.proxy_url) if self._config.proxy_url else None
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
                return context, (context.pages[0] if context.pages else context.new_page())

            with contextlib.ExitStack() as stack:
                try:
                    context, page, is_resident = stack.enter_context(
                        platform_browser(pw, platform=_PLATFORM_SLUG, launch=_launch)
                    )
                except (_IncompleteCapture, BrowserBusyError):
                    # launch 失败已包装（可重试）；锁排队超时如实上报（契约词表）。
                    raise
                except Exception as exc:
                    # attach 断连/常驻崩溃：按契约「调用方按 browser_launch_failed
                    # 诚实重试」；supervisor 重启后自愈。
                    raise _IncompleteCapture(
                        f"browser-launch-failed({driver}): {type(exc).__name__}: {exc}"
                    ) from exc
                try:
                    context.set_default_timeout(_NAV_TIMEOUT_MS)

                    on_stage("navigate")
                    try:
                        page.goto(_CHAT_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
                    except PWTimeout:
                        page.goto(_HOME_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
                    page.wait_for_timeout(6_000)  # SPA + 反爬 JS 挂载（旧链同款 settle）
                    _try_close_overlays(page, self._rng)
                    if _detect_login_wall(page):
                        raise _WallError(
                            "wall_login_required",
                            "doubao login wall detected right after navigation",
                            self._shot(page, "login"),
                        )
                    yield context, page, PWTimeout, driver
                finally:
                    if not is_resident:
                        # 优雅关闭（launch 路径原语义）：成功/墙/超时/异常所有退出路径都
                        # 走到这里。close 异常如实记日志（不再静默吞——吞掉=浏览器被
                        # driver 强杀=profile 留崩溃标记）。
                        try:
                            context.close()
                        except Exception as exc:
                            log.warning(
                                "doubao_context_close_failed",
                                business_key=self._file_stem,
                                error=f"{type(exc).__name__}: {exc}",
                            )
                        # close 后兜底清理崩溃标记（覆盖 close 期竞态）；幂等纯文件操作。
                        try:
                            _clean_profile_crash_state(self._config.profile_dir)
                        except Exception as exc:
                            log.warning(
                                "doubao_profile_crash_clean_failed",
                                business_key=self._file_stem,
                                error=f"{type(exc).__name__}: {exc}",
                            )

    def _collect_one(
        self,
        context: Any,
        page: Any,
        spec: DoubaoBatchItemSpec,
        on_stage: Callable[[str], None],
        *,
        pw_timeout: type[Exception],
        driver: str,
    ) -> CollectedAnswer:
        """单题主体：await_input → fresh_chat → [deep_think toggle] → 拟人输入/
        发送 → SSE 捕获/组装/证据落盘。per-task 单题与 batch 每题共用。"""
        capture = _CompletionCapture(context, page)
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

            try:
                input_loc = _wait_for_input_or_cloak(page, timeout_ms=15_000)
            except _Cloaked as exc:
                raise _WallError(
                    "wall_send",
                    f"doubao_cloaked: {exc}",
                    _shot("cloaked"),
                ) from exc
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
            # 豆包会在 SPA settle 后异步弹出「下载电脑版」模态。输入框仍会被
            # locator 判定为 visible，但模态会截获发送按钮，最终表现为
            # composer 一直不清空。await_input 后再收一次，覆盖迟到弹层。
            _try_close_overlays(page, self._rng)
            if spec.mode == "deep_think":
                # UI toggle 启用深度思考（移植自旧链 server/proxyllm/deep_think.py）。
                # 必须在打字/发送之前完成并经后置校验确认；确认不了即诚实失败，
                # 绝不静默回退 normal（那会把 normal 答案错标成 deep_think）。
                on_stage("enable_deep_think")
                if not _try_enable_deep_think(page, self._rng):
                    raise _DeepThinkToggleFailed(
                        "deep_think mode picker could not be engaged "
                        "(selector drift or mode unavailable)",
                        _shot("deep_think"),
                    )
                _pace(*_PACE_AFTER_TOGGLE_S)  # 切完模式回神再回到输入框
                on_stage("typing")
            # 点输入框聚焦（贝塞尔移动 + 悬停 + 框内随机偏移点击）。human_click
            # 拿不到布局时内部回退原生 click；仍失败则原样抛出=诚实失败。
            clicked_at = human_click(input_loc, page, self._rng, start=self._mouse_pos)
            if clicked_at is not None:
                self._mouse_pos = clicked_at
            human_type(input_loc, spec.query, self._rng)
            # 发送前通读一遍（原实现 type 后固定 800ms 即发送=秒发指纹）。
            _pace(*_PACE_BEFORE_SEND_S)

            submit = _submit_and_confirm(
                page, input_loc, self._rng, pace=_pace, start=self._mouse_pos
            )
            if not submit.get("submitted"):
                raise _WallError(
                    "wall_send",
                    "send-not-accepted: composer still populated after "
                    f"{submit.get('attempts', '?')} send attempts (submission swallowed)",
                    _shot("send_wall"),
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
                        _shot("captcha"),
                    )
                if (
                    capture.has_completion_started()
                    and time.monotonic() - challenge_start >= 3.5
                ):
                    break
                page.wait_for_timeout(500)

            on_stage("await_stream")
            meta = capture.wait_finish(
                page, appearance_timeout_s=20.0, timeout_s=self._config.chat_timeout_s
            )
            answer_text = ""
            references: list[dict[str, Any]] = []
            search_queries: list[dict[str, Any]] = []
            sse_trace: dict[str, Any] | None = None
            sse_body = capture.latest_body()
            if sse_body:
                rich = _rich_record_from_sse(sse_body)
                if rich is not None:
                    answer_text = str(rich.get("answer_text") or "").strip()
                    references = list(rich.get("references") or [])
                    # W1：结构化 trace（thinking/search/queries/stats，非全量原文）
                    sse_trace = _sse_trace_from_body(sse_body)
                    if sse_trace is not None:
                        search_queries = list(sse_trace.get("queries") or [])
            if not answer_text and meta.get("found"):
                # SSE 捕获竞态失败时的 DOM 兜底（旧链同款回退路径）
                answer_text = _extract_response_text(page, spec.query)
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
                    "send-accepted-no-completion: composer cleared (submission accepted) "
                    "but no /chat/completion stream fired within timeout — likely "
                    "content-filter or silent server-side drop",
                    _shot("no_stream"),
                )
            if not meta.get("finished"):
                raise _IncompleteCapture(
                    "stream-open-at-timeout: /chat/completion stream still open after "
                    f"budget ({meta.get('bytes_received', 0)} bytes captured) — answer "
                    "would be truncated; failing honestly",
                    _shot("truncated"),
                )
            if not answer_text:
                raise _IncompleteCapture(
                    "answer-empty-after-finished-stream: neither SSE assembly nor DOM "
                    "fallback produced answer text",
                    _shot("empty_answer"),
                )

            on_stage("screenshot")
            shot_path = self._evidence_dir / f"{spec.file_stem}.png"
            _capture_full_page(page, shot_path)
            if not shot_path.exists():
                raise _IncompleteCapture("evidence-screenshot-failed: no file written")
            evidence = [
                CollectionEvidenceRef(
                    kind="answer_screenshot",
                    path=str(shot_path),
                    relation_type="answer_page",
                    mime_type="image/png",
                    source_url=_CHAT_URL,
                )
            ]

            # W1：SSE 结构化 trace 落盘进证据链（kind="sse"）。写盘失败不拖垮
            # 已成功的采集——如实 warning 且不出该证据（绝不出残缺/编造证据）。
            if sse_trace is not None:
                trace_path = self._evidence_dir / f"{spec.file_stem}-sse-trace.json"
                try:
                    trace_path.write_text(
                        json.dumps(
                            sse_trace,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        encoding="utf-8",
                    )
                except OSError as exc:
                    log.warning(
                        "doubao_sse_trace_write_failed",
                        business_key=spec.business_key,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                else:
                    evidence.append(
                        CollectionEvidenceRef(
                            kind="sse",
                            path=str(trace_path),
                            relation_type="answer_sse_trace",
                            mime_type="application/json",
                            source_url=_CHAT_URL,
                        )
                    )

            # Official sharing is intentionally best-effort: a transient share-panel
            # redesign must not discard a valid answer, but each failure is explicit in
            # structured worker logs and a successful export enters the evidence chain.
            on_stage("share_export")
            # legacy share_export（server/proxyllm 只读移植源，bridge 动态加载）内部
            # 大量裸 mouse.click / locator.click：包一层 facade 换成拟人化路径。
            human_page = _HumanizedPageFacade(page, self._rng, start=self._mouse_pos)
            share_image_path = self._evidence_dir / f"{spec.file_stem}-share.png"
            share_image_audit: dict[str, Any]
            share_link_audit: dict[str, Any]
            try:
                share_image_audit = capture_share_image(human_page, share_image_path)
            except Exception as exc:
                share_image_audit = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            try:
                share_link_audit = capture_share_link(human_page)
            except Exception as exc:
                share_link_audit = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            share_url = _validated_doubao_share_url(share_link_audit.get("url"))
            if share_image_audit.get("ok") and share_image_path.is_file():
                evidence.append(
                    CollectionEvidenceRef(
                        kind="share_image",
                        path=str(share_image_path),
                        relation_type="official_share_image",
                        mime_type="image/png",
                        source_url=share_url,
                    )
                )
            if share_link_audit.get("ok") and share_url:
                share_link_path = self._evidence_dir / f"{spec.file_stem}-share-link.json"
                share_link_path.write_text(
                    json.dumps(
                        {
                            "channel": share_link_audit.get("channel"),
                            "url": share_url,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
                evidence.append(
                    CollectionEvidenceRef(
                        kind="share_link",
                        path=str(share_link_path),
                        relation_type="official_share_link",
                        mime_type="application/json",
                        source_url=share_url,
                    )
                )
            if not share_image_audit.get("ok") or not share_url:
                log.warning(
                    "doubao_share_export_partial",
                    business_key=spec.business_key,
                    image_ok=bool(share_image_audit.get("ok")),
                    image_error=str(share_image_audit.get("error") or "")[:300],
                    link_ok=bool(share_link_audit.get("ok")),
                    link_error=str(share_link_audit.get("error") or "")[:300],
                )

            on_stage("source_screenshots")
            source_evidence, source_audit = _capture_source_screenshots(
                context,
                references,
                evidence_dir=self._evidence_dir,
                file_stem=spec.file_stem,
                timeout_error=pw_timeout,
            )
            evidence.extend(source_evidence)
            return CollectedAnswer(
                answer_text=answer_text,
                references=references,
                screenshot_path=shot_path,
                evidence=evidence,
                meta={
                    "stream": meta,
                    "sse_body_bytes": len(sse_body),
                    "driver": driver,
                    "share_image": share_image_audit,
                    "share_link": share_link_audit,
                    "source_screenshots": source_audit,
                    "sse_trace_persisted": sse_trace is not None,
                },
                search_queries=search_queries,
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


class _HumanizedMouse:
    """legacy share_export 的 ``page.mouse`` 替身：瞬移→贝塞尔轨迹，裸点→移动+悬停+点击。

    追踪 ``pos``（最近落点）让连续多次移动/点击的轨迹首尾相接。
    """

    def __init__(
        self, page: Any, rng: random.Random, start: tuple[float, float] | None
    ) -> None:
        self._page = page
        self._rng = rng
        self.pos = start

    def move(self, x: float, y: float, **_kwargs: Any) -> None:
        self.pos = human_move_to(self._page, float(x), float(y), self._rng, start=self.pos)

    def click(self, x: float, y: float, **kwargs: Any) -> None:
        self.pos = human_move_to(self._page, float(x), float(y), self._rng, start=self.pos)
        self._page.wait_for_timeout(self._rng.uniform(80.0, 300.0))
        call = dict(kwargs)
        call.setdefault("delay", self._rng.randint(30, 90))
        self._page.mouse.click(float(x), float(y), **call)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._page.mouse, name)


class _HumanizedLocator:
    """``locator.click`` → human_click；链式方法（first/last/nth/filter）保持包装。"""

    def __init__(self, locator: Any, page: Any, rng: random.Random, mouse: _HumanizedMouse) -> None:
        self._locator = locator
        self._page = page
        self._rng = rng
        self._mouse = mouse

    def _wrap(self, locator: Any) -> _HumanizedLocator:
        return _HumanizedLocator(locator, self._page, self._rng, self._mouse)

    @property
    def first(self) -> _HumanizedLocator:
        return self._wrap(self._locator.first)

    @property
    def last(self) -> _HumanizedLocator:
        return self._wrap(self._locator.last)

    def nth(self, index: int) -> _HumanizedLocator:
        return self._wrap(self._locator.nth(index))

    def filter(self, **kwargs: Any) -> _HumanizedLocator:
        return self._wrap(self._locator.filter(**kwargs))

    def click(self, **kwargs: Any) -> None:
        # timeout 等 kwargs 透传给 human_click 的原生兜底分支，保持原调用语义。
        pos = human_click(
            self._locator, self._page, self._rng, start=self._mouse.pos, click_kwargs=kwargs
        )
        if pos is not None:
            self._mouse.pos = pos

    def __getattr__(self, name: str) -> Any:
        return getattr(self._locator, name)


class _HumanizedPageFacade:
    """传给 legacy share_export 的 page 替身。

    server/proxyllm/share_export.py 是只读移植源（doubao_share_bridge 动态加载），
    其内部大量裸 ``page.mouse.click(cx, cy)`` / ``locator.click`` 属机器人指纹。
    本 facade 只拦截 mouse 与 locator 工厂方法，其余（evaluate / expect_download /
    expect_response / context / keyboard / goto / screenshot …）原样透传。
    """

    def __init__(
        self, page: Any, rng: random.Random, start: tuple[float, float] | None = None
    ) -> None:
        self._page = page
        self._rng = rng
        self.mouse = _HumanizedMouse(page, rng, start)

    def locator(self, *args: Any, **kwargs: Any) -> _HumanizedLocator:
        return _HumanizedLocator(
            self._page.locator(*args, **kwargs), self._page, self._rng, self.mouse
        )

    def get_by_role(self, *args: Any, **kwargs: Any) -> _HumanizedLocator:
        return _HumanizedLocator(
            self._page.get_by_role(*args, **kwargs), self._page, self._rng, self.mouse
        )

    def get_by_text(self, *args: Any, **kwargs: Any) -> _HumanizedLocator:
        return _HumanizedLocator(
            self._page.get_by_text(*args, **kwargs), self._page, self._rng, self.mouse
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._page, name)


def _validated_doubao_share_url(value: object) -> str | None:
    url = _external_http_url(value)
    if not url:
        return None
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"doubao.com", "www.doubao.com"}:
        return None
    if not parsed.path.startswith("/thread/"):
        return None
    return url


def _source_screenshot_limit() -> int:
    try:
        configured = int(os.environ.get(ENV_SOURCE_SCREENSHOT_LIMIT, "3"))
    except ValueError:
        return 3
    return min(max(configured, 0), 10)


def _capture_source_screenshots(
    context: Any,
    references: list[dict[str, Any]],
    *,
    evidence_dir: Path,
    file_stem: str,
    timeout_error: type[Exception],
) -> tuple[list[CollectionEvidenceRef], dict[str, Any]]:
    """Capture cited pages and visibly annotate title + mentioned paragraph metadata."""
    limit = _source_screenshot_limit()
    output: list[CollectionEvidenceRef] = []
    failures: list[dict[str, Any]] = []
    seen: set[str] = set()
    candidates: list[tuple[int, dict[str, Any], str]] = []
    for ordinal, reference in enumerate(references, 1):
        url = _external_http_url(reference.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        candidates.append((ordinal, reference, url))
        if len(candidates) >= limit:
            break
    for ordinal, reference, url in candidates:
        source_page = context.new_page()
        try:
            try:
                source_page.goto(url, wait_until="domcontentloaded", timeout=15_000)
            except timeout_error:
                # A useful DOM often exists even when trackers keep the load event open.
                pass
            source_page.wait_for_timeout(1_500)
            summary_hint = str(reference.get("summary") or "").strip()[:2_000]
            page_projection = source_page.evaluate(
                r"""
                hint => {
                  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
                  const blocks = Array.from(document.querySelectorAll(
                    'article p, main p, [role="main"] p, p, article li, main li'
                  )).filter(el => el.offsetParent !== null)
                    .map(el => clean(el.textContent)).filter(text => text.length >= 30);
                  const needle = clean(hint).slice(0, 24);
                  const mentioned = needle
                    ? (blocks.find(text => text.includes(needle)) || '')
                    : (blocks.find(text => text.length >= 60) || blocks[0] || '');
                  return {title: clean(document.title), mentioned: mentioned.slice(0, 2000)};
                }
                """,
                summary_hint,
            )
            actual_title = str((page_projection or {}).get("title") or "").strip()
            actual_mention = str((page_projection or {}).get("mentioned") or "").strip()
            title = (
                actual_title or str(reference.get("title") or "").strip() or urlsplit(url).hostname
            )
            cited_text = summary_hint or actual_mention or None
            # Keep the visual annotation compact enough that its heading, source title,
            # mention excerpt, and URL are all visible in the captured viewport.  The
            # full citation text remains available in the structured citation payload.
            banner_mention = cited_text[:500] if cited_text else None
            source_page.evaluate(
                """
                data => {
                  document.getElementById('geo-source-evidence-banner')?.remove();
                  const root = document.createElement('section');
                  root.id = 'geo-source-evidence-banner';
                  root.setAttribute('aria-label', 'GEO 信源证据元数据');
                  Object.assign(root.style, {
                    position: 'fixed', top: '12px', left: '12px', right: '12px',
                    zIndex: '2147483647',
                    padding: '14px 18px', background: 'rgba(255,255,255,.97)', color: '#111827',
                    border: '2px solid #2563eb', borderRadius: '10px',
                    boxShadow: '0 8px 28px rgba(0,0,0,.22)',
                    font: '14px/1.5 system-ui, sans-serif', maxHeight: '34vh', overflow: 'auto'
                  });
                  const heading = document.createElement('strong');
                  heading.textContent = 'GEO 信源证据（采集注释）';
                  const title = document.createElement('div');
                  title.textContent = `标题：${data.title || '未识别'}`;
                  const mention = document.createElement('div');
                  mention.textContent = `提及段落：${data.mention || '页面未提取到正文段落'}`;
                  const address = document.createElement('div');
                  address.textContent = `来源：${data.url}`;
                  root.append(heading, title, mention, address);
                  document.documentElement.appendChild(root);
                }
                """,
                {"title": title, "mention": banner_mention, "url": url},
            )
            screenshot_path = evidence_dir / f"{file_stem}-source-{ordinal:02d}.png"
            source_page.screenshot(path=str(screenshot_path), full_page=False, timeout=15_000)
            if not screenshot_path.is_file() or screenshot_path.stat().st_size <= 0:
                raise RuntimeError("source screenshot was not written")
            output.append(
                CollectionEvidenceRef(
                    kind="source_screenshot",
                    path=str(screenshot_path),
                    relation_type="cited_source_snapshot",
                    mime_type="image/png",
                    source_url=url,
                    title=title,
                    cited_text=cited_text,
                    ordinal=ordinal,
                )
            )
        except Exception as exc:
            failures.append({"ordinal": ordinal, "error": f"{type(exc).__name__}: {exc}"[:300]})
        finally:
            try:
                source_page.close()
            except Exception:
                pass
    if failures:
        log.warning(
            "doubao_source_screenshot_partial",
            requested=len(candidates),
            captured=len(output),
            failures=failures,
        )
    return output, {"requested": len(candidates), "captured": len(output), "failures": failures}


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


def _try_close_overlays(page: Any, rng: random.Random) -> None:
    """best-effort 关 cookie 横幅、下载客户端提示等非业务遮罩（拟人化点击）。

    先 count/visible 粗筛（纯观测），只有真实存在的遮罩才 human_click——
    避免对 10 个候选选择器逐一发贝塞尔点击（那本身也是机器人指纹）。
    """
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
    except Exception:
        pass
    for sel in (
        'button:has-text("下次提醒")',
        'text="下次提醒"',
        'div[role="dialog"]:has-text("下载电脑版") [class*="close"]',
        '[class*="modal"]:has-text("下载电脑版") [class*="close"]',
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
    按钮，仍不新则导航回聊天首页兜底；最终验证不过 → _IncompleteCapture
    诚实失败（可重试），绝不静默沿用旧会话。
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
    # 回退：导航到聊天首页（全新聊天页）并等 composer 回来。
    try:
        page.goto(_CHAT_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
    except Exception:
        pass
    pace(1.0, 2.0)
    deadline = time.monotonic() + 10.0
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


def _click_send_button(
    page: Any,
    rng: random.Random,
    *,
    start: tuple[float, float] | None = None,
) -> bool:
    """JS 打标发送按钮后拟人化点击（贝塞尔移动 + 悬停 + 完整鼠标事件链）。"""
    try:
        tagged = page.evaluate(_TAG_JS)
    except Exception:
        return False
    if not tagged:
        return False
    try:
        loc = page.locator('[data-proxyllm-send="true"]').first
        human_click(loc, page, rng, start=start)
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
    """拟人化点击发送并确认提交真正生效，被吞时像真人一样顿一下再试一次
    （2026-07-15 live 风控间歇性吞点击）。"""
    used = 0
    for i in range(max(1, attempts)):
        used = i + 1
        _try_close_overlays(page, rng)
        if not _click_send_button(page, rng, start=start):
            _send_via_keyboard(page, input_loc)
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
    return str(body).strip()


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


# ---------------------------------------------------------------------------
# SSE 结构化 trace 证据（W1）：只存解析后的结构化产物，不存全量 SSE 原文。
# 口径参照旧链 server/proxyllm/sse_parser.py build_rich_record；体积纪律 ≤1MB
# （旧链全量 SSE 均值 ~58MB/run，本产物约为其 1/100 量级）。
# ---------------------------------------------------------------------------

_SSE_TRACE_VERSION = 1
_SSE_TRACE_MAX_BYTES = 1_000_000
_THINKING_TEXT_LIMIT = 5_000  # 每块思考文本截断上限（字符）
_RESULT_SUMMARY_LIMIT = 800  # 每条检索结果 summary 截断上限（字符）
_BLOCK_SUMMARY_LIMIT = 2_000  # 每个检索块 summary 截断上限（字符）
_RESULTS_PER_BLOCK_LIMIT = 50  # 每个检索块保留的 results 上限


def _sse_trace_from_body(body: str) -> dict[str, Any] | None:
    """SSE 原文 → 结构化 trace record；解析失败返回 None（诚实：不出证据、不编造）。"""
    try:
        events = _parse_sse_events(body)
        assembled = _assemble_final_message(events)
        return _build_sse_trace_record(
            events, assembled, sse_body_bytes=len(body.encode("utf-8", "replace"))
        )
    except Exception:
        return None


def _assemble_sse_trace(
    events: list[dict[str, Any]],
    assembled: dict[str, Any],
    *,
    results_per_block: int,
    thinking_text_limit: int,
    sse_body_bytes: int | None = None,
) -> dict[str, Any]:
    """按给定截断水位组装 trace record（不含体积控制循环）。"""
    blocks = assembled.get("content_block") or []
    thinking_root: dict[str, Any] | None = None
    for b in blocks:
        if b.get("block_type") == 10040:
            thinking_root = b
            break
    thinking_id = thinking_root.get("block_id") if thinking_root else None

    thinking_title = None
    if thinking_root:
        inner = thinking_root.get("content") or {}
        tb = (inner.get("thinking_block") or {}) if isinstance(inner, dict) else {}
        thinking_title = tb.get("finish_title") or tb.get("streaming_title")

    thinking_chain: list[dict[str, Any]] = []
    search_blocks: list[dict[str, Any]] = []
    queries: list[dict[str, Any]] = []
    scene_counter = 0

    for b in blocks:
        bt = b.get("block_type")
        pid = b.get("parent_id") or ""
        if bt == 10000:
            if not (thinking_id and pid == thinking_id):
                continue
            text = _block_text(b)
            if not text:
                continue
            inner = b.get("content") or {}
            tb = (inner.get("text_block") or {}) if isinstance(inner, dict) else {}
            thinking_chain.append(
                {
                    "kind": "reasoning",
                    "block_id": b.get("block_id"),
                    "finish_title": tb.get("finish_title"),
                    "streaming_title": tb.get("streaming_title"),
                    "text": text[:thinking_text_limit],
                }
            )
        elif bt == 10025:
            scene_counter += 1
            inner = b.get("content") or {}
            sqr = (inner.get("search_query_result_block") or {}) if isinstance(inner, dict) else {}
            block_queries = [
                str(q).strip()
                for q in sqr.get("queries") or []
                if isinstance(q, str) and q.strip()
            ]
            for q in block_queries:
                queries.append({"query": q, "ordinal": len(queries) + 1})
            block_summary = str(sqr.get("summary") or "")[:_BLOCK_SUMMARY_LIMIT]
            results: list[dict[str, Any]] = []
            for res in (sqr.get("results") or [])[:results_per_block]:
                if not isinstance(res, dict):
                    continue
                tc = res.get("text_card") or {}
                if not isinstance(tc, dict):
                    continue
                url = tc.get("url")
                if not _is_real_url(url):
                    continue
                results.append(
                    {
                        "title": tc.get("title"),
                        "url": url,
                        "site": tc.get("sitename"),
                        "rank": tc.get("index", res.get("index")),
                        "summary": str(tc.get("summary") or "")[:_RESULT_SUMMARY_LIMIT],
                    }
                )
            search_blocks.append(
                {
                    "scene": scene_counter,
                    "queries": block_queries,
                    "summary": block_summary,
                    "results": results,
                }
            )
            if thinking_id and pid == thinking_id:
                thinking_chain.append(
                    {
                        "kind": "search",
                        "block_id": b.get("block_id"),
                        "queries": block_queries,
                        "summary": block_summary,
                        "n_results": len(results),
                    }
                )

    events_by_type: dict[str, int] = {}
    for ev in events:
        name = str(ev.get("event") or "")
        events_by_type[name] = events_by_type.get(name, 0) + 1

    return {
        "version": _SSE_TRACE_VERSION,
        "deep_think_active": thinking_root is not None,
        "thinking_title": thinking_title,
        "thinking_chain": thinking_chain,
        "search_blocks": search_blocks,
        "queries": queries,
        "stats": {
            "event_count": len(events),
            "events_by_type": events_by_type,
            "sse_body_bytes": sse_body_bytes,
            "truncated": False,
        },
        "conversation_id": assembled.get("conversation_id"),
        "section_id": assembled.get("section_id"),
        "message_id": assembled.get("message_id"),
    }


def _build_sse_trace_record(
    events: list[dict[str, Any]],
    assembled: dict[str, Any],
    sse_body_bytes: int | None = None,
) -> dict[str, Any]:
    """组装 trace record 并执行体积纪律：整体 JSON ≤1MB，超限先截 results 再截
    thinking 文本，最终 stats.truncated 如实标注。"""
    # (results_per_block, thinking_text_limit) 逐级收紧；第一级即口径上限。
    cap_ladder = (
        (_RESULTS_PER_BLOCK_LIMIT, _THINKING_TEXT_LIMIT),
        (20, _THINKING_TEXT_LIMIT),
        (5, 2_000),
        (0, 500),
        (0, 100),
    )
    record: dict[str, Any] | None = None
    truncated = False
    for level, (results_cap, thinking_cap) in enumerate(cap_ladder):
        candidate = _assemble_sse_trace(
            events,
            assembled,
            results_per_block=results_cap,
            thinking_text_limit=thinking_cap,
            sse_body_bytes=sse_body_bytes,
        )
        payload_size = len(
            json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        record = candidate
        if payload_size <= _SSE_TRACE_MAX_BYTES:
            truncated = level > 0
            break
        truncated = True
    assert record is not None
    record["stats"]["truncated"] = truncated
    return record


# ---------------------------------------------------------------------------
# deep_think UI toggle（移植自旧链 server/proxyllm/deep_think.py，语义原样保留：
# selector 清单 + picker 弹层候选 + 后置校验 + T-03 二次确认）。
# 历史教训：route patch 改写 /chat/completion 请求体已被否决——豆包客户端对请求体
# 签名，route.continue_(post_data=...) 改字节即签名失效、服务端静默吞发送
# （旧链 2026-07-15 live 实证）。UI toggle 是唯一合法机制，不得恢复改写做法。
# 与旧链差异：旧链 toggle 失败时调用方静默降级 normal；本适配器改为
# _DeepThinkToggleFailed 诚实失败（mode='deep_think' 的 task 绝不允许产出 normal 答案）。
# ---------------------------------------------------------------------------

# 深度推理模式标签优先级。2026-07 live：豆包把旧「思考」卡片换成「专家」（研究级
# 智能模型），当前弹层为 快速 / 专家 / 办公任务 Turbo / 办公任务 Pro，无「思考」
# 入口。「快速」是默认项，绝不能算作已启用。
_DEEP_MODE_LABELS = ("专家", "思考", "深度思考")
_DEEP_MODE_SUBTITLES = ("研究级智能模型", "擅长解决更难的问题")

# Picker 状态探针（selector 漂移防护，旧链 T-03）：读全部可见短按钮文本并筛
# picker 候选——长度 <12 字符以免答案区长 chip 混入；思考块按钮（思考过程/已完成
# 思考/思考中/查看/收起）显式排除，否则旧式 button:has-text("思考") 子串探测会把
# picker 仍显示「快速」的页面误报为已启用（false-positive = normal 答案错标 deep_think）。
_PICKER_STATE_JS = r"""() => {
  const btns = [...document.querySelectorAll('button, [role="button"]')]
    .filter(b => b.offsetParent !== null);
  const hits = [];
  for (const b of btns) {
    const t = (b.innerText || '').replace(/\s+/g, ' ').trim();
    if (!t || t.length >= 12) continue;
    if (!/快速|专家|思考|办公任务|深度/.test(t)) continue;
    if (/过程|已完成|思考中|正在思考|查看|收起/.test(t)) continue;
    hits.push(t);
  }
  return hits;
}"""

# picker 文本精确匹配表（非子串）——「思考过程」绝不能命中「思考」。
_DEEP_PICKER_TEXTS = ("专家", "思考", "深度思考", "专家模式", "深度思考模式")


def _picker_state(page: Any) -> list[str]:
    """可见短模式按钮文本（JS 探针）。页面丢失/探针失败返回 []，调用方回退旧选择器。"""
    try:
        hits = page.evaluate(_PICKER_STATE_JS)
    except Exception:
        return []
    if not isinstance(hits, list):
        return []
    return [str(t).strip() for t in hits if str(t).strip()]


def _mode_picker(page: Any) -> Any | None:
    """composer 的模式 picker 按钮（当前显示 快速 / 专家 / 思考 / …）。可见则返回 Locator。

    选择器顺序有意 快速/专家 在前：当前构建 picker 总是显示二者之一，提前返回
    可避开 has-text("思考") 子串风险（答案区的思考块按钮也匹配它，点它会展开思考
    块而不是打开模式弹层）。思考条目留给回滚到旧版的构建。"""
    for sel in (
        'button:has-text("快速")',
        'button:has-text("专家")',
        '[role="button"]:has-text("快速")',
        '[role="button"]:has-text("专家")',
        'button:has-text("思考")',
        '[role="button"]:has-text("思考")',
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=500):
                return loc
        except Exception:
            continue
    return None


def _deep_think_engaged(page: Any) -> bool:
    """后置校验：picker 现在显示深度推理模式（专家/思考），即不再是「快速」。
    只验证切换结果而非点击动作——静默无生效的点击必须返回 False。

    主路径是 JS picker 状态探针（精确文本匹配，思考块按钮无法冒充 picker）。
    仅当探针不可用/为空时回退旧子串选择器（此时页面已属异常状态，接受其
    思考 false-positive 风险）。"""
    hits = _picker_state(page)
    if hits:
        if any(t.startswith("快速") for t in hits):
            return False  # 快速仍在屏上 → 未启用
        return any(t in _DEEP_PICKER_TEXTS or t.startswith(("专家", "深度思考")) for t in hits)
    for label in _DEEP_MODE_LABELS:
        try:
            btn = page.locator(f'button:has-text("{label}")').first
            if btn.count() > 0 and btn.is_visible(timeout=400):
                return True
        except Exception:
            continue
    return False


def _try_enable_deep_think(page: Any, rng: random.Random) -> bool:
    """把 composer 模式 picker 切到深度推理模式（专家，旧称思考）。仅当切换被
    后置校验确认时返回 True。

    真人节奏（2026-08-06 起；picker 连点+秒发曾被行为风控稳定识别出验证码）：
    悬停 picker 300-900ms → 点击 → 等弹层挂载 → 读菜单 400-1000ms → 拟人化点
    选项（专家 > 思考 > 深度思考，副标题最抗漂移）→ 每次点击后
    _deep_think_engaged 校验 + 隔拍二次确认（T-03：豆包可能乐观翻标签后回退）。
    全部失败返回 False，调用方诚实报错。"""

    def _pace(lo: float, hi: float) -> float:
        return human_pause(rng, lo, hi, sleep=lambda s: page.wait_for_timeout(int(s * 1000)))

    if _deep_think_engaged(page):
        return True

    picker = _mode_picker(page)
    if picker is not None:
        try:
            # 弹层水合等待：picker 点击后候选不会立即挂载（JS hydration 滞后，
            # 固定 600ms 曾稳定假失败）。等候选真实可见，不出现就再点一次（真人
            # 点不开菜单的自然反应就是再点）。
            menu_open = False
            for _attempt in range(2):
                human_click(picker, page, rng, hover_s=_PACE_PICKER_HOVER_S)
                try:
                    page.get_by_text(_DEEP_MODE_LABELS[0], exact=True).first.wait_for(
                        state="visible", timeout=2500
                    )
                    menu_open = True
                    break
                except Exception:
                    continue
            _pace(*_PACE_MENU_READ_S)  # 读菜单
            candidates = []
            if menu_open:
                for label in _DEEP_MODE_LABELS:
                    candidates.append(page.get_by_text(label, exact=True).first)
                    candidates.append(page.get_by_role("menuitem", name=label).first)
                    candidates.append(page.get_by_role("button", name=label).first)
                for sub in _DEEP_MODE_SUBTITLES:
                    candidates.append(page.get_by_text(sub).first)
            for opt in candidates:
                try:
                    if opt.count() > 0 and opt.is_visible(timeout=400):
                        human_click(opt, page, rng)
                        page.wait_for_timeout(400)
                        if _deep_think_engaged(page):
                            # 隔拍二次确认（T-03）：picker 标签可能先乐观翻转再回退。
                            page.wait_for_timeout(400)
                            if _deep_think_engaged(page):
                                return True
                except Exception:
                    continue
        except Exception:
            pass
        # 弹层可能仍开着并截获 composer——关闭。
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(150)
        except Exception:
            pass

    # 旧版豆包的独立「深度思考」按钮，同样后置校验。
    for sel in (
        'button[aria-label*="深度思考"]',
        'button:has-text("深度思考")',
        'div[role="button"]:has-text("深度思考")',
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=400):
                human_click(loc, page, rng)
                page.wait_for_timeout(300)
                if _deep_think_engaged(page):
                    page.wait_for_timeout(400)  # 同款回退二次确认（T-03）
                    if _deep_think_engaged(page):
                        return True
        except Exception:
            continue
    return _deep_think_engaged(page)
