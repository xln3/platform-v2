"""Add S01 resource and credential-access governance.

Revision ID: s01_0002
Revises: s01_0001
"""

from collections.abc import Sequence

from alembic import op
from geo_platform.collection import models as collection_models  # noqa: F401
from geo_platform.projects import models as project_models  # noqa: F401
from geo_platform.tenancy import models as tenancy_models  # noqa: F401
from geo_platform.tenancy.database import Base

revision: str = "s01_0002"
down_revision: str | Sequence[str] | None = "s01_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_TENANT_TABLES = [
    "resource_registration",
    "session_health_check",
    "credential_access_request",
    "credential_access_approval",
]


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)
    for table in NEW_TENANT_TABLES:
        op.execute(f'ALTER TABLE platform."{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY tenant_isolation ON platform."{table}" '
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )


def downgrade() -> None:
    for table in reversed(NEW_TENANT_TABLES):
        op.drop_table(table, schema="platform")
