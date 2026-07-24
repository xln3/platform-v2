# ruff: noqa: B008

import hashlib
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..identity.policy import Principal, get_principal
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.models import AuditLog
from ..tenancy.repository import TenantRepository
from .models import Customer

router = APIRouter(prefix="/api/v2/customers", tags=["customers"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CustomerCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    external_ref: str | None = Field(default=None, max_length=200)


class CustomerPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    external_ref: str | None = Field(default=None, max_length=200)
    expected_version: int = Field(ge=1)


class CustomerView(StrictModel):
    pub_id: str
    name: str
    external_ref: str | None
    version: int


class CustomerPage(StrictModel):
    data: list[CustomerView]
    next_cursor: str | None


def view(customer: Customer) -> CustomerView:
    return CustomerView(
        pub_id=customer.pub_id,
        name=customer.name,
        external_ref=customer.external_ref,
        version=customer.version,
    )


@router.get("", response_model=CustomerPage)
def list_customers(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> CustomerPage:
    principal.require("project:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    statement = select(Customer).where(Customer.tenant_id == repository.tenant.id)
    if cursor:
        statement = statement.where(Customer.pub_id > cursor)
    rows = session.scalars(statement.order_by(Customer.pub_id).limit(limit + 1)).all()
    has_more = len(rows) > limit
    return CustomerPage(
        data=[view(item) for item in rows[:limit]],
        next_cursor=rows[limit - 1].pub_id if has_more else None,
    )


@router.post("", response_model=CustomerView, status_code=201)
def create_customer(
    body: CustomerCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=16, max_length=128),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> CustomerView:
    principal.require("project:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    action = f"customer.created:{hashlib.sha256(idempotency_key.encode()).hexdigest()}"
    prior = session.scalar(
        select(AuditLog).where(
            AuditLog.tenant_id == repository.tenant.id, AuditLog.action == action
        )
    )
    if prior:
        customer = session.scalar(
            select(Customer).where(
                Customer.tenant_id == repository.tenant.id,
                Customer.pub_id == json.loads(prior.receipt)["customer_pub_id"],
            )
        )
        assert customer is not None
        return view(customer)
    customer = Customer(
        pub_id=new_pub_id("cst"),
        tenant_id=repository.tenant.id,
        name=body.name,
        external_ref=body.external_ref,
    )
    session.add(customer)
    session.flush()
    session.add(
        AuditLog(
            pub_id=new_pub_id("aud"),
            tenant_id=repository.tenant.id,
            actor_pub_id=principal.subject,
            action=action,
            resource_type="customer",
            resource_pub_id=customer.pub_id,
            receipt=json.dumps({"customer_pub_id": customer.pub_id}),
        )
    )
    session.commit()
    return view(customer)


@router.patch("/{customer_pub_id}", response_model=CustomerView)
def update_customer(
    customer_pub_id: str,
    body: CustomerPatch,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> CustomerView:
    principal.require("project:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    customer = session.scalar(
        select(Customer).where(
            Customer.tenant_id == repository.tenant.id,
            Customer.pub_id == customer_pub_id,
        )
    )
    if customer is None:
        raise HTTPException(status_code=404, detail={"code": "customer_not_found"})
    if customer.version != body.expected_version:
        raise HTTPException(status_code=409, detail={"code": "version_conflict"})
    if body.name is not None:
        customer.name = body.name
    if "external_ref" in body.model_fields_set:
        customer.external_ref = body.external_ref
    customer.version += 1
    session.commit()
    return view(customer)
