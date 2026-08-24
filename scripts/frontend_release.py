"""Prepare and atomically activate the five production React applications.

The production Nginx configuration serves each application's ``build/client``
directory directly. A normal React Router build empties that directory before
writing it, so running a production build in place can expose a partial bundle.
This tool builds into isolated ``build-release`` directories, verifies immutable
manifests, and uses Linux ``renameat2(RENAME_EXCHANGE)`` to swap whole build
directories without a missing-path window.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import errno
import fcntl
import hashlib
import html
import json
import os
import re
import signal
import stat
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).parents[1]
RELEASES_ROOT = ROOT / ".frontend-releases"
NGINX_CONFIGS = (
    ROOT / "deploy/production/nginx-v2-locations.conf",
    ROOT / "deploy/production/geo-platform-v2-port-edges.conf",
)
APPS: dict[str, str] = {
    "customer-web": "/platform/customer/",
    "operations-web": "/platform/operations/",
    "report-studio": "/platform/reports/",
    "intelligence-web": "/platform/intelligence/",
    "intake-form": "/platform/intake-form/",
}
SHARED_PACKAGES = (
    "api-client",
    "auth",
    "charts",
    "design-system",
    "domain-types",
    "evidence-viewer",
    "workflow-ui",
)
ROOT_INPUTS = (
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "turbo.json",
    "tsconfig.base.json",
    "contracts/openapi.json",
    "contracts/generated-manifest.json",
)
IGNORED_SOURCE_PARTS = frozenset(
    {
        ".git",
        ".react-router",
        ".turbo",
        "build",
        "build-e2e",
        "build-release",
        "coverage",
        "dist",
        "node_modules",
    }
)
FORBIDDEN_BUNDLE_MARKERS = (
    b"CONTRACTFIXTURE",
    b"customer-contract-fixture",
    b"operator-contract-fixture",
    b"analyst-contract-fixture",
    b"reviewer-contract-fixture",
)
TEXT_BUNDLE_SUFFIXES = frozenset({".css", ".html", ".js", ".json", ".mjs", ".svg"})
RELEASE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
HTML_REFERENCE_PATTERN = re.compile(r"""(?:src|href)=["']([^"'<>]+)["']""", re.IGNORECASE)
RENAME_EXCHANGE = 2
AT_FDCWD = -100
RETRYABLE_RELEASE_STATUSES = frozenset(
    {
        "prepared",
        "rolled_back",
        "rolled_back_after_failed_activation",
    }
)
VALIDATABLE_RELEASE_STATUSES = RETRYABLE_RELEASE_STATUSES | frozenset(
    {
        "active_verified",
        "verification_pending",
    }
)


class ReleaseError(RuntimeError):
    """A stable, secret-free release failure."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError("release_manifest_unreadable") from exc
    if not isinstance(value, dict):
        raise ReleaseError("release_manifest_not_an_object")
    return value


def validated_release_id(value: str) -> str:
    if RELEASE_ID_PATTERN.fullmatch(value) is None:
        raise ReleaseError("invalid_release_id")
    return value


def release_directory(release_id: str, releases_root: Path = RELEASES_ROOT) -> Path:
    return releases_root / validated_release_id(release_id)


def default_release_id() -> str:
    return datetime.now(UTC).strftime("s03-%Y%m%dT%H%M%SZ")


def source_roots(root: Path = ROOT) -> tuple[Path, ...]:
    paths = [root / item for item in ROOT_INPUTS]
    paths.extend(root / "apps" / app for app in APPS)
    paths.extend(root / "packages" / package for package in SHARED_PACKAGES)
    return tuple(paths)


def iter_source_files(root: Path = ROOT) -> Iterator[Path]:
    for source_root in source_roots(root):
        if not source_root.exists():
            raise ReleaseError(f"release_input_missing:{source_root.relative_to(root)}")
        if source_root.is_symlink():
            raise ReleaseError(f"release_input_symlink:{source_root.relative_to(root)}")
        if source_root.is_file():
            yield source_root
            continue
        for candidate in sorted(source_root.rglob("*")):
            relative = candidate.relative_to(root)
            if any(part in IGNORED_SOURCE_PARTS for part in relative.parts):
                continue
            if candidate.is_symlink():
                raise ReleaseError(f"release_input_symlink:{relative}")
            if candidate.is_file():
                yield candidate


