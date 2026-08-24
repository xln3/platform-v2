from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import geo_platform.customer_services.router as customer_services_router
import pytest
from fastapi import HTTPException, Response
from geo_platform.customer_services.router import (
    _observation_state,
    _official_site_stage,
    _safe_delivery,
)
from geo_platform.identity.policy import Principal, Role
from geo_platform.source_intelligence.router import WChunkReviewCreate, list_sites, review_w_chunk


class _FakeRows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class _CustomerServicesConnection:
    def execute(self, query: str, _params: object = None) -> _FakeRows:
        if "SELECT id FROM platform.project" in query:
            return _FakeRows([{"id": "00000000-0000-0000-0000-000000000101"}])
        if "FROM platform.project_service_entitlement" in query:
            return _FakeRows(
                [
                    {
                        "service_code": "ranking_test",
                        "catalog_version": "quotation-services-v2",
                        "state": "active",
                    }
                ]
            )
        if "FROM reporting.formal_report_output" in query:
            return _FakeRows([])
        if "AS answer_count FROM platform.collection_task" in query:
            return _FakeRows([{"answer_count": 3}])
        if "WITH official_host AS" in query:
            return _FakeRows(
                [
                    {
                        "u_count": 0,
                        "v_count": 0,
                        "w_count": 0,
                        "v_observed": 0,
                        "v_partial": 0,
                        "v_unobserved": 0,
                        "w_pending": 0,
                    }
                ]
            )
        if "FROM platform.answer_retrieval_event WHERE project_id" in query:
            return _FakeRows([{"observed": 0, "partial": 0, "unobserved": 1}])
        raise AssertionError(query)


@contextmanager
def _customer_services_connection(_tenant_pub_id: str) -> Iterator[_CustomerServicesConnection]:
    yield _CustomerServicesConnection()


def test_customer_cannot_cross_internal_source_directory_permission_boundary() -> None:
    principal = Principal(
        subject="customer-subject",
        role=Role.CUSTOMER,
        tenant_pub_id="tnt_test",
        user_pub_id="usr_customer",
    )

    with pytest.raises(HTTPException) as raised:
        list_sites("prj_test", cursor=None, limit=50, principal=principal)

    assert raised.value.status_code == 403
    assert raised.value.detail == {"code": "permission_denied"}


def test_customer_cannot_review_internal_w_evidence() -> None:
    principal = Principal(
        subject="customer-subject",
        role=Role.CUSTOMER,
        tenant_pub_id="tnt_test",
        user_pub_id="usr_customer",
    )

    with pytest.raises(HTTPException) as raised:
        review_w_chunk(
            "prj_test",
            "wch_test",
            WChunkReviewCreate(decision="accepted", rationale="复核通过"),
            "review-idempotency-key",
            principal,
        )

    assert raised.value.status_code == 403
    assert raised.value.detail == {"code": "permission_denied"}


def test_customer_delivery_projection_drops_secret_bearing_title() -> None:
    base = {
        "report_pub_id": "rpt_safe",
        "report_version_pub_id": "rptv_safe",
        "published_at": datetime(2026, 8, 20, tzinfo=UTC),
        "delivered_at": None,
        "confirmed_at": None,
    }

    assert _safe_delivery({**base, "title": "Authorization: Bearer customer-secret"}) is None
    projected = _safe_delivery({**base, "title": "官网引用效率正式报告"})
    assert projected is not None
    assert projected.title == "官网引用效率正式报告"


def test_customer_official_site_projection_never_turns_unknown_into_zero() -> None:
    assert _observation_state(observed=0, partial=0, unobserved=4) == "unobserved"
    assert _observation_state(observed=3, partial=0, unobserved=1) == "partial"
    assert (
        _official_site_stage(
            u_count=0,
            u_observation="unobserved",
            v_count=0,
            v_observation="unobserved",
            w_count=0,
            w_pending=0,
        )
        == "u_unobserved"
    )
    assert (
        _official_site_stage(
            u_count=7,
            u_observation="partial",
            v_count=3,
            v_observation="observed",
            w_count=1,
            w_pending=0,
        )
        == "u_partially_observed"
    )


def test_customer_service_answer_count_accepts_dict_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(customer_services_router, "_connection", _customer_services_connection)
    principal = Principal(
        subject="customer-subject",
        role=Role.CUSTOMER,
        tenant_pub_id="tnt_test",
        user_pub_id="usr_customer",
    )

    result = customer_services_router.get_customer_services(
        "prj_test",
        Response(),
        principal,
    )

    ranking = result.services[0]
    assert ranking.entitlement_state == "active"
    assert ranking.summary is not None
    assert ranking.summary.answer_count == 3
