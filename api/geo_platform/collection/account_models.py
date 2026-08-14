"""采集账号治理实体模型（migration s06_0022，设计文档 caiji-0813 §3）。

五张表全部是机器资源（无租户、无 RLS），照本文件 BrowserFence 先例只继承
``Base``；主键 BIGSERIAL（实体间 FK 用 bigint），``pub_id`` 文本唯一对外。
与 S01 旧模型（PlatformAccount 等，租户域账号资产）互不干扰。

状态词表（合法性校验在 account_governor 程序层，列不加 CHECK）：

- ``CollectionPlatformAccount.runtime_state``：idle / running / quota_exhausted /
  muted / captcha / error
- ``CollectionPhoneAccount.state``：active / suspended / retired；
  ``sms_link_state`` / ``push_link_state``：ok / down / untested
- ``CollectionRegion.state``：ok / down / arrears（欠费=人工标注）
- ``CollectionBrowser.activity``：idle / busy / captcha
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..tenancy.database import Base
from ..tenancy.models import now_utc


class CollectionPhoneAccount(Base):
    """账号管理页的行 = 一个手机号（身份主轴；无地域列——地域绑定在 ×平台 层）。"""

    __tablename__ = "collection_phone_account"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pub_id: Mapped[str] = mapped_column(String(40), unique=True)
    phone: Mapped[str] = mapped_column(Text, unique=True)
    owner_note: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, default="active")
    # 转码链路（smsforwarder 自动回码）能否联通 + 最近成功收码时间（/otp/push 回填）
    sms_link_state: Mapped[str] = mapped_column(Text, default="untested")
    last_sms_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 接管通道（方糖推送）能否联通 + 最近测试时间
    push_link_state: Mapped[str] = mapped_column(Text, default="untested")
    last_push_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class CollectionPlatformAccount(Base):
    """手机号 × 平台 = 五平台格：地域绑定 / 额度台账 / 运行时状态机 / 实例绑定。"""

    __tablename__ = "collection_platform_account"
    __table_args__ = (
        UniqueConstraint("phone_account_id", "platform"),
        Index("ix_collection_platform_account_dispatch", "platform", "region_gb", "runtime_state"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pub_id: Mapped[str] = mapped_column(String(40), unique=True)
    phone_account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("platform.collection_phone_account.id", ondelete="CASCADE"),
    )
    platform: Mapped[str] = mapped_column(Text)
    # 地域绑定（可空=未分配）；手机号不绑地域，×平台 才绑（每平台会话固定同地域出口）
    region_gb: Mapped[str | None] = mapped_column(Text)
    # 额度：预算（None=不限）+ 自记台账（成功采集计数）+ 日重置点 + 平台探测快照
    quota_day: Mapped[int | None] = mapped_column(Integer)
    quota_week: Mapped[int | None] = mapped_column(Integer)
    quota_year: Mapped[int | None] = mapped_column(Integer)
    used_today: Mapped[int] = mapped_column(Integer, default=0)
    used_week: Mapped[int] = mapped_column(Integer, default=0)
    used_year: Mapped[int] = mapped_column(Integer, default=0)
    quota_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quota_probe_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    probed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 运行时状态机（per phone×platform；合法性校验在 account_governor）
    runtime_state: Mapped[str] = mapped_column(Text, default="idle")
    current_run_pub_id: Mapped[str | None] = mapped_column(Text)
    muted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quota_resume_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state_reason: Mapped[str | None] = mapped_column(Text)
    state_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 浏览器实例绑定（一实例一平台一号；浏览器管理页「序号」反查键）
    browser_instance_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class CollectionRegion(Base):
    """地域字典——「添加地域」（悟空代理购买向导）的落点；凭证明文不落库。"""

    __tablename__ = "collection_region"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pub_id: Mapped[str] = mapped_column(String(40), unique=True)
    region_gb: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, default="wukong")
    # 代理凭证的 env 键名引用 + relay systemd 单元名
    proxy_env_key: Mapped[str | None] = mapped_column(Text)
    relay_unit: Mapped[str | None] = mapped_column(Text)
    exit_ip_last: Mapped[str | None] = mapped_column(Text)
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(Text, default="ok")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class CollectionBrowser(Base):
    """常驻浏览器实例运行时镜像（env/systemd 仍是部署真源，启动 sync 在后续 worker）。"""

    __tablename__ = "collection_browser"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pub_id: Mapped[str] = mapped_column(String(40), unique=True)
    # = GEO_BROWSER_INSTANCES 键（<platform>_<region>）
    instance_key: Mapped[str] = mapped_column(Text, unique=True)
    platform: Mapped[str] = mapped_column(Text)
    region_gb: Mapped[str | None] = mapped_column(Text)
    exit_ip: Mapped[str | None] = mapped_column(Text)
    cdp_port: Mapped[int | None] = mapped_column(Integer)
    systemd_unit: Mapped[str | None] = mapped_column(Text)
    profile_path: Mapped[str | None] = mapped_column(Text)
    activity: Mapped[str] = mapped_column(Text, default="idle")
    # 实例级失败收敛熔断（无账号行时的兜底状态载体）
    error_streak: Mapped[int] = mapped_column(Integer, default=0)
    breaker_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    muted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class CollectionAccountEvent(Base):
    """审计事件：状态迁移/额度地域修改/绑定变更/墙命中/链路测试/relay 巡检。"""

    __tablename__ = "collection_account_event"
    __table_args__ = (
        Index("ix_collection_account_event_account_created", "platform_account_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pub_id: Mapped[str] = mapped_column(String(40), unique=True)
    phone_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("platform.collection_phone_account.id")
    )
    platform_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("platform.collection_platform_account.id")
    )
    browser_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("platform.collection_browser.id")
    )
    region_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("platform.collection_region.id")
    )
    event_type: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text, default="system")
    old_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    evidence: Mapped[str | None] = mapped_column(Text)
    run_pub_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
