from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..tenancy.ids import new_pub_id

EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SESSION_TOKEN_BYTES = 48


@dataclass(frozen=True)
class NativeSessionIdentity:
    tenant_id: uuid.UUID
    tenant_pub_id: str
    user_id: uuid.UUID
    user_pub_id: str
    subject: str
    role: str


def _enable_native_auth_lookup(session: Session) -> None:
    """Enable the narrow, transaction-local RLS path used before tenant resolution."""
    session.execute(text("SELECT set_config('app.auth_scope', 'native_session', true)"))


def normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) > 254 or not EMAIL.fullmatch(normalized):
        raise ValueError("invalid_email")
    return normalized


def validate_password(value: str) -> None:
    if not value or len(value) > 256:
        raise ValueError("invalid_password")


def _verify_werkzeug_password_hash(stored: str, password: str) -> bool:
    """Verify a legacy Werkzeug hash before upgrading it to native V2 scrypt."""
    try:
        method, salt, expected = stored.split("$", 2)
        if method.startswith("scrypt:"):
            _, n, r, p = method.split(":", 3)
            candidate = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt.encode("utf-8"),
                n=int(n),
                r=int(r),
                p=int(p),
                dklen=len(bytes.fromhex(expected)),
                maxmem=128 * 1024 * 1024,
            ).hex()
        elif method.startswith("pbkdf2:"):
            parts = method.split(":")
            algorithm = parts[1]
            iterations = int(parts[2]) if len(parts) == 3 else 600_000
            candidate = hashlib.pbkdf2_hmac(
                algorithm,
                password.encode("utf-8"),
                salt.encode("utf-8"),
                iterations,
            ).hex()
        else:
            return False
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(candidate, expected)


def _verify_legacy_password(sqlite_path: str, email: str, password: str) -> bool:
    if not sqlite_path:
        return False
    try:
        connection = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True, timeout=2)
        try:
            row = connection.execute(
                "SELECT password_hash FROM app_user WHERE lower(email)=? LIMIT 1",
                (email,),
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return False
    return bool(row and _verify_werkzeug_password_hash(str(row[0]), password))


def _legacy_subject(email: str) -> str:
    return f"legacy-metadata:{hashlib.sha256(email.casefold().encode()).hexdigest()}"


def _password_material(value: str) -> bytes:
    pepper = get_settings().native_auth_pepper
    if get_settings().env in {"production", "prod"} and len(pepper) < 32:
        raise RuntimeError("native_auth_pepper_not_configured")
    return value.encode("utf-8") + b"\0" + pepper.encode("utf-8")


def _derive_password(
    value: str,
    salt: bytes,
    *,
    n: int = SCRYPT_N,
    r: int = SCRYPT_R,
    p: int = SCRYPT_P,
) -> bytes:
    return hashlib.scrypt(
        _password_material(value),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=64,
        maxmem=128 * 1024 * 1024,
    )


def set_native_password(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    password: str,
) -> None:
    validate_password(password)
    salt = secrets.token_bytes(32)
    password_hash = _derive_password(password, salt)
    session.execute(
        text(
            """
            INSERT INTO platform.user_password_credential
              (id,tenant_id,user_id,salt,password_hash,scrypt_n,scrypt_r,scrypt_p)
            VALUES (:id,:tenant_id,:user_id,:salt,:password_hash,:n,:r,:p)
            ON CONFLICT (tenant_id,user_id) DO UPDATE
            SET salt=EXCLUDED.salt,password_hash=EXCLUDED.password_hash,
                scrypt_n=EXCLUDED.scrypt_n,scrypt_r=EXCLUDED.scrypt_r,
                scrypt_p=EXCLUDED.scrypt_p,password_changed_at=now()
            """
        ),
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "salt": salt,
            "password_hash": password_hash,
            "n": SCRYPT_N,
            "r": SCRYPT_R,
            "p": SCRYPT_P,
        },
    )
    session.execute(
        text(
            """
            UPDATE platform.browser_session SET revoked_at=now()
            WHERE tenant_id=:tenant_id AND user_id=:user_id AND revoked_at IS NULL
            """
        ),
        {"tenant_id": tenant_id, "user_id": user_id},
    )


def _rate_hash(value: str) -> str:
    pepper = get_settings().native_auth_pepper.encode("utf-8")
    return hmac.new(pepper, value.encode("utf-8"), hashlib.sha256).hexdigest()


