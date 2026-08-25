from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from geo_platform.pagination import decode_keyset_cursor, encode_keyset_cursor, numbered_page

_NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
_ANCHOR = datetime(2026, 8, 23, 8, 30, 0, 123456, tzinfo=UTC)


def token(*, tenant: str = "tnt_a", project: str | None = "prj_a") -> str:
    return encode_keyset_cursor(
        kind="collection-runs",
        tenant_pub_id=tenant,
        filters={"project_pub_id": project},
        created_at=_ANCHOR,
        pub_id="run_anchor",
        now=_NOW,
    )


def test_keyset_cursor_round_trip_preserves_immutable_composite_anchor() -> None:
    decoded = decode_keyset_cursor(
        token(),
        kind="collection-runs",
        tenant_pub_id="tnt_a",
        filters={"project_pub_id": "prj_a"},
        now=_NOW + timedelta(minutes=1),
    )

    assert decoded.created_at == _ANCHOR
    assert decoded.pub_id == "run_anchor"
    assert "tnt_a" not in token()


@pytest.mark.parametrize(
    ("kind", "tenant", "filters"),
    [
        ("schedules", "tnt_a", {"project_pub_id": "prj_a"}),
        ("collection-runs", "tnt_b", {"project_pub_id": "prj_a"}),
        ("collection-runs", "tnt_a", {"project_pub_id": "prj_b"}),
        ("collection-runs", "tnt_a", {"project_pub_id": None}),
    ],
)
def test_cursor_is_bound_to_endpoint_tenant_and_filters(
    kind: str, tenant: str, filters: dict[str, str | None]
) -> None:
    with pytest.raises(HTTPException) as caught:
        decode_keyset_cursor(
            token(),
            kind=kind,
            tenant_pub_id=tenant,
            filters=filters,
            now=_NOW,
        )

    assert caught.value.status_code == 422
    assert caught.value.detail == {"code": "invalid_cursor"}


def test_cursor_rejects_tampering_and_expiry() -> None:
    signed = token()
    with pytest.raises(HTTPException) as tampered:
        decode_keyset_cursor(
            signed[:-1] + ("A" if signed[-1] != "A" else "B"),
            kind="collection-runs",
            tenant_pub_id="tnt_a",
            filters={"project_pub_id": "prj_a"},
            now=_NOW,
        )
    assert tampered.value.detail == {"code": "invalid_cursor"}

    with pytest.raises(HTTPException) as expired:
        decode_keyset_cursor(
            signed,
            kind="collection-runs",
            tenant_pub_id="tnt_a",
            filters={"project_pub_id": "prj_a"},
            now=_NOW + timedelta(hours=24, seconds=1),
        )
    assert expired.value.detail == {"code": "cursor_expired"}


@pytest.mark.parametrize(
    ("requested", "total", "expected_page", "expected_pages", "expected_offset"),
    [
        (1, 0, 1, 0, 0),
        (1, 1, 1, 1, 0),
        (1, 4, 1, 1, 0),
        (2, 5, 2, 2, 4),
        (3, 9, 3, 3, 8),
        (999, 9, 3, 3, 8),
    ],
)
def test_numbered_page_reports_full_totals_and_clamps_shrinking_collections(
    requested: int,
    total: int,
    expected_page: int,
    expected_pages: int,
    expected_offset: int,
) -> None:
    result = numbered_page(requested_page=requested, page_size=4, total_count=total)

    assert result.page == expected_page
    assert result.page_size == 4
    assert result.total_count == total
    assert result.total_pages == expected_pages
    assert result.offset == expected_offset
