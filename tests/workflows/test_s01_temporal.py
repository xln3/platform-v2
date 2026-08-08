import asyncio
import hashlib

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from workflows.activities.captcha_assist import (
    CaptchaAssistInput,
    CaptchaAssistStarted,
    CaptchaAssistStopInput,
)
from workflows.activities.collection import (
    CaptchaPause,
    CollectionBatchInput,
    CollectionBatchItemResult,
    CollectionBatchResult,
    CollectionTaskInput,
    CollectionTaskResult,
    SessionPreparation,
)
from workflows.definitions.collection import GeoCollectionInput, GeoCollectionWorkflow
from workflows.definitions.session import (
    HumanInterventionWorkflow,
    PlatformSessionLifecycleWorkflow,
    SessionLifecycleInput,
)


@activity.defn(name="collect_with_adapter")
async def collect_with_adapter(item: CollectionTaskInput) -> CollectionTaskResult:
    """Deterministic contract fixture registered only by this test worker."""
    activity.heartbeat({"business_key": item.business_key, "stage": "fixture_started"})
    if activity.info().attempt <= item.fail_until_attempt:
        raise RuntimeError("fixture_injected_retryable_failure")
    digest = hashlib.sha256(
        f"{item.query}|{item.model}|{item.region}|{item.mode}".encode()
    ).hexdigest()
    return CollectionTaskResult(
        business_key=item.business_key,
        answer_text=f"[test-fixture] {item.query}",
        screenshot_ref=f"fixture://screenshots/{digest}.png",
        quality_state="fixture_valid",
    )


lease_preparations: list[str] = []
lease_releases: list[tuple[str, str, int]] = []
terminal_run_states: list[tuple[str, str, str, str | None]] = []


@activity.defn(name="publish_downstream_event")
async def publish_downstream_event(
    run_pub_id: str,
    tenant_pub_id: str | None = None,
    task_inputs: list[CollectionTaskInput] | None = None,
) -> str:
    del tenant_pub_id, task_inputs
    return f"collection.completed:{run_pub_id}"


@activity.defn(name="prepare_collection_session")
async def prepare_collection_session(
    tenant_pub_id: str, account_pub_id: str, holder: str, required_scope: str
) -> SessionPreparation:
    del account_pub_id, holder, required_scope
    lease_preparations.append(tenant_pub_id)
    return SessionPreparation(
        lease_pub_id="sle_cleanup_fixture",
        fencing_token=41,
        profile_version=3,
    )


@activity.defn(name="release_collection_session")
async def release_collection_session(
    tenant_pub_id: str, lease_pub_id: str, fencing_token: int
) -> None:
    lease_releases.append((tenant_pub_id, lease_pub_id, fencing_token))


@activity.defn(name="mark_collection_run_terminal")
async def mark_collection_run_terminal(
    tenant_pub_id: str, run_pub_id: str, state: str, error_code: str | None
) -> None:
    terminal_run_states.append((tenant_pub_id, run_pub_id, state, error_code))


@activity.defn(name="collect_with_adapter")
async def collect_with_nonretryable_failure(
    item: CollectionTaskInput,
) -> CollectionTaskResult:
    del item
    raise ApplicationError(
        "fixture adapter rejected task",
        type="unsupported_adapter",
        non_retryable=True,
    )


async def test_collection_fixed_adapter_and_duplicate_intervention_signal() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-test",
            workflows=[GeoCollectionWorkflow, HumanInterventionWorkflow],
            activities=[
                collect_with_adapter,
                publish_downstream_event,
                mark_collection_run_terminal,
            ],
        ):
            handle = await environment.client.start_workflow(
                HumanInterventionWorkflow.run,
                "int_test",
                id="human-intervention/test",
                task_queue="s01-test",
            )
            await handle.signal(HumanInterventionWorkflow.complete, args=["nonce-1", "verified"])
            await handle.signal(HumanInterventionWorkflow.complete, args=["nonce-1", "verified"])
            await handle.signal(HumanInterventionWorkflow.complete, args=["nonce-2", "failed"])
            assert await handle.result() == "verified"


async def test_activity_failure_is_retried_and_workflow_recovers() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-retry-test",
            workflows=[GeoCollectionWorkflow],
            activities=[
                collect_with_adapter,
                publish_downstream_event,
                mark_collection_run_terminal,
            ],
        ):
            result = await environment.client.execute_workflow(
                GeoCollectionWorkflow.run,
                GeoCollectionInput(
                    tenant_pub_id="tnt_test",
                    project_pub_id="prj_test",
                    run_pub_id="run_test",
                    config_version_pub_id="cfv_test",
                    tasks=[
                        CollectionTaskInput(
                            business_key="retry-key",
                            query="retry me",
                            model="fixed",
                            region="CN-BJ",
                            mode="fast",
                            fail_until_attempt=1,
                        )
                    ],
                    persist_results=False,
                ),
                id="geo-collection/retry-test",
                task_queue="s01-retry-test",
            )
            assert result.state == "completed"
            assert result.completed[0].quality_state == "fixture_valid"


