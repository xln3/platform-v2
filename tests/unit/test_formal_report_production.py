from __future__ import annotations

import asyncio
import builtins
import json
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from geo_platform.collection.workflow_outbox import (
    WorkflowStartCommand,
    WorkflowStartOutbox,
)
from geo_platform.identity.policy import Principal, Role
from geo_platform.main import app
from geo_platform.reports import formal_production as production
from geo_platform.reports import formal_production_router as production_router
from geo_platform.reports.formal_production import (
    FormalProductionIncomplete,
    FormalProductionInvalid,
    FormalProductionRequest,
    FormalReportProductionService,
    FormalWindow,
    customer_fact_snapshot,
    evidence_descriptors,
    formal_evidence_gate,
    formal_review_contract_hash,
    request_contract,
)
from geo_platform.tenancy.database import get_db
from PIL import Image
from temporalio.exceptions import ApplicationError

from domain.reporting import libreoffice
from domain.reporting.formal_review_service2_docx import _answer_views
from workflows.activities import s02 as s02_activities
from workflows.activities.s02 import (
    fail_formal_report_activity,
    finalize_formal_report_activity,
    preflight_formal_report_runtime_activity,
    produce_formal_report_activity,
)
from workflows.definitions import s02 as s02_definitions
from workflows.definitions.s02 import ReportProductionWorkflow
from workflows.workers import s02 as s02_worker


def _descriptor(prefix: str) -> dict[str, str]:
    return {
        "pub_id": f"evd_{prefix}",
        "object_key": f"sha256/{prefix}",
        "sha256": "a" * 64,
        "mime_type": "image/png",
    }


def test_request_contract_requires_service4_windows_and_rejects_duplicates() -> None:
    window = FormalWindow(date(2026, 7, 1), date(2026, 7, 31))
    with pytest.raises(FormalProductionInvalid, match="invalid_services"):
        request_contract(
            project_pub_id="prj_unit",
            services=[1, 1],
            window=window,
            document_status="pre_formal",
            candidate_group_strategy="evidence_completeness_v1",
            before_window=None,
            after_window=None,
        )
    with pytest.raises(FormalProductionInvalid, match="service4_windows_required"):
        request_contract(
            project_pub_id="prj_unit",
            services=[4],
            window=window,
            document_status="pre_formal",
            candidate_group_strategy="evidence_completeness_v1",
            before_window=None,
            after_window=None,
        )


def test_governed_release_contract_requires_preparation_and_candidate_review() -> None:
    window = FormalWindow(date(2026, 8, 1), date(2026, 8, 13))
    with pytest.raises(FormalProductionInvalid, match="document_governance_required"):
        request_contract(
            project_pub_id="prj_unit",
            services=[1],
            window=window,
            document_status="internal_review",
            candidate_group_strategy="preregistered_scope_v1",
            before_window=None,
            after_window=None,
        )
    with pytest.raises(FormalProductionInvalid, match="candidate_review_record_required"):
        request_contract(
            project_pub_id="prj_unit",
            services=[1],
            window=window,
            document_status="delivery_candidate",
            candidate_group_strategy="preregistered_scope_v1",
            before_window=None,
            after_window=None,
            document_governance={
                "version": "V1.0",
                "prepared_by": "编制员",
                "prepared_date": "2026-08-14",
            },
        )
    contract = request_contract(
        project_pub_id="prj_unit",
        services=[1],
        window=window,
        document_status="delivery_candidate",
        candidate_group_strategy="preregistered_scope_v1",
        before_window=None,
        after_window=None,
        document_governance={
            "version": "V1.0",
            "prepared_by": "编制员",
            "prepared_date": "2026-08-14",
            "reviewed_by": "复核员",
            "reviewed_date": "2026-08-14",
        },
    )
    assert contract["document_governance"]["reviewed_by"] == "复核员"


def test_signed_filename_uses_governed_approval_date(monkeypatch: pytest.MonkeyPatch) -> None:
    statements: list[tuple[str, tuple[object, ...]]] = []

    class Result:
        def fetchone(self) -> dict[str, object]:
            return {
                "project_name": "客户项目",
                "document_status": "approved_signed",
                "document_governance": {
                    "version": "V1.0",
                    "prepared_date": "2026-08-14",
                    "approved_date": "2026-08-15",
                },
                "frozen_at": datetime(2026, 8, 12, tzinfo=UTC),
            }

    class Connection:
        def execute(self, statement: str, params: tuple[object, ...]) -> Result:
            statements.append((statement, params))
            return Result()

    @contextmanager
    def fake_connection(*args: object, **kwargs: object):
        del args, kwargs
        yield Connection()

    monkeypatch.setattr(production, "tenant_connection", fake_connection)
    service = FormalReportProductionService(
        dsn="postgresql://unit", evidence=SimpleNamespace(store=SimpleNamespace())
    )

    assert (
        service.artifact_filename(
            tenant_pub_id="ten_unit",
            production_pub_id="frp_unit",
            service_number=1,
            format_name="pdf",
        )
        == "客户项目_服务1_V1.0_已批准签发版_20260815.pdf"
    )
    statement, params = statements[0]
    assert "JOIN platform.project" not in statement
    assert "jsonb_extract_path_text" in statement
    assert params == (1, 1, "ten_unit", "frp_unit", 1)


