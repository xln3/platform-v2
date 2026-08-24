from __future__ import annotations

import pytest
from pydantic import ValidationError

from domain.collection.surface import (
    MAINLAND_PROVINCE_CODES,
    CapabilityDeclaration,
    CapabilityRegistry,
    CapabilityStatus,
    CollectionConfigV2,
    CollectionSurface,
    CollectionTarget,
    QuotaScopeDeclaration,
    QuotaScopeKind,
    QuotaScopeRegistry,
    QuotaWindowPolicy,
    QuotaWindowUnit,
    SlotIdentity,
    StaticCapabilityError,
)


def _target(
    collection_surface: CollectionSurface,
    *,
    product_variant: str,
    interaction_modes: tuple[str, ...] = ("normal",),
) -> CollectionTarget:
    return CollectionTarget(
        platform="doubao",
        collection_surface=collection_surface,
        product_variant=product_variant,
        interaction_modes=interaction_modes,
    )


def _config(
    *targets: CollectionTarget,
    province_codes: tuple[str, ...] = ("110000", "310000"),
    samples_per_cell: int = 3,
) -> CollectionConfigV2:
    return CollectionConfigV2(
        question_set_revision="questions-v1",
        collection_targets=targets,
        province_codes=province_codes,
        samples_per_cell=samples_per_cell,
        schedule_policy={"window": {"timezone": "Asia/Shanghai", "days": [1, 2]}},
        comparison_policy_revision="comparison-v1",
    )


def test_three_surfaces_have_independent_target_and_slot_business_keys() -> None:
    targets = (
        _target(CollectionSurface.PROVIDER_API, product_variant="same-comparison-product"),
        _target(CollectionSurface.CONSUMER_WEB, product_variant="same-comparison-product"),
        _target(CollectionSurface.CONSUMER_APP, product_variant="same-comparison-product"),
    )

    assert len({target.target_key for target in targets}) == 3

    slot_keys = {
        SlotIdentity(
            campaign_id="campaign-01",
            question_slot_id="question-01",
            platform=target.platform,
            collection_surface=target.collection_surface,
            product_variant=target.product_variant,
            province_code="110000",
            interaction_mode="normal",
            sample_ordinal=1,
        ).slot_key
        for target in targets
    }
    assert len(slot_keys) == 3


def test_semantically_equivalent_collection_order_has_one_config_hash() -> None:
    api = _target(
        CollectionSurface.PROVIDER_API,
        product_variant="provider-v1",
        interaction_modes=("research", "normal"),
    )
    web = _target(CollectionSurface.CONSUMER_WEB, product_variant="consumer-web-default")
    first = _config(api, web, province_codes=("310000", "110000"))
    second = CollectionConfigV2(
        question_set_revision="questions-v1",
        collection_targets=(
            web,
            _target(
                CollectionSurface.PROVIDER_API,
                product_variant="provider-v1",
                interaction_modes=("normal", "research"),
            ),
        ),
        province_codes=("110000", "310000"),
        samples_per_cell=3,
        schedule_policy={"window": {"days": [1, 2], "timezone": "Asia/Shanghai"}},
        comparison_policy_revision="comparison-v1",
    )

    assert first.canonical_json == second.canonical_json
    assert first.revision_hash == second.revision_hash


def test_nested_schedule_policy_cannot_mutate_a_frozen_revision() -> None:
    config = _config(
        _target(CollectionSurface.CONSUMER_WEB, product_variant="consumer-web-default")
    )

    with pytest.raises(TypeError):
        config.schedule_policy["new_key"] = True
    days = config.schedule_policy["window"]["days"]  # type: ignore[index]
    with pytest.raises(AttributeError):
        days.append(3)  # type: ignore[union-attr]


def test_duplicate_canonical_target_is_rejected_even_when_modes_differ() -> None:
    with pytest.raises(ValidationError, match="duplicate_collection_target"):
        _config(
            _target(CollectionSurface.CONSUMER_WEB, product_variant="consumer-web-default"),
            _target(
                CollectionSurface.CONSUMER_WEB,
                product_variant="consumer-web-default",
                interaction_modes=("research",),
            ),
        )


def test_unsupported_capability_is_rejected_at_static_validation() -> None:
    target = _target(CollectionSurface.CONSUMER_APP, product_variant="android-stable")
    registry = CapabilityRegistry(
        registry_revision="registry-v1",
        capabilities=(
            CapabilityDeclaration(
                capability_revision="doubao-app-v1",
                platform="doubao",
                collection_surface=CollectionSurface.CONSUMER_APP,
                product_variant="android-stable",
                interaction_mode="normal",
                status=CapabilityStatus.UNSUPPORTED,
                unsupported_reason="managed_device_binding_not_available",
            ),
        ),
    )

    with pytest.raises(StaticCapabilityError) as exc_info:
        registry.validate_config(_config(target))

    assert exc_info.value.code == "capability_unsupported"
    assert exc_info.value.target_key == target.target_key
    assert exc_info.value.capability_status is CapabilityStatus.UNSUPPORTED


