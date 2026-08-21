"""Application service for auditable quotation-service report production.

API callers provide only a tenant-scoped project, service numbers and fact windows.
All facts and evidence assets are resolved internally, frozen before rendering, and
then persisted through the existing reporting and CAS models.  A completed bundle is
committed in one database transaction so a failed multi-service run is never exposed
as a partial success.
"""

from __future__ import annotations

import copy
import hmac
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time
from hashlib import sha256
from importlib import import_module
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row
from sqlalchemy import text
from sqlalchemy.orm import Session

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from domain.reporting.freeze import ReportFreeze, freeze_report
from domain.reporting.libreoffice import refresh_docx_and_export_pdf, report_runtime_preflight
from domain.reporting.policy import assert_customer_report_safe
from domain.reporting.publication_qa import (
    compare_reexport,
    displayed_service1_urls,
    inspect_publication,
)
from domain.reporting.service1_artifacts import render_service1_sidecars
from domain.reporting.service1_governance import release_state_label
from geo_platform.evidence.service import EvidenceService
from geo_platform.tenancy.psycopg import tenant_connection

from .formal_review import build_formal_review_facts
from .formal_review_service1 import enrich_service1_v2_facts
from .formal_review_service2 import enrich_service2_v2_facts
from .service3_review_v2 import build_service3_review_v2_facts

FORMAL_WORKFLOW_TYPE = "formal_report_production"
FORMAL_STRATEGY = "preregistered_scope_v1"
LEGACY_FORMAL_STRATEGY = "evidence_completeness_v1"
FORMAL_METRIC_VERSION = "formal-review-metrics-v2"
FORMAL_SCORER_VERSION = "formal-review-evidence-v2"

LEGACY_SERVICE_CATALOG = "legacy_report_services_v1"
QUOTATION_SERVICE_CATALOG = "quotation_services_v2"
SERVICE_CATALOGS = frozenset({LEGACY_SERVICE_CATALOG, QUOTATION_SERVICE_CATALOG})

LEGACY_SERVICE_TITLES: dict[int, str] = {
    1: "品牌 GEO 推荐结果评测报告",
    2: "品牌 GEO 内容生态风险核查报告",
    3: "官网内容 AI 引用能效评估报告",
    4: "GEO 试点与效果验证报告",
}

QUOTATION_SERVICE_TITLES: dict[int, str] = {
    1: "AI 推荐排名效果测试报告",
    2: "主动拉踩内容核查报告",
    3: "被拉踩内容核查报告",
    4: "官网内容 AI 引用效率分析报告",
    5: "内容发布与排名提升试点报告",
}

LEGACY_SERVICE_CODES: dict[int, str] = {
    1: "legacy_ranking_assessment",
    2: "legacy_content_ecosystem_risk",
    3: "legacy_official_site_efficiency",
    4: "legacy_pilot_comparison",
}

QUOTATION_SERVICE_CODES: dict[int, str] = {
    1: "ranking_test",
    2: "outbound_disparagement_audit",
    3: "inbound_disparagement_audit",
    4: "official_site_audit",
    5: "content_publishing_pilot",
}

_MIME_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "zip": "application/zip",
    "manifest": "application/json",
}


def _artifact_formats(service: int, document_status: str) -> tuple[str, ...]:
    if service == 1 and document_status in {
        "internal_review",
        "delivery_candidate",
        "approved_signed",
    }:
        return ("docx", "pdf", "xlsx", "zip", "manifest")
    return ("docx", "pdf", "manifest")


class FormalProductionNotFound(LookupError):
    pass


class FormalProductionConflict(RuntimeError):
    pass


class FormalProductionInvalid(ValueError):
    pass


class FormalProductionIncomplete(RuntimeError):
    pass


def formal_review_contract_hash(*, approved: bool, reviewer_pub_id: str, rationale: str) -> str:
    """Hash the exact review signal contract claimed by the API transaction."""

    if (
        type(approved) is not bool
        or not isinstance(reviewer_pub_id, str)
        or not reviewer_pub_id
        or not isinstance(rationale, str)
        or not 1 <= len(rationale) <= 1000
    ):
        raise FormalProductionInvalid("formal_review_signal_invalid")
    payload = {
        "approved": approved,
        "reviewer_pub_id": reviewer_pub_id,
        "rationale": rationale,
    }
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class FormalWindow:
    start: date
    end: date


@dataclass(frozen=True, slots=True)
class FormalProductionRequest:
    pub_id: str
    tenant_pub_id: str
    project_pub_id: str
    services: tuple[int, ...]
    window: FormalWindow
    document_status: str
    candidate_group_strategy: str
    frozen_at: datetime
    created_by_pub_id: str
    request_hash: str
    before_window: FormalWindow | None = None
    after_window: FormalWindow | None = None
    document_governance: Mapping[str, Any] | None = None
    service_catalog_version: str = LEGACY_SERVICE_CATALOG
    sop_project_pub_id: str | None = None


@dataclass(frozen=True, slots=True)
class GeneratedFormalBundle:
    facts: dict[int, dict[str, Any]]
    artifacts: dict[int, dict[str, bytes]]
    fact_snapshot_hash: str


class FormalReportAdapter(Protocol):
    service_number: int
    service_code: str
    title: str

    def build(self, context: FormalBuildContext) -> dict[str, Any]: ...

    def render(
        self,
        facts: dict[str, Any],
        *,
        blob_loader: Callable[[str, str], bytes],
    ) -> bytes: ...


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _canonical_hash(value: object) -> str:
    return sha256(_canonical_json(value).encode()).hexdigest()


def _stable_pub_id(prefix: str, *values: object) -> str:
    material = "|".join(str(value) for value in values)
    return f"{prefix}_{sha256(material.encode()).hexdigest()[:26]}"


def normalize_services(
    values: Sequence[int], *, service_catalog_version: str = LEGACY_SERVICE_CATALOG
) -> tuple[int, ...]:
    if service_catalog_version not in SERVICE_CATALOGS:
        raise FormalProductionInvalid("invalid_service_catalog_version")
    allowed = (
        frozenset(QUOTATION_SERVICE_CODES)
        if service_catalog_version == QUOTATION_SERVICE_CATALOG
        else frozenset(LEGACY_SERVICE_CODES)
    )
    services = tuple(sorted(set(values)))
    if (
        not services
        or len(services) != len(values)
        or any(value not in allowed for value in services)
    ):
        raise FormalProductionInvalid("invalid_services")
    return services


def request_contract(
    *,
    project_pub_id: str,
    services: Sequence[int],
    window: FormalWindow,
    document_status: str,
    candidate_group_strategy: str,
    before_window: FormalWindow | None,
    after_window: FormalWindow | None,
    document_governance: Mapping[str, Any] | None = None,
    service_catalog_version: str | None = None,
    sop_project_pub_id: str | None = None,
) -> dict[str, Any]:
    resolved_catalog = service_catalog_version or LEGACY_SERVICE_CATALOG
    normalized_services = normalize_services(services, service_catalog_version=resolved_catalog)
    if window.start > window.end:
        raise FormalProductionInvalid("invalid_window")
    if (window.end - window.start).days > 366:
        raise FormalProductionInvalid("window_too_large")
    if document_status not in {
        "pre_formal",
        "formal",
        "internal_review",
        "delivery_candidate",
    }:
        raise FormalProductionInvalid("invalid_document_status")
    if candidate_group_strategy not in {FORMAL_STRATEGY, LEGACY_FORMAL_STRATEGY}:
        raise FormalProductionInvalid("invalid_candidate_group_strategy")
    if document_status in {"internal_review", "delivery_candidate"} and (
        candidate_group_strategy != FORMAL_STRATEGY
    ):
        raise FormalProductionInvalid("preregistered_scope_strategy_required")
    if document_status in {"internal_review", "delivery_candidate"} and (
        document_governance is None
    ):
        raise FormalProductionInvalid("document_governance_required")
    governance: dict[str, Any] | None = None
    if document_governance is not None:
        governance = {
            "version": str(document_governance.get("version") or "").strip(),
            "prepared_by": str(document_governance.get("prepared_by") or "").strip(),
            "reviewed_by": str(document_governance.get("reviewed_by") or "").strip() or None,
            "prepared_date": str(document_governance.get("prepared_date") or "").strip(),
            "reviewed_date": str(document_governance.get("reviewed_date") or "").strip() or None,
        }
        if not re.fullmatch(r"V[1-9]\d*\.\d+", governance["version"]):
            raise FormalProductionInvalid("invalid_report_version")
        if not governance["prepared_by"] or not governance["prepared_date"]:
            raise FormalProductionInvalid("document_preparation_record_required")
        try:
            date.fromisoformat(governance["prepared_date"])
            if governance["reviewed_date"]:
                date.fromisoformat(str(governance["reviewed_date"]))
        except ValueError as exc:
            raise FormalProductionInvalid("invalid_governance_date") from exc
        if document_status == "delivery_candidate" and (
            not governance["reviewed_by"] or not governance["reviewed_date"]
        ):
            raise FormalProductionInvalid("candidate_review_record_required")
    comparison_service = 5 if resolved_catalog == QUOTATION_SERVICE_CATALOG else 4
    if comparison_service in normalized_services:
        if before_window is None or after_window is None:
            raise FormalProductionInvalid(f"service{comparison_service}_windows_required")
        if before_window.start > before_window.end or after_window.start > after_window.end:
            raise FormalProductionInvalid("invalid_comparison_window")
        if before_window.end >= after_window.start:
            raise FormalProductionInvalid("comparison_windows_overlap")
    elif before_window is not None or after_window is not None:
        raise FormalProductionInvalid(f"comparison_windows_require_service{comparison_service}")
    normalized_sop_project = str(sop_project_pub_id or "").strip() or None
    requires_sop_project = resolved_catalog == QUOTATION_SERVICE_CATALOG and bool(
        {2, 5}.intersection(normalized_services)
    )
    if requires_sop_project and normalized_sop_project is None:
        raise FormalProductionInvalid("sop_project_required_for_selected_services")
    if not requires_sop_project and normalized_sop_project is not None:
        raise FormalProductionInvalid("sop_project_not_applicable")
    contract = {
        "project_pub_id": project_pub_id,
        "services": list(normalized_services),
        "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "document_status": document_status,
        "candidate_group_strategy": candidate_group_strategy,
        "before_window": (
            {"start": before_window.start.isoformat(), "end": before_window.end.isoformat()}
            if before_window
            else None
        ),
        "after_window": (
            {"start": after_window.start.isoformat(), "end": after_window.end.isoformat()}
            if after_window
            else None
        ),
    }
    # Legacy immutable rows predate the governance record; omitting the key for
    # those requests preserves their historical request hash.
    if governance is not None:
        contract["document_governance"] = governance
    if service_catalog_version is not None:
        contract["service_catalog_version"] = resolved_catalog
    if normalized_sop_project is not None:
        contract["sop_project_pub_id"] = normalized_sop_project
    return contract


_DROP = object()
_INTERNAL_KEYS = frozenset(
    {
        "object_key",
        "answer_screenshot_ref",
        "audit_refs",
        "judgment_pub_ids",
        "fact_bundle",
        "rendered_bundle",
        "post_analysis_wiring",
        "customer_render_policy",
        "loaded_config_ids",
        "before_selected_answer_ids",
        "after_selected_answer_ids",
        "answer_refs",
        "candidate_group_id",
        "group_id",
        "selected_group_ids",
        "cited_text",
        "workflow_id",
        "id",
    }
)
_PUBLIC_ID_RE = re.compile(r"^[a-z][a-z0-9]{1,15}_(?:[0-9A-HJKMNP-TV-Z]{26}|[0-9a-f]{20,64})$")


def customer_fact_snapshot(value: Any) -> Any:
    """Remove implementation references while preserving customer-readable facts."""

    def clean(child: Any, *, key: str | None = None) -> Any:
        normalized = (key or "").lower()
        if (
            normalized in _INTERNAL_KEYS
            or normalized.startswith("_")
            or normalized == "pub_id"
            or normalized.endswith("_id")
            or normalized.endswith("_ids")
            or normalized.endswith("_pub_id")
            or normalized.endswith("_pub_ids")
        ):
            return _DROP
        if isinstance(child, Mapping):
            output: dict[str, Any] = {}
            for raw_key, raw_value in child.items():
                name = str(raw_key)
                cleaned = clean(raw_value, key=name)
                if cleaned is not _DROP:
                    output[name] = cleaned
            return output
        if isinstance(child, Sequence) and not isinstance(child, str | bytes):
            output_list = []
            for item in child:
                cleaned = clean(item)
                if cleaned is not _DROP:
                    output_list.append(cleaned)
            return output_list
        if isinstance(child, str) and (
            child.startswith("file://")
            or child.startswith("/home/")
            or child.startswith(("ans_", "dpj_", "evd_", "prj_", "rpt_", "run_", "tnt_", "usr_"))
            or _PUBLIC_ID_RE.fullmatch(child) is not None
        ):
            return _DROP
        return child

    result = clean(value)
    return None if result is _DROP else result


def _freeze_service_fact(
    request: FormalProductionRequest,
    service: int,
    facts: Mapping[str, Any],
) -> ReportFreeze:
    customer_facts = customer_fact_snapshot(facts)
    if not isinstance(customer_facts, dict):
        raise FormalProductionIncomplete("customer_fact_snapshot_invalid")
    filters = {
        "formal_production": True,
        "service_number": service,
        "document_status": request.document_status,
        "candidate_group_strategy": request.candidate_group_strategy,
        "before_window": (
            {
                "start": request.before_window.start.isoformat(),
                "end": request.before_window.end.isoformat(),
            }
            if request.before_window
            else None
        ),
        "after_window": (
            {
                "start": request.after_window.start.isoformat(),
                "end": request.after_window.end.isoformat(),
            }
            if request.after_window
            else None
        ),
    }
    if request.service_catalog_version == QUOTATION_SERVICE_CATALOG:
        filters.update(
            {
                "service_catalog_version": request.service_catalog_version,
                "service_code": _service_code_for(request, service),
                "sop_project_pub_id": request.sop_project_pub_id,
            }
        )
    return freeze_report(
        window_start=datetime.combine(request.window.start, time.min, tzinfo=UTC),
        window_end=datetime.combine(request.window.end, time.max, tzinfo=UTC),
        filters=filters,
        metric_version=FORMAL_METRIC_VERSION,
        scorer_version=FORMAL_SCORER_VERSION,
        fact_rows=[customer_facts],
    )


