# ruff: noqa: B008

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.evidence.dlp import assert_secret_free
from domain.reporting.artifacts import render_docx, render_xlsx

from ..identity.policy import Principal, get_principal
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.models import AuditLog
from ..tenancy.repository import TenantRepository
from .models import (
    AssetConfirmationVersion,
    Brand,
    ClientGoal,
    ClientProfileVersion,
    Competitor,
    Customer,
    MonitoringConfig,
    MonitoringConfigVersion,
    Project,
    QueryGroup,
    QueryItem,
)

router = APIRouter(prefix="/api/v2/onboarding", tags=["onboarding"])
DocumentKind = Literal["mvp", "measurement-requirements"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OnboardingCreate(StrictModel):
    customer_name: str = Field(min_length=2, max_length=200)
    project_name: str = Field(min_length=2, max_length=200)
    contact_role: str = Field(min_length=2, max_length=120)
    audience: str = Field(min_length=10, max_length=4000)
    public_statement: str = Field(min_length=10, max_length=4000)
    brand_name: str = Field(min_length=2, max_length=200)
    website: HttpUrl
    product_name: str = Field(min_length=2, max_length=200)
    competitors: list[str] = Field(min_length=1, max_length=20)
    prohibited_claim: str = Field(min_length=5, max_length=4000)
    goal: str = Field(min_length=5, max_length=4000)
    questions: list[str] = Field(min_length=1, max_length=100)
    models: list[Literal["doubao", "deepseek", "yiyan", "tongyi", "yuanbao"]] = Field(
        min_length=1, max_length=5
    )
    regions: list[str] = Field(min_length=1, max_length=20)
    frequency: Literal["one-off", "daily", "weekly", "monthly"] = "one-off"
    truth_confirmed: bool

    @field_validator(
        "customer_name",
        "project_name",
        "contact_role",
        "audience",
        "public_statement",
        "brand_name",
        "product_name",
        "prohibited_claim",
        "goal",
    )
    @classmethod
    def reject_secret_content(cls, value: str) -> str:
        normalized = value.strip()
        assert_secret_free(normalized)
        return normalized

    @field_validator("competitors", "questions", "regions")
    @classmethod
    def normalize_lists(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len(normalized) != len(values):
            raise ValueError("blank list item")
        for value in normalized:
            assert_secret_free(value)
        return normalized

    @field_validator("truth_confirmed")
    @classmethod
    def require_truth_confirmation(cls, value: bool) -> bool:
        if not value:
            raise ValueError("truth confirmation is required")
        return value


class OnboardingView(StrictModel):
    customer_pub_id: str
    project_pub_id: str
    config_version_pub_id: str
    config_revision: int
    task_count: int
    mvp_document_url: str
    measurement_requirements_url: str


def _onboarding_view(receipt: dict[str, object]) -> OnboardingView:
    project_pub_id = str(receipt["project_pub_id"])
    return OnboardingView(
        customer_pub_id=str(receipt["customer_pub_id"]),
        project_pub_id=project_pub_id,
        config_version_pub_id=str(receipt["config_version_pub_id"]),
        config_revision=int(str(receipt["config_revision"])),
        task_count=int(str(receipt["task_count"])),
        mvp_document_url=f"/api/v2/onboarding/{project_pub_id}/documents/mvp",
        measurement_requirements_url=(
            f"/api/v2/onboarding/{project_pub_id}/documents/measurement-requirements"
        ),
    )


@router.post("", response_model=OnboardingView, status_code=201)
def create_onboarding(
    body: OnboardingCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=16, max_length=128),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> OnboardingView:
    principal.require("project:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    action = f"onboarding.created:{hashlib.sha256(idempotency_key.encode()).hexdigest()}"
    payload_json = json.dumps(body.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
    prior = session.scalar(
        select(AuditLog).where(
            AuditLog.tenant_id == repository.tenant.id,
            AuditLog.action == action,
        )
    )
    if prior is not None:
        prior_receipt = json.loads(prior.receipt)
        if prior_receipt.get("payload_hash") != payload_hash:
            raise HTTPException(status_code=409, detail={"code": "idempotency_payload_mismatch"})
        return _onboarding_view(prior_receipt)

    customer = Customer(
        pub_id=new_pub_id("cst"),
        tenant_id=repository.tenant.id,
        name=body.customer_name,
        external_ref=None,
    )
    session.add(customer)
    session.flush()
    project = Project(
        pub_id=new_pub_id("prj"),
        tenant_id=repository.tenant.id,
        customer_id=customer.id,
        name=body.project_name,
    )
    session.add(project)
    session.flush()
    session.add(
        ClientProfileVersion(
            pub_id=new_pub_id("cpv"),
            tenant_id=repository.tenant.id,
            project_id=project.id,
            revision=1,
            company_name=body.customer_name,
            contact_role=body.contact_role,
            audience=body.audience,
            public_statement=body.public_statement,
            declared_by=principal.actor_pub_id,
        )
    )
    session.add(
        AssetConfirmationVersion(
            pub_id=new_pub_id("acv"),
            tenant_id=repository.tenant.id,
            project_id=project.id,
            revision=1,
            brand_name=body.brand_name,
            website=str(body.website),
            product_name=body.product_name,
            competitor_name=body.competitors[0],
            prohibited_claim=body.prohibited_claim,
            declared_by=principal.actor_pub_id,
        )
    )
    session.add(
        Brand(
            pub_id=new_pub_id("ent"),
            tenant_id=repository.tenant.id,
            project_id=project.id,
            name=body.brand_name,
            website=str(body.website),
        )
    )
    session.add_all(
        [
            Competitor(
                pub_id=new_pub_id("ent"),
                tenant_id=repository.tenant.id,
                project_id=project.id,
                name=name,
                website=None,
            )
            for name in body.competitors
        ]
    )
    session.add(
        ClientGoal(
            pub_id=new_pub_id("ent"),
            tenant_id=repository.tenant.id,
            project_id=project.id,
            metric="mvp_goal",
            target_json=json.dumps({"description": body.goal}, ensure_ascii=False),
            state="confirmed",
        )
    )
    query_group = QueryGroup(
        pub_id=new_pub_id("ent"),
        tenant_id=repository.tenant.id,
        project_id=project.id,
        name="首版评测问题",
    )
    session.add(query_group)
    session.flush()
    session.add_all(
        [
            QueryItem(
                pub_id=new_pub_id("ent"),
                tenant_id=repository.tenant.id,
                group_id=query_group.id,
                text=question,
                priority=index,
            )
            for index, question in enumerate(body.questions, 1)
        ]
    )
    config = MonitoringConfig(
        pub_id=new_pub_id("cfg"),
        tenant_id=repository.tenant.id,
        project_id=project.id,
        state="frozen",
        current_version=1,
    )
    session.add(config)
    session.flush()
    effective_at = datetime.now(UTC)
    snapshot = {
        "query_groups": [
            {
                "name": "首版评测问题",
                "items": [
                    {"text": question, "priority": index}
                    for index, question in enumerate(body.questions, 1)
                ],
            }
        ],
        "regions": body.regions,
        "models": body.models,
        "modes": ["web"],
        "frequency": body.frequency,
        "effective_at": effective_at.isoformat(),
    }
    snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    config_version = MonitoringConfigVersion(
        pub_id=new_pub_id("cfv"),
        tenant_id=repository.tenant.id,
        config_id=config.id,
        revision=1,
        effective_at=effective_at,
        frozen_at=effective_at,
        snapshot_json=snapshot_json,
        snapshot_hash=hashlib.sha256(snapshot_json.encode()).hexdigest(),
    )
    session.add(config_version)
    session.flush()
    task_count = len(body.questions) * len(body.models) * len(body.regions)
    receipt: dict[str, object] = {
        "payload_hash": payload_hash,
        "customer_pub_id": customer.pub_id,
        "project_pub_id": project.pub_id,
        "config_version_pub_id": config_version.pub_id,
        "config_revision": 1,
        "task_count": task_count,
    }
    session.add(
        AuditLog(
            pub_id=new_pub_id("aud"),
            tenant_id=repository.tenant.id,
            actor_pub_id=principal.actor_pub_id,
            action=action,
            resource_type="onboarding",
            resource_pub_id=project.pub_id,
            receipt=json.dumps(receipt, ensure_ascii=False),
        )
    )
    session.commit()
    return _onboarding_view(receipt)


def _project_bundle(
    session: Session, tenant_id: object, project_pub_id: str
) -> tuple[Project, Customer, ClientProfileVersion, AssetConfirmationVersion, dict[str, Any]]:
    project = session.scalar(
        select(Project).where(Project.tenant_id == tenant_id, Project.pub_id == project_pub_id)
    )
    if project is None:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"})
    customer = session.get(Customer, project.customer_id)
    profile = session.scalar(
        select(ClientProfileVersion)
        .where(
            ClientProfileVersion.tenant_id == tenant_id,
            ClientProfileVersion.project_id == project.id,
        )
        .order_by(ClientProfileVersion.revision.desc())
        .limit(1)
    )
    assets = session.scalar(
        select(AssetConfirmationVersion)
        .where(
            AssetConfirmationVersion.tenant_id == tenant_id,
            AssetConfirmationVersion.project_id == project.id,
        )
        .order_by(AssetConfirmationVersion.revision.desc())
        .limit(1)
    )
    config_version = session.scalar(
        select(MonitoringConfigVersion)
        .join(MonitoringConfig, MonitoringConfig.id == MonitoringConfigVersion.config_id)
        .where(
            MonitoringConfigVersion.tenant_id == tenant_id,
            MonitoringConfig.project_id == project.id,
        )
        .order_by(MonitoringConfigVersion.revision.desc())
        .limit(1)
    )
    if customer is None or profile is None or assets is None or config_version is None:
        raise HTTPException(status_code=409, detail={"code": "onboarding_incomplete"})
    try:
        snapshot: dict[str, Any] = json.loads(config_version.snapshot_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=409, detail={"code": "config_snapshot_invalid"}) from exc
    return project, customer, profile, assets, snapshot


@router.get("/{project_pub_id}/documents/{kind}")
def download_onboarding_document(
    project_pub_id: str,
    kind: DocumentKind,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> Response:
    principal.require("project:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project, customer, profile, assets, snapshot = _project_bundle(
        session, repository.tenant.id, project_pub_id
    )
    question_groups = snapshot.get("query_groups", [])
    questions = [
        item
        for group in question_groups
        if isinstance(group, dict)
        for item in group.get("items", [])
        if isinstance(item, dict)
    ]
    models = [str(value) for value in snapshot.get("models", [])]
    regions = [str(value) for value in snapshot.get("regions", [])]
    if kind == "mvp":
        competitors = session.scalars(
            select(Competitor).where(
                Competitor.tenant_id == repository.tenant.id,
                Competitor.project_id == project.id,
            )
        ).all()
        goals = session.scalars(
            select(ClientGoal).where(
                ClientGoal.tenant_id == repository.tenant.id,
                ClientGoal.project_id == project.id,
            )
        ).all()
        goal_text = "；".join(
            str(json.loads(goal.target_json).get("description", goal.metric)) for goal in goals
        )
        sections = [
            {"title": "客户与项目", "body": f"客户：{customer.name}\n项目：{project.name}"},
            {
                "title": "品牌与产品",
                "body": (
                    f"品牌：{assets.brand_name}\n产品：{assets.product_name}\n官网：{assets.website}"
                ),
            },
            {
                "title": "目标受众与公开口径",
                "body": f"目标受众：{profile.audience}\n公开口径：{profile.public_statement}",
            },
            {"title": "GEO MVP 目标", "body": goal_text or "待确认"},
            {
                "title": "首版评测范围",
                "body": (
                    f"问题数：{len(questions)}\n平台：{'、'.join(models)}\n"
                    f"地域：{'、'.join(regions)}\n频率：{snapshot.get('frequency', '')}"
                ),
            },
            {
                "title": "竞品与风险边界",
                "body": (
                    f"竞品：{'、'.join(item.name for item in competitors)}\n"
                    f"禁止口径：{assets.prohibited_claim}"
                ),
            },
        ]
        payload = render_docx(f"{customer.name} GEO MVP 项目文档", sections)
        extension = "docx"
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        chinese_name = f"{customer.name}_GEO_MVP项目文档.docx"
    else:
        rows = [
            {
                "问题组": str(group.get("name", "")),
                "评测问题": str(item.get("text", "")),
                "优先级": int(item.get("priority", 100)),
                "AI平台": "、".join(models),
                "地域": "、".join(regions),
                "模式": "、".join(str(value) for value in snapshot.get("modes", [])),
                "频率": str(snapshot.get("frequency", "")),
                "生效时间": str(snapshot.get("effective_at", "")),
            }
            for group in question_groups
            if isinstance(group, dict)
            for item in group.get("items", [])
            if isinstance(item, dict)
        ]
        if not rows:
            raise HTTPException(status_code=409, detail={"code": "measurement_questions_missing"})
        payload = render_xlsx(rows)
        extension = "xlsx"
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        chinese_name = f"{customer.name}_GEO评测需求表.xlsx"
    ascii_name = f"geo-{kind}-{project.pub_id}.{extension}"
    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(chinese_name)}"
            ),
            "Cache-Control": "private, no-store",
        },
    )
