"""Single-model OpenAI-compatible semantic judge for Metrics V2.

This module is intentionally activity-local.  It hydrates immutable answer and
query references from the tenant database, renders an audited prompt, calls the
configured LLM, and returns only validated structured output plus bounded
telemetry.  Source text, API credentials, raw upstream responses, and private
reasoning never leave this process boundary.
"""

from __future__ import annotations

import json
import time
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol

import httpx
from geo_platform.config import Settings
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.tenancy.psycopg import tenant_connection
from psycopg.rows import dict_row

from domain.analysis.v2._canonical import canonical_hash, canonical_json
from domain.analysis.v2.candidates import CandidateSet
from domain.analysis.v2.decision_task_schema import DecisionTaskDefinition, SubjectType
from domain.analysis.v2.output_validation import validate_decision_output
from domain.metrics.v2 import normalize_query_text

_PROMPT_REF = "resource://analysis/v2/prompts/semantic-judge-v2.txt"
_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "domain"
    / "analysis"
    / "v2"
    / "prompts"
    / "semantic-judge-v2.txt"
)
_MAX_ERROR_CODE_LENGTH = 100
_MAX_CLAIM_CHARS = 4_000
_MAX_CITATION_CHARS = 4_000
_MAX_EVIDENCE_ITEMS = 12
_MAX_EVIDENCE_ITEM_CHARS = 8_000
_MAX_EVIDENCE_TOTAL_CHARS = 32_000
SEMANTIC_JUDGE_TOTAL_DEADLINE_SECONDS = 600.0


