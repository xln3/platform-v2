"""Standalone replay workflow for page inspection on existing source snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from workflows.activities.analysis_jobs import AnalysisJobStateInput, mark_analysis_job
    from workflows.activities.page_inspection import (
        PageInspectionInput,
        inspect_run_source_pages,
    )


@dataclass(frozen=True)
class PageInspectionWorkflowInput:
    tenant_pub_id: str
    project_pub_id: str
    run_pub_id: str
    profile_pub_id: str
    profile_hash: str
    policy_version: str
    model: str
    prompt_version: str


_MARK_RETRY = RetryPolicy(maximum_attempts=10)
_WORK_RETRY = RetryPolicy(maximum_attempts=2)


def _state(value: Any) -> str:
    if bool(getattr(value, "disabled", False)) or getattr(value, "skipped", None):
        return "skipped"
    if bool(getattr(value, "llm_unavailable", False)):
        return "partial" if getattr(value, "inspected", None) else "skipped"
    if getattr(value, "failures", None) or int(getattr(value, "truncated", 0) or 0) > 0:
        return "partial"
    return "completed"


def _summary(value: Any) -> dict[str, Any]:
    return {
        "inspected_count": len(getattr(value, "inspected", []) or []),
        "failure_count": len(getattr(value, "failures", []) or []),
        "skipped_documents": int(getattr(value, "skipped_documents", 0) or 0),
        "invalid_candidates": int(getattr(value, "invalid_candidates", 0) or 0),
        "candidate_quotes": int(getattr(value, "candidate_quotes", 0) or 0),
        "verified_quotes": int(getattr(value, "verified_quotes", 0) or 0),
        "truncated": int(getattr(value, "truncated", 0) or 0),
        "llm_unavailable": bool(getattr(value, "llm_unavailable", False)),
        "skipped": getattr(value, "skipped", None),
    }


@workflow.defn(name="PageInspectionWorkflow")
class PageInspectionWorkflow:
    async def _mark(
        self,
        data: PageInspectionWorkflowInput,
        state: str,
        *,
        error_code: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        await workflow.execute_activity(
            mark_analysis_job,
            AnalysisJobStateInput(
                tenant_pub_id=data.tenant_pub_id,
                subject_type="run",
                subject_pub_id=data.run_pub_id,
                analyzer_kind="page_inspection",
                policy_version=data.policy_version,
                state=state,
                error_code=error_code,
                result=result,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_MARK_RETRY,
        )

    @workflow.run
    async def run(self, data: PageInspectionWorkflowInput) -> dict[str, Any]:
        await self._mark(data, "running")
        try:
            result = await workflow.execute_activity(
                inspect_run_source_pages,
                PageInspectionInput(
                    tenant_pub_id=data.tenant_pub_id,
                    project_pub_id=data.project_pub_id,
                    run_pub_id=data.run_pub_id,
                    profile_pub_id=data.profile_pub_id,
                    profile_hash=data.profile_hash,
                    policy_version=data.policy_version,
                    model=data.model,
                    prompt_version=data.prompt_version,
                ),
                start_to_close_timeout=timedelta(minutes=120),
                heartbeat_timeout=timedelta(seconds=60),
                retry_policy=_WORK_RETRY,
            )
        except Exception as exc:
            workflow.logger.warning("page inspection failed: %s", type(exc).__name__)
            await self._mark(data, "failed", error_code="activity_failed")
            return {"state": "failed", "run_pub_id": data.run_pub_id}
        terminal = _state(result)
        await self._mark(data, terminal, result=_summary(result))
        return {
            "state": terminal,
            "run_pub_id": data.run_pub_id,
            "profile_pub_id": data.profile_pub_id,
        }


__all__ = ["PageInspectionWorkflow", "PageInspectionWorkflowInput"]
