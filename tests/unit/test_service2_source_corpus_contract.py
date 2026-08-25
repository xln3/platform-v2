from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

import pytest
from fastapi import HTTPException, Response
from geo_platform.identity.policy import Principal, Role
from geo_platform.service2_corpus.router import freeze_batch, lifecycle_action
from geo_platform.service2_corpus.schemas import (
    AttributionInput,
    BatchCreate,
    CorpusProcessingState,
    CorpusReviewState,
    FindingCreate,
)
from geo_platform.service2_corpus.service import (
    EvidenceInvalid,
    Service2CorpusService,
    _factcheck_manifest_projection,
    _fetch_projection,
    _relation_version_hash,
    _safe_evidence_projection,
)
from pydantic import TypeAdapter, ValidationError

from domain.scoring.service2_source_corpus import (
    AttributionConfidence,
    DisparagementLevel,
    FactAnchorState,
    Ledger,
    OrthogonalFlags,
    RelationDirection,
    RelationFindingCandidate,
    ValidationStatus,
    VisualValidationStatus,
    attribution_wording_allowed,
    customer_case_eligible,
    factcheck_case_ready,
    has_public_evidence_candidate,
    has_reviewable_evidence,
    validate_relation_finding,
    validated_visual_bbox,
    visual_anchor_matches_quote,
)

SOURCE_TEXT = "前文。甲公司不如乙公司可靠。后文。"
QUOTE = "甲公司不如乙公司可靠"
QUOTE_START = SOURCE_TEXT.index(QUOTE)
SOURCE_HASH = sha256(SOURCE_TEXT.encode("utf-8")).hexdigest()


def _candidate(**overrides: object) -> RelationFindingCandidate:
    base = RelationFindingCandidate(
        ledger=Ledger.STATEMENT,
        level=DisparagementLevel.L1,
        relation_direction=RelationDirection.TARGET_NEGATIVE,
        textual_speaker="页面作者",
        target_entity="甲公司",
        beneficiary_entity=None,
        quote=QUOTE,
        quote_start=QUOTE_START,
        quote_end=QUOTE_START + len(QUOTE),
        context=SOURCE_TEXT,
        context_start=0,
        context_end=len(SOURCE_TEXT),
        snapshot_text_sha256=SOURCE_HASH,
        is_disparagement=False,
        fact_anchor_state=FactAnchorState.PRESENT,
        flags=OrthogonalFlags(direct_target_negative=True),
    )
    return replace(base, **overrides)


def _failures(candidate: RelationFindingCandidate) -> tuple[str, ...]:
    return validate_relation_finding(
        candidate,
        source_text=SOURCE_TEXT,
        snapshot_text_sha256=SOURCE_HASH,
    )


def test_all_u_processing_projection_keeps_every_occurrence_and_failure_in_denominator() -> None:
    rows = [
        {
            "snapshot_state": "succeeded",
            "snapshot_id": f"snapshot-{index}",
            "body_object_key": f"cas-{index}",
            "text_sha256": SOURCE_HASH,
            "u_state": "observed",
        }
        for index in range(4)
    ]
    rows.append({"snapshot_state": "blocked", "u_state": "observed"})

    projections = [_fetch_projection(row) for row in rows]

    assert len(projections) == 5
    assert [state[1] for state in projections] == ["queued"] * 4 + ["blocked"]
    # No ownership, publisher, commissioner, entity or finding field participates
    # in the projection, so zero attribution evidence cannot create an empty pool.
    assert all("publisher" not in row and "commissioner" not in row for row in rows)


def test_l1_is_deliverable_statement_but_never_disparagement() -> None:
    assert _failures(_candidate()) == ()
    assert "l0_l1_cannot_be_disparagement" in _failures(_candidate(is_disparagement=True))


def test_peer_elevation_or_scope_narrowing_alone_is_not_disparagement() -> None:
    peer_only = _candidate(
        level=DisparagementLevel.L2A,
        is_disparagement=True,
        flags=OrthogonalFlags(peer_elevated=True, scope_narrowed=True),
    )
    failures = _failures(peer_only)
    assert "l2a_requires_direct_target_negative" in failures
    assert "peer_elevation_or_observation_flag_alone_is_not_disparagement" in failures
    assert "scope_narrowed_alone_is_not_disparagement" in failures


