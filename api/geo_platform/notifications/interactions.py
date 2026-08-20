"""Validated, durable handling for Feishu ``card.action.trigger`` callbacks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..collection.assist_completion import (
    AssistCompletionError,
    WorkflowSignalConflictError,
    prepare_assist_completion,
)
from ..collection.assist_registry import (
    DEFAULT_ASSIST_DIR,
    AssistRegistryError,
    load_registry_by_digest,
)
from .models import CallbackReplay, Interaction, Notice
from .security import actor_hash, mask_actor
from .service import NotificationService, utc_now

_ACTIONS = frozenset({"claim", "release", "recheck", "complete"})
_TERMINAL = frozenset({"solved", "expired", "closed"})


class InteractionProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedCardAction:
    event_id: str
    app_id: str
    tenant_key: str
    open_id: str
    notification_id: str
    action: str
    message_id: str
    chat_id: str


@dataclass(frozen=True)
class InteractionResult:
    response: dict[str, Any]
    finalize_ticket_sha256: str | None = None


def _bounded_text(value: object, *, name: str, limit: int, required: bool = True) -> str:
    if not isinstance(value, str):
        if required:
            raise InteractionProtocolError(f"{name}_invalid")
        return ""
    result = value.strip()
    if (required and not result) or len(result) > limit:
        raise InteractionProtocolError(f"{name}_invalid")
    return result


def parse_card_action(payload: dict[str, Any]) -> ParsedCardAction:
    if payload.get("schema") not in {"2.0", "2.0.0"}:
        raise InteractionProtocolError("callback_schema_invalid")
    header = payload.get("header")
    event = payload.get("event")
    if not isinstance(header, dict) or not isinstance(event, dict):
        raise InteractionProtocolError("callback_shape_invalid")
    if header.get("event_type") != "card.action.trigger":
        raise InteractionProtocolError("callback_event_type_invalid")

    operator = event.get("operator")
    action_value = event.get("action")
    context = event.get("context")
    if not isinstance(operator, dict) or not isinstance(action_value, dict):
        raise InteractionProtocolError("callback_action_shape_invalid")
    if not isinstance(context, dict):
        context = {}
    operator_id = operator.get("operator_id")
    open_id_value = operator.get("open_id")
    if not isinstance(open_id_value, str) and isinstance(operator_id, dict):
        open_id_value = operator_id.get("open_id")
    value = action_value.get("value")
    if not isinstance(value, dict):
        raise InteractionProtocolError("callback_action_value_invalid")
    if str(value.get("v", "")) != "1":
        raise InteractionProtocolError("callback_action_version_invalid")

    action = _bounded_text(value.get("action"), name="callback_action", limit=32)
    if action not in _ACTIONS:
        raise InteractionProtocolError("callback_action_unknown")
    return ParsedCardAction(
        event_id=_bounded_text(header.get("event_id"), name="callback_event_id", limit=200),
        app_id=_bounded_text(header.get("app_id"), name="callback_app_id", limit=160),
        tenant_key=_bounded_text(header.get("tenant_key"), name="callback_tenant_key", limit=160),
        open_id=_bounded_text(open_id_value, name="callback_open_id", limit=160),
        notification_id=_bounded_text(
            value.get("notification_id"), name="callback_notification_id", limit=80
        ),
        action=action,
        message_id=_bounded_text(
            context.get("open_message_id"),
            name="callback_message_id",
            limit=200,
        ),
        chat_id=_bounded_text(
            context.get("open_chat_id"),
            name="callback_chat_id",
            limit=200,
        ),
    )


def _toast(content: str, *, level: str = "success") -> dict[str, Any]:
    return {"toast": {"type": level, "content": content[:120]}}


class InteractionService:
    def __init__(
        self,
        session: Session,
        *,
        app_id: str,
        tenant_key: str,
        allowed_open_ids: frozenset[str],
        replay_ttl_seconds: int,
        registry_dir: Path = DEFAULT_ASSIST_DIR,
    ) -> None:
        self.session = session
        self.app_id = app_id
        self.tenant_key = tenant_key
        self.allowed_open_ids = allowed_open_ids
        self.replay_ttl_seconds = replay_ttl_seconds
        self.registry_dir = registry_dir
        self.notifications = NotificationService(session)

    def _persist_replay(self, *, replay_key: str, event_id: str, now: datetime) -> None:
        self.session.execute(delete(CallbackReplay).where(CallbackReplay.expires_at <= now))
        statement = (
            pg_insert(CallbackReplay)
            .values(
                replay_key=replay_key,
                event_id=event_id,
                expires_at=now + timedelta(seconds=self.replay_ttl_seconds * 2),
                created_at=now,
            )
            .on_conflict_do_update(
                index_elements=["replay_key"],
                set_={"event_id": CallbackReplay.event_id},
            )
            .returning(CallbackReplay.event_id)
        )
        persisted_event = self.session.execute(statement).scalar_one()
        if persisted_event != event_id:
            raise InteractionProtocolError("callback_replay_conflict")

    def _insert_or_replay(
        self, action: ParsedCardAction, *, actor_digest: str, actor_mask: str, now: datetime
    ) -> tuple[Interaction, bool]:
        statement = (
            pg_insert(Interaction)
            .values(
                event_id=action.event_id,
                notice_id=None,
                action=action.action,
                actor_hash=actor_digest,
                actor_mask=actor_mask,
                result="processing",
                response={},
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["event_id"])
            .returning(Interaction.id)
        )
        inserted_id = self.session.execute(statement).scalar_one_or_none()
        interaction = self.session.scalar(
            select(Interaction).where(Interaction.event_id == action.event_id)
        )
        if interaction is None:
            raise RuntimeError("interaction_insert_lost")
        if inserted_id is None and (
            interaction.action != action.action
            or interaction.actor_hash != actor_digest
            or interaction.actor_mask != actor_mask
        ):
            raise InteractionProtocolError("callback_event_contract_mismatch")
        return interaction, inserted_id is not None

    def _finish(
        self,
        interaction: Interaction,
        notice: Notice | None,
        *,
        result: str,
        response: dict[str, Any],
        actor_digest: str,
        action: str,
        now: datetime,
    ) -> InteractionResult:
        interaction.notice_id = notice.id if notice is not None else None
        interaction.result = result
        interaction.response = response
        interaction.updated_at = now
        self.notifications._audit(
            notice,
            actor_hash=actor_digest,
            action=f"card_{action}",
            result=result,
        )
        return InteractionResult(response=response)

    def _set_assist_state(
        self,
        notice: Notice,
        state: str,
        *,
        now: datetime,
        clear_claim: bool = False,
    ) -> None:
        notice.desired_state = state
        notice.state = state if notice.message_id else "pending_delivery"
        if clear_claim:
            notice.claimed_actor_hash = None
            notice.claimed_actor_mask = None
            notice.claimed_at = None
        if state in _TERMINAL:
            notice.resolved_at = now
        notice.updated_at = now
        notice.last_seen_at = now
        notice.revision += 1
        self.notifications._queue(notice, "update" if notice.message_id else "send", now=now)

    def handle(
        self,
        payload: dict[str, Any],
        *,
        replay_key: str,
        now: datetime | None = None,
    ) -> InteractionResult:
        action = parse_card_action(payload)
        if action.app_id != self.app_id:
            raise InteractionProtocolError("callback_app_mismatch")
        if action.tenant_key != self.tenant_key:
            raise InteractionProtocolError("callback_tenant_mismatch")
        at = now or utc_now()
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        digest = actor_hash(action.open_id)
        masked = mask_actor(action.open_id)
        self._persist_replay(replay_key=replay_key, event_id=action.event_id, now=at)
        interaction, inserted = self._insert_or_replay(
            action, actor_digest=digest, actor_mask=masked, now=at
        )
        if not inserted:
            existing_notice = (
                self.session.get(Notice, interaction.notice_id)
                if interaction.notice_id is not None
                else None
            )
            if existing_notice is not None and existing_notice.pub_id != action.notification_id:
                raise InteractionProtocolError("callback_event_contract_mismatch")
            finalize = None
            if interaction.action == "complete" and interaction.result == "succeeded":
                finalize = (
                    existing_notice.assist_ticket_sha256 if existing_notice is not None else None
                )
            return InteractionResult(
                response=dict(interaction.response),
                finalize_ticket_sha256=finalize,
            )

        notice = self.session.scalar(
            select(Notice).where(Notice.pub_id == action.notification_id).with_for_update()
        )
        if notice is None or notice.kind != "assist":
            return self._finish(
                interaction,
                None,
                result="not_found",
                response=_toast("接管通知不存在或已删除", level="warning"),
                actor_digest=digest,
                action=action.action,
                now=at,
            )
        if (action.message_id and action.message_id != notice.message_id) or (
            action.chat_id and action.chat_id != notice.target_chat_id
        ):
            return self._finish(
                interaction,
                notice,
                result="context_mismatch",
                response=_toast("卡片上下文不匹配，操作已拒绝", level="warning"),
                actor_digest=digest,
                action=action.action,
                now=at,
            )
        if action.open_id not in self.allowed_open_ids:
            return self._finish(
                interaction,
                notice,
                result="forbidden",
                response=_toast("你没有执行此操作的权限", level="warning"),
                actor_digest=digest,
                action=action.action,
                now=at,
            )
        if notice.expires_at is not None and at >= notice.expires_at:
            if notice.desired_state not in _TERMINAL:
                self._set_assist_state(notice, "expired", now=at)
            return self._finish(
                interaction,
                notice,
                result="expired",
                response=_toast("接管会话已过期", level="warning"),
                actor_digest=digest,
                action=action.action,
                now=at,
            )
        if notice.desired_state in _TERMINAL:
            return self._finish(
                interaction,
                notice,
                result="terminal",
                response=_toast("接管会话已结束", level="warning"),
                actor_digest=digest,
                action=action.action,
                now=at,
            )

        ticket_sha256 = notice.assist_ticket_sha256
        if not ticket_sha256:
            return self._finish(
                interaction,
                notice,
                result="registry_missing",
                response=_toast("接管会话不可用", level="warning"),
                actor_digest=digest,
                action=action.action,
                now=at,
            )
        try:
            registry = load_registry_by_digest(
                self.registry_dir, ticket_sha256, require_usable=False
            )
        except AssistRegistryError:
            return self._finish(
                interaction,
                notice,
                result="registry_missing",
                response=_toast("无法读取接管会话，请稍后重试", level="warning"),
                actor_digest=digest,
                action=action.action,
                now=at,
            )

        registry_state = str(registry.get("state"))
        registry_expiry = float(registry.get("expires_at", 0))
        if registry_state == "solved":
            self._set_assist_state(notice, "solved", now=at)
            return self._finish(
                interaction,
                notice,
                result="succeeded",
                response=_toast("会话已完成，卡片状态已收口"),
                actor_digest=digest,
                action=action.action,
                now=at,
            )
        if registry_state == "closed" or at.timestamp() >= registry_expiry:
            final_state = "expired" if at.timestamp() >= registry_expiry else "closed"
            self._set_assist_state(notice, final_state, now=at)
            return self._finish(
                interaction,
                notice,
                result=final_state,
                response=_toast("接管会话已结束", level="warning"),
                actor_digest=digest,
                action=action.action,
                now=at,
            )

        if action.action == "claim":
            if notice.desired_state == "claimed":
                if notice.claimed_actor_hash == digest:
                    return self._finish(
                        interaction,
                        notice,
                        result="succeeded",
                        response=_toast("你已认领，无需重复操作"),
                        actor_digest=digest,
                        action=action.action,
                        now=at,
                    )
                return self._finish(
                    interaction,
                    notice,
                    result="already_claimed",
                    response=_toast("已有其他值班人员认领", level="warning"),
                    actor_digest=digest,
                    action=action.action,
                    now=at,
                )
            self._set_assist_state(notice, "claimed", now=at)
            notice.claimed_actor_hash = digest
            notice.claimed_actor_mask = masked
            notice.claimed_at = at
            return self._finish(
                interaction,
                notice,
                result="succeeded",
                response=_toast("认领成功"),
                actor_digest=digest,
                action=action.action,
                now=at,
            )

        if action.action == "release":
            if notice.desired_state != "claimed" or notice.claimed_actor_hash != digest:
                return self._finish(
                    interaction,
                    notice,
                    result="not_claimant",
                    response=_toast("只有当前认领人可以释放", level="warning"),
                    actor_digest=digest,
                    action=action.action,
                    now=at,
                )
            self._set_assist_state(notice, "active", now=at, clear_claim=True)
            return self._finish(
                interaction,
                notice,
                result="succeeded",
                response=_toast("已释放认领"),
                actor_digest=digest,
                action=action.action,
                now=at,
            )

        if action.action == "recheck":
            return self._finish(
                interaction,
                notice,
                result="succeeded",
                response=_toast("会话仍在等待人工完成"),
                actor_digest=digest,
                action=action.action,
                now=at,
            )

        if notice.desired_state != "claimed" or notice.claimed_actor_hash != digest:
            return self._finish(
                interaction,
                notice,
                result="not_claimant",
                response=_toast("请先认领；只有当前认领人可以确认完成", level="warning"),
                actor_digest=digest,
                action=action.action,
                now=at,
            )
        try:
            prepare_assist_completion(
                self.session,
                registry=registry,
                ticket_sha256=ticket_sha256,
                actor_hash=digest,
                actor_mask=masked,
            )
        except WorkflowSignalConflictError as error:
            raise InteractionProtocolError("workflow_signal_conflict") from error
        except AssistCompletionError:
            return self._finish(
                interaction,
                notice,
                result="completion_failed",
                response=_toast("完成回执未受理，请稍后重试", level="warning"),
                actor_digest=digest,
                action=action.action,
                now=at,
            )
        result = self._finish(
            interaction,
            notice,
            result="succeeded",
            response=_toast("已确认完成"),
            actor_digest=digest,
            action=action.action,
            now=at,
        )
        return InteractionResult(
            response=result.response,
            finalize_ticket_sha256=ticket_sha256,
        )
