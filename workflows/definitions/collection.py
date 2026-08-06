from asyncio import CancelledError as AsyncioCancelledError
from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError, CancelledError

with workflow.unsafe.imports_passed_through():
    from workflows.activities.collection import (
        CollectionBatchInput,
        CollectionBatchItemResult,
        CollectionTaskInput,
        CollectionTaskResult,
        collect_doubao_batch,
        collect_with_adapter,
        mark_collection_run_terminal,
        persist_collection_result,
        prepare_collection_session,
        publish_downstream_event,
        release_collection_session,
    )
    from workflows.activities.disparagement import (
        DisparagementInput,
        judge_run_disparagement,
    )
    from workflows.activities.own_site_snapshot import (
        OwnSiteSnapshotInput,
        capture_own_site_snapshots,
    )
    from workflows.activities.source_audit import (
        SourceAuditInput,
        audit_run_sources,
    )
    from workflows.activities.source_fetch import (
        SourceFetchInput,
        fetch_run_sources,
    )


@dataclass
class GeoCollectionInput:
    tenant_pub_id: str
    project_pub_id: str
    run_pub_id: str
    config_version_pub_id: str
    tasks: list[CollectionTaskInput]
    requires_intervention: bool = False
    persist_results: bool = True
    account_pub_id: str | None = None
    prior_completed: list[CollectionTaskResult] = field(default_factory=list)
    generation: int = 1
    history_batch_size: int = 100
    # collect_with_adapter 的 start_to_close 预算（分钟）。W1 起可配：deep_think
    # 流远长于 normal，旧 5 分钟硬编码放不下；缺省 15（API 侧 settings 注入）。
    activity_timeout_minutes: float = 15.0
    # 任务间拟人节奏（秒）：相邻任务随机间隔。机器节拍连发会被行为风控识别
    # （2026-08-06 生产实证）；max<=0 关闭。
    inter_task_delay_min_s: float = 45.0
    inter_task_delay_max_s: float = 150.0


@dataclass
class GeoCollectionResult:
    state: str
    completed: list[CollectionTaskResult] = field(default_factory=list)
    downstream_event: str | None = None


@dataclass(frozen=True)
class GeoCollectionStatus:
    paused: bool
    cancelled: bool
    intervention_completed: bool
    completed: int


def inter_task_delay_seconds(rand: float, index: int, min_s: float, max_s: float) -> float:
    """任务间拟人间隔：首个任务 0；其后 U[min_s, max(min_s, max_s)]；max_s<=0 关闭。"""
    if index <= 0 or max_s <= 0:
        return 0.0
    lo, hi = min_s, max(min_s, max_s)
    return lo + rand * (hi - lo)


# collect_doubao_batch 的 start_to_close 上限（分钟）：len(items)×per-item 预算，
# 封顶 120——一个常驻会话不宜无界拖长（heartbeat 30s 照旧）。
DOUBAO_BATCH_MAX_TIMEOUT_MINUTES = 120.0


def doubao_batch_timeout_minutes(item_count: int, per_item_minutes: float) -> float:
    """batch 超时公式：min(上限, 题数 × per-item 预算)。"""
    return min(DOUBAO_BATCH_MAX_TIMEOUT_MINUTES, max(1, item_count) * per_item_minutes)


def plan_collection_segments(
    tasks: list[CollectionTaskInput],
) -> list[tuple[bool, list[CollectionTaskInput]]]:
    """按原顺序把 tasks 切成连续段：``(is_doubao, items)``。

    doubao 连续段合成一个 batch 调用（保持原相对顺序）；非 doubao 段保持
    per-task 老路径（段内逐题）。纯函数，workflow 重放确定。
    """
    segments: list[tuple[bool, list[CollectionTaskInput]]] = []
    for item in tasks:
        is_doubao = (item.adapter or "").strip().lower() == "doubao"
        if segments and segments[-1][0] == is_doubao:
            segments[-1][1].append(item)
        else:
            segments.append((is_doubao, [item]))
    return segments