def test_l4_is_reserved_but_rejected_until_the_authoritative_mapping_exists() -> None:
    failures = _failures(
        _candidate(
            level=DisparagementLevel.L4,
            is_disparagement=True,
            fact_anchor_state=FactAnchorState.ABSENT,
            flags=OrthogonalFlags(direct_target_negative=True),
        )
    )
    assert "l4_authoritative_taxonomy_mapping_unavailable" in failures


@pytest.mark.parametrize(
    ("candidate", "required_failures"),
    [
        (
            _candidate(
                level=DisparagementLevel.L2B,
                is_disparagement=True,
                relation_direction=RelationDirection.TARGET_DEGRADED,
                fact_anchor_state=FactAnchorState.PRESENT,
                flags=OrthogonalFlags(),
            ),
            {"l2b_requires_secondary_position", "l2b_requires_missing_fact_anchor"},
        ),
        (
            _candidate(
                level=DisparagementLevel.L3A,
                is_disparagement=True,
                relation_direction=RelationDirection.TARGET_COMPARED,
                flags=OrthogonalFlags(comparison_present=True),
            ),
            {"l3a_requires_manipulated_comparison", "l3a_requires_comparison_dimensions"},
        ),
        (
            _candidate(
                level=DisparagementLevel.L3B,
                is_disparagement=True,
                relation_direction=RelationDirection.TARGET_OMITTED,
                flags=OrthogonalFlags(),
            ),
            {"l3b_requires_key_fact_omission", "l3b_requires_omitted_facts"},
        ),
    ],
)
def test_l2b_l3a_l3b_fail_closed_without_complete_elements(
    candidate: RelationFindingCandidate,
    required_failures: set[str],
) -> None:
    assert required_failures <= set(_failures(candidate))


def test_complete_l2b_l3a_and_l3b_pass_the_versioned_contract() -> None:
    assert (
        _failures(
            _candidate(
                level=DisparagementLevel.L2B,
                is_disparagement=True,
                relation_direction=RelationDirection.TARGET_DEGRADED,
                fact_anchor_state=FactAnchorState.ABSENT,
                flags=OrthogonalFlags(secondary_position=True),
            )
        )
        == ()
    )
    assert (
        _failures(
            _candidate(
                level=DisparagementLevel.L3A,
                is_disparagement=True,
                relation_direction=RelationDirection.TARGET_COMPARED,
                flags=OrthogonalFlags(comparison_present=True, comparison_manipulated=True),
                comparison_dimensions=("样本口径",),
            )
        )
        == ()
    )
    assert (
        _failures(
            _candidate(
                level=DisparagementLevel.L3B,
                is_disparagement=True,
                relation_direction=RelationDirection.TARGET_OMITTED,
                flags=OrthogonalFlags(key_fact_omitted=True),
                omitted_facts=("适用范围",),
            )
        )
        == ()
    )


def test_speaker_and_level_direction_are_required_for_reviewable_relations() -> None:
    assert "textual_speaker_required" in _failures(_candidate(textual_speaker="  "))
    assert "l2a_requires_target_negative_direction" in _failures(
        _candidate(
            level=DisparagementLevel.L2A,
            is_disparagement=True,
            relation_direction=RelationDirection.CONTEXT_ONLY,
            flags=OrthogonalFlags(direct_target_negative=True),
        )
    )
    assert "l3a_requires_target_compared_direction" in _failures(
        _candidate(
            level=DisparagementLevel.L3A,
            is_disparagement=True,
            relation_direction=RelationDirection.TARGET_NEGATIVE,
            flags=OrthogonalFlags(comparison_present=True, comparison_manipulated=True),
            comparison_dimensions=("样本口径",),
        )
    )
    assert "l3b_requires_target_omitted_direction" in _failures(
        _candidate(
            level=DisparagementLevel.L3B,
            is_disparagement=True,
            relation_direction=RelationDirection.TARGET_NEGATIVE,
            flags=OrthogonalFlags(key_fact_omitted=True),
            omitted_facts=("适用范围",),
        )
    )


def test_quote_offset_context_and_snapshot_hash_mismatches_fail_closed() -> None:
    failures = _failures(
        _candidate(
            quote_start=0,
            quote_end=len(QUOTE),
            snapshot_text_sha256="0" * 64,
        )
    )
    assert "finding_snapshot_hash_mismatch" in failures
    assert "quote_not_exact_snapshot_substring" in failures
    assert "context_does_not_contain_quote" in _failures(
        _candidate(
            context=SOURCE_TEXT[QUOTE_START + 1 :],
            context_start=QUOTE_START + 1,
            context_end=len(SOURCE_TEXT),
        )
    )

    assert "snapshot_text_hash_mismatch" in validate_relation_finding(
        _candidate(),
        source_text=SOURCE_TEXT + "篡改",
        snapshot_text_sha256=SOURCE_HASH,
    )


