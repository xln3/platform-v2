"""Add cross-service scoped capability leases.

Revision ID: s01_0005
Revises: s01_0004
"""

from collections.abc import Sequence

from alembic import op
from geo_platform.collection import models as collection_models  # noqa: F401
from geo_platform.tenancy.database import Base

revision: str = "s01_0005"
down_revision: str | Sequence[str] | None = "s01_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)
    op.execute('ALTER TABLE platform."capability_lease" ENABLE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON platform."capability_lease" '
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.drop_table("capability_lease", schema="platform")
