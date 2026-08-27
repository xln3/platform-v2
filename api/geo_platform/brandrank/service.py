"""品牌可见度分析服务层：按需计算（读 analytics.answer → 抽取 → 规则归并 → 指标）。

数据通路（INV-1 口径延续：只读测量结果行，绝不触碰账号/profile 维）：
- 答案：``analytics.answer``（tenant+project+时间窗+``eligible AND NOT degraded``，
  对应旧库 answer_agg_blind 视图语义）；
- 信源：``analytics.citation_fact``（ordinal/host/canonical_url）；
- 项目/品牌/竞品：``platform.project/brand/competitor``（target_brand/competitors 缺省来源；
  ``project.brandrank_domain`` = 项目级规则包 domain 真源，s06_0014 起）。

LLM 抽取读取顺序（s06_0014 起）：
1. ``analytics.answer_brand_extract`` 表（fanout 落账，status='ok' 才算命中；
   failed 行与文件缓存 failed 条目同口径=未命中，下同）；
2. 文件缓存 ``runtime/brandrank-extract/``（domain.brandrank.cache，只读兜底——
   端点现抽成功仍写文件缓存，不回写表）；
3. LLM 现抽（端点对历史未覆盖 run 的兜底）。
表与文件缓存字段口径一致（status/model/error/domain/extracted_at）。
failed 条不进品牌分析但进信源分析，失败计数在 extraction 账目里披露（INV-32 零合成）。

诚实边界：
- LLM 未配置（GEO_BRANDRANK_LLM_API_KEY 缺失）且存在待抽取答案 → LlmDisabled
  （API 503 llm_disabled）；全部命中表/缓存或窗内零答案时无需 LLM，照常返回并如实披露。
- 窗内零答案 → insufficient=true 的空分析（如实空，不假装有数据）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import structlog
from psycopg.rows import dict_row
from sqlalchemy.exc import SQLAlchemyError

from domain.brandrank import adapter, cache, extract, metrics
from domain.brandrank.entities import EntityMaster, load_entity_master
from domain.brandrank.rules import DEFAULT_DOMAIN, DomainRules, domain_for_industry, load_domain
from domain.knowledge_evolution.contracts import ReasoningPolicy, RuntimeRequest
from domain.knowledge_evolution.domains.brand import apply_adopted_model_decisions
from domain.knowledge_evolution.release import KnowledgeReleaseError
from domain.knowledge_evolution.runtime import ReasoningEngine, ReasoningError

from ..config import get_settings
from ..knowledge.repository import KnowledgeRepository
from ..knowledge.service import gateway as knowledge_gateway
from ..knowledge.service import registry as knowledge_registry
from ..tenancy.database import SessionLocal
from ..tenancy.psycopg import tenant_connection
from ..tenancy.repository import TenantRepository

log = structlog.getLogger()

_MAX_ANSWERS = 2000  # 单窗防御性上限（超出截断并披露 truncated）
_MAX_COMPETITORS = 20  # 照旧库 api.py 口径
MAX_TOP_NS = 8  # 照旧库 api.py 口径；top_n 取值 1..50


class ProjectNotFound(LookupError):
    """project 在本租户内不存在 → API 404 project_not_found（跨租户同 404，不泄露存在性）。"""


class UnknownDomain(ValueError):
    """显式指定的 domain 无规则包 → API 400 unknown_domain。"""


class UnmappedIndustry(ValueError):
    """行业有值但未映射规则包 → API 400 unmapped_industry（绝不静默回退保险包）。"""


class DomainUnresolved(ValueError):
    """无显式 domain/industry 且项目真源未设 → API 400 brandrank_domain_unresolved
    （仅 brand-visibility 端点路径 fail-loud；resolve_rules 缺省行为不变）。"""


class LlmDisabled(RuntimeError):
    """存在待抽取答案但 LLM 未配置 → API 503 llm_disabled（诚实降级，绝不合成）。"""

    def __init__(self, status: dict[str, Any]) -> None:
        super().__init__("llm_disabled")
        self.status = status


class KnowledgeReasoningUnavailable(RuntimeError):
    """The caller required governed reasoning and prohibited deterministic fallback."""


def _master_release(master: EntityMaster) -> dict[str, Any]:
    return {
        "release_id": master.source_release_id or master.revision or "unversioned",
        "content_hash": master.source_content_hash or "sha256:unknown",
        "schema_version": master.schema_version,
        "source": master.source_mode or master.source_system or "unavailable",
        "degraded": bool(master.source_error),
    }


def _reasoned_entity_master(
    *,
    tenant_pub_id: str,
    project_pub_id: str,
    request_id: str,
    rules: DomainRules,
    base_master: EntityMaster,
    answers: list[dict[str, Any]],
    entries: dict[str, list[str]],
    target_brand: str | None,
    competitors: list[str],
    comparison_scopes: tuple[str, ...],
    policy: ReasoningPolicy,
    adopt_model_inferred: bool,
    on_model_failure: str,
    allow_external_model: bool,
    max_latency_ms: int | None,
    max_cost_usd: float | None,
) -> tuple[EntityMaster, dict[str, Any]]:
    if policy == ReasoningPolicy.DETERMINISTIC_ONLY:
        return base_master, {
            "policy": policy.value,
            "policy_id": "brandrank-runtime",
            "policy_version": "1",
            "release": _master_release(base_master),
            "model_called": False,
            "model_inferred_adopted": 0,
            "model_hypotheses": 0,
            "cache_status": "bypass",
            "degradation": [],
            "observation_count": 0,
        }

    answer_by_id = {str(answer["pub_id"]): answer for answer in answers}
    occurrences: dict[str, list[str]] = {}
    for answer_pub_id, brands in entries.items():
        for name in brands:
            cleaned = str(name).strip()
            if cleaned:
                occurrences.setdefault(cleaned, []).append(answer_pub_id)
    if not occurrences:
        return base_master, {
            "policy": policy.value,
            "policy_id": "brandrank-runtime",
            "policy_version": "1",
            "release": _master_release(base_master),
            "model_called": False,
            "model_inferred_adopted": 0,
            "model_hypotheses": 0,
            "cache_status": "bypass",
            "degradation": ["no_brand_mentions"],
            "observation_count": 0,
        }
    safe_project_ref = hashlib.sha256(project_pub_id.encode()).hexdigest()
    items: list[dict[str, Any]] = []
    for index, (name, answer_ids) in enumerate(occurrences.items(), start=1):
        contexts = []
        if allow_external_model:
            contexts = [
                str(answer_by_id[answer_id].get("response_text") or "")[:800]
                for answer_id in answer_ids[:4]
                if answer_id in answer_by_id
            ]
        source_ref = "answers:" + hashlib.sha256("|".join(sorted(answer_ids)).encode()).hexdigest()
        items.append(
            {
                "id": f"mention-{index}",
                "value": name,
                "contexts": contexts,
                "source_ref": source_ref,
                "source_type": "geo_brand_visibility",
                "safe_context": f"project-ref:{safe_project_ref}",
                "idempotency_key": hashlib.sha256(
                    (
                        f"{tenant_pub_id}|{rules.domain}|{name}|{'|'.join(sorted(answer_ids))}"
                    ).encode()
                ).hexdigest(),
            }
        )
    settings = get_settings()
    try:
        with SessionLocal() as session:
            TenantRepository(session, tenant_pub_id)
            repository = KnowledgeRepository(
                session,
                tenant_pub_id,
                namespace="geo-brandrank",
                domain="brand/entity-resolution",
            )
            runtime = RuntimeRequest(
                request_id=request_id,
                tenant=tenant_pub_id,
                namespace="geo-brandrank",
                domain="brand/entity-resolution",
                task="resolve_mentions_for_visibility",
                items=tuple(items),
                context={
                    "analysis_domain": rules.domain,
                    "comparison_scopes": list(comparison_scopes),
                    "named_competitors": competitors,
                    "target_brand": target_brand,
                    "safe_context": f"project-ref:{safe_project_ref}",
                    "allowed_evidence_refs": [],
                },
                policy=policy,
                policy_id="brandrank-runtime",
                policy_version="1",
                adopt_model_inferred=adopt_model_inferred,
                on_model_failure=on_model_failure,
                data_classification="internal",
                allow_external_model=allow_external_model,
                max_latency_ms=max_latency_ms,
                max_cost_usd=max_cost_usd,
            )
            response = ReasoningEngine(
                knowledge_registry(settings),
                repository,
                knowledge_gateway(settings),
            ).decide(runtime)
            session.commit()
    except (
        KeyError,
        ValueError,
        ReasoningError,
        KnowledgeReleaseError,
        OSError,
        SQLAlchemyError,
    ) as exc:
        if on_model_failure == "fail":
            raise KnowledgeReasoningUnavailable(str(exc)) from exc
        log.warning(
            "brandrank_knowledge_reasoning_degraded",
            exception_type=type(exc).__name__,
            policy=policy.value,
        )
        return base_master, {
            "policy": policy.value,
            "policy_id": "brandrank-runtime",
            "policy_version": "1",
            "release": _master_release(base_master),
            "model_called": False,
            "model_inferred_adopted": 0,
            "model_hypotheses": 0,
            "cache_status": "bypass",
            "degradation": [f"knowledge_runtime_unavailable:{type(exc).__name__}"],
            "observation_count": 0,
        }
    overlay = apply_adopted_model_decisions(base_master, response.decisions)
    return overlay, {
        "policy": response.policy.value,
        "policy_id": response.policy_id,
        "policy_version": response.policy_version,
        "release": asdict(response.release),
        "model_called": response.prompt_id is not None,
        "model_provider": response.model_provider,
        "model": response.model_name,
        "model_version": response.model_version,
        "prompt_id": response.prompt_id,
        "prompt_version": response.prompt_version,
        "model_inferred_adopted": sum(
            decision.knowledge_status.value == "model_inferred" and decision.adopted
            for decision in response.decisions
        ),
        "model_hypotheses": len(response.model_hypotheses),
        "cache_status": response.cache_status,
        "degradation": list(response.degradation),
        "observation_count": response.observation_count,
        "latency_ms": response.latency_ms,
        "usage": response.usage,
    }


# ── DB 读取接缝（单测 monkeypatch 点；生产走真 PG）──────────────────────────
@contextmanager
def _platform_tenant_connection(dsn: str, tenant_pub_id: str) -> Iterator[psycopg.Connection[Any]]:
    """platform schema 读连接：解析 tenant uuid 并置 app.tenant_id + app.tenant_pub_id。

    照 analytics/service.py 同款双 selector 口径（platform.* 表 RLS 按 app.tenant_id）。
    """
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        tenant_row = connection.execute(
            "SELECT id FROM platform.tenant WHERE pub_id=%s", (tenant_pub_id,)
        ).fetchone()
        if tenant_row is None:
            raise LookupError("tenant_not_found")
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true), "
            "set_config('app.tenant_pub_id', %s, true)",
            (str(tenant_row["id"]), tenant_pub_id),
        )
        yield connection


def fetch_project(dsn: str, tenant_pub_id: str, project_pub_id: str) -> dict[str, Any] | None:
    """项目 + 品牌名列表 + 竞品名列表；不存在 → None（跨租户同 None）。

    品牌/竞品只取 name（target_brand/competitors 缺省来源），不取 website 等无关列；
    brandrank_domain = 项目级规则包 domain 真源（s06_0014，可空）。
    """
    with _platform_tenant_connection(dsn, tenant_pub_id) as connection:
        project = connection.execute(
            """
            SELECT id, pub_id, name, brandrank_domain FROM platform.project
            WHERE pub_id=%s
              AND tenant_id=NULLIF(current_setting('app.tenant_id', true), '')::uuid
            """,
            (project_pub_id,),
        ).fetchone()
        if project is None:
            return None
        brands = connection.execute(
            """
            SELECT name FROM platform.brand
            WHERE tenant_id=NULLIF(current_setting('app.tenant_id', true), '')::uuid
              AND project_id=%s
            ORDER BY created_at, pub_id
            """,
            (project["id"],),
        ).fetchall()
        competitors = connection.execute(
            """
            SELECT name FROM platform.competitor
            WHERE tenant_id=NULLIF(current_setting('app.tenant_id', true), '')::uuid
              AND project_id=%s
            ORDER BY created_at, pub_id
            """,
            (project["id"],),
        ).fetchall()
    return {
        "pub_id": project["pub_id"],
        "name": project["name"],
        "brandrank_domain": project["brandrank_domain"],
        "brand_names": [row["name"] for row in brands],
        "competitor_names": [row["name"] for row in competitors],
    }


def fetch_project_brandrank_domain(dsn: str, tenant_pub_id: str, project_pub_id: str) -> str | None:
    """项目级 brandrank domain 真源（fanout extract_brands_activity 的取值缝）。

    租户/项目不存在或未设置 → None（调用方落 failed/domain_unset 标记，
    绝不臆造规则包；不存在不抛错——这是永久态而非可重试的基础设施故障）。
    """
    if not project_pub_id:
        return None
    try:
        with _platform_tenant_connection(dsn, tenant_pub_id) as connection:
            row = connection.execute(
                """
                SELECT brandrank_domain FROM platform.project
                WHERE pub_id=%s
                  AND tenant_id=NULLIF(current_setting('app.tenant_id', true), '')::uuid
                """,
                (project_pub_id,),
            ).fetchone()
    except LookupError:
        return None  # tenant_not_found ≈ 未设置
    if row is None:
        return None
    value = (row["brandrank_domain"] or "").strip()
    return value or None


def fetch_answers(
    dsn: str, tenant_pub_id: str, project_pub_id: str, since: datetime
) -> tuple[list[dict[str, Any]], bool]:
    """本窗 eligible 答案（eligible AND NOT degraded = 旧库 answer_agg_blind 语义）。

    返回 (rows, truncated)：超 _MAX_ANSWERS 截断并置标记（诚实披露，绝不静默丢）。"""
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT pub_id, query_text, response_text, model, region, mode, capture_time
            FROM analytics.answer
            WHERE tenant_pub_id=%s AND project_pub_id=%s
              AND eligible AND NOT degraded
              AND capture_time >= %s
            ORDER BY capture_time, pub_id
            LIMIT %s
            """,
            (tenant_pub_id, project_pub_id, since, _MAX_ANSWERS + 1),
        ).fetchall()
    truncated = len(rows) > _MAX_ANSWERS
    return [dict(row) for row in rows[:_MAX_ANSWERS]], truncated


