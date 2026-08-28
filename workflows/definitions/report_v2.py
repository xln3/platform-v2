"""Dedicated report workflow for a pre-bound Metrics V2 snapshot set."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from workflows.activities.report_v2 import (
        validate_formal_metric_snapshot_binding_activity,
    )
    from workflows.activities.s02 import (
        fail_formal_report_activity,
        finalize_formal_report_activity,
        preflight_formal_report_runtime_activity,
        produce_formal_report_activity,
    )

_RETRY = RetryPolicy(
    initial_interval=timedelta(milliseconds=100),
    backoff_coefficient=2,
    maximum_interval=timedelta(seconds=2),
    maximum_attempts=5,
)


@workflow.defn(name="FormalSnapshotReportWorkflowV2")
class FormalSnapshotReportWorkflowV2:
    """Validate immutable metric identity before any document renderer starts."""

    def __init__(self) -> None:
        self._review: dict[str, Any] | None = None
        self._state = "queued"

    @workflow.signal
    async def review(self, decision: dict[str, Any]) -> None:
        if self._review is None:
            self._review = decision

    @workflow.query
    def state(self) -> str:
        return self._state

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._state = "binding_snapshot"
        try:
            binding: dict[str, Any] = await workflow.execute_activity(
                validate_formal_metric_snapshot_binding_activity,
                payload,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY,
            )
            self._state = "preflight"
            await workflow.execute_activity(
                preflight_formal_report_runtime_activity,
                payload,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY,
            )
        except Exception as exc:
            self._state = "failed"
            activity_error = getattr(exc, "cause", None)
            error_type = getattr(activity_error, "type", None) or getattr(exc, "type", None)
            error_code = (
                "metric_snapshot_set_not_ready"
                if error_type == "metric_snapshot_set_not_ready"
                else "metric_snapshot_binding_failed"
            )
            return await workflow.execute_activity(
                fail_formal_report_activity,
                payload | {"error_code": error_code},
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY,
            )

        self._state = "running"
        try:
            produced: dict[str, Any] = await workflow.execute_activity(
                produce_formal_report_activity,
                payload,
                start_to_close_timeout=timedelta(minutes=30),
                retry_policy=_RETRY,
            )
        except Exception:
            self._state = "failed"
            failed: dict[str, Any] = await workflow.execute_activity(
                fail_formal_report_activity,
                payload | {"error_code": "production_failed"},
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY,
            )
            if failed.get("status") != "awaiting_review":
                return failed
            produced = failed
        if produced.get("status") == "failed":
            self._state = "failed"
            return produced

        self._state = "awaiting_review"
        await workflow.wait_condition(lambda: self._review is not None)
        assert self._review is not None
        self._state = "finalizing"
        finalized: dict[str, Any] = await workflow.execute_activity(
            finalize_formal_report_activity,
            payload | {"review": self._review},
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=_RETRY,
        )
        self._state = str(finalized["status"])
        return {
            **finalized,
            "binding": binding,
            "production": produced,
            "review": self._review,
        }


__all__ = ["FormalSnapshotReportWorkflowV2"]
