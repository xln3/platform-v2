"""AnswerAnalysisWorkflow 的 brandrank-extract-v1 patch 门测试（W3）。

覆盖三条语义：
1. patched 路径：分析后追加执行 extract_brands_activity，结果并入返回；
2. 侧车基础设施级失败（重试耗尽）：workflow 不失败，brand_extract=sidecar_failed
   （绝不阻塞分析主链）；
3. 重放兼容：未打补丁的旧 history（无 marker）用新代码重放——patched() 返回
   False，不排新 activity，Replayer 确定性校验通过（若排了新 activity，
   Replayer 会抛 nondeterminism）。

时间跳跃环境全 mock activity（零 PG 零 LLM）。legacy workflow 克隆定义在本测试
文件内，故用 UnsandboxedWorkflowRunner（避开 sandbox 对测试模块的重导入限制；
patched/marker 语义与 sandbox 无关，不受影响）。
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.client import WorkflowHistory
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from workflows.definitions.s02 import AnswerAnalysisWorkflow

_PAYLOAD: dict[str, Any] = {
    "persist": True,
    "tenant_pub_id": "tnt_patch",
    "project_pub_id": "prj_patch",
    "answer_pub_id": "ans_patch",
    "text": "推荐 Acme。",
    "brand": "Acme",
}

extract_calls: list[dict[str, Any]] = []
analyze_result = {"answer_pub_id": "ans_patch", "metrics": {"mention_rate": {"value": "1"}}}


@activity.defn(name="analyze_answer_activity")
async def analyze_answer_mock(payload: dict[str, Any]) -> dict[str, Any]:
    return dict(analyze_result)


@activity.defn(name="extract_brands_activity")
async def extract_brands_mock(payload: dict[str, Any]) -> dict[str, Any]:
    extract_calls.append(dict(payload))
    return {"state": "ok", "domain": "insurance", "model": "m-test", "brand_count": 2}


@activity.defn(name="extract_brands_activity")
async def extract_brands_boom_mock(payload: dict[str, Any]) -> dict[str, Any]:
    extract_calls.append(dict(payload))
    raise ApplicationError("pg down", type="infra_failure", non_retryable=True)


@workflow.defn(name="AnswerAnalysisWorkflow")
class LegacyAnswerAnalysisWorkflow:
    """patch 前的旧定义克隆（仅 analyze 一步）：用于产出无 marker 的旧 history。"""

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await workflow.execute_activity(
            analyze_answer_mock,
            payload,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(milliseconds=100),
                backoff_coefficient=2,
                maximum_interval=timedelta(seconds=2),
                maximum_attempts=5,
            ),
        )


async def test_patched_path_runs_extract_sidecar() -> None:
    extract_calls.clear()
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        queue = f"s02-patch-{uuid.uuid4().hex}"
        async with Worker(
            environment.client,
            task_queue=queue,
            workflows=[AnswerAnalysisWorkflow],
            activities=[analyze_answer_mock, extract_brands_mock],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await environment.client.execute_workflow(
                AnswerAnalysisWorkflow.run,
                dict(_PAYLOAD),
                id=f"answer-analysis/patch/{uuid.uuid4().hex}",
                task_queue=queue,
            )
    assert result["metrics"]["mention_rate"]["value"] == "1"
    assert result["brand_extract"] == {
        "state": "ok", "domain": "insurance", "model": "m-test", "brand_count": 2}
    assert extract_calls == [_PAYLOAD]


async def test_sidecar_infra_failure_does_not_fail_analysis() -> None:
    extract_calls.clear()
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        queue = f"s02-sidecar-{uuid.uuid4().hex}"
        async with Worker(
            environment.client,
            task_queue=queue,
            workflows=[AnswerAnalysisWorkflow],
            activities=[analyze_answer_mock, extract_brands_boom_mock],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await environment.client.execute_workflow(
                AnswerAnalysisWorkflow.run,
                dict(_PAYLOAD),
                id=f"answer-analysis/sidecar/{uuid.uuid4().hex}",
                task_queue=queue,
            )
    # 分析主链结果原样返回；侧车失败降级为如实标记
    assert result["metrics"]["mention_rate"]["value"] == "1"
    assert result["brand_extract"] == {"state": "sidecar_failed"}
    assert extract_calls == [_PAYLOAD]


async def test_unpatched_legacy_history_replays_without_extract() -> None:
    extract_calls.clear()
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        queue = f"s02-legacy-{uuid.uuid4().hex}"
        async with Worker(
            environment.client,
            task_queue=queue,
            workflows=[LegacyAnswerAnalysisWorkflow],
            activities=[analyze_answer_mock],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await environment.client.start_workflow(
                LegacyAnswerAnalysisWorkflow.run,
                dict(_PAYLOAD),
                id=f"answer-analysis/legacy/{uuid.uuid4().hex}",
                task_queue=queue,
            )
            legacy_result = await handle.result()
            history: WorkflowHistory = await handle.fetch_history()
    assert "brand_extract" not in legacy_result
    # 新代码重放旧 history：patched()=False → 不排 extract activity。
    # 若排了，Replayer 确定性校验会直接抛错（history 里无对应事件）。
    replayer = Replayer(
        workflows=[AnswerAnalysisWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(history)
    assert extract_calls == []