async def test_nonretryable_activity_failure_releases_fenced_session_lease() -> None:
    lease_preparations.clear()
    lease_releases.clear()
    terminal_run_states.clear()
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-failure-cleanup-test",
            workflows=[GeoCollectionWorkflow],
            activities=[
                prepare_collection_session,
                release_collection_session,
                collect_with_nonretryable_failure,
                mark_collection_run_terminal,
                publish_downstream_event,
            ],
        ):
            with pytest.raises(WorkflowFailureError):
                await environment.client.execute_workflow(
                    GeoCollectionWorkflow.run,
                    GeoCollectionInput(
                        tenant_pub_id="tnt_cleanup",
                        project_pub_id="prj_cleanup",
                        run_pub_id="run_cleanup",
                        config_version_pub_id="cfv_cleanup",
                        account_pub_id="pac_cleanup",
                        tasks=[
                            CollectionTaskInput(
                                business_key="cleanup-key",
                                query="must fail",
                                model="fixed",
                                region="CN-BJ",
                                mode="fast",
                            )
                        ],
                        persist_results=False,
                    ),
                    id="geo-collection/failure-cleanup-test",
                    task_queue="s01-failure-cleanup-test",
                )
    assert lease_preparations == ["tnt_cleanup"]
    assert lease_releases == [("tnt_cleanup", "sle_cleanup_fixture", 41)]
    assert terminal_run_states == [("tnt_cleanup", "run_cleanup", "failed", "workflow_failed")]


async def test_external_workflow_cancellation_releases_fenced_session_lease() -> None:
    lease_preparations.clear()
    lease_releases.clear()
    terminal_run_states.clear()
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-external-cancel-cleanup-test",
            workflows=[GeoCollectionWorkflow],
            activities=[
                prepare_collection_session,
                release_collection_session,
                collect_with_adapter,
                mark_collection_run_terminal,
                publish_downstream_event,
            ],
        ):
            handle = await environment.client.start_workflow(
                GeoCollectionWorkflow.run,
                GeoCollectionInput(
                    tenant_pub_id="tnt_cancel_cleanup",
                    project_pub_id="prj_cancel_cleanup",
                    run_pub_id="run_cancel_cleanup",
                    config_version_pub_id="cfv_cancel_cleanup",
                    account_pub_id="pac_cancel_cleanup",
                    tasks=[],
                    requires_intervention=True,
                    persist_results=False,
                ),
                id="geo-collection/external-cancel-cleanup-test",
                task_queue="s01-external-cancel-cleanup-test",
            )
            for _ in range(100):
                if lease_preparations:
                    break
                await asyncio.sleep(0.01)
            assert lease_preparations == ["tnt_cancel_cleanup"]
            await handle.cancel()
            with pytest.raises(WorkflowFailureError):
                await handle.result()
    assert lease_releases == [("tnt_cancel_cleanup", "sle_cleanup_fixture", 41)]
    assert terminal_run_states == [
        (
            "tnt_cancel_cleanup",
            "run_cancel_cleanup",
            "cancelled",
            "workflow_cancelled",
        )
    ]


async def test_pause_resume_cancel_signal_is_durable_and_idempotent() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-cancel-test",
            workflows=[GeoCollectionWorkflow],
            activities=[
                collect_with_adapter,
                publish_downstream_event,
                mark_collection_run_terminal,
            ],
        ):
            handle = await environment.client.start_workflow(
                GeoCollectionWorkflow.run,
                GeoCollectionInput(
                    tenant_pub_id="tnt_test",
                    project_pub_id="prj_test",
                    run_pub_id="run_cancel",
                    config_version_pub_id="cfv_test",
                    tasks=[],
                    requires_intervention=True,
                    persist_results=False,
                ),
                id="geo-collection/cancel-test",
                task_queue="s01-cancel-test",
            )
            await handle.signal(GeoCollectionWorkflow.pause)
            await handle.signal(GeoCollectionWorkflow.pause)
            await handle.signal(GeoCollectionWorkflow.cancel)
            assert (await handle.result()).state == "cancelled"


async def test_continue_as_new_preserves_results_and_business_keys() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-continue-test",
            workflows=[GeoCollectionWorkflow],
            activities=[
                collect_with_adapter,
                publish_downstream_event,
                mark_collection_run_terminal,
            ],
        ):
            result = await environment.client.execute_workflow(
                GeoCollectionWorkflow.run,
                GeoCollectionInput(
                    tenant_pub_id="tnt_test",
                    project_pub_id="prj_test",
                    run_pub_id="run_continue",
                    config_version_pub_id="cfv_test",
                    tasks=[
                        CollectionTaskInput(
                            business_key=f"continue-key-{index}",
                            query=f"query {index}",
                            model="fixed",
                            region="CN-BJ",
                            mode="fast",
                        )
                        for index in range(2)
                    ],
                    persist_results=False,
                    history_batch_size=1,
                ),
                id="geo-collection/continue-test",
                task_queue="s01-continue-test",
            )
            assert result.state == "completed"
            assert [item.business_key for item in result.completed] == [
                "continue-key-0",
                "continue-key-1",
            ]


