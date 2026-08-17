"""Add durable notification state, delivery outbox, callback replay, and audit.

Revision ID: s06_0025
Revises: s06_0024

The schema is channel-neutral even though the first sender is the Feishu custom
application bot.  Assist bearer tickets are never stored: only their SHA-256
digests are persisted.  Delivery commands contain references and revisions,
not rendered cards or callback payloads, so secrets cannot leak through the
outbox or dead-letter diagnostics.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s06_0025"
down_revision: str | Sequence[str] | None = "s06_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE SCHEMA IF NOT EXISTS notification;

        CREATE TABLE notification.notice (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          kind TEXT NOT NULL CHECK (kind IN ('assist','alert')),
          channel TEXT NOT NULL DEFAULT 'feishu_app',
          fingerprint TEXT NOT NULL,
          tenant_pub_id TEXT,
          state TEXT NOT NULL CHECK (state IN (
            'pending_delivery','active','claimed','solved','expired','closed',
            'delivery_failed'
          )),
          desired_state TEXT NOT NULL CHECK (desired_state IN (
            'active','claimed','solved','expired','closed'
          )),
          severity TEXT NOT NULL,
          title TEXT NOT NULL,
          summary JSONB NOT NULL,
          target_chat_id TEXT NOT NULL,
          message_id TEXT,
          session_kind TEXT CHECK (
            session_kind IS NULL OR session_kind IN ('workflow_captcha','otp_cli')
          ),
          resource_pub_id TEXT,
          assist_ticket_sha256 TEXT CHECK (
            assist_ticket_sha256 IS NULL OR assist_ticket_sha256 ~ '^[0-9a-f]{64}$'
          ),
          claimed_actor_hash TEXT,
          claimed_actor_mask TEXT,
          claimed_at TIMESTAMPTZ,
          occurrence_count INTEGER NOT NULL DEFAULT 1 CHECK (occurrence_count > 0),
          revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
          last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          last_card_enqueued_at TIMESTAMPTZ,
          resolved_at TIMESTAMPTZ,
          expires_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          delivery_failed_at TIMESTAMPTZ,
          last_delivery_error TEXT,
          UNIQUE (kind,fingerprint)
        );

        CREATE INDEX ix_notification_notice_delivery_state
          ON notification.notice (state,updated_at);
        CREATE UNIQUE INDEX uq_notification_notice_assist_ticket
          ON notification.notice (assist_ticket_sha256)
          WHERE assist_ticket_sha256 IS NOT NULL;

        CREATE TABLE notification.delivery_command (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          command_uuid UUID NOT NULL UNIQUE,
          notice_id BIGINT NOT NULL REFERENCES notification.notice(id) ON DELETE CASCADE,
          operation TEXT NOT NULL CHECK (operation IN ('send','update')),
          notice_revision INTEGER NOT NULL CHECK (notice_revision > 0),
          state TEXT NOT NULL DEFAULT 'pending' CHECK (
            state IN ('pending','dispatching','succeeded','dead')
          ),
          attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
          next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          locked_at TIMESTAMPTZ,
          last_error TEXT,
          request_log_id TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (notice_id,operation,notice_revision)
        );

        CREATE INDEX ix_notification_delivery_ready
          ON notification.delivery_command (next_attempt_at,id)
          WHERE state IN ('pending','dispatching');

        CREATE TABLE notification.interaction (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          event_id TEXT NOT NULL UNIQUE,
          notice_id BIGINT REFERENCES notification.notice(id) ON DELETE SET NULL,
          action TEXT NOT NULL,
          actor_hash TEXT NOT NULL,
          actor_mask TEXT NOT NULL,
          result TEXT NOT NULL,
          response JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX ix_notification_interaction_notice_created
          ON notification.interaction (notice_id,created_at DESC);

        CREATE TABLE notification.callback_replay (
          replay_key TEXT PRIMARY KEY,
          event_id TEXT NOT NULL,
          expires_at TIMESTAMPTZ NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX ix_notification_callback_replay_expiry
          ON notification.callback_replay (expires_at);

        CREATE TABLE notification.audit_event (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          notice_id BIGINT REFERENCES notification.notice(id) ON DELETE SET NULL,
          actor_hash TEXT NOT NULL,
          action TEXT NOT NULL,
          result TEXT NOT NULL,
          detail JSONB NOT NULL DEFAULT '{}'::jsonb,
          occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX ix_notification_audit_notice_occurred
          ON notification.audit_event (notice_id,occurred_at DESC);

        REVOKE ALL ON SCHEMA notification FROM PUBLIC;
        REVOKE ALL ON ALL TABLES IN SCHEMA notification FROM PUBLIC;
        REVOKE ALL ON ALL SEQUENCES IN SCHEMA notification FROM PUBLIC;

        DO $$
        DECLARE
          role_name TEXT;
        BEGIN
          FOREACH role_name IN ARRAY ARRAY['geo','geo_worker','geo_api'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
              EXECUTE format('GRANT USAGE ON SCHEMA notification TO %I', role_name);
              EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES '
                'IN SCHEMA notification TO %I', role_name);
              EXECUTE format(
                'GRANT USAGE, SELECT ON ALL SEQUENCES '
                'IN SCHEMA notification TO %I', role_name);
            END IF;
          END LOOP;
        END
        $$;

        COMMENT ON COLUMN notification.notice.assist_ticket_sha256 IS
          'One-way registry lookup only; raw assist bearer tickets are forbidden here.';
        COMMENT ON COLUMN notification.delivery_command.last_error IS
          'Sanitized exception class/business code only; never response bodies or credentials.';
        """
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS notification CASCADE")
