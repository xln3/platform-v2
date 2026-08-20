"""DeepSeek 网页采集适配器（chat.deepseek.com）。

结构严格镜像 ``doubao_adapter.py``（已 live 验证的同款契约）。DeepSeek 平台知识
移植自旧链（``server/proxyllm/engines/deepseek.py``、``server/geosys/collector_deepseek.py``），
关键面 2026-07-27 已 live 校准：输入框 placeholder / 回答气泡
``div.ds-markdown.ds-assistant-message-main-content`` / SSE JSON-patch 增量流 schema
（见 ``_assemble_deepseek_record`` 碎片状态机——含 answer_len=1 根因记录与
20260810 deep_think+智能搜索实测流校准）；登录墙 /sign_in 跳转为旧链
CONFIRMED 信号。仍未校准项在各常量行内标注（发送按钮锚点、「新对话」入口、
尾部噪声词表等）。

DeepSeek 特性：``/api/v0/chat/completion`` 每请求带 WASM PoW（``x-ds-pow-*``），
真实浏览器管线自动解题——故坚持 DOM/浏览器路径，绝不直连 API、绝不重实现 wasm。

mode 支持（20260810 起，用户拍板测量口径。deepseek 特殊性：专家模式不支持
搜索，快速模式同时支持深度思考与搜索——故 GEO 评测不测专家模式，只测快速
模式的两种组合）：

- ``mode='normal'``：快速模式 tab + 智能搜索 chip **开** + 深度思考 chip **关**。
- ``mode='deep_think'``：快速模式 tab + 智能搜索 chip **开** + 深度思考 chip **开**。
- 两种 mode 都在发送前显式确保（20260810 live 校准：chip=
  ``div.ds-toggle-button`` + ``aria-pressed`` 状态位，语义属性跨构建稳定；模式
  tab 条选中态用结构差分判定——hash class 跨构建漂移，绝不硬编码）；幂等
  （状态已到位零点击）。全部后置校验确认不了 → ``mode_toggle_failed``
  non_retryable，绝不静默按错误口径采集。
- 其他 mode → ``ApplicationError(..., type="unsupported_mode", non_retryable=True)``。
- 正文纯净：SSE fragments 只收 ``type=="RESPONSE"``（THINK/TOOL_SEARCH/
  TOOL_OPEN 痕迹碎片不进正文）；DOM 兜底经 ``_REASONING_TRACE_MARKERS``
  剥离推理/搜索痕迹行；``[reference:N]`` 渲染层锚点剔除，引用卡片进
  references（results/result/references 三载体形状判形）。
- 配置全走 env（秘密绝不进 task payload）：
  ``GEO_DEEPSEEK_PROFILE_DIR``（必填，persistent profile 目录；缺失/不存在 →
  ``adapter_not_configured`` non_retryable）；``GEO_DEEPSEEK_PROXY_URL``（可选，
  形如 http://user:pass@host:port——日志只出现打码后的 scheme://host:port）；
  ``GEO_ADAPTER_EVIDENCE_DIR``（五平台共享截图目录 env，缺省
  ``platform-v2/runtime/adapter-evidence/deepseek/``，自动建目录）；
  ``GEO_DEEPSEEK_HEADLESS``（默认 1 headless；0=headed 需 DISPLAY）；
  ``GEO_DEEPSEEK_CDP_URL``（可选，常驻浏览器 attach，见下）。
- 执行模型：sync 浏览器驱动包在 ``asyncio.to_thread`` 里跑（sync PW 绝不能进事件
  循环——旧系统 greenlet 坑）。协程侧每 10s 泵一次 heartbeat（workflow
  heartbeat_timeout=30s）。
- 浏览器驱动首选 patchright（旧链生产同款反检测补丁版）；vanilla playwright 仅兜底。
- 墙分类（先截屏存证再抛，错误 message 带证据路径、绝不含秘密）：
  登录墙（未登录访问 ``/`` 自动跳 ``/sign_in``，旧链 CONFIRMED 信号）/实名墙 →
  ``wall_login_required`` non_retryable；验证码 → ``wall_captcha`` non_retryable；
  发送墙/限流 → ``wall_send`` non_retryable（重试只是再撞）。
  2026-08-14 起（墙词表 ``wall_lexicon``，对齐豆包）：答案文本级配额/禁言/
  拒答 → ``wall_quota``/``wall_muted``/``wall_refusal`` non_retryable；batch
  连坐按 wall_type 细化（muted 全连坐、quota 只连坐同 mode、refusal 不
  连坐，见 ``collect_batch`` docstring）。
- 成功判据（零合成）：提交被接受（输入框清空）且 completion 流真正 loadingFinished
  且解析出非空正文且不含墙特征——缺一都不得返回成功。流截断/空答案/无流 →
  ``answer_capture_incomplete``（可重试的诚实失败）。官方公开分享 URL 与分享页
  图像也同属成功门：DeepSeek 当前无原生“下载分享图”，故图像取自新建的官方
  ``/share/...`` 公共页面；绝不以登录态运行窗口截图替代，任一缺失即 incomplete。

拟人化口径（2026-08-06 起，与豆包同构。背景：自动化交互序列本身即行为指纹——
零停顿直点、insert_text 注入、秒发都会被风控稳定识别）：

- 输入：composer 正文一律 ``human_like.human_type`` 逐字真实键盘事件
  （40-140ms 抖动 + 标点/空格后 15% 概率 250-800ms 停顿），绝不 insert_text/fill。
- 点击：所有业务点击（输入框聚焦、发送按钮兜底、弹层清理、「新对话」）一律
  ``human_like.human_click``——贝塞尔移动 + 到位悬停 + 元素内随机偏移点击。
  发送主路径保留 live 校准的 Enter 键盘提交（真实键盘事件，非指纹面）。
- 节奏：页面就绪 → 端详 0.6-1.8s → 点输入框 → 逐字输入 → 通读 0.5-1.5s → 发送。
- 机器路径不动：CDP/SSE 捕获、提交确认轮询、墙识别、截图等纯观测逻辑不产生
  输入事件，不构成行为指纹，保持原样。

run 级会话复用 + CDP 常驻 attach（2026-08-06 起，``collect_deepseek_batch``，
与豆包 ``collect_doubao_batch`` 同构）：

- 结构：``_browser_session`` 经 ``resident_browser.platform_browser``
  attach-or-launch——``GEO_DEEPSEEK_CDP_URL`` 非空 → ``connect_over_cdp``
  attach 常驻浏览器（退出只断开 CDP：不关 context、不清理 profile——
  profile/登录态归 supervisor）；否则回退 ``launch_persistent_context``
  （每次全新、结束由契约层 finally close）。导航 + 登录墙检查两条路径都做。
- 优雅关闭（launch 路径，profile 崩溃标记根治）：启动前与 close 后各幂等执行
  一次 ``_clean_profile_crash_state``（复用 doubao_adapter 同款实现，单一出处）。
- 一个 run 的 deepseek 任务在同一个浏览器会话/同一标签页里顺序完成（绝不每题
  冷启全新 Chromium——「冷启动即发问+短时间再次冷启动」的会话结构是风控
  指纹，真人是在同一浏览器窗口里连续聊天的）。每题：fresh_chat 纪律（点
  「新对话」+ composer 空验证 + 消息节点计数探针，按钮缺失导航回聊天首页
  兜底，最终验证不过 _IncompleteCapture 诚实失败，绝不静默沿用旧会话）→
  拟人输入/发送 → SSE 捕获/组装/证据落盘（与 per-task 共用 ``_collect_one``
  主体，绝无两套复制）→ 「阅读停顿」（human_like.human_read_pause：滚动
  2-5 次 + 停留 8-25s 抖动，含最后一题）→ 下一题。
- 失败语义（与豆包逐字对齐）：题级墙/incomplete → 该题诚实记失败、后续题
  aborted（aborted_after_failure，零浏览器交互——真人撞墙后会停下，不编造
  不硬闯）；结果列表与输入等长同序返回，绝不 raise 丢掉已完成题。session
  建立阶段（launch/navigate/登录墙）异常=一题未发：wall 类成全题 wall 结果，
  临时故障（_IncompleteCapture）raise 走 batch 级重试。仅配置类错误
  （adapter_not_configured/unsupported_mode）允许 raise non_retryable。
- 注册：``collect_deepseek_batch`` activity 在本文件自带 ``@activity.defn``；
  per-task ``run_deepseek_collection`` 仍由 platform_registry dispatcher 调用
  （本文件不自带 per-task activity 包装）。worker 接线（workers/main.py）由
  协调者统一做。activity 实现不显式传 session_factory——缺省 None 才走
  to_thread 分支跑真实 sync 浏览器（显式传真实类会在事件循环里崩，豆包
  2026-08-06 生产事故教训）。
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
import socket
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import structlog
from temporalio import activity
from temporalio.exceptions import ApplicationError

from workflows.activities.answer_dom_anchor import capture_answer_evidence
from workflows.activities.browser_driver import load_sync_browser_driver
from workflows.activities.browser_router import resolve_batch_instance
from workflows.activities.collection import (
    CollectionBatchInput,
    CollectionBatchItemResult,
    CollectionBatchResult,
    CollectionEvidenceRef,
    CollectionTaskInput,
    CollectionTaskResult,
    batch_result_with_captcha_pause,
)
from workflows.activities.doubao_adapter import (
    _clean_profile_crash_state,
    _wall_verdict_message,
)
from workflows.activities.human_like import (
    human_click,
    human_pause,
    human_read_pause,
    human_type,
)
from workflows.activities.official_share import (
    OfficialShareExportError,
    capture_deepseek_official_share,
    probe_official_share_url,
    write_share_link_manifest,
)
from workflows.activities.page_capture import capture_scoped_chat_tiles
from workflows.activities.raw_capture import dump_raw_evidence_refs, maybe_raw_capture
from workflows.activities.resident_browser import platform_browser
from workflows.activities.wall_lexicon import classify_answer_text, detect_muted_banner

log = structlog.get_logger()

ENV_PROFILE_DIR = "GEO_DEEPSEEK_PROFILE_DIR"
ENV_PROXY_URL = "GEO_DEEPSEEK_PROXY_URL"
ENV_EVIDENCE_DIR = "GEO_ADAPTER_EVIDENCE_DIR"  # 五平台共享 env；缺省落 deepseek 子目录
ENV_HEADLESS = "GEO_DEEPSEEK_HEADLESS"
ENV_CDP_URL = "GEO_DEEPSEEK_CDP_URL"  # 常驻浏览器 CDP attach（空=回退 launch；契约层读取）
ENV_OPENED_SOURCE_PREVIEW_LIMIT = "GEO_DEEPSEEK_OPENED_SOURCE_PREVIEW_LIMIT"

_DEFAULT_EVIDENCE_DIR = (
    Path(__file__).resolve().parents[2] / "runtime" / "adapter-evidence" / "deepseek"
)
_HEARTBEAT_INTERVAL_S = 10.0  # workflow heartbeat_timeout=30s，泵频 ≤15s 硬约束
_NAV_TIMEOUT_MS = 25_000
_CHAT_TIMEOUT_S = 120.0  # normal 模式流式完成预算（workflow 总预算 5 分钟）
# deep_think（深度思考+智能搜索）流远长于 normal——对齐豆包 600s；workflow
# 缺省总预算 15min（activity_timeout_minutes）放得下。
_CHAT_TIMEOUT_DEEP_THINK_S = 600.0
_DEFAULT_OPENED_SOURCE_PREVIEW_LIMIT = 10
_MAX_OPENED_SOURCE_PREVIEW_LIMIT = 20

_CHAT_URL = "https://chat.deepseek.com/"
_SIGN_IN_PATH = "/sign_in"  # 未登录访问 / 自动跳 /sign_in（旧链 CONFIRMED 信号）
_PLATFORM = "deepseek"  # resident_browser 平台互斥锁/GEO_DEEPSEEK_CDP_URL 的 slug

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

# 「新对话」入口（batch fresh-chat 纪律）。
# 2026-08-07 headed live 校准（CDP attach deepseek-tj 常驻浏览器，旧会话计数 2→0 实证）：
# 侧边栏顶部新建入口 = <div class="_5a8ac7a">「开启新对话」（div 非 button、无
# aria-label；class 为构建 hash 不稳定，文本才是锚点）——Playwright text 精确匹配
# 命中即点。其余为结构候选兜底；全部缺失时调用方导航回聊天首页兜底（DeepSeek /
# 默认即全新会话）。
_NEW_CHAT_SELECTORS: tuple[str, ...] = (
    'text="开启新对话"',
    "text=开启新对话",
    '[aria-label*="新对话"]',
    'button:has-text("新对话")',
    '[role="button"]:has-text("新对话")',
    'a:has-text("新对话")',
)

# 新会话验证：页面已存在消息节点计数（>0 = 旧会话/进行中的旧回答）。
# div[class*="ds-markdown"] 是 2026-07-27 live 校准的助手气泡选择器（权威信号）；
# 其余为保守补充（匹配不到=0，无害）。
_CHAT_MESSAGE_COUNT_JS = r"""() => {
  const sels = [
    'div[class*="ds-markdown"]',
    '[class*="message"][class*="assistant"]'
  ];
  let n = 0;
  for (const s of sels) n += document.querySelectorAll(s).length;
  return n;
}"""

# 拟人化节奏区间（秒）——端详页面 / 发送前通读 / 新会话切换
_PACE_PAGE_READY_S = (0.6, 1.8)
_PACE_BEFORE_SEND_S = (0.5, 1.5)
_PACE_AFTER_NEW_CHAT_S = (0.6, 1.2)

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

# Runtime answer evidence must contain only the current question and assistant
# message.  The previous whole-page flattener changed every transform/fixed node,
# which put the composer between answer fragments and duplicated virtual-list UI.
# This probe only scrolls the one validated DeepSeek chat pane.  Its capture band
# stops above the sticky composer; repeated tiles skip the 34px sticky thinking
# header after recording it once at its natural position.
_DEEPSEEK_CAPTURE_STATE_JS = r"""async (request) => {
  const fail = (error) => ({ok: false, error});
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0
      && cs.display !== 'none' && cs.visibility !== 'hidden';
  };
  const normalizeText = (value) => String(value || '')
    .replace(/\s+/g, ' ')
    .replace(/\s*([“”‘’「」『』])\s*/g, '$1')
    .trim();
  const scrollers = Array.from(document.querySelectorAll('.ds-virtual-list'))
    .filter((el) => {
      const cs = getComputedStyle(el);
      return visible(el) && (cs.overflowY === 'auto' || cs.overflowY === 'scroll')
        && el.scrollHeight > el.clientHeight + 50;
    });
  if (scrollers.length !== 1) return fail(`chat_scroller_count:${scrollers.length}`);
  const scroller = scrollers[0];
  if (request && Number.isFinite(request.scrollTop)) {
    scroller.scrollTop = Number(request.scrollTop);
    await new Promise((resolve) => requestAnimationFrame(
      () => requestAnimationFrame(resolve)
    ));
  }
  const items = scroller.querySelector('.ds-virtual-list-items');
  if (!items) return fail('virtual_items_missing');
  const messages = Array.from(items.querySelectorAll('.ds-message'))
    .filter((el) => el.closest('.ds-virtual-list') === scroller);
  if (messages.length !== 2) return fail(`message_count:${messages.length}`);
  const question = messages[0];
  const answer = messages[1];
  const expectedQuestion = normalizeText(request && request.expectedQuestion);
  if (!expectedQuestion) return fail('expected_question_missing');
  if (normalizeText(question.innerText) !== expectedQuestion) {
    return fail('question_text_mismatch');
  }
  const markdown = answer.querySelectorAll('.ds-markdown.ds-assistant-message-main-content');
  if (markdown.length !== 1 || !normalizeText(markdown[0].innerText)) {
    return fail(`assistant_markdown_count:${markdown.length}`);
  }
  if ([question, answer].some((node) => node.querySelector(
    'textarea, input, [contenteditable="true"]'
  ))) {
    return fail('composer_inside_message');
  }
  const textarea = scroller.querySelector(
    'textarea#chat-input, textarea[placeholder*="DeepSeek"], textarea[placeholder*="发消息"]'
  );
  if (!textarea || !visible(textarea)) return fail('composer_missing');
  let composer = textarea;
  while (composer && composer !== scroller
      && getComputedStyle(composer).position !== 'sticky') {
    composer = composer.parentElement;
  }
  if (!composer || composer === scroller) return fail('sticky_composer_missing');

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
  const composerRect = composer.getBoundingClientRect();
  const safeBottom = Math.min(scrollerRect.bottom, composerRect.top - 8);
  const captureHeight = safeBottom - scrollerRect.top;
  if (captureHeight < 200) return fail('capture_band_occluded');
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
  const captureX = Math.min(...blocks.map((block) => block.left));
  const captureRight = Math.max(...blocks.map((block) => block.right));
  const captureWidth = captureRight - captureX;
  const maxScroll = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
  if (captureX < 0 || captureRight > window.innerWidth + 1 || captureWidth <= 0) {
    return fail('message_outside_viewport');
  }
  if (blocks.some((block) => block.top < -1
      || block.bottom > scroller.scrollHeight + 1
      || block.bottom <= block.top)) {
    return fail('message_outside_scroll_extent');
  }
  return {
    ok: true,
    scroll_top: scrollTop,
    scroll_height: scroller.scrollHeight,
    max_scroll: maxScroll,
    capture_x: captureX,
    capture_y: scrollerRect.top,
    capture_width: captureWidth,
    capture_height: captureHeight,
    blocks,
  };
}"""


_DEEPSEEK_CAPTURE_RESTORE_JS = r"""async (scrollTop) => {
  const scrollers = Array.from(document.querySelectorAll('.ds-virtual-list'))
    .filter((el) => el.scrollHeight > el.clientHeight + 50);
  if (scrollers.length !== 1) {
    return {ok: false, error: `restore_scroller_count:${scrollers.length}`};
  }
  const scroller = scrollers[0];
  scroller.scrollTop = Number(scrollTop);
  await new Promise((resolve) => requestAnimationFrame(
    () => requestAnimationFrame(resolve)
  ));
  return {
    ok: Math.abs(scroller.scrollTop - Number(scrollTop)) <= 1,
    actual_scroll_top: scroller.scrollTop,
  };
}"""


# ---------------------------------------------------------------------------
# 配置 / 错误类型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeepseekAdapterConfig:
    """env 配置。proxy_url 原文只在启动浏览器时使用，绝不落日志/payload。

    ``browser_key``（2026-08-09 起，浏览器矩阵化）：attach/互斥锁/fence 用的
    opaque "platform"——batch 路径由 browser_router 解析为常驻实例键
    （``deepseek_tj`` 等）；缺省平台 slug（per-task 老路径/测试行为不变）。"""

    profile_dir: Path
    proxy_url: str | None
    evidence_dir: Path
    headless: bool
    browser_key: str = _PLATFORM

    @classmethod
    def from_env(cls, *, proxy_url_override: str | None = None) -> DeepseekAdapterConfig:
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
        # 失败题原始流量证据（2026-08-10 起）：_collect_one 题末挂 raw/HAR ref，
        # 经 _failure_outcome → 失败 result.evidence 进 CAS。缺省空。
        self.evidence_refs: list[CollectionEvidenceRef] = []


class _IncompleteCapture(RuntimeError):
    """采集未完成的诚实失败（可重试）：流截断 / 空答案 / 无流等。"""

    def __init__(self, message: str, evidence_path: Path | None = None) -> None:
        super().__init__(message)
        self.evidence_path = evidence_path
        self.evidence_refs: list[CollectionEvidenceRef] = []


class _ModeToggleFailed(RuntimeError):
    """模式开关无法确认到位（快速模式 tab / 深度思考 / 智能搜索；non_retryable；
    绝不静默按错误口径采集）。"""

    def __init__(self, message: str, evidence_path: Path | None = None) -> None:
        super().__init__(message)
        self.evidence_path = evidence_path
        self.evidence_refs: list[CollectionEvidenceRef] = []


class _ModeUnconfirmed(RuntimeError):
    """deep_think 请求已下达（chip 后置校验通过）但 SSE 无 thinking 碎片证据
    ——诚实失败（non_retryable）：绝不把无思考证据的答案按 deep_think 落
    completed（对照豆包 2026-08-14 口径：配额耗尽后平台静默回退非思考模式的
    「正常答案」曾是 2026-08-13 事故源头之一）。"""

    def __init__(self, message: str, evidence_path: Path | None = None) -> None:
        super().__init__(message)
        self.evidence_path = evidence_path
        self.evidence_refs: list[CollectionEvidenceRef] = []


@dataclass
class CollectedAnswer:
    answer_text: str
    references: list[dict[str, Any]]
    screenshot_path: Path
    meta: dict[str, Any] = field(default_factory=dict)
    # SSE 结构化 trace 证据路径（kind="sse"，含思考链/检索词；解析失败=None 诚实缺省）
    trace_path: Path | None = None
    # 平台真实检索词（W1）：[{"query": ..., "ordinal": ...}]；无检索词为空列表。
    search_queries: list[dict[str, Any]] = field(default_factory=list)
    # 原始流量证据 ref（2026-08-10 起：sse_raw/har；GEO_RAW_CAPTURE=0 或写盘
    # 失败为空——诚实缺省）。_task_result_from_collected 并入 evidence。
    raw_evidence: list[CollectionEvidenceRef] = field(default_factory=list)
    # DeepSeek TOOL_OPEN 事件驱动的事后重开页面概览。它只证明平台流明确报告
    # “打开了该 URL”，以及采集时该 URL 可被复现；不冒充 AI 会话内像素证据，
    # 也绝不作为品牌提及证据。
    opened_source_previews: list[CollectionEvidenceRef] = field(default_factory=list)
    # Runtime answer screenshot + official public-share page image/link. The
    # official share image is selected as screenshot_ref for product display.
    evidence: list[CollectionEvidenceRef] = field(default_factory=list)
    answer_evidence: CollectionEvidenceRef | None = None


@dataclass(frozen=True)
class DeepseekBatchItemSpec:
    """batch 内单题输入（session 层）：查询/mode + 证据文件名片段。"""

    business_key: str
    query: str
    mode: str
    file_stem: str


@dataclass
class DeepseekBatchItemOutcome:
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


class _BrowserSession(Protocol):
    """Playwright 交互隔离面：测试注入 fake，绝不启动真浏览器。"""

    def collect(
        self, query: str, on_stage: Callable[[str], None], *, mode: str = "normal"
    ) -> CollectedAnswer: ...

    def collect_batch(
        self, items: list[DeepseekBatchItemSpec], on_stage: Callable[[str], None]
    ) -> list[DeepseekBatchItemOutcome]: ...


SessionFactory = Callable[[DeepseekAdapterConfig, Path, str], _BrowserSession]


def _default_heartbeat() -> Callable[[dict[str, Any]], None]:
    """activity 上下文内用真 heartbeat；脱离上下文（live 冒烟脚本）退化为 no-op。"""
    try:
        activity.info()
    except RuntimeError:
        return lambda payload: None
    return activity.heartbeat


# ---------------------------------------------------------------------------
# batch activity 入口与异步泵
# ---------------------------------------------------------------------------


@activity.defn(name="collect_deepseek_batch")
async def collect_deepseek_batch(batch: CollectionBatchInput) -> CollectionBatchResult:
    """DeepSeek batch 采集注册实现（workers/main.py 接线由协调者统一做）。

    整个 batch 在同一个浏览器会话里顺序完成（run 级会话复用）；墙/失败诚实
    记录在 per-item 结果里（本 activity 不因墙类失败 raise），仅配置类错误
    （adapter_not_configured/unsupported_mode）raise。
    """
    try:
        attempt = activity.info().attempt
    except RuntimeError:
        attempt = 1
    # 不传 session_factory：缺省 None 才走 to_thread 分支跑真实 sync 浏览器；
    # 显式传 _PlaywrightDeepseekSession 会误判为注入 fake，在事件循环里直跑
    # sync API（豆包 2026-08-06 batch 首航生产事故同款教训）。
    return await run_deepseek_batch(
        batch,
        heartbeat=activity.heartbeat,
        attempt=attempt,
    )


async def run_deepseek_batch(
    batch: CollectionBatchInput,
    *,
    session_factory: SessionFactory | None = None,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
    proxy_url_override: str | None = None,
    attempt: int = 1,
) -> CollectionBatchResult:
    """batch activity 核心：配置门 → mode 门 → to_thread 跑共享浏览器会话 →
    per-item outcome 映射。与 activity 上下文解耦（heartbeat/attempt 注入）。

    失败语义（与豆包 batch 逐字对齐）：题级墙/incomplete 由 session 转 outcome
    （后续题 aborted），本函数不 raise；session 级 _WallError（导航/登录墙，
    一题未发）成全题 wall 结果（non_retryable 语义，重试只是再撞）；session
    级 _IncompleteCapture（浏览器启动失败等临时故障，一题未发）raise 可重试
    ApplicationError——结果全空时重试无已完成题损失。配置类错误一律 raise。
    """
    uses_default_session = session_factory is None
    if session_factory is None:
        session_factory = _PlaywrightDeepseekSession
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
    # 无实例/地域不符/清单畸形一律 fail-closed。空 batch 不解析（旧契约不变）。
    route = resolve_batch_instance(batch.items)
    instance_key = route.instance_key if route is not None else None
    config = DeepseekAdapterConfig.from_env(proxy_url_override=proxy_url_override)
    if route is not None:
        config = replace(config, browser_key=route.instance_key)
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    batch_stem = f"batch-{_safe_stem(batch.run_pub_id)}-a{attempt}"
    specs = [
        DeepseekBatchItemSpec(
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
    if not specs:
        # 空 batch → 空结果，零浏览器交互（连 session 都不建）。
        return CollectionBatchResult(results=[])
    progress: dict[str, Any] = {"stage": "browser_launch", "item": None}

    def _blocking() -> list[DeepseekBatchItemOutcome]:
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
        bound.info("deepseek_batch_session_wall", wall_type=wall.wall_type, stage=progress["stage"])
        return CollectionBatchResult(
            results=[
                _failure_batch_item(
                    item,
                    status="wall",
                    error_type=wall.wall_type,
                    error_message=f"{wall}{evidence_suffix}",
                    evidence_path=wall.evidence_path,
                    evidence=wall.evidence_refs,
                )
                for item in batch.items
            ]
        )
    except _ModeUnconfirmed as mu:
        # 防御：mode_unconfirmed 应在题内转 outcome；逃出即按 session 级诚实记录。
        evidence_suffix = f"; evidence={mu.evidence_path}" if mu.evidence_path else ""
        bound.info("deepseek_batch_session_mode_unconfirmed", stage=progress["stage"])
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
    except _ModeToggleFailed as toggle:
        # 防御：toggle 失败应在题内转 outcome；逃出即按 session 级 wall 诚实记录。
        evidence_suffix = f"; evidence={toggle.evidence_path}" if toggle.evidence_path else ""
        bound.info("deepseek_batch_session_toggle_failed", stage=progress["stage"])
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
    except _IncompleteCapture as inc:
        # session 级临时故障（浏览器启动失败等）：一题未发，raise 走 batch 重试。
        evidence_suffix = f"; evidence={inc.evidence_path}" if inc.evidence_path else ""
        bound.info("deepseek_batch_session_incomplete", reason=str(inc), stage=progress["stage"])
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
        "deepseek_batch_done",
        ok=sum(1 for r in results if r.status == "ok"),
        failed=sum(1 for r in results if r.status != "ok"),
        stage=progress["stage"],
    )
    return batch_result_with_captcha_pause(results, instance_key=instance_key)


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
    item: CollectionTaskInput, outcome: DeepseekBatchItemOutcome
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
        evidence=outcome.evidence,
    )


# ---------------------------------------------------------------------------
# per-task activity 核心（platform_registry dispatcher 调用入口）
# ---------------------------------------------------------------------------


async def run_deepseek_collection(
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
    只传 ``(item, heartbeat=..., proxy_url_override=...)``，与本签名对齐。
    """
    if item.mode not in ("normal", "deep_think"):
        raise ApplicationError(
            f"unsupported mode: {item.mode!r} (expected 'normal' or 'deep_think')",
            type="unsupported_mode",
            non_retryable=True,
        )
    uses_default_session = session_factory is None
    if session_factory is None:
        session_factory = _PlaywrightDeepseekSession
    if heartbeat is None:
        heartbeat = _default_heartbeat()
    config = DeepseekAdapterConfig.from_env(proxy_url_override=proxy_url_override)
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
        evidence = f"; evidence={wall.evidence_path}" if wall.evidence_path else ""
        bound.info("deepseek_wall", wall_type=wall.wall_type, stage=progress["stage"])
        raise ApplicationError(
            f"{wall}{evidence}", type=wall.wall_type, non_retryable=True
        ) from wall
    except _ModeToggleFailed as toggle:
        evidence = f"; evidence={toggle.evidence_path}" if toggle.evidence_path else ""
        bound.info("deepseek_mode_toggle_failed", stage=progress["stage"])
        raise ApplicationError(
            f"{toggle}{evidence}", type="mode_toggle_failed", non_retryable=True
        ) from toggle
    except _ModeUnconfirmed as mu:
        # deep_think 无 SSE 思考证据（2026-08-14 起）：non_retryable 诚实失败，
        # 绝不把无思考证据的答案按 deep_think 落 completed。
        evidence = f"; evidence={mu.evidence_path}" if mu.evidence_path else ""
        bound.info("deepseek_mode_unconfirmed", stage=progress["stage"])
        raise ApplicationError(
            f"{mu}{evidence}", type="mode_unconfirmed", non_retryable=True
        ) from mu
    except _IncompleteCapture as inc:
        evidence = f"; evidence={inc.evidence_path}" if inc.evidence_path else ""
        bound.info("deepseek_capture_incomplete", reason=str(inc), stage=progress["stage"])
        raise ApplicationError(f"{inc}{evidence}", type="answer_capture_incomplete") from inc
    bound.info(
        "deepseek_collect_ok",
        answer_len=len(collected.answer_text),
        references=len(collected.references),
        stage=progress["stage"],
    )
    return _task_result_from_collected(item, collected)


