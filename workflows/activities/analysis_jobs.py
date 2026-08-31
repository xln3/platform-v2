"""Durable, versioned state for analysis that runs after answer capture.

The collector only creates jobs.  Analysis workers own every later state
transition, so an analyzer retry or failure can never rewrite captured data.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from geo_platform.tenancy.database import WorkerSessionLocal
from geo_platform.tenancy.repository import TenantRepository
from sqlalchemy import text
from sqlalchemy.orm import Session
from temporalio import activity
from temporalio.exceptions import ApplicationError

ANSWER_BASIC_POLICY_VERSION = "answer-basic-v1"
POST_COLLECTION_POLICY_VERSION = "post-collection-v2"

RUN_ANALYZER_KINDS = (
    "own_site_snapshot",
    "source_fetch",
    "content_contribution",
    "content_strategy",
    "source_audit",
    "page_inspection",
    "site_suggestions",
    "risk_disparagement",
    "risk_factcheck",
)

ANALYSIS_JOB_STATES = frozenset(
    {
        "not_requested",
        "queued",
        "running",
        "completed",
        "partial",
        "failed",
        "skipped",
    }
)
ANALYSIS_TERMINAL_STATES = frozenset({"not_requested", "completed", "partial", "failed", "skipped"})
_ERROR_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,119}$")


@dataclass(frozen=True)
class AnalysisJobStateInput:
    tenant_pub_id: str
    subject_type: str
    subject_pub_id: str
    analyzer_kind: str
    policy_version: str
    state: str
    error_code: str | None = None
    result: dict[str, Any] | None = None


def canonical_input_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256(encoded).hexdigest()


def derive_analysis_job_pub_id(
    *,
    tenant_pub_id: str,
    subject_type: str,
    subject_pub_id: str,
    analyzer_kind: str,
    policy_version: str,
) -> str:
    stable = "|".join(
        (
            tenant_pub_id,
            subject_type,
            subject_pub_id,
            analyzer_kind,
            policy_version,
        )
    )
    return f"ajb_{sha256(stable.encode()).hexdigest()[:26]}"


def enqueue_analysis_job(
    session: Session,
    *,
    tenant_pub_id: str,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    answer_task_id: uuid.UUID | None,
    subject_type: str,
    subject_pub_id: str,
    analyzer_kind: str,
    policy_version: str,
    input_hash: str,
    workflow_id: str,
    state: str = "queued",
    error_code: str | None = None,
) -> str:
    """Insert a job in the caller's transaction and reject replay drift."""

    if subject_type not in {"answer", "run"}:
        raise ValueError("analysis_job_subject_type_invalid")
    if (subject_type == "answer") != (answer_task_id is not None):
        raise ValueError("analysis_job_subject_link_invalid")
    if state not in ANALYSIS_JOB_STATES:
        raise ValueError("analysis_job_state_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", input_hash):
        raise ValueError("analysis_job_input_hash_invalid")
    if error_code is not None and not _ERROR_CODE_RE.fullmatch(error_code):
        raise ValueError("analysis_job_error_code_invalid")

    pub_id = derive_analysis_job_pub_id(
        tenant_pub_id=tenant_pub_id,
        subject_type=subject_type,
        subject_pub_id=subject_pub_id,
        analyzer_kind=analyzer_kind,
        policy_version=policy_version,
    )
    persisted = (
        session.execute(
            text(
                """
            INSERT INTO platform.analysis_job (
              id,pub_id,tenant_id,project_id,run_id,answer_task_id,
              subject_type,subject_pub_id,analyzer_kind,policy_version,
              input_hash,workflow_id,state,error_code
            ) VALUES (
              :id,:pub_id,:tenant_id,:project_id,:run_id,:answer_task_id,
              :subject_type,:subject_pub_id,:analyzer_kind,:policy_version,
              :input_hash,:workflow_id,:state,:error_code
            )
            ON CONFLICT ON CONSTRAINT uq_analysis_job_subject_analyzer_policy
            DO UPDATE SET updated_at=platform.analysis_job.updated_at
            RETURNING pub_id,input_hash,workflow_id,subject_type,subject_pub_id,
                      analyzer_kind,policy_version
            """
            ),
            {
                "id": uuid.uuid4(),
                "pub_id": pub_id,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "run_id": run_id,
                "answer_task_id": answer_task_id,
                "subject_type": subject_type,
                "subject_pub_id": subject_pub_id,
                "analyzer_kind": analyzer_kind,
                "policy_version": policy_version,
                "input_hash": input_hash,
                "workflow_id": workflow_id,
                "state": state,
                "error_code": error_code,
            },
        )
        .mappings()
        .one()
    )
    expected = {
        "pub_id": pub_id,
        "input_hash": input_hash,
        "workflow_id": workflow_id,
        "subject_type": subject_type,
        "subject_pub_id": subject_pub_id,
        "analyzer_kind": analyzer_kind,
        "policy_version": policy_version,
    }
    if dict(persisted) != expected:
        raise ApplicationError(
            "analysis job replay payload drifted",
            type="analysis_job_payload_drift",
            non_retryable=True,
        )
    return pub_id