def test_worker_rejects_a_persisted_request_whose_contract_drifted() -> None:
    window = FormalWindow(date(2026, 7, 1), date(2026, 7, 31))
    contract = request_contract(
        project_pub_id="prj_unit",
        services=[1],
        window=window,
        document_status="pre_formal",
        candidate_group_strategy="evidence_completeness_v1",
        before_window=None,
        after_window=None,
    )
    row = {
        "pub_id": "frp_unit",
        "tenant_pub_id": "ten_unit",
        "project_pub_id": "prj_unit",
        "services": [1],
        "window_start": window.start,
        "window_end": window.end,
        "document_status": "pre_formal",
        "candidate_group_strategy": "evidence_completeness_v1",
        "document_governance": {},
        "frozen_at": datetime(2026, 8, 12, tzinfo=UTC),
        "created_by_pub_id": "usr_unit",
        "request_hash": production._canonical_hash(contract),
        "before_start": None,
        "before_end": None,
        "after_start": None,
        "after_end": None,
    }
    service = FormalReportProductionService(
        dsn="postgresql://unit",
        evidence=SimpleNamespace(store=SimpleNamespace()),
    )
    assert service._request_from_row(row).window == window
    row["window_end"] = date(2026, 7, 30)
    with pytest.raises(FormalProductionIncomplete, match="formal_request_integrity_failed"):
        service._request_from_row(row)


def test_rendered_bundle_replay_requires_exact_artifact_mime_type() -> None:
    request = FormalProductionRequest(
        pub_id="frp_unit",
        tenant_pub_id="ten_unit",
        project_pub_id="prj_unit",
        services=(1,),
        window=FormalWindow(date(2026, 7, 1), date(2026, 7, 31)),
        document_status="pre_formal",
        candidate_group_strategy="evidence_completeness_v1",
        frozen_at=datetime(2026, 8, 12, tzinfo=UTC),
        created_by_pub_id="usr_unit",
        request_hash="a" * 64,
    )
    docx = b"unit-docx"
    pdf = b"unit-pdf"
    facts = {1: {"document_status": "pre_formal", "summary": "unit facts"}}
    fact_snapshot_hash = production._freeze_service_fact(request, 1, facts[1]).fact_snapshot_hash
    manifest = json.dumps(
        {
            "schema_version": "formal-report-manifest-v1",
            "service_number": 1,
            "document_status": "pre_formal",
            "window": {"start": "2026-07-01", "end": "2026-07-31"},
            "fact_snapshot_hash": fact_snapshot_hash,
            "artifacts": {
                "docx": {
                    "sha256": production.sha256(docx).hexdigest(),
                    "byte_size": len(docx),
                },
                "pdf": {
                    "sha256": production.sha256(pdf).hexdigest(),
                    "byte_size": len(pdf),
                },
            },
        }
    ).encode()
    objects = {"cas/docx": docx, "cas/pdf": pdf, "cas/manifest": manifest}
    mime_types = {
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "application/pdf",
        "manifest": "application/json",
    }
    descriptors = {
        "1": {
            name: {
                "object_key": f"cas/{name}",
                "sha256": production.sha256(payload).hexdigest(),
                "byte_size": len(payload),
                "mime_type": mime_types[name],
            }
            for name, payload in (("docx", docx), ("pdf", pdf), ("manifest", manifest))
        }
    }
    service = FormalReportProductionService(
        dsn="postgresql://unit",
        evidence=SimpleNamespace(
            store=SimpleNamespace(get_verified=lambda key, digest: objects[key])
        ),
    )
    assert service._load_rendered_bundle(
        request, facts, descriptors, production._canonical_hash(descriptors)
    ) == {1: {"docx": docx, "pdf": pdf, "manifest": manifest}}
    descriptors["1"]["pdf"]["mime_type"] = "pdf"
    with pytest.raises(FormalProductionIncomplete, match="rendered_bundle_invalid"):
        service._load_rendered_bundle(
            request, facts, descriptors, production._canonical_hash(descriptors)
        )


