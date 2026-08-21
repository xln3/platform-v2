"""通义千问网页采集适配器（tongyi.aliyun.com / www.tongyi.com → www.qianwen.com）。

按用户直接指令实现（ADR-0003 五平台上线）；2026-08-06 起与 doubao_adapter 同构
改造（拟人化 + run 级 batch 会话复用 + CDP 常驻 attach + 诚实失败语义）。结构照
``doubao_adapter.py``：配置门 → mode 门 → to_thread 跑 sync 浏览器 → 墙/结果映射。
与 doubao 的差异：

- 成功判据的「流式结束」两路取一：CDP Network 捕获到 ``text/event-stream``
  且 ``loadingFinished``（主路），或助手气泡 DOM 文本静默 settle 且停止按钮消失
  （兜底——通义部分会话走 WebSocket 时 CDP 看不到 event-stream）。正文抽取一律
  走渲染后 DOM（零合成：只取页面真实渲染文本，不解析/不臆造 SSE 协议格式）。
- batch activity 注册名 ``collect_tongyi_batch``（per-task 路径仍由
  platform_registry dispatcher 调 ``run_tongyi_collection``）。

边界：

- ``mode='normal'`` = 快速（composer 默认模式）；``mode='deep_think'`` =
  思考研究（20260810 起解锁，口径见下文「问答模式口径」节）；其余 mode →
  ``ApplicationError(..., type="unsupported_mode", non_retryable=True)``。
- 配置全走 env（秘密绝不进 task payload）：
  ``GEO_TONGYI_PROFILE_DIR``（必填，persistent profile 目录；缺失/不存在 →
  ``adapter_not_configured`` non_retryable）；``GEO_TONGYI_PROXY_URL``（可选，
  http://user:pass@host:port——日志只出现打码后的 scheme://host:port）；
  ``GEO_ADAPTER_EVIDENCE_DIR``（证据目录，默认
  ``platform-v2/runtime/adapter-evidence/tongyi/``，自动建目录）；
  ``GEO_TONGYI_HEADLESS``（默认 1；0=headed 需 DISPLAY）；
  ``GEO_TONGYI_CDP_URL``（可选，常驻浏览器 CDP 端点——非空则 attach 复用，
  空则每次 launch 全新 context；URL 非法 → ``adapter_not_configured``）。
- 执行模型：sync 浏览器驱动包在 ``asyncio.to_thread`` 里跑；协程侧每 10s 泵一次
  heartbeat（workflow heartbeat_timeout=30s，泵频 ≤15s 硬约束）。
- 浏览器驱动 patchright（生产同款反检测补丁版）；vanilla playwright 仅开发兜底。
- 墙分类（先截屏存证再抛，错误 message 带证据路径、绝不含秘密）：
  登录墙/实名墙 → ``wall_login_required`` non_retryable；验证码（阿里滑块等）→
  ``wall_captcha`` non_retryable；发送墙/限流 → ``wall_send`` non_retryable。
  2026-08-14 起（墙词表 ``wall_lexicon``，对齐豆包）：答案文本级配额/禁言/
  拒答 → ``wall_quota``/``wall_muted``/``wall_refusal`` non_retryable；batch
  连坐按 wall_type 细化（muted 全连坐、quota 只连坐同 mode、refusal 不
  连坐，见 ``collect_batch`` docstring）。
- 成功判据（零合成）：提交被接受（输入框清空）且流式结束（CDP finished 或
  DOM 静默兜底成立）且 **DOM 稳定门通过**（complete 类或文本静默 2.5s，
  2026-08-07 起——CDP 流完成不等于渲染完成）且正文非空且不含墙特征——
  缺一都不得返回成功。
  流截断/空答案/无流且 DOM 不静默/流完后 DOM 仍增长 → ``answer_capture_incomplete``
  （可重试的诚实失败）。

拟人化口径（2026-08-06 起，与 doubao 逐点对齐——自动化交互序列本身即指纹）：

- 输入：composer 正文一律 ``human_like.human_type`` 逐字真实键盘事件
  （40-140ms 抖动 + 标点/空格后 15% 概率 250-800ms 停顿），绝不 insert_text/fill。
- 点击：所有业务点击（发送按钮、弹层清理、「新对话」、输入框聚焦）一律
  ``human_like.human_click``——贝塞尔移动 + 到位悬停 + 元素内随机偏移点击。
- 节奏：页面就绪 → 端详 0.6-1.8s → 点输入框 → 逐字输入 → 通读 0.5-1.5s → 发送。
- 机器路径不动：CDP 捕获、提交确认轮询、墙识别、DOM 抽取、截图等纯观测逻辑
  不产生输入事件，不构成行为指纹，保持原样（含全部 live 校准语义）。

新会话纪律（每个问题必须落在全新会话，绝不在旧会话里追问）：

- await_input 后 ``_ensure_fresh_chat`` 验证：composer 为空（识别 qianwen
  占位符「\\ufeff向千问提问」）且页面无已存在消息节点 → 放行；否则优先点
  「新对话」类按钮（首选 ``button:has-text("新建对话")`` 已于 2026-08-07
  headed live 校准实证命中），仍不新则导航回聊天首页兜底；最终验证不过 →
  ``_IncompleteCapture`` 诚实失败（可重试），绝不静默沿用旧会话。

优雅关闭（profile 崩溃标记根治，launch 路径）：

- 所有退出路径（成功/墙/超时/异常）都经 ``platform_browser`` launch 分支的
  finally ``context.close()``；``_clean_profile_crash_state`` 在启动前（愈合被
  SIGKILL 的前任进程）与 close 后（兜底 close 期竞态）各幂等执行一次，把
  ``exit_type="Normal"`` / ``exited_cleanly=true`` 写回。
- attach 路径（``GEO_TONGYI_CDP_URL`` 非空）：浏览器归 supervisor 所有——退出只
  断开 CDP 连接（``platform_browser`` 契约），绝不 close context、绝不动 profile。

run 级会话复用（2026-08-06 起，``collect_tongyi_batch``，治本反风控）：

- 结构：一个 run 的通义任务在同一个常驻浏览器会话/同一标签页里顺序完成
  （一次 launch/attach，绝不每题冷启全新 Chromium——真人是在同一浏览器窗口里
  连续聊天的）。每题：fresh_chat 纪律（点「新对话」，绝不重开浏览器）→
  拟人输入/发送 → CDP/DOM 捕获/证据落盘（与 per-task 共用 ``_collect_one``
  主体，绝无两套复制）→ 「阅读停顿」（human_like.human_read_pause：滚动
  2-5 次 + 停留 8-25s 抖动——题间天然间隔，也产出真实浏览信号）→ 下一题。
- 失败语义：题级墙/incomplete → 该题诚实记失败、后续题 aborted
  （aborted_after_failure，零浏览器交互——真人撞墙后会停下，不编造不硬闯）；
  结果列表与输入等长同序返回，绝不 raise 丢掉已完成题。session 建立阶段
  （launch/navigate/登录墙）异常=一题未发：wall 类成全题 wall 结果，
  临时故障（_IncompleteCapture）raise 走 batch 级重试。仅配置类错误
  （adapter_not_configured/unsupported_mode）允许 raise。

问答模式口径（20260810 起，对照 deepseek/yuanbao 同日解锁；live 探针实证
于 tongyi_bj 常驻浏览器，存档 /tmp/probe11-page.html）：

- ``normal`` = 「快速」模式；``deep_think`` = 「思考研究」模式。开关 = composer
  ``[data-chat-input-shell="true"]`` 内 radix 菜单 trigger
  ``button[aria-haspopup="menu"]``（aria-label 即当前模式名「快速」/「思考研究」）。
  **原生 click 被 composer 布局层拦截、dispatch_event 对 radix 无效（听
  pointerdown）——唯一实证可靠路径是键盘**：trigger.focus() → ArrowDown 开菜单
  → 按高亮差分 ArrowDown/ArrowUp 到目标项 → Enter 选中。选中后按钮 aria-label
  变目标模式名（有 toast「已切换到思考研究」）。模式跨会话不粘滞（reload 回
  快速）——每题发送前显式确保（已是目标模式零交互幂等），切换后读回 aria-label
  确认 + 隔拍二次确认；确认不了 → ``mode_toggle_failed`` non_retryable（题级
  wall + 后续题 aborted），绝不按错误口径采集（模式错态 = 答案口径错标）。
- 思考研究答案 DOM：``div[data-chat-answers-wrap]`` 内 ``data-card_name=
  "bar_workflow"`` 思考流程卡在前、答案卡（.answer-common-card）在后。思考卡
  折叠容器（grid-rows-[0fr] opacity-0）textContent 零交互可读；逐步骤
  ``div.flex.gap-1``：标题行（text-sm font-semibold）+ 思考正文
  ``div[class*="thinking-content-"]``；搜索步骤的检索词 = 可见副本
  ``div.mb-2.flex.flex-wrap.items-center`` 内带引号 span（strip 引号），结果 =
  可见副本 ``div.flex.flex-wrap.gap-x-1.gap-y-2`` 内 ``span.truncate`` 标题
  （``a[href]`` 存在时取真实 URL，无则 None 诚实缺省）；两容器各有
  ``invisible absolute`` 隐藏副本，只取可见副本并按文本去重。思考链/检索词/
  结果折叠进 trace 证据（``_build_tongyi_trace``），**绝不混入答案正文**——
  ``_ANSWER_EXTRACT_JS`` 兜底分支已显式排除 bar_workflow/thinking-content
  内的 .qk-markdown 节点。
- deep_think 单题预算 600s（``_DEEP_THINK_CHAT_TIMEOUT_S``，对照
  deepseek/yuanbao 同款口径；思考研究实测可以很快但给足预算）。

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
- 2026-08-06 batch 化新增：「新对话」入口候选选择器 ``_NEW_CHAT_SELECTORS``
  与消息节点计数探针 ``_CHAT_MESSAGE_COUNT_JS``（基于已实证的
  qk-markdown/answer-common-card 类）——点不中有导航兜底，绝不静默沿用旧会话。
- 2026-08-07 「新建对话」按钮 headed live 校准（CDP attach tongyi-bj 常驻浏览器
  探针 dump）：侧边栏新建入口 = ``<button>新建对话</button>``（无 aria-label），
  ``button:has-text("新建对话")`` 实证命中并真实切到新会话——已提为首选候选。
- 2026-08-07 答案截断根因实证（五平台联合 run 通义仅 215 字案）：完整答案截图
  显示捕获时正文仍在流式增长（截在句中）。根因 = CDP 判「流完成」即抽取，未等
  DOM 稳定（多阶段流的生成段可能走 WebSocket、CDP 只看到检索流）。修复 =
  流完成后一律过 DOM 稳定门（``_DOM_SETTLE_AFTER_STREAM_S``），不过且有文本 →
  ``answer_capture_incomplete`` 诚实失败。当日三题复测（含卡片触发题）：答案均
  完整渲染在 ``.qk-markdown`` 内（1204-1419 字），未复现独立挂件卡片。
- 2026-08-07 容器级抽取：``_ANSWER_EXTRACT_JS`` 按文档序走查最后一个
  ``.answer-common-card`` 的直接子节点——markdown 段（可多段）+ 非 markdown
  富文本卡片段（供应商卡片类挂件，Python 侧过滤工具栏/按钮噪声后拼接）；无容器
  时兜底页面最后一个 .qk-markdown，再不行回退旧猜测选择器链。
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

from domain.collection.uvw import retrieval_events_from_trace_path
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

# 答案验收门命中消息的组装与豆包同一份实现（单一事实源，行为逐字一致）。
from workflows.activities.doubao_adapter import _wall_verdict_message
from workflows.activities.human_like import (
    human_click,
    human_pause,
    human_read_pause,
    human_type,
)
from workflows.activities.raw_capture import dump_raw_evidence_refs, maybe_raw_capture
from workflows.activities.resident_browser import platform_browser, resident_cdp_url
from workflows.activities.wall_lexicon import classify_answer_text, detect_muted_banner

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
# 2026-08-07 live 校准：千问深搜流（"检索到 N 篇资料"→再生成）单题可超 2 分钟，
# 120s 预算会在生成中段被掐（导航离开还会杀死服务端流）——放宽到 300s，
# 仍在 workflow 单题 15min 预算内。完成判定（saw_text+静默/complete 类）不变。
_CHAT_TIMEOUT_S = 300.0  # normal 模式流式完成预算
# deep_think（思考研究）单题预算 600s（对照 deepseek/yuanbao 20260810 同款口径；
# 思考研究实测可以很快，但多段检索+长生成给足预算）。
_DEEP_THINK_CHAT_TIMEOUT_S = 600.0
# 2026-08-07 live 实证（215 字截断案）：CDP 判「流完成」时正文可能仍在增长
# （多阶段流的生成段走 WebSocket / React 渲染滞后于流关闭）——流完成后必须再过
# DOM 稳定门：complete 类或文本静默 2.5s 才算真完成；60s 仍不稳 → 诚实失败。
_DOM_SETTLE_AFTER_STREAM_S = 60.0

# 2026-08-07 live 校准：通义已更名千问，主域 qianwen.com；tongyi.com 301 跳转
# 白白吃掉首轮导航预算（代理 RTT~1.3s 下 await_input 15s 不够）——主从互换。
_CHAT_URL = "https://www.qianwen.com/"
_FALLBACK_URL = "https://www.tongyi.com/"

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

# 「新对话」入口候选（fresh-chat 纪律；命中第一个可见者即点，顺序即优先级）。
# 2026-08-07 headed live 校准（CDP attach 常驻浏览器 tongyi-bj，探针 dump 实证）：
# 侧边栏新建入口 = `<button>新建对话</button>`（visible，y=60 h=36，无 aria-label），
# `button:has-text("新建对话")` 一击即中——提为首选；其余为结构候选兜底，
# 点不中时由「导航回聊天首页」兜底保证新会话，绝不静默沿用旧会话。
_NEW_CHAT_SELECTORS: tuple[str, ...] = (
    'button:has-text("新建对话")',
    '[aria-label*="新建对话"]',
    '[aria-label*="新对话"]',
    'button:has-text("新对话")',
    '[role="button"]:has-text("新对话")',
    'a:has-text("新对话")',
    '[class*="new-chat"]',
    '[class*="newChat"]',
)

# 新会话验证：页面已存在消息节点计数（>0 = 旧会话/进行中的旧回答）。
# .qk-markdown / .answer-common-card 均为 2026-07-27 live 实测的助手气泡类。
_CHAT_MESSAGE_COUNT_JS = r"""() => {
  const sels = ['.qk-markdown', '.answer-common-card'];
  let n = 0;
  for (const s of sels) n += document.querySelectorAll(s).length;
  return n;
}"""

# ---------------------------------------------------------------------------
# 问答模式开关（20260810 live 探针实证，tongyi_bj 常驻浏览器）：composer
# [data-chat-input-shell="true"] 内 radix 菜单 trigger button[aria-haspopup="menu"]，
# aria-label 即当前模式名（「快速」/「思考研究」）。原生 click 被 composer 布局层
# 拦截、dispatch_event("click") 对 radix 无效（听 pointerdown）——唯一实证可靠
# 路径是键盘：focus → ArrowDown 开菜单 → 高亮导航 → Enter 选中。模式跨会话不
# 粘滞（reload 回快速），每题发送前显式确保。
# ---------------------------------------------------------------------------

# 当前模式读取：trigger aria-label → mode slug（读不出 = None → 调用方诚实失败）
_CHAT_MODE_STATE_JS = r"""() => {
  const shell = document.querySelector('[data-chat-input-shell="true"]');
  if (!shell) return null;
  for (const b of shell.querySelectorAll('button[aria-haspopup="menu"]')) {
    const label = (b.getAttribute('aria-label') || '').trim();
    if (label.includes('思考研究')) return 'deep_think';
    if (label.includes('快速')) return 'normal';
  }
  return null;
}"""

# trigger 定位（按当前模式名选 aria-label；模式确保只在两种已知态间切换）
_MODE_TRIGGER_SELECTOR_TPL = (
    '[data-chat-input-shell="true"] button[aria-haspopup="menu"][aria-label*="{label}"]'
)
_MODE_LABEL_BY_MODE = {"normal": "快速", "deep_think": "思考研究"}
_MODE_MENU_LABELS = ("快速", "思考研究")  # 菜单项顺序（第一项=快速，实证）

# 菜单项探针：开菜单后读 [role="menu"] 内条目的文本与高亮态（radix 高亮项带
# data-highlighted 属性）——用于按高亮差分导航；菜单未开/结构漂移 → 空列表，
# 调用方回退「当前模式项高亮」的固定键序（实证路径的对称推断）。
_CHAT_MODE_MENU_ITEMS_JS = r"""() => {
  const items = [];
  for (const menu of document.querySelectorAll('[role="menu"]')) {
    for (const it of menu.querySelectorAll('[role="menuitemradio"], [role="menuitem"]')) {
      items.push({
        text: (it.textContent || '').trim(),
        highlighted: it.hasAttribute('data-highlighted'),
      });
    }
  }
  return items;
}"""

# 拟人化节奏区间（秒）——端详页面 / 发送前通读 / 新会话切换
_PACE_PAGE_READY_S = (0.6, 1.8)
_PACE_BEFORE_SEND_S = (0.5, 1.5)
_PACE_AFTER_NEW_CHAT_S = (0.6, 1.2)

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
    """env 配置。proxy_url 原文只在启动浏览器时使用，绝不落日志/payload。

    ``browser_key``（2026-08-09 起，浏览器矩阵化）：attach/互斥锁/fence 用的
    opaque "platform"——batch 路径由 browser_router 解析为常驻实例键
    （``tongyi_bj`` 等）；缺省平台 slug（per-task 老路径/测试行为不变）。"""

    profile_dir: Path
    proxy_url: str | None
    evidence_dir: Path
    headless: bool
    browser_key: str = "tongyi"

    @classmethod
    def from_env(cls, *, proxy_url_override: str | None = None) -> TongyiAdapterConfig:
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
        # 常驻浏览器 CDP 端点（可选）：URL 非法属配置类错误，fail-closed 在配置门。
        try:
            resident_cdp_url("tongyi")
        except ValueError as error:
            raise ApplicationError(
                str(error),
                type="adapter_not_configured",
                non_retryable=True,
            ) from None
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
    """模式开关无法确认到位（快速/思考研究 radix 菜单；non_retryable；
    绝不静默按错误口径采集）。"""

    def __init__(self, message: str, evidence_path: Path | None = None) -> None:
        super().__init__(message)
        self.evidence_path = evidence_path
        self.evidence_refs: list[CollectionEvidenceRef] = []


class _ModeUnconfirmed(RuntimeError):
    """deep_think 请求已下达（菜单后置校验通过）但无 bar_workflow 思考流程卡
    DOM 证据——诚实失败（non_retryable）：绝不把无思考证据的答案按 deep_think
    落 completed（对照豆包 2026-08-14 口径：配额耗尽后平台静默回退非思考
    模式的「正常答案」曾是 2026-08-13 事故源头之一）。"""

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
    # 结构化 trace 证据路径（kind="sse"，transport="dom"；无引用/写盘失败=None 诚实缺省）
    trace_path: Path | None = None
    # 平台真实检索词（W1 词表 {"query","ordinal"}；20260810 起 deep_think 思考
    # 流程卡的检索步骤抽取；normal/未抽到为空列表）
    search_queries: list[dict[str, Any]] = field(default_factory=list)
    # 原始流量证据 ref（2026-08-10 起：sse_raw/har；GEO_RAW_CAPTURE=0 或写盘
    # 失败为空——诚实缺省）。_task_result_from_collected 并入 evidence。
    raw_evidence: list[CollectionEvidenceRef] = field(default_factory=list)
    # Clean answer-only image plus verified DOM/OCR rectangles for report evidence cards.
    answer_evidence: CollectionEvidenceRef | None = None


class _BrowserSession(Protocol):
    """Playwright 交互隔离面：测试注入 fake，绝不启动真浏览器。"""

    def collect(
        self, query: str, on_stage: Callable[[str], None], *, mode: str = "normal"
    ) -> CollectedAnswer: ...

    def collect_batch(
        self, items: list[TongyiBatchItemSpec], on_stage: Callable[[str], None]
    ) -> list[TongyiBatchItemOutcome]: ...


SessionFactory = Callable[[TongyiAdapterConfig, Path, str], _BrowserSession]


@dataclass(frozen=True)
class TongyiBatchItemSpec:
    """batch 内单题输入（session 层）：查询/mode + 证据文件名片段。"""

    business_key: str
    query: str
    mode: str
    file_stem: str


@dataclass
class TongyiBatchItemOutcome:
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


# ---------------------------------------------------------------------------
# batch activity 入口与异步泵
# ---------------------------------------------------------------------------


@activity.defn(name="collect_tongyi_batch")
async def collect_tongyi_batch(batch: CollectionBatchInput) -> CollectionBatchResult:
    """通义 batch 采集注册实现（workers/main.py 按 GEO_COLLECTION_ADAPTER 门控选择）。

    整个 batch 在同一个常驻浏览器会话里顺序完成（run 级会话复用）；墙/失败
    诚实记录在 per-item 结果里（本 activity 不因墙类失败 raise），仅配置类
    错误（adapter_not_configured/unsupported_mode）raise。
    """
    try:
        attempt = activity.info().attempt
    except RuntimeError:
        attempt = 1
    # 不传 session_factory：与 run_tongyi_collection 的生产约定一致（dispatcher
    # 只传业务参数）——缺省 None 才走 to_thread 分支跑真实 sync 浏览器；显式传
    # _PlaywrightTongyiSession 会误判为注入 fake，在事件循环里直跑 sync API
    # （doubao 2026-08-06 batch 首航生产事故同款教训）。
    return await run_tongyi_batch(
        batch,
        heartbeat=activity.heartbeat,
        attempt=attempt,
    )


async def run_tongyi_batch(
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
        session_factory = _PlaywrightTongyiSession
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
    config = TongyiAdapterConfig.from_env(proxy_url_override=proxy_url_override)
    if route is not None:
        config = replace(config, browser_key=route.instance_key)
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    batch_stem = f"batch-{_safe_stem(batch.run_pub_id)}-a{attempt}"
    specs = [
        TongyiBatchItemSpec(
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

    def _blocking() -> list[TongyiBatchItemOutcome]:
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
        bound.info("tongyi_batch_session_wall", wall_type=wall.wall_type, stage=progress["stage"])
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
        bound.info("tongyi_batch_session_mode_unconfirmed", stage=progress["stage"])
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
        bound.info("tongyi_batch_session_toggle_failed", stage=progress["stage"])
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
        bound.info("tongyi_batch_session_incomplete", reason=str(inc), stage=progress["stage"])
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
        "tongyi_batch_done",
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
    item: CollectionTaskInput, outcome: TongyiBatchItemOutcome
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


# ---------------------------------------------------------------------------
# per-task 异步入口（platform_registry dispatcher 生产路径）
# ---------------------------------------------------------------------------


async def run_tongyi_collection(
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
    uses_default_session = session_factory is None
    factory: SessionFactory = session_factory or _PlaywrightTongyiSession
    hb = heartbeat if heartbeat is not None else (lambda payload: None)
    if item.mode not in ("normal", "deep_think"):
        raise ApplicationError(
            f"unsupported mode: {item.mode!r} (expected 'normal' or 'deep_think')",
            type="unsupported_mode",
            non_retryable=True,
        )
    config = TongyiAdapterConfig.from_env(proxy_url_override=proxy_url_override)
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
                hb({"business_key": item.business_key, "stage": progress["stage"]})
                done, _pending = await asyncio.wait({thread}, timeout=_HEARTBEAT_INTERVAL_S)
                if done:
                    break
            collected = thread.result()
        else:
            hb({"business_key": item.business_key, "stage": progress["stage"]})
            collected = _blocking()
    except _WallError as wall:
        evidence = f"; evidence={wall.evidence_path}" if wall.evidence_path else ""
        bound.info("tongyi_wall", wall_type=wall.wall_type, stage=progress["stage"])
        raise ApplicationError(
            f"{wall}{evidence}", type=wall.wall_type, non_retryable=True
        ) from wall
    except _ModeToggleFailed as toggle:
        evidence = f"; evidence={toggle.evidence_path}" if toggle.evidence_path else ""
        bound.info("tongyi_mode_toggle_failed", stage=progress["stage"])
        raise ApplicationError(
            f"{toggle}{evidence}", type="mode_toggle_failed", non_retryable=True
        ) from toggle
    except _ModeUnconfirmed as mu:
        # deep_think 无思考流程卡证据（2026-08-14 起）：non_retryable 诚实失败，
        # 绝不把无思考证据的答案按 deep_think 落 completed。
        evidence = f"; evidence={mu.evidence_path}" if mu.evidence_path else ""
        bound.info("tongyi_mode_unconfirmed", stage=progress["stage"])
        raise ApplicationError(
            f"{mu}{evidence}", type="mode_unconfirmed", non_retryable=True
        ) from mu
    except _IncompleteCapture as inc:
        evidence = f"; evidence={inc.evidence_path}" if inc.evidence_path else ""
        bound.info("tongyi_capture_incomplete", reason=str(inc), stage=progress["stage"])
        raise ApplicationError(f"{inc}{evidence}", type="answer_capture_incomplete") from inc
    bound.info(
        "tongyi_collect_ok",
        answer_len=len(collected.answer_text),
        references=len(collected.references),
        stage=progress["stage"],
    )
    return _task_result_from_collected(item, collected)


def _task_result_from_collected(
    item: CollectionTaskInput, collected: CollectedAnswer
) -> CollectionTaskResult:
    """CollectedAnswer → CollectionTaskResult 映射（answer 组装/出界 DLP 自检）。
    run_tongyi_collection 与 batch per-item ok 映射共用。"""
    answer_text = _compose_answer_text(collected.answer_text, collected.references)
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
    if collected.answer_evidence is not None:
        evidence.append(collected.answer_evidence)
    # DLP 统一由 persist 层脱敏处理（单一权威边界，2026-08-06 起）。
    return CollectionTaskResult(
        business_key=item.business_key,
        answer_text=answer_text,
        screenshot_ref=screenshot_ref,
        quality_state="live_valid",
        evidence=evidence,
        search_queries=collected.search_queries,
        retrieval_events=retrieval_events_from_trace_path(collected.trace_path),
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


def _clean_profile_crash_state(profile_dir: Path) -> bool:
    """幂等清理 Chromium profile 的异常退出标记。返回是否改写了 Preferences。

    真人正常关浏览器后 ``profile.exit_type`` 就是 ``"Normal"``；本函数把
    ``exit_type="Normal"`` / ``exited_cleanly=true`` 写回，其余键原样保留。
    Preferences 不存在 / JSON 损坏 / 结构异常 → 不动文件返回 False。
    原子写（同目录 tmp + os.replace），不截断原文件。仅 launch 路径使用——
    attach 路径 profile 归 supervisor 所有，绝不动。
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


