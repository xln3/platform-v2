"""W2 抓取层 activity（``fetch_run_sources``）：回答级信源正文抓取。

需求规格：developlog/specs/geo-evaluation-improvement-20260805.md W2 节。
workflow 挂接由协调者集成，本模块只提供 activity 与可单测核心：

- 每份回答独立规划引用 URL：优先有逐字引文的来源，再按 citation ordinal 稳定排序，
  每回答取前 N（``GEO_SOURCE_FETCH_LIMIT`` 缺省 200、硬夹 1..500）；全 run 另设
  ``GEO_SOURCE_FETCH_RUN_LIMIT`` 安全上限（缺省 20000、硬夹 1..50000）。相同 URL 只抓
  一次，但会扇出关联到每份引用它的回答，避免旧版“全 run Top-N”只覆盖最早回答。
- 每条：httpx 优先（15s 超时、正常浏览器 UA、跟随重定向、按域限速 2s）；
  正文 <200 字符 / 明显 JS 壳 / 超时 / 401·403·429 → patchright 浏览器回退（只取正文）。
  httpx 路径用 stdlib 密度抽取（``density-extract-v1``），浏览器路径 innerText
  （``innertext-v1``，与 own_site_snapshot 同款 JS），正文截 ≤20000 字符。
- 产物：正文 bytes 进 evidence CAS（kind=``source_text``，text/plain;charset=utf-8）+
  ``platform.source_document`` 行（(run_id,url_hash) 唯一幂等；重跑已存在的 URL 直接复用）。
- 品牌证据严格二次取证：只有抓到的 ``source_document`` 正文逐字包含项目
  ``Brand.name``/``brand_alias``，且浏览器 DOM 也定位到同一词时，才截取该词所在
  最小可读段落；只给原网页文字加红色矩形，不注入标题、摘要或说明浮层。截图以
  ``brand_mention_source_snapshot`` 关联答案，并持久化真实 bbox/quote_hash。

纪律：

- INV-32 零合成：抓不到就如实落 extract_status（http_error/timeout/blocked/extract_empty），
  绝不编造正文；CAS 只写真抽到的文本。
- 重放确定性：evidence_pub_id 按 ``sha256(tenant|run|url|kind|asset)`` 派生、
  source_document pub_id 按 ``sha256(tenant|run|url_hash)`` 派生、capture_time 用
  run.created_at（own_site_snapshot.py 同模式）；EvidenceService 漂移规则要求同
  evidence_pub_id 再 capture 全部字段一致，写入前先查既存资产直接复用。
- env：``GEO_SOURCE_FETCH_ENABLED``（缺省 true，false 时两 W2 activity 都 skipped=
  "disabled" 零 IO）；``GEO_SOURCE_FETCH_LIMIT``（每回答缺省 200、硬夹 1..500）；
  ``GEO_SOURCE_FETCH_RUN_LIMIT``（全 run 安全上限，缺省 20000、硬夹 1..50000）。
- 执行模型与 own_site_snapshot 同款：sync 抓取包在 ``asyncio.to_thread`` 里跑，
  activity 协程侧每 10s 泵一次 heartbeat；公开页普通 launch+new_context（无需登录态
  profile），驱动首选 patchright（browser_driver 延迟加载）。
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import psycopg
import structlog
from geo_platform.config import get_settings
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.evidence.service import EvidenceService
from PIL import Image
from psycopg.rows import dict_row
from temporalio import activity
from temporalio.exceptions import ApplicationError

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from workflows.activities.browser_driver import load_sync_browser_driver

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# env 配置
# ---------------------------------------------------------------------------

ENV_ENABLED = "GEO_SOURCE_FETCH_ENABLED"
ENV_FETCH_LIMIT = "GEO_SOURCE_FETCH_LIMIT"
ENV_RUN_LIMIT = "GEO_SOURCE_FETCH_RUN_LIMIT"

_DEFAULT_LIMIT = 200
_HARD_CAP_LIMIT = 500
_DEFAULT_RUN_LIMIT = 20_000
_HARD_CAP_RUN_LIMIT = 50_000

_HEARTBEAT_INTERVAL_S = 10.0  # 与 own_site_snapshot 同款泵频
_HTTP_TIMEOUT_S = 15.0
_PER_DOMAIN_DELAY_S = 2.0  # 限速纪律：同域请求间隔 ≥2s（抓取触发风控风险，规格明示）
_GOTO_TIMEOUT_MS = 20_000
_SETTLE_MS = 1_500
_MIN_TEXT_CHARS = 200  # 低于此视为 JS 壳/空页 → 浏览器回退
_MAX_TEXT_CHARS = 20_000

_EVIDENCE_KIND = "source_text"
_EVIDENCE_ASSET = "text"
_ADAPTER_VERSION = "source-fetch-v2"
_BRAND_EVIDENCE_KIND = "source_screenshot"
_BRAND_EVIDENCE_RELATION = "brand_mention_source_snapshot"
_BRAND_EVIDENCE_ADAPTER_VERSION = "source-fetch-brand-mention-v1"
_EXTRACTOR_HTTPX = "density-extract-v1"
_EXTRACTOR_BROWSER = "innertext-v1"

# extract_status 词表：ok / http_error / timeout / blocked / extract_empty / fetch_skipped
# （fetch_skipped 保留给规划层跳过的 URL，当前规划直接丢弃不立项，词表保持规格全集）

# 与 own_site_snapshot.py 同值（旧链 doubao_client.py 实测 UA）
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_STATIC_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".webp",
        ".ico",
        ".bmp",
        ".css",
        ".js",
        ".mjs",
        ".map",
        ".pdf",
        ".zip",
        ".rar",
        ".7z",
        ".tar",
        ".gz",
        ".mp4",
        ".mp3",
        ".wav",
        ".avi",
        ".mov",
        ".webm",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".xml",
        ".txt",
        ".csv",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
    }
)


# ---------------------------------------------------------------------------
# activity 输入输出契约
# ---------------------------------------------------------------------------


@dataclass
class SourceFetchInput:
    tenant_pub_id: str
    project_pub_id: str
    run_pub_id: str


@dataclass
class FetchedSource:
    url: str
    extract_status: str
    source_document_pub_id: str
    bytes: int
    answer_pub_ids: tuple[str, ...] = ()
    brand_mention_captured: bool = False


@dataclass
class SourceFetchFailure:
    url: str
    error: str


@dataclass(frozen=True)
class SourceFetchPlanCoverage:
    """Per-answer planning audit; never presented as fetched-document counts."""

    answer_pub_id: str
    eligible_urls: int
    planned_urls: int
    truncated_urls: int
    coverage_rate: float | None
    truncation_reason: str | None


@dataclass
class SourceFetchResult:
    fetched: list[FetchedSource] = field(default_factory=list)
    failures: list[SourceFetchFailure] = field(default_factory=list)
    planning_coverage: list[SourceFetchPlanCoverage] = field(default_factory=list)
    per_answer_limit: int | None = None
    run_limit: int | None = None
    skipped: str | None = None  # "disabled" / None


@dataclass(frozen=True)
class SourceFetchConfig:
    enabled: bool
    limit: int  # 每份回答的正文抓取上限
    headless: bool = True
    run_limit: int = _DEFAULT_RUN_LIMIT

    @classmethod
    def from_env(cls) -> SourceFetchConfig:
        raw_enabled = os.environ.get(ENV_ENABLED, "").strip().lower()
        return cls(
            enabled=raw_enabled not in {"0", "false", "no", "off"},
            limit=_env_limit(),
            run_limit=_env_run_limit(),
        )


def _env_limit() -> int:
    raw = os.environ.get(ENV_FETCH_LIMIT, "").strip()
    if not raw:
        return _DEFAULT_LIMIT
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_LIMIT
    return max(1, min(value, _HARD_CAP_LIMIT))


def _env_run_limit() -> int:
    raw = os.environ.get(ENV_RUN_LIMIT, "").strip()
    if not raw:
        return _DEFAULT_RUN_LIMIT
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_RUN_LIMIT
    return max(1, min(value, _HARD_CAP_RUN_LIMIT))


# ---------------------------------------------------------------------------
# 目标规划（纯函数，全部可单测）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceTarget:
    url: str  # 实际抓取的 URL（保持被引用原文，不改写）
    key: str  # 归一化去重键
    url_hash: str  # sha256(key)，source_document 幂等键成分
    host: str
    task_pub_ids: tuple[str, ...]  # 引用此 URL 的回答；同 URL 只抓一次但关系扇出


def normalize_host(url: str) -> str | None:
    """URL → 归一化 host：剥 scheme、端口、www. 前缀、尾点，小写。无法解析 → None。"""
    candidate = url.strip()
    if not candidate:
        return None
    try:
        if "://" in candidate:
            host = urlsplit(candidate).hostname
        else:
            host = urlsplit(f"//{candidate}").hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None


def url_dedupe_key(url: str) -> str | None:
    """URL 去重键：小写 scheme/host、剥 www./默认端口/fragment、path 尾斜杠归一。"""
    try:
        parts = urlsplit(url.strip())
        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"}:
            return None
        host = normalize_host(url)
        if host is None:
            return None
        port = parts.port
    except ValueError:
        return None
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host if port is None or default_port else f"{host}:{port}"
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    key = f"{scheme}://{netloc}{path}"
    if parts.query:
        key = f"{key}?{parts.query}"
    return key


def is_static_resource_url(url: str) -> bool:
    """静态资源（图片/样式/脚本/文档等）抽不出正文，规划层直接丢弃。"""
    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        return True
    return any(path.endswith(ext) for ext in _STATIC_EXTENSIONS)


def plan_source_targets(
    tasks: list[tuple[str, list[dict[str, Any]]]],
    limit: int,
    run_limit: int = _DEFAULT_RUN_LIMIT,
) -> list[SourceTarget]:
    """按回答规划、跨回答去重，并保留 URL→全部引用回答的关系。

    每回答优先选择带 ``cited_text`` 的 URL，再按原 citation ordinal，最多
    ``limit`` 条；跨回答相同 URL 只生成一个抓取目标，但 ``task_pub_ids`` 会
    扇出。目标按回答轮询（每份回答先取第 1 条，再取第 2 条），避免
    ``run_limit`` 安全阀被最早回答独占。非 http(s)/不可归一化/静态资源 URL
    直接丢弃（不立项、不造假状态行）。
    """
    per_answer_limit = max(1, min(limit, _HARD_CAP_LIMIT))
    safe_run_limit = max(1, min(run_limit, _HARD_CAP_RUN_LIMIT))
    targets: list[SourceTarget] = []
    target_index_by_key: dict[str, int] = {}
    task_order = {task_pub_id: index for index, (task_pub_id, _citations) in enumerate(tasks)}
    first_url_by_key: dict[str, str] = {}
    planned_by_answer: list[tuple[str, list[tuple[int, int, str, str, str]]]] = []
    for task_pub_id, citations in tasks:
        candidates: list[tuple[int, int, str, str, str]] = []
        seen_for_answer: set[str] = set()
        for ordinal, citation in enumerate(citations):
            url = citation.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            key = url_dedupe_key(url)
            if key is None or key in seen_for_answer:
                continue
            if is_static_resource_url(key):
                continue
            host = normalize_host(key)
            if host is None:
                continue
            seen_for_answer.add(key)
            cited_text = citation.get("cited_text")
            priority = 0 if isinstance(cited_text, str) and cited_text.strip() else 1
            clean_url = url.strip()
            first_url_by_key.setdefault(key, clean_url)
            candidates.append((priority, ordinal, clean_url, key, host))
        planned_by_answer.append((task_pub_id, sorted(candidates)[:per_answer_limit]))

    # Round-robin by citation rank so a run safety cap cannot be monopolized by
    # the earliest answers.  First give each answer one planned source, then its
    # second source, and so on.
    for citation_rank in range(per_answer_limit):
        for task_pub_id, candidates in planned_by_answer:
            if citation_rank >= len(candidates):
                continue
            _priority, _ordinal, url, key, host = candidates[citation_rank]
            existing_index = target_index_by_key.get(key)
            if existing_index is not None:
                existing = targets[existing_index]
                if task_pub_id not in existing.task_pub_ids:
                    linked_task_pub_ids = tuple(
                        sorted(
                            (*existing.task_pub_ids, task_pub_id),
                            key=lambda value: task_order[value],
                        )
                    )
                    targets[existing_index] = SourceTarget(
                        url=existing.url,
                        key=existing.key,
                        url_hash=existing.url_hash,
                        host=existing.host,
                        task_pub_ids=linked_task_pub_ids,
                    )
                continue
            if len(targets) >= safe_run_limit:
                continue
            target_index_by_key[key] = len(targets)
            targets.append(
                SourceTarget(
                    url=first_url_by_key[key],
                    key=key,
                    url_hash=sha256(key.encode()).hexdigest(),
                    host=host,
                    task_pub_ids=(task_pub_id,),
                )
            )
    return targets


def source_plan_coverage(
    tasks: list[tuple[str, list[dict[str, Any]]]],
    targets: list[SourceTarget],
    *,
    limit: int,
    run_limit: int,
) -> list[SourceFetchPlanCoverage]:
    """Explain every answer's URL planning coverage and any protection truncation.

    Counts use unique, parseable, non-static HTTP(S) citation URLs.  They are
    planning counts, not fetch-success/document counts.  The result is returned by
    the Temporal activity, making configured protection effects durable and
    inspectable instead of a hidden Top-N.
    """

    per_answer_limit = max(1, min(limit, _HARD_CAP_LIMIT))
    safe_run_limit = max(1, min(run_limit, _HARD_CAP_RUN_LIMIT))
    planned_by_answer: dict[str, set[str]] = {answer_pub_id: set() for answer_pub_id, _ in tasks}
    for target in targets:
        for answer_pub_id in target.task_pub_ids:
            planned_by_answer.setdefault(answer_pub_id, set()).add(target.key)

    rows: list[SourceFetchPlanCoverage] = []
    for answer_pub_id, citations in tasks:
        eligible: set[str] = set()
        for citation in citations:
            url = citation.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            key = url_dedupe_key(url)
            if key is None or is_static_resource_url(key):
                continue
            eligible.add(key)
        eligible_count = len(eligible)
        planned_count = len(planned_by_answer.get(answer_pub_id, set()))
        reasons: list[str] = []
        if eligible_count > per_answer_limit:
            reasons.append("per_answer_limit")
        if planned_count < min(eligible_count, per_answer_limit) and len(targets) >= safe_run_limit:
            reasons.append("run_limit")
        rows.append(
            SourceFetchPlanCoverage(
                answer_pub_id=answer_pub_id,
                eligible_urls=eligible_count,
                planned_urls=planned_count,
                truncated_urls=max(0, eligible_count - planned_count),
                coverage_rate=(
                    round(planned_count / eligible_count, 4) if eligible_count else None
                ),
                truncation_reason="+".join(reasons) or None,
            )
        )
    return rows


def derive_document_pub_id(tenant_pub_id: str, run_pub_id: str, url_hash: str) -> str:
    """source_document pub_id 确定性派生：同 (tenant,run,url_hash) 必同 id。"""
    stable_key = "|".join((tenant_pub_id, run_pub_id, url_hash))
    return f"srd_{sha256(stable_key.encode()).hexdigest()[:26]}"


def derive_evidence_pub_id(tenant_pub_id: str, run_pub_id: str, url: str) -> str:
    """确定性派生（own_site_snapshot 同模式）：同 (tenant,run,url,kind,asset) 必同 id。"""
    stable_key = "|".join((tenant_pub_id, run_pub_id, url, _EVIDENCE_KIND, _EVIDENCE_ASSET))
    return f"evd_{sha256(stable_key.encode()).hexdigest()[:26]}"


def derive_brand_evidence_pub_id(tenant_pub_id: str, run_pub_id: str, url_hash: str) -> str:
    """One deterministic brand-mention screenshot occurrence per run/source URL."""

    stable_key = "|".join((tenant_pub_id, run_pub_id, url_hash, _BRAND_EVIDENCE_RELATION, "png"))
    return f"evd_{sha256(stable_key.encode()).hexdigest()[:26]}"


def derive_brand_anchor_pub_id(evidence_pub_id: str, matched_text: str) -> str:
    stable_key = "|".join((evidence_pub_id, matched_text.casefold(), "dom-range-v1"))
    return f"anch_{sha256(stable_key.encode()).hexdigest()[:26]}"


# ---------------------------------------------------------------------------
# stdlib 正文密度抽取 v1（无 readability/bs4 依赖，html.parser + 块聚合）
# ---------------------------------------------------------------------------

# 整块丢弃的元素（脚本/样式/导航/页脚/表单等 chrome）
_SKIP_TAGS = frozenset(
    {"script", "style", "noscript", "template", "svg", "nav", "footer", "header", "aside", "form"}
)
# 块级元素：结束标签处切断成块
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "article",
        "section",
        "main",
        "li",
        "ul",
        "ol",
        "table",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "figure",
        "br",
    }
)
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
# 实质块阈值：短块多为导航残留/免责声明，密度优先时丢弃
_SUBSTANTIVE_BLOCK_CHARS = 40

_INLINE_WS_RE = re.compile(r"[ \t　]+")
_BLANK_RUN_RE = re.compile(r"\n{3,}")


class _BlockTextParser(HTMLParser):
    """跳过 chrome 子树、按块级元素切块的纯 stdlib 文本抽取器。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._current: list[str] = []
        self.blocks: list[tuple[str, bool]] = []  # (文本, 是否标题)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in _BLOCK_TAGS:
            self._flush(tag in _HEADING_TAGS)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in _BLOCK_TAGS:
            self._flush(tag in _HEADING_TAGS)

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._current.append(data)

    def _flush(self, heading: bool) -> None:
        text = _INLINE_WS_RE.sub(" ", "".join(self._current)).strip()
        self._current = []
        if text:
            self.blocks.append((text, heading))

    def close(self) -> None:
        super().close()
        self._flush(False)


