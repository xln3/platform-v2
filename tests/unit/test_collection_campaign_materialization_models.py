from __future__ import annotations

from geo_platform.collection.models import (
    CollectionCampaign,
    CollectionCampaignMaterializationBatch,
    CollectionPrimarySlot,
)
from sqlalchemy import UniqueConstraint


def _unique_columns(model: type) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_campaign_header_is_compact_materialization_state_not_expanded_membership() -> None:
    columns = set(CollectionCampaign.__table__.columns.keys())

    assert {
        "membership_specification_json",
        "specification_schema_version",
        "specification_hash",
        "slot_generator_version",
        "membership_digest_version",
        "expected_primary_slot_count",
        "expected_non_primary_slot_count",
        "expected_slot_count",
        "materialized_slot_count",
        "materialization_state",
        "materialization_cursor",
        "membership_hash",
    }.issubset(columns)
    assert "membership_json" not in columns
    assert "question_slots_json" not in columns


def test_campaign_materialization_lineage_and_final_digest_are_unique_per_scope() -> None:
    constraints = _unique_columns(CollectionCampaign)

    assert ("tenant_id", "project_id", "membership_hash") in constraints
    assert (
        "id",
        "tenant_id",
        "project_id",
        "specification_hash",
        "slot_generator_version",
    ) in constraints


def test_materialization_batch_has_retry_safe_range_and_idempotency_identity() -> None:
    assert (
        CollectionCampaignMaterializationBatch.__tablename__
        == "collection_campaign_materialization_batch"
    )
    columns = set(CollectionCampaignMaterializationBatch.__table__.columns.keys())
    assert {
        "campaign_id",
        "specification_hash",
        "slot_generator_version",
        "start_slot_ordinal",
        "end_slot_ordinal_exclusive",
        "slot_count",
        "prior_membership_chain_hash",
        "membership_chain_hash",
        "chunk_hash",
        "idempotency_key",
        "batch_state",
        "committed_at",
    }.issubset(columns)
    constraints = _unique_columns(CollectionCampaignMaterializationBatch)
    assert (
        "tenant_id",
        "project_id",
        "campaign_id",
        "start_slot_ordinal",
        "end_slot_ordinal_exclusive",
    ) in constraints
    assert (
        "tenant_id",
        "project_id",
        "campaign_id",
        "idempotency_key",
    ) in constraints


def test_slot_has_campaign_global_ordinal_hash_and_exact_batch_lineage() -> None:
    columns = set(CollectionPrimarySlot.__table__.columns.keys())
    assert {"materialization_batch_id", "slot_ordinal", "slot_identity_hash"}.issubset(columns)
    assert (
        "tenant_id",
        "project_id",
        "campaign_id",
        "slot_ordinal",
    ) in _unique_columns(CollectionPrimarySlot)
    assert (
        "tenant_id",
        "project_id",
        "campaign_id",
        "slot_identity_hash",
    ) in _unique_columns(CollectionPrimarySlot)

    foreign_key_pairs = {
        (element.parent.name, element.target_fullname)
        for constraint in CollectionPrimarySlot.__table__.foreign_key_constraints
        for element in constraint.elements
    }
    assert (
        "materialization_batch_id",
        "platform.collection_campaign_materialization_batch.id",
    ) in foreign_key_pairs
    assert (
        "campaign_id",
        "platform.collection_campaign_materialization_batch.campaign_id",
    ) in foreign_key_pairs
