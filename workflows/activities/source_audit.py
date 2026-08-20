"""W2 核对层 activity（``audit_run_sources``）：信源两口径 LLM 准确性判定。

需求规格：developlog/specs/geo-evaluation-improvement-20260805.md W2 节。
对本 run 已成功抓取（extract_status="ok"）的每条 source_document 做两口径判定，
分开落两行（platform.source_audit + outbox 事件 → CH source_audit_fact）：

- 口径A 转述准确性（dimension="transcript"）：豆包引述（citations_json 里该 url 的
  cited_text）vs 信源正文 → {verdict: accurate/inaccurate/unsupported,
  quote_source, quote_answer, rationale≤500}。
- 口径B 事实准确性（dimension="factual"）：信源正文 vs 事实基底 → 同 schema。
  事实基底两级来源（可叠加）：① 客户已确认事实（intake_profile.truth_confirmed
  为真时的 selling_points + licenses 资质）；② 官网语料（本 run W4 官网快照
  正文，evidence own_site_snapshot JSON 资产，按最新 asset_confirmation_version.
  website 的 host 过滤——官网纠错前的旧域名快照绝不混入）。两级皆空 →
  口径B 落 "no_confirmed_facts"，绝不编造判定。

纪律：

- quote_source 必须是正文逐字子串、quote_answer 必须是引述/事实逐字子串
  （空白归一化后程序校验）；不过 → 丢弃该判分，落 audit_status="validation_failure"
  行如实记录（verdict 置 NULL，判分绝不入 CH 分布）。
- 抓不到正文（extract_status != "ok"）→ 两口径都落 "unverifiable"，绝不编造判定；
  引用无 cited_text → 口径A "unverifiable"；事实基底为空 → 口径B "no_confirmed_facts"。
- LLM 超时/5xx/格式坏 → "llm_error"；API key 缺失 → "llm_unavailable"（如实落库，
  绝不编造判定）。模型名 + prompt 版本随判定落库（口径A "source-audit-v1"，口径B
  "source-audit-v2"——事实基底引入官网语料后升版重判，幂等键含版本互不干扰）。
- LLM 调用：OpenAI Responses API 非流式 + text.format json_schema 严格结构化输出，
  超时 60s；key 只走 settings（GEO_AUDIT_LLM_*，缺省复用 GEO_RESEARCH_LLM_*），
  严禁入库/日志。
- 幂等：(source_document, dimension, model, prompt_version) 唯一键，重跑跳过已落库
  判定（判定结果不重复算；重判 = 升 prompt_version）；outbox event_id 确定性派生，
  同事务写入，重试安全。
- env：``GEO_SOURCE_FETCH_ENABLED``（缺省 true，false → skipped="disabled" 零 IO）。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
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

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# env / 常量
# ---------------------------------------------------------------------------

ENV_ENABLED = "GEO_SOURCE_FETCH_ENABLED"  # 与抓取层同一总开关（规格：false→两 activity 都 skipped）

PROMPT_VERSION_TRANSCRIPT = "source-audit-v1"
# 口径B v2：事实基底从「仅 intake 已确认事实」扩为「已确认事实 + 官网语料」，
# 判定语义与 prompt 均变，升版重判（旧 v1 行保留，读路径按版本取最新）。
PROMPT_VERSION_FACTUAL = "source-audit-v2"
_LLM_TIMEOUT_S = 60.0
_HEARTBEAT_INTERVAL_S = 10.0  # 与 own_site_snapshot 同款泵频
_MAX_RATIONALE_CHARS = 500
_MAX_PROMPT_TEXT_CHARS = 12_000  # 送审正文截断（判定用）；verbatim 校验仍对全文
_MAX_FACTS_CHARS = 4_000  # 口径A 引述 / 口径B 已确认事实节截断
_MAX_SITE_PAGE_CHARS = 3_000  # 官网语料单页正文截断
_MAX_SITE_PAGES = 10  # 官网语料页数上限（W4 快照上限 20，取前 10 页足够覆盖）
_MAX_SITE_CORPUS_CHARS = 24_000  # 官网语料总截断
_MAX_FACT_BASE_CHARS = 28_000  # 口径B 事实基底送审总上限（确认事实节 + 官网语料）

_DIMENSION_TRANSCRIPT = "transcript"
_DIMENSION_FACTUAL = "factual"
_DIMENSIONS = (_DIMENSION_TRANSCRIPT, _DIMENSION_FACTUAL)


def prompt_version_for(dimension: str) -> str:
    """口径各自的 prompt 版本（幂等键成分）：口径B 升 v2 后口径A 历史判定仍幂等命中。"""
    return (
        PROMPT_VERSION_TRANSCRIPT if dimension == _DIMENSION_TRANSCRIPT else PROMPT_VERSION_FACTUAL
    )


# audit_status 词表：ok / validation_failure / llm_error / llm_unavailable /
# no_confirmed_facts / unverifiable
_VERDICTS = ("accurate", "inaccurate", "unsupported")

_EVENT_TYPE = "source_audit.recorded"


# ---------------------------------------------------------------------------
# activity 输入输出契约
# ---------------------------------------------------------------------------


@dataclass
class SourceAuditInput:
    tenant_pub_id: str
    project_pub_id: str
    run_pub_id: str


@dataclass
class AuditedSource:
    url: str
    dimension: str
    verdict: str | None
    audit_status: str


@dataclass
class AuditSkipped:
    url: str
    dimension: str
    reason: str  # "already_audited"


@dataclass
class AuditFailure:
    url: str
    error: str


@dataclass
class SourceAuditResult:
    audited: list[AuditedSource] = field(default_factory=list)
    skipped: list[AuditSkipped] = field(default_factory=list)
    failures: list[AuditFailure] = field(default_factory=list)
    disabled: bool = False


# ---------------------------------------------------------------------------
# LLM 配置（settings 读取；缺省复用 GEO_RESEARCH_LLM_*）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditLlmConfig:
    api_key: str  # 空 = 未配置 → llm_unavailable（诚实降级）
    model: str
    base_url: str
    # 主备 failover（post_analysis 同款口径）：空 = 单通道。生产只允许 inferera。
    base_url_fallback: str = ""


def audit_llm_config_from_settings(settings: Settings) -> AuditLlmConfig:
    """GEO_AUDIT_LLM_* 优先，空则复用 GEO_RESEARCH_LLM_*；key 绝不入库/日志。"""
    return AuditLlmConfig(
        api_key=(settings.audit_llm_api_key or settings.research_llm_api_key).strip(),
        model=(settings.audit_llm_model or settings.research_llm_model).strip(),
        base_url=(settings.audit_llm_base_url or settings.research_llm_base_url).strip(),
        base_url_fallback=(
            settings.audit_llm_base_url_fallback or settings.research_llm_base_url_fallback
        ).strip(),
    )


def _normalize_base_url(base_url: str) -> str:
    """历史 env 值无 /v1（OpenAI SDK 不自动补）——归一补齐。"""
    base = base_url.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base


# ---------------------------------------------------------------------------
# verbatim 程序校验（空白归一化后逐字子串比对）
# ---------------------------------------------------------------------------

_WS_RUN_RE = re.compile(r"\s+")


def normalize_verbatim(text: str) -> str:
    """空白归一化：所有空白串（含换行/全角空格）压成单空格、首尾 strip。"""
    return _WS_RUN_RE.sub(" ", text.replace("　", " ")).strip()


def quote_is_verbatim(quote: str, blob: str) -> bool:
    """quote 归一化后必须是 blob 归一化后的逐字子串（空 quote 不算命中）。"""
    needle = normalize_verbatim(quote)
    if not needle:
        return False
    return needle in normalize_verbatim(blob)


def validate_judgment(judgment: JudgeOutcome, *, source_text: str, answer_blob: str) -> str | None:
    """判分程序校验 → None=通过；否则返回失败原因（判分必须丢弃）。

    - accurate/inaccurate：quote_source/quote_answer 都必须非空且逐字命中；
    - unsupported：允许空 quote（正文无依据本就是判词含义），非空 quote 仍须逐字命中。
    """
    if judgment.verdict not in _VERDICTS:
        return f"verdict 非法: {judgment.verdict!r}"
    if judgment.verdict in {"accurate", "inaccurate"}:
        if not normalize_verbatim(judgment.quote_source):
            return "quote_source 为空（accurate/inaccurate 必须给正文证据）"
        if not normalize_verbatim(judgment.quote_answer):
            return "quote_answer 为空（accurate/inaccurate 必须给引述/事实证据）"
    if judgment.quote_source.strip() and not quote_is_verbatim(judgment.quote_source, source_text):
        return "quote_source 非正文逐字子串"
    if judgment.quote_answer.strip() and not quote_is_verbatim(judgment.quote_answer, answer_blob):
        return "quote_answer 非引述/事实逐字子串"
    return None


# ---------------------------------------------------------------------------
# 已确认事实收集（intake_profile.truth_confirmed 为真时的 selling_points + licenses）
# ---------------------------------------------------------------------------


def collect_confirmed_facts(profile: dict[str, Any] | None) -> list[str]:
    """→ 已确认事实文本列表；profile 缺失/未确认/无内容 → 空列表（口径B 跳过）。"""
    if not profile:
        return []
    if profile.get("truth_confirmed") is not True:
        return []
    facts: list[str] = []
    selling_points = profile.get("selling_points")
    if isinstance(selling_points, str) and selling_points.strip():
        facts.append(f"核心卖点：{selling_points.strip()}")
    licenses = profile.get("licenses")
    if isinstance(licenses, list):
        for item in licenses:
            if not isinstance(item, dict):
                continue
            parts = [
                f"{key}={str(value).strip()}" for key, value in item.items() if str(value).strip()
            ]
            if parts:
                facts.append("资质：" + "，".join(parts))
    return facts


def citations_answer_blob(citations_by_url: dict[str, list[str]], url: str) -> str:
    """该 URL 的全部豆包引述（cited_text）聚合为判定对照文本。"""
    return "\n---\n".join(citations_by_url.get(url, []))


# ---------------------------------------------------------------------------
# 官网语料（口径B 事实基底第二级来源）
# ---------------------------------------------------------------------------

# own_site 判定助手：复制自 api/geo_platform/analytics/service.py（写边界隔离，
# 允许复制；site_suggestions.py 同款复制件）。改判定规则必须三处同步。


def _host_from_website(value: object) -> str | None:
    """官网 URL/website → host（小写、去 scheme/路径/端口）；缺 scheme 裸串按 https 解析。"""
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    candidate = value.strip() if isinstance(value, str) else value
    if not candidate:
        return None
    candidate = candidate if "://" in candidate else f"https://{candidate}"
    try:
        hostname = urlsplit(candidate).hostname
    except ValueError:
        return None
    return hostname or None


def _is_own_site_host(host: object, own_site_host: str | None) -> bool:
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


def _resolve_official_site_host(*candidates: object) -> str | None:
    """确认官网 host：按优先级取第一个可解析的 website（acv → intake → brand）。"""
    for candidate in candidates:
        host = _host_from_website(candidate)
        if host:
            return host
    return None


@dataclass(frozen=True)
class OfficialSiteAsset:
    """本 run 官网快照正文资产引用（loader 已按确认官网 host 过滤）。"""

    pub_id: str
    source_url: str
    object_key: str
    sha256: str


@dataclass(frozen=True)
class OfficialSitePage:
    """官网页正文（CAS JSON payload 解出）。"""

    url: str
    title: str
    text: str


_HOMEPAGE_PATHS = frozenset({"", "/", "/index.html", "/index.htm", "/index.php"})


def _is_homepage_url(url: str) -> bool:
    try:
        path = urlsplit(url).path.rstrip("/").lower() or "/"
    except ValueError:
        return False
    return path in _HOMEPAGE_PATHS or f"{path}/" in _HOMEPAGE_PATHS


def build_official_site_corpus(pages: list[OfficialSitePage]) -> str:
    """官网语料装配：主页排前（其余保序），每页截 3000、总截 24000、上限 10 页。

    页节带 URL 供判词引证（"依据来自官网哪页"）；空正文页跳过。
    """
    ordered = sorted(pages, key=lambda page: 0 if _is_homepage_url(page.url) else 1)
    sections: list[str] = []
    total = 0
    for page in ordered:
        if len(sections) >= _MAX_SITE_PAGES:
            break
        text = page.text[:_MAX_SITE_PAGE_CHARS].strip()
        if not text:
            continue
        section = f"【官网页 {page.url}】\n{text}"
        separator = 2 if sections else 0  # "\n\n" 拼接符计入总量
        if total + separator + len(section) > _MAX_SITE_CORPUS_CHARS:
            remaining = _MAX_SITE_CORPUS_CHARS - total - separator
            if remaining < 200:  # 剩余空间装不出有意义的一节，直接截停
                break
            section = section[:remaining]
        sections.append(section)
        total += separator + len(section)
    return "\n\n".join(sections)


def build_fact_base_blob(confirmed_facts: list[str], site_corpus: str) -> str:
    """口径B 事实基底：已确认事实节 + 官网语料节（可叠加）；两级皆空 → 空串。"""
    sections: list[str] = []
    facts = "\n".join(confirmed_facts).strip()
    if facts:
        sections.append(f"【客户已确认事实】\n{facts[:_MAX_FACTS_CHARS]}")
    corpus = site_corpus.strip()
    if corpus:
        sections.append(f"【客户官网公开信息】\n{corpus[:_MAX_SITE_CORPUS_CHARS]}")
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# 判定 prompt（source-audit-v1）
# ---------------------------------------------------------------------------

_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(_VERDICTS)},
        "quote_source": {"type": "string"},
        "quote_answer": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["verdict", "quote_source", "quote_answer", "rationale"],
    "additionalProperties": False,
}

_TRANSCRIPT_INSTRUCTIONS = (
    "你是 GEO 信源质量审计员。给你【AI 助手引述】（AI 搜索答案中对该信源的引用摘要）"
    "与【信源正文】。判定引述是否准确转述了正文：\n"
    "- accurate：引述与正文一致，无歪曲\n"
    "- inaccurate：引述与正文矛盾、歪曲或张冠李戴\n"
    "- unsupported：引述内容在正文中找不到依据\n"
    "quote_source=正文中支撑你判定的**逐字原文**片段；"
    "quote_answer=引述中被你判定的**逐字原文**片段（两者程序会校验是否逐字，不得改写）；"
    "verdict 为 unsupported 且正文确实无相关内容时两个 quote 可留空字符串。"
    "rationale 用中文、不超过 200 字，只讲判定依据。"
)

_FACTUAL_INSTRUCTIONS = (
    "你是 GEO 信源质量审计员。给你【事实基底】（由「客户已确认事实」=客户书面确认过的"
    "核心卖点与资质，和「客户官网公开信息」=客户官网各页面正文组成，均为客户侧权威信息）"
    "与【信源正文】。判定正文中涉及该客户的陈述与事实基底是否一致：\n"
    "- accurate：正文相关陈述与事实基底一致\n"
    "- inaccurate：正文相关陈述与事实基底矛盾（如卖点、资质、数据、定位不符）\n"
    "- unsupported：事实基底未涉及正文的相关陈述，无法比对\n"
    "quote_source=正文中支撑你判定的**逐字原文**片段；"
    "quote_answer=事实基底中被你判定的**逐字原文**片段（两者程序会校验是否逐字，不得改写）；"
    "verdict 为 unsupported 时两个 quote 可留空字符串。"
    "rationale 用中文、不超过 200 字，只讲判定依据；依据来自官网时必须注明具体官网页 URL。"
)


def build_judge_user_prompt(*, dimension: str, url: str, source_text: str, answer_blob: str) -> str:
    source_excerpt = source_text[:_MAX_PROMPT_TEXT_CHARS]
    if dimension == _DIMENSION_TRANSCRIPT:
        answer_label = "AI 助手引述"
        answer_limit = _MAX_FACTS_CHARS
    else:
        answer_label = "事实基底（客户已确认事实＋客户官网公开信息）"
        answer_limit = _MAX_FACT_BASE_CHARS
    return (
        f"信源 URL：{url}\n\n"
        f"【{answer_label}】\n{answer_blob[:answer_limit]}\n\n"
        f"【信源正文】\n{source_excerpt}\n\n"
        "请按 JSON schema 输出判定。"
    )


# ---------------------------------------------------------------------------
# 可替换薄层：LLM 判定 / DB 读 / CAS 读 / 落库（单测全部 fake 注入）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JudgeOutcome:
    verdict: str
    quote_source: str
    quote_answer: str
    rationale: str


class JudgeError(RuntimeError):
    """LLM 超时/5xx/传输错误/格式坏 → audit_status="llm_error"。"""


class AuditJudge(Protocol):
    """LLM 判定薄层（单测 fake 注入）。"""

    def judge(
        self, *, dimension: str, url: str, source_text: str, answer_blob: str
    ) -> JudgeOutcome: ...


@dataclass(frozen=True)
class AuditDocument:
    pub_id: str
    url: str
    host: str
    extract_status: str
    text_cas_key: str | None
    text_sha256: str | None


@dataclass(frozen=True)
class RunAuditContext:
    tenant_pub_id: str
    tenant_id: str
    project_id: str
    run_id: str
    run_pub_id: str
    project_pub_id: str
    created_at: datetime
    documents: list[AuditDocument]  # 本 run 全部 source_document（含非 ok，→ unverifiable）
    citations_by_url: dict[str, list[str]]  # url → 去重后的 cited_text 列表
    confirmed_facts: list[str]
    # (doc_pub, dimension, model, prompt_version)
    existing_keys: frozenset[tuple[str, str, str, str]]
    # 确认官网 host（最新 asset_confirmation_version.website → intake → brand 回退）
    official_site_host: str | None = None
    # 本 run 官网快照正文资产（loader 已按 official_site_host 过滤）
    official_site_assets: list[OfficialSiteAsset] = field(default_factory=list)


class AuditContextLoader(Protocol):
    def load(
        self,
        tenant_pub_id: str,
        run_pub_id: str,
        project_pub_id: str,
    ) -> RunAuditContext | None: ...


class SourceTextStore(Protocol):
    """CAS 读薄层：按 object_key + sha256 取回正文 bytes。"""

    def get_text(self, object_key: str, expected_sha256: str) -> str: ...


@dataclass(frozen=True)
class AuditRecord:
    source_document_pub_id: str
    url: str
    host: str
    dimension: str
    verdict: str | None
    quote_source: str | None
    quote_answer: str | None
    rationale: str | None
    audit_status: str
    model: str
    prompt_version: str


class AuditSink(Protocol):
    """落库薄层：source_audit 行 + outbox 事件（同事务，幂等）。"""

    def persist(self, *, context: RunAuditContext, record: AuditRecord) -> str:
        """→ 确定性派生的 source_audit pub_id。"""
        ...


def derive_audit_pub_id(
    tenant_pub_id: str,
    run_pub_id: str,
    source_document_pub_id: str,
    dimension: str,
    model: str,
    prompt_version: str,
) -> str:
    """source_audit pub_id 确定性派生：幂等键全成分参与。"""
    stable_key = "|".join(
        (tenant_pub_id, run_pub_id, source_document_pub_id, dimension, model, prompt_version)
    )
    return f"sra_{sha256(stable_key.encode()).hexdigest()[:26]}"


# ---------------------------------------------------------------------------
# 生产实现：Responses API judge / psycopg loader / CAS store / sink
# ---------------------------------------------------------------------------


class _ResponsesApiJudge:
    """OpenAI Responses API 非流式判定（text.format json_schema 严格输出，60s 超时）。

    ``client`` 可注入（测试 mock 接缝，与 intake/research.py 同模式）。
    """

    def __init__(self, config: AuditLlmConfig, *, client: httpx.Client | None = None) -> None:
        self._config = config
        self._client = client

    def judge(
        self, *, dimension: str, url: str, source_text: str, answer_blob: str
    ) -> JudgeOutcome:
        instructions = (
            _TRANSCRIPT_INSTRUCTIONS
            if dimension == _DIMENSION_TRANSCRIPT
            else _FACTUAL_INSTRUCTIONS
        )
        body: dict[str, Any] = {
            "model": self._config.model,
            "instructions": instructions,
            "input": build_judge_user_prompt(
                dimension=dimension, url=url, source_text=source_text, answer_blob=answer_blob
            ),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "source_audit",
                    "strict": True,
                    "schema": _JSON_SCHEMA,
                }
            },
        }
        payload = self._post(body)
        return _parse_judge_payload(payload)

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        if self._client is not None:
            return self._post_with(self._client, body)
        bases = [self._config.base_url]
        if self._config.base_url_fallback.strip():
            bases.append(self._config.base_url_fallback)
        error: JudgeError | None = None
        for base in bases:
            try:
                with httpx.Client(
                    base_url=_normalize_base_url(base),
                    headers={"Authorization": f"Bearer {self._config.api_key}"},
                    timeout=_LLM_TIMEOUT_S,
                    trust_env=False,
                ) as client:
                    return self._post_with(client, body)
            except JudgeError as exc:
                # 主通道失败（网络/5xx/4xx 都含）→ 换备通道再试一次；POST 幂等无害。
                error = exc
        assert error is not None
        raise error

    @staticmethod
    def _post_with(client: httpx.Client, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = client.post("/responses", json=body)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise JudgeError(f"LLM 上游调用失败: {type(exc).__name__}") from exc
        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            raise JudgeError("LLM 响应非 JSON") from exc
        return payload


def _parse_judge_payload(payload: dict[str, Any]) -> JudgeOutcome:
    """Responses API output → JudgeOutcome；格式坏一律 JudgeError（→ llm_error）。"""
    text_parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                text_parts.append(str(content.get("text") or ""))
    raw_text = "\n".join(text_parts).strip()
    if not raw_text:
        raise JudgeError("LLM 未返回任何文本内容")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise JudgeError("LLM 输出 JSON 解析失败") from exc
    if not isinstance(data, dict):
        raise JudgeError("LLM 输出非 JSON 对象")
    verdict = str(data.get("verdict") or "").strip()
    if verdict not in _VERDICTS:
        raise JudgeError(f"LLM 返回非法 verdict: {verdict!r}")
    return JudgeOutcome(
        verdict=verdict,
        quote_source=str(data.get("quote_source") or ""),
        quote_answer=str(data.get("quote_answer") or ""),
        rationale=str(data.get("rationale") or "")[:_MAX_RATIONALE_CHARS],
    )


def _postgres_dsn() -> str:
    """与 own_site_snapshot 同款 DSN 读法（worker 覆盖优先，psycopg scheme 归一）。"""
    settings = get_settings()
    return os.getenv("S02_POSTGRES_DSN") or (
        settings.worker_postgres_dsn or settings.postgres_dsn
    ).replace("postgresql+psycopg://", "postgresql://")


class _PostgresAuditLoader:
    """platform.* 表走 app.tenant_id（uuid）RLS：先按 pub_id 解析 tenant，再置双 selector。"""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def load(
        self,
        tenant_pub_id: str,
        run_pub_id: str,
        project_pub_id: str,
    ) -> RunAuditContext | None:
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
                SELECT citations_json FROM platform.collection_task
                WHERE run_id = %s ORDER BY created_at, pub_id
                """,
                (run_row["id"],),
            ).fetchall()
            document_rows = connection.execute(
                """
                SELECT pub_id, url, host, extract_status, text_cas_key, text_sha256
                FROM platform.source_document WHERE run_id = %s ORDER BY created_at, pub_id
                """,
                (run_row["id"],),
            ).fetchall()
            audit_rows = connection.execute(
                """
                SELECT d.pub_id AS document_pub_id, a.dimension, a.model, a.prompt_version
                FROM platform.source_audit a
                JOIN platform.source_document d ON d.id = a.source_document_id
                WHERE d.run_id = %s
                """,
                (run_row["id"],),
            ).fetchall()
            profile_row = connection.execute(
                """
                SELECT truth_confirmed, selling_points, licenses, website
                FROM platform.intake_profile WHERE project_id = %s
                """,
                (run_row["project_id"],),
            ).fetchone()
            # 确认官网 host：最新 asset_confirmation_version.website 为权威（与
            # analytics 读路径 own_site_host 同口径），回退 intake.website → brand.website
            # （与 W4 own_site_snapshot 种子同口径）。
            acv_row = connection.execute(
                """
                SELECT website FROM platform.asset_confirmation_version
                WHERE project_id = %s
                ORDER BY revision DESC, created_at DESC, pub_id DESC
                LIMIT 1
                """,
                (run_row["project_id"],),
            ).fetchone()
            official_site_host = _resolve_official_site_host(
                acv_row["website"] if acv_row is not None else None,
                profile_row["website"] if profile_row is not None else None,
                self._brand_website(connection, run_row["project_id"]),
            )
            official_site_assets = (
                self._load_official_site_assets(
                    connection,
                    tenant_pub_id=tenant_pub_id,
                    run_id=str(run_row["id"]),
                    run_pub_id=run_pub_id,
                    official_site_host=official_site_host,
                )
                if official_site_host
                else []
            )
        created_at = run_row["created_at"]
        if not isinstance(created_at, datetime):
            raise ApplicationError(
                "collection run created_at is invalid",
                type="run_context_invalid",
                non_retryable=True,
            )
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        citations_by_url: dict[str, list[str]] = {}
        for row in task_rows:
            raw = row["citations_json"] or "[]"
            try:
                citations = json.loads(raw)
            except (TypeError, ValueError):
                log.warning("audit_citations_unparseable")
                continue
            if not isinstance(citations, list):
                continue
            for item in citations:
                if not isinstance(item, dict):
                    continue
                url = item.get("url")
                cited_text = item.get("cited_text")
                if not isinstance(url, str) or not url.strip():
                    continue
                if not isinstance(cited_text, str) or not cited_text.strip():
                    continue
                bucket = citations_by_url.setdefault(url.strip(), [])
                if cited_text.strip() not in bucket:
                    bucket.append(cited_text.strip())
        documents = [
            AuditDocument(
                pub_id=str(row["pub_id"]),
                url=str(row["url"]),
                host=str(row["host"]),
                extract_status=str(row["extract_status"]),
                text_cas_key=(
                    str(row["text_cas_key"]) if row["text_cas_key"] is not None else None
                ),
                text_sha256=(str(row["text_sha256"]) if row["text_sha256"] is not None else None),
            )
            for row in document_rows
        ]
        existing_keys = frozenset(
            (
                str(row["document_pub_id"]),
                str(row["dimension"]),
                str(row["model"]),
                str(row["prompt_version"]),
            )
            for row in audit_rows
        )
        profile: dict[str, Any] | None = dict(profile_row) if profile_row is not None else None
        return RunAuditContext(
            tenant_pub_id=tenant_pub_id,
            tenant_id=str(tenant_row["id"]),
            project_id=str(run_row["project_id"]),
            run_id=str(run_row["id"]),
            run_pub_id=str(run_row["pub_id"]),
            project_pub_id=str(run_row["project_pub_id"]),
            created_at=created_at,
            documents=documents,
            citations_by_url=citations_by_url,
            confirmed_facts=collect_confirmed_facts(profile),
            existing_keys=existing_keys,
            official_site_host=official_site_host,
            official_site_assets=official_site_assets,
        )

    @staticmethod
    def _brand_website(connection: Any, project_id: Any) -> str | None:
        """brand.website 回退种子（与 W4 own_site_snapshot loader 同口径）。"""
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

    @staticmethod
    def _load_official_site_assets(
        connection: Any,
        *,
        tenant_pub_id: str,
        run_id: str,
        run_pub_id: str,
        official_site_host: str,
    ) -> list[OfficialSiteAsset]:
        """本 run 官网快照正文资产（W4 产物）：own_site_page（挂 run）∪ own_site_snapshot
        引用页（挂 task），再按确认官网 host 过滤——官网纠错前旧域名的快照绝不混入。"""
        rows = connection.execute(
            """
            SELECT e.pub_id, e.source_url, e.object_key, e.sha256
            FROM evidence.evidence_asset e
            WHERE e.tenant_pub_id = %s
              AND e.kind = 'own_site_snapshot'
              AND e.mime_type = 'application/json'
              AND e.pub_id IN (
                  SELECT rel.to_pub_id FROM evidence.evidence_relation rel
                  WHERE rel.tenant_pub_id = %s
                    AND rel.relation_type = 'own_site_page'
                    AND rel.from_pub_id = %s
                  UNION
                  SELECT rel.to_pub_id FROM evidence.evidence_relation rel
                  JOIN platform.collection_task ct ON ct.pub_id = rel.from_pub_id
                  WHERE rel.tenant_pub_id = %s
                    AND rel.relation_type = 'own_site_snapshot'
                    AND ct.run_id = %s
              )
            ORDER BY e.created_at, e.pub_id
            """,
            (tenant_pub_id, tenant_pub_id, run_pub_id, tenant_pub_id, run_id),
        ).fetchall()
        assets: list[OfficialSiteAsset] = []
        for row in rows:
            source_url = str(row["source_url"] or "")
            page_host = _host_from_website(source_url)
            if not _is_own_site_host(page_host, official_site_host):
                continue
            if not row["object_key"] or not row["sha256"]:
                continue
            assets.append(
                OfficialSiteAsset(
                    pub_id=str(row["pub_id"]),
                    source_url=source_url,
                    object_key=str(row["object_key"]),
                    sha256=str(row["sha256"]),
                )
            )
        return assets


