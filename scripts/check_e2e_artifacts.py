from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]
TESTS = ROOT / "tests"
EVIDENCE = TESTS / "s04-evidence"
TERMINAL_EVIDENCE_REPORTS = (
    EVIDENCE / "customer-terminal-extension-release.json",
    EVIDENCE / "customer-terminal-extension-runtime.json",
)
BROWSER_EVIDENCE_REPORTS = (
    EVIDENCE / "frontend-candidate-browser-acceptance.json",
    EVIDENCE / "production-browser-acceptance.json",
    EVIDENCE / "production-mock-scan.json",
)
FRONTEND_RELEASE_REPORT = EVIDENCE / "frontend-production-release.json"
TERMINAL_SCREENSHOT = TESTS / "visual-evidence/s03/customer-terminal-resumed-task.png"
PRODUCTION_SCREENSHOT_ROOT = EVIDENCE / "production-screenshots"
CANDIDATE_SCREENSHOT_ROOT = EVIDENCE / "frontend-candidate-screenshots"
ALLOWED_BROWSER_SCREENSHOT_ROOTS = (
    PRODUCTION_SCREENSHOT_ROOT,
    CANDIDATE_SCREENSHOT_ROOT,
)
HISTORICAL_BROWSER_SCREENSHOTS = {
    PRODUCTION_SCREENSHOT_ROOT
    / "contact-sheet.png": "889718d971f7de7fa461af1bcda3892347dbcbf27782c1df6e17fd2b0a640be3",
    PRODUCTION_SCREENSHOT_ROOT / "operations-admin-desktop.png": (
        "d6c53f694ee4a0e33e8c14f729ef562daae6fcdd908e0172f37127add5c8cf95"
    ),
    PRODUCTION_SCREENSHOT_ROOT
    / "operations-desktop.png": "2d7fe6d4e9ac527afc3054e33392083bd400639384c7a8acfb99ae04414954ef",
    PRODUCTION_SCREENSHOT_ROOT
    / "operations-mobile.png": "4a03d80f6489391cf4a7eef3828307e0d33b96d99979e6dfaa2a6f8afaa4088e",
    PRODUCTION_SCREENSHOT_ROOT
    / "operations-tablet.png": "0f637c354008f20ac1835c22a90631779a064ea4d9a31f825d62d5c8c143b391",
}
OUTPUT_ROOT_PATTERN = re.compile(r"e2e-results(?:-.+)?\Z")
MANUAL_SCREENSHOT_PATTERN = re.compile(
    r"(?:customer-(?:account|forms|delivery)|state-matrix|report-studio|"
    r"intelligence-workbench)-(?:desktop|tablet|mobile)\.png\Z"
)
FORBIDDEN_NEGATIVE_SNAPSHOT_PATTERN = re.compile(
    r"(?:forbidden-machine-readable|machine-readable-rejection|browser-surface-rejection)"
    r".+\.png\Z"
)
SECRET_PATTERNS = {
    "bearer": re.compile(r"\bBearer\s+[A-Za-z0-9%._~+/=-]{6,}", re.IGNORECASE),
    "cookie": re.compile(
        r"\b(?:Cookie|Set-Cookie|SESSION)\s*[\"']?\s*[:=]\s*[\"']?[^\s\"'<]{4,}",
        re.IGNORECASE,
    ),
    "token": re.compile(
        r"\b(?:access_token|refresh_token|pairing_token|proxy_password)\s*[\"']?\s*"
        r"[:=]\s*[\"']?[^\s\"'<]{4,}",
        re.IGNORECASE,
    ),
    "otp": re.compile(r"\bOTP\s*[:=]?\s*\d{4,}", re.IGNORECASE),
    "profile": re.compile(
        r"(?:profile_path\s*[\"']?\s*[:=]|/secret/(?:browser/)?profile)",
        re.IGNORECASE,
    ),
}
STANDALONE_PHONE_PATTERN = re.compile(r"(?<![A-Fa-f0-9])1[3-9]\d{9}(?![A-Fa-f0-9])")
PHONE_KEY_PATTERN = re.compile(r"(?:phone|mobile|telephone|contact)", re.IGNORECASE)
ZERO_WIDTH_TRANSLATION = str.maketrans("", "", "\u200b\u200c\u200d\u2060\ufeff")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).translate(ZERO_WIDTH_TRANSLATION)


