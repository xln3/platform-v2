# ruff: noqa: B008
from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from ..config import get_settings
from ..identity.policy import Principal, get_principal
from .object_store import ContentAddressedObjectStore
from .service import EvidenceService

router = APIRouter(prefix="/api/v2/evidence", tags=["evidence"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PackageCreate(StrictModel):
    package_pub_id: str
    evidence_pub_ids: list[str] = Field(min_length=1, max_length=200)
    public: bool = False
    expires_at: datetime | None = None


class PackageAccess(StrictModel):
    grant_token: str
    request_id: str


def _dsn() -> str:
    return get_settings().postgres_dsn.replace("postgresql+psycopg://", "postgresql://")


def _service() -> EvidenceService:
    settings = get_settings()
    store = ContentAddressedObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
    )
    return EvidenceService(dsn=_dsn(), store=store)


@router.get("/assets")
def list_assets(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("project:read")
    with psycopg.connect(_dsn(), row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT pub_id,project_pub_id,kind,access_class,sha256,mime_type,byte_size,
                   source_url,capture_time,authorized_session_capture
            FROM evidence.evidence_asset
            WHERE tenant_pub_id=%s AND deleted_at IS NULL
              AND (%s::text IS NULL OR pub_id>%s::text)
            ORDER BY pub_id LIMIT %s
            """,
            (principal.tenant_pub_id, cursor, cursor, limit + 1),
        ).fetchall()
    has_more = len(rows) > limit
    data = rows[:limit]
    return {
        "data": [dict(row) for row in data],
        "page": {
            "next_cursor": data[-1]["pub_id"] if has_more else None,
            "has_more": has_more,
        },
    }


@router.post("/packages", status_code=201)
def create_package(
    body: PackageCreate,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("project:read")
    stored = _service().create_package(
        package_pub_id=body.package_pub_id,
        tenant_pub_id=principal.tenant_pub_id,
        evidence_pub_ids=body.evidence_pub_ids,
        public=body.public,
        expires_at=body.expires_at,
    )
    return {
        "package_pub_id": body.package_pub_id,
        "manifest_sha256": stored.sha256,
        "state": "ready",
    }


@router.post("/packages/{package_pub_id}/grants", status_code=201)
def grant_package(
    package_pub_id: str,
    grant_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> dict[str, str]:
    principal.require("project:read")
    token = _service().grant(
        grant_pub_id=grant_pub_id,
        package_pub_id=package_pub_id,
        tenant_pub_id=principal.tenant_pub_id,
    )
    return {"grant_token": token}


@router.post("/packages/{package_pub_id}/revoke", status_code=204)
def revoke_package(
    package_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> Response:
    principal.require("project:read")
    try:
        _service().revoke_package(package_pub_id, principal.tenant_pub_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "package_not_found"}) from exc
    return Response(status_code=204)


@router.post("/package-access")
def access_package(body: PackageAccess) -> dict[str, Any]:
    service = _service()
    package = service.authorize_package_access(
        token=body.grant_token,
        request_id=body.request_id,
    )
    return {
        "package_pub_id": package["pub_id"],
        "manifest_sha256": package["manifest_sha256"],
        "expires_at": package["expires_at"],
        "download_url": service.store.presign_get(package["object_key"], expires_seconds=300),
    }
