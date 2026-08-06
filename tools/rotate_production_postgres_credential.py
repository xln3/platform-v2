from __future__ import annotations

import os
import re
import secrets
import stat
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
from psycopg import sql

PLATFORM_ENV = Path("/etc/geo-platform-v2/platform.env")
COMPOSE_ENV = Path("/etc/geo-platform-v2/compose.env")
DSN_KEYS = {
    "GEO_POSTGRES_DSN",
    "GEO_RUNTIME_POSTGRES_DSN",
    "GEO_WORKER_POSTGRES_DSN",
    "S02_POSTGRES_DSN",
}


def _values(source: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in source.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key] = value
    return result


def _dsn_with_password(dsn: str, password: str) -> str:
    parsed = urlsplit(dsn)
    if parsed.hostname is None or parsed.username is None:
        raise ValueError("production DSN lacks a network host or user")
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{quote(parsed.username, safe='')}:{quote(password, safe='')}@{host}"
    if parsed.port is not None:
        netloc += f":{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _dsn_user(dsn: str) -> str:
    username = urlsplit(dsn).username
    if username is None:
        raise ValueError("production DSN lacks a user")
    return username


def _replace_values(source: str, replacements: dict[str, str]) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for line in source.splitlines(keepends=True):
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
        if match is None or match.group(1) not in replacements:
            lines.append(line)
            continue
        key = match.group(1)
        newline = "\n" if line.endswith("\n") else ""
        lines.append(f"{key}={replacements[key]}{newline}")
        seen.add(key)
    missing = replacements.keys() - seen
    if missing:
        raise ValueError("required production configuration keys are missing")
    return "".join(lines)


def _stage(path: Path, payload: str) -> Path:
    current = path.stat()
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(staged, stat.S_IMODE(current.st_mode))
        os.chown(staged, current.st_uid, current.st_gid)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return staged


def main() -> int:
    if os.geteuid() != 0:
        print("rotation requires root", file=sys.stderr)
        return 2
    platform_source = PLATFORM_ENV.read_text(encoding="utf-8")
    compose_source = COMPOSE_ENV.read_text(encoding="utf-8")
    platform_values = _values(platform_source)
    missing = DSN_KEYS - platform_values.keys()
    if missing:
        print("production DSN configuration is incomplete", file=sys.stderr)
        return 2

    passwords = {_dsn_user(platform_values[key]): secrets.token_hex(32) for key in DSN_KEYS}
    replacements = {
        key: _dsn_with_password(platform_values[key], passwords[_dsn_user(platform_values[key])])
        for key in DSN_KEYS
    }
    platform_updated = _replace_values(platform_source, replacements)
    compose_updated = _replace_values(
        compose_source, {"GEO_PROD_POSTGRES_PASSWORD": passwords["geo"]}
    )
    platform_staged = _stage(PLATFORM_ENV, platform_updated)
    compose_staged = _stage(COMPOSE_ENV, compose_updated)

    owner_dsn = platform_values["GEO_POSTGRES_DSN"]
    if owner_dsn.startswith("postgresql+psycopg://"):
        owner_dsn = owner_dsn.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        with psycopg.connect(owner_dsn) as connection:
            for role, password in passwords.items():
                connection.execute(
                    sql.SQL("ALTER ROLE {} WITH PASSWORD {}").format(
                        sql.Identifier(role),
                        sql.Literal(password),
                    )
                )
            os.replace(platform_staged, PLATFORM_ENV)
            os.replace(compose_staged, COMPOSE_ENV)
    except BaseException as exc:
        platform_staged.unlink(missing_ok=True)
        compose_staged.unlink(missing_ok=True)
        print(f"rotation failed safely: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        passwords.clear()

    print("production PostgreSQL credential rotated and restricted configs synchronized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
