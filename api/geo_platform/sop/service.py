from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from hashlib import sha256
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..tenancy.ids import new_pub_id
from ..tenancy.psycopg import tenant_connection


class SopNotFound(LookupError):
    """The requested sop resource does not exist inside the tenant scope."""


class SopInvalidState(RuntimeError):
    """The requested transition or write violates the sop workflow state machine."""


def _public(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "id"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


class SopService:
    def __init__(self, *, dsn: str) -> None:
        self.dsn = dsn

    @contextmanager
    def _conn(self, tenant_pub_id: str) -> Iterator[psycopg.Connection[Any]]:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            yield connection

    @staticmethod
    def _require(row: Mapping[str, Any] | None) -> Mapping[str, Any]:
        if row is None:
            raise SopNotFound("sop resource not found")
        return row

    def _fetch_required(
        self,
        connection: psycopg.Connection[Any],
        sql: str,
        params: tuple[Any, ...],
    ) -> Mapping[str, Any]:
        return self._require(connection.execute(sql, params).fetchone())

    def _require_project(
        self, connection: psycopg.Connection[Any], tenant_pub_id: str, project_pub_id: str
    ) -> Mapping[str, Any]:
        return self._fetch_required(
            connection,
            "SELECT * FROM sop.project WHERE tenant_pub_id=%s AND pub_id=%s",
            (tenant_pub_id, project_pub_id),
        )

    # -- Stage 0: projects --------------------------------------------------

    def create_project(
        self,
        *,
        tenant_pub_id: str,
        name: str,
        brand_standard_name: str,
        brand_profile: Mapping[str, Any],
        target_platforms: Sequence[Any],
        success_definition: Sequence[Any],
        created_by_pub_id: str,
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            row = connection.execute(
                """
                INSERT INTO sop.project
                  (pub_id,tenant_pub_id,name,brand_standard_name,brand_profile,
                   target_platforms,success_definition,created_by_pub_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING *
                """,
                (
                    new_pub_id("spr"),
                    tenant_pub_id,
                    name,
                    brand_standard_name,
                    _json(brand_profile),
                    _json(list(target_platforms)),
                    _json(list(success_definition)),
                    created_by_pub_id,
                ),
            ).fetchone()
        return _public(self._require(row))

    def list_projects(
        self,
        *,
        tenant_pub_id: str,
        status: str | None,
        cursor: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._conn(tenant_pub_id) as connection:
            rows = connection.execute(
                """
                SELECT * FROM sop.project
                WHERE tenant_pub_id=%s
                  AND (%s::text IS NULL OR status=%s)
                  AND (%s::text IS NULL OR pub_id>%s)
                ORDER BY pub_id LIMIT %s
                """,
                (tenant_pub_id, status, status, cursor, cursor, limit + 1),
            ).fetchall()
        return [_public(row) for row in rows]

    def get_project(self, *, tenant_pub_id: str, project_pub_id: str) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            row = self._require_project(connection, tenant_pub_id, project_pub_id)
        return _public(row)

    def update_project(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        jsonb_fields = {"brand_profile", "target_platforms", "success_definition"}
        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            sets.append(f"{key}=%s")
            params.append(_json(value) if key in jsonb_fields else value)
        with self._conn(tenant_pub_id) as connection:
            self._require_project(connection, tenant_pub_id, project_pub_id)
            if not sets:
                existing = self._require_project(connection, tenant_pub_id, project_pub_id)
                return _public(existing)
            sets.append("updated_at=now()")
            row = connection.execute(
                f"UPDATE sop.project SET {', '.join(sets)}"
                " WHERE tenant_pub_id=%s AND pub_id=%s RETURNING *",
                (*params, tenant_pub_id, project_pub_id),
            ).fetchone()
        return _public(self._require(row))

    # -- Stage 1: query sets and items --------------------------------------

    def create_query_set(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        note: str,
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            self._require_project(connection, tenant_pub_id, project_pub_id)
            row = connection.execute(
                """
                INSERT INTO sop.query_set (pub_id,tenant_pub_id,project_pub_id,version_no,note)
                VALUES (
                  %s,%s,%s,
                  (SELECT COALESCE(max(version_no),0)+1 FROM sop.query_set
                   WHERE tenant_pub_id=%s AND project_pub_id=%s),
                  %s
                )
                RETURNING *
                """,
                (
                    new_pub_id("sqs"),
                    tenant_pub_id,
                    project_pub_id,
                    tenant_pub_id,
                    project_pub_id,
                    note,
                ),
            ).fetchone()
        return _public(self._require(row))

    def list_query_sets(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        cursor: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._conn(tenant_pub_id) as connection:
            self._require_project(connection, tenant_pub_id, project_pub_id)
            rows = connection.execute(
                """
                SELECT qs.*,
                       (SELECT count(*) FROM sop.query_item qi
                        WHERE qi.tenant_pub_id=qs.tenant_pub_id
                          AND qi.query_set_pub_id=qs.pub_id) AS item_count
                FROM sop.query_set qs
                WHERE qs.tenant_pub_id=%s AND qs.project_pub_id=%s
                  AND (%s::text IS NULL OR qs.pub_id>%s)
                ORDER BY qs.pub_id LIMIT %s
                """,
                (tenant_pub_id, project_pub_id, cursor, cursor, limit + 1),
            ).fetchall()
        return [_public(row) for row in rows]

    def _require_query_set(
        self,
        connection: psycopg.Connection[Any],
        tenant_pub_id: str,
        query_set_pub_id: str,
        *,
        for_update: bool = False,
    ) -> Mapping[str, Any]:
        suffix = " FOR UPDATE" if for_update else ""
        return self._fetch_required(
            connection,
            f"SELECT * FROM sop.query_set WHERE tenant_pub_id=%s AND pub_id=%s{suffix}",
            (tenant_pub_id, query_set_pub_id),
        )

    def _require_query_set_in_project(
        self,
        connection: psycopg.Connection[Any],
        tenant_pub_id: str,
        query_set_pub_id: str,
        project_pub_id: str,
    ) -> Mapping[str, Any]:
        query_set = self._require_query_set(connection, tenant_pub_id, query_set_pub_id)
        if query_set["project_pub_id"] != project_pub_id:
            raise SopNotFound("query set not found in project")
        return query_set

    def add_query_items(
        self,
        *,
        tenant_pub_id: str,
        query_set_pub_id: str,
        items: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        with self._conn(tenant_pub_id) as connection:
            query_set = self._require_query_set(
                connection, tenant_pub_id, query_set_pub_id, for_update=True
            )
            if query_set["status"] != "draft":
                raise SopInvalidState("query set is not writable unless draft")
            base = connection.execute(
                """
                SELECT COALESCE(max(ordinal),0) AS max_ordinal FROM sop.query_item
                WHERE tenant_pub_id=%s AND query_set_pub_id=%s
                """,
                (tenant_pub_id, query_set_pub_id),
            ).fetchone()
            assert base is not None
            ordinal = int(base["max_ordinal"])
            inserted: list[dict[str, Any]] = []
            for item in items:
                ordinal += 1
                row = connection.execute(
                    """
                    INSERT INTO sop.query_item
                      (pub_id,tenant_pub_id,query_set_pub_id,ordinal,query_text,layer,
                       contains_brand,intent,persona,decision_stage,expected_facts,priority)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING *
                    """,
                    (
                        new_pub_id("sqi"),
                        tenant_pub_id,
                        query_set_pub_id,
                        ordinal,
                        item["query_text"],
                        item["layer"],
                        item.get("contains_brand", False),
                        item.get("intent", ""),
                        item.get("persona", ""),
                        item.get("decision_stage", ""),
                        item.get("expected_facts", ""),
                        item.get("priority", "P1"),
                    ),
                ).fetchone()
                inserted.append(_public(self._require(row)))
        return inserted

    def freeze_query_set(
        self,
        *,
        tenant_pub_id: str,
        query_set_pub_id: str,
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            query_set = self._require_query_set(
                connection, tenant_pub_id, query_set_pub_id, for_update=True
            )
            if query_set["status"] == "frozen":
                return _public(query_set)
            if query_set["status"] != "draft":
                raise SopInvalidState("only a draft query set can be frozen")
            connection.execute(
                """
                UPDATE sop.query_set SET status='superseded'
                WHERE tenant_pub_id=%s AND project_pub_id=%s AND status='frozen'
                """,
                (tenant_pub_id, query_set["project_pub_id"]),
            )
            row = connection.execute(
                """
                UPDATE sop.query_set SET status='frozen',frozen_at=now()
                WHERE tenant_pub_id=%s AND pub_id=%s
                RETURNING *
                """,
                (tenant_pub_id, query_set_pub_id),
            ).fetchone()
        return _public(self._require(row))

    def list_query_items(
        self,
        *,
        tenant_pub_id: str,
        query_set_pub_id: str,
        cursor: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._conn(tenant_pub_id) as connection:
            self._require_query_set(connection, tenant_pub_id, query_set_pub_id)
            rows = connection.execute(
                """
                SELECT * FROM sop.query_item
                WHERE tenant_pub_id=%s AND query_set_pub_id=%s
                  AND (%s::text IS NULL OR pub_id>%s)
                ORDER BY pub_id LIMIT %s
                """,
                (tenant_pub_id, query_set_pub_id, cursor, cursor, limit + 1),
            ).fetchall()
        return [_public(row) for row in rows]

    # -- Stage 2: baseline answers ------------------------------------------

    def _query_item_project(
        self,
        connection: psycopg.Connection[Any],
        tenant_pub_id: str,
        query_item_pub_id: str,
    ) -> str | None:
        row = connection.execute(
            """
            SELECT qs.project_pub_id
            FROM sop.query_item qi
            JOIN sop.query_set qs
              ON qs.tenant_pub_id=qi.tenant_pub_id AND qs.pub_id=qi.query_set_pub_id
            WHERE qi.tenant_pub_id=%s AND qi.pub_id=%s
            """,
            (tenant_pub_id, query_item_pub_id),
        ).fetchone()
        if row is None:
            return None
        return str(row["project_pub_id"])

    def create_baseline_answer(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        jsonb_fields = {"search_terms", "search_results", "citations", "key_facts"}
        columns = ["project_pub_id"]
        values: list[Any] = [project_pub_id]
        for key, value in fields.items():
            columns.append(key)
            values.append(_json(value) if key in jsonb_fields else value)
        placeholders = ",".join(["%s"] * len(columns))
        with self._conn(tenant_pub_id) as connection:
            self._require_project(connection, tenant_pub_id, project_pub_id)
            item_project = self._query_item_project(
                connection, tenant_pub_id, str(fields["query_item_pub_id"])
            )
            if item_project != project_pub_id:
                raise SopNotFound("query item does not belong to the project")
            try:
                row = connection.execute(
                    f"""
                    INSERT INTO sop.baseline_answer (pub_id,tenant_pub_id,{",".join(columns)})
                    VALUES (%s,%s,{placeholders})
                    RETURNING *
                    """,
                    (new_pub_id("sbl"), tenant_pub_id, *values),
                ).fetchone()
            except psycopg.errors.UniqueViolation as exc:
                raise SopInvalidState(
                    "baseline answer already exists for (query_item, sample_index)"
                ) from exc
        return _public(self._require(row))

    def list_baseline_answers(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        query_item_pub_id: str | None,
        platform: str | None,
        capture_status: str | None,
        cursor: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._conn(tenant_pub_id) as connection:
            self._require_project(connection, tenant_pub_id, project_pub_id)
            rows = connection.execute(
                """
                SELECT * FROM sop.baseline_answer
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                  AND (%s::text IS NULL OR query_item_pub_id=%s)
                  AND (%s::text IS NULL OR platform=%s)
                  AND (%s::text IS NULL OR capture_status=%s)
                  AND (%s::text IS NULL OR pub_id>%s)
                ORDER BY pub_id LIMIT %s
                """,
                (
                    tenant_pub_id,
                    project_pub_id,
                    query_item_pub_id,
                    query_item_pub_id,
                    platform,
                    platform,
                    capture_status,
                    capture_status,
                    cursor,
                    cursor,
                    limit + 1,
                ),
            ).fetchall()
        return [_public(row) for row in rows]

    # -- Stage 3: retrieval insights -----------------------------------------

    def create_insight(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        insight_type: str,
        payload: Mapping[str, Any],
        note: str,
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            self._require_project(connection, tenant_pub_id, project_pub_id)
            row = connection.execute(
                """
                INSERT INTO sop.retrieval_insight
                  (pub_id,tenant_pub_id,project_pub_id,insight_type,payload,note)
                VALUES (%s,%s,%s,%s,%s,%s)
                RETURNING *
                """,
                (
                    new_pub_id("sis"),
                    tenant_pub_id,
                    project_pub_id,
                    insight_type,
                    _json(payload),
                    note,
                ),
            ).fetchone()
        return _public(self._require(row))

    def list_insights(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        cursor: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._conn(tenant_pub_id) as connection:
            self._require_project(connection, tenant_pub_id, project_pub_id)
            rows = connection.execute(
                """
                SELECT * FROM sop.retrieval_insight
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                  AND (%s::text IS NULL OR pub_id>%s)
                ORDER BY pub_id LIMIT %s
                """,
                (tenant_pub_id, project_pub_id, cursor, cursor, limit + 1),
            ).fetchall()
        return [_public(row) for row in rows]

    # -- generic helpers -----------------------------------------------------

    def _insert(
        self,
        connection: psycopg.Connection[Any],
        *,
        table: str,
        prefix: str,
        tenant_pub_id: str,
        fields: Mapping[str, Any],
        jsonb_fields: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        columns = list(fields)
        placeholders = ",".join(["%s"] * len(columns))
        values = [_json(value) if key in jsonb_fields else value for key, value in fields.items()]
        row = connection.execute(
            f"INSERT INTO sop.{table} (pub_id,tenant_pub_id,{','.join(columns)})"
            f" VALUES (%s,%s,{placeholders}) RETURNING *",
            (new_pub_id(prefix), tenant_pub_id, *values),
        ).fetchone()
        return _public(self._require(row))

    def _update(
        self,
        connection: psycopg.Connection[Any],
        *,
        table: str,
        tenant_pub_id: str,
        pub_id: str,
        fields: Mapping[str, Any],
        jsonb_fields: frozenset[str] = frozenset(),
        touch_updated_at: bool = True,
    ) -> dict[str, Any]:
        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            sets.append(f"{key}=%s")
            params.append(_json(value) if key in jsonb_fields else value)
        if not sets:
            row = connection.execute(
                f"SELECT * FROM sop.{table} WHERE tenant_pub_id=%s AND pub_id=%s",
                (tenant_pub_id, pub_id),
            ).fetchone()
            return _public(self._require(row))
        if touch_updated_at:
            sets.append("updated_at=now()")
        row = connection.execute(
            f"UPDATE sop.{table} SET {', '.join(sets)}"
            " WHERE tenant_pub_id=%s AND pub_id=%s RETURNING *",
            (*params, tenant_pub_id, pub_id),
        ).fetchone()
        return _public(self._require(row))

    # -- Stage 4: evidence ledger --------------------------------------------

    def create_evidence(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            self._require_project(connection, tenant_pub_id, project_pub_id)
            return self._insert(
                connection,
                table="evidence_item",
                prefix="sev",
                tenant_pub_id=tenant_pub_id,
                fields={"project_pub_id": project_pub_id, **fields},
            )

    def list_evidence(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        source_level: str | None,
        cursor: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._conn(tenant_pub_id) as connection:
            self._require_project(connection, tenant_pub_id, project_pub_id)
            rows = connection.execute(
                """
                SELECT * FROM sop.evidence_item
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                  AND (%s::text IS NULL OR source_level=%s)
                  AND (%s::text IS NULL OR pub_id>%s)
                ORDER BY pub_id LIMIT %s
                """,
                (
                    tenant_pub_id,
                    project_pub_id,
                    source_level,
                    source_level,
                    cursor,
                    cursor,
                    limit + 1,
                ),
            ).fetchall()
        return [_public(row) for row in rows]

    def update_evidence(
        self,
        *,
        tenant_pub_id: str,
        evidence_pub_id: str,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            self._fetch_required(
                connection,
                "SELECT pub_id FROM sop.evidence_item WHERE tenant_pub_id=%s AND pub_id=%s",
                (tenant_pub_id, evidence_pub_id),
            )
            return self._update(
                connection,
                table="evidence_item",
                tenant_pub_id=tenant_pub_id,
                pub_id=evidence_pub_id,
                fields=fields,
            )

    # -- Stages 5-6: opportunities --------------------------------------------

    def create_opportunity(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            self._require_project(connection, tenant_pub_id, project_pub_id)
            return self._insert(
                connection,
                table="opportunity",
                prefix="sop",
                tenant_pub_id=tenant_pub_id,
                fields={"project_pub_id": project_pub_id, **fields},
                jsonb_fields=frozenset({"current_sources"}),
            )

    def list_opportunities(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        status: str | None,
        cursor: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._conn(tenant_pub_id) as connection:
            self._require_project(connection, tenant_pub_id, project_pub_id)
            rows = connection.execute(
                """
                SELECT * FROM sop.opportunity
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                  AND (%s::text IS NULL OR status=%s)
                  AND (%s::text IS NULL OR pub_id>%s)
                ORDER BY pub_id LIMIT %s
                """,
                (tenant_pub_id, project_pub_id, status, status, cursor, cursor, limit + 1),
            ).fetchall()
        return [_public(row) for row in rows]

    def update_opportunity(
        self,
        *,
        tenant_pub_id: str,
        opportunity_pub_id: str,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            self._fetch_required(
                connection,
                "SELECT pub_id FROM sop.opportunity WHERE tenant_pub_id=%s AND pub_id=%s",
                (tenant_pub_id, opportunity_pub_id),
            )
            return self._update(
                connection,
                table="opportunity",
                tenant_pub_id=tenant_pub_id,
                pub_id=opportunity_pub_id,
                fields=fields,
                jsonb_fields=frozenset({"current_sources"}),
            )

    # -- Stage 7: articles and versions ---------------------------------------

    def create_article(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        title: str,
        opportunity_pub_id: str | None,
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            self._require_project(connection, tenant_pub_id, project_pub_id)
            if opportunity_pub_id is not None:
                opportunity = connection.execute(
                    """
                    SELECT pub_id FROM sop.opportunity
                    WHERE tenant_pub_id=%s AND pub_id=%s AND project_pub_id=%s
                    """,
                    (tenant_pub_id, opportunity_pub_id, project_pub_id),
                ).fetchone()
                if opportunity is None:
                    raise SopNotFound("opportunity not found in project")
            return self._insert(
                connection,
                table="article",
                prefix="sar",
                tenant_pub_id=tenant_pub_id,
                fields={
                    "project_pub_id": project_pub_id,
                    "opportunity_pub_id": opportunity_pub_id,
                    "title": title,
                },
            )

    def _article_maturity(
        self,
        connection: psycopg.Connection[Any],
        tenant_pub_id: str,
        article_pub_id: str,
    ) -> str:
        row = self._require(
            connection.execute(
                """
                SELECT
                  EXISTS(
                    SELECT 1 FROM sop.comparison c
                    JOIN sop.publication p
                      ON p.tenant_pub_id=c.tenant_pub_id AND p.pub_id=c.publication_pub_id
                    JOIN sop.article_version av
                      ON av.tenant_pub_id=p.tenant_pub_id
                     AND av.pub_id=p.article_version_pub_id
                    WHERE c.tenant_pub_id=%s AND av.article_pub_id=%s
                      AND c.from_article_confidence IN ('medium','high')
                      AND c.attribution_correct IS TRUE
                  ) AS l4,
                  EXISTS(
                    SELECT 1 FROM sop.retest_answer r
                    JOIN sop.publication p
                      ON p.tenant_pub_id=r.tenant_pub_id AND p.pub_id=r.publication_pub_id
                    JOIN sop.article_version av
                      ON av.tenant_pub_id=p.tenant_pub_id
                     AND av.pub_id=p.article_version_pub_id
                    WHERE r.tenant_pub_id=%s AND av.article_pub_id=%s
                      AND r.article_cited IS TRUE
                  ) AS l3,
                  EXISTS(
                    SELECT 1 FROM sop.retest_answer r
                    JOIN sop.publication p
                      ON p.tenant_pub_id=r.tenant_pub_id AND p.pub_id=r.publication_pub_id
                    JOIN sop.article_version av
                      ON av.tenant_pub_id=p.tenant_pub_id
                     AND av.pub_id=p.article_version_pub_id
                    WHERE r.tenant_pub_id=%s AND av.article_pub_id=%s
                      AND r.article_appeared IS TRUE
                  ) AS l2,
                  EXISTS(
                    SELECT 1 FROM sop.publication p
                    JOIN sop.article_version av
                      ON av.tenant_pub_id=p.tenant_pub_id
                     AND av.pub_id=p.article_version_pub_id
                    WHERE p.tenant_pub_id=%s AND av.article_pub_id=%s AND p.status='public'
                  ) AS l1
                """,
                (
                    tenant_pub_id,
                    article_pub_id,
                    tenant_pub_id,
                    article_pub_id,
                    tenant_pub_id,
                    article_pub_id,
                    tenant_pub_id,
                    article_pub_id,
                ),
            ).fetchone()
        )
        for level, flag in (("L4", "l4"), ("L3", "l3"), ("L2", "l2"), ("L1", "l1")):
            if row[flag]:
                return level
        return "L0"

    def list_articles(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        cursor: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._conn(tenant_pub_id) as connection:
            self._require_project(connection, tenant_pub_id, project_pub_id)
            rows = connection.execute(
                """
                SELECT a.*,
                       (SELECT count(*) FROM sop.article_version av
                        WHERE av.tenant_pub_id=a.tenant_pub_id
                          AND av.article_pub_id=a.pub_id) AS version_count,
                       (SELECT max(av.version_no) FROM sop.article_version av
                        WHERE av.tenant_pub_id=a.tenant_pub_id
                          AND av.article_pub_id=a.pub_id) AS latest_version_no
                FROM sop.article a
                WHERE a.tenant_pub_id=%s AND a.project_pub_id=%s
                  AND (%s::text IS NULL OR a.pub_id>%s)
                ORDER BY a.pub_id LIMIT %s
                """,
                (tenant_pub_id, project_pub_id, cursor, cursor, limit + 1),
            ).fetchall()
            return [
                {
                    **_public(row),
                    "maturity_level": self._article_maturity(
                        connection, tenant_pub_id, str(row["pub_id"])
                    ),
                }
                for row in rows
            ]

    def get_article(self, *, tenant_pub_id: str, article_pub_id: str) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            article = self._fetch_required(
                connection,
                "SELECT * FROM sop.article WHERE tenant_pub_id=%s AND pub_id=%s",
                (tenant_pub_id, article_pub_id),
            )
            versions = connection.execute(
                """
                SELECT av.pub_id,av.tenant_pub_id,av.article_pub_id,av.version_no,av.title,
                       av.body_sha256,av.change_note,av.readiness_checklist,av.publication_ready,
                       av.created_at,
                       (SELECT count(*) FROM sop.pre_publish_check c
                        WHERE c.tenant_pub_id=av.tenant_pub_id
                          AND c.article_version_pub_id=av.pub_id) AS check_count
                FROM sop.article_version av
                WHERE av.tenant_pub_id=%s AND av.article_pub_id=%s
                ORDER BY av.version_no
                """,
                (tenant_pub_id, article_pub_id),
            ).fetchall()
            maturity = self._article_maturity(connection, tenant_pub_id, article_pub_id)
        return {
            **_public(article),
            "maturity_level": maturity,
            "versions": [_public(row) for row in versions],
        }

    def update_article(
        self,
        *,
        tenant_pub_id: str,
        article_pub_id: str,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            self._fetch_required(
                connection,
                "SELECT pub_id FROM sop.article WHERE tenant_pub_id=%s AND pub_id=%s",
                (tenant_pub_id, article_pub_id),
            )
            return self._update(
                connection,
                table="article",
                tenant_pub_id=tenant_pub_id,
                pub_id=article_pub_id,
                fields=fields,
            )

    def create_article_version(
        self,
        *,
        tenant_pub_id: str,
        article_pub_id: str,
        title: str,
        body: str,
        change_note: str,
    ) -> dict[str, Any]:
        body_sha256 = sha256(body.encode("utf-8")).hexdigest()
        with self._conn(tenant_pub_id) as connection:
            article = self._fetch_required(
                connection,
                "SELECT * FROM sop.article WHERE tenant_pub_id=%s AND pub_id=%s FOR UPDATE",
                (tenant_pub_id, article_pub_id),
            )
            row = connection.execute(
                """
                INSERT INTO sop.article_version
                  (pub_id,tenant_pub_id,article_pub_id,version_no,title,body,body_sha256,
                   change_note)
                VALUES (
                  %s,%s,%s,
                  (SELECT COALESCE(max(version_no),0)+1 FROM sop.article_version
                   WHERE tenant_pub_id=%s AND article_pub_id=%s),
                  %s,%s,%s,%s
                )
                RETURNING *
                """,
                (
                    new_pub_id("sav"),
                    tenant_pub_id,
                    article_pub_id,
                    tenant_pub_id,
                    article_pub_id,
                    title,
                    body,
                    body_sha256,
                    change_note,
                ),
            ).fetchone()
            if article["status"] == "draft":
                connection.execute(
                    """
                    UPDATE sop.article SET status='in_review',updated_at=now()
                    WHERE tenant_pub_id=%s AND pub_id=%s
                    """,
                    (tenant_pub_id, article_pub_id),
                )
        return _public(self._require(row))

    def get_article_version(self, *, tenant_pub_id: str, version_pub_id: str) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            version = self._fetch_required(
                connection,
                "SELECT * FROM sop.article_version WHERE tenant_pub_id=%s AND pub_id=%s",
                (tenant_pub_id, version_pub_id),
            )
            checks = connection.execute(
                """
                SELECT * FROM sop.pre_publish_check
                WHERE tenant_pub_id=%s AND article_version_pub_id=%s
                ORDER BY pub_id
                """,
                (tenant_pub_id, version_pub_id),
            ).fetchall()
        return {**_public(version), "checks": [_public(row) for row in checks]}

    def update_article_version(
        self,
        *,
        tenant_pub_id: str,
        version_pub_id: str,
        readiness_checklist: Mapping[str, Any] | None,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            version = self._fetch_required(
                connection,
                "SELECT * FROM sop.article_version WHERE tenant_pub_id=%s AND pub_id=%s FOR UPDATE",
                (tenant_pub_id, version_pub_id),
            )
            updates = dict(fields)
            if readiness_checklist is not None:
                current = version["readiness_checklist"]
                merged = {**dict(current or {}), **dict(readiness_checklist)}
                updates["readiness_checklist"] = merged
            return self._update(
                connection,
                table="article_version",
                tenant_pub_id=tenant_pub_id,
                pub_id=version_pub_id,
                fields=updates,
                jsonb_fields=frozenset({"readiness_checklist"}),
                touch_updated_at=False,
            )

    # -- Stage 8: pre-publish checks ------------------------------------------

    def create_check(
        self,
        *,
        tenant_pub_id: str,
        version_pub_id: str,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            self._fetch_required(
                connection,
                "SELECT pub_id FROM sop.article_version WHERE tenant_pub_id=%s AND pub_id=%s",
                (tenant_pub_id, version_pub_id),
            )
            return self._insert(
                connection,
                table="pre_publish_check",
                prefix="spc",
                tenant_pub_id=tenant_pub_id,
                fields={"article_version_pub_id": version_pub_id, **fields},
            )

    def list_checks(
        self,
        *,
        tenant_pub_id: str,
        version_pub_id: str,
        cursor: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._conn(tenant_pub_id) as connection:
            self._fetch_required(
                connection,
                "SELECT pub_id FROM sop.article_version WHERE tenant_pub_id=%s AND pub_id=%s",
                (tenant_pub_id, version_pub_id),
            )
            rows = connection.execute(
                """
                SELECT * FROM sop.pre_publish_check
                WHERE tenant_pub_id=%s AND article_version_pub_id=%s
                  AND (%s::text IS NULL OR pub_id>%s)
                ORDER BY pub_id LIMIT %s
                """,
                (tenant_pub_id, version_pub_id, cursor, cursor, limit + 1),
            ).fetchall()
        return [_public(row) for row in rows]

    # -- Stage 9: publications -------------------------------------------------

    _PUBLICATION_TRANSITIONS: dict[str, frozenset[str]] = {
        "submitted": frozenset({"reviewing", "rejected", "withdrawn"}),
        "reviewing": frozenset({"published", "rejected", "withdrawn"}),
        "published": frozenset({"public", "login_only", "rejected", "withdrawn"}),
        "login_only": frozenset({"published", "rejected", "withdrawn"}),
        "public": frozenset(),
        "rejected": frozenset(),
        "withdrawn": frozenset(),
    }
    _PUBLIC_MUTABLE_FIELDS = frozenset(
        {"evidence", "note", "public_checked_at", "public_http_status"}
    )

    def create_publication(
        self,
        *,
        tenant_pub_id: str,
        version_pub_id: str,
        platform: str,
        account_label: str,
        submitted_at: Any,
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            version = self._fetch_required(
                connection,
                """
                SELECT av.*, a.project_pub_id, a.pub_id AS article_pub_id
                FROM sop.article_version av
                JOIN sop.article a
                  ON a.tenant_pub_id=av.tenant_pub_id AND a.pub_id=av.article_pub_id
                WHERE av.tenant_pub_id=%s AND av.pub_id=%s
                FOR UPDATE
                """,
                (tenant_pub_id, version_pub_id),
            )
            if not version["publication_ready"]:
                raise SopInvalidState("article version is not publication ready")
            row = self._insert(
                connection,
                table="publication",
                prefix="spb",
                tenant_pub_id=tenant_pub_id,
                fields={
                    "project_pub_id": version["project_pub_id"],
                    "article_version_pub_id": version_pub_id,
                    "platform": platform,
                    "account_label": account_label,
                    "title": version["title"],
                    "body_sha256": version["body_sha256"],
                    "submitted_at": submitted_at,
                },
            )
            connection.execute(
                """
                UPDATE sop.article SET status='published',updated_at=now()
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (tenant_pub_id, version["article_pub_id"]),
            )
        return row

    def list_publications(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        status: str | None,
        platform: str | None,
        cursor: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._conn(tenant_pub_id) as connection:
            self._require_project(connection, tenant_pub_id, project_pub_id)
            rows = connection.execute(
                """
                SELECT * FROM sop.publication
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                  AND (%s::text IS NULL OR status=%s)
                  AND (%s::text IS NULL OR platform=%s)
                  AND (%s::text IS NULL OR pub_id>%s)
                ORDER BY pub_id LIMIT %s
                """,
                (
                    tenant_pub_id,
                    project_pub_id,
                    status,
                    status,
                    platform,
                    platform,
                    cursor,
                    cursor,
                    limit + 1,
                ),
            ).fetchall()
        return [_public(row) for row in rows]

    def _require_publication(
        self,
        connection: psycopg.Connection[Any],
        tenant_pub_id: str,
        publication_pub_id: str,
        *,
        for_update: bool = False,
    ) -> Mapping[str, Any]:
        suffix = " FOR UPDATE" if for_update else ""
        return self._fetch_required(
            connection,
            f"SELECT * FROM sop.publication WHERE tenant_pub_id=%s AND pub_id=%s{suffix}",
            (tenant_pub_id, publication_pub_id),
        )

    def get_publication(self, *, tenant_pub_id: str, publication_pub_id: str) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            publication = self._require_publication(connection, tenant_pub_id, publication_pub_id)
            observations = connection.execute(
                """
                SELECT * FROM sop.index_observation
                WHERE tenant_pub_id=%s AND publication_pub_id=%s
                ORDER BY observed_at,pub_id
                """,
                (tenant_pub_id, publication_pub_id),
            ).fetchall()
            retest_count = connection.execute(
                """
                SELECT count(*) AS count FROM sop.retest_answer
                WHERE tenant_pub_id=%s AND publication_pub_id=%s
                """,
                (tenant_pub_id, publication_pub_id),
            ).fetchone()
            comparison_count = connection.execute(
                """
                SELECT count(*) AS count FROM sop.comparison
                WHERE tenant_pub_id=%s AND publication_pub_id=%s
                """,
                (tenant_pub_id, publication_pub_id),
            ).fetchone()
        return {
            **_public(publication),
            "observations": [_public(row) for row in observations],
            "retest_count": int(self._require(retest_count)["count"]),
            "comparison_count": int(self._require(comparison_count)["count"]),
        }

    def update_publication(
        self,
        *,
        tenant_pub_id: str,
        publication_pub_id: str,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            publication = self._require_publication(
                connection, tenant_pub_id, publication_pub_id, for_update=True
            )
            current = str(publication["status"])
            new_status = fields.get("status")
            if current == "public":
                if new_status is not None and new_status != "public":
                    raise SopInvalidState("public publication is terminal")
                disallowed = set(fields) - self._PUBLIC_MUTABLE_FIELDS - {"status"}
                if disallowed:
                    raise SopInvalidState(
                        "public publication only allows evidence/note/check updates"
                    )
            elif new_status is not None and new_status != current:
                if new_status not in self._PUBLICATION_TRANSITIONS[current]:
                    raise SopInvalidState(
                        f"publication cannot transition {current} -> {new_status}"
                    )
            return self._update(
                connection,
                table="publication",
                tenant_pub_id=tenant_pub_id,
                pub_id=publication_pub_id,
                fields=fields,
                jsonb_fields=frozenset({"evidence"}),
            )

    # -- Stage 10: index observations ------------------------------------------

    def create_observation(
        self,
        *,
        tenant_pub_id: str,
        publication_pub_id: str,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            self._require_publication(connection, tenant_pub_id, publication_pub_id)
            try:
                return self._insert(
                    connection,
                    table="index_observation",
                    prefix="sio",
                    tenant_pub_id=tenant_pub_id,
                    fields={"publication_pub_id": publication_pub_id, **fields},
                )
            except psycopg.errors.UniqueViolation as exc:
                raise SopInvalidState(
                    "observation already exists for (checkpoint, checkpoint_label)"
                ) from exc

    def list_observations(
        self,
        *,
        tenant_pub_id: str,
        publication_pub_id: str,
        cursor: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._conn(tenant_pub_id) as connection:
            self._require_publication(connection, tenant_pub_id, publication_pub_id)
            rows = connection.execute(
                """
                SELECT * FROM sop.index_observation
                WHERE tenant_pub_id=%s AND publication_pub_id=%s
                  AND (%s::text IS NULL OR pub_id>%s)
                ORDER BY pub_id LIMIT %s
                """,
                (tenant_pub_id, publication_pub_id, cursor, cursor, limit + 1),
            ).fetchall()
        return [_public(row) for row in rows]

    # -- Stage 11: retest answers ----------------------------------------------

    def create_retest_answer(
        self,
        *,
        tenant_pub_id: str,
        publication_pub_id: str,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        jsonb_fields = frozenset(
            {"search_terms", "search_results", "citations", "key_facts", "new_facts"}
        )
        with self._conn(tenant_pub_id) as connection:
            publication = self._require_publication(connection, tenant_pub_id, publication_pub_id)
            item_project = self._query_item_project(
                connection, tenant_pub_id, str(fields["query_item_pub_id"])
            )
            if item_project is None or item_project != str(publication["project_pub_id"]):
                raise SopNotFound("query item does not belong to the project")
            try:
                return self._insert(
                    connection,
                    table="retest_answer",
                    prefix="srt",
                    tenant_pub_id=tenant_pub_id,
                    fields={"publication_pub_id": publication_pub_id, **fields},
                    jsonb_fields=jsonb_fields,
                )
            except psycopg.errors.UniqueViolation as exc:
                raise SopInvalidState(
                    "retest answer already exists for (query_item, sample_index)"
                ) from exc

    def list_retest_answers(
        self,
        *,
        tenant_pub_id: str,
        publication_pub_id: str,
        query_item_pub_id: str | None,
        cursor: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._conn(tenant_pub_id) as connection:
            self._require_publication(connection, tenant_pub_id, publication_pub_id)
            rows = connection.execute(
                """
                SELECT * FROM sop.retest_answer
                WHERE tenant_pub_id=%s AND publication_pub_id=%s
                  AND (%s::text IS NULL OR query_item_pub_id=%s)
                  AND (%s::text IS NULL OR pub_id>%s)
                ORDER BY pub_id LIMIT %s
                """,
                (
                    tenant_pub_id,
                    publication_pub_id,
                    query_item_pub_id,
                    query_item_pub_id,
                    cursor,
                    cursor,
                    limit + 1,
                ),
            ).fetchall()
        return [_public(row) for row in rows]

    # -- Stages 12-13: comparisons ----------------------------------------------

    _COMPARISON_WRITABLE = (
        "baseline_answer_pub_id",
        "retest_answer_pub_id",
        "metrics",
        "new_info_location",
        "from_article_confidence",
        "attribution_correct",
        "conclusion",
        "next_actions",
    )

    def _validate_comparison_references(
        self,
        connection: psycopg.Connection[Any],
        *,
        tenant_pub_id: str,
        publication: Mapping[str, Any],
        fields: Mapping[str, Any],
    ) -> None:
        publication_pub_id = str(publication["pub_id"])
        project_pub_id = str(publication["project_pub_id"])
        query_item_pub_id = str(fields["query_item_pub_id"])
        item_project = self._query_item_project(
            connection,
            tenant_pub_id,
            query_item_pub_id,
        )
        if item_project != project_pub_id:
            raise SopNotFound("query item does not belong to the publication project")

        baseline_answer_pub_id = fields.get("baseline_answer_pub_id")
        if baseline_answer_pub_id is not None:
            baseline = connection.execute(
                """
                SELECT project_pub_id,query_item_pub_id
                FROM sop.baseline_answer
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (tenant_pub_id, baseline_answer_pub_id),
            ).fetchone()
            if (
                baseline is None
                or baseline["project_pub_id"] != project_pub_id
                or baseline["query_item_pub_id"] != query_item_pub_id
            ):
                raise SopNotFound(
                    "baseline answer does not belong to the publication project and query"
                )

        retest_answer_pub_id = fields.get("retest_answer_pub_id")
        if retest_answer_pub_id is not None:
            retest = connection.execute(
                """
                SELECT publication_pub_id,query_item_pub_id
                FROM sop.retest_answer
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (tenant_pub_id, retest_answer_pub_id),
            ).fetchone()
            if (
                retest is None
                or retest["publication_pub_id"] != publication_pub_id
                or retest["query_item_pub_id"] != query_item_pub_id
            ):
                raise SopNotFound("retest answer does not belong to the publication and query")

    def upsert_comparison(
        self,
        *,
        tenant_pub_id: str,
        publication_pub_id: str,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        columns = ["publication_pub_id", *fields]
        placeholders = ",".join(["%s"] * len(columns))
        values = [
            _json(value) if key in {"metrics", "next_actions"} else value
            for key, value in fields.items()
        ]
        update_sets = ", ".join(
            f"{key}=EXCLUDED.{key}"
            for key in fields
            if key in self._COMPARISON_WRITABLE and key != "query_item_pub_id"
        )
        with self._conn(tenant_pub_id) as connection:
            publication = self._require_publication(
                connection,
                tenant_pub_id,
                publication_pub_id,
            )
            self._validate_comparison_references(
                connection,
                tenant_pub_id=tenant_pub_id,
                publication=publication,
                fields=fields,
            )
            row = connection.execute(
                f"""
                INSERT INTO sop.comparison (pub_id,tenant_pub_id,{",".join(columns)})
                VALUES (%s,%s,{placeholders})
                ON CONFLICT (tenant_pub_id, publication_pub_id, query_item_pub_id)
                DO UPDATE SET {update_sets}, updated_at=now()
                RETURNING *
                """,
                (new_pub_id("scm"), tenant_pub_id, publication_pub_id, *values),
            ).fetchone()
        return _public(self._require(row))

    def list_comparisons(
        self,
        *,
        tenant_pub_id: str,
        publication_pub_id: str,
        cursor: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._conn(tenant_pub_id) as connection:
            self._require_publication(connection, tenant_pub_id, publication_pub_id)
            rows = connection.execute(
                """
                SELECT * FROM sop.comparison
                WHERE tenant_pub_id=%s AND publication_pub_id=%s
                  AND (%s::text IS NULL OR pub_id>%s)
                ORDER BY pub_id LIMIT %s
                """,
                (tenant_pub_id, publication_pub_id, cursor, cursor, limit + 1),
            ).fetchall()
        return [_public(row) for row in rows]

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float | None:
        if denominator == 0:
            return None
        return numerator / denominator

    @staticmethod
    def _mean(values: Sequence[float]) -> float | None:
        if not values:
            return None
        return sum(values) / len(values)

    def comparison_summary(self, *, tenant_pub_id: str, project_pub_id: str) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            self._require_project(connection, tenant_pub_id, project_pub_id)
            retests = connection.execute(
                """
                SELECT r.* FROM sop.retest_answer r
                JOIN sop.publication p
                  ON p.tenant_pub_id=r.tenant_pub_id AND p.pub_id=r.publication_pub_id
                WHERE r.tenant_pub_id=%s AND p.project_pub_id=%s
                ORDER BY r.created_at,r.pub_id
                """,
                (tenant_pub_id, project_pub_id),
            ).fetchall()
            baselines = connection.execute(
                """
                SELECT * FROM sop.baseline_answer
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                ORDER BY created_at,pub_id
                """,
                (tenant_pub_id, project_pub_id),
            ).fetchall()
            comparisons = connection.execute(
                """
                SELECT c.* FROM sop.comparison c
                JOIN sop.publication p
                  ON p.tenant_pub_id=c.tenant_pub_id AND p.pub_id=c.publication_pub_id
                WHERE c.tenant_pub_id=%s AND p.project_pub_id=%s
                ORDER BY c.created_at,c.pub_id
                """,
                (tenant_pub_id, project_pub_id),
            ).fetchall()
            frozen_set = connection.execute(
                """
                SELECT pub_id FROM sop.query_set
                WHERE tenant_pub_id=%s AND project_pub_id=%s AND status='frozen'
                ORDER BY version_no DESC LIMIT 1
                """,
                (tenant_pub_id, project_pub_id),
            ).fetchone()
            items: list[Mapping[str, Any]] = []
            if frozen_set is not None:
                items = connection.execute(
                    """
                    SELECT * FROM sop.query_item
                    WHERE tenant_pub_id=%s AND query_set_pub_id=%s
                    ORDER BY ordinal
                    """,
                    (tenant_pub_id, frozen_set["pub_id"]),
                ).fetchall()

        success_retests = [row for row in retests if row["capture_status"] == "success"]
        success_baselines = [row for row in baselines if row["capture_status"] == "success"]
        appeared = [row for row in success_retests if row["article_appeared"] is True]
        cited = [row for row in success_retests if row["article_cited"] is True]
        attribution = [
            row for row in success_retests if row["brand_attribution_correct"] is not None
        ]
        per_query: list[dict[str, Any]] = []
        for item in items:
            item_id = str(item["pub_id"])
            item_baselines = [
                row for row in success_baselines if row["query_item_pub_id"] == item_id
            ]
            item_retests = [row for row in success_retests if row["query_item_pub_id"] == item_id]
            item_comparisons = [row for row in comparisons if row["query_item_pub_id"] == item_id]
            latest_baseline = item_baselines[-1] if item_baselines else None
            latest_retest = item_retests[-1] if item_retests else None
            latest_comparison = item_comparisons[-1] if item_comparisons else None
            per_query.append(
                {
                    "query_item_pub_id": item_id,
                    "query_text": item["query_text"],
                    "baseline_mentioned": (
                        latest_baseline["brand_mentioned"] if latest_baseline else None
                    ),
                    "retest_mentioned": (
                        latest_retest["brand_mentioned"] if latest_retest else None
                    ),
                    "article_appeared": (
                        latest_retest["article_appeared"] if latest_retest else None
                    ),
                    "article_cited": (latest_retest["article_cited"] if latest_retest else None),
                    "from_article_confidence": (
                        latest_comparison["from_article_confidence"] if latest_comparison else None
                    ),
                }
            )
        return {
            "project_pub_id": project_pub_id,
            "retrieval": {
                "retests_success": len(success_retests),
                "article_recall_rate": self._rate(len(appeared), len(success_retests)),
                "avg_article_position": self._mean(
                    [
                        float(row["article_position"])
                        for row in appeared
                        if row["article_position"] is not None
                    ]
                ),
            },
            "citation": {
                "citation_rate": self._rate(len(cited), len(success_retests)),
                "avg_citation_position": self._mean(
                    [
                        float(row["citation_position"])
                        for row in cited
                        if row["citation_position"] is not None
                    ]
                ),
            },
            "brand": {
                "baseline_mention_rate": self._rate(
                    len([row for row in success_baselines if row["brand_mentioned"] is True]),
                    len(success_baselines),
                ),
                "retest_mention_rate": self._rate(
                    len([row for row in success_retests if row["brand_mentioned"] is True]),
                    len(success_retests),
                ),
                "attribution_correct_rate": self._rate(
                    len([row for row in attribution if row["brand_attribution_correct"] is True]),
                    len(attribution),
                ),
            },
            "answer": {
                "avg_new_facts": self._mean(
                    [float(len(row["new_facts"] or [])) for row in success_retests]
                ),
                "comparisons": len(comparisons),
                "from_article_medium_or_high": len(
                    [
                        row
                        for row in comparisons
                        if row["from_article_confidence"] in ("medium", "high")
                    ]
                ),
            },
            "per_query": per_query,
        }

    # -- Stage 14: experiments ---------------------------------------------------

    def create_experiment(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            self._require_project(connection, tenant_pub_id, project_pub_id)
            query_set_pub_id = fields.get("query_set_pub_id")
            if query_set_pub_id is not None:
                self._require_query_set_in_project(
                    connection,
                    tenant_pub_id,
                    str(query_set_pub_id),
                    project_pub_id,
                )
            return self._insert(
                connection,
                table="experiment",
                prefix="sex",
                tenant_pub_id=tenant_pub_id,
                fields={"project_pub_id": project_pub_id, **fields},
                jsonb_fields=frozenset({"controlled_conditions"}),
            )

    def list_experiments(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        status: str | None,
        cursor: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._conn(tenant_pub_id) as connection:
            self._require_project(connection, tenant_pub_id, project_pub_id)
            rows = connection.execute(
                """
                SELECT * FROM sop.experiment
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                  AND (%s::text IS NULL OR status=%s)
                  AND (%s::text IS NULL OR pub_id>%s)
                ORDER BY pub_id LIMIT %s
                """,
                (tenant_pub_id, project_pub_id, status, status, cursor, cursor, limit + 1),
            ).fetchall()
        return [_public(row) for row in rows]

    def update_experiment(
        self,
        *,
        tenant_pub_id: str,
        experiment_pub_id: str,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            experiment = self._fetch_required(
                connection,
                """
                SELECT pub_id,project_pub_id FROM sop.experiment
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (tenant_pub_id, experiment_pub_id),
            )
            query_set_pub_id = fields.get("query_set_pub_id")
            if query_set_pub_id is not None:
                self._require_query_set_in_project(
                    connection,
                    tenant_pub_id,
                    str(query_set_pub_id),
                    str(experiment["project_pub_id"]),
                )
            return self._update(
                connection,
                table="experiment",
                tenant_pub_id=tenant_pub_id,
                pub_id=experiment_pub_id,
                fields=fields,
                jsonb_fields=frozenset({"controlled_conditions"}),
            )

    # -- Stage 15: work logs ------------------------------------------------------

    def create_work_log(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        entry_type: str,
        failure_class: str | None,
        content: str,
        actor_pub_id: str,
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            self._require_project(connection, tenant_pub_id, project_pub_id)
            return self._insert(
                connection,
                table="work_log",
                prefix="swl",
                tenant_pub_id=tenant_pub_id,
                fields={
                    "project_pub_id": project_pub_id,
                    "entry_type": entry_type,
                    "failure_class": failure_class,
                    "content": content,
                    "actor_pub_id": actor_pub_id,
                },
            )

    def list_work_logs(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        entry_type: str | None,
        cursor: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._conn(tenant_pub_id) as connection:
            self._require_project(connection, tenant_pub_id, project_pub_id)
            rows = connection.execute(
                """
                SELECT * FROM sop.work_log
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                  AND (%s::text IS NULL OR entry_type=%s)
                  AND (%s::text IS NULL OR pub_id>%s)
                ORDER BY pub_id LIMIT %s
                """,
                (tenant_pub_id, project_pub_id, entry_type, entry_type, cursor, cursor, limit + 1),
            ).fetchall()
        return [_public(row) for row in rows]

    # -- Dashboard aggregation ------------------------------------------------------

    _STEP_META: tuple[tuple[str, str, str], ...] = (
        ("project-definition", "阶段0", "项目定义"),
        ("query-set", "阶段1", "查询词全集"),
        ("baseline", "阶段2", "基线采集"),
        ("retrieval-review", "阶段3", "检索复盘"),
        ("evidence-ledger", "阶段4", "证据账本"),
        ("opportunities", "阶段5-6", "内容机会与信源"),
        ("writing", "阶段7", "文章写作"),
        ("pre-publish", "阶段8", "发布前验证"),
        ("publishing", "阶段9", "发布管理"),
        ("index-watch", "阶段10", "索引观察"),
        ("retest", "阶段11", "同题复测"),
        ("comparison", "阶段12-13", "对比归因"),
        ("experiments", "阶段14", "持续实验"),
        ("archive-log", "阶段15", "归档与工作日志"),
    )

    @staticmethod
    def _step_status(done: bool, has_data: bool) -> str:
        if done:
            return "done"
        return "in_progress" if has_data else "empty"

    @staticmethod
    def _count(connection: psycopg.Connection[Any], sql: str, params: tuple[Any, ...]) -> int:
        row = connection.execute(sql, params).fetchone()
        if row is None:
            return 0
        return int(row["count"])

    def dashboard(self, *, tenant_pub_id: str, project_pub_id: str) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            project = self._require_project(connection, tenant_pub_id, project_pub_id)
            scoped = (tenant_pub_id, project_pub_id)

            query_sets = connection.execute(
                """
                SELECT pub_id,status FROM sop.query_set
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                """,
                scoped,
            ).fetchall()
            frozen_set_ids = [str(row["pub_id"]) for row in query_sets if row["status"] == "frozen"]
            frozen_items = 0
            p0_total = 0
            p0_covered = 0
            if frozen_set_ids:
                frozen_items = self._count(
                    connection,
                    """
                    SELECT count(*) AS count FROM sop.query_item
                    WHERE tenant_pub_id=%s AND query_set_pub_id=ANY(%s)
                    """,
                    (tenant_pub_id, frozen_set_ids),
                )
                p0_total = self._count(
                    connection,
                    """
                    SELECT count(*) AS count FROM sop.query_item
                    WHERE tenant_pub_id=%s AND query_set_pub_id=ANY(%s) AND priority='P0'
                    """,
                    (tenant_pub_id, frozen_set_ids),
                )
                p0_covered = self._count(
                    connection,
                    """
                    SELECT count(*) AS count FROM sop.query_item qi
                    WHERE qi.tenant_pub_id=%s AND qi.query_set_pub_id=ANY(%s)
                      AND qi.priority='P0'
                      AND EXISTS(
                        SELECT 1 FROM sop.baseline_answer b
                        WHERE b.tenant_pub_id=qi.tenant_pub_id
                          AND b.query_item_pub_id=qi.pub_id
                          AND b.capture_status='success'
                      )
                    """,
                    (tenant_pub_id, frozen_set_ids),
                )

            baseline_total = self._count(
                connection,
                """
                SELECT count(*) AS count FROM sop.baseline_answer
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                """,
                scoped,
            )
            baseline_success = self._count(
                connection,
                """
                SELECT count(*) AS count FROM sop.baseline_answer
                WHERE tenant_pub_id=%s AND project_pub_id=%s AND capture_status='success'
                """,
                scoped,
            )
            insights = self._count(
                connection,
                """
                SELECT count(*) AS count FROM sop.retrieval_insight
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                """,
                scoped,
            )
            evidence = self._count(
                connection,
                """
                SELECT count(*) AS count FROM sop.evidence_item
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                """,
                scoped,
            )
            opportunities = self._count(
                connection,
                """
                SELECT count(*) AS count FROM sop.opportunity
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                """,
                scoped,
            )
            opportunities_selected = self._count(
                connection,
                """
                SELECT count(*) AS count FROM sop.opportunity
                WHERE tenant_pub_id=%s AND project_pub_id=%s AND status='selected'
                """,
                scoped,
            )
            articles = self._count(
                connection,
                """
                SELECT count(*) AS count FROM sop.article
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                """,
                scoped,
            )
            versions = self._count(
                connection,
                """
                SELECT count(*) AS count FROM sop.article_version av
                JOIN sop.article a
                  ON a.tenant_pub_id=av.tenant_pub_id AND a.pub_id=av.article_pub_id
                WHERE av.tenant_pub_id=%s AND a.project_pub_id=%s
                """,
                scoped,
            )
            ready_versions = self._count(
                connection,
                """
                SELECT count(*) AS count FROM sop.article_version av
                JOIN sop.article a
                  ON a.tenant_pub_id=av.tenant_pub_id AND a.pub_id=av.article_pub_id
                WHERE av.tenant_pub_id=%s AND a.project_pub_id=%s AND av.publication_ready
                """,
                scoped,
            )
            checks = self._count(
                connection,
                """
                SELECT count(*) AS count FROM sop.pre_publish_check c
                JOIN sop.article_version av
                  ON av.tenant_pub_id=c.tenant_pub_id
                 AND av.pub_id=c.article_version_pub_id
                JOIN sop.article a
                  ON a.tenant_pub_id=av.tenant_pub_id AND a.pub_id=av.article_pub_id
                WHERE c.tenant_pub_id=%s AND a.project_pub_id=%s
                """,
                scoped,
            )
            publications = self._count(
                connection,
                """
                SELECT count(*) AS count FROM sop.publication
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                """,
                scoped,
            )
            publications_public = self._count(
                connection,
                """
                SELECT count(*) AS count FROM sop.publication
                WHERE tenant_pub_id=%s AND project_pub_id=%s AND status='public'
                """,
                scoped,
            )
            observations = self._count(
                connection,
                """
                SELECT count(*) AS count FROM sop.index_observation o
                JOIN sop.publication p
                  ON p.tenant_pub_id=o.tenant_pub_id AND p.pub_id=o.publication_pub_id
                WHERE o.tenant_pub_id=%s AND p.project_pub_id=%s
                """,
                scoped,
            )
            max_checkpoints_row = connection.execute(
                """
                SELECT COALESCE(max(checkpoints),0) AS count FROM (
                  SELECT count(DISTINCT o.checkpoint) AS checkpoints
                  FROM sop.index_observation o
                  JOIN sop.publication p
                    ON p.tenant_pub_id=o.tenant_pub_id AND p.pub_id=o.publication_pub_id
                  WHERE o.tenant_pub_id=%s AND p.project_pub_id=%s
                  GROUP BY o.publication_pub_id
                ) per_publication
                """,
                scoped,
            ).fetchone()
            max_checkpoints = int(self._require(max_checkpoints_row)["count"])
            retests = self._count(
                connection,
                """
                SELECT count(*) AS count FROM sop.retest_answer r
                JOIN sop.publication p
                  ON p.tenant_pub_id=r.tenant_pub_id AND p.pub_id=r.publication_pub_id
                WHERE r.tenant_pub_id=%s AND p.project_pub_id=%s
                """,
                scoped,
            )
            retests_success = self._count(
                connection,
                """
                SELECT count(*) AS count FROM sop.retest_answer r
                JOIN sop.publication p
                  ON p.tenant_pub_id=r.tenant_pub_id AND p.pub_id=r.publication_pub_id
                WHERE r.tenant_pub_id=%s AND p.project_pub_id=%s AND r.capture_status='success'
                """,
                scoped,
            )
            comparisons = self._count(
                connection,
                """
                SELECT count(*) AS count FROM sop.comparison c
                JOIN sop.publication p
                  ON p.tenant_pub_id=c.tenant_pub_id AND p.pub_id=c.publication_pub_id
                WHERE c.tenant_pub_id=%s AND p.project_pub_id=%s
                """,
                scoped,
            )
            experiments = self._count(
                connection,
                """
                SELECT count(*) AS count FROM sop.experiment
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                """,
                scoped,
            )
            work_logs = self._count(
                connection,
                """
                SELECT count(*) AS count FROM sop.work_log
                WHERE tenant_pub_id=%s AND project_pub_id=%s
                """,
                scoped,
            )

            article_rows = connection.execute(
                """
                SELECT a.pub_id AS article_pub_id,a.title,a.status,
                       (SELECT count(*) FROM sop.article_version av
                        WHERE av.tenant_pub_id=a.tenant_pub_id
                          AND av.article_pub_id=a.pub_id) AS version_count,
                       EXISTS(
                         SELECT 1 FROM sop.article_version av
                         WHERE av.tenant_pub_id=a.tenant_pub_id
                           AND av.article_pub_id=a.pub_id AND av.publication_ready
                       ) AS publication_ready,
                       EXISTS(
                         SELECT 1 FROM sop.publication p
                         JOIN sop.article_version av
                           ON av.tenant_pub_id=p.tenant_pub_id
                          AND av.pub_id=p.article_version_pub_id
                         WHERE p.tenant_pub_id=a.tenant_pub_id
                           AND av.article_pub_id=a.pub_id
                       ) AS has_publication
                FROM sop.article a
                WHERE a.tenant_pub_id=%s AND a.project_pub_id=%s
                ORDER BY a.pub_id
                """,
                scoped,
            ).fetchall()
            dashboard_articles = [
                {
                    "article_pub_id": str(row["article_pub_id"]),
                    "title": row["title"],
                    "status": row["status"],
                    "version_count": int(row["version_count"]),
                    "publication_ready": bool(row["publication_ready"]),
                    "has_publication": bool(row["has_publication"]),
                    "maturity_level": self._article_maturity(
                        connection, tenant_pub_id, str(row["article_pub_id"])
                    ),
                }
                for row in article_rows
            ]

        brand_profile_keys = len(project["brand_profile"] or {})
        target_platforms = len(project["target_platforms"] or [])
        success_definitions = len(project["success_definition"] or [])
        coverage_pct = round(100.0 * p0_covered / p0_total, 2) if p0_total > 0 else None
        step_metrics: list[dict[str, Any]] = [
            {
                "brand_profile_keys": brand_profile_keys,
                "target_platforms": target_platforms,
                "success_definitions": success_definitions,
            },
            {
                "sets": len(query_sets),
                "frozen_sets": len(frozen_set_ids),
                "frozen_items": frozen_items,
            },
            {
                "answers": baseline_total,
                "success": baseline_success,
                "failed_samples": baseline_total - baseline_success,
                "coverage_pct": coverage_pct,
            },
            {"insights": insights},
            {"evidence": evidence},
            {"opportunities": opportunities, "selected": opportunities_selected},
            {"articles": articles, "versions": versions},
            {"ready_versions": ready_versions, "checks": checks},
            {"publications": publications, "public": publications_public},
            {"observations": observations, "max_checkpoints_per_publication": max_checkpoints},
            {"retests": retests, "success": retests_success},
            {"comparisons": comparisons},
            {"experiments": experiments},
            {"work_logs": work_logs},
        ]
        step_done = [
            brand_profile_keys > 0 and target_platforms > 0 and success_definitions > 0,
            len(frozen_set_ids) > 0 and frozen_items >= 1,
            p0_total > 0 and p0_covered == p0_total,
            insights >= 1,
            evidence >= 1,
            opportunities_selected >= 1,
            articles >= 1 and versions >= 1,
            ready_versions >= 1,
            publications_public >= 1,
            max_checkpoints >= 2,
            retests_success >= 1,
            comparisons >= 1,
            experiments >= 1,
            work_logs >= 1,
        ]
        step_has_data = [
            brand_profile_keys > 0 or target_platforms > 0 or success_definitions > 0,
            len(query_sets) > 0,
            baseline_total > 0,
            insights > 0,
            evidence > 0,
            opportunities > 0,
            articles > 0,
            ready_versions > 0 or checks > 0,
            publications > 0,
            observations > 0,
            retests > 0,
            comparisons > 0,
            experiments > 0,
            work_logs > 0,
        ]
        steps = [
            {
                "key": key,
                "stage": stage,
                "name": name,
                "status": self._step_status(step_done[index], step_has_data[index]),
                "metrics": step_metrics[index],
            }
            for index, (key, stage, name) in enumerate(self._STEP_META)
        ]
        return {
            "project": _public(project),
            "steps": steps,
            "articles": dashboard_articles,
        }