def clean_text(raw: str, limit: int = _MAX_TEXT_CHARS) -> str:
    """去多余空白（行内空白压单格、空行压一行、行首尾 strip），截 ≤20000 字符。"""
    lines = [_INLINE_WS_RE.sub(" ", line).strip() for line in raw.splitlines()]
    text = "\n".join(lines)
    text = _BLANK_RUN_RE.sub("\n\n", text).strip()
    return text[:limit]


def extract_text_from_html(html: str, limit: int = _MAX_TEXT_CHARS) -> str:
    """密度抽取 v1：去 chrome 后实质块（≥40 字符或标题）聚合；无实质块回退全部块。"""
    parser = _BlockTextParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # 畸形 HTML 不致命：用已收集的块
        log.warning("source_text_parse_partial", error="htmlparser")
    substantive = [
        text for text, heading in parser.blocks if heading or len(text) >= _SUBSTANTIVE_BLOCK_CHARS
    ]
    if not substantive:
        substantive = [text for text, _heading in parser.blocks]
    return clean_text("\n".join(substantive), limit=limit)


def looks_like_js_shell(html: str, text: str) -> bool:
    """明显 JS 壳：页面体积可观但抽不出正文，或显式提示需要 JavaScript。"""
    if len(text) >= _MIN_TEXT_CHARS:
        return False
    if len(html) > 2_000 and len(text) < _MIN_TEXT_CHARS:
        return True
    lowered = html.lower()
    return "enable javascript" in lowered or "请开启 javascript" in lowered or "需要开启" in lowered