class _PlaywrightTongyiSession:
    """通义网页采集的 sync Playwright 实现（persistent context / CDP 常驻 attach）。

    单题（``collect``，per-task 老路径）与 run 级会话复用（``collect_batch``）
    共享同一套 per-item 主体 ``_collect_one``——绝不复制出两套：

    - ``collect``：一次会话、一题、收尾（老行为不变）；
    - ``collect_batch``：一次 launch/attach，N 题在同一常驻会话/同一标签页里
      顺序完成（真人在同一浏览器窗口里连续聊天——每题落在全新会话但绝不重开
      浏览器）；每题成功后做「阅读停顿」（拟人读完回答：滚动浏览 + 停留）；
      launch 路径结束统一 context.close()（platform_browser 契约）+ 崩溃标记
      清理；attach 路径只断开连接，绝不 close/清理 profile。

    batch 失败语义（2026-08-14 细化，对齐豆包）：题级失败转 outcome，结果列表
    与输入等长同序；连坐按失败类型分级——真墙（captcha/login/send/muted）=
    账号级阻断，后续题全 aborted（零浏览器交互：真人撞墙后会停下，不编造不
    硬闯）；wall_quota=配额按 (账号×mode) 计费，只连坐同 mode 余题；
    wall_refusal/mode_unconfirmed=题级内容/证据失败，不连坐，本题诚实失败后
    续跑；incomplete/toggle 失败维持旧语义（该题诚实失败、后续题 aborted）。
    session 建立阶段（launch/navigate/登录墙检查）的异常原样逃出，由
    activity 层按 session 级语义处理（一题未发）。
    """

    def __init__(self, config: TongyiAdapterConfig, evidence_dir: Path, file_stem: str) -> None:
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
        spec = TongyiBatchItemSpec(
            business_key=self._file_stem,
            query=query,
            mode=mode,
            file_stem=self._file_stem,
        )
        with self._browser_session(on_stage) as (context, page, _pw_timeout, driver):
            return self._collect_one(context, page, spec, on_stage, driver=driver)

    def collect_batch(
        self, items: list[TongyiBatchItemSpec], on_stage: Callable[[str], None]
    ) -> list[TongyiBatchItemOutcome]:
        outcomes: list[TongyiBatchItemOutcome] = []
        # 配额墙按 (账号×mode) 计费（2026-08-14 起，对齐豆包）：wall_quota 只
        # 连坐同 mode 余题——记录已撞配额的 mode，轮到其余题位次时零浏览器交互
        # 追加 aborted 占位（结果列表与输入等长同序的契约不变）。
        quota_blocked: dict[str, TongyiBatchItemSpec] = {}
        with self._browser_session(on_stage) as (context, page, _pw_timeout, driver):
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
                        # 照跑（思考研究配额耗尽 ≠ 快速模式不可用）。
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
                    # deep_think 无思考流程卡 DOM 证据（2026-08-14 起 non_retryable
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
                    TongyiBatchItemOutcome(
                        business_key=spec.business_key, status="ok", answer=answer
                    )
                )
                # 阅读停顿：拟人读完回答（滚动浏览 + 停留 8-25s 抖动）——题间天然
                # 间隔，也产出真实浏览信号；最后一题同样停留（真人读完才关浏览器）。
                pause_s = self._reading_pause(page)
                log.info(
                    "tongyi_read_pause",
                    business_key=spec.business_key,
                    seconds=round(pause_s, 2),
                )
        return outcomes

    @staticmethod
    def _failure_outcome(
        spec: TongyiBatchItemSpec,
        status: str,
        error_type: str,
        exc: _WallError | _IncompleteCapture | _ModeToggleFailed | _ModeUnconfirmed,
    ) -> TongyiBatchItemOutcome:
        return TongyiBatchItemOutcome(
            business_key=spec.business_key,
            status=status,
            error_type=error_type,
            error_message=str(exc),
            evidence_path=exc.evidence_path,
            evidence=list(exc.evidence_refs),
        )

    @staticmethod
    def _aborted_outcome(
        spec: TongyiBatchItemSpec,
        failed_spec: TongyiBatchItemSpec,
        error_type: str | None,
        *,
        batch_stopped: bool = True,
    ) -> TongyiBatchItemOutcome:
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
        return TongyiBatchItemOutcome(
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

        经 ``platform_browser``：``GEO_TONGYI_CDP_URL`` 非空 → connect_over_cdp
        attach 常驻浏览器（退出只断开，绝不 close context、绝不动 profile——
        浏览器归 supervisor 所有）；否则回退 launch_persistent_context（每次
        全新 context，platform_browser 退出时统一 close）。

        优雅关闭（launch 路径，profile 崩溃标记根治）：启动前
        ``_clean_profile_crash_state`` 愈合前任进程的崩溃标记（幂等纯文件
        操作，失败不阻塞启动）；close 后再兜底清理一次（覆盖 close 期竞态）。
        """
        # 延迟导入：模块加载不硬依赖浏览器驱动（worker 未装依赖时仍可注册 fail-closed 实现）。
        # 驱动首选 patchright（生产同款反检测补丁版）；vanilla playwright 的
        # webdriver 指纹会触发平台风控静默吞发送，仅作开发兜底。
        driver, sync_playwright, PWTimeout = load_sync_browser_driver()

        on_stage("browser_launch")
        resident = resident_cdp_url(self._config.browser_key) is not None
        with sync_playwright() as pw:

            def _launch() -> tuple[Any, Any]:
                # 启动前愈合前任进程的崩溃标记（activity 取消/SIGKILL 会绕过正常
                # close，Chromium 未写回 exit_type=Normal → 下次启动弹
                # 「Restore pages?」）。仅 launch 路径执行（attach 不归我们管）。
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

            try:
                with platform_browser(pw, platform=self._config.browser_key, launch=_launch) as (
                    context,
                    page,
                    _is_resident,
                ):
                    on_stage("navigate")
                    try:
                        page.goto(_CHAT_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
                    except PWTimeout:
                        page.goto(
                            _FALLBACK_URL,
                            wait_until="domcontentloaded",
                            timeout=_NAV_TIMEOUT_MS,
                        )
                    page.wait_for_timeout(6_000)  # SPA + 反爬 JS 挂载 settle
                    _try_close_overlays(page, self._rng)
                    if _detect_login_wall(page):
                        raise _WallError(
                            "wall_login_required",
                            "tongyi login wall detected right after navigation",
                            self._shot(page, "login"),
                        )
                    yield context, page, PWTimeout, driver
            finally:
                if not resident:
                    # launch 路径：platform_browser 退出时已 context.close()；close 后
                    # 兜底清理崩溃标记（覆盖 close 期竞态）；幂等纯文件操作。
                    # attach 路径：浏览器归 supervisor，绝不动 profile。
                    try:
                        _clean_profile_crash_state(self._config.profile_dir)
                    except Exception as exc:
                        log.warning(
                            "tongyi_profile_crash_clean_failed",
                            business_key=self._file_stem,
                            error=f"{type(exc).__name__}: {exc}",
                        )

    def _collect_one(
        self,
        context: Any,
        page: Any,
        spec: TongyiBatchItemSpec,
        on_stage: Callable[[str], None],
        *,
        driver: str,
    ) -> CollectedAnswer:
        """单题主体：await_input → fresh_chat → 拟人输入/发送 → CDP/DOM 捕获/
        证据落盘。per-task 单题与 batch 每题共用（墙识别/SSE 校准语义原样）。"""
        capture = _EventStreamCapture(context, page)
        # 原始流量留痕（2026-08-10 起，用户拍板默认开）：独立 CDP session 自组
        # HAR + 落 completion 原始响应体（通义第一次抓 body——接口路径以 live
        # 校准为准，域级 hint + event-stream mime 双条件命中），与既有 capture
        # 互不干扰。GEO_RAW_CAPTURE=0 → None（全关回退现状）。
        raw = maybe_raw_capture(
            context,
            page,
            body_url_hints=("tongyi.com",),
            creator="geo-tongyi-adapter",
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

            input_loc = _wait_for_input(page, timeout_ms=30_000)
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
                muted = detect_muted_banner("tongyi", _read_page_text(page))
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

            # 问答模式确保（20260810 起，对照 deepseek/yuanbao 同日口径）：
            # normal=快速 / deep_think=思考研究——模式跨会话不粘滞，每题显式确保；
            # 必须在打字/发送之前完成并经读回+隔拍二次确认；确认不了即诚实失败，
            # 绝不静默按错误口径采集（模式错态 = 答案口径错标）。
            on_stage("ensure_mode")
            if not _ensure_collection_mode(page, spec.mode):
                raise _ModeToggleFailed(
                    f"mode toggle could not be confirmed for mode={spec.mode!r} "
                    "(快速/思考研究 radix 菜单; selector drift or control unavailable)",
                    _shot("mode_toggle"),
                )
            _pace(*_PACE_AFTER_NEW_CHAT_S)  # 切完（或确认完）模式回神再回到输入框

            on_stage("typing")
            # 页面就绪：真人先端详一眼再动手（零停顿直点输入框是机器人指纹）。
            _pace(*_PACE_PAGE_READY_S)
            # 迟到的 cookie/通知弹层可能截获发送按钮（composer 一直不清空）。
            # await_input 后再收一次（真实存在的遮罩才点击，见 _try_close_overlays）。
            _try_close_overlays(page, self._rng)
            # 点输入框聚焦（贝塞尔移动 + 悬停 + 框内随机偏移点击）。human_click
            # 拿不到布局时内部回退原生 click；仍失败则原样抛出=诚实失败。
            clicked_at = human_click(input_loc, page, self._rng, start=self._mouse_pos)
            if clicked_at is not None:
                self._mouse_pos = clicked_at
            human_type(input_loc, spec.query, self._rng)
            # 发送前通读一遍（type 后固定短停顿即发送=秒发指纹）。
            _pace(*_PACE_BEFORE_SEND_S)

            submit = _submit_and_confirm(
                page, input_loc, self._rng, pace=_pace, start=self._mouse_pos
            )
            if not submit.get("submitted"):
                # 输入框未清空：未登录拦截 or 发送被吞——先看登录墙再定发送墙
                if _detect_login_wall(page):
                    raise _WallError(
                        "wall_login_required",
                        "login wall surfaced on send (submission blocked by login)",
                        _shot("login"),
                    )
                raise _WallError(
                    "wall_send",
                    "send-not-accepted: composer still populated after "
                    f"{submit.get('attempts', '?')} send attempts (submission swallowed)",
                    _shot("send_wall"),
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
                        _shot("captcha"),
                    )
                if _detect_login_wall(page):
                    raise _WallError(
                        "wall_login_required",
                        "login wall surfaced post-send",
                        _shot("login"),
                    )
                if capture.has_stream_started() and time.monotonic() - challenge_start >= 3.5:
                    break
                page.wait_for_timeout(500)

            on_stage("await_stream")
            # deep_think（思考研究）给 600s 预算（对照 deepseek/yuanbao 同款口径）。
            chat_timeout_s = (
                _DEEP_THINK_CHAT_TIMEOUT_S if spec.mode == "deep_think" else _CHAT_TIMEOUT_S
            )
            meta = capture.wait_finish(page, appearance_timeout_s=20.0, timeout_s=chat_timeout_s)
            stream_finished = bool(meta.get("found") and meta.get("finished"))
            if meta.get("found") and not meta.get("finished"):
                raise _IncompleteCapture(
                    "stream-open-at-timeout: event-stream still open after budget "
                    f"({meta.get('bytes_received', 0)} bytes captured) — answer "
                    "would be truncated; failing honestly",
                    _shot("truncated"),
                )
            if not stream_finished:
                # CDP 看不到流（WebSocket 通道等）→ DOM 静默兜底：停止按钮消失
                # 且助手文本 quiet_s 内不再增长
                meta["dom_quiet"] = _wait_dom_quiet(page, quiet_s=2.5, timeout_s=chat_timeout_s)
                if not meta["dom_quiet"].get("quiet"):
                    raise _IncompleteCapture(
                        "no-stream-and-dom-not-quiet: neither CDP event-stream nor "
                        "DOM stability confirmed completion — failing honestly",
                        _shot("no_stream"),
                    )
            else:
                # 2026-08-07 硬化（215 字截断案）：CDP 判「流完成」≠ 渲染完成——
                # 多阶段流的生成段可能走 WebSocket / React 渲染滞后，旧口径流一完
                # 就抽取，实证截在正文中间（截图光标仍在流式）。流完成后一律再过
                # DOM 稳定门（complete 类快走 / 文本静默 2.5s）。
                meta["dom_settle"] = _wait_dom_quiet(
                    page, quiet_s=2.5, timeout_s=_DOM_SETTLE_AFTER_STREAM_S
                )
                if not meta["dom_settle"].get("quiet"):
                    probe_text, _probe_refs = _extract_response(page)
                    if probe_text:
                        raise _IncompleteCapture(
                            "stream-finished-but-dom-still-growing: CDP stream "
                            "completed but answer DOM kept changing past the settle "
                            "budget — answer would be truncated; failing honestly",
                            _shot("dom_unstable"),
                        )
                    # 文本始终为空：落入下方既有空答诊断（notices/墙/诚实失败）。

            on_stage("answer_extract")
            answer_text, references = _extract_response(page)
            if not answer_text and stream_finished:
                # SSE 先完、DOM 后渲（React 渲染滞后于流关闭）：给内容宽限窗，
                # 仍空再走既有 notices/诚实失败判定（绝不把空答当成功）。
                grace_deadline = time.monotonic() + 15.0
                while not answer_text and time.monotonic() < grace_deadline:
                    page.wait_for_timeout(500)
                    answer_text, references = _extract_response(page)
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
            if not answer_text:
                if _detect_login_wall(page):
                    raise _WallError(
                        "wall_login_required",
                        "login wall detected after completed stream (empty answer)",
                        _shot("login"),
                    )
                raise _IncompleteCapture(
                    "answer-empty-after-finished-stream: DOM extraction produced no "
                    "answer text after confirmed stream completion",
                    _shot("empty_answer"),
                )
            if any(marker in answer_text for marker in _LOGIN_TEXT_MARKERS):
                raise _WallError(
                    "wall_login_required",
                    "login-required text marker inside extracted answer",
                    _shot("login"),
                )

            # 答案验收门（2026-08-14 起，词表唯一真源 wall_lexicon，对齐豆包）：
            # 答案文本定稿后、返回 ok 之前——平台提示文案（配额耗尽/禁言/拒答
            # 模板）被当作答案采回时在此拦截，抛 _WallError 走既有墙管道（batch
            # 连坐语义按 wall_type 细化，见 collect_batch docstring）。batch 与
            # per-task 单题共用本路径，两路都盖。
            verdict = classify_answer_text("tongyi", answer_text)
            if verdict is not None:
                raise _WallError(
                    verdict.wall_type,
                    _wall_verdict_message(verdict, answer_text),
                    _shot("answer_wall"),
                )

            # 思考研究模式：答案稳定后抽思考流程卡（bar_workflow：思考链步骤 +
            # 检索词 + 检索结果；零交互 textContent 读取，无卡/探针异常 → 空，
            # 诚实缺省绝不编造）。normal 模式不调（页面无此卡）。
            thinking: dict[str, Any] | None = None
            if spec.mode == "deep_think":
                on_stage("thinking_extract")
                thinking = _extract_tongyi_thinking(page)

            on_stage("screenshot")
            shot_path = self._evidence_dir / f"{spec.file_stem}.png"
            _capture_full_page(page, shot_path)
            if not shot_path.exists():
                raise _IncompleteCapture("evidence-screenshot-failed: no file written")
            # 结构化 trace 落盘进证据链（kind="sse"，transport="dom"；词表对齐
            # 其余四平台）：deep_think 时思考链/检索词/检索结果折叠自思考流程卡，
            # 引用卡片折叠照常保留；normal 无引用不出空证据（诚实缺省）。写盘失败
            # 不拖垮已成功的采集——如实 warning 且不出该证据（绝不出残缺/编造证据）。
            trace_path: Path | None = None
            if references or (thinking is not None and thinking.get("card_found")):
                trace_candidate = self._evidence_dir / f"{spec.file_stem}-sse-trace.json"
                try:
                    trace_candidate.write_text(
                        json.dumps(
                            _build_tongyi_trace(references, thinking=thinking),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        encoding="utf-8",
                    )
                    trace_path = trace_candidate
                except Exception:
                    log.warning(
                        "tongyi_trace_persist_failed",
                        file_stem=spec.file_stem,
                        exc_info=True,
                    )
            # 平台真实检索词（W1 词表 {"query","ordinal"}）：思考流程卡检索步骤
            # 的关键词按文档序编号；normal/未抽到为空列表。
            search_queries: list[dict[str, Any]] = []
            if thinking is not None:
                search_queries = [
                    {"query": query, "ordinal": index}
                    for index, query in enumerate(thinking.get("queries") or [], 1)
                ]
            # mode 证据升级（2026-08-14 起，对齐豆包，warning-only →
            # non_retryable 诚实失败）：trace 已先落盘取证（deep_think_active 以
            # 实际抽到思考流程卡为准）。请求 deep_think 而无 bar_workflow 思考卡
            # 证据 = 平台静默回退非思考模式的嫌疑答案，绝不落 completed
            # （2026-08-13 事故教训：配额耗尽后的回退答案曾被当 deep_think 采回）。
            if spec.mode == "deep_think" and not (thinking and thinking.get("card_found")):
                raise _ModeUnconfirmed(
                    "deep_think requested and mode menu confirmed, but no "
                    "bar_workflow thinking card found in DOM — refusing to record "
                    "a normal-evidence answer as deep_think",
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
            answer = CollectedAnswer(
                answer_text=answer_text,
                references=references,
                screenshot_path=shot_path,
                meta={"stream": meta, "driver": driver},
                trace_path=trace_path,
                search_queries=search_queries,
                raw_evidence=raw_evidence,
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
                warn_tag="tongyi",
            )
            raise
        else:
            raw_evidence.extend(
                dump_raw_evidence_refs(
                    raw,
                    self._evidence_dir,
                    spec.file_stem,
                    source_url=_CHAT_URL,
                    warn_tag="tongyi",
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
        stream_settle_s: float = 5.0,
    ) -> dict[str, Any]:
        """两段等待：先等流出现，再等**最新一条**流 loadingFinished/Failed 且
        settle 窗内无新流，最后 DOM settle。

        2026-08-07 live 校准：千问深搜是多阶段流（检索流"检索到 N 篇资料"先完、
        生成流后完），旧口径盯第一条流导致答案未渲染就抽取（空答误报
        answer_capture_incomplete）——以最新流为准。
        """
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
        last_new_at = time.monotonic()
        seen_count = 1
        while time.monotonic() < overall_deadline:
            ids = self._stream_request_ids
            if len(ids) > seen_count:
                seen_count = len(ids)
                last_new_at = time.monotonic()
            target = ids[-1]
            if target in self._loading_finished or target in self._loading_failed:
                if time.monotonic() - last_new_at >= stream_settle_s:
                    page.wait_for_timeout(int(dom_settle_s * 1000))
                    break
            page.wait_for_timeout(150)
        return {
            "found": True,
            "finished": target in self._loading_finished,
            "failed": target in self._loading_failed,
            "bytes_received": self._bytes.get(target, 0),
            "streams_seen": seen_count,
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


def _fresh_chat_ok(page: Any, input_loc: Any) -> bool:
    """新会话 ground truth：composer 为空（识别 qianwen 占位符）且页面无已存在
    消息节点。探针异常一律按「不新」处理——宁可多走一步兜底，绝不静默沿用旧会话。
    """
    try:
        if not _composer_cleared(page, input_loc):
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
    类按钮，仍不新则导航回聊天首页兜底；最终验证不过 → _IncompleteCapture
    诚实失败（可重试），绝不静默沿用旧会话。
    """
    if _fresh_chat_ok(page, input_loc):
        return
    # 优先点「新对话」（真人在旧会话里想提新问题的标准动作；首选 2026-08-07 已
    # live 校准，点不中有导航兜底）。
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


def _current_collection_mode(page: Any) -> str | None:
    """读 composer 模式 trigger 的 aria-label → mode slug。读不出（无 shell/
    无 trigger/aria-label 漂移/探针异常）→ None（调用方诚实失败，绝不猜）。"""
    try:
        mode = page.evaluate(_CHAT_MODE_STATE_JS)
    except Exception:
        return None
    return mode if mode in _MODE_LABEL_BY_MODE else None


def _press(page: Any, key: str, times: int) -> None:
    for _ in range(times):
        page.keyboard.press(key)
        page.wait_for_timeout(120)


def _ensure_collection_mode(page: Any, mode: str) -> bool:
    """发送前把 composer 模式确保到 ``mode``（normal=快速 / deep_think=思考研究）。

    幂等：已是目标模式零交互 True。否则走唯一实证可靠的键盘路径（原生 click
    被 composer 布局层拦截、dispatch_event 对 radix 无效——20260810 探针实证）：
    trigger.focus() → ArrowDown 开菜单 → 按菜单探针的高亮差分（或「当前模式项
    高亮」固定键序兜底）导航到目标项 → Enter 选中。切换后读回 aria-label 确认 +
    隔拍二次确认（UI 可能乐观翻转后回退）；任何一步确认不了 → False（调用方
    mode_toggle_failed，绝不按错误口径采集）。
    """
    target_label = _MODE_LABEL_BY_MODE.get(mode)
    if target_label is None:
        return False
    current = _current_collection_mode(page)
    if current is None:
        return False
    if current == mode:
        return True  # 已在目标模式：零交互幂等
    current_label = _MODE_LABEL_BY_MODE[current]
    try:
        trigger = page.locator(_MODE_TRIGGER_SELECTOR_TPL.format(label=current_label)).first
        if trigger.count() == 0:
            return False
        trigger.focus()
        page.wait_for_timeout(200)
        page.keyboard.press("ArrowDown")  # 开菜单（radix 听键盘）
        page.wait_for_timeout(300)
    except Exception:
        return False
    # 菜单导航：优先按探针读到的高亮项差分；探针落空回退固定键序（开菜单时
    # 当前模式项高亮——实证路径的对称推断，确认门兜底，错了如实失败）。
    steps = 0
    key = "ArrowDown"
    try:
        items = page.evaluate(_CHAT_MODE_MENU_ITEMS_JS)
    except Exception:
        items = None
    if isinstance(items, list) and items:
        entries = [it for it in items if isinstance(it, dict)]
        texts = [str(it.get("text") or "") for it in entries]
        highlighted = next(
            (i for i, it in enumerate(entries) if it.get("highlighted")),
            None,
        )
        target_idx = next((i for i, t in enumerate(texts) if target_label in t), None)
        base_idx = highlighted
        if base_idx is None:
            base_idx = next((i for i, t in enumerate(texts) if current_label in t), None)
        if target_idx is None or base_idx is None:
            return False
        delta = target_idx - base_idx
        key = "ArrowDown" if delta >= 0 else "ArrowUp"
        steps = abs(delta)
    else:
        # 固定键序：菜单两项（快速/思考研究），开菜单时当前模式项高亮。
        steps = 1
        key = "ArrowDown" if mode == "deep_think" else "ArrowUp"
    try:
        _press(page, key, steps)
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)
    except Exception:
        return False
    if _current_collection_mode(page) != mode:
        # 隔拍二次确认（UI 可能乐观翻转后回退）。
        page.wait_for_timeout(400)
        if _current_collection_mode(page) != mode:
            return False
    return True


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