def test_customer_snapshot_removes_storage_and_internal_identifiers() -> None:
    snapshot = customer_fact_snapshot(
        {
            "project_pub_id": "prj_secret",
            "visible": "customer fact",
            "asset": {
                "pub_id": "evd_secret",
                "object_key": "sha256/private",
                "sha256": "a" * 64,
            },
            "_runtime": {"path": "/home/operator/private.png"},
            "post_analysis_wiring": {"project_id": "internal"},
            "before_selected_answer_ids": ["ans_secret"],
            "nested": {
                "opaque": "run_secret",
                "project_id": "project-primary-key",
                "internal_ids": ["one", "two"],
            },
            "answer_refs": ["ana_01K2ABCD000000000000000000"],
            "candidate_group_id": "cfg_01K2ABCD000000000000000000",
            "opaque_id": "cit_01K2ABCD000000000000000000",
        }
    )
    assert snapshot == {
        "visible": "customer fact",
        "asset": {"sha256": "a" * 64},
        "nested": {},
    }


def test_frozen_evidence_rejects_conflicting_descriptors_for_one_asset() -> None:
    with pytest.raises(FormalProductionIncomplete, match="frozen_evidence_descriptor_conflict"):
        evidence_descriptors(
            {
                "first": _descriptor("same"),
                "second": _descriptor("same") | {"object_key": "sha256/drifted"},
            }
        )


def test_completed_production_requires_every_service_and_artifact() -> None:
    row = _production_row(status="awaiting_review")
    row["outputs"] = [
        {
            "service_number": service,
            "fact_snapshot_hash": "a" * 64,
            "artifacts": [
                {
                    "format": format_name,
                    "sha256": "b" * 64,
                    "mime_type": "application/octet-stream",
                    "byte_size": 1,
                }
                for format_name in ("docx", "pdf", "manifest")
            ],
        }
        for service in (1, 2)
    ]
    FormalReportProductionService._assert_completed_outputs(row)
    row["outputs"][1]["artifacts"].pop()
    with pytest.raises(FormalProductionIncomplete, match="formal_artifacts_incomplete"):
        FormalReportProductionService._assert_completed_outputs(row)


def test_produce_replay_returns_only_a_complete_persisted_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FormalReportProductionService(
        dsn="postgresql://unit",
        evidence=SimpleNamespace(store=SimpleNamespace()),
    )
    row = _production_row(status="awaiting_review")
    row["outputs"] = [
        {
            "service_number": service_number,
            "fact_snapshot_hash": "a" * 64,
            "artifacts": [
                {
                    "format": format_name,
                    "sha256": "b" * 64,
                    "mime_type": "application/octet-stream",
                    "byte_size": 1,
                }
                for format_name in ("docx", "pdf", "manifest")
            ],
        }
        for service_number in (1, 2)
    ]
    monkeypatch.setattr(service, "get", lambda **kwargs: row)
    monkeypatch.setattr(
        service,
        "_request",
        lambda *args, **kwargs: pytest.fail("completed replay rebuilt the request"),
    )
    assert service.produce(tenant_pub_id="ten_unit", production_pub_id="frp_unit") is row
    row["outputs"][0]["artifacts"].pop()
    with pytest.raises(FormalProductionIncomplete, match="formal_artifacts_incomplete"):
        service.produce(tenant_pub_id="ten_unit", production_pub_id="frp_unit")


def test_service1_formal_gate_requires_complete_cells_and_visual_evidence() -> None:
    selected = [
        {
            "selected_for_main_report": True,
            "expected_cells": 4,
            "observed_cells": 4,
        }
        for _ in range(3)
    ]
    facts = {
        "service1": {
            "quotation_required_repetitions_per_cell": 2,
            "candidate_groups": selected,
            "delivery_v2": {
                "scope": {"current_repetitions": 2, "answers": 2, "extract_ok": 2},
                "sample_registry": [
                    {"has_share_image": True, "has_answer_screenshot": False},
                    {"has_share_image": False, "has_answer_screenshot": True},
                ],
            },
        }
    }
    assert formal_evidence_gate(1, facts) == (True, ())
    selected[0]["observed_cells"] = 3
    ready, reasons = formal_evidence_gate(1, facts)
    assert ready is False
    assert "selected_candidate_group_cells_incomplete" in reasons


def test_service2_formal_gate_uses_project_scoped_source_and_native_anchor_coverage() -> None:
    facts = {
        "service2": {
            "delivery_v2": {
                "citation_funnel": {"eligible_answers": 2, "answers_with_citation": 1},
                "source_fetch": {
                    "planner_mode": "answer_level_v2",
                    "answers_with_planned_documents": 1,
                    "documents_with_answer_relation": 2,
                    "ok": 2,
                },
                "cases": [{"answer_screenshot": _descriptor("answer")}],
                "source_cases": [{"source_screenshot": _descriptor("source")}],
                "answer_visual_coverage": {
                    "cases_with_native_dom_anchor": 0,
                    "cases_with_native_ocr_anchor": 1,
                },
                # Diagnostic-only legacy state must never block a formal report.
                "post_analysis_wiring": {"project_linked": False},
            }
        }
    }
    assert formal_evidence_gate(2, facts) == (True, ())
    facts["service2"]["delivery_v2"]["source_fetch"]["ok"] = 1
    ready, reasons = formal_evidence_gate(2, facts)
    assert ready is False
    assert "source_document_fetch_incomplete" in reasons


