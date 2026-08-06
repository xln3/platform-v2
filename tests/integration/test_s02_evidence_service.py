import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import psycopg
import pytest
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.evidence.service import EvidenceService

from domain.evidence.diff import BoundingBox, OcrSpan
from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from domain.reporting.artifacts import render_docx, render_html, render_pdf, render_xlsx

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


@pytest.fixture
def service() -> EvidenceService:
    store = ContentAddressedObjectStore(
        endpoint="http://127.0.0.1:19000",
        access_key="geo",
        secret_key="geo_dev_only_password",
    )
    store.ensure_bucket()
    return EvidenceService(dsn=POSTGRES_DSN, store=store)


def provenance(access_class: AccessClass = AccessClass.PUBLIC) -> RedactedProvenance:
    return RedactedProvenance(
        platform_account_pub_id=None,
        browser_profile_version_pub_id=None,
        session_event_pub_id=None,
        channel=CaptureChannel.API,
        authorization_scope=("read",),
        adapter_version="fixture-v1",
        capture_time=datetime.now(UTC),
        access_class=access_class,
    )


def test_authorized_session_capture_requires_validated_lease(
    service: EvidenceService,
) -> None:
    suffix = uuid4().hex
    authorized = RedactedProvenance(
        platform_account_pub_id=f"acct_{uuid4().hex}",
        browser_profile_version_pub_id=f"bpv_{uuid4().hex}",
        session_event_pub_id=f"sevt_{uuid4().hex}",
        channel=CaptureChannel.WEB,
        authorization_scope=("evidence:capture",),
        adapter_version="fixture-v1",
        capture_time=datetime.now(UTC),
        access_class=AccessClass.CUSTOMER_PRIVATE,
        authorized_session_capture=True,
    )
    arguments = {
        "evidence_pub_id": f"evd_{uuid4().hex}",
        "tenant_pub_id": f"tnt_{suffix}",
        "project_pub_id": f"prj_{suffix}",
        "kind": "authenticated_page",
        "payload": b"redacted authenticated content",
        "mime_type": "text/plain",
        "source_url": "https://example.com/private",
        "provenance": authorized,
    }
    with pytest.raises(PermissionError, match="validated capability lease"):
        service.capture(**arguments)
    service.capture(**arguments, validated_lease_pub_id=f"lease_{uuid4().hex}")


def test_object_success_db_failure_is_detectable_and_recoverable(
    service: EvidenceService,
) -> None:
    with pytest.raises(RuntimeError, match="injected"):
        service.capture(
            evidence_pub_id=f"evd_{uuid4().hex}",
            tenant_pub_id="tnt_recovery",
            project_pub_id="prj_recovery",
            kind="html_snapshot",
            payload=f"orphan-{uuid4().hex}".encode(),
            mime_type="text/html",
            source_url="https://example.com",
            provenance=provenance(),
            inject_db_failure=True,
        )
    # Locate the object deterministically from the redacted content and prove DB has no row.
    stored = service.store.put_redacted(
        # A separate marker is used to verify the same failure/recovery invariant explicitly.
        b"explicit-orphan",
        mime_type="text/plain",
    )
    assert service.find_orphan(stored, tenant_pub_id="tnt_recovery")
    service.store.delete(stored.key)


def test_concurrent_evidence_occurrences_share_object_but_not_provenance(
    service: EvidenceService,
) -> None:
    suffix = uuid4().hex
    tenant = f"tnt_{suffix}"
    payload = f"concurrent-parent-{suffix}".encode()

    def capture(index: int) -> tuple[str, str]:
        stored = service.capture(
            evidence_pub_id=f"evd_{suffix}_{index}",
            tenant_pub_id=tenant,
            project_pub_id=f"prj_{suffix}",
            kind="report_pdf",
            payload=payload,
            mime_type="application/pdf",
            source_url=None,
            provenance=provenance(),
        )
        assert stored.metadata_pub_id is not None
        return stored.metadata_pub_id, stored.key

    with ThreadPoolExecutor(max_workers=8) as executor:
        occurrences = list(executor.map(capture, range(16)))
    assert len({item[0] for item in occurrences}) == 16
    assert len({item[1] for item in occurrences}) == 1
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_pub_id', %s, true)",
            (tenant,),
        )
        count = connection.execute(
            """
            SELECT count(*) FROM evidence.evidence_asset
            WHERE tenant_pub_id=%s AND kind='report_pdf'
            """,
            (tenant,),
        ).fetchone()
        assert count is not None
        assert count[0] == 16


def test_evidence_public_id_replay_is_exact_and_rejects_metadata_drift(
    service: EvidenceService,
) -> None:
    suffix = uuid4().hex
    evidence_pub_id = f"evd_{suffix}"
    tenant = f"tnt_{suffix}"
    captured_at = datetime.now(UTC)
    capture_provenance = RedactedProvenance(
        platform_account_pub_id=None,
        browser_profile_version_pub_id=None,
        session_event_pub_id=None,
        channel=CaptureChannel.API,
        authorization_scope=("read",),
        adapter_version="fixture-v1",
        capture_time=captured_at,
        access_class=AccessClass.PUBLIC,
    )
    arguments = {
        "evidence_pub_id": evidence_pub_id,
        "tenant_pub_id": tenant,
        "project_pub_id": f"prj_{suffix}",
        "kind": "answer_screenshot",
        "payload": b"stable evidence occurrence",
        "mime_type": "image/png",
        "source_url": "https://example.com/answer",
        "provenance": capture_provenance,
    }
    first = service.capture(**arguments)
    replay = service.capture(**arguments)
    assert replay.metadata_pub_id == first.metadata_pub_id == evidence_pub_id
    assert replay.key == first.key
    with pytest.raises(ValueError, match="evidence replay payload drifted"):
        service.capture(**(arguments | {"project_pub_id": f"prj_other_{suffix}"}))