@pytest.mark.parametrize("province_code", ["100000", "710000", "810000", "全国"])
def test_non_catalog_province_code_is_rejected(province_code: str) -> None:
    with pytest.raises(ValidationError, match="invalid_province_code"):
        _config(
            _target(CollectionSurface.CONSUMER_WEB, product_variant="consumer-web-default"),
            province_codes=(province_code,),
        )


def test_catalog_contains_exactly_31_mainland_province_codes() -> None:
    assert len(MAINLAND_PROVINCE_CODES) == 31
    assert "110000" in MAINLAND_PROVINCE_CODES
    assert "650000" in MAINLAND_PROVINCE_CODES


def test_samples_per_cell_expands_to_stable_one_based_ordinals() -> None:
    config = _config(
        _target(CollectionSurface.CONSUMER_WEB, product_variant="consumer-web-default"),
        samples_per_cell=3,
    )

    assert config.sample_ordinals == (1, 2, 3)
    assert 4 not in config.sample_ordinals


def _quota_window() -> QuotaWindowPolicy:
    return QuotaWindowPolicy(
        unit=QuotaWindowUnit.DAY,
        timezone="Asia/Shanghai",
        boundary_revision="calendar-v1",
    )


def _quota_scope(
    scope_kind: QuotaScopeKind,
    scope_subject_id: str,
    **dimensions: object,
) -> QuotaScopeDeclaration:
    return QuotaScopeDeclaration(
        policy_revision="quota-policy-v1",
        scope_kind=scope_kind,
        scope_subject_id=scope_subject_id,
        limit=100,
        window=_quota_window(),
        **dimensions,
    )


def test_quota_registry_applies_stable_canonical_lock_order() -> None:
    provider = _quota_scope(QuotaScopeKind.PROVIDER, "provider-main")
    project = _quota_scope(QuotaScopeKind.PROJECT, "project-01")
    surface = _quota_scope(
        QuotaScopeKind.PLATFORM_SURFACE,
        "project-01",
        platform="doubao",
        collection_surface=CollectionSurface.CONSUMER_WEB,
        product_variant="consumer-web-default",
    )
    mode = _quota_scope(
        QuotaScopeKind.MODE,
        "project-01",
        interaction_mode="research",
    )

    first = QuotaScopeRegistry(
        registry_revision="quota-registry-v1",
        scopes=(mode, surface, project, provider),
    )
    second = QuotaScopeRegistry(
        registry_revision="quota-registry-v1",
        scopes=(provider, project, surface, mode),
    )

    assert tuple(scope.scope_kind for scope in first.scopes) == (
        QuotaScopeKind.PROVIDER,
        QuotaScopeKind.PROJECT,
        QuotaScopeKind.PLATFORM_SURFACE,
        QuotaScopeKind.MODE,
    )
    assert first.canonical_scope_keys == second.canonical_scope_keys
    assert "collection_surface=consumer_web" in surface.scope_key


def test_quota_registry_rejects_duplicate_logical_scope_across_policy_revisions() -> None:
    first = _quota_scope(QuotaScopeKind.ACCOUNT, "account-01")
    replacement = QuotaScopeDeclaration(
        policy_revision="quota-policy-v2",
        scope_kind=QuotaScopeKind.ACCOUNT,
        scope_subject_id="account-01",
        limit=200,
        window=_quota_window(),
    )

    with pytest.raises(ValidationError, match="duplicate_quota_scope"):
        QuotaScopeRegistry(
            registry_revision="quota-registry-v2",
            scopes=(first, replacement),
        )


def test_quota_window_timezone_limit_and_custom_policy_are_strict() -> None:
    with pytest.raises(ValidationError, match="invalid_quota_timezone"):
        QuotaWindowPolicy(
            unit=QuotaWindowUnit.DAY,
            timezone="Mars/Olympus",
            boundary_revision="calendar-v1",
        )
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        QuotaScopeDeclaration(
            policy_revision="quota-policy-v1",
            scope_kind=QuotaScopeKind.PROJECT,
            scope_subject_id="project-01",
            limit=0,
            window=_quota_window(),
        )
    with pytest.raises(ValidationError, match="provider_custom_window_requires_code"):
        QuotaWindowPolicy(
            unit=QuotaWindowUnit.PROVIDER_CUSTOM,
            timezone="UTC",
            boundary_revision="provider-window-v1",
        )
