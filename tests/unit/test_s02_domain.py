from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from domain.evidence.capability import CaptureCapabilityLease
from domain.evidence.dlp import redact_bytes
from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from domain.intelligence.core import (
    EvidenceRelation,
    SourceAssessment,
    assert_cluster_split,
    extract_claims,
    score_investigation,
)
from domain.metrics.core import MetricRegistry, MetricState
from domain.reporting.freeze import freeze_report, verify_frozen_report
from domain.scoring.analyzer import CitationInput, analyze_answer, canonicalize_url


def test_answer_analysis_and_shared_kpis_are_traceable() -> None:
    result = analyze_answer(
        answer_pub_id="ans_01ABCDEFGHIJKLMNOPQRSTUVWX",
        text="推荐 Acme，它可靠。竞品 Beta 也可考虑。",
        brand="Acme",
        competitors=("Beta",),
        citations=(CitationInput("https://EXAMPLE.com/a?utm_source=x&id=1"),),
        dimensions={"model": "doubao", "region": "beijing"},
    )
    registry = MetricRegistry(metric_version="metrics-v2", scorer_version="scorer-v2")
    mention = registry.compute("mention_rate", [result.fact], filters={"model": "doubao"})
    recommendation = registry.compute(
        "recommendation_rate", [result.fact], filters={"model": "doubao"}
    )
    assert mention.value == Decimal("1")
    assert mention.contributing_answer_pub_ids == ("ans_01ABCDEFGHIJKLMNOPQRSTUVWX",)
    assert len(mention.trace_token) == 64
    assert recommendation.state is MetricState.EXPERIMENTAL
    assert recommendation.value is None
    assert canonicalize_url("https://EXAMPLE.com/a?utm_source=x&id=1") == (
        "https://example.com/a?id=1"
    )


def test_chinese_explicit_ranks_drive_brand_and_competitor_topn() -> None:
    result = analyze_answer(
        answer_pub_id="ans_rank_01ABCDEFGHIJKLMNOP",
        text="Acme 排第一，Beta 排第二十一位。",
        brand="Acme",
        competitors=("Beta",),
        citations=(),
        dimensions={},
    )
    assert result.fact.rank == 1
    assert result.fact.competitor_ranks == {"Beta": 21}
    no_cross_entity_rank = analyze_answer(
        answer_pub_id="ans_rank_02ABCDEFGHIJKLMNOP",
        text="推荐 Acme，Beta 排第二。",
        brand="Acme",
        competitors=("Beta",),
        citations=(),
        dimensions={},
    )
    assert no_cross_entity_rank.fact.rank is None
    assert no_cross_entity_rank.fact.competitor_ranks == {"Beta": 2}


def test_dlp_removes_searchable_secrets() -> None:
    payload = (
        "Cookie: sid=secret\nAuthorization: Bearer abc.def\nOTP: 123456\n"
        "手机号 13800138000\nrefresh_token=oops"
    ).encode()
    result = redact_bytes(payload, mime_type="application/har+json")
    assert set(result.findings) == {
        "authorization",
        "cookie",
        "otp",
        "phone",
        "security_field",
    }
    for secret in (b"secret", b"abc.def", b"123456", b"13800138000", b"oops"):
        assert secret not in result.redacted


def test_private_provenance_public_projection_hides_account_dimensions() -> None:
    provenance = RedactedProvenance(
        platform_account_pub_id="acct_01ABCDEFGHIJKLMNOPQRSTUV",
        browser_profile_version_pub_id="bpv_01ABCDEFGHIJKLMNOPQRSTUV",
        session_event_pub_id="evt_01ABCDEFGHIJKLMNOPQRSTUV",
        channel=CaptureChannel.WEB,
        authorization_scope=("read",),
        adapter_version="adapter-v1",
        capture_time=datetime.now(UTC),
        access_class=AccessClass.PAID_OR_ORGANIZATION,
        authorized_session_capture=True,
    )
    projection = provenance.public_projection()
    assert projection["platform_account_pub_id"] is None
    assert projection["browser_profile_version_pub_id"] is None
    assert projection["session_event_pub_id"] is None
    assert projection["authorized_session_capture"] is True