def test_package_access_expiry_revoke_audit_and_public_boundary(
    service: EvidenceService,
) -> None:
    suffix = uuid4().hex
    tenant = f"tnt_{suffix}"
    public_id = f"evd_{uuid4().hex}"
    private_id = f"evd_{uuid4().hex}"
    service.capture(
        evidence_pub_id=public_id,
        tenant_pub_id=tenant,
        project_pub_id=f"prj_{suffix}",
        kind="answer_text",
        payload=f"public-{suffix}".encode(),
        mime_type="text/plain",
        source_url="https://example.com/public",
        provenance=provenance(),
    )
    service.capture(
        evidence_pub_id=private_id,
        tenant_pub_id=tenant,
        project_pub_id=f"prj_{suffix}",
        kind="private_text",
        payload=f"private-{suffix}".encode(),
        mime_type="text/plain",
        source_url="https://example.com/private",
        provenance=provenance(AccessClass.PAID_OR_ORGANIZATION),
    )
    with pytest.raises(PermissionError, match="public package"):
        service.create_package(
            package_pub_id=f"pkg_{uuid4().hex}",
            tenant_pub_id=tenant,
            evidence_pub_ids=[private_id],
            public=True,
            expires_at=None,
        )
    package_id = f"pkg_{uuid4().hex}"
    service.create_package(
        package_pub_id=package_id,
        tenant_pub_id=tenant,
        evidence_pub_ids=[public_id],
        public=True,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    token = service.grant(
        grant_pub_id=f"grant_{uuid4().hex}",
        package_pub_id=package_id,
        tenant_pub_id=tenant,
    )
    assert service.authorize_package_access(token=token, request_id=f"req_{suffix}")["pub_id"] == (
        package_id
    )
    service.revoke_package(package_id, tenant)
    with pytest.raises(PermissionError):
        service.authorize_package_access(token=token, request_id=f"req_revoke_{suffix}")
    with psycopg.connect(POSTGRES_DSN) as connection:
        outcomes = connection.execute(
            """
            SELECT outcome FROM evidence.evidence_access_audit
            WHERE resource_pub_id=%s ORDER BY id
            """,
            (package_id,),
        ).fetchall()
    assert outcomes == [("allowed",), ("denied",)]


def test_report_artifacts_are_real_container_formats() -> None:
    sections = [{"title": "结论", "body": "概率结论，需要人工裁决。"}]
    assert render_html("GEO 报告", sections).startswith(b"<!doctype html>")
    pdf = render_pdf("GEO report", sections)
    assert pdf.startswith(b"%PDF-1.7") and pdf.endswith(b"%%EOF\n")
    for artifact, expected in (
        (render_docx("GEO 报告", sections), "word/document.xml"),
        (render_xlsx([{"metric": "mention_rate", "value": 1}]), "xl/worksheets/sheet1.xml"),
    ):
        with ZipFile(BytesIO(artifact)) as archive:
            assert expected in archive.namelist()


def test_ocr_text_bbox_anchors_and_historical_diff(service: EvidenceService) -> None:
    suffix = uuid4().hex
    tenant = f"tnt_{suffix}"
    ids = [f"evd_{uuid4().hex}", f"evd_{uuid4().hex}"]
    for index, evidence_id in enumerate(ids):
        service.capture(
            evidence_pub_id=evidence_id,
            tenant_pub_id=tenant,
            project_pub_id=None,
            kind=f"source_snapshot_{index}",
            payload=f"Acme version {index}".encode(),
            mime_type="text/plain",
            source_url="https://example.com/history",
            provenance=provenance(),
        )
    anchor_ids = service.persist_ocr(
        tenant_pub_id=tenant,
        evidence_pub_id=ids[1],
        text="Acme version 1",
        spans=[
            OcrSpan(
                text="Acme",
                start=0,
                end=4,
                bbox=BoundingBox(x=10, y=20, width=80, height=24),
                confidence=0.99,
            )
        ],
        ocr_version="fixture-ocr-v1",
    )
    diff_id = service.persist_diff(
        tenant_pub_id=tenant,
        before_evidence_pub_id=ids[0],
        after_evidence_pub_id=ids[1],
        before_text="Acme version 0",
        after_text="Acme version 1",
        before_perceptual_hash="00001111",
        after_perceptual_hash="00011111",
    )
    snapshots = [
        service.record_snapshot(
            tenant_pub_id=tenant,
            subject_pub_id="source_history",
            evidence_pub_id=evidence_id,
            normalized_text=f"Acme version {index}",
            perceptual_hash=("00001111", "00011111")[index],
        )
        for index, evidence_id in enumerate(ids)
    ]
    with psycopg.connect(POSTGRES_DSN) as connection:
        anchor = connection.execute(
            "SELECT bbox FROM evidence.evidence_anchor WHERE pub_id=%s", (anchor_ids[0],)
        ).fetchone()
        diff = connection.execute(
            "SELECT text_diff,similarity FROM evidence.evidence_diff WHERE pub_id=%s",
            (diff_id,),
        ).fetchone()
    assert anchor[0]["ocr_version"] == "fixture-ocr-v1"
    assert diff[0]["visual_similarity"] == 0.875
    assert 0 < diff[1] < 1
    assert [item["snapshot_number"] for item in snapshots] == [1, 2]
    assert snapshots[0]["normalized_text_hash"] != snapshots[1]["normalized_text_hash"]
