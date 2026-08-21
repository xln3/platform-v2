from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from domain.collection.answer_content import project_answer_content
from domain.metrics.customer import assert_customer_projection_safe

from ..analytics.sampling_progress import parse_sampling_configs, select_sampling_campaign
from ..variants.textutil import normalize_query
from .service import _customer_connection


@dataclass(frozen=True)
class LibraryQuestion:
    question_id: str
    ordinal: int
    variant_label: str
    text: str
    normalized_text: str


@dataclass(frozen=True)
class LibraryMetaQuery:
    meta_query_id: str
    ordinal: int
    label: str
    questions: tuple[LibraryQuestion, ...]


@dataclass(frozen=True)
class LibraryDefinition:
    snapshot_id: str
    config_version_pub_ids: tuple[str, ...]
    meta_queries: tuple[LibraryMetaQuery, ...]


_GENERIC_GROUP_NAMES = {
    "",
    "core",
    "默认分组",
    "问题组",
    "首版评测问题",
}


def _public_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256("\x1f".join(str(part) for part in parts).encode()).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _variant_label(ordinal: int) -> str:
    if ordinal == 1:
        return "原问题"
    variant = ordinal - 1
    if variant <= 26:
        return f"变体 {chr(ord('A') + variant - 1)}"
    return f"变体 {variant}"


def _snapshot_groups(snapshot: object) -> list[tuple[str, list[str]]]:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("query_groups"), list):
        return []
    groups: list[tuple[str, list[str]]] = []
    for raw_group in snapshot["query_groups"]:
        if not isinstance(raw_group, dict) or not isinstance(raw_group.get("items"), list):
            continue
        questions = [
            str(item["text"]).strip()
            for item in raw_group["items"]
            if isinstance(item, dict)
            and isinstance(item.get("text"), str)
            and str(item["text"]).strip()
        ]
        if questions:
            groups.append((str(raw_group.get("name") or "").strip(), questions))
    if len(groups) == 1 and len(groups[0][1]) >= 8 and len(groups[0][1]) % 4 == 0:
        questions = groups[0][1]
        return [
            (questions[offset], questions[offset : offset + 4])
            for offset in range(0, len(questions), 4)
        ]
    return groups


def build_library_definition(
    snapshot_hash: str,
    snapshot: object,
    *,
    config_version_pub_ids: tuple[str, ...] = (),
) -> LibraryDefinition:
    snapshot_id = f"als_{snapshot_hash[:24]}"
    meta_queries: list[LibraryMetaQuery] = []
    for meta_ordinal, (raw_label, question_texts) in enumerate(_snapshot_groups(snapshot), start=1):
        label = question_texts[0] if raw_label.casefold() in _GENERIC_GROUP_NAMES else raw_label
        meta_query_id = _public_id(
            "amq", snapshot_id, meta_ordinal, label, "\x1e".join(question_texts)
        )
        questions = tuple(
            LibraryQuestion(
                question_id=_public_id("aq", meta_query_id, ordinal, text),
                ordinal=ordinal,
                variant_label=_variant_label(ordinal),
                text=text,
                normalized_text=normalize_query(text),
            )
            for ordinal, text in enumerate(question_texts, start=1)
        )
        meta_queries.append(
            LibraryMetaQuery(
                meta_query_id=meta_query_id,
                ordinal=meta_ordinal,
                label=label,
                questions=questions,
            )
        )
    return LibraryDefinition(
        snapshot_id=snapshot_id,
        config_version_pub_ids=config_version_pub_ids,
        meta_queries=tuple(meta_queries),
    )


def _legacy_snapshot(connection: Any, project_id: object) -> tuple[str, dict[str, object]]:
    rows = connection.execute(
        """
        SELECT qg.pub_id AS group_pub_id,qg.name,qi.pub_id AS question_pub_id,
               qi.text,qi.priority
        FROM platform.query_group qg
        JOIN platform.query_item qi ON qi.group_id=qg.id
        WHERE qg.project_id=%s
        ORDER BY qg.created_at,qg.pub_id,qi.priority,qi.pub_id
        """,
        (project_id,),
    ).fetchall()
    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        group_id = str(row["group_pub_id"])
        group = grouped.setdefault(group_id, {"name": str(row["name"]), "items": []})
        items = group["items"]
        if isinstance(items, list):
            items.append({"text": str(row["text"]), "priority": int(row["priority"])})
    snapshot: dict[str, object] = {"query_groups": list(grouped.values())}
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest(), snapshot