def normalize_brand_terms(values: list[object]) -> tuple[str, ...]:
    """Normalize project brand/alias terms without inventing fuzzy matches.

    One-character aliases are excluded because a substring hit on an arbitrary
    Chinese character is not defensible brand evidence. Ordering is stable and
    case-insensitive duplicates are removed, with ``Brand.name`` supplied first.
    """

    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        term = _INLINE_WS_RE.sub(" ", value).strip()
        if len(term) < 2:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(term)
    return tuple(output)


def find_brand_term(text: str, brand_terms: tuple[str, ...]) -> str | None:
    """Return the first exact project term present in extracted source text."""

    folded = text.casefold()
    return next((term for term in brand_terms if term.casefold() in folded), None)


# ---------------------------------------------------------------------------
# 抓取结果分类（纯函数，全部可单测）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HttpAttempt:
    """一次 httpx 抓取的如实结果（不抛业务异常，错误也是数据）。"""

    final_url: str | None
    http_status: int | None
    text: str
    extractor: str | None
    error_kind: str | None  # "timeout" / "transport" / None
    detail: str | None


def classify_attempt(attempt: HttpAttempt) -> tuple[str, bool]:
    """→ (终态 extract_status, 是否需要浏览器回退)。

    - 2xx 且正文达标 → ok，不回退
    - 2xx 但正文 <200 字符/JS 壳 → extract_empty，回退浏览器
    - 401/403/429 → blocked，回退浏览器（反爬拦截常可被真浏览器过掉）
    - 其余 ≥400 → http_error，不回退（404/5xx 浏览器也救不回）
    - 超时 → timeout，回退浏览器一次；传输错误 → http_error，回退一次
    """
    if attempt.error_kind == "timeout":
        return "timeout", True
    if attempt.error_kind is not None:
        return "http_error", True
    status = attempt.http_status
    if status is None:
        return "http_error", False
    if status in (401, 403, 429):
        return "blocked", True
    if status >= 400:
        return "http_error", False
    if len(attempt.text) >= _MIN_TEXT_CHARS:
        return "ok", False
    return "extract_empty", True


# ---------------------------------------------------------------------------
# 可替换薄层：抓取 / DB 读 / 存证（单测全部 fake 注入）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExistingDocument:
    pub_id: str
    extract_status: str
    bytes: int


@dataclass(frozen=True)
class BrandMentionCapture:
    """A real DOM-backed brand occurrence cropped from the source page.

    ``paragraph_text`` and offsets describe the exact readable DOM container that
    was screenshotted. ``bbox`` is relative to the PNG asset (CSS pixels,
    device_scale_factor=1), not guessed full-page coordinates.
    """

    png_bytes: bytes
    matched_text: str
    paragraph_text: str
    text_start: int
    text_end: int
    bbox: dict[str, float]


