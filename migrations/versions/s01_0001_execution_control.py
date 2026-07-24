"""Create S01 execution-control domains and tenant RLS.

Revision ID: s01_0001
Revises: s00_0001
"""

from collections.abc import Sequence

from alembic import op
from geo_platform.collection import models as collection_models  # noqa: F401
from geo_platform.projects import models as project_models  # noqa: F401
from geo_platform.tenancy import models as tenancy_models  # noqa: F401
from geo_platform.tenancy.database import Base

revision: str = "s01_0001"
down_revision: str | Sequence[str] | None = "s00_0001"
branch_labels: str | Sequence[str] | None = ("s01",)
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = [
    "audit_log",
    "membership",
    "customer",
    "project",
    "brand",
    "brand_alias",
    "brand_asset",
    "competitor",
    "monitoring_config",
    "monitoring_config_version",
    "query_group",
    "query_item",
    "client_goal",
    "change_request",
    "platform_account",
    "account_authorization",
    "browser_profile",
    "session_lease",
    "resource_lease",
    "collection_run",
    "collection_task",
    "intervention_request",
    "session_event",
    "revocation_request",
]


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)
    for table in TENANT_TABLES:
        op.execute(f'ALTER TABLE platform."{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY tenant_isolation ON platform."{table}" '
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
