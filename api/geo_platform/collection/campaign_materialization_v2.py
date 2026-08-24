"""Short-transaction persistence for collection-v2 campaign materialization.

The logical campaign is compact.  Only one bounded slot chunk is held and
written at a time; database materialization chunks are deliberately unrelated
to later workflow execution partitions or runtime concurrency.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, NoReturn
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..tenancy.repository import set_tenant_context
from .identity_v2 import (
    CampaignAssemblyBlueprint,
    CampaignMaterializationCheckpoint,
    CampaignSlotChunk,
    PersistedCampaignFreezeConfirmation,
    advance_campaign_checkpoint,
    initial_campaign_checkpoint,
    iter_campaign_slot_chunks,
)
from .models import (
    CollectionCampaign,
    CollectionCampaignMaterializationBatch,
    CollectionCampaignTarget,
    CollectionPrimarySlot,
    CollectionSamplingLeg,
)

SessionFactory = Callable[[], Session]

_CAMPAIGN_PROGRESS_FIELDS = frozenset(
    {
        "materialized_slot_count",
        "materialization_state",
        "materialization_cursor",
        "membership_hash",
        "frozen_at",
        "state",
    }
)
_BATCH_COMMIT_FIELDS = frozenset({"batch_state", "committed_at"})


class CampaignMaterializationError(RuntimeError):
    """Fail-closed persistence error with a stable machine-readable code."""

    code: str
    context: Mapping[str, str | int | bool | None]

    def __init__(self, code: str, **context: str | int | bool | None) -> None:
        self.code = code
        self.context = dict(sorted(context.items()))
        suffix = ":" + ":".join(f"{key}={value}" for key, value in self.context.items())
        super().__init__(f"{code}{suffix}" if self.context else code)


class CampaignMaterializationV2:
    """Persist an assembly blueprint through independent short transactions."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        tenant_pub_id: str,
    ) -> None:
        if not tenant_pub_id.strip():
            raise ValueError("tenant_pub_id_required")
        self._session_factory = session_factory
        self._tenant_pub_id = tenant_pub_id

    def ensure_assembling_campaign(
        self,
        blueprint: CampaignAssemblyBlueprint,
    ) -> CampaignMaterializationCheckpoint:
        """Idempotently persist the compact header, targets, and sampling legs."""

        with self._session_factory() as session:
            with session.begin():
                self._set_scope(session, blueprint.tenant_id)
                campaign_by_id = session.get(
                    CollectionCampaign,
                    blueprint.id,
                    with_for_update=True,
                )
                campaign_by_trigger = session.scalar(
                    select(CollectionCampaign).where(
                        CollectionCampaign.tenant_id == blueprint.tenant_id,
                        CollectionCampaign.project_id == blueprint.project_id,
                        CollectionCampaign.trigger_idempotency_key
                        == blueprint.trigger_idempotency_key,
                    )
                )
                campaign = _one_identity(
                    campaign_by_id,
                    campaign_by_trigger,
                    code="campaign_trigger_idempotency_collision",
                )
                if campaign is None:
                    session.add(CollectionCampaign(**blueprint.campaign_row_values))
                    session.add_all(
                        CollectionCampaignTarget(**values)
                        for values in blueprint.campaign_target_row_values
                    )
                    session.add_all(
                        CollectionSamplingLeg(**values) for values in blueprint.leg_row_values
                    )
                    session.flush()
                    campaign = session.get(
                        CollectionCampaign,
                        blueprint.id,
                        with_for_update=True,
                    )
                    if campaign is None:
                        self._fail("campaign_header_insert_not_visible")

                self._assert_campaign_specification(campaign, blueprint)
                self._assert_static_membership_rows(session, blueprint)
                return self._checkpoint_from_row(session, campaign, blueprint)

    def load_checkpoint(
        self,
        blueprint: CampaignAssemblyBlueprint,
    ) -> CampaignMaterializationCheckpoint:
        """Read the constant-size durable resume position in its own transaction."""

        with self._session_factory() as session:
            with session.begin():
                self._set_scope(session, blueprint.tenant_id)
                campaign = session.get(CollectionCampaign, blueprint.id)
                if campaign is None:
                    self._fail("campaign_not_found")
                self._assert_campaign_specification(campaign, blueprint)
                return self._checkpoint_from_row(session, campaign, blueprint)

    def persist_chunk(
        self,
        blueprint: CampaignAssemblyBlueprint,
        chunk: CampaignSlotChunk,
    ) -> CampaignMaterializationCheckpoint:
        """Commit exactly one bounded range, or strictly verify an earlier commit."""

        with self._session_factory() as session:
            with session.begin():
                self._set_scope(session, blueprint.tenant_id)
                campaign = session.get(
                    CollectionCampaign,
                    blueprint.id,
                    with_for_update=True,
                )
                if campaign is None:
                    self._fail("campaign_not_found")
                self._assert_campaign_specification(campaign, blueprint)
                persisted_checkpoint = self._checkpoint_from_row(session, campaign, blueprint)

                batch_by_id = session.get(
                    CollectionCampaignMaterializationBatch,
                    chunk.batch_id,
                    with_for_update=True,
                )
                batch_by_idempotency = session.scalar(
                    select(CollectionCampaignMaterializationBatch).where(
                        CollectionCampaignMaterializationBatch.campaign_id == blueprint.id,
                        CollectionCampaignMaterializationBatch.tenant_id == blueprint.tenant_id,
                        CollectionCampaignMaterializationBatch.project_id == blueprint.project_id,
                        CollectionCampaignMaterializationBatch.idempotency_key
                        == chunk.idempotency_key,
                    )
                )
                existing = _one_identity(
                    batch_by_id,
                    batch_by_idempotency,
                    code="campaign_batch_idempotency_collision",
                )
                if existing is not None:
                    self._assert_committed_chunk_exact(session, existing, chunk)
                    if persisted_checkpoint.next_slot_ordinal < chunk.end_slot_ordinal_exclusive:
                        self._fail(
                            "committed_batch_ahead_of_campaign_cursor",
                            campaign_cursor=persisted_checkpoint.next_slot_ordinal,
                            batch_end=chunk.end_slot_ordinal_exclusive,
                        )
                    return persisted_checkpoint

                if campaign.state != "assembling":
                    self._fail("frozen_campaign_rejects_new_chunk")
                if chunk.start_slot_ordinal < persisted_checkpoint.next_slot_ordinal:
                    self._fail(
                        "campaign_chunk_overlap",
                        campaign_cursor=persisted_checkpoint.next_slot_ordinal,
                        chunk_start=chunk.start_slot_ordinal,
                    )
                if chunk.start_slot_ordinal > persisted_checkpoint.next_slot_ordinal:
                    self._fail(
                        "campaign_chunk_gap",
                        campaign_cursor=persisted_checkpoint.next_slot_ordinal,
                        chunk_start=chunk.start_slot_ordinal,
                    )

                # This validates specification/generator lineage, prior digest,
                # deterministic slot identities, range, chunk hash, and result digest.
                expected_checkpoint = advance_campaign_checkpoint(
                    blueprint,
                    persisted_checkpoint,
                    chunk,
                )
                batch = CollectionCampaignMaterializationBatch(**chunk.batch_insert_values)
                session.add(batch)
                session.add_all(CollectionPrimarySlot(**values) for values in chunk.slot_row_values)
                session.flush()
                batch.batch_state = "completed"
                batch.committed_at = datetime.now(UTC)
                session.flush()
                session.refresh(campaign)

                if (
                    campaign.materialization_cursor != expected_checkpoint.next_slot_ordinal
                    or campaign.materialized_slot_count
                    != expected_checkpoint.materialized_slot_count
                    or campaign.materialization_state
                    != ("complete" if chunk.is_complete else "materializing")
                ):
                    self._fail(
                        "campaign_checkpoint_advance_not_visible",
                        expected_cursor=expected_checkpoint.next_slot_ordinal,
                        actual_cursor=campaign.materialization_cursor,
                    )
                return expected_checkpoint

    def freeze_completed_campaign(
        self,
        blueprint: CampaignAssemblyBlueprint,
        checkpoint: CampaignMaterializationCheckpoint,
    ) -> PersistedCampaignFreezeConfirmation:
        """Perform the final complete-only state change in a separate short CAS."""

        # Full deterministic digest validation happens before a row is locked.
        blueprint.persistence_plan.validate_complete(checkpoint)

        with self._session_factory() as session:
            with session.begin():
                self._set_scope(session, blueprint.tenant_id)
                campaign = session.get(
                    CollectionCampaign,
                    blueprint.id,
                    with_for_update=True,
                )
                if campaign is None:
                    self._fail("campaign_not_found")
                self._assert_campaign_specification(campaign, blueprint)
                persisted_checkpoint = self._checkpoint_from_row(session, campaign, blueprint)
                if persisted_checkpoint != checkpoint:
                    self._fail("campaign_finalization_checkpoint_mismatch")

                if campaign.state == "assembling":
                    if (
                        campaign.materialization_state != "complete"
                        or campaign.materialization_cursor != blueprint.expected_slot_count
                        or campaign.materialized_slot_count != blueprint.expected_slot_count
                        or campaign.membership_hash is not None
                        or campaign.frozen_at is not None
                    ):
                        self._fail("campaign_materialization_incomplete")
                    campaign.membership_hash = checkpoint.membership_chain_hash
                    campaign.frozen_at = blueprint.requested_frozen_at
                    campaign.state = "frozen"
                    session.flush()
                    session.refresh(campaign)

                return self._freeze_confirmation(campaign, blueprint, checkpoint)

    def materialize_and_freeze(
        self,
        blueprint: CampaignAssemblyBlueprint,
        *,
        chunk_size: int,
    ) -> PersistedCampaignFreezeConfirmation:
        """Resume from the durable cursor without retaining earlier chunks."""

        checkpoint = self.ensure_assembling_campaign(blueprint)
        for chunk in iter_campaign_slot_chunks(
            blueprint,
            start_cursor=checkpoint.next_slot_ordinal,
            chunk_size=chunk_size,
            checkpoint_digest=checkpoint.membership_chain_hash,
        ):
            checkpoint = self.persist_chunk(blueprint, chunk)
        return self.freeze_completed_campaign(blueprint, checkpoint)

    def _set_scope(self, session: Session, tenant_id: UUID) -> None:
        set_tenant_context(
            session,
            tenant_id=tenant_id,
            tenant_pub_id=self._tenant_pub_id,
        )

    @staticmethod
    def _assert_campaign_specification(
        campaign: CollectionCampaign,
        blueprint: CampaignAssemblyBlueprint,
    ) -> None:
        expected = {
            key: value
            for key, value in blueprint.campaign_row_values.items()
            if key not in _CAMPAIGN_PROGRESS_FIELDS
        }
        _assert_model_matches(
            campaign,
            expected,
            code="campaign_header_exact_match_failed",
        )
        if campaign.state not in {"assembling", "frozen"}:
            raise CampaignMaterializationError(
                "campaign_state_invalid",
                state=campaign.state,
            )

    @staticmethod
    def _assert_static_membership_rows(
        session: Session,
        blueprint: CampaignAssemblyBlueprint,
    ) -> None:
        targets = list(
            session.scalars(
                select(CollectionCampaignTarget)
                .where(CollectionCampaignTarget.campaign_id == blueprint.id)
                .order_by(CollectionCampaignTarget.pub_id)
            )
        )
        legs = list(
            session.scalars(
                select(CollectionSamplingLeg)
                .where(CollectionSamplingLeg.campaign_id == blueprint.id)
                .order_by(CollectionSamplingLeg.pub_id)
            )
        )
        _assert_row_sets_exact(
            targets,
            blueprint.campaign_target_row_values,
            code="campaign_targets_exact_match_failed",
        )
        _assert_row_sets_exact(
            legs,
            blueprint.leg_row_values,
            code="campaign_legs_exact_match_failed",
        )

    @staticmethod
    def _checkpoint_from_row(
        session: Session,
        campaign: CollectionCampaign,
        blueprint: CampaignAssemblyBlueprint,
    ) -> CampaignMaterializationCheckpoint:
        cursor = campaign.materialization_cursor
        if (
            campaign.materialized_slot_count != cursor
            or cursor < 0
            or cursor > blueprint.expected_slot_count
        ):
            raise CampaignMaterializationError(
                "campaign_persisted_cursor_invalid",
                cursor=cursor,
                count=campaign.materialized_slot_count,
            )
        if cursor == 0:
            if campaign.materialization_state != "pending":
                raise CampaignMaterializationError(
                    "campaign_zero_cursor_state_invalid",
                    state=campaign.materialization_state,
                )
            return initial_campaign_checkpoint(blueprint)

        last_batch = session.scalar(
            select(CollectionCampaignMaterializationBatch).where(
                CollectionCampaignMaterializationBatch.campaign_id == blueprint.id,
                CollectionCampaignMaterializationBatch.tenant_id == blueprint.tenant_id,
                CollectionCampaignMaterializationBatch.project_id == blueprint.project_id,
                CollectionCampaignMaterializationBatch.end_slot_ordinal_exclusive == cursor,
            )
        )
        if (
            last_batch is None
            or last_batch.batch_state != "completed"
            or last_batch.specification_hash != blueprint.specification_hash
            or last_batch.slot_generator_version != blueprint.slot_generator_version
        ):
            raise CampaignMaterializationError(
                "campaign_checkpoint_batch_missing",
                cursor=cursor,
            )
        expected_state = "complete" if cursor == blueprint.expected_slot_count else "materializing"
        if campaign.materialization_state != expected_state:
            raise CampaignMaterializationError(
                "campaign_persisted_materialization_state_invalid",
                cursor=cursor,
                state=campaign.materialization_state,
            )
        return CampaignMaterializationCheckpoint(
            campaign_pub_id=blueprint.campaign_pub_id,
            specification_hash=blueprint.specification_hash,
            slot_generator_version=blueprint.slot_generator_version,
            next_slot_ordinal=cursor,
            materialized_slot_count=cursor,
            membership_chain_hash=last_batch.membership_chain_hash,
        )

    @staticmethod
    def _assert_committed_chunk_exact(
        session: Session,
        batch: CollectionCampaignMaterializationBatch,
        chunk: CampaignSlotChunk,
    ) -> None:
        expected_batch = {
            key: value
            for key, value in chunk.batch_insert_values.items()
            if key not in _BATCH_COMMIT_FIELDS
        }
        _assert_model_matches(
            batch,
            expected_batch,
            code="campaign_batch_exact_match_failed",
        )
        if batch.batch_state != "completed" or batch.committed_at is None:
            raise CampaignMaterializationError("campaign_batch_not_committed")

        slots = list(
            session.scalars(
                select(CollectionPrimarySlot)
                .where(CollectionPrimarySlot.materialization_batch_id == chunk.batch_id)
                .order_by(CollectionPrimarySlot.slot_ordinal)
            )
        )
        _assert_row_sets_exact(
            slots,
            chunk.slot_row_values,
            code="campaign_batch_slots_exact_match_failed",
        )

    @staticmethod
    def _freeze_confirmation(
        campaign: CollectionCampaign,
        blueprint: CampaignAssemblyBlueprint,
        checkpoint: CampaignMaterializationCheckpoint,
    ) -> PersistedCampaignFreezeConfirmation:
        if (
            campaign.state != "frozen"
            or campaign.materialization_state != "complete"
            or campaign.materialization_cursor != blueprint.expected_slot_count
            or campaign.materialized_slot_count != blueprint.expected_slot_count
            or campaign.membership_hash != checkpoint.membership_chain_hash
            or campaign.frozen_at != blueprint.requested_frozen_at
        ):
            raise CampaignMaterializationError("campaign_freeze_exact_match_failed")
        return PersistedCampaignFreezeConfirmation(
            campaign_id=campaign.id,
            campaign_pub_id=campaign.pub_id,
            tenant_id=campaign.tenant_id,
            project_id=campaign.project_id,
            specification_hash=campaign.specification_hash,
            slot_generator_version=blueprint.slot_generator_version,
            membership_digest_version=blueprint.membership_digest_version,
            expected_slot_count=campaign.expected_slot_count,
            materialized_slot_count=campaign.materialized_slot_count,
            materialization_state="complete",
            materialization_cursor=campaign.materialization_cursor,
            membership_hash=campaign.membership_hash,
            frozen_at=campaign.frozen_at,
            state="frozen",
        )

    @staticmethod
    def _fail(code: str, **context: str | int | bool | None) -> NoReturn:
        raise CampaignMaterializationError(code, **context)


def _assert_row_sets_exact(
    actual_rows: Sequence[Any],
    expected_rows: Sequence[Mapping[str, object]],
    *,
    code: str,
) -> None:
    if len(actual_rows) != len(expected_rows):
        raise CampaignMaterializationError(
            code,
            expected_count=len(expected_rows),
            actual_count=len(actual_rows),
        )
    actual_by_id = {row.id: row for row in actual_rows}
    if len(actual_by_id) != len(actual_rows):
        raise CampaignMaterializationError(code)
    for expected in expected_rows:
        row = actual_by_id.get(expected["id"])
        if row is None:
            raise CampaignMaterializationError(code)
        _assert_model_matches(row, expected, code=code)


def _one_identity(first: Any | None, second: Any | None, *, code: str) -> Any | None:
    if first is not None and second is not None and first.id != second.id:
        raise CampaignMaterializationError(code)
    return first if first is not None else second


def _assert_model_matches(
    row: Any,
    expected: Mapping[str, object],
    *,
    code: str,
) -> None:
    for field, expected_value in expected.items():
        if getattr(row, field) != expected_value:
            raise CampaignMaterializationError(code, field=field)
