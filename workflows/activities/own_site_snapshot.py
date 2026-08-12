"""W4 官网素材采集 activity（``capture_own_site_snapshots``）：只采证据不做分析。

需求规格：developlog/specs/geo-evaluation-improvement-20260805.md W4 节。
随采集 run 顺带执行（workflow 挂接由协调者集成，本模块只提供 activity 与可单测核心）：

- own_source 命中的引用页：抓正文 + 整页截图进 evidence CAS
  （kind=``own_site_snapshot``，relation_type=``own_site_snapshot``，
  from=对应 collection_task/answer pub_id）。
- intake_profile.website 为种子（空则回退 brand.website）：官网主页 + 主页同 host
  链接快照（relation_type=``own_site_page``，from=collection_run pub_id）。

纪律：

- INV-32 零合成：抓不到/存不进就如实进 ``failures``，绝不编造正文；只存证据不做分析。
- 重放确定性：evidence_pub_id 按 ``sha256(tenant|run|url|kind|asset)`` 派生
  （collection.py:263-273 同模式，asset ∈ {text,png} 区分同页两份资产）；
  capture_time 用 run.created_at（从 DB 读，固定）；adapter_version 固定串。
  EvidenceService 漂移规则要求同 evidence_pub_id 再 capture 全部字段一致，
  本模块写入前先查既存资产：已存在就直接复用（activity 重试幂等），不重复写入。
- env 配置（秘密绝不进 task payload）：``GEO_OWN_SITE_SNAPSHOT_ENABLED``（默认 true，
  false 时直接返回 skipped="disabled"）；``GEO_OWN_SITE_SNAPSHOT_LIMIT``（官网页上限，
  默认 5 硬上限 20，主页本身算 1 条）；``GEO_OWN_SITE_CITATION_LIMIT``（每个
  回答的 own_source 引用页上限，默认 200 硬上限 500）；同一官网 URL 被多个
  回答引用时只抓取一次，但必须向每个回答扇出证据关系；
  ``GEO_OWN_SITE_CITATION_RUN_LIMIT`` 作为单 run 安全上限（默认 20000，硬上限
  50000），按回答轮询使用，避免早期回答独占；``GEO_OWN_SITE_PROXY_URL``（可选，
  缺省直连）。
- 执行模型与 doubao_adapter 同款：sync 浏览器驱动包在 ``asyncio.to_thread`` 里跑，
  activity 协程侧每 10s 泵一次 heartbeat。官网是公开页，普通 launch+new_context
  （无需登录态 profile）；驱动首选 patchright（browser_driver 延迟加载）。
- 限速纪律：页间 sleep 2s；UA/locale/timezone 照 doubao_adapter.py:614-625 取值。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Protocol
from urllib.parse import urlsplit

import psycopg
import structlog
from geo_platform.config import get_settings
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.evidence.service import EvidenceService
from geo_platform.tenancy.psycopg import tenant_connection
from psycopg.rows import dict_row
from temporalio import activity
from temporalio.exceptions import ApplicationError

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from workflows.activities.browser_driver import load_sync_browser_driver

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# env 配置
# ---------------------------------------------------------------------------

ENV_ENABLED = "GEO_OWN_SITE_SNAPSHOT_ENABLED"
ENV_SNAPSHOT_LIMIT = "GEO_OWN_SITE_SNAPSHOT_LIMIT"
ENV_CITATION_LIMIT = "GEO_OWN_SITE_CITATION_LIMIT"
ENV_CITATION_RUN_LIMIT = "GEO_OWN_SITE_CITATION_RUN_LIMIT"
ENV_PROXY_URL = "GEO_OWN_SITE_PROXY_URL"

_DEFAULT_SNAPSHOT_LIMIT = 5
_HARD_CAP_SNAPSHOT_LIMIT = 20
_DEFAULT_CITATION_LIMIT = 200
_HARD_CAP_CITATION_LIMIT = 500
_DEFAULT_CITATION_RUN_LIMIT = 20_000
_HARD_CAP_CITATION_RUN_LIMIT = 50_000

_HEARTBEAT_INTERVAL_S = 10.0  # 与 doubao_adapter 同款泵频（heartbeat_timeout=30s 约束）
_GOTO_TIMEOUT_MS = 20_000
_SETTLE_MS = 1_500
_INTER_PAGE_DELAY_S = 2.0
_MAX_TEXT_CHARS = 20_000

_EVIDENCE_KIND = "own_site_snapshot"
_ADAPTER_VERSION = "own-site-snapshot-v1"
_TEXT_EXTRACTOR = "innertext-v1"

_RELATION_CITATION = "own_site_snapshot"  # 引用页快照挂 answer(task) pub_id
_RELATION_SITE_PAGE = "own_site_page"  # 官网页快照挂 run pub_id

# 旧链 doubao_client.py 实测 UA（doubao_adapter.py:86-89 同值）
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
        ".json",
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

# 复制自 doubao_adapter.py:361-364（写边界隔离，允许复制）：env 代理 URL 解析。
_PROXY_RE = re.compile(
    r"^(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*)://"
    r"(?:(?P<user>[^:@/]+)(?::(?P<password>[^@/]*))?@)?"
    r"(?P<host>[^/@]+)$"
)


def _parse_proxy(proxy_url: str) -> dict[str, str] | None:
    """把 env 代理 URL 拆成 Playwright proxy dict；不匹配返回 None。"""
    match = _PROXY_RE.match(proxy_url.strip())
    if not match:
        return None
    out: dict[str, str] = {"server": f"{match.group('scheme')}://{match.group('host')}"}
    if match.group("user"):
        out["username"] = match.group("user")
        out["password"] = match.group("password") or ""
    return out


# 复制自 doubao_adapter.py:205-270（写边界隔离，允许复制）：
# 整页截图前把页面内部 overflow 滚动容器压平进文档流（旧链 _FLATTEN_FOR_SCREENSHOT_JS）。
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

# 正文抽取（证据级保真，不做分析）：优先 article/main/[role=main]，回退 body.innerText；
# 同时抽出全部 a[href] 绝对 URL 供官网页发现。
_EXTRACT_TEXT_AND_LINKS_JS = r"""
() => {
  const root = document.querySelector('article, main, [role="main"]') || document.body;
  const text = root && root.innerText ? root.innerText : '';
  const links = Array.from(document.querySelectorAll('a[href]'))
    .map(a => a.href)
    .filter(h => typeof h === 'string' && h.length > 0);
  return {text, links};
}
"""

_RELATION_INSERT_SQL = """
INSERT INTO evidence.evidence_relation
  (tenant_pub_id,from_pub_id,to_pub_id,relation_type)