def _task_result_from_collected(
    item: CollectionTaskInput, collected: CollectedAnswer
) -> CollectionTaskResult:
    """CollectedAnswer → CollectionTaskResult 映射（answer 组装/出界 DLP 自检）。
    run_deepseek_collection 与 batch per-item ok 映射共用。"""
    answer_text = _compose_answer_text(collected.answer_text, collected.references)
    # 结构化信源（W2 source_fetch 的唯一输入）：references 判形时已保证真实 http(s)
    # URL；cited_text 无逐句引述可填 → None，transcript 口径诚实落 unverifiable。
    citations = [
        {
            "url": str(ref["url"]),
            "title": str(ref["title"]).strip() if ref.get("title") else None,
            "cited_text": None,
        }
        for ref in collected.references
        if isinstance(ref, dict) and _is_real_url(ref.get("url"))
    ]
    evidence: list[CollectionEvidenceRef] = list(collected.evidence)
    if collected.trace_path is not None:
        evidence.append(
            CollectionEvidenceRef(
                kind="sse",
                path=str(collected.trace_path),
                relation_type="answer_sse_trace",
                mime_type="application/json",
                source_url=_CHAT_URL,
            )
        )
    evidence.extend(collected.opened_source_previews)
    # 原始流量证据（2026-08-10 起）：sse_raw/har，_collect_one 题末导出。
    evidence.extend(collected.raw_evidence)
    if collected.answer_evidence is not None:
        evidence.append(collected.answer_evidence)
    official_share_image = next(
        (
            ref.path
            for ref in evidence
            if ref.kind == "share_image" and ref.relation_type == "official_share_image"
        ),
        None,
    )
    screenshot_ref = f"file://{official_share_image or collected.screenshot_path}"
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
    """Keep the platform answer separate from its structured source relations."""
    del references
    return answer_text.strip()


