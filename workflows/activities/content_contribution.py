"""Versioned W content-contribution analysis over captured answers and page snapshots."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

import psycopg
from geo_platform.config import get_settings
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from psycopg.rows import dict_row
from temporalio import activity
from temporalio.exceptions import ApplicationError

from domain.collection.uvw import citation_text_for_reference
from domain.source_analysis.content_contribution import (
    ALGORITHM_VERSION,
    POLICY_VERSION,
    PROMPT_VERSION,
    ContributionChunk,
    exact_answer_chunks,
    explicit_citation_chunk,
    validate_chunk,
)
from workflows.activities.source_audit import _MinioSourceTextStore


@dataclass(frozen=True)
class ContentContributionInput:
    tenant_pub_id: str
    project_pub_id: str
    run_pub_id: str
    policy_version: str = POLICY_VERSION


@dataclass(frozen=True)
class ContentContributionFailure:
    occurrence_pub_id: str
    error: str


@dataclass
class ContentContributionResult:
    analyzed: int = 0
    confirmed_occurrences: int = 0
    chunks: int = 0
    no_evidence: int = 0
    failures: list[ContentContributionFailure] = field(default_factory=list)
    skipped: str | None = None


def _dsn() -> str:
    settings = get_settings()
    return os.getenv("S02_POSTGRES_DSN") or (
        settings.worker_postgres_dsn or settings.postgres_dsn
    ).replace("postgresql+psycopg://", "postgresql://")


def _load_subjects(dsn: str, item: ContentContributionInput) -> tuple[str, list[dict[str, Any]]]:
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
            SELECT run.id FROM platform.collection_run run
            JOIN platform.project project ON project.id=run.project_id
            WHERE run.pub_id=%s AND project.pub_id=%s
            """,
            (item.run_pub_id, item.project_pub_id),
        ).fetchone()
        if run is None:
            return tenant_id, []
        rows = connection.execute(
            """
            SELECT occurrence.id AS occurrence_id,occurrence.pub_id AS occurrence_pub_id,
                   occurrence.project_id,occurrence.raw_url,
                   occurrence.final_reference_state,occurrence.final_reference_ordinal,
                   task.id AS answer_task_id,task.answer_text,
                   task.citations_json,url.canonical_url,
                   snapshot.id AS snapshot_id,snapshot.pub_id AS snapshot_pub_id,
                   snapshot.body_object_key AS text_cas_key,snapshot.text_sha256
            FROM platform.answer_source_occurrence occurrence
            JOIN platform.collection_task task ON task.id=occurrence.answer_task_id
            JOIN platform.source_url url ON url.id=occurrence.source_url_id
            JOIN LATERAL (
              SELECT candidate.* FROM platform.source_page_snapshot candidate
              WHERE candidate.source_url_id=occurrence.source_url_id
                AND candidate.project_id=occurrence.project_id
                AND candidate.snapshot_state='succeeded'
                AND candidate.text_sha256 IS NOT NULL
              ORDER BY candidate.captured_at DESC,candidate.pub_id DESC
              LIMIT 1
            ) snapshot ON true
            WHERE occurrence.run_id=%s
              AND (
                occurrence.v_state='entered'
                OR occurrence.final_reference_state='referenced'
              )
            ORDER BY task.created_at,task.pub_id,occurrence.occurrence_ordinal
            """,
            (run["id"],),
        ).fetchall()
    return tenant_id, [dict(row) for row in rows]


def _candidate_chunks(
    *, source_text: str, answer_text: str, cited_text: str | None, final_referenced: bool
) -> list[ContributionChunk]:
    candidates = list(exact_answer_chunks(source_text=source_text, answer_text=answer_text))
    if final_referenced:
        citation = explicit_citation_chunk(source_text=source_text, cited_text=cited_text)
        if citation is not None:
            candidates.append(citation)
    unique: list[ContributionChunk] = []
    seen: set[tuple[int, int, int | None, int | None, str]] = set()
    for candidate in candidates:
        key = (
            candidate.source_text_start,
            candidate.source_text_end,
            candidate.answer_text_start,
            candidate.answer_text_end,
            candidate.basis,
        )
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _citation_text(row: dict[str, Any]) -> str | None:
    """Return only the exact platform citation text for this final-reference row.

    Search-result summaries and citation quotes share neither semantics nor
    provenance, so a candidate summary must never be promoted to W evidence.
    """

    if row.get("final_reference_state") != "referenced":
        return None
    raw = row.get("citations_json")
    try:
        citations = raw if isinstance(raw, list) else json.loads(str(raw or "[]"))
    except (TypeError, ValueError):
        return None
    if not isinstance(citations, list):
        return None
    return citation_text_for_reference(
        citations,
        canonical_url=str(row.get("canonical_url") or ""),
        final_reference_ordinal=row.get("final_reference_ordinal"),
    )


