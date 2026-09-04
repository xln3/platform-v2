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

- 两种 mode（20260810 起，对照 deepseek 同日用户拍板口径）：``normal`` =
  账号默认模型族（Hy3）+ 深度思考**关**；``deep_think`` = Hy3 + 深度思考**开**
  （hunyuan_gpt_175B_0404 → hunyuan_t1）。**联网搜索无独立开关**——20260810
  live 实测（CDP attach yuanbao-tj 常驻浏览器）：全页含隐藏元素无任何
  「联网搜索/连网」控件，元宝联网检索是平台自动行为（检索词/引用卡片平台自决），
  可控口径只有「模型族 + 深度思考开关」两件，均发送前显式确保 + 后置校验；
  确认不了 → ``mode_toggle_failed`` non_retryable（绝不静默按错误口径采集，
  模型/思考错态 = 答案口径错标）；其余 mode → ``unsupported_mode``
  non_retryable。选择器校准数据见文末「模式开关确保」节注释。
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
  non_retryable（重试只是再撞）；模式开关无法确认到位（模型族/深度思考 toggle
  选择器漂移或控件不可用）→ ``mode_toggle_failed`` non_retryable（题级 wall +
  后续题 aborted，绝不按错误口径蒙混）。
  2026-08-14 起（墙词表 ``wall_lexicon``，对齐豆包）：答案文本级配额/禁言/
  拒答 → ``wall_quota``/``wall_muted``/``wall_refusal`` non_retryable；batch
  连坐按 wall_type 细化（muted 全连坐、quota 只连坐同 mode、refusal 不
  连坐，见 ``collect_batch`` docstring）。
- 成功判据（零合成）：``/api/chat/`` 流真正 loadingFinished，优先从该请求的 SSE
  响应体组装非空正文；协议解析失败时才读取 DOM 备用正文。两条路径都必须通过墙
  检查——缺一都不得返回成功。流未出现/截断/空答案 →
  ``answer_capture_incomplete``（可重试的诚实失败）。
- 官方分享导出（20260903 起，对齐文心/通义/DeepSeek）：答案定稿后在同一答案页
  打开分享条——「复制链接」剪贴板取 ``https://yb.tencent.com/s/<id>``，「生成图片」
  从 PhotoView 弹层取平台自渲染海报（``data:image/jpeg;base64`` 解码原样落盘，
  INV-32 零合成）。分享链接与海报是采集期一次性证据（20260831 定案：sse_raw/HAR
  离线补不出）；任一缺失 → ``answer_capture_incomplete``（可重试诚实失败），
  运行时截图绝不冒充分享图。

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

from domain.collection.uvw import normalize_retrieval_events, retrieval_events_from_trace_path
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

# profile 崩溃标记清理与豆包同一份实现（单一事实源，行为逐字一致）。
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
    YUANBAO_OFFICIAL_SHARE_HOSTS,
    OfficialShareExportError,
    capture_yuanbao_official_share,
    probe_official_share_url,
    write_share_link_manifest,
)
from workflows.activities.raw_capture import dump_raw_evidence_refs, maybe_raw_capture
from workflows.activities.resident_browser import platform_browser, resident_cdp_url
from workflows.activities.wall_lexicon import classify_answer_text, detect_muted_banner

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
# deep_think（深度思考，hunyuan_t1）流远长于 normal——对齐豆包/deepseek 600s；
# workflow 缺省总预算 15min（activity_timeout_minutes）放得下。
_CHAT_TIMEOUT_DEEP_THINK_S = 600.0
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

# ---------------------------------------------------------------------------
# 模式开关（20260810 live 校准，CDP attach yuanbao-tj 常驻浏览器 headed 窗口实证；
# 校准脚本/过程见 developlog fix log）：
#
# - 模型选择器 = ``div[dt-button-id='model_switch']``（aria-label「模型选择」），
#   ``dt-model-id`` 暴露当前模型：Hy3 族 = ``hunyuan_*``（实测 hunyuan_gpt_175B_0404），
#   DeepSeek 族 = ``deep_seek_*``（实测 deep_seek_v3）。点击弹出
#   ``div.ybc-model-select-dropdown-item``（Hy3=全能模型 / DeepSeek=适合深度思考）。
# - 深度思考 toggle = ``div[dt-button-id='deep_think']``（aria-label「深度思考」，
#   ThinkSelector 组件）：**开态比关态多一个 ``ThinkSelector_selected__*`` class
#   token**（结构差分——hash 后缀跨构建漂移、``ThinkSelector_selected`` 前缀稳定）；
#   开启后 dt-model-id 翻转为思考模型（Hy3 族 hunyuan_gpt_175B_0404 → hunyuan_t1，
#   仅作旁证不作主判据——模型 id 随发版轮换）。
# - 联网搜索：无控件（全页含隐藏元素实测零命中），平台自动检索，无可确保项。
#
# dt-button-id / aria-label 是数据埋点/无障碍语义属性，跨构建稳定性远高于 hash
# class——作 locator 主锚点；class 前缀仅作兜底。
# ---------------------------------------------------------------------------

# 深度思考 toggle 定位（命中第一个可见者，顺序即优先级）
_DEEP_THINK_TOGGLE_SELECTORS: tuple[str, ...] = (
    "div[dt-button-id='deep_think']",
    "div[aria-label='深度思考']",
    "div[class*='ThinkSelector_iconContainer']",
)

# 模型选择器定位
_MODEL_SWITCH_SELECTORS: tuple[str, ...] = (
    "div[dt-button-id='model_switch']",
    "div[aria-label='模型选择']",
)

# 20260831 live 校准：新版元宝把旧「Hy3 模型选择器 + 深度思考 toggle」合并成
# 一个按钮。触发器带稳定语义属性 ``data-thinking-mode-switcher-trigger`` / aria-label
# 「切换模型」，按钮文本直接显示当前口径（快速回答 / 深度思考 / 专家模式）；
# 弹层先展示当前模型与回答模式，模型行的二级菜单再列 Hy4 preview / Hy3 /
# DeepSeek。账号默认可能漂到「Hy4 preview + 专家模式」，而 Hy4 只支持专家模式，
# 所以不能只按触发器文案切回答模式：必须先在二级菜单确保 Hy3，再选快速回答或
# 深度思考。弹层选项使用 ``role=menuitemradio`` + ``aria-checked``。旧 DOM 仍保留
# 在后面的兼容路径中。
_COMBINED_MODE_TRIGGER_SELECTORS: tuple[str, ...] = (
    "button[data-thinking-mode-switcher-trigger='true']",
    "button[aria-label='切换模型']",
)

_COMBINED_MODEL_MENU_SELECTORS: tuple[str, ...] = (
    "button[role='menuitem'][aria-label='选择模型']",
    "[role='menuitem'][aria-label='选择模型']",
)

_COMBINED_HY3_OPTION_SELECTORS: tuple[str, ...] = (
    "button[role='menuitemradio']:has(span:text-is('Hy3'))",
    "button[role='menuitemradio']:has-text('Hy3')",
    "[role='menuitemradio']:has-text('Hy3')",
)

_COMBINED_MODE_OPTION_SELECTORS: dict[str, tuple[str, ...]] = {
    "normal": (
        "button[role='menuitemradio']:has-text('快速回答')",
        "[role='menuitemradio']:has-text('快速回答')",
    ),
    "deep_think": (
        "button[role='menuitemradio']:has-text('深度思考')",
        "[role='menuitemradio']:has-text('深度思考')",
    ),
}

_COMBINED_MODE_STATE_JS = r"""() => {
  const el = document.querySelector("button[data-thinking-mode-switcher-trigger='true']")
    || document.querySelector("button[aria-label='切换模型']");
  if (!el) return {found: false};
  const r = el.getBoundingClientRect();
  if (r.width === 0 || r.height === 0) return {found: false};
  const label = (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
  const mode = label.includes("快速回答")
    ? "normal"
    : (label.includes("深度思考")
      ? "deep_think"
      : (label.includes("专家模式") ? "expert" : null));
  return {
    found: true,
    label,
    mode,
    expanded: el.getAttribute("aria-expanded") === "true",
  };
}"""

_COMBINED_MENU_STATE_JS = r"""() => {
  const rows = Array.from(document.querySelectorAll(
    "button[role='menuitem'][aria-label='选择模型'], [role='menuitem'][aria-label='选择模型']"
  ));
  const el = rows.find((row) => {
    const r = row.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  });
  if (!el) return {found: false};
  const label = (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
  const model = label.includes("Hy4")
    ? "hy4"
    : (/(^|\s)Hy3($|\s)/.test(label)
      ? "hy3"
      : (label.toLowerCase().includes("deepseek") ? "deepseek" : null));
  return {found: true, label, model};
}"""

# 模型下拉 Hy3 选项（下拉弹出后可见；:text-is 精确匹配防未来多 Hy 选项歧义）
_HY3_OPTION_SELECTORS: tuple[str, ...] = (
    "div.ybc-model-select-dropdown-item-name:text-is('Hy3')",
    "div[class*='model-select-dropdown-item-name']:text-is('Hy3')",
    "div[class*='model-select-dropdown-item']:has-text('Hy3')",
)

# 深度思考开关态探针：选中态 = class token 含 ThinkSelector_selected 前缀（结构
# 差分，绝不硬编码 hash 后缀）；found:false = 控件不存在/不可见，调用方诚实失败。
_DEEP_THINK_STATE_JS = r"""() => {
  const el = document.querySelector("div[dt-button-id='deep_think']")
    || document.querySelector("div[aria-label='深度思考']")
    || document.querySelector("div[class*='ThinkSelector_iconContainer']");
  if (!el) return {found: false};
  const r = el.getBoundingClientRect();
  if (r.width === 0 || r.height === 0) return {found: false};
  const tokens = (el.className || "").toString().trim().split(/\s+/).filter(Boolean);
  return {
    found: true,
    selected: tokens.some((t) => t.startsWith("ThinkSelector_selected")),
    model: el.getAttribute("dt-model-id") || null,
  };
}"""

