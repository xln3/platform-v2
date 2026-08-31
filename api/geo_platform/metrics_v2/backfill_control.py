"""Bounded operations control plane for Metrics V2 semantic backfills."""

from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from typing import Any

from temporalio.client import Client, WorkflowExecutionStatus, WorkflowFailureError
from temporalio.exceptions import WorkflowAlreadyStartedError

from domain.analysis.v2._canonical import canonical_hash
from geo_platform.config import Settings

from .repository import MetricsV2Repository
from .schemas import (
    SemanticBackfillOptionsView,
    SemanticBackfillPlanRequest,
    SemanticBackfillPlanView,
    SemanticBackfillStartView,
    SemanticBackfillStatusView,
)
from .semantic_models import (
    CATALOG_REVISION,
    estimated_cost_usd,
    model_catalog,
    model_option,
    resolve_model,
)

_MAX_UI_BATCH_SIZE = 100
_ESTIMATED_OUTPUT_TOKENS_PER_DECISION = 450
_HIGH_OUTPUT_TOKENS_PER_DECISION = 900


def _maximum_batch_size(settings: Settings) -> int:
    return max(
        1,
        min(
            _MAX_UI_BATCH_SIZE,
            int(settings.semantic_decision_backfill_batch_size or _MAX_UI_BATCH_SIZE),
        ),
    )


def _candidate_view(item: dict[str, Any]) -> dict[str, Any]:
    display = dict(item.get("display") or {})
    return {
        "answer_pub_id": str(item["answer_pub_id"]),
        "query_text": str(display.get("query_text") or "")[:500],
        "model": str(display.get("model") or "unknown"),
        "region": str(display.get("region") or "unknown"),
        "mode": str(display.get("mode") or "unknown"),
        "channel": str(display.get("channel") or "unknown"),
        "capture_time": item["capture_time"],
        "preparation_state": str(item.get("preparation_state") or "unknown"),
        "reason_codes": list(map(str, item.get("reason_codes") or ())),
    }


def backfill_options(
    repository: MetricsV2Repository,
    settings: Settings,
    *,
    tenant_pub_id: str,
    project_pub_id: str,
    cursor: str | None,
    limit: int,
    as_of: str | None,
) -> SemanticBackfillOptionsView:
    batch = repository.load_decision_backfill_batch(
        tenant_pub_id=tenant_pub_id,
        project_pub_id=project_pub_id,
        cursor=cursor,
        limit=min(max(1, limit), 200),
        as_of=as_of,
        dry_run=True,
    )
    models = model_catalog(settings)
    default_model = next(
        (str(model["model"]) for model in models if bool(model.get("recommended"))),
        resolve_model(settings, None),
    )
    return SemanticBackfillOptionsView.model_validate(
        {
            "project_pub_id": project_pub_id,
            "as_of": batch["as_of"],
            "candidate_count": int(batch.get("candidate_count") or 0),
            "candidates": [_candidate_view(item) for item in batch.get("items") or ()],
            "next_cursor": batch.get("next_cursor"),
            "max_batch_size": _maximum_batch_size(settings),
            "default_model": default_model,
            "models": models,
        }
    )


def _task_count(workflow_payload: dict[str, Any]) -> int:
    query_tasks = (workflow_payload.get("query_context_request") or {}).get("decision_tasks", ())
    return len(workflow_payload.get("decision_tasks") or ()) + len(query_tasks or ())


