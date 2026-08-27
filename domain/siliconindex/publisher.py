"""Deterministic Git/Render publication for an approved SiliconIndex bundle."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .snapshot import SiliconIndexSyncError


def _run(arguments: Sequence[str], *, cwd: Path, timeout: float = 600) -> str:
    try:
        result = subprocess.run(  # noqa: S603 - every executable/argument is fixed or validated
            list(arguments),
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise SiliconIndexSyncError(f"publisher_command_failed:{arguments[0]}") from exc
    return result.stdout.strip()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SiliconIndexSyncError(f"publisher_json_invalid:{path.name}") from exc
    if not isinstance(value, dict):
        raise SiliconIndexSyncError(f"publisher_json_invalid:{path.name}")
    return value


def _file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _command_json(arguments: Sequence[str], *, cwd: Path) -> dict[str, Any]:
    output = _run(arguments, cwd=cwd)
    try:
        value = json.loads(output.splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise SiliconIndexSyncError("publisher_command_output_invalid") from exc
    if not isinstance(value, dict):
        raise SiliconIndexSyncError("publisher_command_output_invalid")
    return value


def _verify_public(
    base_url: str,
    *,
    release_id: str,
    content_hash: str,
    timeout_seconds: float,
    poll_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error = "public_manifest_not_observed"
    while time.monotonic() < deadline:
        url = f"{base_url.rstrip('/')}/manifest.json?release={release_id}&t={time.time_ns()}"
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "GeoPlatform-SiliconIndex-Publisher/1"},
            )
            with urllib.request.urlopen(  # noqa: S310 - configured HTTPS endpoint
                request,
                timeout=min(20.0, timeout_seconds),
            ) as response:
                document = json.loads(response.read(1024 * 1024).decode())
            if (
                isinstance(document, dict)
                and document.get("release_id") == release_id
                and document.get("content_hash") == content_hash
            ):
                return document
            last_error = "public_manifest_version_or_hash_mismatch"
        except (OSError, UnicodeError, ValueError, urllib.error.URLError) as exc:
            last_error = f"public_manifest_unavailable:{type(exc).__name__}"
        time.sleep(max(0.1, min(poll_seconds, 30.0)))
    raise SiliconIndexSyncError(last_error)


def _release_approval(
    *,
    change_approval: Mapping[str, Any],
    release_id: str,
    previous_release_id: str,
    content_hash: str,
    quality_report_path: Path,
) -> dict[str, Any]:
    return {
        "schema_version": "siliconindex-release-approval-v1",
        "decision": "approved",
        "release_id": release_id,
        "previous_release_id": previous_release_id,
        "content_hash": content_hash,
        "quality_report_hash": _file_hash(quality_report_path),
        "quality_gate": "passed",
        "historical_replay": {
            "status": "passed",
            "report_hash": change_approval["historical_replay_report_hash"],
        },
        "rollback_release_id": previous_release_id,
        "review_basis": list(change_approval["review_basis"]),
        "reviewers": list(change_approval["reviewers"]),
        "source_change_bundle_hash": change_approval["bundle_hash"],
    }


def preview_change_bundle(
    *,
    repository_url: str,
    branch: str,
    bundle_path: Path,
    release_id: str,
) -> dict[str, Any]:
    """Compute the deterministic public content hash without modifying or pushing."""

    if not bundle_path.is_file():
        raise SiliconIndexSyncError("publisher_artifact_missing")
    bundle = _json(bundle_path)
    temporary_root = Path(tempfile.mkdtemp(prefix="siliconindex-publisher-preview-"))
    checkout = temporary_root / "repo"
    try:
        _run(
            [
                "git",
                "clone",
                "--branch",
                branch,
                "--single-branch",
                "--",
                repository_url,
                str(checkout),
            ],
            cwd=temporary_root,
        )
        manifest = _json(checkout / "public/data/v1/manifest.json")
        if manifest.get("release_id") != bundle.get("base_upstream_release_id"):
            raise SiliconIndexSyncError("publisher_repository_base_mismatch")
        return _command_json(
            [
                "node",
                "scripts/apply-change-bundle.mjs",
                "--bundle-file",
                str(bundle_path.resolve()),
                "--release-id",
                release_id,
                "--mode",
                "preview",
            ],
            cwd=checkout,
        )
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def publish_change_bundle(
    *,
    repository_url: str,
    branch: str,
    bundle_path: Path,
    approval_path: Path,
    release_id: str,
    public_base_url: str,
    deploy_timeout_seconds: float = 600,
    poll_seconds: float = 10,
) -> dict[str, Any]:
    """Clone, apply, validate, commit, push, then verify the public release.

    A fresh clone is used for every attempt. A failed pre-push attempt therefore
    cannot dirty the operator checkout; a post-push retry recognizes the already
    published immutable release and only repeats public verification.
    """

    if not repository_url or not branch or not release_id:
        raise SiliconIndexSyncError("publisher_configuration_incomplete")
    if not bundle_path.is_file() or not approval_path.is_file():
        raise SiliconIndexSyncError("publisher_artifact_missing")
    approval = _json(approval_path)
    bundle = _json(bundle_path)
    if approval.get("bundle_hash") != _file_hash(bundle_path):
        raise SiliconIndexSyncError("publisher_bundle_approval_hash_mismatch")
    expected_hash = str(approval.get("result_content_hash") or "")
    if approval.get("target_release_id") != release_id or not expected_hash.startswith("sha256:"):
        raise SiliconIndexSyncError("publisher_approval_target_invalid")

    temporary_root = Path(tempfile.mkdtemp(prefix="siliconindex-publisher-"))
    checkout = temporary_root / "repo"
    try:
        _run(
            [
                "git",
                "clone",
                "--branch",
                branch,
                "--single-branch",
                "--",
                repository_url,
                str(checkout),
            ],
            cwd=temporary_root,
        )
        manifest_path = checkout / "public/data/v1/manifest.json"
        existing_manifest = _json(manifest_path)
        if existing_manifest.get("release_id") == release_id:
            if existing_manifest.get("content_hash") != expected_hash:
                raise SiliconIndexSyncError("publisher_existing_release_hash_mismatch")
            commit = _run(["git", "rev-parse", "HEAD"], cwd=checkout)
            public = _verify_public(
                public_base_url,
                release_id=release_id,
                content_hash=expected_hash,
                timeout_seconds=deploy_timeout_seconds,
                poll_seconds=poll_seconds,
            )
            return {
                "status": "already_published",
                "release_id": release_id,
                "content_hash": expected_hash,
                "git_commit": commit,
                "public_generated_at": public.get("generated_at"),
            }
        if existing_manifest.get("release_id") != bundle.get("base_upstream_release_id"):
            raise SiliconIndexSyncError("publisher_repository_base_mismatch")

        _run(["npm", "ci", "--ignore-scripts"], cwd=checkout, timeout=900)
        preview = _command_json(
            [
                "node",
                "scripts/apply-change-bundle.mjs",
                "--bundle-file",
                str(bundle_path.resolve()),
                "--release-id",
                release_id,
                "--mode",
                "preview",
            ],
            cwd=checkout,
        )
        if preview.get("result_content_hash") != expected_hash:
            raise SiliconIndexSyncError("publisher_preview_hash_mismatch")
        applied = _command_json(
            [
                "node",
                "scripts/apply-change-bundle.mjs",
                "--bundle-file",
                str(bundle_path.resolve()),
                "--approval-file",
                str(approval_path.resolve()),
                "--release-id",
                release_id,
                "--mode",
                "apply",
            ],
            cwd=checkout,
        )
        if (
            applied.get("result_content_hash") != expected_hash
            or applied.get("written") is not True
        ):
            raise SiliconIndexSyncError("publisher_apply_hash_mismatch")

        for command in (
            ["npm", "run", "build:search-index"],
            ["npm", "run", "build:graph"],
            ["npm", "run", "build:quality-report"],
            ["npm", "run", "validate:data"],
            ["npm", "test", "--", "--run"],
            ["npm", "run", "lint"],
            ["npm", "run", "build"],
        ):
            _run(command, cwd=checkout, timeout=900)

        build_approval_path = temporary_root / "release-approval.json"
        build_approval_path.write_text(
            json.dumps(
                _release_approval(
                    change_approval=approval,
                    release_id=release_id,
                    previous_release_id=str(bundle["base_upstream_release_id"]),
                    content_hash=expected_hash,
                    quality_report_path=checkout / "public/data/v1/quality-report.json",
                ),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        _run(
            [
                "node",
                "scripts/build-release.mjs",
                "--approval-file",
                str(build_approval_path),
                "--previous-release-id",
                str(bundle["base_upstream_release_id"]),
                "--release-id",
                release_id,
            ],
            cwd=checkout,
        )
        for command in (
            ["npm", "run", "build:bundles"],
            ["npm", "run", "build:summary"],
            ["npm", "run", "validate:data"],
        ):
            _run(command, cwd=checkout, timeout=900)
        _run(["git", "diff", "--check"], cwd=checkout)
        # The upstream repository still contains historical AppleDouble files.
        # They are unrelated to a governed knowledge release and a derived-data
        # rebuild may delete them. Keep those pre-existing paths unstaged so a
        # publication cannot smuggle repository cleanup into the data commit.
        _run(
            [
                "git",
                "add",
                "--all",
                "--",
                "public",
                ":(exclude)public/data/v1/bundles/._*",
            ],
            cwd=checkout,
        )
        staged = _run(["git", "diff", "--cached", "--name-only"], cwd=checkout)
        if not staged:
            raise SiliconIndexSyncError("publisher_no_deterministic_changes")
        _run(
            [
                "git",
                "-c",
                "user.name=GEO Knowledge Publisher",
                "-c",
                "user.email=knowledge-publisher@localhost",
                "commit",
                "-m",
                f"feat: publish governed knowledge release {release_id}",
            ],
            cwd=checkout,
        )
        commit = _run(["git", "rev-parse", "HEAD"], cwd=checkout)
        _run(["git", "push", "--porcelain", "origin", f"HEAD:{branch}"], cwd=checkout)
        public = _verify_public(
            public_base_url,
            release_id=release_id,
            content_hash=expected_hash,
            timeout_seconds=deploy_timeout_seconds,
            poll_seconds=poll_seconds,
        )
        return {
            "status": "published",
            "release_id": release_id,
            "content_hash": expected_hash,
            "git_commit": commit,
            "staged_files": staged.splitlines(),
            "public_generated_at": public.get("generated_at"),
        }
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


__all__ = ["preview_change_bundle", "publish_change_bundle"]