VALUES (%s,%s,%s,%s)
ON CONFLICT (tenant_pub_id,from_pub_id,to_pub_id,relation_type) DO NOTHING
"""

_EXISTING_ASSET_SQL = (
    "SELECT byte_size FROM evidence.evidence_asset WHERE tenant_pub_id=%s AND pub_id=%s"
)


# ---------------------------------------------------------------------------
# activity 输入输出契约
# ---------------------------------------------------------------------------


@dataclass
class OwnSiteSnapshotInput:
    tenant_pub_id: str
    project_pub_id: str
    run_pub_id: str


@dataclass
class OwnSiteCaptured:
    url: str
    # own_site_snapshot=own_source 引用页 / own_site_page=官网页（与 relation_type 同词表）
    kind: str
    # 正文 JSON 资产的 evidence_pub_id（截图资产为同派生式 asset="png" 变体）
    evidence_pub_id: str
    bytes: int  # 正文 JSON + 整页截图合计字节


@dataclass
class OwnSiteFailure:
    url: str
    error: str


@dataclass(frozen=True)
class OwnSitePlanCoverage:
    answer_pub_id: str
    eligible_official_urls: int
    planned_official_urls: int
    truncated_official_urls: int
    coverage_rate: float | None
    truncation_reason: str | None


@dataclass
class OwnSiteSnapshotResult:
    captured: list[OwnSiteCaptured] = field(default_factory=list)
    failures: list[OwnSiteFailure] = field(default_factory=list)
    planning_coverage: list[OwnSitePlanCoverage] = field(default_factory=list)
    citation_limit: int | None = None
    citation_run_limit: int | None = None
    skipped: str | None = None  # "disabled" / "no_website" / None


@dataclass(frozen=True)
class OwnSiteSnapshotConfig:
    enabled: bool
    snapshot_limit: int
    citation_limit: int
    proxy_url: str | None
    citation_run_limit: int = _DEFAULT_CITATION_RUN_LIMIT
    headless: bool = True

    @classmethod
    def from_env(cls) -> OwnSiteSnapshotConfig:
        raw_enabled = os.environ.get(ENV_ENABLED, "").strip().lower()
        proxy_url = os.environ.get(ENV_PROXY_URL, "").strip() or None
        return cls(
            enabled=raw_enabled not in {"0", "false", "no", "off"},
            snapshot_limit=_env_limit(
                ENV_SNAPSHOT_LIMIT,
                default=_DEFAULT_SNAPSHOT_LIMIT,
                hard_cap=_HARD_CAP_SNAPSHOT_LIMIT,
            ),
            citation_limit=_env_limit(
                ENV_CITATION_LIMIT,
                default=_DEFAULT_CITATION_LIMIT,
                hard_cap=_HARD_CAP_CITATION_LIMIT,
            ),
            proxy_url=proxy_url,
            citation_run_limit=_env_bounded_positive_int(
                ENV_CITATION_RUN_LIMIT,
                default=_DEFAULT_CITATION_RUN_LIMIT,
                hard_cap=_HARD_CAP_CITATION_RUN_LIMIT,
            ),
        )


def _env_limit(name: str, *, default: int, hard_cap: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, min(value, hard_cap))


def _env_bounded_positive_int(name: str, *, default: int, hard_cap: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, min(value, hard_cap))


# ---------------------------------------------------------------------------
# 目标规划（纯函数，全部可单测）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapshotTarget:
    url: str  # 实际抓取的 URL（引用页保持被引用原文，不改写）
    key: str  # 归一化去重键
    kind: str  # "citation"=own_source 引用页 / "site_page"=官网页
    task_pub_id: str | None = None
    # 同 URL 可能被多个回答引用。task_pub_id 保留首个值以兼容旧调用，
    # task_pub_ids 是完整证据关系集合。
    task_pub_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class FetchedPage:
    url: str  # 请求 URL
    final_url: str  # 跳转后最终 URL
    title: str
    text: str
    links: list[str]
    png_bytes: bytes
    fetched_at: datetime


def normalize_host(value: str) -> str | None:
    """website/URL → 归一化 host：剥 scheme、端口、www. 前缀、尾点，小写。无法解析 → None。

    Brand.website/IntakeProfile.website 可能带 scheme（https://...），必须在此剥离，
    绝不能拿原串与 citation hostname 直接比（永不命中）。
    """
    candidate = value.strip()
    if not candidate:
        return None
    try:
        if "://" in candidate:
            host = urlsplit(candidate).hostname
        else:
            # 无 scheme：// 前缀让 urlsplit 按 netloc 解析（含端口剥离）
            host = urlsplit(f"//{candidate}").hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None


def host_matches_domain(host: str, domain: str) -> bool:
    """与 analyzer.py:58-62 own_source 判定同语义：== 或 "." 后缀（输入均已归一化）。"""
    return host == domain or host.endswith(f".{domain}")


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
    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        return True
    return any(path.endswith(ext) for ext in _STATIC_EXTENSIONS)


def homepage_url(website: str) -> str:
    """website 字段 → 可抓取的种子 URL（无 scheme 补 https://）。"""
    candidate = website.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    return candidate


