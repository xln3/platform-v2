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

v1 边界（20260810 deep_think 解锁）：

- ``mode='normal'`` 与 ``mode='deep_think'`` 均支持；其他 mode →
  ``ApplicationError(..., type="unsupported_mode", non_retryable=True)``。
  deep_think = composer 左侧「深度思考」chip（``div.ci-model-button:has(
  div.internet-search-icon)``，20260810 live 校准）：开=容器 class
  ``ci-model-button-active`` 且 ``data-ci-show-ext`` 的 ``is_open:"1"``，
  关=``ci-model-button-inactive``/``is_open:"0"``。chip 态账号级粘滞——
  每题发送前显式确保目标态（幂等，已在目标态零点击）；点击后读不回
  目标态 → ``mode_toggle_failed`` non_retryable，绝不按错误口径采。
- deep_think 思考链落证据：思考步在 ``div.ai-thinking-steps``（答案
  generate 块内部，header「深度思考完成/中」）——逐 step ``main`` 叶节点
  原文拼装，落 trace JSON（kind="sse"，transport="dom"：文心 SW 中转抓不到
  SSE，思考链来自 DOM 实渲染，与豆包/DeepSeek 的 SSE 来源差异如实标注）；
  正文抽取经 JS clone 剔除思考块，绝不混入 answer_text。
- 配置全走 env（秘密绝不进 task payload）：
  ``GEO_YIYAN_PROFILE_DIR``（必填，persistent profile 目录；缺失/不存在 →
  ``adapter_not_configured`` non_retryable）；``GEO_YIYAN_PROXY_URL``（可选，
  形如 http://user:pass@host:port——日志只出现打码后的 scheme://host:port）；
  ``GEO_ADAPTER_EVIDENCE_DIR``（截图目录，全适配器共享 env，默认
  ``platform-v2/runtime/adapter-evidence/yiyan/``，自动建目录）；
  ``GEO_YIYAN_HEADLESS``（默认 1 headless；0=headed 需 DISPLAY）；
  ``GEO_YIYAN_CDP_URL``（可选，常驻 Chromium 的 CDP 地址——非空则 attach
  跨 run 复用会话，见 resident_browser 契约）。
- 执行模型：sync 浏览器驱动（patchright 首选，vanilla playwright 兜底）包在
  ``asyncio.to_thread`` 里跑；activity 协程侧每 10s 泵一次 heartbeat。
- 墙分类（先截屏存证再抛，错误 message 带证据路径、绝不含秘密）：
  登录墙/实名墙 → ``wall_login_required`` non_retryable；验证码 → ``wall_captcha``
  non_retryable；发送墙/限流 → ``wall_send`` non_retryable。
  2026-08-14 起（墙词表 ``wall_lexicon``，对齐豆包）：答案文本级配额/禁言/
  拒答 → ``wall_quota``/``wall_muted``/``wall_refusal`` non_retryable；batch
  连坐按 wall_type 细化（muted 全连坐、quota 只连坐同 mode、refusal 不
  连坐，见 ``collect_batch`` docstring）。
- 成功判据（零合成）：提交被接受（输入框清空）且答案容器出现且「生成中」
  指示器消失、正文静默稳定且非空且不含墙特征——缺一都不得返回成功。
  流截断/空答案/无流，或文心官方分享卡片 PNG/公开链接任一缺失 →
  ``answer_capture_incomplete``（可重试的诚实失败）；运行时截图绝不冒充分享图。
- 开户状态（20260810 已完成）：yiyan_sh(155) 与 yiyan_bj(188) 均经站内登录
  弹层短信表单登录成功——155 为未注册号码，走「验证即登录，未注册将自动
  创建百度账号」流程新建账号（弹层 tooltip「立即注册」确认后放开发送）；
  OTP 收件链路 20260809 起已修复（``tools/otp_wait.py`` 直取）。双实例
  batch live 各一题 ok（live_valid，登录态墙特征零误报）。
  注意：百度侧短信推送可能丢失 SIM 槽位信息而落 ``otp_inbox/unrouted.json``
  （188 的【百度】码实证）——otp_wait 按手机号查不到时查 unrouted 兜底。

拟人化口径（2026-08-06 起，与豆包同构——背景：豆包侧 picker 连点+秒发被行为
风控稳定识别出 wall_captcha，而人工同账号同代理发送无验证码——自动化交互序列
本身即指纹）：

- 输入：composer 正文一律 ``human_like.human_type`` 逐字真实键盘事件
  （40-140ms 抖动 + 标点/空格后 15% 概率 250-800ms 停顿），绝不 insert_text/fill。
- 点击：所有业务点击（发送按钮、弹层清理、「新对话」）一律
  ``human_like.human_click``——贝塞尔移动 + 到位悬停 + 元素内随机偏移点击。
- 节奏：页面就绪 → 端详 0.6-1.8s → 点输入框 → 逐字输入 → 通读 0.5-1.5s → 发送。
- 机器路径不动：DOM 流观测、提交确认轮询、墙识别、截图等纯观测逻辑不产生
  输入事件，不构成行为指纹，保持 20260727 live 校准语义原样。

新会话纪律（每个问题必须落在全新会话，绝不在旧会话里追问）：

- await_input 后 ``_ensure_fresh_chat`` 验证：composer 为空且页面无已存在
  答案节点 → 放行；否则优先点「新对话」按钮，仍不新则导航回聊天首页兜底；
  最终验证不过 → ``_IncompleteCapture`` 诚实失败（可重试），绝不静默沿用旧会话。
- 2026-08-07 headed live 校准（CDP attach yiyan-sh 常驻浏览器，wenxin.baidu.com
  旧会话计数 1→0 实证）：「开启新对话」入口 = 侧边栏顶部文本元素（div 非
  button、无 aria-label）——Playwright text 精确匹配实证命中切新，已提为首选；
  其余通用候选兜底，仍全失效时导航回首页兜底（行为仍正确，只是少一次点击）。

优雅关闭（profile 崩溃标记根治，与豆包同款）：

- 根因：persistent context 的浏览器进程若未经 ``context.close()`` 走完正常退出，
  Chromium 不会把 profile ``Preferences`` 里的 ``profile.exit_type`` 写回
  ``"Normal"``，下次启动即弹「Restore pages?」。
- 对策：launch 路径退出由 ``platform_browser`` finally 统一 ``context.close()``
  （成功/墙/超时/异常全路径覆盖）；``_clean_profile_crash_state``（复用豆包同
  名实现）在启动前与 close 后各幂等执行一次。attach 路径（``GEO_YIYAN_CDP_URL``
  非空）不关 context、不清理 profile——浏览器与登录态归 supervisor 所有，
  退出只断开 CDP 连接（resident_browser 契约）。

run 级会话复用（2026-08-06 起，``collect_yiyan_batch``，治本反风控）：

- 背景（豆包生产实证）：拟人化后每个 run 的第一问永远成功、第二问在发送瞬间
  必撞验证码——风控抓的是「冷启动即发问+短时间再次冷启动」的会话结构，真人
  是在同一浏览器窗口里连续聊天的。