def secret_findings(value: str, *, include_phone: bool = True) -> list[str]:
    normalized = normalize_text(value)
    findings = [name for name, pattern in SECRET_PATTERNS.items() if pattern.search(normalized)]
    if include_phone and STANDALONE_PHONE_PATTERN.search(normalized):
        findings.append("standalone_phone")
    return findings


def _json_nested_findings(value: Any, parent_key: str = "") -> list[str]:
    if isinstance(value, str):
        return secret_findings(value)
    if isinstance(value, dict):
        return [
            finding
            for key, item in value.items()
            for finding in _json_nested_findings(item, str(key))
        ]
    if isinstance(value, list):
        return [finding for item in value for finding in _json_nested_findings(item, parent_key)]
    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and PHONE_KEY_PATTERN.search(parent_key)
    ):
        if STANDALONE_PHONE_PATTERN.fullmatch(str(value)) is not None:
            return ["standalone_phone"]
    return []


def json_secret_findings(value: bytes) -> list[str]:
    try:
        text = value.decode("utf-8-sig")
        document = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ["invalid_json"]
    findings = [
        *secret_findings(text, include_phone=False),
        *_json_nested_findings(document),
    ]
    return list(dict.fromkeys(findings))


def browser_report_structure_findings(document: Any) -> list[str]:
    if not isinstance(document, dict):
        return ["browser_report_not_object"]
    summary = document.get("summary")
    checks = document.get("checks")
    if (
        not isinstance(summary, dict)
        or not isinstance(summary.get("total"), int)
        or not isinstance(summary.get("passed"), int)
        or summary["total"] <= 0
        or summary["passed"] != summary["total"]
        or not isinstance(checks, list)
        or len(checks) != summary["total"]
    ):
        return ["browser_report_not_fully_passing"]
    findings: list[str] = []
    identity = document.get("identity")
    if isinstance(identity, dict) and identity.get("secret_emitted") is not False:
        findings.append("browser_report_secret_boundary_missing")
    has_screenshots = any(isinstance(check, dict) and "screenshot" in check for check in checks)
    for check in checks:
        if not isinstance(check, dict):
            findings.append("browser_check_not_object")
            continue
        runtime_counts = check.get("runtime_issue_counts")
        if not isinstance(runtime_counts, dict) or any(
            not isinstance(value, int) or isinstance(value, bool) or value != 0
            for value in runtime_counts.values()
        ):
            findings.append("browser_runtime_issue")
        if "forbidden_fixture_markers" in check and check["forbidden_fixture_markers"] != []:
            findings.append("browser_fixture_marker")
        if has_screenshots and (
            check.get("secret_material_absent") is not True
            or not isinstance(check.get("screenshot"), str)
            or not check["screenshot"]
        ):
            findings.append("browser_screenshot_not_secret_safe")
    return list(dict.fromkeys(findings))


