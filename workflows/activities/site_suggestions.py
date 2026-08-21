"""官网诊断建议 activity（``generate_site_audit_suggestions``）：W2 审计后的建议生成。

在 audit_run_sources 之后随 run 顺带触发（collection workflow patch
"site-suggestions-v1"，fail-open sidecar：失败只 warning，绝不拖垮采集 run）。

仅当本 run 有 own_site 信源文档时执行（own_site 判定与 analytics 同口径：最新
asset_confirmation_version.website 的 host，www/裸域/子域互配）。输入：own_site
source_document 的正文要点（CAS 正文截断）+ 该文档两口径 audit 判定
（transcript/factual 的 verdict+rationale，只取 audit_status='ok' 行）+ 项目品牌
与官网 host。LLM 产出结构化建议数组，程序校验后整批落
platform.site_audit_suggestion（T2，共享确定性 batch_pub_id）。

纪律：

- 程序校验（不过则丢弃该条并计数，绝不静默改写）：category/severity 必须在契约
  词表内；title/detail 非空；evidence_document_pub_id 必须由 LLM 给的
  evidence_url 精确映射到本 run 输入的 own_site 文档，映射不上置 NULL
  （evidence_dropped 计数）；整批上限 10 条（超出记 truncated）。
- 幂等：batch_pub_id = 确定性派生（tenant|run|model|prompt_version），sink 先查
  批次存在性（已存在 → skipped="already_generated" 零 LLM 零写）；行 pub_id =
  sha256(batch_pub_id|ordinal) 确定性派生 + ON CONFLICT DO NOTHING，重试安全。
- 诚实降级（INV-32）：LLM key 缺失 → llm_unavailable=true 零调用零落库；无
  own_site 文档 / 官网 host 未知 → skipped 如实标记；LLM 失败 → failures 留痕。
  建议本身是生成物（非测量），model + prompt_version 随批次落库可溯源。
- LLM：OpenAI Responses API 非流式 + text.format json_schema 严格输出，120s；
  key 只走 settings（GEO_AUDIT_LLM_*，缺省复用 GEO_RESEARCH_LLM_*，与 W2/W3
  同口径），严禁入库/日志。T2 不发 outbox 事件（PG 为唯一读源）。
- env：``GEO_SITE_SUGGESTIONS_ENABLED``（缺省 true，false → disabled 零 IO）。
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import psycopg
import structlog
from geo_platform.config import Settings, get_settings
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from psycopg.rows import dict_row
from temporalio import activity
from temporalio.exceptions import ApplicationError

from workflows.activities.disparagement import _postgres_dsn
from workflows.activities.source_audit import (
    AuditLlmConfig,
    SourceTextStore,
    _MinioSourceTextStore,
    _normalize_base_url,
    audit_llm_config_from_settings,
)

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# env / 常量
# ---------------------------------------------------------------------------

ENV_ENABLED = "GEO_SITE_SUGGESTIONS_ENABLED"

PROMPT_VERSION = "site-suggestions-v1"
_LLM_TIMEOUT_S = 120.0
_HEARTBEAT_INTERVAL_S = 10.0

CATEGORIES = ("content_coverage", "citability", "fact_consistency", "crawlability", "other")
SEVERITIES = ("high", "medium", "low")

_MAX_SUGGESTIONS = 10  # 契约上限：整批 ≤10 条
_DOCS_PER_SUGGESTION_REQUEST = 10  # 单次提示词批量；不得作为官网事实全集上限
_MAX_DOC_EXCERPT_CHARS = 3_000  # 每文档正文要点截断
_MAX_TITLE_CHARS = 200
_MAX_DETAIL_CHARS = 2_000

_DIMENSIONS = ("transcript", "factual")

# ---------------------------------------------------------------------------
# own_site 判定（与 api/geo_platform/analytics/service.py 同口径的复制件——
# 写边界隔离，允许复制；改判定规则必须两边同步）
# ---------------------------------------------------------------------------


def _host_from_website(value: object) -> str | None:
    """官网 URL → host（小写、去 scheme/路径/端口）；缺 scheme 裸串按 https 解析。"""
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    candidate = value if "://" in value else f"https://{value}"
    try:
        hostname = urlsplit(candidate).hostname
    except ValueError:
        return None
    return hostname or None


def _is_own_site(host: object, own_site_host: str | None) -> bool:
    """own_site 判定：与官网 host 相同、互为 www./裸域变体、或为官网裸域的子域。"""
    if not isinstance(host, str) or not host or not own_site_host:
        return False
    candidate = host.lower()
    apex = own_site_host[4:] if own_site_host.startswith("www.") else own_site_host
    if not apex:
        return candidate == own_site_host
    return (
        candidate == own_site_host
        or candidate == apex
        or candidate == f"www.{apex}"
        or candidate.endswith(f".{apex}")
    )


# ---------------------------------------------------------------------------
# activity 输入输出契约
# ---------------------------------------------------------------------------


@dataclass
class SiteSuggestionsInput:
    tenant_pub_id: str
    project_pub_id: str
    run_pub_id: str


@dataclass(frozen=True)
class OwnSiteDocument:
    """一条送审 own_site 文档（正文要点 + 两口径 audit 判定）。"""

    pub_id: str
    url: str
    host: str
    excerpt: str  # CAS 正文截断（要点）
    transcript_verdict: str  # "" = 无 ok 判定
    transcript_rationale: str
    factual_verdict: str
    factual_rationale: str


@dataclass(frozen=True)
class SuggestionDraft:
    """LLM 产出、程序校验后的单条建议（落 T2 前的最终形态）。"""

    category: str
    severity: str
    title: str
    detail: str
    evidence_document_pub_id: str | None


@dataclass
class SiteSuggestionsResult:
    own_site_documents: int = 0
    suggestions: int = 0
    dropped: int = 0  # 枚举/空字段校验丢弃条数
    evidence_dropped: int = 0  # evidence_url 映射不上 → 置 NULL 条数
    truncated: int = 0  # 超上限未采纳条数（文档截断 + 建议截断合计）
    batch_pub_id: str = ""
    skipped: str = ""  # no_own_site_host / no_own_site_documents / already_generated
    llm_unavailable: bool = False
    disabled: bool = False
    failures: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 建议 prompt（site-suggestions-v1）+ schema + 程序校验
# ---------------------------------------------------------------------------

_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "evidence_url": {"type": "string"},
                },
                "required": ["category", "severity", "title", "detail", "evidence_url"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["suggestions"],
    "additionalProperties": False,
}

_INSTRUCTIONS = (
    "你是 GEO 官网诊断顾问。给你某品牌官网（own_site）若干页面的【正文要点】与这些页面"
    "在 AI 搜索信源审计中的【两口径判定】（transcript=AI 引述 vs 原文；factual=原文 vs "
    "客户确认事实；verdict 词表 accurate/inaccurate/unsupported/unverifiable）。"
    "产出提升官网在 AI 搜索中可见度与引用质量的整改建议，按 JSON schema 输出数组：\n"
    "- category：content_coverage=内容覆盖不足（AI 常问主题官网无对应内容）；"
    "citability=可引用性差（内容在但结构/表述不利于 AI 摘引）；"
    "fact_consistency=事实不一致（官网陈述与客户确认事实/AI 引述矛盾）；"
    "crawlability=可抓取性（正文为空/过短/疑似渲染或反爬问题）；other=其他\n"
    "- severity：high/medium/low（按对 AI 可见度的影响）\n"
    "- title 一句话结论（≤50 字）；detail 整改动作与依据（≤500 字，中文）\n"
    "- evidence_url：支撑该建议的输入页面 URL，必须原样取自输入；无直接对应页面"
    "填空字符串\n"
    "只依据输入材料下判断，无充分依据的建议宁可不提；建议不超过 10 条。"
)


def build_suggestions_user_prompt(
    *, brand: str, own_site_host: str, documents: list[OwnSiteDocument]
) -> str:
    parts = [f"【品牌】{brand or '未知'}\n【官网 host】{own_site_host}\n"]
    for index, doc in enumerate(documents, start=1):
        parts.append(
            f"--- 页面 {index} ---\n"
            f"URL：{doc.url}\n"
            f"transcript 判定：{doc.transcript_verdict or '无'}；依据："
            f"{doc.transcript_rationale or '无'}\n"
            f"factual 判定：{doc.factual_verdict or '无'}；依据："
            f"{doc.factual_rationale or '无'}\n"
            f"【正文要点】\n{doc.excerpt}\n"
        )
    parts.append("请按 JSON schema 输出建议数组。")
    return "\n".join(parts)


class SuggestionsError(RuntimeError):
    """LLM 超时/5xx/传输错误/格式坏 → 本批记 failures，不落库。"""


def parse_suggestions_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Responses API output → 原始建议 dict 列表；结构坏一律 SuggestionsError。"""
    text_parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                text_parts.append(str(content.get("text") or ""))
    raw_text = "\n".join(text_parts).strip()
    if not raw_text:
        raise SuggestionsError("LLM 未返回任何文本内容")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SuggestionsError("LLM 输出 JSON 解析失败") from exc
    if not isinstance(data, dict) or not isinstance(data.get("suggestions"), list):
        raise SuggestionsError("LLM 输出缺 suggestions 数组")
    return [item for item in data["suggestions"] if isinstance(item, dict)]