def fetch_citations(
    dsn: str, tenant_pub_id: str, answer_pub_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """答案的引用事实（信源分析原料）：{answer_pub_id: [{ordinal,host,url},...]}。

    只取每个答案**最新一次** analysis_run 的引用：citation_fact 的 UNIQUE 键含
    analysis_run_pub_id，重分析（新 run）会让同一 ordinal 出现多行——不收敛会把
    信源权重重复计数（旧口径=每答案引用计一次）。answer_analysis 的 latest 口径
    （created_at DESC,id DESC）照 analytics/service.py aggregate_competitors 先例。
    """
    if not answer_pub_ids:
        return {}
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            WITH latest_run AS (
              SELECT DISTINCT ON (answer_pub_id) answer_pub_id, analysis_run_pub_id
              FROM analytics.citation_fact
              WHERE tenant_pub_id=%s AND answer_pub_id=ANY(%s::text[])
              ORDER BY answer_pub_id, id DESC
            )
            SELECT c.answer_pub_id, c.ordinal, c.host, c.canonical_url, c.original_url,
                   c.title, c.cited_text, c.own_source, c.content_hash
            FROM analytics.citation_fact c
            JOIN latest_run lr ON lr.answer_pub_id=c.answer_pub_id
                              AND lr.analysis_run_pub_id=c.analysis_run_pub_id
            WHERE c.tenant_pub_id=%s
            ORDER BY c.answer_pub_id, c.ordinal
            """,
            (tenant_pub_id, answer_pub_ids, tenant_pub_id),
        ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(row["answer_pub_id"], []).append(dict(row))
    return out


def fetch_brand_extracts(
    dsn: str, tenant_pub_id: str, answer_pub_ids: list[str], domain: str
) -> dict[str, dict[str, Any]]:
    """fanout 落账表读取接缝：{answer_pub_id: row}（analytics.answer_brand_extract）。

    只按 (tenant, domain) 取本批答案的行；命中口径与文件缓存一致——调用方只把
    status='ok' 行当命中，failed 行视为未命中（留给缓存/LLM 兜底重试）。
    """
    if not answer_pub_ids:
        return {}
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT answer_pub_id, brands, status, model, error, domain, extracted_at
            FROM analytics.answer_brand_extract
            WHERE tenant_pub_id=%s AND domain=%s AND answer_pub_id=ANY(%s::text[])
            """,
            (tenant_pub_id, domain, answer_pub_ids),
        ).fetchall()
    return {row["answer_pub_id"]: dict(row) for row in rows}


