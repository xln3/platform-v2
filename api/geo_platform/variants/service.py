"""W5 变体服务（DB 编排层）：种子聚合、矩阵生成、确认门、零提及闭环、覆盖报告。

数据出处（零编造纪律）：
  * 种子首选 = collection_task.search_queries_json（W1 豆包 SSE 真实检索词）与
    answer_text 用户口吻问句（textutil 确定性抽取）；
  * 轴值 = intake profile.audience_type / intake_promo 产品名 / 最新冻结配置 regions，
    全部读库，缺轴即只剩 "通用"，绝不臆造；
  * 零提及判定 = PG analytics.answer ⋈ analytics.answer_analysis（同一 tenant/project，
    任一分析 mentioned=true 即算"有结果"）；
  * LLM 扩写仅补充：未配置 GEO_RESEARCH_LLM_API_KEY → 跳过并在摘要 llm_note 如实标注。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from domain.evidence.dlp import assert_secret_free

from ..collection.models import CollectionRun, CollectionTask
from ..intake.models import IntakeProfile, IntakePromo
from ..intake.research import LlmConfig
from ..projects.models import (
    AssetConfirmationVersion,
    MonitoringConfig,
    MonitoringConfigVersion,
    Project,
    QueryGroup,
    QueryItem,
)
from . import llm, matrix, textutil
from .matrix import Axes, Cell
from .models import QueryVariant, VariantSeed

# 生成预算（可配常量，防单次爆炸）。
_MAX_TASKS_SCANNED = 500
_MAX_QUESTIONS_PER_TASK = 10
_MAX_NEW_SEEDS_PER_RUN = 200
_MAX_GAP_VARIANTS_PER_RUN = 60
_MAX_VARIANT_TEXT_LEN = 120
_ANALYTICS_SCAN_LIMIT = 20000


@dataclass
class GenerateSummary:
    seeds_upserted: int = 0
    seeds_dropped_dlp: int = 0
    variants_created: int = 0
    variants_skipped_existing: int = 0
    gap_variants_created: int = 0
    llm_variants_created: int = 0
    llm_note: str = "not_requested"
    recycled_zero_mention: int = 0
    verified_marked: int = 0
    coverage_before: dict[str, Any] = field(default_factory=dict)
    coverage_after: dict[str, Any] = field(default_factory=dict)


# ── 读侧装配 ────────────────────────────────────────────────────────────────


def existing_pool(session: Session, tenant_id: uuid.UUID, project: Project) -> list[str]:
    """现有 query 池 = 项目全部 QueryItem（query_group 归属本项目）。"""
    return list(
        session.scalars(
            select(QueryItem.text)
            .join(QueryGroup, QueryGroup.id == QueryItem.group_id)
            .where(QueryItem.tenant_id == tenant_id, QueryGroup.project_id == project.id)
        ).all()
    )


def latest_config_snapshot(
    session: Session, tenant_id: uuid.UUID, project: Project
) -> dict[str, Any] | None:
    version = session.scalar(
        select(MonitoringConfigVersion)
        .join(MonitoringConfig, MonitoringConfig.id == MonitoringConfigVersion.config_id)
        .where(
            MonitoringConfigVersion.tenant_id == tenant_id,
            MonitoringConfig.project_id == project.id,
        )
        .order_by(MonitoringConfigVersion.revision.desc())
        .limit(1)
    )
    if version is None:
        return None
    try:
        snapshot: dict[str, Any] = json.loads(version.snapshot_json)
    except json.JSONDecodeError:
        return None
    return snapshot


def project_axes(session: Session, tenant_id: uuid.UUID, project: Project) -> Axes:
    """轴值真实出处：受众=intake profile.audience_type；产品线=intake_promo(product).name
    （缺省回退 asset 确认 product_name）；地域=最新冻结配置 regions（缺省回退 intake regions）。"""
    profile = session.scalar(
        select(IntakeProfile).where(
            IntakeProfile.tenant_id == tenant_id, IntakeProfile.project_id == project.id
        )
    )
    audiences = list(profile.audience_type) if profile is not None else []
    product_lines = list(
        session.scalars(
            select(IntakePromo.payload["name"].astext).where(
                IntakePromo.tenant_id == tenant_id,
                IntakePromo.project_id == project.id,
                IntakePromo.kind == "product",
            )
        ).all()
    )
    if not product_lines:
        asset = session.scalar(
            select(AssetConfirmationVersion)
            .where(
                AssetConfirmationVersion.tenant_id == tenant_id,
                AssetConfirmationVersion.project_id == project.id,
            )
            .order_by(AssetConfirmationVersion.revision.desc())
            .limit(1)
        )
        if asset is not None:
            product_lines = [asset.product_name]
    snapshot = latest_config_snapshot(session, tenant_id, project)
    regions: list[str] = []
    if snapshot is not None:
        raw = snapshot.get("regions", [])
        if isinstance(raw, list):
            regions = [str(value) for value in raw]
    if not regions and profile is not None:
        regions = list(profile.regions)
    return matrix.build_axes(audiences, regions, product_lines)


def project_brand(session: Session, tenant_id: uuid.UUID, project: Project) -> str | None:
    asset = session.scalar(
        select(AssetConfirmationVersion)
        .where(
            AssetConfirmationVersion.tenant_id == tenant_id,
            AssetConfirmationVersion.project_id == project.id,
        )
        .order_by(AssetConfirmationVersion.revision.desc())
        .limit(1)
    )
    return asset.brand_name if asset is not None else None


# ── 种子聚合（真实数据优先） ────────────────────────────────────────────────


def _upsert_seed(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    project: Project,
    text_value: str,
    source_type: str,
    source_ref: str,
    usage: int,
    pub_id_factory: Any,
) -> bool:
    """按 (project, normalized) 幂等 upsert；已存在则累计 usage 并刷新 last_seen。"""
    normalized = textutil.normalize_query(text_value)
    if not normalized or len(text_value) > _MAX_VARIANT_TEXT_LEN:
        return False
    try:
        assert_secret_free(text_value)
    except ValueError:
        raise
    seed = session.scalar(
        select(VariantSeed).where(
            VariantSeed.tenant_id == tenant_id,
            VariantSeed.project_id == project.id,
            VariantSeed.normalized == normalized,
        )
    )
    if seed is None:
        session.add(
            VariantSeed(
                pub_id=pub_id_factory("sed"),
                tenant_id=tenant_id,
                project_id=project.id,
                text=text_value.strip(),
                normalized=normalized,
                source_type=source_type,
                source_ref=source_ref,
                usage_count=usage,
            )
        )
    else:
        seed.usage_count += usage
        seed.last_seen_at = datetime.now(UTC)
    return True


def aggregate_seeds(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    project: Project,
    window_days: int | None,
    pub_id_factory: Any,
    summary: GenerateSummary,
) -> None:
    """从 collection_task 聚合真实种子：SSE 检索词（usage 计数）+ 回答用户口吻问句。"""
    statement = (
        select(CollectionTask)
        .join(CollectionRun, CollectionRun.id == CollectionTask.run_id)
        .where(
            CollectionTask.tenant_id == tenant_id,
            CollectionRun.project_id == project.id,
        )
        .order_by(CollectionTask.created_at.desc())
        .limit(_MAX_TASKS_SCANNED)
    )
    if window_days is not None and window_days > 0:
        cutoff = datetime.now(UTC) - timedelta(days=window_days)
        statement = statement.where(CollectionTask.created_at >= cutoff)
    tasks = list(session.scalars(statement).all())
    new_seeds = 0
    for task in tasks:
        if new_seeds >= _MAX_NEW_SEEDS_PER_RUN:
            break
        try:
            queries = json.loads(task.search_queries_json or "[]")
        except json.JSONDecodeError:
            queries = []
        if isinstance(queries, list):
            for item in queries:
                if new_seeds >= _MAX_NEW_SEEDS_PER_RUN:
                    break
                if not isinstance(item, dict):
                    continue
                query = str(item.get("query", "")).strip()
                if not query:
                    continue
                try:
                    if _upsert_seed(
                        session,
                        tenant_id=tenant_id,
                        project=project,
                        text_value=query,
                        source_type="search_query",
                        source_ref=task.pub_id,
                        usage=1,
                        pub_id_factory=pub_id_factory,
                    ):
                        new_seeds += 1
                        summary.seeds_upserted += 1
                except ValueError:
                    summary.seeds_dropped_dlp += 1
        if not task.answer_text:
            continue
        for question in textutil.extract_user_questions(task.answer_text)[:_MAX_QUESTIONS_PER_TASK]:
            if new_seeds >= _MAX_NEW_SEEDS_PER_RUN:
                break
            try:
                if _upsert_seed(
                    session,
                    tenant_id=tenant_id,
                    project=project,
                    text_value=question,
                    source_type="answer_mining",
                    source_ref=task.pub_id,
                    usage=1,
                    pub_id_factory=pub_id_factory,
                ):
                    new_seeds += 1
                    summary.seeds_upserted += 1
            except ValueError:
                summary.seeds_dropped_dlp += 1
    session.flush()


# ── 矩阵空格变体（模板口径固定，绝不编造品牌事实） ──────────────────────────


def render_gap_variant(cell: Cell, brand: str) -> str:
    """按格子坐标渲染一条候选问句。subject 取产品线（"通用" 时回退品牌名）。

    模板口径纪律：每条模板只含本意图的关键词（"面向X的" 前缀规避 "适合" 与场景
    意图串桶），保证渲染文本经 classify_intent + 轴归因后**落回被补格子**
    （单元测试逐意图锁定 roundtrip）。
    """
    subject = cell.product_line if cell.product_line != matrix.GENERIC else brand
    prefix = f"面向{cell.audience}的" if cell.audience != matrix.GENERIC else ""
    loc = cell.region if cell.region != matrix.GENERIC else ""
    templates = {
        "推荐": f"{prefix}{loc}{subject}推荐有哪些",
        "对比": f"{prefix}{loc}{subject}和同类产品对比哪个好",
        "选购": f"{prefix}{loc}{subject}怎么选",
        "场景": f"{prefix}{loc}{subject}什么场景下使用",
        "口碑": f"{prefix}{loc}{subject}口碑怎么样",
        "地域": f"{prefix}{loc}{subject}服务覆盖哪些地区",
    }
    return templates[cell.intent]


# ── 生成主流程 ──────────────────────────────────────────────────────────────


def _variant_exists(
    session: Session, tenant_id: uuid.UUID, project: Project, normalized: str
) -> bool:
    return (
        session.scalar(
            select(QueryVariant.id).where(
                QueryVariant.tenant_id == tenant_id,
                QueryVariant.project_id == project.id,
                QueryVariant.normalized == normalized,
            )
        )
        is not None
    )


def _add_variant(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    project: Project,
    pub_id_factory: Any,
    text_value: str,
    source_type: str,
    source_ref: str,
    cell: Cell,
    cluster_id: str | None,
    cluster_size: int,
    model: str | None = None,
    prompt_version: str | None = None,
) -> QueryVariant | None:
    normalized = textutil.normalize_query(text_value)
    if not normalized or len(text_value) > _MAX_VARIANT_TEXT_LEN:
        return None
    try:
        assert_secret_free(text_value)
    except ValueError:
        return None
    variant = QueryVariant(
        pub_id=pub_id_factory("var"),
        tenant_id=tenant_id,
        project_id=project.id,
        text=text_value.strip(),
        normalized=normalized,
        source_type=source_type,
        source_ref=source_ref,
        intent=cell.intent,
        audience=cell.audience,
        region=cell.region,
        product_line=cell.product_line,
        marginal_coverage_cell=cell.key(),
        cluster_id=cluster_id,
        cluster_size=cluster_size,
        model=model,
        prompt_version=prompt_version,
    )
    session.add(variant)
    return variant


def generate_variants(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    tenant_pub_id: str,
    project: Project,
    window_days: int | None,
    use_llm: bool,
    llm_config: LlmConfig | None,
    max_variants: int,
    legacy_recycle_answer_analysis: bool,
    pub_id_factory: Any,
) -> GenerateSummary:
    """种子聚合 → 聚类出候选 → 矩阵空格补格 → （可选）LLM 扩写。

    V2 起生成写路径不再根据 ``answer_analysis.mentioned`` 自动验证或回炉
    变体。正式目标/效果必须由 official snapshot contribution 读路径完成；旧
    ``recycle_zero_mentions`` 仅在调用方显式设置
    ``legacy_recycle_answer_analysis`` 时用于历史审计。
    """
    summary = GenerateSummary()
    axes = project_axes(session, tenant_id, project)
    pool = existing_pool(session, tenant_id, project)
    pool_normalized = {textutil.normalize_query(item) for item in pool}
    before = matrix.compute_coverage(pool, axes)
    summary.coverage_before = {
        "total_cells": before.total_cells,
        "covered_cells": before.covered_cells,
        "coverage_ratio": before.coverage_ratio,
    }

    aggregate_seeds(
        session,
        tenant_id=tenant_id,
        project=project,
        window_days=window_days,
        pub_id_factory=pub_id_factory,
        summary=summary,
    )

    # 种子聚类：同簇只留代表（用量最高者），成员数落 cluster_size。
    seeds = list(
        session.scalars(
            select(VariantSeed)
            .where(VariantSeed.tenant_id == tenant_id, VariantSeed.project_id == project.id)
            .order_by(VariantSeed.usage_count.desc(), VariantSeed.created_at.asc())
        ).all()
    )
    clusters = textutil.cluster_texts([(seed.text, seed.usage_count) for seed in seeds])
    seed_by_normalized = {seed.normalized: seed for seed in seeds}
    for cluster in clusters:
        if summary.variants_created >= max_variants:
            break
        representative = cluster.representative
        normalized = textutil.normalize_query(representative)
        if normalized in pool_normalized or _variant_exists(
            session, tenant_id, project, normalized
        ):
            summary.variants_skipped_existing += 1
            continue
        seed = seed_by_normalized.get(normalized)
        cell = matrix.attribute_query(representative, axes)
        variant = _add_variant(
            session,
            tenant_id=tenant_id,
            project=project,
            pub_id_factory=pub_id_factory,
            text_value=representative,
            source_type=seed.source_type if seed is not None else "search_query",
            source_ref=seed.source_ref if seed is not None else "",
            cell=cell,
            cluster_id=cluster.cluster_id,
            cluster_size=len(cluster.members),
        )
        if variant is not None:
            summary.variants_created += 1
    session.flush()

    # 矩阵空格：对前 N 个空格模板生成，marginal_coverage_cell = 被补格子坐标。
    brand = project_brand(session, tenant_id, project)
    if brand is not None:
        for gap_cell in before.gaps[:_MAX_GAP_VARIANTS_PER_RUN]:
            if summary.variants_created >= max_variants:
                break
            text_value = render_gap_variant(gap_cell, brand)
            normalized = textutil.normalize_query(text_value)
            if normalized in pool_normalized or _variant_exists(
                session, tenant_id, project, normalized
            ):
                continue
            variant = _add_variant(
                session,
                tenant_id=tenant_id,
                project=project,
                pub_id_factory=pub_id_factory,
                text_value=text_value,
                source_type="matrix_gap",
                source_ref="matrix:coverage",
                cell=gap_cell,
                cluster_id=None,
                cluster_size=1,
            )
            if variant is not None:
                summary.variants_created += 1
                summary.gap_variants_created += 1
        session.flush()

    # LLM 扩写（补充；不可用 → 跳过并如实标注，不阻塞）。
    if use_llm:
        gap_intents = sorted({cell.intent for cell in before.gaps}) or list(textutil.INTENTS)
        try:
            if llm_config is None:
                raise llm.LlmDisabled("llm_config_missing")
            expansions = llm.expand_queries(
                brand=brand or project.name,
                product_lines=[v for v in axes.product_lines if v != matrix.GENERIC],
                gap_intents=gap_intents,
                max_variants=max(1, max_variants - summary.variants_created),
                config=llm_config,
            )
        except llm.LlmDisabled:
            summary.llm_note = "llm_disabled"
        except llm.LlmFailed:
            summary.llm_note = "llm_failed"
        else:
            summary.llm_note = "expanded"
            for expansion in expansions:
                if summary.variants_created >= max_variants:
                    break
                normalized = textutil.normalize_query(expansion)
                if normalized in pool_normalized or _variant_exists(
                    session, tenant_id, project, normalized
                ):
                    continue
                variant = _add_variant(
                    session,
                    tenant_id=tenant_id,
                    project=project,
                    pub_id_factory=pub_id_factory,
                    text_value=expansion,
                    source_type="llm_expansion",
                    source_ref="llm:" + (llm_config.model if llm_config else ""),
                    cell=matrix.attribute_query(expansion, axes),
                    cluster_id=None,
                    cluster_size=1,
                    model=llm_config.model if llm_config else None,
                    prompt_version=llm.PROMPT_VERSION,
                )
                if variant is not None:
                    summary.variants_created += 1
                    summary.llm_variants_created += 1
            session.flush()

    if legacy_recycle_answer_analysis:
        recycle_zero_mentions(
            session,
            tenant_id=tenant_id,
            tenant_pub_id=tenant_pub_id,
            project=project,
            pub_id_factory=pub_id_factory,
            summary=summary,
        )

    variant_texts = list(
        session.scalars(
            select(QueryVariant.text).where(
                QueryVariant.tenant_id == tenant_id,
                QueryVariant.project_id == project.id,
                QueryVariant.status.in_(["pending", "confirmed"]),
            )
        ).all()
    )
    after = matrix.compute_coverage(pool + variant_texts, axes)
    summary.coverage_after = {
        "total_cells": after.total_cells,
        "covered_cells": after.covered_cells,
        "coverage_ratio": after.coverage_ratio,
    }
    return summary


# ── 零提及闭环（analytics.answer 口径） ─────────────────────────────────────

_ZERO_MENTION_SQL = text(
    """
    SELECT a.query_text AS query_text, aa.mentioned AS mentioned
      FROM analytics.answer a
      JOIN analytics.answer_analysis aa
        ON aa.tenant_pub_id = a.tenant_pub_id AND aa.answer_pub_id = a.pub_id
     WHERE a.tenant_pub_id = :tenant_pub_id
       AND a.project_pub_id = :project_pub_id
       AND a.query_text IS NOT NULL
     LIMIT :scan_limit
    """
)


def recycle_zero_mentions(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    tenant_pub_id: str,
    project: Project,
    pub_id_factory: Any,
    summary: GenerateSummary,
) -> None:
    """Legacy audit helper; never called by the V2 formal generate path.

    已验证闭环：mentioned>0 → verified=True；mentioned=0 → 回炉为优先种子。

    口径：任一 analysis mentioned=true 即算"有结果"（多 analysis_run 取并集，宁可不
    回炉也不错杀）。回炉种子 usage_count = 该问法回答数（回答越多、越是优先种子）。
    """
    rows = session.execute(
        _ZERO_MENTION_SQL,
        {
            "tenant_pub_id": tenant_pub_id,
            "project_pub_id": project.pub_id,
            "scan_limit": _ANALYTICS_SCAN_LIMIT,
        },
    ).all()
    stats: dict[str, dict[str, int]] = {}
    for row in rows:
        query_text = row._mapping["query_text"]
        normalized = textutil.normalize_query(str(query_text))
        if not normalized:
            continue
        bucket = stats.setdefault(normalized, {"answers": 0, "mentions": 0})
        bucket["answers"] += 1
        if row._mapping["mentioned"]:
            bucket["mentions"] += 1
    if not stats:
        return
    variants = list(
        session.scalars(
            select(QueryVariant).where(
                QueryVariant.tenant_id == tenant_id, QueryVariant.project_id == project.id
            )
        ).all()
    )
    now = datetime.now(UTC)
    for variant in variants:
        variant_stats = stats.get(variant.normalized)
        if variant_stats is None or variant_stats["answers"] == 0:
            continue
        if variant_stats["mentions"] > 0:
            if not variant.verified:
                variant.verified = True
                summary.verified_marked += 1
            continue
        # 零提及 → 回炉为优先种子（幂等：同 project+normalized 种子累计 usage）。
        existing_seed = session.scalar(
            select(VariantSeed).where(
                VariantSeed.tenant_id == tenant_id,
                VariantSeed.project_id == project.id,
                VariantSeed.normalized == variant.normalized,
            )
        )
        if existing_seed is None:
            session.add(
                VariantSeed(
                    pub_id=pub_id_factory("sed"),
                    tenant_id=tenant_id,
                    project_id=project.id,
                    text=variant.text,
                    normalized=variant.normalized,
                    source_type="recycled_zero_mention",
                    source_ref=variant.pub_id,
                    usage_count=bucket["answers"],
                    last_seen_at=now,
                )
            )
        elif existing_seed.source_type != "recycled_zero_mention":
            existing_seed.source_type = "recycled_zero_mention"
            existing_seed.source_ref = variant.pub_id
            existing_seed.last_seen_at = now
        else:
            existing_seed.last_seen_at = now
        summary.recycled_zero_mention += 1
    session.flush()


# ── 确认门（INV-25）与草稿导出 ──────────────────────────────────────────────


def confirm_variants(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    project: Project,
    variant_pub_ids: list[str],
    decision: str,
) -> dict[str, Any]:
    """批量确认/拒绝：仅 pending 可迁移（状态机 pending → confirmed/rejected）。"""
    rows = list(
        session.scalars(
            select(QueryVariant)
            .where(
                QueryVariant.tenant_id == tenant_id,
                QueryVariant.project_id == project.id,
                QueryVariant.pub_id.in_(variant_pub_ids),
            )
            .with_for_update()
        ).all()
    )
    now = datetime.now(UTC)
    updated = 0
    result_rows: list[dict[str, str]] = []
    for row in rows:
        if row.status == "pending":
            row.status = decision
            if decision == "confirmed":
                row.confirmed_at = now
            updated += 1
        result_rows.append({"pub_id": row.pub_id, "status": row.status})
    session.flush()
    return {
        "decision": decision,
        "updated": updated,
        "skipped": len(rows) - updated,
        "missing": sorted(set(variant_pub_ids) - {row.pub_id for row in rows}),
        "variants": result_rows,
    }


def confirmed_draft(session: Session, *, tenant_id: uuid.UUID, project: Project) -> dict[str, Any]:
    """INV-25 出口：仅 confirmed 变体可组装为 config draft 的 QueryGroup 形状。

    优先级：零提及回炉（recycled_zero_mention）=10，其余=100；同优先级按确认时间。
    """
    rows = list(
        session.scalars(
            select(QueryVariant)
            .where(
                QueryVariant.tenant_id == tenant_id,
                QueryVariant.project_id == project.id,
                QueryVariant.status == "confirmed",
            )
            .order_by(QueryVariant.confirmed_at.asc().nulls_last(), QueryVariant.created_at.asc())
        ).all()
    )
    items = [
        {
            "text": row.text,
            "priority": 10 if row.source_type == "recycled_zero_mention" else 100,
        }
        for row in rows
    ]
    items.sort(key=lambda item: item["priority"])
    return {"name": "W5 数据驱动变体（已确认）", "items": items}