def build_backfill_plan(
    repository: MetricsV2Repository,
    settings: Settings,
    *,
    tenant_pub_id: str,
    project_pub_id: str,
    request: SemanticBackfillPlanRequest,
) -> SemanticBackfillPlanView:
    maximum = _maximum_batch_size(settings)
    answer_ids = sorted(set(request.answer_pub_ids))
    if len(answer_ids) > maximum:
        raise ValueError("semantic_backfill_selection_too_large")
    selected_model = resolve_model(settings, request.model)
    as_of = request.as_of.isoformat()
    batch = repository.load_decision_backfill_batch(
        tenant_pub_id=tenant_pub_id,
        project_pub_id=project_pub_id,
        cursor=None,
        limit=len(answer_ids),
        as_of=as_of,
        dry_run=True,
        answer_pub_ids=answer_ids,
    )
    items = list(batch.get("items") or ())
    executable = [item for item in items if isinstance(item.get("workflow_payload"), dict)]
    capture_dates = sorted(str(item.get("capture_time") or "")[:10] for item in items)
    focal_entity_ids = sorted(
        {
            str(candidate.get("candidate_id"))
            for item in executable
            for candidate in (
                (
                    (item["workflow_payload"].get("query_context_request") or {}).get(
                        "candidate_input", {}
                    )
                    or {}
                ).get("candidates", ())
            )
            if isinstance(candidate, dict) and candidate.get("candidate_id")
        }
    )
    atomic_decisions = sum(_task_count(item["workflow_payload"]) for item in executable)
    # Prompt bodies contain the selected answer/query plus a fixed task rubric
    # and schema.  The upper figure adds a 60% input and 2x output reserve; the
    # provider invoice remains authoritative.
    estimated_input_tokens = 0
    for item in executable:
        task_count = _task_count(item["workflow_payload"])
        source_chars = max(0, int((item.get("display") or {}).get("source_char_count") or 0))
        per_decision = max(1_800, 1_200 + (source_chars + 2) // 3)
        estimated_input_tokens += per_decision * task_count
    estimated_output_tokens = atomic_decisions * _ESTIMATED_OUTPUT_TOKENS_PER_DECISION
    high_input_tokens = (estimated_input_tokens * 8 + 4) // 5
    high_output_tokens = atomic_decisions * _HIGH_OUTPUT_TOKENS_PER_DECISION
    option = model_option(settings, selected_model)
    estimated_cost = estimated_cost_usd(
        model=option,
        input_tokens=estimated_input_tokens,
        output_tokens=estimated_output_tokens,
    )
    estimated_cost_high = estimated_cost_usd(
        model=option,
        input_tokens=high_input_tokens,
        output_tokens=high_output_tokens,
    )
    budget_limit = Decimal(str(max(0.0, settings.semantic_decision_daily_budget)))
    blockers: list[str] = []
    if len(items) != len(answer_ids):
        blockers.append("answer_selection_changed")
    if len(executable) != len(answer_ids):
        blockers.append("answer_preparation_incomplete")
    if atomic_decisions <= 0:
        blockers.append("no_executable_decisions")
    if not focal_entity_ids:
        blockers.append("focal_entity_dictionary_missing")
    if budget_limit <= 0:
        blockers.append("semantic_budget_disabled")
    elif estimated_cost_high > budget_limit:
        blockers.append("estimated_cost_exceeds_budget")
    selection_material = {
        "tenant_pub_id": tenant_pub_id,
        "project_pub_id": project_pub_id,
        "answer_pub_ids": answer_ids,
        "model": selected_model,
        "as_of": batch.get("as_of"),
        "batch_hash": batch.get("batch_hash"),
        "catalog_revision": CATALOG_REVISION,
        "estimated_atomic_decisions": atomic_decisions,
        "window": {
            "start": capture_dates[0] if capture_dates else "1970-01-01",
            "end": capture_dates[-1] if capture_dates else "1970-01-01",
        },
        "focal_entity_ids": focal_entity_ids,
    }
    selection_hash = canonical_hash(selection_material)
    confirmation_token = sha256(
        f"metrics-v2-semantic-backfill:{selection_hash}".encode()
    ).hexdigest()
    return SemanticBackfillPlanView.model_validate(
        {
            "project_pub_id": project_pub_id,
            "model": selected_model,
            "as_of": batch["as_of"],
            "window": selection_material["window"],
            "focal_entity_ids": focal_entity_ids,
            "selected_answer_count": len(answer_ids),
            "executable_answer_count": len(executable),
            "preparation_unknown_count": len(items) - len(executable),
            "estimated_atomic_decisions": atomic_decisions,
            "estimated_input_tokens": estimated_input_tokens,
            "estimated_output_tokens": estimated_output_tokens,
            "estimated_cost_usd": float(estimated_cost),
            "estimated_cost_high_usd": float(estimated_cost_high),
            "budget_limit_usd": float(budget_limit),
            "selection_hash": selection_hash,
            "confirmation_token": confirmation_token,
            "start_allowed": not blockers,
            "blocker_codes": blockers,
        }
    )


async def start_backfill(
    settings: Settings,
    *,
    tenant_pub_id: str,
    project_pub_id: str,
    plan: SemanticBackfillPlanView,
    answer_pub_ids: list[str],
) -> SemanticBackfillStartView:
    if not plan.start_allowed:
        raise ValueError("semantic_backfill_plan_blocked")
    workflow_id = f"metrics-v2-backfill/semantic/{plan.selection_hash}"
    job_pub_id = f"sdb_{plan.selection_hash[:26]}"
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    status = "started"
    try:
        await client.start_workflow(
            "SemanticDecisionBackfillWorkflowV2",
            {
                "tenant_pub_id": tenant_pub_id,
                "project_pub_id": project_pub_id,
                "answer_pub_ids": sorted(set(answer_pub_ids)),
                "as_of": plan.as_of.isoformat(),
                "limit": len(answer_pub_ids),
                "dry_run": False,
                "job_pub_id": job_pub_id,
                "judge_model_ref": plan.model,
                "run_metrics_after_semantic": True,
                "metrics_task_queue": settings.metrics_temporal_task_queue,
                "snapshot_scope": {
                    "tenant_pub_id": tenant_pub_id,
                    "project_pub_id": project_pub_id,
                    "window": plan.window.model_dump(mode="json"),
                    "filters": {"model": [], "region": [], "mode": []},
                    "focal_entity_ids": plan.focal_entity_ids,
                    "answer_pub_ids": sorted(set(answer_pub_ids)),
                    "aggregation_method": "query_macro",
                    "design_basis": "observed_cells",
                },
                "analysis_task_queue": settings.analysis_temporal_task_queue,
                "decision_task_queue": settings.decision_temporal_task_queue,
            },
            id=workflow_id,
            task_queue=settings.decision_temporal_task_queue,
        )
    except WorkflowAlreadyStartedError:
        status = "reused"
    return SemanticBackfillStartView(
        project_pub_id=project_pub_id,
        workflow_id=workflow_id,
        job_pub_id=job_pub_id,
        selection_hash=plan.selection_hash,
        status=status,
        selected_answer_count=len(answer_pub_ids),
        model=plan.model,
    )


async def backfill_status(
    settings: Settings,
    *,
    project_pub_id: str,
    selection_hash: str,
) -> SemanticBackfillStatusView:
    workflow_id = f"metrics-v2-backfill/semantic/{selection_hash}"
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    handle = client.get_workflow_handle(workflow_id)
    description = await handle.describe()
    if description.status is WorkflowExecutionStatus.RUNNING:
        return SemanticBackfillStatusView(
            project_pub_id=project_pub_id,
            selection_hash=selection_hash,
            workflow_id=workflow_id,
            status="running",
            processed_answer_count=0,
            metric_evaluation_count=0,
        )
    if description.status is not WorkflowExecutionStatus.COMPLETED:
        return SemanticBackfillStatusView(
            project_pub_id=project_pub_id,
            selection_hash=selection_hash,
            workflow_id=workflow_id,
            status="failed",
            processed_answer_count=0,
            metric_evaluation_count=0,
            failure_code="semantic_backfill_workflow_failed",
        )
    try:
        result = await handle.result()
    except WorkflowFailureError:
        return SemanticBackfillStatusView(
            project_pub_id=project_pub_id,
            selection_hash=selection_hash,
            workflow_id=workflow_id,
            status="failed",
            processed_answer_count=0,
            metric_evaluation_count=0,
            failure_code="semantic_backfill_workflow_failed",
        )
    result = dict(result or {})
    metrics = dict(result.get("metrics") or {})
    snapshot = dict(result.get("snapshot") or {})
    return SemanticBackfillStatusView(
        project_pub_id=project_pub_id,
        selection_hash=selection_hash,
        workflow_id=workflow_id,
        status="succeeded",
        processed_answer_count=int(result.get("processed_count") or 0),
        metric_evaluation_count=len(metrics.get("evaluation_pub_ids") or ()),
        snapshot_set_pub_id=snapshot.get("snapshot_set_pub_id"),
    )


__all__ = [
    "backfill_options",
    "backfill_status",
    "build_backfill_plan",
    "start_backfill",
]
