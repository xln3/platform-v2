# ruff: noqa: B008

"""Tenant-scoped, read-only Operations project/business portfolio projection."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..customer_services.router import SERVICE_CATALOG
from ..identity.policy import Principal, get_principal
from ..tenancy.database import get_db
from ..tenancy.repository import TenantRepository
from .operations_constants import RUN_DELAY_THRESHOLD, TERMINAL_RUN_STATES

router = APIRouter()

PUBLIC_ID_PATTERN = r"^[a-z]{3}_[A-Za-z0-9_-]{1,116}$"
CURSOR_ID_PATTERN = re.compile(r"^prj_[A-Za-z0-9_-]{1,116}$")
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SERVICE_NAMES = {code: name for _number, code, name in SERVICE_CATALOG}
FAILED_RUN_STATES = frozenset({"failed", "completed_with_failures"})


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectState(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class AttentionCode(StrEnum):
    COLLECTION_FAILED_OR_DELAYED = "collection_failed_or_delayed"
    FORMAL_PRODUCTION_FAILED = "formal_production_failed"
    FORMAL_REVIEW_REQUIRED = "formal_review_required"
    DELIVERY_CONFIRMATION_REQUIRED = "delivery_confirmation_required"
    SETUP_RECORDS_MISSING = "setup_records_missing"
    INTAKE_TRUTH_CONFIRMATION_REQUIRED = "intake_truth_confirmation_required"
    SERVICE_ENTITLEMENT_UNRECORDED = "service_entitlement_unrecorded"
    NO_CURRENT_ATTENTION = "no_current_attention"


class AttentionSeverity(StrEnum):
    DANGER = "danger"
    WARNING = "warning"
    NEUTRAL = "neutral"


class EntitlementState(StrEnum):
    INACTIVE = "inactive"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    EXPIRED = "expired"


class CollectionRunState(StrEnum):
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    RESUMING = "resuming"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class FormalProductionState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    FAILED = "failed"
    AWAITING_REVIEW = "awaiting_review"
    SIGNED = "signed"


class ProjectIdentityView(StrictModel):
    id: str = Field(pattern=PUBLIC_ID_PATTERN, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    state: ProjectState


class CustomerIdentityView(StrictModel):
    id: str = Field(pattern=PUBLIC_ID_PATTERN, max_length=120)
    name: str = Field(min_length=1, max_length=200)


class SetupView(StrictModel):
    client_profile_revision: int | None = Field(default=None, ge=1)
    asset_confirmation_revision: int | None = Field(default=None, ge=1)
    frozen_monitoring_config_revision: int | None = Field(default=None, ge=1)
    setup_ready: bool
    intake_profile_exists: bool
    intake_truth_confirmed: bool | None


class ServiceEntitlementView(StrictModel):
    service_code: str = Field(min_length=1, max_length=48)
    service_name: str = Field(min_length=1, max_length=120)
    state: EntitlementState
    authorized_from: AwareDatetime | None
    authorized_until: AwareDatetime | None
    effective_now: bool


class CollectionSummaryView(StrictModel):
    active_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    delayed_count: int = Field(ge=0)
    latest_state: CollectionRunState | None
    latest_at: AwareDatetime | None


class FormalReportSummaryView(StrictModel):
    production_count: int = Field(ge=0)
    latest_state: FormalProductionState | None
    latest_at: AwareDatetime | None


class DeliverySummaryView(StrictModel):
    delivered_at: AwareDatetime | None
    confirmed_at: AwareDatetime | None
    pending_confirmation_count: int = Field(ge=0)


class PrimaryAttentionView(StrictModel):
    code: AttentionCode
    severity: AttentionSeverity
    additional_count: int = Field(ge=0, le=6)


class BusinessOverviewItem(StrictModel):
    project: ProjectIdentityView
    customer: CustomerIdentityView
    setup: SetupView
    service_entitlements: list[ServiceEntitlementView] = Field(max_length=5)
    collection: CollectionSummaryView
    formal_report: FormalReportSummaryView
    delivery: DeliverySummaryView
    contract_draft_export: None = None
    primary_attention: PrimaryAttentionView
    last_business_fact_at: AwareDatetime


class BusinessOverviewSummary(StrictModel):
    scope: Literal["filtered"]
    tenant_project_count: int = Field(ge=0)
    project_count: int = Field(ge=0)
    project_state_counts: dict[ProjectState, int]
    setup_ready_project_count: int = Field(ge=0)
    project_with_entitlement_record_count: int = Field(ge=0)
    active_entitlement_count: int = Field(ge=0)
    attention_project_count: int = Field(ge=0)


class CommercialCapabilities(StrictModel):
    quotation_history: Literal["unsupported"] = "unsupported"
    signed_contract_ledger: Literal["unsupported"] = "unsupported"
    invoice_receivable_payment_ledger: Literal["unsupported"] = "unsupported"


class BusinessOverviewPage(StrictModel):
    limit: int = Field(ge=1, le=20)
    next_cursor: str | None = Field(default=None, max_length=512)
    has_more: bool
    filtered_total: int = Field(ge=0)


class BusinessOverviewView(StrictModel):
    schema_version: Literal[1] = 1
    as_of: AwareDatetime
    summary: BusinessOverviewSummary
    commercial_capabilities: CommercialCapabilities
    items: list[BusinessOverviewItem] = Field(max_length=20)
    page: BusinessOverviewPage


class CursorPayload(StrictModel):
    version: Literal[1]
    last_business_fact_at: AwareDatetime
    project_pub_id: str = Field(pattern=r"^prj_[A-Za-z0-9_-]{1,116}$", max_length=120)
    filter_hash: str = Field(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)


def _filter_hash(
    q: str | None,
    project_state: ProjectState | None,
    attention: AttentionCode | None,
) -> str:
    canonical = json.dumps(
        {
            "attention": attention.value if attention else None,
            "project_state": project_state.value if project_state else None,
            "q": q,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _decode_cursor(value: str, expected_filter_hash: str) -> CursorPayload:
    if len(value) > 512 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise HTTPException(status_code=400, detail={"code": "invalid_cursor"})
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(f"{value}{padding}")
        if len(decoded) > 512:
            raise ValueError("cursor payload too large")
        payload = CursorPayload.model_validate(json.loads(decoded))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_cursor"}) from exc
    if not CURSOR_ID_PATTERN.fullmatch(payload.project_pub_id) or not HASH_PATTERN.fullmatch(
        payload.filter_hash
    ):
        raise HTTPException(status_code=400, detail={"code": "invalid_cursor"})
    if payload.filter_hash != expected_filter_hash:
        raise HTTPException(status_code=400, detail={"code": "cursor_filter_mismatch"})
    return payload


def _encode_cursor(item: BusinessOverviewItem, filter_hash: str) -> str:
    payload = CursorPayload(
        version=1,
        last_business_fact_at=item.last_business_fact_at,
        project_pub_id=item.project.id,
        filter_hash=filter_hash,
    )
    raw = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _escape_like(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


BUSINESS_OVERVIEW_SQL = """
WITH request_clock AS (
  SELECT CAST(:as_of AS timestamptz) AS as_of
), project_base AS (
  SELECT project.id AS project_id, project.pub_id AS project_pub_id,
         project.name AS project_name, project.state AS project_state,
         project.updated_at AS project_updated_at,
         customer.pub_id AS customer_pub_id, customer.name AS customer_name
  FROM platform.project project
  JOIN platform.customer customer
    ON customer.id=project.customer_id
   AND customer.tenant_id=:tenant_id
  WHERE project.tenant_id=:tenant_id
), latest_profile AS (
  SELECT DISTINCT ON (profile.project_id)
         profile.project_id,profile.revision,profile.updated_at
  FROM platform.client_profile_version profile
  WHERE profile.tenant_id=:tenant_id
  ORDER BY profile.project_id,profile.revision DESC,profile.pub_id DESC
), latest_assets AS (
  SELECT DISTINCT ON (assets.project_id)
         assets.project_id,assets.revision,assets.updated_at
  FROM platform.asset_confirmation_version assets
  WHERE assets.tenant_id=:tenant_id
  ORDER BY assets.project_id,assets.revision DESC,assets.pub_id DESC
), latest_frozen_config AS (
  SELECT DISTINCT ON (config.project_id)
         config.project_id,version.revision,
         GREATEST(config.updated_at,version.updated_at) AS updated_at
  FROM platform.monitoring_config config
  JOIN platform.monitoring_config_version version
    ON version.config_id=config.id
   AND version.tenant_id=:tenant_id
  WHERE config.tenant_id=:tenant_id AND version.frozen_at IS NOT NULL
  ORDER BY config.project_id,version.revision DESC,version.pub_id DESC
), intake_facts AS (
  SELECT intake.project_id,true AS profile_exists,intake.truth_confirmed,intake.updated_at
  FROM platform.intake_profile intake
  WHERE intake.tenant_id=:tenant_id
), latest_entitlement AS (
  SELECT DISTINCT ON (entitlement.project_id,entitlement.service_code)
         entitlement.project_id,entitlement.service_code,entitlement.state,
         entitlement.authorized_from,entitlement.authorized_until,entitlement.updated_at
  FROM platform.project_service_entitlement entitlement
  WHERE entitlement.tenant_id=:tenant_id
  ORDER BY entitlement.project_id,entitlement.service_code,
           entitlement.updated_at DESC,entitlement.pub_id DESC
), entitlement_facts AS (
  SELECT entitlement.project_id,
         jsonb_agg(
           jsonb_build_object(
             'service_code',entitlement.service_code,
             'state',entitlement.state,
             'authorized_from',entitlement.authorized_from,
             'authorized_until',entitlement.authorized_until,
             'effective_now',entitlement.state='active'
               AND (entitlement.authorized_from IS NULL OR entitlement.authorized_from<=clock.as_of)
               AND (
                 entitlement.authorized_until IS NULL
                 OR entitlement.authorized_until>clock.as_of
               )
           ) ORDER BY entitlement.service_code
         ) AS entitlements,
         count(*)::integer AS entitlement_record_count,
         count(*) FILTER (
           WHERE entitlement.state='active'
             AND (entitlement.authorized_from IS NULL OR entitlement.authorized_from<=clock.as_of)
             AND (entitlement.authorized_until IS NULL OR entitlement.authorized_until>clock.as_of)
         )::integer AS active_entitlement_count,
         max(entitlement.updated_at) AS updated_at
  FROM latest_entitlement entitlement CROSS JOIN request_clock clock
  GROUP BY entitlement.project_id
), run_facts AS (
  SELECT run.project_id,
         count(*) FILTER (WHERE NOT (run.state=ANY(CAST(:terminal_states AS text[]))))::integer
           AS active_count,
         count(*) FILTER (WHERE run.state=ANY(CAST(:failed_run_states AS text[])))::integer
           AS failed_count,
         count(*) FILTER (
           WHERE NOT (run.state=ANY(CAST(:terminal_states AS text[])))
             AND run.updated_at<=CAST(:delay_cutoff AS timestamptz)
         )::integer AS delayed_count,
         max(run.updated_at) AS updated_at
  FROM platform.collection_run run
  WHERE run.tenant_id=:tenant_id
  GROUP BY run.project_id
), latest_run AS (
  SELECT DISTINCT ON (run.project_id)
         run.project_id,run.state,run.updated_at
  FROM platform.collection_run run
  WHERE run.tenant_id=:tenant_id
  ORDER BY run.project_id,run.updated_at DESC,run.pub_id DESC
), production_facts AS (
  SELECT production.project_pub_id,
         count(*)::integer AS production_count,max(production.updated_at) AS updated_at
  FROM reporting.formal_report_production production
  WHERE production.tenant_pub_id=:tenant_pub_id
  GROUP BY production.project_pub_id
), latest_production AS (
  SELECT DISTINCT ON (production.project_pub_id)
         production.project_pub_id,production.status,production.updated_at
  FROM reporting.formal_report_production production
  WHERE production.tenant_pub_id=:tenant_pub_id
  ORDER BY production.project_pub_id,production.updated_at DESC,production.pub_id DESC
), delivery_rows AS (
  SELECT production.project_pub_id,delivery.delivered_at,delivery.confirmed_at,delivery.pub_id
  FROM reporting.report_delivery delivery
  JOIN reporting.formal_report_output output
    ON output.report_pub_id=delivery.report_pub_id
   AND output.tenant_pub_id=:tenant_pub_id
  JOIN reporting.formal_report_production production
    ON production.pub_id=output.production_pub_id
   AND production.tenant_pub_id=:tenant_pub_id
  WHERE delivery.tenant_pub_id=:tenant_pub_id
), delivery_facts AS (
  SELECT delivery.project_pub_id,
         count(*) FILTER (WHERE delivery.confirmed_at IS NULL)::integer
           AS pending_confirmation_count,
         max(GREATEST(delivery.delivered_at,delivery.confirmed_at)) AS updated_at
  FROM delivery_rows delivery
  GROUP BY delivery.project_pub_id
), latest_delivery AS (
  SELECT DISTINCT ON (delivery.project_pub_id)
         delivery.project_pub_id,delivery.delivered_at,delivery.confirmed_at
  FROM delivery_rows delivery
  ORDER BY delivery.project_pub_id,delivery.delivered_at DESC,delivery.pub_id DESC
), combined AS (
  SELECT project.*,
         profile.revision AS client_profile_revision,
         assets.revision AS asset_confirmation_revision,
         config.revision AS frozen_monitoring_config_revision,
         intake.profile_exists AS intake_profile_exists,
         intake.truth_confirmed AS intake_truth_confirmed,
         COALESCE(entitlement.entitlements,'[]'::jsonb) AS entitlements,
         COALESCE(entitlement.entitlement_record_count,0) AS entitlement_record_count,
         COALESCE(entitlement.active_entitlement_count,0) AS active_entitlement_count,
         COALESCE(runs.active_count,0) AS collection_active_count,
         COALESCE(runs.failed_count,0) AS collection_failed_count,
         COALESCE(runs.delayed_count,0) AS collection_delayed_count,
         latest_run.state AS latest_collection_state,
         latest_run.updated_at AS latest_collection_at,
         COALESCE(productions.production_count,0) AS production_count,
         latest_production.status AS latest_production_state,
         latest_production.updated_at AS latest_production_at,
         delivery.delivered_at,delivery.confirmed_at,
         COALESCE(deliveries.pending_confirmation_count,0) AS pending_confirmation_count,
         GREATEST(
           project.project_updated_at,
           COALESCE(profile.updated_at,project.project_updated_at),
           COALESCE(assets.updated_at,project.project_updated_at),
           COALESCE(config.updated_at,project.project_updated_at),
           COALESCE(intake.updated_at,project.project_updated_at),
           COALESCE(entitlement.updated_at,project.project_updated_at),
           COALESCE(runs.updated_at,project.project_updated_at),
           COALESCE(productions.updated_at,project.project_updated_at),
           COALESCE(deliveries.updated_at,project.project_updated_at)
         ) AS last_business_fact_at
  FROM project_base project
  LEFT JOIN latest_profile profile ON profile.project_id=project.project_id
  LEFT JOIN latest_assets assets ON assets.project_id=project.project_id
  LEFT JOIN latest_frozen_config config ON config.project_id=project.project_id
  LEFT JOIN intake_facts intake ON intake.project_id=project.project_id
  LEFT JOIN entitlement_facts entitlement ON entitlement.project_id=project.project_id
  LEFT JOIN run_facts runs ON runs.project_id=project.project_id
  LEFT JOIN latest_run ON latest_run.project_id=project.project_id
  LEFT JOIN production_facts productions
    ON productions.project_pub_id=project.project_pub_id
  LEFT JOIN latest_production
    ON latest_production.project_pub_id=project.project_pub_id
  LEFT JOIN delivery_facts deliveries
    ON deliveries.project_pub_id=project.project_pub_id
  LEFT JOIN latest_delivery delivery
    ON delivery.project_pub_id=project.project_pub_id
), attention_flags AS (
  SELECT combined.*,
         COALESCE(latest_collection_state=ANY(CAST(:failed_run_states AS text[])),false)
           OR collection_delayed_count>0 AS collection_attention,
         COALESCE(latest_production_state='failed',false) AS production_failed_attention,
         COALESCE(latest_production_state='awaiting_review',false) AS review_attention,
         pending_confirmation_count>0 AS delivery_attention,
         client_profile_revision IS NULL OR asset_confirmation_revision IS NULL
           OR frozen_monitoring_config_revision IS NULL AS setup_attention,
         intake_profile_exists IS DISTINCT FROM true
           OR intake_truth_confirmed IS DISTINCT FROM true AS intake_attention,
         entitlement_record_count=0 AS entitlement_attention
  FROM combined
), facts AS (
  SELECT flags.*,
         client_profile_revision IS NOT NULL
           AND asset_confirmation_revision IS NOT NULL
           AND frozen_monitoring_config_revision IS NOT NULL AS setup_ready,
         CASE
           WHEN collection_attention THEN 'collection_failed_or_delayed'
           WHEN production_failed_attention THEN 'formal_production_failed'
           WHEN review_attention THEN 'formal_review_required'
           WHEN delivery_attention THEN 'delivery_confirmation_required'
           WHEN setup_attention THEN 'setup_records_missing'
           WHEN intake_attention THEN 'intake_truth_confirmation_required'
           WHEN entitlement_attention THEN 'service_entitlement_unrecorded'
           ELSE 'no_current_attention'
         END AS primary_attention_code,
         CASE
           WHEN collection_attention OR production_failed_attention THEN 'danger'
           WHEN review_attention OR delivery_attention OR setup_attention
             OR intake_attention OR entitlement_attention THEN 'warning'
           ELSE 'neutral'
         END AS primary_attention_severity,
         GREATEST(
           collection_attention::integer+production_failed_attention::integer+
           review_attention::integer+delivery_attention::integer+setup_attention::integer+
           intake_attention::integer+entitlement_attention::integer-1,
           0
         )::integer AS additional_attention_count
  FROM attention_flags flags
), filtered AS (
  SELECT * FROM facts
  WHERE 1=1
  {filter_clauses}
), summary AS (
  SELECT count(*)::integer AS project_count,
         jsonb_build_object(
           'draft',count(*) FILTER (WHERE project_state='draft')::integer,
           'active',count(*) FILTER (WHERE project_state='active')::integer,
           'paused',count(*) FILTER (WHERE project_state='paused')::integer,
           'archived',count(*) FILTER (WHERE project_state='archived')::integer
         ) AS project_state_counts,
         count(*) FILTER (WHERE setup_ready)::integer AS setup_ready_project_count,
         count(*) FILTER (WHERE entitlement_record_count>0)::integer
           AS project_with_entitlement_record_count,
         COALESCE(sum(active_entitlement_count),0)::integer AS active_entitlement_count,
         count(*) FILTER (WHERE primary_attention_code<>'no_current_attention')::integer
           AS attention_project_count
  FROM filtered
), page_candidates AS (
  SELECT * FROM filtered
  WHERE 1=1
  {cursor_clause}
  ORDER BY last_business_fact_at DESC,project_pub_id DESC
  LIMIT :fetch_limit
)
SELECT clock.as_of,
       (SELECT count(*)::integer FROM project_base) AS tenant_project_count,
       jsonb_build_object(
         'project_count',summary.project_count,
         'project_state_counts',summary.project_state_counts,
         'setup_ready_project_count',summary.setup_ready_project_count,
         'project_with_entitlement_record_count',summary.project_with_entitlement_record_count,
         'active_entitlement_count',summary.active_entitlement_count,
         'attention_project_count',summary.attention_project_count
       ) AS summary,
       COALESCE((
         SELECT jsonb_agg(
           jsonb_build_object(
             'project',jsonb_build_object(
               'id',page.project_pub_id,'name',page.project_name,'state',page.project_state
             ),
             'customer',jsonb_build_object(
               'id',page.customer_pub_id,'name',page.customer_name
             ),
             'setup',jsonb_build_object(
               'client_profile_revision',page.client_profile_revision,
               'asset_confirmation_revision',page.asset_confirmation_revision,
               'frozen_monitoring_config_revision',page.frozen_monitoring_config_revision,
               'setup_ready',page.setup_ready,
               'intake_profile_exists',COALESCE(page.intake_profile_exists,false),
               'intake_truth_confirmed',page.intake_truth_confirmed
             ),
             'service_entitlements',page.entitlements,
             'collection',jsonb_build_object(
               'active_count',page.collection_active_count,
               'failed_count',page.collection_failed_count,
               'delayed_count',page.collection_delayed_count,
               'latest_state',page.latest_collection_state,
               'latest_at',page.latest_collection_at
             ),
             'formal_report',jsonb_build_object(
               'production_count',page.production_count,
               'latest_state',page.latest_production_state,
               'latest_at',page.latest_production_at
             ),
             'delivery',jsonb_build_object(
               'delivered_at',page.delivered_at,
               'confirmed_at',page.confirmed_at,
               'pending_confirmation_count',page.pending_confirmation_count
             ),
             'contract_draft_export',NULL,
             'primary_attention',jsonb_build_object(
               'code',page.primary_attention_code,
               'severity',page.primary_attention_severity,
               'additional_count',page.additional_attention_count
             ),
             'last_business_fact_at',page.last_business_fact_at
           ) ORDER BY page.last_business_fact_at DESC,page.project_pub_id DESC
         ) FROM page_candidates page
       ),'[]'::jsonb) AS items