def test_service2_ocr_anchor_has_customer_readable_label() -> None:
    stream = BytesIO()
    Image.new("RGB", (640, 480), "white").save(stream, format="PNG")
    _full, crop, note = _answer_views(
        stream.getvalue(),
        {
            "bbox": [100, 100, 300, 180],
            "method": "ocr_rapidocr_ppocrv6_v1",
            "label": "命中原句",
        },
    )
    assert crop is not None
    assert note == "红框按采集时保存的文本位置绘制；红框仅标命中原句"


def test_fact_builder_applies_requested_status_before_formal_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Adapter:
        service_number = 4
        title = "unit"

        def build(self, context: object) -> dict[str, Any]:
            del context
            return {
                "document_status": "pre_formal_review_nonproduction_data",
                "evidence_gate": {"status": "sufficient_for_description"},
                "comparability": {"status": "comparable"},
                "metrics": [{"name": "unit"}],
            }

        def render(self, facts: dict[str, Any], *, blob_loader: object) -> bytes:
            del facts, blob_loader
            return b"unused"

    monkeypatch.setitem(production.FORMAL_REPORT_REGISTRY, 4, Adapter())
    service = FormalReportProductionService(
        dsn="postgresql://unit",
        evidence=SimpleNamespace(store=SimpleNamespace(get_verified=lambda *_: b"")),
    )
    request = FormalProductionRequest(
        pub_id="frp_unit",
        tenant_pub_id="ten_unit",
        project_pub_id="prj_unit",
        services=(4,),
        window=FormalWindow(date(2026, 7, 1), date(2026, 7, 31)),
        document_status="formal",
        candidate_group_strategy="evidence_completeness_v1",
        frozen_at=datetime(2026, 8, 12, tzinfo=UTC),
        created_by_pub_id="usr_unit",
        request_hash="a" * 64,
        before_window=FormalWindow(date(2026, 6, 1), date(2026, 6, 30)),
        after_window=FormalWindow(date(2026, 7, 1), date(2026, 7, 31)),
    )
    facts, digest = service._build_fact_bundle(request)
    assert facts[4]["document_status"] == "formal"
    assert facts[4]["formal_evidence_gate"]["status"] == "ready"
    assert len(digest) == 64


def test_fact_builder_maps_answer_volume_limit_to_stable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Adapter:
        service_number = 1
        title = "unit"

        @staticmethod
        def build(context: object) -> dict[str, Any]:
            del context
            raise ValueError("formal_answer_volume_exceeded")

        @staticmethod
        def render(facts: dict[str, Any], *, blob_loader: object) -> bytes:
            del facts, blob_loader
            return b"unused"

    monkeypatch.setitem(production.FORMAL_REPORT_REGISTRY, 1, Adapter())
    service = FormalReportProductionService(
        dsn="postgresql://unit",
        evidence=SimpleNamespace(store=SimpleNamespace(get_verified=lambda *_: b"")),
    )
    request = FormalProductionRequest(
        pub_id="frp_unit",
        tenant_pub_id="ten_unit",
        project_pub_id="prj_unit",
        services=(1,),
        window=FormalWindow(date(2026, 7, 1), date(2026, 7, 31)),
        document_status="pre_formal",
        candidate_group_strategy="evidence_completeness_v1",
        frozen_at=datetime(2026, 8, 12, tzinfo=UTC),
        created_by_pub_id="usr_unit",
        request_hash="a" * 64,
    )
    with pytest.raises(FormalProductionInvalid, match="formal_fact_volume_exceeded"):
        service._build_fact_bundle(request)