def validate_brand_mention_capture(
    capture: BrandMentionCapture,
) -> BrandMentionCapture | None:
    """Bind an anchor to the decoded PNG dimensions, failing closed.

    Playwright can occasionally return a transparent 1x1 fallback image while the
    DOM geometry still describes the pre-rasterized element.  Positive coordinates
    alone therefore do not prove that a red box is drawable on the captured pixels.
    Decode and verify the PNG, reject grossly impossible geometry, and persist the
    exact image dimensions alongside the bbox so every read path can enforce the
    same invariant without fetching the object from CAS.
    """

    if (
        not isinstance(capture.png_bytes, bytes)
        or not capture.png_bytes
        or not capture.matched_text
        or not capture.paragraph_text
        or capture.text_start < 0
        or capture.text_end <= capture.text_start
        or capture.text_end > len(capture.paragraph_text)
        or capture.paragraph_text[capture.text_start : capture.text_end].casefold()
        != capture.matched_text.casefold()
    ):
        return None
    try:
        with Image.open(BytesIO(capture.png_bytes)) as image:
            if image.format != "PNG":
                return None
            image_width, image_height = image.size
            image.verify()
    except (OSError, ValueError):
        return None
    if image_width <= 0 or image_height <= 0 or image_width > 100_000 or image_height > 100_000:
        return None

    geometry: dict[str, float] = {}
    for key in ("x", "y", "width", "height"):
        value = capture.bbox.get(key)
        if (
            not isinstance(value, int | float)
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            return None
        geometry[key] = float(value)
    if (
        geometry["x"] < 0
        or geometry["y"] < 0
        or geometry["width"] <= 0
        or geometry["height"] <= 0
        or geometry["x"] >= image_width
        or geometry["y"] >= image_height
    ):
        return None

    # Rasterization can round the final CSS pixel outward.  Clip only that trailing
    # edge; a box whose origin lies outside the real image has already been rejected.
    geometry["width"] = min(geometry["width"], image_width - geometry["x"])
    geometry["height"] = min(geometry["height"], image_height - geometry["y"])
    if geometry["width"] <= 0 or geometry["height"] <= 0:
        return None
    geometry.update(
        {
            "confidence": 1.0,
            "image_width": float(image_width),
            "image_height": float(image_height),
        }
    )
    return BrandMentionCapture(
        png_bytes=capture.png_bytes,
        matched_text=capture.matched_text,
        paragraph_text=capture.paragraph_text,
        text_start=capture.text_start,
        text_end=capture.text_end,
        bbox=geometry,
    )


@dataclass(frozen=True)
class RunSourceContext:
    tenant_pub_id: str
    tenant_id: str  # uuid 文本（platform RLS/外键用）
    project_id: str
    run_id: str
    run_pub_id: str
    project_pub_id: str
    created_at: datetime  # tz-aware；固定为 run 创建时间，作 capture_time
    tasks: list[tuple[str, list[dict[str, Any]]]]  # (task_pub_id, citations)
    existing: dict[str, ExistingDocument]  # url_hash → 已落库 source_document（幂等复用）
    brand_terms: tuple[str, ...] = ()  # Brand.name first, then aliases; exact-match evidence only


@dataclass(frozen=True)
class PersistedDocument:
    pub_id: str
    bytes: int


class SourcePageFetcher(Protocol):
    """抓取薄层：httpx 优先 + 浏览器回退（单测 fake 注入）。"""

    def fetch_httpx(self, url: str) -> HttpAttempt: ...

    def fetch_browser(self, url: str) -> HttpAttempt: ...

    def capture_brand_mention(
        self, url: str, brand_terms: tuple[str, ...]
    ) -> BrandMentionCapture | None: ...

    def close(self) -> None: ...


class SourceContextLoader(Protocol):
    """DB 读薄层：run 行 + 本 run 全部 collection_task + 既存 source_document。"""

    def load(
        self,
        tenant_pub_id: str,
        run_pub_id: str,
        project_pub_id: str,
    ) -> RunSourceContext | None: ...


class SourceDocumentSink(Protocol):
    """存证薄层：正文进 CAS + source_document 行，返回 pub_id 与字节数。"""

    def persist(
        self,
        *,
        context: RunSourceContext,
        target: SourceTarget,
        final_url: str | None,
        http_status: int | None,
        extract_status: str,
        extractor: str | None,
        text: str,
        fetched_at: datetime,
        brand_mention: BrandMentionCapture | None = None,
    ) -> PersistedDocument: ...

    def link(
        self,
        *,
        context: RunSourceContext,
        target: SourceTarget,
        source_document_pub_id: str,
    ) -> None:
        """Persist answer→source-document relations idempotently."""
        ...


# ---------------------------------------------------------------------------
# 生产实现：psycopg loader / EvidenceService sink / httpx+patchright fetcher
# ---------------------------------------------------------------------------


def _postgres_dsn() -> str:
    """与 own_site_snapshot 同款 DSN 读法（worker 覆盖优先，psycopg scheme 归一）。"""
    settings = get_settings()
    return os.getenv("S02_POSTGRES_DSN") or (
        settings.worker_postgres_dsn or settings.postgres_dsn
    ).replace("postgresql+psycopg://", "postgresql://")


class _PostgresSourceLoader:
    """platform.* 表走 app.tenant_id（uuid）RLS：先按 pub_id 解析 tenant，再置双 selector。"""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def load(
        self,
        tenant_pub_id: str,
        run_pub_id: str,
        project_pub_id: str,
    ) -> RunSourceContext | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as connection:
            tenant_row = connection.execute(
                "SELECT id FROM platform.tenant WHERE pub_id=%s", (tenant_pub_id,)
            ).fetchone()
            if tenant_row is None:
                raise ApplicationError(
                    "tenant not found", type="tenant_not_found", non_retryable=True
                )
            connection.execute(
                "SELECT set_config('app.tenant_id', %s, true), "
                "set_config('app.tenant_pub_id', %s, true)",
                (str(tenant_row["id"]), tenant_pub_id),
            )
            run_row = connection.execute(
                """
                SELECT r.id, r.pub_id, r.created_at, r.project_id, p.pub_id AS project_pub_id
                FROM platform.collection_run r
                JOIN platform.project p ON p.id = r.project_id
                WHERE r.pub_id = %s
                """,
                (run_pub_id,),
            ).fetchone()
            if run_row is None:
                return None
            if run_row["project_pub_id"] != project_pub_id:
                raise ApplicationError(
                    "collection run does not belong to project",
                    type="project_mismatch",
                    non_retryable=True,
                )
            task_rows = connection.execute(
                """
                SELECT pub_id, citations_json FROM platform.collection_task
                WHERE run_id = %s ORDER BY created_at, pub_id
                """,
                (run_row["id"],),
            ).fetchall()
            document_rows = connection.execute(
                """
                SELECT pub_id, url_hash, extract_status, bytes FROM platform.source_document
                WHERE run_id = %s
                """,
                (run_row["id"],),
            ).fetchall()
            brand_rows = connection.execute(
                """
                SELECT b.name, ba.value AS alias
                FROM platform.brand b
                LEFT JOIN platform.brand_alias ba ON ba.brand_id = b.id
                WHERE b.project_id = %s
                ORDER BY b.created_at, b.pub_id, ba.created_at, ba.pub_id
                """,
                (run_row["project_id"],),
            ).fetchall()
        created_at = run_row["created_at"]
        if not isinstance(created_at, datetime):
            raise ApplicationError(
                "collection run created_at is invalid",
                type="run_context_invalid",
                non_retryable=True,
            )
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        tasks: list[tuple[str, list[dict[str, Any]]]] = []
        for row in task_rows:
            raw = row["citations_json"] or "[]"
            try:
                citations = json.loads(raw)
            except (TypeError, ValueError):
                log.warning("source_citations_unparseable", task_pub_id=row["pub_id"])
                citations = []
            if not isinstance(citations, list):
                citations = []
            tasks.append(
                (str(row["pub_id"]), [item for item in citations if isinstance(item, dict)])
            )
        existing = {
            str(row["url_hash"]): ExistingDocument(
                pub_id=str(row["pub_id"]),
                extract_status=str(row["extract_status"]),
                bytes=int(row["bytes"]),
            )
            for row in document_rows
        }
        brand_terms = normalize_brand_terms(
            [row["name"] for row in brand_rows]
            + [row["alias"] for row in brand_rows if row["alias"] is not None]
        )
        return RunSourceContext(
            tenant_pub_id=tenant_pub_id,
            tenant_id=str(tenant_row["id"]),
            project_id=str(run_row["project_id"]),
            run_id=str(run_row["id"]),
            run_pub_id=str(run_row["pub_id"]),
            project_pub_id=str(run_row["project_pub_id"]),
            created_at=created_at,
            tasks=tasks,
            existing=existing,
            brand_terms=brand_terms,
        )


class _EvidenceServiceSink:
    """生产存证：正文 bytes 进 CAS + source_document 行（单文档单事务）。

    evidence_pub_id / source_document pub_id 均确定性派生；写入前先查既存资产——
    activity 重试时直接复用不重复写入（幂等，且规避同一 pub_id 二次 capture 的
    重放漂移 ValueError）。抓不到的 URL 也落 source_document 行（extract_status 如实，
    无 CAS 资产）。每条 source_document 与所有引用它的回答另以
    ``evidence_relation:cited_source_document`` 幂等关联。
    """

    def __init__(self, *, dsn: str, service: EvidenceService) -> None:
        self._dsn = dsn
        self._service = service

    def persist(
        self,
        *,
        context: RunSourceContext,
        target: SourceTarget,
        final_url: str | None,
        http_status: int | None,
        extract_status: str,
        extractor: str | None,
        text: str,
        fetched_at: datetime,
        brand_mention: BrandMentionCapture | None = None,
    ) -> PersistedDocument:
        document_pub_id = derive_document_pub_id(
            context.tenant_pub_id, context.run_pub_id, target.url_hash
        )
        payload = text.encode("utf-8") if text else b""
        text_cas_key: str | None = None
        text_sha256: str | None = None
        with _platform_connection(self._dsn, context) as connection:
            if payload:
                evidence_pub_id = derive_evidence_pub_id(
                    context.tenant_pub_id, context.run_pub_id, target.url
                )
                provenance = RedactedProvenance(
                    platform_account_pub_id=None,
                    browser_profile_version_pub_id=None,
                    session_event_pub_id=None,
                    channel=CaptureChannel.WEB,
                    authorization_scope=(),
                    adapter_version=_ADAPTER_VERSION,
                    capture_time=context.created_at,
                    access_class=AccessClass.CUSTOMER_PRIVATE,
                )
                stored = self._ensure_asset(
                    connection,
                    tenant_pub_id=context.tenant_pub_id,
                    project_pub_id=context.project_pub_id,
                    evidence_pub_id=evidence_pub_id,
                    payload=payload,
                    source_url=target.url,
                    provenance=provenance,
                )
                text_cas_key = stored.key
                text_sha256 = stored.sha256
            connection.execute(
                """
                INSERT INTO platform.source_document
                  (id,pub_id,tenant_id,project_id,run_id,url,url_hash,host,final_url,
                   http_status,fetched_at,extract_status,extractor,bytes,text_cas_key,
                   text_sha256,created_at,updated_at)
                VALUES
                  (gen_random_uuid(),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),now())
                ON CONFLICT (run_id,url_hash) DO NOTHING
                """,
                (
                    document_pub_id,
                    context.tenant_id,
                    context.project_id,
                    context.run_id,
                    target.url,
                    target.url_hash,
                    target.host,
                    final_url,
                    http_status,
                    fetched_at,
                    extract_status,
                    extractor,
                    len(payload),
                    text_cas_key,
                    text_sha256,
                ),
            )
            self._link_with_connection(
                connection,
                context=context,
                target=target,
                source_document_pub_id=document_pub_id,
            )
            if brand_mention is not None:
                self._persist_brand_mention_with_connection(
                    connection,
                    context=context,
                    target=target,
                    source_document_pub_id=document_pub_id,
                    capture=brand_mention,
                )
            connection.commit()
        return PersistedDocument(pub_id=document_pub_id, bytes=len(payload))

    def link(
        self,
        *,
        context: RunSourceContext,
        target: SourceTarget,
        source_document_pub_id: str,
    ) -> None:
        """Backfill/fan out relations when the document itself is reused."""

        with _platform_connection(self._dsn, context) as connection:
            self._link_with_connection(
                connection,
                context=context,
                target=target,
                source_document_pub_id=source_document_pub_id,
            )
            connection.commit()

    @staticmethod
    def _link_with_connection(
        connection: psycopg.Connection[Any],
        *,
        context: RunSourceContext,
        target: SourceTarget,
        source_document_pub_id: str,
    ) -> None:
        for answer_pub_id in target.task_pub_ids:
            connection.execute(
                """
                INSERT INTO evidence.evidence_relation
                  (tenant_pub_id,from_pub_id,to_pub_id,relation_type)
                VALUES (%s,%s,%s,'cited_source_document')
                ON CONFLICT (tenant_pub_id,from_pub_id,to_pub_id,relation_type) DO NOTHING
                """,
                (context.tenant_pub_id, answer_pub_id, source_document_pub_id),
            )

    def _persist_brand_mention_with_connection(
        self,
        connection: psycopg.Connection[Any],
        *,
        context: RunSourceContext,
        target: SourceTarget,
        source_document_pub_id: str,
        capture: BrandMentionCapture,
    ) -> None:
        """Persist only a real, DOM-located brand occurrence and its pixel anchor."""

        evidence_pub_id = derive_brand_evidence_pub_id(
            context.tenant_pub_id, context.run_pub_id, target.url_hash
        )
        existing = connection.execute(
            "SELECT 1 FROM evidence.evidence_asset WHERE tenant_pub_id=%s AND pub_id=%s",
            (context.tenant_pub_id, evidence_pub_id),
        ).fetchone()
        if existing is None:
            self._service.capture(
                evidence_pub_id=evidence_pub_id,
                tenant_pub_id=context.tenant_pub_id,
                project_pub_id=context.project_pub_id,
                kind=_BRAND_EVIDENCE_KIND,
                payload=capture.png_bytes,
                mime_type="image/png",
                source_url=target.url,
                provenance=RedactedProvenance(
                    platform_account_pub_id=None,
                    browser_profile_version_pub_id=None,
                    session_event_pub_id=None,
                    channel=CaptureChannel.WEB,
                    authorization_scope=(),
                    adapter_version=_BRAND_EVIDENCE_ADAPTER_VERSION,
                    capture_time=context.created_at,
                    access_class=AccessClass.CUSTOMER_PRIVATE,
                ),
                db_connection=connection,
            )
        anchor_pub_id = derive_brand_anchor_pub_id(evidence_pub_id, capture.matched_text)
        connection.execute(
            """
            INSERT INTO evidence.evidence_anchor
              (pub_id,tenant_pub_id,evidence_pub_id,text_start,text_end,bbox,quote_hash)
            VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)
            ON CONFLICT (pub_id) DO NOTHING
            """,
            (
                anchor_pub_id,
                context.tenant_pub_id,
                evidence_pub_id,
                capture.text_start,
                capture.text_end,
                json.dumps(capture.bbox, sort_keys=True, separators=(",", ":")),
                sha256(capture.matched_text.encode()).hexdigest(),
            ),
        )
        # Link from the source document for audit provenance, and from every answer
        # that actually cited this URL for the answer-detail evidence surface.
        for from_pub_id in (source_document_pub_id, *target.task_pub_ids):
            connection.execute(
                """
                INSERT INTO evidence.evidence_relation
                  (tenant_pub_id,from_pub_id,to_pub_id,relation_type)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (tenant_pub_id,from_pub_id,to_pub_id,relation_type) DO NOTHING
                """,
                (
                    context.tenant_pub_id,
                    from_pub_id,
                    evidence_pub_id,
                    _BRAND_EVIDENCE_RELATION,
                ),
            )

    def _ensure_asset(
        self,
        connection: psycopg.Connection[Any],
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        evidence_pub_id: str,
        payload: bytes,
        source_url: str,
        provenance: RedactedProvenance,
    ) -> _StoredAsset:
        row = connection.execute(
            "SELECT object_key, sha256, byte_size FROM evidence.evidence_asset "
            "WHERE tenant_pub_id=%s AND pub_id=%s",
            (tenant_pub_id, evidence_pub_id),
        ).fetchone()
        if row is not None:
            return _StoredAsset(key=str(row[0]), sha256=str(row[1]), byte_size=int(row[2]))
        stored = self._service.capture(
            evidence_pub_id=evidence_pub_id,
            tenant_pub_id=tenant_pub_id,
            project_pub_id=project_pub_id,
            kind=_EVIDENCE_KIND,
            payload=payload,
            mime_type="text/plain;charset=utf-8",
            source_url=source_url,
            provenance=provenance,
            db_connection=connection,
        )
        return _StoredAsset(key=stored.key, sha256=stored.sha256, byte_size=stored.byte_size)


@dataclass(frozen=True)
class _StoredAsset:
    key: str
    sha256: str
    byte_size: int


@contextmanager
def _platform_connection(dsn: str, context: RunSourceContext) -> Iterator[psycopg.Connection[Any]]:
    """platform schema 写连接：置 app.tenant_id + app.tenant_pub_id 双 selector。"""
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true), "
            "set_config('app.tenant_pub_id', %s, true)",
            (context.tenant_id, context.tenant_pub_id),
        )
        yield connection