def _browser_screenshot_path(value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("browser screenshot path must be workspace-relative")
    path = ROOT / relative
    if not any(path.is_relative_to(root) for root in ALLOWED_BROWSER_SCREENSHOT_ROOTS):
        raise ValueError("browser screenshot path is outside the bounded evidence roots")
    return path


def is_forbidden_negative_snapshot(path: Path) -> bool:
    return path.parent.name.endswith("-snapshots") and (
        FORBIDDEN_NEGATIVE_SNAPSHOT_PATTERN.fullmatch(path.name) is not None
    )


def main() -> None:
    errors: list[str] = []
    output_roots = sorted(
        path
        for path in TESTS.iterdir()
        if path.is_dir() and OUTPUT_ROOT_PATTERN.fullmatch(path.name)
    )
    output_files = 0
    for output_root in output_roots:
        for artifact in sorted(output_root.rglob("*")):
            if artifact.is_symlink():
                errors.append(f"{artifact.relative_to(ROOT)}: symlink evidence is forbidden")
                continue
            if not artifact.is_file():
                continue
            output_files += 1
            relative_to_output = artifact.relative_to(output_root)
            allowed = len(relative_to_output.parts) == 1 and (
                artifact.name == ".last-run.json"
                or MANUAL_SCREENSHOT_PATTERN.fullmatch(artifact.name) is not None
            )
            if not allowed:
                errors.append(
                    f"{artifact.relative_to(ROOT)}: stale or unbounded Playwright artifact"
                )
            if artifact.suffix == ".json":
                findings = json_secret_findings(artifact.read_bytes())
                if findings:
                    errors.append(
                        f"{artifact.relative_to(ROOT)}: secret-shaped evidence {findings}"
                    )
            elif artifact.suffix in {".md", ".txt", ".zip"}:
                findings = secret_findings(artifact.read_bytes().decode("utf-8", errors="replace"))
                if findings:
                    errors.append(
                        f"{artifact.relative_to(ROOT)}: secret-shaped evidence {findings}"
                    )

    reports = sorted(
        {
            *EVIDENCE.glob("e2e*.json"),
            *TERMINAL_EVIDENCE_REPORTS,
            *BROWSER_EVIDENCE_REPORTS,
            FRONTEND_RELEASE_REPORT,
        }
    )
    referenced_browser_screenshots: set[Path] = set()
    for report in reports:
        if not report.is_file() or report.is_symlink():
            errors.append(f"{report.relative_to(ROOT)}: required bounded JSON evidence is missing")
            continue
        payload = report.read_bytes()
        findings = json_secret_findings(payload)
        if findings:
            errors.append(f"{report.relative_to(ROOT)}: secret-shaped evidence {findings}")
        if report not in BROWSER_EVIDENCE_REPORTS:
            continue
        try:
            document = json.loads(payload.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        structure_findings = browser_report_structure_findings(document)
        if structure_findings:
            errors.append(
                f"{report.relative_to(ROOT)}: unsafe browser evidence {structure_findings}"
            )
        if not isinstance(document, dict) or not isinstance(document.get("checks"), list):
            continue
        for check in document["checks"]:
            if not isinstance(check, dict) or not isinstance(check.get("screenshot"), str):
                continue
            try:
                screenshot = _browser_screenshot_path(check["screenshot"])
            except ValueError:
                errors.append(
                    f"{report.relative_to(ROOT)}: browser screenshot path is outside "
                    "the bounded evidence roots"
                )
                continue
            referenced_browser_screenshots.add(screenshot)

    present_browser_screenshots = {
        path
        for root in ALLOWED_BROWSER_SCREENSHOT_ROOTS
        if root.is_dir() and not root.is_symlink()
        for path in root.rglob("*.png")
    }
    for screenshot in sorted(referenced_browser_screenshots | present_browser_screenshots):
        if screenshot not in referenced_browser_screenshots:
            expected_historical_hash = HISTORICAL_BROWSER_SCREENSHOTS.get(screenshot)
            if (
                expected_historical_hash is not None
                and screenshot.is_file()
                and not screenshot.is_symlink()
                and screenshot.stat().st_size <= 2_000_000
                and screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
                and sha256_file(screenshot) == expected_historical_hash
            ):
                continue
            errors.append(
                f"{screenshot.relative_to(ROOT)}: stale unreferenced browser screenshot evidence"
            )
            continue
        if (
            screenshot.is_symlink()
            or not screenshot.is_file()
            or screenshot.stat().st_size > 2_000_000
            or not screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        ):
            errors.append(
                f"{screenshot.relative_to(ROOT)}: required bounded PNG evidence is invalid"
            )

    if (
        not TERMINAL_SCREENSHOT.is_file()
        or TERMINAL_SCREENSHOT.is_symlink()
        or TERMINAL_SCREENSHOT.stat().st_size > 2_000_000
        or not TERMINAL_SCREENSHOT.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    ):
        errors.append(
            f"{TERMINAL_SCREENSHOT.relative_to(ROOT)}: required bounded PNG evidence is invalid"
        )

    for snapshot in sorted((TESTS / "e2e").glob("*-snapshots/*.png")):
        if is_forbidden_negative_snapshot(snapshot):
            errors.append(
                f"{snapshot.relative_to(ROOT)}: negative browser-safety test artifact "
                "must not become a versioned baseline"
            )

    if errors:
        raise SystemExit("\n".join(errors))
    print(
        "E2E artifact DLP guard passed: "
        f"{len(output_roots)} output roots/{output_files} bounded files and "
        f"{len(reports)} JSON reports/{len(referenced_browser_screenshots)} browser screenshots "
        f"plus {len(HISTORICAL_BROWSER_SCREENSHOTS)} immutable reviewed historical screenshots "
        "contain no secret-shaped evidence."
    )


if __name__ == "__main__":
    main()
