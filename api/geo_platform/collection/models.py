import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..projects.models import TenantModel
from ..tenancy.database import Base
from ..tenancy.models import now_utc, uuid_pk


class PlatformAdapter(Base):
    __tablename__ = "platform_adapter"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid_pk)
    pub_id: Mapped[str] = mapped_column(String(30), unique=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    display_name: Mapped[str] = mapped_column(String(160))
    admission_level: Mapped[str] = mapped_column(String(30), default="catalogued")
    capabilities_json: Mapped[str] = mapped_column(Text, default="[]")
    adapter_version: Mapped[str] = mapped_column(String(80))
    last_passed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlatformAccount(TenantModel, Base):
    __tablename__ = "platform_account"
    adapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.platform_adapter.id"))
    owner_pub_id: Mapped[str] = mapped_column(String(30))
    account_mask: Mapped[str] = mapped_column(String(120))
    purpose: Mapped[str] = mapped_column(String(80))
    responsible_pub_id: Mapped[str] = mapped_column(String(30))
    custody_mode: Mapped[str] = mapped_column(String(30))
    region: Mapped[str] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(30), default="requested")
    admission_level: Mapped[str] = mapped_column(String(30), default="catalogued")


class AccountAuthorization(TenantModel, Base):
    __tablename__ = "account_authorization"
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.platform_account.id"))
    scopes_json: Mapped[str] = mapped_column(Text)
    forbidden_actions_json: Mapped[str] = mapped_column(Text, default="[]")
    regions_json: Mapped[str] = mapped_column(Text, default="[]")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BrowserProfile(TenantModel, Base):
    __tablename__ = "browser_profile"
    __table_args__ = (UniqueConstraint("account_id", "profile_version"),)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.platform_account.id"))
    profile_version: Mapped[int] = mapped_column(Integer)
    custody_mode: Mapped[str] = mapped_column(String(30))
    state: Mapped[str] = mapped_column(String(40), default="REQUESTED")
    constraints_json: Mapped[str] = mapped_column(Text, default="[]")
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    wrapped_dek: Mapped[bytes | None] = mapped_column(LargeBinary)
    ciphertext_sha256: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SessionLease(TenantModel, Base):
    __tablename__ = "session_lease"
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.platform_account.id"))
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.browser_profile.id"))
    holder: Mapped[str] = mapped_column(String(160))
    capability: Mapped[str] = mapped_column(String(30))
    fencing_token: Mapped[int] = mapped_column(BigInteger)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CapabilityLease(TenantModel, Base):
    __tablename__ = "capability_lease"
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.platform_account.id"))
    issued_by: Mapped[str] = mapped_column(String(160))
    subject_workflow_id: Mapped[str] = mapped_column(String(500))
    allowed_domains_json: Mapped[str] = mapped_column(Text)
    allowed_actions_json: Mapped[str] = mapped_column(Text)
    authorization_scope_json: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    use_count: Mapped[int] = mapped_column(Integer, default=0)


class ResourceLease(TenantModel, Base):
    __tablename__ = "resource_lease"
    resource_kind: Mapped[str] = mapped_column(String(30))
    resource_pub_id: Mapped[str] = mapped_column(String(30))
    holder: Mapped[str] = mapped_column(String(160))
    capability_json: Mapped[str] = mapped_column(Text)
    region: Mapped[str] = mapped_column(String(80))
    fencing_token: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResourceRegistration(TenantModel, Base):
    __tablename__ = "resource_registration"
    resource_kind: Mapped[str] = mapped_column(String(30))
    display_mask: Mapped[str] = mapped_column(String(160))
    capabilities_json: Mapped[str] = mapped_column(Text)
    region: Mapped[str] = mapped_column(String(80))
    concurrency_limit: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(30), default="active")
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CollectionRun(TenantModel, Base):
    __tablename__ = "collection_run"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.project.id"))
    config_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("platform.monitoring_config_version.id")
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))
    workflow_id: Mapped[str] = mapped_column(String(500), unique=True)
    temporal_run_id: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(30), default="pending")
    total_tasks: Mapped[int] = mapped_column(Integer, default=0)
    completed_tasks: Mapped[int] = mapped_column(Integer, default=0)
    failed_tasks: Mapped[int] = mapped_column(Integer, default=0)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(30), default="manual")
    schedule_pub_id: Mapped[str | None] = mapped_column(String(30))
    retry_of_run_pub_id: Mapped[str | None] = mapped_column(String(30))
    initiated_by_pub_id: Mapped[str | None] = mapped_column(String(30))