def test_capability_lease_rejects_wrong_scope_expiry_revocation_and_account_context() -> None:
    now = datetime.now(UTC)
    base = dict(
        lease_pub_id="lease_01ABCDEFGHIJKLMNOPQRST",
        tenant_pub_id="tnt_01ABCDEFGHIJKLMNOPQRSTUV",
        platform_account_pub_id="acct_01ABCDEFGHIJKLMNOPQRSTUV",
        allowed_domains=("example.com",),
        allowed_actions=("capture",),
        authorization_scope=("read",),
        expires_at=now + timedelta(minutes=5),
        revoked_at=None,
        subject_workflow_id="evidence-capture/tnt/x/op",
        signature_verified=True,
    )
    lease = CaptureCapabilityLease(**base)
    lease.authorize(
        tenant_pub_id=base["tenant_pub_id"],
        platform_account_pub_id=base["platform_account_pub_id"],
        target_url="https://docs.example.com/a",
        action="capture",
        workflow_id=base["subject_workflow_id"],
        now=now,
    )
    with pytest.raises(PermissionError):
        lease.authorize(
            tenant_pub_id=base["tenant_pub_id"],
            platform_account_pub_id=base["platform_account_pub_id"],
            target_url="https://evil.example.net",
            action="capture",
            workflow_id=base["subject_workflow_id"],
            now=now,
        )
    with pytest.raises(PermissionError):
        CaptureCapabilityLease(**(base | {"expires_at": now - timedelta(seconds=1)})).authorize(
            tenant_pub_id=base["tenant_pub_id"],
            platform_account_pub_id=base["platform_account_pub_id"],
            target_url="https://example.com",
            action="capture",
            workflow_id=base["subject_workflow_id"],
            now=now,
        )
    with pytest.raises(PermissionError):
        CaptureCapabilityLease(**(base | {"revoked_at": now})).authorize(
            tenant_pub_id=base["tenant_pub_id"],
            platform_account_pub_id=base["platform_account_pub_id"],
            target_url="https://example.com",
            action="capture",
            workflow_id=base["subject_workflow_id"],
            now=now,
        )
    with pytest.raises(PermissionError, match="another account"):
        lease.authorize(
            tenant_pub_id=base["tenant_pub_id"],
            platform_account_pub_id="acct_01ZZZZZZZZZZZZZZZZZZZZZZ",
            target_url="https://example.com",
            action="capture",
            workflow_id=base["subject_workflow_id"],
            now=now,
        )


def test_anti_geo_is_probabilistic_conservative_and_cluster_split_safe() -> None:
    claims = extract_claims("某品牌声称市场第一。单篇帖子不能证明品牌实施 GEO。")
    assert len(claims) == 2
    single_source = score_investigation(
        assessments=(
            SourceAssessment(
                "src_1",
                "cluster_1",
                EvidenceRelation.SUPPORTS,
                Decimal("1"),
            ),
        ),
        content_feature_score=Decimal("1"),
        propagation_feature_score=Decimal("1"),
        circular_citation_risk=Decimal("0"),
    )
    assert single_source.probability <= Decimal("0.49")
    assert single_source.requires_human_verdict
    with pytest.raises(ValueError, match="leakage"):
        assert_cluster_split(["cluster_1"], ["cluster_1"])


def test_report_freeze_detects_drift() -> None:
    now = datetime.now(UTC)
    rows = [{"answer_pub_id": "ans_a", "mentioned": True}]
    frozen = freeze_report(
        window_start=now - timedelta(days=7),
        window_end=now,
        filters={"model": "doubao"},
        metric_version="metrics-v2",
        scorer_version="scorer-v2",
        fact_rows=rows,
    )
    verify_frozen_report(frozen, rows)
    with pytest.raises(ValueError, match="drifted"):
        verify_frozen_report(frozen, rows + [{"answer_pub_id": "ans_b", "mentioned": False}])