def evidence_descriptors(value: Any) -> list[dict[str, str]]:
    """Collect only internally-built CAS descriptors, never arbitrary caller IDs."""

    found: dict[str, dict[str, str]] = {}

    def visit(child: Any) -> None:
        if isinstance(child, Mapping):
            pub_id = child.get("pub_id")
            object_key = child.get("object_key")
            digest = child.get("sha256")
            mime_type = child.get("mime_type")
            if all(isinstance(item, str) and item for item in (pub_id, object_key, digest)):
                if len(str(digest)) == 64 and all(
                    char in "0123456789abcdef" for char in str(digest)
                ):
                    descriptor = {
                        "pub_id": str(pub_id),
                        "object_key": str(object_key),
                        "sha256": str(digest),
                        "mime_type": str(mime_type or "application/octet-stream"),
                    }
                    previous = found.get(str(pub_id))
                    if previous is not None and previous != descriptor:
                        raise FormalProductionIncomplete("frozen_evidence_descriptor_conflict")
                    found[str(pub_id)] = descriptor
            for item in child.values():
                visit(item)
        elif isinstance(child, Sequence) and not isinstance(child, str | bytes):
            for item in child:
                visit(item)

    visit(value)
    return [found[key] for key in sorted(found)]


def _complete_cas_descriptor(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    pub_id = value.get("pub_id")
    object_key = value.get("object_key")
    digest = value.get("sha256")
    return bool(
        isinstance(pub_id, str)
        and pub_id
        and isinstance(object_key, str)
        and object_key
        and isinstance(digest, str)
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
    )


def formal_evidence_gate(service: int, facts: Mapping[str, Any]) -> tuple[bool, tuple[str, ...]]:
    """Return a conservative, explicit gate for customer-signable report facts."""

    reasons: list[str] = []
    service_code = str(facts.get("service_code") or "")
    if service == 1:
        service1_facts_value = facts.get("service1")
        service1_facts: Mapping[str, Any] = (
            service1_facts_value if isinstance(service1_facts_value, Mapping) else {}
        )
        delivery = service1_facts.get("delivery_v3")
        if not isinstance(delivery, Mapping):
            # Read-only compatibility for already frozen v2 productions.  New
            # requests cannot use this path because their strategy is preregistered_scope_v1.
            legacy = service1_facts.get("delivery_v2")
            candidate_groups = service1_facts.get("candidate_groups")
            selected = (
                [
                    row
                    for row in candidate_groups
                    if isinstance(row, Mapping) and row.get("selected_for_main_report")
                ]
                if isinstance(candidate_groups, Sequence)
                and not isinstance(candidate_groups, str | bytes)
                else []
            )
            scope = legacy.get("scope") if isinstance(legacy, Mapping) else None
            registry = legacy.get("sample_registry") if isinstance(legacy, Mapping) else None
            required = int(service1_facts.get("quotation_required_repetitions_per_cell") or 0)
            if len(selected) != 3:
                reasons.append("three_complete_candidate_groups_required")
            if any(
                int(group.get("observed_cells") or 0) != int(group.get("expected_cells") or 0)
                or int(group.get("expected_cells") or 0) <= 0
                for group in selected
            ):
                reasons.append("selected_candidate_group_cells_incomplete")
            answers = int(scope.get("answers") or 0) if isinstance(scope, Mapping) else 0
            extracts = int(scope.get("extract_ok") or 0) if isinstance(scope, Mapping) else 0
            if not answers or extracts != answers:
                reasons.append("brand_extraction_incomplete")
            if (
                int(scope.get("current_repetitions") or 0) if isinstance(scope, Mapping) else 0
            ) < required or required <= 0:
                reasons.append("quotation_repetitions_incomplete")
            if (
                not isinstance(registry, Sequence)
                or len(registry) != answers
                or any(
                    not isinstance(row, Mapping)
                    or not (row.get("has_share_image") or row.get("has_answer_screenshot"))
                    for row in registry
                )
            ):
                reasons.append("answer_visual_evidence_incomplete")
            return not reasons, tuple(dict.fromkeys(reasons))
        scope = delivery.get("scope") if isinstance(delivery, Mapping) else None
        required = int(service1_facts.get("quotation_required_repetitions_per_cell") or 0)
        current = int(scope.get("current_repetitions") or 0) if isinstance(scope, Mapping) else 0
        answers = int(scope.get("answers") or 0) if isinstance(scope, Mapping) else 0
        extracts = int(scope.get("extract_ok") or 0) if isinstance(scope, Mapping) else 0
        selected_groups = delivery.get("selected_groups")
        selected = (
            [row for row in selected_groups if isinstance(row, Mapping)]
            if isinstance(selected_groups, Sequence)
            and not isinstance(selected_groups, str | bytes)
            else []
        )
        quotation = delivery.get("quotation_gate")
        if not isinstance(quotation, Mapping) or quotation.get("status") != "ready":
            reasons.extend(
                str(value)
                for value in (
                    quotation.get("reasons", []) if isinstance(quotation, Mapping) else []
                )
            )
            if not reasons:
                reasons.append("service1_quotation_gate_incomplete")
        if len(selected) != 3:
            reasons.append("three_complete_candidate_groups_required")
        if any(
            int(group.get("observed_cells") or 0) != int(group.get("expected_cells") or 0)
            or int(group.get("expected_cells") or 0) <= 0
            for group in selected
        ):
            reasons.append("selected_candidate_group_cells_incomplete")
        if not answers or extracts != answers:
            reasons.append("brand_extraction_incomplete")
        if current < required or required <= 0:
            reasons.append("quotation_repetitions_incomplete")
        registry = delivery.get("sample_registry")
        if (
            not isinstance(registry, Sequence)
            or len(registry) != answers
            or any(
                not isinstance(row, Mapping)
                or not row.get("answer_evidence")
                or not row.get("screenshot_evidence")
                or not row.get("share_image_evidence")
                for row in registry
            )
        ):
            reasons.append("answer_visual_evidence_incomplete")
        unclassified_entities = (
            int(scope.get("unclassified_entities") or 0) if isinstance(scope, Mapping) else 0
        )
        if unclassified_entities > 0:
            reasons.append("entity_master_classification_incomplete")
        if not delivery.get("representative_platforms_complete"):
            reasons.append("three_platform_representative_evidence_required")
    elif service_code == "outbound_disparagement_audit":
        gate = facts.get("evidence_gate")
        if not isinstance(gate, Mapping) or gate.get("status") != "ready":
            reasons.extend(
                str(value)
                for value in (gate.get("reasons", []) if isinstance(gate, Mapping) else [])
            )
            if not reasons:
                reasons.append("outbound_disparagement_evidence_incomplete")
    elif service_code in {
        "legacy_content_ecosystem_risk",
        "inbound_disparagement_audit",
    } or (not service_code and service == 2):
        service_facts = facts.get("service2")
        delivery = service_facts.get("delivery_v2") if isinstance(service_facts, Mapping) else None
        citation = delivery.get("citation_funnel") if isinstance(delivery, Mapping) else None
        fetch = delivery.get("source_fetch") if isinstance(delivery, Mapping) else None
        visual = delivery.get("answer_visual_coverage") if isinstance(delivery, Mapping) else None
        cases = delivery.get("cases") if isinstance(delivery, Mapping) else None
        if not isinstance(citation, Mapping) or int(citation.get("eligible_answers") or 0) <= 0:
            reasons.append("eligible_answers_missing")
        if not isinstance(fetch, Mapping) or fetch.get("planner_mode") != "answer_level_v2":
            reasons.append("answer_level_source_plan_required")
        cited_answers = (
            int(citation.get("answers_with_citation") or 0) if isinstance(citation, Mapping) else 0
        )
        planned_answers = (
            int(fetch.get("answers_with_planned_documents") or 0)
            if isinstance(fetch, Mapping)
            else 0
        )
        related_documents = (
            int(fetch.get("documents_with_answer_relation") or 0)
            if isinstance(fetch, Mapping)
            else 0
        )
        fetched_documents = int(fetch.get("ok") or 0) if isinstance(fetch, Mapping) else 0
        if planned_answers < cited_answers:
            reasons.append("answer_level_source_planning_incomplete")
        if related_documents <= 0 or fetched_documents < related_documents:
            reasons.append("source_document_fetch_incomplete")
        if isinstance(cases, Sequence) and cases:
            native_dom = (
                int(visual.get("cases_with_native_dom_anchor") or 0)
                if isinstance(visual, Mapping)
                else 0
            )
            native_ocr = (
                int(visual.get("cases_with_native_ocr_anchor") or 0)
                if isinstance(visual, Mapping)
                else 0
            )
            if native_dom + native_ocr < len(cases):
                reasons.append("native_answer_anchor_incomplete")
            if any(
                not isinstance(case, Mapping)
                or not _complete_cas_descriptor(case.get("answer_screenshot"))
                for case in cases
            ):
                reasons.append("native_answer_screenshot_incomplete")
        source_cases = delivery.get("source_cases") if isinstance(delivery, Mapping) else None
        if isinstance(source_cases, Sequence) and any(
            not isinstance(case, Mapping)
            or not _complete_cas_descriptor(case.get("source_screenshot"))
            for case in source_cases
        ):
            reasons.append("source_case_screenshot_incomplete")
    elif service_code in {
        "legacy_official_site_efficiency",
        "official_site_audit",
    } or (not service_code and service == 3):
        metrics = facts.get("metrics")
        if not isinstance(metrics, Mapping) or int(metrics.get("answers_total") or 0) <= 0:
            reasons.append("eligible_answers_missing")
        if not isinstance(metrics, Mapping) or int(
            metrics.get("adoption_evaluated_answers") or 0
        ) < int(metrics.get("answers_with_own_site_citation") or 0):
            reasons.append("official_snapshot_evaluation_incomplete")
        if not facts.get("own_site_host"):
            reasons.append("official_site_domain_missing")
    elif service_code == "content_publishing_pilot":
        gate = facts.get("evidence_gate")
        comparison = facts.get("comparability")
        publishing = facts.get("publication_evidence")
        publication_gate = (
            publishing.get("evidence_gate") if isinstance(publishing, Mapping) else None
        )
        if not isinstance(gate, Mapping) or gate.get("status") != "sufficient_for_description":
            reasons.append("service5_measurement_evidence_insufficient")
        if not isinstance(comparison, Mapping) or comparison.get("status") != "comparable":
            reasons.append("service5_arms_not_comparable")
        if not facts.get("metrics"):
            reasons.append("service5_metrics_missing")
        if not isinstance(publication_gate, Mapping) or publication_gate.get("status") != "ready":
            reasons.extend(
                str(value)
                for value in (
                    publication_gate.get("reasons", [])
                    if isinstance(publication_gate, Mapping)
                    else ["service5_publication_evidence_missing"]
                )
            )
    elif service_code == "legacy_pilot_comparison" or (not service_code and service == 4):
        gate = facts.get("evidence_gate")
        comparison = facts.get("comparability")
        if not isinstance(gate, Mapping) or gate.get("status") != "sufficient_for_description":
            reasons.append("service4_evidence_insufficient")
        if not isinstance(comparison, Mapping) or comparison.get("status") != "comparable":
            reasons.append("service4_arms_not_comparable")
        if not facts.get("metrics"):
            reasons.append("service4_metrics_missing")
    else:
        reasons.append("unsupported_service")
    return not reasons, tuple(reasons)


@dataclass
class FormalBuildContext:
    dsn: str
    request: FormalProductionRequest
    blob_loader: Callable[[str, str], bytes]
    _base_facts: dict[str, Any] | None = None

    def base_facts(self) -> dict[str, Any]:
        if self._base_facts is None:
            self._base_facts = build_formal_review_facts(
                dsn=self.dsn,
                tenant_pub_id=self.request.tenant_pub_id,
                project_pub_id=self.request.project_pub_id,
                start=self.request.window.start,
                end=self.request.window.end,
                generated_at=self.request.frozen_at,
            )
        return copy.deepcopy(self._base_facts)

    def attach_service1_assets(self, facts: dict[str, Any]) -> None:
        answer_ids = [
            str(row.get("answer_pub_id") or "")
            for row in facts["service1"].get("answer_registry", [])
            if isinstance(row, dict)
            and row.get("selected_for_main_report")
            and row.get("answer_pub_id")
        ]
        if not answer_ids:
            facts["_formal_evidence_assets"] = {}
            return
        with tenant_connection(
            self.dsn, self.request.tenant_pub_id, row_factory=dict_row
        ) as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT ON (relation.from_pub_id)
                       relation.from_pub_id,asset.pub_id,asset.object_key,asset.sha256,
                       asset.mime_type,asset.kind
                FROM evidence.evidence_relation relation
                JOIN evidence.evidence_asset asset
                  ON asset.tenant_pub_id=relation.tenant_pub_id
                 AND asset.pub_id=relation.to_pub_id
                WHERE relation.tenant_pub_id=%s
                  AND relation.from_pub_id=ANY(%s::text[])
                  AND asset.kind IN (
                    'share_image','answer_excerpt_screenshot','answer_screenshot'
                  )
                  AND asset.mime_type LIKE 'image/%%' AND asset.deleted_at IS NULL
                ORDER BY relation.from_pub_id,
                         CASE asset.kind
                           WHEN 'share_image' THEN 0
                           WHEN 'answer_excerpt_screenshot' THEN 1
                           ELSE 2
                         END,
                         asset.capture_time DESC,asset.pub_id DESC
                """,
                (self.request.tenant_pub_id, answer_ids),
            ).fetchall()
        facts["_formal_evidence_assets"] = {
            str(row["from_pub_id"]): {
                "pub_id": str(row["pub_id"]),
                "object_key": str(row["object_key"]),
                "sha256": str(row["sha256"]),
                "mime_type": str(row["mime_type"]),
                "kind": str(row["kind"]),
            }
            for row in rows
        }


class _Service1Adapter:
    service_number = 1
    service_code = LEGACY_SERVICE_CODES[1]
    title = LEGACY_SERVICE_TITLES[1]
    renderer_title: str | None = None
    subtitle = "服务 1 · 品牌 AI 可见性与竞品表现"

    def build(self, context: FormalBuildContext) -> dict[str, Any]:
        facts = enrich_service1_v2_facts(
            dsn=context.dsn,
            tenant_pub_id=context.request.tenant_pub_id,
            facts=context.base_facts(),
            answer_anchor_overrides={},
        )
        context.attach_service1_assets(facts)
        return facts

    def render(self, facts: dict[str, Any], *, blob_loader: Callable[[str, str], bytes]) -> bytes:
        renderer = import_module(
            "domain.reporting.formal_service1_delivery_docx"
        ).render_service1_delivery_docx
        assets = {
            answer_id: blob_loader(str(row["object_key"]), str(row["sha256"]))
            for answer_id, row in facts.get("_formal_evidence_assets", {}).items()
        }
        return cast(
            bytes,
            renderer(
                facts,
                screenshots=assets,
                service_number=self.service_number,
                report_title=self.renderer_title,
                report_subtitle=self.subtitle,
            ),
        )


class _QuotationService1Adapter(_Service1Adapter):
    service_code = QUOTATION_SERVICE_CODES[1]
    title = QUOTATION_SERVICE_TITLES[1]
    renderer_title = title
    subtitle = "服务 1 · AI 推荐排名效果与 API/手机端差异"


class _Service2Adapter:
    service_number = 2
    service_code = LEGACY_SERVICE_CODES[2]
    title = LEGACY_SERVICE_TITLES[2]
    subtitle = "服务 2 · AI 拉踩表述、公开事实核查与可视证据"

    def build(self, context: FormalBuildContext) -> dict[str, Any]:
        return enrich_service2_v2_facts(
            dsn=context.dsn,
            tenant_pub_id=context.request.tenant_pub_id,
            facts=context.base_facts(),
            answer_anchor_overrides={},
        )

    def render(self, facts: dict[str, Any], *, blob_loader: Callable[[str, str], bytes]) -> bytes:
        renderer = import_module(
            "domain.reporting.formal_review_service2_docx"
        ).render_service2_v2_docx
        delivery = facts["service2"]["delivery_v2"]
        cases = [
            *delivery.get("cases", []),
            *delivery.get("supplemental_factcheck_cases", []),
        ]
        answer_assets: dict[str, bytes] = {}
        for case in cases:
            descriptor = case.get("answer_screenshot") if isinstance(case, dict) else None
            if not isinstance(descriptor, dict):
                continue
            key = str(descriptor.get("object_key") or "")
            digest = str(descriptor.get("sha256") or "")
            answer_id = str(case.get("answer_pub_id") or "")
            if key and digest and answer_id:
                answer_assets[answer_id] = blob_loader(key, digest)
        source_assets: dict[str, bytes] = {}
        for case in delivery.get("source_cases", []):
            descriptor = case.get("source_screenshot") if isinstance(case, dict) else None
            if not isinstance(descriptor, dict):
                continue
            pub_id = str(descriptor.get("pub_id") or "")
            key = str(descriptor.get("object_key") or "")
            digest = str(descriptor.get("sha256") or "")
            if pub_id and key and digest:
                source_assets[pub_id] = blob_loader(key, digest)
        return cast(
            bytes,
            renderer(
                facts,
                answer_screenshots=answer_assets,
                source_captures={},
                source_case_screenshots=source_assets,
                service_number=self.service_number,
                report_title=self.title,
                report_subtitle=self.subtitle,
            ),
        )


class _OutboundService2Adapter:
    service_number = 2
    service_code = QUOTATION_SERVICE_CODES[2]
    title = QUOTATION_SERVICE_TITLES[2]

    def build(self, context: FormalBuildContext) -> dict[str, Any]:
        if not context.request.sop_project_pub_id:
            raise FormalProductionInvalid("service2_sop_project_required")
        builder = import_module(
            "geo_platform.reports.formal_review_service2_outbound"
        ).build_outbound_disparagement_facts
        facts = cast(
            dict[str, Any],
            builder(
                dsn=context.dsn,
                tenant_pub_id=context.request.tenant_pub_id,
                sop_project_pub_id=context.request.sop_project_pub_id,
                start=context.request.window.start,
                end=context.request.window.end,
                generated_at=context.request.frozen_at,
            ),
        )
        raw_aliases = facts.get("brand_aliases")
        aliases = (
            raw_aliases
            if isinstance(raw_aliases, Sequence) and not isinstance(raw_aliases, str | bytes)
            else ()
        )
        _assert_sop_brand_binding(
            context.request,
            [facts.get("target_brand"), *aliases],
            dsn=context.dsn,
        )
        return facts

    def render(self, facts: dict[str, Any], *, blob_loader: Callable[[str, str], bytes]) -> bytes:
        del blob_loader
        renderer = import_module(
            "domain.reporting.formal_review_service2_outbound_docx"
        ).render_outbound_disparagement_docx
        return cast(bytes, renderer(facts))


class _InboundService3Adapter(_Service2Adapter):
    service_number = 3
    service_code = QUOTATION_SERVICE_CODES[3]
    title = QUOTATION_SERVICE_TITLES[3]
    subtitle = "服务 3 · AI 回答与公开信源中的负向比较证据"


class _Service3Adapter:
    service_number = 3
    service_code = LEGACY_SERVICE_CODES[3]
    title = LEGACY_SERVICE_TITLES[3]
    subtitle = "服务 3 · 回答—URL—官网正文证据链"

    def build(self, context: FormalBuildContext) -> dict[str, Any]:
        return build_service3_review_v2_facts(
            dsn=context.dsn,
            blob_loader=context.blob_loader,
            tenant_pub_id=context.request.tenant_pub_id,
            project_pub_id=context.request.project_pub_id,
            start=context.request.window.start,
            end=context.request.window.end,
            generated_at=context.request.frozen_at,
        )

    def render(self, facts: dict[str, Any], *, blob_loader: Callable[[str, str], bytes]) -> bytes:
        renderer = import_module(
            "domain.reporting.formal_review_service3_docx"
        ).render_service3_v2_docx
        assets: dict[str, bytes] = {}
        for case in facts.get("selected_evidence_cases", []):
            if not isinstance(case, dict):
                continue
            for field in ("answer_screenshot", "official_screenshot"):
                descriptor = case.get(field)
                if not isinstance(descriptor, dict):
                    continue
                pub_id = str(descriptor.get("pub_id") or "")
                key = str(descriptor.get("object_key") or "")
                digest = str(descriptor.get("sha256") or "")
                if pub_id and key and digest:
                    assets[pub_id] = blob_loader(key, digest)
        return cast(
            bytes,
            renderer(
                facts,
                evidence_assets=assets,
                official_captures={},
                service_number=self.service_number,
                report_title=self.title,
                report_subtitle=self.subtitle,
            ),
        )


class _OfficialSiteService4Adapter(_Service3Adapter):
    service_number = 4
    service_code = QUOTATION_SERVICE_CODES[4]
    title = QUOTATION_SERVICE_TITLES[4]
    subtitle = "服务 4 · 回答—URL—官网正文证据链"


class _Service4Adapter:
    service_number = 4
    service_code = LEGACY_SERVICE_CODES[4]
    title = LEGACY_SERVICE_TITLES[4]

    def build(self, context: FormalBuildContext) -> dict[str, Any]:
        before = context.request.before_window
        after = context.request.after_window
        if before is None or after is None:
            raise FormalProductionInvalid("service4_windows_required")
        builder = import_module(
            "geo_platform.reports.formal_review_service4"
        ).build_service4_review_facts
        return cast(
            dict[str, Any],
            builder(
                dsn=context.dsn,
                tenant_pub_id=context.request.tenant_pub_id,
                project_pub_id=context.request.project_pub_id,
                before_start=before.start,
                before_end=before.end,
                after_start=after.start,
                after_end=after.end,
                generated_at=context.request.frozen_at,
            ),
        )

    def render(self, facts: dict[str, Any], *, blob_loader: Callable[[str, str], bytes]) -> bytes:
        del blob_loader
        renderer = import_module(
            "domain.reporting.formal_review_service4_docx"
        ).render_service4_review_docx
        return cast(bytes, renderer(facts))


class _PublishingService5Adapter:
    service_number = 5
    service_code = QUOTATION_SERVICE_CODES[5]
    title = QUOTATION_SERVICE_TITLES[5]

    def build(self, context: FormalBuildContext) -> dict[str, Any]:
        before = context.request.before_window
        after = context.request.after_window
        if before is None or after is None:
            raise FormalProductionInvalid("service5_windows_required")
        if not context.request.sop_project_pub_id:
            raise FormalProductionInvalid("service5_sop_project_required")
        publishing_builder = import_module(
            "geo_platform.reports.formal_review_service5"
        ).build_publishing_evidence
        publication_evidence = publishing_builder(
            dsn=context.dsn,
            tenant_pub_id=context.request.tenant_pub_id,
            sop_project_pub_id=context.request.sop_project_pub_id,
            before_end=before.end,
            after_start=after.start,
            window_start=context.request.window.start,
            window_end=context.request.window.end,
            generated_at=context.request.frozen_at,
        )
        raw_aliases = publication_evidence.get("brand_aliases")
        aliases = (
            raw_aliases
            if isinstance(raw_aliases, Sequence) and not isinstance(raw_aliases, str | bytes)
            else ()
        )
        _assert_sop_brand_binding(
            context.request,
            [publication_evidence.get("target_brand"), *aliases],
            dsn=context.dsn,
        )
        comparison_builder = import_module(
            "geo_platform.reports.formal_review_service4"
        ).build_service4_review_facts
        facts = cast(
            dict[str, Any],
            comparison_builder(
                dsn=context.dsn,
                tenant_pub_id=context.request.tenant_pub_id,
                project_pub_id=context.request.project_pub_id,
                before_start=before.start,
                before_end=before.end,
                after_start=after.start,
                after_end=after.end,
                generated_at=context.request.frozen_at,
                target_brand=str(publication_evidence.get("target_brand") or ""),
            ),
        )
        facts["publication_evidence"] = publication_evidence
        return facts

    def render(self, facts: dict[str, Any], *, blob_loader: Callable[[str, str], bytes]) -> bytes:
        del blob_loader
        renderer = import_module(
            "domain.reporting.formal_review_service5_docx"
        ).render_publishing_pilot_docx
        return cast(bytes, renderer(facts))


FORMAL_REPORT_REGISTRY: dict[int, FormalReportAdapter] = {
    adapter.service_number: adapter
    for adapter in (
        _Service1Adapter(),
        _Service2Adapter(),
        _Service3Adapter(),
        _Service4Adapter(),
    )
}

QUOTATION_FORMAL_REPORT_REGISTRY: dict[int, FormalReportAdapter] = {
    adapter.service_number: adapter
    for adapter in (
        _QuotationService1Adapter(),
        _OutboundService2Adapter(),
        _InboundService3Adapter(),
        _OfficialSiteService4Adapter(),
        _PublishingService5Adapter(),
    )
}


def _registry_for_request(request: FormalProductionRequest) -> dict[int, FormalReportAdapter]:
    return (
        QUOTATION_FORMAL_REPORT_REGISTRY
        if request.service_catalog_version == QUOTATION_SERVICE_CATALOG
        else FORMAL_REPORT_REGISTRY
    )


def _adapter_for(request: FormalProductionRequest, service: int) -> FormalReportAdapter:
    try:
        return _registry_for_request(request)[service]
    except KeyError as exc:
        raise FormalProductionInvalid("unsupported_service_for_catalog") from exc


def _service_code_for(request: FormalProductionRequest, service: int) -> str:
    """Resolve a stable code while retaining the legacy adapter test seam."""

    adapter = _adapter_for(request, service)
    service_code = getattr(adapter, "service_code", None)
    if isinstance(service_code, str) and service_code:
        return service_code
    codes = (
        QUOTATION_SERVICE_CODES
        if request.service_catalog_version == QUOTATION_SERVICE_CATALOG
        else LEGACY_SERVICE_CODES
    )
    try:
        return codes[service]
    except KeyError as exc:
        raise FormalProductionInvalid("unsupported_service_for_catalog") from exc


def _service1_report_title(request: FormalProductionRequest, facts: Mapping[str, Any]) -> str:
    if request.service_catalog_version == QUOTATION_SERVICE_CATALOG:
        return QUOTATION_SERVICE_TITLES[1]
    service1 = facts.get("service1")
    delivery = service1.get("delivery_v3") if isinstance(service1, Mapping) else None
    scope = delivery.get("scope") if isinstance(delivery, Mapping) else None
    scope_label = (
        str(scope.get("scope_label") or "本次三组已测业务场景")
        if isinstance(scope, Mapping)
        else "本次三组已测业务场景"
    )
    return f"{scope_label}品牌 GEO 推荐结果评测报告"


def _platform_project_brand_names(
    dsn: str, tenant_pub_id: str, project_pub_id: str
) -> frozenset[str]:
    """Load every canonical name and alias for a tenant-scoped platform project."""

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        tenant = connection.execute(
            "SELECT id FROM platform.tenant WHERE pub_id=%s", (tenant_pub_id,)
        ).fetchone()
        if tenant is None:
            raise FormalProductionNotFound("tenant_not_found")
        connection.execute(
            "SELECT set_config('app.tenant_id',%s,true), set_config('app.tenant_pub_id',%s,true)",
            (str(tenant["id"]), tenant_pub_id),
        )
        rows = connection.execute(
            """
            SELECT brand.name AS value
            FROM platform.project project
            JOIN platform.brand brand
              ON brand.project_id=project.id AND brand.tenant_id=project.tenant_id
            WHERE project.pub_id=%s
              AND project.tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid
            UNION
            SELECT alias.value
            FROM platform.project project
            JOIN platform.brand brand
              ON brand.project_id=project.id AND brand.tenant_id=project.tenant_id
            JOIN platform.brand_alias alias
              ON alias.brand_id=brand.id AND alias.tenant_id=project.tenant_id
            WHERE project.pub_id=%s
              AND project.tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid
            """,
            (project_pub_id, project_pub_id),
        ).fetchall()
    return frozenset(
        str(row["value"]).strip().casefold()
        for row in rows
        if isinstance(row.get("value"), str) and str(row["value"]).strip()
    )


def _assert_sop_brand_binding(
    request: FormalProductionRequest, sop_names: Sequence[object], *, dsn: str
) -> None:
    normalized_sop_names = {
        str(value).strip().casefold()
        for value in sop_names
        if isinstance(value, str) and value.strip()
    }
    platform_names = _platform_project_brand_names(
        dsn, request.tenant_pub_id, request.project_pub_id
    )
    if not normalized_sop_names or not platform_names.intersection(normalized_sop_names):
        raise FormalProductionInvalid("sop_project_brand_mismatch")


class FormalReportProductionService:
    def __init__(self, *, dsn: str, evidence: EvidenceService) -> None:
        self.dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
        self.evidence = evidence

    def enqueue(
        self,
        session: Session,
        *,
        tenant_pub_id: str,
        tenant_id: object,
        project_pub_id: str,
        services: Sequence[int],
        window: FormalWindow,
        document_status: str,
        candidate_group_strategy: str,
        before_window: FormalWindow | None,
        after_window: FormalWindow | None,
        idempotency_key: str,
        created_by_pub_id: str,
        task_queue: str,
        document_governance: Mapping[str, Any] | None = None,
        service_catalog_version: str = LEGACY_SERVICE_CATALOG,
        sop_project_pub_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        contract_catalog = (
            service_catalog_version if service_catalog_version != LEGACY_SERVICE_CATALOG else None
        )
        contract = request_contract(
            project_pub_id=project_pub_id,
            services=services,
            window=window,
            document_status=document_status,
            candidate_group_strategy=candidate_group_strategy,
            before_window=before_window,
            after_window=after_window,
            document_governance=document_governance,
            service_catalog_version=contract_catalog,
            sop_project_pub_id=sop_project_pub_id,
        )
        project_exists = session.execute(
            text(
                "SELECT 1 FROM platform.project "
                "WHERE tenant_id=:tenant_id AND pub_id=:project_pub_id"
            ),
            {"tenant_id": str(tenant_id), "project_pub_id": project_pub_id},
        ).scalar_one_or_none()
        if project_exists is None:
            raise FormalProductionNotFound("project_not_found")
        if sop_project_pub_id is not None:
            sop_project = (
                session.execute(
                    text(
                        "SELECT brand_standard_name,brand_profile FROM sop.project "
                        "WHERE tenant_pub_id=:tenant_pub_id AND pub_id=:sop_project_pub_id"
                    ),
                    {
                        "tenant_pub_id": tenant_pub_id,
                        "sop_project_pub_id": sop_project_pub_id,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if sop_project is None:
                raise FormalProductionNotFound("sop_project_not_found")
            platform_brand_rows = (
                session.execute(
                    text(
                        "SELECT brand.name AS value FROM platform.project project "
                        "JOIN platform.brand brand "
                        "ON brand.project_id=project.id AND brand.tenant_id=project.tenant_id "
                        "WHERE project.tenant_id=:tenant_id AND project.pub_id=:project_pub_id "
                        "UNION SELECT alias.value FROM platform.project project "
                        "JOIN platform.brand brand "
                        "ON brand.project_id=project.id AND brand.tenant_id=project.tenant_id "
                        "JOIN platform.brand_alias alias "
                        "ON alias.brand_id=brand.id AND alias.tenant_id=project.tenant_id "
                        "WHERE project.tenant_id=:tenant_id AND project.pub_id=:project_pub_id"
                    ),
                    {"tenant_id": str(tenant_id), "project_pub_id": project_pub_id},
                )
                .scalars()
                .all()
            )
            profile = sop_project.get("brand_profile")
            if isinstance(profile, str):
                try:
                    profile = json.loads(profile)
                except ValueError:
                    profile = {}
            raw_aliases = profile.get("aliases", []) if isinstance(profile, Mapping) else []
            aliases = raw_aliases if isinstance(raw_aliases, list) else []
            sop_brand_names = {
                str(value).strip().casefold()
                for value in [sop_project.get("brand_standard_name"), *aliases]
                if isinstance(value, str) and value.strip()
            }
            platform_brand_names = {
                str(value).strip().casefold()
                for value in platform_brand_rows
                if isinstance(value, str) and value.strip()
            }
            if not platform_brand_names.intersection(sop_brand_names):
                raise FormalProductionInvalid("sop_project_brand_mismatch")
        key_hash = sha256(idempotency_key.encode()).hexdigest()
        contract_hash = _canonical_hash(contract)
        production_pub_id = _stable_pub_id("frp", tenant_pub_id, key_hash)
        workflow_id = f"formal-report/{tenant_pub_id}/{production_pub_id}"
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
            {"scope": f"{tenant_pub_id}:formal-report-enqueue"},
        )
        replay = (
            session.execute(
                text(
                    """
                    SELECT * FROM reporting.formal_report_production
                    WHERE tenant_pub_id=:tenant_pub_id
                      AND idempotency_key_hash=:key_hash
                    """
                ),
                {"tenant_pub_id": tenant_pub_id, "key_hash": key_hash},
            )
            .mappings()
            .one_or_none()
        )
        if replay is not None:
            if replay["request_hash"] != contract_hash:
                raise FormalProductionConflict("idempotency_conflict")
            return self._public_row(dict(replay), outputs=[]), False
        active = session.execute(
            text(
                """
                SELECT 1 FROM reporting.formal_report_production
                WHERE tenant_pub_id=:tenant_pub_id AND status IN ('queued','running')
                LIMIT 1
                """
            ),
            {"tenant_pub_id": tenant_pub_id},
        ).scalar_one_or_none()
        if active is not None:
            raise FormalProductionConflict("formal_production_in_progress")
        inserted = session.execute(
            text(
                """
                INSERT INTO reporting.formal_report_production (
                  pub_id,tenant_pub_id,project_pub_id,services,window_start,window_end,
                  before_start,before_end,after_start,after_end,document_status,
                  candidate_group_strategy,idempotency_key_hash,request_hash,workflow_id,
                  created_by_pub_id,document_governance,service_catalog_version,
                  sop_project_pub_id
                ) VALUES (
                  :pub_id,:tenant_pub_id,:project_pub_id,:services,:window_start,:window_end,
                  :before_start,:before_end,:after_start,:after_end,:document_status,
                  :strategy,:key_hash,:request_hash,:workflow_id,:created_by,
                  CAST(:document_governance AS jsonb),:service_catalog_version,
                  :sop_project_pub_id
                )
                ON CONFLICT (tenant_pub_id,idempotency_key_hash) DO NOTHING
                RETURNING pub_id
                """
            ),
            {
                "pub_id": production_pub_id,
                "tenant_pub_id": tenant_pub_id,
                "project_pub_id": project_pub_id,
                "services": list(contract["services"]),
                "window_start": window.start,
                "window_end": window.end,
                "before_start": before_window.start if before_window else None,
                "before_end": before_window.end if before_window else None,
                "after_start": after_window.start if after_window else None,
                "after_end": after_window.end if after_window else None,
                "document_status": document_status,
                "strategy": candidate_group_strategy,
                "key_hash": key_hash,
                "request_hash": contract_hash,
                "workflow_id": workflow_id,
                "created_by": created_by_pub_id,
                "document_governance": json.dumps(
                    contract.get("document_governance") or {}, ensure_ascii=False
                ),
                "service_catalog_version": service_catalog_version,
                "sop_project_pub_id": sop_project_pub_id,
            },
        ).scalar_one_or_none()
        created = inserted is not None
        row = (
            session.execute(
                text(
                    """
                SELECT * FROM reporting.formal_report_production
                WHERE tenant_pub_id=:tenant_pub_id AND idempotency_key_hash=:key_hash
                """
                ),
                {"tenant_pub_id": tenant_pub_id, "key_hash": key_hash},
            )
            .mappings()
            .one()
        )
        if row["request_hash"] != contract_hash:
            raise FormalProductionConflict("idempotency_conflict")
        if created:
            # Keep the persistence service importable by Temporal activities.  The
            # outbox dispatcher imports the workflow definition, which imports those
            # activities back into this module.
            from geo_platform.collection.workflow_outbox import enqueue_workflow_start

            enqueue_workflow_start(
                session,
                tenant_pub_id=tenant_pub_id,
                workflow_type=FORMAL_WORKFLOW_TYPE,
                workflow_id=workflow_id,
                task_queue=task_queue,
                payload={
                    "tenant_pub_id": tenant_pub_id,
                    "formal_production_pub_id": production_pub_id,
                },
            )
        return self._public_row(dict(row), outputs=[]), created

    def get(self, *, tenant_pub_id: str, production_pub_id: str) -> dict[str, Any]:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT * FROM reporting.formal_report_production
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (tenant_pub_id, production_pub_id),
            ).fetchone()
            if row is None:
                raise FormalProductionNotFound("formal_production_not_found")
            outputs = self._output_rows(connection, tenant_pub_id, [production_pub_id])
        return self._public_row(dict(row), outputs=outputs)

    @staticmethod
    def _assert_completed_outputs(row: Mapping[str, Any]) -> None:
        expected_services = {int(value) for value in row.get("services", [])}
        outputs = row.get("outputs")
        if not isinstance(outputs, Sequence) or isinstance(outputs, str | bytes):
            raise FormalProductionIncomplete("formal_outputs_incomplete")
        observed_services: set[int] = set()
        for output in outputs:
            if (
                not isinstance(output, Mapping)
                or not isinstance(output.get("service_number"), int)
                or isinstance(output.get("service_number"), bool)
            ):
                raise FormalProductionIncomplete("formal_outputs_incomplete")
            observed_services.add(int(output["service_number"]))
        if observed_services != expected_services or len(outputs) != len(expected_services):
            raise FormalProductionIncomplete("formal_outputs_incomplete")
        for output in outputs:
            if not isinstance(output, Mapping) or not output.get("fact_snapshot_hash"):
                raise FormalProductionIncomplete("formal_outputs_incomplete")
            artifacts = output.get("artifacts")
            if not isinstance(artifacts, Sequence) or isinstance(artifacts, str | bytes):
                raise FormalProductionIncomplete("formal_artifacts_incomplete")
            formats = {
                str(artifact.get("format"))
                for artifact in artifacts
                if isinstance(artifact, Mapping)
            }
            expected_formats = set(
                _artifact_formats(
                    int(output["service_number"]), str(row.get("document_status") or "")
                )
            )
            if formats != expected_formats or len(artifacts) != len(expected_formats):
                raise FormalProductionIncomplete("formal_artifacts_incomplete")
            if any(
                not isinstance(artifact, Mapping)
                or not artifact.get("sha256")
                or not artifact.get("mime_type")
                or not isinstance(artifact.get("byte_size"), int)
                or isinstance(artifact.get("byte_size"), bool)
                or int(artifact["byte_size"]) <= 0
                for artifact in artifacts
            ):
                raise FormalProductionIncomplete("formal_artifacts_incomplete")

    def list_productions(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str | None,
        cursor: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 100:
            raise FormalProductionInvalid("invalid_limit")
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            cursor_created_at: datetime | None = None
            if cursor is not None:
                cursor_row = connection.execute(
                    """
                    SELECT created_at FROM reporting.formal_report_production
                    WHERE tenant_pub_id=%s AND pub_id=%s
                      AND (%s::text IS NULL OR project_pub_id=%s)
                    """,
                    (tenant_pub_id, cursor, project_pub_id, project_pub_id),
                ).fetchone()
                if cursor_row is None:
                    raise FormalProductionInvalid("invalid_cursor")
                cursor_created_at = cursor_row["created_at"]
            rows = connection.execute(
                """
                SELECT * FROM reporting.formal_report_production
                WHERE tenant_pub_id=%s
                  AND (%s::text IS NULL OR project_pub_id=%s)
                  AND (
                    %s::timestamptz IS NULL
                    OR (created_at,pub_id) < (%s::timestamptz,%s::text)
                  )
                ORDER BY created_at DESC,pub_id DESC LIMIT %s
                """,
                (
                    tenant_pub_id,
                    project_pub_id,
                    project_pub_id,
                    cursor_created_at,
                    cursor_created_at,
                    cursor,
                    limit + 1,
                ),
            ).fetchall()
            outputs = self._output_rows(
                connection, tenant_pub_id, [str(row["pub_id"]) for row in rows]
            )
        return [self._public_row(dict(row), outputs=outputs) for row in rows]

    def generate_offline(self, request: FormalProductionRequest) -> GeneratedFormalBundle:
        facts, fact_snapshot_hash = self._build_fact_bundle(request)
        artifacts = self._render_artifacts(request, facts)
        return GeneratedFormalBundle(
            facts=facts,
            artifacts=artifacts,
            fact_snapshot_hash=fact_snapshot_hash,
        )

    def produce(self, *, tenant_pub_id: str, production_pub_id: str) -> dict[str, Any]:
        current = self.get(
            tenant_pub_id=tenant_pub_id,
            production_pub_id=production_pub_id,
        )
        if current["status"] in {"awaiting_review", "signed"}:
            self._assert_completed_outputs(current)
            return current
        request = self._request(tenant_pub_id, production_pub_id)
        report_runtime_preflight()
        facts, _ = self._freeze_facts(request)
        artifacts = self._freeze_rendered_artifacts(request, facts)
        return self._persist_bundle(request, facts, artifacts)

    def mark_failed(
        self, *, tenant_pub_id: str, production_pub_id: str, error_code: str
    ) -> dict[str, Any]:
        safe_code = (
            error_code
            if error_code
            in {
                "production_failed",
                "formal_evidence_requirements_not_met",
                "formal_fact_volume_exceeded",
                "libreoffice_dependency_missing",
                "workflow_interrupted",
                "changes_requested",
            }
            else "production_failed"
        )
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                UPDATE reporting.formal_report_production
                SET status='failed',error_code=%s,updated_at=now()
                WHERE tenant_pub_id=%s AND pub_id=%s
                  AND status NOT IN ('awaiting_review','signed')
                RETURNING *
                """,
                (safe_code, tenant_pub_id, production_pub_id),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    """SELECT * FROM reporting.formal_report_production
                       WHERE tenant_pub_id=%s AND pub_id=%s""",
                    (tenant_pub_id, production_pub_id),
                ).fetchone()
            if row is None:
                raise FormalProductionNotFound("formal_production_not_found")
        return self._public_row(dict(row), outputs=[])

    def _prepare_signed_bundle(
        self,
        *,
        tenant_pub_id: str,
        production_pub_id: str,
        approver_pub_id: str,
    ) -> tuple[FormalProductionRequest, dict[int, dict[str, Any]], dict[int, dict[str, bytes]]]:
        """Re-render an approved candidate with signed chrome and a new immutable version."""

        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            row = connection.execute(
                """SELECT * FROM reporting.formal_report_production
                   WHERE tenant_pub_id=%s AND pub_id=%s""",
                (tenant_pub_id, production_pub_id),
            ).fetchone()
        if row is None:
            raise FormalProductionNotFound("formal_production_not_found")
        if row["status"] == "signed" and row["document_status"] == "approved_signed":
            raise FormalProductionConflict("formal_production_already_signed")
        if row["document_status"] != "delivery_candidate":
            raise FormalProductionConflict("delivery_candidate_required")
        candidate_request = self._request_from_row(row)
        candidate_facts, _ = self._freeze_facts(candidate_request)
        signed_at = datetime.now(UTC)
        governance = {
            **dict(candidate_request.document_governance or {}),
            "approved_by": approver_pub_id,
            "approved_date": signed_at.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat(),
        }
        signed_request = replace(
            candidate_request,
            document_status="approved_signed",
            frozen_at=signed_at,
            document_governance=governance,
        )
        signed_facts = copy.deepcopy(candidate_facts)
        for service, value in signed_facts.items():
            value["document_status"] = "approved_signed"
            value["document_governance"] = governance
            ready, reasons = formal_evidence_gate(service, value)
            if not ready:
                raise FormalProductionInvalid("approval_data_gate_drifted:" + ",".join(reasons))
            value["formal_evidence_gate"] = {"status": "ready", "reasons": []}
        return signed_request, signed_facts, self._render_artifacts(signed_request, signed_facts)

    def _persist_signed_service(
        self,
        *,
        connection: psycopg.Connection[Any],
        candidate_version_pub_id: str,
        report_pub_id: str,
        service: int,
        request: FormalProductionRequest,
        facts: Mapping[str, Any],
        artifacts: Mapping[str, bytes],
    ) -> str:
        """Persist the post-approval rendering as version 2 and point delivery at it."""

        expected_formats = set(_artifact_formats(service, "approved_signed"))
        if set(artifacts) != expected_formats:
            raise FormalProductionIncomplete("signed_artifacts_incomplete")
        frozen = _freeze_service_fact(request, service, facts)
        customer_facts = customer_fact_snapshot(facts)
        if not isinstance(customer_facts, dict):
            raise FormalProductionIncomplete("customer_fact_snapshot_invalid")
        version_pub_id = _stable_pub_id(
            "rptv", request.tenant_pub_id, request.pub_id, service, "approved-signed"
        )
        filters = dict(frozen.filters)
        connection.execute(
            """
            INSERT INTO reporting.report_version (
              pub_id,tenant_pub_id,report_pub_id,version_number,window_start,window_end,
              filters,filter_hash,metric_version,scorer_version,fact_snapshot_hash,
              status,ai_draft_hash,human_edit_hash,created_by_pub_id
            ) VALUES (%s,%s,%s,2,%s,%s,%s,%s,%s,%s,%s,'published',%s,%s,%s)
            """,
            (
                version_pub_id,
                request.tenant_pub_id,
                report_pub_id,
                frozen.window_start,
                frozen.window_end,
                json.dumps(filters, ensure_ascii=False),
                frozen.filter_hash,
                frozen.metric_version,
                frozen.scorer_version,
                frozen.fact_snapshot_hash,
                sha256(artifacts["docx"]).hexdigest(),
                sha256(artifacts["pdf"]).hexdigest(),
                str((facts.get("document_governance") or {}).get("approved_by")),
            ),
        )
        component = {
            "service_number": service,
            "service_code": _service_code_for(request, service),
            "title": _adapter_for(request, service).title,
            "document_status": "approved_signed",
            "fact_snapshot_hash": frozen.fact_snapshot_hash,
        }
        connection.execute(
            """
            INSERT INTO reporting.report_component (
              pub_id,tenant_pub_id,report_version_pub_id,component_type,ordinal,payload,source
            ) VALUES (%s,%s,%s,'section',0,%s,'system')
            """,
            (
                _stable_pub_id("rptc", request.tenant_pub_id, version_pub_id),
                request.tenant_pub_id,
                version_pub_id,
                json.dumps(component, ensure_ascii=False),
            ),
        )
        fact_payload = _canonical_json(customer_facts)
        connection.execute(
            """
            INSERT INTO reporting.report_frozen_fact (
              pub_id,tenant_pub_id,report_version_pub_id,ordinal,payload,payload_hash
            ) VALUES (%s,%s,%s,0,%s,%s)
            """,
            (
                _stable_pub_id("rptf", request.tenant_pub_id, version_pub_id),
                request.tenant_pub_id,
                version_pub_id,
                fact_payload,
                sha256(fact_payload.encode()).hexdigest(),
            ),
        )
        for descriptor in evidence_descriptors(facts):
            connection.execute(
                """
                INSERT INTO reporting.report_evidence_reference (
                  pub_id,tenant_pub_id,report_version_pub_id,evidence_pub_id,purpose
                ) VALUES (%s,%s,%s,%s,'formal_report_frozen_evidence')
                """,
                (
                    _stable_pub_id(
                        "rptev", request.tenant_pub_id, version_pub_id, descriptor["pub_id"]
                    ),
                    request.tenant_pub_id,
                    version_pub_id,
                    descriptor["pub_id"],
                ),
            )
        provenance = RedactedProvenance(
            platform_account_pub_id=None,
            browser_profile_version_pub_id=None,
            session_event_pub_id=None,
            channel=CaptureChannel.API,
            authorization_scope=("report:approve",),
            adapter_version="formal-report-signing-v2",
            capture_time=request.frozen_at,
            access_class=AccessClass.CUSTOMER_PRIVATE,
        )
        for format_name in _artifact_formats(service, "approved_signed"):
            evidence_pub_id = _stable_pub_id(
                "evd",
                request.tenant_pub_id,
                request.pub_id,
                service,
                "approved-signed",
                format_name,
            )
            captured = self.evidence.capture(
                evidence_pub_id=evidence_pub_id,
                tenant_pub_id=request.tenant_pub_id,
                project_pub_id=request.project_pub_id,
                kind=f"formal_report_service_{service}_approved_signed_{format_name}",
                payload=artifacts[format_name],
                mime_type=_MIME_TYPES[format_name],
                source_url=None,
                provenance=provenance,
                db_connection=connection,
            )
            connection.execute(
                """
                INSERT INTO reporting.report_artifact (
                  pub_id,tenant_pub_id,report_version_pub_id,format,evidence_pub_id
                ) VALUES (%s,%s,%s,%s,%s)
                """,
                (
                    _stable_pub_id("rpta", request.tenant_pub_id, version_pub_id, format_name),
                    request.tenant_pub_id,
                    version_pub_id,
                    format_name,
                    captured.metadata_pub_id or evidence_pub_id,
                ),
            )
        connection.execute(
            """UPDATE reporting.report_version SET status='superseded'
               WHERE tenant_pub_id=%s AND pub_id=%s""",
            (request.tenant_pub_id, candidate_version_pub_id),
        )
        connection.execute(
            """UPDATE reporting.formal_report_output
               SET report_version_pub_id=%s,fact_snapshot_hash=%s
               WHERE tenant_pub_id=%s AND production_pub_id=%s AND service_number=%s""",
            (
                version_pub_id,
                frozen.fact_snapshot_hash,
                request.tenant_pub_id,
                request.pub_id,
                service,
            ),
        )
        return version_pub_id

    def finalize(
        self,
        *,
        tenant_pub_id: str,
        production_pub_id: str,
        reviewer_pub_id: str,
        approved: bool,
        rationale: str,
        workflow_operation_id: str,
    ) -> dict[str, Any]:
        expected_review_hash = formal_review_contract_hash(
            approved=approved,
            reviewer_pub_id=reviewer_pub_id,
            rationale=rationale,
        )
        decision = "approved" if approved else "changes_requested"
        current = self.get(
            tenant_pub_id=tenant_pub_id,
            production_pub_id=production_pub_id,
        )
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            review_contract = connection.execute(
                """
                SELECT status,document_status,review_request_hash
                FROM reporting.formal_report_production
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (tenant_pub_id, production_pub_id),
            ).fetchone()
        if review_contract is None:
            raise FormalProductionNotFound("formal_production_not_found")
        claimed_review_hash = review_contract["review_request_hash"]
        if not isinstance(claimed_review_hash, str) or not hmac.compare_digest(
            claimed_review_hash, expected_review_hash
        ):
            raise FormalProductionConflict("formal_review_contract_mismatch")
        if (
            approved
            and review_contract["status"] != "signed"
            and review_contract["document_status"] != "delivery_candidate"
        ):
            raise FormalProductionConflict("delivery_candidate_required")
        signed_bundle = None
        if approved and current["status"] != "signed":
            signed_bundle = self._prepare_signed_bundle(
                tenant_pub_id=tenant_pub_id,
                production_pub_id=production_pub_id,
                approver_pub_id=reviewer_pub_id,
            )
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            production = connection.execute(
                """
                SELECT * FROM reporting.formal_report_production
                WHERE tenant_pub_id=%s AND pub_id=%s FOR UPDATE
                """,
                (tenant_pub_id, production_pub_id),
            ).fetchone()
            if production is None:
                raise FormalProductionNotFound("formal_production_not_found")
            claimed_review_hash = production["review_request_hash"]
            if not isinstance(claimed_review_hash, str) or not hmac.compare_digest(
                claimed_review_hash, expected_review_hash
            ):
                raise FormalProductionConflict("formal_review_contract_mismatch")
            if (
                approved
                and production["status"] != "signed"
                and production["document_status"] != "delivery_candidate"
            ):
                raise FormalProductionConflict("delivery_candidate_required")
            outputs = connection.execute(
                """
                SELECT service_number,report_pub_id,report_version_pub_id
                FROM reporting.formal_report_output
                WHERE tenant_pub_id=%s AND production_pub_id=%s
                ORDER BY service_number
                """,
                (tenant_pub_id, production_pub_id),
            ).fetchall()
            if {int(row["service_number"]) for row in outputs} != {
                int(value) for value in production["services"]
            } or len(outputs) != len(production["services"]):
                raise FormalProductionIncomplete("formal_outputs_incomplete")
            completed = self._public_row(
                dict(production),
                outputs=self._output_rows(
                    connection,
                    tenant_pub_id,
                    [production_pub_id],
                ),
            )
            self._assert_completed_outputs(completed)
            terminal_replay = (production["status"] == "signed" and approved) or (
                production["status"] == "failed"
                and production["error_code"] == "changes_requested"
                and not approved
            )
            if terminal_replay:
                for output in outputs:
                    operation = f"{workflow_operation_id}/service-{output['service_number']}"
                    persisted = connection.execute(
                        """
                        SELECT report_version_pub_id,reviewer_pub_id,decision,rationale
                        FROM reporting.report_review
                        WHERE tenant_pub_id=%s AND workflow_operation_id=%s
                        """,
                        (tenant_pub_id, operation),
                    ).fetchone()
                    observed = (
                        (
                            persisted["report_version_pub_id"],
                            persisted["reviewer_pub_id"],
                            persisted["decision"],
                            persisted["rationale"],
                        )
                        if persisted is not None
                        else None
                    )
                    expected = (
                        output["report_version_pub_id"],
                        reviewer_pub_id,
                        decision,
                        rationale,
                    )
                    if observed != expected:
                        raise FormalProductionConflict("formal_review_replay_drift")
                return self._public_row(dict(production), outputs=[])
            if production["status"] != "awaiting_review":
                raise FormalProductionConflict("formal_production_not_reviewable")
            for output in outputs:
                operation = f"{workflow_operation_id}/service-{output['service_number']}"
                review_version_pub_id = str(output["report_version_pub_id"])
                if approved:
                    if signed_bundle is None:
                        raise FormalProductionIncomplete("signed_bundle_missing")
                    signed_request, signed_facts, signed_artifacts = signed_bundle
                    service_number = int(output["service_number"])
                    review_version_pub_id = self._persist_signed_service(
                        connection=connection,
                        candidate_version_pub_id=str(output["report_version_pub_id"]),
                        report_pub_id=str(output["report_pub_id"]),
                        service=service_number,
                        request=signed_request,
                        facts=signed_facts[service_number],
                        artifacts=signed_artifacts[service_number],
                    )
                persisted = connection.execute(
                    """
                    INSERT INTO reporting.report_review (
                      pub_id,tenant_pub_id,report_version_pub_id,reviewer_pub_id,
                      decision,rationale,workflow_operation_id
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (tenant_pub_id,workflow_operation_id)
                      WHERE workflow_operation_id IS NOT NULL
                    DO UPDATE SET pub_id=reporting.report_review.pub_id
                    RETURNING report_version_pub_id,reviewer_pub_id,decision,rationale
                    """,
                    (
                        _stable_pub_id("rvw", tenant_pub_id, operation),
                        tenant_pub_id,
                        review_version_pub_id,
                        reviewer_pub_id,
                        decision,
                        rationale,
                        operation,
                    ),
                ).fetchone()
                expected = (review_version_pub_id, reviewer_pub_id, decision, rationale)
                observed = (
                    (
                        persisted["report_version_pub_id"],
                        persisted["reviewer_pub_id"],
                        persisted["decision"],
                        persisted["rationale"],
                    )
                    if persisted is not None
                    else None
                )
                if observed != expected:
                    raise FormalProductionConflict("formal_review_replay_drift")
                connection.execute(
                    """
                    UPDATE reporting.report
                    SET state=%s,updated_at=now()
                    WHERE tenant_pub_id=%s AND pub_id=%s
                    """,
                    ("published" if approved else "review", tenant_pub_id, output["report_pub_id"]),
                )
                connection.execute(
                    """
                    INSERT INTO reporting.report_event (
                      pub_id,tenant_pub_id,report_pub_id,report_version_pub_id,
                      event_type,actor_pub_id,data
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        _stable_pub_id("evt", tenant_pub_id, operation),
                        tenant_pub_id,
                        output["report_pub_id"],
                        review_version_pub_id,
                        "published" if approved else "changes_requested",
                        reviewer_pub_id,
                        json.dumps({"formal_production": True}),
                    ),
                )
            final_status = "signed" if approved else "failed"
            error_code = None if approved else "changes_requested"
            signed_governance = (
                dict(signed_bundle[0].document_governance or {}) if signed_bundle else {}
            )
            signed_fact_bundle_json = None
            signed_fact_bundle_hash = None
            signed_fact_snapshot_hash = None
            if signed_bundle:
                signed_fact_values = signed_bundle[1]
                signed_fact_bundle = {
                    "schema_version": "formal-report-fact-bundle-v1",
                    "services": {
                        str(service): value for service, value in signed_fact_values.items()
                    },
                }
                signed_fact_bundle_json = _canonical_json(signed_fact_bundle)
                signed_fact_bundle_hash = sha256(signed_fact_bundle_json.encode()).hexdigest()
                signed_fact_snapshot_hash = _canonical_hash(
                    {
                        str(service): customer_fact_snapshot(value)
                        for service, value in signed_fact_values.items()
                    }
                )
            updated = connection.execute(
                """
                UPDATE reporting.formal_report_production
                SET status=%s,error_code=%s,
                    document_status=CASE WHEN %s THEN 'approved_signed' ELSE document_status END,
                    document_governance=CASE
                      WHEN %s THEN %s::jsonb ELSE document_governance
                    END,
                    fact_bundle=CASE WHEN %s THEN %s::jsonb ELSE fact_bundle END,
                    fact_bundle_hash=CASE WHEN %s THEN %s ELSE fact_bundle_hash END,
                    fact_snapshot_hash=CASE WHEN %s THEN %s ELSE fact_snapshot_hash END,
                    rendered_bundle=CASE WHEN %s THEN NULL ELSE rendered_bundle END,
                    artifact_snapshot_hash=CASE WHEN %s THEN NULL ELSE artifact_snapshot_hash END,
                    updated_at=now()
                WHERE tenant_pub_id=%s AND pub_id=%s RETURNING *
                """,
                (
                    final_status,
                    error_code,
                    approved,
                    approved,
                    json.dumps(signed_governance, ensure_ascii=False),
                    approved,
                    signed_fact_bundle_json,
                    approved,
                    signed_fact_bundle_hash,
                    approved,
                    signed_fact_snapshot_hash,
                    approved,
                    approved,
                    tenant_pub_id,
                    production_pub_id,
                ),
            ).fetchone()
            assert updated is not None
        return self._public_row(dict(updated), outputs=[])

    def artifact(
        self,
        *,
        tenant_pub_id: str,
        production_pub_id: str,
        service_number: int,
        format_name: str,
        customer_recipient_pub_id: str | None = None,
    ) -> tuple[bytes, str, str]:
        if service_number not in {1, 2, 3, 4, 5} or format_name not in _MIME_TYPES:
            raise FormalProductionNotFound("formal_artifact_not_found")
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT asset.object_key,asset.sha256,asset.mime_type
                FROM reporting.formal_report_production production
                JOIN reporting.formal_report_output output
                  ON output.tenant_pub_id=production.tenant_pub_id
                 AND output.production_pub_id=production.pub_id
                JOIN reporting.report_artifact artifact
                  ON artifact.tenant_pub_id=output.tenant_pub_id
                 AND artifact.report_version_pub_id=output.report_version_pub_id
                JOIN reporting.report_version version
                  ON version.tenant_pub_id=output.tenant_pub_id
                 AND version.pub_id=output.report_version_pub_id
                JOIN evidence.evidence_asset asset
                  ON asset.tenant_pub_id=artifact.tenant_pub_id
                 AND asset.pub_id=artifact.evidence_pub_id
                JOIN reporting.report report
                  ON report.tenant_pub_id=output.tenant_pub_id
                 AND report.pub_id=output.report_pub_id
                 AND version.report_pub_id=report.pub_id
                WHERE production.tenant_pub_id=%s AND production.pub_id=%s
                  AND output.service_number=%s AND artifact.format=%s
                  AND asset.deleted_at IS NULL
                  AND (
                    (
                      %s::text IS NULL
                      AND production.status IN ('awaiting_review','signed','failed')
                    )
                    OR (
                      %s::text IS NOT NULL
                      AND production.status='signed' AND report.state='published'
                      AND EXISTS (
                        SELECT 1 FROM reporting.report_delivery delivery
                        WHERE delivery.tenant_pub_id=report.tenant_pub_id
                          AND delivery.report_pub_id=report.pub_id
                          AND delivery.recipient_pub_id=%s
                      )
                    )
                  )
                """,
                (
                    tenant_pub_id,
                    production_pub_id,
                    service_number,
                    format_name,
                    customer_recipient_pub_id,
                    customer_recipient_pub_id,
                    customer_recipient_pub_id,
                ),
            ).fetchone()
        if row is None:
            raise FormalProductionNotFound("formal_artifact_not_found")
        payload = self.evidence.store.get_verified(str(row["object_key"]), str(row["sha256"]))
        return payload, str(row["mime_type"]), str(row["sha256"])

    def artifact_filename(
        self,
        *,
        tenant_pub_id: str,
        production_pub_id: str,
        service_number: int,
        format_name: str,
    ) -> str:
        """Return the governed customer/service/version/date delivery filename."""

        if service_number not in {1, 2, 3, 4, 5} or format_name not in _MIME_TYPES:
            raise FormalProductionNotFound("formal_artifact_not_found")
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT COALESCE(
                         jsonb_extract_path_text(
                           production.fact_bundle,'services',%s::text,'project_name'
                         ),
                         jsonb_extract_path_text(
                           production.fact_bundle,'services',%s::text,'account_name'
                         ),
                         '客户项目'
                       ) AS project_name,
                       production.document_status,
                       production.document_governance,production.frozen_at
                FROM reporting.formal_report_production production
                WHERE production.tenant_pub_id=%s AND production.pub_id=%s
                  AND %s=ANY(production.services)
                """,
                (
                    service_number,
                    service_number,
                    tenant_pub_id,
                    production_pub_id,
                    service_number,
                ),
            ).fetchone()
        if row is None:
            raise FormalProductionNotFound("formal_artifact_not_found")
        governance = row["document_governance"]
        governance = governance if isinstance(governance, Mapping) else {}
        version = str(governance.get("version") or "V1.0")
        governed_date = (
            governance.get("approved_date")
            if row["document_status"] == "approved_signed"
            else governance.get("prepared_date")
        )
        try:
            stamp = date.fromisoformat(str(governed_date)).strftime("%Y%m%d")
        except ValueError:
            stamp = row["frozen_at"].astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
        label = release_state_label(str(row["document_status"]))

        def safe(value: object) -> str:
            return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", str(value)).strip("_")

        safe_version = re.sub(r"[^0-9A-Za-z.-]+", "_", version).strip("_.")
        return (
            f"{safe(row['project_name'])}_服务{service_number}_{safe_version}_"
            f"{safe(label)}_{stamp}.{format_name if format_name != 'manifest' else 'json'}"
        )

    def _request(self, tenant_pub_id: str, production_pub_id: str) -> FormalProductionRequest:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                UPDATE reporting.formal_report_production
                SET status=CASE WHEN status='queued' THEN 'running' ELSE status END,
                    error_code=CASE WHEN status='queued' THEN NULL ELSE error_code END,
                    updated_at=now()
                WHERE tenant_pub_id=%s AND pub_id=%s
                RETURNING *
                """,
                (tenant_pub_id, production_pub_id),
            ).fetchone()
        if row is None:
            raise FormalProductionNotFound("formal_production_not_found")
        if row["status"] == "failed":
            raise FormalProductionConflict("formal_production_failed")
        return self._request_from_row(row)

    def _request_from_row(self, row: Mapping[str, Any]) -> FormalProductionRequest:
        before = (
            FormalWindow(row["before_start"], row["before_end"])
            if row.get("before_start") is not None
            else None
        )
        after = (
            FormalWindow(row["after_start"], row["after_end"])
            if row.get("after_start") is not None
            else None
        )
        raw_services = tuple(int(value) for value in row["services"])
        window = FormalWindow(row["window_start"], row["window_end"])
        document_status = str(row["document_status"])
        candidate_group_strategy = str(row["candidate_group_strategy"])
        raw_governance = row.get("document_governance")
        governance = (
            raw_governance if isinstance(raw_governance, Mapping) and raw_governance else None
        )
        service_catalog_version = str(row.get("service_catalog_version") or LEGACY_SERVICE_CATALOG)
        sop_project_pub_id = str(row.get("sop_project_pub_id") or "").strip() or None
        try:
            contract = request_contract(
                project_pub_id=str(row["project_pub_id"]),
                services=raw_services,
                window=window,
                document_status=document_status,
                candidate_group_strategy=candidate_group_strategy,
                before_window=before,
                after_window=after,
                document_governance=governance,
                service_catalog_version=(
                    service_catalog_version
                    if service_catalog_version != LEGACY_SERVICE_CATALOG
                    else None
                ),
                sop_project_pub_id=sop_project_pub_id,
            )
        except FormalProductionInvalid as exc:
            raise FormalProductionIncomplete("formal_request_invalid") from exc
        request_hash = str(row["request_hash"])
        if _canonical_hash(contract) != request_hash:
            raise FormalProductionIncomplete("formal_request_integrity_failed")
        return FormalProductionRequest(
            pub_id=str(row["pub_id"]),
            tenant_pub_id=str(row["tenant_pub_id"]),
            project_pub_id=str(row["project_pub_id"]),
            services=tuple(int(value) for value in contract["services"]),
            window=window,
            document_status=document_status,
            candidate_group_strategy=candidate_group_strategy,
            frozen_at=row["frozen_at"],
            created_by_pub_id=str(row["created_by_pub_id"]),
            request_hash=request_hash,
            before_window=before,
            after_window=after,
            document_governance=governance,
            service_catalog_version=service_catalog_version,
            sop_project_pub_id=sop_project_pub_id,
        )

    def _build_fact_bundle(
        self, request: FormalProductionRequest
    ) -> tuple[dict[int, dict[str, Any]], str]:
        context = FormalBuildContext(
            dsn=self.dsn,
            request=request,
            blob_loader=self.evidence.store.get_verified,
        )
        try:
            facts = {
                service: _adapter_for(request, service).build(context)
                for service in request.services
            }
        except ValueError as exc:
            if str(exc) == "formal_answer_volume_exceeded":
                raise FormalProductionInvalid("formal_fact_volume_exceeded") from exc
            raise
        for service, value in facts.items():
            if request.service_catalog_version == QUOTATION_SERVICE_CATALOG:
                value["service_catalog_version"] = request.service_catalog_version
                value["service_code"] = _service_code_for(request, service)
            value["document_status"] = request.document_status
            value["document_governance"] = {
                "version": "V1.0",
                "prepared_by": request.created_by_pub_id,
                "reviewed_by": None,
                "approved_by": None,
                "prepared_date": request.frozen_at.date().isoformat(),
                "reviewed_date": None,
                "approved_date": None,
                **dict(request.document_governance or {}),
            }
            ready, reasons = formal_evidence_gate(service, value)
            value["formal_evidence_gate"] = {
                "status": "ready" if ready else "insufficient",
                "reasons": list(reasons),
            }
        if request.document_status in {"formal", "delivery_candidate"} and any(
            value["formal_evidence_gate"]["status"] != "ready" for value in facts.values()
        ):
            raise FormalProductionInvalid("formal_evidence_requirements_not_met")
        customer_facts = {
            str(service): customer_fact_snapshot(value) for service, value in facts.items()
        }
        assert_customer_report_safe(
            [value for value in customer_facts.values() if isinstance(value, dict)]
        )
        return facts, _canonical_hash(customer_facts)

    def _freeze_facts(
        self, request: FormalProductionRequest
    ) -> tuple[dict[int, dict[str, Any]], str]:
        def validate_bundle(bundle: object, snapshot_hash: object) -> dict[int, dict[str, Any]]:
            if not isinstance(bundle, Mapping) or bundle.get("schema_version") != (
                "formal-report-fact-bundle-v1"
            ):
                raise FormalProductionIncomplete("frozen_fact_bundle_invalid")
            raw_services = bundle.get("services")
            if not isinstance(raw_services, Mapping):
                raise FormalProductionIncomplete("frozen_fact_bundle_invalid")
            try:
                parsed = {int(key): value for key, value in raw_services.items()}
            except (TypeError, ValueError) as exc:
                raise FormalProductionIncomplete("frozen_fact_bundle_invalid") from exc
            if set(parsed) != set(request.services) or any(
                not isinstance(value, dict) for value in parsed.values()
            ):
                raise FormalProductionIncomplete("frozen_fact_bundle_invalid")
            if any(
                value.get("document_status") != request.document_status for value in parsed.values()
            ):
                raise FormalProductionIncomplete("frozen_fact_bundle_request_drifted")
            customer_facts = {
                str(service): customer_fact_snapshot(value) for service, value in parsed.items()
            }
            if _canonical_hash(customer_facts) != snapshot_hash:
                raise FormalProductionIncomplete("frozen_fact_snapshot_integrity_failed")
            return cast(dict[int, dict[str, Any]], parsed)

        with tenant_connection(self.dsn, request.tenant_pub_id, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT fact_bundle,fact_bundle_hash,fact_snapshot_hash
                FROM reporting.formal_report_production
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (request.tenant_pub_id, request.pub_id),
            ).fetchone()
        if row and row["fact_bundle"] is not None:
            serialized = _canonical_json(row["fact_bundle"])
            if sha256(serialized.encode()).hexdigest() != row["fact_bundle_hash"]:
                raise FormalProductionIncomplete("frozen_fact_bundle_integrity_failed")
            return validate_bundle(row["fact_bundle"], row["fact_snapshot_hash"]), str(
                row["fact_snapshot_hash"]
            )
        facts, fact_snapshot_hash = self._build_fact_bundle(request)
        bundle = {
            "schema_version": "formal-report-fact-bundle-v1",
            "services": {str(service): value for service, value in facts.items()},
        }
        bundle_json = _canonical_json(bundle)
        bundle_hash = sha256(bundle_json.encode()).hexdigest()
        with tenant_connection(self.dsn, request.tenant_pub_id, row_factory=dict_row) as connection:
            persisted = connection.execute(
                """
                UPDATE reporting.formal_report_production
                SET fact_bundle=%s,fact_bundle_hash=%s,fact_snapshot_hash=%s,updated_at=now()
                WHERE tenant_pub_id=%s AND pub_id=%s AND fact_bundle IS NULL
                RETURNING fact_bundle,fact_bundle_hash,fact_snapshot_hash
                """,
                (
                    bundle_json,
                    bundle_hash,
                    fact_snapshot_hash,
                    request.tenant_pub_id,
                    request.pub_id,
                ),
            ).fetchone()
            if persisted is None:
                persisted = connection.execute(
                    """
                    SELECT fact_bundle,fact_bundle_hash,fact_snapshot_hash
                    FROM reporting.formal_report_production
                    WHERE tenant_pub_id=%s AND pub_id=%s
                    """,
                    (request.tenant_pub_id, request.pub_id),
                ).fetchone()
        if persisted is None or persisted["fact_bundle"] is None:
            raise FormalProductionIncomplete("frozen_fact_bundle_missing")
        persisted_json = _canonical_json(persisted["fact_bundle"])
        if sha256(persisted_json.encode()).hexdigest() != persisted["fact_bundle_hash"]:
            raise FormalProductionIncomplete("frozen_fact_bundle_integrity_failed")
        return validate_bundle(persisted["fact_bundle"], persisted["fact_snapshot_hash"]), str(
            persisted["fact_snapshot_hash"]
        )

    def _render_artifacts(
        self,
        request: FormalProductionRequest,
        facts: dict[int, dict[str, Any]],
    ) -> dict[int, dict[str, bytes]]:
        rendered: dict[int, dict[str, bytes]] = {}
        for service in request.services:
            initial_docx = _adapter_for(request, service).render(
                facts[service], blob_loader=self.evidence.store.get_verified
            )
            docx, pdf = refresh_docx_and_export_pdf(initial_docx)
            frozen = _freeze_service_fact(request, service, facts[service])
            extra_artifacts: dict[str, bytes] = {}
            publication_qa: dict[str, Any] | None = None
            reexport_qa: dict[str, Any] | None = None
            if service == 1 and request.document_status in {
                "internal_review",
                "delivery_candidate",
                "approved_signed",
            }:
                extra_artifacts = render_service1_sidecars(
                    facts[service], blob_loader=self.evidence.store.get_verified
                )
                report_title = _service1_report_title(request, facts[service])
                publication_qa = inspect_publication(
                    docx=docx,
                    pdf=pdf,
                    expected_title=report_title,
                    expected_status_label=release_state_label(request.document_status),
                    expected_urls=displayed_service1_urls(facts[service]),
                )
                second_docx, second_pdf = refresh_docx_and_export_pdf(docx)
                reexport_qa = compare_reexport(
                    first_docx=docx,
                    first_pdf=pdf,
                    second_docx=second_docx,
                    second_pdf=second_pdf,
                )
                if request.document_status in {"delivery_candidate", "approved_signed"} and (
                    publication_qa["status"] != "passed" or reexport_qa["status"] != "passed"
                ):
                    raise FormalProductionInvalid("publication_quality_gate_failed")
            payloads = {"docx": docx, "pdf": pdf, **extra_artifacts}
            manifest = {
                "schema_version": (
                    "formal-report-manifest-v2"
                    if request.document_status
                    in {"internal_review", "delivery_candidate", "approved_signed"}
                    and service == 1
                    else "formal-report-manifest-v1"
                ),
                "service_number": service,
                "service_code": _service_code_for(request, service),
                "service_catalog_version": request.service_catalog_version,
                "document_status": request.document_status,
                "release_status_label": release_state_label(request.document_status),
                "document_governance": facts[service].get("document_governance"),
                "window": {
                    "start": request.window.start.isoformat(),
                    "end": request.window.end.isoformat(),
                },
                "generated_at": request.frozen_at.astimezone(UTC).isoformat(),
                "fact_snapshot_hash": frozen.fact_snapshot_hash,
                "data_gate": facts[service].get("formal_evidence_gate"),
                "publication_qa": publication_qa,
                "reexport_qa": reexport_qa,
                "artifacts": {
                    name: {
                        "sha256": sha256(payload).hexdigest(),
                        "byte_size": len(payload),
                        **(
                            {"pages": publication_qa["pdf"]["pages"]}
                            if name == "pdf" and publication_qa
                            else {}
                        ),
                    }
                    for name, payload in payloads.items()
                },
            }
            rendered[service] = {
                **payloads,
                "manifest": _canonical_json(manifest).encode(),
            }
        return rendered

    def _freeze_rendered_artifacts(
        self,
        request: FormalProductionRequest,
        facts: dict[int, dict[str, Any]],
    ) -> dict[int, dict[str, bytes]]:
        with tenant_connection(self.dsn, request.tenant_pub_id, row_factory=dict_row) as connection:
            row = connection.execute(
                """SELECT rendered_bundle,artifact_snapshot_hash
                   FROM reporting.formal_report_production
                   WHERE tenant_pub_id=%s AND pub_id=%s""",
                (request.tenant_pub_id, request.pub_id),
            ).fetchone()
        if row and row["rendered_bundle"] is not None:
            return self._load_rendered_bundle(
                request,
                facts,
                row["rendered_bundle"],
                row["artifact_snapshot_hash"],
            )
        rendered = self._render_artifacts(request, facts)
        descriptors: dict[str, dict[str, dict[str, Any]]] = {}
        for service, formats in rendered.items():
            descriptors[str(service)] = {}
            for format_name, payload in formats.items():
                stored = self.evidence.store.put_redacted(
                    payload, mime_type=_MIME_TYPES[format_name]
                )
                descriptors[str(service)][format_name] = {
                    "object_key": stored.key,
                    "sha256": stored.sha256,
                    "byte_size": stored.byte_size,
                    "mime_type": stored.mime_type,
                }
        descriptor_hash = _canonical_hash(descriptors)
        descriptor_json = _canonical_json(descriptors)
        with tenant_connection(self.dsn, request.tenant_pub_id, row_factory=dict_row) as connection:
            persisted = connection.execute(
                """
                UPDATE reporting.formal_report_production
                SET rendered_bundle=%s,artifact_snapshot_hash=%s,updated_at=now()
                WHERE tenant_pub_id=%s AND pub_id=%s AND rendered_bundle IS NULL
                RETURNING rendered_bundle,artifact_snapshot_hash
                """,
                (descriptor_json, descriptor_hash, request.tenant_pub_id, request.pub_id),
            ).fetchone()
            if persisted is None:
                persisted = connection.execute(
                    """SELECT rendered_bundle,artifact_snapshot_hash
                       FROM reporting.formal_report_production
                       WHERE tenant_pub_id=%s AND pub_id=%s""",
                    (request.tenant_pub_id, request.pub_id),
                ).fetchone()
        if persisted is None or persisted["rendered_bundle"] is None:
            raise FormalProductionIncomplete("rendered_bundle_missing")
        return self._load_rendered_bundle(
            request,
            facts,
            persisted["rendered_bundle"],
            persisted["artifact_snapshot_hash"],
        )

    def _load_rendered_bundle(
        self,
        request: FormalProductionRequest,
        facts: Mapping[int, Mapping[str, Any]],
        descriptors: Mapping[str, Any],
        expected_hash: str,
    ) -> dict[int, dict[str, bytes]]:
        if _canonical_hash(descriptors) != expected_hash:
            raise FormalProductionIncomplete("rendered_bundle_integrity_failed")
        if set(descriptors) != {str(service) for service in request.services}:
            raise FormalProductionIncomplete("rendered_bundle_invalid")
        output: dict[int, dict[str, bytes]] = {}
        for service, formats in descriptors.items():
            if not isinstance(formats, Mapping):
                raise FormalProductionIncomplete("rendered_bundle_invalid")
            expected_formats = set(_artifact_formats(int(service), request.document_status))
            if set(formats) != expected_formats:
                raise FormalProductionIncomplete("rendered_bundle_invalid")
            output[int(service)] = {}
            for format_name, descriptor in formats.items():
                if (
                    not isinstance(descriptor, Mapping)
                    or not isinstance(descriptor.get("sha256"), str)
                    or len(str(descriptor["sha256"])) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in str(descriptor["sha256"])
                    )
                    or not isinstance(descriptor.get("object_key"), str)
                    or not descriptor["object_key"]
                ):
                    raise FormalProductionIncomplete("rendered_bundle_invalid")
                payload = self.evidence.store.get_verified(
                    str(descriptor["object_key"]), str(descriptor["sha256"])
                )
                expected_mime_type = _MIME_TYPES.get(str(format_name))
                if (
                    descriptor.get("byte_size") != len(payload)
                    or descriptor.get("mime_type") != expected_mime_type
                ):
                    raise FormalProductionIncomplete("rendered_bundle_invalid")
                output[int(service)][str(format_name)] = payload
            try:
                manifest = json.loads(output[int(service)]["manifest"])
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise FormalProductionIncomplete("rendered_manifest_integrity_failed") from exc
            if (
                not isinstance(manifest, Mapping)
                or manifest.get("schema_version")
                != (
                    "formal-report-manifest-v2"
                    if int(service) == 1
                    and request.document_status
                    in {"internal_review", "delivery_candidate", "approved_signed"}
                    else "formal-report-manifest-v1"
                )
                or manifest.get("service_number") != int(service)
                or (
                    request.service_catalog_version == QUOTATION_SERVICE_CATALOG
                    and manifest.get("service_code") != _service_code_for(request, int(service))
                )
                or (
                    request.service_catalog_version == QUOTATION_SERVICE_CATALOG
                    and manifest.get("service_catalog_version") != request.service_catalog_version
                )
                or (
                    request.service_catalog_version == LEGACY_SERVICE_CATALOG
                    and manifest.get("service_code") is not None
                    and manifest.get("service_code") != _service_code_for(request, int(service))
                )
                or (
                    request.service_catalog_version == LEGACY_SERVICE_CATALOG
                    and manifest.get("service_catalog_version") is not None
                    and manifest.get("service_catalog_version") != request.service_catalog_version
                )
                or manifest.get("document_status") != request.document_status
                or manifest.get("fact_snapshot_hash")
                != _freeze_service_fact(
                    request,
                    int(service),
                    facts[int(service)],
                ).fact_snapshot_hash
                or manifest.get("window")
                != {
                    "start": request.window.start.isoformat(),
                    "end": request.window.end.isoformat(),
                }
            ):
                raise FormalProductionIncomplete("rendered_manifest_request_drifted")
            manifest_artifacts = manifest.get("artifacts")
            if not isinstance(manifest_artifacts, Mapping) or any(
                not isinstance(manifest_artifacts.get(format_name), Mapping)
                or manifest_artifacts[format_name].get("sha256")
                != sha256(output[int(service)][format_name]).hexdigest()
                or manifest_artifacts[format_name].get("byte_size")
                != len(output[int(service)][format_name])
                for format_name in expected_formats - {"manifest"}
            ):
                raise FormalProductionIncomplete("rendered_manifest_integrity_failed")
        return output

    def _persist_bundle(
        self,
        request: FormalProductionRequest,
        facts: dict[int, dict[str, Any]],
        artifacts: dict[int, dict[str, bytes]],
    ) -> dict[str, Any]:
        if set(facts) != set(request.services) or set(artifacts) != set(request.services):
            raise FormalProductionIncomplete("formal_bundle_services_incomplete")
        for service in request.services:
            expected_formats = set(_artifact_formats(service, request.document_status))
            if set(artifacts[service]) != expected_formats or any(
                not isinstance(artifacts[service][format_name], bytes)
                or not artifacts[service][format_name]
                for format_name in expected_formats
            ):
                raise FormalProductionIncomplete("formal_artifacts_incomplete")
        with tenant_connection(self.dsn, request.tenant_pub_id, row_factory=dict_row) as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"{request.tenant_pub_id}:{request.pub_id}:formal-report",),
            )
            production = connection.execute(
                """SELECT * FROM reporting.formal_report_production
                   WHERE tenant_pub_id=%s AND pub_id=%s FOR UPDATE""",
                (request.tenant_pub_id, request.pub_id),
            ).fetchone()
            if production is None:
                raise FormalProductionNotFound("formal_production_not_found")
            existing = connection.execute(
                """SELECT service_number FROM reporting.formal_report_output
                   WHERE tenant_pub_id=%s AND production_pub_id=%s""",
                (request.tenant_pub_id, request.pub_id),
            ).fetchall()
            if production["status"] in {"awaiting_review", "signed"}:
                completed = self._public_row(
                    dict(production),
                    outputs=self._output_rows(
                        connection,
                        request.tenant_pub_id,
                        [request.pub_id],
                    ),
                )
                self._assert_completed_outputs(completed)
                return completed
            if production["status"] != "running":
                raise FormalProductionConflict("formal_production_not_persistable")
            if existing:
                raise FormalProductionIncomplete("partial_formal_outputs_detected")

            descriptors_by_service = {
                service: evidence_descriptors(facts[service]) for service in request.services
            }
            all_descriptors = {
                row["pub_id"]: row for rows in descriptors_by_service.values() for row in rows
            }
            if all_descriptors:
                stored_rows = connection.execute(
                    """
                    SELECT pub_id,project_pub_id,object_key,sha256,mime_type
                    FROM evidence.evidence_asset
                    WHERE tenant_pub_id=%s AND pub_id=ANY(%s::text[]) AND deleted_at IS NULL
                    """,
                    (request.tenant_pub_id, sorted(all_descriptors)),
                ).fetchall()
                stored = {str(row["pub_id"]): row for row in stored_rows}
                if set(stored) != set(all_descriptors):
                    raise FormalProductionIncomplete("frozen_evidence_missing")
                for pub_id, descriptor in all_descriptors.items():
                    if (
                        stored[pub_id]["project_pub_id"] != request.project_pub_id
                        or stored[pub_id]["object_key"] != descriptor["object_key"]
                        or stored[pub_id]["sha256"] != descriptor["sha256"]
                        or stored[pub_id]["mime_type"] != descriptor["mime_type"]
                    ):
                        raise FormalProductionIncomplete("frozen_evidence_drifted")

            for service in request.services:
                customer_facts = customer_fact_snapshot(facts[service])
                if not isinstance(customer_facts, dict):
                    raise FormalProductionIncomplete("customer_fact_snapshot_invalid")
                frozen = _freeze_service_fact(request, service, facts[service])
                filters = dict(frozen.filters)
                try:
                    manifest = json.loads(artifacts[service]["manifest"])
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise FormalProductionIncomplete("rendered_manifest_integrity_failed") from exc
                if (
                    not isinstance(manifest, Mapping)
                    or manifest.get("fact_snapshot_hash") != frozen.fact_snapshot_hash
                ):
                    raise FormalProductionIncomplete("rendered_manifest_fact_snapshot_drifted")
                report_pub_id = _stable_pub_id(
                    "rpt", request.tenant_pub_id, request.pub_id, service
                )
                version_pub_id = _stable_pub_id(
                    "rptv", request.tenant_pub_id, request.pub_id, service, 1
                )
                component = {
                    "service_number": service,
                    "service_code": _service_code_for(request, service),
                    "title": _adapter_for(request, service).title,
                    "document_status": request.document_status,
                    "fact_snapshot_hash": frozen.fact_snapshot_hash,
                }
                connection.execute(
                    """
                    INSERT INTO reporting.report (
                      pub_id,tenant_pub_id,project_pub_id,title,state,workflow_operation_id
                    ) VALUES (%s,%s,%s,%s,'review',%s)
                    """,
                    (
                        report_pub_id,
                        request.tenant_pub_id,
                        request.project_pub_id,
                        _adapter_for(request, service).title,
                        f"formal:{request.pub_id}:service:{service}",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO reporting.report_version (
                      pub_id,tenant_pub_id,report_pub_id,version_number,window_start,window_end,
                      filters,filter_hash,metric_version,scorer_version,fact_snapshot_hash,
                      status,ai_draft_hash,created_by_pub_id
                    ) VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,'review',%s,%s)
                    """,
                    (
                        version_pub_id,
                        request.tenant_pub_id,
                        report_pub_id,
                        frozen.window_start,
                        frozen.window_end,
                        json.dumps(filters, ensure_ascii=False),
                        frozen.filter_hash,
                        frozen.metric_version,
                        frozen.scorer_version,
                        frozen.fact_snapshot_hash,
                        sha256(artifacts[service]["docx"]).hexdigest(),
                        request.created_by_pub_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO reporting.report_component (
                      pub_id,tenant_pub_id,report_version_pub_id,component_type,ordinal,
                      payload,source
                    ) VALUES (%s,%s,%s,'section',0,%s,'system')
                    """,
                    (
                        _stable_pub_id("rptc", request.tenant_pub_id, version_pub_id),
                        request.tenant_pub_id,
                        version_pub_id,
                        json.dumps(component, ensure_ascii=False),
                    ),
                )
                fact_payload = _canonical_json(customer_facts)
                connection.execute(
                    """
                    INSERT INTO reporting.report_frozen_fact (
                      pub_id,tenant_pub_id,report_version_pub_id,ordinal,payload,payload_hash
                    ) VALUES (%s,%s,%s,0,%s,%s)
                    """,
                    (
                        _stable_pub_id("rptf", request.tenant_pub_id, version_pub_id),
                        request.tenant_pub_id,
                        version_pub_id,
                        fact_payload,
                        sha256(fact_payload.encode()).hexdigest(),
                    ),
                )
                for descriptor in descriptors_by_service[service]:
                    connection.execute(
                        """
                        INSERT INTO reporting.report_evidence_reference (
                          pub_id,tenant_pub_id,report_version_pub_id,evidence_pub_id,purpose
                        ) VALUES (%s,%s,%s,%s,'formal_report_frozen_evidence')
                        """,
                        (
                            _stable_pub_id(
                                "rptev", request.tenant_pub_id, version_pub_id, descriptor["pub_id"]
                            ),
                            request.tenant_pub_id,
                            version_pub_id,
                            descriptor["pub_id"],
                        ),
                    )
                provenance = RedactedProvenance(
                    platform_account_pub_id=None,
                    browser_profile_version_pub_id=None,
                    session_event_pub_id=None,
                    channel=CaptureChannel.API,
                    authorization_scope=("report:write",),
                    adapter_version="formal-report-production-v1",
                    capture_time=request.frozen_at,
                    access_class=AccessClass.CUSTOMER_PRIVATE,
                )
                for format_name in _artifact_formats(service, request.document_status):
                    evidence_pub_id = _stable_pub_id(
                        "evd", request.tenant_pub_id, request.pub_id, service, format_name
                    )
                    captured = self.evidence.capture(
                        evidence_pub_id=evidence_pub_id,
                        tenant_pub_id=request.tenant_pub_id,
                        project_pub_id=request.project_pub_id,
                        kind=f"formal_report_service_{service}_{format_name}",
                        payload=artifacts[service][format_name],
                        mime_type=_MIME_TYPES[format_name],
                        source_url=None,
                        provenance=provenance,
                        db_connection=connection,
                    )
                    connection.execute(
                        """
                        INSERT INTO reporting.report_artifact (
                          pub_id,tenant_pub_id,report_version_pub_id,format,evidence_pub_id
                        ) VALUES (%s,%s,%s,%s,%s)
                        """,
                        (
                            _stable_pub_id(
                                "rpta", request.tenant_pub_id, version_pub_id, format_name
                            ),
                            request.tenant_pub_id,
                            version_pub_id,
                            format_name,
                            captured.metadata_pub_id or evidence_pub_id,
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO reporting.formal_report_output (
                      pub_id,tenant_pub_id,production_pub_id,service_number,report_pub_id,
                      report_version_pub_id,fact_snapshot_hash
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        _stable_pub_id("frpo", request.tenant_pub_id, request.pub_id, service),
                        request.tenant_pub_id,
                        request.pub_id,
                        service,
                        report_pub_id,
                        version_pub_id,
                        frozen.fact_snapshot_hash,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO reporting.report_event (
                      pub_id,tenant_pub_id,report_pub_id,report_version_pub_id,event_type,
                      actor_pub_id,data
                    ) VALUES (%s,%s,%s,%s,'formal_production_completed',%s,%s)
                    """,
                    (
                        _stable_pub_id("evt", request.tenant_pub_id, request.pub_id, service),
                        request.tenant_pub_id,
                        report_pub_id,
                        version_pub_id,
                        request.created_by_pub_id,
                        json.dumps({"service_number": service}),
                    ),
                )
            connection.execute(
                """
                UPDATE reporting.formal_report_production
                SET status='awaiting_review',error_code=NULL,updated_at=now()
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (request.tenant_pub_id, request.pub_id),
            )
        return self.get(
            tenant_pub_id=request.tenant_pub_id,
            production_pub_id=request.pub_id,
        )

    def _output_rows(
        self,
        connection: psycopg.Connection[Any],
        tenant_pub_id: str,
        production_pub_ids: list[str],
    ) -> list[dict[str, Any]]:
        if not production_pub_ids:
            return []
        rows = connection.execute(
            """
            SELECT output.production_pub_id,output.service_number,output.report_pub_id,
                   output.report_version_pub_id,output.fact_snapshot_hash,
                   artifact.format,asset.sha256,asset.byte_size,asset.mime_type
            FROM reporting.formal_report_output output
            LEFT JOIN reporting.report_artifact artifact
              ON artifact.tenant_pub_id=output.tenant_pub_id
             AND artifact.report_version_pub_id=output.report_version_pub_id
            LEFT JOIN evidence.evidence_asset asset
              ON asset.tenant_pub_id=artifact.tenant_pub_id
             AND asset.pub_id=artifact.evidence_pub_id AND asset.deleted_at IS NULL
            WHERE output.tenant_pub_id=%s
              AND output.production_pub_id=ANY(%s::text[])
            ORDER BY output.production_pub_id,output.service_number,artifact.format
            """,
            (tenant_pub_id, production_pub_ids),
        ).fetchall()
        return [dict(row) for row in rows]

    def _public_row(self, row: dict[str, Any], *, outputs: list[dict[str, Any]]) -> dict[str, Any]:
        service_catalog_version = str(row.get("service_catalog_version") or LEGACY_SERVICE_CATALOG)
        if service_catalog_version == QUOTATION_SERVICE_CATALOG:
            service_codes = QUOTATION_SERVICE_CODES
        elif service_catalog_version == LEGACY_SERVICE_CATALOG:
            service_codes = LEGACY_SERVICE_CODES
        else:
            raise FormalProductionIncomplete("formal_output_service_catalog_mismatch")
        try:
            selected_services = {int(value) for value in row["services"]}
        except (KeyError, TypeError, ValueError) as exc:
            raise FormalProductionIncomplete("formal_output_service_catalog_mismatch") from exc
        if not selected_services or any(
            service not in service_codes for service in selected_services
        ):
            raise FormalProductionIncomplete("formal_output_service_catalog_mismatch")
        grouped: dict[int, dict[str, Any]] = {}
        for output in outputs:
            if output.get("production_pub_id") != row["pub_id"]:
                continue
            service = int(output["service_number"])
            if service not in selected_services or service not in service_codes:
                raise FormalProductionIncomplete("formal_output_service_catalog_mismatch")
            target = grouped.setdefault(
                service,
                {
                    "service_number": service,
                    "service_code": service_codes[service],
                    "report_pub_id": output["report_pub_id"],
                    "report_version_pub_id": output["report_version_pub_id"],
                    "fact_snapshot_hash": output["fact_snapshot_hash"],
                    "artifacts": [],
                },
            )
            if output.get("format"):
                target["artifacts"].append(
                    {
                        "format": output["format"],
                        "sha256": output["sha256"],
                        "byte_size": output["byte_size"],
                        "mime_type": output["mime_type"],
                        "download_url": (
                            f"/api/v2/reports/formal-productions/{row['pub_id']}"
                            f"/artifacts/{service}/{output['format']}"
                        ),
                    }
                )
        return {
            "pub_id": row["pub_id"],
            "project_pub_id": row["project_pub_id"],
            "services": list(row["services"]),
            "service_catalog_version": service_catalog_version,
            "sop_project_pub_id": row.get("sop_project_pub_id"),
            "status": row["status"],
            "document_status": row["document_status"],
            "window_start": row["window_start"],
            "window_end": row["window_end"],
            "before_window": (
                {"start": row["before_start"], "end": row["before_end"]}
                if row.get("before_start") is not None
                else None
            ),
            "after_window": (
                {"start": row["after_start"], "end": row["after_end"]}
                if row.get("after_start") is not None
                else None
            ),
            "candidate_group_strategy": row["candidate_group_strategy"],
            "document_governance": dict(row.get("document_governance") or {}),
            "workflow_id": row["workflow_id"],
            "fact_snapshot_hash": row.get("fact_snapshot_hash"),
            "error_code": row.get("error_code"),
            "outputs": [grouped[key] for key in sorted(grouped)],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


__all__ = [
    "FORMAL_REPORT_REGISTRY",
    "FORMAL_STRATEGY",
    "FORMAL_WORKFLOW_TYPE",
    "LEGACY_SERVICE_CATALOG",
    "QUOTATION_FORMAL_REPORT_REGISTRY",
    "QUOTATION_SERVICE_CATALOG",
    "FormalProductionConflict",
    "FormalProductionIncomplete",
    "FormalProductionInvalid",
    "FormalProductionNotFound",
    "FormalProductionRequest",
    "FormalReportAdapter",
    "FormalReportProductionService",
    "FormalWindow",
    "GeneratedFormalBundle",
    "customer_fact_snapshot",
    "evidence_descriptors",
    "formal_evidence_gate",
    "normalize_services",
    "request_contract",
]
