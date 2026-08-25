"""Transactional Service 2 all-U corpus materialization and review service.

The service starts from successful terminal collection tasks and preserves every
``answer_source_occurrence`` they produced.  A run is only a frozen selection
envelope: ``completed_with_failures`` is admissible, successful query output is
kept, and each failed query is written to a separate coverage ledger.  Only
network work is reusable by URL; every admitted occurrence becomes one item.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from typing import Any
from urllib.parse import urlsplit

from PIL import Image
from sqlalchemy import text
from sqlalchemy.orm import Session

from domain.scoring.service2_source_corpus import (
    FACT_SCHEMA_VERSION,
    AttributionConfidence,
    DisparagementLevel,
    FactAnchorState,
    Ledger,
    OrthogonalFlags,
    RelationDirection,
    RelationFindingCandidate,
    ValidationStatus,
    VisualValidationStatus,
    attribution_wording_allowed,
    customer_case_eligible,
    validate_relation_finding,
    validated_visual_bbox,
    visual_anchor_matches_quote,
)
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.tenancy.ids import new_pub_id

from .analysis_models import DEFAULT_SERVICE2_ANALYSIS_MODELS
from .schemas import BatchCreate, FindingCreate, FindingReviewCreate

QUERY_COVERAGE_POLICY_VERSION = "service2-query-outcomes-v1"
RowData = Mapping[Any, Any]


class Service2CorpusError(RuntimeError):
    pass


class NotFound(Service2CorpusError):
    pass


class Conflict(Service2CorpusError):
    pass


class Invalid(Service2CorpusError):
    pass


class EvidenceInvalid(Invalid):
    """A fail-closed finding rejection whose analysis-attempt audit must commit."""


class PreconditionFailed(Service2CorpusError):
    pass


@dataclass(frozen=True, slots=True)
class MaterializedBatch:
    batch_pub_id: str
    replayed: bool


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_json(value: object) -> str:
    return sha256(_canonical_json(value).encode()).hexdigest()


def _stable_uuid(key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"geo-service2:{key}")


def _stable_pub_id(prefix: str, key: str) -> str:
    return f"{prefix}_{sha256(key.encode()).hexdigest()[:26]}"


def _idempotency_hash(tenant_pub_id: str, operation: str, key: str) -> str:
    return sha256(f"{tenant_pub_id}|{operation}|{key}".encode()).hexdigest()


def _mapping(row: RowData | None, code: str) -> RowData:
    if row is None:
        raise NotFound(code)
    return row


def _safe_matrix(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _fetch_projection(row: RowData) -> tuple[str, str, str, str]:
    """Return fetch, processing, entity and judgment states for one U item."""

    snapshot_state = str(row.get("snapshot_state") or "")
    attempt_state = str(row.get("attempt_state") or "")
    has_text = bool(
        row.get("snapshot_id") and row.get("body_object_key") and row.get("text_sha256")
    )
    if snapshot_state == "succeeded" and has_text:
        return "succeeded", "queued", "pending", "pending"
    if snapshot_state == "partial":
        return "partial", "manual_evidence_required", "pending", "pending"
    if snapshot_state in {"blocked", "gone", "failed"}:
        processing = {
            "blocked": "blocked",
            "gone": "gone",
            "failed": "failed",
        }[snapshot_state]
        return snapshot_state, processing, "pending", "pending"
    if attempt_state in {
        "queued",
        "fetching",
        "succeeded",
        "partial",
        "blocked",
        "gone",
        "retry_wait",
        "failed",
    }:
        processing = {
            "queued": "queued",
            "fetching": "fetching",
            "succeeded": "queued",
            "partial": "manual_evidence_required",
            "blocked": "blocked",
            "gone": "gone",
            "retry_wait": "retry_wait",
            "failed": "failed",
        }[attempt_state]
        return attempt_state, processing, "pending", "pending"
    if str(row.get("u_state")) == "unobserved":
        # Historical final-reference-only rows are still part of the U fact
        # plane, but the original U stage must remain honestly unobserved.
        return "unobserved", "unobservable", "pending", "pending"
    return "queued", "queued", "pending", "pending"


_PUBLIC_EVIDENCE_ID_KEYS = (
    "evidence_pub_id",
    "source_pub_id",
    "document_pub_id",
    "account_pub_id",
    "approval_pub_id",
)
_PUBLIC_EVIDENCE_TEXT_KEYS = ("title", "evidence_type", "type")


def _safe_public_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    try:
        parsed = urlsplit(candidate)
        return candidate if parsed.scheme.lower() in {"http", "https"} and parsed.hostname else None
    except ValueError:
        return None


def _safe_evidence_projection(value: object) -> list[dict[str, str]]:
    """Project customer facts to public references; drop arbitrary internal keys."""

    if not isinstance(value, list | tuple):
        return []
    projected: list[dict[str, str]] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        row: dict[str, str] = {}
        for key in _PUBLIC_EVIDENCE_ID_KEYS + _PUBLIC_EVIDENCE_TEXT_KEYS:
            candidate = raw.get(key)
            if isinstance(candidate, str) and candidate.strip():
                row[key] = candidate.strip()
        for key in ("url", "source_url"):
            candidate = _safe_public_url(raw.get(key))
            if candidate:
                row[key] = candidate
        if row:
            projected.append(row)
    return projected


def _factcheck_manifest_projection(row: RowData) -> dict[str, object]:
    """Keep the reviewed verdict, sources, and explicit uncertainty boundary together."""

    return {
        "factcheck_claim": row.get("factcheck_claim"),
        "factcheck_verdict": row.get("factcheck_verdict"),
        "factcheck_evidence": _safe_evidence_projection(row.get("factcheck_evidence")),
        "factcheck_boundary": row.get("factcheck_boundary"),
    }


def _relation_version_hash(
    *,
    relation: Mapping[str, object],
    candidate_input_hash: str,
    visual_status: VisualValidationStatus,
    visual_anchor: Mapping[str, object],
) -> str:
    """Dedupe exact retries while versioning material evidence changes append-only."""

    return _hash_json(
        {
            "relation": dict(relation),
            "candidate_input_hash": candidate_input_hash,
            "visual_status": visual_status,
            "visual_anchor": dict(visual_anchor),
        }
    )


class Service2CorpusService:
    def __init__(
        self,
        *,
        store: ContentAddressedObjectStore | None = None,
        allowed_analysis_models: Iterable[str] = DEFAULT_SERVICE2_ANALYSIS_MODELS,
    ) -> None:
        self.store = store
        self.allowed_analysis_models = frozenset(
            value.strip() for value in allowed_analysis_models if value.strip()
        )

    def create_batch(
        self,
        session: Session,
        *,
        tenant_id: uuid.UUID,
        tenant_pub_id: str,
        project_id: uuid.UUID,
        project_pub_id: str,
        actor_pub_id: str,
        idempotency_key: str,
        body: BatchCreate,
    ) -> MaterializedBatch:
        now = datetime.now(UTC)
        if body.source_snapshot_boundary > now:
            raise Invalid("snapshot_boundary_in_future")
        if body.analysis_model not in self.allowed_analysis_models:
            raise Invalid("analysis_model_not_allowed")
        run_pub_ids = sorted(body.run_pub_ids)
        scope_selector = {
            "tenant_pub_id": tenant_pub_id,
            "project_pub_id": project_pub_id,
            "run_pub_ids": run_pub_ids,
            "window_start": body.window_start.astimezone(UTC).isoformat(),
            "window_end": body.window_end.astimezone(UTC).isoformat(),
            "source_snapshot_boundary": body.source_snapshot_boundary.astimezone(UTC).isoformat(),
            "corpus_policy_version": body.corpus_policy_version,
            "judgment_policy_version": body.judgment_policy_version,
            "query_coverage_policy_version": QUERY_COVERAGE_POLICY_VERSION,
            "analysis_model": body.analysis_model,
        }
        scope_hash = _hash_json(scope_selector)
        idem = _idempotency_hash(tenant_pub_id, "service2-batch-create", idempotency_key)
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
            {"scope": f"service2:{tenant_pub_id}:{idem}"},
        )
        replay = (
            session.execute(
                text(
                    """
                    SELECT batch.pub_id,batch.scope_selector_hash
                    FROM platform.service2_batch_event event
                    JOIN platform.service2_corpus_batch batch ON batch.id=event.batch_id
                    WHERE event.tenant_id=:tenant_id AND event.idempotency_key=:idem
                    """
                ),
                {"tenant_id": tenant_id, "idem": idem},
            )
            .mappings()
            .one_or_none()
        )
        if replay is not None:
            if replay["scope_selector_hash"] != scope_hash:
                raise Conflict("idempotency_key_payload_conflict")
            return MaterializedBatch(str(replay["pub_id"]), True)

        existing = (
            session.execute(
                text(
                    """
                    SELECT pub_id FROM platform.service2_corpus_batch
                    WHERE tenant_id=:tenant_id AND scope_selector_hash=:scope_hash
                    """
                ),
                {"tenant_id": tenant_id, "scope_hash": scope_hash},
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            return MaterializedBatch(str(existing["pub_id"]), True)

        entitlement = (
            session.execute(
                text(
                    """
                    SELECT id,pub_id,catalog_version,state,authorized_from,authorized_until,
                           updated_at
                    FROM platform.project_service_entitlement
                    WHERE tenant_id=:tenant_id AND project_id=:project_id
                      AND service_code='outbound_disparagement_audit' AND state='active'
                      AND (authorized_from IS NULL OR authorized_from<=:window_end)
                      AND (authorized_until IS NULL OR authorized_until>=:window_start)
                    ORDER BY updated_at DESC,pub_id DESC LIMIT 1
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "window_start": body.window_start,
                    "window_end": body.window_end,
                },
            )
            .mappings()
            .one_or_none()
        )
        if entitlement is None:
            raise Conflict("service2_entitlement_required")
        entitlement_revision = _hash_json(
            {
                "pub_id": str(entitlement["pub_id"]),
                "catalog_version": str(entitlement["catalog_version"]),
                "state": str(entitlement["state"]),
                "authorized_from": (
                    entitlement["authorized_from"].isoformat()
                    if entitlement["authorized_from"]
                    else None
                ),
                "authorized_until": (
                    entitlement["authorized_until"].isoformat()
                    if entitlement["authorized_until"]
                    else None
                ),
                "updated_at": entitlement["updated_at"].isoformat(),
            }
        )
        run_rows = (
            session.execute(
                text(
                    """
                    SELECT id,pub_id,state,total_tasks,completed_tasks,failed_tasks
                    FROM platform.collection_run
                    WHERE tenant_id=:tenant_id AND project_id=:project_id
                      AND pub_id=ANY(:run_pub_ids)
                    ORDER BY pub_id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "run_pub_ids": run_pub_ids,
                },
            )
            .mappings()
            .all()
        )
        if [str(row["pub_id"]) for row in run_rows] != run_pub_ids:
            raise NotFound("one_or_more_runs_not_found_in_project")
        if any(
            str(row["state"]) not in {"completed", "completed_with_failures"} for row in run_rows
        ):
            raise Conflict("service2_runs_must_be_terminal")
        run_ids = [row["id"] for row in run_rows]
        query_rows = (
            session.execute(
                text(
                    """
                    SELECT task.id,task.pub_id,task.run_id,run.pub_id AS run_pub_id,
                           task.state,task.quality_state,
                           btrim(COALESCE(task.answer_text,''))<>'' AS answer_present,
                           count(occurrence.id) FILTER (
                             WHERE occurrence.captured_at>=:window_start
                               AND occurrence.captured_at<=:window_end
                               AND occurrence.created_at<=:snapshot_boundary
                           )::int AS u_occurrence_count
                    FROM platform.collection_task task
                    JOIN platform.collection_run run ON run.id=task.run_id
                    LEFT JOIN platform.answer_source_occurrence occurrence
                      ON occurrence.tenant_id=task.tenant_id
                     AND occurrence.project_id=run.project_id
                     AND occurrence.run_id=task.run_id
                     AND occurrence.answer_task_id=task.id
                    WHERE task.tenant_id=:tenant_id
                      AND run.project_id=:project_id
                      AND task.run_id=ANY(:run_ids)
                    GROUP BY task.id,task.pub_id,task.run_id,run.pub_id,task.state,
                             task.quality_state,task.answer_text,task.created_at
                    ORDER BY run.pub_id,task.created_at,task.pub_id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "run_ids": run_ids,
                    "window_start": body.window_start,
                    "window_end": body.window_end,
                    "snapshot_boundary": body.source_snapshot_boundary,
                },
            )
            .mappings()
            .all()
        )
        queries_by_run: Counter[uuid.UUID] = Counter(row["run_id"] for row in query_rows)
        succeeded_by_run: Counter[uuid.UUID] = Counter(
            row["run_id"] for row in query_rows if str(row["state"]) == "done"
        )
        failed_by_run: Counter[uuid.UUID] = Counter(
            row["run_id"] for row in query_rows if str(row["state"]) == "failed"
        )
        for run in run_rows:
            run_id = run["id"]
            if (
                queries_by_run[run_id] != int(run["total_tasks"])
                or succeeded_by_run[run_id] != int(run["completed_tasks"])
                or failed_by_run[run_id] != int(run["failed_tasks"])
            ):
                raise Conflict("service2_run_task_outcomes_incomplete")
        if any(str(row["state"]) not in {"done", "failed"} for row in query_rows):
            raise Conflict("service2_run_task_outcomes_incomplete")
        if any(str(row["state"]) == "done" and not row["answer_present"] for row in query_rows):
            raise Conflict("service2_successful_query_answer_missing")
        counts = (
            session.execute(
                text(
                    """
                    SELECT count(*)::int AS expected,
                           count(DISTINCT occurrence.source_url_id)::int AS distinct_urls
                    FROM platform.answer_source_occurrence occurrence
                    JOIN platform.collection_task task
                      ON task.id=occurrence.answer_task_id AND task.state='done'
                    WHERE occurrence.tenant_id=:tenant_id
                      AND occurrence.project_id=:project_id
                      AND occurrence.run_id=ANY(:run_ids)
                      AND occurrence.captured_at>=:window_start
                      AND occurrence.captured_at<=:window_end
                      AND occurrence.created_at<=:snapshot_boundary
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "run_ids": run_ids,
                    "window_start": body.window_start,
                    "window_end": body.window_end,
                    "snapshot_boundary": body.source_snapshot_boundary,
                },
            )
            .mappings()
            .one()
        )
        expected = int(counts["expected"])
        distinct_urls = int(counts["distinct_urls"])
        if expected != sum(
            int(row["u_occurrence_count"]) for row in query_rows if str(row["state"]) == "done"
        ):
            raise Conflict("service2_query_occurrence_count_mismatch")
        batch_id = uuid.uuid4()
        batch_pub_id = new_pub_id("s2b")
        session.execute(
            text(
                """
                INSERT INTO platform.service2_corpus_batch
                  (id,pub_id,tenant_id,project_id,service_entitlement_id,
                   service_entitlement_pub_id,service_entitlement_revision,scope_revision,
                   scope_selector,scope_selector_hash,window_start,window_end,
                   source_snapshot_boundary,corpus_policy_version,judgment_policy_version,
                   schema_version,expected_occurrence_count,distinct_url_count,
                   materialized_item_count,status,version,created_by_pub_id,created_at,updated_at)
                VALUES
                  (:id,:pub_id,:tenant_id,:project_id,:entitlement_id,:entitlement_pub_id,
                   :entitlement_revision,1,CAST(:scope_selector AS jsonb),:scope_hash,
                   :window_start,:window_end,:snapshot_boundary,:corpus_policy,
                   :judgment_policy,:schema_version,:expected,:distinct_urls,0,'draft',1,
                   :actor,now(),now())
                """
            ),
            {
                "id": batch_id,
                "pub_id": batch_pub_id,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "entitlement_id": entitlement["id"],
                "entitlement_pub_id": entitlement["pub_id"],
                "entitlement_revision": entitlement_revision,
                "scope_selector": _canonical_json(scope_selector),
                "scope_hash": scope_hash,
                "window_start": body.window_start,
                "window_end": body.window_end,
                "snapshot_boundary": body.source_snapshot_boundary,
                "corpus_policy": body.corpus_policy_version,
                "judgment_policy": body.judgment_policy_version,
                "schema_version": FACT_SCHEMA_VERSION,
                "expected": expected,
                "distinct_urls": distinct_urls,
                "actor": actor_pub_id,
            },
        )
        session.execute(
            text(
                """
                INSERT INTO platform.service2_corpus_batch_run
                  (id,pub_id,tenant_id,project_id,batch_id,run_id,run_pub_id,ordinal,created_at)
                VALUES
                  (:id,:pub_id,:tenant_id,:project_id,:batch_id,:run_id,:run_pub_id,:ordinal,now())
                """
            ),
            [
                {
                    "id": _stable_uuid(f"{batch_pub_id}|run|{row['pub_id']}"),
                    "pub_id": _stable_pub_id("s2r", f"{batch_pub_id}|{row['pub_id']}"),
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "batch_id": batch_id,
                    "run_id": row["id"],
                    "run_pub_id": row["pub_id"],
                    "ordinal": ordinal,
                }
                for ordinal, row in enumerate(run_rows, 1)
            ],
        )
        if query_rows:
            session.execute(
                text(
                    """
                    INSERT INTO platform.service2_corpus_batch_query
                      (id,pub_id,tenant_id,project_id,batch_id,run_id,run_pub_id,
                       answer_task_id,answer_task_pub_id,ordinal,task_state,outcome,
                       failure_code,answer_present,u_occurrence_count,created_at)
                    VALUES
                      (:id,:pub_id,:tenant_id,:project_id,:batch_id,:run_id,:run_pub_id,
                       :answer_task_id,:answer_task_pub_id,:ordinal,:task_state,:outcome,
                       :failure_code,:answer_present,:u_occurrence_count,now())
                    """
                ),
                [
                    {
                        "id": _stable_uuid(f"{batch_pub_id}|query|{row['pub_id']}"),
                        "pub_id": _stable_pub_id("s2q", f"{batch_pub_id}|{row['pub_id']}"),
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                        "batch_id": batch_id,
                        "run_id": row["run_id"],
                        "run_pub_id": row["run_pub_id"],
                        "answer_task_id": row["id"],
                        "answer_task_pub_id": row["pub_id"],
                        "ordinal": ordinal,
                        "task_state": row["state"],
                        "outcome": "succeeded" if row["state"] == "done" else "failed",
                        "failure_code": (
                            None
                            if row["state"] == "done"
                            else str(row["quality_state"] or "query_failed")[:120]
                        ),
                        "answer_present": bool(row["answer_present"]),
                        "u_occurrence_count": int(row["u_occurrence_count"]),
                    }
                    for ordinal, row in enumerate(query_rows, 1)
                ],
            )
        result = session.execute(
            text(
                """
                SELECT occurrence.id AS occurrence_id,occurrence.pub_id AS occurrence_pub_id,
                       occurrence.run_id,run.pub_id AS run_pub_id,
                       occurrence.answer_task_id,task.pub_id AS answer_task_pub_id,
                       occurrence.source_url_id,url.pub_id AS source_url_pub_id,
                       occurrence.raw_url,url.canonical_url,site.host AS site_host,
                       occurrence.occurrence_ordinal,occurrence.u_rank,occurrence.captured_at,
                       occurrence.query_text,occurrence.u_state,task.matrix_json,
                       COALESCE(task.collection_surface,run.collection_surface)
                         AS collection_surface,
                       snapshot.id AS snapshot_id,snapshot.pub_id AS snapshot_pub_id,
                       snapshot.source_document_id,document.pub_id AS source_document_pub_id,
                       COALESCE(snapshot.fetch_attempt_id,attempt.id) AS fetch_attempt_id,
                       COALESCE(snapshot_attempt.pub_id,attempt.pub_id) AS fetch_attempt_pub_id,
                       snapshot.snapshot_state,snapshot.body_object_key,snapshot.text_sha256,
                       COALESCE(snapshot_attempt.state,attempt.state) AS attempt_state
                FROM platform.answer_source_occurrence occurrence
                JOIN platform.collection_run run ON run.id=occurrence.run_id
                JOIN platform.collection_task task ON task.id=occurrence.answer_task_id
                JOIN platform.source_url url ON url.id=occurrence.source_url_id
                JOIN platform.source_site site ON site.id=url.site_id
                LEFT JOIN LATERAL (
                  SELECT candidate.* FROM platform.source_page_snapshot candidate
                  WHERE candidate.tenant_id=occurrence.tenant_id
                    AND candidate.project_id=occurrence.project_id
                    AND candidate.source_url_id=occurrence.source_url_id
                    AND candidate.captured_at<=:snapshot_boundary
                  ORDER BY candidate.captured_at DESC,candidate.pub_id DESC LIMIT 1
                ) snapshot ON TRUE
                LEFT JOIN platform.source_document document
                  ON document.id=snapshot.source_document_id
                LEFT JOIN platform.source_fetch_attempt snapshot_attempt
                  ON snapshot_attempt.id=snapshot.fetch_attempt_id
                LEFT JOIN LATERAL (
                  SELECT candidate.* FROM platform.source_fetch_attempt candidate
                  WHERE candidate.tenant_id=occurrence.tenant_id
                    AND candidate.project_id=occurrence.project_id
                    AND candidate.source_url_id=occurrence.source_url_id
                    AND candidate.started_at<=:snapshot_boundary
                  ORDER BY candidate.started_at DESC,candidate.pub_id DESC LIMIT 1
                ) attempt ON TRUE
                WHERE occurrence.tenant_id=:tenant_id
                  AND occurrence.project_id=:project_id
                  AND occurrence.run_id=ANY(:run_ids)
                  AND task.state='done'
                  AND occurrence.captured_at>=:window_start
                  AND occurrence.captured_at<=:window_end
                  AND occurrence.created_at<=:snapshot_boundary
                ORDER BY occurrence.captured_at,occurrence.pub_id
                """
            ),
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "run_ids": run_ids,
                "window_start": body.window_start,
                "window_end": body.window_end,
                "snapshot_boundary": body.source_snapshot_boundary,
            },
        )
        insert_sql = text(
            """
            INSERT INTO platform.service2_corpus_item
              (id,pub_id,tenant_id,project_id,batch_id,occurrence_id,occurrence_pub_id,
               run_id,run_pub_id,answer_task_id,answer_task_pub_id,source_url_id,
               source_url_pub_id,snapshot_id,snapshot_pub_id,source_document_id,
               source_document_pub_id,fetch_attempt_id,fetch_attempt_pub_id,raw_url,
               canonical_url,site_host,occurrence_ordinal,u_rank,captured_at,platform,model,
               region,collection_surface,question,retrieval_query,u_state,fetch_state,
               processing_state,entity_state,judgment_state,review_state,entered_judgment,
               finding_count,retry_count,failure_code,failure_detail,manual_evidence_state,
               version,created_at,updated_at)
            VALUES
              (:id,:pub_id,:tenant_id,:project_id,:batch_id,:occurrence_id,:occurrence_pub_id,
               :run_id,:run_pub_id,:answer_task_id,:answer_task_pub_id,:source_url_id,
               :source_url_pub_id,:snapshot_id,:snapshot_pub_id,:source_document_id,
               :source_document_pub_id,:fetch_attempt_id,:fetch_attempt_pub_id,:raw_url,
               :canonical_url,:site_host,:occurrence_ordinal,:u_rank,:captured_at,:platform,:model,
               :region,:collection_surface,:question,:retrieval_query,:u_state,:fetch_state,
               :processing_state,:entity_state,:judgment_state,'unreviewed',false,0,0,
               :failure_code,NULL,:manual_evidence_state,1,now(),now())
            """
        )
        materialized = 0
        for partition in result.mappings().partitions(500):
            parameters: list[dict[str, Any]] = []
            for row in partition:
                matrix = _safe_matrix(row["matrix_json"])
                fetch_state, processing_state, entity_state, judgment_state = _fetch_projection(row)
                failure_code = None
                if processing_state in {"blocked", "gone", "failed", "unobservable"}:
                    failure_code = f"source_{processing_state}"
                parameters.append(
                    {
                        "id": _stable_uuid(f"{batch_pub_id}|item|{row['occurrence_pub_id']}"),
                        "pub_id": _stable_pub_id(
                            "s2i", f"{batch_pub_id}|{row['occurrence_pub_id']}"
                        ),
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                        "batch_id": batch_id,
                        **{
                            key: row[key]
                            for key in (
                                "occurrence_id",
                                "occurrence_pub_id",
                                "run_id",
                                "run_pub_id",
                                "answer_task_id",
                                "answer_task_pub_id",
                                "source_url_id",
                                "source_url_pub_id",
                                "snapshot_id",
                                "snapshot_pub_id",
                                "source_document_id",
                                "source_document_pub_id",
                                "fetch_attempt_id",
                                "fetch_attempt_pub_id",
                                "raw_url",
                                "canonical_url",
                                "site_host",
                                "occurrence_ordinal",
                                "u_rank",
                                "captured_at",
                                "collection_surface",
                            )
                        },
                        "platform": str(matrix.get("adapter") or "unknown"),
                        "model": str(matrix.get("model") or "unknown"),
                        "region": str(matrix.get("region") or "unknown"),
                        "question": str(matrix.get("query") or ""),
                        "retrieval_query": row["query_text"],
                        "u_state": row["u_state"],
                        "fetch_state": fetch_state,
                        "processing_state": processing_state,
                        "entity_state": entity_state,
                        "judgment_state": judgment_state,
                        "failure_code": failure_code,
                        "manual_evidence_state": (
                            "pending"
                            if processing_state == "manual_evidence_required"
                            else "not_required"
                        ),
                    }
                )
            if parameters:
                session.execute(insert_sql, parameters)
                materialized += len(parameters)
        if materialized != expected:
            raise Conflict("all_u_materialization_count_mismatch")
        session.execute(
            text(
                """
                UPDATE platform.service2_corpus_batch
                SET materialized_item_count=:materialized,updated_at=now()
                WHERE id=:batch_id
                """
            ),
            {"materialized": materialized, "batch_id": batch_id},
        )
        self._append_event(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            batch_id=batch_id,
            batch_pub_id=batch_pub_id,
            event_type="created",
            actor_pub_id=actor_pub_id,
            idempotency_hash=idem,
            payload={
                "scope_selector_hash": scope_hash,
                "expected_occurrences": expected,
                "selected_queries": len(query_rows),
                "successful_queries": sum(1 for row in query_rows if str(row["state"]) == "done"),
                "failed_queries": sum(1 for row in query_rows if str(row["state"]) == "failed"),
                "analysis_model": body.analysis_model,
            },
        )
        return MaterializedBatch(batch_pub_id, False)

    def _append_event(
        self,
        session: Session,
        *,
        tenant_id: uuid.UUID,
        project_id: uuid.UUID,
        batch_id: uuid.UUID,
        batch_pub_id: str,
        event_type: str,
        actor_pub_id: str,
        idempotency_hash: str,
        payload: dict[str, object],
    ) -> None:
        session.execute(
            text(
                """
                INSERT INTO platform.service2_batch_event
                  (id,pub_id,tenant_id,project_id,batch_id,event_type,actor_pub_id,
                   idempotency_key,payload,created_at)
                VALUES
                  (:id,:pub_id,:tenant_id,:project_id,:batch_id,:event_type,:actor,
                   :idem,CAST(:payload AS jsonb),now())
                """
            ),
            {
                "id": _stable_uuid(f"{batch_pub_id}|event|{idempotency_hash}"),
                "pub_id": _stable_pub_id("s2e", f"{batch_pub_id}|{idempotency_hash}"),
                "tenant_id": tenant_id,
                "project_id": project_id,
                "batch_id": batch_id,
                "event_type": event_type,
                "actor": actor_pub_id,
                "idem": idempotency_hash,
                "payload": _canonical_json(payload),
            },
        )

    def transition(
        self,
        session: Session,
        *,
        tenant_id: uuid.UUID,
        project_id: uuid.UUID,
        tenant_pub_id: str,
        project_pub_id: str,
        batch_pub_id: str,
        actor_pub_id: str,
        idempotency_key: str,
        action: str,
        task_queue: str,
        source_task_queue: str,
    ) -> tuple[str, int, bool]:
        # Keep the transport dispatcher lazy: its registry imports workflow
        # definitions, whose activities use this service during worker startup.
        from geo_platform.collection.workflow_outbox import (  # noqa: PLC0415
            enqueue_workflow_signal,
            enqueue_workflow_start,
        )

        event_type = {
            "start": "started",
            "pause": "paused",
            "resume": "resumed",
            "retry": "retry_requested",
            "cancel": "cancel_requested",
        }.get(action)
        if event_type is None:
            raise Invalid("unsupported_service2_lifecycle_action")
        idem = _idempotency_hash(tenant_pub_id, f"service2-batch-{action}", idempotency_key)
        prior = (
            session.execute(
                text(
                    """
                    SELECT event.event_type,batch.pub_id,batch.status,batch.version
                    FROM platform.service2_batch_event event
                    JOIN platform.service2_corpus_batch batch ON batch.id=event.batch_id
                    WHERE event.tenant_id=:tenant_id AND event.idempotency_key=:idem
                    """
                ),
                {"tenant_id": tenant_id, "idem": idem},
            )
            .mappings()
            .one_or_none()
        )
        if prior is not None:
            if prior["pub_id"] != batch_pub_id or prior["event_type"] != event_type:
                raise Conflict("idempotency_key_payload_conflict")
            return str(prior["status"]), int(prior["version"]), True
        batch = self.batch_row(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            batch_pub_id=batch_pub_id,
            lock=True,
        )
        current = str(batch["status"])
        if action in {"start", "resume", "retry"}:
            entitlement_active = session.execute(
                text(
                    """
                    SELECT 1 FROM platform.project_service_entitlement entitlement
                    WHERE entitlement.id=:entitlement_id
                      AND entitlement.tenant_id=:tenant_id
                      AND entitlement.project_id=:project_id
                      AND entitlement.service_code='outbound_disparagement_audit'
                      AND entitlement.state='active'
                      AND (entitlement.authorized_from IS NULL
                           OR entitlement.authorized_from<=:window_end)
                      AND (entitlement.authorized_until IS NULL
                           OR entitlement.authorized_until>=:window_start)
                    """
                ),
                {
                    "entitlement_id": batch["service_entitlement_id"],
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "window_start": batch["window_start"],
                    "window_end": batch["window_end"],
                },
            ).scalar_one_or_none()
            if entitlement_active is None:
                raise Conflict("service2_entitlement_inactive")
        allowed: dict[str, set[str]] = {
            "start": {"draft"},
            "pause": {"queued", "running"},
            "resume": {"paused"},
            "retry": {"failed", "review"},
            "cancel": {"draft", "queued", "running", "paused", "review", "failed"},
        }
        if current not in allowed[action]:
            raise Conflict(f"service2_{action}_not_allowed_from_{current}")
        workflow_id = str(
            batch["workflow_id"]
            or f"service2-corpus/{tenant_pub_id}/{batch_pub_id}/attempt/{batch['version']}"
        )
        if action in {"start", "retry"}:
            if action == "retry":
                workflow_id = (
                    f"service2-corpus/{tenant_pub_id}/{batch_pub_id}/attempt/"
                    f"{int(batch['version']) + 1}"
                )
            enqueue_workflow_start(
                session,
                tenant_pub_id=tenant_pub_id,
                workflow_type="service2_source_corpus",
                workflow_id=workflow_id,
                task_queue=task_queue,
                payload={
                    "schema_version": "service2-source-corpus-workflow-v1",
                    "tenant_pub_id": tenant_pub_id,
                    "project_pub_id": project_pub_id,
                    "batch_pub_id": batch_pub_id,
                    "source_task_queue": source_task_queue,
                    "coverage_cursor": None,
                    "processed_count": 0,
                    "history_processed": 0,
                    "fetch_completed": False,
                },
            )
            next_status = "queued"
        else:
            if not (action == "cancel" and current in {"draft", "review", "failed"}):
                enqueue_workflow_signal(
                    session,
                    tenant_pub_id=tenant_pub_id,
                    workflow_id=workflow_id,
                    signal_name=action,
                    args=[{"batch_pub_id": batch_pub_id}],
                    idempotency_key=idempotency_key,
                )
            next_status = {
                "pause": "paused",
                "resume": "running",
                "retry": "queued",
                "cancel": (
                    "cancelled" if current in {"draft", "review", "failed"} else "cancel_requested"
                ),
            }[action]
        if action == "cancel" and next_status == "cancelled":
            session.execute(
                text(
                    """
                    UPDATE platform.service2_corpus_item
                    SET processing_state='cancelled',version=version+1,updated_at=now()
                    WHERE batch_id=:batch_id
                      AND processing_state IN ('queued','fetching','retry_wait')
                    """
                ),
                {"batch_id": batch["id"]},
            )
        if action == "retry":
            session.execute(
                text(
                    """
                    UPDATE platform.service2_corpus_item
                    SET processing_state='queued',retry_count=retry_count+1,
                        failure_code=NULL,failure_detail=NULL,version=version+1,
                        updated_at=now()
                    WHERE batch_id=:batch_id
                      AND processing_state IN ('retry_wait','failed','manual_evidence_required')
                    """
                ),
                {"batch_id": batch["id"]},
            )
        self._append_event(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            batch_id=batch["id"],
            batch_pub_id=batch_pub_id,
            event_type=event_type,
            actor_pub_id=actor_pub_id,
            idempotency_hash=idem,
            payload={"from": current, "to": next_status, "workflow_id": workflow_id},
        )
        version = int(batch["version"]) + 1
        session.execute(
            text(
                """
                UPDATE platform.service2_corpus_batch
                SET status=:status,workflow_id=:workflow_id,version=:version,
                    error_code=NULL,updated_at=now()
                WHERE id=:batch_id
                """
            ),
            {
                "status": next_status,
                "workflow_id": workflow_id,
                "version": version,
                "batch_id": batch["id"],
            },
        )
        return next_status, version, False

    def batch_row(
        self,
        session: Session,
        *,
        tenant_id: uuid.UUID,
        project_id: uuid.UUID,
        batch_pub_id: str,
        lock: bool = False,
    ) -> RowData:
        suffix = " FOR UPDATE" if lock else ""
        row = (
            session.execute(
                text(
                    """
                    SELECT batch.*,project.pub_id AS project_pub_id,
                           ARRAY(
                             SELECT link.run_pub_id FROM platform.service2_corpus_batch_run link
                             WHERE link.batch_id=batch.id ORDER BY link.ordinal
                           ) AS run_pub_ids
                    FROM platform.service2_corpus_batch batch
                    JOIN platform.project project ON project.id=batch.project_id
                    WHERE batch.tenant_id=:tenant_id AND batch.project_id=:project_id
                      AND batch.pub_id=:batch_pub_id
                    """
                    + suffix
                ),
                {
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "batch_pub_id": batch_pub_id,
                },
            )
            .mappings()
            .one_or_none()
        )
        return _mapping(row, "service2_batch_not_found")

    def coverage(
        self, session: Session, *, tenant_id: uuid.UUID, batch_id: uuid.UUID
    ) -> dict[str, object]:
        batch = (
            session.execute(
                text(
                    """
                    SELECT expected_occurrence_count,materialized_item_count,distinct_url_count
                    FROM platform.service2_corpus_batch
                    WHERE tenant_id=:tenant_id AND id=:batch_id
                    """
                ),
                {"tenant_id": tenant_id, "batch_id": batch_id},
            )
            .mappings()
            .one()
        )
        item_states = (
            session.execute(
                text(
                    """
                    SELECT processing_state,fetch_state,count(*)::int AS count
                    FROM platform.service2_corpus_item
                    WHERE tenant_id=:tenant_id AND batch_id=:batch_id
                    GROUP BY processing_state,fetch_state
                    """
                ),
                {"tenant_id": tenant_id, "batch_id": batch_id},
            )
            .mappings()
            .all()
        )
        query_summary = (
            session.execute(
                text(
                    """
                    SELECT count(*)::int AS selected,
                           count(*) FILTER (WHERE outcome='succeeded')::int AS succeeded,
                           count(*) FILTER (WHERE outcome='failed')::int AS failed,
                           count(*) FILTER (
                             WHERE outcome='succeeded' AND u_occurrence_count>0
                           )::int AS succeeded_with_u,
                           count(*) FILTER (
                             WHERE outcome='succeeded' AND u_occurrence_count=0
                           )::int AS succeeded_without_u
                    FROM platform.service2_corpus_batch_query
                    WHERE tenant_id=:tenant_id AND batch_id=:batch_id
                    """
                ),
                {"tenant_id": tenant_id, "batch_id": batch_id},
            )
            .mappings()
            .one()
        )
        query_failures = session.execute(
            text(
                """
                SELECT failure_code,count(*)::int AS count
                FROM platform.service2_corpus_batch_query
                WHERE tenant_id=:tenant_id AND batch_id=:batch_id AND outcome='failed'
                GROUP BY failure_code ORDER BY failure_code
                """
            ),
            {"tenant_id": tenant_id, "batch_id": batch_id},
        ).all()
        finding = (
            session.execute(
                text(
                    """
                    SELECT count(*)::int AS findings,
                           count(*) FILTER (
                             WHERE current_review_state IN ('accepted','rejected')
                           )::int
                             AS reviewed,
                           count(*) FILTER (
                             WHERE ledger='statement' AND level<>'L0'
                               AND validation_status='exact'
                               AND visual_validation_status='verified'
                               AND current_review_state='accepted'
                               AND factcheck_claim IS NOT NULL
                               AND factcheck_verdict IS NOT NULL
                               AND (
                                 (factcheck_verdict='unverifiable'
                                  AND btrim(COALESCE(factcheck_boundary,''))<>'')
                                 OR
                                 (factcheck_verdict IN ('supported','refuted','mixed')
                                  AND EXISTS (
                                    SELECT 1
                                    FROM jsonb_array_elements(factcheck_evidence) evidence(value)
                                    WHERE COALESCE(
                                      NULLIF(btrim(evidence.value->>'evidence_pub_id'),''),
                                      NULLIF(btrim(evidence.value->>'source_pub_id'),''),
                                      NULLIF(btrim(evidence.value->>'document_pub_id'),''),
                                      NULLIF(btrim(evidence.value->>'account_pub_id'),''),
                                      NULLIF(btrim(evidence.value->>'approval_pub_id'),'')
                                    ) IS NOT NULL
                                    OR btrim(COALESCE(
                                      evidence.value->>'url',evidence.value->>'source_url',''
                                    )) ~* '^https?://[^/?#[:space:]]+'
                                  ))
                               )
                           )::int AS eligible
                    FROM platform.service2_relation_finding
                    WHERE tenant_id=:tenant_id AND batch_id=:batch_id
                    """
                ),
                {"tenant_id": tenant_id, "batch_id": batch_id},
            )
            .mappings()
            .one()
        )
        entered = session.execute(
            text(
                """
                SELECT count(*) FILTER (WHERE entered_judgment)::int
                FROM platform.service2_corpus_item
                WHERE tenant_id=:tenant_id AND batch_id=:batch_id
                """
            ),
            {"tenant_id": tenant_id, "batch_id": batch_id},
        ).scalar_one()
        processing: Counter[str] = Counter()
        fetch: Counter[str] = Counter()
        for state in item_states:
            processing[str(state["processing_state"])] += int(state["count"])
            fetch[str(state["fetch_state"])] += int(state["count"])
        expected = int(batch["expected_occurrence_count"])
        materialized = int(batch["materialized_item_count"])
        selected_queries = int(query_summary["selected"])
        successful_queries = int(query_summary["succeeded"])
        failed_queries = int(query_summary["failed"])
        return {
            "selected_queries": selected_queries,
            "successful_queries": successful_queries,
            "failed_queries": failed_queries,
            "successful_queries_with_u": int(query_summary["succeeded_with_u"]),
            "successful_queries_without_u": int(query_summary["succeeded_without_u"]),
            "query_failure_codes": {
                str(code): int(count) for code, count in query_failures if code
            },
            "query_outcomes_complete": (selected_queries == successful_queries + failed_queries),
            "query_coverage_complete": failed_queries == 0,
            "expected_occurrences": expected,
            "materialized_items": materialized,
            "distinct_urls": int(batch["distinct_url_count"]),
            "processing_states": dict(sorted(processing.items())),
            "fetch_states": dict(sorted(fetch.items())),
            "entered_judgment": int(entered),
            "findings": int(finding["findings"]),
            "reviewed_findings": int(finding["reviewed"]),
            "eligible_cases": int(finding["eligible"]),
            "coverage_complete": materialized == expected,
        }

    def batch_view(self, session: Session, row: RowData) -> dict[str, object]:
        selector = row["scope_selector"] if isinstance(row["scope_selector"], dict) else {}
        return {
            "batch_pub_id": str(row["pub_id"]),
            "project_pub_id": str(row["project_pub_id"]),
            "service_entitlement_pub_id": str(row["service_entitlement_pub_id"]),
            "service_entitlement_revision": str(row["service_entitlement_revision"]),
            "run_pub_ids": list(row["run_pub_ids"] or []),
            "analysis_model": str(selector.get("analysis_model") or "unknown"),
            "window_start": row["window_start"],
            "window_end": row["window_end"],
            "source_snapshot_boundary": row["source_snapshot_boundary"],
            "corpus_policy_version": str(row["corpus_policy_version"]),
            "judgment_policy_version": str(row["judgment_policy_version"]),
            "status": str(row["status"]),
            "version": int(row["version"]),
            "workflow_id": row["workflow_id"],
            "frozen_at": row["frozen_at"],
            "manifest_hash": row["manifest_hash"],
            "error_code": row["error_code"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "coverage": self.coverage(session, tenant_id=row["tenant_id"], batch_id=row["id"]),
        }

    def record_analysis_attempt(
        self,
        session: Session,
        *,
        item: RowData,
        snapshot_id: uuid.UUID | None,
        input_hash: str,
        method: str,
        model: str,
        prompt_version: str,
        policy_version: str,
        result_state: str,
        failure_codes: Iterable[str],
    ) -> None:
        key = "|".join(
            (
                str(item["pub_id"]),
                str(snapshot_id or "none"),
                policy_version,
                model,
                input_hash,
            )
        )
        session.execute(
            text(
                """
                INSERT INTO platform.service2_analysis_attempt
                  (id,pub_id,tenant_id,project_id,batch_id,corpus_item_id,snapshot_id,
                   input_hash,method,model,prompt_version,policy_version,result_state,
                   failure_codes,created_at)
                VALUES
                  (:id,:pub_id,:tenant_id,:project_id,:batch_id,:item_id,:snapshot_id,
                   :input_hash,:method,:model,:prompt_version,:policy_version,:result_state,
                   CAST(:failures AS jsonb),now())
                ON CONFLICT (corpus_item_id,snapshot_id,policy_version,model,input_hash)
                DO NOTHING
                """
            ),
            {
                "id": _stable_uuid(f"analysis|{key}"),
                "pub_id": _stable_pub_id("s2a", key),
                "tenant_id": item["tenant_id"],
                "project_id": item["project_id"],
                "batch_id": item["batch_id"],
                "item_id": item["id"],
                "snapshot_id": snapshot_id,
                "input_hash": input_hash,
                "method": method,
                "model": model,
                "prompt_version": prompt_version,
                "policy_version": policy_version,
                "result_state": result_state,
                "failures": _canonical_json(list(dict.fromkeys(failure_codes))),
            },
        )

    def create_finding(
        self,
        session: Session,
        *,
        tenant_id: uuid.UUID,
        project_id: uuid.UUID,
        batch_pub_id: str,
        body: FindingCreate,
    ) -> RowData:
        raw_item = (
            session.execute(
                text(
                    """
                    SELECT item.*,batch.status AS batch_status,
                           project.pub_id AS project_pub_id,tenant.pub_id AS tenant_pub_id
                    FROM platform.service2_corpus_item item
                    JOIN platform.service2_corpus_batch batch ON batch.id=item.batch_id
                    JOIN platform.project project ON project.id=item.project_id
                    JOIN platform.tenant tenant ON tenant.id=item.tenant_id
                    WHERE item.tenant_id=:tenant_id AND item.project_id=:project_id
                      AND batch.pub_id=:batch_pub_id
                      AND item.pub_id=:item_pub_id
                    FOR UPDATE OF item
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "batch_pub_id": batch_pub_id,
                    "item_pub_id": body.corpus_item_pub_id,
                },
            )
            .mappings()
            .one_or_none()
        )
        item = _mapping(raw_item, "service2_corpus_item_not_found")
        if item["batch_status"] not in {"queued", "running", "review"}:
            raise Conflict(f"service2_finding_not_allowed_from_{item['batch_status']}")
        snapshot = (
            session.execute(
                text(
                    """
                    SELECT snapshot.*,document.pub_id AS source_document_pub_id
                    FROM platform.source_page_snapshot snapshot
                    LEFT JOIN platform.source_document document
                      ON document.id=snapshot.source_document_id
                    WHERE snapshot.tenant_id=:tenant_id AND snapshot.project_id=:project_id
                      AND snapshot.id=:bound_snapshot_id AND snapshot.pub_id=:snapshot_pub_id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "bound_snapshot_id": item["snapshot_id"],
                    "snapshot_pub_id": body.snapshot_pub_id,
                },
            )
            .mappings()
            .one_or_none()
        )
        if snapshot is None:
            raise Invalid("finding_snapshot_not_bound_to_corpus_item")
        input_hash = _hash_json(body.model_dump(mode="json"))
        source_text: str
        try:
            if self.store is None or not snapshot["body_object_key"] or not snapshot["body_sha256"]:
                raise ValueError("snapshot_body_unavailable")
            payload = self.store.get_verified(
                str(snapshot["body_object_key"]), str(snapshot["body_sha256"])
            )
            source_text = payload.decode("utf-8")
        except Exception as exc:
            self.record_analysis_attempt(
                session,
                item=item,
                snapshot_id=snapshot["id"],
                input_hash=input_hash,
                method=body.method,
                model=body.model,
                prompt_version=body.prompt_version,
                policy_version=body.policy_version,
                result_state="evidence_invalid",
                failure_codes=("snapshot_body_integrity_or_encoding_failure",),
            )
            raise EvidenceInvalid("snapshot_body_integrity_or_encoding_failure") from exc
        flags = OrthogonalFlags(**body.flags.model_dump())
        candidate = RelationFindingCandidate(
            ledger=Ledger(body.ledger),
            level=DisparagementLevel(body.level),
            relation_direction=RelationDirection(body.relation_direction),
            textual_speaker=body.textual_speaker,
            target_entity=body.target_entity,
            beneficiary_entity=body.beneficiary_entity,
            quote=body.evidence_quote,
            quote_start=body.quote_start,
            quote_end=body.quote_end,
            context=body.context_text,
            context_start=body.context_start,
            context_end=body.context_end,
            snapshot_text_sha256=body.snapshot_text_sha256,
            is_disparagement=body.is_disparagement,
            fact_anchor_state=FactAnchorState(body.fact_anchor_state),
            flags=flags,
            comparison_dimensions=tuple(body.comparison_dimensions),
            omitted_facts=tuple(body.omitted_facts),
            publisher_party=body.publisher.party,
            publisher_confidence=AttributionConfidence(body.publisher.confidence),
            publisher_evidence=tuple(body.publisher.evidence),
            commissioner_party=body.commissioner.party,
            commissioner_confidence=AttributionConfidence(body.commissioner.confidence),
            commissioner_evidence=tuple(body.commissioner.evidence),
        )
        failures = validate_relation_finding(
            candidate,
            source_text=source_text,
            snapshot_text_sha256=str(snapshot["text_sha256"] or ""),
        )
        if failures:
            self.record_analysis_attempt(
                session,
                item=item,
                snapshot_id=snapshot["id"],
                input_hash=input_hash,
                method=body.method,
                model=body.model,
                prompt_version=body.prompt_version,
                policy_version=body.policy_version,
                result_state="evidence_invalid",
                failure_codes=failures,
            )
            raise EvidenceInvalid(f"finding_validation_failed:{','.join(failures)}")
        visual_status = VisualValidationStatus.UNAVAILABLE
        visual_anchor: dict[str, object] = {}
        if body.visual_anchor_pub_id:
            anchor = (
                session.execute(
                    text(
                        """
                        SELECT anchor.pub_id AS anchor_pub_id,asset.pub_id AS evidence_pub_id,
                               anchor.quote_hash,anchor.text_start,anchor.text_end,
                               anchor.bbox,anchor.page_number,asset.source_url,asset.mime_type,
                               asset.object_key,asset.sha256
                        FROM evidence.evidence_anchor anchor
                        JOIN evidence.evidence_asset asset ON asset.pub_id=anchor.evidence_pub_id
                        JOIN evidence.evidence_relation relation
                          ON relation.tenant_pub_id=anchor.tenant_pub_id
                         AND relation.to_pub_id=asset.pub_id
                         AND relation.from_pub_id=:source_document_pub_id
                        WHERE anchor.tenant_pub_id=:tenant_pub_id
                          AND anchor.pub_id=:anchor_pub_id
                          AND asset.project_pub_id=:project_pub_id
                          AND asset.deleted_at IS NULL
                          AND asset.byte_size BETWEEN 1 AND 52428800
                        """
                    ),
                    {
                        "source_document_pub_id": snapshot["source_document_pub_id"],
                        "tenant_pub_id": str(item["tenant_pub_id"]),
                        "anchor_pub_id": body.visual_anchor_pub_id,
                        "project_pub_id": str(item["project_pub_id"]),
                    },
                )
                .mappings()
                .one_or_none()
            )
            visual_status = VisualValidationStatus.MISMATCH
            bbox: tuple[float, float, float, float] | None = None
            if (
                anchor is not None
                and visual_anchor_matches_quote(
                    anchor_quote_hash=anchor["quote_hash"],
                    anchor_text_start=anchor["text_start"],
                    anchor_text_end=anchor["text_end"],
                    quote_hash=candidate.quote_sha256,
                    quote_start=candidate.quote_start,
                    quote_end=candidate.quote_end,
                )
                and str(anchor["source_url"] or "")
                in {
                    str(item["raw_url"]),
                    str(item["canonical_url"]),
                    str(snapshot["final_url"] or ""),
                }
                and str(anchor["mime_type"]).startswith("image/")
                and self.store is not None
                and anchor["object_key"]
                and anchor["sha256"]
            ):
                try:
                    image_payload = self.store.get_verified(
                        str(anchor["object_key"]), str(anchor["sha256"])
                    )
                    with Image.open(BytesIO(image_payload)) as image:
                        image_width, image_height = image.size
                        image.verify()
                    bbox = validated_visual_bbox(
                        anchor["bbox"],
                        image_width=image_width,
                        image_height=image_height,
                    )
                except Exception:
                    bbox = None
            if anchor is not None and bbox is not None:
                visual_status = VisualValidationStatus.VERIFIED
                visual_anchor = {
                    "anchor_pub_id": str(anchor["anchor_pub_id"]),
                    "evidence_pub_id": str(anchor["evidence_pub_id"]),
                    "text_start": anchor["text_start"],
                    "text_end": anchor["text_end"],
                    "page_number": anchor["page_number"],
                    "bbox": {
                        "x": bbox[0],
                        "y": bbox[1],
                        "width": bbox[2],
                        "height": bbox[3],
                    },
                }
        relation_payload = {
            "ledger": candidate.ledger,
            "level": candidate.level,
            "direction": candidate.relation_direction,
            "speaker": candidate.textual_speaker,
            "target": candidate.target_entity,
            "beneficiary": candidate.beneficiary_entity,
            "quote_hash": candidate.quote_sha256,
            "quote_start": candidate.quote_start,
            "quote_end": candidate.quote_end,
            "context_start": candidate.context_start,
            "context_end": candidate.context_end,
            "flags": body.flags.model_dump(mode="json"),
        }
        relation_hash = _relation_version_hash(
            relation=relation_payload,
            candidate_input_hash=input_hash,
            visual_status=visual_status,
            visual_anchor=visual_anchor,
        )
        key = "|".join(
            (
                str(item["pub_id"]),
                str(snapshot["pub_id"]),
                body.policy_version,
                body.model,
                relation_hash,
            )
        )
        finding_pub_id = _stable_pub_id("s2f", key)
        validation_status = (
            ValidationStatus.EXPERIMENTAL
            if body.method == "dictionary_experimental"
            else ValidationStatus.EXACT
        )
        self.record_analysis_attempt(
            session,
            item=item,
            snapshot_id=snapshot["id"],
            input_hash=input_hash,
            method=body.method,
            model=body.model,
            prompt_version=body.prompt_version,
            policy_version=body.policy_version,
            result_state="accepted",
            failure_codes=(),
        )
        insert_result = session.execute(
            text(
                """
                INSERT INTO platform.service2_relation_finding
                  (id,pub_id,tenant_id,project_id,batch_id,corpus_item_id,snapshot_id,
                   relation_hash,ledger,level,relation_direction,textual_speaker,target_entity,
                   beneficiary_entity,is_disparagement,fact_anchor_state,evidence_quote,
                   evidence_quote_hash,quote_start,quote_end,context_text,context_start,
                   context_end,snapshot_text_sha256,visual_anchor,visual_validation_status,
                   comparison_present,peer_elevated,scope_narrowed,industry_wide,
                   direct_target_negative,secondary_position,comparison_manipulated,
                   key_fact_omitted,comparison_dimensions,omitted_facts,method,model,
                   prompt_version,policy_version,confidence,validation_status,
                   validation_failures,publisher_party,publisher_confidence,publisher_evidence,
                   commissioner_party,commissioner_confidence,commissioner_evidence,
                   factcheck_claim,factcheck_verdict,factcheck_evidence,factcheck_boundary,
                   current_review_state,version,created_at,updated_at)
                VALUES
                  (:id,:pub_id,:tenant_id,:project_id,:batch_id,:item_id,:snapshot_id,
                   :relation_hash,:ledger,:level,:direction,:speaker,:target,:beneficiary,
                   :is_disparagement,:fact_anchor_state,:quote,:quote_hash,:quote_start,
                   :quote_end,:context,:context_start,:context_end,:snapshot_hash,
                   CAST(:visual_anchor AS jsonb),:visual_status,:comparison_present,
                   :peer_elevated,:scope_narrowed,:industry_wide,:direct_target_negative,
                   :secondary_position,:comparison_manipulated,:key_fact_omitted,
                   CAST(:comparison_dimensions AS jsonb),CAST(:omitted_facts AS jsonb),
                   :method,:model,:prompt_version,:policy_version,:confidence,
                   :validation_status,'[]'::jsonb,:publisher_party,:publisher_confidence,
                   CAST(:publisher_evidence AS jsonb),:commissioner_party,
                   :commissioner_confidence,CAST(:commissioner_evidence AS jsonb),
                   :factcheck_claim,:factcheck_verdict,CAST(:factcheck_evidence AS jsonb),
                   :factcheck_boundary,'unreviewed',1,now(),now())
                ON CONFLICT (corpus_item_id,snapshot_id,policy_version,model,relation_hash)
                DO NOTHING
                RETURNING id
                """
            ),
            {
                "id": _stable_uuid(f"finding|{key}"),
                "pub_id": finding_pub_id,
                "tenant_id": item["tenant_id"],
                "project_id": item["project_id"],
                "batch_id": item["batch_id"],
                "item_id": item["id"],
                "snapshot_id": snapshot["id"],
                "relation_hash": relation_hash,
                "ledger": candidate.ledger,
                "level": candidate.level,
                "direction": candidate.relation_direction,
                "speaker": candidate.textual_speaker,
                "target": candidate.target_entity,
                "beneficiary": candidate.beneficiary_entity,
                "is_disparagement": candidate.is_disparagement,
                "fact_anchor_state": candidate.fact_anchor_state,
                "quote": candidate.quote,
                "quote_hash": candidate.quote_sha256,
                "quote_start": candidate.quote_start,
                "quote_end": candidate.quote_end,
                "context": candidate.context,
                "context_start": candidate.context_start,
                "context_end": candidate.context_end,
                "snapshot_hash": candidate.snapshot_text_sha256,
                "visual_anchor": _canonical_json(visual_anchor),
                "visual_status": visual_status,
                **body.flags.model_dump(),
                "comparison_dimensions": _canonical_json(body.comparison_dimensions),
                "omitted_facts": _canonical_json(body.omitted_facts),
                "method": body.method,
                "model": body.model,
                "prompt_version": body.prompt_version,
                "policy_version": body.policy_version,
                "confidence": body.confidence,
                "validation_status": validation_status,
                "publisher_party": body.publisher.party,
                "publisher_confidence": body.publisher.confidence,
                "publisher_evidence": _canonical_json(body.publisher.evidence),
                "commissioner_party": body.commissioner.party,
                "commissioner_confidence": body.commissioner.confidence,
                "commissioner_evidence": _canonical_json(body.commissioner.evidence),
                "factcheck_claim": body.factcheck_claim,
                "factcheck_verdict": body.factcheck_verdict,
                "factcheck_evidence": _canonical_json(body.factcheck_evidence),
                "factcheck_boundary": body.factcheck_boundary,
            },
        )
        if insert_result.scalar_one_or_none() is not None:
            session.execute(
                text(
                    """
                    UPDATE platform.service2_corpus_item item
                    SET finding_count=(
                          SELECT count(*) FROM platform.service2_relation_finding finding
                          WHERE finding.corpus_item_id=item.id
                        ),
                        entered_judgment=true,entity_state='validated',judgment_state='completed',
                        processing_state='processed',version=version+1,updated_at=now()
                    WHERE item.id=:item_id
                    """
                ),
                {"item_id": item["id"]},
            )
        return self.finding_row(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            finding_pub_id=finding_pub_id,
        )

    def finding_row(
        self,
        session: Session,
        *,
        tenant_id: uuid.UUID,
        project_id: uuid.UUID,
        finding_pub_id: str,
        lock: bool = False,
    ) -> RowData:
        suffix = " FOR UPDATE OF finding" if lock else ""
        row = (
            session.execute(
                text(
                    """
                    SELECT finding.*,batch.pub_id AS batch_pub_id,item.pub_id AS item_pub_id,
                           item.occurrence_pub_id,item.canonical_url,
                           snapshot.pub_id AS snapshot_pub_id
                    FROM platform.service2_relation_finding finding
                    JOIN platform.service2_corpus_batch batch ON batch.id=finding.batch_id
                    JOIN platform.service2_corpus_item item ON item.id=finding.corpus_item_id
                    JOIN platform.source_page_snapshot snapshot ON snapshot.id=finding.snapshot_id
                    WHERE finding.tenant_id=:tenant_id AND finding.project_id=:project_id
                      AND finding.pub_id=:finding_pub_id
                    """
                    + suffix
                ),
                {
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "finding_pub_id": finding_pub_id,
                },
            )
            .mappings()
            .one_or_none()
        )
        return _mapping(row, "service2_finding_not_found")

    @staticmethod
    def finding_view(row: RowData) -> dict[str, object]:
        raw_anchor = row["visual_anchor"]
        anchor: dict[str, Any] = raw_anchor if isinstance(raw_anchor, dict) else {}
        raw_bbox = anchor.get("bbox")
        bbox: dict[str, Any] = raw_bbox if isinstance(raw_bbox, dict) else {}
        visual_bbox = (
            tuple(float(bbox[name]) for name in ("x", "y", "width", "height"))
            if all(
                isinstance(bbox.get(name), int | float) for name in ("x", "y", "width", "height")
            )
            else None
        )
        return {
            "finding_pub_id": str(row["pub_id"]),
            "batch_pub_id": str(row["batch_pub_id"]),
            "corpus_item_pub_id": str(row["item_pub_id"]),
            "occurrence_pub_id": str(row["occurrence_pub_id"]),
            "snapshot_pub_id": str(row["snapshot_pub_id"]),
            "canonical_url": str(row["canonical_url"]),
            "ledger": str(row["ledger"]),
            "level": str(row["level"]),
            "relation_direction": str(row["relation_direction"]),
            "textual_speaker": str(row["textual_speaker"]),
            "target_entity": str(row["target_entity"]),
            "beneficiary_entity": row["beneficiary_entity"],
            "is_disparagement": bool(row["is_disparagement"]),
            "fact_anchor_state": str(row["fact_anchor_state"]),
            "evidence_quote": str(row["evidence_quote"]),
            "quote_start": int(row["quote_start"]),
            "quote_end": int(row["quote_end"]),
            "context_text": str(row["context_text"]),
            "context_start": int(row["context_start"]),
            "context_end": int(row["context_end"]),
            "snapshot_text_sha256": str(row["snapshot_text_sha256"]),
            "visual_anchor_pub_id": anchor.get("anchor_pub_id"),
            "visual_evidence_pub_id": anchor.get("evidence_pub_id"),
            "visual_bbox": visual_bbox,
            "visual_page_number": anchor.get("page_number"),
            "visual_validation_status": str(row["visual_validation_status"]),
            "flags": {
                name: bool(row[name])
                for name in (
                    "comparison_present",
                    "peer_elevated",
                    "scope_narrowed",
                    "industry_wide",
                    "direct_target_negative",
                    "secondary_position",
                    "comparison_manipulated",
                    "key_fact_omitted",
                )
            },
            "comparison_dimensions": list(row["comparison_dimensions"] or []),
            "omitted_facts": list(row["omitted_facts"] or []),
            "method": str(row["method"]),
            "policy_version": str(row["policy_version"]),
            "confidence": float(row["confidence"]),
            "validation_status": str(row["validation_status"]),
            "validation_failures": list(row["validation_failures"] or []),
            "publisher": {
                "party": row["publisher_party"],
                "confidence": str(row["publisher_confidence"]),
                "evidence": list(row["publisher_evidence"] or []),
            },
            "commissioner": {
                "party": row["commissioner_party"],
                "confidence": str(row["commissioner_confidence"]),
                "evidence": list(row["commissioner_evidence"] or []),
            },
            "factcheck_claim": row["factcheck_claim"],
            "factcheck_verdict": row["factcheck_verdict"],
            "factcheck_evidence": list(row["factcheck_evidence"] or []),
            "factcheck_boundary": row["factcheck_boundary"],
            "current_review_state": str(row["current_review_state"]),
            "version": int(row["version"]),
            "created_at": row["created_at"],
        }

    def review_finding(
        self,
        session: Session,
        *,
        tenant_id: uuid.UUID,
        project_id: uuid.UUID,
        tenant_pub_id: str,
        finding_pub_id: str,
        expected_version: int,
        idempotency_key: str,
        reviewer_pub_id: str,
        body: FindingReviewCreate,
    ) -> tuple[RowData, bool]:
        idem = _idempotency_hash(tenant_pub_id, "service2-finding-review", idempotency_key)
        prior = (
            session.execute(
                text(
                    """
                    SELECT review.finding_id,review.decision,review.reason_code,review.rationale,
                           finding.pub_id AS finding_pub_id
                    FROM platform.service2_finding_review review
                    JOIN platform.service2_relation_finding finding ON finding.id=review.finding_id
                    WHERE review.tenant_id=:tenant_id AND review.idempotency_key=:idem
                    """
                ),
                {"tenant_id": tenant_id, "idem": idem},
            )
            .mappings()
            .one_or_none()
        )
        if prior is not None:
            if (
                prior["finding_pub_id"] != finding_pub_id
                or prior["decision"] != body.decision
                or prior["reason_code"] != body.reason_code
                or prior["rationale"] != body.rationale
            ):
                raise Conflict("idempotency_key_payload_conflict")
            return (
                self.finding_row(
                    session,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    finding_pub_id=finding_pub_id,
                ),
                True,
            )
        finding = self.finding_row(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            finding_pub_id=finding_pub_id,
            lock=True,
        )
        if int(finding["version"]) != expected_version:
            raise PreconditionFailed("finding_version_mismatch")
        next_version = expected_version + 1
        review_key = f"{finding_pub_id}|{next_version}"
        session.execute(
            text(
                """
                INSERT INTO platform.service2_finding_review
                  (id,pub_id,tenant_id,project_id,batch_id,finding_id,decision,reason_code,
                   rationale,reviewer_pub_id,idempotency_key,based_on_version,
                   resulting_version,created_at)
                VALUES
                  (:id,:pub_id,:tenant_id,:project_id,:batch_id,:finding_id,:decision,
                   :reason_code,:rationale,:reviewer,:idem,:based_on,:resulting,now())
                """
            ),
            {
                "id": _stable_uuid(f"review|{review_key}"),
                "pub_id": _stable_pub_id("s2v", review_key),
                "tenant_id": tenant_id,
                "project_id": project_id,
                "batch_id": finding["batch_id"],
                "finding_id": finding["id"],
                "decision": body.decision,
                "reason_code": body.reason_code,
                "rationale": body.rationale,
                "reviewer": reviewer_pub_id,
                "idem": idem,
                "based_on": expected_version,
                "resulting": next_version,
            },
        )
        session.execute(
            text(
                """
                UPDATE platform.service2_relation_finding
                SET current_review_state=:decision,version=:version,updated_at=now()
                WHERE id=:finding_id AND version=:expected_version
                """
            ),
            {
                "decision": body.decision,
                "version": next_version,
                "finding_id": finding["id"],
                "expected_version": expected_version,
            },
        )
        session.execute(
            text(
                """
                UPDATE platform.service2_corpus_item item
                SET review_state=CASE
                      WHEN EXISTS (
                        SELECT 1 FROM platform.service2_relation_finding finding
                        WHERE finding.corpus_item_id=item.id
                          AND finding.current_review_state IN ('unreviewed','needs_changes')
                      ) THEN 'in_review'
                      WHEN EXISTS (
                        SELECT 1 FROM platform.service2_relation_finding finding
                        WHERE finding.corpus_item_id=item.id
                          AND finding.current_review_state='rejected'
                      ) THEN 'rejected'
                      ELSE 'accepted' END,
                    version=version+1,updated_at=now()
                WHERE id=:item_id
                """
            ),
            {"item_id": finding["corpus_item_id"]},
        )
        return (
            self.finding_row(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                finding_pub_id=finding_pub_id,
            ),
            False,
        )

    def freeze(
        self,
        session: Session,
        *,
        tenant_id: uuid.UUID,
        project_id: uuid.UUID,
        tenant_pub_id: str,
        batch_pub_id: str,
        actor_pub_id: str,
        idempotency_key: str,
    ) -> tuple[RowData, bool]:
        idem = _idempotency_hash(tenant_pub_id, "service2-batch-freeze", idempotency_key)
        prior = (
            session.execute(
                text(
                    """
                    SELECT batch.pub_id AS batch_pub_id,batch.status,manifest.*
                    FROM platform.service2_batch_event event
                    JOIN platform.service2_corpus_batch batch ON batch.id=event.batch_id
                    LEFT JOIN platform.service2_fact_manifest manifest
                      ON manifest.batch_id=batch.id
                    WHERE event.tenant_id=:tenant_id AND event.idempotency_key=:idem
                      AND event.event_type='frozen'
                    ORDER BY manifest.revision DESC NULLS LAST LIMIT 1
                    """
                ),
                {"tenant_id": tenant_id, "idem": idem},
            )
            .mappings()
            .one_or_none()
        )
        if prior is not None:
            if prior["batch_pub_id"] != batch_pub_id:
                raise Conflict("idempotency_key_payload_conflict")
            if prior["status"] != "frozen" or prior["pub_id"] is None:
                raise Conflict("service2_freeze_idempotency_incomplete")
            return prior, True
        batch = self.batch_row(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            batch_pub_id=batch_pub_id,
            lock=True,
        )
        existing = (
            session.execute(
                text(
                    """
                    SELECT manifest.* FROM platform.service2_fact_manifest manifest
                    WHERE manifest.batch_id=:batch_id
                    ORDER BY revision DESC LIMIT 1
                    """
                ),
                {"batch_id": batch["id"]},
            )
            .mappings()
            .one_or_none()
        )
        if batch["status"] == "frozen":
            if existing is None:
                raise Conflict("frozen_batch_manifest_missing")
            return existing, True
        if batch["status"] != "review":
            raise Conflict(f"service2_freeze_not_allowed_from_{batch['status']}")
        if int(batch["materialized_item_count"]) != int(batch["expected_occurrence_count"]):
            raise Conflict("all_u_coverage_incomplete")
        active = session.execute(
            text(
                """
                SELECT count(*) FROM platform.service2_corpus_item
                WHERE batch_id=:batch_id
                  AND processing_state IN ('queued','fetching','retry_wait')
                """
            ),
            {"batch_id": batch["id"]},
        ).scalar_one()
        if int(active):
            raise Conflict("service2_processing_not_terminal")
        pending_reviews = session.execute(
            text(
                """
                SELECT count(*) FROM platform.service2_relation_finding
                WHERE batch_id=:batch_id
                  AND current_review_state IN ('unreviewed','needs_changes')
                """
            ),
            {"batch_id": batch["id"]},
        ).scalar_one()
        if int(pending_reviews):
            raise Conflict("service2_findings_require_review")
        coverage = self.coverage(session, tenant_id=tenant_id, batch_id=batch["id"])
        cases = (
            session.execute(
                text(
                    """
                    SELECT finding.*,item.pub_id AS item_pub_id,item.occurrence_pub_id,
                           item.answer_task_pub_id,item.source_url_pub_id,item.snapshot_pub_id,
                           item.canonical_url,item.site_host,item.captured_at
                    FROM platform.service2_relation_finding finding
                    JOIN platform.service2_corpus_item item ON item.id=finding.corpus_item_id
                    WHERE finding.batch_id=:batch_id
                      AND finding.ledger='statement' AND finding.level<>'L0'
                      AND finding.validation_status='exact'
                      AND finding.visual_validation_status='verified'
                      AND finding.current_review_state='accepted'
                      AND finding.factcheck_claim IS NOT NULL
                      AND finding.factcheck_verdict IS NOT NULL
                      AND (
                        (finding.factcheck_verdict='unverifiable'
                         AND btrim(COALESCE(finding.factcheck_boundary,''))<>'')
                        OR
                        (finding.factcheck_verdict IN ('supported','refuted','mixed')
                         AND EXISTS (
                           SELECT 1
                           FROM jsonb_array_elements(finding.factcheck_evidence) evidence(value)
                           WHERE COALESCE(
                             NULLIF(btrim(evidence.value->>'evidence_pub_id'),''),
                             NULLIF(btrim(evidence.value->>'source_pub_id'),''),
                             NULLIF(btrim(evidence.value->>'document_pub_id'),''),
                             NULLIF(btrim(evidence.value->>'account_pub_id'),''),
                             NULLIF(btrim(evidence.value->>'approval_pub_id'),'')
                           ) IS NOT NULL
                           OR btrim(COALESCE(
                             evidence.value->>'url',evidence.value->>'source_url',''
                           )) ~* '^https?://[^/?#[:space:]]+'
                         ))
                      )
                    ORDER BY finding.created_at,finding.pub_id
                    """
                ),
                {"batch_id": batch["id"]},
            )
            .mappings()
            .all()
        )
        case_facts: list[dict[str, object]] = []
        evidence_refs: set[str] = set()
        evidence_urls: set[str] = set()
        for row in cases:
            if not customer_case_eligible(
                ledger=Ledger(row["ledger"]),
                level=DisparagementLevel(row["level"]),
                validation_status=ValidationStatus(row["validation_status"]),
                visual_status=VisualValidationStatus(row["visual_validation_status"]),
                review_state=str(row["current_review_state"]),
                factcheck_verdict=(
                    str(row["factcheck_verdict"]) if row["factcheck_verdict"] else None
                ),
                factcheck_evidence=tuple(row["factcheck_evidence"] or []),
                factcheck_boundary=(
                    str(row["factcheck_boundary"]) if row["factcheck_boundary"] else None
                ),
            ):
                continue
            anchor = row["visual_anchor"] if isinstance(row["visual_anchor"], dict) else {}
            evidence_pub_id = anchor.get("evidence_pub_id")
            if isinstance(evidence_pub_id, str):
                evidence_refs.add(evidence_pub_id)
            publisher_evidence = tuple(row["publisher_evidence"] or [])
            commissioner_evidence = tuple(row["commissioner_evidence"] or [])
            publisher_confidence = AttributionConfidence(row["publisher_confidence"])
            commissioner_confidence = AttributionConfidence(row["commissioner_confidence"])
            factcheck_projection = _factcheck_manifest_projection(row)
            publisher_attribution: dict[str, object] = (
                {
                    "party": row["publisher_party"],
                    "confidence": str(publisher_confidence),
                    "evidence": _safe_evidence_projection(publisher_evidence),
                }
                if attribution_wording_allowed(publisher_confidence, publisher_evidence)
                else {"party": None, "confidence": "unknown", "evidence": []}
            )
            commissioner_attribution: dict[str, object] = (
                {
                    "party": row["commissioner_party"],
                    "confidence": str(commissioner_confidence),
                    "evidence": _safe_evidence_projection(commissioner_evidence),
                }
                if attribution_wording_allowed(commissioner_confidence, commissioner_evidence)
                else {"party": None, "confidence": "unknown", "evidence": []}
            )
            for evidence_rows in (
                factcheck_projection["factcheck_evidence"],
                publisher_attribution["evidence"],
                commissioner_attribution["evidence"],
            ):
                if not isinstance(evidence_rows, list):
                    continue
                for evidence in evidence_rows:
                    if not isinstance(evidence, dict):
                        continue
                    evidence_refs.update(
                        str(evidence[key])
                        for key in _PUBLIC_EVIDENCE_ID_KEYS
                        if isinstance(evidence.get(key), str)
                    )
                    evidence_urls.update(
                        str(evidence[key])
                        for key in ("url", "source_url")
                        if isinstance(evidence.get(key), str)
                    )
            case_facts.append(
                {
                    "finding_pub_id": str(row["pub_id"]),
                    "corpus_item_pub_id": str(row["item_pub_id"]),
                    "occurrence_pub_id": str(row["occurrence_pub_id"]),
                    "answer_pub_id": str(row["answer_task_pub_id"]),
                    "source_url_pub_id": str(row["source_url_pub_id"]),
                    "snapshot_pub_id": str(row["snapshot_pub_id"]),
                    "canonical_url": str(row["canonical_url"]),
                    "site_host": str(row["site_host"]),
                    "captured_at": row["captured_at"].isoformat(),
                    "ledger": str(row["ledger"]),
                    "level": str(row["level"]),
                    "is_disparagement": bool(row["is_disparagement"]),
                    "fact_anchor_state": str(row["fact_anchor_state"]),
                    "relation_direction": str(row["relation_direction"]),
                    "textual_speaker": str(row["textual_speaker"]),
                    "target_entity": str(row["target_entity"]),
                    "beneficiary_entity": row["beneficiary_entity"],
                    "evidence_quote": str(row["evidence_quote"]),
                    "evidence_quote_hash": str(row["evidence_quote_hash"]),
                    "quote_start": int(row["quote_start"]),
                    "quote_end": int(row["quote_end"]),
                    "context_text": str(row["context_text"]),
                    "context_start": int(row["context_start"]),
                    "context_end": int(row["context_end"]),
                    "snapshot_text_sha256": str(row["snapshot_text_sha256"]),
                    "visual_anchor_pub_id": anchor.get("anchor_pub_id"),
                    "visual_evidence_pub_id": evidence_pub_id,
                    "visual_bbox": (
                        [float(anchor["bbox"][name]) for name in ("x", "y", "width", "height")]
                        if isinstance(anchor.get("bbox"), dict)
                        and all(
                            isinstance(anchor["bbox"].get(name), int | float)
                            for name in ("x", "y", "width", "height")
                        )
                        else None
                    ),
                    "visual_page_number": anchor.get("page_number"),
                    "validation_status": str(row["validation_status"]),
                    "visual_validation_status": str(row["visual_validation_status"]),
                    "review_state": str(row["current_review_state"]),
                    **factcheck_projection,
                    "publisher_attribution": publisher_attribution,
                    "commissioner_attribution": commissioner_attribution,
                }
            )
        facts: dict[str, object] = {
            "schema_version": FACT_SCHEMA_VERSION,
            "scope": {
                "project_pub_id": str(batch["project_pub_id"]),
                "batch_pub_id": batch_pub_id,
                "run_pub_ids": list(batch["run_pub_ids"] or []),
                "window_start": batch["window_start"].isoformat(),
                "window_end": batch["window_end"].isoformat(),
                "source_snapshot_boundary": batch["source_snapshot_boundary"].isoformat(),
                "scope_selector_hash": str(batch["scope_selector_hash"]),
                "corpus_policy_version": str(batch["corpus_policy_version"]),
                "judgment_policy_version": str(batch["judgment_policy_version"]),
                "query_coverage_policy_version": str(
                    (batch["scope_selector"] or {}).get(
                        "query_coverage_policy_version", QUERY_COVERAGE_POLICY_VERSION
                    )
                ),
                "analysis_model": str(
                    (batch["scope_selector"] or {}).get("analysis_model") or "unknown"
                ),
                "service_entitlement_pub_id": str(batch["service_entitlement_pub_id"]),
                "service_entitlement_revision": str(batch["service_entitlement_revision"]),
            },
            "coverage": coverage,
            "cases": case_facts,
            "evidence_pub_ids": sorted(evidence_refs),
            "evidence_urls": sorted(evidence_urls),
            "rendering_boundary": "frozen_facts_only_no_network_or_model",
        }
        manifest_hash = _hash_json(facts)
        manifest_pub_id = _stable_pub_id("s2m", f"{batch_pub_id}|1|{manifest_hash}")
        session.execute(
            text(
                """
                INSERT INTO platform.service2_fact_manifest
                  (id,pub_id,tenant_id,project_id,batch_id,revision,schema_version,
                   manifest_hash,facts,case_count,evidence_reference_count,frozen_by_pub_id,
                   created_at)
                VALUES
                  (:id,:pub_id,:tenant_id,:project_id,:batch_id,1,:schema_version,
                   :manifest_hash,CAST(:facts AS jsonb),:case_count,:evidence_count,:actor,now())
                """
            ),
            {
                "id": _stable_uuid(f"manifest|{batch_pub_id}|1|{manifest_hash}"),
                "pub_id": manifest_pub_id,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "batch_id": batch["id"],
                "schema_version": FACT_SCHEMA_VERSION,
                "manifest_hash": manifest_hash,
                "facts": _canonical_json(facts),
                "case_count": len(case_facts),
                "evidence_count": len(evidence_refs) + len(evidence_urls),
                "actor": actor_pub_id,
            },
        )
        self._append_event(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            batch_id=batch["id"],
            batch_pub_id=batch_pub_id,
            event_type="frozen",
            actor_pub_id=actor_pub_id,
            idempotency_hash=idem,
            payload={"manifest_hash": manifest_hash, "revision": 1},
        )
        session.execute(
            text(
                """
                UPDATE platform.service2_corpus_batch
                SET status='frozen',frozen_by_pub_id=:actor,frozen_at=now(),
                    manifest_hash=:manifest_hash,version=version+1,updated_at=now()
                WHERE id=:batch_id
                """
            ),
            {
                "actor": actor_pub_id,
                "manifest_hash": manifest_hash,
                "batch_id": batch["id"],
            },
        )
        manifest = (
            session.execute(
                text("SELECT * FROM platform.service2_fact_manifest WHERE pub_id=:pub_id"),
                {"pub_id": manifest_pub_id},
            )
            .mappings()
            .one()
        )
        return manifest, False


def manifest_view(row: RowData, batch_pub_id: str) -> dict[str, object]:
    return {
        "batch_pub_id": batch_pub_id,
        "manifest_pub_id": str(row["pub_id"]),
        "revision": int(row["revision"]),
        "manifest_hash": str(row["manifest_hash"]),
        "case_count": int(row["case_count"]),
        "evidence_reference_count": int(row["evidence_reference_count"]),
        "facts": dict(row["facts"]),
        "created_at": row["created_at"],
    }


__all__ = [
    "Conflict",
    "EvidenceInvalid",
    "Invalid",
    "MaterializedBatch",
    "NotFound",
    "PreconditionFailed",
    "Service2CorpusService",
    "manifest_view",
]
