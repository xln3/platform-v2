#!/usr/bin/env python3
"""Generate formal-review artifacts through the production application service.

This command is an offline export seam only.  It accepts report scope, never caller-
supplied evidence IDs, object-store keys, input paths, or historical sidecars.  Facts,
evidence selection, formal gates, rendering, and LibreOffice conversion therefore use
the exact same code path as the API/Temporal production flow.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))

from geo_platform.config import get_settings  # noqa: E402
from geo_platform.evidence.object_store import ContentAddressedObjectStore  # noqa: E402
from geo_platform.evidence.service import EvidenceService  # noqa: E402
from geo_platform.reports.formal_production import (  # noqa: E402
    FormalProductionInvalid,
    FormalProductionRequest,
    FormalReportProductionService,
    FormalWindow,
    customer_fact_snapshot,
    request_contract,
)

from domain.reporting.libreoffice import ReportRuntimeDependencyError  # noqa: E402
from domain.reporting.service1_governance import release_state_label  # noqa: E402

_PUB_ID_RE = re.compile(r"^[a-z][A-Za-z0-9_]{4,119}$")


def _service_numbers(raw: str) -> tuple[int, ...]:
    try:
        values = tuple(int(value.strip()) for value in raw.split(",") if value.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("services 必须是逗号分隔的 1..4") from exc
    if (
        not values
        or len(values) != len(set(values))
        or any(value not in {1, 2, 3, 4} for value in values)
    ):
        raise argparse.ArgumentTypeError("services 必须是无重复的 1..4")
    return tuple(sorted(values))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="通过正式报告应用服务生成审阅产物")
    parser.add_argument("--tenant", required=True, help="tenant pub_id")
    parser.add_argument("--project", required=True, help="project pub_id")
    parser.add_argument("--services", type=_service_numbers, default=(1, 2, 3))
    parser.add_argument("--start", required=True, type=date.fromisoformat, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, type=date.fromisoformat, help="YYYY-MM-DD")
    parser.add_argument(
        "--document-status",
        choices=("internal_review", "delivery_candidate"),
        default="internal_review",
    )
    parser.add_argument("--version", default="V1.0")
    parser.add_argument("--prepared-by", default="GEO 项目组")
    parser.add_argument("--prepared-date", type=date.fromisoformat)
    parser.add_argument("--reviewed-by")
    parser.add_argument("--reviewed-date", type=date.fromisoformat)
    parser.add_argument("--before-start", type=date.fromisoformat)
    parser.add_argument("--before-end", type=date.fromisoformat)
    parser.add_argument("--after-start", type=date.fromisoformat)
    parser.add_argument("--after-end", type=date.fromisoformat)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--generated-at", type=datetime.fromisoformat)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-pdf", action="store_true")
    return parser.parse_args()


def _dsn(settings: Any) -> str:
    value = (
        os.getenv("GEO_RUNTIME_POSTGRES_DSN")
        or os.getenv("GEO_POSTGRES_DSN")
        or settings.postgres_dsn
    )
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _window(start: date | None, end: date | None, *, label: str) -> FormalWindow | None:
    if start is None and end is None:
        return None
    if start is None or end is None:
        raise FormalProductionInvalid(f"{label}_window_incomplete")
    return FormalWindow(start, end)


def _json_default(value: object) -> object:
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _write(path: Path, payload: bytes, *, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"输出已存在：{path}（覆盖请加 --force）")
    path.write_bytes(payload)


def _request(args: argparse.Namespace, generated_at: datetime) -> FormalProductionRequest:
    if not _PUB_ID_RE.fullmatch(args.tenant) or not _PUB_ID_RE.fullmatch(args.project):
        raise FormalProductionInvalid("invalid_public_id")
    before = _window(args.before_start, args.before_end, label="before")
    after = _window(args.after_start, args.after_end, label="after")
    contract = request_contract(
        project_pub_id=args.project,
        services=args.services,
        window=FormalWindow(args.start, args.end),
        document_status=args.document_status,
        candidate_group_strategy="preregistered_scope_v1",
        before_window=before,
        after_window=after,
        document_governance={
            "version": args.version,
            "prepared_by": args.prepared_by,
            "prepared_date": (args.prepared_date or generated_at.date()).isoformat(),
            "reviewed_by": args.reviewed_by,
            "reviewed_date": args.reviewed_date.isoformat() if args.reviewed_date else None,
        },
    )
    contract_json = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    request_hash = sha256(contract_json.encode()).hexdigest()
    return FormalProductionRequest(
        pub_id=f"frp_cli_{request_hash[:24]}",
        tenant_pub_id=args.tenant,
        project_pub_id=args.project,
        services=args.services,
        window=FormalWindow(args.start, args.end),
        document_status=args.document_status,
        candidate_group_strategy="preregistered_scope_v1",
        frozen_at=generated_at,
        created_by_pub_id="usr_formal_report_cli",
        request_hash=request_hash,
        before_window=before,
        after_window=after,
        document_governance=contract["document_governance"],
    )


def main() -> int:
    args = _arguments()
    generated_at = args.generated_at or datetime.now(UTC)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=UTC)
    try:
        request = _request(args, generated_at)
        settings = get_settings()
        dsn = _dsn(settings)
        store = ContentAddressedObjectStore(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
        )
        service = FormalReportProductionService(
            dsn=dsn,
            evidence=EvidenceService(dsn=dsn, store=store),
        )
        bundle = service.generate_offline(request)

        output_dir = args.output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = generated_at.astimezone().strftime("%Y%m%d")
        status_label = release_state_label(request.document_status)
        paths: list[Path] = []
        page_counts: dict[Path, int] = {}
        for service_number in request.services:
            service_facts = bundle.facts[service_number]
            client_name = str(
                service_facts.get("project_name") or service_facts.get("account_name") or "客户项目"
            )
            client_name = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", client_name).strip("_")
            scope_label = ""
            if service_number == 1:
                scope_label = str(
                    service_facts["service1"]["delivery_v3"]["scope"].get("scope_label") or ""
                )
                scope_label = "_" + re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", scope_label).strip(
                    "_"
                )
            stem = (
                f"{client_name}_服务{service_number}{scope_label}_"
                f"{args.version}_{status_label}_{stamp}"
            )
            manifest = json.loads(bundle.artifacts[service_number]["manifest"])
            for format_name in bundle.artifacts[service_number]:
                if format_name == "pdf" and args.skip_pdf:
                    continue
                suffix = "json" if format_name == "manifest" else format_name
                path = output_dir / f"{stem}.{suffix}"
                _write(path, bundle.artifacts[service_number][format_name], force=args.force)
                paths.append(path)
                if format_name == "pdf":
                    page_counts[path] = int(
                        manifest.get("artifacts", {}).get("pdf", {}).get("pages") or 0
                    )
            if manifest.get("publication_qa") is not None:
                qa_path = output_dir / f"{stem}_出版质量检查.json"
                _write(
                    qa_path,
                    json.dumps(
                        {
                            "publication_qa": manifest["publication_qa"],
                            "reexport_qa": manifest["reexport_qa"],
                        },
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ).encode(),
                    force=args.force,
                )
                paths.append(qa_path)

        facts_path = output_dir / f"{status_label}_冻结事实快照_{args.version}_{stamp}.json"
        customer_facts = {
            str(service_number): customer_fact_snapshot(bundle.facts[service_number])
            for service_number in request.services
        }
        _write(
            facts_path,
            json.dumps(
                {
                    "schema_version": "formal-report-fact-bundle-v1",
                    "fact_snapshot_hash": bundle.fact_snapshot_hash,
                    "services": customer_facts,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                default=_json_default,
            ).encode(),
            force=args.force,
        )
        paths.append(facts_path)
        checklist_path = output_dir / f"{status_label}_文件清单_{args.version}_{stamp}.json"
        checklist = {
            "schema_version": "formal-delivery-checklist-v1",
            "document_status": request.document_status,
            "release_status_label": status_label,
            "files": [
                {
                    "filename": path.name,
                    "absolute_path": str(path),
                    "byte_size": path.stat().st_size,
                    "sha256": sha256(path.read_bytes()).hexdigest(),
                    **({"pages": page_counts[path]} if path in page_counts else {}),
                }
                for path in paths
            ],
        }
        _write(
            checklist_path,
            json.dumps(checklist, ensure_ascii=False, indent=2, sort_keys=True).encode(),
            force=args.force,
        )
        paths.append(checklist_path)
    except (
        FileExistsError,
        FormalProductionInvalid,
        LookupError,
        ReportRuntimeDependencyError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for path in paths:
        print(f"{path}\t{path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
