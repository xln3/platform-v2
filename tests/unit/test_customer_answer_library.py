from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from inspect import getsource
from typing import Any

import pytest
from fastapi import HTTPException, Response
from geo_platform.customer_dashboard import answer_library as library
from geo_platform.customer_dashboard import router as customer_router
from geo_platform.customer_dashboard.schemas import (
    CustomerAnswerLibraryDetailView,
    CustomerAnswerLibraryMetaDetailView,
    CustomerAnswerLibraryPageView,
    CustomerAnswerLibraryQuestionRunsView,
)
from geo_platform.identity.policy import Principal, Role


def _snapshot() -> dict[str, Any]:
    return {
        "query_groups": [
            {
                "name": f"关键词 {group}",
                "items": [
                    {"text": f"关键词 {group} 的问题 {variant}", "priority": variant}
                    for variant in range(1, 5)
                ],
            }
            for group in range(1, 35)
        ],
        "models": ["DeepSeek", "豆包"],
        "regions": ["上海", "北京"],
        "modes": ["deep_think", "normal"],
    }


class _Result:
    def __init__(
        self,
        *,
        one: dict[str, Any] | None = None,
        many: list[dict[str, Any]] | None = None,
    ) -> None:
        self.one = one
        self.many = many or []

    def fetchone(self) -> dict[str, Any] | None:
        return self.one

    def fetchall(self) -> list[dict[str, Any]]:
        return self.many


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.catalog_row: dict[str, Any] | None = None
        self.catalog_config_rows: list[dict[str, Any]] = []
        self.config_rows = [
            {
                "pub_id": "cfv_library",
                "revision": 1,
                "snapshot_hash": "a" * 64,
                "snapshot_json": json.dumps(_snapshot()),
            }
        ]
        capture = datetime(2026, 8, 18, 8, 0, tzinfo=UTC)
        self.answer_rows = [
            {
                "pub_id": "ans_library_01",
                "query_pub_id": None,
                "query_text": "关键词 1 的问题 1",
                "model": "DeepSeek",
                "region": "上海",
                "mode": "deep_think",
                "capture_time": capture,
                "analysis_run_pub_id": "arun_library_01",
                "mentioned": True,
                "rank": 1,
                "sentiment": "positive",
                "recommended": True,
                "citation_count": 3,
            },
            {
                "pub_id": "ans_library_02",
                "query_pub_id": None,
                "query_text": "关键词 1 的问题 1",
                "model": "DeepSeek",
                "region": "上海",
                "mode": "deep_think",
                "capture_time": capture.replace(hour=9),
                "analysis_run_pub_id": "arun_library_02",
                "mentioned": False,
                "rank": None,
                "sentiment": "neutral",
                "recommended": False,
                "citation_count": 0,
            },
            {
                "pub_id": "ans_library_03",
                "query_pub_id": None,
                "query_text": "关键词 2 的问题 4",
                "model": "豆包",
                "region": "北京",
                "mode": "normal",
                "capture_time": capture.replace(hour=10),
                "analysis_run_pub_id": None,
                "mentioned": None,
                "rank": None,
                "sentiment": None,
                "recommended": None,
                "citation_count": 0,
            },
            {
                "pub_id": "ans_library_unmapped",
                "query_pub_id": None,
                "query_text": "不在冻结配置中的历史问题",
                "model": "豆包",
                "region": "北京",
                "mode": "normal",
                "capture_time": capture.replace(hour=11),
                "analysis_run_pub_id": "arun_unmapped",
                "mentioned": True,
                "rank": 2,
                "sentiment": "positive",
                "recommended": True,
                "citation_count": 2,
            },
        ]

    def execute(self, sql: str, parameters: tuple[object, ...]) -> _Result:
        self.calls.append((sql, parameters))
        if "SELECT id,pub_id FROM platform.project" in sql:
            return _Result(one={"id": "project-uuid", "pub_id": "prj_library"})
        if "FROM platform.answer_library_catalog catalog" in sql:
            return _Result(one=self.catalog_row)
        if "mcv.frozen_at>=%s" in sql:
            return _Result(many=self.catalog_config_rows)
        if "FROM platform.monitoring_config mc" in sql:
            return _Result(many=self.config_rows)
        if "SELECT task.pub_id,NULL::text AS query_pub_id" in sql:
            model = parameters[-6]
            region = parameters[-4]
            mode = parameters[-2]
            rows = [
                row
                for row in self.answer_rows
                if (model is None or row["model"] == model)
                and (region is None or row["region"] == region)
                and (mode is None or row["mode"] == mode)
            ]
            return _Result(
                many=sorted(
                    rows,
                    key=lambda row: (row["capture_time"], str(row["pub_id"])),
                    reverse=True,
                )
            )
        if "SELECT task.answer_text,task.citations_json" in sql:
            return _Result(
                one={
                    "answer_text": "推荐测试品牌。[citation:1]",
                    "citations_json": json.dumps(
                        [
                            {
                                "url": "https://example.com/source",
                                "title": "来源",
                                "cited_text": "证据",
                                "ordinal": 1,
                                "platform_ordinal": 1,
                                "ordinal_base": 1,
                            }
                        ]
                    ),
                }
            )
        raise AssertionError(sql)


