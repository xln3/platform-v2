# ruff: noqa: B008
# mypy: disable-error-code="arg-type"

import hashlib
import json
import secrets
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..contracts import WorkflowAccepted
from ..evidence.object_store import ContentAddressedObjectStore
from ..identity.policy import Principal, get_principal
from ..projects.models import MonitoringConfigVersion, Project
from ..tenancy.database import get_db
from ..tenancy.ids import new_pub_id
from ..tenancy.models import AuditLog
from ..tenancy.repository import TenantRepository
from .authorization import replace_account_authorization
from .leases import FenceViolationError, assert_fenced_write
from .models import (
    AccountAuthorization,
    BrowserProfile,
    CollectionRun,
    CollectionTask,
    InterventionRequest,
    PlatformAccount,
    PlatformAdapter,
    RevocationRequest,
    SessionEvent,
    SessionHealthCheck,
    SessionLease,
    TerminalTask,
)
from .revocation import stage_account_revocation
from .run_service import stage_collection_run
from .terminal_protocol import (
    fingerprint,
    normalize_allowed_domain,
    public_key_bytes,
    task_signing_key,
)
from .vault import LocalKms, ProfileVault, SealedProfile, VaultTransitKms, profile_aad
from .workflow_outbox import (
    WorkflowSignalConflictError,
    enqueue_workflow_signal,
    enqueue_workflow_start,
    workflow_signal_replayed,
)

router = APIRouter(prefix="/api/v2", tags=["collection"])
settings = get_settings()


def _profile_vault() -> ProfileVault:
    """Return the development vault only outside production.

    LocalKms keeps its wrapping key in the API process configuration and cannot
    provide an independently retained deletion authority across database
    restores. Production profile custody therefore stays fail-closed until an
    external KMS implementation is configured.
    """
    if settings.env.lower() in {"production", "prod"}:
        if settings.kms_provider != "vault_transit":
            raise HTTPException(status_code=503, detail={"code": "profile_vault_unavailable"})
        try:
            kms = VaultTransitKms(
                settings.vault_transit_address,
                settings.vault_transit_token_file,
                settings.vault_transit_key_name,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=503, detail={"code": "profile_vault_unavailable"}
            ) from exc
        return ProfileVault(kms)
    return ProfileVault(LocalKms(settings.kms_master_key))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunCreate(StrictModel):
    project_pub_id: str
    config_version_pub_id: str
    requires_intervention: bool = False
    account_pub_id: str | None = None


class RunView(StrictModel):
    pub_id: str
    project_pub_id: str
    config_version_pub_id: str
    workflow_id: str
    temporal_run_id: str | None
    state: str
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    paused: bool
    error_code: str | None
    source: str
    schedule_pub_id: str | None
    retry_of_run_pub_id: str | None
    initiated_by_pub_id: str | None
    updated_at: datetime


class AccountCreate(StrictModel):
    platform_slug: str
    platform_name: str
    account_mask: str
    owner_pub_id: str
    purpose: str
    responsible_pub_id: str
    custody_mode: Literal["server", "customer_device", "hybrid"]
    region: str


class AdapterView(StrictModel):
    pub_id: str
    slug: str
    display_name: str
    admission_level: str
    capabilities: list[str]
    adapter_version: str
    last_passed_at: datetime | None


class AccountView(StrictModel):
    pub_id: str
    platform: str
    account_mask: str
    owner_pub_id: str
    purpose: str
    responsible_pub_id: str
    custody_mode: str
    region: str
    state: str
    admission_level: str
    last_passed_at: datetime | None
    scopes: list[str]
    authorization_expires_at: datetime | None
    profile_state: str | None
    profile_version: int | None
    profile_constraints: list[str]
    profile_expires_at: datetime | None
    lease_expires_at: datetime | None


class AuthorizationCreate(StrictModel):
    scopes: list[Literal["read", "query", "draft", "publish"]]
    forbidden_actions: list[str] = Field(default_factory=list)
    regions: list[str]
    valid_from: datetime
    valid_until: datetime


class ProfileEnroll(StrictModel):
    profile_payload: str | None = None
    custody_mode: Literal["server", "customer_device", "hybrid"]
    constraints: list[Literal["DEVICE_BOUND", "READ_ONLY"]] = Field(default_factory=list)
    expires_at: datetime | None = None


class ProfileView(StrictModel):
    pub_id: str
    profile_version: int
    custody_mode: str
    state: str
    constraints: list[str]
    ciphertext_sha256: str | None
    expires_at: datetime | None


class ProfileSeal(StrictModel):
    lease_pub_id: str
    fencing_token: int
    expected_profile_version: int = Field(ge=1)
    profile_payload: str
    expires_at: datetime | None = None


class ProfileRekey(StrictModel):
    lease_pub_id: str
    fencing_token: int
    expected_profile_version: int = Field(ge=1)
    reason: Literal["scheduled_rotation", "key_policy_change", "incident_recovery"]


class InterventionCreate(StrictModel):
    challenge_type: Literal["otp", "qr", "push", "passkey", "face", "graphical"]
    allowed_domain: str = Field(min_length=3, max_length=255)
    action: Literal["read", "query", "draft", "publish"]
    run_pub_id: str | None = None

    @field_validator("allowed_domain")
    @classmethod
    def hostname_only(cls, value: str) -> str:
        return normalize_allowed_domain(value)


class InterventionView(StrictModel):
    pub_id: str
    account_pub_id: str
    account_mask: str
    challenge_type: str
    allowed_domain: str
    action: str
    state: str
    pairing_expires_at: datetime | None
    platform_result: str | None
    assigned_to_pub_id: str | None = None
    due_at: datetime | None = None
    resolution_note: str = ""


class InterventionAssignment(StrictModel):
    assigned_to_pub_id: str = Field(min_length=5, max_length=30)
    due_at: datetime


class InterventionResolution(StrictModel):
    reason: str = Field(min_length=3, max_length=500)


class PairingView(StrictModel):
    intervention_pub_id: str
    pairing_token: str
    server_public_key_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    allowed_domain: str
    action: Literal["read", "query", "draft", "publish"]
    challenge_type: Literal["otp", "qr", "push", "passkey", "face", "graphical"]
    expires_at: datetime


class CompleteIntervention(StrictModel):
    pairing_token: str
    platform_result: Literal["verified", "failed", "expired", "rejected"]
    evidence_hash: str = Field(pattern="^[a-f0-9]{64}$")


class PlatformAttestation(StrictModel):
    proof_source: Literal["platform_callback", "identity_probe"]
    platform_result: Literal["verified", "failed", "expired", "rejected"]
    evidence_hash: str = Field(pattern="^[a-f0-9]{64}$")


class EventView(StrictModel):
    pub_id: str
    event_type: str
    summary: dict[str, Any]
    occurred_at: datetime


