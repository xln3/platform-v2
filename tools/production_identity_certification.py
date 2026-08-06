from __future__ import annotations

import argparse
import json
import sqlite3
import ssl
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request_json(url: str, *, headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    request = Request(url, headers=headers or {})
    context = ssl._create_unverified_context()  # noqa: S323
    try:
        with urlopen(request, context=context, timeout=10) as response:  # noqa: S310
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def active_session_metadata(database_path: Path) -> tuple[str, int, dict[str, int]]:
    uri = f"file:{database_path.resolve()}?mode=ro&immutable=0"
    with sqlite3.connect(uri, uri=True, timeout=2) as connection:
        rows = connection.execute(
            """
            SELECT s.token,m.role
            FROM session s
            JOIN app_user u ON u.id=s.user_id
            JOIN membership m ON m.user_id=s.user_id AND m.tenant_id=s.tenant_id
            WHERE s.expires_at >= datetime('now')
            """
        ).fetchall()
    if not rows:
        raise RuntimeError("no active legacy session is available for certification")
    role_counts: dict[str, int] = {}
    for _, role in rows:
        role_counts[str(role)] = role_counts.get(str(role), 0) + 1
    return str(rows[0][0]), len(rows), role_counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-db", type=Path, required=True)
    parser.add_argument("--base-url", default="https://127.0.0.1:8443")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/s04-evidence/production-identity-certification.json"),
    )
    args = parser.parse_args()

    token, active_count, legacy_role_counts = active_session_metadata(args.legacy_db)
    endpoint = f"{args.base_url.rstrip('/')}/api/v2/identity/session"
    rejected_status, rejected_body = request_json(
        endpoint,
        headers={
            "X-Tenant-Id": "tnt_untrusted_browser_claim",
            "X-Actor-Id": "usr_untrusted_browser_claim",
            "X-Actor-Role": "admin",
        },
    )
    accepted_status, accepted_body = request_json(endpoint, headers={"Cookie": f"session={token}"})
    accepted_role = accepted_body.get("role")
    certification_result = (
        "passed"
        if rejected_status == 401
        and rejected_body.get("error", {}).get("code") == "session_invalid"
        and accepted_status == 200
        else "failed"
    )
    result: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "result": certification_result,
        "identity_mode": "legacy_session_bridge",
        "legacy_session": {
            "active_session_count": active_count,
            "role_counts": legacy_role_counts,
            "database_open_mode": "read_only",
        },
        "browser_actor_header_impersonation": {
            "status": rejected_status,
            "error_code": rejected_body.get("error", {}).get("code"),
            "rejected": rejected_status == 401,
        },
        "mapped_session": {
            "status": accepted_status,
            "v2_role": accepted_role,
            "accepted": accepted_status == 200,
        },
        "final_identity_gates": {
            "oidc_verified": False,
            "passkey_verified": False,
            "human_roles_verified": (
                [accepted_role] if accepted_status == 200 and accepted_role else []
            ),
            "human_roles_missing": [
                role
                for role in ["customer", "operator", "analyst", "reviewer", "admin"]
                if role != accepted_role
            ],
        },
        "secret_emitted": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "result": certification_result,
                "accepted_role": accepted_role,
                "secret_emitted": False,
            }
        )
    )
    if certification_result != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
