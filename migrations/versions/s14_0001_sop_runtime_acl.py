"""Grant the production runtime roles the minimum SOP catalog privileges.

The SOP catalog predates the non-superuser ``geo_api`` and ``geo_worker``
roles.  Provisioning a fresh runtime role repairs those privileges, but an
already provisioned production database must not depend on an out-of-band
role setup command.  Keep the repair in the immutable migration chain.

Revision ID: s14_0001_sop_runtime_acl
Revises: s13_0001_service2_query_outcomes
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s14_0001_sop_runtime_acl"
down_revision: str | Sequence[str] | None = "s13_0001_service2_query_outcomes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOP_TABLES = (
    "project",
    "query_set",
    "query_item",
    "baseline_answer",
    "retrieval_insight",
    "evidence_item",
    "opportunity",
    "article",
    "article_version",
    "pre_publish_check",
    "publication",
    "index_observation",
    "retest_answer",
    "comparison",
    "experiment",
    "work_log",
)


def _table_list() -> str:
    return ",".join(f"sop.{table}" for table in _SOP_TABLES)


def upgrade() -> None:
    tables = _table_list()
    op.execute(
        f"""
        DO $acl$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            GRANT USAGE ON SCHEMA sop TO geo_api;
            GRANT SELECT,INSERT,UPDATE ON TABLE {tables} TO geo_api;
            GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA sop TO geo_api;
          END IF;

          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            GRANT USAGE ON SCHEMA sop TO geo_worker;
            GRANT SELECT ON TABLE {tables} TO geo_worker;
          END IF;
        END
        $acl$
        """
    )


def downgrade() -> None:
    tables = _table_list()
    op.execute(
        f"""
        DO $acl$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
            REVOKE ALL ON TABLE {tables} FROM geo_api;
            REVOKE ALL ON ALL SEQUENCES IN SCHEMA sop FROM geo_api;
            REVOKE USAGE ON SCHEMA sop FROM geo_api;
          END IF;

          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            REVOKE ALL ON TABLE {tables} FROM geo_worker;
            REVOKE USAGE ON SCHEMA sop FROM geo_worker;
          END IF;
        END
        $acl$
        """
    )


__all__ = ["downgrade", "upgrade"]
