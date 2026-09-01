#!/usr/bin/env python3
"""Plan or explicitly start one bounded GEO metrics V2 backfill page.

Dry-run is the default.  Applying requires the exact selection hash and
confirmation token printed by a fresh dry-run.  Semantic and deterministic
metric replay are separate stages so model budgets, review queues, and unknown
rates can be inspected before aggregation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from geo_platform.config import get_settings  # noqa: E402
from geo_platform.metrics_v2.repository import MetricsV2Repository  # noqa: E402
from temporalio.client import Client, WorkflowHandle  # noqa: E402

from domain.analysis.v2._canonical import canonical_hash  # noqa: E402

_PUBLIC_ID = re.compile(r"^[a-z][a-z0-9]*_[A-Za-z0-9_-]{1,116}$")


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-pub-id", required=True)
    parser.add_argument("--project-pub-id", required=True)
    parser.add_argument("--stage", choices=("semantic", "metrics"), required=True)
    parser.add_argument("--as-of", help="frozen UTC ISO-8601 upper bound")
    parser.add_argument("--cursor", help="opaque cursor returned by the previous page")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--dsn", help="worker PostgreSQL DSN")
    parser.add_argument("--temporal-address")
    parser.add_argument("--temporal-namespace")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--selection-hash")
    parser.add_argument("--confirm-token")
    parser.add_argument("--wait", action="store_true")
    arguments = parser.parse_args(argv)
    for field in ("tenant_pub_id", "project_pub_id"):
        if _PUBLIC_ID.fullmatch(str(getattr(arguments, field))) is None:
            parser.error(f"--{field.replace('_', '-')} is not a valid public ID")
    maximum = 1000 if arguments.stage == "semantic" else 2000
    if not 1 <= arguments.batch_size <= maximum:
        parser.error(f"--batch-size must be between 1 and {maximum}")
    confirmations = (arguments.selection_hash, arguments.confirm_token)
    if arguments.apply and not all(confirmations):
        parser.error("--apply requires --selection-hash and --confirm-token")
    if not arguments.apply and any(confirmations):
        parser.error("confirmation arguments are only valid with --apply")
    return arguments


def _dsn(value: str | None) -> str:
    settings = get_settings()
    configured = value or settings.worker_postgres_dsn or settings.postgres_dsn
    return configured.replace("postgresql+psycopg://", "postgresql://", 1)


def _confirmation_token(selection_hash: str) -> str:
    return sha256(f"geo-metrics-v2-backfill:{selection_hash}".encode()).hexdigest()


def plan_backfill(
    repository: MetricsV2Repository,
    *,
    tenant_pub_id: str,
    project_pub_id: str,
    stage: str,
    cursor: str | None,
    limit: int,
    as_of: str | None,
) -> dict[str, Any]:
    loader = (
        repository.load_decision_backfill_batch
        if stage == "semantic"
        else repository.load_metrics_backfill_batch
    )
    batch = loader(
        tenant_pub_id=tenant_pub_id,
        project_pub_id=project_pub_id,
        cursor=cursor,
        limit=limit,
        as_of=as_of,
        dry_run=True,
    )
    items = list(batch.get("items") or ())
    prepared = [item for item in items if isinstance(item.get("workflow_payload"), dict)]
    reason_counts: dict[str, int] = {}
    for item in items:
        for reason in item.get("reason_codes") or ():
            key = str(reason)
            reason_counts[key] = reason_counts.get(key, 0) + 1
    estimated_tasks = sum(
        len(item["workflow_payload"].get("decision_tasks", ()))
        + len(
            (item["workflow_payload"].get("query_context_request") or {}).get("decision_tasks", ())
        )
        for item in prepared
    )
    selection = {
        "tenant_pub_id": tenant_pub_id,
        "project_pub_id": project_pub_id,
        "stage": stage,
        "as_of": batch.get("as_of"),
        "cursor": cursor,
        "batch_hash": batch.get("batch_hash"),
        "page_count": batch.get("page_count", len(items)),
    }
    selection_hash = canonical_hash(selection)
    settings = get_settings()
    report = {
        "schema_version": "metrics-v2-backfill-plan-v1",
        "mode": "dry_run",
        "stage": stage,
        "selection_hash": selection_hash,
        "confirm_token": _confirmation_token(selection_hash),
        "as_of": batch.get("as_of"),
        "cursor": cursor,
        "next_cursor": batch.get("next_cursor"),
        "candidate_count": int(batch.get("candidate_count") or 0),
        "page_count": int(batch.get("page_count") or len(items)),
        "prepared_count": len(prepared) if stage == "semantic" else len(items),
        "preparation_unknown_count": (
            len(items) - len(prepared)
            if stage == "semantic"
            else int(batch.get("unknown_count") or 0)
        ),
        "preparation_reason_counts": dict(sorted(reason_counts.items())),
        "estimated_atomic_decisions": estimated_tasks,
        "configured_daily_model_budget": settings.semantic_decision_daily_budget,
        "budget_gate": (
            "blocked_model_attempts_fail_llm_api_budget_exhausted"
            if stage == "semantic" and settings.semantic_decision_daily_budget <= 0
            else "configured"
        ),
        "batch_hash": batch.get("batch_hash"),
        "official_activation": False,
    }
    return report


async def _start(arguments: argparse.Namespace, plan: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    client = await Client.connect(
        arguments.temporal_address or settings.temporal_address,
        namespace=arguments.temporal_namespace or settings.temporal_namespace,
    )
    workflow_type = (
        "SemanticDecisionBackfillWorkflowV2"
        if arguments.stage == "semantic"
        else "MetricsBackfillWorkflowV2"
    )
    task_queue = (
        settings.decision_temporal_task_queue
        if arguments.stage == "semantic"
        else settings.metrics_temporal_task_queue
    )
    selection_hash = str(plan["selection_hash"])
    payload = {
        "tenant_pub_id": arguments.tenant_pub_id,
        "project_pub_id": arguments.project_pub_id,
        "cursor": arguments.cursor,
        "as_of": plan["as_of"],
        "limit": arguments.batch_size,
        "dry_run": False,
        "job_pub_id": f"sdb_{selection_hash[:26]}",
    }
    if arguments.stage == "semantic":
        payload.update(
            {
                "analysis_task_queue": settings.analysis_temporal_task_queue,
                "decision_task_queue": settings.decision_temporal_task_queue,
            }
        )
    else:
        payload["metrics_task_queue"] = settings.metrics_temporal_task_queue
    handle: WorkflowHandle[Any, Any] = await client.start_workflow(
        workflow_type,
        payload,
        id=f"metrics-v2-backfill/{arguments.stage}/{selection_hash}",
        task_queue=task_queue,
    )
    output: dict[str, Any] = {
        "mode": "started",
        "stage": arguments.stage,
        "workflow_id": handle.id,
        "selection_hash": selection_hash,
        "as_of": plan["as_of"],
        "official_activation": False,
    }
    if arguments.wait:
        output["result"] = await handle.result()
    return output


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    repository = MetricsV2Repository(_dsn(arguments.dsn))
    plan = plan_backfill(
        repository,
        tenant_pub_id=arguments.tenant_pub_id,
        project_pub_id=arguments.project_pub_id,
        stage=arguments.stage,
        cursor=arguments.cursor,
        limit=arguments.batch_size,
        as_of=arguments.as_of,
    )
    if not arguments.apply:
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
        return 0
    if arguments.selection_hash != plan["selection_hash"]:
        raise SystemExit("backfill selection changed; run dry-run again")
    if arguments.confirm_token != _confirmation_token(str(plan["selection_hash"])):
        raise SystemExit("backfill confirmation token mismatch")
    if arguments.stage == "semantic" and int(plan["prepared_count"]) == 0:
        raise SystemExit("semantic backfill has no prepared reference-only work items")
    result = asyncio.run(_start(arguments, plan))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