# ---------------------------------------------------------------------------
# Playwright 实现（sync，全部跑在 to_thread 线程里）
# ---------------------------------------------------------------------------


class _PlaywrightDeepseekSession:
    """DeepSeek 网页采集的 sync Playwright 实现（persistent context / CDP 常驻 attach）。

    单题（``collect``，per-task 老路径）与 run 级会话复用（``collect_batch``）
    共享同一套 per-item 主体 ``_collect_one``——绝不复制出两套：

    - ``collect``：一次会话、一题、收尾（per-task 行为不变）；
    - ``collect_batch``：一次会话，N 题在同一会话/同一标签页里顺序完成
      （真人在同一浏览器窗口里连续聊天——每题落在全新会话但绝不重开浏览器）；
      每题成功后做「阅读停顿」（human_like.human_read_pause：滚动浏览 + 停留，
      含最后一题——真人读完才关浏览器）。

    batch 失败语义（2026-08-14 细化，对齐豆包）：题级失败转 outcome，结果列表
    与输入等长同序；连坐按失败类型分级——真墙（captcha/login/send/muted）=
    账号级阻断，后续题全 aborted（零浏览器交互：真人撞墙后会停下，不编造不
    硬闯）；wall_quota=配额按 (账号×mode) 计费，只连坐同 mode 余题；
    wall_refusal/incomplete/toggle 失败/mode_unconfirmed=题级 flake 或内容
    失败，不连坐，本题诚实失败后续跑。session 建立阶段（launch/navigate/
    登录墙检查）的异常原样逃出，由 activity 层按 session 级语义处理（一题
    未发）。
    """

    def __init__(self, config: DeepseekAdapterConfig, evidence_dir: Path, file_stem: str) -> None:
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
        spec = DeepseekBatchItemSpec(
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
        self, items: list[DeepseekBatchItemSpec], on_stage: Callable[[str], None]
    ) -> list[DeepseekBatchItemOutcome]:
        outcomes: list[DeepseekBatchItemOutcome] = []
        # 配额墙按 (账号×mode) 计费（2026-08-14 起，对齐豆包）：wall_quota 只
        # 连坐同 mode 余题——记录已撞配额的 mode，轮到其余题位次时零浏览器交互
        # 追加 aborted 占位（结果列表与输入等长同序的契约不变）。
        quota_blocked: dict[str, DeepseekBatchItemSpec] = {}
        with self._browser_session(on_stage) as (context, page, pw_timeout, driver):
            for index, spec in enumerate(items):
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
                        # 照跑（深度思考配额耗尽 ≠ 快速模式不可用）。
                        quota_blocked[spec.mode] = spec
                        continue
                    # 真墙（captcha/login/send/muted…）：账号级阻断，余题全
                    # aborted（真人撞墙即停，零浏览器交互不硬闯）。
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
                except _ModeToggleFailed as toggle:
                    # 2026-08-13 起不连坐：chip 确认失败是页面态 flake（非账号墙），
                    # 大段批量下一中止会把百余未执行题连坐成 aborted。本题诚实失败、
                    # 继续下一题；真墙（验证码/登录）仍走上面的中止。
                    outcomes.append(
                        self._failure_outcome(spec, "wall", "mode_toggle_failed", toggle)
                    )
                    continue
                except _IncompleteCapture as inc:
                    # 同上：截图为题级 flake，记 incomplete 后续跑，不中止整批。
                    outcomes.append(
                        self._failure_outcome(spec, "incomplete", "answer_capture_incomplete", inc)
                    )
                    continue
                outcomes.append(
                    DeepseekBatchItemOutcome(
                        business_key=spec.business_key, status="ok", answer=answer
                    )
                )
                # 阅读停顿：拟人读完回答（滚动浏览 + 停留 8-25s 抖动）——题间天然
                # 间隔，也产出真实浏览信号；最后一题同样停留（真人读完才关浏览器）。
                pause_s = self._reading_pause(page)
                log.info(
                    "deepseek_read_pause",
                    business_key=spec.business_key,
                    seconds=round(pause_s, 2),
                )
        return outcomes

    @staticmethod
    def _failure_outcome(
        spec: DeepseekBatchItemSpec,
        status: str,
        error_type: str,
        exc: _WallError | _IncompleteCapture | _ModeToggleFailed | _ModeUnconfirmed,
    ) -> DeepseekBatchItemOutcome:
        return DeepseekBatchItemOutcome(
            business_key=spec.business_key,
            status=status,
            error_type=error_type,
            error_message=str(exc),
            evidence_path=exc.evidence_path,
            evidence=list(exc.evidence_refs),
        )

    @staticmethod
    def _aborted_outcome(
        spec: DeepseekBatchItemSpec,
        failed_spec: DeepseekBatchItemSpec,
        error_type: str | None,
        *,
        batch_stopped: bool = True,
    ) -> DeepseekBatchItemOutcome:
        # 真人撞墙后会停下：本题未执行（零浏览器交互），诚实标记不编造不硬闯。
        if batch_stopped:
            reason = (
                f"not executed: batch stopped after item {failed_spec.business_key!r} "
                f"failed ({error_type or 'unknown'}) — no browser interaction for this item"
            )
        else:
            # 配额连坐（wall_quota）：批次未停，仅同 mode 余题占位。
            reason = (
                f"not executed: same-mode quota wall at item {failed_spec.business_key!r} "
                f"({error_type or 'unknown'}) — no browser interaction for this item"
            )
        return DeepseekBatchItemOutcome(
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

        经 resident_browser.platform_browser：``GEO_DEEPSEEK_CDP_URL`` 非空 →
        ``connect_over_cdp`` attach 常驻浏览器（退出只断开 CDP 连接——不关
        context、不清理 profile，profile/登录态归 supervisor 所有）；否则回退
        ``launch_persistent_context``（每次全新，结束由契约层 finally close）。

        优雅关闭（仅 launch 路径，profile 崩溃标记根治）：启动前与 close 后各
        幂等执行一次 ``_clean_profile_crash_state``（复用 doubao_adapter 实现）。
        """
        # 延迟导入：模块加载不硬依赖浏览器驱动（worker 未装依赖时仍可注册 fail-closed 实现）。
        # 驱动首选 patchright（旧链生产同款，反检测补丁版）；vanilla playwright 的
        # webdriver 指纹有风控静默吞发送前科（豆包旧链 2026-07-15 live 实证），仅作开发兜底。
        driver, sync_playwright, PWTimeout = load_sync_browser_driver()

        on_stage("browser_launch")
        with sync_playwright() as pw:

            def _launch() -> tuple[Any, Any]:
                # 启动前愈合前任进程的崩溃标记（activity 取消/SIGKILL 会绕过正常 close，
                # Chromium 未写回 exit_type=Normal → 下次启动弹「Restore pages?」）。
                # 幂等纯文件操作，失败不阻塞启动（close 后还有一次兜底清理）。
                try:
                    _clean_profile_crash_state(self._config.profile_dir)
                except Exception:
                    pass
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
                context.set_default_timeout(_NAV_TIMEOUT_MS)
                page = context.pages[0] if context.pages else context.new_page()
                return context, page

            resident = False
            try:
                with platform_browser(
                    pw, platform=self._config.browser_key, launch=_launch
                ) as lease:
                    context, page, is_resident = lease
                    resident = is_resident

                    on_stage("navigate")
                    try:
                        page.goto(_CHAT_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
                    except PWTimeout:
                        page.goto(_CHAT_URL, wait_until="load", timeout=_NAV_TIMEOUT_MS)
                    page.wait_for_timeout(6_000)  # SPA + 未登录 /sign_in 跳转 settle（旧链同款）
                    _try_close_overlays(page, self._rng)
                    if _detect_login_wall(page):
                        raise _WallError(
                            "wall_login_required",
                            "deepseek login wall detected right after navigation "
                            "(redirect to /sign_in)",
                            self._shot(page, "login"),
                        )
                    yield context, page, PWTimeout, driver
            finally:
                # launch 路径：契约层 finally close 之后再兜底清理崩溃标记（覆盖
                # close 期竞态）；attach 路径绝不动 profile（归 supervisor 所有）。
                if not resident:
                    try:
                        _clean_profile_crash_state(self._config.profile_dir)
                    except Exception as exc:
                        log.warning(
                            "deepseek_profile_crash_clean_failed",
                            business_key=self._file_stem,
                            error=f"{type(exc).__name__}: {exc}",
                        )

    def _collect_one(
        self,
        context: Any,
        page: Any,
        spec: DeepseekBatchItemSpec,
        on_stage: Callable[[str], None],
        *,
        pw_timeout: type[Exception],
        driver: str,
    ) -> CollectedAnswer:
        """单题主体：await_input → fresh_chat → 拟人输入/发送 → SSE 捕获/组装/
        证据落盘。per-task 单题与 batch 每题共用。"""
        capture = _CompletionCapture(context, page)
        # 原始流量留痕（2026-08-10 起，用户拍板默认开）：独立 CDP session 自组
        # HAR + 落 completion 原始响应体，与既有 capture 互不干扰。
        # GEO_RAW_CAPTURE=0 → None（全关回退现状）。
        raw = maybe_raw_capture(
            context,
            page,
            body_url_hints=_COMPLETION_URL_HINTS,
            creator="geo-deepseek-adapter",
        )
        raw_evidence: list[CollectionEvidenceRef] = []
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
                # 禁言 banner（2026-08-14 起，对齐豆包）：composer 长期不可得时
                # 扫整页文本。只跑禁言 regex（整页含 UI 营销件，配额/拒答词表
                # 套整页必误伤，词表层已隔离）；命中改抛 wall_muted（带解封
                # 时间），走既有墙管道。
                muted = detect_muted_banner("deepseek", _read_page_text(page))
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

            # 测量口径（20260810 用户拍板）：两种 mode 都显式确保——normal=
            # 快速模式+智能搜索开+深度思考关；deep_think=快速+搜索开+思考开。
            # 必须在打字/发送之前完成并经后置校验确认；确认不了即诚实失败，
            # 绝不静默按错误口径采集（思考开/关错态 = 答案口径错标）。
            on_stage("ensure_mode")
            if not _set_collection_mode(page, self._rng, spec.mode):
                raise _ModeToggleFailed(
                    f"mode toggles could not be confirmed for mode={spec.mode!r} "
                    "(快速模式 tab / 深度思考 chip / 智能搜索 chip; selector drift "
                    "or control unavailable)",
                    _shot("mode_toggle"),
                )
            _pace(*_PACE_AFTER_NEW_CHAT_S)  # 切完（或确认完）开关回神再回到输入框

            on_stage("typing")
            # 页面就绪：真人先端详一眼再动手（零停顿直点输入框是机器人指纹）。
            _pace(*_PACE_PAGE_READY_S)
            # SPA settle 后可能异步弹遮罩（豆包「下载电脑版」同款教训）：await_input
            # 后再收一次，覆盖迟到弹层。
            _try_close_overlays(page, self._rng)
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

            # 异步验证码窗口：challenge 发送后才挂载（豆包旧链实测 ~2.2s），轮询至多 12s；
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
                page,
                appearance_timeout_s=20.0,
                timeout_s=(
                    _CHAT_TIMEOUT_DEEP_THINK_S if spec.mode == "deep_think" else _CHAT_TIMEOUT_S
                ),
            )
            answer_text = ""
            references: list[dict[str, Any]] = []
            trace_path: Path | None = None
            search_queries: list[dict[str, Any]] = []
            opened_source_previews: list[CollectionEvidenceRef] = []
            sse_body = capture.latest_body()
            rich: dict[str, Any] | None = None
            if sse_body:
                rich = _rich_record_from_sse(sse_body)
                if rich is not None:
                    answer_text = str(rich.get("answer_text") or "").strip()
                    references = list(rich.get("references") or [])
                    search_queries = list(rich.get("search_queries") or [])
                    # SSE 结构化 trace 落盘进证据链（kind="sse"，豆包同款流程）：
                    # 思考链/检索词等结构化产物序列化落证据目录。写盘失败不拖垮
                    # 已成功的采集——如实 warning 且不出该证据（绝不出残缺/编造）。
                    trace_path_candidate = self._evidence_dir / f"{spec.file_stem}-sse-trace.json"
                    try:
                        trace_path_candidate.write_text(
                            json.dumps(
                                _build_sse_trace(rich),
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            encoding="utf-8",
                        )
                        trace_path = trace_path_candidate
                    except Exception:
                        log.warning(
                            "deepseek_sse_trace_persist_failed",
                            file_stem=spec.file_stem,
                            exc_info=True,
                        )
                    on_stage("opened_source_previews")
                    opened_source_previews, opened_preview_audit = _capture_opened_source_previews(
                        context,
                        list(rich.get("opened_pages") or []),
                        evidence_dir=self._evidence_dir,
                        file_stem=spec.file_stem,
                        timeout_error=pw_timeout,
                    )
                    log.info(
                        "deepseek_opened_source_previews",
                        business_key=spec.business_key,
                        **opened_preview_audit,
                    )
            if not answer_text and meta.get("found"):
                # SSE 捕获/解析失败时的 DOM 兜底（推理链剥离后取正文）
                answer_text = _extract_response_text(page)
            on_stage("answer_extracted")

            # 软墙/实名扫描无条件执行（2026-08-14 起，对齐豆包——曾被
            # `if not answer_text:` 门挡，出了"答案"就绝不扫描 = 配额/禁言文案
            # 当答案采回的事故根因）。已出答案时把答案正文从扫描文本中剔除：
            # 「答案正文提及「过频/实名」不翻标记」的旧不变量保持成立
            # （best-effort 精确串剔除，见 _scan_dom_notices）。
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
                    "but no completion stream fired within timeout — likely "
                    "content-filter or silent server-side drop",
                    _shot("no_stream"),
                )
            if not meta.get("finished"):
                raise _IncompleteCapture(
                    "stream-open-at-timeout: completion stream still open after "
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

            # 答案验收门（2026-08-14 起，词表唯一真源 wall_lexicon，对齐豆包）：
            # 答案文本定稿后、返回 ok 之前——平台提示文案（配额耗尽/禁言/拒答
            # 模板）被当作答案采回时在此拦截，抛 _WallError 走既有墙管道（batch
            # 连坐语义按 wall_type 细化，见 collect_batch docstring）。batch 与
            # per-task 单题共用本路径，两路都盖。
            verdict = classify_answer_text("deepseek", answer_text)
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

            # mode 证据升级（2026-08-14 起，对齐豆包，warning-only →
            # non_retryable 诚实失败）：SSE trace 已在答案抽取阶段先落盘取证。
            # 请求 deep_think 而 SSE 无 thinking 碎片证据 = 平台静默回退非思考
            # 模式的嫌疑答案，绝不落 completed（2026-08-13 事故教训：配额耗尽后
            # 的回退答案曾被当 deep_think 有效答案采回）。
            if spec.mode == "deep_think" and not (rich and rich.get("deep_think_active")):
                raise _ModeUnconfirmed(
                    "deep_think requested and mode chips confirmed, but SSE stream "
                    "carries no thinking-fragment evidence (deep_think_active=False) "
                    "— refusing to record a normal-evidence answer as deep_think",
                    _shot("mode_unconfirmed"),
                )

            on_stage("share_export")
            share_image_path = self._evidence_dir / f"{spec.file_stem}-share.png"

            def _share_click(locator: Any) -> None:
                clicked_at = human_click(locator, page, self._rng, start=self._mouse_pos)
                if clicked_at is not None:
                    self._mouse_pos = clicked_at

            try:
                share = capture_deepseek_official_share(
                    page,
                    share_image_path,
                    click=_share_click,
                )
                share_link_path = self._evidence_dir / f"{spec.file_stem}-share-link.json"
                write_share_link_manifest(
                    share_link_path,
                    share_url=share.share_url,
                    platform="deepseek",
                    channel="create-and-copy",
                    verification=probe_official_share_url(
                        share.share_url,
                        allowed_hosts={"chat.deepseek.com"},
                    ),
                )
            except (OfficialShareExportError, OSError) as exc:
                raise _IncompleteCapture(
                    "official-share-export-incomplete: DeepSeek must provide its "
                    "public share URL and a clean image of that official share page "
                    f"({type(exc).__name__}: {exc})",
                    _shot("share_export"),
                ) from exc
            except Exception as exc:
                raise _IncompleteCapture(
                    "official-share-export-incomplete: unexpected DeepSeek share UI "
                    f"failure ({type(exc).__name__}: {exc})",
                    _shot("share_export"),
                ) from exc
            evidence.extend(
                [
                    CollectionEvidenceRef(
                        kind="share_image",
                        path=str(share.image_path),
                        relation_type="official_share_image",
                        mime_type="image/png",
                        source_url=share.share_url,
                    ),
                    CollectionEvidenceRef(
                        kind="share_link",
                        path=str(share_link_path),
                        relation_type="official_share_link",
                        mime_type="application/json",
                        source_url=share.share_url,
                    ),
                ]
            )
            answer = CollectedAnswer(
                answer_text=answer_text,
                references=references,
                screenshot_path=shot_path,
                meta={
                    "stream": meta,
                    "sse_body_bytes": len(sse_body),
                    "driver": driver,
                },
                trace_path=trace_path,
                search_queries=search_queries,
                raw_evidence=raw_evidence,
                opened_source_previews=opened_source_previews,
                evidence=evidence,
                answer_evidence=answer_evidence,
            )
        except (_WallError, _IncompleteCapture, _ModeToggleFailed, _ModeUnconfirmed) as exc:
            # 失败题同样留 raw/HAR（题末先 dump 后 detach）：ref 挂异常对象，经
            # _failure_outcome → 失败 result.evidence → persist 层进 CAS。
            exc.evidence_refs = dump_raw_evidence_refs(
                raw,
                self._evidence_dir,
                spec.file_stem,
                source_url=_CHAT_URL,
                warn_tag="deepseek",
            )
            raise
        else:
            raw_evidence.extend(
                dump_raw_evidence_refs(
                    raw,
                    self._evidence_dir,
                    spec.file_stem,
                    source_url=_CHAT_URL,
                    warn_tag="deepseek",
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


def _try_close_overlays(page: Any, rng: random.Random) -> None:
    """best-effort 关 cookie 横幅/「我知道了」等遮罩（拟人化点击）。

    先 count/visible 粗筛（纯观测），只有真实存在的遮罩才 human_click——
    避免对全部候选选择器逐一发贝塞尔点击（那本身也是机器人指纹）。
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
    # 回退：导航到聊天首页（DeepSeek / 默认即全新会话）并等 composer 回来。
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


# ---------------------------------------------------------------------------
# 模式开关确保（20260810 live 校准：快速模式 tab + 深度思考/智能搜索 chips）
# ---------------------------------------------------------------------------

# 开关 chip = ``div.ds-toggle-button``（ds- 设计系统 class，跨构建稳定），状态位
# 用语义属性 ``aria-pressed``（优先于 ``ds-toggle-button--selected`` class 判定）。

# 模式 tab 条（快速/专家/识图）选中态探针：hash class 跨构建漂移，绝不硬编码——
# 用结构差分（选中 tab 容器比兄弟多一个 class token；20260810 实测快速模式容器
# = 3 token，未选中 = 2 token）。三标签凑不齐（非新会话首页布局）/无法唯一差分
# → found:false / selected:null，调用方按「不可观测」处理，绝不猜。
_TAB_STATE_JS = """() => {
  const labels = ["快速模式", "专家模式", "识图模式"];
  const rows = [];
  for (const label of labels) {
    let hit = null;
    for (const el of document.querySelectorAll("div")) {
      if ((el.innerText || "").trim() !== label) continue;
      if (el.children.length > 1) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.width > 240) continue;
      hit = el;
      break;
    }
    if (!hit) return {found: false};
    let cont = hit;
    for (let i = 0; i < 2 && cont.parentElement; i++) cont = cont.parentElement;
    const tokens = (cont.className || "").toString().trim().split(/\\s+/).filter(Boolean);
    rows.push({label, n: tokens.length});
  }
  const max = Math.max(...rows.map(r => r.n));
  const top = rows.filter(r => r.n === max);
  if (top.length !== 1) return {found: true, selected: null};
  return {found: true, selected: top[0].label};
}"""


def _chip_locator(page: Any, name: str) -> Any | None:
    """composer 区具名开关 chip（可见才返回 Locator；找不到返回 None）。"""
    try:
        loc = page.locator(f'div.ds-toggle-button:has-text("{name}")').first
        if loc.count() > 0 and loc.is_visible(timeout=500):
            return loc
    except Exception:
        pass
    return None


def _chip_engaged(page: Any, name: str) -> bool | None:
    """chip 当前是否按下；找不到/读不到状态 → None（调用方诚实失败，绝不猜）。"""
    loc = _chip_locator(page, name)
    if loc is None:
        return None
    try:
        pressed = loc.get_attribute("aria-pressed")
    except Exception:
        return None
    if pressed is None:
        return None
    return str(pressed).strip().lower() == "true"


def _fast_mode_engaged(page: Any) -> bool | None:
    """tab 条选中态：快速模式选中 → True；明确选中其他模式 → False；tab 条不在屏
    （非新会话首页布局）或结构差分失败 → None。"""
    try:
        state = page.evaluate(_TAB_STATE_JS)
    except Exception:
        return None
    if not isinstance(state, dict) or not state.get("found"):
        return None
    selected = state.get("selected")
    if selected is None:
        return None
    return str(selected) == "快速模式"


def _ensure_fast_mode(page: Any, rng: random.Random) -> bool:
    """确保 tab 条选中快速模式。已在快速 → True；明确在其他模式 → 拟人点击
    「快速模式」并二次确认；tab 条不可观测 → 不阻断（ chips 仍严格校验——
    快速模式是账号缺省且两生产 profile 实证选中，tab 条只在新会话首页出现）。"""
    state = _fast_mode_engaged(page)
    if state is None:
        return True
    if state:
        return True
    tab = page.locator('span:text-is("快速模式")').first
    try:
        if tab.count() == 0 or not tab.is_visible(timeout=500):
            return False
        human_click(tab, page, rng)
    except Exception:
        return False
    page.wait_for_timeout(400)
    if _fast_mode_engaged(page) is not True:
        # 隔拍二次确认（豆包 T-03 同款纪律：UI 可能乐观翻转后回退）。
        page.wait_for_timeout(400)
        if _fast_mode_engaged(page) is not True:
            return False
    return True


def _ensure_chip(page: Any, rng: random.Random, name: str, want: bool) -> bool:
    """把具名 chip 确保到 want 态：已在目标态零点击（幂等，不制造多余行为
    指纹）；否则拟人点击 + 状态翻转等待 + 隔拍二次确认。找不到/读不出状态/
    点了不翻转 → False（调用方诚实失败，绝不猜）。"""
    current = _chip_engaged(page, name)
    if current is None:
        return False
    if current is want:
        return True
    chip = _chip_locator(page, name)
    if chip is None:
        return False
    try:
        human_click(chip, page, rng)
    except Exception:
        return False
    page.wait_for_timeout(400)
    if _chip_engaged(page, name) is not want:
        # 隔拍二次确认（豆包 T-03 同款纪律：UI 可能乐观翻转后回退）。
        page.wait_for_timeout(400)
        if _chip_engaged(page, name) is not want:
            return False
    return True


def _set_collection_mode(page: Any, rng: random.Random, mode: str) -> bool:
    """测量口径（20260810 用户拍板——专家模式不支持搜索，GEO 评测不测专家）：
    ``normal`` = 快速模式 tab + 智能搜索开 + 深度思考关；``deep_think`` =
    快速 + 搜索开 + 思考开。全部后置校验确认才 True；确认不了 False。"""
    if not _ensure_fast_mode(page, rng):
        return False
    if not _ensure_chip(page, rng, "智能搜索", True):
        return False
    return _ensure_chip(page, rng, "深度思考", mode == "deep_think")


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
        loc = page.locator('[data-geo-send="true"]').first
        human_click(loc, page, rng, start=start)
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
    """发送并确认提交真正生效（DeepSeek 优先回车——live 校准回车即发送；
    拟人化按钮点击兜底；被吞时像真人一样顿一下再试一次）。"""
    used = 0
    for i in range(max(1, attempts)):
        used = i + 1
        if not _send_via_keyboard(page, input_loc):
            _click_send_button(page, rng, start=start)
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


def _read_page_text(page: Any) -> str:
    """best-effort 读 body 文本（2s 超时；失败=空串，绝不因此拖垮采集）。"""
    try:
        return str(page.locator("body").inner_text(timeout=2000) or "")
    except Exception:
        return ""


def _scan_dom_notices(page: Any, *, exclude: str = "") -> dict[str, list[str]]:
    """best-effort 读 body 文本扫系统通知词（限流 / 实名墙）。

    2026-08-14 起调用方无条件扫描（不再 gated by 无答案，对齐豆包）；
    ``exclude`` 传入已定稿答案正文做精确串剔除——系统通知在答案气泡之外，
    剔除后「答案正文提及「过频/实名」不翻标记」的旧不变量保持成立
    （best-effort 精确串剔除，词表本身已是平台口吻短语）。"""
    text = _read_page_text(page)
    if exclude:
        text = text.replace(exclude, " ")
    return {
        "softban": [p for p in _SOFTBAN_DOM_PHRASES if p in text],
        "realname": [p for p in _REALNAME_DOM_PHRASES if p in text],
    }


def _capture_full_page(page: Any, out_path: Path, *, expected_question: str) -> dict[str, Any]:
    """Capture exactly one DeepSeek question and assistant message, without UI chrome."""

    return capture_scoped_chat_tiles(
        page,
        out_path,
        probe_script=_DEEPSEEK_CAPTURE_STATE_JS,
        restore_script=_DEEPSEEK_CAPTURE_RESTORE_JS,
        expected_question=expected_question,
        method="deepseek_scoped_message_tiles",
        # DeepSeek's 34px thinking header is sticky inside the assistant message.
        # Record it naturally in the first tile, then exclude it from repeat tiles.
        repeat_top_inset_css_px=48.0,
    )


def _opened_source_preview_limit() -> int:
    raw = os.environ.get(ENV_OPENED_SOURCE_PREVIEW_LIMIT, "").strip()
    if not raw:
        return _DEFAULT_OPENED_SOURCE_PREVIEW_LIMIT
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_OPENED_SOURCE_PREVIEW_LIMIT
    return max(0, min(value, _MAX_OPENED_SOURCE_PREVIEW_LIMIT))


_BLOCKED_PREVIEW_HOSTS = frozenset(
    {
        "localhost",
        "metadata",
        "metadata.google.internal",
        "metadata.google",
        "instance-data",
        "instance-data.ec2.internal",
        "169.254.169.254",
    }
)


def _host_resolves_globally(
    host: str, *, resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo
) -> bool:
    """Fail closed unless every resolved IPv4/IPv6 address is globally routable."""

    lowered = host.casefold().rstrip(".")
    if (
        lowered in _BLOCKED_PREVIEW_HOSTS
        or lowered.endswith(".localhost")
        or lowered.endswith(".local")
        or lowered.endswith(".internal")
    ):
        return False
    try:
        infos = resolver(lowered, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    addresses: set[str] = set()
    for info in infos:
        try:
            address = str(info[4][0]).split("%", 1)[0]
            parsed_address = ipaddress.ip_address(address)
        except (IndexError, TypeError, ValueError):
            return False
        if not parsed_address.is_global:
            return False
        addresses.add(str(parsed_address))
    return bool(addresses)


def _external_http_url(
    value: object,
    *,
    host_guard: Callable[[str], bool] = _host_resolves_globally,
) -> str | None:
    """Reject credentials and non-public destinations before browser navigation."""

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
        if not host_guard(host):
            return None
    except ValueError:
        return None
    return value


def _capture_opened_source_previews(
    context: Any,
    opened_pages: list[dict[str, Any]],
    *,
    evidence_dir: Path,
    file_stem: str,
    timeout_error: type[Exception],
) -> tuple[list[CollectionEvidenceRef], dict[str, Any]]:
    """Re-open TOOL_OPEN URLs and capture an unmodified visible-page overview.

    A preview is deliberately not called an original browsing screenshot: DeepSeek's
    SSE proves the URL-level TOOL_OPEN event, while this image is a later reproducible
    rendering in the collector browser. No title, summary, badge, or synthetic text is
    injected into the page.
    """

    limit = _opened_source_preview_limit()
    output: list[CollectionEvidenceRef] = []
    failures: list[dict[str, str | int]] = []
    seen: set[str] = set()
    candidates: list[tuple[int, dict[str, Any], str]] = []
    for ordinal, source in enumerate(opened_pages, 1):
        if not isinstance(source, dict):
            continue
        url = _external_http_url(source.get("url"))
        if url is None:
            failures.append({"ordinal": ordinal, "error": "unsafe_or_invalid_url"})
            continue
        dedupe_key = url.split("#", 1)[0]
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        candidates.append((ordinal, source, url))
        if len(candidates) >= limit:
            break

    for ordinal, source, url in candidates:
        source_page = context.new_page()
        try:
            try:
                source_page.goto(url, wait_until="domcontentloaded", timeout=15_000)
            except timeout_error:
                # A visible DOM can still be a faithful preview when trackers keep the
                # navigation open. Any other navigation failure is recorded below.
                pass
            source_page.wait_for_timeout(1_500)
            path = evidence_dir / f"{file_stem}-opened-source-{ordinal:02d}.png"
            source_page.screenshot(path=str(path), full_page=False)
            if not path.is_file() or path.stat().st_size <= 0:
                raise RuntimeError("preview_screenshot_missing")
            try:
                rendered_title = str(source_page.title() or "").strip()
            except Exception:
                rendered_title = ""
            output.append(
                CollectionEvidenceRef(
                    kind="source_screenshot",
                    path=str(path),
                    relation_type="ai_opened_source_preview",
                    mime_type="image/png",
                    source_url=url,
                    title=rendered_title or str(source.get("title") or "").strip() or None,
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
    return output, {
        "tool_open_count": len(opened_pages),
        "requested": len(candidates),
        "captured": len(output),
        "failures": failures,
        "semantics": "tool_open_url_reproduction_preview",
    }


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


# 引用卡片判形（20260810 deep_think+智能搜索实测流）：带真实 url 且带标题/站点/
# 摘要任一即卡片——载体有三种（答案 ``references`` 数组 / TOOL_SEARCH ``results``
# SET 数组 / TOOL_OPEN ``result`` 单卡），按形状识别比按父键名识别更抗漂移。
# 指针型 dict（{"id":3,"type":"TOOL_SEARCH"} 引用锚）无 url，天然滤掉。
_REF_LABEL_KEYS = ("title", "name", "site_name", "sitename", "source", "snippet", "summary")


def _append_reference_card(card: Any, sink: list[dict[str, Any]], seen_urls: set[str]) -> None:
    """引用卡片判形（带真实 url 才收）+ 按 URL（去 query）去重 + 字段归一。"""
    if not isinstance(card, dict):
        return
    url = card.get("url") or card.get("link") or ""
    if not _is_real_url(url):
        return
    dedup = str(url).split("?")[0]
    if dedup in seen_urls:
        return
    seen_urls.add(dedup)
    sink.append(
        {
            "url": url,
            "title": card.get("title") or card.get("name"),
            "sitename": (card.get("site_name") or card.get("sitename") or card.get("source")),
            "summary": card.get("snippet") or card.get("summary"),
        }
    )


def _walk_references(node: Any, sink: list[dict[str, Any]], seen_urls: set[str]) -> None:
    """递归找引用卡片（判形见 ``_append_reference_card``；旧链 _references_native
    口径，⚠ 字段名 GUESS：url|link / title|name / site_name|sitename|source），
    按 URL 去重。"""
    if isinstance(node, dict):
        if _is_real_url(node.get("url") or node.get("link") or "") and any(
            node.get(key) for key in _REF_LABEL_KEYS
        ):
            _append_reference_card(node, sink, seen_urls)
            return  # 卡片不再下钻（卡内 url 字段唯一，防重复计数）
        for value in node.values():
            _walk_references(value, sink, seen_urls)
    elif isinstance(node, list):
        for item in node:
            _walk_references(item, sink, seen_urls)


# 引用锚点（[reference:N]）：渲染层 chip 的机器锚点，不是正文文本——剔除（引用
# 本身进 references；DOM 兜底路径本就不含这些字面量，两个抽取口径对齐）。
_REFERENCE_ANCHOR_RE = re.compile(r"\s*\[reference:\d+\]")


def _assemble_deepseek_record(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """把 SSE 事件序列组装成 answer_text + references；零正文 → None（→ DOM 兜底）。

    正文 = 碎片状态机（20260810 deep_think+智能搜索实测流校准，兼容 20260727
    normal 流）：

    - 碎片建档（按到达顺序登记 ``type``）：初始快照
      ``{"v":{"response":{"fragments":[...]}}}``；独立新增
      ``{"p":"response/fragments","o":"APPEND","v":[...]}``；BATCH 内相对路径
      ``{"p":"fragments","o":"APPEND"}``。
    - 增量归属：``{"p":"response/fragments/<N>/content","o":"APPEND"}``（N=-1=最后
      一个）→ 指定碎片；裸增量 ``{"v":"..."}`` 与裸子补丁列 ``{"v":[...]}`` →
      当前最后碎片（实测：碎片切换后裸增量立即跟随新碎片）。
    - 只收 ``type=="RESPONSE"`` 碎片的 content——THINK/TOOL_SEARCH/TOOL_OPEN 等
      痕迹碎片不进正文（normal 流答案即唯一 RESPONSE 碎片，行为与旧版一致；
      deep_think 流思考链混入正文是旧版状态无关设计的实测缺陷）。
    - ``{"o":"SET"/"BATCH"}`` 状态/批处理 op 一律不进正文（防 "FINISHED" 混入）。
    - 思考链不落正文但落证据（20260810 起）：THINK 碎片 content 单独累积为
      ``thinking_text``，TOOL_SEARCH 碎片携带的 ``queries`` 登记为平台真实
      检索词（W1），快照 ``thinking_enabled`` 记为 ``deep_think_active``——
      供 SSE trace 证据（kind="sse"）与 search_queries 持久化复用。
    """
    frag_types: list[str] = []
    parts: list[str] = []
    thinking_parts: list[str] = []
    search_queries: list[dict[str, Any]] = []
    deep_think_active = False
    # DeepSeek 的检索流有三个不能混为一谈的来源集合：
    #
    # - TOOL_SEARCH.results：搜索引擎返回的候选命中；
    # - TOOL_OPEN.result：模型随后实际打开/读取的页面；
    # - RESPONSE.references：最终回答显式挂载的引用指针（只在指向一个
    #   URL 可解析的 TOOL_OPEN，或自身就是 URL 卡片时才能落 citation）。
    #
    # 旧实现对每个 SSE data 做无上下文递归，导致 48 个搜索命中与 6 个
    # TOOL_OPEN 页面全部进入 ``references``。这既把“检索到”冒充“浏览过”，
    # 又让 analytics.citation_fact 的语义失真。
    search_results: list[dict[str, Any]] = []
    opened_pages: list[dict[str, Any]] = []
    direct_answer_references: list[dict[str, Any]] = []
    seen_search_urls: set[str] = set()
    seen_opened_urls: set[str] = set()
    seen_answer_urls: set[str] = set()
    opened_by_fragment_id: dict[str, dict[str, Any]] = {}
    answer_reference_ids: list[str] = []
    source_activity_observed = False
    frag_ids: list[str | None] = []

    def _card(value: Any) -> dict[str, Any] | None:
        cards: list[dict[str, Any]] = []
        _append_reference_card(value, cards, set())
        return cards[0] if cards else None

    def _record_answer_references(value: Any) -> None:
        if not isinstance(value, list):
            return
        for reference in value:
            card = _card(reference)
            if card is not None:
                _append_reference_card(card, direct_answer_references, seen_answer_urls)
                continue
            if not isinstance(reference, dict) or reference.get("id") is None:
                continue
            fragment_id = str(reference["id"])
            if fragment_id not in answer_reference_ids:
                answer_reference_ids.append(fragment_id)

    def _record_opened(value: Any, fragment_id: str | None = None) -> None:
        nonlocal source_activity_observed
        source_activity_observed = True
        card = _card(value)
        if card is None:
            return
        _append_reference_card(card, opened_pages, seen_opened_urls)
        if fragment_id is not None:
            opened_by_fragment_id[fragment_id] = card

    def _register(frags: Any) -> None:
        nonlocal source_activity_observed
        if not isinstance(frags, list):
            return
        for frag in frags:
            if not isinstance(frag, dict):
                continue
            frag_types.append(str(frag.get("type") or ""))
            fragment_id = str(frag["id"]) if frag.get("id") is not None else None
            frag_ids.append(fragment_id)
            # 快照/新增碎片自带的起始 content（正文只收 RESPONSE；思考链收 THINK）
            content = frag.get("content")
            if frag_types[-1] == "RESPONSE" and isinstance(content, str) and content:
                parts.append(content)
            elif frag_types[-1] == "THINK" and isinstance(content, str) and content:
                thinking_parts.append(content)
            elif frag_types[-1] == "TOOL_SEARCH":
                source_activity_observed = True
                for query in frag.get("queries") or []:
                    if not isinstance(query, dict):
                        continue
                    text = query.get("query")
                    if isinstance(text, str) and text.strip():
                        search_queries.append(
                            {"query": text.strip(), "ordinal": len(search_queries) + 1}
                        )
                _walk_references(frag.get("results"), search_results, seen_search_urls)
            elif frag_types[-1] == "TOOL_OPEN":
                _record_opened(frag.get("result"), fragment_id)
            if frag_types[-1] == "RESPONSE":
                _record_answer_references(frag.get("references"))

    def _target_index(path: str) -> int | None:
        m = re.search(r"fragments/(-?\d+)(?:/|$)", path)
        if not m:
            return None
        idx = int(m.group(1))
        if idx < 0:
            idx += len(frag_types)
        return idx if 0 <= idx < len(frag_types) else None

    def _accept(idx: int | None, text: Any) -> None:
        if idx is None or not isinstance(text, str) or not text:
            return
        if frag_types[idx] == "RESPONSE":
            parts.append(text)
        elif frag_types[idx] == "THINK":
            thinking_parts.append(text)

    def _sub_patches(subs: Any, base_idx: int | None) -> None:
        """BATCH/裸列的子补丁（相对路径：base = response 或某个 fragment）。"""
        if not isinstance(subs, list):
            return
        for sub in subs:
            if not isinstance(sub, dict):
                continue
            sp, so, sv = sub.get("p") or "", sub.get("o"), sub.get("v")
            if sp == "fragments" and so == "APPEND":
                _register(sv)
            elif so == "APPEND" and sp == "content":
                _accept(base_idx, sv)
            elif so == "APPEND" and sp.endswith("/content"):
                _accept(_target_index(sp), sv)
            elif sp == "references" or sp.endswith("/references"):
                if base_idx is None or frag_types[base_idx] == "RESPONSE":
                    _record_answer_references(sv)

    for ev in events:
        data = ev.get("data")
        if not isinstance(data, dict):
            continue
        v = data.get("v")
        o = data.get("o")
        p = data.get("p") or ""
        target_idx = _target_index(p) if "fragments/" in p else None
        if p.rstrip("/").endswith("/results"):
            source_activity_observed = True
            _walk_references(v, search_results, seen_search_urls)
        elif p.rstrip("/").endswith("/result"):
            fragment_id = frag_ids[target_idx] if target_idx is not None else None
            _record_opened(v, fragment_id)
        elif p.rstrip("/").endswith("/references"):
            if target_idx is None or frag_types[target_idx] == "RESPONSE":
                _record_answer_references(v)
        if isinstance(v, dict):
            resp = v.get("response")
            if isinstance(resp, dict):
                if resp.get("thinking_enabled") is True:
                    deep_think_active = True
                _register(resp.get("fragments"))
            continue
        if isinstance(v, list):
            if o == "APPEND" and p.rstrip("/").endswith("fragments"):
                _register(v)  # 独立碎片新增
            elif o == "BATCH":
                _sub_patches(v, _target_index(p) if "fragments/" in p else None)
            elif o is None and not p:
                _sub_patches(v, len(frag_types) - 1 if frag_types else None)
            continue
        if not (isinstance(v, str) and v):
            continue
        if o == "APPEND":
            idx = _target_index(p) if p else (len(frag_types) - 1 if frag_types else None)
            _accept(idx, v)
        elif o is None and not p:
            _accept(len(frag_types) - 1 if frag_types else None, v)

    answer_text = _REFERENCE_ANCHOR_RE.sub("", _recover_mojibake("".join(parts))).strip()
    if not answer_text:
        return None
    references = list(direct_answer_references)
    for fragment_id in answer_reference_ids:
        opened = opened_by_fragment_id.get(fragment_id)
        if opened is not None:
            _append_reference_card(opened, references, seen_answer_urls)
    return {
        "answer_text": answer_text,
        "references": references,
        "search_results": search_results,
        "opened_pages": opened_pages,
        # taxonomy v2 parsed the complete captured stream, therefore an empty list
        # means “zero TOOL_OPEN events”, not “legacy classification unavailable”.
        "opened_pages_observed": True,
        "thinking_text": _recover_mojibake("".join(thinking_parts)).strip(),
        "search_queries": search_queries,
        "deep_think_active": deep_think_active,
    }


def _build_sse_trace(record: dict[str, Any]) -> dict[str, Any]:
    """组装结果 → SSE 结构化 trace record（kind="sse" 证据内容）。

    形状对齐豆包 trace（collection router 的 build_task_trace_view 消费同一
    词表：thinking_chain / search_blocks / deep_think_active）——思考链为平台
    明确传输到浏览器的公开思考碎片原文，不含 HAR/headers/cookies。
    """
    thinking_text = str(record.get("thinking_text") or "")
    search_queries = list(record.get("search_queries") or [])
    references = list(record.get("references") or [])
    search_results = list(record.get("search_results") or [])
    opened_pages = list(record.get("opened_pages") or [])
    thinking_chain: list[dict[str, Any]] = []
    if thinking_text:
        thinking_chain.append({"kind": "reasoning", "text": thinking_text})
    if search_queries:
        thinking_chain.append(
            {
                "kind": "search",
                "queries": [str(q.get("query") or "") for q in search_queries],
                "summary": "",
            }
        )
    search_blocks: list[dict[str, Any]] = []
    if search_queries or search_results:
        search_blocks.append(
            {
                "scene": None,
                "queries": [str(q.get("query") or "") for q in search_queries],
                "summary": "",
                "results": [
                    {
                        "title": str(ref.get("title") or "未命名来源"),
                        "url": ref.get("url"),
                        "site": ref.get("sitename"),
                        "rank": index,
                        "summary": str(ref.get("summary") or ""),
                    }
                    for index, ref in enumerate(search_results, 1)
                ],
            }
        )
    return {
        "engine": "deepseek",
        "source_taxonomy_version": 2,
        "deep_think_active": bool(record.get("deep_think_active")),
        "thinking_chain": thinking_chain,
        "search_blocks": search_blocks,
        "opened_pages_observed": bool(record.get("opened_pages_observed")),
        "opened_pages": [
            {
                "title": str(ref.get("title") or "未命名来源"),
                "url": ref.get("url"),
                "site": ref.get("sitename"),
                "rank": index,
                "summary": str(ref.get("summary") or ""),
            }
            for index, ref in enumerate(opened_pages, 1)
        ],
        "answer_reference_pages": [
            {
                "title": str(ref.get("title") or "未命名来源"),
                "url": ref.get("url"),
                "site": ref.get("sitename"),
                "rank": index,
                "summary": str(ref.get("summary") or ""),
            }
            for index, ref in enumerate(references, 1)
        ],
    }
