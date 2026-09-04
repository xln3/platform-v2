"""Mint a short-lived native browser session for frontend release acceptance.

Reads GEO_POSTGRES_DSN from /etc/geo-platform-v2/platform.env, picks an active
admin membership, inserts platform.browser_session with sha256(token_hash),
writes the raw token to a 0600 temp file. Revocation is a separate step.
"""

import hashlib
import json
import os
import secrets
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "api"))

import psycopg

ENV_FILE = Path("/etc/geo-platform-v2/platform.env")
TOKEN_FILE = Path(os.environ.get("GEO_MINT_TOKEN_FILE", "/tmp/s04-acceptance-token"))


def load_dsn() -> str:
    dsn = os.environ.get("GEO_POSTGRES_DSN")
    if not dsn:
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("GEO_POSTGRES_DSN="):
                dsn = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not dsn:
        raise SystemExit("GEO_POSTGRES_DSN not found")
    return dsn.replace("postgresql+psycopg://", "postgresql://", 1)


def main() -> None:
    dsn = load_dsn()
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires_at = datetime.now(UTC) + timedelta(hours=2)
    session_id = uuid.uuid4()
    with psycopg.connect(dsn, autocommit=False) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT m.tenant_id, m.user_id, t.pub_id AS tenant_pub_id, u.pub_id AS user_pub_id
                FROM platform.membership m
                JOIN platform.tenant t ON t.id = m.tenant_id
                JOIN platform.app_user u ON u.id = m.user_id
                WHERE m.role = 'admin' AND m.state = 'active' AND m.revoked_at IS NULL
                ORDER BY m.created_at
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            if row is None:
                raise SystemExit("no active admin membership found")
            tenant_id, user_id, tenant_pub_id, user_pub_id = row
            cursor.execute(
                """
                INSERT INTO platform.browser_session
                  (id, pub_id, tenant_id, user_id, token_hash, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    str(session_id),
                    f"ses_acc{uuid.uuid4().hex[:20]}",
                    str(tenant_id),
                    str(user_id),
                    token_hash,
                    expires_at,
                ),
            )
        connection.commit()
    TOKEN_FILE.write_text(token)
    os.chmod(TOKEN_FILE, 0o600)
    print(
        json.dumps(
            {
                "session_id": str(session_id),
                "tenant_pub_id": tenant_pub_id,
                "user_pub_id": user_pub_id,
                "expires_at": expires_at.isoformat(),
                "token_file": str(TOKEN_FILE),
            }
        )
    )


if __name__ == "__main__":
    main()