def test_formal_services_render_and_convert_strictly_serially(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Adapter:
        def __init__(self, service_number: int) -> None:
            self.service_number = service_number
            self.title = "unit"

        def build(self, context: object) -> dict[str, Any]:
            del context
            return {}

        def render(self, facts: dict[str, Any], *, blob_loader: object) -> bytes:
            del facts, blob_loader
            events.append(f"render-{self.service_number}")
            return f"docx-{self.service_number}".encode()

    def convert(payload: bytes) -> tuple[bytes, bytes]:
        service_number = payload.decode().rsplit("-", 1)[1]
        events.append(f"libreoffice-{service_number}")
        return payload + b"-refreshed", f"pdf-{service_number}".encode()

    monkeypatch.setitem(production.FORMAL_REPORT_REGISTRY, 1, Adapter(1))
    monkeypatch.setitem(production.FORMAL_REPORT_REGISTRY, 2, Adapter(2))
    monkeypatch.setattr(production, "refresh_docx_and_export_pdf", convert)
    service = FormalReportProductionService(
        dsn="postgresql://unit",
        evidence=SimpleNamespace(store=SimpleNamespace(get_verified=lambda *_: b"")),
    )
    request = FormalProductionRequest(
        pub_id="frp_unit",
        tenant_pub_id="ten_unit",
        project_pub_id="prj_unit",
        services=(1, 2),
        window=FormalWindow(date(2026, 7, 1), date(2026, 7, 31)),
        document_status="pre_formal",
        candidate_group_strategy="evidence_completeness_v1",
        frozen_at=datetime(2026, 8, 12, tzinfo=UTC),
        created_by_pub_id="usr_unit",
        request_hash="a" * 64,
    )
    rendered = service._render_artifacts(
        request,
        {
            1: {"document_status": "pre_formal"},
            2: {"document_status": "pre_formal"},
        },
    )
    assert set(rendered) == {1, 2}
    assert events == ["render-1", "libreoffice-1", "render-2", "libreoffice-2"]


def test_service1_formal_asset_loader_prefers_clean_excerpt_screenshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements: list[str] = []

    class Result:
        @staticmethod
        def fetchall() -> list[dict[str, object]]:
            return [
                {
                    "from_pub_id": "ans_unit",
                    "pub_id": "evd_clean_excerpt",
                    "object_key": "sha256/clean-excerpt",
                    "sha256": "a" * 64,
                    "mime_type": "image/png",
                    "kind": "answer_excerpt_screenshot",
                }
            ]

    class Connection:
        @staticmethod
        def execute(statement: str, parameters: object) -> Result:
            statements.append(" ".join(statement.split()))
            assert parameters == ("ten_unit", ["ans_unit"])
            return Result()

    @contextmanager
    def connection(*args: object, **kwargs: object) -> object:
        del args, kwargs
        yield Connection()

    monkeypatch.setattr(production, "tenant_connection", connection)
    request = FormalProductionRequest(
        pub_id="frp_unit",
        tenant_pub_id="ten_unit",
        project_pub_id="prj_unit",
        services=(1,),
        window=FormalWindow(date(2026, 7, 1), date(2026, 7, 31)),
        document_status="pre_formal",
        candidate_group_strategy="evidence_completeness_v1",
        frozen_at=datetime(2026, 8, 12, tzinfo=UTC),
        created_by_pub_id="usr_unit",
        request_hash="a" * 64,
    )
    facts: dict[str, Any] = {
        "service1": {
            "answer_registry": [{"answer_pub_id": "ans_unit", "selected_for_main_report": True}]
        }
    }
    production.FormalBuildContext(
        dsn="postgresql://unit",
        request=request,
        blob_loader=lambda *_: b"",
    ).attach_service1_assets(facts)

    assert facts["_formal_evidence_assets"]["ans_unit"]["kind"] == ("answer_excerpt_screenshot")
    assert "'share_image','answer_excerpt_screenshot','answer_screenshot'" in statements[0]
    assert "WHEN 'share_image' THEN 0 WHEN 'answer_excerpt_screenshot' THEN 1" in statements[0]


def test_formal_temporal_activities_are_registered() -> None:
    assert {
        preflight_formal_report_runtime_activity,
        produce_formal_report_activity,
        fail_formal_report_activity,
        finalize_formal_report_activity,
    }.issubset(set(s02_worker.S02_ACTIVITIES))


def test_fail_formal_activity_does_not_preflight_object_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Store:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def ensure_bucket(self) -> None:
            calls.append("ensure_bucket")

    class Evidence:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

    class Service:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def mark_failed(self, **kwargs: object) -> dict[str, object]:
            calls.append("mark_failed")
            return {"status": "failed"} | kwargs

    monkeypatch.setattr(s02_activities, "ContentAddressedObjectStore", Store)
    monkeypatch.setattr(s02_activities, "EvidenceService", Evidence)
    monkeypatch.setattr(s02_activities, "FormalReportProductionService", Service)
    monkeypatch.setattr(s02_activities, "_postgres_dsn", lambda: "postgresql://unit")

    result = asyncio.run(
        fail_formal_report_activity(
            {
                "tenant_pub_id": "ten_unit",
                "formal_production_pub_id": "frp_unit",
                "error_code": "production_failed",
            }
        )
    )

    assert result["status"] == "failed"
    assert calls == ["mark_failed"]


def test_formal_review_contract_hash_is_canonical_and_strict() -> None:
    expected = formal_review_contract_hash(
        approved=False,
        reviewer_pub_id="usr_reviewer",
        rationale="Needs changes.",
    )
    assert (
        expected
        == sha256(
            b'{"approved":false,"rationale":"Needs changes.","reviewer_pub_id":"usr_reviewer"}'
        ).hexdigest()
    )
    with pytest.raises(FormalProductionInvalid, match="formal_review_signal_invalid"):
        formal_review_contract_hash(  # type: ignore[arg-type]
            approved="false",
            reviewer_pub_id="usr_reviewer",
            rationale="Needs changes.",
        )


def test_finalize_formal_activity_rejects_string_boolean_before_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        s02_activities,
        "_formal_report_service",
        lambda: pytest.fail("invalid review signal must not reach the database service"),
    )
    with pytest.raises(ApplicationError) as exc_info:
        asyncio.run(
            finalize_formal_report_activity(
                {
                    "tenant_pub_id": "ten_unit",
                    "formal_production_pub_id": "frp_unit",
                    "review": {
                        "approved": "false",
                        "reviewer_pub_id": "usr_reviewer",
                        "rationale": "Needs changes.",
                    },
                }
            )
        )
    assert exc_info.value.type == "formal_review_signal_invalid"
    assert exc_info.value.non_retryable is True


