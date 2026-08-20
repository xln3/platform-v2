from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts import frontend_release


def make_bundle(root: Path, basename: str, marker: str) -> None:
    assets = root / "client" / "assets"
    assets.mkdir(parents=True)
    (assets / "app.js").write_text(f"globalThis.__bundle={marker!r};\n", encoding="utf-8")
    (root / "client" / "index.html").write_text(
        f'<main>{marker}</main><script src="{basename}assets/app.js"></script>\n',
        encoding="utf-8",
    )
    server = root / "server"
    server.mkdir()
    (server / "index.js").write_text("export {};\n", encoding="utf-8")


def make_release_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str]:
    root = tmp_path / "workspace"
    releases = root / ".frontend-releases"
    release_id = "s03-test-release"
    release = releases / release_id
    source = {"sha256": "source-safe", "files": 1, "bytes": 1}
    manifest_apps: dict[str, object] = {}
    nginx_lines: list[str] = []

    for app, basename in frontend_release.APPS.items():
        active = root / "apps" / app / "build"
        candidate = release / "candidates" / app
        make_bundle(active, basename, f"old-{app}")
        make_bundle(candidate, basename, f"new-{app}")
        manifest_apps[app] = frontend_release.inspect_bundle(candidate, basename)
        nginx_lines.extend(
            [
                f"location {basename} {{",
                f"alias {root / 'apps' / app / 'build' / 'client'}/;",
                "}",
            ]
        )

    manifest = {
        "schema_version": 1,
        "release_id": release_id,
        "created_at": "2026-07-28T00:00:00+00:00",
        "prepared_at": "2026-07-28T00:00:01+00:00",
        "status": "prepared",
        "source": source,
        "apps": manifest_apps,
        "secrets_recorded": False,
    }
    frontend_release.atomic_json_write(release / "manifest.json", manifest)
    nginx_config = root / "nginx.conf"
    nginx_config.write_text("\n".join(nginx_lines), encoding="utf-8")
    monkeypatch.setattr(frontend_release, "source_fingerprint", lambda _root: source)
    return root, nginx_config, release_id


def bundle_marker(root: Path) -> str:
    return (root / "client" / "index.html").read_text(encoding="utf-8")


def make_browser_report(
    total: int,
    generated_at: str,
    qualification: str,
    *,
    release_id: str | None = None,
    source_sha256: str | None = None,
) -> dict[str, object]:
    qualification_record: dict[str, object] = {
        "kind": qualification,
        "production_assets_mutated": False,
    }
    if release_id is not None:
        qualification_record["release_id"] = release_id
    if source_sha256 is not None:
        qualification_record["source_sha256"] = source_sha256
    return {
        "generated_at": generated_at,
        "qualification": qualification_record,
        "result": "passed",
        "summary": {"total": total, "passed": total},
        "identity": {
            "source": "native_http_only_session",
            "browser_actor_headers_used": False,
            "secret_emitted": False,
        },
        "checks": [
            {
                "runtime_issue_counts": {},
                "secret_material_absent": True,
                "forbidden_fixture_markers": [],
            }
            for _ in range(total)
        ],
    }


def test_build_environment_drops_all_secret_and_vite_values() -> None:
    result = frontend_release.sanitized_build_environment(
        {
            "PATH": "/safe/bin",
            "HOME": "/safe/home",
            "VITE_GEO_API_BASE": "https://unsafe.invalid/",
            "VITE_COOKIE": "secret",
            "TOKEN": "secret",
            "DATABASE_URL": "secret",
            "GEOSYS_DB": "secret",
        }
    )

    assert result["PATH"] == "/safe/bin"
    assert result["HOME"] == "/safe/home"
    assert result["NODE_ENV"] == "production"
    assert result["GEO_FRONTEND_RELEASE_BUILD"] == "1"
    assert result["VITE_ALLOW_CONTRACT_FIXTURES"] == "false"
    assert all(
        not key.startswith("VITE_") or key == "VITE_ALLOW_CONTRACT_FIXTURES" for key in result
    )
    assert {"TOKEN", "DATABASE_URL", "GEOSYS_DB"}.isdisjoint(result)