def _load_definition(
    connection: Any,
    *,
    project_id: object,
    requested_snapshot_id: str | None,
    snapshot_at: datetime,
) -> LibraryDefinition:
    catalog = connection.execute(
        """
        SELECT mcv.pub_id,mcv.snapshot_hash,mcv.snapshot_json,
               catalog.campaign_started_at,catalog.retired_at
        FROM platform.answer_library_catalog catalog
        JOIN platform.monitoring_config_version mcv
          ON mcv.id=catalog.catalog_config_version_id
        WHERE catalog.project_id=%s AND catalog.activated_at<=%s
          AND (catalog.retired_at IS NULL OR catalog.retired_at>%s)
        ORDER BY catalog.activated_at DESC,catalog.created_at DESC,catalog.pub_id DESC
        LIMIT 1
        """,
        (project_id, snapshot_at, snapshot_at),
    ).fetchone()
    if catalog is not None:
        try:
            raw_snapshot = catalog["snapshot_json"]
            snapshot = json.loads(raw_snapshot) if isinstance(raw_snapshot, str) else raw_snapshot
        except (TypeError, ValueError) as exc:
            raise LookupError("answer_library_snapshot_invalid") from exc
        if not isinstance(snapshot, dict):
            raise LookupError("answer_library_snapshot_invalid")
        config_rows = connection.execute(
            """
            SELECT mcv.pub_id
            FROM platform.monitoring_config mc
            JOIN platform.monitoring_config_version mcv ON mcv.config_id=mc.id
            WHERE mc.project_id=%s AND mcv.frozen_at IS NOT NULL
              AND mcv.frozen_at>=%s AND mcv.frozen_at<=%s
              AND (%s::timestamptz IS NULL OR mcv.frozen_at<%s::timestamptz)
            ORDER BY mcv.frozen_at,mcv.created_at,mcv.pub_id
            """,
            (
                project_id,
                catalog["campaign_started_at"],
                snapshot_at,
                catalog["retired_at"],
                catalog["retired_at"],
            ),
        ).fetchall()
        definition = build_library_definition(
            str(catalog["snapshot_hash"]),
            snapshot,
            config_version_pub_ids=tuple(str(row["pub_id"]) for row in config_rows),
        )
    else:
        definition = _infer_legacy_definition(
            connection,
            project_id=project_id,
            snapshot_at=snapshot_at,
        )
    if requested_snapshot_id is not None and definition.snapshot_id != requested_snapshot_id:
        raise LookupError("answer_library_snapshot_not_found")
    if not definition.meta_queries:
        raise LookupError("answer_library_snapshot_invalid")
    return definition


def _infer_legacy_definition(
    connection: Any,
    *,
    project_id: object,
    snapshot_at: datetime,
) -> LibraryDefinition:
    rows = connection.execute(
        """
        SELECT mcv.pub_id,mcv.revision,mcv.snapshot_hash,mcv.snapshot_json
        FROM platform.monitoring_config mc
        JOIN platform.monitoring_config_version mcv ON mcv.config_id=mc.id
        WHERE mc.project_id=%s AND mcv.frozen_at IS NOT NULL
          AND mcv.frozen_at<=%s
        ORDER BY mcv.revision DESC,mcv.created_at DESC,mcv.pub_id DESC
        LIMIT 1000
        """,
        (project_id, snapshot_at),
    ).fetchall()
    configs = parse_sampling_configs(rows)
    baseline, campaign = select_sampling_campaign(configs)
    if baseline is not None:
        row = next(
            (candidate for candidate in rows if candidate["pub_id"] == baseline.pub_id),
            None,
        )
        if row is None:
            raise LookupError("answer_library_snapshot_invalid")
        try:
            raw_snapshot = row["snapshot_json"]
            snapshot = json.loads(raw_snapshot) if isinstance(raw_snapshot, str) else raw_snapshot
        except (TypeError, ValueError) as exc:
            raise LookupError("answer_library_snapshot_invalid") from exc
        if not isinstance(snapshot, dict):
            raise LookupError("answer_library_snapshot_invalid")
        definition = build_library_definition(
            str(row["snapshot_hash"]),
            snapshot,
            config_version_pub_ids=tuple(config.pub_id for config in campaign),
        )
    else:
        snapshot_hash, snapshot = _legacy_snapshot(connection, project_id)
        definition = build_library_definition(snapshot_hash, snapshot)
    return definition


