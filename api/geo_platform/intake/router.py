# ruff: noqa: B008
"""Intake（客户信息收集表）REST：profile/promo/trigger + AI 联网调研预填 + docx 导出。

纪律：
  * 端点第一行 principal.require("intake:read"|"intake:write")；跨租户一律 404；
  * 词表 fail-closed 校验全部在 schemas 层（违规 422），路由不再重复校验；
  * 写操作全写 AuditLog（confirmation_router 惯例：action=`<resource>.<past-tense>:<sha256>`，
    POST 创建类经 Idempotency-Key 兼幂等键、写前查 AuditLog 重放）；
  * GET /api/v2/intake/form-schema 公开免鉴权（纯 schema + 词表，零租户数据）。
"""

import datetime
import hashlib
import json
from dataclasses import replace
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..identity.policy import Principal, get_principal
from ..projects.models import Project
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.models import AuditLog
from ..tenancy.repository import TenantRepository
from . import contract, models, research, schemas, service

router = APIRouter(prefix="/api/v2/projects", tags=["intake"])
public_router = APIRouter(prefix="/api/v2/intake", tags=["intake"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)]


# ══ 收集表公开 schema（未登录也可查看完整表单内容；纯静态 schema + models 词表，零租户数据）══
# 结构对齐合同附件二《GEO 客户信息收集表（通用版）》两大部分；词表一律从 models 单一真源取。
def _opt(value: str, label: str | None = None) -> dict[str, str]:
    return {"value": value, "label": label or value}


_FORM_SCHEMA: list[dict[str, Any]] = [
    {
        "id": "promo",
        "title": "第一部分　宣传内容与目标",
        "fields": [
            {"key": "company_name", "label": "公司 / 品牌名称", "type": "text", "required": True},
            {
                "key": "industry",
                "label": "所属行业",
                "type": "text",
                "required": True,
                "hint": "如 消费品 / 企业服务 / 医药健康 / 金融保险 / 教育 / 文化娱乐 / 房地产 / "
                "招商加盟 / 电信 / 人力资源 / 其他",
            },
            {
                "key": "review_category",
                "label": "行业广告审查分类",
                "type": "radio",
                "required": True,
                "options": [
                    _opt(v, models.REVIEW_CATEGORY_LABELS[v]) for v in models.REVIEW_CATEGORIES
                ],
            },
            {
                "key": "pre_review_required",
                "label": "是否属于法定前置审查行业",
                "type": "bool",
                "required": True,
                "hint": "选「是」须提供广告审查批准文件，并填写下方广告审查批准文号栏",
            },
            {
                "key": "ad_review_no",
                "label": "广告审查批准文号",
                "type": "text",
                "hint": "仅 A 类行业必填；B 类行业填写行业准入资质编号",
            },
            {"key": "ad_review_authority", "label": "审查机关", "type": "text"},
            {"key": "ad_review_expiry", "label": "有效期至", "type": "date"},
            {"key": "contact_person", "label": "联系人", "type": "text", "required": True},
            {
                "key": "contact_info",
                "label": "联系方式（手机 / 微信 / 邮箱）",
                "type": "text",
                "required": True,
            },
            {
                "key": "promos",
                "label": "拟推广产品 / 服务",
                "type": "subform",
                "required": True,
                "hint": "名称 + 一句话介绍（每次合作聚焦 1-3 个）",
            },
            {
                "key": "goals",
                "label": "推广目标",
                "type": "chips",
                "required": True,
                "options": [_opt(v) for v in models.GOALS],
            },
            {
                "key": "trigger_questions",
                "label": "期望的用户提问场景",
                "type": "textarea",
                "required": True,
                "hint": "这是方案设计的最重要输入。用户会怎么问 AI？请写 3-5 条，每行一条，"
                "例：「预算 3000 的扫地机器人怎么选」",
            },
            {
                "key": "platforms",
                "label": "目标 AI 平台",
                "type": "chips",
                "options": [_opt(v) for v in models.PLATFORMS],
                "hint": "也可交由我方建议",
            },
            {
                "key": "regions",
                "label": "重点地域",
                "type": "tags",
                "hint": "全国 或 重点区域（如 华东, 上海）",
            },
            {
                "key": "selling_points",
                "label": "核心卖点",
                "type": "textarea",
                "required": True,
                "hint": "与同类相比，为什么应该推荐您？200 字以内，每一条卖点需有出处"
                "（认证、数据、案例等）。无佐证材料的表述将无法用于内容投放。",
            },
            {
                "key": "evidence_links",
                "label": "可公开引用的佐证材料",
                "type": "textarea",
                "hint": "官网、检测报告、权威媒体报道、行业奖项等，每行一条链接或说明",
            },
        ],
    },
    {
        "id": "qualification",
        "title": "第二部分　资质",
        "fields": [
            {
                "key": "business_license_code",
                "label": "营业执照 · 统一社会信用代码",
                "type": "text",
                "required": True,
                "hint": "18 位（0-9/A-Z），扫描件线下交运营方归档",
            },
            {
                "key": "licenses",
                "label": "行业许可证（须持证经营行业必填）",
                "type": "subform",
                "hint": "医药、医疗、金融、保险、教育、房地产、电信、人力资源、食品经营等"
                "须持证经营行业必填；证照名称 / 编号 / 有效期至",
            },
            {
                "key": "trademarks",
                "label": "商标 / 品牌权属证明",
                "type": "tags",
                "hint": "商标注册证或授权文件（选填，有助于提升内容可信度）",
            },
            {
                "key": "ad_review_doc_types",
                "label": "广告审查批准文件（A 类行业必填）",
                "type": "chips",
                "options": [_opt(v) for v in models.AD_REVIEW_DOC_TYPES],
            },
            {
                "key": "truth_confirmed",
                "label": "信息真实性确认",
                "type": "confirm",
                "required": True,
                "items": list(models.TRUTH_CONFIRM_ITEMS),
            },
            {
                "key": "filler_name",
                "label": "填表人",
                "type": "text",
                "hint": "网页版以勾选提交代替签字",
            },
        ],
    },
]


