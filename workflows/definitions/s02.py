from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from workflows.activities.s02 import (
        analyze_answer_activity,
        capture_evidence_activity,
        finalize_report_activity,
        freeze_report_activity,
        persist_investigation_verdict_activity,
        prepare_evidence_activity,
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
        return await workflow.execute_activity(
            analyze_answer_activity,
            payload,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_RETRY,
        )


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

    @workflow.signal
    async def review(self, decision: dict[str, Any]) -> None:
        if self._review is None:
            self._review = decision

    @workflow.query
    def state(self) -> str:
        return "reviewed" if self._review else "awaiting_review"

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
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
