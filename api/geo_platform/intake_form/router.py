# ruff: noqa: B008
"""免登录客户填表通道（邀请 token 制）REST。

两个面：
  * 运营端 ``/api/v2/projects/{pub}/intake/invites``（intake:write）：签发/列表/撤销邀请，
    幂等 + AuditLog 惯例照 intake/confirmation_router；
  * token 域 ``/api/v2/intake-form``（匿名）：principal 从 ``X-Intake-Token`` 头解析——
    先开 RLS 窄口子（auth_scope=intake_invite）按 sha256 找 invite，再把 invite 的
    tenant_id 注入 RLS 上下文；此后整请求只摸得到绑定 project。失效态 403 语义化 code
    （invalid/expired/revoked），submit 后全部写端点 409 invite_submitted。

写纪律照 intake 模块：词表 fail-closed + DLP 在 intake/schemas 层；audit action=`<resource>.
<verb>:<sha256>`；token 域的 AuditLog.actor_pub_id = invite.pub_id。
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..config import get_settings
from ..identity.policy import Principal, get_principal
from ..intake import research
from ..intake import schemas as intake_schemas
from ..intake import service as intake_service
from ..intake.router import (
    _action,
    _prior_audit,
    _profile_view,
    _promo_view,
    _trigger_view,
    form_schema,
)
from ..projects.models import Project
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.models import AuditLog, Tenant
from ..tenancy.repository import TenantRepository
from . import models, schemas, service, siliconindex

router = APIRouter(prefix="/api/v2/projects", tags=["intake-form"])
token_router = APIRouter(prefix="/api/v2/intake-form", tags=["intake-form"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)]
OptionalIdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=16, max_length=128)
]


# ══ 公共助手 ═══════════════════════════════════════════════════════════════
def _project(
    session: Session, tenant_id: Any, project_pub_id: str, *, lock: bool = False
) -> Project:
    statement = session.query(Project).filter(
        Project.tenant_id == tenant_id, Project.pub_id == project_pub_id
    )
    if lock:
        statement = statement.with_for_update()
    project = statement.first()
    if project is None:
        raise HTTPException(status_code=404, detail={"code": "project_not_found"})
    return project


def _audit(
    session: Session,
    *,
    tenant_id: Any,
    actor_pub_id: str,
    action: str,
    resource_type: str,
    resource_pub_id: str,
    receipt: dict[str, Any],
) -> None:
    session.add(
        AuditLog(
            pub_id=new_pub_id("aud"),
            tenant_id=tenant_id,
            actor_pub_id=actor_pub_id,
            action=action,
            resource_type=resource_type,
            resource_pub_id=resource_pub_id,
            receipt=json.dumps(receipt, ensure_ascii=False),
        )
    )


def _invite_view(invite: models.IntakeInvite, project: Project) -> schemas.InviteView:
    return schemas.InviteView(
        pub_id=invite.pub_id,
        project_pub_id=project.pub_id,
        expires_at=invite.expires_at.isoformat(),
        revoked_at=invite.revoked_at.isoformat() if invite.revoked_at else None,
        submitted_at=invite.submitted_at.isoformat() if invite.submitted_at else None,
        ai_quota=invite.ai_quota,
        ai_used=invite.ai_used,
        created_by=invite.created_by,
        created_at=invite.created_at.isoformat(),
    )


# ══ 运营端：邀请签发/列表/撤销（intake:write）════════════════════════════════
@router.post("/{project_pub_id}/intake/invites", status_code=201)
def create_intake_invite(
    project_pub_id: str,
    body: schemas.InviteCreate,
    idempotency_key: IdempotencyKey,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    principal.require("intake:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id, lock=True)
    settings = get_settings()
    action = _action("intake_invite", "created", project.pub_id, idempotency_key)
    prior = _prior_audit(session, tenant_id=repository.tenant.id, action=action)
    if prior is not None:
        # 幂等重放：token 原文只存哈希，重放给视图即可（不再发原文）。
        existing = service.get_invite(
            session,
            tenant_id=repository.tenant.id,
            project_id=project.id,
            invite_pub_id=prior.resource_pub_id,
        )
        if existing is None:
            raise HTTPException(status_code=409, detail={"code": "idempotency_receipt_invalid"})
        view = _invite_view(existing, project).model_dump()
        view["replay"] = True
        view["token"] = None
        return view
    invite, token = service.create_invite(
        session,
        tenant_id=repository.tenant.id,
        project_id=project.id,
        created_by=principal.actor_pub_id,
        ttl_hours=body.ttl_hours or settings.intake_invite_ttl_hours,
        ai_quota=body.ai_quota if body.ai_quota is not None else settings.intake_invite_ai_quota,
    )
    _audit(
        session,
        tenant_id=repository.tenant.id,
        actor_pub_id=principal.actor_pub_id,
        action=action,
        resource_type="intake_invite",
        resource_pub_id=invite.pub_id,
        receipt={
            "project_pub_id": project.pub_id,
            "expires_at": invite.expires_at.isoformat(),
            "ai_quota": invite.ai_quota,
        },
    )
    session.commit()
    view = _invite_view(invite, project).model_dump()
    view["replay"] = False
    view["token"] = token  # 原文只在这一次响应出现
    return view


@router.get("/{project_pub_id}/intake/invites", response_model=schemas.InviteListView)
def list_intake_invites(
    project_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> schemas.InviteListView:
    principal.require("intake:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id)
    rows = service.list_invites(session, tenant_id=repository.tenant.id, project_id=project.id)
    return schemas.InviteListView(items=[_invite_view(r, project) for r in rows])


@router.delete("/{project_pub_id}/intake/invites/{invite_pub_id}")
def revoke_intake_invite(
    project_pub_id: str,
    invite_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    principal.require("intake:write")
    repository = TenantRepository(session, principal.tenant_pub_id)
    project = _project(session, repository.tenant.id, project_pub_id)
    invite = service.get_invite(
        session,
        tenant_id=repository.tenant.id,
        project_id=project.id,
        invite_pub_id=invite_pub_id,
    )
    if invite is None:
        raise HTTPException(status_code=404, detail={"code": "invite_not_found"})
    already = invite.revoked_at is not None
    if not already:
        invite.revoked_at = datetime.now(UTC)
        session.flush()
    _audit(
        session,
        tenant_id=repository.tenant.id,
        actor_pub_id=principal.actor_pub_id,
        action=_action("intake_invite", "revoked", invite.pub_id),
        resource_type="intake_invite",
        resource_pub_id=invite.pub_id,
        receipt={"project_pub_id": project.pub_id, "already_revoked": already},
    )
    session.commit()
    return {"revoked": invite_pub_id, "already_revoked": already}


# ══ token 域：principal 从 X-Intake-Token 解析 ═════════════════════════════
@dataclass
class InviteContext:
    invite: models.IntakeInvite
    tenant: Tenant
    project: Project


def get_invite_context(
    x_intake_token: str | None = Header(default=None, alias="X-Intake-Token"),
    session: Session = Depends(get_db),
) -> InviteContext:
    """匿名 → invite：RLS 窄口子按 sha256 查找，然后把 invite 的 tenant 注入上下文。"""
    if (
        x_intake_token is None
        or not x_intake_token.strip()
        or len(x_intake_token) > 200
        or any(c.isspace() for c in x_intake_token)
    ):
        raise HTTPException(status_code=401, detail={"code": "intake_token_missing"})
    service.enable_invite_lookup(session)
    invite = service.find_by_token(session, x_intake_token.strip())
    if invite is None:
        raise HTTPException(status_code=403, detail={"code": "invite_token_invalid"})
    if invite.revoked_at is not None:
        raise HTTPException(status_code=403, detail={"code": "invite_token_revoked"})
    if service.invite_expired(invite):
        raise HTTPException(status_code=403, detail={"code": "invite_token_expired"})
    tenant = service.bind_tenant_context(session, invite)
    if tenant is None:
        raise HTTPException(status_code=403, detail={"code": "invite_token_invalid"})
    project = service.get_project(session, tenant_id=tenant.id, project_id=invite.project_id)
    if project is None:
        raise HTTPException(status_code=403, detail={"code": "invite_token_invalid"})
    return InviteContext(invite=invite, tenant=tenant, project=project)


def _writable(ctx: InviteContext) -> None:
    """submit 后全部写端点 409。"""
    if ctx.invite.submitted_at is not None:
        raise HTTPException(status_code=409, detail={"code": "invite_submitted"})


def _consume_quota(session: Session, ctx: InviteContext) -> None:
    """AI 配额闸门（行锁防并发超扣）；成功调用后由端点把 ai_used += 1。"""
    session.refresh(ctx.invite, with_for_update=True)
    if ctx.invite.ai_used >= ctx.invite.ai_quota:
        raise HTTPException(status_code=429, detail={"code": "quota_exhausted"})


def _invite_state(ctx: InviteContext) -> dict[str, Any]:
    return {
        "pub_id": ctx.invite.pub_id,
        "expires_at": ctx.invite.expires_at.isoformat(),
        "submitted": ctx.invite.submitted_at is not None,
        "submitted_at": ctx.invite.submitted_at.isoformat() if ctx.invite.submitted_at else None,
        "ai_quota": ctx.invite.ai_quota,
        "ai_used": ctx.invite.ai_used,
        "ai_remaining": max(0, ctx.invite.ai_quota - ctx.invite.ai_used),
    }


def _brand_view(session: Session, ctx: InviteContext) -> schemas.BrandView:
    brand = service.get_brand(session, tenant_id=ctx.tenant.id, project_id=ctx.project.id)
    if brand is None:
        return schemas.BrandView(exists=False, pub_id=None, name=None, website=None, aliases=[])
    aliases = [
        a.value for a in service.list_aliases(session, tenant_id=ctx.tenant.id, brand_id=brand.id)
    ]
    return schemas.BrandView(
        exists=True, pub_id=brand.pub_id, name=brand.name, website=brand.website, aliases=aliases
    )


def _competitor_view(row: Any) -> schemas.CompetitorView:
    return schemas.CompetitorView(
        pub_id=row.pub_id,
        name=row.name,
        website=row.website,
        created_at=row.created_at.isoformat(),
    )


def _brand_name(session: Session, ctx: InviteContext) -> str:
    brand = service.get_brand(session, tenant_id=ctx.tenant.id, project_id=ctx.project.id)
    return (brand.name if brand else ctx.project.name) or ctx.project.name


def _catalog() -> siliconindex.SiliconIndex:
    return siliconindex.SiliconIndex(get_settings().siliconindex_snapshot_dir)


# ── 上下文（form-schema + brand + competitors + 当前 profile + invite 状态）────
@token_router.get("/context")
def intake_form_context(
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = intake_service.get_profile(
        session, tenant_id=ctx.tenant.id, project_id=ctx.project.id
    )
    competitors = service.list_competitors(
        session, tenant_id=ctx.tenant.id, project_id=ctx.project.id
    )
    return {
        "form": form_schema(),
        "brand": _brand_view(session, ctx).model_dump(),
        "competitors": [_competitor_view(c).model_dump() for c in competitors],
        "profile": _profile_view(profile, ctx.project).model_dump(),
        "invite": _invite_state(ctx),
    }


# ── profile（复用 intake service/schema；submit 后 PUT 409）──────────────────
@token_router.get("/profile", response_model=intake_schemas.ProfileView)
def intake_form_get_profile(
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> intake_schemas.ProfileView:
    profile = intake_service.get_profile(
        session, tenant_id=ctx.tenant.id, project_id=ctx.project.id
    )
    return _profile_view(profile, ctx.project)


@token_router.put("/profile", response_model=intake_schemas.ProfileView)
def intake_form_put_profile(
    body: intake_schemas.ProfileUpdate,
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> intake_schemas.ProfileView:
    _writable(ctx)
    sets = body.model_dump(exclude_unset=True)
    touched = sorted(sets)
    canonical = json.dumps(sets, ensure_ascii=False, sort_keys=True)
    action = _action("intake_profile", "updated", ctx.project.pub_id, canonical)
    if _prior_audit(session, tenant_id=ctx.tenant.id, action=action) is not None:
        profile = intake_service.get_profile(
            session, tenant_id=ctx.tenant.id, project_id=ctx.project.id
        )
        return _profile_view(profile, ctx.project)
    profile = intake_service.upsert_profile(
        session,
        tenant_id=ctx.tenant.id,
        project_id=ctx.project.id,
        sets=sets,
        clear_prefill=touched,
    )
    _audit(
        session,
        tenant_id=ctx.tenant.id,
        actor_pub_id=ctx.invite.pub_id,
        action=action,
        resource_type="intake_profile",
        resource_pub_id=profile.pub_id,
        receipt={"project_pub_id": ctx.project.pub_id, "fields": touched, "via": "intake_form"},
    )
    session.commit()
    return _profile_view(profile, ctx.project)


# ── promo CRUD（同 intake 语义）──────────────────────────────────────────────
@token_router.get("/promos", response_model=intake_schemas.PromoListView)
def intake_form_list_promos(
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> intake_schemas.PromoListView:
    rows = intake_service.list_promos(session, tenant_id=ctx.tenant.id, project_id=ctx.project.id)
    return intake_schemas.PromoListView(items=[_promo_view(r) for r in rows])


@token_router.post("/promos", response_model=intake_schemas.PromoView, status_code=201)
def intake_form_create_promo(
    body: intake_schemas.PromoCreate,
    idempotency_key: OptionalIdempotencyKey = None,
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> intake_schemas.PromoView:
    _writable(ctx)
    canonical = body.model_dump_json()
    payload_hash = hashlib.sha256(canonical.encode()).hexdigest()
    key = idempotency_key or payload_hash  # 匿名端：缺 key 时按 body 自然幂等
    action = _action("intake_promo", "created", ctx.project.pub_id, key)
    prior = _prior_audit(session, tenant_id=ctx.tenant.id, action=action)
    if prior is not None:
        receipt = json.loads(prior.receipt)
        if receipt.get("payload_hash") != payload_hash:
            raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"})
        existing = intake_service.get_promo(
            session,
            tenant_id=ctx.tenant.id,
            project_id=ctx.project.id,
            promo_pub_id=prior.resource_pub_id,
        )
        if existing is None:
            raise HTTPException(status_code=409, detail={"code": "idempotency_receipt_invalid"})
        return _promo_view(existing)
    promo = intake_service.create_promo(
        session,
        tenant_id=ctx.tenant.id,
        project_id=ctx.project.id,
        kind=body.kind,
        payload=body.payload,
    )
    intake_service.clear_prefill_marks(
        session, tenant_id=ctx.tenant.id, project_id=ctx.project.id, keys=("promos",)
    )
    _audit(
        session,
        tenant_id=ctx.tenant.id,
        actor_pub_id=ctx.invite.pub_id,
        action=action,
        resource_type="intake_promo",
        resource_pub_id=promo.pub_id,
        receipt={"project_pub_id": ctx.project.pub_id, "payload_hash": payload_hash},
    )
    session.commit()
    return _promo_view(promo)


@token_router.patch("/promos/{promo_pub_id}", response_model=intake_schemas.PromoView)
def intake_form_patch_promo(
    promo_pub_id: str,
    body: intake_schemas.PromoUpdate,
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> intake_schemas.PromoView:
    _writable(ctx)
    promo = intake_service.get_promo(
        session,
        tenant_id=ctx.tenant.id,
        project_id=ctx.project.id,
        promo_pub_id=promo_pub_id,
    )
    if promo is None:
        raise HTTPException(status_code=404, detail={"code": "promo_not_found"})
    kind = body.kind or promo.kind
    if body.payload is not None or kind != promo.kind:
        new_payload = intake_schemas.validate_promo_payload(kind, body.payload or {}, partial=True)
        if kind != promo.kind:
            merged = new_payload
        else:
            merged = dict(promo.payload)
            merged.update(new_payload)
        if kind == "product" and not merged.get("name"):
            raise HTTPException(status_code=422, detail={"code": "promo_name_required"})
        promo.kind = kind
        promo.payload = merged
        session.flush()
    intake_service.clear_prefill_marks(
        session, tenant_id=ctx.tenant.id, project_id=ctx.project.id, keys=("promos",)
    )
    _audit(
        session,
        tenant_id=ctx.tenant.id,
        actor_pub_id=ctx.invite.pub_id,
        action=_action("intake_promo", "updated", promo.pub_id, body.model_dump_json()),
        resource_type="intake_promo",
        resource_pub_id=promo.pub_id,
        receipt={"project_pub_id": ctx.project.pub_id, "kind": promo.kind},
    )
    session.commit()
    return _promo_view(promo)


@token_router.delete("/promos/{promo_pub_id}")
def intake_form_delete_promo(
    promo_pub_id: str,
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> dict[str, str]:
    _writable(ctx)
    promo = intake_service.get_promo(
        session,
        tenant_id=ctx.tenant.id,
        project_id=ctx.project.id,
        promo_pub_id=promo_pub_id,
    )
    if promo is None:
        raise HTTPException(status_code=404, detail={"code": "promo_not_found"})
    intake_service.delete_promo(session, promo)
    intake_service.clear_prefill_marks(
        session, tenant_id=ctx.tenant.id, project_id=ctx.project.id, keys=("promos",)
    )
    _audit(
        session,
        tenant_id=ctx.tenant.id,
        actor_pub_id=ctx.invite.pub_id,
        action=_action("intake_promo", "deleted", promo_pub_id),
        resource_type="intake_promo",
        resource_pub_id=promo_pub_id,
        receipt={"project_pub_id": ctx.project.pub_id},
    )
    session.commit()
    return {"deleted": promo_pub_id}


# ── 期望触发问法 CRUD（同 intake 语义）──────────────────────────────────────
@token_router.get("/trigger-questions", response_model=intake_schemas.TriggerListView)
def intake_form_list_triggers(
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> intake_schemas.TriggerListView:
    rows = intake_service.list_triggers(session, tenant_id=ctx.tenant.id, project_id=ctx.project.id)
    return intake_schemas.TriggerListView(items=[_trigger_view(r) for r in rows])


@token_router.post(
    "/trigger-questions", response_model=intake_schemas.TriggerCreateView, status_code=201
)
def intake_form_create_triggers(
    body: intake_schemas.TriggerCreate,
    idempotency_key: OptionalIdempotencyKey = None,
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> intake_schemas.TriggerCreateView:
    _writable(ctx)
    try:
        lines = body.lines()
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"code": f"invalid_text:{e}"}) from e
    key = idempotency_key or hashlib.sha256(body.text.encode()).hexdigest()
    action = _action("intake_trigger", "created", ctx.project.pub_id, key)
    prior = _prior_audit(session, tenant_id=ctx.tenant.id, action=action)
    if prior is not None:
        receipt = json.loads(prior.receipt)
        if receipt.get("texts") != lines:
            raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"})
        rows = intake_service.list_triggers(
            session, tenant_id=ctx.tenant.id, project_id=ctx.project.id
        )
        by_text = {r.text: r for r in rows}
        return intake_schemas.TriggerCreateView(
            items=[_trigger_view(by_text[t]) for t in lines if t in by_text],
            skipped_duplicates=list(receipt.get("skipped", [])),
        )
    created, skipped = intake_service.create_trigger_questions(
        session, tenant_id=ctx.tenant.id, project_id=ctx.project.id, texts=lines
    )
    intake_service.clear_prefill_marks(
        session, tenant_id=ctx.tenant.id, project_id=ctx.project.id, keys=("trigger_questions",)
    )
    _audit(
        session,
        tenant_id=ctx.tenant.id,
        actor_pub_id=ctx.invite.pub_id,
        action=action,
        resource_type="intake_trigger_question",
        resource_pub_id=created[0].pub_id if created else ctx.project.pub_id,
        receipt={
            "project_pub_id": ctx.project.pub_id,
            "texts": lines,
            "created": len(created),
            "skipped": skipped,
        },
    )
    session.commit()
    return intake_schemas.TriggerCreateView(
        items=[_trigger_view(r) for r in created], skipped_duplicates=skipped
    )


def _draft_trigger(session: Session, ctx: InviteContext, trigger_pub_id: str) -> Any:
    row = intake_service.get_trigger(
        session,
        tenant_id=ctx.tenant.id,
        project_id=ctx.project.id,
        trigger_pub_id=trigger_pub_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "trigger_not_found"})
    if row.status != "draft":
        raise HTTPException(status_code=409, detail={"code": "trigger_frozen"})
    return row


@token_router.patch(
    "/trigger-questions/{trigger_pub_id}", response_model=intake_schemas.TriggerView
)
def intake_form_patch_trigger(
    trigger_pub_id: str,
    body: intake_schemas.TriggerUpdate,
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> intake_schemas.TriggerView:
    _writable(ctx)
    row = _draft_trigger(session, ctx, trigger_pub_id)
    if any(
        other.pub_id != row.pub_id and other.text == body.text
        for other in intake_service.list_triggers(
            session, tenant_id=ctx.tenant.id, project_id=ctx.project.id
        )
    ):
        raise HTTPException(status_code=409, detail={"code": "duplicate_text"})
    row.text = body.text
    session.flush()
    intake_service.clear_prefill_marks(
        session, tenant_id=ctx.tenant.id, project_id=ctx.project.id, keys=("trigger_questions",)
    )
    _audit(
        session,
        tenant_id=ctx.tenant.id,
        actor_pub_id=ctx.invite.pub_id,
        action=_action("intake_trigger", "updated", row.pub_id, body.text),
        resource_type="intake_trigger_question",
        resource_pub_id=row.pub_id,
        receipt={"project_pub_id": ctx.project.pub_id},
    )
    session.commit()
    return _trigger_view(row)


@token_router.delete("/trigger-questions/{trigger_pub_id}")
def intake_form_delete_trigger(
    trigger_pub_id: str,
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> dict[str, str]:
    _writable(ctx)
    row = _draft_trigger(session, ctx, trigger_pub_id)
    session.delete(row)
    intake_service.clear_prefill_marks(
        session, tenant_id=ctx.tenant.id, project_id=ctx.project.id, keys=("trigger_questions",)
    )
    _audit(
        session,
        tenant_id=ctx.tenant.id,
        actor_pub_id=ctx.invite.pub_id,
        action=_action("intake_trigger", "deleted", trigger_pub_id),
        resource_type="intake_trigger_question",
        resource_pub_id=trigger_pub_id,
        receipt={"project_pub_id": ctx.project.pub_id},
    )
    session.commit()
    return {"deleted": trigger_pub_id}


# ── AI 联网调研预填（复用 intake research；配额闸门）──────────────────────────
@token_router.post("/ai-research")
def intake_form_ai_research(
    body: intake_schemas.AiResearchRequest,
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    _writable(ctx)
    _consume_quota(session, ctx)
    hints = {"website": body.website} if body.website else None
    config = research.config_from_settings(get_settings())
    try:
        result = research.research_brand_fields(body.brand, hints, config=config)
    except research.ResearchDisabled:
        raise HTTPException(status_code=503, detail={"code": "llm_disabled"}) from None
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"code": f"invalid_brand:{e}"}) from e
    except research.ResearchFailed as e:
        raise HTTPException(status_code=502, detail={"code": "research_failed"}) from e

    data: dict[str, Any] = result["data"]
    applied = intake_service.apply_research_data(
        session, tenant_id=ctx.tenant.id, project_id=ctx.project.id, data=data
    )
    ctx.invite.ai_used += 1  # 成功调用才扣减
    session.flush()
    _audit(
        session,
        tenant_id=ctx.tenant.id,
        actor_pub_id=ctx.invite.pub_id,
        action=_action(
            "intake_profile",
            "researched",
            ctx.project.pub_id,
            body.brand,
            json.dumps(hints or {}, sort_keys=True),
        ),
        resource_type="intake_profile",
        resource_pub_id=ctx.project.pub_id,
        receipt={
            "project_pub_id": ctx.project.pub_id,
            "brand": body.brand,
            "model": result["model"],
            "prefilled": applied["prefilled"],
            "promos": len(applied["promos_created"]),
            "triggers": len(applied["triggers_created"]),
            "rounds": result["rounds"],
            "dropped": result["dropped"],
            "via": "intake_form",
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
        "prefilled": applied["prefilled"],
        "rounds": result["rounds"],
        "unavailable": result["unavailable"],
        "unfilled": result["unfilled"],
        "promos_created": applied["promos_created"],
        "triggers_created": applied["triggers_created"],
        "triggers_skipped": applied["triggers_skipped"],
        "ai_used": ctx.invite.ai_used,
        "ai_remaining": max(0, ctx.invite.ai_quota - ctx.invite.ai_used),
    }


# ── AI 扩写问法（candidate_only，不落库；与 ai-research 共用配额）──────────────
@token_router.post("/query-suggestions")
def intake_form_query_suggestions(
    body: schemas.QuerySuggestionsRequest,
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    _consume_quota(session, ctx)
    brand = _brand_name(session, ctx)
    existing = [
        r.text
        for r in intake_service.list_triggers(
            session, tenant_id=ctx.tenant.id, project_id=ctx.project.id
        )
    ]
    config = research.config_from_settings(get_settings())
    try:
        questions = research.suggest_monitor_questions(
            brand, body.core_words, existing, body.n, config=config
        )
    except research.ResearchDisabled:
        raise HTTPException(status_code=503, detail={"code": "llm_disabled"}) from None
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"code": f"invalid_input:{e}"}) from e
    except research.ResearchFailed as e:
        raise HTTPException(status_code=502, detail={"code": "research_failed"}) from e
    ctx.invite.ai_used += 1
    session.flush()
    _audit(
        session,
        tenant_id=ctx.tenant.id,
        actor_pub_id=ctx.invite.pub_id,
        action=_action(
            "intake_trigger", "suggested", ctx.project.pub_id, json.dumps(body.core_words)
        ),
        resource_type="intake_trigger_question",
        resource_pub_id=ctx.project.pub_id,
        receipt={
            "project_pub_id": ctx.project.pub_id,
            "core_words": body.core_words,
            "count": len(questions),
        },
    )
    session.commit()
    return {
        "questions": questions,
        "candidate_only": True,
        "ai_used": ctx.invite.ai_used,
        "ai_remaining": max(0, ctx.invite.ai_quota - ctx.invite.ai_used),
    }


# ── SiliconIndex 只读预览（无快照 → {available:false} 优雅降级）────────────────
@token_router.get("/siliconindex/candidates")
def intake_form_siliconindex_candidates(
    name: str | None = None,
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    catalog = _catalog()
    if not catalog.available:
        return {"available": False}
    brand_name = (name or "").strip() or _brand_name(session, ctx)
    return catalog.candidates(brand_name)


@token_router.post("/siliconindex/template-questions")
def intake_form_siliconindex_templates(
    body: schemas.TemplateQuestionsRequest,
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    catalog = _catalog()
    if not catalog.available:
        return {"available": False}
    result = catalog.template_questions(
        _brand_name(session, ctx), region=body.region, competitor=body.competitor
    )
    result["candidate_only"] = True
    return result


# ── brand / competitor（直接写 projects 模型，AuditLog）──────────────────────
@token_router.get("/brand", response_model=schemas.BrandView)
def intake_form_get_brand(
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> schemas.BrandView:
    return _brand_view(session, ctx)


@token_router.patch("/brand", response_model=schemas.BrandView)
def intake_form_patch_brand(
    body: schemas.BrandUpdate,
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> schemas.BrandView:
    _writable(ctx)
    sets = body.model_dump(exclude_unset=True)
    brand = service.get_brand(session, tenant_id=ctx.tenant.id, project_id=ctx.project.id)
    if brand is None:
        if not body.name:
            raise HTTPException(status_code=422, detail={"code": "brand_name_required"})
        brand = service.get_or_create_brand(
            session, tenant_id=ctx.tenant.id, project_id=ctx.project.id, name=body.name
        )
    elif "name" in sets:
        if not body.name:
            raise HTTPException(status_code=422, detail={"code": "brand_name_required"})
        brand.name = body.name
    if "website" in sets:
        brand.website = body.website
    if body.aliases is not None:
        service.replace_aliases(
            session, tenant_id=ctx.tenant.id, brand_id=brand.id, values=body.aliases
        )
    session.flush()
    _audit(
        session,
        tenant_id=ctx.tenant.id,
        actor_pub_id=ctx.invite.pub_id,
        action=_action("brand", "updated", brand.pub_id, json.dumps(sets, sort_keys=True)),
        resource_type="brand",
        resource_pub_id=brand.pub_id,
        receipt={"project_pub_id": ctx.project.pub_id, "fields": sorted(sets)},
    )
    session.commit()
    return _brand_view(session, ctx)


@token_router.get("/competitors", response_model=schemas.CompetitorListView)
def intake_form_list_competitors(
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> schemas.CompetitorListView:
    rows = service.list_competitors(session, tenant_id=ctx.tenant.id, project_id=ctx.project.id)
    return schemas.CompetitorListView(items=[_competitor_view(r) for r in rows])


@token_router.post("/competitors", response_model=schemas.CompetitorView, status_code=201)
def intake_form_create_competitor(
    body: schemas.CompetitorCreate,
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> schemas.CompetitorView:
    _writable(ctx)
    row = service.create_competitor(
        session,
        tenant_id=ctx.tenant.id,
        project_id=ctx.project.id,
        name=body.name,
        website=body.website,
    )
    _audit(
        session,
        tenant_id=ctx.tenant.id,
        actor_pub_id=ctx.invite.pub_id,
        action=_action("competitor", "created", row.pub_id, body.name),
        resource_type="competitor",
        resource_pub_id=row.pub_id,
        receipt={"project_pub_id": ctx.project.pub_id, "name": body.name},
    )
    session.commit()
    return _competitor_view(row)


@token_router.delete("/competitors/{competitor_pub_id}")
def intake_form_delete_competitor(
    competitor_pub_id: str,
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> dict[str, str]:
    _writable(ctx)
    row = service.get_competitor(
        session,
        tenant_id=ctx.tenant.id,
        project_id=ctx.project.id,
        competitor_pub_id=competitor_pub_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "competitor_not_found"})
    session.delete(row)
    _audit(
        session,
        tenant_id=ctx.tenant.id,
        actor_pub_id=ctx.invite.pub_id,
        action=_action("competitor", "deleted", competitor_pub_id),
        resource_type="competitor",
        resource_pub_id=competitor_pub_id,
        receipt={"project_pub_id": ctx.project.pub_id},
    )
    session.commit()
    return {"deleted": competitor_pub_id}


# ── 提交（合规亲笔项门 + 幂等；提交后全部写端点 409）──────────────────────────
@token_router.post("/submit")
def intake_form_submit(
    ctx: InviteContext = Depends(get_invite_context),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    session.refresh(ctx.invite, with_for_update=True)
    if ctx.invite.submitted_at is not None:
        # 幂等：重复提交返回原状态
        return {
            "submitted": True,
            "submitted_at": ctx.invite.submitted_at.isoformat(),
            "replay": True,
        }
    profile = intake_service.get_profile(
        session, tenant_id=ctx.tenant.id, project_id=ctx.project.id
    )
    missing: list[str] = []
    if profile is None or profile.truth_confirmed is not True:
        missing.append("truth_confirmed")
    if profile is None or not (profile.filler_name or "").strip():
        missing.append("filler_name")
    if missing:
        raise HTTPException(
            status_code=422, detail={"code": "submit_incomplete", "missing": missing}
        )
    assert profile is not None  # missing 检查已排除 None
    ctx.invite.submitted_at = datetime.now(UTC)
    session.flush()
    _audit(
        session,
        tenant_id=ctx.tenant.id,
        actor_pub_id=ctx.invite.pub_id,
        action=_action("intake_invite", "submitted", ctx.invite.pub_id),
        resource_type="intake_invite",
        resource_pub_id=ctx.invite.pub_id,
        receipt={"project_pub_id": ctx.project.pub_id, "filler_name": profile.filler_name},
    )
    session.commit()
    return {
        "submitted": True,
        "submitted_at": ctx.invite.submitted_at.isoformat(),
        "replay": False,
    }