# ── 编排 ──────────────────────────────────────────────────────────────────
def resolve_rules(
    domain: str | None,
    industry: str | None,
    project_domain: str | None = None,
    *,
    allow_default: bool = True,
) -> tuple[DomainRules, str]:
    """domain 解析优先级：显式 domain > 显式 industry（fail-loud 映射）> 项目真源
    （project.brandrank_domain，s06_0014）> 缺省包。

    V2 项目无持久化行业字段（intake profile 无 industry 列，见 intake/contract.py），
    故行业只能由调用方显式给出；项目真源值非法（绕过 API 词表校验的直写）同样
    fail-loud 400，绝不静默回退保险包；全部缺省且 allow_default → DEFAULT_DOMAIN
    并如实标 domain_source=default；allow_default=False（brand-visibility 端点
    路径）→ DomainUnresolved fail-loud（400 brandrank_domain_unresolved，
    绝不静默拿保险包跑评测）。
    """
    if domain and domain.strip():
        try:
            return load_domain(domain.strip()), "explicit"
        except ValueError as exc:
            raise UnknownDomain(str(exc)) from exc
    if industry and industry.strip():
        try:
            return load_domain(domain_for_industry(industry)), "industry"
        except ValueError as exc:
            raise UnmappedIndustry(str(exc)) from exc
    if project_domain and project_domain.strip():
        try:
            return load_domain(project_domain.strip()), "project"
        except ValueError as exc:
            raise UnknownDomain(str(exc)) from exc
    if not allow_default:
        raise DomainUnresolved(
            "未给出显式 domain/industry 且项目未设置 brandrank_domain（规则包真源），无法确定分析域"
        )
    return load_domain(DEFAULT_DOMAIN), "default"