class _HttpxBrowserFetcher:
    """生产抓取：httpx 优先（15s/跟随重定向/浏览器 UA），patchright 浏览器惰性回退。"""

    def __init__(self, config: SourceFetchConfig) -> None:
        self._config = config
        self._client = httpx.Client(
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
            },
            follow_redirects=True,
            timeout=_HTTP_TIMEOUT_S,
            trust_env=False,
        )
        self._pw_cm: Any = None
        self._browser: Any = None
        self._context: Any = None

    def fetch_httpx(self, url: str) -> HttpAttempt:
        try:
            response = self._client.get(url)
        except httpx.TimeoutException as exc:
            return HttpAttempt(None, None, "", None, "timeout", type(exc).__name__)
        except httpx.TransportError as exc:
            return HttpAttempt(None, None, "", None, "transport", type(exc).__name__)
        status = response.status_code
        final_url = str(response.url)
        content_type = response.headers.get("content-type", "").lower()
        if status < 400 and "html" not in content_type and "text/" not in content_type:
            # 非 HTML 正文（PDF/图片/JSON 等）：不抽文本，如实记 0 字节走 extract_empty 判定
            return HttpAttempt(final_url, status, "", None, None, f"content-type:{content_type}")
        text = extract_text_from_html(response.text) if status < 400 else ""
        return HttpAttempt(final_url, status, text, _EXTRACTOR_HTTPX if text else None, None, None)

    def fetch_browser(self, url: str) -> HttpAttempt:
        self._ensure_browser()
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT_MS)
            page.wait_for_timeout(_SETTLE_MS)
            extracted = page.evaluate(_EXTRACT_TEXT_JS)
            raw_text = extracted if isinstance(extracted, str) else ""
            text = clean_text(raw_text)
            final_url = str(page.url)
        finally:
            try:
                page.close()
            except Exception:
                pass
        return HttpAttempt(final_url, None, text, _EXTRACTOR_BROWSER if text else None, None, None)

    def capture_brand_mention(
        self, url: str, brand_terms: tuple[str, ...]
    ) -> BrandMentionCapture | None:
        """Re-open a source and capture only a DOM-verifiable brand paragraph.

        Extracted-text presence is checked by the caller first.  This second gate
        deliberately requires a real text node in the live DOM; a citation summary,
        page title, metadata tag, or generated overlay can never create evidence.
        """

        self._ensure_browser()
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT_MS)
            page.wait_for_timeout(_SETTLE_MS)
            projection = page.evaluate(_BRAND_MENTION_JS, list(brand_terms))
            if not isinstance(projection, dict) or projection.get("matched") is not True:
                return None
            matched_text = projection.get("matched_text")
            paragraph_text = projection.get("paragraph_text")
            text_start = projection.get("text_start")
            text_end = projection.get("text_end")
            bbox = projection.get("bbox")
            if (
                not isinstance(matched_text, str)
                or not matched_text
                or not isinstance(paragraph_text, str)
                or not paragraph_text
                or not isinstance(text_start, int)
                or isinstance(text_start, bool)
                or not isinstance(text_end, int)
                or isinstance(text_end, bool)
                or text_start < 0
                or text_end <= text_start
                or not isinstance(bbox, dict)
            ):
                return None
            safe_bbox: dict[str, float] = {}
            for key in ("x", "y", "width", "height"):
                value = bbox.get(key)
                if not isinstance(value, int | float) or isinstance(value, bool):
                    return None
                safe_bbox[key] = float(value)
            if safe_bbox["width"] <= 0 or safe_bbox["height"] <= 0:
                return None
            safe_bbox["confidence"] = 1.0
            locator = page.locator('[data-geo-brand-mention-container="1"]').first
            if locator.count() != 1:
                return None
            payload = locator.screenshot(type="png", timeout=15_000, animations="disabled")
            if not isinstance(payload, bytes) or not payload:
                return None
            return validate_brand_mention_capture(
                BrandMentionCapture(
                    png_bytes=payload,
                    matched_text=matched_text,
                    paragraph_text=paragraph_text,
                    text_start=text_start,
                    text_end=text_end,
                    bbox=safe_bbox,
                )
            )
        finally:
            try:
                page.close()
            except Exception:
                pass

    def _ensure_browser(self) -> None:
        if self._context is not None:
            return
        driver, sync_playwright, _timeout_error = load_sync_browser_driver()
        try:
            self._pw_cm = sync_playwright()
            pw = self._pw_cm.__enter__()
            self._browser = pw.chromium.launch(
                headless=self._config.headless,
                args=["--lang=zh-CN"],
            )
            self._context = self._browser.new_context(
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                device_scale_factor=1,
                extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5"},
                user_agent=_USER_AGENT,
            )
            self._context.set_default_timeout(_GOTO_TIMEOUT_MS)
        except Exception as exc:
            raise ApplicationError(
                f"browser-launch-failed({driver}): {type(exc).__name__}: {exc}",
                type="browser_launch_failed",
            ) from exc

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw_cm is not None:
            try:
                self._pw_cm.__exit__(None, None, None)
            except Exception:
                pass
            self._pw_cm = None