def plan_citation_targets(
    tasks: list[tuple[str, list[dict[str, Any]]]],
    domain: str,
    limit: int,
    run_limit: int = _DEFAULT_CITATION_RUN_LIMIT,
) -> list[SnapshotTarget]:
    """own_source 引用页规划。

    ``limit`` 是**每个回答**的上限，不是全 run 共享的 Top-N。抓取目标
    仍按 URL 全局去重，但保留引用它的所有回答，便于存证后扇出 relation。
    ``run_limit`` 只作为可配置运营保护：按引用序号跨回答轮询，先给每份
    回答规划第 1 个官网 URL，再规划第 2 个，避免较早回答独占额度。
    """
    per_answer_limit = max(1, min(limit, _HARD_CAP_CITATION_LIMIT))
    safe_run_limit = max(1, min(run_limit, _HARD_CAP_CITATION_RUN_LIMIT))
    targets: list[SnapshotTarget] = []
    target_index_by_key: dict[str, int] = {}
    task_order = {task_pub_id: index for index, (task_pub_id, _citations) in enumerate(tasks)}
    planned_by_answer: list[tuple[str, list[tuple[str, str]]]] = []
    for task_pub_id, citations in tasks:
        candidates: list[tuple[str, str]] = []
        task_seen: set[str] = set()
        for citation in citations:
            url = citation.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            host = normalize_host(url)
            if host is None or not host_matches_domain(host, domain):
                continue
            key = url_dedupe_key(url)
            if key is None or key in task_seen:
                continue
            task_seen.add(key)
            candidates.append((url.strip(), key))
            if len(candidates) >= per_answer_limit:
                break
        planned_by_answer.append((task_pub_id, candidates))

    for citation_rank in range(per_answer_limit):
        for task_pub_id, candidates in planned_by_answer:
            if citation_rank >= len(candidates):
                continue
            url, key = candidates[citation_rank]
            existing_index = target_index_by_key.get(key)
            if existing_index is not None:
                existing = targets[existing_index]
                if task_pub_id not in existing.task_pub_ids:
                    linked = tuple(
                        sorted(
                            (*existing.task_pub_ids, task_pub_id),
                            key=lambda value: task_order[value],
                        )
                    )
                    targets[existing_index] = SnapshotTarget(
                        url=existing.url,
                        key=existing.key,
                        kind="citation",
                        task_pub_id=existing.task_pub_id,
                        task_pub_ids=linked,
                    )
                continue
            if len(targets) >= safe_run_limit:
                continue
            target_index_by_key[key] = len(targets)
            targets.append(
                SnapshotTarget(
                    url=url,
                    key=key,
                    kind="citation",
                    task_pub_id=task_pub_id,
                    task_pub_ids=(task_pub_id,),
                )
            )
    return targets


