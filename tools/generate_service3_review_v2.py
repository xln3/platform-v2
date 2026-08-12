#!/usr/bin/env python3
"""Generate the Service 3 V2 evidence-chain review report from current facts."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))

from geo_platform.config import get_settings  # noqa: E402
from geo_platform.evidence.object_store import ContentAddressedObjectStore  # noqa: E402
from geo_platform.reports.service3_review_v2 import (  # noqa: E402
    build_service3_review_v2_facts,
)

from domain.reporting.formal_review_service3_docx import (  # noqa: E402
    render_service3_v2_docx,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成服务 3 V2 官网引用证据链报告")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--generated-at", type=datetime.fromisoformat)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def _dsn() -> str:
    settings = get_settings()
    value = (
        os.getenv("GEO_RUNTIME_POSTGRES_DSN")
        or os.getenv("GEO_POSTGRES_DSN")
        or settings.postgres_dsn
    )
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _json_default(value: object) -> object:
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _write(path: Path, payload: bytes, *, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"输出已存在：{path}（覆盖请加 --force）")
    path.write_bytes(payload)


def _asset_payloads(facts: dict[str, Any], store: ContentAddressedObjectStore) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    for case in facts["selected_evidence_cases"]:
        for key in ("answer_screenshot", "official_screenshot"):
            descriptor = case.get(key)
            if not isinstance(descriptor, dict):
                continue
            pub_id = str(descriptor.get("pub_id") or "")
            object_key = str(descriptor.get("object_key") or "")
            expected_sha256 = str(descriptor.get("sha256") or "")
            if pub_id and object_key and expected_sha256 and pub_id not in payloads:
                payloads[pub_id] = store.get_verified(object_key, expected_sha256)
    return payloads


def _pdf(docx_path: Path) -> Path:
    result = subprocess.run(
        [
            "/usr/bin/python3",
            str(ROOT / "tools" / "refresh_docx_indexes.py"),
            "--pdf",
            str(docx_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=240,
    )
    pdf_path = docx_path.with_suffix(".pdf")
    if result.returncode or not pdf_path.exists() or not pdf_path.stat().st_size:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "PDF 转换失败")
    return pdf_path


def main() -> int:
    args = _arguments()
    if args.start > args.end:
        raise ValueError("日期窗口无效：start > end")
    generated_at = args.generated_at or datetime.now(UTC)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=UTC)
    settings = get_settings()
    store = ContentAddressedObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
    )
    facts = build_service3_review_v2_facts(
        dsn=_dsn(),
        blob_loader=store.get_verified,
        tenant_pub_id=args.tenant,
        project_pub_id=args.project,
        start=args.start,
        end=args.end,
        generated_at=generated_at,
    )
    assets = _asset_payloads(facts, store)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = generated_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    stem = f"服务3_官网内容AI引用能效评估_预正式审阅稿_V2_{stamp}"
    docx_path = output_dir / f"{stem}.docx"
    facts_path = output_dir / f"服务3_官网引用证据链_事实快照V2_{stamp}.json"
    _write(docx_path, render_service3_v2_docx(facts, evidence_assets=assets), force=args.force)
    _write(
        facts_path,
        json.dumps(facts, ensure_ascii=False, indent=2, default=_json_default).encode(),
        force=args.force,
    )
    pdf_path = _pdf(docx_path)
    manifest = {
        "schema_version": "service3-review-v2-manifest-v1",
        "project_pub_id": args.project,
        "window": facts["window"],
        "generated_at": generated_at,
        "loaded_evidence_assets": sorted(assets),
        "files": [
            {
                "name": path.name,
                "bytes": path.stat().st_size,
                "sha256": sha256(path.read_bytes()).hexdigest(),
            }
            for path in (docx_path, pdf_path, facts_path)
        ],
    }
    manifest_path = output_dir / f"服务3_官网引用证据链_产物清单V2_{stamp}.json"
    _write(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2, default=_json_default).encode(),
        force=args.force,
    )
    for path in (docx_path, pdf_path, facts_path, manifest_path):
        print(f"{path}\t{path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
