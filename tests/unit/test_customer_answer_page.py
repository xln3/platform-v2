from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from inspect import getsource
from typing import Any

import pytest
from fastapi import HTTPException
from geo_platform.customer_dashboard import service as customer_service
from geo_platform.customer_dashboard.router import customer_answers
from geo_platform.customer_dashboard.schemas import CustomerAnswerPageView
from geo_platform.identity.policy import Principal, Role
from pydantic import ValidationError


def _principal() -> Principal:
    return Principal(
        subject="customer-answer-reader",
        role=Role.CUSTOMER,
        tenant_pub_id="tnt_safe",
        user_pub_id="usr_safe",
    )


def _answer_document() -> dict[str, Any]:
    return {
        "schema_version": "customer-answer-page-v1",
        "project_pub_id": "prj_safe",
        "data": [
            {
                "answer_pub_id": "ans_latest",
                "query_pub_id": "qry_hash_b5855173086854844b54",
                "query_text": "网络安全厂商有哪些推荐？",
                "response_text": "建议优先考虑盛邦安全。",
                "model": "DeepSeek",
                "region": "CN",
                "mode": "web",
                "capture_time": datetime(2026, 8, 17, 8, 0, tzinfo=UTC),
                "mentioned": True,
                "rank": 1,
                "sentiment": "positive",
                "recommended": True,
                "citation_count": 3,
            }
        ],
        "page": {"total": 2, "offset": 0, "limit": 1, "has_more": True},
    }


def test_customer_answer_schema_is_strict_and_bounded() -> None:
    result = CustomerAnswerPageView.model_validate(_answer_document())

    assert result.schema_version == "customer-answer-page-v1"
    assert result.data[0].response_text == "建议优先考虑盛邦安全。"
    assert result.page.total == 2
    assert result.page.has_more is True

    invalid = _answer_document()
    invalid["data"][0]["run_pub_id"] = "run_private"
    with pytest.raises(ValidationError):
        CustomerAnswerPageView.model_validate(invalid)


