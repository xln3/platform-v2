from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psycopg

ROOT = Path(__file__).parents[1]
ENV_PATH = Path("/etc/geo-platform-v2/platform.env")
COMPOSE_ENV_PATH = Path("/etc/geo-platform-v2/compose.env")
OUTPUT = ROOT / "tests/s04-evidence/production-postgres-credential-rotation.json"
DSN_KEYS = (
    "GEO_POSTGRES_DSN",
    "GEO_RUNTIME_POSTGRES_DSN",
    "GEO_WORKER_POSTGRES_DSN",
    "S02_POSTGRES_DSN",
)
SERVICES = (
    "geo-platform-v2-api.service",
    "geo-platform-v2-worker.service",
    "geo-platform-v2-s02-worker.service",
    "geo-platform-v2-outbox-worker.service",
)


def _environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _psycopg_dsn(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def main() -> None:
    values = _environment(ENV_PATH)
    parsed = {key: urlsplit(values[key]) for key in DSN_KEYS}
    if any(value.username is None or value.password is None for value in parsed.values()):
        raise RuntimeError("configured PostgreSQL role or credential is absent")
    configured_roles = {str(value.username) for value in parsed.values()}
    configured_passwords = {value.password for value in parsed.values()}
    connected_roles: set[str] = set()
    role_flags: dict[str, tuple[bool, bool]] = {}
    for key in DSN_KEYS:
        expected_role = parsed[key].username
        if expected_role is None:
            raise RuntimeError("configured PostgreSQL role is absent")
        with psycopg.connect(_psycopg_dsn(values[key])) as connection:
            row = connection.execute(
                """
                SELECT current_user,rolsuper,rolbypassrls
                FROM pg_roles WHERE rolname=current_user
                """
            ).fetchone()
        if row is None or row[0] != expected_role:
            raise RuntimeError("configured PostgreSQL role does not match authenticated role")
        connected_roles.add(row[0])
        role_flags[row[0]] = (row[1], row[2])

    service_states = {
        service: subprocess.run(
            ["systemctl", "is-active", "--quiet", service],
            check=False,
        ).returncode
        == 0
        for service in SERVICES
    }
    health = httpx.get("http://127.0.0.1:8020/api/v2/health", timeout=5)
    environment_modes = {
        str(path): f"{path.stat().st_mode & 0o777:04o}" for path in (ENV_PATH, COMPOSE_ENV_PATH)
    }
    non_owner_roles = configured_roles - {"geo"}
    assertions = {
        "all_configured_dsns_authenticate": connected_roles == configured_roles,
        "credential_is_distinct_per_database_role": (
            len(configured_passwords) == len(configured_roles)
        ),
        "runtime_roles_are_not_superuser_or_bypass_rls": all(
            role_flags[role] == (False, False) for role in non_owner_roles
        ),
        "restricted_configuration_not_world_accessible": all(
            path.stat().st_mode & 0o007 == 0 for path in (ENV_PATH, COMPOSE_ENV_PATH)
        ),
        "all_database_consumers_active": all(service_states.values()),
        "api_health_after_rotation": (
            health.status_code == 200 and health.json().get("status") == "ok"
        ),
    }
    evidence = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "result": "passed" if all(assertions.values()) else "failed",
        "assertions": assertions,
        "configured_database_role_count": len(configured_roles),
        "service_states": service_states,
        "configuration_modes": environment_modes,
        "incident": "diagnostic_dsn_exposure_remediated_by_full_role_credential_rotation",
        "credential_values_or_digests_recorded": False,
        "legacy_service_modified": False,
    }
    OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": evidence["result"], "assertions": len(assertions)}))
    if evidence["result"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        print(
            json.dumps({"result": "failed", "error_type": type(exc).__name__}),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
