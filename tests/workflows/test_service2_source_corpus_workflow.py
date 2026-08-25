from __future__ import annotations

import uuid

from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from workflows.activities.service2_source_corpus import (
    Service2BatchInput,
    Service2BatchPreparation,
    Service2CorpusPageInput,
    Service2CorpusPageResult,
    Service2SourceFetchShard,
)
from workflows.activities.source_fetch import SourceFetchInput, SourceFetchResult
from workflows.definitions.service2_source_corpus import (
    Service2SourceCorpusWorkflow,
    Service2SourceCorpusWorkflowInput,
)

TOTAL_OCCURRENCES = 509
fetch_calls: list[SourceFetchInput] = []
page_starts: list[int] = []
refresh_calls = 0
finish_calls = 0
failure_calls = 0


@activity.defn(name="prepare_service2_corpus_batch")
async def prepare_fixture(_data: Service2BatchInput) -> Service2BatchPreparation:
    return Service2BatchPreparation(
        run_pub_ids=["run_a", "run_b"],
        cancelled=False,
        fetch_shards=[
            Service2SourceFetchShard("run_a", ["url_shared", "url_a"]),
            Service2SourceFetchShard("run_b", ["url_b"]),
        ],
    )


@activity.defn(name="fetch_run_sources")
async def fetch_fixture(data: SourceFetchInput) -> SourceFetchResult:
    fetch_calls.append(data)
    return SourceFetchResult()


@activity.defn(name="refresh_service2_corpus_bindings")
async def refresh_fixture(_data: Service2BatchInput) -> int:
    global refresh_calls
    refresh_calls += 1
    return TOTAL_OCCURRENCES


@activity.defn(name="process_service2_corpus_page")
async def page_fixture(data: Service2CorpusPageInput) -> Service2CorpusPageResult:
    start = int(data.cursor or "0")
    page_starts.append(start)
    processed = min(data.page_size, TOTAL_OCCURRENCES - start)
    end = start + processed
    return Service2CorpusPageResult(
        processed=processed,
        next_cursor=str(end),
        has_more=end < TOTAL_OCCURRENCES,
        states={"processed": processed},
    )


@activity.defn(name="finish_service2_corpus_batch")
async def finish_fixture(_data: Service2BatchInput) -> str:
    global finish_calls
    finish_calls += 1
    return "review"


@activity.defn(name="fail_service2_corpus_batch")
async def fail_fixture(_data: Service2BatchInput) -> str:
    global failure_calls
    failure_calls += 1
    return "failed"


async def test_more_than_500_occurrences_continue_as_new_without_refetch_or_truncation() -> None:
    global refresh_calls, finish_calls, failure_calls
    fetch_calls.clear()
    page_starts.clear()
    refresh_calls = 0
    finish_calls = 0
    failure_calls = 0
    analysis_queue = f"service2-analysis-{uuid.uuid4().hex}"
    source_queue = f"service2-source-{uuid.uuid4().hex}"
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with (
            Worker(
                environment.client,
                task_queue=source_queue,
                activities=[fetch_fixture],
            ),
            Worker(
                environment.client,
                task_queue=analysis_queue,
                workflows=[Service2SourceCorpusWorkflow],
                activities=[
                    prepare_fixture,
                    refresh_fixture,
                    page_fixture,
                    finish_fixture,
                    fail_fixture,
                ],
            ),
        ):
            result = await environment.client.execute_workflow(
                Service2SourceCorpusWorkflow.run,
                Service2SourceCorpusWorkflowInput(
                    schema_version="service2-source-corpus-workflow-v1",
                    tenant_pub_id="tnt_service2",
                    project_pub_id="prj_service2",
                    batch_pub_id="s2b_service2",
                    source_task_queue=source_queue,
                ),
                id=f"service2-source-corpus/test/{uuid.uuid4().hex}",
                task_queue=analysis_queue,
            )

    assert result == {
        "state": "review",
        "processed_count": TOTAL_OCCURRENCES,
        "coverage_cursor": str(TOTAL_OCCURRENCES),
    }
    assert page_starts == [0, 100, 200, 300, 400, 500]
    assert refresh_calls == 1
    assert finish_calls == 1
    assert failure_calls == 0
    assert [(call.run_pub_id, call.source_url_pub_ids) for call in fetch_calls] == [
        ("run_a", ("url_shared", "url_a")),
        ("run_b", ("url_b",)),
    ]
    fetched_url_ids = [
        source_url for call in fetch_calls for source_url in (call.source_url_pub_ids or ())
    ]
    assert len(fetched_url_ids) == len(set(fetched_url_ids)) == 3