def test_bundle_inspection_rejects_fixture_source_map_and_missing_asset(tmp_path: Path) -> None:
    basename = "/platform/customer/"

    fixture_bundle = tmp_path / "fixture"
    make_bundle(fixture_bundle, basename, "safe")
    (fixture_bundle / "client" / "assets" / "app.js").write_bytes(b"CONTRACTFIXTURE")
    with pytest.raises(frontend_release.ReleaseError, match="contract_identity_fixture"):
        frontend_release.inspect_bundle(fixture_bundle, basename)

    source_map_bundle = tmp_path / "source-map"
    make_bundle(source_map_bundle, basename, "safe")
    (source_map_bundle / "client" / "assets" / "app.js.map").write_text("{}", encoding="utf-8")
    with pytest.raises(frontend_release.ReleaseError, match="source_map"):
        frontend_release.inspect_bundle(source_map_bundle, basename)

    missing_bundle = tmp_path / "missing"
    make_bundle(missing_bundle, basename, "safe")
    (missing_bundle / "client" / "assets" / "app.js").unlink()
    with pytest.raises(frontend_release.ReleaseError, match="index_reference_missing"):
        frontend_release.inspect_bundle(missing_bundle, basename)


def test_failed_verification_atomically_restores_every_active_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, nginx_config, release_id = make_release_fixture(tmp_path, monkeypatch)
    releases = root / ".frontend-releases"

    with pytest.raises(frontend_release.ReleaseError, match="verification_command_failed:7"):
        frontend_release.activate_release(
            release_id,
            [sys.executable, "-c", "raise SystemExit(7)"],
            root=root,
            releases_root=releases,
            nginx_config=nginx_config,
        )

    for app in frontend_release.APPS:
        assert f"old-{app}" in bundle_marker(root / "apps" / app / "build")
        assert f"new-{app}" in bundle_marker(releases / release_id / "candidates" / app)
    manifest = json.loads((releases / release_id / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "rolled_back_after_failed_activation"
    assert manifest["failure"] == "verification_command_failed:7"
    assert manifest["activation_failures"] == [
        {
            "failed_at": manifest["rolled_back_at"],
            "failure": "verification_command_failed:7",
            "rolled_back": True,
        }
    ]
    assert manifest["secrets_recorded"] is False


def test_failed_activation_can_be_inspected_and_retried_without_rebuilding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, nginx_config, release_id = make_release_fixture(tmp_path, monkeypatch)
    releases = root / ".frontend-releases"

    with pytest.raises(frontend_release.ReleaseError, match="verification_command_failed:7"):
        frontend_release.activate_release(
            release_id,
            [sys.executable, "-c", "raise SystemExit(7)"],
            root=root,
            releases_root=releases,
            nginx_config=nginx_config,
        )

    inspected = frontend_release.validate_prepared_release(
        release_id,
        root=root,
        releases_root=releases,
    )
    assert inspected["status"] == "rolled_back_after_failed_activation"

    activated = frontend_release.activate_release(
        release_id,
        [sys.executable, "-c", "raise SystemExit(0)"],
        root=root,
        releases_root=releases,
        nginx_config=nginx_config,
    )

    assert activated["status"] == "active_verified"
    assert "failure" not in activated
    assert len(activated["activation_failures"]) == 1
    assert activated["activation_failures"][0]["failure"] == "verification_command_failed:7"
    for app in frontend_release.APPS:
        assert f"new-{app}" in bundle_marker(root / "apps" / app / "build")
        assert f"old-{app}" in bundle_marker(releases / release_id / "candidates" / app)


def test_verified_activation_and_manual_rollback_exchange_whole_trees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, nginx_config, release_id = make_release_fixture(tmp_path, monkeypatch)
    releases = root / ".frontend-releases"
    pass_command = [sys.executable, "-c", "raise SystemExit(0)"]

    activated = frontend_release.activate_release(
        release_id,
        pass_command,
        root=root,
        releases_root=releases,
        nginx_config=nginx_config,
    )

    assert activated["status"] == "active_verified"
    for app in frontend_release.APPS:
        assert f"new-{app}" in bundle_marker(root / "apps" / app / "build")
        assert f"old-{app}" in bundle_marker(releases / release_id / "candidates" / app)

    rolled_back = frontend_release.rollback_release(
        release_id,
        pass_command,
        root=root,
        releases_root=releases,
    )

    assert rolled_back["status"] == "rolled_back"
    for app in frontend_release.APPS:
        assert f"old-{app}" in bundle_marker(root / "apps" / app / "build")
        assert f"new-{app}" in bundle_marker(releases / release_id / "candidates" / app)


def test_browser_report_requires_native_identity_and_matching_source() -> None:
    report = make_browser_report(
        48,
        "2026-08-12T00:01:00Z",
        "isolated_frontend_candidate",
        release_id="s03-test-release",
        source_sha256="source-safe",
    )
    frontend_release._assert_browser_report(
        report,
        expected_total=48,
        expected_qualification="isolated_frontend_candidate",
        release_id="s03-test-release",
        source_sha256="source-safe",
        identity_source="native_http_only_session",
    )

    report["qualification"]["source_sha256"] = "stale-source"  # type: ignore[index]
    with pytest.raises(frontend_release.ReleaseError, match="qualification_mismatch"):
        frontend_release._assert_browser_report(
            report,
            expected_total=48,
            expected_qualification="isolated_frontend_candidate",
            release_id="s03-test-release",
            source_sha256="source-safe",
            identity_source="native_http_only_session",
        )

    report["qualification"]["source_sha256"] = "source-safe"  # type: ignore[index]
    report["identity"]["source"] = "legacy_http_only_session"  # type: ignore[index]
    with pytest.raises(frontend_release.ReleaseError, match="identity_mismatch"):
        frontend_release._assert_browser_report(
            report,
            expected_total=48,
            expected_qualification="isolated_frontend_candidate",
            release_id="s03-test-release",
            source_sha256="source-safe",
            identity_source="native_http_only_session",
        )


def test_active_release_certifier_rejects_stale_mock_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, nginx_config, release_id = make_release_fixture(tmp_path, monkeypatch)
    releases = root / ".frontend-releases"
    frontend_release.activate_release(
        release_id,
        [sys.executable, "-c", "raise SystemExit(0)"],
        root=root,
        releases_root=releases,
        nginx_config=nginx_config,
    )
    manifest_path = releases / release_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    evidence = root / "tests/s04-evidence"
    evidence.mkdir(parents=True)
    frontend_release.atomic_json_write(
        evidence / "frontend-candidate-browser-acceptance.json",
        make_browser_report(
            48,
            manifest["prepared_at"],
            "isolated_frontend_candidate",
            release_id=release_id,
            source_sha256=manifest["source"]["sha256"],
        ),
    )
    frontend_release.atomic_json_write(
        evidence / "production-browser-acceptance.json",
        make_browser_report(
            48,
            manifest["swapped_at"],
            "active_production_assets",
        ),
    )
    mock_report_path = evidence / "production-mock-scan.json"
    frontend_release.atomic_json_write(
        mock_report_path,
        make_browser_report(
            29,
            manifest["prepared_at"],
            "active_production_mock_scan",
        ),
    )

    backups = root / ".production-backups"
    backups.mkdir()
    backup = backups / "pre-release.tar"
    backup.write_bytes(b"restricted backup")
    backup.chmod(0o600)
    certificate = evidence / "frontend-production-release.json"

    with pytest.raises(frontend_release.ReleaseError, match="browser_report_stale"):
        frontend_release.certify_active_release(
            release_id,
            backup,
            certificate,
            root=root,
            releases_root=releases,
        )
    assert not certificate.exists()

    frontend_release.atomic_json_write(
        mock_report_path,
        make_browser_report(
            29,
            manifest["verified_at"],
            "active_production_mock_scan",
        ),
    )
    result = frontend_release.certify_active_release(
        release_id,
        backup,
        certificate,
        root=root,
        releases_root=releases,
    )
    assert result["assertions"]["candidate_real_session_48_of_48"] is True
    assert result["assertions"]["production_mock_scan_current_29_of_29"] is True
