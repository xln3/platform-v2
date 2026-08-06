import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..tenancy.database import Base
from ..tenancy.models import now_utc, uuid_pk


class TenantModel:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid_pk)
    pub_id: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.tenant.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class Customer(TenantModel, Base):
    __tablename__ = "customer"
    name: Mapped[str] = mapped_column(String(200))
    external_ref: Mapped[str | None] = mapped_column(String(200))


class Project(TenantModel, Base):
    __tablename__ = "project"
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.customer.id"))
    name: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(30), default="draft")


class Brand(TenantModel, Base):
    __tablename__ = "brand"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.project.id"))
    name: Mapped[str] = mapped_column(String(200))
    website: Mapped[str | None] = mapped_column(String(500))


class BrandAlias(TenantModel, Base):
    __tablename__ = "brand_alias"
    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.brand.id"))
    value: Mapped[str] = mapped_column(String(200))


class BrandAsset(TenantModel, Base):
    __tablename__ = "brand_asset"
    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.brand.id"))
    kind: Mapped[str] = mapped_column(String(40))
    uri: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str | None] = mapped_column(String(64))


class Competitor(TenantModel, Base):
    __tablename__ = "competitor"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.project.id"))
    name: Mapped[str] = mapped_column(String(200))
    website: Mapped[str | None] = mapped_column(String(500))


class MonitoringConfig(TenantModel, Base):
    __tablename__ = "monitoring_config"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.project.id"), unique=True)
    state: Mapped[str] = mapped_column(String(30), default="draft")
    current_version: Mapped[int] = mapped_column(Integer, default=0)


class MonitoringConfigVersion(TenantModel, Base):
    __tablename__ = "monitoring_config_version"
    __table_args__ = (UniqueConstraint("config_id", "revision"),)
    config_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.monitoring_config.id"))
    revision: Mapped[int] = mapped_column(Integer)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_json: Mapped[str] = mapped_column(Text)
    snapshot_hash: Mapped[str] = mapped_column(String(64))


class QueryGroup(TenantModel, Base):
    __tablename__ = "query_group"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.project.id"))
    name: Mapped[str] = mapped_column(String(200))


class QueryItem(TenantModel, Base):
    __tablename__ = "query_item"
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.query_group.id"))
    text: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=100)


class ClientGoal(TenantModel, Base):
    __tablename__ = "client_goal"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.project.id"))
    metric: Mapped[str] = mapped_column(String(80))
    target_json: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(30), default="draft")


class ChangeRequest(TenantModel, Base):
    __tablename__ = "change_request"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.project.id"))
    kind: Mapped[str] = mapped_column(String(80))
    requested_json: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(30), default="pending")
    reviewed_by: Mapped[str | None] = mapped_column(String(30))


class ClientProfileVersion(TenantModel, Base):
    __tablename__ = "client_profile_version"
    __table_args__ = (UniqueConstraint("project_id", "revision"),)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.project.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    company_name: Mapped[str] = mapped_column(String(200))
    contact_role: Mapped[str] = mapped_column(String(120))
    audience: Mapped[str] = mapped_column(Text)
    public_statement: Mapped[str] = mapped_column(Text)
    declared_by: Mapped[str] = mapped_column(String(255))


class AssetConfirmationVersion(TenantModel, Base):
    __tablename__ = "asset_confirmation_version"
    __table_args__ = (UniqueConstraint("project_id", "revision"),)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.project.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    brand_name: Mapped[str] = mapped_column(String(200))
    website: Mapped[str] = mapped_column(String(500))
    product_name: Mapped[str] = mapped_column(String(200))
    competitor_name: Mapped[str] = mapped_column(String(200))
    prohibited_claim: Mapped[str] = mapped_column(Text)
    declared_by: Mapped[str] = mapped_column(String(255))