class _VerifiedObjectLoader(Protocol):
    def get_verified(self, key: str, expected_sha256: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class SemanticJudgeConfig:
    api_key: str = field(repr=False)
    base_url: str
    base_url_fallback: str
    provider: str
    model: str
    model_revision: str
    timeout_seconds: float
    max_retries: int


@dataclass(frozen=True, slots=True)
class FrozenSemanticSource:
    source_ref: str
    source_kind: str
    source_text: str = field(repr=False)
    source_text_hash: str
    related_query_text: str | None = field(default=None, repr=False)
    query_text_hash: str | None = None
    answer_content_hash: str | None = None


@dataclass(frozen=True, slots=True)
class SemanticJudgeResult:
    output: dict[str, Any]
    request_payload_hash: str
    response_payload_hash: str
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    resolved_model: str | None
    transport_mode: str


@dataclass(frozen=True, slots=True)
class FrozenSemanticContext:
    """Untrusted text hydrated inside an activity; deliberately opaque in repr."""

    prompt_input: dict[str, Any] = field(repr=False)
    evidence_context: dict[str, Any]


class SemanticJudgeFailure(RuntimeError):
    """Sanitized machine failure; never carries source text or raw response."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        safe_code = code[:_MAX_ERROR_CODE_LENGTH]
        super().__init__(safe_code)
        self.code = safe_code
        self.retryable = retryable


def config_from_settings(settings: Settings) -> SemanticJudgeConfig:
    """Resolve the dedicated judge config, falling back to shared credentials."""

    return SemanticJudgeConfig(
        api_key=(
            settings.semantic_decision_llm_api_key.strip() or settings.research_llm_api_key.strip()
        ),
        base_url=(
            settings.semantic_decision_llm_base_url.strip()
            or settings.research_llm_base_url.strip()
        ),
        base_url_fallback=(
            settings.semantic_decision_llm_base_url_fallback.strip()
            or settings.research_llm_base_url_fallback.strip()
        ),
        provider=settings.semantic_decision_llm_provider.strip() or "openai-compatible",
        model=settings.semantic_decision_llm_model.strip() or "gpt-5.6-sol",
        model_revision=(
            settings.semantic_decision_llm_model_revision.strip() or "runtime-configured"
        ),
        timeout_seconds=max(1.0, min(settings.semantic_decision_llm_timeout_seconds, 600.0)),
        max_retries=max(0, min(settings.semantic_decision_llm_max_retries, 5)),
    )


def load_frozen_semantic_source(
    *,
    dsn: str,
    payload: dict[str, Any],
    task: DecisionTaskDefinition,
) -> FrozenSemanticSource:
    """Load and verify source text by tenant/project/answer immutable references."""

    tenant_pub_id = str(payload.get("tenant_pub_id") or "")
    project_pub_id = str(payload.get("project_pub_id") or "")
    answer_pub_id = str(payload.get("source_answer_pub_id") or "")
    input_snapshot_ref = str(payload.get("input_snapshot_ref") or "")
    if not answer_pub_id and input_snapshot_ref.startswith("capture://answer/"):
        answer_pub_id = input_snapshot_ref.removeprefix("capture://answer/")
    if not tenant_pub_id or not project_pub_id or not answer_pub_id:
        raise SemanticJudgeFailure("upstream_source_reference_missing")

    normalized_dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
    with tenant_connection(
        normalized_dsn,
        tenant_pub_id,
        row_factory=dict_row,
    ) as connection:
        row = connection.execute(
            """
            SELECT pub_id,query_pub_id,query_text,response_plain_text,
                   response_markdown_normalized,response_hash
            FROM analytics.answer
            WHERE tenant_pub_id=%s AND project_pub_id=%s AND pub_id=%s
            """,
            (tenant_pub_id, project_pub_id, answer_pub_id),
        ).fetchone()
    if row is None:
        raise SemanticJudgeFailure("upstream_source_snapshot_missing")

    query_text = row.get("query_text")
    answer_text = row.get("response_plain_text")
    normalized_answer = row.get("response_markdown_normalized")
    stored_answer_hash = row.get("response_hash")
    if not isinstance(query_text, str) or not isinstance(answer_text, str):
        raise SemanticJudgeFailure("upstream_source_text_missing")
    if not isinstance(normalized_answer, str) or not isinstance(stored_answer_hash, str):
        raise SemanticJudgeFailure("upstream_source_content_hash_missing")
    if sha256(normalized_answer.encode()).hexdigest() != stored_answer_hash:
        raise SemanticJudgeFailure("upstream_source_content_hash_mismatch")

    material_hashes = payload.get("input_material_hashes")
    if not isinstance(material_hashes, dict):
        raise SemanticJudgeFailure("upstream_source_hash_envelope_missing")
    expected_query_hash = material_hashes.get("query_text_hash")
    actual_query_hash = sha256(normalize_query_text(query_text).encode()).hexdigest()
    if not isinstance(expected_query_hash, str) or expected_query_hash != actual_query_hash:
        raise SemanticJudgeFailure("upstream_source_query_text_hash_mismatch")
    expected_answer_hash = material_hashes.get("answer_text_hash")
    if expected_answer_hash is not None and expected_answer_hash != stored_answer_hash:
        raise SemanticJudgeFailure("upstream_source_answer_text_hash_mismatch")

    query_scoped = task.subject_type in {SubjectType.QUERY, SubjectType.QUERY_DIMENSION}
    if query_scoped:
        primary_text = normalize_query_text(query_text)
        return FrozenSemanticSource(
            source_ref=input_snapshot_ref,
            source_kind="query",
            source_text=primary_text,
            source_text_hash=actual_query_hash,
            query_text_hash=actual_query_hash,
            answer_content_hash=stored_answer_hash,
        )
    return FrozenSemanticSource(
        source_ref=input_snapshot_ref,
        source_kind="answer",
        source_text=answer_text,
        source_text_hash=sha256(answer_text.encode()).hexdigest(),
        related_query_text=normalize_query_text(query_text),
        query_text_hash=actual_query_hash,
        answer_content_hash=stored_answer_hash,
    )


def load_frozen_semantic_context(
    *,
    dsn: str,
    settings: Settings,
    payload: dict[str, Any],
    task: DecisionTaskDefinition,
    source: FrozenSemanticSource,
    object_store: _VerifiedObjectLoader | None = None,
) -> FrozenSemanticContext:
    """Hydrate claim/citation/evidence only inside the model activity.

    Every lookup is tenant/project/answer scoped.  The returned object may be
    used to build the local LLM request, but must never be returned by an
    activity or attached to an attempt/log record.
    """

    tenant_pub_id = str(payload.get("tenant_pub_id") or "")
    project_pub_id = str(payload.get("project_pub_id") or "")
    subject_ref = payload.get("subject_ref")
    if not tenant_pub_id or not project_pub_id or not isinstance(subject_ref, dict):
        raise SemanticJudgeFailure("upstream_evidence_integrity_failed")
    answer_pub_id = str(subject_ref.get("answer_pub_id") or "")
    claim_fingerprint = str(subject_ref.get("claim_fingerprint") or "")
    citation_pub_id = str(subject_ref.get("citation_pub_id") or "")
    needs_claim = task.subject_type in {SubjectType.CLAIM, SubjectType.CITATION}
    needs_citation = task.subject_type is SubjectType.CITATION
    needs_bundle = task.evidence_requirements.requires_frozen_evidence_bundle
    if not (needs_claim or needs_citation or needs_bundle):
        return FrozenSemanticContext(prompt_input={}, evidence_context={})

    normalized_dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
    claim_rows: list[dict[str, Any]] = []
    citation_row: dict[str, Any] | None = None
    bundle_row: dict[str, Any] | None = None
    with tenant_connection(normalized_dsn, tenant_pub_id, row_factory=dict_row) as connection:
        if needs_claim:
            if not answer_pub_id or len(claim_fingerprint) != 64:
                raise SemanticJudgeFailure("upstream_evidence_integrity_failed")
            claim_rows = list(
                connection.execute(
                    """
                    SELECT result,decision_hash,created_at
                    FROM analytics.semantic_decision_record_v2
                    WHERE tenant_pub_id=%s AND project_pub_id=%s
                      AND task_name='claim-extraction' AND status='accepted'
                      AND subject_ref->>'answer_pub_id'=%s
                    ORDER BY created_at DESC
                    """,
                    (tenant_pub_id, project_pub_id, answer_pub_id),
                ).fetchall()
            )
        if needs_citation:
            if not citation_pub_id:
                raise SemanticJudgeFailure("upstream_evidence_integrity_failed")
            citation_row = connection.execute(
                """
                SELECT citation.pub_id,citation.answer_pub_id,citation.original_url,
                       citation.canonical_url,citation.host,citation.title,
                       citation.cited_text,citation.content_hash,
                       relation.source_quote,relation.source_quote_hash,
                       relation.source_match_status
                FROM analytics.citation_fact AS citation
                JOIN analytics.answer AS answer
                  ON answer.tenant_pub_id=citation.tenant_pub_id
                 AND answer.pub_id=citation.answer_pub_id
                LEFT JOIN analytics.answer_citation_relation AS relation
                  ON relation.tenant_pub_id=citation.tenant_pub_id
                 AND relation.answer_pub_id=citation.answer_pub_id
                 AND relation.citation_pub_id=citation.pub_id
                WHERE citation.tenant_pub_id=%s AND answer.project_pub_id=%s
                  AND citation.answer_pub_id=%s AND citation.pub_id=%s
                """,
                (tenant_pub_id, project_pub_id, answer_pub_id, citation_pub_id),
            ).fetchone()
        if needs_bundle:
            bundle_ref = str(payload.get("evidence_bundle_ref") or "")
            expected_bundle_hash = str(payload.get("evidence_bundle_hash") or "")
            if not bundle_ref or len(expected_bundle_hash) != 64:
                raise SemanticJudgeFailure("upstream_evidence_retrieval_failed")
            bundle_row = connection.execute(
                """
                SELECT pub_id,purpose_task_name,subject_key,truth_as_of_policy,
                       verification_as_of,retrieval_policy_hash,retrieval_query_hash,
                       source_items,source_count,fetched_source_count,status,
                       failure_codes,bundle_hash
                FROM analytics.semantic_evidence_bundle_v2
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                  AND pub_id=%s
                """,
                (tenant_pub_id, project_pub_id, bundle_ref),
            ).fetchone()

    if needs_bundle and object_store is None:
        object_store = ContentAddressedObjectStore(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
        )
    return hydrate_frozen_semantic_context(
        task=task,
        source=source,
        subject_ref=subject_ref,
        claim_rows=claim_rows,
        citation_row=citation_row,
        bundle_row=bundle_row,
        expected_bundle_hash=str(payload.get("evidence_bundle_hash") or "") or None,
        object_store=object_store,
        requested_truth_as_of=(
            str((payload.get("evidence_context") or {}).get("truth_as_of_policy") or "")
            if isinstance(payload.get("evidence_context"), dict)
            else ""
        ),
    )


def hydrate_frozen_semantic_context(
    *,
    task: DecisionTaskDefinition,
    source: FrozenSemanticSource,
    subject_ref: dict[str, Any],
    claim_rows: list[dict[str, Any]],
    citation_row: dict[str, Any] | None,
    bundle_row: dict[str, Any] | None,
    expected_bundle_hash: str | None,
    object_store: _VerifiedObjectLoader | None,
    requested_truth_as_of: str = "",
) -> FrozenSemanticContext:
    """Validate already loaded frozen rows and construct bounded prompt input."""

    prompt_input: dict[str, Any] = {}
    context_material_truncated = False
    if task.subject_type in {SubjectType.CLAIM, SubjectType.CITATION}:
        claim = _select_verified_claim(
            claim_rows,
            answer_pub_id=str(subject_ref.get("answer_pub_id") or ""),
            claim_fingerprint=str(subject_ref.get("claim_fingerprint") or ""),
            answer_text=source.source_text,
        )
        prompt_input["frozen_claim"] = claim
        context_material_truncated = bool(claim["claim_text_truncated"])
    if task.subject_type is SubjectType.CITATION:
        citation = _verified_citation(
            citation_row,
            expected_pub_id=str(subject_ref.get("citation_pub_id") or ""),
            expected_answer_pub_id=str(subject_ref.get("answer_pub_id") or ""),
        )
        prompt_input["frozen_citation"] = citation
        context_material_truncated = context_material_truncated or bool(
            citation["material_truncated"]
        )

    evidence_context: dict[str, Any] = (
        {"evidence_material_truncated": True} if context_material_truncated else {}
    )
    if task.evidence_requirements.requires_frozen_evidence_bundle:
        if bundle_row is None:
            raise SemanticJudgeFailure("upstream_evidence_retrieval_failed")
        if bundle_row.get("status") != "ready":
            raise SemanticJudgeFailure("upstream_evidence_retrieval_failed")
        items = bundle_row.get("source_items")
        if not isinstance(items, list):
            raise SemanticJudgeFailure("upstream_evidence_integrity_failed")
        if int(bundle_row.get("source_count") or 0) != len(items):
            raise SemanticJudgeFailure("upstream_evidence_integrity_failed")
        if int(bundle_row.get("fetched_source_count") or 0) != sum(
            str(item.get("fetch_status") or "") == "fetched"
            for item in items
            if isinstance(item, dict)
        ):
            raise SemanticJudgeFailure("upstream_evidence_integrity_failed")
        actual_bundle_hash = canonical_hash(items)
        stored_bundle_hash = str(bundle_row.get("bundle_hash") or "")
        if (
            not expected_bundle_hash
            or stored_bundle_hash != expected_bundle_hash
            or actual_bundle_hash != expected_bundle_hash
        ):
            raise SemanticJudgeFailure("upstream_evidence_integrity_failed")
        truth_as_of = str(bundle_row.get("truth_as_of_policy") or "")
        if requested_truth_as_of and truth_as_of != requested_truth_as_of:
            raise SemanticJudgeFailure("upstream_evidence_integrity_failed")
        if object_store is None:
            raise SemanticJudgeFailure("upstream_evidence_retrieval_failed")
        hydrated_items = _hydrate_evidence_items(items, object_store=object_store)
        material_truncated = (
            context_material_truncated
            or len(hydrated_items) < len(items)
            or any(item["text_truncated"] for item in hydrated_items)
        )
        prompt_input["frozen_evidence_bundle"] = {
            "bundle_ref": str(bundle_row.get("pub_id") or ""),
            "bundle_hash": stored_bundle_hash,
            "truth_as_of_policy": truth_as_of,
            "verification_as_of": str(bundle_row.get("verification_as_of") or ""),
            "source_items": hydrated_items,
            "omitted_source_count": max(0, len(items) - len(hydrated_items)),
            "material_truncated": material_truncated,
        }
        evidence_context = {
            "evidence_bundle_ref": str(bundle_row.get("pub_id") or ""),
            "evidence_bundle_hash": stored_bundle_hash,
            "evidence_bundle_status": "ready",
            "retrieval_protocol_complete": True,
            "truth_as_of_policy": truth_as_of,
            "evidence_material_truncated": material_truncated,
        }
    return FrozenSemanticContext(prompt_input=prompt_input, evidence_context=evidence_context)


def execute_semantic_judge(
    *,
    config: SemanticJudgeConfig,
    task: DecisionTaskDefinition,
    source: FrozenSemanticSource,
    subject_ref: dict[str, Any],
    candidate_set: CandidateSet | None,
    evidence_context: dict[str, Any] | None = None,
    frozen_context: FrozenSemanticContext | None = None,
    transport: httpx.BaseTransport | None = None,
) -> SemanticJudgeResult:
    """Call one configured model and return only strictly validated output."""

    if not config.api_key:
        raise SemanticJudgeFailure("llm_api_auth_missing")
    if not config.base_url:
        raise SemanticJudgeFailure("llm_api_adapter_unavailable")

    system_message = _render_system_prompt(task)
    untrusted_input = {
        "candidate_set": (
            candidate_set.model_dump(mode="json") if candidate_set is not None else None
        ),
        "evidence_context": _safe_evidence_context(evidence_context or {}),
        "related_query_text": source.related_query_text,
        "source_kind": source.source_kind,
        "source_ref": source.source_ref,
        "source_text": source.source_text,
        "source_text_hash": source.source_text_hash,
        "subject_ref": subject_ref,
        # These are quoted data, never instructions. They were hydrated and
        # hash-checked inside this activity and are absent from every result.
        "untrusted_frozen_material": (
            frozen_context.prompt_input if frozen_context is not None else {}
        ),
    }
    user_message = "UNTRUSTED_INPUT=" + canonical_json(untrusted_input)
    strict_body: dict[str, Any] = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "semantic_decision_v2",
                "strict": True,
                "schema": task.output_schema,
            },
        },
    }
    started = time.perf_counter()
    deadline = started + SEMANTIC_JUDGE_TOTAL_DEADLINE_SECONDS
    last_failure = SemanticJudgeFailure("llm_api_adapter_unavailable", retryable=True)
    base_urls = tuple(
        dict.fromkeys(
            _normalize_base_url(value)
            for value in (config.base_url, config.base_url_fallback)
            if value.strip()
        )
    )
    json_only_body = {
        "model": config.model,
        "messages": strict_body["messages"],
    }
    for transport_mode, body in (
        ("json_schema", strict_body),
        ("json_only", json_only_body),
    ):
        fallback_requested = False
        request_hash = canonical_hash(body)
        for _retry_index in range(config.max_retries + 1):
            for base_url in base_urls:
                remaining_seconds = deadline - time.perf_counter()
                if remaining_seconds <= 0:
                    raise SemanticJudgeFailure("llm_api_timeout", retryable=True)
                try:
                    document = _post_once(
                        config=config,
                        base_url=base_url,
                        body=body,
                        transport=transport,
                        timeout_seconds=min(config.timeout_seconds, remaining_seconds),
                    )
                    output, input_tokens, output_tokens, resolved_model = _extract_output(document)
                    normalized = _complete_deterministic_fields(
                        output,
                        task=task,
                        source_text=source.source_text,
                        subject_ref=subject_ref,
                    )
                    checked = validate_decision_output(
                        task=task,
                        output=normalized,
                        candidate_set=candidate_set,
                        answer_text=source.source_text,
                        expected_answer_text_hash=source.source_text_hash,
                        evidence_context=evidence_context,
                    )
                    if not checked.is_valid or checked.output is None:
                        raise SemanticJudgeFailure("llm_api_schema_violation", retryable=True)
                    return SemanticJudgeResult(
                        output=checked.output,
                        request_payload_hash=request_hash,
                        response_payload_hash=canonical_hash(checked.output),
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        resolved_model=resolved_model,
                        transport_mode=transport_mode,
                    )
                except SemanticJudgeFailure as failure:
                    last_failure = failure
                    if time.perf_counter() >= deadline:
                        raise SemanticJudgeFailure("llm_api_timeout", retryable=True) from failure
                    if (
                        failure.code == "llm_api_response_format_unsupported"
                        and transport_mode == "json_schema"
                    ):
                        fallback_requested = True
                        break
                    if not failure.retryable:
                        raise
            if fallback_requested:
                break
        if fallback_requested:
            continue
        raise last_failure
    raise last_failure


def _select_verified_claim(
    rows: list[dict[str, Any]],
    *,
    answer_pub_id: str,
    claim_fingerprint: str,
    answer_text: str,
) -> dict[str, Any]:
    for row in rows:
        result = row.get("result")
        claims = result.get("claims") if isinstance(result, dict) else None
        if not isinstance(claims, list):
            continue
        for value in claims:
            if not isinstance(value, dict) or value.get("claim_fingerprint") != claim_fingerprint:
                continue
            calculated = canonical_hash(
                {
                    "answer_pub_id": answer_pub_id,
                    "claim_text": value.get("claim_text"),
                    "end": value.get("end"),
                    "object": value.get("object"),
                    "predicate": value.get("predicate"),
                    "start": value.get("start"),
                    "subject": value.get("subject"),
                    "subject_entity_id": value.get("subject_entity_id"),
                    "time_scope": value.get("time_scope"),
                }
            )
            if calculated != claim_fingerprint:
                raise SemanticJudgeFailure("upstream_evidence_integrity_failed")
            start, end = value.get("start"), value.get("end")
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or not 0 <= start < end <= len(answer_text)
            ):
                raise SemanticJudgeFailure("upstream_evidence_integrity_failed")
            claim_text = str(value.get("claim_text") or "")
            if not claim_text or claim_text != answer_text[start:end]:
                raise SemanticJudgeFailure("upstream_evidence_integrity_failed")
            excerpt_hash = value.get("excerpt_hash")
            if excerpt_hash is not None and excerpt_hash != sha256(claim_text.encode()).hexdigest():
                raise SemanticJudgeFailure("upstream_evidence_integrity_failed")
            return {
                "claim_fingerprint": claim_fingerprint,
                "claim_text": claim_text[:_MAX_CLAIM_CHARS],
                "claim_text_truncated": len(claim_text) > _MAX_CLAIM_CHARS,
                "claim_text_hash": sha256(claim_text.encode()).hexdigest(),
                "subject": value.get("subject"),
                "predicate": value.get("predicate"),
                "object": value.get("object"),
                "time_scope": value.get("time_scope"),
                "start": start,
                "end": end,
            }
    raise SemanticJudgeFailure("upstream_evidence_retrieval_failed")


def _verified_citation(
    row: dict[str, Any] | None,
    *,
    expected_pub_id: str,
    expected_answer_pub_id: str,
) -> dict[str, Any]:
    if row is None:
        raise SemanticJudgeFailure("upstream_evidence_retrieval_failed")
    if row.get("pub_id") != expected_pub_id or row.get("answer_pub_id") != expected_answer_pub_id:
        raise SemanticJudgeFailure("upstream_evidence_integrity_failed")
    source_quote = row.get("source_quote")
    source_quote_hash = row.get("source_quote_hash")
    if source_quote is not None:
        if (
            not isinstance(source_quote, str)
            or source_quote_hash != sha256(source_quote.encode()).hexdigest()
        ):
            raise SemanticJudgeFailure("upstream_evidence_integrity_failed")
    cited_text = row.get("cited_text")
    if cited_text is not None and not isinstance(cited_text, str):
        raise SemanticJudgeFailure("upstream_evidence_integrity_failed")
    return {
        "citation_pub_id": expected_pub_id,
        "canonical_url": str(row.get("canonical_url") or "")[:2_000],
        "title": str(row.get("title") or "")[:500],
        "cited_text": (cited_text or "")[:_MAX_CITATION_CHARS],
        "cited_text_hash": sha256((cited_text or "").encode()).hexdigest(),
        "verified_source_quote": (source_quote or "")[:_MAX_CITATION_CHARS],
        "verified_source_quote_hash": source_quote_hash,
        "source_match_status": row.get("source_match_status"),
        "material_truncated": len(cited_text or "") > _MAX_CITATION_CHARS
        or len(source_quote or "") > _MAX_CITATION_CHARS,
    }


def _hydrate_evidence_items(
    items: list[Any], *, object_store: _VerifiedObjectLoader
) -> list[dict[str, Any]]:
    hydrated: list[dict[str, Any]] = []
    remaining = _MAX_EVIDENCE_TOTAL_CHARS
    for item in items[:_MAX_EVIDENCE_ITEMS]:
        if not isinstance(item, dict):
            raise SemanticJudgeFailure("upstream_evidence_integrity_failed")
        if str(item.get("fetch_status") or "") != "fetched":
            raise SemanticJudgeFailure("upstream_evidence_retrieval_failed")
        cas_ref = str(item.get("cas_ref") or "")
        content_hash = str(item.get("content_hash") or "")
        if not cas_ref or len(content_hash) != 64:
            raise SemanticJudgeFailure("upstream_evidence_retrieval_failed")
        try:
            raw = object_store.get_verified(_cas_object_key(cas_ref), content_hash)
        except SemanticJudgeFailure:
            raise
        except ValueError as error:
            raise SemanticJudgeFailure("upstream_evidence_integrity_failed") from error
        except Exception as error:
            raise SemanticJudgeFailure(
                "upstream_evidence_retrieval_failed", retryable=True
            ) from error
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise SemanticJudgeFailure("upstream_evidence_integrity_failed") from error
        maximum = min(_MAX_EVIDENCE_ITEM_CHARS, remaining)
        excerpt = text[:maximum]
        remaining -= len(excerpt)
        hydrated.append(
            {
                "source_ref": str(item.get("source_ref") or "")[:500],
                "cas_ref": cas_ref[:500],
                "content_hash": content_hash,
                "paragraph_start": item.get("paragraph_start"),
                "paragraph_end": item.get("paragraph_end"),
                "text": excerpt,
                "text_truncated": len(excerpt) < len(text),
                "original_char_count": len(text),
            }
        )
        if remaining <= 0:
            break
    return hydrated


def _cas_object_key(cas_ref: str) -> str:
    if cas_ref.startswith("cas://geo-evidence/"):
        key = cas_ref.removeprefix("cas://geo-evidence/")
    elif cas_ref.startswith("cas://"):
        key = cas_ref.removeprefix("cas://")
    else:
        key = cas_ref
    if not key or key.startswith("/") or ".." in key.split("/"):
        raise SemanticJudgeFailure("upstream_evidence_integrity_failed")
    return key


def _render_system_prompt(task: DecisionTaskDefinition) -> str:
    if task.prompt_template_ref != _PROMPT_REF:
        raise SemanticJudgeFailure("upstream_prompt_template_unavailable")
    try:
        template = _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as error:
        raise SemanticJudgeFailure("upstream_prompt_template_unavailable") from error
    if sha256(template.encode()).hexdigest() != task.prompt_template_hash:
        raise SemanticJudgeFailure("upstream_prompt_template_hash_mismatch")
    placeholder = "{{task_contract_json}}"
    if template.count(placeholder) != 1:
        raise SemanticJudgeFailure("upstream_prompt_template_invalid")
    contract = {
        "business_question": task.business_question,
        "candidate_policy": task.candidate_policy.model_dump(mode="json"),
        "evidence_requirements": task.evidence_requirements.model_dump(mode="json"),
        "output_schema": task.output_schema,
        "prompt_template_hash": task.prompt_template_hash,
        "rubric_hash": task.rubric_hash,
        "rubric_ref": task.rubric_ref,
        "subject_ref_schema": task.subject_ref_schema,
        "task_definition_hash": task.definition_hash,
        "task_ref": task.task_ref,
    }
    return template.replace(placeholder, canonical_json(contract))


def _post_once(
    *,
    config: SemanticJudgeConfig,
    base_url: str,
    body: dict[str, Any],
    transport: httpx.BaseTransport | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    try:
        with httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=timeout_seconds,
            trust_env=False,
            transport=transport,
        ) as client:
            response = client.post("/chat/completions", json=body)
    except httpx.TimeoutException as error:
        raise SemanticJudgeFailure("llm_api_timeout", retryable=True) from error
    except httpx.TransportError as error:
        raise SemanticJudgeFailure("llm_api_network_error", retryable=True) from error

    if response.status_code in {401, 403}:
        raise SemanticJudgeFailure("llm_api_auth_rejected")
    if response.status_code == 429:
        raise SemanticJudgeFailure("llm_api_rate_limited", retryable=True)
    if response.status_code == 408:
        raise SemanticJudgeFailure("llm_api_timeout", retryable=True)
    if response.status_code >= 500:
        raise SemanticJudgeFailure("llm_api_upstream_unavailable", retryable=True)
    if response.status_code == 400 and "response_format" in body:
        # OpenAI-compatible gateways do not use one stable error vocabulary for
        # unsupported json_schema.  The strict request differs from the
        # compatibility request only by response_format, so one JSON-only retry
        # is safe even when the bounded provider message does not literally say
        # "unsupported".  The same model/prompt is used and the result still has
        # to pass the complete local schema/candidate/span invariant validator.
        raise SemanticJudgeFailure("llm_api_response_format_unsupported")
    if response.status_code != 200:
        raise SemanticJudgeFailure("llm_api_request_rejected")
    try:
        document = response.json()
    except ValueError as error:
        raise SemanticJudgeFailure("llm_api_invalid_response", retryable=True) from error
    if not isinstance(document, dict):
        raise SemanticJudgeFailure("llm_api_invalid_response", retryable=True)
    return document


def _extract_output(
    document: dict[str, Any],
) -> tuple[dict[str, Any], int | None, int | None, str | None]:
    try:
        message = document["choices"][0]["message"]
        content = message["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise SemanticJudgeFailure("llm_api_invalid_response", retryable=True) from error
    if not isinstance(content, str) or not content.strip():
        raise SemanticJudgeFailure("llm_api_empty_response", retryable=True)
    try:
        output = json.loads(content)
    except ValueError as error:
        raise SemanticJudgeFailure("llm_api_invalid_json", retryable=True) from error
    if not isinstance(output, dict):
        raise SemanticJudgeFailure("llm_api_invalid_json", retryable=True)
    usage = document.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    input_tokens = _optional_nonnegative_int(usage.get("prompt_tokens"))
    output_tokens = _optional_nonnegative_int(usage.get("completion_tokens"))
    resolved_model = document.get("model")
    return (
        output,
        input_tokens,
        output_tokens,
        str(resolved_model)[:200] if isinstance(resolved_model, str) else None,
    )


def _complete_deterministic_fields(
    output: dict[str, Any],
    *,
    task: DecisionTaskDefinition,
    source_text: str,
    subject_ref: dict[str, Any],
) -> dict[str, Any]:
    completed = deepcopy(output)

    def visit(value: object) -> None:
        if isinstance(value, dict):
            start = value.get("start")
            end = value.get("end")
            if (
                "excerpt_hash" in value
                and isinstance(start, int)
                and not isinstance(start, bool)
                and isinstance(end, int)
                and not isinstance(end, bool)
                and 0 <= start < end <= len(source_text)
            ):
                value["excerpt_hash"] = sha256(source_text[start:end].encode()).hexdigest()
            elif "excerpt_hash" in value and start is None and end is None:
                value["excerpt_hash"] = None
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(completed)
    if task.name == "claim-extraction":
        claims = completed.get("claims")
        if isinstance(claims, list):
            for claim in claims:
                if not isinstance(claim, dict) or "claim_fingerprint" not in claim:
                    continue
                claim["claim_fingerprint"] = canonical_hash(
                    {
                        "answer_pub_id": subject_ref.get("answer_pub_id"),
                        "claim_text": claim.get("claim_text"),
                        "end": claim.get("end"),
                        "object": claim.get("object"),
                        "predicate": claim.get("predicate"),
                        "start": claim.get("start"),
                        "subject": claim.get("subject"),
                        "subject_entity_id": claim.get("subject_entity_id"),
                        "time_scope": claim.get("time_scope"),
                    }
                )
    elif "claim_fingerprint" in completed and isinstance(subject_ref.get("claim_fingerprint"), str):
        completed["claim_fingerprint"] = subject_ref["claim_fingerprint"]
    for identity_field in ("citation_pub_id", "dimension_id"):
        if identity_field in completed and isinstance(subject_ref.get(identity_field), str):
            completed[identity_field] = subject_ref[identity_field]
    return completed


def _safe_evidence_context(value: dict[str, Any]) -> dict[str, Any]:
    """Keep only bounded structured references; never accept raw evidence text."""

    allowed = {
        "evidence_bundle_hash",
        "evidence_bundle_ref",
        "evidence_bundle_status",
        "retrieval_protocol_complete",
        "truth_as_of_policy",
        "evidence_material_truncated",
        "high_severity_review",
    }
    return {key: value[key] for key in sorted(value) if key in allowed}


def _normalize_base_url(value: str) -> str:
    normalized = value.rstrip("/")
    return normalized if normalized.endswith("/v1") else f"{normalized}/v1"


def _optional_nonnegative_int(value: object) -> int | None:
    if value is None or isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    try:
        converted = int(value)
    except (TypeError, ValueError):
        return None
    return converted if converted >= 0 else None


__all__ = [
    "FrozenSemanticContext",
    "FrozenSemanticSource",
    "SemanticJudgeConfig",
    "SemanticJudgeFailure",
    "SemanticJudgeResult",
    "config_from_settings",
    "execute_semantic_judge",
    "hydrate_frozen_semantic_context",
    "load_frozen_semantic_context",
    "load_frozen_semantic_source",
]