def account_view(
    account: PlatformAccount, adapter: PlatformAdapter, session: Session
) -> AccountView:
    now = datetime.now(UTC)
    authorization = session.scalar(
        select(AccountAuthorization)
        .where(
            AccountAuthorization.account_id == account.id,
            AccountAuthorization.revoked_at.is_(None),
            AccountAuthorization.valid_from <= now,
            AccountAuthorization.valid_until > now,
        )
        .order_by(AccountAuthorization.valid_until.desc())
    )
    profile = session.scalar(
        select(BrowserProfile)
        .where(BrowserProfile.account_id == account.id)
        .order_by(BrowserProfile.profile_version.desc())
    )
    lease = session.scalar(
        select(SessionLease)
        .where(
            SessionLease.account_id == account.id,
            SessionLease.released_at.is_(None),
            SessionLease.expires_at > now,
        )
        .order_by(SessionLease.expires_at.desc())
    )
    return AccountView(
        pub_id=account.pub_id,
        platform=adapter.slug,
        account_mask=account.account_mask,
        owner_pub_id=account.owner_pub_id,
        purpose=account.purpose,
        responsible_pub_id=account.responsible_pub_id,
        custody_mode=account.custody_mode,
        region=account.region,
        state=account.state,
        admission_level=account.admission_level,
        last_passed_at=adapter.last_passed_at,
        scopes=json.loads(authorization.scopes_json) if authorization else [],
        authorization_expires_at=authorization.valid_until if authorization else None,
        profile_state=profile.state if profile else None,
        profile_version=profile.profile_version if profile else None,
        profile_constraints=json.loads(profile.constraints_json) if profile else [],
        profile_expires_at=profile.expires_at if profile else None,
        lease_expires_at=lease.expires_at if lease else None,
    )


@router.post(
    "/collection/runs",
    response_model=WorkflowAccepted,
    status_code=202,
    operation_id="startCollectionRun",
)
async def start_collection_run(
    body: RunCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=16, max_length=128),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> WorkflowAccepted:
    principal.require("collection:control")
    repository = TenantRepository(session, principal.tenant_pub_id)
    existing = session.scalar(
        select(CollectionRun).where(
            CollectionRun.tenant_id == repository.tenant.id,
            CollectionRun.idempotency_key == idempotency_key,
        )
    )
    if existing:
        persisted_payload = session.execute(
            text(
                """
                SELECT payload
                FROM integration.workflow_start_command
                WHERE workflow_id=:workflow_id
                """
            ),
            {"workflow_id": existing.workflow_id},
        ).scalar_one_or_none()
        if persisted_payload is not None:
            requested_contract = {
                "project_pub_id": body.project_pub_id,
                "config_version_pub_id": body.config_version_pub_id,
                "requires_intervention": body.requires_intervention,
                "account_pub_id": body.account_pub_id,
            }
            persisted_contract = {key: persisted_payload.get(key) for key in requested_contract}
            if persisted_contract != requested_contract:
                raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"})
        return WorkflowAccepted(workflow_id=existing.workflow_id, run_id=existing.temporal_run_id)
    try:
        run = stage_collection_run(
            session,
            tenant_id=repository.tenant.id,
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=body.project_pub_id,
            config_version_pub_id=body.config_version_pub_id,
            idempotency_key=idempotency_key,
            initiated_by_pub_id=principal.actor_pub_id,
            source="manual",
            requires_intervention=body.requires_intervention,
            account_pub_id=body.account_pub_id,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "project_or_config_not_found"}
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
    session.commit()
    return WorkflowAccepted(workflow_id=run.workflow_id)


