from __future__ import annotations

import copy
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from geo_platform.collection.campaign_materialization_v2 import (
    CampaignMaterializationError,
    CampaignMaterializationV2,
)
from geo_platform.collection.identity_v2 import (
    CampaignActors,
    CampaignFreezeRequest,
    ConfigFreezeRequest,
    IdentityV2Error,
    QuestionSlotRef,
    freeze_campaign,
    freeze_config,
    iter_campaign_slot_chunks,
)
from geo_platform.collection.models import (
    CollectionCampaign,
    CollectionCampaignMaterializationBatch,
    CollectionPrimarySlot,
)

from domain.collection.surface import (
    CapabilityDeclaration,
    CapabilityRegistry,
    CapabilityStatus,
    CollectionConfigV2,
    CollectionSurface,
    CollectionTarget,
)

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
PROJECT_ID = UUID("00000000-0000-0000-0000-000000000002")


def _blueprint(*, samples_per_cell: int = 5):
    target = CollectionTarget(
        platform="doubao",
        collection_surface=CollectionSurface.CONSUMER_WEB,
        product_variant="default",
        interaction_modes=("normal",),
    )
    config = CollectionConfigV2(
        question_set_revision="questions-v1",
        collection_targets=(target,),
        province_codes=("110000",),
        samples_per_cell=samples_per_cell,
        schedule_policy={},
        comparison_policy_revision="comparison-v1",
    )
    registry = CapabilityRegistry(
        registry_revision="capabilities-v1",
        capabilities=(
            CapabilityDeclaration(
                capability_revision="doubao-web-normal-v1",
                platform="doubao",
                collection_surface=CollectionSurface.CONSUMER_WEB,
                product_variant="default",
                interaction_mode="normal",
                status=CapabilityStatus.SUPPORTED,
                production_allowed=True,
            ),
        ),
    )
    frozen_config = freeze_config(
        ConfigFreezeRequest(
            revision_pub_id="ccr2_revision_1",
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            revision=1,
            config=config,
            capability_registry=registry,
            change_reason="initial-freeze",
            frozen_at=NOW,
        )
    )
    return freeze_campaign(
        CampaignFreezeRequest(
            campaign_pub_id="campaign_1",
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            config_revision=frozen_config,
            question_slots=(
                QuestionSlotRef(
                    question_slot_id="question-1",
                    question_revision="qrev-1",
                ),
            ),
            time_window_key="2026-08-24/2026-08-25",
            run_trigger_source="manual",
            trigger_idempotency_key="campaign-idempotency-1",
            actors=CampaignActors(
                created_by_pub_id="user-1",
                approved_by_pub_id="reviewer-1",
                triggered_by_pub_id="user-1",
            ),
            binding_policy_revision="binding-policy-v1",
            frozen_at=NOW,
        )
    )


class _Rows:
    def __init__(self, values: Iterable[Any]) -> None:
        self._values = list(values)

    def __iter__(self):
        return iter(self._values)


class _Store:
    def __init__(self) -> None:
        self.rows: dict[type[Any], dict[object, Any]] = {}
        self.advanced_batches: set[object] = set()
        self.commits = 0
        self.rollbacks = 0
        self.scope_calls: list[dict[str, object]] = []
        self.session_count = 0
        self.max_slots_added_in_one_session = 0
        self.fail_completed_flush_once = False
        self.raise_lost_ack_after_commit_once = False

    def factory(self):
        self.session_count += 1
        return _FakeSession(self)


class _Transaction:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session
        self._rows_snapshot: dict[type[Any], dict[object, Any]] = {}
        self._advanced_snapshot: set[object] = set()

    def __enter__(self):
        self._rows_snapshot = copy.deepcopy(self._session.store.rows)
        self._advanced_snapshot = set(self._session.store.advanced_batches)
        return self

    def __exit__(self, exc_type, exc, traceback):
        store = self._session.store
        if exc_type is not None:
            store.rows = self._rows_snapshot
            store.advanced_batches = self._advanced_snapshot
            store.rollbacks += 1
            return False
        store.commits += 1
        if store.raise_lost_ack_after_commit_once:
            store.raise_lost_ack_after_commit_once = False
            raise RuntimeError("simulated_lost_commit_ack")
        return False