def test_customer_answer_route_forwards_all_customer_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_answer_page(self: object, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return _answer_document()

    monkeypatch.setattr(customer_service.CustomerDashboardService, "answer_page", fake_answer_page)

    result = customer_answers(
        project_pub_id="prj_safe",
        start=date(2026, 8, 1),
        end=date(2026, 8, 17),
        search="盛邦安全",
        model="DeepSeek",
        region="CN",
        mode="web",
        mentioned=True,
        sentiment="positive",
        offset=0,
        limit=1,
        principal=_principal(),
    )

    assert result.data[0].answer_pub_id == "ans_latest"
    assert captured == {
        "tenant_pub_id": "tnt_safe",
        "project_pub_id": "prj_safe",
        "start": date(2026, 8, 1),
        "end": date(2026, 8, 17),
        "search": "盛邦安全",
        "model": "DeepSeek",
        "region": "CN",
        "mode": "web",
        "mentioned": True,
        "sentiment": "positive",
        "offset": 0,
        "limit": 1,
    }


def test_customer_answer_route_rejects_invalid_window() -> None:
    with pytest.raises(HTTPException) as error:
        customer_answers(
            project_pub_id="prj_safe",
            start=date(2026, 8, 18),
            end=date(2026, 8, 17),
            search=None,
            model=None,
            region=None,
            mode=None,
            mentioned=None,
            sentiment=None,
            offset=0,
            limit=20,
            principal=_principal(),
        )
    assert error.value.status_code == 422
    detail: Any = error.value.detail
    assert detail == {"code": "invalid_analytics_window"}


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

    def execute(self, sql: str, parameters: tuple[object, ...]) -> _Result:
        self.calls.append((sql, parameters))
        if "FROM platform.project" in sql:
            return _Result(one={"id": "project-uuid", "name": "项目", "brand_name": "盛邦安全"})
        if "SELECT count(*) AS total" in sql:
            return _Result(one={"total": 3})
        if "SELECT answer_pub_id,analysis_run_pub_id,ordinal" in sql:
            return _Result(
                many=[
                    {
                        "answer_pub_id": "ans_20260817",
                        "analysis_run_pub_id": "ana_latest",
                        "ordinal": 1,
                        "platform_ordinal": 1,
                        "ordinal_base": 1,
                    }
                ]
            )
        return _Result(
            many=[
                {
                    "pub_id": "ans_20260817",
                    "query_pub_id": "qry_hash_b5855173086854844b54",
                    "query_text": "网络安全品牌推荐",
                    "response_text": "推荐盛邦安全，综合能力突出。",
                    "response_raw": (
                        "推荐盛邦安全，综合能力突出。[citation:1]\n\n"
                        "参考来源：\n1. 示例来源\nhttps://example.com/article"
                    ),
                    "model": "DeepSeek",
                    "region": "CN",
                    "mode": "web",
                    "capture_time": datetime(2026, 8, 17, 8, 0, tzinfo=UTC),
                    "analysis_run_pub_id": "ana_latest",
                    "mentioned": True,
                    "rank": None,
                    "sentiment": "positive",
                    "recommended": None,
                    "citation_count": 4,
                }
            ]
        )


def test_answer_page_maps_real_facts_and_infers_missing_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection()

    @contextmanager
    def fake_connection(dsn: str, tenant_pub_id: str) -> Iterator[_Connection]:
        assert dsn == "postgresql://safe"
        assert tenant_pub_id == "tnt_safe"
        yield connection

    monkeypatch.setattr(customer_service, "_customer_connection", fake_connection)
    document = customer_service.CustomerDashboardService(dsn="postgresql://safe").answer_page(
        tenant_pub_id="tnt_safe",
        project_pub_id="prj_safe",
        start=date(2026, 8, 1),
        end=date(2026, 8, 17),
        search="  盛邦安全  ",
        model="DeepSeek",
        region="CN",
        mode="web",
        mentioned=True,
        sentiment="positive",
        offset=1,
        limit=1,
    )

    result = CustomerAnswerPageView.model_validate(document)
    assert result.data[0].recommended is True
    assert result.data[0].citation_count == 4
    assert result.data[0].response_text == "推荐盛邦安全，综合能力突出。[1](#citation-1)"
    assert "参考来源" not in result.data[0].response_text
    assert result.page.total == 3
    assert result.page.offset == 1
    assert result.page.has_more is True

    count_sql, count_parameters = connection.calls[1]
    page_sql, page_parameters = connection.calls[2]
    assert "a.eligible" in count_sql
    assert "ORDER BY created_at DESC,id DESC LIMIT 1" in count_sql
    assert "STRPOS" in count_sql
    assert "ORDER BY ca.capture_time DESC,ca.pub_id DESC" in page_sql
    assert "cf.analysis_run_pub_id=ca.analysis_run_pub_id" in page_sql
    assert count_parameters[-3:] == ("盛邦安全", "盛邦安全", "盛邦安全")
    assert page_parameters[-3:] == ("tnt_safe", 1, 1)


def test_answer_page_uses_only_customer_fact_tables_and_fields() -> None:
    source = getsource(customer_service.CustomerDashboardService.answer_page).lower()

    for forbidden in (
        "collection_task",
        "collection_run",
        "total_tasks",
        "completed_tasks",
        "failed_tasks",
        "success_rate",
        "platform_account",
        "browser_instance",
        "error_code",
    ):
        assert forbidden not in source


def test_answer_page_missing_project_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    class MissingProjectConnection:
        def execute(self, sql: str, parameters: tuple[object, ...]) -> _Result:
            return _Result(one=None)

    @contextmanager
    def fake_connection(dsn: str, tenant_pub_id: str) -> Iterator[MissingProjectConnection]:
        yield MissingProjectConnection()

    monkeypatch.setattr(customer_service, "_customer_connection", fake_connection)
    with pytest.raises(LookupError, match="project_not_found"):
        customer_service.CustomerDashboardService(dsn="postgresql://safe").answer_page(
            tenant_pub_id="tnt_safe",
            project_pub_id="prj_other_tenant",
            start=date(2026, 8, 1),
            end=date(2026, 8, 17),
        )