@pytest.fixture
def fake_connection(monkeypatch: pytest.MonkeyPatch) -> _Connection:
    connection = _Connection()

    @contextmanager
    def connect(dsn: str, tenant_pub_id: str) -> Iterator[_Connection]:
        assert dsn == "postgresql://safe"
        assert tenant_pub_id == "tnt_safe"
        yield connection

    monkeypatch.setattr(library, "_customer_connection", connect)
    return connection


def test_definition_preserves_34_confirmed_meta_queries_and_four_variants() -> None:
    definition = library.build_library_definition("a" * 64, _snapshot())

    assert definition.snapshot_id == f"als_{'a' * 24}"
    assert definition.config_version_pub_ids == ()
    assert len(definition.meta_queries) == 34
    assert [item.variant_label for item in definition.meta_queries[0].questions] == [
        "原问题",
        "变体 A",
        "变体 B",
        "变体 C",
    ]
    assert definition.meta_queries[0].meta_query_id.startswith("amq_")
    assert definition.meta_queries[0].questions[0].question_id.startswith("aq_")


def test_definition_splits_one_legacy_flat_group_into_quartets() -> None:
    snapshot = {
        "query_groups": [
            {
                "name": "首版评测问题",
                "items": [{"text": f"问题 {index}"} for index in range(1, 13)],
            }
        ]
    }

    definition = library.build_library_definition("b" * 64, snapshot)

    assert len(definition.meta_queries) == 3
    assert definition.meta_queries[0].label == "问题 1"
    assert [item.text for item in definition.meta_queries[-1].questions] == [
        "问题 9",
        "问题 10",
        "问题 11",
        "问题 12",
    ]


def test_duplicate_frozen_question_text_is_not_silently_assigned_to_the_first_group() -> None:
    definition = library.build_library_definition(
        "c" * 64,
        {
            "query_groups": [
                {"name": "关键词一", "items": [{"text": "重复问题"}]},
                {"name": "关键词二", "items": [{"text": "重复问题"}]},
            ]
        },
    )

    by_question, unmapped = library._partition_answers(
        definition,
        [{"pub_id": "ans_ambiguous", "query_text": "重复问题"}],
    )

    assert all(not rows for rows in by_question.values())
    assert [row["pub_id"] for row in unmapped] == ["ans_ambiguous"]


def test_directory_pagination_rejects_an_incomplete_snapshot_pair() -> None:
    with pytest.raises(HTTPException) as raised:
        customer_router.customer_answer_library(
            response=Response(),
            project_pub_id="prj_library",
            start=date(2026, 8, 1),
            end=date(2026, 8, 19),
            search=None,
            model=None,
            region=None,
            mode=None,
            snapshot_id=f"als_{'a' * 24}",
            snapshot_at=None,
            metric_snapshot_set_pub_id=None,
            metric_snapshot_set_hash=None,
            offset=8,
            limit=8,
            principal=Principal(
                subject="customer-safe",
                role=Role.CUSTOMER,
                tenant_pub_id="tnt_safe",
                user_pub_id="usr_safe",
            ),
        )

    assert raised.value.status_code == 422
    assert raised.value.detail == {"code": "incomplete_answer_library_snapshot"}


