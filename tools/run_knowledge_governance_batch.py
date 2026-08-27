#!/usr/bin/env python3
"""Run the bounded weekly knowledge-governance intake and health report.

The batch may contact SiliconIndex, but request-time GEO paths never do.  A
remote failure is recorded as degraded and the already-verified local snapshot
and knowledge release remain usable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
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
from geo_platform.knowledge.models import ConnectorRun  # noqa: E402
from geo_platform.knowledge.repository import KnowledgeRepository  # noqa: E402
from geo_platform.tenancy.database import SessionLocal  # noqa: E402
from geo_platform.tenancy.repository import TenantRepository  # noqa: E402

from domain.knowledge_evolution.release import KnowledgeReleaseStore  # noqa: E402
from domain.siliconindex import (  # noqa: E402
    SiliconIndexAdapter,
    SiliconIndexSyncError,
    SiliconIndexSynchronizer,
)

NAMESPACE = "geo-brandrank"
DOMAIN = "brand/entity-resolution"
ADAPTER = "siliconindex-static"


def _json_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def _write_report(root: Path, report: dict[str, Any]) -> Path:
    report_root = root / "governance-reports"
    report_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    digest = _json_hash(report).removeprefix("sha256:")[:12]
    target = report_root / f"{timestamp}-{digest}.json"
    temporary = report_root / f".{target.name}.tmp"
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return target


def _start_run(tenant_pub_id: str, *, local_release_id: str | None) -> str:
    with SessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        repository = KnowledgeRepository(session, tenant_pub_id)
        row = repository.create_connector_run(
            {
                "namespace": NAMESPACE,
                "domain": DOMAIN,
                "adapter": ADAPTER,
                "operation": "import",
                "status": "running",
                "local_release_id": local_release_id,
                "cursor": {"mode": "weekly_governance"},
            }
        )
        repository.audit(
            namespace=NAMESPACE,
            domain=DOMAIN,
            actor="system:knowledge-governance",
            action="connector_run.started",
            resource_type="connector_run",
            resource_pub_id=row.pub_id,
            receipt={"adapter": ADAPTER, "operation": "import"},
        )
        session.commit()
        return row.pub_id


def _finish_run(
    tenant_pub_id: str,
    run_pub_id: str,
    *,
    status: str,
    upstream_release_id: str | None,
    local_release_id: str | None,
    result: dict[str, Any],
    error_code: str | None,
) -> None:
    with SessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        row = session.scalar(
            select(ConnectorRun)
            .where(
                ConnectorRun.tenant_pub_id == tenant_pub_id,
                ConnectorRun.pub_id == run_pub_id,
            )
            .with_for_update()
        )
        if row is None:
            raise RuntimeError("connector_run_missing")
        row.status = status
        row.upstream_release_id = upstream_release_id
        row.local_release_id = local_release_id
        row.result = result
        row.error_code = error_code
        row.finished_at = datetime.now(UTC)
        repository = KnowledgeRepository(session, tenant_pub_id)
        repository.audit(
            namespace=NAMESPACE,
            domain=DOMAIN,
            actor="system:knowledge-governance",
            action=f"connector_run.{status}",
            resource_type="connector_run",
            resource_pub_id=row.pub_id,
            receipt={
                "adapter": ADAPTER,
                "operation": "import",
                "upstream_release_id": upstream_release_id,
                "local_release_id": local_release_id,
                "error_code": error_code,
            },
        )
        session.commit()


def run(*, tenant_pub_id: str, refresh: bool, snapshot_source: Path) -> dict[str, Any]:
    settings = get_settings()
    store = KnowledgeReleaseStore(settings.knowledge_release_dir)
    local_release_id = store.current_release_id()
    if local_release_id is not None:
        store.verify(local_release_id)
    run_pub_id = _start_run(tenant_pub_id, local_release_id=local_release_id)

    upstream_error: str | None = None
    sync_result: dict[str, Any] | None = None
    if refresh:
        try:
            sync_result = SiliconIndexSynchronizer(
                snapshot_source,
                settings.siliconindex_base_url,
            ).sync()
        except SiliconIndexSyncError as exc:
            upstream_error = type(exc).__name__

    adapter_result = None
    try:
        adapter_result = SiliconIndexAdapter().import_release(str(snapshot_source))
    except SiliconIndexSyncError as exc:
        error_code = type(exc).__name__
        _finish_run(
            tenant_pub_id,
            run_pub_id,
            status="failed",
            upstream_release_id=None,
            local_release_id=local_release_id,
            result={"local_release_verified": local_release_id is not None},
            error_code=error_code,
        )
        raise RuntimeError("no_valid_siliconindex_snapshot") from exc

    with SessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        metrics = KnowledgeRepository(session, tenant_pub_id).metrics()

    upstream_release_id = adapter_result.upstream_release_id
    status = "degraded" if upstream_error else "success"
    report = {
        "schema_version": "knowledge-governance-report-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "namespace": NAMESPACE,
        "domain": DOMAIN,
        "connector": {
            "adapter": ADAPTER,
            "adapter_version": SiliconIndexAdapter.adapter_version,
            "status": status,
            "upstream_release_id": upstream_release_id,
            "upstream_content_hash": adapter_result.result.get("content_hash"),
            "refresh_attempted": refresh,
            "refresh_error_code": upstream_error,
            "sync": sync_result,
        },
        "local": {
            "active_release_id": local_release_id,
            "verified": local_release_id is not None,
        },
        "queue": {
            "candidate_backlog": metrics["candidate_backlog"],
            "review_ready": metrics["review_ready"],
            "oldest_candidate_age_seconds": metrics["oldest_candidate_age_seconds"],
            "conflicts": metrics["conflicts"],
        },
        "required_actions": [
            action
            for condition, action in (
                (upstream_error is not None, "restore_upstream_and_reconcile"),
                (metrics["conflicts"] > 0, "adjudicate_merge_conflicts"),
                (metrics["candidate_backlog"] > 0, "review_candidate_backlog"),
            )
            if condition
        ],
    }
    report["report_hash"] = _json_hash(report)
    report_path = _write_report(Path(settings.knowledge_release_dir), report)
    result = {
        "report_path": str(report_path),
        "report_hash": report["report_hash"],
        "candidate_backlog": metrics["candidate_backlog"],
        "conflicts": metrics["conflicts"],
        "local_release_verified": local_release_id is not None,
    }
    _finish_run(
        tenant_pub_id,
        run_pub_id,
        status=status,
        upstream_release_id=upstream_release_id,
        local_release_id=local_release_id,
        result=result,
        error_code=upstream_error,
    )
    return {"status": status, "run_pub_id": run_pub_id, **result}


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tenant-pub-id",
        default=settings.knowledge_governance_tenant_pub_id,
    )
    parser.add_argument(
        "--snapshot-source",
        type=Path,
        default=Path(settings.siliconindex_snapshot_dir),
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="validate the existing last-known-good snapshot without network access",
    )
    args = parser.parse_args()
    if not args.tenant_pub_id:
        raise SystemExit("GEO_KNOWLEDGE_GOVERNANCE_TENANT_PUB_ID is required")
    try:
        result = run(
            tenant_pub_id=args.tenant_pub_id,
            refresh=not args.offline,
            snapshot_source=args.snapshot_source,
        )
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_code": type(exc).__name__}))
        raise SystemExit(1) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] == "degraded":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