async def test_intervention_timeout_is_durable_and_typed() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-timeout-test",
            workflows=[HumanInterventionWorkflow],
        ):
            with pytest.raises(WorkflowFailureError) as failure:
                await environment.client.execute_workflow(
                    HumanInterventionWorkflow.run,
                    "int_timeout",
                    id="human-intervention/timeout-test",
                    task_queue="s01-timeout-test",
                )
            cause = failure.value.__cause__
            assert isinstance(cause, ApplicationError)
            assert cause.type == "TimeoutError"


async def test_platform_session_duplicate_verification_and_revocation_signals() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-session-test",
            workflows=[PlatformSessionLifecycleWorkflow],
            activities=[prepare_collection_session, release_collection_session],
        ):
            lease_preparations.clear()
            lease_releases.clear()
            verified = await environment.client.start_workflow(
                PlatformSessionLifecycleWorkflow.run,
                SessionLifecycleInput(
                    tenant_pub_id="ten_verified",
                    account_pub_id="pac_verified",
                    scope="query",
                    holder="verified-test",
                    challenge_required=True,
                ),
                id="platform-session/verified-test",
                task_queue="s01-session-test",
            )
            await verified.signal(
                PlatformSessionLifecycleWorkflow.intervention_completed, "nonce-1"
            )
            await verified.signal(
                PlatformSessionLifecycleWorkflow.intervention_completed, "nonce-1"
            )
            verified_result = await verified.result()
            assert verified_result.state == "completed"
            assert verified_result.intervention_verified is True
            assert verified_result.lease_released is True

            revoked = await environment.client.start_workflow(
                PlatformSessionLifecycleWorkflow.run,
                SessionLifecycleInput(
                    tenant_pub_id="ten_revoked",
                    account_pub_id="pac_revoked",
                    scope="publish",
                    holder="revoked-test",
                    challenge_required=True,
                ),
                id="platform-session/revoked-test",
                task_queue="s01-session-test",
            )
            await revoked.signal(PlatformSessionLifecycleWorkflow.revoke)
            revoked_result = await revoked.result()
            assert revoked_result.state == "revoked"
            assert revoked_result.intervention_verified is False
            assert revoked_result.lease_released is True
            assert lease_preparations == ["ten_verified", "ten_revoked"]
            assert lease_releases == [
                ("ten_verified", "sle_cleanup_fixture", 41),
                ("ten_revoked", "sle_cleanup_fixture", 41),
            ]


async def test_platform_session_external_cancellation_releases_fenced_lease() -> None:
    lease_preparations.clear()
    lease_releases.clear()
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-session-cancel-test",
            workflows=[PlatformSessionLifecycleWorkflow],
            activities=[prepare_collection_session, release_collection_session],
        ):
            handle = await environment.client.start_workflow(
                PlatformSessionLifecycleWorkflow.run,
                SessionLifecycleInput(
                    tenant_pub_id="ten_session_cancel",
                    account_pub_id="pac_session_cancel",
                    scope="query",
                    holder="session-cancel-test",
                    challenge_required=True,
                ),
                id="platform-session/external-cancel-test",
                task_queue="s01-session-cancel-test",
            )
            for _ in range(100):
                if lease_preparations:
                    break
                await asyncio.sleep(0.01)
            assert lease_preparations == ["ten_session_cancel"]
            await handle.cancel()
            with pytest.raises(WorkflowFailureError):
                await handle.result()
    assert lease_releases == [("ten_session_cancel", "sle_cleanup_fixture", 41)]



# ---------------------------------------------------------------------------
# doubao-batch-collect-v1：batch 路由（doubao 连续段 → 一个 batch activity）
# ---------------------------------------------------------------------------

batch_calls: list[list[str]] = []
persisted_items: list[tuple[str, str]] = []


def _batch_task(key: str, adapter: str) -> CollectionTaskInput:
    return CollectionTaskInput(
        business_key=key,
        query=f"query-{key}",
        model="doubao" if adapter == "doubao" else "fixed",
        region="CN-BJ",
        mode="fast",
        adapter=adapter,
    )


@activity.defn(name="collect_doubao_batch")
async def collect_doubao_batch_ok(batch: CollectionBatchInput) -> CollectionBatchResult:
    """Deterministic batch fixture：记录整批输入，全题 ok 返回。"""
    activity.heartbeat({"run_pub_id": batch.run_pub_id, "stage": "fixture_started"})
    batch_calls.append([item.business_key for item in batch.items])
    return CollectionBatchResult(
        results=[
            CollectionBatchItemResult(
                business_key=item.business_key,
                status="ok",
                answer_text=f"[batch-fixture] {item.query}",
                screenshot_ref="fixture://screenshots/batch.png",
                quality_state="fixture_valid",
            )
            for item in batch.items
        ]
    )