def validate_suggestions(
    raw_items: list[dict[str, Any]],
    *,
    evidence_pub_by_url: dict[str, str],
    max_suggestions: int = _MAX_SUGGESTIONS,
) -> tuple[list[SuggestionDraft], int, int, int]:
    """程序校验（纯函数）→ (drafts, dropped, evidence_dropped, truncated)。

    - category/severity 词表外、title/detail 为空 → 丢弃（dropped）；
    - evidence_url 非空但必须精确命中输入 own_site 文档 URL，否则置 NULL
      （evidence_dropped）——证据归属 fail-closed，绝不指向项目外文档；
    - 整批上限 max_suggestions，超出记 truncated。
    """
    drafts: list[SuggestionDraft] = []
    dropped = 0
    evidence_dropped = 0
    for item in raw_items:
        category = str(item.get("category") or "").strip()
        severity = str(item.get("severity") or "").strip()
        title = str(item.get("title") or "").strip()
        detail = str(item.get("detail") or "").strip()
        if category not in CATEGORIES or severity not in SEVERITIES or not title or not detail:
            dropped += 1
            continue
        evidence_url = str(item.get("evidence_url") or "").strip()
        evidence_document_pub_id: str | None = None
        if evidence_url:
            evidence_document_pub_id = evidence_pub_by_url.get(evidence_url)
            if evidence_document_pub_id is None:
                evidence_dropped += 1
        drafts.append(
            SuggestionDraft(
                category=category,
                severity=severity,
                title=title[:_MAX_TITLE_CHARS],
                detail=detail[:_MAX_DETAIL_CHARS],
                evidence_document_pub_id=evidence_document_pub_id,
            )
        )
    truncated = 0
    if len(drafts) > max_suggestions:
        truncated = len(drafts) - max_suggestions
        drafts = drafts[:max_suggestions]
    return drafts, dropped, evidence_dropped, truncated


