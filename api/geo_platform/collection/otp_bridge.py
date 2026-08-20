"""OTP 链路 → 采集账号实体的迁库旁路（migration s06_0022，设计文档 caiji-0813 §3.1）。

现状真源仍是文件链路（``runtime/otp_registered_numbers.json`` + 收件箱
``<phone>.json``）；本模块把同一事实镜像进 ``collection_phone_account``：

- ``upsert_phone_account``：``POST /otp/register`` 成功后在册号码落 DB 行
  （phone 唯一；slot/carrier 备注拼 owner_note）。
- ``record_sms_received``：``POST /otp/push`` 路由到手机号后回填
  ``last_sms_at`` + ``sms_link_state='ok'``（转码链路事实源，设计 §6.4）。

调用方（otp/router.py）以 best-effort 包裹本模块——失败只 warning，绝不阻断
现有文件链路。DB 访问模式照 account_governor：``conn`` 是 SQLAlchemy
``Session``，本模块只 flush，事务边界由调用方持有。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..tenancy.ids import new_pub_id
from ..tenancy.models import now_utc
from .account_models import CollectionPhoneAccount


def upsert_phone_account(
    conn: Session, *, phone: str, owner_note: str | None
) -> CollectionPhoneAccount:
    """按 phone 唯一 upsert 手机号行。已存在只更新 owner_note（注册动作更新、更近），
    状态/链路字段一律不动（不覆盖 OTP push 回填的 last_sms_at）。"""
    row = conn.scalar(select(CollectionPhoneAccount).where(CollectionPhoneAccount.phone == phone))
    now = now_utc()
    if row is None:
        row = CollectionPhoneAccount(
            pub_id=new_pub_id("pha"),
            phone=phone,
            owner_note=owner_note,
            state="active",
            sms_link_state="untested",
            push_link_state="untested",
            created_at=now,
            updated_at=now,
        )
        conn.add(row)
        conn.flush()
        return row
    if owner_note is not None and owner_note != row.owner_note:
        row.owner_note = owner_note
        row.updated_at = now
        conn.flush()
    return row


def record_sms_received(conn: Session, *, phone: str) -> bool:
    """回填收码事实：last_sms_at=now + sms_link_state='ok'。无行（未在册号）
    → False 如实返回（不建档——建档是注册/管理页动作的职责）。"""
    row = conn.scalar(select(CollectionPhoneAccount).where(CollectionPhoneAccount.phone == phone))
    if row is None:
        return False
    now = now_utc()
    row.last_sms_at = now
    row.sms_link_state = "ok"
    row.updated_at = now
    conn.flush()
    return True
