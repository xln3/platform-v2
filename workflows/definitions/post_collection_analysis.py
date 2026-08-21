"""Run-level analysis detached from authenticated answer collection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import CancelledError

with workflow.unsafe.imports_passed_through():
    from workflows.activities.analysis_jobs import AnalysisJobStateInput, mark_analysis_job
    from workflows.activities.disparagement import (
        DisparagementInput,
        judge_run_disparagement,
    )
    from workflows.activities.disparagement_factcheck import (
        FactcheckInput,
        factcheck_disparagement_cases,
    )
    from workflows.activities.own_site_snapshot import (
        OwnSiteSnapshotInput,
        capture_own_site_snapshots,
    )
    from workflows.activities.page_inspection import (
        PageInspectionInput,
        inspect_run_source_pages,
    )
    from workflows.activities.site_suggestions import (
        SiteSuggestionsInput,
        generate_site_audit_suggestions,
    )
    from workflows.activities.source_audit import SourceAuditInput, audit_run_sources
    from workflows.activities.source_fetch import SourceFetchInput, fetch_run_sources


@dataclass(frozen=True)
class PostCollectionAnalysisInput:
    tenant_pub_id: str
    project_pub_id: str
    run_pub_id: str
    config_version_pub_id: str
    policy_version: str
    source_task_queue: str
    analysis_jobs: list[str]
    source_analysis_profile_pub_id: str | None = None
    source_analysis_profile_hash: str | None = None
    page_inspection_policy_version: str = "page-inspection-v1"
    page_inspection_model: str = ""
    page_inspection_prompt_version: str = "page-hazard-evidence-v1"


_MARK_RETRY = RetryPolicy(maximum_attempts=10)
_WORK_RETRY = RetryPolicy(maximum_attempts=2)


def _result_summary(value: Any) -> dict[str, Any]:
    """Keep job status useful without persisting page text or exception values."""

    summary: dict[str, Any] = {}
    for field in (
        "windows",
        "judged",
        "validation_failures",
        "skipped",
        "truncated",
        "candidates",
        "checked",
        "suggestions",
        "own_site_documents",
        "disabled",
        "llm_unavailable",
        "invalid_candidates",
        "candidate_quotes",
        "verified_quotes",
        "skipped_documents",
    ):
        item = getattr(value, field, None)
        if isinstance(item, bool | int | str):
            summary[field] = item
    for field in ("captured", "fetched", "audited", "inspected", "failures"):
        item = getattr(value, field, None)
        if isinstance(item, list):
            summary[f"{field}_count"] = len(item)
    return summary


def _result_state(value: Any) -> str:
    if bool(getattr(value, "disabled", False)):
        return "skipped"
    skipped = getattr(value, "skipped", None)
    if isinstance(skipped, str) and skipped:
        return "skipped"
    if bool(getattr(value, "llm_unavailable", False)):
        return "partial" if getattr(value, "inspected", None) else "skipped"
    failures = getattr(value, "failures", None)
    truncated = getattr(value, "truncated", 0)
    if (isinstance(failures, list) and failures) or (isinstance(truncated, int) and truncated > 0):
        return "partial"
    return "completed"


@workflow.defn(name="PostCollectionAnalysisWorkflow")
class PostCollectionAnalysisWorkflow:
    async def _mark(
        self,
        data: PostCollectionAnalysisInput,
        analyzer_kind: str,
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
                analyzer_kind=analyzer_kind,
                policy_version=data.policy_version,
                state=state,
                error_code=error_code,
                result=result,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_MARK_RETRY,
        )

    async def _stage(
        self,
        data: PostCollectionAnalysisInput,
        analyzer_kind: str,
        activity_callable: Any,
        activity_input: Any,
        *,
        start_to_close: timedelta,
        task_queue: str | None = None,
    ) -> bool:
        await self._mark(data, analyzer_kind, "running")
        try:
            result = await workflow.execute_activity(
                activity_callable,
                activity_input,
                start_to_close_timeout=start_to_close,
                heartbeat_timeout=timedelta(seconds=60),
                retry_policy=_WORK_RETRY,
                task_queue=task_queue,
            )
        except (asyncio.CancelledError, CancelledError):
            raise
        except Exception as exc:  # stage failure is isolated from sibling stages
            workflow.logger.warning("%s failed: %r", analyzer_kind, exc)
            await self._mark(
                data,
                analyzer_kind,
                "failed",
                error_code="activity_failed",
            )
            return False
        await self._mark(
            data,
            analyzer_kind,
            _result_state(result),
            result=_result_summary(result),
        )
        return True

    async def _skip_dependency(
        self,
        data: PostCollectionAnalysisInput,
        analyzer_kind: str,
    ) -> None:
        await self._mark(
            data,
            analyzer_kind,
            "skipped",
            error_code="dependency_failed",
        )

    async def _source_audit_branch(
        self,
        data: PostCollectionAnalysisInput,
        source_fetch_ok: bool,
    ) -> None:
        if not source_fetch_ok:
            await self._skip_dependency(data, "source_audit")
            await self._skip_dependency(data, "site_suggestions")
            return
        audit_ok = await self._stage(
            data,
            "source_audit",
            audit_run_sources,
            SourceAuditInput(
                tenant_pub_id=data.tenant_pub_id,
                project_pub_id=data.project_pub_id,
                run_pub_id=data.run_pub_id,
            ),
            start_to_close=timedelta(minutes=15),
        )
        if not audit_ok:
            await self._skip_dependency(data, "site_suggestions")
            return
        await self._stage(
            data,
            "site_suggestions",
            generate_site_audit_suggestions,
            SiteSuggestionsInput(
                tenant_pub_id=data.tenant_pub_id,
                project_pub_id=data.project_pub_id,
                run_pub_id=data.run_pub_id,
            ),
            start_to_close=timedelta(minutes=15),
        )

    async def _risk_branch(self, data: PostCollectionAnalysisInput) -> None:
        risk_ok = await self._stage(
            data,
            "risk_disparagement",
            judge_run_disparagement,
            DisparagementInput(
                tenant_pub_id=data.tenant_pub_id,
                project_pub_id=data.project_pub_id,
                run_pub_id=data.run_pub_id,
            ),
            start_to_close=timedelta(minutes=120),
        )
        if not risk_ok:
            await self._skip_dependency(data, "risk_factcheck")
            return
        await self._stage(
            data,
            "risk_factcheck",
            factcheck_disparagement_cases,
            FactcheckInput(
                tenant_pub_id=data.tenant_pub_id,
                project_pub_id=data.project_pub_id,
                run_pub_id=data.run_pub_id,
            ),
            start_to_close=timedelta(minutes=30),
        )

    async def _page_inspection_branch(
        self,
        data: PostCollectionAnalysisInput,
        source_fetch_ok: bool,
    ) -> None:
        if "page_inspection" not in data.analysis_jobs:
            return
        if data.source_analysis_profile_pub_id is None:
            # The durable job was inserted as not_requested at collection
            # handoff.  Do not silently bind a profile created afterwards.
            return
        if not source_fetch_ok:
            await self._skip_dependency(data, "page_inspection")
            return
        await self._stage(
            data,
            "page_inspection",
            inspect_run_source_pages,
            PageInspectionInput(
                tenant_pub_id=data.tenant_pub_id,
                project_pub_id=data.project_pub_id,
                run_pub_id=data.run_pub_id,
                profile_pub_id=data.source_analysis_profile_pub_id,
                profile_hash=data.source_analysis_profile_hash or "",
                policy_version=data.page_inspection_policy_version,
                model=data.page_inspection_model,
                prompt_version=data.page_inspection_prompt_version,
            ),
            start_to_close=timedelta(minutes=120),
        )

    @workflow.run
    async def run(self, data: PostCollectionAnalysisInput) -> dict[str, str]:
        source_fetch, own_site = await asyncio.gather(
            self._stage(
                data,
                "source_fetch",
                fetch_run_sources,
                SourceFetchInput(
                    tenant_pub_id=data.tenant_pub_id,
                    project_pub_id=data.project_pub_id,
                    run_pub_id=data.run_pub_id,
                ),
                start_to_close=timedelta(minutes=60),
                task_queue=data.source_task_queue,
            ),
            self._stage(
                data,
                "own_site_snapshot",
                capture_own_site_snapshots,
                OwnSiteSnapshotInput(
                    tenant_pub_id=data.tenant_pub_id,
                    project_pub_id=data.project_pub_id,
                    run_pub_id=data.run_pub_id,
                ),
                start_to_close=timedelta(minutes=10),
                task_queue=data.source_task_queue,
            ),
        )
        # The answer-risk branch can still run if public-page acquisition failed;
        # it reads the immutable captured answers and any snapshots that exist.
        await asyncio.gather(
            self._source_audit_branch(data, source_fetch),
            self._page_inspection_branch(data, source_fetch),
            self._risk_branch(data),
        )
        return {
            "state": "terminal",
            "run_pub_id": data.run_pub_id,
            "source_fetch": "terminal" if source_fetch else "failed",
            "own_site_snapshot": "terminal" if own_site else "failed",
        }


__all__ = ["PostCollectionAnalysisInput", "PostCollectionAnalysisWorkflow"]
