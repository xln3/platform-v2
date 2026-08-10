"""W5 纯文本工具：归一化 / 意图分类 / 近义聚类 / 回答问句抽取。

全部为确定性纯函数（无 DB、无 LLM），单元测试直接锁定口径：
  * 归一化：去空白/标点/句尾语气词，ASCII 小写——聚类与幂等键共用一个口径；
  * 意图分类：关键词规则 v1，固定优先级 对比>选购>口碑>场景>推荐>地域，第一命中即返回，
    全部未命中 → "未分类"（如实落桶，绝不硬猜）；
  * 聚类：字符 3-gram Jaccard >= CLUSTER_SIMILARITY_THRESHOLD 归同簇（stdlib 实现）；
  * 问句抽取：切句 + 疑问词/问号规则 + 长度过滤，专挑"用户口吻"问法。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

INTENTS: tuple[str, ...] = ("推荐", "对比", "选购", "场景", "口碑", "地域")
UNCLASSIFIED = "未分类"

# 聚类相似度阈值（可配常量）：3-gram Jaccard >= 0.75 视为同簇。
CLUSTER_SIMILARITY_THRESHOLD = 0.75
NGRAM_SIZE = 3

# 意图关键词规则 v1：按优先级排列，自上而下第一命中胜出。
# 口径刻意保守——宁落"未分类"也不错归。
_INTENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("对比", ("对比", "比较", "哪个好", "哪家好", "哪个更好", "区别", "差别", "vs", "还是")),
    (
        "选购",
        (
            "怎么选",
            "如何选",
            "选购",
            "怎么买",
            "如何买",
            "买哪个",
            "多少钱",
            "价格",
            "价位",
            "费用",
            "性价比",
            "值得买",
            "值得入手",
        ),
    ),
    (
        "口碑",
        (
            "口碑",
            "怎么样",
            "靠谱",
            "评价",
            "评分",
            "踩雷",
            "避坑",
            "吐槽",
            "差评",
            "好评",
            "红黑榜",
        ),
    ),
    (
        "场景",
        (
            "场景",
            "适合",
            "能不能",
            "可以用吗",
            "能用吗",
            "怎么用",
            "如何使用",
            "教程",
            "攻略",
            "流程",
            "条件",
            "门槛",
        ),
    ),
    ("推荐", ("推荐", "排行", "排名", "十大", "有哪些", "求推荐", "哪个牌子", "哪些品牌")),
)
# 地域意图：文本命中项目配置地域词（由调用方传入）或含泛指地域词。
_REGION_GENERIC_MARKERS = (
    "附近",
    "本地",
    "当地",
    "哪里有",
    "在哪儿",
    "在哪里",
    "哪个城市",
    "哪些地区",
    "哪个地区",
)

_PUNCT_PATTERN = re.compile(r"[？?！!。，,、；;：:\"'“”‘’（）()【】\[\]…—\-~·<>《》\s]+")
_TRAILING_MODAL = ("呢", "吗", "嘛", "啊", "呀", "吧", "啦", "哦", "噢")
_SENTENCE_SPLIT = re.compile(r"([。？！?!；;，,、\n])")
_LEADING_BULLET = re.compile(r"^(?:\d{1,2}[.、)]|[-*•]|\(?[一二三四五六七八九十]\)?[、.])\s*")
_INTERROGATIVE_START = re.compile(
    r"^(怎么|怎样|如何|什么|哪个|哪些|哪里|哪儿|谁|是否|是不是|能不能|能够|有没有|"
    r"可不可以|为什么|为何|多少|哪家|哪类|哪种|求)"
)
_QUESTION_MIN_LEN = 4
_QUESTION_MAX_LEN = 60


def normalize_query(text: str) -> str:
    """归一化：去标点/空白/句尾语气词，ASCII 小写。聚类与幂等键 (project, normalized) 共用。"""
    value = _PUNCT_PATTERN.sub("", text.strip().lower())
    changed = True
    while changed and value:
        changed = False
        for modal in _TRAILING_MODAL:
            if value.endswith(modal) and len(value) > len(modal):
                value = value[: -len(modal)]
                changed = True
    return value


def classify_intent(text: str, regions: tuple[str, ...] = ()) -> str:
    """关键词规则 v1：固定优先级第一命中；命中地域词/泛指地域词 → 地域；否则未分类。"""
    for intent, keywords in _INTENT_RULES:
        lowered = text.lower()
        if any(keyword in lowered for keyword in keywords):
            return intent
    if any(region and region in text for region in regions):
        return "地域"
    if any(marker in text for marker in _REGION_GENERIC_MARKERS):
        return "地域"
    return UNCLASSIFIED


def _ngrams(text: str, n: int = NGRAM_SIZE) -> frozenset[str]:
    if len(text) < n:
        return frozenset({text}) if text else frozenset()
    return frozenset(text[i : i + n] for i in range(len(text) - n + 1))


def jaccard(a: str, b: str) -> float:
    """两段归一化文本的字符 3-gram Jaccard 相似度。"""
    set_a, set_b = _ngrams(a), _ngrams(b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


@dataclass(frozen=True)
class Cluster:
    """近义簇：representative 为簇代表（用量最高者，并列取短者），members 含代表本身。"""

    representative: str
    members: tuple[str, ...]
    total_count: int
    cluster_id: str


def cluster_id_for(normalized_representative: str) -> str:
    return "clu_" + hashlib.sha1(normalized_representative.encode()).hexdigest()[:12]


def cluster_texts(
    items: list[tuple[str, int]], threshold: float = CLUSTER_SIMILARITY_THRESHOLD
) -> list[Cluster]:
    """贪心近义聚类。

    items = [(原文, 用量计数)]；先归一化去重（累加计数），按 (-count, 长度, 文本) 排序后
    逐个归入第一个相似度 >= threshold 的簇（与簇代表比较），都不相似则自成一簇。
    """
    merged: dict[str, tuple[str, int]] = {}
    for text, count in items:
        normalized = normalize_query(text)
        if not normalized:
            continue
        if normalized in merged:
            kept_text, kept_count = merged[normalized]
            merged[normalized] = (kept_text, kept_count + count)
        else:
            merged[normalized] = (text.strip(), count)
    ordered = sorted(merged.items(), key=lambda kv: (-kv[1][1], len(kv[0]), kv[0]))
    clusters: list[_ClusterBuilder] = []
    for normalized, (text, count) in ordered:
        target: _ClusterBuilder | None = None
        for candidate in clusters:
            if jaccard(normalized, candidate.rep_normalized) >= threshold:
                target = candidate
                break
        if target is None:
            target = _ClusterBuilder(rep_normalized=normalized)
            clusters.append(target)
        target.members.append(text)
        target.total += count
    return [
        Cluster(
            representative=c.members[0],
            members=tuple(c.members),
            total_count=c.total,
            cluster_id=cluster_id_for(c.rep_normalized),
        )
        for c in clusters
    ]


@dataclass
class _ClusterBuilder:
    rep_normalized: str
    members: list[str] = field(default_factory=list)
    total: int = 0


def extract_user_questions(answer_text: str) -> list[str]:
    """从 AI 回答正文抽"用户口吻"问句（确定性规则，标 source="answer_mining"）。

    口径：按句切分 → 去项目符号/编号 → 命中「以疑问词开头」或「原句带问号」→
    长度 [4, 60] 过滤 → 归一化去重。回答腔（反问/设问长句）大多被长度与疑问词
    起始规则滤掉；滤不掉的宁缺毋滥，宁少勿编。
    """
    found: dict[str, str] = {}
    # 切分保留分隔符再拼回：问号是疑问句的核心信号，不能被切分吞掉。
    parts = _SENTENCE_SPLIT.split(answer_text)
    sentences = [
        parts[index] + (parts[index + 1] if index + 1 < len(parts) else "")
        for index in range(0, len(parts), 2)
    ]
    for raw in sentences:
        candidate = _LEADING_BULLET.sub("", raw.strip()).strip("“”\"' ")
        if not (_QUESTION_MIN_LEN <= len(candidate) <= _QUESTION_MAX_LEN):
            continue
        has_question_mark = "？" in raw or "?" in raw
        if not (has_question_mark or _INTERROGATIVE_START.match(candidate)):
            continue
        normalized = normalize_query(candidate)
        if normalized and normalized not in found:
            found[normalized] = candidate.rstrip("？?").strip() + "？"
    return list(found.values())
