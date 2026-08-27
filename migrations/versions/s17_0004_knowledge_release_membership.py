"""Bind materialized object and assertion versions to immutable releases.

Revision ID: s17_0004_release_membership
Revises: s17_0003_knowledge_immutable
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s17_0004_release_membership"
down_revision: str | Sequence[str] | None = "s17_0003_knowledge_immutable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rls(table: str) -> None:
    op.execute(f'ALTER TABLE knowledge."{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE knowledge."{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'''CREATE POLICY tenant_isolation ON knowledge."{table}"
        USING (tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), ''))
        WITH CHECK (tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), ''))'''
    )


def upgrade() -> None:
    op.create_table(
        "knowledge_release_object",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_pub_id", sa.String(30), nullable=False),
        sa.Column("namespace", sa.String(120), nullable=False),
        sa.Column("domain", sa.String(160), nullable=False),
        sa.Column("knowledge_release_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_object_id", sa.Uuid(), nullable=False),
        sa.Column("stable_id", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_release_id"],
            ["knowledge.knowledge_release.id"],
            name="fk_knowledge_release_object_release",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_object_id"],
            ["knowledge.knowledge_object.id"],
            name="fk_knowledge_release_object_object",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_release_object"),
        sa.UniqueConstraint(
            "knowledge_release_id",
            "stable_id",
            name="uq_knowledge_release_object_stable",
        ),
        sa.UniqueConstraint(
            "knowledge_release_id",
            "knowledge_object_id",
            name="uq_knowledge_release_object_member",
        ),
        schema="knowledge",
    )
    op.create_table(
        "knowledge_release_assertion",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_pub_id", sa.String(30), nullable=False),
        sa.Column("namespace", sa.String(120), nullable=False),
        sa.Column("domain", sa.String(160), nullable=False),
        sa.Column("knowledge_release_id", sa.Uuid(), nullable=False),
        sa.Column("assertion_id", sa.Uuid(), nullable=False),
        sa.Column("assertion_key", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_release_id"],
            ["knowledge.knowledge_release.id"],
            name="fk_knowledge_release_assertion_release",
        ),
        sa.ForeignKeyConstraint(
            ["assertion_id"],
            ["knowledge.assertion.id"],
            name="fk_knowledge_release_assertion_assertion",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_release_assertion"),
        sa.UniqueConstraint(
            "knowledge_release_id",
            "assertion_key",
            name="uq_knowledge_release_assertion_key",
        ),
        sa.UniqueConstraint(
            "knowledge_release_id",
            "assertion_id",
            name="uq_knowledge_release_assertion_member",
        ),
        schema="knowledge",
    )

    # Existing releases were created after their materialized rows in the same
    # transaction.  Reconstruct each historical release as the latest object or
    # assertion version that existed when that release record was created.
    op.execute(
        """
        INSERT INTO knowledge.knowledge_release_object (
          id, tenant_pub_id, namespace, domain, knowledge_release_id,
          knowledge_object_id, stable_id, created_at
        )
        SELECT md5(release.id::text || ':object:' || member.id::text)::uuid,
               release.tenant_pub_id, release.namespace, release.domain,
               release.id, member.id, member.stable_id, release.created_at
        FROM knowledge.knowledge_release AS release
        JOIN LATERAL (
          SELECT DISTINCT ON (object.stable_id) object.id, object.stable_id
          FROM knowledge.knowledge_object AS object
          WHERE object.tenant_pub_id = release.tenant_pub_id
            AND object.namespace = release.namespace
            AND object.domain = release.domain
            AND object.created_at <= release.created_at
          ORDER BY object.stable_id, object.version DESC, object.created_at DESC, object.id DESC
        ) AS member ON TRUE
        """
    )
    op.execute(
        """
        INSERT INTO knowledge.knowledge_release_assertion (
          id, tenant_pub_id, namespace, domain, knowledge_release_id,
          assertion_id, assertion_key, created_at
        )
        SELECT md5(release.id::text || ':assertion:' || member.id::text)::uuid,
               release.tenant_pub_id, release.namespace, release.domain,
               release.id, member.id, member.assertion_key, release.created_at
        FROM knowledge.knowledge_release AS release
        JOIN LATERAL (
          SELECT DISTINCT ON (assertion.assertion_key) assertion.id, assertion.assertion_key
          FROM knowledge.assertion AS assertion
          WHERE assertion.tenant_pub_id = release.tenant_pub_id
            AND assertion.namespace = release.namespace
            AND assertion.domain = release.domain
            AND assertion.created_at <= release.created_at
          ORDER BY assertion.assertion_key, assertion.version DESC,
                   assertion.created_at DESC, assertion.id DESC
        ) AS member ON TRUE
        """
    )

    for table in ("knowledge_release_object", "knowledge_release_assertion"):
        _rls(table)
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE "
            f'ON knowledge."{table}" '
            "FOR EACH ROW EXECUTE FUNCTION knowledge.reject_mutation()"
        )
    op.execute(
        """
        DO $$
        DECLARE role_name text;
        BEGIN
          FOREACH role_name IN ARRAY ARRAY['geo','geo_api','geo_worker'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
              EXECUTE format(
                'GRANT SELECT,INSERT ON knowledge.knowledge_release_object TO %I', role_name
              );
              EXECUTE format(
                'GRANT SELECT,INSERT ON knowledge.knowledge_release_assertion TO %I', role_name
              );
            END IF;
          END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM knowledge.knowledge_release_object LIMIT 1)
             OR EXISTS (SELECT 1 FROM knowledge.knowledge_release_assertion LIMIT 1) THEN
            RAISE EXCEPTION 'knowledge_release_membership_history_present_downgrade_refused';
          END IF;
        END $$;
        """
    )
    for table in ("knowledge_release_assertion", "knowledge_release_object"):
        op.execute(f"DROP TRIGGER trg_{table}_append_only ON knowledge.{table}")
        op.drop_table(table, schema="knowledge")