FROM request_clock clock CROSS JOIN summary
"""


def _normalized_search(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


@router.get(
    "/business-overview",
    response_model=BusinessOverviewView,
    operation_id="getOperationsBusinessOverview",
)
def get_operations_business_overview(
    response: Response,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 4,
    q: Annotated[str | None, Query(max_length=120)] = None,
    project_state: ProjectState | None = None,
    attention: AttentionCode | None = None,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> BusinessOverviewView:
    """Return one bounded, secret-free portfolio snapshot for internal Operations roles."""

    principal.require("operations:business:read")
    repository = TenantRepository(session, principal.tenant_pub_id)
    as_of = datetime.now(UTC)
    normalized_q = _normalized_search(q)
    current_filter_hash = _filter_hash(normalized_q, project_state, attention)
    decoded_cursor = _decode_cursor(cursor, current_filter_hash) if cursor else None
    filter_clauses: list[str] = []
    parameters: dict[str, object] = {
        "as_of": as_of,
        "delay_cutoff": as_of - RUN_DELAY_THRESHOLD,
        "failed_run_states": sorted(FAILED_RUN_STATES),
        "fetch_limit": limit + 1,
        "tenant_id": repository.tenant.id,
        "tenant_pub_id": principal.tenant_pub_id,
        "terminal_states": sorted(TERMINAL_RUN_STATES),
    }
    if normalized_q:
        filter_clauses.append(
            "AND (customer_name ILIKE :q_pattern ESCAPE '!' "
            "OR project_name ILIKE :q_pattern ESCAPE '!')"
        )
        parameters["q_pattern"] = f"%{_escape_like(normalized_q)}%"
    if project_state:
        filter_clauses.append("AND project_state=:project_state")
        parameters["project_state"] = project_state.value
    if attention:
        filter_clauses.append("AND primary_attention_code=:attention")
        parameters["attention"] = attention.value
    cursor_clause = ""
    if decoded_cursor:
        cursor_clause = (
            "AND (last_business_fact_at<CAST(:cursor_at AS timestamptz) "
            "OR (last_business_fact_at=CAST(:cursor_at AS timestamptz) "
            "AND project_pub_id<:cursor_project_pub_id))"
        )
        parameters["cursor_at"] = decoded_cursor.last_business_fact_at
        parameters["cursor_project_pub_id"] = decoded_cursor.project_pub_id
    statement = text(
        BUSINESS_OVERVIEW_SQL.format(
            filter_clauses="\n  ".join(filter_clauses),
            cursor_clause=cursor_clause,
        )
    )
    row = session.execute(statement, parameters).mappings().one()
    raw_items = list(row["items"] or [])
    has_more = len(raw_items) > limit
    visible_items: list[BusinessOverviewItem] = []
    for raw_item in raw_items[:limit]:
        entitlements = raw_item.get("service_entitlements")
        if not isinstance(entitlements, list):
            raise RuntimeError("business overview entitlement projection drifted")
        for entitlement in entitlements:
            if not isinstance(entitlement, dict):
                raise RuntimeError("business overview entitlement projection drifted")
            code = entitlement.get("service_code")
            if not isinstance(code, str) or code not in SERVICE_NAMES:
                raise RuntimeError("business overview service catalog drifted")
            entitlement["service_name"] = SERVICE_NAMES[code]
        visible_items.append(BusinessOverviewItem.model_validate(raw_item))
    raw_summary = dict(row["summary"])
    summary = BusinessOverviewSummary(
        scope="filtered",
        tenant_project_count=int(row["tenant_project_count"]),
        **raw_summary,
    )
    next_cursor = (
        _encode_cursor(visible_items[-1], current_filter_hash)
        if has_more and visible_items
        else None
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie, Authorization"
    return BusinessOverviewView(
        as_of=row["as_of"],
        summary=summary,
        commercial_capabilities=CommercialCapabilities(),
        items=visible_items,
        page=BusinessOverviewPage(
            limit=limit,
            next_cursor=next_cursor,
            has_more=has_more,
            filtered_total=summary.project_count,
        ),
    )