def _record_attempt(
    session: Session,
    *,
    subject_hash: str,
    network_hash: str,
    succeeded: bool,
) -> None:
    session.execute(
        text(
            """
            INSERT INTO platform.login_attempt (subject_hash,network_hash,succeeded)
            VALUES (:subject_hash,:network_hash,:succeeded)
            """
        ),
        {
            "subject_hash": subject_hash,
            "network_hash": network_hash,
            "succeeded": succeeded,
        },
    )


def create_native_session(
    session: Session,
    *,
    email: str,
    password: str,
    network_label: str,
) -> tuple[str, NativeSessionIdentity, datetime]:
    normalized = normalize_email(email)
    if not password or len(password) > 256:
        raise ValueError("invalid_password")
    subject_hash = _rate_hash(normalized)
    network_hash = _rate_hash(network_label or "unknown")
    recent_failures = int(
        session.execute(
            text(
                """
                SELECT count(*) FROM platform.login_attempt
                WHERE subject_hash=:subject_hash AND network_hash=:network_hash
                  AND succeeded=false AND occurred_at >= now() - interval '15 minutes'
                """
            ),
            {"subject_hash": subject_hash, "network_hash": network_hash},
        ).scalar_one()
    )
    if recent_failures >= 5:
        raise PermissionError("login_rate_limited")
    _enable_native_auth_lookup(session)
    legacy_subject = _legacy_subject(normalized)
    row = (
        session.execute(
            text(
                """
            SELECT c.tenant_id,c.user_id,c.salt,c.password_hash,c.scrypt_n,c.scrypt_r,c.scrypt_p,
                   t.pub_id AS tenant_pub_id,u.pub_id AS user_pub_id,u.subject
            FROM platform.user_password_credential c
            JOIN platform.app_user u ON u.id=c.user_id
            JOIN platform.tenant t ON t.id=c.tenant_id
            WHERE lower(u.subject) IN (:subject,:legacy_subject)
              AND u.disabled_at IS NULL AND t.state='active'
            ORDER BY c.created_at
            LIMIT 1
            """
            ),
            {"subject": normalized, "legacy_subject": legacy_subject},
        )
        .mappings()
        .first()
    )
    if row is None and _verify_legacy_password(
        get_settings().legacy_auth_sqlite_path,
        normalized,
        password,
    ):
        migrated_identity = (
            session.execute(
                text(
                    """
                    SELECT m.tenant_id,u.id AS user_id,t.pub_id AS tenant_pub_id,
                           u.pub_id AS user_pub_id,u.subject
                    FROM platform.app_user u
                    JOIN platform.membership m ON m.user_id=u.id
                    JOIN platform.tenant t ON t.id=m.tenant_id
                    WHERE lower(u.subject)=:legacy_subject
                      AND u.disabled_at IS NULL AND t.state='active'
                      AND m.state='active' AND m.revoked_at IS NULL
                    ORDER BY m.created_at
                    LIMIT 1
                    """
                ),
                {"legacy_subject": legacy_subject},
            )
            .mappings()
            .first()
        )
        if migrated_identity is not None:
            set_native_password(
                session,
                tenant_id=migrated_identity["tenant_id"],
                user_id=migrated_identity["user_id"],
                password=password,
            )
            row = (
                session.execute(
                    text(
                        """
                        SELECT c.tenant_id,c.user_id,c.salt,c.password_hash,
                               c.scrypt_n,c.scrypt_r,c.scrypt_p,
                               t.pub_id AS tenant_pub_id,u.pub_id AS user_pub_id,u.subject
                        FROM platform.user_password_credential c
                        JOIN platform.app_user u ON u.id=c.user_id
                        JOIN platform.tenant t ON t.id=c.tenant_id
                        WHERE c.tenant_id=:tenant_id AND c.user_id=:user_id
                        LIMIT 1
                        """
                    ),
                    {
                        "tenant_id": migrated_identity["tenant_id"],
                        "user_id": migrated_identity["user_id"],
                    },
                )
                .mappings()
                .first()
            )
    if row is None:
        _derive_password(password, b"\0" * 32)
        _record_attempt(
            session,
            subject_hash=subject_hash,
            network_hash=network_hash,
            succeeded=False,
        )
        session.commit()
        raise PermissionError("invalid_credentials")
    candidate = _derive_password(
        password,
        bytes(row["salt"]),
        n=int(row["scrypt_n"]),
        r=int(row["scrypt_r"]),
        p=int(row["scrypt_p"]),
    )
    if not hmac.compare_digest(candidate, bytes(row["password_hash"])):
        _record_attempt(
            session,
            subject_hash=subject_hash,
            network_hash=network_hash,
            succeeded=False,
        )
        session.commit()
        raise PermissionError("invalid_credentials")
    session.execute(
        text(
            "SELECT set_config('app.tenant_id', :tenant_id, true), "
            "set_config('app.tenant_pub_id', :tenant_pub_id, true)"
        ),
        {"tenant_id": str(row["tenant_id"]), "tenant_pub_id": row["tenant_pub_id"]},
    )
    membership = (
        session.execute(
            text(
                """
            SELECT role FROM platform.membership
            WHERE tenant_id=:tenant_id AND user_id=:user_id
              AND state='active' AND revoked_at IS NULL
            """
            ),
            {"tenant_id": row["tenant_id"], "user_id": row["user_id"]},
        )
        .mappings()
        .first()
    )
    if membership is None:
        _record_attempt(
            session,
            subject_hash=subject_hash,
            network_hash=network_hash,
            succeeded=False,
        )
        session.commit()
        raise PermissionError("invalid_credentials")
    token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
    expires_at = datetime.now(UTC) + timedelta(hours=get_settings().native_session_hours)
    session.execute(
        text(
            """
            INSERT INTO platform.browser_session
              (id,pub_id,tenant_id,user_id,token_hash,expires_at)
            VALUES (:id,:pub_id,:tenant_id,:user_id,:token_hash,:expires_at)
            """
        ),
        {
            "id": uuid.uuid4(),
            "pub_id": new_pub_id("ses"),
            "tenant_id": row["tenant_id"],
            "user_id": row["user_id"],
            "token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "expires_at": expires_at,
        },
    )
    _record_attempt(
        session,
        subject_hash=subject_hash,
        network_hash=network_hash,
        succeeded=True,
    )
    identity = NativeSessionIdentity(
        tenant_id=row["tenant_id"],
        tenant_pub_id=str(row["tenant_pub_id"]),
        user_id=row["user_id"],
        user_pub_id=str(row["user_pub_id"]),
        subject=str(row["subject"]),
        role=str(membership["role"]),
    )
    session.commit()
    return token, identity, expires_at


