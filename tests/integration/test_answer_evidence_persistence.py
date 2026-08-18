from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import psycopg
from fastapi.testclient import TestClient
from geo_platform.config import get_settings
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.main import app
from geo_platform.reports.formal_review_service2 import (
    _attach_answer_case_assets,
    load_service2_answer_screenshots,
)
from PIL import Image

from domain.reporting.formal_review_service2_docx import _answer_views
from workflows.activities.collection import (
    CollectionEvidenceRef,
    CollectionTaskInput,
    CollectionTaskResult,
    persist_collection_result,
)
from workflows.activities.official_share import ShareLinkVerification, write_share_link_manifest

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


def _bootstrap_run(client: TestClient) -> tuple[str, str]:
    suffix = secrets.token_hex(8)
    subject = f"answer-evidence-{suffix}"
    bootstrap = client.post(
        "/api/v2/identity/bootstrap",
        headers={"X-Bootstrap-Secret": "development-bootstrap"},
        json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
    )
    assert bootstrap.status_code == 201
    tenant_pub_id = str(bootstrap.json()["tenant_pub_id"])
    headers = {
        "X-Tenant-Id": tenant_pub_id,
        "X-Actor-Id": subject,
        "X-Actor-Role": "admin",
        "Idempotency-Key": f"project-{suffix}",
    }
    project = client.post(
        "/api/v2/projects",
        headers=headers,
        json={"name": "Answer evidence", "customer_name": "Persistence fixture"},
    )
    assert project.status_code == 201
    project_pub_id = str(project.json()["pub_id"])
    headers["Idempotency-Key"] = f"freeze-{suffix}"
    frozen = client.post(
        f"/api/v2/projects/{project_pub_id}/config/freeze",
        headers=headers,
        json={
            "query_groups": [{"name": "Core", "items": [{"text": "Evidence?"}]}],
            "regions": ["CN-BJ"],
            "models": ["fixed"],
            "modes": ["normal"],
            "frequency": "manual",
            "effective_at": datetime.now(UTC).isoformat(),
        },
    )
    assert frozen.status_code == 201
    headers["Idempotency-Key"] = f"run-{suffix}"
    accepted = client.post(
        "/api/v2/collection/runs",
        headers=headers,
        json={
            "project_pub_id": project_pub_id,
            "config_version_pub_id": frozen.json()["pub_id"],
            "requires_intervention": False,
        },
    )
    assert accepted.status_code == 202
    return tenant_pub_id, str(accepted.json()["workflow_id"]).rsplit("/", 1)[-1]


def test_answer_anchor_and_clean_image_round_trip_through_postgres_and_cas(
    tmp_path: Path,
) -> None:
    with TestClient(app) as client:
        tenant_pub_id, run_pub_id = _bootstrap_run(client)

    answer_text = "盛邦安全证据链可复核"
    image_path = tmp_path / "answer-evidence.png"
    Image.new("RGB", (640, 320), "white").save(image_path, format="PNG")
    image_payload = image_path.read_bytes()
    bbox = {
        "x": 10,
        "y": 20,
        "width": 120,
        "height": 32,
        "image_width": 640,
        "image_height": 320,
        "confidence": 0.97,
        "anchor_method": "ocr_rapidocr_ppocrv6_v1",
        "ocr_version": "rapidocr-3.9.2+onnxruntime-1.28.0",
    }
    persist_collection_result(
        tenant_pub_id,
        run_pub_id,
        CollectionTaskResult(
            business_key="answer-evidence-1",
            answer_text=answer_text,
            screenshot_ref="",
            quality_state="accepted",
            evidence=[
                CollectionEvidenceRef(
                    kind="answer_excerpt_screenshot",
                    path=str(image_path),
                    relation_type="answer_evidence_excerpt",
                    mime_type="image/png",
                    anchors=[
                        {
                            "text_start": 0,
                            "text_end": len(answer_text),
                            "text": answer_text,
                            "bbox": bbox,
                        }
                    ],
                )
            ],
        ),
        CollectionTaskInput(
            business_key="answer-evidence-1",
            query="Evidence?",
            model="fixed",
            region="CN-BJ",
            mode="normal",
            adapter="fixture",
        ),
    )

    with psycopg.connect(POSTGRES_DSN) as connection:
        row = connection.execute(
            """
            SELECT task.answer_text,asset.object_key,asset.sha256,asset.mime_type,
                   anchor.text_start,anchor.text_end,anchor.bbox,anchor.quote_hash
            FROM platform.collection_task task
            JOIN evidence.evidence_relation relation
              ON relation.tenant_pub_id=%s AND relation.from_pub_id=task.pub_id
             AND relation.relation_type='answer_evidence_excerpt'
            JOIN evidence.evidence_asset asset
              ON asset.tenant_pub_id=relation.tenant_pub_id
             AND asset.pub_id=relation.to_pub_id
            JOIN evidence.evidence_anchor anchor
              ON anchor.tenant_pub_id=asset.tenant_pub_id
             AND anchor.evidence_pub_id=asset.pub_id
            WHERE task.business_key='answer-evidence-1'
              AND task.run_id=(SELECT id FROM platform.collection_run WHERE pub_id=%s)
            """,
            (tenant_pub_id, run_pub_id),
        ).fetchone()
    assert row is not None
    assert row[0] == answer_text
    assert row[4:6] == (0, len(answer_text))
    assert row[6] == bbox
    assert row[7] == sha256(answer_text.encode()).hexdigest()

    settings = get_settings()
    store = ContentAddressedObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
    )
    assert store.get_verified(str(row[1]), str(row[2])) == image_payload
    assert row[3] == "image/png"

    case = {
        "answer_pub_id": None,
        "_answer_quote_range": (0, len(answer_text)),
        "_answer_text": answer_text,
    }
    with psycopg.connect(POSTGRES_DSN) as connection:
        case["answer_pub_id"] = connection.execute(
            """
            SELECT task.pub_id FROM platform.collection_task task
            WHERE task.run_id=(SELECT id FROM platform.collection_run WHERE pub_id=%s)
              AND task.business_key='answer-evidence-1'
            """,
            (run_pub_id,),
        ).fetchone()[0]
    _attach_answer_case_assets(POSTGRES_DSN, tenant_pub_id, [case])
    assert case["answer_anchor"] == {
        "bbox": [10, 20, 120, 32],
        "method": "ocr_rapidocr_ppocrv6_v1",
        "label": "AI 回答命中表述",
    }
    facts = {"service2": {"delivery_v2": {"cases": [case], "supplemental_factcheck_cases": []}}}
    report_payloads = load_service2_answer_screenshots(facts, blob_loader=store.get_verified)
    loaded = report_payloads[str(case["answer_pub_id"])]
    assert loaded == image_payload
    _full, highlighted_crop, note = _answer_views(loaded, case["answer_anchor"])
    assert highlighted_crop is not None
    assert "红框" in note
    with Image.open(highlighted_crop) as rendered:
        assert rendered.width > 0 and rendered.height > 0

    with psycopg.connect(POSTGRES_DSN) as connection:
        evidence_json = connection.execute(
            """
            SELECT evidence_json FROM platform.collection_task
            WHERE run_id=(SELECT id FROM platform.collection_run WHERE pub_id=%s)
              AND business_key='answer-evidence-1'
            """,
            (run_pub_id,),
        ).fetchone()[0]
    persisted_ref = json.loads(evidence_json)[0]
    assert persisted_ref["anchors"][0]["bbox"] == bbox