def citation_plan_coverage(
    tasks: list[tuple[str, list[dict[str, Any]]]],
    targets: list[SnapshotTarget],
    *,
    domain: str,
    limit: int,
    run_limit: int,
) -> list[OwnSitePlanCoverage]:
    """Return durable per-answer coverage for official citation planning."""

    per_answer_limit = max(1, min(limit, _HARD_CAP_CITATION_LIMIT))
    safe_run_limit = max(1, min(run_limit, _HARD_CAP_CITATION_RUN_LIMIT))
    planned_by_answer: dict[str, set[str]] = {answer_pub_id: set() for answer_pub_id, _ in tasks}
    for target in targets:
        for answer_pub_id in target.task_pub_ids:
            planned_by_answer.setdefault(answer_pub_id, set()).add(target.key)

    rows: list[OwnSitePlanCoverage] = []
    for answer_pub_id, citations in tasks:
        eligible: set[str] = set()
        for citation in citations:
            url = citation.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            host = normalize_host(url)
            key = url_dedupe_key(url)
            if (
                host is None
                or not host_matches_domain(host, domain)
                or key is None
                or is_static_resource_url(key)
            ):
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
            OwnSitePlanCoverage(
                answer_pub_id=answer_pub_id,
                eligible_official_urls=eligible_count,
                planned_official_urls=planned_count,
                truncated_official_urls=max(0, eligible_count - planned_count),
                coverage_rate=(
                    round(planned_count / eligible_count, 4) if eligible_count else None
                ),
                truncation_reason="+".join(reasons) or None,
            )
        )
    return rows


def select_site_targets(
    links: list[str],
    site_host: str,
    limit: int,
    exclude: frozenset[str] = frozenset(),
) -> list[SnapshotTarget]:
    """官网页发现：主页同 host 链接，去重、排除静态资源/锚点/mailto/tel，上限截断。"""
    targets: list[SnapshotTarget] = []
    seen: set[str] = set(exclude)
    if limit <= 0:
        return targets
    for link in links:
        if not isinstance(link, str) or not link.strip():
            continue
        key = url_dedupe_key(link)
        if key is None or key in seen:
            continue
        if normalize_host(link) != site_host:  # 同 host（不含子域，发现策略从紧）
            continue
        if is_static_resource_url(key):
            continue
        seen.add(key)
        targets.append(SnapshotTarget(url=link.strip(), key=key, kind="site_page"))
        if len(targets) >= limit:
            break
    return targets


def merge_targets(
    citation_targets: list[SnapshotTarget],
    site_targets: list[SnapshotTarget],
) -> list[SnapshotTarget]:
    """a∩b 去重：同一 URL 只抓一次，citation（引用页）身份优先。"""
    merged: list[SnapshotTarget] = []
    seen: set[str] = set()
    for target in [*citation_targets, *site_targets]:
        if target.key in seen:
            continue
        seen.add(target.key)
        merged.append(target)
    return merged


def relation_for_target(target: SnapshotTarget, run_pub_id: str) -> tuple[str, str]:
    """→ (from_pub_id, relation_type)：引用页挂 answer(task) pub，官网页挂 run pub。"""
    if target.kind == "citation":
        if target.task_pub_id is None:
            raise ValueError("citation target requires task_pub_id")
        return target.task_pub_id, _RELATION_CITATION
    return run_pub_id, _RELATION_SITE_PAGE


def relations_for_target(target: SnapshotTarget, run_pub_id: str) -> list[tuple[str, str]]:
    """返回目标的全部证据关系。

    引用页同 URL 可被多个回答引用；每个回答都必须可回溯到同一份
    正文/截图资产。
    """
    if target.kind != "citation":
        return [relation_for_target(target, run_pub_id)]
    task_ids = target.task_pub_ids or ((target.task_pub_id,) if target.task_pub_id else ())
    if not task_ids:
        raise ValueError("citation target requires task_pub_id")
    return [(task_pub_id, _RELATION_CITATION) for task_pub_id in task_ids]


def derive_evidence_pub_id(
    tenant_pub_id: str,
    run_pub_id: str,
    url: str,
    kind: str,
    asset: str,
) -> str:
    """确定性派生（collection.py:263-273 同模式）：同 (tenant,run,url,kind,asset) 必同 id。"""
    stable_key = "|".join((tenant_pub_id, run_pub_id, url, kind, asset))
    return f"evd_{sha256(stable_key.encode()).hexdigest()[:26]}"


