from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from workflows.activities.analysis_jobs import AnalysisJobStateInput, mark_analysis_job
    from workflows.activities.report_v2 import (
        validate_formal_metric_snapshot_binding_activity,
    )
    from workflows.activities.s02 import (
        analyze_answer_activity,
        capture_evidence_activity,
        extract_brands_activity,
        fail_formal_report_activity,
        finalize_formal_report_activity,
        finalize_report_activity,
        freeze_report_activity,
        persist_investigation_verdict_activity,
        preflight_formal_report_runtime_activity,
        prepare_evidence_activity,
        produce_formal_report_activity,
        produce_report_activity,
        score_investigation_activity,
    )

_RETRY = RetryPolicy(
    initial_interval=timedelta(milliseconds=100),
    backoff_coefficient=2,
    maximum_interval=timedelta(seconds=2),
    maximum_attempts=5,
)


@workflow.defn(name="AnswerAnalysisWorkflow")
class AnswerAnalysisWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        track_job = workflow.patched("answer-analysis-job-state-v1") and isinstance(
            payload.get("analysis_job"), dict
        )
        job = payload.get("analysis_job") if track_job else None

        async def mark(
            state: str,
            *,
            error_code: str | None = None,
            summary: dict[str, Any] | None = None,
        ) -> None:
            if not isinstance(job, dict):
                return
            await workflow.execute_activity(
                mark_analysis_job,
                AnalysisJobStateInput(
                    tenant_pub_id=str(payload["tenant_pub_id"]),
                    subject_type=str(job["subject_type"]),
                    subject_pub_id=str(job["subject_pub_id"]),
                    analyzer_kind=str(job["analyzer_kind"]),
                    policy_version=str(job["policy_version"]),
                    state=state,
                    error_code=error_code,
                    result=summary,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=10),
            )

        await mark("running")
        try:
            result: dict[str, Any] = await workflow.execute_activity(
                analyze_answer_activity,
                payload,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY,
            )
        except Exception:
            await mark("failed", error_code="activity_failed")
            raise
        # brandrank-extract-v1（W3）：分析主链之后追加品牌抽取侧车。
        # 未打补丁的历史重放不含 marker → patched() 返回 False → 不排新 activity
        # （旧 history 逐字节按旧语义重放）。LLM 单条 60s 超时+线程切换开销，
        # 30s 装不下，start_to_close 给 120s。侧车失败绝不阻塞分析主链：
        # activity 内部对 LLM 失败已诚实落 failed 行，这里只对基础设施级
        # 异常（重试耗尽）降级为 warning 留痕。
        if workflow.patched("brandrank-extract-v1"):
            try:
                result["brand_extract"] = await workflow.execute_activity(
                    extract_brands_activity,
                    payload,
                    start_to_close_timeout=timedelta(seconds=120),
                    retry_policy=_RETRY,
                )
            except Exception as exc:
                workflow.logger.warning("brandrank extract sidecar failed: %s", type(exc).__name__)
                result["brand_extract"] = {"state": "sidecar_failed"}
        persistence = result.get("persistence")
        await mark(
            "completed",
            summary=(
                {
                    "analysis_pub_id": str(persistence.get("analysis_pub_id") or ""),
                    "analysis_run_pub_id": str(persistence.get("analysis_run_pub_id") or ""),
                }
                if isinstance(persistence, dict)
                else {}
            ),
        )
        return result


@workflow.defn(name="EvidenceCaptureWorkflow")
class EvidenceCaptureWorkflow:
    def __init__(self) -> None:
        self._lease_pub_id: str | None = None
        self._cancelled = False

    @workflow.signal
    async def authorize_capture(self, lease_pub_id: str) -> None:
        if self._lease_pub_id is None:
            self._lease_pub_id = lease_pub_id

    @workflow.signal
    async def revoke(self) -> None:
        self._cancelled = True

    @workflow.query
    def state(self) -> str:
        if self._cancelled:
            return "revoked"
        if self._lease_pub_id:
            return "authorized"
        return "awaiting_capability"

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("requires_authenticated_session"):
            await workflow.wait_condition(lambda: self._lease_pub_id is not None or self._cancelled)
        if self._cancelled:
            return {"state": "revoked", "captured": False}
        prepared: dict[str, Any] = await workflow.execute_activity(
            prepare_evidence_activity,
            payload
            | {
                "lease_pub_id": self._lease_pub_id,
                "workflow_id": workflow.info().workflow_id,
            },
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_RETRY,
        )
        if "capture_payload_b64" not in payload:
            return prepared
        return await workflow.execute_activity(
            capture_evidence_activity,
            prepared,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_RETRY,
        )


