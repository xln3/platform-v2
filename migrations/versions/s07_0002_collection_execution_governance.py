"""Add fail-closed collection execution governance and durable send truth.

This is an additive revision.  Existing S01 resource rows remain legacy until
their nullable v2 discriminator is populated by an explicit, audited adoption
flow.  No resource, lease, account, quota counter, or submission is inferred or
backfilled by this migration.

Revision ID: s07_0002_execution_governance
Revises: s07_0001_surface_identity
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "s07_0002_execution_governance"
down_revision: str | Sequence[str] | None = "s07_0001_surface_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SURFACE = "{column} IN ('provider_api','consumer_web','consumer_app')"
_SHA256 = "{column} ~ '^[0-9a-f]{{64}}$'"
_RESOURCE_KINDS = (
    "provider_tenant",
    "credential_slot",
    "governed_account",
    "browser_owner",
    "browser_profile",
    "web_session",
    "device_owner",
    "app_install",
    "app_session",
    "relay_capacity",
)
_NEW_TABLES = (
    "collection_capability_registry_revision",
    "collection_capability_declaration",
    "collection_quota_registry_revision",
    "collection_quota_scope_policy",
    "collection_binding_revision_v2",
    "collection_api_binding_v2",
    "collection_web_binding_v2",
    "collection_app_binding_v2",
    "collection_binding_capability",
    "collection_binding_resource",
    "collection_binding_quota_scope",
    "collection_submission_operation",
    "collection_submission_reconciliation_proof",
    "collection_resource_adoption",
    "collection_resource_capacity_unit",
    "collection_quota_bucket",
    "collection_quota_reservation",
    "collection_quota_reservation_effect",
    "collection_quota_ledger_event",
    "collection_execution_grant_v2",
    "collection_api_execution_grant_v2",
    "collection_web_execution_grant_v2",
    "collection_app_execution_grant_v2",
    "collection_execution_grant_resource",
)


def _identity_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pub_id", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ]


def _scope_constraints(table: str) -> list[sa.Constraint]:
    return [
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["platform.tenant.id"],
            name=f"fk_{table}_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "tenant_id"],
            ["platform.project.id", "platform.project.tenant_id"],
            name=f"fk_{table}_project_scope",
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{table}"),
        sa.UniqueConstraint("pub_id", name=f"uq_{table}_pub_id"),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            name=f"uq_{table}_id_scope",
        ),
        sa.CheckConstraint("version > 0", name=f"ck_{table}_version"),
    ]


def _enable_rls(table: str) -> None:
    op.execute(f'ALTER TABLE platform."{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE platform."{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON platform."{table}"
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def _extend_resource_registration() -> None:
    columns: tuple[sa.Column[Any], ...] = (
        sa.Column("project_id", sa.Uuid()),
        sa.Column("resource_schema_version", sa.String(length=80)),
        sa.Column("resource_revision", sa.String(length=128)),
        sa.Column("owner_gateway_kind", sa.String(length=80)),
        sa.Column("owner_gateway_revision", sa.String(length=128)),
        sa.Column("opaque_owner_handle", sa.String(length=255)),
        sa.Column("attestation_revision", sa.String(length=128)),
        sa.Column("route_policy_revision", sa.String(length=128)),
        sa.Column("resource_fingerprint", sa.String(length=64)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    for column in columns:
        op.add_column("resource_registration", column, schema="platform")

    op.create_foreign_key(
        "fk_resource_registration_project_s07",
        "resource_registration",
        "project",
        ["project_id", "tenant_id"],
        ["id", "tenant_id"],
        source_schema="platform",
        referent_schema="platform",
    )
    op.create_unique_constraint(
        "uq_resource_registration_scope_s07",
        "resource_registration",
        ["id", "tenant_id", "project_id"],
        schema="platform",
    )
    op.create_unique_constraint(
        "uq_resource_registration_identity_s07",
        "resource_registration",
        ["id", "tenant_id", "project_id", "resource_kind", "pub_id"],
        schema="platform",
    )
    op.create_check_constraint(
        "ck_resource_registration_v2_shape_s07",
        "resource_registration",
        """
        (resource_schema_version IS NULL AND project_id IS NULL
          AND resource_revision IS NULL AND owner_gateway_kind IS NULL
          AND owner_gateway_revision IS NULL AND opaque_owner_handle IS NULL
          AND attestation_revision IS NULL AND route_policy_revision IS NULL
          AND resource_fingerprint IS NULL AND approved_at IS NULL
          AND revoked_at IS NULL)
        OR
        (resource_schema_version = 'collection-resource-v2'
          AND project_id IS NOT NULL AND resource_revision IS NOT NULL
          AND owner_gateway_kind IS NOT NULL
          AND owner_gateway_revision IS NOT NULL
          AND opaque_owner_handle IS NOT NULL
          AND attestation_revision IS NOT NULL
          AND route_policy_revision IS NOT NULL
          AND resource_fingerprint IS NOT NULL
          AND btrim(resource_revision) <> ''
          AND owner_gateway_kind IN
            ('provider_request','resident_browser','managed_app_session')
          AND btrim(owner_gateway_revision) <> ''
          AND opaque_owner_handle ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
          AND btrim(attestation_revision) <> ''
          AND btrim(route_policy_revision) <> ''
          AND resource_fingerprint ~ '^[0-9a-f]{64}$'
          AND approved_at IS NOT NULL
          AND resource_kind IN
            ('provider_tenant','credential_slot','governed_account',
             'browser_owner','browser_profile','web_session','device_owner',
             'app_install','app_session','relay_capacity')
          AND state IN ('candidate','active','quarantined','revoked')
          AND (state <> 'revoked' OR revoked_at IS NOT NULL))
        """,
        schema="platform",
    )
    op.create_index(
        "uq_resource_registration_revision_s07",
        "resource_registration",
        ["tenant_id", "project_id", "resource_kind", "resource_revision"],
        unique=True,
        schema="platform",
        postgresql_where=sa.text("resource_schema_version = 'collection-resource-v2'"),
    )
    op.create_index(
        "uq_resource_registration_fingerprint_s07",
        "resource_registration",
        ["tenant_id", "project_id", "resource_fingerprint"],
        unique=True,
        schema="platform",
        postgresql_where=sa.text("resource_schema_version = 'collection-resource-v2'"),
    )
    op.execute(
        """
        CREATE FUNCTION platform.guard_resource_registration_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.resource_schema_version = 'collection-resource-v2' THEN
              RAISE EXCEPTION 'formal resource registration cannot be deleted';
            END IF;
            RETURN OLD;
          END IF;
          IF OLD.resource_schema_version = 'collection-resource-v2' THEN
            IF ROW(NEW.pub_id, NEW.tenant_id, NEW.project_id, NEW.resource_kind,
                   NEW.resource_schema_version, NEW.resource_revision,
                   NEW.owner_gateway_kind, NEW.owner_gateway_revision,
                   NEW.opaque_owner_handle, NEW.attestation_revision,
                   NEW.route_policy_revision, NEW.resource_fingerprint,
                   NEW.approved_at)
               IS DISTINCT FROM
               ROW(OLD.pub_id, OLD.tenant_id, OLD.project_id, OLD.resource_kind,
                   OLD.resource_schema_version, OLD.resource_revision,
                   OLD.owner_gateway_kind, OLD.owner_gateway_revision,
                   OLD.opaque_owner_handle, OLD.attestation_revision,
                   OLD.route_policy_revision, OLD.resource_fingerprint,
                   OLD.approved_at) THEN
              RAISE EXCEPTION 'formal resource registration identity is immutable';
            END IF;
            IF OLD.state IN ('revoked','quarantined') AND NEW.state <> OLD.state THEN
              RAISE EXCEPTION 'terminal formal resource state is immutable';
            END IF;
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER resource_registration_v2_guard_trg
        BEFORE UPDATE OR DELETE ON platform.resource_registration
        FOR EACH ROW EXECUTE FUNCTION platform.guard_resource_registration_v2()
        """
    )


def _create_capability_tables() -> None:
    op.create_table(
        "collection_capability_registry_revision",
        *_identity_columns(),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("registry_revision", sa.String(length=128), nullable=False),
        sa.Column("parent_revision_id", sa.Uuid()),
        sa.Column("lifecycle_state", sa.String(length=30), nullable=False),
        sa.Column("canonical_json", sa.Text(), nullable=False),
        sa.Column("revision_hash", sa.String(length=64), nullable=False),
        sa.Column("change_reason", sa.String(length=128), nullable=False),
        sa.Column("approved_by_pub_id", sa.String(length=255)),
        sa.Column("frozen_at", sa.DateTime(timezone=True)),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        *_scope_constraints("collection_capability_registry_revision"),
        sa.ForeignKeyConstraint(
            ["parent_revision_id", "tenant_id", "project_id"],
            [
                "platform.collection_capability_registry_revision.id",
                "platform.collection_capability_registry_revision.tenant_id",
                "platform.collection_capability_registry_revision.project_id",
            ],
            name="fk_cap_registry_parent_scope",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "registry_revision",
            name="uq_cap_registry_revision",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "registry_revision",
            name="uq_cap_registry_exact_revision",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "revision_hash",
            name="uq_cap_registry_hash",
        ),
        sa.CheckConstraint(
            "schema_version = 'collection-capability-registry-v1'",
            name="ck_cap_registry_schema",
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('draft','candidate','frozen','active','superseded','retired')",
            name="ck_cap_registry_lifecycle",
        ),
        sa.CheckConstraint(_SHA256.format(column="revision_hash"), name="ck_cap_registry_hash"),
        sa.CheckConstraint(
            "(lifecycle_state IN ('draft','candidate') AND frozen_at IS NULL "
            "AND activated_at IS NULL AND retired_at IS NULL) OR "
            "(lifecycle_state = 'frozen' AND frozen_at IS NOT NULL "
            "AND activated_at IS NULL AND retired_at IS NULL) OR "
            "(lifecycle_state IN ('active','superseded') AND frozen_at IS NOT NULL "
            "AND activated_at IS NOT NULL AND retired_at IS NULL) OR "
            "(lifecycle_state = 'retired' AND frozen_at IS NOT NULL "
            "AND activated_at IS NOT NULL AND retired_at IS NOT NULL)",
            name="ck_cap_registry_timestamps",
        ),
        schema="platform",
    )
    op.create_table(
        "collection_capability_declaration",
        *_identity_columns(),
        sa.Column("registry_revision_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("declaration_key", sa.String(length=1000), nullable=False),
        sa.Column("capability_revision", sa.String(length=128), nullable=False),
        sa.Column("platform", sa.String(length=128), nullable=False),
        sa.Column("collection_surface", sa.String(length=30), nullable=False),
        sa.Column("product_variant", sa.String(length=128), nullable=False),
        sa.Column("interaction_mode", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("production_allowed", sa.Boolean(), nullable=False),
        sa.Column("region_policy_revision", sa.String(length=128)),
        sa.Column("required_resource_kinds_json", sa.Text(), nullable=False),
        sa.Column("observable_capture_fields_json", sa.Text(), nullable=False),
        sa.Column("product_version_constraints_json", sa.Text(), nullable=False),
        sa.Column("unsupported_reason", sa.String(length=500)),
        sa.Column("alternative_suggestion", sa.String(length=500)),
        *_scope_constraints("collection_capability_declaration"),
        sa.ForeignKeyConstraint(
            ["registry_revision_id", "tenant_id", "project_id"],
            [
                "platform.collection_capability_registry_revision.id",
                "platform.collection_capability_registry_revision.tenant_id",
                "platform.collection_capability_registry_revision.project_id",
            ],
            name="fk_cap_declaration_registry_scope",
        ),
        sa.UniqueConstraint(
            "registry_revision_id",
            "tenant_id",
            "project_id",
            "declaration_key",
            name="uq_cap_declaration_key",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "capability_revision",
            "platform",
            "collection_surface",
            "product_variant",
            "interaction_mode",
            name="uq_cap_declaration_binding_identity",
        ),
        sa.UniqueConstraint(
            "registry_revision_id",
            "tenant_id",
            "project_id",
            "platform",
            "collection_surface",
            "product_variant",
            "interaction_mode",
            name="uq_cap_declaration_dimensions",
        ),
        sa.CheckConstraint(
            "schema_version = 'collection-capability-v1'",
            name="ck_cap_declaration_schema",
        ),
        sa.CheckConstraint(
            _SURFACE.format(column="collection_surface"),
            name="ck_cap_declaration_surface",
        ),
        sa.CheckConstraint(
            "status IN ('supported','pilot','unsupported')",
            name="ck_cap_declaration_status",
        ),
        sa.CheckConstraint(
            "status <> 'unsupported' OR "
            "(production_allowed = false AND btrim(unsupported_reason) <> '')",
            name="ck_cap_declaration_unsupported",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(required_resource_kinds_json::jsonb) = 'array' "
            "AND jsonb_array_length(required_resource_kinds_json::jsonb) > 0",
            name="ck_cap_declaration_resource_kinds",
        ),
        sa.CheckConstraint(
            "btrim(declaration_key) <> '' AND btrim(capability_revision) <> '' "
            "AND btrim(platform) <> '' AND btrim(product_variant) <> '' "
            "AND btrim(interaction_mode) <> ''",
            name="ck_cap_declaration_required_text",
        ),
        schema="platform",
    )

    op.execute(
        """
        CREATE FUNCTION platform.guard_capability_registry_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NEW.lifecycle_state NOT IN ('draft','candidate') THEN
              RAISE EXCEPTION 'capability registry must begin mutable';
            END IF;
            RETURN NEW;
          END IF;
          IF TG_OP = 'DELETE' THEN
            IF OLD.lifecycle_state NOT IN ('draft','candidate') THEN
              RAISE EXCEPTION 'frozen capability registry cannot be deleted';
            END IF;
            RETURN OLD;
          END IF;
          IF OLD.lifecycle_state IN ('frozen','active','superseded','retired') AND
             ROW(NEW.pub_id, NEW.tenant_id, NEW.project_id,
                 NEW.schema_version, NEW.registry_revision,
                 NEW.parent_revision_id, NEW.canonical_json, NEW.revision_hash,
                 NEW.change_reason, NEW.approved_by_pub_id, NEW.frozen_at)
             IS DISTINCT FROM
             ROW(OLD.pub_id, OLD.tenant_id, OLD.project_id,
                 OLD.schema_version, OLD.registry_revision,
                 OLD.parent_revision_id, OLD.canonical_json, OLD.revision_hash,
                 OLD.change_reason, OLD.approved_by_pub_id, OLD.frozen_at) THEN
            RAISE EXCEPTION 'frozen capability registry content is immutable';
          END IF;
          IF OLD.lifecycle_state IN ('active','superseded','retired') AND
             NEW.activated_at IS DISTINCT FROM OLD.activated_at THEN
            RAISE EXCEPTION 'capability registry activation timestamp is immutable';
          END IF;
          IF OLD.lifecycle_state = 'retired' AND
             NEW.retired_at IS DISTINCT FROM OLD.retired_at THEN
            RAISE EXCEPTION 'capability registry retirement timestamp is immutable';
          END IF;
          IF OLD.lifecycle_state='candidate' AND NEW.lifecycle_state='frozen' AND
             (NOT EXISTS (
                SELECT 1 FROM platform.collection_capability_declaration d
                 WHERE d.registry_revision_id=NEW.id
                   AND d.tenant_id=NEW.tenant_id AND d.project_id=NEW.project_id
                   AND d.status='supported' AND d.production_allowed=true
              ) OR EXISTS (
                SELECT 1 FROM platform.collection_capability_declaration d
                 WHERE d.registry_revision_id=NEW.id
                   AND d.tenant_id=NEW.tenant_id AND d.project_id=NEW.project_id
                   AND d.status='supported' AND d.production_allowed=true
                   AND (EXISTS (
                     SELECT 1 FROM jsonb_array_elements_text(
                       d.required_resource_kinds_json::jsonb
                     ) AS required(kind)
                      WHERE required.kind NOT IN
                        ('provider_tenant','credential_slot','governed_account',
                         'browser_owner','browser_profile','web_session',
                         'device_owner','app_install','app_session','relay_capacity')
                   ) OR
                   (d.collection_surface='provider_api' AND NOT (
                     d.required_resource_kinds_json::jsonb ? 'provider_tenant' AND
                     d.required_resource_kinds_json::jsonb ? 'credential_slot'
                   )) OR
                   (d.collection_surface='consumer_web' AND NOT (
                     d.required_resource_kinds_json::jsonb ? 'governed_account' AND
                     d.required_resource_kinds_json::jsonb ? 'browser_owner' AND
                     d.required_resource_kinds_json::jsonb ? 'browser_profile' AND
                     d.required_resource_kinds_json::jsonb ? 'web_session'
                   )) OR
                   (d.collection_surface='consumer_app' AND NOT (
                     d.required_resource_kinds_json::jsonb ? 'governed_account' AND
                     d.required_resource_kinds_json::jsonb ? 'device_owner' AND
                     d.required_resource_kinds_json::jsonb ? 'app_install' AND
                     d.required_resource_kinds_json::jsonb ? 'app_session'
                   )))
              )) THEN
            RAISE EXCEPTION
              'capability registry resource policy is incomplete or invalid';
          END IF;
          IF (OLD.lifecycle_state = 'draft' AND
              NEW.lifecycle_state NOT IN ('draft','candidate')) OR
             (OLD.lifecycle_state = 'candidate' AND
              NEW.lifecycle_state NOT IN ('candidate','frozen')) OR
             (OLD.lifecycle_state = 'frozen' AND
              NEW.lifecycle_state NOT IN ('frozen','active')) OR
             (OLD.lifecycle_state = 'active' AND
              NEW.lifecycle_state NOT IN ('active','superseded','retired')) OR
             (OLD.lifecycle_state = 'superseded' AND
              NEW.lifecycle_state NOT IN ('superseded','retired')) OR
             (OLD.lifecycle_state = 'retired' AND NEW.lifecycle_state <> 'retired') THEN
            RAISE EXCEPTION 'invalid capability registry lifecycle transition';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.guard_capability_declaration_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE parent_state text;
        BEGIN
          SELECT lifecycle_state INTO parent_state
            FROM platform.collection_capability_registry_revision
           WHERE id=COALESCE(NEW.registry_revision_id, OLD.registry_revision_id)
             AND tenant_id=COALESCE(NEW.tenant_id, OLD.tenant_id)
             AND project_id=COALESCE(NEW.project_id, OLD.project_id);
          IF parent_state NOT IN ('draft','candidate') THEN
            RAISE EXCEPTION 'capability declarations are frozen with their registry';
          END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER capability_registry_v2_guard_trg
        BEFORE INSERT OR UPDATE OR DELETE
        ON platform.collection_capability_registry_revision
        FOR EACH ROW EXECUTE FUNCTION platform.guard_capability_registry_v2()
        """
    )
    op.execute(
        """
        CREATE TRIGGER capability_declaration_v2_guard_trg
        BEFORE INSERT OR UPDATE OR DELETE
        ON platform.collection_capability_declaration
        FOR EACH ROW EXECUTE FUNCTION platform.guard_capability_declaration_v2()
        """
    )


def _create_quota_policy_tables() -> None:
    op.create_table(
        "collection_quota_registry_revision",
        *_identity_columns(),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("registry_revision", sa.String(length=128), nullable=False),
        sa.Column("parent_revision_id", sa.Uuid()),
        sa.Column("lock_order_version", sa.String(length=128), nullable=False),
        sa.Column("lifecycle_state", sa.String(length=30), nullable=False),
        sa.Column("canonical_json", sa.Text(), nullable=False),
        sa.Column("revision_hash", sa.String(length=64), nullable=False),
        sa.Column("change_reason", sa.String(length=128), nullable=False),
        sa.Column("approved_by_pub_id", sa.String(length=255)),
        sa.Column("frozen_at", sa.DateTime(timezone=True)),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        *_scope_constraints("collection_quota_registry_revision"),
        sa.ForeignKeyConstraint(
            ["parent_revision_id", "tenant_id", "project_id"],
            [
                "platform.collection_quota_registry_revision.id",
                "platform.collection_quota_registry_revision.tenant_id",
                "platform.collection_quota_registry_revision.project_id",
            ],
            name="fk_quota_registry_parent_scope",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "registry_revision",
            name="uq_quota_registry_revision",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "registry_revision",
            name="uq_quota_registry_exact_revision",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "revision_hash",
            name="uq_quota_registry_hash",
        ),
        sa.CheckConstraint(
            "schema_version = 'quota-scope-registry-v1'",
            name="ck_quota_registry_schema",
        ),
        sa.CheckConstraint(
            "lock_order_version = 'quota-scope-lock-order-v1'",
            name="ck_quota_registry_lock_order",
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('draft','candidate','frozen','active','superseded','retired')",
            name="ck_quota_registry_lifecycle",
        ),
        sa.CheckConstraint(
            _SHA256.format(column="revision_hash"),
            name="ck_quota_registry_hash",
        ),
        sa.CheckConstraint(
            "(lifecycle_state IN ('draft','candidate') AND frozen_at IS NULL "
            "AND activated_at IS NULL AND retired_at IS NULL) OR "
            "(lifecycle_state = 'frozen' AND frozen_at IS NOT NULL "
            "AND activated_at IS NULL AND retired_at IS NULL) OR "
            "(lifecycle_state IN ('active','superseded') AND frozen_at IS NOT NULL "
            "AND activated_at IS NOT NULL AND retired_at IS NULL) OR "
            "(lifecycle_state = 'retired' AND frozen_at IS NOT NULL "
            "AND activated_at IS NOT NULL AND retired_at IS NOT NULL)",
            name="ck_quota_registry_timestamps",
        ),
        schema="platform",
    )
    op.create_table(
        "collection_quota_scope_policy",
        *_identity_columns(),
        sa.Column("registry_revision_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("scope_policy_key", sa.String(length=1500), nullable=False),
        sa.Column("selector_key", sa.String(length=1500), nullable=False),
        sa.Column("policy_revision", sa.String(length=128), nullable=False),
        sa.Column("scope_kind", sa.String(length=40), nullable=False),
        sa.Column("scope_subject_id", sa.String(length=255), nullable=False),
        sa.Column("platform", sa.String(length=128)),
        sa.Column("collection_surface", sa.String(length=30)),
        sa.Column("product_variant", sa.String(length=128)),
        sa.Column("interaction_mode", sa.String(length=128)),
        sa.Column("share_policy", sa.String(length=40), nullable=False),
        sa.Column("window_schema_version", sa.String(length=80), nullable=False),
        sa.Column("window_unit", sa.String(length=40), nullable=False),
        sa.Column("window_size", sa.Integer(), nullable=False),
        sa.Column("window_timezone", sa.String(length=128), nullable=False),
        sa.Column("window_boundary_revision", sa.String(length=128), nullable=False),
        sa.Column("provider_window_code", sa.String(length=128)),
        sa.Column("limit_units", sa.Integer(), nullable=False),
        sa.Column("limit_source", sa.String(length=40), nullable=False),
        sa.Column("settlement_policy_revision", sa.String(length=128), nullable=False),
        sa.Column("lock_order_ordinal", sa.Integer(), nullable=False),
        *_scope_constraints("collection_quota_scope_policy"),
        sa.ForeignKeyConstraint(
            ["registry_revision_id", "tenant_id", "project_id"],
            [
                "platform.collection_quota_registry_revision.id",
                "platform.collection_quota_registry_revision.tenant_id",
                "platform.collection_quota_registry_revision.project_id",
            ],
            name="fk_quota_scope_registry_scope",
        ),
        sa.UniqueConstraint(
            "registry_revision_id",
            "tenant_id",
            "project_id",
            "scope_policy_key",
            name="uq_quota_scope_policy_key",
        ),
        sa.UniqueConstraint(
            "registry_revision_id",
            "tenant_id",
            "project_id",
            "selector_key",
            name="uq_quota_scope_selector",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "registry_revision_id",
            "scope_policy_key",
            "scope_kind",
            "scope_subject_id",
            "policy_revision",
            name="uq_quota_scope_runtime_identity",
        ),
        sa.CheckConstraint(
            "schema_version = 'quota-scope-v1'",
            name="ck_quota_scope_schema",
        ),
        sa.CheckConstraint(
            "scope_kind IN "
            "('provider','account','credential','project','contract',"
            "'platform_surface','mode')",
            name="ck_quota_scope_kind",
        ),
        sa.CheckConstraint(
            "collection_surface IS NULL OR " + _SURFACE.format(column="collection_surface"),
            name="ck_quota_scope_surface",
        ),
        sa.CheckConstraint(
            "collection_surface IS NULL OR platform IS NOT NULL",
            name="ck_quota_scope_surface_platform",
        ),
        sa.CheckConstraint(
            "product_variant IS NULL OR platform IS NOT NULL",
            name="ck_quota_scope_product_platform",
        ),
        sa.CheckConstraint(
            "scope_kind <> 'platform_surface' OR "
            "(platform IS NOT NULL AND collection_surface IS NOT NULL)",
            name="ck_quota_scope_platform_surface",
        ),
        sa.CheckConstraint(
            "scope_kind <> 'mode' OR interaction_mode IS NOT NULL",
            name="ck_quota_scope_mode",
        ),
        sa.CheckConstraint(
            "window_schema_version = 'quota-window-v1'",
            name="ck_quota_scope_window_schema",
        ),
        sa.CheckConstraint(
            "window_unit IN ('day','week','year','provider_custom')",
            name="ck_quota_scope_window_unit",
        ),
        sa.CheckConstraint(
            "window_size > 0 AND limit_units > 0 AND lock_order_ordinal >= 0",
            name="ck_quota_scope_positive_values",
        ),
        sa.CheckConstraint(
            "lock_order_ordinal = CASE scope_kind "
            "WHEN 'provider' THEN 0 WHEN 'account' THEN 1 "
            "WHEN 'credential' THEN 2 WHEN 'project' THEN 3 "
            "WHEN 'contract' THEN 4 WHEN 'platform_surface' THEN 5 "
            "WHEN 'mode' THEN 6 END",
            name="ck_quota_scope_canonical_lock_order",
        ),
        sa.CheckConstraint(
            "(window_unit = 'provider_custom' AND "
            "btrim(provider_window_code) <> '') OR "
            "(window_unit <> 'provider_custom' AND provider_window_code IS NULL)",
            name="ck_quota_scope_provider_window",
        ),
        sa.CheckConstraint(
            "share_policy IN ('shared','dedicated')",
            name="ck_quota_scope_share_policy",
        ),
        sa.CheckConstraint(
            "limit_source IN ('contract','provider','project_policy','manual_approval')",
            name="ck_quota_scope_limit_source",
        ),
        sa.CheckConstraint(
            "btrim(scope_policy_key) <> '' AND btrim(selector_key) <> '' "
            "AND btrim(policy_revision) <> '' AND btrim(scope_subject_id) <> '' "
            "AND btrim(window_timezone) <> '' "
            "AND btrim(window_boundary_revision) <> '' "
            "AND btrim(settlement_policy_revision) <> ''",
            name="ck_quota_scope_required_text",
        ),
        schema="platform",
    )

    op.execute(
        """
        CREATE FUNCTION platform.guard_quota_registry_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NEW.lifecycle_state NOT IN ('draft','candidate') THEN
              RAISE EXCEPTION 'quota registry must begin mutable';
            END IF;
            RETURN NEW;
          END IF;
          IF TG_OP = 'DELETE' THEN
            IF OLD.lifecycle_state NOT IN ('draft','candidate') THEN
              RAISE EXCEPTION 'frozen quota registry cannot be deleted';
            END IF;
            RETURN OLD;
          END IF;
          IF OLD.lifecycle_state IN ('frozen','active','superseded','retired') AND
             ROW(NEW.pub_id, NEW.tenant_id, NEW.project_id,
                 NEW.schema_version, NEW.registry_revision,
                 NEW.parent_revision_id, NEW.lock_order_version,
                 NEW.canonical_json, NEW.revision_hash, NEW.change_reason,
                 NEW.approved_by_pub_id, NEW.frozen_at)
             IS DISTINCT FROM
             ROW(OLD.pub_id, OLD.tenant_id, OLD.project_id,
                 OLD.schema_version, OLD.registry_revision,
                 OLD.parent_revision_id, OLD.lock_order_version,
                 OLD.canonical_json, OLD.revision_hash, OLD.change_reason,
                 OLD.approved_by_pub_id, OLD.frozen_at) THEN
            RAISE EXCEPTION 'frozen quota registry content is immutable';
          END IF;
          IF OLD.lifecycle_state IN ('active','superseded','retired') AND
             NEW.activated_at IS DISTINCT FROM OLD.activated_at THEN
            RAISE EXCEPTION 'quota registry activation timestamp is immutable';
          END IF;
          IF OLD.lifecycle_state = 'retired' AND
             NEW.retired_at IS DISTINCT FROM OLD.retired_at THEN
            RAISE EXCEPTION 'quota registry retirement timestamp is immutable';
          END IF;
          IF (OLD.lifecycle_state = 'draft' AND
              NEW.lifecycle_state NOT IN ('draft','candidate')) OR
             (OLD.lifecycle_state = 'candidate' AND
              NEW.lifecycle_state NOT IN ('candidate','frozen')) OR
             (OLD.lifecycle_state = 'frozen' AND
              NEW.lifecycle_state NOT IN ('frozen','active')) OR
             (OLD.lifecycle_state = 'active' AND
              NEW.lifecycle_state NOT IN ('active','superseded','retired')) OR
             (OLD.lifecycle_state = 'superseded' AND
              NEW.lifecycle_state NOT IN ('superseded','retired')) OR
             (OLD.lifecycle_state = 'retired' AND NEW.lifecycle_state <> 'retired') THEN
            RAISE EXCEPTION 'invalid quota registry lifecycle transition';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.guard_quota_scope_policy_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE parent_state text;
        BEGIN
          SELECT lifecycle_state INTO parent_state
            FROM platform.collection_quota_registry_revision
           WHERE id=COALESCE(NEW.registry_revision_id, OLD.registry_revision_id)
             AND tenant_id=COALESCE(NEW.tenant_id, OLD.tenant_id)
             AND project_id=COALESCE(NEW.project_id, OLD.project_id);
          IF parent_state NOT IN ('draft','candidate') THEN
            RAISE EXCEPTION 'quota scope policies are frozen with their registry';
          END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER quota_registry_v2_guard_trg
        BEFORE INSERT OR UPDATE OR DELETE
        ON platform.collection_quota_registry_revision
        FOR EACH ROW EXECUTE FUNCTION platform.guard_quota_registry_v2()
        """
    )
    op.execute(
        """
        CREATE TRIGGER quota_scope_policy_v2_guard_trg
        BEFORE INSERT OR UPDATE OR DELETE ON platform.collection_quota_scope_policy
        FOR EACH ROW EXECUTE FUNCTION platform.guard_quota_scope_policy_v2()
        """
    )


def _create_binding_tables() -> None:
    op.create_table(
        "collection_binding_revision_v2",
        *_identity_columns(),
        sa.Column("parent_binding_revision_id", sa.Uuid()),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("binding_key", sa.String(length=1000), nullable=False),
        sa.Column("binding_revision", sa.Integer(), nullable=False),
        sa.Column("binding_policy_revision", sa.String(length=128), nullable=False),
        sa.Column("lifecycle_state", sa.String(length=30), nullable=False),
        sa.Column("lifecycle_reason", sa.String(length=128), nullable=False),
        sa.Column("platform", sa.String(length=128), nullable=False),
        sa.Column("collection_surface", sa.String(length=30), nullable=False),
        sa.Column("product_variant", sa.String(length=128), nullable=False),
        sa.Column("capability_registry_id", sa.Uuid(), nullable=False),
        sa.Column("capability_registry_revision", sa.String(length=128), nullable=False),
        sa.Column("quota_registry_id", sa.Uuid(), nullable=False),
        sa.Column("quota_registry_revision", sa.String(length=128), nullable=False),
        sa.Column("quota_policy_revision", sa.String(length=128), nullable=False),
        sa.Column("region_policy_revision", sa.String(length=128), nullable=False),
        sa.Column("route_policy_revision", sa.String(length=128), nullable=False),
        sa.Column("resource_policy_revision", sa.String(length=128), nullable=False),
        sa.Column("readiness_revision", sa.String(length=128), nullable=False),
        sa.Column("required_resource_kinds_json", sa.Text(), nullable=False),
        sa.Column("credential_references_json", sa.Text(), nullable=False),
        sa.Column("canonical_json", sa.Text(), nullable=False),
        sa.Column("binding_hash", sa.String(length=64), nullable=False),
        sa.Column("owner_pub_id", sa.String(length=255), nullable=False),
        sa.Column("approved_by_pub_id", sa.String(length=255)),
        sa.Column("approval_pub_id", sa.String(length=128)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("suspended_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        *_scope_constraints("collection_binding_revision_v2"),
        sa.ForeignKeyConstraint(
            ["parent_binding_revision_id", "tenant_id", "project_id"],
            [
                "platform.collection_binding_revision_v2.id",
                "platform.collection_binding_revision_v2.tenant_id",
                "platform.collection_binding_revision_v2.project_id",
            ],
            name="fk_binding_v2_parent_scope",
        ),
        sa.ForeignKeyConstraint(
            [
                "capability_registry_id",
                "tenant_id",
                "project_id",
                "capability_registry_revision",
            ],
            [
                "platform.collection_capability_registry_revision.id",
                "platform.collection_capability_registry_revision.tenant_id",
                "platform.collection_capability_registry_revision.project_id",
                "platform.collection_capability_registry_revision.registry_revision",
            ],
            name="fk_binding_v2_cap_registry_exact",
        ),
        sa.ForeignKeyConstraint(
            [
                "quota_registry_id",
                "tenant_id",
                "project_id",
                "quota_registry_revision",
            ],
            [
                "platform.collection_quota_registry_revision.id",
                "platform.collection_quota_registry_revision.tenant_id",
                "platform.collection_quota_registry_revision.project_id",
                "platform.collection_quota_registry_revision.registry_revision",
            ],
            name="fk_binding_v2_quota_registry_exact",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "binding_key",
            "binding_revision",
            name="uq_binding_v2_key_revision",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "binding_hash",
            name="uq_binding_v2_hash",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "platform",
            "collection_surface",
            "product_variant",
            name="uq_binding_v2_dimensions",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "collection_surface",
            name="uq_binding_v2_subtype_identity",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "quota_registry_id",
            name="uq_binding_v2_quota_registry",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "binding_revision",
            "platform",
            "collection_surface",
            "product_variant",
            name="uq_binding_v2_exact_identity",
        ),
        sa.CheckConstraint(
            "schema_version = 'collection-binding-v1'",
            name="ck_binding_v2_schema",
        ),
        sa.CheckConstraint(
            _SURFACE.format(column="collection_surface"),
            name="ck_binding_v2_surface",
        ),
        sa.CheckConstraint(
            "binding_revision > 0 AND effective_from < expires_at",
            name="ck_binding_v2_revision_window",
        ),
        sa.CheckConstraint(_SHA256.format(column="binding_hash"), name="ck_binding_v2_hash"),
        sa.CheckConstraint(
            "lifecycle_state IN ('draft','candidate','active','suspended','revoked','superseded')",
            name="ck_binding_v2_lifecycle",
        ),
        sa.CheckConstraint(
            "(lifecycle_state IN ('draft','candidate') AND activated_at IS NULL "
            "AND suspended_at IS NULL AND revoked_at IS NULL "
            "AND superseded_at IS NULL) OR "
            "(lifecycle_state = 'active' AND activated_at IS NOT NULL "
            "AND suspended_at IS NULL AND revoked_at IS NULL "
            "AND superseded_at IS NULL) OR "
            "(lifecycle_state = 'suspended' AND activated_at IS NOT NULL "
            "AND suspended_at IS NOT NULL AND revoked_at IS NULL "
            "AND superseded_at IS NULL) OR "
            "(lifecycle_state = 'revoked' AND activated_at IS NOT NULL "
            "AND revoked_at IS NOT NULL AND superseded_at IS NULL) OR "
            "(lifecycle_state = 'superseded' AND activated_at IS NOT NULL "
            "AND superseded_at IS NOT NULL AND revoked_at IS NULL)",
            name="ck_binding_v2_lifecycle_timestamps",
        ),
        sa.CheckConstraint(
            "btrim(binding_key) <> '' AND btrim(binding_policy_revision) <> '' "
            "AND btrim(lifecycle_reason) <> '' AND btrim(platform) <> '' "
            "AND btrim(product_variant) <> '' "
            "AND btrim(capability_registry_revision) <> '' "
            "AND btrim(quota_registry_revision) <> '' "
            "AND btrim(quota_policy_revision) <> '' "
            "AND btrim(region_policy_revision) <> '' "
            "AND btrim(route_policy_revision) <> '' "
            "AND btrim(resource_policy_revision) <> '' "
            "AND btrim(readiness_revision) <> '' "
            "AND btrim(owner_pub_id) <> ''",
            name="ck_binding_v2_required_text",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(required_resource_kinds_json::jsonb) = 'array' "
            "AND jsonb_array_length(required_resource_kinds_json::jsonb) > 0",
            name="ck_binding_v2_resource_kinds",
        ),
        schema="platform",
    )
    op.create_index(
        "uq_binding_v2_active_target",
        "collection_binding_revision_v2",
        ["tenant_id", "project_id", "platform", "collection_surface", "product_variant"],
        unique=True,
        schema="platform",
        postgresql_where=sa.text("lifecycle_state = 'active'"),
    )

    _create_binding_subtypes()
    _create_binding_mappings()
    _create_binding_guards()


def _binding_subtype_constraints(
    table: str,
    surface: str,
) -> list[sa.Constraint]:
    return [
        *_scope_constraints(table),
        sa.ForeignKeyConstraint(
            ["binding_revision_id", "tenant_id", "project_id", "collection_surface"],
            [
                "platform.collection_binding_revision_v2.id",
                "platform.collection_binding_revision_v2.tenant_id",
                "platform.collection_binding_revision_v2.project_id",
                "platform.collection_binding_revision_v2.collection_surface",
            ],
            name=f"fk_{table}_binding_surface",
        ),
        sa.UniqueConstraint(
            "binding_revision_id",
            "tenant_id",
            "project_id",
            name=f"uq_{table}_binding",
        ),
        sa.CheckConstraint(
            f"collection_surface = '{surface}'",
            name=f"ck_{table}_surface",
        ),
    ]


def _create_binding_subtypes() -> None:
    op.create_table(
        "collection_api_binding_v2",
        *_identity_columns(),
        sa.Column("binding_revision_id", sa.Uuid(), nullable=False),
        sa.Column("collection_surface", sa.String(length=30), nullable=False),
        sa.Column("provider_gateway_handle", sa.String(length=255), nullable=False),
        sa.Column("provider_tenant_ref", sa.String(length=255), nullable=False),
        sa.Column("provider_account_ref", sa.String(length=255), nullable=False),
        sa.Column("provider_contract_ref", sa.String(length=255), nullable=False),
        sa.Column("credential_slot_ref", sa.String(length=255), nullable=False),
        sa.Column("endpoint_catalog_id", sa.String(length=128), nullable=False),
        sa.Column("endpoint_catalog_revision", sa.String(length=128), nullable=False),
        sa.Column("api_version", sa.String(length=128), nullable=False),
        sa.Column("entitlement_revision", sa.String(length=128), nullable=False),
        sa.Column("credential_rotation_revision", sa.String(length=128), nullable=False),
        sa.Column("egress_policy_revision", sa.String(length=128), nullable=False),
        sa.Column("credential_state", sa.String(length=30), nullable=False),
        sa.Column("entitlement_state", sa.String(length=30), nullable=False),
        sa.Column("provider_account_state", sa.String(length=30), nullable=False),
        sa.Column("relay_required", sa.Boolean(), nullable=False),
        *_binding_subtype_constraints(
            "collection_api_binding_v2",
            "provider_api",
        ),
        sa.CheckConstraint(
            "credential_state = 'ready' AND entitlement_state = 'ready' "
            "AND provider_account_state = 'ready'",
            name="ck_api_binding_v2_ready",
        ),
        sa.CheckConstraint(
            "provider_gateway_handle ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND provider_tenant_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND provider_account_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND provider_contract_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND credential_slot_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'",
            name="ck_api_binding_v2_opaque_refs",
        ),
        schema="platform",
    )
    op.create_table(
        "collection_web_binding_v2",
        *_identity_columns(),
        sa.Column("binding_revision_id", sa.Uuid(), nullable=False),
        sa.Column("collection_surface", sa.String(length=30), nullable=False),
        sa.Column("governed_account_ref", sa.String(length=255), nullable=False),
        sa.Column("browser_owner_handle", sa.String(length=255), nullable=False),
        sa.Column("browser_profile_ref", sa.String(length=255), nullable=False),
        sa.Column("browser_profile_revision", sa.String(length=128), nullable=False),
        sa.Column("web_session_ref", sa.String(length=255), nullable=False),
        sa.Column("web_session_revision", sa.String(length=128), nullable=False),
        sa.Column("approved_host_catalog_id", sa.String(length=128), nullable=False),
        sa.Column("approved_host_catalog_revision", sa.String(length=128), nullable=False),
        sa.Column("relay_policy_revision", sa.String(length=128), nullable=False),
        sa.Column("constraints_revision", sa.String(length=128), nullable=False),
        sa.Column("login_state", sa.String(length=30), nullable=False),
        sa.Column("captcha_state", sa.String(length=30), nullable=False),
        sa.Column("risk_state", sa.String(length=30), nullable=False),
        sa.Column("human_assist_state", sa.String(length=30), nullable=False),
        sa.Column("relay_required", sa.Boolean(), nullable=False),
        *_binding_subtype_constraints(
            "collection_web_binding_v2",
            "consumer_web",
        ),
        sa.CheckConstraint(
            "login_state = 'ready' AND captcha_state = 'ready' "
            "AND risk_state = 'ready' AND human_assist_state = 'ready'",
            name="ck_web_binding_v2_ready",
        ),
        sa.CheckConstraint(
            "governed_account_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND browser_owner_handle ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND browser_profile_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND web_session_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'",
            name="ck_web_binding_v2_opaque_refs",
        ),
        schema="platform",
    )
    op.create_table(
        "collection_app_binding_v2",
        *_identity_columns(),
        sa.Column("binding_revision_id", sa.Uuid(), nullable=False),
        sa.Column("collection_surface", sa.String(length=30), nullable=False),
        sa.Column("governed_account_ref", sa.String(length=255), nullable=False),
        sa.Column("device_owner_handle", sa.String(length=255), nullable=False),
        sa.Column("managed_device_ref", sa.String(length=255), nullable=False),
        sa.Column("app_package_id", sa.String(length=128), nullable=False),
        sa.Column("app_build_version", sa.String(length=128), nullable=False),
        sa.Column("distribution_channel", sa.String(length=128), nullable=False),
        sa.Column("app_install_ref", sa.String(length=255), nullable=False),
        sa.Column("app_profile_revision", sa.String(length=128), nullable=False),
        sa.Column("app_session_ref", sa.String(length=255), nullable=False),
        sa.Column("app_session_revision", sa.String(length=128), nullable=False),
        sa.Column("automation_agent_revision", sa.String(length=128), nullable=False),
        sa.Column("attestation_policy_revision", sa.String(length=128), nullable=False),
        sa.Column("relay_policy_revision", sa.String(length=128), nullable=False),
        sa.Column("session_state", sa.String(length=30), nullable=False),
        sa.Column("attestation_state", sa.String(length=30), nullable=False),
        sa.Column("device_health_state", sa.String(length=30), nullable=False),
        sa.Column("human_assist_state", sa.String(length=30), nullable=False),
        sa.Column("relay_required", sa.Boolean(), nullable=False),
        *_binding_subtype_constraints(
            "collection_app_binding_v2",
            "consumer_app",
        ),
        sa.CheckConstraint(
            "session_state = 'ready' AND attestation_state = 'ready' "
            "AND device_health_state = 'ready' AND human_assist_state = 'ready'",
            name="ck_app_binding_v2_ready",
        ),
        sa.CheckConstraint(
            "governed_account_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND device_owner_handle ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND managed_device_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND app_install_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND app_session_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'",
            name="ck_app_binding_v2_opaque_refs",
        ),
        schema="platform",
    )


def _create_binding_mappings() -> None:
    op.create_table(
        "collection_binding_capability",
        *_identity_columns(),
        sa.Column("binding_revision_id", sa.Uuid(), nullable=False),
        sa.Column("capability_declaration_id", sa.Uuid(), nullable=False),
        sa.Column("capability_revision", sa.String(length=128), nullable=False),
        sa.Column("platform", sa.String(length=128), nullable=False),
        sa.Column("collection_surface", sa.String(length=30), nullable=False),
        sa.Column("product_variant", sa.String(length=128), nullable=False),
        sa.Column("interaction_mode", sa.String(length=128), nullable=False),
        sa.Column("requirement_state", sa.String(length=30), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        *_scope_constraints("collection_binding_capability"),
        sa.ForeignKeyConstraint(
            [
                "binding_revision_id",
                "tenant_id",
                "project_id",
                "platform",
                "collection_surface",
                "product_variant",
            ],
            [
                "platform.collection_binding_revision_v2.id",
                "platform.collection_binding_revision_v2.tenant_id",
                "platform.collection_binding_revision_v2.project_id",
                "platform.collection_binding_revision_v2.platform",
                "platform.collection_binding_revision_v2.collection_surface",
                "platform.collection_binding_revision_v2.product_variant",
            ],
            name="fk_binding_capability_binding_identity",
        ),
        sa.ForeignKeyConstraint(
            [
                "capability_declaration_id",
                "tenant_id",
                "project_id",
                "capability_revision",
                "platform",
                "collection_surface",
                "product_variant",
                "interaction_mode",
            ],
            [
                "platform.collection_capability_declaration.id",
                "platform.collection_capability_declaration.tenant_id",
                "platform.collection_capability_declaration.project_id",
                "platform.collection_capability_declaration.capability_revision",
                "platform.collection_capability_declaration.platform",
                "platform.collection_capability_declaration.collection_surface",
                "platform.collection_capability_declaration.product_variant",
                "platform.collection_capability_declaration.interaction_mode",
            ],
            name="fk_binding_capability_declaration_exact",
        ),
        sa.UniqueConstraint(
            "binding_revision_id",
            "tenant_id",
            "project_id",
            "capability_declaration_id",
            "interaction_mode",
            name="uq_binding_capability_declaration",
        ),
        sa.UniqueConstraint(
            "binding_revision_id",
            "tenant_id",
            "project_id",
            "ordinal",
            name="uq_binding_capability_ordinal",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "binding_revision_id",
            "capability_revision",
            "platform",
            "collection_surface",
            "product_variant",
            "interaction_mode",
            name="uq_binding_capability_grant_identity",
        ),
        sa.CheckConstraint(
            "requirement_state IN ('required','optional') AND ordinal >= 0",
            name="ck_binding_capability_requirement",
        ),
        sa.CheckConstraint(
            _SURFACE.format(column="collection_surface"),
            name="ck_binding_capability_surface",
        ),
        schema="platform",
    )
    op.create_table(
        "collection_binding_resource",
        *_identity_columns(),
        sa.Column("binding_revision_id", sa.Uuid(), nullable=False),
        sa.Column("resource_registration_id", sa.Uuid(), nullable=False),
        sa.Column("resource_pub_id", sa.String(length=30), nullable=False),
        sa.Column("resource_kind", sa.String(length=30), nullable=False),
        sa.Column("resource_role", sa.String(length=80), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("adoption_required", sa.Boolean(), nullable=False),
        sa.Column("mapping_revision", sa.String(length=128), nullable=False),
        *_scope_constraints("collection_binding_resource"),
        sa.ForeignKeyConstraint(
            ["binding_revision_id", "tenant_id", "project_id"],
            [
                "platform.collection_binding_revision_v2.id",
                "platform.collection_binding_revision_v2.tenant_id",
                "platform.collection_binding_revision_v2.project_id",
            ],
            name="fk_binding_resource_binding_scope",
        ),
        sa.ForeignKeyConstraint(
            [
                "resource_registration_id",
                "tenant_id",
                "project_id",
                "resource_kind",
                "resource_pub_id",
            ],
            [
                "platform.resource_registration.id",
                "platform.resource_registration.tenant_id",
                "platform.resource_registration.project_id",
                "platform.resource_registration.resource_kind",
                "platform.resource_registration.pub_id",
            ],
            name="fk_binding_resource_registration_exact",
        ),
        sa.UniqueConstraint(
            "binding_revision_id",
            "tenant_id",
            "project_id",
            "resource_registration_id",
            "resource_role",
            name="uq_binding_resource_registration",
        ),
        sa.UniqueConstraint(
            "binding_revision_id",
            "tenant_id",
            "project_id",
            "resource_role",
            "ordinal",
            name="uq_binding_resource_role_ordinal",
        ),
        sa.UniqueConstraint(
            "binding_revision_id",
            "tenant_id",
            "project_id",
            "resource_registration_id",
            "resource_pub_id",
            "resource_kind",
            "resource_role",
            "ordinal",
            "mapping_revision",
            name="uq_binding_resource_grant_mapping",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_binding_resource_ordinal"),
        sa.CheckConstraint(
            "resource_kind IN " + str(_RESOURCE_KINDS).replace('"', "'"),
            name="ck_binding_resource_kind",
        ),
        sa.CheckConstraint(
            "btrim(resource_role) <> '' AND btrim(mapping_revision) <> ''",
            name="ck_binding_resource_required_text",
        ),
        schema="platform",
    )
    op.create_table(
        "collection_binding_quota_scope",
        *_identity_columns(),
        sa.Column("binding_revision_id", sa.Uuid(), nullable=False),
        sa.Column("quota_scope_policy_id", sa.Uuid(), nullable=False),
        sa.Column("quota_registry_id", sa.Uuid(), nullable=False),
        sa.Column("scope_policy_key", sa.String(length=1500), nullable=False),
        sa.Column("scope_kind", sa.String(length=40), nullable=False),
        sa.Column("scope_subject_id", sa.String(length=255), nullable=False),
        sa.Column("policy_revision", sa.String(length=128), nullable=False),
        sa.Column("applicability_key", sa.String(length=1000), nullable=False),
        sa.Column("quota_units", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        *_scope_constraints("collection_binding_quota_scope"),
        sa.ForeignKeyConstraint(
            ["binding_revision_id", "tenant_id", "project_id", "quota_registry_id"],
            [
                "platform.collection_binding_revision_v2.id",
                "platform.collection_binding_revision_v2.tenant_id",
                "platform.collection_binding_revision_v2.project_id",
                "platform.collection_binding_revision_v2.quota_registry_id",
            ],
            name="fk_binding_quota_binding_registry",
        ),
        sa.ForeignKeyConstraint(
            [
                "quota_scope_policy_id",
                "tenant_id",
                "project_id",
                "quota_registry_id",
                "scope_policy_key",
                "scope_kind",
                "scope_subject_id",
                "policy_revision",
            ],
            [
                "platform.collection_quota_scope_policy.id",
                "platform.collection_quota_scope_policy.tenant_id",
                "platform.collection_quota_scope_policy.project_id",
                "platform.collection_quota_scope_policy.registry_revision_id",
                "platform.collection_quota_scope_policy.scope_policy_key",
                "platform.collection_quota_scope_policy.scope_kind",
                "platform.collection_quota_scope_policy.scope_subject_id",
                "platform.collection_quota_scope_policy.policy_revision",
            ],
            name="fk_binding_quota_scope_exact",
        ),
        sa.UniqueConstraint(
            "binding_revision_id",
            "tenant_id",
            "project_id",
            "quota_scope_policy_id",
            name="uq_binding_quota_scope_policy",
        ),
        sa.UniqueConstraint(
            "binding_revision_id",
            "tenant_id",
            "project_id",
            "ordinal",
            name="uq_binding_quota_scope_ordinal",
        ),
        sa.CheckConstraint(
            "quota_units > 0 AND ordinal >= 0",
            name="ck_binding_quota_scope_values",
        ),
        schema="platform",
    )


def _create_binding_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION platform.guard_binding_revision_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE subtype_count integer;
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NEW.lifecycle_state NOT IN ('draft','candidate') THEN
              RAISE EXCEPTION 'binding revision must begin mutable';
            END IF;
            RETURN NEW;
          END IF;
          IF TG_OP = 'DELETE' THEN
            IF OLD.lifecycle_state NOT IN ('draft','candidate') THEN
              RAISE EXCEPTION 'issued binding revision cannot be deleted';
            END IF;
            RETURN OLD;
          END IF;

          IF OLD.lifecycle_state IN ('active','suspended','revoked','superseded') AND
             ROW(NEW.pub_id, NEW.tenant_id, NEW.project_id,
                 NEW.parent_binding_revision_id, NEW.schema_version,
                 NEW.binding_key, NEW.binding_revision,
                 NEW.binding_policy_revision, NEW.platform,
                 NEW.collection_surface, NEW.product_variant,
                 NEW.capability_registry_id, NEW.capability_registry_revision,
                 NEW.quota_registry_id, NEW.quota_registry_revision,
                 NEW.quota_policy_revision, NEW.region_policy_revision,
                 NEW.route_policy_revision, NEW.resource_policy_revision,
                 NEW.readiness_revision, NEW.required_resource_kinds_json,
                 NEW.credential_references_json, NEW.canonical_json,
                 NEW.binding_hash, NEW.owner_pub_id, NEW.approved_by_pub_id,
                 NEW.approval_pub_id, NEW.approved_at, NEW.effective_from,
                 NEW.expires_at, NEW.activated_at)
             IS DISTINCT FROM
             ROW(OLD.pub_id, OLD.tenant_id, OLD.project_id,
                 OLD.parent_binding_revision_id, OLD.schema_version,
                 OLD.binding_key, OLD.binding_revision,
                 OLD.binding_policy_revision, OLD.platform,
                 OLD.collection_surface, OLD.product_variant,
                 OLD.capability_registry_id, OLD.capability_registry_revision,
                 OLD.quota_registry_id, OLD.quota_registry_revision,
                 OLD.quota_policy_revision, OLD.region_policy_revision,
                 OLD.route_policy_revision, OLD.resource_policy_revision,
                 OLD.readiness_revision, OLD.required_resource_kinds_json,
                 OLD.credential_references_json, OLD.canonical_json,
                 OLD.binding_hash, OLD.owner_pub_id, OLD.approved_by_pub_id,
                 OLD.approval_pub_id, OLD.approved_at, OLD.effective_from,
                 OLD.expires_at, OLD.activated_at) THEN
            RAISE EXCEPTION 'active binding identity and content are immutable';
          END IF;

          IF OLD.lifecycle_state = 'draft' AND
             NEW.lifecycle_state NOT IN ('draft','candidate') OR
             OLD.lifecycle_state = 'candidate' AND
             NEW.lifecycle_state NOT IN ('candidate','active') OR
             OLD.lifecycle_state = 'active' AND
             NEW.lifecycle_state NOT IN
               ('active','suspended','revoked','superseded') OR
             OLD.lifecycle_state = 'suspended' AND
             NEW.lifecycle_state NOT IN ('suspended','active','revoked','superseded') OR
             OLD.lifecycle_state IN ('revoked','superseded') AND
             NEW.lifecycle_state <> OLD.lifecycle_state THEN
            RAISE EXCEPTION 'invalid binding lifecycle transition';
          END IF;

          IF OLD.lifecycle_state <> 'active' AND NEW.lifecycle_state = 'active' THEN
            IF NEW.approved_at IS NULL OR NEW.activated_at IS NULL THEN
              RAISE EXCEPTION 'binding activation requires approval timestamps';
            END IF;
            IF NOT EXISTS (
              SELECT 1 FROM platform.collection_capability_registry_revision r
               WHERE r.id=NEW.capability_registry_id
                 AND r.tenant_id=NEW.tenant_id AND r.project_id=NEW.project_id
                 AND r.registry_revision=NEW.capability_registry_revision
                 AND r.lifecycle_state='active'
            ) OR NOT EXISTS (
              SELECT 1 FROM platform.collection_quota_registry_revision r
               WHERE r.id=NEW.quota_registry_id
                 AND r.tenant_id=NEW.tenant_id AND r.project_id=NEW.project_id
                 AND r.registry_revision=NEW.quota_registry_revision
                 AND r.lifecycle_state='active'
            ) THEN
              RAISE EXCEPTION 'binding activation requires active registries';
            END IF;
            SELECT
              (SELECT count(*) FROM platform.collection_api_binding_v2 s
                WHERE s.binding_revision_id=NEW.id
                  AND s.tenant_id=NEW.tenant_id AND s.project_id=NEW.project_id) +
              (SELECT count(*) FROM platform.collection_web_binding_v2 s
                WHERE s.binding_revision_id=NEW.id
                  AND s.tenant_id=NEW.tenant_id AND s.project_id=NEW.project_id) +
              (SELECT count(*) FROM platform.collection_app_binding_v2 s
                WHERE s.binding_revision_id=NEW.id
                  AND s.tenant_id=NEW.tenant_id AND s.project_id=NEW.project_id)
              INTO subtype_count;
            IF subtype_count <> 1 OR
               NOT EXISTS (
                 SELECT 1 FROM platform.collection_binding_capability m
                  JOIN platform.collection_capability_declaration d
                    ON d.id=m.capability_declaration_id
                   AND d.tenant_id=m.tenant_id AND d.project_id=m.project_id
                  WHERE m.binding_revision_id=NEW.id
                    AND m.tenant_id=NEW.tenant_id AND m.project_id=NEW.project_id
                    AND m.requirement_state='required'
                    AND d.registry_revision_id=NEW.capability_registry_id
                    AND d.production_allowed=true AND d.status='supported'
               ) OR
               EXISTS (
                 SELECT 1 FROM platform.collection_binding_capability m
                  JOIN platform.collection_capability_declaration d
                    ON d.id=m.capability_declaration_id
                   AND d.tenant_id=m.tenant_id AND d.project_id=m.project_id
                  WHERE m.binding_revision_id=NEW.id
                    AND m.tenant_id=NEW.tenant_id AND m.project_id=NEW.project_id
                    AND m.requirement_state='required'
                    AND (d.registry_revision_id<>NEW.capability_registry_id
                         OR d.production_allowed=false OR d.status<>'supported')
               ) OR
               NOT EXISTS (
                 SELECT 1 FROM platform.collection_binding_resource m
                  JOIN platform.resource_registration r
                    ON r.id=m.resource_registration_id
                   AND r.tenant_id=m.tenant_id AND r.project_id=m.project_id
                  WHERE m.binding_revision_id=NEW.id
                    AND m.tenant_id=NEW.tenant_id AND m.project_id=NEW.project_id
                    AND m.required=true
                    AND r.resource_schema_version='collection-resource-v2'
                    AND r.state='active' AND r.revoked_at IS NULL
               ) OR
               EXISTS (
                 SELECT 1 FROM platform.collection_binding_resource m
                  JOIN platform.resource_registration r
                    ON r.id=m.resource_registration_id
                   AND r.tenant_id=m.tenant_id AND r.project_id=m.project_id
                  WHERE m.binding_revision_id=NEW.id
                    AND m.tenant_id=NEW.tenant_id AND m.project_id=NEW.project_id
                    AND m.required=true
                    AND (r.resource_schema_version<>'collection-resource-v2'
                         OR r.state<>'active' OR r.revoked_at IS NOT NULL
                         OR (m.adoption_required AND NOT EXISTS (
                           SELECT 1 FROM platform.collection_resource_adoption a
                            WHERE a.resource_registration_id=r.id
                              AND a.tenant_id=r.tenant_id
                              AND a.project_id=r.project_id
                              AND a.verification_state='verified'
                         )))
               ) OR
               EXISTS (
                 SELECT 1
                   FROM platform.collection_binding_capability capability
                   JOIN platform.collection_capability_declaration declaration
                     ON declaration.id=capability.capability_declaration_id
                    AND declaration.tenant_id=capability.tenant_id
                    AND declaration.project_id=capability.project_id
                   CROSS JOIN LATERAL jsonb_array_elements_text(
                     declaration.required_resource_kinds_json::jsonb
                   ) AS policy_kind(kind)
                  WHERE capability.binding_revision_id=NEW.id
                    AND capability.tenant_id=NEW.tenant_id
                    AND capability.project_id=NEW.project_id
                    AND capability.requirement_state='required'
                    AND declaration.registry_revision_id=NEW.capability_registry_id
                    AND (policy_kind.kind NOT IN
                      ('provider_tenant','credential_slot','governed_account',
                       'browser_owner','browser_profile','web_session',
                       'device_owner','app_install','app_session','relay_capacity')
                      OR NOT EXISTS (
                        SELECT 1 FROM platform.collection_binding_resource resource
                         WHERE resource.binding_revision_id=NEW.id
                           AND resource.tenant_id=NEW.tenant_id
                           AND resource.project_id=NEW.project_id
                           AND resource.required=true
                           AND resource.resource_kind=policy_kind.kind
                      ))
               ) OR
               EXISTS (
                 SELECT 1 FROM platform.collection_binding_resource resource
                  WHERE resource.binding_revision_id=NEW.id
                    AND resource.tenant_id=NEW.tenant_id
                    AND resource.project_id=NEW.project_id
                    AND resource.required=true
                    AND NOT EXISTS (
                      SELECT 1
                        FROM platform.collection_binding_capability capability
                        JOIN platform.collection_capability_declaration declaration
                          ON declaration.id=capability.capability_declaration_id
                         AND declaration.tenant_id=capability.tenant_id
                         AND declaration.project_id=capability.project_id
                        CROSS JOIN LATERAL jsonb_array_elements_text(
                          declaration.required_resource_kinds_json::jsonb
                        ) AS policy_kind(kind)
                       WHERE capability.binding_revision_id=NEW.id
                         AND capability.tenant_id=NEW.tenant_id
                         AND capability.project_id=NEW.project_id
                         AND capability.requirement_state='required'
                         AND declaration.registry_revision_id=
                             NEW.capability_registry_id
                         AND policy_kind.kind=resource.resource_kind
                    )
                    AND NOT (resource.resource_kind='relay_capacity' AND
                      (EXISTS (
                        SELECT 1 FROM platform.collection_api_binding_v2 subtype
                         WHERE subtype.binding_revision_id=NEW.id
                           AND subtype.tenant_id=NEW.tenant_id
                           AND subtype.project_id=NEW.project_id
                           AND subtype.relay_required=true
                      ) OR EXISTS (
                        SELECT 1 FROM platform.collection_web_binding_v2 subtype
                         WHERE subtype.binding_revision_id=NEW.id
                           AND subtype.tenant_id=NEW.tenant_id
                           AND subtype.project_id=NEW.project_id
                           AND subtype.relay_required=true
                      ) OR EXISTS (
                        SELECT 1 FROM platform.collection_app_binding_v2 subtype
                         WHERE subtype.binding_revision_id=NEW.id
                           AND subtype.tenant_id=NEW.tenant_id
                           AND subtype.project_id=NEW.project_id
                           AND subtype.relay_required=true
                      )))
               ) OR
               EXISTS (
                 SELECT 1 FROM jsonb_array_elements_text(
                   NEW.required_resource_kinds_json::jsonb
                 ) AS declared(kind)
                  WHERE NOT EXISTS (
                    SELECT 1 FROM platform.collection_binding_resource resource
                     WHERE resource.binding_revision_id=NEW.id
                       AND resource.tenant_id=NEW.tenant_id
                       AND resource.project_id=NEW.project_id
                       AND resource.required=true
                       AND resource.resource_kind=declared.kind
                  )
               ) OR
               EXISTS (
                 SELECT 1 FROM platform.collection_binding_resource resource
                  WHERE resource.binding_revision_id=NEW.id
                    AND resource.tenant_id=NEW.tenant_id
                    AND resource.project_id=NEW.project_id
                    AND resource.required=true
                    AND NOT (NEW.required_resource_kinds_json::jsonb ?
                             resource.resource_kind)
               ) OR
               ((NEW.collection_surface='provider_api' AND EXISTS (
                   SELECT 1 FROM platform.collection_api_binding_v2 subtype
                    WHERE subtype.binding_revision_id=NEW.id
                      AND subtype.tenant_id=NEW.tenant_id
                      AND subtype.project_id=NEW.project_id
                      AND (NOT EXISTS (
                        SELECT 1 FROM platform.collection_binding_resource resource
                        JOIN platform.resource_registration registration
                          ON registration.id=resource.resource_registration_id
                         AND registration.tenant_id=resource.tenant_id
                         AND registration.project_id=resource.project_id
                       WHERE resource.binding_revision_id=NEW.id
                         AND resource.tenant_id=NEW.tenant_id
                         AND resource.project_id=NEW.project_id
                         AND resource.required=true
                         AND resource.resource_kind='provider_tenant'
                         AND registration.pub_id=subtype.provider_tenant_ref
                         AND registration.opaque_owner_handle=
                             subtype.provider_gateway_handle
                      ) OR NOT EXISTS (
                        SELECT 1 FROM platform.collection_binding_resource resource
                        JOIN platform.resource_registration registration
                          ON registration.id=resource.resource_registration_id
                         AND registration.tenant_id=resource.tenant_id
                         AND registration.project_id=resource.project_id
                       WHERE resource.binding_revision_id=NEW.id
                         AND resource.tenant_id=NEW.tenant_id
                         AND resource.project_id=NEW.project_id
                         AND resource.required=true
                         AND resource.resource_kind='credential_slot'
                         AND registration.pub_id=subtype.credential_slot_ref
                      ) OR (subtype.relay_required AND NOT EXISTS (
                        SELECT 1 FROM platform.collection_binding_resource resource
                         WHERE resource.binding_revision_id=NEW.id
                           AND resource.tenant_id=NEW.tenant_id
                           AND resource.project_id=NEW.project_id
                           AND resource.required=true
                           AND resource.resource_kind='relay_capacity'
                      )))
                 )) OR
                (NEW.collection_surface='consumer_web' AND EXISTS (
                   SELECT 1 FROM platform.collection_web_binding_v2 subtype
                    WHERE subtype.binding_revision_id=NEW.id
                      AND subtype.tenant_id=NEW.tenant_id
                      AND subtype.project_id=NEW.project_id
                      AND (NOT EXISTS (
                        SELECT 1 FROM platform.collection_binding_resource resource
                        JOIN platform.resource_registration registration
                          ON registration.id=resource.resource_registration_id
                         AND registration.tenant_id=resource.tenant_id
                         AND registration.project_id=resource.project_id
                       WHERE resource.binding_revision_id=NEW.id
                         AND resource.tenant_id=NEW.tenant_id
                         AND resource.project_id=NEW.project_id
                         AND resource.required=true
                         AND resource.resource_kind='browser_owner'
                         AND registration.opaque_owner_handle=
                             subtype.browser_owner_handle
                      ) OR NOT EXISTS (
                        SELECT 1 FROM platform.collection_binding_resource resource
                         WHERE resource.binding_revision_id=NEW.id
                           AND resource.tenant_id=NEW.tenant_id
                           AND resource.project_id=NEW.project_id
                           AND resource.required=true
                           AND ((resource.resource_kind='governed_account' AND
                                 resource.resource_pub_id=
                                   subtype.governed_account_ref) OR
                                (resource.resource_kind='browser_profile' AND
                                 resource.resource_pub_id=
                                   subtype.browser_profile_ref) OR
                                (resource.resource_kind='web_session' AND
                                 resource.resource_pub_id=subtype.web_session_ref))
                         GROUP BY resource.binding_revision_id
                        HAVING count(DISTINCT resource.resource_kind) = 3
                      ) OR (subtype.relay_required AND NOT EXISTS (
                        SELECT 1 FROM platform.collection_binding_resource resource
                         WHERE resource.binding_revision_id=NEW.id
                           AND resource.tenant_id=NEW.tenant_id
                           AND resource.project_id=NEW.project_id
                           AND resource.required=true
                           AND resource.resource_kind='relay_capacity'
                      )))
                 )) OR
                (NEW.collection_surface='consumer_app' AND EXISTS (
                   SELECT 1 FROM platform.collection_app_binding_v2 subtype
                    WHERE subtype.binding_revision_id=NEW.id
                      AND subtype.tenant_id=NEW.tenant_id
                      AND subtype.project_id=NEW.project_id
                      AND (NOT EXISTS (
                        SELECT 1 FROM platform.collection_binding_resource resource
                        JOIN platform.resource_registration registration
                          ON registration.id=resource.resource_registration_id
                         AND registration.tenant_id=resource.tenant_id
                         AND registration.project_id=resource.project_id
                       WHERE resource.binding_revision_id=NEW.id
                         AND resource.tenant_id=NEW.tenant_id
                         AND resource.project_id=NEW.project_id
                         AND resource.required=true
                         AND resource.resource_kind='device_owner'
                         AND registration.pub_id=subtype.managed_device_ref
                         AND registration.opaque_owner_handle=
                             subtype.device_owner_handle
                      ) OR NOT EXISTS (
                        SELECT 1 FROM platform.collection_binding_resource resource
                         WHERE resource.binding_revision_id=NEW.id
                           AND resource.tenant_id=NEW.tenant_id
                           AND resource.project_id=NEW.project_id
                           AND resource.required=true
                           AND ((resource.resource_kind='governed_account' AND
                                 resource.resource_pub_id=
                                   subtype.governed_account_ref) OR
                                (resource.resource_kind='app_install' AND
                                 resource.resource_pub_id=subtype.app_install_ref) OR
                                (resource.resource_kind='app_session' AND
                                 resource.resource_pub_id=subtype.app_session_ref))
                         GROUP BY resource.binding_revision_id
                        HAVING count(DISTINCT resource.resource_kind) = 3
                      ) OR (subtype.relay_required AND NOT EXISTS (
                        SELECT 1 FROM platform.collection_binding_resource resource
                         WHERE resource.binding_revision_id=NEW.id
                           AND resource.tenant_id=NEW.tenant_id
                           AND resource.project_id=NEW.project_id
                           AND resource.required=true
                           AND resource.resource_kind='relay_capacity'
                      )))
                 ))) OR
               NOT EXISTS (
                 SELECT 1 FROM platform.collection_binding_quota_scope m
                  WHERE m.binding_revision_id=NEW.id
                    AND m.tenant_id=NEW.tenant_id AND m.project_id=NEW.project_id
               ) THEN
              RAISE EXCEPTION 'binding activation requires subtype and formal mappings';
            END IF;
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.guard_binding_child_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE parent_state text;
        DECLARE capability_parent_state text;
        BEGIN
          SELECT lifecycle_state INTO parent_state
            FROM platform.collection_binding_revision_v2
           WHERE id=COALESCE(NEW.binding_revision_id, OLD.binding_revision_id)
             AND tenant_id=COALESCE(NEW.tenant_id, OLD.tenant_id)
             AND project_id=COALESCE(NEW.project_id, OLD.project_id);
          IF parent_state NOT IN ('draft','candidate') THEN
            RAISE EXCEPTION 'active binding children and mappings are immutable';
          END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
          IF TG_TABLE_NAME='collection_binding_capability' THEN
            SELECT registry.lifecycle_state INTO capability_parent_state
              FROM platform.collection_binding_revision_v2 binding
              JOIN platform.collection_capability_declaration declaration
                ON declaration.id=NEW.capability_declaration_id
               AND declaration.tenant_id=NEW.tenant_id
               AND declaration.project_id=NEW.project_id
              JOIN platform.collection_capability_registry_revision registry
                ON registry.id=declaration.registry_revision_id
               AND registry.tenant_id=declaration.tenant_id
               AND registry.project_id=declaration.project_id
             WHERE binding.id=NEW.binding_revision_id
               AND binding.tenant_id=NEW.tenant_id
               AND binding.project_id=NEW.project_id
               AND binding.capability_registry_id=registry.id
               AND declaration.status='supported'
               AND declaration.production_allowed=true;
            IF capability_parent_state NOT IN ('frozen','active') THEN
              RAISE EXCEPTION
                'binding capability must use supported declaration from exact frozen registry';
            END IF;
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER binding_revision_v2_guard_trg
        BEFORE INSERT OR UPDATE OR DELETE
        ON platform.collection_binding_revision_v2
        FOR EACH ROW EXECUTE FUNCTION platform.guard_binding_revision_v2()
        """
    )
    for table in (
        "collection_api_binding_v2",
        "collection_web_binding_v2",
        "collection_app_binding_v2",
        "collection_binding_capability",
        "collection_binding_resource",
        "collection_binding_quota_scope",
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table}_guard_trg
            BEFORE INSERT OR UPDATE OR DELETE ON platform.{table}
            FOR EACH ROW EXECUTE FUNCTION platform.guard_binding_child_v2()
            """
        )


def _create_submission_operation() -> None:
    op.create_unique_constraint(
        "uq_primary_slot_operation_identity_s07",
        "collection_primary_slot",
        [
            "id",
            "tenant_id",
            "project_id",
            "campaign_id",
            "campaign_target_id",
            "sampling_leg_id",
            "slot_key",
            "platform",
            "collection_surface",
            "product_variant",
            "province_code",
            "interaction_mode",
        ],
        schema="platform",
    )
    op.create_table(
        "collection_submission_operation",
        *_identity_columns(),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_target_id", sa.Uuid(), nullable=False),
        sa.Column("sampling_leg_id", sa.Uuid(), nullable=False),
        sa.Column("primary_slot_id", sa.Uuid(), nullable=False),
        sa.Column("slot_key", sa.String(length=1500), nullable=False),
        sa.Column("platform", sa.String(length=128), nullable=False),
        sa.Column("collection_surface", sa.String(length=30), nullable=False),
        sa.Column("product_variant", sa.String(length=128), nullable=False),
        sa.Column("province_code", sa.String(length=6), nullable=False),
        sa.Column("interaction_mode", sa.String(length=128), nullable=False),
        sa.Column("operation_generation", sa.Integer(), nullable=False),
        sa.Column("operation_key", sa.String(length=1800), nullable=False),
        sa.Column("operation_policy_revision", sa.String(length=128), nullable=False),
        sa.Column("send_state", sa.String(length=40), nullable=False),
        sa.Column("send_state_version", sa.Integer(), nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("send_started_at", sa.DateTime(timezone=True)),
        sa.Column("send_resolved_at", sa.DateTime(timezone=True)),
        sa.Column("reconciliation_state", sa.String(length=40), nullable=False),
        sa.Column("reconcile_after", sa.DateTime(timezone=True)),
        sa.Column("state_reason", sa.String(length=128), nullable=False),
        *_scope_constraints("collection_submission_operation"),
        sa.ForeignKeyConstraint(
            [
                "primary_slot_id",
                "tenant_id",
                "project_id",
                "campaign_id",
                "campaign_target_id",
                "sampling_leg_id",
                "slot_key",
                "platform",
                "collection_surface",
                "product_variant",
                "province_code",
                "interaction_mode",
            ],
            [
                "platform.collection_primary_slot.id",
                "platform.collection_primary_slot.tenant_id",
                "platform.collection_primary_slot.project_id",
                "platform.collection_primary_slot.campaign_id",
                "platform.collection_primary_slot.campaign_target_id",
                "platform.collection_primary_slot.sampling_leg_id",
                "platform.collection_primary_slot.slot_key",
                "platform.collection_primary_slot.platform",
                "platform.collection_primary_slot.collection_surface",
                "platform.collection_primary_slot.product_variant",
                "platform.collection_primary_slot.province_code",
                "platform.collection_primary_slot.interaction_mode",
            ],
            name="fk_submission_operation_slot_identity",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "primary_slot_id",
            "operation_generation",
            name="uq_submission_operation_generation",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "operation_key",
            name="uq_submission_operation_key",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "campaign_id",
            "campaign_target_id",
            "sampling_leg_id",
            "primary_slot_id",
            "platform",
            "collection_surface",
            "product_variant",
            "province_code",
            "interaction_mode",
            name="uq_submission_operation_grant_identity",
        ),
        sa.CheckConstraint(
            _SURFACE.format(column="collection_surface"),
            name="ck_submission_operation_surface",
        ),
        sa.CheckConstraint(
            "province_code ~ '^[0-9]{6}$'",
            name="ck_submission_operation_province",
        ),
        sa.CheckConstraint(
            "operation_generation > 0 AND send_state_version > 0",
            name="ck_submission_operation_versions",
        ),
        sa.CheckConstraint(
            "send_state IN "
            "('NOT_SENT','SENDING','CONFIRMED_SENT','SEND_UNKNOWN',"
            "'CONFIRMED_NOT_SENT')",
            name="ck_submission_operation_send_state",
        ),
        sa.CheckConstraint(
            "reconciliation_state IN ('not_required','pending','in_progress','resolved','blocked')",
            name="ck_submission_operation_reconciliation",
        ),
        sa.CheckConstraint(
            "(send_state = 'NOT_SENT' AND send_started_at IS NULL "
            "AND send_resolved_at IS NULL) OR "
            "(send_state = 'SENDING' AND send_started_at IS NOT NULL "
            "AND send_resolved_at IS NULL) OR "
            "(send_state IN ('CONFIRMED_SENT','SEND_UNKNOWN') "
            "AND send_started_at IS NOT NULL AND send_resolved_at IS NOT NULL) OR "
            "(send_state = 'CONFIRMED_NOT_SENT' AND send_resolved_at IS NOT NULL)",
            name="ck_submission_operation_timestamps",
        ),
        sa.CheckConstraint(
            "send_state <> 'SEND_UNKNOWN' OR reconciliation_state <> 'not_required'",
            name="ck_submission_operation_unknown_reconcile",
        ),
        sa.CheckConstraint(
            "btrim(slot_key) <> '' AND btrim(operation_key) <> '' "
            "AND btrim(operation_policy_revision) <> '' "
            "AND btrim(platform) <> '' AND btrim(product_variant) <> '' "
            "AND btrim(interaction_mode) <> '' AND btrim(state_reason) <> ''",
            name="ck_submission_operation_required_text",
        ),
        schema="platform",
    )
    op.create_index(
        "uq_submission_operation_no_resend",
        "collection_submission_operation",
        ["tenant_id", "project_id", "primary_slot_id"],
        unique=True,
        schema="platform",
        postgresql_where=sa.text(
            "send_state IN ('NOT_SENT','SENDING','CONFIRMED_SENT','SEND_UNKNOWN')"
        ),
    )
    op.create_index(
        "ix_submission_operation_reconcile",
        "collection_submission_operation",
        ["tenant_id", "project_id", "reconciliation_state", "reconcile_after"],
        schema="platform",
    )
    op.execute(
        """
        CREATE FUNCTION platform.guard_submission_operation_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE previous_state text;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'submission operations are durable and cannot be deleted';
          END IF;
          IF TG_OP = 'INSERT' THEN
            IF NEW.send_state <> 'NOT_SENT' OR NEW.send_state_version <> 1 THEN
              RAISE EXCEPTION 'submission operation must begin at NOT_SENT version 1';
            END IF;
            IF NEW.operation_generation = 1 THEN
              IF EXISTS (
                SELECT 1 FROM platform.collection_submission_operation o
                 WHERE o.tenant_id=NEW.tenant_id AND o.project_id=NEW.project_id
                   AND o.primary_slot_id=NEW.primary_slot_id
              ) THEN
                RAISE EXCEPTION 'submission operation generation must be monotonic';
              END IF;
            ELSE
              SELECT send_state INTO previous_state
                FROM platform.collection_submission_operation o
               WHERE o.tenant_id=NEW.tenant_id AND o.project_id=NEW.project_id
                 AND o.primary_slot_id=NEW.primary_slot_id
                 AND o.operation_generation=NEW.operation_generation - 1
               FOR KEY SHARE;
              IF previous_state IS DISTINCT FROM 'CONFIRMED_NOT_SENT' THEN
                RAISE EXCEPTION
                  'new submission generation requires prior CONFIRMED_NOT_SENT';
              END IF;
              IF EXISTS (
                SELECT 1 FROM platform.collection_submission_operation o
                 WHERE o.tenant_id=NEW.tenant_id AND o.project_id=NEW.project_id
                   AND o.primary_slot_id=NEW.primary_slot_id
                   AND o.operation_generation >= NEW.operation_generation
              ) THEN
                RAISE EXCEPTION 'submission operation generation must be monotonic';
              END IF;
            END IF;
            RETURN NEW;
          END IF;

          IF ROW(NEW.pub_id, NEW.tenant_id, NEW.project_id, NEW.campaign_id,
                 NEW.campaign_target_id, NEW.sampling_leg_id, NEW.primary_slot_id,
                 NEW.slot_key, NEW.platform, NEW.collection_surface,
                 NEW.product_variant, NEW.province_code, NEW.interaction_mode,
                 NEW.operation_generation, NEW.operation_key,
                 NEW.operation_policy_revision, NEW.prepared_at)
             IS DISTINCT FROM
             ROW(OLD.pub_id, OLD.tenant_id, OLD.project_id, OLD.campaign_id,
                 OLD.campaign_target_id, OLD.sampling_leg_id, OLD.primary_slot_id,
                 OLD.slot_key, OLD.platform, OLD.collection_surface,
                 OLD.product_variant, OLD.province_code, OLD.interaction_mode,
                 OLD.operation_generation, OLD.operation_key,
                 OLD.operation_policy_revision, OLD.prepared_at) THEN
            RAISE EXCEPTION 'submission operation identity is immutable';
          END IF;
          IF NEW.send_state <> OLD.send_state THEN
            IF NEW.send_state_version <> OLD.send_state_version + 1 THEN
              RAISE EXCEPTION 'send-state transition must increment version once';
            END IF;
            IF OLD.send_state='SENDING' AND
               NEW.send_state='CONFIRMED_NOT_SENT' AND (
                 OLD.reconciliation_state <> 'in_progress' OR
                 NEW.reconciliation_state <> 'resolved' OR
                 NOT EXISTS (
                   SELECT 1
                     FROM platform.collection_submission_reconciliation_proof p
                    WHERE p.operation_id=OLD.id
                      AND p.tenant_id=OLD.tenant_id
                      AND p.project_id=OLD.project_id
                      AND p.proof_kind='owner_proved_not_sent'
                      AND p.proof_state='accepted'
                 ) OR EXISTS (
                   SELECT 1 FROM platform.resource_lease l
                    WHERE l.operation_id=OLD.id
                      AND l.tenant_id=OLD.tenant_id
                      AND l.project_id=OLD.project_id
                      AND l.lease_schema_version='collection-resource-lease-v2'
                      AND (l.lease_state NOT IN
                            ('released','expired','preempted','quarantined')
                           OR (l.lease_state='released' AND l.released_at IS NULL)
                           OR (l.lease_state='expired'
                               AND l.expires_at > CURRENT_TIMESTAMP)
                           OR (l.lease_state IN ('preempted','quarantined')
                               AND l.revoked_at IS NULL))
                 )
               ) THEN
              RAISE EXCEPTION
                'SENDING operation requires accepted not-sent proof and terminated leases';
            END IF;
            IF NOT (
              (OLD.send_state='NOT_SENT' AND
               NEW.send_state IN ('SENDING','CONFIRMED_NOT_SENT')) OR
              (OLD.send_state='SENDING' AND NEW.send_state IN
               ('CONFIRMED_SENT','SEND_UNKNOWN','CONFIRMED_NOT_SENT'))
            ) THEN
              RAISE EXCEPTION 'invalid irreversible send-state transition';
            END IF;
          ELSIF NEW.send_state_version <> OLD.send_state_version THEN
            RAISE EXCEPTION 'send-state version changed without a transition';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER submission_operation_v2_guard_trg
        BEFORE INSERT OR UPDATE OR DELETE
        ON platform.collection_submission_operation
        FOR EACH ROW EXECUTE FUNCTION platform.guard_submission_operation_v2()
        """
    )


def _create_submission_reconciliation_proof() -> None:
    op.create_table(
        "collection_submission_reconciliation_proof",
        *_identity_columns(),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("proof_key", sa.String(length=1000), nullable=False),
        sa.Column("proof_kind", sa.String(length=40), nullable=False),
        sa.Column("owner_gateway_revision", sa.String(length=128), nullable=False),
        sa.Column("owner_evidence_ref", sa.String(length=255), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("terminated_lease_count", sa.Integer(), nullable=False),
        sa.Column("terminated_lease_set_hash", sa.String(length=64), nullable=False),
        sa.Column("proof_state", sa.String(length=30), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("recorded_by", sa.String(length=128), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        *_scope_constraints("collection_submission_reconciliation_proof"),
        sa.ForeignKeyConstraint(
            ["operation_id", "tenant_id", "project_id"],
            [
                "platform.collection_submission_operation.id",
                "platform.collection_submission_operation.tenant_id",
                "platform.collection_submission_operation.project_id",
            ],
            name="fk_submission_reconciliation_proof_operation_scope",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "proof_key",
            name="uq_submission_reconciliation_proof_key",
        ),
        sa.UniqueConstraint(
            "operation_id",
            "tenant_id",
            "project_id",
            "evidence_hash",
            name="uq_submission_reconciliation_proof_evidence",
        ),
        sa.CheckConstraint(
            "proof_kind = 'owner_proved_not_sent' AND proof_state = 'accepted'",
            name="ck_submission_reconciliation_proof_kind_state",
        ),
        sa.CheckConstraint(
            "owner_gateway_revision ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND owner_evidence_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'",
            name="ck_submission_reconciliation_proof_opaque_refs",
        ),
        sa.CheckConstraint(
            _SHA256.format(column="evidence_hash")
            + " AND "
            + _SHA256.format(column="terminated_lease_set_hash"),
            name="ck_submission_reconciliation_proof_hashes",
        ),
        sa.CheckConstraint(
            "terminated_lease_count > 0 AND btrim(reason_code) <> '' AND btrim(recorded_by) <> ''",
            name="ck_submission_reconciliation_proof_required",
        ),
        schema="platform",
    )
    op.execute(
        """
        CREATE FUNCTION platform.guard_submission_reconciliation_proof_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          RAISE EXCEPTION 'submission reconciliation proofs are append-only';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER submission_reconciliation_proof_v2_guard_trg
        BEFORE UPDATE OR DELETE
        ON platform.collection_submission_reconciliation_proof
        FOR EACH ROW
        EXECUTE FUNCTION platform.guard_submission_reconciliation_proof_v2()
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.record_collection_not_sent_proof_v2(
          p_tenant_id uuid,
          p_project_id uuid,
          p_operation_id uuid,
          p_owner_gateway_revision text,
          p_owner_evidence_ref text,
          p_evidence_hash text,
          p_reason_code text
        ) RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE
          proof_id uuid;
          operation_state text;
          lease_count integer;
          invalid_lease_count integer;
          lease_set_hash text;
          tenant_context text;
          caller_role text;
        BEGIN
          caller_role := current_setting('role', true);
          IF caller_role IS NULL OR caller_role = '' OR caller_role = 'none' THEN
            caller_role := session_user;
          END IF;
          IF caller_role <> 'geo_worker' THEN
            RAISE EXCEPTION 'reconciliation proof caller is not trusted worker';
          END IF;
          tenant_context := current_setting('app.tenant_id', true);
          IF tenant_context IS NULL OR tenant_context = '' OR
             tenant_context::uuid IS DISTINCT FROM p_tenant_id THEN
            RAISE EXCEPTION 'reconciliation proof tenant context mismatch';
          END IF;
          IF p_owner_gateway_revision !~
               '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_owner_evidence_ref !~
               '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' OR
             p_evidence_hash !~ '^[0-9a-f]{64}$' OR
             p_reason_code IS NULL OR btrim(p_reason_code) = '' THEN
            RAISE EXCEPTION 'reconciliation proof input is invalid';
          END IF;

          SELECT send_state INTO operation_state
            FROM platform.collection_submission_operation
           WHERE id=p_operation_id AND tenant_id=p_tenant_id
             AND project_id=p_project_id
           FOR UPDATE;
          IF operation_state IS DISTINCT FROM 'SENDING' THEN
            RAISE EXCEPTION 'not-sent proof requires a SENDING operation';
          END IF;

          SELECT count(*),
                 count(*) FILTER (
                   WHERE lease_state NOT IN
                     ('released','expired','preempted','quarantined')
                      OR (lease_state='released' AND released_at IS NULL)
                      OR (lease_state='expired' AND expires_at > CURRENT_TIMESTAMP)
                      OR (lease_state IN ('preempted','quarantined')
                          AND revoked_at IS NULL)
                 ),
                 encode(public.digest(string_agg(
                   id::text || ':' || fencing_token::text || ':' || lease_state ||
                   ':' || to_char(expires_at AT TIME ZONE 'UTC',
                                  'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                   E'\n' ORDER BY id
                 ), 'sha256'), 'hex')
            INTO lease_count, invalid_lease_count, lease_set_hash
            FROM platform.resource_lease
           WHERE tenant_id=p_tenant_id AND project_id=p_project_id
             AND operation_id=p_operation_id
             AND lease_schema_version='collection-resource-lease-v2';
          IF lease_count = 0 OR invalid_lease_count <> 0 OR
             lease_set_hash IS NULL THEN
            RAISE EXCEPTION 'not-sent proof requires every formal lease terminated';
          END IF;

          SELECT id INTO proof_id
            FROM platform.collection_submission_reconciliation_proof
           WHERE tenant_id=p_tenant_id AND project_id=p_project_id
             AND operation_id=p_operation_id AND evidence_hash=p_evidence_hash;
          IF proof_id IS NOT NULL THEN
            RETURN proof_id;
          END IF;

          proof_id := gen_random_uuid();
          INSERT INTO platform.collection_submission_reconciliation_proof (
            id,pub_id,tenant_id,project_id,operation_id,proof_key,proof_kind,
            owner_gateway_revision,owner_evidence_ref,evidence_hash,
            terminated_lease_count,terminated_lease_set_hash,proof_state,
            reason_code,recorded_by,accepted_at
          ) VALUES (
            proof_id,'crp_' || substr(replace(proof_id::text,'-',''),1,26),
            p_tenant_id,p_project_id,p_operation_id,
            p_operation_id::text || ':' || p_evidence_hash,
            'owner_proved_not_sent',p_owner_gateway_revision,
            p_owner_evidence_ref,p_evidence_hash,lease_count,lease_set_hash,
            'accepted',p_reason_code,caller_role,CURRENT_TIMESTAMP
          );
          UPDATE platform.collection_submission_operation
             SET reconciliation_state='in_progress', reconcile_after=NULL,
                 state_reason=p_reason_code, version=version+1,
                 updated_at=CURRENT_TIMESTAMP
           WHERE id=p_operation_id AND tenant_id=p_tenant_id
             AND project_id=p_project_id AND send_state='SENDING';
          RETURN proof_id;
        END
        $$
        """
    )


def _create_resource_governance_tables() -> None:
    op.create_unique_constraint(
        "uq_platform_account_id_tenant_s07",
        "platform_account",
        ["id", "tenant_id"],
        schema="platform",
    )
    op.create_unique_constraint(
        "uq_browser_profile_id_tenant_s07",
        "browser_profile",
        ["id", "tenant_id"],
        schema="platform",
    )
    op.create_table(
        "collection_resource_adoption",
        *_identity_columns(),
        sa.Column("resource_registration_id", sa.Uuid(), nullable=False),
        sa.Column("adoption_key", sa.String(length=255), nullable=False),
        sa.Column("source_kind", sa.String(length=40), nullable=False),
        sa.Column("source_platform_account_id", sa.Uuid()),
        sa.Column("source_browser_profile_id", sa.Uuid()),
        sa.Column("source_s06_platform_account_id", sa.BigInteger()),
        sa.Column("source_s06_browser_id", sa.BigInteger()),
        sa.Column("source_s06_region_id", sa.BigInteger()),
        sa.Column("verification_state", sa.String(length=30), nullable=False),
        sa.Column("verification_revision", sa.String(length=128), nullable=False),
        sa.Column("verification_hash", sa.String(length=64), nullable=False),
        sa.Column("verified_by_pub_id", sa.String(length=255)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("adopted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("state_reason", sa.String(length=128), nullable=False),
        *_scope_constraints("collection_resource_adoption"),
        sa.ForeignKeyConstraint(
            ["resource_registration_id", "tenant_id", "project_id"],
            [
                "platform.resource_registration.id",
                "platform.resource_registration.tenant_id",
                "platform.resource_registration.project_id",
            ],
            name="fk_resource_adoption_registration_scope",
        ),
        sa.ForeignKeyConstraint(
            ["source_platform_account_id", "tenant_id"],
            ["platform.platform_account.id", "platform.platform_account.tenant_id"],
            name="fk_resource_adoption_s01_account",
        ),
        sa.ForeignKeyConstraint(
            ["source_browser_profile_id", "tenant_id"],
            ["platform.browser_profile.id", "platform.browser_profile.tenant_id"],
            name="fk_resource_adoption_s01_profile",
        ),
        sa.ForeignKeyConstraint(
            ["source_s06_platform_account_id"],
            ["platform.collection_platform_account.id"],
            name="fk_resource_adoption_s06_account",
        ),
        sa.ForeignKeyConstraint(
            ["source_s06_browser_id"],
            ["platform.collection_browser.id"],
            name="fk_resource_adoption_s06_browser",
        ),
        sa.ForeignKeyConstraint(
            ["source_s06_region_id"],
            ["platform.collection_region.id"],
            name="fk_resource_adoption_s06_region",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "adoption_key",
            name="uq_resource_adoption_key",
        ),
        sa.UniqueConstraint(
            "resource_registration_id",
            "tenant_id",
            "project_id",
            name="uq_resource_adoption_registration",
        ),
        sa.CheckConstraint(
            "source_kind IN "
            "('s01_platform_account','s01_browser_profile','s06_platform_account',"
            "'s06_browser','s06_region')",
            name="ck_resource_adoption_source_kind",
        ),
        sa.CheckConstraint(
            "(source_kind='s01_platform_account' "
            "AND source_platform_account_id IS NOT NULL "
            "AND source_browser_profile_id IS NULL "
            "AND source_s06_platform_account_id IS NULL "
            "AND source_s06_browser_id IS NULL AND source_s06_region_id IS NULL) OR "
            "(source_kind='s01_browser_profile' "
            "AND source_platform_account_id IS NULL "
            "AND source_browser_profile_id IS NOT NULL "
            "AND source_s06_platform_account_id IS NULL "
            "AND source_s06_browser_id IS NULL AND source_s06_region_id IS NULL) OR "
            "(source_kind='s06_platform_account' "
            "AND source_platform_account_id IS NULL "
            "AND source_browser_profile_id IS NULL "
            "AND source_s06_platform_account_id IS NOT NULL "
            "AND source_s06_browser_id IS NULL AND source_s06_region_id IS NULL) OR "
            "(source_kind='s06_browser' AND source_platform_account_id IS NULL "
            "AND source_browser_profile_id IS NULL "
            "AND source_s06_platform_account_id IS NULL "
            "AND source_s06_browser_id IS NOT NULL AND source_s06_region_id IS NULL) OR "
            "(source_kind='s06_region' AND source_platform_account_id IS NULL "
            "AND source_browser_profile_id IS NULL "
            "AND source_s06_platform_account_id IS NULL "
            "AND source_s06_browser_id IS NULL AND source_s06_region_id IS NOT NULL)",
            name="ck_resource_adoption_exact_source",
        ),
        sa.CheckConstraint(
            "verification_state IN ('proposed','verified','rejected','revoked')",
            name="ck_resource_adoption_state",
        ),
        sa.CheckConstraint(
            _SHA256.format(column="verification_hash"),
            name="ck_resource_adoption_hash",
        ),
        sa.CheckConstraint(
            "(verification_state='proposed' AND verified_at IS NULL "
            "AND adopted_at IS NULL AND revoked_at IS NULL) OR "
            "(verification_state='verified' AND verified_at IS NOT NULL "
            "AND adopted_at IS NOT NULL AND revoked_at IS NULL "
            "AND btrim(verified_by_pub_id) <> '') OR "
            "(verification_state='rejected' AND verified_at IS NOT NULL "
            "AND adopted_at IS NULL AND revoked_at IS NULL) OR "
            "(verification_state='revoked' AND verified_at IS NOT NULL "
            "AND adopted_at IS NOT NULL AND revoked_at IS NOT NULL)",
            name="ck_resource_adoption_timestamps",
        ),
        schema="platform",
    )
    for column in (
        "source_platform_account_id",
        "source_browser_profile_id",
        "source_s06_platform_account_id",
        "source_s06_browser_id",
        "source_s06_region_id",
    ):
        op.create_index(
            f"uq_resource_adoption_{column.removeprefix('source_')}",
            "collection_resource_adoption",
            [column],
            unique=True,
            schema="platform",
            postgresql_where=sa.text(f"{column} IS NOT NULL"),
        )

    op.create_table(
        "collection_resource_capacity_unit",
        *_identity_columns(),
        sa.Column("resource_registration_id", sa.Uuid(), nullable=False),
        sa.Column("resource_pub_id", sa.String(length=30), nullable=False),
        sa.Column("resource_kind", sa.String(length=30), nullable=False),
        sa.Column("capacity_unit_key", sa.String(length=255), nullable=False),
        sa.Column("unit_ordinal", sa.Integer(), nullable=False),
        sa.Column("capacity_state", sa.String(length=30), nullable=False),
        sa.Column("current_fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("owner_gateway_revision", sa.String(length=128), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("quarantined_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("state_reason", sa.String(length=128), nullable=False),
        *_scope_constraints("collection_resource_capacity_unit"),
        sa.ForeignKeyConstraint(
            [
                "resource_registration_id",
                "tenant_id",
                "project_id",
                "resource_kind",
                "resource_pub_id",
            ],
            [
                "platform.resource_registration.id",
                "platform.resource_registration.tenant_id",
                "platform.resource_registration.project_id",
                "platform.resource_registration.resource_kind",
                "platform.resource_registration.pub_id",
            ],
            name="fk_resource_capacity_registration_exact",
        ),
        sa.UniqueConstraint(
            "resource_registration_id",
            "tenant_id",
            "project_id",
            "unit_ordinal",
            name="uq_resource_capacity_ordinal",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "capacity_unit_key",
            name="uq_resource_capacity_key",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "resource_registration_id",
            "resource_kind",
            name="uq_resource_capacity_lease_identity",
        ),
        sa.CheckConstraint(
            "unit_ordinal >= 1 AND current_fencing_token >= 0",
            name="ck_resource_capacity_numbers",
        ),
        sa.CheckConstraint(
            "capacity_state IN ('candidate','available','leased','quarantined','revoked')",
            name="ck_resource_capacity_state",
        ),
        sa.CheckConstraint(
            "(capacity_state NOT IN ('quarantined','revoked')) OR "
            "(capacity_state='quarantined' AND quarantined_at IS NOT NULL) OR "
            "(capacity_state='revoked' AND revoked_at IS NOT NULL)",
            name="ck_resource_capacity_timestamps",
        ),
        schema="platform",
    )

    op.execute(
        """
        CREATE FUNCTION platform.guard_resource_adoption_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE source_tenant uuid;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'resource adoption evidence cannot be deleted';
          END IF;
          IF TG_OP = 'INSERT' AND NEW.verification_state <> 'proposed' THEN
            RAISE EXCEPTION 'resource adoption must begin proposed';
          END IF;
          IF TG_OP = 'UPDATE' AND
             ROW(NEW.pub_id, NEW.tenant_id, NEW.project_id,
                 NEW.resource_registration_id, NEW.adoption_key,
                 NEW.source_kind, NEW.source_platform_account_id,
                 NEW.source_browser_profile_id, NEW.source_s06_platform_account_id,
                 NEW.source_s06_browser_id, NEW.source_s06_region_id,
                 NEW.verification_revision, NEW.verification_hash)
             IS DISTINCT FROM
             ROW(OLD.pub_id, OLD.tenant_id, OLD.project_id,
                 OLD.resource_registration_id, OLD.adoption_key,
                 OLD.source_kind, OLD.source_platform_account_id,
                 OLD.source_browser_profile_id, OLD.source_s06_platform_account_id,
                 OLD.source_s06_browser_id, OLD.source_s06_region_id,
                 OLD.verification_revision, OLD.verification_hash) THEN
            RAISE EXCEPTION 'resource adoption source evidence is immutable';
          END IF;
          IF NEW.source_kind='s01_platform_account' THEN
            SELECT tenant_id INTO source_tenant FROM platform.platform_account
             WHERE id=NEW.source_platform_account_id;
          ELSIF NEW.source_kind='s01_browser_profile' THEN
            SELECT tenant_id INTO source_tenant FROM platform.browser_profile
             WHERE id=NEW.source_browser_profile_id;
          END IF;
          IF NEW.source_kind IN ('s01_platform_account','s01_browser_profile') AND
             (source_tenant IS NULL OR source_tenant <> NEW.tenant_id) THEN
            RAISE EXCEPTION 'legacy adoption cannot cross tenant scope';
          END IF;
          IF TG_OP = 'UPDATE' AND (
             OLD.verification_state='proposed' AND
             NEW.verification_state NOT IN ('proposed','verified','rejected') OR
             OLD.verification_state='verified' AND
             NEW.verification_state NOT IN ('verified','revoked') OR
             OLD.verification_state IN ('rejected','revoked') AND
             NEW.verification_state <> OLD.verification_state) THEN
            RAISE EXCEPTION 'invalid resource adoption transition';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.guard_resource_capacity_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NEW.capacity_state <> 'candidate' OR
               NEW.current_fencing_token <> 0 THEN
              RAISE EXCEPTION 'resource capacity must begin candidate at fence zero';
            END IF;
            RETURN NEW;
          END IF;
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'resource capacity history cannot be deleted';
          END IF;
          IF ROW(NEW.pub_id, NEW.tenant_id, NEW.project_id,
                 NEW.resource_registration_id, NEW.resource_pub_id,
                 NEW.resource_kind, NEW.capacity_unit_key, NEW.unit_ordinal,
                 NEW.owner_gateway_revision)
             IS DISTINCT FROM
             ROW(OLD.pub_id, OLD.tenant_id, OLD.project_id,
                 OLD.resource_registration_id, OLD.resource_pub_id,
                 OLD.resource_kind, OLD.capacity_unit_key, OLD.unit_ordinal,
                 OLD.owner_gateway_revision) THEN
            RAISE EXCEPTION 'resource capacity identity is immutable';
          END IF;
          IF NEW.current_fencing_token < OLD.current_fencing_token THEN
            RAISE EXCEPTION 'resource fencing token cannot decrease';
          END IF;
          IF OLD.capacity_state='candidate' AND
             NEW.capacity_state NOT IN ('candidate','available','quarantined','revoked') OR
             OLD.capacity_state='available' AND
             NEW.capacity_state NOT IN ('available','leased','quarantined','revoked') OR
             OLD.capacity_state='leased' AND
             NEW.capacity_state NOT IN ('leased','available','quarantined','revoked') OR
             OLD.capacity_state IN ('quarantined','revoked') AND
             NEW.capacity_state <> OLD.capacity_state THEN
            RAISE EXCEPTION 'invalid resource capacity transition';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER resource_adoption_v2_guard_trg
        BEFORE INSERT OR UPDATE OR DELETE ON platform.collection_resource_adoption
        FOR EACH ROW EXECUTE FUNCTION platform.guard_resource_adoption_v2()
        """
    )
    op.execute(
        """
        CREATE TRIGGER resource_capacity_v2_guard_trg
        BEFORE INSERT OR UPDATE OR DELETE
        ON platform.collection_resource_capacity_unit
        FOR EACH ROW EXECUTE FUNCTION platform.guard_resource_capacity_v2()
        """
    )


def _extend_resource_lease() -> None:
    columns: tuple[sa.Column[Any], ...] = (
        sa.Column("project_id", sa.Uuid()),
        sa.Column("lease_schema_version", sa.String(length=80)),
        sa.Column("resource_registration_id", sa.Uuid()),
        sa.Column("capacity_unit_id", sa.Uuid()),
        sa.Column("operation_id", sa.Uuid()),
        sa.Column("binding_revision_id", sa.Uuid()),
        sa.Column("lease_key", sa.String(length=1000)),
        sa.Column("lease_attempt", sa.Integer()),
        sa.Column("lease_state", sa.String(length=30)),
        sa.Column("acquired_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("owner_gateway_revision", sa.String(length=128)),
        sa.Column("reconciliation_reason", sa.String(length=128)),
    )
    for column in columns:
        op.add_column("resource_lease", column, schema="platform")

    op.create_foreign_key(
        "fk_resource_lease_project_s07",
        "resource_lease",
        "project",
        ["project_id", "tenant_id"],
        ["id", "tenant_id"],
        source_schema="platform",
        referent_schema="platform",
    )
    op.create_foreign_key(
        "fk_resource_lease_registration_s07",
        "resource_lease",
        "resource_registration",
        [
            "resource_registration_id",
            "tenant_id",
            "project_id",
            "resource_kind",
            "resource_pub_id",
        ],
        ["id", "tenant_id", "project_id", "resource_kind", "pub_id"],
        source_schema="platform",
        referent_schema="platform",
    )
    op.create_foreign_key(
        "fk_resource_lease_capacity_s07",
        "resource_lease",
        "collection_resource_capacity_unit",
        [
            "capacity_unit_id",
            "tenant_id",
            "project_id",
            "resource_registration_id",
            "resource_kind",
        ],
        ["id", "tenant_id", "project_id", "resource_registration_id", "resource_kind"],
        source_schema="platform",
        referent_schema="platform",
    )
    op.create_foreign_key(
        "fk_resource_lease_operation_s07",
        "resource_lease",
        "collection_submission_operation",
        ["operation_id", "tenant_id", "project_id"],
        ["id", "tenant_id", "project_id"],
        source_schema="platform",
        referent_schema="platform",
    )
    op.create_foreign_key(
        "fk_resource_lease_binding_s07",
        "resource_lease",
        "collection_binding_revision_v2",
        ["binding_revision_id", "tenant_id", "project_id"],
        ["id", "tenant_id", "project_id"],
        source_schema="platform",
        referent_schema="platform",
    )
    op.create_unique_constraint(
        "uq_resource_lease_scope_s07",
        "resource_lease",
        ["id", "tenant_id", "project_id"],
        schema="platform",
    )
    op.create_unique_constraint(
        "uq_resource_lease_grant_identity_s07",
        "resource_lease",
        [
            "id",
            "tenant_id",
            "project_id",
            "operation_id",
            "binding_revision_id",
            "resource_registration_id",
            "capacity_unit_id",
            "resource_kind",
            "fencing_token",
        ],
        schema="platform",
    )
    op.create_check_constraint(
        "ck_resource_lease_v2_shape_s07",
        "resource_lease",
        """
        (lease_schema_version IS NULL AND project_id IS NULL
          AND resource_registration_id IS NULL AND capacity_unit_id IS NULL
          AND operation_id IS NULL AND binding_revision_id IS NULL
          AND lease_key IS NULL AND lease_attempt IS NULL
          AND lease_state IS NULL AND acquired_at IS NULL
          AND heartbeat_at IS NULL AND revoked_at IS NULL
          AND owner_gateway_revision IS NULL AND reconciliation_reason IS NULL)
        OR
        (lease_schema_version = 'collection-resource-lease-v2'
          AND project_id IS NOT NULL AND resource_registration_id IS NOT NULL
          AND capacity_unit_id IS NOT NULL AND operation_id IS NOT NULL
          AND binding_revision_id IS NOT NULL AND lease_key IS NOT NULL
          AND lease_attempt IS NOT NULL AND lease_state IS NOT NULL
          AND acquired_at IS NOT NULL AND heartbeat_at IS NOT NULL
          AND owner_gateway_revision IS NOT NULL
          AND btrim(lease_key) <> ''
          AND lease_attempt > 0 AND lease_state IN
            ('active','released','expired','preempted','quarantined')
          AND btrim(owner_gateway_revision) <> '' AND fencing_token > 0
          AND acquired_at <= heartbeat_at AND heartbeat_at < expires_at
          AND (lease_state <> 'released' OR released_at IS NOT NULL)
          AND (lease_state NOT IN ('preempted','quarantined') OR revoked_at IS NOT NULL))
        """,
        schema="platform",
    )
    op.create_index(
        "uq_resource_lease_key_s07",
        "resource_lease",
        ["tenant_id", "project_id", "lease_key", "lease_attempt"],
        unique=True,
        schema="platform",
        postgresql_where=sa.text("lease_schema_version = 'collection-resource-lease-v2'"),
    )
    op.create_index(
        "uq_resource_lease_active_capacity_s07",
        "resource_lease",
        ["tenant_id", "project_id", "capacity_unit_id"],
        unique=True,
        schema="platform",
        postgresql_where=sa.text(
            "lease_schema_version = 'collection-resource-lease-v2' AND lease_state = 'active'"
        ),
    )
    op.create_index(
        "uq_resource_lease_capacity_fence_s07",
        "resource_lease",
        ["tenant_id", "project_id", "capacity_unit_id", "fencing_token"],
        unique=True,
        schema="platform",
        postgresql_where=sa.text("lease_schema_version = 'collection-resource-lease-v2'"),
    )
    op.execute(
        """
        CREATE FUNCTION platform.guard_resource_lease_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE current_fence bigint;
        DECLARE current_capacity_state text;
        DECLARE binding_state text;
        DECLARE binding_activated_at timestamptz;
        DECLARE binding_effective_from timestamptz;
        DECLARE binding_expires_at timestamptz;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.lease_schema_version = 'collection-resource-lease-v2' THEN
              RAISE EXCEPTION 'formal resource lease cannot be deleted';
            END IF;
            RETURN OLD;
          END IF;
          IF NEW.lease_schema_version IS DISTINCT FROM 'collection-resource-lease-v2'
             AND OLD.lease_schema_version IS DISTINCT FROM
                 'collection-resource-lease-v2' THEN
            RETURN NEW;
          END IF;
          IF TG_OP = 'INSERT' OR OLD.lease_schema_version IS NULL THEN
            SELECT current_fencing_token, capacity_state
              INTO current_fence, current_capacity_state
              FROM platform.collection_resource_capacity_unit
             WHERE id=NEW.capacity_unit_id AND tenant_id=NEW.tenant_id
               AND project_id=NEW.project_id
             FOR UPDATE;
            IF current_fence IS DISTINCT FROM NEW.fencing_token OR
               current_capacity_state IS DISTINCT FROM 'leased' THEN
              RAISE EXCEPTION 'resource lease does not own current fencing token';
            END IF;
            SELECT lifecycle_state,activated_at,effective_from,expires_at
              INTO binding_state,binding_activated_at,binding_effective_from,
                   binding_expires_at
              FROM platform.collection_binding_revision_v2
             WHERE id=NEW.binding_revision_id AND tenant_id=NEW.tenant_id
               AND project_id=NEW.project_id;
            IF binding_state IS DISTINCT FROM 'active' OR
               binding_activated_at IS NULL OR
               binding_activated_at > NEW.acquired_at OR
               binding_effective_from > NEW.acquired_at OR
               binding_expires_at < NEW.expires_at THEN
              RAISE EXCEPTION
                'resource lease requires active binding for the full lease window';
            END IF;
            RETURN NEW;
          END IF;
          IF ROW(NEW.pub_id, NEW.tenant_id, NEW.project_id,
                 NEW.lease_schema_version, NEW.resource_registration_id,
                 NEW.capacity_unit_id, NEW.operation_id, NEW.binding_revision_id,
                 NEW.lease_key, NEW.lease_attempt, NEW.resource_kind,
                 NEW.resource_pub_id, NEW.holder, NEW.fencing_token,
                 NEW.acquired_at, NEW.owner_gateway_revision)
             IS DISTINCT FROM
             ROW(OLD.pub_id, OLD.tenant_id, OLD.project_id,
                 OLD.lease_schema_version, OLD.resource_registration_id,
                 OLD.capacity_unit_id, OLD.operation_id, OLD.binding_revision_id,
                 OLD.lease_key, OLD.lease_attempt, OLD.resource_kind,
                 OLD.resource_pub_id, OLD.holder, OLD.fencing_token,
                 OLD.acquired_at, OLD.owner_gateway_revision) THEN
            RAISE EXCEPTION 'formal resource lease identity is immutable';
          END IF;
          IF OLD.lease_state='active' AND NEW.lease_state NOT IN
               ('active','released','expired','preempted','quarantined') OR
             OLD.lease_state IN ('released','expired','preempted','quarantined')
               AND NEW.lease_state <> OLD.lease_state THEN
            RAISE EXCEPTION 'invalid irreversible resource lease transition';
          END IF;
          IF OLD.lease_state = 'active' AND NEW.lease_state = 'active' THEN
            SELECT lifecycle_state,activated_at,effective_from,expires_at
              INTO binding_state,binding_activated_at,binding_effective_from,
                   binding_expires_at
              FROM platform.collection_binding_revision_v2
             WHERE id=NEW.binding_revision_id AND tenant_id=NEW.tenant_id
               AND project_id=NEW.project_id;
            IF binding_state IS DISTINCT FROM 'active' OR
               binding_activated_at IS NULL OR
               binding_activated_at > NEW.acquired_at OR
               binding_effective_from > NEW.acquired_at OR
               binding_expires_at < NEW.expires_at THEN
              RAISE EXCEPTION
                'active resource lease exceeds its active binding window';
            END IF;
            SELECT current_fencing_token, capacity_state
              INTO current_fence, current_capacity_state
              FROM platform.collection_resource_capacity_unit
             WHERE id=NEW.capacity_unit_id AND tenant_id=NEW.tenant_id
               AND project_id=NEW.project_id
             FOR SHARE;
            IF current_fence IS DISTINCT FROM NEW.fencing_token OR
               current_capacity_state IS DISTINCT FROM 'leased' OR
               NEW.heartbeat_at < OLD.heartbeat_at THEN
              RETURN OLD;
            END IF;
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER resource_lease_v2_guard_trg
        BEFORE INSERT OR UPDATE OR DELETE ON platform.resource_lease
        FOR EACH ROW EXECUTE FUNCTION platform.guard_resource_lease_v2()
        """
    )


def _create_quota_runtime_tables() -> None:
    op.create_table(
        "collection_quota_bucket",
        *_identity_columns(),
        sa.Column("registry_revision_id", sa.Uuid(), nullable=False),
        sa.Column("quota_scope_policy_id", sa.Uuid(), nullable=False),
        sa.Column("scope_policy_key", sa.String(length=1500), nullable=False),
        sa.Column("scope_kind", sa.String(length=40), nullable=False),
        sa.Column("scope_subject_id", sa.String(length=255), nullable=False),
        sa.Column("policy_revision", sa.String(length=128), nullable=False),
        sa.Column("bucket_key", sa.String(length=2000), nullable=False),
        sa.Column("bucket_hash", sa.String(length=64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("limit_units", sa.Integer(), nullable=False),
        sa.Column("reserved_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "settled_consumed_units",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "settled_unknown_units",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("bucket_state", sa.String(length=30), nullable=False),
        sa.Column("fence_version", sa.BigInteger(), nullable=False, server_default="1"),
        *_scope_constraints("collection_quota_bucket"),
        sa.ForeignKeyConstraint(
            ["registry_revision_id", "tenant_id", "project_id"],
            [
                "platform.collection_quota_registry_revision.id",
                "platform.collection_quota_registry_revision.tenant_id",
                "platform.collection_quota_registry_revision.project_id",
            ],
            name="fk_quota_bucket_registry_scope",
        ),
        sa.ForeignKeyConstraint(
            [
                "quota_scope_policy_id",
                "tenant_id",
                "project_id",
                "registry_revision_id",
                "scope_policy_key",
                "scope_kind",
                "scope_subject_id",
                "policy_revision",
            ],
            [
                "platform.collection_quota_scope_policy.id",
                "platform.collection_quota_scope_policy.tenant_id",
                "platform.collection_quota_scope_policy.project_id",
                "platform.collection_quota_scope_policy.registry_revision_id",
                "platform.collection_quota_scope_policy.scope_policy_key",
                "platform.collection_quota_scope_policy.scope_kind",
                "platform.collection_quota_scope_policy.scope_subject_id",
                "platform.collection_quota_scope_policy.policy_revision",
            ],
            name="fk_quota_bucket_scope_exact",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "quota_scope_policy_id",
            "bucket_key",
            name="uq_quota_bucket_window",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "bucket_hash",
            name="uq_quota_bucket_hash",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "quota_scope_policy_id",
            name="uq_quota_bucket_effect_identity",
        ),
        sa.CheckConstraint(
            _SHA256.format(column="bucket_hash"),
            name="ck_quota_bucket_hash",
        ),
        sa.CheckConstraint(
            "window_start < window_end AND limit_units > 0 "
            "AND reserved_units >= 0 AND settled_consumed_units >= 0 "
            "AND settled_unknown_units >= 0 AND fence_version > 0 "
            "AND reserved_units + settled_consumed_units "
            "+ settled_unknown_units <= limit_units",
            name="ck_quota_bucket_capacity",
        ),
        sa.CheckConstraint(
            "bucket_state IN ('open','reconciling','closed')",
            name="ck_quota_bucket_state",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_quota_bucket_lock_order",
        "collection_quota_bucket",
        ["tenant_id", "project_id", "scope_kind", "bucket_key"],
        schema="platform",
    )

    op.create_table(
        "collection_quota_reservation",
        *_identity_columns(),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("binding_revision_id", sa.Uuid(), nullable=False),
        sa.Column("quota_registry_id", sa.Uuid(), nullable=False),
        sa.Column("reservation_key", sa.String(length=1000), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("reservation_state", sa.String(length=40), nullable=False),
        sa.Column("requested_units", sa.Integer(), nullable=False),
        sa.Column("expected_effect_count", sa.Integer(), nullable=False),
        sa.Column("effect_set_hash", sa.String(length=64), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True)),
        sa.Column("reconcile_after", sa.DateTime(timezone=True)),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.Column("state_reason", sa.String(length=128), nullable=False),
        *_scope_constraints("collection_quota_reservation"),
        sa.ForeignKeyConstraint(
            ["operation_id", "tenant_id", "project_id"],
            [
                "platform.collection_submission_operation.id",
                "platform.collection_submission_operation.tenant_id",
                "platform.collection_submission_operation.project_id",
            ],
            name="fk_quota_reservation_operation_scope",
        ),
        sa.ForeignKeyConstraint(
            ["binding_revision_id", "tenant_id", "project_id", "quota_registry_id"],
            [
                "platform.collection_binding_revision_v2.id",
                "platform.collection_binding_revision_v2.tenant_id",
                "platform.collection_binding_revision_v2.project_id",
                "platform.collection_binding_revision_v2.quota_registry_id",
            ],
            name="fk_quota_reservation_binding_registry",
        ),
        sa.UniqueConstraint(
            "operation_id",
            "tenant_id",
            "project_id",
            name="uq_quota_reservation_operation",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "reservation_key",
            name="uq_quota_reservation_key",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "idempotency_key",
            name="uq_quota_reservation_idempotency",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "operation_id",
            "binding_revision_id",
            "quota_registry_id",
            name="uq_quota_reservation_grant_identity",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "operation_id",
            name="uq_quota_reservation_operation_identity",
        ),
        sa.CheckConstraint(
            "reservation_state IN "
            "('preparing','reserved','reconciling','settled_consumed',"
            "'settled_unknown','released')",
            name="ck_quota_reservation_state",
        ),
        sa.CheckConstraint(
            "requested_units > 0 AND expected_effect_count > 0",
            name="ck_quota_reservation_positive",
        ),
        sa.CheckConstraint(
            _SHA256.format(column="effect_set_hash"),
            name="ck_quota_reservation_effect_hash",
        ),
        sa.CheckConstraint(
            "(reservation_state='preparing' AND reserved_at IS NULL "
            "AND finalized_at IS NULL) OR "
            "(reservation_state IN ('reserved','reconciling') "
            "AND reserved_at IS NOT NULL AND finalized_at IS NULL) OR "
            "(reservation_state IN "
            "('settled_consumed','settled_unknown','released') "
            "AND reserved_at IS NOT NULL AND finalized_at IS NOT NULL)",
            name="ck_quota_reservation_timestamps",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_quota_reservation_reconcile",
        "collection_quota_reservation",
        ["tenant_id", "project_id", "reservation_state", "reconcile_after"],
        schema="platform",
    )

    op.create_table(
        "collection_quota_reservation_effect",
        *_identity_columns(),
        sa.Column("reservation_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("quota_bucket_id", sa.Uuid(), nullable=False),
        sa.Column("quota_scope_policy_id", sa.Uuid(), nullable=False),
        sa.Column("effect_key", sa.String(length=1000), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("effect_state", sa.String(length=40), nullable=False),
        sa.Column("state_reason", sa.String(length=128), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        *_scope_constraints("collection_quota_reservation_effect"),
        sa.ForeignKeyConstraint(
            ["reservation_id", "tenant_id", "project_id", "operation_id"],
            [
                "platform.collection_quota_reservation.id",
                "platform.collection_quota_reservation.tenant_id",
                "platform.collection_quota_reservation.project_id",
                "platform.collection_quota_reservation.operation_id",
            ],
            name="fk_quota_effect_reservation_operation",
        ),
        sa.ForeignKeyConstraint(
            ["quota_bucket_id", "tenant_id", "project_id", "quota_scope_policy_id"],
            [
                "platform.collection_quota_bucket.id",
                "platform.collection_quota_bucket.tenant_id",
                "platform.collection_quota_bucket.project_id",
                "platform.collection_quota_bucket.quota_scope_policy_id",
            ],
            name="fk_quota_effect_bucket_scope",
        ),
        sa.UniqueConstraint(
            "operation_id",
            "tenant_id",
            "project_id",
            "quota_bucket_id",
            name="uq_quota_effect_operation_bucket",
        ),
        sa.UniqueConstraint(
            "reservation_id",
            "tenant_id",
            "project_id",
            "effect_key",
            name="uq_quota_effect_key",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "reservation_id",
            "operation_id",
            "quota_bucket_id",
            "quota_scope_policy_id",
            name="uq_quota_effect_ledger_identity",
        ),
        sa.CheckConstraint("units > 0", name="ck_quota_effect_units"),
        sa.CheckConstraint(
            "effect_state IN ('reserved','settled_consumed','settled_unknown','released')",
            name="ck_quota_effect_state",
        ),
        sa.CheckConstraint(
            "(effect_state='reserved' AND settled_at IS NULL "
            "AND released_at IS NULL) OR "
            "(effect_state IN ('settled_consumed','settled_unknown') "
            "AND settled_at IS NOT NULL AND released_at IS NULL) OR "
            "(effect_state='released' AND settled_at IS NULL "
            "AND released_at IS NOT NULL)",
            name="ck_quota_effect_timestamps",
        ),
        schema="platform",
    )

    op.create_table(
        "collection_quota_ledger_event",
        *_identity_columns(),
        sa.Column("reservation_effect_id", sa.Uuid(), nullable=False),
        sa.Column("reservation_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("quota_bucket_id", sa.Uuid(), nullable=False),
        sa.Column("quota_scope_policy_id", sa.Uuid(), nullable=False),
        sa.Column("event_key", sa.String(length=1000), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("effect_kind", sa.String(length=40), nullable=False),
        sa.Column("from_state", sa.String(length=40)),
        sa.Column("to_state", sa.String(length=40), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("actor_pub_id", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_scope_constraints("collection_quota_ledger_event"),
        sa.ForeignKeyConstraint(
            [
                "reservation_effect_id",
                "tenant_id",
                "project_id",
                "reservation_id",
                "operation_id",
                "quota_bucket_id",
                "quota_scope_policy_id",
            ],
            [
                "platform.collection_quota_reservation_effect.id",
                "platform.collection_quota_reservation_effect.tenant_id",
                "platform.collection_quota_reservation_effect.project_id",
                "platform.collection_quota_reservation_effect.reservation_id",
                "platform.collection_quota_reservation_effect.operation_id",
                "platform.collection_quota_reservation_effect.quota_bucket_id",
                "platform.collection_quota_reservation_effect.quota_scope_policy_id",
            ],
            name="fk_quota_ledger_effect_exact",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "event_key",
            name="uq_quota_ledger_event_key",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "idempotency_key",
            name="uq_quota_ledger_idempotency",
        ),
        sa.CheckConstraint("units > 0", name="ck_quota_ledger_units"),
        sa.CheckConstraint(
            "effect_kind IN ('reserve','settle_consumed','settle_unknown','release')",
            name="ck_quota_ledger_kind",
        ),
        sa.CheckConstraint(
            "(effect_kind='reserve' AND from_state IS NULL "
            "AND to_state='reserved') OR "
            "(effect_kind='settle_consumed' AND from_state='reserved' "
            "AND to_state='settled_consumed') OR "
            "(effect_kind='settle_unknown' AND from_state='reserved' "
            "AND to_state='settled_unknown') OR "
            "(effect_kind='release' AND from_state='reserved' "
            "AND to_state='released')",
            name="ck_quota_ledger_transition",
        ),
        schema="platform",
    )

    _create_quota_runtime_guards()


def _create_quota_runtime_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION platform.guard_quota_bucket_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE policy_limit integer;
        DECLARE policy_window_unit text;
        DECLARE policy_window_size integer;
        DECLARE policy_window_timezone text;
        DECLARE expected_bucket_key text;
        DECLARE local_start timestamp;
        DECLARE local_end timestamp;
        DECLARE local_start_date date;
        DECLARE local_end_date date;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'quota buckets are durable and cannot be deleted';
          END IF;
          SELECT p.limit_units,p.window_unit,p.window_size,p.window_timezone
            INTO policy_limit,policy_window_unit,policy_window_size,
                 policy_window_timezone
            FROM platform.collection_quota_scope_policy p
           WHERE p.id=NEW.quota_scope_policy_id
             AND p.tenant_id=NEW.tenant_id AND p.project_id=NEW.project_id
             AND p.registry_revision_id=NEW.registry_revision_id
             AND p.scope_policy_key=NEW.scope_policy_key
             AND p.scope_kind=NEW.scope_kind
             AND p.scope_subject_id=NEW.scope_subject_id
             AND p.policy_revision=NEW.policy_revision;
          IF policy_limit IS NULL OR NEW.limit_units <> policy_limit OR
             NEW.bucket_hash <>
               encode(public.digest(NEW.bucket_key,'sha256'),'hex') THEN
            RAISE EXCEPTION 'quota bucket does not exactly match its policy';
          END IF;
          expected_bucket_key :=
            'collection-quota-bucket-v1|tenant_id=' || NEW.tenant_id::text ||
            '|project_id=' || NEW.project_id::text ||
            '|scope=' || NEW.scope_policy_key ||
            '|window_start=' || to_char(
              NEW.window_start AT TIME ZONE 'UTC',
              'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') ||
            '|window_end=' || to_char(
              NEW.window_end AT TIME ZONE 'UTC',
              'YYYY-MM-DD"T"HH24:MI:SS.US"Z"');
          IF NEW.bucket_key <> expected_bucket_key THEN
            RAISE EXCEPTION 'quota bucket canonical window key is invalid';
          END IF;
          IF policy_window_unit <> 'provider_custom' THEN
            local_start := NEW.window_start AT TIME ZONE policy_window_timezone;
            local_end := NEW.window_end AT TIME ZONE policy_window_timezone;
            local_start_date := local_start::date;
            local_end_date := local_end::date;
            IF local_start::time <> time '00:00:00' OR
               local_end::time <> time '00:00:00' OR
               (policy_window_unit='day' AND (
                 local_end_date-local_start_date <> policy_window_size OR
                 (local_start_date-date '1970-01-01') % policy_window_size <> 0
               )) OR (policy_window_unit='week' AND (
                 local_end_date-local_start_date <> 7*policy_window_size OR
                 extract(isodow FROM local_start_date) <> 1 OR
                 (local_start_date-date '1970-01-05') %
                   (7*policy_window_size) <> 0
               )) OR (policy_window_unit='year' AND (
                 extract(month FROM local_start_date) <> 1 OR
                 extract(day FROM local_start_date) <> 1 OR
                 extract(month FROM local_end_date) <> 1 OR
                 extract(day FROM local_end_date) <> 1 OR
                 extract(year FROM local_end_date)-
                   extract(year FROM local_start_date) <> policy_window_size OR
                 (extract(year FROM local_start_date)::integer-1) %
                   policy_window_size <> 0
               )) THEN
              RAISE EXCEPTION 'quota bucket window violates policy boundary';
            END IF;
          END IF;
          IF TG_OP = 'UPDATE' AND ROW(NEW.pub_id, NEW.tenant_id, NEW.project_id,
                 NEW.registry_revision_id, NEW.quota_scope_policy_id,
                 NEW.scope_policy_key, NEW.scope_kind, NEW.scope_subject_id,
                 NEW.policy_revision, NEW.bucket_key, NEW.bucket_hash,
                 NEW.window_start, NEW.window_end, NEW.limit_units)
             IS DISTINCT FROM
             ROW(OLD.pub_id, OLD.tenant_id, OLD.project_id,
                 OLD.registry_revision_id, OLD.quota_scope_policy_id,
                 OLD.scope_policy_key, OLD.scope_kind, OLD.scope_subject_id,
                 OLD.policy_revision, OLD.bucket_key, OLD.bucket_hash,
                 OLD.window_start, OLD.window_end, OLD.limit_units) THEN
            RAISE EXCEPTION 'quota bucket identity and limit are immutable';
          END IF;
          IF TG_OP = 'UPDATE' AND ROW(NEW.reserved_units, NEW.settled_consumed_units,
                 NEW.settled_unknown_units, NEW.bucket_state)
             IS DISTINCT FROM
             ROW(OLD.reserved_units, OLD.settled_consumed_units,
                 OLD.settled_unknown_units, OLD.bucket_state) THEN
            IF NEW.fence_version <> OLD.fence_version + 1 THEN
              RAISE EXCEPTION 'quota bucket update must advance fence once';
            END IF;
          ELSIF TG_OP = 'UPDATE' AND NEW.fence_version <> OLD.fence_version THEN
            RAISE EXCEPTION 'quota bucket fence changed without an effect';
          END IF;
          IF TG_OP = 'UPDATE' AND OLD.bucket_state='closed' AND
             NEW.bucket_state <> 'closed' THEN
            RAISE EXCEPTION 'closed quota bucket cannot reopen';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.guard_quota_reservation_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE effect_count integer;
        DECLARE operation_state text;
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NEW.reservation_state <> 'preparing' THEN
              RAISE EXCEPTION 'quota reservation must begin preparing';
            END IF;
            RETURN NEW;
          END IF;
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'quota reservations are durable and cannot be deleted';
          END IF;
          IF ROW(NEW.pub_id, NEW.tenant_id, NEW.project_id, NEW.operation_id,
                 NEW.binding_revision_id, NEW.quota_registry_id,
                 NEW.reservation_key, NEW.idempotency_key, NEW.requested_units,
                 NEW.expected_effect_count, NEW.effect_set_hash)
             IS DISTINCT FROM
             ROW(OLD.pub_id, OLD.tenant_id, OLD.project_id, OLD.operation_id,
                 OLD.binding_revision_id, OLD.quota_registry_id,
                 OLD.reservation_key, OLD.idempotency_key, OLD.requested_units,
                 OLD.expected_effect_count, OLD.effect_set_hash) THEN
            RAISE EXCEPTION 'quota reservation identity is immutable';
          END IF;
          IF OLD.reservation_state='preparing' AND
             NEW.reservation_state NOT IN ('preparing','reserved','released') OR
             OLD.reservation_state='reserved' AND NEW.reservation_state NOT IN
               ('reserved','reconciling','settled_consumed',
                'settled_unknown','released') OR
             OLD.reservation_state='reconciling' AND NEW.reservation_state NOT IN
               ('reconciling','settled_consumed','settled_unknown','released') OR
             OLD.reservation_state IN
               ('settled_consumed','settled_unknown','released') AND
             NEW.reservation_state <> OLD.reservation_state THEN
            RAISE EXCEPTION 'invalid irreversible quota reservation transition';
          END IF;
          IF OLD.reservation_state='preparing' AND NEW.reservation_state='reserved' THEN
            SELECT count(*) INTO effect_count
              FROM platform.collection_quota_reservation_effect e
             WHERE e.reservation_id=NEW.id AND e.tenant_id=NEW.tenant_id
               AND e.project_id=NEW.project_id AND e.effect_state='reserved';
            IF effect_count <> NEW.expected_effect_count THEN
              RAISE EXCEPTION 'quota reservation effect set is incomplete';
            END IF;
          END IF;
          IF NEW.reservation_state IN
               ('settled_consumed','settled_unknown','released') AND
             NEW.reservation_state <> OLD.reservation_state THEN
            SELECT send_state INTO operation_state
              FROM platform.collection_submission_operation
             WHERE id=NEW.operation_id AND tenant_id=NEW.tenant_id
               AND project_id=NEW.project_id;
            IF NEW.reservation_state='settled_consumed' AND
               operation_state <> 'CONFIRMED_SENT' OR
               NEW.reservation_state='settled_unknown' AND
               operation_state <> 'SEND_UNKNOWN' OR
               NEW.reservation_state='released' AND
               operation_state IN ('SENDING','CONFIRMED_SENT','SEND_UNKNOWN') THEN
              RAISE EXCEPTION 'quota settlement contradicts durable send truth';
            END IF;
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.guard_quota_effect_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE parent_state text;
        DECLARE operation_state text;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'quota effects are durable and cannot be deleted';
          END IF;
          IF TG_OP = 'INSERT' THEN
            SELECT reservation_state INTO parent_state
              FROM platform.collection_quota_reservation
             WHERE id=NEW.reservation_id AND tenant_id=NEW.tenant_id
               AND project_id=NEW.project_id;
            IF parent_state <> 'preparing' OR NEW.effect_state <> 'reserved' THEN
              RAISE EXCEPTION 'quota effects must assemble under preparing reservation';
            END IF;
            RETURN NEW;
          END IF;
          IF ROW(NEW.pub_id, NEW.tenant_id, NEW.project_id,
                 NEW.reservation_id, NEW.operation_id, NEW.quota_bucket_id,
                 NEW.quota_scope_policy_id, NEW.effect_key, NEW.units,
                 NEW.reserved_at)
             IS DISTINCT FROM
             ROW(OLD.pub_id, OLD.tenant_id, OLD.project_id,
                 OLD.reservation_id, OLD.operation_id, OLD.quota_bucket_id,
                 OLD.quota_scope_policy_id, OLD.effect_key, OLD.units,
                 OLD.reserved_at) THEN
            RAISE EXCEPTION 'quota effect identity is immutable';
          END IF;
          IF OLD.effect_state='reserved' AND NEW.effect_state NOT IN
               ('reserved','settled_consumed','settled_unknown','released') OR
             OLD.effect_state IN
               ('settled_consumed','settled_unknown','released') AND
             NEW.effect_state <> OLD.effect_state THEN
            RAISE EXCEPTION 'invalid irreversible quota effect transition';
          END IF;
          IF NEW.effect_state <> OLD.effect_state THEN
            SELECT send_state INTO operation_state
              FROM platform.collection_submission_operation
             WHERE id=NEW.operation_id AND tenant_id=NEW.tenant_id
               AND project_id=NEW.project_id;
            IF NEW.effect_state='settled_consumed' AND
               operation_state <> 'CONFIRMED_SENT' OR
               NEW.effect_state='settled_unknown' AND
               operation_state <> 'SEND_UNKNOWN' OR
               NEW.effect_state='released' AND
               operation_state IN ('SENDING','CONFIRMED_SENT','SEND_UNKNOWN') THEN
              RAISE EXCEPTION 'quota effect contradicts durable send truth';
            END IF;
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.guard_quota_ledger_append_only_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          RAISE EXCEPTION 'quota ledger events are append-only';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER quota_bucket_v2_guard_trg
        BEFORE INSERT OR UPDATE OR DELETE ON platform.collection_quota_bucket
        FOR EACH ROW EXECUTE FUNCTION platform.guard_quota_bucket_v2()
        """
    )
    op.execute(
        """
        CREATE TRIGGER quota_reservation_v2_guard_trg
        BEFORE INSERT OR UPDATE OR DELETE
        ON platform.collection_quota_reservation
        FOR EACH ROW EXECUTE FUNCTION platform.guard_quota_reservation_v2()
        """
    )
    op.execute(
        """
        CREATE TRIGGER quota_effect_v2_guard_trg
        BEFORE INSERT OR UPDATE OR DELETE
        ON platform.collection_quota_reservation_effect
        FOR EACH ROW EXECUTE FUNCTION platform.guard_quota_effect_v2()
        """
    )
    op.execute(
        """
        CREATE TRIGGER quota_ledger_append_only_v2_trg
        BEFORE UPDATE OR DELETE ON platform.collection_quota_ledger_event
        FOR EACH ROW EXECUTE FUNCTION platform.guard_quota_ledger_append_only_v2()
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.assert_collection_quota_bucket_v2(
          p_tenant_id uuid,p_project_id uuid,p_bucket_id uuid
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE actual_reserved bigint;
        DECLARE actual_consumed bigint;
        DECLARE actual_unknown bigint;
        DECLARE projected_reserved bigint;
        DECLARE projected_consumed bigint;
        DECLARE projected_unknown bigint;
        BEGIN
          SELECT reserved_units,settled_consumed_units,settled_unknown_units
            INTO projected_reserved,projected_consumed,projected_unknown
            FROM platform.collection_quota_bucket
           WHERE id=p_bucket_id AND tenant_id=p_tenant_id
             AND project_id=p_project_id;
          IF NOT FOUND THEN RETURN; END IF;
          SELECT
            COALESCE(sum(units) FILTER (WHERE effect_state='reserved'),0),
            COALESCE(sum(units) FILTER
              (WHERE effect_state='settled_consumed'),0),
            COALESCE(sum(units) FILTER
              (WHERE effect_state='settled_unknown'),0)
            INTO actual_reserved,actual_consumed,actual_unknown
            FROM platform.collection_quota_reservation_effect
           WHERE quota_bucket_id=p_bucket_id AND tenant_id=p_tenant_id
             AND project_id=p_project_id;
          IF ROW(projected_reserved,projected_consumed,projected_unknown)
             IS DISTINCT FROM ROW(actual_reserved,actual_consumed,actual_unknown) THEN
            RAISE EXCEPTION 'quota bucket projection violates ledger conservation';
          END IF;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.assert_collection_quota_reservation_v2(
          p_tenant_id uuid,p_project_id uuid,p_reservation_id uuid
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE reservation_row record;
        DECLARE mapping_count integer;
        DECLARE effect_count integer;
        DECLARE invalid_effect_count integer;
        DECLARE calculated_hash text;
        DECLARE invalid_state_count integer;
        DECLARE ledger_count integer;
        DECLARE invalid_ledger_count integer;
        DECLARE expected_ledger_count integer;
        DECLARE expected_terminal_kind text;
        BEGIN
          SELECT * INTO reservation_row
            FROM platform.collection_quota_reservation
           WHERE id=p_reservation_id AND tenant_id=p_tenant_id
             AND project_id=p_project_id;
          IF NOT FOUND THEN RETURN; END IF;
          IF reservation_row.reservation_state='preparing' THEN
            RAISE EXCEPTION 'preparing quota reservation cannot survive commit';
          END IF;

          SELECT count(*) INTO mapping_count
            FROM platform.collection_binding_quota_scope m
           WHERE m.binding_revision_id=reservation_row.binding_revision_id
             AND m.quota_registry_id=reservation_row.quota_registry_id
             AND m.tenant_id=p_tenant_id AND m.project_id=p_project_id;
          SELECT count(*),count(*) FILTER (WHERE m.id IS NULL OR
                   e.units <> m.quota_units*reservation_row.requested_units)
            INTO effect_count,invalid_effect_count
            FROM platform.collection_quota_reservation_effect e
            LEFT JOIN platform.collection_binding_quota_scope m
              ON m.binding_revision_id=reservation_row.binding_revision_id
             AND m.quota_registry_id=reservation_row.quota_registry_id
             AND m.quota_scope_policy_id=e.quota_scope_policy_id
             AND m.tenant_id=e.tenant_id AND m.project_id=e.project_id
           WHERE e.reservation_id=p_reservation_id
             AND e.tenant_id=p_tenant_id AND e.project_id=p_project_id;
          IF mapping_count = 0 OR effect_count <> mapping_count OR
             effect_count <> reservation_row.expected_effect_count OR
             invalid_effect_count <> 0 THEN
            RAISE EXCEPTION 'quota reservation does not cover exact binding scopes';
          END IF;

          SELECT encode(public.digest(
            'collection-quota-reservation-set-v1' || E'\n' ||
            'quota-scope-lock-order-v1' || E'\n' ||
            'requested_units=' || reservation_row.requested_units::text || E'\n' ||
            string_agg(
              b.bucket_hash || '|scope_policy_id=' ||
              e.quota_scope_policy_id::text || '|units=' || e.units::text,
              E'\n' ORDER BY p.lock_order_ordinal,p.scope_policy_key
            ),'sha256'),'hex')
            INTO calculated_hash
            FROM platform.collection_quota_reservation_effect e
            JOIN platform.collection_quota_bucket b
              ON b.id=e.quota_bucket_id AND b.tenant_id=e.tenant_id
             AND b.project_id=e.project_id
            JOIN platform.collection_quota_scope_policy p
              ON p.id=e.quota_scope_policy_id AND p.tenant_id=e.tenant_id
             AND p.project_id=e.project_id
           WHERE e.reservation_id=p_reservation_id
             AND e.tenant_id=p_tenant_id AND e.project_id=p_project_id;
          IF calculated_hash IS DISTINCT FROM reservation_row.effect_set_hash THEN
            RAISE EXCEPTION 'quota reservation effect-set hash is invalid';
          END IF;

          SELECT count(*) FILTER (WHERE effect_state <> CASE
              WHEN reservation_row.reservation_state IN ('reserved','reconciling')
                THEN 'reserved'
              ELSE reservation_row.reservation_state END)
            INTO invalid_state_count
            FROM platform.collection_quota_reservation_effect
           WHERE reservation_id=p_reservation_id AND tenant_id=p_tenant_id
             AND project_id=p_project_id;
          IF invalid_state_count <> 0 THEN
            RAISE EXCEPTION 'quota reservation and effects are not homomorphic';
          END IF;

          expected_terminal_kind := CASE reservation_row.reservation_state
            WHEN 'settled_consumed' THEN 'settle_consumed'
            WHEN 'settled_unknown' THEN 'settle_unknown'
            WHEN 'released' THEN 'release'
            ELSE NULL END;
          expected_ledger_count := effect_count *
            CASE WHEN expected_terminal_kind IS NULL THEN 1 ELSE 2 END;
          SELECT count(*),count(*) FILTER (WHERE
              (l.effect_kind='reserve' AND
               (l.from_state IS NOT NULL OR l.to_state<>'reserved' OR
                l.units<>e.units)) OR
              (l.effect_kind<>'reserve' AND
               (expected_terminal_kind IS NULL OR
                l.effect_kind<>expected_terminal_kind OR
                l.from_state<>'reserved' OR
                l.to_state<>reservation_row.reservation_state OR
                l.units<>e.units)))
            INTO ledger_count,invalid_ledger_count
            FROM platform.collection_quota_ledger_event l
            JOIN platform.collection_quota_reservation_effect e
              ON e.id=l.reservation_effect_id AND e.tenant_id=l.tenant_id
             AND e.project_id=l.project_id
           WHERE l.reservation_id=p_reservation_id
             AND l.tenant_id=p_tenant_id AND l.project_id=p_project_id;
          IF ledger_count <> expected_ledger_count OR
             invalid_ledger_count <> 0 OR EXISTS (
               SELECT 1
                 FROM platform.collection_quota_reservation_effect e
                WHERE e.reservation_id=p_reservation_id
                  AND e.tenant_id=p_tenant_id AND e.project_id=p_project_id
                  AND ((SELECT count(*)
                          FROM platform.collection_quota_ledger_event l
                         WHERE l.reservation_effect_id=e.id
                           AND l.tenant_id=e.tenant_id
                           AND l.project_id=e.project_id
                           AND l.effect_kind='reserve') <> 1 OR
                       (expected_terminal_kind IS NOT NULL AND
                        (SELECT count(*)
                           FROM platform.collection_quota_ledger_event l
                          WHERE l.reservation_effect_id=e.id
                            AND l.tenant_id=e.tenant_id
                            AND l.project_id=e.project_id
                            AND l.effect_kind=expected_terminal_kind) <> 1))
             ) THEN
            RAISE EXCEPTION 'quota ledger is incomplete or forged';
          END IF;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.validate_collection_quota_conservation_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, platform
        AS $$
        DECLARE v_reservation_id uuid;
        DECLARE v_bucket_id uuid;
        BEGIN
          IF TG_TABLE_NAME='collection_quota_bucket' THEN
            PERFORM platform.assert_collection_quota_bucket_v2(
              NEW.tenant_id,NEW.project_id,NEW.id);
            FOR v_reservation_id IN
              SELECT DISTINCT reservation_id
                FROM platform.collection_quota_reservation_effect
               WHERE quota_bucket_id=NEW.id AND tenant_id=NEW.tenant_id
                 AND project_id=NEW.project_id
            LOOP
              PERFORM platform.assert_collection_quota_reservation_v2(
                NEW.tenant_id,NEW.project_id,v_reservation_id);
            END LOOP;
            RETURN NULL;
          END IF;
          IF TG_TABLE_NAME='collection_quota_reservation' THEN
            v_reservation_id := NEW.id;
          ELSE
            v_reservation_id := NEW.reservation_id;
          END IF;
          PERFORM platform.assert_collection_quota_reservation_v2(
            NEW.tenant_id,NEW.project_id,v_reservation_id);
          FOR v_bucket_id IN
            SELECT DISTINCT quota_bucket_id
              FROM platform.collection_quota_reservation_effect
             WHERE reservation_id=v_reservation_id AND tenant_id=NEW.tenant_id
               AND project_id=NEW.project_id
          LOOP
            PERFORM platform.assert_collection_quota_bucket_v2(
              NEW.tenant_id,NEW.project_id,v_bucket_id);
          END LOOP;
          RETURN NULL;
        END
        $$
        """
    )
    for table in (
        "collection_quota_bucket",
        "collection_quota_reservation",
        "collection_quota_reservation_effect",
    ):
        op.execute(
            f"""
            CREATE CONSTRAINT TRIGGER {table}_conservation_trg
            AFTER INSERT OR UPDATE ON platform.{table}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION platform.validate_collection_quota_conservation_v2()
            """
        )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER collection_quota_ledger_event_conservation_trg
        AFTER INSERT ON platform.collection_quota_ledger_event
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION platform.validate_collection_quota_conservation_v2()
        """
    )


def _create_execution_grant_tables() -> None:
    op.create_table(
        "collection_execution_grant_v2",
        *_identity_columns(),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("grant_key", sa.String(length=1000), nullable=False),
        sa.Column("grant_revision", sa.Integer(), nullable=False),
        sa.Column("grant_state", sa.String(length=30), nullable=False),
        sa.Column("config_revision_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_target_id", sa.Uuid(), nullable=False),
        sa.Column("sampling_leg_id", sa.Uuid(), nullable=False),
        sa.Column("primary_slot_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("binding_revision_id", sa.Uuid(), nullable=False),
        sa.Column("binding_revision", sa.Integer(), nullable=False),
        sa.Column("binding_capability_id", sa.Uuid(), nullable=False),
        sa.Column("capability_revision", sa.String(length=128), nullable=False),
        sa.Column("quota_registry_id", sa.Uuid(), nullable=False),
        sa.Column("quota_reservation_id", sa.Uuid(), nullable=False),
        sa.Column("platform", sa.String(length=128), nullable=False),
        sa.Column("collection_surface", sa.String(length=30), nullable=False),
        sa.Column("product_variant", sa.String(length=128), nullable=False),
        sa.Column("province_code", sa.String(length=6), nullable=False),
        sa.Column("interaction_mode", sa.String(length=128), nullable=False),
        sa.Column("route_policy_revision", sa.String(length=128), nullable=False),
        sa.Column("resource_policy_revision", sa.String(length=128), nullable=False),
        sa.Column("workflow_contract_version", sa.String(length=128), nullable=False),
        sa.Column("adapter_revision", sa.String(length=128), nullable=False),
        sa.Column("gateway_protocol_revision", sa.String(length=128), nullable=False),
        sa.Column("worker_build_id", sa.String(length=128), nullable=False),
        sa.Column("agent_revision", sa.String(length=128)),
        sa.Column("allowed_actions_json", sa.Text(), nullable=False),
        sa.Column("grant_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_by_pub_id", sa.String(length=255), nullable=False),
        sa.Column("issuance_reason", sa.String(length=128), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revocation_reason", sa.String(length=128)),
        *_scope_constraints("collection_execution_grant_v2"),
        sa.ForeignKeyConstraint(
            ["campaign_id", "tenant_id", "project_id", "config_revision_id"],
            [
                "platform.collection_campaign.id",
                "platform.collection_campaign.tenant_id",
                "platform.collection_campaign.project_id",
                "platform.collection_campaign.config_revision_id",
            ],
            name="fk_execution_grant_campaign_config",
        ),
        sa.ForeignKeyConstraint(
            [
                "operation_id",
                "tenant_id",
                "project_id",
                "campaign_id",
                "campaign_target_id",
                "sampling_leg_id",
                "primary_slot_id",
                "platform",
                "collection_surface",
                "product_variant",
                "province_code",
                "interaction_mode",
            ],
            [
                "platform.collection_submission_operation.id",
                "platform.collection_submission_operation.tenant_id",
                "platform.collection_submission_operation.project_id",
                "platform.collection_submission_operation.campaign_id",
                "platform.collection_submission_operation.campaign_target_id",
                "platform.collection_submission_operation.sampling_leg_id",
                "platform.collection_submission_operation.primary_slot_id",
                "platform.collection_submission_operation.platform",
                "platform.collection_submission_operation.collection_surface",
                "platform.collection_submission_operation.product_variant",
                "platform.collection_submission_operation.province_code",
                "platform.collection_submission_operation.interaction_mode",
            ],
            name="fk_execution_grant_operation_identity",
        ),
        sa.ForeignKeyConstraint(
            [
                "binding_revision_id",
                "tenant_id",
                "project_id",
                "binding_revision",
                "platform",
                "collection_surface",
                "product_variant",
            ],
            [
                "platform.collection_binding_revision_v2.id",
                "platform.collection_binding_revision_v2.tenant_id",
                "platform.collection_binding_revision_v2.project_id",
                "platform.collection_binding_revision_v2.binding_revision",
                "platform.collection_binding_revision_v2.platform",
                "platform.collection_binding_revision_v2.collection_surface",
                "platform.collection_binding_revision_v2.product_variant",
            ],
            name="fk_execution_grant_binding_identity",
        ),
        sa.ForeignKeyConstraint(
            [
                "binding_capability_id",
                "tenant_id",
                "project_id",
                "binding_revision_id",
                "capability_revision",
                "platform",
                "collection_surface",
                "product_variant",
                "interaction_mode",
            ],
            [
                "platform.collection_binding_capability.id",
                "platform.collection_binding_capability.tenant_id",
                "platform.collection_binding_capability.project_id",
                "platform.collection_binding_capability.binding_revision_id",
                "platform.collection_binding_capability.capability_revision",
                "platform.collection_binding_capability.platform",
                "platform.collection_binding_capability.collection_surface",
                "platform.collection_binding_capability.product_variant",
                "platform.collection_binding_capability.interaction_mode",
            ],
            name="fk_execution_grant_capability_exact",
        ),
        sa.ForeignKeyConstraint(
            [
                "quota_reservation_id",
                "tenant_id",
                "project_id",
                "operation_id",
                "binding_revision_id",
                "quota_registry_id",
            ],
            [
                "platform.collection_quota_reservation.id",
                "platform.collection_quota_reservation.tenant_id",
                "platform.collection_quota_reservation.project_id",
                "platform.collection_quota_reservation.operation_id",
                "platform.collection_quota_reservation.binding_revision_id",
                "platform.collection_quota_reservation.quota_registry_id",
            ],
            name="fk_execution_grant_quota_reservation",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "grant_key",
            "grant_revision",
            name="uq_execution_grant_key_revision",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "grant_hash",
            name="uq_execution_grant_hash",
        ),
        sa.UniqueConstraint(
            "operation_id",
            "tenant_id",
            "project_id",
            "grant_revision",
            name="uq_execution_grant_operation_revision",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "operation_id",
            "binding_revision_id",
            "collection_surface",
            name="uq_execution_grant_resource_identity",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "operation_id",
            "binding_revision_id",
            name="uq_execution_grant_resource_parent",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "collection_surface",
            name="uq_execution_grant_subtype_identity",
        ),
        sa.CheckConstraint(
            "schema_version = 'collection-execution-grant-v1'",
            name="ck_execution_grant_schema",
        ),
        sa.CheckConstraint(
            "grant_state IN ('assembling','issued','revoked','expired')",
            name="ck_execution_grant_state",
        ),
        sa.CheckConstraint(
            "grant_revision > 0 AND expires_at > created_at",
            name="ck_execution_grant_revision_window",
        ),
        sa.CheckConstraint(
            _SURFACE.format(column="collection_surface"),
            name="ck_execution_grant_surface",
        ),
        sa.CheckConstraint(
            "province_code ~ '^[0-9]{6}$'",
            name="ck_execution_grant_province",
        ),
        sa.CheckConstraint(_SHA256.format(column="grant_hash"), name="ck_execution_grant_hash"),
        sa.CheckConstraint(
            "(grant_state='assembling' AND issued_at IS NULL "
            "AND revoked_at IS NULL AND revocation_reason IS NULL) OR "
            "(grant_state='issued' AND issued_at IS NOT NULL "
            "AND issued_at < expires_at AND revoked_at IS NULL "
            "AND revocation_reason IS NULL) OR "
            "(grant_state='revoked' AND issued_at IS NOT NULL "
            "AND revoked_at IS NOT NULL AND btrim(revocation_reason) <> '') OR "
            "(grant_state='expired' AND issued_at IS NOT NULL "
            "AND revoked_at IS NULL)",
            name="ck_execution_grant_timestamps",
        ),
        sa.CheckConstraint(
            "btrim(grant_key) <> '' AND btrim(capability_revision) <> '' "
            "AND btrim(platform) <> '' AND btrim(product_variant) <> '' "
            "AND btrim(interaction_mode) <> '' "
            "AND btrim(route_policy_revision) <> '' "
            "AND btrim(resource_policy_revision) <> '' "
            "AND btrim(workflow_contract_version) <> '' "
            "AND btrim(adapter_revision) <> '' "
            "AND btrim(gateway_protocol_revision) <> '' "
            "AND btrim(worker_build_id) <> '' "
            "AND btrim(issued_by_pub_id) <> '' AND btrim(issuance_reason) <> ''",
            name="ck_execution_grant_required_text",
        ),
        schema="platform",
    )
    op.create_index(
        "uq_execution_grant_issued_operation",
        "collection_execution_grant_v2",
        ["tenant_id", "project_id", "operation_id"],
        unique=True,
        schema="platform",
        postgresql_where=sa.text("grant_state = 'issued'"),
    )

    _create_execution_grant_subtypes()
    _create_execution_grant_resources()
    _create_execution_grant_guards()


def _grant_subtype_constraints(table: str, surface: str) -> list[sa.Constraint]:
    return [
        *_scope_constraints(table),
        sa.ForeignKeyConstraint(
            ["execution_grant_id", "tenant_id", "project_id", "collection_surface"],
            [
                "platform.collection_execution_grant_v2.id",
                "platform.collection_execution_grant_v2.tenant_id",
                "platform.collection_execution_grant_v2.project_id",
                "platform.collection_execution_grant_v2.collection_surface",
            ],
            name=f"fk_{table}_grant_surface",
        ),
        sa.UniqueConstraint(
            "execution_grant_id",
            "tenant_id",
            "project_id",
            name=f"uq_{table}_grant",
        ),
        sa.CheckConstraint(
            f"collection_surface = '{surface}'",
            name=f"ck_{table}_surface",
        ),
    ]


def _create_execution_grant_subtypes() -> None:
    op.create_table(
        "collection_api_execution_grant_v2",
        *_identity_columns(),
        sa.Column("execution_grant_id", sa.Uuid(), nullable=False),
        sa.Column("collection_surface", sa.String(length=30), nullable=False),
        sa.Column("provider_gateway_handle", sa.String(length=255), nullable=False),
        sa.Column("credential_slot_handle", sa.String(length=255), nullable=False),
        sa.Column("provider_endpoint_catalog_id", sa.String(length=128), nullable=False),
        sa.Column("provider_api_version", sa.String(length=128), nullable=False),
        sa.Column("provider_tenant_context_ref", sa.String(length=255), nullable=False),
        sa.Column("provider_quota_subject_ref", sa.String(length=255), nullable=False),
        *_grant_subtype_constraints(
            "collection_api_execution_grant_v2",
            "provider_api",
        ),
        sa.CheckConstraint(
            "provider_gateway_handle ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND credential_slot_handle ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND provider_tenant_context_ref ~ "
            "'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND provider_quota_subject_ref ~ "
            "'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'",
            name="ck_api_execution_grant_v2_opaque_refs",
        ),
        schema="platform",
    )
    op.create_table(
        "collection_web_execution_grant_v2",
        *_identity_columns(),
        sa.Column("execution_grant_id", sa.Uuid(), nullable=False),
        sa.Column("collection_surface", sa.String(length=30), nullable=False),
        sa.Column("browser_owner_handle", sa.String(length=255), nullable=False),
        sa.Column("governed_account_ref", sa.String(length=255), nullable=False),
        sa.Column("browser_profile_ref", sa.String(length=255), nullable=False),
        sa.Column("browser_profile_revision", sa.String(length=128), nullable=False),
        sa.Column("web_session_ref", sa.String(length=255), nullable=False),
        sa.Column("web_session_revision", sa.String(length=128), nullable=False),
        sa.Column("approved_host_catalog_id", sa.String(length=128), nullable=False),
        *_grant_subtype_constraints(
            "collection_web_execution_grant_v2",
            "consumer_web",
        ),
        sa.CheckConstraint(
            "browser_owner_handle ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND governed_account_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND browser_profile_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND web_session_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'",
            name="ck_web_execution_grant_v2_opaque_refs",
        ),
        schema="platform",
    )
    op.create_table(
        "collection_app_execution_grant_v2",
        *_identity_columns(),
        sa.Column("execution_grant_id", sa.Uuid(), nullable=False),
        sa.Column("collection_surface", sa.String(length=30), nullable=False),
        sa.Column("device_owner_handle", sa.String(length=255), nullable=False),
        sa.Column("governed_account_ref", sa.String(length=255), nullable=False),
        sa.Column("managed_device_ref", sa.String(length=255), nullable=False),
        sa.Column("app_package_id", sa.String(length=128), nullable=False),
        sa.Column("app_build_version", sa.String(length=128), nullable=False),
        sa.Column("distribution_channel", sa.String(length=128), nullable=False),
        sa.Column("app_install_ref", sa.String(length=255), nullable=False),
        sa.Column("app_session_ref", sa.String(length=255), nullable=False),
        sa.Column("app_session_revision", sa.String(length=128), nullable=False),
        sa.Column("automation_agent_revision", sa.String(length=128), nullable=False),
        *_grant_subtype_constraints(
            "collection_app_execution_grant_v2",
            "consumer_app",
        ),
        sa.CheckConstraint(
            "device_owner_handle ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND governed_account_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND managed_device_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND app_install_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND app_session_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'",
            name="ck_app_execution_grant_v2_opaque_refs",
        ),
        schema="platform",
    )


def _create_execution_grant_resources() -> None:
    op.create_table(
        "collection_execution_grant_resource",
        *_identity_columns(),
        sa.Column("execution_grant_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("binding_revision_id", sa.Uuid(), nullable=False),
        sa.Column("resource_registration_id", sa.Uuid(), nullable=False),
        sa.Column("capacity_unit_id", sa.Uuid(), nullable=False),
        sa.Column("resource_lease_id", sa.Uuid(), nullable=False),
        sa.Column("resource_pub_id", sa.String(length=30), nullable=False),
        sa.Column("resource_kind", sa.String(length=30), nullable=False),
        sa.Column("resource_role", sa.String(length=80), nullable=False),
        sa.Column("resource_ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "binding_resource_mapping_revision",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("owner_gateway_handle", sa.String(length=255), nullable=False),
        sa.Column("fence_generation", sa.BigInteger(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        *_scope_constraints("collection_execution_grant_resource"),
        sa.ForeignKeyConstraint(
            [
                "binding_revision_id",
                "tenant_id",
                "project_id",
                "resource_registration_id",
                "resource_pub_id",
                "resource_kind",
                "resource_role",
                "resource_ordinal",
                "binding_resource_mapping_revision",
            ],
            [
                "platform.collection_binding_resource.binding_revision_id",
                "platform.collection_binding_resource.tenant_id",
                "platform.collection_binding_resource.project_id",
                "platform.collection_binding_resource.resource_registration_id",
                "platform.collection_binding_resource.resource_pub_id",
                "platform.collection_binding_resource.resource_kind",
                "platform.collection_binding_resource.resource_role",
                "platform.collection_binding_resource.ordinal",
                "platform.collection_binding_resource.mapping_revision",
            ],
            name="fk_execution_grant_resource_binding_mapping",
        ),
        sa.ForeignKeyConstraint(
            [
                "execution_grant_id",
                "tenant_id",
                "project_id",
                "operation_id",
                "binding_revision_id",
            ],
            [
                "platform.collection_execution_grant_v2.id",
                "platform.collection_execution_grant_v2.tenant_id",
                "platform.collection_execution_grant_v2.project_id",
                "platform.collection_execution_grant_v2.operation_id",
                "platform.collection_execution_grant_v2.binding_revision_id",
            ],
            name="fk_execution_grant_resource_grant",
        ),
        sa.ForeignKeyConstraint(
            [
                "resource_lease_id",
                "tenant_id",
                "project_id",
                "operation_id",
                "binding_revision_id",
                "resource_registration_id",
                "capacity_unit_id",
                "resource_kind",
                "fence_generation",
            ],
            [
                "platform.resource_lease.id",
                "platform.resource_lease.tenant_id",
                "platform.resource_lease.project_id",
                "platform.resource_lease.operation_id",
                "platform.resource_lease.binding_revision_id",
                "platform.resource_lease.resource_registration_id",
                "platform.resource_lease.capacity_unit_id",
                "platform.resource_lease.resource_kind",
                "platform.resource_lease.fencing_token",
            ],
            name="fk_execution_grant_resource_lease_exact",
        ),
        sa.ForeignKeyConstraint(
            [
                "resource_registration_id",
                "tenant_id",
                "project_id",
                "resource_kind",
                "resource_pub_id",
            ],
            [
                "platform.resource_registration.id",
                "platform.resource_registration.tenant_id",
                "platform.resource_registration.project_id",
                "platform.resource_registration.resource_kind",
                "platform.resource_registration.pub_id",
            ],
            name="fk_execution_grant_resource_registration",
        ),
        sa.UniqueConstraint(
            "execution_grant_id",
            "tenant_id",
            "project_id",
            "resource_lease_id",
            name="uq_execution_grant_resource_lease",
        ),
        sa.UniqueConstraint(
            "execution_grant_id",
            "tenant_id",
            "project_id",
            "resource_role",
            "resource_ordinal",
            name="uq_execution_grant_resource_role",
        ),
        sa.CheckConstraint(
            "resource_kind IN " + str(_RESOURCE_KINDS).replace('"', "'"),
            name="ck_execution_grant_resource_kind",
        ),
        sa.CheckConstraint(
            "resource_ordinal >= 0 AND fence_generation > 0 "
            "AND btrim(binding_resource_mapping_revision) <> ''",
            name="ck_execution_grant_resource_numbers",
        ),
        sa.CheckConstraint(
            "owner_gateway_handle ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'",
            name="ck_execution_grant_resource_owner_handle",
        ),
        schema="platform",
    )


def _create_execution_grant_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION platform.guard_execution_grant_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE subtype_count integer;
        DECLARE invalid_resource_count integer;
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NEW.grant_state <> 'assembling' THEN
              RAISE EXCEPTION 'execution grant must begin assembling';
            END IF;
            RETURN NEW;
          END IF;
          IF TG_OP = 'DELETE' THEN
            IF OLD.grant_state <> 'assembling' THEN
              RAISE EXCEPTION 'issued execution grant cannot be deleted';
            END IF;
            RETURN OLD;
          END IF;
          IF OLD.grant_state IN ('issued','revoked','expired') AND
             ROW(NEW.pub_id, NEW.tenant_id, NEW.project_id,
                 NEW.schema_version, NEW.grant_key, NEW.grant_revision,
                 NEW.config_revision_id, NEW.campaign_id,
                 NEW.campaign_target_id, NEW.sampling_leg_id,
                 NEW.primary_slot_id, NEW.operation_id,
                 NEW.binding_revision_id, NEW.binding_revision,
                 NEW.binding_capability_id, NEW.capability_revision,
                 NEW.quota_registry_id, NEW.quota_reservation_id,
                 NEW.platform, NEW.collection_surface, NEW.product_variant,
                 NEW.province_code, NEW.interaction_mode,
                 NEW.route_policy_revision, NEW.resource_policy_revision,
                 NEW.workflow_contract_version, NEW.adapter_revision,
                 NEW.gateway_protocol_revision, NEW.worker_build_id,
                 NEW.agent_revision, NEW.allowed_actions_json, NEW.grant_hash,
                 NEW.issued_by_pub_id, NEW.issuance_reason,
                 NEW.issued_at, NEW.expires_at)
             IS DISTINCT FROM
             ROW(OLD.pub_id, OLD.tenant_id, OLD.project_id,
                 OLD.schema_version, OLD.grant_key, OLD.grant_revision,
                 OLD.config_revision_id, OLD.campaign_id,
                 OLD.campaign_target_id, OLD.sampling_leg_id,
                 OLD.primary_slot_id, OLD.operation_id,
                 OLD.binding_revision_id, OLD.binding_revision,
                 OLD.binding_capability_id, OLD.capability_revision,
                 OLD.quota_registry_id, OLD.quota_reservation_id,
                 OLD.platform, OLD.collection_surface, OLD.product_variant,
                 OLD.province_code, OLD.interaction_mode,
                 OLD.route_policy_revision, OLD.resource_policy_revision,
                 OLD.workflow_contract_version, OLD.adapter_revision,
                 OLD.gateway_protocol_revision, OLD.worker_build_id,
                 OLD.agent_revision, OLD.allowed_actions_json, OLD.grant_hash,
                 OLD.issued_by_pub_id, OLD.issuance_reason,
                 OLD.issued_at, OLD.expires_at) THEN
            RAISE EXCEPTION 'issued execution grant content is immutable';
          END IF;
          IF OLD.grant_state='assembling' AND
             NEW.grant_state NOT IN ('assembling','issued') OR
             OLD.grant_state='issued' AND
             NEW.grant_state NOT IN ('issued','revoked','expired') OR
             OLD.grant_state IN ('revoked','expired') AND
             NEW.grant_state <> OLD.grant_state THEN
            RAISE EXCEPTION 'invalid irreversible execution grant transition';
          END IF;
          IF OLD.grant_state='assembling' AND NEW.grant_state='issued' THEN
            IF NEW.issued_at IS NULL OR NEW.issued_at >= NEW.expires_at THEN
              RAISE EXCEPTION 'execution grant issuance window is invalid';
            END IF;
            IF NOT EXISTS (
              SELECT 1 FROM platform.collection_submission_operation o
               WHERE o.id=NEW.operation_id AND o.tenant_id=NEW.tenant_id
                 AND o.project_id=NEW.project_id AND o.send_state='NOT_SENT'
            ) OR NOT EXISTS (
              SELECT 1 FROM platform.collection_binding_revision_v2 b
               WHERE b.id=NEW.binding_revision_id AND b.tenant_id=NEW.tenant_id
                 AND b.project_id=NEW.project_id AND b.lifecycle_state='active'
                 AND b.activated_at IS NOT NULL
                 AND b.activated_at <= NEW.issued_at
                 AND b.effective_from <= NEW.issued_at
                 AND b.expires_at >= NEW.expires_at
            ) OR NOT EXISTS (
              SELECT 1 FROM platform.collection_quota_reservation q
               WHERE q.id=NEW.quota_reservation_id AND q.tenant_id=NEW.tenant_id
                 AND q.project_id=NEW.project_id AND q.reservation_state='reserved'
            ) OR NOT EXISTS (
              SELECT 1
                FROM platform.collection_config_revision_v2 config
                JOIN platform.collection_campaign campaign
                  ON campaign.id=NEW.campaign_id
                 AND campaign.tenant_id=config.tenant_id
                 AND campaign.project_id=config.project_id
                 AND campaign.config_revision_id=config.id
                 AND campaign.config_revision_hash=config.revision_hash
                JOIN platform.collection_campaign_target target
                  ON target.id=NEW.campaign_target_id
                 AND target.tenant_id=campaign.tenant_id
                 AND target.project_id=campaign.project_id
                 AND target.campaign_id=campaign.id
                JOIN platform.collection_config_target_v2 config_target
                  ON config_target.id=target.config_target_id
                 AND config_target.tenant_id=target.tenant_id
                 AND config_target.project_id=target.project_id
                 AND config_target.config_revision_id=config.id
                JOIN platform.collection_binding_revision_v2 binding
                  ON binding.id=NEW.binding_revision_id
                 AND binding.tenant_id=target.tenant_id
                 AND binding.project_id=target.project_id
                 AND binding.platform=target.platform
                 AND binding.collection_surface=target.collection_surface
                 AND binding.product_variant=target.product_variant
                JOIN platform.collection_binding_capability capability
                  ON capability.id=NEW.binding_capability_id
                 AND capability.binding_revision_id=binding.id
                 AND capability.tenant_id=binding.tenant_id
                 AND capability.project_id=binding.project_id
                JOIN platform.collection_capability_declaration declaration
                  ON declaration.id=capability.capability_declaration_id
                 AND declaration.tenant_id=capability.tenant_id
                 AND declaration.project_id=capability.project_id
                 AND declaration.registry_revision_id=
                     binding.capability_registry_id
               WHERE config.id=NEW.config_revision_id
                 AND config.tenant_id=NEW.tenant_id
                 AND config.project_id=NEW.project_id
                 AND config.lifecycle_state='active'
                 AND config.capability_registry_revision=
                     binding.capability_registry_revision
                 AND campaign.state='frozen'
                 AND campaign.materialization_state='complete'
                 AND campaign.materialized_slot_count=campaign.expected_slot_count
                 AND campaign.materialization_cursor=campaign.expected_slot_count
                 AND campaign.membership_hash IS NOT NULL
                 AND campaign.binding_policy_revision=
                     binding.binding_policy_revision
                 AND target.binding_policy_revision=
                     binding.binding_policy_revision
                 AND target.interaction_modes_json::jsonb ? NEW.interaction_mode
                 AND target.capability_revisions_json::jsonb ->>
                     NEW.interaction_mode = NEW.capability_revision
                 AND capability.requirement_state='required'
                 AND declaration.status='supported'
                 AND declaration.production_allowed=true
            ) THEN
              RAISE EXCEPTION 'execution grant prerequisites are not active';
            END IF;
            SELECT
              (SELECT count(*) FROM platform.collection_api_execution_grant_v2 s
                WHERE s.execution_grant_id=NEW.id
                  AND s.tenant_id=NEW.tenant_id AND s.project_id=NEW.project_id) +
              (SELECT count(*) FROM platform.collection_web_execution_grant_v2 s
                WHERE s.execution_grant_id=NEW.id
                  AND s.tenant_id=NEW.tenant_id AND s.project_id=NEW.project_id) +
              (SELECT count(*) FROM platform.collection_app_execution_grant_v2 s
                WHERE s.execution_grant_id=NEW.id
                  AND s.tenant_id=NEW.tenant_id AND s.project_id=NEW.project_id)
              INTO subtype_count;
            SELECT count(*) INTO invalid_resource_count
              FROM platform.collection_execution_grant_resource m
              JOIN platform.resource_lease l
                ON l.id=m.resource_lease_id AND l.tenant_id=m.tenant_id
               AND l.project_id=m.project_id
              JOIN platform.collection_resource_capacity_unit c
                ON c.id=m.capacity_unit_id AND c.tenant_id=m.tenant_id
               AND c.project_id=m.project_id
              JOIN platform.resource_registration r
                ON r.id=m.resource_registration_id AND r.tenant_id=m.tenant_id
               AND r.project_id=m.project_id
             WHERE m.execution_grant_id=NEW.id
               AND m.tenant_id=NEW.tenant_id AND m.project_id=NEW.project_id
               AND (l.lease_state <> 'active'
                    OR l.acquired_at > NEW.issued_at
                    OR l.expires_at <= NEW.issued_at
                    OR l.expires_at < NEW.expires_at
                    OR l.fencing_token <> c.current_fencing_token
                    OR m.fence_generation <> c.current_fencing_token
                    OR c.capacity_state <> 'leased'
                    OR r.resource_schema_version <> 'collection-resource-v2'
                    OR r.state <> 'active' OR r.revoked_at IS NOT NULL
                    OR m.owner_gateway_handle<>r.opaque_owner_handle
                    OR NOT EXISTS (
                      SELECT 1 FROM platform.collection_binding_resource br
                       WHERE br.binding_revision_id=NEW.binding_revision_id
                         AND br.tenant_id=NEW.tenant_id
                         AND br.project_id=NEW.project_id
                         AND br.resource_registration_id=m.resource_registration_id
                         AND br.resource_pub_id=m.resource_pub_id
                         AND br.resource_kind=m.resource_kind
                         AND br.resource_role=m.resource_role
                         AND br.ordinal=m.resource_ordinal
                         AND br.mapping_revision=
                             m.binding_resource_mapping_revision
                    ));
            IF subtype_count <> 1 OR invalid_resource_count > 0 OR NOT EXISTS (
              SELECT 1 FROM platform.collection_execution_grant_resource m
               WHERE m.execution_grant_id=NEW.id
                 AND m.tenant_id=NEW.tenant_id AND m.project_id=NEW.project_id
            ) OR EXISTS (
              SELECT 1 FROM platform.collection_binding_resource br
               WHERE br.binding_revision_id=NEW.binding_revision_id
                 AND br.tenant_id=NEW.tenant_id
                 AND br.project_id=NEW.project_id
                 AND br.required=true
                 AND NOT EXISTS (
                   SELECT 1 FROM platform.collection_execution_grant_resource m
                    WHERE m.execution_grant_id=NEW.id
                      AND m.tenant_id=NEW.tenant_id
                      AND m.project_id=NEW.project_id
                      AND m.resource_registration_id=br.resource_registration_id
                      AND m.resource_pub_id=br.resource_pub_id
                      AND m.resource_kind=br.resource_kind
                      AND m.resource_role=br.resource_role
                      AND m.resource_ordinal=br.ordinal
                      AND m.binding_resource_mapping_revision=
                          br.mapping_revision
                 )
            ) OR (NEW.collection_surface='provider_api' AND NOT EXISTS (
              SELECT 1
                FROM platform.collection_api_execution_grant_v2 grant_subtype
                JOIN platform.collection_api_binding_v2 binding_subtype
                  ON binding_subtype.binding_revision_id=NEW.binding_revision_id
                 AND binding_subtype.tenant_id=grant_subtype.tenant_id
                 AND binding_subtype.project_id=grant_subtype.project_id
               WHERE grant_subtype.execution_grant_id=NEW.id
                 AND grant_subtype.tenant_id=NEW.tenant_id
                 AND grant_subtype.project_id=NEW.project_id
                 AND grant_subtype.provider_gateway_handle=
                     binding_subtype.provider_gateway_handle
                 AND grant_subtype.credential_slot_handle=
                     binding_subtype.credential_slot_ref
                 AND grant_subtype.provider_endpoint_catalog_id=
                     binding_subtype.endpoint_catalog_id
                 AND grant_subtype.provider_api_version=binding_subtype.api_version
                 AND grant_subtype.provider_tenant_context_ref=
                     binding_subtype.provider_tenant_ref
                 AND grant_subtype.provider_quota_subject_ref=
                     binding_subtype.provider_account_ref
            )) OR (NEW.collection_surface='consumer_web' AND NOT EXISTS (
              SELECT 1
                FROM platform.collection_web_execution_grant_v2 grant_subtype
                JOIN platform.collection_web_binding_v2 binding_subtype
                  ON binding_subtype.binding_revision_id=NEW.binding_revision_id
                 AND binding_subtype.tenant_id=grant_subtype.tenant_id
                 AND binding_subtype.project_id=grant_subtype.project_id
               WHERE grant_subtype.execution_grant_id=NEW.id
                 AND grant_subtype.tenant_id=NEW.tenant_id
                 AND grant_subtype.project_id=NEW.project_id
                 AND grant_subtype.browser_owner_handle=
                     binding_subtype.browser_owner_handle
                 AND grant_subtype.governed_account_ref=
                     binding_subtype.governed_account_ref
                 AND grant_subtype.browser_profile_ref=
                     binding_subtype.browser_profile_ref
                 AND grant_subtype.browser_profile_revision=
                     binding_subtype.browser_profile_revision
                 AND grant_subtype.web_session_ref=binding_subtype.web_session_ref
                 AND grant_subtype.web_session_revision=
                     binding_subtype.web_session_revision
                 AND grant_subtype.approved_host_catalog_id=
                     binding_subtype.approved_host_catalog_id
            )) OR (NEW.collection_surface='consumer_app' AND NOT EXISTS (
              SELECT 1
                FROM platform.collection_app_execution_grant_v2 grant_subtype
                JOIN platform.collection_app_binding_v2 binding_subtype
                  ON binding_subtype.binding_revision_id=NEW.binding_revision_id
                 AND binding_subtype.tenant_id=grant_subtype.tenant_id
                 AND binding_subtype.project_id=grant_subtype.project_id
               WHERE grant_subtype.execution_grant_id=NEW.id
                 AND grant_subtype.tenant_id=NEW.tenant_id
                 AND grant_subtype.project_id=NEW.project_id
                 AND grant_subtype.device_owner_handle=
                     binding_subtype.device_owner_handle
                 AND grant_subtype.governed_account_ref=
                     binding_subtype.governed_account_ref
                 AND grant_subtype.managed_device_ref=
                     binding_subtype.managed_device_ref
                 AND grant_subtype.app_package_id=binding_subtype.app_package_id
                 AND grant_subtype.app_build_version=
                     binding_subtype.app_build_version
                 AND grant_subtype.distribution_channel=
                     binding_subtype.distribution_channel
                 AND grant_subtype.app_install_ref=binding_subtype.app_install_ref
                 AND grant_subtype.app_session_ref=binding_subtype.app_session_ref
                 AND grant_subtype.app_session_revision=
                     binding_subtype.app_session_revision
                 AND grant_subtype.automation_agent_revision=
                     binding_subtype.automation_agent_revision
            )) THEN
              RAISE EXCEPTION 'execution grant subtype or resource fences are invalid';
            END IF;
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.guard_execution_grant_child_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE parent_state text;
        BEGIN
          SELECT grant_state INTO parent_state
            FROM platform.collection_execution_grant_v2
           WHERE id=COALESCE(NEW.execution_grant_id, OLD.execution_grant_id)
             AND tenant_id=COALESCE(NEW.tenant_id, OLD.tenant_id)
             AND project_id=COALESCE(NEW.project_id, OLD.project_id);
          IF parent_state <> 'assembling' THEN
            RAISE EXCEPTION 'issued execution grant children are immutable';
          END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER execution_grant_v2_guard_trg
        BEFORE INSERT OR UPDATE OR DELETE
        ON platform.collection_execution_grant_v2
        FOR EACH ROW EXECUTE FUNCTION platform.guard_execution_grant_v2()
        """
    )
    for table in (
        "collection_api_execution_grant_v2",
        "collection_web_execution_grant_v2",
        "collection_app_execution_grant_v2",
        "collection_execution_grant_resource",
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table}_guard_trg
            BEFORE INSERT OR UPDATE OR DELETE ON platform.{table}
            FOR EACH ROW EXECUTE FUNCTION platform.guard_execution_grant_child_v2()
            """
        )


def _grant_minimum_privileges() -> None:
    op.execute(
        """
        DO $$
        DECLARE table_name text;
        DECLARE role_name text;
        DECLARE function_identity text;
        BEGIN
          FOREACH table_name IN ARRAY ARRAY[
            'collection_capability_registry_revision',
            'collection_capability_declaration',
            'collection_quota_registry_revision',
            'collection_quota_scope_policy',
            'collection_binding_revision_v2',
            'collection_api_binding_v2','collection_web_binding_v2',
            'collection_app_binding_v2','collection_binding_capability',
            'collection_binding_resource','collection_binding_quota_scope',
            'collection_submission_operation',
            'collection_submission_reconciliation_proof',
            'collection_resource_adoption','collection_resource_capacity_unit',
            'collection_quota_bucket','collection_quota_reservation',
            'collection_quota_reservation_effect','collection_quota_ledger_event',
            'collection_execution_grant_v2',
            'collection_api_execution_grant_v2',
            'collection_web_execution_grant_v2',
            'collection_app_execution_grant_v2',
            'collection_execution_grant_resource'
          ] LOOP
            EXECUTE format(
              'REVOKE ALL ON TABLE platform.%I FROM PUBLIC',table_name
            );
            FOREACH role_name IN ARRAY ARRAY['geo','geo_api','geo_worker'] LOOP
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
                EXECUTE format(
                  'REVOKE ALL ON TABLE platform.%I FROM %I',table_name,role_name
                );
              END IF;
            END LOOP;
          END LOOP;

          FOR function_identity IN
            SELECT format('%I.%I(%s)',n.nspname,p.proname,
                          pg_get_function_identity_arguments(p.oid))
              FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
             WHERE n.nspname='platform' AND p.proname IN (
               'guard_resource_registration_v2',
               'guard_capability_registry_v2','guard_capability_declaration_v2',
               'guard_quota_registry_v2','guard_quota_scope_policy_v2',
               'guard_binding_revision_v2','guard_binding_child_v2',
               'guard_submission_operation_v2',
               'guard_submission_reconciliation_proof_v2',
               'record_collection_not_sent_proof_v2',
               'guard_resource_adoption_v2','guard_resource_capacity_v2',
               'guard_resource_lease_v2','guard_quota_bucket_v2',
               'guard_quota_reservation_v2','guard_quota_effect_v2',
               'guard_quota_ledger_append_only_v2',
               'assert_collection_quota_bucket_v2',
               'assert_collection_quota_reservation_v2',
               'validate_collection_quota_conservation_v2',
               'guard_execution_grant_v2','guard_execution_grant_child_v2'
             )
          LOOP
            EXECUTE 'REVOKE ALL ON FUNCTION ' || function_identity ||
                    ' FROM PUBLIC';
            FOREACH role_name IN ARRAY ARRAY['geo','geo_api','geo_worker'] LOOP
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
                EXECUTE 'REVOKE ALL ON FUNCTION ' || function_identity ||
                        ' FROM ' || quote_ident(role_name);
              END IF;
            END LOOP;
          END LOOP;

          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            GRANT USAGE ON SCHEMA platform TO geo_api;
            FOREACH table_name IN ARRAY ARRAY[
              'collection_capability_registry_revision',
              'collection_capability_declaration',
              'collection_quota_registry_revision',
              'collection_quota_scope_policy',
              'collection_binding_revision_v2','collection_api_binding_v2',
              'collection_web_binding_v2','collection_app_binding_v2',
              'collection_binding_capability','collection_binding_resource',
              'collection_binding_quota_scope','collection_resource_adoption'
            ] LOOP
              EXECUTE format(
                'GRANT SELECT,INSERT ON TABLE platform.%I TO geo_api',
                table_name
              );
            END LOOP;
            GRANT UPDATE (
              lifecycle_state,change_reason,approved_by_pub_id,frozen_at,
              activated_at,retired_at,
              version,updated_at
            ) ON platform.collection_capability_registry_revision TO geo_api;
            GRANT UPDATE (
              status,production_allowed,region_policy_revision,
              required_resource_kinds_json,observable_capture_fields_json,
              product_version_constraints_json,unsupported_reason,
              alternative_suggestion,version,updated_at
            ) ON platform.collection_capability_declaration TO geo_api;
            GRANT UPDATE (
              lifecycle_state,change_reason,approved_by_pub_id,frozen_at,
              activated_at,retired_at,
              version,updated_at
            ) ON platform.collection_quota_registry_revision TO geo_api;
            GRANT UPDATE (
              share_policy,window_unit,window_size,window_timezone,
              window_boundary_revision,provider_window_code,limit_units,
              limit_source,settlement_policy_revision,lock_order_ordinal,
              version,updated_at
            ) ON platform.collection_quota_scope_policy TO geo_api;
            GRANT UPDATE (
              lifecycle_state,lifecycle_reason,activated_at,suspended_at,
              revoked_at,superseded_at,version,updated_at
            ) ON platform.collection_binding_revision_v2 TO geo_api;
            GRANT UPDATE (
              verification_state,verified_by_pub_id,verified_at,adopted_at,
              revoked_at,state_reason,version,updated_at
            ) ON platform.collection_resource_adoption TO geo_api;
            FOREACH table_name IN ARRAY ARRAY[
              'collection_submission_operation',
              'collection_submission_reconciliation_proof',
              'collection_resource_capacity_unit','collection_quota_bucket',
              'collection_quota_reservation','collection_quota_reservation_effect',
              'collection_quota_ledger_event','collection_execution_grant_v2',
              'collection_api_execution_grant_v2',
              'collection_web_execution_grant_v2',
              'collection_app_execution_grant_v2',
              'collection_execution_grant_resource'
            ] LOOP
              EXECUTE format(
                'GRANT SELECT ON TABLE platform.%I TO geo_api',table_name
              );
            END LOOP;
          END IF;

          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            GRANT USAGE ON SCHEMA platform TO geo_worker;
            FOREACH table_name IN ARRAY ARRAY[
              'collection_capability_registry_revision',
              'collection_capability_declaration','collection_quota_registry_revision',
              'collection_quota_scope_policy','collection_binding_revision_v2',
              'collection_api_binding_v2','collection_web_binding_v2',
              'collection_app_binding_v2','collection_binding_capability',
              'collection_binding_resource','collection_binding_quota_scope',
              'collection_submission_operation',
              'collection_submission_reconciliation_proof',
              'collection_resource_adoption','collection_resource_capacity_unit',
              'collection_quota_bucket','collection_quota_reservation',
              'collection_quota_reservation_effect','collection_quota_ledger_event',
              'collection_execution_grant_v2',
              'collection_api_execution_grant_v2',
              'collection_web_execution_grant_v2',
              'collection_app_execution_grant_v2',
              'collection_execution_grant_resource'
            ] LOOP
              EXECUTE format(
                'GRANT SELECT ON TABLE platform.%I TO geo_worker',table_name
              );
            END LOOP;
            FOREACH table_name IN ARRAY ARRAY[
              'collection_submission_operation','collection_resource_capacity_unit',
              'collection_quota_bucket','collection_quota_reservation',
              'collection_quota_reservation_effect','collection_execution_grant_v2',
              'collection_api_execution_grant_v2',
              'collection_web_execution_grant_v2',
              'collection_app_execution_grant_v2',
              'collection_execution_grant_resource'
            ] LOOP
              EXECUTE format(
                'GRANT INSERT ON TABLE platform.%I TO geo_worker',table_name
              );
            END LOOP;
            GRANT UPDATE (
              send_state,send_state_version,send_started_at,send_resolved_at,
              reconciliation_state,reconcile_after,state_reason,version,updated_at
            ) ON platform.collection_submission_operation TO geo_worker;
            GRANT UPDATE (
              capacity_state,current_fencing_token,last_heartbeat_at,
              quarantined_at,revoked_at,state_reason,version,updated_at
            ) ON platform.collection_resource_capacity_unit TO geo_worker;
            GRANT UPDATE (
              reserved_units,settled_consumed_units,settled_unknown_units,
              bucket_state,fence_version,version,updated_at
            ) ON platform.collection_quota_bucket TO geo_worker;
            GRANT UPDATE (
              reservation_state,reserved_at,finalized_at,reconcile_after,
              state_reason,version,updated_at
            ) ON platform.collection_quota_reservation TO geo_worker;
            GRANT UPDATE (
              effect_state,state_reason,settled_at,released_at,version,updated_at
            ) ON platform.collection_quota_reservation_effect TO geo_worker;
            GRANT UPDATE (
              grant_state,issued_at,revoked_at,revocation_reason,version,updated_at
            ) ON platform.collection_execution_grant_v2 TO geo_worker;
            GRANT INSERT ON TABLE platform.collection_quota_ledger_event
              TO geo_worker;
            GRANT EXECUTE ON FUNCTION
              platform.record_collection_not_sent_proof_v2(
                uuid,uuid,uuid,text,text,text,text
              ) TO geo_worker;
          END IF;

          FOREACH role_name IN ARRAY ARRAY['geo_api','geo_worker'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
              EXECUTE format(
                'REVOKE ALL ON TABLE platform.resource_registration FROM %I',
                role_name
              );
              EXECUTE format(
                'REVOKE ALL ON TABLE platform.resource_lease FROM %I',role_name
              );
              EXECUTE format(
                'GRANT SELECT ON TABLE platform.resource_registration TO %I',
                role_name
              );
              EXECUTE format(
                'GRANT SELECT ON TABLE platform.resource_lease TO %I',role_name
              );
            END IF;
          END LOOP;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            GRANT INSERT ON TABLE platform.resource_registration TO geo_api;
            GRANT UPDATE (
              display_mask,capabilities_json,region,concurrency_limit,state,
              last_heartbeat_at,project_id,resource_schema_version,
              resource_revision,owner_gateway_kind,owner_gateway_revision,
              opaque_owner_handle,attestation_revision,route_policy_revision,
              resource_fingerprint,approved_at,revoked_at,version,updated_at
            ) ON platform.resource_registration TO geo_api;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            GRANT UPDATE (state,last_heartbeat_at,revoked_at,version,updated_at)
              ON platform.resource_registration TO geo_worker;
            GRANT INSERT ON TABLE platform.resource_lease TO geo_worker;
            GRANT UPDATE (
              lease_state,heartbeat_at,expires_at,released_at,revoked_at,
              reconciliation_reason,version,updated_at
            ) ON platform.resource_lease TO geo_worker;
          END IF;
        END
        $$
        """
    )


def upgrade() -> None:
    _extend_resource_registration()
    _create_capability_tables()
    _create_quota_policy_tables()
    _create_binding_tables()
    _create_submission_operation()
    _create_resource_governance_tables()
    _extend_resource_lease()
    _create_submission_reconciliation_proof()
    _create_quota_runtime_tables()
    _create_execution_grant_tables()
    for table in _NEW_TABLES:
        _enable_rls(table)
    _grant_minimum_privileges()


def _drop_execution_grant_tables() -> None:
    for table in (
        "collection_api_execution_grant_v2",
        "collection_web_execution_grant_v2",
        "collection_app_execution_grant_v2",
        "collection_execution_grant_resource",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_guard_trg ON platform.{table}")
    op.execute(
        "DROP TRIGGER IF EXISTS execution_grant_v2_guard_trg "
        "ON platform.collection_execution_grant_v2"
    )
    op.execute("DROP FUNCTION platform.guard_execution_grant_child_v2()")
    op.execute("DROP FUNCTION platform.guard_execution_grant_v2()")
    op.drop_table("collection_execution_grant_resource", schema="platform")
    op.drop_table("collection_app_execution_grant_v2", schema="platform")
    op.drop_table("collection_web_execution_grant_v2", schema="platform")
    op.drop_table("collection_api_execution_grant_v2", schema="platform")
    op.drop_table("collection_execution_grant_v2", schema="platform")


def _drop_quota_runtime_tables() -> None:
    for table in (
        "collection_quota_bucket",
        "collection_quota_reservation",
        "collection_quota_reservation_effect",
        "collection_quota_ledger_event",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_conservation_trg ON platform.{table}")
    op.execute("DROP FUNCTION platform.validate_collection_quota_conservation_v2()")
    op.execute("DROP FUNCTION platform.assert_collection_quota_reservation_v2(uuid,uuid,uuid)")
    op.execute("DROP FUNCTION platform.assert_collection_quota_bucket_v2(uuid,uuid,uuid)")
    op.execute(
        "DROP TRIGGER IF EXISTS quota_ledger_append_only_v2_trg "
        "ON platform.collection_quota_ledger_event"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS quota_effect_v2_guard_trg "
        "ON platform.collection_quota_reservation_effect"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS quota_reservation_v2_guard_trg "
        "ON platform.collection_quota_reservation"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS quota_bucket_v2_guard_trg ON platform.collection_quota_bucket"
    )
    op.execute("DROP FUNCTION platform.guard_quota_ledger_append_only_v2()")
    op.execute("DROP FUNCTION platform.guard_quota_effect_v2()")
    op.execute("DROP FUNCTION platform.guard_quota_reservation_v2()")
    op.execute("DROP FUNCTION platform.guard_quota_bucket_v2()")
    op.drop_table("collection_quota_ledger_event", schema="platform")
    op.drop_table("collection_quota_reservation_effect", schema="platform")
    op.drop_table("collection_quota_reservation", schema="platform")
    op.drop_table("collection_quota_bucket", schema="platform")


def _drop_resource_lease_extension() -> None:
    op.execute("DROP TRIGGER IF EXISTS resource_lease_v2_guard_trg ON platform.resource_lease")
    op.execute("DROP FUNCTION platform.guard_resource_lease_v2()")
    for index in (
        "uq_resource_lease_capacity_fence_s07",
        "uq_resource_lease_active_capacity_s07",
        "uq_resource_lease_key_s07",
    ):
        op.drop_index(index, table_name="resource_lease", schema="platform")
    op.drop_constraint(
        "ck_resource_lease_v2_shape_s07",
        "resource_lease",
        schema="platform",
        type_="check",
    )
    for constraint, constraint_type in (
        ("uq_resource_lease_grant_identity_s07", "unique"),
        ("uq_resource_lease_scope_s07", "unique"),
        ("fk_resource_lease_binding_s07", "foreignkey"),
        ("fk_resource_lease_operation_s07", "foreignkey"),
        ("fk_resource_lease_capacity_s07", "foreignkey"),
        ("fk_resource_lease_registration_s07", "foreignkey"),
        ("fk_resource_lease_project_s07", "foreignkey"),
    ):
        op.drop_constraint(
            constraint,
            "resource_lease",
            schema="platform",
            type_=constraint_type,
        )
    for column in reversed(
        (
            "project_id",
            "lease_schema_version",
            "resource_registration_id",
            "capacity_unit_id",
            "operation_id",
            "binding_revision_id",
            "lease_key",
            "lease_attempt",
            "lease_state",
            "acquired_at",
            "heartbeat_at",
            "revoked_at",
            "owner_gateway_revision",
            "reconciliation_reason",
        )
    ):
        op.drop_column("resource_lease", column, schema="platform")


def _drop_submission_reconciliation_proof() -> None:
    op.execute(
        "DROP FUNCTION platform.record_collection_not_sent_proof_v2("
        "uuid,uuid,uuid,text,text,text,text)"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS submission_reconciliation_proof_v2_guard_trg "
        "ON platform.collection_submission_reconciliation_proof"
    )
    op.execute("DROP FUNCTION platform.guard_submission_reconciliation_proof_v2()")
    op.drop_table("collection_submission_reconciliation_proof", schema="platform")


def _drop_resource_governance_tables() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS resource_capacity_v2_guard_trg "
        "ON platform.collection_resource_capacity_unit"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS resource_adoption_v2_guard_trg "
        "ON platform.collection_resource_adoption"
    )
    op.execute("DROP FUNCTION platform.guard_resource_capacity_v2()")
    op.execute("DROP FUNCTION platform.guard_resource_adoption_v2()")
    op.drop_table("collection_resource_capacity_unit", schema="platform")
    op.drop_table("collection_resource_adoption", schema="platform")
    op.drop_constraint(
        "uq_browser_profile_id_tenant_s07",
        "browser_profile",
        schema="platform",
        type_="unique",
    )
    op.drop_constraint(
        "uq_platform_account_id_tenant_s07",
        "platform_account",
        schema="platform",
        type_="unique",
    )


def _drop_submission_operation() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS submission_operation_v2_guard_trg "
        "ON platform.collection_submission_operation"
    )
    op.execute("DROP FUNCTION platform.guard_submission_operation_v2()")
    op.drop_table("collection_submission_operation", schema="platform")
    op.drop_constraint(
        "uq_primary_slot_operation_identity_s07",
        "collection_primary_slot",
        schema="platform",
        type_="unique",
    )


def _drop_binding_tables() -> None:
    for table in (
        "collection_api_binding_v2",
        "collection_web_binding_v2",
        "collection_app_binding_v2",
        "collection_binding_capability",
        "collection_binding_resource",
        "collection_binding_quota_scope",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_guard_trg ON platform.{table}")
    op.execute(
        "DROP TRIGGER IF EXISTS binding_revision_v2_guard_trg "
        "ON platform.collection_binding_revision_v2"
    )
    op.execute("DROP FUNCTION platform.guard_binding_child_v2()")
    op.execute("DROP FUNCTION platform.guard_binding_revision_v2()")
    op.drop_table("collection_binding_quota_scope", schema="platform")
    op.drop_table("collection_binding_resource", schema="platform")
    op.drop_table("collection_binding_capability", schema="platform")
    op.drop_table("collection_app_binding_v2", schema="platform")
    op.drop_table("collection_web_binding_v2", schema="platform")
    op.drop_table("collection_api_binding_v2", schema="platform")
    op.drop_table("collection_binding_revision_v2", schema="platform")


def _drop_quota_policy_tables() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS quota_scope_policy_v2_guard_trg "
        "ON platform.collection_quota_scope_policy"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS quota_registry_v2_guard_trg "
        "ON platform.collection_quota_registry_revision"
    )
    op.execute("DROP FUNCTION platform.guard_quota_scope_policy_v2()")
    op.execute("DROP FUNCTION platform.guard_quota_registry_v2()")
    op.drop_table("collection_quota_scope_policy", schema="platform")
    op.drop_table("collection_quota_registry_revision", schema="platform")


def _drop_capability_tables() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS capability_declaration_v2_guard_trg "
        "ON platform.collection_capability_declaration"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS capability_registry_v2_guard_trg "
        "ON platform.collection_capability_registry_revision"
    )
    op.execute("DROP FUNCTION platform.guard_capability_declaration_v2()")
    op.execute("DROP FUNCTION platform.guard_capability_registry_v2()")
    op.drop_table("collection_capability_declaration", schema="platform")
    op.drop_table("collection_capability_registry_revision", schema="platform")


def _drop_resource_registration_extension() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS resource_registration_v2_guard_trg "
        "ON platform.resource_registration"
    )
    op.execute("DROP FUNCTION platform.guard_resource_registration_v2()")
    op.drop_index(
        "uq_resource_registration_fingerprint_s07",
        table_name="resource_registration",
        schema="platform",
    )
    op.drop_index(
        "uq_resource_registration_revision_s07",
        table_name="resource_registration",
        schema="platform",
    )
    op.drop_constraint(
        "ck_resource_registration_v2_shape_s07",
        "resource_registration",
        schema="platform",
        type_="check",
    )
    for constraint, constraint_type in (
        ("uq_resource_registration_identity_s07", "unique"),
        ("uq_resource_registration_scope_s07", "unique"),
        ("fk_resource_registration_project_s07", "foreignkey"),
    ):
        op.drop_constraint(
            constraint,
            "resource_registration",
            schema="platform",
            type_=constraint_type,
        )
    for column in reversed(
        (
            "project_id",
            "resource_schema_version",
            "resource_revision",
            "owner_gateway_kind",
            "owner_gateway_revision",
            "opaque_owner_handle",
            "attestation_revision",
            "route_policy_revision",
            "resource_fingerprint",
            "approved_at",
            "revoked_at",
        )
    ):
        op.drop_column("resource_registration", column, schema="platform")


def downgrade() -> None:
    _drop_execution_grant_tables()
    _drop_quota_runtime_tables()
    _drop_submission_reconciliation_proof()
    _drop_resource_lease_extension()
    _drop_resource_governance_tables()
    _drop_submission_operation()
    _drop_binding_tables()
    _drop_quota_policy_tables()
    _drop_capability_tables()
    _drop_resource_registration_extension()


__all__ = ["downgrade", "upgrade"]