# qianwen 空 composer 的 textContent = "\ufeff向千问提问"（占位符以真实文本节点实现，
# 2026-07-27 live 探针实测）——清空判定必须识别占位符与 BOM
_COMPOSER_PLACEHOLDERS: tuple[str, ...] = ("向千问提问",)


def _composer_value_empty(raw: Any) -> bool:
    text = str(raw or "").replace("\ufeff", "").strip()
    return text == "" or text in _COMPOSER_PLACEHOLDERS


def _composer_cleared(page: Any, input_loc: Any) -> bool:
    """输入框清空 = 提交被受理的 ground-truth 信号。

    qianwen 发送成功后 composer 重渲染、占位符恢复为「\ufeff向千问提问」
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
            _send_via_keyboard(page, input_loc)
        waited = 0
        while waited < settle_ms:
            page.wait_for_timeout(poll_ms)
            waited += poll_ms
            if _composer_cleared(page, input_loc):
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


# 容器级答案走查（2026-08-07 起）：最后一个 .answer-common-card 内按文档序取
# markdown 段（.qk-markdown，可能多段——多阶段流分段渲染）+ 非 markdown 的
# 富文本卡片段（供应商卡片等挂件，2026-08-07 用户实测「正文完整、卡片损失」）。
# 无容器时兜底为页面最后一个 .qk-markdown（旧行为）。只读真实渲染文本，零合成。
# 20260810 deep_think 硬化：思考流程卡（bar_workflow / thinking-content-*）内
# 也有 .qk-markdown 节点——容器路径天然隔离（思考卡不在 .answer-common-card
# 内），兜底分支必须显式排除，否则思考链可能混进答案正文。
_ANSWER_EXTRACT_JS = r"""() => {
  const inThinkingCard = (el) =>
    !!(el.closest('[data-card_name="bar_workflow"]')
       || el.closest('div[class*="thinking-content-"]'));
  // 20260812 表格保结构（W3 表格碎片证据根治，yiyan 同款）：innerText 会把
  // <table> 压成 \n\t 序列丢行列对应（tongyi_bj 当前页 7×42 测绘对比表实证）；
  // clone 内逐表改写为 markdown 管道行（首行表头补分隔行、| 转义、<pre> 首尾
  // 补换行防表头粘连——两坑均 yiyan live 实证），原 DOM 不动。
  const tableMd = (rootEl) => {
    for (const t of rootEl.querySelectorAll('table')) {
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
  };
  const textOf = (el) => {
    const c = el.cloneNode(true);
    tableMd(c);
    return (c.innerText || '').trim();
  };
  const cards = Array.from(document.querySelectorAll('.answer-common-card'))
    .filter((el) => !inThinkingCard(el));
  const root = cards.length ? cards[cards.length - 1] : null;
  const segments = [];
  if (root) {
    for (const child of root.querySelectorAll(':scope > *')) {
      if (inThinkingCard(child)) continue;
      if (child.matches('.qk-markdown')) {
        segments.push({kind: 'markdown', cls: 'qk-markdown',
                       text: textOf(child)});
        continue;
      }
      const inner = Array.from(child.querySelectorAll('.qk-markdown'))
        .filter((el) => !inThinkingCard(el));
      if (inner.length) {
        for (const s of inner) {
          segments.push({kind: 'markdown', cls: 'qk-markdown',
                         text: textOf(s)});
        }
        continue;
      }
      segments.push({kind: 'widget',
                     cls: (child.className || '').toString().slice(0, 120),
                     text: textOf(child)});
    }
  } else {
    const mds = Array.from(document.querySelectorAll('.qk-markdown'))
      .filter((el) => !inThinkingCard(el));
    if (!mds.length) return {segments: [], refs: []};
    const last = mds[mds.length - 1];
    segments.push({kind: 'markdown', cls: 'qk-markdown',
                   text: textOf(last)});
  }
  const refRoot = root || document;
  const refs = [];
  const seen = new Set();
  for (const a of refRoot.querySelectorAll('a[href^="http"]')) {
    const href = a.getAttribute('href') || '';
    if (!href || seen.has(href)) continue;
    seen.add(href);
    refs.push({url: href, title: (a.innerText || '').trim() || null, sitename: null});
  }
  return {segments, refs};
}"""

# 元素级正文文本（旧选择器链兜底通道用；与 _ANSWER_EXTRACT_JS 内联副本同逻辑——
# 20260812 表格保结构：clone 内 <table>→markdown 管道行再取 innerText，原 DOM 不动）。
_ELEMENT_TEXT_JS = r"""(el) => {
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

# 卡片段噪声过滤（Python 侧，可单测）：工具栏/操作条类名与纯按钮短文本丢弃；
# 真正的富文本卡片（供应商卡片等）正文远超 8 字，短文本一律按噪声处理。
_WIDGET_NOISE_CLS_RE = re.compile(
    r"action|toolbar|operation|footer|copy|like|vote|share|btn|button", re.IGNORECASE
)
_WIDGET_NOISE_TEXTS = frozenset(
    {"复制", "点赞", "点踩", "踩", "重新生成", "分享", "收藏", "反馈", "更多", "查看详情"}
)
_WIDGET_MIN_LEN = 8


def _compose_answer_segments(segments: list[dict[str, Any]]) -> str:
    """容器走查的段列表 → 答案正文。markdown 段全收；widget 段（富文本卡片）
    过滤工具栏/按钮噪声后按文档序拼接。空段丢弃。"""
    parts: list[str] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        if seg.get("kind") == "widget":
            squashed = re.sub(r"\s+", "", text)
            if len(text) < _WIDGET_MIN_LEN or squashed in _WIDGET_NOISE_TEXTS:
                continue
            if _WIDGET_NOISE_CLS_RE.search(str(seg.get("cls") or "")):
                continue
        parts.append(text)
    return "\n".join(parts).strip()


def _extract_response(page: Any) -> tuple[str, list[dict[str, Any]]]:
    """DOM 抽取：容器级走查（markdown 段 + 富文本卡片段）优先；结构剧变时
    回退旧选择器链（猜测候选）。引用锚点取答案容器内全部 http 链接。"""
    try:
        raw = page.evaluate(_ANSWER_EXTRACT_JS)
    except Exception:
        raw = None
    if isinstance(raw, dict):
        text = _compose_answer_segments(raw.get("segments") or [])
        refs = [r for r in (raw.get("refs") or []) if isinstance(r, dict) and r.get("url")]
        if text:
            return _trim_response(text), refs
    # 旧选择器链兜底：.qk-markdown/.answer-common-card 已被 JS 路径覆盖，
    # 这里只剩结构猜测候选（UI 大改时的保命通道）。
    for sel in _ASSISTANT_SELECTORS[2:]:
        try:
            elements = page.locator(sel).all()
            if not elements:
                continue
            last = elements[-1]
            text = last.evaluate(_ELEMENT_TEXT_JS, timeout=2000) or ""
            if text and text.strip():
                refs = []
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


# 思考流程卡探针（20260810 live 实证结构，存档 /tmp/probe11-page.html）：
# deep_think（思考研究）答案 wrap 内 data-card_name="bar_workflow" 卡在前、
# 答案卡在后。折叠容器（grid-rows-[0fr] opacity-0）textContent 零交互可读。
# 每步骤 = class 恰为 "flex gap-1" 的 div（标题行等复合类不入选）：标题 =
# div.flex.items-center.gap-1.text-sm.font-semibold；思考正文 = div[class*=
# "thinking-content-"]；检索词 = 可见副本 div.mb-2.flex.flex-wrap.items-center
# 内带引号 span（strip 一层引号）；检索结果 = 可见副本 div.flex.flex-wrap.
# gap-x-1.gap-y-2 内 a[href]>span.truncate（无锚点回退 span.truncate 文本，
# url=None 诚实缺省）。检索词/结果两容器各有 invisible absolute 隐藏副本——
# 只取可见副本并按文本去重。
_THINKING_EXTRACT_JS = r"""() => {
  const out = {card_found: false, steps: [], queries: []};
  const stripQ = (s) => {
    let t = (s || '').trim();
    if (t.length >= 2) {
      const a = t[0], b = t[t.length - 1];
      if ((a === '"' && b === '"') || (a === '“' && b === '”')) {
        t = t.slice(1, -1).trim();
      }
    }
    return t;
  };
  const wraps = document.querySelectorAll('div[data-chat-answers-wrap]');
  if (!wraps.length) return out;
  const card = wraps[wraps.length - 1].querySelector('div[data-card_name="bar_workflow"]');
  if (!card) return out;
  out.card_found = true;
  for (const stepEl of card.querySelectorAll('div.flex.gap-1')) {
    const toks = (stepEl.className || '').trim().split(/\s+/).filter(Boolean).sort();
    if (toks.join(' ') !== 'flex gap-1') continue;  // 步骤容器恰为 "flex gap-1"
    const titleEl = stepEl.querySelector(
      'div.flex.items-center.gap-1.text-sm.font-semibold');
    const title = titleEl ? (titleEl.textContent || '').trim() : '';
    const queries = [];
    for (const box of stepEl.querySelectorAll('div.mb-2.flex.flex-wrap.items-center')) {
      if ((box.className || '').includes('invisible')) continue;  // 隐藏副本跳过
      for (const span of box.querySelectorAll(':scope > span')) {
        const q = stripQ(span.textContent);
        if (q && !queries.includes(q)) queries.push(q);
      }
    }
    const results = [];
    for (const box of stepEl.querySelectorAll('div.flex.flex-wrap.gap-x-1.gap-y-2')) {
      if ((box.className || '').includes('invisible')) continue;  // 隐藏副本跳过
      const anchors = box.querySelectorAll('a[href^="http"]');
      if (anchors.length) {
        for (const a of anchors) {
          const tEl = a.querySelector('span.truncate');
          const title = ((tEl ? tEl.textContent : a.textContent) || '').trim();
          const url = a.getAttribute('href') || null;
          if (!title) continue;
          results.push({title, url});
        }
      } else {
        for (const s of box.querySelectorAll('span.truncate')) {
          const title = (s.textContent || '').trim();
          if (!title) continue;
          results.push({title, url: null});
        }
      }
    }
    if (queries.length || results.length) {
      out.steps.push({kind: 'search', title, queries, results});
      for (const q of queries) if (!out.queries.includes(q)) out.queries.push(q);
      continue;
    }
    const thinkEl = stepEl.querySelector('div[class*="thinking-content-"]');
    out.steps.push({
      kind: 'reasoning',
      title,
      text: thinkEl ? (thinkEl.textContent || '').trim() : '',
    });
  }
  return out;
}"""

_THINKING_TEXT_LIMIT = 5_000  # 单段思考正文截断上限（对齐豆包水位）


def _empty_thinking() -> dict[str, Any]:
    """思考流程卡缺货/探针失败的统一空形状（每次新建，绝不可共享可变列表）。"""
    return {"card_found": False, "steps": [], "queries": [], "thinking_text": ""}


def _extract_tongyi_thinking(page: Any) -> dict[str, Any]:
    """deep_think（思考研究）思考流程卡抽取 → {card_found, steps, queries,
    thinking_text}。steps 词表：{kind:"reasoning", title, text} /
    {kind:"search", title, queries[], results[]}。

    零合成：无 bar_workflow 卡 / 探针异常 / 结构漂移一律返回空（诚实缺省，
    绝不编造思考链）。reasoning 正文单段截 _THINKING_TEXT_LIMIT 字符（对齐
    豆包水位）；thinking_text 由截断后的 reasoning 步骤（标题+正文）聚合。
    """
    try:
        raw = page.evaluate(_THINKING_EXTRACT_JS)
    except Exception:
        return _empty_thinking()
    if not isinstance(raw, dict) or not raw.get("card_found"):
        return _empty_thinking()
    steps: list[dict[str, Any]] = []
    queries: list[str] = []
    for entry in raw.get("steps") or []:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if entry.get("kind") == "search":
            step_queries = [
                q for q in (str(x or "").strip() for x in entry.get("queries") or []) if q
            ]
            results: list[dict[str, Any]] = []
            for r in entry.get("results") or []:
                if not isinstance(r, dict):
                    continue
                r_title = str(r.get("title") or "").strip()
                if not r_title:
                    continue
                r_url = str(r.get("url") or "").strip() or None
                results.append({"title": r_title, "url": r_url})
            steps.append(
                {"kind": "search", "title": title, "queries": step_queries, "results": results}
            )
            for q in step_queries:
                if q not in queries:
                    queries.append(q)
        elif entry.get("kind") == "reasoning":
            text = str(entry.get("text") or "").strip()[:_THINKING_TEXT_LIMIT]
            steps.append({"kind": "reasoning", "title": title, "text": text})
    thinking_parts = [
        f"{s['title']}\n{s['text']}".strip() if s["title"] else s["text"]
        for s in steps
        if s["kind"] == "reasoning" and s["text"]
    ]
    return {
        "card_found": True,
        "steps": steps,
        "queries": queries,
        "thinking_text": "\n".join(p for p in thinking_parts if p),
    }


def _build_tongyi_trace(
    references: list[dict[str, Any]], *, thinking: dict[str, Any] | None = None
) -> dict[str, Any]:
    """引用卡片 + 思考流程卡 → trace record（kind="sse" 证据内容，词表对齐其余
    四平台：collection router 的 build_task_trace_view 消费同一词表）。

    transport="dom" 如实标注：千问流只当完成信号（CDP 不读 body），引用卡片与
    思考流程卡均来自 DOM 实渲染。deep_think 时思考链/检索词/检索结果折叠自
    ``_extract_tongyi_thinking``（reasoning 步骤 → {kind:"reasoning", text:
    标题+正文}；搜索步骤 → {kind:"search", queries, summary: 标题}，其
    results 折叠为独立 search_block）；``deep_think_active`` 以实际抽到思考卡
    为准（卡片缺货 = False 诚实标注，绝不按请求模式硬标）。references 单独保存
    为 ``answer_reference_pages``，不能冒充完整 U 候选。normal（thinking=None）
    因此保持 U/V 不可观察，只保存最终引用。
    """
    thinking_chain: list[dict[str, Any]] = []
    search_blocks: list[dict[str, Any]] = []
    queries: list[str] = []
    deep_think_active = False
    if thinking:
        deep_think_active = bool(thinking.get("card_found"))
        for step in thinking.get("steps") or []:
            if step.get("kind") == "reasoning":
                title = str(step.get("title") or "").strip()
                text = str(step.get("text") or "").strip()
                combined = f"{title}\n{text}".strip() if title else text
                if combined:
                    thinking_chain.append({"kind": "reasoning", "text": combined})
            elif step.get("kind") == "search":
                step_queries = [str(q) for q in step.get("queries") or []]
                title = str(step.get("title") or "").strip()
                thinking_chain.append({"kind": "search", "queries": step_queries, "summary": title})
                results = [
                    {
                        "title": str(r.get("title") or "未命名来源"),
                        "url": r.get("url"),
                        "site": None,
                        "rank": index,
                        "summary": "",
                    }
                    for index, r in enumerate(step.get("results") or [], 1)
                    if isinstance(r, dict)
                ]
                if step_queries or results:
                    search_blocks.append(
                        {
                            "scene": None,
                            "queries": step_queries,
                            "summary": title,
                            "results": results,
                        }
                    )
        queries = [str(q) for q in thinking.get("queries") or []]
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
        "engine": "tongyi",
        "transport": "dom",
        "deep_think_active": deep_think_active,
        "thinking_chain": thinking_chain,
        "search_blocks": search_blocks,
        "queries": queries,
        "opened_pages_observed": False,
        "opened_pages": [],
        "answer_reference_pages": answer_reference_pages,
    }


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