- 结构：一个 run 的文心任务在同一个常驻浏览器会话/同一标签页里顺序完成
  （一次 launch/attach，绝不每题冷启全新 Chromium）。每题：fresh_chat 纪律
  （点「新对话」或导航兜底，绝不重开浏览器）→ 拟人输入/发送 → DOM 流观测/
  证据落盘（与 per-task 共用 ``_collect_one`` 主体，绝无两套复制）→ 「阅读
  停顿」（human_like.human_read_pause：滚动 2-5 次 + 停留 8-25s 抖动——题间
  天然间隔，也产出真实浏览信号；最后一题同样停留）→ 下一题。
- 失败语义：题级墙/incomplete → 该题诚实记失败、后续题 aborted
  （aborted_after_failure，零浏览器交互——真人撞墙后会停下，不编造不硬闯）；
  结果列表与输入等长同序返回，绝不 raise 丢掉已完成题。session 建立阶段
  （launch/attach/navigate/登录墙）异常=一题未发：wall 类成全题 wall 结果，
  临时故障（_IncompleteCapture）raise 走 batch 级重试。仅配置类错误
  （adapter_not_configured/unsupported_mode）允许 raise。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

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
    capture_yiyan_official_share,
    write_share_link_manifest,
)
from workflows.activities.page_capture import capture_scoped_chat_tiles
from workflows.activities.raw_capture import dump_raw_evidence_refs, maybe_raw_capture
from workflows.activities.resident_browser import platform_browser
from workflows.activities.wall_lexicon import classify_answer_text, detect_muted_banner

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
# deep_think（深度思考）思考+作答远长于 normal——对齐豆包/DeepSeek 600s；
# workflow per-item 预算 15min 放得下。
_CHAT_TIMEOUT_DEEP_THINK_S = 600.0
# deep_think 阶段切换（思考→整理→作答）DOM 静默期长于 normal 的 2.5s 稳定窗，
# 放宽到 8s 防提前判完成截断答案（20260810 live 校准观察窗）。
_DEEP_THINK_QUIET_S = 8.0

# 深度思考 chip（20260810 live 校准，yiyan_bj 常驻浏览器实证）：composer 左侧
# 「深度思考」入口 = div.ci-model-button:has(div.internet-search-icon)；开=
# 容器 class 含 ci-model-button-active 且 data-ci-show-ext 的 is_open:"1"，
# 关=ci-model-button-inactive/is_open:"0"。chip 态账号级粘滞。
_DEEP_THINK_CHIP_SELECTOR = "div.ci-model-button:has(div.internet-search-icon)"
_DEEP_THINK_CHIP_STATE_JS = r"""() => {
  const btn = document.querySelector('div.ci-model-button:has(div.internet-search-icon)');
  if (!btn) return null;
  const cls = btn.className || '';
  const m = (btn.getAttribute('data-ci-show-ext') || '').match(/"is_open":"(\d)"/);
  return {
    active: cls.includes('ci-model-button-active'),
    inactive: cls.includes('ci-model-button-inactive'),
    is_open: m ? m[1] : null,
  };
}"""

# 思考链块（20260810 live 校准）：答案 generate 块内 div.ai-thinking-steps，
# header「深度思考完成/深度思考中」，逐 step 文本在叶 main 元素。
_THINKING_BLOCK_SELECTOR = "div.ai-thinking-steps"
# 正文抽取剔除思考块与信源卡片：clone 后 remove，原 DOM 不动（截图证据仍含
# 原样）。div.cosd-note-list = deep_think 信源卡片列表（20260810 live 校准：
# 卡片 a[href] 已结构化进 citations，正文尾部不再重复其文本碎片）。
# 20260812 表格保结构（W3 表格碎片证据根治）：innerText 把 <table> 压成
# 制表符/换行序列、丢失行列对应（文心对比表「弱」案根因）；clone 内逐表改写为
# markdown 管道行（首行视作表头补分隔行），单元格内换行压空格、| 转义。
# live 实证：文心答案容器用真 <table>（wenxin 会话页 6×36 测绘平台对比表探针）。
_STRIP_THINKING_JS = r"""(el) => {
  const c = el.cloneNode(true);
  // Current Wenxin builds hash the thinking class suffix. data-no-share-select
  // is also attached to non-answer thinking/source UI in the rendered answer.
  for (const t of c.querySelectorAll(
    'div.ai-thinking-steps, [class*="thinking-steps"], [data-no-share-select]'
  )) t.remove();
  for (const t of c.querySelectorAll('div.cosd-note-list, [class*="note-list"]')) t.remove();
  for (const t of c.querySelectorAll('table')) {
    const rows = [];
    let cols = 0;
    for (const tr of t.querySelectorAll('tr')) {
      const cells = Array.from(tr.querySelectorAll('th,td')).map((td) =>
        (td.innerText || '').trim().replace(/\s+/g, ' ').replaceAll('|', '\\|')
      );
      if (!cells.length) continue;
      cols = Math.max(cols, cells.length);
      rows.push(cells);
    }
    if (!rows.length) { t.remove(); continue; }
    const lines = rows.map((r) =>
      '| ' + r.concat(Array(cols - r.length).fill('')).join(' | ') + ' |'
    );
    lines.splice(1, 0, '| ' + Array(cols).fill('---').join(' | ') + ' |');
    const pre = document.createElement('pre');
    // 首尾补换行：detached clone 的 innerText 不会在 <pre> 前自动断行，
    // 否则表头行与前序文本（如「表格」标签）粘连失去 | 前缀（20260812 live 实证）。
    pre.textContent = '\n' + lines.join('\n') + '\n';
    t.replaceWith(pre);
  }
  return c.innerText;
}"""

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

# 「新对话」入口（新会话纪律；命中第一个可见者即点，顺序即优先级）。
# 2026-08-07 headed live 校准（wenxin.baidu.com）：新建入口 = 侧边栏顶部
# 「开启新对话」文本元素（div 非 button、无 aria-label）——text 精确匹配实证
# 命中（消息计数 1→0）。其余候选兜底；全失效时 _ensure_fresh_chat 自动走导航
# 回首页兜底，行为仍正确。
_NEW_CHAT_SELECTORS: tuple[str, ...] = (
    'text="开启新对话"',
    "text=开启新对话",
    '[aria-label*="新对话"]',
    'button:has-text("新对话")',
    '[role="button"]:has-text("新对话")',
    'a:has-text("新对话")',
    'button:has-text("新建对话")',
    '[class*="new-chat"]',
    '[class*="newChat"]',
)

# 新会话验证：页面已存在答案节点计数（>0 = 旧会话/进行中的旧回答）。
# div.conversation-flow-answer-container 是 20260727 live 校准的答案容器（权威信号）；
# generate 块是其子块（双重计数无害——本探针只判零）。
_CHAT_MESSAGE_COUNT_JS = r"""() => {
  const sels = [
    'div.conversation-flow-answer-container',
    'div.chat-search-answer-generate'
  ];
  let n = 0;
  for (const s of sels) n += document.querySelectorAll(s).length;
  return n;
}"""