@activity.defn(name="collect_doubao_batch")
async def collect_doubao_batch_with_wall(batch: CollectionBatchInput) -> CollectionBatchResult:
    """第 2 题撞墙、第 3 题未执行（模拟真实 session 的失败语义），不 raise。"""
    batch_calls.append([item.business_key for item in batch.items])
    results: list[CollectionBatchItemResult] = []
    for index, item in enumerate(batch.items):
        if index == 0:
            results.append(
                CollectionBatchItemResult(
                    business_key=item.business_key,
                    status="ok",
                    answer_text=f"[batch-fixture] {item.query}",
                    screenshot_ref="fixture://screenshots/batch.png",
                    quality_state="fixture_valid",
                )
            )
        elif index == 1:
            results.append(
                CollectionBatchItemResult(
                    business_key=item.business_key,
                    status="wall",
                    error_type="wall_captcha",
                    error_message="captcha challenge appeared post-send (fixture)",
                    screenshot_ref="fixture://screenshots/wall.png",
                )
            )
        else:
            results.append(
                CollectionBatchItemResult(
                    business_key=item.business_key,
                    status="aborted",
                    error_type="aborted_after_failure",
                    error_message="not executed: batch stopped after failure (fixture)",
                )
            )
    return CollectionBatchResult(results=results)


@activity.defn(name="persist_collection_result")
async def persist_collection_result_fixture(
    tenant_pub_id: str,
    run_pub_id: str,
    result: CollectionBatchItemResult,
    task_input: CollectionTaskInput | None = None,
) -> None:
    del tenant_pub_id, run_pub_id, task_input
    persisted_items.append((result.business_key, result.status))


async def test_doubao_segments_routed_to_batch_and_per_task() -> None:
    """混合任务 [d,d,fixed,d]：doubao 连续段各合成一次 batch，fixed 保持 per-task；
    完成顺序与原任务顺序一致；逐题 persist（含 batch 题）。"""
    batch_calls.clear()
    persisted_items.clear()
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-batch-route-test",
            workflows=[GeoCollectionWorkflow],
            activities=[
                collect_with_adapter,
                collect_doubao_batch_ok,
                persist_collection_result_fixture,
                publish_downstream_event,
                mark_collection_run_terminal,
            ],
        ):
            result = await environment.client.execute_workflow(
                GeoCollectionWorkflow.run,
                GeoCollectionInput(
                    tenant_pub_id="tnt_batch",
                    project_pub_id="prj_batch",
                    run_pub_id="run_batch",
                    config_version_pub_id="cfv_batch",
                    tasks=[
                        _batch_task("d-1", "doubao"),
                        _batch_task("d-2", "doubao"),
                        _batch_task("f-1", "fixed"),
                        _batch_task("d-3", "doubao"),
                    ],
                    persist_results=True,
                    inter_task_delay_max_s=0.0,  # 关掉节奏睡眠（路由测试不验证节奏）
                ),
                id="geo-collection/batch-route-test",
                task_queue="s01-batch-route-test",
            )
    assert result.state == "completed"
    # doubao 连续段 [d-1,d-2] 与 [d-3] 各一次 batch 调用（原相对顺序）
    assert batch_calls == [["d-1", "d-2"], ["d-3"]]
    # 完成顺序保持原任务顺序（batch 内 d-1→d-2，per-task f-1 居中）
    assert [item.business_key for item in result.completed] == ["d-1", "d-2", "f-1", "d-3"]
    assert result.completed[0].answer_text == "[batch-fixture] query-d-1"
    assert result.completed[2].quality_state == "fixture_valid"
    # 逐题 persist：batch 题（新形状）与 per-task 题（旧形状 → 默认 status=ok）
    assert persisted_items == [("d-1", "ok"), ("d-2", "ok"), ("f-1", "ok"), ("d-3", "ok")]


async def test_doubao_batch_wall_persists_failures_and_run_continues() -> None:
    """batch 内第 2 题 wall、第 3 题 aborted：题级失败是数据不是工作流故障——
    逐题诚实落库（含失败/未执行题）后 run 继续走完后续任务与分析扇出，
    终态由 persist 推导为 completed_with_failures（s04_0019 词表），不硬闯。"""
    batch_calls.clear()
    persisted_items.clear()
    terminal_run_states.clear()
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-batch-wall-test",
            workflows=[GeoCollectionWorkflow],
            activities=[
                collect_with_adapter,
                collect_doubao_batch_with_wall,
                persist_collection_result_fixture,
                publish_downstream_event,
                mark_collection_run_terminal,
            ],
        ):
            result = await environment.client.execute_workflow(
                GeoCollectionWorkflow.run,
                GeoCollectionInput(
                    tenant_pub_id="tnt_batch_wall",
                    project_pub_id="prj_batch_wall",
                    run_pub_id="run_batch_wall",
                    config_version_pub_id="cfv_batch_wall",
                    tasks=[
                        _batch_task("w-1", "doubao"),
                        _batch_task("w-2", "doubao"),
                        _batch_task("w-3", "doubao"),
                        _batch_task("w-4", "fixed"),
                    ],
                    persist_results=True,
                    inter_task_delay_max_s=0.0,
                ),
                id="geo-collection/batch-wall-test",
                task_queue="s01-batch-wall-test",
            )
    assert result.state == "completed"
    # batch 只调用一次（3 题一段）；墙不阻断后续 fixed 题
    assert batch_calls == [["w-1", "w-2", "w-3"]]
    # 逐题 persist：ok + wall + aborted + 后续 fixed ok 全部落库
    assert persisted_items == [
        ("w-1", "ok"), ("w-2", "wall"), ("w-3", "aborted"), ("w-4", "ok"),
    ]
    # 终态按 completed 标记（persist 侧 derive 落 completed_with_failures，mark 不降级）
    assert terminal_run_states == [
        ("tnt_batch_wall", "run_batch_wall", "completed", None)
    ]


