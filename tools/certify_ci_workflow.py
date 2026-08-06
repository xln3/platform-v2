from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
EVIDENCE = ROOT / "tests/s04-evidence/ci-workflow-certification.json"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return value


def main() -> None:
    source = WORKFLOW.read_text()
    workflow = yaml.safe_load(source)
    jobs = workflow["jobs"]
    quality = load_json(ROOT / "tests/s04-evidence/full-quality-certification.json")
    browser_quality = quality["browser_e2e"]
    e2e_evidence = browser_quality.get("evidence")
    if not isinstance(e2e_evidence, str):
        raise ValueError("full quality evidence does not identify its browser E2E result")
    e2e_path = (ROOT / e2e_evidence).resolve()
    if ROOT.resolve() not in e2e_path.parents:
        raise ValueError("browser E2E evidence must remain inside the repository")
    e2e = load_json(e2e_path)
    compose_rows = [
        json.loads(line)
        for line in (ROOT / "tests/compose-smoke.json").read_text().splitlines()
        if line.strip()
    ]
    postgres = next(row for row in compose_rows if row.get("Service") == "postgres")
    remote = subprocess.run(
        ["git", "remote"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    expected_jobs = {
        "python",
        "typescript",
        "contract-and-release-guards",
        "browser-e2e",
        "compose-smoke",
    }
    assertions = {
        "yaml_parses": isinstance(workflow, dict),
        "least_privilege_contents_read": workflow.get("permissions") == {"contents": "read"},
        "expected_jobs_exact": set(jobs) == expected_jobs,
        "repository_root_directory": "working-directory: platform-v2" not in source,
        "all_changes_trigger": "platform-v2/**" not in source,
        "fresh_pgvector_dependencies": ("deploy/s02/compose.pgvector.yaml up -d --wait" in source),
        "schema_migrated_before_python_tests": (
            source.index(".venv/bin/alembic upgrade head") < source.index(".venv/bin/pytest -q")
        ),
        "release_guards_present": all(
            marker in source
            for marker in (
                "pnpm check:api",
                "pnpm check:production-bundles",
                "pnpm check:legacy-routes",
                "pnpm check:ci",
            )
        ),
        "browser_failure_artifacts_retained": "playwright-failure-artifacts" in source,
        "local_full_quality_passed": quality["result"] == "passed",
        "local_e2e_complete": (
            e2e["stats"]["expected"] == browser_quality["passed"]
            and e2e["stats"]["unexpected"] == browser_quality["unexpected"] == 0
            and e2e["stats"]["skipped"] == browser_quality["skipped"] == 0
            and e2e["stats"]["flaky"] == browser_quality["flaky"] == 0
        ),
        "local_compose_pgvector_healthy": postgres["Health"] == "healthy"
        and "pgvector/pgvector" in postgres["Image"],
    }
    result = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "result": (
            "configuration_and_local_equivalent_passed_remote_run_unavailable"
            if all(assertions.values()) and not remote
            else "failed"
        ),
        "workflow_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "jobs": sorted(jobs),
        "assertions": assertions,
        "remote_configured": bool(remote),
        "remote_run_verified": False,
        "qualification": (
            "The repository has no configured Git remote. This proves workflow configuration "
            "and its current local equivalent, not a hosted GitHub Actions run."
        ),
    }
    EVIDENCE.write_text(json.dumps(result, indent=2) + "\n")
    if result["result"] == "failed":
        raise RuntimeError("ci_workflow_certification_failed")
    print(json.dumps({"result": result["result"], "assertions": len(assertions)}))


if __name__ == "__main__":
    main()
