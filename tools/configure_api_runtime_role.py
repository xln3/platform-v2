"""Provision a non-superuser, RLS-bound production API database role.

The generated password is written only to the restricted production environment
file. It is never printed or included in evidence.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import psycopg
from psycopg import sql

ENV_PATH = Path(os.getenv("GEO_PRODUCTION_ENV", "/etc/geo-platform-v2/platform.env"))
API_ROLE = "geo_api"
WORKER_ROLE = "geo_worker"
SCHEMAS = (
    "platform",
    "analytics",
    "evidence",
    "reporting",
    "intelligence",
    "integration",
    "sop",
)


def read_environment(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return lines, values


def runtime_dsn(owner_dsn: str, password: str, role: str) -> str:
    parsed = urlsplit(owner_dsn.replace("postgresql+psycopg://", "postgresql://"))
    host = parsed.hostname or "127.0.0.1"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit(
        (
            "postgresql+psycopg",
            f"{role}:{quote(password, safe='')}@{host}",
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


def install_role(owner_dsn: str, password: str, *, role: str, bypass_rls: bool) -> None:
    parsed = urlsplit(owner_dsn.replace("postgresql+psycopg://", "postgresql://"))
    owner = unquote(parsed.username or "")
    with psycopg.connect(owner_dsn.replace("postgresql+psycopg://", "postgresql://")) as connection:
        database = connection.info.dbname
        exists = connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
        rls_clause = sql.SQL("BYPASSRLS" if bypass_rls else "NOBYPASSRLS")
        if exists is None:
            connection.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT {} PASSWORD {}"
                ).format(
                    sql.Identifier(role),
                    rls_clause,
                    sql.Literal(password),
                )
            )
        else:
            connection.execute(
                sql.SQL(
                    "ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT {} PASSWORD {}"
                ).format(
                    sql.Identifier(role),
                    rls_clause,
                    sql.Literal(password),
                )
            )
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database), sql.Identifier(role)
            )
        )
        for schema in SCHEMAS:
            connection.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                )
            )
            connection.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO {}"
                ).format(sql.Identifier(schema), sql.Identifier(role))
            )
            connection.execute(
                sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                )
            )
            connection.execute(
                sql.SQL("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                )
            )
            connection.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} "
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
                ).format(
                    sql.Identifier(owner),
                    sql.Identifier(schema),
                    sql.Identifier(role),
                )
            )
            connection.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} "
                    "GRANT USAGE, SELECT ON SEQUENCES TO {}"
                ).format(
                    sql.Identifier(owner),
                    sql.Identifier(schema),
                    sql.Identifier(role),
                )
            )


def verify_role(dsn: str, *, bypass_rls: bool) -> None:
    with psycopg.connect(dsn.replace("postgresql+psycopg://", "postgresql://")) as connection:
        role = connection.execute(
            """
            SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls
            FROM pg_roles WHERE rolname=current_user
            """
        ).fetchone()
        if role != (False, False, False, bypass_rls):
            raise RuntimeError("runtime database role is privileged")
        connection.execute("SELECT count(*) FROM sop.project").fetchone()
        tenant = connection.execute(
            "SELECT id,pub_id FROM platform.tenant ORDER BY id LIMIT 1"
        ).fetchone()
        if tenant is not None:
            connection.execute(
                """
                SELECT set_config('app.tenant_id', %s, true),
                       set_config('app.tenant_pub_id', %s, true)
                """,
                (str(tenant[0]), tenant[1]),
            )
            connection.execute("SELECT count(*) FROM platform.membership").fetchone()
            connection.execute("SELECT count(*) FROM analytics.metric_daily").fetchone()


def write_environment(path: Path, lines: list[str], replacements: dict[str, str]) -> None:
    updated: set[str] = set()
    output: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line else ""
        if key in replacements:
            output.append(f"{key}={replacements[key]}")
            updated.add(key)
            continue
        output.append(line)
    for key, value in replacements.items():
        if key not in updated:
            output.append(f"{key}={value}")
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text("\n".join(output) + "\n", encoding="utf-8")
    os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
    os.replace(temporary, path)


def main() -> None:
    lines, values = read_environment(ENV_PATH)
    owner_dsn = values.get("GEO_POSTGRES_DSN")
    if not owner_dsn:
        raise RuntimeError("GEO_POSTGRES_DSN is missing")
    api_password = secrets.token_urlsafe(36)
    worker_password = secrets.token_urlsafe(36)
    install_role(owner_dsn, api_password, role=API_ROLE, bypass_rls=False)
    install_role(owner_dsn, worker_password, role=WORKER_ROLE, bypass_rls=False)
    api_dsn = runtime_dsn(owner_dsn, api_password, API_ROLE)
    worker_dsn = runtime_dsn(owner_dsn, worker_password, WORKER_ROLE)
    verify_role(api_dsn, bypass_rls=False)
    verify_role(worker_dsn, bypass_rls=False)
    write_environment(
        ENV_PATH,
        lines,
        {
            "GEO_RUNTIME_POSTGRES_DSN": api_dsn,
            "GEO_WORKER_POSTGRES_DSN": worker_dsn,
            "S02_POSTGRES_DSN": worker_dsn.replace("postgresql+psycopg://", "postgresql://"),
            "GEO_IDENTITY_MODE": "native_session",
        },
    )
    print(
        json.dumps(
            {
                "result": "configured",
                "api_role": {
                    "name": API_ROLE,
                    "superuser": False,
                    "bypass_rls": False,
                },
                "worker_role": {
                    "name": WORKER_ROLE,
                    "superuser": False,
                    "bypass_rls": False,
                },
                "secret_emitted": False,
            }
        )
    )


if __name__ == "__main__":
    main()
