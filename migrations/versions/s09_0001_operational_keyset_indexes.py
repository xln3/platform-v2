"""Add composite indexes for opaque operational keyset pages.

This revision changes indexes only.  It does not rewrite frozen snapshots,
collection facts, tenant policies, grants, or user data.

Revision ID: s09_0001_ops_keysets
Revises: s08_0001_service2_all_u
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s09_0001_ops_keysets"
down_revision: str | Sequence[str] | None = "s08_0001_service2_all_u"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_project_tenant_created_pub",
        "project",
        ["tenant_id", sa.text("created_at DESC"), sa.text("pub_id DESC")],
        schema="platform",
    )
    op.create_index(
        "ix_collection_run_tenant_created_pub",
        "collection_run",
        ["tenant_id", sa.text("created_at DESC"), sa.text("pub_id DESC")],
        schema="platform",
    )
    op.create_index(
        "ix_collection_run_project_created_pub",
        "collection_run",
        ["tenant_id", "project_id", sa.text("created_at DESC"), sa.text("pub_id DESC")],
        schema="platform",
    )
    op.create_index(
        "ix_platform_account_tenant_created_pub",
        "platform_account",
        ["tenant_id", sa.text("created_at DESC"), sa.text("pub_id DESC")],
        schema="platform",
    )
    op.create_index(
        "ix_collection_phone_account_created_pub",
        "collection_phone_account",
        [sa.text("created_at DESC"), sa.text("pub_id DESC")],
        schema="platform",
    )
    op.create_index(
        "ix_collection_browser_created_pub",
        "collection_browser",
        [sa.text("created_at DESC"), sa.text("pub_id DESC")],
        schema="platform",
    )
    op.create_index(
        "ix_collection_account_event_phone_created_pub",
        "collection_account_event",
        ["phone_account_id", sa.text("created_at DESC"), sa.text("pub_id DESC")],
        schema="platform",
    )
    op.create_index(
        "ix_collection_account_event_platform_created_pub",
        "collection_account_event",
        ["platform_account_id", sa.text("created_at DESC"), sa.text("pub_id DESC")],
        schema="platform",
    )
    op.create_index(
        "ix_intervention_tenant_created_pub",
        "intervention_request",
        ["tenant_id", sa.text("created_at DESC"), sa.text("pub_id DESC")],
        schema="platform",
    )
    op.create_index(
        "ix_credential_access_tenant_created_pub",
        "credential_access_request",
        ["tenant_id", sa.text("created_at DESC"), sa.text("pub_id DESC")],
        schema="platform",
    )
    op.create_index(
        "ix_session_event_tenant_occurred_pub",
        "session_event",
        ["tenant_id", sa.text("occurred_at DESC"), sa.text("pub_id DESC")],
        schema="platform",
    )
    op.create_index(
        "ix_session_event_account_occurred_pub",
        "session_event",
        ["tenant_id", "account_id", sa.text("occurred_at DESC"), sa.text("pub_id DESC")],
        schema="platform",
    )
    op.create_index(
        "ix_monitoring_schedule_tenant_created_pub",
        "monitoring_schedule",
        ["tenant_id", sa.text("created_at DESC"), sa.text("pub_id DESC")],
        schema="platform",
    )
    op.create_index(
        "ix_schedule_event_keyset",
        "monitoring_schedule_event",
        ["tenant_id", "schedule_id", sa.text("occurred_at DESC"), sa.text("pub_id DESC")],
        schema="platform",
    )
    op.create_index(
        "ix_run_comparison_project_keyset",
        "run_comparison",
        [
            "tenant_pub_id",
            "project_pub_id",
            sa.text("created_at DESC"),
            sa.text("pub_id DESC"),
        ],
        schema="analytics",
    )
    op.create_index(
        "ix_post_analysis_task_tenant_pub",
        "post_analysis_task",
        ["tenant_id", "pub_id"],
        schema="platform",
    )
    op.create_index(
        "ix_post_analysis_item_task_pub",
        "post_analysis_item",
        ["task_id", "pub_id"],
        schema="platform",
    )
    op.create_index(
        "ix_posting_batch_tenant_created_pub",
        "batch",
        ["tenant_pub_id", sa.text("created_at DESC"), sa.text("pub_id DESC")],
        schema="posting",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_posting_batch_tenant_created_pub",
        table_name="batch",
        schema="posting",
        if_exists=True,
    )
    op.drop_index(
        "ix_post_analysis_item_task_pub",
        table_name="post_analysis_item",
        schema="platform",
        if_exists=True,
    )
    op.drop_index(
        "ix_post_analysis_task_tenant_pub",
        table_name="post_analysis_task",
        schema="platform",
        if_exists=True,
    )
    op.drop_index(
        "ix_run_comparison_project_keyset",
        table_name="run_comparison",
        schema="analytics",
        if_exists=True,
    )
    op.drop_index(
        "ix_schedule_event_keyset",
        table_name="monitoring_schedule_event",
        schema="platform",
        if_exists=True,
    )
    op.drop_index(
        "ix_monitoring_schedule_tenant_created_pub",
        table_name="monitoring_schedule",
        schema="platform",
        if_exists=True,
    )
    op.drop_index(
        "ix_session_event_account_occurred_pub",
        table_name="session_event",
        schema="platform",
        if_exists=True,
    )
    op.drop_index(
        "ix_session_event_tenant_occurred_pub",
        table_name="session_event",
        schema="platform",
        if_exists=True,
    )
    op.drop_index(
        "ix_credential_access_tenant_created_pub",
        table_name="credential_access_request",
        schema="platform",
        if_exists=True,
    )
    op.drop_index(
        "ix_intervention_tenant_created_pub",
        table_name="intervention_request",
        schema="platform",
        if_exists=True,
    )
    op.drop_index(
        "ix_platform_account_tenant_created_pub",
        table_name="platform_account",
        schema="platform",
        if_exists=True,
    )
    op.drop_index(
        "ix_collection_account_event_platform_created_pub",
        table_name="collection_account_event",
        schema="platform",
        if_exists=True,
    )
    op.drop_index(
        "ix_collection_account_event_phone_created_pub",
        table_name="collection_account_event",
        schema="platform",
        if_exists=True,
    )
    op.drop_index(
        "ix_collection_browser_created_pub",
        table_name="collection_browser",
        schema="platform",
        if_exists=True,
    )
    op.drop_index(
        "ix_collection_phone_account_created_pub",
        table_name="collection_phone_account",
        schema="platform",
        if_exists=True,
    )
    op.drop_index(
        "ix_collection_run_project_created_pub",
        table_name="collection_run",
        schema="platform",
        if_exists=True,
    )
    op.drop_index(
        "ix_collection_run_tenant_created_pub",
        table_name="collection_run",
        schema="platform",
        if_exists=True,
    )
    op.drop_index(
        "ix_project_tenant_created_pub",
        table_name="project",
        schema="platform",
        if_exists=True,
    )