def test_formal_workflow_waits_for_review_when_timeout_loses_commit_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = ReportProductionWorkflow()
    calls: list[str] = []

    async def execute_activity(activity_fn: object, payload: dict[str, Any], **kwargs: object):
        del kwargs
        name = getattr(activity_fn, "__name__", "")
        calls.append(name)
        if name == "preflight_formal_report_runtime_activity":
            return {"state": "ready"}
        if name == "produce_formal_report_activity":
            raise TimeoutError("activity thread is still completing")
        if name == "fail_formal_report_activity":
            assert payload["error_code"] == "production_failed"
            return {"status": "awaiting_review", "outputs": [{"service_number": 1}]}
        if name == "finalize_formal_report_activity":
            assert payload["review"]["approved"] is True
            return {"status": "signed"}
        raise AssertionError(name)

    async def wait_condition(predicate: object) -> None:
        instance._review = {"approved": True, "reviewer_pub_id": "usr_reviewer"}
        assert callable(predicate) and predicate()

    monkeypatch.setattr(s02_definitions.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(s02_definitions.workflow, "wait_condition", wait_condition)

    result = asyncio.run(
        instance._run_formal({"tenant_pub_id": "ten_unit", "formal_production_pub_id": "frp_unit"})
    )
    assert result["status"] == "signed"
    assert "finalize_formal_report_activity" in calls


def test_uno_loader_restores_python_import_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def uno_import(*args: object, **kwargs: object) -> None:
        del args, kwargs

    def import_uno(name: str) -> object:
        assert name == "uno"
        builtins.__import__ = uno_import
        return object()

    monkeypatch.setattr(libreoffice, "_PYTHON_IMPORT", original_import)
    monkeypatch.setattr(libreoffice.importlib, "import_module", import_uno)
    try:
        libreoffice._load_uno()
        assert builtins.__import__ is original_import
    finally:
        builtins.__import__ = original_import


def test_produce_activity_preserves_formal_evidence_error_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeService:
        def produce(self, **kwargs: object) -> dict[str, Any]:
            del kwargs
            raise FormalProductionInvalid("formal_evidence_requirements_not_met")

        def mark_failed(self, **kwargs: object) -> dict[str, Any]:
            return {"status": "failed", "error_code": kwargs["error_code"]}

    monkeypatch.setattr(s02_activities, "_formal_report_service", lambda: FakeService())
    monkeypatch.setattr(s02_activities.activity, "heartbeat", lambda *args, **kwargs: None)
    result = asyncio.run(
        produce_formal_report_activity(
            {"tenant_pub_id": "ten_unit", "formal_production_pub_id": "frp_unit"}
        )
    )
    assert result == {
        "status": "failed",
        "error_code": "formal_evidence_requirements_not_met",
    }


def test_produce_activity_preserves_formal_fact_volume_error_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeService:
        @staticmethod
        def produce(**kwargs: object) -> dict[str, Any]:
            del kwargs
            raise FormalProductionInvalid("formal_fact_volume_exceeded")

        @staticmethod
        def mark_failed(**kwargs: object) -> dict[str, Any]:
            return {"status": "failed", "error_code": kwargs["error_code"]}

    monkeypatch.setattr(s02_activities, "_formal_report_service", lambda: FakeService())
    monkeypatch.setattr(s02_activities.activity, "heartbeat", lambda *args, **kwargs: None)
    result = asyncio.run(
        produce_formal_report_activity(
            {"tenant_pub_id": "ten_unit", "formal_production_pub_id": "frp_unit"}
        )
    )
    assert result == {"status": "failed", "error_code": "formal_fact_volume_exceeded"}


def test_formal_activity_result_is_temporal_json_serializable() -> None:
    result = s02_activities._formal_activity_result(
        {
            "window_start": date(2026, 8, 10),
            "created_at": datetime(2026, 8, 13, 0, 20, tzinfo=UTC),
            "score": Decimal("1.25"),
            "outputs": ({"service_number": 1},),
        }
    )

    assert result == {
        "window_start": "2026-08-10",
        "created_at": "2026-08-13T00:20:00+00:00",
        "score": "1.25",
        "outputs": [{"service_number": 1}],
    }
    assert json.loads(json.dumps(result)) == result


def test_s02_worker_preflights_before_accepting_work(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class FakeClient:
        @staticmethod
        async def connect(*args: object, **kwargs: object) -> object:
            del args, kwargs
            events.append("connect")
            return object()

    class FakeWorker:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args
            assert set(kwargs["activities"]) >= {
                preflight_formal_report_runtime_activity,
                produce_formal_report_activity,
            }
            assert kwargs["max_concurrent_activities"] == 2

        async def run(self) -> None:
            events.append("run")

    monkeypatch.setattr(s02_worker, "report_runtime_preflight", lambda: events.append("preflight"))
    monkeypatch.setattr(s02_worker, "Client", FakeClient)
    monkeypatch.setattr(s02_worker, "Worker", FakeWorker)
    monkeypatch.setattr(s02_worker, "get_settings", lambda: object())
    monkeypatch.setattr(s02_worker, "configure_tracing", lambda *args, **kwargs: None)
    asyncio.run(s02_worker.run_s02_worker())
    assert events == ["preflight", "connect", "run"]


def test_outbox_dispatches_formal_report_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, object, str, str]] = []

    class Temporal:
        async def start_workflow(
            self, workflow: object, payload: object, *, id: str, task_queue: str
        ) -> object:
            calls.append((workflow, payload, id, task_queue))
            return SimpleNamespace(result_run_id="run_formal")

    command = WorkflowStartCommand(
        command_id="00000000-0000-0000-0000-000000000001",
        tenant_pub_id="ten_unit",
        workflow_type="formal_report_production",
        workflow_id="formal-report/ten_unit/frp_unit",
        task_queue="geo-platform-v2-s02",
        payload={"tenant_pub_id": "ten_unit", "formal_production_pub_id": "frp_unit"},
        trace_context={},
    )
    outbox = WorkflowStartOutbox(dsn="postgresql://unused", temporal=Temporal())  # type: ignore[arg-type]
    monkeypatch.setattr(outbox, "claim", lambda workflow_id=None: command)
    started: list[str | None] = []
    monkeypatch.setattr(outbox, "started", lambda item, run_id: started.append(run_id))
    monkeypatch.setattr(outbox, "failed", lambda item, error: pytest.fail(str(error)))
    assert asyncio.run(outbox.dispatch_one()) is True
    assert calls == [
        (
            ReportProductionWorkflow.run,
            {"tenant_pub_id": "ten_unit", "formal_production_pub_id": "frp_unit"},
            command.workflow_id,
            command.task_queue,
        )
    ]
    assert started == ["run_formal"]


