"""W3 拉踩检测 activity（``judge_run_disparagement``）：窗级 LLM 判定层。

需求规格：developlog/specs/geo-evaluation-improvement-20260805.md W3 节。
对本 run ① 各 task answer_text（目标品牌+竞品提及窗，±200 字符）② 已抓取成功
（extract_status="ok"）的 source_document 正文（同口径切窗）③ 竞品共现合并窗
（≥2 竞品名、间距 ≤600 字符）做窗级拉踩判定，落 platform.disparagement_judgment
+ outbox 事件 "disparagement.recorded" → CH geo_analytics.disparagement_fact。

纪律（INV-32 零合成）：

- evidence_quote 必须是窗文本逐字子串（空白归一化后程序校验）；不过 → 丢弃
  判分，落 judgment_status="validation_failure" 行如实留痕（attitude/
  disparagement 置 NULL，判分绝不入聚合分布）。
- LLM 未配 key / 调用失败 → 词典弱判定兜底（domain.scoring.disparagement
  .dictionary_judge），行标 method="dictionary_experimental"、prompt_version=
  "dictionary-v1"；LLM 判定标 method="llm" + model + prompt_version=
  "disparage-v2"（v2 起 evidence_quote 须完整：表格证据引用整行，碎片由
  expand_table_fragment_quote 确定性扩行）。LLM 调用失败同时如实记入结果 failures。
- 幂等：(subject_pub_id, window_hash, target_brand, model, prompt_version)
  唯一键，重跑跳过已落库判定（重判 = 升 prompt_version）；pub_id/event_id
  确定性派生 + ON CONFLICT DO NOTHING，activity 重试安全。
- LLM 调用：OpenAI Responses API 非流式 + text.format json_schema 严格结构化
  输出，每窗 60s 超时；key 只走 settings（GEO_AUDIT_LLM_*，缺省复用
  GEO_RESEARCH_LLM_*，与 W2 同口径），严禁入库/日志。
- env：``GEO_DISPARAGEMENT_ENABLED``（缺省 true，false → disabled 零 IO）；
  ``GEO_DISPARAGEMENT_WINDOW_LIMIT``（缺省 1000，硬夹 1..10000，超出如实记
  truncated，绝不暗吞；20260810 起缺省 50→1000——50 窗对正式 run 必然截断，
  配合幂等 resume 与 sidecar 120min 预算，上限只当防爆安全阀）。
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Protocol

import httpx
import psycopg
import structlog
from geo_platform.config import Settings, get_settings
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from psycopg.rows import dict_row
from temporalio import activity
from temporalio.exceptions import ApplicationError

from domain.scoring.disparagement import (
    ATTITUDES,
    DICTIONARY_VERSION,
    JUDGMENT_JSON_SCHEMA,
    METHOD_DICTIONARY,
    METHOD_LLM,
    PROMPT_VERSION,
    LlmJudgment,
    Window,
    clamp_window_limit,
    dedupe_windows,
    dictionary_judge,
    expand_table_fragment_quote,
    extract_windows,
    validate_judgment,
)
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

ENV_ENABLED = "GEO_DISPARAGEMENT_ENABLED"
ENV_WINDOW_LIMIT = "GEO_DISPARAGEMENT_WINDOW_LIMIT"

_LLM_TIMEOUT_S = 60.0  # 每窗 LLM 超时（规格 W3 任务书）
_HEARTBEAT_INTERVAL_S = 10.0

_EVENT_TYPE = "disparagement.recorded"

# ---------------------------------------------------------------------------
# activity 输入输出契约
# ---------------------------------------------------------------------------


@dataclass
class DisparagementInput:
    tenant_pub_id: str
    project_pub_id: str
    run_pub_id: str


@dataclass
class WindowFailure:
    subject_pub_id: str
    target_brand: str
    error: str


@dataclass
class DisparagementResult:
    windows: int = 0  # 去重后候选窗总数（cap 前）
    judged: int = 0  # 产出判定的窗数（LLM ok + 词典兜底）
    dictionary_fallback: int = 0  # 其中词典弱判定窗数
    validation_failures: int = 0  # verbatim/语义校验丢弃的判分窗数
    failures: list[WindowFailure] = field(default_factory=list)
    skipped: int = 0  # 幂等键命中跳过
    truncated: int = 0  # 超 GEO_DISPARAGEMENT_WINDOW_LIMIT 未处理窗数
    disabled: bool = False


# ---------------------------------------------------------------------------
# 判定 prompt（disparage-v2）
# ---------------------------------------------------------------------------

_INSTRUCTIONS = (
    "你是 GEO 竞品舆情判定员。给你一段从 AI 回答或网页正文截取的【提及窗】、窗的"
    "【评价目标品牌】与【候选品牌清单】。判定窗文本对目标品牌的态度：\n"
    "- support：明确支持/推荐/赞扬目标品牌\n"
    "- neutral：客观陈述、无立场或态度不明\n"
    "- negative：贬低、质疑、劝退目标品牌\n"
    "disparagement（拉踩）：文本是否构成对目标品牌的拉踩——通过贬低目标品牌来"
    "抬高其他品牌或衬托比较优势；仅当 attitude=negative 时才可能为 true。\n"
    "subject：观点的表态主体——若窗文本把该观点归于候选清单中的另一品牌（如其"
    "官方宣传、其发布的对比材料），填该品牌名；否则填空字符串（文本/平台本身的"
    "叙述）。\n"
    "target：必须原样回显【评价目标品牌】。\n"
    "evidence_quote：窗文本中支撑你判定的**逐字原文**片段（程序会做逐字子串校验，"
    "不得改写、不得翻译、不得补字）。必须引用完整：完整的句子或从句；证据在 "
    "Markdown 表格中时，必须引用该表格的完整一行（含全部单元格），不得只摘"
    "单元格碎片。\n"
    "confidence：0 到 1 的判定置信度。"
)


def build_judge_user_prompt(
    *, window_text: str, target_brand: str, known_brands: tuple[str, ...]
) -> str:
    brands = "、".join(known_brands) if known_brands else target_brand
    return (
        f"【评价目标品牌】{target_brand}\n"
        f"【候选品牌清单】{brands}\n"
        f"【提及窗】\n{window_text}\n\n"
        "请按 JSON schema 输出判定。"
    )


# ---------------------------------------------------------------------------
# 可替换薄层：LLM 判定 / DB 读 / CAS 读 / 落库（单测全部 fake 注入）
# ---------------------------------------------------------------------------


class JudgeError(RuntimeError):
    """LLM 超时/5xx/传输错误/格式坏 → 该窗词典兜底 + failures 如实留痕。"""


class DisparagementJudge(Protocol):
    """LLM 判定薄层（单测 fake 注入）。"""

    def judge(
        self, *, window_text: str, target_brand: str, known_brands: tuple[str, ...]
    ) -> LlmJudgment: ...


@dataclass(frozen=True)
class AnswerSubject:
    pub_id: str
    text: str
    model: str  # matrix_json.model；缺省 ""


@dataclass(frozen=True)
class DocumentSubject:
    pub_id: str
    url: str
    host: str
    text_cas_key: str
    text_sha256: str


@dataclass(frozen=True)
class RunDisparagementContext:
    tenant_pub_id: str
    tenant_id: str
    project_id: str
    run_id: str
    run_pub_id: str
    project_pub_id: str
    brand: str | None
    competitors: tuple[str, ...]
    answers: list[AnswerSubject]  # 本 run answer_text 非空的 task
    documents: list[DocumentSubject]  # 本 run extract_status="ok" 的 source_document
    # (subject_pub_id, window_hash, target_brand, model, prompt_version)
    existing_keys: frozenset[tuple[str, str, str, str, str]]

    @property
    def known_brands(self) -> tuple[str, ...]:
        names: list[str] = []
        for name in ([self.brand] if self.brand else []) + list(self.competitors):
            cleaned = (name or "").strip()
            if cleaned and cleaned not in names:
                names.append(cleaned)
        return tuple(names)


class DisparagementContextLoader(Protocol):
    def load(
        self,
        tenant_pub_id: str,
        run_pub_id: str,
        project_pub_id: str,
    ) -> RunDisparagementContext | None: ...


@dataclass(frozen=True)
class DisparagementRecord:
    subject_type: str
    subject_pub_id: str
    platform: str
    source_url: str
    window_hash: str
    subject_brand: str
    target_brand: str
    attitude: str | None  # validation_failure 行置 NULL（判分已丢弃）
    disparagement: bool | None
    evidence_quote: str | None
    confidence: float | None
    method: str  # llm | dictionary_experimental
    model: str
    prompt_version: str
    judgment_status: str  # ok | validation_failure


class DisparagementSink(Protocol):
    """落库薄层：disparagement_judgment 行 + outbox 事件（同事务，幂等）。"""

    def persist(self, *, context: RunDisparagementContext, record: DisparagementRecord) -> str:
        """→ 确定性派生的 disparagement_judgment pub_id。"""
        ...


def derive_judgment_pub_id(
    tenant_pub_id: str,
    run_pub_id: str,
    subject_pub_id: str,
    window_hash: str,
    target_brand: str,
    model: str,
    prompt_version: str,
) -> str:
    """disparagement_judgment pub_id 确定性派生：幂等键全成分参与。"""
    stable_key = "|".join(
        (
            tenant_pub_id,
            run_pub_id,
            subject_pub_id,
            window_hash,
            target_brand,
            model,
            prompt_version,
        )
    )
    return f"dpj_{sha256(stable_key.encode()).hexdigest()[:26]}"


# ---------------------------------------------------------------------------
# 生产实现：Responses API judge / psycopg loader / sink
# ---------------------------------------------------------------------------


class _ResponsesApiJudge:
    """OpenAI Responses API 非流式判定（text.format json_schema 严格输出，60s 超时）。"""

    def __init__(self, config: AuditLlmConfig, *, client: httpx.Client | None = None) -> None:
        self._config = config
        self._client = client

    def judge(
        self, *, window_text: str, target_brand: str, known_brands: tuple[str, ...]
    ) -> LlmJudgment:
        body: dict[str, Any] = {
            "model": self._config.model,
            "instructions": _INSTRUCTIONS,
            "input": build_judge_user_prompt(
                window_text=window_text, target_brand=target_brand, known_brands=known_brands
            ),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "disparagement_judgment",
                    "strict": True,
                    "schema": JUDGMENT_JSON_SCHEMA,
                }
            },
        }
        payload = self._post(body)
        return parse_judge_payload(payload)

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
                ) as client:
                    return self._post_with(client, body)
            except JudgeError as exc:
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
            raise JudgeError(f"LLM 上游调用失败: {type(exc).__name__}") from exc
        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            raise JudgeError("LLM 响应非 JSON") from exc
        return payload


def parse_judge_payload(payload: dict[str, Any]) -> LlmJudgment:
    """Responses API output → LlmJudgment；格式坏一律 JudgeError（→ 词典兜底）。"""
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
    disparagement = data.get("disparagement")
    confidence = data.get("confidence")
    if not isinstance(disparagement, bool) or not isinstance(confidence, int | float):
        raise JudgeError("LLM 输出 disparagement/confidence 类型非法")
    return LlmJudgment(
        subject=str(data.get("subject") or "").strip(),
        target=str(data.get("target") or "").strip(),
        attitude=str(data.get("attitude") or "").strip(),
        disparagement=disparagement,
        evidence_quote=str(data.get("evidence_quote") or ""),
        confidence=float(confidence),
    )


def _postgres_dsn() -> str:
    """与 source_audit 同款 DSN 读法（worker 覆盖优先，psycopg scheme 归一）。"""
    settings = get_settings()
    return os.getenv("S02_POSTGRES_DSN") or (
        settings.worker_postgres_dsn or settings.postgres_dsn
    ).replace("postgresql+psycopg://", "postgresql://")


class _PostgresDisparagementLoader:
    """platform.* 表走 app.tenant_id（uuid）RLS：先按 pub_id 解析 tenant，再置双 selector。"""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def load(
        self,
        tenant_pub_id: str,
        run_pub_id: str,
        project_pub_id: str,
    ) -> RunDisparagementContext | None:
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
            task_rows = connection.execute(
                """
                SELECT pub_id, answer_text, matrix_json FROM platform.collection_task
                WHERE run_id = %s AND answer_text IS NOT NULL
                ORDER BY created_at, pub_id
                """,
                (run_row["id"],),
            ).fetchall()
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
            brand_row = connection.execute(
                """
                SELECT name FROM platform.brand
                WHERE project_id = %s ORDER BY created_at, pub_id LIMIT 1
                """,
                (run_row["project_id"],),
            ).fetchone()
            competitor_rows = connection.execute(
                """
                SELECT name FROM platform.competitor
                WHERE project_id = %s ORDER BY created_at, pub_id
                """,
                (run_row["project_id"],),
            ).fetchall()
            judgment_rows = connection.execute(
                """
                SELECT subject_pub_id, window_hash, target_brand, model, prompt_version
                FROM platform.disparagement_judgment WHERE run_id = %s
                """,
                (run_row["id"],),
            ).fetchall()
        answers: list[AnswerSubject] = []
        for row in task_rows:
            model = ""
            try:
                matrix = json.loads(row["matrix_json"] or "{}")
            except (TypeError, ValueError):
                matrix = {}
            if isinstance(matrix, dict):
                raw_model = matrix.get("model")
                model = str(raw_model).strip() if raw_model is not None else ""
            answers.append(
                AnswerSubject(
                    pub_id=str(row["pub_id"]),
                    text=str(row["answer_text"]),
                    model=model,
                )
            )
        documents = [
            DocumentSubject(
                pub_id=str(row["pub_id"]),
                url=str(row["url"]),
                host=str(row["host"]),
                text_cas_key=str(row["text_cas_key"]),
                text_sha256=str(row["text_sha256"]),
            )
            for row in document_rows
        ]
        existing_keys = frozenset(
            (
                str(row["subject_pub_id"]),
                str(row["window_hash"]),
                str(row["target_brand"]),
                str(row["model"]),
                str(row["prompt_version"]),
            )
            for row in judgment_rows
        )
        return RunDisparagementContext(
            tenant_pub_id=tenant_pub_id,
            tenant_id=str(tenant_row["id"]),
            project_id=str(run_row["project_id"]),
            run_id=str(run_row["id"]),
            run_pub_id=str(run_row["pub_id"]),
            project_pub_id=str(run_row["project_pub_id"]),
            brand=(str(brand_row["name"]).strip() if brand_row is not None else None),
            competitors=tuple(str(row["name"]).strip() for row in competitor_rows),
            answers=answers,
            documents=documents,
            existing_keys=existing_keys,
        )


@contextmanager
def _platform_connection(
    dsn: str, context: RunDisparagementContext
) -> Iterator[psycopg.Connection[Any]]:
    """platform schema 写连接：置 app.tenant_id + app.tenant_pub_id 双 selector。"""
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true), "
            "set_config('app.tenant_pub_id', %s, true)",
            (context.tenant_id, context.tenant_pub_id),
        )
        yield connection


class _PostgresDisparagementSink:
    """生产落库：disparagement_judgment 行 + outbox 事件单事务写入。

    pub_id / event_id 均确定性派生 + ON CONFLICT DO NOTHING：activity 重试/重跑
    不产生重复行、不产生重复事件（CH 侧还有 consumer_receipt 兜底）。
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def persist(self, *, context: RunDisparagementContext, record: DisparagementRecord) -> str:
        judgment_pub_id = derive_judgment_pub_id(
            context.tenant_pub_id,
            context.run_pub_id,
            record.subject_pub_id,
            record.window_hash,
            record.target_brand,
            record.model,
            record.prompt_version,
        )
        event_key = f"{context.tenant_pub_id}|{judgment_pub_id}"
        event_id = f"evt_{sha256(event_key.encode()).hexdigest()[:24]}"
        payload = {
            "project_pub_id": context.project_pub_id,
            "run_pub_id": context.run_pub_id,
            "judgment_pub_id": judgment_pub_id,
            "subject_type": record.subject_type,
            "subject_pub_id": record.subject_pub_id,
            "platform": record.platform,
            "source_url": record.source_url,
            "subject_brand": record.subject_brand,
            "target_brand": record.target_brand,
            "attitude": record.attitude or "",
            "disparagement": bool(record.disparagement),
            "confidence": record.confidence if record.confidence is not None else 0.0,
            "method": record.method,
            "model": record.model,
            "prompt_version": record.prompt_version,
            "judgment_status": record.judgment_status,
            "event_time": datetime.now(UTC).isoformat(),
        }
        with _platform_connection(self._dsn, context) as connection:
            connection.execute(
                """
                INSERT INTO platform.disparagement_judgment
                  (id,pub_id,tenant_id,project_id,run_id,subject_type,subject_pub_id,
                   window_hash,platform,source_url,subject_brand,target_brand,attitude,
                   disparagement,evidence_quote,confidence,method,model,prompt_version,
                   judgment_status,created_at,updated_at)
                VALUES (gen_random_uuid(),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,now(),now())
                ON CONFLICT (tenant_id,subject_pub_id,window_hash,target_brand,model,
                             prompt_version) DO NOTHING
                """,
                (
                    judgment_pub_id,
                    context.tenant_id,
                    context.project_id,
                    context.run_id,
                    record.subject_type,
                    record.subject_pub_id,
                    record.window_hash,
                    record.platform,
                    record.source_url,
                    record.subject_brand,
                    record.target_brand,
                    record.attitude,
                    record.disparagement,
                    record.evidence_quote,
                    record.confidence,
                    record.method,
                    record.model,
                    record.prompt_version,
                    record.judgment_status,
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
                    judgment_pub_id,
                    sha256(f"disparagement|{judgment_pub_id}".encode()).hexdigest(),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            connection.commit()
        return judgment_pub_id


# ---------------------------------------------------------------------------
# 同步核心（生产线程内跑；单测直接调用，依赖全注入）
# ---------------------------------------------------------------------------


def _noop_progress(stage: str, label: str) -> None:
    del stage, label


def execute_disparagement(
    item: DisparagementInput,
    *,
    enabled: bool,
    window_limit: int,
    llm: AuditLlmConfig,
    judge: DisparagementJudge | None,
    loader: DisparagementContextLoader,
    text_store: SourceTextStore,
    sink: DisparagementSink,
    on_progress: Callable[[str, str], None] | None = None,
) -> DisparagementResult:
    """读 DB → 确定性切窗 → 窗级 LLM/词典判定 → verbatim 校验 → 落库。"""
    if not enabled:
        return DisparagementResult(disabled=True)
    progress = on_progress if on_progress is not None else _noop_progress
    progress("load_context", "")
    context = loader.load(item.tenant_pub_id, item.run_pub_id, item.project_pub_id)
    if context is None:
        raise ApplicationError("collection run not found", type="run_not_found", non_retryable=True)
    known_brands = context.known_brands
    result = DisparagementResult()

    # 阶段一：确定性切窗（answers 在前、documents 在后，顺序确定性）
    candidates: list[Window] = []
    for answer in context.answers:
        candidates.extend(
            extract_windows(
                subject_type="answer",
                subject_pub_id=answer.pub_id,
                text=answer.text,
                brand=context.brand,
                competitors=context.competitors,
                platform=answer.model,
            )
        )
    document_texts: dict[str, str] = {}
    for document in context.documents:
        progress("read_text", document.url)
        try:
            document_texts[document.pub_id] = text_store.get_text(
                document.text_cas_key, document.text_sha256
            )
        except Exception as exc:
            result.failures.append(
                WindowFailure(
                    subject_pub_id=document.pub_id,
                    target_brand="",
                    error=f"cas_read: {type(exc).__name__}: {exc}",
                )
            )
    for document in context.documents:
        text = document_texts.get(document.pub_id)
        if text is None:
            continue  # CAS 读失败已如实记 failures，绝不判定残缺正文
        candidates.extend(
            extract_windows(
                subject_type="source_document",
                subject_pub_id=document.pub_id,
                text=text,
                brand=context.brand,
                competitors=context.competitors,
                platform=document.host,
                source_url=document.url,
            )
        )
    candidates = dedupe_windows(candidates)
    result.windows = len(candidates)

    # 幂等跳过 + 窗数上限（cap 只夹待判定窗，已判定的不占位）
    model = llm.model or "unknown"
    llm_available = bool(llm.api_key) and judge is not None
    prompt_version = PROMPT_VERSION if llm_available else DICTIONARY_VERSION
    effective_model = model if llm_available else ""

    pending: list[Window] = []
    for window in candidates:
        key = (
            window.subject_pub_id,
            window.window_hash,
            window.target_brand,
            effective_model,
            prompt_version,
        )
        if key in context.existing_keys:
            result.skipped += 1
            continue
        pending.append(window)
    if len(pending) > window_limit:
        result.truncated = len(pending) - window_limit
        pending = pending[:window_limit]
        log.warning(
            "disparagement_windows_truncated",
            run_pub_id=context.run_pub_id,
            truncated=result.truncated,
            window_limit=window_limit,
        )

    def _persist(window: Window, record: DisparagementRecord) -> None:
        progress("persist", f"{window.target_brand}:{window.subject_pub_id}")
        sink.persist(context=context, record=record)

    def _dictionary_fallback(window: Window) -> None:
        fallback = dictionary_judge(
            window.text, target_brand=window.target_brand, known_brands=known_brands
        )
        _persist(
            window,
            DisparagementRecord(
                subject_type=window.subject_type,
                subject_pub_id=window.subject_pub_id,
                platform=window.platform,
                source_url=window.source_url,
                window_hash=window.window_hash,
                subject_brand="",
                target_brand=window.target_brand,
                attitude=fallback.attitude,
                disparagement=fallback.disparagement,
                evidence_quote=fallback.evidence_quote or None,
                confidence=fallback.confidence,
                method=METHOD_DICTIONARY,
                model="",
                prompt_version=DICTIONARY_VERSION,
                judgment_status="ok",
            ),
        )
        result.judged += 1
        result.dictionary_fallback += 1

    # 阶段二：窗级判定
    for window in pending:
        progress("judge", f"{window.target_brand}:{window.subject_pub_id}")
        if not llm_available:
            _dictionary_fallback(window)
            continue
        assert judge is not None  # llm_available 保证
        try:
            judgment = judge.judge(
                window_text=window.text,
                target_brand=window.target_brand,
                known_brands=known_brands,
            )
        except JudgeError as exc:
            # LLM 失败 → 词典兜底（规格 W3.2），失败如实记 failures
            result.failures.append(
                WindowFailure(
                    subject_pub_id=window.subject_pub_id,
                    target_brand=window.target_brand,
                    error=f"llm_error: {exc}",
                )
            )
            _dictionary_fallback(window)
            continue
        except Exception as exc:
            result.failures.append(
                WindowFailure(
                    subject_pub_id=window.subject_pub_id,
                    target_brand=window.target_brand,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        # disparage-v2：表格碎片 quote 先确定性扩到整行（扩后仍是窗内逐字子串），再校验
        judgment = replace(
            judgment,
            evidence_quote=expand_table_fragment_quote(judgment.evidence_quote, window.text),
        )
        failure = validate_judgment(
            judgment,
            window_text=window.text,
            expected_target=window.target_brand,
            known_brands=known_brands,
        )
        if failure is not None:
            # 判分丢弃：attitude/disparagement 置 NULL 落 validation_failure 行如实留痕
            _persist(
                window,
                DisparagementRecord(
                    subject_type=window.subject_type,
                    subject_pub_id=window.subject_pub_id,
                    platform=window.platform,
                    source_url=window.source_url,
                    window_hash=window.window_hash,
                    subject_brand="",
                    target_brand=window.target_brand,
                    attitude=None,
                    disparagement=None,
                    evidence_quote=judgment.evidence_quote[:2_000] or None,
                    confidence=None,
                    method=METHOD_LLM,
                    model=model,
                    prompt_version=PROMPT_VERSION,
                    judgment_status="validation_failure",
                ),
            )
            result.validation_failures += 1
            log.warning(
                "disparagement_validation_failure",
                run_pub_id=context.run_pub_id,
                subject_pub_id=window.subject_pub_id,
                target_brand=window.target_brand,
                reason=failure,
            )
            continue
        _persist(
            window,
            DisparagementRecord(
                subject_type=window.subject_type,
                subject_pub_id=window.subject_pub_id,
                platform=window.platform,
                source_url=window.source_url,
                window_hash=window.window_hash,
                subject_brand=judgment.subject,
                target_brand=judgment.target,
                attitude=judgment.attitude,
                disparagement=judgment.disparagement,
                evidence_quote=judgment.evidence_quote,
                confidence=judgment.confidence,
                method=METHOD_LLM,
                model=model,
                prompt_version=PROMPT_VERSION,
                judgment_status="ok",
            ),
        )
        result.judged += 1
    log.info(
        "disparagement_done",
        run_pub_id=context.run_pub_id,
        windows=result.windows,
        judged=result.judged,
        dictionary_fallback=result.dictionary_fallback,
        validation_failures=result.validation_failures,
        skipped=result.skipped,
        truncated=result.truncated,
        failures=len(result.failures),
    )
    return result


# ---------------------------------------------------------------------------
# activity 入口与异步泵
# ---------------------------------------------------------------------------


async def run_disparagement(
    item: DisparagementInput,
    *,
    enabled: bool,
    window_limit: int,
    llm: AuditLlmConfig,
    loader: DisparagementContextLoader,
    text_store: SourceTextStore,
    sink: DisparagementSink,
    judge: DisparagementJudge | None = None,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
) -> DisparagementResult:
    """异步泵封装：默认实现跑 asyncio.to_thread + 10s heartbeat 泵（W2 同款）。

    注入 judge 时（单测）同步内联执行，不起线程。
    """
    uses_default_judge = judge is None
    effective_judge: DisparagementJudge | None = judge
    if effective_judge is None and llm.api_key:
        effective_judge = _ResponsesApiJudge(llm)
    if heartbeat is None:

        def heartbeat(payload: dict[str, Any]) -> None:
            del payload

    progress: dict[str, str] = {"stage": "start", "label": ""}

    def _on_progress(stage: str, label: str) -> None:
        progress["stage"] = stage
        progress["label"] = label

    def _blocking() -> DisparagementResult:
        return execute_disparagement(
            item,
            enabled=enabled,
            window_limit=window_limit,
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


@activity.defn(name="judge_run_disparagement")
async def judge_run_disparagement(item: DisparagementInput) -> DisparagementResult:
    """W3 拉踩检测 activity 入口：env 配置 + 真实 DB/CAS/LLM 接线。"""
    raw_enabled = os.environ.get(ENV_ENABLED, "").strip().lower()
    enabled = raw_enabled not in {"0", "false", "no", "off"}
    if not enabled:
        return DisparagementResult(disabled=True)
    window_limit = clamp_window_limit(os.environ.get(ENV_WINDOW_LIMIT))
    dsn = _postgres_dsn()
    settings: Settings = get_settings()
    llm = audit_llm_config_from_settings(settings)
    store = ContentAddressedObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
    )
    return await run_disparagement(
        item,
        enabled=enabled,
        window_limit=window_limit,
        llm=llm,
        loader=_PostgresDisparagementLoader(dsn),
        text_store=_MinioSourceTextStore(store),
        sink=_PostgresDisparagementSink(dsn),
        heartbeat=activity.heartbeat,
    )


__all__ = [
    "ATTITUDES",
    "AnswerSubject",
    "DisparagementInput",
    "DisparagementRecord",
    "DisparagementResult",
    "DocumentSubject",
    "JudgeError",
    "LlmJudgment",
    "RunDisparagementContext",
    "WindowFailure",
    "build_judge_user_prompt",
    "derive_judgment_pub_id",
    "execute_disparagement",
    "judge_run_disparagement",
    "parse_judge_payload",
    "run_disparagement",
]
