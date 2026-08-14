"""平台墙词表（配额/禁言/拒答）——答案文本验收门与禁言 banner 检测的唯一真源。

事故背景（2026-08-13 豆包）：账号配额耗尽/被禁言时，平台提示文案被当作正常
答案采回并落库——软墙 DOM 扫描被 ``if not answer_text:`` 门挡（出了"答案"就
绝不扫描），答案侧又无任何平台口吻校验。本模块把「平台自己说的话」词表化：

- ``classify_answer_text``：答案定稿后、返回 ok 之前的验收门（适配器调用）；
- ``detect_muted_banner``：composer 不可得路径的整页文本禁言检测。**只跑禁言
  regex**——整页文本含 UI 营销件（「开通会员」按钮等），配额/拒答词表套在整页
  上必然误伤，绝不复用。

误伤护栏（回归测试锚定）：

- 配额短词必须伴随平台语境（如「免费次数用完」须伴随「专家模式/专业版」）；
- 禁言 regex 锁定平台第二人称铁证（「已被禁言至 YYYY 年 M 月 D 日 HH:MM」带
  具体解封时间，或「违反…规范…已被禁言」完整句式）；
- 正常中文答案里出现「额度/次数/禁言/会员」等词（第三人称、条件句、科普句）
  绝不命中。

词表覆盖：doubao 为事故实证全量；deepseek/yiyan/yuanbao/tongyi 为保守子串
（与各 adapter 既有 ``_SOFTBAN_DOM_PHRASES`` 口径对齐复用），待 P1 attach
实证扩充。改词表必须同步 bump ``WALL_LEXICON_VERSION`` 并补回归测试。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

WALL_LEXICON_VERSION = "2026-08-14"

PLATFORMS: tuple[str, ...] = ("doubao", "deepseek", "yiyan", "yuanbao", "tongyi")


@dataclass(frozen=True)
class WallVerdict:
    """词表命中结论。``until`` 仅禁言带解封时间时有值（naive 本地时间，精确到分）。"""

    wall_type: str  # "wall_quota" | "wall_muted" | "wall_refusal"
    phrase: str
    until: datetime | None = None


@dataclass(frozen=True)
class _QuotaRule:
    """配额/付费墙文案规则：``phrase`` 完整子串命中，且 ``context`` 非空时任一
    语境子串须同现（短词防误伤护栏）；``context`` 为空 = 短语本身已足够特异。"""

    phrase: str
    context: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# 配额/付费墙（答案文本级）
# ---------------------------------------------------------------------------

QUOTA_PHRASES: dict[str, tuple[_QuotaRule, ...]] = {
    "common": (
        # 与各 adapter 既有 _SOFTBAN_DOM_PHRASES 对齐复用（平台第一人称完整句式）。
        _QuotaRule("今日对话次数已达", ()),
        _QuotaRule("对话次数已达上限", ()),
        _QuotaRule("免费次数已用完", ("今日", "明天", "明日", "开通")),
    ),
    "doubao": (
        # 2026-08-13 事故原文（live 实证，逐字子串）：
        # 「今日专家模式免费次数用完了，暂时无法使用专业版功能，先使用快速模式和
        #   我聊聊别的吧。开通豆包专业版，免等待，继续为你服务。」
        _QuotaRule("免费次数用完", ("专家模式", "专业版")),
        _QuotaRule("暂时无法使用专业版功能", ()),
        _QuotaRule("开通豆包专业版", ("免等待", "继续为你服务")),
    ),
    # 以下四平台为保守子串（常见付费/限流文案），待 P1 attach 实证扩充。
    "deepseek": (
        _QuotaRule("免费额度已用完", ("今日", "本月")),
    ),
    "yiyan": (
        _QuotaRule("今日额度已用完", ()),
        _QuotaRule("开通文心一言会员", ("畅享", "免等待", "无限")),
    ),
    "yuanbao": (
        _QuotaRule("今日免费对话次数已用完", ()),
    ),
    "tongyi": (
        _QuotaRule("体验次数已用完", ("今日", "开通")),
    ),
}

# ---------------------------------------------------------------------------
# 禁言/封禁（答案文本级 + 页面 banner 级共用；regex 锁定平台第二人称口吻）
# ---------------------------------------------------------------------------

# 事故原文（豆包 2026-08-13 live 实证）：
# 「由于违反用户使用规范，你的账号已被禁言至 2026 年 8 月 14 日 13:02，如有疑问
#   请联系我们。」
# 带具体解封日期时间 = 平台铁证（正常答案不会引用一个具体到分的自身禁言时间）。
_MUTED_UNTIL_RE = re.compile(
    r"已被禁言至\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
    r"(?:\s*(\d{1,2})\s*[:：]\s*(\d{2}))?"
)

MUTED_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "common": (
        _MUTED_UNTIL_RE,
        # 无日期变体：须与「违反…规范」完整句式同现（防「如果账号已被禁言…」
        # 类第三人称/条件句科普答案误伤）。
        re.compile(r"违反[^。！？\n]{0,12}规(?:范|定)[^。！？\n]{0,40}已被禁言"),
        # 封禁：第二人称平台口吻（「你的账号已被封禁」）。
        re.compile(r"(?:你的|您的)账号已被(?:永久)?封禁"),
    ),
    # 各平台特定变体待 P1 attach 实证扩充（当前共用 common 表）。
    "doubao": (),
    "deepseek": (),
    "yiyan": (),
    "yuanbao": (),
    "tongyi": (),
}

# ---------------------------------------------------------------------------
# 拒答模板（答案文本级；平台拒答本题 ≠ 账号墙，batch 不连坐）
# ---------------------------------------------------------------------------

REFUSAL_PHRASES: dict[str, tuple[str, ...]] = {
    "common": (
        # 过载拒答以完整平台模板留存（只留前半句会把「当前请求人数过多的问题
        # 可以通过扩容解决」类科普答案误伤——截断到「请稍后再试」才是平台口吻）。
        "当前请求人数过多，请稍后再试",
    ),
    "doubao": (
        "我们换个话题",
    ),
    # 以下四平台为保守子串（各平台经典拒答/过载文案），待 P1 attach 实证扩充。
    "deepseek": (
        "服务器繁忙，请稍后再试",
    ),
    "yiyan": (
        "很抱歉，我无法回答该问题",
    ),
    "yuanbao": (
        "很抱歉，我无法回答你的问题",
    ),
    "tongyi": (
        "很抱歉，我暂时无法回答",
    ),
}


def _platform_rules(table: dict[str, tuple], platform: str) -> tuple:
    """平台特定规则优先（证据更精确），通用表兜底；未知平台只用通用表。"""
    return table.get(platform, ()) + table.get("common", ())


def classify_answer_text(platform: str, text: str) -> WallVerdict | None:
    """答案验收门：平台提示文案（配额/禁言/拒答模板）被当作答案采回时命中。

    命中顺序：配额 → 禁言 → 拒答（互不重叠；任一命中即返回，证据取先中者）。
    正常答案（含「额度/次数/禁言」等词的第三人称句）必须返回 None。
    """
    if not text:
        return None
    for rule in _platform_rules(QUOTA_PHRASES, platform):
        if rule.phrase in text and (
            not rule.context or any(ctx in text for ctx in rule.context)
        ):
            return WallVerdict("wall_quota", rule.phrase)
    muted = detect_muted_banner(platform, text)
    if muted is not None:
        return muted
    for phrase in _platform_rules(REFUSAL_PHRASES, platform):
        if phrase in text:
            return WallVerdict("wall_refusal", phrase)
    return None


def detect_muted_banner(platform: str, page_text: str) -> WallVerdict | None:
    """页面/答案文本的禁言检测。只跑禁言 regex——整页文本含 UI 营销件
    （「开通会员」按钮等），配额/拒答词表套整页必然误伤，绝不在此复用。"""
    if not page_text:
        return None
    for pattern in _platform_rules(MUTED_PATTERNS, platform):
        match = pattern.search(page_text)
        if match is not None:
            return WallVerdict(
                "wall_muted", match.group(0), until=_parse_muted_until(match)
            )
    return None


def _parse_muted_until(match: re.Match[str]) -> datetime | None:
    """从「已被禁言至 YYYY 年 M 月 D 日[ HH:MM]」命中里解析解封时间（naive
    本地时间，精确到分；无时间成分按当日 00:00；非法日期/无分组 = None 不影响命中）。"""
    try:
        year, month, day = (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
    except (IndexError, ValueError):
        return None
    try:
        hour = int(match.group(4)) if match.group(4) else 0
        minute = int(match.group(5)) if match.group(5) else 0
    except (IndexError, ValueError):
        hour, minute = 0, 0
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None