def test_outbox_rejects_a_cross_tenant_start_before_temporal_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = WorkflowStartCommand(
        command_id="00000000-0000-0000-0000-000000000002",
        tenant_pub_id="ten_column",
        workflow_type="formal_report_production",
        workflow_id="formal-report/ten_column/frp_unit",
        task_queue="geo-platform-v2-s02",
        payload={"tenant_pub_id": "ten_payload", "formal_production_pub_id": "frp_unit"},
        trace_context={},
    )
    outbox = WorkflowStartOutbox(dsn="postgresql://unused", temporal=object())  # type: ignore[arg-type]
    monkeypatch.setattr(outbox, "claim", lambda workflow_id=None: command)
    failed: list[str] = []
    monkeypatch.setattr(
        outbox,
        "failed",
        lambda item, error: failed.append(type(error).__name__),
    )

    with pytest.raises(RuntimeError, match="workflow_start_tenant_mismatch"):
        asyncio.run(outbox.dispatch_one())
    assert failed == []


def _production_row(status: str = "awaiting_review") -> dict[str, Any]:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    return {
        "pub_id": "frp_unit",
        "project_pub_id": "prj_unit",
        "services": [1, 2],
        "service_catalog_version": "legacy_report_services_v1",
        "sop_project_pub_id": None,
        "status": status,
        "document_status": "pre_formal",
        "window_start": date(2026, 7, 1),
        "window_end": date(2026, 7, 31),
        "before_window": None,
        "after_window": None,
        "candidate_group_strategy": "evidence_completeness_v1",
        "document_governance": {},
        "workflow_id": "formal-report/ten_unit/frp_unit",
        "fact_snapshot_hash": "a" * 64,
        "outputs": [],
        "error_code": None,
        "created_at": now,
        "updated_at": now,
    }


