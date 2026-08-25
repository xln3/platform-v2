# ruff: noqa: B008
"""信源帖子取证分析 API（/api/v2/post-analysis）。

规格：developlog/specs/post-analysis-20260806.md §6。
StrictModel extra=forbid + 游标分页 + Idempotency-Key（sop/router.py 同款先例）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from ..config import get_settings
from ..evidence.object_store import ContentAddressedObjectStore
from ..identity.policy import Principal, get_principal
from ..pagination import decode_keyset_cursor, encode_keyset_cursor
from .service import (
    PostAnalysisConflict,
    PostAnalysisInvalid,
    PostAnalysisNotFound,
    PostAnalysisService,
)

router = APIRouter(prefix="/api/v2/post-analysis", tags=["post_analysis"])

IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=16,
        max_length=128,
        pattern=r"^[\x20-\x7e]+$",
    ),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PageMeta(StrictModel):
    next_cursor: str | None
    has_more: bool


class PostAnalysisPage[PageItem: BaseModel](StrictModel):
    data: list[PageItem]
    page: PageMeta


class TaskOptions(StrictModel):
    verify_facts: bool = True
    annotate: bool = True
    # 命中后自动开 AntiGeo 调查（情报面接线，缺省开；显式 false 关闭）
    open_investigation: bool = True


class TaskCreate(StrictModel):
    target_brand: str = Field(min_length=1, max_length=200)
    target_brand_aliases: list[str] = Field(default_factory=list, max_length=20)
    urls: list[str] = Field(min_length=1, max_length=50)
    options: TaskOptions = Field(default_factory=lambda: TaskOptions())


class TaskView(StrictModel):
    pub_id: str
    target_brand: str
    target_brand_aliases: list[Any]
    status: str
    url_count: int
    options: dict[str, Any]
    workflow_id: str
    error: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class TaskDetailView(TaskView):
    status_counts: dict[str, int]
    # finalize 侧车建案结果（options JSONB 投影；未开案为 null）
    investigation_pub_id: str | None


class ItemListRow(StrictModel):
    pub_id: str
    ordinal: int
    url: str
    host: str
    status: str
    annotation_status: str
    category: str | None
    category_label: str | None
    is_geo_post: bool | None
    is_target_brand_geo: bool | None
    disparagement_count: int
    misinformation_count: int
    error: str | None
    created_at: datetime
    updated_at: datetime


class ItemAssetRef(StrictModel):
    """evidence 资产完整性三元组（前端 verified-Blob 边界：先取此 JSON 再拉字节校验）。"""

    sha256: str
    byte_size: int
    mime_type: str


class ItemDetailView(StrictModel):
    pub_id: str
    ordinal: int
    url: str
    url_hash: str
    host: str
    status: str
    annotation_status: str
    final_url: str | None
    http_status: int | None
    extractor: str | None
    text_sha256: str | None
    analysis: dict[str, Any] | None
    analysis_validation: dict[str, Any] | None
    annotations: list[Any] | None
    error: str | None
    has_screenshot: bool
    has_annotated: bool
    screenshot_asset: ItemAssetRef | None
    annotated_asset: ItemAssetRef | None
    created_at: datetime
    updated_at: datetime


def _dsn() -> str:
    settings = get_settings()
    return (settings.runtime_postgres_dsn or settings.postgres_dsn).replace(
        "postgresql+psycopg://", "postgresql://"
    )


def _service() -> PostAnalysisService:
    settings = get_settings()
    return PostAnalysisService(
        dsn=_dsn(),
        max_urls_per_task=settings.post_analysis_max_urls_per_task,
        object_store=ContentAddressedObjectStore(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
        ),
    )


@contextmanager
def _service_errors() -> Iterator[None]:
    try:
        yield
    except PostAnalysisNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found"}) from exc
    except PostAnalysisConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"}) from exc
    except PostAnalysisInvalid as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_url"}) from exc


def _page(
    rows: list[dict[str, Any]],
    *,
    limit: int,
    validator: Callable[[Mapping[str, Any]], BaseModel],
    kind: str,
    tenant_pub_id: str,
    filters: Mapping[str, str | None],
) -> dict[str, Any]:
    has_more = len(rows) > limit
    visible = rows[:limit]
    data = [validator(row) for row in visible]
    return {
        "data": data,
        "page": {
            "next_cursor": (
                encode_keyset_cursor(
                    kind=kind,
                    tenant_pub_id=tenant_pub_id,
                    filters=filters,
                    created_at=visible[-1]["created_at"],
                    pub_id=str(visible[-1]["pub_id"]),
                )
                if has_more and visible
                else None
            ),
            "has_more": has_more,
        },
    }


@router.post("/tasks", response_model=TaskView, status_code=201)
def create_task(
    body: TaskCreate,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    principal: Principal = Depends(get_principal),
) -> TaskView:
    principal.require("project:write")
    with _service_errors():
        row, created = _service().create_task(
            tenant_pub_id=principal.tenant_pub_id,
            created_by_pub_id=principal.actor_pub_id,
            target_brand=body.target_brand,
            target_brand_aliases=body.target_brand_aliases,
            urls=body.urls,
            options=body.options.model_dump(),
            idempotency_key=idempotency_key,
            task_queue=get_settings().analysis_temporal_task_queue,
            source_task_queue=get_settings().source_temporal_task_queue,
        )
    if idempotency_key is not None:
        response.headers["Idempotency-Key"] = idempotency_key
    if not created:
        response.status_code = 200
    return TaskView.model_validate(row)


@router.get("/tasks", response_model=PostAnalysisPage[TaskView])
def list_tasks(
    cursor: str | None = Query(default=None, min_length=16, max_length=2_048),
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("project:read")
    filters: dict[str, str | None] = {}
    anchor = (
        decode_keyset_cursor(
            cursor,
            kind="post-analysis-tasks",
            tenant_pub_id=principal.tenant_pub_id,
            filters=filters,
        )
        if cursor is not None
        else None
    )
    rows = _service().list_tasks(
        tenant_pub_id=principal.tenant_pub_id,
        cursor=anchor.pub_id if anchor else None,
        limit=limit,
    )
    return _page(
        rows,
        limit=limit,
        validator=TaskView.model_validate,
        kind="post-analysis-tasks",
        tenant_pub_id=principal.tenant_pub_id,
        filters=filters,
    )


@router.get("/tasks/{task_pub_id}", response_model=TaskDetailView)
def get_task(
    task_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> TaskDetailView:
    principal.require("project:read")
    with _service_errors():
        row = _service().get_task(tenant_pub_id=principal.tenant_pub_id, task_pub_id=task_pub_id)
    return TaskDetailView.model_validate(row)


@router.get("/tasks/{task_pub_id}/items", response_model=PostAnalysisPage[ItemListRow])
def list_items(
    task_pub_id: str,
    cursor: str | None = Query(default=None, min_length=16, max_length=2_048),
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    principal.require("project:read")
    filters = {"task_pub_id": task_pub_id}
    anchor = (
        decode_keyset_cursor(
            cursor,
            kind="post-analysis-items",
            tenant_pub_id=principal.tenant_pub_id,
            filters=filters,
        )
        if cursor is not None
        else None
    )
    with _service_errors():
        rows = _service().list_items(
            tenant_pub_id=principal.tenant_pub_id,
            task_pub_id=task_pub_id,
            cursor=anchor.pub_id if anchor else None,
            limit=limit,
        )
    return _page(
        rows,
        limit=limit,
        validator=ItemListRow.model_validate,
        kind="post-analysis-items",
        tenant_pub_id=principal.tenant_pub_id,
        filters=filters,
    )


@router.get("/items/{item_pub_id}", response_model=ItemDetailView)
def get_item(
    item_pub_id: str,
    principal: Principal = Depends(get_principal),
) -> ItemDetailView:
    principal.require("project:read")
    with _service_errors():
        row = _service().get_item(tenant_pub_id=principal.tenant_pub_id, item_pub_id=item_pub_id)
    return ItemDetailView.model_validate(row)


@router.get(
    "/items/{item_pub_id}/assets/{kind}",
    response_class=Response,
    responses={200: {"content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}}},
)
def get_item_asset(
    item_pub_id: str,
    kind: str,
    principal: Principal = Depends(get_principal),
) -> Response:
    """截图/标注图字节流（evidence 资产下载先例：完整性校验后代理，不暴露对象键）。"""
    principal.require("project:read")
    with _service_errors():
        try:
            payload, mime_type, digest = _service().get_item_asset(
                tenant_pub_id=principal.tenant_pub_id,
                item_pub_id=item_pub_id,
                kind=kind,
            )
        except PostAnalysisNotFound:
            raise
        except ValueError as exc:
            raise HTTPException(
                status_code=409, detail={"code": "evidence_integrity_failed"}
            ) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail={"code": "evidence_object_unavailable"}
            ) from exc
    return Response(
        content=payload,
        media_type=mime_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'inline; filename="{item_pub_id}-{kind}"',
            "X-Content-Type-Options": "nosniff",
            "X-Evidence-SHA256": digest,
        },
    )
