"""Provision an idempotent, no-spend sandbox for the complete GEO workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path

from geo_platform.config import get_settings
from geo_platform.identity.native_session import set_native_password
from geo_platform.projects.models import (
    Customer,
    MonitoringConfig,
    MonitoringConfigVersion,
    Project,
)
from geo_platform.tenancy.ids import new_pub_id
from geo_platform.tenancy.models import Membership, Tenant, User
from geo_platform.tenancy.repository import set_tenant_context
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

TENANT_PUB_ID = "tnt_training_sandbox"
PROJECT_PUB_ID = "prj_training_full_flow"
ROLES = ("admin", "operator", "analyst", "reviewer", "customer")
PLATFORMS = ("doubao", "deepseek", "yiyan", "tongyi", "yuanbao")


def password() -> str:
    return f"G3o-{secrets.token_urlsafe(22)}-Aa"


def write_credentials(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    path.chmod(0o600)


def provision(session: Session) -> dict[str, object]:
    tenant = session.scalar(select(Tenant).where(Tenant.pub_id == TENANT_PUB_ID))
    if tenant is None:
        tenant = Tenant(
            pub_id=TENANT_PUB_ID,
            name="GEO 全流程安全演练",
            state="active",
            environment="training",
        )
        session.add(tenant)
        session.flush()
    else:
        tenant.name = "GEO 全流程安全演练"
        tenant.environment = "training"
        tenant.state = "active"

    credentials: list[dict[str, str]] = []
    users: dict[str, User] = {}
    for role in ROLES:
        email = f"training-{role}@geo.training"
        user = session.scalar(select(User).where(User.subject == email))
        if user is None:
            user = User(
                pub_id=new_pub_id("usr"),
                subject=email,
                display_name=f"演练 {role}",
                is_service_account=False,
            )
            session.add(user)
            session.flush()
        membership = session.scalar(
            select(Membership).where(
                Membership.tenant_id == tenant.id,
                Membership.user_id == user.id,
            )
        )
        if membership is None:
            membership = Membership(
                pub_id=new_pub_id("mem"),
                tenant_id=tenant.id,
                user_id=user.id,
                role=role,
                state="active",
            )
            session.add(membership)
        else:
            membership.role = role
            membership.state = "active"
            membership.revoked_at = None
        generated = password()
        set_native_password(
            session,
            tenant_id=tenant.id,
            user_id=user.id,
            password=generated,
        )
        credentials.append({"role": role, "email": email, "password": generated})
        users[role] = user

    set_tenant_context(session, tenant_id=tenant.id, tenant_pub_id=tenant.pub_id)
    project = session.scalar(
        select(Project).where(Project.tenant_id == tenant.id, Project.pub_id == PROJECT_PUB_ID)
    )
    if project is None:
        customer = Customer(
            pub_id="cst_training_brand",
            tenant_id=tenant.id,
            name="演练品牌（不对外）",
        )
        session.add(customer)
        session.flush()
        project = Project(
            pub_id=PROJECT_PUB_ID,
            tenant_id=tenant.id,
            customer_id=customer.id,
            name="五平台 GEO 全流程演练",
            state="active",
        )
        session.add(project)
        session.flush()

    config = session.scalar(
        select(MonitoringConfig).where(
            MonitoringConfig.tenant_id == tenant.id,
            MonitoringConfig.project_id == project.id,
        )
    )
    if config is None:
        config = MonitoringConfig(
            pub_id="cfg_training_full_flow",
            tenant_id=tenant.id,
            project_id=project.id,
            state="frozen",
            current_version=1,
        )
        session.add(config)
        session.flush()
        snapshot = {
            "query_groups": [
                {
                    "name": "演练问题",
                    "items": [
                        {"text": "演练品牌的公开定位是什么？", "priority": 1},
                        {"text": "演练品牌有哪些可验证优势？", "priority": 2},
                    ],
                }
            ],
            "regions": ["CN"],
            "models": list(PLATFORMS),
            "modes": ["normal"],
            "frequency": "training-only",
            "effective_at": datetime.now(UTC).isoformat(),
        }
        canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        version = MonitoringConfigVersion(
            pub_id="cfv_training_v1",
            tenant_id=tenant.id,
            config_id=config.id,
            revision=1,
            effective_at=datetime.now(UTC),
            frozen_at=datetime.now(UTC),
            snapshot_json=canonical,
            snapshot_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        )
        session.add(version)
        session.flush()
    else:
        version = session.scalar(
            select(MonitoringConfigVersion)
            .where(
                MonitoringConfigVersion.tenant_id == tenant.id,
                MonitoringConfigVersion.config_id == config.id,
            )
            .order_by(MonitoringConfigVersion.revision.desc())
        )
        if version is None:
            raise RuntimeError("training config exists without a frozen version")

    session.execute(
        text(
            """
            INSERT INTO platform.monitoring_schedule
              (id,pub_id,tenant_id,project_id,config_version_id,interval_minutes,
               timezone,state,next_run_at,responsible_pub_id,created_by_pub_id)
            VALUES (:id,'sch_training_paused',:tenant_id,:project_id,:version_id,10080,
                    'Asia/Shanghai','paused',now() + interval '7 days',:actor,:actor)
            ON CONFLICT (pub_id) DO UPDATE
            SET state='paused',updated_at=now(),responsible_pub_id=EXCLUDED.responsible_pub_id
            """
        ),
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant.id,
            "project_id": project.id,
            "version_id": version.id,
            "actor": users["operator"].pub_id,
        },
    )
    session.commit()
    return {
        "tenant_pub_id": tenant.pub_id,
        "project_pub_id": project.pub_id,
        "config_version_pub_id": version.pub_id,
        "schedule_pub_id": "sch_training_paused",
        "distribution_mode": "simulated_no_spend",
        "credentials": credentials,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("runtime/private/training-credentials.json"),
    )
    parser.add_argument("--allow-production", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if settings.env.lower() in {"production", "prod"} and not args.allow_production:
        raise SystemExit("Production provisioning requires --allow-production")
    dsn = settings.postgres_dsn
    if dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(dsn, pool_pre_ping=True)
    with Session(engine) as session:
        result = provision(session)
    write_credentials(args.output, result)
    print(
        json.dumps(
            {
                "status": "provisioned",
                "tenant_pub_id": result["tenant_pub_id"],
                "project_pub_id": result["project_pub_id"],
                "credentials_file": str(args.output),
                "file_mode": "0600",
                "distribution_mode": result["distribution_mode"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