_INLINE_WS_RE = re.compile(r"[ \t　]+")
_BLANK_RUN_RE = re.compile(r"\n{3,}")


def clean_text(raw: str, limit: int = _MAX_TEXT_CHARS) -> str:
    """去多余空白（行内空白压单格、空行压一行、行首尾 strip），截 ≤20000 字符。"""
    lines = [_INLINE_WS_RE.sub(" ", line).strip() for line in raw.splitlines()]
    text = "\n".join(lines)
    text = _BLANK_RUN_RE.sub("\n\n", text).strip()
    return text[:limit]


def build_text_payload(url: str, fetched: FetchedPage) -> bytes:
    """正文证据 JSON：{url, final_url, title, fetched_at, text, text_bytes, extractor}。"""
    payload = {
        "url": url,
        "final_url": fetched.final_url,
        "title": fetched.title,
        "fetched_at": fetched.fetched_at.isoformat(),
        "text": fetched.text,
        "text_bytes": len(fetched.text.encode("utf-8")),
        "extractor": _TEXT_EXTRACTOR,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


# ---------------------------------------------------------------------------
# 可替换薄层：抓取 / DB 读 / 存证（单测全部 fake 注入）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunSnapshotContext:
    run_pub_id: str
    project_pub_id: str
    created_at: datetime  # tz-aware；固定为 run 创建时间，作 capture_time
    website: str | None  # intake_profile.website 优先、brand.website 回退（本层已完成回退）
    tasks: list[tuple[str, list[dict[str, Any]]]]  # (task_pub_id, citations)


@dataclass(frozen=True)
class PersistedPage:
    evidence_pub_id: str  # 正文 JSON 资产
    png_evidence_pub_id: str  # 整页截图资产
    byte_size: int  # 两份资产合计字节


class OwnSiteFetchSession(Protocol):
    """抓取 session 协议：给 URL 返回 FetchedPage 或抛错（单测 fake 注入）。"""

    def fetch(self, url: str) -> FetchedPage: ...

    def close(self) -> None: ...


class SnapshotContextLoader(Protocol):
    """DB 读薄层：run 行 + 本 run 全部 collection_task + website（含回退）。"""

    def load(
        self,
        tenant_pub_id: str,
        run_pub_id: str,
        project_pub_id: str,
    ) -> RunSnapshotContext | None: ...


class EvidenceSink(Protocol):
    """存证薄层：每页两份 evidence + relation，返回派生 pub_id 与合计字节。"""

    def persist_page(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        run_pub_id: str,
        target: SnapshotTarget,
        fetched: FetchedPage,
        from_pub_id: str,
        relation_type: str,
        capture_time: datetime,
    ) -> PersistedPage: ...


# ---------------------------------------------------------------------------
# 生产实现：psycopg loader / EvidenceService sink / patchright fetcher
# ---------------------------------------------------------------------------


def _postgres_dsn() -> str:
    """与 s02.py:430-434 同款 DSN 读法（worker 覆盖优先，psycopg scheme 归一）。"""
    settings = get_settings()
    return os.getenv("S02_POSTGRES_DSN") or (
        settings.worker_postgres_dsn or settings.postgres_dsn
    ).replace("postgresql+psycopg://", "postgresql://")


class _PostgresContextLoader:
    """platform.* 表走 app.tenant_id（uuid）RLS：先按 pub_id 解析 tenant，再置双 selector。"""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def load(
        self,
        tenant_pub_id: str,
        run_pub_id: str,
        project_pub_id: str,
    ) -> RunSnapshotContext | None:
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
            website = self._load_website(connection, run_row["project_id"])
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
                log.warning("own_site_citations_unparseable", task_pub_id=row["pub_id"])
                citations = []
            if not isinstance(citations, list):
                citations = []
            tasks.append(
                (str(row["pub_id"]), [item for item in citations if isinstance(item, dict)])
            )
        return RunSnapshotContext(
            run_pub_id=str(run_row["pub_id"]),
            project_pub_id=str(run_row["project_pub_id"]),
            created_at=created_at,
            website=website,
            tasks=tasks,
        )

    @staticmethod
    def _load_website(connection: Any, project_id: Any) -> str | None:
        """intake_profile.website 为主种子，为空回退 brand.website；两者都空 → None。"""
        intake_row = connection.execute(
            "SELECT website FROM platform.intake_profile WHERE project_id=%s",
            (project_id,),
        ).fetchone()
        if intake_row is not None:
            website = intake_row["website"]
            if isinstance(website, str) and website.strip():
                return website.strip()
        brand_row = connection.execute(
            "SELECT website FROM platform.brand WHERE project_id=%s "
            "ORDER BY created_at, pub_id LIMIT 1",
            (project_id,),
        ).fetchone()
        if brand_row is not None:
            website = brand_row["website"]
            if isinstance(website, str) and website.strip():
                return website.strip()
        return None


class _EvidenceServiceSink:
    """生产存证：EvidenceService.capture + 同事务 evidence_relation（tenant_connection）。

    每页两份资产（正文 JSON / 整页截图 PNG），evidence_pub_id 确定性派生；
    写入前先查既存资产——activity 重试时直接复用不重复写入（幂等，且规避
    同一 pub_id 二次 capture 的重放漂移 ValueError）。
    """

    def __init__(self, *, dsn: str, service: EvidenceService) -> None:
        self._dsn = dsn
        self._service = service

    def persist_page(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        run_pub_id: str,
        target: SnapshotTarget,
        fetched: FetchedPage,
        from_pub_id: str,
        relation_type: str,
        capture_time: datetime,
    ) -> PersistedPage:
        text_pub_id = derive_evidence_pub_id(
            tenant_pub_id, run_pub_id, target.url, _EVIDENCE_KIND, "text"
        )
        png_pub_id = derive_evidence_pub_id(
            tenant_pub_id, run_pub_id, target.url, _EVIDENCE_KIND, "png"
        )
        provenance = RedactedProvenance(
            platform_account_pub_id=None,
            browser_profile_version_pub_id=None,
            session_event_pub_id=None,
            channel=CaptureChannel.WEB,
            authorization_scope=(),
            adapter_version=_ADAPTER_VERSION,
            capture_time=capture_time,
            access_class=AccessClass.CUSTOMER_PRIVATE,
        )
        text_payload = build_text_payload(target.url, fetched)
        with tenant_connection(self._dsn, tenant_pub_id) as connection:
            text_size = self._ensure_asset(
                connection,
                tenant_pub_id=tenant_pub_id,
                project_pub_id=project_pub_id,
                evidence_pub_id=text_pub_id,
                payload=text_payload,
                mime_type="application/json",
                source_url=target.url,
                provenance=provenance,
            )
            png_size = self._ensure_asset(
                connection,
                tenant_pub_id=tenant_pub_id,
                project_pub_id=project_pub_id,
                evidence_pub_id=png_pub_id,
                payload=fetched.png_bytes,
                mime_type="image/png",
                source_url=target.url,
                provenance=provenance,
            )
            for to_pub_id in (text_pub_id, png_pub_id):
                connection.execute(
                    _RELATION_INSERT_SQL,
                    (tenant_pub_id, from_pub_id, to_pub_id, relation_type),
                )
            connection.commit()
        return PersistedPage(
            evidence_pub_id=text_pub_id,
            png_evidence_pub_id=png_pub_id,
            byte_size=text_size + png_size,
        )

    def _ensure_asset(
        self,
        connection: psycopg.Connection[Any],
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        evidence_pub_id: str,
        payload: bytes,
        mime_type: str,
        source_url: str,
        provenance: RedactedProvenance,
    ) -> int:
        row = connection.execute(_EXISTING_ASSET_SQL, (tenant_pub_id, evidence_pub_id)).fetchone()
        if row is not None:
            return int(row[0])
        stored = self._service.capture(
            evidence_pub_id=evidence_pub_id,
            tenant_pub_id=tenant_pub_id,
            project_pub_id=project_pub_id,
            kind=_EVIDENCE_KIND,
            payload=payload,
            mime_type=mime_type,
            source_url=source_url,
            provenance=provenance,
            db_connection=connection,
        )
        return stored.byte_size


class _PlaywrightOwnSiteSession:
    """公开页抓取 session：普通 launch + new_context（无需登录态 profile）。"""

    def __init__(self, config: OwnSiteSnapshotConfig) -> None:
        self._config = config
        self._pw_cm: Any = None
        self._browser: Any = None
        self._context: Any = None
        driver, sync_playwright, _timeout_error = load_sync_browser_driver()
        try:
            self._pw_cm = sync_playwright()
            pw = self._pw_cm.__enter__()
            self._browser = pw.chromium.launch(
                headless=config.headless,
                proxy=_parse_proxy(config.proxy_url) if config.proxy_url else None,
                args=["--lang=zh-CN"],
            )
            self._context = self._browser.new_context(
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5"},
                user_agent=_USER_AGENT,
            )
            self._context.set_default_timeout(_GOTO_TIMEOUT_MS)
        except Exception as exc:
            self.close()
            raise ApplicationError(
                f"browser-launch-failed({driver}): {type(exc).__name__}: {exc}",
                type="browser_launch_failed",
            ) from exc

    def fetch(self, url: str) -> FetchedPage:
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT_MS)
            page.wait_for_timeout(_SETTLE_MS)  # 短 settle：公开页静态渲染 + 反爬 JS 挂载
            extracted = page.evaluate(_EXTRACT_TEXT_AND_LINKS_JS)
            if not isinstance(extracted, dict):
                extracted = {}
            raw_text = extracted.get("text")
            raw_links = extracted.get("links")
            text = clean_text(raw_text if isinstance(raw_text, str) else "")
            links = [
                item
                for item in (raw_links if isinstance(raw_links, list) else [])
                if isinstance(item, str)
            ]
            try:
                title = str(page.title())
            except Exception:
                title = ""
            final_url = str(page.url)
            png_bytes = _capture_full_page_bytes(page)
            fetched_at = datetime.now(UTC)
        finally:
            try:
                page.close()
            except Exception:
                pass
        return FetchedPage(
            url=url,
            final_url=final_url,
            title=title,
            text=text,
            links=links,
            png_bytes=png_bytes,
            fetched_at=fetched_at,
        )

    def close(self) -> None:
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


