from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).parents[1] / ".github/workflows/ci.yml"


def main() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    required = {
        "root checkout": "uses: actions/checkout@v4",
        "fresh real dependencies": (
            "docker compose -f compose.yaml -f deploy/s02/compose.pgvector.yaml up -d --wait"
        ),
        "schema migration": ".venv/bin/alembic upgrade head",
        "python suite": ".venv/bin/pytest -q",
        "typescript matrix": "pnpm turbo run lint typecheck test build",
        "OpenAPI guard": "pnpm check:api",
        "production bundle guard": "pnpm check:production-bundles",
        "legacy route guard": "pnpm check:legacy-routes",
        "business alert guard": "pnpm check:observability",
        "browser E2E": "pnpm test:e2e",
        "browser install": "playwright install --with-deps chromium",
        "real dependency smoke": "bash scripts/smoke.sh",
        "failure artifact": "uses: actions/upload-artifact@v4",
        "cleanup": ("docker compose -f compose.yaml -f deploy/s02/compose.pgvector.yaml down -v"),
    }
    missing = [name for name, marker in required.items() if marker not in source]
    forbidden = {
        "nonexistent repository subdirectory": "working-directory: platform-v2",
        "nonmatching repository path filter": "platform-v2/**",
    }
    present_forbidden = [name for name, marker in forbidden.items() if marker in source]
    if missing or present_forbidden:
        raise SystemExit(f"ci_workflow_invalid missing={missing} forbidden={present_forbidden}")
    print(
        {
            "result": "passed",
            "required_capabilities": len(required),
            "forbidden_drift": 0,
        }
    )


if __name__ == "__main__":
    main()
