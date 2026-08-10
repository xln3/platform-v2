"""己方内容拉踩检测 activity（``judge_own_content_disparagement``）：SOP 稿件通道。

对 SOP article version 定稿（publication_ready false→true，API 服务层触发
OwnContentDisparagementWorkflow，见 api/geo_platform/sop/service.py 钩子）的己方稿件
正文做拉踩判定：复用 domain/scoring/disparagement.py 的确定性切窗 + 窗级 LLM 判定
（subject=己方品牌，target=SOP 项目竞品清单），结果落 platform.disparagement_judgment
——与 W3 采集侧同表同构，content_origin='own_content' 区分来源（run_id/project_id
置 NULL：己方稿件不依附采集 run，sop.project 与 platform.project 无外键关联）。

纪律（与 W3 采集侧同口径）：

- evidence_quote 逐字子串程序校验，不过丢弃判分落 validation_failure 行如实留痕；
  LLM 未配 key / 调用失败 → 词典弱判定兜底（method="dictionary_experimental"）。
- 安静跳过：稿件正文为空 → skipped="empty_body"；SOP 项目未配置竞品
  （brand_profile.competitors 为空）→ skipped="no_competitors"；两者零 LLM 零落库。
- 竞品/品牌真源：sop.project.brand_standard_name + brand_profile JSONB 的
  aliases/competitors 键（SOP 世界无 platform 项目关联，绝不跨库模糊匹配）。
- 幂等：(tenant_id, subject_pub_id=article_version_pub_id, window_hash, target_brand,
  model, prompt_version) 唯一键与采集侧共用（pub_id 前缀不同天然不撞），确定性
  pub_id/event_id 派生 + ON CONFLICT DO NOTHING，重试/重定稿安全。
- outbox 事件 "disparagement.recorded" 与采集侧同型：run_pub_id/project_pub_id
  无对应物填空字符串（绝不编造），另带 content_origin 供下游区分。
- 窗数上限复用 ``GEO_DISPARAGEMENT_WINDOW_LIMIT``（缺省 50，硬夹 1..200）。
- env：``GEO_OWN_CONTENT_DISPARAGEMENT_ENABLED``（缺省 true，false → disabled 零 IO）。
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Protocol

import psycopg
import structlog
from geo_platform.config import Settings, get_settings
from psycopg.rows import dict_row
from temporalio import activity
from temporalio.exceptions import ApplicationError

from domain.scoring.disparagement import (
    DICTIONARY_VERSION,
    METHOD_DICTIONARY,
    METHOD_LLM,
    PROMPT_VERSION,
    Window,
    clamp_window_limit,
    dedupe_windows,
    dictionary_judge,
    extract_windows,
    validate_judgment,
)
from workflows.activities.disparagement import (
    DisparagementJudge,
    DisparagementRecord,
    JudgeError,
    WindowFailure,
    _postgres_dsn,
    _ResponsesApiJudge,
    derive_judgment_pub_id,
)
from workflows.activities.source_audit import (
    AuditLlmConfig,
    audit_llm_config_from_settings,
)

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# env / 常量
# ---------------------------------------------------------------------------

ENV_ENABLED = "GEO_OWN_CONTENT_DISPARAGEMENT_ENABLED"

_SUBJECT_TYPE = "own_content"
_PLATFORM_LABEL = "own_content"  # disparagement_judgment.platform 列取值（聚合自描述）
_EVENT_TYPE = "disparagement.recorded"
_HEARTBEAT_INTERVAL_S = 10.0

# ---------------------------------------------------------------------------
# activity 输入输出契约
# ---------------------------------------------------------------------------


@dataclass
class OwnContentDisparagementInput:
    tenant_pub_id: str
    article_version_pub_id: str


@dataclass
class OwnContentDisparagementResult:
    windows: int = 0
    judged: int = 0
    dictionary_fallback: int = 0
    validation_failures: int = 0
    skipped_idempotent: int = 0  # 幂等键命中跳过
    truncated: int = 0
    skipped: str = ""  # empty_body / no_competitors
    disabled: bool = False
    failures: list[WindowFailure] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 可替换薄层：DB 读 / 落库（LLM 判定复用 disparagement.DisparagementJudge）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OwnContentContext:
    tenant_pub_id: str
    tenant_id: str
    article_version_pub_id: str
    sop_project_pub_id: str
    title: str
    body: str
    brand: str | None  # sop.project.brand_standard_name
    aliases: tuple[str, ...]  # brand_profile.aliases
    competitors: tuple[str, ...]  # brand_profile.competitors
    # (window_hash, target_brand, model, prompt_version)——subject_pub_id 恒为本版本
    existing_keys: frozenset[tuple[str, str, str, str]]

    @property
    def known_brands(self) -> tuple[str, ...]:
        """判定/校验用品牌全集：己方品牌 + 别名 + 竞品（去重保序）。"""
        names: list[str] = []
        for name in (
            ([self.brand] if self.brand else []) + list(self.aliases) + list(self.competitors)
        ):
            cleaned = (name or "").strip()
            if cleaned and cleaned not in names:
                names.append(cleaned)
        return tuple(names)


class OwnContentLoader(Protocol):
    def load(self, tenant_pub_id: str, article_version_pub_id: str) -> OwnContentContext | None: ...


class OwnContentSink(Protocol):
    """落库薄层：disparagement_judgment 行（own_content）+ outbox 事件（同事务，幂等）。"""

    def persist(self, *, context: OwnContentContext, record: DisparagementRecord) -> str: ...


def parse_brand_profile(raw: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """brand_profile JSONB → (aliases, competitors)；非列表/非串成员一律剔除。"""
    profile = raw if isinstance(raw, dict) else {}

    def _names(key: str) -> tuple[str, ...]:
        value = profile.get(key)
        if not isinstance(value, list):
            return ()
        names: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            cleaned = item.strip()
            if cleaned and cleaned not in names:
                names.append(cleaned)
        return tuple(names)

    return _names("aliases"), _names("competitors")


# ---------------------------------------------------------------------------
# 生产实现：psycopg loader / sink
# ---------------------------------------------------------------------------


class _PostgresOwnContentLoader:
    """sop.* 走 app.tenant_pub_id（text）RLS；platform.* 写面另需 app.tenant_id（uuid）。"""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def load(self, tenant_pub_id: str, article_version_pub_id: str) -> OwnContentContext | None:
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
            row = connection.execute(
                """
                SELECT av.pub_id, av.title, av.body,
                       sp.pub_id AS sop_project_pub_id,
                       sp.brand_standard_name, sp.brand_profile
                FROM sop.article_version av
                JOIN sop.article a
                  ON a.tenant_pub_id = av.tenant_pub_id AND a.pub_id = av.article_pub_id
                JOIN sop.project sp
                  ON sp.tenant_pub_id = av.tenant_pub_id AND sp.pub_id = a.project_pub_id
                WHERE av.tenant_pub_id = %s AND av.pub_id = %s
                """,
                (tenant_pub_id, article_version_pub_id),
            ).fetchone()
            if row is None:
                return None
            judgment_rows = connection.execute(
                """
                SELECT window_hash, target_brand, model, prompt_version
                FROM platform.disparagement_judgment
                WHERE tenant_id = %s AND subject_pub_id = %s
                """,
                (str(tenant_row["id"]), article_version_pub_id),
            ).fetchall()
        raw_profile = row["brand_profile"]
        if isinstance(raw_profile, str):
            try:
                raw_profile = json.loads(raw_profile)
            except ValueError:
                raw_profile = {}
        aliases, competitors = parse_brand_profile(raw_profile)
        return OwnContentContext(
            tenant_pub_id=tenant_pub_id,
            tenant_id=str(tenant_row["id"]),
            article_version_pub_id=article_version_pub_id,
            sop_project_pub_id=str(row["sop_project_pub_id"]),
            title=str(row["title"] or ""),
            body=str(row["body"] or ""),
            brand=(
                str(row["brand_standard_name"]).strip()
                if row["brand_standard_name"] is not None
                else None
            ),
            aliases=aliases,
            competitors=competitors,
            existing_keys=frozenset(
                (
                    str(item["window_hash"]),
                    str(item["target_brand"]),
                    str(item["model"]),
                    str(item["prompt_version"]),
                )
                for item in judgment_rows
            ),
        )


class _PostgresOwnContentSink:
    """生产落库：content_origin='own_content'、run_id/project_id 置 NULL。

    pub_id / event_id 确定性派生 + ON CONFLICT DO NOTHING：activity 重试/重定稿
    不产生重复行、不产生重复事件（CH 侧还有 consumer_receipt 兜底）。
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def persist(self, *, context: OwnContentContext, record: DisparagementRecord) -> str:
        judgment_pub_id = derive_judgment_pub_id(
            context.tenant_pub_id,
            _PLATFORM_LABEL,  # run_pub_id 槽位：己方内容无 run，固定占位（非 run_ 前缀）
            record.subject_pub_id,
            record.window_hash,
            record.target_brand,
            record.model,
            record.prompt_version,
        )
        event_key = f"{context.tenant_pub_id}|{judgment_pub_id}"
        event_id = f"evt_{sha256(event_key.encode()).hexdigest()[:24]}"
        payload = {
            "project_pub_id": "",  # 己方内容无 platform 项目，绝不编造
            "run_pub_id": "",
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
            "content_origin": _SUBJECT_TYPE,
            "event_time": datetime.now(UTC).isoformat(),
        }
        with psycopg.connect(self._dsn) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_id', %s, true), "
                "set_config('app.tenant_pub_id', %s, true)",
                (context.tenant_id, context.tenant_pub_id),
            )
            connection.execute(
                """
                INSERT INTO platform.disparagement_judgment
                  (id,pub_id,tenant_id,project_id,run_id,subject_type,subject_pub_id,
                   window_hash,platform,source_url,subject_brand,target_brand,attitude,
                   disparagement,evidence_quote,confidence,method,model,prompt_version,
                   judgment_status,content_origin,created_at,updated_at)
                VALUES (gen_random_uuid(),%s,%s,NULL,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,now(),now())
                ON CONFLICT (tenant_id,subject_pub_id,window_hash,target_brand,model,
                             prompt_version) DO NOTHING
                """,
                (
                    judgment_pub_id,
                    context.tenant_id,
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
                    _SUBJECT_TYPE,
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


def execute_own_content_disparagement(
    item: OwnContentDisparagementInput,
    *,
    enabled: bool,
    window_limit: int,
    llm: AuditLlmConfig,
    judge: DisparagementJudge | None,
    loader: OwnContentLoader,
    sink: OwnContentSink,
    on_progress: Callable[[str, str], None] | None = None,
) -> OwnContentDisparagementResult:
    """读 DB → 安静跳过门 → 确定性切窗 → 窗级 LLM/词典判定 → verbatim 校验 → 落库。"""
    if not enabled:
        return OwnContentDisparagementResult(disabled=True)
    progress = on_progress if on_progress is not None else _noop_progress
    progress("load_context", "")
    context = loader.load(item.tenant_pub_id, item.article_version_pub_id)
    if context is None:
        raise ApplicationError(
            "article version not found", type="version_not_found", non_retryable=True
        )
    result = OwnContentDisparagementResult()
    if not context.body.strip():
        result.skipped = "empty_body"
        return result
    if not context.competitors:
        result.skipped = "no_competitors"
        return result

    known_brands = context.known_brands
    candidates = dedupe_windows(
        extract_windows(
            subject_type=_SUBJECT_TYPE,
            subject_pub_id=context.article_version_pub_id,
            text=context.body,
            brand=context.brand,
            competitors=context.competitors,
            platform=_PLATFORM_LABEL,
        )
    )
    result.windows = len(candidates)

    model = llm.model or "unknown"
    llm_available = bool(llm.api_key) and judge is not None
    prompt_version = PROMPT_VERSION if llm_available else DICTIONARY_VERSION
    effective_model = model if llm_available else ""

    pending: list[Window] = []
    for window in candidates:
        key = (window.window_hash, window.target_brand, effective_model, prompt_version)
        if key in context.existing_keys:
            result.skipped_idempotent += 1
            continue
        pending.append(window)
    if len(pending) > window_limit:
        result.truncated = len(pending) - window_limit
        pending = pending[:window_limit]
        log.warning(
            "own_content_disparagement_windows_truncated",
            article_version_pub_id=context.article_version_pub_id,
            truncated=result.truncated,
            window_limit=window_limit,
        )

    def _persist(window: Window, record: DisparagementRecord) -> None:
        progress("persist", f"{window.target_brand}:{window.window_hash[:8]}")
        sink.persist(context=context, record=record)

    def _record(window: Window, **overrides: Any) -> DisparagementRecord:
        base: dict[str, Any] = {
            "subject_type": window.subject_type,
            "subject_pub_id": window.subject_pub_id,
            "platform": window.platform,
            "source_url": window.source_url,
            "window_hash": window.window_hash,
            "subject_brand": "",
            "target_brand": window.target_brand,
            "attitude": None,
            "disparagement": None,
            "evidence_quote": None,
            "confidence": None,
            "method": METHOD_LLM,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "judgment_status": "ok",
        }
        base.update(overrides)
        return DisparagementRecord(**base)

    def _dictionary_fallback(window: Window) -> None:
        fallback = dictionary_judge(
            window.text, target_brand=window.target_brand, known_brands=known_brands
        )
        _persist(
            window,
            _record(
                window,
                attitude=fallback.attitude,
                disparagement=fallback.disparagement,
                evidence_quote=fallback.evidence_quote or None,
                confidence=fallback.confidence,
                method=METHOD_DICTIONARY,
                model="",
                prompt_version=DICTIONARY_VERSION,
            ),
        )
        result.judged += 1
        result.dictionary_fallback += 1

    for window in pending:
        progress("judge", f"{window.target_brand}:{window.window_hash[:8]}")
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
            # LLM 失败 → 词典兜底（与 W3 采集侧同口径），失败如实记 failures
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
                _record(
                    window,
                    evidence_quote=judgment.evidence_quote[:2_000] or None,
                    judgment_status="validation_failure",
                ),
            )
            result.validation_failures += 1
            log.warning(
                "own_content_disparagement_validation_failure",
                article_version_pub_id=context.article_version_pub_id,
                target_brand=window.target_brand,
                reason=failure,
            )
            continue
        _persist(
            window,
            _record(
                window,
                subject_brand=judgment.subject,
                attitude=judgment.attitude,
                disparagement=judgment.disparagement,
                evidence_quote=judgment.evidence_quote,
                confidence=judgment.confidence,
            ),
        )
        result.judged += 1
    log.info(
        "own_content_disparagement_done",
        article_version_pub_id=context.article_version_pub_id,
        windows=result.windows,
        judged=result.judged,
        dictionary_fallback=result.dictionary_fallback,
        validation_failures=result.validation_failures,
        skipped_idempotent=result.skipped_idempotent,
        truncated=result.truncated,
        failures=len(result.failures),
    )
    return result


# ---------------------------------------------------------------------------
# activity 入口与异步泵
# ---------------------------------------------------------------------------


async def run_own_content_disparagement(
    item: OwnContentDisparagementInput,
    *,
    enabled: bool,
    window_limit: int,
    llm: AuditLlmConfig,
    loader: OwnContentLoader,
    sink: OwnContentSink,
    judge: DisparagementJudge | None = None,
    heartbeat: Callable[[dict[str, Any]], None] | None = None,
) -> OwnContentDisparagementResult:
    """异步泵封装：默认实现跑 asyncio.to_thread + 10s heartbeat 泵（W3 同款）。

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

    def _blocking() -> OwnContentDisparagementResult:
        return execute_own_content_disparagement(
            item,
            enabled=enabled,
            window_limit=window_limit,
            llm=llm,
            judge=effective_judge,
            loader=loader,
            sink=sink,
            on_progress=_on_progress,
        )

    if uses_default_judge:
        thread = asyncio.ensure_future(asyncio.to_thread(_blocking))
        while True:
            heartbeat({"article_version_pub_id": item.article_version_pub_id, **progress})
            done, _pending = await asyncio.wait({thread}, timeout=_HEARTBEAT_INTERVAL_S)
            if done:
                break
        return thread.result()
    heartbeat({"article_version_pub_id": item.article_version_pub_id, **progress})
    return _blocking()


@activity.defn(name="judge_own_content_disparagement")
async def judge_own_content_disparagement(
    item: OwnContentDisparagementInput,
) -> OwnContentDisparagementResult:
    """己方内容拉踩检测 activity 入口：env 配置 + 真实 DB/LLM 接线。"""
    raw_enabled = os.environ.get(ENV_ENABLED, "").strip().lower()
    enabled = raw_enabled not in {"0", "false", "no", "off"}
    if not enabled:
        return OwnContentDisparagementResult(disabled=True)
    window_limit = clamp_window_limit(os.environ.get("GEO_DISPARAGEMENT_WINDOW_LIMIT"))
    dsn = _postgres_dsn()
    settings: Settings = get_settings()
    llm = audit_llm_config_from_settings(settings)
    return await run_own_content_disparagement(
        item,
        enabled=enabled,
        window_limit=window_limit,
        llm=llm,
        loader=_PostgresOwnContentLoader(dsn),
        sink=_PostgresOwnContentSink(dsn),
        heartbeat=activity.heartbeat,
    )


__all__ = [
    "ENV_ENABLED",
    "OwnContentContext",
    "OwnContentDisparagementInput",
    "OwnContentDisparagementResult",
    "execute_own_content_disparagement",
    "judge_own_content_disparagement",
    "parse_brand_profile",
    "run_own_content_disparagement",
]
