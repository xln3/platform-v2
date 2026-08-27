#!/usr/bin/env python3
"""Idempotently copy legacy GEO brand observations into the shared namespace."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
API_ROOT = PROJECT_ROOT / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from geo_platform.config import get_settings  # noqa: E402
from geo_platform.knowledge.models import Observation  # noqa: E402
from geo_platform.knowledge.repository import KnowledgeRepository  # noqa: E402
from geo_platform.tenancy.database import SessionLocal  # noqa: E402
from geo_platform.tenancy.repository import TenantRepository  # noqa: E402

from domain.knowledge_evolution.contracts import ObservationDraft  # noqa: E402

SOURCE_NAMESPACE = "geo-brandrank"
TARGET_NAMESPACE = "shared"
DOMAIN = "brand/entity-resolution"
_SAFE_CONTEXT_FIELDS = {
    "analysis_domain",
    "comparison_scopes",
    "task",
    "region",
    "audience",
}
_SAFE_PAYLOAD_FIELDS = {
    "knowledge_status",
    "confidence",
    "model_provider",
    "model",
    "prompt_version",
    "caller_safe_context_hash",
}


def _safe_context(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    safe = {key: parsed[key] for key in sorted(_SAFE_CONTEXT_FIELDS) if key in parsed}
    return json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_payload(value: dict[str, Any], source_pub_id: str) -> dict[str, Any]:
    payload = {key: value[key] for key in sorted(_SAFE_PAYLOAD_FIELDS) if key in value}
    payload["namespace_migration"] = "geo-brandrank-to-shared-v1"
    payload["source_observation_hash"] = (
        "sha256:" + hashlib.sha256(source_pub_id.encode()).hexdigest()
    )
    payload["policy_version"] = "shared-brand-observation-v2"
    return payload


def run(tenant_pub_id: str) -> dict[str, int]:
    with SessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        rows = list(
            session.scalars(
                select(Observation)
                .where(
                    Observation.tenant_pub_id == tenant_pub_id,
                    Observation.namespace == SOURCE_NAMESPACE,
                    Observation.domain == DOMAIN,
                )
                .order_by(Observation.observed_at, Observation.pub_id)
            )
        )
        drafts = tuple(
            ObservationDraft(
                namespace=TARGET_NAMESPACE,
                domain=DOMAIN,
                task=row.task,
                surface_form=row.surface_form,
                normalized_key=row.normalized_key,
                source_type=row.source_type,
                source_ref_hash=row.source_ref_hash,
                idempotency_key=hashlib.sha256(
                    f"geo-brandrank-to-shared-v1|{row.pub_id}".encode()
                ).hexdigest(),
                safe_context=_safe_context(row.safe_context),
                data_classification=row.data_classification,
                visibility=(
                    "public"
                    if row.visibility == "public" and row.data_classification == "public"
                    else "tenant"
                ),
                payload=_safe_payload(dict(row.payload), row.pub_id),
            )
            for row in rows
        )
        repository = KnowledgeRepository(session, tenant_pub_id)
        inserted = repository.record_observations(tenant_pub_id, drafts)
        repository.audit(
            namespace=TARGET_NAMESPACE,
            domain=DOMAIN,
            actor="migration:shared-brand-observations-v1",
            action="observation.namespace_migrated",
            resource_type="observation_batch",
            resource_pub_id="shared-brand-observations-v1",
            receipt={"source": len(rows), "inserted": inserted},
        )
        session.commit()
        return {"source": len(rows), "inserted": inserted, "duplicate": len(rows) - inserted}


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tenant-pub-id",
        default=settings.knowledge_governance_tenant_pub_id,
    )
    args = parser.parse_args()
    if not args.tenant_pub_id:
        raise SystemExit("GEO_KNOWLEDGE_GOVERNANCE_TENANT_PUB_ID is required")
    print(json.dumps(run(args.tenant_pub_id), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
