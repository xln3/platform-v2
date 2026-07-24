from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from workflows.activities.collection import (
        CollectionTaskInput,
        CollectionTaskResult,
        collect_with_adapter,
        persist_collection_result,
        prepare_collection_session,
        publish_downstream_event,
        release_collection_session,
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


@dataclass
class GeoCollectionResult:
    state: str
    completed: list[CollectionTaskResult] = field(default_factory=list)
    downstream_event: str | None = None


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
    def status(self) -> dict[str, object]:
        return {
            "paused": self._paused,
            "cancelled": self._cancelled,
            "intervention_completed": self._intervention_completed,
            "completed": len(self._completed),
        }

    @workflow.run
    async def run(self, data: GeoCollectionInput) -> GeoCollectionResult:
        self._completed.extend(data.prior_completed)
        session_preparation = None
        if data.account_pub_id:
            session_preparation = await workflow.execute_activity(
                prepare_collection_session,
                args=[data.account_pub_id, data.run_pub_id, "query"],
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
            if session_preparation:
                await workflow.execute_activity(
                    release_collection_session,
                    args=[
                        session_preparation.lease_pub_id,
                        session_preparation.fencing_token,
                    ],
                    start_to_close_timeout=timedelta(seconds=30),
                )
            return GeoCollectionResult(state="cancelled", completed=self._completed)
        for index, item in enumerate(data.tasks):
            await workflow.wait_condition(lambda: not self._paused or self._cancelled)
            if self._cancelled:
                if session_preparation:
                    await workflow.execute_activity(
                        release_collection_session,
                        args=[
                            session_preparation.lease_pub_id,
                            session_preparation.fencing_token,
                        ],
                        start_to_close_timeout=timedelta(seconds=30),
                    )
                return GeoCollectionResult(state="cancelled", completed=self._completed)
            try:
                result = await workflow.execute_activity(
                    collect_with_adapter,
                    item,
                    start_to_close_timeout=timedelta(minutes=5),
                    heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=1),
                        maximum_interval=timedelta(seconds=30),
                        maximum_attempts=5,
                        non_retryable_error_types=["unsupported_adapter"],
                    ),
                )
            except ApplicationError:
                raise
            self._completed.append(result)
            if data.persist_results:
                await workflow.execute_activity(
                    persist_collection_result,
                    args=[data.run_pub_id, result],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=10),
                )
            if index + 1 >= data.history_batch_size and index + 1 < len(data.tasks):
                if session_preparation:
                    await workflow.execute_activity(
                        release_collection_session,
                        args=[
                            session_preparation.lease_pub_id,
                            session_preparation.fencing_token,
                        ],
                        start_to_close_timeout=timedelta(seconds=30),
                    )
                workflow.continue_as_new(
                    GeoCollectionInput(
                        tenant_pub_id=data.tenant_pub_id,
                        project_pub_id=data.project_pub_id,
                        run_pub_id=data.run_pub_id,
                        config_version_pub_id=data.config_version_pub_id,
                        tasks=data.tasks[index + 1 :],
                        requires_intervention=False,
                        persist_results=data.persist_results,
                        account_pub_id=data.account_pub_id,
                        prior_completed=self._completed,
                        generation=data.generation + 1,
                        history_batch_size=data.history_batch_size,
                    )
                )
        downstream = await workflow.execute_activity(
            publish_downstream_event,
            data.run_pub_id,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=5),
        )
        if session_preparation:
            await workflow.execute_activity(
                release_collection_session,
                args=[
                    session_preparation.lease_pub_id,
                    session_preparation.fencing_token,
                ],
                start_to_close_timeout=timedelta(seconds=30),
            )
        return GeoCollectionResult(
            state="completed", completed=self._completed, downstream_event=downstream
        )
