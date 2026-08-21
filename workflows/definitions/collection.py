from asyncio import CancelledError as AsyncioCancelledError
from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, CancelledError

with workflow.unsafe.imports_passed_through():
    from collections.abc import Callable, Coroutine
    from typing import Any

    from workflows.activities.captcha_assist import (
        CaptchaAssistInput,
        CaptchaAssistStopInput,
        captcha_assist_start,
        captcha_assist_stop,
    )
    from workflows.activities.collection import (
        CaptchaPause,
        CollectionBatchInput,
        CollectionBatchItemResult,
        CollectionBatchResult,
        CollectionTaskInput,
        CollectionTaskResult,
        collect_deepseek_batch,
        collect_doubao_batch,
        collect_tongyi_batch,
        collect_with_adapter,
        collect_yiyan_batch,
        collect_yuanbao_batch,
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
    from workflows.activities.disparagement_factcheck import (
        FactcheckInput,
        factcheck_disparagement_cases,
    )
    from workflows.activities.own_site_snapshot import (
        OwnSiteSnapshotInput,
        capture_own_site_snapshots,
    )
    from workflows.activities.site_suggestions import (
        SiteSuggestionsInput,
        generate_site_audit_suggestions,
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

# captcha-assist-v1：撞码挂起等人工接管的上限与单 run（单 generation）挂起次数
# 护栏。等不到人工 ≠ 链路故障——超时/超限一律回退现行 wall+abort 语义。
CAPTCHA_ASSIST_WAIT_TIMEOUT = timedelta(minutes=60)
MAX_CAPTCHA_PAUSES_PER_RUN = 3


def gate_captcha_pause(
    patched: bool,
    pause: CaptchaPause | None,
    *,
    items: int,
    results: int,
) -> CaptchaPause | None:
    """captcha-assist-v1 门控纯函数（可单测）。

    workflow 侧 ``workflow.patched("captcha-assist-v1")`` 每次循环迭代只调一次
    传入本函数（重放确定性）；门控判定本身与 sandbox 解耦：

    - 未打补丁的历史重放 → 丢弃 pause（旧语义：等长结果原样全量落库）；
    - adapter 契约违背（resume_index 越界 / 结果与题数不等长）→ 不当 pause
      处理，丢弃后同样按旧语义全量落库；
    - 五平台 live adapter 的正常 pause 标注一律放行进入挂起接管分支——门只
      看 patch，不看平台 slug（2026-08-07 起非豆包四平台也产 pause 标记）。
    """
    if not patched or pause is None:
        return None
    if not (0 <= pause.resume_index < items and results == items):
        return None
    return pause


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
# 经 BATCH_ACTIVITY_BY_SLUG 的具名 callable 派发（**不用字符串名派发**——字符串
# 派发的结果只会转成 dict，workflow 任务随即异常并被 Temporal 无限重试，表现
# 为静默挂起，2026-08-06 实测坑）；不在词表的 slug（如测试用 "fixed"）保持
# per-task 老路径。
BATCH_CAPABLE_ADAPTERS = frozenset({"doubao", "deepseek", "tongyi", "yiyan", "yuanbao"})
ADAPTER_BATCH_MODE_SEGMENTS_PATCH = "adapter-batch-mode-segments-v3"
BATCH_ACTIVITY_BY_SLUG: dict[
    str, Callable[[CollectionBatchInput], Coroutine[Any, Any, CollectionBatchResult]]
] = {
    "doubao": collect_doubao_batch,
    "deepseek": collect_deepseek_batch,
    "tongyi": collect_tongyi_batch,
    "yiyan": collect_yiyan_batch,
    "yuanbao": collect_yuanbao_batch,
}


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


def plan_instance_segments(
    tasks: list[CollectionTaskInput],
) -> list[tuple[tuple[str, str], list[CollectionTaskInput]]]:
    """adapter-batch-collect-v2 分组：按原顺序把 tasks 切成连续段，
    ``((adapter_slug, region), items)``。

    浏览器矩阵化（2026-08-09 起）：batch 段是「同平台同地域」的连续任务——
    每段在 activity 侧经 browser_router 路由到对应该地域出口的常驻实例
    （如 doubao+CN-SH → doubao_sh）；同平台不同地域 = 不同 batch，各用各的
    实例。分组键用原始 region 串（workflow 侧不做 env 相关归一——归一在
    activity 侧纯函数里）；``CN-SH`` 与 ``上海`` 会分两段但都路由到同一实例，
    行为正确仅多一次分段。纯函数，workflow 重放确定。
    """
    segments: list[tuple[tuple[str, str], list[CollectionTaskInput]]] = []
    for item in tasks:
        key = ((item.adapter or "").strip().lower(), (item.region or "").strip())
        if segments and segments[-1][0] == key:
            segments[-1][1].append(item)
        else:
            segments.append((key, [item]))
    return segments


def plan_mode_instance_segments(
    tasks: list[CollectionTaskInput],
) -> list[tuple[tuple[str, str, str], list[CollectionTaskInput]]]:
    """新执行的 batch 分组：连续 ``(adapter, region, mode)`` 各自成段。

    mode 是账号治理/额度墙的一部分；把 normal 与 deep_think 放进同一 activity，
    任一模式不可用都会令整个 activity 在浏览器交互前失败，错误地连坐另一模式。
    因此 v3 在 v2 的实例键上追加 mode，保持题序但隔离模式级失败边界。
    """
    segments: list[tuple[tuple[str, str, str], list[CollectionTaskInput]]] = []
    for item in tasks:
        key = (
            (item.adapter or "").strip().lower(),
            (item.region or "").strip(),
            (item.mode or "").strip().lower(),
        )
        if segments and segments[-1][0] == key:
            segments[-1][1].append(item)
        else:
            segments.append((key, [item]))
    return segments


def plan_batch_segments(
    patched_v2: bool,
    tasks: list[CollectionTaskInput],
) -> list[tuple[str, list[CollectionTaskInput]]]:
    """adapter-batch-collect-v2 分组门控纯函数（可单测，与 gate_captcha_pause 同款）。

    workflow 侧 ``workflow.patched("adapter-batch-collect-v2")`` 在段循环前恰好
    调一次传入本函数（重放确定性）；分组判定本身与 sandbox 解耦：

    - 未打补丁的历史重放 → ``plan_adapter_segments`` 旧分组（仅按 adapter），
      旧 run 重放零变化；
    - 已 patch → ``plan_instance_segments`` 新分组（(adapter, region) 切段）。

    返回统一成 ``(adapter_slug, items)``，段循环体与 v1 完全一致。
    """
    if not patched_v2:
        return plan_adapter_segments(tasks)
    return [(key[0], items) for key, items in plan_instance_segments(tasks)]


def plan_versioned_batch_segments(
    patched_v2: bool,
    patched_mode_v3: bool,
    tasks: list[CollectionTaskInput],
) -> list[tuple[str, list[CollectionTaskInput]]]:
    """Preserve both published histories while enabling mode-isolated new batches.

    - pre-v2 replay: adapter-only grouping;
    - v2 history replay without the new marker: adapter+region grouping unchanged;
    - new v3 execution: adapter+region+mode grouping.

    ``plan_batch_segments`` remains untouched as the published v2 semantic anchor.
    """
    if not patched_mode_v3:
        return plan_batch_segments(patched_v2, tasks)
    return [(key[0], items) for key, items in plan_mode_instance_segments(tasks)]


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


def account_unavailable_reason(exc: BaseException) -> str | None:
    """batch activity 失败因子里的 account_unavailable 原因串；非治理不可用 → None。

    采集账号治理（2026-08-14 起，caiji-0813 §6.2）：browser_router 消费
    AccountGovernor 判定「该平台该地域有账号但全不可用」时 raise
    ApplicationError(type="account_unavailable", non_retryable=True)——activity
    在任何浏览器交互之前失败（不 attach、不撞墙、不回退 env），异常因子经
    ActivityError.cause 传到 workflow 侧。
    """
    cause = getattr(exc, "cause", None)
    if isinstance(cause, ApplicationError) and cause.type == "account_unavailable":
        return cause.message or str(cause)
    return None


def account_unavailable_placeholders(
    items: list[CollectionTaskInput], reason: str
) -> list[CollectionBatchItemResult]:
    """治理不可用段的等长占位（status=wall + error_type=account_unavailable）。

    照 captcha_pause 占位先例：与输入等长同序、走失败落库路径
    （state=failed / quality_state=error_type / answer_text=None）——不进
    fanout、不污染 analytics（dimensions 的 not_challenged/degraded=0 盖章只
    发生在 completed 答案上，占位行永远盖不到）。内容确定（无时间戳），
    activity 重试/重放的 drift 校验幂等。
    """
    return [
        CollectionBatchItemResult(
            business_key=item.business_key,
            status="wall",
            error_type="account_unavailable",
            error_message=reason,
        )
        for item in items
    ]


@workflow.defn
class GeoCollectionWorkflow:
    def __init__(self) -> None:
        self._paused = False
        self._cancelled = False
        self._intervention_completed = False
        self._intervention_nonce: str | None = None
        self._completed: list[CollectionTaskResult] = []
        # captcha-assist-v1：撞码挂起状态。session 关联 id 由 assist activity
        # 铸造并返回，signal 按它对准具体哪一次挂起（连撞时各次互不串扰）。
        self._captcha_solved_session: str | None = None
        self._captcha_pauses = 0

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

    @workflow.signal
    async def captcha_solved(self, session_id: str) -> None:
        """手机端确认验证码已解决（API assist done → outbox → 本 signal）。"""
        self._captcha_solved_session = session_id

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

    async def _persist_batch_results(
        self,
        data: GeoCollectionInput,
        items: list[CollectionTaskInput],
        results: list[CollectionBatchItemResult],
    ) -> list[str]:
        """逐题落库 batch 结果并记账 completed；返回 failures 描述串列表。

        items 与 results 必须等长同序；results 允许是 items 的前缀（captcha_pause
        时只落 resume_index 之前的题），超长即 adapter 契约违背（fail loud）。"""
        if len(results) > len(items):
            raise ApplicationError(
                f"batch results ({len(results)}) longer than items ({len(items)})",
                type="batch_outcome_contract_violation",
                non_retryable=True,
            )
        failures: list[str] = []
        for item, item_result in zip(items[: len(results)], results, strict=True):
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
        return failures

    async def _captcha_intervention(
        self, data: GeoCollectionInput, slug: str, pause: CaptchaPause
    ) -> str:
        """撞码挂起：起 assist 接管会话 + 等人工解决 signal。

        返回 "resumed"（人工已解，续跑）/ "fallback"（超时/超限/基建不可用，
        回退现行 wall+abort 语义）/ "cancelled"。assist 侧一切故障都只降级不
        阻断采集——撞码接管的代价绝不允许超过撞码本身。
        """
        self._captcha_pauses += 1
        if self._captcha_pauses > MAX_CAPTCHA_PAUSES_PER_RUN:
            workflow.logger.warning(
                "captcha pause limit (%d/run) exceeded; falling back to wall+abort",
                MAX_CAPTCHA_PAUSES_PER_RUN,
            )
            return "fallback"
        try:
            started = await workflow.execute_activity(
                captcha_assist_start,
                CaptchaAssistInput(
                    tenant_pub_id=data.tenant_pub_id,
                    run_pub_id=data.run_pub_id,
                    platform=slug,
                    business_key=pause.business_key,
                    evidence_ref=pause.evidence_ref,
                    # 浏览器矩阵化：assist 必须 attach 撞码 batch 的同一台常驻
                    # 实例（锁/CDP/fence 按实例键）；旧历史 pause 无此字段 →
                    # None → assist 回退按平台 slug（启用前行为，replay 安全）。
                    instance_key=pause.instance_key,
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    maximum_interval=timedelta(seconds=10),
                    maximum_attempts=2,
                    non_retryable_error_types=[
                        "assist_no_resident_browser",
                        "assist_not_configured",
                    ],
                ),
            )
        except Exception as exc:  # noqa: BLE001 — assist 基建不可用不阻断采集
            workflow.logger.warning("captcha_assist_start failed: %r; falling back", exc)
            return "fallback"
        solved = False
        try:
            await workflow.wait_condition(
                lambda: self._captcha_solved_session == started.session_id or self._cancelled,
                timeout=CAPTCHA_ASSIST_WAIT_TIMEOUT,
            )
            solved = self._captcha_solved_session == started.session_id
        except TimeoutError:
            solved = False  # 60 分钟无人接管——回退，挂起时长绝不无界
        try:
            await workflow.execute_activity(
                captcha_assist_stop,
                CaptchaAssistStopInput(run_pub_id=data.run_pub_id, session_id=started.session_id),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except Exception as exc:  # noqa: BLE001 — 注册表 TTL 自燃兜底，停桥失败不阻断续跑
            workflow.logger.warning("captcha_assist_stop failed: %r", exc)
        if self._cancelled:
            return "cancelled"
        return "resumed" if solved else "fallback"

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
        段合成一个 batch activity（run 级常驻浏览器会话），经
        BATCH_ACTIVITY_BY_SLUG 具名 callable 分发；非 batch 词表 slug（如测试
        "fixed"）保持 per-task 老调用。返回非 None 表示已提前终止（cancelled）。

        adapter-batch-collect-v2（2026-08-09，浏览器矩阵化）：按
        (adapter, region) 切段；未 patch 的历史重放仍按 adapter。

        adapter-batch-mode-segments-v3：新执行追加 mode 分段，隔离模式级额度墙。
        v2 marker 仍先调用且语义不变；新 marker 在其后恰好调用一次。v2-era 历史
        重放时新 marker=False，继续走原 (adapter, region) 分段。"""
        processed = 0
        patched_v2 = workflow.patched("adapter-batch-collect-v2")
        patched_mode_v3 = workflow.patched(ADAPTER_BATCH_MODE_SEGMENTS_PATCH)
        for slug, segment_items in plan_versioned_batch_segments(
            patched_v2,
            patched_mode_v3,
            data.tasks,
        ):
            if slug in BATCH_CAPABLE_ADAPTERS:
                await workflow.wait_condition(lambda: not self._paused or self._cancelled)
                if self._cancelled:
                    return GeoCollectionResult(state="cancelled", completed=self._completed)
                await self._inter_task_pacing(data, processed)
                if self._cancelled:
                    return GeoCollectionResult(state="cancelled", completed=self._completed)
                # captcha-assist-v1：撞码挂起→人工接管→断点续跑。remaining 随续跑
                # 截短重发（撞码题本身重采）；无撞码时循环一次即出，行为同旧路径。
                remaining_items = segment_items
                while remaining_items:
                    try:
                        batch_output = await workflow.execute_activity(
                            BATCH_ACTIVITY_BY_SLUG[slug],
                            CollectionBatchInput(
                                tenant_pub_id=data.tenant_pub_id,
                                run_pub_id=data.run_pub_id,
                                items=remaining_items,
                            ),
                            start_to_close_timeout=timedelta(
                                minutes=doubao_batch_timeout_minutes(
                                    len(remaining_items), data.activity_timeout_minutes
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
                                    "account_unavailable",
                                ],
                            ),
                        )
                    except ActivityError as exc:
                        unavailable = account_unavailable_reason(exc)
                        if unavailable is None:
                            raise
                        # 账号治理不可用（额度尽/禁言/region_down…）：整段落等长
                        # account_unavailable 占位——activity 在任何浏览器交互之前
                        # 失败（不 attach、不撞墙、不回退 env），占位诚实落库后继续
                        # 后续段；绝不整批 failed（2026-08-14 起，caiji-0813 §6.2）。
                        workflow.logger.warning(
                            "%s batch unavailable (account governance): %s", slug, unavailable
                        )
                        placeholders = account_unavailable_placeholders(
                            remaining_items, unavailable
                        )
                        await self._persist_batch_results(data, remaining_items, placeholders)
                        processed += len(placeholders)
                        break
                    # captcha-assist-v1 patch 每次循环迭代只调一次（重放确定性），
                    # 门控判定走纯函数 gate_captcha_pause：未 patch 的历史重放与
                    # 畸形 pause 一律丢弃按旧语义落库；五平台 live adapter 的正常
                    # pause 标注都进挂起接管分支，不再限 doubao。
                    captcha_assist_patched = workflow.patched("captcha-assist-v1")
                    pause = gate_captcha_pause(
                        captcha_assist_patched,
                        batch_output.captcha_pause,
                        items=len(remaining_items),
                        results=len(batch_output.results),
                    )
                    if (
                        pause is None
                        and batch_output.captcha_pause is not None
                        and captcha_assist_patched
                    ):
                        # adapter 契约违背：不当 pause 处理，按旧语义全量落库。
                        workflow.logger.warning(
                            "ignoring malformed captcha_pause "
                            "(resume_index=%d, items=%d, results=%d)",
                            batch_output.captcha_pause.resume_index,
                            len(remaining_items),
                            len(batch_output.results),
                        )
                    prefix = (
                        batch_output.results[: pause.resume_index]
                        if pause
                        else batch_output.results
                    )
                    failures = await self._persist_batch_results(data, remaining_items, prefix)
                    processed += len(prefix)
                    if pause is None:
                        if failures:
                            # 题级失败是数据不是工作流故障：失败/未执行题已诚实落库
                            # （含 aborted），run 终态由 persist 侧 _derive_run_state 推导
                            # 为 completed_with_failures（s04_0019 触发器词表）；继续走完
                            # 分析扇出与侧车，ok 题照常进分析——绝不硬闯、绝不编造。
                            workflow.logger.warning(
                                "%s batch finished with item failure(s): %s",
                                slug,
                                ", ".join(failures),
                            )
                        break
                    outcome = await self._captcha_intervention(data, slug, pause)
                    if outcome == "cancelled":
                        return GeoCollectionResult(state="cancelled", completed=self._completed)
                    if outcome == "resumed":
                        # 人工已解围：从撞码题起重采（本题重发，余题照旧）。
                        remaining_items = remaining_items[pause.resume_index :]
                        continue
                    # fallback：等不到人工——撞码题 wall + 余题 aborted 按现行
                    # 语义全量落库，run 终态推导与旧路径一致。
                    rest_failures = await self._persist_batch_results(
                        data,
                        remaining_items[pause.resume_index :],
                        batch_output.results[pause.resume_index :],
                    )
                    processed += len(batch_output.results) - pause.resume_index
                    workflow.logger.warning(
                        "%s captcha pause unresolved; wall+abort persisted: %s",
                        slug,
                        ", ".join(failures + rest_failures),
                    )
                    break
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
            analysis_detached = workflow.patched("collection-analysis-detached-v1")
            if analysis_detached:
                downstream = await workflow.execute_activity(
                    publish_downstream_event,
                    args=[data.run_pub_id, data.tenant_pub_id, data.tasks, True],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=5),
                )
            elif workflow.patched("collection-completion-analysis-fanout-v1"):
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
            if analysis_detached:
                # Every browser-only artifact is already durable. Public source
                # acquisition and all semantic/risk work now belong to the
                # separately queued PostCollectionAnalysisWorkflow, so return
                # immediately and let ``finally`` release the account lease.
                terminal_state = "completed"
                return GeoCollectionResult(
                    state="completed",
                    completed=self._completed,
                    downstream_event=downstream,
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
                    source_fetch_timeout = (
                        timedelta(minutes=60)
                        if workflow.patched("source-fetch-per-answer-v2")
                        else timedelta(minutes=10)
                    )
                    await workflow.execute_activity(
                        fetch_run_sources,
                        SourceFetchInput(
                            tenant_pub_id=data.tenant_pub_id,
                            project_pub_id=data.project_pub_id,
                            run_pub_id=data.run_pub_id,
                        ),
                        start_to_close_timeout=source_fetch_timeout,
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
            # 官网诊断建议侧车：仅当本 run 有 own_site 文档时产建议（activity 内部门控），
            # 紧跟 source-audit（建议输入=审计判定+官网正文要点）。fail-open 同其他侧车。
            if workflow.patched("site-suggestions-v1"):
                try:
                    await workflow.execute_activity(
                        generate_site_audit_suggestions,
                        SiteSuggestionsInput(
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
                    workflow.logger.warning("site suggestions sidecar failed: %r", exc)
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
                        start_to_close_timeout=timedelta(minutes=120),
                        heartbeat_timeout=timedelta(seconds=60),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )
                except (AsyncioCancelledError, CancelledError):
                    raise
                except Exception as exc:
                    workflow.logger.warning("disparagement sidecar failed: %r", exc)
            # W3 拉踩事实核查侧车：disparagement=true 判定逐条联网核查（T1），
            # 紧跟 disparagement 判定（核查输入=判定引文）。fail-open 同上。
            if workflow.patched("disparagement-factcheck-v1"):
                try:
                    await workflow.execute_activity(
                        factcheck_disparagement_cases,
                        FactcheckInput(
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
                    workflow.logger.warning("disparagement factcheck sidecar failed: %r", exc)
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