# ---------------------------------------------------------------------------
# 可替换薄层：LLM 建议 / DB 读 / CAS 读 / 落库（单测全部 fake 注入）
# ---------------------------------------------------------------------------


class SuggestionsJudge(Protocol):
    """LLM 建议薄层（单测 fake 注入）。"""

    def suggest(
        self, *, brand: str, own_site_host: str, documents: list[OwnSiteDocument]
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class SiteDocumentRow:
    pub_id: str
    url: str
    host: str
    text_cas_key: str
    text_sha256: str
    transcript_verdict: str
    transcript_rationale: str
    factual_verdict: str
    factual_rationale: str


@dataclass(frozen=True)
class SiteSuggestionsContext:
    tenant_pub_id: str
    tenant_id: str
    project_id: str
    project_pub_id: str
    run_id: str
    run_pub_id: str
    brand: str | None
    own_site_host: str | None  # None = 无 asset confirmation
    documents: list[SiteDocumentRow]  # 本 run extract_status='ok' 的全部文档（程序再筛 own_site）


class SiteSuggestionsLoader(Protocol):
    def load(
        self,
        tenant_pub_id: str,
        run_pub_id: str,
        project_pub_id: str,
    ) -> SiteSuggestionsContext | None: ...


class SiteSuggestionsSink(Protocol):
    """落库薄层：批次存在性检查 + 整批写入（确定性 pub_id 幂等）。"""

    def batch_exists(self, *, context: SiteSuggestionsContext, batch_pub_id: str) -> bool: ...

    def persist_batch(
        self,
        *,
        context: SiteSuggestionsContext,
        batch_pub_id: str,
        model: str,
        drafts: list[SuggestionDraft],
    ) -> int:
        """→ 实际插入行数。"""
        ...


def derive_batch_pub_id(
    tenant_pub_id: str, run_pub_id: str, model: str, prompt_version: str
) -> str:
    """T2 batch_pub_id 确定性派生：同 (tenant,run,model,prompt_version) 必同批。"""
    stable_key = "|".join((tenant_pub_id, run_pub_id, model, prompt_version))
    return f"sab_{sha256(stable_key.encode()).hexdigest()[:26]}"


def derive_suggestion_pub_id(batch_pub_id: str, ordinal: int) -> str:
    stable_key = "|".join((batch_pub_id, str(ordinal)))
    return f"sas_{sha256(stable_key.encode()).hexdigest()[:26]}"


# ---------------------------------------------------------------------------
# 生产实现：Responses judge / psycopg loader / sink
# ---------------------------------------------------------------------------


class _ResponsesApiSuggestionsJudge:
    """OpenAI Responses API 非流式建议（text.format json_schema 严格输出，120s）。

    ``client`` 可注入（测试 mock 接缝，source_audit._ResponsesApiJudge 同模式）。
    """

    def __init__(self, config: AuditLlmConfig, *, client: httpx.Client | None = None) -> None:
        self._config = config
        self._client = client

    def suggest(
        self, *, brand: str, own_site_host: str, documents: list[OwnSiteDocument]
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {
            "model": self._config.model,
            "instructions": _INSTRUCTIONS,
            "input": build_suggestions_user_prompt(
                brand=brand, own_site_host=own_site_host, documents=documents
            ),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "site_audit_suggestions",
                    "strict": True,
                    "schema": _JSON_SCHEMA,
                }
            },
        }
        payload = self._post(body)
        return parse_suggestions_payload(payload)

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        if self._client is not None:
            return self._post_with(self._client, body)
        bases = [self._config.base_url]
        if self._config.base_url_fallback.strip():
            bases.append(self._config.base_url_fallback)
        error: SuggestionsError | None = None
        for base in bases:
            try:
                with httpx.Client(
                    base_url=_normalize_base_url(base),
                    headers={"Authorization": f"Bearer {self._config.api_key}"},
                    timeout=_LLM_TIMEOUT_S,
                    trust_env=False,
                ) as client:
                    return self._post_with(client, body)
            except SuggestionsError as exc:
                # 主通道失败 → 换备通道再试一次；POST 幂等无害。
                error = exc
        assert error is not None
        raise error

    @staticmethod
    def _post_with(client: httpx.Client, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = client.post("/responses", json=body)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SuggestionsError(f"LLM 上游调用失败: {type(exc).__name__}") from exc
        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            raise SuggestionsError("LLM 响应非 JSON") from exc
        return payload


class _PostgresSiteSuggestionsLoader:
    """platform.* 表走 app.tenant_id（uuid）RLS：先按 pub_id 解析 tenant，再置双 selector。"""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def load(
        self,
        tenant_pub_id: str,
        run_pub_id: str,
        project_pub_id: str,
    ) -> SiteSuggestionsContext | None:
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
                SELECT r.id, r.pub_id, r.project_id, p.pub_id AS project_pub_id
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
            brand_row = connection.execute(
                """
                SELECT name FROM platform.brand
                WHERE project_id = %s ORDER BY created_at, pub_id LIMIT 1
                """,
                (run_row["project_id"],),
            ).fetchone()
            website_row = connection.execute(
                """
                SELECT website FROM platform.asset_confirmation_version
                WHERE project_id = %s
                ORDER BY revision DESC, created_at DESC, pub_id DESC
                LIMIT 1
                """,
                (run_row["project_id"],),
            ).fetchone()
            document_rows = connection.execute(
                """
                SELECT pub_id, url, host, text_cas_key, text_sha256
                FROM platform.source_document
                WHERE run_id = %s AND extract_status = 'ok'
                  AND text_cas_key IS NOT NULL AND text_sha256 IS NOT NULL
                ORDER BY created_at, pub_id
                """,
                (run_row["id"],),
            ).fetchall()
            audit_rows = connection.execute(
                """
                SELECT d.pub_id AS document_pub_id, a.dimension, a.verdict, a.rationale,
                       a.created_at
                FROM platform.source_audit a
                JOIN platform.source_document d ON d.id = a.source_document_id
                WHERE d.run_id = %s AND a.audit_status = 'ok' AND a.verdict IS NOT NULL
                ORDER BY a.created_at, a.pub_id
                """,
                (run_row["id"],),
            ).fetchall()
        # 同文档同口径多条 ok 判定：取最新（ORDER BY created_at 后者覆盖前者）
        audits: dict[tuple[str, str], tuple[str, str]] = {}
        for row in audit_rows:
            audits[(str(row["document_pub_id"]), str(row["dimension"]))] = (
                str(row["verdict"]),
                str(row["rationale"] or ""),
            )
        documents: list[SiteDocumentRow] = []
        for row in document_rows:
            doc_pub_id = str(row["pub_id"])
            transcript = audits.get((doc_pub_id, "transcript"), ("", ""))
            factual = audits.get((doc_pub_id, "factual"), ("", ""))
            documents.append(
                SiteDocumentRow(
                    pub_id=doc_pub_id,
                    url=str(row["url"]),
                    host=str(row["host"]),
                    text_cas_key=str(row["text_cas_key"]),
                    text_sha256=str(row["text_sha256"]),
                    transcript_verdict=transcript[0],
                    transcript_rationale=transcript[1],
                    factual_verdict=factual[0],
                    factual_rationale=factual[1],
                )
            )
        return SiteSuggestionsContext(
            tenant_pub_id=tenant_pub_id,
            tenant_id=str(tenant_row["id"]),
            project_id=str(run_row["project_id"]),
            project_pub_id=str(run_row["project_pub_id"]),
            run_id=str(run_row["id"]),
            run_pub_id=str(run_row["pub_id"]),
            brand=(str(brand_row["name"]).strip() if brand_row is not None else None),
            own_site_host=(
                _host_from_website(website_row["website"]) if website_row is not None else None
            ),
            documents=documents,
        )


class _PostgresSiteSuggestionsSink:
    """生产落库：批次存在性门 + 确定性 pub_id + ON CONFLICT DO NOTHING，重试安全。"""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    @staticmethod
    def _connection(dsn: str, context: SiteSuggestionsContext) -> psycopg.Connection[Any]:
        connection = psycopg.connect(dsn)
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true), "
            "set_config('app.tenant_pub_id', %s, true)",
            (context.tenant_id, context.tenant_pub_id),
        )
        return connection

    def batch_exists(self, *, context: SiteSuggestionsContext, batch_pub_id: str) -> bool:
        with self._connection(self._dsn, context) as connection:
            row = connection.execute(
                "SELECT 1 FROM platform.site_audit_suggestion WHERE batch_pub_id=%s LIMIT 1",
                (batch_pub_id,),
            ).fetchone()
        return row is not None

    def persist_batch(
        self,
        *,
        context: SiteSuggestionsContext,
        batch_pub_id: str,
        model: str,
        drafts: list[SuggestionDraft],
    ) -> int:
        inserted = 0
        with self._connection(self._dsn, context) as connection:
            for ordinal, draft in enumerate(drafts):
                cursor = connection.execute(
                    """
                    INSERT INTO platform.site_audit_suggestion
                      (pub_id,project_pub_id,batch_pub_id,category,severity,title,detail,
                       evidence_document_pub_id,model)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (pub_id) DO NOTHING
                    """,
                    (
                        derive_suggestion_pub_id(batch_pub_id, ordinal),
                        context.project_pub_id,
                        batch_pub_id,
                        draft.category,
                        draft.severity,
                        draft.title,
                        draft.detail,
                        draft.evidence_document_pub_id,
                        model,
                    ),
                )
                inserted += cursor.rowcount
            connection.commit()
        return inserted