# 模型族探针：dt-model-id 前缀判族（deep_seek* → deepseek；其余非空 → hunyuan）。
_MODEL_FAMILY_JS = r"""() => {
  const el = document.querySelector("div[dt-button-id='model_switch']")
    || document.querySelector("div[aria-label='模型选择']");
  if (!el) return {found: false};
  const r = el.getBoundingClientRect();
  if (r.width === 0 || r.height === 0) return {found: false};
  const model = el.getAttribute("dt-model-id") || "";
  const family = model.startsWith("deep_seek") ? "deepseek" : (model ? "hunyuan" : null);
  return {found: true, model: model || null, family};
}"""

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
    "*:text-is('请使用微信扫描二维码登录')",
    "*:text-is('登录失败，请稍后重试')",
    "*:text-is('获取登录态超时，请刷新后重试')",
    "*:text-is('微信登录')",
    "div[role='dialog']:has-text('微信登录')",
    "div[role='dialog']:has-text('手机号登录')",
    "div[role='dialog']:has-text('扫码登录')",
    "div[role='dialog']:has-text('登录后')",
    "div[class*='login-modal']:visible",
    "div[class*='LoginModal']:visible",
    "iframe[src*='login']",
    "iframe[src*='passport']",
)

# 20260831 北京账号现场校准：登录态失效时元宝不一定弹 modal，而是保留可见
# composer/模式工具栏，同时右上角显示「登录」、账号区显示「未登录」。如果只在
# composer 缺失时检查登录墙，这种静态壳会一路落到 mode_toggle_failed，误报成
# 选择器漂移并触发账号熔断。必须用「可见登录入口 + 未登录文案」组合判断，避免
# 把回答正文里偶然出现的“登录”二字误判成墙。
_STATIC_LOGIN_BUTTON_SELECTORS: tuple[str, ...] = (
    "button:text-is('登录')",
    "[role='button']:text-is('登录')",
    "a:text-is('登录')",
)

# 20260831 北京现场失败页原文。服务页可能仍保留 composer，因此必须先于模式
# 开关判定；否则平台临时故障会被永久归因为 mode_toggle_failed。
_SERVICE_UNAVAILABLE_PHRASES: tuple[str, ...] = (
    "拉取配置信息失败，请稍后重试",
    "服务异常，请稍后重试",
    "网络异常，请稍后重试",
)
_SERVICE_RETRY_SELECTORS: tuple[str, ...] = (
    "button:text-is('重试')",
    "[role='button']:text-is('重试')",
    "button:has-text('重新加载')",
)

# 验证码组件（旧链 login_state.CAPTCHA_SELECTORS 权威词表，通用）
_CAPTCHA_SELECTORS: tuple[str, ...] = (
    'iframe[src*="captcha"]',
    'iframe[src*="verify"]',
    'div[class*="captcha"]:visible',
    'div[id*="verify"]:visible',
    'div[class*="verify-wrap"]:visible',
)

# 助手回答气泡（选择器顺序即优先级）：markdown 正文容器优先于整气泡——
# 20260810 deep_think live 校准：深度思考模式下整气泡 inner_text 会混入
# 「已深度思考(用时N秒)」+ 思考链（``hyc-component-deepsearch-cot__think`` 子树），
# 正文权威节点 = ``hyc-content-md`` / ``hyc-common-markdown``（normal 模式同构，
# 流式完成态 ``hyc-content-md-done``）。取最后一个可见非思考块元素。
_ASSISTANT_SELECTORS: tuple[str, ...] = (
    "div[class*='hyc-content-md']",
    "div[class*='hyc-common-markdown']",
    "div[class*='hyc-content']",
    "div[class*='agent-chat__bubble--ai']",
    "div[class*='bubble'][class*='ai']",
    "div[class*='answer'] .markdown-body",
    ".markdown-body",
    "div[class*='message'][class*='assistant']",
)

# 思考链子树 class 特征（深度思考模式）：正文抽取一律跳过——思考链混入正文 =
# 答案口径事故（20260810 冒烟实证：气泡首命中截取「已深度思考」截断出 1 字答案）。
_THINK_BLOCK_CLASS_SUBSTR = "deepsearch-cot__think"

# 思考文本单块截断上限（字符）：对齐豆包 _THINKING_TEXT_LIMIT 水位。
_THINKING_TEXT_LIMIT = 5_000

# 引用资料（20260810 live 校准，deep_think/normal 双模式实证）：
# 元宝的检索资料**不是 a[href] 卡片**——答案页全页零 http 链接。真实载体 =
# 思考折叠块内 ``__item-search`` 的 doc 列表（``__doc__num`` 序号 +
# ``__doc__title__text``「标题 - 站点」纯文本；折叠态也在 DOM，textContent
# 可读，零交互零点击）。**平台不在 DOM 暴露 URL**（条目跳转走 JS 状态，无
# href/data-*）；模板副本（__template）与重复行去重。
# normal 模式实测**无任何资料列表组件**（来源只以纯文本写在答案正文里）→
# 该模式 references=[] 是诚实结果，不是选择器缺口。
# URL 补全（20260903 全流量探针实证，captures/yuanbao-conv1-bodies）：DOM 之外的
# 权威载体 = ``POST /api/user/agent/conversation/v1/detail`` 响应
# ``convs[].speechesV2[].content[type=searchGuid].docs[]``（每条带完整
# url/title/web_site_name/quote/index，22 条全量在案）——DOM 抽取后在页面上下文
# 同源 fetch（credentials:'include'，只读）补全 url（及 quote/web_site_name 可用
# 则补 summary/sitename），匹配不上的引用保持 url=None 诚实缺省。
_REFS_FROM_DOCS_JS = r"""() => {
  const bubbles = document.querySelectorAll("div[class*='agent-chat__bubble--ai']");
  if (!bubbles.length) return [];
  const last = bubbles[bubbles.length - 1];
  const out = [];
  const seen = new Set();
  for (const el of last.querySelectorAll("div[class*='__item__doc']")) {
    if (!(el instanceof HTMLElement)) continue;
    const cls = (el.className || "").toString();
    if (cls.includes("doc-container")) continue;      // 容器，不是条目
    if (cls.includes("__template")) continue;          // 模板副本
    if (el.closest("[class*='__template']")) continue; // 模板祖先下的渲染副本
    const numEl = el.querySelector("[class*='__doc__num']");
    const titleEl = el.querySelector("[class*='__doc__title__text']");
    const text = titleEl ? (titleEl.textContent || "").trim() : "";
    if (!text) continue;
    if (seen.has(text)) continue;  // 折叠/展开双份渲染序号各自重排——只能按文本去重
    seen.add(text);
    const num = numEl ? (numEl.textContent || "").trim().replace(/\.$/, "") : "";
    out.push({num, text});
  }
  return out;
}"""

# 会话 detail 接口引用 URL 补全（20260903 探针实证，仅 deep_think 有 doc 列表时
# 才由 _references_from_dom 触发；normal 模式 references=[] 不发本请求）。
# 答案页 URL 实测形状 /chat/<agentId>/<convId>（探针 meta postData 在案）；
# 页面内原生 fetch 同源带会话 cookie，AbortController 限时，只取最后一个带
# searchGuid docs 的 speech（与 DOM「最后一个 AI 气泡」口径一致；fresh-chat
# 纪律下每会话一题，正常只有一条）。任何失败由调用方 fail-open。
_DETAIL_DOCS_FETCH_JS = r"""async ({agentId, conversationId, timeoutMs}) => {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const resp = await fetch("/api/user/agent/conversation/v1/detail", {
      method: "POST",
      credentials: "include",
      headers: {"Content-Type": "application/json", "Accept": "application/json"},
      body: JSON.stringify({conversationId, offset: 0, limit: 14, agentId}),
      signal: ctrl.signal,
    });
    if (!resp.ok) return {ok: false, status: resp.status};
    const data = await resp.json();
    const convs = data && Array.isArray(data.convs) ? data.convs : [];
    let docs = null;
    for (const conv of convs) {
      const speeches = conv && Array.isArray(conv.speechesV2) ? conv.speechesV2 : [];
      for (const sp of speeches) {
        const contents = sp && Array.isArray(sp.content) ? sp.content : [];
        for (const ct of contents) {
          if (ct && ct.type === "searchGuid" && Array.isArray(ct.docs)) {
            docs = ct.docs.map((d) => ({
              index: typeof d.index === "number" ? d.index : null,
              title: typeof d.title === "string" ? d.title : null,
              url: typeof d.url === "string" ? d.url : null,
              sitename: typeof d.web_site_name === "string" ? d.web_site_name : null,
              quote: typeof d.quote === "string" ? d.quote : null,
            }));
          }
        }
      }
    }
    return {ok: true, docs: docs || []};
  } catch (e) {
    return {ok: false, error: String((e && e.name) || e)};
  } finally {
    clearTimeout(timer);
  }
}"""

_DETAIL_FETCH_TIMEOUT_MS = 6_000  # 页面内 detail fetch 预算（AbortController）

# 答案页 URL → (agentId, conversationId)（/chat/<agentId>/<convId>，两段缺一不可）
_CHAT_IDS_RE = re.compile(r"/chat/([^/?#]+)/([^/?#]+)")

# 引用卡片 a[href] 旧 GUESS 组（当前 UI 实测零命中；留作未来链接卡片形态兜底，
# 只收真实 http(s) href，绝不臆造，按 URL 去重）
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

# DOM 兜底抽取后裁剪尾部 UI 噪声（建议 chips / 工具栏 / 输入区占位）。
# 这些词也可能合法出现在答案正文里，因此 _trim_response 只能裁掉独立 UI
# 行，不能对子串做全文 find。
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