# batch 会话复用已推广到全部 live 平台（W8，2026-08-06）：每段按 adapter slug
# 动态调用 ``collect_<slug>_batch`` activity。不在词表的 slug（如测试用
# "fixed"）保持 per-task 老路径。
BATCH_CAPABLE_ADAPTERS = frozenset({"doubao", "deepseek", "tongyi", "yiyan", "yuanbao"})


def plan_adapter_segments(
    tasks: list[CollectionTaskInput],
) -> list[tuple[str, list[CollectionTaskInput]]]:
    """按原顺序把 tasks 切成连续段：``(adapter_slug, items)``。纯函数。"""
    segments: list[tuple[str, list[CollectionTaskInput]]] = []
    for item in tasks:
        slug = (item.adapter or "").strip().lower()
        if segments and segments[-1][0] == slug:
            segments[-1][1].append(item)
        else:
            segments.append((slug, [item]))
    return segments


def task_result_from_batch_item(item_result: CollectionBatchItemResult) -> CollectionTaskResult:
    """batch ok 题 → GeoCollectionResult.completed 的 CollectionTaskResult 形状。"""
    return CollectionTaskResult(
        business_key=item_result.business_key,
        answer_text=item_result.answer_text or "",
        screenshot_ref=item_result.screenshot_ref or "",
        quality_state=item_result.quality_state or "",
        citations=list(item_result.citations),
        evidence=list(item_result.evidence),
        search_queries=list(item_result.search_queries),
    )