@activity.defn(name="collect_deepseek_batch")
async def collect_deepseek_batch_ok(batch: CollectionBatchInput) -> CollectionBatchResult:
    batch_calls.append(["deepseek:" + item.business_key for item in batch.items])
    return CollectionBatchResult(
        results=[
            CollectionBatchItemResult(
                business_key=item.business_key,
                status="ok",
                answer_text=f"[ds-fixture] {item.query}",
                screenshot_ref="fixture://screenshots/ds.png",
                quality_state="fixture_valid",
            )
            for item in batch.items
        ]
    )


@activity.defn(name="collect_yuanbao_batch")
async def collect_yuanbao_batch_ok(batch: CollectionBatchInput) -> CollectionBatchResult:
    batch_calls.append(["yuanbao:" + item.business_key for item in batch.items])
    return CollectionBatchResult(
        results=[
            CollectionBatchItemResult(
                business_key=item.business_key,
                status="ok",
                answer_text=f"[yb-fixture] {item.query}",
                screenshot_ref="fixture://screenshots/yb.png",
                quality_state="fixture_valid",
            )
            for item in batch.items
        ]
    )


async def test_all_adapters_routed_to_named_batch_activities() -> None:
    """adapter-batch-collect-v1 泛化路由（W8）：[doubao, deepseek, fixed, yuanbao]
    → 三个 batch-capable slug 各按动态名 collect_<slug>_batch 调用一次，fixed
    保持 per-task；完成顺序与原任务顺序一致，逐题 persist。"""
    batch_calls.clear()
    persisted_items.clear()
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-batch-all-route-test",
            workflows=[GeoCollectionWorkflow],
            activities=[
                collect_with_adapter,
                collect_doubao_batch_ok,
                collect_deepseek_batch_ok,
                collect_yuanbao_batch_ok,
                persist_collection_result_fixture,
                publish_downstream_event,
                mark_collection_run_terminal,
            ],
        ):
            result = await environment.client.execute_workflow(
                GeoCollectionWorkflow.run,
                GeoCollectionInput(
                    tenant_pub_id="tnt_batch_all",
                    project_pub_id="prj_batch_all",
                    run_pub_id="run_batch_all",
                    config_version_pub_id="cfv_batch_all",
                    tasks=[
                        _batch_task("d-1", "doubao"),
                        _batch_task("s-1", "deepseek"),
                        _batch_task("s-2", "deepseek"),
                        _batch_task("f-1", "fixed"),
                        _batch_task("y-1", "yuanbao"),
                    ],
                    persist_results=True,
                    inter_task_delay_max_s=0.0,
                ),
                id="geo-collection/batch-all-route-test",
                task_queue="s01-batch-all-route-test",
            )
    assert result.state == "completed"
    # 三段 batch 调用：doubao×1、deepseek×1（两题）、yuanbao×1
    assert batch_calls == [["d-1"], ["deepseek:s-1", "deepseek:s-2"], ["yuanbao:y-1"]]
    assert [item.business_key for item in result.completed] == [
        "d-1", "s-1", "s-2", "f-1", "y-1",
    ]
    assert result.completed[0].answer_text == "[batch-fixture] query-d-1"
    assert result.completed[1].answer_text == "[ds-fixture] query-s-1"
    assert result.completed[4].answer_text == "[yb-fixture] query-y-1"
    assert persisted_items == [
        ("d-1", "ok"), ("s-1", "ok"), ("s-2", "ok"), ("f-1", "ok"), ("y-1", "ok"),
    ]


# ---------------------------------------------------------------------------
# captcha-assist-v1：撞码挂起 → 手机人工接管 → 断点续跑
# ---------------------------------------------------------------------------

assist_events: list[tuple[str, str, str]] = []


@activity.defn(name="captcha_assist_start")
async def captcha_assist_start_fixture(input: CaptchaAssistInput) -> CaptchaAssistStarted:
    """每次接管铸造递增 session_id（连撞时各次挂起互不串扰）。

    序号从 assist_events 里已记录的 start 数推导——测试间清空 assist_events
    即自然复位，不留跨测试的全局序号泄漏。"""
    session_id = f"sess-{sum(1 for e in assist_events if e[0] == 'start') + 1}"
    assist_events.append(("start", input.run_pub_id, f"{input.business_key}|{session_id}"))
    return CaptchaAssistStarted(
        session_id=session_id,
        assist_url="https://fixture.local/api/v2/assist/ticket",
        pushed=True,
    )


@activity.defn(name="captcha_assist_stop")
async def captcha_assist_stop_fixture(input: CaptchaAssistStopInput) -> None:
    assist_events.append(("stop", input.run_pub_id, input.session_id))


@activity.defn(name="captcha_assist_start")
async def captcha_assist_start_unavailable(input: CaptchaAssistInput) -> CaptchaAssistStarted:
    del input
    raise ApplicationError(
        "no resident browser (fixture)",
        type="assist_no_resident_browser",
        non_retryable=True,
    )


