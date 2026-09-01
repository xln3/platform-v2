"""Fail when test-only identity fixture payloads leak into production web bundles."""

from pathlib import Path

APPS = (
    "customer-web",
    "operations-web",
    "report-studio",
    "intelligence-web",
    "intake-form",
)
FORBIDDEN = (
    b"CONTRACTFIXTURE",
    b"customer-contract-fixture",
    b"operator-contract-fixture",
    b"analyst-contract-fixture",
    b"reviewer-contract-fixture",
)


def main() -> None:
    violations: list[str] = []
    for app in APPS:
        bundle_root = Path("apps") / app / "build" / "client"
        if not (bundle_root / "index.html").is_file():
            violations.append(f"{app}: production bundle missing")
            continue
        for asset in bundle_root.rglob("*"):
            if not asset.is_file() or asset.suffix not in {".html", ".js", ".mjs"}:
                continue
            payload = asset.read_bytes()
            if any(marker in payload for marker in FORBIDDEN):
                violations.append(f"{app}: test identity payload in {asset.name}")
    if violations:
        raise SystemExit("\n".join(violations))
    print("Production bundle guard passed: no contract identity fixture payload is shipped.")


if __name__ == "__main__":
    main()