def source_fingerprint(root: Path = ROOT) -> dict[str, Any]:
    digest = hashlib.sha256()
    count = 0
    total_bytes = 0
    for path in sorted(
        set(iter_source_files(root)), key=lambda item: item.relative_to(root).as_posix()
    ):
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        file_hash = sha256_file(path)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\0")
        count += 1
        total_bytes += size
    return {"sha256": digest.hexdigest(), "files": count, "bytes": total_bytes}


def _bundle_reference_path(reference: str, basename: str) -> str | None:
    decoded = html.unescape(reference)
    parsed = urlsplit(decoded)
    if parsed.scheme or parsed.netloc or decoded.startswith(("#", "data:", "mailto:", "tel:")):
        return None
    path = unquote(parsed.path)
    if path.startswith(basename):
        path = path[len(basename) :]
    elif path.startswith("/"):
        raise ReleaseError("bundle_cross_origin_or_wrong_basename_reference")
    while path.startswith("./"):
        path = path[2:]
    if not path:
        return None
    parts = Path(path).parts
    if ".." in parts:
        raise ReleaseError("bundle_parent_path_reference")
    return Path(*parts).as_posix()


def inspect_bundle(bundle_root: Path, basename: str) -> dict[str, Any]:
    if bundle_root.is_symlink() or not bundle_root.is_dir():
        raise ReleaseError("bundle_root_missing_or_symlinked")
    client_root = bundle_root / "client"
    index_path = client_root / "index.html"
    if client_root.is_symlink() or not index_path.is_file() or index_path.is_symlink():
        raise ReleaseError("bundle_client_index_missing_or_symlinked")

    files: list[dict[str, Any]] = []
    tree_digest = hashlib.sha256()
    total_bytes = 0
    for candidate in sorted(bundle_root.rglob("*")):
        if candidate.is_symlink():
            raise ReleaseError("bundle_contains_symlink")
        if candidate.is_dir():
            continue
        mode = candidate.stat().st_mode
        if not stat.S_ISREG(mode):
            raise ReleaseError("bundle_contains_non_regular_file")
        relative = candidate.relative_to(bundle_root).as_posix()
        size = candidate.stat().st_size
        file_hash = sha256_file(candidate)
        if candidate.suffix in TEXT_BUNDLE_SUFFIXES:
            payload = candidate.read_bytes()
            if any(marker in payload for marker in FORBIDDEN_BUNDLE_MARKERS):
                raise ReleaseError("bundle_contains_contract_identity_fixture")
        if candidate.suffix == ".map":
            raise ReleaseError("bundle_contains_source_map")
        files.append({"path": relative, "bytes": size, "sha256": file_hash})
        tree_digest.update(relative.encode("utf-8"))
        tree_digest.update(b"\0")
        tree_digest.update(str(size).encode("ascii"))
        tree_digest.update(b"\0")
        tree_digest.update(file_hash.encode("ascii"))
        tree_digest.update(b"\0")
        total_bytes += size

    index_payload = index_path.read_text(encoding="utf-8")
    for raw_reference in HTML_REFERENCE_PATTERN.findall(index_payload):
        relative_reference = _bundle_reference_path(raw_reference, basename)
        if relative_reference is None:
            continue
        referenced_file = client_root / relative_reference
        if (
            referenced_file.is_symlink()
            or not referenced_file.is_file()
            or not referenced_file.resolve().is_relative_to(client_root.resolve())
        ):
            raise ReleaseError("bundle_index_reference_missing")

    if not files:
        raise ReleaseError("bundle_is_empty")
    return {
        "basename": basename,
        "tree_sha256": tree_digest.hexdigest(),
        "index_sha256": sha256_file(index_path),
        "files": files,
        "file_count": len(files),
        "bytes": total_bytes,
    }


def assert_bundle_matches(bundle_root: Path, expected: Mapping[str, Any]) -> dict[str, Any]:
    basename = expected.get("basename")
    if not isinstance(basename, str):
        raise ReleaseError("bundle_manifest_basename_missing")
    actual = inspect_bundle(bundle_root, basename)
    for field in ("tree_sha256", "index_sha256", "file_count", "bytes"):
        if actual[field] != expected.get(field):
            raise ReleaseError(f"bundle_manifest_mismatch:{field}")
    if actual["files"] != expected.get("files"):
        raise ReleaseError("bundle_manifest_mismatch:files")
    return actual


