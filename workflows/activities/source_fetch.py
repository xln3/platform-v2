"""All-U public-page fetch activity (``fetch_run_sources``).

需求规格：developlog/specs/geo-evaluation-improvement-20260805.md W2 节。
workflow 挂接由协调者集成，本模块只提供 activity 与可单测核心：

- 每份回答的全部 U URL 都进入计划。旧 ``GEO_SOURCE_FETCH_LIMIT`` 与
  ``GEO_SOURCE_FETCH_RUN_LIMIT`` 仅保留为调度批量提示，不再缩小业务全集。相同
  URL 身份可复用一次页面快照，但每条问答 occurrence 仍由事实表完整保留。
- 每条：httpx 优先（15s 超时、正常浏览器 UA、跟随重定向、按域限速 2s）；
  正文 <200 字符 / 明显 JS 壳 / 超时 / 401·403·429 → patchright 浏览器回退（只取正文）。
  httpx 路径用 stdlib 密度抽取（``density-extract-v1``），浏览器路径 innerText
  （``innertext-v1``，与 own_site_snapshot 同款 JS）；大正文由后续分析切窗，
  抓取层不再静默截掉尾部。
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
- env：``GEO_SOURCE_FETCH_ENABLED``（缺省 true，false 时 activity skipped=
  "disabled" 零 IO）；两个历史 limit 配置仅作为兼容的 batch hint 返回。
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
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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

from domain.collection.source_metadata import SourceMetadata, extract_source_metadata
from domain.collection.uvw import URL_NORMALIZATION_VERSION, citation_text_for_reference
from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from domain.scoring.analyzer import canonicalize_url
from workflows.activities.browser_driver import load_sync_browser_driver

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# env 配置
# ---------------------------------------------------------------------------

ENV_ENABLED = "GEO_SOURCE_FETCH_ENABLED"
ENV_FETCH_LIMIT = "GEO_SOURCE_FETCH_LIMIT"
ENV_RUN_LIMIT = "GEO_SOURCE_FETCH_RUN_LIMIT"

_DEFAULT_LIMIT = 200
_DEFAULT_RUN_LIMIT = 20_000

_HEARTBEAT_INTERVAL_S = 10.0  # 与 own_site_snapshot 同款泵频
_HTTP_TIMEOUT_S = 15.0
_PER_DOMAIN_DELAY_S = 2.0  # 限速纪律：同域请求间隔 ≥2s（抓取触发风控风险，规格明示）
_GOTO_TIMEOUT_MS = 20_000
_SETTLE_MS = 1_500
_MIN_TEXT_CHARS = 200  # 低于此视为 JS 壳/空页 → 浏览器回退

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
    limit: int  # 兼容字段：调度 batch hint，不得作为业务全集上限
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
    return max(1, value)


def _env_run_limit() -> int:
    raw = os.environ.get(ENV_RUN_LIMIT, "").strip()
    if not raw:
        return _DEFAULT_RUN_LIMIT
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_RUN_LIMIT
    return max(1, value)


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
    source_url_id: str | None = None


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
    """Classify a likely non-HTML asset without removing it from U coverage."""
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
    """Plan every distinct U URL while preserving answer fan-out.

    ``limit`` and ``run_limit`` are accepted for Temporal/config replay
    compatibility but intentionally do not truncate facts.  Non-page/static
    URLs remain in the denominator and will receive an explicit fetch outcome.
    """
    del limit, run_limit
    targets: list[SourceTarget] = []
    target_index_by_key: dict[str, int] = {}
    task_order = {task_pub_id: index for index, (task_pub_id, _citations) in enumerate(tasks)}
    first_url_by_key: dict[str, str] = {}
    source_url_id_by_key: dict[str, str | None] = {}
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
            host = normalize_host(key)
            if host is None:
                continue
            seen_for_answer.add(key)
            cited_text = citation.get("cited_text")
            priority = 0 if isinstance(cited_text, str) and cited_text.strip() else 1
            clean_url = url.strip()
            first_url_by_key.setdefault(key, clean_url)
            raw_source_url_id = citation.get("source_url_id")
            source_url_id_by_key.setdefault(
                key, str(raw_source_url_id) if raw_source_url_id is not None else None
            )
            candidates.append((priority, ordinal, clean_url, key, host))
        planned_by_answer.append((task_pub_id, sorted(candidates)))

    # Round-robin by citation rank so a run safety cap cannot be monopolized by
    # the earliest answers.  First give each answer one planned source, then its
    # second source, and so on.
    max_candidates = max((len(candidates) for _task, candidates in planned_by_answer), default=0)
    for citation_rank in range(max_candidates):
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
                        source_url_id=existing.source_url_id,
                    )
                continue
            target_index_by_key[key] = len(targets)
            targets.append(
                SourceTarget(
                    url=first_url_by_key[key],
                    key=key,
                    url_hash=sha256(key.encode()).hexdigest(),
                    host=host,
                    task_pub_ids=(task_pub_id,),
                    source_url_id=source_url_id_by_key[key],
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

    Counts use unique, parseable HTTP(S) occurrence URLs.  They are
    planning counts, not fetch-success/document counts.  The result is returned by
    the Temporal activity, making configured protection effects durable and
    inspectable instead of a hidden Top-N.
    """

    del limit, run_limit
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
            if key is None:
                continue
            eligible.add(key)
        eligible_count = len(eligible)
        planned_count = len(planned_by_answer.get(answer_pub_id, set()))
        rows.append(
            SourceFetchPlanCoverage(
                answer_pub_id=answer_pub_id,
                eligible_urls=eligible_count,
                planned_urls=planned_count,
                truncated_urls=max(0, eligible_count - planned_count),
                coverage_rate=(
                    round(planned_count / eligible_count, 4) if eligible_count else None
                ),
                truncation_reason=None,
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


def _stable_source_uuid(value: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"geo-platform-v2:{value}")


def _stable_source_pub_id(prefix: str, value: str) -> str:
    return f"{prefix}_{sha256(value.encode()).hexdigest()[:26]}"


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


def clean_text(raw: str, limit: int | None = None) -> str:
    """Normalize whitespace; only explicitly scoped callers may request a slice."""
    lines = [_INLINE_WS_RE.sub(" ", line).strip() for line in raw.splitlines()]
    text = "\n".join(lines)
    text = _BLANK_RUN_RE.sub("\n\n", text).strip()
    return text if limit is None else text[: max(0, limit)]


def extract_text_from_html(html: str, limit: int | None = None) -> str:
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
    metadata: SourceMetadata | None = None
    redirect_chain: tuple[dict[str, Any], ...] = ()


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
        metadata: SourceMetadata | None = None,
        redirect_chain: tuple[dict[str, Any], ...] = (),
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
                SELECT task.id,task.pub_id,task.citations_json,
                       EXISTS (
                         SELECT 1 FROM platform.answer_retrieval_event event
                         WHERE event.answer_task_id=task.id
                       ) AS has_uvw_events
                FROM platform.collection_task task
                WHERE task.run_id = %s ORDER BY task.created_at, task.pub_id
                """,
                (run_row["id"],),
            ).fetchall()
            occurrence_rows = connection.execute(
                """
                SELECT occurrence.answer_task_id,occurrence.occurrence_ordinal,
                       occurrence.raw_url,occurrence.title,
                       occurrence.u_state,occurrence.final_reference_state,
                       occurrence.final_reference_ordinal,url.canonical_url,
                       occurrence.source_url_id
                FROM platform.answer_source_occurrence occurrence
                JOIN platform.source_url url ON url.id=occurrence.source_url_id
                WHERE occurrence.run_id=%s
                  AND (
                    occurrence.u_state='observed'
                    OR occurrence.final_reference_state='referenced'
                  )
                ORDER BY occurrence.answer_task_id,occurrence.occurrence_ordinal
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
        citations_by_task: dict[str, list[dict[str, Any]]] = {}
        for task_row in task_rows:
            raw_citations = task_row["citations_json"] or "[]"
            try:
                citations = json.loads(raw_citations)
            except (TypeError, ValueError):
                log.warning("source_citations_unparseable", task_pub_id=task_row["pub_id"])
                citations = []
            citations_by_task[str(task_row["id"])] = (
                [item for item in citations if isinstance(item, dict)]
                if isinstance(citations, list)
                else []
            )
        rows_by_task: dict[str, list[dict[str, Any]]] = {str(row["id"]): [] for row in task_rows}
        for row in occurrence_rows:
            task_id = str(row["answer_task_id"])
            rows_by_task.setdefault(str(row["answer_task_id"]), []).append(
                {
                    "url": str(row["raw_url"]),
                    "title": row["title"],
                    "cited_text": (
                        citation_text_for_reference(
                            citations_by_task.get(task_id, []),
                            canonical_url=str(row["canonical_url"]),
                            final_reference_ordinal=row["final_reference_ordinal"],
                        )
                        if row["final_reference_state"] == "referenced"
                        else None
                    ),
                    "ordinal": int(row["occurrence_ordinal"]),
                    "u_state": str(row["u_state"]),
                    "source_url_id": str(row["source_url_id"]),
                }
            )
        # Rolling deployments can briefly leave tasks written by the legacy
        # collector after the UVW migration has run.  Use citations_json only
        # when the task has no UVW retrieval event at all; once a modern event
        # exists, an empty occurrence set is an observed fact and must not be
        # replaced with inferred legacy rows.
        for row in task_rows:
            task_id = str(row["id"])
            if rows_by_task.get(task_id) or bool(row["has_uvw_events"]):
                continue
            rows_by_task[task_id] = citations_by_task.get(task_id, [])
        tasks = [(str(row["pub_id"]), rows_by_task.get(str(row["id"]), [])) for row in task_rows]
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


def exact_source_quote_matches(
    context: RunSourceContext,
    target: SourceTarget,
    source_text: str,
) -> list[dict[str, Any]]:
    """Return only byte-for-byte source fragments; never infer semantic support."""

    matches: list[dict[str, Any]] = []
    linked_answers = set(target.task_pub_ids)
    for answer_pub_id, citations in context.tasks:
        if answer_pub_id not in linked_answers:
            continue
        for fallback_ordinal, citation in enumerate(citations, 1):
            url = citation.get("url")
            cited_text = citation.get("cited_text")
            if (
                not isinstance(url, str)
                or url_dedupe_key(url) != target.key
                or not isinstance(cited_text, str)
                or not cited_text.strip()
            ):
                continue
            quote = cited_text.strip()
            start = source_text.find(quote)
            if start < 0:
                continue
            ordinal = citation.get("ordinal", fallback_ordinal)
            if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
                ordinal = fallback_ordinal
            matches.append(
                {
                    "answer_pub_id": answer_pub_id,
                    "ordinal": ordinal,
                    "source_quote": source_text[start : start + len(quote)],
                    "source_text_start": start,
                    "source_text_end": start + len(quote),
                    "source_quote_hash": sha256(quote.encode()).hexdigest(),
                }
            )
    return matches


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
        metadata: SourceMetadata | None = None,
        redirect_chain: tuple[dict[str, Any], ...] = (),
    ) -> PersistedDocument:
        document_pub_id = derive_document_pub_id(
            context.tenant_pub_id, context.run_pub_id, target.url_hash
        )
        payload = text.encode("utf-8") if text else b""
        text_cas_key: str | None = None
        text_sha256: str | None = None
        metadata = metadata or SourceMetadata(canonical_url=final_url or target.url)
        with _platform_connection(self._dsn, context) as connection:
            source_url_id = target.source_url_id
            if source_url_id is None:
                source_url_row = connection.execute(
                    """
                    SELECT id FROM platform.source_url
                    WHERE tenant_id=%s
                      AND canonical_url=ANY(%s::text[])
                    ORDER BY created_at,id LIMIT 1
                    """,
                    (
                        context.tenant_id,
                        list(
                            {
                                canonicalize_url(target.url),
                                canonicalize_url(metadata.canonical_url or target.url),
                            }
                        ),
                    ),
                ).fetchone()
                source_url_id = str(source_url_row[0]) if source_url_row is not None else None
            if source_url_id is None:
                # Compatibility path for a task written by a legacy collector
                # during a rolling deployment.  Modern tasks already carry the
                # immutable source_url_id on every occurrence.
                canonical_url = canonicalize_url(target.url)
                host = (urlsplit(canonical_url).hostname or "").lower().rstrip(".")
                if not host:
                    raise ApplicationError(
                        "source URL identity is missing",
                        type="source_url_identity_missing",
                        non_retryable=True,
                    )
                site_key = f"{context.tenant_id}|{host}"
                site_row = connection.execute(
                    """
                    INSERT INTO platform.source_site
                      (id,pub_id,tenant_id,host,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (tenant_id,host)
                    DO UPDATE SET updated_at=GREATEST(
                      platform.source_site.updated_at,EXCLUDED.updated_at)
                    RETURNING id
                    """,
                    (
                        _stable_source_uuid(site_key),
                        _stable_source_pub_id("sit", site_key),
                        context.tenant_id,
                        host,
                        context.created_at,
                        context.created_at,
                    ),
                ).fetchone()
                if site_row is None:
                    raise ApplicationError(
                        "source site identity was not persisted",
                        type="source_url_identity_missing",
                        non_retryable=True,
                    )
                canonical_hash = sha256(canonical_url.encode()).hexdigest()
                url_key = f"{context.tenant_id}|{canonical_hash}|{canonical_url}"
                source_url_row = connection.execute(
                    """
                    INSERT INTO platform.source_url
                      (id,pub_id,tenant_id,site_id,canonical_url,canonical_url_hash,
                       normalization_version,first_raw_url,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (tenant_id,canonical_url_hash,canonical_url)
                    DO UPDATE SET updated_at=GREATEST(
                      platform.source_url.updated_at,EXCLUDED.updated_at)
                    RETURNING id
                    """,
                    (
                        _stable_source_uuid(url_key),
                        _stable_source_pub_id("url", url_key),
                        context.tenant_id,
                        site_row[0],
                        canonical_url,
                        canonical_hash,
                        URL_NORMALIZATION_VERSION,
                        target.url,
                        context.created_at,
                        context.created_at,
                    ),
                ).fetchone()
                if source_url_row is None:
                    raise ApplicationError(
                        "source URL identity was not persisted",
                        type="source_url_identity_missing",
                        non_retryable=True,
                    )
                source_url_id = str(source_url_row[0])
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
            document_row = connection.execute(
                """
                INSERT INTO platform.source_document
                  (id,pub_id,tenant_id,project_id,run_id,source_url_id,url,url_hash,host,final_url,
                   http_status,fetched_at,extract_status,extractor,bytes,text_cas_key,
                   text_sha256,canonical_url,redirect_chain,page_title,site_name,publisher,
                   authors,language,content_format,published_at_raw,published_at,
                   published_at_timezone,published_at_precision,published_at_source,
                   published_at_confidence,published_at_candidates,modified_at,first_seen_at,
                   last_verified_at,metadata_parser_version,created_at,updated_at)
                VALUES
                  (gen_random_uuid(),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                   %s,%s::jsonb,%s,%s,%s,%s::jsonb,%s,'html',%s,%s,%s,%s,%s,%s,
                   %s::jsonb,%s,%s,%s,%s,now(),now())
                ON CONFLICT (run_id,url_hash) DO UPDATE SET
                  source_url_id=EXCLUDED.source_url_id,
                  final_url=EXCLUDED.final_url,
                  http_status=EXCLUDED.http_status,
                  fetched_at=EXCLUDED.fetched_at,
                  extract_status=EXCLUDED.extract_status,
                  extractor=EXCLUDED.extractor,
                  bytes=EXCLUDED.bytes,
                  text_cas_key=EXCLUDED.text_cas_key,
                  text_sha256=EXCLUDED.text_sha256,
                  canonical_url=EXCLUDED.canonical_url,
                  redirect_chain=EXCLUDED.redirect_chain,
                  page_title=EXCLUDED.page_title,
                  site_name=EXCLUDED.site_name,
                  publisher=EXCLUDED.publisher,
                  authors=EXCLUDED.authors,
                  language=EXCLUDED.language,
                  published_at_raw=EXCLUDED.published_at_raw,
                  published_at=EXCLUDED.published_at,
                  published_at_timezone=EXCLUDED.published_at_timezone,
                  published_at_precision=EXCLUDED.published_at_precision,
                  published_at_source=EXCLUDED.published_at_source,
                  published_at_confidence=EXCLUDED.published_at_confidence,
                  published_at_candidates=EXCLUDED.published_at_candidates,
                  modified_at=EXCLUDED.modified_at,
                  last_verified_at=EXCLUDED.last_verified_at,
                  metadata_parser_version=EXCLUDED.metadata_parser_version,
                  updated_at=now()
                RETURNING id
                """,
                (
                    document_pub_id,
                    context.tenant_id,
                    context.project_id,
                    context.run_id,
                    source_url_id,
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
                    metadata.canonical_url or final_url or target.url,
                    json.dumps(list(redirect_chain), ensure_ascii=False, separators=(",", ":")),
                    metadata.title,
                    metadata.site_name,
                    metadata.publisher,
                    json.dumps(list(metadata.authors), ensure_ascii=False, separators=(",", ":")),
                    metadata.language,
                    metadata.published_at_raw,
                    metadata.published_at,
                    metadata.published_at_timezone,
                    metadata.published_at_precision,
                    metadata.published_at_source,
                    metadata.published_at_confidence,
                    json.dumps(
                        metadata.candidates_json(),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    metadata.modified_at,
                    fetched_at,
                    fetched_at,
                    metadata.parser_version,
                ),
            ).fetchone()
            if document_row is None:
                raise RuntimeError("source document upsert returned no identity")
            source_document_id = str(document_row[0])
            if extract_status == "ok":
                attempt_state = "succeeded"
            elif extract_status == "blocked":
                attempt_state = "blocked"
            elif http_status in {404, 410}:
                attempt_state = "gone"
            elif extract_status == "extract_empty":
                attempt_state = "partial"
            elif extract_status == "timeout" or http_status is None or http_status >= 500:
                attempt_state = "retry_wait"
            else:
                attempt_state = "failed"
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"source-fetch-attempt:{context.tenant_pub_id}:{source_url_id}",),
            )
            attempt_ordinal_row = connection.execute(
                """
                SELECT COALESCE(max(attempt_ordinal),0)+1
                FROM platform.source_fetch_attempt WHERE source_url_id=%s
                """,
                (source_url_id,),
            ).fetchone()
            if attempt_ordinal_row is None:
                raise ApplicationError(
                    "source fetch attempt ordinal was not returned",
                    type="source_fetch_persistence_error",
                    non_retryable=True,
                )
            attempt_ordinal = int(attempt_ordinal_row[0])
            attempt_pub_id = (
                "fat_"
                + sha256(
                    f"{context.tenant_pub_id}|{source_url_id}|{attempt_ordinal}".encode()
                ).hexdigest()[:26]
            )
            attempt_row = connection.execute(
                """
                INSERT INTO platform.source_fetch_attempt
                  (id,pub_id,tenant_id,project_id,source_url_id,run_id,attempt_ordinal,
                   fetcher,state,requested_url,final_url,redirect_chain,http_status,
                   error_code,error_detail,started_at,finished_at,next_retry_at)
                VALUES
                  (gen_random_uuid(),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,
                   %s,%s,%s,%s,%s)
                RETURNING id
                """,
                (
                    attempt_pub_id,
                    context.tenant_id,
                    context.project_id,
                    source_url_id,
                    context.run_id,
                    attempt_ordinal,
                    extractor or "httpx-browser-v2",
                    attempt_state,
                    target.url,
                    final_url,
                    json.dumps(list(redirect_chain), ensure_ascii=False, separators=(",", ":")),
                    http_status,
                    None if extract_status == "ok" else extract_status,
                    None if extract_status == "ok" else "public page was not fully extracted",
                    fetched_at,
                    fetched_at,
                    fetched_at + timedelta(minutes=15) if attempt_state == "retry_wait" else None,
                ),
            ).fetchone()
            if attempt_row is None:
                raise ApplicationError(
                    "source fetch attempt was not persisted",
                    type="source_fetch_persistence_error",
                    non_retryable=True,
                )
            snapshot_key = "|".join(
                (
                    context.tenant_pub_id,
                    source_url_id,
                    text_sha256 or extract_status,
                    extractor or "none",
                    fetched_at.isoformat(),
                )
            )
            connection.execute(
                """
                INSERT INTO platform.source_page_snapshot
                  (id,pub_id,tenant_id,project_id,source_url_id,source_document_id,
                   fetch_attempt_id,snapshot_state,final_url,http_status,title,site_name,
                   author,account_name,published_at,metadata,body_object_key,body_sha256,
                   text_sha256,extractor_version,captured_at,created_at)
                VALUES
                  (gen_random_uuid(),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,
                   %s::jsonb,%s,%s,%s,%s,%s,now())
                ON CONFLICT DO NOTHING
                """,
                (
                    "snp_" + sha256(snapshot_key.encode()).hexdigest()[:26],
                    context.tenant_id,
                    context.project_id,
                    source_url_id,
                    source_document_id,
                    attempt_row[0],
                    attempt_state
                    if attempt_state in {"succeeded", "partial", "blocked", "gone"}
                    else "failed",
                    final_url,
                    http_status,
                    metadata.title,
                    metadata.site_name,
                    metadata.authors[0] if metadata.authors else None,
                    metadata.published_at,
                    json.dumps(
                        {
                            "publisher": metadata.publisher,
                            "language": metadata.language,
                            "published_at_precision": metadata.published_at_precision,
                            "published_at_source": metadata.published_at_source,
                            "redirect_chain": list(redirect_chain),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    text_cas_key,
                    text_sha256,
                    text_sha256,
                    extractor,
                    fetched_at,
                ),
            )
            self._link_with_connection(
                connection,
                context=context,
                target=target,
                source_document_pub_id=document_pub_id,
            )
            self._sync_citation_metadata_with_connection(
                connection,
                context=context,
                target=target,
                source_document_pub_id=document_pub_id,
                metadata=metadata,
            )
            self._sync_source_quote_matches_with_connection(
                connection,
                context=context,
                source_document_pub_id=document_pub_id,
                matches=exact_source_quote_matches(context, target, text),
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
            document = connection.execute(
                """
                SELECT canonical_url,published_at_raw,published_at,published_at_timezone,
                       published_at_precision,published_at_source,published_at_confidence,
                       published_at_candidates,metadata_parser_version
                FROM platform.source_document WHERE pub_id=%s
                """,
                (source_document_pub_id,),
            ).fetchone()
            if document is not None:
                self._sync_citation_metadata_with_connection(
                    connection,
                    context=context,
                    target=target,
                    source_document_pub_id=source_document_pub_id,
                    metadata=SourceMetadata(
                        canonical_url=document[0],
                        published_at_raw=document[1],
                        published_at=document[2],
                        published_at_timezone=document[3],
                        published_at_precision=document[4],
                        published_at_source=document[5],
                        published_at_confidence=document[6] or "unknown",
                        parser_version=document[8] or "legacy-backfill-v1",
                    ),
                    candidates_json=document[7] or [],
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
        # Analysis delivery and W2 fetching are asynchronous.  Sharing this
        # transaction lock with AnalyticsService closes the interleaving where
        # each side could otherwise update before the other row became visible.
        for answer_pub_id in sorted(set(target.task_pub_ids)):
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"answer-source-metadata:{context.tenant_pub_id}:{answer_pub_id}",),
            )
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

    @staticmethod
    def _sync_citation_metadata_with_connection(
        connection: psycopg.Connection[Any],
        *,
        context: RunSourceContext,
        target: SourceTarget,
        source_document_pub_id: str,
        metadata: SourceMetadata,
        candidates_json: list[dict[str, Any]] | None = None,
    ) -> None:
        """Cover both race orders: source fetch before or after answer analysis."""

        canonical_candidates = {
            canonicalize_url(target.url),
            canonicalize_url(metadata.canonical_url or target.url),
        }
        for answer_pub_id in target.task_pub_ids:
            connection.execute(
                """
                UPDATE analytics.citation_fact
                SET source_document_pub_id=%s,
                    published_at_raw=%s,
                    published_at=%s,
                    published_at_timezone=%s,
                    published_at_precision=%s,
                    published_at_source=%s,
                    published_at_confidence=%s,
                    published_at_candidates=%s::jsonb
                WHERE tenant_pub_id=%s AND answer_pub_id=%s
                  AND (original_url=%s OR canonical_url=ANY(%s::text[]))
                """,
                (
                    source_document_pub_id,
                    metadata.published_at_raw,
                    metadata.published_at,
                    metadata.published_at_timezone,
                    metadata.published_at_precision,
                    metadata.published_at_source,
                    metadata.published_at_confidence,
                    json.dumps(
                        candidates_json
                        if candidates_json is not None
                        else metadata.candidates_json(),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    context.tenant_pub_id,
                    answer_pub_id,
                    target.url,
                    list(canonical_candidates),
                ),
            )

    @staticmethod
    def _sync_source_quote_matches_with_connection(
        connection: psycopg.Connection[Any],
        *,
        context: RunSourceContext,
        source_document_pub_id: str,
        matches: list[dict[str, Any]],
    ) -> None:
        for match in matches:
            relation_pub_id = (
                "acr_"
                + sha256(
                    (
                        f"{context.tenant_pub_id}|{match['answer_pub_id']}|{match['ordinal']}"
                    ).encode()
                ).hexdigest()[:26]
            )
            connection.execute(
                """
                INSERT INTO analytics.answer_citation_relation
                  (pub_id,tenant_pub_id,answer_pub_id,ordinal,source_document_pub_id,
                   mapping_status,source_quote,source_text_start,source_text_end,
                   source_quote_hash,source_match_status,source_match_version,relation,
                   classifier_version,review_status)
                VALUES
                  (%s,%s,%s,%s,%s,'unmapped',%s,%s,%s,%s,'exact',
                   'source-exact-match-v1','unverified','source-exact-match-v1','unreviewed')
                ON CONFLICT (tenant_pub_id,answer_pub_id,ordinal) DO UPDATE SET
                  source_document_pub_id=EXCLUDED.source_document_pub_id,
                  source_quote=EXCLUDED.source_quote,
                  source_text_start=EXCLUDED.source_text_start,
                  source_text_end=EXCLUDED.source_text_end,
                  source_quote_hash=EXCLUDED.source_quote_hash,
                  source_match_status='exact',
                  source_match_version='source-exact-match-v1',
                  updated_at=now()
                """,
                (
                    relation_pub_id,
                    context.tenant_pub_id,
                    match["answer_pub_id"],
                    match["ordinal"],
                    source_document_pub_id,
                    match["source_quote"],
                    match["source_text_start"],
                    match["source_text_end"],
                    match["source_quote_hash"],
                ),
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
        redirect_chain = tuple(
            {"url": str(item.url), "http_status": item.status_code} for item in response.history
        )
        content_type = response.headers.get("content-type", "").lower()
        if status < 400 and "html" not in content_type and "text/" not in content_type:
            # 非 HTML 正文（PDF/图片/JSON 等）：不抽文本，如实记 0 字节走 extract_empty 判定
            return HttpAttempt(
                final_url,
                status,
                "",
                None,
                None,
                f"content-type:{content_type}",
                redirect_chain=redirect_chain,
            )
        text = extract_text_from_html(response.text) if status < 400 else ""
        metadata = (
            extract_source_metadata(
                response.text,
                final_url=final_url,
                response_headers=dict(response.headers),
            )
            if status < 400 and "html" in content_type
            else None
        )
        return HttpAttempt(
            final_url,
            status,
            text,
            _EXTRACTOR_HTTPX if text else None,
            None,
            None,
            metadata=metadata,
            redirect_chain=redirect_chain,
        )

    def fetch_browser(self, url: str) -> HttpAttempt:
        self._ensure_browser()
        page = self._context.new_page()
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT_MS)
            page.wait_for_timeout(_SETTLE_MS)
            extracted = page.evaluate(_EXTRACT_TEXT_JS)
            raw_text = extracted if isinstance(extracted, str) else ""
            text = clean_text(raw_text)
            final_url = str(page.url)
            page_html = page.content()
            response_headers = response.all_headers() if response is not None else {}
            http_status = response.status if response is not None else None
            metadata = extract_source_metadata(
                page_html,
                final_url=final_url,
                response_headers=response_headers,
            )
        finally:
            try:
                page.close()
            except Exception:
                pass
        return HttpAttempt(
            final_url,
            http_status,
            text,
            _EXTRACTOR_BROWSER if text else None,
            None,
            None,
            metadata=metadata,
        )

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
        if existing is not None and existing.extract_status == "ok":
            # Successful immutable snapshots are reusable.  Failed/blocked
            # documents are retried and produce a new source_fetch_attempt.
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
                        metadata=attempt.metadata,
                        redirect_chain=attempt.redirect_chain,
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
                        metadata=attempt.metadata,
                        redirect_chain=attempt.redirect_chain,
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
