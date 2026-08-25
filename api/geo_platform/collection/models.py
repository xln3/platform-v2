import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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


class BrowserFence(Base):
    """常驻浏览器跨 worker lease fencing（2026-08-06 起）。

    机器资源（非租户、无 RLS）：platform 单行唯一，fencing_token 单调递增
    （含抢占：过期未释放的租约被新 holder 拿走时 token 照样 +1），holder
    进程崩溃靠 expires_at 兜底回收。契约层=workflows/activities/resident_browser.py。
    """

    __tablename__ = "browser_fence"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid_pk)
    platform: Mapped[str] = mapped_column(String(80), unique=True)
    holder: Mapped[str] = mapped_column(String(160))
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
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key"),
        ForeignKeyConstraint(
            ["config_revision_v2_id", "tenant_id", "project_id"],
            [
                "platform.collection_config_revision_v2.id",
                "platform.collection_config_revision_v2.tenant_id",
                "platform.collection_config_revision_v2.project_id",
            ],
            name="fk_collection_run_config_v2_scope",
        ),
        ForeignKeyConstraint(
            ["campaign_id", "tenant_id", "project_id", "config_revision_v2_id"],
            [
                "platform.collection_campaign.id",
                "platform.collection_campaign.tenant_id",
                "platform.collection_campaign.project_id",
                "platform.collection_campaign.config_revision_id",
            ],
            name="fk_collection_run_campaign_config_scope",
        ),
    )
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
    collection_surface: Mapped[str | None] = mapped_column(String(30))
    surface_assignment_basis: Mapped[str | None] = mapped_column(String(128))
    legacy_contract_version: Mapped[str | None] = mapped_column(String(80))
    config_revision_v2_id: Mapped[uuid.UUID | None] = mapped_column()
    campaign_id: Mapped[uuid.UUID | None] = mapped_column()