def authenticate_native_session(
    session: Session,
    token: str | None,
) -> NativeSessionIdentity | None:
    if (
        token is None
        or not 32 <= len(token) <= 256
        or any(character.isspace() for character in token)
    ):
        return None
    _enable_native_auth_lookup(session)
    row = (
        session.execute(
            text(
                """
            SELECT s.tenant_id,s.user_id,t.pub_id AS tenant_pub_id,
                   u.pub_id AS user_pub_id,u.subject
            FROM platform.browser_session s
            JOIN platform.tenant t ON t.id=s.tenant_id
            JOIN platform.app_user u ON u.id=s.user_id
            WHERE s.token_hash=:token_hash AND s.revoked_at IS NULL
              AND s.expires_at > now() AND t.state='active' AND u.disabled_at IS NULL
            """
            ),
            {"token_hash": hashlib.sha256(token.encode()).hexdigest()},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    session.execute(
        text(
            "SELECT set_config('app.tenant_id', :tenant_id, true), "
            "set_config('app.tenant_pub_id', :tenant_pub_id, true)"
        ),
        {"tenant_id": str(row["tenant_id"]), "tenant_pub_id": row["tenant_pub_id"]},
    )
    membership = (
        session.execute(
            text(
                """
            SELECT role FROM platform.membership
            WHERE tenant_id=:tenant_id AND user_id=:user_id
              AND state='active' AND revoked_at IS NULL
            """
            ),
            {"tenant_id": row["tenant_id"], "user_id": row["user_id"]},
        )
        .mappings()
        .first()
    )
    if membership is None:
        return None
    return NativeSessionIdentity(
        tenant_id=row["tenant_id"],
        tenant_pub_id=str(row["tenant_pub_id"]),
        user_id=row["user_id"],
        user_pub_id=str(row["user_pub_id"]),
        subject=str(row["subject"]),
        role=str(membership["role"]),
    )


def revoke_native_session(session: Session, token: str | None) -> None:
    if token is None:
        return
    _enable_native_auth_lookup(session)
    session.execute(
        text(
            """
            UPDATE platform.browser_session SET revoked_at=now()
            WHERE token_hash=:token_hash AND revoked_at IS NULL
            """
        ),
        {"token_hash": hashlib.sha256(token.encode()).hexdigest()},
    )
    session.commit()
