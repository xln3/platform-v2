from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import Tenant


class TenantRepository:
    def __init__(self, session: Session, tenant_pub_id: str) -> None:
        self.session = session
        tenant = session.scalar(select(Tenant).where(Tenant.pub_id == tenant_pub_id))
        if tenant is None:
            raise LookupError("tenant_not_found")
        self.tenant: Tenant = tenant
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(self.tenant.id)},
        )