class _FakeSession:
    def __init__(self, store: _Store) -> None:
        self.store = store
        self._slots_added = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def begin(self):
        return _Transaction(self)

    def execute(self, statement, parameters=None):
        sql = str(statement)
        if "set_config('app.tenant_id'" in sql:
            assert parameters is not None
            self.store.scope_calls.append(dict(parameters))
        return None

    def get(self, model, row_id, **kwargs):
        return self.store.rows.get(model, {}).get(row_id)

    def add(self, row) -> None:
        self.store.rows.setdefault(type(row), {})[row.id] = row
        if isinstance(row, CollectionPrimarySlot):
            self._slots_added += 1
            self.store.max_slots_added_in_one_session = max(
                self.store.max_slots_added_in_one_session,
                self._slots_added,
            )

    def add_all(self, rows) -> None:
        for row in rows:
            self.add(row)

    def scalars(self, statement):
        model = statement.column_descriptions[0]["entity"]
        rows = list(self.store.rows.get(model, {}).values())
        for criterion in statement._where_criteria:
            field = getattr(criterion.left, "key", None)
            expected = getattr(criterion.right, "value", object())
            if field is not None:
                rows = [row for row in rows if getattr(row, field) == expected]
        order_by = list(statement._order_by_clauses)
        if order_by:
            field = getattr(order_by[0], "key", None)
            if field is not None:
                rows.sort(key=lambda row: getattr(row, field))
        return _Rows(rows)

    def scalar(self, statement):
        rows = list(self.scalars(statement))
        return rows[0] if rows else None

    def flush(self) -> None:
        completed = [
            batch
            for batch in self.store.rows.get(
                CollectionCampaignMaterializationBatch,
                {},
            ).values()
            if batch.batch_state == "completed" and batch.id not in self.store.advanced_batches
        ]
        if completed and self.store.fail_completed_flush_once:
            self.store.fail_completed_flush_once = False
            raise RuntimeError("simulated_chunk_flush_failure")
        for batch in completed:
            campaign = self.store.rows[CollectionCampaign][batch.campaign_id]
            assert campaign.materialization_cursor == batch.start_slot_ordinal
            campaign.materialization_cursor = batch.end_slot_ordinal_exclusive
            campaign.materialized_slot_count = batch.end_slot_ordinal_exclusive
            campaign.materialization_state = (
                "complete"
                if batch.end_slot_ordinal_exclusive == campaign.expected_slot_count
                else "materializing"
            )
            self.store.advanced_batches.add(batch.id)

    def refresh(self, row) -> None:
        return None


def _service(store: _Store) -> CampaignMaterializationV2:
    return CampaignMaterializationV2(
        session_factory=store.factory,  # type: ignore[arg-type]
        tenant_pub_id="tenant-1",
    )


def test_materialize_uses_one_short_transaction_per_bounded_chunk_and_final_cas() -> None:
    blueprint = _blueprint(samples_per_cell=5)
    store = _Store()

    confirmation = _service(store).materialize_and_freeze(blueprint, chunk_size=2)

    assert confirmation.state == "frozen"
    assert confirmation.materialized_slot_count == 5
    assert store.commits == 5  # header + three chunks + final CAS
    assert store.rollbacks == 0
    assert store.max_slots_added_in_one_session == 2
    assert len(store.scope_calls) == store.commits
    assert {call["tenant_id"] for call in store.scope_calls} == {str(TENANT_ID)}


def test_lost_commit_ack_replays_via_exact_database_readback_without_duplicates() -> None:
    blueprint = _blueprint(samples_per_cell=3)
    store = _Store()
    service = _service(store)
    checkpoint = service.ensure_assembling_campaign(blueprint)
    chunk = next(
        iter_campaign_slot_chunks(
            blueprint,
            chunk_size=2,
            checkpoint_digest=checkpoint.membership_chain_hash,
        )
    )
    store.raise_lost_ack_after_commit_once = True

    with pytest.raises(RuntimeError, match="simulated_lost_commit_ack"):
        service.persist_chunk(blueprint, chunk)

    recovered = service.persist_chunk(blueprint, chunk)
    assert recovered == chunk.checkpoint
    assert len(store.rows[CollectionCampaignMaterializationBatch]) == 1
    assert len(store.rows[CollectionPrimarySlot]) == 2