@public_router.get("/form-schema")
def form_schema() -> dict[str, Any]:
    """公开只读：收集表完整结构（分节/字段/选项/必填/提示）。无鉴权——
    内容=合同附件二栏目 + models 词表，零租户数据；写接口全部要鉴权。"""
    return {
        "title": "GEO 客户信息收集表（通用版）",
        "note": "填写约需 10 分钟。标注 ★ 为必填项。"
        "本表信息用于制定后续 GEO 方案，我方承担保密义务。",
        "sections": _FORM_SCHEMA,
    }


# ── 公共助手 ─────────────────────────────────────────────────────────────
def _project(
    session: Session, tenant_id: Any, project_pub_id: str, *, lock: bool = False
) -> Project:
    statement = select(Project).where(
        Project.tenant_id == tenant_id, Project.pub_id == project_pub_id
    )
    if lock:
        statement = statement.with_for_update()
    project = session.scalar(statement)
    if project is None:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"})
    return project


def _audit(
    session: Session,
    *,
    tenant_id: Any,
    principal: Principal,
    action: str,
    resource_type: str,
    resource_pub_id: str,
    receipt: dict[str, Any],
) -> None:
    session.add(
        AuditLog(
            pub_id=new_pub_id("aud"),
            tenant_id=tenant_id,
            actor_pub_id=principal.actor_pub_id,
            action=action,
            resource_type=resource_type,
            resource_pub_id=resource_pub_id,
            receipt=json.dumps(receipt, ensure_ascii=False),
        )
    )


def _action(resource: str, verb: str, *parts: str) -> str:
    digest = hashlib.sha256(":".join(parts).encode()).hexdigest()
    return f"{resource}.{verb}:{digest}"


