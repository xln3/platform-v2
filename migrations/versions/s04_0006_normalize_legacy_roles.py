"""Normalize legacy ACL labels to supported V2 product roles.

Revision ID: s04_0006
Revises: s04_0005
"""

from alembic import op

revision = "s04_0006"
down_revision = "s04_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE platform.membership
        SET role = CASE role
          WHEN 'owner' THEN 'admin'
          WHEN 'viewer' THEN 'customer'
          ELSE role
        END
        WHERE role IN ('owner', 'viewer')
        """
    )


def downgrade() -> None:
    # The original ACL label is deliberately not inferred from a valid V2 role.
    # Downgrading the schema leaves the semantically valid authorization intact.
    pass