@router.post("/platform-accounts/{account_pub_id}/quarantine", response_model=AccountView)
def quarantine_account(
    account_pub_id: str,
    reason: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> AccountView:
    principal.require("account:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    account = find_account(session, repository.tenant.id, account_pub_id)
    if account.state == "revoked":
        raise HTTPException(status_code=409, detail={"code": "account_revoked"})
    account.state = "quarantined"
    now = datetime.now(UTC)
    session.execute(
        update(SessionLease)
        .where(SessionLease.account_id == account.id, SessionLease.released_at.is_(None))
        .values(released_at=now)
    )
    profiles = session.scalars(
        select(BrowserProfile).where(BrowserProfile.account_id == account.id)
    ).all()
    for profile in profiles:
        profile.state = "QUARANTINED"
    session.add(
        SessionEvent(
            pub_id=new_pub_id("sev"),
            tenant_id=repository.tenant.id,
            account_id=account.id,
            event_type="account.quarantined",
            summary_json=json.dumps({"reason": reason}),
        )
    )
    adapter = session.get(PlatformAdapter, account.adapter_id)
    assert adapter is not None
    session.commit()
    return account_view(account, adapter, session)


@router.get("/collection/runs", response_model=list[RunView])
def list_runs(
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[RunView]:
    principal.require("project:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    rows = session.scalars(
        select(CollectionRun)
        .where(CollectionRun.tenant_id == repository.tenant.id)
        .order_by(CollectionRun.created_at.desc())
        .limit(limit)
    ).all()
    projects = {
        item.id: item.pub_id
        for item in session.scalars(
            select(Project).where(Project.tenant_id == repository.tenant.id)
        ).all()
    }
    config_versions = {
        item.id: item.pub_id
        for item in session.scalars(
            select(MonitoringConfigVersion).where(
                MonitoringConfigVersion.tenant_id == repository.tenant.id
            )
        ).all()
    }
    return [
        RunView(
            pub_id=item.pub_id,
            project_pub_id=projects[item.project_id],
            config_version_pub_id=config_versions[item.config_version_id],
            workflow_id=item.workflow_id,
            temporal_run_id=item.temporal_run_id,
            state=item.state,
            total_tasks=item.total_tasks,
            completed_tasks=item.completed_tasks,
            failed_tasks=item.failed_tasks,
            paused=item.paused,
            error_code=item.error_code,
            source=item.source,
            schedule_pub_id=item.schedule_pub_id,
            retry_of_run_pub_id=item.retry_of_run_pub_id,
            initiated_by_pub_id=item.initiated_by_pub_id,
            updated_at=item.updated_at,
        )
        for item in rows
    ]


class TraceReasoningStep(StrictModel):
    kind: str
    text: str | None = None
    queries: list[str] = Field(default_factory=list)
    summary: str | None = None


class TraceSearchResult(StrictModel):
    title: str
    url: str | None
    site: str | None
    rank: int | str | None = None
    summary: str
    status: str


class TraceSearchBlock(StrictModel):
    scene: int | None
    queries: list[str]
    summary: str
    result_count: int
    results: list[TraceSearchResult]


class TraceAnswer(StrictModel):
    id: str
    query: str | None
    mode: str | None
    engine: str | None
    region: str | None
    tick_time: str | None
    response_text: str


class TraceTotals(StrictModel):
    queries: int
    results: int
    surfaced_reasoning_steps: int
    response_text_truncated: bool


class TraceSearchQuery(StrictModel):
    query: str
    ordinal: int


class TaskTraceView(StrictModel):
    answer: TraceAnswer
    deep_think_active: bool
    thinking_title: str | None
    reasoning: list[TraceReasoningStep]
    search_blocks: list[TraceSearchBlock]
    search_queries: list[TraceSearchQuery]
    totals: TraceTotals
    disclosure: str


_TRACE_DISCLOSURE = (
    "仅展示豆包明确传输到浏览器的检索与公开思考步骤；"
    "未返回的内部推理、资料取舍原因不可据此推断。"
)
_TRACE_TEXT_LIMIT = 5_000  # 单段公开思考/回答正文的响应截断上限（对齐旧链口径）


def build_task_trace_view(
    *,
    task_pub_id: str,
    matrix: Mapping[str, Any],
    answer_text: str | None,
    tick_time: str | None,
    stored_search_queries: list[dict[str, Any]],
    trace_record: Mapping[str, Any],
) -> TaskTraceView:
    """把 task 行 + SSE 结构化 trace record 整形成回放响应（纯函数，可单测）。

    语义对齐旧链 server/geosys/api.py 的 research-trace：只暴露平台明确传输到
    浏览器的检索与公开思考内容，绝不含 HAR/headers/cookies。
    """
    reasoning: list[TraceReasoningStep] = []
    for step in trace_record.get("thinking_chain") or []:
        if not isinstance(step, Mapping):
            continue
        if step.get("kind") == "reasoning" and step.get("text"):
            reasoning.append(
                TraceReasoningStep(
                    kind="surfaced_reasoning", text=str(step["text"])[:_TRACE_TEXT_LIMIT]
                )
            )
        elif step.get("kind") == "search":
            reasoning.append(
                TraceReasoningStep(
                    kind="search",
                    queries=[str(q) for q in step.get("queries") or []],
                    summary=str(step.get("summary") or ""),
                )
            )
    search_blocks: list[TraceSearchBlock] = []
    for block in trace_record.get("search_blocks") or []:
        if not isinstance(block, Mapping):
            continue
        results = [
            TraceSearchResult(
                title=str(x.get("title") or "未命名来源")
                if isinstance(x, Mapping)
                else "未命名来源",
                url=str(x.get("url")) if isinstance(x, Mapping) and x.get("url") else None,
                site=(str(x.get("site")) if isinstance(x, Mapping) and x.get("site") else None),
                rank=x.get("rank") if isinstance(x, Mapping) else None,
                summary=(str(x.get("summary") or "")[:800] if isinstance(x, Mapping) else ""),
                status="returned_reference",
            )
            for x in block.get("results") or []
        ]
        scene = block.get("scene")
        search_blocks.append(
            TraceSearchBlock(
                scene=int(scene) if isinstance(scene, int) else None,
                queries=[str(q) for q in block.get("queries") or []],
                summary=str(block.get("summary") or ""),
                result_count=len(results),
                results=results,
            )
        )
    response_text = answer_text or ""
    response_truncated = len(response_text) > _TRACE_TEXT_LIMIT
    return TaskTraceView(
        answer=TraceAnswer(
            id=task_pub_id,
            query=str(matrix["query"]) if matrix.get("query") else None,
            mode=str(matrix["mode"]) if matrix.get("mode") else None,
            engine=str(matrix["model"]) if matrix.get("model") else None,
            region=str(matrix["region"]) if matrix.get("region") else None,
            tick_time=tick_time,
            response_text=response_text[:_TRACE_TEXT_LIMIT],
        ),
        deep_think_active=bool(trace_record.get("deep_think_active")),
        thinking_title=(
            str(trace_record["thinking_title"]) if trace_record.get("thinking_title") else None
        ),
        reasoning=reasoning,
        search_blocks=search_blocks,
        search_queries=[
            TraceSearchQuery(
                query=str(item.get("query") or ""), ordinal=int(item.get("ordinal") or 0)
            )
            for item in stored_search_queries
            if isinstance(item, Mapping)
        ],
        totals=TraceTotals(
            queries=sum(len(b.queries) for b in search_blocks),
            results=sum(b.result_count for b in search_blocks),
            surfaced_reasoning_steps=len(
                [s for s in reasoning if s.kind == "surfaced_reasoning"]
            ),
            response_text_truncated=response_truncated,
        ),
        disclosure=_TRACE_DISCLOSURE,
    )


def resolve_task_trace(
    *,
    task_pub_id: str,
    matrix: Mapping[str, Any],
    answer_text: str | None,
    tick_time: str | None,
    stored_search_queries: list[dict[str, Any]],
    asset_rows: list[Mapping[str, Any]],
    blob_loader: Callable[[str, str], bytes],
) -> TaskTraceView:
    """sse 证据定位 + CAS 读取 + 整形（纯函数；404 分支可单测）。

    asset_rows 为空 → sse_evidence_missing（对齐旧链）；blob 读取/校验/解析失败 →
    sse_blob_missing（证据登记了但内容不可用，如实区分，绝不编造）。
    """
    if not asset_rows:
        raise HTTPException(status_code=404, detail={"code": "sse_evidence_missing"})
    row = asset_rows[0]
    try:
        blob = blob_loader(str(row["object_key"]), str(row["sha256"]))
        trace_record = json.loads(blob.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=404, detail={"code": "sse_blob_missing"}) from exc
    if not isinstance(trace_record, dict):
        raise HTTPException(status_code=404, detail={"code": "sse_blob_missing"})
    return build_task_trace_view(
        task_pub_id=task_pub_id,
        matrix=matrix,
        answer_text=answer_text,
        tick_time=tick_time,
        stored_search_queries=stored_search_queries,
        trace_record=trace_record,
    )


@router.get("/collection/tasks/{task_pub_id}/trace", response_model=TaskTraceView)
def collection_task_trace(
    task_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> TaskTraceView:
    """回放采集任务的豆包 SSE 结构化 trace（W1；对齐旧链 research-trace 语义）。"""
    principal.require("project:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    task = session.scalar(
        select(CollectionTask).where(
            CollectionTask.tenant_id == repository.tenant.id,
            CollectionTask.pub_id == task_pub_id,
        )
    )
    if task is None:
        raise HTTPException(status_code=404, detail={"code": "task_not_found"})
    asset_rows = (
        session.execute(
            text(
                """
                SELECT ea.object_key, ea.sha256
                FROM evidence.evidence_relation er
                JOIN evidence.evidence_asset ea
                  ON ea.tenant_pub_id=er.tenant_pub_id AND ea.pub_id=er.to_pub_id
                WHERE er.tenant_pub_id=:tenant_pub_id AND er.from_pub_id=:answer_pub_id
                  AND er.relation_type='answer_sse_trace' AND ea.kind='sse'
                  AND ea.deleted_at IS NULL
                ORDER BY ea.capture_time DESC, ea.pub_id DESC
                LIMIT 1
                """
            ),
            {"tenant_pub_id": principal.tenant_pub_id, "answer_pub_id": task.pub_id},
        )
        .mappings()
        .all()
    )
    store = ContentAddressedObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
    )
    try:
        matrix = json.loads(task.matrix_json or "{}")
    except ValueError:
        matrix = {}
    try:
        stored_search_queries = json.loads(task.search_queries_json or "[]")
    except ValueError:
        stored_search_queries = []
    return resolve_task_trace(
        task_pub_id=task.pub_id,
        matrix=matrix if isinstance(matrix, dict) else {},
        answer_text=task.answer_text,
        tick_time=task.created_at.astimezone(UTC).isoformat(),
        stored_search_queries=(
            stored_search_queries if isinstance(stored_search_queries, list) else []
        ),
        asset_rows=[dict(row) for row in asset_rows],
        blob_loader=store.get_verified,
    )


@router.post("/collection/runs/{run_pub_id}/{action}", response_model=WorkflowAccepted)
async def control_run(
    run_pub_id: str,
    action: Literal["pause", "resume", "cancel", "retry"],
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=16, max_length=128),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> WorkflowAccepted:
    principal.require("collection:control")
    repository = TenantRepository(session, principal.tenant_pub_id)
    run = session.scalar(
        select(CollectionRun)
        .where(CollectionRun.tenant_id == repository.tenant.id, CollectionRun.pub_id == run_pub_id)
        .with_for_update()
    )
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "run_not_found"})
    if action == "retry":
        if run.state not in {"completed_with_failures", "failed", "cancelled", "skipped"}:
            raise HTTPException(status_code=409, detail={"code": "run_not_retryable"})
        replay = session.scalar(
            select(CollectionRun).where(
                CollectionRun.tenant_id == repository.tenant.id,
                CollectionRun.idempotency_key == idempotency_key,
            )
        )
        if replay is not None:
            return WorkflowAccepted(workflow_id=replay.workflow_id, run_id=replay.temporal_run_id)
        project = session.get(Project, run.project_id)
        config = session.get(MonitoringConfigVersion, run.config_version_id)
        if project is None or config is None:
            raise HTTPException(status_code=409, detail={"code": "retry_source_incomplete"})
        retry_run = stage_collection_run(
            session,
            tenant_id=repository.tenant.id,
            tenant_pub_id=principal.tenant_pub_id,
            project_pub_id=project.pub_id,
            config_version_pub_id=config.pub_id,
            idempotency_key=idempotency_key,
            initiated_by_pub_id=principal.actor_pub_id,
            source="retry",
            retry_of_run_pub_id=run.pub_id,
        )
        session.commit()
        return WorkflowAccepted(workflow_id=retry_run.workflow_id)
    if action in {"pause", "resume", "cancel"}:
        try:
            if workflow_signal_replayed(
                session,
                tenant_pub_id=principal.tenant_pub_id,
                workflow_id=run.workflow_id,
                signal_name=action,
                args=[],
                idempotency_key=idempotency_key,
            ):
                return WorkflowAccepted(workflow_id=run.workflow_id, run_id=run.temporal_run_id)
        except WorkflowSignalConflictError as error:
            raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"}) from error
    if run.state in {
        "completed",
        "completed_with_failures",
        "failed",
        "cancelled",
        "skipped",
    }:
        raise HTTPException(status_code=409, detail={"code": "run_terminal"})
    if run.state == "cancelling":
        if action == "cancel":
            return WorkflowAccepted(workflow_id=run.workflow_id, run_id=run.temporal_run_id)
        raise HTTPException(status_code=409, detail={"code": "run_cancelling"})
    if action == "pause" and run.paused:
        return WorkflowAccepted(workflow_id=run.workflow_id, run_id=run.temporal_run_id)
    if action == "resume" and not run.paused:
        return WorkflowAccepted(workflow_id=run.workflow_id, run_id=run.temporal_run_id)
    if action in {"pause", "resume", "cancel"}:
        run.paused = action == "pause"
        if action == "cancel":
            run.state = "cancelling"
        try:
            enqueue_workflow_signal(
                session,
                tenant_pub_id=principal.tenant_pub_id,
                workflow_id=run.workflow_id,
                signal_name=action,
                args=[],
                idempotency_key=idempotency_key,
            )
        except WorkflowSignalConflictError as error:
            session.rollback()
            raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"}) from error
    else:
        raise HTTPException(status_code=409, detail={"code": "retry_requires_new_run"})
    session.commit()
    return WorkflowAccepted(workflow_id=run.workflow_id, run_id=run.temporal_run_id)


@router.get("/platform-accounts", response_model=list[AccountView])
def list_accounts(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db)
) -> list[AccountView]:
    principal.require("account:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    rows = session.execute(
        select(PlatformAccount, PlatformAdapter)
        .join(PlatformAdapter, PlatformAdapter.id == PlatformAccount.adapter_id)
        .where(PlatformAccount.tenant_id == repository.tenant.id)
        .order_by(PlatformAccount.created_at.desc())
    ).all()
    return [account_view(account, adapter, session) for account, adapter in rows]


