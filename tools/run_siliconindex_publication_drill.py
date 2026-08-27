#!/usr/bin/env python3
"""Exercise bundle approval, Git push, static deployment, and public readback locally."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from domain.siliconindex import (  # noqa: E402
    preview_change_bundle,
    project_brand_domain,
    publish_change_bundle,
)
from domain.siliconindex.snapshot import validate_snapshot  # noqa: E402


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        del format, args


def _run(arguments: list[str], *, cwd: Path) -> str:
    result = subprocess.run(  # noqa: S603 - fixed local drill commands and paths
        arguments,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=900,
    )
    return result.stdout.strip()


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.tmp"
    temporary.write_bytes(_canonical(value))
    temporary.replace(path)


def _copy_repository(source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(
            ".git",
            "node_modules",
            ".next",
            "out",
            "coverage",
        ),
    )
    _run(["git", "init", "--initial-branch=main"], cwd=target)
    _run(["git", "config", "user.name", "Publication Drill"], cwd=target)
    _run(["git", "config", "user.email", "publication-drill@localhost"], cwd=target)
    _run(["git", "add", "--all"], cwd=target)
    _run(["git", "commit", "-m", "test: seed local publication drill"], cwd=target)
    _run(["git", "config", "receive.denyCurrentBranch", "updateInstead"], cwd=target)


def _next_release(value: str) -> str:
    date, separator, sequence = value.rpartition(".")
    if not separator or not sequence.isdigit():
        raise RuntimeError("publication_drill_base_release_unorderable")
    return f"{date}.{int(sequence) + 1000}"


def _bundle(source: Path, target: Path) -> tuple[dict[str, Any], str]:
    data_dir = source / "public" / "data" / "v1"
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    projection = project_brand_domain(data_dir, analysis_domain="cybersecurity")
    entity = next(
        value for value in projection["entities"] if value["entity_id"] == "CYB-BR-TENCENT"
    )
    attributes = {
        key: value
        for key, value in entity.items()
        if key not in {"knowledge_status", "origin", "sync_status"}
    }
    attributes["analysis_domain"] = "cybersecurity"
    attributes["eligibility_note"] = (
        str(attributes.get("eligibility_note") or "") + " [local-publication-drill]"
    ).strip()
    document = {
        "schema_version": "siliconindex-change-bundle-v1",
        "base_upstream_release_id": manifest["release_id"],
        "local_knowledge_release_id": "knowledge-local-publication-drill",
        "changes": [
            {
                "operation": "upsert",
                "stable_id": entity["entity_id"],
                "object_type": entity["entity_type"],
                "attributes": attributes,
                "origin": "local_publication_drill",
                "review_status": "reviewed",
                "visibility": "public",
                "sync_status": "local_ahead",
                "version": 999,
            }
        ],
    }
    _write(target, document)
    return document, _next_release(str(manifest["release_id"]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-repository", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = args.source_repository.resolve()
    if not (source / "scripts" / "apply-change-bundle.mjs").is_file():
        raise SystemExit("publication_drill_source_invalid")

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="siliconindex-publication-drill-") as raw_root:
        root = Path(raw_root)
        deployed = root / "deployed"
        _copy_repository(source, deployed)
        bundle_path = root / "bundle.json"
        bundle, release_id = _bundle(deployed, bundle_path)
        preview = preview_change_bundle(
            repository_url=str(deployed),
            branch="main",
            bundle_path=bundle_path,
            release_id=release_id,
        )
        approval = {
            "schema_version": "siliconindex-change-bundle-approval-v1",
            "decision": "approved",
            "bundle_hash": _file_hash(bundle_path),
            "base_upstream_release_id": bundle["base_upstream_release_id"],
            "local_knowledge_release_id": bundle["local_knowledge_release_id"],
            "target_release_id": release_id,
            "result_content_hash": preview["result_content_hash"],
            "historical_replay_report_hash": "sha256:" + "1" * 64,
            "reviewers": ["drill:independent-reviewer", "drill:release-publisher"],
            "review_basis": ["drill:change-set", "drill:historical-replay"],
        }
        approval_path = root / "approval.json"
        _write(approval_path, approval)

        handler = partial(
            _QuietHandler,
            directory=str(deployed / "public" / "data" / "v1"),
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        public_base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            published = publish_change_bundle(
                repository_url=str(deployed),
                branch="main",
                bundle_path=bundle_path,
                approval_path=approval_path,
                release_id=release_id,
                public_base_url=public_base_url,
                deploy_timeout_seconds=30,
                poll_seconds=0.1,
            )
            repeated = publish_change_bundle(
                repository_url=str(deployed),
                branch="main",
                bundle_path=bundle_path,
                approval_path=approval_path,
                release_id=release_id,
                public_base_url=public_base_url,
                deploy_timeout_seconds=30,
                poll_seconds=0.1,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        manifest = validate_snapshot(deployed / "public" / "data" / "v1")
        report = {
            "schema_version": "siliconindex-publication-drill-v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "source_repository_commit": _run(["git", "rev-parse", "HEAD^"], cwd=deployed),
            "target_release_id": release_id,
            "preview_content_hash": preview["result_content_hash"],
            "first_publish": published,
            "idempotent_retry": repeated,
            "deployed_manifest": {
                "release_id": manifest["release_id"],
                "content_hash": manifest["content_hash"],
                "schema_version": manifest["schema_version"],
            },
            "checks": {
                "normal_git_push": published["status"] == "published",
                "public_manifest_readback": published["content_hash"] == manifest["content_hash"],
                "full_static_snapshot_validation": True,
                "idempotent_retry": repeated["status"] == "already_published",
                "source_worktree_not_used_as_push_target": True,
            },
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
        report["report_hash"] = "sha256:" + hashlib.sha256(_canonical(report)).hexdigest()
        if args.output is not None:
            _write(args.output.resolve(), report)
        print(
            json.dumps(
                {
                    "release_id": release_id,
                    "content_hash": manifest["content_hash"],
                    "status": published["status"],
                    "retry_status": repeated["status"],
                    "report_hash": report["report_hash"],
                }
            )
        )


if __name__ == "__main__":
    main()