class CollectionTask(TenantModel, Base):
    __tablename__ = "collection_task"
    __table_args__ = (
        UniqueConstraint("run_id", "business_key"),
        ForeignKeyConstraint(
            ["campaign_target_id", "tenant_id"],
            [
                "platform.collection_campaign_target.id",
                "platform.collection_campaign_target.tenant_id",
            ],
            name="fk_collection_task_campaign_target_tenant",
        ),
        ForeignKeyConstraint(
            ["sampling_leg_id", "tenant_id"],
            [
                "platform.collection_sampling_leg.id",
                "platform.collection_sampling_leg.tenant_id",
            ],
            name="fk_collection_task_sampling_leg_tenant",
        ),
        ForeignKeyConstraint(
            ["primary_slot_id", "tenant_id"],
            [
                "platform.collection_primary_slot.id",
                "platform.collection_primary_slot.tenant_id",
            ],
            name="fk_collection_task_primary_slot_tenant",
        ),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.collection_run.id"))
    business_key: Mapped[str] = mapped_column(String(255))
    matrix_json: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(30), default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    # Planning and completion are separate facts. ``created_at`` keeps the
    # immutable queue-plan time; ``terminal_at`` freezes when the query became
    # completed/failed so downstream snapshots can exclude late retries.
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # ``answer_text`` is the immutable platform response.  The remaining fields
    # are deterministic customer/read/search projections of that raw value.
    answer_text: Mapped[str | None] = mapped_column(Text)
    response_markdown_normalized: Mapped[str | None] = mapped_column(Text)
    response_ast_json: Mapped[str | None] = mapped_column(Text)
    response_html_sanitized: Mapped[str | None] = mapped_column(Text)
    response_plain_text: Mapped[str | None] = mapped_column(Text)
    response_hash: Mapped[str | None] = mapped_column(String(64))
    render_parser_version: Mapped[str | None] = mapped_column(String(80))
    screenshot_ref: Mapped[str | None] = mapped_column(String(500))
    quality_state: Mapped[str | None] = mapped_column(String(40))
    citations_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    # W1：平台真实检索词 JSON 数组 [{"query": str, "ordinal": int}]；无检索词存 "[]"。
    search_queries_json: Mapped[str] = mapped_column(Text, default="[]")
    collection_surface: Mapped[str | None] = mapped_column(String(30))
    surface_assignment_basis: Mapped[str | None] = mapped_column(String(128))
    legacy_contract_version: Mapped[str | None] = mapped_column(String(80))
    requested_surface: Mapped[str | None] = mapped_column(String(30))
    observed_surface: Mapped[str | None] = mapped_column(String(30))
    observed_product_variant: Mapped[str | None] = mapped_column(String(128))
    campaign_target_id: Mapped[uuid.UUID | None] = mapped_column()
    sampling_leg_id: Mapped[uuid.UUID | None] = mapped_column()
    primary_slot_id: Mapped[uuid.UUID | None] = mapped_column()


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


class CollectionConfigRevisionV2(TenantModel, Base):
    """Immutable canonical collection configuration revision.

    ``canonical_json`` and ``revision_hash`` are produced by
    :mod:`domain.collection.surface`; persistence never reimplements the hash.
    """

    __tablename__ = "collection_config_revision_v2"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "tenant_id"],
            ["platform.project.id", "platform.project.tenant_id"],
            name="fk_collection_config_revision_v2_project_scope",
        ),
        ForeignKeyConstraint(
            ["parent_revision_id", "tenant_id", "project_id"],
            [
                "platform.collection_config_revision_v2.id",
                "platform.collection_config_revision_v2.tenant_id",
                "platform.collection_config_revision_v2.project_id",
            ],
            name="fk_collection_config_v2_parent_scope",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            name="uq_collection_config_revision_v2_id_scope",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "revision",
            name="uq_collection_config_v2_revision",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "revision_hash",
            name="uq_collection_config_v2_hash",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "revision_hash",
            name="uq_collection_config_v2_id_hash_scope",
        ),
    )
    project_id: Mapped[uuid.UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    parent_revision_id: Mapped[uuid.UUID | None] = mapped_column()
    lifecycle_state: Mapped[str] = mapped_column(String(30), default="draft")
    schema_version: Mapped[str] = mapped_column(String(80))
    question_set_revision: Mapped[str] = mapped_column(String(128))
    canonical_json: Mapped[str] = mapped_column(Text)
    revision_hash: Mapped[str] = mapped_column(String(64))
    capability_registry_revision: Mapped[str] = mapped_column(String(128))
    comparison_policy_revision: Mapped[str] = mapped_column(String(128))
    samples_per_cell: Mapped[int] = mapped_column(Integer)
    province_codes_json: Mapped[str] = mapped_column(Text)
    schedule_policy_json: Mapped[str] = mapped_column(Text)
    change_reason: Mapped[str] = mapped_column(String(128))
    change_request_pub_id: Mapped[str | None] = mapped_column(String(128))
    approved_by_pub_id: Mapped[str | None] = mapped_column(String(128))
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CollectionConfigTargetV2(TenantModel, Base):
    """One explicit surface target frozen into a config revision."""

    __tablename__ = "collection_config_target_v2"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "tenant_id"],
            ["platform.project.id", "platform.project.tenant_id"],
            name="fk_collection_config_target_v2_project_scope",
        ),
        ForeignKeyConstraint(
            ["config_revision_id", "tenant_id", "project_id"],
            [
                "platform.collection_config_revision_v2.id",
                "platform.collection_config_revision_v2.tenant_id",
                "platform.collection_config_revision_v2.project_id",
            ],
            name="fk_collection_config_target_v2_config_scope",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            name="uq_collection_config_target_v2_id_scope",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "config_revision_id",
            name="uq_collection_config_target_v2_config_scope",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "config_revision_id",
            "target_key",
            name="uq_collection_config_target_v2_key",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "config_revision_id",
            "platform",
            "collection_surface",
            "product_variant",
            name="uq_collection_config_target_v2_identity",
        ),
    )
    project_id: Mapped[uuid.UUID] = mapped_column()
    config_revision_id: Mapped[uuid.UUID] = mapped_column()
    target_key: Mapped[str] = mapped_column(String(500))
    platform: Mapped[str] = mapped_column(String(128))
    collection_surface: Mapped[str] = mapped_column(String(30))
    product_variant: Mapped[str] = mapped_column(String(128))
    interaction_modes_json: Mapped[str] = mapped_column(Text)
    capability_revisions_json: Mapped[str] = mapped_column(Text)


