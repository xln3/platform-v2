#!/usr/bin/env python3
"""Verify a quotation template manifest and fail closed for production use."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = REPO_ROOT / "api/geo_platform/quotations/assets"
DEFAULT_MANIFEST = ASSET_DIR / "quotation-template-v1.yaml"
KNOWN_SCHEMA = "quotation-template-manifest-v1"
KNOWN_IDENTITIES = {
    "geo-quotation-v1": {
        "template_version": "v1",
        "manifest_name": "quotation-template-v1.yaml",
        "canonical_name": "quotation-template-v1.docx",
        "sha256": "90ae5beb10ab3bacea3b706a2068945f828e275784e99da6b72dc44f8b0d9913",
    }
}


def _fail(code: str, detail: str) -> int:
    print(json.dumps({"ok": False, "code": code, "detail": detail}, ensure_ascii=False))
    return 1


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"manifest_unreadable:{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("manifest_root_invalid")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="校验报价模板身份、摘要、批准状态和正式出单资格。")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--require-production",
        action="store_true",
        help="除模板批准外，还要求 manifest 明确允许正式出单。",
    )
    args = parser.parse_args()
    manifest_path = args.manifest.expanduser().resolve()
    if not manifest_path.is_file():
        return _fail("quotation_template_missing", f"manifest:{manifest_path}")
    try:
        manifest = _load_manifest(manifest_path)
    except ValueError as exc:
        return _fail("quotation_template_manifest_invalid", str(exc))

    if manifest.get("schema_version") != KNOWN_SCHEMA:
        return _fail(
            "quotation_template_version_unknown",
            str(manifest.get("schema_version") or "missing"),
        )
    required = (
        "template_id",
        "template_version",
        "status",
        "approval_status",
        "canonical_path",
        "sha256",
    )
    missing = [field for field in required if not manifest.get(field)]
    if missing:
        return _fail("quotation_template_manifest_invalid", ",".join(missing))
    identity = KNOWN_IDENTITIES.get(str(manifest["template_id"]))
    if identity is None or manifest["template_version"] != identity["template_version"]:
        return _fail(
            "quotation_template_version_unknown",
            f"template_id={manifest['template_id']};version={manifest['template_version']}",
        )
    expected_manifest = ASSET_DIR / str(identity["manifest_name"])
    if manifest_path != expected_manifest.resolve():
        return _fail(
            "quotation_template_manifest_invalid",
            f"manifest_path={manifest_path};expected={expected_manifest.resolve()}",
        )
    if (
        manifest["canonical_path"] != identity["canonical_name"]
        or manifest["sha256"] != identity["sha256"]
    ):
        return _fail(
            "quotation_template_hash_mismatch",
            "manifest identity differs from the approved registry",
        )
    if manifest["status"] != "approved" or manifest["approval_status"] != "approved":
        return _fail(
            "quotation_template_not_approved",
            f"status={manifest['status']};approval_status={manifest['approval_status']}",
        )

    template_path = ASSET_DIR / str(identity["canonical_name"])
    if not template_path.is_file():
        return _fail("quotation_template_missing", str(template_path))
    try:
        actual_sha256 = _sha256(template_path)
    except OSError as exc:
        return _fail("quotation_template_missing", f"{template_path}:{exc}")
    if actual_sha256 != identity["sha256"]:
        return _fail(
            "quotation_template_hash_mismatch",
            f"expected={identity['sha256']};actual={actual_sha256}",
        )

    production = manifest.get("production_use")
    production_status = production.get("status") if isinstance(production, dict) else "unspecified"
    if args.require_production and production_status != "approved":
        return _fail(
            "quotation_template_not_approved",
            f"production_use.status={production_status}",
        )

    print(
        json.dumps(
            {
                "ok": True,
                "canonical_template": str(template_path),
                "template_id": manifest["template_id"],
                "template_version": manifest["template_version"],
                "template_sha256": actual_sha256,
                "approval_status": manifest["approval_status"],
                "production_use_status": production_status,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
