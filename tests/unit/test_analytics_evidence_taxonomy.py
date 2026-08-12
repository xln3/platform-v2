"""Answer evidence taxonomy fail-closed helpers (no DB/network)."""

from __future__ import annotations

from datetime import UTC, datetime

from geo_platform.analytics.router import (
    AnswerEvidenceView,
    EvidenceAnchorView,
    _has_valid_brand_bbox,
    _is_brand_mention_evidence,
    _safe_bbox,
)


def _anchor(bbox: object) -> EvidenceAnchorView:
    return EvidenceAnchorView(
        pub_id="anch_test",
        text_start=0,
        text_end=4,
        bbox=_safe_bbox(bbox),
        page_number=None,
        quote_hash="0" * 64,
    )


def _bbox(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "x": 1,
        "y": 2,
        "width": 80,
        "height": 24,
        "confidence": 1,
        "image_width": 700,
        "image_height": 120,
    }
    value.update(updates)
    return value


def test_brand_bbox_requires_complete_positive_finite_geometry() -> None:
    assert _has_valid_brand_bbox(_anchor(_bbox()))
    for malicious in (
        _bbox(x=True),
        _bbox(width=float("nan")),
        _bbox(width=float("inf")),
        _bbox(x=-1),
        _bbox(width=0),
        {"x": 1, "y": 2, "width": 80, "height": 24},
        _bbox(width=2_000_000),
        _bbox(confidence=2),
        _bbox(x=650, width=80),
        _bbox(y=110, height=24),
        _bbox(image_width=0),
    ):
        assert _has_valid_brand_bbox(_anchor(malicious)) is False


def test_bbox_projection_allow_lists_geometry_fields() -> None:
    projected = _safe_bbox(
        {
            "x": 1,
            "y": 2,
            "width": 80,
            "height": 24,
            "confidence": 0.9,
            "image_width": 700,
            "image_height": 120,
            "html": "bad",
        }
    )
    assert projected == {
        "x": 1.0,
        "y": 2.0,
        "width": 80.0,
        "height": 24.0,
        "confidence": 0.9,
        "image_width": 700.0,
        "image_height": 120.0,
    }


def test_malicious_bbox_never_enters_brand_mention_evidence_group() -> None:
    item = AnswerEvidenceView(
        pub_id="evd_bad",
        relation_type="brand_mention_source_snapshot",
        kind="source_screenshot",
        access_class="customer_private",
        sha256="0" * 64,
        mime_type="image/png",
        byte_size=500,
        source_url="https://example.com",
        capture_time=datetime(2026, 8, 12, tzinfo=UTC),
        anchors=[_anchor(_bbox(width=float("inf")))],
    )

    assert _is_brand_mention_evidence(item) is False


def test_only_dimension_bound_png_enters_brand_mention_evidence_group() -> None:
    item = AnswerEvidenceView(
        pub_id="evd_good",
        relation_type="brand_mention_source_snapshot",
        kind="source_screenshot",
        access_class="customer_private",
        sha256="0" * 64,
        mime_type="image/png",
        byte_size=500,
        source_url="https://example.com",
        capture_time=datetime(2026, 8, 12, tzinfo=UTC),
        anchors=[_anchor(_bbox())],
    )

    assert _is_brand_mention_evidence(item) is True
    assert _is_brand_mention_evidence(item.model_copy(update={"byte_size": 87})) is False
    assert _is_brand_mention_evidence(item.model_copy(update={"mime_type": "image/webp"})) is False
