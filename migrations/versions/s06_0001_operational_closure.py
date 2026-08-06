"""Close the operator workflow and remove runtime dependencies outside Platform V2.

Revision ID: s06_0001
Revises: s05_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s06_0001"
down_revision: str | Sequence[str] | None = "s05_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _platform_rls(table: str) -> None:
    op.execute(f'ALTER TABLE platform."{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE platform."{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_isolation ON platform."{table}" '
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def _posting_rls(table: str) -> None:
    op.execute(f'ALTER TABLE posting."{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE posting."{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_isolation ON posting."{table}" '
        "USING (tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), '')) "
        "WITH CHECK (tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), ''))"
    )


def upgrade() -> None:
    op.add_column(
        "tenant",
        sa.Column("environment", sa.String(20), nullable=False, server_default="production"),
        schema="platform",
    )
    op.create_check_constraint(
        "tenant_environment_ck",
        "tenant",
        "environment IN ('production','training')",
        schema="platform",
    )
    # Native V2 browser identity. These tables intentionally do not use tenant RLS: the
    # authentication boundary must resolve the tenant before it can set the RLS selector.
    op.execute(
        """
        CREATE TABLE platform.user_password_credential (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES platform.tenant(id) ON DELETE CASCADE,
          user_id UUID NOT NULL REFERENCES platform.app_user(id) ON DELETE CASCADE,
          salt BYTEA NOT NULL,
          password_hash BYTEA NOT NULL,
          scrypt_n INTEGER NOT NULL CHECK (scrypt_n BETWEEN 16384 AND 1048576),
          scrypt_r INTEGER NOT NULL CHECK (scrypt_r BETWEEN 8 AND 64),
          scrypt_p INTEGER NOT NULL CHECK (scrypt_p BETWEEN 1 AND 16),
          password_changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_id,user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE platform.browser_session (
          id UUID PRIMARY KEY,
          pub_id VARCHAR(30) NOT NULL UNIQUE,
          tenant_id UUID NOT NULL REFERENCES platform.tenant(id) ON DELETE CASCADE,
          user_id UUID NOT NULL REFERENCES platform.app_user(id) ON DELETE CASCADE,
          token_hash CHAR(64) NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
          expires_at TIMESTAMPTZ NOT NULL,
          last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          revoked_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX browser_session_active_idx "
        "ON platform.browser_session (token_hash,expires_at) WHERE revoked_at IS NULL"
    )
    op.execute(
        """
        CREATE TABLE platform.login_attempt (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          subject_hash CHAR(64) NOT NULL CHECK (subject_hash ~ '^[0-9a-f]{64}$'),
          network_hash CHAR(64) NOT NULL CHECK (network_hash ~ '^[0-9a-f]{64}$'),
          succeeded BOOLEAN NOT NULL,
          occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX login_attempt_rate_idx "
        "ON platform.login_attempt (subject_hash,network_hash,occurred_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE platform.monitoring_schedule (
          id UUID PRIMARY KEY,
          pub_id VARCHAR(30) NOT NULL UNIQUE,
          tenant_id UUID NOT NULL REFERENCES platform.tenant(id) ON DELETE CASCADE,
          project_id UUID NOT NULL REFERENCES platform.project(id) ON DELETE CASCADE,
          config_version_id UUID NOT NULL
            REFERENCES platform.monitoring_config_version(id),
          interval_minutes INTEGER NOT NULL
            CHECK (interval_minutes BETWEEN 15 AND 525600),
          timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai',
          state VARCHAR(30) NOT NULL DEFAULT 'active'
            CHECK (state IN ('active','paused','archived')),
          next_run_at TIMESTAMPTZ NOT NULL,
          last_run_at TIMESTAMPTZ,
          last_run_pub_id VARCHAR(30),
          responsible_pub_id VARCHAR(30) NOT NULL,
          created_by_pub_id VARCHAR(30) NOT NULL,
          version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX monitoring_schedule_due_idx "
        "ON platform.monitoring_schedule (state,next_run_at)"
    )
    op.execute(
        """
        CREATE TABLE platform.monitoring_schedule_event (
          id UUID PRIMARY KEY,
          pub_id VARCHAR(30) NOT NULL UNIQUE,
          tenant_id UUID NOT NULL REFERENCES platform.tenant(id) ON DELETE CASCADE,
          schedule_id UUID NOT NULL
            REFERENCES platform.monitoring_schedule(id) ON DELETE CASCADE,
          event_type VARCHAR(80) NOT NULL,
          actor_pub_id VARCHAR(30) NOT NULL,
          data_json TEXT NOT NULL DEFAULT '{}',
          occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX monitoring_schedule_event_idx "
        "ON platform.monitoring_schedule_event (tenant_id,schedule_id,occurred_at DESC)"
    )
    op.execute(
        """
        CREATE TABLE platform.account_sla_policy (
          id UUID PRIMARY KEY,
          pub_id VARCHAR(30) NOT NULL UNIQUE,
          tenant_id UUID NOT NULL REFERENCES platform.tenant(id) ON DELETE CASCADE,
          adapter_id UUID NOT NULL REFERENCES platform.platform_adapter(id),
          owner_pub_id VARCHAR(30) NOT NULL,
          session_ttl_minutes INTEGER NOT NULL DEFAULT 10080
            CHECK (session_ttl_minutes BETWEEN 15 AND 525600),
          intervention_sla_minutes INTEGER NOT NULL DEFAULT 30
            CHECK (intervention_sla_minutes BETWEEN 1 AND 10080),
          success_target_bps INTEGER NOT NULL DEFAULT 9500
            CHECK (success_target_bps BETWEEN 0 AND 10000),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_id,adapter_id)
        )
        """
    )
    for table in (
        "monitoring_schedule",
        "monitoring_schedule_event",
        "account_sla_policy",
    ):
        _platform_rls(table)

    op.add_column(
        "collection_run",
        sa.Column("source", sa.String(30), nullable=False, server_default="manual"),
        schema="platform",
    )
    op.add_column(
        "collection_run",
        sa.Column("schedule_pub_id", sa.String(30), nullable=True),
        schema="platform",
    )
    op.add_column(
        "collection_run",
        sa.Column("retry_of_run_pub_id", sa.String(30), nullable=True),
        schema="platform",
    )
    op.add_column(
        "collection_run",
        sa.Column("initiated_by_pub_id", sa.String(30), nullable=True),
        schema="platform",
    )
    op.create_check_constraint(
        "collection_run_source_ck",
        "collection_run",
        "source IN ('manual','schedule','retry','training')",
        schema="platform",
    )
    op.create_index(
        "collection_run_schedule_idx",
        "collection_run",
        ["tenant_id", "schedule_pub_id", "created_at"],
        schema="platform",
    )
    op.add_column(
        "intervention_request",
        sa.Column("assigned_to_pub_id", sa.String(30), nullable=True),
        schema="platform",
    )
    op.add_column(
        "intervention_request",
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        schema="platform",
    )
    op.add_column(
        "intervention_request",
        sa.Column("resolution_note", sa.Text(), nullable=False, server_default=""),
        schema="platform",
    )
    op.create_index(
        "intervention_owner_due_idx",
        "intervention_request",
        ["tenant_id", "assigned_to_pub_id", "due_at"],
        schema="platform",
    )

    op.add_column(
        "batch",
        sa.Column("sop_project_pub_id", sa.Text(), nullable=True),
        schema="posting",
    )
    op.add_column(
        "batch",
        sa.Column("article_version_pub_id", sa.Text(), nullable=True),
        schema="posting",
    )
    op.add_column(
        "batch",
        sa.Column("approval_state", sa.Text(), nullable=False, server_default="draft"),
        schema="posting",
    )
    op.add_column(
        "batch",
        sa.Column("approval_requested_by_pub_id", sa.Text(), nullable=True),
        schema="posting",
    )
    op.add_column(
        "batch",
        sa.Column("approved_by_pub_id", sa.Text(), nullable=True),
        schema="posting",
    )
    op.add_column(
        "batch",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        schema="posting",
    )
    op.create_check_constraint(
        "posting_batch_approval_state_ck",
        "batch",
        "approval_state IN ('draft','pending','approved','rejected')",
        schema="posting",
    )
    op.execute(
        """
        UPDATE posting.batch
        SET approval_state='approved',approved_at=COALESCE(spend_confirmed_at,created_at),
            approved_by_pub_id=created_by_pub_id
        WHERE status <> 'draft' OR auto_submit
        """
    )
    op.execute(
        """
        CREATE TABLE posting.attribution (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          batch_pub_id TEXT NOT NULL REFERENCES posting.batch(pub_id),
          target_pub_id TEXT REFERENCES posting.target(pub_id),
          sop_publication_pub_id TEXT,
          retest_run_pub_id TEXT,
          public_url TEXT NOT NULL DEFAULT '',
          relation_type TEXT NOT NULL
            CHECK (relation_type IN ('published_as','retested_by','cited_by','correlated_with')),
          evidence_sha256 TEXT CHECK (
            evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$'
          ),
          note TEXT NOT NULL DEFAULT '',
          created_by_pub_id TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (
            tenant_pub_id,batch_pub_id,target_pub_id,sop_publication_pub_id,
            retest_run_pub_id,relation_type
          )
        )
        """
    )
    op.execute(
        "CREATE INDEX posting_attribution_batch_idx "
        "ON posting.attribution (tenant_pub_id,batch_pub_id,created_at DESC)"
    )
    _posting_rls("attribution")


def downgrade() -> None:
    op.execute("DROP TABLE posting.attribution")
    op.drop_constraint("posting_batch_approval_state_ck", "batch", schema="posting", type_="check")
    for column in (
        "approved_at",
        "approved_by_pub_id",
        "approval_requested_by_pub_id",
        "approval_state",
        "article_version_pub_id",
        "sop_project_pub_id",
    ):
        op.drop_column("batch", column, schema="posting")
    op.drop_index(
        "intervention_owner_due_idx", table_name="intervention_request", schema="platform"
    )
    for column in ("resolution_note", "due_at", "assigned_to_pub_id"):
        op.drop_column("intervention_request", column, schema="platform")
    op.drop_index("collection_run_schedule_idx", table_name="collection_run", schema="platform")
    op.drop_constraint(
        "collection_run_source_ck", "collection_run", schema="platform", type_="check"
    )
    for column in (
        "initiated_by_pub_id",
        "retry_of_run_pub_id",
        "schedule_pub_id",
        "source",
    ):
        op.drop_column("collection_run", column, schema="platform")
    for table in (
        "account_sla_policy",
        "monitoring_schedule_event",
        "monitoring_schedule",
        "login_attempt",
        "browser_session",
        "user_password_credential",
    ):
        op.execute(f'DROP TABLE platform."{table}"')
    op.drop_constraint("tenant_environment_ck", "tenant", schema="platform", type_="check")
    op.drop_column("tenant", "environment", schema="platform")