# 正文抽取：优先 article/main/[role=main]，回退 body.innerText（own_site_snapshot 同款）
_EXTRACT_TEXT_JS = r"""
() => {
  const root = document.querySelector('article, main, [role="main"]') || document.body;
  return root && root.innerText ? root.innerText : '';
}
"""


# No badge, title, summary, or synthetic paragraph is injected.  The only DOM
# mutation is wrapping an existing exact text-node range with a red rectangle.
# The screenshot target is the smallest readable existing container around it.
_BRAND_MENTION_JS = r"""
(terms) => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const root = document.querySelector('article, main, [role="main"]') || document.body;
  if (!root) return {matched: false};
  const candidates = (terms || [])
    .map((value) => clean(value))
    .filter((value) => value.length >= 2);
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent || ['SCRIPT','STYLE','NOSCRIPT','TEMPLATE','TEXTAREA'].includes(parent.tagName)) {
        return NodeFilter.FILTER_REJECT;
      }
      if (parent.closest('[aria-hidden="true"]')) return NodeFilter.FILTER_REJECT;
      const style = window.getComputedStyle(parent);
      if (style.display === 'none' || style.visibility === 'hidden') {
        return NodeFilter.FILTER_REJECT;
      }
      const text = String(node.nodeValue || '').toLocaleLowerCase();
      return candidates.some((term) => text.includes(term.toLocaleLowerCase()))
        ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
    }
  });
  const node = walker.nextNode();
  if (!node) return {matched: false};
  const raw = String(node.nodeValue || '');
  const lowered = raw.toLocaleLowerCase();
  const matchedTerm = candidates.find((term) => lowered.includes(term.toLocaleLowerCase()));
  if (!matchedTerm) return {matched: false};
  const start = lowered.indexOf(matchedTerm.toLocaleLowerCase());
  if (start < 0 || start + matchedTerm.length > raw.length) return {matched: false};

  const range = document.createRange();
  range.setStart(node, start);
  range.setEnd(node, start + matchedTerm.length);
  const mark = document.createElement('mark');
  mark.setAttribute('data-geo-brand-mention-mark', '1');
  mark.style.cssText = [
    'background:transparent', 'color:inherit', 'border:3px solid #dc2626',
    'border-radius:2px', 'padding:0 2px', 'box-decoration-break:clone',
    '-webkit-box-decoration-break:clone'
  ].join(';');
  try {
    range.surroundContents(mark);
  } catch (_) {
    return {matched: false};
  }

  let container = mark.closest('p,li,blockquote,td,th,dd,dt,figcaption');
  if (!container) {
    let cursor = mark.parentElement;
    while (cursor && cursor !== root) {
      const text = clean(cursor.innerText || cursor.textContent);
      const rect = cursor.getBoundingClientRect();
      if (text.length >= matchedTerm.length && text.length <= 2000 && rect.width >= 120) {
        container = cursor;
        break;
      }
      cursor = cursor.parentElement;
    }
  }
  if (!container) container = mark.parentElement;
  if (!container) return {matched: false};
  container.setAttribute('data-geo-brand-mention-container', '1');
  container.scrollIntoView({block: 'center', inline: 'nearest'});

  const paragraphText = clean(container.innerText || container.textContent).slice(0, 2000);
  const paragraphFolded = paragraphText.toLocaleLowerCase();
  const paragraphStart = paragraphFolded.indexOf(matchedTerm.toLocaleLowerCase());
  if (paragraphStart < 0) return {matched: false};
  const containerRect = container.getBoundingClientRect();
  const markRect = mark.getBoundingClientRect();
  if (containerRect.width <= 0 || containerRect.height <= 0 ||
      markRect.width <= 0 || markRect.height <= 0) {
    return {matched: false};
  }
  return {
    matched: true,
    matched_text: raw.slice(start, start + matchedTerm.length),
    paragraph_text: paragraphText,
    text_start: paragraphStart,
    text_end: paragraphStart + matchedTerm.length,
    bbox: {
      x: Math.max(0, markRect.left - containerRect.left),
      y: Math.max(0, markRect.top - containerRect.top),
      width: markRect.width,
      height: markRect.height
    }
  };
}
"""