# ---------------------------------------------------------------------------
# 同步核心（生产线程内跑；单测直接调用，依赖全注入）
# ---------------------------------------------------------------------------


def _noop_progress(stage: str, label: str) -> None:
    del stage, label


def execute_site_suggestions(
    item: SiteSuggestionsInput,
    *,
    enabled: bool,
    llm: AuditLlmConfig,
    judge: SuggestionsJudge | None,
    loader: SiteSuggestionsLoader,
    text_store: SourceTextStore,
    sink: SiteSuggestionsSink,
    on_progress: Callable[[str, str], None] | None = None,
) -> SiteSuggestionsResult:
    """读 DB → own_site 门 → CAS 正文要点 → LLM 建议 → 程序校验 → 整批落 T2。"""
    if not enabled:
        return SiteSuggestionsResult(disabled=True)
    progress = on_progress if on_progress is not None else _noop_progress
    progress("load_context", "")
    context = loader.load(item.tenant_pub_id, item.run_pub_id, item.project_pub_id)
    if context is None:
        raise ApplicationError("collection run not found", type="run_not_found", non_retryable=True)
    result = SiteSuggestionsResult()
    if not context.own_site_host:
        result.skipped = "no_own_site_host"
        return result
    own_site_rows = [
        row for row in context.documents if _is_own_site(row.host, context.own_site_host)
    ]
    if not own_site_rows:
        result.skipped = "no_own_site_documents"
        return result
    result.own_site_documents = len(own_site_rows)

    # CAS 读正文要点：读失败的文档如实记 failures 并剔除（绝不拿残缺正文送审）
    documents: list[OwnSiteDocument] = []
    for row in own_site_rows:
        progress("read_text", row.url)
        try:
            text = text_store.get_text(row.text_cas_key, row.text_sha256)
        except Exception as exc:
            result.failures.append(f"cas_read:{row.url}: {type(exc).__name__}: {exc}")
            continue
        documents.append(
            OwnSiteDocument(
                pub_id=row.pub_id,
                url=row.url,
                host=row.host,
                excerpt=text[:_MAX_DOC_EXCERPT_CHARS],
                transcript_verdict=row.transcript_verdict,
                transcript_rationale=row.transcript_rationale,
                factual_verdict=row.factual_verdict,
                factual_rationale=row.factual_rationale,
            )
        )
    if not documents:
        result.skipped = "no_own_site_documents"
        return result

    # LLM key 缺失 → 诚实跳过：零调用零落库
    if not llm.api_key or judge is None:
        result.llm_unavailable = True
        log.warning(
            "site_suggestions_llm_unavailable",
            run_pub_id=item.run_pub_id,
            own_site_documents=result.own_site_documents,
        )
        return result

    model = llm.model or "unknown"
    batch_pub_id = derive_batch_pub_id(
        context.tenant_pub_id, context.run_pub_id, model, PROMPT_VERSION
    )
    result.batch_pub_id = batch_pub_id
    if sink.batch_exists(context=context, batch_pub_id=batch_pub_id):
        result.skipped = "already_generated"
        return result

    raw_items: list[dict[str, Any]] = []
    for batch_start in range(0, len(documents), _DOCS_PER_SUGGESTION_REQUEST):
        document_batch = documents[batch_start : batch_start + _DOCS_PER_SUGGESTION_REQUEST]
        progress("suggest", f"{context.own_site_host}:{batch_start}")
        try:
            raw_items.extend(
                judge.suggest(
                    brand=context.brand or "",
                    own_site_host=context.own_site_host,
                    documents=document_batch,
                )
            )
        except SuggestionsError as exc:
            result.failures.append(f"llm_error: batch={batch_start}: {exc}")
        except Exception as exc:
            result.failures.append(f"batch={batch_start}: {type(exc).__name__}: {exc}")
    if not raw_items:
        return result
    drafts, dropped, evidence_dropped, truncated = validate_suggestions(
        raw_items,
        evidence_pub_by_url={doc.url: doc.pub_id for doc in documents},
    )
    result.dropped = dropped
    result.evidence_dropped = evidence_dropped
    result.truncated += truncated
    if not drafts:
        # 全部建议被程序校验丢弃：如实计数，不落空批次（下轮可重试）
        log.warning(
            "site_suggestions_all_dropped",
            run_pub_id=item.run_pub_id,
            dropped=dropped,
        )
        return result
    progress("persist", batch_pub_id)
    result.suggestions = sink.persist_batch(
        context=context, batch_pub_id=batch_pub_id, model=model, drafts=drafts
    )
    log.info(
        "site_suggestions_done",
        run_pub_id=item.run_pub_id,
        batch_pub_id=batch_pub_id,
        own_site_documents=result.own_site_documents,
        suggestions=result.suggestions,
        dropped=result.dropped,
        evidence_dropped=result.evidence_dropped,
        truncated=result.truncated,
        failures=len(result.failures),
    )
    return result