def _capture_full_page_bytes(page: Any) -> bytes:
    """复制自 doubao_adapter.py:1332-1370（写边界隔离）：flatten 后整页截图，
    CDP captureBeyondViewport 优先，page.screenshot(full_page=True) 兜底。"""
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
                return base64.b64decode(png_b64)
        except Exception:
            pass
    return bytes(page.screenshot(full_page=True))


# ---------------------------------------------------------------------------
# 同步核心（生产线程内跑；单测直接调用，依赖全注入）
# ---------------------------------------------------------------------------


def _noop_progress(stage: str, url: str) -> None:
    del stage, url


def execute_own_site_capture(
    item: OwnSiteSnapshotInput,
    *,
    config: OwnSiteSnapshotConfig,
    loader: SnapshotContextLoader,
    session_factory: Callable[[OwnSiteSnapshotConfig], OwnSiteFetchSession],
    sink: EvidenceSink,
    sleep: Callable[[float], None] = time.sleep,
    on_progress: Callable[[str, str], None] | None = None,
) -> OwnSiteSnapshotResult:
    """读 DB → 目标规划 → 逐页抓取 → 存证。单页失败进 failures 不中断（INV-32 如实记录）。"""
    if not config.enabled:
        return OwnSiteSnapshotResult(
            citation_limit=config.citation_limit,
            citation_run_limit=config.citation_run_limit,
            skipped="disabled",
        )
    progress = on_progress if on_progress is not None else _noop_progress
    progress("load_context", "")
    context = loader.load(item.tenant_pub_id, item.run_pub_id, item.project_pub_id)
    if context is None:
        raise ApplicationError("collection run not found", type="run_not_found", non_retryable=True)
    site_host = normalize_host(context.website or "")
    if not context.website or site_host is None:
        return OwnSiteSnapshotResult(
            citation_limit=config.citation_limit,
            citation_run_limit=config.citation_run_limit,
            skipped="no_website",
        )
    homepage = homepage_url(context.website)
    homepage_key = url_dedupe_key(homepage)
    citation_targets = plan_citation_targets(
        context.tasks,
        site_host,
        config.citation_limit,
        config.citation_run_limit,
    )
    planning_coverage = citation_plan_coverage(
        context.tasks,
        citation_targets,
        domain=site_host,
        limit=config.citation_limit,
        run_limit=config.citation_run_limit,
    )
    # 主页若同时是引用页，按引用页身份存证（a∩b 去重、引用优先）
    homepage_citation = next((t for t in citation_targets if t.key == homepage_key), None)
    homepage_target = homepage_citation or SnapshotTarget(
        url=homepage, key=homepage_key or homepage, kind="site_page"
    )

    captured: list[OwnSiteCaptured] = []
    failures: list[OwnSiteFailure] = []
    fetch_count = 0

    def _fetch(session: OwnSiteFetchSession, target: SnapshotTarget) -> FetchedPage | None:
        nonlocal fetch_count
        if fetch_count:
            sleep(_INTER_PAGE_DELAY_S)  # 限速纪律：页间 ~2s
        fetch_count += 1
        progress("fetch", target.url)
        try:
            return session.fetch(target.url)
        except Exception as exc:
            failures.append(OwnSiteFailure(url=target.url, error=f"{type(exc).__name__}: {exc}"))
            return None

    def _persist(target: SnapshotTarget, fetched: FetchedPage) -> None:
        relations = relations_for_target(target, context.run_pub_id)
        progress("persist", target.url)
        persisted = None
        relation_type = relations[0][1]
        for from_pub_id, relation_type in relations:
            try:
                persisted = sink.persist_page(
                    tenant_pub_id=item.tenant_pub_id,
                    project_pub_id=context.project_pub_id,
                    run_pub_id=context.run_pub_id,
                    target=target,
                    fetched=fetched,
                    from_pub_id=from_pub_id,
                    relation_type=relation_type,
                    capture_time=context.created_at,
                )
            except Exception as exc:
                failures.append(
                    OwnSiteFailure(
                        url=target.url,
                        error=f"persist({from_pub_id}): {type(exc).__name__}: {exc}",
                    )
                )
        if persisted is None:
            return
        captured.append(
            OwnSiteCaptured(
                url=target.url,
                kind=relation_type,
                evidence_pub_id=persisted.evidence_pub_id,
                bytes=persisted.byte_size,
            )
        )

    session = session_factory(config)
    try:
        homepage_page = _fetch(session, homepage_target)
        if homepage_page is not None:
            _persist(homepage_target, homepage_page)
            site_targets = select_site_targets(
                homepage_page.links,
                site_host,
                config.snapshot_limit - 1,  # 主页本身算 1 条
                exclude=frozenset({homepage_key}) if homepage_key else frozenset(),
            )
        else:
            site_targets = []
        remaining_citations = [t for t in citation_targets if t is not homepage_citation]
        for target in merge_targets(remaining_citations, site_targets):
            fetched = _fetch(session, target)
            if fetched is not None:
                _persist(target, fetched)
    finally:
        session.close()
    result = OwnSiteSnapshotResult(
        captured=captured,
        failures=failures,
        planning_coverage=planning_coverage,
        citation_limit=config.citation_limit,
        citation_run_limit=config.citation_run_limit,
        skipped=None,
    )
    log.info(
        "own_site_capture_done",
        run_pub_id=context.run_pub_id,
        captured=len(result.captured),
        failures=len(result.failures),
        planned_official_urls=sum(row.planned_official_urls for row in planning_coverage),
        truncated_official_urls=sum(row.truncated_official_urls for row in planning_coverage),
        answers_truncated=sum(row.truncated_official_urls > 0 for row in planning_coverage),
    )
    return result


