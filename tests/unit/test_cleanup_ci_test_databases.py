from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "cleanup_ci_test_databases.sh"
ALLOWED_DATABASES = (
    "geo_platform_s01_ci",
    "geo_platform_knowledge_ci",
    "geo_platform_quota_s07_ci",
)


def _run(
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *arguments],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _fake_docker_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "docker-commands.log"
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf \'%s\\n\' "$*" >> "$GEO_TEST_DOCKER_LOG"\n'
        'if [[ "$1" == context && "$2" == inspect ]]; then\n'
        "  printf '%s\\n' 'unix:///var/run/docker.sock'\n"
        'elif [[ "$1" == --context && "$3" == compose && "$*" == *\' ps -q postgres\' ]]; then\n'
        "  printf '%s\\n' 'geo-platform-v2-postgres-test'\n"
        'elif [[ "$1" == --context && "$3" == inspect ]]; then\n'
        "  printf '%s\\n' 'geo-platform-v2|postgres'\n"
        "fi\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    environment = os.environ.copy()
    for override in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "COMPOSE_PROJECT_NAME"):
        environment.pop(override, None)
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["GEO_TEST_DOCKER_LOG"] = str(command_log)
    return environment, command_log


def test_dry_run_lists_only_the_three_allowed_databases_without_docker(tmp_path: Path) -> None:
    environment, command_log = _fake_docker_environment(tmp_path)

    result = _run("--dry-run", "--all", environment=environment)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.splitlines() == [
        line
        for database_name in ALLOWED_DATABASES
        for line in (
            f"dry-run terminate-connections database={database_name}",
            f"dry-run drop-if-exists database={database_name}",
        )
    ]
    assert not command_log.exists()


@pytest.mark.parametrize(
    ("refused_database", "expected_error"),
    (
        ("geo_platform", "refusing default business database: geo_platform"),
        ("customer_production", "refusing unknown CI test database: customer_production"),
    ),
)
def test_invalid_request_is_rejected_before_any_docker_command(
    tmp_path: Path,
    refused_database: str,
    expected_error: str,
) -> None:
    environment, command_log = _fake_docker_environment(tmp_path)

    result = _run(
        "geo_platform_s01_ci",
        refused_database,
        environment=environment,
    )

    assert result.returncode == 2
    assert expected_error in result.stderr
    assert not command_log.exists()


def test_cleanup_requires_explicit_target_or_all(tmp_path: Path) -> None:
    environment, command_log = _fake_docker_environment(tmp_path)

    result = _run(environment=environment)

    assert result.returncode == 2
    assert "refusing cleanup without --all" in result.stderr
    assert not command_log.exists()


@pytest.mark.parametrize(
    "override",
    ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "COMPOSE_PROJECT_NAME"),
)
def test_docker_target_overrides_are_rejected_before_cleanup(
    tmp_path: Path,
    override: str,
) -> None:
    environment, command_log = _fake_docker_environment(tmp_path)
    environment[override] = "unsafe-override"

    result = _run("geo_platform_s01_ci", environment=environment)

    assert result.returncode == 2
    assert "refusing Docker or Compose environment overrides" in result.stderr
    assert not command_log.exists()


def test_each_database_terminates_connections_before_idempotent_drop(tmp_path: Path) -> None:
    environment, command_log = _fake_docker_environment(tmp_path)

    result = _run("--all", environment=environment)

    assert result.returncode == 0, result.stderr
    commands = command_log.read_text(encoding="utf-8").splitlines()
    assert commands[0].startswith("context inspect default")
    assert "compose --project-name geo-platform-v2" in commands[1]
    assert commands[1].endswith("ps -q postgres")
    assert "inspect --format" in commands[2]
    database_commands = [command for command in commands if "exec -T postgres" in command]
    assert len(database_commands) == len(ALLOWED_DATABASES) * 2
    for index, database_name in enumerate(ALLOWED_DATABASES):
        terminate = database_commands[index * 2]
        drop = database_commands[index * 2 + 1]
        assert "exec -T postgres psql -U geo -d postgres" in terminate
        assert f"datname = '{database_name}'" in terminate
        assert ":'target_database'" not in terminate
        assert "pg_terminate_backend(pid)" in terminate
        assert "pid <> pg_backend_pid()" in terminate
        assert "exec -T postgres dropdb -U geo --if-exists --force" in drop
        assert drop.endswith(database_name)


def test_duplicate_target_is_cleaned_once_and_can_be_repeated(tmp_path: Path) -> None:
    environment, command_log = _fake_docker_environment(tmp_path)

    first = _run(
        "geo_platform_knowledge_ci",
        "geo_platform_knowledge_ci",
        environment=environment,
    )
    first_commands = command_log.read_text(encoding="utf-8").splitlines()
    second = _run("geo_platform_knowledge_ci", environment=environment)
    all_commands = command_log.read_text(encoding="utf-8").splitlines()

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert len(first_commands) == 5
    assert all_commands == first_commands + first_commands
