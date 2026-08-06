import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import Tenant


def set_tenant_context(session: Session, *, tenant_id: uuid.UUID, tenant_pub_id: str) -> None:
    """Set transaction-local RLS selectors for both platform and S02 schemas."""
    session.execute(
        text(
            "SELECT set_config('app.tenant_id', :tenant_id, true), "
            "set_config('app.tenant_pub_id', :tenant_pub_id, true)"
        ),
        {"tenant_id": str(tenant_id), "tenant_pub_id": tenant_pub_id},
    )


class TenantRepository:
    def __init__(self, session: Session, tenant_pub_id: str) -> None:
        self.session = session
        tenant = session.scalar(select(Tenant).where(Tenant.pub_id == tenant_pub_id))
        if tenant is None:
            raise LookupError("tenant_not_found")
        self.tenant: Tenant = tenant
        set_tenant_context(
            session,
            tenant_id=self.tenant.id,
            tenant_pub_id=self.tenant.pub_id,
        )