class CollectionCampaign(TenantModel, Base):
    """A compact logical specification materialized before scheduler admission."""

    __tablename__ = "collection_campaign"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "tenant_id"],
            ["platform.project.id", "platform.project.tenant_id"],
            name="fk_collection_campaign_project_scope",
        ),
        ForeignKeyConstraint(
            ["config_revision_id", "tenant_id", "project_id", "config_revision_hash"],
            [
                "platform.collection_config_revision_v2.id",
                "platform.collection_config_revision_v2.tenant_id",
                "platform.collection_config_revision_v2.project_id",
                "platform.collection_config_revision_v2.revision_hash",
            ],
            name="fk_collection_campaign_config_hash_scope",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            name="uq_collection_campaign_id_scope",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "config_revision_id",
            name="uq_collection_campaign_config_scope",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "trigger_idempotency_key",
            name="uq_collection_campaign_trigger_idempotency",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "membership_hash",
            name="uq_collection_campaign_membership_hash",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "specification_hash",
            "slot_generator_version",
            name="uq_collection_campaign_materialization_lineage",
        ),
    )
    project_id: Mapped[uuid.UUID] = mapped_column()
    config_revision_id: Mapped[uuid.UUID] = mapped_column()
    config_revision_hash: Mapped[str] = mapped_column(String(64))
    question_set_revision: Mapped[str] = mapped_column(String(128))
    time_window_key: Mapped[str] = mapped_column(String(255))
    run_trigger_source: Mapped[str] = mapped_column(String(30))
    trigger_idempotency_key: Mapped[str] = mapped_column(String(128))
    binding_policy_revision: Mapped[str] = mapped_column(String(128))
    membership_specification_json: Mapped[str] = mapped_column(Text)
    specification_schema_version: Mapped[str] = mapped_column(String(80))
    specification_hash: Mapped[str] = mapped_column(String(64))
    slot_generator_version: Mapped[str] = mapped_column(String(80))
    membership_digest_version: Mapped[str] = mapped_column(String(80))
    expected_primary_slot_count: Mapped[int] = mapped_column(BigInteger)
    expected_non_primary_slot_count: Mapped[int] = mapped_column(BigInteger)
    expected_slot_count: Mapped[int] = mapped_column(BigInteger)
    materialized_slot_count: Mapped[int] = mapped_column(BigInteger, default=0)
    materialization_state: Mapped[str] = mapped_column(String(30), default="pending")
    materialization_cursor: Mapped[int] = mapped_column(BigInteger, default=0)
    membership_hash: Mapped[str | None] = mapped_column(String(64))
    created_by_pub_id: Mapped[str] = mapped_column(String(128))
    approved_by_pub_id: Mapped[str | None] = mapped_column(String(128))
    triggered_by_pub_id: Mapped[str] = mapped_column(String(128))
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(30), default="assembling")


class CollectionCampaignTarget(TenantModel, Base):
    """A campaign-local copy of a configured platform/product surface target."""

    __tablename__ = "collection_campaign_target"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "tenant_id"],
            ["platform.project.id", "platform.project.tenant_id"],
            name="fk_collection_campaign_target_project_scope",
        ),
        ForeignKeyConstraint(
            ["campaign_id", "tenant_id", "project_id"],
            [
                "platform.collection_campaign.id",
                "platform.collection_campaign.tenant_id",
                "platform.collection_campaign.project_id",
            ],
            name="fk_collection_campaign_target_campaign_scope",
        ),
        ForeignKeyConstraint(
            ["config_target_id", "tenant_id", "project_id"],
            [
                "platform.collection_config_target_v2.id",
                "platform.collection_config_target_v2.tenant_id",
                "platform.collection_config_target_v2.project_id",
            ],
            name="fk_collection_campaign_target_config_scope",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            name="uq_collection_campaign_target_id_scope",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "campaign_id",
            name="uq_collection_campaign_target_campaign_scope",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            name="uq_collection_campaign_target_tenant",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "campaign_id",
            "platform",
            "collection_surface",
            "product_variant",
            name="uq_collection_campaign_target_identity_scope",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "campaign_id",
            "target_key",
            name="uq_collection_campaign_target_key",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "campaign_id",
            "config_target_id",
            name="uq_collection_campaign_target_config",
        ),
    )
    project_id: Mapped[uuid.UUID] = mapped_column()
    campaign_id: Mapped[uuid.UUID] = mapped_column()
    config_target_id: Mapped[uuid.UUID] = mapped_column()
    target_key: Mapped[str] = mapped_column(String(500))
    platform: Mapped[str] = mapped_column(String(128))
    collection_surface: Mapped[str] = mapped_column(String(30))
    product_variant: Mapped[str] = mapped_column(String(128))
    interaction_modes_json: Mapped[str] = mapped_column(Text)
    capability_revisions_json: Mapped[str] = mapped_column(Text)
    binding_policy_revision: Mapped[str] = mapped_column(String(128))


