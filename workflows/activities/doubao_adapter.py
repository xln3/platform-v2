"""豆包网页采集适配器 v1（Temporal activity：``collect_with_adapter``）。

由审视会话按用户直接指令实现（见 docs/contract-gaps/S01-003）。选择器、等待逻辑、
墙识别特征、SSE 组装全部移植自旧系统 live 验证过的代码：

- ``server/proxyllm/doubao_client.py``（1771 行：输入框/发送按钮、提交确认、cloak/登录墙、
  DOM 通知词表、成功门、整页截图 flatten）
- ``server/proxyllm/capture.py``（CDP Network 捕获 /chat/completion 事件流——豆包用
  SharedWorker 发请求，page.on("response") 看不到，必须走 CDP）
- ``server/proxyllm/sse_parser.py``（SSE 事件切分、块组装、references 抽取、mojibake 修复）
- ``server/proxyllm/login_state.py``（CAPTCHA_SELECTORS 验证码权威词表）

采集主链、官方分享导出与原始流量留痕已接入；session_heal、软禁打标等旧链外围不搬。

v1 边界（2026-08-05 W1 起 deep_think 解锁）：

- ``mode='normal'``（页面「快速」）与 ``mode='deep_think'``（页面「专家」）均支持；其他 mode →
  ``ApplicationError(..., type="unsupported_mode", non_retryable=True)`` 诚实拒绝。
  两种模式都经 composer picker 显式切到目标态并做后置校验，避免浏览器继承上题
  模式后把专家回答错标为快速（或反之）；快速态确认失败 → ``mode_toggle_failed``。
  deep_think 经 composer 模式 picker 的 UI toggle 启用（移植自旧链
  ``server/proxyllm/deep_think.py``，selector 漂移防护 + 后置校验原样保留）；
  无法确认启用 → ``deep_think_toggle_failed`` non_retryable，绝不静默回退 normal。
  历史教训：route patch 改写 /chat/completion 请求体（need_deep_think 0→1）已被否决——
  豆包客户端对请求体签名，改字节即签名失效、服务端静默吞发送（旧链 2026-07-15
  live 实证），UI toggle 是唯一合法机制。
  请求态≠实际态分开记录（旧链纪律：请求 deep_think ≠ 实际启用）：发送前的
  picker 后置校验只确认「请求已下达」；流结束后以 SSE 证据（thinking root
  block_type=10040）二次确认实际态——``_mode_evidence`` 产出
  {requested, ui_toggle_engaged, sse_deep_think_active, actual}，actual 仅当
  SSE 证据为正才标 deep_think，证据缺失（DOM 兜底/解析失败）或为负一律如实
  标 normal（旧链 portal 同款口径：「请求了深度思考，但证据中未检测到启用」
  不计深度态）。结论注入 trace JSON 的 ``mode`` 段随证据落盘并进
  ``CollectedAnswer.meta``；2026-08-14 起，请求 deep 而未获 SSE 确认由
  warning-only 升级为 ``mode_unconfirmed`` 诚实失败（non_retryable，
  不落 completed——配额耗尽后平台静默回退快速模式的答案曾是事故源头）。
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
  2026-08-14 起（墙词表 ``wall_lexicon``）：答案文本级配额/禁言/拒答 →
  ``wall_quota``/``wall_muted``/``wall_refusal`` non_retryable；batch 连坐按
  wall_type 细化（muted 全连坐、quota 只连坐同 mode、refusal 不连坐，见
  ``collect_batch`` docstring）。
- 成功判据（零合成）：提交被接受（输入框清空）且 /chat/completion 流真正
  loadingFinished 且解析出非空正文且不含墙特征——缺一都不得返回成功。
  流截断/空答案/无流，或平台官方分享 PNG/公开链接任一缺失 →
  ``answer_capture_incomplete``（可重试的诚实失败）；运行时截图绝不冒充分享图。

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
import difflib
import io
import ipaddress
import json
import math
import os
import random
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import structlog
from PIL import Image, UnidentifiedImageError
from temporalio import activity
from temporalio.exceptions import ApplicationError

from domain.collection.uvw import normalize_retrieval_events, retrieval_events_from_trace_path
from workflows.activities.answer_dom_anchor import capture_answer_evidence, recognize_image_text
from workflows.activities.browser_driver import load_sync_browser_driver
from workflows.activities.browser_router import resolve_batch_instance
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
from workflows.activities.official_share import (
    probe_official_share_url,
    recover_png_from_export_audit,
    write_share_link_manifest,
)
from workflows.activities.page_capture import capture_full_page_safely
from workflows.activities.raw_capture import dump_raw_evidence_refs, maybe_raw_capture
from workflows.activities.resident_browser import (
    BrowserBusyError,
    platform_browser,
    resident_cdp_url,
)
from workflows.activities.wall_lexicon import (
    WallVerdict,
    classify_answer_text,
    detect_muted_banner,
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

# Official ``/thread`` pages keep the answer in an inner vertical scroller and
# Markdown tables in a second horizontal scroller.  ``full_page=True`` expands
# neither one.  The shared capture helper snapshots every inline style before
# running this script and restores it in ``finally``, so these temporary layout
# changes cannot leak into another task on the resident browser.
_DOUBAO_OFFICIAL_SHARE_FLATTEN_JS = r"""() => {
  const body = document.body;
  const doc = document.documentElement;
  const beforeBodyClientH = body ? body.clientHeight : 0;
  const beforeBodyScrollH = body ? body.scrollHeight : 0;

  const verticalCandidates = [];
  for (const el of document.querySelectorAll('div, main, section, article')) {
    const cs = getComputedStyle(el);
    if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll')
        && el.scrollHeight > el.clientHeight + 100) {
      verticalCandidates.push(el);
    }
  }
  verticalCandidates.sort((a, b) => b.scrollHeight - a.scrollHeight);
  const main = verticalCandidates[0] || null;
  const fullHeight = main ? main.scrollHeight : 0;
  if (main) {
    let cur = main;
    while (cur) {
      if (cur === main) cur.style.setProperty('height', fullHeight + 'px', 'important');
      else cur.style.setProperty('height', 'auto', 'important');
      cur.style.setProperty('max-height', 'none', 'important');
      cur.style.setProperty('min-height', '0', 'important');
      cur.style.setProperty('overflow-y', 'visible', 'important');
      cur.style.setProperty('flex', '0 0 auto', 'important');
      cur.style.setProperty('transform', 'none', 'important');
      cur.style.setProperty('contain', 'none', 'important');
      if (cur === doc) break;
      cur = cur.parentElement;
    }
  }

  let expandedTableCount = 0;
  let widestTable = 0;
  for (const tableScroller of document.querySelectorAll('.mdbox-table-scroll-container')) {
    if (tableScroller.scrollWidth <= tableScroller.clientWidth + 1) continue;
    const tableWidth = Math.ceil(tableScroller.scrollWidth);
    widestTable = Math.max(widestTable, tableWidth);
    expandedTableCount += 1;
    tableScroller.style.setProperty('width', tableWidth + 'px', 'important');
    tableScroller.style.setProperty('min-width', tableWidth + 'px', 'important');
    tableScroller.style.setProperty('max-width', 'none', 'important');
    tableScroller.style.setProperty('overflow-x', 'visible', 'important');
    let cur = tableScroller.parentElement;
    while (cur && cur !== body && cur !== doc) {
      cur.style.setProperty('width', 'max-content', 'important');
      cur.style.setProperty('min-width', 'max-content', 'important');
      cur.style.setProperty('max-width', 'none', 'important');
      cur.style.setProperty('overflow-x', 'visible', 'important');
      cur.style.setProperty('contain', 'none', 'important');
      cur = cur.parentElement;
    }
  }

  for (const el of document.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    if (cs.transform && cs.transform !== 'none') {
      el.style.setProperty('transform', 'none', 'important');
    }
    if (cs.position === 'fixed') {
      // Floating “continue in Doubao” buttons otherwise get frozen over a table
      // cell in the beyond-viewport image. They are page chrome, not evidence.
      el.style.setProperty('display', 'none', 'important');
    }
  }
  const targetH = Math.max(fullHeight, beforeBodyScrollH, beforeBodyClientH);
  const targetW = Math.max(widestTable + 160, window.innerWidth);
  if (body) {
    body.style.setProperty('height', 'auto', 'important');
    body.style.setProperty('min-height', targetH + 'px', 'important');
    body.style.setProperty('min-width', targetW + 'px', 'important');
    body.style.setProperty('overflow', 'visible', 'important');
  }
  if (doc) {
    doc.style.setProperty('height', 'auto', 'important');
    doc.style.setProperty('min-height', targetH + 'px', 'important');
    doc.style.setProperty('min-width', targetW + 'px', 'important');
    doc.style.setProperty('overflow', 'visible', 'important');
  }
  if (body) void body.offsetHeight;
  return {
    ok: !!main,
    scroller_full_height: fullHeight,
    expanded_table_count: expandedTableCount,
    widest_table: widestTable,
    body_scroll_height_after: body ? body.scrollHeight : 0,
    doc_scroll_height_after: doc ? doc.scrollHeight : 0,
    viewport_height: window.innerHeight
  };
}"""

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
    '[aria-label*="开启新对话"]',
    'button:has-text("开启新对话")',
    '[role="button"]:has-text("开启新对话")',
    'button:has-text("新对话")',
    '[role="button"]:has-text("新对话")',
    'a:has-text("新对话")',
)

# 新会话验证：显式 data-empty-conversation=false 为最高优先级旧会话信号；
# 其次用 202608 live 的 action-bar/message-box/messageid 语义节点，最后兼容旧
# data-testid/author-role。>0 = 旧会话/进行中的旧回答。
_CHAT_MESSAGE_COUNT_JS = r"""() => {
  // 2026-08 live DOM no longer renders message_text_content.  A completed Q/A
  // exposes one send and one receive action bar plus message-box target nodes.
  // Any one of these stable semantic families is enough to prove this is not an
  // empty conversation.  Return the first non-zero family to avoid double count.
  //
  // 2026-08-13 live 修正：当前构建的空会话首页也常驻
  // data-empty-conversation="false"（语义漂移，豆包 sh/bj 双账号实测），单看
  // 属性会把全新会话误判成旧会话。仅当「属性=false 且 URL 带会话 id」时才采信
  // 其非空语义——该组合覆盖虚拟列表卸载长会话消息的场景。
  const conversationStates = Array.from(
    document.querySelectorAll('[data-empty-conversation]')
  ).map((el) => el.getAttribute('data-empty-conversation'));
  const hasConversationId = /\/chat\/[^/?#]+/.test(location.pathname);
  const stable = [
    '[data-foundation-type="send-message-action-bar"]',
    '[data-foundation-type="receive-message-action-bar"]',
    '[data-target-id="message-box-target-id"]',
    '[messageid]'
  ];
  for (const s of stable) {
    const n = document.querySelectorAll(s).length;
    if (n > 0) return n;
  }
  const legacy = [
    '[data-testid="message_text_content"]',
    '[data-message-author-role="assistant"]',
    '[data-message-author-role="user"]'
  ];
  let n = 0;
  for (const s of legacy) n += document.querySelectorAll(s).length;
  if (n > 0) return n;
  if (conversationStates.includes('false') && hasConversationId) return 1;
  return 0;
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

# DOM 层系统通知词表（softban 过频提示 / 实名墙）——2026-08-14 起扫描无条件
# 执行（曾被 `if not answer_text:` 门挡 = 配额/禁言文案当答案采回的事故根因）；
# 已出答案时把答案正文从扫描文本中剔除，「答案正文提及「过频/实名」不翻标记」
# 的旧不变量保持成立。答案文本级的配额/禁言/拒答判定在 wall_lexicon（唯一真源）。
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

# 2026-08 live DOM: the chat and the left history rail are separate scrollers.
# The history rail is taller, so the old "largest scrollHeight + flatten every
# transform" capture selected the rail and destroyed the virtual rows' translateY
# positioning.  These scripts only read layout and move the *validated chat*
# scroller.  They never touch an inline style or the document scroll position.
_DOUBAO_CAPTURE_STATE_JS = r"""async (request) => {
  const fail = (error) => ({ok: false, error});
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0
      && cs.display !== 'none' && cs.visibility !== 'hidden';
  };
  const chatScrollers = () => Array.from(
    document.querySelectorAll('div.scroller[class*="v_list_scroller"]')
  ).filter((el) => {
    if (!visible(el)) return false;
    const cs = getComputedStyle(el);
    if (cs.overflowY !== 'auto' && cs.overflowY !== 'scroll') return false;
    return el.querySelectorAll('[data-target-id="message-box-target-id"]').length === 2;
  });

  let candidates = chatScrollers();
  if (candidates.length !== 1) return fail(`chat_scroller_count:${candidates.length}`);
  let scroller = candidates[0];
  if (request && Number.isFinite(request.scrollTop)) {
    scroller.scrollTop = Number(request.scrollTop);
    await new Promise((resolve) => requestAnimationFrame(
      () => requestAnimationFrame(resolve)
    ));
    candidates = chatScrollers();
    if (candidates.length !== 1) {
      return fail(`chat_scroller_count_after_scroll:${candidates.length}`);
    }
    scroller = candidates[0];
  }

  const roots = Array.from(
    scroller.querySelectorAll('[data-target-id="message-box-target-id"]')
  );
  if (roots.length !== 2) return fail(`message_root_count:${roots.length}`);
  if (!roots[0].querySelector('[data-foundation-type="send-message-action-bar"]')) {
    return fail('question_role_unproven');
  }
  if (!roots[1].querySelector('[data-foundation-type="receive-message-action-bar"]')) {
    return fail('answer_role_unproven');
  }
  const contentNodes = roots.map((root) => Array.from(
    root.querySelectorAll('[data-message-id]')
  ));
  if (contentNodes[0].length !== 1 || contentNodes[1].length !== 1) {
    return fail(`message_content_count:${contentNodes[0].length},${contentNodes[1].length}`);
  }
  const question = contentNodes[0][0];
  const answer = contentNodes[1][0];
  // Doubao's markdown renderer inserts presentation-only spaces around Chinese
  // quotation marks (e.g. `出现 “品牌”`), although the submitted task payload is
  // `出现“品牌”`. Ignore only that renderer artifact; all other characters must
  // remain an exact match so an old/adjacent question cannot be screenshotted.
  const normalizeText = (value) => String(value || '')
    .replace(/\s+/g, ' ')
    .replace(/\s*([“”‘’「」『』])\s*/g, '$1')
    .trim();
  const expectedQuestion = normalizeText(request && request.expectedQuestion);
  if (!expectedQuestion) return fail('expected_question_missing');
  // 2026-08-13 live：豆包渲染层还会在 CJK↔拉丁字母边界自动补展示空格（提交
  // 「高校非传统IT资产」→ 气泡显示「高校非传统 IT 资产」）。问题比对改为
  // 去除全部空白后的精确比较——防错问截图的强度不变（不同问题的非空白
  // 字符必然不同），只豁免渲染层补的空格。
  const normalizeQuestionText = (value) => normalizeText(value).replace(/\s+/g, '');
  if (normalizeQuestionText(question.innerText) !== normalizeQuestionText(expectedQuestion)) {
    return fail('question_text_mismatch');
  }
  const excluded = [question, answer].find((node) => node.querySelector(
    '[data-foundation-type$="message-action-bar"],'
      + '[data-foundation-type="receive-message-suggest-foundation"],'
      + '#input-engine-container'
  ));
  if (excluded) return fail('excluded_ui_inside_message_content');
  if (scroller.querySelector('#input-engine-container')) {
    return fail('composer_inside_chat_scroller');
  }

  const fingerprint = (node) => {
    const text = normalizeText(node.innerText);
    let hash = 2166136261;
    for (let i = 0; i < text.length; i += 1) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }
    return `${text.length}:${(hash >>> 0).toString(16)}`;
  };
  const scrollerRect = scroller.getBoundingClientRect();
  const scrollTop = scroller.scrollTop;
  const blocks = [question, answer].map((node, index) => {
    const rect = node.getBoundingClientRect();
    return {
      role: index === 0 ? 'question' : 'answer',
      top: rect.top - scrollerRect.top + scrollTop,
      bottom: rect.bottom - scrollerRect.top + scrollTop,
      left: rect.left,
      right: rect.right,
      fingerprint: fingerprint(node),
    };
  });
  if (blocks.some((block) => !Number.isFinite(block.top)
      || !Number.isFinite(block.bottom))) {
    return fail('message_bounds_invalid');
  }
  if (blocks.some((block) => block.fingerprint.startsWith('0:'))) {
    return fail('message_text_empty');
  }
  if (blocks.some((block) => block.bottom <= block.top)) {
    return fail('message_height_invalid');
  }
  if (blocks[0].bottom > blocks[1].top + 1) {
    return fail('message_order_invalid');
  }

  const clipX = Math.min(blocks[0].left, blocks[1].left);
  const clipRight = Math.max(blocks[0].right, blocks[1].right);
  const clipWidth = clipRight - clipX;
  const viewportHeight = scroller.clientHeight;
  const toBottomButton = document.querySelector('#to-bottom-button');
  if (!toBottomButton) return fail('to_bottom_button_missing');
  const toBottomRect = toBottomButton.getBoundingClientRect();
  const overlapsScroller =
    toBottomRect.right > scrollerRect.left &&
    toBottomRect.left < scrollerRect.right &&
    toBottomRect.bottom > scrollerRect.top &&
    toBottomRect.top < scrollerRect.bottom;
  if (!overlapsScroller) return fail('to_bottom_button_outside_scroller');
  // The bottom arrow and its gradient are viewport chrome.  Capturing the full
  // scroller copied them into every stitched tile and also hid the text beneath
  // the gradient.  Reserve the band above that control; the answer content ends
  // before the scroller's trailing action/suggestion rows, so it remains reachable.
  const captureBottom = Math.min(scrollerRect.bottom, toBottomRect.top - 8);
  const captureHeight = captureBottom - scrollerRect.top;
  const maxScroll = Math.max(0, scroller.scrollHeight - viewportHeight);
  if (clipWidth <= 0 || viewportHeight <= 0 || captureHeight < 200
      || scroller.scrollHeight <= 0) {
    return fail('chat_scroller_bounds_invalid');
  }
  if (clipX < scrollerRect.left - 1
      || clipRight > scrollerRect.left + scroller.clientWidth + 1) {
    return fail('message_outside_chat_scroller');
  }
  if (scrollerRect.top < 0 || scrollerRect.top + viewportHeight > window.innerHeight + 1
      || clipX < 0 || clipRight > window.innerWidth + 1) {
    return fail('chat_scroller_outside_viewport');
  }
  if (blocks.some((block) => block.top < -1
      || block.bottom > scroller.scrollHeight + 1)) {
    return fail('message_outside_scroll_extent');
  }
  return {
    ok: true,
    scroll_top: scrollTop,
    scroll_height: scroller.scrollHeight,
    max_scroll: maxScroll,
    viewport_height: viewportHeight,
    clip_x: clipX,
    clip_y: scrollerRect.top,
    clip_width: clipWidth,
    capture_height: captureHeight,
    blocks,
  };
}"""

_DOUBAO_CAPTURE_RESTORE_JS = r"""async (scrollTop) => {
  const candidates = Array.from(
    document.querySelectorAll('div.scroller[class*="v_list_scroller"]')
  ).filter((el) => {
    const rect = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0
      && cs.display !== 'none' && cs.visibility !== 'hidden'
      && (cs.overflowY === 'auto' || cs.overflowY === 'scroll');
  });
  if (candidates.length !== 1) {
    return {ok: false, error: `restore_scroller_count:${candidates.length}`};
  }
  const scroller = candidates[0];
  scroller.scrollTop = Number(scrollTop);
  await new Promise((resolve) => requestAnimationFrame(
    () => requestAnimationFrame(resolve)
  ));
  return {
    ok: Math.abs(scroller.scrollTop - Number(scrollTop)) <= 1,
    actual_scroll_top: scroller.scrollTop,
  };
}"""

_DOUBAO_CAPTURE_OVERLAP_CSS_PX = 32.0
_DOUBAO_CAPTURE_MAX_TILES = 200
_DOUBAO_CAPTURE_MAX_WIDTH_CSS_PX = 5_000.0
_DOUBAO_CAPTURE_MAX_HEIGHT_CSS_PX = 50_000.0


# ---------------------------------------------------------------------------
# 配置 / 错误类型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DoubaoAdapterConfig:
    """env 配置。proxy_url 原文只在启动浏览器时使用，绝不落日志/payload。

    ``browser_key``（2026-08-09 起，浏览器矩阵化）：attach/互斥锁/fence 用的
     opaque "platform"——batch 路径由 browser_router 解析为常驻实例键
    （``doubao_sh`` 等）；缺省平台 slug（per-task 老路径/测试行为不变）。"""

    profile_dir: Path
    proxy_url: str | None
    evidence_dir: Path
    headless: bool
    chat_timeout_s: float = _DEFAULT_CHAT_TIMEOUT_S
    browser_key: str = _PLATFORM_SLUG

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
        # 失败题原始流量证据（2026-08-10 起）：_collect_one 题末挂 raw/HAR ref，
        # 经 _failure_outcome → 失败 result.evidence 进 CAS。缺省空（session 级
        # 墙在 raw capture 建立之前抛出，无证据可挂）。
        self.evidence_refs: list[CollectionEvidenceRef] = []


class _IncompleteCapture(RuntimeError):
    """采集未完成的诚实失败（可重试）：流截断 / 空答案 / 无流等。"""

    def __init__(self, message: str, evidence_path: Path | None = None) -> None:
        super().__init__(message)
        self.evidence_path = evidence_path
        self.evidence_refs: list[CollectionEvidenceRef] = []


class _DeepThinkToggleFailed(RuntimeError):
    """deep_think 模式 picker 无法确认启用（non_retryable；绝不静默回退 normal）。"""

    def __init__(self, message: str, evidence_path: Path | None = None) -> None:
        super().__init__(message)
        self.evidence_path = evidence_path
        self.evidence_refs: list[CollectionEvidenceRef] = []


class _QuickModeToggleFailed(RuntimeError):
    """normal 请求无法确认页面已切到「快速」（non_retryable；拒绝错标结果）。"""

    def __init__(self, message: str, evidence_path: Path | None = None) -> None:
        super().__init__(message)
        self.evidence_path = evidence_path
        self.evidence_refs: list[CollectionEvidenceRef] = []


class _ModeUnconfirmed(RuntimeError):
    """页面目标模式与 SSE 实际模式不一致时的诚实失败（non_retryable）。

    deep_think 请求缺 thinking root，或 normal/快速请求反而出现 thinking root，
    都拒绝按请求标签落 completed，防止模式错标。"""

    def __init__(self, message: str, evidence_path: Path | None = None) -> None:
        super().__init__(message)
        self.evidence_path = evidence_path
        self.evidence_refs: list[CollectionEvidenceRef] = []


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
    retrieval_events: list[dict[str, Any]] = field(default_factory=list)
    answer_evidence: CollectionEvidenceRef | None = None


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
    CollectionBatchItemResult 对齐（ok/wall/incomplete/aborted）。

    ``evidence``（2026-08-10 起）：失败题的原始流量证据 ref（raw/HAR，
    由题末异常对象携带而来）；aborted 题零浏览器交互，恒空。"""

    business_key: str
    status: str
    answer: CollectedAnswer | None = None
    error_type: str | None = None
    error_message: str | None = None
    evidence_path: Path | None = None
    evidence: list[CollectionEvidenceRef] = field(default_factory=list)


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


def _batch_result_with_pause(
    results: list[CollectionBatchItemResult],
    *,
    instance_key: str | None = None,
) -> CollectionBatchResult:
    """等长结果 → CollectionBatchResult；首个 wall_captcha 题标注 captcha_pause。

    captcha-assist-v1：撞码是可人工恢复的暂停点而非终局失败——workflow 见到
    pause 会挂起等人工接管、从 resume_index 起重采；results 仍保持等长全占
    位（未打补丁的旧 workflow 重放本结果，行为与今天完全一致）。非撞码失败
    （登录墙/incomplete/toggle）不产生 pause，维持现行语义。

    ``instance_key``（浏览器矩阵化）：batch 出口统一盖实例章——逐结果写
    ``browser_instance``（persist 进 matrix_json 的 provenance 来源）且 pause
    携带实例键（assist 接管 attach 同一台常驻浏览器）。None = 旧行为不变。
    """
    if instance_key is not None:
        for result in results:
            result.browser_instance = instance_key
    for index, result in enumerate(results):
        if result.status == "wall" and result.error_type == "wall_captcha":
            return CollectionBatchResult(
                results=results,
                captcha_pause=CaptchaPause(
                    resume_index=index,
                    business_key=result.business_key,
                    wall_type=result.error_type,
                    evidence_ref=result.screenshot_ref,
                    instance_key=instance_key,
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
    # 浏览器矩阵化（2026-08-09 起）：batch 段（同平台同地域）路由到对应常驻
    # 实例，实例键当 opaque platform 进 platform_browser/锁/fence/CDP 解析；
    # 无实例/地域不符/清单畸形一律 fail-closed（诚实失败，绝不静默替换）。
    # 空 batch 不解析（零浏览器交互的旧契约不变）。
    route = resolve_batch_instance(batch.items)
    instance_key = route.instance_key if route is not None else None
    config = DoubaoAdapterConfig.from_env(proxy_url_override=proxy_url_override)
    if route is not None:
        config = replace(config, browser_key=route.instance_key)
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
        browser_instance=instance_key,
        egress_region_gb=route.exit_gb if route is not None else None,
        fallback_proxy=(mask_proxy_url(config.proxy_url) if route is None else None),
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
                    evidence=wall.evidence_refs,
                )
                for item in batch.items
            ],
            instance_key=instance_key,
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
                    evidence=toggle.evidence_refs,
                )
                for item in batch.items
            ]
        )
    except _QuickModeToggleFailed as toggle:
        # normal/快速同样 fail-closed：无法确认目标态时不得按快速模式落结果。
        evidence_suffix = f"; evidence={toggle.evidence_path}" if toggle.evidence_path else ""
        bound.info("doubao_batch_session_quick_toggle_failed", stage=progress["stage"])
        return CollectionBatchResult(
            results=[
                _failure_batch_item(
                    item,
                    status="wall",
                    error_type="mode_toggle_failed",
                    error_message=f"{toggle}{evidence_suffix}",
                    evidence_path=toggle.evidence_path,
                    evidence=toggle.evidence_refs,
                )
                for item in batch.items
            ]
        )
    except _ModeUnconfirmed as mu:
        # 防御：mode_unconfirmed 应在题内转 outcome；逃出即按 session 级诚实记录。
        evidence_suffix = f"; evidence={mu.evidence_path}" if mu.evidence_path else ""
        bound.info("doubao_batch_session_mode_unconfirmed", stage=progress["stage"])
        return CollectionBatchResult(
            results=[
                _failure_batch_item(
                    item,
                    status="wall",
                    error_type="mode_unconfirmed",
                    error_message=f"{mu}{evidence_suffix}",
                    evidence_path=mu.evidence_path,
                    evidence=mu.evidence_refs,
                )
                for item in batch.items
            ]
        )
    except _IncompleteCapture as inc:
        # session 级临时故障（浏览器启动失败等）：一题未发，raise 走 batch 重试。
        evidence_suffix = f"; evidence={inc.evidence_path}" if inc.evidence_path else ""
        bound.info("doubao_batch_session_incomplete", reason=str(inc), stage=progress["stage"])
        raise ApplicationError(f"{inc}{evidence_suffix}", type="answer_capture_incomplete") from inc
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
    return _batch_result_with_pause(results, instance_key=instance_key)


def _failure_batch_item(
    item: CollectionTaskInput,
    *,
    status: str,
    error_type: str,
    error_message: str,
    evidence_path: Path | None,
    evidence: list[CollectionEvidenceRef] | None = None,
) -> CollectionBatchItemResult:
    """失败/未执行题 → CollectionBatchItemResult。DLP 由 persist 层统一脱敏。

    ``evidence``（2026-08-10 起）：失败题原始流量证据 ref（raw/HAR）——
    persist 层 `_persist_collection_failure` 会把它 persist 进 CAS（墙截图
    维持现状不进 CAS）。"""
    screenshot_ref = f"file://{evidence_path}" if evidence_path is not None else None
    return CollectionBatchItemResult(
        business_key=item.business_key,
        status=status,
        error_type=error_type,
        error_message=error_message,
        screenshot_ref=screenshot_ref,
        quality_state=error_type,
        evidence=list(evidence or []),
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
            retrieval_events=base.retrieval_events,
        )
    return _failure_batch_item(
        item,
        status=outcome.status,
        error_type=outcome.error_type or "unknown_failure",
        error_message=outcome.error_message or "",
        evidence_path=outcome.evidence_path,
        evidence=outcome.evidence,
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
    except _QuickModeToggleFailed as toggle:
        evidence_suffix = f"; evidence={toggle.evidence_path}" if toggle.evidence_path else ""
        bound.info("doubao_quick_mode_toggle_failed", stage=progress["stage"])
        raise ApplicationError(
            f"{toggle}{evidence_suffix}", type="mode_toggle_failed", non_retryable=True
        ) from toggle
    except _ModeUnconfirmed as mu:
        # 请求模式与 SSE 实际态不一致：non_retryable 诚实失败，绝不错标落库。
        evidence_suffix = f"; evidence={mu.evidence_path}" if mu.evidence_path else ""
        bound.info("doubao_mode_unconfirmed", stage=progress["stage"])
        raise ApplicationError(
            f"{mu}{evidence_suffix}", type="mode_unconfirmed", non_retryable=True
        ) from mu
    except _IncompleteCapture as inc:
        evidence_suffix = f"; evidence={inc.evidence_path}" if inc.evidence_path else ""
        bound.info("doubao_capture_incomplete", reason=str(inc), stage=progress["stage"])
        raise ApplicationError(f"{inc}{evidence_suffix}", type="answer_capture_incomplete") from inc
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
    if collected.answer_evidence is not None:
        evidence.append(collected.answer_evidence)
    # The product-facing answer image is the platform's official share image.
    # Runtime screenshots remain separate audit evidence and are only a backward-
    # compatible fallback for injected/legacy CollectedAnswer objects.
    official_share_image = next(
        (
            ref.path
            for ref in evidence
            if ref.kind == "share_image" and ref.relation_type == "official_share_image"
        ),
        None,
    )
    screenshot_ref = f"file://{official_share_image or collected.screenshot_path}"
    trace_path = next(
        (
            ref.path
            for ref in evidence
            if ref.kind == "sse" and ref.relation_type == "answer_sse_trace"
        ),
        None,
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
        retrieval_events=(
            collected.retrieval_events or retrieval_events_from_trace_path(trace_path)
        ),
    )


_SAFE_STEM_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_stem(business_key: str) -> str:
    """business_key 安全化成文件名片段（路径里绝不可含账号/口令等敏感字符）。"""
    stem = _SAFE_STEM_RE.sub("-", business_key).strip("-.")
    return (stem or "task")[:80]


def _compose_answer_text(answer_text: str, references: list[dict[str, Any]]) -> str:
    """Keep the platform answer separate from its structured source relations."""
    del references
    return answer_text.strip()


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
    for reference in references:
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

    batch 失败语义（2026-08-14 细化）：题级失败转 outcome，结果列表与输入
    等长同序；连坐按失败类型分级——真墙（captcha/login/send/muted/cloak）=
    账号级阻断，后续题全 aborted（零浏览器交互：真人撞墙后会停下，不编造不
    硬闯）；wall_quota=配额按 (账号×mode) 计费，只连坐同 mode 余题；
    wall_refusal/incomplete/mode_unconfirmed=题级 flake 或内容失败，不连坐；模式
    toggle 已在单题内穷尽两轮 trigger/option + native fallback 仍失败时，视为当前
    session 的同 mode 控件不可用，后续同 mode 题 aborted（零浏览器交互），其他
    mode 仍可继续。session 建立阶段（launch/navigate/登录墙检查）的异常原样逃出，
    由 activity 层按 session 级语义处理（一题未发）。
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
        # 配额墙按 (账号×mode) 计费（2026-08-14 起）：wall_quota 只连坐同 mode
        # 余题——记录已撞配额的 mode，轮到其余题位次时零浏览器交互追加 aborted
        # 占位（结果列表与输入等长同序的契约不变）。
        quota_blocked: dict[str, DoubaoBatchItemSpec] = {}
        # 模式控件失败同样按 mode 建 session 级小熔断。_try_enable_* 单题内部已经
        # 穷尽两轮菜单打开、语义候选点击和 native fallback；同一 DOM/session 中
        # 继续逐题重复只会制造同源失败并触发平台风控。不同 mode 不连坐。
        mode_toggle_blocked: dict[str, tuple[DoubaoBatchItemSpec, str]] = {}
        with self._browser_session(on_stage) as (context, page, pw_timeout, driver):
            for index, spec in enumerate(items):
                if spec.mode in mode_toggle_blocked:
                    failed_spec, error_type = mode_toggle_blocked[spec.mode]
                    outcomes.append(
                        self._aborted_outcome(spec, failed_spec, error_type, batch_stopped=False)
                    )
                    continue
                if spec.mode in quota_blocked:
                    outcomes.append(
                        self._aborted_outcome(
                            spec, quota_blocked[spec.mode], "wall_quota", batch_stopped=False
                        )
                    )
                    continue
                on_stage(f"item:{spec.business_key}")
                try:
                    answer = self._collect_one(
                        context, page, spec, on_stage, pw_timeout=pw_timeout, driver=driver
                    )
                except _WallError as wall:
                    outcomes.append(self._failure_outcome(spec, "wall", wall.wall_type, wall))
                    if wall.wall_type == "wall_refusal":
                        # 拒答=题级内容失败（平台拒答本题），非账号墙：不连坐，
                        # 本题诚实失败后继续下一题。
                        continue
                    if wall.wall_type == "wall_quota":
                        # 配额按 (账号×mode) 计费：只连坐同 mode 余题，其他 mode
                        # 照跑（专家模式配额耗尽 ≠ 快速模式不可用）。
                        quota_blocked[spec.mode] = spec
                        continue
                    # 真墙（captcha/login/send/muted/cloak…）：账号级阻断，余题
                    # 全 aborted（真人撞墙即停，零浏览器交互不硬闯）。
                    outcomes.extend(
                        self._aborted_outcome(rest, spec, wall.wall_type)
                        for rest in items[index + 1 :]
                    )
                    return outcomes
                except _ModeUnconfirmed as mu:
                    # deep_think 无 SSE 思考证据（2026-08-14 起 non_retryable 诚实
                    # 失败）：题级失败不连坐（与 toggle 失败同哲学），余题照跑。
                    outcomes.append(self._failure_outcome(spec, "wall", "mode_unconfirmed", mu))
                    continue
                except _DeepThinkToggleFailed as toggle:
                    # 单题内两轮 trigger/option + native fallback 均失败后，当前
                    # session 的专家控件不可确认；本题诚实失败，后续同 mode 零交互
                    # aborted。normal 题仍可继续，避免专家不可用拖死快速补齐。
                    outcomes.append(
                        self._failure_outcome(spec, "wall", "deep_think_toggle_failed", toggle)
                    )
                    mode_toggle_blocked[spec.mode] = (spec, "deep_think_toggle_failed")
                    continue
                except _QuickModeToggleFailed as toggle:
                    # 与专家同款 session×mode 小熔断；绝不把未确认的专家态答案
                    # 按快速落库，也不让同批后续题重复撞同一个漂移控件。
                    outcomes.append(
                        self._failure_outcome(spec, "wall", "mode_toggle_failed", toggle)
                    )
                    mode_toggle_blocked[spec.mode] = (spec, "mode_toggle_failed")
                    continue
                except _IncompleteCapture as inc:
                    # 同上：截图为题级 flake，记 incomplete 后续跑，不中止整批。
                    outcomes.append(
                        self._failure_outcome(spec, "incomplete", "answer_capture_incomplete", inc)
                    )
                    continue
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
        exc: (
            _WallError
            | _IncompleteCapture
            | _DeepThinkToggleFailed
            | _QuickModeToggleFailed
            | _ModeUnconfirmed
        ),
    ) -> DoubaoBatchItemOutcome:
        return DoubaoBatchItemOutcome(
            business_key=spec.business_key,
            status=status,
            error_type=error_type,
            error_message=str(exc),
            evidence_path=exc.evidence_path,
            evidence=list(exc.evidence_refs),
        )

    @staticmethod
    def _aborted_outcome(
        spec: DoubaoBatchItemSpec,
        failed_spec: DoubaoBatchItemSpec,
        error_type: str | None,
        *,
        batch_stopped: bool = True,
    ) -> DoubaoBatchItemOutcome:
        # 真人撞墙后会停下：本题未执行（零浏览器交互），诚实标记不编造不硬闯。
        if batch_stopped:
            reason = (
                f"not executed: batch stopped after item {failed_spec.business_key!r} "
                f"failed ({error_type or 'unknown'}) — no browser interaction for this item"
            )
        else:
            # 批次未停，仅同 mode 余题占位：quota 或模式控件 session 小熔断。
            if error_type == "wall_quota":
                reason = (
                    f"not executed: same-mode quota wall at item {failed_spec.business_key!r} "
                    f"({error_type}) — no browser interaction for this item"
                )
            else:
                reason = (
                    f"not executed: same-mode mode-toggle circuit opened at item "
                    f"{failed_spec.business_key!r} ({error_type or 'unknown'}) — "
                    "no browser interaction for this item"
                )
        return DoubaoBatchItemOutcome(
            business_key=spec.business_key,
            status="aborted",
            error_type="aborted_after_failure",
            error_message=reason,
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
            resident = resident_cdp_url(self._config.browser_key) is not None
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
                        platform_browser(pw, platform=self._config.browser_key, launch=_launch)
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
        # 原始流量留痕（2026-08-10 起，用户拍板默认开）：独立 CDP session 自组
        # HAR + 落 completion 原始响应体，与既有 capture 互不干扰。
        # GEO_RAW_CAPTURE=0 → None（全关回退现状）。
        raw = maybe_raw_capture(
            context,
            page,
            body_url_hints=("/chat/completion",),
            creator="geo-doubao-adapter",
        )
        # 请求态≠实际态：目标模式的 UI picker 后置校验结果。None 仅表示旧版页面
        # 未暴露 picker（normal 沿用其默认快速态）；可观察到 picker 时必须显式确认。
        mode_ui_engaged: bool | None = None
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
                # 禁言 banner（2026-08-14 起）：composer 长期不可得时扫整页文本。
                # 只跑禁言 regex（整页含 UI 营销件，配额/拒答词表套整页必误伤，
                # 词表层已隔离）；命中改抛 wall_muted（带解封时间），走既有墙管道。
                muted = detect_muted_banner("doubao", _read_page_text(page))
                if muted is not None:
                    until_note = (
                        f" until={muted.until.isoformat()}" if muted.until is not None else ""
                    )
                    raise _WallError(
                        "wall_muted",
                        f"muted banner on page ({muted.phrase!r}){until_note}",
                        _shot("muted"),
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
                # 新会话 SPA 重建 composer 后模式 picker 可能晚于输入框挂载
                # （20260813 正式采集首批 3 任务均因此快速失败）：先有界等待再进
                # toggle，等不到仍按原语义诚实失败。
                picker_deadline = time.monotonic() + 20.0
                while _mode_picker(page) is None and time.monotonic() < picker_deadline:
                    page.wait_for_timeout(400)
                if not _try_enable_deep_think(page, self._rng):
                    raise _DeepThinkToggleFailed(
                        "deep_think mode picker could not be engaged "
                        "(selector drift or mode unavailable)",
                        _shot("deep_think"),
                    )
                # UI 后置校验通过 = 请求态已确认下达（实际态仍待 SSE 证据二次确认）。
                mode_ui_engaged = True
                _pace(*_PACE_AFTER_TOGGLE_S)  # 切完模式回神再回到输入框
                on_stage("typing")
            else:
                # normal 在当前豆包 UI 中就是「快速」。浏览器会继承上题的专家态，
                # 因此只在配置中写 normal 不够：picker 可观察时必须显式切回快速并
                # 后置确认，确认不了就拒绝错标落库。无 picker 仅兼容旧版默认快速 UI。
                picker = _mode_picker(page)
                picker_state = _picker_state(page)
                if picker is not None or picker_state:
                    on_stage("enable_quick_mode")
                    if not _try_enable_quick_mode(page, self._rng):
                        raise _QuickModeToggleFailed(
                            "normal mode requested but the composer picker could not be "
                            "confirmed in quick mode (selector drift or mode unavailable)",
                            _shot("quick_mode"),
                        )
                    mode_ui_engaged = True
                    _pace(*_PACE_AFTER_TOGGLE_S)
                    on_stage("typing")
                else:
                    log.info(
                        "doubao_quick_mode_legacy_default",
                        business_key=spec.business_key,
                    )
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
                if capture.has_completion_started() and time.monotonic() - challenge_start >= 3.5:
                    break
                page.wait_for_timeout(500)

            on_stage("await_stream")
            meta = capture.wait_finish(
                page, appearance_timeout_s=20.0, timeout_s=self._config.chat_timeout_s
            )
            answer_text = ""
            references: list[dict[str, Any]] = []
            search_queries: list[dict[str, Any]] = []
            retrieval_events: list[dict[str, Any]] = []
            sse_trace: dict[str, Any] | None = None
            sse_body = capture.latest_body()
            if sse_body:
                rich = _rich_record_from_sse(sse_body)
                if rich is not None:
                    answer_text = str(rich.get("answer_text") or "").strip()
                    references = list(rich.get("references") or [])
                    retrieval_events = list(rich.get("retrieval_events") or [])
                    # W1：结构化 trace（thinking/search/queries/stats，非全量原文）
                    sse_trace = _sse_trace_from_body(sse_body)
                    if sse_trace is not None:
                        search_queries = list(sse_trace.get("queries") or [])
            if meta.get("recovered"):
                # A recovery segment may begin at start_seq > 0, so its retained
                # network body is not guaranteed to contain the whole prose.  Once
                # the recovered stream and DOM have settled, prefer the complete
                # assistant bubble for answer text while keeping network-derived
                # references/trace explicitly as the observed subset.
                recovered_dom_text = _extract_response_text(page, spec.query)
                if recovered_dom_text:
                    answer_text = recovered_dom_text
            if not answer_text and meta.get("found"):
                # SSE 捕获竞态失败时的 DOM 兜底（旧链同款回退路径）
                answer_text = _extract_response_text(page, spec.query)
            on_stage("answer_extracted")

            # 软墙/实名扫描无条件执行（2026-08-14 起——曾被 `if not answer_text:`
            # 门挡，出了"答案"就绝不扫描 = 配额/禁言文案当答案采回的事故根因）。
            # 已出答案时把答案正文从扫描文本中剔除：「答案正文提及「过频/实名」
            # 不翻标记」的旧不变量保持成立（best-effort 精确串剔除，见
            # _scan_dom_notices）。
            notices = _scan_dom_notices(page, exclude=answer_text)
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

            # 答案验收门（2026-08-14 起，词表唯一真源 wall_lexicon）：答案文本
            # 定稿后、返回 ok 之前——平台提示文案（配额耗尽/禁言/拒答模板）被
            # 当作答案采回时在此拦截，抛 _WallError 走既有墙管道（batch 连坐
            # 语义按 wall_type 细化，见 collect_batch docstring）。batch 与
            # per-task 单题共用本路径，两路都盖。
            verdict = classify_answer_text("doubao", answer_text)
            if verdict is not None:
                raise _WallError(
                    verdict.wall_type,
                    _wall_verdict_message(verdict, answer_text),
                    _shot("answer_wall"),
                )

            on_stage("screenshot")
            shot_path = self._evidence_dir / f"{spec.file_stem}.png"
            try:
                _capture_full_page(page, shot_path, expected_question=spec.query)
            except Exception as exc:
                raise _IncompleteCapture(
                    f"evidence-screenshot-failed: {type(exc).__name__}: {exc}",
                    _shot("screenshot"),
                ) from exc
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
            answer_capture = capture_answer_evidence(
                page,
                assistant_selectors=_ASSISTANT_SELECTORS,
                answer_text=answer_text,
                output_path=self._evidence_dir / f"{spec.file_stem}-answer-evidence.png",
            )
            answer_evidence = (
                CollectionEvidenceRef(
                    kind="answer_excerpt_screenshot",
                    path=str(answer_capture.path),
                    relation_type="answer_evidence_excerpt",
                    mime_type="image/png",
                    source_url=_CHAT_URL,
                    anchors=answer_capture.anchors,
                )
                if answer_capture is not None and answer_capture.anchors
                else None
            )

            # 请求态≠实际态（旧链纪律：请求 deep_think ≠ 实际启用）。actual 仅当
            # SSE 证据（thinking root block_type=10040）为正才标 deep_think；证据
            # 缺失（DOM 兜底/解析失败）或为负一律如实 normal。结论注入 trace 的
            # mode 段随证据落盘——注入在截断阶梯之后（~120B 固定开销，不挤占
            # 业务字段水位）；normal 题同样记录（反向错配=请求 normal 实际 deep
            # 也如实可见）。
            mode_evidence = _mode_evidence(
                spec.mode, ui_engaged=mode_ui_engaged, sse_trace=sse_trace
            )
            if sse_trace is not None:
                sse_trace["mode"] = mode_evidence

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

            # mode 证据升级（2026-08-14 起，warning-only → non_retryable 诚实
            # 失败）：trace 已先落盘取证。请求 deep_think 而 SSE 无思考证据 =
            # 平台静默回退快速模式的嫌疑答案，绝不落 completed（2026-08-13
            # 事故教训：配额耗尽后的回退答案曾被当 deep_think 有效答案采回）。
            if spec.mode == "deep_think" and mode_evidence["actual"] != "deep_think":
                raise _ModeUnconfirmed(
                    "deep_think requested and UI toggle confirmed, but SSE stream "
                    "carries no thinking-root evidence (sse_deep_think_active="
                    f"{mode_evidence['sse_deep_think_active']}) — refusing to record "
                    "a normal-evidence answer as deep_think",
                    _shot("mode_unconfirmed"),
                )
            if spec.mode == "normal" and mode_evidence["actual"] != "normal":
                raise _ModeUnconfirmed(
                    "quick mode requested and UI picker confirmed, but SSE stream carries "
                    "thinking-root evidence — refusing to record a deep-think answer as quick",
                    _shot("mode_unconfirmed"),
                )

            # Official share output is part of the successful-capture contract.  A
            # runtime browser screenshot is audit evidence, not a substitute for the
            # user-requested official share image/link.
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
                    "capture_method": "share_image",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            share_image_ok = _verify_doubao_generated_share_image(
                share_image_path,
                share_image_audit,
                expected_question=spec.query,
                expected_answer=answer_text,
            )
            if not share_image_ok:
                first_image_audit = share_image_audit
                try:
                    share_image_audit = capture_share_image(human_page, share_image_path)
                except Exception as exc:
                    share_image_audit = {
                        "ok": False,
                        "capture_method": "share_image",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                share_image_audit["content_retry_of"] = {
                    "error": first_image_audit.get("error"),
                    "channel": first_image_audit.get("channel"),
                    "content_verification": first_image_audit.get("content_verification"),
                }
                share_image_ok = _verify_doubao_generated_share_image(
                    share_image_path,
                    share_image_audit,
                    expected_question=spec.query,
                    expected_answer=answer_text,
                )
            if not share_image_ok:
                # A syntactically valid PNG can still be Doubao's half-hydrated
                # card (for example, answer head only).  Never leave that payload
                # at the canonical ``*-share.png`` path where an operator or a
                # later filesystem scan could mistake it for admitted evidence.
                rejected_path = share_image_path.with_name(
                    f"{share_image_path.stem}-rejected{share_image_path.suffix}"
                )
                try:
                    rejected_path.unlink(missing_ok=True)
                    share_image_path.replace(rejected_path)
                except OSError as exc:
                    share_image_audit["rejected_image_quarantine_error"] = (
                        f"{type(exc).__name__}: {exc}"
                    )
                else:
                    share_image_audit["rejected_image_path"] = str(rejected_path)
            try:
                share_link_audit = capture_share_link(human_page)
            except Exception as exc:
                share_link_audit = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            share_url = _validated_doubao_share_url(share_link_audit.get("url"))
            share_link_ok = bool(share_link_audit.get("ok")) and share_url is not None
            if not share_image_ok and share_link_ok and share_url:
                generated_failure = {
                    "capture_method": share_image_audit.get("capture_method"),
                    "channel": share_image_audit.get("channel"),
                    "content_verification": share_image_audit.get("content_verification"),
                    "error": share_image_audit.get("error"),
                    "rejected_image_path": share_image_audit.get("rejected_image_path"),
                }
                fallback_audit = _capture_official_share_page(
                    context,
                    share_url,
                    share_image_path,
                    expected_question=spec.query,
                    expected_answer=answer_text,
                )
                fallback_audit["fallback_of"] = generated_failure
                share_image_audit = fallback_audit
                share_image_ok = bool(fallback_audit.get("ok"))
                if not share_image_ok and share_image_path.exists():
                    page_rejected_path = share_image_path.with_name(
                        f"{share_image_path.stem}-page-rejected{share_image_path.suffix}"
                    )
                    try:
                        page_rejected_path.unlink(missing_ok=True)
                        share_image_path.replace(page_rejected_path)
                    except OSError as exc:
                        share_image_audit["rejected_image_quarantine_error"] = (
                            f"{type(exc).__name__}: {exc}"
                        )
                    else:
                        share_image_audit["rejected_image_path"] = str(page_rejected_path)
            share_verification_path = (
                self._evidence_dir / f"{spec.file_stem}-share-verification.json"
            )
            if share_image_ok:
                try:
                    image_bytes = share_image_path.read_bytes()
                    with Image.open(io.BytesIO(image_bytes)) as verified_image:
                        verified_image.load()
                        verified_dimensions = {
                            "width": verified_image.width,
                            "height": verified_image.height,
                        }
                    share_verification_path.write_text(
                        json.dumps(
                            {
                                "answer_sha256": sha256(answer_text.encode()).hexdigest(),
                                "capture_method": share_image_audit.get("capture_method"),
                                "channel": share_image_audit.get("channel"),
                                "page_capture_method": share_image_audit.get("page_capture_method"),
                                "content_verification": share_image_audit.get(
                                    "content_verification"
                                ),
                                "dimensions": verified_dimensions,
                                "image_sha256": sha256(image_bytes).hexdigest(),
                                "platform": "doubao",
                                "question_sha256": sha256(spec.query.encode()).hexdigest(),
                                "schema_version": "official-share-verification-v1",
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        encoding="utf-8",
                    )
                except (OSError, UnidentifiedImageError, ValueError) as exc:
                    share_image_ok = False
                    share_image_audit["verification_manifest_error"] = (
                        f"{type(exc).__name__}: {exc}"
                    )
            if share_image_ok:
                evidence.append(
                    CollectionEvidenceRef(
                        kind="share_image",
                        path=str(share_image_path),
                        relation_type="official_share_image",
                        mime_type="image/png",
                        source_url=share_url,
                    )
                )
                evidence.append(
                    CollectionEvidenceRef(
                        kind="share_verification",
                        path=str(share_verification_path),
                        relation_type="official_share_verification",
                        mime_type="application/json",
                        source_url=share_url,
                    )
                )
            if share_link_ok and share_url:
                share_link_path = self._evidence_dir / f"{spec.file_stem}-share-link.json"
                try:
                    write_share_link_manifest(
                        share_link_path,
                        share_url=share_url,
                        platform="doubao",
                        channel=(
                            str(share_link_audit.get("channel"))
                            if share_link_audit.get("channel")
                            else None
                        ),
                        verification=probe_official_share_url(
                            share_url,
                            allowed_hosts={"doubao.com", "www.doubao.com"},
                        ),
                    )
                except OSError as exc:
                    share_link_ok = False
                    share_link_audit["manifest_error"] = f"{type(exc).__name__}: {exc}"
                else:
                    evidence.append(
                        CollectionEvidenceRef(
                            kind="share_link",
                            path=str(share_link_path),
                            relation_type="official_share_link",
                            mime_type="application/json",
                            source_url=share_url,
                        )
                    )
            if not share_image_ok or not share_link_ok:
                image_verification = share_image_audit.get("content_verification")
                image_failure_reasons = (
                    image_verification.get("failure_reasons")
                    if isinstance(image_verification, dict)
                    else None
                )
                log.warning(
                    "doubao_share_export_incomplete",
                    business_key=spec.business_key,
                    image_ok=share_image_ok,
                    image_error=str(
                        share_image_audit.get("error")
                        or ",".join(str(reason) for reason in image_failure_reasons or ())
                    )[:300],
                    link_ok=share_link_ok,
                    link_error=str(
                        share_link_audit.get("error")
                        or share_link_audit.get("manifest_error")
                        or ""
                    )[:300],
                )
                raise _IncompleteCapture(
                    "official-share-export-incomplete: both a valid platform share PNG "
                    "and an official public share URL are required",
                    _shot("share_export"),
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
            answer = CollectedAnswer(
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
                    "mode": mode_evidence,
                },
                search_queries=search_queries,
                retrieval_events=retrieval_events,
                answer_evidence=answer_evidence,
            )
        except (
            _WallError,
            _IncompleteCapture,
            _DeepThinkToggleFailed,
            _QuickModeToggleFailed,
            _ModeUnconfirmed,
        ) as exc:
            # 失败题同样留 raw/HAR（题末先 dump 后 detach）：ref 挂异常对象，经
            # _failure_outcome → 失败 result.evidence → persist 层进 CAS。
            exc.evidence_refs = dump_raw_evidence_refs(
                raw,
                self._evidence_dir,
                spec.file_stem,
                source_url=_CHAT_URL,
                warn_tag="doubao",
            )
            raise
        else:
            evidence.extend(
                dump_raw_evidence_refs(
                    raw,
                    self._evidence_dir,
                    spec.file_stem,
                    source_url=_CHAT_URL,
                    warn_tag="doubao",
                )
            )
            return answer
        finally:
            # batch 内每题一个 CDP session：题末 best-effort detach，避免旧
            # session 挂着监听累积（下一题新建 capture，绝不串题读到旧流）。
            if raw is not None:
                raw.detach()
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

    def __init__(self, page: Any, rng: random.Random, start: tuple[float, float] | None) -> None:
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


def _share_text_compact(value: str) -> str:
    """Normalize Markdown/OCR punctuation while retaining semantic characters."""

    return re.sub(r"[^0-9A-Za-z\u3400-\u9fff]+", "", value).lower()


def _share_answer_sequence_coverage(
    expected_answer: str,
    recognized_text: str,
    *,
    chunk_width: int = 10,
) -> tuple[float, bool, bool, int, int]:
    """Measure ordered OCR character coverage and explicit head/tail presence.

    OCR commonly substitutes one glyph inside an otherwise correct run (for
    example ``1``/``l`` or a CJK homograph).  Requiring every fixed-width chunk
    to match exactly amplified one such substitution into ten missing
    characters and rejected complete official share cards.  SequenceMatcher's
    ordered exact blocks count only characters that remain in answer order, so
    it tolerates sparse OCR substitutions without admitting reordered or
    materially truncated content.

    ``chunk_width`` now defines the head/tail probe span and remains a keyword
    argument for callers that used the previous fixed-chunk implementation.
    """

    expected = _share_text_compact(expected_answer)
    recognized = _share_text_compact(recognized_text)
    if not expected or not recognized:
        return 0.0, False, False, 0, 0
    matcher = difflib.SequenceMatcher(None, expected, recognized, autojunk=False)
    blocks = tuple(block for block in matcher.get_matching_blocks() if block.size)
    matched_characters = sum(block.size for block in blocks)
    edge_span = min(max(chunk_width * 2, 1), len(expected))
    head_ok = any(block.a < edge_span for block in blocks)
    tail_start = len(expected) - edge_span
    tail_ok = any(block.a + block.size > tail_start for block in blocks)
    return (
        matched_characters / len(expected),
        head_ok,
        tail_ok,
        matched_characters,
        len(expected),
    )


def _verify_doubao_generated_share_image(
    path: Path,
    audit: dict[str, Any],
    *,
    expected_question: str,
    expected_answer: str,
) -> bool:
    """Prove an exported PNG contains this turn, including its answer tail."""

    transport_ok = (
        bool(audit.get("ok"))
        and audit.get("capture_method") == "share_image"
        and audit.get("channel") in {"download", "cdp_download_path", "blob", "data"}
        and recover_png_from_export_audit(path, audit)
    )
    return _verify_share_image_ocr_content(
        path,
        audit,
        expected_question=expected_question,
        expected_answer=expected_answer,
        transport_ok=transport_ok,
    )


def _verify_share_image_ocr_content(
    path: Path,
    audit: dict[str, Any],
    *,
    expected_question: str,
    expected_answer: str,
    transport_ok: bool,
) -> bool:
    """Apply the same fail-closed OCR contract to every official image channel."""

    verification: dict[str, Any] = {
        "transport_ok": transport_ok,
        "question_verified": False,
        "answer_coverage": 0.0,
        "answer_head_verified": False,
        "answer_tail_verified": False,
        "failure_reasons": [],
    }
    audit["content_verification"] = verification
    if not transport_ok:
        verification["failure_reasons"] = ["transport_unverified"]
        return False
    recognized = recognize_image_text(path)
    if not recognized:
        verification["error"] = "share_image_ocr_unavailable_or_empty"
        verification["failure_reasons"] = ["ocr_unavailable_or_empty"]
        return False
    compact_ocr = _share_text_compact(recognized)
    compact_question = _share_text_compact(expected_question)
    question_ok = bool(compact_question) and compact_question in compact_ocr
    coverage, head_ok, tail_ok, matched_chars, total_chars = _share_answer_sequence_coverage(
        expected_answer, recognized
    )
    # The official public thread is rendered as a wide HTML table before the
    # full-page screenshot is OCRed.  Column-wise OCR can reorder or omit a
    # small part of the middle even when the screenshot visibly contains the
    # complete answer.  Keep generated cards on the stricter threshold.  The
    # measured 80% floor is available only when the independently URL-bound
    # official page has already passed the separate high-coverage DOM proof;
    # setting the channel name alone must never relax image admission.
    dom_verification = audit.get("dom_content_verification")
    official_dom_verified = (
        audit.get("channel") == "official_share_page_screenshot"
        and isinstance(dom_verification, dict)
        and dom_verification.get("ok") is True
    )
    coverage_threshold = (
        0.80 if official_dom_verified else 0.85
    )
    verification.update(
        {
            "ocr_text_length": len(recognized),
            "question_verified": question_ok,
            "answer_coverage": round(coverage, 4),
            "answer_coverage_threshold": coverage_threshold,
            "dom_content_verified": official_dom_verified,
            "answer_head_verified": head_ok,
            "answer_tail_verified": tail_ok,
            "answer_characters_verified": matched_chars,
            "answer_characters_total": total_chars,
        }
    )
    failure_reasons: list[str] = []
    if not question_ok:
        failure_reasons.append("question_mismatch")
    if coverage < coverage_threshold:
        failure_reasons.append("answer_coverage_below_threshold")
    if not head_ok:
        failure_reasons.append("answer_head_missing")
    if not tail_ok:
        failure_reasons.append("answer_tail_missing")
    verification["failure_reasons"] = failure_reasons
    return not failure_reasons


def _capture_official_share_page(
    context: Any,
    share_url: str,
    output_path: Path,
    *,
    expected_question: str,
    expected_answer: str,
) -> dict[str, Any]:
    """把豆包官方 ``/thread`` 分享页存为 PNG，并校验它确属当前问答。

    分享 URL 本身已通过 host/path 白名单；这里先以当前问题和答案前缀校验可见
    DOM，防止剪贴板残留把上一题链接挂到本题。随后展开官方页面的纵向内容和
    横向表格，截图后再执行与生成卡完全相同的 OCR 覆盖/首尾校验。使用临时
    tab，不改变采集主页面。
    """

    audit: dict[str, Any] = {
        "ok": False,
        "channel": "official_share_page_screenshot",
        "capture_method": "official_share_page_screenshot",
        "url": share_url,
        "path": str(output_path),
        "error": None,
        "question_verified": False,
        "answer_verified": False,
    }
    share_page: Any | None = None

    def _compact(value: str) -> str:
        # SSE 正文保留 Markdown（#、**、列表符），官方分享页呈现的是渲染后
        # 可见文本；比较前移除纯格式符，语义文字仍须逐字对应。
        # 豆包的有序列表序号由 CSS 绘制，不会进入 ``inner_text``；先移除
        # Markdown 行首列表标记，避免把同一官方正文误判成链接串题。
        without_list_markers = re.sub(r"(?m)^\s*(?:[-+*]|\d+[.)、])\s+", "", value)
        without_markdown = re.sub(r"[#*_>`~\[\]()]", "", without_list_markers)
        return re.sub(r"\s+", "", without_markdown)

    try:
        share_page = context.new_page()
        response = share_page.goto(share_url, wait_until="domcontentloaded", timeout=30_000)
        status = getattr(response, "status", None) if response is not None else None
        audit["http_status"] = status
        if isinstance(status, int) and status >= 400:
            raise RuntimeError(f"official share page returned HTTP {status}")

        # SSR shell 会先到，问答正文随后水合。轮询可见 body 文本，不依赖豆包
        # 对问题节点的拆分方式（长问题可能被多个 span 分段）。
        compact_question = _compact(expected_question)
        share_page.wait_for_function(
            """question => String(document.body?.innerText || '')
              .replace(/\\s+/g, '').includes(question)""",
            arg=compact_question,
            timeout=25_000,
        )
        body_text = str(share_page.locator("body").inner_text(timeout=10_000) or "")
        audit["body_text_length"] = len(body_text)
        compact_body = _compact(body_text)
        compact_answer = _compact(expected_answer)
        # 取足以区分题目的答案前缀，同时避免长正文因 UI 插入工具条而精确串失败。
        answer_probe = compact_answer[:80]
        audit["answer_probe_length"] = len(answer_probe)
        audit["question_verified"] = bool(compact_question) and compact_question in compact_body
        audit["answer_probe_verified"] = bool(answer_probe) and answer_probe in compact_body
        dom_coverage, dom_head_ok, dom_tail_ok, dom_matched, dom_total = (
            _share_answer_sequence_coverage(expected_answer, body_text)
        )
        dom_threshold = 0.95
        dom_ok = (
            audit["question_verified"]
            and audit["answer_probe_verified"]
            and dom_coverage >= dom_threshold
            and dom_head_ok
            and dom_tail_ok
        )
        audit["dom_content_verification"] = {
            "ok": dom_ok,
            "answer_coverage": round(dom_coverage, 4),
            "answer_coverage_threshold": dom_threshold,
            "answer_head_verified": dom_head_ok,
            "answer_tail_verified": dom_tail_ok,
            "answer_characters_verified": dom_matched,
            "answer_characters_total": dom_total,
        }
        audit["answer_verified"] = dom_ok
        if not dom_ok:
            raise RuntimeError("official share page content does not match the current Q&A")

        capture = capture_full_page_safely(
            share_page,
            output_path,
            flatten_script=_DOUBAO_OFFICIAL_SHARE_FLATTEN_JS,
        )
        audit["page_capture_method"] = capture.get("method")
        audit["layout_metrics"] = capture.get("metrics")
        with Image.open(output_path) as image:
            image.load()
            if image.format != "PNG" or image.width < 320 or image.height < 240:
                raise RuntimeError(
                    f"invalid official share PNG: format={image.format} "
                    f"size={image.width}x{image.height}"
                )
            audit["dims"] = {"width": image.width, "height": image.height}
        audit["size"] = output_path.stat().st_size
        image_ok = _verify_share_image_ocr_content(
            output_path,
            audit,
            expected_question=expected_question,
            expected_answer=expected_answer,
            transport_ok=True,
        )
        if not image_ok:
            reasons = audit["content_verification"].get("failure_reasons") or ()
            raise RuntimeError(
                "official share page screenshot failed content verification: "
                + ",".join(str(reason) for reason in reasons)
            )
        audit["ok"] = True
        return audit
    except Exception as exc:
        audit["error"] = f"{type(exc).__name__}: {exc}"
        return audit
    finally:
        if share_page is not None:
            try:
                share_page.close()
            except Exception:
                pass


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
    """Fail closed: collection-time citation cards are not brand evidence.

    This adapter has no authoritative project ``Brand.name``/alias context.  The
    removed implementation copied a citation summary into a fixed page overlay and
    screenshotted it, which could visually claim text existed on a page even when it
    did not.  Real brand evidence is now produced downstream by ``source_fetch``
    only after source text and live DOM both contain an exact project brand term.
    """

    del context, evidence_dir, file_stem, timeout_error
    requested = min(
        _source_screenshot_limit(),
        len(
            {
                url
                for reference in references
                if (url := _external_http_url(reference.get("url"))) is not None
            }
        ),
    )
    return [], {
        "requested": requested,
        "captured": 0,
        "failures": [],
        "skipped": "brand_context_required_for_evidence",
    }


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
        self._loading_failed_at: dict[str, float] = {}
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
                self._loading_failed_at[req_id] = time.monotonic()
                # Chromium may still expose the received prefix after a resumable
                # SSE transport failure.  Preserve it when available so a recovery
                # request (start_seq > 0) can be assembled with the original prefix.
                if req_id in self._completion_request_ids:
                    self._fetch_body(req_id)
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
        # Doubao resumes an interrupted logical answer with a second completion
        # request carrying ``start_seq``.  Return every retained segment in wire
        # order; the SSE assembler already applies later block updates by id.
        return "\n".join(
            body for rid in self._completion_request_ids if (body := self._bodies.get(rid, ""))
        )

    def wait_finish(
        self,
        page: Any,
        *,
        appearance_timeout_s: float,
        timeout_s: float,
        dom_quiet_s: float = 2.0,
        recovery_grace_s: float = 15.0,
    ) -> dict[str, Any]:
        """Wait for one logical answer, including Doubao's SSE recovery request.

        A long ``deep_think`` response can lose its first event-stream connection.
        The web client immediately reconnects with the same message and a positive
        ``start_seq``.  Treating the first ``loadingFailed`` as final used to take a
        diagnostic screenshot while the recovered answer was still rendering.
        We now follow newly observed completion requests for a bounded grace period
        and only declare success after the latest segment reaches
        ``loadingFinished``.
        """
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
                "request_count": 0,
                "recovered": False,
                "elapsed_ms": int((time.monotonic() - t0) * 1000),
            }
        failed_segments: set[str] = set()
        while time.monotonic() < overall_deadline:
            if target in self._loading_finished:
                page.wait_for_timeout(int(dom_quiet_s * 1000))
                return {
                    "found": True,
                    "finished": True,
                    "failed": bool(failed_segments),
                    "bytes_received": sum(
                        self._bytes.get(rid, 0) for rid in self._completion_request_ids
                    ),
                    "request_count": len(self._completion_request_ids),
                    "recovered": bool(failed_segments),
                    "elapsed_ms": int((time.monotonic() - t0) * 1000),
                }
            if target in self._loading_failed:
                failed_segments.add(target)
                try:
                    target_index = self._completion_request_ids.index(target)
                except ValueError:
                    target_index = -1
                if target_index >= 0 and target_index + 1 < len(self._completion_request_ids):
                    target = self._completion_request_ids[target_index + 1]
                    continue
                failed_at = self._loading_failed_at.get(target, time.monotonic())
                if time.monotonic() >= min(overall_deadline, failed_at + recovery_grace_s):
                    break
            page.wait_for_timeout(150)
        return {
            "found": True,
            "finished": False,
            "failed": bool(failed_segments) or target in self._loading_failed,
            "bytes_received": sum(self._bytes.get(rid, 0) for rid in self._completion_request_ids),
            "request_count": len(self._completion_request_ids),
            "recovered": False,
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


def _read_page_text(page: Any) -> str:
    """best-effort 读 body 文本（2s 超时；失败=空串，绝不因此拖垮采集）。"""
    try:
        return str(page.locator("body").inner_text(timeout=2000) or "")
    except Exception:
        return ""


def _scan_dom_notices(page: Any, *, exclude: str = "") -> dict[str, list[str]]:
    """best-effort 读 body 文本扫系统通知词（softban 过频 / 实名墙）。

    2026-08-14 起调用方无条件扫描（不再 gated by 无答案）；``exclude`` 传入
    已定稿答案正文做精确串剔除——系统通知在答案气泡之外，剔除后「答案正文
    提及「过频/实名」不翻标记」的旧不变量保持成立（SSE 正文与 DOM 渲染可能有
    空白差，剔除是 best-effort，词表本身已是平台口吻短语）。"""
    text = _read_page_text(page)
    if exclude:
        text = text.replace(exclude, " ")
    return {
        "softban": [p for p in _SOFTBAN_DOM_PHRASES if p in text],
        "realname": [p for p in _REALNAME_DOM_PHRASES if p in text],
    }


def _wall_hit_fragment(text: str, phrase: str, *, limit: int = 120) -> str:
    """命中证据片段：命中短语前后各留上下文，整体截断（答案文本本身就是要
    落库的采集内容，绝不含秘密）。phrase 为 regex 命中串时同样是原文子串。"""
    idx = text.find(phrase)
    if idx < 0:
        return text[:limit]
    start = max(0, idx - 20)
    fragment = text[start : idx + len(phrase) + 80].strip()
    if len(fragment) <= limit:
        return fragment
    return fragment[: limit - 1] + "…"


def _wall_verdict_message(verdict: WallVerdict, answer_text: str) -> str:
    """答案验收门命中 → _WallError message（类型 + 短语 + 截断片段 + 禁言解封时间）。"""
    fragment = _wall_hit_fragment(answer_text, verdict.phrase)
    until_note = f" until={verdict.until.isoformat()}" if verdict.until is not None else ""
    return (
        f"answer-text wall hit [{verdict.wall_type}] phrase={verdict.phrase!r}"
        f"{until_note} fragment={fragment!r}"
    )


class _DoubaoScopedCaptureError(RuntimeError):
    """The current one-question Doubao answer could not be captured exactly."""


class _DoubaoCaptureLayoutChanged(_DoubaoScopedCaptureError):
    """The same answer is still hydrating and its measured layout moved."""


def _capture_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _DoubaoScopedCaptureError(f"capture metric {name} was not numeric")
    number = float(value)
    if not math.isfinite(number):
        raise _DoubaoScopedCaptureError(f"capture metric {name} was not finite")
    return number


def _read_doubao_capture_state(
    page: Any, *, expected_question: str, scroll_top: float | None = None
) -> dict[str, Any]:
    if not expected_question.strip():
        raise _DoubaoScopedCaptureError("Doubao expected question was empty")
    raw = page.evaluate(
        _DOUBAO_CAPTURE_STATE_JS,
        {"expectedQuestion": expected_question, "scrollTop": scroll_top},
    )
    if not isinstance(raw, dict):
        raise _DoubaoScopedCaptureError("Doubao capture probe returned no state")
    if raw.get("ok") is not True:
        reason = str(raw.get("error") or "unknown DOM shape")
        raise _DoubaoScopedCaptureError(f"Doubao scoped capture unavailable: {reason}")
    raw_blocks = raw.get("blocks")
    if not isinstance(raw_blocks, list) or len(raw_blocks) != 2:
        raise _DoubaoScopedCaptureError("Doubao capture requires exactly two message blocks")

    blocks: list[dict[str, Any]] = []
    for index, raw_block in enumerate(raw_blocks):
        if not isinstance(raw_block, dict):
            raise _DoubaoScopedCaptureError("Doubao message block was malformed")
        expected_role = "question" if index == 0 else "answer"
        if raw_block.get("role") != expected_role:
            raise _DoubaoScopedCaptureError(f"Doubao message order changed at {expected_role}")
        fingerprint = raw_block.get("fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            raise _DoubaoScopedCaptureError(f"Doubao {expected_role} text fingerprint was missing")
        blocks.append(
            {
                "role": expected_role,
                "top": _capture_number(raw_block.get("top"), f"{expected_role}.top"),
                "bottom": _capture_number(raw_block.get("bottom"), f"{expected_role}.bottom"),
                "left": _capture_number(raw_block.get("left"), f"{expected_role}.left"),
                "right": _capture_number(raw_block.get("right"), f"{expected_role}.right"),
                "fingerprint": fingerprint,
            }
        )

    state: dict[str, Any] = {
        "scroll_top": _capture_number(raw.get("scroll_top"), "scroll_top"),
        "scroll_height": _capture_number(raw.get("scroll_height"), "scroll_height"),
        "max_scroll": _capture_number(raw.get("max_scroll"), "max_scroll"),
        "viewport_height": _capture_number(raw.get("viewport_height"), "viewport_height"),
        "clip_x": _capture_number(raw.get("clip_x"), "clip_x"),
        "clip_y": _capture_number(raw.get("clip_y"), "clip_y"),
        "clip_width": _capture_number(raw.get("clip_width"), "clip_width"),
        "capture_height": _capture_number(raw.get("capture_height"), "capture_height"),
        "blocks": blocks,
    }
    if state["clip_width"] > _DOUBAO_CAPTURE_MAX_WIDTH_CSS_PX:
        raise _DoubaoScopedCaptureError("Doubao message capture width exceeded safety limit")
    total_height = sum(block["bottom"] - block["top"] for block in blocks)
    if total_height <= 0 or total_height > _DOUBAO_CAPTURE_MAX_HEIGHT_CSS_PX:
        raise _DoubaoScopedCaptureError("Doubao message capture height was unsafe")
    return state


def _assert_doubao_capture_stable(
    expected: dict[str, Any],
    actual: dict[str, Any],
    *,
    requested_scroll_top: float,
) -> None:
    if abs(actual["scroll_top"] - requested_scroll_top) > 1:
        raise _DoubaoCaptureLayoutChanged(
            "Doubao chat scroller did not settle at the requested tile"
        )
    for key in (
        "scroll_height",
        "max_scroll",
        "viewport_height",
        "clip_x",
        "clip_y",
        "clip_width",
        "capture_height",
    ):
        if abs(actual[key] - expected[key]) > 1:
            raise _DoubaoCaptureLayoutChanged(f"Doubao chat layout changed during capture ({key})")
    for expected_block, actual_block in zip(expected["blocks"], actual["blocks"], strict=True):
        role = expected_block["role"]
        if actual_block["fingerprint"] != expected_block["fingerprint"]:
            raise _DoubaoScopedCaptureError(f"Doubao {role} text changed during screenshot capture")
        for key in ("top", "bottom", "left", "right"):
            if abs(actual_block[key] - expected_block[key]) > 1:
                raise _DoubaoCaptureLayoutChanged(
                    f"Doubao {role} bounds changed during screenshot capture"
                )


def _doubao_tile_positions(
    *,
    top: float,
    bottom: float,
    viewport_height: float,
    max_scroll: float,
) -> list[float]:
    first = min(max(top, 0.0), max_scroll)
    last = min(max(bottom - viewport_height, 0.0), max_scroll)
    if last <= first + 0.5:
        return [first]
    step = viewport_height - _DOUBAO_CAPTURE_OVERLAP_CSS_PX
    if step <= 1:
        raise _DoubaoScopedCaptureError("Doubao chat viewport was too short to tile")
    count = int(math.ceil((last - first) / step)) + 1
    if count > _DOUBAO_CAPTURE_MAX_TILES:
        raise _DoubaoScopedCaptureError("Doubao answer required too many screenshot tiles")
    stride = (last - first) / (count - 1)
    return [first + stride * index for index in range(count)]


def _capture_doubao_message_block(
    page: Any,
    *,
    expected_question: str,
    expected: dict[str, Any],
    block: dict[str, Any],
) -> tuple[Image.Image, int]:
    positions = _doubao_tile_positions(
        top=block["top"],
        bottom=block["bottom"],
        viewport_height=expected["capture_height"],
        max_scroll=expected["max_scroll"],
    )
    canvas: Image.Image | None = None
    scale_x: float | None = None
    scale_y: float | None = None
    painted_until = block["top"]
    try:
        for position in positions:
            state = _read_doubao_capture_state(
                page,
                expected_question=expected_question,
                scroll_top=position,
            )
            _assert_doubao_capture_stable(expected, state, requested_scroll_top=position)
            # Two rAFs ran in the probe; one short paint window then a second
            # read proves neither virtual row nor its text was recycled meanwhile.
            page.wait_for_timeout(75)
            stable_state = _read_doubao_capture_state(page, expected_question=expected_question)
            _assert_doubao_capture_stable(
                expected,
                stable_state,
                requested_scroll_top=state["scroll_top"],
            )
            clip = {
                "x": expected["clip_x"],
                "y": expected["clip_y"],
                "width": expected["clip_width"],
                "height": expected["capture_height"],
            }
            raw_png = page.screenshot(clip=clip, timeout=15_000)
            if not isinstance(raw_png, bytes | bytearray):
                raise _DoubaoScopedCaptureError("Doubao screenshot API returned no PNG bytes")
            try:
                with Image.open(io.BytesIO(bytes(raw_png))) as opened:
                    opened.load()
                    tile = opened.convert("RGB")
            except (OSError, UnidentifiedImageError, ValueError) as exc:
                raise _DoubaoScopedCaptureError(
                    "Doubao screenshot tile was not a valid image"
                ) from exc

            current_scale_x = tile.width / expected["clip_width"]
            current_scale_y = tile.height / expected["capture_height"]
            if current_scale_x <= 0 or current_scale_y <= 0:
                tile.close()
                raise _DoubaoScopedCaptureError("Doubao screenshot tile scale was invalid")
            if scale_x is None or scale_y is None:
                scale_x = current_scale_x
                scale_y = current_scale_y
                if abs(scale_x - scale_y) > max(scale_x, scale_y) * 0.02:
                    tile.close()
                    raise _DoubaoScopedCaptureError(
                        "Doubao screenshot tile used inconsistent pixel scaling"
                    )
                canvas = Image.new(
                    "RGB",
                    (
                        tile.width,
                        max(1, round((block["bottom"] - block["top"]) * scale_y)),
                    ),
                    "white",
                )
            elif (
                abs(current_scale_x - scale_x) > 0.01
                or abs(current_scale_y - scale_y) > 0.01
                or canvas is None
                or tile.width != canvas.width
            ):
                tile.close()
                raise _DoubaoScopedCaptureError(
                    "Doubao screenshot tile dimensions changed during capture"
                )

            visible_start = max(state["scroll_top"], block["top"])
            visible_end = min(state["scroll_top"] + expected["capture_height"], block["bottom"])
            segment_start = max(visible_start, painted_until)
            if segment_start > painted_until + 1:
                tile.close()
                raise _DoubaoScopedCaptureError("Doubao screenshot tiles contained a gap")
            if visible_end > segment_start:
                assert scale_y is not None and canvas is not None
                source_top = round((segment_start - state["scroll_top"]) * scale_y)
                source_bottom = round((visible_end - state["scroll_top"]) * scale_y)
                destination_top = round((segment_start - block["top"]) * scale_y)
                source_top = min(max(source_top, 0), tile.height)
                source_bottom = min(max(source_bottom, source_top), tile.height)
                segment = tile.crop((0, source_top, tile.width, source_bottom))
                remaining = canvas.height - destination_top
                if segment.height > remaining:
                    segment = segment.crop((0, 0, segment.width, max(0, remaining)))
                if segment.height > 0:
                    canvas.paste(segment, (0, destination_top))
                segment.close()
                painted_until = visible_end
            tile.close()

        if canvas is None or painted_until < block["bottom"] - 1:
            raise _DoubaoScopedCaptureError(
                f"Doubao {block['role']} screenshot tiles were incomplete"
            )
        return canvas, len(positions)
    except BaseException:
        if canvas is not None:
            canvas.close()
        raise


def _compose_doubao_message_capture(
    page: Any,
    *,
    expected_question: str,
    expected: dict[str, Any],
) -> tuple[Image.Image, int]:
    """Capture one stable layout snapshot and close all intermediate tiles."""

    block_images: list[Image.Image] = []
    try:
        tile_count = 0
        for block in expected["blocks"]:
            image, count = _capture_doubao_message_block(
                page,
                expected_question=expected_question,
                expected=expected,
                block=block,
            )
            block_images.append(image)
            tile_count += count
        widths = {image.width for image in block_images}
        if len(widths) != 1:
            raise _DoubaoScopedCaptureError("Doubao question and answer screenshot widths differed")
        final_image = Image.new(
            "RGB",
            (block_images[0].width, sum(image.height for image in block_images)),
            "white",
        )
        paste_y = 0
        for image in block_images:
            final_image.paste(image, (0, paste_y))
            paste_y += image.height
        return final_image, tile_count
    finally:
        for image in block_images:
            image.close()


def _restore_doubao_capture_scroll(page: Any, scroll_top: float) -> None:
    restored = page.evaluate(_DOUBAO_CAPTURE_RESTORE_JS, scroll_top)
    if not isinstance(restored, dict) or restored.get("ok") is not True:
        reason = (
            str(restored.get("error") or restored.get("actual_scroll_top"))
            if isinstance(restored, dict)
            else "no restore result"
        )
        raise _DoubaoScopedCaptureError(
            f"Doubao chat scroll position could not be restored: {reason}"
        )


def _capture_full_page(page: Any, out_path: Path, *, expected_question: str) -> dict[str, Any]:
    """Capture only the current question and answer content from Doubao.

    The two unique ``[data-message-id]`` nodes exclude both message action bars,
    follow-up suggestions, the composer, and the left history rail.  Tall answer
    nodes are captured through the validated chat scroller in overlapping tiles;
    no DOM style is changed, and the original ``scrollTop`` is restored even when
    a screenshot or virtual-row stability check fails.  There is deliberately no
    whole-page/flatten fallback because that would create misleading evidence.
    """

    initial = _read_doubao_capture_state(page, expected_question=expected_question)
    original_scroll_top = initial["scroll_top"]
    final_image: Image.Image | None = None
    tile_count = 0
    layout_attempts = 0
    capture_error: BaseException | None = None
    restore_error: BaseException | None = None
    try:
        expected = initial
        for attempt in range(1, 4):
            layout_attempts = attempt
            try:
                final_image, tile_count = _compose_doubao_message_capture(
                    page,
                    expected_question=expected_question,
                    expected=expected,
                )
                break
            except _DoubaoCaptureLayoutChanged:
                if attempt >= 3:
                    raise
                # Related cards and videos hydrate after the answer stream closes.
                # Re-measure the same fingerprint from the original position and
                # retry the whole tile set; never mix tiles from different layouts.
                _restore_doubao_capture_scroll(page, original_scroll_top)
                page.wait_for_timeout(300)
                expected = _read_doubao_capture_state(
                    page,
                    expected_question=expected_question,
                    scroll_top=original_scroll_top,
                )
    except BaseException as exc:
        capture_error = exc
    finally:
        try:
            _restore_doubao_capture_scroll(page, original_scroll_top)
        except BaseException as exc:
            restore_error = exc

    if capture_error is not None:
        if final_image is not None:
            final_image.close()
        if restore_error is not None:
            raise _DoubaoScopedCaptureError(
                f"Doubao scoped screenshot failed and scroll restore also failed: {restore_error}"
            ) from capture_error
        raise capture_error
    if restore_error is not None:
        if final_image is not None:
            final_image.close()
        raise restore_error
    if final_image is None:
        raise _DoubaoScopedCaptureError("Doubao scoped screenshot produced no image")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        final_image.save(out_path, format="PNG")
    finally:
        final_image.close()
    return {
        "method": "doubao_scoped_message_tiles",
        "tile_count": tile_count,
        "block_count": 2,
        "restored_scroll_top": original_scroll_top,
        "layout_attempts": layout_attempts,
    }


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
        "retrieval_events": _retrieval_events_from_assembled(assembled),
    }


def _retrieval_events_from_assembled(assembled: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract every Doubao search-result occurrence before trace truncation.

    Doubao currently exposes search-result blocks but no separate, reliable
    TOOL_OPEN stage.  V and final-reference stages therefore remain unobserved;
    legacy ``references`` projections are not promoted into either stage.
    """

    events: list[dict[str, Any]] = []
    for block in assembled.get("content_block") or []:
        if not isinstance(block, dict) or block.get("block_type") != 10025:
            continue
        content = block.get("content")
        search = content.get("search_query_result_block") if isinstance(content, dict) else None
        if not isinstance(search, dict):
            continue
        queries = [
            " ".join(value.split())
            for value in (search.get("queries") or [])
            if isinstance(value, str) and value.strip()
        ]
        candidates: list[dict[str, Any]] = []
        for fallback_rank, result in enumerate(search.get("results") or [], 1):
            if not isinstance(result, dict):
                continue
            card = result.get("text_card")
            if not isinstance(card, dict) or not _is_real_url(card.get("url")):
                continue
            raw_rank = card.get("index", result.get("index"))
            rank = (
                raw_rank
                if isinstance(raw_rank, int) and not isinstance(raw_rank, bool) and raw_rank >= 1
                else fallback_rank
            )
            candidates.append(
                {
                    "url": card["url"],
                    "title": card.get("title"),
                    "summary": card.get("summary"),
                    "u_rank": rank,
                }
            )
        events.append(
            {
                "ordinal": len(events) + 1,
                "queries": queries,
                "u_observation": "observed",
                "v_observation": "unobserved",
                "final_reference_observation": "unobserved",
                "candidates": candidates,
                "opened_pages": [],
                "final_references": [],
                "evidence_relation": "answer_sse_trace",
            }
        )
    return normalize_retrieval_events(events) if events else []


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
    seen_queries: set[str] = set()
    seen_search_blocks: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    seen_thinking_searches: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    duplicate_search_blocks = 0
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
            inner = b.get("content") or {}
            sqr = (inner.get("search_query_result_block") or {}) if isinstance(inner, dict) else {}
            block_queries: list[str] = []
            block_query_keys: set[str] = set()
            for raw_query in sqr.get("queries") or []:
                if not isinstance(raw_query, str):
                    continue
                query = " ".join(raw_query.split())
                query_key = query.casefold()
                if not query or query_key in block_query_keys:
                    continue
                block_query_keys.add(query_key)
                block_queries.append(query)
            block_summary = str(sqr.get("summary") or "")[:_BLOCK_SUMMARY_LIMIT]
            results: list[dict[str, Any]] = []
            result_keys: list[str] = []
            seen_block_results: set[str] = set()
            for res in sqr.get("results") or []:
                if not isinstance(res, dict):
                    continue
                tc = res.get("text_card") or {}
                if not isinstance(tc, dict):
                    continue
                url = tc.get("url")
                if not _is_real_url(url):
                    continue
                result_key = str(url).strip().split("#", 1)[0]
                if result_key in seen_block_results:
                    continue
                seen_block_results.add(result_key)
                result_keys.append(result_key)
                if len(results) < results_per_block:
                    results.append(
                        {
                            "title": tc.get("title"),
                            "url": url,
                            "site": tc.get("sitename"),
                            "rank": tc.get("index", res.get("index")),
                            "summary": str(tc.get("summary") or "")[:_RESULT_SUMMARY_LIMIT],
                        }
                    )
            block_identity = (
                tuple(query.casefold() for query in block_queries),
                tuple(sorted(result_keys)),
            )
            if thinking_id and pid == thinking_id and block_identity not in seen_thinking_searches:
                seen_thinking_searches.add(block_identity)
                thinking_chain.append(
                    {
                        "kind": "search",
                        "block_id": b.get("block_id"),
                        "queries": block_queries,
                        "summary": block_summary,
                        "n_results": len(results),
                    }
                )
            if block_identity in seen_search_blocks:
                duplicate_search_blocks += 1
                continue
            seen_search_blocks.add(block_identity)
            scene_counter += 1
            for query in block_queries:
                query_key = query.casefold()
                if query_key in seen_queries:
                    continue
                seen_queries.add(query_key)
                queries.append({"query": query, "ordinal": len(queries) + 1})
            search_blocks.append(
                {
                    "scene": scene_counter,
                    "queries": block_queries,
                    "summary": block_summary,
                    "results": results,
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
            "search_block_duplicates_dropped": duplicate_search_blocks,
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


def _mode_evidence(
    requested_mode: str,
    *,
    ui_engaged: bool | None,
    sse_trace: dict[str, Any] | None,
) -> dict[str, Any]:
    """请求态 vs 实际态分开记录（旧链纪律：请求 deep_think ≠ 实际启用——旧链
    portal 对「请求了深度思考但证据中未检测到启用」的答案拒记深度态）。

    - ``requested``：任务请求的 mode（normal/deep_think）；
    - ``ui_toggle_engaged``：发送前 picker 后置校验是否确认目标态；已处于目标态
      也记 True，None 仅表示旧版 normal UI 没有可观察 picker；toggle 失败已诚实
      raise，走不到这里；
    - ``sse_deep_think_active``：SSE 流证据（thinking root block_type=10040
      是否出现；None=无 SSE 可判——DOM 兜底/解析失败）；
    - ``actual``：仅当 SSE 证据为正才标 deep_think；证据缺失或为负一律如实
      normal，绝不把请求态当实际态。反向错配（请求 normal 而 SSE 见 thinking
      root）同样如实标 deep_think。
    """
    sse_active = sse_trace.get("deep_think_active") if sse_trace is not None else None
    return {
        "requested": requested_mode,
        "ui_toggle_engaged": ui_engaged,
        "sse_deep_think_active": sse_active,
        "actual": "deep_think" if sse_active is True else "normal",
    }


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
_QUICK_MODE_LABELS = ("快速", "快速模式")

# Picker 状态探针（selector 漂移防护，旧链 T-03）。2026-08-20 北京账号命中新版
# composer：模式 trigger 从短文本「快速/专家」变成「豆包 快速/豆包 专家」，但保留
# 稳定语义属性 data-valid-btn="model-select-action-btn"。优先只读这个 composer-scoped
# trigger，并给结果加内部前缀；旧页面没有该属性时才回退扫描可见短按钮。这样既能
# 识别新版复合标签，也不会把推荐卡片/答案区的「思考」按钮冒充模式状态。
_SCOPED_PICKER_PREFIX = "composer-model:"
_PICKER_STATE_JS = r"""() => {
  const visible = b => b.offsetParent !== null;
  const scoped = [...document.querySelectorAll(
    '[data-valid-btn="model-select-action-btn"]'
  )].filter(visible);
  if (scoped.length) {
    return scoped.map(b => {
      const t = (b.innerText || '').replace(/\s+/g, ' ').trim();
      return t ? `composer-model:${t}` : '';
    }).filter(Boolean);
  }
  const btns = [...document.querySelectorAll('button, [role="button"]')]
    .filter(visible);
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
_QUICK_PICKER_TEXTS = ("快速", "快速模式")


def _picker_state(page: Any) -> list[str]:
    """可见短模式按钮文本（JS 探针）。页面丢失/探针失败返回 []，调用方回退旧选择器。"""
    try:
        hits = page.evaluate(_PICKER_STATE_JS)
    except Exception:
        return []
    if not isinstance(hits, list):
        return []
    return [str(t).strip() for t in hits if str(t).strip()]


def _picker_mode(hits: list[str]) -> str | None:
    """把模式 trigger 文本归一成 ``normal`` / ``deep_think``。

    新版 scoped trigger 允许平台名前缀（实证为「豆包 快速」）；旧版非 scoped
    探针继续只接受精确标签或标签开头。若同屏同时出现快速和专家证据，返回 None
    fail-closed，绝不靠顺序猜模式。
    """
    modes: set[str] = set()
    for raw in hits:
        scoped = raw.startswith(_SCOPED_PICKER_PREFIX)
        text = raw[len(_SCOPED_PICKER_PREFIX) :] if scoped else raw
        text = " ".join(text.split())
        if not text:
            continue
        if scoped:
            quick = any(
                text == label or text.endswith(f" {label}") for label in _QUICK_PICKER_TEXTS
            )
            deep = any(text == label or text.endswith(f" {label}") for label in _DEEP_PICKER_TEXTS)
        else:
            quick = any(text == label or text.startswith(label) for label in _QUICK_PICKER_TEXTS)
            deep = any(text == label or text.startswith(label) for label in _DEEP_PICKER_TEXTS)
        if quick:
            modes.add("normal")
        if deep:
            modes.add("deep_think")
    return next(iter(modes)) if len(modes) == 1 else None


def _mode_picker(page: Any) -> Any | None:
    """composer 的模式 picker 按钮（当前显示 快速 / 专家 / 思考 / …）。可见则返回 Locator。

    当前构建优先用 composer 专属 ``data-valid-btn``；旧版回退选择器仍把
    快速/专家放在思考前，避免 has-text("思考") 命中答案区思考块并误点展开。
    """
    for sel in (
        '[data-valid-btn="model-select-action-btn"][aria-haspopup="menu"]',
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

    主路径是 composer-scoped JS 状态探针；仅当探针不可用/为空时回退定位旧版
    trigger，再把其文本送入同一精确/歧义分类器。两路都 fail-closed。
    """
    hits = _picker_state(page)
    if hits:
        return _picker_mode(hits) == "deep_think"
    # 探针异常时仍只读 _mode_picker 的精确短文本，并经同一歧义判定；不再用任意
    # button:has-text("思考") 的存在性直接判真，避免答案区思考块 false-positive。
    picker = _mode_picker(page)
    if picker is None:
        return False
    try:
        return _picker_mode([picker.inner_text(timeout=400)]) == "deep_think"
    except Exception:
        return False


def _quick_mode_engaged(page: Any) -> bool:
    """后置校验：composer picker 当前显示「快速」，且没有仍显示专家/深思态。"""
    hits = _picker_state(page)
    if hits:
        return _picker_mode(hits) == "normal"
    picker = _mode_picker(page)
    if picker is None:
        return False
    try:
        return _picker_mode([picker.inner_text(timeout=400)]) == "normal"
    except Exception:
        return False


def _mode_stably_engaged(page: Any, engaged: Callable[[Any], bool]) -> bool:
    """连续两拍确认目标模式；任一拍或拍间等待异常都 fail-closed。

    豆包曾出现 picker 标签先乐观翻转、随后回退的 T-03 行为。所有成功出口
    （已经处于目标态、菜单点击、native fallback、函数末尾兜底）必须共用本门，
    避免某个尾部分支退化成单拍放行。
    """
    try:
        if not engaged(page):
            return False
        page.wait_for_timeout(400)
        return engaged(page)
    except Exception:
        return False


def _try_enable_quick_mode(page: Any, rng: random.Random) -> bool:
    """把 composer picker 显式切到「快速」，以双拍后置状态为成功判据。

    豆包会跨新会话保留上一次的专家态；normal 任务若不切换会被错误标注。与专家
    toggle 一样，坐标点击被 Radix 吞掉时才使用 Playwright 原生指针点击兜底。
    """

    def _pace(lo: float, hi: float) -> float:
        return human_pause(rng, lo, hi, sleep=lambda s: page.wait_for_timeout(int(s * 1000)))

    if _mode_stably_engaged(page, _quick_mode_engaged):
        return True

    picker = _mode_picker(page)
    if picker is None:
        return False

    try:
        menu_open = False
        for _attempt in range(2):
            human_click(picker, page, rng, hover_s=_PACE_PICKER_HOVER_S)
            try:
                page.get_by_role("menuitem", name=_QUICK_MODE_LABELS[0], exact=True).first.wait_for(
                    state="visible", timeout=5_000
                )
                menu_open = True
                break
            except Exception:
                try:
                    page.get_by_text(_QUICK_MODE_LABELS[0], exact=True).first.wait_for(
                        state="visible", timeout=1_000
                    )
                    menu_open = True
                    break
                except Exception:
                    pass
                try:
                    picker.click(timeout=2_000)
                    page.get_by_role(
                        "menuitem", name=_QUICK_MODE_LABELS[0], exact=True
                    ).first.wait_for(state="visible", timeout=5_000)
                    menu_open = True
                    break
                except Exception:
                    try:
                        page.get_by_text(_QUICK_MODE_LABELS[0], exact=True).first.wait_for(
                            state="visible", timeout=1_000
                        )
                        menu_open = True
                        break
                    except Exception:
                        continue

        _pace(*_PACE_MENU_READ_S)
        candidates = []
        if menu_open:
            for label in _QUICK_MODE_LABELS:
                candidates.append(page.get_by_role("menuitem", name=label, exact=True).first)
                candidates.append(page.get_by_text(label, exact=True).first)
                candidates.append(page.get_by_role("button", name=label, exact=True).first)
        for option in candidates:
            try:
                if option.count() == 0 or not option.is_visible(timeout=400):
                    continue
                human_click(option, page, rng)
                page.wait_for_timeout(400)
                if _mode_stably_engaged(page, _quick_mode_engaged):
                    return True
                if option.is_visible(timeout=300):
                    option.click(timeout=2_000)
                    page.wait_for_timeout(400)
                    if _mode_stably_engaged(page, _quick_mode_engaged):
                        return True
            except Exception:
                continue
    except Exception:
        pass
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
    except Exception:
        pass
    return _mode_stably_engaged(page, _quick_mode_engaged)


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

    if _mode_stably_engaged(page, _deep_think_engaged):
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
                    page.get_by_role(
                        "menuitem", name=_DEEP_MODE_LABELS[0], exact=True
                    ).first.wait_for(state="visible", timeout=5_000)
                    menu_open = True
                    break
                except Exception:
                    # Older builds expose the option without menuitem semantics.
                    try:
                        page.get_by_text(_DEEP_MODE_LABELS[0], exact=True).first.wait_for(
                            state="visible", timeout=1_000
                        )
                        menu_open = True
                        break
                    except Exception:
                        pass
                    # 20260814 live calibration: on the current Radix picker the
                    # Bezier mouse click can occasionally leave the trigger closed
                    # even though its bounding box is valid.  Fall back to
                    # Playwright's native pointer click only after that observable
                    # failure; it still emits real mouse events and avoids turning a
                    # transient click miss into deep_think_toggle_failed.
                    try:
                        picker.click(timeout=2_000)
                        page.get_by_role(
                            "menuitem", name=_DEEP_MODE_LABELS[0], exact=True
                        ).first.wait_for(state="visible", timeout=5_000)
                        menu_open = True
                        break
                    except Exception:
                        try:
                            page.get_by_text(_DEEP_MODE_LABELS[0], exact=True).first.wait_for(
                                state="visible", timeout=1_000
                            )
                            menu_open = True
                            break
                        except Exception:
                            continue
            _pace(*_PACE_MENU_READ_S)  # 读菜单
            candidates = []
            if menu_open:
                for label in _DEEP_MODE_LABELS:
                    # Prefer the semantic Radix menuitem.  A generic exact-text
                    # locator can resolve to an inner wrapper whose mouse click does
                    # not activate the menu item in the 20260814 build.
                    candidates.append(page.get_by_role("menuitem", name=label, exact=True).first)
                    candidates.append(page.get_by_text(label, exact=True).first)
                    candidates.append(page.get_by_role("button", name=label).first)
                for sub in _DEEP_MODE_SUBTITLES:
                    candidates.append(page.get_by_text(sub).first)
            for opt in candidates:
                try:
                    if opt.count() > 0 and opt.is_visible(timeout=400):
                        human_click(opt, page, rng)
                        page.wait_for_timeout(400)
                        if _mode_stably_engaged(page, _deep_think_engaged):
                            return True
                        # Same calibrated fallback for a visible menu item whose
                        # coordinate click was swallowed.  Never use it after the
                        # state changed, so a successful human click is not doubled.
                        if opt.is_visible(timeout=300):
                            opt.click(timeout=2_000)
                            page.wait_for_timeout(400)
                            if _mode_stably_engaged(page, _deep_think_engaged):
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
                if _mode_stably_engaged(page, _deep_think_engaged):
                    return True
        except Exception:
            continue
    return _mode_stably_engaged(page, _deep_think_engaged)
