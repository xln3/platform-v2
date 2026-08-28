from __future__ import annotations

from domain.metrics.v2.query_context import (
    AnalysisLens,
    BrandStructureType,
    ClassificationState,
    ExposureRole,
    QueryContextFact,
    RequestedOperation,
    derive_brand_structure,
    derive_exposure_role,
    derive_query_key,
    hash_normalized_query_text,
    hash_query_text,
    normalize_query_text,
)


def test_query_normalization_is_nfkc_and_whitespace_only() -> None:
    assert normalize_query_text("  盛邦\u3000安全\nＡＩ  ") == "盛邦 安全 AI"
    assert hash_query_text("盛邦\u3000安全") != hash_query_text("盛邦 安全")
    assert hash_normalized_query_text("盛邦\u3000安全") == hash_normalized_query_text("盛邦 安全")


def test_legacy_query_key_is_stable_and_public_id_wins() -> None:
    first = derive_query_key(
        tenant_pub_id="ten_1", project_pub_id="prj_1", query_text=" 推荐  安全公司 "
    )
    second = derive_query_key(
        tenant_pub_id="ten_1", project_pub_id="prj_1", query_text="推荐 安全公司"
    )
    assert first == second
    assert first.startswith("legacy:") and len(first) == 71
    assert (
        derive_query_key(
            tenant_pub_id="ten_1",
            project_pub_id="prj_1",
            query_text="ignored",
            query_pub_id="qry_1",
        )
        == "qry_1"
    )


def test_brand_structure_and_exposure_are_focal_entity_relative() -> None:
    entities = {"brand_qax"}
    assert derive_brand_structure(entities) is BrandStructureType.SINGLE_BRAND_NAMED
    assert derive_exposure_role(entities, "brand_qax") is ExposureRole.FOCAL_NAMED_ONLY
    assert derive_exposure_role(entities, "brand_sbang") is ExposureRole.OTHER_BRAND_NAMED
    assert derive_exposure_role(set(), "brand_sbang") is ExposureRole.BRAND_NEUTRAL
    assert (
        derive_exposure_role({"brand_qax", "brand_sbang"}, "brand_sbang")
        is ExposureRole.FOCAL_NAMED_WITH_OTHERS
    )


def test_unresolved_brand_surface_never_becomes_brand_neutral() -> None:
    assert (
        derive_brand_structure(set(), has_unresolved_brand_surface=True)
        is BrandStructureType.UNKNOWN
    )
    assert (
        derive_exposure_role(set(), "brand_sbang", has_unresolved_brand_surface=True)
        is ExposureRole.UNKNOWN
    )


def test_ready_query_context_enforces_multilabel_facts_but_not_primary_lens() -> None:
    fact = QueryContextFact(
        query_key="qry_1",
        query_text_hash=hash_query_text("盛邦安全排第几？"),
        primary_lens=AnalysisLens.AI_IMPRESSION,
        analysis_lenses=frozenset({AnalysisLens.AI_IMPRESSION, AnalysisLens.AI_RECOMMENDATION}),
        requested_operations=frozenset(
            {
                RequestedOperation.FACT_LOOKUP,
                RequestedOperation.EVALUATE,
                RequestedOperation.RANK,
            }
        ),
        detected_entity_ids=frozenset({"brand_sbang"}),
        brand_structure_type=BrandStructureType.SINGLE_BRAND_NAMED,
        classification_state=ClassificationState.READY,
        classifier_version="query-context@2",
        decision_task_bundle_hash="a" * 64,
        entity_dictionary_hash="b" * 64,
    )
    assert fact.exposure_for("brand_sbang") is ExposureRole.FOCAL_NAMED_ONLY
    assert AnalysisLens.AI_RECOMMENDATION in fact.analysis_lenses