def _prior_audit(session: Session, *, tenant_id: Any, action: str) -> AuditLog | None:
    return session.scalar(
        select(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.action == action)
    )


def _profile_view(profile: models.IntakeProfile | None, project: Project) -> schemas.ProfileView:
    def scalar(name: str) -> str | None:
        return getattr(profile, name) if profile else None

    def listed(name: str) -> list[str]:
        return list(getattr(profile, name)) if profile else []

    return schemas.ProfileView(
        project_pub_id=project.pub_id,
        exists=profile is not None,
        prefilled=dict(profile.prefilled) if profile else {},
        updated_at=profile.updated_at.isoformat() if profile else None,
        contact_person=scalar("contact_person"),
        contact_info=scalar("contact_info"),
        website=scalar("website"),
        wechat=scalar("wechat"),
        douyin=scalar("douyin"),
        social_media=scalar("social_media"),
        audience_desc=scalar("audience_desc"),
        business_license_code=scalar("business_license_code"),
        selling_points=scalar("selling_points"),
        filler_name=scalar("filler_name"),
        ad_review_no=scalar("ad_review_no"),
        ad_review_authority=scalar("ad_review_authority"),
        ad_review_expiry=scalar("ad_review_expiry"),
        review_category=scalar("review_category"),
        pre_review_required=(profile.pre_review_required if profile else None),
        truth_confirmed=(profile.truth_confirmed if profile else None),
        goals=listed("goals"),
        audience_type=listed("audience_type"),
        platforms=listed("platforms"),
        regions=listed("regions"),
        trademarks=listed("trademarks"),
        ad_review_doc_types=listed("ad_review_doc_types"),
        evidence_links=listed("evidence_links"),
        licenses=[dict(x) for x in profile.licenses] if profile else [],
    )


def _promo_view(promo: models.IntakePromo) -> schemas.PromoView:
    return schemas.PromoView(
        pub_id=promo.pub_id,
        kind=promo.kind,
        payload=dict(promo.payload),
        created_at=promo.created_at.isoformat(),
        updated_at=promo.updated_at.isoformat(),
    )


def _trigger_view(row: models.IntakeTriggerQuestion) -> schemas.TriggerView:
    return schemas.TriggerView(
        pub_id=row.pub_id,
        text=row.text,
        status=row.status,
        created_at=row.created_at.isoformat(),
    )


