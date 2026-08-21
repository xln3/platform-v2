"""Persist deterministic, versioned UVW cohort facts for service 5."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import psycopg
from geo_platform.config import get_settings
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from psycopg.rows import dict_row
from temporalio import activity
from temporalio.exceptions import ApplicationError

from domain.source_analysis.content_contribution import POLICY_VERSION as W_POLICY_VERSION
from domain.source_analysis.content_strategy import (
    ALGORITHM_VERSION,
    POLICY_VERSION,
    ContentStrategySignal,
    build_content_strategy,
)
from workflows.activities.source_audit import _MinioSourceTextStore


@dataclass(frozen=True)
class ContentStrategyInput:
    tenant_pub_id: str
    project_pub_id: str
    run_pub_id: str
    policy_version: str = POLICY_VERSION
    content_contribution_policy_version: str = W_POLICY_VERSION


@dataclass(frozen=True)
class ContentStrategyResult:
    analysis_pub_id: str
    status: str
    u_occurrences: int
    snapshot_available: int
    recommendations: int


def _dsn() -> str:
    settings = get_settings()
    return os.getenv("S02_POSTGRES_DSN") or (
        settings.worker_postgres_dsn or settings.postgres_dsn
    ).replace("postgresql+psycopg://", "postgresql://")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_subjects(
    dsn: str, item: ContentStrategyInput
) -> tuple[str, str, str, list[dict[str, Any]]]:
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        tenant = connection.execute(
            "SELECT id FROM platform.tenant WHERE pub_id=%s", (item.tenant_pub_id,)
        ).fetchone()
        if tenant is None:
            raise ApplicationError("tenant not found", type="tenant_not_found", non_retryable=True)
        tenant_id = str(tenant["id"])
        connection.execute(
            "SELECT set_config('app.tenant_id',%s,true),set_config('app.tenant_pub_id',%s,true)",
            (tenant_id, item.tenant_pub_id),
        )
        run = connection.execute(
            """
            SELECT run.id,run.project_id
            FROM platform.collection_run run
            JOIN platform.project project ON project.id=run.project_id
            WHERE run.pub_id=%s AND project.pub_id=%s
            """,
            (item.run_pub_id, item.project_pub_id),
        ).fetchone()
        if run is None:
            raise ApplicationError(
                "collection run not found",
                type="collection_run_not_found",
                non_retryable=True,
            )
        rows = connection.execute(
            """
            SELECT occurrence.pub_id AS occurrence_pub_id,
                   occurrence.u_state,occurrence.v_state,occurrence.w_state,
                   snapshot.pub_id AS snapshot_pub_id,
                   snapshot.body_object_key AS text_cas_key,snapshot.text_sha256,
                   weighted.pub_id AS w_analysis_pub_id,
                   weighted.result_state AS w_analysis_state,weighted.w_score,
                   weighted.w_review_facts
            FROM platform.answer_source_occurrence occurrence
            LEFT JOIN LATERAL (
              SELECT candidate.*
              FROM platform.source_page_snapshot candidate
              WHERE candidate.source_url_id=occurrence.source_url_id
                AND candidate.project_id=occurrence.project_id
                AND candidate.snapshot_state='succeeded'
                AND candidate.body_object_key IS NOT NULL
                AND candidate.text_sha256 IS NOT NULL
              ORDER BY candidate.captured_at DESC,candidate.pub_id DESC
              LIMIT 1
            ) snapshot ON true
            LEFT JOIN LATERAL (
              SELECT analysis.pub_id,analysis.result_state,
                     (SELECT max(chunk.contribution_score)
                      FROM platform.weighted_content_chunk chunk
                      WHERE chunk.analysis_id=analysis.id
                        AND (
                          (chunk.verification_state='exact'
                           AND chunk.review_state<>'rejected')
                          OR chunk.review_state='accepted'
                        )) AS w_score,
                     (SELECT COALESCE(jsonb_agg(jsonb_build_object(
                               'chunk_pub_id',reviewed_chunk.pub_id,
                               'review_state',reviewed_chunk.review_state,
                               'latest_review_pub_id',COALESCE((
                                 SELECT review.pub_id
                                 FROM platform.weighted_content_chunk_review review
                                 WHERE review.chunk_id=reviewed_chunk.id
                                 ORDER BY review.reviewed_at DESC,review.pub_id DESC
                                 LIMIT 1
                               ),'')
                             ) ORDER BY reviewed_chunk.ordinal,reviewed_chunk.pub_id),'[]'::jsonb)
                      FROM platform.weighted_content_chunk reviewed_chunk
                      WHERE reviewed_chunk.analysis_id=analysis.id) AS w_review_facts
              FROM platform.content_contribution_analysis analysis
              WHERE analysis.occurrence_id=occurrence.id
                AND analysis.snapshot_id=snapshot.id
                AND analysis.policy_version=%s
              ORDER BY analysis.created_at DESC,analysis.pub_id DESC
              LIMIT 1
            ) weighted ON true
            WHERE occurrence.run_id=%s
            ORDER BY occurrence.answer_task_id,occurrence.occurrence_ordinal,occurrence.pub_id
            """,
            (item.content_contribution_policy_version, run["id"]),
        ).fetchall()
    return tenant_id, str(run["project_id"]), str(run["id"]), [dict(row) for row in rows]


def _signals(
    rows: list[dict[str, Any]], *, text_store: _MinioSourceTextStore
) -> list[ContentStrategySignal]:
    cache: dict[str, str] = {}
    signals: list[ContentStrategySignal] = []
    for row in rows:
        snapshot_pub_id = str(row["snapshot_pub_id"] or "")
        source_text: str | None = None
        if snapshot_pub_id:
            if snapshot_pub_id not in cache:
                source_text = text_store.get_text(str(row["text_cas_key"]), str(row["text_sha256"]))
                if sha256(source_text.encode()).hexdigest() != row["text_sha256"]:
                    raise ValueError("source_hash_mismatch")
                cache[snapshot_pub_id] = source_text
            source_text = cache[snapshot_pub_id]
        w_score = float(row["w_score"]) if row["w_score"] is not None else None
        analysis_state = str(row.get("w_analysis_state") or "")
        # Positive and negative W results both come from the frozen analysis
        # version. The mutable occurrence summary is never reused as evidence
        # for a different policy or page snapshot.
        w_state = (
            "confirmed"
            if analysis_state == "confirmed" and w_score is not None
            else "no_evidence"
            if analysis_state in {"confirmed", "no_evidence"}
            else "pending"
            if str(row["v_state"]) == "entered" and row.get("snapshot_pub_id")
            else "unobserved"
        )
        signals.append(
            ContentStrategySignal(
                occurrence_pub_id=str(row["occurrence_pub_id"]),
                u_state=str(row["u_state"]),  # type: ignore[arg-type]
                v_state=str(row["v_state"]),  # type: ignore[arg-type]
                w_state=w_state,  # type: ignore[arg-type]
                w_score=w_score,
                source_text=source_text,
            )
        )
    return signals


def _input_hash(rows: list[dict[str, Any]], item: ContentStrategyInput) -> str:
    payload = {
        "policy_version": item.policy_version,
        "algorithm_version": ALGORITHM_VERSION,
        "content_contribution_policy_version": item.content_contribution_policy_version,
        "occurrences": [
            {
                "occurrence_pub_id": str(row["occurrence_pub_id"]),
                "u_state": str(row["u_state"]),
                "v_state": str(row["v_state"]),
                "w_state": str(row["w_state"]),
                "w_score": float(row["w_score"]) if row["w_score"] is not None else None,
                "w_analysis_pub_id": str(row.get("w_analysis_pub_id") or ""),
                "w_analysis_state": str(row.get("w_analysis_state") or ""),
                "w_review_facts": row.get("w_review_facts") or [],
                "snapshot_pub_id": str(row["snapshot_pub_id"] or ""),
                "text_sha256": str(row["text_sha256"] or ""),
            }
            for row in rows
        ],
    }
    return sha256(_canonical_json(payload).encode()).hexdigest()


def execute_content_strategy(
    item: ContentStrategyInput,
    *,
    dsn: str,
    text_store: _MinioSourceTextStore,
) -> ContentStrategyResult:
    tenant_id, project_id, run_id, rows = _load_subjects(dsn, item)
    analysis = build_content_strategy(_signals(rows, text_store=text_store))
    input_hash = _input_hash(rows, item)
    stable = "|".join((item.tenant_pub_id, item.run_pub_id, item.policy_version, input_hash))
    pub_id = "csa_" + sha256(stable.encode()).hexdigest()[:26]
    expected = {
        "pub_id": pub_id,
        "input_hash": input_hash,
        "policy_version": item.policy_version,
        "algorithm_version": ALGORITHM_VERSION,
        "status": analysis.status,
        "cohort_counts": analysis.cohort_counts,
        "feature_comparison": analysis.feature_comparison,
        "recommendations": list(analysis.recommendations),
    }
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id',%s,true),set_config('app.tenant_pub_id',%s,true)",
            (tenant_id, item.tenant_pub_id),
        )
        persisted = connection.execute(
            """
            INSERT INTO platform.content_strategy_analysis
              (id,pub_id,tenant_id,project_id,run_id,input_hash,policy_version,
               algorithm_version,status,cohort_counts,feature_comparison,recommendations)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb)
            ON CONFLICT (run_id,policy_version,input_hash) DO NOTHING
            RETURNING pub_id,input_hash,policy_version,algorithm_version,status,
                      cohort_counts,feature_comparison,recommendations
            """,
            (
                uuid.uuid5(uuid.NAMESPACE_URL, f"geo-content-strategy:{stable}"),
                pub_id,
                tenant_id,
                project_id,
                run_id,
                input_hash,
                item.policy_version,
                ALGORITHM_VERSION,
                analysis.status,
                _canonical_json(analysis.cohort_counts),
                _canonical_json(analysis.feature_comparison),
                _canonical_json(list(analysis.recommendations)),
            ),
        ).fetchone()
        if persisted is None:
            persisted = connection.execute(
                """
                SELECT pub_id,input_hash,policy_version,algorithm_version,status,
                       cohort_counts,feature_comparison,recommendations
                FROM platform.content_strategy_analysis
                WHERE run_id=%s AND policy_version=%s AND input_hash=%s
                """,
                (run_id, item.policy_version, input_hash),
            ).fetchone()
        if persisted is None or dict(persisted) != expected:
            raise ApplicationError(
                "content strategy replay payload drifted",
                type="content_strategy_replay_drift",
                non_retryable=True,
            )
        connection.commit()
    return ContentStrategyResult(
        analysis_pub_id=pub_id,
        status=analysis.status,
        u_occurrences=analysis.cohort_counts["u_occurrences"],
        snapshot_available=analysis.cohort_counts["snapshot_available"],
        recommendations=len(analysis.recommendations),
    )


@activity.defn(name="analyze_content_strategy")
async def analyze_content_strategy(item: ContentStrategyInput) -> ContentStrategyResult:
    settings = get_settings()
    store = ContentAddressedObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
    )
    return await asyncio.to_thread(
        execute_content_strategy,
        item,
        dsn=_dsn(),
        text_store=_MinioSourceTextStore(store),
    )


__all__ = [
    "ContentStrategyInput",
    "ContentStrategyResult",
    "analyze_content_strategy",
    "execute_content_strategy",
]
