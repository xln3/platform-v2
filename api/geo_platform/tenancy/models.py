import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def now_utc() -> datetime:
    return datetime.now(UTC)


def uuid_pk() -> uuid.UUID:
    return uuid.uuid4()


class Tenant(Base):
    __tablename__ = "tenant"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid_pk)
    pub_id: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(30), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class User(Base):
    __tablename__ = "app_user"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid_pk)
    pub_id: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    subject: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    is_service_account: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Membership(Base):
    __tablename__ = "membership"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid_pk)
    pub_id: Mapped[str] = mapped_column(String(30), unique=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.tenant.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.app_user.id"))
    role: Mapped[str] = mapped_column(String(30))
    state: Mapped[str] = mapped_column(String(30), default="active")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class RoleDefinition(Base):
    __tablename__ = "role"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid_pk)
    name: Mapped[str] = mapped_column(String(30), unique=True)
    description: Mapped[str] = mapped_column(Text)


class Permission(Base):
    __tablename__ = "permission"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid_pk)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(Text)


class RolePermission(Base):
    __tablename__ = "role_permission"
    __table_args__ = (UniqueConstraint("role_id", "permission_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid_pk)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.role.id"))
    permission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.permission.id"))


class ServiceCredential(Base):
    __tablename__ = "service_credential"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid_pk)
    pub_id: Mapped[str] = mapped_column(String(30), unique=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.tenant.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.app_user.id"))
    secret_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid_pk)
    pub_id: Mapped[str] = mapped_column(String(30), unique=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.tenant.id"))
    actor_pub_id: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(120))
    resource_type: Mapped[str] = mapped_column(String(80))
    resource_pub_id: Mapped[str] = mapped_column(String(30))
    receipt: Mapped[str] = mapped_column(Text, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
