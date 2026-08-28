"""Temporal orchestration for query context and atomic semantic decisions V2."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from domain.analysis.v2 import instantiate_decision_task_request
    from workflows.activities.semantic_decisions_v2 import (
        adjudicate_decision_activity,
        build_candidates_activity,
        commit_semantic_decision_backfill_cursor_activity,
        create_decision_request_activity,
        derive_events_activity,
        derive_query_context_activity,
        freeze_decision_input_activity,
        load_semantic_decision_backfill_batch_activity,
        persist_decision_activity,
        persist_events_activity,
        persist_query_context_activity,
        retrieve_evidence_activity,
        run_model_judge_activity,
    )

_IO_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
_MODEL_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=2,
)


def _queues(payload: dict[str, Any]) -> tuple[str, str]:
    return (
        str(payload.get("analysis_task_queue") or "geo-platform-v2-analysis"),
        str(payload.get("decision_task_queue") or "geo-platform-v2-decision"),
    )


@workflow.defn(name="SemanticDecisionWorkflowV2")
class SemanticDecisionWorkflowV2:
    """Run one leaf DecisionTask with idempotent immutable persistence."""

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        analysis_queue, decision_queue = _queues(payload)
        frozen: dict[str, Any] = await workflow.execute_activity(
            freeze_decision_input_activity,
            payload,
            task_queue=analysis_queue,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_IO_RETRY,
        )
        request: dict[str, Any] = await workflow.execute_activity(
            create_decision_request_activity,
            payload
            | frozen
            | {
                "workflow_id": workflow.info().workflow_id,
                "run_id": workflow.info().run_id,
            },
            task_queue=decision_queue,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_IO_RETRY,
        )
        if request.get("status") in {
            "succeeded",
            "abstained",
            "review_required",
            "failed",
        } and request.get("decision"):
            return request

        evidence: dict[str, Any] | None = None
        if isinstance(payload.get("evidence_bundle"), dict):
            evidence = await workflow.execute_activity(
                retrieve_evidence_activity,
                payload["evidence_bundle"],
                task_queue=analysis_queue,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=_IO_RETRY,
            )

        attempts = list(payload.get("attempts", []))
        dependency_statuses = dict(payload.get("dependency_statuses") or {})
        blocked = any(
            dependency_statuses.get(str(task_ref)) != "accepted"
            for task_ref in payload.get("dependency_task_refs", [])
        )
        if not attempts and not blocked:
            attempt: dict[str, Any] = await workflow.execute_activity(
                run_model_judge_activity,
                payload
                | frozen
                | request
                | {
                    "evidence_bundle_ref": evidence and evidence["bundle_ref"],
                    "evidence_bundle_hash": evidence and evidence["bundle_hash"],
                },
                task_queue=decision_queue,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=_MODEL_RETRY,
            )
            attempts.append(attempt)

        decision: dict[str, Any] = await workflow.execute_activity(
            adjudicate_decision_activity,
            payload
            | frozen
            | request
            | {
                "attempts": attempts,
                "decision_pub_id": str(
                    payload.get("decision_pub_id")
                    or f"sdr_{str(request['decision_job_pub_id']).removeprefix('sdj_')}"
                ),
                "evidence_refs": [evidence["bundle_ref"]] if evidence else [],
                "evidence_context": (
                    {
                        "evidence_bundle_status": evidence["status"],
                        "retrieval_protocol_complete": evidence["status"] == "ready",
                        "truth_as_of_policy": payload.get(
                            "truth_as_of_policy", "answer_capture_time"
                        ),
                    }
                    if evidence
                    else dict(payload.get("evidence_context") or {})
                ),
                "created_at": workflow.now().isoformat(),
            },
            task_queue=decision_queue,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_IO_RETRY,
        )
        persisted: dict[str, Any] = await workflow.execute_activity(
            persist_decision_activity,
            {
                "decision": decision,
                "attempts": attempts,
                "workflow_id": workflow.info().workflow_id,
                "run_id": workflow.info().run_id,
            },
            task_queue=decision_queue,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_IO_RETRY,
        )
        return {**persisted, "decision": decision}


@workflow.defn(name="QueryContextClassificationWorkflowV2")
class QueryContextClassificationWorkflowV2:
    """Resolve query intent/entities and persist focal-relative exposure."""

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        analysis_queue, decision_queue = _queues(payload)
        candidate_set: dict[str, Any] = await workflow.execute_activity(
            build_candidates_activity,
            dict(payload["candidate_input"]),
            task_queue=analysis_queue,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_IO_RETRY,
        )
        decisions: list[dict[str, Any]] = []
        for index, task_payload in enumerate(payload.get("decision_tasks", [])):
            result = await workflow.execute_child_workflow(
                SemanticDecisionWorkflowV2.run,
                dict(task_payload)
                | {
                    "tenant_pub_id": payload["tenant_pub_id"],
                    "project_pub_id": payload["project_pub_id"],
                    "candidate_set": candidate_set,
                    "candidate_set_hash": candidate_set["candidate_set_hash"],
                    "analysis_task_queue": analysis_queue,
                    "decision_task_queue": decision_queue,
                },
                id=f"{workflow.info().workflow_id}:decision:{index}",
                task_queue=decision_queue,
            )
            if isinstance(result.get("decision"), dict):
                decisions.append(result["decision"])
        derived: dict[str, Any] = await workflow.execute_activity(
            derive_query_context_activity,
            payload | {"decisions": decisions},
            task_queue=analysis_queue,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_IO_RETRY,
        )
        persisted: dict[str, Any] = await workflow.execute_activity(
            persist_query_context_activity,
            {
                "tenant_pub_id": payload["tenant_pub_id"],
                "project_pub_id": payload["project_pub_id"],
                **derived,
            },
            task_queue=analysis_queue,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_IO_RETRY,
        )
        return {
            **persisted,
            **derived,
            "candidate_set": candidate_set,
            "decisions": decisions,
        }


@workflow.defn(name="AnswerSemanticEventWorkflowV2")
class AnswerSemanticEventWorkflowV2:
    """Persist a full per-capability manifest even when no events exist."""

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        analysis_queue, decision_queue = _queues(payload)
        query_context: dict[str, Any] | None = None
        if isinstance(payload.get("query_context_request"), dict):
            query_context = await workflow.execute_child_workflow(
                QueryContextClassificationWorkflowV2.run,
                dict(payload["query_context_request"])
                | {
                    "analysis_task_queue": analysis_queue,
                    "decision_task_queue": decision_queue,
                },
                id=f"{workflow.info().workflow_id}:query-context",
                task_queue=analysis_queue,
            )
        decisions = list(payload.get("decisions", []))
        existing_decision_ids = {
            str(item.get("decision_pub_id"))
            for item in decisions
            if isinstance(item, dict) and item.get("decision_pub_id")
        }
        for decision in (query_context or {}).get("decisions", []):
            decision_pub_id = str(decision.get("decision_pub_id") or "")
            if decision_pub_id and decision_pub_id not in existing_decision_ids:
                decisions.append(decision)
                existing_decision_ids.add(decision_pub_id)
        dependency_statuses = {
            f"{item['task_name']}@{item['task_version']}": item["status"]
            for item in (query_context or {}).get("decisions", [])
        }
        for index, task_payload in enumerate(payload.get("decision_tasks", [])):
            result = await workflow.execute_child_workflow(
                SemanticDecisionWorkflowV2.run,
                dict(task_payload)
                | {
                    "tenant_pub_id": payload["tenant_pub_id"],
                    "project_pub_id": payload["project_pub_id"],
                    "analysis_task_queue": analysis_queue,
                    "decision_task_queue": decision_queue,
                    "candidate_set": (query_context or {}).get("candidate_set"),
                    "candidate_set_hash": ((query_context or {}).get("candidate_set") or {}).get(
                        "candidate_set_hash"
                    ),
                    "dependency_statuses": dependency_statuses,
                },
                id=f"{workflow.info().workflow_id}:answer-decision:{index}",
                task_queue=decision_queue,
            )
            if isinstance(result.get("decision"), dict):
                decisions.append(result["decision"])
                decision = result["decision"]
                dependency_statuses[f"{decision['task_name']}@{decision['task_version']}"] = (
                    decision["status"]
                )

        dynamic_templates = dict(payload.get("dynamic_task_templates") or {})
        dynamic_inputs = dict(payload.get("dynamic_inputs") or {})
        forced_capability_failures: dict[str, list[str]] = {}
        dynamic_index = 0

        async def execute_dynamic(
            task_ref: str,
            subject_ref: dict[str, Any],
            *,
            extra: dict[str, Any] | None = None,
        ) -> dict[str, Any] | None:
            nonlocal dynamic_index
            template = dynamic_templates.get(task_ref)
            if not isinstance(template, dict):
                return None
            request = instantiate_decision_task_request(template, subject_ref)
            result = await workflow.execute_child_workflow(
                SemanticDecisionWorkflowV2.run,
                request
                | dict(extra or {})
                | {
                    "tenant_pub_id": payload["tenant_pub_id"],
                    "project_pub_id": payload["project_pub_id"],
                    "analysis_task_queue": analysis_queue,
                    "decision_task_queue": decision_queue,
                    "candidate_set": (query_context or {}).get("candidate_set"),
                    "candidate_set_hash": ((query_context or {}).get("candidate_set") or {}).get(
                        "candidate_set_hash"
                    ),
                    "dependency_statuses": dependency_statuses,
                },
                id=(f"{workflow.info().workflow_id}:dynamic-decision:{dynamic_index}"),
                task_queue=decision_queue,
            )
            dynamic_index += 1
            decision = result.get("decision")
            if isinstance(decision, dict):
                decision_pub_id = str(decision.get("decision_pub_id") or "")
                if decision_pub_id and decision_pub_id not in existing_decision_ids:
                    decisions.append(decision)
                    existing_decision_ids.add(decision_pub_id)
                dependency_statuses[f"{decision['task_name']}@{decision['task_version']}"] = (
                    decision["status"]
                )
                return decision
            return None

        claim_extraction = next(
            (
                item
                for item in decisions
                if item.get("task_name") == "claim-extraction" and item.get("status") == "accepted"
            ),
            None,
        )
        claims = (
            list((claim_extraction.get("result") or {}).get("claims") or [])
            if isinstance(claim_extraction, dict)
            else []
        )
        claim_extraction_pub_id = (
            str(claim_extraction["decision_pub_id"]) if isinstance(claim_extraction, dict) else ""
        )
        maximum_claims = max(0, int(dynamic_inputs.get("maximum_claims") or 100))
        citations = sorted(set(map(str, dynamic_inputs.get("citation_pub_ids") or [])))
        maximum_citations = max(0, int(dynamic_inputs.get("maximum_citations") or 50))
        if len(claims) > maximum_claims:
            forced_capability_failures["claim_evidence_verdict"] = ["claim_fanout_budget_exceeded"]
            if citations:
                forced_capability_failures["citation_claim_support"] = [
                    "claim_fanout_budget_exceeded"
                ]
        elif len(citations) > maximum_citations:
            forced_capability_failures["citation_claim_support"] = [
                "citation_fanout_budget_exceeded"
            ]

        if not forced_capability_failures.get("claim_evidence_verdict"):
            for claim in claims:
                if not isinstance(claim, dict) or not claim.get("claim_fingerprint"):
                    forced_capability_failures["claim_evidence_verdict"] = ["claim_subject_invalid"]
                    continue
                claim_fingerprint = str(claim["claim_fingerprint"])
                subject_ref = {
                    "answer_pub_id": payload["answer_pub_id"],
                    "claim_fingerprint": claim_fingerprint,
                }
                verifiability = await execute_dynamic(
                    "claim-verifiability@2.0.0",
                    subject_ref,
                    extra={"parent_decision_pub_ids": [claim_extraction_pub_id]},
                )
                evidence_bundles = dict(dynamic_inputs.get("evidence_bundles_by_claim") or {})
                evidence_bundle = evidence_bundles.get(claim_fingerprint)
                await execute_dynamic(
                    "claim-evidence-verdict@2.0.0",
                    subject_ref,
                    extra={
                        "parent_decision_pub_ids": [
                            claim_extraction_pub_id,
                            *(
                                [str(verifiability["decision_pub_id"])]
                                if isinstance(verifiability, dict)
                                else []
                            ),
                        ],
                        **(
                            {"evidence_bundle": evidence_bundle}
                            if isinstance(evidence_bundle, dict)
                            else {}
                        ),
                    },
                )
                if not forced_capability_failures.get("citation_claim_support"):
                    for citation_pub_id in citations:
                        await execute_dynamic(
                            "citation-claim-support@2.0.0",
                            {
                                "answer_pub_id": payload["answer_pub_id"],
                                "citation_pub_id": citation_pub_id,
                                "claim_fingerprint": claim_fingerprint,
                            },
                            extra={
                                "parent_decision_pub_ids": [claim_extraction_pub_id],
                                **(
                                    {"evidence_bundle": evidence_bundle}
                                    if isinstance(evidence_bundle, dict)
                                    else {}
                                ),
                            },
                        )
        derived: dict[str, Any] = await workflow.execute_activity(
            derive_events_activity,
            payload | {"decisions": decisions, "created_at": workflow.now().isoformat()},
            task_queue=decision_queue,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_IO_RETRY,
        )
        capability_statuses = dict(derived["capability_statuses"])
        for name in payload.get("required_capabilities", []):
            capability_statuses.setdefault(
                str(name),
                {
                    "status": "failed",
                    "decision_record_pub_ids": [],
                    "reason_codes": ["decision_missing"],
                },
            )
        for name, reason_codes in forced_capability_failures.items():
            existing = dict(capability_statuses.get(name) or {})
            capability_statuses[name] = {
                "status": "failed",
                "decision_record_pub_ids": list(existing.get("decision_record_pub_ids") or []),
                "reason_codes": sorted(set(existing.get("reason_codes") or []) | set(reason_codes)),
            }
        capability_states = {
            name: str(item["status"]) for name, item in capability_statuses.items()
        }
        states = set(capability_states.values())
        if "review_required" in states:
            status = "review_required"
        elif "failed" in states and len(states) == 1:
            status = "failed"
        elif states & {"failed", "abstained"}:
            status = "partial"
        else:
            status = "ready"
        manifest = dict(payload["manifest"])
        if query_context is not None:
            manifest["query_context_fact_pub_id"] = query_context["query_context_fact_pub_id"]
        manifest.update(
            {
                "status": status,
                "capability_statuses": capability_statuses,
                "decision_record_pub_ids": derived["decision_record_pub_ids"],
                "decision_set_hash": derived["decision_set_hash"],
                "event_count": len(derived["events"]),
                "evidenced_event_count": sum(
                    event.get("answer_text_start") is not None for event in derived["events"]
                ),
                "event_set_hash": derived["event_set_hash"],
                "completed_at": workflow.now().isoformat(),
            }
        )
        persisted: dict[str, Any] = await workflow.execute_activity(
            persist_events_activity,
            {
                "tenant_pub_id": payload["tenant_pub_id"],
                "project_pub_id": payload["project_pub_id"],
                "manifest": manifest,
                "events": derived["events"],
            },
            task_queue=decision_queue,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_IO_RETRY,
        )
        return {**persisted, "manifest": manifest, "events": derived["events"]}


@workflow.defn(name="SemanticDecisionBackfillWorkflowV2")
class SemanticDecisionBackfillWorkflowV2:
    """Process one bounded stable-keyset historical page.

    Callers explicitly enqueue the returned next cursor.  This bounds Temporal
    history and makes pause/budget control observable instead of using sleeps.
    """

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        analysis_queue, decision_queue = _queues(payload)
        batch: dict[str, Any] = await workflow.execute_activity(
            load_semantic_decision_backfill_batch_activity,
            payload,
            task_queue=analysis_queue,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_IO_RETRY,
        )
        if bool(payload.get("dry_run", False)):
            return batch
        results: list[dict[str, Any]] = []
        for index, item in enumerate(batch.get("items", [])):
            semantic_request = item.get("workflow_payload")
            if not isinstance(semantic_request, dict):
                raise ValueError("semantic_backfill_workflow_payload_missing")
            result = await workflow.execute_child_workflow(
                AnswerSemanticEventWorkflowV2.run,
                semantic_request
                | {
                    "analysis_task_queue": analysis_queue,
                    "decision_task_queue": decision_queue,
                },
                id=(f"{workflow.info().workflow_id}:answer:{str(item['answer_pub_id'])}:{index}"),
                task_queue=decision_queue,
            )
            results.append(result)
        counts: dict[str, int] = {}
        for result in results:
            status = str((result.get("manifest") or {}).get("status") or "failed")
            counts[status] = counts.get(status, 0) + 1
        audit: dict[str, Any] = await workflow.execute_activity(
            commit_semantic_decision_backfill_cursor_activity,
            {
                "tenant_pub_id": payload["tenant_pub_id"],
                "project_pub_id": payload.get("project_pub_id"),
                "job_pub_id": payload["job_pub_id"],
                "previous_cursor": payload.get("cursor"),
                "cursor": batch.get("next_cursor"),
                "input_count": len(batch.get("items", [])),
                "status_counts": counts,
                "cost_amount": sum(float(item.get("cost_amount") or 0) for item in results),
                "batch_hash": batch.get("batch_hash"),
            },
            task_queue=decision_queue,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_IO_RETRY,
        )
        return {
            "processed_count": len(results),
            "status_counts": counts,
            "next_cursor": batch.get("next_cursor"),
            "done": batch.get("next_cursor") is None,
            "audit": audit,
        }