def _ok_batch_result(items: list[CollectionTaskInput]) -> CollectionBatchResult:
    return CollectionBatchResult(
        results=[
            CollectionBatchItemResult(
                business_key=item.business_key,
                status="ok",
                answer_text=f"[batch-fixture] {item.query}",
                screenshot_ref="fixture://screenshots/batch.png",
                quality_state="fixture_valid",
            )
            for item in items
        ]
    )


def _captcha_pause_batch_result(
    items: list[CollectionTaskInput], pause_index: int
) -> CollectionBatchResult:
    """等长全占位结果 + captcha_pause 标注（live 适配器的生产形状）。"""
    results: list[CollectionBatchItemResult] = []
    for index, item in enumerate(items):
        if index < pause_index:
            results.append(
                CollectionBatchItemResult(
                    business_key=item.business_key,
                    status="ok",
                    answer_text=f"[batch-fixture] {item.query}",
                    screenshot_ref="fixture://screenshots/batch.png",
                    quality_state="fixture_valid",
                )
            )
        elif index == pause_index:
            results.append(
                CollectionBatchItemResult(
                    business_key=item.business_key,
                    status="wall",
                    error_type="wall_captcha",
                    error_message="captcha challenge appeared post-send (fixture)",
                    screenshot_ref="fixture://screenshots/wall.png",
                )
            )
        else:
            results.append(
                CollectionBatchItemResult(
                    business_key=item.business_key,
                    status="aborted",
                    error_type="aborted_after_failure",
                    error_message="not executed: batch stopped after failure (fixture)",
                )
            )
    return CollectionBatchResult(
        results=results,
        captcha_pause=CaptchaPause(
            resume_index=pause_index,
            business_key=items[pause_index].business_key,
            evidence_ref="fixture://screenshots/wall.png",
        ),
    )


@activity.defn(name="collect_doubao_batch")
async def collect_doubao_batch_captcha_once(batch: CollectionBatchInput) -> CollectionBatchResult:
    """首段（c-1 开头）第 2 题撞码并标注 pause；续跑段全题 ok。"""
    batch_calls.append([item.business_key for item in batch.items])
    if batch.items[0].business_key == "c-1":
        return _captcha_pause_batch_result(batch.items, 1)
    return _ok_batch_result(batch.items)


@activity.defn(name="collect_doubao_batch")
async def collect_doubao_batch_captcha_always(batch: CollectionBatchInput) -> CollectionBatchResult:
    """每次都首题撞码（连撞护栏测试用）。"""
    batch_calls.append([item.business_key for item in batch.items])
    return _captcha_pause_batch_result(batch.items, 0)


async def _await_assist_starts(count: int) -> None:
    for _ in range(500):
        if sum(1 for e in assist_events if e[0] == "start") >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"assist start events did not reach {count}: {assist_events}")


async def test_captcha_pause_solved_resumes_from_breakpoint() -> None:
    """第 2 题撞码 → 挂起等人工 → 手机确认解决（signal）→ 从断点起重采：
    撞码题的 wall 结果绝不落库，续跑段的 ok 覆盖之；run 终态 completed。"""
    batch_calls.clear()
    persisted_items.clear()
    assist_events.clear()
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-captcha-resume-test",
            workflows=[GeoCollectionWorkflow],
            activities=[
                collect_doubao_batch_captcha_once,
                captcha_assist_start_fixture,
                captcha_assist_stop_fixture,
                persist_collection_result_fixture,
                publish_downstream_event,
                mark_collection_run_terminal,
            ],
        ):
            handle = await environment.client.start_workflow(
                GeoCollectionWorkflow.run,
                GeoCollectionInput(
                    tenant_pub_id="tnt_captcha",
                    project_pub_id="prj_captcha",
                    run_pub_id="run_captcha",
                    config_version_pub_id="cfv_captcha",
                    tasks=[
                        _batch_task("c-1", "doubao"),
                        _batch_task("c-2", "doubao"),
                        _batch_task("c-3", "doubao"),
                    ],
                    persist_results=True,
                    inter_task_delay_max_s=0.0,
                ),
                id="geo-collection/captcha-resume-test",
                task_queue="s01-captcha-resume-test",
            )
            await _await_assist_starts(1)
            await handle.signal(GeoCollectionWorkflow.captcha_solved, "sess-1")
            result = await handle.result()
    assert result.state == "completed"
    # 首段全量一次 + 断点续跑段（撞码题 c-2 重发）
    assert batch_calls == [["c-1", "c-2", "c-3"], ["c-2", "c-3"]]
    # 撞码题的 wall 不落库：落库序列 = 前缀 ok + 续跑 ok
    assert persisted_items == [("c-1", "ok"), ("c-2", "ok"), ("c-3", "ok")]
    assert ("start", "run_captcha", "c-2|sess-1") in assist_events
    assert ("stop", "run_captcha", "sess-1") in assist_events