# ---------------------------------------------------------------------------
# activity 入口与异步泵
# ---------------------------------------------------------------------------


async def run_site_suggestions(
    item: SiteSuggestionsInput,
    *,
    enabled: bool,
    llm: AuditLlmConfig,
    loader: SiteSuggestionsLoader,
    text_store: SourceTextStore,
    sink: SiteSuggestionsSink,
    judge: SuggestionsJudge | None = None,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
) -> SiteSuggestionsResult:
    """异步泵封装：默认实现跑 asyncio.to_thread + 10s heartbeat 泵（W2/W3 同款）。

    注入 judge 时（单测）同步内联执行，不起线程。
    """
    uses_default_judge = judge is None
    effective_judge: SuggestionsJudge | None = judge
    if effective_judge is None and llm.api_key:
        effective_judge = _ResponsesApiSuggestionsJudge(llm)
    if heartbeat is None:

        def heartbeat(payload: dict[str, Any]) -> None:
            del payload

    progress: dict[str, str] = {"stage": "start", "label": ""}

    def _on_progress(stage: str, label: str) -> None:
        progress["stage"] = stage
        progress["label"] = label

    def _blocking() -> SiteSuggestionsResult:
        return execute_site_suggestions(
            item,
            enabled=enabled,
            llm=llm,
            judge=effective_judge,
            loader=loader,
            text_store=text_store,
            sink=sink,
            on_progress=_on_progress,
        )

    if uses_default_judge:
        thread = asyncio.ensure_future(asyncio.to_thread(_blocking))
        while True:
            heartbeat({"run_pub_id": item.run_pub_id, **progress})
            done, _pending = await asyncio.wait({thread}, timeout=_HEARTBEAT_INTERVAL_S)
            if done:
                break
        return thread.result()
    heartbeat({"run_pub_id": item.run_pub_id, **progress})
    return _blocking()