def _answer_rows(
    connection: Any,
    *,
    tenant_pub_id: str,
    project_pub_id: str,
    start: date,
    end: date,
    snapshot_at: datetime,
    config_version_pub_ids: tuple[str, ...] = (),
    model: str | None = None,
    region: str | None = None,
    mode: str | None = None,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT task.pub_id,NULL::text AS query_pub_id,
               task.matrix_json::jsonb->>'query' AS query_text,
               task.matrix_json::jsonb->>'model' AS model,
               task.matrix_json::jsonb->>'region' AS region,
               task.matrix_json::jsonb->>'mode' AS mode,
               task.created_at AS capture_time,
               aa.analysis_run_pub_id,aa.mentioned,aa.rank,aa.sentiment,aa.recommended,
               jsonb_array_length(COALESCE(task.citations_json,'[]')::jsonb) AS citation_count
        FROM platform.collection_task task
        JOIN platform.collection_run run ON run.id=task.run_id
        JOIN platform.project project ON project.id=run.project_id
        JOIN platform.monitoring_config_version config ON config.id=run.config_version_id
        JOIN platform.tenant tenant ON tenant.id=task.tenant_id
        LEFT JOIN LATERAL (
          SELECT analysis_run_pub_id,mentioned,rank,sentiment,recommended
          FROM analytics.answer_analysis
          WHERE tenant_pub_id=tenant.pub_id AND answer_pub_id=task.pub_id
            AND created_at<=%s
          ORDER BY created_at DESC,id DESC LIMIT 1
        ) aa ON true
        WHERE tenant.pub_id=%s AND project.pub_id=%s AND task.state='completed'
          AND task.created_at::date BETWEEN %s AND %s
          AND task.created_at<=%s
          AND (%s::text[] IS NULL OR config.pub_id=ANY(%s::text[]))
          AND (%s::text IS NULL OR task.matrix_json::jsonb->>'model'=%s::text)
          AND (%s::text IS NULL OR task.matrix_json::jsonb->>'region'=%s::text)
          AND (%s::text IS NULL OR task.matrix_json::jsonb->>'mode'=%s::text)
        ORDER BY task.created_at DESC,task.pub_id DESC
        """,
        (
            snapshot_at,
            tenant_pub_id,
            project_pub_id,
            start,
            end,
            snapshot_at,
            list(config_version_pub_ids) or None,
            list(config_version_pub_ids) or None,
            model,
            model,
            region,
            region,
            mode,
            mode,
        ),
    ).fetchall()
    return [dict(row) for row in rows]


def _definition_indexes(
    definition: LibraryDefinition,
) -> tuple[dict[str, LibraryQuestion | None], dict[str, LibraryQuestion | None]]:
    exact: dict[str, LibraryQuestion | None] = {}
    normalized: dict[str, LibraryQuestion | None] = {}

    def register(
        index: dict[str, LibraryQuestion | None], key: str, question: LibraryQuestion
    ) -> None:
        if key not in index:
            index[key] = question
        elif index[key] != question:
            # A historical answer without immutable question lineage cannot safely be
            # assigned when two frozen questions share the same matching key.
            index[key] = None

    for meta_query in definition.meta_queries:
        for question in meta_query.questions:
            register(exact, question.text.strip(), question)
            if question.normalized_text:
                register(normalized, question.normalized_text, question)
    return exact, normalized


def _partition_answers(
    definition: LibraryDefinition, rows: list[dict[str, Any]]
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    exact, normalized = _definition_indexes(definition)
    by_question: dict[str, list[dict[str, Any]]] = {
        question.question_id: []
        for meta_query in definition.meta_queries
        for question in meta_query.questions
    }
    unmapped: list[dict[str, Any]] = []
    for row in rows:
        raw_text = str(row.get("query_text") or "").strip()
        question = (
            exact[raw_text] if raw_text in exact else normalized.get(normalize_query(raw_text))
        )
        if question is None:
            unmapped.append(row)
        else:
            by_question[question.question_id].append(row)
    return by_question, unmapped


def _dimension_rows(rows: list[dict[str, Any]], key: str) -> list[dict[str, object]]:
    counts = Counter(str(row.get(key) or "").strip() or "未标注" for row in rows)
    return [
        {"label": label, "answer_count": count}
        for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:100]
    ]


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "answer_count": len(rows),
        "cited_answer_count": sum(int(row["citation_count"]) > 0 for row in rows),
        "citation_count": sum(int(row["citation_count"]) for row in rows),
        "mentioned_answer_count": sum(row.get("mentioned") is True for row in rows),
        "latest_capture_time": max((row["capture_time"] for row in rows), default=None),
        "models": _dimension_rows(rows, "model"),
        "regions": _dimension_rows(rows, "region"),
        "modes": _dimension_rows(rows, "mode"),
    }


def _question_view(question: LibraryQuestion, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "question_id": question.question_id,
        "ordinal": question.ordinal,
        "variant_label": question.variant_label,
        "text": question.text,
        **_stats(rows),
    }


def _meta_view(
    meta_query: LibraryMetaQuery,
    by_question: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    rows = [row for question in meta_query.questions for row in by_question[question.question_id]]
    return {
        "meta_query_id": meta_query.meta_query_id,
        "ordinal": meta_query.ordinal,
        "label": meta_query.label,
        "question_count": len(meta_query.questions),
        **_stats(rows),
        "questions": [
            {
                "question_id": question.question_id,
                "ordinal": question.ordinal,
                "variant_label": question.variant_label,
                "text": question.text,
                "answer_count": len(by_question[question.question_id]),
            }
            for question in meta_query.questions
        ],
    }


def _run_views(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    repeat_by_answer: dict[str, int] = {}
    partitions: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row["model"]), str(row["region"]), str(row["mode"]))
        partitions.setdefault(key, []).append(row)
    for partition in partitions.values():
        for repeat_index, row in enumerate(
            sorted(partition, key=lambda item: (item["capture_time"], str(item["pub_id"]))),
            start=1,
        ):
            repeat_by_answer[str(row["pub_id"])] = repeat_index
    return [
        {
            "answer_pub_id": str(row["pub_id"]),
            "repeat_index": repeat_by_answer[str(row["pub_id"])],
            "model": str(row["model"]),
            "region": str(row["region"]),
            "mode": str(row["mode"]),
            "capture_time": row["capture_time"],
            "analysis_state": "ready" if row.get("analysis_run_pub_id") else "pending",
            "mentioned": row.get("mentioned"),
            "rank": int(row["rank"]) if row.get("rank") is not None else None,
            "sentiment": str(row["sentiment"]) if row.get("sentiment") is not None else None,
            "recommended": row.get("recommended"),
            "citation_count": int(row["citation_count"]),
        }
        for row in rows
    ]


def _locate_meta(definition: LibraryDefinition, meta_query_id: str) -> LibraryMetaQuery:
    for meta_query in definition.meta_queries:
        if meta_query.meta_query_id == meta_query_id:
            return meta_query
    raise LookupError("answer_library_meta_query_not_found")


def _locate_question(
    definition: LibraryDefinition, question_id: str
) -> tuple[LibraryMetaQuery, LibraryQuestion]:
    for meta_query in definition.meta_queries:
        for question in meta_query.questions:
            if question.question_id == question_id:
                return meta_query, question
    raise LookupError("answer_library_question_not_found")


class CustomerAnswerLibraryService:
    def __init__(self, *, dsn: str) -> None:
        self.dsn = dsn

    @staticmethod
    def _project(connection: Any, project_pub_id: str) -> dict[str, Any]:
        project = connection.execute(
            "SELECT id,pub_id FROM platform.project WHERE pub_id=%s", (project_pub_id,)
        ).fetchone()
        if project is None:
            raise LookupError("project_not_found")
        return dict(project)

    def library_page(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        start: date,
        end: date,
        snapshot_at: datetime,
        snapshot_id: str | None = None,
        search: str | None = None,
        model: str | None = None,
        region: str | None = None,
        mode: str | None = None,
        offset: int = 0,
        limit: int = 8,
    ) -> dict[str, Any]:
        with _customer_connection(self.dsn, tenant_pub_id) as connection:
            project = self._project(connection, project_pub_id)
            definition = _load_definition(
                connection,
                project_id=project["id"],
                requested_snapshot_id=snapshot_id,
                snapshot_at=snapshot_at,
            )
            rows = _answer_rows(
                connection,
                tenant_pub_id=tenant_pub_id,
                project_pub_id=project_pub_id,
                start=start,
                end=end,
                snapshot_at=snapshot_at,
                config_version_pub_ids=definition.config_version_pub_ids,
                model=model,
                region=region,
                mode=mode,
            )
        by_question, unmapped = _partition_answers(definition, rows)
        meta_views = [_meta_view(meta, by_question) for meta in definition.meta_queries]
        needle = search.strip().casefold() if search and search.strip() else None
        visible = [
            view
            for meta, view in zip(definition.meta_queries, meta_views, strict=True)
            if needle is None
            or needle in meta.label.casefold()
            or any(needle in question.text.casefold() for question in meta.questions)
        ]
        mapped_rows = [row for question_rows in by_question.values() for row in question_rows]
        mapped_stats = _stats(mapped_rows)
        page_data = visible[offset : offset + limit]
        document = {
            "schema_version": "customer-answer-library-v1",
            "project_pub_id": project_pub_id,
            "snapshot_id": definition.snapshot_id,
            "snapshot_at": snapshot_at,
            "totals": {
                "meta_query_count": len(definition.meta_queries),
                "question_count": sum(len(meta.questions) for meta in definition.meta_queries),
                "answer_count": mapped_stats["answer_count"],
                "cited_answer_count": mapped_stats["cited_answer_count"],
                "citation_count": mapped_stats["citation_count"],
                "mentioned_answer_count": mapped_stats["mentioned_answer_count"],
                "unmapped_answer_count": len(unmapped),
            },
            "models": mapped_stats["models"],
            "regions": mapped_stats["regions"],
            "modes": mapped_stats["modes"],
            "data": page_data,
            "page": {
                "total": len(visible),
                "offset": offset,
                "limit": limit,
                "has_more": offset + len(page_data) < len(visible),
            },
        }
        assert_customer_projection_safe(document)
        return document

    def meta_query(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        meta_query_id: str,
        snapshot_id: str,
        snapshot_at: datetime,
        start: date,
        end: date,
        model: str | None = None,
        region: str | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        with _customer_connection(self.dsn, tenant_pub_id) as connection:
            project = self._project(connection, project_pub_id)
            definition = _load_definition(
                connection,
                project_id=project["id"],
                requested_snapshot_id=snapshot_id,
                snapshot_at=snapshot_at,
            )
            meta_query = _locate_meta(definition, meta_query_id)
            rows = _answer_rows(
                connection,
                tenant_pub_id=tenant_pub_id,
                project_pub_id=project_pub_id,
                start=start,
                end=end,
                snapshot_at=snapshot_at,
                config_version_pub_ids=definition.config_version_pub_ids,
                model=model,
                region=region,
                mode=mode,
            )
        by_question, _ = _partition_answers(definition, rows)
        meta_rows = [
            row for question in meta_query.questions for row in by_question[question.question_id]
        ]
        meta_stats = _stats(meta_rows)
        document = {
            "schema_version": "customer-answer-library-meta-v1",
            "project_pub_id": project_pub_id,
            "snapshot_id": snapshot_id,
            "snapshot_at": snapshot_at,
            "meta_query_id": meta_query.meta_query_id,
            "ordinal": meta_query.ordinal,
            "label": meta_query.label,
            "answer_count": meta_stats["answer_count"],
            "cited_answer_count": meta_stats["cited_answer_count"],
            "citation_count": meta_stats["citation_count"],
            "mentioned_answer_count": meta_stats["mentioned_answer_count"],
            "latest_capture_time": meta_stats["latest_capture_time"],
            "questions": [
                _question_view(question, by_question[question.question_id])
                for question in meta_query.questions
            ],
        }
        assert_customer_projection_safe(document)
        return document

    def question_runs(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        question_id: str,
        snapshot_id: str,
        snapshot_at: datetime,
        start: date,
        end: date,
        model: str | None = None,
        region: str | None = None,
        mode: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        with _customer_connection(self.dsn, tenant_pub_id) as connection:
            project = self._project(connection, project_pub_id)
            definition = _load_definition(
                connection,
                project_id=project["id"],
                requested_snapshot_id=snapshot_id,
                snapshot_at=snapshot_at,
            )
            meta_query, question = _locate_question(definition, question_id)
            rows = _answer_rows(
                connection,
                tenant_pub_id=tenant_pub_id,
                project_pub_id=project_pub_id,
                start=start,
                end=end,
                snapshot_at=snapshot_at,
                config_version_pub_ids=definition.config_version_pub_ids,
                model=model,
                region=region,
                mode=mode,
            )
        by_question, _ = _partition_answers(definition, rows)
        question_rows = by_question[question.question_id]
        runs = _run_views(question_rows)
        page_data = runs[offset : offset + limit]
        stats = _stats(question_rows)
        document = {
            "schema_version": "customer-answer-library-runs-v1",
            "project_pub_id": project_pub_id,
            "snapshot_id": snapshot_id,
            "snapshot_at": snapshot_at,
            "meta_query_id": meta_query.meta_query_id,
            "meta_query_ordinal": meta_query.ordinal,
            "meta_query_label": meta_query.label,
            "question": _question_view(question, question_rows),
            "models": stats["models"],
            "regions": stats["regions"],
            "modes": stats["modes"],
            "data": page_data,
            "page": {
                "total": len(runs),
                "offset": offset,
                "limit": limit,
                "has_more": offset + len(page_data) < len(runs),
            },
        }
        assert_customer_projection_safe(document)
        return document

    def answer_detail(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        answer_pub_id: str,
        snapshot_id: str,
        snapshot_at: datetime,
        start: date,
        end: date,
    ) -> dict[str, Any]:
        with _customer_connection(self.dsn, tenant_pub_id) as connection:
            project = self._project(connection, project_pub_id)
            definition = _load_definition(
                connection,
                project_id=project["id"],
                requested_snapshot_id=snapshot_id,
                snapshot_at=snapshot_at,
            )
            rows = _answer_rows(
                connection,
                tenant_pub_id=tenant_pub_id,
                project_pub_id=project_pub_id,
                start=start,
                end=end,
                snapshot_at=snapshot_at,
                config_version_pub_ids=definition.config_version_pub_ids,
            )
            by_question, _ = _partition_answers(definition, rows)
            selected_question: LibraryQuestion | None = None
            selected_meta: LibraryMetaQuery | None = None
            selected_row: dict[str, Any] | None = None
            for meta_query in definition.meta_queries:
                for question in meta_query.questions:
                    for row in by_question[question.question_id]:
                        if str(row["pub_id"]) == answer_pub_id:
                            selected_meta = meta_query
                            selected_question = question
                            selected_row = row
                            break
                    if selected_row is not None:
                        break
                if selected_row is not None:
                    break
            if selected_row is None or selected_question is None or selected_meta is None:
                raise LookupError("answer_library_answer_not_found")
            body = connection.execute(
                """
                SELECT task.answer_text,task.citations_json
                FROM platform.collection_task task
                JOIN platform.collection_run run ON run.id=task.run_id
                JOIN platform.project project ON project.id=run.project_id
                JOIN platform.tenant tenant ON tenant.id=task.tenant_id
                WHERE tenant.pub_id=%s AND project.pub_id=%s AND task.pub_id=%s
                  AND task.created_at<=%s AND task.state='completed'
                """,
                (tenant_pub_id, project_pub_id, answer_pub_id, snapshot_at),
            ).fetchone()
            if body is None:
                raise LookupError("answer_library_answer_not_found")
            try:
                citations = json.loads(body.get("citations_json") or "[]")
            except (TypeError, ValueError) as exc:
                raise LookupError("answer_library_answer_not_found") from exc
        raw_response = str(body.get("answer_text") or "")
        response_text = project_answer_content(raw_response, citations)
        question_rows = by_question[selected_question.question_id]
        run = next(
            item for item in _run_views(question_rows) if item["answer_pub_id"] == answer_pub_id
        )
        document = {
            "schema_version": "customer-answer-library-detail-v1",
            "project_pub_id": project_pub_id,
            "snapshot_id": snapshot_id,
            "snapshot_at": snapshot_at,
            "meta_query_id": selected_meta.meta_query_id,
            "meta_query_ordinal": selected_meta.ordinal,
            "meta_query_label": selected_meta.label,
            "question_id": selected_question.question_id,
            "question_ordinal": selected_question.ordinal,
            "variant_label": selected_question.variant_label,
            "question_text": selected_question.text,
            "answer": run,
            "response_text": response_text.response_markdown_normalized,
        }
        assert_customer_projection_safe(document)
        return document