class CollectionSamplingLeg(TenantModel, Base):
    """One target x province x interaction-mode comparison leg."""

    __tablename__ = "collection_sampling_leg"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "tenant_id"],
            ["platform.project.id", "platform.project.tenant_id"],
            name="fk_collection_sampling_leg_project_scope",
        ),
        ForeignKeyConstraint(
            [
                "campaign_target_id",
                "tenant_id",
                "project_id",
                "campaign_id",
                "platform",
                "collection_surface",
                "product_variant",
            ],
            [
                "platform.collection_campaign_target.id",
                "platform.collection_campaign_target.tenant_id",
                "platform.collection_campaign_target.project_id",
                "platform.collection_campaign_target.campaign_id",
                "platform.collection_campaign_target.platform",
                "platform.collection_campaign_target.collection_surface",
                "platform.collection_campaign_target.product_variant",
            ],
            name="fk_collection_sampling_leg_target_identity",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            name="uq_collection_sampling_leg_id_scope",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "campaign_id",
            "campaign_target_id",
            "platform",
            "collection_surface",
            "product_variant",
            "province_code",
            "interaction_mode",
            name="uq_collection_sampling_leg_identity_scope",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            name="uq_collection_sampling_leg_tenant",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "campaign_id",
            "leg_key",
            name="uq_collection_sampling_leg_key",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "campaign_target_id",
            "province_code",
            "interaction_mode",
            name="uq_collection_sampling_leg_cell",
        ),
    )
    project_id: Mapped[uuid.UUID] = mapped_column()
    campaign_id: Mapped[uuid.UUID] = mapped_column()
    campaign_target_id: Mapped[uuid.UUID] = mapped_column()
    leg_key: Mapped[str] = mapped_column(String(1000))
    platform: Mapped[str] = mapped_column(String(128))
    collection_surface: Mapped[str] = mapped_column(String(30))
    product_variant: Mapped[str] = mapped_column(String(128))
    province_code: Mapped[str] = mapped_column(String(6))
    interaction_mode: Mapped[str] = mapped_column(String(128))


