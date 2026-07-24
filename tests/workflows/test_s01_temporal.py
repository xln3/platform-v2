import hashlib

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from workflows.activities.collection import (
    CollectionTaskInput,
    CollectionTaskResult,
    publish_downstream_event,
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


async def test_collection_fixed_adapter_and_duplicate_intervention_signal() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-test",
            workflows=[GeoCollectionWorkflow, HumanInterventionWorkflow],
            activities=[collect_with_adapter, publish_downstream_event],
        ):
            handle = await environment.client.start_workflow(
                HumanInterventionWorkflow.run,
                "int_test",
                id="human-intervention/test",
                task_queue="s01-test",
            )
            await handle.signal(HumanInterventionWorkflow.complete, args=["nonce-1", "verified"])
            await handle.signal(HumanInterventionWorkflow.complete, args=["nonce-1", "verified"])
            assert await handle.result() == "verified"


async def test_activity_failure_is_retried_and_workflow_recovers() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-retry-test",
            workflows=[GeoCollectionWorkflow],
            activities=[collect_with_adapter, publish_downstream_event],
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


async def test_pause_resume_cancel_signal_is_durable_and_idempotent() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue="s01-cancel-test",
            workflows=[GeoCollectionWorkflow],
            activities=[collect_with_adapter, publish_downstream_event],
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
            activities=[collect_with_adapter, publish_downstream_event],
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
        ):
            verified = await environment.client.start_workflow(
                PlatformSessionLifecycleWorkflow.run,
                SessionLifecycleInput(
                    account_pub_id="pac_verified",
                    scope="query",
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
            assert await verified.result() == "active:pac_verified:query"

            revoked = await environment.client.start_workflow(
                PlatformSessionLifecycleWorkflow.run,
                SessionLifecycleInput(
                    account_pub_id="pac_revoked",
                    scope="publish",
                    challenge_required=True,
                ),
                id="platform-session/revoked-test",
                task_queue="s01-session-test",
            )
            await revoked.signal(PlatformSessionLifecycleWorkflow.revoke)
            assert await revoked.result() == "revoked"
