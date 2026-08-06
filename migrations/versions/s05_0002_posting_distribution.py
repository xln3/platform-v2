"""Paid media distribution batches and per-target posting status.

Revision ID: s05_0002
Revises: s05_0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s05_0002"
down_revision: str | Sequence[str] | None = "s05_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("batch", "target", "event")


def _force_tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE posting.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE posting.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON posting.{table}
        USING (tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), ''))
        WITH CHECK (tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), ''))
        """
    )


def upgrade() -> None:
    op.execute("CREATE SCHEMA posting")
    op.execute(
        """
        CREATE TABLE posting.batch (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          idempotency_key_sha256 TEXT NOT NULL
            CHECK (idempotency_key_sha256 ~ '^[0-9a-f]{64}$'),
          source_filename TEXT NOT NULL,
          source_sha256 TEXT NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
          catalog_sha256 TEXT NOT NULL CHECK (catalog_sha256 ~ '^[0-9a-f]{64}$'),
          title TEXT NOT NULL,
          content_text TEXT NOT NULL,
          content_html TEXT NOT NULL,
          image_count INTEGER NOT NULL DEFAULT 0 CHECK (image_count >= 0),
          customer_name TEXT NOT NULL DEFAULT '',
          release_time DATE,
          auto_submit BOOLEAN NOT NULL DEFAULT false,
          spend_confirmed_at TIMESTAMPTZ,
          max_total_amount NUMERIC(12,2),
          quoted_total_amount NUMERIC(12,2) NOT NULL
            CHECK (quoted_total_amount >= 0),
          status TEXT NOT NULL DEFAULT 'draft'
            CHECK (status IN (
              'draft','queued','processing','partially_submitted',
              'submitted','published','blocked','failed','canceled'
            )),
          note TEXT NOT NULL DEFAULT '',
          created_by_pub_id TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_pub_id, idempotency_key_sha256)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE posting.target (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          batch_pub_id TEXT NOT NULL REFERENCES posting.batch(pub_id),
          catalog_type TEXT NOT NULL CHECK (catalog_type IN ('news','wemedia')),
          provider TEXT NOT NULL CHECK (provider IN (
            'prfabu','toumeiw','mtpfw','meititejia','meijiehezi','pinda'
          )),
          media_name TEXT NOT NULL,
          media_platform TEXT NOT NULL DEFAULT '',
          provider_media_id TEXT NOT NULL DEFAULT '',
          quoted_price NUMERIC(12,2) NOT NULL CHECK (quoted_price > 0),
          status TEXT NOT NULL DEFAULT 'selected'
            CHECK (status IN (
              'selected','queued','submitting','submitted','reviewing','published',
              'balance_insufficient','provider_session_expired',
              'provider_confirmation_required','unsupported_provider',
              'rejected','failed','canceled'
            )),
          external_order_id TEXT NOT NULL DEFAULT '',
          public_url TEXT NOT NULL DEFAULT '',
          provider_message TEXT NOT NULL DEFAULT '',
          submitted_at TIMESTAMPTZ,
          published_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (
            tenant_pub_id,batch_pub_id,catalog_type,provider,media_name,media_platform
          )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE posting.event (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          batch_pub_id TEXT NOT NULL REFERENCES posting.batch(pub_id),
          target_pub_id TEXT REFERENCES posting.target(pub_id),
          event_type TEXT NOT NULL,
          from_status TEXT NOT NULL DEFAULT '',
          to_status TEXT NOT NULL DEFAULT '',
          message TEXT NOT NULL DEFAULT '',
          payload JSONB NOT NULL DEFAULT '{}',
          actor_pub_id TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX posting_batch_tenant_created_idx "
        "ON posting.batch (tenant_pub_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX posting_target_batch_idx "
        "ON posting.target (tenant_pub_id, batch_pub_id, created_at)"
    )
    op.execute(
        "CREATE INDEX posting_target_status_idx "
        "ON posting.target (tenant_pub_id, status, updated_at)"
    )
    op.execute(
        "CREATE INDEX posting_event_batch_idx "
        "ON posting.event (tenant_pub_id, batch_pub_id, created_at, pub_id)"
    )
    for table in _TABLES:
        _force_tenant_rls(table)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE posting.{table}")
    op.execute("DROP SCHEMA posting")
