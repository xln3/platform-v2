from pathlib import Path

from scripts.check_e2e_artifacts import (
    browser_report_structure_findings,
    is_forbidden_negative_snapshot,
    json_secret_findings,
)


def test_json_artifact_phone_detection_is_structure_aware() -> None:
    safe_report = b'{"stats":{"duration":181879.62100123456},"phone_mask":"138****0000"}'
    assert json_secret_findings(safe_report) == []

    phone = "138" + "0013" + "8000"
    assert json_secret_findings(f'{{"error":"full phone {phone}"}}'.encode()) == [
        "standalone_phone"
    ]
    assert json_secret_findings(f'{{"mobile":{phone}}}'.encode()) == ["standalone_phone"]


def test_json_artifact_secret_detection_normalizes_obfuscation() -> None:
    hostile = (
        b'{"error":"\\uff23\\uff4f\\uff4f\\uff4b\\uff49\\uff45'
        b'\\u200b\\uff1dsession=artifact-canary"}'
    )
    findings = json_secret_findings(hostile)
    assert findings == ["cookie"]


def test_json_artifact_rejects_invalid_json_without_echoing_content() -> None:
    assert json_secret_findings(b'{"access_token":"artifact-canary"') == ["invalid_json"]


def test_negative_machine_visuals_cannot_become_snapshot_baselines() -> None:
    assert is_forbidden_negative_snapshot(
        Path(
            "tests/e2e/customer-visual.spec.ts-snapshots/"
            "forbidden-machine-readable-qr-customer-desktop-linux.png"
        )
    )
    assert is_forbidden_negative_snapshot(
        Path(
            "tests/e2e/customer-visual.spec.ts-snapshots/"
            "machine-readable-rejection-customer-mobile.png"
        )
    )
    assert is_forbidden_negative_snapshot(
        Path(
            "tests/e2e/customer-visual.spec.ts-snapshots/"
            "browser-surface-rejection-customer-tablet.png"
        )
    )
    assert not is_forbidden_negative_snapshot(
        Path(
            "tests/e2e/customer-visual.spec.ts-snapshots/"
            "customer-account-pairing-qr-customer-mobile-linux.png"
        )
    )


def test_browser_evidence_must_be_fully_passing_secret_safe_and_runtime_clean() -> None:
    safe = {
        "summary": {"total": 1, "passed": 1},
        "identity": {"secret_emitted": False},
        "checks": [
            {
                "runtime_issue_counts": {"console_error": 0, "request_failed": 0},
                "secret_material_absent": True,
                "screenshot": "tests/s04-evidence/production-screenshots/safe.png",
            }
        ],
    }
    assert browser_report_structure_findings(safe) == []

    unsafe = {
        **safe,
        "summary": {"total": 1, "passed": 0},
        "identity": {"secret_emitted": True},
        "checks": [
            {
                "runtime_issue_counts": {"console_error": 1},
                "secret_material_absent": False,
                "screenshot": None,
            }
        ],
    }
    assert browser_report_structure_findings(unsafe) == ["browser_report_not_fully_passing"]