@activity.defn(name="generate_site_audit_suggestions")
async def generate_site_audit_suggestions(
    item: SiteSuggestionsInput,
) -> SiteSuggestionsResult:
    """官网诊断建议 activity 入口：env 配置 + 真实 DB/CAS/LLM 接线。"""
    raw_enabled = os.environ.get(ENV_ENABLED, "").strip().lower()
    enabled = raw_enabled not in {"0", "false", "no", "off"}
    if not enabled:
        return SiteSuggestionsResult(disabled=True)
    dsn = _postgres_dsn()
    settings: Settings = get_settings()
    llm = audit_llm_config_from_settings(settings)
    store = ContentAddressedObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
    )
    return await run_site_suggestions(
        item,
        enabled=enabled,
        llm=llm,
        loader=_PostgresSiteSuggestionsLoader(dsn),
        text_store=_MinioSourceTextStore(store),
        sink=_PostgresSiteSuggestionsSink(dsn),
        heartbeat=activity.heartbeat,
    )


__all__ = [
    "CATEGORIES",
    "ENV_ENABLED",
    "PROMPT_VERSION",
    "SEVERITIES",
    "OwnSiteDocument",
    "SiteDocumentRow",
    "SiteSuggestionsContext",
    "SiteSuggestionsInput",
    "SiteSuggestionsResult",
    "SuggestionDraft",
    "SuggestionsError",
    "SuggestionsJudge",
    "build_suggestions_user_prompt",
    "derive_batch_pub_id",
    "derive_suggestion_pub_id",
    "execute_site_suggestions",
    "generate_site_audit_suggestions",
    "parse_suggestions_payload",
    "run_site_suggestions",
    "validate_suggestions",
]
