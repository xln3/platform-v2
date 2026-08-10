"""己方内容拉踩检测 workflow（``OwnContentDisparagementWorkflow``）。

SOP article version 定稿（publication_ready false→true）时由 API 服务层经
integration.workflow_start_command outbox 触发（workflow_type=
"own_content_disparagement"，钩子见 api/geo_platform/sop/service.py；dispatcher
分支在 api/geo_platform/collection/workflow_outbox.py，集成点）。

薄壳：单个 judge_own_content_disparagement activity——安静跳过/词典兜底/幂等
全部在 activity 内部（fail-open 纪律同 W3 sidecar）。新 workflow，无 replay 历史，
不需要 workflow.patched。
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from workflows.activities.own_content_disparagement import (
        OwnContentDisparagementInput,
        OwnContentDisparagementResult,
        judge_own_content_disparagement,
    )


@workflow.defn(name="OwnContentDisparagementWorkflow")
class OwnContentDisparagementWorkflow:
    @workflow.run
    async def run(
        self, data: OwnContentDisparagementInput
    ) -> OwnContentDisparagementResult:
        return await workflow.execute_activity(
            judge_own_content_disparagement,
            data,
            start_to_close_timeout=timedelta(minutes=30),
            heartbeat_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(
                maximum_attempts=2,
                non_retryable_error_types=["version_not_found", "tenant_not_found"],
            ),
        )