def test_failed_chunk_rolls_back_and_resume_has_no_gap_or_duplicate() -> None:
    blueprint = _blueprint(samples_per_cell=5)
    store = _Store()
    service = _service(store)
    checkpoint = service.ensure_assembling_campaign(blueprint)
    chunks = iter_campaign_slot_chunks(
        blueprint,
        chunk_size=2,
        checkpoint_digest=checkpoint.membership_chain_hash,
    )
    first = next(chunks)
    checkpoint = service.persist_chunk(blueprint, first)
    second = next(chunks)
    store.fail_completed_flush_once = True

    with pytest.raises(RuntimeError, match="simulated_chunk_flush_failure"):
        service.persist_chunk(blueprint, second)

    recovered = service.load_checkpoint(blueprint)
    assert recovered == checkpoint
    assert len(store.rows[CollectionPrimarySlot]) == 2
    assert store.rollbacks == 1

    for chunk in iter_campaign_slot_chunks(
        blueprint,
        start_cursor=recovered.next_slot_ordinal,
        chunk_size=2,
        checkpoint_digest=recovered.membership_chain_hash,
    ):
        recovered = service.persist_chunk(blueprint, chunk)
    confirmation = service.freeze_completed_campaign(blueprint, recovered)

    slots = list(store.rows[CollectionPrimarySlot].values())
    assert confirmation.state == "frozen"
    assert sorted(slot.slot_ordinal for slot in slots) == list(range(5))
    assert len({slot.slot_key for slot in slots}) == 5


def test_gap_overlap_and_exact_retry_drift_fail_closed() -> None:
    blueprint = _blueprint(samples_per_cell=5)
    store = _Store()
    service = _service(store)
    checkpoint = service.ensure_assembling_campaign(blueprint)
    first = next(
        iter_campaign_slot_chunks(
            blueprint,
            chunk_size=2,
            checkpoint_digest=checkpoint.membership_chain_hash,
        )
    )
    checkpoint = service.persist_chunk(blueprint, first)

    overlapping = first.model_copy(
        update={
            "batch_id": UUID(int=999),
            "batch_pub_id": "overlap-batch",
            "idempotency_key": "overlap-batch",
        }
    )
    with pytest.raises(CampaignMaterializationError) as overlap:
        service.persist_chunk(blueprint, overlapping)
    assert overlap.value.code == "campaign_chunk_overlap"

    gap = next(
        iter_campaign_slot_chunks(
            blueprint,
            start_cursor=4,
            chunk_size=1,
        )
    )
    with pytest.raises(CampaignMaterializationError) as gap_error:
        service.persist_chunk(blueprint, gap)
    assert gap_error.value.code == "campaign_chunk_gap"

    drifted_retry = first.model_copy(update={"chunk_hash": "f" * 64})
    with pytest.raises(CampaignMaterializationError) as drift:
        service.persist_chunk(blueprint, drifted_retry)
    assert drift.value.code == "campaign_batch_exact_match_failed"
    assert service.load_checkpoint(blueprint) == checkpoint


def test_incomplete_campaign_cannot_freeze_and_final_ack_is_idempotent() -> None:
    blueprint = _blueprint(samples_per_cell=2)
    store = _Store()
    service = _service(store)
    incomplete = service.ensure_assembling_campaign(blueprint)

    with pytest.raises(IdentityV2Error) as caught:
        service.freeze_completed_campaign(blueprint, incomplete)
    assert caught.value.code == "campaign_materialization_incomplete"

    chunk = next(
        iter_campaign_slot_chunks(
            blueprint,
            chunk_size=2,
            checkpoint_digest=incomplete.membership_chain_hash,
        )
    )
    complete = service.persist_chunk(blueprint, chunk)
    first = service.freeze_completed_campaign(blueprint, complete)
    replay = service.freeze_completed_campaign(blueprint, complete)

    assert replay == first
    assert store.rows[CollectionCampaign][blueprint.id].state == "frozen"