def _validate_state_input(item: AnalysisJobStateInput) -> None:
    if item.subject_type not in {"answer", "run"}:
        raise ApplicationError(
            "analysis job subject is invalid",
            type="analysis_job_subject_invalid",
            non_retryable=True,
        )
    if item.state not in ANALYSIS_JOB_STATES - {"queued", "not_requested"}:
        raise ApplicationError(
            "analysis job transition is invalid",
            type="analysis_job_state_invalid",
            non_retryable=True,
        )
    if item.error_code is not None and not _ERROR_CODE_RE.fullmatch(item.error_code):
        raise ApplicationError(
            "analysis job error code is invalid",
            type="analysis_job_error_code_invalid",
            non_retryable=True,
        )


def _mark_analysis_job(item: AnalysisJobStateInput) -> dict[str, Any]:
    _validate_state_input(item)
    result_json = json.dumps(
        item.result or {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with WorkerSessionLocal() as session:
        TenantRepository(session, item.tenant_pub_id)
        current = (
            session.execute(
                text(
                    """
                SELECT pub_id,state,attempt_count
                FROM platform.analysis_job
                WHERE subject_type=:subject_type
                  AND subject_pub_id=:subject_pub_id
                  AND analyzer_kind=:analyzer_kind
                  AND policy_version=:policy_version
                FOR UPDATE
                """
                ),
                {
                    "subject_type": item.subject_type,
                    "subject_pub_id": item.subject_pub_id,
                    "analyzer_kind": item.analyzer_kind,
                    "policy_version": item.policy_version,
                },
            )
            .mappings()
            .one_or_none()
        )
        if current is None:
            raise ApplicationError(
                "analysis job does not exist",
                type="analysis_job_not_found",
                non_retryable=True,
            )
        current_state = str(current["state"])
        if current_state in ANALYSIS_TERMINAL_STATES and current_state != item.state:
            # A terminal result is immutable. A new policy version creates a new
            # job instead of silently reopening an old judgment.
            return {
                "pub_id": str(current["pub_id"]),
                "state": current_state,
                "attempt_count": int(current["attempt_count"]),
            }
        updated = (
            session.execute(
                text(
                    """
                UPDATE platform.analysis_job
                SET state=CAST(:state AS varchar),
                    attempt_count=attempt_count+CASE
                      WHEN CAST(:state AS varchar)='running' AND state<>'running' THEN 1 ELSE 0 END,
                    started_at=CASE WHEN CAST(:state AS varchar)='running'
                                    THEN COALESCE(started_at,now())
                                    ELSE started_at END,
                    completed_at=CASE WHEN :terminal THEN COALESCE(completed_at,now())
                                      ELSE completed_at END,
                    error_code=:error_code,
                    result_json=CAST(:result_json AS jsonb),
                    updated_at=now()
                WHERE pub_id=:pub_id
                RETURNING pub_id,state,attempt_count
                """
                ),
                {
                    "pub_id": current["pub_id"],
                    "state": item.state,
                    "terminal": item.state in ANALYSIS_TERMINAL_STATES,
                    "error_code": item.error_code,
                    "result_json": result_json,
                },
            )
            .mappings()
            .one()
        )
        session.commit()
        return dict(updated)


@activity.defn(name="mark_analysis_job")
async def mark_analysis_job(item: AnalysisJobStateInput) -> dict[str, Any]:
    """Move one job without blocking the analysis worker's event loop."""

    return await asyncio.to_thread(_mark_analysis_job, item)


__all__ = [
    "ANALYSIS_JOB_STATES",
    "ANALYSIS_TERMINAL_STATES",
    "ANSWER_BASIC_POLICY_VERSION",
    "AnalysisJobStateInput",
    "POST_COLLECTION_POLICY_VERSION",
    "RUN_ANALYZER_KINDS",
    "canonical_input_hash",
    "derive_analysis_job_pub_id",
    "enqueue_analysis_job",
    "mark_analysis_job",
]
