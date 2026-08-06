from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from certify_production_outbox_trace import database_dsn
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.evidence.service import EvidenceService

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "tests/s04-evidence/production-evidence-occurrence-identity.json"


def provenance(access_class: AccessClass, captured_at: datetime) -> RedactedProvenance:
    return RedactedProvenance(
        platform_account_pub_id=None,
        browser_profile_version_pub_id=None,
        session_event_pub_id=None,
        channel=CaptureChannel.API,
        authorization_scope=("read",),
        adapter_version="s04-production-probe",
        capture_time=captured_at,
        access_class=access_class,
    )


def main() -> None:
    dsn = database_dsn()
    store = ContentAddressedObjectStore(
        endpoint=os.environ["GEO_MINIO_ENDPOINT"],
        access_key=os.environ["GEO_MINIO_ACCESS_KEY"],
        secret_key=os.environ["GEO_MINIO_SECRET_KEY"],
    )
    store.ensure_bucket()
    service = EvidenceService(dsn=dsn, store=store)
    suffix = secrets.token_hex(12)
    tenant = f"tnt_evidence_occurrence_probe_{suffix}"
    first_id = f"evd_occurrence_a_{suffix}"
    second_id = f"evd_occurrence_b_{suffix}"
    captured_at = datetime.now(UTC)
    payload = b"same redacted bytes with isolated occurrence metadata"
    first_args = {
        "evidence_pub_id": first_id,
        "tenant_pub_id": tenant,
        "project_pub_id": f"prj_occurrence_a_{suffix}",
        "kind": "answer_screenshot",
        "payload": payload,
        "mime_type": "image/png",
        "source_url": "https://example.com/public",
        "provenance": provenance(AccessClass.PUBLIC, captured_at),
    }
    object_key: str | None = None
    try:
        first = service.capture(**first_args)
        replay = service.capture(**first_args)
        second = service.capture(
            evidence_pub_id=second_id,
            tenant_pub_id=tenant,
            project_pub_id=f"prj_occurrence_b_{suffix}",
            kind="answer_screenshot",
            payload=payload,
            mime_type="image/png",
            source_url="https://example.com/private",
            provenance=provenance(AccessClass.CUSTOMER_PRIVATE, captured_at),
        )
        object_key = first.key
        drift_rejected = False
        try:
            service.capture(**(first_args | {"project_pub_id": f"prj_drift_{suffix}"}))
        except ValueError:
            drift_rejected = True
        with psycopg.connect(dsn) as connection:
            rows = connection.execute(
                """
                SELECT pub_id,project_pub_id,access_class,source_url,object_key
                FROM evidence.evidence_asset
                WHERE tenant_pub_id=%s ORDER BY pub_id
                """,
                (tenant,),
            ).fetchall()
        assertions = {
            "exact_replay_returns_same_occurrence": replay.metadata_pub_id == first_id,
            "same_content_has_two_occurrence_rows": len(rows) == 2,
            "same_content_shares_cas_object": first.key == replay.key == second.key,
            "access_and_project_metadata_isolated": len({(row[1], row[2], row[3]) for row in rows})
            == 2,
            "stable_id_payload_drift_rejected": drift_rejected,
        }
        evidence = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "result": "passed" if all(assertions.values()) else "failed",
            "database_revision": "s04_0021",
            "assertions": assertions,
            "synthetic_fixture": True,
            "synthetic_fixture_removed": True,
            "sensitive_values_recorded": False,
        }
        OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        if evidence["result"] != "passed":
            raise RuntimeError("production_evidence_occurrence_identity_failed")
        print(json.dumps({"result": "passed", "assertions": len(assertions)}))
    finally:
        with psycopg.connect(dsn) as connection:
            connection.execute(
                "DELETE FROM evidence.evidence_asset WHERE tenant_pub_id=%s", (tenant,)
            )
        if object_key is not None:
            store.delete(object_key)


if __name__ == "__main__":
    main()