def test_review_endpoint_queues_idempotent_signal_and_keeps_review_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeService:
        def get(self, **kwargs: object) -> dict[str, Any]:
            del kwargs
            return _production_row() | {
                "document_status": "delivery_candidate",
                "candidate_group_strategy": "preregistered_scope_v1",
            }

    class FakeSession:
        def scalar(self, statement: object) -> object:
            del statement
            return SimpleNamespace(id="tenant-id", pub_id="ten_unit")

        def execute(self, statement: object, parameters: dict[str, object] | None = None) -> object:
            sql = str(statement)

            class Result:
                def mappings(self) -> Result:
                    return self

                def one_or_none(self) -> dict[str, object]:
                    return {
                        "status": "awaiting_review",
                        "document_status": "delivery_candidate",
                        "review_request_hash": None,
                    }

                def scalar_one_or_none(self) -> object:
                    assert parameters is not None
                    return parameters["review_hash"]

            if "pg_advisory_xact_lock" in sql:
                return Result()
            if "SELECT status,document_status,review_request_hash" in sql:
                return Result()
            if "UPDATE reporting.formal_report_production" in sql:
                return Result()
            raise AssertionError(sql)

        def commit(self) -> None:
            pass

        def rollback(self) -> None:
            pass

    signals: list[dict[str, Any]] = []
    monkeypatch.setattr(production_router, "_service", lambda: FakeService())
    monkeypatch.setattr(production_router, "set_tenant_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        production_router, "workflow_signal_replayed", lambda *args, **kwargs: False
    )
    monkeypatch.setattr(
        production_router,
        "enqueue_workflow_signal",
        lambda *args, **kwargs: signals.append(kwargs),
    )
    app.dependency_overrides[production_router.get_principal] = lambda: Principal(
        subject="reviewer@example.test",
        role=Role.REVIEWER,
        tenant_pub_id="ten_unit",
        user_pub_id="usr_reviewer",
    )
    app.dependency_overrides[get_db] = lambda: FakeSession()
    try:
        response = TestClient(app).post(
            "/api/v2/reports/formal-productions/frp_unit/review",
            headers={"Idempotency-Key": "review-unit-key-0001"},
            json={"decision": "approved", "rationale": "证据已逐项复核。"},
        )
    finally:
        app.dependency_overrides.pop(production_router.get_principal, None)
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 202
    assert response.json()["status"] == "awaiting_review"
    assert signals[0]["signal_name"] == "review"
    assert signals[0]["args"][0] == {
        "approved": True,
        "reviewer_pub_id": "usr_reviewer",
        "rationale": "证据已逐项复核。",
    }


def test_review_endpoint_rejects_pre_formal_approval_before_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeService:
        def get(self, **kwargs: object) -> dict[str, Any]:
            del kwargs
            return _production_row()

    signals: list[object] = []
    monkeypatch.setattr(production_router, "_service", lambda: FakeService())
    monkeypatch.setattr(
        production_router,
        "enqueue_workflow_signal",
        lambda *args, **kwargs: signals.append((args, kwargs)),
    )
    app.dependency_overrides[production_router.get_principal] = lambda: Principal(
        subject="reviewer@example.test",
        role=Role.REVIEWER,
        tenant_pub_id="ten_unit",
        user_pub_id="usr_reviewer",
    )
    try:
        response = TestClient(app).post(
            "/api/v2/reports/formal-productions/frp_unit/review",
            headers={"Idempotency-Key": "review-unit-key-0002"},
            json={"decision": "approved", "rationale": "试图批准预正式稿。"},
        )
    finally:
        app.dependency_overrides.pop(production_router.get_principal, None)
    assert response.status_code == 409
    assert "delivery_candidate_required" in str(response.json())
    assert signals == []


def test_list_endpoint_rejects_invalid_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeService:
        def list_productions(self, **kwargs: object) -> list[dict[str, Any]]:
            del kwargs
            raise FormalProductionInvalid("invalid_cursor")

    monkeypatch.setattr(production_router, "_service", lambda: FakeService())
    app.dependency_overrides[production_router.get_principal] = lambda: Principal(
        subject="operator@example.test",
        role=Role.OPERATOR,
        tenant_pub_id="ten_unit",
        user_pub_id="usr_operator",
    )
    try:
        response = TestClient(app).get(
            "/api/v2/reports/formal-productions?cursor=frp_other_project"
        )
    finally:
        app.dependency_overrides.pop(production_router.get_principal, None)
    assert response.status_code == 422
    assert "invalid_cursor" in str(response.json())