def sanitized_build_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    current = os.environ if source is None else source
    allowed = (
        "HOME",
        "LANG",
        "LC_ALL",
        "LOGNAME",
        "PATH",
        "PNPM_HOME",
        "SHELL",
        "TERM",
        "TMPDIR",
        "USER",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
    )
    environment = {key: current[key] for key in allowed if key in current}
    environment.update(
        {
            "CHOKIDAR_USEPOLLING": "true",
            "CI": "1",
            "GEO_FRONTEND_RELEASE_BUILD": "1",
            "NODE_ENV": "production",
            # Vite cannot always fold an absent custom key. Supplying the one
            # fixture gate as a fixed false literal guarantees that contract
            # identities are removed from production bundles; every ambient
            # VITE value remains excluded by the allowlist above.
            "VITE_ALLOW_CONTRACT_FIXTURES": "false",
        }
    )
    return environment


@contextlib.contextmanager
def release_lock(releases_root: Path = RELEASES_ROOT) -> Iterator[None]:
    releases_root.mkdir(parents=True, exist_ok=True)
    lock_path = releases_root / ".release.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextlib.contextmanager
def defer_termination_signals() -> Iterator[None]:
    if not hasattr(signal, "pthread_sigmask"):
        yield
        return
    blocked = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def rename_exchange(left: Path, right: Path) -> None:
    if left.is_symlink() or right.is_symlink() or not left.is_dir() or not right.is_dir():
        raise ReleaseError("exchange_requires_real_directories")
    if left.stat().st_dev != right.stat().st_dev:
        raise ReleaseError("exchange_requires_same_filesystem")
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise ReleaseError("renameat2_unavailable")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        AT_FDCWD,
        os.fsencode(left),
        AT_FDCWD,
        os.fsencode(right),
        RENAME_EXCHANGE,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        stable = errno.errorcode.get(error_number, "UNKNOWN")
        raise ReleaseError(f"rename_exchange_failed:{stable}")


def assert_nginx_direct_build_contract(
    nginx_config: Path | Sequence[Path] = NGINX_CONFIGS, root: Path = ROOT
) -> None:
    config_paths = (nginx_config,) if isinstance(nginx_config, Path) else tuple(nginx_config)
    configs: list[str] = []
    for config_path in config_paths:
        try:
            configs.append(config_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ReleaseError("nginx_frontend_config_unreadable") from exc
    for app, basename in APPS.items():
        app_route = basename.rstrip("/")
        alias = root / "apps" / app / "build" / "client"
        expected_location = f"location {basename} {{"
        expected_alias = f"alias {alias}/;"
        if not any(expected_location in config and expected_alias in config for config in configs):
            raise ReleaseError(f"nginx_frontend_contract_drift:{app_route}")


def prepare_release(
    release_id: str,
    *,
    root: Path = ROOT,
    releases_root: Path = RELEASES_ROOT,
    pnpm: str = "pnpm",
) -> Path:
    release_id = validated_release_id(release_id)
    destination = release_directory(release_id, releases_root)
    manifest_path = destination / "manifest.json"
    with release_lock(releases_root):
        if destination.exists():
            raise ReleaseError("release_id_already_exists")
        destination.mkdir(parents=True, mode=0o755)
        candidates = destination / "candidates"
        candidates.mkdir(mode=0o755)
        before = source_fingerprint(root)
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "release_id": release_id,
            "created_at": utc_now(),
            "status": "preparing",
            "source": before,
            "apps": {},
            "secrets_recorded": False,
        }
        atomic_json_write(manifest_path, manifest)
        try:
            for app, basename in APPS.items():
                app_root = root / "apps" / app
                transient = app_root / "build-release"
                if transient.exists() or transient.is_symlink():
                    raise ReleaseError(f"stale_release_build_directory:{app}")
                subprocess.run(
                    [pnpm, "--dir", str(app_root), "run", "build"],
                    cwd=root,
                    env=sanitized_build_environment(),
                    check=True,
                )
                if transient.is_symlink() or not transient.is_dir():
                    raise ReleaseError(f"release_build_missing:{app}")
                candidate = candidates / app
                os.rename(transient, candidate)
                manifest["apps"][app] = inspect_bundle(candidate, basename)
                atomic_json_write(manifest_path, manifest)
            after = source_fingerprint(root)
            if before != after:
                raise ReleaseError("release_inputs_changed_during_build")
            manifest["status"] = "prepared"
            manifest["prepared_at"] = utc_now()
            atomic_json_write(manifest_path, manifest)
        except BaseException as exc:
            manifest["status"] = "prepare_failed"
            manifest["failed_at"] = utc_now()
            manifest["failure"] = str(exc) if isinstance(exc, ReleaseError) else type(exc).__name__
            atomic_json_write(manifest_path, manifest)
            raise
    return manifest_path


