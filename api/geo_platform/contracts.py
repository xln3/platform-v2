from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Health(ContractModel):
    status: Literal["ok"]
    service: Literal["geo-platform-v2"] = "geo-platform-v2"
    version: str


class Readiness(ContractModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, Literal["configured", "unavailable"]]


class ApiErrorDetail(ContractModel):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class ApiError(ContractModel):
    error: ApiErrorDetail


class PageMeta(ContractModel):
    next_cursor: str | None
    has_more: bool


class ProjectSummary(ContractModel):
    pub_id: str = Field(pattern=r"^prj_[0-9A-HJKMNP-TV-Z]{26}$")
    tenant_pub_id: str = Field(pattern=r"^tnt_[0-9A-HJKMNP-TV-Z]{26}$")
    name: str
    state: Literal["draft", "active", "paused", "archived"]
    # 项目级 brandrank 规则包 domain 真源（s06_0014；None=未设置，读取回退缺省包）
    brandrank_domain: str | None = None
    created_at: datetime
    updated_at: datetime


class ProjectPage(ContractModel):
    data: list[ProjectSummary]
    page: PageMeta


class WorkflowAccepted(ContractModel):
    workflow_id: str
    run_id: str | None = None
    accepted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AuditEvent(ContractModel):
    event_id: str
    event_type: str
    tenant_pub_id: str
    actor_pub_id: str
    occurred_at: datetime
    resource_type: str
    resource_pub_id: str
    data: dict[str, Any] = Field(default_factory=dict)


class OutboxEnvelope(ContractModel):
    envelope_version: Literal["1.0"] = "1.0"
    event_id: str
    event_type: str
    occurred_at: datetime
    tenant_pub_id: str
    aggregate_type: str
    aggregate_pub_id: str
    trace_id: str
    payload: dict[str, Any]