def test_official_share_verification_is_persisted_without_exposing_image_contract(
    tmp_path: Path,
) -> None:
    with TestClient(app) as client:
        tenant_pub_id, run_pub_id = _bootstrap_run(client)

    checked_at = datetime(2026, 8, 18, 9, 30, tzinfo=UTC)
    share_url = "https://chat.deepseek.com/share/integration-safe"
    manifest_path = tmp_path / "answer-share-link.json"
    write_share_link_manifest(
        manifest_path,
        share_url=share_url,
        platform="deepseek",
        channel="create-and-copy",
        verification=ShareLinkVerification(
            checked_at=checked_at,
            availability_status="reachable",
            http_status=200,
            final_url=share_url,
            redirect_chain=(),
            allowlist_valid=True,
            content_hash=sha256(b"official answer").hexdigest(),
            embed_status="blocked",
            x_frame_options="DENY",
            csp_frame_ancestors=None,
            embed_reason="x_frame_options_restricts_embedding",
            failure_reason=None,
        ),
    )
    persist_collection_result(
        tenant_pub_id,
        run_pub_id,
        CollectionTaskResult(
            business_key="answer-share-1",
            answer_text="official answer",
            screenshot_ref="",
            quality_state="accepted",
            evidence=[
                CollectionEvidenceRef(
                    kind="share_link",
                    path=str(manifest_path),
                    relation_type="official_share_link",
                    mime_type="application/json",
                    source_url=share_url,
                )
            ],
        ),
        CollectionTaskInput(
            business_key="answer-share-1",
            query="Share?",
            model="deepseek",
            region="CN-BJ",
            mode="normal",
            adapter="deepseek",
        ),
    )

    with psycopg.connect(POSTGRES_DSN) as connection:
        artifact = connection.execute(
            """
            SELECT status,share_url,availability_status,http_status,checked_at,
                   embed_status,x_frame_options,share_link_evidence_pub_id,
                   share_image_evidence_pub_id
            FROM evidence.answer_share_artifact
            WHERE tenant_pub_id=%s AND answer_pub_id=(
              SELECT task.pub_id FROM platform.collection_task task
              WHERE task.run_id=(SELECT id FROM platform.collection_run WHERE pub_id=%s)
                AND task.business_key='answer-share-1'
            )
            """,
            (tenant_pub_id, run_pub_id),
        ).fetchone()
        event_count = connection.execute(
            """
            SELECT count(*) FROM evidence.answer_share_verification_event event
            JOIN evidence.answer_share_artifact artifact
              ON artifact.pub_id=event.artifact_pub_id
            WHERE artifact.tenant_pub_id=%s AND artifact.share_url=%s
            """,
            (tenant_pub_id, share_url),
        ).fetchone()[0]
    assert artifact is not None
    assert artifact[:7] == (
        "available",
        share_url,
        "reachable",
        200,
        checked_at,
        "blocked",
        "DENY",
    )
    assert artifact[7] is not None
    assert artifact[8] is None
    assert event_count == 1