def validate_prepared_release(
    release_id: str,
    *,
    root: Path = ROOT,
    releases_root: Path = RELEASES_ROOT,
    require_current_source: bool = True,
) -> dict[str, Any]:
    directory = release_directory(release_id, releases_root)
    manifest = load_manifest(directory / "manifest.json")
    if manifest.get("release_id") != release_id or manifest.get("schema_version") != 1:
        raise ReleaseError("release_manifest_identity_mismatch")
    if manifest.get("status") not in VALIDATABLE_RELEASE_STATUSES:
        raise ReleaseError("release_is_not_validatable")
    expected_apps = manifest.get("apps")
    if not isinstance(expected_apps, dict) or set(expected_apps) != set(APPS):
        raise ReleaseError("release_manifest_app_inventory_mismatch")
    for app in APPS:
        expected = expected_apps[app]
        if not isinstance(expected, dict):
            raise ReleaseError("release_manifest_bundle_invalid")
        candidate = directory / "candidates" / app
        if manifest.get("status") in RETRYABLE_RELEASE_STATUSES:
            assert_bundle_matches(candidate, expected)
        elif manifest.get("status") in {"active_verified", "verification_pending"}:
            assert_bundle_matches(root / "apps" / app / "build", expected)
    if require_current_source and manifest.get("source") != source_fingerprint(root):
        raise ReleaseError("prepared_release_source_drift")
    return manifest


def run_verification(command: Sequence[str], root: Path = ROOT) -> None:
    if not command:
        raise ReleaseError("verification_command_required")
    result = subprocess.run(list(command), cwd=root, check=False)
    if result.returncode != 0:
        raise ReleaseError(f"verification_command_failed:{result.returncode}")


def _reverse_exchanges(
    exchanged: Sequence[str],
    *,
    root: Path,
    candidates: Path,
) -> None:
    for app in reversed(exchanged):
        rename_exchange(root / "apps" / app / "build", candidates / app)


def activate_release(
    release_id: str,
    verification_command: Sequence[str],
    *,
    root: Path = ROOT,
    releases_root: Path = RELEASES_ROOT,
    nginx_config: Path | Sequence[Path] = NGINX_CONFIGS,
) -> dict[str, Any]:
    directory = release_directory(release_id, releases_root)
    manifest_path = directory / "manifest.json"
    candidates = directory / "candidates"
    with release_lock(releases_root):
        manifest = validate_prepared_release(
            release_id,
            root=root,
            releases_root=releases_root,
            require_current_source=True,
        )
        if manifest.get("status") not in RETRYABLE_RELEASE_STATUSES:
            raise ReleaseError("release_is_not_activatable")
        assert_nginx_direct_build_contract(nginx_config, root)
        previous: dict[str, Any] = {}
        for app, basename in APPS.items():
            active = root / "apps" / app / "build"
            candidate = candidates / app
            if active.stat().st_dev != candidate.stat().st_dev:
                raise ReleaseError("active_and_candidate_filesystems_differ")
            previous[app] = inspect_bundle(active, basename)
        manifest["previous_apps"] = previous
        manifest["status"] = "activating"
        manifest["activation_started_at"] = utc_now()
        atomic_json_write(manifest_path, manifest)

        exchanged: list[str] = []
        try:
            with defer_termination_signals():
                for app in APPS:
                    rename_exchange(root / "apps" / app / "build", candidates / app)
                    exchanged.append(app)
            for app in APPS:
                assert_bundle_matches(root / "apps" / app / "build", manifest["apps"][app])
                assert_bundle_matches(candidates / app, previous[app])
            manifest["status"] = "verification_pending"
            manifest["swapped_at"] = utc_now()
            atomic_json_write(manifest_path, manifest)
            run_verification(verification_command, root)
        except BaseException as exc:
            if exchanged:
                with defer_termination_signals():
                    _reverse_exchanges(exchanged, root=root, candidates=candidates)
                for app in exchanged:
                    assert_bundle_matches(root / "apps" / app / "build", previous[app])
            failed_at = utc_now()
            failure = str(exc) if isinstance(exc, ReleaseError) else type(exc).__name__
            failure_history = manifest.get("activation_failures")
            if not isinstance(failure_history, list):
                failure_history = []
            failure_history.append(
                {
                    "failed_at": failed_at,
                    "failure": failure,
                    "rolled_back": bool(exchanged),
                }
            )
            manifest["activation_failures"] = failure_history[-32:]
            manifest["status"] = "rolled_back_after_failed_activation"
            manifest["rolled_back_at"] = failed_at
            manifest["failure"] = failure
            atomic_json_write(manifest_path, manifest)
            raise

        manifest["status"] = "active_verified"
        manifest["verified_at"] = utc_now()
        manifest["verification"] = {"result": "passed", "secrets_recorded": False}
        manifest.pop("failure", None)
        atomic_json_write(manifest_path, manifest)
        return manifest


