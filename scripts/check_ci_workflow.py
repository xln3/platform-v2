from __future__ import annotations

import json
import re
from pathlib import Path

WORKFLOW = Path(__file__).parents[1] / ".github/workflows/ci.yml"
PACKAGE = Path(__file__).parents[1] / "package.json"
PYTHON_RUNNER = Path(__file__).with_name("test_python.sh")
PLAYWRIGHT_CONFIG = Path(__file__).parents[1] / "playwright.config.ts"


def _job_source(source: str, job_name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job_name)}:\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        source,
    )
    if match is None:
        raise ValueError(f"CI job is missing: {job_name}")
    return match.group("body")


def main() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    package = json.loads(PACKAGE.read_text(encoding="utf-8"))
    python_runner = PYTHON_RUNNER.read_text(encoding="utf-8")
    playwright_config = PLAYWRIGHT_CONFIG.read_text(encoding="utf-8")
    required_by_job = {
        "python": {
            "root checkout": "uses: actions/checkout@v4",
            "fresh real dependencies": (
                "docker compose -f compose.yaml -f deploy/s02/compose.pgvector.yaml up -d --wait"
            ),
            "schema migration": ".venv/bin/alembic upgrade head",
            "isolated database preparation": "bash scripts/prepare_ci_test_databases.sh",
            "skip-intolerant test policy": "--fail-on-skip",
            "quick Python lane": "bash scripts/test_python.sh",
            "slow Python lane": "-m slow",
            "service integration lane": "-m service_integration",
            "isolated PostgreSQL lane": "-m isolated_postgres",
            "knowledge PostgreSQL lane": "-m knowledge_postgres",
            "historical migration lane": "-m compat_postgres",
            "document toolchain lane": "-m document_toolchain",
            "Vault Transit integration": "tools/test_vault_transit_integration.py",
            "cleanup": (
                "docker compose -f compose.yaml -f deploy/s02/compose.pgvector.yaml down -v"
            ),
        },
        "typescript": {
            "typescript build": "pnpm turbo run build",
            "typescript lint": "pnpm turbo run lint",
            "typescript typecheck": "pnpm turbo run typecheck",
            "typescript test": "pnpm turbo run test",
            "root browser runtime tests": "pnpm test:e2e-runtime-unit",
        },
        "contract-and-release-guards": {
            "OpenAPI guard": "pnpm check:api",
            "production bundle guard": "pnpm check:production-bundles",
            "production route guard": "pnpm check:production-routes",
            "business alert guard": "pnpm check:observability",
            "E2E artifact guard": "pnpm check:e2e-artifacts",
        },
        "browser-e2e": {
            "browser contract E2E": "pnpm test:e2e:contract",
            "browser install": "playwright install --with-deps chromium",
            "failure artifact": "uses: actions/upload-artifact@v4",
        },
        "live-api-e2e": {
            "real API process": "uvicorn geo_platform.main:app",
            "real API health probe": "http://127.0.0.1:45200/api/v2/health",
            "large deterministic dataset": "prepare_live_api_e2e_dataset.py",
            "live API browser lane": "pnpm test:e2e:live",
            "live API failure artifact": "uses: actions/upload-artifact@v4",
            "cleanup": (
                "docker compose -f compose.yaml -f deploy/s02/compose.pgvector.yaml down -v"
            ),
        },
        "compose-smoke": {
            "real dependency smoke": "bash scripts/smoke.sh",
            "cleanup": (
                "docker compose -f compose.yaml -f deploy/s02/compose.pgvector.yaml down -v"
            ),
        },
    }
    missing: list[str] = []
    for job_name, required in required_by_job.items():
        try:
            job = _job_source(source, job_name)
        except ValueError:
            missing.append(f"job:{job_name}")
            continue
        missing.extend(
            f"{job_name}:{name}" for name, marker in required.items() if marker not in job
        )
    forbidden = {
        "nonexistent repository subdirectory": "working-directory: platform-v2",
        "nonmatching repository path filter": "platform-v2/**",
    }
    present_forbidden = [name for name, marker in forbidden.items() if marker in source]
    required_package_scripts = {
        "test:python": "bash scripts/test_python.sh",
        "test:e2e": "pnpm test:e2e:contract",
        "test:e2e:contract": "playwright test --grep-invert @live-api",
        "test:e2e:live": "playwright test --grep @live-api --project=operations-desktop",
    }
    package_scripts = package.get("scripts", {})
    missing.extend(
        f"package.json:{name}"
        for name, command in required_package_scripts.items()
        if package_scripts.get(name) != command
    )
    for marker in (
        "not isolated_postgres",
        "not knowledge_postgres",
        "not compat_postgres",
        "not external_fixture",
        "not document_toolchain",
        "not slow",
        "not service_integration",
        "--fail-on-skip",
    ):
        if marker not in python_runner:
            missing.append(f"test_python.sh:{marker}")
    if "'test-results/playwright/e2e-results.json'" not in playwright_config:
        missing.append("playwright.config.ts:ignored default JSON report")
    if "'test-results/playwright/results'" not in playwright_config:
        missing.append("playwright.config.ts:ignored default output root")
    if missing or present_forbidden:
        raise SystemExit(f"ci_workflow_invalid missing={missing} forbidden={present_forbidden}")
    print(
        {
            "result": "passed",
            "required_capabilities": sum(len(items) for items in required_by_job.values()),
            "forbidden_drift": 0,
        }
    )


if __name__ == "__main__":
    main()