# ── profile 读写（project 1:1；PUT=部分更新，只写 body 里出现的字段）──────────────
@router.get("/{project_pub_id}/intake/profile", response_model=schemas.ProfileView)
def get_intake_profile(
    project_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> schemas.ProfileView:
    principal.require("intake:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id)
    profile = service.get_profile(session, tenant_id=repository.tenant.id, project_id=project.id)
    return _profile_view(profile, project)


@router.put("/{project_pub_id}/intake/profile", response_model=schemas.ProfileView)
def put_intake_profile(
    project_pub_id: str,
    body: schemas.ProfileUpdate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> schemas.ProfileView:
    principal.require("intake:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id, lock=True)
    sets = body.model_dump(exclude_unset=True)
    touched = sorted(sets)
    canonical = json.dumps(sets, ensure_ascii=False, sort_keys=True)
    action = _action("intake_profile", "updated", project.pub_id, canonical)
    if _prior_audit(session, tenant_id=repository.tenant.id, action=action) is not None:
        # 幂等重放：同 payload 重复 PUT → 直接回当前视图，不重复写审计。
        profile = service.get_profile(
            session, tenant_id=repository.tenant.id, project_id=project.id
        )
        return _profile_view(profile, project)
    profile = service.upsert_profile(
        session,
        tenant_id=repository.tenant.id,
        project_id=project.id,
        sets=sets,
        clear_prefill=touched,
    )
    _audit(
        session,
        tenant_id=repository.tenant.id,
        principal=principal,
        action=action,
        resource_type="intake_profile",
        resource_pub_id=profile.pub_id,
        receipt={"project_pub_id": project.pub_id, "fields": touched},
    )
    session.commit()
    return _profile_view(profile, project)


# ── promo CRUD（推广内容子表；payload 形状按 kind fail-closed 校验）────────────────
@router.get("/{project_pub_id}/intake/promos", response_model=schemas.PromoListView)
def list_intake_promos(
    project_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> schemas.PromoListView:
    principal.require("intake:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id)
    rows = service.list_promos(session, tenant_id=repository.tenant.id, project_id=project.id)
    return schemas.PromoListView(items=[_promo_view(r) for r in rows])


@router.post(
    "/{project_pub_id}/intake/promos",
    response_model=schemas.PromoView,
    status_code=201,
)
def create_intake_promo(
    project_pub_id: str,
    body: schemas.PromoCreate,
    idempotency_key: IdempotencyKey,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> schemas.PromoView:
    principal.require("intake:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id, lock=True)
    canonical = body.model_dump_json()
    payload_hash = hashlib.sha256(canonical.encode()).hexdigest()
    action = _action("intake_promo", "created", project.pub_id, idempotency_key)
    prior = _prior_audit(session, tenant_id=repository.tenant.id, action=action)
    if prior is not None:
        receipt = json.loads(prior.receipt)
        if receipt.get("payload_hash") != payload_hash:
            raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"})
        existing = service.get_promo(
            session,
            tenant_id=repository.tenant.id,
            project_id=project.id,
            promo_pub_id=prior.resource_pub_id,
        )
        if existing is None:
            raise HTTPException(status_code=409, detail={"code": "idempotency_receipt_invalid"})
        return _promo_view(existing)
    promo = service.create_promo(
        session,
        tenant_id=repository.tenant.id,
        project_id=project.id,
        kind=body.kind,
        payload=body.payload,
    )
    # 用户手动加推广内容 → AI 草稿标清除（转用户数据）
    service.clear_prefill_marks(
        session, tenant_id=repository.tenant.id, project_id=project.id, keys=("promos",)
    )
    _audit(
        session,
        tenant_id=repository.tenant.id,
        principal=principal,
        action=action,
        resource_type="intake_promo",
        resource_pub_id=promo.pub_id,
        receipt={"project_pub_id": project.pub_id, "payload_hash": payload_hash},
    )
    session.commit()
    return _promo_view(promo)


@router.patch("/{project_pub_id}/intake/promos/{promo_pub_id}", response_model=schemas.PromoView)
def patch_intake_promo(
    project_pub_id: str,
    promo_pub_id: str,
    body: schemas.PromoUpdate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> schemas.PromoView:
    principal.require("intake:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id)
    promo = service.get_promo(
        session,
        tenant_id=repository.tenant.id,
        project_id=project.id,
        promo_pub_id=promo_pub_id,
    )
    if promo is None:
        raise HTTPException(status_code=404, detail={"code": "promo_not_found"})
    kind = body.kind or promo.kind
    if body.payload is not None or kind != promo.kind:
        new_payload = schemas.validate_promo_payload(kind, body.payload or {}, partial=True)
        if kind != promo.kind:
            merged = new_payload  # 换 kind=换 payload 形状：整体替换
        else:
            merged = dict(promo.payload)
            merged.update(new_payload)
        if kind == "product" and not merged.get("name"):
            raise HTTPException(status_code=422, detail={"code": "promo_name_required"})
        promo.kind = kind
        promo.payload = merged
        session.flush()
    service.clear_prefill_marks(
        session, tenant_id=repository.tenant.id, project_id=project.id, keys=("promos",)
    )
    _audit(
        session,
        tenant_id=repository.tenant.id,
        principal=principal,
        action=_action("intake_promo", "updated", promo.pub_id, body.model_dump_json()),
        resource_type="intake_promo",
        resource_pub_id=promo.pub_id,
        receipt={"project_pub_id": project.pub_id, "kind": promo.kind},
    )
    session.commit()
    return _promo_view(promo)


@router.delete("/{project_pub_id}/intake/promos/{promo_pub_id}")
def delete_intake_promo(
    project_pub_id: str,
    promo_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict[str, str]:
    principal.require("intake:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id)
    promo = service.get_promo(
        session,
        tenant_id=repository.tenant.id,
        project_id=project.id,
        promo_pub_id=promo_pub_id,
    )
    if promo is None:
        raise HTTPException(status_code=404, detail={"code": "promo_not_found"})
    service.delete_promo(session, promo)
    service.clear_prefill_marks(
        session, tenant_id=repository.tenant.id, project_id=project.id, keys=("promos",)
    )
    _audit(
        session,
        tenant_id=repository.tenant.id,
        principal=principal,
        action=_action("intake_promo", "deleted", promo_pub_id),
        resource_type="intake_promo",
        resource_pub_id=promo_pub_id,
        receipt={"project_pub_id": project.pub_id},
    )
    session.commit()
    return {"deleted": promo_pub_id}


# ── 期望触发问法 CRUD（draft 可改可删；claim_created 后文本冻结）──────────────────
@router.get("/{project_pub_id}/intake/trigger-questions", response_model=schemas.TriggerListView)
def list_intake_triggers(
    project_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> schemas.TriggerListView:
    principal.require("intake:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id)
    rows = service.list_triggers(session, tenant_id=repository.tenant.id, project_id=project.id)
    return schemas.TriggerListView(items=[_trigger_view(r) for r in rows])


@router.post(
    "/{project_pub_id}/intake/trigger-questions",
    response_model=schemas.TriggerCreateView,
    status_code=201,
)
def create_intake_triggers(
    project_pub_id: str,
    body: schemas.TriggerCreate,
    idempotency_key: IdempotencyKey,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> schemas.TriggerCreateView:
    principal.require("intake:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id, lock=True)
    try:
        lines = body.lines()
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"code": f"invalid_text:{e}"}) from e
    action = _action("intake_trigger", "created", project.pub_id, idempotency_key)
    prior = _prior_audit(session, tenant_id=repository.tenant.id, action=action)
    if prior is not None:
        receipt = json.loads(prior.receipt)
        if receipt.get("texts") != lines:
            raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"})
        rows = service.list_triggers(session, tenant_id=repository.tenant.id, project_id=project.id)
        by_text = {r.text: r for r in rows}
        return schemas.TriggerCreateView(
            items=[_trigger_view(by_text[t]) for t in lines if t in by_text],
            skipped_duplicates=list(receipt.get("skipped", [])),
        )
    created, skipped = service.create_trigger_questions(
        session, tenant_id=repository.tenant.id, project_id=project.id, texts=lines
    )
    # 用户手动收录 → AI 草稿标清除
    service.clear_prefill_marks(
        session,
        tenant_id=repository.tenant.id,
        project_id=project.id,
        keys=("trigger_questions",),
    )
    _audit(
        session,
        tenant_id=repository.tenant.id,
        principal=principal,
        action=action,
        resource_type="intake_trigger_question",
        resource_pub_id=created[0].pub_id if created else project.pub_id,
        receipt={
            "project_pub_id": project.pub_id,
            "texts": lines,
            "created": len(created),
            "skipped": skipped,
        },
    )
    session.commit()
    return schemas.TriggerCreateView(
        items=[_trigger_view(r) for r in created], skipped_duplicates=skipped
    )


def _draft_trigger(
    session: Session, tenant_id: Any, project_id: Any, trigger_pub_id: str
) -> models.IntakeTriggerQuestion:
    row = service.get_trigger(
        session, tenant_id=tenant_id, project_id=project_id, trigger_pub_id=trigger_pub_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "trigger_not_found"})
    if row.status != "draft":
        # 已生成 claim 的问法文本冻结（防与草稿 claim 漂移）——改问法=删了重录
        raise HTTPException(status_code=409, detail={"code": "trigger_frozen"})
    return row


@router.patch(
    "/{project_pub_id}/intake/trigger-questions/{trigger_pub_id}",
    response_model=schemas.TriggerView,
)
def patch_intake_trigger(
    project_pub_id: str,
    trigger_pub_id: str,
    body: schemas.TriggerUpdate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> schemas.TriggerView:
    principal.require("intake:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id)
    row = _draft_trigger(session, repository.tenant.id, project.id, trigger_pub_id)
    if any(
        other.pub_id != row.pub_id and other.text == body.text
        for other in service.list_triggers(
            session, tenant_id=repository.tenant.id, project_id=project.id
        )
    ):
        raise HTTPException(status_code=409, detail={"code": "duplicate_text"})
    row.text = body.text
    session.flush()
    service.clear_prefill_marks(
        session,
        tenant_id=repository.tenant.id,
        project_id=project.id,
        keys=("trigger_questions",),
    )
    _audit(
        session,
        tenant_id=repository.tenant.id,
        principal=principal,
        action=_action("intake_trigger", "updated", row.pub_id, body.text),
        resource_type="intake_trigger_question",
        resource_pub_id=row.pub_id,
        receipt={"project_pub_id": project.pub_id},
    )
    session.commit()
    return _trigger_view(row)


@router.delete("/{project_pub_id}/intake/trigger-questions/{trigger_pub_id}")
def delete_intake_trigger(
    project_pub_id: str,
    trigger_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict[str, str]:
    principal.require("intake:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id)
    row = _draft_trigger(session, repository.tenant.id, project.id, trigger_pub_id)
    session.delete(row)
    service.clear_prefill_marks(
        session,
        tenant_id=repository.tenant.id,
        project_id=project.id,
        keys=("trigger_questions",),
    )
    _audit(
        session,
        tenant_id=repository.tenant.id,
        principal=principal,
        action=_action("intake_trigger", "deleted", trigger_pub_id),
        resource_type="intake_trigger_question",
        resource_pub_id=trigger_pub_id,
        receipt={"project_pub_id": project.pub_id},
    )
    session.commit()
    return {"deleted": trigger_pub_id}


# ══ AI 一键填充（live 联网调研 → 草稿预填）══════════════════════════════════
# 落库纪律（照旧版 + 零合成）：profile 只填**当前为空**的字段并记 prefilled provenance；
# 无 promo 时才建 product≤3 + company≤1 草稿；问法收录 draft ≤20 条；词表 fail-closed
# 已在 research._filter_vocab 完成（丢弃值在响应 dropped 里披露）。
@router.get("/{project_pub_id}/intake/research-models")
def intake_research_models(
    project_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """前端 AI 面板的调研模型下拉清单（GEO_RESEARCH_LLM_MODELS，缺省模型恒在首位）。"""
    principal.require("intake:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    _project(session, repository.tenant.id, project_pub_id)
    return {"models": research.available_models(get_settings())}


@router.post("/{project_pub_id}/intake/ai-research")
def intake_ai_research(
    project_pub_id: str,
    body: schemas.AiResearchRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    principal.require("intake:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id, lock=True)
    hints = {"website": body.website} if body.website else None
    settings = get_settings()
    try:
        model = research.resolve_research_model(settings, body.model)
    except research.ResearchModelNotAllowed:
        raise HTTPException(status_code=400, detail={"code": "model_not_allowed"}) from None
    config = replace(research.config_from_settings(settings), model=model)
    try:
        result = research.research_brand_fields(body.brand, hints, config=config)
    except research.ResearchDisabled:
        raise HTTPException(status_code=503, detail={"code": "llm_disabled"}) from None
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"code": f"invalid_brand:{e}"}) from e
    except research.ResearchFailed as e:
        raise HTTPException(status_code=502, detail={"code": "research_failed"}) from e

    data: dict[str, Any] = result["data"]
    tenant_id = repository.tenant.id
    # 落库（profile 空字段预填 + promo/问法草稿）与 intake_form 免登录通道共用同一实现。
    applied = service.apply_research_data(
        session, tenant_id=tenant_id, project_id=project.id, data=data
    )
    filled = applied["prefilled"]
    promos_created = applied["promos_created"]
    triggers_created = applied["triggers_created"]
    triggers_skipped = applied["triggers_skipped"]
    _audit(
        session,
        tenant_id=tenant_id,
        principal=principal,
        action=_action(
            "intake_profile",
            "researched",
            project.pub_id,
            body.brand,
            json.dumps(hints or {}, sort_keys=True),
        ),
        resource_type="intake_profile",
        resource_pub_id=project.pub_id,
        receipt={
            "project_pub_id": project.pub_id,
            "brand": body.brand,
            "model": result["model"],
            "prefilled": filled,
            "promos": len(promos_created),
            "triggers": len(triggers_created),
            "rounds": result["rounds"],
            "dropped": result["dropped"],
        },
    )
    session.commit()
    return {
        "data": data,
        "confidence": result["confidence"],
        "sources": result["sources"],
        "summary": result["summary"],
        "model": result["model"],
        "usage": result["usage"],
        "dropped": result["dropped"],
        "prefilled": filled,
        "rounds": result["rounds"],
        "unavailable": result["unavailable"],
        "unfilled": result["unfilled"],
        "promos_created": promos_created,
        "triggers_created": triggers_created,
        "triggers_skipped": triggers_skipped,
    }


# ══ 导出 Word（五节结构照搬旧 _build_profile_docx；python-docx）══════════════════
@router.get("/{project_pub_id}/intake/profile.docx")
def export_intake_profile_docx(
    project_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> Response:
    principal.require("intake:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id)
    profile = service.get_profile(session, tenant_id=repository.tenant.id, project_id=project.id)
    promos = service.list_promos(session, tenant_id=repository.tenant.id, project_id=project.id)
    triggers = service.list_triggers(session, tenant_id=repository.tenant.id, project_id=project.id)
    doc, today = service.build_profile_docx(project.name, profile, promos, triggers)
    fname = f"GEO客户信息_{project.name}_{today}.docx"
    _audit(
        session,
        tenant_id=repository.tenant.id,
        principal=principal,
        action=_action("intake_profile", "exported", project.pub_id, today),
        resource_type="intake_profile",
        resource_pub_id=project.pub_id,
        receipt={"project_pub_id": project.pub_id},
    )
    session.commit()
    return Response(
        content=service.docx_bytes(doc),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(fname)},
    )


# ══ 导出合同 Word（模板填槽；空槽 unfilled 经响应头披露数量）══════════════════════
@router.get("/{project_pub_id}/intake/contract.docx")
def export_intake_contract_docx(
    project_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> Response:
    principal.require("intake:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id)
    content, unfilled = contract.render_contract_docx(
        session, tenant_id=repository.tenant.id, project=project
    )
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    fname = f"GEO技术验证MVP服务合同_{project.name}_{today}.docx"
    _audit(
        session,
        tenant_id=repository.tenant.id,
        principal=principal,
        action=_action("intake_contract", "exported", project.pub_id, today),
        resource_type="intake_contract",
        resource_pub_id=project.pub_id,
        receipt={"project_pub_id": project.pub_id, "unfilled": len(unfilled)},
    )
    session.commit()
    headers = {"Content-Disposition": "attachment; filename*=UTF-8''" + quote(fname)}
    if unfilled:
        headers["X-Contract-Unfilled-Count"] = str(len(unfilled))
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers=headers,
    )
