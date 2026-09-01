"""Expose credential checks without granting runtime access to token hashes.

Revision ID: s17_0005_credential_boundary
Revises: s17_0004_release_membership
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s17_0005_credential_boundary"
down_revision: str | Sequence[str] | None = "s17_0004_release_membership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION platform.verify_service_credential(
          p_tenant_id uuid,
          p_user_id uuid,
          p_secret_hash text
        ) RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
          SELECT p_secret_hash ~ '^[0-9a-f]{64}$'
             AND EXISTS (
               SELECT 1
               FROM platform.tenant AS tenant
               JOIN platform.membership AS membership
                 ON membership.tenant_id = tenant.id
               JOIN platform.app_user AS app_user
                 ON app_user.id = membership.user_id
               JOIN platform.service_credential AS credential
                 ON credential.tenant_id = tenant.id
                AND credential.user_id = app_user.id
               WHERE tenant.id = p_tenant_id
                 AND tenant.pub_id = NULLIF(current_setting('app.tenant_pub_id', true), '')
                 AND app_user.id = p_user_id
                 AND app_user.is_service_account IS TRUE
                 AND app_user.disabled_at IS NULL
                 AND membership.state = 'active'
                 AND membership.revoked_at IS NULL
                 AND credential.secret_hash = p_secret_hash
                 AND credential.revoked_at IS NULL
                 AND credential.expires_at > statement_timestamp()
             )
        $function$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.create_service_credential(
          p_id uuid,
          p_pub_id text,
          p_tenant_id uuid,
          p_user_id uuid,
          p_secret_hash text,
          p_expires_at timestamptz
        ) RETURNS boolean
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
        BEGIN
          IF p_secret_hash !~ '^[0-9a-f]{64}$'
             OR p_expires_at <= statement_timestamp() THEN
            RAISE EXCEPTION 'service_credential_input_invalid' USING ERRCODE = '22023';
          END IF;
          IF NOT EXISTS (
            SELECT 1
            FROM platform.tenant AS tenant
            JOIN platform.membership AS membership
              ON membership.tenant_id = tenant.id
            JOIN platform.app_user AS app_user
              ON app_user.id = membership.user_id
            WHERE tenant.id = p_tenant_id
              AND tenant.pub_id = NULLIF(current_setting('app.tenant_pub_id', true), '')
              AND app_user.id = p_user_id
              AND app_user.is_service_account IS TRUE
              AND app_user.disabled_at IS NULL
              AND membership.state = 'active'
              AND membership.revoked_at IS NULL
          ) THEN
            RAISE EXCEPTION 'service_credential_scope_invalid' USING ERRCODE = '42501';
          END IF;
          INSERT INTO platform.service_credential (
            id, pub_id, tenant_id, user_id, secret_hash, expires_at, created_at
          ) VALUES (
            p_id, p_pub_id, p_tenant_id, p_user_id, p_secret_hash,
            p_expires_at, statement_timestamp()
          );
          RETURN true;
        END
        $function$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.revoke_service_credentials(
          p_tenant_id uuid,
          p_user_id uuid
        ) RETURNS timestamptz
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          revoked_time timestamptz := statement_timestamp();
        BEGIN
          IF NOT EXISTS (
            SELECT 1
            FROM platform.tenant AS tenant
            JOIN platform.membership AS membership
              ON membership.tenant_id = tenant.id
            JOIN platform.app_user AS app_user
              ON app_user.id = membership.user_id
            WHERE tenant.id = p_tenant_id
              AND tenant.pub_id = NULLIF(current_setting('app.tenant_pub_id', true), '')
              AND app_user.id = p_user_id
              AND app_user.is_service_account IS TRUE
          ) THEN
            RAISE EXCEPTION 'service_credential_scope_invalid' USING ERRCODE = '42501';
          END IF;
          UPDATE platform.service_credential
          SET revoked_at = revoked_time
          WHERE tenant_id = p_tenant_id
            AND user_id = p_user_id
            AND revoked_at IS NULL;
          RETURN revoked_time;
        END
        $function$
        """
    )
    for function in (
        "platform.verify_service_credential(uuid,uuid,text)",
        "platform.create_service_credential(uuid,text,uuid,uuid,text,timestamptz)",
        "platform.revoke_service_credentials(uuid,uuid)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(
            f"""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_api') THEN
              GRANT EXECUTE ON FUNCTION {function} TO geo_api;
            END IF;
            END $$"""
        )


def downgrade() -> None:
    for function in (
        "platform.revoke_service_credentials(uuid,uuid)",
        "platform.create_service_credential(uuid,text,uuid,uuid,text,timestamptz)",
        "platform.verify_service_credential(uuid,uuid,text)",
    ):
        op.execute(f"DROP FUNCTION {function}")