def test_exposure_ledger_cannot_be_promoted_to_a_disparagement_statement() -> None:
    exposure = _candidate(
        ledger=Ledger.EXPOSURE,
        level=DisparagementLevel.L2A,
        relation_direction=RelationDirection.TARGET_NEGATIVE,
        is_disparagement=True,
    )
    failures = set(_failures(exposure))
    assert "exposure_ledger_cannot_be_disparagement" in failures
    assert "exposure_ledger_level_must_be_l0" in failures
    assert "exposure_ledger_direction_must_be_context_only" in failures


def test_attribution_is_independent_and_unknown_cannot_name_a_party() -> None:
    failures = _failures(
        _candidate(
            publisher_party="乙公司",
            publisher_confidence=AttributionConfidence.UNKNOWN,
        )
    )
    assert "publisher_party_requires_attribution_evidence" in failures
    with pytest.raises(ValidationError, match="unknown_attribution_cannot_name_party"):
        AttributionInput(party="乙公司", confidence="unknown")
    with pytest.raises(
        ValidationError, match="attribution_confidence_requires_reviewable_evidence"
    ):
        AttributionInput(party="乙公司", confidence="verified")
    assert not attribution_wording_allowed(AttributionConfidence.UNKNOWN, ())
    assert attribution_wording_allowed(
        AttributionConfidence.PROBABLE,
        (
            {
                "evidence_pub_id": "evd_reviewable",
                "verification_status": "verified",
                "content_sha256": "b" * 64,
                "retrieved_at": "2026-08-25T00:00:00+00:00",
            },
        ),
    )


def test_customer_case_gate_requires_exact_visual_and_human_acceptance() -> None:
    required = {
        "ledger": Ledger.STATEMENT,
        "level": DisparagementLevel.L1,
        "validation_status": ValidationStatus.EXACT,
        "visual_status": VisualValidationStatus.VERIFIED,
        "review_state": "accepted",
        "factcheck_verdict": "unverifiable",
        "factcheck_evidence": (),
        "factcheck_boundary": "当前公开材料不足以判真。",
    }
    assert customer_case_eligible(**required)
    for field, value in (
        ("ledger", Ledger.EXPOSURE),
        ("validation_status", ValidationStatus.EXPERIMENTAL),
        ("visual_status", VisualValidationStatus.UNAVAILABLE),
        ("review_state", "unreviewed"),
        ("factcheck_boundary", None),
    ):
        assert not customer_case_eligible(**{**required, field: value})
    assert not factcheck_case_ready(verdict="supported", evidence=(), boundary=None)
    assert not factcheck_case_ready(verdict="supported", evidence=({},), boundary=None)
    assert not factcheck_case_ready(
        verdict="supported",
        evidence=({"url": "https://facts.example.com/source"},),
        boundary=None,
    )
    assert factcheck_case_ready(
        verdict="supported",
        evidence=(
            {
                "url": "https://facts.example.com/source",
                "evidence_pub_id": "evd_verified",
                "verification_status": "verified",
                "content_sha256": "a" * 64,
                "retrieved_at": "2026-08-25T00:00:00+00:00",
            },
        ),
        boundary=None,
    )
    assert not has_reviewable_evidence(({"evidence_pub_id": "evd_fabricated"},))
    assert not has_reviewable_evidence(({"url": "http://127.0.0.1/internal"},))
    assert has_public_evidence_candidate(({"url": "https://facts.example/source"},))
    assert not has_public_evidence_candidate(({"url": "http://127.0.0.1/internal"},))
    assert not has_public_evidence_candidate(({"url": "http://localhost/internal"},))
    assert not has_public_evidence_candidate(({"url": "https://user:pass@example.com"},))
    assert not has_reviewable_evidence(({"note": "人工认为可靠"},))
    assert not has_reviewable_evidence(({"url": "https://"},))
    assert not has_reviewable_evidence(({"url": "https:///missing-host"},))


