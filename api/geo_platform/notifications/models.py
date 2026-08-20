from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..tenancy.database import Base
from ..tenancy.models import now_utc


class Notice(Base):
    __tablename__ = "notice"
    __table_args__ = (
        UniqueConstraint("kind", "fingerprint", name="notice_kind_fingerprint_key"),
        {"schema": "notification"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pub_id: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(Text, default="feishu_app")
    fingerprint: Mapped[str] = mapped_column(Text)
    tenant_pub_id: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text)
    desired_state: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    target_chat_id: Mapped[str] = mapped_column(Text)
    message_id: Mapped[str | None] = mapped_column(Text)
    session_kind: Mapped[str | None] = mapped_column(Text)
    resource_pub_id: Mapped[str | None] = mapped_column(Text)
    assist_ticket_sha256: Mapped[str | None] = mapped_column(Text)
    claimed_actor_hash: Mapped[str | None] = mapped_column(Text)
    claimed_actor_mask: Mapped[str | None] = mapped_column(Text)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_card_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )
    delivery_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_delivery_error: Mapped[str | None] = mapped_column(Text)


class DeliveryCommand(Base):
    __tablename__ = "delivery_command"
    __table_args__ = (
        UniqueConstraint(
            "notice_id",
            "operation",
            "notice_revision",
            name="delivery_command_notice_id_operation_notice_revision_key",
        ),
        {"schema": "notification"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    command_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)
    notice_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("notification.notice.id", ondelete="CASCADE")
    )
    operation: Mapped[str] = mapped_column(Text)
    notice_revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(Text, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    request_log_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class Interaction(Base):
    __tablename__ = "interaction"
    __table_args__ = {"schema": "notification"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(Text, unique=True)
    notice_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("notification.notice.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(Text)
    actor_hash: Mapped[str] = mapped_column(Text)
    actor_mask: Mapped[str] = mapped_column(Text)
    result: Mapped[str] = mapped_column(Text)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class CallbackReplay(Base):
    __tablename__ = "callback_replay"
    __table_args__ = {"schema": "notification"}

    replay_key: Mapped[str] = mapped_column(Text, primary_key=True)
    event_id: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AuditEvent(Base):
    __tablename__ = "audit_event"
    __table_args__ = {"schema": "notification"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    notice_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("notification.notice.id", ondelete="SET NULL")
    )
    actor_hash: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    result: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