async def test_captcha_pause_timeout_falls_back_to_wall_abort() -> None:
    """无人接管（时间跳跃自动越过 60min 等待）→ 回退现行语义：撞码题 wall +
    余题 aborted 全量落库，不续跑，run 照常走完（终态 persist 侧推导）。"""
    batch_calls.clear()
    persisted_items.clear()
    assist_events.clear()
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-captcha-timeout-test",
            workflows=[GeoCollectionWorkflow],
            activities=[
                collect_doubao_batch_captcha_once,
                captcha_assist_start_fixture,
                captcha_assist_stop_fixture,
                persist_collection_result_fixture,
                publish_downstream_event,
                mark_collection_run_terminal,
            ],
        ):
            result = await environment.client.execute_workflow(
                GeoCollectionWorkflow.run,
                GeoCollectionInput(
                    tenant_pub_id="tnt_captcha_to",
                    project_pub_id="prj_captcha_to",
                    run_pub_id="run_captcha_to",
                    config_version_pub_id="cfv_captcha_to",
                    tasks=[
                        _batch_task("c-1", "doubao"),
                        _batch_task("c-2", "doubao"),
                        _batch_task("c-3", "doubao"),
                    ],
                    persist_results=True,
                    inter_task_delay_max_s=0.0,
                ),
                id="geo-collection/captcha-timeout-test",
                task_queue="s01-captcha-timeout-test",
            )
    assert result.state == "completed"
    assert batch_calls == [["c-1", "c-2", "c-3"]]
    assert persisted_items == [("c-1", "ok"), ("c-2", "wall"), ("c-3", "aborted")]
    assert ("stop", "run_captcha_to", "sess-1") in assist_events


async def test_captcha_pause_assist_unavailable_falls_back() -> None:
    """assist 基建不可用（无常驻浏览器）→ 立即回退，绝不阻断/空等。"""
    batch_calls.clear()
    persisted_items.clear()
    assist_events.clear()
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-captcha-noassist-test",
            workflows=[GeoCollectionWorkflow],
            activities=[
                collect_doubao_batch_captcha_once,
                captcha_assist_start_unavailable,
                captcha_assist_stop_fixture,
                persist_collection_result_fixture,
                publish_downstream_event,
                mark_collection_run_terminal,
            ],
        ):
            result = await environment.client.execute_workflow(
                GeoCollectionWorkflow.run,
                GeoCollectionInput(
                    tenant_pub_id="tnt_captcha_na",
                    project_pub_id="prj_captcha_na",
                    run_pub_id="run_captcha_na",
                    config_version_pub_id="cfv_captcha_na",
                    tasks=[
                        _batch_task("c-1", "doubao"),
                        _batch_task("c-2", "doubao"),
                    ],
                    persist_results=True,
                    inter_task_delay_max_s=0.0,
                ),
                id="geo-collection/captcha-noassist-test",
                task_queue="s01-captcha-noassist-test",
            )
    assert result.state == "completed"
    assert batch_calls == [["c-1", "c-2"]]
    assert persisted_items == [("c-1", "ok"), ("c-2", "wall")]
    # assist_stop 无从谈起（start 都没成），但绝不影响落库
    assert [e for e in assist_events if e[0] == "stop"] == []


async def test_captcha_pause_limit_falls_back_after_three_interventions() -> None:
    """连撞护栏：单 run 挂起上限 3 次——前三次人工解围续跑，第 4 次撞码直接
    回退 wall+abort（不再推送打扰人工，账号大概率已敏感化）。"""
    batch_calls.clear()
    persisted_items.clear()
    assist_events.clear()
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-captcha-limit-test",
            workflows=[GeoCollectionWorkflow],
            activities=[
                collect_doubao_batch_captcha_always,
                captcha_assist_start_fixture,
                captcha_assist_stop_fixture,
                persist_collection_result_fixture,
                publish_downstream_event,
                mark_collection_run_terminal,
            ],
        ):
            handle = await environment.client.start_workflow(
                GeoCollectionWorkflow.run,
                GeoCollectionInput(
                    tenant_pub_id="tnt_captcha_lim",
                    project_pub_id="prj_captcha_lim",
                    run_pub_id="run_captcha_lim",
                    config_version_pub_id="cfv_captcha_lim",
                    tasks=[_batch_task("c-1", "doubao"), _batch_task("c-2", "doubao")],
                    persist_results=True,
                    inter_task_delay_max_s=0.0,
                ),
                id="geo-collection/captcha-limit-test",
                task_queue="s01-captcha-limit-test",
            )
            for seq in (1, 2, 3):
                await _await_assist_starts(seq)
                await handle.signal(GeoCollectionWorkflow.captcha_solved, f"sess-{seq}")
            result = await handle.result()
    assert result.state == "completed"
    # 3 次接管 + 第 4 次撞码直接回退：batch 共 4 调，assist_start 恰 3 次
    assert len(batch_calls) == 4
    assert sum(1 for e in assist_events if e[0] == "start") == 3
    assert persisted_items == [("c-1", "wall"), ("c-2", "aborted")]


# ---------------------------------------------------------------------------
# captcha-assist-v1 门放开（2026-08-07）：非豆包平台 pause 同样挂起接管；
# 畸形 pause 不当挂起处理，按旧语义全量落库
# ---------------------------------------------------------------------------


