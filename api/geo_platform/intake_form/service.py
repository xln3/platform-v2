"""Intake 邀请 + 免登录填表通道的存储层（execute-only，NO commit——调用方单点提交 + 审计）。

token 解析纪律：
  * 查找 invite 前必须先 ``enable_invite_lookup``（RLS 窄口子 auth_scope=intake_invite，
    s06_0003 native_session 先例）；解析出 invite 后立刻 ``set_tenant_context`` 注入其
    tenant_id——此后整事务只摸得到该租户数据；
  * token 原文永不出库：入库只存 sha256 hexdigest。
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..projects.models import Brand, BrandAlias, Competitor, Project
from ..tenancy.ids import new_pub_id
from ..tenancy.models import Tenant
from ..tenancy.repository import set_tenant_context
from . import models

TOKEN_BYTES = 32


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def enable_invite_lookup(session: Session) -> None:
    """开启 invite 前置查找的窄 RLS 通道（transaction-local）。"""
    session.execute(
        text("SELECT set_config('app.auth_scope', :scope, true)"),
        {"scope": models.INVITE_AUTH_SCOPE},
    )


def create_invite(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    created_by: str,
    ttl_hours: int,
    ai_quota: int,
) -> tuple[models.IntakeInvite, str]:
    """签发邀请：返回 (invite, token 原文)——原文只经此返回值出去一次，绝不落库。"""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    invite = models.IntakeInvite(
        pub_id=new_pub_id("itv"),
        tenant_id=tenant_id,
        project_id=project_id,
        token_hash=hash_token(token),
        expires_at=datetime.now(UTC) + timedelta(hours=ttl_hours),
        ai_quota=ai_quota,
        ai_used=0,
        created_by=created_by,
    )
    session.add(invite)
    session.flush()
    return invite, token


def list_invites(
    session: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID
) -> list[models.IntakeInvite]:
    return list(
        session.scalars(
            select(models.IntakeInvite)
            .where(
                models.IntakeInvite.tenant_id == tenant_id,
                models.IntakeInvite.project_id == project_id,
            )
            .order_by(models.IntakeInvite.created_at, models.IntakeInvite.id)
        ).all()
    )


def get_invite(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    invite_pub_id: str,
) -> models.IntakeInvite | None:
    return session.scalar(
        select(models.IntakeInvite).where(
            models.IntakeInvite.tenant_id == tenant_id,
            models.IntakeInvite.project_id == project_id,
            models.IntakeInvite.pub_id == invite_pub_id,
        )
    )


def find_by_token(session: Session, token: str) -> models.IntakeInvite | None:
    """按 token 哈希找 invite（调用前必须已 enable_invite_lookup）。"""
    return session.scalar(
        select(models.IntakeInvite).where(models.IntakeInvite.token_hash == hash_token(token))
    )


def resolve_tenant(session: Session, tenant_id: uuid.UUID) -> Tenant | None:
    return session.scalar(select(Tenant).where(Tenant.id == tenant_id))


def bind_tenant_context(session: Session, invite: models.IntakeInvite) -> Tenant | None:
    """token 解析成功后注入 invite 的 tenant 上下文（此后 RLS 只剩该租户）。"""
    tenant = resolve_tenant(session, invite.tenant_id)
    if tenant is not None:
        set_tenant_context(session, tenant_id=tenant.id, tenant_pub_id=tenant.pub_id)
    return tenant


def invite_expired(invite: models.IntakeInvite) -> bool:
    expires = invite.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires <= datetime.now(UTC)


# ── brand / competitor（直接写 projects 模型）──────────────────────────────
def get_brand(session: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID) -> Brand | None:
    return session.scalar(
        select(Brand)
        .where(Brand.tenant_id == tenant_id, Brand.project_id == project_id)
        .order_by(Brand.created_at, Brand.id)
        .limit(1)
    )


def get_or_create_brand(
    session: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, name: str
) -> Brand:
    brand = get_brand(session, tenant_id=tenant_id, project_id=project_id)
    if brand is None:
        brand = Brand(
            pub_id=new_pub_id("brd"), tenant_id=tenant_id, project_id=project_id, name=name
        )
        session.add(brand)
        session.flush()
    return brand


def list_aliases(
    session: Session, *, tenant_id: uuid.UUID, brand_id: uuid.UUID
) -> list[BrandAlias]:
    return list(
        session.scalars(
            select(BrandAlias)
            .where(BrandAlias.tenant_id == tenant_id, BrandAlias.brand_id == brand_id)
            .order_by(BrandAlias.created_at, BrandAlias.id)
        ).all()
    )


def replace_aliases(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    brand_id: uuid.UUID,
    values: list[str],
) -> list[BrandAlias]:
    """整体替换 alias 集（PATCH 语义：给什么就是什么）。"""
    for row in list_aliases(session, tenant_id=tenant_id, brand_id=brand_id):
        session.delete(row)
    out: list[BrandAlias] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        row = BrandAlias(
            pub_id=new_pub_id("bra"), tenant_id=tenant_id, brand_id=brand_id, value=value
        )
        session.add(row)
        out.append(row)
    session.flush()
    return out


def list_competitors(
    session: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID
) -> list[Competitor]:
    return list(
        session.scalars(
            select(Competitor)
            .where(Competitor.tenant_id == tenant_id, Competitor.project_id == project_id)
            .order_by(Competitor.created_at, Competitor.id)
        ).all()
    )


def get_competitor(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    competitor_pub_id: str,
) -> Competitor | None:
    return session.scalar(
        select(Competitor).where(
            Competitor.tenant_id == tenant_id,
            Competitor.project_id == project_id,
            Competitor.pub_id == competitor_pub_id,
        )
    )


def create_competitor(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    name: str,
    website: str | None,
) -> Competitor:
    row = Competitor(
        pub_id=new_pub_id("cmp"),
        tenant_id=tenant_id,
        project_id=project_id,
        name=name,
        website=website,
    )
    session.add(row)
    session.flush()
    return row


def get_project(session: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID) -> Project | None:
    return session.scalar(
        select(Project).where(Project.tenant_id == tenant_id, Project.id == project_id)
    )
