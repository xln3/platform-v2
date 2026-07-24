"""Retain every evidence asset cited by a published report.

Revision ID: s02_0008
Revises: s02_0007
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s02_0008"
down_revision: str | None = "s02_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE reporting.report_evidence_reference (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          report_version_pub_id TEXT NOT NULL REFERENCES reporting.report_version(pub_id),
          evidence_pub_id TEXT NOT NULL REFERENCES evidence.evidence_asset(pub_id),
          purpose TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_pub_id,report_version_pub_id,evidence_pub_id)
        );

        CREATE OR REPLACE FUNCTION evidence.prevent_published_evidence_deletion()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL AND (
            EXISTS (
              SELECT 1
              FROM reporting.report_artifact ra
              JOIN reporting.report_version rv ON rv.pub_id=ra.report_version_pub_id
              JOIN reporting.report r ON r.pub_id=rv.report_pub_id
              WHERE ra.evidence_pub_id=OLD.pub_id AND r.state='published'
            )
            OR EXISTS (
              SELECT 1
              FROM reporting.report_evidence_reference ref
              JOIN reporting.report_version rv ON rv.pub_id=ref.report_version_pub_id
              JOIN reporting.report r ON r.pub_id=rv.report_pub_id
              WHERE ref.evidence_pub_id=OLD.pub_id AND r.state='published'
            )
          ) THEN
            RAISE EXCEPTION 'published report evidence is retained';
          END IF;
          RETURN NEW;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS reporting.report_evidence_reference;
        CREATE OR REPLACE FUNCTION evidence.prevent_published_evidence_deletion()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL AND EXISTS (
            SELECT 1
            FROM reporting.report_artifact ra
            JOIN reporting.report_version rv ON rv.pub_id=ra.report_version_pub_id
            JOIN reporting.report r ON r.pub_id=rv.report_pub_id
            WHERE ra.evidence_pub_id=OLD.pub_id AND r.state='published'
          ) THEN
            RAISE EXCEPTION 'published report evidence is retained';
          END IF;
          RETURN NEW;
        END $$;
        """
    )
