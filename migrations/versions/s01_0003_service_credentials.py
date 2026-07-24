"""Add scoped worker service credentials.

Revision ID: s01_0003
Revises: s01_0002
"""

from collections.abc import Sequence

from alembic import op
from geo_platform.collection import models as collection_models  # noqa: F401
from geo_platform.projects import models as project_models  # noqa: F401
from geo_platform.tenancy import models as tenancy_models  # noqa: F401
from geo_platform.tenancy.database import Base

revision: str = "s01_0003"
down_revision: str | Sequence[str] | None = "s01_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)
    op.execute('ALTER TABLE platform."service_credential" ENABLE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON platform."service_credential" '
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.drop_table("service_credential", schema="platform")