def rollback_release(
    release_id: str,
    verification_command: Sequence[str],
    *,
    root: Path = ROOT,
    releases_root: Path = RELEASES_ROOT,
) -> dict[str, Any]:
    directory = release_directory(release_id, releases_root)
    manifest_path = directory / "manifest.json"
    candidates = directory / "candidates"
    with release_lock(releases_root):
        manifest = load_manifest(manifest_path)
        if manifest.get("status") not in {"active_verified", "verification_pending"}:
            raise ReleaseError("release_is_not_rollback_eligible")
        previous = manifest.get("previous_apps")
        apps = manifest.get("apps")
        if not isinstance(previous, dict) or not isinstance(apps, dict):
            raise ReleaseError("rollback_manifest_incomplete")
        for app in APPS:
            assert_bundle_matches(root / "apps" / app / "build", apps[app])
            assert_bundle_matches(candidates / app, previous[app])
        with defer_termination_signals():
            for app in APPS:
                rename_exchange(root / "apps" / app / "build", candidates / app)
        for app in APPS:
            assert_bundle_matches(root / "apps" / app / "build", previous[app])
            assert_bundle_matches(candidates / app, apps[app])
        manifest["status"] = "rolled_back"
        manifest["rolled_back_at"] = utc_now()
        atomic_json_write(manifest_path, manifest)
        run_verification(verification_command, root)
        return manifest


def _assert_browser_report(
    report: Mapping[str, Any],
    *,
    expected_total: int,
    expected_qualification: str | None = None,
    release_id: str | None = None,
    source_sha256: str | None = None,
    identity_source: str | None = None,
) -> None:
    if (
        report.get("result") != "passed"
        or report.get("summary") != {"total": expected_total, "passed": expected_total}
        or not isinstance(report.get("checks"), list)
        or len(report["checks"]) != expected_total
    ):
        raise ReleaseError("frontend_release_browser_report_failed")
    qualification = report.get("qualification")
    if expected_qualification is not None and (
        not isinstance(qualification, dict)
        or qualification.get("kind") != expected_qualification
        or qualification.get("production_assets_mutated") is not False
        or (release_id is not None and qualification.get("release_id") != release_id)
        or (source_sha256 is not None and qualification.get("source_sha256") != source_sha256)
    ):
        raise ReleaseError("frontend_release_browser_qualification_mismatch")
    identity = report.get("identity")
    if identity_source is not None and (
        not isinstance(identity, dict)
        or identity.get("source") != identity_source
        or identity.get("browser_actor_headers_used") is not False
        or identity.get("secret_emitted") is not False
    ):
        raise ReleaseError("frontend_release_browser_identity_mismatch")
    for check in report["checks"]:
        if not isinstance(check, dict):
            raise ReleaseError("frontend_release_browser_check_invalid")
        runtime_counts = check.get("runtime_issue_counts")
        if not isinstance(runtime_counts, dict) or any(
            not isinstance(value, int) or isinstance(value, bool) or value != 0
            for value in runtime_counts.values()
        ):
            raise ReleaseError("frontend_release_browser_runtime_not_clean")
        if "secret_material_absent" in check and check["secret_material_absent"] is not True:
            raise ReleaseError("frontend_release_browser_secret_boundary_failed")
        if check.get("forbidden_fixture_markers", []) != []:
            raise ReleaseError("frontend_release_browser_fixture_marker")