def test_latest_top_up_config_resolves_to_the_complete_34_group_campaign(
    fake_connection: _Connection,
) -> None:
    top_up_snapshot = {
        **_snapshot(),
        "query_groups": _snapshot()["query_groups"][:1],
        "models": ["豆包"],
        "regions": ["北京"],
        "modes": ["normal"],
    }
    fake_connection.config_rows = [
        {
            "pub_id": "cfv_top_up",
            "revision": 48,
            "snapshot_hash": "d" * 64,
            "snapshot_json": json.dumps(top_up_snapshot),
        },
        {
            "pub_id": "cfv_complete_new",
            "revision": 47,
            "snapshot_hash": "a" * 64,
            "snapshot_json": json.dumps(_snapshot()),
        },
        {
            "pub_id": "cfv_complete_old",
            "revision": 46,
            "snapshot_hash": "a" * 64,
            "snapshot_json": json.dumps(_snapshot()),
        },
    ]
    cutoff = datetime(2026, 8, 19, 3, 0, tzinfo=UTC)

    result = CustomerAnswerLibraryPageView.model_validate(
        library.CustomerAnswerLibraryService(dsn="postgresql://safe").library_page(
            tenant_pub_id="tnt_safe",
            project_pub_id="prj_library",
            start=date(2026, 8, 1),
            end=date(2026, 8, 19),
            snapshot_at=cutoff,
        )
    )

    assert result.snapshot_id == f"als_{'a' * 24}"
    assert result.totals.meta_query_count == 34
    assert result.totals.question_count == 136
    answer_params = next(
        params
        for sql, params in reversed(fake_connection.calls)
        if "SELECT task.pub_id,NULL::text AS query_pub_id" in sql
    )
    assert answer_params[7] == ["cfv_top_up", "cfv_complete_new", "cfv_complete_old"]


def test_explicit_catalog_keeps_the_complete_directory_after_many_micro_top_ups(
    fake_connection: _Connection,
) -> None:
    campaign_started_at = datetime(2026, 8, 12, 18, 0, tzinfo=UTC)
    fake_connection.catalog_row = {
        "pub_id": "cfv_catalog",
        "snapshot_hash": "a" * 64,
        "snapshot_json": json.dumps(_snapshot()),
        "campaign_started_at": campaign_started_at,
        "retired_at": None,
    }
    fake_connection.catalog_config_rows = [
        {"pub_id": "cfv_catalog"},
        {"pub_id": "cfv_micro_top_up_001"},
        {"pub_id": "cfv_micro_top_up_339"},
    ]
    cutoff = datetime(2026, 8, 19, 7, 0, tzinfo=UTC)

    result = CustomerAnswerLibraryPageView.model_validate(
        library.CustomerAnswerLibraryService(dsn="postgresql://safe").library_page(
            tenant_pub_id="tnt_safe",
            project_pub_id="prj_library",
            start=date(2026, 8, 1),
            end=date(2026, 8, 19),
            snapshot_at=cutoff,
        )
    )

    assert result.totals.meta_query_count == 34
    assert result.totals.question_count == 136
    assert not any("LIMIT 1000" in sql for sql, _ in fake_connection.calls)
    lineage_sql, lineage_params = next(
        (sql, params) for sql, params in fake_connection.calls if "mcv.frozen_at>=%s" in sql
    )
    assert "mcv.frozen_at<=%s" in lineage_sql
    assert lineage_params == (
        "project-uuid",
        campaign_started_at,
        cutoff,
        None,
        None,
    )
    answer_params = next(
        params
        for sql, params in reversed(fake_connection.calls)
        if "SELECT task.pub_id,NULL::text AS query_pub_id" in sql
    )
    assert answer_params[7] == [
        "cfv_catalog",
        "cfv_micro_top_up_001",
        "cfv_micro_top_up_339",
    ]