# ---------------------------------------------------------------------------
# 同步核心（生产线程内跑；单测直接调用，依赖全注入）
# ---------------------------------------------------------------------------


def _noop_progress(stage: str, url: str) -> None:
    del stage, url


def execute_source_fetch(
    item: SourceFetchInput,
    *,
    config: SourceFetchConfig,
    loader: SourceContextLoader,
    fetcher: SourcePageFetcher,
    sink: SourceDocumentSink,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    on_progress: Callable[[str, str], None] | None = None,
) -> SourceFetchResult:
    """读 DB → 目标规划 → 逐条抓取（限速）→ 存证。单条失败如实落库，不中断。"""
    if not config.enabled:
        return SourceFetchResult(
            per_answer_limit=config.limit,
            run_limit=config.run_limit,
            skipped="disabled",
        )
    progress = on_progress if on_progress is not None else _noop_progress
    progress("load_context", "")
    context = loader.load(item.tenant_pub_id, item.run_pub_id, item.project_pub_id)
    if context is None:
        raise ApplicationError("collection run not found", type="run_not_found", non_retryable=True)
    targets = plan_source_targets(context.tasks, config.limit, config.run_limit)
    planning_coverage = source_plan_coverage(
        context.tasks,
        targets,
        limit=config.limit,
        run_limit=config.run_limit,
    )

    fetched: list[FetchedSource] = []
    failures: list[SourceFetchFailure] = []
    last_hit_by_host: dict[str, float] = {}

    def _throttle(host: str) -> None:
        """限速纪律：同域请求间隔 ≥2s（首次不限）。"""
        last = last_hit_by_host.get(host)
        now = monotonic()
        if last is not None:
            wait = _PER_DOMAIN_DELAY_S - (now - last)
            if wait > 0:
                sleep(wait)
        last_hit_by_host[host] = monotonic()

    for target in targets:
        existing = context.existing.get(target.url_hash)
        if existing is not None:
            # 幂等复用：同 run 重跑不重复抓取/不重复 capture
            try:
                sink.link(
                    context=context,
                    target=target,
                    source_document_pub_id=existing.pub_id,
                )
            except Exception as exc:
                failures.append(
                    SourceFetchFailure(
                        url=target.url,
                        error=f"link: {type(exc).__name__}: {exc}",
                    )
                )
            fetched.append(
                FetchedSource(
                    url=target.url,
                    extract_status=existing.extract_status,
                    source_document_pub_id=existing.pub_id,
                    bytes=existing.bytes,
                    answer_pub_ids=target.task_pub_ids,
                )
            )
            continue
        progress("fetch", target.url)
        try:
            _throttle(target.host)
            attempt = fetcher.fetch_httpx(target.url)
            status, needs_browser = classify_attempt(attempt)
            if needs_browser:
                progress("fetch_browser", target.url)
                try:
                    _throttle(target.host)
                    browser_attempt = fetcher.fetch_browser(target.url)
                except Exception as exc:
                    log.warning(
                        "source_browser_fallback_failed",
                        url=target.url,
                        error_type=type(exc).__name__,
                    )
                else:
                    if len(browser_attempt.text) >= _MIN_TEXT_CHARS:
                        attempt = browser_attempt
                        status = "ok"
            fetched_at = datetime.now(UTC)
            text = attempt.text if status == "ok" else ""
            brand_mention: BrandMentionCapture | None = None
            matched_brand = find_brand_term(text, context.brand_terms) if text else None
            if matched_brand is not None:
                progress("capture_brand_mention", target.url)
                capture_method = getattr(fetcher, "capture_brand_mention", None)
                if not callable(capture_method):
                    failures.append(
                        SourceFetchFailure(
                            url=target.url,
                            error="brand_capture: fetcher_does_not_support_dom_capture",
                        )
                    )
                else:
                    try:
                        candidate = capture_method(target.url, context.brand_terms)
                    except Exception as exc:
                        failures.append(
                            SourceFetchFailure(
                                url=target.url,
                                error=f"brand_capture: {type(exc).__name__}: {exc}",
                            )
                        )
                    else:
                        # A screenshot cannot become evidence merely because the
                        # extracted text had a term. The live DOM capture must return
                        # the same exact project term and that term must also exist in
                        # the persisted source_document text.
                        validated_candidate = (
                            validate_brand_mention_capture(candidate)
                            if candidate is not None
                            else None
                        )
                        if candidate is not None and validated_candidate is None:
                            failures.append(
                                SourceFetchFailure(
                                    url=target.url,
                                    error="brand_capture: invalid_png_or_bbox",
                                )
                            )
                        elif (
                            validated_candidate is not None
                            and validated_candidate.matched_text.casefold() in text.casefold()
                            and any(
                                validated_candidate.matched_text.casefold() == term.casefold()
                                for term in context.brand_terms
                            )
                        ):
                            brand_mention = validated_candidate
                        else:
                            failures.append(
                                SourceFetchFailure(
                                    url=target.url,
                                    error="brand_capture: dom_brand_term_not_found",
                                )
                            )
            try:
                if brand_mention is not None:
                    persisted = sink.persist(
                        context=context,
                        target=target,
                        final_url=attempt.final_url,
                        http_status=attempt.http_status,
                        extract_status=status,
                        extractor=attempt.extractor if status == "ok" else None,
                        text=text,
                        fetched_at=fetched_at,
                        brand_mention=brand_mention,
                    )
                else:
                    persisted = sink.persist(
                        context=context,
                        target=target,
                        final_url=attempt.final_url,
                        http_status=attempt.http_status,
                        extract_status=status,
                        extractor=attempt.extractor if status == "ok" else None,
                        text=text,
                        fetched_at=fetched_at,
                    )
            except Exception as exc:
                failures.append(
                    SourceFetchFailure(
                        url=target.url, error=f"persist: {type(exc).__name__}: {exc}"
                    )
                )
                continue
            fetched.append(
                FetchedSource(
                    url=target.url,
                    extract_status=status,
                    source_document_pub_id=persisted.pub_id,
                    bytes=persisted.bytes,
                    answer_pub_ids=target.task_pub_ids,
                    brand_mention_captured=brand_mention is not None,
                )
            )
        except Exception as exc:
            failures.append(
                SourceFetchFailure(url=target.url, error=f"{type(exc).__name__}: {exc}")
            )
    result = SourceFetchResult(
        fetched=fetched,
        failures=failures,
        planning_coverage=planning_coverage,
        per_answer_limit=config.limit,
        run_limit=config.run_limit,
        skipped=None,
    )
    log.info(
        "source_fetch_done",
        run_pub_id=context.run_pub_id,
        fetched=len(result.fetched),
        ok=sum(1 for f in result.fetched if f.extract_status == "ok"),
        failures=len(result.failures),
        brand_mention_screenshots=sum(f.brand_mention_captured for f in result.fetched),
        answer_source_relations=sum(len(target.task_pub_ids) for target in targets),
        answers_with_planned_sources=len(
            {answer_pub_id for target in targets for answer_pub_id in target.task_pub_ids}
        ),
        planned_urls=sum(row.planned_urls for row in planning_coverage),
        truncated_urls=sum(row.truncated_urls for row in planning_coverage),
        answers_truncated=sum(row.truncated_urls > 0 for row in planning_coverage),
        per_answer_limit=config.limit,
        run_limit=config.run_limit,
    )
    return result