# ---------------------------------------------------------------------------
# 配置 / 错误类型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class YuanbaoAdapterConfig:
    """env 配置。proxy_url 原文只在启动浏览器时使用，绝不落日志/payload。

    ``browser_key``（2026-08-09 起，浏览器矩阵化）：attach/互斥锁/fence 用的
    opaque "platform"——batch 路径由 browser_router 解析为常驻实例键
    （``yuanbao_tj`` 等）；缺省平台 slug（per-task 老路径/测试行为不变）。"""

    profile_dir: Path
    proxy_url: str | None
    evidence_dir: Path
    headless: bool
    browser_key: str = "yuanbao"

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
        # 失败题原始流量证据（2026-08-10 起）：_collect_one 题末挂 raw/HAR ref，
        # 经 _failure_outcome → 失败 result.evidence 进 CAS。缺省空。
        self.evidence_refs: list[CollectionEvidenceRef] = []


class _IncompleteCapture(RuntimeError):
    """采集未完成的诚实失败（可重试）：流截断 / 空答案 / 无流等。"""

    def __init__(self, message: str, evidence_path: Path | None = None) -> None:
        super().__init__(message)
        self.evidence_path = evidence_path
        self.evidence_refs: list[CollectionEvidenceRef] = []


class _TransientServiceUnavailable(_IncompleteCapture):
    """元宝临时服务页未恢复；提升到 activity 边界，在同一题上重新 attach。"""


class _ModeToggleFailed(RuntimeError):
    """模式开关无法确认到位（模型族 Hy3 / 深度思考 toggle；non_retryable；
    绝不静默按错误口径采集）。"""

    def __init__(self, message: str, evidence_path: Path | None = None) -> None:
        super().__init__(message)
        self.evidence_path = evidence_path
        self.evidence_refs: list[CollectionEvidenceRef] = []


class _ModeUnconfirmed(RuntimeError):
    """deep_think 请求已下达（toggle 后置校验通过）但无 deepsearch-cot__think
    DOM 思考块证据——诚实失败（non_retryable）：绝不把无思考证据的答案按
    deep_think 落 completed（对照豆包 2026-08-14 口径：配额耗尽后平台静默
    回退非思考模式的「正常答案」曾是 2026-08-13 事故源头之一）。"""

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
    # 结构化 trace 证据路径（kind="sse"，transport="dom"；无内容/写盘失败=None 诚实缺省）
    trace_path: Path | None = None
    # 原始流量证据 ref（2026-08-10 起：sse_raw/har；GEO_RAW_CAPTURE=0 或写盘
    # 失败为空——诚实缺省）。_task_result_from_collected 并入 evidence。
    raw_evidence: list[CollectionEvidenceRef] = field(default_factory=list)
    # 官方分享证据（20260903 起，对齐文心/通义/DeepSeek）：share_image（平台
    # 自渲染海报 JPEG）+ share_link（official-share-link-v2 manifest）。导出
    # 失败即题级 incomplete，ok 答案恒携带两条。
    share_evidence: list[CollectionEvidenceRef] = field(default_factory=list)
    # Clean answer-only image plus verified DOM/OCR rectangles for report evidence cards.
    answer_evidence: CollectionEvidenceRef | None = None


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
    # 失败题的原始流量证据 ref（2026-08-10 起，raw/HAR，由题末异常对象携带
    # 而来）；aborted 题零浏览器交互，恒空。
    evidence: list[CollectionEvidenceRef] = field(default_factory=list)