class _MinioSourceTextStore:
    """生产 CAS 读：ContentAddressedObjectStore.get_verified（sha256 校验防漂）。"""

    def __init__(self, store: ContentAddressedObjectStore) -> None:
        self._store = store

    def get_text(self, object_key: str, expected_sha256: str) -> str:
        payload: bytes = self._store.get_verified(object_key, expected_sha256)
        return payload.decode("utf-8")


@contextmanager
def _platform_connection(dsn: str, context: RunAuditContext) -> Iterator[psycopg.Connection[Any]]:
    """platform schema 写连接：置 app.tenant_id + app.tenant_pub_id 双 selector。"""
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true), "
            "set_config('app.tenant_pub_id', %s, true)",
            (context.tenant_id, context.tenant_pub_id),
        )
        yield connection


class _PostgresAuditSink:
    """生产落库：source_audit 行 + outbox 事件单事务写入。

    pub_id / event_id 均确定性派生 + ON CONFLICT DO NOTHING：activity 重试/重跑
    不产生重复行、不产生重复事件（CH 侧还有 consumer_receipt 兜底）。
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def persist(self, *, context: RunAuditContext, record: AuditRecord) -> str:
        audit_pub_id = derive_audit_pub_id(
            context.tenant_pub_id,
            context.run_pub_id,
            record.source_document_pub_id,
            record.dimension,
            record.model,
            record.prompt_version,
        )
        event_key = f"{context.tenant_pub_id}|{audit_pub_id}"
        event_id = f"evt_{sha256(event_key.encode()).hexdigest()[:24]}"
        payload = {
            "project_pub_id": context.project_pub_id,
            "run_pub_id": context.run_pub_id,
            "source_document_pub_id": record.source_document_pub_id,
            "source_audit_pub_id": audit_pub_id,
            "url": record.url,
            "host": record.host,
            "dimension": record.dimension,
            "verdict": record.verdict or "",
            "audit_status": record.audit_status,
            "model": record.model,
            "prompt_version": record.prompt_version,
            "event_time": datetime.now(UTC).isoformat(),
        }
        with _platform_connection(self._dsn, context) as connection:
            connection.execute(
                """
                INSERT INTO platform.source_audit
                  (id,pub_id,tenant_id,project_id,source_document_id,dimension,verdict,
                   quote_source,quote_answer,rationale,audit_status,model,prompt_version,
                   created_at,updated_at)
                SELECT gen_random_uuid(),%s,%s,%s,d.id,%s,%s,%s,%s,%s,%s,%s,%s,now(),now()
                FROM platform.source_document d
                WHERE d.pub_id = %s
                ON CONFLICT (source_document_id,dimension,model,prompt_version) DO NOTHING
                """,
                (
                    audit_pub_id,
                    context.tenant_id,
                    context.project_id,
                    record.dimension,
                    record.verdict,
                    record.quote_source,
                    record.quote_answer,
                    record.rationale,
                    record.audit_status,
                    record.model,
                    record.prompt_version,
                    record.source_document_pub_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO integration.outbox_event
                  (event_id,tenant_pub_id,event_type,aggregate_pub_id,trace_id,payload,
                   occurred_at)
                VALUES (%s,%s,%s,%s,%s,%s,now())
                ON CONFLICT (event_id) DO NOTHING
                """,
                (
                    event_id,
                    context.tenant_pub_id,
                    _EVENT_TYPE,
                    audit_pub_id,
                    sha256(f"source-audit|{audit_pub_id}".encode()).hexdigest(),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            connection.commit()
        return audit_pub_id


# ---------------------------------------------------------------------------
# 同步核心（生产线程内跑；单测直接调用，依赖全注入）
# ---------------------------------------------------------------------------


def _noop_progress(stage: str, url: str) -> None:
    del stage, url


def _load_official_site_corpus(context: RunAuditContext, text_store: SourceTextStore) -> str:
    """读本 run 官网快照正文（CAS JSON → text）装配官网语料；读失败的页记日志跳过。"""
    if not context.official_site_assets:
        return ""
    pages: list[OfficialSitePage] = []
    for asset in context.official_site_assets:
        try:
            payload = json.loads(text_store.get_text(asset.object_key, asset.sha256))
        except Exception as exc:
            log.warning(
                "official_site_corpus_read_failed",
                url=asset.source_url,
                error=f"{type(exc).__name__}: {exc}",
            )
            continue
        if not isinstance(payload, dict):
            continue
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        pages.append(
            OfficialSitePage(
                url=asset.source_url,
                title=str(payload.get("title") or ""),
                text=text,
            )
        )
    corpus = build_official_site_corpus(pages)
    log.info(
        "official_site_corpus_built",
        run_pub_id=context.run_pub_id,
        pages=len(pages),
        chars=len(corpus),
    )
    return corpus


def execute_source_audit(
    item: SourceAuditInput,
    *,
    enabled: bool,
    llm: AuditLlmConfig,
    judge: AuditJudge | None,
    loader: AuditContextLoader,
    text_store: SourceTextStore,
    sink: AuditSink,
    on_progress: Callable[[str, str], None] | None = None,
) -> SourceAuditResult:
    """读 DB → 逐文档两口径判定 → verbatim 校验 → 落库。INV-32：判不了就如实标记。"""
    if not enabled:
        return SourceAuditResult(disabled=True)
    progress = on_progress if on_progress is not None else _noop_progress
    progress("load_context", "")
    context = loader.load(item.tenant_pub_id, item.run_pub_id, item.project_pub_id)
    if context is None:
        raise ApplicationError("collection run not found", type="run_not_found", non_retryable=True)
    model = llm.model or "unknown"
    llm_available = bool(llm.api_key) and judge is not None

    result = SourceAuditResult()
    site_corpus: str | None = None  # 惰性装载：首个 ok 文档走到口径B 时才读 CAS

    def _site_corpus() -> str:
        nonlocal site_corpus
        if site_corpus is None:
            site_corpus = _load_official_site_corpus(context, text_store)
        return site_corpus

    def _persist(record: AuditRecord) -> None:
        progress("persist", record.url)
        sink.persist(context=context, record=record)
        result.audited.append(
            AuditedSource(
                url=record.url,
                dimension=record.dimension,
                verdict=record.verdict,
                audit_status=record.audit_status,
            )
        )

    def _skip_existing(doc: AuditDocument, dimension: str) -> bool:
        key = (doc.pub_id, dimension, model, prompt_version_for(dimension))
        if key in context.existing_keys:
            result.skipped.append(
                AuditSkipped(url=doc.url, dimension=dimension, reason="already_audited")
            )
            return True
        return False

    def _judge_and_record(doc: AuditDocument, dimension: str, source_text: str, blob: str) -> None:
        assert judge is not None  # llm_available 保证
        progress("judge", f"{dimension}:{doc.url}")
        try:
            judgment = judge.judge(
                dimension=dimension, url=doc.url, source_text=source_text, answer_blob=blob
            )
        except JudgeError as exc:
            _persist(
                AuditRecord(
                    source_document_pub_id=doc.pub_id,
                    url=doc.url,
                    host=doc.host,
                    dimension=dimension,
                    verdict=None,
                    quote_source=None,
                    quote_answer=None,
                    rationale=f"LLM 判定失败：{exc}"[:_MAX_RATIONALE_CHARS],
                    audit_status="llm_error",
                    model=model,
                    prompt_version=prompt_version_for(dimension),
                )
            )
            return
        failure = validate_judgment(judgment, source_text=source_text, answer_blob=blob)
        if failure is not None:
            # 判分丢弃：verdict 置 NULL 落 validation_failure 行如实记录（含问题 quote 供复查）
            _persist(
                AuditRecord(
                    source_document_pub_id=doc.pub_id,
                    url=doc.url,
                    host=doc.host,
                    dimension=dimension,
                    verdict=None,
                    quote_source=judgment.quote_source[:2_000],
                    quote_answer=judgment.quote_answer[:2_000],
                    rationale=(
                        f"逐字校验未过（{failure}），判分已丢弃。模型自述：{judgment.rationale}"
                    )[:_MAX_RATIONALE_CHARS],
                    audit_status="validation_failure",
                    model=model,
                    prompt_version=prompt_version_for(dimension),
                )
            )
            return
        _persist(
            AuditRecord(
                source_document_pub_id=doc.pub_id,
                url=doc.url,
                host=doc.host,
                dimension=dimension,
                verdict=judgment.verdict,
                quote_source=judgment.quote_source,
                quote_answer=judgment.quote_answer,
                rationale=judgment.rationale[:_MAX_RATIONALE_CHARS],
                audit_status="ok",
                model=model,
                prompt_version=prompt_version_for(dimension),
            )
        )

    for doc in context.documents:
        try:
            if doc.extract_status != "ok":
                # 抓不到正文 → 两口径 unverifiable，绝不编造判定
                for dimension in _DIMENSIONS:
                    if _skip_existing(doc, dimension):
                        continue
                    _persist(
                        AuditRecord(
                            source_document_pub_id=doc.pub_id,
                            url=doc.url,
                            host=doc.host,
                            dimension=dimension,
                            verdict="unverifiable",
                            quote_source=None,
                            quote_answer=None,
                            rationale=f"正文未抓取成功（extract_status={doc.extract_status}）",
                            audit_status="unverifiable",
                            model=model,
                            prompt_version=prompt_version_for(dimension),
                        )
                    )
                continue
            progress("read_text", doc.url)
            if not doc.text_cas_key or not doc.text_sha256:
                raise ApplicationError(
                    f"source_document {doc.pub_id} extract_status=ok 但缺 CAS 引用",
                    type="source_text_missing",
                    non_retryable=True,
                )
            try:
                source_text = text_store.get_text(doc.text_cas_key, doc.text_sha256)
            except Exception as exc:
                result.failures.append(
                    AuditFailure(url=doc.url, error=f"cas_read: {type(exc).__name__}: {exc}")
                )
                continue
            for dimension in _DIMENSIONS:
                if _skip_existing(doc, dimension):
                    continue
                if dimension == _DIMENSION_TRANSCRIPT:
                    blob = citations_answer_blob(context.citations_by_url, doc.url)
                    if not blob.strip():
                        _persist(
                            AuditRecord(
                                source_document_pub_id=doc.pub_id,
                                url=doc.url,
                                host=doc.host,
                                dimension=dimension,
                                verdict="unverifiable",
                                quote_source=None,
                                quote_answer=None,
                                rationale="该 URL 在答案引用中无 cited_text 可比对",
                                audit_status="unverifiable",
                                model=model,
                                prompt_version=prompt_version_for(dimension),
                            )
                        )
                        continue
                else:
                    blob = build_fact_base_blob(context.confirmed_facts, _site_corpus())
                    if not blob.strip():
                        _persist(
                            AuditRecord(
                                source_document_pub_id=doc.pub_id,
                                url=doc.url,
                                host=doc.host,
                                dimension=dimension,
                                verdict=None,
                                quote_source=None,
                                quote_answer=None,
                                rationale=(
                                    "intake 无已确认事实且官网语料为空"
                                    "（truth_confirmed 未确认/卖点资质为空；"
                                    "本 run 官网快照缺失或与确认官网 host 不符）"
                                ),
                                audit_status="no_confirmed_facts",
                                model=model,
                                prompt_version=prompt_version_for(dimension),
                            )
                        )
                        continue
                if not llm_available:
                    _persist(
                        AuditRecord(
                            source_document_pub_id=doc.pub_id,
                            url=doc.url,
                            host=doc.host,
                            dimension=dimension,
                            verdict=None,
                            quote_source=None,
                            quote_answer=None,
                            rationale="未配置 GEO_AUDIT_LLM_API_KEY（含 GEO_RESEARCH_LLM_* 复用）",
                            audit_status="llm_unavailable",
                            model=model,
                            prompt_version=prompt_version_for(dimension),
                        )
                    )
                    continue
                _judge_and_record(doc, dimension, source_text, blob)
        except ApplicationError:
            raise
        except Exception as exc:
            result.failures.append(AuditFailure(url=doc.url, error=f"{type(exc).__name__}: {exc}"))
    log.info(
        "source_audit_done",
        run_pub_id=context.run_pub_id,
        audited=len(result.audited),
        skipped=len(result.skipped),
        failures=len(result.failures),
    )
    return result


# ---------------------------------------------------------------------------
# activity 入口与异步泵
# ---------------------------------------------------------------------------


async def run_source_audit(
    item: SourceAuditInput,
    *,
    enabled: bool,
    llm: AuditLlmConfig,
    loader: AuditContextLoader,
    text_store: SourceTextStore,
    sink: AuditSink,
    judge: AuditJudge | None = None,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
) -> SourceAuditResult:
    """异步泵封装：默认实现跑 asyncio.to_thread + 10s heartbeat 泵（own_site_snapshot 同款）。

    注入 judge 时（单测）同步内联执行，不起线程。
    """
    uses_default_judge = judge is None
    effective_judge: AuditJudge | None = judge
    if effective_judge is None and llm.api_key:
        effective_judge = _ResponsesApiJudge(llm)
    if heartbeat is None:

        def heartbeat(payload: dict[str, Any]) -> None:
            del payload

    progress: dict[str, str] = {"stage": "start", "url": ""}

    def _on_progress(stage: str, url: str) -> None:
        progress["stage"] = stage
        progress["url"] = url

    def _blocking() -> SourceAuditResult:
        return execute_source_audit(
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


@activity.defn(name="audit_run_sources")
async def audit_run_sources(item: SourceAuditInput) -> SourceAuditResult:
    """W2 核对层 activity 入口：env 配置 + 真实 DB/CAS/LLM 接线。"""
    raw_enabled = os.environ.get(ENV_ENABLED, "").strip().lower()
    enabled = raw_enabled not in {"0", "false", "no", "off"}
    if not enabled:
        return SourceAuditResult(disabled=True)
    dsn = _postgres_dsn()
    settings = get_settings()
    llm = audit_llm_config_from_settings(settings)
    store = ContentAddressedObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
    )
    return await run_source_audit(
        item,
        enabled=enabled,
        llm=llm,
        loader=_PostgresAuditLoader(dsn),
        text_store=_MinioSourceTextStore(store),
        sink=_PostgresAuditSink(dsn),
        heartbeat=activity.heartbeat,
    )
