#!/usr/bin/env python3
"""Generate the corrected Service-2 V2 DOCX/PDF and visual-evidence audit files."""

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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))

from geo_platform.config import get_settings  # noqa: E402
from geo_platform.evidence.object_store import ContentAddressedObjectStore  # noqa: E402
from geo_platform.reports.formal_review import build_formal_review_facts  # noqa: E402
from geo_platform.reports.formal_review_service2 import (  # noqa: E402
    enrich_service2_v2_facts,
    load_service2_answer_screenshots,
)

from domain.reporting.formal_review_service2_docx import (  # noqa: E402
    render_service2_v2_docx,
)
from domain.reporting.service2_source_capture import (  # noqa: E402
    capture_service2_source_screenshots,
    persist_source_capture_images,
    source_capture_manifest,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成服务 2 V2 可视证据审阅报告")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--answer-anchors", type=Path)
    parser.add_argument("--generated-at", type=datetime.fromisoformat)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-pdf", action="store_true")
    parser.add_argument("--skip-source-capture", action="store_true")
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


def _anchors(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value.get("answers") if isinstance(value, dict) else None
    if not isinstance(rows, dict):
        raise ValueError("answer_anchor_sidecar_invalid")
    return {
        str(answer_id): [dict(item) for item in items if isinstance(item, dict)]
        for answer_id, items in rows.items()
        if isinstance(items, list)
    }


def _convert_pdf(docx_path: Path, output_dir: Path) -> Path:
    del output_dir
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
    if result.returncode != 0 or not pdf_path.is_file() or not pdf_path.stat().st_size:
        raise RuntimeError(
            f"Word 目录更新/PDF 转换失败：{result.stderr.strip() or result.stdout.strip()}"
        )
    return pdf_path


def _refresh_docx(docx_path: Path) -> None:
    result = subprocess.run(
        [
            "/usr/bin/python3",
            str(ROOT / "tools" / "refresh_docx_indexes.py"),
            str(docx_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=240,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Word 目录更新失败：{result.stderr.strip() or result.stdout.strip()}")


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _service2_audit_snapshot(facts: dict[str, Any]) -> dict[str, Any]:
    """Exclude the obsolete execution-row summary from the V2 audit file."""

    snapshot = _without_internal_refs(
        {
            "schema_version": "service2-review-audit-v2",
            "document_status": facts["document_status"],
            "project_pub_id": facts["project_pub_id"],
            "project_name": facts["project_name"],
            "target_brand": facts["target_brand"],
            "competitors": facts["competitors"],
            "window": facts["window"],
            "generated_at": facts["generated_at"],
            "service2": facts["service2"]["delivery_v2"],
        }
    )
    if not isinstance(snapshot, dict):  # defensive: the source root is always a mapping
        raise TypeError("service2_audit_snapshot_invalid")
    return snapshot


def _without_internal_refs(value: Any) -> Any:
    """Remove opaque operator-only judgment references from review artifacts."""

    if isinstance(value, dict):
        return {
            key: _without_internal_refs(child)
            for key, child in value.items()
            if key not in {"audit_refs", "judgment_pub_ids"}
        }
    if isinstance(value, list):
        return [_without_internal_refs(child) for child in value]
    return value


def main() -> int:
    args = _arguments()
    if args.start > args.end:
        print("日期窗口无效：start > end", file=sys.stderr)
        return 2
    generated_at = args.generated_at or datetime.now(UTC)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=UTC)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        dsn = _dsn()
        facts = build_formal_review_facts(
            dsn=dsn,
            tenant_pub_id=args.tenant,
            project_pub_id=args.project,
            start=args.start,
            end=args.end,
            generated_at=generated_at,
        )
        facts = enrich_service2_v2_facts(
            dsn=dsn,
            tenant_pub_id=args.tenant,
            facts=facts,
            answer_anchor_overrides=_anchors(args.answer_anchors),
        )
        delivery = facts["service2"]["delivery_v2"]
        settings = get_settings()
        store = ContentAddressedObjectStore(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
        )
        answer_screenshots = load_service2_answer_screenshots(
            facts,
            blob_loader=store.get_verified,
            allowed_root=ROOT,
        )
        captures = (
            {}
            if args.skip_source_capture
            else capture_service2_source_screenshots(delivery["cases"])
        )
        image_dir = output_dir / "服务2_事实核查网页截图_V2"
        persisted_images = persist_source_capture_images(captures, image_dir)
        capture_audit = source_capture_manifest(captures)
        for url, path in persisted_images.items():
            capture_audit[url]["screenshot_file"] = str(
                Path(path).resolve().relative_to(output_dir)
            )
        delivery["source_capture_manifest"] = capture_audit
        delivery["visual_asset_counts"] = {
            "answer_screenshots_loaded": len(answer_screenshots),
            "source_urls": len(captures),
            "source_pages_captured": sum(
                row.get("capture_status") == "captured" for row in captures.values()
            ),
            "source_pages_usable": sum(
                row.get("capture_status") == "captured"
                and row.get("content_status") == "ok"
                and bool(row.get("matched_terms"))
                for row in captures.values()
            ),
        }

        stamp = generated_at.astimezone().strftime("%Y%m%d")
        stem = f"服务2_品牌GEO内容生态风险核查_预正式审阅稿_V2_{stamp}"
        docx_path = output_dir / f"{stem}.docx"
        _write(
            docx_path,
            render_service2_v2_docx(
                facts,
                answer_screenshots=answer_screenshots,
                source_captures=captures,
            ),
            force=args.force,
        )
        audit_path = output_dir / f"服务2_审计事实快照_V2_{stamp}.json"
        _write(
            audit_path,
            json.dumps(
                _service2_audit_snapshot(facts),
                ensure_ascii=False,
                indent=2,
                default=_json_default,
            ).encode("utf-8"),
            force=args.force,
        )
        capture_path = output_dir / f"服务2_网页快照审计清单_V2_{stamp}.json"
        _write(
            capture_path,
            json.dumps(capture_audit, ensure_ascii=False, indent=2).encode("utf-8"),
            force=args.force,
        )
        if args.skip_pdf:
            _refresh_docx(docx_path)
            pdf_path = None
        else:
            pdf_path = _convert_pdf(docx_path, output_dir)
        outputs = [docx_path, audit_path, capture_path, *persisted_images.values()]
        if pdf_path is not None:
            outputs.append(pdf_path)
        output_paths = [Path(value) for value in outputs]
        manifest_path = output_dir / f"服务2_审阅产物清单_V2_{stamp}.json"
        manifest = {
            "schema_version": "service2-review-manifest-v2",
            "document_status": facts["document_status"],
            "files": [
                {
                    "name": str(path.resolve().relative_to(output_dir)),
                    "bytes": path.stat().st_size,
                    "sha256": _sha(path),
                }
                for path in output_paths
            ],
        }
        _write(
            manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            force=args.force,
        )
    except (FileExistsError, LookupError, ValueError, RuntimeError) as exc:
        print(type(exc).__name__, file=sys.stderr)
        return 2
    for path in [*output_paths, manifest_path]:
        print(f"{path}\t{path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