class _BrowserSession(Protocol):
    """Playwright 交互隔离面：测试注入 fake，绝不启动真浏览器。"""

    def collect(
        self, query: str, on_stage: Callable[[str], None], *, mode: str = "normal"
    ) -> CollectedAnswer: ...

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
        if item.mode not in ("normal", "deep_think"):
            raise ApplicationError(
                f"unsupported mode: {item.mode!r} (expected 'normal' or 'deep_think')",
                type="unsupported_mode",
                non_retryable=True,
            )
    # 浏览器矩阵化（2026-08-09 起）：batch 段（同平台同地域）路由到对应常驻
    # 实例，实例键当 opaque platform 进 platform_browser/锁/fence/CDP 解析；
    # 无实例/地域不符/清单畸形一律 fail-closed。空 batch 不解析（旧契约不变）。
    # to_thread（2026-09-01 起）：账号竞争排队等待是阻塞轮询，绝不能卡住
    # async activity 的事件循环（同进程其他活动的心跳会被饿死）。
    route = await asyncio.to_thread(
        resolve_batch_instance, batch.items, run_pub_id=batch.run_pub_id
    )
    instance_key = route.instance_key if route is not None else None
    config = YuanbaoAdapterConfig.from_env(proxy_url_override=proxy_url_override)
    if route is not None:
        config = replace(config, browser_key=route.instance_key)
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
        browser_instance=instance_key,
        egress_region_gb=route.exit_gb if route is not None else None,
        fallback_proxy=(mask_proxy_url(config.proxy_url) if route is None else None),
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
                    evidence=wall.evidence_refs,
                )
                for item in batch.items
            ]
        )
    except _ModeUnconfirmed as mu:
        # 防御：mode_unconfirmed 应在题内转 outcome；逃出即按 session 级诚实记录。
        evidence_suffix = f"; evidence={mu.evidence_path}" if mu.evidence_path else ""
        bound.info("yuanbao_batch_session_mode_unconfirmed", stage=progress["stage"])
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
        bound.info("yuanbao_batch_session_toggle_failed", stage=progress["stage"])
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
        bound.info("yuanbao_batch_session_incomplete", reason=str(inc), stage=progress["stage"])
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
        "yuanbao_batch_done",
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
    if item.mode not in ("normal", "deep_think"):
        raise ApplicationError(
            f"unsupported mode: {item.mode!r} (expected 'normal' or 'deep_think')",
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
        bound.info("yuanbao_wall", wall_type=wall.wall_type, stage=progress["stage"])
        raise ApplicationError(
            f"{wall}{evidence}", type=wall.wall_type, non_retryable=True
        ) from wall
    except _ModeToggleFailed as toggle:
        evidence = f"; evidence={toggle.evidence_path}" if toggle.evidence_path else ""
        bound.info("yuanbao_mode_toggle_failed", stage=progress["stage"])
        raise ApplicationError(
            f"{toggle}{evidence}", type="mode_toggle_failed", non_retryable=True
        ) from toggle
    except _ModeUnconfirmed as mu:
        # deep_think 无思考块证据（2026-08-14 起）：non_retryable 诚实失败，
        # 绝不把无思考证据的答案按 deep_think 落 completed。
        evidence = f"; evidence={mu.evidence_path}" if mu.evidence_path else ""
        bound.info("yuanbao_mode_unconfirmed", stage=progress["stage"])
        raise ApplicationError(
            f"{mu}{evidence}", type="mode_unconfirmed", non_retryable=True
        ) from mu
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
    raw_record = _yuanbao_record_from_raw_evidence(collected.raw_evidence)
    use_raw_sources = raw_record is not None and bool(raw_record.get("search_guid_observed"))
    references = (
        list(raw_record.get("references") or []) if use_raw_sources else collected.references
    )
    answer_text = _compose_answer_text(collected.answer_text, references)
    citations = [
        {
            "url": str(ref["url"]),
            "title": str(ref["title"]).strip() if ref.get("title") else None,
            "cited_text": (str(ref["summary"]).strip() if ref.get("summary") else None),
            "platform_ordinal": ref.get("platform_ordinal", index),
            "ordinal_base": ref.get("ordinal_base", 1),
        }
        for index, ref in enumerate(references, 1)
        if isinstance(ref, dict)
        and isinstance(ref.get("url"), str)
        and ref["url"].startswith(("http://", "https://"))
    ]
    screenshot_ref = f"file://{collected.screenshot_path}"
    evidence: list[CollectionEvidenceRef] = []
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
    # 官方分享证据（20260903 起）：share_image（海报 JPEG）+ share_link manifest。
    evidence.extend(collected.share_evidence)
    if collected.answer_evidence is not None:
        evidence.append(collected.answer_evidence)
    # DLP 统一由 persist 层脱敏处理（单一权威边界，2026-08-06 起）。
    return CollectionTaskResult(
        business_key=item.business_key,
        answer_text=answer_text,
        screenshot_ref=screenshot_ref,
        quality_state="live_valid",
        citations=citations,
        evidence=evidence,
        retrieval_events=(
            list(raw_record.get("retrieval_events") or [])
            if use_raw_sources
            else retrieval_events_from_trace_path(collected.trace_path)
        ),
    )


_YUANBAO_CITATION_RE = re.compile(r"\[citation:(\d+)\]", re.IGNORECASE)
_YUANBAO_MOJIBAKE_HINTS = frozenset("ÃÂâæçåéèäöü")


def _repair_yuanbao_mojibake(value: Any) -> Any:
    if not isinstance(value, str) or not any(ch in value for ch in _YUANBAO_MOJIBAKE_HINTS):
        return value
    try:
        output = bytearray()
        for char in value:
            try:
                output.extend(char.encode("cp1252"))
            except UnicodeEncodeError:
                codepoint = ord(char)
                if codepoint < 256:
                    output.append(codepoint)
                else:
                    output.extend(char.encode())
        decoded = output.decode("utf-8", "replace")
    except Exception:
        return value
    has_cjk = any(0x3400 <= ord(char) <= 0x9FFF for char in decoded)
    return decoded if has_cjk and decoded.count("�") <= value.count("�") else value


def _yuanbao_record_from_sse(raw_sse: str) -> dict[str, Any] | None:
    """组装元宝 ``/api/chat/`` SSE 正文及搜索来源。

    2026-08-31 已有实现只在出现 ``searchGuid`` 时返回来源记录，不能用于普通
    问答正文。实测协议的 ``type=text`` / ``msg`` 是按到达顺序发送的文本增量；
    此处按协议字段顺序拼接，不使用字符块相似度或重叠猜测规则。引用锚点只用于
    关联来源卡片，返回正文前将其移除，与页面实际渲染语义一致。
    """
    docs: list[dict[str, Any]] = []
    answer_parts: list[str] = []
    search_guid_observed = False
    for block in re.split(r"\r?\n\r?\n", raw_sse):
        data_lines = [
            line[len("data:") :].strip() for line in block.splitlines() if line.startswith("data:")
        ]
        if not data_lines:
            continue
        try:
            payload = json.loads("\n".join(data_lines))
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("type") == "searchGuid":
            search_guid_observed = True
            source_docs = payload.get("docs")
            if isinstance(source_docs, list):
                docs.extend(row for row in source_docs if isinstance(row, dict))
        elif payload.get("type") == "text" and isinstance(payload.get("msg"), str):
            part = _repair_yuanbao_mojibake(payload["msg"])
            answer_parts.append(str(part))
    if not search_guid_observed and not answer_parts:
        return None

    raw_answer_text = "".join(answer_parts)
    answer_text = _YUANBAO_CITATION_RE.sub("", raw_answer_text).strip()

    cards_by_index: dict[int, dict[str, Any]] = {}
    collided_indexes: set[int] = set()
    candidates: list[dict[str, Any]] = []
    for position, doc in enumerate(docs, 1):
        raw_index = doc.get("index", position)
        index = (
            int(raw_index)
            if isinstance(raw_index, int) and not isinstance(raw_index, bool) and raw_index >= 1
            else position
        )
        url = doc.get("url")
        title = _repair_yuanbao_mojibake(doc.get("title"))
        summary = _repair_yuanbao_mojibake(doc.get("quote"))
        card = {
            "url": url,
            "title": str(title).strip() if isinstance(title, str) and title.strip() else None,
            "summary": (
                str(summary).strip() if isinstance(summary, str) and summary.strip() else None
            ),
            "platform_ordinal": index,
            "ordinal_base": 1,
        }
        prior = cards_by_index.get(index)
        if prior is not None and prior != card:
            collided_indexes.add(index)
        else:
            cards_by_index[index] = card
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            candidates.append(
                {
                    "url": url,
                    "title": card["title"],
                    "summary": card["summary"],
                    "u_rank": position,
                }
            )

    citation_indexes = list(
        dict.fromkeys(int(value) for value in _YUANBAO_CITATION_RE.findall("".join(answer_parts)))
    )
    references: list[dict[str, Any]] = []
    unresolved: list[int] = []
    for index in citation_indexes:
        card = cards_by_index.get(index)
        if (
            index in collided_indexes
            or card is None
            or not isinstance(card.get("url"), str)
            or not card["url"].startswith(("http://", "https://"))
        ):
            unresolved.append(index)
            continue
        references.append(card)
    final_references = [
        {
            "url": ref["url"],
            "title": ref.get("title"),
            "summary": ref.get("summary"),
            "final_reference_ordinal": ref["platform_ordinal"],
        }
        for ref in references
    ]
    retrieval_events = (
        normalize_retrieval_events(
            [
                {
                    "ordinal": 1,
                    "queries": [],
                    "u_observation": "observed",
                    "v_observation": "unobserved",
                    "final_reference_observation": "observed",
                    "candidates": candidates,
                    "opened_pages": [],
                    "final_references": final_references,
                    "evidence_relation": "answer_sse_raw",
                }
            ]
        )
        if search_guid_observed
        else []
    )
    return {
        "answer_text": answer_text,
        "raw_answer_text": raw_answer_text,
        "search_guid_observed": search_guid_observed,
        "references": references,
        "citation_indexes": citation_indexes,
        "unresolved_citation_indexes": unresolved,
        "candidate_count": len(candidates),
        "source_card_count": len(docs),
        "retrieval_events": retrieval_events,
    }


def _yuanbao_record_from_raw_evidence(
    evidence: list[CollectionEvidenceRef],
) -> dict[str, Any] | None:
    paths = [
        Path(item.path)
        for item in evidence
        if item.kind == "sse_raw" and item.relation_type == "answer_sse_raw"
    ]
    if len(paths) != 1:
        return None
    try:
        return _yuanbao_record_from_sse(paths[0].read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return None
    except Exception:
        log.warning("yuanbao_raw_sse_parse_failed", path=str(paths[0]), exc_info=True)
        return None


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


class _PlaywrightYuanbaoSession:
    """元宝网页采集的 sync Playwright 实现（persistent context / 常驻 CDP attach）。

    单题（``collect``，per-task 老路径）与 run 级会话复用（``collect_batch``）
    共享同一套 per-item 主体 ``_collect_one``——绝不复制出两套：

    - ``collect``：一次会话、一题、收尾（老行为不变）；
    - ``collect_batch``：一次会话，N 题在同一常驻会话/同一标签页里顺序完成
      （真人在同一浏览器窗口里连续聊天——每题落在全新会话但绝不重开浏览器）；
      每题成功后做「阅读停顿」（拟人读完回答：滚动浏览 + 停留）。

    batch 失败语义（2026-08-14 细化，对齐豆包）：题级失败转 outcome，结果列表
    与输入等长同序；连坐按失败类型分级——真墙（captcha/login/send/muted）=
    账号级阻断，后续题全 aborted（零浏览器交互：真人撞墙后会停下，不编造不
    硬闯）；wall_quota=配额按 (账号×mode) 计费，只连坐同 mode 余题；
    wall_refusal/mode_unconfirmed=题级内容/证据失败，不连坐，本题诚实失败后
    续跑；incomplete/toggle 失败维持旧语义（该题诚实失败、后续题 aborted）。
    session 建立阶段（launch/navigate/登录墙检查）的异常原样逃出，由
    activity 层按 session 级语义处理（一题未发）。
    """

    def __init__(self, config: YuanbaoAdapterConfig, evidence_dir: Path, file_stem: str) -> None:
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
        spec = YuanbaoBatchItemSpec(
            business_key=self._file_stem,
            query=query,
            mode=mode,
            file_stem=self._file_stem,
        )
        with self._browser_session(on_stage) as (context, page, driver):
            return self._collect_one(context, page, spec, on_stage, driver=driver)

    def collect_batch(
        self, items: list[YuanbaoBatchItemSpec], on_stage: Callable[[str], None]
    ) -> list[YuanbaoBatchItemOutcome]:
        outcomes: list[YuanbaoBatchItemOutcome] = []
        # 配额墙按 (账号×mode) 计费（2026-08-14 起，对齐豆包）：wall_quota 只
        # 连坐同 mode 余题——记录已撞配额的 mode，轮到其余题位次时零浏览器交互
        # 追加 aborted 占位（结果列表与输入等长同序的契约不变）。
        quota_blocked: dict[str, YuanbaoBatchItemSpec] = {}
        with self._browser_session(on_stage) as (context, page, driver):
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
                    answer = self._collect_one(context, page, spec, on_stage, driver=driver)
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
                    # deep_think 无思考块 DOM 证据（2026-08-14 起 non_retryable
                    # 诚实失败）：题级失败不连坐，余题照跑。
                    outcomes.append(self._failure_outcome(spec, "wall", "mode_unconfirmed", mu))
                    continue
                except _ModeToggleFailed as toggle:
                    outcomes.append(
                        self._failure_outcome(spec, "wall", "mode_toggle_failed", toggle)
                    )
                    outcomes.extend(
                        self._aborted_outcome(rest, spec, "mode_toggle_failed")
                        for rest in items[index + 1 :]
                    )
                    return outcomes
                except _TransientServiceUnavailable:
                    # 服务异常是平台/会话级瞬时故障，不是本题内容失败。提升到
                    # activity 边界，让 Temporal maximum_attempts=2 在同一题上
                    # 重新 attach；否则 itemwise run 会永久固化一条 failed。
                    raise
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
        exc: _WallError | _IncompleteCapture | _ModeToggleFailed | _ModeUnconfirmed,
    ) -> YuanbaoBatchItemOutcome:
        return YuanbaoBatchItemOutcome(
            business_key=spec.business_key,
            status=status,
            error_type=error_type,
            error_message=str(exc),
            evidence_path=exc.evidence_path,
            evidence=list(exc.evidence_refs),
        )

    @staticmethod
    def _aborted_outcome(
        spec: YuanbaoBatchItemSpec,
        failed_spec: YuanbaoBatchItemSpec,
        error_type: str | None,
        *,
        batch_stopped: bool = True,
    ) -> YuanbaoBatchItemOutcome:
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
        return YuanbaoBatchItemOutcome(
            business_key=spec.business_key,
            status="aborted",
            error_type="aborted_after_failure",
            error_message=reason,
        )

    def _reading_pause(self, page: Any) -> float:
        """拟人阅读停顿（human_like.human_read_pause，RNG 用本 session 实例）。"""
        return human_read_pause(page, self._rng)

    @contextlib.contextmanager
    def _browser_session(self, on_stage: Callable[[str], None]) -> Iterator[tuple[Any, Any, str]]:
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
                resident_url = resident_cdp_url(self._config.browser_key)
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
                page = context.pages[0] if context.pages else context.new_page()
                return context, page

            try:
                with platform_browser(pw, platform=self._config.browser_key, launch=_launch) as (
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
                    if _detect_login_wall(page) and not _recover_login_page(page, self._rng):
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
        # 原始流量留痕（2026-08-10 起，用户拍板默认开）：独立 CDP session 自组
        # HAR + 落 completion 原始响应体（元宝第一次抓 body——loadingFinished
        # 同步 getResponseBody，与既有 capture 同纪律），互不干扰。
        # GEO_RAW_CAPTURE=0 → None（全关回退现状）。
        raw = maybe_raw_capture(
            context,
            page,
            body_url_hints=("/api/chat/",),
            creator="geo-yuanbao-adapter",
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

            # 元宝偶发把已登录聊天页替换成「拉取配置失败/服务异常」页；该页可能
            # 仍渲染 composer，因此不能依赖 await_input 暴露故障。先点一次官方
            # 重试，再导航聊天首页兜底；恢复不了按临时 incomplete 重试，绝不把
            # 它记为模式选择器漂移并熔断账号。
            service_error = _service_unavailable_reason(page)
            if service_error is not None:
                on_stage("recover_service_page")
                if not _recover_service_page(page, self._rng):
                    raise _TransientServiceUnavailable(
                        f"yuanbao-service-unavailable: {service_error}",
                        _shot("service_unavailable"),
                    )

            # 静态掉登录壳仍有 composer；必须无条件检查，而不是仅在找不到输入框
            # 时才检查 login modal。
            if _detect_login_wall(page):
                on_stage("recover_login_page")
                if not _recover_login_page(page, self._rng):
                    raise _WallError(
                        "wall_login_required",
                        "yuanbao logged-out shell or login wall surfaced before input",
                        _shot("login"),
                    )

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
                muted = detect_muted_banner("yuanbao", _read_page_text(page))
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

            # 测量口径（对照 deepseek 20260810 用户拍板）：两种 mode 都显式确保——
            # normal=Hy3+深度思考关；deep_think=Hy3+深度思考开（联网检索为平台自动
            # 行为，无开关可确保）。必须在打字/发送之前完成并经后置校验确认；确认
            # 不了即诚实失败，绝不静默按错误口径采集（模型/思考错态 = 答案口径错标）。
            on_stage("ensure_mode")
            # Next.js 会在 composer 出现后继续异步挂载模式控件；给健康页面一个短
            # settle 窗。等待期间若转成服务异常/掉登录页，按真实状态分流，不再
            # 一律报 mode_toggle_failed。
            if not _wait_for_mode_surface(page, timeout_ms=5_000):
                if _detect_login_wall(page):
                    on_stage("recover_login_page")
                    if not _recover_login_page(page, self._rng):
                        raise _WallError(
                            "wall_login_required",
                            "yuanbao logged-out shell surfaced while awaiting mode controls",
                            _shot("login"),
                        )
                else:
                    service_error = _service_unavailable_reason(page)
                    if service_error is not None:
                        on_stage("recover_service_page")
                        if not _recover_service_page(page, self._rng):
                            raise _TransientServiceUnavailable(
                                f"yuanbao-service-unavailable-before-mode: {service_error}",
                                _shot("service_unavailable"),
                            )
                # 恢复动作可能重新导航，原 Locator 会在 Playwright 中重解析；重新
                # 确保 composer、新会话和模式控件后再进入硬校验。
                input_loc = _wait_for_input(page, timeout_ms=15_000)
                if input_loc is None:
                    raise _IncompleteCapture(
                        "could-not-find-chat-input-after-page-recovery",
                        _shot("no_input"),
                    )
                _ensure_fresh_chat(
                    page,
                    input_loc,
                    self._rng,
                    pace=_pace,
                    shot=_shot,
                )
                if not _wait_for_mode_surface(page, timeout_ms=5_000):
                    if _detect_login_wall(page):
                        raise _WallError(
                            "wall_login_required",
                            "yuanbao login wall persisted after recovery",
                            _shot("login"),
                        )
                    service_error = _service_unavailable_reason(page)
                    if service_error is not None:
                        raise _TransientServiceUnavailable(
                            f"yuanbao-service-unavailable-after-recovery: {service_error}",
                            _shot("service_unavailable"),
                        )
            if not _set_collection_mode(page, self._rng, spec.mode):
                raise _ModeToggleFailed(
                    f"mode toggles could not be confirmed for mode={spec.mode!r} "
                    "(模型族 Hy3 / 深度思考 toggle; selector drift or control "
                    "unavailable)",
                    _shot("mode_toggle"),
                )
            _pace(*_PACE_AFTER_NEW_CHAT_S)  # 切完（或确认完）开关回神再回到输入框

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
                page,
                appearance_timeout_s=20.0,
                timeout_s=(
                    _CHAT_TIMEOUT_DEEP_THINK_S if spec.mode == "deep_think" else _CHAT_TIMEOUT_S
                ),
            )
            sse_body = capture.latest_body()
            try:
                sse_record = _yuanbao_record_from_sse(sse_body) if sse_body else None
            except Exception:
                # 协议漂移不能越过已经存在的 DOM 备用路径。原始响应仍由
                # RawTrafficCapture 保存，日志只记录异常类型，不打印响应体。
                log.warning(
                    "yuanbao_sse_parse_failed",
                    business_key=spec.business_key,
                    exc_info=True,
                )
                sse_record = None
            sse_answer = (
                str(sse_record.get("answer_text") or "").strip() if sse_record is not None else ""
            )
            if sse_answer:
                # ``/api/chat/`` 是本次请求的机器响应真相源。DOM 仍承担页面截图、
                # 墙扫描和协议漂移时的备用抽取，不再无条件决定答案正文。
                answer_text = sse_answer
                answer_transport = "sse"
                references = (
                    list(sse_record.get("references") or [])
                    if sse_record.get("search_guid_observed")
                    else _references_from_dom(page)
                )
            else:
                answer_text = _wait_answer_stable(page, max_seconds=30.0, quiet_seconds=2.5)
                answer_transport = "dom_fallback"
                references = _references_from_dom(page)
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
                    "answer-empty-after-finished-stream: neither SSE assembly nor DOM "
                    "fallback produced answer text",
                    _shot("empty_answer"),
                )

            # 答案验收门（2026-08-14 起，词表唯一真源 wall_lexicon，对齐豆包）：
            # 答案文本定稿后、返回 ok 之前——平台提示文案（配额耗尽/禁言/拒答
            # 模板）被当作答案采回时在此拦截，抛 _WallError 走既有墙管道（batch
            # 连坐语义按 wall_type 细化，见 collect_batch docstring）。batch 与
            # per-task 单题共用本路径，两路都盖。
            verdict = classify_answer_text("yuanbao", answer_text)
            if verdict is not None:
                raise _WallError(
                    verdict.wall_type,
                    _wall_verdict_message(verdict, answer_text),
                    _shot("answer_wall"),
                )

            on_stage("screenshot")
            shot_path = self._evidence_dir / f"{spec.file_stem}.png"
            _capture_full_page(page, shot_path)
            if not shot_path.exists():
                raise _IncompleteCapture("evidence-screenshot-failed: no file written")
            # 结构化 trace 落盘进证据链（kind="sse"，transport="dom"；词表对齐
            # 文心/DeepSeek）：思考链（deep_think 模式 DOM 探针）+ 引用卡片折叠。
            # 写盘失败不拖垮已成功的采集——如实 warning 且不出该证据（绝不出残缺/
            # 编造证据）。deep_think_active 以实际抽到思考块为准（证据为正才标
            # true；toggle 已确保但块缺失时如实 false）。
            thinking_text = _extract_thinking_text(page) if spec.mode == "deep_think" else ""
            trace_references = (
                []
                if sse_record is not None and sse_record.get("search_guid_observed")
                else references
            )
            trace_path: Path | None = None
            if thinking_text or trace_references:
                trace_candidate = self._evidence_dir / f"{spec.file_stem}-sse-trace.json"
                try:
                    trace_candidate.write_text(
                        json.dumps(
                            _build_yuanbao_trace(
                                thinking_text,
                                trace_references,
                                deep_think_active=bool(thinking_text),
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
                        "yuanbao_trace_persist_failed",
                        file_stem=spec.file_stem,
                        exc_info=True,
                    )
            # mode 证据升级（2026-08-14 起，对齐豆包，warning-only →
            # non_retryable 诚实失败）：trace 已先落盘取证（deep_think_active 以
            # 实际抽到思考块为准）。请求 deep_think 而无 deepsearch-cot__think
            # 思考块证据 = 平台静默回退非思考模式的嫌疑答案，绝不落 completed
            # （2026-08-13 事故教训：配额耗尽后的回退答案曾被当 deep_think 采回）。
            if spec.mode == "deep_think" and not thinking_text:
                raise _ModeUnconfirmed(
                    "deep_think requested and toggle confirmed, but no "
                    "deepsearch-cot__think thinking block found in DOM — refusing "
                    "to record a normal-evidence answer as deep_think",
                    _shot("mode_unconfirmed"),
                )
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
            # 官方分享导出（20260903 起，对齐文心/通义/DeepSeek）：采集当下在
            # 同一答案页打开分享条——复制链接（剪贴板取 https://yb.tencent.com/s/
            # <id>）+ 生成图片（PhotoView 弹层平台自渲染海报 JPEG）。分享链接与
            # 海报是采集期一次性证据（20260831 定案：sse_raw/HAR 离线补不出），
            # 任一导不出 → 题级 incomplete 诚实失败，绝不落 completed。
            on_stage("share_export")
            share_image_path = self._evidence_dir / f"{spec.file_stem}-share.jpg"

            def _share_click(locator: Any) -> None:
                clicked_at = human_click(locator, page, self._rng, start=self._mouse_pos)
                if clicked_at is not None:
                    self._mouse_pos = clicked_at

            try:
                share = capture_yuanbao_official_share(
                    page,
                    share_image_path,
                    click=_share_click,
                )
                share_link_path = self._evidence_dir / f"{spec.file_stem}-share-link.json"
                write_share_link_manifest(
                    share_link_path,
                    share_url=share.share_url,
                    platform="yuanbao",
                    channel="clipboard",
                    verification=probe_official_share_url(
                        share.share_url,
                        allowed_hosts=YUANBAO_OFFICIAL_SHARE_HOSTS,
                    ),
                )
            except (OfficialShareExportError, OSError) as exc:
                raise _IncompleteCapture(
                    "official-share-export-incomplete: Yuanbao must provide both its "
                    f"share poster JPEG and public share URL ({type(exc).__name__}: {exc})",
                    _shot("share_export"),
                ) from exc
            except Exception as exc:
                raise _IncompleteCapture(
                    "official-share-export-incomplete: unexpected Yuanbao share UI failure "
                    f"({type(exc).__name__}: {exc})",
                    _shot("share_export"),
                ) from exc
            share_evidence = [
                CollectionEvidenceRef(
                    kind="share_image",
                    path=str(share.image_path),
                    relation_type="official_share_image",
                    mime_type="image/jpeg",
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
            answer = CollectedAnswer(
                answer_text=answer_text,
                references=references,
                screenshot_path=shot_path,
                meta={
                    "stream": meta,
                    "driver": driver,
                    "answer_transport": answer_transport,
                },
                trace_path=trace_path,
                raw_evidence=raw_evidence,
                share_evidence=share_evidence,
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
                warn_tag="yuanbao",
            )
            raise
        else:
            raw_evidence.extend(
                dump_raw_evidence_refs(
                    raw,
                    self._evidence_dir,
                    spec.file_stem,
                    source_url=_CHAT_URL,
                    warn_tag="yuanbao",
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


class _ChatStreamCapture:
    """捕获 POST ``/api/chat/`` SSE，并在流完成时同步读取响应体。

    Chromium 只短暂保留已完成请求的 body，因此 ``loadingFinished`` 处理器必须
    立即调用 ``Network.getResponseBody``。读取或协议解析失败时，上层才回退 DOM。
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
                req = payload.get("request") or {}
                self._url_by_request_id[req_id] = req.get("url", "")
                self._method_by_request_id[req_id] = req.get("method", "")
            elif name == "Network.responseReceived":
                response = payload.get("response") or {}
                url = self._url_by_request_id.get(req_id, "")
                method = self._method_by_request_id.get(req_id, "")
                mime_type = str(response.get("mimeType") or "").lower()
                if "/api/chat/" in url and method == "POST" and "event-stream" in mime_type:
                    if req_id not in self._stream_request_ids:
                        self._stream_request_ids.append(req_id)
            elif name == "Network.loadingFinished":
                self._loading_finished.add(req_id)
                if req_id in self._stream_request_ids:
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
        self._bodies[req_id] = str(_repair_yuanbao_mojibake(body))

    def has_stream_started(self) -> bool:
        return bool(self._stream_request_ids)

    def latest_body(self) -> str:
        """返回本题最后一个已完成 ``/api/chat/`` SSE 响应体。"""
        for req_id in reversed(self._stream_request_ids):
            if req_id in self._bodies:
                return self._bodies[req_id]
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
                "body_captured": False,
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
            "body_captured": target in self._bodies,
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
    # 静态掉登录页没有 dialog，且仍可能保留 composer/模式工具栏。双条件门避免
    # 单凭全页文案误伤正常回答。
    if "未登录" not in _read_page_text(page):
        return False
    for sel in _STATIC_LOGIN_BUTTON_SELECTORS:
        try:
            if page.locator(sel).first.is_visible(timeout=500):
                return True
        except Exception:
            continue
    return False


def _service_unavailable_reason(page: Any) -> str | None:
    """返回元宝临时服务故障页的原始命中文案；健康页返回 None。"""
    text = _read_page_text(page)
    # 北京 live 页面实际出现过 ASCII `, `，截图/旧版本则是中文逗号；空白与
    # 标点差异不应让同一故障页漏过分类门。
    normalized = re.sub(r"\s+", "", text).replace(",", "，")
    for phrase in _SERVICE_UNAVAILABLE_PHRASES:
        if re.sub(r"\s+", "", phrase).replace(",", "，") in normalized:
            return phrase
    return None


def _recover_service_page(page: Any, rng: random.Random) -> bool:
    """best-effort 恢复元宝服务异常页：官方重试一次，再导航 /chat 兜底。

    只在已确认服务异常文案后调用。返回值只说明故障文案是否消失；模式控件与
    登录态仍由后续独立硬门确认，避免一次恢复动作掩盖掉登录或 selector drift。
    """
    if _service_unavailable_reason(page) is None:
        return True
    for sel in _SERVICE_RETRY_SELECTORS:
        try:
            retry = page.locator(sel).first
            if retry.count() == 0 or not retry.is_visible(timeout=500):
                continue
            human_click(retry, page, rng)
            page.wait_for_timeout(2_500)
            break
        except Exception:
            continue
    if _service_unavailable_reason(page) is None:
        return True
    try:
        page.goto(_CHAT_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
        page.wait_for_timeout(_HYDRATION_SETTLE_MS)
        _try_close_overlays(page, rng)
    except Exception:
        return False
    return _service_unavailable_reason(page) is None


def _recover_login_page(page: Any, rng: random.Random) -> bool:
    """恢复元宝偶发的登录失败弹层；真正掉登录时保持 fail-closed。

    北京 live 会在已登录 profile 上短暂弹出「登录失败，请稍后重试 / 微信登录」，
    随后刷新又恢复原账号。先按真人动作关弹层，再导航聊天首页重新 hydration；
    最终仍见登录墙才返回 False，绝不把匿名壳当作已登录。
    """
    if not _detect_login_wall(page):
        return True
    _try_close_overlays(page, rng)
    page.wait_for_timeout(1_500)
    if not _detect_login_wall(page):
        return True
    try:
        page.goto(_CHAT_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
        page.wait_for_timeout(_HYDRATION_SETTLE_MS)
        _try_close_overlays(page, rng)
    except Exception:
        return False
    return not _detect_login_wall(page)


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


# ---------------------------------------------------------------------------
# 模式开关确保（校准依据见文件头「模式开关」节注释；语义对齐 deepseek 适配器
# 20260810 同款实现：幂等零点击 + 后置校验 + 隔拍二次确认，确认不了诚实失败）
# ---------------------------------------------------------------------------


def _first_visible(page: Any, selectors: tuple[str, ...]) -> Any | None:
    """选择器组里第一个可见元素（Locator）；全不可见/异常 → None。"""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=500):
                return loc
        except Exception:
            continue
    return None


def _deep_think_engaged(page: Any) -> bool | None:
    """深度思考 toggle 当前是否开启；找不到/读不出状态 → None（调用方诚实失败，
    绝不猜）。主判据 = ThinkSelector_selected class token 结构差分。"""
    try:
        state = page.evaluate(_DEEP_THINK_STATE_JS)
    except Exception:
        return None
    if not isinstance(state, dict) or not state.get("found"):
        return None
    selected = state.get("selected")
    return selected if isinstance(selected, bool) else None


def _model_family(page: Any) -> str | None:
    """当前模型族（'hunyuan' / 'deepseek'）；找不到/读不出 → None。"""
    try:
        state = page.evaluate(_MODEL_FAMILY_JS)
    except Exception:
        return None
    if not isinstance(state, dict) or not state.get("found"):
        return None
    family = state.get("family")
    return family if isinstance(family, str) else None


def _combined_mode_state(page: Any) -> dict[str, Any] | None:
    """新版合并模式按钮的可观察状态；控件不存在返回 None，存在但标签未知时
    保留 ``mode=None``，调用方必须 fail-closed，不能猜测当前口径。"""
    try:
        state = page.evaluate(_COMBINED_MODE_STATE_JS)
    except Exception:
        return None
    if not isinstance(state, dict) or not state.get("found"):
        return None
    return state


def _mode_surface_ready(page: Any) -> bool:
    """新版合并控件或完整旧版 Hy3/toggle 控件已经可观测。"""
    if _combined_mode_state(page) is not None:
        return True
    return _model_family(page) is not None and _deep_think_engaged(page) is not None


def _wait_for_mode_surface(page: Any, *, timeout_ms: int) -> bool:
    """等待 Next.js 在 composer 之后异步挂载模式控件。"""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        # 服务异常页会短暂保留上一帧模式控件；错误态必须比残留控件优先，
        # 否则后续 _set_collection_mode 会把平台故障误记为 selector drift。
        if _detect_login_wall(page) or _service_unavailable_reason(page) is not None:
            return False
        if _mode_surface_ready(page):
            return True
        page.wait_for_timeout(300)
    if _detect_login_wall(page) or _service_unavailable_reason(page) is not None:
        return False
    return _mode_surface_ready(page)


def _combined_menu_model(page: Any) -> str | None:
    """新版合并弹层里当前模型（hy3/hy4/deepseek）；弹层未打开或文案未知
    返回 None。模型行是当前 UI 唯一可观察的模型 ground-truth。"""
    try:
        state = page.evaluate(_COMBINED_MENU_STATE_JS)
    except Exception:
        return None
    if not isinstance(state, dict) or not state.get("found"):
        return None
    model = state.get("model")
    return model if isinstance(model, str) else None


def _open_combined_menu(page: Any, rng: random.Random) -> Any | None:
    """打开新版合并菜单并返回触发器；弹层模型行不可观察即失败。"""
    trigger = _first_visible(page, _COMBINED_MODE_TRIGGER_SELECTORS)
    if trigger is None:
        return None
    state = _combined_mode_state(page)
    if not state or not state.get("expanded"):
        try:
            human_click(trigger, page, rng)
        except Exception:
            return None
        page.wait_for_timeout(300)
    # 当前模型可能是未来新增的 Hy5 等未知值；只要模型行真实可见，调用方仍可
    # 进入二级菜单显式选择 Hy3。这里不能把「未知模型」误判成「菜单没打开」。
    if _first_visible(page, _COMBINED_MODEL_MENU_SELECTORS) is None:
        return None
    return trigger


def _close_combined_menu(page: Any, rng: random.Random, trigger: Any) -> bool:
    """关闭仍展开的合并菜单，避免弹层截获后续输入；已关闭则幂等成功。"""
    state = _combined_mode_state(page)
    if state is None:
        return False
    if not state.get("expanded"):
        return True
    try:
        human_click(trigger, page, rng)
    except Exception:
        pass
    # 北京新版菜单的 aria-expanded 会晚于点击动画更新，200ms 单拍会把已在
    # Hy3+快速回答的健康页面误判为 mode_toggle_failed。先给点击完整收口窗口；
    # 仍未关闭时用标准 Escape 语义兜底，再以 aria-expanded 后置校验。
    for _ in range(5):
        page.wait_for_timeout(200)
        state = _combined_mode_state(page)
        if state is not None and not state.get("expanded"):
            return True
    try:
        page.keyboard.press("Escape")
    except Exception:
        return False
    for _ in range(5):
        page.wait_for_timeout(200)
        state = _combined_mode_state(page)
        if state is not None and not state.get("expanded"):
            return True
    return False


def _ensure_combined_mode(page: Any, rng: random.Random, mode: str) -> bool | None:
    """确保新版合并模式按钮到目标态。

    None 表示新版控件不存在，允许调用方走旧 Hy3/toggle 兼容路径；False 表示
    新版控件存在但无法确认或切换，必须诚实失败，不能再用旧 DOM 猜测。
    """
    state = _combined_mode_state(page)
    if state is None:
        return None

    # 触发器只证明回答模式，不能证明模型：DeepSeek 也可能显示「深度思考」，
    # 账号默认还可能漂到仅支持专家模式的 Hy4。每题都打开菜单读取模型行，确保
    # 真正落在测量口径要求的 Hy3。
    trigger = _open_combined_menu(page, rng)
    if trigger is None:
        return False
    current_model = _combined_menu_model(page)
    if current_model != "hy3":
        model_menu = _first_visible(page, _COMBINED_MODEL_MENU_SELECTORS)
        if model_menu is None:
            return False
        try:
            human_click(model_menu, page, rng)
        except Exception:
            return False
        page.wait_for_timeout(250)
        hy3 = _first_visible(page, _COMBINED_HY3_OPTION_SELECTORS)
        if hy3 is None:
            return False
        try:
            human_click(hy3, page, rng)
        except Exception:
            return False
        page.wait_for_timeout(400)
        # 当前版本选 Hy3 后主菜单保持展开；未来若改为自动收起，则重开后再验。
        if _combined_menu_model(page) is None:
            trigger = _open_combined_menu(page, rng)
            if trigger is None:
                return False
        if _combined_menu_model(page) != "hy3":
            page.wait_for_timeout(400)
            if _combined_menu_model(page) != "hy3":
                return False

    state = _combined_mode_state(page)
    if state is None:
        return False
    if state.get("mode") == mode:
        return _close_combined_menu(page, rng, trigger)

    option = _first_visible(page, _COMBINED_MODE_OPTION_SELECTORS[mode])
    if option is None:
        return False
    try:
        human_click(option, page, rng)
    except Exception:
        return False
    page.wait_for_timeout(400)
    state = _combined_mode_state(page)
    if state is None or state.get("mode") != mode or state.get("expanded"):
        page.wait_for_timeout(400)
        state = _combined_mode_state(page)
        if state is None or state.get("mode") != mode or state.get("expanded"):
            return False
    return True


def _ensure_default_model(page: Any, rng: random.Random) -> bool:
    """确保模型族 = Hy3（账号默认全能模型）。已在 Hy3 → 零点击 True；在 DeepSeek
    族 → 拟人打开模型下拉点 Hy3 选项 + 后置校验；不可观测/点了不变 → False。"""
    family = _model_family(page)
    if family is None:
        return False
    if family == "hunyuan":
        return True
    switch = _first_visible(page, _MODEL_SWITCH_SELECTORS)
    if switch is None:
        return False
    try:
        human_click(switch, page, rng)
    except Exception:
        return False
    page.wait_for_timeout(600)  # 等下拉弹出
    option = _first_visible(page, _HY3_OPTION_SELECTORS)
    if option is None:
        return False
    try:
        human_click(option, page, rng)
    except Exception:
        return False
    page.wait_for_timeout(600)
    if _model_family(page) != "hunyuan":
        # 隔拍二次确认（UI 可能乐观翻转后回退）。
        page.wait_for_timeout(400)
        if _model_family(page) != "hunyuan":
            return False
    return True


def _ensure_deep_think(page: Any, rng: random.Random, want: bool) -> bool:
    """深度思考 toggle 确保到 want 态：已在目标态零点击（幂等，不制造多余行为
    指纹）；否则拟人点击 + 状态翻转等待 + 隔拍二次确认。找不到/读不出状态/
    点了不翻转 → False（调用方诚实失败，绝不猜）。"""
    current = _deep_think_engaged(page)
    if current is None:
        return False
    if current is want:
        return True
    toggle = _first_visible(page, _DEEP_THINK_TOGGLE_SELECTORS)
    if toggle is None:
        return False
    try:
        human_click(toggle, page, rng)
    except Exception:
        return False
    page.wait_for_timeout(400)
    if _deep_think_engaged(page) is not want:
        # 隔拍二次确认（豆包 T-03 同款纪律：UI 可能乐观翻转后回退）。
        page.wait_for_timeout(400)
        if _deep_think_engaged(page) is not want:
            return False
    return True


def _set_collection_mode(page: Any, rng: random.Random, mode: str) -> bool:
    """测量口径：``normal`` = Hy3 + 深度思考关；``deep_think`` = Hy3 + 深度思考开
    （联网检索为平台自动行为，无开关可确保）。先模型族后开关（切模型可能重置
    开关态）；全部后置校验确认才 True；确认不了 False。"""
    combined = _ensure_combined_mode(page, rng, mode)
    if combined is not None:
        return combined
    if not _ensure_default_model(page, rng):
        return False
    return _ensure_deep_think(page, rng, mode == "deep_think")


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


# 正文文本抽取（20260812 起表格保结构，W3 表格碎片证据根治，yiyan 同款）：
# inner_text 直出会把 <table> 压成 tab/换行序列丢行列对应（存量答案实测混入
# tab 压平的表格行）；clone 内逐表改写为 markdown 管道行（首行表头补分隔行、
# 单元格换行压空格、| 转义、<pre> 首尾补换行防表头粘连），原 DOM 不动。
_BODY_TEXT_JS = r"""(el) => {
  const c = el.cloneNode(true);
  for (const t of c.querySelectorAll('table')) {
    const rows = [];
    let cols = 0;
    for (const tr of t.querySelectorAll('tr')) {
      const cells = Array.from(tr.querySelectorAll('th,td')).map((td) =>
        (td.innerText || '').trim().replace(/\s+/g, ' ').replaceAll('|', '\\|'));
      if (!cells.length) continue;
      cols = Math.max(cols, cells.length);
      rows.push(cells);
    }
    if (!rows.length) { t.remove(); continue; }
    const lines = rows.map((r) =>
      '| ' + r.concat(Array(cols - r.length).fill('')).join(' | ') + ' |');
    lines.splice(1, 0, '| ' + Array(cols).fill('---').join(' | ') + ' |');
    const pre = document.createElement('pre');
    pre.textContent = '\n' + lines.join('\n') + '\n';
    t.replaceWith(pre);
  }
  return c.innerText;
}"""


def _extract_answer_text(page: Any) -> str:
    """DOM 抽取助手回答文本（最后一个可见 markdown 正文容器/气泡为准；
    思考链子树一律跳过——深度思考模式的链正文绝不混入答案）。"""
    for sel in _ASSISTANT_SELECTORS:
        try:
            elements = page.locator(sel).all()
            if not elements:
                continue
            for el in reversed(elements):
                try:
                    cls = el.get_attribute("class") or ""
                except Exception:
                    cls = ""
                if _THINK_BLOCK_CLASS_SUBSTR in cls:
                    continue
                try:
                    text = el.evaluate(_BODY_TEXT_JS, timeout=1_500) or ""
                except Exception:
                    continue
                if text and text.strip():
                    return _trim_response(text.strip())
        except Exception:
            continue
    return ""


# 深度思考链 DOM 探针（20260810 冒烟实证的结构）：最后一个 AI 气泡内
# ``[class*='deepsearch-cot__think']`` 块的 ``__content`` 子树即思考链正文
# （innerText 首行是「已深度思考(用时N秒)」header，剔除）。块不存在/为空 → ""
# （零合成，不编造）。仅 deep_think 模式调用；normal 模式无此子树。
_THINKING_EXTRACT_JS = r"""() => {
  const bubbles = document.querySelectorAll("div[class*='agent-chat__bubble--ai']");
  if (!bubbles.length) return '';
  const last = bubbles[bubbles.length - 1];
  const think = last.querySelector("[class*='deepsearch-cot__think']");
  if (!think) return '';
  const content = think.querySelector("[class*='__content']") || think;
  const text = (content.innerText || '').trim();
  if (!text) return '';
  return text
    .split('\n')
    .filter((line) => !line.trim().startsWith('已深度思考'))
    .join('\n')
    .trim();
}"""


def _extract_thinking_text(page: Any) -> str:
    """抽深度思考链原文（最后一个 AI 气泡内 deepsearch-cot__think 块的 __content
    子树文本，剔除「已深度思考(用时N秒)」header 行）。

    零合成：块不存在/无文本/探针异常一律空串（诚实缺省，绝不编造思考链）。
    """
    try:
        text = page.evaluate(_THINKING_EXTRACT_JS)
    except Exception:
        return ""
    return str(text or "").strip()


def _build_yuanbao_trace(
    thinking_text: str,
    references: list[dict[str, Any]],
    *,
    deep_think_active: bool,
) -> dict[str, Any]:
    """思考链 + 引用卡片 → trace record（kind="sse" 证据内容，词表对齐文心/
    DeepSeek：collection router 的 build_task_trace_view 消费同一词表）。

    transport="dom" 只描述本 trace 中思考链的来源：思考链来自 DOM 实渲染。
    答案正文另由 ``/api/chat/`` SSE 组装。references 单独保存为
    ``answer_reference_pages``，
    不能冒充完整 U 候选（检索词和 V 均未暴露）。思考文本单块截
    _THINKING_TEXT_LIMIT 字符（对齐豆包水位）。
    """
    thinking_chain: list[dict[str, Any]] = []
    if thinking_text:
        thinking_chain.append({"kind": "reasoning", "text": thinking_text[:_THINKING_TEXT_LIMIT]})
    answer_reference_pages = [
        {
            "title": str(ref.get("title") or "未命名来源"),
            "url": ref.get("url"),
            "site": ref.get("sitename"),
            "rank": index,
            "summary": str(ref.get("summary") or ""),
        }
        for index, ref in enumerate(references, 1)
    ]
    return {
        "engine": "yuanbao",
        "transport": "dom",
        "deep_think_active": deep_think_active,
        "thinking_chain": thinking_chain,
        # Yuanbao's DOM card is a final document/reference list, not proof of
        # the complete search candidate set.  U and V therefore stay unknown.
        "search_blocks": [],
        "opened_pages_observed": False,
        "opened_pages": [],
        "answer_reference_pages": answer_reference_pages,
    }


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
    """裁掉抽取容器末尾混入的独立 UI 行，保留正文内同词。

    2026-08-31 live 回归曾把“支持…联网搜索等。”从“联网搜索”开始误删：
    旧实现用 str.find 在全文找 UI 标签，没有区分行内正文。真实工具栏/chip
    在 innerText 中以独立行出现，所以只在标记词占满整行且前面已有正文时裁剪。
    """
    for marker in _TRAILING_NOISE_MARKERS:
        match = re.search(rf"(?m)^[ \t]*{re.escape(marker)}[ \t]*$", text)
        if match is not None and match.start() > 0:
            text = text[: match.start()].rstrip()
    return text.strip()


def _chat_ids_from_url(page_url: str) -> tuple[str, str] | None:
    """从答案页 URL 解析 (agentId, conversationId)（实测形状 /chat/<agentId>/<convId>，
    20260903 探针 Referer/postData 在案）。两段不齐 → None（fail-open，绝不瞎猜 id）。"""
    match = _CHAT_IDS_RE.search(page_url or "")
    if match is None:
        return None
    return match.group(1), match.group(2)


def _fetch_conversation_detail_docs(page: Any) -> list[dict[str, Any]]:
    """页面上下文 fetch 当前会话 detail 接口的 searchGuid docs（同源带会话 cookie，
    只读）。fail-open：id 解析失败/请求失败/超时/形状不符 → warning + []，绝不影响
    采集主路径（引用保持 url=None 诚实缺省）。"""
    page_url = str(getattr(page, "url", "") or "")
    ids = _chat_ids_from_url(page_url)
    if ids is None:
        log.warning("yuanbao_detail_backfill_skipped", reason="chat_ids_unparsed")
        return []
    agent_id, conversation_id = ids
    try:
        result = page.evaluate(
            _DETAIL_DOCS_FETCH_JS,
            {
                "agentId": agent_id,
                "conversationId": conversation_id,
                "timeoutMs": _DETAIL_FETCH_TIMEOUT_MS,
            },
        )
    except Exception as exc:
        log.warning("yuanbao_detail_fetch_failed", reason=type(exc).__name__)
        return []
    if not isinstance(result, dict) or not result.get("ok"):
        reason = "unexpected_result_shape"
        if isinstance(result, dict):
            reason = str(result.get("error") or f"http_{result.get('status')}")
        log.warning("yuanbao_detail_fetch_failed", reason=reason)
        return []
    docs = result.get("docs")
    if not isinstance(docs, list):
        log.warning("yuanbao_detail_fetch_failed", reason="docs_not_list")
        return []
    return [doc for doc in docs if isinstance(doc, dict)]


def _normalize_ref_title(value: Any) -> str:
    """标题规范化（去全部空白字符）供 DOM「标题 - 站点」与 detail docs 标题互配。"""
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", "", value)


def _parse_ref_num(value: Any) -> int | None:
    """DOM ``__doc__num`` 序号（"12"/"12." → 12）；解析不了 → None（只剩标题兜底）。"""
    try:
        num = int(str(value or "").strip().rstrip("."))
    except ValueError:
        return None
    return num if num >= 1 else None


def _merge_detail_doc_urls(
    refs: list[dict[str, Any]],
    nums: list[int | None],
    docs: list[dict[str, Any]],
) -> int:
    """把 detail docs 的 url（及 quote/web_site_name 可用则补 summary/sitename）合并进
    DOM refs，返回补全条数。

    匹配键：优先 DOM ``__doc__num`` 序号 ↔ doc ``index``——但序号对上而规范化标题
    冲突时不信任序号（序号口径漂移错挂 URL 比缺 URL 更糟），退化规范化标题精确匹配；
    匹配不上的 ref 保持 url=None（进 citations 的丢弃行为不变）；detail 多出的 docs
    绝不新增引用（以 DOM 为准，URL 只是补全）。
    """
    by_index: dict[int, dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}
    for doc in docs:
        url = doc.get("url")
        if not (isinstance(url, str) and url.startswith(("http://", "https://"))):
            continue  # 无有效 URL 的 doc 没有补全价值
        index = doc.get("index")
        if isinstance(index, int) and not isinstance(index, bool) and index >= 1:
            by_index.setdefault(index, doc)
        title_key = _normalize_ref_title(doc.get("title"))
        if title_key:
            by_title.setdefault(title_key, doc)
    filled = 0
    for ref, num in zip(refs, nums, strict=True):
        matched: dict[str, Any] | None = None
        if num is not None:
            candidate = by_index.get(num)
            if candidate is not None:
                ref_title = _normalize_ref_title(ref.get("title"))
                doc_title = _normalize_ref_title(candidate.get("title"))
                # 序号命中但标题冲突 = 序号口径漂移证据，不信任；退化标题匹配。
                if not ref_title or not doc_title or ref_title == doc_title:
                    matched = candidate
        if matched is None:
            matched = by_title.get(_normalize_ref_title(ref.get("title")))
        if matched is None:
            continue
        ref["url"] = matched["url"]
        quote = matched.get("quote")
        if not ref.get("summary") and isinstance(quote, str) and quote.strip():
            ref["summary"] = quote.strip()
        sitename = matched.get("sitename")
        if not ref.get("sitename") and isinstance(sitename, str) and sitename.strip():
            ref["sitename"] = sitename.strip()
        filled += 1
    return filled


def _references_from_dom(page: Any) -> list[dict[str, Any]]:
    """抽取检索资料列表。优先校准路径（思考折叠块 doc 列表，零交互 textContent
    读取；标题按最后一个「 - 」拆站点后缀）；url 平台不在 DOM 暴露，由
    conversation detail 接口在页面上下文补全（20260903 探针实证），补不上保持
    None 诚实缺省。空则走旧 a[href] GUESS 兜底组（当前 UI 零命中）。
    绝不编造条目/URL。"""
    try:
        rows = page.evaluate(_REFS_FROM_DOCS_JS)
    except Exception:
        rows = None
    if isinstance(rows, list):
        refs: list[dict[str, Any]] = []
        nums: list[int | None] = []  # 与 refs 同序的 __doc__num 序号（detail index 匹配主键）
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            title, sep, site = text.rpartition(" - ")
            if not sep:
                title, site = text, ""
            refs.append(
                {
                    "url": None,
                    "title": title.strip() or text,
                    "sitename": site.strip() or None,
                    "summary": None,
                    "index": len(refs),
                }
            )
            nums.append(_parse_ref_num(row.get("num")))
        if refs:
            # deep_think 才有 doc 列表（normal refs 恒空 → 不会触发 detail 请求）。
            docs = _fetch_conversation_detail_docs(page)
            if docs:
                filled = _merge_detail_doc_urls(refs, nums, docs)
                log.info(
                    "yuanbao_reference_urls_backfilled",
                    references=len(refs),
                    detail_docs=len(docs),
                    filled=filled,
                )
            return refs
    # 旧 GUESS a[href] 兜底组（当前 UI 零命中；有真实链接卡片形态时收 href）
    refs = []
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


def _read_page_text(page: Any) -> str:
    """best-effort 读 body 文本（2s 超时；失败=空串，绝不因此拖垮采集）。"""
    try:
        return str(page.locator("body").inner_text(timeout=2_000) or "")
    except Exception:
        return ""


def _scan_dom_notices(page: Any, *, exclude: str = "") -> dict[str, list[str]]:
    """best-effort 读 body 文本扫系统通知词（softban 过频 / 实名墙）。

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


def _capture_full_page(page: Any, out_path: Path) -> None:
    """整页截图（20260810 重写，零侵入）：CDP captureBeyondViewport 按
    cssContentSize 直接裁剪整页，``full_page`` 兜底——**绝不做 DOM flatten**。

    旧实现（与豆包同款 flatten JS）在元宝页面必现空白帧（0727 冒烟 + 0810 三次
    冒烟四张 4365B 全白图）：元宝会话正文是文档流（flatten 探针找不到内层滚动
    容器，``ok:false``），但 flatten 第三段仍无条件改写 body/html 高度/overflow
    并剥全页 transform——布局被打塌，随后无论 CDP 还是 full_page 路径截到的都
    是塌后的空白帧（X11 层窗口像素正常，纯合成器帧被毁）。元宝内容本就是文档
    流，captureBeyondViewport 不交 flatten 即可截全（0810 探针实证 139-157KB
    真实帧）。豆包/deepseek 的 flatten 副本不动（其页面实证无恙）。"""
    try:
        cdp = page.context.new_cdp_session(page)
        layout = cdp.send("Page.getLayoutMetrics")
        css_size = layout.get("cssContentSize") or layout.get("contentSize") or {}
        width = int(css_size.get("width") or 0) or 1280
        height = int(css_size.get("height") or 0) or 720
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