# ---------------------------------------------------------------------------
# activity 入口与异步泵
# ---------------------------------------------------------------------------


async def run_own_site_snapshots(
    item: OwnSiteSnapshotInput,
    *,
    config: OwnSiteSnapshotConfig,
    loader: SnapshotContextLoader,
    sink: EvidenceSink,
    session_factory: Callable[[OwnSiteSnapshotConfig], OwnSiteFetchSession] | None = None,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> OwnSiteSnapshotResult:
    """异步泵封装：默认实现跑 asyncio.to_thread + 10s heartbeat 泵（doubao_adapter 同款）。

    注入 session_factory 时（单测）同步内联执行，不起线程。
    """
    uses_default_session = session_factory is None
    factory: Callable[[OwnSiteSnapshotConfig], OwnSiteFetchSession] = (
        session_factory if session_factory is not None else _PlaywrightOwnSiteSession
    )
    if heartbeat is None:

        def heartbeat(payload: dict[str, Any]) -> None:
            del payload

    progress: dict[str, str] = {"stage": "start", "url": ""}

    def _on_progress(stage: str, url: str) -> None:
        progress["stage"] = stage
        progress["url"] = url

    def _blocking() -> OwnSiteSnapshotResult:
        return execute_own_site_capture(
            item,
            config=config,
            loader=loader,
            session_factory=factory,
            sink=sink,
            sleep=sleep,
            on_progress=_on_progress,
        )

    if uses_default_session:
        thread = asyncio.ensure_future(asyncio.to_thread(_blocking))
        while True:
            heartbeat({"run_pub_id": item.run_pub_id, **progress})
            done, _pending = await asyncio.wait({thread}, timeout=_HEARTBEAT_INTERVAL_S)
            if done:
                break
        return thread.result()
    heartbeat({"run_pub_id": item.run_pub_id, **progress})
    return _blocking()


@activity.defn(name="capture_own_site_snapshots")
async def capture_own_site_snapshots(item: OwnSiteSnapshotInput) -> OwnSiteSnapshotResult:
    """W4 官网素材采集 activity 入口：env 配置 + 真实 DB/CAS/浏览器接线。"""
    config = OwnSiteSnapshotConfig.from_env()
    if not config.enabled:
        return OwnSiteSnapshotResult(skipped="disabled")
    dsn = _postgres_dsn()
    settings = get_settings()
    store = ContentAddressedObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
    )
    store.ensure_bucket()
    return await run_own_site_snapshots(
        item,
        config=config,
        loader=_PostgresContextLoader(dsn),
        sink=_EvidenceServiceSink(dsn=dsn, service=EvidenceService(dsn=dsn, store=store)),
        heartbeat=activity.heartbeat,
    )
