"""Bounded, idempotent activities for the Service 2 all-U corpus workflow.

This worker never invents entity relations.  It refreshes one shared page fetch
per URL, preserves every occurrence item, and sends readable pages without a
configured relation analyzer to the explicit manual-evidence queue.  Strict
findings are ingested through the same API/service evidence validator.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

import httpx
from geo_platform.config import get_settings
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.service2_corpus.analysis_models import configured_model_ids
from geo_platform.service2_corpus.service import EvidenceInvalid, Service2CorpusService
from geo_platform.tenancy.database import WorkerSessionLocal
from geo_platform.tenancy.repository import TenantRepository
from sqlalchemy import text
from temporalio import activity
from temporalio.exceptions import ApplicationError

from workflows.activities.service2_relation_analysis import (
    PROMPT_VERSION,
    RelationAnalysisError,
    RelationAnalysisSchemaError,
    RelationAnalysisUnavailable,
    Service2WebSearchAnalyzer,
    config_from_settings,
)


@dataclass(frozen=True)
class Service2BatchInput:
    tenant_pub_id: str
    project_pub_id: str
    batch_pub_id: str


@dataclass(frozen=True)
class Service2SourceFetchShard:
    run_pub_id: str
    source_url_pub_ids: list[str]


@dataclass(frozen=True)
class Service2BatchPreparation:
    run_pub_ids: list[str]
    cancelled: bool
    fetch_shards: list[Service2SourceFetchShard] = field(default_factory=list)
    paused: bool = False


@dataclass(frozen=True)
class Service2CorpusPageInput(Service2BatchInput):
    cursor: str | None = None
    page_size: int = 100


@dataclass(frozen=True)
class Service2CorpusPageResult:
    processed: int
    next_cursor: str | None
    has_more: bool
    states: dict[str, int] = field(default_factory=dict)


def _batch(session: object, data: Service2BatchInput, *, lock: bool = False):  # type: ignore[no-untyped-def]
    suffix = " FOR UPDATE OF batch" if lock else ""
    return (
        session.execute(  # type: ignore[attr-defined]
            text(
                """
                SELECT batch.id,batch.tenant_id,batch.project_id,batch.status,
                       batch.source_snapshot_boundary,batch.scope_selector,
                       batch.judgment_policy_version,project.pub_id AS project_pub_id
                FROM platform.service2_corpus_batch batch
                JOIN platform.project project ON project.id=batch.project_id
                WHERE batch.pub_id=:batch_pub_id
                """
                + suffix
            ),
            {"batch_pub_id": data.batch_pub_id},
        )
        .mappings()
        .one_or_none()
    )


@activity.defn
def prepare_service2_corpus_batch(data: Service2BatchInput) -> Service2BatchPreparation:
    with WorkerSessionLocal() as session:
        TenantRepository(session, data.tenant_pub_id)
        batch = _batch(session, data, lock=True)
        if batch is None or batch["project_pub_id"] != data.project_pub_id:
            raise ApplicationError(
                "service2 batch not found",
                type="service2_batch_not_found",
                non_retryable=True,
            )
        if batch["status"] in {"cancel_requested", "cancelled"}:
            return Service2BatchPreparation(run_pub_ids=[], cancelled=True)
        if batch["status"] == "frozen":
            raise ApplicationError(
                "service2 batch is frozen", type="service2_batch_frozen", non_retryable=True
            )
        session.execute(
            text(
                """
                UPDATE platform.service2_corpus_batch
                SET status='running',version=version+1,updated_at=now()
                WHERE id=:batch_id AND status<>'paused'
                """
            ),
            {"batch_id": batch["id"]},
        )
        run_pub_ids = list(
            session.execute(
                text(
                    """
                    SELECT run_pub_id FROM platform.service2_corpus_batch_run
                    WHERE batch_id=:batch_id ORDER BY ordinal
                    """
                ),
                {"batch_id": batch["id"]},
            ).scalars()
        )
        shard_rows = (
            session.execute(
                text(
                    """
                    SELECT DISTINCT ON (item.source_url_id)
                           item.run_pub_id,item.source_url_pub_id,link.ordinal
                    FROM platform.service2_corpus_item item
                    JOIN platform.service2_corpus_batch_run link
                      ON link.batch_id=item.batch_id AND link.run_id=item.run_id
                    WHERE item.batch_id=:batch_id
                    ORDER BY item.source_url_id,link.ordinal,item.run_pub_id,item.pub_id
                    """
                ),
                {"batch_id": batch["id"]},
            )
            .mappings()
            .all()
        )
        urls_by_run: dict[str, list[str]] = {str(run_pub_id): [] for run_pub_id in run_pub_ids}
        for row in shard_rows:
            urls_by_run[str(row["run_pub_id"])].append(str(row["source_url_pub_id"]))
        fetch_shards = [
            Service2SourceFetchShard(
                run_pub_id=run_pub_id, source_url_pub_ids=urls_by_run[run_pub_id]
            )
            for run_pub_id in run_pub_ids
            if urls_by_run[run_pub_id]
        ]
        session.commit()
        return Service2BatchPreparation(
            run_pub_ids=run_pub_ids,
            cancelled=False,
            fetch_shards=fetch_shards,
            paused=batch["status"] == "paused",
        )


@activity.defn
def refresh_service2_corpus_bindings(data: Service2BatchInput) -> int:
    """Bind the latest immutable page version after URL-level fetch fan-in."""

    with WorkerSessionLocal() as session:
        TenantRepository(session, data.tenant_pub_id)
        batch = _batch(session, data)
        if batch is None or batch["project_pub_id"] != data.project_pub_id:
            raise ApplicationError(
                "service2 batch not found",
                type="service2_batch_not_found",
                non_retryable=True,
            )
        rows = session.execute(
            text(
                """
                SELECT item.id,item.u_state,
                       snapshot.id AS snapshot_id,snapshot.pub_id AS snapshot_pub_id,
                       snapshot.source_document_id,document.pub_id AS source_document_pub_id,
                       COALESCE(snapshot.fetch_attempt_id,attempt.id) AS fetch_attempt_id,
                       COALESCE(snapshot_attempt.pub_id,attempt.pub_id) AS fetch_attempt_pub_id,
                       snapshot.snapshot_state,snapshot.body_object_key,snapshot.text_sha256,
                       COALESCE(snapshot_attempt.state,attempt.state) AS attempt_state
                FROM platform.service2_corpus_item item
                LEFT JOIN LATERAL (
                  SELECT candidate.* FROM platform.source_page_snapshot candidate
                  WHERE candidate.tenant_id=item.tenant_id
                    AND candidate.project_id=item.project_id
                    AND candidate.source_url_id=item.source_url_id
                  ORDER BY candidate.captured_at DESC,candidate.pub_id DESC LIMIT 1
                ) snapshot ON TRUE
                LEFT JOIN platform.source_document document
                  ON document.id=snapshot.source_document_id
                LEFT JOIN platform.source_fetch_attempt snapshot_attempt
                  ON snapshot_attempt.id=snapshot.fetch_attempt_id
                LEFT JOIN LATERAL (
                  SELECT candidate.* FROM platform.source_fetch_attempt candidate
                  WHERE candidate.tenant_id=item.tenant_id
                    AND candidate.project_id=item.project_id
                    AND candidate.source_url_id=item.source_url_id
                  ORDER BY candidate.started_at DESC,candidate.pub_id DESC LIMIT 1
                ) attempt ON TRUE
                WHERE item.batch_id=:batch_id
                ORDER BY item.pub_id
                """
            ),
            {"batch_id": batch["id"]},
        )
        update = text(
            """
            UPDATE platform.service2_corpus_item
            SET snapshot_id=:snapshot_id,snapshot_pub_id=:snapshot_pub_id,
                source_document_id=:source_document_id,
                source_document_pub_id=:source_document_pub_id,
                fetch_attempt_id=:fetch_attempt_id,
                fetch_attempt_pub_id=:fetch_attempt_pub_id,
                fetch_state=:fetch_state,processing_state=:processing_state,
                failure_code=:failure_code,manual_evidence_state=:manual_state,
                version=version+1,updated_at=now()
            WHERE id=:id AND processing_state NOT IN ('processed','cancelled')
            """
        )
        parameters: list[dict[str, object]] = []
        for row in rows.mappings():
            snapshot_state = str(row["snapshot_state"] or "")
            attempt_state = str(row["attempt_state"] or "")
            has_text = bool(row["snapshot_id"] and row["body_object_key"] and row["text_sha256"])
            if snapshot_state == "succeeded" and has_text:
                fetch_state, processing, failure, manual = (
                    "succeeded",
                    "queued",
                    None,
                    "not_required",
                )
            elif snapshot_state == "partial":
                fetch_state, processing, failure, manual = (
                    "partial",
                    "manual_evidence_required",
                    "source_partial",
                    "pending",
                )
            elif snapshot_state in {"blocked", "gone", "failed"}:
                fetch_state, processing, failure, manual = (
                    snapshot_state,
                    snapshot_state,
                    f"source_{snapshot_state}",
                    "pending" if snapshot_state == "blocked" else "not_required",
                )
            elif attempt_state == "retry_wait":
                fetch_state, processing, failure, manual = (
                    "retry_wait",
                    "retry_wait",
                    "source_retry_wait",
                    "not_required",
                )
            elif attempt_state in {"queued", "fetching"}:
                fetch_state = processing = attempt_state
                failure, manual = None, "not_required"
            elif str(row["u_state"]) == "unobserved":
                fetch_state, processing, failure, manual = (
                    "unobserved",
                    "unobservable",
                    "source_unobservable",
                    "not_required",
                )
            else:
                fetch_state, processing, failure, manual = (
                    "failed",
                    "manual_evidence_required",
                    "source_snapshot_missing",
                    "pending",
                )
            parameters.append(
                {
                    "id": row["id"],
                    "snapshot_id": row["snapshot_id"],
                    "snapshot_pub_id": row["snapshot_pub_id"],
                    "source_document_id": row["source_document_id"],
                    "source_document_pub_id": row["source_document_pub_id"],
                    "fetch_attempt_id": row["fetch_attempt_id"],
                    "fetch_attempt_pub_id": row["fetch_attempt_pub_id"],
                    "fetch_state": fetch_state,
                    "processing_state": processing,
                    "failure_code": failure,
                    "manual_state": manual,
                }
            )
        if parameters:
            session.execute(update, parameters)
        session.commit()
        return len(parameters)


@activity.defn
def process_service2_corpus_page(data: Service2CorpusPageInput) -> Service2CorpusPageResult:
    """Advance one bounded page without treating the page size as a corpus cap."""

    if not 1 <= data.page_size <= 100:
        raise ApplicationError(
            "invalid page size", type="service2_page_size_invalid", non_retryable=True
        )
    with WorkerSessionLocal() as session:
        TenantRepository(session, data.tenant_pub_id)
        batch = _batch(session, data)
        if batch is None or batch["project_pub_id"] != data.project_pub_id:
            raise ApplicationError(
                "service2 batch not found",
                type="service2_batch_not_found",
                non_retryable=True,
            )
        if batch["status"] in {"cancel_requested", "cancelled"}:
            return Service2CorpusPageResult(0, data.cursor, False, {"cancelled": 0})
        settings = get_settings()
        selector = batch["scope_selector"] if isinstance(batch["scope_selector"], dict) else {}
        analysis_model = str(selector.get("analysis_model") or "").strip()
        allowed_models = configured_model_ids(settings)
        model_allowed = analysis_model in allowed_models
        analyzer = Service2WebSearchAnalyzer(
            config_from_settings(settings, model=analysis_model or allowed_models[0])
        )
        object_store = ContentAddressedObjectStore(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
        )
        service = Service2CorpusService(
            store=object_store,
            allowed_analysis_models=allowed_models,
        )
        entity_row = (
            session.execute(
                text(
                    """
                    SELECT COALESCE((
                             SELECT brand.name FROM platform.brand brand
                             WHERE brand.tenant_id=:tenant_id
                               AND brand.project_id=:project_id
                             ORDER BY brand.created_at,brand.pub_id LIMIT 1
                           ),'') AS project_brand,
                           ARRAY(
                             SELECT competitor.name FROM platform.competitor competitor
                             WHERE competitor.tenant_id=:tenant_id
                               AND competitor.project_id=:project_id
                             ORDER BY competitor.created_at,competitor.pub_id
                           ) AS competitors
                    """
                ),
                {"tenant_id": batch["tenant_id"], "project_id": batch["project_id"]},
            )
            .mappings()
            .one()
        )
        project_brand = str(entity_row["project_brand"] or "")
        known_entities = tuple(
            dict.fromkeys(
                value
                for value in (project_brand, *(str(v) for v in entity_row["competitors"] or []))
                if value
            )
        )
        rows = (
            session.execute(
                text(
                    """
                    SELECT item.*,snapshot.pub_id AS bound_snapshot_pub_id,
                           snapshot.text_sha256,snapshot.body_object_key,snapshot.body_sha256
                    FROM platform.service2_corpus_item item
                    LEFT JOIN platform.source_page_snapshot snapshot ON snapshot.id=item.snapshot_id
                    WHERE item.batch_id=:batch_id
                      AND (CAST(:cursor AS text) IS NULL OR item.pub_id>:cursor)
                    ORDER BY item.pub_id LIMIT :limit
                    """
                ),
                {"batch_id": batch["id"], "cursor": data.cursor, "limit": data.page_size + 1},
            )
            .mappings()
            .all()
        )
        has_more = len(rows) > data.page_size
        visible = rows[: data.page_size]
        states: dict[str, int] = {}
        for row in visible:
            current = str(row["processing_state"])
            if current == "queued" and row["snapshot_id"] and row["text_sha256"]:
                activity.heartbeat(str(row["pub_id"]), "relation_analysis")
                base_input_hash = sha256(
                    "|".join(
                        (
                            str(row["pub_id"]),
                            str(row["text_sha256"]),
                            analysis_model or "missing",
                            PROMPT_VERSION,
                        )
                    ).encode()
                ).hexdigest()

                def fail_closed(
                    result_state: str,
                    failure_code: str,
                    *,
                    item: Mapping[Any, Any] = row,
                    attempt_input_hash: str = base_input_hash,
                ) -> None:
                    service.record_analysis_attempt(
                        session,
                        item=item,
                        snapshot_id=item["snapshot_id"],
                        input_hash=attempt_input_hash,
                        method="system" if result_state == "llm_unavailable" else "llm",
                        model=analysis_model,
                        prompt_version=PROMPT_VERSION,
                        policy_version=str(batch["judgment_policy_version"]),
                        result_state=result_state,
                        failure_codes=(failure_code,),
                    )
                    session.execute(
                        text(
                            """
                            UPDATE platform.service2_corpus_item
                            SET processing_state='manual_evidence_required',
                                entity_state=:entity_state,judgment_state=:judgment_state,
                                failure_code=:failure_code,manual_evidence_state='pending',
                                version=version+1,updated_at=now()
                            WHERE id=:item_id AND processing_state='queued'
                            """
                        ),
                        {
                            "item_id": item["id"],
                            "entity_state": (
                                "validation_failure"
                                if result_state == "schema_invalid"
                                else "error"
                            ),
                            "judgment_state": (
                                "validation_failure"
                                if result_state == "schema_invalid"
                                else "error"
                            ),
                            "failure_code": failure_code,
                        },
                    )

                if not model_allowed:
                    fail_closed("llm_unavailable", "analysis_model_not_allowed")
                    current = "manual_evidence_required"
                else:
                    try:
                        if not row["body_object_key"] or not row["body_sha256"]:
                            raise RelationAnalysisUnavailable("snapshot_body_unavailable")
                        source_text = object_store.get_verified(
                            str(row["body_object_key"]), str(row["body_sha256"])
                        ).decode("utf-8")
                        result = analyzer.analyze(
                            project_brand=project_brand,
                            known_entities=known_entities,
                            url=str(row["canonical_url"]),
                            source_text=source_text,
                            snapshot_text_sha256=str(row["text_sha256"]),
                        )
                    except RelationAnalysisUnavailable as exc:
                        fail_closed("llm_unavailable", str(exc)[:120])
                        current = "manual_evidence_required"
                    except RelationAnalysisSchemaError as exc:
                        fail_closed("schema_invalid", str(exc)[:120])
                        current = "manual_evidence_required"
                    except (
                        RelationAnalysisError,
                        UnicodeDecodeError,
                        ValueError,
                        httpx.HTTPError,
                    ) as exc:
                        fail_closed("llm_error", f"relation_analysis_{type(exc).__name__}"[:120])
                        current = "manual_evidence_required"
                    else:
                        accepted = 0
                        evidence_rejected = list(result.rejected_candidates)
                        for candidate in result.findings:
                            body = candidate.model_copy(
                                update={
                                    "corpus_item_pub_id": str(row["pub_id"]),
                                    "snapshot_pub_id": str(row["bound_snapshot_pub_id"]),
                                }
                            )
                            try:
                                service.create_finding(
                                    session,
                                    tenant_id=batch["tenant_id"],
                                    project_id=batch["project_id"],
                                    batch_pub_id=data.batch_pub_id,
                                    body=body,
                                )
                                accepted += 1
                            except EvidenceInvalid as exc:
                                evidence_rejected.append(str(exc)[:120])
                        if accepted == 0 and not result.findings and not evidence_rejected:
                            service.record_analysis_attempt(
                                session,
                                item=row,
                                snapshot_id=row["snapshot_id"],
                                input_hash=result.input_hash,
                                method="llm",
                                model=analysis_model,
                                prompt_version=PROMPT_VERSION,
                                policy_version=str(batch["judgment_policy_version"]),
                                result_state="no_entities",
                                failure_codes=(),
                            )
                            session.execute(
                                text(
                                    """
                                    UPDATE platform.service2_corpus_item
                                    SET processing_state='processed',entity_state='no_entities',
                                        judgment_state='not_applicable',review_state='not_applicable',
                                        failure_code=NULL,manual_evidence_state='not_required',
                                        version=version+1,updated_at=now()
                                    WHERE id=:item_id AND processing_state='queued'
                                    """
                                ),
                                {"item_id": row["id"]},
                            )
                            current = "processed"
                        elif accepted == 0:
                            service.record_analysis_attempt(
                                session,
                                item=row,
                                snapshot_id=row["snapshot_id"],
                                input_hash=result.input_hash,
                                method="llm",
                                model=analysis_model,
                                prompt_version=PROMPT_VERSION,
                                policy_version=str(batch["judgment_policy_version"]),
                                result_state="schema_invalid",
                                failure_codes=evidence_rejected
                                or ("relation_candidate_validation_failed",),
                            )
                            session.execute(
                                text(
                                    """
                                    UPDATE platform.service2_corpus_item
                                    SET processing_state='manual_evidence_required',
                                        entity_state='validation_failure',
                                        judgment_state='validation_failure',
                                        failure_code='relation_candidate_validation_failed',
                                        manual_evidence_state='pending',version=version+1,
                                        updated_at=now()
                                    WHERE id=:item_id AND processing_state='queued'
                                    """
                                ),
                                {"item_id": row["id"]},
                            )
                            current = "manual_evidence_required"
                        elif evidence_rejected:
                            session.execute(
                                text(
                                    """
                                    UPDATE platform.service2_corpus_item
                                    SET processing_state='partial',
                                        failure_code='some_relation_candidates_rejected',
                                        manual_evidence_state='pending',version=version+1,
                                        updated_at=now()
                                    WHERE id=:item_id
                                    """
                                ),
                                {"item_id": row["id"]},
                            )
                            current = "partial"
                        else:
                            current = "processed"
            states[current] = states.get(current, 0) + 1
            activity.heartbeat(str(row["pub_id"]))
        session.commit()
        return Service2CorpusPageResult(
            processed=len(visible),
            next_cursor=str(visible[-1]["pub_id"]) if visible else data.cursor,
            has_more=has_more,
            states=states,
        )


def uuid_from_hash(value: str) -> uuid.UUID:
    """Stable UUID with RFC-4122 variant/version bits for activity retries."""

    return uuid.uuid5(uuid.NAMESPACE_URL, f"geo-service2:{value}")


@activity.defn
def finish_service2_corpus_batch(data: Service2BatchInput) -> str:
    with WorkerSessionLocal() as session:
        TenantRepository(session, data.tenant_pub_id)
        batch = _batch(session, data, lock=True)
        if batch is None or batch["project_pub_id"] != data.project_pub_id:
            raise ApplicationError(
                "service2 batch not found",
                type="service2_batch_not_found",
                non_retryable=True,
            )
        if batch["status"] == "cancel_requested":
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
            terminal = "cancelled"
        else:
            terminal = "review"
        event_type = "cancelled" if terminal == "cancelled" else "processing_completed"
        event_key = sha256(
            f"{data.tenant_pub_id}|{data.batch_pub_id}|{event_type}".encode()
        ).hexdigest()
        session.execute(
            text(
                """
                INSERT INTO platform.service2_batch_event
                  (id,pub_id,tenant_id,project_id,batch_id,event_type,actor_pub_id,
                   idempotency_key,payload,created_at)
                VALUES
                  (:id,:pub_id,:tenant_id,:project_id,:batch_id,:event_type,'system',
                   :idem,'{}'::jsonb,now())
                ON CONFLICT (tenant_id,idempotency_key) DO NOTHING
                """
            ),
            {
                "id": uuid_from_hash(f"event|{event_key}"),
                "pub_id": f"s2e_{sha256(event_key.encode()).hexdigest()[:26]}",
                "tenant_id": batch["tenant_id"],
                "project_id": batch["project_id"],
                "batch_id": batch["id"],
                "event_type": event_type,
                "idem": event_key,
            },
        )
        session.execute(
            text(
                """
                UPDATE platform.service2_corpus_batch
                SET status=:status,version=version+1,updated_at=now()
                WHERE id=:batch_id
                """
            ),
            {"status": terminal, "batch_id": batch["id"]},
        )
        session.commit()
        return terminal


@activity.defn
def fail_service2_corpus_batch(data: Service2BatchInput) -> str:
    """Record a bounded public error code; never persist exception text."""

    with WorkerSessionLocal() as session:
        TenantRepository(session, data.tenant_pub_id)
        batch = _batch(session, data, lock=True)
        if batch is None or batch["project_pub_id"] != data.project_pub_id:
            return "missing"
        if batch["status"] in {"frozen", "cancelled"}:
            return str(batch["status"])
        event_key = sha256(
            f"{data.tenant_pub_id}|{data.batch_pub_id}|failed|workflow_activity_failed".encode()
        ).hexdigest()
        session.execute(
            text(
                """
                INSERT INTO platform.service2_batch_event
                  (id,pub_id,tenant_id,project_id,batch_id,event_type,actor_pub_id,
                   idempotency_key,payload,created_at)
                VALUES
                  (:id,:pub_id,:tenant_id,:project_id,:batch_id,'failed','system',
                   :idem,'{"error_code":"workflow_activity_failed"}'::jsonb,now())
                ON CONFLICT (tenant_id,idempotency_key) DO NOTHING
                """
            ),
            {
                "id": uuid_from_hash(f"event|{event_key}"),
                "pub_id": f"s2e_{sha256(event_key.encode()).hexdigest()[:26]}",
                "tenant_id": batch["tenant_id"],
                "project_id": batch["project_id"],
                "batch_id": batch["id"],
                "idem": event_key,
            },
        )
        session.execute(
            text(
                """
                UPDATE platform.service2_corpus_batch
                SET status='failed',error_code='workflow_activity_failed',
                    version=version+1,updated_at=now()
                WHERE id=:batch_id
                """
            ),
            {"batch_id": batch["id"]},
        )
        session.commit()
        return "failed"


__all__ = [
    "Service2BatchInput",
    "Service2BatchPreparation",
    "Service2CorpusPageInput",
    "Service2CorpusPageResult",
    "Service2SourceFetchShard",
    "fail_service2_corpus_batch",
    "finish_service2_corpus_batch",
    "prepare_service2_corpus_batch",
    "process_service2_corpus_page",
    "refresh_service2_corpus_bindings",
]