@activity.defn(name="captcha_assist_start")
async def captcha_assist_start_platform_fixture(
    input: CaptchaAssistInput,
) -> CaptchaAssistStarted:
    """记录 platform 的 start fixture（非豆包 slug 门放开断言用）。"""
    session_id = f"sess-{sum(1 for e in assist_events if e[0] == 'start') + 1}"
    assist_events.append(
        ("start", input.run_pub_id, f"{input.platform}|{input.business_key}|{session_id}")
    )
    return CaptchaAssistStarted(
        session_id=session_id,
        assist_url="https://fixture.local/api/v2/assist/ticket",
        pushed=True,
    )


@activity.defn(name="collect_tongyi_batch")
async def collect_tongyi_batch_captcha_once(
    batch: CollectionBatchInput,
) -> CollectionBatchResult:
    """tongyi 首段（t-1 开头）第 2 题撞码并标注 pause；续跑段全题 ok。"""
    batch_calls.append([item.business_key for item in batch.items])
    if batch.items[0].business_key == "t-1":
        return _captcha_pause_batch_result(batch.items, 1)
    return _ok_batch_result(batch.items)


async def test_captcha_pause_non_doubao_slug_suspends_and_resumes() -> None:
    """门放开（不再限 doubao）：tongyi batch 撞码 → 挂起等人工 → signal 解围
    → 断点起重采；assist_start 收到的 platform 原样是 tongyi。"""
    batch_calls.clear()
    persisted_items.clear()
    assist_events.clear()
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-captcha-tongyi-test",
            workflows=[GeoCollectionWorkflow],
            activities=[
                collect_tongyi_batch_captcha_once,
                captcha_assist_start_platform_fixture,
                captcha_assist_stop_fixture,
                persist_collection_result_fixture,
                publish_downstream_event,
                mark_collection_run_terminal,
            ],
        ):
            handle = await environment.client.start_workflow(
                GeoCollectionWorkflow.run,
                GeoCollectionInput(
                    tenant_pub_id="tnt_captcha_ty",
                    project_pub_id="prj_captcha_ty",
                    run_pub_id="run_captcha_ty",
                    config_version_pub_id="cfv_captcha_ty",
                    tasks=[
                        _batch_task("t-1", "tongyi"),
                        _batch_task("t-2", "tongyi"),
                        _batch_task("t-3", "tongyi"),
                    ],
                    persist_results=True,
                    inter_task_delay_max_s=0.0,
                ),
                id="geo-collection/captcha-tongyi-test",
                task_queue="s01-captcha-tongyi-test",
            )
            await _await_assist_starts(1)
            await handle.signal(GeoCollectionWorkflow.captcha_solved, "sess-1")
            result = await handle.result()
    assert result.state == "completed"
    # 首段全量一次 + 断点续跑段（撞码题 t-2 重发）
    assert batch_calls == [["t-1", "t-2", "t-3"], ["t-2", "t-3"]]
    assert persisted_items == [("t-1", "ok"), ("t-2", "ok"), ("t-3", "ok")]
    assert ("start", "run_captcha_ty", "tongyi|t-2|sess-1") in assist_events
    assert ("stop", "run_captcha_ty", "sess-1") in assist_events


@activity.defn(name="collect_doubao_batch")
async def collect_doubao_batch_malformed_pause(
    batch: CollectionBatchInput,
) -> CollectionBatchResult:
    """adapter 契约违背 fixture：等长结果照旧，但 pause.resume_index 越界。"""
    batch_calls.append([item.business_key for item in batch.items])
    result = _captcha_pause_batch_result(batch.items, 1)
    result.captcha_pause = CaptchaPause(
        resume_index=len(batch.items) + 1,
        business_key=batch.items[1].business_key,
        evidence_ref="fixture://screenshots/wall.png",
    )
    return result


async def test_malformed_captcha_pause_falls_back_to_full_persist() -> None:
    """畸形 pause（resume_index 越界）不当挂起处理：不起 assist 会话、不续跑，
    等长结果按旧语义全量落库（撞码题 wall 原样持久化）。"""
    batch_calls.clear()
    persisted_items.clear()
    assist_events.clear()
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-captcha-malformed-test",
            workflows=[GeoCollectionWorkflow],
            activities=[
                collect_doubao_batch_malformed_pause,
                captcha_assist_start_fixture,
                captcha_assist_stop_fixture,
                persist_collection_result_fixture,
                publish_downstream_event,
                mark_collection_run_terminal,
            ],
        ):
            result = await environment.client.execute_workflow(
                GeoCollectionWorkflow.run,
                GeoCollectionInput(
                    tenant_pub_id="tnt_captcha_bad",
                    project_pub_id="prj_captcha_bad",
                    run_pub_id="run_captcha_bad",
                    config_version_pub_id="cfv_captcha_bad",
                    tasks=[_batch_task("c-1", "doubao"), _batch_task("c-2", "doubao")],
                    persist_results=True,
                    inter_task_delay_max_s=0.0,
                ),
                id="geo-collection/captcha-malformed-test",
                task_queue="s01-captcha-malformed-test",
            )
    assert result.state == "completed"
    assert batch_calls == [["c-1", "c-2"]]
    assert persisted_items == [("c-1", "ok"), ("c-2", "wall")]
    assert assist_events == []                     # 畸形 pause 绝不起接管会话
