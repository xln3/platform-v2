#!/usr/bin/env python3
"""Atomically point one local relay at a live Wukong lease without leaking credentials.

The provider cache is the credential-bearing server-truth projection. This tool
selects exactly one live lease for the requested city, replaces only
``UPSTREAM_PROXY_URL`` in an existing root-owned relay env file, preserves its
owner/mode, and prints secret-free metadata. Mutation requires ``--apply``.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

DEFAULT_CACHE = Path("/home/xln/geo-system/platform-v2/runtime/wukong_leases.json")


def _city_key(value: object) -> str:
    return str(value or "").strip().rstrip("市省")


def _endtime(value: object) -> datetime:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise SystemExit("selected lease has an invalid endtime") from exc


def _load_lease(cache_path: Path, city: str) -> dict[str, Any]:
    stat = cache_path.stat()
    if stat.st_mode & 0o077:
        raise SystemExit("Wukong cache must not be group/world accessible")
    state = json.loads(cache_path.read_text(encoding="utf-8"))
    now = datetime.now()
    matches = [
        row
        for row in state.get("leases", [])
        if isinstance(row, dict)
        and _city_key(row.get("city")) == _city_key(city)
        and _endtime(row.get("endtime")) > now
    ]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one live lease for {city}, found {len(matches)}")
    return matches[0]


def _proxy_url(lease: dict[str, Any]) -> str:
    username = str(lease.get("username") or "")
    password = str(lease.get("password") or "")
    server = str(lease.get("server") or "")
    try:
        port = int(lease.get("port") or 0)
    except (TypeError, ValueError) as exc:
        raise SystemExit("selected lease has an invalid port") from exc
    if not username or not password or not server or not 1 <= port <= 65535:
        raise SystemExit("selected lease is missing proxy connection material")
    value = f"http://{quote(username, safe='')}:{quote(password, safe='')}@{server}:{port}"
    parsed = urlsplit(value)
    if parsed.scheme != "http" or not parsed.hostname or not parsed.username or not parsed.password:
        raise SystemExit("selected lease did not produce a valid authenticated proxy URL")
    return value


def _rewrite_env(env_path: Path, proxy_url: str, *, apply: bool) -> int:
    stat = env_path.stat()
    if stat.st_mode & 0o077:
        raise SystemExit("relay env must not be group/world accessible")
    lines = env_path.read_text(encoding="utf-8").splitlines()
    upstream_indexes = [
        index for index, line in enumerate(lines) if line.startswith("UPSTREAM_PROXY_URL=")
    ]
    if len(upstream_indexes) != 1:
        raise SystemExit("relay env must contain exactly one UPSTREAM_PROXY_URL entry")
    port_values = [
        line.split("=", 1)[1].strip() for line in lines if line.startswith("RELAY_PORT=")
    ]
    if len(port_values) != 1 or not port_values[0].isdigit():
        raise SystemExit("relay env must contain exactly one numeric RELAY_PORT entry")
    lines[upstream_indexes[0]] = f"UPSTREAM_PROXY_URL={proxy_url}"
    if not apply:
        return int(port_values[0])

    payload = "\n".join(lines) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{env_path.name}.", dir=env_path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, stat.st_mode & 0o777)
        os.fchown(descriptor, stat.st_uid, stat.st_gid)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, env_path)
        directory_descriptor = os.open(env_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary_path.unlink(missing_ok=True)
    return int(port_values[0])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", required=True)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    lease = _load_lease(args.cache.resolve(), args.city)
    relay_port = _rewrite_env(args.env_file.resolve(), _proxy_url(lease), apply=args.apply)
    print(
        json.dumps(
            {
                "ok": True,
                "action": "updated" if args.apply else "checked",
                "city": args.city,
                "endtime": str(lease.get("endtime")),
                "relay_port": relay_port,
                "env_file": str(args.env_file.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
