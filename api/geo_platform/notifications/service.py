from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..collection.account_models import CollectionPhoneAccount, CollectionPlatformAccount
from ..tenancy.ids import new_pub_id
from .models import AuditEvent, DeliveryCommand, Notice
from .redaction import redact_notification_text

_COMMAND_NAMESPACE = uuid.UUID("eb6cd5c5-6c31-4ecb-98cc-d4388cb18d85")
_FINAL_STATES = frozenset({"solved", "expired", "closed"})


class NotificationConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class AlertRecordResult:
    notification_id: str
    created: bool
    transition: bool
    delivery_enqueued: bool


@dataclass(frozen=True)
class ClaimedDelivery:
    command_id: int
    command_uuid: uuid.UUID
    operation: str
    notice_revision: int


def utc_now() -> datetime:
    return datetime.now(UTC)


def mask_phone(value: str | None) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) < 7:
        return "未绑定"
    return f"{digits[:3]}****{digits[-4:]}"


def _short_session_id(session_id: str) -> str:
    return "ast_" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]


def _safe_summary(value: str, *, limit: int) -> str:
    return " ".join(redact_notification_text(value).split())[:limit]


def _command_uuid(notice_pub_id: str, operation: str, revision: int) -> uuid.UUID:
    return uuid.uuid5(_COMMAND_NAMESPACE, f"{notice_pub_id}:{operation}:{revision}")


def notice_card_mapping(notice: Notice) -> dict[str, Any]:
    return {
        "pub_id": notice.pub_id,
        "kind": notice.kind,
        "state": notice.state,
        "desired_state": notice.desired_state,
        "severity": notice.severity,
        "title": notice.title,
        "summary": dict(notice.summary),
        "resource_pub_id": notice.resource_pub_id,
        "assist_ticket_sha256": notice.assist_ticket_sha256,
        "claimed_actor_mask": notice.claimed_actor_mask,
        "claimed_at": notice.claimed_at,
        "occurrence_count": notice.occurrence_count,
        "last_seen_at": notice.last_seen_at,
        "resolved_at": notice.resolved_at,
        "expires_at": notice.expires_at,
        "created_at": notice.created_at,
        "updated_at": notice.updated_at,
    }


class NotificationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _audit(
        self,
        notice: Notice | None,
        *,
        actor_hash: str,
        action: str,
        result: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(
            AuditEvent(
                notice_id=notice.id if notice is not None else None,
                actor_hash=actor_hash[:64],
                action=action[:120],
                result=result[:80],
                detail=detail or {},
            )
        )

    def _queue(self, notice: Notice, operation: str, *, now: datetime | None = None) -> bool:
        queued_at = now or utc_now()
        in_flight = self.session.scalar(
            select(DeliveryCommand.id)
            .where(
                DeliveryCommand.notice_id == notice.id,
                DeliveryCommand.operation == operation,
                DeliveryCommand.state.in_(["pending", "dispatching"]),
            )
            .limit(1)
        )
        if in_flight is not None:
            # Commands render from the latest notice at dispatch time. Keeping
            # one in-flight send/update coalesces rapid claim/repeat/resolved
            # transitions without losing their eventual final projection.
            return False
        statement = (
            pg_insert(DeliveryCommand)
            .values(
                command_uuid=_command_uuid(notice.pub_id, operation, notice.revision),
                notice_id=notice.id,
                operation=operation,
                notice_revision=notice.revision,
                state="pending",
                attempts=0,
                next_attempt_at=queued_at,
                created_at=queued_at,
                updated_at=queued_at,
            )
            .on_conflict_do_nothing(index_elements=["notice_id", "operation", "notice_revision"])
            .returning(DeliveryCommand.id)
        )
        inserted = self.session.execute(statement).scalar_one_or_none() is not None
        if inserted:
            notice.last_card_enqueued_at = queued_at
        return inserted

    def _account_projection(self, *, platform: str, instance_key: str) -> tuple[str, str]:
        row = self.session.execute(
            select(CollectionPhoneAccount.phone, CollectionPlatformAccount.region_gb)
            .join(
                CollectionPlatformAccount,
                CollectionPlatformAccount.phone_account_id == CollectionPhoneAccount.id,
            )
            .where(
                CollectionPlatformAccount.platform == platform,
                CollectionPlatformAccount.browser_instance_key == instance_key,
            )
            .limit(1)
        ).one_or_none()
        if row is not None:
            return mask_phone(row.phone), str(row.region_gb or "-")
        suffix = instance_key.rsplit("_", 1)[-1].lower() if "_" in instance_key else ""
        region = {"bj": "北京", "sh": "上海", "tj": "天津"}.get(suffix, "-")
        return "未绑定", region

    def enqueue_assist(
        self,
        *,
        tenant_pub_id: str | None,
        session_kind: str,
        run_pub_id: str,
        session_id: str,
        ticket_sha256: str,
        platform: str,
        instance_key: str,
        business_key: str,
        created_at_epoch: int,
        expires_at_epoch: int,
        target_chat_id: str,
    ) -> str:
        if session_kind not in {"workflow_captcha", "otp_cli"}:
            raise ValueError("invalid_session_kind")
        if not target_chat_id:
            raise ValueError("notification_chat_not_configured")
        if len(ticket_sha256) != 64 or any(c not in "0123456789abcdef" for c in ticket_sha256):
            raise ValueError("invalid_ticket_sha256")
        fingerprint = hashlib.sha256(f"{session_kind}:{session_id}".encode()).hexdigest()
        lock_key = int.from_bytes(
            hashlib.sha256(f"assist:{fingerprint}".encode()).digest()[:8],
            byteorder="big",
            signed=True,
        )
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )
        existing = self.session.scalar(
            select(Notice).where(Notice.kind == "assist", Notice.fingerprint == fingerprint)
        )
        if existing is not None:
            if existing.assist_ticket_sha256 != ticket_sha256:
                raise NotificationConflictError("assist_notification_fingerprint_conflict")
            return existing.pub_id

        account_mask, region = self._account_projection(
            platform=platform, instance_key=instance_key
        )
        created_at = datetime.fromtimestamp(created_at_epoch, UTC)
        expires_at = datetime.fromtimestamp(expires_at_epoch, UTC)
        notice = Notice(
            pub_id=new_pub_id("ntf"),
            kind="assist",
            channel="feishu_app",
            fingerprint=fingerprint,
            tenant_pub_id=tenant_pub_id,
            state="pending_delivery",
            desired_state="active",
            severity="warning",
            title="[GEO] 人工接管请求",
            summary={
                "event_type": "验证码接管"
                if session_kind == "workflow_captcha"
                else "登录 / OTP 接管",
                "platform": _safe_summary(platform, limit=80),
                "instance_key": _safe_summary(instance_key, limit=120),
                "region": region,
                "account_mask": account_mask,
                "session_public_id": _short_session_id(session_id),
                "reason": _safe_summary(business_key, limit=500),
            },
            target_chat_id=target_chat_id,
            session_kind=session_kind,
            resource_pub_id=run_pub_id[:160],
            assist_ticket_sha256=ticket_sha256,
            occurrence_count=1,
            revision=1,
            last_seen_at=created_at,
            expires_at=expires_at,
            created_at=created_at,
            updated_at=created_at,
        )
        self.session.add(notice)
        self.session.flush()
        self._queue(notice, "send", now=created_at)
        self._audit(
            notice,
            actor_hash="system",
            action="assist_notification_created",
            result="pending_delivery",
            detail={"session_kind": session_kind},
        )
        return notice.pub_id

    def mark_assist_state_by_ticket(
        self,
        *,
        ticket_sha256: str,
        state: str,
        actor_hash: str = "system",
        actor_mask: str | None = None,
        now: datetime | None = None,
    ) -> Notice | None:
        if state not in {"active", "claimed", "solved", "expired", "closed"}:
            raise ValueError("invalid_assist_state")
        at = now or utc_now()
        notice = self.session.scalar(
            select(Notice)
            .where(Notice.kind == "assist", Notice.assist_ticket_sha256 == ticket_sha256)
            .with_for_update()
        )
        if notice is None:
            return None
        if notice.state == state and notice.desired_state == state:
            return notice
        if notice.desired_state in _FINAL_STATES and notice.desired_state != state:
            self._audit(
                notice,
                actor_hash=actor_hash,
                action="assist_state_ignored_terminal",
                result=state,
            )
            return notice
        notice.state = state if notice.message_id else "pending_delivery"
        notice.desired_state = state
        notice.updated_at = at
        notice.last_seen_at = at
        if state in _FINAL_STATES:
            notice.resolved_at = at
        if actor_hash != "system":
            notice.claimed_actor_hash = actor_hash
            notice.claimed_actor_mask = actor_mask
        notice.revision += 1
        self._queue(notice, "update" if notice.message_id else "send", now=at)
        self._audit(
            notice,
            actor_hash=actor_hash,
            action="assist_state_changed",
            result=state,
        )
        return notice

    def record_alert(
        self,
        alert: dict[str, str],
        *,
        target_chat_id: str,
        repeat_window_seconds: int,
        card_update_seconds: int,
        now: datetime | None = None,
    ) -> AlertRecordResult:
        at = now or utc_now()
        status = alert.get("status", "firing").lower()
        desired = "solved" if status == "resolved" else "active"
        fingerprint = alert.get("fingerprint", "")[:128]
        if not fingerprint:
            canonical = json.dumps(
                {
                    key: alert.get(key, "")
                    for key in (
                        "alertname",
                        "severity",
                        "category",
                        "service",
                        "region",
                        "starts_at",
                    )
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        # Serialize the first insert as well as later updates for one alert.
        # ``SELECT .. FOR UPDATE`` cannot protect a row that does not exist yet,
        # while this transaction-scoped lock keeps concurrent Alertmanager
        # deliveries from racing the unique constraint and losing an intake.
        lock_key = int.from_bytes(
            hashlib.sha256(fingerprint.encode("utf-8")).digest()[:8],
            byteorder="big",
            signed=True,
        )
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )
        summary = {
            key: _safe_summary(alert.get(key, ""), limit=500 if key == "summary" else 160)
            for key in (
                "alertname",
                "severity",
                "category",
                "service",
                "region",
                "summary",
                "starts_at",
                "ends_at",
            )
            if alert.get(key)
        }
        notice = self.session.scalar(
            select(Notice)
            .where(Notice.kind == "alert", Notice.fingerprint == fingerprint)
            .with_for_update()
        )
        if notice is None:
            # A resolved callback can arrive after rollout or during a warning
            # quiet window without a corresponding firing card. There is no
            # original message to close, so do not create a standalone green card.
            if desired == "solved":
                return AlertRecordResult("", False, False, False)
            notice = Notice(
                pub_id=new_pub_id("ntf"),
                kind="alert",
                channel="feishu_app",
                fingerprint=fingerprint,
                state="pending_delivery",
                desired_state=desired,
                severity=_safe_summary(alert.get("severity", "warning"), limit=32),
                title=f"[GEO告警] {_safe_summary(alert.get('alertname', 'unknown'), limit=90)}",
                summary=summary,
                target_chat_id=target_chat_id,
                occurrence_count=1,
                revision=1,
                last_seen_at=at,
                resolved_at=at if desired == "solved" else None,
                created_at=at,
                updated_at=at,
            )
            self.session.add(notice)
            self.session.flush()
            queued = self._queue(notice, "send", now=at)
            self._audit(
                notice,
                actor_hash="alertmanager",
                action="alert_transition",
                result=status,
            )
            return AlertRecordResult(notice.pub_id, True, True, queued)

        notice.occurrence_count += 1
        notice.last_seen_at = at
        notice.summary = summary
        notice.severity = _safe_summary(alert.get("severity", notice.severity), limit=32)
        transition = notice.desired_state != desired
        enqueue = False
        if transition:
            notice.desired_state = desired
            notice.state = desired if notice.message_id else "pending_delivery"
            notice.resolved_at = at if desired == "solved" else None
            notice.revision += 1
            enqueue = True
        elif desired == "active":
            last_card = notice.last_card_enqueued_at or notice.created_at
            # Alertmanager repeat_interval is the hard resend window.  Card edits
            # are coalesced further to protect Feishu's per-message edit quota.
            minimum = max(1, min(repeat_window_seconds, card_update_seconds))
            if at - last_card >= timedelta(seconds=minimum):
                notice.revision += 1
                enqueue = True
        notice.updated_at = at
        queued = (
            self._queue(notice, "update" if notice.message_id else "send", now=at)
            if enqueue
            else False
        )
        self._audit(
            notice,
            actor_hash="alertmanager",
            action="alert_transition" if transition else "alert_repeat",
            result=status,
            detail={"delivery_enqueued": queued},
        )
        return AlertRecordResult(notice.pub_id, False, transition, queued)

    def expire_due_assists(self, *, now: datetime | None = None, limit: int = 100) -> int:
        at = now or utc_now()
        notices = list(
            self.session.scalars(
                select(Notice)
                .where(
                    Notice.kind == "assist",
                    Notice.expires_at.is_not(None),
                    Notice.expires_at <= at,
                    Notice.desired_state.in_(["active", "claimed"]),
                )
                .order_by(Notice.expires_at)
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        )
        for notice in notices:
            notice.desired_state = "expired"
            notice.state = "expired" if notice.message_id else "pending_delivery"
            notice.resolved_at = at
            notice.updated_at = at
            notice.revision += 1
            self._queue(notice, "update" if notice.message_id else "send", now=at)
            self._audit(
                notice,
                actor_hash="system",
                action="assist_expired",
                result="expired",
            )
        return len(notices)

    def claim_deliveries(
        self,
        *,
        limit: int,
        max_attempts: int,
        now: datetime | None = None,
    ) -> list[ClaimedDelivery]:
        at = now or utc_now()
        stale = at - timedelta(minutes=5)
        stale_rows = list(
            self.session.scalars(
                select(DeliveryCommand)
                .where(
                    DeliveryCommand.state == "dispatching",
                    DeliveryCommand.locked_at < stale,
                    DeliveryCommand.attempts < max_attempts,
                )
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        )
        for command in stale_rows:
            command.state = "pending"
            command.locked_at = None
            command.next_attempt_at = at
        commands = list(
            self.session.scalars(
                select(DeliveryCommand)
                .where(
                    DeliveryCommand.state == "pending",
                    DeliveryCommand.attempts < max_attempts,
                    DeliveryCommand.next_attempt_at <= at,
                )
                .order_by(DeliveryCommand.id)
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        )
        for command in commands:
            command.state = "dispatching"
            command.locked_at = at
            command.updated_at = at
        self.session.flush()
        return [
            ClaimedDelivery(
                command_id=command.id,
                command_uuid=command.command_uuid,
                operation=command.operation,
                notice_revision=command.notice_revision,
            )
            for command in commands
        ]

    def delivery_context(self, command_id: int) -> tuple[DeliveryCommand, Notice]:
        row = self.session.execute(
            select(DeliveryCommand, Notice)
            .join(Notice, Notice.id == DeliveryCommand.notice_id)
            .where(DeliveryCommand.id == command_id)
        ).one()
        return row[0], row[1]

    def delivery_succeeded(
        self,
        *,
        command_id: int,
        message_id: str | None,
        request_log_id: str | None,
        now: datetime | None = None,
    ) -> None:
        at = now or utc_now()
        command, notice = self.session.execute(
            select(DeliveryCommand, Notice)
            .join(Notice, Notice.id == DeliveryCommand.notice_id)
            .where(DeliveryCommand.id == command_id)
            .with_for_update()
        ).one()
        command.state = "succeeded"
        command.attempts += 1
        command.locked_at = None
        command.last_error = None
        command.request_log_id = request_log_id[:160] if request_log_id else None
        command.updated_at = at
        if command.operation == "send":
            if not message_id:
                raise ValueError("message_id_required_for_send")
            notice.message_id = message_id[:200]
            if notice.state in {"pending_delivery", "delivery_failed"}:
                notice.state = notice.desired_state
        notice.delivery_failed_at = None
        notice.last_delivery_error = None
        notice.updated_at = at
        if notice.revision > command.notice_revision:
            self._queue(notice, "update", now=at)
        self._audit(
            notice,
            actor_hash="feishu_app",
            action=f"delivery_{command.operation}",
            result="succeeded",
            detail={"request_log_id": command.request_log_id} if command.request_log_id else {},
        )

    def delivery_failed(
        self,
        *,
        command_id: int,
        marker: str,
        request_log_id: str | None,
        max_attempts: int,
        retryable: bool,
        now: datetime | None = None,
    ) -> None:
        at = now or utc_now()
        command, notice = self.session.execute(
            select(DeliveryCommand, Notice)
            .join(Notice, Notice.id == DeliveryCommand.notice_id)
            .where(DeliveryCommand.id == command_id)
            .with_for_update()
        ).one()
        command.attempts += 1
        exhausted = command.attempts >= max_attempts or not retryable
        command.state = "dead" if exhausted else "pending"
        command.locked_at = None
        command.last_error = _safe_summary(marker, limit=200)
        command.request_log_id = request_log_id[:160] if request_log_id else None
        command.updated_at = at
        if not exhausted:
            command.next_attempt_at = at + timedelta(
                seconds=min(300, 2 ** min(command.attempts, 8))
            )
        notice.last_delivery_error = command.last_error
        notice.updated_at = at
        if exhausted and command.operation == "send" and not notice.message_id:
            notice.state = "delivery_failed"
            notice.delivery_failed_at = at
        self._audit(
            notice,
            actor_hash="feishu_app",
            action=f"delivery_{command.operation}",
            result="dead" if exhausted else "retry_scheduled",
            detail={
                "error": command.last_error,
                **({"request_log_id": command.request_log_id} if command.request_log_id else {}),
            },
        )