# 拟人化节奏区间（秒）——端详页面 / 发送前通读 / 新会话切换
_PACE_PAGE_READY_S = (0.6, 1.8)
_PACE_BEFORE_SEND_S = (0.5, 1.5)
_PACE_AFTER_NEW_CHAT_S = (0.6, 1.2)

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

# Runtime evidence is scoped to the one current Q&A.  Whole-page flattening used
# to pull the history rail and bottom composer into the long image and changed
# virtual/sticky layout.  This probe scrolls only #conversation-flow-container,
# captures the exact question plus generated answer blocks, and excludes answer
# suggestions/toolbars by selecting chat-search-answer-generate directly.
_YIYAN_CAPTURE_STATE_JS = r"""async (request) => {
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
  const scrollers = Array.from(document.querySelectorAll('#conversation-flow-container'))
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
  const qaRows = Array.from(scroller.querySelectorAll('.chat-qa-container'));
  if (qaRows.length !== 1) return fail(`qa_count:${qaRows.length}`);
  const qa = qaRows[0];
  const questions = qa.querySelectorAll('.conversation-flow-question-container');
  const answers = qa.querySelectorAll('div.chat-search-answer-generate');
  if (questions.length !== 1) return fail(`question_count:${questions.length}`);
  if (answers.length !== 1) return fail(`answer_count:${answers.length}`);
  const question = questions[0];
  const answer = answers[0];
  const expectedQuestion = normalizeText(request && request.expectedQuestion);
  if (!expectedQuestion) return fail('expected_question_missing');
  if (normalizeText(question.innerText) !== expectedQuestion) {
    return fail('question_text_mismatch');
  }
  if (!normalizeText(answer.innerText)) return fail('answer_text_empty');
  if ([question, answer].some((node) => node.querySelector(
    'textarea, input, [contenteditable="true"], .answer-ask-container, .answer-tips-wrapper'
  ))) return fail('excluded_ui_inside_message');
  const stickyTargets = Array.from(answer.querySelectorAll('*')).filter((el) => {
    const rect = el.getBoundingClientRect();
    return getComputedStyle(el).position === 'sticky' && rect.height > 1;
  });
  if (stickyTargets.length) return fail(`sticky_answer_node_count:${stickyTargets.length}`);

  const fingerprint = (node) => {
    const stableClone = node.cloneNode(true);
    // Wenxin mounts a viewport-only copy of the thinking header while scrolling.
    // It is absolute UI chrome (not answer content) and appears/disappears as the
    // pane crosses a threshold, so exclude it from the semantic text fingerprint.
    stableClone.querySelectorAll('.fixed-header-container').forEach((el) => el.remove());
    const text = normalizeText(stableClone.textContent);
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
  const captureX = Math.min(...blocks.map((block) => block.left));
  const captureRight = Math.max(...blocks.map((block) => block.right));
  const captureWidth = captureRight - captureX;
  const topBars = Array.from(document.querySelectorAll('[class*="chat-top-bar-new"]'))
    .filter(visible);
  if (topBars.length !== 1) return fail(`top_bar_count:${topBars.length}`);
  const bottomButtons = Array.from(document.querySelectorAll('.cs-scroll-to-bottom-btn'));
  if (bottomButtons.length > 1) {
    return fail(`scroll_to_bottom_count:${bottomButtons.length}`);
  }
  // The top navigation and the circular scroll-to-bottom control are overlaid on
  // the inner scroller.  Screenshot only the unobscured band between them; both
  // controls otherwise get copied into every stitched tile and cover answer text.
  const topBarRect = topBars[0].getBoundingClientRect();
  const bottomButtonRect = bottomButtons[0]?.getBoundingClientRect();
  const captureTopInset = Math.max(0, topBarRect.bottom - scrollerRect.top);
  // The control is intentionally display:none when already at the bottom, so its
  // bounding box cannot define a stable band on every tile.  Its live layout is a
  // 38px circle in the bottom 58px. Wenxin also paints a scroll-edge fade above
  // it; reserve the bottom 116px so seams never contain that fade or the control.
  const captureHeight = scrollerRect.height - 116;
  if (bottomButtonRect && bottomButtonRect.width > 0 && bottomButtonRect.height > 0
      && bottomButtonRect.top < scrollerRect.top + captureHeight) {
    return fail('scroll_to_bottom_inside_capture_band');
  }
  const floatingThinkingHeaders = Array.from(
    answer.querySelectorAll('.fixed-header-container')
  ).filter(visible);
  if (floatingThinkingHeaders.some((el) => (
    el.getBoundingClientRect().bottom > scrollerRect.top + captureTopInset
  ))) return fail('floating_thinking_header_inside_capture_band');
  const maxScroll = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
  if (captureX < 0 || captureRight > window.innerWidth + 1 || captureWidth <= 0
      || captureTopInset < 0 || captureHeight - captureTopInset < 200) {
    return fail('capture_band_invalid');
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
    capture_top_inset: captureTopInset,
    capture_height: captureHeight,
    blocks,
  };
}"""


_YIYAN_CAPTURE_RESTORE_JS = r"""async (scrollTop) => {
  const scrollers = Array.from(document.querySelectorAll('#conversation-flow-container'));
  if (scrollers.length !== 1) {
    return {ok: false, error: `restore_scroller_count:${scrollers.length}`};
  }
  const scroller = scrollers[0];
  scroller.scrollTop = Number(scrollTop);
  await new Promise((resolve) => requestAnimationFrame(
    () => requestAnimationFrame(resolve)
  ));
  for (const el of document.querySelectorAll('[data-yiyan-capture-unsticky]')) {
    el.style.removeProperty('position');
    el.removeAttribute('data-yiyan-capture-unsticky');
  }
  const injected = document.getElementById('yiyan-capture-chrome-hide');
  if (injected) injected.remove();
  return {
    ok: Math.abs(scroller.scrollTop - Number(scrollTop)) <= 1,
    actual_scroll_top: scroller.scrollTop,
  };
}"""

# 表格答案的 markdown 表头工具条（.cosd-markdown-table-header）是 position:sticky，
# 分片滚动时随视口吸附位置漂移，拼接会重影/覆盖正文。采集期间临时改 static（由
# _YIYAN_CAPTURE_RESTORE_JS 统一还原），探针的未知 sticky 节点检查保持 fail-closed。
# 浮动思考头（.fixed-header-container）是滚动时按需挂载的视口副本 UI chrome，
# 非答案内容（fingerprint 本就排除）；用注入样式整采隐藏，滚动中新挂载的副本同样
# 生效，restore 时移除样式节点统一还原。
_YIYAN_CAPTURE_UNSTICKY_JS = r"""() => {
  const STYLE_ID = 'yiyan-capture-chrome-hide';
  if (!document.getElementById(STYLE_ID)) {
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent =
      'div.chat-search-answer-generate .fixed-header-container{display:none!important}';
    document.head.appendChild(style);
  }
  const headers = Array.from(document.querySelectorAll(
    'div.chat-search-answer-generate .cosd-markdown-table-header'
  ));
  let marked = 0;
  for (const el of headers) {
    if (el.hasAttribute('data-yiyan-capture-unsticky')) continue;
    if (getComputedStyle(el).position !== 'sticky') continue;
    el.setAttribute('data-yiyan-capture-unsticky', '1');
    el.style.position = 'static';
    marked += 1;
  }
  return {ok: true, marked};
}"""