def _parse_utc_timestamp(value: object, *, error: str) -> datetime:
    if not isinstance(value, str):
        raise ReleaseError(error)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseError(error) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReleaseError(error)
    return parsed.astimezone(UTC)


def _assert_report_generated_between(
    report: Mapping[str, Any],
    *,
    not_before: object,
    not_after: object,
) -> None:
    generated_at = _parse_utc_timestamp(
        report.get("generated_at"),
        error="frontend_release_browser_generated_at_invalid",
    )
    lower_bound = _parse_utc_timestamp(
        not_before,
        error="frontend_release_manifest_timestamp_invalid",
    )
    upper_bound = _parse_utc_timestamp(
        not_after,
        error="frontend_release_manifest_timestamp_invalid",
    )
    if lower_bound > upper_bound:
        raise ReleaseError("frontend_release_evidence_window_invalid")
    if generated_at < lower_bound or generated_at > upper_bound:
        raise ReleaseError("frontend_release_browser_report_stale")


def certify_active_release(
    release_id: str,
    backup: Path,
    output: Path,
    *,
    root: Path = ROOT,
    releases_root: Path = RELEASES_ROOT,
) -> dict[str, Any]:
    release_id = validated_release_id(release_id)
    directory = release_directory(release_id, releases_root)
    manifest_path = directory / "manifest.json"
    candidate_report_path = root / "tests/s04-evidence/frontend-candidate-browser-acceptance.json"
    production_report_path = root / "tests/s04-evidence/production-browser-acceptance.json"
    mock_report_path = root / "tests/s04-evidence/production-mock-scan.json"
    backup = backup.resolve()
    backup_root = (root / ".production-backups").resolve()
    output = output.resolve()
    evidence_root = (root / "tests/s04-evidence").resolve()
    if (
        not backup.is_relative_to(backup_root)
        or backup.is_symlink()
        or not backup.is_file()
        or stat.S_IMODE(backup.stat().st_mode) != 0o600
    ):
        raise ReleaseError("frontend_release_backup_invalid")
    if not output.is_relative_to(evidence_root) or output.is_symlink():
        raise ReleaseError("frontend_release_certificate_path_invalid")

    with release_lock(releases_root):
        manifest = validate_prepared_release(
            release_id,
            root=root,
            releases_root=releases_root,
            require_current_source=True,
        )
        if manifest.get("status") != "active_verified":
            raise ReleaseError("frontend_release_not_active_verified")
        previous = manifest.get("previous_apps")
        if not isinstance(previous, dict) or set(previous) != set(APPS):
            raise ReleaseError("frontend_release_previous_inventory_missing")
        for app in APPS:
            if not isinstance(previous[app], dict):
                raise ReleaseError("frontend_release_previous_bundle_invalid")
            assert_bundle_matches(directory / "candidates" / app, previous[app])
        source = manifest.get("source")
        if not isinstance(source, dict) or not isinstance(source.get("sha256"), str):
            raise ReleaseError("frontend_release_source_fingerprint_invalid")

        candidate_report = load_manifest(candidate_report_path)
        production_report = load_manifest(production_report_path)
        mock_report = load_manifest(mock_report_path)
        certification_started_at = utc_now()
        _assert_browser_report(
            candidate_report,
            expected_total=48,
            expected_qualification="isolated_frontend_candidate",
            release_id=release_id,
            source_sha256=source["sha256"],
            identity_source="native_http_only_session",
        )
        _assert_browser_report(
            production_report,
            expected_total=48,
            expected_qualification="active_production_assets",
            identity_source="native_http_only_session",
        )
        _assert_browser_report(
            mock_report,
            expected_total=29,
            expected_qualification="active_production_mock_scan",
            identity_source="native_http_only_session",
        )
        _assert_report_generated_between(
            candidate_report,
            not_before=manifest.get("prepared_at"),
            not_after=manifest.get("activation_started_at"),
        )
        _assert_report_generated_between(
            production_report,
            not_before=manifest.get("swapped_at"),
            not_after=manifest.get("verified_at"),
        )
        _assert_report_generated_between(
            mock_report,
            not_before=manifest.get("verified_at"),
            not_after=certification_started_at,
        )

        certificate = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "result": "passed_active_frontend_release",
            "release_id": release_id,
            "status": "certified_active",
            "source": manifest["source"],
            "apps": manifest["apps"],
            "rollback_apps": previous,
            "manifest": {
                "path": str(manifest_path.relative_to(root)),
                "sha256": sha256_file(manifest_path),
                "status": manifest["status"],
            },
            "backup": {
                "path": str(backup.relative_to(root)),
                "bytes": backup.stat().st_size,
                "mode": "0600",
                "sha256": sha256_file(backup),
            },
            "browser_qualification": {
                "candidate": {
                    "report": str(candidate_report_path.relative_to(root)),
                    "sha256": sha256_file(candidate_report_path),
                    "summary": candidate_report["summary"],
                    "generated_at": candidate_report["generated_at"],
                    "production_assets_mutated": False,
                },
                "active_production": {
                    "report": str(production_report_path.relative_to(root)),
                    "sha256": sha256_file(production_report_path),
                    "summary": production_report["summary"],
                    "generated_at": production_report["generated_at"],
                },
                "mock_scan": {
                    "report": str(mock_report_path.relative_to(root)),
                    "sha256": sha256_file(mock_report_path),
                    "summary": mock_report["summary"],
                    "generated_at": mock_report["generated_at"],
                },
            },
            "assertions": {
                "active_trees_match_prepared_release": True,
                "rollback_trees_match_previous_release": True,
                "backup_is_restricted": True,
                "candidate_real_session_48_of_48": True,
                "active_production_real_session_48_of_48": True,
                "production_mock_scan_current_29_of_29": True,
                "runtime_issue_counts_zero": True,
                "secret_material_absent": True,
            },
            "secrets_recorded": False,
            "goal_status": "active",
        }
        atomic_json_write(output, certificate)
        return certificate


