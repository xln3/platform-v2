#!/usr/bin/env python3
"""Backfill citations from captured DeepSeek/Tongyi/Yuanbao evidence.

The command is dry-run by default and never sends a new question. Resolved
references are persisted while unresolved platform ordinals are reported
explicitly and never assigned a guessed URL.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from geo_platform.collection.models import CollectionRun, CollectionTask  # noqa: E402
from geo_platform.projects.models import (  # noqa: E402
    Brand,
    Competitor,
    MonitoringConfigVersion,
    Project,
)
from geo_platform.tenancy.database import WorkerSessionLocal  # noqa: E402
from geo_platform.tenancy.repository import TenantRepository  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from domain.collection.answer_content import (  # noqa: E402
    extract_answer_citation_anchors,
    project_answer_content,
)
from domain.collection.uvw import legacy_reference_event, occurrence_rows  # noqa: E402
from domain.scoring.analyzer import CitationInput, analyze_answer  # noqa: E402
from scripts.readback_tongyi_citations import _target_ids_from_har  # noqa: E402
from workflows.activities.collection import (  # noqa: E402
    _normalize_citations,
    _normalize_search_queries,
    _persist_uvw_facts,
)
from workflows.activities.deepseek_adapter import _rich_record_from_sse  # noqa: E402
from workflows.activities.yuanbao_adapter import _yuanbao_record_from_sse  # noqa: E402


@dataclass(frozen=True)
class DeepSeekBackfillPlan:
    citations: list[dict[str, Any]]
    search_queries: list[dict[str, Any]]
    retrieval_events: list[dict[str, Any]]
    citation_indexes: list[int]
    unresolved_citation_indexes: list[int]
    candidate_count: int
    occurrence_count: int


@dataclass(frozen=True)
class TongyiBackfillPlan:
    citations: list[dict[str, Any]]
    retrieval_events: list[dict[str, Any]]
    unresolved_source_ordinals: list[int]
    displayed_source_count: int
    occurrence_count: int


@dataclass(frozen=True)
class YuanbaoBackfillPlan:
    citations: list[dict[str, Any]]
    retrieval_events: list[dict[str, Any]]
    citation_indexes: list[int]
    unresolved_citation_indexes: list[int]
    candidate_count: int
    occurrence_count: int


def build_deepseek_plan(raw_sse: str, *, answer_text: str) -> DeepSeekBackfillPlan:
    rich = _rich_record_from_sse(raw_sse)
    if rich is None:
        raise ValueError("DeepSeek SSE cannot be assembled")
    citation_indexes = list(rich.get("citation_indexes") or [])
    unresolved = list(rich.get("unresolved_citation_indexes") or [])
    if not citation_indexes:
        raise ValueError("DeepSeek SSE has no citation markers")
    payloads = [
        {
            "url": str(ref["url"]),
            "title": str(ref["title"]).strip() if ref.get("title") else None,
            "cited_text": None,
            "platform_ordinal": ref["platform_ordinal"],
            "ordinal_base": ref.get("ordinal_base", 1),
        }
        for ref in rich.get("references") or []
        if isinstance(ref, dict)
        and isinstance(ref.get("url"), str)
        and ref["url"].startswith(("http://", "https://"))
        and isinstance(ref.get("platform_ordinal"), int)
        and not isinstance(ref.get("platform_ordinal"), bool)
    ]
    citations = _normalize_citations(payloads, answer_text=answer_text)
    persisted_indexes = [int(row["platform_ordinal"]) for row in citations]
    resolved_indexes = [index for index in citation_indexes if index not in unresolved]
    if resolved_indexes != persisted_indexes:
        raise ValueError(
            "DeepSeek citation coverage mismatch: "
            f"expected_resolved={resolved_indexes}, resolved={persisted_indexes}"
        )
    search_queries = _normalize_search_queries(list(rich.get("search_queries") or []))
    retrieval_events = list(rich.get("retrieval_events") or [])
    candidates = sum(len(event.get("candidates") or []) for event in retrieval_events)
    occurrences = occurrence_rows(retrieval_events)
    final_indexes = sorted(
        int(row.final_reference_ordinal)
        for row in occurrences
        if row.final_reference_ordinal is not None
    )
    if final_indexes != sorted(resolved_indexes):
        raise ValueError(
            "DeepSeek final-reference coverage mismatch: "
            f"resolved={sorted(resolved_indexes)}, final={final_indexes}"
        )
    return DeepSeekBackfillPlan(
        citations=citations,
        search_queries=search_queries,
        retrieval_events=retrieval_events,
        citation_indexes=citation_indexes,
        unresolved_citation_indexes=unresolved,
        candidate_count=candidates,
        occurrence_count=len(occurrences),
    )


def build_tongyi_plan(
    artifact_task: dict[str, Any], *, answer_text: str
) -> TongyiBackfillPlan:
    if artifact_task.get("status") != "completed":
        raise ValueError("Tongyi history readback task is not completed")
    raw_citations = artifact_task.get("citations")
    unresolved = artifact_task.get("unresolved_source_ordinals")
    displayed_count = artifact_task.get("displayed_source_count")
    raw_source_count = artifact_task.get("raw_source_count")
    if not isinstance(raw_citations, list) or not isinstance(unresolved, list):
        raise ValueError("Tongyi history readback source arrays are invalid")
    if (
        not isinstance(displayed_count, int)
        or isinstance(displayed_count, bool)
        or displayed_count < 0
        or not isinstance(raw_source_count, int)
        or isinstance(raw_source_count, bool)
        or raw_source_count < 0
        or displayed_count != raw_source_count
        or artifact_task.get("display_count_matches") is not True
    ):
        raise ValueError("Tongyi displayed source count was not fully reconciled")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 1
        for value in unresolved
    ):
        raise ValueError("Tongyi unresolved source ordinals are invalid")
    citations = _normalize_citations(raw_citations, answer_text=answer_text)
    resolved_ordinals = [int(row["platform_ordinal"]) for row in citations]
    if sorted(resolved_ordinals + unresolved) != list(range(1, raw_source_count + 1)):
        raise ValueError("Tongyi source ordinals do not cover the displayed source panel")
    retrieval_events = legacy_reference_event(citations)
    occurrences = occurrence_rows(retrieval_events)
    if len(occurrences) != len(citations):
        raise ValueError("Tongyi final-reference occurrence coverage mismatch")
    return TongyiBackfillPlan(
        citations=citations,
        retrieval_events=retrieval_events,
        unresolved_source_ordinals=list(unresolved),
        displayed_source_count=displayed_count,
        occurrence_count=len(occurrences),
    )


def build_yuanbao_plan(raw_sse: str, *, answer_text: str) -> YuanbaoBackfillPlan:
    record = _yuanbao_record_from_sse(raw_sse)
    if record is None:
        raise ValueError("Yuanbao SSE has no searchGuid source payload")
    citation_indexes = list(record.get("citation_indexes") or [])
    unresolved = list(record.get("unresolved_citation_indexes") or [])
    if not citation_indexes:
        raise ValueError("Yuanbao SSE has no citation markers")
    payloads = [
        {
            "url": str(ref["url"]),
            "title": str(ref["title"]).strip()[:300] if ref.get("title") else None,
            "cited_text": ref.get("summary"),
            "platform_ordinal": ref["platform_ordinal"],
            "ordinal_base": ref.get("ordinal_base", 1),
        }
        for ref in record.get("references") or []
        if isinstance(ref, dict)
        and isinstance(ref.get("url"), str)
        and ref["url"].startswith(("http://", "https://"))
        and isinstance(ref.get("platform_ordinal"), int)
        and not isinstance(ref.get("platform_ordinal"), bool)
    ]
    citations = _normalize_citations(payloads, answer_text=answer_text)
    resolved_indexes = [index for index in citation_indexes if index not in unresolved]
    if [int(row["platform_ordinal"]) for row in citations] != resolved_indexes:
        raise ValueError("Yuanbao resolved citation coverage mismatch")
    retrieval_events = list(record.get("retrieval_events") or [])
    occurrences = occurrence_rows(retrieval_events)
    final_indexes = sorted(
        int(row.final_reference_ordinal)
        for row in occurrences
        if row.final_reference_ordinal is not None
    )
    if final_indexes != sorted(resolved_indexes):
        raise ValueError("Yuanbao final-reference occurrence coverage mismatch")
    return YuanbaoBackfillPlan(
        citations=citations,
        retrieval_events=retrieval_events,
        citation_indexes=citation_indexes,
        unresolved_citation_indexes=unresolved,
        candidate_count=int(record.get("candidate_count") or 0),
        occurrence_count=len(occurrences),
    )


def _raw_sse_path(task: CollectionTask) -> Path:
    try:
        evidence = json.loads(task.evidence_json or "[]")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{task.pub_id}: malformed evidence_json") from exc
    paths = [
        Path(str(item.get("path")))
        for item in evidence
        if isinstance(item, dict)
        and item.get("kind") == "sse_raw"
        and item.get("relation_type") == "answer_sse_raw"
        and item.get("path")
    ]
    if len(paths) != 1 or not paths[0].is_file():
        raise ValueError(f"{task.pub_id}: expected exactly one readable sse_raw evidence")
    return paths[0]


def _answer_har_path(task: CollectionTask) -> Path:
    try:
        evidence = json.loads(task.evidence_json or "[]")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{task.pub_id}: malformed evidence_json") from exc
    paths = [
        Path(str(item.get("path")))
        for item in evidence
        if isinstance(item, dict)
        and item.get("kind") == "har"
        and item.get("relation_type") == "answer_har"
        and item.get("path")
    ]
    if len(paths) != 1 or not paths[0].is_file():
        raise ValueError(f"{task.pub_id}: expected exactly one readable answer HAR")
    return paths[0]


def _project_task(task: CollectionTask, citations: list[dict[str, Any]]) -> None:
    projected = project_answer_content(task.answer_text or "", citations)
    task.citations_json = json.dumps(
        citations, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    task.response_markdown_normalized = projected.response_markdown_normalized
    task.response_ast_json = json.dumps(
        projected.response_ast,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    task.response_html_sanitized = projected.response_html_sanitized
    task.response_plain_text = projected.response_plain_text
    task.response_hash = projected.response_hash
    task.render_parser_version = projected.render_parser_version


def _update_retrieval_event_and_sources(
    session: Any,
    *,
    run: CollectionRun,
    project: Project,
    task: CollectionTask,
    plan: DeepSeekBackfillPlan | TongyiBackfillPlan | YuanbaoBackfillPlan,
) -> None:
    existing = list(
        session.execute(
            text(
                """
                SELECT ordinal,evidence_pub_id
                FROM platform.answer_retrieval_event
                WHERE answer_task_id=:task_id ORDER BY ordinal
                """
            ),
            {"task_id": task.id},
        ).mappings()
    )
    expected_ordinals = [int(event["ordinal"]) for event in plan.retrieval_events]
    if [int(row["ordinal"]) for row in existing] != expected_ordinals:
        raise ValueError(
            f"{task.pub_id}: retrieval event ordinals differ: "
            f"existing={[row['ordinal'] for row in existing]}, expected={expected_ordinals}"
        )
    evidence_ids: dict[str, str] = {}
    for event, persisted in zip(plan.retrieval_events, existing, strict=True):
        relation = str(event.get("evidence_relation") or "")
        evidence_pub_id = persisted["evidence_pub_id"]
        if relation:
            related_assets = list(
                session.execute(
                    text(
                        """
                        SELECT to_pub_id FROM evidence.evidence_relation
                        WHERE from_pub_id=:task_pub_id
                          AND relation_type=:relation_type
                        ORDER BY to_pub_id
                        """
                    ),
                    {
                        "task_pub_id": task.pub_id,
                        "relation_type": relation,
                    },
                ).scalars()
            )
            if len(related_assets) == 1:
                evidence_pub_id = str(related_assets[0])
            elif len(related_assets) > 1:
                raise ValueError(
                    f"{task.pub_id}: evidence relation {relation!r} is ambiguous"
                )
        if relation and evidence_pub_id:
            prior = evidence_ids.setdefault(relation, str(evidence_pub_id))
            if prior != str(evidence_pub_id):
                raise ValueError(f"{task.pub_id}: one evidence relation maps to multiple assets")
        session.execute(
            text(
                """
                UPDATE platform.answer_retrieval_event
                SET queries=CAST(:queries AS jsonb),
                    u_observation=:u_observation,
                    v_observation=:v_observation,
                    final_reference_observation=:final_observation,
                    evidence_pub_id=:evidence_pub_id
                WHERE answer_task_id=:task_id AND ordinal=:ordinal
                """
            ),
            {
                "task_id": task.id,
                "ordinal": event["ordinal"],
                "queries": json.dumps(event["queries"], ensure_ascii=False, separators=(",", ":")),
                "u_observation": event["u_observation"],
                "v_observation": event["v_observation"],
                "final_observation": event["final_reference_observation"],
                "evidence_pub_id": evidence_pub_id,
            },
        )
    _persist_uvw_facts(
        session=session,
        run=run,
        project=project,
        task=task,
        retrieval_events=plan.retrieval_events,
        evidence_ids_by_relation=evidence_ids,
    )


def _stable_pub_id(prefix: str, value: str) -> str:
    return f"{prefix}_{sha256(value.encode()).hexdigest()[:26]}"


def _analytics_backfill(
    session: Any,
    *,
    tenant_pub_id: str,
    project: Project,
    task: CollectionTask,
    citations: list[dict[str, Any]],
    brand: Brand,
    competitors: tuple[str, ...],
) -> None:
    answer = session.execute(
        text(
            """
            SELECT pub_id,response_raw FROM analytics.answer
            WHERE tenant_pub_id=:tenant_pub_id AND pub_id=:answer_pub_id
            """
        ),
        {"tenant_pub_id": tenant_pub_id, "answer_pub_id": task.pub_id},
    ).mappings().one()
    analyses = list(
        session.execute(
            text(
                """
                SELECT aa.analysis_run_pub_id,ar.input_hash
                FROM analytics.answer_analysis aa
                JOIN analytics.analysis_run ar ON ar.pub_id=aa.analysis_run_pub_id
                WHERE aa.tenant_pub_id=:tenant_pub_id AND aa.answer_pub_id=:answer_pub_id
                """
            ),
            {"tenant_pub_id": tenant_pub_id, "answer_pub_id": task.pub_id},
        ).mappings()
    )
    if len(analyses) != 1:
        raise ValueError(f"{task.pub_id}: expected one existing analytics analysis")
    analysis_run_pub_id = str(analyses[0]["analysis_run_pub_id"])
    analysis_ref_count = session.execute(
        text(
            """
            SELECT count(*) FROM analytics.answer_analysis
            WHERE tenant_pub_id=:tenant_pub_id AND analysis_run_pub_id=:analysis_run_pub_id
            """
        ),
        {"tenant_pub_id": tenant_pub_id, "analysis_run_pub_id": analysis_run_pub_id},
    ).scalar_one()
    if int(analysis_ref_count) != 1:
        raise ValueError(f"{task.pub_id}: analytics analysis_run is shared")
    own_domains: tuple[str, ...] = ()
    if brand.website:
        host = (urlsplit(brand.website).hostname or "").lower()
        own_domains = (host,) if host else ()
    inputs = tuple(
        CitationInput(
            url=str(row["url"]),
            title=row.get("title"),
            cited_text=row.get("cited_text"),
            ordinal=int(row["ordinal"]),
            platform_ordinal=int(row["platform_ordinal"]),
            ordinal_base=int(row["ordinal_base"]),
        )
        for row in citations
    )
    baseline = analyze_answer(
        answer_pub_id=task.pub_id,
        text=str(answer["response_raw"]),
        brand=brand.name,
        competitors=competitors,
        citations=(),
        dimensions={},
        own_domains=own_domains,
    )
    analyzed = analyze_answer(
        answer_pub_id=task.pub_id,
        text=str(answer["response_raw"]),
        brand=brand.name,
        competitors=competitors,
        citations=inputs,
        dimensions={},
        own_domains=own_domains,
    )
    current_input_hash = str(analyses[0]["input_hash"])
    if current_input_hash not in {baseline.input_hash, analyzed.input_hash}:
        raise ValueError(f"{task.pub_id}: existing analytics input hash cannot be reproduced")
    session.execute(
        text(
            """
            UPDATE analytics.analysis_run SET input_hash=:input_hash,updated_at=now()
            WHERE tenant_pub_id=:tenant_pub_id AND pub_id=:analysis_run_pub_id
            """
        ),
        {
            "input_hash": analyzed.input_hash,
            "tenant_pub_id": tenant_pub_id,
            "analysis_run_pub_id": analysis_run_pub_id,
        },
    )
    anchors = extract_answer_citation_anchors(str(answer["response_raw"]), list(analyzed.citations))
    for citation in analyzed.citations:
        ordinal = int(citation["ordinal"])
        citation_pub_id = _stable_pub_id(
            "cit", f"{task.pub_id}|{analysis_run_pub_id}|{ordinal}"
        )
        session.execute(
            text(
                """
                INSERT INTO analytics.citation_fact
                  (pub_id,tenant_pub_id,answer_pub_id,analysis_run_pub_id,ordinal,
                   platform_ordinal,ordinal_base,original_url,canonical_url,host,title,
                   cited_text,own_source,content_hash,published_at_confidence,
                   published_at_candidates)
                VALUES
                  (:pub_id,:tenant_pub_id,:answer_pub_id,:analysis_run_pub_id,:ordinal,
                   :platform_ordinal,:ordinal_base,:original_url,:canonical_url,:host,:title,
                   :cited_text,:own_source,:content_hash,'unknown','[]'::jsonb)
                ON CONFLICT (tenant_pub_id,answer_pub_id,ordinal,analysis_run_pub_id)
                DO UPDATE SET
                  platform_ordinal=EXCLUDED.platform_ordinal,
                  ordinal_base=EXCLUDED.ordinal_base,
                  original_url=EXCLUDED.original_url,
                  canonical_url=EXCLUDED.canonical_url,
                  host=EXCLUDED.host,title=EXCLUDED.title,cited_text=EXCLUDED.cited_text,
                  own_source=EXCLUDED.own_source,content_hash=EXCLUDED.content_hash
                """
            ),
            {
                "pub_id": citation_pub_id,
                "tenant_pub_id": tenant_pub_id,
                "answer_pub_id": task.pub_id,
                "analysis_run_pub_id": analysis_run_pub_id,
                "ordinal": ordinal,
                "platform_ordinal": citation["platform_ordinal"],
                "ordinal_base": citation["ordinal_base"],
                "original_url": citation["original_url"],
                "canonical_url": citation["canonical_url"],
                "host": citation["host"],
                "title": citation["title"],
                "cited_text": citation["cited_text"],
                "own_source": citation["own_source"],
                "content_hash": None,
            },
        )
        anchor = anchors[ordinal]
        relation_pub_id = _stable_pub_id("acr", f"{tenant_pub_id}|{task.pub_id}|{ordinal}")
        session.execute(
            text(
                """
                INSERT INTO analytics.answer_citation_relation
                  (pub_id,tenant_pub_id,answer_pub_id,citation_pub_id,ordinal,
                   mapping_status,mapping_basis,answer_text_start,answer_text_end,
                   answer_ast_path,answer_sentence,source_match_status,relation,
                   classifier_version,review_status,first_cited_at,last_cited_at)
                VALUES
                  (:pub_id,:tenant_pub_id,:answer_pub_id,:citation_pub_id,:ordinal,
                   :mapping_status,:mapping_basis,:answer_text_start,:answer_text_end,
                   CAST(:answer_ast_path AS jsonb),:answer_sentence,'not_checked','unverified',
                   'answer-marker-v1','unreviewed',:cited_at,:cited_at)
                ON CONFLICT (tenant_pub_id,answer_pub_id,ordinal) DO UPDATE SET
                  citation_pub_id=EXCLUDED.citation_pub_id,
                  mapping_status=EXCLUDED.mapping_status,mapping_basis=EXCLUDED.mapping_basis,
                  answer_text_start=EXCLUDED.answer_text_start,
                  answer_text_end=EXCLUDED.answer_text_end,
                  answer_ast_path=EXCLUDED.answer_ast_path,
                  answer_sentence=EXCLUDED.answer_sentence,updated_at=now()
                """
            ),
            {
                "pub_id": relation_pub_id,
                "tenant_pub_id": tenant_pub_id,
                "answer_pub_id": task.pub_id,
                "citation_pub_id": citation_pub_id,
                "ordinal": ordinal,
                "mapping_status": anchor["mapping_status"],
                "mapping_basis": anchor["mapping_basis"],
                "answer_text_start": anchor["answer_text_start"],
                "answer_text_end": anchor["answer_text_end"],
                "answer_ast_path": (
                    json.dumps(anchor["answer_ast_path"])
                    if anchor["answer_ast_path"] is not None
                    else None
                ),
                "answer_sentence": anchor["answer_sentence"],
                "cited_at": task.created_at,
            },
        )
    projected = project_answer_content(str(answer["response_raw"]), list(analyzed.citations))
    session.execute(
        text(
            """
            UPDATE analytics.answer
            SET response_text=:markdown,response_markdown_normalized=:markdown,
                response_ast=CAST(:ast AS jsonb),response_html_sanitized=:html,
                response_plain_text=:plain,response_hash=:hash,
                render_parser_version=:parser
            WHERE tenant_pub_id=:tenant_pub_id AND pub_id=:answer_pub_id
            """
        ),
        {
            "markdown": projected.response_markdown_normalized,
            "ast": json.dumps(projected.response_ast, ensure_ascii=False, separators=(",", ":")),
            "html": projected.response_html_sanitized,
            "plain": projected.response_plain_text,
            "hash": projected.response_hash,
            "parser": projected.render_parser_version,
            "tenant_pub_id": tenant_pub_id,
            "answer_pub_id": task.pub_id,
        },
    )
    updated_metrics = session.execute(
        text(
            """
            UPDATE analytics.metric_trace
            SET contribution=jsonb_build_object(
                  'numerator',1,'denominator',1,'value_sum',NULL),
                numerator=1,denominator=1,value_sum=NULL
            WHERE tenant_pub_id=:tenant_pub_id AND answer_pub_id=:answer_pub_id
              AND metric_name='citation_coverage'
            """
        ),
        {"tenant_pub_id": tenant_pub_id, "answer_pub_id": task.pub_id},
    ).rowcount
    if updated_metrics != 1:
        raise ValueError(f"{task.pub_id}: expected one citation_coverage metric trace")


def _refresh_citation_metric_daily(session: Any, tenant_pub_id: str, project_pub_id: str) -> None:
    session.execute(
        text(
            """
            WITH rollup AS (
              SELECT tenant_pub_id,project_pub_id,metric_date,metric_name,dimensions_hash,
                     metric_version,scorer_version,
                     sum(numerator)::bigint AS numerator,
                     sum(denominator)::bigint AS denominator,
                     bool_or(state='experimental') AS experimental
              FROM analytics.metric_trace
              WHERE tenant_pub_id=:tenant_pub_id AND project_pub_id=:project_pub_id
                AND metric_name='citation_coverage'
              GROUP BY tenant_pub_id,project_pub_id,metric_date,metric_name,dimensions_hash,
                       metric_version,scorer_version
            )
            UPDATE analytics.metric_daily daily
            SET numerator=rollup.numerator,denominator=rollup.denominator,
                value=CASE WHEN rollup.denominator>0
                           THEN rollup.numerator::numeric/rollup.denominator ELSE NULL END,
                state=CASE WHEN rollup.experimental THEN 'experimental' ELSE 'ready' END,
                updated_at=now()
            FROM rollup
            WHERE daily.tenant_pub_id=rollup.tenant_pub_id
              AND daily.project_pub_id=rollup.project_pub_id
              AND daily.metric_date=rollup.metric_date
              AND daily.metric_name=rollup.metric_name
              AND daily.dimensions_hash=rollup.dimensions_hash
              AND daily.metric_version=rollup.metric_version
              AND daily.scorer_version=rollup.scorer_version
            """
        ),
        {"tenant_pub_id": tenant_pub_id, "project_pub_id": project_pub_id},
    )


def run(*, tenant_pub_id: str, config_pub_id: str, apply: bool) -> dict[str, Any]:
    with WorkerSessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        config = session.scalar(
            select(MonitoringConfigVersion).where(MonitoringConfigVersion.pub_id == config_pub_id)
        )
        if config is None:
            raise ValueError("config version not found")
        runs = list(
            session.scalars(
                select(CollectionRun).where(CollectionRun.config_version_id == config.id)
            )
        )
        if not runs:
            raise ValueError("config version has no collection runs")
        project = session.get(Project, runs[0].project_id)
        if project is None or any(run.project_id != project.id for run in runs):
            raise ValueError("config version project scope is invalid")
        brands = list(session.scalars(select(Brand).where(Brand.project_id == project.id)))
        if len(brands) != 1:
            raise ValueError("project must have exactly one brand")
        competitors = tuple(
            session.scalars(
                select(Competitor.name)
                .where(Competitor.project_id == project.id)
                .order_by(Competitor.created_at, Competitor.pub_id)
            )
        )
        run_by_id = {run.id: run for run in runs}
        tasks = list(
            session.scalars(
                select(CollectionTask)
                .where(
                    CollectionTask.run_id.in_(run_by_id),
                    CollectionTask.state == "completed",
                )
                .order_by(CollectionTask.created_at, CollectionTask.pub_id)
            )
        )
        if not tasks:
            raise ValueError("config version has no completed tasks")
        plans: list[tuple[CollectionTask, DeepSeekBackfillPlan]] = []
        for task in tasks:
            path = _raw_sse_path(task)
            plan = build_deepseek_plan(
                path.read_text(encoding="utf-8"), answer_text=task.answer_text or ""
            )
            current = json.loads(task.citations_json or "[]")
            if current not in ([], plan.citations):
                raise ValueError(f"{task.pub_id}: existing citations differ from backfill plan")
            plans.append((task, plan))
        unresolved_by_task = {
            task.pub_id: plan.unresolved_citation_indexes
            for task, plan in plans
            if plan.unresolved_citation_indexes
        }
        summary = {
            "mode": "apply" if apply else "dry-run",
            "tenant_pub_id": tenant_pub_id,
            "project_pub_id": project.pub_id,
            "config_version_pub_id": config_pub_id,
            "tasks": len(plans),
            "citations": sum(len(plan.citations) for _, plan in plans),
            "search_candidates": sum(plan.candidate_count for _, plan in plans),
            "source_occurrences": sum(plan.occurrence_count for _, plan in plans),
            "unresolved_citation_indexes": sum(
                len(indexes) for indexes in unresolved_by_task.values()
            ),
            "unresolved_by_task": unresolved_by_task,
        }
        if not apply:
            session.rollback()
            return summary
        for task, plan in plans:
            task.search_queries_json = json.dumps(
                plan.search_queries,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            _project_task(task, plan.citations)
            _update_retrieval_event_and_sources(
                session,
                run=run_by_id[task.run_id],
                project=project,
                task=task,
                plan=plan,
            )
            if plan.citations:
                _analytics_backfill(
                    session,
                    tenant_pub_id=tenant_pub_id,
                    project=project,
                    task=task,
                    citations=plan.citations,
                    brand=brands[0],
                    competitors=competitors,
                )
        _refresh_citation_metric_daily(session, tenant_pub_id, project.pub_id)
        session.commit()
        return summary


def run_yuanbao(*, tenant_pub_id: str, config_pub_id: str, apply: bool) -> dict[str, Any]:
    with WorkerSessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        config = session.scalar(
            select(MonitoringConfigVersion).where(
                MonitoringConfigVersion.pub_id == config_pub_id
            )
        )
        if config is None:
            raise ValueError("config version not found")
        runs = list(
            session.scalars(
                select(CollectionRun).where(CollectionRun.config_version_id == config.id)
            )
        )
        if not runs:
            raise ValueError("config version has no collection runs")
        project = session.get(Project, runs[0].project_id)
        if project is None or any(run.project_id != project.id for run in runs):
            raise ValueError("config version project scope is invalid")
        brands = list(session.scalars(select(Brand).where(Brand.project_id == project.id)))
        if len(brands) != 1:
            raise ValueError("project must have exactly one brand")
        competitors = tuple(
            session.scalars(
                select(Competitor.name)
                .where(Competitor.project_id == project.id)
                .order_by(Competitor.created_at, Competitor.pub_id)
            )
        )
        run_by_id = {run.id: run for run in runs}
        tasks = list(
            session.scalars(
                select(CollectionTask)
                .where(
                    CollectionTask.run_id.in_(run_by_id),
                    CollectionTask.state == "completed",
                )
                .order_by(CollectionTask.created_at, CollectionTask.pub_id)
            )
        )
        if not tasks:
            raise ValueError("config version has no completed tasks")
        plans: list[tuple[CollectionTask, YuanbaoBackfillPlan]] = []
        for task in tasks:
            path = _raw_sse_path(task)
            plan = build_yuanbao_plan(
                path.read_text(encoding="utf-8"), answer_text=task.answer_text or ""
            )
            current = json.loads(task.citations_json or "[]")
            if current not in ([], plan.citations):
                raise ValueError(f"{task.pub_id}: existing citations differ from backfill plan")
            plans.append((task, plan))
        unresolved_by_task = {
            task.pub_id: plan.unresolved_citation_indexes
            for task, plan in plans
            if plan.unresolved_citation_indexes
        }
        summary = {
            "mode": "apply" if apply else "dry-run",
            "platform": "yuanbao",
            "tenant_pub_id": tenant_pub_id,
            "project_pub_id": project.pub_id,
            "config_version_pub_id": config_pub_id,
            "tasks": len(plans),
            "citations": sum(len(plan.citations) for _, plan in plans),
            "search_candidates": sum(plan.candidate_count for _, plan in plans),
            "source_occurrences": sum(plan.occurrence_count for _, plan in plans),
            "unresolved_citation_indexes": sum(
                len(indexes) for indexes in unresolved_by_task.values()
            ),
            "unresolved_by_task": unresolved_by_task,
        }
        if not apply:
            session.rollback()
            return summary
        for task, plan in plans:
            _project_task(task, plan.citations)
            _update_retrieval_event_and_sources(
                session,
                run=run_by_id[task.run_id],
                project=project,
                task=task,
                plan=plan,
            )
            if plan.citations:
                _analytics_backfill(
                    session,
                    tenant_pub_id=tenant_pub_id,
                    project=project,
                    task=task,
                    citations=plan.citations,
                    brand=brands[0],
                    competitors=competitors,
                )
        _refresh_citation_metric_daily(session, tenant_pub_id, project.pub_id)
        session.commit()
        return summary


def run_tongyi(
    *, tenant_pub_id: str, config_pub_id: str, artifact_path: Path, apply: bool
) -> dict[str, Any]:
    artifact_bytes = artifact_path.read_bytes()
    artifact = json.loads(artifact_bytes)
    if not isinstance(artifact, dict):
        raise ValueError("Tongyi readback artifact is not an object")
    expected_header = {
        "schema_version": "tongyi-history-citations-v1",
        "tenant_pub_id": tenant_pub_id,
        "config_version_pub_id": config_pub_id,
        "read_only": True,
    }
    if any(artifact.get(key) != value for key, value in expected_header.items()):
        raise ValueError("Tongyi readback artifact scope or schema differs")
    artifact_tasks = artifact.get("tasks")
    if not isinstance(artifact_tasks, list):
        raise ValueError("Tongyi readback artifact task list is invalid")
    rows_by_task = {
        row.get("task_pub_id"): row
        for row in artifact_tasks
        if isinstance(row, dict) and isinstance(row.get("task_pub_id"), str)
    }
    if len(rows_by_task) != len(artifact_tasks):
        raise ValueError("Tongyi readback artifact has duplicate or invalid tasks")

    with WorkerSessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        config = session.scalar(
            select(MonitoringConfigVersion).where(
                MonitoringConfigVersion.pub_id == config_pub_id
            )
        )
        if config is None:
            raise ValueError("config version not found")
        runs = list(
            session.scalars(
                select(CollectionRun).where(CollectionRun.config_version_id == config.id)
            )
        )
        if not runs:
            raise ValueError("config version has no collection runs")
        project = session.get(Project, runs[0].project_id)
        if project is None or any(run.project_id != project.id for run in runs):
            raise ValueError("config version project scope is invalid")
        brands = list(session.scalars(select(Brand).where(Brand.project_id == project.id)))
        if len(brands) != 1:
            raise ValueError("project must have exactly one brand")
        competitors = tuple(
            session.scalars(
                select(Competitor.name)
                .where(Competitor.project_id == project.id)
                .order_by(Competitor.created_at, Competitor.pub_id)
            )
        )
        run_by_id = {run.id: run for run in runs}
        tasks = list(
            session.scalars(
                select(CollectionTask)
                .where(
                    CollectionTask.run_id.in_(run_by_id),
                    CollectionTask.state == "completed",
                )
                .order_by(CollectionTask.created_at, CollectionTask.pub_id)
            )
        )
        if not tasks:
            raise ValueError("config version has no completed tasks")
        if set(rows_by_task) != {task.pub_id for task in tasks}:
            raise ValueError("Tongyi readback artifact does not cover exactly the completed tasks")

        plans: list[tuple[CollectionTask, TongyiBackfillPlan]] = []
        for task in tasks:
            row = rows_by_task[task.pub_id]
            matrix = json.loads(task.matrix_json or "{}")
            query = matrix.get("query") if isinstance(matrix, dict) else None
            if (
                not isinstance(query, str)
                or sha256(query.strip().encode()).hexdigest() != row.get("query_sha256")
            ):
                raise ValueError(f"{task.pub_id}: artifact query hash differs")
            har_path = _answer_har_path(task)
            if Path(str(row.get("har_path") or "")).resolve() != har_path.resolve():
                raise ValueError(f"{task.pub_id}: artifact HAR path differs")
            session_id, request_id = _target_ids_from_har(har_path)
            if session_id != row.get("session_id") or request_id != row.get("request_id"):
                raise ValueError(f"{task.pub_id}: artifact session/request identity differs")
            plan = build_tongyi_plan(row, answer_text=task.answer_text or "")
            current = json.loads(task.citations_json or "[]")
            if current not in ([], plan.citations):
                raise ValueError(f"{task.pub_id}: existing citations differ from backfill plan")
            plans.append((task, plan))

        unresolved_by_task = {
            task.pub_id: plan.unresolved_source_ordinals
            for task, plan in plans
            if plan.unresolved_source_ordinals
        }
        summary = {
            "mode": "apply" if apply else "dry-run",
            "platform": "tongyi",
            "tenant_pub_id": tenant_pub_id,
            "project_pub_id": project.pub_id,
            "config_version_pub_id": config_pub_id,
            "artifact_path": str(artifact_path),
            "artifact_sha256": sha256(artifact_bytes).hexdigest(),
            "tasks": len(plans),
            "citations": sum(len(plan.citations) for _, plan in plans),
            "source_occurrences": sum(plan.occurrence_count for _, plan in plans),
            "unresolved_source_ordinals": sum(
                len(indexes) for indexes in unresolved_by_task.values()
            ),
            "unresolved_by_task": unresolved_by_task,
        }
        if not apply:
            session.rollback()
            return summary

        for task, plan in plans:
            _project_task(task, plan.citations)
            _update_retrieval_event_and_sources(
                session,
                run=run_by_id[task.run_id],
                project=project,
                task=task,
                plan=plan,
            )
            if plan.citations:
                _analytics_backfill(
                    session,
                    tenant_pub_id=tenant_pub_id,
                    project=project,
                    task=task,
                    citations=plan.citations,
                    brand=brands[0],
                    competitors=competitors,
                )
        _refresh_citation_metric_daily(session, tenant_pub_id, project.pub_id)
        session.commit()
        return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant-pub-id", required=True)
    parser.add_argument("--config-version-pub-id", required=True)
    parser.add_argument(
        "--platform",
        choices=("deepseek", "tongyi", "yuanbao"),
        default="deepseek",
    )
    parser.add_argument(
        "--artifact-path",
        type=Path,
        help="required for Tongyi history-readback backfill",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="commit the correction; omitted means a read-only dry run",
    )
    args = parser.parse_args()
    if args.platform == "tongyi" and args.artifact_path is None:
        parser.error("--artifact-path is required when --platform=tongyi")
    try:
        if args.platform == "tongyi":
            summary = run_tongyi(
                tenant_pub_id=args.tenant_pub_id,
                config_pub_id=args.config_version_pub_id,
                artifact_path=args.artifact_path,
                apply=args.apply,
            )
        elif args.platform == "yuanbao":
            summary = run_yuanbao(
                tenant_pub_id=args.tenant_pub_id,
                config_pub_id=args.config_version_pub_id,
                apply=args.apply,
            )
        else:
            summary = run(
                tenant_pub_id=args.tenant_pub_id,
                config_pub_id=args.config_version_pub_id,
                apply=args.apply,
            )
    except Exception as exc:
        print(f"backfill failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