# ---------------------------------------------------------------------------
# 配置 / 错误类型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class YiyanAdapterConfig:
    """env 配置。proxy_url 原文只在启动浏览器时使用，绝不落日志/payload。

    ``browser_key``（2026-08-09 起，浏览器矩阵化）：attach/互斥锁/fence 用的
    opaque "platform"——batch 路径由 browser_router 解析为常驻实例键
    （``yiyan_sh`` 等）；缺省平台 slug（per-task 老路径/测试行为不变）。"""

    profile_dir: Path
    proxy_url: str | None
    evidence_dir: Path
    headless: bool
    browser_key: str = "yiyan"

    @classmethod
    def from_env(cls, *, proxy_url_override: str | None = None) -> YiyanAdapterConfig:
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
    """深度思考 chip 无法确认到目标态（non_retryable；绝不按错误口径采）。"""

    def __init__(self, message: str, evidence_path: Path | None = None) -> None:
        super().__init__(message)
        self.evidence_path = evidence_path
        self.evidence_refs: list[CollectionEvidenceRef] = []


class _ModeUnconfirmed(RuntimeError):
    """deep_think 请求已下达（chip 后置校验通过）但无 ai-thinking-steps DOM
    思考步证据——诚实失败（non_retryable）：绝不把无思考证据的答案按
    deep_think 落 completed（对照豆包 2026-08-14 口径：配额耗尽后平台静默
    回退非思考模式的「正常答案」曾是 2026-08-13 事故源头之一）。"""

    def __init__(self, message: str, evidence_path: Path | None = None) -> None:
        super().__init__(message)
        self.evidence_path = evidence_path
        self.evidence_refs: list[CollectionEvidenceRef] = []


def _deep_think_chip_state(page: Any) -> bool | None:
    """读深度思考 chip 当前态：True=开 / False=关 / None=不可观测（不猜）。"""
    try:
        state = page.evaluate(_DEEP_THINK_CHIP_STATE_JS)
    except Exception:
        return None
    if not isinstance(state, dict):
        return None
    active = state.get("active") is True and state.get("is_open") == "1"
    inactive = state.get("inactive") is True and state.get("is_open") == "0"
    if active and not inactive:
        return True
    if inactive and not active:
        return False
    return None  # class 与 is_open 不一致 → 不可观测，诚实 None


def _ensure_deep_think(
    page: Any,
    rng: random.Random,
    *,
    engaged: bool,
    shot: Callable[[str], Path | None],
    mouse_pos: tuple[float, float] | None = None,
) -> tuple[float, float] | None:
    """把深度思考 chip 确保到目标态（幂等：已在目标态零点击）。

    chip 态账号级粘滞——每题发送前显式确保，防止上一 run 的残留态污染本题
    口径。最多两次点击尝试；读不回目标态 → _ModeToggleFailed（存证截图）。
    """
    for _attempt in range(2):
        state = _deep_think_chip_state(page)
        if state is not None and state == engaged:
            return mouse_pos
        try:
            chip = page.locator(_DEEP_THINK_CHIP_SELECTOR).first
            clicked_at = human_click(chip, page, rng, start=mouse_pos)
            if clicked_at is not None:
                mouse_pos = clicked_at
        except Exception:
            pass
        page.wait_for_timeout(800)
        state = _deep_think_chip_state(page)
        if state is not None and state == engaged:
            return mouse_pos
    raise _ModeToggleFailed(
        f"deep_think chip could not be confirmed to target state engaged={engaged}",
        shot("mode_toggle"),
    )


def _extract_thinking_text(page: Any) -> str:
    """抽深度思考链原文（最后一个答案容器内 div.ai-thinking-steps 的叶 main 文本）。

    零合成：块不存在/无文本即空串。叶 main = 不含子 main 的 main（思考步容器
    与内层展开区会嵌套重复，取叶去重）。
    """
    try:
        text = page.evaluate(
            r"""() => {
              const sel = 'div.conversation-flow-answer-container';
              const containers = document.querySelectorAll(sel);
              if (!containers.length) return '';
              const last = containers[containers.length - 1];
              const think = last.querySelector(
                'div.ai-thinking-steps, [class*="thinking-steps"]');
              if (!think) return '';
              const mains = Array.from(think.querySelectorAll('main'))
                .filter((m) => !m.querySelector('main'));
              const parts = mains.map((m) => (m.innerText || '').trim()).filter(Boolean);
              if (parts.length) return parts.join('\n\n');
              return (think.innerText || '').trim();
            }"""
        )
    except Exception:
        return ""
    return str(text or "").strip()


