"""W3 拉踩检测：确定性切窗 + 窗级判定的纯函数层。

需求规格：developlog/specs/geo-evaluation-improvement-20260805.md W3 节。

两阶段判定：

- 阶段一（本模块）：确定性切窗。对答案/信源正文/己方稿件（own_content）按
  品牌+竞品提及（±200 字符窗）与 竞品共现（≥2 个竞品名、间距上限内合并为一窗）
  切窗；窗级去重靠 (subject_pub_id, window_hash, target_brand)。
- 阶段二：窗级 LLM 判定（schema/校验在本模块，传输在
  workflows/activities/disparagement.py）。evidence_quote 逐字子串程序校验，
  不过则丢弃判分；LLM 不可用 → 词典弱判定兜底并标 experimental
  （server/proxyllm/geo_scoring.py 小词典先例）。

与既有粗 sentiment 的关系：保留 analyzer 的粗 sentiment 不动，拉踩是独立细粒度
层，不混口径（规格 W3.4）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

WINDOW_RADIUS = 200  # 提及窗半径（字符，规格 W3.1）
PAIR_SPAN = 600  # 竞品共现合并窗：两个竞品提及间距上限（字符）

# own_content = 己方稿件正文（SOP article version 定稿通道，judge_own_content_disparagement）
SUBJECT_TYPES = ("answer", "source_document", "own_content")
ATTITUDES = ("support", "neutral", "negative")

METHOD_LLM = "llm"
METHOD_DICTIONARY = "dictionary_experimental"
PROMPT_VERSION = "disparage-v1"
DICTIONARY_VERSION = "dictionary-v1"

# 判定状态词表：ok / validation_failure（LLM 失败→词典兜底，不单列 llm_error 状态行）
JUDGMENT_STATUSES = ("ok", "validation_failure")

# ---------------------------------------------------------------------------
# 切窗
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Window:
    """一个待判定提及窗。window_hash = 空白归一化窗文本的 sha256。"""

    subject_type: str  # answer | source_document
    subject_pub_id: str
    platform: str  # answer → 采集 model；source_document → host
    source_url: str  # source_document → url；answer → ""
    target_brand: str
    kind: str  # mention | competitor_pair
    text: str
    window_hash: str


def normalize_verbatim(text: str) -> str:
    """空白归一化：所有空白串（含换行/全角空格）压成单空格、首尾 strip。"""
    return _WS_RUN_RE.sub(" ", text.replace("　", " ")).strip()


_WS_RUN_RE = re.compile(r"\s+")


def window_text_hash(text: str) -> str:
    """窗级幂等 hash：空白归一化后的 sha256（同窗文本重切不重复判定）。"""
    return sha256(normalize_verbatim(text).encode()).hexdigest()


def quote_is_verbatim(quote: str, blob: str) -> bool:
    """quote 归一化后必须是 blob 归一化后的逐字子串（空 quote 不算命中）。"""
    needle = normalize_verbatim(quote)
    if not needle:
        return False
    return needle in normalize_verbatim(blob)


def _occurrences(text: str, term: str) -> list[tuple[int, int]]:
    """term 在 text 中的全部 (start, end)（casefold 子串匹配，与 analyzer 提及口径一致）。"""
    haystack = text.casefold()
    needle = term.casefold()
    hits: list[tuple[int, int]] = []
    start = 0
    while needle:
        index = haystack.find(needle, start)
        if index < 0:
            break
        hits.append((index, index + len(term)))
        start = index + len(needle)
    return hits


def extract_windows(
    *,
    subject_type: str,
    subject_pub_id: str,
    text: str,
    brand: str | None,
    competitors: tuple[str, ...],
    platform: str,
    source_url: str = "",
    radius: int = WINDOW_RADIUS,
    pair_span: int = PAIR_SPAN,
) -> list[Window]:
    """对一段文本确定性切窗（纯函数）。

    - 提及窗：目标品牌+每个竞品的每处提及，±radius 字符；相邻提及落在已切窗内
      的跳过（窗不重叠漂移，控制长文窗量）。
    - 竞品共现窗：无序竞品对 (c1, c2) 最近一对提及间距 ≤ pair_span 时，切一扇
      覆盖两提及的合并窗，对 c1、c2 各产一窗（target 不同，同窗文本 hash 幂等
      不冲突）。拉踩常是"A 好 B 差"同框比较，单提及窗可能装不下对照对象。
    """
    if subject_type not in SUBJECT_TYPES:
        raise ValueError(f"非法 subject_type: {subject_type!r}")
    if not text.strip():
        return []
    tracked: list[str] = []
    for name in ([brand] if brand else []) + list(competitors):
        cleaned = (name or "").strip()
        if cleaned and cleaned not in tracked:
            tracked.append(cleaned)

    windows: list[Window] = []

    def _emit(target: str, kind: str, start: int, end: int) -> None:
        snippet = text[max(0, start) : min(len(text), end)]
        if not snippet.strip():
            return
        windows.append(
            Window(
                subject_type=subject_type,
                subject_pub_id=subject_pub_id,
                platform=platform,
                source_url=source_url,
                target_brand=target,
                kind=kind,
                text=snippet,
                window_hash=window_text_hash(snippet),
            )
        )

    occurrence_map: dict[str, list[tuple[int, int]]] = {}
    for name in tracked:
        hits = _occurrences(text, name)
        occurrence_map[name] = hits
        covered_until = -1
        for start, end in hits:
            if start < covered_until:
                continue  # 该提及已落在上一扇窗内
            _emit(name, "mention", start - radius, end + radius)
            covered_until = end + radius

    competitor_set = {name.strip() for name in competitors if name.strip()}
    brand_name = (brand or "").strip()
    competitor_names = [
        name for name in tracked if name in competitor_set and name != brand_name
    ]
    for index, first in enumerate(competitor_names):
        for second in competitor_names[index + 1 :]:
            best: tuple[int, int, int] | None = None  # (gap, span_start, span_end)
            for s1, e1 in occurrence_map.get(first, []):
                for s2, e2 in occurrence_map.get(second, []):
                    gap = max(0, max(s1, s2) - min(e1, e2))
                    if best is None or gap < best[0]:
                        best = (gap, min(s1, s2), max(e1, e2))
            if best is None or best[0] > pair_span:
                continue
            _, span_start, span_end = best
            for target in (first, second):
                _emit(target, "competitor_pair", span_start - radius, span_end + radius)
    return windows


def dedupe_windows(windows: list[Window]) -> list[Window]:
    """窗级去重：(subject_pub_id, window_hash, target_brand) 相同只留第一扇。"""
    seen: set[tuple[str, str, str]] = set()
    unique: list[Window] = []
    for window in windows:
        key = (window.subject_pub_id, window.window_hash, window.target_brand)
        if key in seen:
            continue
        seen.add(key)
        unique.append(window)
    return unique


# ---------------------------------------------------------------------------
# LLM 判定 schema + 程序校验
# ---------------------------------------------------------------------------

JUDGMENT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "target": {"type": "string"},
        "attitude": {"type": "string", "enum": list(ATTITUDES)},
        "disparagement": {"type": "boolean"},
        "evidence_quote": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": [
        "subject",
        "target",
        "attitude",
        "disparagement",
        "evidence_quote",
        "confidence",
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class LlmJudgment:
    """窗级 LLM 判定结果（schema 见 JUDGMENT_JSON_SCHEMA）。

    subject：表态主体——文中若把观点归于另一品牌（拉踩方）则填该品牌名，
    否则为空字符串（= 文本/平台本身）。target：被评价品牌（必须回显窗的
    target_brand）。
    """

    subject: str
    target: str
    attitude: str  # support | neutral | negative
    disparagement: bool
    evidence_quote: str
    confidence: float


def validate_judgment(
    judgment: LlmJudgment,
    *,
    window_text: str,
    expected_target: str,
    known_brands: tuple[str, ...],
) -> str | None:
    """判分程序校验 → None=通过；否则返回失败原因（判分必须丢弃）。

    - attitude 必须合法；target 必须回显窗的 target_brand；
    - subject 为空（文本本身）或已知品牌之一，且判拉踩时不得与 target 相同；
    - disparagement=true 仅在 attitude=negative 时成立（support/neutral + 拉踩
      是自相矛盾，按校验失败处理，绝不静默改写）；
    - confidence ∈ [0,1]；
    - evidence_quote 非空且为窗文本逐字子串（空白归一化后比对）。
    """
    if judgment.attitude not in ATTITUDES:
        return f"attitude 非法: {judgment.attitude!r}"
    if judgment.target != expected_target:
        return f"target 未回显窗目标: {judgment.target!r} != {expected_target!r}"
    if judgment.subject and judgment.subject not in known_brands:
        return f"subject 非已知品牌: {judgment.subject!r}"
    if judgment.disparagement:
        if judgment.attitude != "negative":
            return "disparagement=true 但 attitude 非 negative（自相矛盾）"
        if judgment.subject == judgment.target:
            return "disparagement=true 但 subject 与 target 相同"
    if not 0.0 <= judgment.confidence <= 1.0:
        return f"confidence 越界: {judgment.confidence!r}"
    if not quote_is_verbatim(judgment.evidence_quote, window_text):
        return "evidence_quote 非窗文本逐字子串"
    return None


# ---------------------------------------------------------------------------
# 词典弱判定兜底（geo_scoring 小词典先例；标 experimental，仅供 LLM 不可用降级）
# ---------------------------------------------------------------------------

# 口径：server/proxyllm/geo_scoring.py _POS_LEX/_NEG_LEX 扩展，补拉踩高频比较词。
SUPPORT_WORDS = frozenset(
    {
        "推荐", "首选", "值得", "优质", "口碑", "好评", "出色", "优秀", "领先",
        "靠谱", "不错", "信赖", "最佳", "顶级", "强烈推荐", "优于", "胜过",
        "更强", "划算", "良心",
    }
)
NEGATIVE_WORDS = frozenset(
    {
        "不推荐", "不建议", "差评", "失望", "劣质", "投诉", "避雷", "不值",
        "不如", "落后", "过时", "翻车", "踩雷", "坑", "缩水", "堪忧", "逊色",
        "垫底", "质疑", "诟病", "偏贵", "繁琐",
    }
)


@dataclass(frozen=True, slots=True)
class DictionaryJudgment:
    attitude: str
    disparagement: bool
    evidence_quote: str  # 命中的主导侧词典词（逐字必然在窗内）；无命中为 ""
    confidence: float
    matched_words: tuple[str, ...]


def brands_in_window(window_text: str, known_brands: tuple[str, ...]) -> tuple[str, ...]:
    """窗内出现的已知品牌（casefold 子串，与切窗口径一致）。"""
    return tuple(name for name in known_brands if _occurrences(window_text, name))


def dictionary_judge(
    window_text: str,
    *,
    target_brand: str,
    known_brands: tuple[str, ...],
) -> DictionaryJudgment:
    """词典弱判定（LLM 不可用时兜底；结果必须标 method=dictionary_experimental）。

    规则：分别计数 support/negative 词命中；多者定 attitude（持平 → neutral）。
    disparagement 仅当 attitude=negative 且窗内同时出现 target 之外的品牌
    （有比较对象才算"踩"）时置真。evidence_quote 取主导侧第一个命中词
    （逐字必然命中窗文本）。confidence 随命中数 0.3 起、每词 +0.1、封顶 0.6
    —— 词典法天生弱证据，置信度硬封顶低于 LLM 路径。
    """
    support_hits = sorted(word for word in SUPPORT_WORDS if word in window_text)
    negative_hits = sorted(word for word in NEGATIVE_WORDS if word in window_text)
    if len(negative_hits) > len(support_hits):
        attitude = "negative"
        dominant = negative_hits
    elif len(support_hits) > len(negative_hits):
        attitude = "support"
        dominant = support_hits
    else:
        attitude = "neutral"
        dominant = []
    others = [name for name in brands_in_window(window_text, known_brands) if name != target_brand]
    disparagement = attitude == "negative" and bool(others)
    confidence = min(0.6, 0.3 + 0.1 * len(dominant))
    return DictionaryJudgment(
        attitude=attitude,
        disparagement=disparagement,
        evidence_quote=dominant[0] if dominant else "",
        confidence=confidence,
        matched_words=tuple(support_hits + negative_hits),
    )


def clamp_window_limit(
    raw: str | None, *, default: int = 50, hard_min: int = 1, hard_max: int = 200
) -> int:
    """GEO_DISPARAGEMENT_WINDOW_LIMIT 解析：缺省 50，硬夹 1..200，坏值回落缺省。"""
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return max(hard_min, min(hard_max, value))
