from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from geo_platform.customer_services.router import (
    _observation_state,
    _official_site_stage,
    _safe_delivery,
)
from geo_platform.identity.policy import Principal, Role
from geo_platform.source_intelligence.router import list_sites


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
