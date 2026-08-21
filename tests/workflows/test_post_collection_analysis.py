"""Detached post-collection analysis queue and failure-isolation contracts."""

from __future__ import annotations

import uuid

from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from workflows.activities.analysis_jobs import AnalysisJobStateInput
from workflows.activities.disparagement import DisparagementInput, DisparagementResult
from workflows.activities.disparagement_factcheck import FactcheckInput, FactcheckResult
from workflows.activities.own_site_snapshot import (
    OwnSiteSnapshotInput,
    OwnSiteSnapshotResult,
)
from workflows.activities.page_inspection import PageInspectionInput, PageInspectionResult
from workflows.activities.site_suggestions import SiteSuggestionsInput, SiteSuggestionsResult
from workflows.activities.source_audit import SourceAuditInput, SourceAuditResult
from workflows.activities.source_fetch import SourceFetchInput, SourceFetchResult
from workflows.definitions.post_collection_analysis import (
    PostCollectionAnalysisInput,
    PostCollectionAnalysisWorkflow,
)

marks: list[tuple[str, str, str | None]] = []
mark_policies: list[tuple[str, str]] = []
stage_calls: list[tuple[str, str]] = []


@activity.defn(name="mark_analysis_job")
async def mark_job_fixture(item: AnalysisJobStateInput) -> dict[str, object]:
    marks.append((item.analyzer_kind, item.state, item.error_code))
    mark_policies.append((item.analyzer_kind, item.policy_version))
    return {"state": item.state}


@activity.defn(name="fetch_run_sources")
async def source_fetch_fixture(item: SourceFetchInput) -> SourceFetchResult:
    stage_calls.append(("source_fetch", item.run_pub_id))
    if item.run_pub_id == "run_source_failure":
        raise ApplicationError(
            "fixture public source unavailable",
            type="fixture_source_unavailable",
            non_retryable=True,
        )
    return SourceFetchResult()


@activity.defn(name="capture_own_site_snapshots")
async def own_site_fixture(item: OwnSiteSnapshotInput) -> OwnSiteSnapshotResult:
    stage_calls.append(("own_site_snapshot", item.run_pub_id))
    return OwnSiteSnapshotResult()


@activity.defn(name="audit_run_sources")
async def source_audit_fixture(item: SourceAuditInput) -> SourceAuditResult:
    stage_calls.append(("source_audit", item.run_pub_id))
    return SourceAuditResult()


@activity.defn(name="inspect_run_source_pages")
async def page_inspection_fixture(item: PageInspectionInput) -> PageInspectionResult:
    assert item.profile_pub_id == "sap_detached"
    assert item.profile_hash == "a" * 64
    assert item.policy_version == "page-inspection-v1-r1-fixture"
    assert item.model == "audit-model-fixed"
    assert item.prompt_version == "page-hazard-evidence-v1"
    stage_calls.append(("page_inspection", item.run_pub_id))
    return PageInspectionResult()


@activity.defn(name="generate_site_audit_suggestions")
async def site_suggestions_fixture(item: SiteSuggestionsInput) -> SiteSuggestionsResult:
    stage_calls.append(("site_suggestions", item.run_pub_id))
    return SiteSuggestionsResult()


@activity.defn(name="judge_run_disparagement")
async def risk_fixture(item: DisparagementInput) -> DisparagementResult:
    stage_calls.append(("risk_disparagement", item.run_pub_id))
    return DisparagementResult()


@activity.defn(name="factcheck_disparagement_cases")
async def factcheck_fixture(item: FactcheckInput) -> FactcheckResult:
    stage_calls.append(("risk_factcheck", item.run_pub_id))
    return FactcheckResult()


def _input(run_pub_id: str, source_queue: str) -> PostCollectionAnalysisInput:
    return PostCollectionAnalysisInput(
        tenant_pub_id="tnt_detached",
        project_pub_id="prj_detached",
        run_pub_id=run_pub_id,
        config_version_pub_id="cfv_detached",
        policy_version="post-collection-v1",
        source_task_queue=source_queue,
        analysis_jobs=[
            "own_site_snapshot",
            "source_fetch",
            "source_audit",
            "page_inspection",
            "site_suggestions",
            "risk_disparagement",
            "risk_factcheck",
        ],
        source_analysis_profile_pub_id="sap_detached",
        source_analysis_profile_hash="a" * 64,
        page_inspection_policy_version="page-inspection-v1-r1-fixture",
        page_inspection_model="audit-model-fixed",
        page_inspection_prompt_version="page-hazard-evidence-v1",
    )


async def _run(run_pub_id: str) -> dict[str, str]:
    analysis_queue = f"analysis-{uuid.uuid4().hex}"
    source_queue = f"source-{uuid.uuid4().hex}"
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        # Public-web activities are deliberately unavailable on the analysis
        # worker, and semantic/risk activities are unavailable on the source
        # worker. A wrong route therefore cannot make this test pass.
        async with (
            Worker(
                environment.client,
                task_queue=source_queue,
                activities=[source_fetch_fixture, own_site_fixture],
            ),
            Worker(
                environment.client,
                task_queue=analysis_queue,
                workflows=[PostCollectionAnalysisWorkflow],
                activities=[
                    mark_job_fixture,
                    source_audit_fixture,
                    page_inspection_fixture,
                    site_suggestions_fixture,
                    risk_fixture,
                    factcheck_fixture,
                ],
            ),
        ):
            return await environment.client.execute_workflow(
                PostCollectionAnalysisWorkflow.run,
                _input(run_pub_id, source_queue),
                id=f"post-collection-analysis/test/{uuid.uuid4().hex}",
                task_queue=analysis_queue,
            )


async def test_public_acquisition_and_analysis_run_on_separate_queues() -> None:
    marks.clear()
    mark_policies.clear()
    stage_calls.clear()
    result = await _run("run_success")

    assert result == {
        "state": "terminal",
        "run_pub_id": "run_success",
        "source_fetch": "terminal",
        "own_site_snapshot": "terminal",
    }
    assert {name for name, _run_id in stage_calls} == {
        "source_fetch",
        "own_site_snapshot",
        "source_audit",
        "page_inspection",
        "site_suggestions",
        "risk_disparagement",
        "risk_factcheck",
    }
    final_states = {kind: state for kind, state, _error in marks}
    assert final_states == {
        "source_fetch": "completed",
        "own_site_snapshot": "completed",
        "source_audit": "completed",
        "page_inspection": "completed",
        "site_suggestions": "completed",
        "risk_disparagement": "completed",
        "risk_factcheck": "completed",
    }
    assert {policy for _kind, policy in mark_policies} == {"post-collection-v1"}


async def test_source_failure_does_not_block_answer_risk_analysis() -> None:
    marks.clear()
    mark_policies.clear()
    stage_calls.clear()
    result = await _run("run_source_failure")

    assert result["state"] == "terminal"
    assert result["source_fetch"] == "failed"
    called = {name for name, _run_id in stage_calls}
    assert "source_audit" not in called
    assert "site_suggestions" not in called
    assert "page_inspection" not in called
    assert {"risk_disparagement", "risk_factcheck"} <= called
    final = {kind: (state, error) for kind, state, error in marks}
    assert final["source_fetch"] == ("failed", "activity_failed")
    assert final["source_audit"] == ("skipped", "dependency_failed")
    assert final["site_suggestions"] == ("skipped", "dependency_failed")
    assert final["page_inspection"] == ("skipped", "dependency_failed")
    assert final["risk_disparagement"] == ("completed", None)
    assert final["risk_factcheck"] == ("completed", None)
    assert {policy for _kind, policy in mark_policies} == {"post-collection-v1"}