class CollectionCampaignMaterializationBatch(TenantModel, Base):
    """One committed, retry-safe slot range for an assembling campaign."""

    __tablename__ = "collection_campaign_materialization_batch"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "tenant_id"],
            ["platform.project.id", "platform.project.tenant_id"],
            name="fk_collection_campaign_materialization_batch_project_scope",
        ),
        ForeignKeyConstraint(
            [
                "campaign_id",
                "tenant_id",
                "project_id",
                "specification_hash",
                "slot_generator_version",
            ],
            [
                "platform.collection_campaign.id",
                "platform.collection_campaign.tenant_id",
                "platform.collection_campaign.project_id",
                "platform.collection_campaign.specification_hash",
                "platform.collection_campaign.slot_generator_version",
            ],
            name="fk_collection_campaign_materialization_batch_lineage",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            name="uq_collection_campaign_materialization_batch_id_scope",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "campaign_id",
            name="uq_collection_campaign_materialization_batch_campaign_scope",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "campaign_id",
            "start_slot_ordinal",
            "end_slot_ordinal_exclusive",
            name="uq_collection_campaign_materialization_batch_range",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "campaign_id",
            "idempotency_key",
            name="uq_collection_campaign_materialization_batch_idempotency",
        ),
    )
    project_id: Mapped[uuid.UUID] = mapped_column()
    campaign_id: Mapped[uuid.UUID] = mapped_column()
    specification_hash: Mapped[str] = mapped_column(String(64))
    slot_generator_version: Mapped[str] = mapped_column(String(80))
    start_slot_ordinal: Mapped[int] = mapped_column(BigInteger)
    end_slot_ordinal_exclusive: Mapped[int] = mapped_column(BigInteger)
    slot_count: Mapped[int] = mapped_column(BigInteger)
    prior_membership_chain_hash: Mapped[str] = mapped_column(String(64))
    membership_chain_hash: Mapped[str] = mapped_column(String(64))
    chunk_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    batch_state: Mapped[str] = mapped_column(String(30), default="preparing")
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CollectionPrimarySlot(TenantModel, Base):
    """Frozen logical slot; non-primary roles remain explicit and linked."""

    __tablename__ = "collection_primary_slot"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "tenant_id"],
            ["platform.project.id", "platform.project.tenant_id"],
            name="fk_collection_primary_slot_project_scope",
        ),
        ForeignKeyConstraint(
            [
                "sampling_leg_id",
                "tenant_id",
                "project_id",
                "campaign_id",
                "campaign_target_id",
                "platform",
                "collection_surface",
                "product_variant",
                "province_code",
                "interaction_mode",
            ],
            [
                "platform.collection_sampling_leg.id",
                "platform.collection_sampling_leg.tenant_id",
                "platform.collection_sampling_leg.project_id",
                "platform.collection_sampling_leg.campaign_id",
                "platform.collection_sampling_leg.campaign_target_id",
                "platform.collection_sampling_leg.platform",
                "platform.collection_sampling_leg.collection_surface",
                "platform.collection_sampling_leg.product_variant",
                "platform.collection_sampling_leg.province_code",
                "platform.collection_sampling_leg.interaction_mode",
            ],
            name="fk_collection_primary_slot_leg_identity",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "campaign_id", "related_primary_slot_key"],
            [
                "platform.collection_primary_slot.tenant_id",
                "platform.collection_primary_slot.project_id",
                "platform.collection_primary_slot.campaign_id",
                "platform.collection_primary_slot.slot_key",
            ],
            name="fk_collection_primary_slot_related_primary",
        ),
        ForeignKeyConstraint(
            [
                "materialization_batch_id",
                "tenant_id",
                "project_id",
                "campaign_id",
            ],
            [
                "platform.collection_campaign_materialization_batch.id",
                "platform.collection_campaign_materialization_batch.tenant_id",
                "platform.collection_campaign_materialization_batch.project_id",
                "platform.collection_campaign_materialization_batch.campaign_id",
            ],
            name="fk_collection_primary_slot_materialization_batch",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            name="uq_collection_primary_slot_id_scope",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            name="uq_collection_primary_slot_tenant",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "campaign_id",
            "slot_key",
            name="uq_collection_primary_slot_key",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "campaign_id",
            "slot_ordinal",
            name="uq_collection_primary_slot_ordinal",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "campaign_id",
            "slot_identity_hash",
            name="uq_collection_primary_slot_identity_hash",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "campaign_id",
            "sampling_leg_id",
            "question_slot_id",
            "sample_ordinal",
            "slot_role",
            name="uq_collection_primary_slot_logical_identity",
        ),
    )
    project_id: Mapped[uuid.UUID] = mapped_column()
    campaign_id: Mapped[uuid.UUID] = mapped_column()
    campaign_target_id: Mapped[uuid.UUID] = mapped_column()
    sampling_leg_id: Mapped[uuid.UUID] = mapped_column()
    materialization_batch_id: Mapped[uuid.UUID] = mapped_column()
    slot_ordinal: Mapped[int] = mapped_column(BigInteger)
    slot_key: Mapped[str] = mapped_column(String(1500))
    slot_identity_hash: Mapped[str] = mapped_column(String(64))
    question_slot_id: Mapped[str] = mapped_column(String(128))
    question_revision: Mapped[str] = mapped_column(String(128))
    platform: Mapped[str] = mapped_column(String(128))
    collection_surface: Mapped[str] = mapped_column(String(30))
    product_variant: Mapped[str] = mapped_column(String(128))
    province_code: Mapped[str] = mapped_column(String(6))
    interaction_mode: Mapped[str] = mapped_column(String(128))
    sample_ordinal: Mapped[int] = mapped_column(Integer)
    slot_role: Mapped[str] = mapped_column(String(30))
    role_reason: Mapped[str | None] = mapped_column(String(128))
    related_primary_slot_key: Mapped[str | None] = mapped_column(String(1500))