class CollectionTask(TenantModel, Base):
    __tablename__ = "collection_task"
    __table_args__ = (UniqueConstraint("run_id", "business_key"),)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.collection_run.id"))
    business_key: Mapped[str] = mapped_column(String(255))
    matrix_json: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(30), default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    answer_text: Mapped[str | None] = mapped_column(Text)
    screenshot_ref: Mapped[str | None] = mapped_column(String(500))
    quality_state: Mapped[str | None] = mapped_column(String(40))
    citations_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    # W1：平台真实检索词 JSON 数组 [{"query": str, "ordinal": int}]；无检索词存 "[]"。
    search_queries_json: Mapped[str] = mapped_column(Text, default="[]")


class InterventionRequest(TenantModel, Base):
    __tablename__ = "intervention_request"
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.platform_account.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("platform.collection_run.id"))
    challenge_type: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(30), default="pending")
    allowed_domain: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(80))
    pairing_token_hash: Mapped[str | None] = mapped_column(String(64))
    pairing_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    platform_result: Mapped[str | None] = mapped_column(String(40))
    evidence_hash: Mapped[str | None] = mapped_column(String(64))
    assigned_to_pub_id: Mapped[str | None] = mapped_column(String(30))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str] = mapped_column(Text, default="")


class DeviceBinding(TenantModel, Base):
    __tablename__ = "device_binding"
    __table_args__ = (UniqueConstraint("account_id", "public_key_sha256"),)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.platform_account.id"))
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    public_key_sha256: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(30), default="active")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TerminalTask(TenantModel, Base):
    __tablename__ = "terminal_task"
    __table_args__ = (UniqueConstraint("intervention_id"), UniqueConstraint("nonce_sha256"))
    intervention_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("platform.intervention_request.id")
    )
    device_binding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.device_binding.id"))
    nonce_sha256: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    server_signature: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(30), default="issued")
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str | None] = mapped_column(String(30))
    evidence_hash: Mapped[str | None] = mapped_column(String(64))


class SessionEvent(TenantModel, Base):
    __tablename__ = "session_event"
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.platform_account.id"))
    event_type: Mapped[str] = mapped_column(String(80))
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class SessionHealthCheck(TenantModel, Base):
    __tablename__ = "session_health_check"
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.platform_account.id"))
    probe_levels_json: Mapped[str] = mapped_column(Text)
    result: Mapped[str] = mapped_column(String(30))
    live_canary: Mapped[bool] = mapped_column(Boolean, default=False)
    checked_by: Mapped[str] = mapped_column(String(160))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class CredentialAccessRequest(TenantModel, Base):
    __tablename__ = "credential_access_request"
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.platform_account.id"))
    requested_by: Mapped[str] = mapped_column(String(160))
    reason: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(30), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    capability_token_hash: Mapped[str | None] = mapped_column(String(64))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CredentialAccessApproval(TenantModel, Base):
    __tablename__ = "credential_access_approval"
    __table_args__ = (UniqueConstraint("request_id", "approver_pub_id"),)
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("platform.credential_access_request.id")
    )
    approver_pub_id: Mapped[str] = mapped_column(String(160))
    decision: Mapped[str] = mapped_column(String(20))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class RevocationRequest(TenantModel, Base):
    __tablename__ = "revocation_request"
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.platform_account.id"))
    state: Mapped[str] = mapped_column(String(30), default="requested")
    reason: Mapped[str] = mapped_column(Text)
    workflow_id: Mapped[str] = mapped_column(String(500), unique=True)
    deletion_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(Text)