def test_library_page_only_projects_summaries_and_reports_unmapped_rows(
    fake_connection: _Connection,
) -> None:
    cutoff = datetime(2026, 8, 19, 3, 0, tzinfo=UTC)
    document = library.CustomerAnswerLibraryService(dsn="postgresql://safe").library_page(
        tenant_pub_id="tnt_safe",
        project_pub_id="prj_library",
        start=date(2026, 8, 1),
        end=date(2026, 8, 19),
        snapshot_at=cutoff,
        offset=0,
        limit=8,
    )

    result = CustomerAnswerLibraryPageView.model_validate(document)
    assert result.totals.meta_query_count == 34
    assert result.totals.question_count == 136
    assert result.totals.answer_count == 3
    assert result.totals.citation_count == 3
    assert result.totals.unmapped_answer_count == 1
    assert result.page.total == 34
    assert result.page.has_more is True
    assert len(result.data) == 8
    assert result.data[0].questions[0].answer_count == 2
    assert "response_text" not in result.model_dump_json()

    answer_sql = next(
        sql
        for sql, _ in fake_connection.calls
        if "SELECT task.pub_id,NULL::text AS query_pub_id" in sql
    )
    assert "task.created_at<=%s" in answer_sql
    assert "config.pub_id=ANY" in answer_sql
    assert "JOIN analytics.answer answer" not in answer_sql
    assert "analytics.answer_analysis" in answer_sql and "created_at<=%s" in answer_sql
    assert "response_text" not in answer_sql
    assert "response_raw" not in answer_sql
    config_sql, config_params = next(
        (sql, params)
        for sql, params in fake_connection.calls
        if "FROM platform.monitoring_config mc" in sql
    )
    assert "mcv.frozen_at<=%s" in config_sql
    assert config_params[1] == cutoff


def test_meta_runs_and_detail_are_snapshot_bound_and_lazy_loaded(
    fake_connection: _Connection,
) -> None:
    service = library.CustomerAnswerLibraryService(dsn="postgresql://safe")
    definition = library.build_library_definition("a" * 64, _snapshot())
    meta = definition.meta_queries[0]
    question = meta.questions[0]
    cutoff = datetime(2026, 8, 19, 3, 0, tzinfo=UTC)
    common = {
        "tenant_pub_id": "tnt_safe",
        "project_pub_id": "prj_library",
        "snapshot_id": definition.snapshot_id,
        "snapshot_at": cutoff,
        "start": date(2026, 8, 1),
        "end": date(2026, 8, 19),
    }

    meta_result = CustomerAnswerLibraryMetaDetailView.model_validate(
        service.meta_query(meta_query_id=meta.meta_query_id, **common)
    )
    assert len(meta_result.questions) == 4
    assert meta_result.answer_count == 2

    runs_result = CustomerAnswerLibraryQuestionRunsView.model_validate(
        service.question_runs(question_id=question.question_id, offset=0, limit=20, **common)
    )
    assert [item.repeat_index for item in runs_result.data] == [2, 1]
    assert runs_result.data[0].analysis_state == "ready"

    detail_result = CustomerAnswerLibraryDetailView.model_validate(
        service.answer_detail(answer_pub_id="ans_library_01", **common)
    )
    assert detail_result.question_id == question.question_id
    assert detail_result.response_text == "推荐测试品牌。[1](#citation-1)"
    assert detail_result.answer.repeat_index == 1

    body_calls = [
        sql
        for sql, _ in fake_connection.calls
        if "SELECT task.answer_text,task.citations_json" in sql
    ]
    assert len(body_calls) == 1


def test_customer_library_summary_code_has_no_operational_collection_dependencies() -> None:
    source = getsource(library.CustomerAnswerLibraryService).lower()

    for forbidden in (
        "platform_account",
        "browser_instance",
        "workflow_id",
        "error_code",
    ):
        assert forbidden not in source
    assert "platform.collection_task" in source
