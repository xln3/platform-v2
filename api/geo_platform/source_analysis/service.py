"""Versioned object profiles and read models for page inspection."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

from domain.source_analysis.page_inspection import (
    PAGE_INSPECTION_PROMPT_VERSION,
    derive_page_inspection_version,
    derive_profile_type,
    profile_fingerprint,
)
from workflows.activities.analysis_jobs import canonical_input_hash, derive_analysis_job_pub_id


class SourceAnalysisNotFound(LookupError):
    """Tenant/project/profile/inspection is outside the caller's scope."""


class SourceAnalysisInvalid(ValueError):
    """Profile payload violates a domain invariant."""


class SourceAnalysisNotReady(RuntimeError):
    """The run has no fetched source document to inspect yet."""


def derive_profile_pub_id(
    tenant_pub_id: str, project_pub_id: str, revision: int, profile_hash: str
) -> str:
    stable = f"{tenant_pub_id}|{project_pub_id}|{revision}|{profile_hash}"
    return f"sap_{sha256(stable.encode()).hexdigest()[:26]}"


def derive_inspection_policy_version(
    *, profile_revision: int, model: str, prompt_version: str
) -> str:
    """Version manual reanalysis by every frozen interpretation input.

    A profile revision alone is not enough: changing either the model or prompt
    must create a new immutable analysis job instead of colliding with the old
    job and reporting payload drift.
    """

    return derive_page_inspection_version(
        profile_revision=profile_revision,
        model=model,
        prompt_version=prompt_version,
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _public_profile(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in row.items() if key not in {"id", "tenant_id", "project_id"}
    }


def _is_http_url(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlsplit(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _validate_profile_shape(canonical: Mapping[str, Any]) -> None:
    categories = canonical.get("categories")
    if (
        not isinstance(categories, list)
        or not categories
        or any(not isinstance(item, str) or not item.strip() for item in categories)
    ):
        raise SourceAnalysisInvalid("profile categories are invalid")

    aliases = canonical.get("aliases")
    if not isinstance(aliases, list):
        raise SourceAnalysisInvalid("profile aliases are invalid")
    seen_aliases: set[str] = set()
    for alias in aliases:
        if not isinstance(alias, Mapping):
            raise SourceAnalysisInvalid("profile alias is invalid")
        value = alias.get("value")
        if not isinstance(value, str) or not value.strip():
            raise SourceAnalysisInvalid("profile alias value is invalid")
        folded = value.strip().casefold()
        if folded in seen_aliases:
            raise SourceAnalysisInvalid("profile alias is duplicated")
        seen_aliases.add(folded)
        evidence_url = alias.get("evidence_url")
        capture_pub_id = alias.get("capture_pub_id")
        if not evidence_url and not capture_pub_id:
            raise SourceAnalysisInvalid("profile alias provenance is missing")
        if evidence_url and not _is_http_url(evidence_url):
            raise SourceAnalysisInvalid("profile alias evidence URL is invalid")
        if capture_pub_id and (not isinstance(capture_pub_id, str) or not capture_pub_id.strip()):
            raise SourceAnalysisInvalid("profile alias capture reference is invalid")

    anchors = canonical.get("anchor_sources")
    if not isinstance(anchors, list):
        raise SourceAnalysisInvalid("profile anchors are invalid")
    for anchor in anchors:
        if not isinstance(anchor, Mapping) or not _is_http_url(anchor.get("url")):
            raise SourceAnalysisInvalid("profile anchor is invalid")
    if canonical.get("hard_anchor_available") is True and not anchors:
        raise SourceAnalysisInvalid("hard anchor profile requires anchor sources")


class SourceAnalysisService:
    def __init__(
        self,
        *,
        dsn: str,
        connect: Callable[[], psycopg.Connection[Any]] | None = None,
    ) -> None:
        self._dsn = dsn
        self._connect = connect

    def _new_connection(self) -> psycopg.Connection[Any]:
        if self._connect is not None:
            return self._connect()
        return psycopg.connect(self._dsn, row_factory=dict_row)

    @contextmanager
    def _tenant_conn(self, tenant_pub_id: str) -> Iterator[tuple[psycopg.Connection[Any], str]]:
        with self._new_connection() as connection:
            tenant = connection.execute(
                "SELECT id FROM platform.tenant WHERE pub_id=%s", (tenant_pub_id,)
            ).fetchone()
            if tenant is None:
                raise SourceAnalysisNotFound("tenant not found")
            tenant_id = str(tenant["id"])
            connection.execute(
                "SELECT set_config('app.tenant_id', %s, true), "
                "set_config('app.tenant_pub_id', %s, true)",
                (tenant_id, tenant_pub_id),
            )
            yield connection, tenant_id

    @staticmethod
    def _project_id(connection: psycopg.Connection[Any], project_pub_id: str) -> str:
        row = connection.execute(
            "SELECT id FROM platform.project WHERE pub_id=%s", (project_pub_id,)
        ).fetchone()
        if row is None:
            raise SourceAnalysisNotFound("project not found")
        return str(row["id"])

    def put_profile(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        created_by: str,
        payload: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        decision_mode = str(payload.get("decision_mode") or "")
        hard_anchor_available = payload.get("hard_anchor_available")
        if decision_mode not in {"selection", "reputation"} or not isinstance(
            hard_anchor_available, bool
        ):
            raise SourceAnalysisInvalid("profile axes are invalid")
        profile_type = derive_profile_type(
            hard_anchor_available=hard_anchor_available,
            decision_mode=decision_mode,  # type: ignore[arg-type]
        )
        canonical = {
            "object_name": str(payload.get("object_name") or "").strip(),
            "object_kind": str(payload.get("object_kind") or "").strip(),
            "categories": list(payload.get("categories") or []),
            "aliases": list(payload.get("aliases") or []),
            "own_domains": list(payload.get("own_domains") or []),
            "peers": list(payload.get("peers") or []),
            "anchor_sources": list(payload.get("anchor_sources") or []),
            "linked_entities": list(payload.get("linked_entities") or []),
            "hard_anchor_available": hard_anchor_available,
            "decision_mode": decision_mode,
            "profile_type": profile_type,
        }
        if not canonical["object_name"] or canonical["object_kind"] not in {"brand", "product"}:
            raise SourceAnalysisInvalid("profile object is invalid")
        _validate_profile_shape(canonical)
        digest = profile_fingerprint(canonical)
        canonical_aliases = canonical["aliases"]
        assert isinstance(canonical_aliases, list)

        with self._tenant_conn(tenant_pub_id) as (connection, tenant_id):
            project_id = self._project_id(connection, project_pub_id)
            for alias in canonical_aliases:
                assert isinstance(alias, Mapping)
                capture_pub_id = alias.get("capture_pub_id")
                if not capture_pub_id:
                    continue
                capture = connection.execute(
                    """
                    SELECT task.answer_text
                    FROM platform.collection_task task
                    JOIN platform.collection_run run ON run.id=task.run_id
                    WHERE task.pub_id=%s AND run.project_id=%s AND task.state='completed'
                    """,
                    (capture_pub_id, project_id),
                ).fetchone()
                alias_value = str(alias["value"]).strip()
                if capture is None or alias_value not in str(capture["answer_text"] or ""):
                    raise SourceAnalysisInvalid("profile alias capture does not prove the alias")
            # Serialize revision allocation and active-row replacement per project.
            connection.execute(
                "SELECT id FROM platform.project WHERE id=%s FOR UPDATE", (project_id,)
            ).fetchone()
            current = connection.execute(
                """
                SELECT * FROM platform.source_analysis_profile
                WHERE project_id=%s AND state='active'
                """,
                (project_id,),
            ).fetchone()
            if current is not None and str(current["profile_hash"]) == digest:
                return _public_profile(current), False
            revision_row = connection.execute(
                "SELECT COALESCE(MAX(revision),0)+1 AS next_revision "
                "FROM platform.source_analysis_profile WHERE project_id=%s",
                (project_id,),
            ).fetchone()
            assert revision_row is not None
            revision = int(revision_row["next_revision"])
            profile_pub_id = derive_profile_pub_id(tenant_pub_id, project_pub_id, revision, digest)
            connection.execute(
                """
                UPDATE platform.source_analysis_profile
                SET state='retired',updated_at=now()
                WHERE project_id=%s AND state='active'
                """,
                (project_id,),
            )
            row = connection.execute(
                """
                INSERT INTO platform.source_analysis_profile
                  (id,pub_id,tenant_id,project_id,revision,state,object_name,object_kind,
                   categories,aliases,own_domains,peers,anchor_sources,linked_entities,
                   hard_anchor_available,decision_mode,profile_type,profile_hash,created_by)
                VALUES
                  (%s,%s,%s,%s,%s,'active',%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,
                   %s::jsonb,%s::jsonb,%s,%s,%s,%s,%s)
                RETURNING *
                """,
                (
                    uuid.uuid4(),
                    profile_pub_id,
                    tenant_id,
                    project_id,
                    revision,
                    canonical["object_name"],
                    canonical["object_kind"],
                    _json(canonical["categories"]),
                    _json(canonical["aliases"]),
                    _json(canonical["own_domains"]),
                    _json(canonical["peers"]),
                    _json(canonical["anchor_sources"]),
                    _json(canonical["linked_entities"]),
                    hard_anchor_available,
                    decision_mode,
                    profile_type,
                    digest,
                    created_by,
                ),
            ).fetchone()
            assert row is not None
            connection.commit()
            return _public_profile(row), True

    def get_active_profile(self, *, tenant_pub_id: str, project_pub_id: str) -> dict[str, Any]:
        with self._tenant_conn(tenant_pub_id) as (connection, _tenant_id):
            project_id = self._project_id(connection, project_pub_id)
            row = connection.execute(
                """
                SELECT * FROM platform.source_analysis_profile
                WHERE project_id=%s AND state='active'
                """,
                (project_id,),
            ).fetchone()
            if row is None:
                raise SourceAnalysisNotFound("active profile not found")
            return _public_profile(row)

    def enqueue_run_inspection(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        run_pub_id: str,
        profile_pub_id: str | None,
        task_queue: str,
        model: str,
        prompt_version: str = PAGE_INSPECTION_PROMPT_VERSION,
    ) -> tuple[dict[str, Any], bool]:
        """Queue a profile-versioned reanalysis of already fetched run sources."""

        with self._tenant_conn(tenant_pub_id) as (connection, tenant_id):
            project_id = self._project_id(connection, project_pub_id)
            run = connection.execute(
                """
                SELECT id FROM platform.collection_run
                WHERE pub_id=%s AND project_id=%s
                """,
                (run_pub_id, project_id),
            ).fetchone()
            if run is None:
                raise SourceAnalysisNotFound("run not found")
            profile = connection.execute(
                """
                SELECT id,pub_id,revision,profile_hash
                FROM platform.source_analysis_profile
                WHERE project_id=%s
                  AND ((%s::text IS NOT NULL AND pub_id=%s)
                       OR (%s::text IS NULL AND state='active'))
                ORDER BY revision DESC LIMIT 1
                """,
                (project_id, profile_pub_id, profile_pub_id, profile_pub_id),
            ).fetchone()
            if profile is None:
                raise SourceAnalysisNotFound("profile not found")
            ready = connection.execute(
                """
                SELECT count(*) AS n FROM platform.source_document
                WHERE run_id=%s AND extract_status='ok'
                  AND text_cas_key IS NOT NULL AND text_sha256 IS NOT NULL
                """,
                (run["id"],),
            ).fetchone()
            assert ready is not None
            if int(ready["n"]) == 0:
                raise SourceAnalysisNotReady("source documents are not ready")

            frozen_model = model.strip()
            frozen_prompt_version = prompt_version.strip()
            if not frozen_prompt_version:
                raise SourceAnalysisInvalid("prompt version is required")
            policy_version = derive_inspection_policy_version(
                profile_revision=int(profile["revision"]),
                model=frozen_model,
                prompt_version=frozen_prompt_version,
            )
            workflow_id = (
                f"page-inspection/{tenant_pub_id}/{run_pub_id}/{profile['pub_id']}/{policy_version}"
            )
            contract = {
                "tenant_pub_id": tenant_pub_id,
                "project_pub_id": project_pub_id,
                "run_pub_id": run_pub_id,
                "profile_pub_id": str(profile["pub_id"]),
                "profile_hash": str(profile["profile_hash"]),
                "policy_version": policy_version,
                "model": frozen_model,
                "prompt_version": frozen_prompt_version,
            }
            input_hash = canonical_input_hash(contract)
            job_pub_id = derive_analysis_job_pub_id(
                tenant_pub_id=tenant_pub_id,
                subject_type="run",
                subject_pub_id=run_pub_id,
                analyzer_kind="page_inspection",
                policy_version=policy_version,
            )
            inserted_job = connection.execute(
                """
                INSERT INTO platform.analysis_job
                  (id,pub_id,tenant_id,project_id,run_id,answer_task_id,subject_type,
                   subject_pub_id,analyzer_kind,policy_version,input_hash,workflow_id,state)
                VALUES
                  (%s,%s,%s,%s,%s,NULL,'run',%s,'page_inspection',%s,%s,%s,'queued')
                ON CONFLICT ON CONSTRAINT uq_analysis_job_subject_analyzer_policy DO NOTHING
                RETURNING pub_id,state,policy_version,input_hash,workflow_id,
                          created_at,updated_at
                """,
                (
                    uuid.uuid4(),
                    job_pub_id,
                    tenant_id,
                    project_id,
                    run["id"],
                    run_pub_id,
                    policy_version,
                    input_hash,
                    workflow_id,
                ),
            ).fetchone()
            created = inserted_job is not None
            row = (
                inserted_job
                or connection.execute(
                    """
                SELECT pub_id,state,policy_version,input_hash,workflow_id,
                       created_at,updated_at
                FROM platform.analysis_job
                WHERE tenant_id=%s AND subject_type='run' AND subject_pub_id=%s
                  AND analyzer_kind='page_inspection' AND policy_version=%s
                """,
                    (tenant_id, run_pub_id, policy_version),
                ).fetchone()
            )
            assert row is not None
            if str(row["input_hash"]) != input_hash or str(row["workflow_id"]) != workflow_id:
                raise SourceAnalysisInvalid("analysis job replay payload drifted")
            payload = {**contract, "analysis_job_pub_id": job_pub_id}
            inserted_command = connection.execute(
                """
                INSERT INTO integration.workflow_start_command
                  (command_id,tenant_pub_id,workflow_type,workflow_id,task_queue,
                   payload,trace_context)
                VALUES (%s,%s,'page_inspection',%s,%s,%s::jsonb,'{}'::jsonb)
                ON CONFLICT (workflow_id) DO NOTHING
                RETURNING payload
                """,
                (uuid.uuid4(), tenant_pub_id, workflow_id, task_queue, _json(payload)),
            ).fetchone()
            command = (
                inserted_command
                or connection.execute(
                    """
                SELECT payload FROM integration.workflow_start_command
                WHERE workflow_id=%s AND tenant_pub_id=%s
                """,
                    (workflow_id, tenant_pub_id),
                ).fetchone()
            )
            assert command is not None
            if command["payload"] != payload:
                raise SourceAnalysisInvalid("workflow replay payload drifted")
            connection.commit()
            public = dict(row)
            public.update(
                {
                    "profile_pub_id": str(profile["pub_id"]),
                    "run_pub_id": run_pub_id,
                }
            )
            return public, created

    def list_profiles(self, *, tenant_pub_id: str, project_pub_id: str) -> list[dict[str, Any]]:
        with self._tenant_conn(tenant_pub_id) as (connection, _tenant_id):
            project_id = self._project_id(connection, project_pub_id)
            rows = connection.execute(
                """
                SELECT * FROM platform.source_analysis_profile
                WHERE project_id=%s ORDER BY revision DESC
                """,
                (project_id,),
            ).fetchall()
            return [_public_profile(row) for row in rows]

    def list_inspections(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        run_pub_id: str | None,
        cursor: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._tenant_conn(tenant_pub_id) as (connection, _tenant_id):
            project_id = self._project_id(connection, project_pub_id)
            rows = connection.execute(
                """
                SELECT i.pub_id,r.pub_id AS run_pub_id,d.pub_id AS source_document_pub_id,
                       d.url,d.host,d.page_title,d.publisher,d.authors,
                       p.pub_id AS profile_pub_id,p.revision AS profile_revision,
                       i.policy_version,i.prompt_version,i.model,i.status,i.page_summary,
                       i.transmission,i.attribution,i.quality,i.created_at,i.updated_at,
                       count(f.id)::int AS finding_count,
                       count(f.id) FILTER (WHERE f.ledger='statement')::int AS statement_count,
                       count(f.id) FILTER (WHERE f.ledger='exposure')::int AS exposure_count
                FROM platform.page_inspection i
                JOIN platform.collection_run r ON r.id=i.run_id
                JOIN platform.source_document d ON d.id=i.source_document_id
                JOIN platform.source_analysis_profile p ON p.id=i.profile_id
                LEFT JOIN platform.page_inspection_finding f ON f.inspection_id=i.id
                WHERE i.project_id=%s
                  AND (%s::text IS NULL OR r.pub_id=%s)
                  AND (%s::text IS NULL OR i.pub_id < %s)
                GROUP BY i.id,r.pub_id,d.id,p.id
                ORDER BY i.pub_id DESC
                LIMIT %s
                """,
                (project_id, run_pub_id, run_pub_id, cursor, cursor, limit + 1),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_inspection(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        inspection_pub_id: str,
    ) -> dict[str, Any]:
        with self._tenant_conn(tenant_pub_id) as (connection, _tenant_id):
            project_id = self._project_id(connection, project_pub_id)
            row = connection.execute(
                """
                SELECT i.*,r.pub_id AS run_pub_id,d.pub_id AS source_document_pub_id,
                       d.url,d.host,d.page_title,d.site_name,d.publisher,d.authors,
                       d.published_at,d.published_at_confidence,
                       p.pub_id AS profile_pub_id,p.revision AS profile_revision,
                       p.object_name,p.object_kind,p.categories,p.aliases,p.own_domains,p.peers,
                       p.anchor_sources,p.linked_entities,p.hard_anchor_available,
                       p.decision_mode,p.profile_type,p.profile_hash
                FROM platform.page_inspection i
                JOIN platform.collection_run r ON r.id=i.run_id
                JOIN platform.source_document d ON d.id=i.source_document_id
                JOIN platform.source_analysis_profile p ON p.id=i.profile_id
                WHERE i.project_id=%s AND i.pub_id=%s
                """,
                (project_id, inspection_pub_id),
            ).fetchone()
            if row is None:
                raise SourceAnalysisNotFound("inspection not found")
            findings = connection.execute(
                """
                SELECT f.pub_id,f.ordinal,f.code,f.ledger,f.variant,f.finding_status,
                       f.summary,f.action,f.evidence_chain,f.self_check,f.validation,
                       COALESCE(jsonb_agg(
                         jsonb_build_object(
                           'pub_id',s.pub_id,'chain_ordinal',s.chain_ordinal,'quote',s.quote,
                           'text_start',s.text_start,'text_end',s.text_end,
                           'quote_hash',s.quote_hash,'verification',s.verification
                         ) ORDER BY s.chain_ordinal
                       ) FILTER (WHERE s.id IS NOT NULL),'[]'::jsonb) AS spans
                FROM platform.page_inspection_finding f
                LEFT JOIN platform.page_evidence_span s ON s.finding_id=f.id
                WHERE f.inspection_id=%s
                GROUP BY f.id
                ORDER BY f.ordinal
                """,
                (row["id"],),
            ).fetchall()
            public = {
                key: value
                for key, value in row.items()
                if key
                not in {
                    "id",
                    "tenant_id",
                    "project_id",
                    "run_id",
                    "source_document_id",
                    "profile_id",
                }
            }
            public["findings"] = [dict(finding) for finding in findings]
            return public


__all__ = [
    "SourceAnalysisInvalid",
    "SourceAnalysisNotFound",
    "SourceAnalysisNotReady",
    "SourceAnalysisService",
    "derive_inspection_policy_version",
    "derive_profile_pub_id",
]