def summarize(manifest: Mapping[str, Any]) -> dict[str, Any]:
    apps = manifest.get("apps")
    app_summary: dict[str, Any] = {}
    if isinstance(apps, dict):
        for app, bundle in apps.items():
            if isinstance(bundle, dict):
                app_summary[app] = {
                    "tree_sha256": bundle.get("tree_sha256"),
                    "file_count": bundle.get("file_count"),
                    "bytes": bundle.get("bytes"),
                }
    return {
        "release_id": manifest.get("release_id"),
        "status": manifest.get("status"),
        "source_sha256": (
            manifest.get("source", {}).get("sha256")
            if isinstance(manifest.get("source"), dict)
            else None
        ),
        "apps": app_summary,
        "secrets_recorded": False,
    }


def parser() -> argparse.ArgumentParser:
    root_parser = argparse.ArgumentParser()
    subparsers = root_parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--release-id", default=default_release_id())
    prepare.add_argument("--pnpm", default="pnpm")

    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--release-id", required=True)
    inspect.add_argument("--allow-source-drift", action="store_true")

    activate = subparsers.add_parser("activate")
    activate.add_argument("--release-id", required=True)
    activate.add_argument("--verify-command", nargs=argparse.REMAINDER, required=True)

    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--release-id", required=True)
    rollback.add_argument("--verify-command", nargs=argparse.REMAINDER, required=True)

    certify = subparsers.add_parser("certify")
    certify.add_argument("--release-id", required=True)
    certify.add_argument("--backup", type=Path, required=True)
    certify.add_argument(
        "--output",
        type=Path,
        default=ROOT / "tests/s04-evidence/frontend-production-release.json",
    )
    return root_parser


def main() -> None:
    arguments = parser().parse_args()
    try:
        if arguments.command == "prepare":
            manifest_path = prepare_release(arguments.release_id, pnpm=arguments.pnpm)
            manifest = load_manifest(manifest_path)
        elif arguments.command == "inspect":
            manifest = validate_prepared_release(
                arguments.release_id,
                require_current_source=not arguments.allow_source_drift,
            )
        elif arguments.command == "activate":
            manifest = activate_release(arguments.release_id, arguments.verify_command)
        elif arguments.command == "rollback":
            manifest = rollback_release(arguments.release_id, arguments.verify_command)
        elif arguments.command == "certify":
            manifest = certify_active_release(
                arguments.release_id,
                arguments.backup,
                arguments.output,
            )
        else:
            raise ReleaseError("unknown_release_command")
    except (ReleaseError, subprocess.CalledProcessError) as exc:
        stable_error = str(exc) if isinstance(exc, ReleaseError) else "release_build_failed"
        print(json.dumps({"result": "failed", "error": stable_error}), file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps({"result": "passed", **summarize(manifest)}, sort_keys=True))


if __name__ == "__main__":
    main()