@workflow.defn
class GeoCollectionWorkflow:
    def __init__(self) -> None:
        self._paused = False
        self._cancelled = False
        self._intervention_completed = False
        self._intervention_nonce: str | None = None
        self._completed: list[CollectionTaskResult] = []

    @workflow.signal
    async def pause(self) -> None:
        self._paused = True

    @workflow.signal
    async def resume(self) -> None:
        self._paused = False

    @workflow.signal
    async def cancel(self) -> None:
        self._cancelled = True

    @workflow.signal
    async def complete_intervention(self, nonce: str) -> None:
        if self._intervention_nonce == nonce:
            return
        self._intervention_nonce = nonce
        self._intervention_completed = True

    @workflow.query
    def status(self) -> GeoCollectionStatus:
        return GeoCollectionStatus(
            paused=self._paused,
            cancelled=self._cancelled,
            intervention_completed=self._intervention_completed,
            completed=len(self._completed),
        )

    async def _inter_task_pacing(self, data: GeoCollectionInput, index: int) -> None:
        """任务间拟人节奏：分片睡眠，cancel 信号 15s 内可响应（新路径专用）。

        patched 门：老历史的重放不含此节点，必须走旧路径保确定性。批次之间
        仍由本节奏承担；batch 内题间由浏览器侧「阅读停顿」取代。
        """
        if not workflow.patched("inter-task-pacing-v1"):
            return
        delay = inter_task_delay_seconds(
            workflow.random().random(),
            index,
            data.inter_task_delay_min_s,
            data.inter_task_delay_max_s,
        )
        remaining = delay
        while remaining > 0 and not self._cancelled:
            try:
                await workflow.wait_condition(
                    lambda: self._cancelled,
                    timeout=timedelta(seconds=min(15.0, remaining)),
                )
            except TimeoutError:
                pass  # 分片到点未取消——继续下一片
            remaining -= 15.0

    def _continuation_input(self, data: GeoCollectionInput, processed: int) -> GeoCollectionInput:
        return GeoCollectionInput(
            tenant_pub_id=data.tenant_pub_id,
            project_pub_id=data.project_pub_id,
            run_pub_id=data.run_pub_id,
            config_version_pub_id=data.config_version_pub_id,
            tasks=data.tasks[processed:],
            requires_intervention=False,
            persist_results=data.persist_results,
            account_pub_id=data.account_pub_id,
            prior_completed=self._completed,
            generation=data.generation + 1,
            history_batch_size=data.history_batch_size,
            activity_timeout_minutes=data.activity_timeout_minutes,
            inter_task_delay_min_s=data.inter_task_delay_min_s,
            inter_task_delay_max_s=data.inter_task_delay_max_s,
        )

    async def _collect_tasks_batched(self, data: GeoCollectionInput) -> GeoCollectionResult | None:
        """doubao-batch-collect-v1 新路径：doubao 连续段合成一个 batch activity
        （run 级常驻浏览器会话，题间浏览器侧阅读停顿），其他任务保持 per-task
        collect_with_adapter 老调用。返回非 None 表示已提前终止（cancelled）。"""
        processed = 0
        for is_doubao, segment_items in plan_collection_segments(data.tasks):
            if is_doubao:
                await workflow.wait_condition(lambda: not self._paused or self._cancelled)
                if self._cancelled:
                    return GeoCollectionResult(state="cancelled", completed=self._completed)
                await self._inter_task_pacing(data, processed)
                if self._cancelled:
                    return GeoCollectionResult(state="cancelled", completed=self._completed)
                batch_output = await workflow.execute_activity(
                    collect_doubao_batch,
                    CollectionBatchInput(
                        tenant_pub_id=data.tenant_pub_id,
                        run_pub_id=data.run_pub_id,
                        items=segment_items,
                    ),
                    start_to_close_timeout=timedelta(
                        minutes=doubao_batch_timeout_minutes(
                            len(segment_items), data.activity_timeout_minutes
                        )
                    ),
                    heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=1),
                        maximum_interval=timedelta(seconds=30),
                        maximum_attempts=2,
                        non_retryable_error_types=[
                            "adapter_not_configured",
                            "unsupported_mode",
                        ],
                    ),
                )
                failures: list[str] = []
                for item, item_result in zip(segment_items, batch_output.results, strict=True):
                    if item_result.status == "ok":
                        self._completed.append(task_result_from_batch_item(item_result))
                    else:
                        failures.append(
                            f"{item.business_key}:{item_result.error_type or item_result.status}"
                        )
                    if data.persist_results:
                        await workflow.execute_activity(
                            persist_collection_result,
                            args=[data.tenant_pub_id, data.run_pub_id, item_result, item],
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=RetryPolicy(maximum_attempts=10),
                        )
                    processed += 1
                if failures:
                    # 真人撞墙后会停下：失败/未执行题已诚实落库（含 aborted），
                    # run 终态词汇与 per-task 时代墙失败一致（failed），绝不硬闯
                    # 后续题、绝不编造未执行题的结果。
                    raise ApplicationError(
                        "doubao batch stopped after item failure(s): " + ", ".join(failures),
                        type="doubao_batch_item_failed",
                        non_retryable=True,
                    )
                if processed >= data.history_batch_size and processed < len(data.tasks):
                    workflow.continue_as_new(self._continuation_input(data, processed))
            else:
                for item in segment_items:
                    await workflow.wait_condition(lambda: not self._paused or self._cancelled)
                    if self._cancelled:
                        return GeoCollectionResult(state="cancelled", completed=self._completed)
                    await self._inter_task_pacing(data, processed)
                    if self._cancelled:
                        return GeoCollectionResult(state="cancelled", completed=self._completed)
                    result = await workflow.execute_activity(
                        collect_with_adapter,
                        item,
                        start_to_close_timeout=timedelta(minutes=data.activity_timeout_minutes),
                        heartbeat_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(
                            initial_interval=timedelta(seconds=1),
                            maximum_interval=timedelta(seconds=30),
                            maximum_attempts=5,
                            non_retryable_error_types=["unsupported_adapter"],
                        ),
                    )
                    self._completed.append(result)
                    if data.persist_results:
                        await workflow.execute_activity(
                            persist_collection_result,
                            args=[data.tenant_pub_id, data.run_pub_id, result, item],
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=RetryPolicy(maximum_attempts=10),
                        )
                    processed += 1
                    if processed >= data.history_batch_size and processed < len(data.tasks):
                        workflow.continue_as_new(self._continuation_input(data, processed))
        return None

    async def _collect_tasks_batched_all(
        self, data: GeoCollectionInput
    ) -> GeoCollectionResult | None:
        """adapter-batch-collect-v1 新路径（W8）：所有 live 平台的连续同 adapter
        段合成一个 batch activity（run 级常驻浏览器会话），动态名
        ``collect_<slug>_batch`` 分发；非 batch 词表 slug（如测试 "fixed"）
        保持 per-task 老调用。返回非 None 表示已提前终止（cancelled）。"""
        processed = 0
        for slug, segment_items in plan_adapter_segments(data.tasks):
            if slug in BATCH_CAPABLE_ADAPTERS:
                await workflow.wait_condition(lambda: not self._paused or self._cancelled)
                if self._cancelled:
                    return GeoCollectionResult(state="cancelled", completed=self._completed)
                await self._inter_task_pacing(data, processed)
                if self._cancelled:
                    return GeoCollectionResult(state="cancelled", completed=self._completed)
                batch_output = await workflow.execute_activity(
                    f"collect_{slug}_batch",
                    CollectionBatchInput(
                        tenant_pub_id=data.tenant_pub_id,
                        run_pub_id=data.run_pub_id,
                        items=segment_items,
                    ),
                    start_to_close_timeout=timedelta(
                        minutes=doubao_batch_timeout_minutes(
                            len(segment_items), data.activity_timeout_minutes
                        )
                    ),
                    heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=1),
                        maximum_interval=timedelta(seconds=30),
                        maximum_attempts=2,
                        non_retryable_error_types=[
                            "adapter_not_configured",
                            "unsupported_mode",
                            "batch_outcome_contract_violation",
                        ],
                    ),
                )
                failures: list[str] = []
                for item, item_result in zip(segment_items, batch_output.results, strict=True):
                    if item_result.status == "ok":
                        self._completed.append(task_result_from_batch_item(item_result))
                    else:
                        failures.append(
                            f"{item.business_key}:{item_result.error_type or item_result.status}"
                        )
                    if data.persist_results:
                        await workflow.execute_activity(
                            persist_collection_result,
                            args=[data.tenant_pub_id, data.run_pub_id, item_result, item],
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=RetryPolicy(maximum_attempts=10),
                        )
                    processed += 1
                if failures:
                    # 真人撞墙后会停下：失败/未执行题已诚实落库（含 aborted），
                    # run 终态词汇与 per-task 时代墙失败一致（failed），绝不硬闯。
                    raise ApplicationError(
                        f"{slug} batch stopped after item failure(s): " + ", ".join(failures),
                        type=f"{slug}_batch_item_failed",
                        non_retryable=True,
                    )
                if processed >= data.history_batch_size and processed < len(data.tasks):
                    workflow.continue_as_new(self._continuation_input(data, processed))
            else:
                for item in segment_items:
                    await workflow.wait_condition(lambda: not self._paused or self._cancelled)
                    if self._cancelled:
                        return GeoCollectionResult(state="cancelled", completed=self._completed)
                    await self._inter_task_pacing(data, processed)
                    if self._cancelled:
                        return GeoCollectionResult(state="cancelled", completed=self._completed)
                    result = await workflow.execute_activity(
                        collect_with_adapter,
                        item,
                        start_to_close_timeout=timedelta(minutes=data.activity_timeout_minutes),
                        heartbeat_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(
                            initial_interval=timedelta(seconds=1),
                            maximum_interval=timedelta(seconds=30),
                            maximum_attempts=5,
                            non_retryable_error_types=["unsupported_adapter"],
                        ),
                    )
                    self._completed.append(result)
                    if data.persist_results:
                        await workflow.execute_activity(
                            persist_collection_result,
                            args=[data.tenant_pub_id, data.run_pub_id, result, item],
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=RetryPolicy(maximum_attempts=10),
                        )
                    processed += 1
                    if processed >= data.history_batch_size and processed < len(data.tasks):
                        workflow.continue_as_new(self._continuation_input(data, processed))
        return None

    async def _collect_tasks_per_task(self, data: GeoCollectionInput) -> GeoCollectionResult | None:
        """per-task 老路径（doubao-batch-collect-v1 patch 门前的历史重放专用，
        逻辑原样保留）。返回非 None 表示已提前终止（cancelled）。"""
        for index, item in enumerate(data.tasks):
            await workflow.wait_condition(lambda: not self._paused or self._cancelled)
            if self._cancelled:
                return GeoCollectionResult(state="cancelled", completed=self._completed)
            # 任务间拟人节奏：分片睡眠，cancel 信号 15s 内可响应。
            # patched 门：老历史的重放不含此节点，必须走旧路径保确定性。
            if workflow.patched("inter-task-pacing-v1"):
                delay = inter_task_delay_seconds(
                    workflow.random().random(),
                    index,
                    data.inter_task_delay_min_s,
                    data.inter_task_delay_max_s,
                )
                remaining = delay
                while remaining > 0 and not self._cancelled:
                    try:
                        await workflow.wait_condition(
                            lambda: self._cancelled,
                            timeout=timedelta(seconds=min(15.0, remaining)),
                        )
                    except TimeoutError:
                        pass  # 分片到点未取消——继续下一片
                    remaining -= 15.0
            if self._cancelled:
                return GeoCollectionResult(state="cancelled", completed=self._completed)
            result = await workflow.execute_activity(
                collect_with_adapter,
                item,
                start_to_close_timeout=timedelta(minutes=data.activity_timeout_minutes),
                heartbeat_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    maximum_interval=timedelta(seconds=30),
                    maximum_attempts=5,
                    non_retryable_error_types=["unsupported_adapter"],
                ),
            )
            self._completed.append(result)
            if data.persist_results:
                if workflow.patched("collection-result-matrix-v1"):
                    await workflow.execute_activity(
                        persist_collection_result,
                        args=[data.tenant_pub_id, data.run_pub_id, result, item],
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(maximum_attempts=10),
                    )
                else:
                    await workflow.execute_activity(
                        persist_collection_result,
                        args=[data.tenant_pub_id, data.run_pub_id, result],
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(maximum_attempts=10),
                    )
            if index + 1 >= data.history_batch_size and index + 1 < len(data.tasks):
                workflow.continue_as_new(self._continuation_input(data, index + 1))
        return None

    @workflow.run
    async def run(self, data: GeoCollectionInput) -> GeoCollectionResult:
        self._completed.extend(data.prior_completed)
        session_preparation = None
        terminal_state: str | None = None
        terminal_error_code: str | None = None
        try:
            if data.account_pub_id:
                session_preparation = await workflow.execute_activity(
                    prepare_collection_session,
                    args=[data.tenant_pub_id, data.account_pub_id, data.run_pub_id, "query"],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(
                        maximum_attempts=3,
                        non_retryable_error_types=[
                            "account_not_active",
                            "scope_not_authorized",
                            "active_profile_not_found",
                        ],
                    ),
                )
            if data.requires_intervention:
                await workflow.wait_condition(
                    lambda: self._intervention_completed or self._cancelled,
                    timeout=timedelta(days=7),
                )
            if self._cancelled:
                terminal_state = "cancelled"
                return GeoCollectionResult(state="cancelled", completed=self._completed)
            if workflow.patched("adapter-batch-collect-v1"):
                early_result = await self._collect_tasks_batched_all(data)
            elif workflow.patched("doubao-batch-collect-v1"):
                early_result = await self._collect_tasks_batched(data)
            else:
                early_result = await self._collect_tasks_per_task(data)
            if early_result is not None:
                terminal_state = "cancelled"
                return early_result
            if workflow.patched("collection-completion-analysis-fanout-v1"):
                downstream = await workflow.execute_activity(
                    publish_downstream_event,
                    args=[data.run_pub_id, data.tenant_pub_id, data.tasks],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=5),
                )
            elif workflow.patched("durable-collection-completion-outbox-v1"):
                downstream = await workflow.execute_activity(
                    publish_downstream_event,
                    args=[data.run_pub_id, data.tenant_pub_id],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=5),
                )
            else:
                downstream = await workflow.execute_activity(
                    publish_downstream_event,
                    data.run_pub_id,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=5),
                )
            # W4 官网素材快照：证据侧车，随 run 顺带执行。快照失败（浏览器/MinIO
            # 故障）不得拖垮采集 run——activity 内部已如实记录 failures，这里只对
            # 基础设施级异常记 warning。是否启用由 activity 侧
            # GEO_OWN_SITE_SNAPSHOT_ENABLED 判定（skipped="disabled" 零 IO 返回）。
            if workflow.patched("own-site-snapshot-v1"):
                try:
                    await workflow.execute_activity(
                        capture_own_site_snapshots,
                        OwnSiteSnapshotInput(
                            tenant_pub_id=data.tenant_pub_id,
                            project_pub_id=data.project_pub_id,
                            run_pub_id=data.run_pub_id,
                        ),
                        start_to_close_timeout=timedelta(minutes=10),
                        heartbeat_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(maximum_attempts=2),
                    )
                except (AsyncioCancelledError, CancelledError):
                    raise
                except Exception as exc:
                    workflow.logger.warning("own-site snapshot sidecar failed: %r", exc)
            # W2 信源抓取+核对侧车：同样不得拖垮采集 run；activity 内部幂等+如实状态。
            if workflow.patched("source-fetch-v1"):
                try:
                    await workflow.execute_activity(
                        fetch_run_sources,
                        SourceFetchInput(
                            tenant_pub_id=data.tenant_pub_id,
                            project_pub_id=data.project_pub_id,
                            run_pub_id=data.run_pub_id,
                        ),
                        start_to_close_timeout=timedelta(minutes=10),
                        heartbeat_timeout=timedelta(seconds=60),
                        retry_policy=RetryPolicy(maximum_attempts=2),
                    )
                except (AsyncioCancelledError, CancelledError):
                    raise
                except Exception as exc:
                    workflow.logger.warning("source fetch sidecar failed: %r", exc)
            if workflow.patched("source-audit-v1"):
                try:
                    await workflow.execute_activity(
                        audit_run_sources,
                        SourceAuditInput(
                            tenant_pub_id=data.tenant_pub_id,
                            project_pub_id=data.project_pub_id,
                            run_pub_id=data.run_pub_id,
                        ),
                        start_to_close_timeout=timedelta(minutes=15),
                        heartbeat_timeout=timedelta(seconds=60),
                        retry_policy=RetryPolicy(maximum_attempts=2),
                    )
                except (AsyncioCancelledError, CancelledError):
                    raise
                except Exception as exc:
                    workflow.logger.warning("source audit sidecar failed: %r", exc)
            # W3 拉踩判定侧车：窗级 LLM 判定（LLM 不可用走词典兜底并标 experimental）。
            if workflow.patched("disparagement-v1"):
                try:
                    await workflow.execute_activity(
                        judge_run_disparagement,
                        DisparagementInput(
                            tenant_pub_id=data.tenant_pub_id,
                            project_pub_id=data.project_pub_id,
                            run_pub_id=data.run_pub_id,
                        ),
                        start_to_close_timeout=timedelta(minutes=30),
                        heartbeat_timeout=timedelta(seconds=60),
                        retry_policy=RetryPolicy(maximum_attempts=2),
                    )
                except (AsyncioCancelledError, CancelledError):
                    raise
                except Exception as exc:
                    workflow.logger.warning("disparagement sidecar failed: %r", exc)
            terminal_state = "completed"
            return GeoCollectionResult(
                state="completed", completed=self._completed, downstream_event=downstream
            )
        except (AsyncioCancelledError, CancelledError):
            terminal_state = "cancelled"
            terminal_error_code = "workflow_cancelled"
            raise
        except Exception:
            terminal_state = "failed"
            terminal_error_code = "workflow_failed"
            raise
        finally:
            if terminal_state:
                await workflow.execute_activity(
                    mark_collection_run_terminal,
                    args=[
                        data.tenant_pub_id,
                        data.run_pub_id,
                        terminal_state,
                        terminal_error_code,
                    ],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=10),
                )
            # A lease is account-exclusive authority, not a cache entry. Release
            # it after success, signal cancellation, Activity/persistence failure,
            # timeout and Continue-As-New. The Activity is idempotent so replay is
            # safe; retrying cleanup is preferable to waiting for the lease TTL.
            if session_preparation:
                await workflow.execute_activity(
                    release_collection_session,
                    args=[
                        data.tenant_pub_id,
                        session_preparation.lease_pub_id,
                        session_preparation.fencing_token,
                    ],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=10),
                )