def compute_brand_visibility(
    *,
    dsn: str,
    tenant_pub_id: str,
    project_pub_id: str,
    window_days: int,
    domain: str | None = None,
    industry: str | None = None,
    category: str | None = None,
    target_brand: str | None = None,
    competitors: list[str] | None = None,
    comparison_scopes: list[str] | None = None,
    top_ns: tuple[int, ...] | None = None,
    reasoning_policy: ReasoningPolicy | str = ReasoningPolicy.DETERMINISTIC_ONLY,
    adopt_model_inferred: bool = False,
    on_reasoning_failure: str = "degrade",
    allow_external_reasoning_model: bool = False,
    max_reasoning_latency_ms: int | None = None,
    max_reasoning_cost_usd: float | None = None,
    reasoning_request_id: str | None = None,
    client_factory: Callable[[], Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """按需计算品牌可见度：读窗内答案 → 缓存命中跳过 → LLM 补抽 → 指标快照。

    client_factory 是测试缝（生产=extract.default_client）；返回 envelope+result 全量 dict。
    domain 解析 fail-loud（allow_default=False）：无显式 domain/industry 且项目真源
    未设 → DomainUnresolved（API 400 brandrank_domain_unresolved），不回退缺省包。
    """
    project = fetch_project(dsn, tenant_pub_id, project_pub_id)
    if project is None:
        raise ProjectNotFound(project_pub_id)
    rules, domain_source = resolve_rules(
        domain, industry, project.get("brandrank_domain"), allow_default=False
    )
    resolved_category = (category or "").strip() or rules.category
    resolved_top_ns = tuple(top_ns) if top_ns else metrics.DEFAULT_TOP_NS
    resolved_target = (target_brand or "").strip() or (
        project["brand_names"][0] if project["brand_names"] else None
    )
    resolved_competitors = (
        [c.strip() for c in competitors if c.strip()][:_MAX_COMPETITORS]
        if competitors is not None
        else project["competitor_names"][:_MAX_COMPETITORS]
    )
    resolved_comparison_scopes = tuple(comparison_scopes or ())
    resolved_reasoning_policy = ReasoningPolicy(reasoning_policy)

    now = now or datetime.now(UTC)
    since = now - timedelta(days=window_days)
    answers, truncated = fetch_answers(dsn, tenant_pub_id, project_pub_id, since)
    citations = fetch_citations(dsn, tenant_pub_id, [a["pub_id"] for a in answers])

    # ── 抽取读取顺序（s06_0014）：表（ok 行）→ 文件缓存（只读兜底）→ LLM 现抽 ──
    table_rows = fetch_brand_extracts(
        dsn, tenant_pub_id, [a["pub_id"] for a in answers], rules.domain
    )
    entries: dict[str, list[str]] = {}
    pending: list[tuple[dict[str, Any], str]] = []
    n_table_ok = n_cache_ok = 0
    extraction_prompt_version = str(rules.llm_defaults.get("prompt_version") or "legacy")
    for answer in answers:
        table_row = table_rows.get(answer["pub_id"])
        if table_row is not None and table_row.get("status") == "ok":
            brands = table_row.get("brands")
            if isinstance(brands, list) and all(isinstance(b, str) for b in brands):
                entries[answer["pub_id"]] = list(brands)
                n_table_ok += 1
                continue
            # ok 行但 brands 形状不符 → 与坏缓存同口径：不当命中，落兜底重抽
        key = cache.cache_key(
            rules.domain,
            answer.get("response_text") or "",
            prompt_version=extraction_prompt_version,
        )
        hit = cache.load(key)
        if hit is not None:
            entries[answer["pub_id"]] = list(hit["brands"])
            n_cache_ok += 1
        else:
            pending.append((answer, key))

    cfg = extract.load_config()
    model = cfg[3] if cfg else None
    n_ok_new = n_failed_new = 0
    if pending:
        if cfg is None:
            raise LlmDisabled(extract.llm_status(rules))
        factory = client_factory or extract.default_client
        client = factory()
        tasks = [
            (i, a.get("response_text") or "", resolved_category)
            for i, (a, _k) in enumerate(pending)
        ]
        for idx, brands, error in extract.extract_brands_batch(
            client, tasks, model=model or "", rules=rules
        ):
            answer, key = pending[idx]
            if error is None:
                cache.store(
                    key,
                    brands=brands or [],
                    model=model or "",
                    status="ok",
                    domain=rules.domain,
                    prompt_version=extraction_prompt_version,
                )
                entries[answer["pub_id"]] = list(brands or [])
                n_ok_new += 1
            else:
                cache.store(
                    key,
                    brands=[],
                    model=model or "",
                    status="failed",
                    error=error,
                    domain=rules.domain,
                    prompt_version=extraction_prompt_version,
                )
                n_failed_new += 1
                log.warning(
                    "brandrank_extract_failed", answer_pub_id=answer["pub_id"], error=error[:200]
                )

    # ── 组装她的 brand_list 记录（仅抽取成功条）与信源记录（全部 eligible 条）──
    records: list[dict[str, Any]] = []
    source_records: list[dict[str, Any]] = []
    n_failed_total = 0
    for answer in answers:
        thinking_mode = adapter.mode_label(answer.get("mode") or "")
        for citation in citations.get(answer["pub_id"], []):
            source_records.append(
                {
                    **adapter.citation_to_source_entry(citation),
                    "thinking_mode": thinking_mode,
                    "ip": answer.get("region") or "",
                }
            )
        brands = entries.get(answer["pub_id"])
        if brands is None:
            n_failed_total += 1  # 无缓存=抽取失败，诚实剔除（INV-32）
            continue
        records.append(adapter.answer_to_brand_record(answer, brands))

    settings = get_settings()
    entity_master = load_entity_master(
        rules.domain,
        snapshot_dir=settings.siliconindex_snapshot_dir,
        knowledge_release_dir=settings.knowledge_release_dir,
    )
    entity_master, reasoning = _reasoned_entity_master(
        tenant_pub_id=tenant_pub_id,
        project_pub_id=project_pub_id,
        request_id=reasoning_request_id
        or "brandrank:"
        + hashlib.sha256(
            f"{tenant_pub_id}|{project_pub_id}|{since.isoformat()}".encode()
        ).hexdigest(),
        rules=rules,
        base_master=entity_master,
        answers=answers,
        entries=entries,
        target_brand=resolved_target,
        competitors=resolved_competitors,
        comparison_scopes=resolved_comparison_scopes,
        policy=resolved_reasoning_policy,
        adopt_model_inferred=adopt_model_inferred,
        on_model_failure=on_reasoning_failure,
        allow_external_model=allow_external_reasoning_model,
        max_latency_ms=max_reasoning_latency_ms,
        max_cost_usd=max_reasoning_cost_usd,
    )
    result = metrics.analyze(
        records,
        source_records,
        rules=rules,
        target_brand=resolved_target,
        competitors=resolved_competitors,
        top_ns=resolved_top_ns,
        entity_master=entity_master,
        comparison_scopes=resolved_comparison_scopes,
    )
    result["insufficient"] = len(answers) == 0  # 0 条 eligible=数据不足（照报告 T1 语义）
    result["extraction"] = {  # 抽取账目披露（诚实边界）
        "n_answers": len(answers),
        "table_ok": n_table_ok,  # fanout 落账表命中（s06_0014）
        "cached_ok": n_cache_ok,  # 文件缓存命中（只读兜底）
        "extracted_new": n_ok_new,
        "failed_new": n_failed_new,
        "failed_total": n_failed_total,
        "llm_model": model,
        "prompt_version": extraction_prompt_version,
    }

    return {
        "project_pub_id": project_pub_id,
        "project_name": project["name"],
        "window_days": window_days,
        "since": since.isoformat(),
        "generated_at": now.isoformat(),
        "domain": rules.domain,
        "domain_source": domain_source,
        "category": resolved_category,
        "target_brand": resolved_target,
        "competitors": resolved_competitors,
        "comparison_scopes": list(resolved_comparison_scopes),
        "truncated": truncated,
        "llm": {"enabled": cfg is not None, "model": model},
        "knowledge_reasoning": reasoning,
        "result": result,
    }