def _analysis_input_hash(
    *,
    row: dict[str, Any],
    answer_text: str,
    cited_text: str | None,
    policy_version: str,
) -> str:
    payload = {
        "occurrence_pub_id": str(row["occurrence_pub_id"]),
        "snapshot_pub_id": str(row["snapshot_pub_id"]),
        "source_text_sha256": str(row["text_sha256"]),
        "answer_text_sha256": sha256(answer_text.encode()).hexdigest(),
        "cited_text_sha256": sha256(cited_text.encode()).hexdigest() if cited_text else None,
        "model": "deterministic",
        "prompt_version": PROMPT_VERSION,
        "policy_version": policy_version,
        "algorithm_version": ALGORITHM_VERSION,
    }
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def execute_content_contribution(
    item: ContentContributionInput,
    *,
    dsn: str,
    text_store: _MinioSourceTextStore,
) -> ContentContributionResult:
    tenant_id, rows = _load_subjects(dsn, item)
    if not rows:
        return ContentContributionResult(skipped="no_page_snapshots")
    result = ContentContributionResult()
    for row in rows:
        occurrence_pub_id = str(row["occurrence_pub_id"])
        try:
            source_text = text_store.get_text(str(row["text_cas_key"]), str(row["text_sha256"]))
            if sha256(source_text.encode()).hexdigest() != row["text_sha256"]:
                raise ValueError("source_hash_mismatch")
            answer_text = str(row["answer_text"] or "")
            cited_text = _citation_text(row)
            chunks = _candidate_chunks(
                source_text=source_text,
                answer_text=answer_text,
                cited_text=cited_text,
                final_referenced=row["final_reference_state"] == "referenced",
            )
            if any(
                not validate_chunk(chunk, source_text=source_text, answer_text=answer_text)
                for chunk in chunks
            ):
                raise ValueError("exact_span_validation_failed")
            with psycopg.connect(dsn) as connection:
                connection.execute(
                    "SELECT set_config('app.tenant_id',%s,true),"
                    "set_config('app.tenant_pub_id',%s,true)",
                    (tenant_id, item.tenant_pub_id),
                )
                analysis_input_hash = _analysis_input_hash(
                    row=row,
                    answer_text=answer_text,
                    cited_text=cited_text,
                    policy_version=item.policy_version,
                )
                result_state = "confirmed" if chunks else "no_evidence"
                analysis_stable = "|".join(
                    (
                        occurrence_pub_id,
                        str(row["snapshot_pub_id"]),
                        item.policy_version,
                    )
                )
                analysis_pub_id = "wca_" + sha256(analysis_stable.encode()).hexdigest()[:26]
                analysis = connection.execute(
                    """
                    INSERT INTO platform.content_contribution_analysis
                      (id,pub_id,tenant_id,project_id,answer_task_id,occurrence_id,
                       snapshot_id,input_hash,result_state,chunk_count,model,prompt_version,
                       policy_version,algorithm_version,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                    ON CONFLICT (occurrence_id,snapshot_id,policy_version) DO NOTHING
                    RETURNING id,pub_id,input_hash,result_state,chunk_count,model,
                              prompt_version,policy_version,algorithm_version
                    """,
                    (
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"geo-content-contribution:{analysis_stable}",
                        ),
                        analysis_pub_id,
                        tenant_id,
                        row["project_id"],
                        row["answer_task_id"],
                        row["occurrence_id"],
                        row["snapshot_id"],
                        analysis_input_hash,
                        result_state,
                        len(chunks),
                        "deterministic",
                        PROMPT_VERSION,
                        item.policy_version,
                        ALGORITHM_VERSION,
                    ),
                ).fetchone()
                if analysis is None:
                    analysis = connection.execute(
                        """
                        SELECT id,pub_id,input_hash,result_state,chunk_count,model,
                               prompt_version,policy_version,algorithm_version
                        FROM platform.content_contribution_analysis
                        WHERE occurrence_id=%s AND snapshot_id=%s AND policy_version=%s
                        """,
                        (row["occurrence_id"], row["snapshot_id"], item.policy_version),
                    ).fetchone()
                expected_analysis = (
                    analysis_pub_id,
                    analysis_input_hash,
                    result_state,
                    len(chunks),
                    "deterministic",
                    PROMPT_VERSION,
                    item.policy_version,
                    ALGORITHM_VERSION,
                )
                if analysis is None or tuple(analysis[1:]) != expected_analysis:
                    raise ValueError("content_contribution_analysis_replay_drift")
                analysis_id = analysis[0]
                for ordinal, chunk in enumerate(chunks, 1):
                    stable = "|".join(
                        (
                            analysis_pub_id,
                            str(ordinal),
                            chunk.source_quote_hash,
                            chunk.answer_quote_hash or "",
                        )
                    )
                    chunk_pub_id = "wch_" + sha256(stable.encode()).hexdigest()[:26]
                    persisted = connection.execute(
                        """
                        INSERT INTO platform.weighted_content_chunk
                          (id,pub_id,tenant_id,project_id,answer_task_id,occurrence_id,
                           snapshot_id,analysis_id,ordinal,source_text_start,source_text_end,
                           source_quote,source_quote_hash,answer_text_start,answer_text_end,
                           answer_quote,answer_quote_hash,basis,contribution_score,confidence,model,
                           prompt_version,policy_version,algorithm_version,verification_state,
                           review_state,created_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                %s,%s,%s,%s,'deterministic',%s,%s,%s,'exact','unreviewed',now())
                        ON CONFLICT (analysis_id,ordinal)
                        DO NOTHING
                        RETURNING analysis_id,pub_id,source_text_start,source_text_end,source_quote,
                                  source_quote_hash,answer_text_start,answer_text_end,
                                  answer_quote,answer_quote_hash,basis,contribution_score,
                                  confidence,model,prompt_version,policy_version,
                                  algorithm_version,verification_state
                        """,
                        (
                            uuid.uuid5(uuid.NAMESPACE_URL, f"geo-w:{stable}"),
                            chunk_pub_id,
                            tenant_id,
                            row["project_id"],
                            row["answer_task_id"],
                            row["occurrence_id"],
                            row["snapshot_id"],
                            analysis_id,
                            ordinal,
                            chunk.source_text_start,
                            chunk.source_text_end,
                            chunk.source_quote,
                            chunk.source_quote_hash,
                            chunk.answer_text_start,
                            chunk.answer_text_end,
                            chunk.answer_quote,
                            chunk.answer_quote_hash,
                            chunk.basis,
                            chunk.contribution_score,
                            chunk.confidence,
                            PROMPT_VERSION,
                            item.policy_version,
                            ALGORITHM_VERSION,
                        ),
                    ).fetchone()
                    if persisted is None:
                        persisted = connection.execute(
                            """
                            SELECT analysis_id,pub_id,
                                   source_text_start,source_text_end,source_quote,
                                   source_quote_hash,answer_text_start,answer_text_end,
                                   answer_quote,answer_quote_hash,basis,contribution_score,
                                   confidence,model,prompt_version,policy_version,
                                   algorithm_version,verification_state
                            FROM platform.weighted_content_chunk
                            WHERE analysis_id=%s AND ordinal=%s
                            """,
                            (analysis_id, ordinal),
                        ).fetchone()
                    expected = (
                        analysis_id,
                        chunk_pub_id,
                        chunk.source_text_start,
                        chunk.source_text_end,
                        chunk.source_quote,
                        chunk.source_quote_hash,
                        chunk.answer_text_start,
                        chunk.answer_text_end,
                        chunk.answer_quote,
                        chunk.answer_quote_hash,
                        chunk.basis,
                        chunk.contribution_score,
                        chunk.confidence,
                        "deterministic",
                        PROMPT_VERSION,
                        item.policy_version,
                        ALGORITHM_VERSION,
                        "exact",
                    )
                    if persisted is None or tuple(persisted) != expected:
                        raise ValueError("content_contribution_replay_drift")
                connection.execute(
                    """
                    UPDATE platform.answer_source_occurrence
                    SET w_state=(
                      SELECT current.result_state
                      FROM platform.content_contribution_analysis current
                      WHERE current.occurrence_id=answer_source_occurrence.id
                      ORDER BY current.created_at DESC,current.pub_id DESC
                      LIMIT 1
                    )
                    WHERE id=%s
                    """,
                    (row["occurrence_id"],),
                )
                connection.commit()
            result.analyzed += 1
            if chunks:
                result.confirmed_occurrences += 1
                result.chunks += len(chunks)
            else:
                result.no_evidence += 1
        except Exception as exc:  # one page cannot poison sibling W facts
            result.failures.append(
                ContentContributionFailure(occurrence_pub_id, type(exc).__name__)
            )
    return result


@activity.defn(name="analyze_content_contribution")
async def analyze_content_contribution(
    item: ContentContributionInput,
) -> ContentContributionResult:
    settings = get_settings()
    store = ContentAddressedObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
    )
    return await asyncio.to_thread(
        execute_content_contribution,
        item,
        dsn=_dsn(),
        text_store=_MinioSourceTextStore(store),
    )


__all__ = [
    "ContentContributionInput",
    "ContentContributionResult",
    "analyze_content_contribution",
    "execute_content_contribution",
]