def _build_yiyan_trace(
    thinking_text: str,
    *,
    deep_think_active: bool,
    references: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """思考链 → trace record（kind="sse" 证据内容，词表对齐豆包/DeepSeek）。

    transport="dom" 如实标注：文心 SW 中转抓不到 completion SSE（20260727
    probe_transport 实证），思考链来自 DOM 实渲染，与 SSE 来源差异不掩盖。
    """
    thinking_chain: list[dict[str, Any]] = []
    if thinking_text:
        thinking_chain.append({"kind": "reasoning", "text": thinking_text})
    refs = list(references or [])
    source_rows = [
        {
            "title": str(ref.get("title") or "未命名来源"),
            "url": ref.get("url"),
            "site": ref.get("sitename"),
            "rank": index,
            "summary": str(ref.get("summary") or ""),
        }
        for index, ref in enumerate(refs, 1)
    ]
    return {
        "engine": "yiyan",
        "transport": "dom",
        "source_taxonomy_version": 2,
        "deep_think_active": deep_think_active,
        "thinking_chain": thinking_chain,
        "search_blocks": (
            [{"scene": None, "queries": [], "summary": "", "results": source_rows}]
            if source_rows
            else []
        ),
        # Wenxin exposes its searched/reference rows but no explicit per-page
        # equivalent of DeepSeek TOOL_OPEN. Never infer actual opens from links.
        "opened_pages_observed": False,
        "opened_pages": [],
        "answer_reference_pages": source_rows,
    }


@dataclass
class CollectedAnswer:
    answer_text: str
    references: list[dict[str, Any]]
    screenshot_path: Path
    meta: dict[str, Any] = field(default_factory=dict)
    # 思考链 trace 证据路径（kind="sse"，transport="dom"；无思考/写盘失败=None 诚实缺省）
    trace_path: Path | None = None
    # Runtime answer screenshot + official share image/link. The official share
    # image is preferred as screenshot_ref by _task_result_from_collected.
    evidence: list[CollectionEvidenceRef] = field(default_factory=list)
    # 原始流量证据 ref（2026-08-10 起：sse_raw/har；GEO_RAW_CAPTURE=0 或写盘
    # 失败为空——诚实缺省）。_task_result_from_collected 并入 evidence。
    raw_evidence: list[CollectionEvidenceRef] = field(default_factory=list)
    answer_evidence: CollectionEvidenceRef | None = None


@dataclass(frozen=True)
class YiyanBatchItemSpec:
    """batch 内单题输入（session 层）：查询/mode + 证据文件名片段。"""

    business_key: str
    query: str
    mode: str
    file_stem: str


@dataclass
class YiyanBatchItemOutcome:
    """batch 内单题结果（session 层）：ok 携带 CollectedAnswer；失败/未执行
    携带 error_type/error_message/可选存证截图路径。status 词表与
    CollectionBatchItemResult 对齐（ok/wall/incomplete/aborted）。"""

    business_key: str
    status: str
    answer: CollectedAnswer | None = None
    error_type: str | None = None
    error_message: str | None = None
    evidence_path: Path | None = None
    # 失败题的原始流量证据 ref（2026-08-10 起，raw/HAR，由题末异常对象携带
    # 而来）；aborted 题零浏览器交互，恒空。
    evidence: list[CollectionEvidenceRef] = field(default_factory=list)


class _BrowserSession(Protocol):
    """Playwright 交互隔离面：测试注入 fake，绝不启动真浏览器。"""

    def collect(
        self, query: str, on_stage: Callable[[str], None], *, mode: str = "normal"
    ) -> CollectedAnswer: ...

    def collect_batch(
        self, items: list[YiyanBatchItemSpec], on_stage: Callable[[str], None]
    ) -> list[YiyanBatchItemOutcome]: ...


SessionFactory = Callable[[YiyanAdapterConfig, Path, str], _BrowserSession]


def _noop_heartbeat(payload: dict[str, Any]) -> None:
    """activity 上下文之外的默认 heartbeat（测试/手工驱动时无副作用）。"""


# ---------------------------------------------------------------------------
# batch activity 入口与异步泵
# ---------------------------------------------------------------------------


@activity.defn(name="collect_yiyan_batch")
async def collect_yiyan_batch(batch: CollectionBatchInput) -> CollectionBatchResult:
    """文心一言 batch 采集注册实现（workers/main.py 门控注册由协调者统一接线）。

    整个 batch 在同一个常驻浏览器会话里顺序完成（run 级会话复用）；墙/失败
    诚实记录在 per-item 结果里（本 activity 不因墙类失败 raise），仅配置类
    错误（adapter_not_configured/unsupported_mode）raise。
    """
    try:
        attempt = activity.info().attempt
    except RuntimeError:
        attempt = 1
    # 不传 session_factory：与 run_yiyan_collection 的生产约定一致（dispatcher
    # 只传业务参数）——缺省 None 才走 to_thread 分支跑真实 sync 浏览器；显式传
    # _PlaywrightYiyanSession 会误判为注入 fake，在事件循环里直跑 sync API
    # （豆包 2026-08-06 batch 首航生产事故同款教训）。
    return await run_yiyan_batch(
        batch,
        heartbeat=activity.heartbeat,
        attempt=attempt,
    )


async def run_yiyan_batch(
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
    factory = session_factory or _PlaywrightYiyanSession
    beat = heartbeat or _noop_heartbeat
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
    config = YiyanAdapterConfig.from_env(proxy_url_override=proxy_url_override)
    if route is not None:
        config = replace(config, browser_key=route.instance_key)
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    batch_stem = f"batch-{_safe_stem(batch.run_pub_id)}-a{attempt}"
    specs = [
        YiyanBatchItemSpec(
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

    def _blocking() -> list[YiyanBatchItemOutcome]:
        session = factory(config, config.evidence_dir, batch_stem)

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
                beat(_heartbeat_payload())
                done, _pending = await asyncio.wait({thread}, timeout=_HEARTBEAT_INTERVAL_S)
                if done:
                    break
            outcomes = thread.result()
        else:
            beat(_heartbeat_payload())
            outcomes = _blocking()
    except _WallError as wall:
        # session 级墙（导航后登录墙）：一题未发，全题诚实记 wall。
        evidence_suffix = f"; evidence={wall.evidence_path}" if wall.evidence_path else ""
        bound.info("yiyan_batch_session_wall", wall_type=wall.wall_type, stage=progress["stage"])
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
        bound.info("yiyan_batch_session_mode_unconfirmed", stage=progress["stage"])
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
        bound.info("yiyan_batch_session_incomplete", reason=str(inc), stage=progress["stage"])
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
        "yiyan_batch_done",
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
    item: CollectionTaskInput, outcome: YiyanBatchItemOutcome
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
# per-task 异步泵（公开入口，故意不挂 @activity.defn——注册由 workers/main.py
# 门控完成；platform_registry dispatcher 只调 run_yiyan_collection）
# ---------------------------------------------------------------------------


async def run_yiyan_collection(
    item: CollectionTaskInput,
    *,
    session_factory: SessionFactory | None = None,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
    proxy_url_override: str | None = None,
    attempt: int = 1,
) -> CollectionTaskResult:
    """activity 核心：mode 门 → 配置门 → to_thread 跑浏览器 → 墙/结果映射。

    与 activity 上下文解耦（session_factory/heartbeat/attempt 注入），测试全程 mock
    浏览器层。session_factory 缺省 = 真 patchright 会话（worker 注册路径）。
    """
    if item.mode not in ("normal", "deep_think"):
        raise ApplicationError(
            f"unsupported mode: {item.mode!r} (expected 'normal' or 'deep_think')",
            type="unsupported_mode",
            non_retryable=True,
        )
    config = YiyanAdapterConfig.from_env(proxy_url_override=proxy_url_override)
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    uses_default_session = session_factory is None
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
        return session.collect(
            item.query, on_stage=lambda s: progress.__setitem__("stage", s), mode=item.mode
        )

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
        bound.info("yiyan_wall", wall_type=wall.wall_type, stage=progress["stage"])
        raise ApplicationError(
            f"{wall}{evidence}", type=wall.wall_type, non_retryable=True
        ) from wall
    except _ModeToggleFailed as toggle:
        evidence = f"; evidence={toggle.evidence_path}" if toggle.evidence_path else ""
        bound.info("yiyan_mode_toggle_failed", stage=progress["stage"])
        raise ApplicationError(
            f"{toggle}{evidence}", type="mode_toggle_failed", non_retryable=True
        ) from toggle
    except _ModeUnconfirmed as mu:
        # deep_think 无思考步证据（2026-08-14 起）：non_retryable 诚实失败，
        # 绝不把无思考证据的答案按 deep_think 落 completed。
        evidence = f"; evidence={mu.evidence_path}" if mu.evidence_path else ""
        bound.info("yiyan_mode_unconfirmed", stage=progress["stage"])
        raise ApplicationError(
            f"{mu}{evidence}", type="mode_unconfirmed", non_retryable=True
        ) from mu
    except _IncompleteCapture as inc:
        evidence = f"; evidence={inc.evidence_path}" if inc.evidence_path else ""
        bound.info("yiyan_capture_incomplete", reason=str(inc), stage=progress["stage"])
        raise ApplicationError(f"{inc}{evidence}", type="answer_capture_incomplete") from inc
    bound.info(
        "yiyan_collect_ok",
        answer_len=len(collected.answer_text),
        references=len(collected.references),
        stage=progress["stage"],
    )
    return _task_result_from_collected(item, collected)


def _task_result_from_collected(
    item: CollectionTaskInput, collected: CollectedAnswer
) -> CollectionTaskResult:
    """CollectedAnswer → CollectionTaskResult 映射（answer 组装/出界 DLP 自检）。
    run_yiyan_collection 与 batch per-item ok 映射共用。"""
    answer_text = _compose_answer_text(collected.answer_text, collected.references)
    # 结构化信源（W2 source_fetch 的唯一输入）：references 判形时已保证真实
    # http(s) URL；cited_text 无逐句引述可填 → None，transcript 口径诚实落
    # unverifiable。
    citations = [
        {
            "url": str(ref["url"]),
            "title": str(ref["title"]).strip() if ref.get("title") else None,
            "cited_text": None,
        }
        for ref in collected.references
        if isinstance(ref, dict)
        and isinstance(ref.get("url"), str)
        and ref["url"].startswith(("http://", "https://"))
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
    """文心一言网页采集的 sync Playwright 实现（persistent context / CDP attach）。

    单题（``collect``，per-task 老路径）与 run 级会话复用（``collect_batch``）
    共享同一套 per-item 主体 ``_collect_one``——绝不复制出两套：

    - ``collect``：一次会话、一题、关闭（老行为不变）；
    - ``collect_batch``：一次 launch/attach，N 题在同一常驻会话/同一标签页里
      顺序完成（真人在同一浏览器窗口里连续聊天——每题落在全新会话但绝不重开
      浏览器）；每题成功后做「阅读停顿」（拟人读完回答：滚动浏览 + 停留）；
      launch 路径结束统一 context.close()（platform_browser finally）+ 崩溃
      标记清理；attach 路径退出只断开 CDP。

    batch 失败语义（2026-08-14 细化，对齐豆包）：题级失败转 outcome，结果列表
    与输入等长同序；连坐按失败类型分级——真墙（captcha/login/send/muted）=
    账号级阻断，后续题全 aborted（零浏览器交互：真人撞墙后会停下，不编造不
    硬闯）；wall_quota=配额按 (账号×mode) 计费，只连坐同 mode 余题；
    wall_refusal/incomplete/toggle 失败/mode_unconfirmed=题级 flake 或内容
    失败，不连坐，本题诚实失败后续跑。session 建立阶段（launch/attach/
    navigate/登录墙检查）的异常原样逃出，由 activity 层按 session 级语义处理
    （一题未发）。
    """

    def __init__(self, config: YiyanAdapterConfig, evidence_dir: Path, file_stem: str) -> None:
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
        spec = YiyanBatchItemSpec(
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
        self, items: list[YiyanBatchItemSpec], on_stage: Callable[[str], None]
    ) -> list[YiyanBatchItemOutcome]:
        outcomes: list[YiyanBatchItemOutcome] = []
        # 配额墙按 (账号×mode) 计费（2026-08-14 起，对齐豆包）：wall_quota 只
        # 连坐同 mode 余题——记录已撞配额的 mode，轮到其余题位次时零浏览器交互
        # 追加 aborted 占位（结果列表与输入等长同序的契约不变）。
        quota_blocked: dict[str, YiyanBatchItemSpec] = {}
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
                        # 照跑（深度思考配额耗尽 ≠ 普通模式不可用）。
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
                    # deep_think 无思考步 DOM 证据（2026-08-14 起 non_retryable
                    # 诚实失败）：题级失败不连坐（与 toggle 失败同哲学），余题照跑。
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
                    YiyanBatchItemOutcome(
                        business_key=spec.business_key, status="ok", answer=answer
                    )
                )
                # 阅读停顿：拟人读完回答（滚动浏览 + 停留 8-25s 抖动）——题间天然
                # 间隔，也产出真实浏览信号；最后一题同样停留（真人读完才关浏览器）。
                pause_s = self._reading_pause(page)
                log.info(
                    "yiyan_read_pause",
                    business_key=spec.business_key,
                    seconds=round(pause_s, 2),
                )
        return outcomes

    @staticmethod
    def _failure_outcome(
        spec: YiyanBatchItemSpec,
        status: str,
        error_type: str,
        exc: _WallError | _IncompleteCapture | _ModeToggleFailed | _ModeUnconfirmed,
    ) -> YiyanBatchItemOutcome:
        return YiyanBatchItemOutcome(
            business_key=spec.business_key,
            status=status,
            error_type=error_type,
            error_message=str(exc),
            evidence_path=exc.evidence_path,
            evidence=list(exc.evidence_refs),
        )

    @staticmethod
    def _aborted_outcome(
        spec: YiyanBatchItemSpec,
        failed_spec: YiyanBatchItemSpec,
        error_type: str | None,
        *,
        batch_stopped: bool = True,
    ) -> YiyanBatchItemOutcome:
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
        return YiyanBatchItemOutcome(
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

        ``GEO_YIYAN_CDP_URL`` 非空 → platform_browser connect_over_cdp attach
        （退出只断开，不关 context 不清理 profile——归 supervisor）；否则回退
        launch_persistent_context（退出由 platform_browser finally 统一 close，
        close 后 _clean_profile_crash_state 兜底清理崩溃标记）。
        """
        # 延迟导入：模块加载不硬依赖浏览器驱动（worker 未装依赖时仍可注册 fail-closed 实现）。
        # 驱动首选 patchright（旧链生产同款反检测补丁版）；vanilla playwright 仅作开发兜底。
        driver, sync_playwright, PWTimeout = load_sync_browser_driver()

        on_stage("browser_launch")
        with sync_playwright() as pw:

            def _launch() -> tuple[Any, Any]:
                # 启动前愈合前任进程的崩溃标记（activity 取消/SIGKILL 会绕过正常
                # close → Chromium 未写回 exit_type=Normal → 下次启动弹
                # 「Restore pages?」）。仅 launch 路径：attach 路径 profile 归
                # supervisor 所有，绝不动。幂等纯文件操作，失败不阻塞启动。
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
                with platform_browser(pw, platform=self._config.browser_key, launch=_launch) as (
                    context,
                    page,
                    resident,
                ):
                    on_stage("navigate")
                    try:
                        page.goto(_CHAT_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
                    except PWTimeout:
                        page.goto(_CHAT_URL, wait_until="commit", timeout=_NAV_TIMEOUT_MS)
                    page.wait_for_timeout(6_000)  # SPA + 反爬 JS 挂载（旧链同款 settle）
                    _try_close_overlays(page, self._rng)
                    if _detect_login_wall(page):
                        raise _WallError(
                            "wall_login_required",
                            "yiyan login wall detected right after navigation",
                            self._shot(page, "login"),
                        )
                    yield context, page, PWTimeout, driver
            finally:
                if not resident:
                    # launch 路径：context.close() 由 platform_browser finally 负责
                    # （全路径覆盖）；close 后兜底清理崩溃标记（覆盖 close 期竞态）。
                    # attach 路径不做这两件事（契约管断开，profile 归 supervisor）。
                    try:
                        _clean_profile_crash_state(self._config.profile_dir)
                    except Exception as exc:
                        log.warning(
                            "yiyan_profile_crash_clean_failed",
                            business_key=self._file_stem,
                            error=f"{type(exc).__name__}: {exc}",
                        )

    def _collect_one(
        self,
        context: Any,
        page: Any,
        spec: YiyanBatchItemSpec,
        on_stage: Callable[[str], None],
        *,
        pw_timeout: type[Exception],
        driver: str,
    ) -> CollectedAnswer:
        """单题入口：原始流量 capture 生命周期（2026-08-10 起）+ 委托
        ``_collect_one_dom`` 跑 DOM 观测主体。per-task 单题与 batch 每题共用。

        文心流观测走 DOM（SW 中转）；raw capture 挂 page 级独立 CDP session——
        completion 流量可能经 ServiceWorker 中转而不可见：看不到时 sse_raw
        诚实缺省（None 不出证据）、HAR 有什么算什么；capture 全程零请求时
        log warning（live 复核用）。``pw_timeout`` 仍未被使用（与豆包同构
        签名保留，未来传输层校准升级不改调用形态）。
        """
        del pw_timeout
        raw = maybe_raw_capture(
            context,
            page,
            body_url_hints=("yiyan.baidu.com",),
            creator="geo-yiyan-adapter",
        )
        try:
            answer = self._collect_one_dom(page, spec, on_stage, driver=driver)
        except (_WallError, _IncompleteCapture, _ModeToggleFailed, _ModeUnconfirmed) as exc:
            # 失败题同样留 raw/HAR（题末先 dump 后 detach）：ref 挂异常对象，经
            # _failure_outcome → 失败 result.evidence → persist 层进 CAS。
            exc.evidence_refs = dump_raw_evidence_refs(
                raw,
                self._evidence_dir,
                spec.file_stem,
                source_url=_CHAT_URL,
                warn_tag="yiyan",
            )
            raise
        else:
            answer.raw_evidence = dump_raw_evidence_refs(
                raw,
                self._evidence_dir,
                spec.file_stem,
                source_url=_CHAT_URL,
                warn_tag="yiyan",
            )
            return answer
        finally:
            if raw is not None:
                raw.detach()

    def _collect_one_dom(
        self,
        page: Any,
        spec: YiyanBatchItemSpec,
        on_stage: Callable[[str], None],
        *,
        driver: str,
    ) -> CollectedAnswer:
        """单题主体：await_input → fresh_chat → 拟人输入/发送 → DOM 流观测/
        证据落盘（文心流观测走 DOM，无既有 CDP capture）。raw/HAR 留痕由
        ``_collect_one`` 包装层负责。"""

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
            muted = detect_muted_banner("yiyan", _read_page_text(page))
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

        # 深度思考 chip（20260810 起）：发送前显式确保目标态——chip 态账号级
        # 粘滞，normal 也要显式确保关，防上一 run 残留态污染本题口径。
        # 确认不了 → _ModeToggleFailed（non_retryable），绝不按错误口径采。
        on_stage("mode_toggle")
        self._mouse_pos = _ensure_deep_think(
            page,
            self._rng,
            engaged=(spec.mode == "deep_think"),
            shot=_shot,
            mouse_pos=self._mouse_pos,
        )

        on_stage("typing")
        # 页面就绪：真人先端详一眼再动手（零停顿直点输入框是机器人指纹）。
        _pace(*_PACE_PAGE_READY_S)
        # SPA settle 后可能异步弹层（输入框仍 visible 但弹层会截获发送按钮，
        # 表现为 composer 一直不清空）——await_input 后再收一次，覆盖迟到弹层。
        _try_close_overlays(page, self._rng)
        # 点输入框聚焦（贝塞尔移动 + 悬停 + 框内随机偏移点击）。human_click
        # 拿不到布局时内部回退原生 click；仍失败则原样抛出=诚实失败。
        clicked_at = human_click(input_loc, page, self._rng, start=self._mouse_pos)
        if clicked_at is not None:
            self._mouse_pos = clicked_at
        human_type(input_loc, spec.query, self._rng)
        # 发送前通读一遍（原实现 type 后固定 800ms 即发送=秒发指纹）。
        _pace(*_PACE_BEFORE_SEND_S)

        submit = _submit_and_confirm(page, input_loc, self._rng, pace=_pace, start=self._mouse_pos)
        if not submit.get("submitted"):
            # 发送被吞时优先识别登录墙（未登录点发送会弹 pass 登录层，
            # 20260727 live 实测）——比笼统 wall_send 更诚实
            if _detect_login_wall(page):
                raise _WallError(
                    "wall_login_required",
                    "login wall surfaced on send (composer not cleared, pass login dialog visible)",
                    _shot("login"),
                )
            raise _WallError(
                "wall_send",
                "send-not-accepted: composer still populated after "
                f"{submit.get('attempts', '?')} send attempts (submission swallowed)",
                _shot("send_wall"),
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
                    _shot("captcha"),
                )
            if _dom_stream_started(page) and time.monotonic() - challenge_start >= 3.5:
                break
            page.wait_for_timeout(500)

        on_stage("await_stream")
        meta = _wait_dom_stream(
            page,
            appearance_timeout_s=20.0,
            timeout_s=(
                _CHAT_TIMEOUT_DEEP_THINK_S if spec.mode == "deep_think" else _CHAT_TIMEOUT_S
            ),
            quiet_s=_DEEP_THINK_QUIET_S if spec.mode == "deep_think" else 2.5,
        )
        answer_text = ""
        references: list[dict[str, Any]] = []
        thinking_text = ""
        if meta.get("found"):
            answer_text = _extract_response_text(page, spec.query)
            references = _extract_references(page)
            if spec.mode == "deep_think":
                thinking_text = _extract_thinking_text(page)
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
                "send-accepted-no-stream: composer cleared (submission accepted) "
                "but no answer container appeared within timeout — likely "
                "content-filter or silent server-side drop",
                _shot("no_stream"),
            )
        if not meta.get("finished"):
            raise _IncompleteCapture(
                "stream-open-at-timeout: answer still generating after "
                f"budget ({meta.get('bytes_received', 0)} chars observed) — answer "
                "would be truncated; failing honestly",
                _shot("truncated"),
            )
        if not answer_text:
            raise _IncompleteCapture(
                "answer-empty-after-finished-stream: DOM extraction produced no "
                "answer text despite finished stream",
                _shot("empty_answer"),
            )

        # 答案验收门（2026-08-14 起，词表唯一真源 wall_lexicon，对齐豆包）：
        # 答案文本定稿后、返回 ok 之前——平台提示文案（配额耗尽/禁言/拒答
        # 模板）被当作答案采回时在此拦截，抛 _WallError 走既有墙管道（batch
        # 连坐语义按 wall_type 细化，见 collect_batch docstring）。batch 与
        # per-task 单题共用本路径，两路都盖。
        verdict = classify_answer_text("yiyan", answer_text)
        if verdict is not None:
            raise _WallError(
                verdict.wall_type,
                _wall_verdict_message(verdict, answer_text),
                _shot("answer_wall"),
            )

        on_stage("screenshot")
        shot_path = self._evidence_dir / f"{spec.file_stem}.png"
        try:
            # 表格答案的 sticky 表头工具条先降级为 static（restore 脚本统一还原），
            # 否则分片拼接会重影；未知 sticky 节点仍由探针 fail-closed。
            page.evaluate(_YIYAN_CAPTURE_UNSTICKY_JS)
            capture_scoped_chat_tiles(
                page,
                shot_path,
                probe_script=_YIYAN_CAPTURE_STATE_JS,
                restore_script=_YIYAN_CAPTURE_RESTORE_JS,
                expected_question=spec.query,
                method="yiyan_scoped_message_tiles",
            )
        except Exception as exc:
            raise _IncompleteCapture(
                f"evidence-screenshot-failed: {type(exc).__name__}: {exc}",
                _shot("screenshot"),
            ) from exc
        if not shot_path.exists():
            raise _IncompleteCapture("evidence-screenshot-failed: no file written")
        # 思考链 trace 落盘进证据链（kind="sse"，transport="dom"）。写盘失败不
        # 拖垮已成功的采集——如实 warning 且不出该证据（绝不出残缺/编造证据）。
        # deep_think_active 以实际抽到思考步为准（证据为正才标 true；chip 已
        # 确保但思考步缺失时如实 false，对照元宝同日口径）。
        trace_path: Path | None = None
        if thinking_text or references:
            trace_candidate = self._evidence_dir / f"{spec.file_stem}-sse-trace.json"
            try:
                trace_candidate.write_text(
                    json.dumps(
                        _build_yiyan_trace(
                            thinking_text,
                            deep_think_active=bool(thinking_text),
                            references=references,
                        ),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
                trace_path = trace_candidate
            except Exception:
                log.warning(
                    "yiyan_thinking_trace_persist_failed",
                    file_stem=spec.file_stem,
                    exc_info=True,
                )
        # mode 证据升级（2026-08-14 起，对齐豆包，warning-only → non_retryable
        # 诚实失败）：trace 已先落盘取证。请求 deep_think 而无 ai-thinking-steps
        # 思考步证据 = 平台静默回退非思考模式的嫌疑答案，绝不落 completed
        # （2026-08-13 事故教训：配额耗尽后的回退答案曾被当 deep_think 采回）。
        if spec.mode == "deep_think" and not thinking_text:
            raise _ModeUnconfirmed(
                "deep_think requested and chip confirmed, but no ai-thinking-steps "
                "thinking evidence found in DOM — refusing to record a "
                "normal-evidence answer as deep_think",
                _shot("mode_unconfirmed"),
            )
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
            excluded_selectors=("div.ai-thinking-steps",),
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

        on_stage("share_export")
        share_image_path = self._evidence_dir / f"{spec.file_stem}-share.png"

        def _share_click(locator: Any) -> None:
            clicked_at = human_click(locator, page, self._rng, start=self._mouse_pos)
            if clicked_at is not None:
                self._mouse_pos = clicked_at

        try:
            share = capture_yiyan_official_share(
                page,
                share_image_path,
                click=_share_click,
            )
            share_link_path = self._evidence_dir / f"{spec.file_stem}-share-link.json"
            write_share_link_manifest(
                share_link_path,
                share_url=share.share_url,
                platform="yiyan",
                channel="clipboard",
            )
        except (OfficialShareExportError, OSError) as exc:
            raise _IncompleteCapture(
                "official-share-export-incomplete: Wenxin must provide both its "
                f"share-card PNG and public share URL ({type(exc).__name__}: {exc})",
                _shot("share_export"),
            ) from exc
        except Exception as exc:
            raise _IncompleteCapture(
                "official-share-export-incomplete: unexpected Wenxin share UI failure "
                f"({type(exc).__name__}: {exc})",
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
        return CollectedAnswer(
            answer_text=answer_text,
            references=references,
            screenshot_path=shot_path,
            meta={
                "stream": meta,
                "transport": "dom-observed (sw-intercepted, 20260727 calibrated)",
                "driver": driver,
                "mode": {
                    "requested": spec.mode,
                    "deep_think_chip_engaged": spec.mode == "deep_think",
                    "thinking_captured": bool(thinking_text),
                },
            },
            trace_path=trace_path,
            evidence=evidence,
            answer_evidence=answer_evidence,
        )

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
        return bool(page.locator(_ASSISTANT_SELECTORS[0]).first.is_visible(timeout=200))
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


def _try_close_overlays(page: Any, rng: random.Random) -> None:
    """best-effort 关 cookie 横幅/「我知道了」等遮罩（拟人化点击）。

    先 Escape + count/visible 粗筛（纯观测），只有真实存在的遮罩才 human_click——
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
        'button:has-text("同意")',
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


def _fresh_chat_ok(page: Any, input_loc: Any) -> bool:
    """新会话 ground truth：composer 为空 且 页面无已存在答案节点。

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
        loc = page.locator('[data-yiyan-send="true"]').first
        human_click(loc, page, rng, start=start)
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
    （20260727 live 校准：风控间歇性吞点击——composer 不清空）。"""
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
    """DOM 抽取：先助手气泡选择器，再按「query 最后出现位置之后」切正文。

    20260810 起经 JS clone 剔除 ``div.ai-thinking-steps`` 思考块（deep_think
    模式思考链在 generate 块内部——不剔除会混入正文；原 DOM 不动，截图证据
    仍含思考区）。JS 失败回退 inner_text（旧行为）。
    """
    for sel in _ASSISTANT_SELECTORS:
        try:
            elements = page.locator(sel).all()
            if not elements:
                continue
            try:
                text = str(elements[-1].evaluate(_STRIP_THINKING_JS) or "")
            except Exception:
                text = ""
            if not text.strip():
                # JS 剔除路径不可用/为空 → 回退 inner_text（20260727 旧行为）
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


def _extract_references(page: Any) -> list[dict[str, Any]]:
    """参考来源 DOM best-effort 抽取：含「参考/来源」字样容器内的 http 锚点。

    首选 ``div.cosd-note-card a[href^="http"]``（20260810 deep_think live 校准：
    信源卡片，锚文本为「标题\n站点名」两行，逐行拆开）；其余容器为兜底。
    零合成：抽不到即空列表，绝不从正文猜链接。
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    containers = (
        'div.cosd-note-card a[href^="http"]',
        'div.cosd-note-list a[href^="http"]',
        '[class*="note-card"] a[href^="http"]',
        '[class*="note-list"] a[href^="http"]',
        '[class*="reference"] a[href^="http"]',
        '[class*="source"] a[href^="http"]',
        'div.chat-search-answer-generate a[href^="http"]',
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
                raw_text = (a.inner_text(timeout=500) or "").strip()
            except Exception:
                continue
            if not url.startswith("http"):
                continue
            key = url.split("?")[0]
            if key in seen:
                continue
            seen.add(key)
            lines = [ln.strip() for ln in raw_text.split("\n") if ln.strip()]
            title = lines[0] if lines else None
            sitename = lines[-1] if len(lines) >= 2 else None
            out.append({"url": url, "title": title, "sitename": sitename})
        if out:
            break
    return out


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
