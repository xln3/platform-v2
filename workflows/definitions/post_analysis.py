"""信源帖子取证分析 workflow（``PostAnalysisWorkflow``）。

需求规格：developlog/specs/post-analysis-20260806.md §5。
负载 ``{tenant_pub_id, task_pub_id}``：begin（task→running，装载 items）→ 每 item
顺序 fetch→analyze→annotate（跨 item 并发 2，``asyncio.gather`` 分批；单 item 失败
不拖垮 workflow——activity 内部已如实落 fetch_failed/analysis_failed/annotation
failed 并返回 ok=False）→ finalize（按 item 状态汇总 task=completed/partial/failed）。

重试纪律：瞬时错误 attempts=2；校验/上下文类错误（task_not_found/item_not_found/
post_text_missing/tenant_not_found）non-retryable。无 replay 历史（新 workflow，
不需要 workflow.patched）。
"""

from __future__ import annotations

import asyncio
from asyncio import CancelledError as AsyncioCancelledError
from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import CancelledError

with workflow.unsafe.imports_passed_through():
    from workflows.activities.post_analysis import (
        ANALYZE_MAX_ATTEMPTS,
        ANNOTATE_MAX_ATTEMPTS,
        FETCH_MAX_ATTEMPTS,
        AnalyzePostContentResult,
        AnnotatePostSnapshotResult,
        FetchPostSnapshotResult,
        PostAnalysisItemInput,
        PostAnalysisTaskInput,
        analyze_post_content,
        annotate_post_snapshot,
        begin_post_analysis_task,
        fetch_post_snapshot,
        finalize_post_analysis_task,
    )

# 瞬时错误重试（次数唯一真源在 activities/post_analysis.py 的 *_MAX_ATTEMPTS）；
# 校验/上下文类错误非重试（规格 §5）。fetch/annotate 是浏览器/网络活 → 3；
# analyze 调用内已有主备 base_url failover → 2。
_NON_RETRYABLE = [
    "task_not_found",
    "item_not_found",
    "post_text_missing",
    "tenant_not_found",
    "task_context_invalid",
]
_FETCH_RETRY = RetryPolicy(
    maximum_attempts=FETCH_MAX_ATTEMPTS, non_retryable_error_types=_NON_RETRYABLE
)
_ANALYZE_RETRY = RetryPolicy(
    maximum_attempts=ANALYZE_MAX_ATTEMPTS, non_retryable_error_types=_NON_RETRYABLE
)
_ANNOTATE_RETRY = RetryPolicy(
    maximum_attempts=ANNOTATE_MAX_ATTEMPTS, non_retryable_error_types=_NON_RETRYABLE
)
_DB_RETRY = RetryPolicy(maximum_attempts=5)

# 跨 item 并发上限（规格 §5：并发 2）
_ITEM_CONCURRENCY = 2

_FETCH_TIMEOUT = timedelta(seconds=180)
_ANALYZE_TIMEOUT = timedelta(seconds=300)
_ANNOTATE_TIMEOUT = timedelta(seconds=180)
_HEARTBEAT_TIMEOUT = timedelta(seconds=60)
_DB_TIMEOUT = timedelta(seconds=30)


@dataclass
class PostAnalysisInput:
    tenant_pub_id: str
    task_pub_id: str


@dataclass
class PostAnalysisItemOutcome:
    item_pub_id: str
    status: str  # completed / fetch_failed / analysis_failed / activity_error
    annotated: bool = False


@dataclass
class PostAnalysisResult:
    state: str  # completed / skipped
    task_status: str = ""
    items: int = 0
    outcomes: list[PostAnalysisItemOutcome] = field(default_factory=list)


@workflow.defn(name="PostAnalysisWorkflow")
class PostAnalysisWorkflow:
    @workflow.run
    async def run(self, data: PostAnalysisInput) -> PostAnalysisResult:
        task_input = PostAnalysisTaskInput(
            tenant_pub_id=data.tenant_pub_id, task_pub_id=data.task_pub_id
        )
        begin = await workflow.execute_activity(
            begin_post_analysis_task,
            task_input,
            start_to_close_timeout=_DB_TIMEOUT,
            retry_policy=_DB_RETRY,
        )
        if not begin.ok or begin.skipped is not None:
            # disabled / 无 item：不动 task 状态，如实返回
            return PostAnalysisResult(state="skipped", items=len(begin.item_pub_ids))
        outcomes: list[PostAnalysisItemOutcome] = []
        for start in range(0, len(begin.item_pub_ids), _ITEM_CONCURRENCY):
            batch = begin.item_pub_ids[start : start + _ITEM_CONCURRENCY]
            batch_outcomes = await asyncio.gather(
                *(self._process_item(data, item_pub_id) for item_pub_id in batch)
            )
            outcomes.extend(batch_outcomes)
        finalize = await workflow.execute_activity(
            finalize_post_analysis_task,
            task_input,
            start_to_close_timeout=_DB_TIMEOUT,
            retry_policy=_DB_RETRY,
        )
        return PostAnalysisResult(
            state="completed",
            task_status=finalize.status,
            items=len(begin.item_pub_ids),
            outcomes=outcomes,
        )

    async def _process_item(
        self, data: PostAnalysisInput, item_pub_id: str
    ) -> PostAnalysisItemOutcome:
        """单 item 流水线：fetch→analyze→annotate。任一步 ok=False/异常即终止本 item。"""
        item_input = PostAnalysisItemInput(
            tenant_pub_id=data.tenant_pub_id,
            task_pub_id=data.task_pub_id,
            item_pub_id=item_pub_id,
        )
        try:
            fetch: FetchPostSnapshotResult = await workflow.execute_activity(
                fetch_post_snapshot,
                item_input,
                start_to_close_timeout=_FETCH_TIMEOUT,
                heartbeat_timeout=_HEARTBEAT_TIMEOUT,
                retry_policy=_FETCH_RETRY,
            )
            if not fetch.ok:
                return PostAnalysisItemOutcome(item_pub_id=item_pub_id, status=fetch.status)
            analyze: AnalyzePostContentResult = await workflow.execute_activity(
                analyze_post_content,
                item_input,
                start_to_close_timeout=_ANALYZE_TIMEOUT,
                heartbeat_timeout=_HEARTBEAT_TIMEOUT,
                retry_policy=_ANALYZE_RETRY,
            )
            if not analyze.ok:
                return PostAnalysisItemOutcome(item_pub_id=item_pub_id, status=analyze.status)
            annotate: AnnotatePostSnapshotResult = await workflow.execute_activity(
                annotate_post_snapshot,
                item_input,
                start_to_close_timeout=_ANNOTATE_TIMEOUT,
                heartbeat_timeout=_HEARTBEAT_TIMEOUT,
                retry_policy=_ANNOTATE_RETRY,
            )
            # 标注失败不毁 analysis：item 已 completed，仅 annotation_status=failed
            return PostAnalysisItemOutcome(
                item_pub_id=item_pub_id, status="completed", annotated=annotate.annotated
            )
        except (AsyncioCancelledError, CancelledError):
            raise
        except Exception as exc:
            # activity 重试耗尽/非重试错误：item 状态由 activity 侧如实落库（或保持
            # 中间态由重跑收敛），workflow 继续处理其余 item
            workflow.logger.warning("post analysis item failed: %s %r", item_pub_id, exc)
            return PostAnalysisItemOutcome(item_pub_id=item_pub_id, status="activity_error")
