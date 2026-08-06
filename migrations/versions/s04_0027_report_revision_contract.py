"""Add an idempotent immutable report revision contract.

Revision ID: s04_0027
Revises: s04_0026
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0027"
down_revision: str | Sequence[str] | None = "s04_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE reporting.report_version
          ADD COLUMN authoring_operation_hash TEXT,
          ADD COLUMN authoring_contract_hash TEXT;

        CREATE UNIQUE INDEX uq_report_version_authoring_operation
          ON reporting.report_version
            (tenant_pub_id,report_pub_id,authoring_operation_hash)
          WHERE authoring_operation_hash IS NOT NULL;

        ALTER TABLE reporting.report_version
          ADD CONSTRAINT ck_report_version_authoring_hash_pair
          CHECK (
            (authoring_operation_hash IS NULL AND authoring_contract_hash IS NULL)
            OR
            (
              authoring_operation_hash ~ '^[0-9a-f]{64}$'
              AND authoring_contract_hash ~ '^[0-9a-f]{64}$'
            )
          );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE reporting.report_version
          DROP CONSTRAINT IF EXISTS ck_report_version_authoring_hash_pair;
        DROP INDEX IF EXISTS reporting.uq_report_version_authoring_operation;
        ALTER TABLE reporting.report_version
          DROP COLUMN IF EXISTS authoring_contract_hash,
          DROP COLUMN IF EXISTS authoring_operation_hash;
        """
    )
