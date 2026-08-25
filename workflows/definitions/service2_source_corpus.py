"""Deterministic all-U Service 2 orchestration with bounded history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from workflows.activities.service2_source_corpus import (
        Service2BatchInput,
        Service2CorpusPageInput,
        fail_service2_corpus_batch,
        finish_service2_corpus_batch,
        prepare_service2_corpus_batch,
        process_service2_corpus_page,
        refresh_service2_corpus_bindings,
    )
    from workflows.activities.source_fetch import SourceFetchInput, fetch_run_sources


@dataclass(frozen=True)
class Service2SourceCorpusWorkflowInput:
    schema_version: str
    tenant_pub_id: str
    project_pub_id: str
    batch_pub_id: str
    source_task_queue: str
    coverage_cursor: str | None = None
    processed_count: int = 0
    history_processed: int = 0
    fetch_completed: bool = False


@workflow.defn(name="Service2SourceCorpusWorkflow")
class Service2SourceCorpusWorkflow:
    def __init__(self) -> None:
        self._paused = False
        self._cancelled = False
        self._retry_requested = False

    @workflow.signal
    async def pause(self, _payload: dict[str, str] | None = None) -> None:
        self._paused = True

    @workflow.signal
    async def resume(self, _payload: dict[str, str] | None = None) -> None:
        self._paused = False

    @workflow.signal
    async def cancel(self, _payload: dict[str, str] | None = None) -> None:
        self._cancelled = True

    @workflow.signal
    async def retry(self, _payload: dict[str, str] | None = None) -> None:
        self._retry_requested = True

    @workflow.run
    async def run(self, data: Service2SourceCorpusWorkflowInput) -> dict[str, Any]:
        if data.schema_version != "service2-source-corpus-workflow-v1":
            raise ValueError("unsupported_service2_workflow_schema")
        batch_input = Service2BatchInput(
            tenant_pub_id=data.tenant_pub_id,
            project_pub_id=data.project_pub_id,
            batch_pub_id=data.batch_pub_id,
        )
        try:
            preparation = await workflow.execute_activity(
                prepare_service2_corpus_batch,
                batch_input,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=10),
            )
            if preparation.cancelled:
                state = await workflow.execute_activity(
                    finish_service2_corpus_batch,
                    batch_input,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=10),
                )
                return {"state": state, "processed_count": data.processed_count}
            if preparation.paused:
                # Continue-As-New starts a fresh workflow object, so restore the
                # durable API pause state before doing more network/page work.
                self._paused = True

            if not data.fetch_completed:
                for shard in preparation.fetch_shards:
                    await workflow.wait_condition(lambda: not self._paused or self._cancelled)
                    if self._cancelled:
                        state = await workflow.execute_activity(
                            finish_service2_corpus_batch,
                            batch_input,
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=RetryPolicy(maximum_attempts=10),
                        )
                        return {"state": state, "processed_count": data.processed_count}
                    await workflow.execute_activity(
                        fetch_run_sources,
                        SourceFetchInput(
                            tenant_pub_id=data.tenant_pub_id,
                            project_pub_id=data.project_pub_id,
                            run_pub_id=shard.run_pub_id,
                            source_url_pub_ids=tuple(shard.source_url_pub_ids),
                        ),
                        task_queue=data.source_task_queue,
                        start_to_close_timeout=timedelta(hours=6),
                        heartbeat_timeout=timedelta(seconds=60),
                        retry_policy=RetryPolicy(maximum_attempts=2),
                    )
                await workflow.execute_activity(
                    refresh_service2_corpus_bindings,
                    batch_input,
                    start_to_close_timeout=timedelta(minutes=10),
                    retry_policy=RetryPolicy(maximum_attempts=5),
                )

            cursor = data.coverage_cursor
            processed = data.processed_count
            history_processed = data.history_processed
            while True:
                await workflow.wait_condition(lambda: not self._paused or self._cancelled)
                if self._cancelled:
                    break
                page = await workflow.execute_activity(
                    process_service2_corpus_page,
                    Service2CorpusPageInput(
                        tenant_pub_id=data.tenant_pub_id,
                        project_pub_id=data.project_pub_id,
                        batch_pub_id=data.batch_pub_id,
                        cursor=cursor,
                        page_size=100,
                    ),
                    start_to_close_timeout=timedelta(minutes=30),
                    # A single model+web-search call may legitimately exceed one
                    # minute. The activity heartbeats between immutable items.
                    heartbeat_timeout=timedelta(minutes=6),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
                cursor = page.next_cursor
                processed += page.processed
                history_processed += page.processed
                if not page.has_more:
                    break
                if history_processed >= 500:
                    workflow.continue_as_new(
                        Service2SourceCorpusWorkflowInput(
                            schema_version=data.schema_version,
                            tenant_pub_id=data.tenant_pub_id,
                            project_pub_id=data.project_pub_id,
                            batch_pub_id=data.batch_pub_id,
                            source_task_queue=data.source_task_queue,
                            coverage_cursor=cursor,
                            processed_count=processed,
                            history_processed=0,
                            fetch_completed=True,
                        )
                    )
            state = await workflow.execute_activity(
                finish_service2_corpus_batch,
                batch_input,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=10),
            )
            return {
                "state": state,
                "processed_count": processed,
                "coverage_cursor": cursor,
            }
        except Exception:
            await workflow.execute_activity(
                fail_service2_corpus_batch,
                batch_input,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            raise


__all__ = ["Service2SourceCorpusWorkflow", "Service2SourceCorpusWorkflowInput"]