# ---------------------------------------------------------------------------
# activity 入口与异步泵
# ---------------------------------------------------------------------------


async def run_source_fetch(
    item: SourceFetchInput,
    *,
    config: SourceFetchConfig,
    loader: SourceContextLoader,
    sink: SourceDocumentSink,
    fetcher_factory: Callable[[SourceFetchConfig], SourcePageFetcher] | None = None,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SourceFetchResult:
    """异步泵封装：默认实现跑 asyncio.to_thread + 10s heartbeat 泵（own_site_snapshot 同款）。

    注入 fetcher_factory 时（单测）同步内联执行，不起线程。
    """
    uses_default_fetcher = fetcher_factory is None
    factory: Callable[[SourceFetchConfig], SourcePageFetcher] = (
        fetcher_factory if fetcher_factory is not None else _HttpxBrowserFetcher
    )
    if heartbeat is None:

        def heartbeat(payload: dict[str, Any]) -> None:
            del payload

    progress: dict[str, str] = {"stage": "start", "url": ""}

    def _on_progress(stage: str, url: str) -> None:
        progress["stage"] = stage
        progress["url"] = url

    def _blocking() -> SourceFetchResult:
        fetcher = factory(config)
        try:
            return execute_source_fetch(
                item,
                config=config,
                loader=loader,
                fetcher=fetcher,
                sink=sink,
                sleep=sleep,
                on_progress=_on_progress,
            )
        finally:
            fetcher.close()

    if uses_default_fetcher:
        thread = asyncio.ensure_future(asyncio.to_thread(_blocking))
        while True:
            heartbeat({"run_pub_id": item.run_pub_id, **progress})
            done, _pending = await asyncio.wait({thread}, timeout=_HEARTBEAT_INTERVAL_S)
            if done:
                break
        return thread.result()
    heartbeat({"run_pub_id": item.run_pub_id, **progress})
    return _blocking()


@activity.defn(name="fetch_run_sources")
async def fetch_run_sources(item: SourceFetchInput) -> SourceFetchResult:
    """W2 抓取层 activity 入口：env 配置 + 真实 DB/CAS/httpx+浏览器接线。"""
    config = SourceFetchConfig.from_env()
    if not config.enabled:
        return SourceFetchResult(skipped="disabled")
    dsn = _postgres_dsn()
    settings = get_settings()
    store = ContentAddressedObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
    )
    store.ensure_bucket()
    return await run_source_fetch(
        item,
        config=config,
        loader=_PostgresSourceLoader(dsn),
        sink=_EvidenceServiceSink(dsn=dsn, service=EvidenceService(dsn=dsn, store=store)),
        heartbeat=activity.heartbeat,
    )