@workflow.defn(name="ReportProductionWorkflow")
class ReportProductionWorkflow:
    def __init__(self) -> None:
        self._review: dict[str, Any] | None = None
        self._formal_state: str | None = None

    @workflow.signal
    async def review(self, decision: dict[str, Any]) -> None:
        if self._review is None:
            self._review = decision

    @workflow.query
    def state(self) -> str:
        if self._formal_state is not None:
            return self._formal_state
        return "reviewed" if self._review else "awaiting_review"

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("formal_production_pub_id"):
            return await self._run_formal(payload)
        frozen = await workflow.execute_activity(
            freeze_report_activity,
            payload,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_RETRY,
        )
        produced: dict[str, Any] | None = None
        if payload.get("persist"):
            produced = await workflow.execute_activity(
                produce_report_activity,
                payload | {"workflow_operation_id": workflow.info().workflow_id},
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=_RETRY,
            )
        await workflow.wait_condition(lambda: self._review is not None)
        assert self._review is not None
        if produced is not None:
            finalized: dict[str, Any] = await workflow.execute_activity(
                finalize_report_activity,
                {
                    "tenant_pub_id": payload["tenant_pub_id"],
                    **produced,
                    "review": self._review,
                },
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY,
            )
            return {
                **finalized,
                "freeze": frozen,
                "artifacts": produced["artifacts"],
                "review": self._review,
            }
        return {
            "state": "approved" if self._review["approved"] else "changes_requested",
            "freeze": frozen,
            "review": self._review,
        }

    async def _run_formal(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("metric_snapshot_set_pub_id") is not None and workflow.patched(
            "formal-metric-snapshot-binding-v2"
        ):
            self._formal_state = "binding_snapshot"
            try:
                await workflow.execute_activity(
                    validate_formal_metric_snapshot_binding_activity,
                    payload,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=_RETRY,
                )
            except Exception as exc:
                self._formal_state = "failed"
                activity_error = getattr(exc, "cause", None)
                error_type = getattr(activity_error, "type", None) or getattr(exc, "type", None)
                error_code = (
                    "metric_snapshot_set_not_ready"
                    if error_type == "metric_snapshot_set_not_ready"
                    else "metric_snapshot_binding_failed"
                )
                binding_failed: dict[str, Any] = await workflow.execute_activity(
                    fail_formal_report_activity,
                    payload | {"error_code": error_code},
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=_RETRY,
                )
                return binding_failed
        self._formal_state = "preflight"
        try:
            await workflow.execute_activity(
                preflight_formal_report_runtime_activity,
                payload,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY,
            )
        except Exception:
            self._formal_state = "failed"
            failed: dict[str, Any] = await workflow.execute_activity(
                fail_formal_report_activity,
                payload | {"error_code": "libreoffice_dependency_missing"},
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY,
            )
            return failed
        self._formal_state = "running"
        try:
            produced: dict[str, Any] = await workflow.execute_activity(
                produce_formal_report_activity,
                payload,
                start_to_close_timeout=timedelta(minutes=30),
                retry_policy=_RETRY,
            )
        except Exception:
            self._formal_state = "failed"
            failed = await workflow.execute_activity(
                fail_formal_report_activity,
                payload | {"error_code": "production_failed"},
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY,
            )
            # A start-to-close timeout cancels Temporal's activity task, but it
            # cannot kill the Python thread running synchronous DOCX/PDF work.  If
            # that thread committed just before mark_failed acquired the row lock,
            # the database already contains a complete awaiting-review bundle.  In
            # that one case the workflow must resume the review wait instead of
            # completing and orphaning an unsignable production.
            if failed.get("status") != "awaiting_review":
                return failed
            produced = failed
        if produced.get("status") == "failed":
            self._formal_state = "failed"
            return produced
        self._formal_state = "awaiting_review"
        await workflow.wait_condition(lambda: self._review is not None)
        assert self._review is not None
        self._formal_state = "finalizing"
        finalized: dict[str, Any] = await workflow.execute_activity(
            finalize_formal_report_activity,
            payload | {"review": self._review},
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=_RETRY,
        )
        self._formal_state = str(finalized["status"])
        return {**finalized, "production": produced, "review": self._review}


@workflow.defn(name="AntiGeoInvestigationWorkflow")
class AntiGeoInvestigationWorkflow:
    def __init__(self) -> None:
        self._verdict: dict[str, Any] | None = None

    @workflow.signal
    async def human_verdict(self, verdict: dict[str, Any]) -> None:
        if self._verdict is None:
            self._verdict = verdict

    @workflow.query
    def state(self) -> str:
        return "decided" if self._verdict else "awaiting_human_verdict"

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        score = await workflow.execute_activity(
            score_investigation_activity,
            payload,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_RETRY,
        )
        await workflow.wait_condition(lambda: self._verdict is not None)
        assert self._verdict is not None
        persistence: dict[str, Any] | None = None
        if payload.get("persist"):
            persistence = await workflow.execute_activity(
                persist_investigation_verdict_activity,
                {
                    "tenant_pub_id": payload["tenant_pub_id"],
                    "investigation_pub_id": payload["investigation_pub_id"],
                    "human_verdict": self._verdict,
                },
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY,
            )
        return {
            "state": "decided",
            "score": score,
            "human_verdict": self._verdict,
            "persistence": persistence,
        }
