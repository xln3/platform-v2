"""Intake 邀请 token 模型（免登录客户填表通道）。

口径：
  * 一张 invite 绑定一个 project；token 原文只在签发响应出现一次，库里只存 sha256；
  * expires_at/revoked_at/submitted_at 三态决定 token 域端点可用性；
  * ai_quota/ai_used 是该邀请的 AI 调用预算（ai-research 与 query-suggestions 共用）；
  * RLS：常规 tenant_isolation + 窄口子 auth_scope='intake_invite'（token 解析前置查找
    专用，照搬 s06_0003 native_session 先例）——解析出 invite 后立即注入 tenant 上下文。
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..projects.models import TenantModel
from ..tenancy.database import Base

INVITE_AUTH_SCOPE = "intake_invite"


class IntakeInvite(TenantModel, Base):
    """填表邀请：免登录客户经 X-Intake-Token 头访问绑定 project 的填表端点。"""

    __tablename__ = "intake_invite"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.project.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ai_quota: Mapped[int] = mapped_column(Integer, default=3)
    ai_used: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(255))
