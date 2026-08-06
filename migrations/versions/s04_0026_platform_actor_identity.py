"""Normalize authenticated actors to platform user public IDs.

Revision ID: s04_0026
Revises: s04_0025
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0026"
down_revision: str | Sequence[str] | None = "s04_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE intelligence.appeal
          ADD COLUMN resolved_by_pub_id TEXT,
          ADD COLUMN resolution_rationale TEXT,
          ADD COLUMN resolved_at TIMESTAMPTZ;

        CREATE TEMP TABLE s04_actor_projection ON COMMIT DROP AS
        SELECT DISTINCT tenant.id AS tenant_id,
               tenant.pub_id AS tenant_pub_id,
               app_user.subject,
               app_user.pub_id AS user_pub_id
        FROM platform.membership membership
        JOIN platform.tenant tenant ON tenant.id=membership.tenant_id
        JOIN platform.app_user app_user ON app_user.id=membership.user_id;

        UPDATE platform.audit_log target
        SET actor_pub_id=projection.user_pub_id
        FROM s04_actor_projection projection
        WHERE target.tenant_id=projection.tenant_id
          AND target.actor_pub_id=projection.subject;

        UPDATE platform.client_profile_version target
        SET declared_by=projection.user_pub_id
        FROM s04_actor_projection projection
        WHERE target.tenant_id=projection.tenant_id
          AND target.declared_by=projection.subject;

        UPDATE platform.asset_confirmation_version target
        SET declared_by=projection.user_pub_id
        FROM s04_actor_projection projection
        WHERE target.tenant_id=projection.tenant_id
          AND target.declared_by=projection.subject;

        UPDATE platform.capability_lease target
        SET issued_by=projection.user_pub_id
        FROM s04_actor_projection projection
        WHERE target.tenant_id=projection.tenant_id
          AND target.issued_by=projection.subject;

        UPDATE platform.credential_access_request target
        SET requested_by=projection.user_pub_id
        FROM s04_actor_projection projection
        WHERE target.tenant_id=projection.tenant_id
          AND target.requested_by=projection.subject;

        UPDATE platform.credential_access_approval target
        SET approver_pub_id=projection.user_pub_id
        FROM s04_actor_projection projection
        WHERE target.tenant_id=projection.tenant_id
          AND target.approver_pub_id=projection.subject;

        UPDATE platform.session_health_check target
        SET checked_by=projection.user_pub_id
        FROM s04_actor_projection projection
        WHERE target.tenant_id=projection.tenant_id
          AND target.checked_by=projection.subject;

        UPDATE evidence.evidence_access_audit target
        SET actor_pub_id=projection.user_pub_id
        FROM s04_actor_projection projection
        WHERE target.tenant_pub_id=projection.tenant_pub_id
          AND target.actor_pub_id=projection.subject;

        UPDATE reporting.report_version target
        SET created_by_pub_id=projection.user_pub_id
        FROM s04_actor_projection projection
        WHERE target.tenant_pub_id=projection.tenant_pub_id
          AND target.created_by_pub_id=projection.subject;

        UPDATE reporting.report_review target
        SET reviewer_pub_id=projection.user_pub_id
        FROM s04_actor_projection projection
        WHERE target.tenant_pub_id=projection.tenant_pub_id
          AND target.reviewer_pub_id=projection.subject;

        UPDATE reporting.report_comment target
        SET author_pub_id=projection.user_pub_id
        FROM s04_actor_projection projection
        WHERE target.tenant_pub_id=projection.tenant_pub_id
          AND target.author_pub_id=projection.subject;

        UPDATE reporting.report_event target
        SET actor_pub_id=projection.user_pub_id
        FROM s04_actor_projection projection
        WHERE target.tenant_pub_id=projection.tenant_pub_id
          AND target.actor_pub_id=projection.subject;

        UPDATE reporting.effect_retest target
        SET recorded_by_pub_id=projection.user_pub_id
        FROM s04_actor_projection projection
        WHERE target.tenant_pub_id=projection.tenant_pub_id
          AND target.recorded_by_pub_id=projection.subject;

        UPDATE reporting.data_export target
        SET created_by_pub_id=projection.user_pub_id
        FROM s04_actor_projection projection
        WHERE target.tenant_pub_id=projection.tenant_pub_id
          AND target.created_by_pub_id=projection.subject;

        UPDATE reporting.report_delivery target
        SET recipient_pub_id=projection.user_pub_id
        FROM s04_actor_projection projection
        WHERE target.tenant_pub_id=projection.tenant_pub_id
          AND target.recipient_pub_id=projection.subject;

        UPDATE intelligence.human_verdict target
        SET reviewer_pub_id=projection.user_pub_id
        FROM s04_actor_projection projection
        WHERE target.tenant_pub_id=projection.tenant_pub_id
          AND target.reviewer_pub_id=projection.subject;

        UPDATE intelligence.appeal target
        SET submitted_by_pub_id=projection.user_pub_id
        FROM s04_actor_projection projection
        WHERE target.tenant_pub_id=projection.tenant_pub_id
          AND target.submitted_by_pub_id=projection.subject;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE intelligence.appeal
          DROP COLUMN IF EXISTS resolved_at,
          DROP COLUMN IF EXISTS resolution_rationale,
          DROP COLUMN IF EXISTS resolved_by_pub_id;
        """
    )