def test_verified_evidence_id_must_resolve_in_the_current_tenant_and_project() -> None:
    statements: list[tuple[str, dict[str, object]]] = []

    class Result:
        def mappings(self) -> Result:
            return self

        @staticmethod
        def all() -> list[dict[str, object]]:
            # The ID exists elsewhere, but the scoped lookup must observe no row.
            return []

    class Session:
        @staticmethod
        def execute(statement: object, parameters: dict[str, object]) -> Result:
            sql = str(statement)
            statements.append((sql, parameters))
            return Result()

    with pytest.raises(EvidenceInvalid, match="verified_evidence_asset_not_owned"):
        Service2CorpusService()._require_owned_evidence_assets(  # noqa: SLF001
            Session(),  # type: ignore[arg-type]
            tenant_pub_id="tnt_current",
            project_pub_id="prj_current",
            evidence=(
                {
                    "evidence_pub_id": "evd_from_another_tenant",
                    "evidence_type": "service2_factcheck_source",
                    "verification_status": "verified",
                    "content_sha256": "a" * 64,
                },
            ),
            allowed_kinds=frozenset({"service2_factcheck_source"}),
        )

    sql, parameters = statements[0]
    assert "tenant_pub_id=:tenant_pub_id" in sql
    assert "project_pub_id=:project_pub_id" in sql
    assert parameters["tenant_pub_id"] == "tnt_current"
    assert parameters["project_pub_id"] == "prj_current"


def test_visual_bbox_must_fit_the_verified_source_image() -> None:
    assert validated_visual_bbox(
        {"x": 10, "y": 20, "width": 100, "height": 40},
        image_width=800,
        image_height=600,
    ) == (10.0, 20.0, 100.0, 40.0)
    assert (
        validated_visual_bbox(
            {"x": 760, "y": 20, "width": 100, "height": 40},
            image_width=800,
            image_height=600,
        )
        is None
    )
    assert visual_anchor_matches_quote(
        anchor_quote_hash=sha256(QUOTE.encode()).hexdigest(),
        anchor_text_start=QUOTE_START,
        anchor_text_end=QUOTE_START + len(QUOTE),
        quote_hash=sha256(QUOTE.encode()).hexdigest(),
        quote_start=QUOTE_START,
        quote_end=QUOTE_START + len(QUOTE),
    )
    assert not visual_anchor_matches_quote(
        anchor_quote_hash=sha256(QUOTE.encode()).hexdigest(),
        anchor_text_start=QUOTE_START + 1,
        anchor_text_end=QUOTE_START + len(QUOTE) + 1,
        quote_hash=sha256(QUOTE.encode()).hexdigest(),
        quote_start=QUOTE_START,
        quote_end=QUOTE_START + len(QUOTE),
    )
    assert (
        validated_visual_bbox(
            {"x": 10, "y": 20, "width": True, "height": 40},
            image_width=800,
            image_height=600,
        )
        is None
    )
    assert (
        validated_visual_bbox(
            {"x": 10, "y": 20, "width": 30, "height": 40},
            image_width=100_001,
            image_height=600,
        )
        is None
    )


def test_strict_batch_contract_rejects_unknown_fields_and_time_inversion() -> None:
    payload = {
        "run_pub_ids": ["run_1"],
        "window_start": "2026-08-01T00:00:00+08:00",
        "window_end": "2026-08-02T00:00:00+08:00",
        "source_snapshot_boundary": "2026-08-03T00:00:00+08:00",
    }
    assert BatchCreate.model_validate(payload).run_pub_ids == ["run_1"]
    with pytest.raises(ValidationError, match="extra_forbidden"):
        BatchCreate.model_validate({**payload, "ownership_required": True})
    with pytest.raises(ValidationError, match="window_start_after_end"):
        BatchCreate.model_validate(
            {
                **payload,
                "window_start": "2026-08-04T00:00:00+08:00",
            }
        )
    with pytest.raises(ValidationError, match="timezone_required"):
        BatchCreate.model_validate({**payload, "window_start": "2026-08-01T00:00:00"})


def test_frozen_case_keeps_factcheck_uncertainty_boundary_with_the_verdict() -> None:
    assert _factcheck_manifest_projection(
        {
            "factcheck_claim": "甲公司不如乙公司可靠",
            "factcheck_verdict": "unverifiable",
            "factcheck_evidence": [],
            "factcheck_boundary": "公开材料不足，不能判断真假。",
        }
    ) == {
        "factcheck_claim": "甲公司不如乙公司可靠",
        "factcheck_verdict": "unverifiable",
        "factcheck_evidence": [],
        "factcheck_boundary": "公开材料不足，不能判断真假。",
    }