@router.get("/platform-adapters", response_model=list[AdapterView])
def list_platform_adapters(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db)
) -> list[AdapterView]:
    principal.require("account:read")
    # Global code capabilities; this projection has no tenant account/profile data.
    adapters = session.scalars(select(PlatformAdapter).order_by(PlatformAdapter.slug)).all()
    return [
        AdapterView(
            pub_id=item.pub_id,
            slug=item.slug,
            display_name=item.display_name,
            admission_level=item.admission_level,
            capabilities=json.loads(item.capabilities_json),
            adapter_version=item.adapter_version,
            last_passed_at=item.last_passed_at,
        )
        for item in adapters
    ]


@router.post("/platform-accounts", response_model=AccountView, status_code=201)
def create_account(
    body: AccountCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> AccountView:
    principal.require("account:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    adapter = session.scalar(
        select(PlatformAdapter).where(PlatformAdapter.slug == body.platform_slug)
    )
    if adapter is None:
        adapter = PlatformAdapter(
            pub_id=new_pub_id("pad"),
            slug=body.platform_slug,
            display_name=body.platform_name,
            admission_level="adapter_ready" if body.platform_slug == "fixed" else "catalogued",
            capabilities_json=json.dumps(
                ["read", "query"] if body.platform_slug == "fixed" else []
            ),
            adapter_version="fixed-v1" if body.platform_slug == "fixed" else "unimplemented",
        )
        session.add(adapter)
        session.flush()
    account = PlatformAccount(
        pub_id=new_pub_id("pac"),
        tenant_id=repository.tenant.id,
        adapter_id=adapter.id,
        owner_pub_id=body.owner_pub_id,
        account_mask=body.account_mask,
        purpose=body.purpose,
        responsible_pub_id=body.responsible_pub_id,
        custody_mode=body.custody_mode,
        region=body.region,
        admission_level=adapter.admission_level,
    )
    session.add(account)
    session.commit()
    return account_view(account, adapter, session)


def find_account(session: Session, tenant_id: Any, pub_id: str) -> PlatformAccount:
    account = session.scalar(
        select(PlatformAccount).where(
            PlatformAccount.tenant_id == tenant_id, PlatformAccount.pub_id == pub_id
        )
    )
    if account is None:
        raise HTTPException(status_code=404, detail={"code": "account_not_found"})
    return account


def require_current_authorization(
    session: Session,
    account: PlatformAccount,
    *,
    required_scope: str | None = None,
) -> AccountAuthorization:
    if account.state == "revoked":
        raise HTTPException(status_code=409, detail={"code": "account_revoked"})
    now = datetime.now(UTC)
    authorization = session.scalar(
        select(AccountAuthorization)
        .where(
            AccountAuthorization.account_id == account.id,
            AccountAuthorization.revoked_at.is_(None),
            AccountAuthorization.valid_from <= now,
            AccountAuthorization.valid_until > now,
        )
        .order_by(AccountAuthorization.valid_until.desc())
    )
    if authorization is None:
        raise HTTPException(status_code=403, detail={"code": "authorization_invalid"})
    if required_scope is not None and required_scope not in json.loads(authorization.scopes_json):
        raise HTTPException(status_code=403, detail={"code": "scope_not_authorized"})
    return authorization


@router.post("/platform-accounts/{account_pub_id}/authorizations", status_code=201)
def authorize_account(
    account_pub_id: str,
    body: AuthorizationCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict[str, str]:
    principal.require("account:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    account = find_account(session, repository.tenant.id, account_pub_id)
    if account.state == "revoked":
        raise HTTPException(status_code=409, detail={"code": "account_revoked"})
    if body.valid_until <= body.valid_from:
        raise HTTPException(status_code=422, detail={"code": "invalid_authorization_window"})
    try:
        authorization, _propagation = replace_account_authorization(
            session,
            account=account,
            scopes=set(body.scopes),
            forbidden_actions=set(body.forbidden_actions),
            regions=set(body.regions),
            valid_from=body.valid_from,
            valid_until=body.valid_until,
            pub_id_prefix="atz",
        )
    except ValueError as error:
        if str(error) == "account_revoked":
            raise HTTPException(status_code=409, detail={"code": "account_revoked"}) from error
        raise
    if account.state == "requested":
        account.state = "owner_authorizing"
    session.commit()
    return {"pub_id": authorization.pub_id, "state": "active"}


@router.post(
    "/platform-accounts/{account_pub_id}/profiles/enroll",
    response_model=ProfileView,
    status_code=201,
)
def enroll_profile(
    account_pub_id: str,
    body: ProfileEnroll,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ProfileView:
    principal.require("account:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    account = find_account(session, repository.tenant.id, account_pub_id)
    require_current_authorization(session, account)
    adapter = session.get(PlatformAdapter, account.adapter_id)
    assert adapter is not None
    previous = session.scalar(
        select(BrowserProfile)
        .where(BrowserProfile.account_id == account.id)
        .order_by(BrowserProfile.profile_version.desc())
        .limit(1)
    )
    version = (previous.profile_version if previous else 0) + 1
    ciphertext = nonce = wrapped_dek = None
    ciphertext_sha256 = None
    if body.custody_mode != "customer_device":
        if body.profile_payload is None:
            raise HTTPException(status_code=422, detail={"code": "profile_payload_required"})
        aad = profile_aad(
            principal.tenant_pub_id, account.owner_pub_id, adapter.slug, account.pub_id, version
        )
        sealed = _profile_vault().seal(body.profile_payload.encode(), aad)
        ciphertext, nonce, wrapped_dek = sealed.ciphertext, sealed.nonce, sealed.wrapped_dek
        ciphertext_sha256 = sealed.sha256
    profile = BrowserProfile(
        pub_id=new_pub_id("prf"),
        tenant_id=repository.tenant.id,
        account_id=account.id,
        profile_version=version,
        custody_mode=body.custody_mode,
        state="ACTIVE",
        constraints_json=json.dumps(body.constraints),
        ciphertext=ciphertext,
        nonce=nonce,
        wrapped_dek=wrapped_dek,
        ciphertext_sha256=ciphertext_sha256,
        expires_at=body.expires_at,
    )
    account.state = "active"
    session.add(profile)
    session.commit()
    return ProfileView(
        pub_id=profile.pub_id,
        profile_version=version,
        custody_mode=profile.custody_mode,
        state=profile.state,
        constraints=list(body.constraints),
        ciphertext_sha256=ciphertext_sha256,
        expires_at=profile.expires_at,
    )


@router.post(
    "/platform-accounts/{account_pub_id}/profiles/seal",
    response_model=ProfileView,
    status_code=201,
)
def seal_profile_version(
    account_pub_id: str,
    body: ProfileSeal,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ProfileView:
    principal.require("profile:use")
    repository = TenantRepository(session, principal.tenant_pub_id)
    account = find_account(session, repository.tenant.id, account_pub_id)
    adapter = session.get(PlatformAdapter, account.adapter_id)
    assert adapter is not None
    current = session.scalar(
        select(BrowserProfile)
        .where(BrowserProfile.account_id == account.id)
        .order_by(BrowserProfile.profile_version.desc())
        .limit(1)
        .with_for_update()
    )
    if current is None or current.profile_version != body.expected_profile_version:
        raise HTTPException(status_code=409, detail={"code": "profile_version_conflict"})
    lease = session.scalar(
        select(SessionLease).where(
            SessionLease.tenant_id == repository.tenant.id,
            SessionLease.account_id == account.id,
            SessionLease.pub_id == body.lease_pub_id,
        )
    )
    if lease is None:
        raise HTTPException(status_code=404, detail={"code": "lease_not_found"})
    if lease.profile_id != current.id:
        raise HTTPException(status_code=409, detail={"code": "profile_lease_mismatch"})
    require_current_authorization(session, account, required_scope=lease.capability)
    try:
        assert_fenced_write(lease, body.fencing_token)
    except FenceViolationError as exc:
        raise HTTPException(status_code=409, detail={"code": "fence_violation"}) from exc
    next_version = current.profile_version + 1
    aad = profile_aad(
        principal.tenant_pub_id,
        account.owner_pub_id,
        adapter.slug,
        account.pub_id,
        next_version,
    )
    sealed = _profile_vault().seal(body.profile_payload.encode(), aad)
    next_profile = BrowserProfile(
        pub_id=new_pub_id("prf"),
        tenant_id=repository.tenant.id,
        account_id=account.id,
        profile_version=next_version,
        custody_mode=current.custody_mode,
        state="ACTIVE",
        constraints_json=current.constraints_json,
        ciphertext=sealed.ciphertext,
        nonce=sealed.nonce,
        wrapped_dek=sealed.wrapped_dek,
        ciphertext_sha256=sealed.sha256,
        expires_at=body.expires_at,
    )
    current.state = "SUPERSEDED"
    lease.released_at = datetime.now(UTC)
    session.add(next_profile)
    session.add(
        SessionEvent(
            pub_id=new_pub_id("sev"),
            tenant_id=repository.tenant.id,
            account_id=account.id,
            event_type="profile.version_sealed",
            summary_json=json.dumps(
                {"profile_version": next_version, "fencing_token": lease.fencing_token}
            ),
        )
    )
    session.commit()
    return ProfileView(
        pub_id=next_profile.pub_id,
        profile_version=next_version,
        custody_mode=next_profile.custody_mode,
        state=next_profile.state,
        constraints=json.loads(next_profile.constraints_json),
        ciphertext_sha256=next_profile.ciphertext_sha256,
        expires_at=next_profile.expires_at,
    )


@router.post(
    "/platform-accounts/{account_pub_id}/profiles/rekey",
    response_model=ProfileView,
    status_code=201,
)
def rekey_profile_version(
    account_pub_id: str,
    body: ProfileRekey,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=16, max_length=128),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> ProfileView:
    """Rotate a server-custodied profile DEK without accepting or returning profile plaintext."""

    principal.require("profile:use")
    repository = TenantRepository(session, principal.tenant_pub_id)
    account = find_account(session, repository.tenant.id, account_pub_id)
    adapter = session.get(PlatformAdapter, account.adapter_id)
    assert adapter is not None
    action = (
        "profile.rekeyed:"
        + hashlib.sha256(
            f"{principal.tenant_pub_id}:{account.pub_id}:{idempotency_key}".encode()
        ).hexdigest()
    )
    payload_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    session.execute(select(func.pg_advisory_xact_lock(func.hashtext(action))))
    prior = session.scalar(
        select(AuditLog).where(
            AuditLog.tenant_id == repository.tenant.id,
            AuditLog.action == action,
        )
    )
    if prior is not None:
        receipt = json.loads(prior.receipt)
        if receipt.get("payload_hash") != payload_hash:
            raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"})
        replay = session.scalar(
            select(BrowserProfile).where(
                BrowserProfile.tenant_id == repository.tenant.id,
                BrowserProfile.pub_id == prior.resource_pub_id,
            )
        )
        if replay is None:
            raise HTTPException(status_code=409, detail={"code": "idempotency_receipt_invalid"})
        return ProfileView(
            pub_id=replay.pub_id,
            profile_version=replay.profile_version,
            custody_mode=replay.custody_mode,
            state=replay.state,
            constraints=json.loads(replay.constraints_json),
            ciphertext_sha256=replay.ciphertext_sha256,
            expires_at=replay.expires_at,
        )
    current = session.scalar(
        select(BrowserProfile)
        .where(BrowserProfile.account_id == account.id)
        .order_by(BrowserProfile.profile_version.desc())
        .limit(1)
        .with_for_update()
    )
    if current is None or current.profile_version != body.expected_profile_version:
        raise HTTPException(status_code=409, detail={"code": "profile_version_conflict"})
    if (
        current.custody_mode == "customer_device"
        or current.ciphertext is None
        or current.nonce is None
        or current.wrapped_dek is None
        or current.ciphertext_sha256 is None
    ):
        raise HTTPException(status_code=409, detail={"code": "profile_rekey_not_applicable"})
    lease = session.scalar(
        select(SessionLease).where(
            SessionLease.tenant_id == repository.tenant.id,
            SessionLease.account_id == account.id,
            SessionLease.pub_id == body.lease_pub_id,
        )
    )
    if lease is None:
        raise HTTPException(status_code=404, detail={"code": "lease_not_found"})
    if lease.profile_id != current.id:
        raise HTTPException(status_code=409, detail={"code": "profile_lease_mismatch"})
    require_current_authorization(session, account, required_scope=lease.capability)
    try:
        assert_fenced_write(lease, body.fencing_token)
    except FenceViolationError as exc:
        raise HTTPException(status_code=409, detail={"code": "fence_violation"}) from exc
    next_version = current.profile_version + 1
    old_aad = profile_aad(
        principal.tenant_pub_id,
        account.owner_pub_id,
        adapter.slug,
        account.pub_id,
        current.profile_version,
    )
    new_aad = profile_aad(
        principal.tenant_pub_id,
        account.owner_pub_id,
        adapter.slug,
        account.pub_id,
        next_version,
    )
    sealed = _profile_vault().rotate_dek(
        SealedProfile(
            ciphertext=current.ciphertext,
            nonce=current.nonce,
            wrapped_dek=current.wrapped_dek,
            sha256=current.ciphertext_sha256,
        ),
        old_aad,
        new_aad,
    )
    next_profile = BrowserProfile(
        pub_id=new_pub_id("prf"),
        tenant_id=repository.tenant.id,
        account_id=account.id,
        profile_version=next_version,
        custody_mode=current.custody_mode,
        state="ACTIVE",
        constraints_json=current.constraints_json,
        ciphertext=sealed.ciphertext,
        nonce=sealed.nonce,
        wrapped_dek=sealed.wrapped_dek,
        ciphertext_sha256=sealed.sha256,
        expires_at=current.expires_at,
    )
    current.state = "SUPERSEDED"
    lease.released_at = datetime.now(UTC)
    session.add(next_profile)
    session.flush()
    session.add_all(
        [
            SessionEvent(
                pub_id=new_pub_id("sev"),
                tenant_id=repository.tenant.id,
                account_id=account.id,
                event_type="profile.dek_rekeyed",
                summary_json=json.dumps(
                    {
                        "from_profile_version": current.profile_version,
                        "to_profile_version": next_version,
                        "fencing_token": lease.fencing_token,
                        "reason": body.reason,
                    }
                ),
            ),
            AuditLog(
                pub_id=new_pub_id("aud"),
                tenant_id=repository.tenant.id,
                actor_pub_id=principal.actor_pub_id,
                action=action,
                resource_type="browser_profile",
                resource_pub_id=next_profile.pub_id,
                receipt=json.dumps({"payload_hash": payload_hash}),
            ),
        ]
    )
    session.commit()
    return ProfileView(
        pub_id=next_profile.pub_id,
        profile_version=next_version,
        custody_mode=next_profile.custody_mode,
        state=next_profile.state,
        constraints=json.loads(next_profile.constraints_json),
        ciphertext_sha256=next_profile.ciphertext_sha256,
        expires_at=next_profile.expires_at,
    )


@router.post("/platform-accounts/{account_pub_id}/health-checks", status_code=202)
def health_check(
    account_pub_id: str,
    live_canary: bool = False,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    principal.require("account:operate")
    if live_canary and principal.role.value not in {"admin", "reviewer"}:
        raise HTTPException(status_code=403, detail={"code": "live_canary_confirmation_required"})
    repository = TenantRepository(session, principal.tenant_pub_id)
    account = find_account(session, repository.tenant.id, account_pub_id)
    require_current_authorization(session, account)
    adapter = session.get(PlatformAdapter, account.adapter_id)
    assert adapter is not None
    profile = session.scalar(
        select(BrowserProfile)
        .where(BrowserProfile.account_id == account.id)
        .order_by(BrowserProfile.profile_version.desc())
    )
    l0 = "not_applicable_customer_device"
    if profile and profile.ciphertext is not None:
        try:
            aad = profile_aad(
                principal.tenant_pub_id,
                account.owner_pub_id,
                adapter.slug,
                account.pub_id,
                profile.profile_version,
            )
            _profile_vault().open(
                SealedProfile(
                    ciphertext=profile.ciphertext,
                    nonce=profile.nonce or b"",
                    wrapped_dek=profile.wrapped_dek or b"",
                    sha256=profile.ciphertext_sha256 or "",
                ),
                aad,
            )
            l0 = "passed"
        except Exception:
            l0 = "failed_quarantined"
            account.state = "quarantined"
            profile.state = "QUARANTINED"
    levels = {
        "L0": l0,
        # Network/account/capability probes require the real adapter and an
        # explicitly authorized live canary. Never promote a fixture to a
        # health result.
        "L1": "not_run",
        "L2": "not_run",
        "L3": "not_run",
        "L4": "adapter_not_live" if live_canary else "not_run",
    }
    event = SessionEvent(
        pub_id=new_pub_id("sev"),
        tenant_id=repository.tenant.id,
        account_id=account.id,
        event_type="health_check.completed",
        summary_json=json.dumps({"levels": levels, "live": False, "canary_requested": live_canary}),
    )
    session.add_all(
        [
            event,
            SessionHealthCheck(
                pub_id=new_pub_id("shc"),
                tenant_id=repository.tenant.id,
                account_id=account.id,
                probe_levels_json=json.dumps(levels),
                result=(
                    "failed"
                    if l0 == "failed_quarantined"
                    else ("partial" if l0 == "passed" else "not_verified")
                ),
                live_canary=live_canary,
                checked_by=principal.actor_pub_id,
            ),
        ]
    )
    session.commit()
    return {"account_pub_id": account.pub_id, "levels": levels, "live_verified": False}


@router.post(
    "/platform-accounts/{account_pub_id}/interventions",
    response_model=InterventionView,
    status_code=201,
)
def create_intervention(
    account_pub_id: str,
    body: InterventionCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> InterventionView:
    principal.require("intervention:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    account = find_account(session, repository.tenant.id, account_pub_id)
    now = datetime.now(UTC)
    authorization = session.scalar(
        select(AccountAuthorization)
        .where(
            AccountAuthorization.account_id == account.id,
            AccountAuthorization.revoked_at.is_(None),
            AccountAuthorization.valid_from <= now,
            AccountAuthorization.valid_until > now,
        )
        .order_by(AccountAuthorization.valid_until.desc())
    )
    if account.state == "revoked":
        raise HTTPException(status_code=409, detail={"code": "account_revoked"})
    if authorization is None or body.action not in json.loads(authorization.scopes_json):
        raise HTTPException(status_code=403, detail={"code": "scope_not_authorized"})
    if body.action in json.loads(authorization.forbidden_actions_json):
        raise HTTPException(status_code=403, detail={"code": "action_forbidden"})
    run_id = None
    if body.run_pub_id:
        run = session.scalar(
            select(CollectionRun).where(
                CollectionRun.tenant_id == repository.tenant.id,
                CollectionRun.pub_id == body.run_pub_id,
            )
        )
        if run is None:
            raise HTTPException(status_code=404, detail={"code": "run_not_found"})
        run_id = run.id
    intervention_sla_minutes = session.execute(
        text(
            """
            SELECT intervention_sla_minutes
            FROM platform.account_sla_policy
            WHERE tenant_id=:tenant_id AND adapter_id=:adapter_id
            """
        ),
        {"tenant_id": repository.tenant.id, "adapter_id": account.adapter_id},
    ).scalar_one_or_none()
    due_at = now + timedelta(minutes=int(intervention_sla_minutes or 30))
    request = InterventionRequest(
        pub_id=new_pub_id("int"),
        tenant_id=repository.tenant.id,
        account_id=account.id,
        run_id=run_id,
        challenge_type=body.challenge_type,
        allowed_domain=body.allowed_domain,
        action=body.action,
        state="pending",
        assigned_to_pub_id=account.responsible_pub_id,
        due_at=due_at,
    )
    account.state = "challenge_required"
    session.add(request)
    session.commit()
    return InterventionView(
        pub_id=request.pub_id,
        account_pub_id=account.pub_id,
        account_mask=account.account_mask,
        challenge_type=request.challenge_type,
        allowed_domain=request.allowed_domain,
        action=request.action,
        state=request.state,
        pairing_expires_at=None,
        platform_result=None,
        assigned_to_pub_id=request.assigned_to_pub_id,
        due_at=request.due_at,
        resolution_note=request.resolution_note,
    )


@router.get("/interventions", response_model=list[InterventionView])
def list_interventions(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db)
) -> list[InterventionView]:
    principal.require("intervention:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    rows = session.execute(
        select(InterventionRequest, PlatformAccount)
        .join(PlatformAccount, PlatformAccount.id == InterventionRequest.account_id)
        .where(InterventionRequest.tenant_id == repository.tenant.id)
        .order_by(InterventionRequest.created_at.desc())
    ).all()
    return [
        InterventionView(
            pub_id=item.pub_id,
            account_pub_id=account.pub_id,
            account_mask=account.account_mask,
            challenge_type=item.challenge_type,
            allowed_domain=item.allowed_domain,
            action=item.action,
            state=item.state,
            pairing_expires_at=item.pairing_expires_at,
            platform_result=item.platform_result,
            assigned_to_pub_id=item.assigned_to_pub_id,
            due_at=item.due_at,
            resolution_note=item.resolution_note,
        )
        for item, account in rows
    ]


@router.patch(
    "/interventions/{intervention_pub_id}/assignment",
    response_model=InterventionView,
)
def assign_intervention(
    intervention_pub_id: str,
    body: InterventionAssignment,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> InterventionView:
    principal.require("intervention:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    row = session.execute(
        select(InterventionRequest, PlatformAccount)
        .join(PlatformAccount, PlatformAccount.id == InterventionRequest.account_id)
        .where(
            InterventionRequest.tenant_id == repository.tenant.id,
            InterventionRequest.pub_id == intervention_pub_id,
        )
        .with_for_update()
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "intervention_not_found"})
    item, account = row
    if item.state not in {"pending", "paired", "task_issued", "awaiting_platform_probe"}:
        raise HTTPException(status_code=409, detail={"code": "intervention_terminal"})
    if body.due_at <= datetime.now(UTC):
        raise HTTPException(status_code=422, detail={"code": "intervention_due_at_past"})
    assignee_exists = session.execute(
        text(
            """
            SELECT 1
            FROM platform.membership membership
            JOIN platform.app_user app_user ON app_user.id=membership.user_id
            WHERE membership.tenant_id=:tenant_id AND app_user.pub_id=:user_pub_id
              AND membership.state='active' AND membership.revoked_at IS NULL
              AND app_user.disabled_at IS NULL
            """
        ),
        {
            "tenant_id": repository.tenant.id,
            "user_pub_id": body.assigned_to_pub_id,
        },
    ).first()
    if assignee_exists is None:
        raise HTTPException(status_code=404, detail={"code": "assignee_not_found"})
    item.assigned_to_pub_id = body.assigned_to_pub_id
    item.due_at = body.due_at
    session.add(
        AuditLog(
            pub_id=new_pub_id("aud"),
            tenant_id=repository.tenant.id,
            actor_pub_id=principal.actor_pub_id,
            action="intervention.assigned",
            resource_type="intervention_request",
            resource_pub_id=item.pub_id,
            receipt=json.dumps(
                {
                    "assigned_to_pub_id": item.assigned_to_pub_id,
                    "due_at": item.due_at.isoformat(),
                }
            ),
        )
    )
    session.commit()
    return InterventionView(
        pub_id=item.pub_id,
        account_pub_id=account.pub_id,
        account_mask=account.account_mask,
        challenge_type=item.challenge_type,
        allowed_domain=item.allowed_domain,
        action=item.action,
        state=item.state,
        pairing_expires_at=item.pairing_expires_at,
        platform_result=item.platform_result,
        assigned_to_pub_id=item.assigned_to_pub_id,
        due_at=item.due_at,
        resolution_note=item.resolution_note,
    )


@router.post(
    "/interventions/{intervention_pub_id}/cancel",
    response_model=InterventionView,
)
def cancel_intervention(
    intervention_pub_id: str,
    body: InterventionResolution,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> InterventionView:
    principal.require("intervention:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    row = session.execute(
        select(InterventionRequest, PlatformAccount)
        .join(PlatformAccount, PlatformAccount.id == InterventionRequest.account_id)
        .where(
            InterventionRequest.tenant_id == repository.tenant.id,
            InterventionRequest.pub_id == intervention_pub_id,
        )
        .with_for_update()
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "intervention_not_found"})
    item, account = row
    if item.state == "cancelled":
        return InterventionView(
            pub_id=item.pub_id,
            account_pub_id=account.pub_id,
            account_mask=account.account_mask,
            challenge_type=item.challenge_type,
            allowed_domain=item.allowed_domain,
            action=item.action,
            state=item.state,
            pairing_expires_at=item.pairing_expires_at,
            platform_result=item.platform_result,
            assigned_to_pub_id=item.assigned_to_pub_id,
            due_at=item.due_at,
            resolution_note=item.resolution_note,
        )
    if item.state in {"completed", "failed", "expired", "rejected"}:
        raise HTTPException(status_code=409, detail={"code": "intervention_terminal"})
    item.state = "cancelled"
    item.platform_result = "rejected"
    item.resolution_note = body.reason
    item.pairing_token_hash = None
    item.completed_at = datetime.now(UTC)
    session.add(
        SessionEvent(
            pub_id=new_pub_id("sev"),
            tenant_id=repository.tenant.id,
            account_id=account.id,
            event_type="intervention.cancelled",
            summary_json=json.dumps({"intervention_pub_id": item.pub_id}),
        )
    )
    session.commit()
    return InterventionView(
        pub_id=item.pub_id,
        account_pub_id=account.pub_id,
        account_mask=account.account_mask,
        challenge_type=item.challenge_type,
        allowed_domain=item.allowed_domain,
        action=item.action,
        state=item.state,
        pairing_expires_at=item.pairing_expires_at,
        platform_result=item.platform_result,
        assigned_to_pub_id=item.assigned_to_pub_id,
        due_at=item.due_at,
        resolution_note=item.resolution_note,
    )


@router.post("/interventions/{intervention_pub_id}/pair", response_model=PairingView)
def pair_intervention(
    intervention_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> PairingView:
    principal.require("intervention:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    row = session.execute(
        select(InterventionRequest, PlatformAccount)
        .join(PlatformAccount, PlatformAccount.id == InterventionRequest.account_id)
        .where(
            InterventionRequest.tenant_id == repository.tenant.id,
            InterventionRequest.pub_id == intervention_pub_id,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "intervention_not_found"})
    item, account = row
    if item.state != "pending" or account.state == "revoked":
        raise HTTPException(status_code=409, detail={"code": "intervention_not_pairable"})
    try:
        signing_public_key = public_key_bytes(task_signing_key(settings).public_key())
    except (OSError, UnicodeError, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail={"code": "terminal_signing_unavailable"}
        ) from exc
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(minutes=10)
    item.pairing_token_hash = hashlib.sha256(token.encode()).hexdigest()
    item.pairing_expires_at = expires
    item.paired_at = datetime.now(UTC)
    item.state = "paired"
    session.commit()
    return PairingView(
        intervention_pub_id=item.pub_id,
        pairing_token=token,
        server_public_key_sha256=fingerprint(signing_public_key),
        allowed_domain=item.allowed_domain,
        action=item.action,
        challenge_type=item.challenge_type,
        expires_at=expires,
    )


@router.post("/interventions/{intervention_pub_id}/complete", response_model=InterventionView)
async def complete_intervention(
    intervention_pub_id: str,
    body: CompleteIntervention,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> InterventionView:
    principal.require("intervention:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    row = session.execute(
        select(InterventionRequest, PlatformAccount)
        .join(PlatformAccount, PlatformAccount.id == InterventionRequest.account_id)
        .where(
            InterventionRequest.tenant_id == repository.tenant.id,
            InterventionRequest.pub_id == intervention_pub_id,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "intervention_not_found"})
    item, account = row
    if account.custody_mode == "customer_device":
        raise HTTPException(status_code=409, detail={"code": "terminal_proof_required"})
    if (
        item.pairing_token_hash != hashlib.sha256(body.pairing_token.encode()).hexdigest()
        or item.pairing_expires_at is None
        or item.pairing_expires_at < datetime.now(UTC)
    ):
        raise HTTPException(status_code=410, detail={"code": "pairing_token_invalid"})
    now = datetime.now(UTC)
    authorization = session.scalar(
        select(AccountAuthorization)
        .where(
            AccountAuthorization.account_id == account.id,
            AccountAuthorization.revoked_at.is_(None),
            AccountAuthorization.valid_from <= now,
            AccountAuthorization.valid_until > now,
        )
        .order_by(AccountAuthorization.valid_until.desc())
    )
    if (
        account.state == "revoked"
        or authorization is None
        or item.action not in json.loads(authorization.scopes_json)
        or item.action in json.loads(authorization.forbidden_actions_json)
    ):
        raise HTTPException(status_code=410, detail={"code": "authorization_invalid"})
    item.pairing_token_hash = None
    item.state = "completed" if body.platform_result == "verified" else body.platform_result
    item.platform_result = body.platform_result
    item.evidence_hash = body.evidence_hash
    item.completed_at = datetime.now(UTC)
    account.state = "active" if body.platform_result == "verified" else "challenge_required"
    session.add(
        SessionEvent(
            pub_id=new_pub_id("sev"),
            tenant_id=repository.tenant.id,
            account_id=account.id,
            event_type="intervention.completed",
            summary_json=json.dumps(
                {"challenge_type": item.challenge_type, "platform_result": body.platform_result}
            ),
        )
    )
    if item.run_id is not None and body.platform_result == "verified":
        run = session.get(CollectionRun, item.run_id)
        if run is not None:
            enqueue_workflow_signal(
                session,
                tenant_pub_id=principal.tenant_pub_id,
                workflow_id=run.workflow_id,
                signal_name="complete_intervention",
                args=[body.evidence_hash],
                idempotency_key=(f"intervention:{item.pub_id}:{body.evidence_hash}"),
            )
    session.commit()
    return InterventionView(
        pub_id=item.pub_id,
        account_pub_id=account.pub_id,
        account_mask=account.account_mask,
        challenge_type=item.challenge_type,
        allowed_domain=item.allowed_domain,
        action=item.action,
        state=item.state,
        pairing_expires_at=item.pairing_expires_at,
        platform_result=item.platform_result,
        assigned_to_pub_id=item.assigned_to_pub_id,
        due_at=item.due_at,
        resolution_note=item.resolution_note,
    )


@router.post(
    "/interventions/{intervention_pub_id}/attest",
    response_model=InterventionView,
)
async def attest_customer_terminal_intervention(
    intervention_pub_id: str,
    body: PlatformAttestation,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> InterventionView:
    """Record the trusted platform callback/probe after a terminal finishes its native UI."""
    principal.require("intervention:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    row = session.execute(
        select(InterventionRequest, PlatformAccount, TerminalTask)
        .join(PlatformAccount, PlatformAccount.id == InterventionRequest.account_id)
        .join(TerminalTask, TerminalTask.intervention_id == InterventionRequest.id)
        .where(
            InterventionRequest.tenant_id == repository.tenant.id,
            InterventionRequest.pub_id == intervention_pub_id,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "intervention_not_found"})
    item, account, task = row
    if (
        account.custody_mode != "customer_device"
        or account.state == "revoked"
        or item.state != "awaiting_platform_probe"
        or task.state != "completed"
        or task.result != "challenge_completed"
    ):
        raise HTTPException(status_code=409, detail={"code": "platform_attestation_not_allowed"})
    now = datetime.now(UTC)
    item.state = "completed" if body.platform_result == "verified" else body.platform_result
    item.platform_result = body.platform_result
    item.evidence_hash = body.evidence_hash
    item.completed_at = now
    account.state = "active" if body.platform_result == "verified" else "challenge_required"
    session.add(
        SessionEvent(
            pub_id=new_pub_id("sev"),
            tenant_id=repository.tenant.id,
            account_id=account.id,
            event_type="customer_terminal.platform_attested",
            summary_json=json.dumps(
                {
                    "intervention_pub_id": item.pub_id,
                    "platform_result": body.platform_result,
                    "proof_source": body.proof_source,
                },
                sort_keys=True,
            ),
        )
    )
    if item.run_id is not None and body.platform_result == "verified":
        run = session.get(CollectionRun, item.run_id)
        if run is not None:
            enqueue_workflow_signal(
                session,
                tenant_pub_id=principal.tenant_pub_id,
                workflow_id=run.workflow_id,
                signal_name="complete_intervention",
                args=[body.evidence_hash],
                idempotency_key=(f"intervention:{item.pub_id}:{body.evidence_hash}"),
            )
    session.commit()
    return InterventionView(
        pub_id=item.pub_id,
        account_pub_id=account.pub_id,
        account_mask=account.account_mask,
        challenge_type=item.challenge_type,
        allowed_domain=item.allowed_domain,
        action=item.action,
        state=item.state,
        pairing_expires_at=item.pairing_expires_at,
        platform_result=item.platform_result,
    )


@router.post(
    "/platform-accounts/{account_pub_id}/revoke",
    response_model=WorkflowAccepted,
    status_code=202,
)
async def revoke_account(
    account_pub_id: str,
    reason: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> WorkflowAccepted:
    principal.require("account:operate")
    repository = TenantRepository(session, principal.tenant_pub_id)
    account = find_account(session, repository.tenant.id, account_pub_id)
    existing = session.scalar(
        select(RevocationRequest)
        .where(RevocationRequest.account_id == account.id)
        .order_by(RevocationRequest.created_at.desc())
    )
    if existing is not None:
        return WorkflowAccepted(workflow_id=existing.workflow_id)
    workflow_id = f"account-revocation/{principal.tenant_pub_id}/{account.pub_id}"
    request, profile_versions = stage_account_revocation(
        session,
        account=account,
        reason=reason,
        workflow_id=workflow_id,
    )
    try:
        enqueue_workflow_start(
            session,
            tenant_pub_id=principal.tenant_pub_id,
            workflow_type="account_revocation",
            workflow_id=workflow_id,
            task_queue=settings.temporal_task_queue,
            payload={
                "tenant_pub_id": principal.tenant_pub_id,
                "account_pub_id": account.pub_id,
                "profile_versions": profile_versions,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return WorkflowAccepted(workflow_id=workflow_id)


@router.get("/platform-accounts/{account_pub_id}/events", response_model=list[EventView])
def account_events(
    account_pub_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[EventView]:
    principal.require("account:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    account = find_account(session, repository.tenant.id, account_pub_id)
    rows = session.scalars(
        select(SessionEvent)
        .where(SessionEvent.account_id == account.id)
        .order_by(SessionEvent.occurred_at.desc())
    ).all()
    return [
        EventView(
            pub_id=item.pub_id,
            event_type=item.event_type,
            summary=json.loads(item.summary_json),
            occurred_at=item.occurred_at,
        )
        for item in rows
    ]