def test_customer_evidence_projection_drops_internal_and_invalid_reference_fields() -> None:
    assert _safe_evidence_projection(
        [
            {
                "evidence_pub_id": "evd_service2",
                "url": "https://facts.example.com/full-source",
                "title": "公开事实材料",
                "object_key": "cas/must-not-leak",
                "internal_uuid": "00000000-0000-0000-0000-000000000000",
                "prompt": "must-not-leak",
            },
            {"url": "https:///missing-host", "object_key": "cas/also-hidden"},
        ]
    ) == [
        {
            "evidence_pub_id": "evd_service2",
            "title": "公开事实材料",
            "url": "https://facts.example.com/full-source",
        }
    ]


def test_finding_version_hash_dedupes_retries_but_versions_material_evidence() -> None:
    relation = {"speaker": "页面作者", "target": "甲公司", "quote_hash": SOURCE_HASH}
    base = _relation_version_hash(
        relation=relation,
        candidate_input_hash="a" * 64,
        visual_status=VisualValidationStatus.UNAVAILABLE,
        visual_anchor={},
    )
    assert base == _relation_version_hash(
        relation=relation,
        candidate_input_hash="a" * 64,
        visual_status=VisualValidationStatus.UNAVAILABLE,
        visual_anchor={},
    )
    assert base != _relation_version_hash(
        relation=relation,
        candidate_input_hash="b" * 64,
        visual_status=VisualValidationStatus.UNAVAILABLE,
        visual_anchor={},
    )
    assert base != _relation_version_hash(
        relation=relation,
        candidate_input_hash="a" * 64,
        visual_status=VisualValidationStatus.VERIFIED,
        visual_anchor={"evidence_pub_id": "evd_service2"},
    )


def test_api_states_and_factcheck_contract_are_fail_closed() -> None:
    assert TypeAdapter(CorpusProcessingState).validate_python("partial") == "partial"
    assert TypeAdapter(CorpusReviewState).validate_python("not_applicable") == "not_applicable"
    payload = {
        "corpus_item_pub_id": "s2i_contract",
        "snapshot_pub_id": "snp_contract",
        "ledger": "statement",
        "level": "L1",
        "relation_direction": "target_negative",
        "textual_speaker": "页面作者",
        "target_entity": "甲公司",
        "is_disparagement": False,
        "fact_anchor_state": "present",
        "evidence_quote": QUOTE,
        "quote_start": QUOTE_START,
        "quote_end": QUOTE_START + len(QUOTE),
        "context_text": SOURCE_TEXT,
        "context_start": 0,
        "context_end": len(SOURCE_TEXT),
        "snapshot_text_sha256": SOURCE_HASH,
        "flags": {"direct_target_negative": True},
        "method": "human",
        "model": "human-review",
        "prompt_version": "human-v1",
        "confidence": 1,
        "factcheck_claim": QUOTE,
        "factcheck_verdict": "unverifiable",
    }
    with pytest.raises(ValidationError, match="unverifiable_factcheck_requires_boundary"):
        FindingCreate.model_validate(payload)
    valid = FindingCreate.model_validate(
        {**payload, "factcheck_boundary": "公开材料不足，不能判断真假。"}
    )
    assert valid.factcheck_verdict == "unverifiable"
    with pytest.raises(ValidationError, match="factcheck_verdict_requires_evidence"):
        FindingCreate.model_validate(
            {**payload, "factcheck_verdict": "supported", "factcheck_boundary": None}
        )


def test_customer_cannot_control_or_freeze_internal_service2_batches() -> None:
    principal = Principal(
        subject="customer-service2",
        role=Role.CUSTOMER,
        tenant_pub_id="tnt_service2",
        user_pub_id="usr_customer",
    )
    with pytest.raises(HTTPException) as start_denied:
        lifecycle_action(
            project_pub_id="prj_service2",
            batch_pub_id="s2b_service2",
            action="start",
            idempotency_key="service2-denied-start",
            principal=principal,
            session=None,  # type: ignore[arg-type]
        )
    with pytest.raises(HTTPException) as freeze_denied:
        freeze_batch(
            project_pub_id="prj_service2",
            batch_pub_id="s2b_service2",
            response=Response(),
            idempotency_key="service2-denied-freeze",
            principal=principal,
            session=None,  # type: ignore[arg-type]
        )
    assert start_denied.value.status_code == freeze_denied.value.status_code == 403
    assert start_denied.value.detail == freeze_denied.value.detail == {"code": "permission_denied"}
